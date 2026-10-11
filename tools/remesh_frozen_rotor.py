"""Bounded classroom volume remeshing of two exact official mesh boundaries.

The source is read-only; native float64 coordinates/global node IDs and source
zone ownership remain fixed while Gmsh fills only the three-dimensional interior.
This produces meshing evidence only, never a Fluent case or CFD calculation.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_frozen_rotor_boundaries as boundary
import inspect_frozen_rotor_mesh as inventory

SCHEMA = "untirta.frozen-rotor-classroom-remesh.v1"
GMSH_VERSION = "4.15.2"
MAX_SECONDS = 900
MAX_MEMORY_BYTES = 2 * 1024**3
MAX_OUTPUT_BYTES = 128 * 1024**2  # Raw ASCII meshes; public ZIP budget is separate.
MAX_NEW_CELLS_PER_COMPONENT = 800_000
MAX_NEW_NODES_PER_COMPONENT = 500_000
MAX_COMBINED_CELLS = 999_999
MAX_COMBINED_NODES = 999_999
MAX_CLASSIFIED_SURFACES = 512
GMSH_ALGORITHM_3D = 10  # HXT: efficient Delaunay implementation; exact facets stay fixed.
OPTIMISATION_MODES = ("full", "unoptimised", "gmsh-interior")
HXT_DEFAULT_QUALITY_TARGET = .3  # Native HXT optimisation target, not measured minSICN.
VOLUME_CLOSURE_RELATIVE_TOLERANCE = 1e-8
NATIVE_TAIL_LINES = 24
NATIVE_LINE_CHARACTERS = 240
CHILD_ENVIRONMENT_KEYS = ("PATH", "LD_LIBRARY_PATH", "LANG", "LC_ALL", "TMPDIR", "RUNNER_TEMP", "RUNNER_OS",
    "RUNNER_ENVIRONMENT", "GITHUB_ACTIONS", "GITHUB_REPOSITORY", "GITHUB_REPOSITORY_VISIBILITY", "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT", "GITHUB_SHA", "GITHUB_SERVER_URL", "PYTHONDONTWRITEBYTECODE")
SIZING_DIVISOR = 32
DISTANCE_SAMPLES_PER_ZONE = 64
REFERENCES = ["https://gmsh.info/doc/texinfo/gmsh.html#t13",
              "https://gmsh.info/doc/texinfo/gmsh.html#x2",
              "https://gmsh.info/doc/texinfo/gmsh.html#MSH-file-format-version-2-_0028Legacy_0029"]


class RemeshIssue(inventory.InspectionIssue):
    pass


def optimisation_metadata(mode):
    if mode not in OPTIMISATION_MODES:
        raise RemeshIssue("Choose explicit optimisation mode full, unoptimised or gmsh-interior.")
    return {"mode": mode, "meshOptimize": int(mode == "full"),
            "hxtDefaultQualityTarget": HXT_DEFAULT_QUALITY_TARGET,
            "targetBasis": "Pinned Gmsh 4.15.2 native HXT optimisation default; no target override.",
            "targetIsMeasuredMinSICN": False, "optimisationSuccessVerified": False,
            "postFillDefaultOptimisationRequested": mode == "gmsh-interior",
            "postFillDefaultOptimisationThreshold": .3 if mode == "gmsh-interior" else None,
            "postFillDefaultOptimisationMetric": "Native gamma quality, not minSICN" if mode == "gmsh-interior" else None,
            "postFillTargetOverride": False, "postFillIterationBoundClaimed": False,
            "claimBoundary": "Requested native optimisation setting only. Actual every-cell quality and independent OpenFOAM checkMesh acceptance remain separate."}


def volume_mesh_options(size, mode="full"):
    optimisation = optimisation_metadata(mode)
    return {"Mesh.MeshSizeMin": size, "Mesh.MeshSizeMax": size, "Mesh.MeshSizeFromPoints": 0,
            "Mesh.MeshSizeFromCurvature": 0, "Mesh.MeshSizeExtendFromBoundary": 0,
            "Mesh.MeshOnlyEmpty": 1, "Mesh.Algorithm3D": GMSH_ALGORITHM_3D, "Mesh.ElementOrder": 1,
            "Mesh.MshFileVersion": 2.2, "Mesh.Binary": 0, "Mesh.SaveAll": 0,
            # Native global node IDs are part of the exact boundary contract.
            # The default renumbers both nodes and elements after generate(3).
            "Mesh.Renumber": 0,
            "Mesh.PreserveNumberingMsh2": 1,
            "Mesh.Optimize": optimisation["meshOptimize"]}


def persist_json(destination, name, value):
    if not destination.is_dir() or destination.is_symlink() or getattr(destination, "is_junction", lambda:False)():
        raise RemeshIssue("Evidence must remain in the new regular isolated output directory.")
    target, temporary = destination/name, destination/(name+".part")
    if target.is_symlink() or temporary.exists() or temporary.is_symlink() or (target.exists() and not target.is_file()):
        raise RemeshIssue("The attempt-owned evidence path must remain a regular file.")
    try:
        with temporary.open("xb") as stream:
            stream.write(inventory.bounded_json(value))
        temporary.replace(target)
    finally:
        if temporary.is_file() and not temporary.is_symlink():
            temporary.unlink()


def checked_output(value):
    value = Path(value).absolute()
    inventory.output_path(value / "remesh-report.json")
    if value.exists() or value.is_symlink() or getattr(value, "is_junction", lambda: False)():
        raise RemeshIssue("Use a new regular RUNNER_TEMP remeshing output directory.")
    return value


def source_triangles(model):
    """Keep native node IDs and float64 positions; do not use rounded STL nodes."""
    polygons = [face for zone in model["zones"] for _, face in zone["faces"]]
    audit = boundary.audit_native_topology(polygons)
    if not audit["consistentNativeWinding"]:
        raise RemeshIssue("Volume remeshing requires closed native edge incidence and consistent winding.")
    zones, owners, element_id = [], {}, 1
    for zone in model["zones"]:
        if not zone["faces"]:
            continue
        triangles, element_ids, native_faces = [], [], []
        for face_id, face in zone["faces"]:
            boundary.face_geometry(face, model["coordinates"])
            for index in range(1, len(face)-1):
                triangle = (face[0], face[index], face[index+1])
                key = tuple(sorted(triangle))
                if key in owners:
                    raise RemeshIssue("Duplicate native fan triangles cannot preserve unique zone ownership.")
                owners[key] = zone["zoneId"]
                triangles.append(triangle)
                element_ids.append(element_id)
                native_faces.append(face_id)
                element_id += 1
        zones.append({"zoneId": zone["zoneId"], "zoneName": zone["name"], "triangles": triangles,
                      "elementIds": element_ids, "nativeFaceIds": native_faces})
    if len({zone["zoneName"] for zone in zones}) != len(zones):
        raise RemeshIssue("Distinct source zone names are required for exact physical groups.")
    return zones, owners, audit


def classified_owners(surface_triangles, owners):
    """Require complete unchanged triangle/node-ID identity after classification."""
    groups, seen = defaultdict(list), set()
    if not 1 <= len(surface_triangles) <= MAX_CLASSIFIED_SURFACES:
        raise RemeshIssue("The classified surface count exceeds the preparation budget.")
    for entity, triangles in surface_triangles.items():
        identifiers = set()
        if not triangles:
            raise RemeshIssue("A classified surface without source triangles cannot be tagged.")
        for triangle in triangles:
            key = tuple(sorted(int(node) for node in triangle))
            if len(set(key)) != 3 or key not in owners or key in seen:
                raise RemeshIssue("Classification must preserve every unique native fan triangle and global node ID.")
            seen.add(key)
            identifiers.add(owners[key])
        if len(identifiers) != 1:
            raise RemeshIssue("A classified surface spans multiple native zones; preserve the source tag seam before remeshing.")
        groups[identifiers.pop()].append(int(entity))
    if seen != set(owners):
        raise RemeshIssue("Classification omitted native boundary triangles.")
    return dict(groups)


def preserved_boundary(surface_triangles, owners, originals, native_points, points):
    groups = classified_owners(surface_triangles, owners)
    used = {node for triangles in surface_triangles.values() for triangle in triangles for node in triangle}
    if used != set(native_points) or any(node not in points or points[node] != native_points[node] for node in used):
        raise RemeshIssue("The preserved boundary must retain every exact native float64 node coordinate and global ID.")
    faces = []
    for triangles in surface_triangles.values():
        for triangle in triangles:
            original = originals[tuple(sorted(triangle))]
            if tuple(triangle) not in (original, original[1:]+original[:1], original[2:]+original[:2]):
                raise RemeshIssue("Every fixed boundary triangle must preserve its native winding.")
            faces.append(tuple(triangle))
    audit = boundary.audit_native_topology(faces)
    if not audit["consistentNativeWinding"]:
        raise RemeshIssue("The fixed native fan surface must remain closed with consistent winding.")
    return groups, audit


def checkpoint(progress, stage):
    progress["stage"] = stage
    journal = progress.setdefault("journal", [])
    if len(journal) >= 32:
        raise RemeshIssue("The bounded meshing attempt exceeded its progress journal budget.")
    journal.append({"stage": stage, "component": progress.get("component"),
                    "elapsedSeconds": round(time.monotonic()-progress["started"], 3)})
    value = {"schemaVersion": SCHEMA, "mode": "native-boundary-preserved-tetra-fill", "stage": stage,
             "component": progress.get("component"), "journal": journal,
             "surfaceZoneBindings": progress.get("surfaceZoneBindings", []),
             "verifiedSources": progress.get("verifiedSources", []), "gmshAlgorithm3D": GMSH_ALGORITHM_3D,
             "optimisation": optimisation_metadata(progress.get("optimisationMode", "full")),
             "generatedVolumeDiagnostics": progress.get("generatedVolumeDiagnostics"),
             "interiorOptimisationDiagnostics": progress.get("interiorOptimisationDiagnostics"),
             "actualCfdRunVerified": False, "simulationExecuted": False, "mainApplicationWrites": False}
    persist_json(progress["destination"], "remesh-progress.json", value)


def gmsh_diagnostic(gmsh, progress):
    result = {"surfaceZoneBindings": progress.get("surfaceZoneBindings", []), "lastEntityErrors": [], "loggerTail": []}
    if progress.get("generatedVolumeDiagnostics") is not None:
        result["generatedVolumeDiagnostics"] = progress["generatedVolumeDiagnostics"]
    if progress.get("interiorOptimisationDiagnostics") is not None:
        result["interiorOptimisationDiagnostics"] = progress["interiorOptimisationDiagnostics"]
    if gmsh is not None and gmsh.isInitialized():
        try:
            result["lastEntityErrors"] = [[int(dim), int(tag)] for dim, tag in gmsh.model.mesh.getLastEntityError()[:16]]
            result["loggerTail"] = [str(line)[:240] for line in gmsh.logger.get()[-24:]]
        except Exception as error:
            result["captureIssue"] = type(error).__name__
    return result


def partition_source_seams(surface_triangles, owners, originals):
    """Intersect geometric patches with exact native zone ownership, not distance."""
    if set(originals) != set(owners) or not 1 <= len(surface_triangles) <= MAX_CLASSIFIED_SURFACES:
        raise RemeshIssue("Source seam partitioning requires the complete bounded native triangle map.")
    partitions, seen = [], set()
    for entity, triangles in sorted(surface_triangles.items()):
        by_zone = defaultdict(list)
        if not triangles:
            raise RemeshIssue("Every geometric classified entity must contain source triangles.")
        for triangle in triangles:
            key = tuple(sorted(int(node) for node in triangle))
            if len(set(key)) != 3 or key not in owners or key in seen:
                raise RemeshIssue("Partitioning must retain every unique exact native triangle and global node ID.")
            seen.add(key)
            by_zone[owners[key]].append(originals[key])  # Retain original native winding.
        for zone, native_triangles in sorted(by_zone.items()):
            partitions.append({"entityId": len(partitions)+1, "originalClassifiedEntityId": int(entity),
                               "sourceZoneId": zone, "triangles": native_triangles})
    if seen != set(owners) or len(partitions) > MAX_CLASSIFIED_SURFACES:
        raise RemeshIssue("Native zone-seam partitions must cover all source triangles within the entity budget.")
    return partitions


def bounds(points):
    if not points:
        raise RemeshIssue("A nonempty finite coordinate set is required.")
    if any(len(point) != 3 or not all(math.isfinite(float(value)) for value in point) for point in points):
        raise RemeshIssue("Three finite coordinates are required for every node.")
    return {"minimum": [min(float(point[a]) for point in points) for a in range(3)],
            "maximum": [max(float(point[a]) for point in points) for a in range(3)]}


def sizing(box):
    extents = [high-low for low, high in zip(box["minimum"], box["maximum"])]
    length = max(extents)
    if not math.isfinite(length) or length <= 0 or min(extents) <= 0:
        raise RemeshIssue("A finite three-dimensional source bounding box is required.")
    return length / SIZING_DIVISOR


def finite_statistics(values, expected_count):
    """Bounded measured aggregates only; invalid values never become a pass."""
    result = {"expectedValueCount": expected_count, "returnedValueCount": 0, "finiteValueCount": 0,
              "nonFiniteCount": 0, "negativeCount": 0, "zeroCount": 0, "nonPositiveCount": 0,
              "minimum": None, "maximum": None}
    for value in values:
        value = float(value)
        result["returnedValueCount"] += 1
        if not math.isfinite(value):
            result["nonFiniteCount"] += 1
            continue
        result["finiteValueCount"] += 1
        result["negativeCount"] += int(value < 0)
        result["zeroCount"] += int(value == 0)
        result["nonPositiveCount"] += int(value <= 0)
        result["minimum"] = value if result["minimum"] is None else min(result["minimum"], value)
        result["maximum"] = value if result["maximum"] is None else max(result["maximum"], value)
    return result


def measured_quality_diagnostics(gmsh, cell_tags):
    result = {}
    for quantity in ("minSICN", "minDetJac", "volume"):
        try:
            result[quantity] = finite_statistics(gmsh.model.mesh.getElementQualities(cell_tags, quantity), len(cell_tags))
        except RemeshIssue:
            raise  # Preserve the fixed native runtime deadline and safety gates.
        except Exception as error:
            result[quantity] = {"expectedValueCount": len(cell_tags), "captureIssue": type(error).__name__}
    return result


def tetra_coordinate_diagnostics(cells, points):
    """Inspect every exported tetrahedron without storing/publishing cell arrays."""
    result = {"exportedTetrahedronCount": len(cells), "coordinateNodeCount": len(points), "invalidCellNodeCount": 0,
              "nonFiniteCoordinateNodeCount": 0, "coordinateBoundsNativeUnits": {},
              "signedDeterminant": finite_statistics([], len(cells)),
              "absoluteDeterminantOverMaxOriginEdgeCubed": finite_statistics([], len(cells)),
              "smallScaleRelativeDeterminantThreshold": 1e-12, "smallScaleRelativeDeterminantCount": 0,
              "smallScaleRelativeDeterminantIsDiagnosticOnly": True,
              "claimBoundary": "Measured exported ordering and coordinate diagnostics, not a repaired mesh or CFD acceptance; native coordinate units remain unverified."}
    for point in points.values():
        if len(point) != 3 or not all(math.isfinite(value) for value in point):
            result["nonFiniteCoordinateNodeCount"] += 1
            continue
        for axis, value in zip(("x", "y", "z"), point):
            bounds = result["coordinateBoundsNativeUnits"].setdefault(axis, {"minimum": value, "maximum": value})
            bounds["minimum"], bounds["maximum"] = min(bounds["minimum"], value), max(bounds["maximum"], value)
    def determinants():
        for cell in cells:
            if len(cell) != 4 or len(set(cell)) != 4 or any(node not in points or len(points[node]) != 3 for node in cell):
                result["invalidCellNodeCount"] += 1
                yield math.nan
                continue
            a, b, c, d = [points[node] for node in cell]
            ab, ac, ad = boundary._sub(b, a), boundary._sub(c, a), boundary._sub(d, a)
            determinant = boundary._dot(ab, boundary._cross(ac, ad))
            yield determinant
    result["signedDeterminant"] = finite_statistics(determinants(), len(cells))
    def scaled():
        for cell in cells:
            if len(cell) != 4 or len(set(cell)) != 4 or any(node not in points or len(points[node]) != 3 for node in cell):
                yield math.nan
                continue
            a, b, c, d = [points[node] for node in cell]
            ab, ac, ad = boundary._sub(b, a), boundary._sub(c, a), boundary._sub(d, a)
            determinant = boundary._dot(ab, boundary._cross(ac, ad))
            edge = max(math.hypot(*ab), math.hypot(*ac), math.hypot(*ad))
            try:
                normalized = abs(determinant)/(edge**3) if edge > 0 and math.isfinite(edge) and math.isfinite(determinant) else math.nan
            except (OverflowError, ZeroDivisionError):
                normalized = math.nan
            if math.isfinite(normalized) and normalized <= result["smallScaleRelativeDeterminantThreshold"]:
                result["smallScaleRelativeDeterminantCount"] += 1
            yield normalized
    result["absoluteDeterminantOverMaxOriginEdgeCubed"] = finite_statistics(scaled(), len(cells))
    return result


def tetra_metrics(cells, points):
    """Compute positive signed volume from actual ordered tetrahedron nodes."""
    determinants = []
    for cell in cells:
        if len(cell) != 4 or len(set(cell)) != 4 or any(node not in points for node in cell):
            raise RemeshIssue("Every generated volume cell must be a four-node tetrahedron with valid node IDs.")
        a, b, c, d = [points[node] for node in cell]
        determinant = boundary._dot(boundary._sub(b, a), boundary._cross(boundary._sub(c, a), boundary._sub(d, a)))
        if not math.isfinite(determinant) or determinant <= 0:
            raise RemeshIssue("All generated tetrahedra require finite strictly positive signed determinants.")
        determinants.append(determinant)
    if not determinants or len(determinants) > MAX_NEW_CELLS_PER_COMPONENT:
        raise RemeshIssue("The new tetrahedron count is empty or exceeds the fixed classroom mesh budget.")
    return {"minimumSignedDeterminant": min(determinants), "minimumTetraVolumeAssumedM3": min(determinants)/6,
            "totalTetraVolumeAssumedM3": math.fsum(determinants)/6, "nonPositiveTetrahedronCount": 0,
            "everyTetrahedronChecked": True}


def volume_closure(zones, native_points, tetra_volume):
    """Compare exact native fan geometry with the positive generated volume sum."""
    box = bounds(list(native_points.values()))
    reference = tuple((low+high)/2 for low, high in zip(box["minimum"], box["maximum"]))
    terms = []
    for zone in zones:
        for triangle in zone["triangles"]:
            a, b, c = [boundary._sub(native_points[node], reference) for node in triangle]
            terms.append(boundary._dot(a, boundary._cross(b, c))/6)
    signed = math.fsum(terms)
    if not math.isfinite(signed) or signed == 0 or not math.isfinite(tetra_volume) or tetra_volume <= 0:
        raise RemeshIssue("Volume closure requires finite nonzero native signed volume and positive tetrahedral volume.")
    magnitude = abs(signed)
    difference, tolerance = abs(tetra_volume-magnitude), magnitude*VOLUME_CLOSURE_RELATIVE_TOLERANCE
    result = {"nativeSignedFanVolumeNativeUnitsCubed": signed, "nativeFanVolumeMagnitudeNativeUnitsCubed": magnitude,
              "generatedPositiveTetraVolumeNativeUnitsCubed": tetra_volume,
              "absoluteDifferenceNativeUnitsCubed": difference, "relativeDifference": difference/magnitude,
              "relativeTolerance": VOLUME_CLOSURE_RELATIVE_TOLERANCE,
              "absoluteToleranceNativeUnitsCubed": tolerance, "toleranceScale": "Absolute signed exact float64 closed native fan volume",
              "referencePointNativeUnits": list(reference), "nativeWindingChanged": False,
              "equation": "Vboundary = abs(sum(dot(a-r,cross(b-r,c-r))/6)); |VtetraSum-Vboundary| <= 1e-8*Vboundary",
              "passed": difference <= tolerance,
              "claimBoundary": "Closed-boundary volume consistency only; no CFD accuracy or self-intersection acceptance claim."}
    return result


def nearest_triangle_distance(point, triangles, np):
    """Exact point-to-triangle distance over all same-zone native triangles."""
    best = math.inf
    for start in range(0, len(triangles), 8192):
        xyz = np.asarray(triangles[start:start+8192], dtype="f8")
        a, b, c = xyz[:, 0], xyz[:, 1], xyz[:, 2]
        ab, ac, ap = b-a, c-a, np.asarray(point, dtype="f8")-a
        normal = np.cross(ab, ac)
        normal2 = np.einsum("ij,ij->i", normal, normal)
        if not np.isfinite(normal2).all() or np.any(normal2 <= 0):
            raise RemeshIssue("Native triangle distance requires finite nondegenerate source triangles.")
        projection = ap - (np.einsum("ij,ij->i", ap, normal)/normal2)[:, None]*normal
        d00 = np.einsum("ij,ij->i", ab, ab)
        d01 = np.einsum("ij,ij->i", ab, ac)
        d11 = np.einsum("ij,ij->i", ac, ac)
        d20 = np.einsum("ij,ij->i", projection, ab)
        d21 = np.einsum("ij,ij->i", projection, ac)
        denominator = normal2  # Stable Gram determinant, including slender triangles.
        v, w = (d11*d20-d01*d21)/denominator, (d00*d21-d01*d20)/denominator
        inside = (v >= 0) & (w >= 0) & (v+w <= 1)
        distances = np.full(len(xyz), math.inf)
        distances[inside] = (np.einsum("ij,ij->i", ap, normal)**2/normal2)[inside]
        for left, right in ((a, b), (b, c), (c, a)):
            edge, delta = right-left, np.asarray(point)-left
            parameter = np.clip(np.einsum("ij,ij->i", delta, edge)/np.einsum("ij,ij->i", edge, edge), 0, 1)
            vector = delta-parameter[:, None]*edge
            distances = np.minimum(distances, np.einsum("ij,ij->i", vector, vector))
        best = min(best, float(distances.min()))
    if not math.isfinite(best) or best < 0:
        raise RemeshIssue("A finite source-triangle distance is required.")
    return math.sqrt(best)


def patch_metrics(triangles, points):
    area, vectors = [], [[], [], []]
    used = set()
    for triangle in triangles:
        a, b, c = [points[int(node)] for node in triangle]
        vector = boundary._cross(boundary._sub(b, a), boundary._sub(c, a))
        length = math.hypot(*vector)
        if not math.isfinite(length) or length <= 0:
            raise RemeshIssue("Every boundary triangle requires finite positive area.")
        area.append(length/2)
        for axis in range(3):
            vectors[axis].append(vector[axis]/2)
        used.update(int(node) for node in triangle)
    return {"triangleCount": len(triangles), "uniqueNodeCount": len(used), "areaAssumedM2": math.fsum(area),
            "areaVectorAssumedM2": [math.fsum(row) for row in vectors],
            "boundsAssumedM": bounds([points[node] for node in used]), "outwardNormalConventionVerified": False}


def _triangles_for_surface(gmsh, entity):
    types, tags, node_lists = gmsh.model.mesh.getElements(2, entity)
    result = []
    for kind, element_tags, nodes in zip(types, tags, node_lists):
        if int(kind) != 2 or len(nodes) != 3*len(element_tags):
            raise RemeshIssue("Only exact three-node surface triangles are supported.")
        result.extend(tuple(int(node) for node in nodes[i:i+3]) for i in range(0, len(nodes), 3))
    return result


def _quality(gmsh, cell_tags):
    result = {}
    for quantity in ("minSICN", "minDetJac", "volume"):
        values = [float(value) for value in gmsh.model.mesh.getElementQualities(cell_tags, quantity)]
        if len(values) != len(cell_tags) or not values or any(not math.isfinite(value) or value <= 0 for value in values):
            raise RemeshIssue("Every generated cell requires finite positive Gmsh " + quantity + ".")
        result[quantity] = {"minimum": min(values), "maximum": max(values), "mean": math.fsum(values)/len(values), "everyCellChecked": True}
    result["acceptance"] = "Positive cell orientation and reported shape diagnostics only; CFD accuracy/checkMesh acceptance is separate."
    return result


def inspect_exported_volume(gmsh, volume, name, progress):
    node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
    if not 4 <= len(node_tags) <= MAX_NEW_NODES_PER_COMPONENT or len(coordinates) != 3*len(node_tags):
        raise RemeshIssue("Generated node count exceeds the fixed classroom budget or has an invalid coordinate shape.")
    points = {int(tag): tuple(float(value) for value in coordinates[i*3:i*3+3]) for i, tag in enumerate(node_tags)}
    if len(points) != len(node_tags):
        raise RemeshIssue("Generated nodes must retain unique global node IDs.")
    types, tag_sets, node_sets = gmsh.model.mesh.getElements(3, volume)
    progress["generatedVolumeDiagnostics"] = {
        "sourceMesh": name, "exportedNodeTagCount": len(node_tags), "uniqueExportedNodeCount": len(points),
        "exportedVolumeElementTypes": [int(kind) for kind in types[:8]],
        "exportedVolumeElementCount": sum(len(tags) for tags in tag_sets),
        "exportedVolumeConnectivityEntryCount": sum(len(nodes) for nodes in node_sets),
        "countBasis": "Actual Gmsh getElements(3, volume) exported elements; native HXT internal counters and all-dimensional log summaries are different quantities.",
        "qualityAndBoundaryGatesPassed": False}
    checkpoint(progress, "exported-volume-count-diagnostics")
    if len(types) != 1 or int(types[0]) != 4 or len(node_sets[0]) != 4*len(tag_sets[0]):
        raise RemeshIssue("A nonempty linear tetrahedral volume mesh is required.")
    if not 1 <= len(tag_sets[0]) <= MAX_NEW_CELLS_PER_COMPONENT:
        raise RemeshIssue("The tetra-fill exceeds the fixed per-component cell budget.")
    tags = [int(tag) for tag in tag_sets[0]]
    cells = [tuple(int(node) for node in node_sets[0][i:i+4]) for i in range(0, len(node_sets[0]), 4)]
    progress["generatedVolumeDiagnostics"]["tetraCoordinateAndDeterminant"] = tetra_coordinate_diagnostics(cells, points)
    progress["generatedVolumeDiagnostics"]["nativeQualityMetrics"] = measured_quality_diagnostics(gmsh, tags)
    interior = progress.get("interiorOptimisationDiagnostics")
    if interior is not None and interior["nativeOptimisationReturned"]:
        interior["after"] = progress["generatedVolumeDiagnostics"]
    checkpoint(progress, "measured-volume-quality-diagnostics")
    return points, tags, cells


def remesh_component(model, name, destination, gmsh, np, progress):
    optimisation = optimisation_metadata(progress.get("optimisationMode", "full"))
    component = name.split(".")[0]
    progress["component"] = component
    progress["surfaceZoneBindings"] = []
    progress.pop("generatedVolumeDiagnostics", None)
    progress.pop("interiorOptimisationDiagnostics", None)
    checkpoint(progress, "native-boundary-validation")
    zones, owners, native_audit = source_triangles(model)
    used = sorted({node for triangle in owners for node in triangle})
    native_points = {node: tuple(float(value) for value in model["coordinates"][node]) for node in used}
    native_box, size = bounds(list(native_points.values())), None
    size = sizing(native_box)
    gmsh.model.add(component)
    for zone in zones:
        gmsh.model.addDiscreteEntity(2, zone["zoneId"])
    # Add every shared native global ID once. Gmsh reclassifies nodes by elements.
    gmsh.model.mesh.addNodes(2, zones[0]["zoneId"], used, [value for node in used for value in native_points[node]])
    for zone in zones:
        gmsh.model.mesh.addElementsByType(zone["zoneId"], 2, zone["elementIds"], [node for row in zone["triangles"] for node in row])
    gmsh.model.mesh.reclassifyNodes()
    originals = {tuple(sorted(triangle)): triangle for zone in zones for triangle in zone["triangles"]}
    checkpoint(progress, "fixed-native-zone-topology")
    gmsh.model.mesh.createTopology(makeSimplyConnected=True, exportDiscrete=True)
    surface_rows = {tag: _triangles_for_surface(gmsh, tag) for dim, tag in gmsh.model.getEntities(2)}
    topology_tags, topology_xyz, _ = gmsh.model.mesh.getNodes()
    topology_points = {int(tag): tuple(float(value) for value in topology_xyz[i*3:i*3+3]) for i, tag in enumerate(topology_tags)}
    groups, fixed_audit = preserved_boundary(surface_rows, owners, originals, native_points, topology_points)
    if len(topology_tags) != len(topology_points):
        raise RemeshIssue("Duplicated native global node IDs cannot define a fixed surface.")
    zone_names = {zone["zoneId"]: zone["zoneName"] for zone in zones}
    progress["surfaceZoneBindings"] = [{"entityId": entity, "sourceZoneId": zone, "sourceZoneName": zone_names[zone]}
        for zone, entities in sorted(groups.items()) for entity in entities]
    # Discrete surfaces intentionally receive no createGeometry/reparametrization.
    # Their exact stored facets remain fixed while only the volume is filled.
    checkpoint(progress, "fixed-boundary-volume-definition")
    loop = gmsh.model.geo.addSurfaceLoop(sorted(surface_rows))
    volume = gmsh.model.geo.addVolume([loop])
    gmsh.model.geo.synchronize()
    for zone in zones:
        gmsh.model.addPhysicalGroup(2, groups[zone["zoneId"]], zone["zoneId"])
        gmsh.model.setPhysicalName(2, zone["zoneId"], zone["zoneName"])
    gmsh.model.addPhysicalGroup(3, [volume], 1)
    gmsh.model.setPhysicalName(3, 1, "rotating-fluid" if component == "impeller" else "stationary-fluid")
    for option, value in volume_mesh_options(size, optimisation["mode"]).items():
        gmsh.option.setNumber(option, value)
    checkpoint(progress, "fixed-boundary-tetrahedral-fill")
    gmsh.model.mesh.generate(3)
    points, tags, cells = inspect_exported_volume(gmsh, volume, name, progress)
    if optimisation["mode"] == "gmsh-interior":
        before_surfaces = {tag: _triangles_for_surface(gmsh, tag) for dim, tag in gmsh.model.getEntities(2)}
        before_groups, before_audit = preserved_boundary(before_surfaces, owners, originals, native_points, points)
        if before_groups != groups:
            raise RemeshIssue("Interior optimisation requires the same complete native source-zone surface entities before execution.")
        evidence = {"nativeMethod": "gmsh.model.mesh.optimize('', force=True)", "iterationBoundClaimed": False,
                    "externalMaximumSeconds": MAX_SECONDS, "nativeGammaThreshold": .3, "targetOverride": False,
                    "before": progress["generatedVolumeDiagnostics"], "after": None,
                    "completeNativeBoundaryBeforeVerified": True, "completeNativeBoundaryAfterVerified": False,
                    "boundaryBeforeAudit": before_audit, "nativeOptimisationReturned": False,
                    "elementCacheRebuiltAfterOptimisation": False,
                    "qualityAccepted": False, "claimBoundary": "Native interior optimisation under the unchanged external watchdog; full source boundary equality and every-cell quality remain mandatory."}
        progress["interiorOptimisationDiagnostics"] = evidence
        checkpoint(progress, "native-default-interior-optimisation")
        gmsh.model.mesh.optimize("", force=True)  # Native default ignores niter; the external watchdog owns the bound.
        evidence["nativeOptimisationReturned"] = True
        # Quality reads before optimisation populate Gmsh's element lookup.
        # Native edge swaps delete/add tetrahedra; rebuild before any fresh read.
        gmsh.model.mesh.rebuildElementCache(onlyIfNecessary=False)
        evidence["elementCacheRebuiltAfterOptimisation"] = True
        del points, tags, cells  # Retain bounded summaries, not both pre/post cell arrays.
        points, tags, cells = inspect_exported_volume(gmsh, volume, name, progress)
        evidence["after"] = progress["generatedVolumeDiagnostics"]
        after_surfaces = {tag: _triangles_for_surface(gmsh, tag) for dim, tag in gmsh.model.getEntities(2)}
        after_groups, after_audit = preserved_boundary(after_surfaces, owners, originals, native_points, points)
        if after_groups != groups:
            raise RemeshIssue("Interior optimisation must retain every exact native source-zone surface entity.")
        evidence.update(completeNativeBoundaryAfterVerified=True, boundaryAfterAudit=after_audit)
    checkpoint(progress, "quality-and-preserved-boundary-equality")
    metrics, quality = tetra_metrics(cells, points), _quality(gmsh, tags)
    final_surfaces = {tag: _triangles_for_surface(gmsh, tag) for dim, tag in gmsh.model.getEntities(2)}
    final_groups, final_boundary_audit = preserved_boundary(final_surfaces, owners, originals, native_points, points)
    if final_groups != groups:
        raise RemeshIssue("Volume fill must retain the same exact source-zone surface entities.")
    closure = volume_closure(zones, native_points, metrics["totalTetraVolumeAssumedM3"])
    progress["generatedVolumeDiagnostics"]["nativeBoundaryVolumeClosure"] = closure
    if not closure["passed"]:
        checkpoint(progress, "native-boundary-volume-closure-rejected")
        raise RemeshIssue("The positive tetrahedral volume sum must match the exact closed native fan volume within the declared relative tolerance.")
    progress["generatedVolumeDiagnostics"]["qualityAndBoundaryGatesPassed"] = True
    if optimisation["mode"] == "gmsh-interior":
        progress["interiorOptimisationDiagnostics"]["qualityAccepted"] = True
    if len(cells) >= model["nativeCounts"]["cellCount"]:
        raise RemeshIssue("The classroom candidate must contain fewer volume cells than its native source component.")
    boundary_rows = []
    for zone in zones:
        triangles = [triangle for entity in groups[zone["zoneId"]] for triangle in _triangles_for_surface(gmsh, entity)]
        identifiers = sorted({node for triangle in triangles for node in triangle})
        boundary_rows.append({"sourceZoneId": zone["zoneId"], "sourceZoneName": zone["zoneName"], "physicalGroupId": zone["zoneId"],
                              "classifiedEntityIds": groups[zone["zoneId"]], "sourceTriangleCount": len(zone["triangles"]),
                              "nativeTriangleOwnershipVerifiedBeforeRemesh": True,
                              "source": patch_metrics(zone["triangles"], native_points), "generated": patch_metrics(triangles, points),
                              "boundaryEquality": {"everyNativeTriangleAndBoundaryNodeChecked": True, "nativeBoundaryNodeCount": len(identifiers),
                                  "maximumCoordinateDifferenceNativeUnits": 0, "nativeWindingPreserved": True,
                                  "method": "Complete exact native node-ID/float64 coordinate and oriented triangle-triplet equality; no sampled projection or tag guess."}})
    checkpoint(progress, "msh2-publication")
    target = destination / (component + ".msh")
    if target.exists() or target.is_symlink():
        raise RemeshIssue("A generated mesh cannot overwrite an existing output.")
    gmsh.write(str(target))
    serialization = preserve_msh2_float64_nodes(target, points)
    if not target.is_file() or target.is_symlink() or not 0 < target.stat().st_size <= MAX_OUTPUT_BYTES:
        raise RemeshIssue("A regular bounded generated MSH file is required.")
    with target.open("rb") as stream:
        if stream.read(32).splitlines()[:2] != [b"$MeshFormat", b"2.2 0 8"]:
            raise RemeshIssue("The converter input must use actual MSH2.2 ASCII storage.")
    digest = sha256()
    with target.open("rb") as stream:
        while part := stream.read(1024**2):
            digest.update(part)
    # Re-read every actual serialized triangle/tetrahedron with the independent
    # converter admission reader. In-memory native quality cannot certify a
    # differently rounded file, and no zero/negative cell is tolerated here.
    import frozen_rotor_openfoam_case as converter_admission
    published = converter_admission.msh_inventory(target.read_bytes())
    if published["nodeCount"] != len(points) or published["cellCount"] != len(cells):
        raise RemeshIssue("The actual serialized mesh must preserve every generated node and volume cell.")
    serialization["everyPublishedCellPositiveVolumeVerified"] = True
    serialization["independentSerializedAdmissionReader"] = "frozen_rotor_openfoam_case.msh_inventory"
    result = {"sourceMesh": name, "generatedFile": {"name": target.name, "bytes": target.stat().st_size, "sha256": digest.hexdigest(), "format": "gmsh-msh2.2-ascii"},
              "sourceCellCount": model["nativeCounts"]["cellCount"], "sourceNodeCount": model["nativeCounts"]["nodeCount"],
              "generatedCellCount": len(cells), "generatedNodeCount": len(points), "sourceBoundaryAudit": native_audit,
              "coordinateSerialization": serialization,
              "sourceSeamPartition": {"method": "Exact native-zone discrete surfaces and createTopology(True,True); all original facets fixed with MeshOnlyEmpty=1 and no surface reparametrization",
                  "partitionCount": len(zones), "finalSurfaceEntityCount": len(surface_rows), "completeNativeTriangleCoverageVerified": True,
                  "mixedSourceZoneEntityCount": 0, "nativeGlobalNodeCoordinatesVerified": True, "nearestTagGuessPerformed": False},
              "fixedNativeFanBoundaryAudit": fixed_audit, "finalPreservedBoundaryAudit": final_boundary_audit,
              "boundaryRemeshed": False, "surfaceReparametrizationPerformed": False,
              "sourceBoundaryBoundsAssumedM": native_box, "generatedBoundsAssumedM": bounds(list(points.values())),
              "cellMetrics": metrics, "nativeBoundaryVolumeClosure": closure, "gmshQuality": quality, "boundaryZones": boundary_rows,
              "optimisation": optimisation,
              "interiorOptimisationDiagnostics": progress.get("interiorOptimisationDiagnostics"),
              "volumePhysicalGroup": "rotating-fluid" if component == "impeller" else "stationary-fluid",
              "equationStages": [{"input": {"sourceBoundaryBoundsNative": native_box, "sizingDivisor": SIZING_DIVISOR, "assumedMetresPerNativeUnit": 1},
                  "equation": "h_trial = max(xmax-xmin, ymax-ymin, zmax-zmin) / 32",
                  "substitution": {"maximumExtent": size*SIZING_DIVISOR, "divisor": SIZING_DIVISOR},
                  "result": {"value": size, "unit": "m under explicit classroom scale assumption"}},
                  {"input": {"actualOrderedTetrahedra": len(cells)}, "equation": "Vtet = dot(b-a,cross(c-a,d-a))/6; Vmesh = sum(Vtet)",
                   "substitution": metrics, "result": {"value": metrics["totalTetraVolumeAssumedM3"], "unit": "m3 under explicit classroom scale assumption"}}]}
    gmsh.model.remove()
    return result


def preserve_msh2_float64_nodes(target, points):
    """Round-trip actual Gmsh float64 coordinates; keep all topology bytes.

    Gmsh 4.15.2 MVertex::writeMSH2 uses %.16g, which can alter a double.
    This solely replaces the attempt-owned $Nodes numeric spelling with .17g.
    It never moves a point, changes a tag/connectivity or repairs a cell.
    """
    target = Path(target)
    if target.is_symlink() or not target.is_file() or not 0 < target.stat().st_size <= MAX_OUTPUT_BYTES:
        raise RemeshIssue("Round-trip serialization requires the actual bounded fresh MSH output.")
    temporary = target.with_name(target.name + ".roundtrip")
    if temporary.exists() or temporary.is_symlink():
        raise RemeshIssue("Use a fresh owned coordinate-serialization file.")
    seen, written, sections, maximum_difference = set(), 0, 0, 0.
    try:
        with target.open("rb") as source, temporary.open("xb") as destination:
            def emit(raw):
                nonlocal written
                written += len(raw)
                if written > MAX_OUTPUT_BYTES:
                    raise RemeshIssue("The serialized actual MSH exceeds its byte bound.")
                destination.write(raw)
            for line in source:
                emit(line)
                if line.strip() != b"$Nodes":
                    continue
                sections += 1
                count_line = next(source, b"")
                if sections != 1 or not re.fullmatch(rb"\d+\s*", count_line) or int(count_line) != len(points):
                    raise RemeshIssue("MSH nodes must preserve the complete unique actual Gmsh node inventory.")
                emit(count_line)
                for _ in range(int(count_line)):
                    values = next(source, b"").split()
                    if len(values) != 4 or not re.fullmatch(rb"\d+", values[0]):
                        raise RemeshIssue("Actual ASCII MSH node records must be complete.")
                    node = int(values[0])
                    if node in seen or node not in points:
                        raise RemeshIssue("MSH serialization cannot change any actual global node ID.")
                    xyz, stored = points[node], tuple(float(value) for value in values[1:])
                    if len(xyz) != 3 or not all(math.isfinite(value) for value in (*xyz, *stored)):
                        raise RemeshIssue("All actual and serialized coordinates must be finite.")
                    # Only the known %.16g spelling is allowed as the input;
                    # mismatched coordinates cannot be silently overwritten.
                    if stored != tuple(float(format(value, ".16g")) for value in xyz):
                        raise RemeshIssue("The generated writer coordinates differ from the actual Gmsh points.")
                    maximum_difference = max(maximum_difference, *(abs(a-b) for a,b in zip(xyz, stored)))
                    rendered = tuple(format(value, ".17g") for value in xyz)
                    if tuple(float(value) for value in rendered) != tuple(xyz):
                        raise RemeshIssue("Every coordinate must round-trip to the exact actual float64 value.")
                    emit((str(node) + " " + " ".join(rendered) + "\n").encode("ascii")); seen.add(node)
                if next(source, b"").strip() != b"$EndNodes":
                    raise RemeshIssue("The exact complete MSH node-section terminator is required.")
                emit(b"$EndNodes\n")
        if sections != 1 or seen != set(points):
            raise RemeshIssue("Serialized nodes must account for every actual point exactly once.")
        os.replace(temporary, target)
    finally:
        if temporary.is_file() and not temporary.is_symlink():
            temporary.unlink()  # Only the exact file created by this attempt.
    return {"significantDigits": 17, "actualNodeCount": len(seen), "allCoordinatesFloat64RoundTripVerified": True,
            "maximumGmsh16DigitSpellingDifferenceNativeUnits": maximum_difference,
            "maximumPublishedCoordinateDifferenceFromActualGmshPoints": 0,
            "topologyAndPhysicalTagsChanged": False, "sourceCoordinatesMoved": False,
            "reference": "https://github.com/live-clones/gmsh/blob/gmsh_4_15_2/src/geo/MVertex.cpp#L132"}


def enforce_runtime_limits():
    if sys.platform != "linux":
        raise RemeshIssue("Execute native Gmsh only on the isolated standard GitHub Ubuntu worker.")
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "1"
    def expired(signum, frame):
        raise RemeshIssue(f"The bounded {MAX_SECONDS}-second classroom meshing attempt timed out.")
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(MAX_SECONDS)


def run(value, optimisation_mode="full"):
    optimisation = optimisation_metadata(optimisation_mode)
    destination = checked_output(value)
    started = time.monotonic()
    progress = {"component": None, "stage": "runtime-preflight", "started": started, "destination": destination,
                "optimisationMode": optimisation_mode}
    destination.mkdir(parents=True)
    checkpoint(progress, "runtime-preflight")
    cache = destination.parent / "frozen-rotor-mesh-inputs"
    report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(),
              "attemptMode": "native-boundary-preserved-tetra-fill", "boundaryRemeshed": False,
              "gmshAlgorithm3D": GMSH_ALGORITHM_3D, "gmshAlgorithmName": "HXT",
              "optimisation": optimisation,
              "algorithmChangeBasis": "The previous Delaunay1 fixed-boundary attempt reached its 300-second limit. HXT10 is the documented efficient Delaunay implementation; no geometry or source-identity gate is relaxed. Actual HXT run38160027289 progressed through refinement into GSC optimisation at the 300-second limit; this separate attempt permits 900 seconds of bounded native work.",
              "surfaceReparametrizationPerformed": False,
              "exampleDataCommit": inventory.PIN, "gmshRequiredVersion": GMSH_VERSION, "references": REFERENCES,
              "coordinateUnits": "UNVERIFIED", "classroomScaleAssumption": {"nativeUnitsToMetres": 1, "verifiedFromSource": False,
                  "label": "Explicit teaching assumption: one native coordinate unit equals one metre; physical interpretation requires unit verification."},
              "limits": {"threads": 1, "maximumSeconds": MAX_SECONDS, "maximumAddressSpaceBytes": MAX_MEMORY_BYTES,
                  "maximumOutputBytes": MAX_OUTPUT_BYTES, "maximumReportBytes": inventory.MAX_REPORT_BYTES,
                  "maximumCellsPerComponent": MAX_NEW_CELLS_PER_COMPONENT, "maximumNodesPerComponent": MAX_NEW_NODES_PER_COMPONENT,
                  "maximumCombinedCells": MAX_COMBINED_CELLS, "maximumCombinedNodes": MAX_COMBINED_NODES,
                  "rawOutputBudgetChange": "128 MiB raw MSH2 ASCII trial budget; compressed publication budget is separately enforced."},
              "sources": [], "meshes": [], "files": [], "status": "PREPARATION_REQUIRED", "volumeMeshGenerated": False,
              "originalMeshesImmutable": False, "nativeCoordinateUnitsVerified": False, "sourceGeometryFabricated": False,
              "nodeWeldingPerformed": False, "topologyHealingPerformed": False, "nativeCaseGenerated": False,
              "fluentSessionConnected": False, "licenseVerified": False, "actualCfdRunVerified": False,
              "simulationExecuted": False, "mainApplicationWrites": False, "openFoamConversionVerified": False,
              "claimBoundary": "Classroom tetrahedral volume fill only; all native boundary facets remain fixed. Native units unverified; separate components and source patches preserved. OpenFOAM conversion, coupled interface, wall resolution, solver setup and CFD validation remain separate stages."}
    gmsh = None
    try:
        enforce_runtime_limits()
        import gmsh as native_gmsh
        import numpy as np
        gmsh = native_gmsh
        if gmsh.__version__ != GMSH_VERSION:
            raise RemeshIssue("Install exactly the pinned Gmsh version on the public worker.")
        gmsh.initialize([], readConfigFiles=False, run=False)
        gmsh.option.setNumber("General.Terminal", 1)  # Supervisor persists a bounded native tail while C++ runs.
        gmsh.option.setNumber("General.Verbosity", 4)
        gmsh.logger.start()
        for option in ("General.NumThreads", "Mesh.MaxNumThreads1D", "Mesh.MaxNumThreads2D", "Mesh.MaxNumThreads3D"):
            gmsh.option.setNumber(option, 1)
        report["gmshVersion"] = gmsh.__version__
        if cache.is_symlink() or getattr(cache, "is_junction", lambda: False)():
            raise RemeshIssue("Source cache must be a regular isolated runner directory.")
        cache.mkdir(exist_ok=True)
        for name in inventory.MESH_NAMES:
            progress["component"] = name.split(".")[0]
            checkpoint(progress, "verified-native-source-transfer")
            source = inventory.download_mesh(cache, name)
            provenance = inventory.verify_file(source, name)
            report["sources"].append(provenance)
            progress["verifiedSources"] = list(report["sources"])
        for name in inventory.MESH_NAMES:
            progress["component"] = name.split(".")[0]
            checkpoint(progress, "verified-native-source-read")
            source = cache / name
            model = boundary.read_native_mesh(source)
            report["meshes"].append(remesh_component(model, name, destination, gmsh, np, progress))
            inventory.verify_file(source, name)
        report["files"] = [mesh["generatedFile"] for mesh in report["meshes"]]
        if sum(row["bytes"] for row in report["files"]) > MAX_OUTPUT_BYTES-2*inventory.MAX_REPORT_BYTES:
            raise RemeshIssue("The combined meshes exceed the fixed 128 MiB raw output budget.")
        if sum(mesh["generatedCellCount"] for mesh in report["meshes"]) > MAX_COMBINED_CELLS or sum(mesh["generatedNodeCount"] for mesh in report["meshes"]) > MAX_COMBINED_NODES:
            raise RemeshIssue("The combined classroom candidate must remain below one million cells and nodes.")
        if sum(mesh["generatedCellCount"] for mesh in report["meshes"]) >= sum(mesh["sourceCellCount"] for mesh in report["meshes"]):
            raise RemeshIssue("The combined generated classroom mesh must be lighter than the source mesh.")
        report.update(status="VOLUME_MESH_GENERATED", volumeMeshGenerated=True, originalMeshesImmutable=True)
        checkpoint(progress, "completed")
    except Exception as error:
        report.update(status="PREPARATION_REQUIRED", volumeMeshGenerated=False, reasonCode="CFD_CLASSROOM_REMESH_REQUIRED",
                      diagnostic={"stage": progress["stage"], "component": progress["component"], "type": type(error).__name__, "message": str(error)[:500],
                                  **gmsh_diagnostic(gmsh, progress)},
                      correctiveAction="Review the exact failed meshing stage and source-zone constraints before a separate bounded retry.")
        report["meshes"], report["files"] = [], []
        for name in ("impeller.msh", "volute.msh"):
            target = destination / name
            if target.is_file() and not target.is_symlink():
                target.unlink()
    finally:
        if sys.platform == "linux":
            signal.alarm(0)
        if gmsh is not None and gmsh.isInitialized():
            try:
                gmsh.finalize()
            except Exception as error:
                report["finalizeIssue"] = type(error).__name__
    report["postAttemptSourceVerification"] = []
    for name in inventory.MESH_NAMES:
        before = next((row["sha256"] for row in report["sources"] if row["name"] == name), None)
        try:
            after = inventory.verify_file(cache/name, name)
            report["postAttemptSourceVerification"].append({"name": name, "beforeSha256": before, "afterSha256": after["sha256"],
                                                          "immutableVerified": before == after["sha256"]})
        except (inventory.InspectionIssue, OSError):
            report["postAttemptSourceVerification"].append({"name": name, "beforeSha256": before, "afterSha256": None, "immutableVerified": False})
    report["originalMeshesImmutable"] = all(row["immutableVerified"] for row in report["postAttemptSourceVerification"])
    if report["volumeMeshGenerated"] and not report["originalMeshesImmutable"]:
        report.update(status="PREPARATION_REQUIRED", volumeMeshGenerated=False, reasonCode="CFD_NATIVE_SOURCE_IMMUTABILITY_REQUIRED")
        report["meshes"], report["files"] = [], []
        for name in ("impeller.msh", "volute.msh"):
            target = destination/name
            if target.is_file() and not target.is_symlink():
                target.unlink()
    report["progressJournal"] = progress["journal"]
    report["elapsedSeconds"] = round(time.monotonic()-started, 3)
    for env, key, pattern in (("GITHUB_RUN_ID", "githubRunId", r"[0-9]{1,24}"), ("GITHUB_SHA", "githubSourceCommit", r"[0-9a-f]{40}")):
        raw = os.environ.get(env, "")
        report[key] = raw if re.fullmatch(pattern, raw) else None
    raw = inventory.bounded_json(report)
    with (destination / "remesh-report.json").open("xb") as stream:
        stream.write(raw)
    return {"status": report["status"], "stage": progress["stage"], "volumeMeshGenerated": report["volumeMeshGenerated"],
            "optimisationMode": optimisation_mode,
            "reportSha256": sha256(raw).hexdigest(), "reportBytes": len(raw), "files": report["files"],
            "totalGeneratedCells": sum(mesh["generatedCellCount"] for mesh in report["meshes"]),
            "actualCfdRunVerified": False, "mainApplicationWrites": False}


def child_environment():
    result = {key: os.environ[key] for key in CHILD_ENVIRONMENT_KEYS if key in os.environ}
    result.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
    return result


def stop_owned_worker(process):
    """Signal only the fresh session created by this supervisor's Popen handle."""
    if process.poll() is not None:
        return
    if os.getpgid(process.pid) != process.pid:
        raise RemeshIssue("The native worker no longer owns its isolated process group.")
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if process.poll() is None and os.getpgid(process.pid) == process.pid:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)


