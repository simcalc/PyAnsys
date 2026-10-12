"""Current byte, process and claim gates; synthetic fixtures are not CFD evidence."""
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"tools"))
import run_teaching_pump_sliding as runner


class SlidingRunnerGates(unittest.TestCase):
    def test_only_exact_current_command_and_integer_stage_reach_executor(self):
        root=Path("SYNTHETIC-FRESH-PILOT")
        with patch.object(runner.phase,"execute", return_value=({"synthetic":True},"")) as child:
            runner.execute(runner.commands(root)[0], root, 1000, {}, 0)
            for args,index in ((["foamRun","-case","other"],1),(runner.commands(root)[0],1),
                (runner.commands(root)[0],True),(runner.commands(root)[0],-1)):
                with self.assertRaises(ValueError): runner.execute(args,root,1000,{},index)
            self.assertEqual(child.call_count,1)
        self.assertEqual(runner.LIMITS,runner.phase.LIMITS)
        self.assertEqual(runner.LIMITS["maximumWorkspaceBytes"],1024**3)

    def fixture_inputs(self,root):
        entries={runner.preparation.REPORT:b'{"syntheticFixture":true}',runner.RUNTIME:b'{"syntheticFixture":true}',
            runner.phase.IDENTITY:b'{"syntheticFixture":true}',"source-reference/role.json":b'{"syntheticFixture":true}',
            "case/0/U":b'SYNTHETIC-NOT-NATIVE',"case/constant/polyMesh/points":b'SYNTHETIC-NOT-NATIVE'}
        for path,raw in entries.items():
            file=root/path;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(raw)
        prepared={"sourceEvidenceFileRecords":[runner.record(root,"source-reference/role.json")[1]],
            "authoredInputFiles":[{**runner.record(root,"case/0/U")[1],"path":"0/U"}],
            "preservedNativeMeshFiles":[{**runner.record(root,"case/constant/polyMesh/points")[1],"path":"constant/polyMesh/points"}],
            "motionPlan":{"syntheticFixture":True},"temporalPlan":{"syntheticFixture":True}}
        verified={"reportFile":runner.record(root,runner.preparation.REPORT)[1]}
        return prepared,verified

    def test_input_manifest_binds_whole_source_and_mesh_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepared,verified=self.fixture_inputs(root)
            rows=runner.input_inventory(root,prepared,verified)
            packet=json.loads((root/runner.MANIFEST).read_bytes())
            self.assertEqual(rows[runner.MANIFEST]["sha256"],sha256((root/runner.MANIFEST).read_bytes()).hexdigest())
            self.assertEqual({r["path"] for r in packet["files"]},set(rows)-{runner.MANIFEST})
            self.assertFalse(packet["calculatedResultsSupplied"])
            self.assertFalse(packet["actualCfdRunVerified"])
            self.assertFalse(packet["mainApplicationWrites"])

    def test_changed_source_mesh_or_initial_field_cannot_enter_manifest(self):
        for path in ("source-reference/role.json","case/0/U","case/constant/polyMesh/points"):
            with self.subTest(path=path),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);prepared,verified=self.fixture_inputs(root);(root/path).write_bytes(b'CHANGED')
                with self.assertRaises(ValueError): runner.input_inventory(root,prepared,verified)
                self.assertFalse((root/runner.MANIFEST).exists())

    def test_ambiguous_duplicate_scientific_role_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prepared,verified=self.fixture_inputs(root)
            prepared["sourceEvidenceFileRecords"].append(prepared["sourceEvidenceFileRecords"][0])
            with self.assertRaises(ValueError): runner.input_inventory(root,prepared,verified)

    def test_motion_library_current_bytes_and_package_ownership_are_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/"libfvMotionSolvers.so";path.write_bytes(b'SYNTHETIC-NOT-AN-EXECUTABLE')
            reply=SimpleNamespace(stdout="openfoam12: "+str(path.resolve()),stderr="")
            with patch.dict(os.environ,{"FOAM_LIBBIN":str(root)}),patch.object(Path,"is_relative_to",return_value=True),\
                 patch.object(runner.subprocess,"run",return_value=reply):
                row=runner.motion_library_identity(runner.time.monotonic()+10)
                runner.verify_motion_library(row)
                self.assertEqual(row["sha256"],sha256(path.read_bytes()).hexdigest())
                self.assertFalse(row["allLoadedLibrariesVerified"])
                path.write_bytes(b'SYNTHETIC-MUTATION')
                with self.assertRaises(ValueError): runner.verify_motion_library(row)

    def test_foreign_motion_library_owner_or_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/"libfvMotionSolvers.so").write_bytes(b'SYNTHETIC')
            with patch.dict(os.environ,{"FOAM_LIBBIN":str(root)}):
                with self.assertRaises(ValueError): runner.motion_library_identity(runner.time.monotonic()+10)
            with patch.dict(os.environ,{"FOAM_LIBBIN":str(root)}),patch.object(Path,"is_relative_to",return_value=True),\
                 patch.object(runner.subprocess,"run",return_value=SimpleNamespace(stdout="foreign-package: library",stderr="")):
                with self.assertRaises(ValueError): runner.motion_library_identity(runner.time.monotonic()+10)

    def test_cli_pending_physical_science_and_native_diagnostic_remain_visible(self):
        keys=("nativeSolverCompleted","simulationExecuted","actualCfdRunVerified","coordinateMotionVerified",
            "dynamicNccClockCoverageVerified","numericalTransientQualified","restartVerified","mainApplicationWrites")
        report=dict.fromkeys(keys,False);report.update(status="TEACHING_SLIDING_PILOT_CORRECTION_REQUIRED",diagnostic={"message":"SYNTHETIC missing actual coordinate evidence"})
        with patch.object(sys,"argv",["pilot","--reference-directory","reference","--output-directory","fresh"]),\
             patch.object(runner,"run",return_value=report),redirect_stdout(io.StringIO()) as out:
            self.assertEqual(runner.main(),2)
        result=json.loads(out.getvalue());self.assertEqual(result["diagnostic"],report["diagnostic"])
        self.assertFalse(result["numericalTransientQualified"]);self.assertFalse(result["restartVerified"])


if __name__ == "__main__": unittest.main()
