"""Execute one source-bound physical rotor pilot; acceptance stays independent.

Only package-owned checkMesh and foamRun execute. An observed rotating mesh is
not periodic pump performance, transient convergence, NPSH3 or matched restart.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import teaching_pump_sliding_case as preparation
import run_teaching_pump_phase as phase
import run_teaching_pump_pressure as pressure

base, safety, native = phase.base, phase.safety, phase.native
SCHEMA = "untirta.teaching-pump-sliding-pilot-execution.v1"
REPORT = "teaching-sliding-execution.json"
MANIFEST = "teaching-sliding-input-manifest.json"
MOTION_REPORT = "teaching-sliding-motion-evidence.json"
MAX_MOTION_REPORT = 4*1024**2
RUNTIME = pressure.RUNTIME
LIMITS = dict(phase.LIMITS)
PROGRAMS = phase.PROGRAMS
record, workspace_size = phase.record, phase.workspace_size
FALSE_FLAGS = tuple(sorted(set(preparation.FALSE_FLAGS) | {
    "numericalTransientQualified", "periodicStateVerified", "slidingH0Qualified",
    "aleGclVerified", "wallNormalFluxVerified", "restartExecuted",
    "physicalValidationVerified", "wallResolutionVerified",
    "meshConvergenceVerified", "timeStepConvergenceVerified"}))


def require(value, action): preparation.require(value, action)


def commands(output): return preparation.commands(output)


def execute(args, output, deadline, runtime, index):
    require(type(index) is int and 0 <= index < 2 and args == commands(output)[index],
        "Execute only the exact indexed package-owned physical pilot command.")
    return phase.execute(args, output, deadline, runtime, index)


def motion_library_identity(deadline):
    """Bind the actual official motion library separately from executable identity."""
    directory = Path(os.environ.get("FOAM_LIBBIN", ""))
    require(directory.is_absolute() and directory.resolve().is_relative_to(Path("/opt/openfoam12")),
        "Physical motion must use the exact official OpenFOAM12 library directory.")
    path = (directory / "libfvMotionSolvers.so").resolve()
    require(path.is_file() and path.is_relative_to(Path("/opt/openfoam12"))
        and path.name == "libfvMotionSolvers.so", "Bind the actual package motion library.")
    remaining = deadline-time.monotonic()
    require(remaining > 0, "Pilot deadline expired before motion-library ownership binding.")
    result = subprocess.run(["dpkg-query", "--search", str(path)],
        env=native.child_environment(), capture_output=True, text=True,
        timeout=min(10, remaining), check=True)
    require(len(result.stdout) <= 8192 and len(result.stderr) <= 8192
        and result.stdout.strip() == "openfoam12: " + str(path),
        "The current motion library must belong to the checksum-pinned package.")
    _, row = safety.descriptor(path)
    return {**row, "path":str(path), "library":"libfvMotionSolvers.so",
        "packageOwnership":result.stdout.strip(), "allLoadedLibrariesVerified":False}


def verify_motion_library(row):
    path = Path(row["path"])
    require(path.is_absolute() and path.resolve().is_relative_to(Path("/opt/openfoam12"))
        and path.name == "libfvMotionSolvers.so"
        and row["packageOwnership"] == "openfoam12: " + str(path),
        "Retain the exact package-owned motion-library identity.")
    _, current = safety.descriptor(path)
    require((current["bytes"], current["sha256"]) == (row["bytes"], row["sha256"]),
        "Actual motion-library bytes changed after runtime binding.")


def input_inventory(output, prepared, verified):
    """Whole copied reference and authored input binding before native execution."""
    rows = {preparation.REPORT:verified["reportFile"],
        RUNTIME:record(output, RUNTIME, LIMITS["maximumReportBytes"])[1],
        phase.IDENTITY:record(output, phase.IDENTITY, LIMITS["maximumReportBytes"])[1]}
    for row in prepared["sourceEvidenceFileRecords"]:
        require(row["path"] not in rows, "Every source role needs one unambiguous byte identity.")
        rows[row["path"]] = row
    for key in ("authoredInputFiles", "preservedNativeMeshFiles"):
        for row in prepared[key]:
            path = "case/" + row["path"]
            require(path not in rows, "Each current mesh/model input must have a unique role.")
            rows[path] = {**row, "path":path}
    require(len(rows) <= LIMITS["maximumSourceRecords"]+64,
        "Bound the entire actual sliding input inventory.")
    for path, row in rows.items():
        require(record(output, path, preparation.MAX_ACCEPTANCE if path.endswith("-acceptance.json") else None)[1] == row,
            "Current source/model/motion/clock/mesh bytes disagree before native work.")
    packet = {"schemaVersion":"untirta.teaching-pump-sliding-input-manifest.v1",
        "sourceGeometryRoute":preparation.ROUTE, "coordinateUnits":"SI_METRES",
        "preparationReport":verified["reportFile"], "motionPlan":prepared["motionPlan"],
        "temporalPlan":prepared["temporalPlan"], "files":[rows[p] for p in sorted(rows)],
        "calculatedResultsSupplied":False, "sourceResultsRole":"Immutable prerequisite evidence only",
        "actualCfdRunVerified":False, "mainApplicationWrites":False}
    raw = preparation.encoded(packet)
    require(0 < len(raw) <= LIMITS["maximumReportBytes"], "Bound the source-bound pilot manifest.")
    base.write_exclusive(Path(output)/MANIFEST, raw)
    rows[MANIFEST] = record(output, MANIFEST, LIMITS["maximumReportBytes"])[1]
    return rows


def near_points_evidence(output):
    return phase.baseline.near_points_evidence(output, case_relative="case",
        log_relative="logs/00-checkMesh.log",
        authored_report_relative="source-reference/source-baseline/source-teaching/source-geometry/teaching-geometry.json")


def run(reference_directory, output_directory):
    started = time.monotonic(); deadline = started + LIMITS["maximumSeconds"]
    safety.context_guard()
    source = safety.scope_path(reference_directory)
    output = safety.scope_path(output_directory, fresh=True)
    prepared = preparation.prepare(source, output)
    verified = preparation.verify_output(output)
    output = base.regular_directory(output)
    require(prepared == verified["report"] and prepared["schemaVersion"] == preparation.SCHEMA
        and prepared["mode"] == preparation.MODE and prepared["nativeCommandArguments"] == commands(output),
        "Execute only the exact source-bound authored physical sliding pilot.")
    report = {"schemaVersion":SCHEMA, "githubRepository":"simcalc/PyAnsys",
        "githubRunId":os.environ.get("GITHUB_RUN_ID"), "githubSourceCommit":os.environ.get("GITHUB_SHA"),
        "sourceGeometryRoute":preparation.ROUTE, "coordinateUnits":"SI_METRES",
        "mode":preparation.MODE, "physicalModel":"prescribed-speed-active-phase-sliding-pilot",
        "status":"TEACHING_SLIDING_PILOT_EXECUTION_REQUIRED", "limits":dict(LIMITS), "stages":[],
        "preparationReportSha256":verified["reportFile"]["sha256"],
        **{k:prepared[k] for k in ("motionPlan", "temporalPlan", "sourceActivePhaseEvidenceSha256",
            "sourceReferenceInputManifestSha256", "sourceActivePhaseHeadM", "absolutePressureMapping", "outputCapacityPlan")},
        "sourceActivePhaseH0NumericallyQualified":True, "nativeSolverCompleted":False,
        "nativeCheckMeshVerified":False, "finiteNativeEndFieldsVerified":False,
        "cavitationModelExecuted":False, "coordinateMotionVerified":False,
        "dynamicNccClockCoverageVerified":False, "inputArtifactsImmutable":False,
        "sourceReferenceImmutable":False, "simulationExecuted":False, "actualCfdRunVerified":False,
        **dict.fromkeys(FALSE_FLAGS, False),
        "claimBoundary":"One source-bound 1.25-revolution pilot with measured coordinates and native interface coverage only. Periodicity, ALE/wall flux, transient accuracy, refinement, restart and NPSH3 need independent actual evidence."}
    (output/"logs").mkdir(); current = "runtime-identity"
    try:
        runtime = pressure.runtime_identity(output, deadline)
        motion_library = motion_library_identity(deadline)
        report["nativeMotionLibrary"] = motion_library
        immutable = input_inventory(output, prepared, verified)
        report.update(runtimeIdentitySha256=immutable[RUNTIME]["sha256"], inputManifestSha256=immutable[MANIFEST]["sha256"])
        for index, args in enumerate(commands(output)):
            current = args[0]; verify_motion_library(motion_library)
            stage, text = execute(args, output, deadline, runtime, index)
            report["stages"].append(stage)
            require(stage["exitCode"] == 0, "An actual physical pilot utility returned exit " + str(stage["exitCode"]))
            phase.baseline.geometry.native_geometry.completed_log(text)
            immutable["logs/"+stage["log"]] = record(output, "logs/"+stage["log"], LIMITS["maximumLogBytes"])[1]
            if index == 0:
                phase.baseline.geometry.verify_region_count(text, 2)
                mesh = base.verify_merged_mesh(output/"case", verified["components"], output/"logs"/stage["log"])
                require(mesh["mergedCellCount"] == sum(r["cellCount"] for r in verified["components"]),
                    "Physical pilot cells must retain the exact source component identity.")
                near = near_points_evidence(output)
                report.update(mergedNativeMeshEvidence=mesh, nativeCheckMeshVerified=True,
                    nativeMeshQuality=phase.baseline.scientific.parse_mesh_quality(text),
                    nativeNearPointsEvidence=near["evidence"], nativeCheckMeshDiagnosticRecords=near["files"])
            else:
                report["nativeSolverCompleted"] = True
                require(re.search(r"(?m)^Selecting cavitation model SchnerrSauer\s*$", text),
                    "The actual active model selector must appear in the current pilot log.")
                report["cavitationModelExecuted"] = True
                rows, times = phase.final_fields(output, prepared["temporalPlan"], report["mergedNativeMeshEvidence"]["mergedCellCount"], text)
                pressure_rows = {Path(r["path"]).name:r for r in rows if Path(r["path"]).name in {"p", "p_rgh"}}
                require(set(pressure_rows) == {"p", "p_rgh"}, "Retain both actual absolute-Pa pressure fields.")
                pair = phase.absolute_pressure_reconstruction(record(output, pressure_rows["p"]["path"])[0],
                    record(output, pressure_rows["p_rgh"]["path"])[0], report["mergedNativeMeshEvidence"]["mergedCellCount"])
                final = preparation.verify_output(output, after_native_execution=True)
                import teaching_pump_motion_evidence as motion
                observed = motion.inspect(output, times, text)
                motion_raw = (json.dumps(observed, sort_keys=True, separators=(",", ":"), allow_nan=False)+"\n").encode("ascii")
                require(0 < len(motion_raw) <= MAX_MOTION_REPORT, "Bound the separate actual clock-by-clock motion evidence.")
                base.write_exclusive(output/MOTION_REPORT, motion_raw)
                motion_record = record(output, MOTION_REPORT, MAX_MOTION_REPORT)[1]
                report.update(finalNativeFieldRecords=rows, nativeEndTimeS=times[-1][0],
                    endpointAbsolutePressureReconstruction=pair, finiteNativeEndFieldsVerified=True,
                    simulationExecuted=True, nativeMotionEvidenceFile=motion_record,
                    nativeMotionEvidenceRecords=observed["nativeEvidenceRecords"],
                    nativeMotionDiagnostics=observed["diagnostics"],
                    coordinateMotionVerified=observed["coordinateMotionVerified"],
                    dynamicNccClockCoverageVerified=observed["dynamicNccClockCoverageVerified"],
                    nativeGeneratedDataRecords=final["nativeGeneratedDataRecords"], nativeGeneratedDataEvidence=final["nativeGeneratedDataEvidence"],
                    nativeSamplingRecords=pressure.sampling_records(output), nativeNccDiagnosticRecords=pressure.ncc_diagnostic_records(output))
                require(observed["coordinateMotionVerified"] and observed["dynamicNccClockCoverageVerified"],
                    "Actual rotor coordinates and per-clock native coupling coverage must both pass before pilot execution verification.")
        for path, row in immutable.items():
            require(record(output, path, LIMITS["maximumLogBytes"] if path.startswith("logs/") else preparation.MAX_ACCEPTANCE if path.endswith("-acceptance.json") else None)[1] == row,
                "Source/model/clock/motion/initial mesh bytes changed during the physical pilot.")
        for row in report["finalNativeFieldRecords"]+report["nativeGeneratedDataRecords"]+report["nativeSamplingRecords"]+report["nativeNccDiagnosticRecords"]+report["nativeMotionEvidenceRecords"]+[report["nativeMotionEvidenceFile"]]:
            require(record(output, row["path"])[1] == {k:row[k] for k in ("path", "bytes", "sha256")},
                "Actual measured field/cache/sample bytes changed at retention.")
        require(near_points_evidence(output)["files"] == report["nativeCheckMeshDiagnosticRecords"], "Initial diagnostic evidence changed.")
        original = preparation.verify_reference(source)
        require(original["sourceRecords"] == [{**r, "path":r["path"].removeprefix("source-reference/")} for r in prepared["sourceEvidenceFileRecords"]],
            "The original qualified active reference changed after its full immutable copy.")
        verify_motion_library(motion_library); workspace_size(output)
        require(time.monotonic() < deadline, "The physical pilot evidence deadline expired.")
        report.update(inputArtifactsImmutable=True, sourceReferenceImmutable=True,
            actualCfdRunVerified=True, status="TEACHING_SLIDING_PILOT_EXECUTED_INDEPENDENT_ACCEPTANCE_REQUIRED")
    except Exception as error:
        report.update(status="TEACHING_SLIDING_PILOT_CORRECTION_REQUIRED", actualCfdRunVerified=False,
            diagnostic={"program":current, "type":type(error).__name__, "message":str(error)[:500]})
    try:
        report["retainedNativeLogRecords"] = [record(output, f"logs/{i:02d}-{name}.log", LIMITS["maximumLogBytes"], empty=True)[1]
            for i, name in enumerate(PROGRAMS) if (output/f"logs/{i:02d}-{name}.log").exists()]
        report["workspaceBytes"] = workspace_size(output)
    except Exception as error:
        report.update(status="TEACHING_SLIDING_PILOT_RETENTION_CORRECTION_REQUIRED", actualCfdRunVerified=False,
            retentionDiagnostic={"type":type(error).__name__, "message":str(error)[:500]}, workspaceBytes=None)
    report["elapsedSeconds"] = round(time.monotonic()-started, 3)
    if report["elapsedSeconds"] >= LIMITS["maximumSeconds"]:
        report.update(actualCfdRunVerified=False, status="TEACHING_SLIDING_PILOT_CORRECTION_REQUIRED",
            diagnostic={"program":"final-deadline", "message":"The complete physical pilot deadline expired."})
    base.write_exclusive(output/REPORT, base.inventory.bounded_json(report))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-directory", required=True); parser.add_argument("--output-directory", required=True)
    args=parser.parse_args()
    try:
        report=run(args.reference_directory, args.output_directory)
        summary={k:report[k] for k in ("status", "nativeSolverCompleted", "simulationExecuted", "actualCfdRunVerified",
            "coordinateMotionVerified", "dynamicNccClockCoverageVerified", "numericalTransientQualified", "restartVerified", "mainApplicationWrites")}
        if "diagnostic" in report: summary["diagnostic"]=report["diagnostic"]
        print(json.dumps(summary, allow_nan=False)); return 0 if report["actualCfdRunVerified"] else 2
    except Exception as error:
        print(json.dumps({"status":"TEACHING_SLIDING_INPUT_CORRECTION_REQUIRED", "action":str(error)[:500],
            "actualCfdRunVerified":False, "physicalTransientExecuted":False, "mainApplicationWrites":False})); return 2


if __name__ == "__main__": raise SystemExit(main())
