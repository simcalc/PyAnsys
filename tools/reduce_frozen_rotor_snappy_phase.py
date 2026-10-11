"""Read actual fixed-MRF Schnerr-Sauer evidence on a checked approximation.

The copied single-phase result is a preparation prerequisite, never active H0.
This reader launches nothing and keeps native completion, numerical reference
eligibility, geometry accuracy and unperformed campaign stages separate.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
from itertools import repeat
import math
from pathlib import Path, PurePosixPath
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_native_reduction as native
import frozen_rotor_research_protocol as protocol
import frozen_rotor_phase_reduction as phase
import frozen_rotor_snappy_phase_case as preparation
import run_frozen_rotor_phase as fields
import run_frozen_rotor_snappy as geometry

SCHEMA = "untirta.frozen-rotor-snappy-phase-reference-reduction.v1"
MANIFEST_SCHEMA = "untirta.frozen-rotor-snappy-phase-input-manifest.v1"
EXECUTION_SCHEMA = "untirta.frozen-rotor-snappy-phase-reference-execution.v1"
RUNTIME_SCHEMA = "untirta.frozen-rotor-snappy-phase-runtime-identity.v1"
MAX_LOG, MAX_REDUCTION_BYTES = 8*1024**2, 16*1024**2
PROGRAMS = ("checkMesh", "foamRun")
LIMITS = {"samplingPeriods": 3, "continuityLocalLimit": 1e-4,
          "interfaceMinimumCoverage": .99, "interfaceAverageCoverage": .999,
          "interfaceMaximumCoverage": 1.01, "interfaceFluxRelativeLimit": .005,
          "nativeCoLimit": .5, "nativeAlphaCoLimit": .25}
FALSE_CLAIMS = ("qualifiedBaseline", "fullGeometryQualification", "geometricApproximationQualified",
                "nativeCoordinateUnitsVerified", "nativeNodeIdentityEquivalent", "wallResolutionVerified",
                "meshConvergenceVerified", "timeStepConvergenceVerified", "physicalValidationVerified",
                "cavitationSweepExecuted", "pressureSweepEnabled", "npshrCalculated", "npsh3Qualified",
                "physicalTransientExecuted", "slidingEnabled", "restartVerified", "phaseFamilyReferenceQualified",
                "mainApplicationWrites")
require, encoded, read, record = native.require, native.encoded, native.read, native.record


def binary_identity(identity):
    keys = {"schemaVersion", "openFoamFoundationVersion", "packageVersion", "officialDebSha256",
            "expectedSourceCommit", "binaries", "mainApplicationWrites"}
    require(isinstance(identity, dict) and set(identity) == keys and identity.get("schemaVersion") == RUNTIME_SCHEMA
            and identity.get("openFoamFoundationVersion") == "12" and identity.get("packageVersion") == "20250206"
            and identity.get("officialDebSha256") == geometry.surface.PACKAGE_SHA
            and identity.get("expectedSourceCommit") == geometry.surface.SOURCE_COMMIT
            and identity.get("mainApplicationWrites") is False, "CFD_SNAPPY_PHASE_EXACT_RUNTIME_REQUIRED")
    rows = identity.get("binaries")
    require(isinstance(rows, list) and len(rows) == 2 and {r.get("program") for r in rows} == set(PROGRAMS),
            "CFD_SNAPPY_PHASE_TWO_CURRENT_BINARIES_REQUIRED")
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"program", "path", "bytes", "sha256", "packageOwnership"}
                and type(row.get("bytes")) is int and 0 < row["bytes"] <= native.MAX_FIELD
                and protocol.digest(row.get("sha256")), "CFD_SNAPPY_PHASE_BINARY_DESCRIPTOR_REQUIRED")
        pure = PurePosixPath(row.get("path", "")); path = Path(row.get("path", ""))
        require(pure.is_absolute() and pure.is_relative_to(PurePosixPath("/opt/openfoam12"))
                and ".." not in pure.parts and "\\" not in row["path"] and pure.name == row["program"]
                and not path.is_symlink() and row.get("packageOwnership") == "openfoam12: " + row["path"],
                "CFD_SNAPPY_PHASE_OFFICIAL_CURRENT_BINARY_REQUIRED")
        raw = native.case_tool.read_regular(path, native.MAX_FIELD)
        require(len(raw) == row["bytes"] and sha256(raw).hexdigest() == row["sha256"],
                "CFD_SNAPPY_PHASE_BINARY_IDENTITY_MISMATCH")


def build_manifest(execution_directory):
    """Return {manifest, study}; persist exact bytes before either native command."""
    root = native.case_tool.regular_directory(execution_directory)
    verified = preparation.verify_output(root)
    prep, prep_row = record(root, preparation.REPORT)
    require(verified["report"] == prep and prep.get("schemaVersion") == preparation.SCHEMA
            and prep.get("mode") == preparation.MODE and prep.get("inletAbsoluteTotalPressurePa") == 300000
            and prep.get("sourceNumericalBaselineQualified") is True
            and prep.get("sourceActualCfdRunVerified") is True
            and all(prep.get(k) is False for k in preparation.FALSE_FLAGS), "CFD_SNAPPY_PHASE_EXACT_PREPARATION_REQUIRED")
    runtime, runtime_row = record(root, "phase-runtime-identity.json"); binary_identity(runtime)
    identity, identity_row = record(root, "geometry-identity.json")
    require(identity_row == prep["sourceGeometryIdentityFile"]
            and sha256(encoded([identity_row])).hexdigest() == prep["sourceGeometryControlSha256"],
            "CFD_SNAPPY_PHASE_COPIED_GEOMETRY_IDENTITY_REQUIRED")
    rows = [{**prep_row, "control": None}, {**identity_row, "control": "geometrySha256"},
            {**runtime_row, "control": "runtimeSha256"}]
    group = {key: [] for key in protocol.CONTROL_KEYS}
    group["geometrySha256"] = [identity_row]; group["runtimeSha256"] = [runtime_row]
    source = preparation.records(root, prep["sourceEvidenceFileRecords"])
    rows.extend({**row, "control": None} for row in source)
    for declared in prep["preservedNativeMeshFiles"]:
        _, actual = read(root, "case/" + declared["path"], native.MAX_FIELD)
        require(actual == {**declared, "path": "case/" + declared["path"]}, "CFD_SNAPPY_PHASE_EXACT_COPIED_MESH_REQUIRED")
        rows.append({**actual, "control": "meshSha256"}); group["meshSha256"].append(declared)
    for declared in prep["authoredInputFiles"]:
        _, actual = read(root, "case/" + declared["path"], native.MAX_FIELD)
        require(actual == {**declared, "path": "case/" + declared["path"]}, "CFD_SNAPPY_PHASE_CURRENT_AUTHORED_INPUT_REQUIRED")
        key = "cavitationModelSha256" if declared["path"] in preparation.MODEL_FILES else \
              "discretizationSha256" if declared["path"].startswith("system/") else "boundarySha256"
        rows.append({**actual, "control": key}); group[key].append(declared)
    require(len(rows) <= 384 and len({row["path"] for row in rows}) == len(rows), "CFD_SNAPPY_PHASE_UNIQUE_BOUNDED_MANIFEST_REQUIRED")
    controls = {key: sha256(encoded(sorted(values, key=lambda r: r["path"]))).hexdigest() for key, values in group.items()}
    require(all(group.values()) and controls["cavitationModelSha256"] == prep["authoredCavitationModelSha256"],
            "CFD_SNAPPY_PHASE_ACTIVE_MODEL_BINDING_REQUIRED")
    study = protocol.prepare_study(inputs=prep["declaredStudyInputs"], controls=controls, gravity_in_solver=False)
    require(study["physicalBasis"] == prep["physicalBasis"], "CFD_SNAPPY_PHASE_EXACT_PHYSICAL_BASIS_REQUIRED")
    study["signatureScope"].update({"boundarySha256": "Exact authored active phase BCs; no pressure variation normalization",
                                  "discretizationSha256": "Exact active MRF time/solver controls; no clock normalization",
                                  "geometrySha256": "Preserved source-bound approximation identity; no geometry accuracy claim"})
    manifest = {"schemaVersion": MANIFEST_SCHEMA, "sourceGeometryRoute": preparation.baseline.GEOMETRY_ROUTE,
                "files": sorted(rows, key=lambda r: r["path"]), "controls": controls,
                "structuralSignatureRows": {k: sorted(v, key=lambda r: r["path"]) for k, v in sorted(group.items())},
                "structuralSignatureMethod": "Exact actual bytes with native-relative case paths for control groups; no scalar variation or normalized execution.",
                "signatureScope": study["signatureScope"], "studyInputs": study["input"], "physicalBasis": study["physicalBasis"],
                "samplingDefinition": study["samplingDefinition"], "physicalModel": "steady-cavitation-MRF", "mode": preparation.MODE,
                "pressureLevelAbsolutePa": 300000., "temporalPlan": prep["temporalPlan"],
                "preparationReportSha256": prep_row["sha256"], "runtimeIdentitySha256": runtime_row["sha256"],
                "sourceBaselineEvidenceSha256": prep["sourceBaselineEvidenceSha256"],
                "sourceBaselineRole": "Numerical single-phase prerequisite only; its head is never active-phase H0.",
                "inletElevationM": 0., "outletElevationM": 0.,
                "elevationBasis": "Declared classroom common zero datum; measured native section centroids remain unverified.",
                "absolutePressureMapping": prep["absolutePressureMapping"], "classroomAcceptance": dict(LIMITS),
                "geometricApproximationQualified": False, "nativeCoordinateUnitsVerified": False,
                "simulationExecuted": False, "mainApplicationWrites": False}
    require(len(encoded(manifest)) <= native.MAX_RECORD, "CFD_SNAPPY_PHASE_BOUNDED_MANIFEST_REQUIRED")
    return {"manifest": manifest, "study": study}


def add_record(records, row):
    require(row["path"] not in records or records[row["path"]] == row, "CFD_SNAPPY_PHASE_EVIDENCE_ROLE_COLLISION")
    records[row["path"]] = row


def evidence_hash(records):
    return sha256(encoded(sorted(records.values(), key=lambda r: r["path"]))).hexdigest()


def absolute_pressure_reconstruction(p_raw, prgh_raw, cell_count):
    """Independently check actual internal p=p_rgh for this bound g=0 recipe."""
    def values(raw, name):
        fields.finite_field(raw, name, cell_count)
        text = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw.decode("ascii"), flags=re.S)
        match = re.search(r"\binternalField\s+(?:uniform\s+([^;]+)|nonuniform\s+List<scalar>\s+\d+\s*\((.*?)\))\s*;", text, re.S)
        require(match, "CFD_SNAPPY_PHASE_CURRENT_SCALAR_PRESSURE_REQUIRED")
        return repeat(float(match[1]), cell_count) if match[1] is not None else map(float, match[2].split())
    maximum = 0.; count = 0
    for p, prgh in zip(values(p_raw, "p"), values(prgh_raw, "p_rgh"), strict=True):
        maximum = max(maximum, abs(p-prgh)); count += 1
        require(math.isclose(p, prgh, rel_tol=1e-12, abs_tol=1e-8), "CFD_SNAPPY_PHASE_ABSOLUTE_P_PRGH_RECONSTRUCTION_REQUIRED")
    require(count == cell_count, "CFD_SNAPPY_PHASE_COMPLETE_PRESSURE_RECONSTRUCTION_REQUIRED")
    return {"equation": "p_absolute=p_rgh+rho*gh; authored g=0 and fixed absolute inlet pressure give p_absolute=p_rgh",
            "allInternalCellsChecked": count, "maximumAbsoluteDifferencePa": maximum,
            "relativeRoundoffTolerance": 1e-12, "absoluteRoundoffTolerancePa": 1e-8,
            "atmosphericPressureAddedPa": 0, "verified": True,
            "sourceUrl": "https://github.com/OpenFOAM/OpenFOAM-12/blob/" + geometry.surface.SOURCE_COMMIT +
                         "/applications/modules/twoPhaseSolver/pressureCorrector.C"}


def verify_execution(root, manifest, manifest_row, study, records):
    import run_frozen_rotor_snappy_phase as runner  # Only its constants are read.
    execution, execution_row = record(root, "phase-change-execution.json")
    study_raw, study_row = read(root, "phase-classroom-study.json", native.MAX_RECORD)
    require(study_raw == encoded(study), "CFD_SNAPPY_PHASE_CURRENT_STUDY_REQUIRED")
    require(execution.get("schemaVersion") == EXECUTION_SCHEMA
            and execution.get("status") == "SNAPPY_ACTIVE_PHASE_REFERENCE_EXECUTED_ACCEPTANCE_REQUIRED"
            and execution.get("mode") == preparation.MODE
            and execution.get("exampleDataCommit") == native.case_tool.inventory.PIN
            and execution.get("physicalModel") == "steady-cavitation-MRF"
            and execution.get("inletAbsoluteTotalPressurePa") == manifest["pressureLevelAbsolutePa"]
            and execution.get("absolutePressureMapping") == manifest["absolutePressureMapping"]
            and execution.get("classroomMetresPerSourceCoordinateUnit") == 1
            and all(execution.get(k) is True for k in ("actualCfdRunVerified", "simulationExecuted", "nativeCheckMeshVerified",
                "nativeGeometryQualityVerified", "physicalWallTypesVerified", "coupledInterfaceCoverageVerified", "sourceNumericalBaselineQualified",
                "stationaryMrfRelaxationExecuted", "nativeSolverCompleted", "cavitationModelExecuted", "finiteNativeEndFieldsVerified", "inputArtifactsImmutable", "sourceBaselineImmutable"))
            and all(execution.get(k) is False for k in runner.FALSE_FLAGS)
            and execution.get("preparationReportSha256") == manifest["preparationReportSha256"]
            and execution.get("phaseRuntimeIdentitySha256") == manifest["runtimeIdentitySha256"]
            and execution.get("phaseInputManifestSha256") == manifest_row["sha256"]
            and execution.get("phaseStudySha256") == study_row["sha256"]
            and execution.get("sourceBaselineEvidenceSha256") == manifest["sourceBaselineEvidenceSha256"]
            and execution.get("authoredCavitationModelSha256") == manifest["controls"]["cavitationModelSha256"]
            and execution.get("temporalPlan") == manifest["temporalPlan"], "CFD_SNAPPY_PHASE_ACTUAL_BOUND_NATIVE_EXECUTION_REQUIRED")
    require(execution.get("limits") == runner.LIMITS
            and 0 <= protocol.number(execution.get("elapsedSeconds"), nonnegative=True) < runner.MAX_SECONDS
            and type(execution.get("workspaceBytes")) is int and 0 < execution["workspaceBytes"] <= runner.MAX_WORKSPACE,
            "CFD_SNAPPY_PHASE_EXACT_BOUNDED_NATIVE_EXECUTION_REQUIRED")
    add_record(records, execution_row); add_record(records, study_row)
    stages = execution.get("stages")
    require(isinstance(stages, list) and len(stages) == 2 and [r.get("program") for r in stages] == list(PROGRAMS),
            "CFD_SNAPPY_PHASE_TWO_NATIVE_STAGES_REQUIRED")
    logs = {}
    for index, item in enumerate(stages):
        require(type(item.get("exitCode")) is int and item["exitCode"] == 0
                and item.get("log") == f"{index:02d}-{PROGRAMS[index]}.log", "CFD_SNAPPY_PHASE_EXACT_COMPLETED_LOG_ROLE_REQUIRED")
        require(protocol.number(item.get("elapsedSeconds"), nonnegative=True) < runner.MAX_SECONDS,
                "CFD_SNAPPY_PHASE_BOUNDED_NATIVE_STAGE_REQUIRED")
        raw, row = read(root, "logs/" + item["log"], MAX_LOG)
        require(row["bytes"] == item.get("logBytes") and row["sha256"] == item.get("logSha256"), "CFD_SNAPPY_PHASE_NATIVE_LOG_BINDING_REQUIRED")
        text = raw.decode("utf-8"); geometry.completed_log(text); logs[item["program"]] = text; add_record(records, row)
    require(re.search(r"(?m)^Selecting cavitation model SchnerrSauer\s*$", logs["foamRun"])
            and not re.search(r"Selecting solidBodyMotionFunction|Selecting motion solver", logs["foamRun"]),
            "CFD_SNAPPY_PHASE_ACTUAL_FIXED_MRF_SCHNERR_SAUER_REQUIRED")
    prep = record(root, preparation.REPORT)[0]
    measured = geometry.native_mesh(root / "case", prep["components"], logs["checkMesh"])
    require(measured == execution.get("mergedNativeMeshEvidence")
            and measured["cellCount"] == sum(c["cellCount"] for c in prep["components"])
            and measured["nodeCount"] == sum(c["nodeCount"] for c in prep["components"]), "CFD_SNAPPY_PHASE_CURRENT_SOURCE_ALIGNED_MESH_REQUIRED")
    mesh = native.parse_mesh_quality(logs["checkMesh"])
    require(mesh == execution.get("nativeMeshQuality") and mesh["meshCellCount"] == measured["cellCount"], "CFD_SNAPPY_PHASE_CURRENT_NATIVE_QUALITY_REQUIRED")
    final_rows, _ = fields.final_fields(root, manifest["temporalPlan"], mesh["meshCellCount"], logs["foamRun"])
    require(final_rows == execution.get("finalNativeFieldRecords"), "CFD_SNAPPY_PHASE_CURRENT_FINITE_DIMENSIONAL_ENDPOINT_REQUIRED")
    endpoint = protocol.number(execution.get("nativeEndTimeS"), positive=True)
    require(math.isclose(endpoint, manifest["temporalPlan"]["endTimeS"], rel_tol=5e-11, abs_tol=1e-12), "CFD_SNAPPY_PHASE_ACTUAL_END_TIME_REQUIRED")
    for row in final_rows:
        add_record(records, {k: row[k] for k in ("path", "bytes", "sha256")})
    pressure_fields = {Path(row["path"]).name: row for row in final_rows}
    reconstruction = absolute_pressure_reconstruction(
        read(root, pressure_fields["p"]["path"], native.MAX_FIELD)[0],
        read(root, pressure_fields["p_rgh"]["path"], native.MAX_FIELD)[0], mesh["meshCellCount"])
    sampling = fields.sampling_records(root)
    require(sampling and sampling == execution.get("nativeSamplingRecords"), "CFD_SNAPPY_PHASE_COMPLETE_CURRENT_SAMPLING_INVENTORY_REQUIRED")
    for row in sampling: add_record(records, row)
    return execution, logs, mesh, reconstruction


def sampler_roles():
    roles = {}
    for suffix, patch in (("Inlet", "inlet"), ("Outlet", "mass-flow-inlet-11"),
                          ("RotorInterface", native.COUPLED_PATCHES[0]), ("StatorInterface", native.COUPLED_PATCHES[1])):
        roles["massFlow" + suffix] = (("sum(rhoPhi)",), patch)
        roles["massFlowMagnitude" + suffix] = (("sumMag(rhoPhi)",), patch)
        if suffix in {"Inlet", "Outlet"}:
            roles["weighted" + suffix] = (("average(p)", "average(totalPressurePa)", "average(U2)", "average(alpha.water)"), patch)
            roles["waterMinimum" + suffix] = (("min(alpha.water)",), patch)
    for label, column in (("globalPressureMinimum", "min(p)"), ("globalWaterMinimum", "min(alpha.water)"), ("globalWaterMaximum", "max(alpha.water)")):
        roles[label] = ((column,), None)
    return roles


def observation(root, output, manifest, study, execution, logs, mesh, records):
    """Project valid formula branches first; missing quality cannot create H0."""
    digest = evidence_hash(records)
    run = {"studyInputs": study["input"], "controls": manifest["controls"], "samplingDefinition": study["samplingDefinition"],
           "physicalBasis": study["physicalBasis"], "physicalModel": "steady-cavitation-MRF", "runRevision": "Current",
           "pressureLevelAbsolutePa": 300000., "nativeEvidence": {"kind": "native-solver-sampling", "upstreamVerified": True,
           "sha256": digest, "runId": execution.get("githubRunId") or sha256(encoded(execution)).hexdigest()},
           "controlledVariationProof": {"upstreamVerified": True, "nativeInputManifestSha256": output["nativeInputManifestSha256"],
                "samplingEvidenceSha256": digest, "structuralControls": manifest["controls"], "signatureScope": study["signatureScope"],
                "physicalModel": "steady-cavitation-MRF", "pressureBoundaryDefinition": study["controlledPressureDefinition"],
                "pressureLevelAbsolutePa": 300000., "physicalTimeStepS": None},
           "pressureMapping": {"nativeField": "p_rgh", "nativeUnit": "Pa", "absolutePressureReferencePa": 0,
                "absoluteReconstructionEquation": "p_abs=p_rgh+rho*gh; authored g=0, hence p_abs=p_rgh; no atmospheric addition.",
                "hydrostaticReconstructionApplied": True}, "measurements": {}, "quality": {"nativeMeshQualityChecked": True, **mesh}}
    q = run["quality"]
    tables = {}
    for name, (columns, patch) in sampler_roles().items():
        try:
            tables[name], row = phase.sampler(root, name, columns, patch); add_record(records, row)
        except (protocol.ProtocolError, OSError, UnicodeError, ValueError) as error:
            output["diagnostics"].append({"branch": name, "reasonCode": str(error)[:500]})
    end = manifest["temporalPlan"]["endTimeS"]; period = manifest["temporalPlan"]["rotationPeriodS"]
    start = end - 3*period
    rho, g = study["input"]["densityKgM3"], study["input"]["gravityMS2"]
    for suffix in ("Inlet", "Outlet"):
        try:
            table = tables["weighted" + suffix]; minimum = tables["waterMinimum" + suffix]
            require(math.isclose(next(reversed(table)), end, rel_tol=5e-11, abs_tol=1e-12), "CFD_SNAPPY_PHASE_CURRENT_SECTION_END_REQUIRED")
            actual_end = next(reversed(table))
            cut, liquid_min = (phase.interval_samples(t, start, actual_end) for t in (table, minimum))
            require(set(cut) == set(liquid_min), "CFD_SNAPPY_PHASE_MATCHED_LIQUID_SECTION_TIMES_REQUIRED")
            lowest = min(v[0] for v in liquid_min.values())
            require(-1e-9 <= lowest <= 1+1e-9, "CFD_SNAPPY_PHASE_LIQUID_SECTION_BOUNDS_REQUIRED")
            vapor = min(1., max(0., 1-lowest))
            for instant, (ps, pt, u2, liquid) in cut.items():
                require(u2 >= 0 and -1e-9 <= liquid <= 1+1e-9 and liquid_min[instant][0] <= liquid+1e-9, "CFD_SNAPPY_PHASE_SECTION_MOMENTS_REQUIRED")
                if vapor <= study["acceptance"]["liquidSectionVaporFractionLimit"]:
                    require(math.isclose(pt, ps+rho*u2/2, rel_tol=2e-5, abs_tol=2), "CFD_SNAPPY_PHASE_LIQUID_TOTAL_PRESSURE_REQUIRED")
            ps, pt, u2, _ = phase.time_mean(table, start, actual_end)
            prefix = suffix.lower(); run["measurements"].update({prefix+"TotalPressureAbsolutePa": pt,
                prefix+"ElevationM": 0., prefix+"MaximumVaporFraction": vapor})
            if suffix == "Inlet":
                require(vapor > study["acceptance"]["liquidSectionVaporFractionLimit"] or math.isclose(pt, 300000., rel_tol=2e-5, abs_tol=2),
                        "CFD_SNAPPY_PHASE_INLET_TOTAL_PRESSURE_BC_REQUIRED")
                run["measurements"].update(inletStaticPressureAbsolutePa=ps, inletMeanSquaredAbsoluteSpeedM2S2=u2)
        except (protocol.ProtocolError, OSError, KeyError, ValueError) as error:
            output["diagnostics"].append({"branch": suffix.lower()+"-section", "reasonCode": str(error)[:500]})
    try:
        require(set(tables) == set(sampler_roles()), "CFD_SNAPPY_PHASE_ALL_ACCEPTANCE_SAMPLERS_REQUIRED")
        clock, co, alpha_co = phase.parse_solver_log(logs["foamRun"])
        require(math.isclose(max(clock), end, rel_tol=5e-11, abs_tol=1e-12), "CFD_SNAPPY_PHASE_NATIVE_CLOCK_END_REQUIRED")
        times = list(tables["weightedInlet"])
        require(all(list(table) == times for table in tables.values()), "CFD_SNAPPY_PHASE_ALL_SAMPLING_CLOCKS_MATCH_REQUIRED")
        positive = [t for t in times if t > 0]
        require(len(positive) == len(clock) and all(math.isclose(t, c, rel_tol=5e-10, abs_tol=1e-12) for t, c in zip(positive, clock)),
                "CFD_SNAPPY_PHASE_NATIVE_SAMPLING_CLOCK_BINDING_REQUIRED")
        previous = 0.
        for instant, row in clock.items():
            require(math.isclose(instant-previous, row["dt"], rel_tol=5e-8, abs_tol=1e-12), "CFD_SNAPPY_PHASE_ACTUAL_DT_CLOCK_REQUIRED")
            previous = instant
        actual_end = times[-1]; require(start >= times[0] and actual_end-start >= 3*period-1e-12, "CFD_SNAPPY_PHASE_THREE_SUPPORTED_PERIODS_REQUIRED")
        window = {name: phase.interval_samples(table, start, actual_end) for name, table in tables.items()}
        indexes = list(window["weightedInlet"])
        head_table = {t: [(window["weightedOutlet"][t][1]-window["weightedInlet"][t][1])/(rho*g)] for t in indexes}
        heads = [v[0] for v in head_table.values()]; bins = [start+(actual_end-start)*i/7 for i in range(8)]
        head_bins = [phase.time_mean(head_table, a, b)[0] for a, b in zip(bins, bins[1:])]
        inlet, outlet, reverse, mismatch = [], [], [], []
        for t in indexes:
            fi, fo = window["massFlowInlet"][t][0], window["massFlowOutlet"][t][0]
            mi, mo = window["massFlowMagnitudeInlet"][t][0], window["massFlowMagnitudeOutlet"][t][0]
            reverse.extend((phase.reverse_fraction(fi, mi, True), phase.reverse_fraction(fo, mo, False)))
            inlet.append(fi); outlet.append(fo)
            fr, fs = window["massFlowRotorInterface"][t][0], window["massFlowStatorInterface"][t][0]
            mr, ms = window["massFlowMagnitudeRotorInterface"][t][0], window["massFlowMagnitudeStatorInterface"][t][0]
            reverse.extend((phase.reverse_fraction(fr, mr, False), phase.reverse_fraction(fs, ms, True)))
            mismatch.append(abs(fr+fs)/max(abs(fr), abs(fs)))
        residuals = {name: [] for name in ("p", "U", "k", "omega")}; continuity, actual_dt = [], []
        for instant, row in clock.items():
            if not start <= instant <= actual_end+1e-12: continue
            actual_dt.append(row["dt"])
            require(row["continuity"] and len(row["outer"]) == 3 and {"k", "omega"} <= set(row["outer"][-1]), "CFD_SNAPPY_PHASE_FINAL_OUTER_TRANSPORT_REQUIRED")
            continuity.extend(row["continuity"])
            for outer in row["outer"]:
                require({"p_rgh", "Ux", "Uy", "Uz"} <= set(outer), "CFD_SNAPPY_PHASE_FIRST_OUTER_RESIDUALS_REQUIRED")
                residuals["p"].append(outer["p_rgh"]); residuals["U"].append(max(outer[n] for n in ("Ux", "Uy", "Uz")))
                for name in ("k", "omega"):
                    if name in outer: residuals[name].append(outer[name])
        require(len(actual_dt) >= 5 and max(v[0] for v in continuity) <= LIMITS["continuityLocalLimit"], "CFD_SNAPPY_PHASE_LOCAL_CONTINUITY_REQUIRED")
        minimum = min(v[0] for v in window["globalWaterMinimum"].values()); maximum = max(v[0] for v in window["globalWaterMaximum"].values())
        require(-1e-9 <= minimum <= maximum <= 1+1e-9 and all(window["globalWaterMinimum"][t][0] <= window["globalWaterMaximum"][t][0] for t in indexes),
                "CFD_SNAPPY_PHASE_GLOBAL_ALPHA_BOUNDS_REQUIRED")
        q = run["quality"]; mean_in = phase.time_mean(tables["massFlowInlet"], start, actual_end)[0]; mean_out = phase.time_mean(tables["massFlowOutlet"], start, actual_end)[0]
        q.update(outwardInletMassFlowKgS=mean_in, outwardOutletMassFlowKgS=mean_out, reverseFlowFraction=max(reverse),
                 finalResidualHistory=residuals, residualDefinition="initial-solver-residual-per-outer-iteration-final-window",
                 samplingWindowDefinition="final-converged-window", finalFlowRelativeSpan=max((max(v)-min(v))/abs(mean) for v, mean in ((inlet, mean_in), (outlet, mean_out))),
                 headHistoryM=head_bins, minimumVaporFraction=max(0., 1-maximum), maximumVaporFraction=min(1., 1-minimum),
                 observedMaximumTimeStepS=max(actual_dt), observedMaxCo=co, observedMaxAlphaCo=alpha_co)
        pmin = min(v[0] for v in window["globalPressureMinimum"].values()); require(pmin > 0, "CFD_SNAPPY_PHASE_POSITIVE_ABSOLUTE_PRESSURE_REQUIRED")
        run["measurements"]["minimumStaticPressureAbsolutePa"] = pmin
        output.update(minimumStaticPressureAbsolutePa=pmin, rawGlobalWaterMinimum=minimum, rawGlobalWaterMaximum=maximum,
                      statisticalWindowS=[start, actual_end], continuityHistory=continuity, nativeHeadSampleExtremaM=[min(heads), max(heads)])
        require(co <= LIMITS["nativeCoLimit"] and alpha_co <= LIMITS["nativeAlphaCoLimit"]
                and max(actual_dt) <= manifest["temporalPlan"]["maximumTimeStepS"]*(1+1e-9), "CFD_SNAPPY_PHASE_ACTUAL_COURANT_DT_LIMIT_REQUIRED")
        require(max(mismatch) <= LIMITS["interfaceFluxRelativeLimit"], "CFD_SNAPPY_PHASE_ACTUAL_INTERFACE_FLUX_CONSERVATION_REQUIRED")
        coverage = native.parse_coverage(logs["checkMesh"]+"\n"+logs["foamRun"])
        require(all(v[0] >= .99 and v[1] >= .999 and v[2] <= 1.01 for pair in coverage for v in pair.values()), "CFD_SNAPPY_PHASE_NATIVE_COVERAGE_REQUIRED")
        require((max(heads)-min(heads))/abs(math.fsum(head_bins)/len(head_bins)) <= study["acceptance"]["headWindowRelativeLimit"], "CFD_SNAPPY_PHASE_ACTUAL_HEAD_STATIONARITY_REQUIRED")
        output.update(interfaceCoverageVerified=True, interfaceFluxRelativeMismatch=max(mismatch), nativeCoupledCoverage=coverage)
    except (protocol.ProtocolError, OSError, ValueError, KeyError, ZeroDivisionError) as error:
        output["diagnostics"].append({"branch": "active-phase-numerical-quality", "reasonCode": str(error)[:500]})
    run["nativeEvidence"]["sha256"] = evidence_hash(records); run["controlledVariationProof"]["samplingEvidenceSha256"] = evidence_hash(records)
    projected = protocol.reduce_run(study, run)
    output.update(nativeObservation=run, protocolReduction=projected, stages=projected["stages"], npshaDomainStatus=projected["npshaDomainStatus"])
    output["diagnostics"].extend(projected["diagnostics"])
    output["phaseObservationQualified"] = not output["diagnostics"] and projected["status"] == "QualifiedSuppliedObservation"
    eligible = output["phaseObservationQualified"] and q.get("maximumVaporFraction", 1) <= study["acceptance"]["liquidSectionVaporFractionLimit"] \
        and run["measurements"].get("minimumStaticPressureAbsolutePa", 0) > study["input"]["vaporPressurePa"] \
        and projected["stages"].get("head", {}).get("result", {}).get("value", 0) > 0
    output["phaseFamilyReferenceEligible"] = eligible; output["numericalPhaseReferenceQualified"] = eligible; output["phaseH0Eligible"] = eligible
    if eligible: output["activePhaseReferenceHeadM"] = projected["stages"]["head"]["result"]["value"]
    else: output["diagnostics"].append({"branch": "active-phase-reference", "reasonCode": "CFD_POSITIVE_HEAD_GLOBAL_LIQUID_HIGH_PRESSURE_REFERENCE_REQUIRED"})
    output["status"] = "ExploratoryActivePhaseReferenceNumericallyQualified" if eligible else "PhaseNumericalAcceptanceRequired"


def reduce_native_phase(execution_directory):
    output = {"schemaVersion": SCHEMA, "status": "NativePhaseEvidenceRequired", **{k: False for k in FALSE_CLAIMS},
              "actualCfdRunVerified": False, "simulationExecuted": False, "phaseObservationQualified": False,
              "phaseFamilyReferenceEligible": False, "numericalPhaseReferenceQualified": False, "phaseH0Eligible": False,
              "interfaceCoverageVerified": False, "stages": {}, "diagnostics": [],
              "qualificationScope": "Numerical high-pressure active Schnerr-Sauer fixed-MRF reference on this exact classroom approximation only.",
              "claimBoundary": "Active-phase head is independent of the source single-phase head. Numerical eligibility does not establish geometry accuracy, manufacturer NPSHr, pressure sweep, physical sliding, restart or refinement convergence."}
    records = {}
    try:
        root = native.case_tool.regular_directory(execution_directory); current = build_manifest(root)
        manifest, row = record(root, "phase-input-manifest.json")
        require(manifest == current["manifest"] and manifest.get("schemaVersion") == MANIFEST_SCHEMA, "CFD_SNAPPY_PHASE_CURRENT_PREEXECUTION_MANIFEST_REQUIRED")
        study = current["study"]; output.update(manifest=manifest, inputManifest=manifest, study=study, nativeInputManifestSha256=row["sha256"])
        add_record(records, row)
        for declared in manifest["files"]:
            _, actual = read(root, declared["path"], native.MAX_FIELD)
            require(actual == {k: declared[k] for k in ("path", "bytes", "sha256")}, "CFD_SNAPPY_PHASE_CURRENT_MANIFEST_BYTES_REQUIRED")
            add_record(records, actual)
        execution, logs, mesh, reconstruction = verify_execution(root, manifest, row, study, records)
        output.update(actualCfdRunVerified=True, simulationExecuted=True, endpointAbsolutePressureReconstruction=reconstruction,
                      status="NativeExecutionVerifiedNumericalAcceptanceRequired")
        observation(root, output, manifest, study, execution, logs, mesh, records)
        require(all(read(root, row["path"], native.MAX_FIELD)[1] == row for row in records.values()), "CFD_SNAPPY_PHASE_POSTREDUCTION_IMMUTABILITY_REQUIRED")
    except (protocol.ProtocolError, OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
        for key in ("phaseObservationQualified", "phaseFamilyReferenceEligible", "numericalPhaseReferenceQualified", "phaseH0Eligible"): output[key] = False
        output.pop("activePhaseReferenceHeadM", None)
        output["diagnostics"].append({"branch": "native-active-phase-evidence", "reasonCode": str(error)[:500]})
        if output["actualCfdRunVerified"]: output["status"] = "PhaseNumericalAcceptanceRequired"
        if str(error) == "CFD_SNAPPY_PHASE_POSTREDUCTION_IMMUTABILITY_REQUIRED":
            output["actualCfdRunVerified"] = False; output["status"] = "NativeEvidenceChangedDuringReduction"
            output["stages"] = {}; output.pop("nativeObservation", None); output.pop("protocolReduction", None)
    output["nativeEvidenceRecords"] = sorted(records.values(), key=lambda r: r["path"])
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--execution-directory", type=Path, required=True)
    args = parser.parse_args(); result = reduce_native_phase(args.execution_directory); raw = encoded(result)
    require(len(raw) <= MAX_REDUCTION_BYTES, "CFD_SNAPPY_PHASE_BOUNDED_REDUCTION_REQUIRED")
    target = native.case_tool.inventory.output_path(args.execution_directory / "phase-native-reduction.json")
    native.case_tool.write_exclusive(target, raw)
    visible = {k: result[k] for k in ("status", "actualCfdRunVerified", "numericalPhaseReferenceQualified", "qualifiedBaseline", "npsh3Qualified", "mainApplicationWrites")}
    if result["diagnostics"]:
        visible["diagnostics"] = result["diagnostics"][:6]
        visible["correctiveAction"] = "Review the retained pressure, sampling, convergence and conservation diagnostics; correct the declared phase inputs and rerun the current native proof."
    print(json.dumps(visible))
    return 0 if result["actualCfdRunVerified"] and result["numericalPhaseReferenceQualified"] else 2


if __name__ == "__main__": raise SystemExit(main())
