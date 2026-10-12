"""Run one bounded authored pressure point; numerical/NPSH3 acceptance is separate."""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
import teaching_pump_pressure_case as preparation
import run_teaching_pump_phase as phase

base,safety,native=phase.base,phase.safety,phase.native
SCHEMA="untirta.teaching-pump-pressure-point-execution.v1"
RUNTIME_SCHEMA=preparation.RUNTIME_SCHEMA
REPORT="teaching-pressure-execution.json"
RUNTIME=preparation.RUNTIME
MANIFEST="teaching-pressure-input-manifest.json"
STUDY="teaching-pressure-study.json"
STATUS="TEACHING_PRESSURE_POINT_EXECUTED_ACCEPTANCE_REQUIRED"
PROGRAMS=phase.PROGRAMS
LIMITS=dict(phase.LIMITS)
FALSE_FLAGS=phase.FALSE_FLAGS+("pressurePointObservationQualified","numericalPressurePointQualified","pressureBracketVerified")
record,workspace_size,final_fields,absolute_pressure_reconstruction=phase.record,phase.workspace_size,phase.final_fields,phase.absolute_pressure_reconstruction


def require(value,action):preparation.require(value,action)


def commands(output):return preparation.commands(output)


def runtime_identity(output,deadline):
    def query(args):
        remaining=deadline-time.monotonic();require(remaining>0,"The pressure-point deadline expired during package binding.")
        result=subprocess.run(args,env=native.child_environment(),capture_output=True,text=True,timeout=min(10,remaining),check=True)
        require(len(result.stdout)<=8192 and len(result.stderr)<=8192,"Bound native package-query output.");return result.stdout
    require(query(["dpkg-query","--show","--showformat=${Version}","openfoam12"])=="20250206","Use the exact checksum-pinned official Foundation12 package.")
    rows=[]
    for name in PROGRAMS:
        executable=shutil.which(name);require(executable,"An official pressure-point utility is absent: "+name)
        path=Path(executable).resolve();require(path.is_relative_to(Path("/opt/openfoam12")) and path.name==name,"Use only exact utilities from the official package.")
        owner=query(["dpkg-query","--search",str(path)]).strip();require(owner=="openfoam12: "+str(path),"Each actual utility must retain pinned-package ownership.")
        _,row=safety.descriptor(path);rows.append({**row,"program":name,"path":str(path),"packageOwnership":owner})
    require(time.monotonic()<deadline,"The pressure-point deadline expired after binary binding.")
    value={"schemaVersion":RUNTIME_SCHEMA,"openFoamFoundationVersion":12,"packageVersion":"20250206","officialDebSha256":safety.PACKAGE_SHA,
        "expectedSourceCommit":safety.SOURCE_COMMIT,"binaries":rows,"mainApplicationWrites":False}
    # Family validation independently re-reads actual package-owned binary bytes.
    preparation.runtime_family(value);base.write_exclusive(Path(output)/RUNTIME,base.inventory.bounded_json(value));return value


def execute(args,output,deadline,runtime,index):
    require(type(index) is int and 0<=index<2 and args==commands(output)[index],"Execute only an exact current pressure-point command.")
    # The reviewed low-level two-command executor has the identical argv shape,
    # CPU/address-space/log/workspace bounds and owned-process stop discipline.
    return phase.execute(args,output,deadline,runtime,index)


def bind_manifest(output):
    import reduce_teaching_pump_pressure as reduction
    bound=reduction.build_manifest(output)
    for name,key in ((MANIFEST,"manifest"),(STUDY,"study")):
        raw=reduction.encoded(bound[key]);require(0<len(raw)<=LIMITS["maximumReportBytes"],"Bind bounded canonical pressure-point input packets before native work.")
        base.write_exclusive(Path(output)/name,raw)
    rows=bound["manifest"]["files"]
    require(isinstance(rows,list) and 0<len(rows)<=LIMITS["maximumSourceRecords"]+64 and len({r["path"] for r in rows})==len(rows),"Every actual pressure-point input needs a unique bounded byte identity.")
    require(all(record(output,r["path"],preparation.MAX_ACCEPTANCE if r["path"].endswith("-acceptance.json") else None)[1]=={k:r[k] for k in ("path","bytes","sha256")} for r in rows),"Current pressure-point input bytes disagree with the pre-execution manifest.")
    return {name:record(output,name,LIMITS["maximumReportBytes"])[1] for name in (MANIFEST,STUDY)},rows


