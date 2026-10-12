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

### Actual inspected inventory · 11 October 2026

[Run 38152095914](https://github.com/simcalc/PyAnsys/actions/runs/38152095914) passed all **26 contracts** and inspected both actual checksum-pinned meshes. Its [metadata report](https://github.com/simcalc/PyAnsys/releases/download/frozen-rotor-mesh-review-38152095914/mesh-inspection.json) is **92,717 bytes**, SHA-256 `7c33b681d30be88ad2c5396d27e4990d34cf2463e28ab546ce52d01a27ff65e4`. The workflow downloaded the published report anonymously and compared its bytes with the original; the project separately repeated that verification.

| Stored component | Cells | Nodes |
| --- | ---: | ---: |
| Impeller | 773,352 | 837,936 |
| Volute | 368,411 | 122,551 |
| Sum before native append | **1,141,763** | **960,487** |

The contiguous stored zone ranges confirm these component counts. The original combined cell inventory **exceeds the Student cell limit by 141,763 cells**. The lower node count does not cancel the cell-limit violation. This inspection did not execute native append, verify geometric nonoverlap or create a joined case. The source's two cell zones and all 11 named boundary/interface surfaces are present in the stored metadata.

For the FREE Student path, derive and check a separate lighter learning mesh while preserving the official originals. Reducing the reported number or calling one component the full impeller–volute model would not satisfy the requirement. Coordinate units, mesh quality, interface consistency, RAM use, solver/license compatibility and numerical convergence remain unverified. This completed metadata run supplies no physical CFD, cavitation, NPSHr or transient result.

## FREE continuation from the original boundary geometry

The next manual workflow, **Extract official Frozen Rotor boundary surfaces**, prepares source geometry for a separately reviewed remeshing study. It reads every HDF5 face-node and coordinate section by its explicit global ID range. A face is selected from actual `c0`/`c1` adjacency, with exactly one exterior cell ID zero; a surface name alone cannot identify a boundary. Zone names and IDs, original checksums and derived-file checksums remain traceable.

The output contains tagged binary STL surfaces and `topology-report.json`. It checks native node-ID edge incidence, winding, duplicate faces, convex polygon projection and float32 triangulation. Coordinates retain **UNVERIFIED native units**. Per-zone open edges are expected at patch junctions; the combined component audit is recorded separately. Edge closure does not establish geometric self-intersection clearance, a valid fluid volume or solver mesh quality. No volume cells are created and no solver runs in this workflow.

The source reader is separate from the Main application and its database. Extraction is limited to 2 GiB address space, 300 seconds, one numerical thread and 25 MiB of surface/report output. Release assets include the upstream license and checksums; anonymous download verification follows publication.

| Stage | Required evidence |
| --- | --- |
| 1. Original boundary extraction | Complete section mapping, source/derived checksums, zone identity and honest topology findings |
| 2. New lighter volume mesh | Explicit coordinate-unit basis, boundary and interface mapping, geometric deviation, actual cells/nodes and volume quality |
| 3. Steady baseline | Native solver execution, compatible Q/RPM/fluid/pressure datum, residuals, conservation and numerical sensitivity |
| 4. Cavitation and NPSHr | Matched operating conditions, liquid/vapor properties, absolute suction pressure sweep and bracketed head-reduction criterion |
| 5. Physical transient | Actual rotor motion and time history, matched checkpoints/restart, time-step and spatial verification |

[Gmsh tutorial t13](https://gmsh.info/doc/texinfo/gmsh.html#t13) documents discrete-surface remeshing without an underlying CAD model. This is a possible next meshing method; it has not yet been executed or qualified for these impeller passages. Boundary identity must be checked after surface reclassification. A model meeting the Student count limit still needs a compatible native Student solver and numerical acceptance.

The alternative solver route is **OpenFOAM on a standard public GitHub runner**. The project's separate [OpenFoam repository](https://github.com/simcalc/OpenFoam) already contains small official educational impeller benchmarks. Those 30-degree sector benchmarks are separate from this full impeller–volute example. The [OpenFOAM v12 converter](https://doc.cfd.direct/openfoam/user-guide-v12/mesh-conversion) expects legacy ASCII Fluent `.msh`, so the original `.msh.h5` files require an actual reviewed conversion or a new mesh; renaming the extension does not convert them.

### Executed boundary preparation

[Run 38155649489](https://github.com/simcalc/PyAnsys/actions/runs/38155649489) passed **55/55 tests** and extracted the actual source boundaries on the public runner. The earlier first run stopped at a regression-test import typo; the test binding was corrected and the full unchanged acceptance suite passed on this subsequent run. Both original mesh SHA-256 values remained unchanged.

| Source | Boundary faces | Derived triangles | Open / non-manifold / winding-conflict edges |
| --- | ---: | ---: | --- |
| Impeller | 126,864 | 253,728 | 0 / 0 / 0 |
| Volute | 39,917 | 42,332 | 0 / 0 / 0 |

The [derived boundary release](https://github.com/simcalc/PyAnsys/releases/tag/frozen-rotor-boundaries-38155649489) has **11 named STL surfaces**, topology JSON, checksums and the upstream license. The boundary ZIP is **6,385,711 bytes**, SHA-256 `c1481ce15abcfb7d2336f366c73dd3565a9ea26dfbcc174d8c2c5b26708457e2`. The [topology report](https://github.com/simcalc/PyAnsys/releases/download/frozen-rotor-boundaries-38155649489/topology-report.json) is **68,245 bytes**, SHA-256 `1dc7cebaeac04506dcfbe9da3ce123f149cc94cb44c6990b9b11c3f4195d2595`. Published ZIP/report bytes were compared through anonymous HTTPS downloads on the runner, then every asset and every STL were independently verified again by the NPSH project.

The polygon fan is a documented surface derivative: **124,758 impeller faces and 2,290 volute faces** have measurable nonplanarity under the recorded relative `1e-12` diagnostic. Native 3D vertices and winding are retained, while the report records plane deviation and float32 coordinate error. Subsequent mesh generation must assess the geometric effect of the chosen triangle diagonal and any remeshing. These topology results still do not establish self-intersection clearance, outward orientation, coordinate-unit basis, new volume cells, Student fit, CFD or pump NPSHr.

### Full pump research stages and actual trial history

The manual **Remesh Frozen Rotor educational pump volume** workflow runs on
this public repository's standard Linux runner. It is independent of Main and
the pump database. Source meshes are checksum-pinned and read-only. A volume
candidate must retain the native source boundary identity, have positive
tetrahedron volumes, fit the declared cell/node/output limits, and pass native
OpenFOAM conversion, wall-type, interface and `checkMesh` checks.

The initial volume trials have **not produced an accepted volume mesh**:

| Actual run | Observed outcome |
| --- | --- |
| [38157229696](https://github.com/simcalc/PyAnsys/actions/runs/38157229696) | Surface classification merged source boundary zones; identity gate rejected the candidate. |
| [38157560445](https://github.com/simcalc/PyAnsys/actions/runs/38157560445) | Native zone ownership was retained, but new surface reparametrization produced overlapping facets; volume generation rejected it. |
| [38158266363](https://github.com/simcalc/PyAnsys/actions/runs/38158266363) | Exact native surface facets were preserved; Delaunay volume fill reached the 300-second limit. Its progress artifact establishes the last native stage, not a completed mesh. |
| [38160027289](https://github.com/simcalc/PyAnsys/actions/runs/38160027289) | HXT entered native refinement and tetrahedron optimisation, then reached 300 seconds. The bounded native log tail and both unchanged source hashes are retained. The logged improving-subset counts are not accepted mesh cell counts. |
| [38161734194](https://github.com/simcalc/PyAnsys/actions/runs/38161734194) | All 162 contracts passed on Ubuntu. Native HXT optimisation reached the 900-second deadline; source hashes remained unchanged. No complete mesh or solver result was produced. |
| [38162936389](https://github.com/simcalc/PyAnsys/actions/runs/38162936389) | Unoptimised native volume fill completed in 6.7 seconds, then its exported tetrahedra failed the positive `minSICN` gate. No candidate was admitted or published as solver-ready. Native internal HXT counts are distinct from actual exported volume-element counts. |
| [38163471462](https://github.com/simcalc/PyAnsys/actions/runs/38163471462) | All 211 contracts passed on Ubuntu. Actual exported impeller inventory: 489,775 tetrahedra and 129,605 nodes. Four signed condition numbers were negative (minimum −7.17e−15), while direct and native determinants remained positive. The measured packet records 5,143 nearly degenerate cells under its scale-relative diagnostic. The candidate remained rejected. |

The default HXT lane has a **900-second** native meshing limit based on that
measured optimisation progress. Its owned-worker supervisor retains a small
diagnostic tail and enforces the deadline; the workflow cgroup separately limits
aggregate memory to 2 GiB and CPU to one core, with swap disabled. Candidate
coordinates, boundary facets and ownership remain fixed. Extending this measured
time budget does not establish that a candidate will pass geometry or CFD checks.

The manual workflow now accepts `optimisation_mode=full` (the unchanged default)
or `unoptimised`. The latter changes only Gmsh's `Mesh.Optimize` flag to zero;
native boundary recovery/refinement, original facets and zone ownership stay
fixed. It is a diagnostic candidate, not an optimisation or quality pass. Every
cell must still have positive volume and quality, retain boundary identity, meet
the separately declared lighter-mesh budgets, and pass OpenFOAM `checkMesh` and
interface checks before MRF. These cell budgets describe this learning trial,
not an OpenFOAM licence limit.

The separately selected `gmsh-interior` trial first uses unoptimised HXT,
measures its actual cells, and verifies the complete fixed boundary. It then
invokes Gmsh's default volume optimiser on that single component. A fresh
measurement and exact boundary check follow before the unchanged quality,
count and conversion gates. The native default optimizer's `niter` argument
does not impose an iteration bound in 4.15.2; the owned 900-second watchdog is
the actual runtime bound. No negative-quality tolerance, geometry welding,
facet replacement or guessed cell count is accepted.

### Separate full HXT trial within the standard FREE runner capacity

The manual `Frozen Rotor full HXT optimisation on standard FREE runner`
workflow uses the explicit `extended_free_hxt` profile: **three meshing threads,
8 GiB address space/aggregate memory, and 2400 seconds**. The owned parent
watchdog, native alarm and workflow cgroup enforce these bounds; the outer
2430-second allowance permits bounded report retention. It requires the exact
public `simcalc/PyAnsys` repository, a standard ephemeral Linux runner and
measured capacity of at least four CPUs and 14 GiB RAM. The standard public
Ubuntu-24.04 runner is documented as four CPUs and 16 GB RAM with FREE usage
in [GitHub's runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#standard-github-hosted-runners-for-public-repositories).
There is no paid, custom or self-hosted fallback. The default 900-second lane
remains separate and unchanged.

This fixed profile admits only HXT10 with full native optimisation enabled.
It retains exact source float64 boundary coordinates, oriented facets and
physical zone/seam ownership; it does not reuse the rejected unoptimised
tetrahedral connectivity or move source geometry. Native thread-option readbacks,
measured machine capacity, address-space limits and requested profile are
recorded with the attempt. Every positive-cell, complete face-incidence,
serialized-boundary, below-one-million-cell, output-byte, native `checkMesh`
and NCC coverage gate remains mandatory. More time and parallelism do not
establish that a valid mesh will result.

This choice is based on actual run
[38161734194](https://github.com/simcalc/PyAnsys/actions/runs/38161734194),
which reached its deadline while full native HXT optimisation was still
progressing. The distinct unoptimised/interior-optimised run
[38167255453](https://github.com/simcalc/PyAnsys/actions/runs/38167255453)
had actual overlapping/topologically invalid tetrahedra and is not admitted.
If a fresh full HXT volume passes, the conditional preliminary SST/MRF path
retains its separate **one-CPU, 2-GiB, 1200-second, 512-MiB-workspace** limits.
An independent baseline reducer must also pass before workflow success.
Physical geometry accuracy, wall resolution, convergence, cavitation/NPSHr and
physical transient remain separate research acceptance stages.

The OpenFOAM adapter prepares a separate Foundation-12 SST/MRF case at the
source's 1450 rpm and 90 kg/s. Water properties, the one-native-unit-to-one-metre
scale, wall adaptation and absolute-pressure datum are explicit **classroom
assumptions**. They are not verified manufacturer pump conditions. A successful
iteration-limited native run is recorded separately from a qualified baseline.

The Python research protocol exposes **Input → Equation → Substitution → Result**
for total-pressure head, suction NPSHA, the adjacent 3% head-drop bracket,
mesh/time refinement and matched-field restart. It requires actual upstream
native evidence, coherent source/configuration bindings, conservation and
convergence checks. Test fixtures and generated dictionaries are not CFD results.
There is currently no qualified full-pump MRF baseline, cavitation NPSH3, or
physical sliding-mesh transient result. These dependent stages remain pending
until their native prerequisites pass.

### Native active-phase execution path

`Frozen Rotor native cavitation or sliding transient` is a separate manual
workflow. It admits only a completed run from this repository's exact volume/MRF
workflow, verifies every native case ZIP member against its manifest, and
rechecks the byte-bound numerical baseline after transfer. Source admission and
an uploaded archive alone never qualify a baseline. The outer source artifact
budget includes separate MSH diagnostics; the native ZIP remains capped at
128 MiB and its unpacked case at 512 MiB.

The phase runner authors and hashes its actual SI input manifest before
`checkMesh` and `foamRun`. Native execution facts are written once; the separate
Python reducer checks signed `rhoPhi`, absolute Pa, liquid-section validity,
phase fraction bounds, first nonlinear residuals, continuity, interface flux and
coverage, and the actual Courant/time history. A final three-revolution window
uses only actual bracketing samples and time-weighted integration. The workflow
requires four to ten total revolutions to support that window; its default
0.3 seconds is not an integer number of revolutions at 1450 rpm.

Start the cavitation study with a numerically qualified high-pressure run using
the active phase-change model. Its head can then be explicitly selected as
that phase family's H0. The single-phase source head is a preparation
prerequisite and is never silently reused as phase-change H0. Pressure cases
must preserve the actual operating/configuration bindings and bracket the
3% head drop before NPSH3 can be derived without extrapolation.

`steady-sweep` uses fixed MRF with physical phase relaxation. `transient-sliding`
uses actual rotating mesh points, which must be observed in retained native
checkpoints. Its periodic observation, matched phase H0, mesh/time refinement
and paired native restart verification remain separate acceptance checks.
Neither prepared dictionaries nor parser tests establish that these native
simulations have run. There is still no accepted full-pump volume mesh from
the trials above, so this dependent workflow has not been dispatched.
### Exact boundary IDs after volume generation

Actual run [38164389147](https://github.com/simcalc/PyAnsys/actions/runs/38164389147)
stopped at the complete boundary identity gate **before** the requested interior
optimiser executed. Its exported impeller volume contained 489,656 tetrahedra;
this is a rejected candidate, not an accepted mesh. Gmsh's documented default
`Mesh.Renumber=1` renumbers nodes and elements after generation. The next trial
sets `Mesh.Renumber=0` to retain original global node IDs. Exact coordinate,
triangle, winding, source-zone and every-cell quality checks remain mandatory.
This setting is an attempted correction, not proof that the next mesh passes.

### Measured interior optimisation and coordinate export

Run [38164614202](https://github.com/simcalc/PyAnsys/actions/runs/38164614202)
preserved every original boundary ID, coordinate, triangle, winding and zone
before and after interior optimisation. The actual impeller count decreased
from 489,452 to 385,728 tetrahedra. Reading post-swap quality then encountered
an obsolete element lookup cache. The correction forces a rebuild before any
post-optimisation quality reads; no quality gate was removed.

Run [38165747834](https://github.com/simcalc/PyAnsys/actions/runs/38165747834)
completed the two-component volume candidate in 113.411 seconds:
395,233 impeller plus 63,599 volute tetrahedra, **458,832 total**. Original
source hashes and exact in-memory boundary identity remained unchanged.
OpenFOAM Foundation 12 was installed, but its pre-conversion input inspection
rejected two negative tetrahedron volumes in the serialized impeller file.
This candidate is **not accepted for CFD** and no MRF solver ran.

The pinned [Gmsh MSH2 writer](https://github.com/live-clones/gmsh/blob/gmsh_4_15_2/src/geo/MVertex.cpp#L132)
stores ASCII coordinates with 16 significant digits. Two extremely thin cells
had positive native determinants before that rounding and negative determinants
in the exported file. The next attempt writes the actual measured float64
coordinates with 17 significant digits, verifies exact numeric round trips,
retains all original IDs/topology/physical tags, and independently checks every
serialized element before admitting the candidate. This is coordinate storage
correction, not point movement, mesh healing or a negative-volume tolerance.
OpenFOAM quality, interface and baseline checks remain separate requirements.

### Fixed-clock physical transient and actual native restart pilot

The separate manual `Frozen Rotor fixed-time and native restart pilot` workflow
requires an actual qualified native MRF artifact from this repository. It authors
three matched sliding cavitation cases at **360, 720 and 1440 steps/revolution**,
with `adjustTimeStep=no`. At 1450 rpm the shared endpoint is 5.5 revolutions;
the checkpoint is 2.75 revolutions and the head comparison window spans the
final three revolutions. These are declared settings, not measured CFD results.

The native runner retains all actual physical time records, finite fields,
sampling logs, rotor motion and immutable input hashes. A restart case copies
complete measured native primary/flux/geometry/time state and original reference
points. It then actually resumes the solver for 2.75 revolutions. Checkpoint
inventory or copying alone cannot verify restart equivalence.

The independent Python reducer compares every native cell value of absolute
pressure, absolute velocity and liquid/vapor fraction, plus every endpoint
point coordinate, in the same immutable native ordering. The pilot explicitly
declares absolute restart tolerances of **1 Pa**, **0.001 m/s**, **0.00001 phase
fraction**, and **1e-10 m under the classroom coordinate assumption**.
It separately derives head time-step GCI from three actual matched runs.
Spatial mesh convergence, NPSH3 convergence, wall resolution and experimental
validation are independent conclusions; this pilot cannot establish them.

The pilot uses the standard FREE public runner with deliberate bounds of one
CPU, 2 GiB aggregate memory, 1200 seconds native execution, 1 GiB campaign
storage and 32 MiB per native branch log. These are trial resource budgets,
not GitHub or OpenFOAM licence requirements. Actual failures and incomplete
evidence remain visible in retained reports. No native Stage4 campaign has yet
been dispatched because the prerequisite full-pump mesh/MRF is not qualified.

### Actual lighter candidate and independent original-volume lane

Run [38167255453](https://github.com/simcalc/PyAnsys/actions/runs/38167255453)
generated **405,636 impeller + 63,598 volute = 469,234 tetrahedra** in
139.87 seconds. The native optimiser left five exact duplicate impeller
connectivities. The native volume-only duplicate operation removed those five
copies after every cell's positive orientation was checked. Every unique cell,
point, original boundary triangle, winding and source zone remained unchanged.
The actual 17-digit files passed independent positive-volume and boundary
identity checks. Nevertheless, `gmshToFoam` created positive unassigned
`defaultFaces`, and the independent native admission rejected the mesh.
**This is not an accepted CFD mesh or a completed MRF simulation.**

An independent exact tetra-face audit measured **12 untagged exterior faces**,
**8 faces with three incident cells**, and **155 internal face pairs with the
same outward winding**. No original tagged triangle was absent from the exterior.
The stricter serialized-face gate now rejects these defects before native
conversion. Positive tetra volumes and complete source tags alone do not prove
a valid volume topology; no face was added, relabelled or healed to mask them.

The separate manual `Frozen Rotor original polygon volume MRF baseline`
workflow converts the two immutable official HDF5 volumes directly into
OpenFOAM polygon topology. It retains every original cell and node, preserves
polygon connectivity, and explicitly transfers Fluent c0 winding into the
OpenFOAM owner/neighbour convention. Every cell's closure, positive volume,
source boundary volume, original IDs, coordinate round trips and file hashes
are checked. Native `checkMesh`, NCC interface coverage and actual numerical
baseline qualification are still mandatory independent gates.

This route uses the original **1,141,763 cells**, so it does not establish the
lighter mesh milestone or compatibility with the Ansys Student mesh limit.
It uses FREE OpenFOAM on the standard public GitHub runner. One metre per
native coordinate unit is an explicit classroom assumption, not a verified
manufacturer dimension. Water, 1450 rpm, 90 kg/s and **300,000 Pa** absolute
inlet reference are declared before execution. Native kinematic pressure is
converted to absolute pressure once using that bound reference.

The independent original-volume trial budget is one CPU, 4 GiB memory,
1200 seconds native execution and a 1 GiB working case. Its downstream
archive retains the existing **128 MiB ZIP** and **512 MiB extracted-data**
bounds; excluded component copies are not sent downstream. Execution facts,
preliminary numerical qualification, physical validation, NPSH3 and actual
restart remain separate claims. Neither this workflow nor the lighter-mesh
trial changes Main, its database, its engineering locks or local ports.

### Measured original-volume quality and bounded cell-union diagnostic

Actual run [38168328884](https://github.com/simcalc/PyAnsys/actions/runs/38168328884)
installed the pinned OpenFOAM package, merged both preserved original volumes
and coupled their interface. Topology, positive volumes, nonorthogonality,
skewness and interface coverage passed. The complete `-allGeometry` check
found **11,310 cells below the unchanged 0.001 cell-determinant threshold**;
the actual minimum was **8.9976547569259324e-6**. The solver did **not** run.
An exit code of zero from `checkMesh` is not accepted when its log states
`Failed 1 mesh checks`.

The new manual `Frozen Rotor native mesh quality diagnostic and geometry pilot`
job measures source-cell determinants and boundary adjacency, derives exactly
32 protected interior two-cell unions, then actually runs merge, interface
coupling and the same complete native quality check with `-writeSets`.
All source coordinates, boundary polygons and patch identities remain exact.
The original-to-derived cell maps and the actual native failed-cell set are
retained for one day. This pilot would contain **1,141,731 cells**, so it is
neither a sub-million-cell mesh nor a completed quality repair.

Source arithmetic is an independent replication of the Foundation-12 metric;
native cell-set equivalence is a separate provenance check. The pilot's
workflow completion means its diagnostic was delivered. Its report separately
states `nativeQualityAccepted`; a false value prohibits baseline admission.
No `foamRun`, NPSH3, physical transient, restart or mesh convergence result is
produced by the diagnostic job. A later quality repair must explicitly declare
changes to wall-layer cell membership and recheck every native quality gate.

Actual pilot [38169761754](https://github.com/simcalc/PyAnsys/actions/runs/38169761754)
derived **1,141,731 cells** and actually executed native merge, NCC and
`checkMesh`. The unchanged determinant criterion still rejected **11,310
cells**. All **32** new cell unions were also identified as concave by the
native check, so this candidate is rejected. No solver was called. The
source diagnostic located all 11,310 low-determinant cells in the impeller;
the volute had none. Of those cells, 7,566 directly contact a source boundary
and the remainder lie within three source-cell adjacency steps of one.

This pilot additionally exposed a diagnostic parser mismatch: Foundation-12
`mergeMeshes` writes the native `cellZones` header as `cellZoneList`. The
reader now admits that exact native role while retaining ASCII, object-name,
complete membership, disjoint-zone, count, hash and immutability checks.
Re-reading the retained actual bytes completes the diagnostic; it does not
change the failed native result or accept the 32 unions. Source-cell arithmetic
and original-to-merged cell equivalence remain distinct evidence checks.

The manual remeshing workflow now also exposes the explicit `delaunay`
algorithm with `unoptimised` mode. HXT10 remains its default. Delaunay1 uses
the pinned Gmsh boundary-recovery implementation; it may introduce boundary
changes, so preservation is measured rather than assumed. Algorithm, mode and
current progress identity are bound through the owned worker and its reports.
Any complete volume must pass exact source-boundary identity, positive cells,
complete tetra face incidence and the unchanged independent native mesh and
baseline checks. Invalid volume topology is rejected before an interior
optimiser is invoked. The existing 900-second, one-CPU, 2-GiB meshing and
sub-million-cell trial budgets remain unchanged.

The quality pilot also offers an explicitly separate `determinant-repair`
policy. It targets the independently measured low-determinant original hexes,
including boundary-adjacent cells, without changing the protected three-ring
prototype. At most 32 disjoint pairs may be selected. Each union must improve
the target determinant, preserve a passing partner, retain every original
point and boundary polygon, and pass positive-volume/manifold and the pinned
native concavity formula. Retained planar split faces are rejected by that
formula. The authored policy, actual source-target JSON and complete source
cell/face maps are immutable, and every remaining source failure is counted.
Zero admissible pairs produces a diagnostic with no derived volume. A produced
prototype proceeds through actual merge, NCC and unchanged full `checkMesh`;
the diagnostic runner never invokes `foamRun` or qualifies a baseline.

Actual targeted run [38171167520](https://github.com/simcalc/PyAnsys/actions/runs/38171167520)
passed **443 contract tests**, recomputed all 11,310 source failures and scanned
**10,000 neighbour candidates** across 1,948 targets in 43 seconds. It selected
**zero** pairs. The conservative union policy rejected 8,884 candidates for
nonplanar retained polygons and 1,116 for insufficient determinant improvement.
The scan reached its declared cap; it does not prove that no eligible pair
exists anywhere in the complete source. No derived mesh or native solver
execution was published, and both source hashes remained unchanged. Failure
of this conservative prototype is not a claim that nonplanar source polygons
are intrinsically invalid in OpenFOAM; the original native check separately
accepted those face geometries and rejected the determinant criterion.

Actual Delaunay run [38170639216](https://github.com/simcalc/PyAnsys/actions/runs/38170639216)
passed 416 contract tests and entered boundary recovery and tetra refinement.
It reached its **900-second** deadline in the impeller without publishing a
complete volume. Its explicit result is `CFD_CLASSROOM_MESH_WORKER_TIMEOUT`,
with zero accepted cells. Source hashes remained unchanged. The trial did not
install a solver case, run OpenFOAM, or qualify baseline performance.

### Independent original-cell correspondence and tagged-surface preflight

The original-volume workflow now independently measures all source determinant
failures, retains the actual native failed-cell set and compares all current
oriented polygons, coordinates and owner/neighbour cell bindings. Only exact
source/native correspondence can pass this diagnostic. It cannot change the
0.001 determinant threshold, admit a failed mesh or produce a CFD result.

The manual `Frozen Rotor tagged surface native preflight` workflow prepares
two whole component surfaces from all **11 pinned STL boundaries** and checks
them with the actual Foundation-12 `surfaceCheck -checkSelfIntersection`.
Every stored triangle, float32 coordinate, winding and original patch tag is
retained in deterministic OBJ files. Exact indexing is declared; it is not
proof of original HDF5 node identity or float64 polygon equivalence. Both
surface files can be regenerated independently from retained immutable STLs.

This surface trial is bounded to one CPU, 4 GiB memory and 300 seconds native
execution. Actual native closure, edge incidence, orientation, triangle
quality and self-intersection diagnostics are required; an exit code of zero
alone is insufficient. Multiple consistently oriented closed boundary shells
are allowed, while fluid-volume connectivity and vertex manifold status remain
unverified. Surface acceptance is separate from volume mesh acceptance,
baseline, cavitation/NPSHr and transient qualification. Any later snappyHexMesh
lane must explicitly report geometry approximation and satisfy all unchanged
native volume, interface and numerical checks before CFD results are admitted.

The first surface run, [38172357434](https://github.com/simcalc/PyAnsys/actions/runs/38172357434),
passed **496 contract tests** and extracted all eleven surfaces, but stopped
before native execution because the union reader incorrectly required a
historical report's raw hash. Extraction time and GitHub run provenance change
on each extraction. The reader now pins the complete technical document with
exactly six declared run/runtime fields excluded from that semantic fingerprint.
Those fields still require valid UTC time, run/attempt/commit provenance and the
reviewed h5py/HDF5 pair. Every current raw report byte is copied and hash-bound,
and all eleven STL byte pins remain unchanged. The extraction scope now also
forwards its actual GitHub run provenance rather than publishing null fields.

Actual corrected run [38172742283](https://github.com/simcalc/PyAnsys/actions/runs/38172742283)
passed **500 contract tests** and both actual native surface checks in **8.15
seconds**. The impeller retained 253,728 triangles and the volute 42,332.
Both logs reported zero illegal triangles, zero non-manifold/open edges and
zero self-intersection locations, with one consistently oriented connected
boundary shell each. This establishes the stated native surface-check scope;
it does not establish a generated volume or physical pump performance.

Actual original run [38172359888](https://github.com/simcalc/PyAnsys/actions/runs/38172359888)
also verified complete source/native correspondence. All **11,310 failed cell
IDs** matched, with every current oriented polygon, exact coordinate class and
original owner/neighbour binding checked. Original native quality still fails,
so that run correctly remains red and never invokes `foamRun`.

### Explicit approximate snappyHexMesh volume pilot

The separate manual `Frozen Rotor approximate snappyHexMesh volume pilot`
workflow starts from freshly bound tagged surfaces and actual surface checks
on the same commit. The current recipe authors one common approximately
isotropic padded background grid for both components (largest extent divided
by 44), source-derived interior seeds verified by three nondegenerate rays,
feature extraction and uniform level-2 volume refinement. The distance target
is 1.01 times the common background diagonal; actual selected cell levels must
independently prove that the native refinement reached level 2. The current
trial enables snapping toward the unchanged source-bound surfaces; boundary
layers remain disabled. This is a distinct approximate mesh, with wall
resolution, geometry accuracy and convergence still unqualified. One source
coordinate unit equals one metre by declared
classroom assumption only. Original volume meshes remain unchanged.

Actual prior castellated-only run
[38177037633](https://github.com/simcalc/PyAnsys/actions/runs/38177037633)
generated **357,337 cells** and passed both component and merged native
`checkMesh` checks. Its source/target interface coverage means were only
0.9360466/0.9376218, with zero minimum coverage and maxima 1.5/2.0. The
unchanged NCC acceptance correctly rejected that interface, so no MRF or
other flow solver ran. The new snapping trial must independently pass the
same quality and NCC criteria; its success is not assumed.

Each final admitted component has a 450,000-cell cap; the measured
combined mesh must contain **fewer than 1,000,000 cells**. The serial native
budget is **900 seconds, one CPU, 4 GiB memory and 1 GiB workspace**. All eleven
original patch names/types, complete rotating/stationary cell zones, positive
geometry, full `checkMesh -allGeometry -allTopology -writeSets`, and unchanged
native interface coverage are required. Pre-merge raw meshes are retained as
immutable snapshots. Native patch area, bounds and enclosed-volume comparisons
are measured independently and labelled as approximation diagnostics.

Before authoring cell zones, native `splitMeshRegions -insidePoint -overwrite`
selects only the region containing the measured source interior seed. The
pre-selection primitive mesh and native point/cell/signed-face maps are retained
and independently replayed. Selected positive cell volume must be at least
99.9% of the generated positive volume; all eleven source physical patches
remain mandatory. Removal of disconnected generated fragments is a declared
derived-mesh operation, not a change to source surfaces or a quality waiver.
Only an optional empty background patch may disappear. The complete recipe has
15 ordered native geometry stages and keeps all full mesh checks unchanged.
The authored snappy refinement controls also target 450,000 cells, but native
intermediate refinement can overshoot that target before outside cells are
removed. Actual intermediate counts are retained; the final-cell admission cap
and enforced process memory/time/workspace budgets remain separate checks.

This pilot invokes geometry utilities only. It does not invoke `foamRun` or
admit geometry accuracy, a steady MRF solution, pressure-sweep NPSHr, physical
transient, wall resolution, mesh/time convergence or actual restart. A native
mesh success and those subsequent engineering acceptance stages remain
separate results.

The earlier snapped trial
[38173480933](https://github.com/simcalc/PyAnsys/actions/runs/38173480933)
completed an impeller volume with **60,637 cells**. The full independent native
check rejected **16 isolated one-cell regions with zero determinant** and
**1,959 concave cells**. All native logs and raw failed-cell sets were retained;
no flow solver ran. The subsequent common-grid castellated-only recipe was a distinct
bounded trial, not a reclassification of that failed mesh. Smooth-wall geometry
accuracy, passage fidelity and physical pump performance remain unqualified
even if its subsequent native quality/interface checks pass.

The next common-grid, unsnapped run
[38174426218](https://github.com/simcalc/PyAnsys/actions/runs/38174426218)
generated **76,083 impeller cells**, but the native check still rejected
**8 isolated zero-determinant cells** and **3,062 concave-cell checks**.
The pinned native implementation checks concave or planar cell faces, so that
count does not by itself identify every geometric cause. Its raw mesh, actual
failed-cell sets and native logs are retained. No volute, coupled mesh or flow
solver acceptance follows from that failed impeller. Uniform volume refinement
and source-seed region selection are a new bounded trial; success is unproven.

Run [38175984353](https://github.com/simcalc/PyAnsys/actions/runs/38175984353)
passed **585 contract tests**, then generated **121,226 impeller cells** and
completed native source-seed selection of **121,218 cells** from nine regions.
Its temporary intermediate refinement peaked at **630,342 cells** before
outside-cell removal. The Python reader rejected native `cellLevel` written
as `121226{2}`; full `checkMesh`, the volute and interface were not executed.
Supporting this exact documented uniform `labelList` syntax fixes a reader
compatibility defect and does not qualify the mesh or waive native quality.
The exact format is defined by the pinned Foundation-12
[list writer](https://github.com/OpenFOAM/OpenFOAM-12/blob/86e126a7bc4dbfaed949bbc58c9f0fdd67acc059/src/OpenFOAM/containers/Lists/UList/UListIO.C)
and [list reader](https://github.com/OpenFOAM/OpenFOAM-12/blob/86e126a7bc4dbfaed949bbc58c9f0fdd67acc059/src/OpenFOAM/containers/Lists/List/ListIO.C).

### Independent exploratory MRF lane for an admitted approximate mesh

The manual `Frozen Rotor approximate geometry exploratory MRF baseline`
workflow freshly repeats source extraction, surface and full geometry checks
on one commit. Only an actually accepted volume, connected component meshes,
all eleven nonempty source patch roles and the unchanged NCC coverage gates
can proceed. Immutable component snapshots from before merge are copied into
a fresh physics case. Merge, NCC field creation, full native mesh checks and
500 steady SST/MRF iterations are separate native steps.

The predeclared classroom conditions are water at 293.15 K, 1450 rpm about
the positive z axis, 90 kg/s and **300,000 Pa absolute** reference pressure.
Gravity is disabled in the solver; head conversion uses 9.81 m/s². Kinematic
pressure is converted to absolute pressure exactly once. Inputs, original
surfaces, generated meshes, runtime binaries, rewritten initial fields,
native logs, endpoint fields and sampling files are bound by byte hashes.
Equation stages are reduced by Python from actual numerical evidence.

This physics trial is bounded to **one CPU, 4 GiB, 1200 seconds and 1 GiB**.
The outer 1260-second process allowance includes reduction and report retention.
The 75-minute workflow envelope covers all bounded prerequisite stages and
setup. No paid, custom or self-hosted runner is allowed.

Native execution and numerical acceptance are separate immutable reports.
Workflow success requires both actual CFD evidence and the unchanged
residual, conservation, stationary head/flow, noncavitating pressure and
interface criteria. `numericalBaselineQualified` applies only to this exact
declared approximate geometry. `qualifiedBaseline`, geometry accuracy,
wall resolution, mesh/time convergence, cavitation/NPSH3, physical transient
and restart stay false until their respective actual research gates pass.
No Main code, Pump database, engineering lock, local port or deployed Main
application is modified by this public workflow.

Run [38177037633](https://github.com/simcalc/PyAnsys/actions/runs/38177037633)
completed both component mesh checks and reached the final merged mesh check.
The unchanged native NCC coverage criterion rejected the impeller–volute
connection. The workflow retained the generated meshes and native logs and
skipped MRF execution. No steady solution, pressure sweep, NPSHr or physical
transient follows from that trial.

Downloaded mesh and baseline evidence retains its original native command
paths. Read-only verification reconstructs the complete historical command
tuple with strictly bounded pure POSIX/Windows paths; it reads and hashes all
files from the actual current artifact folder. It never accesses or executes
the historical directory. A fresh native run still requires every execution
argument to target its current independent output folder. Relocation does not
change source bytes, manifests, numerical criteria or engineering authority.

### Conditional high-pressure active cavitation reference preparation

`tools/frozen_rotor_snappy_phase_case.py` prepares one new **300000 Pa absolute**
Schnerr–Sauer reference on the same admitted approximate mesh and fixed MRF
basis. It first re-runs the current native baseline reducer and verifies the
complete source inventory, control hashes, coupled mesh and numerical gates.
It copies the exact six mesh files and all bound source evidence into a fresh
independent case. Whole-package relocation preserves the historical command
tuple and every source checksum.

The initial state is liquid fraction one, zero velocity and absolute pressure
300000 Pa; accepted single-phase result fields are not copied as a restart.
The declared four-to-ten-revolution trial duration does not prove stationarity,
residence-time adequacy or time-step convergence. This preparer launches no
solver. Actual active-phase noncavitating H0, conservation and stationarity
must be checked before any pressure sweep uses the three-percent head-drop
criterion. The single-phase baseline head is not active-phase H0. NPSHr,
sliding motion, physical transient, restart and geometric accuracy remain
unqualified. A failed native mesh or NCC interface cannot be admitted by this helper.

The manual `Frozen Rotor approximate active cavitation high-pressure reference`
workflow accepts only the run ID of a successful approximate MRF workflow in
this same public repository. It verifies the source commit, artifact identity,
expiry, byte count and complete GitHub artifact SHA-256 before safely extracting
only the bound baseline package. Source transfer is not numerical acceptance;
the preparation helper independently re-reduces the actual baseline evidence.

The new native runner checks the pinned Foundation-12 runtime, unchanged mesh,
source-bound controls and initial fields. Two full native mesh checks precede
the active Schnerr–Sauer solve, with one CPU, 4 GiB memory, 1200 seconds and
1 GiB workspace limits. A separate reducer checks absolute pressure, field
units, complete time sampling, residuals, continuity, signed mass conservation,
NCC coverage, stationary head/flow and negligible vapour. Only this actual
active-phase result may provide its phase-family reference head. Native command
completion is retained independently from numerical acceptance; workflow success
requires the numerical reducer to pass. These tools have contract tests, but
that does not establish an actual phase solution, pressure sweep, NPSHr,
sliding-mesh transient, convergence or restart.

The uniform source-snapped trial
[38178994824](https://github.com/simcalc/PyAnsys/actions/runs/38178994824)
retained **121,218 impeller cells**, but the full native check rejected
**47 concave cells**. Positive volume, determinant, non-orthogonality and skew
checks passed independently. The volute, coupled interface and MRF solve were
not executed. Native logs, primitive mesh, selection maps and failed-cell sets
are retained for geometric diagnosis. The conditional active-phase workflow
has not been executed and cannot use this failed trial as its source.

### Reuse of an independently accepted native HXT volume

The manual `Frozen Rotor fresh MRF from independently reverified HXT volume`
workflow is a conditional route for the extended HXT trial. It requires the
exact pinned public source commit, completed run, successful native volume
step and one unexpired artifact. A downstream failure does not revoke an
independently valid volume, and does not qualify any baseline or phase result.

The complete raw artifact must match its actual GitHub size and SHA-256 before
safe extraction of the two MSH files and their original remeshing report.
Current readers replay tetrahedral connectivity and the exact pinned source
coordinates, oriented boundary triangles and eleven physical patch roles.
Source and transferred bytes remain unchanged. Transfer is bounded to one
CPU, 4 GiB and 300 seconds; it launches no mesh generator or flow solver.

Only an accepted replay can start fresh conversion, full native mesh and NCC
checks, and steady SST/MRF. This new solve retains the separate one-CPU,
2-GiB, 1200-second and 512-MiB limits. Its declared inlet reference is
**101325 Pa absolute**, with the original water, speed, flow and gravity
assumptions. No old solution fields or head values are inherited.
Workflow success requires the independent numerical baseline reducer to pass.
Phase admission still requires this new successful baseline run and its own
bound evidence, followed by an actual qualified active-phase reference.
Preparing or testing this route does not establish accepted mesh, CFD, NPSHr,
transient, restart or convergence results. Main files and ports are unchanged.

The completed extended HXT run
[38181682156](https://github.com/simcalc/PyAnsys/actions/runs/38181682156)
generated **426,150 tetrahedra**. Its independent replay and fresh native
conversion in [38183965690](https://github.com/simcalc/PyAnsys/actions/runs/38183965690)
preserved source coordinates with zero deviation and all eleven boundary roles.
The actual native mesh failed eight checks, including one negative-volume cell
and 94,696 cells below the determinant criterion. NCC minimum coverage was
0.682/0.685, below the unchanged 0.99 threshold. No flow solver ran. A successful
volume generation or converter exit does not establish accepted CFD geometry.

### Separate authored classroom pump CAD

The manual `Authored classroom pump CAD and native geometry verification`
workflow creates a **new teaching geometry**, declared in SI metres. It does
not modify, repair or relabel the original Ansys source meshes. Its provenance
is `CLASSROOM_AUTHORED_GEOMETRY`; it is not manufacturer-published geometry.
Dimensions and construction assumptions belong to this distinct source.

One CAD model and volume mesh supply the rotor and stator regions. Both
interface exports must use the exact same coordinate facets with opposite
outward owner winding. Admission independently checks every tetrahedral face,
actual patch roles, SI parameters, source code and artifact hashes. Geometry
preparation does not inherit a previous mesh or physical result.

The first trial is geometry-only: both component conversions, wall types,
full native quality checks, merge and NCC coverage. It contains no flow fields
and never invokes `foamRun`. Failed native sets, meshes and logs are retained.
The existing quality and coverage criteria remain in force.

CAD generation is limited to one CPU, 4 GiB, 900 seconds and a 512 MiB workspace;
native geometry checks use one CPU, 2 GiB and 600 seconds. Combined generated
cells are capped at 500,000. The public standard GitHub runner has no paid or
self-hosted fallback. Artifacts expire after one day.

Only actual accepted native geometry can proceed to a separate steady MRF
study. Cavitation, NPSHr, transient, restart, mesh/time convergence and physical
validation require their own actual runs and evidence. This authored teaching
lane cannot validate the original pump geometry or manufacturer performance.
Main source, engineering locks, database, deployment and local ports are unchanged.

The first actual authored CAD trial
[38185856839](https://github.com/simcalc/PyAnsys/actions/runs/38185856839)
generated **200,028 tetrahedra**. The impeller's volume, concavity, skewness
and non-orthogonality checks passed, but **919 cells** failed the unchanged
determinant check. The native `underdeterminedCells` and
`twoInternalFacesCells` sets contain exactly the same 919 cells. Stator, NCC
and flow execution were correctly withheld by this component quality gate.

The next declared mesh operation inserts an interior centroid only in an
authored tetrahedron having two or more exterior boundary faces. Its four
positive child tetrahedra conserve the parent volume and preserve every
original point, exterior triangle and physical role. Both interface exports
therefore remain exact. Published topology is independently reconstructed
and replayed, with the postprocessor source bytes bound in the manifest.
This operation is not a native-quality acceptance: full component and merged
mesh checks and the original NCC coverage criteria still have to pass.

The actual refined trial
[38186908484](https://github.com/simcalc/PyAnsys/actions/runs/38186908484)
passed all **nine native geometry stages** and **741 contract tests**.
Its 205,611 cells comprise 103,818 impeller and 101,793 volute cells.
All three full `checkMesh` checks passed. The minimum native determinant
was 0.0077888323, above the unchanged 0.001 criterion; source/target minimum
NCC coverage was 0.999999999999993/0.999999999999995. Both complete artifact
archives, retained file hashes, converters, physical walls and merged mesh
were independently replayed. This accepts this authored classroom mesh only;
no flow solver ran in that geometry-only workflow.

### Fresh authored steady baseline

The manual `Authored classroom pump steady MRF baseline` workflow constructs
fresh CAD and repeats all geometry gates in the same public FREE run before
authoring a new single-phase SST/MRF study. It never imports old solution
fields or head. Its explicit teaching inputs are water at 293.15 K,
998.2 kg/m3 and 0.001002 Pa s, 1450 rpm, 90 kg/s, and 300000 Pa absolute
external inlet total pressure. The intake patches are explicitly stationary.
Native gravity is zero; head is pressure/kinetic rise with a declared zero
elevation correction, not a measured manufacturer performance result.

The baseline uses one CPU, 4 GiB, 1200 seconds, 1 GiB workspace and 500 steady
iterations. Actual mesh, source, runtime, logs, full final fields, signed flow
samplers and initial NCC diagnostic VTK files remain byte-bound. A separate
Python reducer requires finite fields, unchanged interface coverage, residual,
conservation, stationary head/flow and noncavitating pressure criteria.
Native completion and numerical acceptance are separate. Numerical acceptance
on this mesh cannot qualify wall resolution, mesh/time convergence, physical
validation, active-phase H0, cavitation/NPSHr, moving-rotor transient or restart.

The first fresh baseline run
[38187978639](https://github.com/simcalc/PyAnsys/actions/runs/38187978639)
passed all geometry gates and 784 tests, then stopped before `foamRun` because
its diagnostic inventory omitted native `checkMesh/constant/nearPoints.vtk`.
The retained native point set contains exactly 3278 labels: the two disjoint
1639-node sides of the matched interface. The corrected reader binds the log,
source count, actual interface face labels, point set and VTK point IDs as
separate diagnostic evidence. Unknown VTK roles and inconsistent counts remain
rejected. No mesh-quality or numerical acceptance threshold changes.

The next actual baseline run
[38189024819](https://github.com/simcalc/PyAnsys/actions/runs/38189024819)
passed the fresh geometry gates and 788 tests, then entered the native SST/MRF
solver. Before its first iteration, OpenFOAM stopped because the authored
`fvSchemes` omitted the required `wallDist` dictionary. The corrected authored
recipe supplies the Foundation 12 `meshWave` wall-distance method for both
components. It also removes the unused `nonRotatingPatches` MRF keyword;
the existing intake `noSlip` and inlet `pressureInletOutletVelocity` conditions
determine their stationary velocity basis. A new actual native run is required;
this configuration correction does not establish convergence or pump head.

The subsequent actual run
[38190272870](https://github.com/simcalc/PyAnsys/actions/runs/38190272870)
passed 789 tests and reached native iteration 500 with a normal `End`. Its
endpoint inventory exposed two native writer roles: dimensionless `yPlus`
uses `dimensions []`, and MRF writes the interpolated face velocity `Uf`.
The teaching reader checks those exact roles, SI dimensions, all actual cell
or internal-face values and unchanged raw file hashes. The legacy source
reader is unchanged. This compatibility correction does not qualify the
solution: its pressure, velocity, residual and continuity evidence shows
nonphysical, unconverged flow.

The next authored numerical trial uses bounded first-order upwind transport,
lower pressure/momentum/turbulence relaxation and a tighter linear pressure
solve. Geometry, operating inputs, physical boundary conditions, the 500
iterations and numerical acceptance thresholds are unchanged. This is a
declared stabilization trial; it does not establish spatial accuracy or allow
negative absolute pressure to become an accepted single-phase baseline.

The actual stabilization run
[38193184426](https://github.com/simcalc/PyAnsys/actions/runs/38193184426)
passed 907 contract tests and completed the native iteration-500 field checks.
Its separate reducer could not publish the acceptance report because its
output directory belonged to the bounded worker and the reducer ran as the
unprivileged job user. This workflow failure does not by itself identify a
numerical failure or a numerical pass. The reducers now use the same output
owner within the explicit worker directory, with a separate 360-second
deadline. Source data and numerical criteria remain unchanged. The retained
whole archives and native data require independent numerical replay.

The subsequent whole-archive replay of that run found an actual numerical
failure: its final-window mean head was about -102679 m, its global absolute
pressure minimum about -6.177e9 Pa, and residuals and head stationarity exceeded
the unchanged acceptance limits. Correct integrated mass flow and finite fields
did not qualify that baseline. Run 38194626972 also retained a numerical
rejection after the output-owner correction. Neither run supplies an active
cavitation H0 or permits a dependent pressure point or physical rotor pilot.

### Uniform outlet candidate after the rejected baseline

The retained native fields localize the largest velocity and negative pressure
to stator cells beside the discharge. All rotating-wall vectors match the
declared `omega cross r`; the integrated outlet flow alone did not detect the
concentrated velocity profile. The pinned Foundation 12
[`flowRateOutletVelocity` implementation](https://github.com/OpenFOAM/OpenFOAM-12/blob/86e126a7bc4dbfaed949bbc58c9f0fdd67acc059/src/finiteVolume/fields/fvPatchFields/derived/flowRateOutletVelocity/flowRateOutletVelocityFvPatchVectorField.C)
retains the extrapolated tangential velocity and rescales the clamped normal
profile. This diagnosis does not establish that a replacement will converge.

One new fresh candidate prescribes a uniform normal plug outlet. Python shows
the complete input, equation, substitution and SI result: `Q = 90/998.2 m3/s`,
`A = 0.04363*(0.12-0.01) = 0.0047993 m2`, and
`U = (0, Q/A, 0) = (0, 18.786550564837889, 0) m/s`. Before authoring the case,
the independent helper verifies every actual native discharge facet, its
outward +Y normal, the authored rectangle, total area, perimeter and current
geometry/mesh hashes. The native velocity condition changes to `fixedValue`;
the outlet pressure remains `zeroGradient`. This is an explicit classroom
boundary assumption, not a measured or developed outlet profile.

Geometry, RPM, water, absolute suction pressure, numerical controls, iteration
count and acceptance thresholds remain unchanged. No native field is clipped,
no calculated pressure is offset, and no sign or acceptance limit is changed.
The phase, pressure-point and moving-rotor adapters retain the same outlet
profile and its full geometry proof; pressure points still change only inlet
pressure. Their dependent execution requires a new accepted native baseline
and then an accepted active-model high-pressure reference.

The baseline reducer also retains independent global-pressure, interface
coverage and interface-flux diagnostics before evaluating positive pump head.
A failed head therefore does not hide valid independent measurements or a
negative-pressure failure. Current raw file identities remain mandatory.
Contract tests and this candidate definition do not qualify convergence,
noncavitating H0, NPSHr, transient performance or physical accuracy. The actual
fresh native run decides those prerequisite numerical results.

The actual uniform-outlet trial
[38197674270](https://github.com/simcalc/PyAnsys/actions/runs/38197674270)
passed 966 contract tests and repeated the native geometry/interface gates.
The native solver reached iteration 500 and `End`, with 999 seconds reported
wall time. The Python evidence checks then reached the outer 1260-second
deadline (exit code 124); execution and acceptance JSON files were not written.
All three complete raw data archives were retained and independently checked.
The raw final 481--500 iterations still have pressure initial residual up to
0.0111081, head span 108.63%, and minimum static absolute pressure about
-2.574 MPa. All 388 outlet face vectors equal the prescribed normal profile,
but an adjacent stator cell still reaches about 332.15 m/s. These are rejected
numerical facts, not qualified pump performance. Fixing evidence-check cost
alone does not repair these physical/numerical prerequisites or qualify H0.

The evidence correction uses one complete source replay per original/copied
path within one explicit transaction. Every reuse rehashes the entire file and
directory inventory and verifies the installed external native binaries; no
proof is shared across paths or retained after the transaction. The runner
checks its deadline around heavy evidence work and interrupts that work before
the outer watchdog, retaining a truthful rejection report. The 1200-second,
one-CPU, 4-GiB, 1-GiB limits and numerical acceptance thresholds are unchanged.
The combined source/runner/phase/import/sliding/reduction regression has 120
passing tests. Those contract results do not qualify the rejected CFD fields.

The pressure-gradient candidate was
`teaching-upwind-pressure-gradient-stabilization-v2`. It adds only
`grad(p) cellLimited Gauss linear 1` to the previous scheme; `grad(U)` was
already limited. Every physical boundary, mesh, operating quantity, other
solver control, 500-iteration limit and acceptance criterion is retained.
The native limiter scales gradient reconstruction using neighbour bounds;
stored pressure/velocity values are not clipped or replaced. The relevant
near-outlet cells have positive native tetrahedral volumes and no detected
local geometry-quality defect, while their unrestricted pressure-gradient
reconstructions overshoot neighbouring values. This supports a controlled
candidate trial, not a proven cause or promised convergence. The preparation
and runner regression passes 48 tests before any new native result is known.

Its actual fresh run
[38202150699](https://github.com/simcalc/PyAnsys/actions/runs/38202150699),
source commit `644a17f046a9cb0445688b52f6248f75d867dfc3`, passed 982 contract
tests and completed the native 500-iteration calculation. The execution and
acceptance reports were retained within the evidence deadline. The numerical
reducer correctly rejected the fields: final-window head is 19.9591 m with
2.8551% span (criterion 0.5%), pressure initial residual reaches 0.0187751
(criterion 0.0001), and minimum static absolute pressure is -13.2664 MPa
(liquid criterion greater than 2339 Pa). Independent mass balance, local
continuity, NCC flux and coverage criteria pass. These independent results
do not qualify the head, baseline or an active-phase H0. All 168 baseline
archive members were checked, and all 205611 velocity/pressure cell values
were independently inspected. The worst pressure is now adjacent to a rotor
blade; the largest cell velocity remains adjacent to the prescribed outlet.
Extending the same divergent local-pressure history is not an accepted fix.

The next fresh controlled candidate is
`teaching-upwind-consistent-coupling-v3`. Its only solver delta from the
pressure-gradient trial is `SIMPLE {consistent yes;}`. The pinned native
pressure corrector uses its consistent reciprocal diagonal in both the
pressure equation and the matched velocity/face-flux corrections. This tests
pressure-velocity coupling sensitivity; an unconverged cell velocity and a
conservative face flux do not prove a software bug or predict this trial's
convergence. The existing gradients, diffusion/convection schemes, mesh,
physical boundary conditions, 500 iterations, resource limits and all
acceptance thresholds remain unchanged. No rejected solution is reused.

### Separate native torque diagnostic

The manual `Fixed unqualified classroom pump torque diagnostic` workflow
examines only the immutable stored iteration 500 of the failed run
`38197674270`, source commit
`834fc53cd1ad582496608d3549a3f18a0461f72f`. Its complete source archive is
69191600 bytes with SHA256
`c633fa042dc9091e5f8dccf12d68456b6c34bfb986681fac5778a296f5d0d25e`.
Exact public-run metadata, archive size, CRC, member roles and every current
extracted byte are checked before a separate minimal case is created.

The package-owned Foundation 12 `foamPostProcess` utility constructs the
single-phase SST solver at that stored state. It runs no new flow iterations.
Four native force tables independently integrate the blade, rotating hub,
rotating shroud and their combined rotor about the declared origin. Their
component sums must agree. Pressure and viscous moments retain their signs;
the Python equation stages show `P_rotor_to_fluid = -omega_z * M_fluid_on_rotor,z`
and the separate sampled hydraulic power `P_h = Q * Delta(p_total)`.

The diagnostic is bounded to one CPU, four GiB, 300 seconds and one GiB
workspace. Utility and selected solver/force library identities, native
warnings, actual finite tables and unchanged original/copied inputs are
checked. A successful utility exit is insufficient. This diagnostic does not
qualify the failed baseline, an efficiency, an energy balance, H0, NPSH3,
mesh/time convergence, physical transient or restart.

The actual native diagnostic
[38203360565](https://github.com/simcalc/PyAnsys/actions/runs/38203360565)
completed without native warnings and passed 23 contract tests. Its 46-member
archive was fully checked. On the fixed older run `38197674270`, the signed
fluid-on-rotor axial moment is -110.4614 N m and the corresponding signed
rotor-to-fluid power is 16772.8569 W. The sampled hydraulic power from that
same rejected state is 78.7267 W. These are separate diagnostic measurements,
not an efficiency or a qualified energy balance; they are not measurements
of the later pressure-gradient candidate.

### Conditional active cavitation reference

The manual `Authored classroom pump active cavitation reference` workflow
repeats fresh authored CAD, native mesh/interface checks and the numerically
accepted single-phase SST/MRF prerequisite in the same run. Only that accepted
case supplies its immutable mesh and provenance to a new liquid-initialized
Schnerr-Sauer calculation. It copies no solution fields or single-phase H0.

Native `checkMesh` writes `0/cellToRegion` for the two topologically separate
regions joined by NCC. This file is a generated label diagnostic, not an
authored initial flow field. The phase and rotor readers check every label,
owner/neighbour connectivity, exactly two complete fluid cell zones, the
generating log and current raw hashes. The accepted-reference archive reader
replays that same proof before extraction. Extra initial fields and unbound
diagnostics remain prohibited; reading this diagnostic supplies no flow or
inherited numerical acceptance.

This separate reference uses 300000 Pa absolute inlet total pressure, the same
water and flow/RPM assumptions, and fixed MRF. Native `p_rgh` and `p` are in Pa;
declared zero gravity gives `p = p_rgh` without atmospheric addition. The
maximum time step is one rotation period divided by 360; four periods are a
declared relaxation trial, not proof of stationarity or physical blade motion.
Actual signed-flow, pressure, kinetic moment, global vapour/pressure extrema,
continuity, Courant and residual histories must independently qualify H0.

Each native phase trial is limited to one CPU, 4 GiB, 1200 seconds and one GiB
of retained workspace. Startup scalar results and NCC addressing are validated
as generated data, separately from immutable authored model inputs. Complete
raw logs, fields, samplers, input/runtime identities and numerical diagnostics
are retained even on failure. Neither preparation, successful contract tests,
nor native completion alone establishes phase-reference acceptance. Pressure
sweep, NPSHr, moving-rotor transient, refinement and restart remain separate
research stages requiring their own actual evidence.

### Conditional pressure points and NPSH3 reduction

The manual `Authored classroom pump pressure only cavitation point` workflow accepts
an explicit successful active-reference run ID, its exact source commit and
one absolute inlet total pressure. It imports the whole GitHub archive only
after metadata, size, SHA256, CRC and every member role are checked. The
installed Foundation 12 binaries then independently replay the current H0
acceptance before a fresh pressure case is authored. Only inlet pressure may
change; the mesh, flow, RPM, fluid, turbulence, phase model and numerical
controls retain their declared bindings. Source solution fields are not reused.

Raw native completion and numerical pressure-point acceptance remain separate.
The offline sweep reducer uses actual qualified liquid-suction points and the
actual active high-pressure reference. It reports a conditional NPSH3 only when
those measured heads bracket `H/H0 = 0.97`; interpolation stays inside that
bracket. A suction section at or below vapour pressure is excluded from this
dependent comparison while its independent numerical facts remain available.
A broad bracket does not establish pressure-resolution independence, physical
validation, mesh/time convergence or restart. These workflows and contract tests
do not themselves prove that any pressure sweep has been executed.

### Conditional physical rotor pilot

The manual `Authored classroom pump physical rotor pilot` workflow accepts
the exact completed active-model reference run and commit. Its whole archive
transfer and current installed-binary H0 replay are the same prerequisites
used by pressure points. The current source reference is copied in full as
immutable evidence; its fields and head are never a sliding result or initial
solution. A measured five-write output projection must fit the one-GiB bound
before a new case is created.

The new case removes MRF and prescribes global-time rigid rotation at 1450 rpm
about the declared +z axis and origin. Rotating physical walls use
`movingWallVelocity`; stationary physical walls retain `noSlip`. The physical
pilot starts from explicit uniform liquid/velocity/pressure/turbulence inputs.
Its maximum time step is one period divided by 360 and its end time is 1.25
periods. All five positive quarter-period states are retained, including the
noninteger final rotor angle. These durations are inputs, not a periodicity or
time-step-convergence test.

The physical pilot explicitly authors `timeFormat general`, `timePrecision 17`
and `writePrecision 17`. Fractional quarter-turn folder names then retain the
precision needed by the strict stored-clock reader; a 12-digit folder name
can differ from the higher-precision stored time value. Synthetic round-trip
tests demonstrate this formatting issue and its guarded input correction.
They do not prove that an actual checkpoint or matched restart was executed.

Only the exact package-owned `checkMesh` and `foamRun` commands execute, with
one CPU, four GiB, 1200 seconds, one GiB workspace and eight-MiB native logs.
The current motion library has its own actual byte/ownership descriptor; this
does not claim that every loaded library has been audited. Actual moved
coordinates are compared label by label with `Rz(omega*t)*x0`, while stator
coordinates must remain fixed. Dynamic NCC coverage is bound to every actual
native `Time` block, the single current owner pair and its physical face counts.
The original 0.99/0.999/1.01 coverage criteria remain in force.

A separate pure audit checks finite SI endpoint fields and complete stored
checkpoint roles: scientific fields, flux state, moved points, actual
`<time>/fvMesh/polyFaces` and the native clock. Quarter-period writes
do not provide the preceding native-step geometry for an ALE conservation or
moving-wall flux proof. Missing data stays explicitly pending and does not
erase valid independent coordinate evidence. A complete stored checkpoint is
not an executed matched restart. This pilot and its contract tests do not
qualify transient performance, sliding H0, NPSH3, wall resolution, mesh/time
convergence, physical validation or restart.

The separate `teaching_pump_restart_clock.py` reader prepares the seeded clock
part of a future restart comparison. It reads all six actual `uniform/time`
keys, the complete current checkpoint inventory, immutable controls, the
pinned native Build and selected solver, the resumed log and the endpoint
clock. Native time and index continue from their stored values. Fixed time
steps may omit `deltaT` log lines; adaptive steps must retain their actual
printed increments. The reader follows Foundation 12's distinct fixed and
adaptive restoration rules and preserves absolute global time.

Its 18 focused tests and independent source review verify these reader
contracts. Header/inventory completeness and clock consistency do not prove
field-state equivalence, physical motion, ALE conservation or a matched
restart. This helper starts no native process and cannot accept those research
claims. An actual paired continuous/resumed solve remains required.

### Smaller mesh candidates for bounded refinement research

The geometry-only manual workflow additionally accepts `lean-coarse` (0.0095 m),
`lean-medium` (0.0075 m) and `lean-fine` (0.0055 m). They retain the exact
authored CAD and original native quality, boundary, interface and resource
criteria. The old `coarse`, `medium`, `fine` choices and default remain unchanged;
six prior canonical plan hashes are checked without recertifying historical
native artifacts under a new generator source identity.

The declared 50000–250000 cell range is a sizing target, not a measured count
or an acceptance criterion. New actual cell counts and surface tessellations
must be inspected after native generation. These choices supply no baseline,
NPSH3, fixed discrete boundary, mesh convergence or transient qualification.
The existing 500000-cell hard limit remains in force. The baseline and phase
workflows do not automatically select these geometry-only candidates.

### Seeded frame inspection before matched restart research

The separate `teaching_pump_seeded_frames.py` reader consumes the complete
checkpoint clock and a fully bound resumed timeline. Selected frames must be
genuinely consecutive native indices, with actual increments and previous
increments. Every retained frame, all six constant mesh primitives and the
motion dictionary have current SHA256 and exact role/header checks. Rotor
coordinates are compared with `Rz(omega*t)*x0` using absolute global time and
unchanged constant reference points; stator coordinates stay fixed.

Its 17 synthetic contract tests check skipped/rebased frames, malformed
numeric identities and primitive headers, inventory/clock mutations and
bounds. The reader starts no process. Coordinate-equation facts and a complete
stored role inventory do not qualify native provenance, installed runtime,
field-state semantics, ALE conservation, wall flux, a matched restart or CFD
performance. Those flags remain false until separately measured and admitted.

### Lean centroid transition balancing before native acceptance

Only the three explicit lean candidates enable bounded original tetrahedron
adjacency balancing. A centroid split reduces the normal distance from an
original face to its face-child centre by four. Where that transition would
give `min(dOwn,dNei)/(dOwn+dNei) < 0.05`, the deterministic closure also splits
the larger-distance original neighbour, once. It then checks every derived
internal face. Original coordinates, physical surface triangles, paired
interface facets, orientation and fluid volume remain bound and unchanged.

Pure replay of the retained failed lean-coarse source selected two additional
rotor parents and six extra tetrahedra. This is a geometry calculation, not an
executed replacement mesh or native quality approval. The old three mesh
recipes and default refinement proof bytes stay unchanged. New lean packages
must retain their own generator identity and pass the complete native
`checkMesh`, physical boundary and NCC coverage gates; the 0.05 native weight
criterion and all execution limits remain unchanged.

### Separate inlet-flow/static-outlet MRF research candidate

The manual `teaching-pump-free-inlet-flow.yml` workflow uses fresh authored
lean geometry and its complete native acceptance before starting a separate
physics case. In this distinct boundary family, `flowRateInletVelocity`
prescribes `Q = 90/998.2 m3/s` on the actual inlet patch area and normals.
The outlet uses pressure-coupled velocity and static kinematic pressure zero.
The declared outlet absolute reference is 300000 Pa, so the reducer reconstructs
`p_abs = 998.2*p_kinematic + 300000` exactly once. Actual sampled inlet static
pressure and flux-weighted `mean(|U_absolute|²)` supply suction NPSHA; the
outlet reference is not treated as a prescribed suction pressure.

Recipe, preparation, runtime, execution, study, manifest and acceptance have
their own boundary-family identities and filenames. No rejected historical
physics field, head, H0 or old inlet-total-pressure admission is copied.
The original v3 SST, MRF, walls, schemes, 500 iteration window, residual,
head stationarity, conservation and noncavitating pressure gates are retained.
Execution remains limited to one CPU, four GiB, 1200 seconds and one GiB of
workspace. Native generated region/cache/function data are independently
bound; final closure rechecks the full inventories and installed binaries.

The 60 focused synthetic contract tests passed, including changed source,
runtime, pressure mapping and late-added endpoint evidence. These tests and
the recipe do not establish convergence. Only a new actual native run and
its independent reducer can qualify a numerical baseline on that exact mesh.
Numerical head/NPSHA stages preserve Input → Equation → Substitution → Result.
NPSHr/NPSH3, wall resolution, mesh/time convergence, active-phase H0, physical
transient, matched restart and manufacturer pump validation remain separate.

The first inlet-flow job, run `38209617567`, stopped after successful native
NCC creation and before CFD because the initial-field reader incorrectly
required an explicit header version. Foundation 12 writes the current stream
version implicitly. The reader now accepts exactly the four native ASCII
role keys, or those same keys with explicit version `2.0`; duplicate, extra,
wrong-role and wrong-version keys remain rejected. The ten retained native
initial fields replay with unchanged byte/SHA records. This correction does
not establish solver convergence or change any numerical acceptance limit.

The next native job, run `38210983836`, reached `End` at iteration 500. Its
final inventory reader then rejected two genuine native roles: the initial
`staticPressurePa` function result and `0/uniform/time`. Both now have bounded,
complete typed readers, tied to the actual authored control and native
producer. Static pressure from `calcTotal no` is **gauge Pa**; the absolute
outlet reference is not added to that field. The initial six-key zero clock
is data only and establishes neither physical integration nor restart.
All 65 focused inlet-flow contracts pass. A read-only replay checks seven
retained native data roles without changing the rejected historical report.

That completed solver history is still unqualified. The last 20 iterations
have mean head about -0.400 m, relative head span 50.502%, pressure initial
residual up to 0.007309, and reconstructed minimum absolute pressure about
-484944 Pa. The flow equations use 9.81 m/s2 as explicitly declared in this
study. Completing 500 iterations or correcting a file reader does not qualify
these numerical or single-phase results. No active-phase H0, NPSHr/NPSH3,
physical transient, refinement or matched restart is obtained from this run.
