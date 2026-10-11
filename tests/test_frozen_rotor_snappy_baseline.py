"""Synthetic native-process orchestration; fixtures are never CFD evidence."""
from contextlib import ExitStack,redirect_stdout
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOLS=Path(__file__).resolve().parents[1]/"tools"
spec=importlib.util.spec_from_file_location("snappy_baseline_runner",TOOLS/"run_frozen_rotor_snappy_baseline.py")
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


def complete_log():return "Build : 12-86e126a7bc4d\nEnd\n"


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
    def get_map(self):return {1:True}if self.active else {}
    def select(self,timeout):return [(SimpleNamespace(fileobj=self.stream),None)]
    def unregister(self,stream):self.active=False
    def close(self):pass


class NativeBaselineProcessTests(unittest.TestCase):
    def test_real_preparer_and_runner_author_the_same_four_commands_and_fields(self):
        import frozen_rotor_snappy_baseline_case as preparation
        self.assertEqual(runner.commands(Path("fixture")),preparation.commands(Path("fixture")))
        inputs=preparation.case_files("impeller",300000)
        self.assertIn("pRef 300000;",inputs["system/controlDict"].decode("ascii"))
        self.assertIn("fields true;",inputs["system/createNonConformalCouplesDict"].decode("ascii"))
        for name in ("U","p","k","omega","nut"):
            self.assertIn("backgroundBox",inputs["0/"+name].decode("ascii"))
        self.assertNotIn("system/snappyHexMeshDict",inputs)

    def fixture(self,root,*,code=0,times=None,content=None):
        output=root/"execution";output.mkdir();(output/"logs").mkdir();(output/"impeller-case").mkdir()
        binary=root/"mergeMeshes";binary.write_bytes(b"SYNTHETIC-NON-NATIVE-BINARY")
        runtime={"binaries":[{"program":"mergeMeshes","path":str(binary),"bytes":binary.stat().st_size,"sha256":sha256(binary.read_bytes()).hexdigest()}]}
        text=complete_log()if content is None else content;chunks=iter([text.encode(),b""])
        with ExitStack()as stack:
            stack.enter_context(patch.object(Path,"is_relative_to",return_value=True))
            stack.enter_context(patch.object(runner.selectors,"DefaultSelector",Selector))
            popen=stack.enter_context(patch.object(runner.subprocess,"Popen",return_value=Process(code)))
            stack.enter_context(patch.object(runner.os,"read",side_effect=lambda *_:next(chunks)))
            stack.enter_context(patch.object(runner.native,"stop_owned"))
            if times is not None:stack.enter_context(patch.object(runner.time,"monotonic",side_effect=times))
            result=runner.execute(runner.commands(output)[0],output,1000 if times is not None else runner.time.monotonic()+100,runtime,0)
        return result,popen,output

    def test_four_commands_do_not_repeat_snappy_or_couple_previously_merged_source(self):
        rows=runner.commands(Path("fixture"))
        self.assertEqual([row[0]for row in rows],["mergeMeshes","createNonConformalCouples","checkMesh","foamRun"])
        self.assertEqual(rows[0][3],"-addCases")
        self.assertEqual(rows[2][3:],["-allGeometry","-allTopology","-writeSets"])
        self.assertNotIn("snappyHexMesh",repr(rows))

    def test_bound_native_binary_isolated_cwd_and_secret_free_owned_process(self):
        with tempfile.TemporaryDirectory()as temp,patch.dict(os.environ,{"GH_TOKEN":"SYNTHETIC-FORBIDDEN"}):
            (stage,text),popen,output=self.fixture(Path(temp))
        args,kwargs=popen.call_args
        self.assertEqual(args[0][1:],runner.commands(output)[0][1:])
        self.assertEqual(kwargs["cwd"],str(output/"impeller-case"))
        self.assertTrue(kwargs["start_new_session"])
        self.assertIs(kwargs["preexec_fn"],runner.surface.child_limits)
        self.assertNotIn("GH_TOKEN",kwargs["env"])
        self.assertEqual(stage["logSha256"],sha256(text.encode()).hexdigest())

    def test_nonzero_exit_is_retained_without_acceptance(self):
        with tempfile.TemporaryDirectory()as temp:(stage,_),_,_=self.fixture(Path(temp),code=7)
        self.assertEqual(stage["exitCode"],7)

    def test_log_limit_and_postcompletion_deadline_are_enforced(self):
        with tempfile.TemporaryDirectory()as temp,patch.object(runner,"MAX_LOG",5),self.assertRaises(runner.surface.SurfaceCheckIssue):self.fixture(Path(temp))
        with tempfile.TemporaryDirectory()as temp,self.assertRaises(runner.surface.SurfaceCheckIssue):self.fixture(Path(temp),times=[0,0,0,0,1001])

    def test_command_index_and_changed_binary_reject_before_startup(self):
        with tempfile.TemporaryDirectory()as temp,patch.object(runner.subprocess,"Popen")as popen:
            root=Path(temp);(root/"logs").mkdir();binary=root/"mergeMeshes";binary.write_bytes(b"SYNTHETIC-CHANGED")
            for args,index in ((["snappyHexMesh","-case",str(root)],0),(runner.commands(root)[0],1),(runner.commands(root)[0],True)):
                with self.assertRaises(runner.surface.SurfaceCheckIssue):runner.execute(args,root,1000,{},index)
            with patch.object(Path,"is_relative_to",return_value=True),self.assertRaises(runner.surface.SurfaceCheckIssue):
                runner.execute(runner.commands(root)[0],root,1000,{"binaries":[{"program":"mergeMeshes","path":str(binary),"bytes":1,"sha256":"0"*64}]},0)
            popen.assert_not_called()


