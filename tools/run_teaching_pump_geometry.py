"""Run only nine fixed native geometry stages for the authored teaching CAD.

Actual component quality and matched NCC geometry are separate from CFD. This
runner never invokes foamRun or accepts a numerical/physical pump baseline.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
import teaching_pump_native_case as preparation
import frozen_rotor_native_reduction as reduction

base, safety, native_geometry = preparation.base, preparation.safety, preparation.native_geometry
native = native_geometry.native
SCHEMA = "untirta.teaching-pump-openfoam-geometry-execution.v1"
RUNTIME_SCHEMA = "untirta.teaching-pump-native-runtime-identity.v1"
REPORT, RUNTIME = "teaching-native-execution.json", "teaching-native-runtime-identity.json"
PROGRAMS = ("gmshToFoam","changeDictionary","checkMesh","mergeMeshes","createNonConformalCouples")
MAX_SECONDS, MAX_MEMORY, MAX_WORKSPACE, MAX_LOG = 600, 2*1024**3, 512*1024**2, 4*1024**2
MESH_FILES = ("points","faces","owner","neighbour","boundary","cellZones")


def require(condition, action):
    preparation.require(condition, action)


def workspace_size(root):
    root=base.regular_directory(root);total=0;count=0
    for path in root.rglob("*"):
        count+=1
        require(count<=2048 and len(path.relative_to(root).parts)<=8,"The native teaching path/depth bound was reached.")
        require(not path.is_symlink() and not getattr(path,"is_junction",lambda:False)() and (path.is_file() or path.is_dir()),
            "Only ordinary owned native files/directories are allowed.")
        if path.is_file():
            size=path.stat().st_size;total+=size
            require(size<=preparation.MAX_FILE and total<=MAX_WORKSPACE,"The native teaching file/workspace bound was reached.")
    return total


def child_limits():
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(MAX_MEMORY,MAX_MEMORY))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_FSIZE,(preparation.MAX_FILE,preparation.MAX_FILE))
    allowed=os.sched_getaffinity(0);require(allowed,"The fresh child needs one allowed CPU.")
    os.sched_setaffinity(0,{min(allowed)})


def runtime_identity(deadline):
    def query(args):
        remaining=deadline-time.monotonic();require(remaining>0,"The native geometry deadline expired during package verification.")
        result=subprocess.run(args,env=native.child_environment(),capture_output=True,text=True,check=True,timeout=min(10,remaining))
        require(len(result.stdout)<=8192 and len(result.stderr)<=8192,"Bound actual package query output.")
        return result.stdout
    require(query(["dpkg-query","--show","--showformat=${Version}","openfoam12"])=="20250206","Require the exact pinned official Foundation12 package.")
    binaries=[]
    for name in PROGRAMS:
        executable=shutil.which(name);require(executable,"An official geometry utility is absent: "+name)
        path=Path(executable).resolve();require(path.is_relative_to(Path("/opt/openfoam12")) and path.name==name,"Use the exact native program inside the official package.")
        owner=query(["dpkg-query","--search",str(path)]).strip()
        require(owner=="openfoam12: "+str(path),"Every native utility must belong to the exact installed package.")
        _,row=safety.descriptor(path);binaries.append({**row,"program":name,"path":str(path),"packageOwnership":owner})
    require(time.monotonic()<deadline,"The runtime identity deadline expired at completion.")
    return {"schemaVersion":RUNTIME_SCHEMA,"openFoamFoundationVersion":12,"packageVersion":"20250206",
        "officialDebSha256":safety.PACKAGE_SHA,"expectedSourceCommit":safety.SOURCE_COMMIT,"binaries":binaries,"mainApplicationWrites":False}


def execute(arguments,output,deadline,runtime,index):
    require(type(index) is int and 0<=index<9 and arguments==preparation.commands(output)[index],
        "Execute only the exact indexed current-workspace geometry command.")
    rows=[r for r in runtime["binaries"] if r["program"]==arguments[0]];require(len(rows)==1,"One bound official native utility is required.")
    row=rows[0];binary=Path(row["path"])
    require(binary.resolve().is_relative_to(Path("/opt/openfoam12")) and binary.name==arguments[0]
        and row.get("packageOwnership")=="openfoam12: "+str(binary),"Use only the bound package-owned native executable.")
    _,actual=safety.descriptor(binary);require((actual["bytes"],actual["sha256"])==(row["bytes"],row["sha256"]),"The native binary changed after binding.")
    relative=f"logs/{index:02d}-{arguments[0]}.log";path=Path(output)/relative
    require(not path.exists() and not path.is_symlink() and time.monotonic()<deadline,"Use a fresh log before the fixed native deadline.")
    process=None;total=0;started=time.monotonic();next_progress=started+30;selector=selectors.DefaultSelector()
    try:
        with path.open("xb") as destination:
            process=subprocess.Popen([str(binary),*arguments[1:]],cwd=arguments[2],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                env=native.child_environment(),preexec_fn=child_limits,start_new_session=True,bufsize=0)
            selector.register(process.stdout,selectors.EVENT_READ)
            while selector.get_map():
                now=time.monotonic();require(now<deadline,"The600s serial native geometry deadline expired.");workspace_size(output)
                if now>=next_progress:
                    print(json.dumps({"stageIndex":index,"program":arguments[0],"status":"NativeGeometryRunning","elapsedSeconds":round(now-started,1),
                        "simulationExecuted":False,"mainApplicationWrites":False}),flush=True);next_progress=now+30
                for key,_ in selector.select(timeout=.25):
                    chunk=os.read(key.fileobj.fileno(),4096)
                    if not chunk:selector.unregister(key.fileobj);continue
                    remaining=MAX_LOG-total;destination.write(chunk[:remaining]);destination.flush();total+=min(len(chunk),remaining)
                    require(len(chunk)<=remaining,"The bounded native log exceeded4MiB.")
            code=process.wait(timeout=3);workspace_size(output)
            require(time.monotonic()<deadline,"The native deadline expired at process completion.")
    finally:
        if process is not None:native.stop_owned(process);process.stdout.close()
        selector.close()
    raw,row=preparation.record(output,relative,limit=MAX_LOG)
    return {"program":arguments[0],"exitCode":code,"elapsedSeconds":round(time.monotonic()-started,3),"log":path.name,
        "logBytes":row["bytes"],"logSha256":row["sha256"]},raw.decode("utf-8",errors="strict")


def native_mesh_records(output,name):
    return [preparation.record(output,f"{name}-case/constant/polyMesh/{file}")[1] for file in MESH_FILES]


def verify_region_count(text,expected):
    """Require real complete topology output; Mesh OK alone permits islands."""
    rows=re.findall(r"(?m)^[ \t]*\*?Number of regions:[ \t]+([1-9][0-9]*)(?: \(OK\)\.)?[ \t]*\r?$",text)
    require(rows==[str(expected)],"Native component/merged fluid connectivity must have exactly the authored region count.")
    return expected


def run(geometry_directory,output_directory):
    started=time.monotonic();deadline=started+MAX_SECONDS;safety.context_guard()
    source=safety.scope_path(geometry_directory);output=safety.scope_path(output_directory,fresh=True)
    prepared=preparation.prepare(source,output);verified=preparation.verify_output(output)
    expected={r["component"]:r for r in verified["components"]}
    # Re-read actual MSH coordinate order for the native converter, without
    # applying any old source/remesh schema or official geometry classification.
    meshes={name:base.msh_inventory(preparation.record(source,name+".msh")[0]) for name in preparation.COMPONENTS}
    report={"schemaVersion":SCHEMA,"sourceGeometryRoute":preparation.SOURCE_ROUTE,"githubRepository":"simcalc/PyAnsys",
        "githubRunId":os.environ.get("GITHUB_RUN_ID"),"githubSourceCommit":os.environ.get("GITHUB_SHA"),
        "status":"CLASSROOM_AUTHORED_NATIVE_GEOMETRY_REQUIRED","limits":preparation.LIMITS,"stages":[],"conversionEvidence":[],
        "physicalWallEvidence":[],"componentQualityEvidence":[],"componentNativeSnapshots":[],
        "preparationReportSha256":verified["reportFile"]["sha256"],"nativeInputManifestSha256":prepared["nativeInputManifestSha256"],
        "sourceGeometryReportSha256":prepared["sourceGeometryReportSha256"],"commonInterfaceEvidence":prepared["commonInterfaceEvidence"],
        "openFoamConversionVerified":False,"physicalWallTypesVerified":False,"inputArtifactsImmutable":False,
        "sourceGeometryImmutable":False,"nativeGeometryAccepted":False,**{k:False for k in preparation.FALSE_FLAGS},
        "claimBoundary":preparation.CLAIM}
    (output/"logs").mkdir();current="runtime-identity"
    try:
        runtime=runtime_identity(deadline);base.write_exclusive(output/RUNTIME,preparation.encoded(runtime))
        manifest,_=preparation.json_file(output,preparation.MANIFEST)
        immutable={r["path"]:r for r in manifest["files"]}
        for file in (preparation.REPORT,preparation.MANIFEST,RUNTIME):immutable[file]=preparation.record(output,file)[1]
        report["nativeRuntimeIdentitySha256"]=immutable[RUNTIME]["sha256"]
        for index,args in enumerate(preparation.commands(output)):
            current=args[0];row,text=execute(args,output,deadline,runtime,index);report["stages"].append(row)
            report["commandExecutionPerformedByThisTool"]=True
            require(row["exitCode"]==0,"Native geometry utility returned exit "+str(row["exitCode"]));native_geometry.completed_log(text)
            if index in (0,3):
                name=preparation.COMPONENTS[index//3]
                report["conversionEvidence"].append(base.verify_conversion(output/(name+"-case"),meshes[name],output/"logs"/row["log"],after_wall_type_change=False))
                report["openFoamConversionVerified"]=len(report["conversionEvidence"])==2
            elif index in (1,4):
                name=preparation.COMPONENTS[index//3];conversion_log=output/"logs"/f"{index-1:02d}-gmshToFoam.log"
                report["physicalWallEvidence"].append(base.verify_conversion(output/(name+"-case"),meshes[name],conversion_log,after_wall_type_change=True))
                report["physicalWallTypesVerified"]=len(report["physicalWallEvidence"])==2
            elif index in (2,5):
                name=preparation.COMPONENTS[index//3];quality=reduction.parse_mesh_quality(text)
                region_count=verify_region_count(text,1)
                require(quality["meshCellCount"]==expected[name]["cellCount"],"Native component checkMesh count differs from its actual source mesh.")
                report["componentQualityEvidence"].append({"component":name,"connectedFluidRegionCount":region_count,**quality})
                snapshots=[]
                for native_row in native_mesh_records(output,name):
                    target="component-mesh-snapshots/"+name+"/constant/polyMesh/"+Path(native_row["path"]).name
                    base.write_exclusive(output/target,preparation.record(output,native_row["path"])[0]);snapshots.append(preparation.record(output,target)[1])
                report["componentNativeSnapshots"].append({"component":name,"files":snapshots})
            elif index==8:
                report["mergedFluidRegionCount"]=verify_region_count(text,2)
                report["mergedNativeMeshEvidence"]=base.verify_merged_mesh(output/"impeller-case",expected,output/"logs"/row["log"])
                report["nativeMeshQuality"]=reduction.parse_mesh_quality(text)
                report["nativeGeometryQualityVerified"]=True;report["coupledInterfaceCoverageVerified"]=True
        require(all(preparation.record(output,path)[1]==row for path,row in immutable.items()),"Authored source, MSH or dictionaries changed during native execution.")
        preparation.verify_output(output);require(preparation.validate_inputs(source)==preparation.validate_inputs(output/"source-geometry"),"The original authored source changed during native execution.")
        require(time.monotonic()<deadline,"The native deadline expired during final source verification.")
        workspace_size(output);report.update(inputArtifactsImmutable=True,sourceGeometryImmutable=True,nativeGeometryAccepted=True,
            status="CLASSROOM_AUTHORED_NATIVE_GEOMETRY_ACCEPTED_PHYSICS_REQUIRED")
    except Exception as error:
        report.update(status="CLASSROOM_AUTHORED_NATIVE_GEOMETRY_CORRECTION_REQUIRED",nativeGeometryAccepted=False,
            diagnostic={"program":current,"type":type(error).__name__,"message":str(error)[:500]})
    report["elapsedSeconds"]=round(time.monotonic()-started,3)
    report["retainedNativeLogRecords"]=[safety.descriptor(path,"logs/"+path.name,MAX_LOG,empty=True)[1] for path in sorted((output/"logs").iterdir())]
    try:report["workspaceBytes"]=workspace_size(output)
    except Exception as error:
        report.update(nativeGeometryAccepted=False,status="CLASSROOM_AUTHORED_NATIVE_GEOMETRY_CORRECTION_REQUIRED",
            diagnostic={"program":"workspace-bound","type":type(error).__name__,"message":str(error)[:500]})
        report["workspaceBytes"]=None
    base.write_exclusive(output/REPORT,preparation.encoded(report))
    return {"status":report["status"],"reportFile":str(output/REPORT),"nativeGeometryAccepted":report["nativeGeometryAccepted"],
        "simulationExecuted":False,"actualCfdRunVerified":False,"mainApplicationWrites":False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--geometry-directory",required=True);parser.add_argument("--output-directory",required=True)
    args=parser.parse_args();result=run(args.geometry_directory,args.output_directory);print(json.dumps(result));return 0 if result["nativeGeometryAccepted"] else 1


if __name__=="__main__":raise SystemExit(main())
