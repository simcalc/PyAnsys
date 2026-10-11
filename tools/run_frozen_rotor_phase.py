"""Execute only authored Foundation-12 phase-change cases on the FREE runner.

Native execution, finite end fields, actual rotor motion and research
qualification are separate facts. This adapter never qualifies NPSH3, a
pressure campaign, time convergence, restart or an experimental pump model.
"""
from __future__ import annotations

import argparse
from array import array
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_cavitation_case as preparation
import frozen_rotor_native_reduction as reduction

SCHEMA = "untirta.frozen-rotor-phase-change-execution.v1"
MAX_SECONDS = 1200
MAX_WORKSPACE = 512 * 1024**2
MAX_LOG = 8 * 1024**2
MAX_PATHS = 1024
MAX_SAMPLER_BYTES = 4 * 1024**2
MAX_MEMORY = 2 * 1024**3
MAX_CELL_COUNT = 2_000_000  # Independent OpenFOAM bound, not an Ansys Student licence limit.
MAX_POINT_COUNT = 2_000_000
PROGRAMS = ("checkMesh", "foamRun")
END_FIELDS = ("p", "p_rgh", "U", "alpha.water", "k", "omega", "nut")
FIELD_DIMENSIONS = {"p": "1 -1 -2 0 0 0 0", "p_rgh": "1 -1 -2 0 0 0 0",
                    "U": "0 1 -1 0 0 0 0", "alpha.water": "0 0 0 0 0 0 0",
                    "k": "0 2 -2 0 0 0 0", "omega": "0 0 -1 0 0 0 0", "nut": "0 2 -1 0 0 0 0"}
TIME_NAME = r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:e[-+]?[0-9]+)?"
CHILD_ENV_KEYS = ("PATH", "LD_LIBRARY_PATH", "LANG", "LC_ALL", "TMPDIR", "RUNNER_TEMP",
                  "WM_PROJECT", "WM_PROJECT_VERSION", "WM_PROJECT_DIR", "WM_PROJECT_INST_DIR",
                  "WM_OPTIONS", "WM_COMPILER", "WM_LABEL_SIZE", "WM_PRECISION_OPTION",
                  "FOAM_APPBIN", "FOAM_LIBBIN", "FOAM_ETC", "FOAM_API", "FOAM_SIGFPE", "FOAM_SETNAN")


class PhaseExecutionIssue(ValueError):
    pass


def require(condition, action):
    if not condition:
        raise PhaseExecutionIssue(action)


def child_environment():
    """Pass native runtime paths, never GitHub/Cloudflare/Fluent credentials."""
    values = {key: os.environ[key] for key in CHILD_ENV_KEYS if key in os.environ}
    require(all(len(value) <= 8192 for value in values.values()), "Native environment paths exceed the bounded profile.")
    values.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
    return values


def context_guard():
    expected = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "simcalc/PyAnsys",
                "GITHUB_EVENT_NAME": "workflow_dispatch", "RUNNER_OS": "Linux", "RUNNER_ENVIRONMENT": "github-hosted",
                "WM_PROJECT_VERSION": "12", "WM_PROJECT_DIR": "/opt/openfoam12"}
    require(sys.platform == "linux" and all(os.environ.get(key) == value for key, value in expected.items()),
            "Use only the independent public standard GitHub Linux worker with the official Foundation-12 environment.")
    require(os.environ.get("GITHUB_REPOSITORY_VISIBILITY", "public") == "public",
            "The FREE phase worker must remain in the public repository; verify public_runner_guard in its workflow first.")


def program_identity():
    package = subprocess.run(["dpkg-query", "--show", "--showformat=${Version}", "openfoam12"],
                             env=child_environment(), capture_output=True, text=True, timeout=10, check=True)
    require(package.stdout == "20250206", "Use the exact checksum-pinned Foundation-12 package 20250206.")
    binaries = []
    for name in PROGRAMS:
        executable = shutil.which(name)
        require(executable is not None, "An actual official native program is absent: " + name)
        path = Path(executable).resolve()
        require(path.is_relative_to(Path("/opt/openfoam12")), "Native programs must resolve inside the official Foundation-12 installation.")
        raw = preparation.base.read_regular(path, preparation.MAX_FILE_BYTES)
        binaries.append({"program": name, "path": str(path), "bytes": len(raw), "sha256": sha256(raw).hexdigest()})
    return {"schemaVersion": "untirta.frozen-rotor-phase-runtime-identity.v1", "openFoamFoundationVersion": 12,
            "packageVersion": package.stdout, "officialDebSha256": "b3dcd9a636a5a827bf43ffcf0156922ba6db492123f035eae1046ee759602dd1",
            "binaries": binaries, "mainApplicationWrites": False}


def commands(directory):
    case = str(Path(directory).absolute() / "case")
    return [["checkMesh", "-case", case, "-allGeometry", "-allTopology"], ["foamRun", "-case", case]]


def workspace_size(directory):
    root = preparation.base.regular_directory(directory)
    total, count = 0, 0
    for path in root.rglob("*"):
        count += 1
        require(count <= MAX_PATHS and len(path.relative_to(root).parts) <= 8,
                "The phase workspace exceeds its bounded file/depth inventory.")
        require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)(),
                "Native execution paths cannot contain symbolic links or junctions.")
        if path.is_file():
            total += path.stat().st_size
            require(total <= MAX_WORKSPACE, "The native phase workspace exceeds 512 MiB.")
    return total


