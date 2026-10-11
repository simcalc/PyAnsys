"""Preserve the pinned STL triangles as two tagged OBJ component surfaces.

This is an explicit float32 surface approximation lane. It does not transfer
native node identity, alter the original volume mesh, heal topology or run CFD.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_boundary_package as source

SCHEMA = "untirta.frozen-rotor-tagged-surface-union.v1"
REPORT = "surface-union-report.json"
SOURCE_REPORT = "source-topology-report.json"
SOURCE_REPORT_BYTES = 68245
SOURCE_REPORT_SHA256 = "1dc7cebaeac04506dcfbe9da3ce123f149cc94cb44c6990b9b11c3f4195d2595"
# Reviewed report from run 38155649489, with only the six declared run/runtime
# fields omitted from canonical JSON. Every remaining nested value is pinned.
SOURCE_TOPOLOGY_SHA256 = "73854afa711a4d7ef23e19ce989ecd959b11dbd4fadb87868355a412649d14b3"
TOPOLOGY_RUN_FIELDS = ("extractedAt", "h5pyVersion", "hdf5Version", "githubRunId", "githubRunAttempt", "githubSourceCommit")
SUPPORTED_READER_PAIRS = (("3.16.0", "2.0.0"),)
MAX_STL_BYTES = 25 * 1024**2
MAX_OBJ_BYTES = 128 * 1024**2
MAX_REPORT_BYTES = 128 * 1024
MAX_MEMORY_BYTES = 4 * 1024**3
OBJ_HEADER = b"# Verified Frozen Rotor STL triangle union; coordinate units UNVERIFIED\n"
# Exact data from the published boundary extraction, native tags unchanged.
STL_PINS = {
    "impeller": (
        (15, "inlet", 4, 6210, 12420, "8465f0e506c746a68469f1bae6729ac75d49d4989358a11f1831a632ff09739b"),
        (14, "inblock-shroud", 3, 5130, 10260, "47009f8d226f3b06959aee02271f1842ec8c4a20c7c34a81a618fdefce9c6a2c"),
        (13, "inblock-hub", 3, 5130, 10260, "5926874335c2ca5b5181ba7ebf0c99ea2357db3c9633f8c5f3132bf58e4d3440"),
        (10, "interface-impeller-outlet", 24, 16974, 33948, "34c241ad2337eec9dbbc1d2dcd975afced0b464e657f382b9b223545ba1b569c"),
        (9, "blade", 3, 36432, 72864, "b7aecdba15b878eefbe986d017f2d43243f04fbbdc65c2814a024c7d45c4b37d"),
        (7, "impeller-shroud", 3, 28494, 56988, "b17ad888b3bc892167f9e4ece48dec587acc0bf0d61d177cb758f619de43f836"),
        (3, "impeller-hub", 3, 28494, 56988, "c7281defa58bea0baef43d8a509e383526bc2bb29aefe486bd534788face8b59"),
    ),
    "volute": (
        (16, "volute-wall", 3, 29991, 29991, "868f94a3b3e96602730a11f78c2d3df9182a81f21a816caec97e1a87aa2181fc"),
        (11, "mass-flow-inlet-11", 20, 196, 321, "3a4a49b0b6ae7dba27be9df1a7e7aaa51158391223afe94bf7eab3c85e961979"),
        (8, "volute-inlet-wall", 3, 6433, 8723, "e6bccfaacb5898a591ef95c64b3667f3f2302c9453192ddc910addebd975edbe"),
        (5, "interface-volute-inlet", 24, 3297, 3297, "d6eff797c98b4a0971d1c435e94e88385e7d51340d6dd4ed9e44f4b7a14fbc0b"),
    ),
}
FALSE_FLAGS = (
    "nativeCoordinateUnitsVerified", "nativeNodeIdentityPreserved", "originalPolygonGeometryVerified",
    "geometricClosureVerified", "geometricSelfIntersectionChecked", "manifoldVertexLinksChecked",
    "outwardOrientationConventionVerified", "nativeSurfaceCheckExecuted", "nativeSurfaceAccepted",
    "volumeMeshGenerated", "meshQualityCheckedBySolver", "nativeCaseGenerated", "fluentSessionConnected",
    "licenseVerified", "actualCfdRunVerified", "simulationExecuted", "qualifiedBaseline", "mainApplicationWrites",
    "geometryHealingPerformed", "triangleSimplificationPerformed", "epsilonWeldingPerformed",
)
TRUE_FLAGS = ("sourceStlFilesImmutable", "everySourceTriangleRetained", "stlFloat32CoordinatesPreserved",
              "stlVertexWindingPreserved", "sourcePatchTagsPreserved", "exactCoordinateIndexDeduplicationPerformed")
REPORT_KEYS = {"schemaVersion", "preparedAt", "exampleDataRepository", "exampleDataCommit", "sourceTopologyReport",
               "sourceTopologySemanticIdentity",
               "sourceStlFiles", "sourceStlBytes", "components", "coordinateUnits", "classroomMetresPerSourceCoordinateUnit",
               "coordinateRepresentation", "derivationMethod", "limits", "claimBoundary", *FALSE_FLAGS, *TRUE_FLAGS}


class SurfaceIssue(ValueError):
    """A surface preparation diagnostic without a solver-readiness claim."""


def require(value, action):
    if not value:
        raise SurfaceIssue(action)


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def read_json(raw):
    try:
        value = json.loads(raw, object_pairs_hook=source._unique_object, parse_constant=source._forbidden_number)
        source._finite_json(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SurfaceIssue("Use finite bounded UTF-8 JSON without duplicate keys.") from exc


def file_row(name, raw):
    return {"path": name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def expected_stls():
    return [{"name": f"{component}-zone-{zone}.stl", "bytes": 84 + 50 * count, "sha256": digest,
             "triangleCount": count, "format": "binary-stl", "sourceMesh": component + ".msh.h5",
             "zoneId": zone, "zoneName": name}
            for component, patches in STL_PINS.items() for zone, name, _, _, count, digest in patches]


def topology_fingerprint(report):
    """Pin the complete technical document while validating exact volatile roles."""
    source.verify_report(report)
    stamp = report.get("extractedAt")
    require(isinstance(stamp, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:\+00:00|Z)", stamp),
            "Extraction time must be a bounded actual UTC ISO timestamp.")
    try:
        require(datetime.fromisoformat(stamp).utcoffset().total_seconds() == 0,
                "Extraction time must retain its UTC offset.")
    except (ValueError, AttributeError) as exc:
        raise SurfaceIssue("Use a valid actual UTC extraction timestamp.") from exc
    require((report.get("h5pyVersion"), report.get("hdf5Version")) in SUPPORTED_READER_PAIRS,
            "Use the explicitly reviewed h5py/HDF5 runtime pair; new versions require a supported-pair review.")
    require(re.fullmatch(r"[1-9][0-9]{0,19}", report["githubRunId"])
            and re.fullmatch(r"[1-9][0-9]{0,3}", report["githubRunAttempt"])
            and re.fullmatch(r"[0-9a-f]{40}", report["githubSourceCommit"]),
            "Current extraction must retain a positive run ID/attempt and exact 40-hex source commit.")
    technical = {key: value for key, value in report.items() if key not in TOPOLOGY_RUN_FIELDS}
    canonical = json.dumps(technical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    return sha256(canonical).hexdigest()


def topology_identity(raw):
    digest = topology_fingerprint(read_json(raw))
    require(digest == SOURCE_TOPOLOGY_SHA256,
            "Every substantive topology value must match the reviewed immutable source report.")
    return {"sha256": digest,
            "canonicalJson": "Sorted keys, compact separators, ASCII-escaped JSON; finite values only.",
            "excludedTopLevelFields": list(TOPOLOGY_RUN_FIELDS),
            "supportedReaderRuntimePairs": [{"h5pyVersion": h5py, "hdf5Version": hdf5} for h5py, hdf5 in SUPPORTED_READER_PAIRS],
            "reviewedReferenceReport": {"bytes": SOURCE_REPORT_BYTES, "sha256": SOURCE_REPORT_SHA256}}


def load_source(directory, report_name="topology-report.json", stl_directory=None):
    raw = source._regular_bytes(directory / report_name, MAX_REPORT_BYTES)
    topology_identity(raw)
    report = read_json(raw)
    require(all(report.get(key) is False for key in source.FALSE_FLAGS)
            and all(report.get(key) is True for key in source.TRUE_FLAGS),
            "A derived surface inventory must not claim native quality or CFD execution.")
    expected = expected_stls()
    require(sorted(report["files"], key=lambda row: row["name"]) == sorted(expected, key=lambda row: row["name"]),
            "All eleven exact published STL files and original zone tags are required.")
    for mesh in report["meshes"]:
        component = mesh["name"].split(".", 1)[0]
        zones = {row["zoneId"]: row for row in mesh["boundaryZones"]}
        require(len(zones) == len(STL_PINS[component]), "Each original boundary tag must occur exactly once.")
        for zone, name, kind, face_count, count, _ in STL_PINS[component]:
            row = zones.get(zone, {})
            require((row.get("name"), row.get("zoneType"), row.get("faceCount"), row.get("triangleCount"))
                    == (name, kind, face_count, count), "Native boundary identity and source face/triangle counts must remain exact.")
    folder = stl_directory if stl_directory is not None else directory
    raw_files = {}
    for row in expected:
        data = source._regular_bytes(folder / row["name"], MAX_STL_BYTES)
        source.verify_stl(data, row)
        raw_files[row["name"]] = data
    require(sum(map(len, raw_files.values())) <= MAX_STL_BYTES, "The combined source STL budget is 25 MiB.")
    return raw, report, raw_files


def subtract(a, b):
    return tuple(x - y for x, y in zip(a, b))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return math.fsum(x * y for x, y in zip(a, b))


def convert_component(component, raw_files):
    """Deterministic exact-float32 indexing; keep every input triangle in order."""
    points, indices, patches, triangles = [], {}, [], []
    for zone, name, kind, face_count, expected_count, _ in STL_PINS[component]:
        stl_name = f"{component}-zone-{zone}.stl"
        raw = raw_files[stl_name]
        require(len(raw) == 84 + expected_count * 50, "Every component patch needs its complete native-derived triangle inventory.")
        start, areas, triangle_digest = len(triangles), [], sha256()
        for offset in range(84, len(raw), 50):
            values = struct.unpack_from("<12fH", raw, offset)
            require(all(math.isfinite(value) for value in values[:-1]) and values[-1] == 0,
                    "STL normals and vertices must be finite, with zero attribute bytes.")
            vertices = [tuple(values[3 + 3 * i:6 + 3 * i]) for i in range(3)]
            area_vector = cross(subtract(vertices[1], vertices[0]), subtract(vertices[2], vertices[0]))
            area = 0.5 * math.hypot(*area_vector)
            require(math.isfinite(area) and area > 0 and len(set(vertices)) == 3,
                    "Reject collapsed or zero-area stored float32 triangles; do not heal them.")
            areas.append(area)
            triangle_digest.update(raw[offset + 12:offset + 48])
            face = []
            for vertex in vertices:
                bits = struct.pack("<3f", *vertex)
                if bits not in indices:
                    indices[bits] = len(points) + 1
                    points.append(vertex)
                face.append(indices[bits])
            triangles.append(tuple(face))
        patches.append({"name": name, "sourceZoneId": zone, "sourceZoneType": kind, "sourceNativeFaceCount": face_count,
                        "triangleCount": expected_count, "firstTriangleIndex": start,
                        "surfaceAreaNativeUnitsSquared": math.fsum(areas),
                        "orderedTriangleFloat32CoordinatesSha256": triangle_digest.hexdigest(),
                        "sourceStlFile": {"name": stl_name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}})
    require(points and triangles, "Both actual components need nonempty surfaces.")
    minima = [min(point[i] for point in points) for i in range(3)]
    maxima = [max(point[i] for point in points) for i in range(3)]
    origin = tuple((a + b) / 2 for a, b in zip(minima, maxima))
    edges, seen, volume_terms = {}, set(), []
    duplicates = 0
    for face in triangles:
        canonical = tuple(sorted(face))
        duplicates += canonical in seen
        seen.add(canonical)
        for a, b in zip(face, (face[1], face[2], face[0])):
            key = (min(a, b), max(a, b))
            count, winding = edges.get(key, (0, 0))
            edges[key] = (count + 1, winding + (1 if a < b else -1))
        a, b, c = [subtract(points[index - 1], origin) for index in face]
        volume_terms.append(dot(a, cross(b, c)) / 6)
    volume = math.fsum(volume_terms)
    open_edges = sum(count == 1 for count, _ in edges.values())
    nonmanifold = sum(count > 2 for count, _ in edges.values())
    conflicts = sum(count == 2 and winding != 0 for count, winding in edges.values())
    require(math.isfinite(volume), "Stored triangle volume reduction must remain finite.")
    obj = BytesIO()
    obj.write(OBJ_HEADER)
    for point in points:
        words = [format(value, ".17g") for value in point]
        require(struct.pack("<3f", *(float(word) for word in words)) == struct.pack("<3f", *point),
                "OBJ decimal serialization must exactly round-trip every stored float32 coordinate.")
        obj.write(("v " + " ".join(words) + "\n").encode("ascii"))
    for patch in patches:
        obj.write(("g " + patch["name"] + "\n").encode("ascii"))
        start = patch["firstTriangleIndex"]
        for face in triangles[start:start + patch["triangleCount"]]:
            obj.write(("f " + " ".join(map(str, face)) + "\n").encode("ascii"))
        require(obj.tell() <= MAX_OBJ_BYTES, "Combined OBJ storage must remain bounded to 128 MiB.")
    raw = obj.getvalue()
    audit = {"uniqueIndexedEdgeCount": len(edges), "openEdgeCount": open_edges,
             "nonManifoldEdgeCount": nonmanifold, "orientationConflictEdgeCount": conflicts,
             "duplicateIndexedTriangleCount": duplicates,
             "closedIndexedEdgeIncidence": open_edges == nonmanifold == 0,
             "consistentIndexedWinding": open_edges == nonmanifold == conflicts == 0,
             "claim": "Exact float32 coordinate-index incidence only; native node IDs, manifold vertex links and geometric self intersection are unverified."}
    row = {"component": component, "surfaceFile": file_row(component + "-union.obj", raw),
           "triangleCount": len(triangles), "vertexCount": len(points), "patches": patches,
           "coordinateBoundsNativeUnits": {"minimum": minima, "maximum": maxima}, "coordinateIndexAudit": audit,
           "surfaceAreaNativeUnitsSquared": math.fsum(patch["surfaceAreaNativeUnitsSquared"] for patch in patches),
           "signedTriangleVolumeNativeUnitsCubed": volume,
           "signedTriangleVolumeEquationStages": {
               "input": {"triangleCount": len(triangles), "referenceOriginNativeUnits": list(origin), "coordinateUnits": "UNVERIFIED"},
               "equation": "V_signed = (1/6) sum_i [(a_i-o) dot ((b_i-o) cross (c_i-o))]",
               "substitution": {"signedTetrahedralTerms": len(triangles), "sumSignedTermsNativeUnitsCubed": volume},
               "result": {"signedVolumeNativeUnitsCubed": volume, "signedVolumeAssumedM3": volume,
                          "classroomMetresPerSourceCoordinateUnit": 1,
                          "claim": "Oriented stored-triangle integration only; no outward convention or geometrically valid enclosed volume is verified."}},
           "everySourceTriangleRetained": True, "stlFloat32CoordinatesPreserved": True, "stlVertexWindingPreserved": True,
           "sourcePatchTagsPreserved": True}
    return raw, row


def report_value(raw_topology, components):
    result = {"schemaVersion": SCHEMA, "preparedAt": datetime.now(timezone.utc).isoformat(),
              "exampleDataRepository": "https://github.com/ansys/example-data", "exampleDataCommit": source.PIN,
              "sourceTopologyReport": file_row(SOURCE_REPORT, raw_topology),
              "sourceTopologySemanticIdentity": topology_identity(raw_topology), "sourceStlFiles": expected_stls(),
              "sourceStlBytes": sum(row["bytes"] for row in expected_stls()), "components": components,
              "coordinateUnits": "UNVERIFIED", "classroomMetresPerSourceCoordinateUnit": 1,
              "coordinateRepresentation": "Original binary STL IEEE-754 float32 vertices serialized with 17 significant decimal digits; original HDF5 float64 polygon geometry is not reproduced.",
              "derivationMethod": "Retain all tagged STL triangles in original order and winding. Reuse an OBJ vertex only for identical little-endian float32 coordinate bit patterns, including signed zero; this indexing-only operation does not establish native-node identity or perform epsilon welding.",
              "limits": {"sourceStlBytes": MAX_STL_BYTES, "combinedObjBytes": MAX_OBJ_BYTES,
                         "eachReportBytes": MAX_REPORT_BYTES, "addressSpaceBytes": MAX_MEMORY_BYTES},
              "claimBoundary": "Tagged surface preparation only. Exact coordinate indexing and edge incidence do not establish geometric closure, self-intersection absence, native volume mesh quality, solver readiness, cavitation, NPSHr or transient results."}
    result.update({key: False for key in FALSE_FLAGS})
    result.update({key: True for key in TRUE_FLAGS})
    return result


def runner_paths(input_directory, output_directory, runner_temp):
    require(runner_temp is not None, "Use an explicit RUNNER_TEMP directory for isolated surface preparation.")
    root = Path(runner_temp)
    inputs, output = source._inside(Path(input_directory), root), source._inside(Path(output_directory), root)
    require(inputs.is_dir() and not output.exists() and not output.is_relative_to(inputs) and not inputs.is_relative_to(output),
            "Use an existing isolated input directory and a fresh independent output directory.")
    return inputs, output


def prepare(surface_directory, output_directory, *, runner_temp=None):
    inputs, output = runner_paths(surface_directory, output_directory, runner_temp or os.environ.get("RUNNER_TEMP"))
    raw_topology, _, raw_files = load_source(inputs)
    require({path.name for path in inputs.iterdir()} == {"topology-report.json", *raw_files},
            "The surface source directory may contain only its exact report and eleven STL files.")
    components, objs = [], {}
    for component in STL_PINS:
        raw, row = convert_component(component, raw_files)
        components.append(row)
        objs[row["surfaceFile"]["path"]] = raw
    require(sum(map(len, objs.values())) <= MAX_OBJ_BYTES, "The combined OBJ budget is 128 MiB.")
    result = report_value(raw_topology, components)
    report_bytes = encoded(result)
    require(len(report_bytes) <= MAX_REPORT_BYTES, "The surface union report budget is 128 KiB.")
    # Validate all inputs again before creating output; preserve the exact source bytes.
    require(source._regular_bytes(inputs / "topology-report.json", MAX_REPORT_BYTES) == raw_topology
            and all(source._regular_bytes(inputs / name, MAX_STL_BYTES) == raw for name, raw in raw_files.items()),
            "Source report or STL bytes changed during surface preparation.")
    written = []
    try:
        output.mkdir(parents=True, exist_ok=False)
        (output / "source-stl").mkdir()
        payloads = {SOURCE_REPORT: raw_topology, **objs, REPORT: report_bytes,
                    **{"source-stl/" + name: raw for name, raw in raw_files.items()}}
        for name, raw in payloads.items():
            target = output / name
            written.append(target)
            with target.open("xb") as stream:
                require(stream.write(raw) == len(raw), "A surface output write was incomplete.")
        verify_output(output, runner_temp=runner_temp or os.environ.get("RUNNER_TEMP"))
        require(source._regular_bytes(inputs / "topology-report.json", MAX_REPORT_BYTES) == raw_topology
                and all(source._regular_bytes(inputs / name, MAX_STL_BYTES) == raw for name, raw in raw_files.items()),
                "Original STL bytes must remain unchanged after output verification.")
    except Exception:
        for target in reversed(written):
            if target.exists() and target.is_file():
                target.unlink()
        if (output / "source-stl").exists():
            (output / "source-stl").rmdir()
        if output.exists():
            output.rmdir()
        raise
    return result


def verify_output(output_directory, *, runner_temp=None):
    require(runner_temp or os.environ.get("RUNNER_TEMP"), "Verify surfaces only inside the isolated RUNNER_TEMP workspace.")
    root = source._inside(Path(output_directory), Path(runner_temp or os.environ["RUNNER_TEMP"]))
    require(root.is_dir() and {path.name for path in root.iterdir()}
            == {REPORT, SOURCE_REPORT, "source-stl", "impeller-union.obj", "volute-union.obj"},
            "A verified surface union may contain only its exact two surfaces, reports and STL copies.")
    source._no_links(root / "source-stl")
    raw_topology, _, raw_files = load_source(root, SOURCE_REPORT, root / "source-stl")
    require({path.name for path in (root / "source-stl").iterdir()} == set(raw_files), "No unlisted source STL copy is permitted.")
    report_raw = source._regular_bytes(root / REPORT, MAX_REPORT_BYTES)
    result = read_json(report_raw)
    require(isinstance(result, dict) and set(result) == REPORT_KEYS and result.get("schemaVersion") == SCHEMA,
            "Use only the exact reviewed surface-union report schema.")
    require(isinstance(result["preparedAt"], str) and len(result["preparedAt"]) <= 40, "Keep bounded preparation time provenance.")
    try:
        require(datetime.fromisoformat(result["preparedAt"]).utcoffset().total_seconds() == 0, "Preparation time must record UTC.")
    except (ValueError, AttributeError) as exc:
        raise SurfaceIssue("Use an actual UTC preparation timestamp.") from exc
    actual_components, obj_bytes = [], 0
    for component in STL_PINS:
        expected, row = convert_component(component, raw_files)
        actual = source._regular_bytes(root / row["surfaceFile"]["path"], MAX_OBJ_BYTES)
        require(actual == expected, "OBJ bytes must reproduce every exact pinned STL coordinate, triangle winding and patch tag.")
        actual_components.append(row)
        obj_bytes += len(actual)
    require(obj_bytes <= MAX_OBJ_BYTES, "Combined OBJ files exceed the reviewed limit.")
    expected_report = report_value(raw_topology, actual_components)
    expected_report["preparedAt"] = result["preparedAt"]
    require(result == expected_report, "Every surface metric, provenance binding, resource limit and no-CFD flag must match actual bytes.")
    return {"report": result, "reportFile": file_row(REPORT, report_raw), "components": actual_components}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        if sys.platform == "linux":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
        report = prepare(args.surface_directory, args.output_directory)
        print(json.dumps({"schemaVersion": SCHEMA, "status": "TaggedSurfacePreparedNativeSurfaceCheckRequired",
                          "components": [{"component": row["component"], "vertexCount": row["vertexCount"],
                                          "triangleCount": row["triangleCount"], "surfaceFile": row["surfaceFile"]}
                                         for row in report["components"]],
                          "nativeSurfaceAccepted": False, "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 0
    except (SurfaceIssue, source.PackageIssue, OSError, ValueError, MemoryError) as exc:
        print(json.dumps({"schemaVersion": SCHEMA, "status": "SurfaceInputCorrectionRequired", "action": str(exc)[:400],
                          "nativeSurfaceAccepted": False, "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
