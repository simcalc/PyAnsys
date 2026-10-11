"""Publish SDK software; verify actual public downloads and preserve prior assets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request

REPOSITORY = "simcalc/PyAnsys"
TAG = "pyfluent-sdk-0.42.2-cpython312-linux-x64"


def gh(*arguments):
    return subprocess.run(["gh", *arguments], capture_output=True, text=True)


def checksum_file(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def verify_public_assets(release, recover_draft=False):
    if (release["draft"] and not recover_draft) or release["prerelease"]:
        raise ValueError("Expected a published stable software release")
    with tempfile.TemporaryDirectory(prefix="public-sdk-check-") as folder:
        root = Path(folder)
        if release["draft"]:
            downloaded = gh("release", "download", TAG, "--repo", REPOSITORY, "--dir", str(root))
            if downloaded.returncode:
                raise ValueError("Draft assets are incomplete; existing assets and tag are preserved. Inspect and restore the exact missing assets before retrying publication.")
        for asset in release["assets"]:
            name = asset["name"]
            if Path(name).name != name or asset["size"] > 512 * 1024 * 1024:
                raise ValueError("Unsafe or oversized release asset")
            if release["draft"]:
                if not (root / name).is_file() or (root / name).stat().st_size != asset["size"]:
                    raise ValueError("Incomplete draft asset")
                continue
            request = urllib.request.Request(asset["browser_download_url"],
                                           headers={"User-Agent": "PyAnsys-public-release-verification"})
            # Deliberately anonymous: no GH_TOKEN or authenticated URL sent.
            with urllib.request.urlopen(request, timeout=60) as response, (root / name).open("wb") as target:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > asset["size"]:
                        raise ValueError("Asset response exceeds declared size")
                    target.write(chunk)
            if size != asset["size"]:
                raise ValueError("Incomplete release asset download")
        expected = {}
        for line in (root / "SHA256SUMS").read_text().splitlines():
            checksum, name = line.split("  ", 1)
            if Path(name).name != name or name in expected or len(checksum) != 64:
                raise ValueError("Invalid checksum inventory")
            expected[name] = checksum
        if set(expected) != {p.name for p in root.iterdir()} - {"SHA256SUMS"}:
            raise ValueError("Release checksum inventory is incomplete")
        for name, checksum in expected.items():
            if checksum_file(root / name) != checksum:
                raise ValueError(f"Release download checksum mismatch: {name}")
        manifest = json.loads((root / "wheel-manifest.json").read_text())
        allowed = {manifest["bundleFilename"], manifest["sdkWheelFilename"], "wheel-manifest.json",
                   "requirements-resolved.txt", "sdk-verification.json", "offline-verification.json", "INSTALL.txt", "SHA256SUMS"}
        if {p.name for p in root.iterdir()} != allowed:
            raise ValueError("Release assets differ from the exact SDK allowlist")
        report = json.loads((root / "sdk-verification.json").read_text())
        offline = json.loads((root / "offline-verification.json").read_text())
        if manifest["sdkVersion"] != "0.42.2" or report["sdkInstallation"] != "VERIFIED" or report["simulationExecuted"] is not False:
            raise ValueError("Release evidence scope/version mismatch")
        if offline["offlineInstallation"] != "VERIFIED" or offline["bundleSha256"] != checksum_file(root / manifest["bundleFilename"]):
            raise ValueError("Actual offline installation evidence does not bind this release ZIP")
        if offline["wheelManifestSha256"] != checksum_file(root / "wheel-manifest.json"):
            raise ValueError("Offline installation evidence does not bind this public wheel manifest")
        sdk = [r for r in manifest["wheels"] if r["filename"] == manifest["sdkWheelFilename"]][0]
        if checksum_file(root / sdk["filename"]) != sdk["sha256"]:
            raise ValueError("Original SDK wheel digest mismatch")
    return {"publicDownloadVerified": not release["draft"], "assetChecksumsVerified": True,
            "releaseUrl": release["html_url"], "actualCfdRunVerified": False,
            "mainApplicationWrites": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("Publication is restricted to the independent public repository")
    prior = gh("api", f"repos/{REPOSITORY}/releases/tags/{TAG}")
    if prior.returncode == 0:
        release = json.loads(prior.stdout)
        if release["draft"]:
            try:
                verify_public_assets(release, recover_draft=True)
            except (ValueError, KeyError, FileNotFoundError) as error:
                raise RuntimeError("Existing draft is incomplete or inconsistent; no assets were replaced. Inspect the exact draft inventory and restore its missing original assets before publication.") from error
            recovered = gh("release", "edit", TAG, "--repo", REPOSITORY, "--draft=false")
            if recovered.returncode:
                raise RuntimeError("Verified complete draft remains unpublished; retry the publish action")
            current = gh("api", f"repos/{REPOSITORY}/releases/tags/{TAG}")
            if current.returncode:
                raise RuntimeError("Recovered release lookup failed")
            release = json.loads(current.stdout)
        print("Existing published assets are preserved; verifying their public checksums.")
    else:
        if "404" not in prior.stderr:
            raise RuntimeError("Unable to inspect release; no publication attempted")
        names = {p.name for p in args.directory.iterdir() if p.is_file()}
        manifest = json.loads((args.directory / "wheel-manifest.json").read_text())
        allowed = {manifest["bundleFilename"], manifest["sdkWheelFilename"], "wheel-manifest.json",
                   "requirements-resolved.txt", "sdk-verification.json", "offline-verification.json", "INSTALL.txt", "SHA256SUMS"}
        if names != allowed:
            raise ValueError("Release asset allowlist differs from generated inventory")
        notes = args.directory.parent / "pyfluent-release-notes.md"
        notes.write_text(
            "Verified installation of PyFluent SDK 0.42.2 and every required dependency on a FREE standard public GitHub runner.\n\n"
            "Download the Linux x64 / CPython 3.12 ZIP for offline installation, or the original upstream SDK wheel. "
            "SHA256SUMS covers all assets; wheel-manifest.json verifies every upstream PyPI checksum. "
            "sdk-verification.json contains the actual import, dependency and size evidence.\n\n"
            "The source ZIP contains setup code. The software bundle contains the actual SDK and dependency wheels. "
            "Python itself and the licensed Ansys Fluent solver are separate. No CFD simulation was run. "
            "The NPSH Main application and its data are not included.\n", encoding="utf-8")
        result = gh("release", "create", TAG, "--repo", REPOSITORY, "--target", os.environ["GITHUB_SHA"],
                    "--draft", "--title", "PyFluent 0.42.2 SDK · Linux x64 / CPython 3.12",
                    "--notes-file", str(notes), *(str(args.directory / n) for n in sorted(allowed)))
        if result.returncode:
            raise RuntimeError("Release creation failed; inspect the GitHub release without exposing credentials")
        result = gh("release", "edit", TAG, "--repo", REPOSITORY, "--draft=false")
        if result.returncode:
            raise RuntimeError("Release publication failed")
        current = gh("api", f"repos/{REPOSITORY}/releases/tags/{TAG}")
        if current.returncode:
            raise RuntimeError("Published release lookup failed")
        release = json.loads(current.stdout)
    result = verify_public_assets(release)
    print(json.dumps(result))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(f"\nPublic release and all asset checksums verified: {result['releaseUrl']}\n")


if __name__ == "__main__":
    main()
