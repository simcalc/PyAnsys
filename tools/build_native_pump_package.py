"""Archive bounded native pump evidence as data, never execute or verify restart.

Preserve actual execution claims from the independent runner. Archiving is not
numerical convergence, physical validation, cavitation or restart acceptance.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from zipfile import ZipFile, ZIP_DEFLATED, ZipInfo

MAX_WORKSPACE = 512 * 1024**2
MAX_FILE = 128 * 1024**2
MAX_ZIP = 128 * 1024**2
MAX_REPORT = 128 * 1024
MAX_ENTRIES = 256
MAX_SCAN_ENTRIES = 512
PIN = "8ef505f6ef699eeec68a16001df91ba353342f40"
LICENSE_BYTES = 1114
LICENSE_SHA256 = "46617266d39d9ecdd829d29ef6e9a237e9312e71531ddc258e2303e28ab041db"
ARCHIVE = "native-case-evidence.zip"
MANIFEST = "native-case-evidence.json"
REPORTS = {"runtime-identity.json", "geometry-identity.json", "native-input-manifest.json", "classroom-study.json",
           "openfoam-preparation.json", "openfoam-execution.json", "native-baseline-reduction.json", "remesh-report.json"}
OPTIONAL_REPORTS = {"openfoam-acceptance.json"}
FIELDS = {"p", "U", "k", "omega", "nut", "phi", "U2", "totalPressurePa", "staticPressurePa", "yPlus", "polyFaces"}
MESH = {"points", "faces", "owner", "neighbour", "boundary", "cellZones", "faceZones", "pointZones", "polyFaces"}
CONSTANT = {"physicalProperties", "momentumTransport", "MRFProperties", "polyFaces"}
SYSTEM = {"controlDict", "fvSchemes", "fvSolution", "changeDictionaryDict", "createNonConformalCouplesDict"}
SAMPLERS = {"flowInlet", "flowOutlet", "flowMagnitudeInlet", "flowMagnitudeOutlet", "weightedInlet", "weightedOutlet",
            "flowRotorInterface", "flowStatorInterface", "flowMagnitudeRotorInterface", "flowMagnitudeStatorInterface"}
PROGRAMS = {"gmshToFoam", "changeDictionary", "mergeMeshes", "createNonConformalCouples", "checkMesh", "foamRun"}
REQUIRED_INPUTS = {"impeller-case/0/" + name for name in ("p", "U", "k", "omega", "nut")} | \
                  {"impeller-case/constant/" + name for name in ("physicalProperties", "momentumTransport", "MRFProperties")} | \
                  {"impeller-case/system/" + name for name in SYSTEM}
REQUIRED_COMPLETED = REPORTS | {"impeller-case/constant/polyMesh/" + name for name in ("points", "faces", "owner", "neighbour", "boundary", "cellZones")} | \
                     {"impeller-case/500/" + name for name in ("p", "U", "k", "omega")}
SECRET = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|Authorization\s*:\s*Bearer\s+\S+)")
ACTIVE_CODE = re.compile(rb"#(?:codeStream|include(?!Etc\s+\"caseDicts/setConstraintTypes\"))|\bcodedFixedValue\b|\bcodeInclude\b|\btype\s+coded\b")


class PackageIssue(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PackageIssue(message)


def no_links(path):
    for item in (path, *path.parents):
        metadata = item.lstat()
        require(not stat.S_ISLNK(metadata.st_mode) and not getattr(metadata, "st_file_attributes", 0) & 0x400,
                "Native evidence paths cannot contain links or junctions.")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def json_record(path):
    require(0 < path.stat().st_size <= MAX_REPORT, "Native reports need nonempty bounded regular JSON.")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate evidence JSON keys are prohibited.")
            result[key] = value
        return result
    value = json.loads(path.read_bytes(), object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(PackageIssue("Native report JSON must be finite.")))
    require(isinstance(value, dict), "A structured native report is required.")
    return value


def case_role(parts):
    if len(parts) == 2 and parts[0] in {"0", "500"} and parts[1] in FIELDS:
        return "native-field"
    if parts in [("500", "uniform", "time"), ("500", "uniform", "functionObjects", "functionObjectProperties")]:
        return "native-dictionary"
    if len(parts) == 2 and ((parts[0] == "constant" and parts[1] in CONSTANT) or (parts[0] == "system" and parts[1] in SYSTEM)):
        return "native-dictionary"
    if len(parts) == 3 and parts[:2] == ("constant", "polyMesh") and parts[2] in MESH:
        return "native-topology"
    if len(parts) == 4 and parts[0] == "postProcessing" and parts[2] in {"0", "1", "500"}:
        if parts[1] in SAMPLERS and parts[3] == "surfaceFieldValue.dat":
            return "native-numerical-table"
        if parts[1] in {"globalPressureMinimum", "globalPressureMaximum"} and parts[3] == "volFieldValue.dat":
            return "native-numerical-table"
        if parts[1] == "wallYPlus" and parts[3] == "yPlus.dat":
            return "native-numerical-table"
    return None


def role(relative):
    parts = tuple(relative.split("/"))
    require(len(relative) <= 240 and len(parts) <= 6 and all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part) for part in parts),
            "A finite visible native data path is required.")
    if len(parts) == 1:
        if relative in REPORTS | OPTIONAL_REPORTS:
            return "native-report", True
        if relative == "EXAMPLE_DATA_LICENSE.txt":
            return "upstream-license", True
    if len(parts) == 2 and parts[0] == "logs" and re.fullmatch(r"[0-9]{2}-(?:" + "|".join(sorted(PROGRAMS)) + r")\.log", parts[1]):
        return "native-log", True
    if parts[0] in {"impeller-case", "volute-case"}:
        component = parts[0].split("-")[0]
        if parts[1:] == (component + ".msh",):
            return "excluded-component-msh-duplicate", False
        found = case_role(parts[1:])
        if found:
            return found, parts[0] == "impeller-case"
    raise PackageIssue("Unrecognized or executable/credential evidence role: " + relative)


def scan(root):
    files, total, visited = [], 0, 0
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                require(visited <= MAX_SCAN_ENTRIES, "Native workspace path count exceeds the fixed bound.")
                path = Path(entry.path)
                no_links(path)
                relative = path.relative_to(root).as_posix()
                require(len(relative) <= 240 and len(path.relative_to(root).parts) <= 6 and not any(part.startswith(".") for part in path.relative_to(root).parts),
                        "Hidden or unbounded native workspace paths are prohibited.")
                # Use one stat API throughout: DirEntry.stat returns a different
                # inode representation from Path.stat on some Windows versions.
                info = path.stat()
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                    continue
                require(stat.S_ISREG(info.st_mode) and not info.st_mode & 0o111 and 0 < info.st_size <= MAX_FILE,
                        "Only bounded nonempty non-executable regular evidence files are permitted.")
                total += info.st_size
                require(total <= MAX_WORKSPACE, "The input native workspace exceeds 512 MiB.")
                kind, include = role(relative)
                files.append((relative, path, info, kind, include))
    require(sum(row[4] for row in files) <= MAX_ENTRIES, "The native archive entry count exceeds the fixed bound.")
    return sorted(files), total


def validate_report_claims(root, names):
    require({"openfoam-preparation.json", "openfoam-execution.json"} | REQUIRED_INPUTS <= names,
            "Required native preparation/execution reports and authored case inputs are absent.")
    preparation, execution = json_record(root / "openfoam-preparation.json"), json_record(root / "openfoam-execution.json")
    require(preparation.get("exampleDataCommit") == PIN and preparation.get("mainApplicationWrites") is False and
            execution.get("schemaVersion") == "untirta.frozen-rotor-openfoam-execution.v1" and execution.get("mainApplicationWrites") is False,
            "Preserve exact educational source provenance and Main isolation.")
    for flag in ("simulationExecuted", "actualCfdRunVerified", "qualifiedBaseline"):
        require(isinstance(execution.get(flag), bool), "Actual execution flags must be explicit booleans: " + flag)
    require(not execution["actualCfdRunVerified"] or (execution["simulationExecuted"] and execution.get("status") == "MRF_EXECUTED_ACCEPTANCE_REQUIRED"),
            "Actual CFD cannot be claimed from an unexecuted or corrected native stage.")
    require(not execution["qualifiedBaseline"] or execution["actualCfdRunVerified"], "Numerical baseline qualification needs an actual verified execution.")
    if execution["actualCfdRunVerified"]:
        require(REQUIRED_COMPLETED <= names, "Verified native execution needs all actual bound reports, mesh and final fields.")
        require(any(name.startswith("impeller-case/postProcessing/") for name in names) and any(name.startswith("logs/") for name in names),
                "Verified native execution requires actual numerical tables and native logs.")
    return execution


def preliminary_qualification(root, names, records, execution):
    """Preserve a separately bound reducer claim without promoting raw execution."""
    if "native-baseline-reduction.json" not in names:
        return False, None
    reduction = json_record(root / "native-baseline-reduction.json")
    if reduction.get("preliminaryBaselineQualified") is not True:
        return False, reduction.get("qualificationScope")
    require(execution["actualCfdRunVerified"] and reduction.get("schemaVersion") == "untirta.frozen-rotor-native-baseline-reduction.v1"
            and reduction.get("status") == "PreliminaryBaselineQualified" and reduction.get("qualifiedBaseline") is True
            and reduction.get("actualCfdRunVerified") is True and reduction.get("mainApplicationWrites") is False,
            "A preliminary qualification must preserve an actual independently reduced native run.")
    scope = reduction.get("qualificationScope")
    require(isinstance(scope, str) and 0 < len(scope) <= 1000, "The reducer's preliminary numerical qualification scope is required.")
    evidence = reduction.get("nativeEvidenceRecords")
    require(isinstance(evidence, list) and evidence and len(evidence) <= MAX_ENTRIES, "Preliminary reduction requires actual native evidence records.")
    archived = {row["path"]: row for row in records}
    seen = set()
    for row in evidence:
        require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"} and row["path"] not in seen
                and row["path"] in archived and all(row[key] == archived[row["path"]][key] for key in ("bytes", "sha256")),
                "The preliminary reduction must bind the exact archived native evidence bytes.")
        seen.add(row["path"])
    require({"native-input-manifest.json", "openfoam-execution.json"} <= seen, "The reduction must bind both actual input and immutable execution records.")
    observation = reduction.get("nativeObservation", {})
    proof = observation.get("controlledVariationProof", {})
    require(proof.get("nativeInputManifestSha256") == archived["native-input-manifest.json"]["sha256"] and
            observation.get("nativeEvidence", {}).get("sha256") == sha256(canonical(sorted(evidence, key=lambda row: row["path"]))).hexdigest(),
            "The reducer's actual input and aggregate evidence checksums must match the archive.")
    return True, scope


def build_package(execution_directory):
    """Write a fresh ZIP and exact file manifest below independent RUNNER_TEMP."""
    root = Path(execution_directory).absolute()
    require(root.is_dir(), "An existing independent native evidence directory is required.")
    no_links(root)
    runner = os.environ.get("RUNNER_TEMP")
    require(bool(runner), "Native packaging requires independent RUNNER_TEMP.")
    runner_path = Path(runner).absolute()
    no_links(runner_path)
    require(root.resolve() != runner_path.resolve() and root.resolve().is_relative_to(runner_path.resolve()), "The case must remain strictly inside independent RUNNER_TEMP.")
    archive, manifest_path = root / ARCHIVE, root / MANIFEST
    require(not archive.exists() and not manifest_path.exists(), "Native package targets must be fresh; existing evidence is never overwritten.")
    files, workspace_bytes = scan(root)
    names = {row[0] for row in files if row[4]}
    execution = validate_report_claims(root, names)
    records, created = [], []
    try:
        with ZipFile(archive, "x", ZIP_DEFLATED, compresslevel=6, allowZip64=False) as bundle:
            created.append(archive)
            for relative, path, info, kind, include in files:
                if not include:
                    continue
                if kind == "native-report":
                    json_record(path)
                no_links(path)
                digest, size, tail = sha256(), 0, b""
                zi = ZipInfo(relative, date_time=(2026, 10, 12, 0, 0, 0))
                zi.compress_type, zi.external_attr = ZIP_DEFLATED, (stat.S_IFREG | 0o644) << 16
                with path.open("rb") as source, bundle.open(zi, "w") as target:
                    opened = os.fstat(source.fileno())
                    require((opened.st_size, opened.st_mtime_ns, opened.st_ino) == (info.st_size, info.st_mtime_ns, info.st_ino),
                            "Native evidence changed before archiving; package a completed immutable run.")
                    while part := source.read(1024*1024):
                        size += len(part)
                        require(size <= MAX_FILE and size <= info.st_size, "A native evidence file changed or exceeded its bound during archiving.")
                        payload = tail + part
                        require(not SECRET.search(payload), "Credential content is prohibited in native evidence.")
                        if size == len(part):
                            require(not part.startswith((b"MZ", b"\x7fELF", b"#!")), "Executable content is prohibited in native evidence.")
                        if kind.startswith("native-") and kind not in {"native-report", "native-log", "native-numerical-table"}:
                            if size == len(part):
                                require(b"FoamFile" in part[:4096] and re.search(rb"\bformat\s+ascii\s*;", part[:4096]), "Native case storage must be actual ASCII FoamFile data.")
                            require(not ACTIVE_CODE.search(payload), "Executable or arbitrary include directives are prohibited in native dictionaries.")
                            require(all(byte in (9, 10, 13) or 32 <= byte < 127 for byte in part), "Only plain ASCII native case data may be archived.")
                        target.write(part); digest.update(part); tail = payload[-256:]
                        require(archive.stat().st_size <= MAX_ZIP, "Compressed native evidence exceeds 128 MiB.")
                after = path.stat()
                no_links(path)
                require(size == info.st_size and (after.st_size, after.st_mtime_ns, after.st_ino) == (info.st_size, info.st_mtime_ns, info.st_ino),
                        "Native evidence changed during archiving; package a completed immutable run.")
                if kind == "upstream-license":
                    require(size == LICENSE_BYTES and digest.hexdigest() == LICENSE_SHA256, "The unchanged pinned upstream MIT license is required.")
                records.append({"path": relative, "bytes": size, "sha256": digest.hexdigest(), "role": kind})
        # A file completed early in the archive must also remain unchanged while
        # later files are read; preserve the snapshot of the completed run.
        for _, path, info, _, include in files:
            if include:
                no_links(path)
                after = path.stat()
                require((after.st_size, after.st_mtime_ns, after.st_ino) == (info.st_size, info.st_mtime_ns, info.st_ino),
                        "Native evidence changed during archiving; package a completed immutable run.")
        require(archive.stat().st_size <= MAX_ZIP, "Compressed native evidence exceeds 128 MiB.")
        with ZipFile(archive) as bundle:
            require(bundle.namelist() == [row["path"] for row in records], "Native archive readback must preserve the exact file inventory.")
            for row in records:
                digest, size = sha256(), 0
                with bundle.open(row["path"]) as stream:
                    while part := stream.read(1024*1024):
                        size += len(part); digest.update(part)
                require(size == row["bytes"] and digest.hexdigest() == row["sha256"], "Native ZIP readback checksum differs from its actual input record.")
        zip_digest = sha256()
        with archive.open("rb") as stream:
            while part := stream.read(1024*1024):
                zip_digest.update(part)
        preliminary, qualification_scope = preliminary_qualification(root, names, records, execution)
        manifest = {"schemaVersion": "untirta.frozen-rotor-native-evidence-package.v1", "exampleDataCommit": PIN,
                    "files": records, "archive": {"name": ARCHIVE, "bytes": archive.stat().st_size, "sha256": zip_digest.hexdigest()},
                    "inputWorkspaceBytes": workspace_bytes, "archivedBytes": sum(row["bytes"] for row in records),
                    "excludedDuplicates": [row[0] for row in files if not row[4]],
                    "missingOptionalEvidence": sorted(REQUIRED_COMPLETED - names), "upstreamLicenseIncluded": "EXAMPLE_DATA_LICENSE.txt" in names,
                    "nativeAcceptanceReport": "openfoam-acceptance.json" if "openfoam-acceptance.json" in names else None,
                    "simulationExecuted": execution["simulationExecuted"], "actualCfdRunVerified": execution["actualCfdRunVerified"],
                    "qualifiedBaseline": execution["qualifiedBaseline"], "executionStatus": execution.get("status"),
                    "preliminaryBaselineQualified": preliminary, "preliminaryQualificationScope": qualification_scope,
                    "baselineQualificationScope": execution.get("baselineQualificationScope"), "restartVerified": False,
                    "physicalValidationVerified": False, "mainApplicationWrites": False,
                    "limits": {"workspaceBytes": MAX_WORKSPACE, "fileBytes": MAX_FILE, "archiveBytes": MAX_ZIP, "entries": MAX_ENTRIES},
                    "claimBoundary": "Native evidence data copy and checksum readback only. Execution flags are preserved from the runner; no additional convergence, mesh/time independence, NPSH3, physical validation or restart claim."}
        raw = canonical(manifest)
        require(len(raw) <= MAX_REPORT, "The native package manifest exceeds 128 KiB.")
        with manifest_path.open("xb") as stream:
            created.append(manifest_path)
            stream.write(raw)
        return {"status": "NativeEvidencePackaged", "archive": manifest["archive"],
                "manifest": {"name": MANIFEST, "bytes": len(raw), "sha256": sha256(raw).hexdigest()},
                "simulationExecuted": manifest["simulationExecuted"], "actualCfdRunVerified": manifest["actualCfdRunVerified"],
                "qualifiedBaseline": manifest["qualifiedBaseline"], "preliminaryBaselineQualified": preliminary,
                "preliminaryQualificationScope": qualification_scope, "restartVerified": False, "mainApplicationWrites": False}
    except Exception:
        # Exact fresh files created by this call only; never recursive cleanup.
        for path in reversed(created):
            require(path.absolute().is_relative_to(root), "Cleanup target escaped the independent native case.")
            if path.exists() and not path.is_symlink():
                path.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-directory", type=Path, required=True)
    args = parser.parse_args()
    result = build_package(args.execution_directory)
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
