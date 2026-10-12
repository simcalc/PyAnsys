"""Read current authored-SI rotor coordinates and native clock-bound NCC logs.

This pure checker launches no process and qualifies no CFD, ALE, wall flux,
periodicity, pressure result, refinement or restart. Coordinate and coverage
branches remain independent when one branch lacks actual native evidence.
"""
from __future__ import annotations
from array import array
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
import frozen_rotor_native_quality as native
import run_teaching_pump_phase as phase
import teaching_pump_geometry as author

SCHEMA="untirta.teaching-pump-motion-evidence.v1"
PREPARATION_SCHEMA="untirta.teaching-pump-sliding-pilot-preparation.v1"
REPORT="teaching-sliding-preparation.json"
IDENTITY="teaching-geometry-identity.json"
MAX_FILE=128*1024**2
MAX_LOG=8*1024**2
MAX_REPORT=128*1024
MAX_CELLS=500000
MAX_POINTS=1000000
MAX_CLOCKS=100000
MESH_FILES={"points","faces","owner","neighbour","boundary","cellZones"}
FLOAT=native.FLOAT
SOURCE_COMMIT="86e126a7bc4dbfaed949bbc58c9f0fdd67acc059"
SOURCE_BASE="https://github.com/OpenFOAM/OpenFOAM-12/blob/"+SOURCE_COMMIT+"/"
SELECTOR_SOURCE=SOURCE_BASE+"src/motionSolvers/displacement/solidBody/solidBodyMotionFunctions/solidBodyMotionFunction/solidBodyMotionFunctionNew.C"
MOTION_SOURCE=SOURCE_BASE+"src/motionSolvers/displacement/solidBody/solidBodyMotionSolver/solidBodyMotionSolver.C"
NCC_SOURCE=SOURCE_BASE+"src/finiteVolume/fvMesh/fvMeshStitchers/fvMeshStitcher/fvMeshStitcher.C"


class MotionIssue(ValueError):pass


def require(condition,action):
    if not condition:raise MotionIssue(action)


def preparer():
    import teaching_pump_sliding_case
    return teaching_pump_sliding_case


def read(root,relative,records,limit=MAX_FILE):
    require(isinstance(relative,str) and not Path(relative).is_absolute() and ".." not in Path(relative).parts
        and all(p and not p.startswith(".") for p in Path(relative).parts),"Use a plain source-owned relative evidence path.")
    path=Path(root)/relative
    for item in (path,*path.parents):
        require(not item.is_symlink() and not getattr(item,"is_junction",lambda:False)(),"Motion evidence cannot traverse links or junctions.")
    raw=phase.base.read_regular(path,limit);row={"path":Path(relative).as_posix(),"bytes":len(raw),"sha256":sha256(raw).hexdigest()}
    if row["path"] in records:require(records[row["path"]]==row,"A source-owned motion input changed during inspection.")
    records[row["path"]]=row;return raw,row


def json_data(raw):
    def pairs(items):
        result={}
        for key,value in items:
            require(key not in result,"Motion metadata cannot contain duplicate keys.");result[key]=value
        return result
    def invalid(_):raise MotionIssue("Motion metadata must contain finite JSON numbers.")
    def finite_float(value):
        number=float(value);require(math.isfinite(number),"Motion metadata must contain finite JSON numbers.");return number
    result=json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid,parse_float=finite_float)
    require(isinstance(result,dict),"A current source-owned metadata object is required.");return result


def point_vectors(raw,*,location=None):
    text=re.sub(r"/\*.*?\*/|//[^\n]*","",raw.decode("ascii"),flags=re.S)
    header=re.match(r"\s*FoamFile\s*\{([^{}]*)\}",text)
    require(header and all(len(re.findall(r"\b"+key+r"\s+[^;{}]+;",header[1]))==1 for key in ("class","object","format")),"Native point headers must have one exact storage/class/object role.")
    if location is not None:
        locations=re.findall(r"\blocation\s+([^;{}]+);",header[1])
        require(len(locations)==1 and locations[0].strip().strip('"')==location,"Actual point header location must match its bound native source/time path.")
    body=native.native_body(raw,"points",{"vectorField"})
    match=re.fullmatch(r"(\d+)\s*\((.*)\)\s*;?",body,re.S)
    require(match and 0<int(match[1])<=MAX_POINTS,"Actual points need a complete bounded native vectorField count.")
    values=[];last=0;payload=match[2]
    for m in re.finditer(r"\(([^()]*)\)",payload):
        require(not payload[last:m.start()].strip(),"All native point-vector text must be accounted for.")
        words=m[1].split();require(len(words)==3 and all(re.fullmatch(FLOAT,w) and math.isfinite(float(w)) for w in words),"Every actual point must have three finite SI coordinates.")
        values.append(tuple(map(float,words)));last=m.end()
    require(len(values)==int(match[1]) and not payload[last:].strip(),"Every native point label must retain its complete vector.")
    return values


