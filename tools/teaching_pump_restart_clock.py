"""Current-byte, seeded Foundation-12 restart clock facts; no solver admission.

Complete checkpoint inventory and a clock match do not establish restart-state,
ALE, numerical or physical equivalence. Native global time is never rebased.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import re

SCHEMA = "untirta.teaching-pump-restart-clock-facts.v1"
INPUT_SCHEMA = "untirta.teaching-pump-restart-clock-input.v1"
SOURCE_COMMIT = "86e126a7bc4dbfaed949bbc58c9f0fdd67acc059"
BASE = "https://github.com/OpenFOAM/OpenFOAM-12/blob/"+SOURCE_COMMIT+"/"
SOURCES = {"timeStateWriter": BASE+"src/OpenFOAM/db/Time/TimeIO.C",
    "seedAndIncrement": BASE+"src/OpenFOAM/db/Time/Time.C",
    "adaptivePrint": BASE+"applications/solvers/foamRun/setDeltaT.C",
    "outerLoop": BASE+"applications/solvers/foamRun/foamRun.C"}
MAX_FILE, MAX_TOTAL, MAX_LOG, MAX_JSON = 128*1024**2, 1024**3, 8*1024**2, 128*1024
MAX_FILES, MAX_STEPS = 64, 100000
FLOAT = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
TIME_NAME = r"(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?"
END7 = {"p", "p_rgh", "U", "alpha.water", "k", "omega", "nut"}
REQUIRED_STATE = END7 | {"phi", "Uf", "rAU", "alphaPhi.water", "meshPhi",
    "polyMesh/points", "fvMesh/polyFaces", "uniform/time"}
ROLES = {n: ("volVectorField" if n == "U" else "volScalarField", n) for n in END7}
ROLES.update({n:("surfaceScalarField", n) for n in ("phi", "meshPhi", "alphaPhi.water")})
ROLES.update({"Uf":("surfaceVectorField", "Uf"), "rAU":("volScalarField", "rAU"),
    "polyMesh/points":("vectorField", "points"), "fvMesh/polyFaces":("surfaceLabelField", "polyFaces"),
    "uniform/time":("dictionary", "time")})
ROLES.update({n:("volScalarField",n) for n in ("rho", "alpha.vapour", "totalPressurePa", "U2", "yPlus")})
# Euler does not require Vc0. An actually stored, separately reviewed future
# integration role must be added deliberately; no guessed header is accepted.
FALSE_FLAGS = ("actualCfdRunVerified", "simulationExecuted", "matchedRestartVerified", "restartVerified",
    "checkpointStateSemanticsVerified", "aleGclVerified", "wallNormalFluxVerified", "headQualified",
    "phaseH0Eligible", "timeStepConvergenceVerified", "meshConvergenceVerified", "physicalValidationVerified",
    "npsh3Qualified", "qualifiedBaseline", "ALEVerified", "wallFluxVerified", "physicalTransientExecuted",
    "physicalMotionVerified", "numericalTransientQualified", "periodicStateVerified", "restartExecuted", "mainApplicationWrites")


class ClockIssue(ValueError): pass


def require(condition, code):
    if not condition: raise ClockIssue(code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def decode_json(raw):
    require(isinstance(raw,bytes) and 0<len(raw)<=MAX_JSON,"RESTART_CLOCK_BOUNDED_JSON_REQUIRED")
    def unique(pairs):
        result={}
        for k,v in pairs:require(k not in result,"RESTART_CLOCK_UNIQUE_JSON_KEYS_REQUIRED");result[k]=v
        return result
    def invalid(_):raise ClockIssue("RESTART_CLOCK_FINITE_JSON_REQUIRED")
    result=json.loads(raw,object_pairs_hook=unique,parse_constant=invalid)
    encoded(result)  # Also reject finite-syntax scalars that overflow float.
    return result


def number(value, *, positive=False):
    require(type(value) in (int, float) and math.isfinite(value) and (not positive or value > 0),
        "RESTART_CLOCK_FINITE_TYPED_NUMBER_REQUIRED")
    return float(value)


def close(a, b):
    return abs(a-b) <= max(4*math.ulp(a),4*math.ulp(b),2e-12*min(abs(a),abs(b),1e-6))


def label(value):
    require(isinstance(value,str) and re.fullmatch(TIME_NAME,value) and math.isfinite(float(value)) and float(value)>0,
        "RESTART_CLOCK_POSITIVE_NATIVE_TIME_LABEL_REQUIRED")
    return value


def data_text(raw):
    require(isinstance(raw,bytes) and 0<len(raw)<=MAX_FILE,"RESTART_CLOCK_BOUNDED_NATIVE_BYTES_REQUIRED")
    require(not re.search(rb"[#\x00$]|\b(?:code\w*|coded\w*|systemCall|include)\b",raw),
        "RESTART_CLOCK_DATA_ONLY_NATIVE_REQUIRED")
    text=raw.decode("ascii")
    text=re.sub(r"/\*.*?\*/|//[^\n]*","",text,flags=re.S)
    require(not re.search(r"\b(?:nan|inf|infinity)\b",text,re.I),"RESTART_CLOCK_FINITE_NATIVE_LITERALS_REQUIRED")
    return text


def entries(text):
    """Top-level dictionary with unique keys; nested blocks are kept as tokens."""
    tokens=re.findall(r'"[^"\n]*"|[{};]|[^\s{};]+',text)
    result={};i=0
    while i<len(tokens):
        key=tokens[i];i+=1
        require(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*",key) and key not in result,"RESTART_CLOCK_UNIQUE_DICTIONARY_KEYS_REQUIRED")
        start=i;depth=0
        if i<len(tokens) and tokens[i]=="{":
            while i<len(tokens):
                depth+=(tokens[i]=="{")-(tokens[i]=="}");i+=1
                if depth==0: break
            require(depth==0,"RESTART_CLOCK_CLOSED_DICTIONARY_REQUIRED")
            result[key]=tokens[start:i]
            if i<len(tokens) and tokens[i]==";":i+=1
        else:
            while i<len(tokens) and tokens[i]!=";":
                require(tokens[i] not in ("{","}"),"RESTART_CLOCK_SCALAR_OR_BLOCK_ENTRY_REQUIRED");i+=1
            require(i<len(tokens) and i>start,"RESTART_CLOCK_COMPLETE_DICTIONARY_ENTRY_REQUIRED")
            result[key]=tokens[start:i];i+=1
    return result


def native(raw, kind, object_name, location):
    text=data_text(raw);all_entries=entries(text)
    header=all_entries.pop("FoamFile",None)
    require(header is not None and header[0]=="{" and header[-1]=="}","RESTART_CLOCK_ONE_NATIVE_HEADER_REQUIRED")
    h=entries(" ".join(header[1:-1]))
    require(set(h)<= {"version","format","class","location","object","arch"},"RESTART_CLOCK_KNOWN_NATIVE_HEADER_REQUIRED")
    for key,wanted in (("format","ascii"),("class",kind),("object",object_name),("location",location)):
        require(h.get(key)==[wanted] or h.get(key)==['"'+wanted+'"'],"RESTART_CLOCK_EXACT_NATIVE_HEADER_REQUIRED")
    return all_entries


def scalar(entries_dict,key):
    tokens=entries_dict.get(key)
    require(tokens is not None and len(tokens)==1 and re.fullmatch(FLOAT,tokens[0]),"RESTART_CLOCK_EXACT_SCALAR_REQUIRED:"+key)
    return number(float(tokens[0]))


def parse_time_state(raw, time_directory):
    name=label(time_directory);e=native(raw,"dictionary","time",name+"/uniform")
    require(set(e)=={"beginTime","value","name","index","deltaT","deltaT0"},"RESTART_CLOCK_EXACT_SIX_TIME_KEYS_REQUIRED")
    require(e["name"]==['"'+name+'"'],"RESTART_CLOCK_STORED_TIME_NAME_REQUIRED")
    require(len(e["index"])==1 and re.fullmatch(r"(?:0|[1-9]\d*)",e["index"][0]),"RESTART_CLOCK_NONNEGATIVE_NATIVE_INDEX_REQUIRED")
    index=int(e["index"][0]);require(index<=2**31-1,"RESTART_CLOCK_BOUNDED_NATIVE_INDEX_REQUIRED")
    value=scalar(e,"value");begin=scalar(e,"beginTime")
    dt=scalar(e,"deltaT");dt0=scalar(e,"deltaT0")
    require(value>0 and 0<=begin<=value and dt>0 and dt0>0 and close(value,float(name)),"RESTART_CLOCK_CURRENT_TIME_AND_POSITIVE_STEPS_REQUIRED")
    return {"name":name,"valueS":value,"beginTimeS":begin,"index":index,"deltaTS":dt,"deltaT0S":dt0}


def parse_controls(raw, seed):
    e=native(raw,"dictionary","controlDict","system")
    require("userTime" not in e,"RESTART_CLOCK_DEFAULT_SI_SECONDS_REQUIRED")
    for key,wanted in (("application","foamRun"),("solver","incompressibleVoF"),("startFrom","startTime"),
        ("stopAt","endTime"),("runTimeModifiable","false")):
        require(e.get(key)==[wanted],"RESTART_CLOCK_EXPLICIT_IMMUTABLE_CONTROLS_REQUIRED:"+key)
    require(e.get("adjustTimeStep") in (["yes"],["no"],["true"],["false"]),"RESTART_CLOCK_EXPLICIT_CLOCK_MODE_REQUIRED")
    start=scalar(e,"startTime");end=scalar(e,"endTime");dt=scalar(e,"deltaT")
    require(close(start,seed["valueS"]) and end>start and dt>0,"RESTART_CLOCK_SEED_MATCHED_CONTROLS_REQUIRED")
    require("beginTime" not in e or close(scalar(e,"beginTime"),seed["beginTimeS"]),"RESTART_CLOCK_MATCHED_BEGIN_TIME_REQUIRED")
    precision=scalar(e,"timePrecision")
    require(precision.is_integer() and 12<=precision<=17 and e.get("timeFormat")==["general"],"RESTART_CLOCK_RESOLVED_NATIVE_TIME_PRECISION_REQUIRED")
    adaptive=e["adjustTimeStep"] in (["yes"],["true"])
    return {"startTimeS":start,"endTimeS":end,"controlDeltaTS":dt,"adaptive":adaptive,
        "effectiveInitialDeltaTS":seed["deltaTS"] if adaptive else dt}


def parse_resumed_log(raw, seed, controls, endpoint):
    state_keys={"name","valueS","beginTimeS","index","deltaTS","deltaT0S"}
    for state in (seed,endpoint):
        require(isinstance(state,dict) and set(state)==state_keys and type(state.get("index")) is int
            and 0<=state["index"]<=2**31-1,"RESTART_CLOCK_TYPED_PARSED_TIME_STATE_REQUIRED")
        label(state["name"])
        for key in ("valueS","beginTimeS","deltaTS","deltaT0S"):number(state[key],positive=key!="beginTimeS")
    require(isinstance(controls,dict) and set(controls)=={"startTimeS","endTimeS","controlDeltaTS","adaptive","effectiveInitialDeltaTS"}
        and type(controls.get("adaptive")) is bool,"RESTART_CLOCK_TYPED_PARSED_CONTROLS_REQUIRED")
    for key in ("startTimeS","endTimeS","controlDeltaTS","effectiveInitialDeltaTS"):number(controls[key],positive=True)
    require(close(controls["startTimeS"],seed["valueS"]) and controls["endTimeS"]>controls["startTimeS"]
        and close(controls["effectiveInitialDeltaTS"],seed["deltaTS"] if controls["adaptive"] else controls["controlDeltaTS"]),
        "RESTART_CLOCK_PARSED_SEED_CONTROL_MATCH_REQUIRED")
    require(isinstance(raw,bytes) and 0<len(raw)<=MAX_LOG,"RESTART_CLOCK_BOUNDED_NATIVE_LOG_REQUIRED")
    text=raw.decode("ascii");require("\x00" not in text and not re.search(r"FOAM FATAL|Floating point exception|Segmentation fault",text,re.I),"RESTART_CLOCK_NO_FATAL_NATIVE_LOG_REQUIRED")
    require(len(re.findall(r"^Starting time loop\s*$",text,re.M))==1 and len(re.findall(r"^End\s*$",text,re.M))==1,
        "RESTART_CLOCK_COMPLETE_NATIVE_LOOP_REQUIRED")
    prefix=text.split("Starting time loop",1)[0]
    require(re.findall(r"^Build\s*:\s*(\S+)\s*$",text,re.M)==["12-"+SOURCE_COMMIT[:12]]
        and re.findall(r"^Build\s*:\s*(\S+)\s*$",prefix,re.M)==["12-"+SOURCE_COMMIT[:12]],
        "RESTART_CLOCK_ACTUAL_PINNED_NATIVE_BUILD_REQUIRED")
    require(re.findall(r"^Selecting solver (\S+)\s*$",text,re.M)==["incompressibleVoF"]
        and re.findall(r"^Selecting solver (\S+)\s*$",prefix,re.M)==["incompressibleVoF"],
        "RESTART_CLOCK_ACTUAL_SELECTED_SOLVER_REQUIRED")
    meshes=re.findall(r"^Create mesh for time = ("+FLOAT+r")\s*(?:s)?\s*$",text,re.M)
    require(len(meshes)==1 and close(float(meshes[0]),seed["valueS"]),"RESTART_CLOCK_ACTUAL_MESH_START_REQUIRED")
    loop=text.split("Starting time loop",1)[1]
    rows=[];previous=seed["valueS"];previous_dt=controls["effectiveInitialDeltaTS"];pending=None;active=False;ended=False
    for line in loop.splitlines():
        stripped=line.strip()
        delta=re.fullmatch(r"deltaT\s*=\s*("+FLOAT+r")",stripped)
        time=re.fullmatch(r"Time\s*=\s*("+FLOAT+r")\s*s?",stripped)
        if stripped=="End":require(not active and pending is None,"RESTART_CLOCK_NO_PARTIAL_FINAL_STEP_REQUIRED");ended=True;continue
        if delta:
            require(not ended and not active and pending is None,"RESTART_CLOCK_ONE_PENDING_NATIVE_DT_REQUIRED")
            pending=number(float(delta[1]),positive=True)
        elif time:
            require(not ended and not active and len(rows)<MAX_STEPS,"RESTART_CLOCK_COMPLETE_ORDERED_STEP_REQUIRED")
            value=number(float(time[1]));dt=value-previous
            require(dt>0,"RESTART_CLOCK_STRICT_MONOTONIC_TIME_REQUIRED")
            require(not controls["adaptive"] or pending is not None,"RESTART_CLOCK_ACTUAL_ADAPTIVE_DT_REQUIRED")
            expected=pending if controls["adaptive"] else controls["controlDeltaTS"]
            roundoff=4*max(math.ulp(previous),math.ulp(value))
            require(expected>64*max(math.ulp(previous),math.ulp(value)),"RESTART_CLOCK_RESOLVED_PHYSICAL_INCREMENT_REQUIRED")
            require(abs(dt-expected)<=roundoff+2e-11*expected and
                (pending is None or abs(dt-pending)<=roundoff+2e-11*pending),"RESTART_CLOCK_ACTUAL_INCREMENT_DT_MATCH_REQUIRED")
            rows.append({"physicalTimeS":value,"index":seed["index"]+len(rows)+1,"deltaTS":dt,
                "deltaT0S":previous_dt,"printedDeltaT":pending is not None})
            previous=value;previous_dt=dt;pending=None;active=True
        elif stripped.startswith("ExecutionTime"):
            execution=re.fullmatch(r"ExecutionTime = ("+FLOAT+r") s\s+ClockTime = ("+FLOAT+r") s",stripped)
            require(active and not ended and execution,
                "RESTART_CLOCK_COMPLETE_EXECUTION_ROW_REQUIRED")
            require(all(math.isfinite(float(v)) and float(v)>=0 for v in execution.groups()),"RESTART_CLOCK_FINITE_EXECUTION_TIMES_REQUIRED")
            active=False
        elif re.match(r"(?:Time|deltaT)\s*=",stripped):raise ClockIssue("RESTART_CLOCK_VALID_NATIVE_CLOCK_LITERAL_REQUIRED")
    require(ended and rows and not active and pending is None,"RESTART_CLOCK_COMPLETE_NONEMPTY_TIMELINE_REQUIRED")
    last=rows[-1]
    endpoint_roundoff=4*math.ulp(last["physicalTimeS"])
    require(endpoint["index"]==last["index"] and close(endpoint["valueS"],last["physicalTimeS"])
        and abs(endpoint["deltaTS"]-last["deltaTS"])<=endpoint_roundoff+2e-11*endpoint["deltaTS"]
        and abs(endpoint["deltaT0S"]-last["deltaT0S"])<=endpoint_roundoff+2e-11*endpoint["deltaT0S"]
        and close(endpoint["beginTimeS"],seed["beginTimeS"]) and close(last["physicalTimeS"],controls["endTimeS"]),
        "RESTART_CLOCK_ENDPOINT_INDEX_TIME_AND_DT_MATCH_REQUIRED")
    return rows


def safe_path(root,name):
    require(isinstance(name,str) and name.isascii() and "\\" not in name and ":" not in name,"RESTART_CLOCK_SAFE_LOCAL_PATH_REQUIRED")
    parts=PurePosixPath(name).parts
    require(parts and len(parts)<=10 and not name.startswith("/") and all(p not in (".","..") for p in parts)
        and str(PurePosixPath(name))==name,"RESTART_CLOCK_SAFE_LOCAL_PATH_REQUIRED")
    target=root
    for part in parts:
        target=target/part;require(not target.is_symlink() and not getattr(target,"is_junction",lambda:False)(),"RESTART_CLOCK_NO_LINKS_REQUIRED")
    require(target.resolve().is_relative_to(root.resolve()),"RESTART_CLOCK_LOCAL_ROOT_REQUIRED")
    return target


def read_bound(root,row,*,limit=MAX_FILE):
    require(isinstance(row,dict) and set(row)=={"path","bytes","sha256"} and type(row["bytes"]) is int
        and 0<row["bytes"]<=limit and isinstance(row["sha256"],str) and re.fullmatch(r"[0-9a-f]{64}",row["sha256"]),
        "RESTART_CLOCK_EXACT_FILE_DESCRIPTOR_REQUIRED")
    target=safe_path(root,row["path"]);require(target.is_file() and target.stat().st_size==row["bytes"],"RESTART_CLOCK_CURRENT_FILE_SIZE_REQUIRED")
    raw=target.read_bytes();require(len(raw)==row["bytes"] and sha256(raw).hexdigest()==row["sha256"],"RESTART_CLOCK_CURRENT_FILE_SHA_REQUIRED")
    return raw


def inventory(root,checkpoint):
    base=safe_path(root,checkpoint);require(base.is_dir(),"RESTART_CLOCK_ACTUAL_CHECKPOINT_DIRECTORY_REQUIRED")
    files=[];dirs=[];total=0;pending=[base];paths=[]
    # Bound entries while scanning, before constructing/sorting an inventory.
    while pending:
        with os.scandir(pending.pop()) as stream:
            for entry in stream:
                path=Path(entry.path);paths.append(path)
                require(len(paths)<=MAX_FILES*4,"RESTART_CLOCK_BOUNDED_DIRECTORY_INVENTORY_REQUIRED")
                require(not path.is_symlink() and not getattr(path,"is_junction",lambda:False)(),"RESTART_CLOCK_NO_LINKS_REQUIRED")
                safe_path(root,path.relative_to(root).as_posix())
                if entry.is_dir(follow_symlinks=False):pending.append(path)
    for path in sorted(paths,key=lambda p:p.as_posix()):
        require(not path.is_symlink() and not getattr(path,"is_junction",lambda:False)(),"RESTART_CLOCK_NO_LINKS_REQUIRED")
        relative=path.relative_to(root).as_posix();safe_path(root,relative)
        if path.is_dir():dirs.append(relative);continue
        require(path.is_file() and len(files)<MAX_FILES,"RESTART_CLOCK_BOUNDED_REGULAR_INVENTORY_REQUIRED")
        size=path.stat().st_size;total+=size
        require(0<size<=MAX_FILE and total<=MAX_TOTAL,"RESTART_CLOCK_BOUNDED_FULL_INVENTORY_REQUIRED")
        digest=sha256();count=0
        with path.open("rb") as stream:
            while chunk:=stream.read(1024**2):
                count+=len(chunk);require(count<=MAX_FILE,"RESTART_CLOCK_BOUNDED_FULL_INVENTORY_REQUIRED");digest.update(chunk)
        require(count==size,"RESTART_CLOCK_CURRENT_FILE_SIZE_REQUIRED")
        files.append({"path":relative,"bytes":count,"sha256":digest.hexdigest()})
    return files,dirs


def checkpoint_roles(root,checkpoint,rows):
    prefix=checkpoint+"/";name=checkpoint.rsplit("/",1)[1];roles=set()
    for row in rows:
        require(row["path"].startswith(prefix),"RESTART_CLOCK_CHECKPOINT_LOCAL_INVENTORY_REQUIRED")
        role=row["path"][len(prefix):];require(role in ROLES,"RESTART_CLOCK_KNOWN_CHECKPOINT_DATA_ROLE_REQUIRED")
        roles.add(role);raw=read_bound(root,row);text=data_text(raw)
        headers=re.findall(r"\bFoamFile\s*\{([^{}]*)\}",text,re.S)
        require(len(headers)==1,"RESTART_CLOCK_ONE_NATIVE_HEADER_REQUIRED")
        h=entries(headers[0]);kind,obj=ROLES[role];location=name+("/"+role.rsplit("/",1)[0] if "/" in role else "")
        for key,wanted in (("format","ascii"),("class",kind),("object",obj),("location",location)):
            require(h.get(key)==[wanted] or h.get(key)==['"'+wanted+'"'],"RESTART_CLOCK_EXACT_CHECKPOINT_ROLE_HEADER_REQUIRED")
    require(REQUIRED_STATE<=roles,"RESTART_CLOCK_COMPLETE_STORED_STATE_INVENTORY_REQUIRED")
    return sorted(roles)


def inspect(execution_directory,packet):
    """Read-only fact packet, with exact checkpoint inventory before and after."""
    original_root=Path(execution_directory)
    require(not original_root.is_symlink() and not getattr(original_root,"is_junction",lambda:False)(),"RESTART_CLOCK_LOCAL_DIRECTORY_REQUIRED")
    root=original_root.resolve();require(root.is_dir(),"RESTART_CLOCK_LOCAL_DIRECTORY_REQUIRED")
    initial_packet=encoded(packet)
    keys={"schema","expectedSourceCommit","checkpointDirectory","checkpointInventory","checkpointInventorySha256",
        "controlDictFile","nativeLogFile","endpointTimeStateFile","sourceGeometryIdentityFile"}
    require(isinstance(packet,dict) and set(packet)==keys and packet["schema"]==INPUT_SCHEMA
        and packet["expectedSourceCommit"]==SOURCE_COMMIT,"RESTART_CLOCK_EXACT_INPUT_SCHEMA_AND_SOURCE_REQUIRED")
    require(all(isinstance(packet[key],dict) for key in ("controlDictFile","nativeLogFile","endpointTimeStateFile","sourceGeometryIdentityFile")),
        "RESTART_CLOCK_TYPED_FILE_DESCRIPTORS_REQUIRED")
    checkpoint=packet["checkpointDirectory"]
    require(isinstance(checkpoint,str) and re.fullmatch(r"case/"+TIME_NAME,checkpoint),"RESTART_CLOCK_EXACT_CASE_CHECKPOINT_REQUIRED")
    seed_name=label(checkpoint.split("/")[1]);declared=packet["checkpointInventory"]
    require(isinstance(declared,list) and 0<len(declared)<=MAX_FILES,"RESTART_CLOCK_DECLARED_FULL_INVENTORY_REQUIRED")
    before,dirs=inventory(root,checkpoint)
    require(declared==before and packet["checkpointInventorySha256"]==sha256(encoded(before)).hexdigest(),"RESTART_CLOCK_COMPLETE_CURRENT_INVENTORY_MATCH_REQUIRED")
    # Equivalent numeric directory aliases are ambiguous restart sources.
    case=root/"case"
    aliases=[]
    for count,path in enumerate(case.iterdir(),1):
        require(count<=MAX_FILES*4,"RESTART_CLOCK_BOUNDED_CASE_DIRECTORY_REQUIRED")
        require(not path.is_symlink() and not getattr(path,"is_junction",lambda:False)(),"RESTART_CLOCK_NO_LINKS_REQUIRED")
        if path.is_dir() and re.fullmatch(TIME_NAME,path.name) and close(float(path.name),float(seed_name)):aliases.append(path.name)
    require(aliases==[seed_name],"RESTART_CLOCK_UNAMBIGUOUS_NATIVE_TIME_DIRECTORY_REQUIRED")
    roles=checkpoint_roles(root,checkpoint,before)
    seed_row=next(r for r in before if r["path"]==checkpoint+"/uniform/time")
    require(packet["controlDictFile"].get("path")=="case/system/controlDict","RESTART_CLOCK_EXACT_CONTROL_PATH_REQUIRED")
    require(re.fullmatch(r"logs/[A-Za-z0-9_.-]+\.log",packet["nativeLogFile"].get("path","")),"RESTART_CLOCK_EXACT_LOG_PATH_REQUIRED")
    endpoint_path=packet["endpointTimeStateFile"].get("path","")
    m=re.fullmatch(r"case/("+TIME_NAME+r")/uniform/time",endpoint_path)
    require(m and float(m[1])>float(seed_name),"RESTART_CLOCK_ACTUAL_LATER_ENDPOINT_REQUIRED")
    require(packet["sourceGeometryIdentityFile"].get("path")=="teaching-geometry-identity.json","RESTART_CLOCK_AUTHORED_IDENTITY_PATH_REQUIRED")
    bound=[seed_row,packet["controlDictFile"],packet["nativeLogFile"],packet["endpointTimeStateFile"],packet["sourceGeometryIdentityFile"]]
    raw=[read_bound(root,r,limit=MAX_LOG if r==packet["nativeLogFile"] else MAX_JSON if r==packet["sourceGeometryIdentityFile"] else MAX_FILE) for r in bound]
    identity=decode_json(raw[-1]);require(isinstance(identity,dict) and identity.get("schemaVersion")=="untirta.classroom-authored-pump-geometry-identity.v1"
        and identity.get("provenance")=="CLASSROOM_AUTHORED_GEOMETRY" and identity.get("units")=="SI"
        and identity.get("manufacturerIdentity") is None and identity.get("originalFluentSourceIdentity") is None,
        "RESTART_CLOCK_AUTHORED_SI_IDENTITY_REQUIRED")
    seed=parse_time_state(raw[0],seed_name);controls=parse_controls(raw[1],seed);endpoint=parse_time_state(raw[3],m[1])
    rows=parse_resumed_log(raw[2],seed,controls,endpoint)
    after,after_dirs=inventory(root,checkpoint)
    require(before==after and dirs==after_dirs and initial_packet==encoded(packet),"RESTART_CLOCK_CHECKPOINT_INVENTORY_IMMUTABLE_REQUIRED")
    for r,data in zip(bound,raw):require(read_bound(root,r)==data,"RESTART_CLOCK_INPUT_BYTES_IMMUTABLE_REQUIRED")
    facts={"schema":SCHEMA,"status":"SEEDED_NATIVE_CLOCK_FACTS_ONLY","sourceCommit":SOURCE_COMMIT,"primarySources":SOURCES,
        "seed":seed,"endpoint":endpoint,"controls":controls,"nativeClockVerified":True,"checkpointInventoryVerified":True,
        "nativeLogBuildVerified":True,"nativeSelectedSolver":"incompressibleVoF",
        "completeStoredStateRolesPresent":True,"checkpointRoleCount":len(roles),"checkpointFiles":before,
        "checkpointInventorySha256":packet["checkpointInventorySha256"],"nativeEvidenceRecords":bound,
        "nativeStepCount":len(rows),"timelineSha256":sha256(encoded(rows)).hexdigest(),
        "firstPhysicalTimeS":rows[0]["physicalTimeS"],"lastPhysicalTimeS":rows[-1]["physicalTimeS"],
        "minimumObservedDeltaTS":min(r["deltaTS"] for r in rows),"maximumObservedDeltaTS":max(r["deltaTS"] for r in rows),
        "globalTimeRebased":False,"storedDeltaTOverridesControl":controls["adaptive"],
        "equationStages":{"input":{"seedTimeS":seed["valueS"],"seedIndex":seed["index"],"actualStepCount":len(rows)},
            "equation":"t_n = t_seed + sum(actual deltaT_j); index_n = index_seed + n",
            "substitution":{"sumObservedDeltaTS":math.fsum(r["deltaTS"] for r in rows)},
            "result":{"endpointTimeS":endpoint["valueS"],"endpointIndex":endpoint["index"],"units":"s"}},
        "limitations":["Inventory/header completeness does not validate all field values or copied restart state.",
            "No native process, matched restart comparison, motion, ALE, head or convergence acceptance is performed."],
        **{flag:False for flag in FALSE_FLAGS}}
    return facts


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--execution-directory",required=True);p.add_argument("--input-json",required=True)
    a=p.parse_args()
    try:
        path=Path(a.input_json);require(path.is_file() and not path.is_symlink() and 0<path.stat().st_size<=MAX_JSON,"RESTART_CLOCK_BOUNDED_INPUT_JSON_REQUIRED")
        packet=decode_json(path.read_bytes())
        report=inspect(a.execution_directory,packet);output=encoded(report);require(len(output)<=MAX_JSON,"RESTART_CLOCK_BOUNDED_FACT_REPORT_REQUIRED")
        print(output.decode());return 0
    except (ValueError,OSError,UnicodeError,StopIteration) as error:
        print(json.dumps({"schema":SCHEMA,"status":"RESTART_CLOCK_EVIDENCE_REJECTED","diagnostic":str(error),
            **{flag:False for flag in FALSE_FLAGS}},sort_keys=True));return 2


if __name__=="__main__":raise SystemExit(main())