class NativeRuntimeBindingTests(unittest.TestCase):
    def test_current_manifest_study_and_geometry_use_the_reducer_canonical_bytes(self):
        def encoded(data):return (json.dumps(data,sort_keys=True,separators=(",",":"))+"\n").encode()
        bound={"geometryIdentity":{"b":2,"a":1},"manifest":{"files":[],"syntheticFixture":True},"study":{"syntheticFixture":True}}
        reducer=SimpleNamespace(build_manifest=lambda _:bound,encoded=encoded)
        with tempfile.TemporaryDirectory()as temp,patch.dict(runner.sys.modules,{"reduce_frozen_rotor_snappy_baseline":reducer}):
            root=Path(temp);records,files=runner.bind_manifest(root)
            for name,key in (("geometry-identity.json","geometryIdentity"),("native-input-manifest.json","manifest"),("classroom-study.json","study")):
                self.assertEqual((root/name).read_bytes(),encoded(bound[key]))
                self.assertEqual(records[name]["sha256"],sha256(encoded(bound[key])).hexdigest())
            self.assertEqual(files,[])

    def test_oversized_canonical_manifest_is_rejected_before_persistence(self):
        reducer=SimpleNamespace(build_manifest=lambda _:{"geometryIdentity":{}},encoded=lambda _:b"x"*(runner.base.inventory.MAX_REPORT_BYTES+1))
        with tempfile.TemporaryDirectory()as temp,patch.dict(runner.sys.modules,{"reduce_frozen_rotor_snappy_baseline":reducer}):
            with self.assertRaises(runner.surface.SurfaceCheckIssue):runner.bind_manifest(Path(temp))
            self.assertFalse((Path(temp)/"geometry-identity.json").exists())

    def test_actual_four_binaries_are_measured_with_official_package_ownership(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp)
            for name in runner.PROGRAMS:(root/name).write_bytes(("SYNTHETIC-"+name).encode())
            replies=[SimpleNamespace(stdout="20250206",stderr=""),*[SimpleNamespace(stdout="openfoam12: "+str((root/name).resolve())+"\n",stderr="")for name in runner.PROGRAMS]]
            with patch.object(runner.shutil,"which",side_effect=lambda name:str(root/name)),patch.object(Path,"is_relative_to",return_value=True),patch.object(runner.subprocess,"run",side_effect=replies):
                identity=runner.runtime_identity(root,runner.time.monotonic()+100)
            self.assertEqual(identity["schemaVersion"],runner.RUNTIME_SCHEMA)
            self.assertEqual([r["program"]for r in identity["binaries"]],list(runner.PROGRAMS))
            self.assertEqual(identity["expectedSourceCommit"],runner.surface.SOURCE_COMMIT)
            self.assertEqual(json.loads((root/"runtime-identity.json").read_bytes()),identity)

    def test_wrong_package_path_or_ownership_is_not_accepted(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);binary=root/"mergeMeshes";binary.write_bytes(b"SYNTHETIC")
            for version,inside,owner in (("other",True,"openfoam12"),("20250206",False,"openfoam12"),("20250206",True,"other")):
                replies=[SimpleNamespace(stdout=version,stderr=""),SimpleNamespace(stdout=owner+": "+str(binary.resolve()),stderr="")]
                with self.subTest(version=version,inside=inside,owner=owner),patch.object(runner.shutil,"which",return_value=str(binary)),patch.object(Path,"is_relative_to",return_value=inside),patch.object(runner.subprocess,"run",side_effect=replies),self.assertRaises(runner.surface.SurfaceCheckIssue):
                    runner.runtime_identity(root,runner.time.monotonic()+100)
                self.assertFalse((root/"runtime-identity.json").exists())


