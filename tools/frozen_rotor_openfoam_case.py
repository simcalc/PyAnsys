"""Prepare a separate Foundation-12 classroom pump case; never launch a solver.

Native conversion verification reads actual ASCII polyMesh and converter logs.
Preparation, conversion and an iteration-limited run do not establish a
converged pump baseline, NPSH3, physical transient or manufacturer performance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inspect_frozen_rotor_mesh as inventory

SCHEMA = "untirta.frozen-rotor-openfoam-preparation.v1"
MAX_MESH_BYTES = 128 * 1024**2
MAX_NATIVE_BYTES = 128 * 1024**2
MAX_COMPONENT_NODES = 500_000
MAX_COMPONENT_CELLS = 800_000
MAX_COMBINED_CELLS = 1_000_000
MAX_COMBINED_NODES = 1_000_000
COORDINATE_TOLERANCE = 1e-11  # Explicit tolerance for native 12+ significant-digit ASCII.
MAX_ITERATIONS = 500
RHO, MU, G, RPM, P_ABSOLUTE = 998.2, 0.001002, 9.81, 1450, 101325
Q = 90 / RHO
ROTATING = {"blade", "impeller-hub", "impeller-shroud"}
STATIONARY = {"inblock-hub", "inblock-shroud", "volute-inlet-wall", "volute-wall"}
INTERFACES = {"interface-impeller-outlet", "interface-volute-inlet"}
PATCHES = {"impeller": {"inlet", *ROTATING, "inblock-hub", "inblock-shroud", "interface-impeller-outlet"},
           "volute": {"mass-flow-inlet-11", "volute-wall", "volute-inlet-wall", "interface-volute-inlet"}}
EXTENDED_REMESH_PROFILE = "extended_free_hxt"
REMESH_INVARIANT_LIMITS = {"maximumOutputBytes": 128*1024**2, "maximumReportBytes": inventory.MAX_REPORT_BYTES,
    "maximumCellsPerComponent": 800_000, "maximumNodesPerComponent": 500_000,
    "maximumCombinedCells": 999_999, "maximumCombinedNodes": 999_999,
    "rawOutputBudgetChange": "128 MiB raw MSH2 ASCII trial budget; compressed publication budget is separately enforced."}


class CaseIssue(ValueError):
    pass


def regular_directory(value):
    value = Path(value).absolute()
    if not value.is_dir():
        raise CaseIssue("An existing regular independent input directory is required.")
    for item in (value, *value.parents):
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise CaseIssue("Input directories cannot contain links or junctions.")
    return value


def read_regular(value, limit):
    value = Path(value)
    if value.is_symlink() or not value.is_file() or not 0 < value.stat().st_size <= limit:
        raise CaseIssue("A regular bounded evidence file is required: " + value.name)
    return value.read_bytes()


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CaseIssue("A finite real engineering value is required.")
    return value


def remesh_resource_limits(profile="classroom"):
    """Exact disjoint mesher budgets; these do not change native CFD limits."""
    if not isinstance(profile, str) or profile not in {"classroom", EXTENDED_REMESH_PROFILE}:
        raise CaseIssue("Only the exact classroom or extended_free_hxt resource profile is supported.")
    resource = {"threads": 1, "maximumSeconds": 900, "maximumAddressSpaceBytes": 2*1024**3} if profile == "classroom" else \
               {"threads": 3, "maximumSeconds": 2400, "maximumAddressSpaceBytes": 8*1024**3}
    return {**resource, **REMESH_INVARIANT_LIMITS}


def validate_remesh_resource_profile(report):
    """Admit an enforced named resource profile without relaxing mesh checks."""
    profile = report.get("resourceProfile", "classroom")
    if not isinstance(profile, str) or profile not in {"classroom", EXTENDED_REMESH_PROFILE} or ("resourceProfile" in report and profile == "classroom"):
        raise CaseIssue("A new allocation requires the exact extended_free_hxt profile; default reports retain their original shape.")
    limits = report.get("limits")
    expected = remesh_resource_limits(profile)
    if not isinstance(limits, dict) or limits != expected or any(type(limits.get(key)) is not int for key, value in expected.items() if type(value) is int):
        raise CaseIssue("Every remeshing limit must match its exact declared resource profile and unchanged mesh/output caps.")
    if profile == "classroom":
        if "runtimeResourceEvidence" in report:
            raise CaseIssue("Extended runtime evidence cannot be relabelled as a legacy default attempt.")
        return profile
    def full_hxt(row):
        if not isinstance(row, dict): return False
        options = row.get("optimisation", {})
        return isinstance(options, dict) and row.get("algorithm") == "hxt" and type(row.get("gmshAlgorithm3D")) is int and row["gmshAlgorithm3D"] == 10 \
            and row.get("gmshAlgorithmName") == "HXT" and options.get("mode") == "full" \
            and type(options.get("meshOptimize")) is int and options["meshOptimize"] == 1
    meshes = report.get("meshes")
    boundary_flags = ("boundaryRemeshed", "surfaceReparametrizationPerformed", "sourceGeometryFabricated", "nodeWeldingPerformed", "topologyHealingPerformed")
    if not full_hxt(report) or not isinstance(meshes, list) or len(meshes) != 2 or not all(full_hxt(row) for row in meshes) \
       or not all(report.get(key) is False for key in boundary_flags) or \
       not all(row.get("boundaryRemeshed") is False and row.get("surfaceReparametrizationPerformed") is False for row in meshes):
        raise CaseIssue("extended_free_hxt admits only original-boundary full HXT10 with Mesh.Optimize=1.")
    evidence = report.get("runtimeResourceEvidence")
    fixed = {"publicRepository": "simcalc/PyAnsys", "manualWorkflowContextVerified": True,
        "runnerEnvironment": "github-hosted", "runnerOs": "Linux", "minimumCpuCount": 4,
        "minimumPhysicalMemoryBytes": 14*1024**3, "paidFallback": False, "customRunnerAllowed": False,
        "basis": "Actual os.cpu_count and bounded procfs MemTotal; repository public verification is performed by the workflow guard.",
        "addressSpaceSoftLimitBytes": 8*1024**3, "addressSpaceHardLimitBytes": 8*1024**3,
        "workerOpenMpThreads": 3, "workerBlasThreads": {"OPENBLAS_NUM_THREADS": 1, "MKL_NUM_THREADS": 1, "NUMEXPR_NUM_THREADS": 1},
        "nativeThreadOptions": {"General.NumThreads": 3, "Mesh.MaxNumThreads1D": 3, "Mesh.MaxNumThreads2D": 3, "Mesh.MaxNumThreads3D": 3}}
    if not isinstance(evidence, dict) or set(evidence) != set(fixed) | {"availableCpuCount", "physicalMemoryBytes"}:
        raise CaseIssue("The extended profile needs complete measured public-host runtime resource evidence.")
    for key, value in fixed.items():
        actual = evidence[key]
        if actual != value or (type(value) in (int, bool, str) and type(actual) is not type(value)):
            raise CaseIssue("Actual extended resource enforcement/context differs: " + key)
    if any(type(value) is not int or value != 1 for value in evidence["workerBlasThreads"].values()) or \
       any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value != 3 for value in evidence["nativeThreadOptions"].values()):
        raise CaseIssue("Read back exactly three native thread limits and one BLAS thread; utilization is not inferred.")
    if any(type(evidence[key]) is not int or not minimum <= evidence[key] <= maximum for key, minimum, maximum in (
            ("availableCpuCount", 4, 4096), ("physicalMemoryBytes", 14*1024**3, 1024**5))):
        raise CaseIssue("Measured hosted hardware must meet the exact finite CPU and physical-memory capacity bounds.")
    return profile


def msh_inventory(raw):
    """Validate the actual supported MSH2.2 nodes, linear cells and exact tags."""
    try:
        text = raw.decode("ascii")
    except UnicodeError as error:
        raise CaseIssue("Actual MSH2.2 ASCII is required.") from error
    sections = {}
    for match in re.finditer(r"(?m)^\$(\w+)\s*\n(.*?)^\$End\1\s*$", text, re.S):
        if match[1] in sections:
            raise CaseIssue("Duplicate MSH sections are unsupported.")
        sections[match[1]] = match[2].strip().splitlines()
    if set(sections) != {"MeshFormat", "PhysicalNames", "Nodes", "Elements"} or sections["MeshFormat"] != ["2.2 0 8"]:
        raise CaseIssue("Use a linear Gmsh MSH2.2 ASCII file with explicit physical names.")
    physical = {}
    for row in sections["PhysicalNames"][1:]:
        match = re.fullmatch(r'(2|3) (\d+) "([A-Za-z0-9_-]+)"', row.strip())
        if not match:
            raise CaseIssue("Safe exact physical group names are required.")
        key = (int(match[1]), int(match[2]))
        if key in physical:
            raise CaseIssue("Physical groups must be unique.")
        physical[key] = match[3]
    if len(physical) != int(sections["PhysicalNames"][0]):
        raise CaseIssue("The physical-name count must match its actual records.")
    # Foundation gmshToFoam uses one physical-name map across dimensions.
    if len({key[1] for key in physical}) != len(physical):
        raise CaseIssue("Physical tags must be globally unique for Foundation gmshToFoam.")
    nodes = {}
    for row in sections["Nodes"][1:]:
        values = row.split()
        if len(values) != 4:
            raise CaseIssue("Every node needs an integer ID and three coordinates.")
        node = int(values[0])
        coordinates = tuple(float(value) for value in values[1:])
        if node <= 0 or node in nodes or not all(math.isfinite(value) for value in coordinates):
            raise CaseIssue("Node IDs must be unique and coordinates finite.")
        nodes[node] = coordinates
    if not nodes or len(nodes) != int(sections["Nodes"][0]) or len(nodes) > MAX_COMPONENT_NODES:
        raise CaseIssue("The actual node count differs from the bounded MSH declaration.")
    counts, used, element_ids, connectivity_seen, cells = {}, set(), set(), set(), 0
    for row in sections["Elements"][1:]:
        values = [int(value) for value in row.split()]
        if len(values) < 8 or values[0] <= 0 or values[0] in element_ids or values[1] not in (2, 4) or values[2] < 2:
            raise CaseIssue("Only unique linear triangle and tetrahedron elements with physical tags are supported.")
        element_ids.add(values[0])
        dimension, expected = (2, 3) if values[1] == 2 else (3, 4)
        connectivity = values[3 + values[2]:]
        key = (dimension, values[3])
        if key not in physical or len(connectivity) != expected or len(set(connectivity)) != expected or any(node not in nodes for node in connectivity):
            raise CaseIssue("Every element must preserve its physical group and valid distinct node references.")
        canonical = (dimension, tuple(sorted(connectivity)))
        if canonical in connectivity_seen:
            raise CaseIssue("Duplicate triangles or tetrahedra cannot preserve native identity.")
        connectivity_seen.add(canonical)
        xyz = [nodes[node] for node in connectivity]
        ab = [xyz[1][i]-xyz[0][i] for i in range(3)]
        ac = [xyz[2][i]-xyz[0][i] for i in range(3)]
        cross = (ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2], ab[0]*ac[1]-ab[1]*ac[0])
        if not math.isfinite(math.hypot(*cross)) or math.hypot(*cross) <= 0:
            raise CaseIssue("Actual surface/volume elements cannot have degenerate triangle geometry.")
        if dimension == 3:
            ad = [xyz[3][i]-xyz[0][i] for i in range(3)]
            determinant = math.fsum(cross[i]*ad[i] for i in range(3))
            if not math.isfinite(determinant) or determinant <= 0:
                raise CaseIssue("Actual MSH tetrahedra need finite positive signed volume.")
        used.update(connectivity)
        name = physical[key]
        counts[name] = counts.get(name, 0) + 1
        cells += dimension == 3
    if len(element_ids) != int(sections["Elements"][0]) or used != set(nodes) or not 0 < cells <= MAX_COMPONENT_CELLS:
        raise CaseIssue("All MSH nodes/elements and a bounded nonempty volume must be accounted for.")
    volumes = [name for (dimension, _), name in physical.items() if dimension == 3]
    if len(volumes) != 1:
        raise CaseIssue("One exact fluid volume is required in each component.")
    return {"nodeCount": len(nodes), "cellCount": cells, "patchFaces": {name: count for name, count in counts.items() if name != volumes[0]},
            "cellZone": volumes[0], "nodeCoordinatesInFileOrder": list(nodes.values())}


def validate_inputs(directory):
    directory = regular_directory(directory)
    raw = read_regular(directory / "remesh-report.json", inventory.MAX_REPORT_BYTES)
    report = json.loads(raw)
    for key, expected in {"schemaVersion": "untirta.frozen-rotor-classroom-remesh.v1", "exampleDataCommit": inventory.PIN,
                          "status": "VOLUME_MESH_GENERATED", "volumeMeshGenerated": True, "originalMeshesImmutable": True,
                          "simulationExecuted": False, "actualCfdRunVerified": False, "mainApplicationWrites": False}.items():
        if report.get(key) != expected:
            raise CaseIssue("An actual successful source-pinned remeshing report is required: " + key)
    validate_remesh_resource_profile(report)
    assumption = report.get("classroomScaleAssumption", {})
    if assumption.get("nativeUnitsToMetres") != 1 or assumption.get("verifiedFromSource") is not False:
        raise CaseIssue("Preserve the explicitly unverified one-unit-to-one-metre classroom assumption.")
    sources = report.get("sources", [])
    if len(sources) != 2 or {row.get("name") for row in sources} != set(inventory.MESH_NAMES):
        raise CaseIssue("Both immutable official source meshes must be bound.")
    for source in sources:
        expected_bytes, expected_hash = inventory.FILES[source["name"]]
        if source.get("bytes") != expected_bytes or source.get("sha256") != expected_hash:
            raise CaseIssue("An immutable official source pin differs.")
    rows = report.get("meshes", [])
    if len(rows) != 2 or {row.get("sourceMesh") for row in rows} != set(inventory.MESH_NAMES):
        raise CaseIssue("Two actual remeshed component records are required.")
    if sum(finite_number(row.get("generatedFile", {}).get("bytes")) for row in rows) > MAX_MESH_BYTES:
        raise CaseIssue("Combined actual generated meshes exceed the bounded 128 MiB input budget.")
    result = {}
    for row in rows:
        component = row["sourceMesh"].split(".")[0]
        metadata = row.get("generatedFile", {})
        if metadata.get("name") != component + ".msh" or metadata.get("format") != "gmsh-msh2.2-ascii":
            raise CaseIssue("Use only the two exact generated MSH component filenames.")
        mesh = read_regular(directory / metadata["name"], MAX_MESH_BYTES)
        if len(mesh) != metadata.get("bytes") or sha256(mesh).hexdigest() != metadata.get("sha256"):
            raise CaseIssue("Generated MSH size or checksum differs from its actual report.")
        actual = msh_inventory(mesh)
        expected_zone = "rotating-fluid" if component == "impeller" else "stationary-fluid"
        if set(actual["patchFaces"]) != PATCHES[component] or actual["cellZone"] != expected_zone:
            raise CaseIssue("Native patch and component identities must survive remeshing exactly.")
        if actual["nodeCount"] != row.get("generatedNodeCount") or actual["cellCount"] != row.get("generatedCellCount"):
            raise CaseIssue("Actual MSH counts differ from the remeshing evidence.")
        source_cells = 773352 if component == "impeller" else 368411
        if actual["cellCount"] >= source_cells:
            raise CaseIssue("The actual generated component must contain fewer cells than its immutable source.")
        zones = row.get("boundaryZones", [])
        expected_faces = {zone.get("sourceZoneName"): zone.get("generated", {}).get("triangleCount") for zone in zones}
        if expected_faces != actual["patchFaces"]:
            raise CaseIssue("Actual MSH boundary counts differ from the exact remeshed source-zone records.")
        result[component] = {"path": str(directory / metadata["name"]), "sha256": metadata["sha256"], **actual}
    if sum(row["cellCount"] for row in result.values()) >= MAX_COMBINED_CELLS or sum(row["nodeCount"] for row in result.values()) >= MAX_COMBINED_NODES:
        raise CaseIssue("Actual combined generated cells and nodes must remain below one million.")
    return result, sha256(raw).hexdigest()


def foam_header(name, kind="dictionary", location=None):
    return "FoamFile\n{\n    version 2.0;\n    format ascii;\n    class " + kind + ";\n" + ("    location \"" + location + "\";\n" if location else "") + "    object " + name + ";\n}\n"


def field(name, dimension, initial, patches):
    kind = "volVectorField" if name == "U" else "volScalarField"
    return foam_header(name, kind, "0") + "dimensions [" + dimension + "];\ninternalField uniform " + initial + ";\nboundaryField\n{\n    #includeEtc \"caseDicts/setConstraintTypes\"\n" + "".join("    \"" + patch + "\"\n    {\n        " + body + "\n    }\n" for patch, body in sorted(patches.items())) + "}\n"


def function_objects():
    # Foundation12 uses select/average+weightField, not regionType/weightedAverage.
    blocks = ["totalPressurePa {type pressure; libs (\"libfieldFunctionObjects.so\"); field p; U U; rho rhoInf; rhoInf 998.2; calcTotal yes; pRef 101325; calcCoeff no; result totalPressurePa; executeControl timeStep; executeInterval 1; writeControl writeTime;}",
              "gaugeStaticPressurePa {type pressure; libs (\"libfieldFunctionObjects.so\"); field p; rho rhoInf; rhoInf 998.2; calcTotal no; calcCoeff no; result staticPressurePa; executeControl timeStep; executeInterval 1; writeControl writeTime;}",
              "absoluteVelocitySquared {type magSqr; libs (\"libfieldFunctionObjects.so\"); field U; result U2; executeControl timeStep; executeInterval 1; writeControl writeTime;}",
              "globalPressureMinimum {type volFieldValue; libs (\"libfieldFunctionObjects.so\"); select all; operation min; fields (p); writeFields false; writeLocation false; log false; writeControl timeStep; writeInterval 1;}",
              "globalPressureMaximum {type volFieldValue; libs (\"libfieldFunctionObjects.so\"); select all; operation max; fields (p); writeFields false; writeLocation false; log false; writeControl timeStep; writeInterval 1;}",
              "wallYPlus {type yPlus; libs (\"libfieldFunctionObjects.so\"); writeControl writeTime; log true;}"]
    for suffix, patch in (("Inlet", "inlet"), ("Outlet", "mass-flow-inlet-11")):
        common = 'type surfaceFieldValue; libs ("libfieldFunctionObjects.so"); select patch; name ' + patch + '; writeFields false; executeControl timeStep; executeInterval 1; writeControl timeStep; writeInterval 1; log false; '
        blocks.append("flow" + suffix + " {" + common + "operation sum; fields (phi);}")
        blocks.append("flowMagnitude" + suffix + " {" + common + "operation sumMag; fields (phi);}")
        blocks.append("weighted" + suffix + " {" + common + "operation average; weightField phi; fields (p totalPressurePa U2);}")
    # Exact names generated by the named pumpInterface dictionary couple.
    for suffix, name in (("RotorInterface", "pumpInterface_on_interface-impeller-outlet"),
                         ("StatorInterface", "pumpInterface_on_interface-volute-inlet")):
        common = 'type surfaceFieldValue; libs ("libfieldFunctionObjects.so"); select patch; name ' + name + '; writeFields false; executeControl timeStep; executeInterval 1; writeControl timeStep; writeInterval 1; log false; '
        blocks.append("flow" + suffix + " {" + common + "operation sum; fields (phi);}")
        blocks.append("flowMagnitude" + suffix + " {" + common + "operation sumMag; fields (phi);}")
    return "\n".join(blocks) + "\n"


def case_files(component):
    # Foundation mergeMeshes merges polyMesh, not the solution fields. The
    # impeller directory becomes the combined case, so its final field patch
    # dictionaries must already include both component boundaries. Extra
    # entries are ignored while each original component is still separate.
    component_names = PATCHES[component]
    names = set().union(*PATCHES.values()) if component == "impeller" else component_names
    u, p, k, omega, nut = {}, {}, {}, {}, {}
    for name in names:
        if name == "inlet":
            u[name] = "type pressureInletOutletVelocity; value uniform (0 0 0);"
            p[name] = "type totalPressure; p0 uniform 0; value uniform 0;"
            k[name], omega[name] = "type fixedValue; value uniform 0.375;", "type fixedValue; value uniform 100;"
            nut[name] = "type calculated; value uniform 0;"
        elif name == "mass-flow-inlet-11":
            u[name] = "type flowRateOutletVelocity; volumetricFlowRate " + format(Q, ".17g") + "; value uniform (0 0 0);"
            p[name] = "type zeroGradient;"
            k[name], omega[name], nut[name] = "type zeroGradient;", "type zeroGradient;", "type calculated; value uniform 0;"
        elif name in INTERFACES:
            u[name], p[name], k[name], omega[name], nut[name] = "type slip;", "type zeroGradient;", "type zeroGradient;", "type zeroGradient;", "type calculated; value uniform 0;"
        else:
            u[name] = "type MRFnoSlip;" if name in ROTATING else "type noSlip;"
            p[name] = "type fixedFluxPressure;"
            k[name], omega[name], nut[name] = "type kqRWallFunction; value uniform 0.375;", "type omegaWallFunction; value uniform 100;", "type nutkWallFunction; value uniform 0;"
    # gmshToFoam can retain this empty patch. The conversion gate forbids any
    # positive face count, so these entries assign no physical boundary data.
    for patches in (u, p, k, omega):
        patches["defaultFaces"] = "type zeroGradient;"
    nut["defaultFaces"] = "type calculated; value uniform 0;"
    result = {"0/U": field("U", "0 1 -1 0 0 0 0", "(0 0 0)", u), "0/p": field("p", "0 2 -2 0 0 0 0", "0", p),
              "0/k": field("k", "0 2 -2 0 0 0 0", "0.375", k), "0/omega": field("omega", "0 0 -1 0 0 0 0", "100", omega),
              "0/nut": field("nut", "0 2 -1 0 0 0 0", "0", nut),
              "constant/physicalProperties": foam_header("physicalProperties") + "viscosityModel constant;\nnu [0 2 -1 0 0 0 0] " + format(MU/RHO, ".17g") + ";\n",
              "constant/momentumTransport": foam_header("momentumTransport") + "simulationType RAS;\nRAS {model kOmegaSST; turbulence on; printCoeffs on;}\n",
              "system/fvSchemes": foam_header("fvSchemes") + "ddtSchemes {default steadyState;}\ngradSchemes {default Gauss linear; grad(U) cellLimited Gauss linear 1;}\ndivSchemes {default none; div(phi,U) bounded Gauss limitedLinearV 1; div(phi,k) bounded Gauss limitedLinear 1; div(phi,omega) bounded Gauss limitedLinear 1; div((nuEff*dev2(T(grad(U))))) Gauss linear;}\nlaplacianSchemes {default Gauss linear limited corrected 0.5;}\ninterpolationSchemes {default linear;}\nsnGradSchemes {default limited corrected 0.5;}\n",
              "system/fvSolution": foam_header("fvSolution") + "solvers {p {solver GAMG; tolerance 1e-8; relTol 0.05; smoother GaussSeidel;} \"(U|k|omega)\" {solver smoothSolver; smoother symGaussSeidel; tolerance 1e-7; relTol 0.1;}}\nSIMPLE {nNonOrthogonalCorrectors 2;}\nrelaxationFactors {fields {p 0.3;} equations {U 0.5; k 0.5; omega 0.5;}}\n",
              "system/controlDict": foam_header("controlDict") + "application foamRun;\nsolver incompressibleFluid;\nstartFrom startTime;\nstartTime 0;\nstopAt endTime;\nendTime 500;\ndeltaT 1;\nwriteControl timeStep;\nwriteInterval 500;\npurgeWrite 1;\nwriteFormat ascii;\nwritePrecision 17;\nwriteCompression off;\ntimeFormat general;\ntimePrecision 12;\nrunTimeModifiable false;\nfunctions\n{\n" + (function_objects() if component == "impeller" else "") + "}\n",
              "system/changeDictionaryDict": foam_header("changeDictionaryDict") + "boundary\n{\n" + "".join('    \"' + name + '\" {type wall;}\n' for name in sorted(component_names & (ROTATING | STATIONARY))) + "}\n"}
    if component == "impeller":
        result["constant/MRFProperties"] = foam_header("MRFProperties") + "rotor {cellZone rotating-fluid; origin (0 0 0); axis (0 0 1); omega 1450 [rpm];}\n"
        result["system/createNonConformalCouplesDict"] = foam_header("createNonConformalCouplesDict") + "fields true;\nnonConformalCouples {pumpInterface {patches (interface-impeller-outlet interface-volute-inlet); transform none;}}\n"
    return result


def write_exclusive(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def prepare(mesh_directory, output_directory):
    meshes, remesh_hash = validate_inputs(mesh_directory)
    output = Path(output_directory).absolute()
    inventory.output_path(output / "openfoam-preparation.json")
    if output.exists() or output.is_symlink():
        raise CaseIssue("Use a fresh RUNNER_TEMP case output directory.")
    output.mkdir(parents=True)
    records, commands = [], []
    for component in ("impeller", "volute"):
        case = output / (component + "-case")
        for name, content in case_files(component).items():
            write_exclusive(case / name, content.encode("ascii"))
        target = case / (component + ".msh")
        write_exclusive(target, read_regular(meshes[component]["path"], MAX_MESH_BYTES))
        commands.extend([["gmshToFoam", "-case", str(case), str(target)], ["changeDictionary", "-case", str(case)]])
        records.append({"component": component, "caseDirectory": str(case), "meshSha256": meshes[component]["sha256"],
                        **{key: meshes[component][key] for key in ("nodeCount", "cellCount", "patchFaces", "cellZone")}})
    main_case, other_case = output / "impeller-case", output / "volute-case"
    commands.extend([["mergeMeshes", "-case", str(main_case), "-addCases", '("' + str(other_case) + '")', "-overwrite"],
                     ["createNonConformalCouples", "-case", str(main_case), "-overwrite"],
                     ["checkMesh", "-case", str(main_case), "-allGeometry", "-allTopology"], ["foamRun", "-case", str(main_case)]])
    report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(), "remeshReportSha256": remesh_hash,
              "exampleDataCommit": inventory.PIN, "status": "CASE_PREPARED_NATIVE_EXECUTION_REQUIRED", "components": records,
              "nativeCommandArguments": commands, "commandExecutionPerformedByThisTool": False,
              "requiredConversionGate": "Verify actual converter logs, points/faces/owner/neighbour/boundary/cellZones before merge; verify merged counts and NCC coverage before the solver.",
              "limits": {"iterations": MAX_ITERATIONS, "cpus": 1, "memoryBytes": 2*1024**3, "maximumSeconds": 1200, "maximumWorkspaceBytes": 512*1024**2,
                         "enforcementOwner": "Independent root workflow cgroup/timeout/output guard; this preparation tool does not launch native processes."},
              "classroomAssumptions": {"metresPerNativeUnit": 1, "nativeUnitsVerified": False, "rhoKgM3": RHO, "muPaS": MU, "gMS2": G,
                  "speedRpm": RPM, "axis": [0,0,1], "massFlowKgS": 90, "volumetricFlowM3S": Q, "externalTotalPressureAbsolutePa": P_ABSOLUTE,
                  "kinematicGaugePressureDefinition": "pAbsolutePa=998.2*pKinematic+101325; inlet external total gauge pressure=0 m2/s2.",
                  "inletTurbulence": {"kM2S2": .375, "omegaSInverse": 100, "basis": "Explicit prescribed classroom inlet values; not manufacturer or measured turbulence."},
                  "gravityInSolver": False, "headDatum": "No gravity field in steady singlephase solver; report total-pressure rise and add declared elevation correction independently.",
                  "wallAdaptation": "Rotating blade/impeller-hub/impeller-shroud; stationary inblock-hub/inblock-shroud/volute walls. Source Python explicitly configures only two wall motions; remaining wall defaults were not verified. This is a declared classroom adaptation."},
              "sampling": {"nativeFunctionObjectConvention": "Foundation12 select patch; operation average; weightField phi (signed).", "flowUnits": "m3/s outward positive",
                  "totalPressureField": "totalPressurePa includes rho*pKinematic + rho*|Uabsolute|^2/2 + 101325.",
                  "staticPressureField": "staticPressurePa from calcTotal=no is rho*pKinematic only; add 101325 exactly once in downstream absolute conversion.",
                  "reverseFlowFromSumAndSumMag": "outletReverse=(sumMagPhi-sumPhi)/2; inletReverse=(sumMagPhi+sumPhi)/2.",
                  "iterationIndexIsPhysicalTime": False, "qualifiedBaseline": False},
              "requiredAcceptance": ["Actual native conversion matches source-pinned generated counts/identities", "checkMesh geometry/topology and coupled interface coverage",
                  "Conservation, finite fields, residual histories, head/flow stationarity over declared final window", "Wall resolution/yPlus and mesh convergence",
                  "Independent phase-change sweep with absolute pressure and qualified noncavitating reference", "Physical-time transient, time-step convergence and matched-field restart comparison"],
              "openFoamConversionVerified": False, "nativeCaseGenerated": False, "actualCfdRunVerified": False, "qualifiedBaseline": False,
              "simulationExecuted": False, "fluentSessionConnected": False, "licenseVerified": False, "mainApplicationWrites": False,
              "claimBoundary": "Prepared OpenFOAM dictionaries from actual remeshed components under explicit classroom assumptions; no executed or validated CFD result.",
              "references": ["https://github.com/OpenFOAM/OpenFOAM-12/tree/master/tutorials/incompressibleFluid/mixerVessel2DMRF",
                  "https://github.com/OpenFOAM/OpenFOAM-12/blob/master/src/finiteVolume/fields/fvPatchFields/derived/flowRateOutletVelocity/flowRateOutletVelocityFvPatchVectorField.H",
                  "https://github.com/OpenFOAM/OpenFOAM-12/blob/master/applications/utilities/mesh/manipulation/mergeMeshes/mergeMeshes.C"]}
    write_exclusive(output / "openfoam-preparation.json", inventory.bounded_json(report))
    return {"status": report["status"], "reportFile": str(output / "openfoam-preparation.json"), "caseDirectories": [row["caseDirectory"] for row in records],
            "actualCfdRunVerified": False, "mainApplicationWrites": False}


def foam_text(path):
    text = read_regular(path, MAX_NATIVE_BYTES).decode("ascii")
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
    match = re.search(r"FoamFile\s*\{([^{}]*)\}", text)
    if not match or not re.search(r"\bformat\s+ascii\s*;", match[1]):
        raise CaseIssue("Native verification requires actual ASCII FoamFile storage.")
    # Foundation 12 (86e126a7bc4dbfaed949bbc58c9f0fdd67acc059):
    # cellZoneList.H declares this class; polyMeshIO.C binds object cellZones.
    # It is valid only for that role, with its counted zone ownership checked below.
    expected = {"points": {"vectorField"}, "faces": {"faceList"}, "owner": {"labelList"}, "neighbour": {"labelList"},
                "boundary": {"polyBoundaryMesh"}, "cellZones": {"regIOobject", "cellZoneMesh", "cellZoneList"}}
    native_class = re.search(r"\bclass\s+(\w+)\s*;", match[1])
    native_object = re.search(r"\bobject\s+([A-Za-z0-9_-]+)\s*;", match[1])
    name = Path(path).name
    if name not in expected or not native_class or native_class[1] not in expected[name] or not native_object or native_object[1] != name:
        raise CaseIssue("Native FoamFile class/object must match its actual polyMesh role.")
    return text[match.end():].strip()


def native_list(path):
    match = re.fullmatch(r"(\d+)\s*\((.*)\)\s*;?", foam_text(path), re.S)
    if not match:
        raise CaseIssue("A counted native list is required: " + Path(path).name)
    return int(match[1]), match[2].strip()


def verify_conversion(case_directory, expected, converter_log, *, after_wall_type_change=True):
    """Read actual native topology; never rewrite a converted mesh or trust exit alone."""
    case = regular_directory(case_directory)
    if not isinstance(after_wall_type_change, bool):
        raise CaseIssue("The physical-wall verification stage must be an explicit boolean.")
    log = read_regular(converter_log, 4*1024**2).decode("utf-8", errors="strict")
    if not re.search(r"(?m)^End\s*$", log) or re.search(r"FOAM FATAL|Segmentation fault|Floating point exception", log):
        raise CaseIssue("A completed actual gmshToFoam log without native fatal errors is required.")
    mesh = case / "constant/polyMesh"
    np, point_body = native_list(mesh / "points")
    vectors = re.findall(r"\(([^()]*)\)", point_body)
    points = [tuple(float(value) for value in row.split()) for row in vectors]
    if np != expected["nodeCount"] or len(points) != np or any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in points):
        raise CaseIssue("Actual converted points differ from the generated component count.")
    generated_points = expected.get("nodeCoordinatesInFileOrder")
    if not generated_points or len(generated_points) != np:
        raise CaseIssue("Use the actual parsed generated MSH coordinates for conversion verification.")
    maximum_deviation = max(abs(a-b) for native, generated in zip(points, generated_points) for a,b in zip(native,generated))
    if not math.isfinite(maximum_deviation) or maximum_deviation > COORDINATE_TOLERANCE:
        raise CaseIssue("Converted point coordinates/order differ from the generated MSH at the required precision.")
    no, owner_body = native_list(mesh / "owner")
    nn, neighbour_body = native_list(mesh / "neighbour")
    owner, neighbour = [int(v) for v in owner_body.split()], [int(v) for v in neighbour_body.split()]
    if len(owner) != no or len(neighbour) != nn or nn > no or any(v < 0 or v >= expected["cellCount"] for v in owner + neighbour):
        raise CaseIssue("Actual owner/neighbour lists need valid complete cell references.")
    if set(owner + neighbour) != set(range(expected["cellCount"])):
        raise CaseIssue("All generated cells must remain present in native topology.")
    nf, face_body = native_list(mesh / "faces")
    face_rows = re.findall(r"(\d+)\s*\(([^()]*)\)", face_body)
    if nf != no or len(face_rows) != nf:
        raise CaseIssue("Actual face and owner counts must agree.")
    for n, vertices in face_rows:
        ids = [int(v) for v in vertices.split()]
        if len(ids) != int(n) or len(ids) < 3 or len(set(ids)) != len(ids) or any(v < 0 or v >= np for v in ids):
            raise CaseIssue("Every converted native face needs distinct valid point references.")
    nb, boundary_body = native_list(mesh / "boundary")
    patches = re.findall(r"([A-Za-z0-9_-]+)\s*\{([^{}]*)\}", boundary_body)
    if len(patches) != nb:
        raise CaseIssue("Actual boundary patches must match the native list count.")
    actual, start, empty_placeholder = {}, nn, None
    for name, body in patches:
        size = re.search(r"\bnFaces\s+(\d+)\s*;", body)
        first = re.search(r"\bstartFace\s+(\d+)\s*;", body)
        if name in actual or not size or not first or int(first[1]) != start:
            raise CaseIssue("Actual boundary face ranges must be contiguous and unique.")
        actual[name] = int(size[1])
        patch_type = re.search(r"\btype\s+(\w+)\s*;", body)
        expected_types = {"patch"}
        if name in ROTATING | STATIONARY:
            expected_types = {"wall"} if after_wall_type_change else {"patch", "wall"}
        if not patch_type or patch_type[1] not in expected_types:
            raise CaseIssue("Native wall/interface class does not match the declared verification stage; apply changeDictionary before the physical-wall gate.")
        if name == "defaultFaces":
            if actual[name] != 0 or not re.search(r"\btype\s+patch\s*;", body):
                raise CaseIssue("Only a zero-face plain defaultFaces placeholder is permitted; no unassigned positive faces.")
            empty_placeholder = {"name": name, "nFaces": 0, "type": "patch", "startFace": start}
        start += actual[name]
    actual_without_placeholder = {name: count for name,count in actual.items() if name != "defaultFaces"}
    if start != no or actual_without_placeholder != expected["patchFaces"]:
        raise CaseIssue("Converted native boundary tags/counts differ from the actual source-tagged generated mesh.")
    nz, zone_body = native_list(mesh / "cellZones")
    zones = re.findall(r"([A-Za-z0-9_-]+)\s*\{([^{}]*)\}", zone_body)
    if nz != 1 or len(zones) != 1 or zones[0][0] != expected["cellZone"]:
        raise CaseIssue("The exact generated fluid cellZone is required.")
    labels = re.search(r"\bcellLabels\s+(?:List<label>\s+)?(\d+)\s*\(([^()]*)\)\s*;", zones[0][1], re.S)
    if not labels or int(labels[1]) != expected["cellCount"] or sorted(int(v) for v in labels[2].split()) != list(range(expected["cellCount"])):
        raise CaseIssue("The actual fluid cellZone must cover every converted cell exactly once.")
    return {"componentCellZone": expected["cellZone"], "nodeCount": np, "cellCount": expected["cellCount"], "faceCount": nf,
            "patchFaces": actual_without_placeholder, "emptyDefaultFacesPlaceholder": empty_placeholder, "conversionVerified": True, "nativeGeometryQualityVerified": False, "actualCfdRunVerified": False,
            "physicalWallTypesVerified": after_wall_type_change,
            "maximumCoordinateDeviationAssumedM": maximum_deviation,
            "coordinateVerificationToleranceAssumedM": COORDINATE_TOLERANCE,
            "logSha256": sha256(log.encode("utf-8")).hexdigest(), "claim": "Actual converter completion plus native points/topology/tag/count/cell coverage; coupled coverage and checkMesh remain separate."}


def verify_merged_mesh(case_directory, expected_components, check_mesh_log):
    """Verify the actual merged native mesh and fixed NCC pair before a solve.

    Native non-conformal cyclic/error patches have zero poly faces; their
    intersection coverage must be read from the actual checkMesh log. An
    original interface remains a plain patch and retains its source faces.
    """
    case = regular_directory(case_directory)
    components = list(expected_components.values()) if isinstance(expected_components, dict) else expected_components
    if not isinstance(components, list) or len(components) != 2 or not all(isinstance(row, dict) for row in components):
        raise CaseIssue("Bind both actual converted component inventories before the merged mesh gate.")
    zones_expected, patches_expected, patch_zone, point_count, cell_count = {}, {}, {}, 0, 0
    for row in components:
        zone, count, nodes, patches = row.get("cellZone"), row.get("cellCount"), row.get("nodeCount"), row.get("patchFaces")
        if zone not in {"rotating-fluid", "stationary-fluid"} or zone in zones_expected or any(isinstance(n, bool) or not isinstance(n, int) or n <= 0 for n in (count, nodes)) or not isinstance(patches, dict):
            raise CaseIssue("Require the two exact fluid zone identities and positive actual component counts.")
        if set(patches_expected) & set(patches) or any(isinstance(n, bool) or not isinstance(n, int) or n <= 0 for n in patches.values()):
            raise CaseIssue("Actual source patch inventories must be distinct with positive face counts.")
        zones_expected[zone] = count
        patches_expected.update(patches)
        patch_zone.update({name: zone for name in patches})
        point_count += nodes
        cell_count += count
    if set(zones_expected) != {"rotating-fluid", "stationary-fluid"} or set(patches_expected) != set().union(*PATCHES.values()):
        raise CaseIssue("Merged verification requires both full exact source component patch inventories.")
    log_raw = read_regular(check_mesh_log, 4*1024**2)
    log = log_raw.decode("utf-8")
    if not re.search(r"(?m)^End\s*$", log) or "Mesh OK." not in log or re.search(r"FOAM FATAL|Segmentation fault|Floating point exception|Failed [1-9]\d* mesh checks", log):
        raise CaseIssue("Completed actual checkMesh geometry/topology acceptance is required before foamRun.")
    native_cells = re.findall(r"(?m)^\s*cells:\s*(\d+)\s*$", log)
    if len(native_cells) != 1 or int(native_cells[0]) != cell_count:
        raise CaseIssue("The actual checkMesh merged cell count must equal both component counts.")
    mesh = case / "constant/polyMesh"
    np, point_body = native_list(mesh / "points")
    seen_points, end = 0, 0
    for match in re.finditer(r"\(([^()]*)\)", point_body):
        if point_body[end:match.start()].strip():
            raise CaseIssue("Every native merged point must have a complete plain vector row.")
        xyz = [float(value) for value in match[1].split()]
        if len(xyz) != 3 or not all(math.isfinite(value) for value in xyz):
            raise CaseIssue("Merged point coordinates must remain finite three-vectors.")
        seen_points += 1
        end = match.end()
    if point_body[end:].strip() or np != point_count or seen_points != np:
        raise CaseIssue("Actual merged points must preserve the combined converted point count.")
    no, owner_body = native_list(mesh / "owner")
    nn, neighbour_body = native_list(mesh / "neighbour")
    owner, neighbour = [int(value) for value in owner_body.split()], [int(value) for value in neighbour_body.split()]
    if len(owner) != no or len(neighbour) != nn or nn > no or any(value < 0 or value >= cell_count for values in (owner, neighbour) for value in values):
        raise CaseIssue("Merged native owner/neighbour lists must be complete and within the actual cell domain.")
    if set(owner) | set(neighbour) != set(range(cell_count)) or any(owner[i] >= neighbour[i] for i in range(nn)):
        raise CaseIssue("The merged topology must retain every cell and ordered distinct internal owner/neighbour pairs.")
    nf, face_body = native_list(mesh / "faces")
    face_count, end = 0, 0
    for match in re.finditer(r"(\d+)\s*\(([^()]*)\)", face_body):
        if face_body[end:match.start()].strip():
            raise CaseIssue("Every merged native face must have a complete counted plain row.")
        ids = [int(value) for value in match[2].split()]
        if len(ids) != int(match[1]) or len(ids) < 3 or len(set(ids)) != len(ids) or any(value < 0 or value >= np for value in ids):
            raise CaseIssue("Merged native faces must retain valid distinct point references.")
        face_count += 1
        end = match.end()
    if face_body[end:].strip() or nf != no or face_count != nf:
        raise CaseIssue("Actual merged face and owner counts must agree.")
    nb, boundary_body = native_list(mesh / "boundary")
    entries = re.findall(r'"?([A-Za-z0-9_-]+)"?\s*\{([^{}]*)\}', boundary_body)
    if len(entries) != nb:
        raise CaseIssue("Merged native boundary must have a complete simple patch dictionary list.")
    rotor, stator = "interface-impeller-outlet", "interface-volute-inlet"
    coupled = {"pumpInterface_on_" + rotor: (rotor, "pumpInterface_on_" + stator),
               "pumpInterface_on_" + stator: (stator, "pumpInterface_on_" + rotor)}
    errors = {"nonConformalError_on_" + original: original for original in (rotor, stator)}
    actual, ranges, start, placeholders = {}, {}, nn, []
    for name, body in entries:
        size = re.search(r"\bnFaces\s+(\d+)\s*;", body)
        first = re.search(r"\bstartFace\s+(\d+)\s*;", body)
        kind = re.search(r"\btype\s+(\w+)\s*;", body)
        if name in actual or not size or not first or not kind or int(first[1]) != start:
            raise CaseIssue("Merged native patches must have contiguous unique face ranges and explicit classes.")
        count = int(size[1])
        actual[name] = count
        ranges[name] = (start, count)
        if name in patches_expected:
            expected_type = "wall" if name in ROTATING | STATIONARY else "patch"
            if kind[1] != expected_type or count != patches_expected[name]:
                raise CaseIssue("The merged source patch face counts and physical wall types must remain exact.")
        elif name in coupled:
            original, opposite = coupled[name]
            if kind[1] != "nonConformalCyclic" or count != 0 or not re.search(r"\boriginalPatch\s+" + re.escape(original) + r"\s*;", body) or not re.search(r"\bneighbourPatch\s+" + re.escape(opposite) + r"\s*;", body) or not re.search(r"\btransformType\s+none\s*;", body):
                raise CaseIssue("The exact reciprocal non-conformal cyclic pair must bind its original source interfaces with no transform.")
        elif name in errors:
            if kind[1] != "nonConformalError" or count != 0 or not re.search(r"\boriginalPatch\s+" + re.escape(errors[name]) + r"\s*;", body):
                raise CaseIssue("The two exact native non-conformal error placeholders must bind the original interfaces.")
        elif name == "defaultFaces" and kind[1] == "patch" and count == 0:
            placeholders.append({"name": name, "type": "patch", "nFaces": 0, "startFace": start})
        else:
            raise CaseIssue("Unexpected or positive unassigned merged boundary patches are prohibited.")
        start += count
    required = set(patches_expected) | set(coupled) | set(errors)
    if required - set(actual) or start != no:
        raise CaseIssue("Merged native boundary must retain every exact source patch and both complete NCC pair/error placeholders.")
    nz, zone_body = native_list(mesh / "cellZones")
    zones = re.findall(r'"?([A-Za-z0-9_-]+)"?\s*\{([^{}]*)\}', zone_body)
    if nz != 2 or len(zones) != 2 or {name for name, _ in zones} != set(zones_expected):
        raise CaseIssue("Merged native cell zones must retain both exact fluid identities.")
    all_labels, verified_zones, zone_labels = set(), {}, {}
    for name, body in zones:
        labels = re.search(r"\bcellLabels\s+(?:List<label>\s+)?(\d+)\s*\(([^()]*)\)\s*;", body, re.S)
        values = [int(value) for value in labels[2].split()] if labels else []
        local = set(values)
        if not re.search(r"\btype\s+cellZone\s*;", body) or not labels or int(labels[1]) != zones_expected[name] or len(values) != zones_expected[name] or len(local) != len(values) or any(value < 0 or value >= cell_count for value in values) or all_labels & local:
            raise CaseIssue("Both merged cell-zone inventories must be complete, disjoint and retain their exact source cell counts.")
        all_labels |= local
        verified_zones[name] = len(values)
        zone_labels[name] = local
    if all_labels != set(range(cell_count)):
        raise CaseIssue("Both exact native cell zones must cover every merged cell exactly once.")
    for name, zone in patch_zone.items():
        first, count = ranges[name]
        if any(owner[index] not in zone_labels[zone] for index in range(first, first + count)):
            raise CaseIssue("Every original merged source patch must retain ownership in its exact component cell zone.")
    rotating_labels = zone_labels["rotating-fluid"]
    if any((owner[index] in rotating_labels) != (neighbour[index] in rotating_labels) for index in range(nn)):
        raise CaseIssue("Separate merged component volumes cannot gain unproved internal faces across the fixed NCC interface.")
    numeric = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    matches = re.findall(r"Source min/average/max coverage = (" + numeric + r")/(" + numeric + r")/(" + numeric + r")\s+Target min/average/max coverage = (" + numeric + r")/(" + numeric + r")/(" + numeric + r")", log)
    if not matches:
        raise CaseIssue("Actual NCC intersection coverage is missing: rerun checkMesh after native coupling and retain its source/target coverage statistics before foamRun.")
    coverage = []
    for match in matches:
        values = [float(value) for value in match]
        for triple in (values[:3], values[3:]):
            if not all(math.isfinite(value) for value in triple) or not .99 <= triple[0] <= triple[1] <= triple[2] <= 1.01 or triple[1] < .999:
                raise CaseIssue("Actual NCC source/target coverage does not satisfy the declared minimum0.99/average0.999/maximum1.01 criteria; repair the coupled interfaces before foamRun.")
        coverage.append({"source": values[:3], "target": values[3:]})
    return {"mergedCellCount": cell_count, "mergedPointCount": np, "mergedFaceCount": nf,
            "cellZones": verified_zones, "sourcePatchFaces": patches_expected,
            "nonConformalCyclicPatches": list(coupled), "nonConformalErrorPatches": list(errors),
            "emptyDefaultFacesPlaceholders": placeholders, "nativeCoupledCoverage": coverage,
            "coverageCriteria": {"minimum": .99, "average": .999, "maximum": 1.01},
            "mergedNativeIdentityVerified": True, "physicalWallTypesVerified": True,
            "nativeGeometryQualityVerified": True, "coupledInterfaceCoverageVerified": True,
            "actualCfdRunVerified": False, "qualifiedBaseline": False, "mainApplicationWrites": False,
            "checkMeshLogSha256": sha256(log_raw).hexdigest(),
            "claimBoundary": "Actual merged topology/source identities, wall classes, checkMesh and fixed-interface coverage before any solver; no flow solution or converged performance claim."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesh-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.mesh_directory, args.output_directory), allow_nan=False))
        return 0
    except (CaseIssue, inventory.InspectionIssue, OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "CASE_PREPARATION_REQUIRED", "reasonCode": "CFD_CLASSROOM_OPENFOAM_PREPARATION_REQUIRED", "action": str(error)[:500],
                          "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
