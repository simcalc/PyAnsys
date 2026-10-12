"""Prepare one conditional active-phase reference for authored SI classroom CAD.

Current teaching-baseline evidence is independently re-reduced and copied. Only
the exact coupled mesh is transferred into a fresh liquid-initialized phase
case. No native command, pressure sweep, result or inherited phase H0 is made.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_cavitation_case as physics
import frozen_rotor_research_protocol as protocol
import teaching_pump_baseline_case as baseline_case
import reduce_teaching_pump_baseline as baseline
import teaching_pump_outlet_profile as outlet

SCHEMA = "untirta.teaching-pump-phase-reference-preparation.v1"
REPORT = "teaching-phase-preparation.json"
STATUS = "CLASSROOM_AUTHORED_ACTIVE_PHASE_REFERENCE_PREPARED_EXECUTION_REQUIRED"
MODE = "high-pressure-active-phase-MRF-reference"
ROUTE = "CLASSROOM_AUTHORED_GEOMETRY"
PRESSURE_PA = 300000.0
PERIOD_S = 60 / 1450
MAX_DT_S, DEFAULT_DURATION_S = PERIOD_S / 360, 4 * PERIOD_S
MAX_SOURCE_RECORDS, MAX_FILE_BYTES, MAX_REPORT_BYTES = 320, 128 * 1024**2, 128 * 1024
MAX_WORKSPACE_BYTES = 1024**3
MAX_NATIVE_CACHE_BYTES = 8 * 1024**2
NATIVE_CACHE_PATH = "case/constant/fvMesh/polyFaces"
NATIVE_CACHE_SOURCE = "https://github.com/OpenFOAM/OpenFOAM-12/blob/86e126a7bc4dbfaed949bbc58c9f0fdd67acc059/src/finiteVolume/fvMesh/fvMesh.C"
STARTUP_FIELDS = {"totalPressurePa": {"dimensions": "1 -1 -2 0 0 0 0", "function": "totalPressurePa", "type": "pressure", "field": "p"},
    "U2": {"dimensions": "0 2 -2 0 0 0 0", "function": "absoluteVelocitySquared", "type": "magSqr", "field": "U"},
    "yPlus": {"dimensions": "0 0 0 0 0 0 0", "function": "wallYPlus", "type": "yPlus", "field": None}}
STARTUP_FIELD_SOURCE = "https://github.com/OpenFOAM/OpenFOAM-12/blob/86e126a7bc4dbfaed949bbc58c9f0fdd67acc059/src/functionObjects/field/fieldExpression/fieldExpression.C"
MESH_FILES = baseline.MESH_FILES
MODEL_FILES = {"constant/fvModels", "constant/phaseProperties", "constant/physicalProperties.water", "constant/physicalProperties.vapour"}
NCC_PATCHES = {"pumpInterface_on_interface-impeller-outlet": "nonConformalCyclic",
    "pumpInterface_on_interface-volute-inlet": "nonConformalCyclic",
    "nonConformalError_on_interface-impeller-outlet": "nonConformalError",
    "nonConformalError_on_interface-volute-inlet": "nonConformalError"}
FALSE_FLAGS = ("commandExecutionPerformedByThisTool", "nativeCaseGenerated", "simulationExecuted", "actualCfdRunVerified",
    "cavitationModelExecuted", "numericalSolverAcceptanceVerified", "qualifiedBaseline", "phaseObservationQualified",
    "numericalPhaseReferenceQualified", "phaseFamilyReferenceEligible", "phaseFamilyReferenceQualified", "phaseH0Eligible",
    "cavitationSweepExecuted", "pressureSweepEnabled", "slidingEnabled", "physicalTransientExecuted", "npshrCalculated",
    "npsh3Qualified", "npsh3Verified", "restartVerified", "meshConvergenceVerified", "timeStepConvergenceVerified",
    "wallResolutionVerified", "physicalValidationVerified", "manufacturerPublishedGeometry", "originalFluentGeometryReproduced",
    "baselineFieldsCopied", "baselineHeadUsedAsPhaseH0", "mainApplicationWrites")
SOURCE_TRUE_FLAGS = ("sourceNumericalBaselineQualified", "sourcePreliminaryBaselineQualified", "sourceActualCfdRunVerified",
    "sourceNativeQualityVerified", "sourceNativeCoupledCoverageVerified", "sourceInputsImmutable")
LIMITS = {"maximumSourceRecords": MAX_SOURCE_RECORDS, "maximumFileBytes": MAX_FILE_BYTES,
    "maximumReportBytes": MAX_REPORT_BYTES, "maximumWorkspaceBytes": MAX_WORKSPACE_BYTES,
    "plannedNativeCpus": 1, "plannedNativeMemoryBytes": 4 * 1024**3, "plannedNativeMaximumSeconds": 1200,
    "maximumNativeLogBytes": 8 * 1024**2,
    "nativeExecutionEnforcementOwner": "A separately reviewed independent FREE workflow; this helper executes no commands."}
ABSOLUTE_PRESSURE_MAPPING = {"nativeField": "p_rgh", "nativeUnit": "Pa", "nativeGravityMS2": [0, 0, 0],
    "equation": "p_abs=p_rgh+rho*gh; authored g=0 gives p_abs=p_rgh, with no atmospheric addition.",
    "referenceAdditionPa": 0, "boundary": "prghTotalPressure p0=300000 Pa absolute inlet total pressure"}
TEMPORAL_PLAN = {"maximumTimeStepS": MAX_DT_S, "endTimeS": DEFAULT_DURATION_S, "rotationPeriodS": PERIOD_S,
    "stationaryMrfRelaxation": True, "physicalRotorMotion": False, "nativeCoLimit": .5, "nativeAlphaCoLimit": .25,
    "durationIsAcceptanceProof": False, "residenceTimeAdequacyVerified": False, "timeStepConvergenceVerified": False}
CLAIM = ("One fresh uniform-liquid active SchnerrSauer fixed-MRF reference on the exact current authored SI classroom mesh. "
    "Single-phase numerical acceptance is a prerequisite, never inherited phase acceptance or active-phase H0. "
    "Four revolutions is a declared trial, not proven residence time or stationarity. "
    "No pressure sweep, physical sliding, restart, NPSHr, manufacturer performance or refinement qualification is supplied.")
encoded, read, json_file = baseline.encoded, baseline_case.record, baseline_case.json_file
base, safety, geometry = baseline_case.base, baseline_case.safety, baseline_case.geometry


class TeachingPhaseIssue(ValueError):
    pass


def require(value, action):
    if not value:
        raise TeachingPhaseIssue(action)


def records(root, rows):
    """Re-read exact unique regular relative files, including actual byte bounds."""
    require(isinstance(rows, list) and 0 < len(rows) <= MAX_SOURCE_RECORDS, "Require a complete bounded source inventory.")
    result, seen, total = [], set(), 0
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}
            and isinstance(row["path"], str) and row["path"] not in seen
            and type(row["bytes"]) is int and 0 < row["bytes"] <= MAX_FILE_BYTES and protocol.digest(row["sha256"]),
            "Source records need unique relative roles, positive bounded bytes and SHA256.")
        raw, actual = read(root, row["path"], limit=MAX_REPORT_BYTES if row["path"].endswith(".json") else MAX_FILE_BYTES)
        require(actual == row, "Source evidence bytes changed.")
        total += len(raw); require(total <= MAX_WORKSPACE_BYTES, "Source evidence exceeds one GiB.")
        result.append(actual); seen.add(row["path"])
    return sorted(result, key=lambda row: row["path"])


def source_inventory(directory):
    root = base.regular_directory(directory); result, total = [], 0
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)()
            and (path.is_file() or path.is_dir()), "Source evidence must have ordinary paths without links.")
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        require(len(result) < MAX_SOURCE_RECORDS and len(path.relative_to(root).parts) <= 10, "Bound the complete source inventory and nesting.")
        raw, row = read(root, relative, limit=MAX_REPORT_BYTES if relative.endswith(".json") else MAX_FILE_BYTES)
        result.append(row); total += len(raw)
        require(total <= MAX_WORKSPACE_BYTES, "Actual source files exceed one GiB.")
    require(result, "Complete current baseline files are required.")
    return result


def verify_baseline(directory):
    """Re-run teaching admission/math; old schemas and source flags cannot qualify."""
    root = safety.scope_path(directory)
    packet, packet_row = json_file(root, baseline.REPORT_FILE)
    require(packet.get("schemaVersion") == baseline.SCHEMA and packet.get("sourceGeometryRoute") == ROUTE,
        "Admit only the distinct authored SI teaching-baseline acceptance schema.")
    current = baseline.reduce_baseline(root)
    require(current == packet and packet.get("status") == "AuthoredNumericalBaselineQualified"
        and all(packet.get(k) is True for k in ("actualCfdRunVerified", "simulationExecuted", "numericalBaselineQualified",
            "preliminaryBaselineQualified", "noncavitatingBaselineVerified", "interfaceCoverageVerified"))
        and all(packet.get(k) is False for k in ("qualifiedBaseline", "physicalValidationVerified", "wallResolutionVerified",
            "meshConvergenceVerified", "timeStepConvergenceVerified", "cavitationSweepExecuted", "npsh3Qualified",
            "physicalTransientExecuted", "restartVerified", "manufacturerPublishedGeometry", "mainApplicationWrites"))
        and packet.get("diagnostics") == [], "Current numerical single-phase acceptance is required; broader claims remain false.")
    study, observation = packet["study"], packet["nativeObservation"]
    parameters, _ = json_file(root, "source-teaching/source-geometry/teaching-input-manifest.json")
    require(study == baseline.study_packet(parameters["parameters"], study["controls"])
        and observation.get("studyInputs") == study["input"] and observation.get("physicalBasis") == study["physicalBasis"]
        and observation.get("physicalModel") == "steady-single-phase-MRF" and observation.get("pressureLevelAbsolutePa") == PRESSURE_PA
        and observation.get("pressureMapping") == {"nativeField": "p", "nativeUnit": "m2/s2", "absolutePressureReferencePa": PRESSURE_PA,
            "absoluteReconstructionEquation": "p_static_absolute_Pa = rho_liquid*p_kinematic + explicit_absolute_reference_Pa"},
        "Retain exact authored SI/fluid/rotation/datum declarations and the source kinematic pressure mapping.")
    reduced = protocol.reduce_run(study, observation)
    require(reduced == packet["protocolReduction"] and reduced["status"] == "QualifiedSuppliedObservation"
        and reduced["stages"]["head"]["result"]["value"] > 0
        and observation["measurements"]["minimumStaticPressureAbsolutePa"] > study["input"]["vaporPressurePa"],
        "Recomputed source head, liquid pressure and numerical acceptance are independently required.")
    manifest, manifest_row = json_file(root, baseline.MANIFEST_FILE)
    require(manifest == packet["manifest"] == packet["inputManifest"] and manifest.get("schemaVersion") == baseline.MANIFEST_SCHEMA
        and manifest.get("sourceGeometryRoute") == ROUTE and manifest["controls"] == study["controls"] == observation["controls"]
        and manifest_row["sha256"] == packet["nativeInputManifestSha256"] == observation["controlledVariationProof"]["nativeInputManifestSha256"],
        "Actual persisted source manifest, observation and input SHA must agree.")
    manifest_rows = records(root, [{k: row[k] for k in ("path", "bytes", "sha256")} for row in manifest["files"]])
    for control in protocol.CONTROL_KEYS - {"cavitationModelSha256"}:
        selected = sorted([{k: row[k] for k in ("path", "bytes", "sha256")}
            for row in manifest["files"] if row.get("control") == control], key=lambda row: row["path"])
        require(selected and sha256(encoded(selected)).hexdigest() == study["controls"][control], "Actual baseline control bytes changed.")
    require(study["controls"]["cavitationModelSha256"] is None, "The source prerequisite is strictly single-phase.")
    evidence = records(root, packet["nativeEvidenceRecords"])
    require(sha256(encoded(evidence)).hexdigest() == observation["nativeEvidence"]["sha256"], "Aggregate native baseline evidence changed.")
    identity, identity_row = json_file(root, baseline.GEOMETRY_FILE)
    require(identity == baseline.authored_identity(root) and identity.get("manufacturerIdentity") is None
        and identity.get("originalFluentSourceIdentity") is None
        and sha256(encoded([identity_row])).hexdigest() == study["controls"]["geometrySha256"],
        "Preserve the distinct canonical authored SI identity without manufacturer or old-source inheritance.")
    preparation, _ = json_file(root, baseline.PREPARATION_FILE)
    outlet_proof = outlet.verify_outlet(root,
        geometry_report_relative="source-teaching/source-geometry/teaching-geometry.json",
        mesh_relative="source-teaching/component-mesh-snapshots/volute/constant/polyMesh")
    require(preparation.get("outletProfileEvidence") == outlet_proof,
        "Re-read the actual authored discharge rectangle and copied native mesh before preserving its declared uniform outlet profile.")
    components = preparation["components"]
    require([c["component"] for c in components] == ["impeller", "volute"], "Both ordered actual source components are required.")
    mesh = base.verify_merged_mesh(root / "impeller-case", components, root / "logs/02-checkMesh.log")
    mesh_rows = [read(root, "impeller-case/constant/polyMesh/" + name)[1] for name in MESH_FILES]
    require(sorted(mesh_rows, key=lambda row: row["path"]) == [row for row in manifest_rows
        if row["path"].startswith("impeller-case/constant/polyMesh/")], "Transfer exactly the six source-bound coupled mesh primitives.")
    patches = geometry.boundary_entries(root / "impeller-case/constant/polyMesh/boundary")
    default = patches.get("defaultFaces")
    require(default is None or default["type"] == "patch" and default["nFaces"] == 0, "Only an actually present zero-face defaultFaces receives a placeholder BC.")
    return {"directory": root, "report": packet, "reportFile": packet_row, "packet": packet, "study": study,
        "manifest": manifest, "manifestFile": manifest_row, "geometryIdentity": identity, "geometryFile": identity_row,
        "sourceRecords": source_inventory(root), "meshRecords": mesh_rows, "components": components,
        "coupledMeshEvidence": mesh, "defaultFacesPresent": default is not None,
        "outletProfileEvidence": outlet_proof}


validate_source = verify_baseline


def native_recipe(study, *, default_faces=False):
    """Reuse pure reviewed physics, not an old geometry/source constructor."""
    require(type(default_faces) is bool and isinstance(study, dict), "Declare actual empty placeholder presence.")
    inputs = {**baseline_case.PARAMETERS, "suctionDatumM": 0.0, "initialAngleRad": 0.0}
    expected = baseline.study_packet({}, study.get("controls", {}))
    require(study.get("input") == inputs and study.get("physicalBasis") == expected["physicalBasis"]
        and study.get("sourceReference") == expected["sourceReference"], "Use only the exact authored SI classroom physical family.")
    authored = physics.native_recipe(study, PRESSURE_PA, "steady-sweep", MAX_DT_S, DEFAULT_DURATION_S)
    velocity = authored["0/U"]
    _, original_patches = baseline_case.fields.boundary_blocks(velocity.encode("ascii"), "U")
    require(original_patches[outlet.PROFILE["patch"]]["type"] == "flowRateOutletVelocity",
        "Begin with the exact reviewed outlet before declaring its controlled uniform-normal replacement.")
    velocity, replaced = re.subn(r'("mass-flow-inlet-11"\s*\{)[^{}]*(\})',
        lambda match: match[1] + "\n        " + outlet.uniform_outlet_body().decode("ascii") + "\n    " + match[2], velocity)
    require(replaced == 1, "Replace exactly one physical outlet velocity block; retain suction pressure, flow, rotor and other patch inputs.")
    authored["0/U"] = velocity
    require(not re.search(r"\bwallDist\s*\{", authored["system/fvSchemes"]), "The reviewed phase recipe must define wallDist exactly once.")
    authored["system/fvSchemes"] += "wallDist {method meshWave;}\n"
    authored["constant/MRFProperties"] = (base.foam_header("MRFProperties", location="constant")
        + "rotor {cellZone rotating-fluid; origin (0 0 0); axis (0 0 1); omega 1450 [rpm];}\n")
    initial = {"U": "(0 0 0)", "p_rgh": "300000", "alpha.water": "1", "k": "0.375", "omega": "100", "nut": "0"}
    physical_names = set().union(*base.PATCHES.values())
    for name, value in initial.items():
        path = "0/" + name; text = authored[path]
        if not default_faces:
            text, count = re.subn(r'    "defaultFaces"\s*\{[^{}]*\}\s*', "", text)
            require(count == 1, "Omit only the absent empty defaultFaces block.")
        constraint = "".join('    "' + patch + '"\n    {\n        type ' + kind + '; value uniform ' + value + ';\n    }\n'
            for patch, kind in sorted(NCC_PATCHES.items()))
        require(text.endswith("}\n"), "Use the complete reviewed native boundary field writer.")
        text = text[:-2] + constraint + "}\n"
        _, blocks = baseline_case.fields.boundary_blocks(text.encode("ascii"), name)
        require(set(blocks) == physical_names | set(NCC_PATCHES) | ({"defaultFaces"} if default_faces else set()),
            "Every source physical and NCC patch receives its exact new phase BC.")
        authored[path] = text
    require("0/p" not in authored and "constant/dynamicMeshDict" not in authored, "Do not import baseline gauge p or enable sliding in this fixed reference.")
    return {path: raw.encode("ascii") for path, raw in authored.items()}


def commands(directory):
    case = str(Path(directory).absolute() / "case")
    return [["checkMesh", "-case", case, "-allGeometry", "-allTopology", "-writeSets"], ["foamRun", "-case", case]]


def historical_commands(command_root):
    """Only lexical argv reconstruction; no historical path reads or execution."""
    case = str(geometry.historical_command_root(command_root) / "case")
    return [["checkMesh", "-case", case, "-allGeometry", "-allTopology", "-writeSets"], ["foamRun", "-case", case]]


def historical_command_root(report):
    rows = report.get("nativeCommandArguments")
    require(isinstance(rows, list) and len(rows) == 2 and isinstance(rows[0], list) and len(rows[0]) == 6
        and rows[0][:2] == ["checkMesh", "-case"], "Retain the complete two-command reference provenance.")
    case = geometry.historical_command_root(rows[0][2]); require(case.name == "case", "Record the exact case/ target.")
    root = str(case.parent)
    require(rows == historical_commands(root), "Both saved native commands must share one reviewed historical root.")
    return root


def declared_report(source, output):
    authored = native_recipe(source["study"], default_faces=source["defaultFacesPresent"])
    rows = [{"path": name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()} for name, raw in sorted(authored.items())]
    model_rows = [row for row in rows if row["path"] in MODEL_FILES]
    copied = [{**row, "path": "source-baseline/" + row["path"]} for row in source["sourceRecords"]]
    report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(), "status": STATUS, "mode": MODE,
        "sourceGeometryRoute": ROUTE, "coordinateUnits": "SI_METRES", "foundationSourceCommit": safety.SOURCE_COMMIT,
        "sourceEvidenceDirectory": "source-baseline", "sourceEvidenceFileRecords": copied,
        "sourceBaselineAcceptanceFile": {**source["reportFile"], "path": "source-baseline/" + baseline.REPORT_FILE},
        "sourceCaseManifestSha256": source["manifestFile"]["sha256"],
        "sourceBaselineEvidenceSha256": source["report"]["nativeObservation"]["nativeEvidence"]["sha256"],
        "sourceGeometryIdentityFile": source["geometryFile"], "sourceGeometryControlSha256": source["study"]["controls"]["geometrySha256"],
        "sourceNativeMeshFiles": source["meshRecords"], "preservedNativeMeshFiles": [{**row, "path": row["path"].removeprefix("impeller-case/")} for row in source["meshRecords"]],
        "outletProfileEvidence": outlet.prefix_evidence(source["outletProfileEvidence"], "source-baseline"),
        "components": source["components"], "sourceCoupledMeshEvidence": source["coupledMeshEvidence"],
        "defaultFacesPresent": source["defaultFacesPresent"], "authoredInputFiles": rows,
        "authoredCavitationModelSha256": sha256(encoded(model_rows)).hexdigest(),
        "declaredStudyInputs": source["study"]["input"], "physicalBasis": source["study"]["physicalBasis"],
        "sourceReference": source["study"]["sourceReference"], "inletAbsoluteTotalPressurePa": PRESSURE_PA,
        "absolutePressureMapping": ABSOLUTE_PRESSURE_MAPPING, "modelAssumptions": physics.MODEL_ASSUMPTIONS,
        "temporalPlan": TEMPORAL_PLAN,
        "initialization": "New uniform alpha.water=1, zero U and absolute p_rgh=300000 Pa at t=0; baseline result fields are retained only as provenance, never as phase state or restart.",
        "sourceBaselineRole": "Numerically accepted single-phase preparation prerequisite only; its head is never active-phase H0.",
        "requiredAcceptance": ["Actual SchnerrSauer selection, complete native clock/sampler histories and finite bounded phase end fields",
            "Unchanged full native geometry/topology quality, NCC coverage and signed mass/interface conservation",
            "Final-window residual/head/flow acceptance and global alpha_v<=1e-4 with pmin>pv and positive active head before H0",
            "No sweep/sliding/restart/refinement/manufacturer qualification follows from this preparation"],
        "nativeCommandArguments": commands(output), "limits": LIMITS, "claimBoundary": CLAIM,
        **{key: True for key in SOURCE_TRUE_FLAGS}, **{key: False for key in FALSE_FLAGS}}
    return report, authored


def native_generated_data(root, *, after_native_execution=False):
    """Validate only the pinned writer's data cache, never an authored control.

