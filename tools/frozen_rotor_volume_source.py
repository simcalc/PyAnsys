"""Transfer and independently replay a pinned successful HXT volume as data.

A downstream failed job supplies volume provenance only. No Gmsh, native CFD,
baseline qualification, phase preparation, source script or Main is executed.
"""
from __future__ import annotations
import argparse
import codecs
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import sys
import time
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parent))
import reuse_volume_workflow_preflight as preflight
import frozen_rotor_openfoam_case as case
import frozen_rotor_native_reduction as native
import remesh_frozen_rotor as remesh
import extract_frozen_rotor_boundaries as boundary
import inspect_frozen_rotor_mesh as inventory
import verify_tetra_face_incidence as incidence
import frozen_rotor_snappy_phase_source as safety

SCHEMA = "untirta.frozen-rotor-accepted-volume-transfer.v1"
REPORT = "volume-transfer.json"
SELECTED_ROOT = "frozen-rotor-remesh"
SELECTED = {"remesh-report.json","impeller.msh","volute.msh"}
MAX_ARCHIVE_BYTES = preflight.MAX_ARTIFACT_BYTES
MAX_EXPANDED_BYTES = 300*1024**2
MAX_MESH_BYTES = 128*1024**2
MAX_REPORT_BYTES = 128*1024
MAX_ENTRIES = 64
MAX_LINE_BYTES = 4096
LIMITS = {"cpus":1,"memoryBytes":4*1024**3,"maximumSeconds":300,
    "maximumArchiveBytes":MAX_ARCHIVE_BYTES,"maximumExpandedBytes":MAX_EXPANDED_BYTES,
    "maximumCombinedMeshBytes":MAX_MESH_BYTES,"maximumReportBytes":MAX_REPORT_BYTES}
FALSE_FLAGS = ("sourceBaselineQualified","qualifiedBaseline","simulationExecuted","actualCfdRunVerified",
    "meshQualityCheckedBySolver","nativeVolumeQualityQualified","geometricVolumeSelfIntersectionChecked",
    "nativeCaseGenerated","phaseFamilyReferenceQualified","cavitationSweepExecuted",
    "physicalTransientExecuted","restartVerified","npsh3Verified","mainApplicationWrites")
REMESH_ALLOWED = SELECTED | {"remesh-progress.json","gmsh-native-tail.json"}
CASE_ALLOWED = {"openfoam-preparation.json","openfoam-execution.json","openfoam-acceptance.json",
    "runtime-identity.json","geometry-identity.json","native-input-manifest.json","classroom-study.json",
    "native-baseline-reduction.json","extended-native-baseline-recheck.json","native-case-evidence.json",
    "native-case-evidence.zip","remesh-report.json"}
PROGRAMS = ("gmshToFoam","changeDictionary","gmshToFoam","changeDictionary","mergeMeshes",
    "createNonConformalCouples","checkMesh","foamRun")


def require(value,action):
    if not value: raise ValueError(action)


