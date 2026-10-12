"""Require this public repository on a standard, ephemeral Linux runner."""
import json
import os
from pathlib import Path
import urllib.request

REPOSITORY = "simcalc/PyAnsys"
PUBLIC_REPOSITORY_URL = f"https://api.github.com/repos/{REPOSITORY}"


class NoRepositoryRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Repository identity request redirects are prohibited")


def fetch_public_repository(env):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "PyAnsys-public-sdk-check"}
    token = env.get("PYANSYS_REPOSITORY_READ_TOKEN")
    if token:
        if not isinstance(token, str) or len(token) > 1024 or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError("Invalid temporary repository read credential format")
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(PUBLIC_REPOSITORY_URL, headers=headers)
    opener = urllib.request.build_opener(NoRepositoryRedirect())
    with opener.open(request, timeout=20) as response:
        if response.status != 200 or response.geturl() != PUBLIC_REPOSITORY_URL:
            raise ValueError("Repository identity requires the exact successful GitHub endpoint")
        payload = response.read(1_048_577)
    if len(payload) > 1_048_576:
        raise ValueError("Repository response exceeds limit")
    return json.loads(payload)


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
    print(json.dumps(validate_context(os.environ, event, fetch_public_repository(os.environ))))


if __name__ == "__main__":
    main()
