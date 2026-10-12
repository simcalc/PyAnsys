"""Execute a bounded fresh active-phase reference on authored teaching CAD.

Only checkMesh and foamRun run. The recorded native facts do not qualify an
active-phase H0, pressure sweep, NPSHr, moving rotor, restart or convergence.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
from itertools import repeat
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_teaching_pump_baseline as baseline
import run_frozen_rotor_phase as fields

base,safety,native=baseline.base,baseline.safety,baseline.native
SCHEMA="untirta.teaching-pump-phase-reference-execution.v1"
PREPARATION_SCHEMA="untirta.teaching-pump-phase-reference-preparation.v1"
RUNTIME_SCHEMA="untirta.teaching-pump-phase-runtime-identity.v1"
REPORT="teaching-phase-execution.json"
RUNTIME="teaching-phase-runtime-identity.json"
MANIFEST="teaching-phase-input-manifest.json"
STUDY="teaching-phase-study.json"
IDENTITY="teaching-geometry-identity.json"
STATUS="TEACHING_ACTIVE_PHASE_REFERENCE_EXECUTED_ACCEPTANCE_REQUIRED"
MODE="high-pressure-active-phase-MRF-reference"
PROGRAMS=("checkMesh","foamRun")
END_FIELDS=fields.END_FIELDS
FIELD_DIMENSIONS=fields.FIELD_DIMENSIONS
LIMITS={"maximumSeconds":1200,"maximumCpus":1,"maximumMemoryBytes":4*1024**3,"maximumWorkspaceBytes":1024**3,
    "maximumFileBytes":128*1024**2,"maximumLogBytes":8*1024**2,"maximumReportBytes":128*1024,
    "maximumSourceRecords":320,"maximumPaths":2048,"maximumPathDepth":10}
FALSE_FLAGS=("qualifiedBaseline","numericalSolverAcceptanceVerified","phaseObservationQualified","numericalPhaseReferenceQualified",
    "phaseFamilyReferenceEligible","phaseFamilyReferenceQualified","phaseH0Eligible","cavitationSweepExecuted","pressureSweepEnabled",
    "slidingEnabled","physicalTransientExecuted","npshrCalculated","npsh3Qualified","npsh3Verified","restartVerified",
    "meshConvergenceVerified","timeStepConvergenceVerified","wallResolutionVerified","physicalValidationVerified",
    "manufacturerPublishedGeometry","originalFluentGeometryReproduced","baselineFieldsCopied","baselineHeadUsedAsPhaseH0","mainApplicationWrites")


def require(value,action):baseline.require(value,action)


def commands(output):
    case=str(Path(output).absolute()/"case")
    return [["checkMesh","-case",case,"-allGeometry","-allTopology","-writeSets"],["foamRun","-case",case]]


def record(root,relative,limit=None,empty=False):
    path=Path(relative)
    require(not path.is_absolute() and ".." not in path.parts and all(p and not p.startswith(".") for p in path.parts),"Use only ordinary current case-relative phase evidence.")
    return safety.descriptor(Path(root)/path,path.as_posix(),limit or LIMITS["maximumFileBytes"],empty=empty)


def workspace_size(directory):
    root=base.regular_directory(directory);total=0;count=0
    for path in root.rglob("*"):
        count+=1
        require(count<=LIMITS["maximumPaths"] and len(path.relative_to(root).parts)<=LIMITS["maximumPathDepth"],"The teaching phase path/depth bound was reached.")
        require(not path.is_symlink() and not getattr(path,"is_junction",lambda:False)() and (path.is_file() or path.is_dir()),"Phase evidence paths must be ordinary owned files/directories.")
        if path.is_file():
            size=path.stat().st_size;total+=size
            require(size<=LIMITS["maximumFileBytes"] and total<=LIMITS["maximumWorkspaceBytes"],"The teaching phase file/workspace bound was reached.")
    return total


def child_limits():
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(LIMITS["maximumMemoryBytes"],LIMITS["maximumMemoryBytes"]))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_FSIZE,(LIMITS["maximumFileBytes"],LIMITS["maximumFileBytes"]))
    allowed=os.sched_getaffinity(0);require(allowed,"A fresh native phase child needs one allowed CPU.");os.sched_setaffinity(0,{min(allowed)})


def runtime_identity(output,deadline):
    def query(args):
        remaining=deadline-time.monotonic();require(remaining>0,"The active-phase deadline expired during package verification.")
        result=subprocess.run(args,env=native.child_environment(),capture_output=True,text=True,timeout=min(10,remaining),check=True)
        require(len(result.stdout)<=8192 and len(result.stderr)<=8192,"Bound each actual native package query output.");return result.stdout
    require(query(["dpkg-query","--show","--showformat=${Version}","openfoam12"])=="20250206","Use the checksum-pinned official Foundation12 package20250206.")
    binaries=[]
    for program in PROGRAMS:
        executable=shutil.which(program);require(executable,"An official phase utility is absent: "+program)
        path=Path(executable).resolve();require(path.is_relative_to(Path("/opt/openfoam12")) and path.name==program,"Use only the exact native utility inside the official package.")
        owner=query(["dpkg-query","--search",str(path)]).strip();require(owner=="openfoam12: "+str(path),"Each actual phase executable must belong to the pinned package.")
        _,row=safety.descriptor(path);binaries.append({**row,"program":program,"path":str(path),"packageOwnership":owner})
    require(time.monotonic()<deadline,"The active-phase deadline expired after native binary binding.")
    value={"schemaVersion":RUNTIME_SCHEMA,"openFoamFoundationVersion":12,"packageVersion":"20250206",
        "officialDebSha256":safety.PACKAGE_SHA,"expectedSourceCommit":safety.SOURCE_COMMIT,"binaries":binaries,"mainApplicationWrites":False}
    base.write_exclusive(Path(output)/RUNTIME,base.inventory.bounded_json(value));return value


def execute(arguments,output,deadline,runtime,index):
    require(type(index) is int and 0<=index<2 and arguments==commands(output)[index],"Execute only the exact indexed current phase command.")
    output=base.regular_directory(output);matches=[row for row in runtime["binaries"] if row["program"]==arguments[0]]
    require(len(matches)==1,"Bind one unambiguous current native phase executable.")
    row=matches[0];binary=Path(row["path"])
    require(binary.resolve().is_relative_to(Path("/opt/openfoam12")) and binary.name==arguments[0]
        and row.get("packageOwnership")=="openfoam12: "+str(binary),"Execute only the actual package-owned phase binary.")
    _,current=safety.descriptor(binary);require((current["bytes"],current["sha256"])==(row["bytes"],row["sha256"]),"The actual native phase binary changed after binding.")
    relative=f"logs/{index:02d}-{arguments[0]}.log";path=output/relative
    require(not path.exists() and not path.is_symlink() and time.monotonic()<deadline,"Native phase logs must be fresh before the serial deadline.")
    workspace_size(output);process=None;total=0;started=time.monotonic();next_progress=started+30;selector=selectors.DefaultSelector()
    try:
        with path.open("xb") as destination:
            process=subprocess.Popen([str(binary),*arguments[1:]],cwd=arguments[2],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                env=native.child_environment(),preexec_fn=child_limits,start_new_session=True,bufsize=0)
            selector.register(process.stdout,selectors.EVENT_READ)
            while selector.get_map():
                now=time.monotonic();require(now<deadline,"The1200s serial active-phase deadline expired.");workspace_size(output)
                if now>=next_progress:
                    print(json.dumps({"stageIndex":index,"program":arguments[0],"status":"TeachingNativePhaseRunning","elapsedSeconds":round(now-started,1),
                        "actualCfdRunVerified":False,"phaseH0Eligible":False,"mainApplicationWrites":False}),flush=True);next_progress=now+30
                for key,_ in selector.select(timeout=.25):
                    chunk=os.read(key.fileobj.fileno(),4096)
                    if not chunk:selector.unregister(key.fileobj);continue
                    remaining=LIMITS["maximumLogBytes"]-total;destination.write(chunk[:remaining]);destination.flush();total+=min(len(chunk),remaining)
                    require(len(chunk)<=remaining,"The actual native phase log exceeded8MiB.")
            code=process.wait(timeout=3);workspace_size(output);require(time.monotonic()<deadline,"The active-phase deadline expired at native completion.")
    finally:
        if process is not None:native.stop_owned(process);process.stdout.close()
        selector.close()
    raw,row=record(output,relative,LIMITS["maximumLogBytes"])
    return {"program":arguments[0],"exitCode":code,"elapsedSeconds":round(time.monotonic()-started,3),"log":path.name,"logBytes":row["bytes"],"logSha256":row["sha256"]},raw.decode("utf-8",errors="strict")


def bind_manifest(output):
    import reduce_teaching_pump_phase as reduction
    bound=reduction.build_manifest(output)
    for name,key in ((MANIFEST,"manifest"),(STUDY,"study")):
        raw=reduction.encoded(bound[key]);require(0<len(raw)<=LIMITS["maximumReportBytes"],"Bound each canonical teaching phase input packet before native execution.")
        base.write_exclusive(Path(output)/name,raw)
    files=bound["manifest"]["files"]
    require(isinstance(files,list) and 0<len(files)<=LIMITS["maximumSourceRecords"]+64 and len({row["path"] for row in files})==len(files),"Bind a unique bounded current phase scientific input inventory.")
    require(all(record(output,row["path"])[1]=={k:row[k] for k in ("path","bytes","sha256")} for row in files),"Every current phase input must match its manifest bytes before execution.")
    return {name:record(output,name,LIMITS["maximumReportBytes"])[1] for name in (MANIFEST,STUDY)},files


def finite_field(raw,name,cell_count):
    """Parse native dimensionless alpha without changing retained field bytes.

    The pinned Foundation12 dimensionSetIO.C129--155 writes [] for a
    dimensionless field. Only alpha.water may use that equivalent encoding;
    the reviewed legacy parser still checks every finite value, count, shape,
    alpha bound and boundary numeric literal on the semantic parse copy.
    """
    if name=="alpha.water":
        text=re.sub(r"/\*.*?\*/|//[^\n]*","",raw.decode("ascii"),flags=re.S)
        dimensions=re.findall(r"\bdimensions\s*\[([^\[\]]*)\]\s*;",text)
        require(len(dimensions)==1,"Actual teaching alpha needs one exact native dimension declaration.")
        if not dimensions[0].strip():
            semantic=re.sub(r"\bdimensions\s*\[\s*\]\s*;","dimensions [0 0 0 0 0 0 0];",text,count=1)
            return fields.finite_field(semantic.encode("ascii"),name,cell_count)
    return fields.finite_field(raw,name,cell_count)


def final_fields(directory,plan,cell_count,log_text):
    """Bind seven actual endpoint byte records and teaching semantic scans."""
    root=base.regular_directory(directory);case=base.regular_directory(root/"case");times=[]
    for path in case.iterdir():
        if re.fullmatch(fields.TIME_NAME,path.name) and float(path.name)>0:
            base.regular_directory(path);times.append((float(path.name),path))
            require(len(times)<=64,"The actual teaching time-directory inventory must remain bounded.")
    require(times,"A completed teaching phase must write actual positive-time fields.")
    latest,endpoint=max(times,key=lambda pair:pair[0]);expected=plan["endTimeS"]
    logged=[float(m[1]) for m in re.finditer(r"(?m)^Time = ("+baseline.scientific.FLOAT+r")s?\s*$",log_text)]
    require(math.isclose(latest,expected,rel_tol=5e-11,abs_tol=1e-12) and logged and math.isclose(logged[-1],expected,rel_tol=5e-11,abs_tol=1e-12),
        "Actual teaching endpoint fields and solver clock must reach the authored physical end time.")
    rows=[]
    for name in END_FIELDS:
        raw,row=record(root,"case/"+endpoint.name+"/"+name)
        rows.append({**row,"fieldCheck":finite_field(raw,name,cell_count)})
    return rows,sorted(times,key=lambda pair:pair[0])


def absolute_pressure_reconstruction(p_raw,prgh_raw,cells):
    """All-cell physical p=p_rgh proof for authored g0, with no added reference."""
    def values(raw,name):
        finite_field(raw,name,cells)
        text=re.sub(r"/\*.*?\*/|//[^\n]*","",raw.decode("ascii"),flags=re.S)
        match=re.search(r"\binternalField\s+(?:uniform\s+([^;]+)|nonuniform\s+List<scalar>\s+\d+\s*\((.*?)\))\s*;",text,re.S)
        require(match,"Explicit complete native scalar pressures are required.")
        return repeat(float(match[1]),cells) if match[1] is not None else map(float,match[2].split())
    maximum=0.;count=0
    for p,prgh in zip(values(p_raw,"p"),values(prgh_raw,"p_rgh"),strict=True):
        count+=1;maximum=max(maximum,abs(p-prgh));require(math.isclose(p,prgh,rel_tol=1e-12,abs_tol=1e-8),"Actual p and p_rgh disagree with the authored absolute-Pa g0 reconstruction.")
    require(count==cells,"Every actual internal cell needs the native pressure reconstruction check.")
    return {"equation":"p_absolute=p_rgh+rho*gh; authored g=0 gives p_absolute=p_rgh","allInternalCellsChecked":count,
        "maximumAbsoluteDifferencePa":maximum,"relativeRoundoffTolerance":1e-12,"absoluteRoundoffTolerancePa":1e-8,
        "atmosphericPressureAddedPa":0,"verified":True,
        "sourceUrl":"https://github.com/OpenFOAM/OpenFOAM-12/blob/"+safety.SOURCE_COMMIT+"/applications/modules/twoPhaseSolver/pressureCorrector.C"}


def postprocessing_records(output):
    folder=Path(output)/"case/postProcessing"
    if not folder.exists():return [],[]
    base.regular_directory(folder);samples=[];diagnostics=[]
    control=record(output,"case/system/controlDict")[0].decode("ascii")
    names=set(re.findall(r"(?m)^([A-Za-z][A-Za-z0-9]*) \{",control))
    for path in sorted(folder.rglob("*")):
        require(not path.is_symlink() and not getattr(path,"is_junction",lambda:False)() and (path.is_file() or path.is_dir()),"Native phase sampler paths must be ordinary owned files/directories.")
        if not path.is_file():continue
        relative=path.relative_to(output).as_posix()
        match=re.fullmatch(r"case/postProcessing/([A-Za-z][A-Za-z0-9]*)/"+fields.TIME_NAME+r"/(?:surfaceFieldValue|volFieldValue|yPlus)\.dat",relative)
        if match:
            require(match[1] in names,"Every native table must belong to an actually authored function object.")
            samples.append(record(output,relative,4*1024**2)[1])
        elif relative=="case/postProcessing/checkMesh/constant/nearPoints.vtk":
            near_points_evidence(output)
        else:
            require(re.fullmatch(r"case/postProcessing/checkMesh/0/(?:interface-impeller-outlet|interface-volute-inlet)_coverage\.vtk",relative),"Only exact authored native tables and initial NCC coverage VTK diagnostics may be retained.")
            diagnostics.append(record(output,relative)[1])
    require(len(samples)<=64 and len(diagnostics) in (0,2),"Bound phase sampling and retain both available native NCC coverage diagnostic roles.")
    return samples,diagnostics


def sampling_records(output):
    samples,_=postprocessing_records(output);require(samples,"A complete actual native phase sampling inventory is required.");return samples


def ncc_diagnostic_records(output):return postprocessing_records(output)[1]


def near_points_evidence(output):
    return baseline.near_points_evidence(output,case_relative="case",log_relative="logs/00-checkMesh.log",
        authored_report_relative="source-baseline/source-teaching/source-geometry/teaching-geometry.json")


def run(baseline_directory,output_directory):
    started=time.monotonic();deadline=started+LIMITS["maximumSeconds"];safety.context_guard()
    source=safety.scope_path(baseline_directory);output=safety.scope_path(output_directory,fresh=True)
    import teaching_pump_phase_case as preparation
    prepared=preparation.prepare(source,output);verified=preparation.verify_output(output);output=base.regular_directory(output)
    require(prepared==verified["report"] and prepared["schemaVersion"]==PREPARATION_SCHEMA and prepared["mode"]==MODE
        and prepared["inletAbsoluteTotalPressurePa"]==300000 and prepared["nativeCommandArguments"]==commands(output),"Execute only the fresh fixed high-pressure teaching reference preparation.")
    report={"schemaVersion":SCHEMA,"sourceGeometryRoute":"CLASSROOM_AUTHORED_GEOMETRY","githubRepository":"simcalc/PyAnsys",
        "githubRunId":os.environ.get("GITHUB_RUN_ID"),"githubSourceCommit":os.environ.get("GITHUB_SHA"),"mode":MODE,"physicalModel":"steady-cavitation-MRF",
        "status":"TEACHING_ACTIVE_PHASE_REFERENCE_EXECUTION_REQUIRED","limits":dict(LIMITS),"stages":[],"preparationReportSha256":verified["reportFile"]["sha256"],
        **{key:prepared[key] for key in ("sourceBaselineEvidenceSha256","sourceCaseManifestSha256","sourceGeometryControlSha256","authoredCavitationModelSha256","temporalPlan","absolutePressureMapping")},
        "inletAbsoluteTotalPressurePa":300000.,"coordinateUnits":"SI_METRES","sourceNumericalBaselineQualified":True,
        "nativeCheckMeshVerified":False,"nativeGeometryQualityVerified":False,"physicalWallTypesVerified":False,"coupledInterfaceCoverageVerified":False,
        "nativeSolverCompleted":False,"cavitationModelExecuted":False,"finiteNativeEndFieldsVerified":False,"inputArtifactsImmutable":False,"sourceBaselineImmutable":False,
        "stationaryMrfRelaxationExecuted":False,"simulationExecuted":False,"actualCfdRunVerified":False,**{key:False for key in FALSE_FLAGS},
        "claimBoundary":"Fresh actual active SchnerrSauer fixed-MRF execution on the source-bound authored SI classroom pump only. Numerical phase H0, pressure sweep, NPSHr, physical sliding, restart and refinement qualification remain independent and pending."}
    (output/"logs").mkdir();current="runtime-identity"
    try:
        runtime=runtime_identity(output,deadline)
        immutable={"teaching-phase-preparation.json":verified["reportFile"],RUNTIME:record(output,RUNTIME,LIMITS["maximumReportBytes"])[1],IDENTITY:record(output,IDENTITY,LIMITS["maximumReportBytes"])[1]}
        for row in prepared["sourceEvidenceFileRecords"]:immutable[row["path"]]=row
        for key in ("authoredInputFiles","preservedNativeMeshFiles"):
            for row in prepared[key]:immutable["case/"+row["path"]]={**row,"path":"case/"+row["path"]}
        require(all(record(output,path)[1]==row for path,row in immutable.items()),"Actual source, geometry, mesh or phase model bytes changed before native startup.")
        bound,input_rows=bind_manifest(output);immutable.update(bound)
        report.update(runtimeIdentitySha256=immutable[RUNTIME]["sha256"],inputManifestSha256=bound[MANIFEST]["sha256"],studySha256=bound[STUDY]["sha256"])
        for index,args in enumerate(commands(output)):
            current=args[0];stage,text=execute(args,output,deadline,runtime,index);report["stages"].append(stage)
            require(stage["exitCode"]==0,"A native active-phase utility returned exit "+str(stage["exitCode"]));baseline.geometry.native_geometry.completed_log(text)
            immutable["logs/"+stage["log"]]=record(output,"logs/"+stage["log"],LIMITS["maximumLogBytes"])[1]
            if current=="checkMesh":
                baseline.geometry.verify_region_count(text,2)
                measured=base.verify_merged_mesh(output/"case",verified["components"],output/"logs"/stage["log"])
                require(measured["mergedCellCount"]==sum(c["cellCount"] for c in verified["components"])
                    and measured["mergedPointCount"]==sum(c["nodeCount"] for c in verified["components"]),"The phase case must retain exact admitted source component counts.")
                report.update(mergedNativeMeshEvidence=measured,nativeMeshQuality=baseline.scientific.parse_mesh_quality(text),nativeCheckMeshVerified=True,
                    nativeGeometryQualityVerified=True,physicalWallTypesVerified=True,coupledInterfaceCoverageVerified=True,nativeNccDiagnosticRecords=ncc_diagnostic_records(output))
                near=near_points_evidence(output);report.update(nativeCheckMeshDiagnosticRecords=near["files"],nativeNearPointsEvidence=near["evidence"])
            else:
                report["nativeSolverCompleted"]=True
                require(re.search(r"(?m)^Selecting cavitation model SchnerrSauer\s*$",text),"Actual active SchnerrSauer selection must appear in the native solver log.")
                report["cavitationModelExecuted"]=True
                records,times=final_fields(output,prepared["temporalPlan"],report["mergedNativeMeshEvidence"]["mergedCellCount"],text)
                pressures={Path(row["path"]).name:row for row in records if Path(row["path"]).name in {"p","p_rgh"}}
                require(set(pressures)=={"p","p_rgh"},"Actual absolute-Pa p/p_rgh endpoint fields are required.")
                reconstruction=absolute_pressure_reconstruction(record(output,pressures["p"]["path"])[0],record(output,pressures["p_rgh"]["path"])[0],report["mergedNativeMeshEvidence"]["mergedCellCount"])
                report.update(finalNativeFieldRecords=records,nativeEndTimeS=times[-1][0],endpointAbsolutePressureReconstruction=reconstruction,finiteNativeEndFieldsVerified=True,
                    nativeSamplingRecords=sampling_records(output),simulationExecuted=True,stationaryMrfRelaxationExecuted=True)
        require(all(record(output,path,LIMITS["maximumLogBytes"] if path.startswith("logs/") else None)[1]==row for path,row in immutable.items())
            and all(record(output,row["path"])[1]=={key:row[key] for key in ("path","bytes","sha256")} for row in input_rows),"Source, model, controls, mesh, authored initial fields or current input packets changed during execution.")
        require(all(record(output,row["path"])[1]=={key:row[key] for key in ("path","bytes","sha256")} for row in report["finalNativeFieldRecords"])
            and all(record(output,row["path"],4*1024**2)[1]==row for row in report["nativeSamplingRecords"])
            and ncc_diagnostic_records(output)==report["nativeNccDiagnosticRecords"],"Actual endpoint, table or NCC diagnostic evidence changed during final verification.")
        near=near_points_evidence(output)
        require(near["files"]==report["nativeCheckMeshDiagnosticRecords"] and near["evidence"]==report["nativeNearPointsEvidence"],"The actual source-bound phase nearPoints diagnostic changed after native checkMesh binding.")
        final_verification=preparation.verify_output(output,after_native_execution=True)
        report.update(nativeGeneratedDataRecords=final_verification["nativeGeneratedDataRecords"],nativeGeneratedDataEvidence=final_verification["nativeGeneratedDataEvidence"])
        require(all(record(output,row["path"])[1]==row for row in report["nativeGeneratedDataRecords"]),"Native generated NCC cache bytes changed during final verification.")
        original=preparation.verify_baseline(source)
        require(original["sourceRecords"]==[{**row,"path":row["path"].removeprefix("source-baseline/")} for row in prepared["sourceEvidenceFileRecords"]],"Original numerical baseline evidence changed after its immutable source copy.")
        workspace_size(output);require(time.monotonic()<deadline,"The teaching active-phase deadline expired during final byte verification.")
        report.update(inputArtifactsImmutable=True,sourceBaselineImmutable=True,actualCfdRunVerified=True,status=STATUS)
    except Exception as error:
        report.update(status="TEACHING_ACTIVE_PHASE_REFERENCE_CORRECTION_REQUIRED",actualCfdRunVerified=False,diagnostic={"program":current,"type":type(error).__name__,"message":str(error)[:500]})
    try:
        report["retainedNativeLogRecords"]=[record(output,f"logs/{i:02d}-{name}.log",LIMITS["maximumLogBytes"],empty=True)[1]
            for i,name in enumerate(PROGRAMS) if (output/f"logs/{i:02d}-{name}.log").exists()]
        report["workspaceBytes"]=workspace_size(output)
    except Exception as error:
        report.update(status="TEACHING_ACTIVE_PHASE_REFERENCE_RETENTION_CORRECTION_REQUIRED",actualCfdRunVerified=False,retentionDiagnostic={"type":type(error).__name__,"message":str(error)[:500]},workspaceBytes=None)
    report["elapsedSeconds"]=round(time.monotonic()-started,3)
    if report["elapsedSeconds"]>=LIMITS["maximumSeconds"]:report.update(status="TEACHING_ACTIVE_PHASE_REFERENCE_CORRECTION_REQUIRED",actualCfdRunVerified=False,diagnostic={"program":"final-deadline","message":"The complete active-phase evidence deadline expired."})
    base.write_exclusive(output/REPORT,base.inventory.bounded_json(report));return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--baseline-directory",type=Path,required=True);parser.add_argument("--output-directory",type=Path,required=True)
    args=parser.parse_args()
    try:
        report=run(args.baseline_directory,args.output_directory)
        summary={key:report[key] for key in ("status","simulationExecuted","actualCfdRunVerified","cavitationModelExecuted","phaseH0Eligible","npsh3Qualified","physicalTransientExecuted","mainApplicationWrites")}
        if "diagnostic" in report:summary["diagnostic"]=report["diagnostic"]
        print(json.dumps(summary,allow_nan=False));return 0 if report["actualCfdRunVerified"] else 2
    except Exception as error:
        print(json.dumps({"status":"TEACHING_PHASE_REFERENCE_INPUT_CORRECTION_REQUIRED","action":str(error)[:500],"actualCfdRunVerified":False,"phaseH0Eligible":False,"mainApplicationWrites":False}));return 2


if __name__=="__main__":raise SystemExit(main())
