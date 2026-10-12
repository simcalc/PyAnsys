"""Synthetic orchestration and current-byte tests, never native CFD evidence."""
from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import run_teaching_pump_extended_outlet as runner
import teaching_pump_baseline_case as transaction


class InletFlowBindingsTests(unittest.TestCase):
    def test_new_four_stage_profile_preserves_native_programs_and_actual_static_outlet_recipe(self):
        import teaching_pump_extended_outlet_case as preparer
        self.assertEqual(preparer.commands(Path("fixture")),runner.commands(Path("fixture")))
        self.assertEqual([a[0] for a in runner.commands("fixture")],list(runner.PROGRAMS))
        self.assertEqual(runner.commands("fixture")[2][3:],["-allGeometry","-allTopology","-writeSets"])
        self.assertEqual(runner.LIMITS,runner.low.LIMITS)
        self.assertEqual(runner.LIMITS["maximumSeconds"],1200);self.assertEqual(runner.LIMITS["maximumWorkspaceBytes"],1024**3)
        fields=runner.recipe.case_files("impeller")
        _,u=transaction.fields.boundary_blocks(fields["0/U"],"U")
        _,p=transaction.fields.boundary_blocks(fields["0/p"],"p")
        self.assertEqual(u["inlet"]["type"],"flowRateInletVelocity");self.assertEqual(p["inlet"]["type"],"fixedFluxPressure")
        self.assertEqual(u["mass-flow-inlet-11"]["type"],"pressureInletOutletVelocity")
        self.assertEqual(p["mass-flow-inlet-11"],{"type":"fixedValue","value":"uniform 0"})
        self.assertIsNot(runner.final_fields,runner.low.final_fields)

    def test_endpoint_reader_requires_own_2000_directory_without_reusing_old500(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'impeller-case/500').mkdir(parents=True)
            with self.assertRaises(ValueError):runner.final_fields(root,2)

    def test_old_runtime_namespace_rejects_before_any_owned_native_process(self):
        with patch.object(runner.low,"execute") as native:
            for schema in (runner.low.RUNTIME_SCHEMA,"unknown"):
                with self.assertRaises(ValueError):runner.execute(runner.commands("fixture")[0],"fixture",100,
                    {"schemaVersion":schema,"openFoamFoundationVersion":12},0)
            native.assert_not_called()

    def test_new_manifest_identity_and_study_use_exact_canonical_bytes_and_names(self):
        reducer=SimpleNamespace(build_manifest=lambda _: {"geometryIdentity":{"geometry":"synthetic"},"manifest":{"files":[]},"study":{"synthetic":True}},
            encoded=lambda x:json.dumps(x,sort_keys=True,separators=(",",":")).encode())
        with tempfile.TemporaryDirectory() as temp,patch.dict(sys.modules,{"reduce_teaching_pump_extended_outlet":reducer}):
            root=Path(temp);rows,files=runner.bind_manifest(root)
            self.assertEqual(set(rows),{runner.IDENTITY,runner.MANIFEST,runner.STUDY});self.assertEqual(files,[])
            self.assertEqual(runner.IDENTITY,"teaching-extended-outlet-geometry-identity.json")
            for name,row in rows.items():self.assertEqual(row,runner.record(root,name)[1])
            self.assertFalse((root/runner.low.MANIFEST).exists())

    def test_runtime_binds_actual_four_current_package_bytes_in_new_namespace(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for program in runner.PROGRAMS:(root/program).write_bytes(("SYNTHETIC-NON-NATIVE-"+program).encode())
            replies=[SimpleNamespace(stdout="20250206",stderr=""),*[SimpleNamespace(stdout="openfoam12: "+str((root/p).resolve())+"\n",stderr="") for p in runner.PROGRAMS]]
            with patch.object(runner.shutil,"which",side_effect=lambda p:str(root/p)),patch.object(Path,"is_relative_to",return_value=True),patch.object(runner.subprocess,"run",side_effect=replies):
                identity=runner.runtime_identity(root,runner.time.monotonic()+100)
            self.assertEqual(identity["schemaVersion"],runner.RUNTIME_SCHEMA);self.assertEqual(identity["openFoamFoundationVersion"],12)
            self.assertEqual([r["program"] for r in identity["binaries"]],list(runner.PROGRAMS))
            for row in identity["binaries"]:self.assertEqual(row["sha256"],sha256((root/row["program"]).read_bytes()).hexdigest())
            self.assertEqual(json.loads((root/runner.RUNTIME).read_bytes()),identity)
            self.assertFalse((root/runner.low.RUNTIME).exists())

    def test_wrong_version_or_ownership_never_persists_native_runtime(self):
        for version,owner in (("other","openfoam12"),("20250206","other")):
            with self.subTest(version=version,owner=owner),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);binary=root/"mergeMeshes";binary.write_bytes(b"SYNTHETIC")
                replies=[SimpleNamespace(stdout=version,stderr=""),SimpleNamespace(stdout=owner+": "+str(binary.resolve()),stderr="")]
                with patch.object(runner.shutil,"which",return_value=str(binary)),patch.object(Path,"is_relative_to",return_value=True),patch.object(runner.subprocess,"run",side_effect=replies):
                    with self.assertRaises(ValueError):runner.runtime_identity(root,runner.time.monotonic()+100)
                self.assertFalse((root/runner.RUNTIME).exists())


