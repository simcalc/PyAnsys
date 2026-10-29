"""Publish per-run, non-secret installation/preflight evidence; preserve old releases."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

REPOSITORY = "simcalc/PyAnsys"


def publication_identity(context, report):
    if context.get("GITHUB_REPOSITORY") != REPOSITORY or context.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise ValueError("Only the manually dispatched independent repository may publish")
    run = context.get("GITHUB_RUN_ID", "")
    if not re.fullmatch(r"[1-9][0-9]*", run) or not re.fullmatch(r"[0-9a-f]{40}", context.get("GITHUB_SHA", "")):
        raise ValueError("Require an exact run and source commit")
    if report.get("githubRunId") != run or report.get("githubCommit") != context["GITHUB_SHA"] or report.get("githubRepository") != REPOSITORY:
        raise ValueError("Refuse stale, cross-run or cross-repository evidence")
    if report.get("sdkInstallationVerified") is not True or any(report.get(key) is not False for key in (
        "solverExecuted", "physicalPumpValidated", "persistentServerProvisioned", "cloudProductionReady", "mainApplicationWrites",
        "licenseVerified", "fluentSessionConnected")):
        raise ValueError("This publication accepts SDK/preflight observations only")
    return "pyfluent-cloud-preflight-" + run


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False, timeout=120)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-report", type=Path, required=True)
    parser.add_argument("--cloud-report", type=Path, required=True)
    args = parser.parse_args()
    cloud_raw, sdk_raw = args.cloud_report.read_bytes(), args.sdk_report.read_bytes()
    cloud = json.loads(cloud_raw)
    tag = publication_identity(os.environ, cloud)
    if hashlib.sha256(sdk_raw).hexdigest() != cloud["sdkReportSha256"]:
        raise ValueError("SDK report no longer matches the preflight record")
    with tempfile.TemporaryDirectory(prefix="pyfluent-preflight-publication-") as directory:
        root = Path(directory)
        (root / "sdk-verification.json").write_bytes(sdk_raw)
        (root / "cloud-readiness.json").write_bytes(cloud_raw)
        names = ("cloud-readiness.json", "sdk-verification.json")
        (root / "SHA256SUMS").write_text("".join(hashlib.sha256((root/name).read_bytes()).hexdigest() + "  " + name + "\n" for name in names), encoding="utf-8")
        prior = gh("release", "view", tag, "--repo", REPOSITORY, "--json", "url")
        if prior.returncode != 0:
            if "not found" not in prior.stderr.lower() and "404" not in prior.stderr:
                raise RuntimeError("Unable to inspect the per-run release; no publication attempted")
            notes = root / "notes.txt"
            notes.write_text("Actual PyFluent SDK installation and native-runtime preflight for this source commit and run. No Fluent solver or persistent service is supplied. License/session and CFD execution remain unverified. Existing software releases are preserved.\n", encoding="utf-8")
            created = gh("release", "create", tag, "--repo", REPOSITORY, "--target", os.environ["GITHUB_SHA"],
                         "--title", "PyFluent SDK installation and runtime preflight " + os.environ["GITHUB_RUN_ID"],
                         "--notes-file", str(notes), "--latest=false", *(str(root/name) for name in (*names, "SHA256SUMS")))
            if created.returncode:
                raise RuntimeError("Preflight release creation did not complete; inspect the exact per-run tag")
        downloaded = root / "downloaded"
        downloaded.mkdir()
        result = gh("release", "download", tag, "--repo", REPOSITORY, "--dir", str(downloaded))
        if result.returncode or {p.name for p in downloaded.iterdir()} != {*names, "SHA256SUMS"}:
            raise RuntimeError("Published preflight inventory verification failed; no assets replaced")
        for name in (*names, "SHA256SUMS"):
            if (downloaded/name).read_bytes() != (root/name).read_bytes():
                raise ValueError("Published preflight bytes disagree; existing evidence remains preserved")
    url = f"https://github.com/{REPOSITORY}/releases/tag/{tag}"
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
            stream.write(f"\nPer-run evidence and exact downloaded bytes verified: {url}\n")
    print(json.dumps({"releaseUrl": url, "publishedBytesVerified": True, "solverExecuted": False, "cloudProductionReady": False}))


if __name__ == "__main__":
    main()
