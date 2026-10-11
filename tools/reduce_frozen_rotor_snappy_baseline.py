"""Reduce real MRF sampling on an independently admitted approximate mesh.

This separate adapter does not impersonate the unchanged original-volume or
tetrahedral routes. Native completion, numerical acceptance on this chosen
classroom approximation and geometric/physical validation are separate facts.
It only reads bounded existing evidence and never launches a native process.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_native_reduction as native
import frozen_rotor_research_protocol as protocol
import run_frozen_rotor_snappy as geometry
import frozen_rotor_surface_union as union

SCHEMA = "untirta.frozen-rotor-snappy-baseline-reduction.v1"
MANIFEST_SCHEMA = "untirta.frozen-rotor-snappy-baseline-input-manifest.v1"
PREPARATION_SCHEMA = "untirta.frozen-rotor-snappy-baseline-preparation.v1"
EXECUTION_SCHEMA = "untirta.frozen-rotor-snappy-baseline-execution.v1"
RUNTIME_SCHEMA = "untirta.frozen-rotor-snappy-baseline-runtime-identity.v1"
GEOMETRY_ROUTE = "source-bound-snappy-approximation"
PROGRAMS = ("mergeMeshes", "createNonConformalCouples", "checkMesh", "foamRun")
MESH_FILES = ("points", "faces", "owner", "neighbour", "boundary", "cellZones")
PRESSURE = 300000.0
ACCEPTANCE = {"samplingWindowIterations": 20, "continuityLocalLimit": 1e-4,
              "interfaceMinimumCoverage": .99, "interfaceAverageCoverage": .999,
              "interfaceMaximumCoverage": 1.01, "interfaceFluxRelativeLimit": .005}
FALSE_CLAIMS = ("qualifiedBaseline", "fullGeometryQualification", "geometricApproximationQualified",
                "surfaceDistanceBoundsVerified", "passageClearanceBoundsVerified", "nativeCoordinateUnitsVerified",
                "nativeNodeIdentityEquivalent", "wallResolutionQualified", "meshConvergenceVerified",
                "timeConvergenceVerified", "cavitationSweepExecuted", "npsh3Qualified", "physicalTransientExecuted",
                "mainApplicationWrites")

require, encoded, read, record = native.require, native.encoded, native.read, native.record


def preparer():
    # Lazy import avoids coupling the existing routes to this new preparation.
    import frozen_rotor_snappy_baseline_case
    return frozen_rotor_snappy_baseline_case


def binary_identity(identity):
    require(isinstance(identity, dict) and set(identity) == {"schemaVersion","openFoamFoundationVersion","packageVersion",
            "officialDebSha256","expectedSourceCommit","binaries","mainApplicationWrites"} and
            identity.get("schemaVersion") == RUNTIME_SCHEMA and
            identity.get("openFoamFoundationVersion") == "12" and identity.get("packageVersion") == "20250206" and
            identity.get("officialDebSha256") == geometry.surface.PACKAGE_SHA and
            identity.get("expectedSourceCommit") == geometry.surface.SOURCE_COMMIT and
            identity.get("mainApplicationWrites") is False,
            "CFD_SNAPPY_EXACT_NATIVE_RUNTIME_REQUIRED")
    rows = identity.get("binaries", [])
    require(isinstance(rows, list) and len(rows) == 4 and {r.get("program") for r in rows} == set(PROGRAMS),
            "CFD_SNAPPY_EXACT_FOUR_NATIVE_BINARIES_REQUIRED")
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"program","path","bytes","sha256","packageOwnership"} and
                isinstance(row.get("bytes"), int) and not isinstance(row["bytes"], bool) and 0 < row["bytes"] <= native.MAX_FIELD and
                protocol.digest(row.get("sha256")), "CFD_SNAPPY_EXACT_BINARY_DESCRIPTOR_REQUIRED")
        pure = PurePosixPath(row.get("path", ""))
        path = Path(row.get("path", ""))
        require(pure.is_absolute() and pure.is_relative_to(PurePosixPath("/opt/openfoam12")) and
                ".." not in pure.parts and "\\" not in row["path"] and pure.name == row["program"] and
                not path.is_symlink() and row.get("packageOwnership") == "openfoam12: " + row["path"],
                "CFD_SNAPPY_OFFICIAL_BINARY_REQUIRED")
        raw = native.case_tool.read_regular(path, native.MAX_FIELD)
        require(len(raw) == row.get("bytes") and sha256(raw).hexdigest() == row.get("sha256"),
                "CFD_SNAPPY_NATIVE_BINARY_IDENTITY_MISMATCH")


def source_identity(root):
    """Stable target surface/recipe identity, not a claim of exact output geometry."""
    source = root / "source-snappy"
    checked = union.verify_output(source / "source-surface")
    preparation, preparation_row = record(root, "source-snappy/snappy-preparation.json")
    execution, execution_row = record(root, "source-snappy/snappy-execution.json")
    require(preparation.get("schemaVersion") == geometry.PREPARATION_SCHEMA and
            execution.get("schemaVersion") == geometry.SCHEMA and
            execution.get("status") == "APPROXIMATE_NATIVE_MESH_CHECKED_GEOMETRY_REVIEW_REQUIRED" and
            all(execution.get(k) is True for k in ("volumeMeshGenerated", "meshQualityCheckedBySolver",
                                                  "nativeCoupledCoverageVerified", "inputArtifactsImmutable")) and
            all(execution.get(k) is False for k in ("geometricApproximationQualified", "fullGeometryQualification",
                "nativeNodeIdentityEquivalent", "nativeCoordinateUnitsVerified", "simulationExecuted", "actualCfdRunVerified", "mainApplicationWrites")),
            "CFD_SNAPPY_CURRENT_NATIVE_GEOMETRY_SOURCE_REQUIRED")
    require(execution.get("preparationReportFile") == {**preparation_row, "path": "snappy-preparation.json"},
            "CFD_SNAPPY_SOURCE_PREPARATION_BINDING_REQUIRED")
    targets = []
    for component in checked["components"]:
        targets.append({"component": component["component"], "surfaceFile": component["surfaceFile"],
                        "patches": [{key: p[key] for key in ("name", "sourceZoneId", "sourceZoneType", "triangleCount")}
                                    for p in component["patches"]]})
    recipe = preparation.get("authoredFileRecords", [])
    require(isinstance(recipe, list) and recipe and len(recipe) <= 64, "CFD_SNAPPY_GENERATOR_RECIPE_REQUIRED")
    for row in recipe:
        require(read(root, "source-snappy/" + row["path"], native.MAX_FIELD)[1] ==
                {**row, "path": "source-snappy/" + row["path"]}, "CFD_SNAPPY_GENERATOR_BYTES_REQUIRED")
    # These technical descriptors exclude extraction/run timestamps, while the
    # complete current raw reports remain immutable manifest evidence below.
    return {"schemaVersion": "untirta.frozen-rotor-snappy-geometry-identity.v1",
            "sourceGeometryRoute": GEOMETRY_ROUTE, "exampleDataCommit": native.case_tool.inventory.PIN,
            "sourceTopologySemanticIdentity": checked["report"]["sourceTopologySemanticIdentity"],
            "sourceStlFiles": union.expected_stls(), "targetSurfaces": targets,
            "generatorRecipeFiles": sorted(recipe, key=lambda r: r["path"]),
            "generatorFoundationSourceCommit": geometry.surface.SOURCE_COMMIT,
            "generatorClassroomAssumptions": preparation["classroomAssumptions"],
            "nativeUnitsToMetres": 1, "nativeCoordinateUnitsVerified": False,
            "geometricApproximationQualified": False, "nativeNodeIdentityEquivalent": False,
            "geometryPolicy": "Pinned float32-derived tagged target surfaces and authored snappy recipe; generated boundary and volume are a distinct approximation. No healing, Hausdorff, passage-clearance or original-node equality claim.",
            "mainApplicationWrites": False}


def source_records(root):
    """All retained source bytes are evidence; their paths do not become controls."""
    source = native.case_tool.regular_directory(root / "source-snappy")
    rows, total = [], 0
    for path in sorted(source.rglob("*")):
        require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)(),
                "CFD_SNAPPY_REGULAR_SOURCE_EVIDENCE_REQUIRED")
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        _, row = read(root, relative, native.MAX_FIELD)
        require(len(rows) < 160 and all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in Path(relative).parts),
                "CFD_SNAPPY_BOUNDED_SOURCE_INVENTORY_REQUIRED")
        total += row["bytes"]
        require(total <= 1024**3, "CFD_SNAPPY_SOURCE_WORKSPACE_BOUND_REQUIRED")
        rows.append(row)
    require(rows, "CFD_SNAPPY_SOURCE_EVIDENCE_REQUIRED")
    return rows


def build_manifest(execution_directory):
    """Read-only packet; caller persists geometry/manifest/study before foamRun."""
    root = native.case_tool.regular_directory(execution_directory)
    helper = preparer()
    verified = helper.verify_output(root, after_native_ncc=True)
    preparation, preparation_row = record(root, "openfoam-preparation.json")
    require(verified["report"] == preparation and preparation.get("schemaVersion") == PREPARATION_SCHEMA and
            preparation.get("exampleDataCommit") == native.case_tool.inventory.PIN and
            preparation.get("mainApplicationWrites") is False and
            preparation.get("nativeCommandArguments") == helper.historical_commands(helper.historical_command_root(preparation)),
            "CFD_SNAPPY_EXACT_BASELINE_PREPARATION_REQUIRED")
    assumptions = preparation["classroomAssumptions"]
    require(assumptions.get("rhoKgM3") == 998.2 and assumptions.get("muPaS") == .001002 and
            assumptions.get("gMS2") == 9.81 and assumptions.get("speedRpm") == 1450 and
            assumptions.get("axis") == [0,0,1] and assumptions.get("massFlowKgS") == 90 and
            assumptions.get("externalTotalPressureAbsolutePa") == PRESSURE and assumptions.get("gravityInSolver") is False and
            assumptions.get("metresPerNativeUnit") == 1 and assumptions.get("nativeUnitsVerified") is False and
            assumptions.get("pressureSelectedBeforeExecution") is True and
            math.isclose(protocol.number(assumptions.get("volumetricFlowM3S"), positive=True), 90/998.2, rel_tol=1e-12),
            "CFD_SNAPPY_FIXED_CLASSROOM_BASIS_REQUIRED")
    identity, runtime_row = record(root, "runtime-identity.json")
    binary_identity(identity)
    geometry_identity = source_identity(root)
    geometry_raw = encoded(geometry_identity)
    geometry_row = {"path": "geometry-identity.json", "bytes": len(geometry_raw), "sha256": sha256(geometry_raw).hexdigest()}
    if (root / geometry_row["path"]).exists():
        require(read(root, geometry_row["path"], native.MAX_RECORD)[0] == geometry_raw,
                "CFD_SNAPPY_CANONICAL_GEOMETRY_IDENTITY_REQUIRED")
    group_paths = {
        "meshSha256": ["impeller-case/constant/polyMesh/" + name for name in MESH_FILES],
        "boundarySha256": ["impeller-case/0/" + name for name in ("U","p","k","omega","nut")] +
                          ["impeller-case/constant/" + name for name in ("MRFProperties","physicalProperties","momentumTransport")],
        "discretizationSha256": ["impeller-case/system/" + name for name in ("fvSchemes","fvSolution","controlDict","createNonConformalCouplesDict")],
        "runtimeSha256": ["runtime-identity.json"]}
    files = [{**preparation_row, "control": None}, {**geometry_row, "control": "geometrySha256"}]
    retained = source_records(root)
    require(retained == sorted(preparation.get("sourceEvidenceFileRecords", []), key=lambda r: r["path"]),
            "CFD_SNAPPY_EXACT_RETAINED_SOURCE_INVENTORY_REQUIRED")
    files.extend({**row, "control": None} for row in retained)
    # The exact original authored 0-field bytes are retained independently;
    # NCC rewriting is verified by the preparer and its current bytes bound here.
    for row in preparation.get("authoredPhysicsFileRecords", []):
        actual = read(root, row["path"], native.MAX_FIELD)[1]
        require(actual == row, "CFD_SNAPPY_IMMUTABLE_AUTHORED_PHYSICS_REQUIRED")
        files.append({**actual, "control": None})
    controls = {"cavitationModelSha256": None, "geometrySha256": sha256(encoded([geometry_row])).hexdigest()}
    for key, paths in group_paths.items():
        rows = []
        for relative in sorted(paths):
            _, row = read(root, relative, native.MAX_FIELD)
            rows.append(row); files.append({**row, "control": key})
        controls[key] = sha256(encoded(rows)).hexdigest()
    bound_paths = {r["path"] for r in files}
    for row in preparation.get("authoredInputFiles", []):
        if row["path"] in bound_paths or "/0/" in row["path"]:
            continue
        actual = read(root, row["path"], native.MAX_FIELD)[1]
        require(actual == row, "CFD_SNAPPY_CURRENT_AUTHORED_DICTIONARY_REQUIRED")
        files.append({**actual, "control": None}); bound_paths.add(row["path"])
    require(len({r["path"] for r in files}) == len(files), "CFD_SNAPPY_UNIQUE_NATIVE_MANIFEST_PATHS_REQUIRED")
    study = protocol.prepare_study(controls=controls, gravity_in_solver=False)
    study["signatureScope"]["geometrySha256"] = "Pinned approximate target surfaces, SI classroom scale and exact generator recipe; no geometric accuracy claim"
    study["signatureScope"]["boundarySha256"] = "Actual post-NCC physical BCs, fixed classroom flow/fluid/MRF configuration; no pressure sweep normalization"
    study["signatureScope"]["discretizationSha256"] = "Exact baseline spatial/linear/iteration controls and selected absolute pressure reference; no physical-time interpretation"
    manifest = {"schemaVersion": MANIFEST_SCHEMA, "sourceGeometryRoute": GEOMETRY_ROUTE,
                "studyInputs": study["input"], "controls": controls, "signatureScope": study["signatureScope"],
                "samplingDefinition": study["samplingDefinition"], "physicalBasis": study["physicalBasis"],
                "physicalModel": "steady-single-phase-MRF", "files": sorted(files, key=lambda r: r["path"]),
                "preparationReportSha256": preparation_row["sha256"], "runtimeIdentitySha256": runtime_row["sha256"],
                "sourceSnappyExecutionReportSha256": preparation["sourceSnappyExecutionReportSha256"],
                "sourceGeometryPreparationReportSha256": preparation["sourceGeometryPreparationReportSha256"],
                "pressureLevelAbsolutePa": PRESSURE, "inletElevationM": 0., "outletElevationM": 0.,
                "elevationBasis": "Explicit classroom common zero datum; section centroids were not verified.",
                "coordinateBasis": "One metre per source unit is a declared classroom assumption, not a verified native unit.",
                "classroomAcceptance": dict(ACCEPTANCE), "geometricApproximationQualified": False,
                "claimBoundary": "Numerical baseline on the exact source-bound approximate mesh only; no geometry, wall, refinement, NPSH3 or physical validation claim.",
                "simulationExecuted": False, "mainApplicationWrites": False}
    require(len(encoded(manifest)) <= native.MAX_RECORD, "CFD_SNAPPY_BOUNDED_INPUT_MANIFEST_REQUIRED")
    return {"study": study, "manifest": manifest, "geometryIdentity": geometry_identity}


def add_record(records, row):
    require(row["path"] not in records or records[row["path"]] == row, "CFD_SNAPPY_EVIDENCE_ROLE_COLLISION")
    records[row["path"]] = row


def evidence_hash(records):
    return sha256(encoded(sorted(records.values(), key=lambda r: r["path"]))).hexdigest()


def endpoint_fields(root, execution, cell_count, records):
    primary = execution.get("finalNativeFields", [])
    require(isinstance(primary, list) and len(primary) == 4 and {r.get("name") for r in primary} == {"p","U","k","omega"},
            "CFD_SNAPPY_ACTUAL_CURRENT_ENDPOINT_FIELDS_REQUIRED")
    scanned = []
    for field in primary:
        raw, row = read(root, "impeller-case/500/" + field["name"], native.MAX_FIELD)
        require(row["bytes"] == field.get("bytes") and row["sha256"] == field.get("sha256"),
                "CFD_SNAPPY_CURRENT_ENDPOINT_FIELD_BINDING_REQUIRED")
        scanned.append(native.finite_field(raw, field["name"], cell_count)); add_record(records, row)
    rows = execution.get("finalNativeFieldRecords", [])
    require(isinstance(rows, list) and rows and len(rows) <= 24 and
            len({r.get("path") for r in rows}) == len(rows) and
            {"impeller-case/500/" + n for n in ("p","U","k","omega","nut")} <= {r.get("path") for r in rows},
            "CFD_SNAPPY_FULL_ENDPOINT_INVENTORY_REQUIRED")
    for row in rows:
        require(re.fullmatch(r"impeller-case/500/(?:p|U|k|omega|nut|totalPressurePa|staticPressurePa|U2|yPlus|phi)", row.get("path", "")),
                "CFD_SNAPPY_REVIEWED_ENDPOINT_FIELD_ROLE_REQUIRED")
        _, actual = read(root, row["path"], native.MAX_FIELD)
        require(actual == row, "CFD_SNAPPY_FULL_ENDPOINT_BYTE_BINDING_REQUIRED")
        add_record(records, actual)
    # Same complete dimensional/finite scans as the native runner, independently
    # recomputed. This function performs no route admission or solver execution.
    import run_frozen_rotor_original as endpoint
    actual_primary, actual_rows, _ = endpoint.final_fields(root, cell_count)
    require(actual_primary == primary and actual_rows == rows, "CFD_SNAPPY_COMPLETE_ENDPOINT_SCAN_REQUIRED")
    return scanned


def execution_evidence(root, manifest, records):
    execution, execution_row = record(root, "openfoam-execution.json")
    require(execution.get("schemaVersion") == EXECUTION_SCHEMA and
            execution.get("status") == "SNAPPY_MRF_EXECUTED_ACCEPTANCE_REQUIRED" and
            execution.get("exampleDataCommit") == native.case_tool.inventory.PIN and
            execution.get("preparationReportSha256") == manifest["preparationReportSha256"] and
            execution.get("runtimeIdentitySha256") == manifest["runtimeIdentitySha256"] and
            execution.get("sourceSnappyExecutionReportSha256") == manifest["sourceSnappyExecutionReportSha256"] and
            all(execution.get(k) is True for k in ("actualCfdRunVerified", "simulationExecuted", "nativeGeometryQualityVerified",
                "physicalWallTypesVerified", "coupledInterfaceCoverageVerified", "nativeSolverCompleted", "finiteNativeFieldsVerified", "inputArtifactsImmutable")) and
            all(execution.get(k) is False for k in FALSE_CLAIMS) and
            execution.get("classroomMetresPerSourceCoordinateUnit") == 1, "CFD_SNAPPY_ACTUAL_NATIVE_COMPLETION_REQUIRED")
    stages = execution.get("stages", [])
    require(isinstance(stages, list) and len(stages) == 4 and [r.get("program") for r in stages] == list(PROGRAMS),
            "CFD_SNAPPY_EXACT_FOUR_NATIVE_STAGES_REQUIRED")
    logs = {}
    for index, stage in enumerate(stages):
        require(stage.get("exitCode") == 0 and not isinstance(stage.get("exitCode"), bool) and
                stage.get("log") == f"{index:02d}-{PROGRAMS[index]}.log", "CFD_SNAPPY_COMPLETED_NATIVE_STAGE_REQUIRED")
        protocol.number(stage.get("elapsedSeconds"), nonnegative=True)
        raw, row = read(root, "logs/" + stage["log"])
        require(row["bytes"] == stage.get("logBytes") and row["sha256"] == stage.get("logSha256"),
                "CFD_SNAPPY_CURRENT_NATIVE_LOG_BINDING_REQUIRED")
        text = raw.decode("utf-8")
        geometry.completed_log(text)
        logs[PROGRAMS[index]] = text; add_record(records, row)
    add_record(records, execution_row)
    preparation = record(root, "openfoam-preparation.json")[0]
    components = preparation["components"]
    measured = geometry.native_mesh(root / "impeller-case", components, logs["checkMesh"])
    require(measured == execution.get("mergedNativeMeshEvidence") and
            measured["cellCount"] == sum(r["cellCount"] for r in components) and
            measured["nodeCount"] == sum(r["nodeCount"] for r in components),
            "CFD_SNAPPY_CURRENT_MERGED_NATIVE_MESH_REQUIRED")
    mesh = native.parse_mesh_quality(logs["checkMesh"])
    require(mesh["meshCellCount"] == measured["cellCount"], "CFD_SNAPPY_NATIVE_CELL_COUNT_REQUIRED")
    fields = endpoint_fields(root, execution, mesh["meshCellCount"], records)
    return execution, logs, mesh, fields


def section(root, name, columns, patch, records, window):
    values, row = native.sampler(root, name, "surfaceFieldValue.dat", columns, patch)
    require(all(i in values for i in window), "CFD_SNAPPY_COMPLETE_CURRENT_SECTION_WINDOW_REQUIRED:" + name)
    add_record(records, row)
    return values


def mapped_section(table, window, rho, pressure):
    for p, pt, u2 in (table[i] for i in window):
        require(u2 >= 0 and math.isclose(pt, rho*p + rho*u2/2 + pressure, rel_tol=2e-5, abs_tol=2),
                "CFD_SNAPPY_ABSOLUTE_TOTAL_PRESSURE_MAPPING_REQUIRED")
    return [math.fsum(table[i][j] for i in window)/len(window) for j in range(3)]


def numerical_observation(root, output, manifest, study, execution, logs, mesh, records):
    """The unchanged engineering criteria consume actual current native values."""
    window = list(range(481, 501))
    rho, g, pv = (study["input"][k] for k in ("densityKgM3", "gravityMS2", "vaporPressurePa"))
    pressure, zi, zo = manifest["pressureLevelAbsolutePa"], manifest["inletElevationM"], manifest["outletElevationM"]
    inlet = section(root, "weightedInlet", native.SAMPLERS["weightedInlet"][1], "inlet", records, window)
    pi, pti, u2i = mapped_section(inlet, window, rho, pressure)
    ps = protocol.number(rho*pi+pressure, positive=True)
    npsha = (ps-pv)/(rho*g)+u2i/(2*g)+zi-study["input"]["suctionDatumM"]
    output["stages"]["npsha"] = protocol.stage(
        {"pStaticAbsolutePa":ps,"pvPa":pv,"meanU2M2S2":u2i,"rhoKgM3":rho,"gMS2":g,"zInM":zi,"zDatumM":study["input"]["suctionDatumM"]},
        "NPSHA = (ps_abs - pv)/(rho*g) + mean(U_absolute^2)/(2*g) + z_in - z_datum",
        f"({ps}-{pv})/({rho}*{g})+{u2i}/(2*{g})+{zi}-{study['input']['suctionDatumM']}", npsha, "m")
    output["npshaDomainStatus"] = "FLASHING_CAVITATION_RISK" if npsha <= 0 or ps <= pv else "NotAssessedByNPSHRComparison"
    outlet = section(root, "weightedOutlet", native.SAMPLERS["weightedOutlet"][1], "mass-flow-inlet-11", records, window)
    _, pto, _ = mapped_section(outlet, window, rho, pressure)
    head = (pto-pti)/(rho*g)+zo-zi
    output["stages"]["head"] = protocol.stage(
        {"ptInPa":pti,"ptOutPa":pto,"rhoKgM3":rho,"gMS2":g,"zInM":zi,"zOutM":zo},
        "H = (pt_out - pt_in)/(rho*g) + z_out - z_in", f"({pto}-{pti})/({rho}*{g})+{zo}-{zi}", head, "m")
    history = native.parse_solver_log(logs["foamRun"])
    require(max(history) == 500 and all(i in history for i in window), "CFD_SNAPPY_ACTUAL_COMPLETE_500_ITERATION_WINDOW_REQUIRED")
    tables = {name: section(root, name, columns, patch, records, window)
              for name, (patch, columns) in native.SAMPLERS.items() if name not in {"weightedInlet", "weightedOutlet"}}
    heads, inlets, outlets, reverse, continuity = [], [], [], [], []
    residuals = {name: [] for name in ("p","U","k","omega")}
    for i in window:
        fi, fo, mi, mo = (tables[k][i][0] for k in ("flowInlet","flowOutlet","flowMagnitudeInlet","flowMagnitudeOutlet"))
        require(fi < 0 < fo and mi >= abs(fi)-1e-10 and mo >= abs(fo)-1e-10, "CFD_SNAPPY_SIGNED_NATIVE_FLUX_REQUIRED")
        reverse.append(max(max(0,(mi+fi)/2)/mi, max(0,(mo-fo)/2)/mo))
        inlets.append(rho*fi); outlets.append(rho*fo)
        heads.append((outlet[i][1]-inlet[i][1])/(rho*g)+zo-zi)
        row = history[i]
        require({"p","Ux","Uy","Uz","k","omega"} <= set(row["residuals"]) and row["continuity"],
                "CFD_SNAPPY_INITIAL_NONLINEAR_RESIDUAL_WINDOW_REQUIRED")
        for field in residuals:
            residuals[field].append(max(row["residuals"][n] for n in ("Ux","Uy","Uz")) if field == "U" else row["residuals"][field])
        continuity.append(row["continuity"][-1])
    mean = lambda v: math.fsum(v)/len(v)
    run = {"studyInputs":study["input"],"controls":manifest["controls"],"samplingDefinition":study["samplingDefinition"],
           "physicalBasis":study["physicalBasis"],"physicalModel":"steady-single-phase-MRF","runRevision":"Current",
           "pressureLevelAbsolutePa":pressure,
           "nativeEvidence":{"kind":"native-solver-sampling","upstreamVerified":True,"sha256":evidence_hash(records),
                             "runId":execution.get("githubRunId") or sha256(encoded(execution)).hexdigest()},
           "controlledVariationProof":{"upstreamVerified":True,"nativeInputManifestSha256":output["nativeInputManifestSha256"],
               "samplingEvidenceSha256":evidence_hash(records),"structuralControls":manifest["controls"],
               "signatureScope":study["signatureScope"],"physicalModel":"steady-single-phase-MRF",
               "pressureBoundaryDefinition":study["controlledPressureDefinition"],"pressureLevelAbsolutePa":pressure,"physicalTimeStepS":None},
           "pressureMapping":{"nativeField":"p","nativeUnit":"m2/s2","absolutePressureReferencePa":pressure,
               "absoluteReconstructionEquation":"p_static_absolute_Pa = rho_liquid*p_kinematic + explicit_absolute_reference_Pa"},
           "measurements":{"inletTotalPressureAbsolutePa":pti,"outletTotalPressureAbsolutePa":pto,
               "inletStaticPressureAbsolutePa":ps,"inletMeanSquaredAbsoluteSpeedM2S2":u2i,"inletElevationM":zi,"outletElevationM":zo},
           "quality":{"nativeMeshQualityChecked":True,**mesh,"outwardInletMassFlowKgS":mean(inlets),"outwardOutletMassFlowKgS":mean(outlets),
               "reverseFlowFraction":max(reverse),"finalResidualHistory":residuals,
               "residualDefinition":"initial-solver-residual-per-outer-iteration-final-window","samplingWindowDefinition":"final-converged-window",
               "finalFlowRelativeSpan":max((max(v)-min(v))/abs(mean(v)) for v in (inlets,outlets)),"headHistoryM":heads}}
    reduction = protocol.reduce_run(study, run)
    output.update(nativeObservation=run, protocolReduction=reduction, samplingIterationWindow=[481,500],
                  iterationIndexIsPhysicalTime=False, continuityHistory=continuity,
                  continuityDefinition="deltaIteration*volumeWeightedMean(abs(div(phi))); independent of boundary mass-flow balance")
    output["diagnostics"].extend(reduction["diagnostics"])
    require(output["npshaDomainStatus"] != "FLASHING_CAVITATION_RISK" and head > 0, "CFD_POSITIVE_HEAD_LIQUID_BASELINE_REQUIRED")
    require(max(c[0] for c in continuity) <= ACCEPTANCE["continuityLocalLimit"], "CFD_NATIVE_CONTINUITY_CONVERGENCE_REQUIRED")
    coverage = native.parse_coverage(logs["checkMesh"] + "\n" + logs["foamRun"])
    output["nativeCoupledCoverage"] = coverage
    require(all(v[0] >= ACCEPTANCE["interfaceMinimumCoverage"] and v[1] >= ACCEPTANCE["interfaceAverageCoverage"] and
                v[2] <= ACCEPTANCE["interfaceMaximumCoverage"] for row in coverage for v in row.values()),
            "CFD_NCC_INTERFACE_COVERAGE_NOT_SATISFIED")
    minimum, row = native.sampler(root,"globalPressureMinimum","volFieldValue.dat",("min(p)",))
    require(all(i in minimum for i in window), "CFD_GLOBAL_PRESSURE_WINDOW_REQUIRED")
    add_record(records,row)
    pmin = min(rho*minimum[i][0]+pressure for i in window)
    output["minimumStaticPressureAbsolutePa"] = pmin
    run["measurements"]["minimumStaticPressureAbsolutePa"] = pmin
    interface = []
    for name, patch in zip(("flowRotorInterface","flowStatorInterface"),native.COUPLED_PATCHES):
        interface.append(section(root,name,("sum(phi)",),patch,records,window))
    mismatch = max(abs(interface[0][i][0]+interface[1][i][0])/max(abs(interface[0][i][0]),abs(interface[1][i][0]),1e-30) for i in window)
    require(all(interface[0][i][0] > 0 > interface[1][i][0] for i in window) and mismatch <= ACCEPTANCE["interfaceFluxRelativeLimit"],
            "CFD_ACTUAL_INTERFACE_FLUX_CONSERVATION_REQUIRED")
    output.update(interfaceFluxRelativeMismatch=mismatch, interfaceCoverageVerified=True, noncavitatingBaselineVerified=pmin>pv)
    run["quality"]["coupledInterfaceCoverageVerified"] = True
    require(pmin > pv, "CFD_NONCAVITATING_BASELINE_REQUIRED")
    run["nativeEvidence"]["sha256"] = evidence_hash(records)
    run["controlledVariationProof"]["samplingEvidenceSha256"] = evidence_hash(records)
    output["protocolReduction"] = protocol.reduce_run(study, run)
    output["numericalBaselineQualified"] = output["protocolReduction"]["status"] == "QualifiedSuppliedObservation"
    output["preliminaryBaselineQualified"] = output["numericalBaselineQualified"]
    # The broad baseline admission used by the exact-geometry routes must not
    # become true from an exploratory numerical solution on this approximation.
    output["qualifiedBaseline"] = False
    output["status"] = "ExploratoryNumericalBaselineQualified" if output["numericalBaselineQualified"] else "NumericalAcceptanceRequired"


def reduce_native_baseline(execution_directory):
    output = {"schemaVersion":SCHEMA,"sourceGeometryRoute":GEOMETRY_ROUTE,"status":"NativeEvidenceRequired",
              "qualifiedBaseline":False,"numericalBaselineQualified":False,"preliminaryBaselineQualified":False,"actualCfdRunVerified":False,
              "simulationExecuted":False,"geometricApproximationQualified":False,"fullGeometryQualification":False,
              "surfaceDistanceBoundsVerified":False,"passageClearanceBoundsVerified":False,"nativeNodeIdentityEquivalent":False,
              "nativeCoordinateUnitsVerified":False,"wallResolutionVerified":False,"yPlusResolutionQualified":False,
              "meshConvergenceVerified":False,"timeStepConvergenceVerified":False,"physicalValidationVerified":False,
              "npsh3Qualified":False,"restartVerified":False,"physicalTransientExecuted":False,"mainApplicationWrites":False,
              "interfaceCoverageVerified":False,"stages":{},"diagnostics":[],
              "qualificationScope":"Exploratory numerical steady SST/MRF baseline on this exact source-bound approximate mesh and declared classroom assumptions only.",
              "claimBoundary":"Native completion does not establish numerical convergence. Numerical qualification does not establish geometric accuracy, wall resolution, mesh/time convergence, manufacturer performance, NPSH3 or restart/physical-transient validity."}
    records = {}
    try:
        root = native.case_tool.regular_directory(execution_directory)
        current = build_manifest(root)
        manifest, manifest_row = record(root,"native-input-manifest.json")
        study, study_row = record(root,"classroom-study.json")
        require(manifest == current["manifest"] and manifest.get("schemaVersion") == MANIFEST_SCHEMA and
                study == current["study"], "CFD_SNAPPY_CURRENT_PREEXECUTION_MANIFEST_REQUIRED")
        require(read(root,"geometry-identity.json",native.MAX_RECORD)[0] == encoded(current["geometryIdentity"]),
                "CFD_SNAPPY_CURRENT_GEOMETRY_IDENTITY_REQUIRED")
        output.update(study=study,inputManifest=manifest,manifest=manifest,nativeInputManifestSha256=manifest_row["sha256"])
        add_record(records,manifest_row); add_record(records,study_row)
        for declared in manifest["files"]:
            _, actual = read(root,declared["path"],native.MAX_FIELD)
            require(actual == {k:declared[k] for k in ("path","bytes","sha256")}, "CFD_SNAPPY_CURRENT_MANIFEST_BYTE_BINDING_REQUIRED")
            add_record(records,actual)
        execution,logs,mesh,fields = execution_evidence(root,manifest,records)
        output.update(actualCfdRunVerified=True,simulationExecuted=True,status="NativeExecutionVerifiedNumericalAcceptanceRequired",finiteNativeFields=fields)
        numerical_observation(root,output,manifest,study,execution,logs,mesh,records)
        require(all(read(root,row["path"],native.MAX_FIELD)[1] == row for row in records.values()),
                "CFD_SNAPPY_POSTREDUCTION_EVIDENCE_IMMUTABILITY_REQUIRED")
    except (protocol.ProtocolError, OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
        output["qualifiedBaseline"] = False; output["numericalBaselineQualified"] = False; output["preliminaryBaselineQualified"] = False
        output["diagnostics"].append({"branch":"approximate-native-baseline-qualification","reasonCode":str(error)[:500]})
        if output["actualCfdRunVerified"]:
            output["status"] = "NumericalAcceptanceRequired"
        if str(error) == "CFD_SNAPPY_POSTREDUCTION_EVIDENCE_IMMUTABILITY_REQUIRED":
            output["actualCfdRunVerified"] = False
            output["status"] = "NativeEvidenceChangedDuringReduction"
    output["nativeEvidenceRecords"] = sorted(records.values(),key=lambda row:row["path"])
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-directory",type=Path,required=True)
    args = parser.parse_args()
    result = reduce_native_baseline(args.execution_directory)
    target = args.execution_directory / "native-baseline-reduction.json"
    raw = encoded(result)
    require(len(raw) <= native.MAX_RECORD, "CFD_SNAPPY_BOUNDED_REDUCTION_REPORT_REQUIRED")
    native.case_tool.write_exclusive(target,raw)
    print(json.dumps({k:result[k] for k in ("status","actualCfdRunVerified","numericalBaselineQualified","qualifiedBaseline","geometricApproximationQualified","mainApplicationWrites")}))
    return 0 if result["actualCfdRunVerified"] and result["numericalBaselineQualified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
