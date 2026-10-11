"""Derive a 32-pair original-volume geometry pilot; never execute a solver.

Only disjoint interior impeller hex pairs are joined. Their shared polygon is
removed, and all other native polygons and float64 points are retained. The
boundary-owning cells plus three adjacency expansions are protected. This is
an explicitly mapped polyhedral derivation, not the original-volume adapter,
native checkMesh evidence, a sub-million-cell mesh, or numerical convergence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import convert_frozen_rotor_native_h5 as original

SCHEMA = "untirta.frozen-rotor-native-cell-agglomeration.v1"
PAIR_COUNT = 32
PROTECTED_RINGS = 3
MAX_CANDIDATE_CHECKS = 10_000
VOLUME_TOLERANCE = 1e-10
REPORT_NAME = "agglomeration-report.json"
MAP_NAMES = {"sourceFaceIds", "sourceCellToDerivedCellIds", "removedSourceFaceIds"}
MAPPING_CHECK_KEYS = {"everySourceCellMappedExactlyOnce", "onlySelectedInternalFacesRemoved", "everyRetainedSourcePolygonMappedExactlyOnce",
    "allOriginalBoundaryPolygonsRetained", "allOriginalPointsRetained", "allCoordinatesAsciiRoundTripVerified", "ownerNeighbourOrderingVerified"}


class AgglomerationIssue(ValueError):
    pass


def require(condition, action):
    if not condition:
        raise AgglomerationIssue(action)


def cell_faces(model):
    """Compact original-cell CSR, retaining actual face and cell identifiers."""
    import numpy as np
    rows = [np.flatnonzero(model[key] > 0).astype("u4") for key in ("c0", "c1")]
    cells = np.concatenate([model[key][row] - 1 for key, row in zip(("c0", "c1"), rows)])
    faces = np.concatenate(rows)
    order = np.lexsort((faces, cells))
    degree = np.bincount(cells, minlength=model["cellCount"])
    offsets = np.r_[0, np.cumsum(degree, dtype="i8")]
    return faces[order], offsets


def polygon(model, face):
    return model["faceNodes"][model["faceOffsets"][face]:model["faceOffsets"][face + 1]]


def boundary_protection(model):
    """Protect distance 0 (boundary owners), then three complete face rings."""
    import numpy as np
    c0, c1 = model["c0"], model["c1"]
    boundary = (c0 == 0) ^ (c1 == 0)
    protected = np.zeros(model["cellCount"], dtype=bool)
    protected[np.maximum(c0[boundary], c1[boundary]) - 1] = True
    internal = np.flatnonzero((c0 > 0) & (c1 > 0))
    a, b = c0[internal] - 1, c1[internal] - 1
    layers = [int(protected.sum())]
    for _ in range(PROTECTED_RINGS):
        reached = protected[a] | protected[b]
        protected[a[reached]] = True
        protected[b[reached]] = True
        layers.append(int(protected.sum()))
    return protected, layers


def signed_volume(model, faces, member_ids):
    """Divergence volume using actual outward native polygon fans, unchanged."""
    import numpy as np
    reference = model["coordinates"][int(polygon(model, int(faces[0]))[0])]
    terms = []
    for face in faces:
        ids = polygon(model, int(face))
        xyz = model["coordinates"][ids] - reference
        sign = -1 if int(model["c0"][face]) in member_ids else 1
        terms.extend(float(sign * np.dot(xyz[0], np.cross(xyz[j], xyz[(j + 1) % len(xyz)])) / 6)
                     for j in range(len(xyz)))
    volume = math.fsum(terms)
    require(math.isfinite(volume) and volume > 0, "PairVolumeNotFinitePositive")
    return volume


def check_pair(model, face, a, b, incidence, offsets):
    """Two hexes may intersect only at their one complete shared quadrilateral."""
    import numpy as np
    fa = incidence[offsets[a]:offsets[a + 1]]
    fb = incidence[offsets[b]:offsets[b + 1]]
    shared = np.intersect1d(fa, fb)
    require(shared.tolist() == [face] and len(fa) == len(fb) == 6 and len(polygon(model, face)) == 4,
            "NotOneSharedQuad")
    va = set(int(v) for f in fa for v in polygon(model, int(f)))
    vb = set(int(v) for f in fb for v in polygon(model, int(f)))
    require(len(va) == len(vb) == 8 and va & vb == set(map(int, polygon(model, face))), "AdditionalCellContacts")
    outside = sorted((set(map(int, fa)) | set(map(int, fb))) - {face})
    require(len(outside) == 10, "UnionTopology")
    members = {a + 1, b + 1}
    edges = {}
    vertex_faces = {}
    for local_face, native_face in enumerate(outside):
        ids = list(map(int, polygon(model, native_face)))
        if int(model["c0"][native_face]) in members:
            ids.reverse()
        for v in ids:
            vertex_faces.setdefault(v, set()).add(local_face)
        for start, end in zip(ids, ids[1:] + ids[:1]):
            edges.setdefault((min(start, end), max(start, end)), []).append((local_face, 1 if start < end else -1))
    require(all(len(rows) == 2 and rows[0][1] + rows[1][1] == 0 for rows in edges.values()), "UnionTopology")
    require(len(vertex_faces) - len(edges) + len(outside) == 2, "UnionTopology")
    adjacent = [set() for _ in outside]
    for rows in edges.values():
        i, j = rows[0][0], rows[1][0]
        adjacent[i].add(j); adjacent[j].add(i)
    def connected(vertices, links):
        remaining = set(vertices); pending = [remaining.pop()]
        while pending:
            for neighbour in links[pending.pop()] & remaining:
                remaining.remove(neighbour); pending.append(neighbour)
        return not remaining
    require(connected(range(len(outside)), adjacent), "UnionTopology")
    for vertex, faces in vertex_faces.items():
        links = {f: set() for f in faces}
        for edge, rows in edges.items():
            if vertex in edge:
                i, j = rows[0][0], rows[1][0]
                links[i].add(j); links[j].add(i)
        require(all(len(row) == 2 for row in links.values()) and connected(faces, links), "UnionTopology")
    source_volumes = [signed_volume(model, fa, {a + 1}), signed_volume(model, fb, {b + 1})]
    union_volume = signed_volume(model, outside, members)
    expected = math.fsum(source_volumes)
    difference = abs(union_volume - expected) / max(union_volume, expected)
    require(difference <= VOLUME_TOLERANCE, "PairVolumeMismatch")
    return {"sourceCellIds": [a + 1, b + 1], "removedSourceFaceId": face + 1,
            "sourceCellVolumesAssumedM3": source_volumes, "unionVolumeAssumedM3": union_volume,
            "relativePairVolumeDifference": difference, "closedOrientedManifoldUnionVerified": True}


def select_pairs(model, *, requested=PAIR_COUNT):
    import numpy as np
    require(model["component"] == "impeller" and 1 <= requested <= PAIR_COUNT,
            "The bounded pilot selects only original impeller pairs.")
    protected, layers = boundary_protection(model)
    eligible = (~protected) & (model["cellTypes"] == 4)
    c0, c1 = model["c0"], model["c1"]
    interior = np.flatnonzero((c0 > 0) & (c1 > 0))
    candidates = interior[eligible[c0[interior] - 1] & eligible[c1[interior] - 1] & (model["faceWidths"][interior] == 4)]
    incidence, offsets = cell_faces(model)
    used = np.zeros(model["cellCount"], dtype=bool)
    pairs = []; rejected = Counter(); checked = 0; exhausted = True
    for face in candidates:
        a, b = sorted((int(c0[face]) - 1, int(c1[face]) - 1))
        if used[a] or used[b]:
            continue
        if checked == MAX_CANDIDATE_CHECKS:
            exhausted = False; break
        checked += 1
        try:
            item = check_pair(model, int(face), a, b, incidence, offsets)
        except AgglomerationIssue as error:
            rejected[str(error)] += 1; continue
        used[a] = used[b] = True; pairs.append(item)
        if len(pairs) == requested:
            exhausted = False; break
    return pairs, {"requestedPairCount": requested, "selectedPairCount": len(pairs),
        "protectedBoundaryAdjacencyRings": PROTECTED_RINGS, "protectedCellCountsByDistance": layers,
        "protectionDefinition": "Boundary-owning cells (distance 0) and all cells at face-adjacency distances 1, 2 and 3 are unchanged.",
        "eligibleHexCellCount": int(eligible.sum()), "eligibleInternalQuadFaceCount": len(candidates),
        "checkedCandidateCount": checked, "maximumCandidateChecks": MAX_CANDIDATE_CHECKS,
        "candidateInventoryExhausted": exhausted, "maximumMatchingComputed": False, "rejectedCandidateReasonCounts": dict(rejected),
        "selectionOrder": "Ascending original 1-based face ID; disjoint accepted pairs; lower source cell ID is master."}


def derive_model(model, pairs):
    import numpy as np
    require(len(pairs) <= PAIR_COUNT and (not pairs or model["component"] == "impeller"), "Only the fixed bounded impeller pilot is supported.")
    masters = np.arange(model["cellCount"], dtype="u4")
    removed = np.zeros(model["faceCount"], dtype=bool); used = set()
    incidence, offsets = cell_faces(model)
    protected, _ = boundary_protection(model)
    for pair in pairs:
        a, b = [int(v) - 1 for v in pair["sourceCellIds"]]; face = int(pair["removedSourceFaceId"]) - 1
        require(0 <= a < b < model["cellCount"] and not ({a, b} & used), "Every source cell has one disjoint pair membership.")
        require(not protected[a] and not protected[b], "Boundary-owning cells and all three protected adjacency rings must remain unchanged.")
        require(model["cellTypes"][a] == model["cellTypes"][b] == 4 and 0 <= face < model["faceCount"]
                and {int(model["c0"][face]), int(model["c1"][face])} == {a + 1, b + 1}, "Only actual adjacent original hex pairs may be joined.")
        require(check_pair(model, face, a, b, incidence, offsets) == pair, "Pair evidence must match the current original topology and coordinates.")
        used.update((a, b)); masters[b] = a; removed[face] = True
    kept = np.flatnonzero(~removed).astype("u4")
    unique = np.unique(masters); cell_map = np.searchsorted(unique, masters).astype("u4")
    widths = model["faceWidths"][kept]
    derived = {**model, "faceCount": len(kept), "cellCount": len(unique), "faceWidths": widths,
               "faceOffsets": np.r_[0, np.cumsum(widths, dtype="i8")],
               "faceNodes": model["faceNodes"][np.repeat(~removed, model["faceWidths"])],
               "cellTypes": model["cellTypes"][unique].copy()}
    for key in ("c0", "c1"):
        ids = model[key][kept]; derived[key] = np.zeros(len(ids), dtype="u4")
        positive = ids > 0; derived[key][positive] = cell_map[ids[positive] - 1] + 1
    require(np.all(derived["c0"] != derived["c1"]), "Only explicitly selected shared faces may disappear; no hidden self-neighbour face is allowed.")
    for pair in pairs:
        derived["cellTypes"][cell_map[pair["sourceCellIds"][0] - 1]] = 7  # Explicit derived polyhedron, never a source hex claim.
    zones = []; cursor = 0
    for zone in model["faceZones"]:
        count = int((~removed[zone["minimumId"] - 1:zone["maximumId"]]).sum())
        if count:
            zones.append({**zone, "minimumId": cursor + 1, "maximumId": cursor + count}); cursor += count
    derived["faceZones"] = zones
    require(cursor == len(kept) and np.array_equal(derived["coordinates"], model["coordinates"]), "Complete native face zones and every original coordinate must be retained.")
    node_used = np.zeros(model["nodeCount"], dtype=bool); node_used[derived["faceNodes"]] = True
    require(node_used.all(), "This pilot cannot drop an unused original point or weld any point.")
    for original_zone, derived_zone in zip(model["faceZones"], zones):
        require(original_zone["name"] == derived_zone["name"] and original_zone["zoneId"] == derived_zone["zoneId"], "Source tags must not be renamed or reclassified.")
        if original_zone["zoneType"] != 2:
            lo, hi = original_zone["minimumId"] - 1, original_zone["maximumId"]
            require(not removed[lo:hi].any() and derived_zone["maximumId"] - derived_zone["minimumId"] == hi - lo - 1,
                    "Every original boundary polygon and zone count must remain unchanged.")
    return derived, kept + 1, cell_map, np.flatnonzero(removed).astype("u4") + 1


def label_lines(values):
    yield str(len(values)) + "\n(\n"
    for first in range(0, len(values), 4096):
        yield "".join(str(int(v)) + "\n" for v in values[first:first + 4096])
    yield ")\n"


def write_component(root, model, source_model, checks, face_map, cell_map, removed, budget):
    import numpy as np
    order, owner, neighbour, reverse, patches = original.face_order(model)
    name = model["component"]; case = name + "-case"; files = []; maps = []
    def emit(relative, kind, lines, rows, location=None):
        writer = original.Writer(root, relative, rows, budget)
        try:
            writer.write(original.guards.base.foam_header(Path(relative).name, kind, location=location))
            for line in lines:
                writer.write(line)
        finally:
            writer.close()
    prefix = case + "/constant/polyMesh/"
    def points():
        yield str(model["nodeCount"]) + "\n(\n"
        for first in range(0, model["nodeCount"], 2048):
            xyz = model["coordinates"][first:first + 2048]
            tokens = [[format(float(v), ".17g") for v in point] for point in xyz]
            require(np.array_equal(np.asarray(tokens, dtype="f8"), xyz), "Every retained coordinate must round-trip with no movement through ASCII17 digits.")
            yield "".join("(" + " ".join(point) + ")\n" for point in tokens)
        yield ")\n"
    def faces():
        yield str(model["faceCount"]) + "\n(\n"
        for first in range(0, len(order), 2048):
            lines = []
            for face in order[first:first + 2048]:
                ids = polygon(model, int(face)); ids = ids[::-1] if reverse[face] else ids
                lines.append(str(len(ids)) + "(" + " ".join(str(int(v)) for v in ids) + ")\n")
            yield "".join(lines)
        yield ")\n"
    for role, kind, lines in (("points", "vectorField", points()), ("faces", "faceList", faces()),
            ("owner", "labelList", label_lines(owner[order])), ("neighbour", "labelList", label_lines(neighbour))):
        emit(prefix + role, kind, lines, files, "constant/polyMesh")
    source_zones = {row["zoneId"]: row for row in source_model["faceZones"]}
    for patch in patches:
        zone = source_zones[patch["sourceZoneId"]]
        patch["sourceMinimumFaceId"], patch["sourceMaximumFaceId"] = zone["minimumId"], zone["maximumId"]
    boundary = [str(len(patches)) + "\n(\n"] + [p["name"] + "\n{\ntype " + p["type"] + ";\nnFaces " + str(p["nFaces"]) + ";\nstartFace " + str(p["startFace"]) + ";\n}\n" for p in patches] + [")\n"]
    emit(prefix + "boundary", "polyBoundaryMesh", iter(boundary), files, "constant/polyMesh")
    zone_name = original.COMPONENTS[name][1]
    def cell_zones():
        yield "1\n(\n" + zone_name + "\n{\ntype cellZone;\ncellLabels List<label>\n"
        yield from label_lines(range(model["cellCount"]))
        yield ";\n}\n)\n"
    emit(prefix + "cellZones", "regIOobject", cell_zones(), files, "constant/polyMesh")
    for role, values in (("sourceFaceIds", face_map[order]), ("sourceCellToDerivedCellIds", cell_map), ("removedSourceFaceIds", removed)):
        emit(case + "/" + role, "labelList", label_lines(values), maps)
    kinds, counts = np.unique(model["cellTypes"], return_counts=True)
    return {"name": name, "sourceFile": source_model["sourceFile"], "casePath": case,
            "sourceCounts": {key: source_model[key] for key in ("cellCount", "nodeCount", "faceCount")},
            **{key: model[key] for key in ("cellCount", "nodeCount", "faceCount")}, "internalFaceCount": len(neighbour),
            "boundaryFaceCount": sum(p["nFaces"] for p in patches), "patches": patches,
            "cellZone": {"name": zone_name, "sourceName": name, "sourceZoneId": original.COMPONENTS[name][2], "cellCount": model["cellCount"]},
            "nativeFiles": files, "mappingFiles": maps, "derivedCellTypeCounts": {str(int(k)): int(n) for k, n in zip(kinds, counts)},
            "derivedCellAudit": checks, "mappingChecks": {"everySourceCellMappedExactlyOnce": True,
                "onlySelectedInternalFacesRemoved": True, "everyRetainedSourcePolygonMappedExactlyOnce": True,
                "allOriginalBoundaryPolygonsRetained": True, "allOriginalPointsRetained": True,
                "allCoordinatesAsciiRoundTripVerified": True, "ownerNeighbourOrderingVerified": True}}


def agglomerate(source_directory, output_directory):
    import numpy as np
    inputs = original.guards.inside_runner(source_directory)
    destination = Path(output_directory).absolute()
    original.inventory.output_path(destination / REPORT_NAME)
    require(not destination.exists() and not destination.is_symlink() and not destination.resolve().is_relative_to(inputs.resolve())
            and not inputs.resolve().is_relative_to(destination.resolve()), "Use a fresh output directory independent of the immutable input directory.")
    sources = [original.inventory.verify_file(inputs / name, name) for name in original.inventory.MESH_NAMES]
    destination.mkdir(parents=True)
    report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(), "exampleDataCommit": original.inventory.PIN,
        "status": "GeometryPilotNotDerived", "sources": sources, "sourceAudits": {}, "components": [],
        "coordinateUnits": "UNVERIFIED", "nativeCoordinateUnitsVerified": False, "classroomMetresPerSourceCoordinateUnit": 1,
        "requestedPairCount": PAIR_COUNT, "volumeMeshDerived": False, "originalMeshesImmutable": False,
        "studentCellCapMet": False, "studentLicenceRouteUsed": False, "meshQualityCheckedBySolver": False,
        "nativeCaseGenerated": False, "qualifiedBaseline": False, "fluentSessionConnected": False, "licenseVerified": False,
        "sourceNativeMeshQualified": False, "nativeCheckMeshExecuted": False,
        "simulationExecuted": False, "actualCfdRunVerified": False, "mainApplicationWrites": False,
        "limits": {"maximumPairCount": PAIR_COUNT, "maximumCandidateChecks": MAX_CANDIDATE_CHECKS,
            "maximumFileBytes": original.MAX_FILE, "maximumDerivedBytes": original.MAX_OUTPUT,
            "maximumWorkerAddressSpaceBytes": original.MAX_MEMORY},
        "claimBoundary": "32-pair geometry-only pilot. Exact retained coordinates/polygons and complete cell membership are conversion evidence, not native checkMesh, mesh quality, sub-million-cell readiness, CFD, NPSH3, restart or convergence. Metre scale is an unverified classroom assumption."}
    budget = [0]
    try:
        models = {name: original.read_mesh(inputs / original.COMPONENTS[name][0], name) for name in original.COMPONENTS}
        for name, model in models.items():
            report["sourceAudits"][name] = original.audit_cells(model)
        pairs, selection = select_pairs(models["impeller"])
        report["selection"] = selection
        if len(pairs) != PAIR_COUNT:
            report["status"] = "GeometryPilotEligiblePairsRequired"
            report["action"] = "Measured deterministic selection did not establish 32 acceptable protected interior pairs. Review the reported eligibility and scan bounds; no partial pair mesh was produced."
        else:
            for name, model in models.items():
                selected = pairs if name == "impeller" else []
                derived, face_map, cell_map, removed = derive_model(model, selected)
                checks = original.audit_cells(derived)
                checks["allDerivedCellsChecked"] = checks.pop("allOriginalCellsChecked")
                checks["derivedCellCountChecked"] = checks.pop("originalCellCountChecked")
                difference = abs(checks["totalCellVolumeAssumedM3"] - report["sourceAudits"][name]["totalCellVolumeAssumedM3"]) / report["sourceAudits"][name]["totalCellVolumeAssumedM3"]
                require(difference <= VOLUME_TOLERANCE, "Every derived volume must equal its complete original component volume.")
                checks["relativeSourceDerivedVolumeDifference"] = difference
                checks["sourceDerivedVolumeAgreementVerified"] = True
                row = write_component(destination, derived, model, checks, face_map, cell_map, removed, budget)
                if name == "impeller":
                    row["pairs"] = [{**pair, "derivedCellIndex": int(cell_map[pair["sourceCellIds"][0] - 1])} for pair in pairs]
                else:
                    row["pairs"] = []
                report["components"].append(row)
                del derived, face_map, cell_map, removed
            report["combinedCounts"] = {key: sum(row[key] for row in report["components"]) for key in ("cellCount", "nodeCount", "faceCount")}
            report["sourceCombinedCounts"] = {key: sum(model[key] for model in models.values()) for key in ("cellCount", "nodeCount", "faceCount")}
            require(report["combinedCounts"]["cellCount"] == report["sourceCombinedCounts"]["cellCount"] - PAIR_COUNT,
                    "The pilot must remove exactly one cell per explicitly bound pair.")
            report["equationStages"] = [{"input": {"sourceCellCount": report["sourceCombinedCounts"]["cellCount"], "acceptedPairCount": PAIR_COUNT},
                "equation": "N_derived = N_source - N_disjoint_pairs", "substitution": str(report["sourceCombinedCounts"]["cellCount"]) + " - 32",
                "result": {"derivedCellCount": report["combinedCounts"]["cellCount"], "units": "cells"}},
                {"input": "Each two-cell union uses all original outward polygons except its shared internal polygon.",
                 "equation": "V_union = sum_f dot(x_0, cross(x_j, x_(j+1))) / 6 = V_a + V_b",
                 "substitution": "Actual per-pair signed native-coordinate terms and source volumes are bound in components.impeller.pairs.",
                 "result": {"pairsChecked": PAIR_COUNT, "relativeVolumeTolerance": VOLUME_TOLERANCE, "units": "assumed m^3"}}]
            report["status"] = "GeometryPilotDerivedNativeAcceptanceRequired"
            report["volumeMeshDerived"] = True
            report["studentCellCapMet"] = report["combinedCounts"]["cellCount"] < 1_000_000
    except (ValueError, OSError, KeyError, TypeError, IndexError, ImportError, MemoryError, OverflowError) as error:
        report["status"] = "GeometryPilotNotDerived"
        report["action"] = str(error)[:500]
        report["volumeMeshDerived"] = False
        report["partialOutputsRetained"] = True
    try:
        after = [original.inventory.verify_file(inputs / name, name) for name in original.inventory.MESH_NAMES]
        report["sourcesAfterDerivation"] = after
        report["originalMeshesImmutable"] = after == sources
        require(after == sources, "Immutable original mesh bytes changed during the pilot.")
    except (ValueError, OSError) as error:
        report.update(status="GeometryPilotNotDerived", volumeMeshDerived=False, originalMeshesImmutable=False, action=str(error)[:500])
    report["derivedMeshAndMapBytes"] = budget[0]
    raw = original.inventory.bounded_json(report)
    require(budget[0] + len(raw) <= original.MAX_OUTPUT, "The complete geometry pilot exceeds its declared output byte bound.")
    original.guards.base.write_exclusive(destination / REPORT_NAME, raw)
    return report


def read_map(root, row, count):
    import numpy as np
    path = root / row["path"]
    text = original.guards.native_bytes(path).decode("ascii")
    match = re.fullmatch(r"\s*FoamFile\s*\{([^{}]*)\}\s*(\d+)\s*\(\s*((?:\d+\s*)*)\)\s*", text, re.S)
    require(match and re.search(r"\bclass\s+labelList\s*;", match[1]) and re.search(r"\bobject\s+" + re.escape(path.name) + r"\s*;", match[1])
            and int(match[2]) == count, "An exact counted plain native mapping list is required.")
    values = np.fromstring(match[3], dtype="i8", sep=" ")
    require(len(values) == count, "Every declared source-to-derived map entry must actually be stored.")
    return values


def verify_audit(audit, count, *, derived=False):
    label = "Derived" if derived else "Original"
    require(audit.get("all" + label + "CellsChecked") is True and audit.get(label.lower() + "CellCountChecked") == count
            and audit.get("directedEdgeIncidenceClosed") is True and audit.get("standardCellShapeIncidenceVerified") is True
            and audit.get("cellBoundaryVolumeAgreementVerified") is True
            and audit.get("relativeAreaClosureTolerance") == VOLUME_TOLERANCE
            and 0 <= audit.get("maximumRelativeAreaVectorClosureError", math.inf) <= VOLUME_TOLERANCE
            and all(audit.get(key) is False for key in ("nativeCoordinateUnitsVerified", "geometricSelfIntersectionChecked", "meshQualityCheckedBySolver"))
            and audit.get("classroomMetresPerSourceCoordinateUnit") == 1,
            "Every source and derived cell must retain its complete independent closure checks and explicit native limitations.")
    keys = ("minimumCellVolumeAssumedM3", "maximumCellVolumeAssumedM3", "totalCellVolumeAssumedM3", "boundarySignedVolumeAssumedM3")
    require(all(isinstance(audit.get(key), (float, int)) and not isinstance(audit[key], bool) and math.isfinite(audit[key]) and audit[key] > 0 for key in keys),
            "Every cell/boundary divergence-volume summary must be finite and strictly positive.")
    minimum, maximum, total, boundary = [audit[key] for key in keys]
    relative = abs(total - boundary) / max(total, boundary)
    require(minimum <= maximum <= total and relative <= VOLUME_TOLERANCE
            and math.isclose(audit.get("relativeCellBoundaryVolumeDifference", math.inf), relative, rel_tol=1e-12, abs_tol=1e-15),
            "Measured cell volumes and independent external integration must actually agree.")


def verify_output(output_directory):
    """Current byte/map verification only; no native mesh or simulation claim."""
    import numpy as np
    root = original.guards.base.regular_directory(output_directory)
    report = original.guards.json_file(root / REPORT_NAME)
    require(report.get("schemaVersion") == SCHEMA and report.get("exampleDataCommit") == original.inventory.PIN
            and report.get("status") == "GeometryPilotDerivedNativeAcceptanceRequired" and report.get("volumeMeshDerived") is True
            and report.get("originalMeshesImmutable") is True and report.get("sourcesAfterDerivation") == report.get("sources")
            and report.get("requestedPairCount") == PAIR_COUNT and report.get("selection", {}).get("selectedPairCount") == PAIR_COUNT,
            "A source-pinned complete 32-pair geometry pilot is required.")
    require(report.get("coordinateUnits") == "UNVERIFIED" and report.get("nativeCoordinateUnitsVerified") is False
            and report.get("classroomMetresPerSourceCoordinateUnit") == 1 and all(report.get(key) is False for key in
            ("meshQualityCheckedBySolver", "nativeCaseGenerated", "qualifiedBaseline", "simulationExecuted", "actualCfdRunVerified", "mainApplicationWrites",
             "fluentSessionConnected", "licenseVerified", "studentLicenceRouteUsed", "sourceNativeMeshQualified", "nativeCheckMeshExecuted")),
            "The pilot cannot promote geometry checks to native CFD or unit verification.")
    selection = report["selection"]
    require(selection.get("requestedPairCount") == PAIR_COUNT and selection.get("protectedBoundaryAdjacencyRings") == PROTECTED_RINGS
            and selection.get("maximumMatchingComputed") is False and selection.get("maximumCandidateChecks") == MAX_CANDIDATE_CHECKS
            and isinstance(selection.get("checkedCandidateCount"), int) and 0 <= selection["checkedCandidateCount"] <= MAX_CANDIDATE_CHECKS,
            "The explicit three-ring fixed-pilot selection cannot be weakened by a later evidence projection.")
    require([row.get("name") for row in report["components"]] == list(original.COMPONENTS), "Both exact component meshes are required.")
    total = 0; normalized = []
    for row in report["components"]:
        component = row["name"]; source_counts = original.SOURCE_COUNTS[component]; name = original.COMPONENTS[component][0]
        size, digest = original.inventory.FILES[name]
        source_pin = {"name": name, "bytes": size, "sha256": digest, "sourceUrl": original.inventory.mesh_url(name)}
        removed_count = PAIR_COUNT if component == "impeller" else 0
        require(row.get("sourceFile") == source_pin and source_pin in report["sources"] and row.get("casePath") == component + "-case"
                and row.get("sourceCounts") == {key: source_counts[key] for key in ("cellCount", "nodeCount", "faceCount")}
                and row.get("cellCount") == source_counts["cellCount"] - removed_count and row.get("nodeCount") == source_counts["nodeCount"]
                and row.get("faceCount") == source_counts["faceCount"] - removed_count
                and row.get("internalFaceCount") == source_counts["internalFaceCount"] - removed_count,
                "Only the exactly mapped fixed pilot may change the original component inventory.")
        verify_audit(report["sourceAudits"][component], source_counts["cellCount"])
        rows = row["nativeFiles"] + row["mappingFiles"]
        expected_paths = {component + "-case/constant/polyMesh/" + name for name in original.MESH_FILES} | {component + "-case/" + name for name in MAP_NAMES}
        require(len(rows) == len(expected_paths) and {r.get("path") for r in rows} == expected_paths, "The exact six mesh roles and three source mapping roles are required.")
        for metadata in rows:
            original.guards.relative_path(metadata["path"])
            original.guards.base.regular_directory((root / metadata["path"]).parent)
            raw = original.guards.base.read_regular(root / metadata["path"], original.MAX_FILE)
            require(metadata == {"path": metadata["path"], "bytes": len(raw), "sha256": sha256(raw).hexdigest()}
                    and not any(x in raw for x in (b"#", b"$", b"\x00")), "Current ordinary geometry/map bytes must match the actual evidence inventory.")
            raw.decode("ascii"); total += len(raw)
            if Path(metadata["path"]).name in original.MESH_FILES:
                original.guards.base.foam_text(root / metadata["path"])
        maps = {Path(r["path"]).name: r for r in row["mappingFiles"]}
        cells = read_map(root, maps["sourceCellToDerivedCellIds"], source_counts["cellCount"])
        retained = read_map(root, maps["sourceFaceIds"], row["faceCount"])
        removed = read_map(root, maps["removedSourceFaceIds"], removed_count)
        require(np.all((cells >= 0) & (cells < row["cellCount"])), "All original cells must map into the exact derived range.")
        membership = np.bincount(cells, minlength=row["cellCount"])
        require(np.count_nonzero(membership == 2) == removed_count and np.count_nonzero(membership == 1) == row["cellCount"] - removed_count
                and np.all((membership == 1) | (membership == 2)), "The source membership map must contain only unchanged cells and exact disjoint two-cell unions.")
        require(np.array_equal(np.sort(np.r_[retained, removed]), np.arange(1, source_counts["faceCount"] + 1)),
                "Every source polygon must occur exactly once as retained or explicitly removed.")
        require(len(row["pairs"]) == removed_count and np.array_equal(removed, sorted(p["removedSourceFaceId"] for p in row["pairs"])), "Every removed source face must belong to an exact measured pair.")
        for pair in row["pairs"]:
            ids = pair["sourceCellIds"]
            require(len(ids) == 2 and 1 <= ids[0] < ids[1] <= len(cells) and cells[ids[0] - 1] == cells[ids[1] - 1] == pair["derivedCellIndex"]
                    and pair.get("closedOrientedManifoldUnionVerified") is True, "Actual cell membership must match every oriented pair proof.")
            volumes = pair.get("sourceCellVolumesAssumedM3", [])
            union = pair.get("unionVolumeAssumedM3")
            require(len(volumes) == 2 and all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in [*volumes, union]),
                    "Each bound pair needs its two actual positive source volumes and positive union volume.")
            difference = abs(union - math.fsum(volumes)) / max(union, math.fsum(volumes))
            require(difference <= VOLUME_TOLERANCE and math.isclose(pair.get("relativePairVolumeDifference", math.inf), difference, rel_tol=1e-12, abs_tol=1e-15),
                    "The actual two-cell volume identity cannot be replaced by an unchecked success label.")
        cursor = row["internalFaceCount"]; names = set(); zone_ids = set()
        for boundary in row["patches"]:
            lo, hi = boundary["sourceMinimumFaceId"], boundary["sourceMaximumFaceId"]
            require(boundary["startFace"] == cursor and boundary["nFaces"] == hi - lo + 1 and hi >= lo >= 1
                    and boundary["type"] in ("wall", "patch") and boundary["name"] not in names and boundary["sourceZoneId"] not in zone_ids
                    and np.array_equal(retained[cursor:cursor + boundary["nFaces"]], np.arange(lo, hi + 1))
                    and not np.isin(removed, np.arange(lo, hi + 1)).any(), "Every original boundary polygon must retain its exact complete patch range and tag.")
            cursor += boundary["nFaces"]; names.add(boundary["name"]); zone_ids.add(boundary["sourceZoneId"])
        require(names and cursor == row["faceCount"] and row["boundaryFaceCount"] == source_counts["faceCount"] - source_counts["internalFaceCount"]
                and set(row.get("mappingChecks", {})) == MAPPING_CHECK_KEYS and all(value is True for value in row["mappingChecks"].values()),
                "Complete actual boundary and mapping derivation proofs are required.")
        audit = row["derivedCellAudit"]
        verify_audit(audit, row["cellCount"], derived=True)
        original_volume = report["sourceAudits"][component]["totalCellVolumeAssumedM3"]
        difference = abs(audit["totalCellVolumeAssumedM3"] - original_volume) / original_volume
        require(audit.get("sourceDerivedVolumeAgreementVerified") is True and difference <= VOLUME_TOLERANCE
                and math.isclose(audit.get("relativeSourceDerivedVolumeDifference", math.inf), difference, rel_tol=1e-12, abs_tol=1e-15),
                "Every actual derived cell must have passed the complete closure and volume derivation checks.")
        normalized.append({"component": component, "caseDirectory": row["casePath"], "cellCount": row["cellCount"], "nodeCount": row["nodeCount"],
            "faceCount": row["faceCount"], "internalFaceCount": row["internalFaceCount"], "patchFaces": {p["name"]: p["nFaces"] for p in row["patches"]},
            "cellZone": original.COMPONENTS[component][1], "files": row["nativeFiles"]})
    require(report.get("combinedCounts") == {key: sum(r[key] for r in report["components"]) for key in ("cellCount", "nodeCount", "faceCount")}
            and report.get("studentCellCapMet") is (report["combinedCounts"]["cellCount"] < 1_000_000)
            and total == report.get("derivedMeshAndMapBytes") and total <= original.MAX_OUTPUT,
            "Actual aggregate mesh/map counts and bytes must match the separate pilot report.")
    raw = original.guards.base.read_regular(root / REPORT_NAME, original.inventory.MAX_REPORT_BYTES)
    return {"report": report, "reportFile": {"path": REPORT_NAME, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}, "components": normalized}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-directory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        import os
        require(os.name == "posix", "Actual pinned HDF5 derivation uses only the separate Ubuntu worker; no Windows policy change.")
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (original.MAX_MEMORY, original.MAX_MEMORY))
        report = agglomerate(args.source_directory, args.output_dir)
        if report["volumeMeshDerived"]:
            verify_output(args.output_dir)
        print(json.dumps({key: report.get(key) for key in ("status", "combinedCounts", "selection", "derivedMeshAndMapBytes", "originalMeshesImmutable",
            "volumeMeshDerived", "studentCellCapMet", "actualCfdRunVerified", "mainApplicationWrites", "action")}, allow_nan=False))
        return 0 if report["volumeMeshDerived"] else 2
    except (ValueError, OSError, KeyError, TypeError, ImportError, MemoryError, OverflowError) as error:
        print(json.dumps({"status": "GeometryPilotNotDerived", "action": str(error)[:500], "volumeMeshDerived": False,
                          "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
