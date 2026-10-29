"""Record real SDK verification and separately observed native runtime presence.

No Fluent launch, connection, license checkout, listener, tunnel or secret read.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil

from verify_sdk import SDK_VERSION, API_CHECKS
from public_runner_guard import REPOSITORY

DOCS = "https://fluent.docs.pyansys.com/version/stable/getting_started/installation.html"


def build_report(sdk, *, source_sha256, context, native_detected):
    if context.get("GITHUB_REPOSITORY") != REPOSITORY or not re.fullmatch(r"[0-9a-f]{40}", context.get("GITHUB_SHA", "")):
        raise ValueError("Require the reviewed repository and exact source commit")
    if not re.fullmatch(r"[1-9][0-9]*", context.get("GITHUB_RUN_ID", "")):
        raise ValueError("Require an actual GitHub Actions run identity")
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ValueError("Bind the exact SDK verification bytes")
    if sdk.get("sdkInstallation") != "VERIFIED" or sdk.get("sdkVersion") != SDK_VERSION or sdk.get("dependencyCheck") != "passed":
        raise ValueError("A failed or different SDK installation cannot pass cloud preflight")
    if set(sdk.get("publicApiImportChecks", {})) != API_CHECKS or not all(v is True for v in sdk["publicApiImportChecks"].values()):
        raise ValueError("Require the actual public API imports")
    for key in ("githubRepository", "githubCommit", "githubRunId"):
        expected = context[{"githubRepository": "GITHUB_REPOSITORY", "githubCommit": "GITHUB_SHA", "githubRunId": "GITHUB_RUN_ID"}[key]]
        if str(sdk.get(key)) != expected:
            raise ValueError("SDK evidence belongs to a different repository, commit or run")
    if sdk.get("simulationExecuted") is not False:
        raise ValueError("Only the installation-only evidence contract is accepted")
    if type(native_detected) is not bool:
        raise ValueError("Native runtime presence must be an actual boolean observation")
    reasons = [] if native_detected else ["FLUENT_NATIVE_RUNTIME_REQUIRED"]
    reasons.extend(["FLUENT_LICENSE_SESSION_NOT_VERIFIED", "PERSISTENT_WORKER_NOT_PROVISIONED"])
    return {"schemaVersion": "simcalc.pyfluent-cloud-preflight.v1",
            "status": "SDK_VERIFIED_NATIVE_EXECUTION_UNVERIFIED", "acceptanceScope": "Bounded repository SDK installation and runtime-presence preflight",
            "sdkInstallationVerified": True, "sdkVersion": SDK_VERSION, "dependencyCheck": "passed",
            "sdkReportSha256": source_sha256, "nativeRuntimeDetected": native_detected,
            "nativeRuntimeDetectionScope": "Executable on PATH or under /usr/ansys_inc/v*/fluent/bin/fluent; other locations and remote workers are not inspected",
            "nativeRuntimeInstalledByWorkflow": False, "licenseVerified": False, "fluentSessionConnected": False,
            "solverExecuted": False, "physicalPumpValidated": False, "persistentServerProvisioned": False,
            "cloudProductionReady": False, "mainApplicationWrites": False, "reasonCodes": reasons,
            "githubRepository": REPOSITORY, "githubCommit": context["GITHUB_SHA"], "githubRunId": context["GITHUB_RUN_ID"],
            "githubRunUrl": f'https://github.com/{REPOSITORY}/actions/runs/{context["GITHUB_RUN_ID"]}',
            "officialInstallationDocumentation": DOCS,
            "hostingScope": "Actions development/testing only; no production CFD endpoint",
            "correctiveAction": "Provide an installed compatible licensed Fluent worker and verify a real session, or use a separately verified OpenFOAM worker. GitHub Pages supplies static presentation only."}


def summary(report):
    native = "Detected; session unverified" if report["nativeRuntimeDetected"] else "Not detected"
    return ("\n## PyFluent installation and online execution boundary\n\n"
            "| Check | Observed result |\n| --- | --- |\n"
            f'| PyFluent {report["sdkVersion"]} and dependency check | Verified |\n'
            f"| Native Fluent runtime | {native} |\n"
            "| Fluent license / connected session | Not verified |\n"
            "| CFD simulation | Not run |\n"
            "| Persistent public CFD backend | Not provisioned |\n\n"
            f'SDK evidence SHA-256: `{report["sdkReportSha256"]}`. '
            "A successful installation job establishes the SDK check only.\n\n"
            f"[Official installation requirements]({DOCS})\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.sdk_report.read_bytes()
    native = bool(shutil.which("fluent")) or any(path.is_file() and os.access(path, os.X_OK)
        for path in Path("/usr/ansys_inc").glob("v*/fluent/bin/fluent"))
    # Environment values are allowlisted by build_report. License values,
    # credentials, executable locations and endpoint addresses are not read.
    report = build_report(json.loads(raw), source_sha256=hashlib.sha256(raw).hexdigest(), context=os.environ, native_detected=native)
    report.update(verifiedAtUtc=datetime.now(timezone.utc).isoformat(), pythonVersion=platform.python_version(), platform=platform.system())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
            stream.write(summary(report))
    print(json.dumps({key: report[key] for key in ("status", "sdkInstallationVerified", "nativeRuntimeDetected", "licenseVerified", "cloudProductionReady")}))


if __name__ == "__main__":
    main()