fvMeshStitcher::connectThis(load=true) resets the instance to pointsInstance;
setPolyFacesBfInstance enables AUTO_WRITE. On this static NCC mesh that instance
is constant. fvMesh::writeObject writes dimensionless surfaceLabelField data.
All actual labels must belong to this NCC patch's originalPatch face range.
"""
    require(type(after_native_execution) is bool, "Declare the native data verification stage explicitly.")
    directory = Path(root) / "case/constant/fvMesh"
    rows, evidence = native_startup_fields(root, after_native_execution=after_native_execution)
    if not directory.exists():
        require(not directory.is_symlink(), "Native cache paths cannot be links.")
        return rows, evidence
    require(after_native_execution, "No unbound native cache is admitted before this phase execution.")
    base.regular_directory(directory)
    require({path.name for path in directory.iterdir()} == {"polyFaces"}, "Only the exact native polyFaces data cache is supported.")
    raw, row = read(root, NATIVE_CACHE_PATH, limit=MAX_NATIVE_CACHE_BYTES)
    return [row, *rows], [native_polyfaces(raw, Path(root) / "case/constant/polyMesh"), *evidence]


def native_startup_fields(root, *, after_native_execution=False):
    """Optional initial function-object data, never initial solver state or H0.

The pinned functionObjectList::start executes/writes executeAtStart functions;
fieldExpression/yPlus writers can produce these exact scalar results at time0.
Before native execution none of them may be supplied as a prefilled input.
"""
    require(type(after_native_execution) is bool, "Declare the native data verification stage explicitly.")
    folder = Path(root) / "case/0"
    found = [name for name in sorted(STARTUP_FIELDS) if (folder / name).exists() or (folder / name).is_symlink()]
    if not found: return [], []
    require(after_native_execution, "Derived startup results cannot be prefilled before this phase execution.")
    owner_count, owners = base.native_list(Path(root) / "case/constant/polyMesh/owner")
    neighbour_count, neighbours = base.native_list(Path(root) / "case/constant/polyMesh/neighbour")
    tokens = owners.split() + neighbours.split()
    require(len(tokens) == owner_count + neighbour_count and tokens and all(re.fullmatch(r"\d+", v) for v in tokens),
        "Startup fields bind to complete actual native owner/neighbour cell labels.")
    cells = max(map(int, tokens)) + 1
    require(0 < cells <= 500_000, "Startup scalar data must bind the bounded authored teaching mesh.")
    patches = geometry.boundary_entries(Path(root) / "case/constant/polyMesh/boundary")
    ncc_counts = None
    cache = Path(root) / NATIVE_CACHE_PATH
    if cache.exists() or cache.is_symlink():
        cache_raw, _ = read(root, NATIVE_CACHE_PATH, limit=MAX_NATIVE_CACHE_BYTES)
        addressing = native_polyfaces(cache_raw, Path(root) / "case/constant/polyMesh")
        ncc_counts = {p["patch"]: p["nativeFaceCount"] for p in addressing["nonconformalPatchMappings"]}
    control, control_row = read(root, "case/system/controlDict")
    control = control.decode("ascii")
    rows, evidence = [], []
    for name in found:
        raw, row = read(root, "case/0/" + name)
        proof = startup_scalar_field(raw, name, cells, patches, ncc_counts=ncc_counts)
        role = STARTUP_FIELDS[name]
        blocks = re.findall(r"(?m)^" + re.escape(role["function"]) + r"\s*\{([^{}]*)\}", control)
        require(len(blocks) == 1 and re.findall(r"\btype\s+(\w+)\s*;", blocks[0]) == [role["type"]],
            "Each generated startup result must bind its exact authored function object.")
        if role["field"] is not None:
            require(re.findall(r"\bfield\s+(\w+)\s*;", blocks[0]) == [role["field"]]
                and re.findall(r"\bresult\s+(\w+)\s*;", blocks[0]) == [name], "Generated startup field/result identities must match.")
        else:
            require(not re.search(r"\b(?:phase|result)\s+", blocks[0]), "This exact yPlus writer has no renamed result or phase suffix.")
        if name == "totalPressurePa":
            require(re.findall(r"\bcalcTotal\s+(\w+)\s*;", blocks[0]) == ["yes"]
                and re.findall(r"\bpRef\s+([^;]+);", blocks[0]) == ["0"]
                and re.findall(r"\brho\s+(\w+)\s*;", blocks[0]) == ["rho"], "Active-phase total pressure must retain absolute-Pa/no-offset mixture-density recipe.")
        proof.update(path=row["path"], nativeRole="derived-startup-volScalarField", dataOnlyVerified=True, authoredInput=False,
            sourceFunctionObject=role["function"], sourceFunctionType=role["type"], resultField=name,
            sourceControlFile=control_row, numericalAcceptanceInherited=False, sourceUrl=STARTUP_FIELD_SOURCE if name != "yPlus" else
                STARTUP_FIELD_SOURCE.replace("fieldExpression/fieldExpression.C", "yPlus/yPlus.C"))
        rows.append(row); evidence.append(proof)
    return rows, evidence


def startup_scalar_field(raw, name, cells, patches, *, ncc_counts=None):
    """Check every stored scalar and flat native patch value, without executing it."""
    require(name in STARTUP_FIELDS and isinstance(raw, bytes) and 0 < len(raw) <= MAX_FILE_BYTES,
        "Only exact bounded native startup result roles are allowed.")
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw.decode("ascii"), flags=re.S)
    dims = STARTUP_FIELDS[name]["dimensions"].split()
    dim_pattern = r"\s+".join(dims)
    if name == "yPlus": dim_pattern = "(?:" + dim_pattern + ")?"
    match = re.fullmatch(r"\s*FoamFile\s*\{([^{}]*)\}\s*dimensions\s*\[\s*" + dim_pattern
        + r"\s*\]\s*;\s*internalField\s+(.+?)\s*;\s*boundaryField\s*\{(.*)\}\s*", text, re.S)
    require(match, "Generated startup data needs one complete scalar field and exact SI dimensions.")
    pairs = re.findall(r"([A-Za-z][A-Za-z0-9_]*)\s+([^;{}]+);", match[1]); header = dict(pairs)
    require(len(header) == len(pairs) and not re.sub(r"[A-Za-z][A-Za-z0-9_]*\s+[^;{}]+;", "", match[1]).strip()
        and set(header) in ({"format", "class", "location", "object"}, {"version", "format", "class", "location", "object"})
        and {k:v.strip() for k,v in header.items() if k != "version"} == {
            "format":"ascii", "class":"volScalarField", "location":'"0"', "object":name}
        and ("version" not in header or header["version"].strip() == "2.0"), "Generated startup class/object/time/ASCII data header must match exactly.")
    def values(source, expected=None):
        value = re.fullmatch(r"\s*(?:uniform\s+(" + baseline.native.FLOAT + r")|nonuniform\s+List<scalar>\s+(\d+)\s*\(([^()]*)\))\s*", source, re.S)
        require(value, "Use only finite plain scalar native data, not executable field dictionaries.")
        tokens = [value[1]] if value[1] is not None else value[3].split()
        require(value[1] is not None or len(tokens) == int(value[2]) and (expected is None or int(value[2]) == expected),
            "Every declared native scalar count must match its full field shape.")
        require(len(tokens) <= 1_000_000 and all(re.fullmatch(baseline.native.FLOAT, token) and math.isfinite(float(token)) for token in tokens),
            "All actual generated startup scalar values must be finite and bounded.")
        if name in {"U2", "yPlus"}: require(all(float(token) >= 0 for token in tokens), "Native squared speed and yPlus data cannot be negative.")
        return [float(token) for token in tokens]
    internal = values(match[2], cells); require(internal, "Every actual native cell needs a represented internal scalar value.")
    blocks = list(re.finditer(r'"?([A-Za-z0-9_-]+)"?\s*\{([^{}]*)\}', match[3]))
    require(len(blocks) == len(patches) and len({b[1] for b in blocks}) == len(blocks) and {b[1] for b in blocks} == set(patches)
        and not re.sub(r'"?[A-Za-z0-9_-]+"?\s*\{[^{}]*\}', "", match[3]).strip(), "Generated data must cover every exact actual native patch once.")
    count = 0
    allowed = {"calculated", "extrapolatedCalculated", "zeroGradient", "fixedValue", "nonConformalCyclic", "nonConformalError", "nonConformalCalculated"}
    for block in blocks:
        body = block[2]; kind = re.findall(r"\btype\s+(\w+)\s*;", body)
        require(len(kind) == 1 and kind[0] in allowed, "Generated startup data permits only known non-executable scalar patch roles.")
        if kind[0] in {"nonConformalCyclic", "nonConformalError", "nonConformalCalculated"}:
            require(block[1] in NCC_PATCHES and (kind[0] == "nonConformalCalculated" or kind[0] == patches[block[1]]["type"]),
                "Native scalar constraints must match the actual NCC patch role.")
        rest = re.sub(r"\btype\s+\w+\s*;", "", body)
        override = re.findall(r"\bpatchType\s+(\w+)\s*;", rest)
        require(not override or override == [patches[block[1]]["type"]], "Any native constraint override must match this actual mesh patch.")
        rest = re.sub(r"\bpatchType\s+\w+\s*;", "", rest)
        if not rest.strip():
            # nonConformalError inherits zeroGradient; nonConformalCyclic
            # inherits cyclic. Their pinned writers can store only the type.
            require(kind[0] in {"zeroGradient", "nonConformalCyclic", "nonConformalError"},
                "Calculated/generated value fields must store their scalar data.")
            continue
        value = re.fullmatch(r"\s*value\s+(.+?)\s*;\s*", rest, re.S); require(value, "Generated patch dictionaries may contain only their plain stored scalar value.")
        expected = patches[block[1]]["nFaces"]
        if block[1] in NCC_PATCHES:
            expected = None
            if re.match(r"\s*nonuniform\b", value[1]):
                require(isinstance(ncc_counts, dict) and block[1] in ncc_counts,
                    "Nonuniform NCC startup data needs the actual native polyFaces addressing count.")
                expected = ncc_counts[block[1]]
        count += len(values(value[1], expected))
    return {"dimensions": " ".join(dims), "allInternalCellsRepresented": cells, "storedInternalScalarCount": len(internal),
        "internalMinimum": min(internal), "internalMaximum": max(internal), "nativePatchCount": len(blocks),
        "storedBoundaryScalarCount": count, "allStoredValuesFinite": True, "allNativePatchRolesChecked": True}


def native_polyfaces(raw, mesh_directory):
    """Read-only data parser; no source admission, geometry or coverage claim."""
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_NATIVE_CACHE_BYTES, "Bind bounded actual native cache bytes.")
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw.decode("ascii"), flags=re.S)
    match = re.fullmatch(r"\s*FoamFile\s*\{([^{}]*)\}\s*dimensions\s*\[\s*(?:0\s+0\s+0\s+0\s+0\s+0\s+0\s*)?\]\s*;\s*"
        r"internalField\s+uniform\s+-1\s*;\s*boundaryField\s*\{(.*)\}\s*", text, re.S)
    require(match, "A plain complete dimensionless native label-field cache with internal uniform -1 is required.")
    pairs = re.findall(r"([A-Za-z][A-Za-z0-9_]*)\s+([^;{}]+);", match[1])
    header = dict(pairs)
    require(len(header) == len(pairs) and not re.sub(r"[A-Za-z][A-Za-z0-9_]*\s+[^;{}]+;", "", match[1]).strip()
        and set(header) in ({"format", "class", "location", "object"}, {"version", "format", "class", "location", "object"})
        and {key: value.strip() for key, value in header.items() if key != "version"} == {
            "format": "ascii", "class": "surfaceLabelField", "location": '"constant/fvMesh"', "object": "polyFaces"}
        and ("version" not in header or header["version"].strip() == "2.0"), "The exact native data header/role/location must match; executable metadata is prohibited.")
    blocks = list(re.finditer(r'"?([A-Za-z0-9_-]+)"?\s*\{([^{}]*)\}', match[2]))
    require(blocks and not re.sub(r'"?[A-Za-z0-9_-]+"?\s*\{[^{}]*\}', "", match[2]).strip(), "Parse every complete flat native cache patch block.")
    mesh = base.regular_directory(mesh_directory)
    patches = geometry.boundary_entries(mesh / "boundary")
    require(len(blocks) == len(patches) and len({block[1] for block in blocks}) == len(blocks)
        and {block[1] for block in blocks} == set(patches), "Every actual native patch must have exactly one cache addressing role.")
    face_count, _ = base.native_list(mesh / "owner"); internal_count, _ = base.native_list(mesh / "neighbour")
    require(0 <= internal_count < face_count, "Bind cache addressing to the actual original face domain.")
    checked, total, coupled_counts = [], 0, []
    for block in blocks:
        name, body = block[1], block[2]
        if name not in NCC_PATCHES:
            require(re.fullmatch(r"\s*type\s+polyFaces\s*;\s*", body), "Conformal patch cache data is implicit, with no additional payload.")
            continue
        kind = NCC_PATCHES[name]
        value = re.fullmatch(r"\s*type\s+nonConformalPolyFaces\s*;\s*patchType\s+" + kind
            + r"\s*;\s*size\s+(\d+)\s*;\s*value\s+(?:uniform\s+(\d+)|nonuniform\s+List<label>\s+(\d+)\s*\(([^()]*)\))\s*;\s*", body, re.S)
        require(value and patches[name]["type"] == kind, "Only the exact native NCC label patch role is supported.")
        count = int(value[1]); require(count <= 1_000_000, "Bound each native NCC addressing count.")
        original = name.split("_on_", 1)[1]
        require(original in patches and re.search(r"\boriginalPatch\s+" + re.escape(original) + r"\s*;", patches[name]["dictionary"]),
            "Every NCC cache must bind its exact originalPatch.")
        first, size = patches[original]["startFace"], patches[original]["nFaces"]
        require(internal_count <= first < first + size <= face_count, "Original interface ranges must remain within the actual boundary face domain.")
        if value[2] is not None:
            require(count > 0, "An empty native label field must have an explicit empty list.")
            tokens = [value[2]]
        else:
            tokens = value[4].split()
            require(int(value[3]) == count == len(tokens), "Native label-list and size entries must cover every actual cache mapping.")
        require(all(re.fullmatch(r"\d+", token) and first <= int(token) < first + size for token in tokens),
            "Every native NCC label must address a face of this exact originalPatch, not another patch.")
        total += count; require(total <= 2_000_000, "Bound the aggregate native label mapping.")
        if kind == "nonConformalCyclic": coupled_counts.append(count)
        checked.append({"patch": name, "patchType": kind, "originalPatch": original, "originalFaceRange": [first, first + size],
            "nativeFaceCount": count, "allMappingLabelsChecked": True})
    require(len(coupled_counts) == 2 and coupled_counts[0] == coupled_counts[1] > 0, "The static reciprocal NCC pair needs matched positive native addressing counts.")
    proof = {"path": NATIVE_CACHE_PATH, "nativeRole": "surfaceLabelField/polyFaces", "dataOnlyVerified": True,
        "authoredInput": False, "nativePatchCount": len(blocks), "nativeIntersectionFaceCount": total,
        "internalLabelSentinel": -1, "allNativePatchRolesChecked": True, "nonconformalPatchMappings": checked,
        "geometryReconstructed": False, "sourceUrl": NATIVE_CACHE_SOURCE}
    return proof


def verify_case_input_roles(root, authored, *, after_native_execution=False):
    """Reject additional initial fields or executable native dictionaries.