def stop_owned(process):
    """The caller owns exactly this fresh Popen session, never an existing PID."""
    if process.poll() is not None:
        return
    try:
        require(os.getpgid(process.pid) == process.pid, "The native process no longer owns its fresh isolated process group.")
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait(timeout=3)
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            require(os.getpgid(process.pid) == process.pid, "Only the original native process group may receive the bounded stop signal.")
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)


def child_limits():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY, MAX_MEMORY))


def execute(arguments, log_path, workspace, deadline, runtime):
    """Capture at most 8 MiB even when a native process prints and exits quickly."""
    require(arguments in commands(workspace), "Execute only this preparer's fixed checkMesh and foamRun command arguments.")
    name = arguments[0]
    expected_log = "00-checkMesh.log" if name == "checkMesh" else "01-foamRun.log"
    require(Path(log_path).absolute() == Path(workspace).absolute() / "logs" / expected_log,
            "Native logs must use the two fixed independent output paths.")
    preparation.base.regular_directory(Path(log_path).parent)
    require(not Path(log_path).exists() and not Path(log_path).is_symlink(), "A native log is immutable once written.")
    row = next((item for item in runtime["binaries"] if item["program"] == name), None)
    require(row is not None, "An actual native program identity is required before execution.")
    executable = Path(row["path"])
    require(executable.resolve().is_relative_to(Path("/opt/openfoam12")), "Use only the hash-bound official native binary.")
    raw = preparation.base.read_regular(executable, preparation.MAX_FILE_BYTES)
    require(len(raw) == row["bytes"] and sha256(raw).hexdigest() == row["sha256"], "Native executable bytes changed after binding.")
    require(time.monotonic() < deadline, "The native wall-clock deadline was already reached before startup.")
    workspace_size(workspace)
    started, total, process = time.monotonic(), 0, None
    selector = selectors.DefaultSelector()
    try:
        with Path(log_path).open("xb") as output:
            process = subprocess.Popen([str(executable), *arguments[1:]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       env=child_environment(), start_new_session=True, preexec_fn=child_limits, bufsize=0)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                require(time.monotonic() < deadline, "The bounded native phase execution timed out.")
                workspace_size(workspace)
                for key, _ in selector.select(timeout=.25):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining = MAX_LOG - total
                    if remaining:
                        output.write(chunk[:remaining])
                        output.flush()
                        total += min(len(chunk), remaining)
                    require(len(chunk) <= remaining, "The native phase log reached its 8 MiB bound.")
            code = process.wait(timeout=3)
            workspace_size(workspace)
            require(time.monotonic() < deadline, "The native deadline was reached at process completion.")
            require(code == 0, name + " returned native exit " + str(code))
    finally:
        if process is not None:
            stop_owned(process)
            process.stdout.close()
        selector.close()
    raw = preparation.base.read_regular(log_path, MAX_LOG)
    text = raw.decode("utf-8", errors="strict")
    require(re.search(r"(?m)^End\s*$", text) and not re.search(r"FOAM FATAL|Segmentation fault|Floating point exception", text, re.I),
            "Actual native completion without a fatal error is required: " + name)
    return {"program": name, "exitCode": 0, "elapsedSeconds": round(time.monotonic() - started, 3),
            "path": "logs/" + expected_log, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}, text


def finite_field(raw, name, cell_count):
    """Read every stored internal value and reject nonfinite boundary literals too."""
    require(name in FIELD_DIMENSIONS and isinstance(cell_count, int) and not isinstance(cell_count, bool) and 0 < cell_count <= MAX_CELL_COUNT,
            "Use a supported native phase field and an actual bounded cell count.")
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw.decode("ascii"), flags=re.S)
    kind = "volVectorField" if name == "U" else "volScalarField"
    require(re.search(r"\bformat\s+ascii\s*;", text) and re.search(r"\bobject\s+" + re.escape(name) + r"\s*;", text)
            and re.search(r"\bclass\s+" + kind + r"\s*;", text)
            and re.search(r"\bdimensions\s*\[\s*" + FIELD_DIMENSIONS[name] + r"\s*\]\s*;", text)
            and not re.search(r"\b(?:nan|inf|infinity)\b|^\s*#|\bcodeStream\b", text, re.I | re.M),
            "Native ASCII field metadata, dimensions and non-executable finite values must match its role: " + name)
    match = re.search(r"\binternalField\s+(uniform\s+([^;]+)|nonuniform\s+List<(scalar|vector)>\s+(\d+)\s*\((.*?)\))\s*;", text, re.S)
    require(match is not None, "Every final native phase field requires an explicit internal value list.")
    vector = name == "U"
    source = match[2] if match[2] is not None else match[5]
    if match[2] is None:
        require(int(match[4]) == cell_count and match[3] == ("vector" if vector else "scalar"), "Native final field count must match the actual mesh cells.")
    if vector:
        vector_pattern = r"\(\s*" + reduction.FLOAT + r"\s+" + reduction.FLOAT + r"\s+" + reduction.FLOAT + r"\s*\)"
        groups = list(re.finditer(vector_pattern, source))
        require(len(groups) == (1 if match[2] is not None else cell_count)
                and not re.sub(vector_pattern, "", source).strip(), "Every native velocity value needs an explicit three-component vector.")
    else:
        require("(" not in source and ")" not in source, "Native scalar values cannot contain vector delimiters.")
    tokens = re.sub(r"[()]", " ", source).split()
    expected = (3 if vector else 1) * (1 if match[2] is not None else cell_count)
    require(len(tokens) == expected and all(re.fullmatch(reduction.FLOAT, value) for value in tokens), "Native internal values must have the exact scalar/vector shape.")
    values = [float(value) for value in tokens]
    require(all(math.isfinite(value) for value in values), "All actual native phase-field values must be finite.")
    require(all(math.isfinite(float(value[0])) for value in re.finditer(reduction.FLOAT, text)), "Overflow in native boundary numeric literals is prohibited.")
    if name in {"k", "omega", "nut"}:
        require(min(values) >= 0, "Native turbulence variables must remain nonnegative.")
    if name == "alpha.water":
        require(min(values) >= -1e-9 and max(values) <= 1 + 1e-9, "The native internal liquid fraction must remain within [0,1] to explicit 1e-9 numerical tolerance.")
    return {"name": name, "internalValueCount": len(values), "uniform": match[2] is not None,
            "minimum": min(values), "maximum": max(values), "allInternalValuesChecked": True,
            "allNumericLiteralsFinite": True, "boundaryPhaseFractionBoundsVerified": False}