def encoded(value):
    raw=(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode("ascii")
    require(len(raw)<=MAX_REPORT_BYTES,"Volume transfer reports must remain within128KiB.")
    return raw


def digest_stream(stream,limit):
    checksum,total=sha256(),0
    while part:=stream.read(1024**2):
        total+=len(part);require(total<=limit,"Actual transferred bytes exceed their bound.");checksum.update(part)
    return total,checksum.hexdigest()


def descriptor(path,limit):
    path=safety.scope_path(path)
    require(path.is_file() and 0<path.stat().st_size<=limit,"Use a bounded ordinary actual volume data file.")
    with path.open("rb") as stream:size,checksum=digest_stream(stream,limit)
    require(size==path.stat().st_size,"Actual volume data size changed during hashing.")
    return {"path":path.name,"bytes":size,"sha256":checksum}


def member_path(info):
    name=info.filename
    require(name==info.orig_filename and 0<len(name.encode("utf-8"))<=256 and "\\" not in name and "\x00" not in name
            and not name.startswith("/"),"Use exact bounded relative archive paths.")
    directory=name.endswith("/");name=name[:-1] if directory else name;parts=name.split("/")
    require(1<=len(parts)<=3 and all(re.fullmatch(r"[A-Za-z0-9_.-]+",p) and p not in {".",".."}
        and not p.startswith(".") and not p.endswith(".") and p.split(".")[0].upper() not in safety.WINDOWS_DEVICES for p in parts),
        "Archive paths cannot contain traversal, aliases, hidden names or directives.")
    require(parts[0] in {SELECTED_ROOT,"frozen-openfoam-case"} and PurePosixPath(name).as_posix()==name,
            "Only the exact two pinned workflow data roots are admitted.")
    mode=(info.external_attr>>16)&0xffff;kind=stat.S_IFMT(mode)
    require(info.compress_type in {zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED} and not info.flag_bits&0x41,
            "Encrypted or unsupported ZIP compression is prohibited.")
    if directory:
        require(kind in {0,stat.S_IFDIR} and info.file_size==0 and info.CRC==0,"Directory entries cannot carry data or link roles.")
        require(name in {SELECTED_ROOT,"frozen-openfoam-case","frozen-openfoam-case/logs"},"Only fixed workflow data directories are admitted.")
    else:
        require(kind in {0,stat.S_IFREG} and not mode&0o111 and not info.external_attr&0x10,
                "Archive members must be regular nonexecutable data, never links or device nodes.")
        relative="/".join(parts[1:])
        allowed=relative in REMESH_ALLOWED if parts[0]==SELECTED_ROOT else relative in CASE_ALLOWED or relative in {
            f"logs/{index:02d}-{program}.log" for index,program in enumerate(PROGRAMS)}
        require(allowed,"Only fixed authored mesh/report/log/package member names are admitted.")
        limit=MAX_REPORT_BYTES if name.endswith(".json") else 4*1024**2 if name.endswith(".log") else MAX_MESH_BYTES
        require(0<info.file_size<=limit,"An actual archive member exceeds its exact role byte bound.")
    return name,directory


def inspect_members(bundle):
    infos=bundle.infolist();require(0<len(infos)<=MAX_ENTRIES,"Use at most64 actual archive members.")
    names,prefixes,selected,total={}, {}, {},0
    for info in infos:
        name,directory=member_path(info);folded=name.casefold()
        require(folded not in names,"Duplicate archive paths and case aliases are prohibited.")
        for n in range(1,len(name.split("/"))+1):
            prefix="/".join(name.split("/")[:n]);key=prefix.casefold()
            require(key not in prefixes or prefixes[key]==prefix,"Archive parent case aliases are prohibited.");prefixes[key]=prefix
        names[folded]=(name,directory);total+=info.file_size
        require(total<=MAX_EXPANDED_BYTES,"The complete expanded archive exceeds300MiB.")
        if name.startswith(SELECTED_ROOT+"/") and not directory and name.split("/")[-1] in SELECTED:
            selected[name.split("/")[-1]]=info
    for name,_ in names.values():
        for parent in PurePosixPath(name).parents:
            prior=names.get(str(parent).casefold());require(prior is None or prior[1],"A file cannot also be an archive parent directory.")
    require(set(selected)==SELECTED,"The exact complete report and two actual MSH component files are required.")
    return selected,infos,total


def verify_contents(bundle,infos):
    """CRC/read every admitted member, including unextracted data, before mkdir."""
    records={}
    for info in infos:
        name,directory=member_path(info)
        if directory: continue
        checksum,total,first=sha256(),0,b"";payload=bytearray()
        decoder=codecs.getincrementaldecoder("utf-8")("strict") if name.endswith(".log") else None
        with bundle.open(info) as stream:
            while part:=stream.read(1024**2):
                total+=len(part);require(total<=info.file_size,"ZIP content exceeds its declared member size.")
                checksum.update(part);first=(first+part)[:32]
                if name.endswith(".json"):payload.extend(part)
                elif decoder:decoder.decode(part)
                elif name.endswith(".msh"):require(part.isascii(),"Native MSH must contain only ASCII data.")
        if decoder:decoder.decode(b"",final=True)
        require(total==info.file_size,"ZIP content bytes differ from their actual header.")
        if name.endswith(".json"):safety.json_data(bytes(payload))
        elif name.endswith(".zip"):require(first.startswith(b"PK\x03\x04"),"A retained nested package must have an actual ZIP signature.")
        elif name.endswith(".msh"):require(first.startswith(b"$MeshFormat\n2.2 0 8\n"),"Use actual linear ASCII MSH2.2 data.")
        records[name]={"path":name,"bytes":total,"sha256":checksum.hexdigest()}
    return records


def verify_report(report,admitted):
    require(report.get("schemaVersion")==remesh.SCHEMA and report.get("status")=="VOLUME_MESH_GENERATED"
        and report.get("githubRunId")==admitted["sourceRunId"] and report.get("githubSourceCommit")==preflight.SOURCE_COMMIT
        and report.get("resourceProfile")=="extended_free_hxt" and report.get("volumeMeshGenerated") is True
        and report.get("originalMeshesImmutable") is True,"The actual source mesher report must bind the admitted successful pinned step.")
    require(case.validate_remesh_resource_profile(report)=="extended_free_hxt","Replay the exact enforced source resource profile.")
    identity=native.geometry_identity(report)
    before=report.get("postAttemptSourceVerification")
    require(isinstance(before,list) and len(before)==2 and {row.get("name") for row in before}==set(inventory.MESH_NAMES),
            "The actual source mesher must have independently rehashed both immutable original inputs.")
    for row in before:
        expected=inventory.FILES[row["name"]][1]
        require(row.get("beforeSha256")==expected and row.get("afterSha256")==expected and row.get("immutableVerified") is True,
                "Both source before/after hashes must match their immutable official pins.")
    require(type(report.get("elapsedSeconds")) in (int,float) and math.isfinite(report["elapsedSeconds"])
            and 0<=report["elapsedSeconds"]<2400,"The actual source successful mesh must satisfy its native deadline.")
    return identity


def coordinate_replay(path,zones,coordinates):
    expected={node:tuple(float(v) for v in coordinates[node]) for zone in zones for face in zone["triangles"] for node in face}
    seen=set();sections=0
    with path.open("rb") as stream:
        for raw in stream:
            require(len(raw)<=MAX_LINE_BYTES,"Every native coordinate row must remain bounded.")
            if raw.strip()!=b"$Nodes":continue
            sections+=1;count=stream.readline(MAX_LINE_BYTES+1)
            require(sections==1 and re.fullmatch(rb"[1-9]\d*\s*",count) and int(count)<=case.MAX_COMPONENT_NODES,
                    "Replay one complete bounded native node section.")
            for _ in range(int(count)):
                line=stream.readline(MAX_LINE_BYTES+1);parts=line.split()
                require(len(line)<=MAX_LINE_BYTES and len(parts)==4,"Every actual node must retain its ID and three coordinates.")
                identifier=int(parts[0]);xyz=tuple(float(v) for v in parts[1:])
                if identifier in expected:
                    require(identifier not in seen and xyz==expected[identifier],"Every source boundary global ID must retain exact float64 coordinates.")
                    seen.add(identifier)
            require(stream.readline(MAX_LINE_BYTES+1).strip()==b"$EndNodes","The native coordinate section must end completely.")
    require(sections==1 and seen==set(expected),"Replay every immutable original boundary node, not a sampled subset.")
    return {"nativeBoundaryNodeCount":len(seen),"everyNativeBoundaryCoordinateChecked":True,
        "maximumCoordinateDifferenceNativeUnits":0,"nativeGlobalNodeIdsPreserved":True}


def replay_geometry(output,cache,report):
    meshes,report_sha=case.validate_inputs(output)
    results=[];source_rows=[]
    for name in inventory.MESH_NAMES:
        source=inventory.download_mesh(cache,name);before=inventory.verify_file(source,name)
        model=boundary.read_native_mesh(source);zones,_,_=remesh.source_triangles(model)
        component=name.split(".")[0];target=output/(component+".msh")
        original=descriptor(target,MAX_MESH_BYTES)
        topology=incidence.verify_msh(target)
        winding=remesh.verify_published_boundary(target,zones)
        positions=coordinate_replay(target,zones,model["coordinates"])
        after=inventory.verify_file(source,name)
        require(before==after and descriptor(target,MAX_MESH_BYTES)==original,"Source or transferred volume changed during exact geometry replay.")
        results.append({"component":component,"meshFile":original,"actualNodeCount":meshes[component]["nodeCount"],
            "actualCellCount":meshes[component]["cellCount"],"exactTetraFaceIncidence":topology,
            "nativeBoundaryWindingAndZoneReplay":winding,"nativeCoordinateReplay":positions})
        source_rows.append({**before,"beforeSha256":before["sha256"],"afterSha256":after["sha256"],"immutableVerified":True})
        del model,zones
    return {"remeshReportSha256":report_sha,"components":results,"originalSourceRecords":source_rows}


def enforce_limits():
    expected={"GITHUB_ACTIONS":"true","GITHUB_REPOSITORY":preflight.REPOSITORY,"GITHUB_EVENT_NAME":"workflow_dispatch",
              "RUNNER_OS":"Linux","RUNNER_ENVIRONMENT":"github-hosted","PYANSYS_PUBLIC_REPOSITORY_VISIBILITY":"public"}
    require(sys.platform=="linux" and all(os.environ.get(k)==v for k,v in expected.items()),
            "Use only the verified manual public standard Linux worker for independent volume replay.")
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(LIMITS["memoryBytes"],LIMITS["memoryBytes"]))
    require(resource.getrlimit(resource.RLIMIT_AS)==(LIMITS["memoryBytes"],LIMITS["memoryBytes"]),"Actually enforce the declared4GiB address-space bound.")
    for key in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS"):os.environ[key]="1"
    def expired(signum,frame):raise ValueError("The bounded300-second volume replay timed out; no accepted transfer is published.")
    signal.signal(signal.SIGALRM,expired);signal.alarm(LIMITS["maximumSeconds"])


