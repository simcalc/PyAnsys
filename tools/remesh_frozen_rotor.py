"""Bounded classroom volume remeshing of two exact official mesh boundaries.

The source is read-only; native float64 coordinates/global node IDs and source
zone ownership are checked before Gmsh reparametrizes and generates a new mesh.
This produces meshing evidence only, never a Fluent case or CFD calculation.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_frozen_rotor_boundaries as boundary
import inspect_frozen_rotor_mesh as inventory

SCHEMA = "untirta.frozen-rotor-classroom-remesh.v1"
GMSH_VERSION = "4.15.2"
MAX_SECONDS = 300
MAX_MEMORY_BYTES = 2 * 1024**3
MAX_OUTPUT_BYTES = 25 * 1024**2
MAX_NEW_CELLS_PER_COMPONENT = 200_000
MAX_NEW_NODES_PER_COMPONENT = 150_000
MAX_CLASSIFIED_SURFACES = 512
SIZING_DIVISOR = 32
DISTANCE_SAMPLES_PER_ZONE = 64
REFERENCES = ["https://gmsh.info/doc/texinfo/gmsh.html#t13",
              "https://gmsh.info/doc/texinfo/gmsh.html#x2",
              "https://gmsh.info/doc/texinfo/gmsh.html#MSH-file-format-version-2-_0028Legacy_0029"]


class RemeshIssue(inventory.InspectionIssue):
    pass


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


def remesh_component(model, name, destination, gmsh, np, progress):
    component = name.split(".")[0]
    progress.update(component=component, stage="native-boundary-validation")
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
    progress["stage"] = "classification-and-zone-binding"
    gmsh.model.mesh.classifySurfaces(math.radians(40), True, True, math.pi)
    surface_rows = {tag: _triangles_for_surface(gmsh, tag) for dim, tag in gmsh.model.getEntities(2)}
    groups = classified_owners(surface_rows, owners)
    classified_tags, classified_xyz, _ = gmsh.model.mesh.getNodes()
    classified_points = {int(tag): tuple(float(value) for value in classified_xyz[i*3:i*3+3]) for i, tag in enumerate(classified_tags)}
    if len(classified_tags) != len(classified_points) or classified_points != native_points:
        raise RemeshIssue("Classification must preserve every exact float64 native coordinate and global node ID.")
    progress["stage"] = "discrete-parametrization"
    gmsh.model.mesh.createGeometry()
    loop = gmsh.model.geo.addSurfaceLoop(sorted(surface_rows))
    volume = gmsh.model.geo.addVolume([loop])
    gmsh.model.geo.synchronize()
    for zone in zones:
        gmsh.model.addPhysicalGroup(2, groups[zone["zoneId"]], zone["zoneId"])
        gmsh.model.setPhysicalName(2, zone["zoneId"], zone["zoneName"])
    gmsh.model.addPhysicalGroup(3, [volume], 1)
    gmsh.model.setPhysicalName(3, 1, "rotating-fluid" if component == "impeller" else "stationary-fluid")
    for option, value in {"Mesh.MeshSizeMin": size, "Mesh.MeshSizeMax": size, "Mesh.MeshSizeFromPoints": 0,
                          "Mesh.MeshSizeFromCurvature": 0, "Mesh.MeshSizeExtendFromBoundary": 0,
                          "Mesh.Algorithm": 6, "Mesh.Algorithm3D": 1, "Mesh.ElementOrder": 1,
                          "Mesh.MshFileVersion": 2.2, "Mesh.Binary": 0, "Mesh.SaveAll": 0}.items():
        gmsh.option.setNumber(option, value)
    progress["stage"] = "tetrahedral-remeshing"
    gmsh.model.mesh.generate(3)
    node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
    if not 4 <= len(node_tags) <= MAX_NEW_NODES_PER_COMPONENT or len(coordinates) != 3*len(node_tags):
        raise RemeshIssue("Generated node count exceeds the fixed classroom budget or has an invalid coordinate shape.")
    points = {int(tag): tuple(float(value) for value in coordinates[i*3:i*3+3]) for i, tag in enumerate(node_tags)}
    types, tag_sets, node_sets = gmsh.model.mesh.getElements(3, volume)
    if len(types) != 1 or int(types[0]) != 4 or len(node_sets[0]) != 4*len(tag_sets[0]):
        raise RemeshIssue("A nonempty linear tetrahedral volume mesh is required.")
    tags = [int(tag) for tag in tag_sets[0]]
    cells = [tuple(int(node) for node in node_sets[0][i:i+4]) for i in range(0, len(node_sets[0]), 4)]
    progress["stage"] = "quality-and-source-deviation"
    metrics, quality = tetra_metrics(cells, points), _quality(gmsh, tags)
    if len(cells) >= model["nativeCounts"]["cellCount"]:
        raise RemeshIssue("The classroom candidate must contain fewer volume cells than its native source component.")
    boundary_rows = []
    for zone in zones:
        triangles = [triangle for entity in groups[zone["zoneId"]] for triangle in _triangles_for_surface(gmsh, entity)]
        source_xyz = [[native_points[node] for node in triangle] for triangle in zone["triangles"]]
        identifiers = sorted({node for triangle in triangles for node in triangle})
        indices = sorted({round(i*(len(identifiers)-1)/(min(DISTANCE_SAMPLES_PER_ZONE, len(identifiers))-1)) for i in range(min(DISTANCE_SAMPLES_PER_ZONE, len(identifiers)))}) if len(identifiers)>1 else [0]
        distances = [nearest_triangle_distance(points[identifiers[index]], source_xyz, np) for index in indices]
        boundary_rows.append({"sourceZoneId": zone["zoneId"], "sourceZoneName": zone["zoneName"], "physicalGroupId": zone["zoneId"],
                              "classifiedEntityIds": groups[zone["zoneId"]], "sourceTriangleCount": len(zone["triangles"]),
                              "nativeTriangleOwnershipVerifiedBeforeRemesh": True,
                              "source": patch_metrics(zone["triangles"], native_points), "generated": patch_metrics(triangles, points),
                              "sampledBoundaryDeviation": {"sampleCount": len(distances), "maximumDistanceAssumedM": max(distances),
                                  "rootMeanSquareDistanceAssumedM": math.sqrt(math.fsum(value*value for value in distances)/len(distances)),
                                  "sampleRule": "Up to 64 sorted new boundary-node tags uniformly sampled; each exact nearest distance searches all same-zone native triangles.",
                                  "fullHausdorffDistanceVerified": False}})
    progress["stage"] = "msh2-publication"
    target = destination / (component + ".msh")
    if target.exists() or target.is_symlink():
        raise RemeshIssue("A generated mesh cannot overwrite an existing output.")
    gmsh.write(str(target))
    if not target.is_file() or target.is_symlink() or not 0 < target.stat().st_size <= MAX_OUTPUT_BYTES:
        raise RemeshIssue("A regular bounded generated MSH file is required.")
    with target.open("rb") as stream:
        if stream.read(32).splitlines()[:2] != [b"$MeshFormat", b"2.2 0 8"]:
            raise RemeshIssue("The converter input must use actual MSH2.2 ASCII storage.")
    digest = sha256(target.read_bytes()).hexdigest()
    result = {"sourceMesh": name, "generatedFile": {"name": target.name, "bytes": target.stat().st_size, "sha256": digest, "format": "gmsh-msh2.2-ascii"},
              "sourceCellCount": model["nativeCounts"]["cellCount"], "sourceNodeCount": model["nativeCounts"]["nodeCount"],
              "generatedCellCount": len(cells), "generatedNodeCount": len(points), "sourceBoundaryAudit": native_audit,
              "sourceBoundaryBoundsAssumedM": native_box, "generatedBoundsAssumedM": bounds(list(points.values())),
              "cellMetrics": metrics, "gmshQuality": quality, "boundaryZones": boundary_rows,
              "volumePhysicalGroup": "rotating-fluid" if component == "impeller" else "stationary-fluid",
              "equationStages": [{"input": {"sourceBoundaryBoundsNative": native_box, "sizingDivisor": SIZING_DIVISOR, "assumedMetresPerNativeUnit": 1},
                  "equation": "h_trial = max(xmax-xmin, ymax-ymin, zmax-zmin) / 32",
                  "substitution": {"maximumExtent": size*SIZING_DIVISOR, "divisor": SIZING_DIVISOR},
                  "result": {"value": size, "unit": "m under explicit classroom scale assumption"}},
                  {"input": {"actualOrderedTetrahedra": len(cells)}, "equation": "Vtet = dot(b-a,cross(c-a,d-a))/6; Vmesh = sum(Vtet)",
                   "substitution": metrics, "result": {"value": metrics["totalTetraVolumeAssumedM3"], "unit": "m3 under explicit classroom scale assumption"}}]}
    gmsh.model.remove()
    return result


def enforce_runtime_limits():
    if sys.platform != "linux":
        raise RemeshIssue("Execute native Gmsh only on the isolated standard GitHub Ubuntu worker.")
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "1"
    def expired(signum, frame):
        raise RemeshIssue("The bounded 300-second classroom meshing attempt timed out.")
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(MAX_SECONDS)


def run(value):
    destination = checked_output(value)
    started, progress = time.monotonic(), {"component": None, "stage": "runtime-preflight"}
    destination.mkdir(parents=True)
    report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(),
              "exampleDataCommit": inventory.PIN, "gmshRequiredVersion": GMSH_VERSION, "references": REFERENCES,
              "coordinateUnits": "UNVERIFIED", "classroomScaleAssumption": {"nativeUnitsToMetres": 1, "verifiedFromSource": False,
                  "label": "Explicit teaching assumption: one native coordinate unit equals one metre; physical interpretation requires unit verification."},
              "limits": {"threads": 1, "maximumSeconds": MAX_SECONDS, "maximumAddressSpaceBytes": MAX_MEMORY_BYTES,
                  "maximumOutputBytes": MAX_OUTPUT_BYTES, "maximumReportBytes": inventory.MAX_REPORT_BYTES,
                  "maximumCellsPerComponent": MAX_NEW_CELLS_PER_COMPONENT, "maximumNodesPerComponent": MAX_NEW_NODES_PER_COMPONENT},
              "sources": [], "meshes": [], "files": [], "status": "PREPARATION_REQUIRED", "volumeMeshGenerated": False,
              "originalMeshesImmutable": False, "nativeCoordinateUnitsVerified": False, "sourceGeometryFabricated": False,
              "nodeWeldingPerformed": False, "topologyHealingPerformed": False, "nativeCaseGenerated": False,
              "fluentSessionConnected": False, "licenseVerified": False, "actualCfdRunVerified": False,
              "simulationExecuted": False, "mainApplicationWrites": False, "openFoamConversionVerified": False,
              "claimBoundary": "Classroom remeshing only. Native units unverified; separate components and source patches preserved. OpenFOAM conversion, coupled interface, wall resolution, solver setup and CFD validation remain separate stages."}
    gmsh = None
    try:
        enforce_runtime_limits()
        import gmsh as native_gmsh
        import numpy as np
        gmsh = native_gmsh
        if gmsh.__version__ != GMSH_VERSION:
            raise RemeshIssue("Install exactly the pinned Gmsh version on the public worker.")
        gmsh.initialize([], readConfigFiles=False, run=False)
        gmsh.option.setNumber("General.Terminal", 0)
        for option in ("General.NumThreads", "Mesh.MaxNumThreads1D", "Mesh.MaxNumThreads2D", "Mesh.MaxNumThreads3D"):
            gmsh.option.setNumber(option, 1)
        report["gmshVersion"] = gmsh.__version__
        cache = destination.parent / "frozen-rotor-mesh-inputs"
        if cache.is_symlink() or getattr(cache, "is_junction", lambda: False)():
            raise RemeshIssue("Source cache must be a regular isolated runner directory.")
        cache.mkdir(exist_ok=True)
        for name in inventory.MESH_NAMES:
            progress.update(component=name.split(".")[0], stage="verified-native-source-read")
            source = inventory.download_mesh(cache, name)
            provenance = inventory.verify_file(source, name)
            model = boundary.read_native_mesh(source)
            report["sources"].append(provenance)
            report["meshes"].append(remesh_component(model, name, destination, gmsh, np, progress))
            inventory.verify_file(source, name)
        report["files"] = [mesh["generatedFile"] for mesh in report["meshes"]]
        if sum(row["bytes"] for row in report["files"]) > MAX_OUTPUT_BYTES-inventory.MAX_REPORT_BYTES:
            raise RemeshIssue("The combined meshes exceed the fixed 25 MiB publication budget.")
        if sum(mesh["generatedCellCount"] for mesh in report["meshes"]) >= sum(mesh["sourceCellCount"] for mesh in report["meshes"]):
            raise RemeshIssue("The combined generated classroom mesh must be lighter than the source mesh.")
        report.update(status="VOLUME_MESH_GENERATED", volumeMeshGenerated=True, originalMeshesImmutable=True)
        progress["stage"] = "completed"
    except Exception as error:
        report.update(status="PREPARATION_REQUIRED", volumeMeshGenerated=False, reasonCode="CFD_CLASSROOM_REMESH_REQUIRED",
                      diagnostic={"stage": progress["stage"], "component": progress["component"], "type": type(error).__name__, "message": str(error)[:500]},
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
            gmsh.finalize()
    report["elapsedSeconds"] = round(time.monotonic()-started, 3)
    for env, key, pattern in (("GITHUB_RUN_ID", "githubRunId", r"[0-9]{1,24}"), ("GITHUB_SHA", "githubSourceCommit", r"[0-9a-f]{40}")):
        raw = os.environ.get(env, "")
        report[key] = raw if re.fullmatch(pattern, raw) else None
    raw = inventory.bounded_json(report)
    with (destination / "remesh-report.json").open("xb") as stream:
        stream.write(raw)
    return {"status": report["status"], "stage": progress["stage"], "volumeMeshGenerated": report["volumeMeshGenerated"],
            "reportSha256": sha256(raw).hexdigest(), "reportBytes": len(raw), "files": report["files"],
            "totalGeneratedCells": sum(mesh["generatedCellCount"] for mesh in report["meshes"]),
            "actualCfdRunVerified": False, "mainApplicationWrites": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = run(arguments.output_dir)
        print(json.dumps(result, allow_nan=False))
        return 0 if result["volumeMeshGenerated"] else 2
    except (inventory.InspectionIssue, OSError, ValueError) as error:
        print(json.dumps({"status": "PREPARATION_REQUIRED", "reasonCode": "CFD_CLASSROOM_REMESH_REQUIRED", "action": str(error)[:500],
                          "volumeMeshGenerated": False, "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
