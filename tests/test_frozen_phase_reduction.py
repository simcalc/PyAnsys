"""Synthetic read-only parser contracts; no fixture represents actual CFD."""
from copy import deepcopy
from hashlib import sha256
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import frozen_rotor_phase_reduction as phase
import run_frozen_rotor_phase as runner


def write(root, relative, value):
    raw = value if isinstance(value,bytes) else value.encode("ascii")
    path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    return {"path":relative,"bytes":len(raw),"sha256":sha256(raw).hexdigest()}


def json_write(root, relative, value):
    return write(root,relative,phase.encoded(value))


def prepared_fixture(root, *, pressure=50000, mode="steady-sweep", dt=None, duration=None):
    """Exact authored dictionaries, toy bound topology and synthetic identities."""
    study=phase.protocol.prepare_study()
    period=60/study["input"]["speedRpm"]
    dt=period/360 if dt is None else dt
    duration=period*4.25 if duration is None else duration
    recipe=phase.campaign.native_recipe(study,pressure,mode,dt,duration)
    authored=[]
    for name,content in sorted(recipe.items()):
        row=write(root,"case/"+name,content);authored.append({**row,"path":name})
    mesh=[]
    for name in sorted(phase.campaign.MESH_FILES):
        content=phase.campaign.base.foam_header(name,"vectorField" if name=="points" else "dictionary")+("2\n((0 0 0)(1 0 0))\n" if name=="points" else "0\n()\n")
        row=write(root,"case/constant/polyMesh/"+name,content);mesh.append({**row,"path":"constant/polyMesh/"+name})
    geometry={"schemaVersion":"untirta.frozen-rotor-fixed-geometry-identity.v1","exampleDataCommit":phase.campaign.base.inventory.PIN,
              "fixtureKind":"SYNTHETIC-UNIT-TEST-NOT-CFD","mainApplicationWrites":False}
    geometry_row=json_write(root,"geometry-identity.json",geometry)
    models=[row for row in authored if row["path"] in phase.MODEL_FILES]
    prep={"schemaVersion":phase.campaign.SCHEMA,"exampleDataCommit":phase.campaign.base.inventory.PIN,
          "mode":mode,"declaredStudyInputs":study["input"],"physicalBasis":study["physicalBasis"],
          "sourceBaselineEvidenceSha256":"a"*64,"sourceCaseManifestSha256":"b"*64,
          "sourceGeometryIdentityFile":{"name":"geometry-identity.json","bytes":geometry_row["bytes"],"sha256":geometry_row["sha256"]},
          "sourceGeometryControlSha256":sha256(phase.encoded([geometry_row])).hexdigest(),
          "authoredInputFiles":authored,"preservedNativeMeshFiles":mesh,
          "authoredCavitationModelSha256":sha256(phase.encoded(models)).hexdigest(),
          "nativeCoordinateUnitsVerified":False,"classroomMetresPerSourceCoordinateUnit":1,"inletAbsoluteTotalPressurePa":pressure,
          "temporalPlan":{"maximumTimeStepS":dt,"endTimeS":duration,"rotationPeriodS":period,"nativeCoLimit":.5,"nativeAlphaCoLimit":.25,
                          "stationaryMrfRelaxation":mode=="steady-sweep","physicalRotorMotion":mode=="transient-sliding","timeStepConvergenceVerified":False},
          "simulationExecuted":False,"mainApplicationWrites":False}
    json_write(root,"phase-change-preparation.json",prep)
    json_write(root,"phase-runtime-identity.json",{"fixtureKind":"SYNTHETIC-UNIT-TEST-NOT-NATIVE-RUNTIME"})
    with patch.object(phase,"phase_binary_identity"):
        packet=phase.build_manifest(root)
    json_write(root,"phase-input-manifest.json",packet["manifest"])
    json_write(root,"phase-classroom-study.json",packet["study"])
    return packet


