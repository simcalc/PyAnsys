"""Synthetic native-process fixtures only; none establish an actual CFD run."""
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("synthetic_snappy_phase_runner", TOOLS / "run_frozen_rotor_snappy_phase.py")
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
fixture_spec = importlib.util.spec_from_file_location("synthetic_phase_source", Path(__file__).parent / "test_frozen_rotor_snappy_phase_case.py")
source_fixture = importlib.util.module_from_spec(fixture_spec); fixture_spec.loader.exec_module(source_fixture)


class Process:
    pid = 123456
    class Stream:
        def fileno(self): return 123
        def close(self): pass
    stdout = Stream()
    def __init__(self, code=0): self.code = code
    def poll(self): return self.code
    def wait(self, timeout): return self.code


class Selector:
    def __init__(self): self.active = True
    def register(self, stream, event): self.stream = stream
    def get_map(self): return {1: True} if self.active else {}
    def select(self, timeout): return [(SimpleNamespace(fileobj=self.stream), None)]
    def unregister(self, stream): self.active = False
    def close(self): pass


def log(): return "Build : 12-86e126a7bc4d\nEnd\n"


class PhaseProcessTests(unittest.TestCase):
    def process_fixture(self, root, *, code=0, times=None):
        output=root/"phase";output.mkdir();(output/"logs").mkdir();(output/"case").mkdir()
        binary=root/"checkMesh";binary.write_bytes(b"SYNTHETIC-NOT-A-NATIVE-EXECUTABLE")
        runtime={"binaries":[{"program":"checkMesh","path":str(binary),"bytes":binary.stat().st_size,
            "sha256":sha256(binary.read_bytes()).hexdigest(),"packageOwnership":"openfoam12: "+str(binary)}]}
        chunks=iter([log().encode(),b""])
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path,"is_relative_to",return_value=True))
            stack.enter_context(patch.object(runner.selectors,"DefaultSelector",Selector))
            popen=stack.enter_context(patch.object(runner.subprocess,"Popen",return_value=Process(code)))
            stack.enter_context(patch.object(runner.os,"read",side_effect=lambda *_:next(chunks)))
            stopped=stack.enter_context(patch.object(runner.native,"stop_owned"))
            if times is not None:stack.enter_context(patch.object(runner.time,"monotonic",side_effect=times))
            result=runner.execute(runner.commands(output)[0],output,1000 if times else runner.time.monotonic()+100,runtime,0)
            self.assertEqual(stopped.call_count,1)
        return result,popen,output

    def test_only_two_fixed_current_root_commands_and_no_sweep_or_motion(self):
        output=Path("synthetic")
        self.assertEqual(runner.commands(output),runner.preparation.commands(output))
        self.assertEqual([row[0] for row in runner.commands(output)],["checkMesh","foamRun"])
        self.assertEqual(runner.commands(output)[0][3:],["-allGeometry","-allTopology","-writeSets"])
        self.assertEqual(runner.MAX_LOG,8*1024**2);self.assertEqual(runner.MAX_MEMORY,4*1024**3)
        self.assertEqual(runner.MAX_WORKSPACE,1024**3);self.assertEqual(runner.MAX_SECONDS,1200)

    def test_actual_binary_hash_secret_free_environment_and_owned_process(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"GH_TOKEN":"SYNTHETIC-FORBIDDEN"}):
            (stage,text),popen,output=self.process_fixture(Path(temp))
        args,kwargs=popen.call_args
        self.assertEqual(args[0][1:],runner.commands(output)[0][1:]);self.assertEqual(kwargs["cwd"],str(output/"case"))
        self.assertIs(kwargs["preexec_fn"],runner.surface.child_limits);self.assertTrue(kwargs["start_new_session"])
        self.assertNotIn("GH_TOKEN",kwargs["env"]);self.assertEqual(stage["logSha256"],sha256(text.encode()).hexdigest())

    def test_nonzero_exit_is_retained_as_fact(self):
        with tempfile.TemporaryDirectory() as temp:(stage,_),_,_=self.process_fixture(Path(temp),code=9)
        self.assertEqual(stage["exitCode"],9)

    def test_log_limit_and_postcompletion_deadline_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(runner,"MAX_LOG",5),self.assertRaises(ValueError):self.process_fixture(Path(temp))
        with tempfile.TemporaryDirectory() as temp,self.assertRaises(ValueError):self.process_fixture(Path(temp),times=[0,0,0,1001])

    def test_unsupported_index_or_command_and_changed_binary_never_start(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(runner.subprocess,"Popen") as popen:
            root=Path(temp);(root/"logs").mkdir();binary=root/"checkMesh";binary.write_bytes(b"changed")
            for arguments,index in ((["foamRun","-case",str(root/"case")],0),(runner.commands(root)[0],1),(runner.commands(root)[0],True)):
                with self.assertRaises(ValueError):runner.execute(arguments,root,1000,{},index)
            with patch.object(Path,"is_relative_to",return_value=True),self.assertRaises(ValueError):
                runner.execute(runner.commands(root)[0],root,1000,{"binaries":[{"program":"checkMesh","path":str(binary),"bytes":1,
                    "sha256":"0"*64,"packageOwnership":"openfoam12: "+str(binary)}]},0)
            popen.assert_not_called()

    def test_free_worker_guard_rejects_local_context_before_output(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp},clear=True),patch.object(runner.preparation,"prepare") as prepare:
            with self.assertRaises(ValueError):runner.run(Path(temp)/"source",Path(temp)/"out")
            prepare.assert_not_called();self.assertFalse((Path(temp)/"out").exists())


