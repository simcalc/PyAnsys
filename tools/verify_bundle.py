"""Extract the generated release ZIP and exercise its real offline installer."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
import zipfile

from build_bundle import digest


def validate_member(info):
    name = PurePosixPath(info.filename)
    if name.is_absolute() or ".." in name.parts or "\\" in info.filename:
        raise ValueError("Unsafe ZIP member path")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ValueError("ZIP symlink is prohibited")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.directory / "wheel-manifest.json").read_text())
    original = json.loads((args.directory / "sdk-verification.json").read_text())
    bundle = args.directory / manifest["bundleFilename"]
    with tempfile.TemporaryDirectory(prefix="pyfluent-offline-", dir=os.environ.get("RUNNER_TEMP")) as folder:
        root = Path(folder)
        with zipfile.ZipFile(bundle) as archive:
            for info in archive.infolist():
                validate_member(info)
            archive.extractall(root)
        subprocess.run([sys.executable, "-B", str(root / "install_offline.py")], check=True)
        result_path = root / "offline-sdk-check.json"
        subprocess.run([str(root / ".venv" / "bin" / "python"), "-B",
                        str(Path(__file__).with_name("verify_sdk.py")), "--output", str(result_path)], check=True)
        result = json.loads(result_path.read_text())
        if result["installedDistributions"] != original["installedDistributions"]:
            raise ValueError("Offline installed distribution versions differ from original SDK verification")
    report = {"schemaVersion": "simcalc.pyfluent-offline-verification.v1",
              "offlineInstallation": "VERIFIED", "sdkVersion": result["sdkVersion"],
              "publicApiImportChecks": result["publicApiImportChecks"],
              "dependencyCheck": result["dependencyCheck"],
              "installedDistributionInventoryMatches": True,
              "bundleFilename": bundle.name, "bundleSha256": digest(bundle),
              "wheelManifestSha256": digest(args.directory / "wheel-manifest.json"),
              "githubRunId": result["githubRunId"], "githubCommit": result["githubCommit"],
              "networkRequiredForPackageInstallation": False,
              "simulationExecuted": False, "mainApplicationWrites": False}
    (args.directory / "offline-verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    assets = sorted(p for p in args.directory.iterdir() if p.is_file() and p.name != "SHA256SUMS")
    (args.directory / "SHA256SUMS").write_text("".join(f"{digest(p)}  {p.name}\n" for p in assets), encoding="utf-8")
    print(json.dumps(report))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write("\nOffline ZIP extraction, fresh environment installation, API imports, dependency check and exact version inventory: **VERIFIED**.\n")


if __name__ == "__main__":
    main()
