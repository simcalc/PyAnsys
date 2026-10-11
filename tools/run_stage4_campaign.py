"""Bounded native fixed-clock and restart experiments on the FREE public worker.

Actual process completion, immutable native evidence and numerical acceptance
are separate. No Main runtime, credential, network or source script is used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import importlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_frozen_rotor_phase as native

SCHEMA = "untirta.frozen-rotor-stage4-campaign-execution.v1"
BRANCH_SCHEMA = "untirta.frozen-rotor-stage4-branch-execution.v1"
MAX_SECONDS = 1200
MAX_CAMPAIGN_BYTES = 1024**3
MAX_BRANCH_BYTES = 512 * 1024**2
MAX_LOG = 32 * 1024**2
MAX_PATHS = 4096
MAX_CLOCK_STEPS = 100_000
BASE = native.preparation.base


class Stage4ExecutionIssue(ValueError):
    pass


def require(condition, action):
    if not condition:
        raise Stage4ExecutionIssue(action)


def case_module():
    return importlib.import_module("frozen_rotor_stage4_case")


def reduction_module():
    return importlib.import_module("reduce_stage4_campaign")


def canonical(value):
    return native.preparation.canonical(value)


def record(root, relative, *, limit=BASE.inventory.MAX_REPORT_BYTES):
    relative = native.preparation.relative_path(relative)
    path = Path(root) / relative
    for parent in path.parents:
        if parent == Path(root):
            break
        require(not parent.is_symlink() and not getattr(parent, "is_junction", lambda: False)(), "Native evidence cannot traverse links.")
    raw = BASE.read_regular(path, limit)
    return raw, {"path": relative, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def write_report(root, name, value, *, canonical_bytes=False):
    raw = canonical(value) if canonical_bytes else BASE.inventory.bounded_json(value)
    require(len(raw) <= BASE.inventory.MAX_REPORT_BYTES, "A native metadata record exceeds 128 KiB.")
    BASE.write_exclusive(Path(root) / name, raw)
    return {"path": name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def context_guard():
    native.context_guard()
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    require(event_path, "The actual manual public GitHub event is required before preparation.")
    raw = BASE.read_regular(Path(event_path), 1024**2)
    event = json.loads(raw)
    repository = event.get("repository", {})
    require(repository.get("full_name") == "simcalc/PyAnsys" and repository.get("private") is False
            and repository.get("visibility", "public") == "public", "Actual public repository context is required; no paid or custom runner fallback.")


def workspace_size(directory, limit=MAX_CAMPAIGN_BYTES):
    root = BASE.regular_directory(directory)
    total, count = 0, 0
    for path in root.rglob("*"):
        count += 1
        require(count <= MAX_PATHS and len(path.relative_to(root).parts) <= 8, "The campaign path/depth inventory exceeds its bound.")
        require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)(), "The independent native workspace cannot contain links or junctions.")
        if path.is_file():
            size = path.stat().st_size
            require(size <= native.preparation.MAX_FILE_BYTES, "An actual native file exceeds 128 MiB.")
            total += size
            require(total <= limit, "The independent campaign or branch workspace reached its declared byte bound.")
    return total


def fixed_clock(log_text, prepared):
    """Verify every actual solver time against the authored fixed time grid."""
    start = native.preparation.protocol.number(prepared["initialTimeS"], nonnegative=True)
    end = native.preparation.protocol.number(prepared["endTimeS"], positive=True)
    dt = native.preparation.protocol.number(prepared["timeStepS"], positive=True)
    steps = (end-start) / dt
    require(end > start and math.isclose(steps, round(steps), rel_tol=0, abs_tol=1e-7)
            and 1 <= round(steps) <= MAX_CLOCK_STEPS, "The actual branch needs a bounded exact fixed time grid.")
    times = [float(match[1]) for match in re.finditer(r"(?m)^Time = (" + native.reduction.FLOAT + r")s?\s*$", log_text)]
    require(len(times) == round(steps) and len(re.findall(r"(?m)^Time = ", log_text)) == len(times)
            and all(math.isfinite(t) for t in times), "Every actual native physical time step must be retained in the completed solver log.")
    tolerance = max(1e-12, dt*1e-7)
    require(all(math.isclose(t, start+(index+1)*dt, rel_tol=0, abs_tol=tolerance) for index, t in enumerate(times)),
            "Actual native physical times must match the fixed grid; adaptive or skipped steps are unsupported.")
    logged_dt = [float(match[1]) for match in re.finditer(r"(?m)^deltaT = (" + native.reduction.FLOAT + r")s?\s*$", log_text)]
    require(len(re.findall(r"(?m)^deltaT = ", log_text)) == len(logged_dt)
            and all(math.isfinite(value) and math.isclose(value, dt, rel_tol=0, abs_tol=tolerance) for value in logged_dt),
            "Actual printed native deltaT must match the authored fixed clock.")
    maxima = {}
    for prefix, key in (("", "maximumCo"), ("Interface ", "maximumAlphaCo")):
        values = [float(match[1]) for match in re.finditer(r"(?m)^" + prefix + r"Courant Number mean: " + native.reduction.FLOAT
                                                         + r" max: (" + native.reduction.FLOAT + r")\s*$", log_text)]
        require(values and all(math.isfinite(value) and value >= 0 for value in values), "Actual finite native Co and alphaCo histories are required.")
        maxima[key] = max(values)
    increments = [times[0]-start] + [b-a for a, b in zip(times, times[1:])]
    return {"fixedClockVerified": True, "physicalTimeStepS": dt, "physicalStartTimeS": start, "physicalEndTimeS": times[-1],
            "nativeStepCount": len(times), "minimumObservedTimeIncrementS": min(increments), "maximumObservedTimeIncrementS": max(increments),
            "timeGridAbsoluteToleranceS": tolerance, "printedDeltaTRecordCount": len(logged_dt),
            "clockBasis": "All actual solver Time records and authored adjustTimeStep=no; optional printed deltaT is independently checked.",
            **maxima, "courantCriteriaSatisfied": maxima["maximumCo"] <= .5 and maxima["maximumAlphaCo"] <= .25,
            "simulationAcceptanceClaimed": False}


def execute(arguments, log_path, branch, campaign, deadline, runtime):
    """Only two hash-bound native binaries; one owned serial process session."""
    require(arguments in native.commands(branch), "Execute only fixed checkMesh or foamRun arguments for this fresh branch.")
    name = arguments[0]
    expected = "00-checkMesh.log" if name == "checkMesh" else "01-foamRun.log"
    require(Path(log_path).absolute() == Path(branch).absolute() / "logs" / expected, "Use only fixed fresh immutable native log paths.")
    BASE.regular_directory(Path(log_path).parent)
    require(not Path(log_path).exists() and not Path(log_path).is_symlink(), "Native branch logs cannot overwrite prior evidence.")
    row = next((item for item in runtime["binaries"] if item["program"] == name), None)
    require(row is not None, "A real native binary identity is required.")
    executable = Path(row["path"])
    require(executable.resolve().is_relative_to(Path("/opt/openfoam12")), "Only the official Foundation-12 native binary may execute.")
    raw = BASE.read_regular(executable, native.preparation.MAX_FILE_BYTES)
    require(len(raw) == row["bytes"] and sha256(raw).hexdigest() == row["sha256"], "Native executable bytes changed after binding.")
    require(time.monotonic() < deadline, "The total serial campaign deadline was reached before native startup.")
    workspace_size(branch, MAX_BRANCH_BYTES); workspace_size(campaign)
    started, total, process = time.monotonic(), 0, None
    selector = selectors.DefaultSelector()
    try:
        with Path(log_path).open("xb") as output:
            process = subprocess.Popen([str(executable), *arguments[1:]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=native.child_environment(), start_new_session=True, preexec_fn=native.child_limits, bufsize=0)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                require(time.monotonic() < deadline, "The bounded total native campaign timed out.")
                workspace_size(branch, MAX_BRANCH_BYTES); workspace_size(campaign)
                for key, _ in selector.select(timeout=.25):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining = MAX_LOG-total
                    output.write(chunk[:remaining]); output.flush()
                    total += min(len(chunk), remaining)
                    require(len(chunk) <= remaining, "The immutable native branch log reached its 32 MiB bound.")
            code = process.wait(timeout=3)
            workspace_size(branch, MAX_BRANCH_BYTES); workspace_size(campaign)
            require(time.monotonic() < deadline, "The total native deadline was reached at process completion.")
            require(code == 0, name + " returned native exit " + str(code))
    finally:
        if process is not None:
            native.stop_owned(process)
            process.stdout.close()
        selector.close()
    raw, log_row = record(branch, "logs/" + expected, limit=MAX_LOG)
    text = raw.decode("utf-8", errors="strict")
    require(re.search(r"(?m)^End\s*$", text) and not re.search(r"FOAM FATAL|Segmentation fault|Floating point exception", text, re.I),
            "Actual native completion without a fatal error is required: " + name)
    return {**log_row, "program": name, "exitCode": 0, "elapsedSeconds": round(time.monotonic()-started, 3)}, text


def input_records(directory, prepared):
    authored, mesh = prepared["authoredInputFiles"], prepared["referenceMeshFiles"]
    # The manifest builder first requires exact authored recipe bytes, including
    # its fixed official constraint include. Generated topology stays plain.
    result = native.preparation.file_records(directory / "case", authored, {row["path"] for row in authored})
    if mesh:
        result += native.preparation.file_records(directory / "case", mesh, {row["path"] for row in mesh}, plain_native=True)
    return [{**row, "path": "case/"+row["path"]} for row in result]


def retain_logs(directory):
    rows = []
    for name in ("00-checkMesh.log", "01-foamRun.log"):
        path = directory / "logs" / name
        if path.exists():
            require(not path.is_symlink() and path.is_file() and path.stat().st_size <= MAX_LOG, "Retain only bounded regular native log files.")
            raw = BASE.read_regular(path, MAX_LOG) if path.stat().st_size else b""
            rows.append({"path": "logs/" + name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()})
    return rows


def run_branch(directory, prepared, runtime, campaign, deadline):
    directory = BASE.regular_directory(directory)
    runtime_raw, runtime_row = record(directory, "stage4-runtime-identity.json")
    require(runtime_raw == canonical(runtime), "The prepared actual runtime identity changed before native execution.")
    family = reduction_module().build_manifest(directory, prepared)
    manifest_row = write_report(directory, "stage4-input-manifest.json", family["manifest"], canonical_bytes=True)
    study_row = write_report(directory, "stage4-classroom-study.json", family["study"], canonical_bytes=True)
    prep_raw, prep_row = record(directory, "stage4-branch-preparation.json")
    actual_preparation = native.preparation.json_file(directory / "stage4-branch-preparation.json")
    # The builder checked the supplied variant against these actual bytes;
    # use its current physical declarations and current native role inventory.
    if "variant" in actual_preparation:
        prepared = {**prepared, **actual_preparation, **actual_preparation["variant"]}
    inputs = input_records(directory, prepared)
    artifacts = {"stage4-branch-preparation.json": prep_raw, "stage4-runtime-identity.json": runtime_raw,
                 "stage4-input-manifest.json": canonical(family["manifest"]), "stage4-classroom-study.json": canonical(family["study"])}
    geometry_raw, geometry_row = record(directory, "geometry-identity.json")
    require(geometry_row == prepared["sourceGeometryIdentityFile"], "The actual source geometry identity must match its preparation binding.")
    artifacts["geometry-identity.json"] = geometry_raw
    checkpoint_inputs = []
    if prepared["id"] == "resumed":
        for name in ("stage4-checkpoint.json", "stage4-resume-preparation.json"):
            artifacts[name] = record(directory, name)[0]
        checkpoint = native.preparation.json_file(directory / "stage4-checkpoint.json")
        require(checkpoint["checkpointSha256"] == prepared["checkpointSha256"], "Resume must bind the exact copied complete native checkpoint.")
        checkpoint_inputs = native.preparation.file_records(directory, checkpoint["files"], {row["path"] for row in checkpoint["files"]}, plain_native=True)
    report = {"schemaVersion": BRANCH_SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(), "id": prepared["id"],
              "status": "NATIVE_STAGE4_EXECUTION_REQUIRED", "exampleDataCommit": BASE.inventory.PIN, "openFoamFoundationVersion": 12,
              "mode": "transient-sliding", "preparationReportSha256": prep_row["sha256"], "runtimeIdentitySha256": runtime_row["sha256"],
              "inputManifestSha256": manifest_row["sha256"], "studySha256": study_row["sha256"],
              "geometryIdentitySha256": geometry_row["sha256"],
              "temporalPlan": {key: prepared[key] for key in ("initialTimeS", "endTimeS", "timeStepS")},
              "stages": [], "nativeCheckMeshVerified": False, "nativeSolverCompleted": False, "cavitationModelExecuted": False,
              "finiteNativeEndFieldsVerified": False, "physicalTransientExecuted": False, "fixedClockVerified": False,
              "simulationExecuted": False, "actualCfdRunVerified": False, "inputArtifactsImmutable": False,
              "timeStepConvergenceVerified": False, "restartVerified": False, "meshConvergenceVerified": False,
              "mainApplicationWrites": False, "claimBoundary": "Immutable native execution facts only; independent numerical acceptance is required."}
    if prepared["id"] == "resumed":
        report["checkpointSha256"] = prepared["checkpointSha256"]
        report["checkpointInputRecords"] = checkpoint_inputs
    (directory / "logs").mkdir()
    started, current = time.monotonic(), "checkMesh"
    try:
        stage, text = execute(native.commands(directory)[0], directory / "logs/00-checkMesh.log", directory, campaign, deadline, runtime)
        report["stages"].append(stage)
        mesh = native.reduction.parse_mesh_quality(text)
        report.update(nativeCheckMeshVerified=True, nativeMeshQuality=mesh)
        current = "foamRun"
        stage, solver = execute(native.commands(directory)[1], directory / "logs/01-foamRun.log", directory, campaign, deadline, runtime)
        report["stages"].append(stage)
        report["nativeSolverCompleted"] = True
        require(re.search(r"(?m)^Selecting cavitation model SchnerrSauer\s*$", solver), "Actual native SchnerrSauer selection must appear in the completed solver log.")
        report["cavitationModelExecuted"] = True
        clock = fixed_clock(solver, prepared)
        report.update(fixedClockEvidence=clock, fixedClockVerified=True)
        fields, times = native.final_fields(directory, {"endTimeS": prepared["endTimeS"]}, mesh["meshCellCount"], solver)
        report.update(finalNativeFieldRecords=fields, nativeEndTimeS=times[-1][0], finiteNativeEndFieldsVerified=True,
                      nativeTimeDirectories=[{"physicalTimeS": instant, "name": path.name} for instant, path in times], simulationExecuted=True)
        report["rotorMotionEvidence"] = native.rotor_motion(directory, times, solver)
        report.update(physicalTransientExecuted=True, nativeSamplingRecords=native.sampling_records(directory))
        require(input_records(directory, prepared) == inputs and all(record(directory, name)[0] == raw for name, raw in artifacts.items()),
                "Actual authored inputs or bound source geometry changed during native execution.")
        if checkpoint_inputs:
            require(native.preparation.file_records(directory, checkpoint_inputs, {row["path"] for row in checkpoint_inputs}, plain_native=True) == checkpoint_inputs,
                    "The native checkpoint inputs changed during resumed execution.")
        require(time.monotonic() < deadline, "The total native deadline was reached before final evidence verification.")
        report.update(inputArtifactsImmutable=True, status="STAGE4_NATIVE_EXECUTED_ACCEPTANCE_REQUIRED", actualCfdRunVerified=True)
    except Exception as error:
        report.update(status="NATIVE_STAGE4_CORRECTION_REQUIRED", reasonCode="CFD_STAGE4_NATIVE_CORRECTION_REQUIRED",
                      diagnostic={"program": current, "type": type(error).__name__, "message": str(error)[:500]})
    try:
        report["retainedNativeLogRecords"] = retain_logs(directory)
        report["workspaceBytes"] = workspace_size(directory, MAX_BRANCH_BYTES)
    except Exception as error:
        report.update(status="NATIVE_STAGE4_CORRECTION_REQUIRED", reasonCode="CFD_STAGE4_RETENTION_REQUIRED", actualCfdRunVerified=False,
                      retentionDiagnostic={"type": type(error).__name__, "message": str(error)[:500]})
    report["elapsedSeconds"] = round(time.monotonic()-started, 3)
    row = write_report(directory, "stage4-branch-execution.json", report)
    return report, row


def run(baseline_directory, packet, output_directory, *, pressure_absolute_pa):
    context_guard()
    require(isinstance(packet, dict) and packet.get("schemaVersion") == native.preparation.NATIVE_REDUCTION_SCHEMA,
            "Use only the actual qualified source native baseline packet; no fixture or synthetic wrapper is an execution input.")
    output = Path(output_directory).absolute()
    BASE.inventory.output_path(output / "stage4-campaign-execution.json")
    require(not output.exists() and not output.is_symlink(), "Use a fresh independent RUNNER_TEMP campaign directory.")
    runtime = native.program_identity()
    prepared = case_module().prepare_campaign(baseline_directory, packet, output, pressure_absolute_pa=pressure_absolute_pa,
                                             revolutions=5.5, runtime_identity=runtime)
    directory = BASE.regular_directory(output)
    prep_raw, prep_row = record(directory, "stage4-case-preparation.json")
    variants = prepared["variants"]
    require(len(variants) == 3 and [item["id"] for item in variants] == ["coarse", "medium", "fine"], "Require the exact three controlled fixed-clock branches.")
    report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(), "exampleDataCommit": BASE.inventory.PIN,
              "status": "STAGE4_NATIVE_EVIDENCE_REQUIRED", "preparationReportSha256": prep_row["sha256"],
              "branches": [], "resumedBranch": None, "checkpoint": None, "actualCfdRunVerified": False,
              "simulationExecuted": False, "timeCampaignExecuted": False, "restartCampaignExecuted": False,
              "timeStepConvergenceVerified": False, "restartVerified": False, "meshConvergenceVerified": False,
              "meshBranchStatus": "IndependentActualMeshFamilyRequired", "sourceBaselineImmutable": False,
              "limits": {"cpus": 1, "childAddressSpaceBytes": native.MAX_MEMORY, "maximumSeconds": MAX_SECONDS,
                         "maximumCampaignBytes": MAX_CAMPAIGN_BYTES, "maximumBranchBytes": MAX_BRANCH_BYTES, "maximumLogBytes": MAX_LOG},
              "mainApplicationWrites": False, "paidFallback": False, "customRunnerAllowed": False,
              "claimBoundary": "Fixed-clock execution and meaningful restart experiments only; actual matched-field/time/mesh acceptance remains independently derived."}
    started = time.monotonic(); deadline = started+MAX_SECONDS
    results = {}
    try:
        workspace_size(directory)
        for variant in variants:
            name = native.preparation.relative_path(variant["casePath"])
            require(name == variant["id"]+"-case", "Use the exact preparer's fresh branch directory only.")
            try:
                actual, row = run_branch(directory / name, variant, runtime, directory, deadline)
            except Exception as error:
                report["branches"].append({"id": variant["id"], "casePath": name, "actualCfdRunVerified": False,
                    "status": "NATIVE_STAGE4_BRANCH_PREFLIGHT_REQUIRED", "diagnostic": {"type": type(error).__name__, "message": str(error)[:500]}})
                if time.monotonic() >= deadline:
                    break
                continue
            report["branches"].append({"id": variant["id"], "casePath": name, "report": {**row, "path": name+"/"+row["path"]},
                                       "actualCfdRunVerified": actual["actualCfdRunVerified"], "status": actual["status"]})
            results[variant["id"]] = actual
            if not actual["nativeCheckMeshVerified"]:
                break  # All three use this same actual reference mesh; the shared dependency failed.
        report["timeCampaignExecuted"] = len(results) == 3 and all(item["actualCfdRunVerified"] for item in results.values())
        report["simulationExecuted"] = any(item["simulationExecuted"] for item in results.values())
        finest = results.get("fine")
        if finest and finest["actualCfdRunVerified"]:
            variant = variants[2]
            checkpoint_time = prepared["checkpointPlan"]["physicalTimeS"]
            matches = [row["name"] for row in finest["nativeTimeDirectories"] if math.isclose(row["physicalTimeS"], checkpoint_time, rel_tol=0, abs_tol=1e-12)]
            require(len(matches) == 1, "The actual finest run must retain the exact authored native checkpoint time.")
            checkpoint = case_module().build_checkpoint(directory, variant, matches[0])
            checkpoint_row = write_report(directory, "stage4-native-checkpoint.json", checkpoint)
            report["checkpoint"] = checkpoint_row
            resume_preparation = case_module().prepare_resume(directory, checkpoint, directory / "resumed-case")
            resumed = {**resume_preparation["variant"], **resume_preparation}
            actual, row = run_branch(directory / "resumed-case", resumed, runtime, directory, deadline)
            report["resumedBranch"] = {"id": "resumed", "casePath": "resumed-case", "report": {**row, "path": "resumed-case/"+row["path"]},
                                       "actualCfdRunVerified": actual["actualCfdRunVerified"], "status": actual["status"]}
            report["restartCampaignExecuted"] = actual["actualCfdRunVerified"]
            report["simulationExecuted"] |= actual["simulationExecuted"]
        require(record(directory, "stage4-case-preparation.json")[0] == prep_raw, "The immutable authored campaign changed during native execution.")
        native.preparation.verify_baseline(baseline_directory, packet)
        report["sourceBaselineImmutable"] = True
        report["workspaceBytes"] = workspace_size(directory)
        report["actualCfdRunVerified"] = report["timeCampaignExecuted"] and report["restartCampaignExecuted"]
        if report["actualCfdRunVerified"]:
            report["status"] = "STAGE4_EXECUTED_ACCEPTANCE_REQUIRED"
        else:
            report.update(status="NATIVE_STAGE4_CORRECTION_REQUIRED", reasonCode="CFD_STAGE4_INCOMPLETE_NATIVE_CAMPAIGN")
    except Exception as error:
        report.update(status="NATIVE_STAGE4_CORRECTION_REQUIRED", reasonCode="CFD_STAGE4_CAMPAIGN_CORRECTION_REQUIRED",
                      diagnostic={"type": type(error).__name__, "message": str(error)[:500]})
    report["elapsedSeconds"] = round(time.monotonic()-started, 3)
    write_report(directory, "stage4-campaign-execution.json", report)
    return {key: report[key] for key in ("status", "actualCfdRunVerified", "simulationExecuted", "timeCampaignExecuted",
                                         "restartCampaignExecuted", "timeStepConvergenceVerified", "restartVerified", "meshConvergenceVerified", "mainApplicationWrites")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-case-directory", type=Path, required=True)
    parser.add_argument("--baseline-evidence-json", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--pressure-absolute-pa", type=float, required=True)
    args = parser.parse_args()
    try:
        result = run(args.baseline_case_directory, native.preparation.json_file(args.baseline_evidence_json), args.output_directory,
                     pressure_absolute_pa=args.pressure_absolute_pa)
        print(json.dumps(result, allow_nan=False))
        return 0 if result["actualCfdRunVerified"] else 2
    except (ValueError, OSError, KeyError, TypeError, ImportError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "NATIVE_STAGE4_EXECUTION_REQUIRED", "reasonCode": "CFD_STAGE4_PREFLIGHT_REQUIRED",
                          "action": str(error)[:500], "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
