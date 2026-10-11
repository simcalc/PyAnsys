"""Package only verified derived boundary data, never source code or a solver."""
from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
from zipfile import ZipFile, ZIP_DEFLATED

REPOSITORY = "simcalc/PyAnsys"
SCHEMA = "untirta.frozen-rotor-boundary-extraction.v1"
MAX_OUTPUT_BYTES = 25 * 1024 * 1024
MAX_REPORT_BYTES = 128 * 1024
LICENSE_BYTES = 1114
LICENSE_SHA256 = "46617266d39d9ecdd829d29ef6e9a237e9312e71531ddc258e2303e28ab041db"
STL_HEADER = b"PyAnsys Frozen Rotor derived boundary; native coordinate units UNVERIFIED".ljust(80, b"\0")
NAME_PATTERN = re.compile(r"(impeller|volute)-zone-([1-9][0-9]*)\.stl\Z")
FALSE_FLAGS = ("nativeCoordinateUnitsVerified", "volumeMeshGenerated", "meshQualityCheckedBySolver",
               "nativeCaseGenerated", "fluentSessionConnected", "licenseVerified",
               "actualCfdRunVerified", "simulationExecuted", "mainApplicationWrites")
TRUE_FLAGS = ("originalMeshesImmutable", "surfaceDerived")
FILE_KEYS = {"name", "bytes", "sha256", "triangleCount", "format", "sourceMesh", "zoneId", "zoneName"}
REPORT_KEYS = {"schemaVersion", "exampleDataRepository", "exampleDataCommit", "extractedAt", "h5pyVersion", "hdf5Version",
               "githubRepository", "githubRunId", "githubRunAttempt", "githubSourceCommit", "coordinateUnits", "derivationMethod",
               "limits", "meshes", "files", "sourceFileBytes", "derivedSurfaceBytes", "originalMeshesImmutable",
               "nativeCoordinateUnitsVerified", "surfaceDerived", "volumeMeshGenerated", "meshQualityCheckedBySolver",
               "nativeCaseGenerated", "fluentSessionConnected", "licenseVerified", "actualCfdRunVerified", "simulationExecuted",
               "mainApplicationWrites", "claimBoundary"}
MESH_KEYS = {"name", "bytes", "sha256", "sourceUrl", "sourceSha256AfterExtraction", "nativeCounts", "sections", "nativeTopology",
             "faceZones", "boundaryZones", "boundaryFaceCount", "triangleCount", "boundaryTopologyAudit", "triangulationAudit",
             "signedEnclosedVolumeNativeUnitsCubed", "signedVolumeEquationStages"}
PIN = "8ef505f6ef699eeec68a16001df91ba353342f40"
MESH_SOURCES = {
    "impeller.msh.h5": (35217934, "541e06f11cf8aac89d18fe1fe26572667879f3b24b40ea5223cac63e10eb8d50"),
    "volute.msh.h5": (13302081, "edc570cae6d2a7a1ad8d1753ebf5dd5b68accbff1819df2a8b3e20fe2c46c8b3"),
}

README = """Frozen Rotor derived boundary surfaces — educational data only

This package contains the exact binary STL files referenced by topology-report.json,
that read-only report, and the original upstream example-data MIT license notice.
Surfaces are derived from the two immutable official HDF5 meshes. Original mesh
files are not modified or included in this boundary package.

Native coordinate units are UNVERIFIED. Do not label these STL coordinates as
metres, scale them silently, or import them as an exact manufacturer pump model.
Zone identity, source mesh provenance, triangle count, file length and SHA-256 are
recorded in the report. Preserve these records before any separate derived work.

This extraction does NOT generate a volume mesh, joined Fluent case, a solver
session, a quality-qualified mesh, CFD, cavitation, NPSHr or physical transient
results. A topology inventory is not a native mesh-quality or solver check.
No Fluent, gmsh or other native solver was launched. Main application inputs,
results, pump database, credentials and deployment are not part of this package.

Before a separate learning mesh can be used, verify coordinate units, boundary
roles, geometry/interface integrity, mesh quality, resource limits, fluid and
pressure datum, solver/license availability and relevant numerical criteria.
The installed PyFluent SDK and a checked surface alone do not establish CFD ready.

SHA256SUMS verifies all separate release files except itself. topology-report.json
also verifies every exact STL file inside the ZIP. No additional directory files
are bundled or executed. Standard public GitHub hosted runner only; no paid fallback.

Official example:
https://fluent.docs.pyansys.com/version/stable/examples/00-fluent/frozen_rotor_workflow.html
"""


