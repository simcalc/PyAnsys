# PyAnsys · independent PyFluent SDK installation

This public educational repository installs **PyFluent (`ansys-fluent-core==0.42.2`)** on a standard GitHub-hosted Linux runner. It is maintained by **simcalc** and is independent of the official [PyAnsys project](https://github.com/ansys/pyfluent).

The initial component is the PyFluent SDK and all its required Python dependencies. Optional PyFluent Visualization/Parametric packages and other PyAnsys products are outside this installation.

## Run the installation on GitHub

1. Open **Actions → Install and verify PyFluent SDK → Run workflow**.
2. The manual job requires this exact repository to be public and uses standard `ubuntu-22.04` with CPython **3.12.10**, with a ten-minute limit.
3. It downloads official PyPI binary wheels, creates an isolated environment in the temporary runner, installs the SDK, runs `pip check`, and verifies public API imports.
4. It checks each wheel against its upstream PyPI SHA-256, records actual sizes, then extracts the generated offline ZIP and actually installs it into a second fresh environment. API imports, `pip check`, and the exact distribution inventory must pass before publishing software under **Releases**. It then anonymously downloads the release assets and verifies their checksums.

Standard GitHub-hosted runners are [free for public repositories](https://docs.github.com/en/actions/reference/runners/github-hosted-runners). This workflow has no paid runner fallback, persistent server, cache, Actions artifact storage, schedule, billing changes, or external compute provider. It runs only when manually dispatched. Software is preserved in [GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases); the temporary installed runner environment is removed by GitHub after the job.

## Download the actual software

Open **Releases**, then choose:

- `pyfluent-sdk-0.42.2-cpython312-linux-x64.zip`: the actual SDK wheel **and every required dependency wheel**, offline installer, resolved versions, upstream digests, and actual installation evidence. Target: **Linux x64 / CPython 3.12**.
- `ansys_fluent_core-0.42.2-...whl`: the original, unmodified upstream SDK wheel. Its dependencies are in the bundle; the wheel by itself is not a complete dependency installation.
- `SHA256SUMS`: SHA-256 checksums for all published assets.
- `wheel-manifest.json`: source URLs, individual wheel sizes, versions, SHA-256 values and upstream license-file inventory.
- `sdk-verification.json`: actual SDK import, dependency, platform, commit, run ID and installed-size evidence.
- `offline-verification.json`: actual fresh offline installation/import evidence bound to the exact ZIP SHA-256.

The repository's **Code → Download ZIP** contains the setup source. The downloadable software is in the **Release assets**. Neither contains the proprietary Ansys Fluent solver or Python runtime.

For the offline Linux installation, verify the bundle's SHA-256, extract it into a new folder, and run:

```sh
python3.12 install_offline.py
```

Python 3.12 and `venv` support must already be installed. The installer checks all wheel hashes, uses `--no-index --require-hashes`, creates `.venv` and runs `pip check`. It refuses to replace an existing environment. Use a compatible Linux/glibc environment such as Ubuntu 22.04/24.04. The Linux dependency wheels are **not** Windows or macOS wheels.

## Optional installation on Windows

This is separate from the GitHub installation and does not run automatically on a student's laptop:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple ansys-fluent-core==0.42.2
.\.venv\Scripts\python.exe -m pip check
```

This obtains the dependencies appropriate for Windows; it does not install Ansys Fluent.

## SDK readiness and CFD readiness

| Check | This workflow |
| --- | --- |
| PyFluent SDK installation and dependency resolution | Verified by actual runner execution |
| `ansys.fluent.core` import and public client functions | Verified without launching a solver |
| Native Fluent installation and license | Separate; not supplied or verified |
| Fluent session connection, physical CFD, cavitation or transient results | Not executed |
| Persistent hosted API endpoint or API key | Not provided by SDK installation |
| Main application, pump database and Lab study data | Not included or modified |

The [official installation documentation](https://fluent.docs.pyansys.com/version/stable/getting_started/installation.html) requires a licensed Ansys Fluent installation for interactive solver use. Installing the Python package exposes the client API; it does not provision a cloud Fluent server. The published PyPI wheel is used; development API generation that requires Fluent is not run here.

SDK version 0.42.2 matches the existing optional CFD adapter's declared pin. Successful installation here does not change that adapter's capability, license or physical-validation gates. The existing OpenFOAM learning benchmarks remain separate.

## Isolation and provenance

Only this authored setup, tests and documentation are tracked. No Main application source, manufacturer data, geometry, study snapshots, database, secret or credential is copied. The GitHub publication token exists only in the release step and is not recorded in output. The top-level MIT license covers this repository's authored helpers; original downloaded wheels retain their own upstream licenses. Existing published release assets are preserved and rechecked on subsequent runs; no claim of GitHub platform-enforced release immutability is made.

The fixed workflow action commits are official `actions/checkout` v7.0.1 and `actions/setup-python` v7.0.0. Required dependency versions are frozen in each release's `requirements-resolved.txt`, with one SHA-256 per downloaded wheel. A future dependency change requires a separately reviewed release.
