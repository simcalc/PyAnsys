"""Mocked native utility and synthetic forces: never actual pump/CFD evidence."""
from contextlib import ExitStack
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import teaching_pump_torque_probe as probe
from test_teaching_pump_torque_probe_source import metadata


def table(mz=-2.,fz=3.):
    return ("# Forces\n# CofR : (0 0 0)\n# Time forces(pressure viscous) moments(pressure viscous)\n"
        +f"500 ((0 0 {fz}) (0 0 0)) ((0 0 {mz}) (0 0 0))\n").encode()


def native_log():
    return ("Build : 12-86e126a7bc4d\nTime = 500\nSelecting solver incompressibleFluid\nSelecting RAS turbulence model kOmegaSST\n"
        +"".join("forces "+name+" write:\n" for name in probe.OBJECTS)+"End\n")


class ForceTests(unittest.TestCase):
    def test_actual_native_four_vector_grammar_origin_header_and_signed_power_si(self):
        component=probe.parse_forces(table());self.assertEqual(component["pressureMomentNm"],[0,0,-2])
        scientific=table().replace(b"# CofR : (0 0 0)",b"# CofR : (0.00000000000000000e+00 0.00000000000000000e+00 0.00000000000000000e+00)")
        self.assertEqual(probe.parse_forces(scientific),component)
        tables={name:component for name in probe.OBJECTS};tables["torqueRotor"]=probe.parse_forces(table(-6.,9.))
        result=probe.reduce_forces(tables)
        self.assertAlmostEqual(result["signedRotorToFluidPowerW"],6*2*math.pi*1450/60)
        self.assertEqual(result["fluidOnRotorTorqueNm"],-6.)
        self.assertFalse(result["physicalEnergyBalanceVerified"]);self.assertFalse(result["efficiencyQualified"])
        reverse={name:probe.parse_forces(table(2.)) for name in probe.OBJECTS};reverse["torqueRotor"]=probe.parse_forces(table(6.,9.))
        self.assertLess(probe.reduce_forces(reverse)["signedRotorToFluidPowerW"],0)

    def test_header_wrong_origin_count_porous_columns_nonfinite_and_component_mismatch_reject(self):
        for raw in (table().replace(b"(0 0 0)\n# Time",b"(1 0 0)\n# Time"),table()+table().splitlines()[-1]+b"\n",
                    table().replace(b"pressure viscous)",b"pressure viscous porous)"),table().replace(b"-2.0",b"nan"),table().replace(b"500 ",b"501 ")):
            with self.assertRaises(ValueError):probe.parse_forces(raw)
        tables={name:probe.parse_forces(table()) for name in probe.OBJECTS}
        with self.assertRaisesRegex(ValueError,"component"):probe.reduce_forces(tables)

    def test_exit_end_without_actual_tables_model_or_warning_free_trace_not_success(self):
        self.assertEqual(probe.parse_log(native_log())["storedTime"],500)
        self.assertEqual(probe.parse_log(native_log().replace("Time = 500\n","Time = 500s\n"))["storedTime"],500)
        for text in (native_log()+"Warning: stored field unavailable\n",native_log().replace("kOmegaSST","unknownModel"),
                     native_log().replace("forces torqueHub write:","forces wrong write:"),native_log()+"Solving for p\n"):
            with self.assertRaises(ValueError):probe.parse_log(text)

    def test_flat_exact_dictionary_excludes_stationary_walls_and_flow_execution(self):
        raw=probe.dictionary();self.assertEqual(raw.count(b"type forces;"),4)
        self.assertNotIn(b"functions\n{",raw);self.assertNotIn(b"inblock",raw);self.assertNotIn(b"volute-wall",raw)
        self.assertIn(b"rhoInf 998.2; pRef 0;",raw);self.assertIn(b"CofR (0 0 0);",raw)
        args=probe.command(Path("fixture"));self.assertEqual(args[:5],["foamPostProcess","-solver","incompressibleFluid","-time","500"])
        self.assertNotIn("foamRun",args)


class RuntimeTests(unittest.TestCase):
    def test_actual_owned_utility_and_both_libraries_bound_and_mutation_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ("foamPostProcess",*probe.LIBRARIES):(root/name).write_bytes(("SYNTHETIC-NON-NATIVE-"+name).encode())
            replies=[SimpleNamespace(stdout="20250206",stderr=""),*[SimpleNamespace(stdout="openfoam12: "+str((root/name).resolve()),stderr="") for name in ("foamPostProcess",*probe.LIBRARIES)]]
            with patch.dict(os.environ,{"FOAM_LIBBIN":str(root)}),patch.object(probe.shutil,"which",return_value=str(root/"foamPostProcess")),patch.object(Path,"is_relative_to",return_value=True),patch.object(probe.subprocess,"run",side_effect=replies):
                identity=probe.runtime_identity(probe.time.monotonic()+20);probe.verify_runtime(identity)
                (root/"libforces.so").write_bytes(b"SYNTHETIC-CHANGED")
                with self.assertRaisesRegex(ValueError,"changed"):probe.verify_runtime(identity)
            self.assertEqual([r["role"] for r in identity["nativeObjects"]],["foamPostProcess",*probe.LIBRARIES])

    def test_wrong_package_or_library_ownership_rejects(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/"foamPostProcess").write_bytes(b"SYNTHETIC")
            for version in ("other","20250206"):
                replies=[SimpleNamespace(stdout=version,stderr=""),SimpleNamespace(stdout="other: "+str(root/"foamPostProcess"),stderr="")]
                with patch.dict(os.environ,{"FOAM_LIBBIN":str(root)}),patch.object(probe.shutil,"which",return_value=str(root/"foamPostProcess")),patch.object(Path,"is_relative_to",return_value=True),patch.object(probe.subprocess,"run",side_effect=replies),self.assertRaises(ValueError):
                    probe.runtime_identity(probe.time.monotonic()+20)


