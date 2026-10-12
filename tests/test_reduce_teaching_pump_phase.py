"""Synthetic fixtures exercise real field/log/table math, never claim native CFD."""
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import reduce_teaching_pump_phase as tool
import run_teaching_pump_phase as runner


def write(root,path,value):
    raw = value if isinstance(value,bytes) else value.encode("ascii")
    target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    return {"path":path,"bytes":len(raw),"sha256":sha256(raw).hexdigest()}


def json_write(root,path,value):return write(root,path,tool.encoded(value))


def context():
    """Heavy source/native topology/binary admission alone is explicitly mocked."""
    stack=ExitStack()
    stack.enter_context(patch.object(tool.preparation,"verify_output",side_effect=lambda root,**_:{
        "report":tool.record(root,tool.preparation.REPORT)[0],"reportFile":tool.record(root,tool.preparation.REPORT)[1],
        "nativeGeneratedDataRecords":[],"nativeGeneratedDataEvidence":[]}))
    stack.enter_context(patch.object(tool,"binary_identity"))
    stack.enter_context(patch.object(tool.native.case_tool,"verify_merged_mesh",return_value={
        "mergedCellCount":2,"mergedPointCount":2,"fixtureKind":"SYNTHETIC-TOPOLOGY-NOT-NATIVE"}))
    return stack


