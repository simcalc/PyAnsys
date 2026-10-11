"""Safely transfer one admitted public baseline ZIP without executing its data.

GitHub metadata, locally verified archive bytes and current native/numerical
acceptance are separate facts. Only the selected baseline subtree is extracted;
the separately reviewed phase preparer must re-reduce its current evidence.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_snappy_phase_preflight as admission_tool

SCHEMA = "untirta.frozen-rotor-snappy-phase-source-transfer.v1"
REPORT = "source-transfer.json"
EXECUTION_SCHEMA = "untirta.frozen-rotor-snappy-baseline-execution.v1"
SELECTED_ROOT = "frozen-snappy-baseline"
ALLOWED_ROOTS = {"frozen-boundary-surfaces", "frozen-snappy-native", SELECTED_ROOT,
                 "frozen-surface-unions", "frozen-surface-native"}
MAX_ARCHIVE_BYTES = 1024**3
MAX_EXPANDED_BYTES = 2 * 1024**3
MAX_FILE_BYTES = 128 * 1024**2
MAX_METADATA_BYTES = MAX_REPORT_BYTES = 128 * 1024
MAX_ENTRIES, MAX_PATH_BYTES, MAX_PATH_DEPTH = 5000, 512, 12
CHUNK_BYTES = 1024**2
FORBIDDEN_SUFFIXES = {".py", ".pyc", ".pyo", ".sh", ".bash", ".ps1", ".bat", ".cmd",
    ".exe", ".dll", ".so", ".dylib", ".com", ".msi", ".scr", ".jar", ".js", ".mjs",
    ".cjs", ".html", ".htm", ".svg"}
WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)}, *{f"LPT{i}" for i in range(1, 10)}}
NATIVE_TRUE_FLAGS = ("actualCfdRunVerified", "simulationExecuted", "nativeGeometryQualityVerified",
    "physicalWallTypesVerified", "coupledInterfaceCoverageVerified", "nativeSolverCompleted",
    "finiteNativeFieldsVerified", "inputArtifactsImmutable")
FALSE_FLAGS = ("sourceNumericalBaselineQualified", "sourceNativeEvidenceVerified", "actualCfdRunVerified",
    "simulationExecuted", "phaseFamilyReferenceQualified", "cavitationSweepExecuted",
    "physicalTransientExecuted", "npshrCalculated", "mainApplicationWrites")


def require(value, action):
    if not value:
        raise ValueError(action)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON object keys are prohibited.")
        result[key] = value
    return result


def json_data(raw):
    require(0 < len(raw) <= MAX_METADATA_BYTES, "Metadata must remain within 128 KiB.")
    def invalid(value):
        raise ValueError("Nonfinite JSON constants are prohibited: " + value)
    value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=unique_object, parse_constant=invalid)
    def visit(item, depth=0):
        require(depth <= 48, "Metadata nesting exceeds its bounded profile.")
        if isinstance(item, float): require(math.isfinite(item), "Nonfinite JSON numbers are prohibited.")
        elif isinstance(item, dict):
            for child in item.values(): visit(child, depth+1)
        elif isinstance(item, list):
            for child in item: visit(child, depth+1)
    visit(value)
    require(isinstance(value, dict), "Metadata must be one JSON object.")
    return value


def scope_path(value, *, fresh=False):
    require(os.environ.get("RUNNER_TEMP"), "Use an existing isolated RUNNER_TEMP.")
    runner = Path(os.environ["RUNNER_TEMP"]).absolute()
    require(runner.is_dir() and not runner.is_symlink() and not getattr(runner, "is_junction", lambda: False)(),
            "RUNNER_TEMP must be one existing regular directory.")
    path = Path(value).absolute()
    require(path != runner and path.resolve().is_relative_to(runner.resolve()), "Transfer paths must remain inside RUNNER_TEMP.")
    require(path.is_relative_to(runner), "Transfer paths must have a plain lexical RUNNER_TEMP parent.")
    for item in (path, *path.parents):
        require(not item.is_symlink() and not getattr(item, "is_junction", lambda: False)(), "Transfer paths cannot contain links or junctions.")
        if item == runner: break
    require(not fresh or not path.exists(), "Use a fresh independently owned transfer output directory.")
    return path


def read_regular(path, limit):
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= limit, "Read one bounded regular transfer file.")
    with path.open("rb") as stream: raw = stream.read(limit+1)
    require(0 < len(raw) <= limit, "Transfer file grew beyond its declared bound.")
    return raw


def validate_admission(value):
    """Revalidate exact admitted metadata, never synthesize native acceptance."""
    inputs = admission_tool.validate_inputs({"BASELINE_RUN_ID": value.get("baselineRunId")})
    repo = {"full_name": admission_tool.REPOSITORY, "private": False}
    run = {"id": int(inputs["baselineRunId"]), "status": "completed", "conclusion": "success",
           "event": "workflow_dispatch", "path": value.get("sourceWorkflow"), "head_sha": value.get("sourceCommit"),
           "repository": repo, "head_repository": repo}
    artifacts = {"artifacts": [{"id": value.get("artifactId"), "name": value.get("artifactName"),
        "expired": False, "size_in_bytes": value.get("artifactBytes"), "workflow_run": {"id": int(inputs["baselineRunId"])},
        "digest": value.get("githubReportedArchiveDigest")}]}
    expected = admission_tool.validate_source(inputs, run, artifacts)
    require(set(value) == set(expected) and value == expected and
            all(value[key] is False for key, item in inputs.items() if item is False),
            "Use the unchanged fixed public source admission; metadata does not qualify downloaded results.")
    require(value["artifactBytes"] <= MAX_ARCHIVE_BYTES, "The admitted archive exceeds one GiB.")
    return value


def stream_digest(stream):
    digest, size = sha256(), 0
    while True:
        chunk = stream.read(CHUNK_BYTES)
        if not chunk: break
        size += len(chunk)
        require(size <= MAX_ARCHIVE_BYTES, "Downloaded archive exceeds one GiB.")
        digest.update(chunk)
    return size, digest.hexdigest()


def member_path(info):
    name = info.filename
    require(isinstance(name, str) and name == info.orig_filename and 0 < len(name.encode("utf-8")) <= MAX_PATH_BYTES
            and "\\" not in name and "\x00" not in name and not name.startswith("/"), "Archive names must be bounded plain relative paths.")
    directory = name.endswith("/")
    normalized = name[:-1] if directory else name
    parts = normalized.split("/")
    require(1 <= len(parts) <= MAX_PATH_DEPTH and all(part not in {"", ".", ".."} and not part.startswith(".")
            and not part.endswith(".") and part.split(".")[0].upper() not in WINDOWS_DEVICES
            and re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts), "Archive path traversal, directives, hidden paths and platform aliases are prohibited.")
    require(parts[0] in ALLOWED_ROOTS and (directory or len(parts) >= 2), "Only the five declared data artifact roots are allowed.")
    require(PurePosixPath(normalized).as_posix() == normalized, "Archive paths must have one canonical relative representation.")
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    require(info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED} and not (info.flag_bits & 0x41),
            "Encrypted or unsupported archive compression is prohibited.")
    if directory:
        require(kind in {0, stat.S_IFDIR} and info.file_size == 0 and info.CRC == 0,
                "Archive directory entries must carry no payload or link role.")
    else:
        require(kind in {0, stat.S_IFREG} and not (mode & 0o111) and not (info.external_attr & 0x10),
                "Archive files must be regular nonexecutable data, never links or device nodes.")
        suffix = PurePosixPath(normalized).suffix.lower()
        require(suffix not in FORBIDDEN_SUFFIXES, "Executable/code-bearing archive file types are prohibited.")
        require(0 < info.file_size <= MAX_FILE_BYTES, "Individual archive data files must be positive and no larger than 128 MiB.")
    return normalized, directory


def inspect_members(archive):
    infos = archive.infolist()
    require(0 < len(infos) <= MAX_ENTRIES, "Archive inventory exceeds the 5000-entry bound.")
    names, prefixes, selected, total = {}, {}, [], 0
    for info in infos:
        name, directory = member_path(info)
        key = name.casefold()
        require(key not in names, "Duplicate archive paths and case aliases are prohibited.")
        parts = name.split("/")
        for i in range(1, len(parts)+1):
            prefix = "/".join(parts[:i]); folded = prefix.casefold()
            require(folded not in prefixes or prefixes[folded] == prefix,
                    "Archive parent directories cannot have alternate case spellings.")
            prefixes[folded] = prefix
        names[key] = (name, directory)
        total += info.file_size
        require(total <= MAX_EXPANDED_BYTES, "All archive members exceed the separate two-GiB expanded bound.")
        if name.startswith(SELECTED_ROOT + "/") and not directory: selected.append((name, info))
    for name, _ in names.values():
        for parent in PurePosixPath(name).parents:
            if str(parent) == ".": continue
            prior = names.get(str(parent).casefold())
            require(prior is None or prior[1], "An archive file cannot also be a parent directory.")
    selected.sort(key=lambda row: row[0])
    require(selected, "The exact frozen-snappy-baseline data subtree is required.")
    return selected, len(infos), total


def verify_execution(archive, selected, admitted):
    matches = [info for name, info in selected if name == SELECTED_ROOT + "/openfoam-execution.json"]
    require(len(matches) == 1 and matches[0].file_size <= MAX_METADATA_BYTES,
            "Require one bounded inner baseline execution report before creating output.")
    with archive.open(matches[0], "r") as stream: raw = stream.read(MAX_METADATA_BYTES+1)
    require(len(raw) == matches[0].file_size, "Inner execution bytes differ from their ZIP header.")
    value = json_data(raw)
    require(value.get("schemaVersion") == EXECUTION_SCHEMA and value.get("githubRepository") == admission_tool.REPOSITORY
            and value.get("githubRunId") == admitted["baselineRunId"] and value.get("githubSourceCommit") == admitted["sourceCommit"]
            and value.get("status") == "SNAPPY_MRF_EXECUTED_ACCEPTANCE_REQUIRED"
            and all(value.get(key) is True for key in NATIVE_TRUE_FLAGS)
            and all(value.get(key) is False for key in ("qualifiedBaseline", "numericalBaselineQualified",
                "fullGeometryQualification", "geometricApproximationQualified", "mainApplicationWrites")),
            "Inner native execution metadata must match the admitted public run/head without inherited numerical qualification.")
    return {"path": SELECTED_ROOT + "/openfoam-execution.json", "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def cleanup_owned(output, runner):
    """Remove only this attempt's checked fresh tree; never follow a changed root."""
    require(output != runner and output.resolve().is_relative_to(runner.resolve()) and not output.is_symlink()
            and not getattr(output, "is_junction", lambda: False)(), "Attempt cleanup target must remain the exact owned RUNNER_TEMP directory.")
    if output.exists(): shutil.rmtree(output)


