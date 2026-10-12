"""Synthetic upstream mocks and real byte/table/log math; never native research."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import reduce_teaching_pump_pressure as tool
import teaching_pump_pressure_case as helper
import run_teaching_pump_pressure as runner

spec=importlib.util.spec_from_file_location("synthetic_phase_fixture",Path(__file__).with_name("test_reduce_teaching_pump_phase.py"))
phase_fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(phase_fixture)
write,json_write=phase_fixture.write,phase_fixture.json_write


def runtime_family(runtime):
    """Mock only installed-binary access; preserve actual typed descriptor data."""
    return {k:runtime[k] for k in ("openFoamFoundationVersion","packageVersion","officialDebSha256","expectedSourceCommit","binaries")}


def source_reference(root):
    packet=tool.reference_reducer.reduce_phase(root)
    assert packet["phaseH0Eligible"],packet["diagnostics"]
    report_row=json_write(root,tool.reference_reducer.REPORT_FILE,packet)
    prep={**tool.record(root,helper.reference_case.REPORT)[0],"defaultFacesPresent":False}
    metadata=tool.record(root,tool.reference_reducer.MANIFEST_FILE)[1]
    return {"directory":root,"packet":packet,"report":packet,"reportFile":report_row,"study":packet["study"],
        "manifest":packet["manifest"],"manifestFile":metadata,"runtime":tool.record(root,tool.reference_reducer.RUNTIME_FILE)[0],
        "preparation":prep,"components":prep["components"],
        "coupledMeshFiles":[tool.read(root,"case/constant/polyMesh/"+n,tool.native.MAX_FIELD)[1] for n in helper.MESH_FILES],
        "geometryIdentity":tool.record(root,"teaching-geometry-identity.json")[0],
        "geometryFile":tool.record(root,"teaching-geometry-identity.json")[1],
        "sourceRecords":helper.source_inventory(root),"defaultFacesPresent":False}


def context(references):
    stack=ExitStack()
    def reference(path):
        name=str(Path(path).resolve())
        if name in references:return references[name]
        # Relocated copies preserve their current actual metadata/bytes; the
        # upstream source admission itself is the explicitly mocked boundary.
        source=next(iter(references.values()));return {**source,"directory":Path(path)}
    def verified(path,**_):
        prep,row=tool.record(path,helper.REPORT)
        source=reference(Path(path)/"source-reference")
        return {"report":prep,"reportFile":row,"sourceReferencePacket":source,"components":prep["components"],
            "nativeGeneratedDataRecords":[],"nativeGeneratedDataEvidence":[]}
    stack.enter_context(patch.object(helper,"verify_reference",side_effect=reference))
    stack.enter_context(patch.object(helper,"verify_output",side_effect=verified))
    stack.enter_context(patch.object(helper,"runtime_family",side_effect=runtime_family))
    stack.enter_context(patch.object(tool,"binary_identity"))
    stack.enter_context(patch.object(tool.native.case_tool,"verify_merged_mesh",return_value={
        "mergedCellCount":2,"mergedPointCount":2,"fixtureKind":"SYNTHETIC-NOT-NATIVE"}))
    return stack


def point_fixture(root,reference,*,pressure=50000.,head=9.9,vapor=.01,minimum_pressure=1000.,residual=1e-6):
    shutil.copytree(reference["directory"],root/"source-reference")
    json_write(root,"teaching-geometry-identity.json",reference["geometryIdentity"])
    authored=[]
    for path,raw in sorted(helper.native_recipe(reference["study"],pressure).items()):
        row=write(root,"case/"+path,raw);authored.append({**row,"path":path})
    meshes=[]
    for row in reference["coupledMeshFiles"]:
        raw=(reference["directory"]/row["path"]).read_bytes();current=write(root,row["path"],raw)
        meshes.append({**current,"path":current["path"].removeprefix("case/")})
    source_rows=[{**r,"path":"source-reference/"+r["path"]} for r in reference["sourceRecords"]]
    prep={"schemaVersion":helper.SCHEMA,"mode":helper.MODE,"sourceGeometryRoute":tool.ROUTE,
        "sourceInputsImmutable":True,"sourceActivePhaseH0NumericallyQualified":True,**{k:False for k in helper.FALSE_FLAGS},
        "sourceReferenceAcceptanceFile":{**reference["reportFile"],"path":"source-reference/"+reference["reportFile"]["path"]},
        "sourceReferenceInputManifestSha256":reference["packet"]["nativeInputManifestSha256"],
        "sourceActivePhaseEvidenceSha256":reference["packet"]["nativeObservation"]["nativeEvidence"]["sha256"],
        "sourceActivePhaseHeadM":reference["packet"]["activePhaseReferenceHeadM"],"sourceReferencePressureAbsolutePa":300000.,
        "sourceGeometryIdentityFile":reference["geometryFile"],"sourceEvidenceFileRecords":source_rows,
        "preservedNativeMeshFiles":meshes,"authoredInputFiles":authored,"defaultFacesPresent":False,
        "authoredCavitationModelSha256":sha256(tool.encoded([r for r in authored if r["path"] in helper.MODEL_FILES])).hexdigest(),
        "declaredStudyInputs":reference["study"]["input"],"physicalBasis":reference["study"]["physicalBasis"],
        "sourceReference":reference["study"]["sourceReference"],"temporalPlan":helper.reference_case.TEMPORAL_PLAN,
        "pressureAbsolutePa":pressure,"inletAbsoluteTotalPressurePa":pressure,
        "absolutePressureMapping":{**helper.reference_case.ABSOLUTE_PRESSURE_MAPPING,"boundary":"prghTotalPressure p0="+format(pressure,".17g")+" Pa absolute inlet total pressure"},
        "nativeCommandArguments":helper.commands(root),"components":reference["components"]}
    prep_row=json_write(root,helper.REPORT,prep)
    runtime={**deepcopy(reference["runtime"]),"schemaVersion":tool.RUNTIME_SCHEMA}
    runtime_row=json_write(root,tool.RUNTIME_FILE,runtime)
    bound=tool.build_manifest(root);manifest_row=json_write(root,tool.MANIFEST_FILE,bound["manifest"]);study_row=json_write(root,tool.STUDY_FILE,bound["study"])
    rho,g=reference["study"]["input"]["densityKgM3"],reference["study"]["input"]["gravityMS2"]
    # Reuse the source fixture's real histories, then author this point's actual
    # pressure/head/alpha histories. Source single-phase and active heads are not
    # substituted into point formula branches.
    shutil.copytree(reference["directory"]/"case/postProcessing",root/"case/postProcessing")
    for name,values in (("weightedInlet",(pressure-2*rho,pressure,4,1)),
        ("weightedOutlet",(pressure+rho*g*head-4.5*rho,pressure+rho*g*head,9,1)),
        ("globalPressureMinimum",(minimum_pressure,)),("globalWaterMinimum",(1-vapor,)),("globalWaterMaximum",(1.,))):
        path=root/"case/postProcessing"/name/"0"/("surfaceFieldValue.dat" if name.startswith("weighted") else "volFieldValue.dat")
        lines=[]
        for line in path.read_text().splitlines():
            lines.append(line if line.startswith("#") else line.split()[0]+" "+" ".join(format(v,".17g") for v in values))
        path.write_text("\n".join(lines)+"\n",encoding="ascii")
    prefixes=["Build : 12-86e126a7bc4d\nnProcs : 1\nExec : "+" ".join([runtime["binaries"][i]["path"],*args[1:]])+"\n"
        for i,args in enumerate(prep["nativeCommandArguments"])]
    check=prefixes[0]+"cells: 2\n*Number of regions: 2\nMin volume = 1e-6. Max volume = 2e-6. Total volume = 3e-6.\nMesh OK.\nSource min/average/max coverage = 1/1/1\nTarget min/average/max coverage = 1/1/1\nEnd\n"
    solver=(reference["directory"]/"logs/01-foamRun.log").read_text()
    solver=prefixes[1]+solver[solver.index("Selecting cavitation model"):]
    if residual!=1e-6:solver=solver.replace("Initial residual = 1e-06","Initial residual = "+str(residual))
    stages=[];logs=[]
    for i,(program,text) in enumerate((("checkMesh",check),("foamRun",solver))):
        row=write(root,f"logs/{i:02d}-{program}.log",text);logs.append(row)
        stages.append({"program":program,"exitCode":0,"elapsedSeconds":1,"log":Path(row["path"]).name,"logBytes":row["bytes"],"logSha256":row["sha256"]})
    end=helper.reference_case.DEFAULT_DURATION_S;endpoint=format(end,".17g")
    for name in tool.fields.END_FIELDS:
        kind="volVectorField" if name=="U" else "volScalarField"
        value="(1 2 3)" if name=="U" else format(minimum_pressure,".17g") if name in {"p","p_rgh"} else "1"
        internal=f"nonuniform List<scalar> 2 ({1-vapor:.17g} 1)" if name=="alpha.water" else "uniform "+value
        write(root,f"case/{endpoint}/{name}",f"FoamFile {{format ascii; class {kind}; object {name};}}\ndimensions [{tool.fields.FIELD_DIMENSIONS[name]}];\ninternalField {internal};\nboundaryField {{}}\n")
    final,_=runner.final_fields(root,prep["temporalPlan"],2,solver)
    pair=tool.reference_reducer.absolute_pressure_reconstruction((root/f"case/{endpoint}/p").read_bytes(),(root/f"case/{endpoint}/p_rgh").read_bytes(),2)
    raw={"schemaVersion":tool.EXECUTION_SCHEMA,"status":runner.STATUS,"sourceGeometryRoute":tool.ROUTE,"mode":helper.MODE,
        "physicalModel":"steady-cavitation-MRF","githubRepository":"simcalc/PyAnsys","githubRunId":str(round(pressure)+10000),"githubSourceCommit":"e"*40,
        **{k:prep[k] for k in ("sourceActivePhaseEvidenceSha256","sourceReferenceInputManifestSha256","sourceActivePhaseHeadM","sourceReferencePressureAbsolutePa",
            "authoredCavitationModelSha256","temporalPlan","pressureAbsolutePa","inletAbsoluteTotalPressurePa","absolutePressureMapping")},
        **{k:False for k in runner.FALSE_FLAGS},**{k:True for k in ("actualCfdRunVerified","simulationExecuted","nativeCheckMeshVerified","nativeGeometryQualityVerified",
            "physicalWallTypesVerified","coupledInterfaceCoverageVerified","sourceActivePhaseH0NumericallyQualified","stationaryMrfRelaxationExecuted",
            "nativeSolverCompleted","cavitationModelExecuted","finiteNativeEndFieldsVerified","inputArtifactsImmutable","sourceReferenceImmutable")},
        "preparationReportSha256":prep_row["sha256"],"runtimeIdentitySha256":runtime_row["sha256"],"inputManifestSha256":manifest_row["sha256"],"studySha256":study_row["sha256"],
        "stages":stages,"retainedNativeLogRecords":logs,"mergedNativeMeshEvidence":{"mergedCellCount":2,"mergedPointCount":2,"fixtureKind":"SYNTHETIC-NOT-NATIVE"},
        "nativeMeshQuality":tool.native.parse_mesh_quality(check),"finalNativeFieldRecords":final,"nativeEndTimeS":end,"endpointAbsolutePressureReconstruction":pair,
        "nativeSamplingRecords":runner.sampling_records(root),"nativeNccDiagnosticRecords":runner.ncc_diagnostic_records(root),
        "nativeCheckMeshDiagnosticRecords":[],"nativeNearPointsEvidence":None,"nativeGeneratedDataRecords":[],"nativeGeneratedDataEvidence":[],
        "limits":dict(runner.LIMITS),"elapsedSeconds":20,"workspaceBytes":runner.workspace_size(root)}
    json_write(root,tool.EXECUTION_FILE,raw)
    return bound


def refresh(root,*,fields_changed=False):
    raw=tool.record(root,tool.EXECUTION_FILE)[0];logs=[]
    for stage in raw["stages"]:
        data,row=tool.read(root,"logs/"+stage["log"],8*1024**2)
        stage.update(logBytes=len(data),logSha256=row["sha256"]);logs.append(row)
    raw["retainedNativeLogRecords"]=logs;raw["nativeSamplingRecords"]=runner.sampling_records(root)
    if fields_changed:
        raw["finalNativeFieldRecords"]=runner.final_fields(root,raw["temporalPlan"],2,(root/"logs/01-foamRun.log").read_text())[0]
        rows={Path(r["path"]).name:r for r in raw["finalNativeFieldRecords"]}
        raw["endpointAbsolutePressureReconstruction"]=tool.reference_reducer.absolute_pressure_reconstruction((root/rows["p"]["path"]).read_bytes(),(root/rows["p_rgh"]["path"]).read_bytes(),2)
    raw["workspaceBytes"]=runner.workspace_size(root)-(root/tool.EXECUTION_FILE).stat().st_size;json_write(root,tool.EXECUTION_FILE,raw)


class PressureReduction(unittest.TestCase):
    def setup_source(self,temp):
        root=Path(temp)/"reference";root.mkdir()
        with phase_fixture.context():phase_fixture.fixture(root);ref=source_reference(root)
        # Actual full source/native binary/geometry admission is mocked by
        # context(), while its head and scientific tables were really reduced.
        refs={str(root.resolve()):ref}
        return ref,refs

    def test_missing_actual_point_or_h0_never_calculates_npshr(self):
        with tempfile.TemporaryDirectory() as temp:
            result=tool.reduce_pressure(temp);self.assertFalse(result["actualCfdRunVerified"]);self.assertFalse(result["npshrCalculated"])

    def test_pressure_only_family_keeps_actual_inputs_and_cavitating_point_math(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                root=Path(temp)/"point";root.mkdir();point_fixture(root,ref);result=tool.reduce_pressure(root)
                self.assertTrue(result["numericalPressurePointQualified"],result["diagnostics"])
                self.assertAlmostEqual(result["stages"]["head"]["result"]["value"],9.9)
                self.assertAlmostEqual(result["stages"]["npsha"]["result"]["value"],(50000-2339)/(998.2*9.81))
                self.assertAlmostEqual(result["nativeObservation"]["quality"]["maximumVaporFraction"],.01)
                self.assertEqual(result["study"]["sourceReference"]["kind"],"classroom-authored-SI-pump-CAD")
                proof=result["manifest"]["pressureNormalizationProof"]
                self.assertFalse(proof["sentinelIsNativeInput"]);self.assertEqual(proof["nativePressureAbsolutePa"],50000)
                self.assertNotEqual(proof["nativeFileSha256"],proof["normalizedFileSha256"])
                self.assertEqual(result["nativeObservation"]["nativeEvidence"]["sha256"],sha256(tool.encoded(result["nativeEvidenceRecords"])).hexdigest())
                for flag in tool.FALSE_CLAIMS:self.assertFalse(result[flag],flag)
                self.assertFalse(result["npshrCalculated"]);self.assertFalse(result["phaseH0Eligible"])

    def test_actual_h0_and_one_lower_point_are_a_valid_bracket_not_a_certificate(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                root=Path(temp)/"low";root.mkdir();point_fixture(root,ref,pressure=20000,head=9.5)
                result=tool.aggregate_pressure_sweep(ref["directory"],[root])
                self.assertTrue(result["pressureBracketVerified"],result["diagnostics"]);self.assertTrue(result["npshrCalculated"])
                self.assertTrue(result["sourceReferenceIncludedAsMeasuredHighPressurePoint"])
                low=(20000-2339)/(998.2*9.81);high=(300000-2339)/(998.2*9.81)
                self.assertAlmostEqual(result["npsh3M"],low+(.97-.95)*(high-low)/(1-.95))
                for observed,expected in zip(result["actualNpshaBracketM"],(low,high)):
                    self.assertAlmostEqual(observed,expected)
                for flag in tool.FALSE_CLAIMS:self.assertFalse(result[flag],flag)
                self.assertFalse(result["pressureResolutionVerified"]);self.assertFalse(result["uncertainty"]["numericalConfidenceIntervalAvailable"])

    def test_pressure_endpoint_replays_teaching_dimensionless_alpha_without_rewriting_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                root=Path(temp)/"point";root.mkdir();point_fixture(root,ref,pressure=20000,head=9.5)
                row=next(r for r in tool.record(root,tool.EXECUTION_FILE)[0]["finalNativeFieldRecords"] if r["path"].endswith("/alpha.water"))
                target=root/row["path"];previous=target.read_bytes()
                empty=previous.replace(b"dimensions [0 0 0 0 0 0 0];",b"dimensions [];")
                self.assertNotEqual(previous,empty);target.write_bytes(empty);refresh(root,fields_changed=True)
                point=tool.reduce_pressure(root)
                self.assertTrue(point["numericalPressurePointQualified"],point["diagnostics"])
                actual={"path":row["path"],"bytes":len(empty),"sha256":sha256(empty).hexdigest()}
                self.assertIn(actual,point["nativeEvidenceRecords"])
                self.assertNotEqual(actual["sha256"],row["sha256"]);self.assertEqual(target.read_bytes(),empty)
                result=tool.aggregate_pressure_sweep(ref["directory"],[root])
                self.assertTrue(result["pressureBracketVerified"],result["diagnostics"])
                self.assertFalse(result["npsh3Qualified"])

    def test_additional_actual_point_tightens_source_bracket_without_extrapolation(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                points=[]
                for name,p,h in (("low",20000,9.5),("high",50000,9.9)):
                    root=Path(temp)/name;root.mkdir();point_fixture(root,ref,pressure=p,head=h);points.append(root)
                result=tool.aggregate_pressure_sweep(ref["directory"],points)
                self.assertTrue(result["pressureBracketVerified"],result["diagnostics"])
                self.assertAlmostEqual(result["npsh3M"],(35000-2339)/(998.2*9.81))
                self.assertLess(result["bracketWidthM"],(300000-20000)/(998.2*9.81))

    def test_no_actual_crossing_keeps_calculation_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                root=Path(temp)/"point";root.mkdir();point_fixture(root,ref,head=9.9)
                result=tool.aggregate_pressure_sweep(ref["directory"],[root])
                self.assertTrue(result["numericalPressureSweepAccepted"],result["diagnostics"])
                self.assertFalse(result["npshrCalculated"]);self.assertIsNone(result["npsh3M"])
                self.assertEqual(result["status"],"PressureCrossingNotYetBracketed")

    def test_positive_npsha_does_not_admit_static_suction_flashing_to_bracket(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                root=Path(temp)/"flashing";root.mkdir();point_fixture(root,ref,pressure=4000,head=9.5)
                point=tool.reduce_pressure(root)
                self.assertTrue(point["actualCfdRunVerified"],point["diagnostics"])
                self.assertTrue(point["numericalPressurePointQualified"],point["diagnostics"])
                self.assertGreater(point["stages"]["npsha"]["result"]["value"],0)
                self.assertLess(point["stages"]["npsha"]["input"]["pStaticAbsolutePa"],2339)
                self.assertEqual(point["npshaDomainStatus"],"FLASHING_CAVITATION_RISK")
                self.assertFalse(point["npsh3BracketEligible"])
                result=tool.aggregate_pressure_sweep(ref["directory"],[root])
                self.assertTrue(result["actualCfdRunVerified"])
                self.assertTrue(result["cavitationSweepExecuted"])
                self.assertFalse(result["pressureBracketVerified"]);self.assertFalse(result["npshrCalculated"])
                self.assertEqual(len(result["qualifiedPointRecords"]),1)
                self.assertEqual(len(result["excludedPointRecords"]),1)
                self.assertIn("LIQUID_SUCTION_DOMAIN_REQUIRED",str(result["diagnostics"]))

    def test_excluded_flashing_point_does_not_block_an_independent_liquid_bracket(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                points=[]
                for name,p,h in (("flashing",4000,9.4),("liquid",20000,9.5)):
                    root=Path(temp)/name;root.mkdir();point_fixture(root,ref,pressure=p,head=h);points.append(root)
                result=tool.aggregate_pressure_sweep(ref["directory"],points)
                self.assertTrue(result["pressureBracketVerified"],result["diagnostics"])
                self.assertEqual(len(result["excludedPointRecords"]),1)
                self.assertEqual(len(result["qualifiedPointRecords"]),2)
                self.assertAlmostEqual(result["actualNpshaBracketM"][0],(20000-2339)/(998.2*9.81))
                self.assertAlmostEqual(result["qualifiedPointRecords"][0]["headRatioStage"]["result"]["value"],.94)
                self.assertFalse(result["npsh3Qualified"])

    def test_nonpressure_recipe_mutation_even_rehashed_cannot_join_family(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                root=Path(temp)/"point";root.mkdir();point_fixture(root,ref)
                path=root/"case/constant/MRFProperties";path.write_bytes(path.read_bytes().replace(b"1450",b"1400"))
                prep=tool.record(root,helper.REPORT)[0]
                for row in prep["authoredInputFiles"]:
                    if row["path"]=="constant/MRFProperties":row.update(bytes=path.stat().st_size,sha256=sha256(path.read_bytes()).hexdigest())
                json_write(root,helper.REPORT,prep)
                result=tool.reduce_pressure(root);self.assertFalse(result["actualCfdRunVerified"])

    def test_point_convergence_mass_and_pressure_endpoint_fail_independently(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                root=Path(temp)/"point";root.mkdir();point_fixture(root,ref,residual=.1)
                result=tool.reduce_pressure(root);self.assertTrue(result["actualCfdRunVerified"]);self.assertFalse(result["numericalPressurePointQualified"])
                self.assertIn("CFD_RESIDUAL_CONVERGENCE_REQUIRED",str(result["diagnostics"]))
                data=root/"case/postProcessing/massFlowOutlet/0/surfaceFieldValue.dat"
                data.write_bytes(data.read_bytes().replace(b" 90\n",b" 80\n"));refresh(root)
                self.assertFalse(tool.reduce_pressure(root)["numericalPressurePointQualified"])
                data=root/"case/postProcessing/globalPressureMinimum/0/volFieldValue.dat"
                data.write_bytes(data.read_bytes().replace(b"1000",b"999"));refresh(root)
                result=tool.reduce_pressure(root);self.assertTrue(result["actualCfdRunVerified"])
                self.assertIn("GLOBAL_FIELD_TABLE_CONSISTENCY",str(result["diagnostics"]))

    def test_unqualified_or_duplicate_points_do_not_yield_npshr(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                a=Path(temp)/"a";a.mkdir();point_fixture(a,ref,pressure=20000,head=9.5)
                result=tool.aggregate_pressure_sweep(ref["directory"],[a,a]);self.assertFalse(result["npshrCalculated"])
                b=Path(temp)/"b";b.mkdir();point_fixture(b,ref,pressure=20000,head=9.5)
                result=tool.aggregate_pressure_sweep(ref["directory"],[a,b]);self.assertFalse(result["npshrCalculated"])
                self.assertIn("CFD_DISTINCT_PRESSURE_SWEEP_REQUIRED",str(result["diagnostics"]))

    def test_source_liquid_h0_is_required_not_a_singlephase_head(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                root=Path(temp)/"point";root.mkdir();point_fixture(root,ref)
                ref["packet"]["phaseH0Eligible"]=False
                result=tool.reduce_pressure(root);self.assertFalse(result["actualCfdRunVerified"])
                ref["packet"]["phaseH0Eligible"]=True;ref["packet"]["nativeObservation"]["quality"]["maximumVaporFraction"]=.1
                result=tool.reduce_pressure(root);self.assertFalse(result["actualCfdRunVerified"])

    def test_typed_resources_actual_selector_and_data_hash_mutation_reject(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                root=Path(temp)/"point";root.mkdir();point_fixture(root,ref)
                raw=tool.record(root,tool.EXECUTION_FILE)[0];raw["limits"]["maximumCpus"]=True;json_write(root,tool.EXECUTION_FILE,raw)
                self.assertFalse(tool.reduce_pressure(root)["actualCfdRunVerified"])
                raw["limits"]=dict(runner.LIMITS);json_write(root,tool.EXECUTION_FILE,raw);refresh(root)
                path=root/"logs/01-foamRun.log";path.write_bytes(path.read_bytes().replace(b"SchnerrSauer",b"OtherModel"));refresh(root)
                self.assertFalse(tool.reduce_pressure(root)["actualCfdRunVerified"])

    def test_independent_npsha_survives_unavailable_outlet(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                root=Path(temp)/"point";root.mkdir();point_fixture(root,ref)
                shutil.rmtree(root/"case/postProcessing/weightedOutlet");refresh(root)
                result=tool.reduce_pressure(root);self.assertTrue(result["actualCfdRunVerified"])
                self.assertEqual(set(result["stages"]),{"npsha"});self.assertFalse(result["numericalPressurePointQualified"])

    def test_changed_actual_bytes_during_reduction_revoke_current_results(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                root=Path(temp)/"point";root.mkdir();point_fixture(root,ref)
                original=tool.numerical_observation
                def changed(*args):
                    original(*args);path=root/"case/postProcessing/globalPressureMinimum/0/volFieldValue.dat";path.write_bytes(path.read_bytes()+b"\n# SYNTHETIC-MUTATION\n")
                with patch.object(tool,"numerical_observation",side_effect=changed):result=tool.reduce_pressure(root)
                self.assertFalse(result["actualCfdRunVerified"]);self.assertEqual(result["stages"],{})

    def test_exact_three_percent_boundary_is_an_actual_measured_endpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            ref,refs=self.setup_source(temp)
            with context(refs):
                root=Path(temp)/"point";root.mkdir();point_fixture(root,ref,pressure=35000,head=9.7)
                result=tool.aggregate_pressure_sweep(ref["directory"],[root])
                self.assertTrue(result["pressureBracketVerified"],result["diagnostics"])
                self.assertAlmostEqual(result["npsh3M"],(35000-2339)/(998.2*9.81))
                self.assertEqual(result["protocolPressureSweep"]["ratioEqualityRoundoffTolerance"],1e-12)

    def test_binary_document_schema_and_current_actual_bytes_are_independent_guards(self):
        raw=b"SYNTHETIC-NOT-ACTUAL-BINARY"
        identity={"schemaVersion":tool.RUNTIME_SCHEMA,"openFoamFoundationVersion":12,"packageVersion":"20250206",
            "officialDebSha256":helper.safety.PACKAGE_SHA,"expectedSourceCommit":helper.safety.SOURCE_COMMIT,
            "mainApplicationWrites":False,"binaries":[{"program":p,"path":"/opt/openfoam12/bin/"+p,"bytes":len(raw),
                "sha256":sha256(raw).hexdigest(),"packageOwnership":"openfoam12: /opt/openfoam12/bin/"+p} for p in tool.PROGRAMS]}
        with patch.object(tool.native.case_tool,"read_regular",return_value=raw) as reader:
            tool.binary_identity(identity);self.assertEqual(reader.call_count,2)
        with patch.object(tool.native.case_tool,"read_regular",return_value=raw+b"changed"),self.assertRaises(tool.protocol.ProtocolError):
            tool.binary_identity(identity)
        identity["schemaVersion"]=tool.reference_reducer.RUNTIME_SCHEMA
        with patch.object(tool.native.case_tool,"read_regular",side_effect=AssertionError("Wrong document role cannot read native binaries")),self.assertRaises(tool.protocol.ProtocolError):
            tool.binary_identity(identity)


if __name__=="__main__":unittest.main()