class Process:
    pid=123456
    class Stream:
        def fileno(self):return 123
        def close(self):pass
    stdout=Stream()
    def poll(self):return 0
    def wait(self,timeout):return 0


class Selector:
    def __init__(self):self.active=True
    def register(self,stream,event):self.stream=stream
    def get_map(self):return {1:True} if self.active else {}
    def select(self,timeout):return [(SimpleNamespace(fileobj=self.stream),None)]
    def unregister(self,stream):self.active=False
    def close(self):pass


class ProcessTests(unittest.TestCase):
    def execute(self,root,*,times=None,limit=None):
        (root/"logs").mkdir();(root/"impeller-case").mkdir();chunks=iter([native_log().encode(),b""])
        runtime={"nativeObjects":[{"path":"/opt/openfoam12/bin/foamPostProcess"}]}
        with ExitStack() as stack:
            stack.enter_context(patch.object(probe,"verify_runtime"));stack.enter_context(patch.object(probe.selectors,"DefaultSelector",Selector))
            popen=stack.enter_context(patch.object(probe.subprocess,"Popen",return_value=Process()));stop=stack.enter_context(patch.object(probe.native,"stop_owned"))
            stack.enter_context(patch.object(probe.os,"read",side_effect=lambda *_:next(chunks)))
            if times is not None:stack.enter_context(patch.object(probe.time,"monotonic",side_effect=times))
            if limit is not None:stack.enter_context(patch.dict(probe.LIMITS,{"maximumLogBytes":limit}))
            result=probe.execute(probe.command(root),root,1000 if times is not None else probe.time.monotonic()+20,runtime)
        return result,popen,stop

    def test_fresh_owned_postprocess_only_secret_free_environment_log_hash(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"GH_TOKEN":"SYNTHETIC-SECRET","PYANSYS_REPOSITORY_READ_TOKEN":"SYNTHETIC-SECRET"}):
            root=Path(temp);(stage,text),popen,stop=self.execute(root)
            self.assertEqual(stage["logFile"]["sha256"],sha256(text.encode()).hexdigest());self.assertEqual(stage["exitCode"],0)
            kwargs=popen.call_args.kwargs;self.assertEqual(kwargs["cwd"],str(root/"impeller-case"));self.assertTrue(kwargs["start_new_session"])
            self.assertNotIn("GH_TOKEN",kwargs["env"]);self.assertNotIn("PYANSYS_REPOSITORY_READ_TOKEN",kwargs["env"]);stop.assert_called_once()

    def test_log_bound_and_fast_completion_deadline_reject_and_stop_only_owned(self):
        with tempfile.TemporaryDirectory() as temp,self.assertRaisesRegex(ValueError,"8MiB"):self.execute(Path(temp),limit=5)
        with tempfile.TemporaryDirectory() as temp,self.assertRaisesRegex(ValueError,"completion"):self.execute(Path(temp),times=[0,0,0,0,0,0,1001])


