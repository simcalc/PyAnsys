"""No SDK or Fluent installation is needed for these isolation/integrity tests."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from public_runner_guard import REPOSITORY, validate_context
from install_offline import isolated_environment, verify_wheels
from verify_sdk import API_CHECKS, make_report


class Contracts(unittest.TestCase):
    def setUp(self):
        self.env = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
                    "GITHUB_REPOSITORY": REPOSITORY, "RUNNER_OS": "Linux",
                    "RUNNER_ENVIRONMENT": "github-hosted"}
        self.repo = {"full_name": REPOSITORY, "private": False, "visibility": "public"}

    def test_accepts_exact_public_hosted_context(self):
        result = validate_context(self.env, {"repository": self.repo}, self.repo)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["paidFallback"])

    def test_rejects_private_repository(self):
        with self.assertRaises(ValueError):
            validate_context(self.env, {"repository": self.repo}, {**self.repo, "private": True})

    def test_rejects_other_repository_or_runner(self):
        for key, bad in (("GITHUB_REPOSITORY", "simcalc/npsh"),
                         ("RUNNER_ENVIRONMENT", "self-hosted"),
                         ("GITHUB_EVENT_NAME", "push")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_context({**self.env, key: bad}, {"repository": self.repo}, self.repo)

    def test_sdk_success_does_not_claim_cfd_success(self):
        report = make_report("0.42.2", dict.fromkeys(API_CHECKS, True), [], 123, 100, "passed")
        self.assertEqual(report["sdkInstallation"], "VERIFIED")
        self.assertIs(report["sdkReady"], True)
        self.assertIs(report["solverAvailable"], False)
        for key in ("simulationExecuted", "actualCfdRunVerified", "solverLicenseVerified",
                    "fluentSessionConnected", "mainApplicationWrites"):
            self.assertIs(report[key], False)

    def test_rejects_wrong_sdk_or_missing_api(self):
        for version, api in (("0.42.1", dict.fromkeys(API_CHECKS, True)), ("0.42.2", {"import": False}), ("0.42.2", {})):
            with self.assertRaises(ValueError):
                make_report(version, api, [], 0, 0, "passed")

    def test_offline_integrity_rejects_tamper_and_extra_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "wheelhouse").mkdir()
            wheel = root / "wheelhouse" / "fixture.whl"
            wheel.write_bytes(b"fixture")
            manifest = {"wheels": [{"filename": wheel.name, "bytes": 7,
                                    "sha256": hashlib.sha256(b"fixture").hexdigest()}]}
            verify_wheels(root, manifest)
            wheel.write_bytes(b"altered")
            with self.assertRaises(ValueError):
                verify_wheels(root, manifest)
            wheel.write_bytes(b"fixture")
            (root / "wheelhouse" / "extra.whl").write_bytes(b"extra")
            with self.assertRaises(ValueError):
                verify_wheels(root, manifest)

    def test_offline_installation_cannot_inherit_external_pip_target(self):
        env = isolated_environment({"PIP_TARGET": "outside", "PIP_PREFIX": "outside",
                                    "PYTHONPATH": "outside", "PYTHONHOME": "outside",
                                    "PATH": "preserved"})
        for key in ("PIP_TARGET", "PIP_PREFIX", "PYTHONPATH", "PYTHONHOME"):
            self.assertNotIn(key, env)
        self.assertEqual(env["PATH"], "preserved")


if __name__ == "__main__":
    unittest.main()
