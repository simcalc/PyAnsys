"""Prepare a bounded Foundation-12 approximate classroom mesh recipe only.

Every surface triangle remains source-bound. snappyHexMesh will create new
approximate cells and boundary faces; native node/polygon identity is not claimed.
No native utility or flow solver is launched by this preparer.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import frozen_rotor_surface_union as union
import run_frozen_rotor_surface_check as surface_check

SCHEMA = "untirta.frozen-rotor-approximate-snappy-preparation.v1"
REPORT = "snappy-preparation.json"
MAX_COMPONENT_CELLS = 450000
MAX_COMBINED_CELLS = 1000000  # Actual admission is strictly less than this.
MAX_MEMORY_BYTES, MAX_WORKSPACE_BYTES, MAX_SECONDS = 4 * 1024**3, 1024**3, 900
MAX_FILE_BYTES, MAX_REPORT_BYTES = 128 * 1024**2, 128 * 1024
MAX_CANDIDATES, BASE_DIVISIONS, MAX_LEVEL = 64, 44, 2
BACKGROUND_PATCH = "backgroundBox"
RELATIVE_RAY_TOLERANCE, BARYCENTRIC_TOLERANCE = 1e-12, 1e-10
SOURCE_COMMIT = surface_check.SOURCE_COMMIT
SOURCE_BASE = f"https://github.com/OpenFOAM/OpenFOAM-12/blob/{SOURCE_COMMIT}/"
REFERENCES = ["https://doc.cfd.direct/openfoam/user-guide-v12/snappyhexmesh",
              SOURCE_BASE + "etc/caseDicts/mesh/generation/snappyHexMeshDict.cfg",
              SOURCE_BASE + "etc/caseDicts/surface/surfaceFeaturesDict",
              SOURCE_BASE + "applications/utilities/mesh/manipulation/splitMeshRegions/splitMeshRegions.C",
              SOURCE_BASE + "src/meshCheck/primitiveMeshCheck/primitiveMeshCheck.C",
              SOURCE_BASE + "applications/utilities/mesh/manipulation/topoSet/topoSet.C",
              SOURCE_BASE + "applications/utilities/mesh/manipulation/createNonConformalCouples/createNonConformalCouples.C"]
FALSE_FLAGS = ("commandExecutionPerformedByThisTool", "nativeCoordinateUnitsVerified", "nativeNodeIdentityEquivalent",
               "originalPolygonGeometryVerified", "volumeMeshGenerated", "meshQualityCheckedBySolver",
               "geometryApproximationQualified", "numericalQualityVerified", "nativeCaseGenerated",
               "qualifiedBaseline", "npsh3Qualified", "simulationExecuted", "actualCfdRunVerified",
               "mainApplicationWrites", "solverFieldsAuthored", "boundaryLayersGenerated")
PROOF_FILES = ("surface-check-execution.json", "surface-runtime-identity.json", "surface-union-report.json",
               "source-topology-report.json", "logs/00-impeller-surfaceCheck.log", "logs/01-volute-surfaceCheck.log",
               "components/impeller/impeller-union.obj", "components/volute/volute-union.obj")
DICT_NAMES = ("system/blockMeshDict", "system/surfaceFeaturesDict", "system/snappyHexMeshDict",
              "system/topoSetDict", "system/controlDict", "system/fvSchemes", "system/fvSolution")
SURFACE_PROOF_KEYS = {"schemaVersion", "checkedAt", "githubRepository", "githubRunId", "githubSourceCommit", "status",
    "limits", "sourceFileRecords", "authoredCommands", "components", "stages", "nativeSurfaceCheckExecuted",
    "originalSurfaceInputsImmutable", "executionSurfaceInputsImmutable", "nativeSurfaceGeometryAccepted",
    "nativeCoordinateUnitsVerified", "classroomMetresPerSourceCoordinateUnit", "nativeNodeIdentityEquivalent",
    "fluidVolumeConnectivityVerified", "vertexManifoldVerified", "volumeMeshGenerated", "meshQualityCheckedBySolver",
    "numericalQualityVerified", "qualifiedBaseline", "npsh3Qualified", "simulationExecuted", "actualCfdRunVerified",
    "mainApplicationWrites", "primarySource", "claimBoundary", "runtimeIdentityFile", "retainedNativeLogRecords",
    "workspaceBytes", "elapsedSeconds"}
REPORT_KEYS = {"schemaVersion", "createdAt", "status", "exampleDataCommit", "foundationSourceCommit", "components",
    "nativeCommandArguments", "authoredFileRecords", "sourceSurfaceFileRecords", "sourceProofFileRecords",
    "sourceSurfaceGeometryPrerequisiteVerified", "sourceSurfaceInputsImmutable", "classroomAssumptions", "limits",
    "requiredAcceptance", "claimBoundary", "references", *FALSE_FLAGS}
STATUS = "APPROXIMATE_CLASSROOM_MESH_RECIPE_PREPARED_NATIVE_ACCEPTANCE_REQUIRED"
CLASSROOM_ASSUMPTIONS = {"metresPerSourceCoordinateUnit": 1, "coordinateUnits": "UNVERIFIED",
    "surfaceApproximation": "Pinned original-HDF5 polygons were fan-triangulated and stored as float32 STL. OBJ preserves those triangles; uniform level-2 castellation and snapping create a distinct approximate boundary mesh on one common background grid. Snapping adjusts generated boundary points toward the source-bound surfaces; original HDF5/STL/OBJ targets remain unchanged. Layers are disabled; no native node/face identity or smooth-wall geometry accuracy is claimed.",
    "recipe": {"backgroundLargestExtentDivisions": BASE_DIVISIONS, "commonBackgroundGrid": True,
               "surfaceRefinementLevel": MAX_LEVEL, "snappingEnabled": True,
               "uniformVolumeRefinementLevel": MAX_LEVEL,
               "uniformRefinementDistanceFactorTimesBackgroundDiagonal": 1.01,
               "selectedConnectedRegion": "Measured source interior seed; actual native addressing maps retain generated topology and all nonempty physical patches.",
               "minimumGeneratedVolumeFractionRetained": .999,
               "maximumBackgroundCells": 100000,
               "priorTrial": "38177037633: uniform level-2 castellated-only trial generated 357337 combined cells; both components and merged native checkMesh passed, but unchanged NCC coverage rejected reported source min/mean/max 0/0.936/1.5 and target 0/0.938/2. No CFD ran. Earlier 38174426218 rejected 8 isolated zero-determinant and 3062 planar/concave impeller cells."},
    "wallResolutionQualified": False, "layersEnabled": False, "rotationOrFlowPhysicsAuthored": False,
    "insidePoint": "Measured surface-facet candidates with explicit three-ray parity, closest-triangle distance and grid-plane avoidance; no original fluid-cell centre is claimed."}
LIMITS = {"maximumSeconds": MAX_SECONDS, "maximumCpus": 1, "maximumMemoryBytes": MAX_MEMORY_BYTES,
    "maximumWorkspaceBytes": MAX_WORKSPACE_BYTES, "maximumFileBytes": MAX_FILE_BYTES, "maximumLogBytes": 4 * 1024**2,
    "maximumReportBytes": MAX_REPORT_BYTES, "maximumActualComponentCells": MAX_COMPONENT_CELLS,
    "exclusiveMaximumActualCombinedCells": MAX_COMBINED_CELLS,
    "enforcementOwner": "Independent native runner; preparation does not launch processes."}
REQUIRED_ACCEPTANCE = ["All actual authored 15 geometry stages completed under fixed resource limits",
    "Actual native source-seed connected-region selection before cell zones; immutable pre-selection mesh and exact point/cell/signed-face addressing preserve at least 99.9% of generated positive volume",
    "Actual selected cell refinement levels uniformly 2; original HDF5/STL/OBJ surface targets are never removed or changed",
    "Each actual component <=450000 cells and combined strictly<1000000; all 11 original patch names/types with positive faces",
    "Only empty backgroundBox may remain; each exact all-fluid zone covers its component",
    "Unmodified native checkMesh -allGeometry -allTopology passes component and coupled mesh checks",
    "Actual geometry area, volume, bounds and surface-deviation diagnostics; no accuracy qualification without declared numerical thresholds",
    "Actual nonconformal interface geometry and coverage; mesh/time convergence and physical CFD are separate later stages"]
CLAIM = "Source-bound approximate classroom mesh dictionaries only; no generated volume mesh, mesh-quality acceptance, geometry-accuracy qualification, solver fields, CFD baseline, cavitation or physical transient evidence."
SURFACE_CLAIM = "Native edge topology, orientation and edge-based self-intersection checks on source-bound float32-derived component surfaces only. No volume mesh, numerical baseline, physical CFD, native-node equivalence or physical-unit validation."


class SnappyIssue(ValueError):
    pass


def require(value, action):
    if not value:
        raise SnappyIssue(action)


def encoded(value):
    return union.encoded(value)


def safe_relative(value):
    require(isinstance(value, str) and len(value) <= 180 and re.fullmatch(r"[A-Za-z0-9_.\-/]+", value)
            and not value.startswith("/") and all(p not in ("", ".", "..") for p in value.split("/")),
            "Use only fixed safe relative evidence paths.")
    return value


def read(root, relative, maximum=MAX_FILE_BYTES):
    relative = safe_relative(relative)
    raw = union.source._regular_bytes(Path(root) / relative, maximum)
    return raw, union.file_row(relative, raw)


def json_file(root, relative):
    return union.read_json(read(root, relative, MAX_REPORT_BYTES)[0])


def inside(value):
    require(os.environ.get("RUNNER_TEMP"), "Use only the isolated RUNNER_TEMP workspace.")
    return union.source._inside(Path(value), Path(os.environ["RUNNER_TEMP"]))


def utc_timestamp(value):
    require(isinstance(value, str) and len(value) <= 40, "Keep a bounded UTC provenance timestamp.")
    try:
        require(datetime.fromisoformat(value).utcoffset().total_seconds() == 0, "Use explicit UTC provenance.")
    except (ValueError, AttributeError) as error:
        raise SnappyIssue("Use an actual UTC provenance timestamp.") from error


def verify_surface_proof(proof_directory, surface_directory, verified):
    """Re-read bound actual native logs and runtime; exit/status alone is insufficient."""
    proof = inside(proof_directory)
    report = json_file(proof, "surface-check-execution.json")
    require(isinstance(report, dict) and set(report) == SURFACE_PROOF_KEYS, "Copy only the exact reviewed successful surface-execution report fields.")
    utc_timestamp(report["checkedAt"])
    require(re.fullmatch(r"[1-9][0-9]{0,19}", report.get("githubRunId", ""))
            and re.fullmatch(r"[a-f0-9]{40}", report.get("githubSourceCommit", "")), "Use bounded actual public run and immutable commit provenance.")
    require(report.get("schemaVersion") == surface_check.SCHEMA and report.get("status") == "NATIVE_SURFACE_GEOMETRY_CHECKED"
            and report.get("githubRepository") == "simcalc/PyAnsys", "Successful source-bound native Foundation-12 surface geometry evidence is required before a volume-mesh attempt.")
    require(report.get("primarySource") == surface_check.SOURCE_URL
            and report.get("limits") == {"maximumSeconds": surface_check.MAX_SECONDS, "maximumCpus": 1,
                "maximumMemoryBytes": surface_check.MAX_MEMORY, "maximumWorkspaceBytes": surface_check.MAX_WORKSPACE,
                "maximumLogBytes": surface_check.MAX_LOG}, "Preserve the reviewed native surface source and bounded execution contract.")
    require(all(report.get(key) is True for key in ("nativeSurfaceCheckExecuted", "nativeSurfaceGeometryAccepted",
            "originalSurfaceInputsImmutable", "executionSurfaceInputsImmutable")), "Actual complete native surface checks and unchanged execution/source inputs are required.")
    require(all(report.get(key) is False for key in ("nativeCoordinateUnitsVerified", "nativeNodeIdentityEquivalent",
            "volumeMeshGenerated", "meshQualityCheckedBySolver", "numericalQualityVerified", "qualifiedBaseline",
            "npsh3Qualified", "simulationExecuted", "actualCfdRunVerified", "mainApplicationWrites",
            "fluidVolumeConnectivityVerified", "vertexManifoldVerified"))
            and report.get("classroomMetresPerSourceCoordinateUnit") == 1, "Surface diagnostics cannot establish volume quality, physical units or CFD qualification.")
    require(report.get("claimBoundary") == SURFACE_CLAIM
            and isinstance(report.get("workspaceBytes"), int) and not isinstance(report["workspaceBytes"], bool)
            and 0 < report["workspaceBytes"] <= surface_check.MAX_WORKSPACE
            and isinstance(report.get("elapsedSeconds"), (int, float)) and not isinstance(report["elapsedSeconds"], bool)
            and math.isfinite(report["elapsedSeconds"]) and 0 <= report["elapsedSeconds"] <= surface_check.MAX_SECONDS,
            "Preserve the exact native surface claim and measured bounded workspace/timing metadata.")
    runtime_raw, runtime_row = read(proof, "surface-runtime-identity.json", MAX_REPORT_BYTES)
    require(report.get("runtimeIdentityFile") == runtime_row, "Native surface runtime bytes must match the execution report.")
    runtime = union.read_json(runtime_raw)
    require(isinstance(runtime, dict) and set(runtime) == {"schemaVersion", "openFoamFoundationVersion", "packageVersion",
        "officialDebSha256", "expectedSourceCommit", "packageOwnership", "binaries", "mainApplicationWrites"},
        "Only the reviewed surface runtime identity fields are allowed.")
    require(runtime.get("schemaVersion") == "untirta.frozen-rotor-surface-runtime-identity.v1"
            and runtime.get("openFoamFoundationVersion") == 12 and runtime.get("packageVersion") == "20250206"
            and runtime.get("officialDebSha256") == surface_check.PACKAGE_SHA
            and runtime.get("expectedSourceCommit") == SOURCE_COMMIT and runtime.get("mainApplicationWrites") is False,
            "Use actual pinned Foundation-12 binary/package evidence for the reviewed surface checker.")
    binaries = runtime.get("binaries", [])
    require(isinstance(binaries, list) and len(binaries) == 1
            and isinstance(binaries[0], dict) and set(binaries[0]) == {"program", "path", "bytes", "sha256"}
            and binaries[0].get("program") == "surfaceCheck"
            and isinstance(binaries[0].get("bytes"), int) and not isinstance(binaries[0]["bytes"], bool) and binaries[0]["bytes"] > 0
            and re.fullmatch(r"[a-f0-9]{64}", binaries[0].get("sha256", ""))
            and re.fullmatch(r"/opt/openfoam12/[A-Za-z0-9_./-]+/surfaceCheck", binaries[0].get("path", ""))
            and ".." not in binaries[0]["path"].split("/")
            and runtime.get("packageOwnership") == "openfoam12: " + binaries[0]["path"], "Bind the actual official surfaceCheck binary and package ownership.")
    source_records = [verified["reportFile"], read(surface_directory, union.SOURCE_REPORT, MAX_REPORT_BYTES)[1],
                      *(row["surfaceFile"] for row in verified["components"])]
    require(report.get("sourceFileRecords") == source_records, "Native surface proof must bind these exact verified union/report bytes.")
    require(len(report.get("stages", [])) == len(report.get("components", [])) == 2,
            "Both complete actual component surface-check stages are required.")
    require(report.get("authoredCommands") == [stage.get("arguments") for stage in report["stages"]],
            "The evidence may contain only the two reviewed actual surface utility commands.")
    retained_logs = []
    for i, component in enumerate(verified["components"]):
        name = component["component"]
        stage, row = report["stages"][i], report["components"][i]
        require(set(stage) == {"program", "component", "arguments", "exitCode", "elapsedSeconds", "logFile"}
                and set(row) == {"component", "sourceSurfaceFile", "nativeGeometry"}, "Native stage and component records must retain their exact reviewed roles.")
        log_path = f"logs/{i:02d}-{name}-surfaceCheck.log"
        raw, log_row = read(proof, log_path, surface_check.MAX_LOG)
        retained_logs.append(log_row)
        arguments = stage.get("arguments", [])
        require(stage.get("program") == "surfaceCheck" and stage.get("component") == name and stage.get("exitCode") == 0
                and stage.get("logFile") == log_row and isinstance(arguments, list) and len(arguments) == 3
                and arguments[:2] == ["surfaceCheck", "-checkSelfIntersection"] and isinstance(arguments[2], str)
                and arguments[2].endswith(f"/components/{name}/{name}-union.obj")
                and row.get("component") == name and row.get("sourceSurfaceFile") == component["surfaceFile"],
                "Exact authored successful surface-check stages and immutable native logs are required.")
        require(isinstance(stage.get("elapsedSeconds"), (int, float)) and not isinstance(stage["elapsedSeconds"], bool)
                and math.isfinite(stage["elapsedSeconds"]) and 0 <= stage["elapsedSeconds"] <= surface_check.MAX_SECONDS,
                "Native surface stage timing must be finite and within its authored deadline.")
        actual = surface_check.parse_log(raw.decode("utf-8", errors="strict"), component)
        require(actual == row.get("nativeGeometry") and actual["nativeSurfaceGeometryAccepted"] is True,
                "Recomputed native topology/orientation/self-intersection diagnostics must actually pass.")
        require(read(proof, f"components/{name}/{name}-union.obj")[0]
                == read(surface_directory, name + "-union.obj")[0], "Native checked surfaces must equal the exact copied input surface bytes.")
    require(report.get("retainedNativeLogRecords") == retained_logs, "Both exact complete actual native logs must remain hash-bound.")
    for name in (union.REPORT, union.SOURCE_REPORT):
        require(read(proof, name, MAX_REPORT_BYTES)[0] == read(surface_directory, name, MAX_REPORT_BYTES)[0],
                "Native checked report provenance must remain byte-identical to this surface package.")
    return {name: read(proof, name, surface_check.MAX_LOG if name.endswith(".log") else MAX_FILE_BYTES)[0] for name in PROOF_FILES}


def source_triangles(surface_directory, component):
    result = []
    for zone, _, _, _, count, _ in union.STL_PINS[component]:
        raw = read(surface_directory, f"source-stl/{component}-zone-{zone}.stl", union.MAX_STL_BYTES)[0]
        dtype = np.dtype({"names": ["vertices"], "formats": [("<f4", (3, 3))], "offsets": [12], "itemsize": 50})
        result.append(np.frombuffer(raw, dtype=dtype, count=count, offset=84)["vertices"].astype(np.float64))
    triangles = np.concatenate(result)
    require(np.isfinite(triangles).all() and len(triangles) <= 300000, "Use bounded exact stored float32 source triangles.")
    return triangles


def background_mesh(triangles):
    minimum, maximum = triangles.min(axis=(0, 1)), triangles.max(axis=(0, 1))
    widths = maximum - minimum
    require(np.isfinite(widths).all() and np.all(widths > 0), "A three-dimensional measured component bounding box is required.")
    span = float(widths.max())
    lower, upper = minimum - .1 * span, maximum + .1 * span
    h = float((upper - lower).max()) / BASE_DIVISIONS
    counts = np.maximum(4, np.ceil((upper - lower) / h).astype(np.int64))
    require(int(np.prod(counts)) <= 100000, "The authored background trial must remain below 100,000 cells within the unchanged worker limits.")
    return {"minimum": lower.tolist(), "maximum": upper.tolist(), "cellCounts": counts.tolist(),
            "initialCellCount": int(np.prod(counts)), "cellWidthsNativeUnits": ((upper - lower) / counts).tolist(),
            "basis": "Common measured bounds of both complete source surfaces, padded on every side by 10% of the largest source extent; approximately isotropic background cells with largest extent divided by " + str(BASE_DIVISIONS) + "."}


def ray_parity(point, triangles, direction):
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    e1, e2 = b - a, c - a
    direction = np.asarray(direction, dtype=np.float64)
    require(direction.shape == (3,) and np.isfinite(direction).all() and np.linalg.norm(direction) > 0,
            "Use a finite nonzero geometric test ray.")
    direction /= np.linalg.norm(direction)
    p = np.cross(np.broadcast_to(direction, e2.shape), e2)
    determinant = np.einsum("ij,ij->i", e1, p)
    scale = np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1)
    usable = np.abs(determinant) > RELATIVE_RAY_TOLERANCE * scale
    inv = np.divide(1., determinant, out=np.zeros_like(determinant), where=usable)
    s = np.asarray(point) - a
    u = np.einsum("ij,ij->i", s, p) * inv
    q = np.cross(s, e1)
    v = q @ direction * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    span = float(np.ptp(triangles, axis=(0, 1)).max())
    normals = np.cross(e1, e2)
    # A ray lying in a facet plane cannot establish a unique intersection.
    # Reject this conservative ambiguity even outside the facet projection;
    # no intersection is merged, perturbed or silently assigned a parity.
    coplanar = ~usable & (np.abs(np.einsum("ij,ij->i", s, normals))
                          <= span * RELATIVE_RAY_TOLERANCE * np.linalg.norm(normals, axis=1))
    hits = usable & (t > span * 1e-12) & (u >= -BARYCENTRIC_TOLERANCE) & (v >= -BARYCENTRIC_TOLERANCE) & (u + v <= 1 + BARYCENTRIC_TOLERANCE)
    ambiguous = hits & ((u <= BARYCENTRIC_TOLERANCE) | (v <= BARYCENTRIC_TOLERANCE) | (1 - u - v <= BARYCENTRIC_TOLERANCE))
    count = int(hits.sum())
    return {"direction": direction.tolist(), "intersectionCount": count, "insideByOddParity": count % 2 == 1,
            "ambiguousEdgeOrVertexHitCount": int(ambiguous.sum()), "ambiguousCoplanarTriangleCount": int(coplanar.sum())}


def nearest_triangle_distance(point, triangles):
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    e1, e2, p = b - a, c - a, np.asarray(point) - a
    normal = np.cross(e1, e2)
    nn = np.einsum("ij,ij->i", normal, normal)
    require(np.all(nn > 0), "Seed distance checks cannot use collapsed source triangles.")
    d00, d01, d11 = (np.einsum("ij,ij->i", x, y) for x, y in ((e1, e1), (e1, e2), (e2, e2)))
    d20, d21 = np.einsum("ij,ij->i", p, e1), np.einsum("ij,ij->i", p, e2)
    v, w = (d11 * d20 - d01 * d21) / nn, (d00 * d21 - d01 * d20) / nn
    signed = np.einsum("ij,ij->i", p, normal)
    distance_squared = np.where((v >= 0) & (w >= 0) & (v + w <= 1), signed * signed / nn, np.inf)
    for first, last in ((a, b), (b, c), (c, a)):
        edge = last - first
        along = np.einsum("ij,ij->i", np.asarray(point) - first, edge) / np.einsum("ij,ij->i", edge, edge)
        difference = np.asarray(point) - first - edge * np.clip(along, 0, 1)[:, None]
        distance_squared = np.minimum(distance_squared, np.einsum("ij,ij->i", difference, difference))
    value = float(np.sqrt(distance_squared.min()))
    require(math.isfinite(value), "A finite measured nearest-surface clearance is required.")
    return value


def grid_clearance(point, background):
    widths = np.asarray(background["cellWidthsNativeUnits"])
    position = (np.asarray(point) - np.asarray(background["minimum"])) / widths * 2**MAX_LEVEL
    return float(np.min(np.abs(position - np.rint(position))))


def find_inside_point(triangles, background):
    """Construct candidates from measured facets, never an invented pump point."""
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    vectors = np.cross(b - a, c - a)
    lengths = np.linalg.norm(vectors, axis=1)
    require(np.isfinite(lengths).all() and np.all(lengths > 0), "All source seed facets must be finite and noncollapsed.")
    areas = lengths / 2
    # Stable source-order tie-break, not a nearest-label guess.
    ranked = np.argsort(-areas, kind="stable")[:16]
    directions = ((1., math.sqrt(2), math.sqrt(3)), (math.sqrt(5), 1., math.sqrt(7)), (math.sqrt(11), math.sqrt(13), 1.))
    span = float(np.ptp(triangles, axis=(0, 1)).max())
    accepted, tested, ambiguous = [], 0, 0
    for index in ranked:
        centre, normal = triangles[index].mean(axis=0), vectors[index] / lengths[index]
        for factor in (.125, .5):
            for sign in (-1, 1):
                tested += 1
                require(tested <= MAX_CANDIDATES, "The deterministic geometric seed candidate budget was reached.")
                offset = sign * factor * math.sqrt(float(areas[index]))
                point = centre + normal * offset
                grid_distance = grid_clearance(point, background)
                if grid_distance <= 1e-7:
                    continue
                rays = [ray_parity(point, triangles, direction) for direction in directions]
                ambiguous += any(row["ambiguousEdgeOrVertexHitCount"] or row["ambiguousCoplanarTriangleCount"] for row in rays)
                if not all(row["insideByOddParity"] and row["ambiguousEdgeOrVertexHitCount"] == 0
                           and row["ambiguousCoplanarTriangleCount"] == 0 for row in rays):
                    continue
                clearance = nearest_triangle_distance(point, triangles)
                if clearance <= span * 1e-8:
                    continue
                accepted.append({"pointNativeUnits": point.tolist(), "sourceTriangleIndex": int(index),
                    "sourceTriangleCentroidNativeUnits": centre.tolist(), "sourceTriangleUnitNormal": normal.tolist(),
                    "sourceTriangleAreaNativeUnitsSquared": float(areas[index]), "signedNormalOffsetNativeUnits": offset,
                    "offsetFactorTimesSquareRootArea": sign * factor, "nearestTriangleDistanceNativeUnits": clearance,
                    "minimumRefinementGridPlaneDistanceInFinestCellWidths": grid_distance, "rayParityEvidence": rays})
    require(accepted, f"No robust interior seed among {tested} measured source-facet candidates. Supply an independently verified original fluid-cell centre or revise the explicitly declared surface seed method; no coordinate is fabricated.")
    best = max(accepted, key=lambda row: row["nearestTriangleDistanceNativeUnits"])
    best.update(candidateCountEvaluated=tested, acceptedCandidateCount=len(accepted), ambiguousCandidateCount=int(ambiguous),
                allThreeNondegenerateRaysInside=True, originalVolumeCellCentreClaimed=False,
                nativeInsidePointTestExecuted=False, nativeFluidVolumeConnectivityVerified=False,
                relativeRayDeterminantTolerance=RELATIVE_RAY_TOLERANCE, barycentricEdgeAmbiguityTolerance=BARYCENTRIC_TOLERANCE,
                minimumAllowedSurfaceDistanceNativeUnits=span * 1e-8,
                method="Source triangle centroid plus/minus two declared normal offsets; test 3 nonparallel forward-ray odd parities without edge hits or coplanar ambiguity, measure closest point on every stored triangle, avoid all authored dyadic grid planes through refinement level 2; choose greatest measured clearance among at most 64 candidates.",
                equationStages={"input": {"sourceTriangleIndex": best["sourceTriangleIndex"], "areaNativeUnitsSquared": best["sourceTriangleAreaNativeUnitsSquared"]},
                    "equation": "x_seed = (a+b+c)/3 + f*sqrt(A)*n; inside iff all 3 unambiguous ray intersection counts are odd",
                    "substitution": {"centroid": best["sourceTriangleCentroidNativeUnits"], "unitNormal": best["sourceTriangleUnitNormal"], "signedOffset": best["signedNormalOffsetNativeUnits"]},
                    "result": {"insidePointNativeUnits": best["pointNativeUnits"], "surfaceClearanceNativeUnits": best["nearestTriangleDistanceNativeUnits"], "nativeUnitsVerified": False}})
    return best


def header(name):
    return "FoamFile {format ascii; class dictionary; object " + name + ";}\n"


def vector(values):
    return "(" + " ".join(format(float(value), ".17g") for value in values) + ")"


def case_files(component, component_surface, background, seed):
    minimum, maximum = background["minimum"], background["maximum"]
    x0, y0, z0 = minimum
    x1, y1, z1 = maximum
    vertices = ((x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),(x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1))
    mesh = header("blockMeshDict") + "convertToMeters 1;\nvertices\n(\n" + "\n".join(vector(p) for p in vertices) + "\n);\n"
    mesh += "blocks (hex (0 1 2 3 4 5 6 7) " + vector(background["cellCounts"]) + " simpleGrading (1 1 1));\nedges ();\nboundary\n(\n"
    mesh += BACKGROUND_PATCH + " {type patch; faces ((0 4 7 3)(1 2 6 5)(0 1 5 4)(3 7 6 2)(0 3 2 1)(4 5 6 7));}\n);\nmergePatchPairs ();\n"
    regions = "\n".join(name + " {name " + name + ";}" for name in (row["name"] for row in component_surface["patches"]))
    refinements = "\n".join(row["name"] + " {level (2 2); patchInfo {type " + ("wall" if row["sourceZoneType"] == 3 else "patch") + ";}}" for row in component_surface["patches"])
    snappy = header("snappyHexMeshDict") + "castellatedMesh true; snap true; addLayers false;\ngeometry\n{\n"
    snappy += component + " {type triSurfaceMesh; file \"" + component + "-union.obj\"; regions {\n" + regions + "\n}}\n}\n"
    snappy += "castellatedMeshControls\n{\nmaxLocalCells 450000; maxGlobalCells 450000; minRefinementCells 0; maxLoadUnbalance 0.1; nCellsBetweenLevels 3; resolveFeatureAngle 30; allowFreeStandingZoneFaces true; extendedRefinementSpan true;\n"
    distance = 1.01 * float(np.linalg.norm(np.asarray(maximum) - np.asarray(minimum)))
    snappy += "features ({file \"" + component + "-union.eMesh\"; level 2;});\nrefinementSurfaces {" + component + " {level (2 2); regions {\n" + refinements + "\n}}}\n"
    snappy += "refinementRegions {" + component + " {mode distance; levels ((" + format(distance, ".17g") + " 2));}};\ninsidePoint " + vector(seed["pointNativeUnits"]) + ";\n}\n"
    snappy += "snapControls {nSmoothPatch 3; tolerance 2; nSolveIter 60; nRelaxIter 5; nFeatureSnapIter 10; explicitFeatureSnap true; multiRegionFeatureSnap true; implicitFeatureSnap false;}\n"
    snappy += "addLayersControls {layers {}; relativeSizes true; expansionRatio 1.2; finalLayerThickness 0.3; minThickness 0.1; nGrow 0; featureAngle 60; nRelaxIter 5; nSmoothSurfaceNormals 1; nSmoothNormals 3; nSmoothThickness 10; maxFaceThicknessRatio 0.5; maxThicknessToMedialRatio 0.3; minMedialAxisAngle 90; nBufferCellsNoExtrude 0; nLayerIter 0; nRelaxedIter 0;}\n"
    snappy += "meshQualityControls {maxNonOrtho 65; maxBoundarySkewness 20; maxInternalSkewness 4; maxConcave 80; minFlatness 0.5; minVol 1e-14; minTetQuality 1e-15; minArea 1e-14; minTwist 0.05; minDeterminant 0.001; minFaceWeight 0.05; minVolRatio 0.01; minTriangleTwist 0; nSmoothScale 4; errorReduction 0.75;}\nmergeTolerance 1e-6; debug 0; writeFlags ();\n"
    zone = "rotating-fluid" if component == "impeller" else "stationary-fluid"
    topo = header("topoSetDict") + "actions\n(\n{ name allFluidCells; type cellSet; action new; source boxToCell; box " + vector(minimum) + vector(maximum) + ";}\n"
    topo += "{name " + zone + "; type cellZoneSet; action new; source setToCellZone; set allFluidCells;}\n);\n"
    result = {"system/blockMeshDict": mesh, "system/snappyHexMeshDict": snappy, "system/topoSetDict": topo,
              "system/surfaceFeaturesDict": header("surfaceFeaturesDict") + 'surfaces ("' + component + '-union.obj");\nincludedAngle 120; geometricTestOnly no; subsetFeatures {nonManifoldEdges yes; openEdges yes;} writeObj no; writeVTK no;\n',
              "system/controlDict": header("controlDict") + "application snappyHexMesh; startFrom startTime; startTime 0; stopAt endTime; endTime 1; deltaT 1; writeControl timeStep; writeInterval 1; writeFormat ascii; writePrecision 17; writeCompression off; timeFormat general; timePrecision 17; runTimeModifiable false; functions {};\n",
              "system/fvSchemes": header("fvSchemes") + "ddtSchemes {default steadyState;} gradSchemes {default Gauss linear;} divSchemes {default none;} laplacianSchemes {default Gauss linear corrected;} interpolationSchemes {default linear;} snGradSchemes {default corrected;}\n",
              "system/fvSolution": header("fvSolution") + "solvers {};\n"}
    if component == "impeller":
        result["system/createNonConformalCouplesDict"] = header("createNonConformalCouplesDict") + "fields false; nonConformalCouples {pumpInterface {patches (interface-impeller-outlet interface-volute-inlet); transform none;}}\n"
    return {key: value.encode("ascii") for key, value in result.items()}


def commands(output, components=None):
    root = Path(output).absolute()
    if components is None:
        components = json_file(root, REPORT)["components"]
    require(isinstance(components, list) and [row["component"] for row in components] == ["impeller", "volute"],
            "Author region selection only from the two measured source component seeds.")
    seeds = {}
    for component in components:
        seed = component["insidePointEvidence"]["pointNativeUnits"]
        require(isinstance(seed, list) and len(seed) == 3 and all(type(v) in (float, int) and math.isfinite(v) for v in seed),
                "Use the exact finite measured three-dimensional inside point for native region selection.")
        seeds[component["component"]] = vector(seed)
    result = []
    for name in ("impeller", "volute"):
        case = str(root / (name + "-case"))
        result.extend([["blockMesh", "-case", case], ["surfaceFeatures", "-case", case],
                       ["snappyHexMesh", "-case", case, "-overwrite"],
                       ["splitMeshRegions", "-case", case, "-insidePoint", seeds[name], "-overwrite"],
                       ["topoSet", "-case", case, "-constant"],
                       ["checkMesh", "-case", case, "-allGeometry", "-allTopology", "-writeSets"]])
    rotor, stator = str(root / "impeller-case"), str(root / "volute-case")
    result.extend([["mergeMeshes", "-case", rotor, "-addCases", '("' + stator + '")', "-overwrite"],
                   ["createNonConformalCouples", "-case", rotor, "-overwrite"],
                   ["checkMesh", "-case", rotor, "-allGeometry", "-allTopology", "-writeSets"]])
    return result


def source_union_payloads(directory):
    names = [union.REPORT, union.SOURCE_REPORT, "impeller-union.obj", "volute-union.obj",
             *("source-stl/" + row["name"] for row in union.expected_stls())]
    return {name: read(directory, name, MAX_REPORT_BYTES if name.endswith(".json") else MAX_FILE_BYTES)[0] for name in names}


def make_components(surface_directory, verified):
    components, payloads = [], {}
    source_components = {row["component"]: source_triangles(surface_directory, row["component"])
                         for row in verified["components"]}
    background = background_mesh(np.concatenate(list(source_components.values()), axis=0))
    for surface in verified["components"]:
        name = surface["component"]
        triangles = source_components[name]
        seed = find_inside_point(triangles, background)
        folder = name + "-case"
        component = {"component": name, "caseDirectory": folder, "cellZone": "rotating-fluid" if name == "impeller" else "stationary-fluid",
                     "patchNames": [p["name"] for p in surface["patches"]],
                     "patchTypes": {p["name"]: "wall" if p["sourceZoneType"] == 3 else "patch" for p in surface["patches"]},
                     "backgroundPatchName": BACKGROUND_PATCH, "backgroundMesh": background, "insidePointEvidence": seed,
                     "surfaceFile": {**surface["surfaceFile"], "path": folder + "/constant/geometry/" + name + "-union.obj"}}
        components.append(component)
        payloads.update({folder + "/" + key: value for key, value in case_files(name, surface, background, seed).items()})
        payloads[component["surfaceFile"]["path"]] = read(surface_directory, name + "-union.obj")[0]
    return components, payloads


def prepare(surface_union_directory, surface_check_directory, output_directory):
    surfaces, proof, output = inside(surface_union_directory), inside(surface_check_directory), inside(output_directory)
    require(not output.exists() and not any(output.is_relative_to(p) or p.is_relative_to(output) for p in (surfaces, proof)),
            "Use a fresh isolated case output independent of surface and native proof inputs.")
    verified = union.verify_output(surfaces)
    proof_payloads = verify_surface_proof(proof, surfaces, verified)
    components, authored = make_components(surfaces, verified)
    source_payloads = source_union_payloads(surfaces)
    report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(),
              "status": STATUS, "exampleDataCommit": union.source.PIN,
              "foundationSourceCommit": SOURCE_COMMIT, "components": components, "nativeCommandArguments": commands(output, components),
              "authoredFileRecords": [union.file_row(name, raw) for name, raw in sorted(authored.items())],
              "sourceSurfaceFileRecords": [union.file_row("source-surface/" + name, raw) for name, raw in sorted(source_payloads.items())],
              "sourceProofFileRecords": [union.file_row("source-surface-check/" + name, raw) for name, raw in sorted(proof_payloads.items())],
              "sourceSurfaceGeometryPrerequisiteVerified": True, "sourceSurfaceInputsImmutable": True,
              "classroomAssumptions": CLASSROOM_ASSUMPTIONS, "limits": LIMITS,
              "requiredAcceptance": REQUIRED_ACCEPTANCE, "claimBoundary": CLAIM,
              "references": REFERENCES}
    report.update({key: False for key in FALSE_FLAGS})
    raw_report = encoded(report)
    require(len(raw_report) <= MAX_REPORT_BYTES, "Preparation report exceeds 128 KiB.")
    all_payloads = {**authored, **{"source-surface/" + k: v for k, v in source_payloads.items()},
                    **{"source-surface-check/" + k: v for k, v in proof_payloads.items()}, REPORT: raw_report}
    require(sum(map(len, all_payloads.values())) <= MAX_WORKSPACE_BYTES, "Prepared surface/case bytes exceed the 1 GiB budget.")
    require(source_union_payloads(surfaces) == source_payloads and verify_surface_proof(proof, surfaces, verified) == proof_payloads,
            "Surface and native geometry proof inputs changed before preparation persistence.")
    written = []
    directories = []
    try:
        output.mkdir(parents=True, exist_ok=False)
        for name, raw in all_payloads.items():
            target = output / name
            missing = []
            parent = target.parent
            while parent != output and not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for folder in reversed(missing):
                folder.mkdir(exist_ok=False)
                directories.append(folder)
            written.append(target)
            with target.open("xb") as stream:
                require(stream.write(raw) == len(raw), "A prepared case write was incomplete.")
        verify_output(output)
        require(source_union_payloads(surfaces) == source_payloads and verify_surface_proof(proof, surfaces, verified) == proof_payloads,
                "Original surface/proof bytes must remain unchanged after persistence.")
    except Exception:
        for path in reversed(written):
            if path.exists() and path.is_file():
                path.unlink()
        for path in reversed(directories):
            if path.exists():
                path.rmdir()
        if output.exists():
            output.rmdir()
        raise
    return report


def verify_output(directory):
    output = inside(directory)
    raw, report_row = read(output, REPORT, MAX_REPORT_BYTES)
    report = union.read_json(raw)
    require(isinstance(report, dict) and set(report) == REPORT_KEYS, "Use only the exact reviewed snappy preparation report fields.")
    utc_timestamp(report["createdAt"])
    require(report.get("schemaVersion") == SCHEMA and report.get("exampleDataCommit") == union.source.PIN
            and report.get("foundationSourceCommit") == SOURCE_COMMIT and all(report.get(key) is False for key in FALSE_FLAGS)
            and report.get("sourceSurfaceGeometryPrerequisiteVerified") is True and report.get("sourceSurfaceInputsImmutable") is True,
            "Preserve the exact preparation source identities and no-native-mesh/CFD claim boundaries.")
    require(report.get("status") == STATUS and report.get("classroomAssumptions") == CLASSROOM_ASSUMPTIONS
            and report.get("limits") == LIMITS and report.get("requiredAcceptance") == REQUIRED_ACCEPTANCE
            and report.get("claimBoundary") == CLAIM and report.get("references") == REFERENCES,
            "The declared geometry approximation, resource limits, engineering gates and references cannot be changed silently.")
    require(report.get("nativeCommandArguments") == commands(output, report["components"]), "Only the fixed authored 15 geometry utility stages are allowed.")
    verified = union.verify_output(output / "source-surface")
    proof = verify_surface_proof(output / "source-surface-check", output / "source-surface", verified)
    source_payloads = source_union_payloads(output / "source-surface")
    components, authored = make_components(output / "source-surface", verified)
    require(report.get("components") == components, "Every measured seed, background bound, patch role and recipe input must match source geometry.")
    expected_records = [union.file_row(name, data) for name, data in sorted(authored.items())]
    require(report.get("authoredFileRecords") == expected_records and all(read(output, name)[0] == data for name, data in authored.items()),
            "Every authored dictionary/surface byte must remain unchanged, including after native meshing.")
    require(report.get("sourceSurfaceFileRecords") == [union.file_row("source-surface/" + n, d) for n, d in sorted(source_payloads.items())]
            and report.get("sourceProofFileRecords") == [union.file_row("source-surface-check/" + n, d) for n, d in sorted(proof.items())],
            "All copied actual surface/proof byte inventories must remain exact.")
    # Native mesh/eMesh/log files may be added by the independent runner. It
    # owns their whole-workspace bound and native validity checks; none are
    # admitted as authored input or numerical proof by this preparer.
    return {"report": report, "reportFile": report_row, "components": components}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface-directory", type=Path, required=True)
    parser.add_argument("--surface-check-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        if sys.platform == "linux":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
        report = prepare(args.surface_directory, args.surface_check_directory, args.output_directory)
        print(json.dumps({"status": report["status"], "nativeCommandCount": len(report["nativeCommandArguments"]),
                          "volumeMeshGenerated": False, "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 0
    except Exception as error:
        print(json.dumps({"status": "APPROXIMATE_MESH_INPUT_CORRECTION_REQUIRED", "action": str(error)[:600],
                          "volumeMeshGenerated": False, "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
