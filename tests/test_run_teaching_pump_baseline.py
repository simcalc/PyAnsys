"""Mocked native orchestration contracts; these fixtures are never CFD evidence."""
from contextlib import ExitStack,redirect_stdout
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
import run_teaching_pump_baseline as runner


def near_points_fixture(root,*,case="impeller-case",log="logs/02-checkMesh.log",source="source-teaching/source-geometry/teaching-geometry.json"):
    """Six synthetic duplicate NCC point labels; never actual pump/native proof."""
    def native(name,kind,body):return ("FoamFile {version 2.0; format ascii; class "+kind+"; object "+name+";}\n"+body+"\n").encode()
    mesh=root/case/"constant/polyMesh";(mesh/"sets").mkdir(parents=True,exist_ok=True)
    (mesh/"points").write_bytes(native("points","vectorField","6\n((1 0 0)(1 1 0)(1 0 1)(1 0 0)(1 1 0)(1 0 1))"))
    (mesh/"faces").write_bytes(native("faces","faceList","2\n(3(0 1 2)3(3 4 5))"))
    (mesh/"boundary").write_bytes(native("boundary","polyBoundaryMesh","2\n(interface-impeller-outlet {type patch; nFaces 1; startFace 0;}\ninterface-volute-inlet {type patch; nFaces 1; startFace 1;})"))
    (mesh/"sets/nearPoints").write_bytes(native("nearPoints","pointSet","6\n(0 1 2 3 4 5)"))
    log_path=root/log;log_path.parent.mkdir(parents=True,exist_ok=True)
    log_path.write_bytes(b"Build : 12-86e126a7bc4d\n <<Writing 6 near (closer than 1e-6 apart) points to set nearPoints\nMesh OK.\nEnd\n")
    source_path=root/source;source_path.parent.mkdir(parents=True,exist_ok=True)
    source_path.write_text(json.dumps({"schemaVersion":"untirta.teaching-pump-geometry.v1","provenance":"CLASSROOM_AUTHORED_GEOMETRY","units":"SI","commonInterfaceEvidence":{"sharedNodeCount":3}}))
    vtk=root/case/"postProcessing/checkMesh/constant/nearPoints.vtk";vtk.parent.mkdir(parents=True,exist_ok=True)
    vtk.write_bytes(b"# vtk DataFile Version 2.0\nnearPoints\nASCII\nDATASET POLYDATA\nPOINTS 6 float\n1 0 0 1 1 0 1 0 1 1 0 0 1 1 0 1 0 1\nVERTICES 6 12\n1 0 1 1 1 2 1 3 1 4 1 5\nPOINT_DATA 6\nFIELD attributes 1\npointID 1 6 float\n0 1 2 3 4 5\n")
    return vtk


class Process:
    pid=123456
    class Stream:
        def fileno(self):return 123
        def close(self):pass
    stdout=Stream()
    def __init__(self,code=0):self.code=code
    def poll(self):return self.code
    def wait(self,timeout):return self.code


class Selector:
    def __init__(self):self.active=True
    def register(self,stream,event):self.stream=stream
    def get_map(self):return {1:True} if self.active else {}
    def select(self,timeout):return [(SimpleNamespace(fileobj=self.stream),None)]
    def unregister(self,stream):self.active=False
    def close(self):pass