class SyntheticBaselineCampaignTests(unittest.TestCase):
    def campaign(self,root,*,failed_stage=None,missing_fields=False,mutate=None,qualification=False,bad_mesh=False):
        """A process/API fixture only; every scientific native reader is mocked."""
        source=root/"source";source.mkdir();output=root/"execution";components=[
            {"component":name,"caseDirectory":name+"-case","cellCount":2,"nodeCount":5,"cellZone":zone,"patchTypes":{name+"-fixture":"wall"}}
            for name,zone in zip(("impeller","volute"),("rotating-fluid","stationary-fluid"))]
        def prepare(*args,**kwargs):
            output.mkdir();authored=[];copies=[]
            for name in ("impeller-case/system/controlDict","impeller-case/0/p","source-snappy/points"):
                path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"SYNTHETIC-INPUT")
                (copies if name.startswith("source-snappy/")else authored).append(runner.geometry.record(output,name)[1])
            report={"schemaVersion":runner.PREPARATION_SCHEMA,"nativeCommandArguments":runner.commands(output),"limits":{"maximumSeconds":1200,"cpus":1,"memoryBytes":4*1024**3},
                "authoredInputFiles":authored,"sourceComponentFiles":copies,"sourceEvidenceFileRecords":copies,"components":components,
                "sourceSnappyExecutionReportSha256":"0"*64,"authoredPhysicsFileRecords":[]}
            (output/"openfoam-preparation.json").write_text(json.dumps(report));return report
        def verify(*args,**kwargs):return {"report":json.loads((output/"openfoam-preparation.json").read_bytes()),"reportFile":runner.geometry.record(output,"openfoam-preparation.json")[1],"components":components}
        preparer=SimpleNamespace(prepare=prepare,verify_output=verify,validate_inputs=lambda _:None)
        def runtime(*_):
            (output/"runtime-identity.json").write_text('{"syntheticFixture":true}');return {"syntheticFixture":True}
        def manifest(*_):
            rows={}
            for name in ("geometry-identity.json","native-input-manifest.json","classroom-study.json"):
                (output/name).write_text('{"syntheticFixture":true}');rows[name]=runner.geometry.record(output,name)[1]
            return rows,[runner.geometry.record(output,"impeller-case/0/p")[1]]
        def execute(arguments,workspace,deadline,identity,index):
            text=complete_log()+('Mesh OK.\n'if index==2 and not bad_mesh else '')
            path=output/"logs"/(f"{index:02d}-{arguments[0]}.log");path.write_text(text)
            row=runner.geometry.record(output,"logs/"+path.name)[1]
            # Native NCC rewrites the current fields; this is explicitly allowed
            # before the current active post-NCC manifest binds those bytes.
            if index==1:(output/"impeller-case/0/p").write_bytes(b"SYNTHETIC-NCC-REWRITTEN")
            if mutate and index==3:(output/mutate).write_bytes(b"SYNTHETIC-MUTATION")
            return {"program":arguments[0],"exitCode":7 if index==failed_stage else 0,"log":path.name,"logBytes":row["bytes"],"logSha256":row["sha256"]},text
        def fields(*_):
            if missing_fields:raise runner.surface.SurfaceCheckIssue("Actual endpoint fields missing")
            return [{"name":"p","bytes":1,"sha256":"0"*64}],[{"path":"impeller-case/500/p","bytes":1,"sha256":"0"*64}],[{"syntheticFixture":True}]
        reducer=SimpleNamespace(reduce_native_baseline=lambda _: {"qualifiedBaseline":False,
            "numericalBaselineQualified":qualification,"actualCfdRunVerified":True,"fullGeometryQualification":False,
            "qualificationScope":"Synthetic fixture of exploratory numerical acceptance only.","diagnostics":[],"syntheticFixture":True})
        with patch.dict(os.environ,{"RUNNER_TEMP":str(root)}),patch.dict(runner.sys.modules,{"frozen_rotor_snappy_baseline_case":preparer,"reduce_frozen_rotor_snappy_baseline":reducer}),\
             patch.object(runner.surface,"context_guard"),patch.object(runner,"runtime_identity",side_effect=runtime),\
             patch.object(runner,"bind_manifest",side_effect=manifest),patch.object(runner,"execute",side_effect=execute),\
             patch.object(runner.geometry,"native_mesh",return_value={"cellCount":4,"nodeCount":10}),\
             patch.object(runner.scientific,"parse_solver_log",return_value={500:{"syntheticFixture":True}}),\
             patch.object(runner,"final_fields",side_effect=fields),redirect_stdout(io.StringIO()):
            if bad_mesh:
                with patch.object(runner.geometry,"native_mesh",side_effect=runner.surface.SurfaceCheckIssue("Actual mesh quality failed")),patch.object(runner,"quality_diagnostic",return_value={"syntheticFixture":True}):
                    code=runner.run(source,output)
            else:code=runner.run(source,output)
        return code,json.loads((output/"openfoam-execution.json").read_bytes()),json.loads((output/"openfoam-acceptance.json").read_bytes()),output

    def test_execution_and_numerical_qualification_are_separate_immutable_reports(self):
        with tempfile.TemporaryDirectory()as temp:
            code,raw,summary,output=self.campaign(Path(temp),qualification=True)
            self.assertEqual(code,0);self.assertEqual(len(raw["stages"]),4)
            self.assertTrue(raw["actualCfdRunVerified"]);self.assertFalse(raw["qualifiedBaseline"])
            self.assertFalse(summary["qualifiedBaseline"])
            self.assertFalse(raw["numericalBaselineQualified"])
            self.assertTrue(summary["numericalBaselineQualified"])
            self.assertEqual(summary["status"],"SNAPPY_EXPLORATORY_NUMERICAL_BASELINE_ACCEPTED")
            self.assertEqual(summary["nativeExecutionReport"]["sha256"],sha256((output/"openfoam-execution.json").read_bytes()).hexdigest())
            for key in ("fullGeometryQualification","geometricApproximationQualified","nativeCoordinateUnitsVerified","nativeNodeIdentityEquivalent","wallResolutionQualified","meshConvergenceVerified","timeConvergenceVerified","npsh3Qualified","physicalTransientExecuted","mainApplicationWrites"):
                self.assertFalse(summary[key])

    def test_unqualified_numeric_baseline_does_not_erase_actual_native_completion(self):
        with tempfile.TemporaryDirectory()as temp:code,raw,summary,_=self.campaign(Path(temp))
        self.assertEqual(code,2);self.assertTrue(raw["simulationExecuted"]);self.assertFalse(summary["qualifiedBaseline"])
        self.assertTrue(raw["actualCfdRunVerified"]);self.assertFalse(summary["numericalBaselineQualified"])
        self.assertEqual(summary["status"],"SNAPPY_NUMERICAL_ACCEPTANCE_REQUIRED")

    def test_failed_native_stage_preserves_exit_and_stops_dependent_solver(self):
        with tempfile.TemporaryDirectory()as temp:code,raw,summary,_=self.campaign(Path(temp),failed_stage=1)
        self.assertEqual(code,2);self.assertEqual(len(raw["stages"]),2);self.assertEqual(raw["stages"][-1]["exitCode"],7)
        self.assertEqual(len(raw["retainedNativeLogRecords"]),2)
        self.assertFalse(raw["simulationExecuted"]);self.assertFalse(summary["qualifiedBaseline"])

    def test_checkmesh_failure_retains_diagnostic_and_never_starts_solver(self):
        with tempfile.TemporaryDirectory()as temp:code,raw,_,_=self.campaign(Path(temp),bad_mesh=True)
        self.assertEqual(code,2);self.assertEqual(len(raw["stages"]),3)
        self.assertIn("nativeQualityDiagnostic",raw);self.assertFalse(raw["nativeGeometryQualityVerified"])

    def test_missing_native_endpoint_fields_prevents_actual_cfd_claim(self):
        with tempfile.TemporaryDirectory()as temp:code,raw,_,_=self.campaign(Path(temp),missing_fields=True)
        self.assertEqual(code,2);self.assertTrue(raw["nativeSolverCompleted"])
        self.assertFalse(raw["finiteNativeFieldsVerified"]);self.assertFalse(raw["actualCfdRunVerified"])

    def test_actual_bound_inputs_geometry_and_source_cannot_mutate_during_solver(self):
        for path in ("impeller-case/0/p","impeller-case/system/controlDict","source-snappy/points","geometry-identity.json"):
            with self.subTest(path=path),tempfile.TemporaryDirectory()as temp:code,raw,_,_=self.campaign(Path(temp),mutate=path)
            self.assertEqual(code,2);self.assertFalse(raw["inputArtifactsImmutable"]);self.assertFalse(raw["actualCfdRunVerified"])


if __name__=="__main__":unittest.main()