def near_points_evidence(output):
    return phase.baseline.near_points_evidence(output,case_relative="case",log_relative="logs/00-checkMesh.log",
        authored_report_relative="source-reference/source-baseline/source-teaching/source-geometry/teaching-geometry.json")


def postprocessing_records(output):
    folder=Path(output)/"case/postProcessing"
    if not folder.exists():return [],[]
    base.regular_directory(folder);samples=[];diagnostics=[]
    names=set(re.findall(r"(?m)^([A-Za-z][A-Za-z0-9]*) \{",record(output,"case/system/controlDict")[0].decode("ascii")))
    for path in sorted(folder.rglob("*")):
        require(not path.is_symlink() and not getattr(path,"is_junction",lambda:False)() and (path.is_file() or path.is_dir()),"Pressure sample/diagnostic paths must be ordinary owned files/directories.")
        if not path.is_file():continue
        relative=path.relative_to(output).as_posix()
        match=re.fullmatch(r"case/postProcessing/([A-Za-z][A-Za-z0-9]*)/"+phase.fields.TIME_NAME+r"/(?:surfaceFieldValue|volFieldValue|yPlus)\.dat",relative)
        if match:
            require(match[1] in names,"Every actual table must belong to its authored pressure-point function object.");samples.append(record(output,relative,4*1024**2)[1])
        elif relative=="case/postProcessing/checkMesh/constant/nearPoints.vtk":near_points_evidence(output)
        else:
            require(re.fullmatch(r"case/postProcessing/checkMesh/0/(?:interface-impeller-outlet|interface-volute-inlet)_coverage\.vtk",relative),"Only authored tables and exact NCC/nearPoints diagnostics may be retained.")
            diagnostics.append(record(output,relative)[1])
    require(len(samples)<=64 and len(diagnostics) in (0,2),"Bound actual pressure table inventory and retain both available native NCC sides.");return samples,diagnostics


def sampling_records(output):
    rows,_=postprocessing_records(output);require(rows,"Actual native pressure-point samples are required.");return rows


def ncc_diagnostic_records(output):return postprocessing_records(output)[1]


