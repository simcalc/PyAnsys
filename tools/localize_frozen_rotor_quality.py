"""Localize the original source mesh's Foundation 12 wellposedness metric.

This read-only diagnostic retains the 0.001 gate and original immutable meshes.
Source arithmetic is an independent numerical replication, not native quality
acceptance. Exact native failed-cell equivalence requires the exported native
cellSet, its source-order proof and its actual coupled-face mask proof.
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
import convert_frozen_rotor_native_h5 as original

SCHEMA = "untirta.frozen-rotor-source-quality-localization.v1"
THRESHOLD = .001
FOAM_COMMIT = "0b487fc98b88d3e8dc3c3ead4662e7632dd5e7dd"
PRIMARY = "https://raw.githubusercontent.com/OpenFOAM/OpenFOAM-12/" + FOAM_COMMIT + "/"
MAX_REPORT = 32 * 1024**2
MAX_FAILED = 100_000
MAX_HOPS = 256
CHUNK = 20_000
CELL_TYPES = {2: "tetrahedron", 4: "hexahedron", 5: "pyramid", 6: "prism", 7: "polyhedron"}


class LocalizationIssue(ValueError):
    pass


def require(condition, action):
    if not condition:
        raise LocalizationIssue(action)


def plain_file(path, maximum=MAX_REPORT):
    path = Path(path).absolute()
    require(all(not item.is_symlink() and not getattr(item, "is_junction", lambda: False)()
                for item in (path, *path.parents)), "Diagnostic files cannot traverse links or junctions.")
    require(path.is_file() and 0 < path.stat().st_size <= maximum, "Use a regular nonempty bounded diagnostic file.")
    raw = path.read_bytes()
    require(len(raw) <= maximum, "The actual diagnostic read exceeded its byte bound.")
    return raw, {"name": path.name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def polygon_areas(model):
    """Foundation face::areaAndCentre area formula, retaining float64 points.

The vector has the stored source winding. Its sign disappears in the cell's
outer-product tensor. Polygon area is the sum of triangles to vertex mean;
triangle area uses the native direct cross-product path.
"""
    import numpy as np
    widths, offsets, nodes = model["faceWidths"], model["faceOffsets"], model["faceNodes"]
    xyz, count = model["coordinates"], model["faceCount"]
    require(0 < count <= original.MAX_FACES and len(widths) == count and len(offsets) == count + 1
            and int(offsets[0]) == 0 and int(offsets[-1]) == len(nodes)
            and np.array_equal(np.diff(offsets), widths) and np.all((widths >= 3) & (widths <= 128)),
            "Every bounded source polygon and connectivity range must be complete.")
    require(xyz.ndim == 2 and xyz.shape[1] == 3 and 0 < len(xyz) <= original.MAX_NODES
            and np.isfinite(xyz).all() and np.all((nodes >= 0) & (nodes < len(xyz))),
            "Every source polygon must reference finite original float64 coordinates.")
    result = np.empty((count, 3), dtype="f8")
    for first in range(0, count, CHUNK):
        last = min(first + CHUNK, count)
        for width in np.unique(widths[first:last]):
            ids = np.flatnonzero(widths[first:last] == width) + first
            references = nodes[offsets[ids, None] + np.arange(int(width))]
            points = xyz[references]
            if width == 3:
                area = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0]) / 2
            else:
                mean = points.mean(axis=1)
                # Sum in the native polygon's edge order, without triangulating
                # or replacing any original polygon in the source mesh.
                area = np.zeros((len(ids), 3), dtype="f8")
                for index in range(int(width)):
                    area += np.cross(points[:, (index + 1) % int(width)] - points[:, index],
                                     mean - points[:, index])
                area /= 2
            require(np.isfinite(area).all() and np.all(np.linalg.norm(area, axis=1) > 0),
                    "Source face areas must remain finite and nonzero; no repair is performed.")
            result[ids] = area
    return result


def cell_determinants(face_areas, c0, c1, cell_count):
    """Replicate the default 3D checkCellDeterminant internal-face formula.

