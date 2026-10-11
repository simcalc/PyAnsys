"""Process mocks and tiny field fixtures; never evidence of a physical pump CFD run."""
from contextlib import ExitStack, redirect_stdout
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools/run_frozen_rotor_openfoam.py"
spec = importlib.util.spec_from_file_location("frozen_openfoam_runner_tests", TOOL)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class MockProcess:
    """The only launched process in this test file is a Python mock object."""
    def __init__(self, *, running=False, returncode=0, ignore_term=False):
        self.pid = 424242
        self.running = running
        self.returncode = None if running else returncode
        self.ignore_term = ignore_term
        self.waits = 0

    def poll(self):
        return None if self.running else self.returncode

    def wait(self, timeout):
        self.waits += 1
        if self.ignore_term and self.waits == 1:
            raise subprocess.TimeoutExpired("mock-native-process", timeout)
        self.running = False
        self.returncode = -signal.SIGTERM
        return self.returncode


class NativeRunnerContracts(unittest.TestCase):
    def execute_fixture(self, root, payload=b"End\n", process=None, deadline=None, extra=None):
        process = process or MockProcess()
        def launch(*arguments, **options):
            options["stdout"].write(payload)
            options["stdout"].flush()
            if extra:
                extra(root)
            return process
        stack = ExitStack()
        # Linux executable paths are absolute; keep that semantics in Windows mocks.
        stack.enter_context(patch.object(runner, "Path", side_effect=lambda value: Path(value).resolve()
                                         if str(value).startswith("/opt/") else Path(value)))
        stack.enter_context(patch.object(signal, "SIGKILL", 9, create=True))
        stack.enter_context(patch.object(runner.shutil, "which", return_value="/opt/openfoam12/bin/foamRun"))
        factory = stack.enter_context(patch.object(runner.subprocess, "Popen", side_effect=launch))
        kills = stack.enter_context(patch.object(runner.os, "killpg", create=True))
        stack.enter_context(patch.object(runner.time, "sleep"))
        self.addCleanup(stack.close)
        return process, factory, kills, lambda: runner.execute(["foamRun", "-case", str(root)], root / "native.log", root,
                                                              time.monotonic() + 10 if deadline is None else deadline)

    def test_success_is_bounded_hashed_native_completion_not_qualified_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, factory, kills, invoke = self.execute_fixture(root)
            row, text = invoke()
            self.assertEqual(row["logSha256"], sha256(b"End\n").hexdigest())
            self.assertEqual(text, "End\n")
            self.assertEqual(row["exitCode"], 0)
            self.assertNotIn("qualifiedBaseline", row)
            self.assertTrue(factory.call_args.kwargs["start_new_session"])
            kills.assert_not_called()

    def test_missing_or_other_native_executable_rejected_before_launch(self):
        for executable in (None, "/usr/bin/foamRun", "/opt/openfoam120/bin/foamRun"):
            with tempfile.TemporaryDirectory() as temp, patch.object(runner.shutil, "which", return_value=executable), \
                    patch.object(runner.subprocess, "Popen") as factory:
                with self.assertRaises(runner.case_tool.CaseIssue):
                    runner.execute(["foamRun"], Path(temp) / "log", Path(temp), time.monotonic() + 1)
                factory.assert_not_called()
                self.assertFalse((Path(temp) / "log").exists())

    def test_nonzero_exit_fatal_and_missing_End_cannot_be_success(self):
        for payload, code in ((b"End\n", 2), (b"FOAM FATAL ERROR\nEnd\n", 0),
                              (b"Floating point exception\nEnd\n", 0), (b"Time = 500\n", 0)):
            with tempfile.TemporaryDirectory() as temp:
                _, _, _, invoke = self.execute_fixture(Path(temp), payload, MockProcess(returncode=code))
                with self.subTest(payload=payload), self.assertRaises(runner.case_tool.CaseIssue):
                    invoke()

    def test_deadline_terminates_whole_native_session(self):
        with tempfile.TemporaryDirectory() as temp:
            process, _, kills, invoke = self.execute_fixture(Path(temp), process=MockProcess(running=True), deadline=time.monotonic() - 1)
            with self.assertRaises(runner.case_tool.CaseIssue):
                invoke()
            kills.assert_called_once_with(process.pid, signal.SIGTERM)
            self.assertEqual(process.waits, 1)

    def test_unresponsive_session_escalates_SIGTERM_to_SIGKILL(self):
        with tempfile.TemporaryDirectory() as temp:
            process, _, kills, invoke = self.execute_fixture(Path(temp), process=MockProcess(running=True, ignore_term=True), deadline=time.monotonic() - 1)
            with self.assertRaises(runner.case_tool.CaseIssue):
                invoke()
            self.assertEqual([call.args for call in kills.call_args_list], [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)])
            self.assertEqual(process.waits, 2)

    def test_fast_completed_process_still_checks_workspace_and_log_limits(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, "MAX_WORKSPACE", 32):
            _, _, _, invoke = self.execute_fixture(Path(temp), extra=lambda root: (root / "oversize.bin").write_bytes(b"x" * 64))
            with self.assertRaises(runner.case_tool.CaseIssue):
                invoke()

    def test_fast_completed_process_cannot_skip_completion_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            process, _, kills, invoke = self.execute_fixture(Path(temp), deadline=2)
            with patch.object(runner.time, "monotonic", side_effect=[1, 3]):
                with self.assertRaisesRegex(runner.case_tool.CaseIssue, "completion"):
                    invoke()
            self.assertEqual(process.returncode, 0)
            kills.assert_not_called()
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, "MAX_LOG", 16):
            _, _, _, invoke = self.execute_fixture(Path(temp), payload=b"x" * 32 + b"\nEnd\n")
            with self.assertRaises(runner.case_tool.CaseIssue):
                invoke()

    def test_running_log_limit_kills_native_session(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, "MAX_LOG", 16):
            process, _, kills, invoke = self.execute_fixture(Path(temp), payload=b"x" * 64, process=MockProcess(running=True))
            with self.assertRaises(runner.case_tool.CaseIssue):
                invoke()
            kills.assert_called_once_with(process.pid, signal.SIGTERM)

    def test_platform_version_and_actions_preflight_precede_case_writes(self):
        for platform, actions, version in (("win32", "true", "12"), ("linux", "false", "12"), ("linux", "true", "13")):
            with patch.object(runner.sys, "platform", platform), patch.dict(os.environ, {"GITHUB_ACTIONS": actions, "WM_PROJECT_VERSION": version}), \
                    patch.object(runner.case_tool, "prepare") as prepare:
                with self.assertRaises(runner.case_tool.CaseIssue):
                    runner.run("unused-mesh", "unused-output")
                prepare.assert_not_called()

    def test_impeller_fields_cover_all_final_merged_physical_patches(self):
        # Foundation mergeMeshes writes polyMesh only, not the added case's fields.
        files = runner.case_tool.case_files("impeller")
        names = runner.case_tool.PATCHES["impeller"] | runner.case_tool.PATCHES["volute"]
        for field in ("U", "p", "k", "omega", "nut"):
            for name in names:
                with self.subTest(field=field, patch=name):
                    self.assertIn('"' + name + '"', files["0/" + field])

    def run_fixture(self, root, *, fields=True, empty_field=False, post_failure=False, mesh_failed=False):
        output = root / "execution"
        def prepare(*arguments):
            output.mkdir()
            commands = [["checkMesh"], ["foamRun"]] if mesh_failed else [["foamRun"]]
            (output / "openfoam-preparation.json").write_text(json.dumps({"nativeCommandArguments": commands}))
            return {"status": "SYNTHETIC_TEST_PREPARATION"}
        def execute(arguments, log, workspace, deadline):
            log.write_text("SYNTHETIC-MOCK-PROCESS-NOT-CFD\nEnd\n")
            if fields:
                final = output / "impeller-case/500"
                final.mkdir(parents=True)
                for name, dimensions, value in (("p", "0 2 -2 0 0 0 0", "0"), ("U", "0 1 -1 0 0 0 0", "(0 0 0)"),
                                                ("k", "0 2 -2 0 0 0 0", "0.375"), ("omega", "0 0 -1 0 0 0 0", "100")):
                    kind = "volVectorField" if name == "U" else "volScalarField"
                    raw = runner.case_tool.foam_header(name, kind, "500") + "dimensions [" + dimensions + "];\ninternalField uniform " + value + ";\nboundaryField {}\n"
                    (final / name).write_bytes(b"" if empty_field and name == "p" else raw.encode())
            return {"program": arguments[0], "log": log.name}, "Mesh OK.\nFailed 1 mesh checks\nEnd\n" if mesh_failed else "End\n"
        real_size = runner.workspace_size
        def measured_size(path):
            # Concrete end-of-pipeline failure, after the helper produced field records.
            if post_failure and (output / "impeller-case/500/p").is_file():
                count[0] += 1
                if count[0] >= 2:
                    raise runner.case_tool.CaseIssue("Synthetic final output bound reached")
            return real_size(path)
        count = [0]
        stack = ExitStack()
        stack.enter_context(patch.object(runner.sys, "platform", "linux"))
        stack.enter_context(patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "WM_PROJECT_VERSION": "12", "GITHUB_RUN_ID": "1"}))
        stack.enter_context(patch.object(runner.case_tool, "prepare", side_effect=prepare))
        stack.enter_context(patch.object(runner.case_tool, "validate_inputs", return_value=({}, "a" * 64)))
        execution = stack.enter_context(patch.object(runner, "execute", side_effect=execute))
        stack.enter_context(patch.object(runner, "workspace_size", side_effect=measured_size))
        self.addCleanup(stack.close)
        return output, execution, lambda: runner.run(root / "synthetic-input", output)

    def test_completed_mock_runner_does_not_claim_qualified_baseline_NPSH_or_transient(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            output, _, invoke = self.run_fixture(Path(temp))
            self.assertEqual(invoke(), 0)
            report = json.loads((output / "openfoam-execution.json").read_text())
            self.assertEqual(report["status"], "MRF_EXECUTED_ACCEPTANCE_REQUIRED")
            self.assertEqual(len(report["finalNativeFields"]), 4)
            for key in ("qualifiedBaseline", "cavitationSweepExecuted", "npsh3Qualified", "physicalTransientExecuted", "mainApplicationWrites"):
                self.assertIs(report[key], False)

    def test_current_native_endpoint_required_and_zero_size_rejected_without_green_exit(self):
        for fields, empty in ((False, False), (True, True)):
            with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
                output, _, invoke = self.run_fixture(Path(temp), fields=fields, empty_field=empty)
                self.assertEqual(invoke(), 2)
                report = json.loads((output / "openfoam-execution.json").read_text())
                self.assertEqual(report["status"], "NATIVE_STAGE_CORRECTION_REQUIRED")
                self.assertIs(report["actualCfdRunVerified"], False)

    def test_failed_checkMesh_stops_before_foamRun(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            output, execution, invoke = self.run_fixture(Path(temp), mesh_failed=True)
            self.assertEqual(invoke(), 2)
            self.assertEqual(execution.call_count, 1)
            report = json.loads((output / "openfoam-execution.json").read_text())
            self.assertIs(report["nativeGeometryQualityVerified"], False)
            self.assertIs(report["actualCfdRunVerified"], False)

    def test_final_bound_correction_returns_nonzero_even_after_solver_executed(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            output, _, invoke = self.run_fixture(Path(temp), post_failure=True)
            self.assertEqual(invoke(), 2)
            self.assertEqual(json.loads((output / "openfoam-execution.json").read_text())["status"], "NATIVE_STAGE_CORRECTION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