class TeachingProcessTests(unittest.TestCase):
    def fixture(self,root,*,code=0,times=None):
        output=root/"execution";output.mkdir();(output/"logs").mkdir();(output/"impeller-case").mkdir()
        binary=root/"mergeMeshes";binary.write_bytes(b"SYNTHETIC-NON-NATIVE-BINARY")
        runtime={"binaries":[{"program":"mergeMeshes","path":str(binary),"packageOwnership":"openfoam12: "+str(binary),
            "bytes":binary.stat().st_size,"sha256":sha256(binary.read_bytes()).hexdigest()}]}
        chunks=iter([b"Build : 12-86e126a7bc4d\nEnd\n",b""])
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path,"is_relative_to",return_value=True))
            stack.enter_context(patch.object(runner.selectors,"DefaultSelector",Selector))
            popen=stack.enter_context(patch.object(runner.subprocess,"Popen",return_value=Process(code)))
            stack.enter_context(patch.object(runner.os,"read",side_effect=lambda *_:next(chunks)))
            stop=stack.enter_context(patch.object(runner.native,"stop_owned"))
            if times is not None:stack.enter_context(patch.object(runner.time,"monotonic",side_effect=times))
            result=runner.execute(runner.commands(output)[0],output,1000 if times is not None else runner.time.monotonic()+100,runtime,0)
        return result,popen,stop,output

    def test_only_four_current_commands_no_meshing_or_old_source_reexecution(self):
        rows=runner.commands(Path("fixture"))
        self.assertEqual([row[0] for row in rows],list(runner.PROGRAMS))
        self.assertEqual(rows[0][3],"-addCases")
        self.assertEqual(rows[2][3:],["-allGeometry","-allTopology","-writeSets"])
        self.assertNotIn("gmshToFoam",repr(rows));self.assertNotIn("snappyHexMesh",repr(rows))

    def test_real_authored_case_contract_uses_single_phase_units_and_stationary_intake(self):
        import teaching_pump_baseline_case as preparation
        self.assertEqual(preparation.commands(Path("fixture")),runner.commands(Path("fixture")))
        inputs=preparation.case_files("impeller")
        self.assertIn(b"pRef 300000;",inputs["system/controlDict"])
        self.assertIn(b"fields true;",inputs["system/createNonConformalCouplesDict"])
        self.assertNotIn(b"nonRotatingPatches",inputs["constant/MRFProperties"])
        self.assertIn(b"wallDist {method meshWave;}",inputs["system/fvSchemes"])
        self.assertIn(b'"inblock-hub"\n    {\n        type noSlip;',inputs["0/U"])
        self.assertIn(b'"inblock-shroud"\n    {\n        type noSlip;',inputs["0/U"])
        self.assertIn(b"omega 1450 [rpm];",inputs["constant/MRFProperties"])
        self.assertIn(b"dimensions [0 2 -2 0 0 0 0];",inputs["0/p"])
        self.assertNotIn("0/alpha.water",inputs)

    def test_bound_binary_owned_process_cwd_and_secret_free_environment(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"GH_TOKEN":"SYNTHETIC-FORBIDDEN"}):
            (stage,text),popen,stop,output=self.fixture(Path(temp))
        args,kwargs=popen.call_args
        self.assertEqual(args[0][1:],runner.commands(output)[0][1:])
        self.assertEqual(kwargs["cwd"],str(output/"impeller-case"))
        self.assertTrue(kwargs["start_new_session"]);self.assertIs(kwargs["preexec_fn"],runner.child_limits)
        self.assertNotIn("GH_TOKEN",kwargs["env"]);stop.assert_called_once()
        self.assertEqual(stage["logSha256"],sha256(text.encode()).hexdigest())

    def test_nonzero_exit_is_retained_and_not_promoted(self):
        with tempfile.TemporaryDirectory() as temp:(stage,_),_,_,_=self.fixture(Path(temp),code=7)
        self.assertEqual(stage["exitCode"],7)

    def test_log_limit_and_postcompletion_deadline_stop_owned_child(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(runner.LIMITS,{"maximumLogBytes":5}),self.assertRaises(ValueError):self.fixture(Path(temp))
        with tempfile.TemporaryDirectory() as temp,self.assertRaisesRegex(ValueError,"process completion"):self.fixture(Path(temp),times=[0,0,0,0,1001])

    def test_mismatched_command_index_and_binary_sha_reject_before_start(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(runner.subprocess,"Popen") as popen:
            root=Path(temp);(root/"logs").mkdir();binary=root/"mergeMeshes";binary.write_bytes(b"SYNTHETIC-CHANGED")
            for args,index in ((["snappyHexMesh","-case",str(root)],0),(runner.commands(root)[0],1),(runner.commands(root)[0],True)):
                with self.assertRaises(ValueError):runner.execute(args,root,1000,{},index)
            runtime={"binaries":[{"program":"mergeMeshes","path":str(binary),"packageOwnership":"openfoam12: "+str(binary),"bytes":1,"sha256":"0"*64}]}
            with patch.object(Path,"is_relative_to",return_value=True),self.assertRaisesRegex(ValueError,"changed after binding"):
                runner.execute(runner.commands(root)[0],root,1000,runtime,0)
            popen.assert_not_called()


class TeachingBindingTests(unittest.TestCase):
    def test_canonical_new_identity_manifest_study_written_before_cfd(self):
        encoded=lambda x:(json.dumps(x,sort_keys=True,separators=(",",":"))+"\n").encode()
        bound={"geometryIdentity":{"b":2,"a":1},"manifest":{"files":[],"syntheticFixture":True},"study":{"syntheticFixture":True}}
        reducer=SimpleNamespace(build_manifest=lambda _:bound,encoded=encoded)
        with tempfile.TemporaryDirectory() as temp,patch.dict(sys.modules,{"reduce_teaching_pump_baseline":reducer}):
            root=Path(temp);rows,files=runner.bind_manifest(root)
            for name,key in ((runner.IDENTITY,"geometryIdentity"),(runner.MANIFEST,"manifest"),(runner.STUDY,"study")):
                self.assertEqual((root/name).read_bytes(),encoded(bound[key]))
                self.assertEqual(rows[name]["sha256"],sha256(encoded(bound[key])).hexdigest())
            self.assertEqual(files,[])

    def test_actual_four_binaries_owned_by_exact_official_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in runner.PROGRAMS:(root/name).write_bytes(("SYNTHETIC-"+name).encode())
            replies=[SimpleNamespace(stdout="20250206",stderr=""),*[SimpleNamespace(stdout="openfoam12: "+str((root/name).resolve())+"\n",stderr="") for name in runner.PROGRAMS]]
            with patch.object(runner.shutil,"which",side_effect=lambda name:str(root/name)),patch.object(Path,"is_relative_to",return_value=True),patch.object(runner.subprocess,"run",side_effect=replies):
                identity=runner.runtime_identity(root,runner.time.monotonic()+100)
            self.assertEqual(identity["schemaVersion"],runner.RUNTIME_SCHEMA)
            self.assertEqual(identity["openFoamFoundationVersion"],12)
            self.assertEqual([r["program"] for r in identity["binaries"]],list(runner.PROGRAMS))
            self.assertEqual(json.loads((root/runner.RUNTIME).read_bytes()),identity)

    def test_wrong_package_path_or_ownership_does_not_persist_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);binary=root/"mergeMeshes";binary.write_bytes(b"SYNTHETIC")
            for version,inside,owner in (("other",True,"openfoam12"),("20250206",False,"openfoam12"),("20250206",True,"other")):
                replies=[SimpleNamespace(stdout=version,stderr=""),SimpleNamespace(stdout=owner+": "+str(binary.resolve()),stderr="")]
                with self.subTest(version=version,inside=inside,owner=owner),patch.object(runner.shutil,"which",return_value=str(binary)),patch.object(Path,"is_relative_to",return_value=inside),patch.object(runner.subprocess,"run",side_effect=replies),self.assertRaises(ValueError):
                    runner.runtime_identity(root,runner.time.monotonic()+100)
                self.assertFalse((root/runner.RUNTIME).exists())

    def test_workspace_and_sampling_bounds_do_not_accept_links_or_arbitrary_roles(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/"file").write_bytes(b"1234")
            with patch.dict(runner.LIMITS,{"maximumWorkspaceBytes":3}),self.assertRaises(ValueError):runner.workspace_size(root)
            folder=root/"impeller-case/postProcessing/fixture/0";folder.mkdir(parents=True);(folder/"volFieldValue.dat").write_bytes(b"# SYNTHETIC\n0 1\n")
            rows=runner.sampling_records(root);self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["sha256"],sha256((folder/"volFieldValue.dat").read_bytes()).hexdigest())
            (folder/"script.py").write_bytes(b"never-execute")
            with self.assertRaisesRegex(ValueError,"sampling tables"):runner.sampling_records(root)
            with self.assertRaises(ValueError):runner.record(root,"../outside")

    def test_actual_ncc_coverage_vtk_paths_are_separate_from_solver_tables(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);tables=root/"impeller-case/postProcessing/flowInlet/0";tables.mkdir(parents=True)
            (tables/"surfaceFieldValue.dat").write_bytes(b"# SYNTHETIC-TABLE-NOT-CFD\n500 1\n")
            folder=root/"impeller-case/postProcessing/checkMesh/0";folder.mkdir(parents=True)
            for name in ("interface-impeller-outlet","interface-volute-inlet"):
                (folder/(name+"_coverage.vtk")).write_bytes(b"# vtk DataFile Version 2.0\nSYNTHETIC-CONTRACT\nASCII\n")
            samples=runner.sampling_records(root);diagnostics=runner.ncc_diagnostic_records(root)
            self.assertEqual(len(samples),1);self.assertEqual(len(diagnostics),2)
            self.assertTrue(all(row["path"].endswith("_coverage.vtk") for row in diagnostics))
            changed=folder/"interface-volute-inlet_coverage.vtk";changed.write_bytes(changed.read_bytes()+b"SYNTHETIC-TAMPER\n")
            self.assertNotEqual(runner.ncc_diagnostic_records(root),diagnostics)
            (folder/"unrecognized_coverage.vtk").write_bytes(b"never-accept")
            with self.assertRaisesRegex(ValueError,"exact initial NCC"):runner.sampling_records(root)

    def test_near_points_native_diagnostic_matches_exact_duplicate_ncc_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);near_points_fixture(root)
            result=runner.near_points_evidence(root)
            self.assertEqual(len(result["files"]),2)
            self.assertEqual(result["evidence"]["nativeNearPointCount"],6)
            self.assertEqual(result["evidence"]["sharedInterfaceNodeCountPerSide"],3)
            self.assertTrue(result["evidence"]["nativePointSetEqualsDuplicatedInterfaceLabels"])
            self.assertFalse(result["evidence"]["vtkCoordinatePrecisionDefinesGeometry"])
            self.assertEqual(runner.postprocessing_records(root),([],[]))

    def test_bad_near_point_counts_ids_source_or_nonfinite_vtk_are_rejected(self):
        for change in ("log-count","source-count","vtk-count","duplicate-set","shared-interface","nonfinite-vtk","vtk-ids"):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);vtk=near_points_fixture(root);mesh=root/"impeller-case/constant/polyMesh"
                if change=="log-count":path=root/"logs/02-checkMesh.log";path.write_bytes(path.read_bytes().replace(b"Writing 6 near",b"Writing 5 near"))
                elif change=="source-count":path=root/"source-teaching/source-geometry/teaching-geometry.json";path.write_bytes(path.read_bytes().replace(b'"sharedNodeCount": 3',b'"sharedNodeCount": 4'))
                elif change=="vtk-count":vtk.write_bytes(vtk.read_bytes().replace(b"POINTS 6 float",b"POINTS 5 float"))
                elif change=="duplicate-set":path=mesh/"sets/nearPoints";path.write_bytes(path.read_bytes().replace(b"0 1 2 3 4 5",b"0 1 2 3 4 4"))
                elif change=="shared-interface":path=mesh/"faces";path.write_bytes(path.read_bytes().replace(b"3(3 4 5)",b"3(0 4 5)"))
                elif change=="nonfinite-vtk":vtk.write_bytes(vtk.read_bytes().replace(b"1 0 0 1 1",b"nan 0 0 1 1"))
                else:vtk.write_bytes(vtk.read_bytes().replace(b"\n0 1 2 3 4 5\n",b"\n0 1 2 3 4 4\n"))
                with self.assertRaises(ValueError):runner.near_points_evidence(root)

    def test_reported_near_point_diagnostic_cannot_be_omitted_partially(self):
        for remove in ("vtk","set","log","both"):
            with self.subTest(remove=remove),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);vtk=near_points_fixture(root)
                if remove in ("vtk","both"):vtk.unlink()
                if remove in ("set","both"):(root/"impeller-case/constant/polyMesh/sets/nearPoints").unlink()
                if remove=="log":(root/"logs/02-checkMesh.log").unlink()
                with self.assertRaises(ValueError):runner.near_points_evidence(root)


