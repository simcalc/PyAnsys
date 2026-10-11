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

The next HXT trial has a **900-second** native meshing limit based on that
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
