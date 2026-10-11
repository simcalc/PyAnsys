"""Admit one public approximate MRF artifact, never qualify its CFD results.

This separate route supports only an active high-pressure fixed-MRF reference.
Native evidence is re-read and reduced after download; GitHub metadata alone
cannot provide numerical, geometry, cavitation or NPSHr authority.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

REPOSITORY = "simcalc/PyAnsys"
SOURCE_WORKFLOW = ".github/workflows/snappy-baseline-frozen-rotor.yml"
ARTIFACT_PREFIX = "frozen-rotor-snappy-baseline-"
MAX_ARTIFACT_BYTES = 1024**3
MAX_METADATA_BYTES = 128 * 1024
MODE = "high-pressure-active-phase-MRF-reference"
PRESSURE_PA = 300000.0
PERIOD_S = 60 / 1450
TIME_STEP_S = PERIOD_S / 360
DURATION_S = 4 * PERIOD_S


def require(value, action):
    if not value:
        raise ValueError(action)


def validate_inputs(env):
    run_id = env.get("BASELINE_RUN_ID", "")
    require(isinstance(run_id, str) and re.fullmatch(r"[1-9][0-9]{0,18}", run_id),
        "Use one positive decimal completed approximate MRF run ID.")
    return {"baselineRunId": run_id, "mode": MODE, "pressureAbsolutePa": PRESSURE_PA,
        "maximumTimeStepS": TIME_STEP_S, "durationS": DURATION_S,
        "sourceNumericalBaselineQualified": False, "artifactBytesVerified": False,
        "actualCfdRunVerified": False, "phaseFamilyReferenceQualified": False,
        "cavitationSweepExecuted": False, "physicalTransientExecuted": False,
        "npshrCalculated": False, "mainApplicationWrites": False}


def validate_source(inputs, run, artifacts):
    require(isinstance(inputs, dict) and inputs == validate_inputs({"BASELINE_RUN_ID": inputs.get("baselineRunId")}),
        "Use the exact fixed-pressure, fixed-MRF preflight input contract.")
    require(isinstance(run, dict) and isinstance(artifacts, dict), "Supply both actual GitHub metadata objects.")
    run_id = inputs["baselineRunId"]
    require(type(run.get("id")) is int and str(run["id"]) == run_id
        and run.get("status") == "completed" and run.get("conclusion") == "success"
        and run.get("event") == "workflow_dispatch" and run.get("path") == SOURCE_WORKFLOW
        and isinstance(run.get("head_sha"), str) and re.fullmatch(r"[0-9a-f]{40}", run["head_sha"]),
        "Only an actual successful manual run of the distinct approximate MRF workflow is admitted.")
    for key in ("repository", "head_repository"):
        row = run.get(key)
        require(isinstance(row, dict) and row.get("full_name") == REPOSITORY and row.get("private") is False,
            "The source and head must both be the exact independent public PyAnsys repository.")
    rows = artifacts.get("artifacts")
    require(isinstance(rows, list) and len(rows) <= 100 and all(isinstance(row, dict) for row in rows),
        "Use the bounded actual artifact listing.")
    name = ARTIFACT_PREFIX + run_id
    matches = [row for row in rows if row.get("name") == name]
    require(len(matches) == 1, "One exact retained approximate MRF evidence artifact is required.")
    row = matches[0]
    workflow_run = row.get("workflow_run")
    require(type(row.get("id")) is int and row["id"] > 0 and row.get("expired") is False
        and type(row.get("size_in_bytes")) is int and 0 < row["size_in_bytes"] <= MAX_ARTIFACT_BYTES
        and isinstance(workflow_run, dict) and type(workflow_run.get("id")) is int
        and str(workflow_run["id"]) == run_id,
        "The exact source artifact must be unexpired, bounded and bound to the same actual run.")
    digest = row.get("digest")
    require(isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest),
        "Retain the actual GitHub-reported archive digest separately from local byte verification.")
    return {**inputs, "artifactName": name, "artifactId": row["id"], "artifactBytes": row["size_in_bytes"],
        "githubReportedArchiveDigest": digest, "sourceWorkflow": SOURCE_WORKFLOW, "sourceCommit": run["head_sha"],
        "claimBoundary": "Metadata admission only. Current downloaded native bytes, approximate geometry/source bindings and numerical single-phase acceptance must be independently reverified before active-phase preparation."}


def read_json(path):
    path = Path(path)
    require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)()
        and path.is_file() and 0 < path.stat().st_size <= MAX_METADATA_BYTES,
        "Read one bounded regular GitHub metadata JSON file.")
    return json.loads(path.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-json", type=Path)
    parser.add_argument("--artifacts-json", type=Path)
    args = parser.parse_args()
    values = validate_inputs(os.environ)
    require(bool(args.run_json) == bool(args.artifacts_json), "Supply both exact source metadata files together.")
    if args.run_json:
        values = validate_source(values, read_json(args.run_json), read_json(args.artifacts_json))
    print(json.dumps(values, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
