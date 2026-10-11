"""Package actual PyPI wheels, upstream digests and the real import report."""
import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import shutil
import urllib.parse
import urllib.request
import zipfile

SDK_VERSION = "0.42.2"
BUNDLE_NAME = "pyfluent-sdk-0.42.2-cpython312-linux-x64.zip"
MAX_WHEEL_BYTES = 512 * 1024 * 1024


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def wheel_record(wheel):
    with zipfile.ZipFile(wheel) as archive:
        metadata_names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ValueError("Exactly one wheel distribution metadata file is required")
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        licenses = [n for n in archive.namelist() if "/licenses/" in n or n.rsplit("/", 1)[-1].upper().startswith(("LICENSE", "COPYING"))]
    name, version = metadata["Name"], metadata["Version"]
    url = f"https://pypi.org/pypi/{urllib.parse.quote(name, safe='')}/{urllib.parse.quote(version, safe='')}/json"
    request = urllib.request.Request(url, headers={"User-Agent": "PyAnsys-sdk-provenance"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read(4_194_305)
    if len(payload) > 4_194_304:
        raise ValueError("PyPI metadata response exceeds limit")
    published = [row for row in json.loads(payload)["urls"] if row["filename"] == wheel.name]
    if len(published) != 1:
        raise ValueError(f"Wheel is not present in official PyPI metadata: {wheel.name}")
    source = published[0]
    checksum = digest(wheel)
    if checksum != source["digests"]["sha256"] or wheel.stat().st_size != source["size"]:
        raise ValueError(f"Official PyPI wheel checksum mismatch: {wheel.name}")
    if urllib.parse.urlparse(source["url"]).hostname != "files.pythonhosted.org":
        raise ValueError("Wheel source must be the official PyPI file host")
    return {"name": name, "version": version, "filename": wheel.name,
            "bytes": wheel.stat().st_size, "sha256": checksum,
            "sourceUrl": source["url"], "metadataUrl": url,
            "upstreamSha256Verified": True, "upstreamLicenseFiles": licenses,
            "licenseExpression": metadata.get("License-Expression")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    wheels = sorted(args.wheels.glob("*.whl"))
    if not wheels or sum(w.stat().st_size for w in wheels) > MAX_WHEEL_BYTES:
        raise ValueError("Missing wheels or bundle exceeds bounded SDK download size")
    report = json.loads((args.output / "sdk-verification.json").read_text())
    if report["sdkInstallation"] != "VERIFIED" or report["sdkVersion"] != SDK_VERSION:
        raise ValueError("An actual successful SDK verification is required")
    records = [wheel_record(wheel) for wheel in wheels]
    if len({row["name"].lower().replace("_", "-") for row in records}) != len(records):
        raise ValueError("Duplicate distribution in wheelhouse")
    installed = {(r["name"].lower().replace("_", "-"), r["version"]) for r in report["installedDistributions"]}
    downloaded = {(r["name"].lower().replace("_", "-"), r["version"]) for r in records}
    if installed != downloaded:
        raise ValueError("Installed distribution inventory differs from downloaded wheels")
    sdk = [r for r in records if r["name"].lower().replace("_", "-") == "ansys-fluent-core" and r["version"] == SDK_VERSION]
    if len(sdk) != 1:
        raise ValueError("Pinned SDK wheel is missing")
    manifest = {"schemaVersion": "simcalc.pyfluent-wheel-manifest.v1",
                "sdkVersion": SDK_VERSION, "python": "CPython 3.12",
                "target": "Linux x64", "bundleFilename": BUNDLE_NAME,
                "sdkWheelFilename": sdk[0]["filename"], "wheels": records,
                "totalDownloadBytes": sum(r["bytes"] for r in records),
                "containsFluentSolver": False, "mainApplicationDataIncluded": False}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "wheel-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (args.output / "requirements-resolved.txt").write_text("".join(
        f"{r['name']}=={r['version']} --hash=sha256:{r['sha256']}\n" for r in records), encoding="utf-8")
    instructions = (
        "PyFluent 0.42.2 SDK with all required dependencies\n"
        "Target: Linux x64, CPython 3.12 (runner verification: 3.12.10).\n"
        "Verify the downloaded ZIP against SHA256SUMS, then extract into a fresh folder.\n"
        "Run: python3.12 install_offline.py\n"
        "The installer verifies every wheel digest and installs with --require-hashes.\n"
        "It creates a new .venv and performs pip check. It never launches Fluent.\n"
        "Python and venv support must already be installed.\n"
        "This Linux bundle is not a Windows installer. See the repository README for Windows.\n"
        "A licensed Ansys Fluent installation is needed to run a CFD solver.\n"
        "Upstream wheel license files remain inside their original, unmodified wheels.\n")
    (args.output / "INSTALL.txt").write_text(instructions, encoding="utf-8")
    with zipfile.ZipFile(args.output / BUNDLE_NAME, "w", compression=zipfile.ZIP_STORED) as archive:
        for wheel in wheels:
            archive.write(wheel, f"wheelhouse/{wheel.name}")
        for name in ("wheel-manifest.json", "requirements-resolved.txt", "sdk-verification.json", "INSTALL.txt"):
            archive.write(args.output / name, name)
        archive.write(Path(__file__).with_name("install_offline.py"), "install_offline.py")
    shutil.copyfile(args.wheels / sdk[0]["filename"], args.output / sdk[0]["filename"])
    assets = sorted(p for p in args.output.iterdir() if p.is_file() and p.name != "SHA256SUMS")
    (args.output / "SHA256SUMS").write_text("".join(f"{digest(p)}  {p.name}\n" for p in assets), encoding="utf-8")
    summary = (f"## Actual SDK installation verified\n\n"
               f"- SDK: ansys-fluent-core {SDK_VERSION}; CPython {report['pythonVersion']}.\n"
               f"- Required wheel distributions: {len(records)}.\n"
               f"- Downloaded wheels: {manifest['totalDownloadBytes']:,} bytes.\n"
               f"- Installed SDK and dependencies: {report['installedSdkAndDependenciesBytes']:,} bytes (excludes Python/pip).\n"
               f"- Every wheel SHA-256 matches official PyPI metadata.\n"
               f"- Fluent solver/license/session and CFD execution: **not verified; no solver installed or launched**.\n"
               f"- Main application source, runtime and data: **not included or modified**.\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(summary)
    print(summary)


if __name__ == "__main__":
    main()
