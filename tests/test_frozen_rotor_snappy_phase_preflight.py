"""Synthetic source-metadata admission checks; never actual CFD evidence."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("snappy_phase_preflight_tests", Path(__file__).resolve().parents[1] / "tools/frozen_rotor_snappy_phase_preflight.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class MetadataAdmission(unittest.TestCase):
    def fixture(self):
        inputs = tool.validate_inputs({"BASELINE_RUN_ID": "123456789"})
        repository = {"full_name": "simcalc/PyAnsys", "private": False}
        run = {"id": 123456789, "status": "completed", "conclusion": "success", "event": "workflow_dispatch",
            "path": tool.SOURCE_WORKFLOW, "head_sha": "a" * 40,
            "repository": dict(repository), "head_repository": dict(repository)}
        artifacts = {"artifacts": [{"id": 7654321, "name": tool.ARTIFACT_PREFIX + "123456789", "expired": False,
            "size_in_bytes": 114205200, "workflow_run": {"id": 123456789}, "digest": "sha256:" + "b" * 64}]}
        return inputs, run, artifacts

    def test_actual_metadata_does_not_inherit_solver_or_reference_acceptance(self):
        inputs, run, artifacts = self.fixture()
        result = tool.validate_source(inputs, run, artifacts)
        self.assertEqual(result["sourceCommit"], "a" * 40)
        self.assertEqual(result["pressureAbsolutePa"], 300000)
        self.assertEqual(result["githubReportedArchiveDigest"], "sha256:" + "b" * 64)
        self.assertEqual(result["maximumTimeStepS"], (60 / 1450) / 360)
        self.assertEqual(result["durationS"], 4 * 60 / 1450)
        for key in ("sourceNumericalBaselineQualified", "artifactBytesVerified", "actualCfdRunVerified",
                "phaseFamilyReferenceQualified", "cavitationSweepExecuted", "physicalTransientExecuted", "npshrCalculated", "mainApplicationWrites"):
            self.assertIs(result[key], False)

    def test_run_id_or_changed_physics_contract_rejects_before_download(self):
        for value in ("", "0", "-1", "../Main", "123\n", "1;run", "1" * 20, True, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                tool.validate_inputs({"BASELINE_RUN_ID": value})
        for key, value in (("mode", "transient-sliding"), ("pressureAbsolutePa", 101325), ("durationS", .01),
                ("sourceNumericalBaselineQualified", True), ("mainApplicationWrites", True)):
            inputs, run, artifacts = self.fixture()
            inputs[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                tool.validate_source(inputs, run, artifacts)

    def test_old_failed_stale_wrong_or_private_source_is_rejected(self):
        for key, value in (("id", True), ("id", 444), ("status", "in_progress"), ("conclusion", "failure"),
                ("event", "pull_request"), ("path", ".github/workflows/original-frozen-rotor.yml"),
                ("head_sha", "a" * 39)):
            inputs, run, artifacts = self.fixture()
            run[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                tool.validate_source(inputs, run, artifacts)
        for key in ("repository", "head_repository"):
            for row in ({"full_name": "simcalc/npsh", "private": True}, {"full_name": "simcalc/PyAnsys", "private": True}, None):
                inputs, run, artifacts = self.fixture()
                run[key] = row
                with self.subTest(key=key, row=row), self.assertRaises(ValueError):
                    tool.validate_source(inputs, run, artifacts)

    def test_archive_metadata_is_exact_bounded_and_not_a_byte_verification_claim(self):
        for change in ("missing", "duplicate", "expired", "zero", "oversize", "bool-size", "wrong-run", "bool-id", "digest", "old-artifact"):
            inputs, run, artifacts = self.fixture()
            row = artifacts["artifacts"][0]
            if change == "missing": artifacts["artifacts"] = []
            elif change == "duplicate": artifacts["artifacts"].append(dict(row))
            elif change == "expired": row["expired"] = True
            elif change == "zero": row["size_in_bytes"] = 0
            elif change == "oversize": row["size_in_bytes"] = tool.MAX_ARTIFACT_BYTES + 1
            elif change == "bool-size": row["size_in_bytes"] = True
            elif change == "wrong-run": row["workflow_run"]["id"] = 555
            elif change == "bool-id": row["id"] = True
            elif change == "digest": row["digest"] = "sha256:wrong"
            else: row["name"] = "frozen-rotor-original-123456789"
            with self.subTest(change=change), self.assertRaises(ValueError):
                tool.validate_source(inputs, run, artifacts)

    def test_metadata_reads_reject_missing_or_oversized_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError): tool.read_json(root / "absent.json")
            path = root / "oversized.json"
            path.write_bytes(b" " * (tool.MAX_METADATA_BYTES + 1))
            with self.assertRaises(ValueError): tool.read_json(path)


if __name__ == "__main__":
    unittest.main()
