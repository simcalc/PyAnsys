"""Derive at most 32 determinant-targeted original-hex unions, never run CFD.

This separate policy explicitly permits boundary-adjacent unions. It does not
change the protected three-ring pilot. Every original point and boundary
polygon is retained. The replicated Foundation 12 determinant is a candidate
criterion; native checkMesh, wall resolution and CFD remain independent gates.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agglomerate_frozen_rotor_native as pilot
import localize_frozen_rotor_quality as quality

original = pilot.original
SCHEMA = "untirta.frozen-rotor-determinant-repair.v1"
REPORT_NAME = "determinant-repair-report.json"
MAX_PAIRS = 32
MAX_CANDIDATE_CHECKS = 10_000
THRESHOLD = .001
PLANARITY_TOLERANCE = 1e-10
CONVEXITY_TOLERANCE = 1e-12
METRIC_RECOMPUTATION_TOLERANCE = 1e-12
FOUNDATION_PLANAR_COS_ANGLE = 1e-6
FORMULA_SOURCE_COMMIT = "86e126a7bc4dbfaed949bbc58c9f0fdd67acc059"
FORMULA_REFERENCES = ["https://raw.githubusercontent.com/OpenFOAM/OpenFOAM-12/" + FORMULA_SOURCE_COMMIT + "/" + path for path in (
    "src/meshCheck/checkGeometry.C", "src/meshCheck/primitiveMeshCheck/primitiveMeshCheck.C",
    "src/OpenFOAM/meshes/meshShapes/face/faceTemplates.C")]
POLICY = {
    "boundaryAdjacentPairingAllowed": True,
    "protectedBoundaryAdjacencyRings": 0,
    "everyOriginalBoundaryPolygonRetained": True,
    "everyOriginalCoordinateRetained": True,
    "originalHexPairsOnly": True,
    "maximumPairCount": MAX_PAIRS,
    "determinantThreshold": THRESHOLD,
    "thresholdComparison": "strict less than; equality passes",
    "selectionRule": "Ascending source determinant then component/source-cell ID; highest valid union determinant among each target's unused hex neighbours; tie by source face ID.",
    "planarityToleranceRelativeToUnionExtent": PLANARITY_TOLERANCE,
    "convexityToleranceRelativeToUnionExtent": CONVEXITY_TOLERANCE,
    "strictPositiveFacePyramidAndFanVolumesRequired": True,
    "foundation12PlanarCosAngle": FOUNDATION_PLANAR_COS_ANGLE,
    "foundation12OtherFaceCentreCosineMaximum": -FOUNDATION_PLANAR_COS_ANGLE,
    "nativeAcceptanceRequired": True,
}
FALSE_CLAIMS = ("nativeCoordinateUnitsVerified", "sourceNativeMeshQualified", "nativeFailedCellSetVerified",
    "meshQualityCheckedBySolver", "nativeCheckMeshExecuted", "nativeCaseGenerated", "qualifiedBaseline",
    "actualCfdRunVerified", "simulationExecuted", "fluentSessionConnected", "licenseVerified", "mainApplicationWrites")
DIAGNOSTIC_KEYS = {"actualCfdRunVerified", "claimBoundary", "combinedCellCount", "combinedFailedCellCount", "components", "createdAt",
    "determinantThreshold", "equationStages", "exampleDataCommit", "mainApplicationWrites", "meanReplicatedDeterminant",
    "minimumReplicatedDeterminant", "nativeFaceMaskAssumption", "nativeFailedCellSetVerified", "nativeGeometryQualityVerified",
    "officialReferences", "openFoamSourceCommit", "physicalDiagnosisValidated", "schemaVersion", "solverExecutedByThisTool",
    "sourceMeshModified", "sourceMeshesImmutable", "status", "thresholdModified", "nativeEquivalence", "nativeQualityReport"}
DIAGNOSTIC_COMPONENT_KEYS = {"actualCfdRunVerified", "boundaryAdjacencyDistanceSummary", "cellCount", "cellDeterminantSummary",
    "component", "coordinateDefinition", "distanceDefinition", "failedCellDirectBoundaryContactCounts", "failedCells", "failedSourceCellIds",
    "mainApplicationWrites", "nativeCoordinateUnitsVerified", "nativeFailedCellSetVerified", "nativeGeometryQualityVerified", "sourceCellZone", "sourceFile"}


class RepairIssue(ValueError):
    def __init__(self, message, evidence=None):
        super().__init__(message)
        self.evidence = evidence or {}


def require(condition, action):
    if not condition:
        raise RepairIssue(action)


def union_metric(model, face_areas, outside):
    """Exactly the internal-face mask, after deleting only the shared face."""
    import numpy as np
    included = [f for f in outside if model["c0"][f] > 0 and model["c1"][f] > 0]
    excluded = [f + 1 for f in outside if model["c0"][f] == 0 or model["c1"][f] == 0]
    vectors = face_areas[included]
    average = float(np.linalg.norm(vectors, axis=1).sum() / len(included)) if included else 0.
    tensor = np.zeros((3, 3), dtype="f8")
    if included:
        require(math.isfinite(average) and average > 0, "Included face areas must be finite and nonzero.")
        scaled = vectors / average
        for row in scaled:
            tensor += np.outer(row, row)
    determinant = float(abs(np.linalg.det(tensor)))
    require(math.isfinite(determinant), "The candidate determinant must be finite.")
    return {"includedSourceFaceIds": [f + 1 for f in included], "excludedPhysicalBoundarySourceFaceIds": excluded,
            "includedInternalFaceCount": len(included), "averageIncludedFaceAreaNativeUnitsSquared": average,
            "includedFaceNormalTensor": tensor.tolist(), "replicatedDeterminant": determinant}


def face_centre(xyz, area):
    """Pinned Foundation face::areaAndCentre projected-area weighting."""
    import numpy as np
    average = xyz.mean(axis=0); normal = area / np.linalg.norm(area)
    weight = 0.; weighted = np.zeros(3, dtype="f8")
    for j in range(len(xyz)):
        nxt = xyz[(j + 1) % len(xyz)]
        projected = float(np.dot(np.cross(nxt - xyz[j], average - xyz[j]), normal))
        weight += projected; weighted += projected * (xyz[j] + nxt + average)
    require(math.isfinite(weight) and weight > 1e-300, "The candidate native projected-area face centre is undefined.")
    result = weighted / weight / 3
    require(np.isfinite(result).all(), "Candidate native face centres must remain finite.")
    return result


def foundation_face_plane_check(centres, outward_areas):
    """Replicate unchanged checkConcaveCells; planar split faces also fail.

