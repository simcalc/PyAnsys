"""Bounded four-stage native inlet-flow/static-outlet classroom execution.

Only this distinct family's source/preparer/reducer supplies scientific input
admission. Shared helpers execute owned processes or read native data; they do
not inherit baseline qualification, pressure identity or hydraulic decisions.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_teaching_pump_baseline as low
import teaching_pump_inlet_flow_recipe as recipe

base,safety,native,geometry,scientific=low.base,low.safety,low.native,low.geometry,low.scientific
SCHEMA="untirta.teaching-pump-inlet-flow-execution.v1"
PREPARATION_SCHEMA="untirta.teaching-pump-inlet-flow-preparation.v1"
RUNTIME_SCHEMA="untirta.teaching-pump-inlet-flow-runtime-identity.v1"
REPORT="teaching-inlet-flow-execution.json"
RUNTIME="teaching-inlet-flow-runtime-identity.json"
MANIFEST="teaching-inlet-flow-input-manifest.json"
STUDY="teaching-inlet-flow-study.json"
IDENTITY="teaching-inlet-flow-geometry-identity.json"
SUCCESS="TEACHING_INLET_FLOW_MRF_EXECUTED_NUMERICAL_ACCEPTANCE_REQUIRED"
PROGRAMS=("mergeMeshes","createNonConformalCouples","checkMesh","foamRun")
LIMITS=deepcopy(low.LIMITS)
record,workspace_size,require=low.record,low.workspace_size,low.require
commands,final_fields,sampling_records=low.commands,low.final_fields,low.sampling_records
ncc_diagnostic_records,near_points_evidence,retain_logs=low.ncc_diagnostic_records,low.near_points_evidence,low.retain_logs
EvidenceDeadline,EvidenceDeadlineIssue,check_deadline=low.EvidenceDeadline,low.EvidenceDeadlineIssue,low.check_deadline
SOURCE_KEYS=("sourceGeometryExecutionReportSha256","sourceGeometryPreparationReportSha256","sourceGeometryInputManifestSha256",
    "sourceAuthoredGeometryReportSha256","sourceGeometryIdentitySha256")


def runtime_identity(output,deadline):
    """Bind actual installed package/binary bytes in this new runtime namespace."""
    def query(arguments):
        remaining=deadline-time.monotonic();require(remaining>0,"The inlet-flow deadline expired during package verification.")
        result=subprocess.run(arguments,env=native.child_environment(),capture_output=True,text=True,timeout=min(10,remaining),check=True)
        require(len(result.stdout)<=8192 and len(result.stderr)<=8192,"Bound each actual package query output.")
        return result.stdout
    require(query(["dpkg-query","--show","--showformat=${Version}","openfoam12"])=="20250206","Require the pinned official Foundation12 package.")
    binaries=[]
    for program in PROGRAMS:
        found=shutil.which(program);require(found,"An official inlet-flow utility is absent: "+program)
        path=Path(found).resolve();require(path.is_relative_to(Path("/opt/openfoam12")) and path.name==program,"Only exact package-owned utilities may execute.")
        owner=query(["dpkg-query","--search",str(path)]).strip()
        require(owner=="openfoam12: "+str(path),"Require exact actual package ownership for every utility.")
        _,row=safety.descriptor(path)
        binaries.append({**row,"program":program,"path":str(path),"packageOwnership":owner})
    check_deadline(deadline,"actual inlet-flow binary binding")
    identity={"schemaVersion":RUNTIME_SCHEMA,"openFoamFoundationVersion":12,"packageVersion":"20250206",
        "officialDebSha256":safety.PACKAGE_SHA,"expectedSourceCommit":safety.SOURCE_COMMIT,"binaries":binaries,"mainApplicationWrites":False}
    base.write_exclusive(Path(output)/RUNTIME,base.inventory.bounded_json(identity));return identity


def execute(arguments,output,deadline,runtime,index):
    require(isinstance(runtime,dict) and runtime.get("schemaVersion")==RUNTIME_SCHEMA
        and type(runtime.get("openFoamFoundationVersion")) is int and runtime["openFoamFoundationVersion"]==12,
        "An old or unknown runtime namespace cannot execute this inlet-flow family.")
    # The shared owned-process executor independently validates exact indexed
    # arguments, binary ownership/SHA, secret-free environment and all bounds.
    return low.execute(arguments,output,deadline,runtime,index)


def bind_manifest(output):
    import reduce_teaching_pump_inlet_flow as reduction
    bound=reduction.build_manifest(output)
    for name,key in ((IDENTITY,"geometryIdentity"),(MANIFEST,"manifest"),(STUDY,"study")):
        raw=reduction.encoded(bound[key]);require(0<len(raw)<=LIMITS["maximumReportBytes"],"Bound every new canonical family identity, manifest and study.")
        base.write_exclusive(Path(output)/name,raw)
    return {name:record(output,name,LIMITS["maximumReportBytes"])[1] for name in (IDENTITY,MANIFEST,STUDY)},bound["manifest"]["files"]


def generated_data(preparation,output,*,after_native_execution=False):
    rows,proof=preparation.generated_data(output,after_native_execution=after_native_execution)
    require(isinstance(rows,list) and isinstance(proof,list) and len(rows)==len(proof),"Each generated native data role requires its current-byte proof.")
    require(all(record(output,row["path"])[1]==row for row in rows),"Generated native data bytes changed during current evidence binding.")
    return rows,proof


def run(geometry_native_directory,output_directory):
    started=time.monotonic();deadline=started+LIMITS["maximumSeconds"];safety.context_guard()
    require(LIMITS==low.LIMITS,"This new family reuses the exact unchanged reviewed native resource profile.")
    source=safety.scope_path(geometry_native_directory);output=safety.scope_path(output_directory,fresh=True)
    import teaching_pump_inlet_flow_case as preparation
    with preparation.SourceProofTransaction(source,output/"source-teaching",deadline=deadline):
        return _run(source,output,started,deadline,preparation)


def _run(source,output,started,deadline,preparation):
    check_deadline(deadline,"new inlet-flow source preparation")
    prepared=preparation.prepare(source,output);verified=preparation.verify_output(output);output=base.regular_directory(output)
    require(prepared==verified["report"] and prepared["schemaVersion"]==PREPARATION_SCHEMA
        and prepared["boundaryFamilyId"]==recipe.BC_FAMILY and prepared["nativeCommandArguments"]==commands(output),
        "Require this distinct source-bound inlet-flow/static-outlet four-stage preparation.")
    mapping=prepared["absolutePressureMapping"]
    require(mapping.get("referenceLocation")=="outlet" and mapping.get("referenceQuantity")=="static absolute pressure"
        and type(mapping.get("referencePa")) in (int,float) and mapping["referencePa"]==recipe.OUTLET_STATIC_REFERENCE_PA
        and type(mapping.get("referenceAdditionCount")) is int and mapping["referenceAdditionCount"]==1,
        "The declared pressure is outlet static reference, added exactly once; inlet pressure must be sampled.")
    report={"schemaVersion":SCHEMA,"sourceGeometryRoute":"CLASSROOM_AUTHORED_GEOMETRY","boundaryFamilyId":recipe.BC_FAMILY,
        "githubRepository":"simcalc/PyAnsys","githubRunId":os.environ.get("GITHUB_RUN_ID"),"githubSourceCommit":os.environ.get("GITHUB_SHA"),
        "status":"TEACHING_INLET_FLOW_NATIVE_BASELINE_REQUIRED","limits":deepcopy(LIMITS),"stages":[],
        "preparationReportSha256":verified["reportFile"]["sha256"],**{key:prepared[key] for key in SOURCE_KEYS},
        "outletStaticAbsoluteReferencePa":recipe.OUTLET_STATIC_REFERENCE_PA,"inletTotalPressurePrescribed":False,"absolutePressureMapping":deepcopy(mapping),
        **dict.fromkeys(("nativeGeometryQualityVerified","physicalWallTypesVerified","coupledInterfaceCoverageVerified","nativeSolverCompleted",
            "finiteNativeFieldsVerified","inputArtifactsImmutable","sourceGeometryImmutable","simulationExecuted","actualCfdRunVerified",
            "numericalBaselineQualified","qualifiedBaseline","wallResolutionQualified","meshConvergenceVerified","timeConvergenceVerified",
            "cavitationSweepExecuted","npsh3Qualified","physicalTransientExecuted","restartVerified","manufacturerGeometryVerified","mainApplicationWrites"),False),
        "claimBoundary":"Actual inlet-flow/static-outlet steady SST/MRF native facts only; independently sampled inlet suction pressure and numerical acceptance are pending. No old inlet total pressure, solution, H0, NPSHR, refinement or physical validation is inherited."}
    (output/"logs").mkdir();current="runtime-identity";bound_rows={};bound_inputs=[]
    alarm=EvidenceDeadline(deadline)
    try:
        alarm.__enter__();check_deadline(deadline,"prepared current source proof")
        generated_data(preparation,output)
        runtime=runtime_identity(output,deadline)
        immutable={preparation.REPORT:verified["reportFile"],RUNTIME:record(output,RUNTIME,LIMITS["maximumReportBytes"])[1]}
        report["runtimeIdentitySha256"]=immutable[RUNTIME]["sha256"]
        for key in ("authoredInputFiles","sourceComponentFiles","sourceEvidenceFileRecords","authoredPhysicsFileRecords"):
            for row in prepared[key]:
                if key=="authoredInputFiles" and "/0/" in row["path"]:continue  # NCC must rewrite only reviewed typed physical fields.
                require(record(output,row["path"])[1]==row,"Source, snapshot, dictionary or retained authored physics changed before native startup.")
                immutable[row["path"]]=row
        for index,args in enumerate(commands(output)):
            current=args[0]
            if current=="foamRun":
                check_deadline(deadline,"new current source manifest");bound_rows,bound_inputs=bind_manifest(output);check_deadline(deadline,"new current source manifest")
                report.update(inputManifestSha256=bound_rows[MANIFEST]["sha256"],studySha256=bound_rows[STUDY]["sha256"],geometryIdentitySha256=bound_rows[IDENTITY]["sha256"])
            stage,text=execute(args,output,deadline,runtime,index);report["stages"].append(stage)
            require(stage.get("program")==args[0] and stage.get("log")==f"{index:02d}-{args[0]}.log",
                "Bind each current native stage to its one exact program and indexed log, without aliases or overwrite.")
            require(stage["exitCode"]==0,"An actual inlet-flow native utility returned exit "+str(stage["exitCode"]));geometry.native_geometry.completed_log(text)
            log_record=record(output,"logs/"+stage["log"],LIMITS["maximumLogBytes"])[1]
            require(stage.get("logBytes")==log_record["bytes"] and stage.get("logSha256")==log_record["sha256"],"An actual native stage log changed before binding its returned byte inventory.")
            immutable[log_record["path"]]=log_record
            if current=="createNonConformalCouples":preparation.verify_output(output,after_native_ncc=True)
            elif current=="checkMesh":
                geometry.verify_region_count(text,2);expected={row["component"]:row for row in verified["components"]}
                measured=base.verify_merged_mesh(output/"impeller-case",expected,output/"logs"/stage["log"])
                require(measured["mergedCellCount"]==sum(row["cellCount"] for row in expected.values())
                    and measured["mergedPointCount"]==sum(row["nodeCount"] for row in expected.values()),"Actual merged counts must preserve both admitted source components.")
                report.update(mergedNativeMeshEvidence=measured,nativeMeshQuality=scientific.parse_mesh_quality(text),nativeGeometryQualityVerified=True,
                    physicalWallTypesVerified=True,coupledInterfaceCoverageVerified=True,nativeNccDiagnosticRecords=ncc_diagnostic_records(output))
                near=near_points_evidence(output);report.update(nativeCheckMeshDiagnosticRecords=near["files"],nativeNearPointsEvidence=near["evidence"])
                pre_solver_rows,_=generated_data(preparation,output,after_native_execution=True)
                # The checkMesh diagnostic is immutable evidence. polyFaces is
                # generated addressing that native writers may rewrite; only
                # its actual final bytes/proof are bound as generated data.
                for row in pre_solver_rows:
                    if row["path"]=="impeller-case/0/cellToRegion":immutable[row["path"]]=row
            elif current=="foamRun":
                history=scientific.parse_solver_log(text);require(max(history)==500,"Actual steady native output must reach the500 declared outer iterations.")
                report["nativeSolverCompleted"]=True;workspace_size(output);current="native-endpoint-fields";check_deadline(deadline,current)
                primary,fields,scans=final_fields(output,sum(row["cellCount"] for row in verified["components"]));check_deadline(deadline,current)
                current="native-sampling-evidence";check_deadline(deadline,current);samples=sampling_records(output);check_deadline(deadline,current)
                require(all(record(output,row["path"])[1]=={k:row[k] for k in ("path","bytes","sha256")} for row in bound_inputs),
                    "An actual post-NCC bound input changed during the native solve.")
                report.update(finalNativeFields=primary,finalNativeFieldRecords=fields,finiteNativeFieldEvidence=scans,nativeSamplingRecords=samples,
                    finiteNativeFieldsVerified=True,simulationExecuted=True)
        current="final-immutable-evidence";check_deadline(deadline,current)
        require(all(record(output,path,LIMITS["maximumLogBytes"] if path.startswith("logs/") else None)[1]==row for path,row in {**immutable,**bound_rows}.items()),
            "A current immutable source, runtime, new family manifest/identity/study, log, dictionary or generated diagnostic changed.")
        require(all(record(output,row["path"])[1]==row for row in report["finalNativeFieldRecords"])
            and all(record(output,row["path"],LIMITS["maximumLogBytes"])[1]==row for row in report["nativeSamplingRecords"]),"Actual endpoint/sample bytes changed during final verification.")
        require(ncc_diagnostic_records(output)==report["nativeNccDiagnosticRecords"],"The actual initial NCC diagnostic inventory changed.")
        near=near_points_evidence(output);require(near["files"]==report["nativeCheckMeshDiagnosticRecords"] and near["evidence"]==report["nativeNearPointsEvidence"],"The actual nearPoints diagnostic changed.")
        data_rows,data_proof=generated_data(preparation,output,after_native_execution=True)
        report.update(nativeGeneratedDataRecords=data_rows,nativeGeneratedDataEvidence=data_proof)
        current="final-copied-source-proof";check_deadline(deadline,current);preparation.verify_output(output,after_native_ncc=True);check_deadline(deadline,current)
        current="final-original-source-proof";check_deadline(deadline,current);original=preparation.validate_source(source);check_deadline(deadline,current)
        require(original["sourceFileRecords"]==[{**row,"path":row["path"].removeprefix("source-teaching/")} for row in prepared["sourceEvidenceFileRecords"]],
            "The original geometry source changed after copying its complete inventory.")
        require(generated_data(preparation,output,after_native_execution=True)==(data_rows,data_proof),"Generated native data bytes/proofs changed during final current-source replay.")
        for row in runtime["binaries"]:
            _,actual=safety.descriptor(Path(row["path"]));require((actual["bytes"],actual["sha256"])==(row["bytes"],row["sha256"]),"A native executable changed during the campaign.")
        workspace_size(output);check_deadline(deadline,"all final source/native bytes")
        report.update(inputArtifactsImmutable=True,sourceGeometryImmutable=True,actualCfdRunVerified=True,status=SUCCESS)
    except Exception as error:
        report.update(status="TEACHING_INLET_FLOW_NATIVE_CORRECTION_REQUIRED",actualCfdRunVerified=False,
            diagnostic={"program":current,"type":type(error).__name__,"message":str(error)[:500]})
    finally:alarm.__exit__(None,None,None)
    try:report["retainedNativeLogRecords"]=retain_logs(output);report["workspaceBytes"]=workspace_size(output)
    except Exception as error:
        report.update(status="TEACHING_INLET_FLOW_RETENTION_CORRECTION_REQUIRED",actualCfdRunVerified=False,workspaceBytes=None,
            diagnostic={"program":"retention","type":type(error).__name__,"message":str(error)[:500]})
    report["elapsedSeconds"]=round(time.monotonic()-started,3)
    if report["elapsedSeconds"]>=LIMITS["maximumSeconds"]:
        report.update(actualCfdRunVerified=False,status="TEACHING_INLET_FLOW_NATIVE_CORRECTION_REQUIRED",
            diagnostic={"program":"final-deadline","message":"The complete1200s inlet-flow evidence budget expired."})
    base.write_exclusive(output/REPORT,base.inventory.bounded_json(report))
    summary={k:report[k] for k in ("status","simulationExecuted","actualCfdRunVerified","numericalBaselineQualified","qualifiedBaseline","mainApplicationWrites")}
    if "diagnostic" in report:summary["diagnostic"]=report["diagnostic"]
    print(json.dumps(summary,allow_nan=False));return 0 if report["actualCfdRunVerified"] else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--geometry-native-directory",type=Path,required=True);parser.add_argument("--output-directory",type=Path,required=True)
    args=parser.parse_args()
    try:return run(args.geometry_native_directory,args.output_directory)
    except Exception as error:
        print(json.dumps({"status":"TEACHING_INLET_FLOW_INPUT_CORRECTION_REQUIRED","action":str(error)[:500],"actualCfdRunVerified":False,"mainApplicationWrites":False}));return 2


if __name__=="__main__":raise SystemExit(main())