def labels(raw,name):
    body=native.native_body(raw,name,{"labelList"})
    match=re.fullmatch(r"(\d+)\s*(?:\(([^()]*)\)|\{([^{}]*)\})\s*;?",body,re.S)
    require(match and int(match[1])<=6*MAX_CELLS,"A complete bounded current native topology labelList is required.")
    count=int(match[1]);uniform=match[3] is not None;words=(match[3] if uniform else match[2]).split()
    require((count>0 and len(words)==1 if uniform else len(words)==count)
        and all(re.fullmatch(r"\d+",w) and int(w)<MAX_CELLS for w in words),"Native cell labels must be exact bounded nonnegative integers.")
    values=array("I",map(int,words));return values*count if uniform else values


def face_vectors(raw,face_count,point_count):
    body=native.native_body(raw,"faces",{"faceList"});match=re.fullmatch(r"(\d+)\s*\((.*)\)\s*;?",body,re.S)
    require(match and int(match[1])==face_count,"Each current native face must have one owner label.")
    faces=[];last=0;payload=match[2]
    for m in re.finditer(r"(\d+)\s*\(([^()]*)\)",payload):
        words=m[2].split();require(not payload[last:m.start()].strip() and len(words)==int(m[1]) and 3<=len(words)<=64
            and all(re.fullmatch(r"\d+",w) and int(w)<point_count for w in words),"Actual face order and distinct bounded point labels must remain complete.")
        ids=tuple(map(int,words));require(len(set(ids))==len(ids),"Native motion faces cannot repeat a point label.")
        faces.append(ids);last=m.end()
    require(len(faces)==face_count and not payload[last:].strip(),"Every native face must be accounted for.");return faces


def boundary_pair(raw,internal_faces,face_count):
    body=native.native_body(raw,"boundary",{"polyBoundaryMesh"});match=re.fullmatch(r"(\d+)\s*\((.*)\)\s*;?",body,re.S)
    require(match,"A complete current native boundary dictionary is required.")
    entries=list(re.finditer(r'"?([A-Za-z0-9_-]+)"?\s*\{([^{}]*)\}',match[2]));boundary={};last=0;offset=internal_faces
    for m in entries:
        require(not match[2][last:m.start()].strip() and m[1] not in boundary,"Every native boundary patch must be uniquely accounted for.")
        row={"dictionary":m[2]}
        for key,pattern in (("type",r"\w+"),("nFaces",r"\d+"),("startFace",r"\d+")):
            found=re.findall(r"\b"+key+r"\s+("+pattern+r")\s*;",m[2]);require(len(found)==1,"Native boundary roles and ranges must be explicit.")
            row[key]=found[0] if key=="type" else int(found[0])
        require(row["startFace"]==offset and offset+row["nFaces"]<=face_count,"Actual boundary faces must cover the complete ordered exterior.")
        offset+=row["nFaces"];boundary[m[1]]=row;last=m.end()
    require(len(entries)==int(match[1]) and offset==face_count and not match[2][last:].strip(),"Actual boundary count/exterior coverage must be exact.")
    owner="pumpInterface_on_interface-impeller-outlet";neighbour="pumpInterface_on_interface-volute-inlet"
    cyclic=[name for name,row in boundary.items() if row["type"]=="nonConformalCyclic"]
    require(cyclic==[owner,neighbour],"This pilot supports exactly one ordered owner NCC pair; anonymous coverage cannot identify multiple pairs.")
    for name,other,original in ((owner,neighbour,"interface-impeller-outlet"),(neighbour,owner,"interface-volute-inlet")):
        row=boundary[name];require(row["nFaces"]==0 and original in boundary and boundary[original]["nFaces"]>0
            and re.findall(r"\bneighbourPatch\s+(\S+)\s*;",row["dictionary"])==[other]
            and re.findall(r"\boriginalPatch\s+(\S+)\s*;",row["dictionary"])==[original]
            and re.findall(r"\btransformType\s+(\S+)\s*;",row["dictionary"])==["none"],"The actual single NCC pair must bind reciprocal original-patch identities and no transform.")
    require(not any(row["type"] in {"processor","processorCyclic","nonConformalMappedWall"} for row in boundary.values()),"The single-CPU pilot cannot assign anonymous logs to extra coupled roles.")
    return boundary,{"ownerPatch":owner,"neighbourPatch":neighbour,"sourceOriginalPatch":"interface-impeller-outlet","targetOriginalPatch":"interface-volute-inlet",
        "sourceFaceCount":boundary["interface-impeller-outlet"]["nFaces"],"targetFaceCount":boundary["interface-volute-inlet"]["nFaces"]}


