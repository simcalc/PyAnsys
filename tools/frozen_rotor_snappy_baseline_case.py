"""Prepare separate classroom MRF physics from actually checked approximate meshes.

Source surfaces, generated mesh quality, physics preparation and actual CFD
qualification remain distinct. This tool does not launch native programs.
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
import frozen_rotor_snappy_case as mesher
import run_frozen_rotor_snappy as geometry
import frozen_rotor_original_case as original

base = original.base
SCHEMA = "untirta.frozen-rotor-snappy-baseline-preparation.v1"
REPORT = "openfoam-preparation.json"
STATUS = "APPROXIMATE_CASE_PREPARED_NATIVE_BASELINE_REQUIRED"
MAX_SECONDS, MAX_MEMORY, MAX_WORKSPACE = 1200, 4 * 1024**3, 1024**3
MESH_NAMES = geometry.MESH_FILES
SOURCE_REPORT_KEYS = {"schemaVersion", "githubRepository", "githubRunId", "githubSourceCommit", "status", "stages",
    "limits", "preparationReportFile", "components", "geometricApproximationMetrics", "volumeMeshGenerated",
    "meshQualityCheckedBySolver", "nativeCoupledCoverageVerified", "inputArtifactsImmutable", "fullGeometryQualification",
    "geometricApproximationQualified", "surfaceDistanceBoundsVerified", "passageClearanceBoundsVerified",
    "nativeCoordinateUnitsVerified", "nativeNodeIdentityEquivalent", "classroomMetresPerSourceCoordinateUnit",
    "meshConvergenceVerified", "numericalBaselineQualified", "qualifiedBaseline", "npsh3Qualified", "simulationExecuted",
    "actualCfdRunVerified", "mainApplicationWrites", "claimBoundary", "runtimeIdentityFile", "mergedNativeMeshEvidence",
    "retainedNativeLogRecords", "workspaceBytes", "elapsedSeconds", "regionSelectionEvidence"}
FALSE_FLAGS = ("nativeCoordinateUnitsVerified", "nativeNodeIdentityEquivalent", "originalPolygonGeometryVerified",
    "geometricApproximationQualified", "geometryAccuracyQualified", "wallResolutionQualified", "meshConvergenceVerified",
    "nativeCaseGenerated", "qualifiedBaseline", "npsh3Qualified", "simulationExecuted", "actualCfdRunVerified",
    "physicalTransientExecuted", "cavitationModelExecuted", "fluentSessionConnected", "licenseVerified",
    "commandExecutionPerformedByThisTool", "mainApplicationWrites")
REPORT_KEYS = {"schemaVersion", "createdAt", "status", "exampleDataCommit", "foundationSourceCommit",
    "sourceSnappyCommandRoot", "sourceSnappyExecutionReportSha256", "sourceGeometryPreparationReportSha256",
    "sourceEvidenceDirectory", "sourceEvidenceFileRecords", "sourceComponentFiles", "initialCaseMeshFiles",
    "authoredInputFiles", "authoredPhysicsFileRecords", "components", "nativeCommandArguments", "classroomAssumptions",
    "limits", "sampling", "requiredAcceptance", "claimBoundary", "sourceNativeGeometryQualityVerified",
    "sourceNativeCoupledCoverageVerified", "sourceInputsImmutable", *FALSE_FLAGS}
LIMITS = {"iterations": 500, "cpus": 1, "memoryBytes": MAX_MEMORY, "maximumSeconds": MAX_SECONDS,
    "maximumWorkspaceBytes": MAX_WORKSPACE, "maximumFileBytes": mesher.MAX_FILE_BYTES,
    "maximumComponentCells": geometry.MAX_COMPONENT_CELLS, "exclusiveMaximumCombinedCells": geometry.MAX_COMBINED_CELLS,
    "exclusiveMaximumCombinedNodes": geometry.MAX_COMBINED_POINTS}
REQUIRED_ACCEPTANCE = ["Unmodified actual native merged checkMesh allGeometry/allTopology and NCC coverage",
    "Finite native fields, residual histories, conservation and stationary head/flow over the declared final window",
    "Measured wall resolution and mesh/time convergence; approximate geometry deviation remains separately unqualified",
    "Active phase-change noncavitating reference and pressure sweep before NPSH3; physical sliding transient/restart separately"]
CLAIM = "Physics preparation from actually quality-checked approximate classroom meshes only. Source STL float32 approximation and new generated approximate boundary faces remain explicit; original polygon/node identity, geometry accuracy, converged CFD, NPSH3 and physical transient are not claimed."


class BaselineCaseIssue(ValueError):
    pass


def require(value, action):
    if not value:
        raise BaselineCaseIssue(action)


def encoded(value):
    return mesher.encoded(value)


def read(root, relative, limit=mesher.MAX_FILE_BYTES):
    return mesher.read(root, relative, limit)


def json_file(root, relative):
    return mesher.json_file(root, relative)


def validate_runtime(value):
    require(isinstance(value, dict) and set(value) == {"schemaVersion", "openFoamFoundationVersion", "packageVersion",
        "officialDebSha256", "expectedSourceCommit", "binaries", "mainApplicationWrites"},
        "Use only the reviewed source geometry runtime fields.")
    require(value["schemaVersion"] == "untirta.frozen-rotor-snappy-runtime-identity.v1"
        and value["openFoamFoundationVersion"] == 12 and value["packageVersion"] == "20250206"
        and value["officialDebSha256"] == geometry.surface.PACKAGE_SHA and value["expectedSourceCommit"] == mesher.SOURCE_COMMIT
        and value["mainApplicationWrites"] is False, "Require the exact actually used Foundation-12 geometry runtime.")
    rows = value["binaries"]
    require(isinstance(rows, list) and len(rows) == len(geometry.PROGRAMS)
        and [row.get("program") for row in rows] == list(geometry.PROGRAMS),
        "All actual source geometry programs must remain ordered and bound.")
    for row in rows:
        require(set(row) == {"program", "path", "bytes", "sha256", "packageOwnership"}
            and isinstance(row["bytes"], int) and not isinstance(row["bytes"], bool) and 0 < row["bytes"] <= mesher.MAX_FILE_BYTES
            and re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
            and re.fullmatch(r"/opt/openfoam12/[A-Za-z0-9_./-]+/" + row["program"], row["path"])
            and ".." not in row["path"].split("/") and row["packageOwnership"] == "openfoam12: " + row["path"],
            "Retain each actual official geometry executable byte identity and package ownership.")


def source_preparation(root, command_root):
    """Verify exact copied preparation without rewriting historical command paths."""
    report = json_file(root, mesher.REPORT)
    require(set(report) == mesher.REPORT_KEYS and report["schemaVersion"] == mesher.SCHEMA
        and report["status"] == mesher.STATUS and report["exampleDataCommit"] == mesher.union.source.PIN
        and report["foundationSourceCommit"] == mesher.SOURCE_COMMIT
        and all(report[key] is False for key in mesher.FALSE_FLAGS)
        and report["sourceSurfaceGeometryPrerequisiteVerified"] is True and report["sourceSurfaceInputsImmutable"] is True,
        "Require the exact source approximate geometry preparation and distinct no-CFD claims.")
    mesher.utc_timestamp(report["createdAt"])
    require(report["classroomAssumptions"] == mesher.CLASSROOM_ASSUMPTIONS and report["limits"] == mesher.LIMITS
        and report["requiredAcceptance"] == mesher.REQUIRED_ACCEPTANCE and report["claimBoundary"] == mesher.CLAIM
        and report["references"] == mesher.REFERENCES,
        "Preserve exact source geometry controls, bounds and historical command provenance.")
    union = mesher.union.verify_output(root / "source-surface")
    proof = mesher.verify_surface_proof(root / "source-surface-check", root / "source-surface", union)
    components, authored = mesher.make_components(root / "source-surface", union)
    require(report["nativeCommandArguments"] == geometry.historical_commands(command_root, components),
        "Bind historical native commands to the independently regenerated source inside points.")
    payloads = mesher.source_union_payloads(root / "source-surface")
    expected = {"authoredFileRecords": [mesher.union.file_row(p, b) for p, b in sorted(authored.items())],
        "sourceSurfaceFileRecords": [mesher.union.file_row("source-surface/" + p, b) for p, b in sorted(payloads.items())],
        "sourceProofFileRecords": [mesher.union.file_row("source-surface-check/" + p, b) for p, b in sorted(proof.items())]}
    require(report["components"] == components and all(report[key] == rows for key, rows in expected.items()),
        "Regenerate every measured seed, tag, dictionary and copied source byte identity.")
    require(all(read(root, path)[0] == raw for path, raw in authored.items()), "Source authored geometry files changed.")
    return {"report": report, "reportFile": read(root, mesher.REPORT, mesher.MAX_REPORT_BYTES)[1],
        "components": components, "union": union}


def validate_inputs(directory, *, command_root=None):
    root = mesher.inside(directory)
    if command_root is None:
        recorded = json_file(root, mesher.REPORT).get("nativeCommandArguments")
        require(isinstance(recorded, list) and len(recorded) == 15 and isinstance(recorded[0], list)
            and len(recorded[0]) == 3 and recorded[0][:2] == ["blockMesh", "-case"],
            "Derive source command provenance only from the exact recorded geometry command tuple.")
        case_path = geometry.historical_command_root(recorded[0][2])
        require(case_path.name == "impeller-case", "The recorded first source case must be the exact impeller component.")
        command_root = str(case_path.parent)
    command_root = str(geometry.historical_command_root(command_root))
    prepared = source_preparation(root, command_root)
    report = json_file(root, "snappy-execution.json")
    require(set(report) == SOURCE_REPORT_KEYS and report["schemaVersion"] == geometry.SCHEMA
        and report["status"] == "APPROXIMATE_NATIVE_MESH_CHECKED_GEOMETRY_REVIEW_REQUIRED"
        and report["githubRepository"] == "simcalc/PyAnsys" and re.fullmatch(r"[1-9][0-9]{0,19}", report["githubRunId"])
        and re.fullmatch(r"[a-f0-9]{40}", report["githubSourceCommit"]), "Require complete actual passing approximate mesh execution provenance.")
    require(all(report[key] is True for key in ("volumeMeshGenerated", "meshQualityCheckedBySolver", "nativeCoupledCoverageVerified", "inputArtifactsImmutable"))
        and all(report[key] is False for key in ("fullGeometryQualification", "geometricApproximationQualified",
            "surfaceDistanceBoundsVerified", "passageClearanceBoundsVerified", "nativeCoordinateUnitsVerified", "nativeNodeIdentityEquivalent",
            "meshConvergenceVerified", "numericalBaselineQualified", "qualifiedBaseline", "npsh3Qualified", "simulationExecuted", "actualCfdRunVerified", "mainApplicationWrites"))
        and report["classroomMetresPerSourceCoordinateUnit"] == 1, "Approximate native mesh quality does not establish geometry accuracy or CFD qualification.")
    limits = {"maximumSeconds": 900, "maximumCpus": 1, "maximumMemoryBytes": geometry.surface.MAX_MEMORY,
        "maximumWorkspaceBytes": geometry.surface.MAX_WORKSPACE, "maximumLogBytes": geometry.MAX_LOG,
        "maximumActualComponentCells": geometry.MAX_COMPONENT_CELLS, "combinedCellsStrictlyBelow": geometry.MAX_COMBINED_CELLS,
        "combinedPointsStrictlyBelow": geometry.MAX_COMBINED_POINTS}
    require(report["limits"] == limits and report["preparationReportFile"] == prepared["reportFile"],
        "Source execution must bind the exact prepared recipe and declared native limits.")
    require(isinstance(report["workspaceBytes"], int) and not isinstance(report["workspaceBytes"], bool)
        and 0 < report["workspaceBytes"] <= geometry.surface.MAX_WORKSPACE
        and isinstance(report["elapsedSeconds"], (int, float)) and not isinstance(report["elapsedSeconds"], bool)
        and math.isfinite(report["elapsedSeconds"]) and 0 <= report["elapsedSeconds"] < 900,
        "Retain finite measured source execution timing and workspace bounds.")
    runtime_raw, runtime_row = read(root, "snappy-runtime-identity.json", mesher.MAX_REPORT_BYTES)
    require(report["runtimeIdentityFile"] == runtime_row, "Actual source runtime bytes changed.")
    validate_runtime(mesher.union.read_json(runtime_raw))
    stages = report["stages"]
    source_commands = geometry.historical_commands(command_root, prepared["components"])
    require(isinstance(stages, list) and len(stages) == len(source_commands), "Every actual geometry stage must be complete.")
    logs = []
    texts = {}
    for i, (stage, args) in enumerate(zip(stages, source_commands)):
        path = f"logs/{i:02d}-{args[0]}.log"
        raw, row = read(root, path, geometry.MAX_LOG)
        require(set(stage) == {"stageIndex", "program", "arguments", "exitCode", "elapsedSeconds", "logFile"}
            and type(stage["stageIndex"]) is int and type(stage["exitCode"]) is int
            and stage["stageIndex"] == i and stage["program"] == args[0] and stage["arguments"] == args and stage["exitCode"] == 0
            and stage["logFile"] == row and isinstance(stage["elapsedSeconds"], (int, float))
            and not isinstance(stage["elapsedSeconds"], bool) and math.isfinite(stage["elapsedSeconds"])
            and 0 <= stage["elapsedSeconds"] < 900, "All source native stages, arguments and actual complete logs must match exactly.")
        text = raw.decode("utf-8", errors="strict")
        geometry.completed_log(text)
        logs.append(row); texts[i] = text
    require(report["retainedNativeLogRecords"] == logs, "Every complete source native log remains immutable and bound.")
    measured, components, snapshots, selections = [], [], [], []
    require(isinstance(report["components"], list) and len(report["components"]) == 2, "Require both actual component inventories.")
    component_quality_indices = [i for i, args in enumerate(source_commands)
        if args[0] == "checkMesh"][0:-1]
    merged_quality_index = len(source_commands) - 1
    require(len(component_quality_indices) == 2 and source_commands[merged_quality_index][0] == "checkMesh",
        "Require two ordered component quality checks and the final merged native quality check.")
    for i, expected in enumerate(prepared["components"]):
        name, index = expected["component"], component_quality_indices[i]
        old = report["components"][i]
        split_indices = [stage_index for stage_index, args in enumerate(source_commands)
            if args[0] == "splitMeshRegions" and args[2] == str(geometry.historical_command_root(command_root) / expected["caseDirectory"])]
        require(len(split_indices) == 1 and split_indices[0] < index,
            "Require one actual measured-seed region selection before each component quality check.")
        selection = geometry.verify_region_selection(root, expected, texts[split_indices[0]], command_root=command_root)
        selections.append(selection)
        actual = geometry.native_mesh(root / "component-mesh-snapshots" / name, [expected], texts[index])
        actual["caseDirectory"] = name + "-case"
        actual["nativeFiles"] = [{**row, "path": name + "-case/constant/polyMesh/" + Path(row["path"]).name} for row in actual["nativeFiles"]]
        rows = [read(root, f"component-mesh-snapshots/{name}/constant/polyMesh/{file}")[1] for file in MESH_NAMES]
        selected_files = {Path(row["path"]).name: row for row in selection["selectedNativeFileRecords"]}
        snapshot_files = {Path(row["path"]).name: row for row in rows}
        require(set(selected_files) == set(geometry.SELECTION_FILES)
            and all(snapshot_files[file]["bytes"] == selected_files[file]["bytes"]
                and snapshot_files[file]["sha256"] == selected_files[file]["sha256"] for file in geometry.SELECTION_FILES),
            "Checked premerge snapshots must retain every exact selected native primitive; topoSet may only add the cell zone.")
        actual.update(nativeMeshSnapshotRecords=rows, nativeFilesMeasuredAtStageIndex=index,
            nativeFilesImmutableAfterMerge=False, regionSelectionEvidence=selection)
        require(actual == old, "Recomputed premerge native topology, quality, tags, zones, counts and snapshot bytes must match the actual geometry evidence.")
        measured.append(actual); snapshots.extend(rows)
        components.append({key: actual[key] for key in ("component", "caseDirectory", "nodeCount", "cellCount", "faceCount", "internalFaceCount", "patchFaces", "cellZone", "patchTypes")})
    require(report["regionSelectionEvidence"] == selections,
        "Both ordered source native region-selection packets must independently replay from their actual maps and immutable primitives.")
    require(sum(row["cellCount"] for row in components) < geometry.MAX_COMBINED_CELLS
        and sum(row["nodeCount"] for row in components) < geometry.MAX_COMBINED_POINTS, "Admit only the measured lighter combined geometry budget.")
    merged = geometry.native_mesh(root / "impeller-case", measured, texts[merged_quality_index])
    require(merged == report["mergedNativeMeshEvidence"] and merged["cellCount"] == sum(row["cellCount"] for row in components)
        and merged["nodeCount"] == sum(row["nodeCount"] for row in components)
        and merged["nativeQuality"]["polyMeshConnectedRegionCount"] == 2, "The actual merged source mesh must retain both separate NCC-coupled fluid components.")
    metrics = [geometry.approximation(row, union) for row, union in zip(measured, prepared["union"]["components"])]
    require(metrics == report["geometricApproximationMetrics"], "Regenerate actual source geometry area/volume diagnostics without promoting accuracy claims.")
    names = {mesher.REPORT, "snappy-execution.json", "snappy-runtime-identity.json"}
    for key in ("authoredFileRecords", "sourceSurfaceFileRecords", "sourceProofFileRecords"):
        names.update(row["path"] for row in prepared["report"][key])
    names.update(row["path"] for row in snapshots)
    names.update(row["path"] for row in merged["nativeFiles"])
    names.update(row["path"] for row in logs)
    names.update(row["path"] for selection in selections for row in geometry.selection_file_records(selection))
    payloads = {path: read(root, path, geometry.MAX_LOG if path.endswith(".log") else mesher.MAX_FILE_BYTES)[0] for path in sorted(names)}
    require(sum(map(len, payloads.values())) <= MAX_WORKSPACE, "Source geometry evidence exceeds the isolated transfer budget.")
    return {"report": report, "reportFile": read(root, "snappy-execution.json", mesher.MAX_REPORT_BYTES)[1],
        "preparation": prepared, "components": components, "snapshots": snapshots, "payloads": payloads,
        "sourceDirectory": root, "commandRoot": command_root}


def case_files(component, pressure_reference_absolute_pa=300000, *, background_box=True):
    require(component in ("impeller", "volute"), "Use the two fixed source component identities.")
    require(isinstance(background_box, bool), "Declare actual presence of the empty background-box placeholder.")
    pressure = original.pressure_reference(pressure_reference_absolute_pa)
    require(pressure == 300000, "This first classroom baseline declares exactly 300000 Pa before execution.")
    result = original.case_files(component, pressure)
    for path in ("0/U", "0/p", "0/k", "0/omega", "0/nut"):
        # The older conversion recipe has an empty defaultFaces placeholder.
        # This lane instead explicitly assigns only snappy's empty backgroundBox.
        result[path] = result[path].replace('"defaultFaces"', '"backgroundBox"')
        require(result[path].count('"backgroundBox"') == 1, "Assign each authored field's empty backgroundBox exactly once.")
        if not background_box:
            result[path], count = re.subn(r'    "backgroundBox"\s*\{[^{}]*\}\s*', "", result[path])
            require(count == 1 and "backgroundBox" not in result[path], "Omit only the absent empty background-box field patch.")
    return {path: raw.encode("ascii") for path, raw in result.items()}


def authored_case_files(source, components):
    """Bind the optional empty placeholder to actual uncoupled snapshot patches."""
    backgrounds = {}
    for component in components:
        name = component["component"]
        patches = geometry.boundary_entries(source / "component-mesh-snapshots" / name / "constant/polyMesh/boundary")
        background = patches.get("backgroundBox")
        require(background is None or (background["type"] == "patch" and background["nFaces"] == 0),
            "An optional backgroundBox must be an actual zero-face patch in the immutable source snapshot.")
        backgrounds[name] = background is not None
    result = {}
    for component in components:
        name, folder = component["component"], component["caseDirectory"]
        # The master field already carries both components' physical BCs.
        # Its empty placeholder is needed only if either input mesh has one.
        include_background = any(backgrounds.values()) if name == "impeller" else backgrounds[name]
        result.update({folder + "/" + p: raw for p, raw in
            case_files(name, background_box=include_background).items()})
    return result


def commands(output):
    return original.commands(output)


def historical_commands(command_root):
    """Replay immutable argv lexically; never access or execute historical paths."""
    root = geometry.historical_command_root(command_root)
    case, other = str(root / "impeller-case"), str(root / "volute-case")
    return [["mergeMeshes", "-case", case, "-addCases", '("' + other + '")', "-overwrite"],
        ["createNonConformalCouples", "-case", case, "-overwrite"],
        ["checkMesh", "-case", case, "-allGeometry", "-allTopology", "-writeSets"],
        ["foamRun", "-case", case]]


def historical_command_root(report):
    recorded = report.get("nativeCommandArguments")
    require(isinstance(recorded, list) and len(recorded) == 4 and isinstance(recorded[0], list)
        and len(recorded[0]) == 6 and recorded[0][:2] == ["mergeMeshes", "-case"],
        "Derive baseline command provenance only from the complete exact four-command tuple.")
    case_path = geometry.historical_command_root(recorded[0][2])
    require(case_path.name == "impeller-case", "The recorded baseline case must be the exact impeller master.")
    command_root = str(geometry.historical_command_root(str(case_path.parent)))
    require(recorded == historical_commands(command_root),
        "Every saved baseline argument must share the same bounded historical root and exact reviewed command.")
    return command_root


def assumptions():
    return {"metresPerNativeUnit": 1, "nativeUnitsVerified": False, "rhoKgM3": 998.2, "muPaS": .001002,
        "gMS2": 9.81, "solverGravityMS2": [0, 0, 0], "gravityInSolver": False, "speedRpm": 1450, "axis": [0, 0, 1],
        "massFlowKgS": 90, "volumetricFlowM3S": 90 / 998.2, "temperatureK": 293.15, "vapourPressurePa": 2339,
        "externalTotalPressureAbsolutePa": 300000, "pressureSelectedBeforeExecution": True,
        "kinematicGaugePressureDefinition": "pAbsolutePa=998.2*pKinematic+300000; inlet external total gauge pressure=0 m2/s2.",
        "temperatureVapourRole": "Declared later cavitation basis only; this singlephase incompressible baseline has no temperature or phase-change equation.",
        "headDatum": "Explicit classroom common zero section datum; no gravity force in solver and no measured section-elevation correction.",
        "wallAdaptation": "Blade/impeller-hub/impeller-shroud rotate; inblock and volute walls are stationary. This is an explicit classroom adaptation.",
        "inletTurbulence": {"kM2S2": .375, "omegaSInverse": 100, "basis": "Declared classroom inputs, not measured."},
        "geometryBasis": "Source-bound float32 STL surfaces with new generated approximate boundary faces; the exact retained generator recipe declares whether snapping occurred. No original polygon/node identity or quantified geometry-accuracy claim."}


SAMPLING = {"nativeFunctionObjectConvention": "Foundation12 select patch; operation average; weightField phi (signed).",
    "flowUnits": "m3/s outward positive", "totalPressureField": "rho*pKinematic + rho*|Uabsolute|^2/2 + 300000 Pa",
    "staticPressureField": "rho*pKinematic only; add 300000 Pa exactly once downstream.",
    "iterationIndexIsPhysicalTime": False, "qualifiedBaseline": False}


def boundary_blocks(raw, field):
    text = raw.decode("ascii", errors="strict")
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
    includes = re.findall(r'#[^\n]*', text)
    require(all(row.strip() == '#includeEtc "caseDicts/setConstraintTypes"' for row in includes) and len(includes) <= 1,
        "Fields may use only the reviewed native constraint include; executable directives are prohibited.")
    text = re.sub(r'#[^\n]*', "", text)
    def constant_flow(match):
        # Foundation-12 Function1::writeEntry emits this block even when
        # authored as a primitive constant. Only this reviewed outlet flow
        # keyword and exact constant type are normalized, never executable
        # functions, tables, units, arbitrary nested dictionaries or code.
        body = match[1]
        match_type = re.fullmatch(r"\s*type\s+constant\s*;\s*value\s+(" + geometry.surface.FLOAT + r")\s*;\s*", body)
        require(match_type and math.isfinite(float(match_type[1])), "Allow only a finite scalar constant volumetricFlowRate Function1.")
        return "volumetricFlowRate " + format(float(match_type[1]), ".17g") + ";"
    text = re.sub(r"\bvolumetricFlowRate\s*\{([^{}]*)\}", constant_flow, text)
    header = re.search(r"FoamFile\s*\{([^{}]*)\}", text)
    require(header and re.search(r"\bformat\s+ascii\s*;", header[1])
        and re.search(r"\bclass\s+" + ("volVectorField" if field == "U" else "volScalarField") + r"\s*;", header[1])
        and re.search(r"\bobject\s+" + re.escape(field) + r"\s*;", header[1]), "Require the plain supported native field role.")
    match = re.search(r"\bboundaryField\s*\{(.*)\}\s*$", text, re.S)
    require(match, "Every native initial field requires a complete boundary dictionary.")
    entries = re.findall(r'"?([A-Za-z0-9_-]+)"?\s*\{([^{}]*)\}', match[1])
    require(not re.sub(r'"?[A-Za-z0-9_-]+"?\s*\{[^{}]*\}', "", match[1]).strip(), "Native field patch dictionaries must be plain and completely parsed.")
    rows = {}
    for name, body in entries:
        require(name not in rows, "Native field patch names must be unique.")
        pairs = re.findall(r"([A-Za-z][A-Za-z0-9_]*)\s+([^;{}]+);", body)
        require(not re.sub(r"[A-Za-z][A-Za-z0-9_]*\s+[^;{}]+;", "", body).strip()
            and len(pairs) == len({key for key, _ in pairs}), "Native patch values must be complete nonexecutable entries.")
        rows[name] = {key: canonical_field_value(value) for key, value in pairs}
    return text[:match.start()], rows


def canonical_field_value(value):
    value = " ".join(value.split())
    if re.fullmatch(geometry.surface.FLOAT, value):
        require(math.isfinite(float(value)), "Native scalar BC constants must be finite.")
        return format(float(value), ".17g")
    uniform = re.fullmatch(r"uniform\s+(" + geometry.surface.FLOAT + r"|\([^()]+\))", value)
    if uniform:
        numeric = uniform[1]
        if numeric.startswith("("):
            words = numeric[1:-1].split()
            require(len(words) == 3 and all(re.fullmatch(geometry.surface.FLOAT, word) and math.isfinite(float(word)) for word in words),
                "Native uniform vector BCs must contain exactly three finite scalars.")
            return "uniform " + mesher.vector(map(float, words))
        require(math.isfinite(float(numeric)), "Native uniform scalar BCs must be finite.")
        return "uniform " + format(float(numeric), ".17g")
    return value


def verify_fields_after_ncc(root, authored):
    """Require unchanged physical BC semantics; permit only native NCC constraints."""
    allowed_defaults = {"phi": "phi", "rho": "rho", "psi": "none", "gamma": "1", "rhoInlet": "1"}
    native_patches = {}
    for relative, expected in authored.items():
        if "/0/" not in relative:
            continue
        field = Path(relative).name
        prefix, original_rows = boundary_blocks(expected, field)
        if relative.startswith("impeller-case/"):
            # mergeMeshes merges polyMesh, not fields. The authored master
            # already includes both sets of physical BCs; verify this union
            # explicitly while the untouched add-case remains separate.
            _, other_rows = boundary_blocks(authored["volute-case/0/" + field], field)
            require(all(name not in original_rows or original_rows[name] == row for name, row in other_rows.items()),
                "Shared empty-placeholder field values must agree across components.")
            original_rows = {**original_rows, **other_rows}
        actual_prefix, rows = boundary_blocks(read(root, relative)[0], field)
        for key in ("dimensions", "internalField"):
            pattern = r"\b" + key + r"\s+([^;{}]+);"
            before, after = re.findall(pattern, prefix), re.findall(pattern, actual_prefix)
            require(len(before) == len(after) == 1 and " ".join(before[0].split()) == " ".join(after[0].split()),
                "NCC setup cannot silently change authored units or initial physical state.")
        initial_value = canonical_field_value(re.findall(r"\binternalField\s+([^;{}]+);", prefix)[0])
        require(set(original_rows) <= set(rows), "Every original physical patch BC must remain present after native NCC creation.")
        for name, original_row in original_rows.items():
            row = rows[name].copy()
            defaults = allowed_defaults.copy()
            # The pinned MRFnoSlip writer materializes its initially zero value;
            # fixedFluxPressure writes its initially zero gradient and value.
            if original_row["type"] == "MRFnoSlip":
                defaults["value"] = "uniform (0 0 0)"
            if original_row["type"] == "fixedFluxPressure":
                defaults.update(gradient="uniform 0", value="uniform 0")
            # Pinned Foundation-12 SST writers materialize these exact
            # constructor defaults; they do not change the authored model.
            # omegaWallFunction.C:294-299; nutWallFunction.C:49-57,185-190,
            # official source commit 86e126a7bc4dbfaed949bbc58c9f0fdd67acc059.
            if field == "omega" and original_row["type"] == "omegaWallFunction":
                defaults.update(beta1=canonical_field_value("0.075"), blended="false")
            if field == "nut" and original_row["type"] == "nutkWallFunction":
                defaults.update({key: canonical_field_value(value) for key, value in
                    {"Cmu": "0.09", "kappa": "0.41", "E": "9.8"}.items()})
            # calculated::write always emits value. Field::writeEntry uses
            # nonuniform for size zero, so the empty scalar list carries no
            # values on this confirmed empty background placeholder. This is
            # not accepted on any physical wall, inlet, outlet or NCC patch.
            if name == "backgroundBox" and field == "nut" and original_row["type"] == "calculated" and \
                    re.fullmatch(r"nonuniform\s+List<scalar>\s+0\s*\(\s*\)", row.get("value", "")):
                folder = relative.split("/", 1)[0]
                if folder not in native_patches:
                    native_patches[folder] = geometry.boundary_entries(root / folder / "constant/polyMesh/boundary")
                patch = native_patches[folder].get(name)
                require(patch is not None and patch["type"] == "patch" and patch["nFaces"] == 0,
                    "An empty calculated value list requires the actual zero-face backgroundBox patch.")
                row["value"] = original_row["value"]
            require(all(row.get(key) == value for key, value in original_row.items())
                and all(key in original_row or defaults.get(key) == value for key, value in row.items()),
                "Native NCC may retain only unchanged authored physical BC values and explicit reviewed default entries.")
        for name in set(rows) - set(original_rows):
            require(name in {"pumpInterface_on_interface-impeller-outlet", "pumpInterface_on_interface-volute-inlet",
                "nonConformalError_on_interface-impeller-outlet", "nonConformalError_on_interface-volute-inlet"}
                and rows[name].get("type") == ("nonConformalCyclic" if name.startswith("pumpInterface") else "nonConformalError")
                and all(key == "type" or (key == "value" and value in {initial_value, "uniform (0 0 0)" if field == "U" else "uniform 0"}) for key, value in rows[name].items()),
                "Only the four known field constraint patches may be added by native NCC setup.")


def prepare(snappy_execution_directory, output_directory, pressure_reference_absolute_pa=300000):
    require(original.pressure_reference(pressure_reference_absolute_pa) == 300000, "Preselect the fixed 300000 Pa classroom reference.")
    source, output = mesher.inside(snappy_execution_directory), mesher.inside(output_directory)
    require(not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output),
        "Use a fresh independent RUNNER_TEMP physics output directory.")
    verified = validate_inputs(source)
    authored, initial_meshes = authored_case_files(source, verified["components"]), {}
    for component in verified["components"]:
        name, folder = component["component"], component["caseDirectory"]
        for filename in MESH_NAMES:
            initial_meshes[folder + "/constant/polyMesh/" + filename] = verified["payloads"][f"component-mesh-snapshots/{name}/constant/polyMesh/{filename}"]
    source_payloads = {"source-snappy/" + p: raw for p, raw in verified["payloads"].items()}
    physics_payloads = {"authored-physics/" + p: raw for p, raw in authored.items() if "/0/" in p}
    report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(), "status": STATUS,
        "exampleDataCommit": base.inventory.PIN, "foundationSourceCommit": mesher.SOURCE_COMMIT,
        "sourceSnappyCommandRoot": verified["commandRoot"], "sourceSnappyExecutionReportSha256": verified["reportFile"]["sha256"],
        "sourceGeometryPreparationReportSha256": verified["preparation"]["reportFile"]["sha256"], "sourceEvidenceDirectory": "source-snappy",
        "sourceEvidenceFileRecords": [mesher.union.file_row(p, raw) for p, raw in sorted(source_payloads.items())],
        "sourceComponentFiles": [{**row, "path": "source-snappy/" + row["path"]} for row in verified["snapshots"]],
        "initialCaseMeshFiles": [mesher.union.file_row(p, raw) for p, raw in sorted(initial_meshes.items())],
        "authoredInputFiles": [mesher.union.file_row(p, raw) for p, raw in sorted(authored.items())],
        "authoredPhysicsFileRecords": [mesher.union.file_row(p, raw) for p, raw in sorted(physics_payloads.items())],
        "components": verified["components"], "nativeCommandArguments": commands(output), "classroomAssumptions": assumptions(),
        "limits": LIMITS, "sampling": SAMPLING, "requiredAcceptance": REQUIRED_ACCEPTANCE, "claimBoundary": CLAIM,
        "sourceNativeGeometryQualityVerified": True, "sourceNativeCoupledCoverageVerified": True, "sourceInputsImmutable": True}
    report.update({key: False for key in FALSE_FLAGS})
    payloads = {**authored, **initial_meshes, **source_payloads, **physics_payloads, REPORT: encoded(report)}
    require(len(payloads[REPORT]) <= mesher.MAX_REPORT_BYTES and sum(map(len, payloads.values())) <= MAX_WORKSPACE,
        "Physics preparation evidence must fit 128 KiB report and 1 GiB measured workspace bounds.")
    require(all(read(source, p)[0] == raw for p, raw in verified["payloads"].items()), "Source geometry evidence changed before persistence.")
    written, directories = [], []
    try:
        output.mkdir(parents=True, exist_ok=False)
        for relative, raw in payloads.items():
            target = output / relative
            missing, parent = [], target.parent
            while parent != output and not parent.exists():
                missing.append(parent); parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir(); directories.append(directory)
            written.append(target)
            with target.open("xb") as stream:
                require(stream.write(raw) == len(raw), "A prepared evidence write was incomplete.")
        verify_output(output)
        require(all(read(source, p)[0] == raw for p, raw in verified["payloads"].items()), "Source evidence changed during physics preparation.")
    except Exception:
        for path in reversed(written):
            if path.is_file(): path.unlink()
        for directory in reversed(directories):
            if directory.exists(): directory.rmdir()
        if output.exists(): output.rmdir()
        raise
    return report


def verify_output(directory, *, after_native_ncc=False):
    require(isinstance(after_native_ncc, bool), "Declare whether native NCC field rewriting has actually occurred.")
    root = mesher.inside(directory)
    report = json_file(root, REPORT)
    require(set(report) == REPORT_KEYS and report["schemaVersion"] == SCHEMA and report["status"] == STATUS
        and report["exampleDataCommit"] == base.inventory.PIN and report["foundationSourceCommit"] == mesher.SOURCE_COMMIT
        and all(report[key] is False for key in FALSE_FLAGS)
        and all(report[key] is True for key in ("sourceNativeGeometryQualityVerified", "sourceNativeCoupledCoverageVerified", "sourceInputsImmutable")),
        "Use only the distinct approximate-baseline preparation schema and truthful claim boundaries.")
    mesher.utc_timestamp(report["createdAt"])
    require(report["sourceEvidenceDirectory"] == "source-snappy" and report["classroomAssumptions"] == assumptions()
        and report["limits"] == LIMITS and report["sampling"] == SAMPLING and report["requiredAcceptance"] == REQUIRED_ACCEPTANCE
        and report["claimBoundary"] == CLAIM and report["nativeCommandArguments"] == historical_commands(historical_command_root(report)),
        "Keep exact authored physics, pressure basis, scope, resource limits and four native commands.")
    source = validate_inputs(root / "source-snappy", command_root=report["sourceSnappyCommandRoot"])
    require(report["sourceSnappyExecutionReportSha256"] == source["reportFile"]["sha256"]
        and report["sourceGeometryPreparationReportSha256"] == source["preparation"]["reportFile"]["sha256"]
        and report["components"] == source["components"], "Current source reports and every actual component identity remain bound.")
    expected_source = [mesher.union.file_row("source-snappy/" + p, raw) for p, raw in sorted(source["payloads"].items())]
    require(report["sourceEvidenceFileRecords"] == expected_source
        and report["sourceComponentFiles"] == [{**row, "path": "source-snappy/" + row["path"]} for row in source["snapshots"]],
        "Every exact retained source byte and premerge snapshot must remain independently verified.")
    authored, initial = authored_case_files(root / "source-snappy", source["components"]), {}
    for component in source["components"]:
        name, folder = component["component"], component["caseDirectory"]
        initial.update({folder + "/constant/polyMesh/" + p: source["payloads"][f"component-mesh-snapshots/{name}/constant/polyMesh/{p}"] for p in MESH_NAMES})
    require(report["authoredInputFiles"] == [mesher.union.file_row(p, raw) for p, raw in sorted(authored.items())]
        and report["initialCaseMeshFiles"] == [mesher.union.file_row(p, raw) for p, raw in sorted(initial.items())],
        "Author only the exact source-bound classroom recipe and uncoupled premerge initial meshes.")
    physics = {"authored-physics/" + p: raw for p, raw in authored.items() if "/0/" in p}
    require(report["authoredPhysicsFileRecords"] == [mesher.union.file_row(p, raw) for p, raw in sorted(physics.items())]
        and all(read(root, p)[0] == raw for p, raw in physics.items()), "Immutable authored physical BC copies changed.")
    require(all(read(root, p)[0] == raw for p, raw in authored.items() if not after_native_ncc or "/0/" not in p),
        "Authored immutable native physics input bytes changed.")
    if after_native_ncc:
        verify_fields_after_ncc(root, authored)
    else:
        require(all(read(root, p)[0] == raw for p, raw in initial.items()), "The fresh initial mesh must equal the actual uncoupled source snapshots.")
    return {"report": report, "reportFile": read(root, REPORT, mesher.MAX_REPORT_BYTES)[1], "components": source["components"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snappy-execution-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--pressure-reference-absolute-pa", type=float, default=300000)
    args = parser.parse_args()
    try:
        report = prepare(args.snappy_execution_directory, args.output_directory, args.pressure_reference_absolute_pa)
        print(json.dumps({"status": report["status"], "sourceNativeGeometryQualityVerified": True,
            "geometricApproximationQualified": False, "simulationExecuted": False, "mainApplicationWrites": False})); return 0
    except Exception as error:
        print(json.dumps({"status": "APPROXIMATE_BASELINE_INPUT_CORRECTION_REQUIRED", "action": str(error)[:500],
            "actualCfdRunVerified": False, "mainApplicationWrites": False})); return 2


if __name__ == "__main__":
    raise SystemExit(main())