class PackageIssue(ValueError):
    """A bounded, reviewable packaging diagnostic."""


def _no_links(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PackageIssue("Symlinks and junctions are prohibited in package paths.")


def _inside(path: Path, runner_temp: Path) -> Path:
    _no_links(runner_temp)
    _no_links(path)
    base, result = runner_temp.resolve(strict=True), path.resolve(strict=False)
    if not base.is_dir() or result == base or not result.is_relative_to(base):
        raise PackageIssue("Input and fresh output must remain inside RUNNER_TEMP.")
    return result


def _regular_bytes(path: Path, maximum: int) -> bytes:
    _no_links(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise PackageIssue("Use only bounded regular package input files.")
    raw = path.read_bytes()
    if len(raw) != info.st_size or len(raw) > maximum:
        raise PackageIssue("Package file changed or exceeded its byte limit.")
    return raw


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PackageIssue("Duplicate JSON keys are prohibited.")
        result[key] = value
    return result


def _forbidden_number(value):
    raise PackageIssue("Non-finite JSON numbers are prohibited.")


def _integer(value, maximum: int, minimum: int = 1) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _finite_json(value, depth=0):
    if depth > 24:
        raise PackageIssue("Topology JSON nesting exceeds the bounded data contract.")
    if isinstance(value, float) and not math.isfinite(value):
        raise PackageIssue("Topology report must contain finite numerical values.")
    if isinstance(value, dict):
        for item in value.values():
            _finite_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _finite_json(item, depth + 1)


def verify_report(report):
    if not isinstance(report, dict) or set(report) != REPORT_KEYS or report.get("schemaVersion") != SCHEMA:
        raise PackageIssue("Use only the exact reviewed topology report fields and schema.")
    _finite_json(report)
    if (report.get("exampleDataRepository") != "https://github.com/ansys/example-data"
            or report.get("exampleDataCommit") != PIN or report.get("githubRepository") != REPOSITORY
            or report.get("coordinateUnits") != "UNVERIFIED"):
        raise PackageIssue("Preserve the pinned official source and unverified coordinate units.")
    if (not isinstance(report.get("githubRunId"), str) or not re.fullmatch(r"[0-9]{1,20}", report["githubRunId"])
            or not isinstance(report.get("githubRunAttempt"), str) or not re.fullmatch(r"[0-9]{1,4}", report["githubRunAttempt"])
            or not isinstance(report.get("githubSourceCommit"), str) or not re.fullmatch(r"[0-9a-f]{40}", report["githubSourceCommit"])):
        raise PackageIssue("Use bounded GitHub run and immutable commit provenance.")
    meshes = report.get("meshes")
    if not isinstance(meshes, list) or len(meshes) != 2 or any(not isinstance(mesh, dict) for mesh in meshes):
        raise PackageIssue("Both exact immutable source meshes must be represented.")
    if {mesh.get("name") for mesh in meshes} != set(MESH_SOURCES):
        raise PackageIssue("Source mesh identity differs from the official two-mesh example.")
    boundaries = []
    for mesh in meshes:
        if set(mesh) != MESH_KEYS:
            raise PackageIssue("Use only the reviewed mesh inventory fields.")
        size, digest = MESH_SOURCES[mesh["name"]]
        expected_url = f"https://media.githubusercontent.com/media/ansys/example-data/{PIN}/pyfluent/examples/pump-volute/{mesh['name']}"
        if (mesh.get("bytes") != size or mesh.get("sha256") != digest or mesh.get("sourceSha256AfterExtraction") != digest
                or mesh.get("sourceUrl") != expected_url):
            raise PackageIssue("Original mesh provenance must match the pinned immutable source.")
        zones = mesh.get("boundaryZones")
        if not isinstance(zones, list) or not 1 <= len(zones) <= 128:
            raise PackageIssue("Each mesh must reference a bounded boundary-zone manifest.")
        for zone in zones:
            if not isinstance(zone, dict) or not isinstance(zone.get("stlFile"), dict) or zone["stlFile"].get("sourceMesh") != mesh["name"]:
                raise PackageIssue("Every boundary zone must bind its exact source STL descriptor.")
            boundaries.append(zone["stlFile"])
    files = report.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 128 or any(not isinstance(item, dict) for item in files):
        raise PackageIssue("Use a bounded explicit STL manifest.")
    if sorted(boundaries, key=lambda item: str(item.get("name"))) != sorted(files, key=lambda item: str(item.get("name"))):
        raise PackageIssue("Top-level STL inventory must match every native boundary-zone descriptor.")
    if type(report.get("sourceFileBytes")) is not int or report["sourceFileBytes"] != sum(size for size, _ in MESH_SOURCES.values()):
        raise PackageIssue("Source byte count must match both immutable upstream meshes.")
    if (any(type(item.get("bytes")) is not int for item in files) or type(report.get("derivedSurfaceBytes")) is not int
            or report["derivedSurfaceBytes"] != sum(item["bytes"] for item in files)):
        raise PackageIssue("Derived surface byte count must match the exact STL manifest.")


def verify_stl(raw: bytes, descriptor: dict) -> None:
    if set(descriptor) != FILE_KEYS:
        raise PackageIssue("Use only the exact STL descriptor fields.")
    name = descriptor.get("name")
    match = NAME_PATTERN.fullmatch(name) if isinstance(name, str) else None
    if not match or descriptor.get("format") != "binary-stl":
        raise PackageIssue("Use only the exact named binary STL allowlist.")
    if descriptor.get("sourceMesh") != match[1] + ".msh.h5" or type(descriptor.get("zoneId")) is not int or descriptor["zoneId"] != int(match[2]):
        raise PackageIssue("STL filename, native zone and source mesh must match.")
    zone = descriptor.get("zoneName")
    if not isinstance(zone, str) or not re.fullmatch(r"[A-Za-z0-9_. -]{1,80}", zone):
        raise PackageIssue("Use a bounded native zone label.")
    count = descriptor.get("triangleCount")
    if not _integer(count, (MAX_OUTPUT_BYTES - 84) // 50) or not _integer(descriptor.get("bytes"), MAX_OUTPUT_BYTES):
        raise PackageIssue("STL triangle count and size must be bounded integers.")
    if len(raw) != descriptor["bytes"] or len(raw) != 84 + 50 * count or raw[:80] != STL_HEADER or struct.unpack_from("<I", raw, 80)[0] != count:
        raise PackageIssue("Binary STL header, declared count and exact length disagree.")
    digest = descriptor.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or sha256(raw).hexdigest() != digest:
        raise PackageIssue("STL SHA-256 differs from its manifest.")
    for values in struct.iter_unpack("<12fH", memoryview(raw)[84:]):
        if any(not math.isfinite(value) for value in values[:-1]) or values[-1] != 0:
            raise PackageIssue("Binary STL triangles must contain finite floats and zero attribute bytes.")


def build(directory: Path, output: Path, license_path: Path, *, runner_temp: Path) -> dict:
    directory, output = _inside(directory, runner_temp), _inside(output, runner_temp)
    if not directory.is_dir() or output == directory or output.is_relative_to(directory) or directory.is_relative_to(output):
        raise PackageIssue("Use separate bounded input and fresh release directories.")
    if output.exists():
        raise PackageIssue("Release output must be a fresh path; existing files are never replaced.")
    report_raw = _regular_bytes(directory / "topology-report.json", MAX_REPORT_BYTES)
    try:
        report = json.loads(report_raw, object_pairs_hook=_unique_object, parse_constant=_forbidden_number)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PackageIssue("Use the exact valid topology JSON report.") from error
    verify_report(report)
    for key in FALSE_FLAGS:
        if report.get(key) is not False:
            raise PackageIssue("Native units, volume mesh, solver and calculation claims must remain unverified: " + key)
    for key in TRUE_FLAGS:
        if report.get(key) is not True:
            raise PackageIssue("Original meshes and derived surface roles must be explicit: " + key)
    descriptors = report.get("files")
    if not isinstance(descriptors, list) or not 1 <= len(descriptors) <= 128 or any(not isinstance(item, dict) for item in descriptors):
        raise PackageIssue("Use a bounded explicit STL manifest.")
    names = [item.get("name") for item in descriptors]
    if any(not isinstance(name, str) or not NAME_PATTERN.fullmatch(name) for name in names) or len(set(names)) != len(names):
        raise PackageIssue("Use unique native boundary STL basenames.")
    expected = set(names) | {"topology-report.json"}
    if {entry.name for entry in directory.iterdir()} != expected:
        raise PackageIssue("Input includes an arbitrary file or directory outside the exact manifest.")
    data = {"topology-report.json": report_raw}
    for descriptor in descriptors:
        raw = _regular_bytes(directory / descriptor["name"], MAX_OUTPUT_BYTES)
        verify_stl(raw, descriptor)
        data[descriptor["name"]] = raw
    if sum(map(len, data.values())) > MAX_OUTPUT_BYTES:
        raise PackageIssue("Boundary input exceeds the total 25 MiB limit.")
    license_raw = _regular_bytes(license_path, LICENSE_BYTES)
    if len(license_raw) != LICENSE_BYTES or sha256(license_raw).hexdigest() != LICENSE_SHA256:
        raise PackageIssue("Preserve the exact upstream 1114-byte license notice.")
    data["EXAMPLE_DATA_LICENSE.txt"] = license_raw
    data["README-boundaries.txt"] = README.encode("utf-8")
    if sum(map(len, data.values())) > MAX_OUTPUT_BYTES:
        raise PackageIssue("The exact boundary package exceeds 25 MiB.")
    # Construct and budget every asset before creating a release directory.
    archive_buffer = BytesIO()
    with ZipFile(archive_buffer, "w", ZIP_DEFLATED, compresslevel=6) as bundle:
        for name, raw in sorted(data.items()):
            bundle.writestr(name, raw)
    assets = {name: data[name] for name in ("topology-report.json", "EXAMPLE_DATA_LICENSE.txt", "README-boundaries.txt")}
    assets["frozen-rotor-boundaries.zip"] = archive_buffer.getvalue()
    sums = "".join(sha256(raw).hexdigest() + "  " + name + "\n" for name, raw in sorted(assets.items()))
    assets["SHA256SUMS"] = sums.encode("utf-8")
    if sum(map(len, assets.values())) > MAX_OUTPUT_BYTES:
        raise PackageIssue("Combined release assets exceed the total 25 MiB limit; do not publish.")
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in assets.items():
        (output / name).write_bytes(raw)
    archive_raw = assets["frozen-rotor-boundaries.zip"]
    return {"packageVerified": True, "archive": "frozen-rotor-boundaries.zip", "archiveBytes": len(archive_raw),
            "archiveSha256": sha256(archive_raw).hexdigest(), "stlCount": len(descriptors),
            "reportSha256": sha256(report_raw).hexdigest(), "nativeCoordinateUnitsVerified": False,
            "volumeMeshGenerated": False, "simulationExecuted": False, "mainApplicationWrites": False}


def main():
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY or os.environ.get("GITHUB_ACTIONS") != "true":
        raise PackageIssue("Publication packaging is limited to this independent GitHub repository.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-directory", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    args = parser.parse_args()
    runner_temp = Path(os.environ["RUNNER_TEMP"])
    license_path = Path(__file__).resolve().parents[1] / "resources/EXAMPLE_DATA_LICENSE.txt"
    print(json.dumps(build(args.input_directory, args.output_directory, license_path, runner_temp=runner_temp)))


if __name__ == "__main__":
    main()