def fixture(root,*,residual=1e-6,vapor=0.,outlet_vapor=0.,minimum_pressure=200000.,outlet_flow=90.,
    interface_flow=-90.,continuity=1e-6,max_co=.1,head_drift=0.,missing_sampler=None,coverage=1.):
    helper=tool.preparation;plan=tool.baseline.author.teaching_plan()
    study=tool.baseline.study_packet(plan["parameters"],{})
    authored=[]
    for path,raw in sorted(helper.native_recipe(study).items()):
        row=write(root,"case/"+path,raw);authored.append({**row,"path":path})
    meshes=[]
    for name in helper.MESH_FILES:
        row=write(root,"case/constant/polyMesh/"+name,"SYNTHETIC-NOT-ACTUAL-MESH:"+name)
        meshes.append({**row,"path":"constant/polyMesh/"+name})
    identity_row=json_write(root,tool.baseline.GEOMETRY_FILE,plan["geometryIdentity"])
    source_input=json_write(root,"source-baseline/source-teaching/source-geometry/teaching-input-manifest.json",{
        "parameters":plan["parameters"],"generatorSourceCommit":"a"*40,"fixtureKind":"SYNTHETIC-NOT-CAD"})
    source_packet=json_write(root,"source-baseline/teaching-baseline-acceptance.json",{
        "manifest":{"sourceGithubRunId":"12345"},"singlePhaseHeadM":-999,"fixtureKind":"SYNTHETIC-NOT-ACTUAL-BASELINE"})
    model_rows=[r for r in authored if r["path"] in helper.MODEL_FILES]
    prep={"schemaVersion":helper.SCHEMA,"mode":helper.MODE,"sourceGeometryRoute":tool.ROUTE,
        "inletAbsoluteTotalPressurePa":300000.,**{k:True for k in helper.SOURCE_TRUE_FLAGS},
        **{k:False for k in helper.FALSE_FLAGS},"sourceGeometryIdentityFile":identity_row,
        "sourceGeometryControlSha256":sha256(tool.encoded([identity_row])).hexdigest(),
        "sourceBaselineEvidenceSha256":"b"*64,"sourceCaseManifestSha256":"c"*64,
        "sourceBaselineAcceptanceFile":source_packet,"sourceEvidenceFileRecords":[source_input,source_packet],
        "preservedNativeMeshFiles":meshes,"authoredInputFiles":authored,
        "authoredCavitationModelSha256":sha256(tool.encoded(model_rows)).hexdigest(),
        "declaredStudyInputs":study["input"],"physicalBasis":study["physicalBasis"],"sourceReference":study["sourceReference"],
        "temporalPlan":helper.TEMPORAL_PLAN,"absolutePressureMapping":helper.ABSOLUTE_PRESSURE_MAPPING,
        "initialization":"SYNTHETIC uniform liquid/zeroU freshstate; source fields not copied",
        "nativeCommandArguments":helper.commands(root),
        "components":[{"component":"impeller","cellCount":1,"nodeCount":1},{"component":"volute","cellCount":1,"nodeCount":1}]}
    prep_row=json_write(root,helper.REPORT,prep)
    binary=b"SYNTHETIC-NOT-ACTUAL-BINARY"
    runtime={"schemaVersion":tool.RUNTIME_SCHEMA,"openFoamFoundationVersion":12,"packageVersion":"20250206",
        "officialDebSha256":helper.safety.PACKAGE_SHA,"expectedSourceCommit":helper.safety.SOURCE_COMMIT,
        "mainApplicationWrites":False,"binaries":[{"program":p,"path":"/opt/openfoam12/bin/"+p,"bytes":len(binary),
            "sha256":sha256(binary).hexdigest(),"packageOwnership":"openfoam12: /opt/openfoam12/bin/"+p} for p in tool.PROGRAMS]}
    runtime_row=json_write(root,tool.RUNTIME_FILE,runtime)
    bound=tool.build_manifest(root)
    manifest_row=json_write(root,tool.MANIFEST_FILE,bound["manifest"]);study_row=json_write(root,tool.STUDY_FILE,bound["study"])
    dt,end=helper.MAX_DT_S,helper.DEFAULT_DURATION_S
    times=[i*dt for i in range(1,round(end/dt)+1)];times[-1]=end
    rho,g=study["input"]["densityKgM3"],study["input"]["gravityMS2"]
    values={"weightedInlet":(300000-2*rho,300000,4,1),"weightedOutlet":(300000+10*rho*g-4.5*rho,300000+10*rho*g,9,1-outlet_vapor),
        "massFlowInlet":(-90,),"massFlowOutlet":(outlet_flow,),"massFlowMagnitudeInlet":(90,),"massFlowMagnitudeOutlet":(abs(outlet_flow),),
        "waterMinimumInlet":(1,),"waterMinimumOutlet":(1-outlet_vapor,),"globalPressureMinimum":(minimum_pressure,),
        "globalWaterMinimum":(1-vapor,),"globalWaterMaximum":(1,),"massFlowRotorInterface":(90,),"massFlowStatorInterface":(interface_flow,),
        "massFlowMagnitudeRotorInterface":(90,),"massFlowMagnitudeStatorInterface":(abs(interface_flow),)}
    for name,(columns,section) in tool.equations.sampler_roles().items():
        if name==missing_sampler:continue
        header=(f"# Selection : patch({section})\n# Faces : 3\n# Area : 0.5\n" if section else "")+"# Time "+" ".join(columns)+"\n"
        lines=[]
        for t in times:
            row=list(values[name])
            if name=="weightedOutlet":row[0]+=head_drift*rho*g*t/end;row[1]+=head_drift*rho*g*t/end
            lines.append(format(t,".17g")+" "+" ".join(format(v,".17g") for v in row)+"\n")
        write(root,"case/postProcessing/"+name+"/0/"+("surfaceFieldValue.dat" if section else "volFieldValue.dat"),header+"".join(lines))
    coverage_text=f"Source min/average/max coverage = {coverage}/1/1\nTarget min/average/max coverage = {coverage}/1/1\n"
    prefixes=[]
    for args in prep["nativeCommandArguments"]:
        prefixes.append("Build : 12-86e126a7bc4d\nnProcs : 1\nExec : "+" ".join(["/opt/openfoam12/bin/"+args[0],*args[1:]])+"\n")
    check=prefixes[0]+"cells: 2\n*Number of regions: 2\nMin volume = 1e-6. Max volume = 2e-6. Total volume = 3e-6.\nMesh OK.\n"+coverage_text+"End\n"
    chunks=[prefixes[1],"Selecting cavitation model SchnerrSauer\n"]
    for t in times:
        chunks.append(f"Courant Number mean: 0.01 max: {max_co}\nInterface Courant Number mean: 0.001 max: 0.01\ndeltaT = {dt:.17g}\nTime = {t:.17g}s\n")
        for outer in range(1,4):
            chunks.append(f"PIMPLE: iteration {outer}\n")
            for name in (("Ux","Uy","Uz","p_rgh","k","omega") if outer==3 else ("Ux","Uy","Uz","p_rgh")):
                chunks.append(f"solver: Solving for {name}, Initial residual = {residual}, Final residual = 1e-10, No Iterations 2\n")
            chunks.append(f"GAMG: Solving for p_rgh, Initial residual = 1e-10, Final residual = 1e-12, No Iterations 1\ntime step continuity errors : sum local = {continuity}, global = -1e-7, cumulative = -1e-5\n")
    solver="".join(chunks)+"End\n";stages=[]
    for i,(program,text) in enumerate((("checkMesh",check),("foamRun",solver))):
        row=write(root,f"logs/{i:02d}-{program}.log",text)
        stages.append({"program":program,"exitCode":0,"elapsedSeconds":1,"log":Path(row["path"]).name,
            "logBytes":row["bytes"],"logSha256":row["sha256"]})
    endpoint=format(end,".17g")
    for name in tool.fields.END_FIELDS:
        kind="volVectorField" if name=="U" else "volScalarField"
        value="(1 2 3)" if name=="U" else format(minimum_pressure,".17g") if name in {"p","p_rgh"} else "1"
        internal=f"nonuniform List<scalar> 2 ({1-vapor:.17g} 1)" if name=="alpha.water" else "uniform "+value
        write(root,f"case/{endpoint}/{name}",f"FoamFile {{format ascii; class {kind}; object {name};}}\ndimensions [{tool.fields.FIELD_DIMENSIONS[name]}];\ninternalField {internal};\nboundaryField {{}}\n")
    final,_=runner.final_fields(root,prep["temporalPlan"],2,solver)
    raw={"schemaVersion":tool.EXECUTION_SCHEMA,"status":runner.STATUS,"sourceGeometryRoute":tool.ROUTE,"mode":helper.MODE,
        "physicalModel":"steady-cavitation-MRF","githubRepository":"simcalc/PyAnsys","githubRunId":"67890","githubSourceCommit":"d"*40,
        "inletAbsoluteTotalPressurePa":300000.,"absolutePressureMapping":prep["absolutePressureMapping"],
        **{k:False for k in runner.FALSE_FLAGS},**{k:True for k in ("actualCfdRunVerified","simulationExecuted","nativeCheckMeshVerified",
            "nativeGeometryQualityVerified","physicalWallTypesVerified","coupledInterfaceCoverageVerified","sourceNumericalBaselineQualified",
            "stationaryMrfRelaxationExecuted","nativeSolverCompleted","cavitationModelExecuted","finiteNativeEndFieldsVerified","inputArtifactsImmutable","sourceBaselineImmutable")},
        "preparationReportSha256":prep_row["sha256"],"runtimeIdentitySha256":runtime_row["sha256"],
        "inputManifestSha256":manifest_row["sha256"],"studySha256":study_row["sha256"],"sourceBaselineEvidenceSha256":"b"*64,
        "authoredCavitationModelSha256":prep["authoredCavitationModelSha256"],"temporalPlan":prep["temporalPlan"],"stages":stages,
        "mergedNativeMeshEvidence":{"mergedCellCount":2,"mergedPointCount":2,"fixtureKind":"SYNTHETIC-TOPOLOGY-NOT-NATIVE"},
        "nativeMeshQuality":tool.native.parse_mesh_quality(check),"finalNativeFieldRecords":final,"nativeEndTimeS":end,
        "nativeSamplingRecords":runner.sampling_records(root),"nativeNccDiagnosticRecords":runner.ncc_diagnostic_records(root),
        "limits":dict(runner.LIMITS),"workspaceBytes":runner.workspace_size(root),"elapsedSeconds":20}
    json_write(root,tool.EXECUTION_FILE,raw)
    return bound


