"""Verify original-volume source/native failed-cell identity, never mesh acceptance.

Only the fixed original Foundation-12 merge/NCC/checkMesh route is admitted.
All native polygons, coordinates and cell adjacency are compared to the pinned
converted inputs; zone-list iteration order never determines source cell IDs.
No mesh, threshold, field or source input is changed and no solver is launched.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_original_case as preparation
import frozen_rotor_native_quality as quality
import frozen_rotor_native_reduction as native
import localize_frozen_rotor_quality as localization

SCHEMA = "untirta.frozen-rotor-original-quality-equivalence.v1"
EXECUTION_SCHEMA = "untirta.frozen-rotor-original-openfoam-execution.v1"
FOAM_COMMIT = "86e126a7bc4dbfaed949bbc58c9f0fdd67acc059"
THRESHOLD = .001
MAX_REPORT = 32 * 1024**2
MAX_FILE = 128 * 1024**2
MAX_LOG = 4 * 1024**2
MAX_OUTPUT = 2 * 1024**2
SOURCE_ORDER_REFERENCES = ["https://github.com/OpenFOAM/OpenFOAM-12/blob/" + FOAM_COMMIT + "/" + path for path in (
    "applications/utilities/mesh/manipulation/mergeMeshes/mergeMeshes.C",
    "applications/utilities/mesh/manipulation/mergeMeshes/mergePolyMesh.C",
    "src/polyTopoChange/polyTopoChange/polyTopoChange.C",
    "src/polyTopoChange/polyTopoChange/polyTopoChange.H",
    "applications/utilities/mesh/manipulation/createNonConformalCouples/createNonConformalCouples.C",
    "src/meshCheck/polyMeshCheck/polyMeshCheck.C",
    "src/meshCheck/primitiveMeshCheck/primitiveMeshCheck.C")]


class EquivalenceIssue(ValueError):
    pass


def require(condition, action):
    if not condition:
        raise EquivalenceIssue(action)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def decoded(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate diagnostic JSON keys are prohibited.")
            result[key] = value
        return result
    value = json.loads(raw.decode("ascii"), object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(EquivalenceIssue("Finite diagnostic JSON is required.")))
    require(isinstance(value, dict), "An exact diagnostic JSON object is required.")
    return value


class Evidence:
    """Current bounded reads plus a final rehash of every consumed file."""
    def __init__(self, runner):
        self.runner = runner
        self.records = {}

    def read(self, path, limit=None):
        limit = MAX_FILE if limit is None else limit
        raw, row = quality.read(path, self.runner, limit)
        old = self.records.get(row["path"])
        require(old is None or old == (row, limit), "Current evidence bytes changed between diagnostic reads.")
        self.records[row["path"]] = (row, limit)
        return raw, row

    def json(self, path, limit=MAX_REPORT):
        raw, row = self.read(path, limit)
        return decoded(raw), row

    def recheck(self):
        for relative, (row, limit) in self.records.items():
            require(quality.read(self.runner / relative, self.runner, limit)[1] == row,
                    "Current source/native diagnostic evidence changed before correspondence persistence.")


def counted(raw, name, classes):
    body = quality.native_body(raw, name, classes)
    match = re.fullmatch(r"(\d+)\s*\((.*)\)\s*;?", body, re.S)
    require(match, "Complete plain native counted storage is required: " + name)
    return int(match[1]), match[2]


def integer_list(raw, name, count, maximum):
    import numpy as np
    size, body = counted(raw, name, {"labelList"})
    # A single invalid-character scan stays linear even on a large malformed
    # list. Nested digit/whitespace repetition could backtrack at its tail.
    require(size == count and not re.search(r"[^0-9\s]", body), "Every actual native label must be complete and nonnegative.")
    values = np.fromstring(body, dtype="i8", sep=" ")
    require(len(values) == count and (len(values) == 0 or bool(((values >= 0) & (values < maximum)).all())),
            "Native label count or cell range differs from the original domain.")
    return values


def points(raw, count):
    import numpy as np
    size, body = counted(raw, "points", {"vectorField"})
    require(size == count and 0 < count <= preparation.MAX_COMBINED_NODES, "Exact original combined point count is required.")
    result, end, index = np.empty((count, 3), dtype="f8"), 0, 0
    for match in re.finditer(r"\(([^()]*)\)", body):
        require(not body[end:match.start()].strip() and index < count, "Every native point row must be accounted for.")
        tokens = match[1].split()
        require(len(tokens) == 3 and all(re.fullmatch(quality.FLOAT, token) for token in tokens), "Plain finite native three-vectors are required.")
        result[index] = [float(token) for token in tokens]
        index += 1; end = match.end()
    require(index == count and not body[end:].strip() and bool(np.isfinite(result).all()), "Complete finite native point coordinates are required.")
    return result


def coordinate_ids(source_points, merged_points):
    """Exact coordinate classes preserve duplicates; no tolerance or welding."""
    import numpy as np
    require(source_points.shape == merged_points.shape, "Original and native coordinate inventories must agree.")
    row_type = np.dtype([("x", "f8"), ("y", "f8"), ("z", "f8")])
    source = np.ascontiguousarray(source_points).view(row_type).reshape(-1)
    current = np.ascontiguousarray(merged_points).view(row_type).reshape(-1)
    unique, inverse, counts = np.unique(source, return_inverse=True, return_counts=True)
    indices = np.searchsorted(unique, current)
    require(bool((indices < len(unique)).all()) and bool((unique[indices] == current).all())
            and bool(np.array_equal(np.bincount(indices, minlength=len(unique)), counts)),
            "Actual native coordinate values/multiplicities changed; exact geometry correspondence is required.")
    return inverse, indices


def face_digests(raw, owner, neighbour, point_ids, *, cell_offset=0):
    """Complete oriented polygons+cell roles; cyclic vertex rotations allowed."""
    import numpy as np
    count, body = counted(raw, "faces", {"faceList"})
    require(count == len(owner), "Every actual polygon must bind its owner exactly once.")
    result, index, end = np.empty(count, dtype="V32"), 0, 0
    for match in re.finditer(r"(\d+)\s*\(([^()]*)\)", body):
        require(index < count and not body[end:match.start()].strip(), "Every actual polygon row must be accounted for.")
        width = int(match[1]); tokens = match[2].split()
        require(3 <= width <= 128 and len(tokens) == width and all(re.fullmatch(r"\d+", value) for value in tokens), "Every polygon requires a complete bounded integer connectivity row.")
        ids = [int(value) for value in tokens]
        require(len(set(ids)) == width and max(ids) < len(point_ids), "Native polygon point references must be distinct and inside the exact coordinate inventory.")
        canonical = [int(point_ids[value]) for value in ids]
        minimum = min(canonical)
        candidates = [tuple(canonical[i:] + canonical[:i]) for i, value in enumerate(canonical) if value == minimum]
        vertices = min(candidates)
        own = int(owner[index]) + cell_offset
        nei = int(neighbour[index]) + cell_offset if index < len(neighbour) else -1
        result[index] = sha256(struct.pack("<qqI", own, nei, width) + struct.pack("<" + "Q" * width, *vertices)).digest()
        index += 1; end = match.end()
    require(index == count and not body[end:].strip(), "Every actual original/native polygon must be compared without omitted payload.")
    return result


def boundary(raw, expected, internal_count, face_count, owner, zones):
    size, body = counted(raw, "boundary", {"polyBoundaryMesh"})
    rotor, stator = "interface-impeller-outlet", "interface-volute-inlet"
    coupled = {"pumpInterface_on_" + rotor: (rotor, "pumpInterface_on_" + stator),
               "pumpInterface_on_" + stator: (stator, "pumpInterface_on_" + rotor)}
    errors = {"nonConformalError_on_" + original: original for original in (rotor, stator)}
    result, start, end = {}, internal_count, 0
    for match in re.finditer(r'"?([A-Za-z0-9_-]+)"?\s*\{([^{}]*)\}', body):
        require(not body[end:match.start()].strip(), "Every native boundary dictionary must be accounted for.")
        name, block = match[1], match[2]
        def value(key, grammar):
            rows = re.findall(r"\b" + key + r"\s+(" + grammar + r")\s*;", block)
            require(len(rows) == 1, "Every native boundary role must have one exact " + key + ".")
            return rows[0]
        kind = value("type", r"\w+"); count = int(value("nFaces", r"\d+")); first = int(value("startFace", r"\d+"))
        require(name not in result and first == start, "Actual native boundary ranges must be unique and contiguous.")
        if name in expected:
            source, zone = expected[name]
            require(kind == source["type"] and count == source["nFaces"], "Original ordinary wall/patch types and face counts must remain exact.")
            require(all(int(cell) in zones[zone] for cell in owner[first:first + count]), "Source patch owners must remain in their exact original cell zone.")
        elif name in coupled:
            original, opposite = coupled[name]
            require(kind == "nonConformalCyclic" and count == 0 and value("originalPatch", r"[\w-]+") == original
                    and value("neighbourPatch", r"[\w-]+") == opposite and value("transformType", r"\w+") == "none",
                    "Exact zero-polyface reciprocal NCC patches are required; positive coupled faces change the determinant mask.")
        elif name in errors:
            require(kind == "nonConformalError" and count == 0 and value("originalPatch", r"[\w-]+") == errors[name],
                    "Exact zero-polyface NCC error placeholders are required.")
        else:
            raise EquivalenceIssue("Unexpected native boundary roles are prohibited in the original-only correspondence route.")
        result[name] = {"type": kind, "nFaces": count, "startFace": first}
        start += count; end = match.end()
    require(not body[end:].strip() and len(result) == size and set(result) == set(expected) | set(coupled) | set(errors)
            and start == face_count, "All eleven original boundaries and four zero-face native NCC/error roles must be complete.")
    return result


def source_packet(record, conversion):
    require(record.get("schemaVersion") == localization.SCHEMA and record.get("exampleDataCommit") == preparation.base.inventory.PIN
            and record.get("openFoamSourceCommit") in {localization.FOAM_COMMIT, FOAM_COMMIT}
            and record.get("determinantThreshold") == THRESHOLD,
            "Source localization must bind the exact source pin, reviewed formula and unchanged 0.001 threshold.")
    require(record.get("sourceMeshesImmutable") is True and all(record.get(key) is False for key in
            ("sourceMeshModified", "thresholdModified", "solverExecutedByThisTool", "actualCfdRunVerified", "nativeGeometryQualityVerified", "mainApplicationWrites")),
            "Source localization cannot claim mesh modification, native quality or CFD execution.")
    components = record.get("components")
    require(isinstance(components, list) and [row.get("component") for row in components] == ["impeller", "volute"], "Two ordered complete source-localization components are required.")
    for row, converted in zip(components, conversion["components"]):
        require(row.get("sourceFile") == converted["sourceFile"] and row.get("cellCount") == converted["cellCount"], "Source diagnostic component identity/cell count differs from the immutable conversion.")
        ids, detail, summary = row.get("failedSourceCellIds"), row.get("failedCells"), row.get("cellDeterminantSummary", {})
        require(isinstance(ids, list) and len(ids) <= localization.MAX_FAILED and all(isinstance(i, int) and not isinstance(i, bool)
                and 1 <= i <= row["cellCount"] for i in ids) and ids == sorted(set(ids)), "Complete sorted unique original one-based failed-cell IDs are required.")
        require(isinstance(detail, list) and [item.get("sourceCellId") for item in detail] == ids
                and summary.get("threshold") == THRESHOLD and summary.get("failedCellCount") == len(ids), "Every failed source-cell ID must bind a complete unchanged-threshold metric row.")
        require(all(finite(item.get("replicatedWellposednessDeterminant")) and 0 <= item["replicatedWellposednessDeterminant"] < THRESHOLD for item in detail)
                and finite(summary.get("minimum")) and finite(summary.get("mean")) and 0 <= summary["minimum"] <= summary["mean"], "Every source determinant row and summary must remain finite within its honest failure domain.")
    require(record.get("combinedCellCount") == sum(row["cellCount"] for row in components)
            and record.get("combinedFailedCellCount") == sum(len(row["failedSourceCellIds"]) for row in components), "Complete source cell/failure counts must bind exactly.")
    require(finite(record.get("minimumReplicatedDeterminant")) and finite(record.get("meanReplicatedDeterminant")), "Finite source aggregate metric values are required.")
    expected_minimum = min(row["cellDeterminantSummary"]["minimum"] for row in components)
    expected_mean = sum(row["cellDeterminantSummary"]["mean"] * row["cellCount"] for row in components) / record["combinedCellCount"]
    require(record["minimumReplicatedDeterminant"] == expected_minimum and math.isclose(record["meanReplicatedDeterminant"], expected_mean, rel_tol=1e-14, abs_tol=1e-15),
            "Source aggregate metrics must remain consistent with both complete component summaries.")
    return components


def stages(execution, prepared, runtime, root, evidence):
    require(execution.get("schemaVersion") == EXECUTION_SCHEMA and execution.get("exampleDataCommit") == preparation.base.inventory.PIN
            and execution.get("status") == "ORIGINAL_NATIVE_STAGE_CORRECTION_REQUIRED" and execution.get("diagnostic", {}).get("program") == "checkMesh",
            "Only a fresh original-volume run stopped at the unchanged failed checkMesh admission is supported.")
    require(all(execution.get(key) is False for key in ("actualCfdRunVerified", "qualifiedBaseline", "nativeSolverCompleted", "simulationExecuted", "nativeGeometryQualityVerified", "mainApplicationWrites")),
            "A failed original mesh cannot carry a solver, baseline, quality or Main-write claim.")
    expected = preparation.commands(root)
    require(prepared.get("nativeCommandArguments") == expected, "The exact master/add utility arguments and unmodified allGeometry/allTopology/writeSets checks are required.")
    rows = execution.get("stages")
    require(isinstance(rows, list) and len(rows) == 3 and [row.get("program") for row in rows] == [row[0] for row in expected[:3]], "Exactly the fixed three native mesh stages, with no solver or renumber stage, are required.")
    binaries = {row["program"]: row for row in runtime["binaries"]}
    logs = {}
    for index, (row, arguments) in enumerate(zip(rows, expected)):
        name = f"{index:02d}-{arguments[0]}.log"
        require(row.get("log") == name and row.get("exitCode") == 0 and finite(row.get("elapsedSeconds")) and row["elapsedSeconds"] >= 0,
                "Each native mesh utility must complete with its fixed log path and actual zero exit.")
        raw, metadata = evidence.read(root / "logs" / name, MAX_LOG)
        require(row.get("logBytes") == metadata["bytes"] and row.get("logSha256") == metadata["sha256"], "Actual native log bytes differ from the immutable execution record.")
        text = raw.decode("utf-8", errors="strict")
        require(re.search(r"(?m)^Build\s*:\s*12-86e126a7bc4d\s*$", text) and re.search(r"(?m)^nProcs\s*:\s*1\s*$", text)
                and re.search(r"(?m)^End\s*$", text) and not re.search(r"FOAM FATAL|Segmentation fault|Floating point exception", text, re.I), "Only the reviewed actual serial Foundation-12 build and completed nonfatal native utilities are supported.")
        actual_exec = re.findall(r"(?m)^Exec\s*:\s*(.*?)\s*$", text)
        actual_case = re.findall(r"(?m)^Case\s*:\s*(.*?)\s*$", text)
        require(actual_exec == [" ".join([binaries[arguments[0]]["path"], *arguments[1:]])]
                and actual_case == [str(root / "impeller-case")], "Actual utility log arguments/case must prove exact original master/add order, without cell reordering/removal commands.")
        logs[arguments[0]] = raw
    volute = str(root / "volute-case")
    merge = logs["mergeMeshes"].decode("utf-8")
    require(re.findall(r'Create polyMesh for case "([^"]+)"', merge) == [volute]
            and re.findall(r'Adding mesh "([^"]+)"', merge) == [volute + "/0/region0"]
            and "Merging all meshes" in merge, "Actual merge logging must prove one ascending appended original volute component after the impeller master.")
    require(not (root / "logs/03-foamRun.log").exists() and not (root / "impeller-case/500").exists(), "This diagnostic must run before any native solver endpoint.")
    return logs


def verify(execution_directory, converted_mesh_directory, source_localization_report):
    import numpy as np
    root = preparation.safety.inside_runner(execution_directory)
    converted = preparation.safety.inside_runner(converted_mesh_directory)
    require(root != converted and not root.is_relative_to(converted) and not converted.is_relative_to(root), "Independent immutable converted and working execution directories are required.")
    runner = preparation.base.regular_directory(os.environ.get("RUNNER_TEMP", ""))
    evidence = Evidence(runner)
    verified = preparation.validate_inputs(converted)
    conversion, conversion_row = evidence.json(converted / "native-conversion.json")
    require(conversion == verified["report"] and conversion_row["sha256"] == verified["reportFile"]["sha256"],
            "Current original conversion bytes must match the independently validated report snapshot.")
    copied, copied_row = evidence.json(root / "native-conversion.json")
    require(copied == conversion and copied_row["sha256"] == conversion_row["sha256"], "The working original conversion report must retain exact independently verified source bytes.")
    prepared, preparation_row = evidence.json(root / "openfoam-preparation.json")
    execution, execution_row = evidence.json(root / "openfoam-execution.json")
    runtime, runtime_row = evidence.json(root / "runtime-identity.json")
    source, source_row = evidence.json(source_localization_report)
    require(prepared.get("schemaVersion") == preparation.SCHEMA and prepared.get("exampleDataCommit") == preparation.base.inventory.PIN
            and prepared.get("nativeConversionReportSha256") == conversion_row["sha256"] and prepared.get("originalMeshesImmutable") is True
            and all(prepared.get(key) is False for key in ("commandExecutionPerformedByThisTool", "nativeGeometryQualityVerified", "actualCfdRunVerified", "simulationExecuted", "mainApplicationWrites")), "The exact original preparation with honest pre-execution scope is required.")
    require(execution.get("preparationReportSha256") == preparation_row["sha256"]
            and execution.get("nativeConversionReportSha256") == conversion_row["sha256"]
            and execution.get("runtimeIdentitySha256") == runtime_row["sha256"], "Native execution must bind the current preparation, original conversion and installed runtime bytes.")
    native.binary_identity(runtime)
    native_logs = stages(execution, prepared, runtime, root, evidence)
    sources = source_packet(source, conversion)
    expected_components = [{key: row[key] for key in ("component", "caseDirectory", "cellCount", "nodeCount", "faceCount", "patchFaces", "cellZone")}
                           for row in verified["components"].values()]
    require(prepared.get("components") == expected_components, "Preparation component counts/roles must retain the exact original native input, not any derived/remeshed route.")
    source_rows = [row for component in verified["components"].values() for row in [*component["nativeFiles"], component["sourceFaceIdMapFile"]]]
    require(prepared.get("sourceComponentFiles") == source_rows, "Every original source component file/map must be bound exactly by preparation.")
    expected_authored = []
    pressure = prepared.get("classroomAssumptions", {}).get("externalTotalPressureAbsolutePa")
    for name in ("impeller", "volute"):
        for relative, content in preparation.case_files(name, pressure).items():
            raw = content.encode("ascii"); path = name + "-case/" + relative
            expected_authored.append({"path": path, "bytes": len(raw), "sha256": sha256(raw).hexdigest()})
            # Native NCC rewrites master 0/* only; all other authored inputs must
            # still have their exact independently regenerated recipe bytes.
            if name == "volute" or not relative.startswith("0/"):
                current, _ = evidence.read(root / path)
                require(current == raw, "An actual authored control outside the native master-field rewrite changed.")
    require(prepared.get("authoredInputFiles") == expected_authored, "Every pre-execution authored input must match the fixed reviewed original recipe.")
    source_data = {}
    for name, component in verified["components"].items():
        records = {}
        for row in [*component["nativeFiles"], component["sourceFaceIdMapFile"]]:
            raw, current = evidence.read(converted / row["path"])
            require({key: current[key] for key in ("bytes", "sha256")} == {key: row[key] for key in ("bytes", "sha256")}, "Immutable original converted source bytes changed.")
            records[Path(row["path"]).name] = raw
            if name == "volute" or row is component["sourceFaceIdMapFile"]:
                working, _ = evidence.read(root / row["path"])
                require(working == raw, "Unchanged appended original mesh or source-face map differs from its independent input.")
        source_data[name] = records
    merged = {name: evidence.read(root / "impeller-case/constant/polyMesh" / name)[0] for name in preparation.MESH_NAMES}
    total_cells = sum(row["cellCount"] for row in verified["components"].values())
    total_points = sum(row["nodeCount"] for row in verified["components"].values())
    total_faces = sum(row["faceCount"] for row in verified["components"].values())
    metrics = quality.log_metrics(native_logs["checkMesh"])
    require(metrics["mergedCellCount"] == total_cells, "Actual checkMesh cell count must equal the exact original combined domain.")
    zones = quality.zones(merged["cellZones"], total_cells)
    first = verified["components"]["impeller"]["cellCount"]
    require(zones["rotating-fluid"] == set(range(first)) and zones["stationary-fluid"] == set(range(first, total_cells)),
            "Actual complete cell-zone membership must prove original master/appended cell ranges; never infer source order from cellZoneList row order.")
    internal = sum(row["internalFaceCount"] for row in conversion["components"])
    owner = integer_list(merged["owner"], "owner", total_faces, total_cells)
    neighbour = integer_list(merged["neighbour"], "neighbour", internal, total_cells)
    require(bool((owner[:internal] < neighbour).all()) and bool(np.array_equal(np.unique(np.r_[owner, neighbour]), np.arange(total_cells))), "Every original merged cell must retain valid ordered internal adjacency.")
    expected_patches = {patch["name"]: (patch, "rotating-fluid" if row["name"] == "impeller" else "stationary-fluid")
                        for row in conversion["components"] for patch in row["patches"]}
    boundaries = boundary(merged["boundary"], expected_patches, internal, total_faces, owner, zones)
    source_xyz = np.concatenate([points(source_data[name]["points"], verified["components"][name]["nodeCount"]) for name in ("impeller", "volute")])
    source_point_ids, native_point_ids = coordinate_ids(source_xyz, points(merged["points"], total_points))
    expected_digests, node_offset, cell_offset = [], 0, 0
    for row in conversion["components"]:
        name, data = row["name"], source_data[row["name"]]
        local_owner = integer_list(data["owner"], "owner", row["faceCount"], row["cellCount"])
        local_neighbour = integer_list(data["neighbour"], "neighbour", row["internalFaceCount"], row["cellCount"])
        expected_digests.append(face_digests(data["faces"], local_owner, local_neighbour,
            source_point_ids[node_offset:node_offset + row["nodeCount"]], cell_offset=cell_offset))
        for patch in row["patches"]:
            current = boundaries[patch["name"]]
            expected_owners = local_owner[patch["startFace"]:patch["startFace"] + patch["nFaces"]] + cell_offset
            actual_owners = owner[current["startFace"]:current["startFace"] + current["nFaces"]]
            require(bool(np.array_equal(np.sort(expected_owners), np.sort(actual_owners))), "Every actual source boundary must retain exact original cell ownership after merging.")
        node_offset += row["nodeCount"]; cell_offset += row["cellCount"]
    expected_digests = np.concatenate(expected_digests); expected_digests.sort()
    actual_digests = face_digests(merged["faces"], owner, neighbour, native_point_ids); actual_digests.sort()
    require(bool(np.array_equal(expected_digests, actual_digests)), "Every actual oriented polygon, exact coordinate class and original owner/neighbour cell identity must match the source; unproven cell permutations or geometry changes are rejected.")
    set_raw, _ = evidence.read(root / "impeller-case/constant/polyMesh/sets/underdeterminedCells")
    ids = quality.labels(quality.native_body(set_raw, "underdeterminedCells", {"cellSet"}), total_cells, expected=metrics["underdeterminedCellCount"])
    failed = [sorted(value + 1 for value in ids & zones["rotating-fluid"]), sorted(value - first + 1 for value in ids & zones["stationary-fluid"])]
    require(all(row["failedSourceCellIds"] == values for row, values in zip(sources, failed)), "Complete independently computed source and actual native failed-cell IDs disagree; do not promote correspondence.")
    require(sum(map(len, failed)) == source["combinedFailedCellCount"], "Every native failed cell must bind one original source ID exactly once.")
    # The formulas are identical in the two explicitly reviewed Foundation-12
    # source commits; normal floating-point reduction order differs in last bits.
    require(math.isclose(metrics["minimumCellDeterminant"], source["minimumReplicatedDeterminant"], rel_tol=1e-10, abs_tol=1e-14)
            and math.isclose(metrics["averageCellDeterminant"], source["meanReplicatedDeterminant"], rel_tol=1e-10, abs_tol=1e-14), "Aggregate actual/default source metric differs beyond the declared arithmetic comparison tolerance.")
    evidence.recheck()
    native.binary_identity(runtime)
    require(preparation.validate_inputs(converted)["reportFile"] == verified["reportFile"], "Independent immutable conversion input changed before final correspondence persistence.")
    records = [row for row, _ in evidence.records.values()]
    records.sort(key=lambda row: row["path"])
    return {"schemaVersion": SCHEMA, "status": "ORIGINAL_FAILED_CELL_SOURCE_NATIVE_EQUIVALENCE_VERIFIED",
        "exampleDataCommit": preparation.base.inventory.PIN, "openFoamNativeBuildSourceCommit": FOAM_COMMIT,
        "sourceLocalizationFormulaCommit": source["openFoamSourceCommit"], "determinantThreshold": THRESHOLD,
        "combinedCellCount": total_cells, "combinedFailedCellCount": len(ids), "nativeFailedCellSetVerified": True,
        "sourceCellOrderVerified": True, "originalBoundaryUncoupledMaskVerified": True, "exactFailedCellSetsMatch": True,
        "allOriginalOrientedPolygonCellBindingsVerified": True, "allCoordinatesExactMultisetVerified": True,
        "currentEvidenceByteBindingVerified": True, "nativeSourceQualityCorrespondenceVerified": True,
        "components": [{"component": row["component"], "cellCount": row["cellCount"], "failedSourceCellIds": values,
                        "nativeMergedCellOffset": 0 if i == 0 else first, "nativeFailedCellCount": len(values)}
                       for i, (row, values) in enumerate(zip(sources, failed))],
        "sourceLocalizationReport": source_row, "nativeExecutionReport": execution_row,
        "nativeBoundaryRoles": boundaries, "nativeMetrics": metrics,
        "metricComparisonTolerance": {"relative": 1e-10, "absolute": 1e-14, "purpose": "Aggregate arithmetic check only; all failed-cell IDs match exactly, with unchanged native threshold."},
        "sourceOrderProof": {"method": "Known fixed native ascending master/add cell append without removal or orderCells, complete exact contiguous native zone membership, and full oriented coordinate/polygon/owner-neighbour multiset comparison.",
                            "zoneRowOrderUsed": False, "sourceIdConvention": "Original one-based component cell ID; master native i maps to i+1 and appended native i maps to i-nMaster+1."},
        "nativeEvidenceRecords": records, "sourceMeshesImmutable": True, "meshModified": False,
        "acceptanceThresholdChanged": False, "nativeQualityAccepted": False, "nativeGeometryQualityVerified": False,
        "qualifiedBaseline": False, "solverExecutedByThisTool": False, "simulationExecuted": False,
        "actualCfdRunVerified": False, "mainApplicationWrites": False,
        "claimBoundary": "Exact current original-source/native identity of failed cells only. The mesh still fails the unchanged native quality gate; no CFD, head, NPSH3, remedy or convergence is qualified.",
        "references": SOURCE_ORDER_REFERENCES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-directory", type=Path, required=True)
    parser.add_argument("--converted-mesh-directory", type=Path, required=True)
    parser.add_argument("--source-localization-report", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    args = parser.parse_args()
    try:
        if sys.platform == "linux":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
        preparation.base.inventory.output_path(args.output_report)
        result = verify(args.execution_directory, args.converted_mesh_directory, args.source_localization_report)
        raw = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
        require(len(raw) <= MAX_OUTPUT, "The bounded original correspondence report exceeds 2 MiB; no truncated identity proof is emitted.")
        preparation.base.write_exclusive(args.output_report, raw)
        print(json.dumps({key: result[key] for key in ("status", "nativeSourceQualityCorrespondenceVerified", "nativeQualityAccepted", "actualCfdRunVerified", "mainApplicationWrites")}))
        return 0
    except Exception as error:
        print(json.dumps({"status": "ORIGINAL_SOURCE_NATIVE_CORRESPONDENCE_CORRECTION_REQUIRED", "action": str(error)[:600],
                          "nativeSourceQualityCorrespondenceVerified": False, "nativeQualityAccepted": False,
                          "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