def cleanup_owned(directory):
    directory=safety.scope_path(directory)
    for path in sorted(directory.rglob("*"),key=lambda p:len(p.parts),reverse=True):
        safety.scope_path(path);require(path.is_file() or path.is_dir(),"Owned cleanup allows regular data paths only.")
        path.rmdir() if path.is_dir() else path.unlink()
    directory.rmdir()


def extract(archive_path,admission_json,output_directory):
    archive=safety.scope_path(archive_path);admission_path=safety.scope_path(admission_json)
    output=safety.scope_path(output_directory,fresh=True)
    require(output.parent.is_dir(),"Use one fresh output inside an existing regular runner directory.")
    cache=safety.scope_path(output.parent/(output.name+"-native-inputs"),fresh=True)
    admission_raw=safety.read_regular(admission_path,MAX_REPORT_BYTES)
    admission_record={"path":admission_path.name,"bytes":len(admission_raw),"sha256":sha256(admission_raw).hexdigest()}
    admitted=preflight.validate_admission(safety.json_data(admission_raw))
    require(descriptor(admission_path,MAX_REPORT_BYTES)==admission_record,"The admitted metadata changed while its actual bytes were read.")
    require(archive.suffix==".zip" and admission_path.suffix==".json"
            and re.fullmatch(r"[A-Za-z0-9_.-]+",archive.name) and re.fullmatch(r"[A-Za-z0-9_.-]+",admission_path.name),
            "Use ordinary exact ZIP/admission filenames without embedded private identifiers or directives.")
    require(archive.is_file() and 0<archive.stat().st_size<=MAX_ARCHIVE_BYTES,"Use the bounded actual raw GitHub artifact ZIP.")
    before=descriptor(archive,MAX_ARCHIVE_BYTES)
    require(before["bytes"]==admitted["artifactBytes"] and before["sha256"]==admitted["artifactSha256"],
            "Raw ZIP size and SHA256 must match the actual GitHub artifact before persistence.")
    started=time.monotonic();enforce_limits();created=False;cache_created=False
    try:
        with zipfile.ZipFile(archive) as bundle:
            selected,infos,total=inspect_members(bundle);contents=verify_contents(bundle,infos)
            raw_report=bundle.read(selected["remesh-report.json"])
            report=safety.json_data(raw_report);identity=verify_report(report,admitted)
            declared={row["generatedFile"]["name"]:row["generatedFile"] for row in report["meshes"]}
            require(set(declared)=={"impeller.msh","volute.msh"} and sum(row["bytes"] for row in declared.values())<=MAX_MESH_BYTES,
                    "Both actual source volume files must satisfy their combined128MiB bound.")
            for name,row in declared.items():
                actual=contents[SELECTED_ROOT+"/"+name]
                require(row.get("bytes")==actual["bytes"] and row.get("sha256")==actual["sha256"],
                        "Every selected source mesh must match its bound report before creating output.")
            require(descriptor(archive,MAX_ARCHIVE_BYTES)==before,"Source ZIP changed during complete CRC/content review.")
            output.mkdir();created=True;cache.mkdir();cache_created=True
            records=[]
            for name in sorted(SELECTED):
                with bundle.open(selected[name]) as stream,(output/name).open("xb") as target:
                    while part:=stream.read(1024**2):target.write(part)
                row=descriptor(output/name,MAX_REPORT_BYTES if name.endswith(".json") else MAX_MESH_BYTES)
                require(row["bytes"]==contents[SELECTED_ROOT+"/"+name]["bytes"] and row["sha256"]==contents[SELECTED_ROOT+"/"+name]["sha256"],
                        "Transferred file bytes differ from the independently checked source archive.")
                records.append(row)
        geometry=replay_geometry(output,cache,report)
        require(descriptor(archive,MAX_ARCHIVE_BYTES)==before and descriptor(admission_path,MAX_REPORT_BYTES)==admission_record
                and all(descriptor(output/row["path"],MAX_MESH_BYTES)==row for row in records),
                "Every source and transferred byte must remain unchanged after full current geometry replay.")
        elapsed=time.monotonic()-started
        require(elapsed<LIMITS["maximumSeconds"],"Completed volume replay must remain inside its actual deadline.")
        result={"schemaVersion":SCHEMA,"status":"VOLUME_TRANSFERRED_FRESH_BASELINE_REQUIRED","sourceAdmission":admitted,
            "sourceArchive":before,"sourceAdmissionFileSha256":admission_record["sha256"],
            "completeArchiveMemberCount":len(infos),"completeExpandedBytesReviewed":total,"transferredFileRecords":records,
            "sourceGeometryIdentitySha256":sha256(native.encoded(identity)).hexdigest(),**geometry,"limits":LIMITS,
            "elapsedSeconds":elapsed,"volumeAcceptanceReplayed":True,"sourceArchiveImmutable":True,
            "transferredMeshesImmutable":True,"originalMeshesImmutable":True,"coordinateUnits":"UNVERIFIED",
            "classroomMetresPerSourceCoordinateUnit":1,"nativeCoordinateUnitsVerified":False,
            **dict.fromkeys(FALSE_FLAGS,False),
            "claimBoundary":"Exact actual source volume, oriented exterior, native IDs/coordinates and tetra incidence replay only. No fresh OpenFOAM quality/solver/baseline/phase/restart acceptance is inherited."}
        with (output/REPORT).open("xb") as stream:stream.write(encoded(result))
        return result
    except BaseException:
        if created:cleanup_owned(output)
        if cache_created:cleanup_owned(cache)
        raise
    finally:
        if sys.platform=="linux":signal.alarm(0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive",type=Path,required=True);parser.add_argument("--admission-json",type=Path,required=True)
    parser.add_argument("--output-directory",type=Path,required=True);args=parser.parse_args()
    try:
        result=extract(args.archive,args.admission_json,args.output_directory)
        print(json.dumps({"status":result["status"],"volumeAcceptanceReplayed":True,
            "totalActualCells":sum(row["actualCellCount"] for row in result["components"]),
            "actualCfdRunVerified":False,"sourceBaselineQualified":False,"mainApplicationWrites":False},sort_keys=True));return 0
    except (ValueError,OSError,MemoryError,zipfile.BadZipFile) as error:
        print(json.dumps({"status":"VOLUME_REPLAY_REQUIRED","volumeAcceptanceReplayed":False,
            "reasonCode":"CFD_EXACT_VOLUME_SOURCE_REPLAY_REQUIRED","action":str(error)[:500],
            "actualCfdRunVerified":False,"sourceBaselineQualified":False,"mainApplicationWrites":False},sort_keys=True));return 2


if __name__=="__main__":raise SystemExit(main())