def execution_fixture(root, *, mode="steady-sweep", residual=1e-6, outlet_vapor=0., missing_sampler=None, max_co=.1):
    packet=prepared_fixture(root,mode=mode)
    manifest,study=packet["manifest"],packet["study"]
    rho,g=study["input"]["densityKgM3"],study["input"]["gravityMS2"]
    plan=manifest["temporalPlan"];dt,end=plan["maximumTimeStepS"],plan["endTimeS"]
    count=round(end/dt)
    times=[i*dt for i in range(1,count+1)]
    times[-1]=end
    p0=manifest["pressureLevelAbsolutePa"]
    values={"weightedInlet":(p0-2*rho,p0,4,1),"weightedOutlet":(p0+10*rho*g-4.5*rho,p0+10*rho*g,9,1-outlet_vapor),
            "massFlowInlet":(-90,),"massFlowOutlet":(90,),"massFlowMagnitudeInlet":(90,),"massFlowMagnitudeOutlet":(90,),
            "waterMinimumInlet":(1,),"waterMinimumOutlet":(1-outlet_vapor,),
            "globalPressureMinimum":(30000,),"globalWaterMinimum":(min(1-outlet_vapor,1),),"globalWaterMaximum":(1,),
            "massFlowRotorInterface":(90,),"massFlowStatorInterface":(-90,),
            "massFlowMagnitudeRotorInterface":(90,),"massFlowMagnitudeStatorInterface":(90,)}
    for name,value in values.items():
        if name==missing_sampler:continue
        patch_name="inlet" if name.endswith("Inlet") else "mass-flow-inlet-11" if name.endswith("Outlet") else phase.native.COUPLED_PATCHES[0] if name.endswith("RotorInterface") else phase.native.COUPLED_PATCHES[1] if name.endswith("StatorInterface") else None
        if name.startswith("weighted"):columns=("average(p)","average(totalPressurePa)","average(U2)","average(alpha.water)")
        elif name.startswith("massFlowMagnitude"):columns=("sumMag(rhoPhi)",)
        elif name.startswith("massFlow"):columns=("sum(rhoPhi)",)
        elif name=="globalPressureMinimum":columns=("min(p)",)
        elif name=="globalWaterMaximum":columns=("max(alpha.water)",)
        else:columns=("min(alpha.water)",)
        header=(f"# Selection : patch({patch_name})\n# Faces : 3\n# Area : 0.5\n" if patch_name else "")+"# Time "+" ".join(columns)+"\n"
        text=header+"".join(format(t,".17g")+" "+" ".join(format(v,".17g") for v in value)+"\n" for t in times)
        write(root,"case/postProcessing/"+name+"/0.0001/"+("surfaceFieldValue.dat" if patch_name else "volFieldValue.dat"),text)
    coverage="Source min/average/max coverage = 1/1/1\nTarget min/average/max coverage = 1/1/1\n"
    check="cells: 2\nMin volume = 1e-6. Max volume = 2e-6. Total volume = 3e-6.\nMesh OK.\n"+coverage+"End\n"
    chunks=["Selecting cavitation model SchnerrSauer\n"]
    if mode=="transient-sliding":chunks.append("Selecting solidBodyMotionFunction rotatingMotion\n")
    for t in times:
        chunks.append(f"Courant Number mean: 0.01 max: {max_co}\nInterface Courant Number mean: 0.001 max: 0.01\ndeltaT = {dt:.17g}\nTime = {t:.17g}s\n")
        if mode=="transient-sliding":chunks.append(coverage)
        for outer in range(1,4):
            chunks.append(f"PIMPLE: iteration {outer}\n")
            for field in (("Ux","Uy","Uz","p_rgh","k","omega") if outer==3 else ("Ux","Uy","Uz","p_rgh")):
                chunks.append(f"solver: Solving for {field}, Initial residual = {residual}, Final residual = 1e-10, No Iterations 2\n")
            chunks.append("GAMG: Solving for p_rgh, Initial residual = 1e-10, Final residual = 1e-12, No Iterations 1\ntime step continuity errors : sum local = 1e-6, global = -1e-7, cumulative = -1e-5\n")
    solver="".join(chunks)+"End\n"
    stages=[]
    for index,(program,text) in enumerate((("checkMesh",check),("foamRun",solver))):
        stages.append({**write(root,f"logs/{index:02d}-{program}.log",text),"program":program,"exitCode":0,"elapsedSeconds":1})
    fields=[]
    endpoint=format(end,".17g")
    for name in runner.END_FIELDS:
        kind="volVectorField" if name=="U" else "volScalarField"
        value="(1 2 3)" if name=="U" else "50000" if name in {"p","p_rgh"} else "1"
        raw=f"FoamFile {{format ascii; class {kind}; object {name};}}\ndimensions [{runner.FIELD_DIMENSIONS[name]}];\ninternalField uniform {value};\nboundaryField {{}}\n".encode("ascii")
        fields.append({**write(root,f"case/{endpoint}/{name}",raw),"fieldCheck":runner.finite_field(raw,name,2)})
    execution={"schemaVersion":runner.SCHEMA,"status":"PHASE_CHANGE_EXECUTED_ACCEPTANCE_REQUIRED","mode":mode,
               "fixtureKind":"SYNTHETIC-UNIT-TEST-NOT-CFD","githubRunId":"synthetic-parser-fixture",
               **{key:True for key in ("actualCfdRunVerified","simulationExecuted","nativeCheckMeshVerified","nativeSolverCompleted","cavitationModelExecuted","finiteNativeEndFieldsVerified","inputArtifactsImmutable","sourceBaselineImmutable")},
               "mainApplicationWrites":False,"physicalTransientExecuted":mode=="transient-sliding","stationaryMrfRelaxationExecuted":mode=="steady-sweep",
               "preparationReportSha256":manifest["preparationReportSha256"],"phaseInputManifestSha256":sha256((root/"phase-input-manifest.json").read_bytes()).hexdigest(),
               "phaseStudySha256":sha256((root/"phase-classroom-study.json").read_bytes()).hexdigest(),"phaseRuntimeIdentitySha256":manifest["runtimeIdentitySha256"],
               "sourceBaselineEvidenceSha256":manifest["sourceBaselineEvidenceSha256"],"authoredCavitationModelSha256":manifest["controls"]["cavitationModelSha256"],
               "temporalPlan":plan,"inletAbsoluteTotalPressurePa":p0,"stages":stages,"nativeMeshQuality":phase.native.parse_mesh_quality(check),
               "nativeEndTimeS":end,"finalNativeFieldRecords":fields,"nativeSamplingRecords":runner.sampling_records(root)}
    if mode=="transient-sliding":
        angle=2*math.pi*end/plan["rotationPeriodS"]
        write(root,f"case/{endpoint}/polyMesh/points",phase.campaign.base.foam_header("points","vectorField")+f"2\n((0 0 0)({math.cos(angle):.17g} {math.sin(angle):.17g} 0))\n")
        execution["rotorMotionEvidence"]=runner.rotor_motion(root,[(end,root/"case"/endpoint)],solver)
    json_write(root,"phase-change-execution.json",execution)
    return packet


