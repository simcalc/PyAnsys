"""Admit one immutable successful HXT volume step, never a failed baseline."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re

REPOSITORY = "simcalc/PyAnsys"
SOURCE_COMMIT = "1a28323dbc17fa3de1e6e5070d8bd6692da79f54"
SOURCE_WORKFLOW = ".github/workflows/remesh-extended-free-frozen-rotor.yml"
JOB_NAME = "FREE bounded full HXT and conditional preliminary MRF"
STEP_NAME = "Preserve exact native boundaries and attempt full parallel HXT optimisation"
STEP_NUMBER = 7  # Actual Actions jobs API includes its native "Set up job" first.
PREREQUISITE_STEPS = ("Set up job", "Check out the independent public repository",
    "Verify the exact public FREE standard runner and measured capacity", "Set up CPython 3.12.10",
    "Install the isolated pinned geometry runtime", "Verify resource profiles, geometry and engineering contracts")
DOWNSTREAM_STEPS = {"Install checksum-pinned OpenFOAM Foundation 12",
    "Convert and run preliminary MRF only after actual volume admission",
    "Require actual independent preliminary baseline numerical acceptance"}
MAX_ARTIFACT_BYTES = 300 * 1024**2
FALSE_FLAGS = ("sourceBaselineQualified", "actualCfdRunVerified", "simulationExecuted",
    "phaseFamilyReferenceQualified", "npsh3Verified", "restartVerified", "mainApplicationWrites")


def require(value, action):
    if not value:
        raise ValueError(action)


def validate_inputs(env):
    run_id = env.get("VOLUME_RUN_ID", "")
    require(isinstance(run_id, str) and re.fullmatch(r"[1-9][0-9]{0,18}", run_id),
            "Use an actual positive bounded source volume run ID.")
    return {"sourceRunId": run_id, **dict.fromkeys(FALSE_FLAGS, False)}


def validate_source(inputs, run, jobs, artifacts):
    run_id = inputs["sourceRunId"]
    require(isinstance(run, dict) and str(run.get("id")) == run_id
        and run.get("status") == "completed" and run.get("conclusion") in {"success", "failure"}
        and run.get("event") == "workflow_dispatch" and run.get("path") == SOURCE_WORKFLOW
        and run.get("head_sha") == SOURCE_COMMIT, "Require the exact completed pinned public extended HXT trial.")
    for key in ("repository", "head_repository"):
        repo = run.get(key, {})
        require(isinstance(repo, dict) and repo.get("full_name") == REPOSITORY and repo.get("private") is False
                and repo.get("visibility", "public") == "public", "Source repositories must be exactly the approved public repository.")
    rows = jobs.get("jobs") if isinstance(jobs, dict) else None
    require(isinstance(rows, list) and len(rows) == 1 and type(jobs.get("total_count")) is int and jobs["total_count"] == 1,
            "One exact standard-runner volume job is required.")
    job = rows[0]
    require(isinstance(job, dict) and type(job.get("id")) is int and 0 < job["id"] < 10**20
            and str(job.get("run_id")) == run_id and job.get("head_sha") == SOURCE_COMMIT
            and job.get("name") == JOB_NAME and job.get("status") == "completed"
            and job.get("conclusion") == run["conclusion"] and job.get("labels") == ["ubuntu-24.04"],
            "The actual job must bind the pinned source run and exact standard Ubuntu job.")
    steps = job.get("steps")
    require(isinstance(steps, list) and STEP_NUMBER <= len(steps) <= 32 and all(isinstance(step, dict)
        and type(step.get("number")) is int and 0 < step["number"] <= 32 for step in steps)
        and len({step["number"] for step in steps}) == len(steps), "Actual bounded uniquely numbered job steps are required.")
    matches = [step for step in steps if step.get("name") == STEP_NAME]
    require(len(matches) == 1 and matches[0]["number"] == STEP_NUMBER and matches[0].get("status") == "completed"
            and matches[0].get("conclusion") == "success", "The actual full HXT mesher step must have completed successfully.")
    failed = [step for step in steps if step.get("conclusion") == "failure"]
    require({step["number"] for step in steps if step["number"] < STEP_NUMBER} == set(range(1,STEP_NUMBER))
            and not any(step["number"] < STEP_NUMBER and (step.get("status") != "completed" or step.get("conclusion") != "success") for step in steps),
            "Every prerequisite before the source mesher must actually pass.")
    require(all(next(step for step in steps if step["number"]==number).get("name")==name
                for number,name in enumerate(PREREQUISITE_STEPS,1)), "Require all six exact reviewed native prerequisite steps.")
    require((not failed if run["conclusion"] == "success" else bool(failed) and all(
            step["number"] > STEP_NUMBER and step.get("status") == "completed" and step.get("name") in DOWNSTREAM_STEPS for step in failed)),
            "A failed run is admitted only when the successful volume step is followed by a declared downstream failure.")
    listings = artifacts.get("artifacts") if isinstance(artifacts, dict) else None
    require(isinstance(listings, list) and 0 < len(listings) <= 32, "Use the bounded actual artifact listing.")
    name = "frozen-rotor-extended-free-hxt-" + run_id
    matching = [row for row in listings if isinstance(row, dict) and row.get("name") == name]
    require(len(matching) == 1, "One exact source volume artifact is required.")
    artifact = matching[0]
    digest = artifact.get("digest", "")
    require(type(artifact.get("id")) is int and 0 < artifact["id"] < 10**20
            and type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= MAX_ARTIFACT_BYTES
            and artifact.get("expired") is False and str(artifact.get("workflow_run", {}).get("id")) == run_id
            and isinstance(digest,str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest),
            "Use the unexpired same-run artifact with actual bounded bytes and GitHub SHA256 digest.")
    return {**inputs, "sourceWorkflow": SOURCE_WORKFLOW, "sourceCommit": SOURCE_COMMIT,
        "sourceRunConclusion": run["conclusion"], "mesherJobId": job["id"], "mesherJobName": JOB_NAME,
        "mesherStep": {key:matches[0][key] for key in ("name", "number", "status", "conclusion")},
        "artifactId": artifact["id"], "artifactName": name, "artifactBytes": artifact["size_in_bytes"],
        "artifactSha256": digest.removeprefix("sha256:"), "githubReportedArchiveDigest": digest,
        "volumeProvenanceAdmitted": True,
        "claimBoundary": "Successful pinned native mesher provenance only; current mesh/source replay and fresh MRF numerical acceptance are mandatory. A failed downstream run is never a baseline."}


def read_json(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and not getattr(path, "is_junction", lambda:False)()
            and 0 < path.stat().st_size <= 128*1024, "Use bounded regular actual GitHub metadata.")
    def unique(pairs):
        result = {}
        for key,value in pairs:
            require(key not in result, "Duplicate metadata keys are prohibited."); result[key] = value
        return result
    return json.loads(path.read_bytes(), object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Finite metadata is required.")))


def validate_admission(value):
    require(isinstance(value, dict), "Use the complete independently admitted source metadata.")
    inputs = validate_inputs({"VOLUME_RUN_ID": value.get("sourceRunId")})
    repo = {"full_name":REPOSITORY,"private":False}
    conclusion = value.get("sourceRunConclusion")
    run = {"id":int(inputs["sourceRunId"]),"status":"completed","conclusion":conclusion,
        "event":"workflow_dispatch","path":value.get("sourceWorkflow"),"head_sha":value.get("sourceCommit"),
        "repository":repo,"head_repository":repo}
    steps = [{"number":n,"name":name,"status":"completed","conclusion":"success"} for n,name in enumerate(PREREQUISITE_STEPS,1)]
    steps.append(value.get("mesherStep"))
    if conclusion == "failure":
        steps.append({"number":9,"name":"Convert and run preliminary MRF only after actual volume admission",
                      "status":"completed","conclusion":"failure"})
    jobs = {"total_count":1,"jobs":[{"id":value.get("mesherJobId"),"run_id":int(inputs["sourceRunId"]),
        "head_sha":value.get("sourceCommit"),"name":value.get("mesherJobName"),"status":"completed",
        "conclusion":conclusion,"labels":["ubuntu-24.04"],"steps":steps}]}
    artifacts = {"artifacts":[{"id":value.get("artifactId"),"name":value.get("artifactName"),
        "expired":False,"size_in_bytes":value.get("artifactBytes"),"workflow_run":{"id":int(inputs["sourceRunId"])},
        "digest":value.get("githubReportedArchiveDigest")}]}
    expected = validate_source(inputs,run,jobs,artifacts)
    require(value == expected and set(value) == set(expected) and all(value.get(key) is False for key in FALSE_FLAGS)
            and value.get("volumeProvenanceAdmitted") is True,
            "The source admission must retain every exact reviewed metadata field and typed claim boundary.")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for role in ("run", "jobs", "artifacts"): parser.add_argument("--"+role+"-json",type=Path)
    args = parser.parse_args(); inputs = validate_inputs(os.environ)
    paths = [args.run_json,args.jobs_json,args.artifacts_json]
    require(all(paths) or not any(paths), "Supply all three exact GitHub metadata files together.")
    result = validate_source(inputs,*map(read_json,paths)) if all(paths) else inputs
    print(json.dumps(result,sort_keys=True,allow_nan=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
