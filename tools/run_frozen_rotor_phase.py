"""Execute only authored Foundation-12 phase-change cases on the FREE runner.

Native execution, finite end fields, actual rotor motion and research
qualification are separate facts. This adapter never qualifies NPSH3, a
pressure campaign, time convergence, restart or an experimental pump model.
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


def rotor_motion(directory, time_directories, log_text):
    """Observe actual changed native coordinates; motion dictionaries alone are insufficient."""
    require(re.search(r"Selecting solidBodyMotionFunction rotatingMotion", log_text), "The native solver must select actual rotatingMotion.")
    original = points(directory / "case/constant/polyMesh/points")
    bounds = [(min(point[axis] for point in original), max(point[axis] for point in original)) for axis in range(3)]
    scale = math.hypot(*(high - low for low, high in bounds))
    require(math.isfinite(scale) and scale > 0, "The reference mesh must span a real three-dimensional coordinate scale.")
    evidence, maximum = [], 0.
    for physical_time, path in time_directories:
        source = path / "polyMesh/points"
        if not source.exists():
            continue
        values = points(source)
        require(len(values) == len(original), "A fixed-topology rotor motion must retain every reference point index.")
        displacement = max(math.dist(before, after) for before, after in zip(original, values))
        raw = preparation.base.read_regular(source, preparation.MAX_FILE_BYTES)
        evidence.append({"path": "case/" + path.name + "/polyMesh/points", "bytes": len(raw), "sha256": sha256(raw).hexdigest(),
                         "physicalTimeS": physical_time, "maximumPointDisplacementAssumedM": displacement, "allPointCoordinatesChecked": True})
        maximum = max(maximum, displacement)
    require(evidence and maximum > 1e-10 * scale, "Physical sliding execution requires actual moved points beyond coordinate-rounding tolerance.")
    return {"nativeRotatingMotionSelected": True, "nativeMovedPointsObserved": True, "nativeCoordinateUnitsVerified": False,
            "classroomMetresPerSourceCoordinateUnit": 1, "referenceDiagonalAssumedM": scale,
            "coordinateRoundoffRelativeTolerance": 1e-10, "nativeMovedPointRecords": evidence,
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