Source physical boundaries are excluded. This applies only where every source
boundary is an uncoupled patch; native nonconformal patches have zero polyfaces.
No meshQualityDict overload or user-adjustable threshold is used.
"""
    import numpy as np
    require(isinstance(cell_count, int) and not isinstance(cell_count, bool) and 0 < cell_count <= original.MAX_CELLS,
            "Use a bounded complete original 3D cell count.")
    areas, c0, c1 = np.asarray(face_areas, dtype="f8"), np.asarray(c0), np.asarray(c1)
    require(areas.ndim == 2 and areas.shape[1] == 3 and len(areas) <= original.MAX_FACES
            and c0.shape == c1.shape == (len(areas),) and c0.dtype.kind in "iu" and c1.dtype.kind in "iu"
            and np.isfinite(areas).all() and np.all((c0 >= 0) & (c0 <= cell_count))
            and np.all((c1 >= 0) & (c1 <= cell_count)) and np.all(c0 != c1),
            "Complete finite face vectors and distinct in-range original adjacency IDs are required.")
    internal = np.flatnonzero((c0 > 0) & (c1 > 0))
    owners, neighbours = c0[internal].astype("i8") - 1, c1[internal].astype("i8") - 1
    vectors = areas[internal]
    magnitudes = np.linalg.norm(vectors, axis=1)
    require(np.isfinite(magnitudes).all() and np.all(magnitudes > 0), "Internal source face vectors must be nonzero.")
    degree = np.bincount(owners, minlength=cell_count) + np.bincount(neighbours, minlength=cell_count)
    sums = np.bincount(owners, weights=magnitudes, minlength=cell_count) + np.bincount(neighbours, weights=magnitudes, minlength=cell_count)
    average = np.zeros(cell_count, dtype="f8")
    active = degree > 0
    average[active] = sums[active] / degree[active]
    tensor = np.zeros((cell_count, 3, 3), dtype="f8")
    for cells in (owners, neighbours):
        scaled = vectors / average[cells, None]
        for a in range(3):
            for b in range(a, 3):
                values = np.bincount(cells, weights=scaled[:, a] * scaled[:, b], minlength=cell_count)
                tensor[:, a, b] += values
                if a != b:
                    tensor[:, b, a] += values
    determinant = np.abs(np.linalg.det(tensor))
    determinant[~active] = 0
    require(np.isfinite(determinant).all() and np.isfinite(tensor).all(), "Every replicated source cell metric must be finite.")
    return {"determinants": determinant, "internalFaceCounts": degree, "normalTensors": tensor,
            "averageIncludedFaceAreasNativeUnitsSquared": average}


def boundary_groups(model):
    """Actual source adjacency seeds; interface is distinct from physical walls."""
    import numpy as np
    count = model["cellCount"]
    groups = {name: np.zeros(count, dtype=bool) for name in ("anyBoundary", "wall", "interface", "inlet", "outlet")}
    contacts = {}
    for zone in model["faceZones"]:
        if zone["zoneType"] == 2:
            continue
        low, high = zone["minimumId"] - 1, zone["maximumId"]
        a, b = model["c0"][low:high], model["c1"][low:high]
        require(np.all((a == 0) ^ (b == 0)), "A source boundary requires exactly one original owning cell.")
        cells = np.unique(np.maximum(a, b).astype("i8") - 1)
        require(np.all((cells >= 0) & (cells < count)), "Boundary owning cells must remain in the original range.")
        contacts[zone["name"]] = cells
        groups["anyBoundary"][cells] = True
        if zone["zoneType"] == 3:
            groups["wall"][cells] = True
        elif zone["name"] in {"interface-impeller-outlet", "interface-volute-inlet"}:
            groups["interface"][cells] = True
        elif zone["name"] == "inlet":
            groups["inlet"][cells] = True
        elif zone["name"] == "mass-flow-inlet-11":
            groups["outlet"][cells] = True
        else:
            raise LocalizationIssue("Review a new original physical boundary's diagnostic role explicitly.")
    return groups, contacts


def adjacency_hops(c0, c1, cell_count, seeds, *, maximum_layers=MAX_HOPS):
    """Shortest internal-face graph distance; zero denotes a boundary owner.

