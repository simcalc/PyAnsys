"""Prepare fixed-clock sliding cases and hash-bound native restart checkpoints.

No solver is launched. Actual ASCII checkpoint bytes are inventoried and
checked; numerical restart equivalence belongs to a separately executed test.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_cavitation_case as phase
import frozen_rotor_native_reduction as native
import frozen_rotor_phase_reduction as phase_reduction
import run_frozen_rotor_phase as phase_runner

SCHEMA = "untirta.frozen-rotor-stage4-case-preparation.v1"
BRANCH_SCHEMA = "untirta.frozen-rotor-stage4-branch-preparation.v1"
CHECKPOINT_SCHEMA = "untirta.frozen-rotor-stage4-native-checkpoint.v1"
RESUME_SCHEMA = "untirta.frozen-rotor-stage4-resume-preparation.v1"
MAX_FILE_BYTES = 128 * 1024**2
MAX_BRANCH_BYTES = 512 * 1024**2
MAX_CAMPAIGN_BYTES = 1024**3
MAX_FILES = 128
TIME_NAME = phase_runner.TIME_NAME
REQUIRED_STATE = set(phase_runner.END_FIELDS) | {"phi", "Uf", "rAU", "alphaPhi.water", "meshPhi"}
FIELD_ROLES = {name: ("volVectorField" if name == "U" else "volScalarField", phase_runner.FIELD_DIMENSIONS[name], "cells")
               for name in phase_runner.END_FIELDS}
FIELD_ROLES.update({
    "phi": ("surfaceScalarField", "0 3 -1 0 0 0 0", "internalFaces"),
    "meshPhi": ("surfaceScalarField", "0 3 -1 0 0 0 0", "internalFaces"),
    "alphaPhi.water": ("surfaceScalarField", "0 3 -1 0 0 0 0", "internalFaces"),
    "Uf": ("surfaceVectorField", "0 1 -1 0 0 0 0", "internalFaces"),
    "rAU": ("volScalarField", "-1 3 1 0 0 0 0", "cells"),
    "rho": ("volScalarField", "1 -3 0 0 0 0 0", "cells"),
    "rhoPhi": ("surfaceScalarField", "1 0 -1 0 0 0 0", "internalFaces"),
    "alpha.vapour": ("volScalarField", "0 0 0 0 0 0 0", "cells"),
    "totalPressurePa": ("volScalarField", "1 -1 -2 0 0 0 0", "cells"),
    "U2": ("volScalarField", "0 2 -2 0 0 0 0", "cells"),
    "yPlus": ("volScalarField", "0 0 0 0 0 0 0", "cells"),
})
PATCH_TYPES = {"calculated", "fixedValue", "zeroGradient", "noSlip", "movingWallVelocity", "movingWallSlipVelocity", "slip", "inletOutlet",
    "pressureInletOutletVelocity", "flowRateOutletVelocity", "prghTotalPressure", "fixedFluxPressure",
    "kqRWallFunction", "omegaWallFunction", "nutkWallFunction", "empty", "wedge", "symmetry", "symmetryPlane",
    "cyclic", "nonConformalCyclic", "nonConformalCalculated", "nonConformalError", "processor", "processorCyclic"}
MESH_ROLES = {"points": "vectorField", "faces": "faceList", "owner": "labelList", "neighbour": "labelList",
              "boundary": "polyBoundaryMesh", "cellZones": "regIOobject", "faceZones": "regIOobject", "pointZones": "regIOobject"}
NATIVE_RESTART_REFERENCES = [
    "https://github.com/OpenFOAM/OpenFOAM-12/blob/master/applications/modules/incompressibleVoF/incompressibleVoF.C",
    "https://github.com/OpenFOAM/OpenFOAM-12/blob/master/src/finiteVolume/fvMesh/fvMesh.C",
    "https://github.com/OpenFOAM/OpenFOAM-12/blob/master/src/finiteVolume/fields/fvsPatchFields/derived/nonConformalPolyFaces/nonConformalPolyFacesFvsPatchLabelField.C",
    "https://github.com/OpenFOAM/OpenFOAM-12/blob/master/src/OpenFOAM/db/Time/TimeIO.C"]
# This exact recipe sets correctPhi=yes. The derived incompressibleVoF
# constructor consequently creates named READ_IF_PRESENT/AUTO_WRITE rAU;
# the generic VoFSolver temporary-inverse fallback is not this case's writer.


class Stage4Issue(ValueError):
    pass


def require(condition, action):
    if not condition:
        raise Stage4Issue(action)


def encoded(value):
    return phase.canonical(value)


def descriptor(path, raw):
    return {"path": path, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def read(root, relative, limit=MAX_FILE_BYTES):
    phase.relative_path(relative)
    root = phase.base.regular_directory(root)
    target = root / relative
    for item in (target, *target.parents):
        require(not item.is_symlink() and not getattr(item, "is_junction", lambda: False)(), "Stage4 inputs cannot contain links or junctions.")
    raw = phase.base.read_regular(target, limit)
    return raw, descriptor(relative, raw)


def verify_rows(root, rows):
    require(isinstance(rows, list) and 0 < len(rows) <= MAX_FILES, "A bounded actual file inventory is required.")
    result, seen, total = [], set(), 0
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}
                and isinstance(row["path"], str) and row["path"] not in seen
                and isinstance(row["bytes"], int) and not isinstance(row["bytes"], bool) and 0 < row["bytes"] <= MAX_FILE_BYTES
                and isinstance(row["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]),
                "Stage4 file rows require exact unique relative paths, sizes and SHA256.")
        raw, actual = read(root, row["path"])
        require(actual == row, "An actual Stage4 input/checkpoint changed after its SHA256 binding: " + row["path"])
        total += len(raw)
        require(total <= MAX_BRANCH_BYTES, "The actual branch inventory exceeds512MiB.")
        result.append(actual); seen.add(row["path"])
    return sorted(result, key=lambda row: row["path"])


def aligned_steps(time_s, dt_s):
    value, dt = phase.protocol.number(time_s), phase.protocol.number(dt_s, positive=True)
    require(value >= 0, "The fixed physical clock cannot be negative.")
    steps = round(value / dt)
    require(steps <= 100_000 and math.isclose(value, steps * dt, rel_tol=5e-12, abs_tol=1e-13),
            "Every start/checkpoint/end time must align with the declared fixed-dt grid.")
    return steps


def temporal_plan(study, coarse_dt_s=None, revolutions=5.5):
    period = 60 / study["input"]["speedRpm"]
    turns = phase.protocol.number(revolutions, positive=True)
    require(5 <= turns <= 10, "Use5–10 declared revolutions with a common final three-revolution window.")
    dt = period / 360 if coarse_dt_s is None else phase.protocol.number(coarse_dt_s, positive=True)
    require(dt <= period / 360 * (1+1e-12), "The coarse fixed dt must resolve at most one rotor degree.")
    end, checkpoint = turns * period, turns * period / 2
    require(end - checkpoint >= period and not math.isclose(turns, round(turns), abs_tol=1e-10)
            and not math.isclose(turns/2, round(turns/2), abs_tol=1e-10),
            "Checkpoint and end writes must retain noninteger rotor angles and at least one resumed revolution.")
    variants = []
    for index, name in enumerate(("coarse", "medium", "fine")):
        step = dt / 2**index
        count, mid = aligned_steps(end, step), aligned_steps(checkpoint, step)
        require(count == 2*mid and mid > 0, "The two native write times must align exactly on every fixed clock.")
        variants.append({"id": name, "casePath": name+"-case", "timeStepS": step, "initialTimeS": 0.,
                         "endTimeS": end, "writeIntervalSteps": mid, "totalStepCount": count,
                         "checkpointStepIndex": mid, "mode": "transient-sliding", "commonWindowS": [end-3*period, end]})
    return {"rotationPeriodS": period, "revolutions": turns, "endTimeS": end, "checkpointTimeS": checkpoint,
            "fixedTimeStep": True, "adaptiveTimeStepping": False, "refinementRatio": 2,
            "commonWindowS": [end-3*period, end], "variants": variants}


def fixed_recipe(study, pressure, dt_s, end_s, *, initial_s=0., write_interval_steps=None):
    """Same physical sliding recipe; only declared clock/write controls differ."""
    initial, end = phase.protocol.number(initial_s), phase.protocol.number(end_s, positive=True)
    total = aligned_steps(end, dt_s); start = aligned_steps(initial, dt_s)
    require(0 <= start < total, "The fixed resume end must follow its actual checkpoint.")
    interval = total // 2 if write_interval_steps is None else write_interval_steps
    require(isinstance(interval, int) and not isinstance(interval, bool) and interval > 0 and (total-start) % interval == 0,
            "The native write interval must be a positive aligned number of fixed time steps.")
    files = phase.native_recipe(study, pressure, "transient-sliding", dt_s, end)
    text = files["system/controlDict"]
    replacements = {"startTime": format(initial, ".17g"), "writeControl": "timeStep", "writeInterval": str(interval),
                    "purgeWrite": "2", "timePrecision": "17", "adjustTimeStep": "no"}
    for key, value in replacements.items():
        text, count = re.subn(r"(?m)^"+key+r"\s+[^;]+;", key+" "+value+";", text)
        require(count == 1, "The authored fixed-clock control must have exactly one entry: "+key)
    files["system/controlDict"] = text
    return files


def control_groups(study, pressure, mesh_rows, geometry_row, runtime_row):
    # Signature-only sentinel recipe is never executed. Original bytes remain
    # bound individually; only declared p/start/end/dt/write scalars are excluded.
    period = 60/study["input"]["speedRpm"]
    sentinel = fixed_recipe(study, 1., period/360, 5.5*period, write_interval_steps=990)
    groups = {key: [] for key in phase.protocol.CONTROL_KEYS}
    groups["geometrySha256"], groups["meshSha256"], groups["runtimeSha256"] = [geometry_row], mesh_rows, [runtime_row]
    for name, content in sentinel.items():
        key = "cavitationModelSha256" if name in phase_reduction.MODEL_FILES else "discretizationSha256" if name.startswith("system/") else "boundarySha256"
        groups[key].append(descriptor(name, content.encode("ascii")))
    return {key: sorted(rows, key=lambda row: row["path"]) for key, rows in groups.items()}


def fresh_output(value, report_name):
    destination = Path(value).absolute()
    phase.base.inventory.output_path(destination / report_name)
    require(not destination.exists() and not destination.is_symlink() and not getattr(destination, "is_junction", lambda:False)(),
            "Use a new independent RUNNER_TEMP output directory.")
    return destination


def write(root, relative, raw, written):
    phase.relative_path(relative)
    target = root / relative
    require(target.absolute().is_relative_to(root.absolute()) and not target.exists(), "Write only new attempt-owned files.")
    for item in (target, *target.parents):
        require(not item.is_symlink() and not getattr(item, "is_junction", lambda: False)(), "Output paths cannot contain links or junctions.")
    written.append(target)
    phase.base.write_exclusive(target, raw)


def cleanup(root, written):
    # Remove only exact files created by this failed preparation; no recursive
    # deletion and no unrelated runtime or existing source directory is touched.
    for target in reversed(written):
        require(target.absolute().is_relative_to(root.absolute()), "Cleanup must remain inside this attempt.")
        if target.is_file() and not target.is_symlink():
            target.unlink()
    if root.is_dir() and not root.is_symlink():
        for directory in sorted((p for p in root.rglob("*") if p.is_dir() and not p.is_symlink()), key=lambda p: len(p.parts), reverse=True):
            if not any(directory.iterdir()): directory.rmdir()
        if not any(root.iterdir()): root.rmdir()


def prepare_campaign(baseline_execution_directory, packet, output_directory, *, pressure_absolute_pa,
                     runtime_identity, coarse_dt_s=None, revolutions=5.5):
    require(packet.get("schemaVersion") == phase.NATIVE_REDUCTION_SCHEMA, "Use the actual qualified single-phase native reducer packet.")
    source_root = phase.inside_runner(baseline_execution_directory)
    source_case, source_study, observation, source_rows, source_manifest = phase.verify_baseline(source_root, packet)
    pressure = phase.protocol.number(pressure_absolute_pa, positive=True)
    require(pressure <= observation["pressureLevelAbsolutePa"], "Choose a declared pressure no higher than the qualified source baseline.")
    phase_reduction.phase_binary_identity(runtime_identity)
    runtime_raw = encoded(runtime_identity); runtime_row = descriptor("stage4-runtime-identity.json", runtime_raw)
    geometry_raw, geometry_row = read(source_root, "geometry-identity.json", native.MAX_RECORD)
    geometry_control = sha256(encoded([geometry_row])).hexdigest()
    require(geometry_control == observation["controls"]["geometrySha256"], "The canonical source geometry identity must remain exact.")
    mesh_rows = [row for row in source_rows if row["path"].startswith("constant/polyMesh/")]
    require({"constant/polyMesh/"+name for name in phase.MESH_FILES} == {row["path"] for row in mesh_rows}, "The exact six-file reference mesh is required.")
    mesh_rows = verify_rows(source_case, mesh_rows)
    source_evidence = verify_rows(source_root, packet["nativeEvidenceRecords"])
    plan = temporal_plan(source_study, coarse_dt_s, revolutions)
    groups = control_groups(source_study, pressure, mesh_rows, geometry_row, runtime_row)
    controls = {key: sha256(encoded(rows)).hexdigest() for key, rows in groups.items()}
    study = phase.protocol.prepare_study(inputs=source_study["input"], controls=controls, gravity_in_solver=False)
    destination = fresh_output(output_directory, "stage4-case-preparation.json")
    written, variants, total_bytes = [], [], 0
    try:
        for variant in plan["variants"]:
            prefix = variant["casePath"]+"/"
            recipe = fixed_recipe(study, pressure, variant["timeStepS"], variant["endTimeS"], write_interval_steps=variant["writeIntervalSteps"])
            for row in mesh_rows:
                raw, actual = read(source_case, row["path"])
                require(actual == row, "The reference mesh changed before copying.")
                write(destination, prefix+"case/"+row["path"], raw, written); total_bytes += len(raw)
            authored = []
            for name, content in sorted(recipe.items()):
                raw = content.encode("ascii"); authored.append(descriptor(name, raw))
                write(destination, prefix+"case/"+name, raw, written); total_bytes += len(raw)
            write(destination, prefix+geometry_row["path"], geometry_raw, written)
            write(destination, prefix+runtime_row["path"], runtime_raw, written)
            branch = {"schemaVersion": BRANCH_SCHEMA, "variant": {**variant, "controls": controls}, "study": study,
                "declaredStudyInputs": study["input"], "physicalBasis": study["physicalBasis"], "controls": controls,
                "pressureLevelAbsolutePa": pressure, "authoredInputFiles": authored, "referenceMeshFiles": mesh_rows,
                "authoredCavitationModelSha256": controls["cavitationModelSha256"], "sourceGeometryIdentityFile": geometry_row,
                "sourceGeometryControlSha256": geometry_control, "sourceBaselineEvidenceSha256": observation["nativeEvidence"]["sha256"],
                "sourceCaseManifestSha256": source_manifest, "runtimeIdentityFile": runtime_row,
                "sourceBaselineRole": "Qualified single-phase preparation prerequisite; not an active-phase H0 reference.",
                "fixedClock": True, "adaptiveTimeStepping": False, "simulationExecuted": False,
                "restartVerified": False, "timeStepConvergenceVerified": False, "mainApplicationWrites": False}
            branch_raw = phase.base.inventory.bounded_json(branch)
            write(destination, prefix+"stage4-branch-preparation.json", branch_raw, written)
            variants.append({**variant, "authoredInputFiles": authored, "referenceMeshFiles": mesh_rows, "controls": controls,
                             "branchPreparationFile": descriptor(prefix+"stage4-branch-preparation.json", branch_raw)})
            require(sum(p.stat().st_size for p in written if p.is_file() and p.is_relative_to(destination/variant["casePath"])) <= MAX_BRANCH_BYTES,
                    "A prepared branch exceeds512MiB.")
        require(sum(p.stat().st_size for p in written) <= MAX_CAMPAIGN_BYTES, "The fresh campaign exceeds1GiB.")
        verify_rows(source_case, source_rows); verify_rows(source_root, source_evidence)
        require(read(source_root, "geometry-identity.json", native.MAX_RECORD)[0] == geometry_raw, "Source geometry identity changed during preparation.")
        report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(), "status": "FIXED_CLOCK_CASES_PREPARED_NATIVE_EXECUTION_REQUIRED",
            "exampleDataCommit": phase.base.inventory.PIN, "sourceBaselineEvidenceSha256": observation["nativeEvidence"]["sha256"],
            "sourceCaseManifestSha256": source_manifest, "study": study, "controls": controls, "variants": variants,
            "pressureLevelAbsolutePa": pressure, "temporalPlan": {key:value for key,value in plan.items() if key != "variants"},
            "checkpointPlan": {"variantId": "fine", "physicalTimeS": plan["checkpointTimeS"], "continuationEndTimeS": plan["endTimeS"],
                "timeStepS": variants[-1]["timeStepS"], "checkpointStepIndex": variants[-1]["checkpointStepIndex"]},
            "limits": {"perBranchBytes": MAX_BRANCH_BYTES, "aggregateBytes": MAX_CAMPAIGN_BYTES},
            "sourceBaselineImmutable": True, "nativeCoordinateUnitsVerified": False, "classroomMetresPerSourceCoordinateUnit": 1,
            "physicalTransientExecuted": False, "phaseFamilyReferenceQualified": False, "actualCfdRunVerified": False,
            "restartVerified": False, "timeStepConvergenceVerified": False, "mainApplicationWrites": False,
            "claimBoundary": "Exact upstream-qualified geometry and authored fixed-dt sliding preparation only. Native execution, Courant acceptance, time GCI and restart equivalence remain separate."}
        write(destination, "stage4-case-preparation.json", phase.base.inventory.bounded_json(report), written)
        require(sum(p.stat().st_size for p in written) <= MAX_CAMPAIGN_BYTES, "The complete campaign including reports exceeds1GiB.")
        return report
    except Exception:
        cleanup(destination, written)
        raise


def plain_native(raw, name, kind):
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw.decode("ascii"), flags=re.S)
    header = re.search(r"\bFoamFile\s*\{([^{}]*)\}", text)
    require(header and len(re.findall(r"\bFoamFile\s*\{", text)) == 1
            and len(re.findall(r"\bformat\s+[^;]+;", header[1])) == 1
            and len(re.findall(r"\bclass\s+[^;]+;", header[1])) == 1
            and len(re.findall(r"\bobject\s+[^;]+;", header[1])) == 1
            and re.search(r"\bformat\s+ascii\s*;", header[1])
            and re.search(r"\bclass\s+"+re.escape(kind)+r"\s*;", header[1])
            and re.search(r"\bobject\s+"+re.escape(name)+r"\s*;", header[1])
            and not re.search(r"[#\$]|\b(?:code\w*|coded\w*|libs|functionObjectLibs|systemCall|include)\b", text),
            "Only exact plain ASCII native roles without executable/include/library directives are admitted: "+name)
    require(not re.search(r"\b(?:nan|inf|infinity)\b", text, re.I)
            and all(math.isfinite(float(m[0])) for m in re.finditer(native.FLOAT, text)), "All native numeric literals must be finite.")
    return text


def mesh_counts(case):
    owners, neighbours = [], []
    for name, result in (("owner", owners), ("neighbour", neighbours)):
        count, body = phase.base.native_list(case/"constant/polyMesh"/name)
        tokens = body.split()
        require(count <= 5_000_000 and len(tokens) == count and all(re.fullmatch(r"\d+", value) for value in tokens), "Actual native adjacency counts/labels must be complete.")
        result.extend(int(value) for value in tokens)
    require(owners and len(neighbours) < len(owners), "A nonempty mesh with exterior faces is required.")
    cells = max(owners+neighbours)+1
    require(cells <= phase_runner.MAX_CELL_COUNT and set(owners+neighbours) == set(range(cells)), "Every bounded native cell must appear in adjacency.")
    points = phase_runner.points(case/"constant/polyMesh/points")
    return {"cells": cells, "faces": len(owners), "internalFaces": len(neighbours), "points": len(points)}


def state_field(raw, name, counts):
    require(name in FIELD_ROLES, "Unknown native checkpoint field roles are prohibited: "+name)
    kind, dimensions, role = FIELD_ROLES[name]
    text = plain_native(raw, name, kind)
    require(len(re.findall(r"\bdimensions\s*\[", text)) == 1 and len(re.findall(r"\binternalField\s+", text)) == 1
            and len(re.findall(r"\bboundaryField\s*\{", text)) == 1
            and re.search(r"\bdimensions\s*\[\s*"+dimensions+r"\s*\]\s*;", text),
            "Native checkpoint field roles require one exact dimension, internal field and boundary dictionary: "+name)
    require(all(match[1].strip() in PATCH_TYPES for match in re.finditer(r"\btype\s+([^;]+);", text)),
            "Unknown executable/native boundary condition roles are prohibited.")
    if name in phase_runner.END_FIELDS:
        return phase_runner.finite_field(raw, name, counts["cells"])
    match = re.search(r"\binternalField\s+(uniform\s+([^;]+)|nonuniform\s+List<(scalar|vector)>\s+(\d+)\s*\((.*?)\))\s*;", text, re.S)
    require(match, "Every native restart field requires an actual internal value declaration.")
    vector = kind.endswith("VectorField"); source = match[2] if match[2] is not None else match[5]
    expected = 1 if match[2] is not None else counts[role]
    if match[2] is None:
        require(int(match[4]) == expected and match[3] == ("vector" if vector else "scalar"), "Native flux/state internal count must match cell or internal-face topology.")
    if vector:
        pattern = r"\(\s*"+native.FLOAT+r"\s+"+native.FLOAT+r"\s+"+native.FLOAT+r"\s*\)"
        require(len(re.findall(pattern, source)) == expected and not re.sub(pattern, "", source).strip(), "Every native face velocity must have three components.")
    else:
        require("(" not in source and ")" not in source, "Native scalar state must contain scalar values only.")
    tokens = re.sub(r"[()]", " ", source).split()
    require(len(tokens) == expected*(3 if vector else 1) and all(re.fullmatch(native.FLOAT, value) for value in tokens), "Native full-state shape/count must be exact.")
    values = [float(value) for value in tokens]
    require(values and all(math.isfinite(value) for value in values), "All native full restart state values must be finite.")
    if name in {"rAU", "rho", "U2", "yPlus"}: require(min(values) >= 0, "Positive physical native state cannot be negative.")
    if name == "alpha.vapour": require(min(values) >= -1e-9 and max(values) <= 1+1e-9, "The actual vapour fraction must remain bounded.")
    return {"name": name, "class": kind, "dimensions": dimensions, "topologyRole": role, "internalValueCount": len(values),
            "uniform": match[2] is not None, "minimum": min(values), "maximum": max(values), "allInternalValuesChecked": True}


def native_time_state(raw, name, dt_s):
    text = plain_native(raw, "time", "dictionary")
    body = re.sub(r"\bFoamFile\s*\{[^{}]*\}", "", text).strip()
    entries = re.findall(r"\b(beginTime|value|name|index|deltaT|deltaT0)\s+([^;]+);", body)
    require(len(entries) == 6 and {key for key,_ in entries} == {"beginTime", "value", "name", "index", "deltaT", "deltaT0"}
            and not re.sub(r"\b(beginTime|value|name|index|deltaT|deltaT0)\s+[^;]+;", "", body).strip(), "Preserve the exact supported native uniform/time state without unknown dictionary entries.")
    values = {key:value.strip().strip('"') for key,value in entries}
    require(values["name"] == name and re.fullmatch(r"\d+", values["index"]) and all(re.fullmatch(native.FLOAT, values[key]) for key in ("beginTime", "value", "deltaT", "deltaT0")), "Native time state names, scalar clock and integer index must be exact.")
    physical = float(name); step = aligned_steps(physical, dt_s)
    require(math.isclose(float(values["value"]), physical, rel_tol=5e-12, abs_tol=1e-13) and int(values["index"]) == step
            and float(values["beginTime"]) == 0 and all(math.isclose(float(values[key]), dt_s, rel_tol=5e-12, abs_tol=1e-15) for key in ("deltaT", "deltaT0")),
            "Checkpoint physical time, index, deltaT and deltaT0 must match the original fixed clock.")
    return {"physicalTimeS": physical, "timeIndex": step, "deltaTS": float(values["deltaT"]), "deltaT0S": float(values["deltaT0"]), "beginTimeS": 0.}


def nonconformal_addressing(raw, case, counts):
    """Check actual NCC face-label addressing without reconstructing or healing it."""
    text = plain_native(raw, "polyFaces", "surfaceLabelField")
    require(len(re.findall(r"\bdimensions\s*\[", text)) == 1
            and re.search(r"\bdimensions\s*\[\s*0 0 0 0 0 0 0\s*\]\s*;",text),
            "NCC addressing must have exactly one dimensionless native label-field role.")
    internal = re.findall(r"\binternalField\s+uniform\s+(-?\d+)\s*;", text)
    require(internal == ["-1"] and len(re.findall(r"\binternalField\s+",text)) == 1,
            "Foundation12 writes NCC internal polyFaces as uniform -1; only that exact role is supported.")
    match = re.search(r"\bboundaryField\s*\{(.*)\}\s*$",text,re.S)
    require(match and len(re.findall(r"\bboundaryField\s*\{",text)) == 1,"A complete native NCC boundary dictionary is required.")
    blocks = list(re.finditer(r'"?([A-Za-z0-9_-]+)"?\s*\{([^{}]*)\}',match[1]))
    require(blocks and not re.sub(r'"?[A-Za-z0-9_-]+"?\s*\{[^{}]*\}',"",match[1]).strip(),
            "NCC addressing admits only flat named native patch dictionaries.")
    patch_count, boundary = phase.base.native_list(case/"constant/polyMesh/boundary")
    source_patches = re.findall(r'"?([A-Za-z0-9_-]+)"?\s*\{([^{}]*)\}',boundary)
    expected = [name for name,_ in source_patches]
    names = [block[1] for block in blocks]
    require(len(names) == patch_count and len(set(names)) == len(names) and set(names) == set(expected),
            "Every original native patch must retain one exact NCC addressing role.")
    original_ranges={}
    for patch,body in source_patches:
        first=re.findall(r"\bstartFace\s+(\d+)\s*;",body);size=re.findall(r"\bnFaces\s+(\d+)\s*;",body)
        require(len(first)==1 and len(size)==1 and counts["internalFaces"]<=int(first[0])<=counts["faces"]
                and int(first[0])+int(size[0])<=counts["faces"],"Native source patch ranges must match original topology.")
        original_ranges[patch]=(int(first[0]),int(first[0])+int(size[0]))
    source_bodies=dict(source_patches)
    checked,total = [],0
    for block in blocks:
        body=block[2]
        kinds=re.findall(r"\btype\s+([^;]+);",body)
        require(len(kinds)==1 and kinds[0].strip() in {"polyFaces","nonConformalPolyFaces"},"Only Foundation12 native face-label patch roles are allowed.")
        rest=re.sub(r"\btype\s+[^;]+;","",body)
        patch_types=re.findall(r"\bpatchType\s+([^;]+);",rest)
        require(not patch_types or len(patch_types)==1 and patch_types[0].strip() in PATCH_TYPES,"The native addressing constraint override must have a known role.")
        rest=re.sub(r"\bpatchType\s+[^;]+;","",rest)
        if kinds[0].strip()=="polyFaces":
            require(not rest.strip(),"Conformal native polyFaces patch addressing is implicit; no unknown payload is allowed.")
            require(not re.search(r"\btype\s+nonConformal(?:Cyclic|Error)\s*;",source_bodies[block[1]]),
                    "A nonconformal native patch cannot discard its explicit restart addressing.")
            checked.append({"patch":block[1],"role":"implicit-original-face-range"});continue
        original=re.findall(r'\boriginalPatch\s+"?([A-Za-z0-9_-]+)"?\s*;',source_bodies[block[1]])
        require(re.search(r"\btype\s+nonConformal(?:Cyclic|Error)\s*;",source_bodies[block[1]])
                and len(original)==1 and original[0] in original_ranges,
                "Each native NCC addressing role must bind its exact declared originalPatch.")
        low,high=original_ranges[original[0]]
        sizes=re.findall(r"\bsize\s+(\d+)\s*;",rest)
        require(len(sizes)==1 and int(sizes[0])<=5_000_000,"The native NCC face count must be bounded and exact.")
        count=int(sizes[0]);rest=re.sub(r"\bsize\s+\d+\s*;","",rest)
        value=re.fullmatch(r"\s*value\s+(uniform\s+(-?\d+)|nonuniform\s+List<label>\s+(\d+)\s*\((.*?)\))\s*;\s*",rest,re.S)
        require(value,"A full native NCC face-label mapping is required.")
        tokens=[value[2]] if value[2] is not None else value[4].split()
        require(value[2] is not None or int(value[3])==count and len(tokens)==count,"Native NCC label-list size must match every actual mapping entry.")
        require(all(re.fullmatch(r"\d+",v) and low<=int(v)<high for v in tokens),
                "Every NCC mapping must reference a face of this patch's exact originalPatch range.")
        total+=count;require(total<=10_000_000,"The aggregate NCC mapping exceeds the bounded native inventory.")
        checked.append({"patch":block[1],"role":"native-nonconformal-face-labels","count":count,
                        "originalPatch":original[0],"originalFaceRange":[low,high],"allFaceIdsInOriginalBoundaryRange":True})
    return {"nativeRole":"nonConformalPolyFaces","patches":checked,"nativeIntersectionFaceCount":total,
            "geometryReconstructed":False,"allNativePatchRolesChecked":True}


def branch_inputs(root, variant):
    require(isinstance(variant, dict) and variant.get("casePath") in {"coarse-case", "medium-case", "fine-case", "resumed-case"},
            "Use only a declared Stage4 branch wrapper.")
    branch = phase.inside_runner(Path(root)/variant["casePath"])
    report = phase.json_file(branch/"stage4-branch-preparation.json")
    require(report.get("schemaVersion") == BRANCH_SCHEMA and report.get("mainApplicationWrites") is False
            and all(report.get("variant", {}).get(key) == variant.get(key) for key in
                ("id", "casePath", "timeStepS", "initialTimeS", "endTimeS", "writeIntervalSteps", "totalStepCount", "checkpointStepIndex", "mode", "commonWindowS")),
            "The current branch preparation must bind this exact variant and complete fixed clock.")
    declared = phase.protocol.prepare_study(inputs=report["declaredStudyInputs"], controls=report["controls"], gravity_in_solver=False)
    require(declared == report["study"] and report["physicalBasis"] == declared["physicalBasis"], "The exact Stage4 study/basis must remain current.")
    recipe = fixed_recipe(declared, report["pressureLevelAbsolutePa"], variant["timeStepS"], variant["endTimeS"],
                          initial_s=variant["initialTimeS"], write_interval_steps=variant["writeIntervalSteps"])
    authored = verify_rows(branch/"case", report["authoredInputFiles"])
    require({row["path"] for row in authored} == set(recipe), "All authored roles must match the fixed sliding recipe.")
    for name, content in recipe.items(): require(read(branch/"case", name)[0] == content.encode("ascii"), "An authored fixed sliding input differs from its exact recipe: "+name)
    mesh = verify_rows(branch/"case", report["referenceMeshFiles"])
    require({row["path"] for row in mesh} == {"constant/polyMesh/"+name for name in phase.MESH_FILES}, "The exact six-file reference mesh inventory is required.")
    geometry = verify_rows(branch, [report["sourceGeometryIdentityFile"]])
    runtime = verify_rows(branch, [report["runtimeIdentityFile"]])
    groups = control_groups(declared, report["pressureLevelAbsolutePa"], mesh, geometry[0], runtime[0])
    require({key:sha256(encoded(rows)).hexdigest() for key,rows in groups.items()} == report["controls"], "Every fresh structural control must match its exact native inputs.")
    return branch, report, authored, mesh


def build_checkpoint(root, variant, native_time_directory):
    """Inventory measured native checkpoint state; this does not execute restart."""
    branch, prepared, authored, mesh = branch_inputs(root, variant)
    name = str(native_time_directory)
    require(re.fullmatch(TIME_NAME, name) and float(name) > 0, "Select an exact positive native physical-time directory.")
    index = aligned_steps(float(name), variant["timeStepS"])
    require(index == variant["checkpointStepIndex"] and float(name) < variant["endTimeS"], "Select the actual authored aligned checkpoint, not an endpoint or stale directory.")
    folder = phase.base.regular_directory(branch/"case"/name)
    counts = mesh_counts(branch/"case")
    records, roles, seen, total = [], [], set(), 0
    for path in sorted(folder.rglob("*"), key=lambda item:item.relative_to(folder).as_posix()):
        require(not path.is_symlink() and not getattr(path, "is_junction", lambda:False)(), "Native checkpoint links/junctions are prohibited.")
        if path.is_dir():
            require(path.relative_to(folder).as_posix() in {"polyMesh", "uniform"}, "Unknown native checkpoint directories are prohibited.")
            continue
        local = path.relative_to(folder).as_posix()
        require(len(records) <64 and (local in FIELD_ROLES or local in {"uniform/time","polyMesh/polyFaces"}
                or local.startswith("polyMesh/") and local[9:] in MESH_ROLES), "Unknown native checkpoint file/executable dictionary roles are prohibited: "+local)
        raw, record = read(branch, "case/"+name+"/"+local)
        total += len(raw); require(total <= MAX_BRANCH_BYTES, "The actual checkpoint exceeds512MiB.")
        if local in FIELD_ROLES: checked = state_field(raw, local, counts)
        elif local == "uniform/time": checked = native_time_state(raw, name, variant["timeStepS"])
        elif local == "polyMesh/polyFaces": checked = nonconformal_addressing(raw,branch/"case",counts)
        else:
            mesh_name = local[9:]; plain_native(raw, mesh_name, MESH_ROLES[mesh_name])
            if mesh_name == "points":
                actual = phase_runner.points(path); require(len(actual) == counts["points"], "Moved checkpoint points must retain every native reference index.")
                checked = {"nativeRole": "movedPoints", "pointCount": len(actual), "allCoordinatesFinite": True,
                           "referencePointsReplaced": False, "nativeCoordinateUnitsVerified": False}
            else:
                original = next((row for row in mesh if row["path"] == "constant/polyMesh/"+mesh_name), None)
                require(original and record["sha256"] == original["sha256"], "Fixed-topology checkpoint overlays must preserve the exact original topology; only moved points may differ.")
                checked = {"nativeRole": mesh_name, "referenceTopologyUnchanged": True}
        records.append(record); roles.append({"path": record["path"], "check": checked}); seen.add(local)
    required=REQUIRED_STATE | {"polyMesh/points", "uniform/time"}
    boundary_text=plain_native(read(branch/"case","constant/polyMesh/boundary")[0],"boundary","polyBoundaryMesh")
    if re.search(r"\btype\s+nonConformal(?:Cyclic|Error)\s*;",boundary_text):required.add("polyMesh/polyFaces")
    require(required <= seen, "The actual native checkpoint must contain all primary/flux fields, phi/Uf/rAU/alphaPhi.water/meshPhi, moved points, native NCC addressing and uniform/time when applicable.")
    verify_rows(branch, records); branch_inputs(root, variant)
    prep_raw, prep_row = read(branch, "stage4-branch-preparation.json", native.MAX_RECORD)
    return {"schemaVersion": CHECKPOINT_SCHEMA, "exampleDataCommit": phase.base.inventory.PIN,
            "variant": variant, "sourceBranch": variant["casePath"], "nativeTimeDirectory": name, "physicalTimeS": float(name),
            "physicalTimeStepS": variant["timeStepS"], "physicalModel": "transient-cavitation-sliding-mesh",
            "files": records, "nativeRoles": roles, "checkpointSha256": sha256(encoded(records)).hexdigest(),
            "referenceMeshFiles": mesh, "referenceMeshSha256": sha256(encoded(mesh)).hexdigest(),
            "nativeCaseFiles": authored+mesh, "nativeCaseManifestSha256": sha256(encoded(sorted(authored+mesh,key=lambda row:row["path"]))).hexdigest(),
            "branchPreparationFile": prep_row, "controls": prepared["controls"], "meshCounts": counts,
            "checkpointInventoryVerified": True, "allRequiredNativeRolesVerified": True, "restartVerified": False,
            "nativeRestartReferences": NATIVE_RESTART_REFERENCES,
            "actualCfdRunVerified": False, "mainApplicationWrites": False,
            "claimBoundary": "Measured complete native checkpoint bytes/roles and fixed-clock state only; execution provenance and continuous/restarted field equivalence must be verified independently."}


def prepare_resume(root, checkpoint_packet, output_directory):
    require(checkpoint_packet.get("schemaVersion") == CHECKPOINT_SCHEMA and checkpoint_packet.get("mainApplicationWrites") is False,
            "Use the exact actual Stage4 checkpoint inventory packet.")
    variant = checkpoint_packet["variant"]
    current = build_checkpoint(root, variant, checkpoint_packet["nativeTimeDirectory"])
    require(current == checkpoint_packet, "The actual checkpoint or its source inputs changed before resume preparation.")
    source, prepared, authored, mesh = branch_inputs(root, variant)
    initial, end = checkpoint_packet["physicalTimeS"], variant["endTimeS"]
    period = 60/prepared["declaredStudyInputs"]["speedRpm"]
    require(end-initial >= period-1e-13, "The actual resumed comparison must span at least one rotor revolution.")
    resumed = {**prepared["variant"], "id": "resumed", "casePath": "resumed-case", "initialTimeS": initial,
               "totalStepCount": aligned_steps(end-initial, variant["timeStepS"]), "checkpointStepIndex": aligned_steps(initial,variant["timeStepS"]),
               "writeIntervalSteps": aligned_steps(end-initial,variant["timeStepS"])}
    recipe = fixed_recipe(prepared["study"], prepared["pressureLevelAbsolutePa"], variant["timeStepS"], end,
                          initial_s=initial, write_interval_steps=resumed["writeIntervalSteps"])
    destination = fresh_output(output_directory, "stage4-resume-preparation.json")
    written, copied, total = [], [], 0
    try:
        for row in authored+mesh:
            raw, actual = read(source/"case", row["path"]); require(actual == row, "An original resume input changed.")
            if row["path"] == "system/controlDict": raw = recipe[row["path"]].encode("ascii")
            else: require(not row["path"] in recipe or raw == recipe[row["path"]].encode("ascii"), "Resume may alter only the explicit fixed start/write controls.")
            write(destination, "case/"+row["path"], raw, written); copied.append(descriptor(row["path"],raw)); total+=len(raw)
        for row in checkpoint_packet["files"]:
            raw, actual = read(source,row["path"]); require(actual == row, "Native restart state changed before copying.")
            write(destination,row["path"],raw,written); total+=len(raw)
        for row in (prepared["sourceGeometryIdentityFile"],prepared["runtimeIdentityFile"]):
            raw,actual=read(source,row["path"]);require(actual==row,"Source identity changed before copying.")
            write(destination,row["path"],raw,written);total+=len(raw)
        require(total <= MAX_BRANCH_BYTES,"The complete resumed case exceeds512MiB.")
        resumed_prepared={**prepared,"variant":{**resumed,"controls":prepared["controls"]},
            "authoredInputFiles":sorted([row for row in copied if not row["path"].startswith("constant/polyMesh/")],key=lambda row:row["path"]),
            "resumeCheckpointSha256":checkpoint_packet["checkpointSha256"],"initialization":"Exact complete native checkpoint; constant reference points remain original.",
            "restartVerified":False,"simulationExecuted":False}
        write(destination,"stage4-branch-preparation.json",phase.base.inventory.bounded_json(resumed_prepared),written)
        write(destination,"stage4-checkpoint.json",phase.base.inventory.bounded_json(checkpoint_packet),written)
        require(build_checkpoint(root,variant,checkpoint_packet["nativeTimeDirectory"])==checkpoint_packet,"Source checkpoint changed during copying.")
        report={"schemaVersion":RESUME_SCHEMA,"status":"COMPLETE_NATIVE_RESTART_PREPARED_EXECUTION_REQUIRED", "variant":resumed,
            "checkpointSha256":checkpoint_packet["checkpointSha256"],"copiedCheckpointFiles":checkpoint_packet["files"],
            "referenceMeshFiles":mesh,"authoredInputFiles":resumed_prepared["authoredInputFiles"],"controls":prepared["controls"],
            "constantReferencePointsPreserved":True,"movedCheckpointPointsPreserved":True,"nativeUniformTimePreserved":True,
            "sourceCheckpointImmutable":True,"actualCfdRunVerified":False,"restartVerified":False,"mainApplicationWrites":False,
            "claimBoundary":"Actual complete restart inputs copied without replacing constant reference geometry; native resume execution and matched-field equivalence remain required."}
        write(destination,"stage4-resume-preparation.json",phase.base.inventory.bounded_json(report),written)
        require(sum(p.stat().st_size for p in written) <= MAX_BRANCH_BYTES,"The complete resumed case including reports exceeds512MiB.")
        return report
    except Exception:
        cleanup(destination,written)
        raise