class NativePhaseReductionContracts(unittest.TestCase):
    def reduce(self,root):
        with patch.object(phase,"phase_binary_identity"):
            return phase.reduce_native_phase(root)

    def test_missing_native_evidence_never_manufactures_result(self):
        with tempfile.TemporaryDirectory() as temp:
            result=self.reduce(Path(temp))
            self.assertFalse(result["actualCfdRunVerified"]);self.assertEqual(result["stages"],{})

    def test_fresh_controls_keep_actual_manifest_pressure_and_dt_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);a=root/"a";b=root/"b";c=root/"c"
            pa=prepared_fixture(a,pressure=50000)
            pb=prepared_fixture(b,pressure=70000,dt=pa["manifest"]["temporalPlan"]["maximumTimeStepS"]/2)
            pc=prepared_fixture(c,mode="transient-sliding")
            self.assertEqual(pa["manifest"]["controls"],pb["manifest"]["controls"])
            self.assertNotEqual(pa["manifest"],pb["manifest"])
            self.assertNotEqual(pa["manifest"]["controls"]["boundarySha256"],pc["manifest"]["controls"]["boundarySha256"])
            self.assertEqual(pa["manifest"]["controls"]["cavitationModelSha256"],pc["manifest"]["controls"]["cavitationModelSha256"])
            self.assertFalse(pa["study"]["sourceReference"]["mainCanonicalReference"])

    def test_changed_authored_input_geometry_or_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepared_fixture(root)
            for name in ("case/0/p_rgh","geometry-identity.json","case/constant/physicalProperties.water"):
                original=(root/name).read_bytes();(root/name).write_bytes(original+b"\n")
                with patch.object(phase,"phase_binary_identity"),self.assertRaises((phase.protocol.ProtocolError,phase.campaign.CampaignIssue)):
                    phase.build_manifest(root)
                (root/name).write_bytes(original)
            with self.assertRaises(phase.protocol.ProtocolError):phase.build_manifest(root)

    def test_physical_tables_reject_wrong_headers_duplicate_times_and_nonfinite(self):
        self.assertEqual(phase.parse_table("# Time min(p)\n0.01 10\n0.02 20\n",("min(p)",)),{.01:[10.],.02:[20.]})
        for text in ("# Time min(p)\n0.01 1\n0.01 2\n","# Time min(p)\n0.01 nan\n","# Time max(p)\n0.01 1\n"):
            with self.assertRaises(phase.protocol.ProtocolError):phase.parse_table(text,("min(p)",))

    def test_nonuniform_time_mean_interpolates_only_inside_actual_support(self):
        table={0.:[0.],.1:[1.],.4:[4.]}
        # interval utility deliberately needs at least five actual points.
        table.update({.05:[.5],.2:[2.]});table=dict(sorted(table.items()))
        self.assertAlmostEqual(phase.time_mean(table,.025,.3)[0],1.625)
        with self.assertRaises(phase.protocol.ProtocolError):phase.time_mean(table,-.1,.3)

    def test_actual_parser_fixture_equations_and_scoped_no_npsh3_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);execution_fixture(root)
            result=self.reduce(root)
            self.assertTrue(result["phaseObservationQualified"],result["diagnostics"])
            self.assertTrue(result["phaseFamilyReferenceEligible"])
            self.assertFalse(result["phaseFamilyReferenceQualified"]);self.assertFalse(result["qualifiedBaseline"])
            self.assertFalse(result["physicalTransientExecuted"]);self.assertTrue(result["stationaryMrfRelaxationExecuted"])
            self.assertAlmostEqual(result["stages"]["head"]["result"]["value"],10)
            self.assertAlmostEqual(result["stages"]["npsha"]["result"]["value"],(50000-2339)/(998.2*9.81))
            self.assertEqual(result["nativeObservation"]["pressureMapping"]["absolutePressureReferencePa"],0)
            self.assertFalse(result["npsh3Verified"]);self.assertFalse(result["restartVerified"])

    def test_missing_outlet_preserves_only_valid_inlet_formula(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);execution_fixture(root,missing_sampler="weightedOutlet")
            result=self.reduce(root)
            self.assertTrue(result["actualCfdRunVerified"])
            self.assertFalse(result["phaseObservationQualified"])
            self.assertIn("npsha",result["stages"]);self.assertNotIn("head",result["stages"])

    def test_vapor_outlet_cannot_block_valid_liquid_inlet_npsha(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);execution_fixture(root,outlet_vapor=.1)
            result=self.reduce(root)
            self.assertIn("npsha",result["stages"]);self.assertNotIn("head",result["stages"])
            self.assertFalse(result["phaseFamilyReferenceEligible"])

    def test_initial_nonlinear_residual_and_authored_courant_limits_remain_gates(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);execution_fixture(root,residual=.1,max_co=.6)
            result=self.reduce(root)
            self.assertFalse(result["phaseObservationQualified"])
            self.assertIn("head",result["stages"])
            self.assertIn("CFD_AUTHORED_PHASE_COURANT_DT_LIMIT_REQUIRED",str(result["diagnostics"]))
            self.assertIn("CFD_RESIDUAL_CONVERGENCE_REQUIRED",str(result["diagnostics"]))

    def test_mutated_execution_fields_or_tables_are_not_actual_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);execution_fixture(root)
            path=next((root/"case/postProcessing/weightedInlet").rglob("*.dat"));path.write_bytes(path.read_bytes()+b"\n")
            result=self.reduce(root)
            self.assertFalse(result["actualCfdRunVerified"]);self.assertEqual(result["stages"],{})

    def test_actual_moved_points_and_sampled_angle_coverage_are_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);execution_fixture(root,mode="transient-sliding")
            result=self.reduce(root)
            self.assertTrue(result["phaseObservationQualified"],result["diagnostics"])
            self.assertTrue(result["physicalTransientExecuted"])
            self.assertTrue(result["nativeCoverageAtSampledRotorStepsVerified"])
            self.assertEqual(len(result["periodMeanHeadM"]),3)
            self.assertFalse(result["timeStepConvergenceVerified"])


if __name__=="__main__":unittest.main()
