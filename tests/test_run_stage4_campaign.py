"""Bounded orchestration fixtures only; no native CFD is executed by these tests."""
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools/run_stage4_campaign.py"
spec = importlib.util.spec_from_file_location("stage4_runner", TOOL)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def clock_log(start=0., end=.5, dt=.1):
    lines = ["SYNTHETIC-CLOCK-NOT-CFD", "Selecting cavitation model SchnerrSauer"]
    for step in range(1, round((end-start)/dt)+1):
        lines.extend(["Courant Number mean: 0.1 max: 0.2", "Interface Courant Number mean: 0.05 max: 0.1",
                      "Time = " + format(start+step*dt, ".17g")])
    return "\n".join(lines+["End", ""])


class Process:
    pid=123456
    class Stream:
        def fileno(self): return 123
        def close(self): pass
    stdout=Stream()
    def __init__(self, code=0, running=False): self.code=code; self.running=running
    def poll(self): return None if self.running else self.code
    def wait(self, timeout): self.running=False; return self.code


class Selector:
    def __init__(self): self.active=True
    def register(self, stream, event): self.stream=stream
    def get_map(self): return {1: True} if self.active else {}
    def select(self, timeout): return [(SimpleNamespace(fileobj=self.stream), None)]
    def unregister(self, stream): self.active=False
    def close(self): pass


class Stage4ClockAndScopeContracts(unittest.TestCase):
    def test_actual_uniform_clock_is_verified_for_fresh_and_resumed_grids(self):
        for start in (0., .2):
            prepared={"initialTimeS":start,"endTimeS":.5,"timeStepS":.1}
            result=runner.fixed_clock(clock_log(start),prepared)
            self.assertTrue(result["fixedClockVerified"])
            self.assertEqual(result["nativeStepCount"],round((.5-start)/.1))
            self.assertEqual(result["physicalStartTimeS"],start)
            self.assertTrue(result["courantCriteriaSatisfied"])
            self.assertFalse(result["simulationAcceptanceClaimed"])

    def test_adaptive_skipped_or_nonfinite_clock_and_missing_courant_reject(self):
        prepared={"initialTimeS":0.,"endTimeS":.5,"timeStepS":.1}
        for text in (clock_log().replace("Time = 0.30000000000000004","Time = 0.31"),
                     clock_log().replace("Time = 0.5\n",""),clock_log()+"deltaT = 0.2\n",
                     clock_log().replace("Interface Courant Number", "Unrecognized Courant Number")):
            with self.subTest(text=text[:40]),self.assertRaises(runner.Stage4ExecutionIssue):
                runner.fixed_clock(text,prepared)
        high=runner.fixed_clock(clock_log().replace("max: 0.2","max: 1.2"),prepared)
        self.assertFalse(high["courantCriteriaSatisfied"])
        self.assertTrue(high["fixedClockVerified"])

    def test_workspace_branch_and_total_bounds_and_link_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/"native-field").write_bytes(b"x"*33)
            with self.assertRaisesRegex(runner.Stage4ExecutionIssue,"byte bound"):
                runner.workspace_size(root,32)
            self.assertEqual(runner.workspace_size(root,33),33)
            with patch.object(runner,"MAX_PATHS",0),self.assertRaises(runner.Stage4ExecutionIssue):
                runner.workspace_size(root)

    def test_actual_manual_public_event_is_required_before_preparation(self):
        for repository in ({"full_name":"simcalc/PyAnsys","private":False},
                           {"full_name":"simcalc/npsh","private":False},
                           {"full_name":"simcalc/PyAnsys","private":True}):
            with tempfile.TemporaryDirectory() as temp:
                event=Path(temp)/"event.json";event.write_text(json.dumps({"repository":repository}))
                with patch.object(runner.native,"context_guard"),patch.dict(os.environ,{"GITHUB_EVENT_PATH":str(event)}):
                    if repository.get("full_name")=="simcalc/PyAnsys" and repository["private"] is False:
                        runner.context_guard()
                    else:
                        with self.assertRaises(runner.Stage4ExecutionIssue): runner.context_guard()