def motion_inputs(directory,*,records=None):
    root=phase.base.regular_directory(directory);helper=preparer();verified=helper.verify_output(root,after_native_execution=True)
    records={} if records is None else records
    raw,row=read(root,REPORT,records,MAX_REPORT);prep=json_data(raw)
    require(prep==verified["report"] and prep.get("schemaVersion")==PREPARATION_SCHEMA and prep.get("sourceGeometryRoute")=="CLASSROOM_AUTHORED_GEOMETRY"
        and prep.get("mainApplicationWrites") is False and prep.get("mode")=="physical-sliding-active-phase-pilot", "Use only independently replayed new authored-SI sliding preparation.")
    motion=prep["motionPlan"];plan=prep["temporalPlan"]
    require(motion==helper.MOTION_PLAN and all(type(x) in (int,float) and math.isfinite(x) for x in motion["axis"]+motion["originM"])
        and motion["axis"]==[0,0,1] and motion["originM"]==[0,0,0] and type(motion["speedRpm"]) in (int,float) and motion["speedRpm"]==1450
        and type(motion["initialPhaseRad"]) in (int,float) and motion["initialPhaseRad"]==0
        and type(prep["declaredStudyInputs"]["initialAngleRad"]) in (int,float) and prep["declaredStudyInputs"]["initialAngleRad"]==0
        and all(type(x) in (int,float) and math.isfinite(x) for x in prep["physicalBasis"]["rotationAxis"]+prep["physicalBasis"]["rotationOriginM"])
        and prep["physicalBasis"]["rotationAxis"]==motion["axis"]
        and prep["physicalBasis"]["rotationOriginM"]==motion["originM"],"The actual motion declaration must bind zero phase, fixed +z/origin0 and1450rpm.")
    period=60/motion["speedRpm"];writes=[x*period for x in (.25,.5,.75,1,1.25)]
    require(plan.get("expectedWriteTimesS")==writes and math.isclose(plan["endTimeS"],1.25*period,rel_tol=1e-12)
        and math.isclose(plan["maximumTimeStepS"],period/360,rel_tol=1e-12),"Retain the declared five quarter-period pilot states and one-degree maximum clock.")
    geometry_raw,geometry_row=read(root,IDENTITY,records,MAX_REPORT);identity=json_data(geometry_raw)
    require(geometry_row==prep["sourceGeometryIdentityFile"] and identity.get("schemaVersion")==author.IDENTITY_SCHEMA
        and identity.get("provenance")=="CLASSROOM_AUTHORED_GEOMETRY" and identity.get("units")=="SI"
        and identity.get("axis")==motion["axis"] and identity.get("originM")==motion["originM"]
        and identity.get("manufacturerIdentity") is None and identity.get("originalFluentSourceIdentity") is None,"Bind the exact current teaching SI geometry; original-source identities are not admitted.")
    mesh_rows=prep["preservedNativeMeshFiles"]
    require(isinstance(mesh_rows,list) and len(mesh_rows)==6 and {r["path"] for r in mesh_rows}=={"constant/polyMesh/"+n for n in MESH_FILES},"All six actual source reference primitives are required.")
    mesh={}
    for declared in mesh_rows:
        raw,row=read(root,"case/"+declared["path"],records);require(row=={**declared,"path":"case/"+declared["path"]},"A source reference topology/coordinate file changed after binding.")
        mesh[Path(declared["path"]).name]=raw
    study={"input":prep["declaredStudyInputs"],"physicalBasis":prep["physicalBasis"],"sourceReference":prep["sourceReference"],"controls":{}}
    authored=helper.native_recipe(study,default_faces=prep["defaultFacesPresent"])
    for name in ("constant/dynamicMeshDict","system/controlDict"):
        raw,row=read(root,"case/"+name,records);expected=next((r for r in prep["authoredInputFiles"] if r["path"]==name),None)
        require(expected and row=={**expected,"path":"case/"+name} and raw==authored[name],"Current motion/clock bytes must match the trusted authored recipe.")
    require(not (root/"case/constant/MRFProperties").exists(),"Physical sliding must not also apply an MRF rotation.")
    points=point_vectors(mesh["points"],location="constant/polyMesh");owner=labels(mesh["owner"],"owner");neighbour=labels(mesh["neighbour"],"neighbour")
    require(owner and len(neighbour)<=len(owner) and all(a!=b for a,b in zip(owner,neighbour)),"Current motion topology requires nondegenerate owner/neighbour labels.")
    cells=max(max(owner),max(neighbour,default=0))+1;zones=native.zones(mesh["cellZones"],cells)
    faces=face_vectors(mesh["faces"],len(owner),len(points));boundary,pair=boundary_pair(mesh["boundary"],len(neighbour),len(owner))
    rotor=bytearray(len(points));stator=bytearray(len(points));used=bytearray(cells)
    for index,ids in enumerate(faces):
        adjacent=(owner[index],neighbour[index]) if index<len(neighbour) else (owner[index],)
        for cell in adjacent:
            used[cell]=1;mask=rotor if cell in zones["rotating-fluid"] else stator
            for point in ids:mask[point]=1
    require(all(used) and 0<sum(rotor)<len(points) and all(a+b==1 for a,b in zip(rotor,stator)),"Both point-adjacency masks must cover every label exactly once; shared rotor/stator topology is unsupported.")
    return {"root":root,"points":points,"rotating":rotor,"stationary":stator,"owner":owner,"neighbour":neighbour,"faces":faces,"zones":zones,
        "boundary":boundary,"records":records,"motionPlan":motion,"temporalPlan":plan,"expectedWriteTimesS":writes,"nccPair":pair,"preparation":prep,"geometryIdentity":identity}


