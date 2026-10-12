"""Accept actual active Schnerr-Sauer reference evidence for authored SI CAD.

This read-only reducer launches nothing. A single-phase teaching baseline is
preparation provenance only: active-phase H0 requires its own current native
fields, tables, convergence and liquid-domain evidence on the exact copied mesh.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import math
from pathlib import Path, PurePosixPath
import re
import sys
import json

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_native_reduction as native
import frozen_rotor_research_protocol as protocol
import reduce_frozen_rotor_snappy_phase as equations  # Pure numerical projections only.
import frozen_rotor_phase_reduction as tables
import run_frozen_rotor_phase as fields
import reduce_teaching_pump_baseline as baseline
import teaching_pump_phase_case as preparation

SCHEMA = "untirta.teaching-pump-phase-reference-acceptance.v1"
MANIFEST_SCHEMA = "untirta.teaching-pump-phase-input-manifest.v1"
STUDY_SCHEMA = "untirta.teaching-pump-phase-study.v1"
EXECUTION_SCHEMA = "untirta.teaching-pump-phase-reference-execution.v1"
RUNTIME_SCHEMA = "untirta.teaching-pump-phase-runtime-identity.v1"
REPORT_FILE = "teaching-phase-acceptance.json"
MANIFEST_FILE = "teaching-phase-input-manifest.json"
STUDY_FILE = "teaching-phase-study.json"
RUNTIME_FILE = "teaching-phase-runtime-identity.json"
EXECUTION_FILE = "teaching-phase-execution.json"
ROUTE = "CLASSROOM_AUTHORED_GEOMETRY"
MAX_REDUCTION_BYTES, MAX_LOG = 16*1024**2, 8*1024**2
PROGRAMS = ("checkMesh", "foamRun")
FALSE_CLAIMS = ("qualifiedBaseline", "numericalBaselineQualified", "preliminaryBaselineQualified",
    "wallResolutionVerified", "meshConvergenceVerified", "timeStepConvergenceVerified",
    "physicalValidationVerified", "cavitationSweepExecuted", "pressureSweepEnabled", "npshrCalculated",
    "npsh3Qualified", "physicalTransientExecuted", "slidingEnabled", "restartVerified",
    "phaseFamilyReferenceQualified", "manufacturerPublishedGeometry", "originalFluentGeometryReproduced",
    "baselineHeadUsedAsPhaseH0", "baselineFieldsCopied", "mainApplicationWrites")
require, encoded, read, record = native.require, native.encoded, native.read, native.record
add_record, evidence_hash = equations.add_record, equations.evidence_hash
absolute_pressure_reconstruction = equations.absolute_pressure_reconstruction


def binary_identity(identity):
    fixed = {"schemaVersion": RUNTIME_SCHEMA, "openFoamFoundationVersion": 12,
        "packageVersion": "20250206", "officialDebSha256": preparation.safety.PACKAGE_SHA,
        "expectedSourceCommit": preparation.safety.SOURCE_COMMIT, "mainApplicationWrites": False}
    require(isinstance(identity, dict) and set(identity) == set(fixed)|{"binaries"}
        and all(identity.get(k) == v and type(identity.get(k)) is type(v) for k, v in fixed.items()),
        "TEACHING_PHASE_EXACT_TYPED_RUNTIME_REQUIRED")
    rows = identity.get("binaries")
    require(isinstance(rows, list) and len(rows) == 2 and {r.get("program") for r in rows} == set(PROGRAMS),
        "TEACHING_PHASE_TWO_CURRENT_NATIVE_BINARIES_REQUIRED")
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"program", "path", "bytes", "sha256", "packageOwnership"}
            and type(row.get("bytes")) is int and 0 < row["bytes"] <= native.MAX_FIELD
            and protocol.digest(row.get("sha256")), "TEACHING_PHASE_NATIVE_BINARY_DESCRIPTOR_REQUIRED")
        pure = PurePosixPath(row.get("path", ""))
        require(pure.is_absolute() and pure.is_relative_to(PurePosixPath("/opt/openfoam12"))
            and ".." not in pure.parts and "\\" not in row["path"] and pure.name == row["program"]
            and row["packageOwnership"] == "openfoam12: "+row["path"], "TEACHING_PHASE_OFFICIAL_BINARY_REQUIRED")
        raw = native.case_tool.read_regular(Path(row["path"]), native.MAX_FIELD)
        require(len(raw) == row["bytes"] and sha256(raw).hexdigest() == row["sha256"],
            "TEACHING_PHASE_CURRENT_NATIVE_BINARY_BYTES_REQUIRED")


def study_packet(parameters, controls, prep):
    study = baseline.study_packet(parameters, controls)
    require(study["input"] == prep["declaredStudyInputs"] and study["physicalBasis"] == prep["physicalBasis"]
        and study["sourceReference"] == prep["sourceReference"], "TEACHING_PHASE_AUTHORED_SI_PHYSICAL_FAMILY_REQUIRED")
    study["schemaVersion"] = STUDY_SCHEMA
    study["signatureScope"].update({"meshSha256": "Exact current native-relative six-file coupled mesh",
        "boundarySha256": "Exact new active-phase initial/physical BCs and fixed MRF; no pressure normalization",
        "discretizationSha256": "Exact adaptive relaxation clock, samplers and schemes; no clock normalization",
        "runtimeSha256": "Exact two installed Foundation12 package-owned executables",
        "cavitationModelSha256": "Exact authored active SchnerrSauer water/vapour phase-change dictionary bytes"})
    study["declaredAssumptions"]["phaseInitialization"] = prep["initialization"]
    study["declaredAssumptions"]["relaxationClock"] = (
        "Four declared rotation periods with fixed mesh/MRF, not physical blade motion or verified residence-time adequacy")
    return study


def build_manifest(execution_directory, *, after_native_execution=False):
    """Return {manifest, study}; persist both canonical files before native calls."""
    root = native.case_tool.regular_directory(execution_directory)
    require(type(after_native_execution) is bool, "TEACHING_PHASE_EXPLICIT_NATIVE_VERIFICATION_STAGE_REQUIRED")
    verified = preparation.verify_output(root,after_native_execution=after_native_execution)
    prep, prep_row = record(root, preparation.REPORT)
    require(prep == verified["report"] and prep.get("schemaVersion") == preparation.SCHEMA
        and prep.get("mode") == preparation.MODE and prep.get("sourceGeometryRoute") == ROUTE
        and prep.get("inletAbsoluteTotalPressurePa") == preparation.PRESSURE_PA
        and all(prep.get(k) is True for k in preparation.SOURCE_TRUE_FLAGS)
        and all(prep.get(k) is False for k in preparation.FALSE_FLAGS), "TEACHING_PHASE_CURRENT_PREPARATION_REQUIRED")
    runtime, runtime_row = record(root, RUNTIME_FILE); binary_identity(runtime)
    identity, identity_row = record(root, baseline.GEOMETRY_FILE)
    require(identity_row == prep["sourceGeometryIdentityFile"]
        and identity.get("provenance") == ROUTE and identity.get("units") == "SI"
        and identity.get("manufacturerIdentity") is None and identity.get("originalFluentSourceIdentity") is None
        and sha256(encoded([identity_row])).hexdigest() == prep["sourceGeometryControlSha256"],
        "TEACHING_PHASE_EXACT_COPIED_AUTHORED_IDENTITY_REQUIRED")
    rows = [{**prep_row, "control": None}, {**identity_row, "control": "geometrySha256"},
        {**runtime_row, "control": "runtimeSha256"}]
    group = {key: [] for key in protocol.CONTROL_KEYS}
    group["geometrySha256"], group["runtimeSha256"] = [identity_row], [runtime_row]
    source = preparation.records(root, prep["sourceEvidenceFileRecords"])
    rows.extend({**row, "control": None} for row in source)
    for declared in prep["preservedNativeMeshFiles"]:
        actual = read(root, "case/"+declared["path"], native.MAX_FIELD)[1]
        require(actual == {**declared, "path": "case/"+declared["path"]}, "TEACHING_PHASE_CURRENT_COPIED_MESH_REQUIRED")
        rows.append({**actual, "control": "meshSha256"}); group["meshSha256"].append(declared)
    for declared in prep["authoredInputFiles"]:
        actual = read(root, "case/"+declared["path"], native.MAX_FIELD)[1]
        require(actual == {**declared, "path": "case/"+declared["path"]}, "TEACHING_PHASE_CURRENT_AUTHORED_INPUT_REQUIRED")
        key = "cavitationModelSha256" if declared["path"] in preparation.MODEL_FILES else \
            "discretizationSha256" if declared["path"].startswith("system/") else "boundarySha256"
        rows.append({**actual, "control": key}); group[key].append(declared)
    require(len(rows) <= 384 and len({r["path"] for r in rows}) == len(rows), "TEACHING_PHASE_UNIQUE_BOUNDED_MANIFEST_REQUIRED")
    controls = {key: sha256(encoded(sorted(values, key=lambda r:r["path"]))).hexdigest() for key,values in group.items()}
    require(all(group.values()) and controls["cavitationModelSha256"] == prep["authoredCavitationModelSha256"],
        "TEACHING_PHASE_ACTIVE_MODEL_CONTROL_REQUIRED")
    source_input = record(root, "source-baseline/source-teaching/source-geometry/teaching-input-manifest.json")[0]
    source_acceptance = record(root, prep["sourceBaselineAcceptanceFile"]["path"])[0]
    study = study_packet(source_input["parameters"], controls, prep)
    manifest = {"schemaVersion": MANIFEST_SCHEMA, "sourceGeometryRoute": ROUTE,
        "files": sorted(rows, key=lambda r:r["path"]), "controls": controls,
        "structuralSignatureRows": {k:sorted(v,key=lambda r:r["path"]) for k,v in sorted(group.items())},
        "structuralSignatureMethod": "Exact actual bytes with native-relative case paths for control groups; no scalar/clock normalization",
        "signatureScope": study["signatureScope"], "studyInputs": study["input"], "physicalBasis": study["physicalBasis"],
        "samplingDefinition": study["samplingDefinition"], "physicalModel": "steady-cavitation-MRF", "mode": preparation.MODE,
        "pressureLevelAbsolutePa": preparation.PRESSURE_PA, "temporalPlan": prep["temporalPlan"],
        "preparationReportSha256": prep_row["sha256"], "runtimeIdentitySha256": runtime_row["sha256"],
        "sourceBaselineEvidenceSha256": prep["sourceBaselineEvidenceSha256"],
        "sourceCaseManifestSha256": prep["sourceCaseManifestSha256"],
        "sourceBaselineAcceptanceFile": prep["sourceBaselineAcceptanceFile"],
        "sourceGithubRunId": source_acceptance["manifest"]["sourceGithubRunId"],
        "sourceGeneratorCommit": source_input["generatorSourceCommit"],
        "sourceBaselineRole": "Numerically accepted single-phase preparation provenance only; its head is never active-phase H0",
        "absolutePressureMapping": prep["absolutePressureMapping"], "inletElevationM": 0., "outletElevationM": 0.,
        "elevationBasis": study["physicalBasis"]["elevationDefinition"], "coordinateBasis": "Explicit authored SI metres",
        "classroomAcceptance": dict(equations.LIMITS), "simulationExecuted": False, "mainApplicationWrites": False}
    require(len(encoded(manifest)) <= native.MAX_RECORD, "TEACHING_PHASE_BOUNDED_MANIFEST_REQUIRED")
    return {"manifest": manifest, "study": study}


def verify_execution(root, manifest, manifest_row, study, records):
    import run_teaching_pump_phase as runner  # Only constants and read-only scanners.
    execution, execution_row = record(root, EXECUTION_FILE)
    raw_study, study_row = read(root, STUDY_FILE, native.MAX_RECORD)
    require(raw_study == encoded(study), "TEACHING_PHASE_CURRENT_STUDY_REQUIRED")
    true_flags = ("actualCfdRunVerified", "simulationExecuted", "nativeCheckMeshVerified", "nativeGeometryQualityVerified",
        "physicalWallTypesVerified", "coupledInterfaceCoverageVerified", "sourceNumericalBaselineQualified",
        "stationaryMrfRelaxationExecuted", "nativeSolverCompleted", "cavitationModelExecuted",
        "finiteNativeEndFieldsVerified", "inputArtifactsImmutable", "sourceBaselineImmutable")
    require(execution.get("schemaVersion") == EXECUTION_SCHEMA
        and execution.get("status") == "TEACHING_ACTIVE_PHASE_REFERENCE_EXECUTED_ACCEPTANCE_REQUIRED"
        and execution.get("sourceGeometryRoute") == ROUTE and execution.get("mode") == preparation.MODE
        and execution.get("physicalModel") == "steady-cavitation-MRF"
        and execution.get("githubRepository") == "simcalc/PyAnsys"
        and isinstance(execution.get("githubRunId"), str) and re.fullmatch(r"[1-9][0-9]{0,19}",execution["githubRunId"])
        and isinstance(execution.get("githubSourceCommit"), str) and re.fullmatch(r"[0-9a-f]{40}",execution["githubSourceCommit"])
        and execution.get("inletAbsoluteTotalPressurePa") == manifest["pressureLevelAbsolutePa"]
        and execution.get("absolutePressureMapping") == manifest["absolutePressureMapping"]
        and all(execution.get(k) is True for k in true_flags) and all(execution.get(k) is False for k in runner.FALSE_FLAGS)
        and execution.get("preparationReportSha256") == manifest["preparationReportSha256"]
        and execution.get("runtimeIdentitySha256") == manifest["runtimeIdentitySha256"]
        and execution.get("inputManifestSha256") == manifest_row["sha256"]
        and execution.get("studySha256") == study_row["sha256"]
        and execution.get("sourceBaselineEvidenceSha256") == manifest["sourceBaselineEvidenceSha256"]
        and execution.get("authoredCavitationModelSha256") == manifest["controls"]["cavitationModelSha256"]
        and execution.get("temporalPlan") == manifest["temporalPlan"], "TEACHING_PHASE_ACTUAL_BOUND_NATIVE_EXECUTION_REQUIRED")
    limits = execution.get("limits")
    require(isinstance(limits, dict) and set(limits) == set(runner.LIMITS)
        and all(limits[k] == v and type(limits[k]) is type(v) for k,v in runner.LIMITS.items())
        and 0 <= protocol.number(execution.get("elapsedSeconds"),nonnegative=True) < runner.LIMITS["maximumSeconds"]
        and type(execution.get("workspaceBytes")) is int and 0 < execution["workspaceBytes"] <= runner.LIMITS["maximumWorkspaceBytes"],
        "TEACHING_PHASE_EXACT_BOUNDED_EXECUTION_REQUIRED")
    add_record(records, execution_row); add_record(records, study_row)
    stages = execution.get("stages")
    require(isinstance(stages,list) and len(stages) == 2 and [s.get("program") for s in stages] == list(PROGRAMS),
        "TEACHING_PHASE_TWO_NATIVE_STAGES_REQUIRED")
    logs = {}; actual_runtime = record(root,RUNTIME_FILE)[0]
    command_rows = preparation.historical_commands(preparation.historical_command_root(record(root,preparation.REPORT)[0]))
    for index,item in enumerate(stages):
        require(type(item.get("exitCode")) is int and item["exitCode"] == 0
            and item.get("log") == f"{index:02d}-{PROGRAMS[index]}.log"
            and protocol.number(item.get("elapsedSeconds"),nonnegative=True) < runner.LIMITS["maximumSeconds"],
            "TEACHING_PHASE_EXACT_COMPLETED_STAGE_REQUIRED")
        raw,row = read(root,"logs/"+item["log"],MAX_LOG)
        require(row["bytes"] == item.get("logBytes") and row["sha256"] == item.get("logSha256"),
            "TEACHING_PHASE_CURRENT_NATIVE_LOG_REQUIRED")
        text = raw.decode("utf-8"); preparation.geometry.completed_log(text)
        binary = next(b for b in actual_runtime["binaries"] if b["program"] == PROGRAMS[index])
        require(re.findall(r"(?m)^Build\s*:\s*(.*?)\s*$",text) == ["12-86e126a7bc4d"]
            and re.findall(r"(?m)^nProcs\s*:\s*(.*?)\s*$",text) == ["1"]
            and re.findall(r"(?m)^Exec\s*:\s*(.*?)\s*$",text) == [" ".join([binary["path"],*command_rows[index][1:]])],
            "TEACHING_PHASE_ACTUAL_NATIVE_PROGRAM_ARGV_BUILD_REQUIRED")
        logs[item["program"]] = text; add_record(records,row)
    require(math.fsum(s["elapsedSeconds"] for s in stages) <= execution["elapsedSeconds"]+.01,
        "TEACHING_PHASE_NATIVE_STAGE_TIMING_REQUIRED")
    require(re.findall(r"(?m)^Selecting cavitation model (\w+)\s*$",logs["foamRun"]) == ["SchnerrSauer"]
        and not re.search(r"Selecting solidBodyMotionFunction|Selecting motion solver",logs["foamRun"]),
        "TEACHING_PHASE_ACTUAL_FIXED_MRF_SCHNERR_SAUER_REQUIRED")
    prep = record(root,preparation.REPORT)[0]
    preparation.baseline_case.geometry_runner.verify_region_count(logs["checkMesh"],2)
    measured = native.case_tool.verify_merged_mesh(root/"case",prep["components"],root/"logs/00-checkMesh.log")
    require(measured == execution.get("mergedNativeMeshEvidence"), "TEACHING_PHASE_CURRENT_COPIED_MESH_REQUIRED")
    mesh = native.parse_mesh_quality(logs["checkMesh"])
    require(mesh == execution.get("nativeMeshQuality") and mesh["meshCellCount"] == measured["mergedCellCount"],
        "TEACHING_PHASE_CURRENT_NATIVE_QUALITY_REQUIRED")
    final_rows,_ = runner.final_fields(root,manifest["temporalPlan"],mesh["meshCellCount"],logs["foamRun"])
    require(final_rows == execution.get("finalNativeFieldRecords"), "TEACHING_PHASE_ALL_SEVEN_CURRENT_ENDPOINT_FIELDS_REQUIRED")
    require(math.isclose(protocol.number(execution.get("nativeEndTimeS"),positive=True),manifest["temporalPlan"]["endTimeS"],rel_tol=5e-11,abs_tol=1e-12),
        "TEACHING_PHASE_ACTUAL_END_TIME_REQUIRED")
    endpoint = {Path(r["path"]).name:r for r in final_rows}
    reconstruction = absolute_pressure_reconstruction(read(root,endpoint["p"]["path"],native.MAX_FIELD)[0],
        read(root,endpoint["p_rgh"]["path"],native.MAX_FIELD)[0],mesh["meshCellCount"])
    for row in final_rows: add_record(records,{k:row[k] for k in ("path","bytes","sha256")})
    sampling = runner.sampling_records(root); diagnostics = runner.ncc_diagnostic_records(root)
    require(sampling and sampling == execution.get("nativeSamplingRecords")
        and diagnostics == execution.get("nativeNccDiagnosticRecords"), "TEACHING_PHASE_COMPLETE_NATIVE_TABLE_NCC_INVENTORY_REQUIRED")
    roles = equations.sampler_roles()
    for row in sampling:
        match = re.fullmatch(r"case/postProcessing/([A-Za-z0-9_-]+)/("+fields.TIME_NAME+r")/(surfaceFieldValue\.dat|volFieldValue\.dat|yPlus\.dat)",row["path"])
        require(match and 0 <= float(match[2]) <= manifest["temporalPlan"]["endTimeS"]
            and (match[1] in roles and match[3] == ("surfaceFieldValue.dat" if roles[match[1]][1] else "volFieldValue.dat")
            or match[1] == "wallYPlus" and match[3] == "yPlus.dat"), "TEACHING_PHASE_EXACT_AUTHORED_SAMPLER_ROLE_REQUIRED")
        add_record(records,row)
    for row in diagnostics: add_record(records,row)
    near = runner.baseline.near_points_evidence(root,case_relative="case",log_relative="logs/00-checkMesh.log",
        authored_report_relative="source-baseline/source-teaching/source-geometry/teaching-geometry.json")
    require(execution.get("nativeCheckMeshDiagnosticRecords",[]) == near["files"]
        and execution.get("nativeNearPointsEvidence") == near["evidence"],
        "TEACHING_PHASE_CURRENT_NEAR_POINTS_DIAGNOSTIC_REQUIRED")
    for row in near["files"]:
        require(read(root,row["path"],native.MAX_FIELD)[1] == row, "TEACHING_PHASE_CURRENT_NEAR_POINTS_BYTES_REQUIRED")
        add_record(records,row)
    generated = preparation.verify_output(root,after_native_execution=True)
    require(execution.get("nativeGeneratedDataRecords",[]) == generated["nativeGeneratedDataRecords"]
        and execution.get("nativeGeneratedDataEvidence",[]) == generated["nativeGeneratedDataEvidence"],
        "TEACHING_PHASE_CURRENT_NATIVE_GENERATED_DATA_REQUIRED")
    for row in generated["nativeGeneratedDataRecords"]:
        require(read(root,row["path"],native.MAX_FIELD)[1] == row, "TEACHING_PHASE_CURRENT_NATIVE_GENERATED_BYTES_REQUIRED")
        add_record(records,row)
    current_bytes = runner.workspace_size(root)-execution_row["bytes"]
    if (root/REPORT_FILE).exists(): current_bytes -= len(native.case_tool.read_regular(root/REPORT_FILE,MAX_REDUCTION_BYTES))
    require(current_bytes == execution["workspaceBytes"], "TEACHING_PHASE_CURRENT_WORKSPACE_BYTES_REQUIRED")
    extrema = {"pMinimumAbsolutePa":endpoint["p"]["fieldCheck"]["minimum"],
        "waterMinimum":endpoint["alpha.water"]["fieldCheck"]["minimum"],
        "waterMaximum":endpoint["alpha.water"]["fieldCheck"]["maximum"],"allInternalCellsChecked":mesh["meshCellCount"]}
    return execution,logs,mesh,reconstruction,extrema


def observation(root,output,manifest,study,execution,logs,mesh,records,extrema):
    """Reuse only actual table/log mathematics; keep authored scope distinct."""
    equations.observation(root,output,manifest,study,execution,logs,mesh,records)
    endpoint = manifest["temporalPlan"]["endTimeS"]
    try:
        for name,columns,key in (("globalPressureMinimum",("min(p)",),"pMinimumAbsolutePa"),
            ("globalWaterMinimum",("min(alpha.water)",),"waterMinimum"),
            ("globalWaterMaximum",("max(alpha.water)",),"waterMaximum")):
            actual,row = tables.sampler(root,name,columns,None); add_record(records,row)
            time,value = next(reversed(actual.items()))
            require(math.isclose(time,endpoint,rel_tol=5e-11,abs_tol=1e-12)
                and math.isclose(value[0],extrema[key],rel_tol=2e-8,abs_tol=2e-4 if key.startswith("p") else 2e-8),
                "TEACHING_PHASE_ENDPOINT_GLOBAL_FIELD_TABLE_RECONCILIATION_REQUIRED")
        output["endpointGlobalFieldTableConsistencyVerified"] = True
    except (protocol.ProtocolError,OSError,ValueError,KeyError) as error:
        output["diagnostics"].append({"branch":"endpoint-global-fields","reasonCode":str(error)[:500]})
    output["phaseObservationQualified"] = output["phaseObservationQualified"] and output["endpointGlobalFieldTableConsistencyVerified"]
    eligible = not output["diagnostics"] and output["phaseH0Eligible"]
    for key in ("phaseFamilyReferenceEligible","numericalPhaseReferenceQualified","phaseH0Eligible"):
        output[key] = eligible
    if not eligible: output.pop("activePhaseReferenceHeadM",None)
    output["status"] = "AuthoredActivePhaseReferenceNumericallyQualified" if eligible else "PhaseNumericalAcceptanceRequired"


def reduce_phase(execution_directory):
    output = {"schemaVersion":SCHEMA,"sourceGeometryRoute":ROUTE,"status":"NativePhaseEvidenceRequired",
        **{k:False for k in FALSE_CLAIMS},"actualCfdRunVerified":False,"simulationExecuted":False,
        "phaseObservationQualified":False,"phaseFamilyReferenceEligible":False,"numericalPhaseReferenceQualified":False,
        "phaseH0Eligible":False,"interfaceCoverageVerified":False,"endpointGlobalFieldTableConsistencyVerified":False,
        "stages":{},"diagnostics":[],"qualificationScope":"Numerical high-pressure active Schnerr-Sauer fixed-MRF reference on this exact authored SI classroom mesh only",
        "claimBoundary":"Active-phase H0 is independent of the prerequisite single-phase head. No manufacturer result, NPSHr, pressure sweep, physical sliding, restart or refinement qualification follows."}
    records = {}
    try:
        root = native.case_tool.regular_directory(execution_directory); current = build_manifest(root,after_native_execution=True)
        manifest,row = record(root,MANIFEST_FILE)
        require(manifest == current["manifest"] and manifest.get("schemaVersion") == MANIFEST_SCHEMA,
            "TEACHING_PHASE_CURRENT_PREEXECUTION_MANIFEST_REQUIRED")
        study = current["study"]
        output.update(manifest=manifest,inputManifest=manifest,study=study,nativeInputManifestSha256=row["sha256"]);add_record(records,row)
        for declared in manifest["files"]:
            actual = read(root,declared["path"],native.MAX_FIELD)[1]
            require(actual == {k:declared[k] for k in ("path","bytes","sha256")}, "TEACHING_PHASE_CURRENT_MANIFEST_BYTES_REQUIRED")
            add_record(records,actual)
        execution,logs,mesh,reconstruction,extrema = verify_execution(root,manifest,row,study,records)
        output.update(actualCfdRunVerified=True,simulationExecuted=True,endpointAbsolutePressureReconstruction=reconstruction,
            endpointInternalFieldExtrema=extrema,status="NativeExecutionVerifiedNumericalAcceptanceRequired")
        observation(root,output,manifest,study,execution,logs,mesh,records,extrema)
        require(all(read(root,r["path"],native.MAX_FIELD)[1] == r for r in records.values()),
            "TEACHING_PHASE_POSTREDUCTION_IMMUTABILITY_REQUIRED")
    except (protocol.ProtocolError,OSError,ValueError,KeyError,TypeError,UnicodeError) as error:
        for key in ("phaseObservationQualified","phaseFamilyReferenceEligible","numericalPhaseReferenceQualified","phaseH0Eligible"):
            output[key] = False
        output.pop("activePhaseReferenceHeadM",None)
        output["diagnostics"].append({"branch":"native-active-phase-evidence","reasonCode":str(error)[:500]})
        if output["actualCfdRunVerified"]: output["status"] = "PhaseNumericalAcceptanceRequired"
        if str(error) == "TEACHING_PHASE_POSTREDUCTION_IMMUTABILITY_REQUIRED":
            output.update(actualCfdRunVerified=False,simulationExecuted=False,status="NativeEvidenceChangedDuringReduction",stages={})
            output.pop("nativeObservation",None);output.pop("protocolReduction",None)
    output["nativeEvidenceRecords"] = sorted(records.values(),key=lambda r:r["path"])
    return output


reduce_native_phase = reduce_phase


def main():
    parser = argparse.ArgumentParser(description=__doc__);parser.add_argument("--execution-directory",type=Path,required=True)
    args = parser.parse_args();root = preparation.safety.scope_path(args.execution_directory)
    result = reduce_phase(root);raw = encoded(result)
    require(len(raw) <= MAX_REDUCTION_BYTES,"TEACHING_PHASE_BOUNDED_ACCEPTANCE_REQUIRED")
    native.case_tool.write_exclusive(root/REPORT_FILE,raw)
    print(json.dumps({k:result[k] for k in ("status","actualCfdRunVerified","numericalPhaseReferenceQualified","phaseH0Eligible","qualifiedBaseline","npsh3Qualified","mainApplicationWrites")}))
    return 0 if result["actualCfdRunVerified"] and result["phaseH0Eligible"] else 2


if __name__ == "__main__":raise SystemExit(main())