def final_fields(directory, plan, cell_count, log_text):
    case = preparation.base.regular_directory(directory / "case")
    times = []
    for path in case.iterdir():
        if re.fullmatch(TIME_NAME, path.name) and float(path.name) > 0:
            preparation.base.regular_directory(path)
            times.append((float(path.name), path))
            require(len(times) <= 64, "The actual time-directory inventory must remain bounded.")
    require(times, "A completed phase solver must write actual positive-time fields.")
    latest, endpoint = max(times, key=lambda pair: pair[0])
    expected = plan["endTimeS"]
    log_times = [float(match[1]) for match in re.finditer(r"(?m)^Time = (" + reduction.FLOAT + r")s?\s*$", log_text)]
    require(math.isclose(latest, expected, rel_tol=5e-11, abs_tol=1e-12) and log_times
            and math.isclose(log_times[-1], expected, rel_tol=5e-11, abs_tol=1e-12),
            "Actual native fields and solver clock must reach the authored physical end time.")
    rows = []
    for name in END_FIELDS:
        raw = preparation.base.read_regular(endpoint / name, preparation.MAX_FILE_BYTES)
        rows.append({"path": "case/" + endpoint.name + "/" + name, "bytes": len(raw), "sha256": sha256(raw).hexdigest(),
                     "fieldCheck": finite_field(raw, name, cell_count)})
    return rows, sorted(times, key=lambda pair: pair[0])


def points(path):
    count, body = preparation.base.native_list(path)
    vectors = re.findall(r"\(([^()]*)\)", body)
    require(0 < count <= MAX_POINT_COUNT and len(vectors) == count and not re.sub(r"\([^()]*\)", "", body).strip(),
            "Actual moved/native points need the exact bounded coordinate count and no trailing payload.")
    values = [tuple(float(value) for value in row.split()) for row in vectors]
    require(all(len(row) == 3 and all(math.isfinite(value) for value in row) for row in values), "Every actual native/moved point must have three finite coordinates.")
    return values


