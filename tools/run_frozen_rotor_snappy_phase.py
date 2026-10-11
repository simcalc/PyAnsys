"""Execute one bounded active-phase MRF reference on an admitted approximation.

Only the fixed authored checkMesh and foamRun commands can execute on the
independent FREE worker. Native completion is recorded once, separately from
numerical/reference acceptance. No sweep, sliding run or Main write is enabled.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_snappy_phase_case as preparation
import run_frozen_rotor_snappy as geometry
import run_frozen_rotor_phase as phase_fields

base, surface, native = geometry.base, geometry.surface, geometry.native
SCHEMA = "untirta.frozen-rotor-snappy-phase-reference-execution.v1"
RUNTIME_SCHEMA = "untirta.frozen-rotor-snappy-phase-runtime-identity.v1"
REPORT = "phase-change-execution.json"
STATUS = "SNAPPY_ACTIVE_PHASE_REFERENCE_EXECUTED_ACCEPTANCE_REQUIRED"
MAX_SECONDS, MAX_MEMORY, MAX_WORKSPACE = 1200, 4 * 1024**3, 1024**3
MAX_LOG = 8 * 1024**2
PROGRAMS = ("checkMesh", "foamRun")
END_FIELDS = phase_fields.END_FIELDS
FIELD_DIMENSIONS = phase_fields.FIELD_DIMENSIONS
FALSE_FLAGS = ("qualifiedBaseline", "numericalSolverAcceptanceVerified", "phaseObservationQualified",
    "phaseFamilyReferenceEligible", "phaseFamilyReferenceQualified", "cavitationSweepExecuted", "pressureSweepEnabled",
    "slidingEnabled", "physicalTransientExecuted", "npshrCalculated", "npsh3Qualified", "npsh3Verified", "restartVerified",
    "meshConvergenceVerified", "timeStepConvergenceVerified", "wallResolutionVerified", "nativeCoordinateUnitsVerified",
    "nativeNodeIdentityEquivalent", "originalPolygonGeometryVerified", "geometricApproximationQualified",
    "fullGeometryQualification", "physicalValidationVerified", "baselineFieldsCopied", "mainApplicationWrites")
LIMITS = {"maximumSeconds": MAX_SECONDS, "maximumCpus": 1, "maximumMemoryBytes": MAX_MEMORY,
    "maximumWorkspaceBytes": MAX_WORKSPACE, "maximumFileBytes": preparation.MAX_FILE_BYTES,
    "maximumLogBytes": MAX_LOG, "enforcement": "Serial owned native processes with address-space, one-CPU, file/log/workspace and wall-clock bounds; aggregate cgroup belongs to the independent FREE workflow."}


def require(value, action):
    surface.require(value, action)


def commands(output):
    return preparation.commands(output)


def runtime_identity(output, deadline):
    """Bind the two actual package-owned executable bytes; no solver launch."""
    def query(arguments):
        remaining = deadline - time.monotonic()
        require(remaining > 0, "The reference deadline was reached during package verification.")
        result = subprocess.run(arguments, env=native.child_environment(), capture_output=True,
            text=True, timeout=min(10, remaining), check=True)
        require(len(result.stdout) <= 8192 and len(result.stderr) <= 8192, "Bound native package-query output.")
        return result.stdout
    require(query(["dpkg-query", "--show", "--showformat=${Version}", "openfoam12"]) == "20250206",
        "Require the exact checksum-pinned Foundation-12 package 20250206.")
    binaries = []
    for program in PROGRAMS:
        executable = shutil.which(program)
        require(executable is not None, "An actual native reference program is absent: " + program)
        path = Path(executable).resolve()
        require(path.is_relative_to(Path("/opt/openfoam12")) and path.name == program,
            "Reference programs must resolve inside the exact official package.")
        owner = query(["dpkg-query", "--search", str(path)]).strip()
        require(owner == "openfoam12: " + str(path), "Each actual executable must belong to the pinned package.")
        _, row = surface.descriptor(path)
        binaries.append({**row, "program": program, "path": str(path), "packageOwnership": owner})
    require(time.monotonic() < deadline, "The reference deadline was reached after runtime verification.")
    identity = {"schemaVersion": RUNTIME_SCHEMA, "openFoamFoundationVersion": "12", "packageVersion": "20250206",
        "officialDebSha256": surface.PACKAGE_SHA, "expectedSourceCommit": surface.SOURCE_COMMIT,
        "binaries": binaries, "mainApplicationWrites": False}
    base.write_exclusive(Path(output) / "phase-runtime-identity.json", base.inventory.bounded_json(identity))
    return identity


def execute(arguments, output, deadline, runtime, index):
    """Execute an exact indexed current-root command with its bound native binary."""
    require(type(index) is int and 0 <= index < 2 and arguments == commands(output)[index],
        "Only the two indexed fixed reference commands may execute.")
    output = base.regular_directory(output)
    relative = f"logs/{index:02d}-{arguments[0]}.log"; path = output / relative
    require(not path.exists() and not path.is_symlink(), "Native reference logs must be fresh and immutable.")
    rows = [row for row in runtime["binaries"] if row["program"] == arguments[0]]
    require(len(rows) == 1, "Use exactly one hash-bound native executable for this command.")
    row = rows[0]; binary = Path(row["path"])
    require(binary.resolve().is_relative_to(Path("/opt/openfoam12")) and binary.name == arguments[0]
        and row.get("packageOwnership") == "openfoam12: " + str(binary), "Execute only the actual reviewed package-owned binary.")
    _, actual = surface.descriptor(binary)
    require(actual["bytes"] == row["bytes"] and actual["sha256"] == row["sha256"], "Native executable bytes changed after identity binding.")
    require(time.monotonic() < deadline, "The reference deadline was reached before native startup.")
    surface.workspace_size(output)
    process, total, started = None, 0, time.monotonic()
    next_progress = started + 30; selector = selectors.DefaultSelector()
    try:
        with path.open("xb") as destination:
            process = subprocess.Popen([str(binary), *arguments[1:]], cwd=arguments[2], stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, env=native.child_environment(), preexec_fn=surface.child_limits,
                start_new_session=True, bufsize=0)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                now = time.monotonic()
                require(now < deadline, "The bounded active-phase reference timed out.")
                surface.workspace_size(output)
                if now >= next_progress:
                    print(json.dumps({"stageIndex": index, "program": arguments[0], "status": "NativeReferenceRunning",
                        "elapsedSeconds": round(now-started, 1), "actualCfdRunVerified": False, "mainApplicationWrites": False}), flush=True)
                    next_progress = now + 30
                for key, _ in selector.select(timeout=.25):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk: selector.unregister(key.fileobj); continue
                    remaining = MAX_LOG - total
                    destination.write(chunk[:remaining]); destination.flush(); total += min(len(chunk), remaining)
                    require(len(chunk) <= remaining, "Native reference log reached the exact 8 MiB cap.")
            code = process.wait(timeout=3)
            surface.workspace_size(output)
            require(time.monotonic() < deadline, "The reference deadline was reached at process completion.")
    finally:
        if process is not None: native.stop_owned(process); process.stdout.close()
        selector.close()
    raw, metadata = geometry.record(output, relative, MAX_LOG)
    return {"program": arguments[0], "exitCode": code, "elapsedSeconds": round(time.monotonic()-started, 3),
        "log": path.name, "logBytes": metadata["bytes"], "logSha256": metadata["sha256"]}, raw.decode("utf-8", errors="strict")


def bind_manifest(output):
    """The separate reducer defines the scientific manifest before native work."""
    import reduce_frozen_rotor_snappy_phase as reduction
    bound = reduction.build_manifest(output)
    for name, key in (("phase-input-manifest.json", "manifest"), ("phase-classroom-study.json", "study")):
        raw = reduction.encoded(bound[key])
        require(0 < len(raw) <= preparation.MAX_REPORT_BYTES, "Canonical reference manifest and study must remain bounded.")
        base.write_exclusive(Path(output) / name, raw)
    rows = {name: geometry.record(output, name, preparation.MAX_REPORT_BYTES)[1]
        for name in ("phase-input-manifest.json", "phase-classroom-study.json")}
    files = bound["manifest"]["files"]
    require(isinstance(files, list) and 0 < len(files) <= preparation.MAX_SOURCE_RECORDS + 64
        and len({row["path"] for row in files}) == len(files), "The actual pre-execution manifest needs bounded unique file identities.")
    for row in files:
        require(geometry.record(output, row["path"])[1] == {key: row[key] for key in ("path", "bytes", "sha256")},
            "Every scientific input record must bind actual current bytes before native startup.")
    return rows, files


finite_field = phase_fields.finite_field
final_fields = phase_fields.final_fields
sampling_records = phase_fields.sampling_records


def run(baseline_directory, output_directory, *, dt_s=preparation.MAX_DT_S, duration_s=preparation.DEFAULT_DURATION_S):
    started = time.monotonic(); deadline = started + MAX_SECONDS
    surface.context_guard()
    source = surface.scope_path(baseline_directory); output = surface.scope_path(output_directory, fresh=True)
    prepared = preparation.prepare(source, output, dt_s=dt_s, duration_s=duration_s)
    verified = preparation.verify_output(output); output = base.regular_directory(output)
    require(prepared == verified["report"] and prepared["schemaVersion"] == preparation.SCHEMA
        and prepared["mode"] == preparation.MODE and prepared["inletAbsoluteTotalPressurePa"] == 300000
        and prepared["nativeCommandArguments"] == commands(output), "Execute only this new fresh fixed high-pressure reference preparation.")
    run_id, commit = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_SHA", "")
    report = {"schemaVersion": SCHEMA, "githubRepository": "simcalc/PyAnsys",
        "githubRunId": run_id if re.fullmatch(r"[1-9][0-9]{0,19}", run_id) else None,
        "githubSourceCommit": commit if re.fullmatch(r"[0-9a-f]{40}", commit) else None, "exampleDataCommit": base.inventory.PIN,
        "status": "SNAPPY_ACTIVE_PHASE_REFERENCE_EXECUTION_REQUIRED", "mode": preparation.MODE,
        "physicalModel": "steady-cavitation-MRF", "sourceGeometryRoute": prepared["sourceGeometryRoute"], "stages": [], "limits": LIMITS,
        "preparationReportSha256": verified["reportFile"]["sha256"], "sourceBaselineEvidenceSha256": prepared["sourceBaselineEvidenceSha256"],
        "sourceCaseManifestSha256": prepared["sourceCaseManifestSha256"], "sourceGeometryControlSha256": prepared["sourceGeometryControlSha256"],
        "authoredCavitationModelSha256": prepared["authoredCavitationModelSha256"], "temporalPlan": prepared["temporalPlan"],
        "inletAbsoluteTotalPressurePa": 300000., "absolutePressureMapping": prepared["absolutePressureMapping"],
        "classroomMetresPerSourceCoordinateUnit": 1, "sourceNumericalBaselineQualified": True,
        "nativeCheckMeshVerified": False, "nativeGeometryQualityVerified": False, "physicalWallTypesVerified": False,
        "coupledInterfaceCoverageVerified": False, "nativeSolverCompleted": False, "cavitationModelExecuted": False,
        "finiteNativeEndFieldsVerified": False, "inputArtifactsImmutable": False, "sourceBaselineImmutable": False,
        "stationaryMrfRelaxationExecuted": False, "simulationExecuted": False, "actualCfdRunVerified": False,
        **{flag: False for flag in FALSE_FLAGS},
        "claimBoundary": "Actual active SchnerrSauer/fixed-MRF native completion on the source-bound approximate mesh only. Numerical convergence, global noncavitating phase H0, geometry/units/wall/refinement, pressure sweep, NPSHr and physical sliding/restart acceptance remain separate."}
    (output / "logs").mkdir(); current = "runtime-identity"
    try:
        runtime = runtime_identity(output, deadline)
        immutable = {preparation.REPORT: verified["reportFile"], "phase-runtime-identity.json": geometry.record(output, "phase-runtime-identity.json")[1],
            "geometry-identity.json": geometry.record(output, "geometry-identity.json")[1]}
        for row in prepared["sourceEvidenceFileRecords"]: immutable[row["path"]] = row
        for row in prepared["authoredInputFiles"]: immutable["case/"+row["path"]] = {**row, "path": "case/"+row["path"]}
        for row in prepared["preservedNativeMeshFiles"]: immutable["case/"+row["path"]] = {**row, "path": "case/"+row["path"]}
        require(all(geometry.record(output, path)[1] == row for path, row in immutable.items()), "Prepared source, model or mesh bytes changed before native work.")
        bound_rows, input_rows = bind_manifest(output); immutable.update(bound_rows)
        report.update(phaseRuntimeIdentitySha256=immutable["phase-runtime-identity.json"]["sha256"],
            phaseInputManifestSha256=bound_rows["phase-input-manifest.json"]["sha256"], phaseStudySha256=bound_rows["phase-classroom-study.json"]["sha256"])
        for index, arguments in enumerate(commands(output)):
            current = arguments[0]
            stage, text = execute(arguments, output, deadline, runtime, index); report["stages"].append(stage)
            require(stage["exitCode"] == 0, "A native reference utility returned exit " + str(stage["exitCode"]))
            geometry.completed_log(text)
            if current == "checkMesh":
                measured = geometry.native_mesh(output / "case", verified["components"], text)
                require(measured["cellCount"] == sum(c["cellCount"] for c in verified["components"])
                    and measured["nodeCount"] == sum(c["nodeCount"] for c in verified["components"]), "The phase mesh must preserve actual source component counts.")
                report.update(mergedNativeMeshEvidence=measured, nativeMeshQuality=preparation.baseline.native.parse_mesh_quality(text),
                    nativeCheckMeshVerified=True, nativeGeometryQualityVerified=True, physicalWallTypesVerified=True, coupledInterfaceCoverageVerified=True)
            else:
                report["nativeSolverCompleted"] = True
                require(re.search(r"(?m)^Selecting cavitation model SchnerrSauer\s*$", text), "Actual active SchnerrSauer selection must be observed in the native solver log.")
                report["cavitationModelExecuted"] = True
                fields, times = final_fields(output, prepared["temporalPlan"], report["mergedNativeMeshEvidence"]["cellCount"], text)
                report.update(finalNativeFieldRecords=fields, nativeEndTimeS=times[-1][0], finiteNativeEndFieldsVerified=True,
                    simulationExecuted=True, stationaryMrfRelaxationExecuted=True, nativeSamplingRecords=sampling_records(output))
        require(all(geometry.record(output, path)[1] == row for path, row in immutable.items()) and all(
            geometry.record(output, row["path"])[1] == {key: row[key] for key in ("path", "bytes", "sha256")} for row in input_rows),
            "Source evidence, fields authored at t=0, controls or mesh changed during native execution.")
        preparation.verify_output(output); preparation.verify_baseline(source)
        require(time.monotonic() < deadline, "The reference deadline was reached before final input verification.")
        surface.workspace_size(output)
        report.update(inputArtifactsImmutable=True, sourceBaselineImmutable=True, actualCfdRunVerified=True, status=STATUS)
    except Exception as error:
        report.update(status="SNAPPY_ACTIVE_PHASE_REFERENCE_CORRECTION_REQUIRED", actualCfdRunVerified=False,
            diagnostic={"program": current, "type": type(error).__name__, "message": str(error)[:500]})
    try:
        report["retainedNativeLogRecords"] = [surface.descriptor(path, "logs/"+path.name, MAX_LOG, empty=True)[1]
            for path in sorted((output / "logs").glob("*.log"))]
        report["workspaceBytes"] = surface.workspace_size(output)
    except Exception as error:
        report.update(status="SNAPPY_ACTIVE_PHASE_REFERENCE_RETENTION_CORRECTION_REQUIRED", actualCfdRunVerified=False,
            retentionDiagnostic={"type": type(error).__name__, "message": str(error)[:500]})
    report["elapsedSeconds"] = round(time.monotonic()-started, 3)
    raw = base.inventory.bounded_json(report); base.write_exclusive(output / REPORT, raw)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-case-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--time-step-s", type=float, default=preparation.MAX_DT_S)
    parser.add_argument("--duration-s", type=float, default=preparation.DEFAULT_DURATION_S)
    args = parser.parse_args()
    try:
        report = run(args.baseline_case_directory, args.output_directory, dt_s=args.time_step_s, duration_s=args.duration_s)
        print(json.dumps({key: report[key] for key in ("status", "actualCfdRunVerified", "cavitationModelExecuted",
            "simulationExecuted", "phaseFamilyReferenceQualified", "npsh3Qualified", "physicalTransientExecuted", "mainApplicationWrites")}))
        return 0 if report["actualCfdRunVerified"] else 2
    except Exception as error:
        print(json.dumps({"status": "SNAPPY_PHASE_REFERENCE_INPUT_CORRECTION_REQUIRED", "action": str(error)[:500],
            "actualCfdRunVerified": False, "simulationExecuted": False, "mainApplicationWrites": False})); return 2


if __name__ == "__main__":
    raise SystemExit(main())