Pinned primitiveMeshCheck.C compares every other face centre against each
outward face plane. checkGeometry.C fixes planarCosAngle=1e-6. This strict
replication is separate from mathematical convexity and native execution.
"""
    import numpy as np
    centres = np.asarray(centres, dtype="f8"); vectors = np.asarray(outward_areas, dtype="f8")
    require(centres.shape == vectors.shape and centres.ndim == 2 and centres.shape[1] == 3
            and np.isfinite(centres).all() and np.isfinite(vectors).all(), "Finite actual native face centres/normals are required.")
    maximum = -math.inf
    for i, (centre, area) in enumerate(zip(centres, vectors)):
        magnitude = float(np.linalg.norm(area)); require(magnitude > 1e-300, "Native face normals must be nonzero.")
        normal = area / magnitude
        for j, other in enumerate(centres):
            if j == i:
                continue
            delta = other - centre
            cosine = float(np.dot(delta / max(float(np.linalg.norm(delta)), 1e-300), normal))
            maximum = max(maximum, cosine)
    if maximum > -FOUNDATION_PLANAR_COS_ANGLE:
        raise RepairIssue("Foundation12ConcaveOrPlanarSplitFaceUnion", {
            "maximumOtherFaceCentreOutwardCosine": maximum, "maximumAllowedCosine": -FOUNDATION_PLANAR_COS_ANGLE})
    return maximum


def geometry_checks(model, outside, member_ids, face_areas, expected_volume):
    """Conservative convex, planar union check without moving or welding points.

