"""Synthetic log/process/transfer fixtures only; no native geometry or CFD run."""
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

TOOLS=Path(__file__).resolve().parents[1]/"tools"
spec=importlib.util.spec_from_file_location("surface_runner",TOOLS/"run_frozen_rotor_surface_check.py")
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


def component(name="impeller"):
    return {"component":name,"triangleCount":4,"vertexCount":4,"patches":[{"name":"fixture-wall","triangleCount":4}]}


def log():
    return """Build  : 12-86e126a7bc4d
Statistics:
Triangles    : 4
Vertices     : 4
Bounding Box : (0 0 0) (1 1 1)

Region Size
------ ----
fixture-wall 4

Surface has no illegal triangles.

Triangle quality (equilateral=1, collapsed=0):
    0 .. 0.05  : 0
    min 0.5 for triangle 0
    max 0.8 for triangle 1

Edges:
    min 1 for edge 0 points (0 0 0)(1 0 0)
    max 1.4 for edge 1 points (0 0 0)(1 1 0)

Surface is closed. All edges connected to two faces.
Number of unconnected parts : 1
Number of zones (connected area with consistent normal) : 1
Checking self-intersection.
Surface is not self-intersecting

End
"""


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


class SurfaceLogTests(unittest.TestCase):
    def test_complete_native_log_is_scoped_surface_acceptance(self):
        result=runner.parse_log(log(),component())
        self.assertTrue(result["nativeSurfaceGeometryAccepted"])
        self.assertTrue(result["nativeEdgeManifoldVerified"])
        self.assertFalse(result["vertexManifoldVerified"])
        self.assertEqual(result["selfIntersectionLocationCount"],0)
        self.assertEqual(result["nativeBuildSourceCommit"],runner.SOURCE_COMMIT)

    def test_native_exit_zero_geometry_failures_are_not_accepted(self):
        cases={"illegal":log().replace("no illegal triangles.","2 illegal triangles."),
            "open":log().replace("Surface is closed. All edges connected to two faces.",
                                 "Surface is not closed since not all edges connected to two faces:\n    connected to one face : 3\n    connected to >2 faces : 2"),
            "disconnected":log().replace("unconnected parts : 1","unconnected parts : 2"),
            "orientation":log().replace("consistent normal) : 1","consistent normal) : 2"),
            "intersections":log().replace("not self-intersecting","self-intersecting at 7 locations."),
            "collapsed":log().replace("min 0.5","min 0")}
        for name,value in cases.items():
            with self.subTest(name=name):self.assertFalse(runner.parse_log(value,component())["nativeSurfaceGeometryAccepted"])

    def test_missing_ambiguous_verbose_or_fatal_diagnostics_reject(self):
        for value in ("Statistics:\nTriangles : 4\nVertices : 4\n",log().replace("Checking self-intersection.\n", ""),
                      log()+"Surface is closed. All edges connected to two faces.\n",log()+"FOAM FATAL ERROR\n",
                      log().replace("End\n", ""),log().replace("12-86e126a7bc4d","12-otherbuild")):
            with self.subTest(value=value[:30]),self.assertRaises(runner.SurfaceCheckIssue):runner.parse_log(value,component())

    def test_exact_bound_native_counts_and_tags_required(self):
        for old,new in (("Triangles    : 4","Triangles    : 5"),("Vertices     : 4","Vertices     : 5"),
                        ("fixture-wall 4","fixture-other 4"),("fixture-wall 4","fixture-wall 3"),
                        ("fixture-wall 4","fixture-wall 4\nfixture-wall 4")):
            with self.subTest(old=old),self.assertRaises(runner.SurfaceCheckIssue):runner.parse_log(log().replace(old,new),component())

    def test_nonfinite_native_quality_rejects(self):
        for value in ("nan","inf","-0.1","1.1"):
            with self.subTest(value=value),self.assertRaises(runner.SurfaceCheckIssue):runner.parse_log(log().replace("min 0.5","min "+value),component())

    def test_multiple_consistent_closed_shells_do_not_claim_fluid_connectivity(self):
        value=log().replace("unconnected parts : 1","unconnected parts : 2").replace("consistent normal) : 1","consistent normal) : 2")
        result=runner.parse_log(value,component())
        self.assertTrue(result["nativeSurfaceGeometryAccepted"])
        self.assertFalse(result["fluidVolumeConnectivityVerified"])
        self.assertTrue(result["boundaryShellConnectivityOnly"])

    def test_native_degenerate_triangle_warning_prevents_self_intersection_acceptance(self):
        result=runner.parse_log(log()+"This might give problems in self-intersection testing later on.\n",component())
        self.assertFalse(result["nativeSurfaceGeometryAccepted"])
        self.assertFalse(result["nativeSelfIntersectionTestPassed"])