class RuntimeAndManifestTests(unittest.TestCase):
    def test_runtime_requires_two_actual_package_owned_binary_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); binaries={name:root/name for name in runner.PROGRAMS}
            for name,path in binaries.items():path.write_bytes(("SYNTHETIC-NATIVE-NOT-EXECUTED:"+name).encode())
            def query(arguments,**_):
                text="20250206" if "--show" in arguments else "openfoam12: "+arguments[-1]+"\n"
                return SimpleNamespace(stdout=text,stderr="")
            with patch.object(runner.subprocess,"run",side_effect=query),patch.object(runner.shutil,"which",side_effect=lambda name:str(binaries[name])),patch.object(Path,"is_relative_to",return_value=True):
                identity=runner.runtime_identity(root,runner.time.monotonic()+10)
            self.assertEqual(identity["schemaVersion"],runner.RUNTIME_SCHEMA)
            self.assertEqual([r["program"] for r in identity["binaries"]],list(runner.PROGRAMS))
            self.assertEqual(identity["expectedSourceCommit"],runner.surface.SOURCE_COMMIT)
            for row in identity["binaries"]:self.assertEqual(row["sha256"],sha256(Path(row["path"]).read_bytes()).hexdigest())
        with tempfile.TemporaryDirectory() as temp,patch.object(runner.subprocess,"run",return_value=SimpleNamespace(stdout="wrong-version",stderr="")),self.assertRaises(ValueError):
            runner.runtime_identity(Path(temp),runner.time.monotonic()+10)

    def test_manifest_canonical_bytes_are_checked_and_persisted_before_native(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);row=source_fixture.write(root,"bound-file",b"actual-synthetic-input")
            packet={"manifest":{"files":[row],"syntheticOnly":True},"study":{"syntheticOnly":True}}
            fake=SimpleNamespace(build_manifest=lambda _:packet,encoded=runner.preparation.encoded)
            with patch.dict(runner.sys.modules,{"reduce_frozen_rotor_snappy_phase":fake}):
                rows,files=runner.bind_manifest(root)
            self.assertEqual(files,[row]);self.assertEqual((root/"phase-input-manifest.json").read_bytes(),fake.encoded(packet["manifest"]))
            self.assertEqual(rows["phase-classroom-study.json"]["sha256"],sha256(fake.encoded(packet["study"])).hexdigest())
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);row=source_fixture.write(root,"bound-file",b"changed");row["sha256"]="0"*64
            fake=SimpleNamespace(build_manifest=lambda _:{"manifest":{"files":[row]},"study":{}},encoded=runner.preparation.encoded)
            with patch.dict(runner.sys.modules,{"reduce_frozen_rotor_snappy_phase":fake}),self.assertRaises(ValueError):runner.bind_manifest(root)


