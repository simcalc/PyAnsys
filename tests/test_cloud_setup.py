"""Installation-only evidence must never imply a licensed hosted CFD service."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"tools"))
from verify_sdk import API_CHECKS
from verify_cloud_setup import build_report, summary
from publish_cloud_preflight import publication_identity


class CloudSetupTests(unittest.TestCase):
    def setUp(self):
        self.context = {"GITHUB_REPOSITORY": "simcalc/PyAnsys", "GITHUB_SHA": "a"*40,
                        "GITHUB_RUN_ID": "123", "GITHUB_EVENT_NAME": "workflow_dispatch"}
        self.sdk = {"sdkInstallation": "VERIFIED", "sdkVersion": "0.42.2", "dependencyCheck": "passed",
                    "publicApiImportChecks": dict.fromkeys(API_CHECKS, True), "simulationExecuted": False,
                    "githubRepository": "simcalc/PyAnsys", "githubCommit": "a"*40, "githubRunId": "123"}

    def report(self, **kwargs):
        return build_report(self.sdk, source_sha256="b"*64, context=self.context, native_detected=kwargs.get("native", False))

    def test_verified_sdk_does_not_supply_native_solver_or_online_backend(self):
        report = self.report()
        self.assertTrue(report["sdkInstallationVerified"])
        for key in ("nativeRuntimeDetected", "nativeRuntimeInstalledByWorkflow", "licenseVerified", "fluentSessionConnected", "solverExecuted", "physicalPumpValidated", "persistentServerProvisioned", "cloudProductionReady"):
            self.assertIs(report[key], False)
        self.assertIn("FLUENT_NATIVE_RUNTIME_REQUIRED", report["reasonCodes"])

    def test_detected_executable_never_proves_license_or_session(self):
        report = self.report(native=True)
        self.assertTrue(report["nativeRuntimeDetected"])
        self.assertFalse(report["licenseVerified"])
        self.assertFalse(report["cloudProductionReady"])
        self.assertNotIn("FLUENT_NATIVE_RUNTIME_REQUIRED", report["reasonCodes"])
        self.assertIn("FLUENT_LICENSE_SESSION_NOT_VERIFIED", report["reasonCodes"])

    def test_failed_import_and_stale_evidence_are_rejected(self):
        for key, value in (("sdkInstallation", "FAILED"), ("dependencyCheck", "failed"), ("githubCommit", "c"*40),
                           ("githubRunId", "124"), ("githubRepository", "elsewhere/other"), ("simulationExecuted", True)):
            with self.subTest(key=key):
                sdk = {**self.sdk, key: value}
                with self.assertRaises(ValueError):
                    build_report(sdk, source_sha256="b"*64, context=self.context, native_detected=False)
        self.sdk["publicApiImportChecks"] = dict.fromkeys(API_CHECKS, False)
        with self.assertRaises(ValueError):
            self.report()

    def test_context_secret_values_are_never_serialized(self):
        self.context.update(GH_TOKEN="synthetic-secret", ANSYSLMD_LICENSE_FILE="private-license-endpoint", SECRET="synthetic-second-secret")
        raw = json.dumps(self.report())
        for value in ("synthetic-secret", "private-license-endpoint", "synthetic-second-secret"):
            self.assertNotIn(value, raw)
        self.assertNotIn("GH_TOKEN", raw)

    def test_publication_rejects_cross_run_and_promoted_claims(self):
        self.assertEqual(publication_identity(self.context, self.report()), "pyfluent-cloud-preflight-123")
        for key, value in (("githubRunId", "124"), ("solverExecuted", True), ("cloudProductionReady", True),
                           ("licenseVerified", True), ("fluentSessionConnected", True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                publication_identity(self.context, {**self.report(), key: value})

    def test_summary_distinguishes_all_execution_gates(self):
        text = summary(self.report())
        for phrase in ("Verified", "Not detected", "Not verified", "Not run", "Not provisioned", "SDK check only"):
            self.assertIn(phrase, text)
        self.assertIn("https://fluent.docs.pyansys.com/", text)


if __name__ == "__main__":
    unittest.main()