def extract(archive_path, admission_json, output_directory):
    archive_path, admission_path = scope_path(archive_path), scope_path(admission_json)
    output = scope_path(output_directory, fresh=True)
    require(not archive_path.is_relative_to(output) and not admission_path.is_relative_to(output), "Transfer inputs cannot be inside its fresh output.")
    admitted_raw = read_regular(admission_path, MAX_METADATA_BYTES)
    admitted = validate_admission(json_data(admitted_raw))
    require(archive_path.is_file() and 0 < archive_path.stat().st_size == admitted["artifactBytes"] <= MAX_ARCHIVE_BYTES,
            "Actual downloaded ZIP size must equal admitted GitHub bytes and remain below one GiB.")
    runner, created = Path(os.environ["RUNNER_TEMP"]).absolute(), False
    try:
        with archive_path.open("rb") as stream:
            size, digest = stream_digest(stream)
            require(size == admitted["artifactBytes"] and "sha256:" + digest == admitted["githubReportedArchiveDigest"],
                    "Actual streamed downloaded ZIP digest differs from GitHub-reported archive identity.")
            stream.seek(0)
            require(stream.read(4) == b"PK\x03\x04", "Use a native ZIP archive without an executable preamble.")
            stream.seek(0)
            with zipfile.ZipFile(stream, "r") as archive:
                selected, entry_count, expanded = inspect_members(archive)
                execution_row = verify_execution(archive, selected, admitted)
                report = {"schemaVersion": SCHEMA, "status": "ARCHIVE_TRANSFERRED_CURRENT_BASELINE_REVALIDATION_REQUIRED",
                    "repository": admission_tool.REPOSITORY, "baselineRunId": admitted["baselineRunId"],
                    "sourceCommit": admitted["sourceCommit"], "artifactId": admitted["artifactId"], "artifactName": admitted["artifactName"],
                    "sourceWorkflow": admitted["sourceWorkflow"], "mode": admitted["mode"], "pressureAbsolutePa": admitted["pressureAbsolutePa"],
                    "maximumTimeStepS": admitted["maximumTimeStepS"], "durationS": admitted["durationS"],
                    "githubReportedArchiveDigest": admitted["githubReportedArchiveDigest"],
                    "actualArchiveSha256": digest, "actualArchiveBytes": size, "artifactBytesVerified": True,
                    "admissionJsonBytes": len(admitted_raw), "admissionJsonSha256": sha256(admitted_raw).hexdigest(),
                    "archiveEntryCount": entry_count, "archiveExpandedBytes": expanded,
                    "selectedSubtree": SELECTED_ROOT, "selectedBytes": sum(info.file_size for _, info in selected),
                    "sourceExecutionMetadataBound": True, "sourceExecutionReport": execution_row,
                    "extractedFileRecords": [{"path": name, "bytes": info.file_size, "sha256": "0"*64} for name, info in selected],
                    "limits": {"archiveBytes": MAX_ARCHIVE_BYTES, "expandedBytes": MAX_EXPANDED_BYTES,
                        "fileBytes": MAX_FILE_BYTES, "entries": MAX_ENTRIES, "metadataAndReportBytes": MAX_METADATA_BYTES},
                    "claimBoundary": "Only local archive bytes, safe data transfer and inner public run/head metadata are verified. The current native baseline and its numerical reduction must be independently revalidated by the phase preparer; no artifact is executed by this tool.",
                    **{flag: False for flag in FALSE_FLAGS}}
                require(len(encoded(report)) <= MAX_REPORT_BYTES, "The selected member proof exceeds its 128-KiB transfer report bound.")
                scope_path(output, fresh=True)
                output.mkdir(parents=True, exist_ok=False); created = True
                rows = []
                for name, info in selected:
                    target = output.joinpath(*PurePosixPath(name).parts)
                    scope_path(target.parent)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    scope_path(target)
                    count, content_hash = 0, sha256()
                    with archive.open(info, "r") as source, target.open("xb") as destination:
                        while True:
                            chunk = source.read(CHUNK_BYTES)
                            if not chunk: break
                            count += len(chunk)
                            require(count <= info.file_size and count <= MAX_FILE_BYTES, "An archive member exceeds its actual declared byte bound.")
                            content_hash.update(chunk)
                            require(destination.write(chunk) == len(chunk), "A data transfer write was incomplete.")
                    require(count == info.file_size, "An extracted member differs from its declared ZIP size.")
                    rows.append({"path": name, "bytes": count, "sha256": content_hash.hexdigest()})
                report["extractedFileRecords"] = rows
                require(next(row for row in rows if row["path"] == execution_row["path"]) == execution_row,
                        "The inner execution bytes changed after pre-write validation.")
            with archive_path.open("rb") as current: post_size, post_digest = stream_digest(current)
            require((post_size, post_digest) == (size, digest) and read_regular(admission_path, MAX_METADATA_BYTES) == admitted_raw,
                    "Archive or admission bytes changed during transfer.")
            raw = encoded(report)
            require(len(raw) <= MAX_REPORT_BYTES, "The final transfer report exceeds 128 KiB.")
            with (output / REPORT).open("xb") as destination: require(destination.write(raw) == len(raw), "Transfer report persistence was incomplete.")
        return report
    except Exception:
        if created: cleanup_owned(output, runner)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--admission-json", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = extract(args.archive, args.admission_json, args.output_directory)
        print(json.dumps({key: report[key] for key in ("status", "artifactBytesVerified", "sourceExecutionMetadataBound",
            "sourceNumericalBaselineQualified", "actualCfdRunVerified", "mainApplicationWrites")}))
        return 0
    except Exception as error:
        print(json.dumps({"status": "SNAPPY_PHASE_SOURCE_TRANSFER_CORRECTION_REQUIRED", "action": str(error)[:500],
            "artifactBytesVerified": False, "sourceNumericalBaselineQualified": False, "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