class RunnerTests(unittest.TestCase):
    def run_fixture(self,root,*,mutate=False):
        parent=root/"transfer";source=parent/probe.source.SOURCE;source.mkdir(parents=True)
        rows=[]
        for name,raw in {"impeller-case/500/U":b"SYNTHETIC-FIELD","impeller-case/system/controlDict":b"SYNTHETIC-DICT"}.items():
            path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            rows.append({"path":name,"bytes":len(raw),"sha256":sha256(raw).hexdigest()})
        rows.sort(key=lambda r:r["path"]);archive=b"SYNTHETIC-NON-NATIVE-SOURCE-ARCHIVE";(parent/probe.source.ARCHIVE).write_bytes(archive)
        checked={"files":rows,"syntheticFixture":True};runtime={"schemaVersion":"SYNTHETIC-NON-NATIVE-RUNTIME"}
        def execute(args,out,deadline,bound):
            for name in probe.OBJECTS:
                path=out/f"impeller-case/postProcessing/{name}/500/forces.dat";path.parent.mkdir(parents=True)
                path.write_bytes(table(-6.,9.) if name=="torqueRotor" else table())
            log=out/"logs/00-foamPostProcess.log";log.write_bytes(native_log().encode())
            if mutate is True:(out/"impeller-case/500/U").write_bytes(b"SYNTHETIC-MUTATED-FIELD")
            elif mutate:(out/mutate).write_bytes(b"SYNTHETIC-MUTATED-METADATA")
            return {"program":"foamPostProcess","exitCode":0},native_log()
        with patch.dict(os.environ,{"RUNNER_TEMP":str(root)}),patch.object(probe.source,"ARTIFACT_BYTES",len(archive)),patch.object(probe.source,"ARTIFACT_SHA",sha256(archive).hexdigest()),ExitStack() as stack:
            admission=probe.source.validate_metadata(*metadata());(parent/probe.source.ADMISSION).write_bytes(probe.encoded(admission))
            transfer={"admission":admission,"sourceFiles":rows,"archiveFile":probe.record(parent,probe.source.ARCHIVE)[1],"byteExactTransferVerified":True}
            (parent/probe.source.REPORT).write_bytes(probe.encoded(transfer))
            stack.enter_context(patch.object(probe.safety,"context_guard"));stack.enter_context(patch.object(probe.source,"verify_tree",return_value=checked))
            stack.enter_context(patch.object(probe.source,"archive_tree_binding",return_value=transfer["archiveFile"]))
            stack.enter_context(patch.object(probe.source,"inventory",return_value=rows));stack.enter_context(patch.object(probe,"runtime_identity",return_value=runtime))
            stack.enter_context(patch.object(probe,"verify_runtime"));stack.enter_context(patch.object(probe,"execute",side_effect=execute))
            stack.enter_context(patch.object(probe,"hydraulic_diagnostic",return_value={"syntheticFixture":True,"physicalEnergyBalanceVerified":False}))
            result=probe.run(source,root/"diagnostic")
        return result,root/"diagnostic"

    def test_complete_native_mock_stays_unqualified_and_input_bytes_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            result,out=self.run_fixture(Path(temp))
            self.assertTrue(result["nativePostprocessCompleted"]);self.assertTrue(result["inputArtifactsImmutable"])
            self.assertTrue(all(result[k] is False for k in probe.FALSE_FLAGS));self.assertEqual(len(result["forceTableRecords"]),4)
            self.assertEqual(json.loads((out/probe.REPORT).read_bytes()),result)

    def test_changed_stored_field_retains_native_stage_but_never_promotes_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            result,out=self.run_fixture(Path(temp),mutate=True)
            self.assertFalse(result["nativePostprocessCompleted"]);self.assertFalse(result["inputArtifactsImmutable"])
            self.assertEqual(result["stages"][0]["exitCode"],0);self.assertIn("unchanged",result["correctiveAction"])
            self.assertTrue((out/probe.REPORT).exists())

    def test_final_current_byte_replay_deadline_rejects_diagnostic_completion(self):
        def expired(deadline,stage):
            if stage=="final current-byte replay":raise ValueError("The300s final replay deadline expired.")
        with tempfile.TemporaryDirectory() as temp,patch.object(probe,"deadline_check",side_effect=expired):
            result,_=self.run_fixture(Path(temp))
            self.assertFalse(result["nativePostprocessCompleted"]);self.assertIn("deadline",result["correctiveAction"])
            self.assertEqual(len(result["forceTableRecords"]),4)

    def test_current_runtime_and_input_manifest_metadata_must_remain_byte_bound(self):
        for name in (probe.RUNTIME,probe.MANIFEST):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                result,_=self.run_fixture(Path(temp),mutate=name)
                self.assertFalse(result["nativePostprocessCompleted"]);self.assertFalse(result["inputArtifactsImmutable"])
                self.assertIn("metadata changed",result["correctiveAction"])


class WorkflowTests(unittest.TestCase):
    def test_only_manual_public_free_read_permissions_fixed_source_no_flow_retrial(self):
        text=(Path(__file__).resolve().parents[1]/".github/workflows/teaching-pump-free-torque-probe.yml").read_text()
        self.assertIn("workflow_dispatch:",text);self.assertNotIn("schedule:",text);self.assertNotIn("contents: write",text)
        self.assertIn("persist-credentials: false",text);self.assertEqual(text.count("PYANSYS_REPOSITORY_READ_TOKEN:"),1)
        self.assertIn("MemoryMax=4294967296",text);self.assertIn("CPUQuota=100%",text);self.assertIn("330s",text)
        self.assertNotIn("tools/run_teaching_pump_baseline.py",text);self.assertIn("tools/public_runner_guard.py",text)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",text)

    @unittest.skipUnless(sys.platform=="linux" and shutil.which("bash"),"Native workflow Bash grammar runs on its Linux test host.")
    def test_all_multiline_workflow_bash_blocks_parse(self):
        text=(Path(__file__).resolve().parents[1]/".github/workflows/teaching-pump-free-torque-probe.yml").read_text()
        import re
        for block in re.findall(r"(?m)^        run: \|\n((?:          .*\n)+)",text):
            script="\n".join(line[10:] for line in block.splitlines())
            result=subprocess.run(["bash","-n"],input=script,text=True,capture_output=True);self.assertEqual(result.returncode,0,result.stderr)


if __name__=="__main__":unittest.main()