def refresh(root,*,fields_changed=False):
    raw=tool.record(root,tool.EXECUTION_FILE)[0]
    for stage in raw["stages"]:
        data=(root/"logs"/stage["log"]).read_bytes();stage.update(logBytes=len(data),logSha256=sha256(data).hexdigest())
    raw["nativeSamplingRecords"]=runner.sampling_records(root);raw["nativeNccDiagnosticRecords"]=runner.ncc_diagnostic_records(root)
    if fields_changed:
        raw["finalNativeFieldRecords"]=runner.final_fields(root,raw["temporalPlan"],2,(root/"logs/01-foamRun.log").read_text())[0]
    raw["workspaceBytes"]=runner.workspace_size(root)-(root/tool.EXECUTION_FILE).stat().st_size
    json_write(root,tool.EXECUTION_FILE,raw)


class TeachingActivePhaseReduction(unittest.TestCase):
    def test_missing_evidence_cannot_create_head(self):
        with tempfile.TemporaryDirectory() as temp:
            result=tool.reduce_phase(temp)
            self.assertFalse(result["actualCfdRunVerified"]);self.assertFalse(result["phaseH0Eligible"]);self.assertEqual(result["stages"],{})

    def test_distinct_authored_reference_uses_actual_active_head_absolute_pa(self):
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);bound=fixture(root);result=tool.reduce_phase(root)
            self.assertTrue(result["phaseH0Eligible"],result["diagnostics"]);self.assertTrue(result["actualCfdRunVerified"])
            self.assertAlmostEqual(result["activePhaseReferenceHeadM"],10)
            self.assertAlmostEqual(result["stages"]["npsha"]["result"]["value"],(300000-2339)/(998.2*9.81))
            self.assertEqual(result["study"]["schemaVersion"],tool.STUDY_SCHEMA)
            self.assertEqual(result["study"]["sourceReference"]["kind"],"classroom-authored-SI-pump-CAD")
            self.assertNotIn("exampleDataCommit",bound["manifest"])
            self.assertNotIn("fluent.docs",json.dumps(result["study"]["sourceReference"]))
            self.assertEqual(result["manifest"]["sourceGithubRunId"],"12345")
            self.assertTrue(result["endpointGlobalFieldTableConsistencyVerified"])
            self.assertEqual(result["endpointAbsolutePressureReconstruction"]["allInternalCellsChecked"],2)
            for flag in tool.FALSE_CLAIMS:self.assertFalse(result[flag],flag)
            self.assertEqual(result["nativeObservation"]["nativeEvidence"]["sha256"],sha256(tool.encoded(result["nativeEvidenceRecords"])).hexdigest())

    def test_high_initial_residual_is_not_hidden_by_linear_final(self):
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root,residual=.1);result=tool.reduce_phase(root)
            self.assertTrue(result["actualCfdRunVerified"]);self.assertFalse(result["phaseH0Eligible"])
            self.assertIn("CFD_RESIDUAL_CONVERGENCE_REQUIRED",str(result["diagnostics"]))

    def test_native_dimensionless_alpha_is_semantic_only_with_original_bytes_bound(self):
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root)
            row=next(r for r in tool.record(root,tool.EXECUTION_FILE)[0]["finalNativeFieldRecords"] if r["path"].endswith("/alpha.water"))
            target=root/row["path"];previous=target.read_bytes()
            empty=previous.replace(b"dimensions [0 0 0 0 0 0 0];",b"dimensions [];")
            self.assertNotEqual(previous,empty);target.write_bytes(empty);refresh(root,fields_changed=True)
            result=tool.reduce_phase(root)
            self.assertTrue(result["phaseH0Eligible"],result["diagnostics"])
            actual={"path":row["path"],"bytes":len(empty),"sha256":sha256(empty).hexdigest()}
            self.assertIn(actual,result["nativeEvidenceRecords"])
            self.assertNotEqual(actual["sha256"],row["sha256"])
            self.assertEqual(target.read_bytes(),empty)
            self.assertEqual(result["endpointInternalFieldExtrema"]["waterMinimum"],1)
            self.assertFalse(result["qualifiedBaseline"])
            target.write_bytes(empty.replace(b"dimensions [];",b"dimensions [0 0 1 0 0 0 0];"))
            result=tool.reduce_phase(root)
            self.assertFalse(result["actualCfdRunVerified"]);self.assertFalse(result["phaseH0Eligible"])

    def test_vapour_or_flashing_pressure_rejects_high_pressure_h0(self):
        for options in ({"vapor":.002},{"minimum_pressure":2000}):
            with self.subTest(options=options),tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root,**options);result=tool.reduce_phase(root)
                self.assertTrue(result["actualCfdRunVerified"]);self.assertFalse(result["phaseH0Eligible"])
                self.assertNotIn("activePhaseReferenceHeadM",result)
                if "vapor" in options:self.assertTrue(result["phaseObservationQualified"])

    def test_outlet_phase_or_missing_head_preserves_independent_npsha(self):
        for options in ({"outlet_vapor":.01},{"missing_sampler":"weightedOutlet"}):
            with self.subTest(options=options),tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root,**options);result=tool.reduce_phase(root)
                self.assertTrue(result["actualCfdRunVerified"]);self.assertFalse(result["phaseH0Eligible"])
                self.assertEqual(set(result["stages"]),{"npsha"})

    def test_signed_flow_interface_continuity_courant_stability_gates(self):
        for options in ({"outlet_flow":80},{"interface_flow":-80},{"continuity":.01},{"max_co":.8},{"head_drift":2},{"coverage":.68}):
            with self.subTest(options=options),tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root,**options);result=tool.reduce_phase(root)
                self.assertTrue(result["actualCfdRunVerified"]);self.assertFalse(result["phaseH0Eligible"])

    def test_source_model_mesh_manifest_mutation_is_not_native_acceptance(self):
        for path,old,new in ((tool.preparation.REPORT,b'"sourceNumericalBaselineQualified":true',b'"sourceNumericalBaselineQualified":false'),
            ("case/constant/fvModels",b"SchnerrSauer",b"OtherModel"),("case/constant/polyMesh/owner",b"MESH",b"CHANGED"),
            (tool.MANIFEST_FILE,b"300000.0",b"299999.0")):
            with self.subTest(path=path),tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root);target=root/path;raw=target.read_bytes();self.assertIn(old,raw);target.write_bytes(raw.replace(old,new))
                result=tool.reduce_phase(root);self.assertFalse(result["actualCfdRunVerified"]);self.assertFalse(result["phaseH0Eligible"])

    def test_actual_selector_native_program_or_sliding_mismatch_rejects(self):
        for old,new in ((b"Selecting cavitation model SchnerrSauer",b"Selecting cavitation model OtherModel"),
            (b"Selecting cavitation model SchnerrSauer",b"Selecting cavitation model SchnerrSauer\nSelecting motion solver motion"),
            (b"nProcs : 1",b"nProcs : 2"),(b"Exec : /opt/openfoam12/bin/foamRun",b"Exec : /tmp/foamRun")):
            with self.subTest(new=new),tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root);target=root/"logs/01-foamRun.log";target.write_bytes(target.read_bytes().replace(old,new));refresh(root)
                self.assertFalse(tool.reduce_phase(root)["actualCfdRunVerified"])

    def test_pressure_units_or_g_zero_reconstruction_or_missing_field_rejects(self):
        for option in ("dimension","reconstruction","missing"):
            with self.subTest(option=option),tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root);end=format(tool.preparation.DEFAULT_DURATION_S,".17g")
                target=root/f"case/{end}/p_rgh"
                if option=="dimension":target.write_bytes(target.read_bytes().replace(b"[1 -1 -2 0 0 0 0]",b"[0 2 -2 0 0 0 0]"))
                elif option=="missing":target.unlink()
                else:target.write_bytes(target.read_bytes().replace(b"200000",b"301325"));refresh(root,fields_changed=True)
                self.assertFalse(tool.reduce_phase(root)["actualCfdRunVerified"])

    def test_endpoint_actual_extrema_must_match_final_global_sampler(self):
        for role,old,new in (("globalPressureMinimum",b"200000",b"199999"),("globalWaterMinimum",b" 1\n",b" 0.999999\n")):
            with self.subTest(role=role),tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root);target=root/f"case/postProcessing/{role}/0/volFieldValue.dat"
                target.write_bytes(target.read_bytes().replace(old,new));refresh(root);result=tool.reduce_phase(root)
                self.assertTrue(result["actualCfdRunVerified"]);self.assertFalse(result["phaseH0Eligible"])
                self.assertIn("TEACHING_PHASE_ENDPOINT_GLOBAL_FIELD_TABLE_RECONCILIATION_REQUIRED",str(result["diagnostics"]))

    def test_extra_unknown_sampler_or_wrong_clock_does_not_qualify(self):
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root);write(root,"case/postProcessing/other/0/surfaceFieldValue.dat","# Time value\n0 1\n")
            self.assertFalse(tool.reduce_phase(root)["actualCfdRunVerified"])
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root);target=root/"logs/01-foamRun.log"
            target.write_bytes(target.read_bytes().replace(b"deltaT = ",b"unboundTimeStep = ",1));refresh(root)
            result=tool.reduce_phase(root);self.assertTrue(result["actualCfdRunVerified"]);self.assertFalse(result["phaseH0Eligible"])

    def test_source_relocation_preserves_historical_argv_and_current_proof(self):
        with tempfile.TemporaryDirectory() as temp,context():
            original=Path(temp)/"old";original.mkdir();fixture(original);moved=Path(temp)/"moved";shutil.copytree(original,moved)
            result=tool.reduce_phase(moved);self.assertTrue(result["phaseH0Eligible"],result["diagnostics"])

    def test_native_first_write_folder_can_be_actual_fractional_time(self):
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root)
            first=format(tool.preparation.MAX_DT_S,".17g")
            for name in tool.equations.sampler_roles():
                (root/"case/postProcessing"/name/"0").rename(root/"case/postProcessing"/name/first)
            refresh(root);result=tool.reduce_phase(root)
            self.assertTrue(result["phaseH0Eligible"],result["diagnostics"])
            name="globalPressureMinimum"
            (root/"case/postProcessing"/name/first).rename(root/"case/postProcessing"/name/"9999")
            refresh(root);self.assertFalse(tool.reduce_phase(root)["actualCfdRunVerified"])

    def test_raw_typed_resources_elapsed_workspace_and_unperformed_claims(self):
        changes=(lambda v:v.update(elapsedSeconds=1200),lambda v:v.update(workspaceBytes=v["workspaceBytes"]+1),
            lambda v:v["limits"].update(maximumCpus=True),lambda v:v["stages"][0].update(elapsedSeconds=1200),
            lambda v:v.update(phaseFamilyReferenceEligible=True))
        for change in changes:
            with tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root);raw=tool.record(root,tool.EXECUTION_FILE)[0];change(raw);json_write(root,tool.EXECUTION_FILE,raw)
                result=tool.reduce_phase(root);self.assertFalse(result["actualCfdRunVerified"]);self.assertFalse(result["phaseH0Eligible"])

    def test_ncc_diagnostics_complete_current_binding_and_postmutation(self):
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root)
            for patch_name in ("interface-impeller-outlet","interface-volute-inlet"):
                write(root,f"case/postProcessing/checkMesh/0/{patch_name}_coverage.vtk",b"SYNTHETIC-NOT-NATIVE-VTK")
            refresh(root);result=tool.reduce_phase(root);self.assertTrue(result["phaseH0Eligible"],result["diagnostics"])
            raw=tool.record(root,tool.EXECUTION_FILE)[0];raw["nativeNccDiagnosticRecords"]=[];json_write(root,tool.EXECUTION_FILE,raw)
            self.assertFalse(tool.reduce_phase(root)["actualCfdRunVerified"])
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root);original=tool.observation
            def changed(*args):
                original(*args);target=root/"case/postProcessing/globalPressureMinimum/0/volFieldValue.dat"
                target.write_bytes(target.read_bytes()+b"\n# SYNTHETIC MUTATION\n")
            with patch.object(tool,"observation",side_effect=changed):result=tool.reduce_phase(root)
            self.assertFalse(result["actualCfdRunVerified"]);self.assertFalse(result["phaseH0Eligible"])
            self.assertEqual(result["status"],"NativeEvidenceChangedDuringReduction");self.assertEqual(result["stages"],{})

    def test_verified_near_point_roles_are_not_folded_into_solver_tables(self):
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root)
            rows=[write(root,"case/postProcessing/checkMesh/constant/nearPoints.vtk",b"SYNTHETIC-NOT-NATIVE-VTK"),
                write(root,"case/constant/polyMesh/sets/nearPoints",b"SYNTHETIC-NOT-NATIVE-POINTSET")]
            proof={"files":rows,"evidence":{"fixtureKind":"MOCKED-GEOMETRY-ONLY-NOT-RESEARCH"}}
            # Actual data-only parser semantics are covered by the runner's
            # focused tests; this fixture verifies immutable consumer linkage.
            with patch.object(runner.baseline,"near_points_evidence",return_value=proof):
                raw=tool.record(root,tool.EXECUTION_FILE)[0]
                raw.update(nativeCheckMeshDiagnosticRecords=rows,nativeNearPointsEvidence=proof["evidence"])
                json_write(root,tool.EXECUTION_FILE,raw);refresh(root)
                result=tool.reduce_phase(root);self.assertTrue(result["phaseH0Eligible"],result["diagnostics"])
                self.assertTrue(all(row in result["nativeEvidenceRecords"] for row in rows))
                raw=tool.record(root,tool.EXECUTION_FILE)[0];raw["nativeNearPointsEvidence"]={}
                json_write(root,tool.EXECUTION_FILE,raw);refresh(root)
                result=tool.reduce_phase(root);self.assertFalse(result["actualCfdRunVerified"])
                self.assertIn("NEAR_POINTS_DIAGNOSTIC",str(result["diagnostics"]))

    def test_native_data_cache_is_bound_separately_from_authored_controls(self):
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root)
            row=write(root,"case/constant/fvMesh/polyFaces",b"SYNTHETIC-DATA-ROLE-NOT-NATIVE-LABEL-FIELD")
            proof=[{"fixtureKind":"MOCKED-GEOMETRY-ONLY-NOT-RESEARCH","authoredInput":False}]
            def native_cache(path,**kwargs):
                tool.require(kwargs.get("after_native_execution") is True,"No generated cache before native execution")
                return {"report":tool.record(path,tool.preparation.REPORT)[0],
                    "reportFile":tool.record(path,tool.preparation.REPORT)[1],
                    "nativeGeneratedDataRecords":[row],"nativeGeneratedDataEvidence":proof}
            # The preparer's own focused tests exercise the real strict label
            # parser. Only geometry mapping is mocked in this consumer fixture.
            with patch.object(tool.preparation,"verify_output",side_effect=native_cache):
                with self.assertRaises(tool.protocol.ProtocolError):tool.build_manifest(root)
                raw=tool.record(root,tool.EXECUTION_FILE)[0]
                raw.update(nativeGeneratedDataRecords=[row],nativeGeneratedDataEvidence=proof)
                json_write(root,tool.EXECUTION_FILE,raw);refresh(root)
                result=tool.reduce_phase(root);self.assertTrue(result["phaseH0Eligible"],result["diagnostics"])
                self.assertIn(row,result["nativeEvidenceRecords"])
                self.assertNotIn(row["path"],{r["path"] for r in result["manifest"]["files"]})
                raw=tool.record(root,tool.EXECUTION_FILE)[0];raw["nativeGeneratedDataRecords"]=[]
                json_write(root,tool.EXECUTION_FILE,raw);refresh(root)
                result=tool.reduce_phase(root);self.assertFalse(result["actualCfdRunVerified"])
                self.assertIn("GENERATED_DATA",str(result["diagnostics"]))