class SurfaceSafetyTests(unittest.TestCase):
    def test_commands_author_only_two_surface_checks(self):
        rows=runner.commands(Path("fixture-execution"))
        self.assertEqual(len(rows),2)
        for index,row in enumerate(rows):
            self.assertEqual(row[:2],["surfaceCheck","-checkSelfIntersection"])
            self.assertTrue(Path(row[2]).is_absolute())
            self.assertEqual(Path(row[2]).name,runner.COMPONENTS[index]+"-union.obj")
        self.assertNotIn("verbose",repr(rows))

    def test_scope_and_workspace_bounds(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);output=root/"fresh"
            self.assertEqual(runner.scope_path(output,fresh=True),output)
            output.mkdir();(output/"fixture.log").write_bytes(b"x")
            self.assertEqual(runner.workspace_size(output),1)
            with self.assertRaises(runner.SurfaceCheckIssue):runner.scope_path(output,fresh=True)
            with self.assertRaises(runner.SurfaceCheckIssue):runner.scope_path(root.parent/"outside")
            with patch.object(runner,"MAX_WORKSPACE",0),self.assertRaises(runner.SurfaceCheckIssue):runner.workspace_size(output)
            with patch.object(runner,"MAX_PATHS",0),self.assertRaises(runner.SurfaceCheckIssue):runner.workspace_size(output)

    def test_native_environment_does_not_forward_credentials(self):
        with patch.dict(os.environ,{"GH_TOKEN":"synthetic-forbidden","CLOUDFLARE_API_TOKEN":"synthetic-forbidden","PATH":"fixture"}):
            values=runner.native.child_environment()
        self.assertNotIn("GH_TOKEN",values);self.assertNotIn("CLOUDFLARE_API_TOKEN",values)
        self.assertEqual(values["OMP_NUM_THREADS"],"1")

    def test_public_manual_context_is_verified_before_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            event=Path(temp)/"event.json";event.write_text(json.dumps({"repository":{"full_name":"simcalc/PyAnsys","private":False,"visibility":"public"}}))
            with patch.dict(os.environ,{"GITHUB_EVENT_PATH":str(event)}),patch.object(runner.native,"context_guard"):
                runner.context_guard()
                event.write_text(json.dumps({"repository":{"full_name":"simcalc/PyAnsys","private":True}}))
                with self.assertRaises(runner.SurfaceCheckIssue):runner.context_guard()

    def test_owned_child_limits_one_cpu_memory_and_output_bytes(self):
        resource=SimpleNamespace(RLIMIT_AS=1,RLIMIT_CORE=2,RLIMIT_FSIZE=3,setrlimit=Mock())
        with patch.dict(runner.sys.modules,{"resource":resource}),patch.object(runner.os,"sched_getaffinity",return_value={4,7},create=True),patch.object(runner.os,"sched_setaffinity",create=True) as affinity:
            runner.child_limits()
        affinity.assert_called_once_with(0,{4})
        self.assertEqual(resource.setrlimit.call_args_list[0].args,(1,(runner.MAX_MEMORY,runner.MAX_MEMORY)))
        self.assertEqual(resource.setrlimit.call_args_list[2].args,(3,(runner.MAX_FILE,runner.MAX_FILE)))


