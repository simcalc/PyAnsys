"""One bounded native force diagnostic on immutable UNQUALIFIED time500 data.

foamPostProcess constructs the stored single-phase solver/model but never runs
flow iterations. Its exit0/End is insufficient: exact finite force tables,
package identities and unchanged source/input bytes must also be verified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
import teaching_pump_torque_probe_source as source
import run_teaching_pump_baseline as fields

base,safety,native = fields.base,fields.safety,fields.native
require,encoded = source.require,source.encoded
SCHEMA = "untirta.teaching-pump-native-torque-diagnostic.v1"
RUNTIME_SCHEMA = "untirta.teaching-pump-torque-runtime-identity.v1"
REPORT,RUNTIME,MANIFEST = "torque-probe-execution.json","torque-probe-runtime-identity.json","torque-probe-input-manifest.json"
DICT = "impeller-case/system/teachingTorqueProbeDict"
LIMITS = {"maximumSeconds":300,"maximumCpus":1,"maximumMemoryBytes":4*1024**3,"maximumWorkspaceBytes":1024**3,
    "maximumFileBytes":128*1024**2,"maximumLogBytes":8*1024**2,"maximumReportBytes":128*1024,"maximumPaths":2048,"maximumPathDepth":12}
OBJECTS = {"torqueBlade":("blade",),"torqueHub":("impeller-hub",),"torqueShroud":("impeller-shroud",),
    "torqueRotor":("blade","impeller-hub","impeller-shroud")}
LIBRARIES = ("libforces.so","libincompressibleFluid.so")
FALSE_FLAGS = (*source.FALSE_FLAGS,"qualifiedBaseline","preliminaryBaselineQualified","phaseFamilyReferenceQualified",
    "cavitationModelExecuted","cavitationSweepExecuted","meshConvergenceVerified","timeStepConvergenceVerified",
    "ALEVerified","wallFluxVerified","physicalEnergyBalanceVerified","efficiencyQualified")
FLOAT = fields.scientific.FLOAT
SOURCE_BASE = "https://github.com/OpenFOAM/OpenFOAM-12/blob/"+safety.SOURCE_COMMIT+"/"


def dictionary():
    """Flat -dict contents replace, rather than augment, solver functionObjects."""
    text="FoamFile {format ascii; class dictionary; object teachingTorqueProbeDict;}\n"
    for name,patches in OBJECTS.items():
        text += name+"\n{\n type forces; libs (\"libforces.so\"); log true;\n patches ("+" ".join(patches)+");\n"
        text += " p p; U U; rho rhoInf; rhoInf 998.2; pRef 0;\n CofR (0 0 0); directForceDensity false; porosity false;\n}\n"
    return text.encode("ascii")


def command(output):
    root=Path(output).absolute()
    return ["foamPostProcess","-solver","incompressibleFluid","-time","500","-dict",str(root/DICT),"-case",str(root/"impeller-case")]


def record(root,name,limit=None):
    source.shared.canonical_path(name)
    return safety.descriptor(Path(root)/name,name,limit or LIMITS["maximumFileBytes"])


def workspace(directory):
    root=base.regular_directory(directory);total,count=0,0
    for path in root.rglob("*"):
        count+=1;require(count<=LIMITS["maximumPaths"] and len(path.relative_to(root).parts)<=LIMITS["maximumPathDepth"],"Bound the diagnostic file/depth inventory.")
        require(not path.is_symlink() and not getattr(path,"is_junction",lambda:False)() and (path.is_file() or path.is_dir()),"The diagnostic workspace prohibits links/devices.")
        if path.is_file():
            size=path.stat().st_size;total+=size
            require(size<=LIMITS["maximumFileBytes"] and total<=LIMITS["maximumWorkspaceBytes"],"Bound every actual diagnostic file and workspace.")
    return total


def deadline_check(deadline,stage):
    require(time.monotonic()<deadline,"The300s diagnostic deadline expired: "+stage)


class Deadline:
    def __init__(self,deadline):self.deadline,self.saved,self.started=deadline,None,None
    def __enter__(self):
        deadline_check(self.deadline,"entry")
        if hasattr(signal,"setitimer") and threading.current_thread() is threading.main_thread():
            self.started=time.monotonic();old=signal.getitimer(signal.ITIMER_REAL)
            self.saved=(signal.getsignal(signal.SIGALRM),old)
            remaining=self.deadline-self.started
            if old[0]>0:remaining=min(remaining,old[0])
            def expired(*_):raise TimeoutError("The owned diagnostic evidence deadline expired.")
            signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,max(remaining,1e-6))
        return self
    def __exit__(self,*_):
        if self.saved is not None:
            handler,old=self.saved;signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,handler)
            left=max(old[0]-(time.monotonic()-self.started),1e-6) if old[0]>0 else 0
            signal.setitimer(signal.ITIMER_REAL,left,old[1]);self.saved=None


def child_limits():
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(LIMITS["maximumMemoryBytes"],LIMITS["maximumMemoryBytes"]))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0));resource.setrlimit(resource.RLIMIT_FSIZE,(LIMITS["maximumFileBytes"],LIMITS["maximumFileBytes"]))
    allowed=os.sched_getaffinity(0);require(allowed,"Bind one currently available CPU for the owned utility only.")
    os.sched_setaffinity(0,{min(allowed)})


def runtime_identity(deadline):
    def query(args):
        deadline_check(deadline,"package query")
        reply=subprocess.run(args,env=native.child_environment(),capture_output=True,text=True,timeout=min(10,deadline-time.monotonic()),check=True)
        require(len(reply.stdout)<=8192 and len(reply.stderr)<=8192,"Bound package query output.");return reply.stdout.strip()
    require(query(["dpkg-query","--show","--showformat=${Version}","openfoam12"])=="20250206","The exact checksum-pinned Foundation12 package is required.")
    executable=shutil.which("foamPostProcess");require(executable,"The package-owned foamPostProcess utility is required.")
    library_root=Path(os.environ.get("FOAM_LIBBIN","/invalid"))
    wanted=[("foamPostProcess",Path(executable)),*[(name,library_root/name) for name in LIBRARIES]]
    rows=[]
    for name,path in wanted:
        resolved=path.resolve();require(resolved.is_relative_to(Path("/opt/openfoam12")) and resolved.name==name,
            "Bind only the exact official utility/force/selected-solver library paths.")
        ownership=query(["dpkg-query","--search",str(resolved)])
        require(ownership=="openfoam12: "+str(resolved),"Every executed utility/loaded diagnostic library must belong to openfoam12.")
        _,row=safety.descriptor(resolved);rows.append({**row,"path":str(resolved),"role":name,"packageOwnership":ownership})
    deadline_check(deadline,"runtime binding")
    return {"schemaVersion":RUNTIME_SCHEMA,"openFoamFoundationVersion":12,"packageVersion":"20250206",
        "officialDebSha256":safety.PACKAGE_SHA,"expectedSourceCommit":safety.SOURCE_COMMIT,
        "nativeObjects":rows,"mainApplicationWrites":False}


def verify_runtime(runtime):
    require(runtime.get("schemaVersion")==RUNTIME_SCHEMA and runtime.get("packageVersion")=="20250206"
        and runtime.get("officialDebSha256")==safety.PACKAGE_SHA and runtime.get("expectedSourceCommit")==safety.SOURCE_COMMIT,
        "Keep the actual diagnostic package identity bound.")
    rows=runtime.get("nativeObjects")
    require(isinstance(rows,list) and [r.get("role") for r in rows]==["foamPostProcess",*LIBRARIES],"Bind all three current diagnostic native objects.")
    for row in rows:
        path=Path(row["path"]);require(path.resolve().is_relative_to(Path("/opt/openfoam12")) and path.name==row["role"]
            and row["packageOwnership"]=="openfoam12: "+str(path),"Use only the hash-bound official executable and libraries.")
        _,actual=safety.descriptor(path);require((actual["bytes"],actual["sha256"])==(row["bytes"],row["sha256"]),"Actual diagnostic utility/library bytes changed after binding.")


def execute(arguments,output,deadline,runtime):
    require(arguments==command(output),"Execute exactly one fixed foamPostProcess/solver/time500 diagnostic command.")
    verify_runtime(runtime);deadline_check(deadline,"utility startup");workspace(output)
    path=Path(output)/"logs/00-foamPostProcess.log";require(not path.exists(),"The diagnostic native log must be fresh.")
    started=time.monotonic();total=0;process=None;selector=selectors.DefaultSelector();next_progress=started+30
    try:
        with path.open("xb") as destination:
            process=subprocess.Popen([runtime["nativeObjects"][0]["path"],*arguments[1:]],cwd=arguments[-1],
                env=native.child_environment(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                start_new_session=True,preexec_fn=child_limits,bufsize=0)
            selector.register(process.stdout,selectors.EVENT_READ)
            while selector.get_map():
                deadline_check(deadline,"utility output");workspace(output)
                if time.monotonic()>=next_progress:
                    print(json.dumps({"status":"NativeTorquePostprocessingRunning","elapsedSeconds":round(time.monotonic()-started,1),"simulationExecuted":False}),flush=True)
                    next_progress=time.monotonic()+30
                for key,_ in selector.select(timeout=.25):
                    chunk=os.read(key.fileobj.fileno(),4096)
                    if not chunk:selector.unregister(key.fileobj);continue
                    remaining=LIMITS["maximumLogBytes"]-total;destination.write(chunk[:remaining]);destination.flush();total+=min(len(chunk),remaining)
                    require(len(chunk)<=remaining,"The native diagnostic log exceeded8MiB.")
            code=process.wait(timeout=3);workspace(output);deadline_check(deadline,"utility completion")
    finally:
        if process is not None:native.stop_owned(process);process.stdout.close()
        selector.close()
    raw,row=record(output,"logs/00-foamPostProcess.log",LIMITS["maximumLogBytes"])
    return {"program":"foamPostProcess","exitCode":code,"elapsedSeconds":round(time.monotonic()-started,3),"logFile":row},raw.decode("ascii")


def parse_log(text):
    require(re.findall(r"(?m)^Build\s*:\s*(\S+)\s*$",text)==["12-"+safety.SOURCE_COMMIT[:12]]
        and re.findall(r"(?m)^Time = (\S+)\s*$",text) in (["500"],["500s"]) and len(re.findall(r"(?m)^End\s*$",text))==1,
        "Require the exact native source build and one stored500 time, not a flow iteration trace.")
    require(not re.search(r"Warning|FOAM FATAL|Floating point exception|Segmentation fault|Solving for|SIMPLE iteration|PIMPLE iteration",text,re.I),
        "Native warnings/IO failures/flow iterations invalidate this postprocessing-only diagnostic.")
    require("Selecting solver incompressibleFluid" in text and "Selecting RAS turbulence model kOmegaSST" in text,
        "The actual selected single-phase solver and SST model must be present in the native diagnostic trace.")
    for name in OBJECTS:
        require(len(re.findall(r"(?m)^forces "+name+r" write:\s*$",text))==1,"Require one real native write for each declared rotor force group.")
    return {"storedTime":500,"solver":"incompressibleFluid","momentumModel":"kOmegaSST","forceObjects":list(OBJECTS),"flowIterationsExecuted":False}


def parse_forces(raw):
    """Pinned forces overrides CoR data writer: four vectors, no CoR data column."""
    require(0<len(raw)<=128*1024,"Bound one complete native force table.");text=raw.decode("ascii")
    require(len(re.findall(r"(?m)^#\s*Forces\s*$",text))==1,"Require native Forces header.")
    centre=re.findall(r"(?m)^#\s*CofR\s*:\s*\(([^()]*)\)\s*$",text)
    zero=centre[0].split() if len(centre)==1 else []
    require(len(zero)==3 and all(re.fullmatch(FLOAT,v) and math.isfinite(float(v)) and float(v)==0 for v in zero),
        "Bind native rotor moments about the exact source origin, including its scientific zero spelling.")
    require(len(re.findall(r"(?m)^#\s*Time\s+forces\(pressure viscous\)\s+moments\(pressure viscous\)\s*$",text))==1,
        "Require the exact pressure/viscous-only native force+moment columns.")
    rows=[line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    vector=r"\(\s*("+FLOAT+r")\s+("+FLOAT+r")\s+("+FLOAT+r")\s*\)"
    shape=r"("+FLOAT+r")\s+\(\s*"+vector+r"\s*"+vector+r"\s*\)\s+\(\s*"+vector+r"\s*"+vector+r"\s*\)"
    match=re.fullmatch(shape,rows[0]) if len(rows)==1 else None
    require(match,"Exactly one complete native500 pressure/viscous force+moment row is required.")
    values=[float(v) for v in match.groups()];require(all(math.isfinite(v) for v in values) and values[0]==500,"Native force/moment values must be finite at500.")
    return {"storedTime":500,"pressureForceN":values[1:4],"viscousForceN":values[4:7],
        "pressureMomentNm":values[7:10],"viscousMomentNm":values[10:13],"originM":[0,0,0]}


def reduce_forces(tables):
    require(set(tables)==set(OBJECTS),"Require all four independent native rotor-force records.")
    for key in ("pressureForceN","viscousForceN","pressureMomentNm","viscousMomentNm"):
        for axis in range(3):
            summed=math.fsum(tables[name][key][axis] for name in ("torqueBlade","torqueHub","torqueShroud"))
            require(math.isclose(summed,tables["torqueRotor"][key][axis],rel_tol=5e-10,abs_tol=1e-8),"Actual component and combined rotor integrals must agree at declared roundoff tolerance.")
    rotor=tables["torqueRotor"];omega=2*math.pi*1450/60
    moment=rotor["pressureMomentNm"][2]+rotor["viscousMomentNm"][2];power=-omega*moment
    require(math.isfinite(power),"A finite signed diagnostic power is required.")
    return {"speedRpm":1450,"angularVelocityRadS":omega,"rotationAxis":[0,0,1],"fluidOnRotorTorqueNm":moment,
        "signedRotorToFluidPowerW":power,"signConvention":"Fluid-on-body native moment; rotor-to-fluid power is minus omega dot moment; no absolute value or sign clipping.",
        "equationStages":{"input":{"pressureMomentNm":rotor["pressureMomentNm"],"viscousMomentNm":rotor["viscousMomentNm"],"speedRpm":1450},
            "equation":"P_rotor_to_fluid = -omega_z * (M_pressure,z + M_viscous,z); omega_z = 2*pi*N/60",
            "substitution":{"omegaRadS":omega,"pressureMzNm":rotor["pressureMomentNm"][2],"viscousMzNm":rotor["viscousMomentNm"][2]},
            "result":{"signedPowerW":power}},"componentSumVerified":True,"physicalEnergyBalanceVerified":False,"efficiencyQualified":False}


def hydraulic_diagnostic(source_root):
    """Stored endpoint sampling only; no source or flow result is qualified."""
    records=[];cuts=[]
    for label,patch in (("Inlet","inlet"),("Outlet","mass-flow-inlet-11")):
        name="impeller-case/postProcessing/weighted"+label+"/0/surfaceFieldValue.dat"
        raw,row=record(source_root,name,8*1024**2);records.append(row)
        table=fields.scientific.parse_table(raw.decode("ascii"),("average(p)","average(totalPressurePa)","average(U2)"),patch)
        require(500 in table and max(table)==500,"Actual matching endpoint total-pressure samples are required.");cuts.append(table[500])
    delta=cuts[1][1]-cuts[0][1];rho,mass=998.2,90.;Q=mass/rho;power=Q*delta
    return {"nativeTableRecords":records,"sourceIteration":500,"totalPressureRisePa":delta,"volumetricFlowM3S":Q,
        "signedHydraulicPowerW":power,"sourceHeadM":delta/(rho*9.81),"headDatum":"Declared gravity-neglected zero elevation correction",
        "equationStages":{"input":{"totalPressureInletPa":cuts[0][1],"totalPressureOutletPa":cuts[1][1],"massFlowKgS":mass,"densityKgM3":rho},
            "equation":"P_h = Q * (p_total,out - p_total,in); Q = massFlow / rho",
            "substitution":{"Q_M3S":Q,"totalPressureRisePa":delta},"result":{"signedHydraulicPowerW":power}},
        "numericalBaselineQualified":False,"physicalEnergyBalanceVerified":False}


def copied_inputs(source_root):
    rows=source.inventory(source_root)
    return [r for r in rows if r["path"].startswith(("impeller-case/0/","impeller-case/500/"))
        or r["path"] in {"impeller-case/"+n for n in source.INPUTS if not n.startswith("0/")}
        or r["path"] in {"impeller-case/constant/polyMesh/"+n for n in source.PRIMITIVES}
        or r["path"]=="impeller-case/constant/fvMesh/polyFaces"]


def run(source_directory,output_directory):
    started=time.monotonic();deadline=started+LIMITS["maximumSeconds"];safety.context_guard()
    source_root=source.paths.scope_path(source_directory);output=source.paths.scope_path(output_directory,fresh=True)
    require(not output.is_relative_to(source_root) and not source_root.is_relative_to(output),"Use a separate fresh diagnostic case outside the immutable source tree.")
    report={"schemaVersion":SCHEMA,"status":"TORQUE_DIAGNOSTIC_REQUIRED","createdAt":datetime.now(timezone.utc).isoformat(),
        "githubRepository":"simcalc/PyAnsys","githubRunId":os.environ.get("GITHUB_RUN_ID"),"githubSourceCommit":os.environ.get("GITHUB_SHA"),
        "sourceRunId":source.RUN_ID,"sourceCommit":source.HEAD,"sourceArtifactId":source.ARTIFACT_ID,"sourceArchiveSha256":source.ARTIFACT_SHA,
        "sourceStoredIteration":500,"storedIterationIsPhysicalTime":False,
        "limits":LIMITS,"stages":[],"nativePostprocessCompleted":False,"inputArtifactsImmutable":False,
        "sourceArtifactsImmutable":False,**{k:False for k in FALSE_FLAGS},
        "claimBoundary":"A native diagnostic on known unqualified, nonstationary stored500 fields; no converged flow, H0, NPSH, efficiency, transient, restart or physical acceptance."}
    try:
        with Deadline(deadline):
            transfer_root=source_root.parent
            admission_raw,admission_row=record(transfer_root,source.ADMISSION,source.MAX_JSON)
            admission=source.admission(source.json_data(admission_raw))
            _,archive=record(transfer_root,source.ARCHIVE)
            require((archive["bytes"],archive["sha256"])==(source.ARTIFACT_BYTES,source.ARTIFACT_SHA),"Keep the fixed original whole archive immutable before utility execution.")
            transfer_raw,transfer_row=record(transfer_root,source.REPORT,source.MAX_JSON)
            transfer=source.json_data(transfer_raw)
            checked=source.verify_tree(source_root)
            require(source.archive_tree_binding(transfer_root/source.ARCHIVE,checked["files"])==archive,
                "Independently bind every current source member to the original fixed ZIP.")
            require(transfer.get("admission")==admission and transfer.get("sourceFiles")==checked["files"]
                and transfer.get("archiveFile")==archive and transfer.get("byteExactTransferVerified") is True,
                "Require the fixed full-CRC/extracted source current-byte inventory before copying native data.")
            report["sourceEvidence"]=checked;report["sourceAdmission"]=admission
            report["sourceSidecarRecords"]=[admission_row,transfer_row]
            output.mkdir();(output/"logs").mkdir();inputs=copied_inputs(source_root)
            for row in inputs:
                raw,actual=record(source_root,row["path"]);require(actual==row,"Copied diagnostic inputs must match the original current byte rows.")
                target=output/row["path"];target.parent.mkdir(parents=True,exist_ok=True);base.write_exclusive(target,raw)
            base.write_exclusive(output/DICT,dictionary());runtime=runtime_identity(deadline)
            base.write_exclusive(output/RUNTIME,encoded(runtime));input_rows=[*inputs,record(output,DICT)[1]]
            manifest={"schemaVersion":"untirta.teaching-pump-torque-diagnostic-inputs.v1","sourceAdmission":admission,
                "sourceFiles":checked["files"],"copiedNativeInputs":input_rows,"runtimeIdentityFile":record(output,RUNTIME)[1],
                "nativeCommandArguments":command(output),"mainApplicationWrites":False}
            base.write_exclusive(output/MANIFEST,encoded(manifest));report["inputManifestFile"]=record(output,MANIFEST)[1]
            report["runtimeIdentityFile"]=record(output,RUNTIME)[1];report["nativeCommandArguments"]=command(output)
            stage,text=execute(command(output),output,deadline,runtime);report["stages"].append(stage)
            require(stage["exitCode"]==0,"The actual foamPostProcess utility returned nonzero; retain its diagnostic log.")
            report["nativeTraceEvidence"]=parse_log(text);tables={};records=[]
            for name in OBJECTS:
                path=f"impeller-case/postProcessing/{name}/500/forces.dat";raw,row=record(output,path,128*1024)
                tables[name]=parse_forces(raw);records.append(row)
            report["forceTableRecords"]=records;report["nativeForces"]=tables;report["rotorPowerDiagnostic"]=reduce_forces(tables)
            report["hydraulicPowerDiagnostic"]=hydraulic_diagnostic(source_root)
            require(all(record(output,r["path"])[1]==r for r in input_rows),"Every original field/dictionary/mesh byte must remain unchanged during the utility.")
            require(all(record(output,r["path"])[1]==r for r in (report["inputManifestFile"],report["runtimeIdentityFile"])),
                "The diagnostic runtime/input-manifest metadata changed after binding.")
            allowed={r["path"] for r in input_rows+records}|{RUNTIME,MANIFEST,"logs/00-foamPostProcess.log"}
            require(all(path.relative_to(output).as_posix() in allowed for path in output.rglob("*") if path.is_file()),"The postprocessor wrote an unrecognized data role.")
            require(source.inventory(source_root)==checked["files"] and record(transfer_root,source.ARCHIVE)[1]==archive,
                "The separate original source tree/archive changed during diagnostics.")
            require(all(record(transfer_root,r["path"],source.MAX_JSON)[1]==r for r in report["sourceSidecarRecords"]),
                "Original diagnostic admission/transfer proof bytes changed during the utility.")
            verify_runtime(runtime);workspace(output);deadline_check(deadline,"final current-byte replay")
            report.update(status="NATIVE_TORQUE_DIAGNOSTIC_COMPLETE_UNQUALIFIED",nativePostprocessCompleted=True,
                inputArtifactsImmutable=True,sourceArtifactsImmutable=True)
    except (ValueError,OSError,TimeoutError,subprocess.SubprocessError,UnicodeError) as error:
        report.update(status="NATIVE_TORQUE_DIAGNOSTIC_REJECTED",correctiveAction=str(error)[:800])
    report["elapsedSeconds"]=round(time.monotonic()-started,3)
    if output.exists():
        raw=encoded(report);require(len(raw)<=LIMITS["maximumReportBytes"],"The native diagnostic report exceeded128KiB.")
        base.write_exclusive(output/REPORT,raw)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--source-directory",required=True);parser.add_argument("--output-directory",required=True)
    args=parser.parse_args()
    result=run(args.source_directory,args.output_directory)
    print(json.dumps({k:result[k] for k in ("status","nativePostprocessCompleted","actualCfdRunVerified","numericalBaselineQualified","mainApplicationWrites")}
        | ({"correctiveAction":result["correctiveAction"]} if "correctiveAction" in result else {})))
    return 0 if result["nativePostprocessCompleted"] else 2


if __name__=="__main__":raise SystemExit(main())
