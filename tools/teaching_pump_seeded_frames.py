"""Read-only seeded clock and global-coordinate facts; no restart admission.

The producer must separately establish source/run and installed-runtime identity.
This reader does not execute native tools, compare restarted solutions or supply
the consecutive native Sf/meshPhi data needed for an ALE or wall-flux proof.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import teaching_pump_restart_clock as clock
import teaching_pump_motion_evidence as motion

INPUT_SCHEMA = "untirta.teaching-pump-seeded-frames-input.v1"
SCHEMA = "untirta.teaching-pump-seeded-frame-facts.v1"
MESH_FILES = ("points", "faces", "owner", "neighbour", "boundary", "cellZones")
MAX_FRAMES, MAX_RECORDS = 16, 512
MAX_POINTS, MAX_CELLS, MAX_FACES = 1000000, 500000, 2000000
FALSE_FLAGS = tuple(sorted(set(clock.FALSE_FLAGS) | {
    "coordinateMotionVerified", "physicalWallVelocityEvidenceVerified",
    "dynamicNccClockCoverageVerified", "eulerGclEvidenceVerified",
    "relativeFluxEvidenceVerified", "fieldValuesSemanticsVerified",
    "installedRuntimeIdentityVerified", "producerNativeExecutionProvenanceVerified",
    "matchedContinuousAndResumedFieldsVerified"}))
require, encoded = clock.require, clock.encoded


def bind(root, row, records, *, limit=clock.MAX_FILE):
    raw = clock.read_bound(root, row, limit=limit)
    name = row["path"]
    require(name not in records or records[name] == row,
            "SEEDED_FRAMES_CURRENT_BYTE_BINDING_REQUIRED")
    if name not in records:
        require(len(records) < MAX_RECORDS
                and sum(r["bytes"] for r in records.values()) + row["bytes"] <= clock.MAX_TOTAL,
                "SEEDED_FRAMES_BOUNDED_DISTINCT_EVIDENCE_REQUIRED")
        records[name] = dict(row)
    return raw


def motion_dictionary(raw):
    entries = clock.native(raw, "dictionary", "dynamicMeshDict", "constant")
    require(set(entries) == {"mover"} and entries["mover"][0] == "{"
            and entries["mover"][-1] == "}", "SEEDED_FRAMES_EXACT_MOVER_REQUIRED")
    mover = clock.entries(" ".join(entries["mover"][1:-1]))
    expected = {"type": "motionSolver", "libs": '("libfvMotionSolvers.so")',
        "motionSolver": "solidBody", "cellZone": "rotating-fluid",
        "solidBodyMotionFunction": "rotatingMotion", "origin": "(0 0 0)",
        "axis": "(0 0 1)", "omega": "1450 [rpm]"}
    require(set(mover) == set(expected)
            and all(" ".join(mover[k]) == v for k, v in expected.items()),
            "SEEDED_FRAMES_AUTHORED_GLOBAL_ROTATION_REQUIRED")
    return {"axis": [0, 0, 1], "originM": [0, 0, 0], "speedRpm": 1450.,
            "initialPhaseRad": 0., "referenceCoordinates": "constant/polyMesh/points",
            "equation": "x_rotor(t)=Rz(2*pi*1450*t/60)*x0; x_stator(t)=x0"}


def frame_roles(root, prefix, rows, records):
    name = prefix.split("/")[1]
    roles = set()
    for row in rows:
        require(row["path"].startswith(prefix + "/"),
                "SEEDED_FRAMES_LOCAL_FRAME_INVENTORY_REQUIRED")
        role = row["path"][len(prefix) + 1:]
        require(role in clock.ROLES, "SEEDED_FRAMES_SUPPORTED_DATA_ROLE_REQUIRED")
        roles.add(role)
        raw = bind(root, row, records)
        text = clock.data_text(raw)
        headers = re.findall(r"\bFoamFile\s*\{([^{}]*)\}", text, re.S)
        require(len(headers) == 1, "SEEDED_FRAMES_ONE_NATIVE_HEADER_REQUIRED")
        header = clock.entries(headers[0])
        kind, object_name = clock.ROLES[role]
        location = name + ("/" + role.rsplit("/", 1)[0] if "/" in role else "")
        for key, value in (("format", "ascii"), ("class", kind),
                           ("object", object_name), ("location", location)):
            require(header.get(key) in ([value], ['"' + value + '"']),
                    "SEEDED_FRAMES_EXACT_NATIVE_ROLE_HEADER_REQUIRED")
    require({"polyMesh/points", "uniform/time"} <= roles,
            "SEEDED_FRAMES_POINTS_AND_STORED_CLOCK_REQUIRED")
    return roles


def mesh_inputs(root, rows, records):
    expected = {"case/constant/polyMesh/" + n for n in MESH_FILES}
    require(isinstance(rows, list) and len(rows) == 6
            and all(isinstance(r, dict) for r in rows)
            and {r.get("path") for r in rows} == expected,
            "SEEDED_FRAMES_SIX_REFERENCE_MESH_FILES_REQUIRED")
    raw = {Path(r["path"]).name: bind(root, r, records) for r in rows}
    classes = {"points": {"vectorField"}, "faces": {"faceList"},
        "owner": {"labelList"}, "neighbour": {"labelList"},
        "boundary": {"polyBoundaryMesh"}, "cellZones": {"regIOobject", "cellZoneMesh", "cellZoneList"}}
    for name, payload in raw.items():
        headers = re.findall(r"\bFoamFile\s*\{([^{}]*)\}", clock.data_text(payload), re.S)
        require(len(headers) == 1, "SEEDED_FRAMES_ONE_REFERENCE_HEADER_REQUIRED")
        header = clock.entries(headers[0])
        for key, value in (("format", "ascii"), ("object", name), ("location", "constant/polyMesh")):
            require(header.get(key) in ([value], ['"' + value + '"']),
                    "SEEDED_FRAMES_EXACT_REFERENCE_HEADER_REQUIRED")
        require(header.get("class") is not None and len(header["class"]) == 1
                and header["class"][0].strip('"') in classes[name],
                "SEEDED_FRAMES_EXACT_REFERENCE_ROLE_CLASS_REQUIRED")
    points = motion.point_vectors(raw["points"], location="constant/polyMesh")
    owner = motion.labels(raw["owner"], "owner")
    neighbour = motion.labels(raw["neighbour"], "neighbour")
    require(0 < len(points) <= MAX_POINTS and 0 < len(owner) <= MAX_FACES
            and len(neighbour) <= len(owner), "SEEDED_FRAMES_BOUNDED_REFERENCE_TOPOLOGY_REQUIRED")
    cells = max(max(owner), max(neighbour, default=0)) + 1
    require(0 < cells <= MAX_CELLS and all(a != b for a, b in zip(owner, neighbour)),
            "SEEDED_FRAMES_VALID_REFERENCE_ADJACENCY_REQUIRED")
    zones = motion.native.zones(raw["cellZones"], cells)
    faces = motion.face_vectors(raw["faces"], len(owner), len(points))
    _, pair = motion.boundary_pair(raw["boundary"], len(neighbour), len(owner))
    rotating, stationary, used = bytearray(len(points)), bytearray(len(points)), bytearray(cells)
    for i, face in enumerate(faces):
        for cell in ((owner[i], neighbour[i]) if i < len(neighbour) else (owner[i],)):
            used[cell] = 1
            mask = rotating if cell in zones["rotating-fluid"] else stationary
            for point in face:
                mask[point] = 1
    require(all(used) and 0 < sum(rotating) < len(points)
            and all(a + b == 1 for a, b in zip(rotating, stationary)),
            "SEEDED_FRAMES_DISJOINT_COMPLETE_POINT_MASKS_REQUIRED")
    return points, rotating, stationary, pair, cells


def case_names(root):
    names = []
    for path in (root / "case").iterdir():
        require(len(names) < clock.MAX_FILES * 4 and not path.is_symlink()
                and not getattr(path, "is_junction", lambda: False)(),
                "SEEDED_FRAMES_BOUNDED_UNLINKED_CASE_REQUIRED")
        names.append(path.name)
        if path.is_dir():
            require(not (path / "points0").exists() and not (path / "points0").is_symlink(),
                    "SEEDED_FRAMES_UNCHANGED_CONSTANT_REFERENCE_REQUIRED")
    require(not (root / "case/constant/MRFProperties").exists()
            and not (root / "case/constant/MRFProperties").is_symlink(),
            "SEEDED_FRAMES_NO_SIMULTANEOUS_MRF_REQUIRED")
    return sorted(names)


def inspect(execution_directory, packet):
    """Expose consecutive clock/coordinate facts from a complete seeded log."""
    root = Path(execution_directory)
    require(root.is_dir() and not root.is_symlink()
            and not getattr(root, "is_junction", lambda: False)(),
            "SEEDED_FRAMES_REGULAR_EXECUTION_DIRECTORY_REQUIRED")
    root = root.resolve()
    before_packet = encoded(packet)
    require(isinstance(packet, dict) and set(packet) == {"schemaVersion", "clockInput",
            "referenceMeshFiles", "dynamicMeshDictFile", "frames"}
            and packet["schemaVersion"] == INPUT_SCHEMA, "SEEDED_FRAMES_EXACT_INPUT_SCHEMA_REQUIRED")
    facts = clock.inspect(root, packet["clockInput"])
    records = {}
    for row in facts["checkpointFiles"] + facts["nativeEvidenceRecords"]:
        bind(root, row, records)
    reference, rotor, stator, pair, cells = mesh_inputs(root, packet["referenceMeshFiles"], records)
    dynamic = packet["dynamicMeshDictFile"]
    require(isinstance(dynamic, dict) and dynamic.get("path") == "case/constant/dynamicMeshDict",
            "SEEDED_FRAMES_EXACT_MOTION_DICTIONARY_PATH_REQUIRED")
    plan = motion_dictionary(bind(root, dynamic, records))
    source_input = packet["clockInput"]
    log = bind(root, source_input["nativeLogFile"], records, limit=clock.MAX_LOG)
    prefix = log.decode("ascii").split("Starting time loop", 1)[0]
    require(re.findall(r"(?m)^Selecting solid-body motion function (\S+)\s*$", log.decode("ascii")) == ["rotatingMotion"]
            and re.findall(r"(?m)^Selecting solid-body motion function (\S+)\s*$", prefix) == ["rotatingMotion"],
            "SEEDED_FRAMES_ACTUAL_NATIVE_ROTATION_SELECTOR_REQUIRED")
    identity = clock.decode_json(bind(root, source_input["sourceGeometryIdentityFile"], records, limit=clock.MAX_JSON))
    require(all(isinstance(identity.get(key), list) and len(identity[key]) == 3
                and all(type(value) in (int, float) and math.isfinite(value) for value in identity[key])
                for key in ("axis", "originM"))
            and identity["axis"] == plan["axis"] and identity["originM"] == plan["originM"],
            "SEEDED_FRAMES_MATCHED_AUTHORED_IDENTITY_REQUIRED")
    timeline = clock.parse_resumed_log(log, facts["seed"], facts["controls"], facts["endpoint"])
    require(sha256(encoded(timeline)).hexdigest() == facts["timelineSha256"],
            "SEEDED_FRAMES_FULL_NATIVE_TIMELINE_BINDING_REQUIRED")
    frames = packet["frames"]
    require(isinstance(frames, list) and 2 <= len(frames) <= MAX_FRAMES,
            "SEEDED_FRAMES_BOUNDED_CONSECUTIVE_FRAME_SET_REQUIRED")
    names_before = case_names(root)
    diagonal = math.hypot(*(max(p[a] for p in reference) - min(p[a] for p in reference) for a in range(3)))
    require(math.isfinite(diagonal) and diagonal > 0, "SEEDED_FRAMES_FINITE_REFERENCE_EXTENT_REQUIRED")
    tolerance = 1e-11 + 1e-10 * diagonal
    rows, inventories, previous_index, previous_time = [], [], None, None
    for frame in frames:
        require(isinstance(frame, dict) and set(frame) == {"timeDirectory", "inventory", "inventorySha256"},
                "SEEDED_FRAMES_TYPED_FRAME_REQUIRED")
        name = clock.label(frame["timeDirectory"])
        instant = float(name)
        if clock.close(instant, facts["seed"]["valueS"]):
            state = facts["seed"]
            prior_time = None
        else:
            matched = [s for s in timeline if clock.close(instant, s["physicalTimeS"])]
            require(len(matched) == 1, "SEEDED_FRAMES_ACTUAL_COMPLETE_CLOCK_FRAME_REQUIRED")
            state = matched[0]
            prior_time = facts["seed"]["valueS"] if state["index"] == facts["seed"]["index"] + 1 else timeline[state["index"] - facts["seed"]["index"] - 2]["physicalTimeS"]
        aliases = [n for n in names_before if re.fullmatch(clock.TIME_NAME, n) and clock.close(float(n), instant)]
        require(aliases == [name], "SEEDED_FRAMES_UNAMBIGUOUS_FRAME_DIRECTORY_REQUIRED")
        prefix = "case/" + name
        actual, directories = clock.inventory(root, prefix)
        require(frame["inventory"] == actual and frame["inventorySha256"] == sha256(encoded(actual)).hexdigest(),
                "SEEDED_FRAMES_FULL_CURRENT_FRAME_INVENTORY_REQUIRED")
        roles = frame_roles(root, prefix, actual, records)
        by_path = {r["path"]: r for r in actual}
        temporal = clock.parse_time_state(bind(root, by_path[prefix + "/uniform/time"], records), name)
        value = state["valueS"] if "valueS" in state else state["physicalTimeS"]
        require(temporal["index"] == state["index"] and clock.close(temporal["valueS"], value)
                and clock.close(temporal["beginTimeS"], facts["seed"]["beginTimeS"]),
                "SEEDED_FRAMES_ACTUAL_INDEX_GLOBAL_TIME_REQUIRED")
        if "physicalTimeS" in state:
            tol = 4 * math.ulp(value)
            require(all(abs(temporal[k] - state[k]) <= tol + 2e-11 * temporal[k]
                        for k in ("deltaTS", "deltaT0S")), "SEEDED_FRAMES_ACTUAL_STEP_HISTORY_REQUIRED")
        else:
            require(temporal == facts["seed"], "SEEDED_FRAMES_UNCHANGED_CHECKPOINT_SEED_REQUIRED")
        if previous_index is not None:
            require(state["index"] == previous_index + 1 and prior_time is not None
                    and clock.close(prior_time, previous_time), "SEEDED_FRAMES_GENUINELY_CONSECUTIVE_NATIVE_STEPS_REQUIRED")
        current = motion.point_vectors(bind(root, by_path[prefix + "/polyMesh/points"], records), location=name + "/polyMesh")
        require(len(current) == len(reference), "SEEDED_FRAMES_ALL_REFERENCE_POINT_LABELS_REQUIRED")
        angle = 2 * math.pi * plan["speedRpm"] * temporal["valueS"] / 60
        sine, cosine = math.sin(angle), math.cos(angle)
        errors = [0., 0.]
        for i, (p0, point) in enumerate(zip(reference, current, strict=True)):
            expected = (cosine * p0[0] - sine * p0[1], sine * p0[0] + cosine * p0[1], p0[2]) if rotor[i] else p0
            group = 0 if rotor[i] else 1
            errors[group] = max(errors[group], math.dist(expected, point))
        require(max(errors) <= tolerance, "SEEDED_FRAMES_GLOBAL_ROTATION_COORDINATE_MATCH_REQUIRED")
        rows.append({"timeDirectory": name, "physicalTimeS": temporal["valueS"], "nativeIndex": temporal["index"],
            "deltaTS": temporal["deltaTS"], "deltaT0S": temporal["deltaT0S"], "rotationAngleRad": angle,
            "maximumRotorErrorM": errors[0], "maximumStationaryErrorM": errors[1], "allPointLabelsChecked": len(reference),
            "frameRoleCount": len(roles), "completeStoredStateRoleInventoryPresent": clock.REQUIRED_STATE <= roles,
            "inventorySha256": frame["inventorySha256"]})
        inventories.append((prefix, actual, directories))
        previous_index, previous_time = temporal["index"], temporal["valueS"]
    for prefix, rows_before, dirs_before in inventories:
        require(clock.inventory(root, prefix) == (rows_before, dirs_before), "SEEDED_FRAMES_FRAME_BYTES_IMMUTABLE_REQUIRED")
    require(case_names(root) == names_before and encoded(packet) == before_packet,
            "SEEDED_FRAMES_CASE_AND_PACKET_IMMUTABLE_REQUIRED")
    for row in list(records.values()):
        bind(root, row, records)
    require(clock.inspect(root, packet["clockInput"]) == facts, "SEEDED_FRAMES_CHECKPOINT_CLOCK_INPUTS_IMMUTABLE_REQUIRED")
    output = {"schemaVersion": SCHEMA, "status": "SEEDED_CONSECUTIVE_CLOCK_COORDINATE_FACTS_ONLY",
        "sourceCommit": clock.SOURCE_COMMIT, "nativeSourceBuildMatched": facts["nativeLogBuildVerified"],
        "nativeClockVerified": True, "completeCheckpointRoleInventoryVerified": True,
        "seed": facts["seed"], "endpoint": facts["endpoint"], "clockControls": facts["controls"],
        "fullNativeStepCount": len(timeline), "fullTimelineSha256": facts["timelineSha256"],
        "globalTimeRebased": False, "globalRotationCoordinateEquationMatched": True,
        "genuinelyConsecutiveClockPairsVerified": True, "motionPlan": plan, "nccPairIdentity": pair,
        "rotatingPointCount": sum(rotor), "stationaryPointCount": sum(stator), "referenceCellCount": cells,
        "rotatingPointMaskSha256": sha256(rotor).hexdigest(), "stationaryPointMaskSha256": sha256(stator).hexdigest(),
        "referenceMeshSha256": sha256(encoded(sorted(packet["referenceMeshFiles"], key=lambda r: r["path"]))).hexdigest(),
        "coordinateToleranceM": tolerance, "frames": rows,
        "consecutivePairs": [{"previousTimeDirectory": a["timeDirectory"], "timeDirectory": b["timeDirectory"],
            "previousNativeIndex": a["nativeIndex"], "nativeIndex": b["nativeIndex"], "deltaTS": b["deltaTS"]}
            for a, b in zip(rows, rows[1:])], "evidenceFileRecords": sorted(records.values(), key=lambda r: r["path"]),
        "limits": {"maximumFrames": MAX_FRAMES, "maximumDistinctEvidenceRecords": MAX_RECORDS,
            "maximumDistinctEvidenceBytes": clock.MAX_TOTAL, "maximumFileBytes": clock.MAX_FILE,
            "maximumLogBytes": clock.MAX_LOG, "maximumReportBytes": clock.MAX_JSON},
        "limitations": ["Source/run and current installed-native identity are producer prerequisites, not established here.",
            "Only coordinate/time values and stored headers/inventory are inspected; restart field semantics are unverified.",
            "No dynamic NCC coverage, consecutive Sf/meshPhi/ALE/wall-flux or paired continuous/resumed field acceptance is supplied.",
            "The committed clock reader requires resolved stored/logged time; inherited twelve-digit names may need a separately authored higher-precision producer before native checkpoint generation.",
            "Euler scope deliberately rejects optional Vc0 or points0 state; no invented old-time field is accepted."],
        **dict.fromkeys(FALSE_FLAGS, False)}
    require(len(encoded(output)) <= clock.MAX_JSON, "SEEDED_FRAMES_BOUNDED_FACT_REPORT_REQUIRED")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-directory", required=True)
    parser.add_argument("--input-json", required=True)
    args = parser.parse_args()
    try:
        path = Path(args.input_json)
        require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= clock.MAX_JSON,
                "SEEDED_FRAMES_BOUNDED_INPUT_JSON_REQUIRED")
        raw = path.read_bytes()
        result = inspect(args.execution_directory, clock.decode_json(raw))
        require(path.read_bytes() == raw, "SEEDED_FRAMES_INPUT_JSON_IMMUTABLE_REQUIRED")
        print(encoded(result).decode("ascii"))
        return 0
    except (ValueError, OSError, UnicodeError, KeyError, TypeError, StopIteration) as error:
        print(encoded({"schemaVersion": SCHEMA, "status": "SEEDED_FRAME_EVIDENCE_REJECTED",
            "diagnostic": str(error)[:500], **dict.fromkeys(FALSE_FLAGS, False)}).decode("ascii"))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
