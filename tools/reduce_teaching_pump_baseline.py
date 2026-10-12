"""Reduce byte-bound native SST/MRF evidence for a new authored SI teaching pump.

Only existing files are read. Shared parsers and numerical equations provide no
source admission: the classroom CAD, study and native manifest have new identities.
Native completion, numerical qualification on this mesh, and physical/refinement
validation are independent facts. No solver or Main calculation is launched.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_native_reduction as native
import frozen_rotor_research_protocol as protocol
import reduce_frozen_rotor_snappy_baseline as equations
import run_teaching_pump_geometry as geometry_runner
import teaching_pump_geometry as author

SCHEMA = "untirta.teaching-pump-baseline-acceptance.v1"
MANIFEST_SCHEMA = "untirta.teaching-pump-baseline-input-manifest.v1"
STUDY_SCHEMA = "untirta.teaching-pump-baseline-study.v1"
PREPARATION_SCHEMA = "untirta.teaching-pump-baseline-preparation.v1"
EXECUTION_SCHEMA = "untirta.teaching-pump-baseline-execution.v1"
RUNTIME_SCHEMA = "untirta.teaching-pump-baseline-runtime-identity.v1"
PREPARATION_FILE = "teaching-baseline-preparation.json"
EXECUTION_FILE = "teaching-baseline-execution.json"
RUNTIME_FILE = "teaching-baseline-runtime-identity.json"
MANIFEST_FILE = "teaching-baseline-input-manifest.json"
STUDY_FILE = "teaching-baseline-study.json"
GEOMETRY_FILE = "teaching-geometry-identity.json"
REPORT_FILE = "teaching-baseline-acceptance.json"
ROUTE = "CLASSROOM_AUTHORED_GEOMETRY"
PROGRAMS = ("mergeMeshes", "createNonConformalCouples", "checkMesh", "foamRun")
MESH_FILES = ("points", "faces", "owner", "neighbour", "boundary", "cellZones")
SAMPLE_FILES = {**{name: "surfaceFieldValue.dat" for name in native.SAMPLERS},
    **{name: "surfaceFieldValue.dat" for name in ("flowRotorInterface", "flowStatorInterface", "flowMagnitudeRotorInterface", "flowMagnitudeStatorInterface")},
    "globalPressureMinimum": "volFieldValue.dat", "globalPressureMaximum": "volFieldValue.dat", "wallYPlus": "yPlus.dat"}
PRESSURE = 300000.0
FALSE_CLAIMS = ("qualifiedBaseline", "numericalBaselineQualified", "physicalValidationVerified",
                "wallResolutionVerified", "meshConvergenceVerified", "timeStepConvergenceVerified",
                "cavitationSweepExecuted", "npsh3Qualified", "physicalTransientExecuted",
                "restartVerified", "manufacturerPublishedGeometry", "mainApplicationWrites")
RAW_FALSE_CLAIMS = ("qualifiedBaseline", "numericalBaselineQualified", "wallResolutionQualified",
    "meshConvergenceVerified", "timeConvergenceVerified", "cavitationSweepExecuted", "npsh3Qualified",
    "physicalTransientExecuted", "restartVerified", "manufacturerGeometryVerified", "mainApplicationWrites")
require, encoded, read, record = native.require, native.encoded, native.read, native.record


def preparer():
    import teaching_pump_baseline_case
    return teaching_pump_baseline_case


def binary_identity(value):
    require(isinstance(value, dict) and set(value) == {"schemaVersion", "openFoamFoundationVersion", "packageVersion",
            "officialDebSha256", "expectedSourceCommit", "binaries", "mainApplicationWrites"}
            and value.get("schemaVersion") == RUNTIME_SCHEMA and type(value.get("openFoamFoundationVersion")) is int
            and value["openFoamFoundationVersion"] == 12 and value.get("packageVersion") == "20250206"
            and value.get("officialDebSha256") == geometry_runner.safety.PACKAGE_SHA
            and value.get("expectedSourceCommit") == geometry_runner.safety.SOURCE_COMMIT
            and value.get("mainApplicationWrites") is False, "TEACHING_EXACT_NATIVE_RUNTIME_REQUIRED")
    rows = value.get("binaries")
    require(isinstance(rows, list) and len(rows) == 4 and {r.get("program") for r in rows} == set(PROGRAMS),
            "TEACHING_EXACT_FOUR_NATIVE_BINARIES_REQUIRED")
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"program", "path", "bytes", "sha256", "packageOwnership"}
                and type(row.get("bytes")) is int and 0 < row["bytes"] <= native.MAX_FIELD
                and protocol.digest(row.get("sha256")) and isinstance(row.get("path"), str),
                "TEACHING_NATIVE_BINARY_DESCRIPTOR_REQUIRED")
        pure = PurePosixPath(row["path"])
        require(pure.is_absolute() and pure.is_relative_to(PurePosixPath("/opt/openfoam12")) and ".." not in pure.parts
                and "\\" not in row["path"] and pure.name == row["program"]
                and row.get("packageOwnership") == "openfoam12: " + row["path"], "TEACHING_OFFICIAL_BINARY_REQUIRED")
        raw = native.case_tool.read_regular(Path(row["path"]), native.MAX_FIELD)
        require(len(raw) == row["bytes"] and sha256(raw).hexdigest() == row["sha256"],
                "TEACHING_CURRENT_NATIVE_BINARY_REQUIRED")


def authored_identity(root):
    manifest, _ = record(root, "source-teaching/source-geometry/teaching-input-manifest.json")
    plan = author.teaching_plan(manifest.get("parameters"), manifest.get("meshLevel"))
    identity = manifest.get("geometryIdentity")
    require(manifest.get("schemaVersion") == author.MANIFEST_SCHEMA and manifest.get("provenance") == ROUTE
            and manifest.get("units") == "SI" and identity == plan["geometryIdentity"]
            and manifest.get("geometryIdentitySha256") == plan["geometryIdentitySha256"]
            and identity.get("manufacturerIdentity") is None and identity.get("originalFluentSourceIdentity") is None,
            "TEACHING_EXACT_AUTHORED_SI_GEOMETRY_IDENTITY_REQUIRED")
    return identity


def study_packet(parameters, controls):
    """New classroom source reference; do not call the original example constructor."""
    p = author.validate_parameters(parameters)
    require(all(p[k] == author.DEFAULTS[k] for k in ("massFlowKgS", "speedRpm", "densityKgM3", "viscosityPaS",
            "gravityMS2", "temperatureK", "vaporPressurePa")), "TEACHING_FIXED_BASELINE_PARAMETER_BASIS_REQUIRED")
    inputs = {k: p[k] for k in ("massFlowKgS", "speedRpm", "densityKgM3", "viscosityPaS", "gravityMS2", "temperatureK", "vaporPressurePa")}
    inputs.update(suctionDatumM=0.0, initialAngleRad=0.0)
    scope = {"geometrySha256": "Authored SI CAD parameter/recipe identity; no manufacturer reconstruction",
             "meshSha256": "Exact actual merged native six-file volume mesh",
             "boundarySha256": "Exact current post-NCC BCs and fluid/MRF configuration",
             "discretizationSha256": "Exact steady iteration, schemes and native sampler recipe",
             "runtimeSha256": "Exact four installed package-owned Foundation12 executable identities",
             "cavitationModelSha256": "Null: this steady baseline has no active phase-change model"}
    return {"schemaVersion": STUDY_SCHEMA, "input": inputs, "controls": controls,
            "samplingDefinition": dict(protocol.SAMPLING), "signatureScope": scope,
            "controlledPressureDefinition": "inlet-absolute-total-pressure",
            "physicalBasis": {"rotationAxis": [0, 0, 1], "rotationOriginM": [0, 0, 0], "gravityInSolver": False,
                "gravityVectorMS2": [0, 0, 0], "headGravityReferenceMS2": p["gravityMS2"],
                "elevationDefinition": "Declared zero elevation correction for the gravity-neglected pressure/kinetic model; not measured CAD section elevations"},
            "sourceReference": {"kind": "classroom-authored-SI-pump-CAD", "sourceGeometryRoute": ROUTE,
                "geometryIdentitySchema": author.IDENTITY_SCHEMA, "mainCanonicalReference": False,
                "manufacturerPublished": False, "originalFluentExample": False},
            "declaredAssumptions": {"fluid": "Explicit classroom water properties", "properties": {k: inputs[k] for k in
                ("densityKgM3", "viscosityPaS", "temperatureK", "vaporPressurePa")},
                "propertySource": "Owner-authorised classroom declarations; no property-source or manufacturer validation claim",
                "gravityTreatment": "Native gravity is zero; reported head is pressure/kinetic rise with zero declared elevation correction"},
            "acceptance": {"residualLimit": 1e-4, "massFlowRelativeLimit": .005, "headWindowRelativeLimit": .005,
                "reverseFlowFractionLimit": .005, "liquidSectionVaporFractionLimit": 1e-4,
                "maxCo": 1.0, "maxAlphaCo": .5, "minimumPeriods": 3,
                "minimumStepsPerRevolution": 20, "numericalGciFractionLimit": .01},
            "simulationExecuted": False, "actualCfdRunVerified": False, "mainApplicationWrites": False}


def build_manifest(execution_directory):
    root = native.case_tool.regular_directory(execution_directory)
    helper = preparer()
    verified = helper.verify_output(root, after_native_ncc=True)
    preparation, prep_row = record(root, PREPARATION_FILE)
    require(preparation == verified["report"] and preparation.get("schemaVersion") == PREPARATION_SCHEMA
            and preparation.get("sourceGeometryRoute") == ROUTE and preparation.get("mainApplicationWrites") is False,
            "TEACHING_CURRENT_BASELINE_PREPARATION_REQUIRED")
    runtime, runtime_row = record(root, RUNTIME_FILE)
    binary_identity(runtime)
    identity = authored_identity(root)
    require(preparation.get("sourceGeometryIdentitySha256") == sha256(encoded(identity)).hexdigest(),
            "TEACHING_PREPARATION_GEOMETRY_IDENTITY_REQUIRED")
    require(preparation.get("classroomAssumptions") == helper.ASSUMPTIONS and
            preparation["classroomAssumptions"].get("externalTotalPressureAbsolutePa") == PRESSURE,
            "TEACHING_FIXED_PRESSURE_FLUID_FRAME_REQUIRED")
    geometry_raw = encoded(identity)
    geometry_row = {"path": GEOMETRY_FILE, "bytes": len(geometry_raw), "sha256": sha256(geometry_raw).hexdigest()}
    if (root / GEOMETRY_FILE).exists():
        require(read(root, GEOMETRY_FILE, native.MAX_RECORD)[0] == geometry_raw, "TEACHING_CANONICAL_GEOMETRY_IDENTITY_REQUIRED")
    files = [{**prep_row, "control": None}, {**geometry_row, "control": "geometrySha256"}]
    controls = {"cavitationModelSha256": None, "geometrySha256": sha256(encoded([geometry_row])).hexdigest()}
    groups = {"meshSha256": ["impeller-case/constant/polyMesh/" + n for n in MESH_FILES],
              "boundarySha256": ["impeller-case/0/" + n for n in ("U", "p", "k", "omega", "nut")] +
                  ["impeller-case/constant/" + n for n in ("MRFProperties", "physicalProperties", "momentumTransport")],
              "discretizationSha256": ["impeller-case/system/" + n for n in ("fvSchemes", "fvSolution", "controlDict", "createNonConformalCouplesDict")],
              "runtimeSha256": [RUNTIME_FILE]}
    for key, paths in groups.items():
        rows = [read(root, path, native.MAX_FIELD)[1] for path in sorted(paths)]
        files.extend({**row, "control": key} for row in rows)
        controls[key] = sha256(encoded(rows)).hexdigest()
    source_rows = preparation.get("sourceEvidenceFileRecords", [])
    require(isinstance(source_rows, list) and 0 < len(source_rows) <= 320, "TEACHING_COMPLETE_SOURCE_INVENTORY_REQUIRED")
    total = 0
    for row in source_rows:
        require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}
                and row["path"].startswith("source-teaching/"), "TEACHING_SOURCE_INVENTORY_ROLE_REQUIRED")
        actual = read(root, row["path"], native.MAX_FIELD)[1]
        require(actual == row, "TEACHING_SOURCE_BYTES_CHANGED")
        total += actual["bytes"]; require(total <= 512*1024**2, "TEACHING_SOURCE_BYTES_BOUND_REQUIRED")
        files.append({**actual, "control": None})
    bound = {r["path"] for r in files}
    for key in ("authoredPhysicsFileRecords", "authoredInputFiles"):
        for row in preparation.get(key, []):
            if row["path"] in bound or key == "authoredInputFiles" and "/0/" in row["path"]:
                continue
            actual = read(root, row["path"], native.MAX_FIELD)[1]
            require(actual == row, "TEACHING_AUTHORED_PHYSICS_BYTES_CHANGED")
            files.append({**actual, "control": None}); bound.add(row["path"])
    require(len({r["path"] for r in files}) == len(files), "TEACHING_UNIQUE_MANIFEST_PATHS_REQUIRED")
    source_manifest = record(root, "source-teaching/source-geometry/teaching-input-manifest.json")[0]
    source_execution = record(root, "source-teaching/teaching-native-execution.json")[0]
    require(source_execution.get("githubRepository") == "simcalc/PyAnsys"
            and isinstance(source_execution.get("githubRunId"), str) and re.fullmatch(r"[1-9][0-9]{0,19}", source_execution["githubRunId"])
            and source_execution.get("githubSourceCommit") == source_manifest.get("generatorSourceCommit")
            and isinstance(source_execution.get("githubSourceCommit"), str) and re.fullmatch(r"[0-9a-f]{40}", source_execution["githubSourceCommit"]),
            "TEACHING_ACTUAL_SAME_RUN_SOURCE_IDENTITY_REQUIRED")
    study = study_packet(source_manifest["parameters"], controls)
    manifest = {"schemaVersion": MANIFEST_SCHEMA, "sourceGeometryRoute": ROUTE, "studyInputs": study["input"],
        "controls": controls, "signatureScope": study["signatureScope"], "samplingDefinition": study["samplingDefinition"],
        "physicalBasis": study["physicalBasis"], "physicalModel": "steady-single-phase-MRF", "files": sorted(files, key=lambda r: r["path"]),
        "preparationReportSha256": prep_row["sha256"], "runtimeIdentitySha256": runtime_row["sha256"],
        "sourceGeometryExecutionReportSha256": preparation["sourceGeometryExecutionReportSha256"],
        "sourceGithubRunId": source_execution["githubRunId"], "sourceGeneratorCommit": source_execution["githubSourceCommit"],
        "pressureLevelAbsolutePa": PRESSURE, "inletElevationM": 0.0, "outletElevationM": 0.0,
        "elevationBasis": study["physicalBasis"]["elevationDefinition"], "coordinateBasis": "Explicit authored SI metres",
        "classroomAcceptance": dict(equations.ACCEPTANCE), "simulationExecuted": False, "mainApplicationWrites": False}
    require(len(encoded(manifest)) <= native.MAX_RECORD, "TEACHING_BOUNDED_INPUT_MANIFEST_REQUIRED")
    return {"geometryIdentity": identity, "manifest": manifest, "study": study}


def teaching_endpoint_fields(root, execution, cell_count, records):
    """Replay only the teaching writer roles; legacy source readers stay strict."""
    import run_teaching_pump_baseline as runner
    primary, rows, scans = runner.final_fields(root, cell_count)
    require(len(primary) == 4 and {row["name"] for row in primary} == {"p", "U", "k", "omega"}
            and 5 <= len(rows) <= 24 and len({row["path"] for row in rows}) == len(rows)
            and primary == execution.get("finalNativeFields") and rows == execution.get("finalNativeFieldRecords")
            and scans == execution.get("finiteNativeFieldEvidence"), "TEACHING_CURRENT_COMPLETE_ENDPOINT_SCAN_REQUIRED")
    for row in rows:
        require(read(root, row["path"], native.MAX_FIELD)[1] == row, "TEACHING_CURRENT_ENDPOINT_BYTE_BINDING_REQUIRED")
        equations.add_record(records, row)
    return [scan for scan in scans if scan["name"] in {"p", "U", "k", "omega"}]


def execution_evidence(root, manifest, records):
    import run_teaching_pump_baseline as runner
    execution, row = record(root, EXECUTION_FILE)
    elapsed = protocol.number(execution.get("elapsedSeconds"), nonnegative=True)
    declared_limits = execution.get("limits")
    require(isinstance(declared_limits, dict) and set(declared_limits) == set(runner.LIMITS)
            and all(type(declared_limits[key]) is type(value) and declared_limits[key] == value for key, value in runner.LIMITS.items())
            and elapsed < runner.LIMITS["maximumSeconds"]
            and type(execution.get("workspaceBytes")) is int and 0 < execution["workspaceBytes"] <= runner.LIMITS["maximumWorkspaceBytes"],
            "TEACHING_EXACT_BOUNDED_NATIVE_EXECUTION_PROFILE_REQUIRED")
    # The native producer measures before writing its immutable raw report.
    # This reducer's later report is excluded to avoid a circular SHA/size gate.
    current_bytes = runner.workspace_size(root) - row["bytes"]
    if (root / REPORT_FILE).exists(): current_bytes -= read(root, REPORT_FILE, native.MAX_RECORD)[1]["bytes"]
    require(current_bytes == execution["workspaceBytes"], "TEACHING_CURRENT_NATIVE_WORKSPACE_MEASUREMENT_REQUIRED")
    require(execution.get("schemaVersion") == EXECUTION_SCHEMA and execution.get("status") == "TEACHING_MRF_EXECUTED_NUMERICAL_ACCEPTANCE_REQUIRED"
            and execution.get("sourceGeometryRoute") == ROUTE
            and execution.get("githubRepository") == "simcalc/PyAnsys" and execution.get("githubRunId") == manifest["sourceGithubRunId"]
            and execution.get("githubSourceCommit") == manifest["sourceGeneratorCommit"]
            and execution.get("preparationReportSha256") == manifest["preparationReportSha256"]
            and execution.get("runtimeIdentitySha256") == manifest["runtimeIdentitySha256"]
            and execution.get("sourceGeometryExecutionReportSha256") == manifest["sourceGeometryExecutionReportSha256"]
            and all(execution.get(k) is True for k in ("actualCfdRunVerified", "simulationExecuted", "nativeGeometryQualityVerified",
                "physicalWallTypesVerified", "coupledInterfaceCoverageVerified", "nativeSolverCompleted", "finiteNativeFieldsVerified", "inputArtifactsImmutable", "sourceGeometryImmutable"))
            and all(execution.get(k) is False for k in RAW_FALSE_CLAIMS), "TEACHING_ACTUAL_NATIVE_EXECUTION_REQUIRED")
    for key, path in (("inputManifestSha256", MANIFEST_FILE), ("studySha256", STUDY_FILE), ("geometryIdentitySha256", GEOMETRY_FILE)):
        require(execution.get(key) == read(root, path, native.MAX_RECORD)[1]["sha256"], "TEACHING_RAW_PREEXECUTION_PACKET_BINDING_REQUIRED")
    stages = execution.get("stages", [])
    require(isinstance(stages, list) and len(stages) == 4 and [s.get("program") for s in stages] == list(PROGRAMS), "TEACHING_FOUR_NATIVE_STAGES_REQUIRED")
    logs = {}
    for i, stage in enumerate(stages):
        require(type(stage.get("exitCode")) is int and stage["exitCode"] == 0 and stage.get("log") == f"{i:02d}-{PROGRAMS[i]}.log", "TEACHING_COMPLETED_NATIVE_STAGE_REQUIRED")
        require(protocol.number(stage.get("elapsedSeconds"), nonnegative=True) < runner.LIMITS["maximumSeconds"], "TEACHING_BOUNDED_NATIVE_STAGE_TIME_REQUIRED")
        raw, actual = read(root, "logs/" + stage["log"])
        require(actual["bytes"] == stage.get("logBytes") and actual["sha256"] == stage.get("logSha256"), "TEACHING_CURRENT_LOG_BYTES_REQUIRED")
        text = raw.decode("utf-8"); geometry_runner.native_geometry.completed_log(text)
        logs[PROGRAMS[i]] = text; equations.add_record(records, actual)
    require(sum(stage["elapsedSeconds"] for stage in stages) <= elapsed + .01, "TEACHING_ACTUAL_SERIAL_STAGE_TIME_REQUIRED")
    equations.add_record(records, row)
    prep = record(root, PREPARATION_FILE)[0]
    geometry_runner.verify_region_count(logs["checkMesh"], 2)
    measured = native.case_tool.verify_merged_mesh(root / "impeller-case", prep["components"], root / "logs/02-checkMesh.log")
    require(measured == execution.get("mergedNativeMeshEvidence"), "TEACHING_CURRENT_MERGED_NATIVE_MESH_REQUIRED")
    mesh = native.parse_mesh_quality(logs["checkMesh"])
    require(mesh["meshCellCount"] == measured["mergedCellCount"], "TEACHING_CURRENT_NATIVE_CELL_COUNT_REQUIRED")
    fields = teaching_endpoint_fields(root, execution, mesh["meshCellCount"], records)
    sampling = execution.get("nativeSamplingRecords")
    require(isinstance(sampling, list) and sampling and len(sampling) <= 128 and len({r.get("path") for r in sampling}) == len(sampling), "TEACHING_NATIVE_SAMPLING_INVENTORY_REQUIRED")
    for row in sampling:
        matched = re.fullmatch(r"impeller-case/postProcessing/([A-Za-z][A-Za-z0-9]+)/[01]/([^/]+)", row.get("path", ""))
        require(matched and SAMPLE_FILES.get(matched[1]) == matched[2], "TEACHING_REVIEWED_NATIVE_SAMPLER_PATH_REQUIRED")
        actual = read(root, row["path"])[1]; require(actual == row, "TEACHING_NATIVE_SAMPLER_BYTES_REQUIRED")
        equations.add_record(records, actual)
    require(runner.sampling_records(root) == sampling, "TEACHING_COMPLETE_NATIVE_SAMPLING_INVENTORY_REQUIRED")
    diagnostics = execution.get("nativeNccDiagnosticRecords", [])
    require(isinstance(diagnostics, list) and diagnostics == runner.ncc_diagnostic_records(root),
            "TEACHING_COMPLETE_NCC_DIAGNOSTIC_INVENTORY_REQUIRED")
    for row in diagnostics:
        actual = read(root, row["path"], native.MAX_FIELD)[1]
        require(actual == row, "TEACHING_CURRENT_NCC_DIAGNOSTIC_BYTES_REQUIRED")
        equations.add_record(records, actual)
    near = runner.near_points_evidence(root)
    require(execution.get("nativeCheckMeshDiagnosticRecords", []) == near["files"]
            and execution.get("nativeNearPointsEvidence") == near["evidence"],
            "TEACHING_COMPLETE_CURRENT_NEAR_POINTS_DIAGNOSTIC_REQUIRED")
    for row in near["files"]:
        actual = read(root, row["path"], native.MAX_FIELD)[1]
        require(actual == row, "TEACHING_CURRENT_NEAR_POINTS_DIAGNOSTIC_BYTES_REQUIRED")
        equations.add_record(records, actual)
    return execution, logs, mesh, fields


def independent_native_diagnostics(execution_directory, *, study=None, manifest=None, logs=None, records=None):
    """Read independent current metrics without granting native/source acceptance.

    In particular, a negative head does not remove the available global liquid
    domain or interface observations. This data projection may inspect a retained
    rejected run; it never substitutes for installed-runtime/source admission.
    """
    root = native.case_tool.regular_directory(execution_directory)
    bound = {} if records is None else records
    local_records = {}
    window = list(range(481, 501))
    output = {"schemaVersion": "untirta.teaching-pump-independent-native-diagnostics.v1",
        "samplingIterationWindow": [481, 500], "iterationIndexIsPhysicalTime": False,
        "actualCfdRunVerified": False, "simulationExecuted": False, "numericalBaselineQualified": False,
        "qualifiedBaseline": False, "installedRuntimeReverified": False,
        "mainApplicationWrites": False, "branches": {}, "diagnostics": [],
        "claimBoundary": "Current byte-bound native metric projection only; source, runtime, head and baseline qualification remain separate."}

    def bind_row(row):
        equations.add_record(local_records, row)
        equations.add_record(bound, row)

    def bound_record(path):
        value, row = record(root, path)
        bind_row(row)
        return value

    current_study = bound_record(STUDY_FILE)
    current_manifest = bound_record(MANIFEST_FILE)
    require((study is None or study == current_study) and (manifest is None or manifest == current_manifest)
            and current_study.get("schemaVersion") == STUDY_SCHEMA
            and current_manifest.get("schemaVersion") == MANIFEST_SCHEMA
            and current_manifest.get("sourceGeometryRoute") == ROUTE
            and current_study.get("sourceReference", {}).get("kind") == "classroom-authored-SI-pump-CAD"
            and current_manifest.get("studyInputs") == current_study.get("input"),
            "TEACHING_CURRENT_DIAGNOSTIC_STUDY_BINDING_REQUIRED")
    study, manifest = current_study, current_manifest
    execution = bound_record(EXECUTION_FILE)
    require(execution.get("schemaVersion") == EXECUTION_SCHEMA
            and execution.get("inputManifestSha256") == bound[MANIFEST_FILE]["sha256"]
            and execution.get("studySha256") == bound[STUDY_FILE]["sha256"],
            "TEACHING_CURRENT_DIAGNOSTIC_PREEXECUTION_BINDING_REQUIRED")

    def bind_data(row, key):
        rows = execution.get(key)
        require(isinstance(rows, list) and len([r for r in rows if r.get("path") == row["path"]]) == 1
                and next(r for r in rows if r.get("path") == row["path"]) == row,
                "TEACHING_CURRENT_DIAGNOSTIC_NATIVE_DATA_BINDING_REQUIRED")
        bind_row(row)

    def native_units(path, name, kind, dimensions, *, input_field=False):
        raw, row = read(root, path, native.MAX_FIELD)
        text = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw.decode("ascii"), flags=re.S)
        require(len(re.findall(r"\bdimensions\b", text)) == 1
                and re.search(r"\bdimensions\s*\[\s*" + dimensions + r"\s*\]\s*;", text)
                and re.search(r"\bformat\s+ascii\s*;", text)
                and re.search(r"\bclass\s+" + kind + r"\s*;", text)
                and re.search(r"\bobject\s+" + name + r"\s*;", text),
                "TEACHING_CURRENT_DIAGNOSTIC_NATIVE_SI_FIELD_REQUIRED")
        if input_field:
            rows = manifest.get("files", [])
            expected = [r for r in rows if r.get("path") == path]
            require(len(expected) == 1 and row == {k: expected[0][k] for k in ("path", "bytes", "sha256")},
                    "TEACHING_CURRENT_DIAGNOSTIC_NATIVE_INPUT_BYTES_REQUIRED")
            bind_row(row)
        else:
            bind_data(row, "finalNativeFieldRecords")

    def current_log(program, path):
        raw, row = read(root, path)
        text = raw.decode("utf-8")
        stages = execution.get("stages", [])
        stage = [s for s in stages if s.get("program") == program]
        require(len(stage) == 1 and stage[0].get("log") == PurePosixPath(path).name
                and stage[0].get("logBytes") == row["bytes"] and stage[0].get("logSha256") == row["sha256"]
                and (logs is None or logs.get(program) == text),
                "TEACHING_CURRENT_DIAGNOSTIC_NATIVE_LOG_BINDING_REQUIRED")
        bind_row(row)
        return text

    def attempt(name, function):
        try:
            result = function()
            result.update(evidenceAvailable=True, status="CurrentSuppliedNativeMetric")
            output["branches"][name] = result
            if not result["criterionSatisfied"]:
                output["diagnostics"].append({"branch": name, "reasonCode": result["reasonCode"]})
        except (protocol.ProtocolError, OSError, ValueError, KeyError, TypeError, UnicodeError, StopIteration) as error:
            output["branches"][name] = {"evidenceAvailable": False, "criterionSatisfied": False,
                "status": "DiagnosticRequired", "reasonCode": str(error)[:500]}
            output["diagnostics"].append({"branch": name, "reasonCode": str(error)[:500]})

    def pressure_domain():
        native_units("impeller-case/0/p", "p", "volScalarField", "0 2 -2 0 0 0 0", input_field=True)
        rho = protocol.number(study["input"]["densityKgM3"], positive=True)
        pv = protocol.number(study["input"]["vaporPressurePa"], positive=True)
        pressure = protocol.number(manifest["pressureLevelAbsolutePa"], positive=True)
        require(rho == 998.2 and pv == 2339.0 and pressure == PRESSURE,
                "TEACHING_DECLARED_DIAGNOSTIC_ABSOLUTE_PRESSURE_BASIS_REQUIRED")
        table, row = native.sampler(root, "globalPressureMinimum", "volFieldValue.dat", ("min(p)",))
        bind_data(row, "nativeSamplingRecords")
        require(all(i in table for i in window), "CFD_GLOBAL_PRESSURE_WINDOW_REQUIRED")
        values = [protocol.number(rho*table[i][0] + pressure) for i in window]
        minimum = min(values)
        return {"criterionSatisfied": minimum > pv, "reasonCode": "CFD_NONCAVITATING_BASELINE_REQUIRED",
            "pressureDomainStatus": "SinglePhaseLiquidDomainSatisfied" if minimum > pv else "FLASHING_CAVITATION_RISK",
            "minimumStaticPressureAbsolutePa": minimum, "vapourPressurePa": pv,
            "minimumPressureHistoryAbsolutePa": values,
            "stage": protocol.stage({"rhoKgM3": rho, "nativeMinimumPressureHistoryM2S2": [table[i][0] for i in window],
                "explicitAbsolutePressureReferencePa": pressure, "vapourPressurePa": pv},
                "p_min_absolute = min(rho*p_min_kinematic + p_reference); liquid domain requires p_min_absolute > p_v",
                f"min({rho}*p_min_kinematic + {pressure}) = {minimum}; {minimum} > {pv}", minimum, "Pa")}

    def coverage():
        values = native.parse_coverage(current_log("checkMesh", "logs/02-checkMesh.log") + "\n" +
            current_log("foamRun", "logs/03-foamRun.log"))
        limits = equations.ACCEPTANCE
        passed = all(v[0] >= limits["interfaceMinimumCoverage"] and v[1] >= limits["interfaceAverageCoverage"]
            and v[2] <= limits["interfaceMaximumCoverage"] for row in values for v in row.values())
        return {"criterionSatisfied": passed, "reasonCode": "CFD_NCC_INTERFACE_COVERAGE_NOT_SATISFIED",
            "nativeCoupledCoverage": values, "criteria": {k: limits[k] for k in
                ("interfaceMinimumCoverage", "interfaceAverageCoverage", "interfaceMaximumCoverage")},
            "equation": "For every source/target record: minimum >= 0.99, average >= 0.999, maximum <= 1.01",
            "unit": "dimensionless"}

    def interface_flux():
        native_units("impeller-case/500/phi", "phi", "surfaceScalarField", "0 3 -1 0 0 0 0")
        tables = []
        for name, patch in zip(("flowRotorInterface", "flowStatorInterface"), native.COUPLED_PATCHES):
            table, row = native.sampler(root, name, "surfaceFieldValue.dat", ("sum(phi)",), patch)
            bind_data(row, "nativeSamplingRecords")
            require(all(i in table for i in window), "CFD_SNAPPY_COMPLETE_CURRENT_SECTION_WINDOW_REQUIRED:" + name)
            tables.append(table)
        rotor, stator = ([table[i][0] for i in window] for table in tables)
        differences = [abs(a+b)/max(abs(a), abs(b), 1e-30) for a, b in zip(rotor, stator)]
        mismatch = max(differences)
        limit = equations.ACCEPTANCE["interfaceFluxRelativeLimit"]
        signs = all(a > 0 > b for a, b in zip(rotor, stator))
        return {"criterionSatisfied": signs and mismatch <= limit,
            "reasonCode": "CFD_ACTUAL_INTERFACE_FLUX_CONSERVATION_REQUIRED", "outwardFluxSignsSatisfied": signs,
            "rotorOutwardVolumeFlowHistoryM3S": rotor, "statorOutwardVolumeFlowHistoryM3S": stator,
            "maximumInterfaceFluxRelativeMismatch": mismatch, "relativeMismatchLimit": limit,
            "stage": protocol.stage({"rotorOutwardVolumeFlowHistoryM3S": rotor, "statorOutwardVolumeFlowHistoryM3S": stator,
                "denominatorFloorM3S": 1e-30, "relativeLimit": limit},
                "epsilon_interface = max(abs(Q_rotor + Q_stator)/max(abs(Q_rotor), abs(Q_stator), 1e-30)); Q_rotor > 0 > Q_stator",
                f"max(abs(Q_rotor + Q_stator)/max(abs(Q_rotor), abs(Q_stator), 1e-30)) = {mismatch}; limit = {limit}",
                mismatch, "dimensionless")}

    attempt("globalPressureDomain", pressure_domain)
    attempt("interfaceCoverage", coverage)
    attempt("interfaceFluxConservation", interface_flux)
    # This remains mandatory even when a metric branch caught a missing file or
    # failed criterion; no prior valid byte is silently carried forward.
    require(all(read(root, row["path"], native.MAX_FIELD)[1] == row for row in bound.values()),
            "TEACHING_EVIDENCE_CHANGED_DURING_REDUCTION")
    output["allIndependentCriteriaSatisfied"] = all(r["criterionSatisfied"] for r in output["branches"].values())
    output["currentEvidenceVerified"] = True
    output["nativeEvidenceRecords"] = sorted(local_records.values(), key=lambda r: r["path"])
    output["nativeEvidenceSha256"] = sha256(encoded(output["nativeEvidenceRecords"])).hexdigest()
    return output


def reduce_baseline(execution_directory):
    output = {"schemaVersion": SCHEMA, "sourceGeometryRoute": ROUTE, "status": "ActualNativeEvidenceRequired",
        **{k: False for k in FALSE_CLAIMS}, "preliminaryBaselineQualified": False, "actualCfdRunVerified": False,
        "simulationExecuted": False, "interfaceCoverageVerified": False, "noncavitatingBaselineVerified": False,
        "stages": {}, "diagnostics": [], "qualificationScope": "Exploratory numerical steady SST/MRF baseline on this exact authored SI classroom mesh only.",
        "headQuantityScope": "Gravity-neglected pressure/kinetic head rise with declared zero elevation correction; not a measured static-lift or manufacturer performance result",
        "claimBoundary": "No native solver is run by this reducer. Numerical acceptance does not establish wall resolution, mesh/time convergence, physical pump validity, NPSHr, NPSH3 or restart."}
    records = {}
    try:
        root = native.case_tool.regular_directory(execution_directory)
        current = build_manifest(root)
        manifest, manifest_row = record(root, MANIFEST_FILE); study, study_row = record(root, STUDY_FILE)
        require(manifest == current["manifest"] and study == current["study"]
                and read(root, GEOMETRY_FILE, native.MAX_RECORD)[0] == encoded(current["geometryIdentity"]), "TEACHING_CURRENT_PREEXECUTION_PACKET_REQUIRED")
        output.update(manifest=manifest, inputManifest=manifest, study=study, nativeInputManifestSha256=manifest_row["sha256"])
        equations.add_record(records, manifest_row); equations.add_record(records, study_row)
        for declared in manifest["files"]:
            actual = read(root, declared["path"], native.MAX_FIELD)[1]
            require(actual == {k: declared[k] for k in ("path", "bytes", "sha256")}, "TEACHING_CURRENT_MANIFEST_BYTES_REQUIRED")
            equations.add_record(records, actual)
        execution, logs, mesh, fields = execution_evidence(root, manifest, records)
        output.update(actualCfdRunVerified=True, simulationExecuted=True, finiteNativeFields=fields,
                      status="ActualNativeExecutionVerifiedNumericalAcceptanceRequired")
        output["independentDiagnostics"] = independent_native_diagnostics(root, study=study, manifest=manifest,
            logs=logs, records=records)
        output["diagnostics"].extend(output["independentDiagnostics"]["diagnostics"])
        pressure_branch = output["independentDiagnostics"]["branches"]["globalPressureDomain"]
        if pressure_branch["evidenceAvailable"]:
            output["minimumStaticPressureAbsolutePa"] = pressure_branch["minimumStaticPressureAbsolutePa"]
        coverage_branch = output["independentDiagnostics"]["branches"]["interfaceCoverage"]
        if coverage_branch["evidenceAvailable"]:
            output["nativeCoupledCoverage"] = coverage_branch["nativeCoupledCoverage"]
        flux_branch = output["independentDiagnostics"]["branches"]["interfaceFluxConservation"]
        if flux_branch["evidenceAvailable"]:
            output["interfaceFluxRelativeMismatch"] = flux_branch["maximumInterfaceFluxRelativeMismatch"]
        # This shared function reads only native tables/logs and applies the
        # unchanged head/NPSHA/conservation/residual/pressure/NCC equations. It
        # does not run its old source, schema or study admission constructors.
        try:
            equations.numerical_observation(root, output, manifest, study, execution, logs, mesh, records)
        finally:
            require(all(read(root, row["path"], native.MAX_FIELD)[1] == row for row in records.values()),
                    "TEACHING_EVIDENCE_CHANGED_DURING_REDUCTION")
        output["qualifiedBaseline"] = False
        output["status"] = "AuthoredNumericalBaselineQualified" if output["numericalBaselineQualified"] else "NumericalAcceptanceRequired"
        require(all(read(root, row["path"], native.MAX_FIELD)[1] == row for row in records.values()), "TEACHING_EVIDENCE_CHANGED_DURING_REDUCTION")
    except (protocol.ProtocolError, OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
        output.update(qualifiedBaseline=False, numericalBaselineQualified=False, preliminaryBaselineQualified=False)
        output["diagnostics"].append({"branch": "authored-native-baseline-qualification", "reasonCode": str(error)[:500]})
        if output["actualCfdRunVerified"]: output["status"] = "NumericalAcceptanceRequired"
        if str(error) == "TEACHING_EVIDENCE_CHANGED_DURING_REDUCTION":
            output.update(actualCfdRunVerified=False, simulationExecuted=False, status="NativeEvidenceChangedDuringReduction")
            if "independentDiagnostics" in output:
                independent = output["independentDiagnostics"]
                independent.update(currentEvidenceVerified=False, allIndependentCriteriaSatisfied=False)
                for branch in independent["branches"].values():
                    branch.update(evidenceAvailable=False, criterionSatisfied=False, status="NativeEvidenceChangedDuringReduction")
                for name in ("minimumStaticPressureAbsolutePa", "nativeCoupledCoverage", "interfaceFluxRelativeMismatch"):
                    output.pop(name, None)
    output["nativeEvidenceRecords"] = sorted(records.values(), key=lambda r: r["path"])
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--execution-directory", type=Path, required=True)
    args = parser.parse_args()
    # Only the explicit fresh runner-owned lane may receive the CLI report.
    # The read-only function above writes nothing, including on invalid input.
    target = preparer().safety.scope_path(args.execution_directory)
    result = reduce_baseline(target)
    raw = encoded(result); require(len(raw) <= native.MAX_RECORD, "TEACHING_BOUNDED_ACCEPTANCE_REPORT_REQUIRED")
    native.case_tool.write_exclusive(target / REPORT_FILE, raw)
    print(json.dumps({k: result[k] for k in ("status", "actualCfdRunVerified", "numericalBaselineQualified", "qualifiedBaseline", "mainApplicationWrites")}))
    return 0 if result["actualCfdRunVerified"] and result["numericalBaselineQualified"] else 2


if __name__ == "__main__": raise SystemExit(main())
