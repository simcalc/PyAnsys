"""Prepare one conditional active-phase MRF reference on a checked approximation.

This tool re-reduces existing native baseline evidence. It authors no pressure
sweep, sliding case or result, and launches no native program. Copied evidence
retains historical command provenance; every file read uses its current root.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_cavitation_case as recipe
import frozen_rotor_research_protocol as protocol
import frozen_rotor_snappy_baseline_case as baseline_case
import reduce_frozen_rotor_snappy_baseline as baseline

SCHEMA = "untirta.frozen-rotor-snappy-phase-reference-preparation.v1"
REPORT = "phase-change-preparation.json"
STATUS = "APPROXIMATE_ACTIVE_PHASE_REFERENCE_PREPARED_EXECUTION_REQUIRED"
MODE = "high-pressure-active-phase-MRF-reference"
PRESSURE_PA = 300000.0
PERIOD_S = 60 / 1450
MAX_DT_S, DEFAULT_DURATION_S = PERIOD_S / 360, 4 * PERIOD_S
MAX_SOURCE_RECORDS, MAX_FILE_BYTES, MAX_REPORT_BYTES = 320, 128 * 1024**2, 128 * 1024
MAX_WORKSPACE_BYTES = 1024**3
MESH_FILES = baseline.MESH_FILES
FALSE_FLAGS = ("commandExecutionPerformedByThisTool", "nativeCaseGenerated", "simulationExecuted",
    "actualCfdRunVerified", "cavitationModelExecuted", "numericalSolverAcceptanceVerified", "qualifiedBaseline",
    "phaseObservationQualified", "phaseFamilyReferenceEligible", "phaseFamilyReferenceQualified", "cavitationSweepExecuted",
    "pressureSweepEnabled", "slidingEnabled", "physicalTransientExecuted", "npshrCalculated", "npsh3Qualified",
    "npsh3Verified", "restartVerified", "meshConvergenceVerified", "timeStepConvergenceVerified", "wallResolutionVerified",
    "nativeCoordinateUnitsVerified", "nativeNodeIdentityEquivalent", "originalPolygonGeometryVerified",
    "geometricApproximationQualified", "fullGeometryQualification", "physicalValidationVerified",
    "baselineFieldsCopied", "mainApplicationWrites")
LIMITS = {"maximumSourceRecords": MAX_SOURCE_RECORDS, "maximumFileBytes": MAX_FILE_BYTES,
    "maximumReportBytes": MAX_REPORT_BYTES, "maximumWorkspaceBytes": MAX_WORKSPACE_BYTES,
    "plannedNativeCpus": 1, "plannedNativeMemoryBytes": 4 * 1024**3, "plannedNativeMaximumSeconds": 1200,
    "nativeExecutionEnforcementOwner": "A separately reviewed independent FREE workflow; this helper executes no commands."}
CLAIM = ("One new uniform-liquid fixed-MRF active SchnerrSauer reference trial on the exact source-bound approximate mesh. "
    "Source single-phase numerical acceptance is a preparation prerequisite, not inherited phase acceptance or H0. "
    "Four revolutions is a declared trial duration, not proven residence time or stationarity. "
    "No pressure sweep, sliding motion, NPSHr, geometric accuracy or physical pump performance is qualified.")
REPORT_KEYS = {"schemaVersion", "createdAt", "status", "mode", "sourceGeometryRoute", "exampleDataCommit",
    "foundationSourceCommit", "sourceEvidenceDirectory", "sourceEvidenceFileRecords",
    "sourceBaselineReductionFile", "sourceCaseManifestSha256", "sourceBaselineEvidenceSha256",
    "sourceGeometryIdentityFile", "sourceGeometryControlSha256", "sourceNativeMeshFiles", "preservedNativeMeshFiles",
    "components", "sourceCoupledMeshEvidence", "authoredInputFiles", "authoredCavitationModelSha256",
    "declaredStudyInputs", "physicalBasis", "inletAbsoluteTotalPressurePa", "absolutePressureMapping",
    "classroomMetresPerSourceCoordinateUnit", "modelAssumptions", "temporalPlan", "initialization",
    "sourceBaselineRole", "requiredAcceptance", "nativeCommandArguments", "limits", "claimBoundary",
    "sourceNumericalBaselineQualified", "sourceActualCfdRunVerified", "sourceNativeQualityVerified",
    "sourceNativeCoupledCoverageVerified", "sourceInputsImmutable", *FALSE_FLAGS}
MODEL_FILES = {"constant/fvModels", "constant/phaseProperties", "constant/physicalProperties.water", "constant/physicalProperties.vapour"}
encoded = baseline.encoded
inside, read, json_file = baseline_case.mesher.inside, baseline_case.read, baseline_case.json_file


class PhaseReferenceIssue(ValueError):
    pass


def require(value, action):
    if not value:
        raise PhaseReferenceIssue(action)


def records(root, rows):
    """Re-read every exact bounded safe record; never trust reported byte totals."""
    require(isinstance(rows, list) and 0 < len(rows) <= MAX_SOURCE_RECORDS, "A complete bounded source inventory is required.")
    result, names, total = [], set(), 0
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}
            and isinstance(row["path"], str) and not any(p.startswith(".") for p in row["path"].split("/"))
            and type(row["bytes"]) is int and 0 < row["bytes"] <= MAX_FILE_BYTES
            and protocol.digest(row["sha256"]) and row["path"] not in names,
            "Source rows must be unique regular relative files with exact size and SHA256.")
        raw, actual = read(root, row["path"], MAX_REPORT_BYTES if row["path"].endswith(".json") else MAX_FILE_BYTES)
        require(actual == row, "Current source bytes differ from their actual evidence inventory.")
        total += len(raw)
        require(total <= MAX_WORKSPACE_BYTES, "Actual source inventory exceeds one GiB.")
        result.append(actual); names.add(row["path"])
    return sorted(result, key=lambda row: row["path"])


def verify_baseline(directory):
    """Actually re-run the distinct reducer; flags or relabelled old packets do not admit a case."""
    root = inside(directory)
    packet = json_file(root, "native-baseline-reduction.json")
    require(packet.get("schemaVersion") == baseline.SCHEMA and packet.get("sourceGeometryRoute") == baseline.GEOMETRY_ROUTE,
        "Only the distinct source-bound snappy baseline reduction is admitted.")
    current = baseline.reduce_native_baseline(root)
    require(current == packet and all(packet.get(k) is True for k in ("actualCfdRunVerified", "simulationExecuted",
        "numericalBaselineQualified", "preliminaryBaselineQualified", "noncavitatingBaselineVerified", "interfaceCoverageVerified"))
        and all(packet.get(k) is False for k in ("qualifiedBaseline", "geometricApproximationQualified", "fullGeometryQualification",
            "nativeCoordinateUnitsVerified", "nativeNodeIdentityEquivalent", "wallResolutionVerified", "yPlusResolutionQualified",
            "meshConvergenceVerified", "timeStepConvergenceVerified", "physicalValidationVerified", "npsh3Qualified",
            "restartVerified", "physicalTransientExecuted", "mainApplicationWrites")) and packet.get("diagnostics") == [],
        "Current actual numerical baseline acceptance is required while broader approximation/physical qualification remains false.")
    study, observation = packet["study"], packet["nativeObservation"]
    fixed = protocol.prepare_study(gravity_in_solver=False)
    require(study["input"] == fixed["input"] and study["physicalBasis"] == fixed["physicalBasis"]
        and study["samplingDefinition"] == fixed["samplingDefinition"] and observation["physicalModel"] == "steady-single-phase-MRF"
        and observation["pressureLevelAbsolutePa"] == PRESSURE_PA
        and observation["pressureMapping"] == {"nativeField": "p", "nativeUnit": "m2/s2", "absolutePressureReferencePa": PRESSURE_PA,
            "absoluteReconstructionEquation": "p_static_absolute_Pa = rho_liquid*p_kinematic + explicit_absolute_reference_Pa"},
        "Retain the exact preselected classroom SI, absolute pressure, rotation, fluid and datum basis.")
    projected = protocol.reduce_run(study, observation)
    require(projected == packet["protocolReduction"] and projected["status"] == "QualifiedSuppliedObservation"
        and projected["stages"]["head"]["result"]["value"] > 0
        and observation["measurements"]["minimumStaticPressureAbsolutePa"] > study["input"]["vaporPressurePa"],
        "Recomputed source numerical head, liquid pressure and convergence must qualify independently.")
    manifest, manifest_row = baseline.record(root, "native-input-manifest.json")
    require(manifest == packet["inputManifest"] == packet["manifest"] and manifest["schemaVersion"] == baseline.MANIFEST_SCHEMA
        and manifest["sourceGeometryRoute"] == baseline.GEOMETRY_ROUTE and manifest["controls"] == study["controls"] == observation["controls"]
        and manifest["studyInputs"] == study["input"] and manifest["physicalBasis"] == study["physicalBasis"]
        and manifest_row["sha256"] == packet["nativeInputManifestSha256"] == observation["controlledVariationProof"]["nativeInputManifestSha256"],
        "Actual persisted approximate manifest, observation and control binding must agree.")
    manifest_rows = [{k: row[k] for k in ("path", "bytes", "sha256")} for row in manifest["files"]]
    records(root, manifest_rows)
    for control in protocol.CONTROL_KEYS - {"cavitationModelSha256"}:
        selected = sorted([{k: row[k] for k in ("path", "bytes", "sha256")}
            for row in manifest["files"] if row.get("control") == control], key=lambda row: row["path"])
        require(selected and sha256(encoded(selected)).hexdigest() == study["controls"][control], "Actual baseline control records changed.")
    evidence = records(root, packet["nativeEvidenceRecords"])
    require(sha256(encoded(evidence)).hexdigest() == observation["nativeEvidence"]["sha256"], "Actual aggregate native baseline evidence changed.")
    geometry, geometry_row = baseline.record(root, "geometry-identity.json")
    require(geometry.get("schemaVersion") == "untirta.frozen-rotor-snappy-geometry-identity.v1"
        and geometry.get("exampleDataCommit") == recipe.base.inventory.PIN and geometry.get("sourceGeometryRoute") == baseline.GEOMETRY_ROUTE
        and geometry.get("nativeUnitsToMetres") == 1 and all(geometry.get(k) is False for k in
            ("geometricApproximationQualified", "nativeCoordinateUnitsVerified", "nativeNodeIdentityEquivalent", "mainApplicationWrites"))
        and sha256(encoded([geometry_row])).hexdigest() == study["controls"]["geometrySha256"],
        "Preserve the distinct approximate geometry identity without an original-node or verified-unit claim.")
    preparation = json_file(root, "openfoam-preparation.json")
    components = preparation["components"]
    require([c["component"] for c in components] == ["impeller", "volute"], "Both ordered source components are required.")
    log, _ = read(root, "logs/02-checkMesh.log")
    mesh = baseline.geometry.native_mesh(root / "impeller-case", components, log.decode("utf-8"))
    mesh_rows = [read(root, "impeller-case/constant/polyMesh/" + name)[1] for name in MESH_FILES]
    require(sorted(mesh_rows, key=lambda row: row["path"]) == sorted([row for row in manifest_rows
        if row["path"].startswith("impeller-case/constant/polyMesh/")], key=lambda row: row["path"]),
        "Copy exactly the six actual coupled mesh primitives from the bound native manifest.")
    combined = {}
    for row in [*manifest_rows, *evidence, read(root, "native-baseline-reduction.json", MAX_REPORT_BYTES)[1]]:
        require(row["path"] not in combined or combined[row["path"]] == row, "Conflicting source record identities are prohibited.")
        combined[row["path"]] = row
    source_rows = records(root, sorted(combined.values(), key=lambda row: row["path"]))
    background = baseline.geometry.boundary_entries(root / "impeller-case/constant/polyMesh/boundary").get("backgroundBox")
    require(background is None or (background["type"] == "patch" and background["nFaces"] == 0),
        "Only an actually present zero-face backgroundBox may receive a placeholder field entry.")
    return {"directory": root, "packet": packet, "study": study, "manifest": manifest, "manifestFile": manifest_row,
        "geometry": geometry, "geometryFile": geometry_row, "sourceRecords": source_rows, "meshRecords": mesh_rows,
        "components": components, "coupledMeshEvidence": mesh, "backgroundBoxPresent": background is not None}


def temporal_plan(dt_s, duration_s):
    dt, duration = protocol.number(dt_s, positive=True), protocol.number(duration_s, positive=True)
    require(dt <= MAX_DT_S and DEFAULT_DURATION_S <= duration <= 10 * PERIOD_S,
        "Predeclare at most one rotor degree per maximum step and a four-to-ten-revolution reference trial.")
    return {"maximumTimeStepS": dt, "endTimeS": duration, "rotationPeriodS": PERIOD_S,
        "stationaryMrfRelaxation": True, "physicalRotorMotion": False, "nativeCoLimit": .5, "nativeAlphaCoLimit": .25,
        "durationIsAcceptanceProof": False, "residenceTimeAdequacyVerified": False, "timeStepConvergenceVerified": False}


def native_recipe(study, dt_s=MAX_DT_S, duration_s=DEFAULT_DURATION_S, *, background_box=False):
    require(isinstance(background_box, bool), "Declare actual zero-face placeholder presence.")
    require(study["input"] == protocol.prepare_study()["input"] and study["physicalBasis"] == protocol.prepare_study()["physicalBasis"],
        "Use the exact declared classroom SI basis.")
    temporal_plan(dt_s, duration_s)
    authored = recipe.native_recipe(study, PRESSURE_PA, "steady-sweep", dt_s, duration_s)
    for name in ("U", "p_rgh", "alpha.water", "k", "omega", "nut"):
        path = "0/" + name
        text = authored[path].replace('"defaultFaces"', '"backgroundBox"')
        if not background_box:
            text, count = re.subn(r'    "backgroundBox"\s*\{[^{}]*\}\s*', "", text)
            require(count == 1, "Remove only the absent empty placeholder field block.")
        require("defaultFaces" not in text, "The approximate route has no defaultFaces placeholder.")
        authored[path] = text
    return {path: raw.encode("ascii") for path, raw in authored.items()}


def commands(output_directory):
    case = str(Path(output_directory).absolute() / "case")
    return [["checkMesh", "-case", case, "-allGeometry", "-allTopology", "-writeSets"], ["foamRun", "-case", case]]


def historical_commands(command_root):
    """Reconstruct provenance argv only; never read or execute this old root."""
    root = baseline.geometry.historical_command_root(command_root)
    case = str(root / "case")
    return [["checkMesh", "-case", case, "-allGeometry", "-allTopology", "-writeSets"], ["foamRun", "-case", case]]


def historical_command_root(report):
    recorded = report.get("nativeCommandArguments")
    require(isinstance(recorded, list) and len(recorded) == 2 and isinstance(recorded[0], list)
        and len(recorded[0]) == 6 and recorded[0][:2] == ["checkMesh", "-case"],
        "Derive reference provenance only from the complete reviewed two-command tuple.")
    case_path = baseline.geometry.historical_command_root(recorded[0][2])
    require(case_path.name == "case", "The recorded reference command must target its exact case/ directory.")
    command_root = str(baseline.geometry.historical_command_root(str(case_path.parent)))
    require(recorded == historical_commands(command_root),
        "Both recorded programs must retain the same bounded historical case and exact reviewed arguments.")
    return command_root


def declared_report(source, output, dt_s, duration_s):
    authored = native_recipe(source["study"], dt_s, duration_s, background_box=source["backgroundBoxPresent"])
    rows = [baseline_case.mesher.union.file_row(name, raw) for name, raw in sorted(authored.items())]
    model_rows = [row for row in rows if row["path"] in MODEL_FILES]
    copied_rows = [{**row, "path": "source-baseline/" + row["path"]} for row in source["sourceRecords"]]
    mesh_rows = [{**row, "path": row["path"].removeprefix("impeller-case/")} for row in source["meshRecords"]]
    geometry_row = source["geometryFile"]
    report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(), "status": STATUS, "mode": MODE,
        "sourceGeometryRoute": baseline.GEOMETRY_ROUTE, "exampleDataCommit": recipe.base.inventory.PIN,
        "foundationSourceCommit": baseline.geometry.surface.SOURCE_COMMIT,
        "sourceEvidenceDirectory": "source-baseline", "sourceEvidenceFileRecords": copied_rows,
        "sourceBaselineReductionFile": next(row for row in copied_rows if row["path"] == "source-baseline/native-baseline-reduction.json"),
        "sourceCaseManifestSha256": source["manifestFile"]["sha256"],
        "sourceBaselineEvidenceSha256": source["packet"]["nativeObservation"]["nativeEvidence"]["sha256"],
        "sourceGeometryIdentityFile": geometry_row, "sourceGeometryControlSha256": source["study"]["controls"]["geometrySha256"],
        "sourceNativeMeshFiles": source["meshRecords"], "preservedNativeMeshFiles": mesh_rows,
        "components": source["components"], "sourceCoupledMeshEvidence": source["coupledMeshEvidence"],
        "authoredInputFiles": rows, "authoredCavitationModelSha256": sha256(encoded(model_rows)).hexdigest(),
        "declaredStudyInputs": source["study"]["input"], "physicalBasis": source["study"]["physicalBasis"],
        "inletAbsoluteTotalPressurePa": PRESSURE_PA,
        "absolutePressureMapping": {"nativeField": "p_rgh", "nativeUnit": "Pa", "nativeGravityMS2": [0, 0, 0],
            "equation": "p_abs=p_rgh+rho*gh; g=0 gives p_abs=p_rgh, with no atmospheric addition.",
            "referenceAdditionPa": 0, "boundary": "prghTotalPressure p0=300000 Pa absolute inlet total pressure"},
        "classroomMetresPerSourceCoordinateUnit": 1, "modelAssumptions": recipe.MODEL_ASSUMPTIONS,
        "temporalPlan": temporal_plan(dt_s, duration_s),
        "initialization": "New uniform alpha.water=1, zero U and absolute p_rgh=300000 Pa at t=0; no baseline result fields are copied as phase state or restart.",
        "sourceBaselineRole": "Numerically qualified single-phase prerequisite on this approximation only; its head is not active-phase H0.",
        "requiredAcceptance": ["Actual native SchnerrSauer selection, finite bounded phase fields and complete solver histories",
            "Unmodified checkMesh/allGeometry/allTopology and NCC coverage with signed mass/interface conservation",
            "Final-window residual/head/flow acceptance, liquid section cuts and global alpha_v<=1e-4 before using active-phase H0",
            "Geometry/units/wall/refinement/physical validation stay separate; no sweep or sliding is admitted by this preparation"],
        "nativeCommandArguments": commands(output), "limits": LIMITS, "claimBoundary": CLAIM,
        "sourceNumericalBaselineQualified": True, "sourceActualCfdRunVerified": True, "sourceNativeQualityVerified": True,
        "sourceNativeCoupledCoverageVerified": True, "sourceInputsImmutable": True, **{key: False for key in FALSE_FLAGS}}
    return report, authored


def verify_output(directory):
    root = inside(directory)
    report = json_file(root, REPORT)
    require(set(report) == REPORT_KEYS and report.get("schemaVersion") == SCHEMA and report.get("status") == STATUS,
        "Use the distinct exact active-phase-reference preparation schema.")
    baseline_case.mesher.utc_timestamp(report["createdAt"])
    source = verify_baseline(root / "source-baseline")
    expected, authored = declared_report(source, root, report["temporalPlan"]["maximumTimeStepS"], report["temporalPlan"]["endTimeS"])
    expected["createdAt"] = report["createdAt"]
    expected["nativeCommandArguments"] = historical_commands(historical_command_root(report))
    require(report == expected, "Regenerated source bindings, pressure, controls, initialization and false qualification flags must agree exactly.")
    records(root, report["sourceEvidenceFileRecords"])
    for row in source["meshRecords"]:
        relative = "case/" + row["path"].removeprefix("impeller-case/")
        require(read(root, relative)[1] == {**row, "path": relative}, "Prepared coupled mesh bytes changed.")
    require(read(root, "geometry-identity.json", MAX_REPORT_BYTES)[1] == source["geometryFile"], "Prepared approximate geometry identity changed.")
    require(all(read(root, "case/" + name)[0] == raw for name, raw in authored.items()), "Authored phase pressure, physical BCs or model controls changed.")
    # No native result file may be mistaken for authored phase state.
    require(not (root / "case/0/p").exists() and not (root / "case/constant/dynamicMeshDict").exists(),
        "Do not import a baseline pressure field or enable sliding motion in this reference preparation.")
    return {"report": report, "reportFile": read(root, REPORT, MAX_REPORT_BYTES)[1], "sourceBaseline": source,
        "components": source["components"]}


def prepare(baseline_directory, output_directory, *, dt_s=MAX_DT_S, duration_s=DEFAULT_DURATION_S):
    source, output = verify_baseline(baseline_directory), inside(output_directory)
    require(not output.exists() and not output.is_relative_to(source["directory"]) and not source["directory"].is_relative_to(output),
        "Use a fresh independent RUNNER_TEMP output directory outside the actual source baseline.")
    report, authored = declared_report(source, output, dt_s, duration_s)
    raw_report = encoded(report)
    require(len(raw_report) <= MAX_REPORT_BYTES, "Prepared reference metadata exceeds 128 KiB.")
    total = sum(row["bytes"] for row in source["sourceRecords"]) + sum(row["bytes"] for row in source["meshRecords"])
    total += source["geometryFile"]["bytes"] + sum(map(len, authored.values())) + len(raw_report)
    require(total <= MAX_WORKSPACE_BYTES, "Actual copied source, coupled mesh and authored phase bytes exceed one GiB.")
    written, created = [], []
    try:
        output.mkdir(parents=True, exist_ok=False)
        def write(relative, raw):
            target = output / relative
            missing, parent = [], target.parent
            while parent != output and not parent.exists(): missing.append(parent); parent = parent.parent
            for folder in reversed(missing): folder.mkdir(); created.append(folder)
            written.append(target)
            with target.open("xb") as stream: require(stream.write(raw) == len(raw), "An independent preparation write was incomplete.")
        for row in source["sourceRecords"]: write("source-baseline/" + row["path"], read(source["directory"], row["path"])[0])
        for row in source["meshRecords"]: write("case/" + row["path"].removeprefix("impeller-case/"), read(source["directory"], row["path"])[0])
        write("geometry-identity.json", read(source["directory"], "geometry-identity.json", MAX_REPORT_BYTES)[0])
        for name, raw in authored.items(): write("case/" + name, raw)
        write(REPORT, raw_report)
        verify_output(output)
        records(source["directory"], source["sourceRecords"])
    except Exception:
        for path in reversed(written):
            if path.is_file(): path.unlink()
        for folder in reversed(created):
            if folder.exists(): folder.rmdir()
        if output.exists(): output.rmdir()
        raise
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-case-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--time-step-s", type=float, default=MAX_DT_S)
    parser.add_argument("--duration-s", type=float, default=DEFAULT_DURATION_S)
    args = parser.parse_args()
    try:
        report = prepare(args.baseline_case_directory, args.output_directory, dt_s=args.time_step_s, duration_s=args.duration_s)
        print(json.dumps({key: report[key] for key in ("status", "mode", "inletAbsoluteTotalPressurePa", "sourceNumericalBaselineQualified",
            "simulationExecuted", "phaseFamilyReferenceQualified", "pressureSweepEnabled", "mainApplicationWrites")})); return 0
    except Exception as error:
        print(json.dumps({"status": "APPROXIMATE_PHASE_REFERENCE_INPUT_CORRECTION_REQUIRED", "action": str(error)[:500],
            "simulationExecuted": False, "mainApplicationWrites": False})); return 2


if __name__ == "__main__":
    raise SystemExit(main())