def interrupted_report(destination, reason, tail, elapsed, returncode, optimisation_mode="full"):
    """Recover factual progress and hashes after owned native-worker interruption."""
    optimisation = optimisation_metadata(optimisation_mode)
    progress = {}
    path = destination/"remesh-progress.json"
    if path.is_file() and not path.is_symlink() and path.stat().st_size <= inventory.MAX_REPORT_BYTES:
        progress = json.loads(path.read_text(encoding="utf-8"))
    before = {row["name"]:row["sha256"] for row in progress.get("verifiedSources", [])
        if row.get("name") in inventory.MESH_NAMES and row.get("sha256") == inventory.FILES[row["name"]][1]
        and row.get("bytes") == inventory.FILES[row["name"]][0] and row.get("sourceUrl") == inventory.mesh_url(row["name"])}
    sources, checks = [], []
    for name in inventory.MESH_NAMES:
        try:
            verified = inventory.verify_file(destination.parent/"frozen-rotor-mesh-inputs"/name, name)
            sources.append(verified)
            checks.append({"name":name,"beforeSha256":before.get(name),"afterSha256":verified["sha256"],
                           "immutableVerified":before.get(name)==verified["sha256"]})
        except (inventory.InspectionIssue, OSError):
            checks.append({"name":name,"beforeSha256":before.get(name),"afterSha256":None,"immutableVerified":False})
    for name in ("impeller.msh", "volute.msh"):
        target = destination/name
        if target.is_file() and not target.is_symlink():
            target.unlink()  # Only outputs owned by this fresh isolated attempt.
    report = {"schemaVersion":SCHEMA,"createdAt":datetime.now(timezone.utc).isoformat(),"exampleDataCommit":inventory.PIN,
        "gmshRequiredVersion":GMSH_VERSION,"gmshAlgorithm3D":GMSH_ALGORITHM_3D,"gmshAlgorithmName":"HXT",
        "optimisation":optimisation,
        "attemptMode":"native-boundary-preserved-tetra-fill","status":"PREPARATION_REQUIRED","reasonCode":reason,
        "diagnostic":{"stage":progress.get("stage","worker-startup"),"component":progress.get("component"),
            "nativeProcessReturnCode":returncode,"loggerTail":list(tail),"surfaceZoneBindings":progress.get("surfaceZoneBindings",[]),
            "generatedVolumeDiagnostics":progress.get("generatedVolumeDiagnostics")},
        "interiorOptimisationDiagnostics":progress.get("interiorOptimisationDiagnostics"),
        "progressJournal":progress.get("journal",[]),"sources":sources,"postAttemptSourceVerification":checks,
        "originalMeshesImmutable":all(row["immutableVerified"] for row in checks),"elapsedSeconds":round(elapsed,3),
        "limits":{"threads":1,"maximumSeconds":MAX_SECONDS,"maximumAddressSpaceBytes":MAX_MEMORY_BYTES,"maximumOutputBytes":MAX_OUTPUT_BYTES},
        "coordinateUnits":"UNVERIFIED","classroomScaleAssumption":{"nativeUnitsToMetres":1,"verifiedFromSource":False},
        "files":[],"meshes":[],"volumeMeshGenerated":False,"boundaryRemeshed":False,"surfaceReparametrizationPerformed":False,
        "nativeCoordinateUnitsVerified":False,"sourceGeometryFabricated":False,"nodeWeldingPerformed":False,"topologyHealingPerformed":False,
        "nativeCaseGenerated":False,"fluentSessionConnected":False,"licenseVerified":False,"actualCfdRunVerified":False,
        "simulationExecuted":False,"mainApplicationWrites":False,"openFoamConversionVerified":False,
        "correctiveAction":"Review the retained native mesher tail and exact source-zone binding before a separate bounded retry.",
        "claimBoundary":"Interrupted meshing attempt; no complete validated volume mesh, solver case or CFD result is published."}
    persist_json(destination,"remesh-report.json",report)
    return report