def motion_inputs(directory):
    """Bind current reference point labels and the exact supported motion recipe.

    Labels come from actual faces/owner/neighbour/cellZones, never a component
    offset, coordinate sort or guessed radius. Upstream numerical qualification
    remains the separate reducer's responsibility.
    """
    import frozen_rotor_native_quality as quality
    root = preparation.base.regular_directory(directory)
    choices = [("phase-change-preparation.json", preparation.SCHEMA, "phase-input-manifest.json",
                "untirta.frozen-rotor-phase-input-manifest.v1", "phase-classroom-study.json", "preservedNativeMeshFiles"),
               ("stage4-branch-preparation.json", "untirta.frozen-rotor-stage4-branch-preparation.v1", "stage4-input-manifest.json",
                "untirta.frozen-rotor-stage4-input-manifest.v1", "stage4-classroom-study.json", "referenceMeshFiles")]
    chosen = [row for row in choices if (root/row[0]).exists()]
    require(len(chosen) == 1, "Exactly one current supported sliding preparation must bind actual point motion.")
    prep_name, prep_schema, manifest_name, manifest_schema, study_name, mesh_key = chosen[0]
    records = []
    def read(relative, limit=preparation.MAX_FILE_BYTES):
        preparation.relative_path(relative)
        target = root/relative
        for item in (target, *target.parents):
            require(not item.is_symlink() and not getattr(item, "is_junction", lambda: False)(), "Motion evidence cannot contain links/junctions.")
        raw = preparation.base.read_regular(target, limit)
        records.append({"path":relative,"bytes":len(raw),"sha256":sha256(raw).hexdigest()})
        return raw
    prep_raw = read(prep_name, preparation.base.inventory.MAX_REPORT_BYTES)
    prep = preparation.json_file(root/prep_name)
    manifest_raw = read(manifest_name, preparation.base.inventory.MAX_REPORT_BYTES)
    manifest = preparation.json_file(root/manifest_name)
    study_raw = read(study_name, preparation.base.inventory.MAX_REPORT_BYTES)
    study = preparation.json_file(root/study_name)
    declared = preparation.protocol.prepare_study(inputs=prep.get("declaredStudyInputs"), gravity_in_solver=False)
    require(declared["input"].get("initialAngleRad") == 0, "This exact native motion recipe supports only an explicitly declared zero initial rotor phase.")
    require(prep.get("schemaVersion") == prep_schema and prep.get("mainApplicationWrites") is False
            and manifest.get("schemaVersion") == manifest_schema and manifest.get("mode") == "transient-sliding"
            and manifest.get("preparationReportSha256") == sha256(prep_raw).hexdigest()
            and prep.get("physicalBasis") == manifest.get("physicalBasis") == study.get("physicalBasis") == declared["physicalBasis"]
            and manifest.get("studyInputs") == study.get("input") == prep.get("declaredStudyInputs") == declared["input"],
            "Current source-bound study, fixed +z origin and sliding preparation/manifest must agree.")
    plan = manifest.get("temporalPlan", {})
    start = preparation.protocol.number(plan.get("initialTimeS", 0), nonnegative=True)
    end = preparation.protocol.number(plan.get("endTimeS"), positive=True)
    speed = declared["input"]["speedRpm"]
    require(start < end <= 10*(60/speed)*(1+1e-12) and math.isclose(plan.get("rotationPeriodS", 0), 60/speed, rel_tol=1e-12), "Actual motion uses the bound global physical-time domain and declared RPM within the supported ten-revolution trial.")
    bound = {row.get("path"):row for row in manifest.get("files", [])}
    require(len(bound) == len(manifest.get("files", [])), "Motion manifest paths must be unique.")
    def expected_record(relative, row):
        actual = records[-1]
        require(row and row.get("bytes") == actual["bytes"] and row.get("sha256") == actual["sha256"], "Current motion input differs from its source-bound SHA256: "+relative)
        current = bound.get(relative)
        require(current and current.get("bytes") == actual["bytes"] and current.get("sha256") == actual["sha256"], "Current native input manifest must bind every motion input: "+relative)
    mesh_rows = prep.get(mesh_key, [])
    require(isinstance(mesh_rows, list) and len(mesh_rows) == 6 and {row.get("path") for row in mesh_rows} == {"constant/polyMesh/"+name for name in preparation.MESH_FILES}, "All six immutable reference mesh files are required for point-label motion proof.")
    mesh_raw = {}
    for row in sorted(mesh_rows, key=lambda value:value["path"]):
        relative = "case/"+row["path"]; mesh_raw[Path(relative).name] = read(relative); expected_record(relative, row)
    authored = {row.get("path"):row for row in prep.get("authoredInputFiles", [])}
    for name in ("constant/dynamicMeshDict", "system/controlDict"):
        raw = read("case/"+name); expected_record("case/"+name, authored.get(name))
        if name.endswith("dynamicMeshDict"):
            expected = preparation.base.foam_header("dynamicMeshDict", location="constant") + 'mover {type motionSolver; libs ("libfvMotionSolvers.so"); motionSolver solidBody; cellZone rotating-fluid; solidBodyMotionFunction rotatingMotion; origin (0 0 0); axis (0 0 1); omega '+format(speed, ".17g")+' [rpm];}\n'
            require(raw == expected.encode("ascii") and not (root/"case/constant/MRFProperties").exists(), "Use only the exact source-bound +z, zero-origin, zero-initial-phase rotating recipe; double MRF rotation is prohibited.")
        else:
            text = raw.decode("ascii")
            for key, value in (("startTime", start), ("endTime", end)):
                values = re.findall(r"(?m)^"+key+r"\s+("+reduction.FLOAT+r");",text)
                require(len(values) == 1 and float(values[0]) == value, "Actual motion clock differs from its authored control: "+key)
    geometry_raw = read("geometry-identity.json", preparation.base.inventory.MAX_REPORT_BYTES)
    geometry = preparation.json_file(root/"geometry-identity.json")
    geometry_row = records[-1]
    source_geometry = prep.get("sourceGeometryIdentityFile", {})
    require(geometry.get("schemaVersion") == "untirta.frozen-rotor-fixed-geometry-identity.v1"
            and geometry.get("exampleDataCommit") == preparation.base.inventory.PIN and geometry.get("mainApplicationWrites") is False
            and source_geometry.get("path", source_geometry.get("name")) == "geometry-identity.json"
            and source_geometry.get("bytes") == len(geometry_raw) and source_geometry.get("sha256") == geometry_row["sha256"]
            and prep.get("sourceGeometryControlSha256") == sha256(preparation.canonical([geometry_row])).hexdigest(), "The original source-pinned geometry identity must bind motion labels unchanged.")
    expected_record("geometry-identity.json", source_geometry)
    original = points(root/"case/constant/polyMesh/points")
    def labels(name):
        body = quality.native_body(mesh_raw[name], name, {"labelList"})
        match = re.fullmatch(r"(\d+)\s*\(([^()]*)\)\s*;?",body,re.S)
        require(match, "Actual motion owner/neighbour labels require a complete counted native list.")
        tokens = match[2].split(); count = int(match[1])
        require(count <= 6*MAX_CELL_COUNT and len(tokens) == count and all(re.fullmatch(r"\d+",token) and int(token) < MAX_CELL_COUNT for token in tokens), "Motion topology labels must be bounded complete nonnegative integers.")
        return array("I", (int(token) for token in tokens))
    owner, neighbour = labels("owner"), labels("neighbour")
    require(owner and len(neighbour) <= len(owner), "Complete current owner/neighbour topology is required.")
    cells = max(max(owner), max(neighbour, default=0))+1
    require(0 < cells <= MAX_CELL_COUNT and all(a != b for a,b in zip(owner,neighbour)), "Actual face cell labels must fit the bounded nondegenerate fluid domain.")
    zones = quality.zones(mesh_raw["cellZones"], cells)
    rotating, stationary = zones["rotating-fluid"], zones["stationary-fluid"]
    masks, stator_masks, used = bytearray(len(original)), bytearray(len(original)), bytearray(len(original))
    faces = quality.native_body(mesh_raw["faces"], "faces", {"faceList"})
    match = re.fullmatch(r"(\d+)\s*\((.*)\)\s*;?",faces,re.S)
    require(match and int(match[1]) == len(owner), "Every actual native face must bind one owner label.")
    previous, count = 0, 0
    for face in re.finditer(r"(\d+)\s*\(([^()]*)\)", match[2]):
        require(not match[2][previous:face.start()].strip() and count < len(owner), "Complete actual native face ordering is required.")
        tokens = face[2].split(); ids = [int(token) for token in tokens if re.fullmatch(r"\d+",token)]
        require(3 <= len(ids) <= 64 and len(ids) == int(face[1]) == len(tokens) and len(set(ids)) == len(ids)
                and all(0 <= value < len(original) for value in ids), "Motion faces require distinct bounded native point labels.")
        selected = owner[count] in rotating or count < len(neighbour) and neighbour[count] in rotating
        stationary_selected = owner[count] in stationary or count < len(neighbour) and neighbour[count] in stationary
        for point in ids:
            used[point] = 1
            if selected:masks[point] = 1
            if stationary_selected:stator_masks[point] = 1
        previous, count = face.end(), count+1
    require(count == len(owner) and not match[2][previous:].strip() and all(used) and 0 < sum(masks) < len(original), "Both moving and stationary native point sets must be complete and nonempty.")
    require(all((rotor+stator) == 1 for rotor,stator in zip(masks,stator_masks)), "This NCC sliding recipe requires explicitly disjoint rotor/stator cell-point adjacency; shared motion topology is unsupported.")
    checkpoint_point = None
    if start > 0:
        require(prep_schema == "untirta.frozen-rotor-stage4-branch-preparation.v1", "Only a source-bound Stage4 checkpoint can establish a resumed motion clock.")
        read("stage4-checkpoint.json", preparation.base.inventory.MAX_REPORT_BYTES)
        checkpoint = preparation.json_file(root/"stage4-checkpoint.json")
        files = checkpoint.get("files", [])
        require(checkpoint.get("schemaVersion") == "untirta.frozen-rotor-stage4-native-checkpoint.v1"
                and checkpoint.get("physicalTimeS") == start and checkpoint.get("mainApplicationWrites") is False
                and isinstance(files,list) and 0 < len(files) <= 64
                and sha256(preparation.canonical(files)).hexdigest() == checkpoint.get("checkpointSha256") == prep.get("resumeCheckpointSha256"),
                "The resumed motion clock requires the exact hash-bound native checkpoint.")
        matches = [row for row in files if isinstance(row,dict) and re.fullmatch(r"case/"+TIME_NAME+r"/polyMesh/points",row.get("path",""))
                   and math.isclose(float(row["path"].split("/")[1]),start,rel_tol=0,abs_tol=1e-12)]
        require(len(matches) == 1, "The initial resumed moving points must be bound to one exact checkpoint file.")
        checkpoint_point = matches[0]
    return {"root":root,"points":original,"rotating":masks,"stationary":stator_masks,"records":records,"start":start,"end":end,"speedRpm":speed,"checkpointPoint":checkpoint_point,
            "pointLabelsSha256":sha256(bytes(masks)).hexdigest(),"statorLabelsSha256":sha256(bytes(stator_masks)).hexdigest(),"manifestSha256":sha256(manifest_raw).hexdigest(),"studySha256":sha256(study_raw).hexdigest()}