class SyntheticTeachingCampaignTests(unittest.TestCase):
    def campaign(self,root,*,failed_stage=None,missing_fields=False,mutate=None,bad_mesh=False,bad_log=False):
        """Every native/scientific reader is mocked; no CAD/mesh/flow executes."""
        source=root/"source";source.mkdir();output=root/"execution";components=[
            {"component":name,"cellCount":2,"nodeCount":5,"cellZone":zone,"patchFaces":{name+"-fixture":1}}
            for name,zone in zip(("impeller","volute"),("rotating-fluid","stationary-fluid"))]
        (source/"source-proof").write_bytes(b"SYNTHETIC-INPUT")
        def prepare(*_):
            output.mkdir();authored=[];copies=[]
            for name in ("impeller-case/system/controlDict","impeller-case/0/p","source-teaching/source-proof"):
                path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"SYNTHETIC-INPUT")
                (copies if name.startswith("source-teaching/") else authored).append(runner.record(output,name)[1])
            report={"schemaVersion":runner.PREPARATION_SCHEMA,"nativeCommandArguments":runner.commands(output),
                "authoredInputFiles":authored,"sourceComponentFiles":copies,"sourceEvidenceFileRecords":copies,"components":components,"authoredPhysicsFileRecords":[],
                **{key:"0"*64 for key in ("sourceGeometryExecutionReportSha256","sourceGeometryPreparationReportSha256","sourceGeometryInputManifestSha256","sourceAuthoredGeometryReportSha256","sourceGeometryIdentitySha256")}}
            (output/"teaching-baseline-preparation.json").write_text(json.dumps(report));return report
        def verify(*_,**kwargs):return {"report":json.loads((output/"teaching-baseline-preparation.json").read_bytes()),"reportFile":runner.record(output,"teaching-baseline-preparation.json")[1],"components":components}
        preparer=SimpleNamespace(prepare=prepare,verify_output=verify,validate_source=lambda _: {"sourceFileRecords":[runner.record(source,"source-proof")[1]]})
        def runtime(*_):
            (output/runner.RUNTIME).write_text('{"syntheticFixture":true}');return {"syntheticFixture":True}
        def manifest(*_):
            rows={}
            for name in (runner.IDENTITY,runner.MANIFEST,runner.STUDY):
                (output/name).write_text('{"syntheticFixture":true}');rows[name]=runner.record(output,name)[1]
            return rows,[{**runner.record(output,"impeller-case/0/p")[1],"control":"boundaryPhysics"}]
        def execute(arguments,workspace,deadline,identity,index):
            text="Build : 12-86e126a7bc4d\n"+("FOAM FATAL ERROR\n" if bad_log and index==1 else "")+"End\n"
            path=output/"logs"/(f"{index:02d}-{arguments[0]}.log");path.write_text(text);row=runner.record(output,"logs/"+path.name)[1]
            if index==1:(output/"impeller-case/0/p").write_bytes(b"SYNTHETIC-NATIVE-NCC-REWRITTEN")
            if index==2:
                folder=output/"impeller-case/postProcessing/checkMesh/0";folder.mkdir(parents=True)
                for name in ("interface-impeller-outlet","interface-volute-inlet"):
                    (folder/(name+"_coverage.vtk")).write_bytes(b"SYNTHETIC-NCC-DIAGNOSTIC-NOT-CFD")
            if mutate and index==3:
                if mutate=="original-source":(source/"source-proof").write_bytes(b"SYNTHETIC-MUTATION")
                else:(output/mutate).write_bytes(b"SYNTHETIC-MUTATION")
            return {"program":arguments[0],"exitCode":7 if index==failed_stage else 0,"elapsedSeconds":.1,"log":path.name,"logBytes":row["bytes"],"logSha256":row["sha256"]},text
        def fields(*_):
            if missing_fields:raise ValueError("Actual endpoint fields missing")
            folder=output/"impeller-case/500";folder.mkdir();(folder/"p").write_bytes(b"SYNTHETIC-FIELD")
            row=runner.record(output,"impeller-case/500/p")[1]
            return [{"name":"p",**{k:row[k] for k in ("bytes","sha256")}}],[row],[{"syntheticFixture":True}]
        def samples(*_):
            folder=output/"impeller-case/postProcessing/fixture/0";folder.mkdir(parents=True);(folder/"volFieldValue.dat").write_bytes(b"# SYNTHETIC\n500 1\n")
            return [runner.record(output,"impeller-case/postProcessing/fixture/0/volFieldValue.dat")[1]]
        with patch.dict(os.environ,{"RUNNER_TEMP":str(root)}),patch.dict(sys.modules,{"teaching_pump_baseline_case":preparer}),\
             patch.object(runner.safety,"context_guard"),patch.object(runner,"runtime_identity",side_effect=runtime),\
             patch.object(runner,"bind_manifest",side_effect=manifest),patch.object(runner,"execute",side_effect=execute),\
             patch.object(runner.geometry,"verify_region_count",return_value=2),\
             patch.object(runner.base,"verify_merged_mesh",side_effect=ValueError("Actual mesh failed") if bad_mesh else None,return_value={"mergedCellCount":4,"mergedPointCount":10}),\
             patch.object(runner.scientific,"parse_mesh_quality",return_value={"syntheticFixture":True}),\
             patch.object(runner.scientific,"parse_solver_log",return_value={500:{"syntheticFixture":True}}),\
             patch.object(runner,"final_fields",side_effect=fields),patch.object(runner,"sampling_records",side_effect=samples),redirect_stdout(io.StringIO()):
            code=runner.run(source,output)
        return code,json.loads((output/runner.REPORT).read_bytes()),output

    def test_native_completion_retains_raw_evidence_without_numerical_qualification(self):
        with tempfile.TemporaryDirectory() as temp:
            code,report,output=self.campaign(Path(temp))
            self.assertEqual(code,0);self.assertEqual(len(report["stages"]),4)
            self.assertTrue(report["simulationExecuted"]);self.assertTrue(report["actualCfdRunVerified"])
            self.assertFalse(report["numericalBaselineQualified"]);self.assertFalse(report["qualifiedBaseline"])
            self.assertFalse((output/"teaching-baseline-acceptance.json").exists())
            self.assertEqual(report["inputManifestSha256"],runner.record(output,runner.MANIFEST)[1]["sha256"])
            self.assertEqual(len(report["retainedNativeLogRecords"]),4)
            self.assertEqual(len(report["nativeSamplingRecords"]),1)
            self.assertEqual(len(report["nativeNccDiagnosticRecords"]),2)
            self.assertEqual(report["mergedNativeMeshEvidence"],{"mergedCellCount":4,"mergedPointCount":10})
            for key in ("meshConvergenceVerified","timeConvergenceVerified","npsh3Qualified","physicalTransientExecuted","restartVerified","manufacturerGeometryVerified","mainApplicationWrites"):
                self.assertFalse(report[key])

    def test_failed_stage_stops_dependent_solver_and_preserves_actual_exit(self):
        with tempfile.TemporaryDirectory() as temp:code,report,_=self.campaign(Path(temp),failed_stage=1)
        self.assertEqual(code,2);self.assertEqual(len(report["stages"]),2)
        self.assertEqual(report["stages"][-1]["exitCode"],7);self.assertFalse(report["simulationExecuted"])
        self.assertFalse(report["actualCfdRunVerified"])

    def test_checkmesh_failure_stops_solver_without_quality_claim(self):
        with tempfile.TemporaryDirectory() as temp:code,report,_=self.campaign(Path(temp),bad_mesh=True)
        self.assertEqual(code,2);self.assertEqual(len(report["stages"]),3)
        self.assertFalse(report["nativeGeometryQualityVerified"]);self.assertFalse(report["nativeSolverCompleted"])

    def test_actual_fatal_log_cannot_be_promoted_by_exit_zero(self):
        with tempfile.TemporaryDirectory() as temp:code,report,_=self.campaign(Path(temp),bad_log=True)
        self.assertEqual(code,2);self.assertEqual(len(report["stages"]),2)
        self.assertFalse(report["actualCfdRunVerified"])

    def test_missing_endpoint_prevents_actual_cfd_fact_but_retains_solver_completion(self):
        with tempfile.TemporaryDirectory() as temp:code,report,_=self.campaign(Path(temp),missing_fields=True)
        self.assertEqual(code,2);self.assertTrue(report["nativeSolverCompleted"])
        self.assertFalse(report["finiteNativeFieldsVerified"]);self.assertFalse(report["actualCfdRunVerified"])

    def test_actual_source_identity_inputs_or_prior_log_cannot_mutate(self):
        for relative in ("impeller-case/0/p","impeller-case/system/controlDict","source-teaching/source-proof",runner.IDENTITY,"logs/00-mergeMeshes.log","original-source",
                         "impeller-case/postProcessing/checkMesh/0/interface-volute-inlet_coverage.vtk"):
            with self.subTest(relative=relative),tempfile.TemporaryDirectory() as temp:code,report,_=self.campaign(Path(temp),mutate=relative)
            self.assertEqual(code,2);self.assertFalse(report["inputArtifactsImmutable"]);self.assertFalse(report["actualCfdRunVerified"])


if __name__=="__main__":unittest.main()