def native_clock(text,plan):
    require(isinstance(text,str) and 0<len(text.encode("utf-8"))<=MAX_LOG and re.search(r"(?m)^End\s*$",text)
        and not re.search(r"FOAM FATAL|Segmentation fault|Floating point exception",text,re.I),"A bounded actually completed native motion log is required.")
    matches=list(re.finditer(r"(?m)^Time = ("+FLOAT+r")s?\s*$",text));times=[float(m[1]) for m in matches]
    require(times and len(times)<=MAX_CLOCKS and len(re.findall(r"(?m)^Time = ",text))==len(times)
        and all(math.isfinite(t) and 0<t<=plan["endTimeS"]+1e-12 for t in times)
        and all(a<b for a,b in zip(times,times[1:])) and math.isclose(times[-1],plan["endTimeS"],rel_tol=5e-11,abs_tol=1e-12),"All native Time tags must be finite, ordered, unique and reach the declared pilot endpoint.")
    increments=[times[0]]+[b-a for a,b in zip(times,times[1:])]
    require(max(increments)<=plan["maximumTimeStepS"]*(1+1e-7)+1e-12,"The actual motion clock exceeds the declared one-degree maximum.")
    return times,matches


def coordinate_motion(inputs,time_directories,text):
    require(len(re.findall(r"(?m)^Selecting solid-body motion function rotatingMotion\s*$",text))==1,"Require the pinned native rotatingMotion selector spelling, not an authored declaration alone.")
    times,_=native_clock(text,inputs["temporalPlan"]);expected=inputs["expectedWriteTimesS"]
    require(isinstance(time_directories,(list,tuple)) and len(time_directories)==5,"The pilot must retain all five actual positive-time point writes.")
    original=inputs["points"];scale=math.hypot(*(max(p[d] for p in original)-min(p[d] for p in original) for d in range(3)))
    require(math.isfinite(scale) and scale>0,"A finite positive source SI extent is required.")
    tolerance=1e-11+1e-10*scale;rows=[];maximum=0;root=inputs["root"];previous=0
    for (instant,path),wanted in zip(time_directories,expected):
        require(type(instant) in (int,float) and math.isfinite(instant) and previous<instant
            and math.isclose(instant,wanted,rel_tol=5e-11,abs_tol=1e-12) and any(math.isclose(t,instant,rel_tol=5e-11,abs_tol=1e-12) for t in times),"Each retained point write must match its exact planned and native logged clock.")
        path=Path(path).absolute();require(path.parent==root/"case" and re.fullmatch(phase.fields.TIME_NAME,path.name)
            and math.isclose(float(path.name),instant,rel_tol=5e-11,abs_tol=1e-12),"Only actual local time directories may provide motion evidence.")
        raw,row=read(root,"case/"+path.name+"/polyMesh/points",inputs["records"]);current=point_vectors(raw,location=path.name+"/polyMesh")
        require(len(current)==len(original),"Every actual moved point must preserve its source label.")
        angle=2*math.pi*inputs["motionPlan"]["speedRpm"]*instant/60;s,c=math.sin(angle),math.cos(angle);rotor_error=stator_error=0
        for index,(before,after) in enumerate(zip(original,current)):
            wanted=(c*before[0]-s*before[1],s*before[0]+c*before[1],before[2]) if inputs["rotating"][index] else before
            error=math.dist(wanted,after)
            if inputs["rotating"][index]:rotor_error=max(rotor_error,error)
            else:stator_error=max(stator_error,error)
            maximum=max(maximum,math.dist(before,after))
        require(rotor_error<=tolerance and stator_error<=tolerance,"Actual rotor/stator points violate global Rz(omega*t)x0 or stationary identity.")
        rows.append({**row,"physicalTimeS":instant,"rotationAngleRad":angle,"maximumRotorErrorM":rotor_error,"maximumStatorErrorM":stator_error,"allPointLabelsChecked":True});previous=instant
    period=inputs["motionPlan"]["rotationPeriodS"]
    require(maximum>tolerance and rows[-1]["physicalTimeS"]-rows[0]["physicalTimeS"]>=period-1e-12,"Motion requires a nonstationary measured trajectory spanning at least one full revolution.")
    return {"coordinateMotionVerified":True,"coordinateUnits":"SI_METRES","rotationAxis":[0,0,1],"rotationOriginM":[0,0,0],"initialPhaseRad":0,
        "equation":"x_rotor(t)=Rz(2*pi*N*t/60)*x0; x_stator(t)=x0; global physical time is not reset",
        "speedRpm":inputs["motionPlan"]["speedRpm"],"rotorPointCount":sum(inputs["rotating"]),"statorPointCount":sum(inputs["stationary"]),
        "rotorMaskSha256":sha256(inputs["rotating"]).hexdigest(),"statorMaskSha256":sha256(inputs["stationary"]).hexdigest(),
        "absoluteCoordinateRoundoffToleranceM":1e-11,"relativeCoordinateRoundoffTolerance":1e-10,"comparisonToleranceM":tolerance,
        "referenceDiagonalM":scale,"maximumDisplacementM":maximum,"nativeMovedPointRecords":rows,"nativeTimeCount":len(times),
        "trajectorySpanS":rows[-1]["physicalTimeS"]-rows[0]["physicalTimeS"],"sourceUrl":MOTION_SOURCE}