class PhaseOrchestrationTests(unittest.TestCase):
    def scenario(self,root,*,failure=None):
        source=source_fixture.fixture(root);output=root/"phase-run"
        def runtime(directory,deadline):
            identity={"schemaVersion":runner.RUNTIME_SCHEMA,"syntheticFixture":True,"binaries":[]}
            source_fixture.save_json(directory,"phase-runtime-identity.json",identity);return identity
        def manifest(directory):
            prepared=runner.preparation.json_file(directory,runner.preparation.REPORT)
            names={runner.preparation.REPORT,"phase-runtime-identity.json","geometry-identity.json"}
            names.update(row["path"] for row in prepared["sourceEvidenceFileRecords"])
            names.update("case/"+row["path"] for row in prepared["authoredInputFiles"])
            names.update("case/"+row["path"] for row in prepared["preservedNativeMeshFiles"])
            return {"manifest":{"files":[runner.geometry.record(directory,name)[1] for name in sorted(names)],"syntheticOnly":True},"study":{"syntheticOnly":True}}
        fake=SimpleNamespace(build_manifest=manifest,encoded=runner.preparation.encoded)
        def execute(arguments,directory,deadline,identity,index):
            self.assertTrue((directory/"phase-input-manifest.json").exists())
            self.assertTrue((directory/"phase-classroom-study.json").exists())
            self.assertEqual(arguments,runner.commands(directory)[index])
            if index==0:text=source_fixture.mesh_fixture.native_log(2)
            else:
                plan=runner.preparation.json_file(directory,runner.preparation.REPORT)["temporalPlan"]
                instant=format(plan["endTimeS"],".17g");folder=directory/"case"/instant
                for name in runner.END_FIELDS:
                    value="(1 2 3)" if name=="U" else "1" if name=="alpha.water" else "300000" if name in {"p","p_rgh"} else "0" if name=="nut" else "1"
                    kind="volVectorField" if name=="U" else "volScalarField"
                    raw=(f"FoamFile {{format ascii; class {kind}; object {name};}}\ndimensions [{runner.FIELD_DIMENSIONS[name]}];\ninternalField uniform {value};\nboundaryField {{}}\n").encode()
                    if failure=="nonfinite" and name=="p":raw=raw.replace(b"300000",b"nan")
                    if failure!="missing-alpha" or name!="alpha.water":source_fixture.write(directory,"case/"+instant+"/"+name,raw)
                text=log().replace("End\n",("" if failure=="missing-model" else "Selecting cavitation model SchnerrSauer\n")+"Time = "+instant+"s\nEnd\n")
                if failure=="input-mutation":(directory/"case/constant/fvModels").write_bytes(b"changed after startup")
                if failure=="source-mutation":(source/"impeller-case/500/U").write_bytes(b"changed source during native solve")
                if failure=="manifest-mutation":(directory/"phase-input-manifest.json").write_bytes(b"{}")
            row=source_fixture.write(directory,f"logs/{index:02d}-{arguments[0]}.log",text)
            return {"program":arguments[0],"exitCode":7 if failure=="nonzero-exit" and index==0 else 0,"elapsedSeconds":.1,
                "log":Path(row["path"]).name,"logBytes":row["bytes"],"logSha256":row["sha256"]},text
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,{"RUNNER_TEMP":str(root),"GITHUB_RUN_ID":"12345","GITHUB_SHA":"a"*40}))
            stack.enter_context(patch.object(runner.surface,"context_guard"))
            stack.enter_context(patch.object(runner.preparation.baseline,"reduce_native_baseline",side_effect=lambda r:runner.preparation.json_file(r,"native-baseline-reduction.json")))
            stack.enter_context(patch.object(runner,"runtime_identity",side_effect=runtime))
            stack.enter_context(patch.object(runner,"execute",side_effect=execute))
            stack.enter_context(patch.dict(runner.sys.modules,{"reduce_frozen_rotor_snappy_phase":fake}))
            result=runner.run(source,output)
        return result,output

    def test_completion_and_qualified_h0_are_distinct_and_raw_report_is_once_written(self):
        with tempfile.TemporaryDirectory() as temp:
            result,output=self.scenario(Path(temp))
            self.assertEqual(result["status"],runner.STATUS)
            self.assertTrue(result["actualCfdRunVerified"],result.get("diagnostic"))
            self.assertTrue(result["cavitationModelExecuted"]);self.assertTrue(result["finiteNativeEndFieldsVerified"])
            self.assertTrue(result["stationaryMrfRelaxationExecuted"])
            self.assertTrue(all(result[k] is False for k in runner.FALSE_FLAGS))
            self.assertEqual({row["fieldCheck"]["name"] for row in result["finalNativeFieldRecords"]},set(runner.END_FIELDS))
            self.assertEqual(len(result["stages"]),2)
            self.assertEqual(runner.preparation.json_file(output,runner.REPORT),result)
            with self.assertRaises(FileExistsError):runner.base.write_exclusive(output/runner.REPORT,b"would-change-native-facts")

    def test_model_endpoint_nonfinite_and_input_source_mutations_never_claim_verified_run(self):
        for failure in ("nonzero-exit","missing-model","missing-alpha","nonfinite","input-mutation","source-mutation","manifest-mutation"):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as temp:
                result,output=self.scenario(Path(temp),failure=failure)
                self.assertFalse(result["actualCfdRunVerified"])
                self.assertTrue(all(result[k] is False for k in runner.FALSE_FLAGS))
                self.assertTrue((output/runner.REPORT).is_file())
                self.assertNotEqual(result["status"],runner.STATUS)

    def test_rejected_numerical_baseline_never_starts_native_phase(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}),patch.object(runner.surface,"context_guard"),patch.object(runner,"execute") as execute:
            root=Path(temp);source=source_fixture.fixture(root)
            packet=runner.preparation.json_file(source,"native-baseline-reduction.json");packet["numericalBaselineQualified"]=False
            source_fixture.save_json(source,"native-baseline-reduction.json",packet)
            with patch.object(runner.preparation.baseline,"reduce_native_baseline",return_value=packet),self.assertRaises(ValueError):runner.run(source,root/"out")
            execute.assert_not_called();self.assertFalse((root/"out").exists())


if __name__ == "__main__":
    unittest.main()