This is a topological distance, not metres, nearest-wall geometry or yPlus.
Disconnected/unreached nodes remain -1 with an explicit exhausted/capped flag.
No periodic or NCC edge is invented across the independent source components.
"""
    import numpy as np
    c0, c1 = np.asarray(c0), np.asarray(c1)
    require(isinstance(cell_count, int) and not isinstance(cell_count, bool) and 0 < cell_count <= original.MAX_CELLS
            and c0.ndim == c1.ndim == 1 and c0.shape == c1.shape and len(c0) <= original.MAX_FACES
            and c0.dtype.kind in "iu" and c1.dtype.kind in "iu" and np.all((c0 >= 0) & (c0 <= cell_count))
            and np.all((c1 >= 0) & (c1 <= cell_count)) and np.all(c0 != c1),
            "Use complete bounded original adjacency rows without invalid cell IDs or self-neighbours.")
    require(isinstance(maximum_layers, int) and not isinstance(maximum_layers, bool) and 0 <= maximum_layers <= MAX_HOPS,
            "Use an explicit bounded topological diagnostic depth.")
    seeds = np.asarray(seeds)
    require(seeds.shape == (cell_count,) and seeds.dtype.kind == "b", "Use actual original boundary-owning seed cells.")
    internal = (c0 > 0) & (c1 > 0)
    a, b = c0[internal].astype("i8") - 1, c1[internal].astype("i8") - 1
    require(np.all((a >= 0) & (a < cell_count)) and np.all((b >= 0) & (b < cell_count)), "Original adjacency range changed.")
    distances = np.full(cell_count, -1, dtype="i4")
    distances[seeds] = 0
    frontier = seeds.copy()
    completed = False
    last = 0
    for layer in range(1, maximum_layers + 1):
        reached = frontier[a] | frontier[b]
        next_frontier = np.zeros(cell_count, dtype=bool)
        next_frontier[a[reached]] = True
        next_frontier[b[reached]] = True
        next_frontier &= distances < 0
        if not next_frontier.any():
            completed = True
            break
        distances[next_frontier] = layer
        frontier = next_frontier
        last = layer
    if not frontier.any() or np.all(distances >= 0):
        completed = True
    return {"distances": distances, "allReachableDistancesExhausted": completed,
            "maximumComputedLayer": last, "maximumAllowedLayer": maximum_layers, "hasSeedCells": bool(seeds.any())}


def cell_face_csr(model):
    import numpy as np
    rows = [np.flatnonzero(model[key] > 0) for key in ("c0", "c1")]
    cells = np.concatenate([model[key][row].astype("i8") - 1 for key, row in zip(("c0", "c1"), rows)])
    faces = np.concatenate(rows)
    order = np.lexsort((faces, cells))
    degree = np.bincount(cells, minlength=model["cellCount"])
    return faces[order], np.r_[0, np.cumsum(degree, dtype="i8")]


def histogram(values):
    return {str(key): count for key, count in sorted(Counter(map(int, values)).items())}


def diagnose_model(model):
    """Return all source threshold failures and bounded source localization."""
    import numpy as np
    metric = cell_determinants(polygon_areas(model), model["c0"], model["c1"], model["cellCount"])
    determinants = metric["determinants"]
    failed = np.flatnonzero(determinants < THRESHOLD)
    require(len(failed) <= MAX_FAILED, "The complete failed-cell localization exceeds its 100,000-cell bound; no partial result is promoted.")
    groups, contacts = boundary_groups(model)
    hops = {name: adjacency_hops(model["c0"], model["c1"], model["cellCount"], seeds) for name, seeds in groups.items()}
    direct = {name: np.isin(failed, cells, assume_unique=True) for name, cells in contacts.items()}
    faces, offsets = cell_face_csr(model)
    rows = []
    zone = model["topology"]["cells"]["zones"][0]
    for position, cell in enumerate(failed):
        incident = faces[offsets[cell]:offsets[cell + 1]]
        nodes = np.unique(np.concatenate([model["faceNodes"][model["faceOffsets"][face]:model["faceOffsets"][face + 1]] for face in incident]))
        xyz = model["coordinates"][nodes]
        eigenvalues = np.linalg.eigvalsh(metric["normalTensors"][cell])
        trace = float(eigenvalues.sum())
        distances = {}
        for name, hop in hops.items():
            value = int(hop["distances"][cell])
            distances[name] = value if value >= 0 else None
        rows.append({"sourceCellId": int(cell) + 1, "sourceCellZoneId": zone["zoneId"],
                     "sourceCellType": CELL_TYPES[int(model["cellTypes"][cell])],
                     "replicatedWellposednessDeterminant": float(determinants[cell]),
                     "includedInternalFaceCount": int(metric["internalFaceCounts"][cell]),
                     "totalOriginalFaceCount": int(len(incident)), "directBoundaryPatches": [name for name, hits in direct.items() if hits[position]],
                     "internalFaceAdjacencyHops": distances,
                     "diagnosticVertexMeanNativeCoordinates": xyz.mean(axis=0).tolist(),
                     "nativeCoordinateBounds": [xyz.min(axis=0).tolist(), xyz.max(axis=0).tolist()],
                     "includedFaceNormalTensorEigenvalues": eigenvalues.tolist(),
                     "leastEigenvalueToTrace": float(eigenvalues[0] / trace) if trace > 0 else None})
    distance_summary = {}
    for name, hop in hops.items():
        distance_summary[name] = {"failedCellHopHistogram": histogram(hop["distances"][failed]),
            **{key: value for key, value in hop.items() if key != "distances"},
            "negativeOneMeaning": "No source-graph path to this boundary category" if hop["allReachableDistancesExhausted"] else "Unresolved beyond the explicit diagnostic depth cap"}
    return {"component": model["component"], "sourceFile": model["sourceFile"],
            "sourceCellZone": {"zoneId": zone["zoneId"], "name": zone["name"]},
            "cellCount": model["cellCount"], "cellDeterminantSummary": {"minimum": float(determinants.min()),
                "mean": float(determinants.mean()), "threshold": THRESHOLD, "failedCellCount": len(failed),
                "comparison": "strict less than; equality passes", "includedInternalFaceCountHistogram": histogram(metric["internalFaceCounts"]),
                "failedCellTypeCounts": dict(sorted(Counter(row["sourceCellType"] for row in rows).items()))},
            "failedSourceCellIds": (failed + 1).tolist(), "failedCells": rows,
            "failedCellDirectBoundaryContactCounts": {name: int(hits.sum()) for name, hits in direct.items()},
            "boundaryAdjacencyDistanceSummary": distance_summary,
            "distanceDefinition": "Minimum number of original internal-face adjacency edges to an owning cell of the stated boundary category; boundary owner distance is zero. Separate original components have no invented NCC graph edge.",
            "coordinateDefinition": "Arithmetic mean and bounds of each cell's unique original vertex coordinates; not a native volume centroid, a wall-normal distance, metres or yPlus.",
            "nativeCoordinateUnitsVerified": False, "nativeFailedCellSetVerified": False,
            "nativeGeometryQualityVerified": False, "actualCfdRunVerified": False, "mainApplicationWrites": False}


def compare_native(report, native):
    """Promote ID equivalence only from a separately verified native cellSet."""
    require(isinstance(native, dict) and all(native.get(key) is True for key in
            ("nativeFailedCellSetVerified", "sourceCellOrderVerified", "originalBoundaryUncoupledMaskVerified")),
            "Native failed IDs need the actual exported cellSet, exact source cell order and coupled-face-mask proofs.")
    require(native.get("exampleDataCommit") == original.inventory.PIN and native.get("determinantThreshold") == THRESHOLD,
            "Compare only the same exact original source pin and unchanged native 0.001 metric.")
    components = native.get("components")
    require(isinstance(components, list) and [row.get("component") for row in components] == ["impeller", "volute"],
            "The verified native report must supply both ordered original components.")
    for source, actual in zip(report["components"], components):
        ids = actual.get("failedSourceCellIds")
        require(isinstance(ids, list) and all(isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= source["cellCount"] for value in ids)
                and ids == sorted(set(ids)), "Every native source-cell ID must be unique, sorted and in range.")
        require(ids == source["failedSourceCellIds"], "The replicated source and actual native failed-cell sets disagree; preserve both and investigate the face mask/arithmetic/mapping.")
    return {"exactFailedCellSetsMatch": True, "sourceCellOrderVerified": True, "originalBoundaryUncoupledMaskVerified": True,
            "nativeFailedCellSetVerified": True, "nativeGeometryQualityVerified": False,
            "claimBoundary": "Exact identity of the failed cells under the unchanged native gate; the original mesh still fails that gate."}


def localize(mesh_directory, *, native_quality_report=None):
    mesh_directory = Path(mesh_directory).absolute()
    require(mesh_directory.is_dir() and all(not item.is_symlink() and not getattr(item, "is_junction", lambda: False)()
            for item in (mesh_directory, *mesh_directory.parents)), "Use a regular independent source-mesh directory.")
    components = []
    for name in ("impeller", "volute"):
        model = original.read_mesh(mesh_directory / (name + ".msh.h5"), name)
        components.append(diagnose_model(model))
        del model
    for row in components:
        require(original.inventory.verify_file(mesh_directory / row["sourceFile"]["name"], row["sourceFile"]["name"]) == row["sourceFile"],
                "Immutable source bytes changed during read-only localization.")
    count = sum(row["cellCount"] for row in components)
    report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(),
        "exampleDataCommit": original.inventory.PIN, "openFoamSourceCommit": FOAM_COMMIT,
        "status": "SOURCE_DIAGNOSTIC_NATIVE_SET_EQUIVALENCE_PENDING", "determinantThreshold": THRESHOLD,
        "components": components, "combinedCellCount": count,
        "combinedFailedCellCount": sum(len(row["failedSourceCellIds"]) for row in components),
        "minimumReplicatedDeterminant": min(row["cellDeterminantSummary"]["minimum"] for row in components),
        "meanReplicatedDeterminant": sum(row["cellDeterminantSummary"]["mean"] * row["cellCount"] for row in components) / count,
        "equationStages": [{"stage": "Input", "definition": "Original float64 polygons/c0/c1, three geometric directions; ordinary boundaries excluded."},
            {"stage": "Equation", "definition": "Abar_c=sum(|Sf|)/nIncluded; T_c=sum((Sf/Abar_c) outer (Sf/Abar_c)); D_c=abs(det(T_c)); fail iff D_c<0.001; D_c=0 when no included faces."},
            {"stage": "Substitution", "definition": "Every source cell's original internal faces; polygon area equals the native edge-to-vertex-mean sum and triangle area the direct cross product. NumPy reductions may differ in last-bit summation order."},
            {"stage": "Result", "definition": "Computed source metric, all failed source IDs and source-graph boundary localization; native acceptance stays independent."}],
        "officialReferences": [PRIMARY + path for path in ("src/meshCheck/primitiveMeshCheck/primitiveMeshCheck.C",
            "src/meshCheck/polyMeshCheck/polyMeshCheck.C", "src/OpenFOAM/meshes/meshShapes/face/faceTemplates.C",
            "src/OpenFOAM/meshes/polyMesh/syncTools/syncTools.C", "src/meshCheck/checkGeometry.C")],
        "sourceMeshesImmutable": True, "sourceMeshModified": False, "thresholdModified": False,
        "nativeFaceMaskAssumption": "Every original source boundary remains uncoupled; generated NCC coupled patches have zero polyfaces. Verify this against the actual native boundary file before ID-equivalence qualification.",
        "nativeFailedCellSetVerified": False, "nativeGeometryQualityVerified": False, "solverExecutedByThisTool": False,
        "actualCfdRunVerified": False, "physicalDiagnosisValidated": False, "mainApplicationWrites": False,
        "claimBoundary": "Source metric and localization only. Boundary proximity and directional normal-tensor deficiency do not prove the physical cause or a valid remedy, turbulence wall resolution, head, NPSH3 or numerical convergence."}
    if native_quality_report is not None:
        raw, metadata = plain_file(native_quality_report)
        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                require(key not in result, "Duplicate native diagnostic JSON keys are prohibited.")
                result[key] = value
            return result
        native = json.loads(raw.decode("ascii"), object_pairs_hook=unique_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(LocalizationIssue("Nonfinite native diagnostic JSON is prohibited.")))
        report["nativeEquivalence"] = compare_native(report, native)
        report["nativeQualityReport"] = metadata
        report["nativeFailedCellSetVerified"] = True
        report["status"] = "ORIGINAL_FAILED_CELLS_LOCALIZED_NATIVE_SET_MATCHED"
    return report


def write_report(path, report):
    path = Path(path).absolute()
    original.inventory.output_path(path)
    raw = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    require(len(raw) <= MAX_REPORT, "The complete quality diagnostic exceeds its explicit 32 MiB output bound.")
    require(not path.exists() and not path.is_symlink(), "Write a fresh diagnostic report without replacing existing evidence.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    return {"name": path.name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesh-directory", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    parser.add_argument("--native-quality-report", type=Path)
    args = parser.parse_args()
    try:
        # Native h5py remains a standard public Ubuntu runner dependency; this
        # tool does not install it or bypass the Windows application policy.
        if sys.platform == "linux":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (original.MAX_MEMORY, original.MAX_MEMORY))
        original.inventory.output_path(args.output_report)
        report = localize(args.mesh_directory, native_quality_report=args.native_quality_report)
        metadata = write_report(args.output_report, report)
        print(json.dumps({"status": report["status"], "combinedFailedCellCount": report["combinedFailedCellCount"],
                          "report": metadata, "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 0
    except Exception as error:
        print(json.dumps({"status": "SOURCE_QUALITY_DIAGNOSTIC_CORRECTION_REQUIRED", "action": str(error)[:600],
                          "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
