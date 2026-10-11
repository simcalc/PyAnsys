"""Require this public repository on a standard, ephemeral Linux runner."""
import json
import os
from pathlib import Path
import urllib.request

REPOSITORY = "simcalc/PyAnsys"


def validate_context(env, event, public_repository):
    expected = {
        "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": REPOSITORY, "RUNNER_OS": "Linux",
        "RUNNER_ENVIRONMENT": "github-hosted",
    }
    for key, value in expected.items():
        if env.get(key) != value:
            raise ValueError(f"Public runner context rejected: {key}")
    for source in (event.get("repository", {}), public_repository):
        if source.get("full_name") != REPOSITORY or source.get("private") is not False:
            raise ValueError("The exact repository must be public")
        if source.get("visibility", "public") != "public":
            raise ValueError("Repository visibility must be public")
    return {"accepted": True, "repository": REPOSITORY,
            "publicRepositoryVerified": True, "runner": "github-hosted Linux",
            "paidFallback": False, "customRunnerAllowed": False,
            "billingApiVerification": False, "mainApplicationWrites": False}


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "PyAnsys-public-sdk-check"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read(1_048_577)
    if len(payload) > 1_048_576:
        raise ValueError("Repository response exceeds limit")
    print(json.dumps(validate_context(os.environ, event, json.loads(payload))))


if __name__ == "__main__":
    main()
