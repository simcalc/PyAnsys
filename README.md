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

Only authored setup, tests and documentation are tracked. Main application source, manufacturer data, study snapshots, database, secrets and credentials are excluded. The separate Frozen Rotor learning release below contains only the official Ansys educational example and meshes. The GitHub publication token exists only in release publishing steps and is not recorded in output. The top-level MIT license covers this repository's authored helpers; original downloaded wheels and educational files retain their upstream notices. Existing published release assets are preserved and rechecked; no claim of GitHub platform-enforced release immutability is made.

The fixed workflow action commits are official `actions/checkout` v7.0.1 and `actions/setup-python` v7.0.0. Required dependency versions are frozen in each release's `requirements-resolved.txt`, with one SHA-256 per downloaded wheel. A future dependency change requires a separately reviewed release.

## Impeller–Volute Frozen Rotor learning example

The [learning release](https://github.com/simcalc/PyAnsys/releases/tag/frozen-rotor-learning-20261011) provides the byte-verified official `frozen_rotor_workflow.py`, unexecuted notebook and both **actual native HDF5 meshes**, with upstream MIT notices and SHA-256 provenance. The original source-only ZIP is also preserved separately. `Code → Download ZIP` contains helpers; download the complete learning package from Releases.

Meshes are pinned to official `ansys/example-data` commit `8ef505f6ef699eeec68a16001df91ba353342f40`. They total **48,520,015 bytes**; the source ZIP alone is **34,935 bytes**. The installer verifies native mesh bytes against their Git LFS object SHA-256 values; pointer files cannot pass.

This package was prepared without executing the supplied script, launching Fluent or producing a case/result. It is a **steady single-phase MRF example**, not a cavitation pressure sweep, NPSHr qualification or physical transient run. Native licensed Fluent **2025 R2 or later** is required for actual execution. The source's angular unit mapping, material/report constants, absolute pressure datum, mesh/interface and four-process resource setting require explicit native checks before solving. Read `README-learning.txt` and `example-provenance.json` first. These source settings do not override the separate CFD worker limits.

`tools/build_learning_package.py` packages only the exact reviewed official file inventory and refuses checksum mismatches or solver-result claims. It does not import or run tutorial code. The learning bundle is separate from the Linux SDK wheel bundle; neither supplies the proprietary Fluent solver.

## Actual mesh inventory before selecting a FREE solver

Run **Actions → Inspect official Frozen Rotor mesh → Run workflow** to read both pinned official HDF5 meshes on the standard public GitHub runner. A separate reader environment uses `h5py==3.16.0` and `numpy==2.5.4`; it does not install or launch Fluent. The job publishes only a small metadata report and checksum in a new Release, without Actions artifact storage, scheduled execution or a paid runner fallback.

The inspector verifies actual file sizes and SHA-256 before reading topology, surface names and coordinate metadata. It rejects external HDF5 links and preserves the distinction between mesh inventory and native mesh quality. Original tutorial code is treated as data and is never imported or executed.

Compare the **combined impeller plus volute** cell count against the Student edition's current limit. The official [Ansys Student page](https://ansys.synopsys.com/academic/students/ansys-student) lists a fluid limit of one million cells/nodes and a 50 GB disk requirement. That requirement is not a measured installer size, and a model within the cell limit still needs a compatible licensed local solver, RAM budget, mesh/interface checks and numerical acceptance. SDK installation and this inventory do not satisfy those execution gates.