Planarity/half-space tolerances describe arithmetic checks, not node matching
or geometry healing. Topology uses exact original integer node IDs. Positive
face pyramids and vertex-mean fan tetrahedra are required independently.
"""
    import numpy as np
    ids = np.unique(np.concatenate([pilot.polygon(model, f) for f in outside]))
    vertices = model["coordinates"][ids]
    origin = vertices.mean(axis=0)
    extent = float(np.linalg.norm(vertices.max(axis=0) - vertices.min(axis=0)))
    require(math.isfinite(extent) and extent > 0, "A finite nonzero union extent is required.")
    max_planarity = max_outside = 0.
    triangle_volumes = []; pyramids = []; centroid_terms = []; faces = []
    for face in outside:
        sign = -1 if int(model["c0"][face]) in member_ids else 1
        xyz = model["coordinates"][pilot.polygon(model, face)]
        if sign < 0:
            xyz = xyz[::-1]
        fan_centre = xyz.mean(axis=0); area = face_areas[face] * sign
        centre = face_centre(xyz, area)
        normal = area / np.linalg.norm(area)
        nonplanarity = float(np.max(np.abs((xyz - centre) @ normal)) / extent)
        outside_distance = float(max(0., np.max((vertices - centre) @ normal)) / extent)
        max_planarity = max(max_planarity, nonplanarity)
        max_outside = max(max_outside, outside_distance)
        if nonplanarity > PLANARITY_TOLERANCE:
            raise RepairIssue("UnionFaceNonPlanarity", {"sourceFaceId": face + 1, "relativeNonPlanarity": nonplanarity, "maximumAllowed": PLANARITY_TOLERANCE})
        if outside_distance > CONVEXITY_TOLERANCE:
            raise RepairIssue("UnionNotConvexWithinDeclaredTolerance", {"sourceFaceId": face + 1, "relativeOutsideDistance": outside_distance, "maximumAllowed": CONVEXITY_TOLERANCE})
        face_pyramid = float(np.dot(area, centre - origin) / 3)
        require(math.isfinite(face_pyramid) and face_pyramid > 0, "UnionFacePyramidNotPositive")
        pyramids.append(face_pyramid); faces.append((centre, area))
        for j in range(len(xyz)):
            nxt = xyz[(j + 1) % len(xyz)]
            fan_area = np.cross(xyz[j] - fan_centre, nxt - fan_centre) / 2
            require(float(np.dot(fan_area, normal)) > 0, "UnionPolygonFanNotStrictlyConvex")
            volume = float(np.dot(fan_centre - origin, np.cross(xyz[j] - origin, nxt - origin)) / 6)
            require(math.isfinite(volume) and volume > 0, "UnionFanTetrahedronNotPositive")
            triangle_volumes.append(volume)
            centroid_terms.append(volume * ((fan_centre - origin) + (xyz[j] - origin) + (nxt - origin)) / 4)
    maximum_cosine = foundation_face_plane_check([row[0] for row in faces], [row[1] for row in faces])
    volume = math.fsum(triangle_volumes)
    relative = abs(volume - expected_volume) / max(volume, expected_volume)
    require(relative <= pilot.VOLUME_TOLERANCE, "UnionIndependentPyramidVolumeMismatch")
    centroid = origin + np.asarray([math.fsum(float(term[d]) for term in centroid_terms) for d in range(3)]) / volume
    centroid_pyramids = [float(np.dot(area, centre - centroid) / 3) for centre, area in faces]
    require(np.isfinite(centroid).all() and all(math.isfinite(v) and v > 0 for v in centroid_pyramids),
            "UnionVolumeCentroidFacePyramidNotPositive")
    return {"exactNativeNodeTopologyVerified": True, "convexityWithinDeclaredToleranceVerified": True,
            "facePlanarityWithinDeclaredToleranceVerified": True, "strictPositiveFacePyramidsVerified": True,
            "strictPositivePolygonFanTetrahedraVerified": True, "unionExtentNativeUnits": extent,
            "foundation12FacePlaneReplicationPassed": True, "foundation12PlanarCosAngle": FOUNDATION_PLANAR_COS_ANGLE,
            "maximumOtherFaceCentreOutwardCosine": maximum_cosine,
            "maximumRelativeFaceNonPlanarity": max_planarity, "maximumRelativeOutsideHalfSpaceDistance": max_outside,
            "minimumFacePyramidVolumeAssumedM3": min(pyramids), "minimumFanTetrahedronVolumeAssumedM3": min(triangle_volumes),
            "volumeCentroidNativeCoordinates": centroid.tolist(), "minimumCentroidFacePyramidVolumeAssumedM3": min(centroid_pyramids),
            "independentPyramidVolumeAssumedM3": volume, "relativeIndependentPyramidVolumeDifference": relative,
            "geometricSelfIntersectionChecked": False, "nativeMeshQualityVerified": False}


def admissible_metric(before, after):
    require(len(before) == 2 and all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 for v in before) and min(before) < THRESHOLD,
            "Every pair must target an actual below-threshold original source cell.")
    require(isinstance(after, (float, int)) and not isinstance(after, bool) and math.isfinite(after) and after > max(v for v in before if v < THRESHOLD),
            "The union must strictly improve every failing source member's determinant.")
    require(not any(v >= THRESHOLD for v in before) or after >= THRESHOLD,
            "A healthy source partner cannot become a below-threshold union.")


def candidate(model, face, a, b, incidence, offsets, areas, metric):
    require(model["cellTypes"][a] == model["cellTypes"][b] == 4, "OnlyOriginalHexPairs")
    item = pilot.check_pair(model, face, a, b, incidence, offsets)
    outside = sorted(set(map(int, incidence[offsets[a]:offsets[a + 1]])) |
                     set(map(int, incidence[offsets[b]:offsets[b + 1]])))
    outside.remove(face)
    result = union_metric(model, areas, outside)
    before = [float(metric["determinants"][cell]) for cell in (a, b)]
    admissible_metric(before, result["replicatedDeterminant"])
    checks = geometry_checks(model, outside, {a + 1, b + 1}, areas, item["unionVolumeAssumedM3"])
    boundary_ids = set(result["excludedPhysicalBoundarySourceFaceIds"])
    patches = [zone["name"] for zone in model["faceZones"] if zone["zoneType"] != 2
               and any(zone["minimumId"] <= f <= zone["maximumId"] for f in boundary_ids)]
    return {**item, "sourceDeterminants": before, "selectionUnionMetric": result, "geometryChecks": checks,
            "boundaryAdjacent": bool(boundary_ids), "retainedBoundaryPatches": patches}


def select_pairs(models, areas, metrics, *, maximum_pairs=MAX_PAIRS):
    """Bounded greedy matching; highest valid union metric for each bad target."""
    import numpy as np
    require(isinstance(maximum_pairs, int) and not isinstance(maximum_pairs, bool) and 1 <= maximum_pairs <= MAX_PAIRS,
            "Use an explicit pair cap between one and 32.")
    targets = []
    for name, model in models.items():
        failed = np.flatnonzero(metrics[name]["determinants"] < THRESHOLD)
        require(len(failed) <= quality.MAX_FAILED, "The complete source failed-cell inventory exceeds its explicit bound.")
        targets.extend((float(metrics[name]["determinants"][cell]), name, int(cell)) for cell in failed if model["cellTypes"][cell] == 4)
    targets.sort()
    csr = {name: pilot.cell_faces(model) for name, model in models.items()}
    used = {name: set() for name in models}; selected = {name: [] for name in models}
    checked = visited = 0; rejected = Counter(); truncated = False; samples = []
    for _, name, target in targets:
        if target in used[name]:
            continue
        model = models[name]; incidence, offsets = csr[name]; options = []; visited += 1
        for face in incidence[offsets[target]:offsets[target + 1]]:
            face = int(face); members = sorted((int(model["c0"][face]) - 1, int(model["c1"][face]) - 1))
            if members[0] < 0 or any(c in used[name] for c in members) or model["faceWidths"][face] != 4:
                continue
            if checked >= MAX_CANDIDATE_CHECKS:
                truncated = True; break
            checked += 1
            try:
                item = candidate(model, face, *members, incidence, offsets, areas[name], metrics[name])
            except (pilot.AgglomerationIssue, RepairIssue) as error:
                rejected[str(error)] += 1
                if len(samples) < 8:
                    samples.append({"component": name, "sourceCellIds": [c + 1 for c in members], "sharedSourceFaceId": face + 1,
                        "sourceDeterminants": [float(metrics[name]["determinants"][c]) for c in members],
                        "reason": str(error)[:160], "geometricEvidence": getattr(error, "evidence", {})})
                continue
            options.append(item)
        # A partial neighbour scan cannot establish the highest valid candidate.
        if truncated:
            break
        if options:
            item = min(options, key=lambda p: (-p["selectionUnionMetric"]["replicatedDeterminant"], p["removedSourceFaceId"]))
            selected[name].append(item); used[name].update(v - 1 for v in item["sourceCellIds"])
            if sum(map(len, selected.values())) == maximum_pairs:
                break
    count = sum(map(len, selected.values()))
    return selected, {"maximumPairCount": maximum_pairs, "selectedPairCount": count,
        "sourceFailedHexTargetCount": len(targets), "visitedTargetCount": visited, "checkedCandidateCount": checked,
        "maximumCandidateChecks": MAX_CANDIDATE_CHECKS, "candidateScanCapped": truncated,
        "targetInventoryExhausted": not truncated and count < maximum_pairs,
        "maximumMatchingComputed": False, "rejectedCandidateReasonCounts": dict(rejected), "rejectedCandidateSamples": samples,
        "selectionRule": POLICY["selectionRule"]}


def derive_model(model, pairs, areas, metric):
    """Separate boundary-permitting map; the protected pilot is never called."""
    import numpy as np
    require(len(pairs) <= MAX_PAIRS, "The separate quality prototype cannot exceed 32 pairs.")
    masters = np.arange(model["cellCount"], dtype="u4"); removed = np.zeros(model["faceCount"], dtype=bool)
    used = set(); incidence, offsets = pilot.cell_faces(model)
    for item in pairs:
        a, b = [v - 1 for v in item["sourceCellIds"]]; face = item["removedSourceFaceId"] - 1
        require(0 <= a < b < model["cellCount"] and 0 <= face < model["faceCount"] and not ({a, b} & used),
                "Every selected original pair must have disjoint in-range membership.")
        require({int(model["c0"][face]), int(model["c1"][face])} == {a + 1, b + 1}, "Only an actual shared internal source face may be removed.")
        require(candidate(model, face, a, b, incidence, offsets, areas, metric) == item, "Pair evidence must match current source coordinates, topology and metrics.")
        used.update((a, b)); masters[b] = a; removed[face] = True
    kept = np.flatnonzero(~removed).astype("u4"); unique = np.unique(masters)
    cell_map = np.searchsorted(unique, masters).astype("u4"); widths = model["faceWidths"][kept]
    derived = {**model, "cellCount": len(unique), "faceCount": len(kept), "faceWidths": widths,
        "faceOffsets": np.r_[0, np.cumsum(widths, dtype="i8")],
        "faceNodes": model["faceNodes"][np.repeat(~removed, model["faceWidths"])], "cellTypes": model["cellTypes"][unique].copy()}
    for key in ("c0", "c1"):
        source_ids = model[key][kept]; values = np.zeros(len(kept), dtype="u4"); positive = source_ids > 0
        values[positive] = cell_map[source_ids[positive] - 1] + 1; derived[key] = values
    require(np.all(derived["c0"] != derived["c1"]), "No unselected source polygon may become a hidden self-neighbour face.")
    for item in pairs:
        derived["cellTypes"][cell_map[item["sourceCellIds"][0] - 1]] = 7
    zones = []; cursor = 0
    for zone in model["faceZones"]:
        low, high = zone["minimumId"] - 1, zone["maximumId"]
        count = int(np.count_nonzero(~removed[low:high]))
        require(count > 0, "No complete original face zone may be dropped.")
        if zone["zoneType"] != 2:
            require(not removed[low:high].any(), "Every exact original boundary polygon must remain present.")
        zones.append({**zone, "minimumId": cursor + 1, "maximumId": cursor + count}); cursor += count
    derived["faceZones"] = zones
    present = np.zeros(model["nodeCount"], dtype=bool); present[derived["faceNodes"]] = True
    require(cursor == len(kept) and present.all() and np.array_equal(model["coordinates"], derived["coordinates"]),
            "All original coordinates, points, source tags and retained polygons must survive without welding.")
    return derived, kept + 1, cell_map, np.flatnonzero(removed).astype("u4") + 1


def impact(source, derived, cell_map, pairs):
    import numpy as np
    before = source["determinants"]; after = derived["determinants"]; projected = after[cell_map]
    merged = np.zeros(len(before), dtype=bool)
    for item in pairs:
        ids = np.asarray(item["sourceCellIds"]) - 1; merged[ids] = True
        actual = float(after[cell_map[ids[0]]]); admissible_metric(item["sourceDeterminants"], actual)
        expected = item["selectionUnionMetric"]["replicatedDeterminant"]
        require(math.isclose(actual, expected, rel_tol=METRIC_RECOMPUTATION_TOLERANCE, abs_tol=1e-15),
                "The complete derived metric must agree with the selected internal-face union formula.")
        item["derivedCellIndex"] = int(cell_map[ids[0]])
        item["derivedReplicatedDeterminant"] = actual
    require(np.array_equal(before[~merged], projected[~merged]), "Unmerged source cells' replicated metric must remain unchanged.")
    bad_before, bad_projected = before < THRESHOLD, projected < THRESHOLD
    require(not np.any((~bad_before) & bad_projected), "No originally healthy cell may become determinant-deficient.")
    return {"determinantThreshold": THRESHOLD, "sourceCellCount": len(before), "derivedCellCount": len(after),
        "sourceFailedCellCount": int(bad_before.sum()), "derivedFailedCellCount": int(np.count_nonzero(after < THRESHOLD)),
        "sourceFailedCellsMappedToPassingDerivedCellCount": int(np.count_nonzero(bad_before & ~bad_projected)),
        "sourceFailedCellsMappedToStillFailingDerivedCellCount": int(np.count_nonzero(bad_before & bad_projected)),
        "healthySourceCellsMappedToFailingDerivedCellCount": 0, "unchangedSourceCellMetricEqualityVerified": True,
        "sourceMinimumReplicatedDeterminant": float(before.min()), "derivedMinimumReplicatedDeterminant": float(after.min()),
        "nativeFailedCellSetVerified": False, "nativeGeometryQualityVerified": False}


def read_source_diagnostic(path):
    """Require the measured pinned-source diagnostic, not user-invented targets."""
    target = Path(path).absolute()
    raw, metadata = quality.plain_file(target)
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate source diagnostic JSON keys are prohibited.")
            result[key] = value
        return result
    def finite_float(value):
        result = float(value)
        require(math.isfinite(result), "Nonfinite source diagnostic JSON is prohibited.")
        return result
    packet = json.loads(raw.decode("ascii"), object_pairs_hook=unique_keys, parse_float=finite_float,
        parse_constant=lambda _: (_ for _ in ()).throw(RepairIssue("Nonfinite source diagnostic JSON is prohibited.")))
    require(isinstance(packet, dict) and set(packet) <= DIAGNOSTIC_KEYS, "Unsupported source diagnostic fields must not enter the derived artifact.")
    components = packet.get("components")
    require(isinstance(components, list) and len(components) == 2 and all(isinstance(row, dict) for row in components),
            "The measured diagnostic must contain exactly two ordinary component records.")
    require(packet.get("schemaVersion") == quality.SCHEMA and packet.get("exampleDataCommit") == original.inventory.PIN
            and packet.get("determinantThreshold") == THRESHOLD and packet.get("sourceMeshesImmutable") is True
            and packet.get("sourceMeshModified") is False and packet.get("thresholdModified") is False
            and packet.get("solverExecutedByThisTool") is False and packet.get("actualCfdRunVerified") is False
            and packet.get("mainApplicationWrites") is False
            and [r.get("component") for r in components] == list(original.COMPONENTS),
            "Use the actual source-pinned, immutable, unchanged-threshold localization diagnostic for both components.")
    for row in packet["components"]:
        require(isinstance(row, dict) and set(row) <= DIAGNOSTIC_COMPONENT_KEYS, "Unsupported source component fields must not enter the derived artifact.")
        ids = row.get("failedSourceCellIds"); count = row.get("cellCount")
        require(isinstance(count, int) and not isinstance(count, bool) and 0 < count <= original.MAX_CELLS
                and isinstance(ids, list) and len(ids) <= quality.MAX_FAILED
                and all(isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= count for v in ids)
                and ids == sorted(set(ids)), "Every measured source target ID must be sorted, unique and within the bounded original cell range.")
    return packet, raw, {"path": "source-quality-localization.json", "bytes": metadata["bytes"], "sha256": metadata["sha256"]}


def verify_source_diagnostic(packet, models, metrics):
    import numpy as np
    for row in packet["components"]:
        name = row["component"]; model = models[name]
        failed = (np.flatnonzero(metrics[name]["determinants"] < THRESHOLD) + 1).tolist()
        require(row.get("sourceFile") == model["sourceFile"] and row.get("cellCount") == model["cellCount"]
                and row.get("failedSourceCellIds") == failed and row.get("cellDeterminantSummary", {}).get("failedCellCount") == len(failed)
                and row.get("cellDeterminantSummary", {}).get("threshold") == THRESHOLD,
                "Every measured source failed-cell ID, source SHA and count must agree with the complete current-source metric replication.")
    require(packet.get("combinedFailedCellCount") == sum(len(r["failedSourceCellIds"]) for r in packet["components"]),
            "The measured source diagnostic cannot hide any component failures.")


def repair(source_directory, output_directory, source_quality_json):
    inputs = original.guards.inside_runner(source_directory); destination = Path(output_directory).absolute()
    original.inventory.output_path(destination / REPORT_NAME)
    require(not destination.exists() and not destination.is_symlink() and not destination.resolve().is_relative_to(inputs.resolve())
            and not inputs.resolve().is_relative_to(destination.resolve()), "Use a fresh independent output directory; source meshes are immutable.")
    sources = [original.inventory.verify_file(inputs / name, name) for name in original.inventory.MESH_NAMES]
    original.guards.inside_runner(Path(source_quality_json).absolute().parent)
    diagnostic, diagnostic_raw, diagnostic_metadata = read_source_diagnostic(source_quality_json)
    destination.mkdir(parents=True)
    report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(), "exampleDataCommit": original.inventory.PIN,
        "formulaSourceCommit": FORMULA_SOURCE_COMMIT, "officialFormulaReferences": FORMULA_REFERENCES,
        "status": "DeterminantPrototypeNotDerived", "sources": sources, "sourceAudits": {}, "components": [],
        "authoredDerivationPolicy": dict(POLICY), "policyAuthoredBeforeDerivation": True,
        "coordinateUnits": "UNVERIFIED", "classroomMetresPerSourceCoordinateUnit": 1,
        "volumeMeshDerived": False, "originalMeshesImmutable": False, **{key: False for key in FALSE_CLAIMS},
        "allSourceDeterminantFailuresRepaired": False, "studentCellCapMet": False,
        "limits": {"maximumPairCount": MAX_PAIRS, "maximumCandidateChecks": MAX_CANDIDATE_CHECKS,
            "maximumFileBytes": original.MAX_FILE, "maximumDerivedBytes": original.MAX_OUTPUT,
            "maximumWorkerAddressSpaceBytes": original.MAX_MEMORY},
        "claimBoundary": "At most 32 explicitly mapped original-hex unions. Boundary-adjacent pairing is deliberate and may change wall-normal resolution/yPlus. All source coordinates and boundary polygons remain fixed. Replicated determinant improvement is not native checkMesh, wall-resolution, self-intersection, solver, NPSH3 or numerical-convergence acceptance."}
    budget = [0]
    # Persist the policy before source arrays, candidate selection or mesh writes.
    policy_raw = original.encoded(report["authoredDerivationPolicy"])
    original.guards.base.write_exclusive(destination / "derivation-policy.json", policy_raw)
    report["derivationPolicyFile"] = {"path": "derivation-policy.json", "bytes": len(policy_raw), "sha256": sha256(policy_raw).hexdigest()}
    original.guards.base.write_exclusive(destination / diagnostic_metadata["path"], diagnostic_raw)
    report["sourceQualityDiagnosticFile"] = diagnostic_metadata
    try:
        require(quality.THRESHOLD == THRESHOLD, "The exact default Foundation 12 determinant threshold cannot change.")
        models = {name: original.read_mesh(inputs / original.COMPONENTS[name][0], name) for name in original.COMPONENTS}
        areas = {}; metrics = {}
        for name, model in models.items():
            report["sourceAudits"][name] = original.audit_cells(model)
            areas[name] = quality.polygon_areas(model)
            metrics[name] = quality.cell_determinants(areas[name], model["c0"], model["c1"], model["cellCount"])
        verify_source_diagnostic(diagnostic, models, metrics)
        report["measuredSourceDiagnosticReplicatedFailedIdsMatch"] = True
        report["sourceDeterminantSummary"] = {name: {"sourceCellCount": model["cellCount"],
            "failedCellCount": int((metrics[name]["determinants"] < THRESHOLD).sum()),
            "minimumReplicatedDeterminant": float(metrics[name]["determinants"].min()), "threshold": THRESHOLD}
            for name, model in models.items()}
        report["sourceReplicatedFailedCellCount"] = sum(r["failedCellCount"] for r in report["sourceDeterminantSummary"].values())
        report["equationStages"] = [{"input": "Actual source internal-face vectors; uncoupled physical boundaries excluded.",
            "equation": "A_bar=sum(|S_f|)/n_internal; T=sum((S_f/A_bar) outer (S_f/A_bar)); D=abs(det(T))",
            "substitution": "All actual source cells were independently recomputed and their below-threshold IDs matched the immutable measured localization packet.",
            "result": {"sourceFailedCellCount": report["sourceReplicatedFailedCellCount"], "threshold": THRESHOLD, "units": "dimensionless"}}]
        selected, report["selection"] = select_pairs(models, areas, metrics)
        if not report["selection"]["selectedPairCount"]:
            report.update(status="DeterminantPrototypeEligiblePairsRequired",
                action="No bounded determinant-improving original-hex pair passed the topology, geometry and healthy-partner gates. No derived mesh was written.")
        else:
            for name, model in models.items():
                pairs = selected[name]
                derived, face_map, cell_map, removed = derive_model(model, pairs, areas[name], metrics[name])
                checks = original.audit_cells(derived)
                checks["allDerivedCellsChecked"] = checks.pop("allOriginalCellsChecked")
                checks["derivedCellCountChecked"] = checks.pop("originalCellCountChecked")
                before_volume = report["sourceAudits"][name]["totalCellVolumeAssumedM3"]
                difference = abs(checks["totalCellVolumeAssumedM3"] - before_volume) / before_volume
                require(difference <= pilot.VOLUME_TOLERANCE, "The full derived component volume must equal its full source volume.")
                checks.update(relativeSourceDerivedVolumeDifference=difference, sourceDerivedVolumeAgreementVerified=True)
                derived_metric = quality.cell_determinants(areas[name][face_map - 1], derived["c0"], derived["c1"], derived["cellCount"])
                quality_impact = impact(metrics[name], derived_metric, cell_map, pairs)
                row = pilot.write_component(destination, derived, model, checks, face_map, cell_map, removed, budget)
                row.update(pairs=pairs, determinantImpact=quality_impact)
                report["components"].append(row)
                del derived, face_map, cell_map, removed, derived_metric
            report["combinedCounts"] = {key: sum(row[key] for row in report["components"]) for key in ("cellCount", "nodeCount", "faceCount")}
            report["sourceCombinedCounts"] = {key: sum(model[key] for model in models.values()) for key in ("cellCount", "nodeCount", "faceCount")}
            impact_keys = ("sourceFailedCellCount", "derivedFailedCellCount", "sourceFailedCellsMappedToPassingDerivedCellCount",
                "sourceFailedCellsMappedToStillFailingDerivedCellCount", "healthySourceCellsMappedToFailingDerivedCellCount")
            report["combinedDeterminantImpact"] = {key: sum(row["determinantImpact"][key] for row in report["components"]) for key in impact_keys}
            count = report["selection"]["selectedPairCount"]
            require(report["combinedCounts"]["cellCount"] == report["sourceCombinedCounts"]["cellCount"] - count,
                    "Only one cell per actually selected disjoint pair may disappear.")
            report["equationStages"] = [
                {"input": "Actual internal face vectors; uncoupled physical boundary faces excluded; selected shared face removed.",
                 "equation": "A_bar = sum_f |S_f| / n_internal; T = sum_f (S_f/A_bar) outer (S_f/A_bar); D = abs(det(T))",
                 "substitution": "Each pair records actual included source-face IDs, A_bar, 3x3 tensor and source/union determinants.",
                 "result": {"threshold": THRESHOLD, "comparison": "D < 0.001 fails; no threshold tolerance", "units": "dimensionless"}},
                {"input": {"sourceCellCount": report["sourceCombinedCounts"]["cellCount"], "acceptedDisjointPairCount": count},
                 "equation": "N_derived = N_source - N_pairs", "substitution": str(report["sourceCombinedCounts"]["cellCount"]) + " - " + str(count),
                 "result": {"derivedCellCount": report["combinedCounts"]["cellCount"], "units": "cells"}},
                {"input": "All retained outward source polygons, float64 points and disjoint original cell members.",
                 "equation": "V_union = V_a + V_b = sum_positive_face_fan_tetrahedra(V_t)",
                 "substitution": "Actual positive source/union/pyramid volumes and relative differences are recorded per pair and full component.",
                 "result": {"relativeVolumeTolerance": pilot.VOLUME_TOLERANCE, "units": "assumed m^3"}}]
            report.update(status="DeterminantPrototypeDerivedNativeAcceptanceRequired", volumeMeshDerived=True,
                studentCellCapMet=report["combinedCounts"]["cellCount"] < 1_000_000)
    except (ValueError, OSError, KeyError, TypeError, IndexError, ImportError, MemoryError, OverflowError) as error:
        report.update(status="DeterminantPrototypeNotDerived", volumeMeshDerived=False, action=str(error)[:500], partialOutputsRetained=True)
    try:
        after = [original.inventory.verify_file(inputs / name, name) for name in original.inventory.MESH_NAMES]
        report["sourcesAfterDerivation"] = after; report["originalMeshesImmutable"] = after == sources
        require(after == sources, "Pinned original source meshes changed during the independent derivation.")
    except (ValueError, OSError) as error:
        report.update(status="DeterminantPrototypeNotDerived", volumeMeshDerived=False, originalMeshesImmutable=False, action=str(error)[:500])
    report["derivedMeshAndMapBytes"] = budget[0]
    raw = original.inventory.bounded_json(report)
    require(budget[0] + len(policy_raw) + len(diagnostic_raw) + len(raw) <= original.MAX_OUTPUT, "The complete bounded output exceeds its declared byte cap.")
    original.guards.base.write_exclusive(destination / REPORT_NAME, raw)
    return report


def verify_output(output_directory):
    """Recheck actual inventory/maps and separate policy; never qualify CFD."""
    import numpy as np
    root = original.guards.base.regular_directory(output_directory)
    report = original.guards.json_file(root / REPORT_NAME)
    require(report.get("schemaVersion") == SCHEMA and report.get("exampleDataCommit") == original.inventory.PIN
            and report.get("formulaSourceCommit") == FORMULA_SOURCE_COMMIT and report.get("officialFormulaReferences") == FORMULA_REFERENCES
            and report.get("status") == "DeterminantPrototypeDerivedNativeAcceptanceRequired"
            and report.get("volumeMeshDerived") is True and report.get("originalMeshesImmutable") is True
            and report.get("sourcesAfterDerivation") == report.get("sources") and report.get("authoredDerivationPolicy") == POLICY
            and report.get("policyAuthoredBeforeDerivation") is True and report.get("measuredSourceDiagnosticReplicatedFailedIdsMatch") is True,
            "The exact separate, source-pinned boundary-permitting derivation policy and measured targets are required.")
    require(report.get("coordinateUnits") == "UNVERIFIED" and report.get("classroomMetresPerSourceCoordinateUnit") == 1
            and all(report.get(key) is False for key in FALSE_CLAIMS) and report.get("allSourceDeterminantFailuresRepaired") is False,
            "Replicated determinant improvement cannot promote native mesh/solver acceptance or claim every source failure repaired.")
    policy_raw = original.guards.base.read_regular(root / "derivation-policy.json", original.inventory.MAX_REPORT_BYTES)
    require(policy_raw == original.encoded(POLICY) and report.get("derivationPolicyFile") == {
        "path": "derivation-policy.json", "bytes": len(policy_raw), "sha256": sha256(policy_raw).hexdigest()}, "The actual policy bytes must remain immutable.")
    diagnostic, diagnostic_raw, diagnostic_metadata = read_source_diagnostic(root / "source-quality-localization.json")
    require(diagnostic_metadata == report.get("sourceQualityDiagnosticFile"), "The actual measured source-target diagnostic bytes must remain immutable.")
    selection = report["selection"]; count = selection.get("selectedPairCount")
    require(isinstance(count, int) and not isinstance(count, bool) and 1 <= count <= MAX_PAIRS
            and selection.get("maximumPairCount") == MAX_PAIRS and selection.get("maximumCandidateChecks") == MAX_CANDIDATE_CHECKS
            and isinstance(selection.get("checkedCandidateCount"), int) and count <= selection["checkedCandidateCount"] <= MAX_CANDIDATE_CHECKS
            and selection.get("maximumMatchingComputed") is False and selection.get("selectionRule") == POLICY["selectionRule"],
            "The independent candidate scan and pair cap cannot change.")
    require([r.get("name") for r in report["components"]] == list(original.COMPONENTS), "Both complete original component derivations are required.")
    normalized = []; total = 0; all_pairs = 0
    for row in report["components"]:
        name = row["name"]; source_counts = original.SOURCE_COUNTS[name]; pairs = row["pairs"]; removed_count = len(pairs); all_pairs += removed_count
        source_name = original.COMPONENTS[name][0]; size, digest = original.inventory.FILES[source_name]
        pin = {"name": source_name, "bytes": size, "sha256": digest, "sourceUrl": original.inventory.mesh_url(source_name)}
        require(row.get("sourceFile") == pin and pin in report["sources"] and row.get("casePath") == name + "-case"
                and row.get("sourceCounts") == {k: source_counts[k] for k in ("cellCount", "nodeCount", "faceCount")}
                and row.get("cellCount") == source_counts["cellCount"] - removed_count and row.get("nodeCount") == source_counts["nodeCount"]
                and row.get("faceCount") == source_counts["faceCount"] - removed_count
                and row.get("internalFaceCount") == source_counts["internalFaceCount"] - removed_count, "Only exact original pair membership may change component counts.")
        pilot.verify_audit(report["sourceAudits"][name], source_counts["cellCount"])
        measured = next(r for r in diagnostic["components"] if r["component"] == name)
        require(measured.get("sourceFile") == pin and measured.get("cellCount") == source_counts["cellCount"]
                and measured.get("cellDeterminantSummary", {}).get("failedCellCount") == len(measured["failedSourceCellIds"]) == row["determinantImpact"]["sourceFailedCellCount"]
                and report.get("sourceDeterminantSummary", {}).get(name, {}).get("failedCellCount") == len(measured["failedSourceCellIds"]),
                "The derived source failure count must remain bound to the measured pinned diagnostic.")
        pilot.verify_audit(row["derivedCellAudit"], row["cellCount"], derived=True)
        audit = row["derivedCellAudit"]; source_volume = report["sourceAudits"][name]["totalCellVolumeAssumedM3"]
        relative = abs(audit["totalCellVolumeAssumedM3"] - source_volume) / source_volume
        require(audit.get("sourceDerivedVolumeAgreementVerified") is True and relative <= pilot.VOLUME_TOLERANCE
                and math.isclose(audit.get("relativeSourceDerivedVolumeDifference", math.inf), relative, rel_tol=1e-12, abs_tol=1e-15),
                "The complete source/derived volume agreement remains required.")
        rows = row["nativeFiles"] + row["mappingFiles"]
        expected = {name + "-case/constant/polyMesh/" + role for role in original.MESH_FILES} | {name + "-case/" + role for role in pilot.MAP_NAMES}
        require(len(rows) == len(expected) and {r.get("path") for r in rows} == expected, "The exact six native mesh and three source-map roles are required.")
        for metadata in rows:
            original.guards.relative_path(metadata["path"]); target = root / metadata["path"]
            original.guards.base.regular_directory(target.parent); raw = original.guards.base.read_regular(target, original.MAX_FILE)
            require(metadata == {"path": metadata["path"], "bytes": len(raw), "sha256": sha256(raw).hexdigest()}
                    and not any(v in raw for v in (b"#", b"$", b"\0")), "Current ordinary mesh/map bytes must match their exact inventory.")
            raw.decode("ascii"); total += len(raw)
            if target.name in original.MESH_FILES:
                original.guards.base.foam_text(target)
        maps = {Path(r["path"]).name: r for r in row["mappingFiles"]}
        cells = pilot.read_map(root, maps["sourceCellToDerivedCellIds"], source_counts["cellCount"])
        retained = pilot.read_map(root, maps["sourceFaceIds"], row["faceCount"])
        removed = pilot.read_map(root, maps["removedSourceFaceIds"], removed_count)
        require(np.all((cells >= 0) & (cells < row["cellCount"])), "Every original source cell must map into the derived range.")
        memberships = np.bincount(cells, minlength=row["cellCount"])
        require(np.count_nonzero(memberships == 2) == removed_count and np.count_nonzero(memberships == 1) == row["cellCount"] - removed_count
                and np.all((memberships == 1) | (memberships == 2)), "Only exact disjoint two-cell unions may change original membership.")
        require(np.array_equal(np.sort(np.r_[retained, removed]), np.arange(1, source_counts["faceCount"] + 1))
                and np.array_equal(removed, sorted(p["removedSourceFaceId"] for p in pairs)), "Every original face must be retained or explicitly bound to one selected internal union.")
        for item in pairs:
            ids = item["sourceCellIds"]
            require(len(ids) == 2 and 1 <= ids[0] < ids[1] <= len(cells) and cells[ids[0] - 1] == cells[ids[1] - 1] == item["derivedCellIndex"]
                    and item.get("closedOrientedManifoldUnionVerified") is True, "The actual source-cell map must agree with every manifold pair.")
            admissible_metric(item["sourceDeterminants"], item["derivedReplicatedDeterminant"])
            require(any(value in measured["failedSourceCellIds"] for value in ids), "Every actual pair must target a measured source failure ID.")
            verify_pair_evidence(item)
        cursor = row["internalFaceCount"]; names = set(); zone_ids = set()
        for boundary in row["patches"]:
            low, high = boundary["sourceMinimumFaceId"], boundary["sourceMaximumFaceId"]
            require(boundary["startFace"] == cursor and boundary["nFaces"] == high - low + 1 and high >= low >= 1
                    and boundary["type"] in ("wall", "patch") and boundary["name"] not in names and boundary["sourceZoneId"] not in zone_ids
                    and np.array_equal(retained[cursor:cursor + boundary["nFaces"]], np.arange(low, high + 1))
                    and not np.isin(removed, np.arange(low, high + 1)).any(), "Every boundary source polygon and tag must be retained exactly.")
            cursor += boundary["nFaces"]; names.add(boundary["name"]); zone_ids.add(boundary["sourceZoneId"])
        require(names and cursor == row["faceCount"] and row["boundaryFaceCount"] == source_counts["faceCount"] - source_counts["internalFaceCount"]
                and set(row.get("mappingChecks", {})) == pilot.MAPPING_CHECK_KEYS and all(row["mappingChecks"].values()), "Every original boundary and point mapping must remain complete.")
        verify_impact(row["determinantImpact"], source_counts["cellCount"], row["cellCount"], pairs)
        normalized.append({"component": name, "caseDirectory": row["casePath"], "cellCount": row["cellCount"], "nodeCount": row["nodeCount"],
            "faceCount": row["faceCount"], "internalFaceCount": row["internalFaceCount"], "patchFaces": {p["name"]: p["nFaces"] for p in row["patches"]},
            "cellZone": original.COMPONENTS[name][1], "files": row["nativeFiles"]})
    require(all_pairs == count and total == report.get("derivedMeshAndMapBytes") and total <= original.MAX_OUTPUT
            and report.get("combinedCounts") == {k: sum(r[k] for r in report["components"]) for k in ("cellCount", "nodeCount", "faceCount")}
            and report.get("sourceCombinedCounts") == {k: sum(original.SOURCE_COUNTS[n][k] for n in original.COMPONENTS) for k in ("cellCount", "nodeCount", "faceCount")}
            and report.get("studentCellCapMet") is (report["combinedCounts"]["cellCount"] < 1_000_000), "The actual full aggregate mesh/count/byte inventory must agree.")
    require(report.get("sourceReplicatedFailedCellCount") == diagnostic.get("combinedFailedCellCount") == sum(r["determinantImpact"]["sourceFailedCellCount"] for r in report["components"]),
            "Every source failure must remain represented in the complete aggregate counts.")
    require(report.get("combinedDeterminantImpact") == {k: sum(r["determinantImpact"][k] for r in report["components"]) for k in
        ("sourceFailedCellCount", "derivedFailedCellCount", "sourceFailedCellsMappedToPassingDerivedCellCount",
         "sourceFailedCellsMappedToStillFailingDerivedCellCount", "healthySourceCellsMappedToFailingDerivedCellCount")}, "The complete source/derived failure accounting cannot hide remaining failures.")
    raw = original.guards.base.read_regular(root / REPORT_NAME, original.inventory.MAX_REPORT_BYTES)
    require(total + len(raw) + len(policy_raw) + len(diagnostic_raw) <= original.MAX_OUTPUT, "The exact complete output byte cap must remain satisfied.")
    return {"report": report, "reportFile": {"path": REPORT_NAME, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}, "components": normalized}


def verify_pair_evidence(item):
    import numpy as np
    volumes = item["sourceCellVolumesAssumedM3"]; union = item["unionVolumeAssumedM3"]
    require(len(volumes) == 2 and all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in [*volumes, union]), "Positive measured source/union volumes are required.")
    difference = abs(union - math.fsum(volumes)) / max(union, math.fsum(volumes))
    require(difference <= pilot.VOLUME_TOLERANCE and math.isclose(item["relativePairVolumeDifference"], difference, rel_tol=1e-12, abs_tol=1e-15), "The exact per-pair source volume identity must agree.")
    metric = item["selectionUnionMetric"]; tensor = np.asarray(metric["includedFaceNormalTensor"], dtype="f8")
    require(tensor.shape == (3, 3) and np.isfinite(tensor).all() and np.array_equal(tensor, tensor.T)
            and len(metric["includedSourceFaceIds"]) == metric["includedInternalFaceCount"] and metric["includedInternalFaceCount"] > 0
            and not set(metric["includedSourceFaceIds"]) & set(metric["excludedPhysicalBoundarySourceFaceIds"])
            and item["removedSourceFaceId"] not in metric["includedSourceFaceIds"] + metric["excludedPhysicalBoundarySourceFaceIds"]
            and len(metric["includedSourceFaceIds"]) + len(metric["excludedPhysicalBoundarySourceFaceIds"]) == 10,
            "Each exact union must keep its internal/physical-boundary mask and actual 3x3 tensor.")
    determinant = float(abs(np.linalg.det(tensor)))
    require(determinant == metric["replicatedDeterminant"] and math.isclose(determinant, item["derivedReplicatedDeterminant"], rel_tol=METRIC_RECOMPUTATION_TOLERANCE, abs_tol=1e-15)
            and math.isfinite(metric["averageIncludedFaceAreaNativeUnitsSquared"]) and metric["averageIncludedFaceAreaNativeUnitsSquared"] > 0,
            "Actual tensor arithmetic and the full derived determinant must agree.")
    checks = item["geometryChecks"]
    require(all(checks.get(k) is True for k in ("exactNativeNodeTopologyVerified", "convexityWithinDeclaredToleranceVerified", "facePlanarityWithinDeclaredToleranceVerified",
            "strictPositiveFacePyramidsVerified", "strictPositivePolygonFanTetrahedraVerified"))
            and checks.get("geometricSelfIntersectionChecked") is False and checks.get("nativeMeshQualityVerified") is False
            and checks.get("foundation12FacePlaneReplicationPassed") is True and checks.get("foundation12PlanarCosAngle") == FOUNDATION_PLANAR_COS_ANGLE
            and math.isfinite(checks.get("maximumOtherFaceCentreOutwardCosine", math.inf))
            and checks["maximumOtherFaceCentreOutwardCosine"] <= -FOUNDATION_PLANAR_COS_ANGLE
            and 0 <= checks["maximumRelativeFaceNonPlanarity"] <= PLANARITY_TOLERANCE
            and 0 <= checks["maximumRelativeOutsideHalfSpaceDistance"] <= CONVEXITY_TOLERANCE,
            "Conservative union geometry checks cannot be promoted or relaxed.")
    require(all(math.isfinite(checks[k]) and checks[k] > 0 for k in ("unionExtentNativeUnits", "minimumFacePyramidVolumeAssumedM3",
            "minimumFanTetrahedronVolumeAssumedM3", "minimumCentroidFacePyramidVolumeAssumedM3", "independentPyramidVolumeAssumedM3"))
            and len(checks["volumeCentroidNativeCoordinates"]) == 3 and all(math.isfinite(v) for v in checks["volumeCentroidNativeCoordinates"]),
            "Every measured face pyramid, fan volume and centroid must remain finite and positive.")
    independent = checks["independentPyramidVolumeAssumedM3"]; relative = abs(independent - union) / max(independent, union)
    require(relative <= pilot.VOLUME_TOLERANCE and math.isclose(checks["relativeIndependentPyramidVolumeDifference"], relative, rel_tol=1e-12, abs_tol=1e-15), "The independent positive pyramid integration must match actual union volume.")


def verify_impact(row, source_count, derived_count, pairs):
    keys = ("sourceFailedCellCount", "derivedFailedCellCount", "sourceFailedCellsMappedToPassingDerivedCellCount", "sourceFailedCellsMappedToStillFailingDerivedCellCount")
    require(row.get("determinantThreshold") == THRESHOLD and row.get("sourceCellCount") == source_count and row.get("derivedCellCount") == derived_count
            and all(isinstance(row.get(k), int) and not isinstance(row[k], bool) and 0 <= row[k] <= source_count for k in keys)
            and row.get("healthySourceCellsMappedToFailingDerivedCellCount") == 0 and row.get("unchangedSourceCellMetricEqualityVerified") is True
            and row.get("nativeFailedCellSetVerified") is False and row.get("nativeGeometryQualityVerified") is False,
            "All source/derived failure accounting and native limitations must remain explicit.")
    fixed = sum(sum(v < THRESHOLD for v in p["sourceDeterminants"]) for p in pairs if p["derivedReplicatedDeterminant"] >= THRESHOLD)
    reduction = sum(sum(v < THRESHOLD for v in p["sourceDeterminants"]) - (p["derivedReplicatedDeterminant"] < THRESHOLD) for p in pairs)
    require(row["sourceFailedCellsMappedToPassingDerivedCellCount"] == fixed
            and row["sourceFailedCellsMappedToStillFailingDerivedCellCount"] + fixed == row["sourceFailedCellCount"]
            and row["sourceFailedCellCount"] - row["derivedFailedCellCount"] == reduction
            and all(math.isfinite(row[k]) and row[k] >= 0 for k in ("sourceMinimumReplicatedDeterminant", "derivedMinimumReplicatedDeterminant")),
            "Determinant improvement and cell-count reduction must not hide remaining original failures.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-directory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-quality-json", type=Path, required=True)
    args = parser.parse_args()
    try:
        import os
        require(os.name == "posix", "Actual pinned HDF5 derivation runs only in the separate Ubuntu worker; no Windows policy change.")
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (original.MAX_MEMORY, original.MAX_MEMORY))
        report = repair(args.source_directory, args.output_dir, args.source_quality_json)
        if report["volumeMeshDerived"]:
            verify_output(args.output_dir)
        print(json.dumps({k: report.get(k) for k in ("status", "combinedCounts", "sourceReplicatedFailedCellCount", "combinedDeterminantImpact", "selection", "originalMeshesImmutable", "volumeMeshDerived", "actualCfdRunVerified", "mainApplicationWrites", "action")}, allow_nan=False))
        return 0 if report["volumeMeshDerived"] else 2
    except (ValueError, OSError, KeyError, TypeError, ImportError, MemoryError, OverflowError) as error:
        print(json.dumps({"status": "DeterminantPrototypeNotDerived", "action": str(error)[:500], "volumeMeshDerived": False, "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