class SyntheticInletFlowCampaignTests(unittest.TestCase):
    def campaign(self,root,*,failed_stage=None,bad_mesh=False,bad_log=False,missing_fields=False,mutate=None,
                 expire_at=None,old_family=False,bad_mapping=False,post_ncc_failure=False,generated_failure=False,bad_stage_metadata=False):
        """Native process/quality/science readers are explicitly mocked only here."""
        source=root/"source";source.mkdir();(source/"source-proof").write_bytes(b"SYNTHETIC-INPUT")
        output=root/"execution";clock={"now":100.};components=[{"component":name,"cellCount":2,"nodeCount":5,"cellZone":zone}
            for name,zone in (("impeller","rotating-fluid"),("volute","stationary-fluid"))]
        def prepare(*_):
            output.mkdir();authored=[];copies=[]
            for name in ("impeller-case/system/controlDict","impeller-case/0/p","source-teaching/source-proof"):
                path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"SYNTHETIC-INPUT")
                (copies if name.startswith("source-teaching/") else authored).append(runner.record(output,name)[1])
            mapping={"referenceLocation":"inlet" if bad_mapping else "outlet","referenceQuantity":"static absolute pressure","referencePa":1000000.,"referenceAdditionCount":1}
            packet={"schemaVersion":runner.PREPARATION_SCHEMA,"researchProfile":runner.recipe.PROFILE,"boundaryFamilyId":"old-family" if old_family else runner.recipe.BC_FAMILY,
                "nativeCommandArguments":runner.commands(output),"absolutePressureMapping":mapping,"components":components,
                "authoredInputFiles":authored,"sourceComponentFiles":copies,"sourceEvidenceFileRecords":copies,"authoredPhysicsFileRecords":[],
                **dict.fromkeys(runner.SOURCE_KEYS,"0"*64)}
            (output/"teaching-extended-outlet-preparation.json").write_text(json.dumps(packet));return packet
        def verify(*_,**kwargs):
            if post_ncc_failure and kwargs.get("after_native_ncc"):raise ValueError("Synthetic post-NCC typed new-BC mismatch")
            if expire_at=="final-source" and kwargs.get("after_native_ncc") and (output/"impeller-case/2000").exists():clock["now"]=1301.
            return {"report":json.loads((output/"teaching-extended-outlet-preparation.json").read_bytes()),"reportFile":runner.record(output,"teaching-extended-outlet-preparation.json")[1],"components":components}
        def data(*_,after_native_execution=False):
            if not after_native_execution:return [],[]
            path=output/"impeller-case/0/cellToRegion"
            if not path.exists():return [],[]
            if generated_failure and (output/"impeller-case/2000").exists():raise ValueError("Synthetic malformed native generated role")
            return [runner.record(output,"impeller-case/0/cellToRegion")[1]],[{"path":"impeller-case/0/cellToRegion","syntheticFixture":True}]
        preparer=SimpleNamespace(REPORT="teaching-extended-outlet-preparation.json",prepare=prepare,verify_output=verify,generated_data=data,
            validate_source=lambda _: {"sourceFileRecords":[runner.record(source,"source-proof")[1]]},SourceProofTransaction=transaction.SourceProofTransaction)
        def runtime(*_):
            rows=[]
            for program in runner.PROGRAMS:
                path=root/program;path.write_bytes(("SYNTHETIC-NON-NATIVE-"+program).encode())
                rows.append({"program":program,"path":str(path),"bytes":path.stat().st_size,"sha256":sha256(path.read_bytes()).hexdigest()})
            packet={"schemaVersion":runner.RUNTIME_SCHEMA,"openFoamFoundationVersion":12,"binaries":rows}
            (output/runner.RUNTIME).write_text(json.dumps(packet));return packet
        def manifest(*_):
            rows={}
            for name in (runner.IDENTITY,runner.MANIFEST,runner.STUDY):
                (output/name).write_bytes(b'{"syntheticFixture":true}');rows[name]=runner.record(output,name)[1]
            if expire_at=="manifest":clock["now"]=1301.
            return rows,[{**runner.record(output,"impeller-case/0/p")[1],"control":"boundaryPhysics"}]
        def execute(arguments,*args):
            index=args[-1];text="Build : 12-86e126a7bc4d\n"+("FOAM FATAL ERROR\n" if bad_log and index==1 else "")+"End\n"
            path=output/"logs"/f"{index:02d}-{arguments[0]}.log";path.write_text(text);row=runner.record(output,"logs/"+path.name)[1]
            if index==1:(output/"impeller-case/0/p").write_bytes(b"SYNTHETIC-NATIVE-NCC-WRITER")
            if index==2:(output/"impeller-case/0/cellToRegion").write_bytes(b"SYNTHETIC-NATIVE-DIAGNOSTIC")
            if index==3 and mutate:
                changed_path=source/"source-proof" if mutate=="original-source" else root/"foamRun" if mutate=="native-binary" else output/mutate
                changed_path.write_bytes(b"SYNTHETIC-MUTATION")
            return {"program":arguments[0],"exitCode":7 if index==failed_stage else 0,"elapsedSeconds":.1,"log":"00-mergeMeshes.log" if bad_stage_metadata and index==1 else path.name,
                "logBytes":row["bytes"],"logSha256":row["sha256"]},text
        def fields(*_):
            if missing_fields:raise ValueError("Synthetic missing actual2000 fields")
            path=output/"impeller-case/2000/p";path.parent.mkdir();path.write_bytes(b"SYNTHETIC-FIELD")
            row=runner.record(output,"impeller-case/2000/p")[1]
            if expire_at=="endpoint":clock["now"]=1301.
            return [{"name":"p","bytes":row["bytes"],"sha256":row["sha256"]}],[row],[{"syntheticFixture":True}]
        def samples(*_):
            path=output/"impeller-case/postProcessing/fixture/0/volFieldValue.dat";path.parent.mkdir(parents=True);path.write_bytes(b"# SYNTHETIC\n2000 1\n")
            return [runner.record(output,path.relative_to(output).as_posix())[1]]
        with patch.dict(os.environ,{"RUNNER_TEMP":str(root)}),patch.dict(sys.modules,{"teaching_pump_extended_outlet_case":preparer}),\
             patch.object(runner.safety,"context_guard"),patch.object(runner,"runtime_identity",side_effect=runtime),\
             patch.object(runner,"bind_manifest",side_effect=manifest),patch.object(runner,"execute",side_effect=execute),\
             patch.object(runner.geometry,"verify_region_count",return_value=2),\
             patch.object(runner.base,"verify_merged_mesh",side_effect=ValueError("Synthetic native mesh failed") if bad_mesh else None,return_value={"mergedCellCount":4,"mergedPointCount":10}),\
             patch.object(runner.scientific,"parse_mesh_quality",return_value={"syntheticFixture":True}),\
             patch.object(runner.scientific,"parse_solver_log",return_value={2000:{"syntheticFixture":True}}),\
             patch.object(runner,"final_fields",side_effect=fields),patch.object(runner,"sampling_records",side_effect=samples),\
             patch.object(runner,"ncc_diagnostic_records",return_value=[]),patch.object(runner,"near_points_evidence",return_value={"files":[],"evidence":None}),\
             patch.object(runner.time,"monotonic",side_effect=lambda:clock["now"]),redirect_stdout(io.StringIO()):
            code=runner.run(source,output)
        return code,json.loads((output/runner.REPORT).read_bytes()),output

    def test_mock_completion_has_new_namespace_static_pressure_identity_and_false_science_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            code,report,output=self.campaign(Path(temp))
            self.assertEqual(code,0);self.assertEqual(report["status"],runner.SUCCESS);self.assertEqual(len(report["stages"]),4)
            self.assertTrue(report["simulationExecuted"]);self.assertTrue(report["actualCfdRunVerified"])
            self.assertEqual(report["boundaryFamilyId"],runner.recipe.BC_FAMILY)
            self.assertEqual(report["outletStaticAbsoluteReferencePa"],1000000.);self.assertFalse(report["inletTotalPressurePrescribed"])
            self.assertEqual(report["absolutePressureMapping"]["referenceLocation"],"outlet")
            self.assertEqual(report["inputManifestSha256"],runner.record(output,runner.MANIFEST)[1]["sha256"])
            self.assertEqual(len(report["nativeGeneratedDataRecords"]),1)
            self.assertFalse((output/"teaching-extended-outlet-acceptance.json").exists())
            for key in ("numericalBaselineQualified","qualifiedBaseline","meshConvergenceVerified","timeConvergenceVerified","npsh3Qualified","physicalTransientExecuted","restartVerified","mainApplicationWrites"):
                self.assertIs(report[key],False)

    def test_failed_native_stage_and_fatal_exit_zero_preserve_diagnostic_and_stop_solver(self):
        for kwargs in ({"failed_stage":1},{"bad_log":True},{"bad_mesh":True},{"post_ncc_failure":True},{"bad_stage_metadata":True}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as temp:
                code,report,_=self.campaign(Path(temp),**kwargs)
                self.assertEqual(code,2);self.assertFalse(report["nativeSolverCompleted"]);self.assertFalse(report["actualCfdRunVerified"])
                self.assertLess(len(report["stages"]),4);self.assertTrue(report["diagnostic"]["message"])

    def test_missing_fields_or_malformed_generated_role_preserves_only_native_completion(self):
        for kwargs in ({"missing_fields":True},{"generated_failure":True}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as temp:
                code,report,_=self.campaign(Path(temp),**kwargs)
                self.assertEqual(code,2);self.assertTrue(report["nativeSolverCompleted"]);self.assertFalse(report["actualCfdRunVerified"])
                self.assertFalse(report["numericalBaselineQualified"])

    def test_new_source_inputs_runtime_manifest_geometry_diagnostics_and_binary_cannot_mutate(self):
        for relative in ("original-source","source-teaching/source-proof","impeller-case/system/controlDict","impeller-case/0/p",
            "impeller-case/0/cellToRegion",runner.RUNTIME,runner.IDENTITY,runner.MANIFEST,runner.STUDY,"logs/00-mergeMeshes.log","native-binary"):
            with self.subTest(relative=relative),tempfile.TemporaryDirectory() as temp:
                code,report,_=self.campaign(Path(temp),mutate=relative)
                self.assertEqual(code,2);self.assertFalse(report["inputArtifactsImmutable"]);self.assertFalse(report["actualCfdRunVerified"])

    def test_deadline_before_solver_or_after_actual_fields_stops_qualification(self):
        for place in ("manifest","endpoint","final-source"):
            with self.subTest(place=place),tempfile.TemporaryDirectory() as temp:
                code,report,_=self.campaign(Path(temp),expire_at=place)
                self.assertEqual(code,2);self.assertFalse(report["actualCfdRunVerified"]);self.assertFalse(report["inputArtifactsImmutable"])
                self.assertGreaterEqual(report["elapsedSeconds"],1200);self.assertFalse(report["numericalBaselineQualified"])

    def test_old_boundary_family_or_inlet_pressure_identity_never_enters_native_pipeline(self):
        for kwargs in ({"old_family":True},{"bad_mapping":True}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as temp,self.assertRaises(ValueError):
                self.campaign(Path(temp),**kwargs)


if __name__=="__main__":unittest.main()