class Stage4NativeProcessContracts(unittest.TestCase):
    def fixture(self, root, payload=b"End\n", code=0, clock=None, extra=None):
        branch=root/"coarse-case"; (branch/"logs").mkdir(parents=True)
        process=Process(code)
        executable=b"SYNTHETIC-EXECUTABLE-BYTES-NOT-INSTALLED"
        runtime={"binaries":[{"program":"foamRun","path":"/opt/openfoam12/bin/foamRun","bytes":len(executable),"sha256":sha256(executable).hexdigest()}]}
        stack=ExitStack()
        original_read=runner.BASE.read_regular
        stack.enter_context(patch.object(runner,"Path",side_effect=lambda value:Path(value).resolve() if str(value).startswith("/opt/") else Path(value)))
        stack.enter_context(patch.object(runner.BASE,"read_regular",side_effect=lambda path,limit:executable if path.name=="foamRun" else original_read(path,limit)))
        stack.enter_context(patch.object(runner.selectors,"DefaultSelector",Selector))
        stack.enter_context(patch.object(runner.os,"read",side_effect=[payload,b""]))
        stack.enter_context(patch.object(runner.time,"monotonic",side_effect=clock if clock else lambda:1))
        def launched(*args,**kwargs):
            if extra: extra(branch)
            return process
        launch=stack.enter_context(patch.object(runner.subprocess,"Popen",side_effect=launched))
        stop=stack.enter_context(patch.object(runner.native,"stop_owned"))
        self.addCleanup(stack.close)
        return branch,launch,stop,lambda:runner.execute(runner.native.commands(branch)[1],branch/"logs/01-foamRun.log",branch,root,2,runtime)

    def test_hash_bound_native_process_capture_is_scoped_and_credentials_filtered(self):
        with tempfile.TemporaryDirectory() as temp:
            _,launch,stop,invoke=self.fixture(Path(temp))
            with patch.dict(os.environ,{"GH_TOKEN":"SYNTHETIC","CLOUDFLARE_API_TOKEN":"SYNTHETIC","FLUENT_LICENSE":"SYNTHETIC","PATH":"native-path"},clear=True):
                row,text=invoke()
            self.assertEqual(row["sha256"],sha256(b"End\n").hexdigest())
            self.assertEqual(text,"End\n")
            self.assertNotIn("restartVerified",row)
            environment=launch.call_args.kwargs["env"]
            self.assertFalse(set(environment)&{"GH_TOKEN","CLOUDFLARE_API_TOKEN","FLUENT_LICENSE"})
            self.assertEqual(environment["OMP_NUM_THREADS"],"1")
            self.assertTrue(launch.call_args.kwargs["start_new_session"])
            self.assertNotIn("shell",launch.call_args.kwargs)
            stop.assert_called_once()

    def test_native_nonzero_fatal_missing_end_and_postcompletion_deadline_reject(self):
        for payload,code,clock in ((b"End\n",2,None),(b"FOAM FATAL ERROR\nEnd\n",0,None),
                                  (b"Time = 0.1\n",0,None),(b"End\n",0,[1,1,1,3])):
            with tempfile.TemporaryDirectory() as temp:
                _,_,_,invoke=self.fixture(Path(temp),payload,code,clock)
                with self.subTest(code=code),self.assertRaises(runner.Stage4ExecutionIssue): invoke()

    def test_fast_log_and_aggregate_workspace_overflow_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(runner,"MAX_LOG",16):
            branch,_,stop,invoke=self.fixture(Path(temp),b"x"*128)
            with self.assertRaisesRegex(runner.Stage4ExecutionIssue,"log reached"):
                invoke()
            self.assertEqual((branch/"logs/01-foamRun.log").stat().st_size,16)
            stop.assert_called_once()
        with tempfile.TemporaryDirectory() as temp,patch.object(runner,"MAX_CAMPAIGN_BYTES",32),patch.object(runner,"MAX_BRANCH_BYTES",128):
            _,_,stop,invoke=self.fixture(Path(temp),extra=lambda root:(root/"field").write_bytes(b"x"*64))
            # The default bound is fixed at definition; override explicitly via
            # a concrete measured inventory rather than claiming native output.
            original=runner.workspace_size
            with patch.object(runner,"workspace_size",side_effect=lambda root,limit=None:original(root,32 if limit is None else limit)):
                with self.assertRaises(runner.Stage4ExecutionIssue): invoke()
            stop.assert_called_once()

    def test_unapproved_command_and_stale_log_never_start_native_process(self):
        with tempfile.TemporaryDirectory() as temp:
            branch,launch,_,invoke=self.fixture(Path(temp))
            (branch/"logs/01-foamRun.log").write_text("SYNTHETIC-OLD-EVIDENCE")
            with self.assertRaises(runner.Stage4ExecutionIssue): invoke()
            launch.assert_not_called()
            with self.assertRaises(runner.Stage4ExecutionIssue):
                runner.execute(["foamRun","-case",str(branch),"-postProcess"],branch/"logs/other.log",branch,Path(temp),2,{})
            launch.assert_not_called()