class SurfaceProcessTests(unittest.TestCase):
    def execute_fixture(self,root,*,content=None,code=0,times=None):
        output=root/"execution";output.mkdir();(output/"logs").mkdir()
        for name in runner.COMPONENTS:(output/"components"/name).mkdir(parents=True)
        executable=root/"surfaceCheck";executable.write_bytes(b"SYNTHETIC-NON-NATIVE-BINARY")
        runtime={"binaries":[{"program":"surfaceCheck","path":str(executable),"bytes":executable.stat().st_size,"sha256":sha256(executable.read_bytes()).hexdigest()}]}
        text=log() if content is None else content
        chunks=iter([text.encode(),b""])
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path,"is_relative_to",return_value=True))
            stack.enter_context(patch.object(runner.selectors,"DefaultSelector",Selector))
            popen=stack.enter_context(patch.object(runner.subprocess,"Popen",return_value=Process(code)))
            stack.enter_context(patch.object(runner.os,"read",side_effect=lambda *_:next(chunks)))
            stack.enter_context(patch.object(runner.native,"stop_owned"))
            if times is not None:stack.enter_context(patch.object(runner.time,"monotonic",side_effect=times))
            result=runner.execute(runner.commands(output)[0],output/"logs/00-impeller-surfaceCheck.log",output,1000 if times is not None else runner.time.monotonic()+100,runtime)
        return result,popen,output

    def test_owned_native_process_uses_actual_binary_and_isolated_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            (stage,text),popen,output=self.execute_fixture(Path(temp))
            self.assertEqual(stage["exitCode"],0);self.assertEqual(text,log())
            args,kwargs=popen.call_args
            self.assertEqual(args[0][1:],runner.commands(output)[0][1:])
            self.assertEqual(kwargs["cwd"],str(output/"components/impeller"))
            self.assertTrue(kwargs["start_new_session"])
            self.assertIs(kwargs["preexec_fn"],runner.child_limits)
            self.assertEqual(stage["logFile"]["sha256"],sha256(log().encode()).hexdigest())

    def test_nonzero_exit_is_retained_not_promoted(self):
        with tempfile.TemporaryDirectory() as temp:
            (stage,_),_,_=self.execute_fixture(Path(temp),code=7)
            self.assertEqual(stage["exitCode"],7)

    def test_log_bytes_and_completion_deadline_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(runner,"MAX_LOG",20),self.assertRaises(runner.SurfaceCheckIssue):
            self.execute_fixture(Path(temp))
        with tempfile.TemporaryDirectory() as temp,self.assertRaises(runner.SurfaceCheckIssue):
            self.execute_fixture(Path(temp),times=[0,0,0,0,1001])

    def test_unsupported_command_is_rejected_before_startup(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(runner.subprocess,"Popen") as popen:
            root=Path(temp)
            for command in (["surfaceCheck","-verbose","fixture.obj"],["foamRun","fixture.obj"],
                            ["surfaceCheck","-checkSelfIntersection","outside.obj"]):
                with self.assertRaises(runner.SurfaceCheckIssue):runner.execute(command,root/"fake.log",root,1000,{})
            popen.assert_not_called()

    def test_changed_binary_bytes_prevent_startup(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(Path,"is_relative_to",return_value=True),patch.object(runner.subprocess,"Popen") as popen:
            root=Path(temp);(root/"logs").mkdir();binary=root/"surfaceCheck";binary.write_bytes(b"CHANGED")
            runtime={"binaries":[{"program":"surfaceCheck","path":str(binary),"bytes":1,"sha256":"0"*64}]}
            with self.assertRaises(runner.SurfaceCheckIssue):
                runner.execute(runner.commands(root)[0],root/"logs/00-impeller-surfaceCheck.log",root,1000,runtime)
            popen.assert_not_called()


class SurfaceBindingTests(unittest.TestCase):
    def test_runtime_binds_actual_bytes_and_package_ownership(self):
        with tempfile.TemporaryDirectory() as temp:
            binary=Path(temp)/"surfaceCheck";binary.write_bytes(b"SYNTHETIC-NON-NATIVE-BINARY")
            replies=[SimpleNamespace(stdout="20250206",stderr=""),SimpleNamespace(stdout="openfoam12: "+str(binary.resolve())+"\n",stderr="")]
            with patch.object(runner.shutil,"which",return_value=str(binary)),patch.object(Path,"is_relative_to",return_value=True),patch.object(runner.subprocess,"run",side_effect=replies) as query:
                result=runner.runtime_identity(runner.time.monotonic()+100)
            self.assertEqual(result["binaries"][0]["sha256"],sha256(binary.read_bytes()).hexdigest())
            self.assertEqual(result["expectedSourceCommit"],runner.SOURCE_COMMIT)
            self.assertEqual(query.call_args_list[1].args[0],["dpkg-query","--search",str(binary.resolve())])

    def test_wrong_package_binary_path_or_ownership_rejects(self):
        with tempfile.TemporaryDirectory() as temp:
            binary=Path(temp)/"surfaceCheck";binary.write_bytes(b"SYNTHETIC")
            for version,inside,owner in (("other",True,"openfoam12"),("20250206",False,"openfoam12"),("20250206",True,"other-package")):
                replies=[SimpleNamespace(stdout=version,stderr=""),SimpleNamespace(stdout=owner+": "+str(binary.resolve())+"\n",stderr="")]
                with self.subTest(version=version,inside=inside,owner=owner),patch.object(runner.shutil,"which",return_value=str(binary)),patch.object(Path,"is_relative_to",return_value=inside),patch.object(runner.subprocess,"run",side_effect=replies),self.assertRaises(runner.SurfaceCheckIssue):
                    runner.runtime_identity(runner.time.monotonic()+100)

    def test_run_retains_surface_only_facts_and_exact_transfer(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);source=root/"source";source.mkdir();output=root/"execution";rows=[]
            for name in runner.COMPONENTS:
                raw=b"SYNTHETIC-UNION-NOT-REAL-GEOMETRY";(source/(name+"-union.obj")).write_bytes(raw)
                row=component(name);row["surfaceFile"]={"path":name+"-union.obj","bytes":len(raw),"sha256":sha256(raw).hexdigest()};rows.append(row)
            (source/"surface-union-report.json").write_text(json.dumps({"schemaVersion":runner.UNION_SCHEMA}))
            (source/"source-topology-report.json").write_text("{}");before={path.name:path.read_bytes() for path in source.iterdir()}
            union=SimpleNamespace(verify_output=lambda _: {"report":{"schemaVersion":runner.UNION_SCHEMA},"reportFile":{},"components":rows})
            def fake_execute(arguments,path,workspace,deadline,runtime):
                path.write_text(log());record=runner.descriptor(path,"logs/"+path.name,runner.MAX_LOG)[1]
                return {"program":"surfaceCheck","exitCode":0,"logFile":record},log()
            with patch.dict(runner.sys.modules,{"frozen_rotor_surface_union":union}),patch.object(runner,"context_guard"),patch.object(runner,"runtime_identity",return_value={"syntheticFixture":True}),patch.object(runner,"execute",side_effect=fake_execute):
                self.assertEqual(runner.run(source,output),0)
            result=json.loads((output/"surface-check-execution.json").read_bytes())
            self.assertTrue(result["nativeSurfaceGeometryAccepted"])
            self.assertTrue(result["originalSurfaceInputsImmutable"])
            self.assertTrue(result["executionSurfaceInputsImmutable"])
            for name in ("simulationExecuted","actualCfdRunVerified","numericalQualityVerified","qualifiedBaseline","fluidVolumeConnectivityVerified","nativeCoordinateUnitsVerified","mainApplicationWrites"):
                self.assertFalse(result[name])
            self.assertEqual(before,{path.name:path.read_bytes() for path in source.iterdir()})
            for name in runner.COMPONENTS:self.assertEqual((output/"components"/name/(name+"-union.obj")).read_bytes(),before[name+"-union.obj"])

    def test_live_input_mutation_prevents_acceptance(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);source=root/"source";source.mkdir();rows=[]
            for name in runner.COMPONENTS:
                path=source/(name+"-union.obj");path.write_bytes(b"SYNTHETIC")
                row=component(name);row["surfaceFile"]=runner.descriptor(path)[1];rows.append(row)
            for name in ("surface-union-report.json","source-topology-report.json"):(source/name).write_text("{}")
            union=SimpleNamespace(verify_output=lambda _: {"report":{"schemaVersion":runner.UNION_SCHEMA},"components":rows})
            def mutate(arguments,path,workspace,deadline,runtime):
                (source/"impeller-union.obj").write_bytes(b"CHANGED")
                path.write_text(log());return {"exitCode":0},log()
            with patch.dict(runner.sys.modules,{"frozen_rotor_surface_union":union}),patch.object(runner,"context_guard"),patch.object(runner,"runtime_identity",return_value={}),patch.object(runner,"execute",side_effect=mutate):
                self.assertEqual(runner.run(source,root/"execution"),2)
            result=json.loads((root/"execution/surface-check-execution.json").read_bytes())
            self.assertFalse(result["nativeSurfaceGeometryAccepted"])
            self.assertFalse(result["originalSurfaceInputsImmutable"])


if __name__=="__main__":unittest.main()
