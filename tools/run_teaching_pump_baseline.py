"""Execute four bounded steady native stages on a new authored teaching pump.

Native solver completion is a recorded fact. A separate reducer must qualify
the actual numerical baseline; this runner calculates no pump head or NPSH.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
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
import run_teaching_pump_geometry as geometry
import run_frozen_rotor_original as endpoint
import frozen_rotor_native_reduction as scientific

base,safety,native=geometry.base,geometry.safety,geometry.native
SCHEMA="untirta.teaching-pump-baseline-execution.v1"
PREPARATION_SCHEMA="untirta.teaching-pump-baseline-preparation.v1"
RUNTIME_SCHEMA="untirta.teaching-pump-baseline-runtime-identity.v1"
REPORT="teaching-baseline-execution.json"
RUNTIME="teaching-baseline-runtime-identity.json"
MANIFEST="teaching-baseline-input-manifest.json"
STUDY="teaching-baseline-study.json"
IDENTITY="teaching-geometry-identity.json"
PROGRAMS=("mergeMeshes","createNonConformalCouples","checkMesh","foamRun")
LIMITS={"maximumSeconds":1200,"cpus":1,"maximumMemoryBytes":4*1024**3,"maximumWorkspaceBytes":1024**3,
    "maximumFileBytes":128*1024**2,"maximumLogBytes":4*1024**2,"maximumReportBytes":128*1024,"maximumPaths":2048,"maximumPathDepth":8}


def require(condition,action):
    geometry.require(condition,action)


def commands(output):
    root=Path(output).absolute();case=str(root/"impeller-case");other=str(root/"volute-case")
    return [["mergeMeshes","-case",case,"-addCases",'(\"'+other+'\")',"-overwrite"],
            ["createNonConformalCouples","-case",case,"-overwrite"],
            ["checkMesh","-case",case,"-allGeometry","-allTopology","-writeSets"],
            ["foamRun","-case",case]]


def record(root,relative,limit=None,empty=False):
    path=Path(relative)
    require(not path.is_absolute() and ".." not in path.parts and all(part and not part.startswith(".") for part in path.parts),
        "Use only an ordinary case-relative native evidence role.")
    return safety.descriptor(Path(root)/path,path.as_posix(),limit or LIMITS["maximumFileBytes"],empty=empty)


def workspace_size(directory):
    root=base.regular_directory(directory);total=0;count=0
    for path in root.rglob("*"):
        count+=1
        require(count<=LIMITS["maximumPaths"] and len(path.relative_to(root).parts)<=LIMITS["maximumPathDepth"],"The authored baseline path/depth bound was reached.")
        require(not path.is_symlink() and not getattr(path,"is_junction",lambda:False)() and (path.is_file() or path.is_dir()),
            "The native baseline workspace permits only ordinary owned files/directories.")
        if path.is_file():
            size=path.stat().st_size;total+=size
            require(size<=LIMITS["maximumFileBytes"] and total<=LIMITS["maximumWorkspaceBytes"],"The authored baseline file/workspace bound was reached.")
    return total


def child_limits():
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(LIMITS["maximumMemoryBytes"],LIMITS["maximumMemoryBytes"]))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_FSIZE,(LIMITS["maximumFileBytes"],LIMITS["maximumFileBytes"]))
    allowed=os.sched_getaffinity(0);require(allowed,"A fresh native child needs one allowed CPU.")
    os.sched_setaffinity(0,{min(allowed)})


def runtime_identity(output,deadline):
    def query(arguments):
        remaining=deadline-time.monotonic();require(remaining>0,"The baseline deadline expired during package verification.")
        result=subprocess.run(arguments,env=native.child_environment(),capture_output=True,text=True,timeout=min(10,remaining),check=True)
        require(len(result.stdout)<=8192 and len(result.stderr)<=8192,"Bound every native package query output.")
        return result.stdout
    require(query(["dpkg-query","--show","--showformat=${Version}","openfoam12"])=="20250206","Require the exact pinned official Foundation12 package.")
    binaries=[]
    for name in PROGRAMS:
        found=shutil.which(name);require(found,"An official baseline utility is absent: "+name)
        path=Path(found).resolve()
        require(path.is_relative_to(Path("/opt/openfoam12")) and path.name==name,"Only the exact utility inside the official native package may execute.")
        owner=query(["dpkg-query","--search",str(path)]).strip()
        require(owner=="openfoam12: "+str(path),"Every actual native utility must be owned by the pinned package.")
        _,row=safety.descriptor(path)
        binaries.append({**row,"program":name,"path":str(path),"packageOwnership":owner})
    require(time.monotonic()<deadline,"The baseline deadline expired after binding actual native binaries.")
    identity={"schemaVersion":RUNTIME_SCHEMA,"openFoamFoundationVersion":12,"packageVersion":"20250206",
        "officialDebSha256":safety.PACKAGE_SHA,"expectedSourceCommit":safety.SOURCE_COMMIT,"binaries":binaries,"mainApplicationWrites":False}
    base.write_exclusive(Path(output)/RUNTIME,base.inventory.bounded_json(identity));return identity


def execute(arguments,output,deadline,runtime,index):
    require(type(index) is int and 0<=index<4 and arguments==commands(output)[index],"Execute only one exact indexed authored baseline command.")
    output=base.regular_directory(output)
    rows=[row for row in runtime["binaries"] if row["program"]==arguments[0]]
    require(len(rows)==1,"Bind one unambiguous current native utility identity.")
    row=rows[0];binary=Path(row["path"])
    require(binary.resolve().is_relative_to(Path("/opt/openfoam12")) and binary.name==arguments[0]
        and row.get("packageOwnership")=="openfoam12: "+str(binary),"Use only the current hash-bound package-owned native executable.")
    _,actual=safety.descriptor(binary)
    require((actual["bytes"],actual["sha256"])==(row["bytes"],row["sha256"]),"The actual native executable changed after binding.")
    relative=f"logs/{index:02d}-{arguments[0]}.log";path=output/relative
    require(not path.exists() and not path.is_symlink() and time.monotonic()<deadline,"Native baseline logs must be fresh before the serial deadline.")
    workspace_size(output);process=None;total=0;started=time.monotonic();next_progress=started+30;selector=selectors.DefaultSelector()
    try:
        with path.open("xb") as destination:
            process=subprocess.Popen([str(binary),*arguments[1:]],cwd=arguments[2],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                env=native.child_environment(),preexec_fn=child_limits,start_new_session=True,bufsize=0)
            selector.register(process.stdout,selectors.EVENT_READ)
            while selector.get_map():
                now=time.monotonic();require(now<deadline,"The1200s serial native baseline deadline expired.");workspace_size(output)
                if now>=next_progress:
                    print(json.dumps({"stageIndex":index,"program":arguments[0],"status":"AuthoredNativeBaselineRunning",
                        "elapsedSeconds":round(now-started,1),"actualCfdRunVerified":False,"mainApplicationWrites":False}),flush=True);next_progress=now+30
                for key,_ in selector.select(timeout=.25):
                    chunk=os.read(key.fileobj.fileno(),4096)
                    if not chunk:selector.unregister(key.fileobj);continue
                    remaining=LIMITS["maximumLogBytes"]-total;destination.write(chunk[:remaining]);destination.flush();total+=min(len(chunk),remaining)
                    require(len(chunk)<=remaining,"The authored native baseline log exceeded4MiB.")
            code=process.wait(timeout=3);workspace_size(output)
            require(time.monotonic()<deadline,"The baseline serial deadline expired at native process completion.")
    finally:
        if process is not None:native.stop_owned(process);process.stdout.close()
        selector.close()
    raw,metadata=record(output,relative,LIMITS["maximumLogBytes"])
    return {"program":arguments[0],"exitCode":code,"elapsedSeconds":round(time.monotonic()-started,3),
        "log":path.name,"logBytes":metadata["bytes"],"logSha256":metadata["sha256"]},raw.decode("utf-8",errors="strict")


def bind_manifest(output):
    import reduce_teaching_pump_baseline as reduction
    bound=reduction.build_manifest(output)
    for name,key in ((IDENTITY,"geometryIdentity"),(MANIFEST,"manifest"),(STUDY,"study")):
        raw=reduction.encoded(bound[key]);require(0<len(raw)<=LIMITS["maximumReportBytes"],"Bound each canonical teaching identity, study and manifest.")
        base.write_exclusive(Path(output)/name,raw)
    return {name:record(output,name,LIMITS["maximumReportBytes"])[1] for name in (IDENTITY,MANIFEST,STUDY)},bound["manifest"]["files"]


def final_fields(output,cells):
    # This reviewed pure parser scans every endpoint cell and exact SI native
    # dimensions. It does not apply old HDF5/remesh provenance or qualification.
    return endpoint.final_fields(Path(output),cells)


def near_points_evidence(output,*,case_relative="impeller-case",log_relative="logs/02-checkMesh.log",
                         authored_report_relative="source-teaching/source-geometry/teaching-geometry.json"):
    """Bind the exact native near-point diagnostic to duplicated NCC labels.

    A native Mesh OK may still report coincident points on disjoint NCC sides.
    VTK float coordinates are a diagnostic projection, never an exact geometry
    replacement; the actual native pointSet and face labels provide identity.
    """
    import frozen_rotor_native_quality as parser
    vtk_path=case_relative+"/postProcessing/checkMesh/constant/nearPoints.vtk"
    set_path=case_relative+"/constant/polyMesh/sets/nearPoints"
    vtk_file,set_file,log_file=(Path(output)/path for path in (vtk_path,set_path,log_relative))
    log,log_record=record(output,log_relative,LIMITS["maximumLogBytes"]) if log_file.exists() else (b"",None)
    text=log.decode("utf-8")
    vtk_exists=vtk_file.exists() or vtk_file.is_symlink();set_exists=set_file.exists() or set_file.is_symlink()
    if not vtk_exists and not set_exists and not re.search(r"Writing \d+ near .*points to set nearPoints",text):return {"files":[],"evidence":None}
    require(vtk_exists and set_exists and log_record is not None,"A native nearPoints log/set/VTK diagnostic must be retained completely.")
    vtk,vtk_record=record(output,vtk_path)
    geometry.native_geometry.completed_log(text)
    require("Mesh OK." in text,"A nearPoints diagnostic cannot qualify an unsuccessful native mesh.")
    counts=re.findall(r"Writing ([1-9][0-9]*) near \(closer than ([^\s]+) apart\) points to set nearPoints",text)
    require(len(counts)==1 and math.isfinite(float(counts[0][1])) and float(counts[0][1])>0,"Retain one actual nearPoints count and threshold from the native checkMesh log.")
    count=int(counts[0][0]);report_raw,report_record=record(output,authored_report_relative,LIMITS["maximumReportBytes"])
    report=json.loads(report_raw)
    shared=report.get("commonInterfaceEvidence",{}).get("sharedNodeCount")
    require(report.get("schemaVersion")=="untirta.teaching-pump-geometry.v1" and report.get("provenance")=="CLASSROOM_AUTHORED_GEOMETRY" and report.get("units")=="SI"
        and type(shared) is int and 0<shared<=500000 and count==2*shared,"The supported nearPoints diagnostic must match both exact authored shared-interface point inventories.")
    np,_=base.native_list(Path(output)/case_relative/"constant/polyMesh/points")
    set_raw,set_record=record(output,set_path)
    labels=parser.labels(parser.native_body(set_raw,"nearPoints",{"pointSet"}),np,expected=count)
    patches=geometry.native_geometry.boundary_entries(Path(output)/case_relative/"constant/polyMesh/boundary")
    intervals={name:(patches[name]["startFace"],patches[name]["startFace"]+patches[name]["nFaces"])
        for name in ("interface-impeller-outlet","interface-volute-inlet")}
    _,body=base.native_list(Path(output)/case_relative/"constant/polyMesh/faces");points={name:set() for name in intervals}
    for index,face in enumerate(re.finditer(r"(\d+)\s*\(([^()]*)\)",body)):
        for name,(first,last) in intervals.items():
            if first<=index<last:
                values=face[2].split();require(len(values)==int(face[1]) and all(re.fullmatch(r"\d+",v) for v in values),"Actual interface face labels must remain complete native integers.")
                points[name].update(map(int,values))
    rotor,stator=points.values()
    require(len(rotor)==len(stator)==shared and not rotor&stator and labels==rotor|stator,"Native nearPoints must equal exactly the disjoint duplicated interface point labels.")
    match=re.fullmatch(r"# vtk DataFile Version 2\.0\s+nearPoints\s+ASCII\s+DATASET POLYDATA\s+POINTS (\d+) float\s+(.*?)\s+VERTICES (\d+) (\d+)\s+(.*?)\s+POINT_DATA (\d+)\s+FIELD attributes 1\s+pointID 1 (\d+) float\s+(.*?)\s*",vtk.decode("ascii"),re.S)
    require(match and all(int(match[i])==count for i in (1,3,6,7)) and int(match[4])==2*count,"The native ASCII nearPoints VTK count and field roles must match the actual pointSet and log.")
    coordinates=match[2].split();vertices=match[5].split();ids=match[8].split()
    require(len(coordinates)==3*count and all(re.fullmatch(scientific.FLOAT,v) and math.isfinite(float(v)) for v in coordinates)
        and len(vertices)==2*count and all(vertices[2*i]=="1" and vertices[2*i+1]==str(i) for i in range(count))
        and len(ids)==count and all(re.fullmatch(r"\d+",v) for v in ids) and {int(v) for v in ids}==labels,
        "Every native nearPoints VTK coordinate, vertex index and actual pointID must be complete and finite.")
    evidence={"nativeNearPointCount":count,"sharedInterfaceNodeCountPerSide":shared,"countEquation":"N_near=2*N_shared_interface",
        "nativeNearDistanceThresholdM":float(counts[0][1]),"nativePointSetEqualsDuplicatedInterfaceLabels":True,
        "vtkCoordinatePrecisionDefinesGeometry":False,"nativeCheckMeshLog":log_record,"authoredGeometryReport":report_record,
        "claimBoundary":"Retained native diagnostic for coincident points on disjoint matched NCC sides; no points are welded, removed or moved, and no mesh/numerical criterion is relaxed."}
    return {"files":[vtk_record,set_record],"evidence":evidence}


def postprocessing_records(output):
    """Separate native checkMesh coverage diagnostics from solver sample tables.

    The authored checkMesh command runs at the declared initial time0. Its two
    exact interface names are source-bound; arbitrary VTK roles stay rejected.
    """
    folder=Path(output)/"impeller-case/postProcessing"
    if not folder.exists():return [],[]
    require(folder.is_dir(),"The native postProcessing role must be an ordinary directory.")
    rows=[];diagnostics=[]
    for path in sorted(folder.rglob("*")):
        require(not path.is_symlink() and not getattr(path,"is_junction",lambda:False)() and (path.is_file() or path.is_dir()),"Native samples must be ordinary files/directories.")
        if path.is_file():
            relative=path.relative_to(output).as_posix()
            if path.suffix==".dat":rows.append(record(output,relative,LIMITS["maximumLogBytes"])[1])
            elif relative=="impeller-case/postProcessing/checkMesh/constant/nearPoints.vtk":
                near_points_evidence(output)
            else:
                require(re.fullmatch(r"impeller-case/postProcessing/checkMesh/0/(?:interface-impeller-outlet|interface-volute-inlet)_coverage\.vtk",relative),
                    "Only solver sampling tables and the exact initial NCC coverage diagnostic VTK roles may be retained.")
                diagnostics.append(record(output,relative)[1])
    require(len(rows)<=128 and len(diagnostics) in (0,2),"Bound native table inventory and retain both available source-bound NCC coverage diagnostics.")
    return rows,diagnostics


def sampling_records(output):
    rows,_=postprocessing_records(output)
    require(0<len(rows)<=128,"A bounded nonempty actual native sampling inventory is required.");return rows


def ncc_diagnostic_records(output):
    return postprocessing_records(output)[1]


def retain_logs(output):
    rows=[]
    for index,args in enumerate(commands(output)):
        relative=f"logs/{index:02d}-{args[0]}.log";path=Path(output)/relative
        if path.exists():rows.append(record(output,relative,LIMITS["maximumLogBytes"],empty=True)[1])
    return rows


def run(geometry_native_directory,output_directory):
    started=time.monotonic();deadline=started+LIMITS["maximumSeconds"];safety.context_guard()
    source=safety.scope_path(geometry_native_directory);output=safety.scope_path(output_directory,fresh=True)
    import teaching_pump_baseline_case as preparation
    prepared=preparation.prepare(source,output);verified=preparation.verify_output(output);output=base.regular_directory(output)
    require(prepared==verified["report"] and prepared["schemaVersion"]==PREPARATION_SCHEMA and prepared["nativeCommandArguments"]==commands(output),
        "Require the exact reviewed four-stage new authored teaching preparation.")
    report={"schemaVersion":SCHEMA,"sourceGeometryRoute":"CLASSROOM_AUTHORED_GEOMETRY","githubRepository":"simcalc/PyAnsys",
        "githubRunId":os.environ.get("GITHUB_RUN_ID"),"githubSourceCommit":os.environ.get("GITHUB_SHA"),
        "status":"TEACHING_NATIVE_BASELINE_REQUIRED","limits":dict(LIMITS),"stages":[],"preparationReportSha256":verified["reportFile"]["sha256"],
        **{key:prepared[key] for key in ("sourceGeometryExecutionReportSha256","sourceGeometryPreparationReportSha256","sourceGeometryInputManifestSha256","sourceAuthoredGeometryReportSha256","sourceGeometryIdentitySha256")},
        "nativeGeometryQualityVerified":False,"physicalWallTypesVerified":False,"coupledInterfaceCoverageVerified":False,
        "nativeSolverCompleted":False,"finiteNativeFieldsVerified":False,"inputArtifactsImmutable":False,"sourceGeometryImmutable":False,
        "simulationExecuted":False,"actualCfdRunVerified":False,"numericalBaselineQualified":False,"qualifiedBaseline":False,
        "wallResolutionQualified":False,"meshConvergenceVerified":False,"timeConvergenceVerified":False,"cavitationSweepExecuted":False,
        "npsh3Qualified":False,"physicalTransientExecuted":False,"restartVerified":False,"manufacturerGeometryVerified":False,"mainApplicationWrites":False,
        "claimBoundary":"Actual steady SST/MRF execution on a new authored SI classroom pump. Numerical acceptance is independent and pending; manufacturer performance, wall resolution, mesh/time convergence, NPSH3 and physical transient remain unverified."}
    (output/"logs").mkdir();current="runtime-identity";bound_rows={};bound_inputs=[]
    try:
        runtime=runtime_identity(output,deadline)
        immutable={"teaching-baseline-preparation.json":verified["reportFile"],RUNTIME:record(output,RUNTIME,LIMITS["maximumReportBytes"])[1]}
        report["runtimeIdentitySha256"]=immutable[RUNTIME]["sha256"]
        for key in ("authoredInputFiles","sourceComponentFiles","sourceEvidenceFileRecords","authoredPhysicsFileRecords"):
            for row in prepared[key]:
                if key=="authoredInputFiles" and "/0/" in row["path"]:continue
                require(record(output,row["path"])[1]==row,"A source, snapshot, dictionary or immutable authored physical copy changed before native startup.")
                immutable[row["path"]]=row
        for index,args in enumerate(commands(output)):
            current=args[0]
            if current=="foamRun":
                bound_rows,bound_inputs=bind_manifest(output)
                report.update(inputManifestSha256=bound_rows[MANIFEST]["sha256"],studySha256=bound_rows[STUDY]["sha256"],geometryIdentitySha256=bound_rows[IDENTITY]["sha256"])
            stage,text=execute(args,output,deadline,runtime,index);report["stages"].append(stage)
            require(stage["exitCode"]==0,"An actual native baseline utility returned exit "+str(stage["exitCode"]));geometry.native_geometry.completed_log(text)
            immutable["logs/"+stage["log"]]=record(output,"logs/"+stage["log"],LIMITS["maximumLogBytes"])[1]
            if current=="checkMesh":
                geometry.verify_region_count(text,2)
                expected={row["component"]:row for row in verified["components"]}
                measured=base.verify_merged_mesh(output/"impeller-case",expected,output/"logs"/stage["log"])
                quality=scientific.parse_mesh_quality(text)
                require(measured["mergedCellCount"]==sum(row["cellCount"] for row in expected.values())
                    and measured["mergedPointCount"]==sum(row["nodeCount"] for row in expected.values()),"The merged native case must retain both admitted component counts.")
                report.update(mergedNativeMeshEvidence=measured,nativeMeshQuality=quality,nativeGeometryQualityVerified=True,
                    physicalWallTypesVerified=True,coupledInterfaceCoverageVerified=True,nativeNccDiagnosticRecords=ncc_diagnostic_records(output))
                near=near_points_evidence(output);report.update(nativeCheckMeshDiagnosticRecords=near["files"],nativeNearPointsEvidence=near["evidence"])
            elif current=="foamRun":
                history=scientific.parse_solver_log(text);require(max(history)==500,"Actual steady native output must reach the500 authored outer iterations.")
                report["nativeSolverCompleted"]=True;workspace_size(output)
                primary,fields,scans=final_fields(output,sum(row["cellCount"] for row in verified["components"]))
                samples=sampling_records(output)
                require(all(record(output,row["path"])[1]=={key:row[key] for key in ("path","bytes","sha256")} for row in bound_inputs),
                    "An actual post-NCC input bound before CFD changed during the native solve.")
                report.update(finalNativeFields=primary,finalNativeFieldRecords=fields,finiteNativeFieldEvidence=scans,nativeSamplingRecords=samples,
                    finiteNativeFieldsVerified=True,simulationExecuted=True)
        require(all(record(output,path,LIMITS["maximumLogBytes"] if path.startswith("logs/") else None)[1]==row for path,row in {**immutable,**bound_rows}.items()),
            "An immutable source, geometry identity, runtime, input manifest, study, log or dictionary changed during execution.")
        require(all(record(output,row["path"])[1]==row for row in report["finalNativeFieldRecords"])
            and all(record(output,row["path"],LIMITS["maximumLogBytes"])[1]==row for row in report["nativeSamplingRecords"]),"Actual endpoint/sample bytes changed during final verification.")
        require(ncc_diagnostic_records(output)==report["nativeNccDiagnosticRecords"],"An actual initial NCC coverage diagnostic changed or appeared after its native checkMesh binding.")
        near=near_points_evidence(output)
        require(near["files"]==report["nativeCheckMeshDiagnosticRecords"] and near["evidence"]==report["nativeNearPointsEvidence"],"An actual source-bound nearPoints diagnostic changed after native checkMesh binding.")
        preparation.verify_output(output,after_native_ncc=True);original=preparation.validate_source(source)
        require(original["sourceFileRecords"]==[{**row,"path":row["path"].removeprefix("source-teaching/")}
            for row in prepared["sourceEvidenceFileRecords"]],"The original geometry source inventory changed after its immutable copy was bound.")
        workspace_size(output);require(time.monotonic()<deadline,"The baseline deadline expired during final source and native byte verification.")
        report.update(inputArtifactsImmutable=True,sourceGeometryImmutable=True,actualCfdRunVerified=True,status="TEACHING_MRF_EXECUTED_NUMERICAL_ACCEPTANCE_REQUIRED")
    except Exception as error:
        report.update(status="TEACHING_NATIVE_BASELINE_CORRECTION_REQUIRED",actualCfdRunVerified=False,
            diagnostic={"program":current,"type":type(error).__name__,"message":str(error)[:500]})
    try:report["retainedNativeLogRecords"]=retain_logs(output);report["workspaceBytes"]=workspace_size(output)
    except Exception as error:
        report.update(status="TEACHING_NATIVE_RETENTION_CORRECTION_REQUIRED",actualCfdRunVerified=False,
            diagnostic={"program":"retention","type":type(error).__name__,"message":str(error)[:500]},workspaceBytes=None)
    report["elapsedSeconds"]=round(time.monotonic()-started,3)
    if report["elapsedSeconds"]>=LIMITS["maximumSeconds"]:
        report.update(actualCfdRunVerified=False,status="TEACHING_NATIVE_BASELINE_CORRECTION_REQUIRED",diagnostic={"program":"final-deadline","message":"The complete native baseline evidence deadline expired."})
    base.write_exclusive(output/REPORT,base.inventory.bounded_json(report))
    summary={key:report[key] for key in ("status","simulationExecuted","actualCfdRunVerified","numericalBaselineQualified","qualifiedBaseline","mainApplicationWrites")}
    if "diagnostic" in report:summary["diagnostic"]=report["diagnostic"]
    print(json.dumps(summary,allow_nan=False));return 0 if report["actualCfdRunVerified"] else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--geometry-native-directory",type=Path,required=True);parser.add_argument("--output-directory",type=Path,required=True)
    args=parser.parse_args()
    try:return run(args.geometry_native_directory,args.output_directory)
    except Exception as error:
        print(json.dumps({"status":"TEACHING_BASELINE_INPUT_CORRECTION_REQUIRED","action":str(error)[:500],"actualCfdRunVerified":False,"mainApplicationWrites":False}));return 2


if __name__=="__main__":raise SystemExit(main())