Positive-time output, postProcessing and checkMesh polyMesh/sets diagnostics
remain separate. No extra constant/system recipe can alter this authored model.
"""
    case = Path(root) / "case"
    for folder in ("0", "system", "constant"):
        actual = base.regular_directory(case / folder)
        expected = {Path(path).name for path in authored if path.startswith(folder + "/")}
        if folder == "0" and after_native_execution: expected |= {name for name in STARTUP_FIELDS if (actual / name).exists()}
        if folder == "constant":
            expected.add("polyMesh")
            if after_native_execution and (case / "constant/fvMesh").exists(): expected.add("fvMesh")
        require({path.name for path in actual.iterdir()} == expected, "Unreviewed initial field or native dictionary role is prohibited.")
        for path in actual.iterdir():
            require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)()
                and (path.is_dir() if folder == "constant" and path.name in {"polyMesh", "fvMesh"} else path.is_file()),
                "Use only the exact plain authored native input roles.")
    return native_generated_data(root, after_native_execution=after_native_execution)


def verify_output(directory, *, after_native_execution=False):
    require(type(after_native_execution) is bool, "Declare the native data verification stage explicitly.")
    root = safety.scope_path(directory); report, report_row = json_file(root, REPORT)
    require(report.get("schemaVersion") == SCHEMA and report.get("status") == STATUS, "Use only the distinct teaching active-phase preparation schema.")
    timestamp = report.get("createdAt")
    require(isinstance(timestamp, str) and len(timestamp) <= 40, "Retain bounded UTC provenance.")
    try:
        require(datetime.fromisoformat(timestamp).utcoffset().total_seconds() == 0, "Retain explicit UTC provenance.")
    except (ValueError, AttributeError) as error:
        raise TeachingPhaseIssue("Retain explicit UTC provenance.") from error
    source = verify_baseline(root / "source-baseline"); expected, authored = declared_report(source, root)
    expected["createdAt"] = report["createdAt"]; expected["nativeCommandArguments"] = historical_commands(historical_command_root(report))
    require(report == expected, "Current copied source, mesh, SI/model/pressure/time controls and false qualification flags must replay exactly.")
    records(root, report["sourceEvidenceFileRecords"])
    for row in source["meshRecords"]:
        target = "case/" + row["path"].removeprefix("impeller-case/")
        require(read(root, target)[1] == {**row, "path": target}, "The exact six coupled native mesh bytes changed.")
    require(read(root, baseline.GEOMETRY_FILE)[1] == source["geometryFile"], "Canonical authored SI geometry identity changed.")
    native_rows, native_evidence = verify_case_input_roles(root, authored, after_native_execution=after_native_execution)
    for path, raw in authored.items():
        require(read(root, "case/" + path)[0] == raw, "Authored active-phase initial fields or model controls changed.")
    require(not (root / "case/0/p").exists() and not (root / "case/constant/dynamicMeshDict").exists(), "Do not import baseline pressure or enable sliding.")
    return {"report": report, "reportFile": report_row, "sourceBaseline": source, "components": source["components"],
        "nativeGeneratedDataRecords": native_rows, "nativeGeneratedDataEvidence": native_evidence}


def prepare(baseline_directory, output_directory):
    source = verify_baseline(baseline_directory); output = safety.scope_path(output_directory, fresh=True)
    require(not output.resolve().is_relative_to(source["directory"].resolve())
        and not source["directory"].resolve().is_relative_to(output.resolve()), "Use a fresh independent output outside the source baseline.")
    report, authored = declared_report(source, output); raw_report = encoded(report)
    require(len(raw_report) <= MAX_REPORT_BYTES, "Prepared teaching metadata exceeds128KiB.")
    total = sum(row["bytes"] for row in source["sourceRecords"]) + sum(row["bytes"] for row in source["meshRecords"])
    total += source["geometryFile"]["bytes"] + sum(map(len, authored.values())) + len(raw_report)
    require(total <= MAX_WORKSPACE_BYTES, "Actual copied source, coupled mesh and phase inputs exceed one GiB.")
    output.mkdir(parents=True)
    try:
        for row in source["sourceRecords"]:
            raw, current = read(source["directory"], row["path"])
            require(current == row, "Source baseline changed while copying.")
            base.write_exclusive(output / "source-baseline" / row["path"], raw)
        for row in source["meshRecords"]:
            raw, current = read(source["directory"], row["path"]); require(current == row, "Source coupled mesh changed while transferring.")
            base.write_exclusive(output / "case" / row["path"].removeprefix("impeller-case/"), raw)
        raw, current = read(source["directory"], baseline.GEOMETRY_FILE)
        require(current == source["geometryFile"], "Source canonical geometry changed while copying.")
        base.write_exclusive(output / baseline.GEOMETRY_FILE, raw)
        for path, raw in authored.items(): base.write_exclusive(output / "case" / path, raw)
        base.write_exclusive(output / REPORT, raw_report); verify_output(output)
        require(source_inventory(source["directory"]) == source["sourceRecords"], "Original baseline source changed before publication.")
    except Exception:
        safety.scope_path(output)
        require(output.resolve().is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()), "Cleanup stays within this fresh runner output.")
        shutil.rmtree(output)
        raise
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-case-directory", required=True); parser.add_argument("--output-directory", required=True)
    args = parser.parse_args(); report = prepare(args.baseline_case_directory, args.output_directory)
    print(json.dumps({"status": report["status"], "reportFile": REPORT, "actualCfdRunVerified": False,
        "phaseH0Eligible": False, "pressureSweepEnabled": False, "mainApplicationWrites": False}))


if __name__ == "__main__": main()
