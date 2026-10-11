"""Verify and copy an accepted native evidence package; never execute CFD.

All member checks run before creating the independent output directory. A
transferred archive preserves an upstream preliminary numerical baseline only;
it does not establish mesh independence, physical validation or restart.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
from zipfile import ZipFile, ZIP_DEFLATED

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_native_pump_package as package
import frozen_rotor_cavitation_case as phase

MAX_RATIO = 4096
MANIFEST_KEYS = {"schemaVersion", "exampleDataCommit", "files", "archive", "inputWorkspaceBytes", "archivedBytes",
                 "excludedDuplicates", "missingOptionalEvidence", "upstreamLicenseIncluded", "nativeAcceptanceReport",
                 "simulationExecuted", "actualCfdRunVerified", "qualifiedBaseline", "executionStatus",
                 "preliminaryBaselineQualified", "preliminaryQualificationScope", "baselineQualificationScope",
                 "restartVerified", "physicalValidationVerified", "mainApplicationWrites", "limits", "claimBoundary"}


class UnpackIssue(ValueError):
    pass


def require(condition, action):
    if not condition:
        raise UnpackIssue(action)


def fingerprint(info):
    return info.st_size, info.st_mtime_ns, info.st_ino


def positive_int(value, upper):
    return type(value) is int and 0 < value <= upper


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)


def json_value(raw):
    require(0 < len(raw) <= package.MAX_REPORT, "Use nonempty bounded native JSON records.")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate evidence JSON keys are prohibited.")
            result[key] = value
        return result
    result = json.loads(raw, object_pairs_hook=unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(UnpackIssue("Evidence JSON must be finite.")))
    require(isinstance(result, dict), "A structured native evidence record is required.")
    return result


def inside_runner(path, runner, *, source):
    result = Path(path).absolute()
    package.no_links(result if source else result.parent)
    result = result.resolve()
    require(result != runner and result.is_relative_to(runner), "Source and target must stay strictly inside independent RUNNER_TEMP.")
    if source:
        package.no_links(result)
        info = result.stat()
        require(stat.S_ISREG(info.st_mode) and not info.st_mode & 0o111, "A regular data source without executable permissions is required.")
    else:
        require(not result.exists() and not result.is_symlink(), "The native extraction target must be fresh; existing evidence is never overwritten.")
        require(result.parent.is_dir(), "The fresh native target needs an existing independent parent directory.")
        package.no_links(result.parent)
    return result


def inspect_manifest(value, zip_size):
    require(set(value) == MANIFEST_KEYS and value.get("schemaVersion") == "untirta.frozen-rotor-native-evidence-package.v1"
            and value.get("exampleDataCommit") == package.PIN and value.get("mainApplicationWrites") is False,
            "Use the exact source-pinned independent native package schema.")
    archive = value.get("archive")
    require(isinstance(archive, dict) and set(archive) == {"name", "bytes", "sha256"} and archive.get("name") == package.ARCHIVE
            and positive_int(archive.get("bytes"), package.MAX_ZIP) and archive["bytes"] == zip_size and digest(archive.get("sha256")),
            "The actual bounded ZIP bytes and SHA256 must match its external manifest.")
    require(value.get("limits") == {"workspaceBytes": package.MAX_WORKSPACE, "fileBytes": package.MAX_FILE,
                                   "archiveBytes": package.MAX_ZIP, "entries": package.MAX_ENTRIES}, "Do not loosen the native package resource limits.")
    require(value.get("simulationExecuted") is True and value.get("actualCfdRunVerified") is True
            and value.get("preliminaryBaselineQualified") is True and type(value.get("qualifiedBaseline")) is bool
            and value.get("restartVerified") is False and value.get("physicalValidationVerified") is False
            and value.get("executionStatus") == "MRF_EXECUTED_ACCEPTANCE_REQUIRED", "An archive alone cannot qualify a baseline; consume actual execution and separate preliminary numerical qualification only.")
    require(isinstance(value.get("preliminaryQualificationScope"), str) and 0 < len(value["preliminaryQualificationScope"]) <= 1000,
            "Preserve the explicit upstream preliminary numerical scope.")
    rows = value.get("files")
    require(isinstance(rows, list) and 0 < len(rows) <= package.MAX_ENTRIES, "A bounded exact archive member inventory is required.")
    total, names = 0, []
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256", "role"} and isinstance(row.get("path"), str), "Every member needs exact path, bytes, SHA256 and native role.")
        kind, included = package.role(row["path"])
        require(included and kind == row["role"] and row["path"] not in names and positive_int(row["bytes"], package.MAX_FILE) and digest(row["sha256"]),
                "Unsupported, duplicate, excluded or unbounded archive member.")
        if kind == "native-report":
            require(row["bytes"] <= package.MAX_REPORT, "Native JSON reports cannot exceed 128 KiB.")
        names.append(row["path"]); total += row["bytes"]
        require(total <= package.MAX_WORKSPACE, "Uncompressed native evidence exceeds 512 MiB.")
    require(names == sorted(names) and package.REQUIRED_COMPLETED | package.REQUIRED_INPUTS <= set(names), "The exact sorted inventory must include all bound reports, authored inputs, native mesh and final fields.")
    require(type(value.get("archivedBytes")) is int and value["archivedBytes"] == total
            and positive_int(value.get("inputWorkspaceBytes"), package.MAX_WORKSPACE) and value["inputWorkspaceBytes"] >= total
            and value.get("missingOptionalEvidence") == [], "Package byte totals and completed evidence coverage must be exact.")
    excluded = value.get("excludedDuplicates")
    require(isinstance(excluded, list) and len(excluded) <= package.MAX_SCAN_ENTRIES and all(isinstance(name, str) for name in excluded)
            and excluded == sorted(set(excluded)) and all(package.role(name)[1] is False for name in excluded), "Only recognized original component duplicates may be excluded.")
    require(value.get("upstreamLicenseIncluded") is ("EXAMPLE_DATA_LICENSE.txt" in names)
            and value.get("nativeAcceptanceReport") == ("openfoam-acceptance.json" if "openfoam-acceptance.json" in names else None),
            "Optional license and acceptance inventory must match actual members.")
    return rows


def check_member(info, row):
    mode = info.external_attr >> 16
    require(info.filename == row["path"] and not info.is_dir() and info.compress_type == ZIP_DEFLATED and not info.flag_bits & 1
            and stat.S_ISREG(mode) and not mode & 0o111 and info.file_size == row["bytes"] and 0 < info.compress_size <= package.MAX_ZIP
            and info.file_size <= info.compress_size * MAX_RATIO and not info.extra and not info.comment,
            "ZIP members must be exact bounded regular data without links, code, encryption or hidden metadata.")


def verify_member(bundle, info, row):
    checksum, size, tail, report_parts = sha256(), 0, b"", []
    with bundle.open(info) as stream:
        while part := stream.read(1024*1024):
            size += len(part)
            require(size <= row["bytes"] and size <= package.MAX_FILE, "A ZIP member exceeded its declared uncompressed bound.")
            payload = tail + part
            require(not package.SECRET.search(payload), "Credential content is prohibited in native evidence.")
            if size == len(part):
                require(not part.startswith((b"MZ", b"\x7fELF", b"#!")), "Executable data is prohibited in the native package.")
            kind = row["role"]
            if kind in {"native-field", "native-dictionary", "native-topology"}:
                if size == len(part):
                    require(b"FoamFile" in part[:4096] and re.search(rb"\bformat\s+ascii\s*;", part[:4096]), "Use actual plain ASCII native FoamFile data.")
                require(not package.ACTIVE_CODE.search(payload) and all(byte in (9, 10, 13) or 32 <= byte < 127 for byte in part), "Executable directives or binary native data are prohibited.")
            if kind == "native-report":
                report_parts.append(part)
            checksum.update(part); tail = payload[-256:]
    require(size == row["bytes"] and checksum.hexdigest() == row["sha256"], "A declared ZIP member size or SHA256 does not match actual bytes.")
    if row["role"] == "upstream-license":
        require(size == package.LICENSE_BYTES and checksum.hexdigest() == package.LICENSE_SHA256, "Preserve the unchanged pinned upstream MIT license.")
    return json_value(b"".join(report_parts)) if report_parts else None


def unpack_package(zip_path, manifest_path, output_directory):
    """Return metadata for a fresh transferred execution directory after all gates."""
    require(bool(os.environ.get("RUNNER_TEMP")), "Native transfer requires independent RUNNER_TEMP.")
    runner = Path(os.environ["RUNNER_TEMP"]).absolute()
    require(runner.is_dir(), "An existing independent RUNNER_TEMP directory is required.")
    package.no_links(runner)
    runner = runner.resolve()
    require(not os.environ.get("GITHUB_REPOSITORY") or os.environ["GITHUB_REPOSITORY"] == "simcalc/PyAnsys", "This manual transfer is scoped to the public simcalc/PyAnsys runner.")
    source = inside_runner(zip_path, runner, source=True)
    manifest_source = inside_runner(manifest_path, runner, source=True)
    output = inside_runner(output_directory, runner, source=False)
    require(positive_int(source.stat().st_size, package.MAX_ZIP) and positive_int(manifest_source.stat().st_size, package.MAX_REPORT), "ZIP and external manifest must respect their fixed byte bounds.")
    with manifest_source.open("rb") as stream:
        manifest_raw = stream.read(package.MAX_REPORT + 1)
    require(not package.SECRET.search(manifest_raw), "Credential content is prohibited in the external native manifest.")
    manifest = json_value(manifest_raw)
    rows = inspect_manifest(manifest, source.stat().st_size)
    created = []
    try:
        with source.open("rb") as raw_zip:
            before = os.fstat(raw_zip.fileno())
            zip_hash, zip_bytes = sha256(), 0
            while part := raw_zip.read(1024*1024):
                zip_bytes += len(part)
                require(zip_bytes <= package.MAX_ZIP and zip_bytes <= manifest["archive"]["bytes"], "The ZIP changed or exceeded its actual byte bound.")
                zip_hash.update(part)
            require(zip_bytes == manifest["archive"]["bytes"] and zip_hash.hexdigest() == manifest["archive"]["sha256"], "The actual ZIP checksum differs from its external manifest.")
            raw_zip.seek(0)
            with ZipFile(raw_zip) as bundle:
                infos = bundle.infolist()
                require(not bundle.comment and [info.filename for info in infos] == [row["path"] for row in rows], "Unexpected, duplicate, missing or reordered ZIP entries are prohibited.")
                reports = {}
                for info, row in zip(infos, rows):
                    check_member(info, row)
                    report = verify_member(bundle, info, row)
                    if report is not None:
                        reports[row["path"]] = report
                execution, reduction = reports["openfoam-execution.json"], reports["native-baseline-reduction.json"]
                require(execution.get("schemaVersion") == "untirta.frozen-rotor-openfoam-execution.v1"
                        and reduction.get("schemaVersion") == "untirta.frozen-rotor-native-baseline-reduction.v1"
                        and reduction.get("status") == "PreliminaryBaselineQualified" and reduction.get("qualifiedBaseline") is True
                        and reduction.get("actualCfdRunVerified") is True and reduction.get("mainApplicationWrites") is False
                        and execution.get("simulationExecuted") is True and execution.get("actualCfdRunVerified") is True
                        and execution.get("qualifiedBaseline") is manifest["qualifiedBaseline"] and execution.get("mainApplicationWrites") is False
                        and execution.get("status") == manifest["executionStatus"] and reduction.get("preliminaryBaselineQualified") is True
                        and reduction.get("qualificationScope") == manifest["preliminaryQualificationScope"], "Archive metadata must preserve distinct actual execution and preliminary reduction claims.")
                require(fingerprint(os.fstat(raw_zip.fileno())) == fingerprint(before), "The source ZIP changed during verification.")
                # No unverified archive bytes or unsupported paths have reached disk.
                output.mkdir(); created.append(output)
                for info, row in zip(infos, rows):
                    target = output / row["path"]
                    require(target.absolute().is_relative_to(output), "The native target escaped its independent directory.")
                    parents = []
                    parent = target.parent
                    while parent != output and not parent.exists():
                        parents.append(parent); parent = parent.parent
                    for parent in reversed(parents):
                        parent.mkdir(); created.append(parent)
                    package.no_links(target.parent)
                    checksum, size = sha256(), 0
                    with target.open("xb") as sink:
                        created.append(target)
                        with bundle.open(info) as stream:
                            while part := stream.read(1024*1024):
                                size += len(part)
                                require(size <= row["bytes"], "The source ZIP changed during native persistence.")
                                sink.write(part); checksum.update(part)
                    require(size == row["bytes"] and checksum.hexdigest() == row["sha256"], "Transferred native evidence checksum differs from the verified source.")
            require(fingerprint(os.fstat(raw_zip.fileno())) == fingerprint(before) and fingerprint(source.stat()) == fingerprint(before), "The source ZIP changed during native transfer.")
        package.no_links(source)
        preliminary, scope = package.preliminary_qualification(output, {row["path"] for row in rows}, rows, execution)
        require(preliminary and scope == manifest["preliminaryQualificationScope"], "The transferred baseline needs its exact separately bound numerical reduction.")
        # Independent upstream read-only engineering and byte binding review.
        phase.verify_emitted_baseline(output, reduction)
        return {"status": "NativeBaselineTransferred", "executionDirectory": str(output), "sourceArchiveSha256": manifest["archive"]["sha256"],
                "sourceManifestSha256": sha256(manifest_raw).hexdigest(), "transferredBytes": manifest["archivedBytes"], "fileCount": len(rows),
                "simulationExecuted": True, "actualCfdRunVerified": True, "rawQualifiedBaseline": execution["qualifiedBaseline"],
                "preliminaryBaselineQualified": True, "preliminaryQualificationScope": scope, "restartVerified": False,
                "physicalValidationVerified": False, "mainApplicationWrites": False}
    except Exception:
        # Only the exact fresh files/directories owned by this call are removed.
        for path in reversed(created):
            require(path.absolute() == output or path.absolute().is_relative_to(output), "Native cleanup escaped the independent fresh target.")
            package.no_links(path)
            path.rmdir() if path.is_dir() else path.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(unpack_package(args.archive, args.manifest, args.output_directory), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
