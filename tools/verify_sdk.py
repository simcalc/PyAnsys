"""Verify SDK imports and installed dependencies; never launch a solver."""
import argparse
from datetime import datetime, timezone
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

SDK_VERSION = "0.42.2"
API_CHECKS = {"ansys.fluent.core", "launch_fluentCallable", "connect_to_fluentCallable"}


def make_report(version, api, packages, installed_bytes, sdk_bytes, pip_check):
    if version != SDK_VERSION or set(api) != API_CHECKS or not all(value is True for value in api.values()) or pip_check != "passed":
        raise ValueError("SDK version, public API imports or dependency check failed")
    return {
        "schemaVersion": "simcalc.pyfluent-sdk-verification.v1",
        "acceptanceScope": "SDK installation and import only",
        "sdkInstallation": "VERIFIED", "sdkVersion": version,
        "sdkReady": True, "solverAvailable": False,
        "solverAvailabilityBasis": "native runtime and licensed solver session not supplied or tested",
        "publicApiImportChecks": api, "dependencyCheck": pip_check,
        "installedDistributions": packages,
        "installedSdkBytes": sdk_bytes,
        "installedSdkAndDependenciesBytes": installed_bytes,
        "installedSizeBasis": "unique installed distribution files; excludes pip and Python runtime",
        "solverLicenseVerified": False, "fluentSessionConnected": False,
        "actualCfdRunVerified": False, "simulationExecuted": False,
        "mainApplicationWrites": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Imports expose the client API but do not connect to Fluent.
    import ansys.fluent.core as pyfluent
    check = subprocess.run([sys.executable, "-m", "pip", "check"],
                           capture_output=True, text=True, check=True)
    files, sdk_files, packages = set(), set(), []
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name.lower() == "pip":
            continue
        packages.append({"name": name, "version": dist.version})
        located = {Path(dist.locate_file(item)).resolve() for item in dist.files or []}
        existing = {p for p in located if p.is_file()}
        files.update(existing)
        if name.lower().replace("_", "-") == "ansys-fluent-core":
            sdk_files.update(existing)
    report = make_report(metadata.version("ansys-fluent-core"), {
        "ansys.fluent.core": pyfluent is not None,
        "launch_fluentCallable": callable(getattr(pyfluent, "launch_fluent", None)),
        "connect_to_fluentCallable": callable(getattr(pyfluent, "connect_to_fluent", None)),
    }, sorted(packages, key=lambda row: row["name"].lower()),
        sum(p.stat().st_size for p in files), sum(p.stat().st_size for p in sdk_files),
        "passed" if check.returncode == 0 else "failed")
    report.update({
        "verifiedAtUtc": datetime.now(timezone.utc).isoformat(),
        "pythonVersion": platform.python_version(), "platform": platform.system(),
        "architecture": platform.machine(),
        "nativeFluentExecutableOnPath": bool(shutil.which("fluent")),
        "nativeFluentInstallation": "NOT_INSTALLED_BY_THIS_WORKFLOW",
        "githubRepository": os.environ.get("GITHUB_REPOSITORY"),
        "githubCommit": os.environ.get("GITHUB_SHA"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in (
        "sdkInstallation", "sdkVersion", "dependencyCheck",
        "installedSdkAndDependenciesBytes", "simulationExecuted", "mainApplicationWrites")}))


if __name__ == "__main__":
    main()