def dynamic_ncc_coverage(text,expected_times,pair,plan):
    times,matches=native_clock(text,plan);require(times==expected_times,"Coverage must bind every actual native Time tag, not a sampled subset.")
    start_pattern=r"(?m)^\s*patchToPatch: Calculating couplings between ([1-9]\d*) source faces and ([1-9]\d*) target faces\s*$"
    coverage_pattern=r"(?m)^\s*Source min/average/max coverage = ("+FLOAT+r")/("+FLOAT+r")/("+FLOAT+r")\s*\n\s*Target min/average/max coverage = ("+FLOAT+r")/("+FLOAT+r")/("+FLOAT+r")\s*$"
    rows=[]
    for index,m in enumerate(matches):
        block=text[m.end():matches[index+1].start() if index+1<len(matches) else len(text)]
        starts=list(re.finditer(start_pattern,block));metrics=list(re.finditer(coverage_pattern,block))
        require(len(starts)==len(metrics)==1 and len(re.findall(r"\b(?:Source|Target) min/average/max coverage =",block))==2,"Every native motion clock requires exactly one complete, nonduplicate source/target coverage pair.")
        require((int(starts[0][1]),int(starts[0][2]))==(pair["sourceFaceCount"],pair["targetFaceCount"])
            and starts[0].end()<=metrics[0].start(),"Native anonymous coverage face counts must match the only source-bound NCC owner pair.")
        values=list(map(float,metrics[0].groups()));require(all(math.isfinite(v) and v>=0 for v in values)
            and all(v[0]<=v[1]<=v[2] for v in (values[:3],values[3:])),"Native NCC coverage values must be finite and ordered.")
        require(all(v[0]>=.99 and v[1]>=.999 and v[2]<=1.01 for v in (values[:3],values[3:])),"Actual dynamic NCC coverage fails unchanged0.99/0.999/1.01 criteria.")
        connected=list(re.finditer(r"(?m)^\s*fvMeshStitcher: Connecting\s*$",block))
        flux=list(re.finditer(r"(?m)^\s*([A-Za-z0-9_-]+) min/avg/max mesh flux error = ("+FLOAT+r")/("+FLOAT+r")/("+FLOAT+r")\s*$",block))
        ended=list(re.finditer(r"(?m)^\s*([1-9]\d*) couplings calculated in ("+FLOAT+r")s\s*$",block))
        require(len(connected)==len(flux)==len(ended)==1 and metrics[0].end()<=ended[0].start()<connected[0].start()<flux[0].start()
            and flux[0][1]==pair["ownerPatch"] and all(math.isfinite(float(v)) for v in flux[0].groups()[1:])
            and float(flux[0][2])<=float(flux[0][3])<=float(flux[0][4]) and int(ended[0][1])<=10000000
            and math.isfinite(float(ended[0][2])) and float(ended[0][2])>=0,"Bind the native completed reconnect and actual named owner role in each motion clock; missing role evidence is pending.")
        rows.append({"physicalTimeS":times[index],"sourceCoverage":values[:3],"targetCoverage":values[3:],"nativeOwnerPatch":flux[0][1],
            "nativeCouplingCount":int(ended[0][1]),"nativeMeshFluxError":list(map(float,flux[0].groups()[1:])),"meshFluxCriterionEvaluated":False})
    return {"dynamicNccClockCoverageVerified":True,"nativeClockCount":len(times),"nativeCoveragePairs":rows,"nccPair":pair,
        "criteria":{"minimum":.99,"average":.999,"maximum":1.01},"roleBinding":"Exactly one current NCC owner pair, exact physical face counts and subsequent named native mesh-flux-error role per Time tag",
        "ALEVerified":False,"wallFluxVerified":False,"sourceUrl":NCC_SOURCE}