class Stage4LifecycleContracts(unittest.TestCase):
    def branch_fixture(self, root, *, failed_mesh=False, changed_input=False, failed_solver=False, resumed=False, changed_checkpoint=False, changed_geometry=False):
        """Mock process completion; retained byte mutations are real test files."""
        branch=root/("resumed-case" if resumed else "coarse-case"); (branch/"case/system").mkdir(parents=True)
        input_path=branch/"case/system/controlDict"
        input_path.write_text("FoamFile {format ascii; class dictionary; object controlDict;}\nSYNTHETIC_NOT_CFD true;\n")
        native_row={"path":"system/controlDict","bytes":input_path.stat().st_size,"sha256":sha256(input_path.read_bytes()).hexdigest()}
        prepared={"id":"resumed" if resumed else "coarse","casePath":branch.name,"initialTimeS":.2 if resumed else 0.,"endTimeS":.5,"timeStepS":.1,
                  "authoredInputFiles":[native_row],"referenceMeshFiles":[native_row]}
        # Distinct role fixture avoids duplicate records; actual native wrapper
        # tests elsewhere validate the full six-file mesh and physical recipe.
        prepared["referenceMeshFiles"]=[]
        geometry_path=branch/"geometry-identity.json"
        geometry_path.write_bytes(runner.canonical({"fixtureKind":"SYNTHETIC-GEOMETRY-NOT-CFD"}))
        prepared["sourceGeometryIdentityFile"]={"path":"geometry-identity.json","bytes":geometry_path.stat().st_size,
                                                "sha256":sha256(geometry_path.read_bytes()).hexdigest()}
        runtime={"fixtureKind":"SYNTHETIC-NOT-INSTALLED"}
        (branch/"stage4-runtime-identity.json").write_bytes(runner.canonical(runtime))
        (branch/"stage4-branch-preparation.json").write_bytes(runner.BASE.inventory.bounded_json(prepared))
        state_path=None
        if resumed:
            prepared["checkpointSha256"]="a"*64
            state_path=branch/"case/0.2/p";state_path.parent.mkdir(parents=True)
            state_path.write_text("FoamFile {format ascii; object p;}\nSYNTHETIC_NOT_CFD true;\n")
            checkpoint={"checkpointSha256":"a"*64,"files":[{"path":"case/0.2/p","bytes":state_path.stat().st_size,"sha256":sha256(state_path.read_bytes()).hexdigest()}]}
            (branch/"stage4-checkpoint.json").write_bytes(runner.canonical(checkpoint))
            (branch/"stage4-resume-preparation.json").write_bytes(runner.canonical({"checkpointSha256":"a"*64,"fixtureKind":"SYNTHETIC-NOT-CFD"}))
        stack=ExitStack()
        stack.enter_context(patch.object(runner,"reduction_module",return_value=SimpleNamespace(build_manifest=lambda *args:{"manifest":{"fixtureKind":"SYNTHETIC-NOT-CFD"},"study":{"fixtureKind":"SYNTHETIC-NOT-CFD"}})))
        def execute(arguments,log,*rest):
            if arguments[0]=="checkMesh":
                text="SYNTHETIC-NOT-CFD\nMesh OK.\nEnd\n"
                if failed_mesh: text="Failed 1 mesh checks\nEnd\n"
            else:
                text=clock_log(prepared["initialTimeS"])
                if changed_input: input_path.write_text("FoamFile {}\nACTUAL_TEST_BYTE_MUTATION\n")
                if changed_checkpoint: state_path.write_text("FoamFile {}\nACTUAL_TEST_CHECKPOINT_MUTATION\n")
                if changed_geometry: geometry_path.write_bytes(runner.canonical({"fixtureKind":"ACTUAL_TEST_GEOMETRY_MUTATION"}))
            log.write_text(text)
            if failed_solver and arguments[0]=="foamRun": raise runner.Stage4ExecutionIssue("Synthetic process correction")
            return {"program":arguments[0],"exitCode":0,"path":"logs/"+log.name,"bytes":log.stat().st_size,"sha256":sha256(log.read_bytes()).hexdigest()},text
        invocation=stack.enter_context(patch.object(runner,"execute",side_effect=execute))
        stack.enter_context(patch.object(runner.native.reduction,"parse_mesh_quality",side_effect=runner.Stage4ExecutionIssue("Synthetic invalid native mesh") if failed_mesh else None,
                                        return_value={"meshCellCount":2}))
        stack.enter_context(patch.object(runner.native,"final_fields",return_value=([{"fixtureKind":"SYNTHETIC-END-FIELDS"}],[(.25,branch/"case/0.25"),(.5,branch/"case/0.5")])))
        stack.enter_context(patch.object(runner.native,"rotor_motion",return_value={"fixtureKind":"SYNTHETIC-MOTION"}))
        stack.enter_context(patch.object(runner.native,"sampling_records",return_value=[]))
        self.addCleanup(stack.close)
        return branch,invocation,lambda:runner.run_branch(branch,prepared,runtime,root,runner.time.monotonic()+100)

    def test_branch_records_native_scope_and_immutable_artifacts_without_research_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            branch,invocations,invoke=self.branch_fixture(Path(temp))
            result,row=invoke()
            raw=(branch/row["path"]).read_bytes()
            self.assertEqual(row["sha256"],sha256(raw).hexdigest())
            self.assertTrue(result["actualCfdRunVerified"])
            self.assertTrue(result["inputArtifactsImmutable"])
            self.assertEqual(invocations.call_count,2)
            for key in ("timeStepConvergenceVerified","restartVerified","meshConvergenceVerified","mainApplicationWrites"):
                self.assertIs(result[key],False)
            before=raw
            with self.assertRaises(FileExistsError): invoke()
            self.assertEqual((branch/row["path"]).read_bytes(),before)

    def test_failed_mesh_solver_and_mutated_inputs_retain_facts_and_stop_dependent_checks(self):
        for bad_mesh,bad_solver,mutation in ((True,False,False),(False,True,False),(False,False,True)):
            with tempfile.TemporaryDirectory() as temp:
                branch,invocations,invoke=self.branch_fixture(Path(temp),failed_mesh=bad_mesh,failed_solver=bad_solver,changed_input=mutation)
                result,row=invoke()
                self.assertFalse(result["actualCfdRunVerified"])
                self.assertFalse(result["inputArtifactsImmutable"])
                self.assertEqual(invocations.call_count,1 if bad_mesh else 2)
                self.assertEqual(len(result["retainedNativeLogRecords"]),invocations.call_count)
                self.assertTrue((branch/row["path"]).is_file())

    def test_resumed_branch_binds_actual_copied_checkpoint_before_and_after_native_process(self):
        for mutation in (False,True):
            with tempfile.TemporaryDirectory() as temp:
                branch,_,invoke=self.branch_fixture(Path(temp),resumed=True,changed_checkpoint=mutation)
                result,_=invoke()
                self.assertEqual(result["checkpointSha256"],"a"*64)
                self.assertEqual(len(result["checkpointInputRecords"]),1)
                self.assertEqual(result["actualCfdRunVerified"],not mutation)
                self.assertFalse(result["restartVerified"])
                self.assertTrue((branch/"stage4-branch-execution.json").is_file())

    def test_geometry_identity_mutation_cannot_claim_immutable_native_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            branch,invocations,invoke=self.branch_fixture(Path(temp),changed_geometry=True)
            original_sha=sha256((branch/"geometry-identity.json").read_bytes()).hexdigest()
            result,_=invoke()
            self.assertEqual(invocations.call_count,2)
            self.assertEqual(result["geometryIdentitySha256"],original_sha)
            self.assertFalse(result["actualCfdRunVerified"])
            self.assertFalse(result["inputArtifactsImmutable"])
            self.assertIn("source geometry changed",result["diagnostic"]["message"])

    def campaign_fixture(self, root, *, failed_coarse=False, changed_source=False, missing_checkpoint=False):
        output=root/"campaign"
        def prepare(*args,**kwargs):
            output.mkdir()
            variants=[]
            for name,dt in (("coarse",.1),("medium",.05),("fine",.025)):
                (output/(name+"-case")).mkdir()
                variants.append({"id":name,"casePath":name+"-case","timeStepS":dt,"initialTimeS":0.,"endTimeS":.5})
            value={"variants":variants,"checkpointPlan":{"physicalTimeS":.25},"fixtureKind":"SYNTHETIC-NOT-CFD"}
            (output/"stage4-case-preparation.json").write_bytes(runner.BASE.inventory.bounded_json(value))
            return value
        def checkpoint(*args): return {"checkpointSha256":"a"*64,"fixtureKind":"SYNTHETIC-NOT-CFD"}
        def resume(*args):
            (output/"resumed-case").mkdir()
            return {"variant":{"id":"resumed","casePath":"resumed-case","timeStepS":.025,"initialTimeS":.25,"endTimeS":.5},
                    "checkpointSha256":"a"*64}
        def branch(path,prepared,*args):
            failed=failed_coarse and prepared["id"]=="coarse"
            times=[] if missing_checkpoint else [{"physicalTimeS":.25,"name":"0.25"},{"physicalTimeS":.5,"name":"0.5"}]
            value={"id":prepared["id"],"status":"SYNTHETIC-CORRECTION" if failed else "SYNTHETIC-EXECUTED-NOT-CFD",
                   "actualCfdRunVerified":not failed,"nativeCheckMeshVerified":True,"simulationExecuted":not failed,"nativeTimeDirectories":times}
            row=runner.write_report(path,"stage4-branch-execution.json",value)
            return value,row
        stack=ExitStack()
        stack.enter_context(patch.object(runner,"context_guard"))
        stack.enter_context(patch.object(runner.native,"program_identity",return_value={"fixtureKind":"SYNTHETIC-NOT-INSTALLED"}))
        stack.enter_context(patch.object(runner,"case_module",return_value=SimpleNamespace(prepare_campaign=prepare,build_checkpoint=checkpoint,prepare_resume=resume)))
        branches=stack.enter_context(patch.object(runner,"run_branch",side_effect=branch))
        stack.enter_context(patch.object(runner.native.preparation,"verify_baseline",side_effect=runner.Stage4ExecutionIssue("Changed source test bytes") if changed_source else None))
        self.addCleanup(stack.close)
        packet={"schemaVersion":runner.native.preparation.NATIVE_REDUCTION_SCHEMA}
        return output,branches,lambda:runner.run(root/"baseline",packet,output,pressure_absolute_pa=101325)

    def test_campaign_consumes_actual_checkpoint_and_keeps_numerics_flags_separate(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            output,branches,invoke=self.campaign_fixture(Path(temp))
            result=invoke();report=json.loads((output/"stage4-campaign-execution.json").read_bytes())
            self.assertTrue(result["timeCampaignExecuted"])
            self.assertTrue(result["restartCampaignExecuted"])
            self.assertEqual([call.args[1]["id"] for call in branches.call_args_list],["coarse","medium","fine","resumed"])
            self.assertTrue(report["sourceBaselineImmutable"])
            self.assertFalse(report["timeStepConvergenceVerified"])
            self.assertFalse(report["restartVerified"])
            self.assertFalse(report["meshConvergenceVerified"])
            self.assertEqual(report["meshBranchStatus"],"IndependentActualMeshFamilyRequired")
            for branch in report["branches"]:
                self.assertEqual(branch["report"]["sha256"],sha256((output/branch["report"]["path"]).read_bytes()).hexdigest())

    def test_independent_branch_failure_source_mutation_or_missing_checkpoint_cannot_close_campaign(self):
        for kwargs in ({"failed_coarse":True},{"changed_source":True},{"missing_checkpoint":True}):
            with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
                output,branches,invoke=self.campaign_fixture(Path(temp),**kwargs)
                result=invoke();report=json.loads((output/"stage4-campaign-execution.json").read_bytes())
                self.assertFalse(result["actualCfdRunVerified"])
                self.assertEqual([call.args[1]["id"] for call in branches.call_args_list[:3]],["coarse","medium","fine"])
                self.assertTrue(report["branches"])
                self.assertFalse(report["restartVerified"])
                if kwargs.get("failed_coarse"):
                    self.assertTrue(report["restartCampaignExecuted"])
                    self.assertFalse(report["timeCampaignExecuted"])
                if kwargs.get("missing_checkpoint"):
                    self.assertIsNone(report["resumedBranch"])


class Stage4CrossModuleContracts(unittest.TestCase):
    def test_real_preparer_and_manifest_builder_accept_current_coarse_fine_and_resume_contract(self):
        # Only upstream qualification/native binary inspection are mocked.
        # Every authored/control/checkpoint byte below is inspected by the
        # actual preparer and reducer, without running any CFD solver.
        fixture_spec=importlib.util.spec_from_file_location("stage4_fixture_helpers",Path(__file__).with_name("test_frozen_rotor_stage4_case.py"))
        fixture=importlib.util.module_from_spec(fixture_spec);fixture_spec.loader.exec_module(fixture)
        stage=runner.case_module();reducer=runner.reduction_module()
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);source=root/"baseline";source_case=source/"impeller-case"
            mesh=fixture.mesh_fixture(source_case)
            study=stage.phase.protocol.prepare_study()
            geometry=fixture.put(source,"geometry-identity.json",stage.encoded({"schemaVersion":"untirta.frozen-rotor-fixed-geometry-identity.v1",
                "exampleDataCommit":runner.BASE.inventory.PIN,"mainApplicationWrites":False,"fixtureKind":"SYNTHETIC-NOT-CFD"}))
            evidence=[fixture.put(source,"logs/fixture.log","SYNTHETIC-NOT-CFD\n")]
            observation={"pressureLevelAbsolutePa":101325.,"controls":{"geometrySha256":sha256(stage.encoded([geometry])).hexdigest()},
                         "nativeEvidence":{"sha256":"a"*64}}
            packet={"schemaVersion":stage.phase.NATIVE_REDUCTION_SCHEMA,"nativeEvidenceRecords":evidence}
            runtime={"fixtureKind":"SYNTHETIC-NOT-INSTALLED","mainApplicationWrites":False}
            with patch.object(stage.phase,"verify_baseline",return_value=(source_case,study,observation,mesh,"b"*64)),\
                 patch.object(stage.phase_reduction,"phase_binary_identity"):
                prepared=stage.prepare_campaign(source,packet,root/"campaign",pressure_absolute_pa=80000.,runtime_identity=runtime)
                families=[]
                for variant in prepared["variants"]:
                    branch=root/"campaign"/variant["casePath"]
                    family=reducer.build_manifest(branch,variant);families.append(family)
                    self.assertTrue(runner.input_records(branch,variant))
                    self.assertTrue(all(row["path"].startswith("case/") for row in runner.input_records(branch,variant)))
                    manifest_row=runner.write_report(branch,"stage4-input-manifest.json",family["manifest"],canonical_bytes=True)
                    self.assertEqual((branch/manifest_row["path"]).read_bytes(),stage.encoded(family["manifest"]))
                    self.assertFalse(family["manifest"]["simulationExecuted"])
                self.assertEqual([f["manifest"]["controls"] for f in families],[families[0]["manifest"]["controls"]]*3)
                variant,name,_=fixture.checkpoint_fixture(root,prepared)
                checkpoint=stage.build_checkpoint(root/"campaign",variant,name)
                resumed=stage.prepare_resume(root/"campaign",checkpoint,root/"campaign/resumed-case")
                active={**resumed["variant"],**resumed}
                resumed_family=reducer.build_manifest(root/"campaign/resumed-case",active)
                self.assertEqual(resumed_family["manifest"]["controls"],families[-1]["manifest"]["controls"])
                self.assertEqual(resumed_family["manifest"]["temporalPlan"]["initialTimeS"],checkpoint["physicalTimeS"])
                self.assertTrue(runner.input_records(root/"campaign/resumed-case",active))
                self.assertFalse(resumed["restartVerified"])


if __name__=="__main__":
    unittest.main()