def live_progress_summary(destination, tail):
    """Small measured facts for the classroom Actions log, never an acceptance."""
    row = {}
    path = destination / "remesh-progress.json"
    if path.exists():
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= inventory.MAX_REPORT_BYTES:
            raise RemeshIssue("Live progress must use the bounded regular attempt-owned report.")
        row = json.loads(path.read_bytes())
        if row.get("schemaVersion") != SCHEMA:
            raise RemeshIssue("Live progress requires the exact native progress schema.")
    measured = row.get("generatedVolumeDiagnostics") or {}
    return {"status": "NativeMeshInProgress", "stage": row.get("stage", "worker-startup"),
            "component": row.get("component"),
            "actualExportedVolumeCells": measured.get("exportedVolumeElementCount"),
            "nativeMinSicn": measured.get("nativeQualityMetrics", {}).get("minSICN"),
            "nativeTail": list(tail)[-3:], "volumeMeshAccepted": False,
            "actualCfdRunVerified": False, "mainApplicationWrites": False}


def supervised_run(value, optimisation_mode="full"):
    optimisation = optimisation_metadata(optimisation_mode)
    destination = checked_output(value)
    if sys.platform != "linux":
        raise RemeshIssue("Run the supervised native geometry trial only on the isolated GitHub Ubuntu worker.")
    started, tail, pending, received, timed_out = time.monotonic(), deque(maxlen=NATIVE_TAIL_LINES), b"", 0, False
    last_live_progress = started - 30
    command = [sys.executable,"-B",str(Path(__file__).resolve()),"--native-worker","--output-dir",str(destination),
               "--optimisation-mode",optimisation_mode]
    process = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=child_environment(),
                               start_new_session=True,bufsize=0)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout,selectors.EVENT_READ)
    try:
        while selector.get_map():
            if time.monotonic() - last_live_progress >= 30:
                print(json.dumps(live_progress_summary(destination, tail), allow_nan=False), flush=True)
                last_live_progress = time.monotonic()
            if not timed_out and process.poll() is None and time.monotonic()-started >= MAX_SECONDS:
                timed_out = True
                stop_owned_worker(process)
            events = selector.select(timeout=.1)
            for key,_ in events:
                chunk = os.read(key.fileobj.fileno(),4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                lines = (pending+chunk.replace(b"\r",b"\n")).split(b"\n")
                pending = lines.pop()[-8192:]
                for line in lines:
                    text = line.decode("utf-8",errors="replace")
                    if text.startswith("REMESH_WORKER_SUMMARY "):
                        continue
                    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]","",text)
                    text = "".join(character for character in text if character.isprintable() or character=="\t").strip()
                    if text:
                        received += 1
                        tail.append(text[:NATIVE_LINE_CHARACTERS])
                if destination.is_dir():
                    persist_json(destination,"gmsh-native-tail.json",{"schemaVersion":"untirta.gmsh-native-tail.v1",
                        "gmshAlgorithm3D":GMSH_ALGORITHM_3D,"elapsedSeconds":round(time.monotonic()-started,3),
                        "optimisation":optimisation,
                        "receivedNativeLineCount":received,"lines":list(tail),"nativeWorkerTimedOut":timed_out,
                        "actualCfdRunVerified":False,"simulationExecuted":False,"mainApplicationWrites":False})
        returncode = process.wait(timeout=3)
        if time.monotonic()-started >= MAX_SECONDS:
            timed_out = True  # A completed process must still satisfy this attempt's wall-clock deadline.
    except BaseException:
        stop_owned_worker(process)
        raise
    finally:
        selector.close()
        process.stdout.close()
    if not destination.exists():
        destination.mkdir(parents=True)
    persist_json(destination,"gmsh-native-tail.json",{"schemaVersion":"untirta.gmsh-native-tail.v1",
        "gmshAlgorithm3D":GMSH_ALGORITHM_3D,"elapsedSeconds":round(time.monotonic()-started,3),
        "optimisation":optimisation,
        "receivedNativeLineCount":received,"lines":list(tail),"nativeWorkerTimedOut":timed_out,
        "nativeProcessReturnCode":returncode,"actualCfdRunVerified":False,"simulationExecuted":False,"mainApplicationWrites":False})
    path = destination/"remesh-report.json"
    report = None
    if not timed_out and returncode in (0,2) and path.is_file() and not path.is_symlink() and path.stat().st_size <= inventory.MAX_REPORT_BYTES:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("schemaVersion") != SCHEMA or report.get("actualCfdRunVerified") is not False or report.get("mainApplicationWrites") is not False or report.get("simulationExecuted") is not False or report.get("volumeMeshGenerated") is not (returncode==0) or report.get("optimisation") != optimisation:
            raise RemeshIssue("Native-worker evidence must preserve the exact preparation-only claim contract.")
    if report is None:
        reason = "CFD_CLASSROOM_MESH_WORKER_TIMEOUT" if timed_out else "CFD_CLASSROOM_MESH_WORKER_STOPPED"
        report = interrupted_report(destination,reason,tail,time.monotonic()-started,returncode,optimisation_mode)
    raw = path.read_bytes()
    return {"status":report["status"],"volumeMeshGenerated":report["volumeMeshGenerated"],"reportSha256":sha256(raw).hexdigest(),
        "optimisationMode":optimisation_mode,
        "reportBytes":len(raw),"files":report["files"],"totalGeneratedCells":sum(row["generatedCellCount"] for row in report["meshes"]),
        "actualCfdRunVerified":False,"mainApplicationWrites":False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--optimisation-mode", choices=OPTIMISATION_MODES, default="full")
    arguments = parser.parse_args()
    try:
        result = run(arguments.output_dir, arguments.optimisation_mode) if arguments.native_worker else supervised_run(arguments.output_dir, arguments.optimisation_mode)
        print(("REMESH_WORKER_SUMMARY " if arguments.native_worker else "")+json.dumps(result, allow_nan=False),flush=True)
        return 0 if result["volumeMeshGenerated"] else 2
    except (inventory.InspectionIssue, OSError, ValueError) as error:
        print(json.dumps({"status": "PREPARATION_REQUIRED", "reasonCode": "CFD_CLASSROOM_REMESH_REQUIRED", "action": str(error)[:500],
                          "volumeMeshGenerated": False, "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