def inspect(directory,time_directories,log_text=None):
    output={"schemaVersion":SCHEMA,"sourceGeometryRoute":"CLASSROOM_AUTHORED_GEOMETRY","coordinateMotionVerified":False,
        "dynamicNccClockCoverageVerified":False,"ALEVerified":False,"wallFluxVerified":False,"physicalTransientQualified":False,
        "periodicTransientQualified":False,"restartVerified":False,"timeConvergenceVerified":False,"actualCfdRunVerified":False,
        "mainApplicationWrites":False,"diagnostics":[],"nativeEvidenceRecords":[],
        "claimBoundary":"Current native coordinate and per-clock coverage evidence only. ALE/wall flux, periodicity, CFD qualification, pressure results, convergence and restart are separate."}
    inputs=None;records={}
    try:
        root=phase.base.regular_directory(directory);raw,row=read(root,"logs/01-foamRun.log",records,MAX_LOG);text=raw.decode("utf-8")
        require(log_text is None or log_text==text,"The supplied native log text must match the actual locally retained log bytes.")
        inputs=motion_inputs(root,records=records)
        times,_=native_clock(text,inputs["temporalPlan"])
        try:output["coordinateMotion"]=coordinate_motion(inputs,time_directories,text);output["coordinateMotionVerified"]=True
        except (ValueError,OSError,KeyError,TypeError,UnicodeError) as error:output["diagnostics"].append({"branch":"coordinate-motion","reasonCode":str(error)[:500]})
        try:output["dynamicNccCoverage"]=dynamic_ncc_coverage(text,times,inputs["nccPair"],inputs["temporalPlan"]);output["dynamicNccClockCoverageVerified"]=True
        except (ValueError,OSError,KeyError,TypeError,UnicodeError) as error:output["diagnostics"].append({"branch":"dynamic-ncc-clock-coverage","reasonCode":str(error)[:500]})
        for relative,declared in list(records.items()):require(read(inputs["root"],relative,records,MAX_LOG if relative.startswith("logs/") else MAX_FILE)[1]==declared,"Source/moved/log evidence changed during motion inspection.")
    except (ValueError,OSError,KeyError,TypeError,UnicodeError,ImportError) as error:
        output.update(coordinateMotionVerified=False,dynamicNccClockCoverageVerified=False)
        output.pop("coordinateMotion",None);output.pop("dynamicNccCoverage",None)
        output["diagnostics"].append({"branch":"source-owned-motion-evidence","reasonCode":str(error)[:500]})
    output["nativeEvidenceRecords"]=sorted(records.values(),key=lambda r:r["path"])
    return output