def rotor_motion(directory, time_directories, log_text):
    """Compare every bound rotor/stator point to its global-clock rigid motion."""
    require(re.search(r"Selecting solidBodyMotionFunction rotatingMotion", log_text), "The native solver must select actual rotatingMotion.")
    inputs = motion_inputs(directory)
    original, rotating, root = inputs["points"], inputs["rotating"], inputs["root"]
    bounds = [(min(point[axis] for point in original), max(point[axis] for point in original)) for axis in range(3)]
    scale = math.hypot(*(high - low for low, high in bounds))
    require(math.isfinite(scale) and scale > 0, "The reference mesh must span a real three-dimensional coordinate scale.")
    absolute, relative = 1e-11, 1e-10
    tolerance = absolute + relative*scale
    logged = [float(value) for value in re.findall(r"(?m)^Time = ("+reduction.FLOAT+r")s?\s*$",log_text)]
    require(isinstance(time_directories, (list,tuple)) and 0 < len(time_directories) <= 64 and logged, "Current moved-point evidence needs actual bounded solver-clock records.")
    evidence, maximum, previous_time = [], 0., -1.
    for physical_time, path in time_directories:
        require(type(physical_time) in (int,float) and math.isfinite(physical_time) and previous_time < physical_time
                and inputs["start"] <= physical_time <= inputs["end"]+1e-12 and physical_time > 0,
                "Moved-point times must be unique, ordered and inside the bound physical-time domain.")
        path = Path(path).absolute()
        initial_checkpoint = physical_time == inputs["start"] and inputs["checkpointPoint"] is not None
        require(path.parent == root/"case" and re.fullmatch(TIME_NAME,path.name)
                and math.isclose(float(path.name),physical_time,rel_tol=5e-11,abs_tol=1e-12)
                and (initial_checkpoint or any(math.isclose(value,physical_time,rel_tol=5e-11,abs_tol=1e-12) for value in logged)), "Every moved-point file must match its current local path and actual native physical clock.")
        previous_time = physical_time
        source = path / "polyMesh/points"
        require(source.exists(), "Every supplied native write time must contain its actual moved-point evidence.")
        for item in (source,*source.parents):
            require(not item.is_symlink() and not getattr(item,"is_junction",lambda:False)(), "Moved-point paths cannot contain links/junctions.")
        raw = preparation.base.read_regular(source, preparation.MAX_FILE_BYTES)
        if initial_checkpoint:
            row = inputs["checkpointPoint"]
            require(row.get("path") == "case/"+path.name+"/polyMesh/points" and row.get("bytes") == len(raw)
                    and row.get("sha256") == sha256(raw).hexdigest(), "Initial resumed point bytes must equal the immutable copied checkpoint.")
        values = points(source)
        require(len(values) == len(original), "A fixed-topology rotor motion must retain every reference point index.")
        displacement = max(math.dist(before, after) for before, after in zip(original, values))
        angle = 2*math.pi*inputs["speedRpm"]*physical_time/60
        sine, cosine = math.sin(angle), math.cos(angle)
        rotor_error, stator_error = 0., 0.
        for index, (before, after) in enumerate(zip(original,values)):
            expected = (cosine*before[0]-sine*before[1],sine*before[0]+cosine*before[1],before[2]) if rotating[index] else before
            error = math.dist(expected,after)
            if rotating[index]:rotor_error = max(rotor_error,error)
            else:stator_error = max(stator_error,error)
        require(rotor_error <= tolerance and stator_error <= tolerance, "Actual rotor/stator coordinates violate the source-bound Rz(omega*t)x0 motion or stationary point identity.")
        require(preparation.base.read_regular(source, preparation.MAX_FILE_BYTES) == raw, "Moved-point evidence changed during comparison.")
        evidence.append({"path": "case/" + path.name + "/polyMesh/points", "bytes": len(raw), "sha256": sha256(raw).hexdigest(),
                         "physicalTimeS": physical_time, "rotationAngleRad":angle,"maximumRotorErrorNativeUnits":rotor_error,
                         "maximumStatorErrorNativeUnits":stator_error,"maximumPointDisplacementAssumedM": displacement, "allPointCoordinatesChecked": True})
        maximum = max(maximum, displacement)
    post_start_writes = sum(row["physicalTimeS"] > inputs["start"] for row in evidence)
    require(post_start_writes > 0, "Actual motion proof requires a new logged post-start point write; a copied checkpoint alone is insufficient.")
    require(evidence and maximum > tolerance, "Physical sliding execution requires actual moved points beyond coordinate-rounding tolerance.")
    for row in inputs["records"]:
        raw = preparation.base.read_regular(root/row["path"], preparation.MAX_FILE_BYTES)
        require(len(raw) == row["bytes"] and sha256(raw).hexdigest() == row["sha256"], "Source-bound motion inputs changed during verification.")
    return {"nativeRotatingMotionSelected": True, "nativeMovedPointsObserved": True, "nativeCoordinateUnitsVerified": False,
            "classroomMetresPerSourceCoordinateUnit": 1, "referenceDiagonalAssumedM": scale,
            "coordinateRoundoffRelativeTolerance": relative,"coordinateRoundoffAbsoluteToleranceNativeUnits":absolute,
            "comparisonToleranceNativeUnits":tolerance,"rotationAxis":[0,0,1],"rotationOriginNativeUnits":[0,0,0],"initialRotorAngleRad":0,
            "motionEquation":"x_rotor(t)=Rz(2*pi*RPM*t/60)*x0; x_stator(t)=x0; global t is not reset at restart",
            "rotorPointCount":sum(rotating),"statorPointCount":sum(inputs["stationary"]),"sourcePointLabelsSha256":inputs["pointLabelsSha256"],
            "sourceStatorPointLabelsSha256":inputs["statorLabelsSha256"],"rotorStatorPointAdjacencyDisjointVerified":True,
            "motionInputRecords":inputs["records"],"inputManifestSha256":inputs["manifestSha256"],"studySha256":inputs["studySha256"],
            "rigidRotorMotionVerified":True,"stationaryPointIdentityVerified":True,"nativeMovedPointRecords": evidence,
            "nativePostStartPointWriteCount":post_start_writes,
            "actualWallVelocityChecked":False,"actualMeshFluxChecked":False,"fullStateRestartVerified":False,
            "interfaceCoverageAtAllRotorAnglesVerified": False, "periodicTransientQualified": False}


