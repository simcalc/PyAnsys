"""Read actual Foundation-12 phase evidence without launching a native solver.

The caller persists the exact input manifest before execution. This reducer
can qualify one educational phase observation; a same-family high-pressure
reference, matched pressure bracket, refinement and restart remain separate.
Synthetic unit-test files are never research evidence.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_cavitation_case as campaign
import frozen_rotor_native_reduction as native
import frozen_rotor_research_protocol as protocol

SCHEMA = "untirta.frozen-rotor-phase-input-manifest.v1"
REDUCTION_SCHEMA = "untirta.frozen-rotor-phase-native-reduction.v1"
MODEL_FILES = {"constant/fvModels", "constant/phaseProperties", "constant/physicalProperties.water", "constant/physicalProperties.vapour"}
MAX_LOG = 8 * 1024**2
MAX_REDUCTION_BYTES = 16 * 1024**2
MAX_ROWS = 100_000
TIME_NAME = r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?"
encoded = native.encoded
require = native.require


def phase_binary_identity(identity):
    """Recheck actual two-program identities, not an installation declaration."""
    require(identity.get("schemaVersion") == "untirta.frozen-rotor-phase-runtime-identity.v1"
            and identity.get("openFoamFoundationVersion") == 12 and identity.get("packageVersion") == "20250206"
            and identity.get("officialDebSha256") == "b3dcd9a636a5a827bf43ffcf0156922ba6db492123f035eae1046ee759602dd1"
            and identity.get("mainApplicationWrites") is False, "CFD_PHASE_NATIVE_RUNTIME_IDENTITY_REQUIRED")
    binaries = identity.get("binaries", [])
    require(len(binaries) == 2 and {row.get("program") for row in binaries} == {"checkMesh", "foamRun"},
            "CFD_PHASE_EXACT_NATIVE_BINARY_INVENTORY_REQUIRED")
    for row in binaries:
        path = Path(row.get("path", ""))
        require(str(path).startswith("/opt/openfoam12/") and not path.is_symlink(), "CFD_OFFICIAL_NATIVE_BINARY_REQUIRED")
        raw = campaign.base.read_regular(path, native.MAX_FIELD)
        require(row.get("bytes") == len(raw) and row.get("sha256") == sha256(raw).hexdigest(), "CFD_NATIVE_BINARY_IDENTITY_MISMATCH")


def build_manifest(execution_directory):
    """Return {study, manifest}; caller writes both before checkMesh/foamRun."""
    root = campaign.base.regular_directory(execution_directory)
    prep, prep_row = native.record(root, "phase-change-preparation.json")
    require(prep.get("schemaVersion") == campaign.SCHEMA and prep.get("exampleDataCommit") == campaign.base.inventory.PIN
            and prep.get("mainApplicationWrites") is False and prep.get("simulationExecuted") is False
            and prep.get("mode") in campaign.MODES and protocol.digest(prep.get("sourceBaselineEvidenceSha256"))
            and protocol.digest(prep.get("sourceCaseManifestSha256")), "CFD_SOURCE_PINNED_PHASE_PREPARATION_REQUIRED")
    declared = prep.get("declaredStudyInputs")
    source_study = protocol.prepare_study(inputs=declared, gravity_in_solver=False)
    require(declared == source_study["input"] and prep.get("physicalBasis") == source_study["physicalBasis"]
            and prep.get("nativeCoordinateUnitsVerified") is False and prep.get("classroomMetresPerSourceCoordinateUnit") == 1,
            "CFD_PHASE_FIXED_CLASSROOM_BASIS_REQUIRED")
    pressure = protocol.number(prep.get("inletAbsoluteTotalPressurePa"), positive=True)
    plan = prep.get("temporalPlan", {})
    dt, duration = protocol.number(plan.get("maximumTimeStepS"), positive=True), protocol.number(plan.get("endTimeS"), positive=True)
    period = 60 / declared["speedRpm"]
    mode = prep["mode"]
    require(plan.get("rotationPeriodS") == period and plan.get("physicalRotorMotion") == (mode == "transient-sliding")
            and plan.get("stationaryMrfRelaxation") == (mode == "steady-sweep")
            and plan.get("nativeCoLimit") == .5 and plan.get("nativeAlphaCoLimit") == .25,
            "CFD_PHASE_EXACT_TEMPORAL_PLAN_REQUIRED")
    authored = campaign.native_recipe(source_study, pressure, mode, dt, duration)
    actual_authored = campaign.file_records(root / "case", prep.get("authoredInputFiles"), set(authored))
    require({row["path"] for row in actual_authored} == set(authored), "CFD_COMPLETE_AUTHORED_PHASE_INPUTS_REQUIRED")
    for name, content in authored.items():
        require(native.read(root, "case/" + name, native.MAX_FIELD)[0] == content.encode("ascii"),
                "CFD_AUTHORED_PHASE_INPUT_MISMATCH:" + name)
    mesh_paths = {"constant/polyMesh/" + name for name in campaign.MESH_FILES}
    mesh_rows = campaign.file_records(root / "case", prep.get("preservedNativeMeshFiles"), mesh_paths, plain_native=True)
    require({row["path"] for row in mesh_rows} == mesh_paths, "CFD_COMPLETE_PRESERVED_NATIVE_MESH_REQUIRED")
    identity, identity_row = native.record(root, "geometry-identity.json")
    recorded = prep.get("sourceGeometryIdentityFile", {})
    require(identity.get("schemaVersion") == "untirta.frozen-rotor-fixed-geometry-identity.v1"
            and identity.get("exampleDataCommit") == campaign.base.inventory.PIN and identity.get("mainApplicationWrites") is False
            and recorded.get("name") == "geometry-identity.json" and recorded.get("bytes") == identity_row["bytes"]
            and recorded.get("sha256") == identity_row["sha256"], "CFD_EXACT_PHASE_GEOMETRY_IDENTITY_REQUIRED")
    geometry_control = sha256(encoded([identity_row])).hexdigest()
    require(prep.get("sourceGeometryControlSha256") == geometry_control, "CFD_SOURCE_GEOMETRY_CONTROL_MISMATCH")
    runtime, runtime_row = native.record(root, "phase-runtime-identity.json")
    phase_binary_identity(runtime)
    # Full actual input bytes remain individually bound below. Only explicitly
    # permitted scalar variations use a signature-only sentinel recipe.
    # These normalized bytes are never written as a native case or a result.
    signature_recipe = campaign.native_recipe(source_study, 1.0, mode, period / 360, period * 3)
    group_rows = {key: [] for key in protocol.CONTROL_KEYS}
    group_rows["geometrySha256"] = [identity_row]
    group_rows["meshSha256"] = mesh_rows
    group_rows["runtimeSha256"] = [runtime_row]
    files = [{**prep_row, "control": None}, {**identity_row, "control": "geometrySha256"}, {**runtime_row, "control": "runtimeSha256"}]
    for row in mesh_rows:
        files.append({**row, "path": "case/" + row["path"], "control": "meshSha256"})
    for row in actual_authored:
        name = row["path"]
        key = "cavitationModelSha256" if name in MODEL_FILES else "discretizationSha256" if name.startswith("system/") else "boundarySha256"
        signature_raw = signature_recipe[name].encode("ascii")
        signature_row = {"path": name, "bytes": len(signature_raw), "sha256": sha256(signature_raw).hexdigest()}
        group_rows[key].append(signature_row)
        files.append({**row, "path": "case/" + name, "control": key})
    controls = {key: sha256(encoded(sorted(rows, key=lambda row: row["path"]))).hexdigest() for key, rows in group_rows.items()}
    require(controls["cavitationModelSha256"] == prep.get("authoredCavitationModelSha256"), "CFD_ACTIVE_PHASE_MODEL_SHA_MISMATCH")
    study = protocol.prepare_study(inputs=declared, controls=controls, gravity_in_solver=False)
    manifest = {"schemaVersion": SCHEMA, "files": sorted(files, key=lambda row: row["path"]), "controls": controls,
                "structuralSignatureRows": {key: sorted(rows, key=lambda row: row["path"]) for key, rows in sorted(group_rows.items())},
                "structuralSignatureMethod": "Canonical per-group records. Actual authored bytes checked against the full recipe first; signature-only p0=1 Pa, dt=one degree, duration=three revolutions exclude solely the declared pressure/time scalars. No normalized native execution occurs.",
                "signatureScope": study["signatureScope"], "studyInputs": study["input"], "physicalBasis": study["physicalBasis"],
                "samplingDefinition": study["samplingDefinition"], "physicalModel": "steady-cavitation-MRF" if mode == "steady-sweep" else "transient-cavitation-sliding-mesh",
                "mode": mode, "pressureLevelAbsolutePa": pressure, "temporalPlan": plan,
                "preparationReportSha256": prep_row["sha256"], "runtimeIdentitySha256": runtime_row["sha256"],
                "sourceBaselineEvidenceSha256": prep["sourceBaselineEvidenceSha256"], "sourceBaselineRole": "Single-phase preparation prerequisite only; never the active-phase H0 reference.",
                "inletElevationM": 0.0, "outletElevationM": 0.0,
                "elevationBasis": "Explicit classroom common zero datum; native section centroids were not verified.",
                "classroomAcceptance": {"samplingPeriods": 3, "continuityLocalLimit": 1e-4, "interfaceMinimumCoverage": .99,
                    "interfaceAverageCoverage": .999, "interfaceMaximumCoverage": 1.01, "interfaceFluxRelativeLimit": .005,
                    "nativeCoLimit": .5, "nativeAlphaCoLimit": .25},
                "simulationExecuted": False, "mainApplicationWrites": False}
    return {"study": study, "manifest": manifest}


def parse_table(text, columns, patch=None):
    """Physical seconds, exact scalar header/section and no repeated time rows."""
    headers = re.findall(r"(?m)^#\s*Time\s+(.+)$", text)
    require(len(headers) == 1 and tuple(headers[0].split()) == tuple(columns), "CFD_PHASE_SAMPLING_HEADER_MISMATCH")
    if patch is not None:
        require(re.search(r"(?m)^#\s*Selection\s*:\s*patch\(" + re.escape(patch) + r"\)\s*$", text), "CFD_NATIVE_SAMPLING_SECTION_MISMATCH")
        faces = re.search(r"(?m)^#\s*Faces\s*:\s*([1-9]\d*)\s*$", text)
        area = re.search(r"(?m)^#\s*Area\s*:\s*(" + native.FLOAT + r")\s*$", text)
        require(faces and area and protocol.number(float(area[1]), positive=True), "CFD_NONEMPTY_NATIVE_SECTION_REQUIRED")
    rows = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        values = line.split()
        require(len(values) == len(columns) + 1 and all(re.fullmatch(native.FLOAT, value) for value in values), "CFD_FINITE_NATIVE_SAMPLING_ROW_REQUIRED")
        values = [protocol.number(float(value)) for value in values]
        instant = values.pop(0)
        require(instant >= 0 and (not rows or instant > next(reversed(rows))) and len(rows) < MAX_ROWS, "CFD_ORDERED_BOUNDED_PHYSICAL_SAMPLES_REQUIRED")
        rows[instant] = values
    require(rows, "CFD_NATIVE_SAMPLING_ROWS_REQUIRED")
    return rows


def sampler(root, name, columns, patch=None):
    directory = root / "case/postProcessing" / name
    children = list(directory.iterdir())
    require(len(children) == 1 and re.fullmatch(TIME_NAME, children[0].name)
            and float(children[0].name) >= 0 and children[0].is_dir(), "CFD_ONE_FRESH_PHASE_SAMPLER_DIRECTORY_REQUIRED")
    filename = "surfaceFieldValue.dat" if patch else "volFieldValue.dat"
    raw, row = native.read(root, "case/postProcessing/" + name + "/" + children[0].name + "/" + filename)
    return parse_table(raw.decode("utf-8"), columns, patch), row


def parse_solver_log(text):
    """Keep first initial residual in every PIMPLE outer block, never final residual."""
    require(re.search(r"(?m)^End\s*$", text) and re.search(r"(?m)^Selecting cavitation model SchnerrSauer\s*$", text)
            and not re.search(r"FOAM FATAL|Segmentation fault|Floating point exception", text, re.I), "CFD_COMPLETED_PHASE_SOLVER_REQUIRED")
    rows, current, pending_dt, co, alpha_co = {}, None, None, [], []
    for line in text.splitlines():
        line = line.strip()
        match = re.fullmatch(r"deltaT = (" + native.FLOAT + r")(?:s)?", line)
        if match:
            pending_dt = protocol.number(float(match[1]), positive=True)
        match = re.fullmatch(r"Time = (" + native.FLOAT + r")(?:s)?", line)
        require(not line.startswith("Time = ") or match, "CFD_PHYSICAL_TIME_LOG_FORMAT_REQUIRED")
        if match:
            instant = protocol.number(float(match[1]), nonnegative=True)
            require(instant not in rows and (not rows or instant > next(reversed(rows))) and len(rows) < MAX_ROWS,
                    "CFD_ORDERED_PHYSICAL_SOLVER_CLOCK_REQUIRED")
            require(pending_dt is not None, "CFD_ACTUAL_NATIVE_DELTA_T_REQUIRED")
            current = {"dt": pending_dt, "outer": [{}], "continuity": [], "coverage": []}
            rows[instant] = current
            pending_dt = None
        match = re.search(r"^(Interface )?Courant Number mean: (" + native.FLOAT + r") max: (" + native.FLOAT + r")$", line)
        if match:
            values = [protocol.number(float(match[i]), nonnegative=True) for i in (2, 3)]
            require(values[0] <= values[1], "CFD_ORDERED_NATIVE_COURANT_REQUIRED")
            (alpha_co if match[1] else co).append(values[1])
        if current is None:
            continue
        if re.fullmatch(r"PIMPLE: iteration [1-9]\d*", line):
            if current["outer"][-1]:
                current["outer"].append({})
        match = re.search(r"Solving for (p_rgh|Ux|Uy|Uz|k|omega), Initial residual = (" + native.FLOAT + r"), Final residual = (" + native.FLOAT + r"), No Iterations (\d+)", line)
        if match:
            initial, final = [protocol.number(float(match[i]), nonnegative=True) for i in (2, 3)]
            require(initial <= 1 and final <= 1, "CFD_FINITE_NORMALIZED_RESIDUAL_REQUIRED")
            current["outer"][-1].setdefault(match[1], initial)
        elif re.search(r"Solving for (p_rgh|Ux|Uy|Uz|k|omega),", line):
            raise protocol.ProtocolError("CFD_NATIVE_RESIDUAL_RECORD_REQUIRED")
        match = re.search(r"time step continuity errors : sum local = (" + native.FLOAT + r"), global = (" + native.FLOAT + r")(?:, cumulative = (" + native.FLOAT + r"))?", line)
        if match:
            values = [protocol.number(float(value)) for value in match.groups() if value is not None]
            require(values[0] >= 0, "CFD_NONNEGATIVE_NATIVE_LOCAL_CONTINUITY_REQUIRED")
            current["continuity"].append(values)
        current["coverage"].append(line)
    require(rows and co and alpha_co, "CFD_PHYSICAL_CLOCK_COURANT_HISTORY_REQUIRED")
    return rows, max(co), max(alpha_co)


def interval_samples(table, start, end):
    """Use only the supported actual history; interpolate bracketed endpoints."""
    times = list(table)
    require(len(times) >= 5 and times[0] <= start < end <= times[-1], "CFD_SUPPORTED_FINAL_PHYSICAL_WINDOW_REQUIRED")
    def value_at(instant):
        if instant in table:
            return list(table[instant])
        for before, after in zip(times, times[1:]):
            if before < instant < after:
                fraction = (instant - before) / (after - before)
                return [a + fraction * (b - a) for a, b in zip(table[before], table[after])]
        raise protocol.ProtocolError("CFD_BRACKETED_TEMPORAL_ENDPOINT_REQUIRED")
    values = {start: value_at(start)}
    values.update((instant, table[instant]) for instant in times if start < instant < end)
    values[end] = value_at(end)
    return values


def time_mean(table, start, end):
    points = list(interval_samples(table, start, end).items())
    return [math.fsum((b - a) * (va[column] + vb[column]) / 2 for (a, va), (b, vb) in zip(points, points[1:])) / (end - start)
            for column in range(len(points[0][1]))]


def reverse_fraction(signed, magnitude, inward):
    require(magnitude > 0 and magnitude >= abs(signed) - 1e-9 and (signed < 0 if inward else signed > 0), "CFD_SIGNED_NATIVE_MASS_FLUX_REQUIRED")
    return max(0, (magnitude + signed if inward else magnitude - signed) / (2 * magnitude))


def verify_execution(root, manifest, manifest_row, study):
    """Bind immutable native facts and recheck all retained logs/fields/tables."""
    import run_frozen_rotor_phase as runner  # Native functions below are reads only.
    execution, execution_row = native.record(root, "phase-change-execution.json")
    study_raw, study_row = native.read(root, "phase-classroom-study.json", native.MAX_RECORD)
    require(study_raw == encoded(study), "CFD_CURRENT_PHASE_STUDY_REQUIRED")
    require(execution.get("schemaVersion") == runner.SCHEMA and execution.get("status") == "PHASE_CHANGE_EXECUTED_ACCEPTANCE_REQUIRED"
            and all(execution.get(key) is True for key in ("actualCfdRunVerified", "simulationExecuted", "nativeCheckMeshVerified",
                "nativeSolverCompleted", "cavitationModelExecuted", "finiteNativeEndFieldsVerified", "inputArtifactsImmutable", "sourceBaselineImmutable"))
            and execution.get("mainApplicationWrites") is False and execution.get("mode") == manifest["mode"]
            and execution.get("preparationReportSha256") == manifest["preparationReportSha256"]
            and execution.get("phaseInputManifestSha256") == manifest_row["sha256"]
            and execution.get("phaseStudySha256") == study_row["sha256"]
            and execution.get("phaseRuntimeIdentitySha256") == manifest["runtimeIdentitySha256"]
            and execution.get("sourceBaselineEvidenceSha256") == manifest["sourceBaselineEvidenceSha256"]
            and execution.get("authoredCavitationModelSha256") == manifest["controls"]["cavitationModelSha256"]
            and execution.get("temporalPlan") == manifest["temporalPlan"]
            and execution.get("inletAbsoluteTotalPressurePa") == manifest["pressureLevelAbsolutePa"], "CFD_ACTUAL_BOUND_PHASE_EXECUTION_REQUIRED")
    stages = execution.get("stages", [])
    require(len(stages) == 2 and [row.get("program") for row in stages] == ["checkMesh", "foamRun"], "CFD_COMPLETE_PHASE_EXECUTION_STAGES_REQUIRED")
    records, logs = [manifest_row, study_row, execution_row], {}
    for index, item in enumerate(stages):
        require(item.get("exitCode") == 0 and item.get("path") == f"logs/{index:02d}-{item['program']}.log", "CFD_EXACT_PHASE_LOG_ROLE_REQUIRED")
        raw, row = native.read(root, item["path"], MAX_LOG)
        require(row["bytes"] == item.get("bytes") and row["sha256"] == item.get("sha256"), "CFD_NATIVE_LOG_BINDING_MISMATCH")
        require(re.search(r"(?m)^End\s*$", raw.decode("utf-8")), "CFD_COMPLETED_NATIVE_LOG_REQUIRED")
        logs[item["program"]] = raw.decode("utf-8"); records.append(row)
    mesh = native.parse_mesh_quality(logs["checkMesh"])
    require(mesh == execution.get("nativeMeshQuality"), "CFD_CURRENT_PHASE_NATIVE_MESH_QUALITY_REQUIRED")
    final_rows = execution.get("finalNativeFieldRecords", [])
    require(len(final_rows) == len(runner.END_FIELDS) and {row.get("path", "").split("/")[-1] for row in final_rows} == set(runner.END_FIELDS), "CFD_COMPLETE_PHASE_NATIVE_END_FIELDS_REQUIRED")
    endpoint = protocol.number(execution.get("nativeEndTimeS"), positive=True)
    require(math.isclose(endpoint, manifest["temporalPlan"]["endTimeS"], rel_tol=5e-11, abs_tol=1e-12), "CFD_PHASE_NATIVE_END_TIME_REQUIRED")
    for item in final_rows:
        parts = item["path"].split("/")
        require(len(parts) == 3 and parts[0] == "case" and re.fullmatch(TIME_NAME, parts[1])
                and float(parts[1]) == endpoint, "CFD_EXACT_CURRENT_PHASE_FIELD_TIME_REQUIRED")
        raw, row = native.read(root, item["path"], native.MAX_FIELD)
        require(row["bytes"] == item.get("bytes") and row["sha256"] == item.get("sha256")
                and runner.finite_field(raw, parts[2], mesh["meshCellCount"]) == item.get("fieldCheck"), "CFD_CURRENT_PHASE_FIELD_BINDING_MISMATCH")
        records.append(row)
    sampling = execution.get("nativeSamplingRecords", [])
    require(isinstance(sampling, list) and len(sampling) <= 64 and sampling, "CFD_PHASE_NATIVE_SAMPLING_INVENTORY_REQUIRED")
    actual_sampling = runner.sampling_records(root)
    require(sampling == actual_sampling, "CFD_CURRENT_PHASE_SAMPLING_INVENTORY_REQUIRED")
    records.extend(actual_sampling)
    if manifest["mode"] == "transient-sliding":
        require(execution.get("physicalTransientExecuted") is True and execution.get("stationaryMrfRelaxationExecuted") is False,
                "CFD_ACTUAL_SLIDING_EXECUTION_REQUIRED")
        moved = execution.get("rotorMotionEvidence", {}).get("nativeMovedPointRecords", [])
        require(moved and re.search(r"Selecting solidBodyMotionFunction rotatingMotion", logs["foamRun"]), "CFD_ACTUAL_MOVED_ROTOR_POINTS_REQUIRED")
        time_paths = []
        for item in moved:
            require(re.fullmatch(r"case/" + TIME_NAME + r"/polyMesh/points", item.get("path", "")), "CFD_EXACT_MOVED_POINTS_PATH_REQUIRED")
            raw, row = native.read(root, item["path"], native.MAX_FIELD)
            require(row["bytes"] == item.get("bytes") and row["sha256"] == item.get("sha256"), "CFD_CURRENT_MOVED_POINTS_BINDING_REQUIRED")
            time_paths.append((float(item["path"].split("/")[1]), root / "case" / item["path"].split("/")[1])); records.append(row)
        require(runner.rotor_motion(root, time_paths, logs["foamRun"]) == execution.get("rotorMotionEvidence"), "CFD_ACTUAL_ROTOR_MOTION_PROOF_REQUIRED")
    else:
        require(execution.get("stationaryMrfRelaxationExecuted") is True and execution.get("physicalTransientExecuted") is False,
                "CFD_STATIONARY_MRF_RELAXATION_REQUIRED")
    require(len({row["path"] for row in records}) == len(records), "CFD_UNIQUE_PHASE_EVIDENCE_RECORDS_REQUIRED")
    return execution, records, logs, mesh


def reduce_native_phase(execution_directory):
    """Project actual samples; unavailable dependent branches remain unavailable."""
    root = Path(execution_directory)
    output = {"schemaVersion": REDUCTION_SCHEMA, "status": "NativePhaseEvidenceRequired", "actualCfdRunVerified": False,
              "phaseObservationQualified": False, "phaseFamilyReferenceEligible": False, "phaseFamilyReferenceQualified": False,
              "qualifiedBaseline": False, "npsh3Verified": False, "cavitationSweepExecuted": False,
              "meshConvergenceVerified": False, "timeStepConvergenceVerified": False, "restartVerified": False,
              "wallResolutionVerified": False, "physicalValidationVerified": False, "mainApplicationWrites": False,
              "stages": {}, "diagnostics": [], "qualificationScope": "One preliminary numerical active-phase observation on the actual chosen mesh under explicit classroom assumptions.",
              "claimBoundary": "A single-phase source head is never H0 for this phase family. An explicitly selected high-pressure active-phase reference and matched actual pressure bracket are required before NPSH3; mesh/time/restart and physical validation remain separate."}
    try:
        manifest, manifest_row = native.record(root, "phase-input-manifest.json")
        current = build_manifest(root)
        require(manifest == current["manifest"] and manifest.get("schemaVersion") == SCHEMA, "CFD_CURRENT_PHASE_INPUT_MANIFEST_REQUIRED")
        study = current["study"]
        execution, records, logs, mesh = verify_execution(root, manifest, manifest_row, study)
        output.update(actualCfdRunVerified=True, study=study, manifest=manifest, inputManifest=manifest,
                      physicalTransientExecuted=execution["physicalTransientExecuted"],
                      stationaryMrfRelaxationExecuted=execution["stationaryMrfRelaxationExecuted"], nativeEvidenceRecords=records)
        evidence_hash = sha256(encoded(sorted(records, key=lambda row: row["path"]))).hexdigest()
        run = {"studyInputs": study["input"], "controls": manifest["controls"], "samplingDefinition": study["samplingDefinition"],
               "physicalBasis": study["physicalBasis"], "physicalModel": manifest["physicalModel"], "runRevision": "Current",
               "pressureLevelAbsolutePa": manifest["pressureLevelAbsolutePa"],
               "nativeEvidence": {"kind": "native-solver-sampling", "upstreamVerified": True, "sha256": evidence_hash,
                                  "runId": execution.get("githubRunId") or sha256(encoded(execution)).hexdigest()},
               "controlledVariationProof": {"upstreamVerified": True, "nativeInputManifestSha256": manifest_row["sha256"],
                    "samplingEvidenceSha256": evidence_hash, "structuralControls": manifest["controls"], "signatureScope": study["signatureScope"],
                    "physicalModel": manifest["physicalModel"], "pressureBoundaryDefinition": study["controlledPressureDefinition"],
                    "pressureLevelAbsolutePa": manifest["pressureLevelAbsolutePa"],
                    "physicalTimeStepS": manifest["temporalPlan"]["maximumTimeStepS"] if manifest["mode"] == "transient-sliding" else None},
               "pressureMapping": {"nativeField": "p_rgh", "nativeUnit": "Pa", "absolutePressureReferencePa": 0,
                    "absoluteReconstructionEquation": "p_static_absolute_Pa=p_rgh+rho*gh; native g=0, hence p_absolute=p_rgh; no atmospheric addition.",
                    "hydrostaticReconstructionApplied": True}, "measurements": {}, "quality": {"nativeMeshQualityChecked": True, **mesh}}
        tables, table_errors = {}, []
        sampler_roles = {}
        for suffix, patch in (("Inlet", "inlet"), ("Outlet", "mass-flow-inlet-11"),
                              ("RotorInterface", native.COUPLED_PATCHES[0]), ("StatorInterface", native.COUPLED_PATCHES[1])):
            sampler_roles["massFlow" + suffix] = (("sum(rhoPhi)",), patch)
            sampler_roles["massFlowMagnitude" + suffix] = (("sumMag(rhoPhi)",), patch)
            if suffix in {"Inlet", "Outlet"}:
                sampler_roles["weighted" + suffix] = (("average(p)", "average(totalPressurePa)", "average(U2)", "average(alpha.water)"), patch)
                sampler_roles["waterMinimum" + suffix] = (("min(alpha.water)",), patch)
        for label, columns in (("globalPressureMinimum", ("min(p)",)), ("globalWaterMinimum", ("min(alpha.water)",)), ("globalWaterMaximum", ("max(alpha.water)",))):
            sampler_roles[label] = (columns, None)
        for name, (columns, patch) in sampler_roles.items():
            try:
                tables[name], _ = sampler(root, name, columns, patch)
            except (protocol.ProtocolError, OSError, UnicodeError, ValueError) as error:
                table_errors.append({"branch": name, "reasonCode": str(error)[:500]})
        output["diagnostics"].extend(table_errors)
        end = manifest["temporalPlan"]["endTimeS"]
        if "weightedInlet" in tables:
            actual_end = next(reversed(tables["weightedInlet"]))
            require(math.isclose(actual_end,end,rel_tol=5e-11,abs_tol=1e-12), "CFD_CURRENT_PHASE_CUT_END_TIME_REQUIRED")
            end = actual_end
        period = manifest["temporalPlan"]["rotationPeriodS"]
        requested_start = max(0, end - manifest["classroomAcceptance"]["samplingPeriods"] * period - 1e-12)
        rho, g = study["input"]["densityKgM3"], study["input"]["gravityMS2"]
        zi, zo = manifest["inletElevationM"], manifest["outletElevationM"]
        cut_windows = {}
        for suffix in ("Inlet", "Outlet"):
            try:
                table = tables["weighted" + suffix]
                require(math.isclose(next(reversed(table)), end, rel_tol=5e-11, abs_tol=1e-12), "CFD_CURRENT_PHASE_CUT_END_TIME_REQUIRED")
                start = max(requested_start, next(iter(table)))
                cut = interval_samples(table, start, end)
                minimum = interval_samples(tables["waterMinimum" + suffix], start, end)
                require(set(cut) == set(minimum), "CFD_MATCHED_LIQUID_SECTION_TIMES_REQUIRED")
                raw_minimum = min(value[0] for value in minimum.values())
                require(-1e-9 <= raw_minimum <= 1+1e-9, "CFD_BOUNDED_LIQUID_SECTION_FRACTION_REQUIRED")
                maximum_vapor = min(1., max(0., 1 - raw_minimum))
                for instant, (ps, pt, u2, liquid) in cut.items():
                    require(u2 >= 0 and -1e-9 <= liquid <= 1+1e-9 and minimum[instant][0] <= liquid+1e-9, "CFD_VALID_PHASE_SECTION_MOMENTS_REQUIRED")
                    if maximum_vapor <= study["acceptance"]["liquidSectionVaporFractionLimit"]:
                        require(math.isclose(pt, ps+rho*u2/2, rel_tol=2e-5, abs_tol=2), "CFD_LIQUID_TOTAL_PRESSURE_DEFINITION_MISMATCH")
                ps, pt, u2, _ = time_mean(table, start, end)
                cut_windows[suffix] = (start, end)
                prefix = suffix.lower()
                run["measurements"].update({prefix+"TotalPressureAbsolutePa": pt, prefix+"ElevationM": zi if suffix == "Inlet" else zo,
                                           prefix+"MaximumVaporFraction": maximum_vapor})
                if suffix == "Inlet":
                    if maximum_vapor <= study["acceptance"]["liquidSectionVaporFractionLimit"]:
                        require(math.isclose(pt,manifest["pressureLevelAbsolutePa"],rel_tol=2e-5,abs_tol=2), "CFD_INLET_ABSOLUTE_TOTAL_PRESSURE_BC_MISMATCH")
                    run["measurements"].update(inletStaticPressureAbsolutePa=ps, inletMeanSquaredAbsoluteSpeedM2S2=u2)
            except (protocol.ProtocolError, KeyError, OSError, ValueError) as error:
                output["diagnostics"].append({"branch": suffix.lower()+"-section", "reasonCode": str(error)[:500]})
        # These formula branches survive an unavailable outlet/global sampler or
        # a failed convergence gate; they are never promoted to a qualified run.
        try:
            require(not table_errors and set(cut_windows) == {"Inlet", "Outlet"}, "CFD_COMPLETE_PHASE_ACCEPTANCE_SAMPLERS_REQUIRED")
            clock, max_co, max_alpha_co = parse_solver_log(logs["foamRun"])
            require(math.isclose(max(clock), end, rel_tol=5e-11, abs_tol=1e-12), "CFD_CURRENT_PHYSICAL_SOLVER_END_TIME_REQUIRED")
            all_times = list(tables["weightedInlet"])
            require(all(list(table) == all_times for table in tables.values()), "CFD_MATCHED_ALL_PHASE_SAMPLING_TIMES_REQUIRED")
            positive_times = [instant for instant in all_times if instant > 0]
            require(len(positive_times) == len(clock) and all(math.isclose(instant,native_time,rel_tol=5e-10,abs_tol=1e-12)
                    for instant,native_time in zip(positive_times,clock)),
                    "CFD_NATIVE_SOLVER_SAMPLING_CLOCK_BINDING_REQUIRED")
            previous = 0.
            for instant,value in clock.items():
                require(math.isclose(instant-previous,value["dt"],rel_tol=5e-8,abs_tol=1e-12), "CFD_NATIVE_PHYSICAL_CLOCK_DELTA_T_MISMATCH")
                previous = instant
            start = max(requested_start, all_times[0])
            output["statisticalWindowS"] = [start, end]
            output["temporalReduction"] = {"method": "Piecewise-linear interpolation between actual bracketing samples; trapezoidal time integration. Seven equal-time head bins are reported for the protocol mean; actual sample extrema also remain gated.",
                                            "requestedWindowS": [requested_start, end], "supportedWindowS": [start, end],
                                            "startInterpolated": start not in tables["weightedInlet"], "noTemporalExtrapolation": True}
            window = {name: interval_samples(table, start, end) for name, table in tables.items()}
            indexes = list(window["weightedInlet"])
            head_table = {instant: [(window["weightedOutlet"][instant][1] - window["weightedInlet"][instant][1])/(rho*g)+zo-zi] for instant in indexes}
            heads = [value[0] for value in head_table.values()]
            bin_edges = [start+(end-start)*i/7 for i in range(8)]
            head_bins = [time_mean(head_table, a, b)[0] for a, b in zip(bin_edges, bin_edges[1:])]
            flows_in, flows_out, reverse, mismatch = [], [], [], []
            for instant in indexes:
                fi, fo = window["massFlowInlet"][instant][0], window["massFlowOutlet"][instant][0]
                mi, mo = window["massFlowMagnitudeInlet"][instant][0], window["massFlowMagnitudeOutlet"][instant][0]
                reverse.extend((reverse_fraction(fi, mi, True), reverse_fraction(fo, mo, False)))
                flows_in.append(fi); flows_out.append(fo)
                fr, fs = window["massFlowRotorInterface"][instant][0], window["massFlowStatorInterface"][instant][0]
                mr, ms = window["massFlowMagnitudeRotorInterface"][instant][0], window["massFlowMagnitudeStatorInterface"][instant][0]
                reverse.extend((reverse_fraction(fr, mr, False), reverse_fraction(fs, ms, True)))
                mismatch.append(abs(fr+fs)/max(abs(fr), abs(fs)))
            residuals = {name: [] for name in ("p", "U", "k", "omega")}
            continuity, actual_dt, selected_clock = [], [], []
            for instant, value in clock.items():
                if not start <= instant <= end+1e-12:
                    continue
                selected_clock.append(value)
                actual_dt.append(value["dt"])
                require(value["continuity"], "CFD_PHASE_LOCAL_CONTINUITY_HISTORY_REQUIRED")
                continuity.extend(value["continuity"])
                require(len(value["outer"]) == 3 and {"k","omega"} <= set(value["outer"][-1]), "CFD_AUTHORED_PIMPLE_TRANSPORT_HISTORY_REQUIRED")
                for outer in value["outer"]:
                    require({"p_rgh", "Ux", "Uy", "Uz"} <= set(outer), "CFD_COMPLETE_PIMPLE_OUTER_RESIDUALS_REQUIRED")
                    residuals["p"].append(outer["p_rgh"])
                    residuals["U"].append(max(outer[axis] for axis in ("Ux","Uy","Uz")))
                    # Foundation12 transportCorrectionFinal defaults true;
                    # turbulence need not be solved on early PIMPLE outers.
                    for name in ("k","omega"):
                        if name in outer:
                            residuals[name].append(outer[name])
            require(len(selected_clock) >= 5 and max(row[0] for row in continuity) <= manifest["classroomAcceptance"]["continuityLocalLimit"], "CFD_PHASE_LOCAL_CONTINUITY_CONVERGENCE_REQUIRED")
            raw_min = min(value[0] for value in window["globalWaterMinimum"].values())
            raw_max = max(value[0] for value in window["globalWaterMaximum"].values())
            require(-1e-9 <= raw_min <= raw_max <= 1+1e-9, "CFD_GLOBAL_PHASE_FRACTION_BOUNDEDNESS_REQUIRED")
            for instant in indexes:
                require(window["globalWaterMinimum"][instant][0] <= window["globalWaterMaximum"][instant][0], "CFD_ORDERED_GLOBAL_PHASE_EXTREMA_REQUIRED")
            q = run["quality"]
            mean_in = time_mean(tables["massFlowInlet"], start, end)[0]
            mean_out = time_mean(tables["massFlowOutlet"], start, end)[0]
            q.update(outwardInletMassFlowKgS=mean_in, outwardOutletMassFlowKgS=mean_out, reverseFlowFraction=max(reverse), finalResidualHistory=residuals,
                     residualDefinition="initial-solver-residual-per-outer-iteration-final-window", samplingWindowDefinition="final-converged-window",
                     finalFlowRelativeSpan=max((max(values)-min(values))/abs(mean) for values, mean in ((flows_in,mean_in),(flows_out,mean_out))),
                     headHistoryM=head_bins, minimumVaporFraction=max(0.,1-raw_max), maximumVaporFraction=min(1.,1-raw_min),
                     observedMaximumTimeStepS=max(actual_dt), observedMaxCo=max_co, observedMaxAlphaCo=max_alpha_co)
            output.update(rawGlobalWaterMinimum=raw_min, rawGlobalWaterMaximum=raw_max, alphaRoundoffTolerance=1e-9,
                          nativeHeadSampleExtremaM=[min(heads),max(heads)], continuityHistory=continuity,
                          residualSamplingDefinition="First initial p_rgh/U residual of each actual PIMPLE outer; k/omega initial residual only on actual transport-correction outers, with required final-outer correction.")
            minimum_pressure = min(value[0] for value in window["globalPressureMinimum"].values())
            run["measurements"]["minimumStaticPressureAbsolutePa"] = minimum_pressure
            output["minimumStaticPressureAbsolutePa"] = minimum_pressure
            require(minimum_pressure > 0, "CFD_POSITIVE_ABSOLUTE_NATIVE_PRESSURE_REQUIRED")
            require(max_co <= manifest["classroomAcceptance"]["nativeCoLimit"] and max_alpha_co <= manifest["classroomAcceptance"]["nativeAlphaCoLimit"]
                    and max(actual_dt) <= manifest["temporalPlan"]["maximumTimeStepS"]*(1+1e-9), "CFD_AUTHORED_PHASE_COURANT_DT_LIMIT_REQUIRED")
            require(max(mismatch) <= manifest["classroomAcceptance"]["interfaceFluxRelativeLimit"], "CFD_ACTUAL_PHASE_INTERFACE_FLUX_CONSERVATION_REQUIRED")
            coverage = native.parse_coverage(logs["checkMesh"]+"\n"+logs["foamRun"])
            limits = manifest["classroomAcceptance"]
            require(all(values[0] >= limits["interfaceMinimumCoverage"] and values[1] >= limits["interfaceAverageCoverage"]
                        and values[2] <= limits["interfaceMaximumCoverage"] for pair in coverage for values in pair.values()), "CFD_PHASE_NCC_COVERAGE_NOT_SATISFIED")
            if manifest["mode"] == "transient-sliding":
                for value in selected_clock:
                    native.parse_coverage("\n".join(value["coverage"]))
                q.update(physicalTimeStepS=manifest["temporalPlan"]["maximumTimeStepS"], statisticalWindowS=[start,end])
                require(end-start >= 3*period-1e-12, "CFD_THREE_SUPPORTED_ROTOR_PERIODS_REQUIRED")
                edges = [end-period*i for i in (3,2,1,0)]
                period_heads = [time_mean(head_table,a,b)[0] for a,b in zip(edges,edges[1:])]
                q["periodicHeadDifferencesM"] = [b-a for a,b in zip(period_heads,period_heads[1:])]
                output["physicalRotorAngleStage"] = protocol.time_step_stages(study,end,manifest["temporalPlan"]["maximumTimeStepS"])
                output["nativeCoverageAtSampledRotorStepsVerified"] = True
                output["periodMeanHeadM"] = period_heads
            else:
                require((max(heads)-min(heads))/abs(math.fsum(head_bins)/len(head_bins)) <= study["acceptance"]["headWindowRelativeLimit"], "CFD_ACTUAL_STATIONARY_HEAD_SAMPLE_SPAN_REQUIRED")
            output.update(interfaceCoverageVerified=True, interfaceFluxRelativeMismatch=max(mismatch))
        except (protocol.ProtocolError, KeyError, OSError, ValueError, ZeroDivisionError) as error:
            output["diagnostics"].append({"branch": "phase-native-quality", "reasonCode": str(error)[:500]})
        # Bind declared dt even when a later dependent quality metric is absent.
        if manifest["mode"] == "transient-sliding":
            run["quality"]["physicalTimeStepS"] = manifest["temporalPlan"]["maximumTimeStepS"]
        projected = protocol.reduce_run(study, run)
        output.update(nativeObservation=run, stages=projected["stages"], protocolReduction=projected, npshaDomainStatus=projected["npshaDomainStatus"])
        output["diagnostics"].extend(projected["diagnostics"])
        output["phaseObservationQualified"] = not output["diagnostics"] and projected["status"] == "QualifiedSuppliedObservation"
        output["phaseFamilyReferenceEligible"] = output["phaseObservationQualified"] and run["quality"].get("maximumVaporFraction",1) <= study["acceptance"]["liquidSectionVaporFractionLimit"] and run["measurements"].get("minimumStaticPressureAbsolutePa",0) > study["input"]["vaporPressurePa"] and output["stages"].get("head",{}).get("result",{}).get("value",0) > 0
        output["status"] = "PreliminaryPhaseObservationQualified" if output["phaseObservationQualified"] else "PhaseNumericalAcceptanceRequired"
    except (protocol.ProtocolError, campaign.CampaignIssue, campaign.base.CaseIssue, OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
        output["diagnostics"].append({"branch": "native-phase-evidence", "reasonCode": str(error)[:500]})
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        root = campaign.inside_runner(args.execution_directory)
        destination = campaign.base.inventory.output_path(root / "phase-native-reduction.json")
        result = reduce_native_phase(root)
        raw = encoded(result)
        require(len(raw) <= MAX_REDUCTION_BYTES, "CFD_BOUNDED_PHASE_REDUCTION_REQUIRED")
        # The pre-execution manifest is verified above and never replaced here.
        campaign.base.write_exclusive(destination, raw)
        print(json.dumps({"status": result["status"], "actualCfdRunVerified": result["actualCfdRunVerified"],
                          "phaseObservationQualified": result["phaseObservationQualified"], "npsh3Verified": False,
                          "report": "phase-native-reduction.json", "mainApplicationWrites": False}, allow_nan=False))
        return 0 if result["phaseObservationQualified"] else 2
    except (protocol.ProtocolError,campaign.CampaignIssue,campaign.base.CaseIssue,campaign.base.inventory.InspectionIssue,OSError,ValueError) as error:
        print(json.dumps({"status":"NativePhaseEvidenceRequired","reasonCode":"CFD_PHASE_REDUCTION_OUTPUT_REQUIRED",
                          "action":str(error)[:500],"actualCfdRunVerified":False,"mainApplicationWrites":False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