class CurrentBinaryIdentity(unittest.TestCase):
    def test_current_bytes_and_exact_typed_foundation_runtime(self):
        raw=b"SYNTHETIC-NOT-NATIVE-BINARY"
        identity={"schemaVersion":tool.RUNTIME_SCHEMA,"openFoamFoundationVersion":12,"packageVersion":"20250206",
            "officialDebSha256":tool.preparation.safety.PACKAGE_SHA,"expectedSourceCommit":tool.preparation.safety.SOURCE_COMMIT,
            "mainApplicationWrites":False,"binaries":[{"program":p,"path":"/opt/openfoam12/bin/"+p,"bytes":len(raw),
                "sha256":sha256(raw).hexdigest(),"packageOwnership":"openfoam12: /opt/openfoam12/bin/"+p} for p in tool.PROGRAMS]}
        with patch.object(tool.native.case_tool,"read_regular",return_value=raw) as read:tool.binary_identity(identity);self.assertEqual(read.call_count,2)
        with patch.object(tool.native.case_tool,"read_regular",return_value=raw+b"changed"),self.assertRaises(tool.protocol.ProtocolError):tool.binary_identity(identity)
        identity["openFoamFoundationVersion"]="12"
        with patch.object(tool.native.case_tool,"read_regular",side_effect=AssertionError("Do not read wrong native provenance")),self.assertRaises(tool.protocol.ProtocolError):tool.binary_identity(identity)


if __name__ == "__main__":unittest.main()