def sampling_records(directory):
    root = directory / "case/postProcessing"
    if not root.exists():
        return []
    preparation.base.regular_directory(root)
    result = []
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)(), "Native sampler paths cannot contain links.")
        if path.is_file():
            relative = preparation.relative_path(path.relative_to(directory).as_posix())
            match = re.fullmatch(r"case/postProcessing/([A-Za-z][A-Za-z0-9]*)/" + TIME_NAME + r"/[A-Za-z][A-Za-z0-9]*\.dat", relative)
            authored_names = set(re.findall(r"(?m)^([A-Za-z][A-Za-z0-9]*) \{", preparation.phase_functions()))
            require(match and match[1] in authored_names,
                    "Retain only actual numeric sampler .dat files from authored function objects.")
            require(len(result) < 64, "The actual sampler record inventory must remain bounded.")
            raw = preparation.base.read_regular(path, MAX_SAMPLER_BYTES)
            result.append({"path": relative, "bytes": len(raw), "sha256": sha256(raw).hexdigest()})
    return result


def bound_inputs(directory, report):
    authored = preparation.file_records(directory / "case", report["authoredInputFiles"], {row["path"] for row in report["authoredInputFiles"]})
    mesh = preparation.file_records(directory / "case", report["preservedNativeMeshFiles"], {row["path"] for row in report["preservedNativeMeshFiles"]}, plain_native=True)
    return {"authoredInputFiles": authored, "preservedNativeMeshFiles": mesh}


