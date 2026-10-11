"""Run a bounded numerical classroom baseline on accepted approximate meshes.

Only four authored Foundation-12 stages execute. Native completion does not
qualify surface approximation, wall resolution, mesh/time convergence or NPSH3.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_frozen_rotor_snappy as geometry
import run_frozen_rotor_original as original
import frozen_rotor_native_reduction as scientific

base,surface,native=geometry.base,geometry.surface,geometry.native
SCHEMA="untirta.frozen-rotor-snappy-baseline-execution.v1"
PREPARATION_SCHEMA="untirta.frozen-rotor-snappy-baseline-preparation.v1"
RUNTIME_SCHEMA="untirta.frozen-rotor-snappy-baseline-runtime-identity.v1"
MAX_SECONDS=1200
MAX_LOG=4*1024**2
PROGRAMS=("mergeMeshes","createNonConformalCouples","checkMesh","foamRun")


def require(condition,action):
    surface.require(condition,action)


def commands(output):
    root=Path(output).absolute();case=str(root/"impeller-case");other=str(root/"volute-case")
    return [["mergeMeshes","-case",case,"-addCases",'(\"'+other+'\")',"-overwrite"],
            ["createNonConformalCouples","-case",case,"-overwrite"],
            ["checkMesh","-case",case,"-allGeometry","-allTopology","-writeSets"],
            ["foamRun","-case",case]]


def runtime_identity(output,deadline):
    def query(arguments):
        remaining=deadline-time.monotonic()
        require(remaining>0,"The baseline deadline was reached during package binding.")
        result=subprocess.run(arguments,env=native.child_environment(),capture_output=True,text=True,timeout=min(10,remaining),check=True)
        require(len(result.stdout)<=8192 and len(result.stderr)<=8192,"Bound native package query output.")
        return result.stdout
    require(query(["dpkg-query","--show","--showformat=${Version}","openfoam12"])=="20250206","Use the exact reviewed Foundation-12 package.")
    binaries=[]
    for name in PROGRAMS:
        executable=shutil.which(name);require(executable,"An actual native baseline utility is absent: "+name)
        path=Path(executable).resolve()
        require(path.is_relative_to(Path("/opt/openfoam12")),"Only actual utilities from the pinned official package may run.")
        owner=query(["dpkg-query","--search",str(path)]).strip()
        require(owner=="openfoam12: "+str(path),"The actual executable must belong to the pinned native package.")
        _,row=surface.descriptor(path)
        binaries.append({**row,"program":name,"path":str(path),"packageOwnership":owner})
    identity={"schemaVersion":RUNTIME_SCHEMA,"openFoamFoundationVersion":"12","packageVersion":"20250206",
        "officialDebSha256":surface.PACKAGE_SHA,"expectedSourceCommit":surface.SOURCE_COMMIT,"binaries":binaries,"mainApplicationWrites":False}
    require(time.monotonic()<deadline,"The baseline deadline was reached after package binding.")
    base.write_exclusive(Path(output)/"runtime-identity.json",base.inventory.bounded_json(identity))
    return identity


def execute(arguments,output,deadline,runtime,index):
    require(isinstance(index,int) and not isinstance(index,bool) and 0<=index<4 and arguments==commands(output)[index],
            "Only the four indexed authored native baseline commands may execute.")
    output=base.regular_directory(output);relative=f"logs/{index:02d}-{arguments[0]}.log";path=output/relative
    require(not path.exists() and not path.is_symlink(),"Native baseline logs must be fresh and immutable.")
    matches=[row for row in runtime["binaries"] if row["program"]==arguments[0]]
    require(len(matches)==1,"Bind one unambiguous actual utility identity.")
    row=matches[0];binary=Path(row["path"])
    require(binary.resolve().is_relative_to(Path("/opt/openfoam12")),"Execute only the hash-bound official native utility.")
    _,actual=surface.descriptor(binary)
    require(actual["bytes"]==row["bytes"] and actual["sha256"]==row["sha256"],"Actual native executable bytes changed after binding.")
    require(time.monotonic()<deadline,"The baseline deadline was reached before native startup.")
    surface.workspace_size(output)
    process,total,started=None,0,time.monotonic();next_progress=started+30;selector=selectors.DefaultSelector()
    try:
        with path.open("xb") as destination:
            process=subprocess.Popen([str(binary),*arguments[1:]],cwd=arguments[2],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                env=native.child_environment(),preexec_fn=surface.child_limits,start_new_session=True,bufsize=0)
            selector.register(process.stdout,selectors.EVENT_READ)
            while selector.get_map():
                now=time.monotonic();require(now<deadline,"The bounded native baseline timed out.")
                surface.workspace_size(output)
                if now>=next_progress:
                    print(json.dumps({"stageIndex":index,"program":arguments[0],"status":"NativeBaselineRunning",
                        "elapsedSeconds":round(now-started,1),"actualCfdRunVerified":False,"mainApplicationWrites":False}),flush=True)
                    next_progress=now+30
                for key,_ in selector.select(timeout=.25):
                    chunk=os.read(key.fileobj.fileno(),4096)
                    if not chunk:selector.unregister(key.fileobj);continue
                    remaining=MAX_LOG-total;destination.write(chunk[:remaining]);destination.flush();total+=min(len(chunk),remaining)
                    require(len(chunk)<=remaining,"The native baseline log reached its 4 MiB cap.")
            code=process.wait(timeout=3);surface.workspace_size(output)
            require(time.monotonic()<deadline,"The native baseline deadline was reached at process completion.")
    finally:
        if process is not None:native.stop_owned(process);process.stdout.close()
        selector.close()
    raw,metadata=geometry.record(output,relative,MAX_LOG)
    return {"program":arguments[0],"exitCode":code,"elapsedSeconds":round(time.monotonic()-started,3),
        "log":path.name,"logBytes":metadata["bytes"],"logSha256":metadata["sha256"]},raw.decode("utf-8",errors="strict")


def bind_manifest(output):
    import reduce_frozen_rotor_snappy_baseline as reduction
    bound=reduction.build_manifest(output)
    for name,key in (("geometry-identity.json","geometryIdentity"),("native-input-manifest.json","manifest"),("classroom-study.json","study")):
        raw=reduction.encoded(bound[key])
        require(0<len(raw)<=base.inventory.MAX_REPORT_BYTES,"Bound every canonical geometry, input manifest and study before persistence.")
        base.write_exclusive(Path(output)/name,raw)
    return {name:geometry.record(output,name,base.inventory.MAX_REPORT_BYTES)[1]for name in
        ("geometry-identity.json","native-input-manifest.json","classroom-study.json")},bound["manifest"]["files"]


def final_fields(output,cells):
    # Reuse the reviewed complete endpoint/units parser; it performs no physics
    # calculation, execution-schema admission or geometry-route promotion.
    return original.final_fields(Path(output),cells)


def quality_diagnostic(output,log_path):
    import frozen_rotor_native_quality as quality
    diagnostic=quality.inspect(Path(output)/"impeller-case",log_path)
    base.write_exclusive(Path(output)/"native-quality-diagnostic.json",base.inventory.bounded_json(diagnostic))
    return geometry.record(output,"native-quality-diagnostic.json",base.inventory.MAX_REPORT_BYTES)[1]


def run(snappy_execution_directory,output_directory,*,pressure_reference_absolute_pa=300000):
    started=time.monotonic();deadline=started+MAX_SECONDS
    surface.context_guard()
    source=surface.scope_path(snappy_execution_directory);output=surface.scope_path(output_directory,fresh=True)
    import frozen_rotor_snappy_baseline_case as preparation
    prepared=preparation.prepare(source,output,pressure_reference_absolute_pa=pressure_reference_absolute_pa)
    verified=preparation.verify_output(output);output=base.regular_directory(output)
    require(prepared==verified["report"] and prepared["schemaVersion"]==PREPARATION_SCHEMA and prepared["nativeCommandArguments"]==commands(output),
            "Require the exact reviewed source-bound four-stage baseline preparation.")
    report={"schemaVersion":SCHEMA,"githubRepository":"simcalc/PyAnsys","githubRunId":os.environ.get("GITHUB_RUN_ID"),
        "githubSourceCommit":os.environ.get("GITHUB_SHA"),"exampleDataCommit":base.inventory.PIN,
        "status":"SNAPPY_NATIVE_BASELINE_REQUIRED","stages":[],"limits":{**prepared["limits"],"maximumLogBytes":MAX_LOG},
        "preparationReportSha256":verified["reportFile"]["sha256"],"nativeGeometryQualityVerified":False,
        "sourceSnappyExecutionReportSha256":prepared["sourceSnappyExecutionReportSha256"],
        "physicalWallTypesVerified":False,"coupledInterfaceCoverageVerified":False,"nativeSolverCompleted":False,
        "finiteNativeFieldsVerified":False,"inputArtifactsImmutable":False,"simulationExecuted":False,"actualCfdRunVerified":False,
        "qualifiedBaseline":False,"numericalBaselineQualified":False,"fullGeometryQualification":False,"geometricApproximationQualified":False,
        "surfaceDistanceBoundsVerified":False,"passageClearanceBoundsVerified":False,"nativeCoordinateUnitsVerified":False,
        "nativeNodeIdentityEquivalent":False,"classroomMetresPerSourceCoordinateUnit":1,"wallResolutionQualified":False,
        "meshConvergenceVerified":False,"timeConvergenceVerified":False,"cavitationSweepExecuted":False,"npsh3Qualified":False,
        "physicalTransientExecuted":False,"mainApplicationWrites":False,
        "claimBoundary":"Actual steady SST/MRF execution on a source-bound approximate classroom mesh. Preliminary numerical baseline acceptance is separate from surface-deviation, wall-resolution, mesh/time convergence, manufacturer performance, NPSH3 and transient validation."}
    (output/"logs").mkdir();current="runtime-identity";bound_rows={};bound_inputs=[]
    try:
        runtime=runtime_identity(output,deadline)
        immutable={"openfoam-preparation.json":verified["reportFile"],
            "runtime-identity.json":geometry.record(output,"runtime-identity.json",base.inventory.MAX_REPORT_BYTES)[1]}
        report["runtimeIdentitySha256"]=immutable["runtime-identity.json"]["sha256"]
        # Pure dictionary inputs remain unchanged; native NCC may rewrite the
        # authored 0 fields, whose actual rewritten bytes are bound before CFD.
        for row in prepared["authoredInputFiles"]:
            if "/0/" not in row["path"]:
                require(geometry.record(output,row["path"])[1]==row,"An authored native dictionary changed before startup.")
                immutable[row["path"]]=row
        for row in prepared["sourceComponentFiles"]:
            require(geometry.record(output,row["path"])[1]==row,"A copied immutable pre-merge mesh snapshot changed before startup.")
            immutable[row["path"]]=row
        for row in prepared["sourceEvidenceFileRecords"]:
            require(geometry.record(output,row["path"])[1]==row,"An actual copied geometry or native source proof changed before baseline startup.")
            immutable[row["path"]]=row
        for row in prepared["authoredPhysicsFileRecords"]:
            require(geometry.record(output,row["path"])[1]==row,"An immutable authored physical BC copy changed before startup.")
            immutable[row["path"]]=row
        for index,arguments in enumerate(commands(output)):
            current=arguments[0]
            if current=="foamRun":bound_rows,bound_inputs=bind_manifest(output)
            stage,text=execute(arguments,output,deadline,runtime,index);report["stages"].append(stage)
            require(stage["exitCode"]==0,"An actual native baseline utility returned exit "+str(stage["exitCode"]))
            geometry.completed_log(text)
            if current=="checkMesh":
                if "Mesh OK." not in text:
                    try:report["nativeQualityDiagnostic"]=quality_diagnostic(output,output/"logs"/stage["log"])
                    except Exception as error:report["nativeQualityDiagnostic"]={"status":"QUALITY_DIAGNOSTIC_CORRECTION_REQUIRED","message":str(error)[:500]}
                measured=geometry.native_mesh(output/"impeller-case",verified["components"],text)
                require(measured["cellCount"]==sum(row["cellCount"]for row in verified["components"])
                    and measured["nodeCount"]==sum(row["nodeCount"]for row in verified["components"]),"Native merge must retain the exact admitted source component counts.")
                report.update(mergedNativeMeshEvidence=measured,nativeGeometryQualityVerified=True,physicalWallTypesVerified=True,coupledInterfaceCoverageVerified=True)
            elif current=="foamRun":
                report["nativeSolverCompleted"]=True
                history=scientific.parse_solver_log(text);require(max(history)==500,"Actual native steady output must reach all 500 authored outer iterations.")
                surface.workspace_size(output)
                primary,fields,scans=final_fields(output,sum(row["cellCount"]for row in verified["components"]))
                report.update(finalNativeFields=primary,finalNativeFieldRecords=fields,finiteNativeFieldEvidence=scans,finiteNativeFieldsVerified=True,simulationExecuted=True)
                require(all(geometry.record(output,row["path"])[1]=={key:row[key]for key in ("path","bytes","sha256")}for row in bound_inputs),
                        "An actual post-NCC input bound before CFD changed during the native solve.")
        require(all(geometry.record(output,path)[1]==row for path,row in {**immutable,**bound_rows}.items()),
                "An immutable source snapshot, geometry identity, input manifest, study or native dictionary changed during execution.")
        preparation.verify_output(output,after_native_ncc=True);preparation.validate_inputs(source)
        require(time.monotonic()<deadline,"The baseline deadline was reached before final byte verification.")
        surface.workspace_size(output)
        report.update(inputArtifactsImmutable=True,actualCfdRunVerified=True,status="SNAPPY_MRF_EXECUTED_ACCEPTANCE_REQUIRED")
    except Exception as error:
        report.update(status="SNAPPY_NATIVE_BASELINE_CORRECTION_REQUIRED",actualCfdRunVerified=False,
            diagnostic={"program":current,"type":type(error).__name__,"message":str(error)[:500]})
    try:
        report["retainedNativeLogRecords"]=[surface.descriptor(path,"logs/"+path.name,MAX_LOG,empty=True)[1]for path in sorted((output/"logs").glob("*.log"))]
        report["workspaceBytes"]=surface.workspace_size(output)
    except Exception as error:
        report.update(status="SNAPPY_NATIVE_RETENTION_CORRECTION_REQUIRED",actualCfdRunVerified=False,
            diagnostic={"program":"retention","type":type(error).__name__,"message":str(error)[:500]})
    report["elapsedSeconds"]=round(time.monotonic()-started,3)
    raw=base.inventory.bounded_json(report);base.write_exclusive(output/"openfoam-execution.json",raw)
    summary={**report,"nativeExecutionReport":{"name":"openfoam-execution.json","bytes":len(raw),"sha256":sha256(raw).hexdigest()}}
    if report["actualCfdRunVerified"]:
        try:
            import reduce_frozen_rotor_snappy_baseline as reduction
            result=reduction.reduce_native_baseline(output);reduced=base.inventory.bounded_json(result)
            base.write_exclusive(output/"native-baseline-reduction.json",reduced)
            require(result.get("qualifiedBaseline") is False and result.get("fullGeometryQualification") is False,
                    "Exploratory numerical acceptance must not promote full pump or geometry qualification.")
            accepted = result.get("actualCfdRunVerified") is True and result.get("numericalBaselineQualified") is True
            summary.update(numericalBaselineQualified=accepted,
                actualCfdRunVerified=report["actualCfdRunVerified"] and result.get("actualCfdRunVerified") is True,
                qualificationScope=result["qualificationScope"],
                status="SNAPPY_EXPLORATORY_NUMERICAL_BASELINE_ACCEPTED" if accepted
                       else "SNAPPY_NUMERICAL_ACCEPTANCE_REQUIRED",
                numericalDiagnostics=result.get("diagnostics",[]),
                nativeReduction={"name":"native-baseline-reduction.json","bytes":len(reduced),"sha256":sha256(reduced).hexdigest()})
        except Exception as error:
            summary.update(status="SNAPPY_NATIVE_ACCEPTANCE_CORRECTION_REQUIRED",qualifiedBaseline=False,numericalBaselineQualified=False,
                diagnostic={"program":"native-reduction","type":type(error).__name__,"message":str(error)[:500]})
    base.write_exclusive(output/"openfoam-acceptance.json",base.inventory.bounded_json(summary))
    print(json.dumps({key:summary[key]for key in ("status","simulationExecuted","actualCfdRunVerified","numericalBaselineQualified","qualifiedBaseline","fullGeometryQualification","mainApplicationWrites")}))
    return 0 if summary["actualCfdRunVerified"] and summary["numericalBaselineQualified"] else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snappy-execution-directory",type=Path,required=True)
    parser.add_argument("--output-directory",type=Path,required=True)
    parser.add_argument("--pressure-reference-absolute-pa",type=float,default=300000)
    args=parser.parse_args()
    try:return run(args.snappy_execution_directory,args.output_directory,pressure_reference_absolute_pa=args.pressure_reference_absolute_pa)
    except Exception as error:
        print(json.dumps({"status":"SNAPPY_BASELINE_INPUT_CORRECTION_REQUIRED","action":str(error)[:500],"actualCfdRunVerified":False,"mainApplicationWrites":False}));return 2


if __name__=="__main__":raise SystemExit(main())