def run(reference_directory,output_directory,*,pressure_absolute_pa):
    started=time.monotonic();deadline=started+LIMITS["maximumSeconds"];safety.context_guard()
    source=safety.scope_path(reference_directory);output=safety.scope_path(output_directory,fresh=True)
    prepared=preparation.prepare(source,output,pressure_absolute_pa=pressure_absolute_pa);verified=preparation.verify_output(output);output=base.regular_directory(output)
    require(prepared==verified["report"] and prepared["schemaVersion"]==preparation.SCHEMA and prepared["nativeCommandArguments"]==commands(output),"Execute only the distinct source-bound pressure-only preparation.")
    report={"schemaVersion":SCHEMA,"sourceGeometryRoute":preparation.ROUTE,"githubRepository":"simcalc/PyAnsys","githubRunId":os.environ.get("GITHUB_RUN_ID"),
        "githubSourceCommit":os.environ.get("GITHUB_SHA"),"status":"TEACHING_PRESSURE_POINT_EXECUTION_REQUIRED","mode":preparation.MODE,"physicalModel":"steady-cavitation-MRF",
        "limits":dict(LIMITS),"stages":[],"preparationReportSha256":verified["reportFile"]["sha256"],
        **{key:prepared[key] for key in ("sourceActivePhaseEvidenceSha256","sourceReferenceInputManifestSha256","sourceActivePhaseHeadM","sourceReferencePressureAbsolutePa","authoredCavitationModelSha256","temporalPlan","pressureAbsolutePa","inletAbsoluteTotalPressurePa","absolutePressureMapping")},
        "sourceActivePhaseH0NumericallyQualified":True,"nativeCheckMeshVerified":False,"nativeGeometryQualityVerified":False,"physicalWallTypesVerified":False,
        "coupledInterfaceCoverageVerified":False,"nativeSolverCompleted":False,"cavitationModelExecuted":False,"finiteNativeEndFieldsVerified":False,
        "inputArtifactsImmutable":False,"sourceReferenceImmutable":False,"stationaryMrfRelaxationExecuted":False,"simulationExecuted":False,"actualCfdRunVerified":False,
        **{k:False for k in FALSE_FLAGS},"claimBoundary":"Actual native execution of one declared pressure point only. Cavitating numerical observation and actual3% bracket/NPSH3 reduction are separate; no single-phase H0 or physical moving/restart/refinement claim."}
    (output/"logs").mkdir();current="runtime-identity"
    try:
        runtime=runtime_identity(output,deadline)
        immutable={preparation.REPORT:verified["reportFile"],RUNTIME:record(output,RUNTIME,LIMITS["maximumReportBytes"])[1],phase.IDENTITY:record(output,phase.IDENTITY,LIMITS["maximumReportBytes"])[1]}
        for row in prepared["sourceEvidenceFileRecords"]:immutable[row["path"]]=row
        for key in ("authoredInputFiles","preservedNativeMeshFiles"):
            for row in prepared[key]:immutable["case/"+row["path"]]={**row,"path":"case/"+row["path"]}
        require(all(record(output,path,preparation.MAX_ACCEPTANCE if path.endswith("-acceptance.json") else None)[1]==row for path,row in immutable.items()),"Current source reference, mesh or pressure recipe changed before native execution.")
        bound,input_rows=bind_manifest(output);immutable.update(bound)
        report.update(runtimeIdentitySha256=immutable[RUNTIME]["sha256"],inputManifestSha256=bound[MANIFEST]["sha256"],studySha256=bound[STUDY]["sha256"])
        for index,args in enumerate(commands(output)):
            current=args[0];stage,text=execute(args,output,deadline,runtime,index);report["stages"].append(stage)
            require(stage["exitCode"]==0,"A native pressure-point utility returned exit "+str(stage["exitCode"]));phase.baseline.geometry.native_geometry.completed_log(text)
            immutable["logs/"+stage["log"]]=record(output,"logs/"+stage["log"],LIMITS["maximumLogBytes"])[1]
            if current=="checkMesh":
                phase.baseline.geometry.verify_region_count(text,2)
                measured=base.verify_merged_mesh(output/"case",verified["components"],output/"logs"/stage["log"])
                require(measured["mergedCellCount"]==sum(r["cellCount"] for r in verified["components"]) and measured["mergedPointCount"]==sum(r["nodeCount"] for r in verified["components"]),"The pressure point must retain exact source component cell/point counts.")
                near=near_points_evidence(output)
                report.update(mergedNativeMeshEvidence=measured,nativeMeshQuality=phase.baseline.scientific.parse_mesh_quality(text),nativeCheckMeshVerified=True,
                    nativeGeometryQualityVerified=True,physicalWallTypesVerified=True,coupledInterfaceCoverageVerified=True,nativeNccDiagnosticRecords=ncc_diagnostic_records(output),
                    nativeCheckMeshDiagnosticRecords=near["files"],nativeNearPointsEvidence=near["evidence"])
            else:
                report["nativeSolverCompleted"]=True
                require(re.search(r"(?m)^Selecting cavitation model SchnerrSauer\s*$",text),"Actual SchnerrSauer selection must appear in the native pressure-point log.")
                report["cavitationModelExecuted"]=True
                rows,times=final_fields(output,prepared["temporalPlan"],report["mergedNativeMeshEvidence"]["mergedCellCount"],text)
                pressures={Path(r["path"]).name:r for r in rows if Path(r["path"]).name in {"p","p_rgh"}}
                require(set(pressures)=={"p","p_rgh"},"Both actual absolute pressure endpoint fields are required.")
                pair=absolute_pressure_reconstruction(record(output,pressures["p"]["path"])[0],record(output,pressures["p_rgh"]["path"])[0],report["mergedNativeMeshEvidence"]["mergedCellCount"])
                report.update(finalNativeFieldRecords=rows,nativeEndTimeS=times[-1][0],endpointAbsolutePressureReconstruction=pair,
                    finiteNativeEndFieldsVerified=True,nativeSamplingRecords=sampling_records(output),simulationExecuted=True,stationaryMrfRelaxationExecuted=True)
        require(all(record(output,path,LIMITS["maximumLogBytes"] if path.startswith("logs/") else preparation.MAX_ACCEPTANCE if path.endswith("-acceptance.json") else None)[1]==row for path,row in immutable.items())
            and all(record(output,r["path"])[1]=={k:r[k] for k in ("path","bytes","sha256")} for r in input_rows),"Source reference, current pressure/clock/model/mesh inputs or packets changed during native execution.")
        require(all(record(output,r["path"])[1]=={k:r[k] for k in ("path","bytes","sha256")} for r in report["finalNativeFieldRecords"])
            and all(record(output,r["path"],4*1024**2)[1]==r for r in report["nativeSamplingRecords"])
            and ncc_diagnostic_records(output)==report["nativeNccDiagnosticRecords"],"Actual endpoint/table/NCC bytes changed during final verification.")
        near=near_points_evidence(output);require(near["files"]==report["nativeCheckMeshDiagnosticRecords"] and near["evidence"]==report["nativeNearPointsEvidence"],"The actual nearPoints diagnostic changed after native mesh verification.")
        final=preparation.verify_output(output,after_native_execution=True)
        report.update(nativeGeneratedDataRecords=final["nativeGeneratedDataRecords"],nativeGeneratedDataEvidence=final["nativeGeneratedDataEvidence"])
        require(all(record(output,r["path"])[1]==r for r in report["nativeGeneratedDataRecords"]),"Native generated data changed at the retention boundary.")
        original=preparation.verify_reference(source)
        require(original["sourceRecords"]==[{**r,"path":r["path"].removeprefix("source-reference/")} for r in prepared["sourceEvidenceFileRecords"]],"The original actual active H0 reference changed after immutable source copying.")
        workspace_size(output);require(time.monotonic()<deadline,"The pressure-point deadline expired during final current-byte verification.")
        report.update(inputArtifactsImmutable=True,sourceReferenceImmutable=True,actualCfdRunVerified=True,status=STATUS)
    except Exception as error:
        report.update(status="TEACHING_PRESSURE_POINT_CORRECTION_REQUIRED",actualCfdRunVerified=False,diagnostic={"program":current,"type":type(error).__name__,"message":str(error)[:500]})
    try:
        report["retainedNativeLogRecords"]=[record(output,f"logs/{i:02d}-{name}.log",LIMITS["maximumLogBytes"],empty=True)[1] for i,name in enumerate(PROGRAMS) if (output/f"logs/{i:02d}-{name}.log").exists()]
        report["workspaceBytes"]=workspace_size(output)
    except Exception as error:report.update(actualCfdRunVerified=False,status="TEACHING_PRESSURE_RETENTION_CORRECTION_REQUIRED",retentionDiagnostic={"type":type(error).__name__,"message":str(error)[:500]},workspaceBytes=None)
    report["elapsedSeconds"]=round(time.monotonic()-started,3)
    if report["elapsedSeconds"]>=LIMITS["maximumSeconds"]:report.update(actualCfdRunVerified=False,status="TEACHING_PRESSURE_POINT_CORRECTION_REQUIRED",diagnostic={"program":"final-deadline","message":"The complete pressure-point evidence deadline expired."})
    base.write_exclusive(output/REPORT,base.inventory.bounded_json(report));return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--reference-directory",required=True);parser.add_argument("--output-directory",required=True);parser.add_argument("--pressure-absolute-pa",type=float,required=True)
    args=parser.parse_args()
    try:
        report=run(args.reference_directory,args.output_directory,pressure_absolute_pa=args.pressure_absolute_pa)
        summary={k:report[k] for k in ("status","pressureAbsolutePa","actualCfdRunVerified","simulationExecuted","numericalPressurePointQualified","phaseH0Eligible","npsh3Qualified","physicalTransientExecuted","mainApplicationWrites")}
        if "diagnostic" in report:summary["diagnostic"]=report["diagnostic"]
        print(json.dumps(summary));return 0 if report["actualCfdRunVerified"] else 2
    except Exception as error:print(json.dumps({"status":"TEACHING_PRESSURE_INPUT_CORRECTION_REQUIRED","action":str(error)[:500],"actualCfdRunVerified":False,"npsh3Qualified":False,"mainApplicationWrites":False}));return 2


if __name__=="__main__":raise SystemExit(main())