def phase_manifest(directory):
    """The separate reducer owns normalized phase-family identities, never execution."""
    import frozen_rotor_phase_reduction
    return frozen_rotor_phase_reduction.build_manifest(directory)


def run(baseline_directory, packet, output_directory, *, pressure_absolute_pa, mode, dt_s, duration_s):
    context_guard()
    require(isinstance(packet, dict) and packet.get("schemaVersion") == preparation.NATIVE_REDUCTION_SCHEMA,
            "Execute only a case prepared from the actual qualified native reducer packet; synthetic wrappers are not an execution input.")
    runtime = program_identity()
    prepared = preparation.prepare(baseline_directory, packet, output_directory, pressure_absolute_pa=pressure_absolute_pa,
                                   mode=mode, dt_s=dt_s, duration_s=duration_s)
    directory = preparation.base.regular_directory(output_directory)
    expected_commands = commands(directory)
    require(prepared["nativeCommandArguments"] == expected_commands, "The prepared native command contract must match the fixed adapter exactly.")
    inputs = bound_inputs(directory, prepared)
    prep_raw = preparation.base.read_regular(directory / "phase-change-preparation.json", preparation.base.inventory.MAX_REPORT_BYTES)
    runtime_raw = preparation.base.inventory.bounded_json(runtime)
    preparation.base.write_exclusive(directory / "phase-runtime-identity.json", runtime_raw)
    family = phase_manifest(directory)
    manifest_raw, study_raw = preparation.canonical(family["manifest"]), preparation.canonical(family["study"])
    require(len(manifest_raw) <= preparation.base.inventory.MAX_REPORT_BYTES and len(study_raw) <= preparation.base.inventory.MAX_REPORT_BYTES,
            "The phase-family input manifest and study must remain within their exact bounded JSON profiles.")
    preparation.base.write_exclusive(directory / "phase-input-manifest.json", manifest_raw)
    preparation.base.write_exclusive(directory / "phase-classroom-study.json", study_raw)
    artifacts = {"phase-change-preparation.json": prep_raw, "phase-runtime-identity.json": runtime_raw,
                 "phase-input-manifest.json": manifest_raw, "phase-classroom-study.json": study_raw}
    if "sourceGeometryIdentityFile" in prepared:
        geometry = preparation.base.read_regular(directory / "geometry-identity.json", preparation.base.inventory.MAX_REPORT_BYTES)
        require(sha256(geometry).hexdigest() == prepared["sourceGeometryIdentityFile"]["sha256"], "The phase source geometry identity must match its immutable preparation record.")
        artifacts["geometry-identity.json"] = geometry
    artifact_records = [{"path": name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()} for name, raw in sorted(artifacts.items())]
    report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(), "status": "NATIVE_PHASE_EXECUTION_REQUIRED",
              "mode": mode, "exampleDataCommit": preparation.base.inventory.PIN, "openFoamFoundationVersion": 12,
              "preparationReportSha256": sha256(prep_raw).hexdigest(), "authoredCavitationModelSha256": prepared["authoredCavitationModelSha256"],
              "phaseRuntimeIdentitySha256": sha256(runtime_raw).hexdigest(), "phaseInputManifestSha256": sha256(manifest_raw).hexdigest(),
              "phaseStudySha256": sha256(study_raw).hexdigest(), "boundPreparationArtifacts": artifact_records,
              "sourceBaselineEvidenceSha256": prepared["sourceBaselineEvidenceSha256"], "temporalPlan": prepared["temporalPlan"],
              "inletAbsoluteTotalPressurePa": prepared["inletAbsoluteTotalPressurePa"], "limits": {"cpus": 1, "memoryBytes": MAX_MEMORY,
              "maximumSeconds": MAX_SECONDS, "maximumWorkspaceBytes": MAX_WORKSPACE, "maximumLogBytesPerProgram": MAX_LOG,
              "enforcement": "Serial native commands, per-child address-space limit and owned-process time/log/workspace watchdog; aggregate memory/CPU cgroup belongs to the independent workflow."},
              "stages": [], "nativeCheckMeshVerified": False, "nativeSolverCompleted": False, "cavitationModelExecuted": False,
              "finiteNativeEndFieldsVerified": False, "physicalTransientExecuted": False, "stationaryMrfRelaxationExecuted": False,
              "simulationExecuted": False, "actualCfdRunVerified": False, "qualifiedBaseline": False, "phaseFamilyReferenceQualified": False,
              "cavitationSweepExecuted": False, "npsh3Verified": False, "restartVerified": False, "timeStepConvergenceVerified": False,
              "inputArtifactsImmutable": False, "sourceBaselineImmutable": False, "mainApplicationWrites": False,
              "claimBoundary": "Actual native execution and finite end-field facts only. Conservation, residuals, stationarity/periodicity, phase-family reference, liquid-section qualification, NPSH3, mesh/time convergence and restart remain separate research gates."}
    for env, key, pattern in (("GITHUB_RUN_ID", "githubRunId", r"[0-9]{1,24}"), ("GITHUB_SHA", "githubSourceCommit", r"[0-9a-f]{40}")):
        value = os.environ.get(env, "")
        report[key] = value if re.fullmatch(pattern, value) else None
    logs = directory / "logs"
    logs.mkdir()
    started, current = time.monotonic(), "checkMesh"
    try:
        require(time.monotonic() - started < MAX_SECONDS, "Native work must start inside its fixed wall-clock budget.")
        stage, check_text = execute(expected_commands[0], logs / "00-checkMesh.log", directory, started + MAX_SECONDS, runtime)
        report["stages"].append(stage)
        mesh = reduction.parse_mesh_quality(check_text)
        report.update(nativeCheckMeshVerified=True, nativeMeshQuality=mesh)
        current = "foamRun"
        stage, solver_text = execute(expected_commands[1], logs / "01-foamRun.log", directory, started + MAX_SECONDS, runtime)
        report["stages"].append(stage)
        report["nativeSolverCompleted"] = True
        require(re.search(r"(?m)^Selecting cavitation model SchnerrSauer\s*$", solver_text), "Actual native SchnerrSauer selection must appear in the solver log.")
        report["cavitationModelExecuted"] = True
        fields, times = final_fields(directory, prepared["temporalPlan"], mesh["meshCellCount"], solver_text)
        report.update(finiteNativeEndFieldsVerified=True, finalNativeFieldRecords=fields, nativeEndTimeS=times[-1][0], simulationExecuted=True)
        if mode == "transient-sliding":
            report["rotorMotionEvidence"] = rotor_motion(directory, times, solver_text)
            report["physicalTransientExecuted"] = True
        else:
            report["stationaryMrfRelaxationExecuted"] = True
        report["nativeSamplingRecords"] = sampling_records(directory)
        report["workspaceBytes"] = workspace_size(directory)
        report.update(status="PHASE_CHANGE_EXECUTED_ACCEPTANCE_REQUIRED", actualCfdRunVerified=True)
    except Exception as error:
        report.update(status="NATIVE_PHASE_CORRECTION_REQUIRED", reasonCode="CFD_NATIVE_PHASE_CORRECTION_REQUIRED", actualCfdRunVerified=False,
                      diagnostic={"program": current, "type": type(error).__name__, "message": str(error)[:500]})
    report["retainedNativeLogRecords"] = []
    try:
        for name in ("00-checkMesh.log", "01-foamRun.log"):
            path = logs / name
            if path.exists():
                require(not path.is_symlink() and path.is_file() and 0 <= path.stat().st_size <= MAX_LOG,
                        "Retain only regular bounded native log files, including an empty startup log.")
                raw = preparation.base.read_regular(path, MAX_LOG) if path.stat().st_size else b""
                report["retainedNativeLogRecords"].append({"path": "logs/" + name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()})
    except Exception as error:
        report.update(status="NATIVE_PHASE_CORRECTION_REQUIRED", reasonCode="CFD_PHASE_LOG_RETENTION_REQUIRED", actualCfdRunVerified=False,
                      nativeLogRetentionDiagnostic={"type": type(error).__name__, "message": str(error)[:500]})
    try:
        report["inputArtifactsImmutable"] = bound_inputs(directory, prepared) == inputs and all(
            preparation.base.read_regular(directory / name, preparation.base.inventory.MAX_REPORT_BYTES) == raw for name, raw in artifacts.items())
        preparation.verify_baseline(baseline_directory, packet)
        report["sourceBaselineImmutable"] = True
        require(report["inputArtifactsImmutable"], "The authored phase inputs or preserved source geometry changed during native execution.")
    except Exception as error:
        report.update(status="NATIVE_PHASE_CORRECTION_REQUIRED", reasonCode="CFD_PHASE_SOURCE_IMMUTABILITY_REQUIRED", actualCfdRunVerified=False,
                      sourceDiagnostic={"type": type(error).__name__, "message": str(error)[:500]})
    report["elapsedSeconds"] = round(time.monotonic() - started, 3)
    # This once-written fact record is bound by later reductions, never amended.
    raw = preparation.base.inventory.bounded_json(report)
    preparation.base.write_exclusive(directory / "phase-change-execution.json", raw)
    return {"status": report["status"], "actualCfdRunVerified": report["actualCfdRunVerified"],
            "cavitationModelExecuted": report["cavitationModelExecuted"], "physicalTransientExecuted": report["physicalTransientExecuted"],
            "stationaryMrfRelaxationExecuted": report["stationaryMrfRelaxationExecuted"], "npsh3Verified": False, "restartVerified": False,
            "reportSha256": sha256(raw).hexdigest(), "reportBytes": len(raw), "mainApplicationWrites": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-case-directory", type=Path, required=True)
    parser.add_argument("--baseline-evidence-json", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--pressure-absolute-pa", type=float, required=True)
    parser.add_argument("--mode", choices=sorted(preparation.MODES), required=True)
    parser.add_argument("--time-step-s", type=float, required=True)
    parser.add_argument("--duration-s", type=float, required=True)
    args = parser.parse_args()
    try:
        packet = preparation.json_file(args.baseline_evidence_json)
        result = run(args.baseline_case_directory, packet, args.output_directory, pressure_absolute_pa=args.pressure_absolute_pa,
                     mode=args.mode, dt_s=args.time_step_s, duration_s=args.duration_s)
        print(json.dumps(result, allow_nan=False))
        return 0 if result["actualCfdRunVerified"] else 2
    except (PhaseExecutionIssue, preparation.CampaignIssue, preparation.protocol.ProtocolError, preparation.base.CaseIssue,
            preparation.base.inventory.InspectionIssue, OSError, ValueError, KeyError, TypeError, ImportError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "NATIVE_PHASE_EXECUTION_REQUIRED", "reasonCode": "CFD_NATIVE_PHASE_PREFLIGHT_REQUIRED",
                          "action": str(error)[:500], "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
