"""Derive tagged boundary STL from the two pinned native meshes; never solve CFD.

All HDF5 sections are mapped by their explicit global ID ranges. Boundaries are
selected by cell adjacency, not by names. Native polygon winding/node IDs remain
unchanged. STL triangulation uses a fan after a dominant-plane projection check;
vertices remain native coordinates, rounded only by binary STL's float32 format.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from numbers import Integral
import os
from pathlib import Path
import re
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inspect_frozen_rotor_mesh as inventory

SCHEMA = "untirta.frozen-rotor-boundary-extraction.v1"
MAX_BUNDLE_BYTES = 25 * 1024**2
MAX_NODES = 5_000_000
MAX_FACES = 3_000_000
MAX_CELLS = 3_000_000
MAX_BOUNDARY_FACES = 1_000_000
MAX_ZONES = 32
MAX_SECTIONS = 128
MAX_FACE_NODES = 128
MAX_CONNECTIVITY_VALUES = 32_000_000
STL_HEADER = b"PyAnsys Frozen Rotor derived boundary; native coordinate units UNVERIFIED".ljust(80, b"\0")
TRIANGLE = struct.Struct("<12fH")


class ExtractionIssue(inventory.InspectionIssue):
    pass


def checked_range_sections(sections: list[dict], total: int) -> list[dict]:
    """Confirm complete, disjoint 1-based global IDs, independently of section names."""
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0 or not 1 <= len(sections) <= MAX_SECTIONS:
        raise ExtractionIssue("Use positive bounded native counts and explicit sections.")
    ordered = sorted(sections, key=lambda row: row["minimumId"])
    next_id = 1
    for row in ordered:
        low, high, count = row["minimumId"], row["maximumId"], row["recordCount"]
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (low, high, count)) or low != next_id or high < low or high - low + 1 != count:
            raise ExtractionIssue("Every native section must match its inclusive range without gaps or overlaps.")
        next_id = high + 1
    if next_id != total + 1:
        raise ExtractionIssue("All sections must cover the complete confirmed native ID range.")
    return ordered


def is_true_boundary(c0: int, c1: int, cell_count: int) -> bool:
    if any(isinstance(value, bool) or not isinstance(value, Integral) or value < 0 or value > cell_count for value in (c0, c1)) or (c0 == c1):
        raise ExtractionIssue("Each native face requires valid distinct adjacent cells; zero denotes exterior.")
    return (c0 == 0) != (c1 == 0)


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2], a[0]*b[1] - a[1]*b[0])


def _dot(a, b):
    return math.fsum(x*y for x, y in zip(a, b))


def _float32_point(point):
    try:
        result = struct.unpack("<3f", struct.pack("<3f", *point))
    except (OverflowError, struct.error):
        raise ExtractionIssue("Native boundary coordinates exceed finite binary STL float32 representation.") from None
    if not all(math.isfinite(value) for value in result):
        raise ExtractionIssue("Binary STL coordinates must remain finite.")
    return result


def face_geometry(node_ids, coordinates):
    """Validate a simple convex projection before a native-order fan; do not heal."""
    if not 3 <= len(node_ids) <= MAX_FACE_NODES or len(set(node_ids)) != len(node_ids):
        raise ExtractionIssue("A boundary polygon must have 3–128 distinct native node IDs.")
    vertices = [tuple(float(value) for value in coordinates[identifier]) for identifier in node_ids]
    if any(len(point) != 3 or not all(math.isfinite(value) for value in point) for point in vertices):
        raise ExtractionIssue("Every boundary vertex must retain three finite native coordinates.")
    quantized = [_float32_point(point) for point in vertices]
    shifted = [_sub(point, vertices[0]) for point in vertices]
    terms = [_cross(shifted[index], shifted[(index + 1) % len(shifted)]) for index in range(len(shifted))]
    normal = tuple(math.fsum(term[axis] for term in terms) for axis in range(3))
    norm = math.hypot(*normal)
    if not math.isfinite(norm) or norm == 0:
        raise ExtractionIssue("A degenerate boundary polygon cannot be triangulated.")
    axis = max(range(3), key=lambda index: abs(normal[index]))
    projected = [tuple(value for index, value in enumerate(point) if index != axis) for point in shifted]
    def cross2(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
    turns = [cross2(projected[index-1], projected[index], projected[(index+1) % len(projected)]) for index in range(len(projected))]
    if any(not math.isfinite(value) or value == 0 for value in turns) or min(turns) < 0 < max(turns):
        raise ExtractionIssue("The projected polygon is degenerate or nonconvex; no implicit triangulation repair is permitted.")
    sign = 1 if turns[0] > 0 else -1
    # Convex winding alone is insufficient for a crossed/star polygon.
    area = math.fsum(projected[i][0]*projected[(i+1) % len(projected)][1] - projected[(i+1) % len(projected)][0]*projected[i][1] for i in range(len(projected)))
    fan_area = math.fsum(cross2(projected[0], projected[i], projected[i+1]) for i in range(1, len(projected)-1))
    if not math.isfinite(area) or sign*area <= 0 or not math.isclose(area, fan_area, rel_tol=1e-12, abs_tol=0) or any(sign*cross2(projected[0], projected[i], projected[i+1]) <= 0 for i in range(1, len(projected)-1)):
        raise ExtractionIssue("A crossed or degenerate projection cannot produce a validated fan.")
    # Explicit nonadjacent segment crossings, without changing native geometry.
    for left in range(len(projected)):
        for right in range(left+1, len(projected)):
            if right == left+1 or (left == 0 and right == len(projected)-1):
                continue
            a, b = projected[left], projected[(left+1) % len(projected)]
            c, d = projected[right], projected[(right+1) % len(projected)]
            if cross2(a,b,c)*cross2(a,b,d) <= 0 and cross2(c,d,a)*cross2(c,d,b) <= 0:
                raise ExtractionIssue("The projected polygon has crossed or touching nonadjacent edges.")
    unit = tuple(value/norm for value in normal)
    deviation = max(abs(_dot(point, unit)) for point in shifted)
    scale = max(math.hypot(*point) for point in shifted)
    max_quantization = max(abs(a-b) for before, after in zip(vertices, quantized) for a, b in zip(before, after))
    native_triangles, stl_triangles = [], []
    for index in range(1, len(vertices)-1):
        native = (vertices[0], vertices[index], vertices[index+1])
        points = (quantized[0], quantized[index], quantized[index+1])
        vector = _cross(_sub(points[1], points[0]), _sub(points[2], points[0]))
        length = math.hypot(*vector)
        if not math.isfinite(length) or length == 0:
            raise ExtractionIssue("A degenerate triangle after float32 quantization cannot be exported.")
        native_vector = _cross(_sub(native[1], native[0]), _sub(native[2], native[0]))
        if math.hypot(*native_vector) == 0 or _dot(vector, native_vector) <= 0:
            raise ExtractionIssue("Quantization cannot reverse or collapse native triangle winding.")
        native_triangles.append(native)
        stl_triangles.append((tuple(value/length for value in vector), points))
    return native_triangles, stl_triangles, {"projectionAxis": axis, "maximumPlaneDeviationNativeUnits": deviation,
        "nonplanarAtRelative1e12": deviation > 1e-12*scale, "maximumCoordinateQuantizationErrorNativeUnits": max_quantization}


def audit_native_topology(faces: list[tuple[int, ...]]) -> dict:
    edges, seen, nodes, duplicates = {}, set(), set(), 0
    for face in faces:
        if len(face) < 3 or len(set(face)) != len(face):
            raise ExtractionIssue("Repeated-node or degenerate boundary faces are prohibited.")
        pivot = face.index(min(face))
        forward = face[pivot:] + face[:pivot]
        reverse = (forward[0],) + tuple(reversed(forward[1:]))
        canonical = min(forward, reverse)
        duplicates += canonical in seen
        seen.add(canonical)
        nodes.update(face)
        for index, start in enumerate(face):
            end = face[(index+1) % len(face)]
            edge = (min(start, end), max(start, end))
            count, balance = edges.get(edge, (0, 0))
            edges[edge] = (count+1, balance+(1 if start < end else -1))
    open_edges = sum(count == 1 for count, _ in edges.values())
    nonmanifold = sum(count > 2 for count, _ in edges.values())
    conflicts = sum(count == 2 and balance != 0 for count, balance in edges.values())
    closed = bool(faces and not open_edges and not nonmanifold and not duplicates)
    oriented = closed and not conflicts
    return {"boundaryFaceCount": len(faces), "uniqueNativeNodeCount": len(nodes), "uniqueNativeEdgeCount": len(edges),
        "openEdgeCount": open_edges, "nonManifoldEdgeCount": nonmanifold, "orientationConflictEdgeCount": conflicts,
        "duplicateFaceCount": duplicates, "repeatedVertexFaceCount": 0, "closedEdgeIncidence": closed,
        "consistentNativeWinding": oriented, "outwardOrientationConventionVerified": False,
        "geometricSelfIntersectionChecked": False, "manifoldVertexLinksChecked": False,
        "claim": "Native node-ID edge incidence and winding only; not a watertight geometric or valid-volume-mesh proof."}


def _attribute_int(obj, name: str) -> int:
    if name not in obj.attrs:
        raise ExtractionIssue("An explicit native section/count attribute is required: " + obj.name + "/@" + name)
    value = obj.attrs[name]
    if hasattr(value, "size"):
        if value.size != 1:
            raise ExtractionIssue("Native range/count attributes must contain one integer.")
        value = value.reshape(-1)[0]
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
        raise ExtractionIssue("Native section ranges/counts must be positive integers.")
    return int(value)


def _dataset(obj, h5py, *, integer=False, count=None):
    if not isinstance(obj, h5py.Dataset) or obj.shape is None or (integer and (len(obj.shape) != 1 or obj.dtype.kind not in "iu")) or (count is not None and obj.shape != (count,)):
        raise ExtractionIssue("Native numeric datasets must match the exact supported shape and type.")
    return obj


def _section_rows(group, h5py, *, coordinate=False, face_nodes=False):
    if not isinstance(group, h5py.Group) or not 1 <= len(group) <= MAX_SECTIONS:
        raise ExtractionIssue("Use bounded explicit native section groups.")
    if not coordinate and _attribute_int(group, "nSections") != len(group):
        raise ExtractionIssue("Native nSections must match every stored section.")
    rows = []
    for name in group:
        if not re.fullmatch(r"[1-9][0-9]*", name):
            raise ExtractionIssue("Native section names must retain positive numeric source IDs.")
        obj = group[name]
        low, high = _attribute_int(obj, "minId"), _attribute_int(obj, "maxId")
        if high < low:
            raise ExtractionIssue("Native inclusive section ranges cannot be reversed.")
        count = high-low+1
        if face_nodes:
            if not isinstance(obj, h5py.Group) or set(obj.keys()) != {"nnodes", "nodes"}:
                raise ExtractionIssue("Each face-node section must contain only native nnodes and flat nodes datasets.")
            _dataset(obj["nnodes"], h5py, integer=True, count=count)
            _dataset(obj["nodes"], h5py, integer=True)
        elif coordinate:
            _dataset(obj, h5py)
            if obj.shape != (count, 3) or obj.dtype.kind not in "fiu":
                raise ExtractionIssue("Every coordinate section must contain its complete Nx3 native coordinate range.")
        else:
            _dataset(obj, h5py, integer=True, count=count)
        rows.append({"path": obj.name, "minimumId": low, "maximumId": high, "recordCount": count})
    return rows


def _native_topology(handle):
    result = {category: inventory._topology(handle, category) for category in ("cells", "faces", "nodes")}
    for category, maximum in (("cells", MAX_CELLS), ("faces", MAX_FACES), ("nodes", MAX_NODES)):
        data = result[category]
        if data["countStatus"] != "ConfirmedFromStoredRanges" or not 0 < data["count"] <= maximum or not 1 <= len(data["zones"]) <= MAX_ZONES or not data.get("namesMatchZoneCount"):
            raise ExtractionIssue("Complete bounded native topology, counts and exact source tags are required: " + category)
        for zone in data["zones"]:
            if zone["zoneId"] <= 0 or not isinstance(zone["name"], str) or not 1 <= len(zone["name"]) <= 80 or any(ord(char) < 32 for char in zone["name"]):
                raise ExtractionIssue("Preserve bounded explicit native zone IDs and original names.")
    return result


def read_native_mesh(path: Path) -> dict:
    """A complete section parser; this function never assumes coordinate section 1."""
    import h5py
    import numpy as np
    with h5py.File(path, "r") as handle:
        inventory._scan_objects(handle, h5py)
        if set(handle["/meshes"].keys()) != {"1"}:
            raise ExtractionIssue("Only the observed single native mesh domain is supported.")
        topology = _native_topology(handle)
        counts = {"cellCount": topology["cells"]["count"], "faceCount": topology["faces"]["count"], "nodeCount": topology["nodes"]["count"]}
        sections = {}
        for key, source, total, options in (("coordinates", "/meshes/1/nodes/coords", counts["nodeCount"], {"coordinate": True}),
                ("faceNodes", "/meshes/1/faces/nodes", counts["faceCount"], {"face_nodes": True}),
                ("c0", "/meshes/1/faces/c0", counts["faceCount"], {}), ("c1", "/meshes/1/faces/c1", counts["faceCount"], {})):
            if source not in handle:
                raise ExtractionIssue("Restore every required native section group: " + source)
            sections[key] = checked_range_sections(_section_rows(handle[source], h5py, **options), total)
        by_range = {key: {(row["minimumId"], row["maximumId"]): row for row in sections[key]} for key in ("faceNodes", "c0", "c1")}
        if set(by_range["faceNodes"]) != set(by_range["c0"]) or set(by_range["faceNodes"]) != set(by_range["c1"]):
            raise ExtractionIssue("Face-node sections and c0/c1 sections require matching exact global ranges.")
        coordinates = np.empty((counts["nodeCount"]+1, 3), dtype="f8")
        coordinates[0] = np.nan  # Exterior ID zero is never a coordinate node.
        for row in sections["coordinates"]:
            dataset = handle[row["path"]]
            for start in range(0, row["recordCount"], inventory.COORDINATE_CHUNK):
                values = dataset[start:start+inventory.COORDINATE_CHUNK, :]
                if not np.isfinite(values).all():
                    raise ExtractionIssue("All stored coordinate sections must contain finite values.")
                low = row["minimumId"]+start
                coordinates[low:low+len(values)] = values
        source_zones = topology["faces"]["zones"]
        zones = [{**zone, "faceCount": 0, "triangleCount": 0, "c0ExteriorCount": 0, "c1ExteriorCount": 0,
            "internalFaceCount": 0, "faces": []} for zone in source_zones]
        for index, zone in enumerate(zones):
            for field in ("zoneType", "faceType", "shadowZoneId", "childZoneId"):
                values = topology["faces"]["rawDatasets"].get(field, {}).get("rawValues")
                zone[field] = values[index] if isinstance(values, list) and len(values) == len(zones) else None
        ordered_zones = sorted(zones, key=lambda row: row["minimumId"])
        zone_maxima = [row["maximumId"] for row in ordered_zones]
        boundary_count, connectivity_count = 0, 0
        for row in sections["faceNodes"]:
            native = handle[row["path"]]
            counts_per_face = native["nnodes"][()]
            declared_connectivity = int(native["nodes"].shape[0])
            if declared_connectivity > MAX_CONNECTIVITY_VALUES-connectivity_count or np.any(counts_per_face < 3) or np.any(counts_per_face > MAX_FACE_NODES) or int(counts_per_face.sum(dtype="i8")) != declared_connectivity:
                raise ExtractionIssue("Every flat connectivity section must exactly match its bounded per-face node counts.")
            connectivity_count += declared_connectivity
            node_ids = native["nodes"][()]
            c0 = handle[by_range["c0"][(row["minimumId"], row["maximumId"])]["path"]][()]
            c1 = handle[by_range["c1"][(row["minimumId"], row["maximumId"])]["path"]][()]
            if np.any(node_ids < 1) or np.any(node_ids > counts["nodeCount"]):
                raise ExtractionIssue("Every stored face-node ID must reference the complete global coordinate map.")
            if np.any(c0 > counts["cellCount"]) or np.any(c1 > counts["cellCount"]) or np.any(c0 < 0) or np.any(c1 < 0) or np.any(c0 == c1):
                raise ExtractionIssue("Every c0/c1 section must contain valid distinct cell IDs; exterior is zero.")
            boundary = np.flatnonzero((c0 == 0) != (c1 == 0))
            boundary_count += len(boundary)
            if boundary_count > MAX_BOUNDARY_FACES:
                raise ExtractionIssue("The boundary extraction exceeds the fixed native face budget.")
            offsets = np.empty(row["recordCount"]+1, dtype="i8")
            offsets[0] = 0
            np.cumsum(counts_per_face, dtype="i8", out=offsets[1:])
            for index in boundary:
                face_id = row["minimumId"]+int(index)
                zone = ordered_zones[bisect_left(zone_maxima, face_id)]
                face = tuple(int(value) for value in node_ids[offsets[index]:offsets[index+1]])
                zone["faces"].append((face_id, face))
                zone["faceCount"] += 1
                zone["triangleCount"] += len(face)-2
                zone["c0ExteriorCount"] += int(c0[index] == 0)
                zone["c1ExteriorCount"] += int(c1[index] == 0)
        for zone in zones:
            zone["faces"].sort(key=lambda row: row[0])
            zone["internalFaceCount"] = zone["inclusiveCount"]-zone["faceCount"]
        if not boundary_count:
            raise ExtractionIssue("No true native exterior boundary faces were found.")
    return {"nativeCounts": counts, "nativeTopology": topology, "sections": sections,
        "coordinates": coordinates, "zones": zones, "boundaryFaceCount": boundary_count}


def prepare_surface(model: dict, source_name: str) -> tuple[dict, list[tuple[dict, bytes]]]:
    faces = [face for zone in model["zones"] for _, face in zone["faces"]]
    audit = audit_native_topology(faces)
    boundary_zones, files, terms, quantization, plane_deviation, nonplanar, axes = [], [], [], 0.0, 0.0, 0, Counter()
    points = model["coordinates"]
    used = {identifier for face in faces for identifier in face}
    reference = tuple((min(float(points[node][axis]) for node in used)+max(float(points[node][axis]) for node in used))/2 for axis in range(3))
    for zone in model["zones"]:
        if not zone["faces"]:
            continue
        size = 84+50*zone["triangleCount"]
        if size > MAX_BUNDLE_BYTES or sum(descriptor["bytes"] for descriptor, _ in files)+size > MAX_BUNDLE_BYTES:
            raise ExtractionIssue("Derived STL bytes exceed the 25 MiB bundle boundary.")
        raw = bytearray(STL_HEADER + struct.pack("<I", zone["triangleCount"]))
        for _, face in zone["faces"]:
            native, triangles, geometry = face_geometry(face, points)
            axes[geometry["projectionAxis"]] += 1
            nonplanar += geometry["nonplanarAtRelative1e12"]
            plane_deviation = max(plane_deviation, geometry["maximumPlaneDeviationNativeUnits"])
            quantization = max(quantization, geometry["maximumCoordinateQuantizationErrorNativeUnits"])
            for triangle in native:
                a, b, c = [_sub(vertex, reference) for vertex in triangle]
                terms.append(_dot(a, _cross(b, c))/6)
            for normal, vertices in triangles:
                raw.extend(TRIANGLE.pack(*normal, *(coordinate for point in vertices for coordinate in point), 0))
        if len(raw) != size:
            raise ExtractionIssue("Binary STL triangle count must match every derived fan triangle.")
        descriptor = {"name": source_name.split(".")[0]+"-zone-"+str(zone["zoneId"])+".stl", "bytes": size,
            "sha256": sha256(raw).hexdigest(), "triangleCount": zone["triangleCount"], "format": "binary-stl",
            "sourceMesh": source_name, "zoneId": zone["zoneId"], "zoneName": zone["name"]}
        files.append((descriptor, bytes(raw)))
        public_zone = {key: value for key, value in zone.items() if key != "faces"}
        public_zone.update(nativeFaceIdRange={"minimumId": zone["faces"][0][0], "maximumId": zone["faces"][-1][0],
            "contiguous": zone["faces"][-1][0]-zone["faces"][0][0]+1 == len(zone["faces"])},
            triangleOrder="Ascending native face ID; (v0,vi,v(i+1)) for i=1..nnodes-2. Reconstruct native face/node IDs from the pinned HDF5; STL stores geometry only.",
            topologyAudit=audit_native_topology([face for _, face in zone["faces"]]), stlFile=descriptor)
        boundary_zones.append(public_zone)
    volume = math.fsum(terms) if audit["consistentNativeWinding"] else None
    if volume is not None and (not math.isfinite(volume) or volume == 0):
        volume = None
    equation = {"input": {"nativeBoundaryFaces": len(faces), "nativeFanTriangles": len(terms), "referencePointNativeUnits": list(reference),
        "closedEdgeIncidence": audit["closedEdgeIncidence"], "consistentNativeWinding": audit["consistentNativeWinding"]},
        "equation": "Vsigned = (1/6) sum dot(a-r, cross(b-r,c-r))",
        "substitution": {"triangleTermCount": len(terms), "signedTriangleTermsSum": volume},
        "result": {"value": volume, "unit": "UNVERIFIED native coordinate units cubed"},
        "claim": "Signed native-fan enclosed-volume diagnostic only when edge incidence is closed and winding consistent; outward orientation, self-intersections and physical units remain unverified."}
    report = {key: model[key] for key in ("nativeCounts", "nativeTopology", "sections", "boundaryFaceCount")}
    report.update(faceZones=[{key: value for key, value in zone.items() if key != "faces"} for zone in model["zones"]], boundaryZones=boundary_zones,
        triangleCount=len(terms), boundaryTopologyAudit=audit, triangulationAudit={"method": "Native-order vertex-zero fan; convex simple dominant-plane projection validated; native 3D vertices retained",
            "projectionAxisCounts": {str(axis): count for axis, count in sorted(axes.items())}, "nonplanarFaceCount": nonplanar,
            "maximumPlaneDeviationNativeUnits": plane_deviation, "maximumCoordinateQuantizationErrorNativeUnits": quantization,
            "stlCoordinateStorage": "IEEE754 little-endian float32", "coordinateUnits": "UNVERIFIED", "nativeWindingPreserved": True,
            "nodeWeldingPerformed": False, "topologyHealingPerformed": False, "degenerateFaceCount": 0, "degenerateFanTriangleCount": 0,
            "quantizationDegenerateTriangleCount": 0}, signedEnclosedVolumeNativeUnitsCubed=volume, signedVolumeEquationStages=[equation])
    return report, files


def output_directory(value: Path) -> Path:
    value = Path(value).absolute()
    inventory.output_path(value / "topology-report.json")
    if value.exists() or value.is_symlink() or getattr(value, "is_junction", lambda: False)():
        raise ExtractionIssue("Use a new independent RUNNER_TEMP boundary output directory.")
    return value


def run(destination: Path) -> dict:
    destination = output_directory(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    cache = destination.parent / "frozen-rotor-mesh-inputs"
    if cache.is_symlink() or getattr(cache, "is_junction", lambda: False)():
        raise ExtractionIssue("Native originals require the regular independent runner input cache.")
    cache.mkdir(exist_ok=True)
    meshes, outputs = [], []
    for name in inventory.MESH_NAMES:
        path = inventory.download_mesh(cache, name)
        source = inventory.verify_file(path, name)  # Before any HDF5 import/open.
        model = read_native_mesh(path)
        derived, files = prepare_surface(model, name)
        after = inventory.verify_file(path, name)
        meshes.append({**source, "sourceSha256AfterExtraction": after["sha256"], **derived})
        outputs.extend(files)
    import h5py
    run_id, attempt, commit = (os.environ.get(name) for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA"))
    report = {"schemaVersion": SCHEMA, "exampleDataRepository": "https://github.com/ansys/example-data", "exampleDataCommit": inventory.PIN,
        "extractedAt": datetime.now(timezone.utc).isoformat(), "h5pyVersion": h5py.__version__, "hdf5Version": h5py.version.hdf5_version,
        "githubRepository": "simcalc/PyAnsys", "githubRunId": run_id if run_id and run_id.isdigit() else None,
        "githubRunAttempt": attempt if attempt and attempt.isdigit() else None,
        "githubSourceCommit": commit if commit and re.fullmatch(r"[0-9a-f]{40}", commit) else None,
        "coordinateUnits": "UNVERIFIED", "derivationMethod": "Native c0/c1 exterior selection, exact source zone tags and global node-ID ranges; native-winding fan binary STL without healing or welding",
        "limits": {"maximumNodesPerMesh": MAX_NODES, "maximumFacesPerMesh": MAX_FACES, "maximumBoundaryFacesPerMesh": MAX_BOUNDARY_FACES,
            "maximumZonesPerTopology": MAX_ZONES, "maximumSectionsPerGroup": MAX_SECTIONS, "maximumFaceNodes": MAX_FACE_NODES,
            "maximumConnectivityValuesPerMesh": MAX_CONNECTIVITY_VALUES, "maximumReportBytes": inventory.MAX_REPORT_BYTES, "maximumBundleBytes": MAX_BUNDLE_BYTES},
        "meshes": meshes, "files": [descriptor for descriptor, _ in outputs], "sourceFileBytes": sum(mesh["bytes"] for mesh in meshes),
        "derivedSurfaceBytes": sum(descriptor["bytes"] for descriptor, _ in outputs), "originalMeshesImmutable": True,
        "nativeCoordinateUnitsVerified": False, "surfaceDerived": True, "volumeMeshGenerated": False, "meshQualityCheckedBySolver": False,
        "nativeCaseGenerated": False, "fluentSessionConnected": False, "licenseVerified": False, "actualCfdRunVerified": False,
        "simulationExecuted": False, "mainApplicationWrites": False,
        "claimBoundary": "Tagged boundary geometry preparation and native node-ID topology audit only; no watertight geometric proof, new volume mesh, solver-ready case, licensed session or CFD result."}
    raw = inventory.bounded_json(report)
    if len(raw)+report["derivedSurfaceBytes"] > MAX_BUNDLE_BYTES:
        raise ExtractionIssue("Tagged STL plus metadata exceed the 25 MiB bounded bundle.")
    destination.mkdir()  # No derivation is published before all validation completes.
    written = []
    try:
        for descriptor, payload in outputs:
            target = destination / descriptor["name"]
            with target.open("xb") as stream:
                written.append(target)
                stream.write(payload)
        target = destination / "topology-report.json"
        with target.open("xb") as stream:
            written.append(target)
            stream.write(raw)
    except OSError:
        # Remove only our verified regular files within this newly created directory.
        for path in written:
            if path.parent == destination and path.is_file() and not path.is_symlink():
                path.unlink()
        if not any(destination.iterdir()):
            destination.rmdir()
        raise
    return {"schemaVersion": SCHEMA, "reportFile": "topology-report.json", "reportBytes": len(raw), "reportSha256": sha256(raw).hexdigest(),
        "surfaceFiles": len(outputs), "surfaceBytes": report["derivedSurfaceBytes"],
        "meshes": [{"name": mesh["name"], "boundaryFaceCount": mesh["boundaryFaceCount"], "triangleCount": mesh["triangleCount"],
            "closedEdgeIncidence": mesh["boundaryTopologyAudit"]["closedEdgeIncidence"], "consistentNativeWinding": mesh["boundaryTopologyAudit"]["consistentNativeWinding"]} for mesh in meshes],
        "volumeMeshGenerated": False, "simulationExecuted": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only pinned native boundary topology and tagged binary STL; no solver execution")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.output_dir), allow_nan=False))
    except (inventory.InspectionIssue, OSError, ImportError, UnicodeError, KeyError) as error:
        print(json.dumps({"schemaVersion": SCHEMA, "status": "InputRequired", "reasonCode": "CFD_NATIVE_BOUNDARY_EXTRACTION_REQUIRED",
            "action": str(error), "volumeMeshGenerated": False, "simulationExecuted": False}))
        raise SystemExit(2)
