"""Build only a bounded approximate full-pump mesh on the public FREE worker.

Actual utility completion, native mesh quality and geometric approximation are
separate facts. This runner never starts a flow solver or qualifies performance.
"""
from __future__ import annotations

import argparse
from array import array
from collections import Counter, deque
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_frozen_rotor_surface_check as surface
import frozen_rotor_native_quality as quality_parser

base, native = surface.base, surface.native
SCHEMA="untirta.frozen-rotor-approximate-snappy-execution.v1"
PREPARATION_SCHEMA="untirta.frozen-rotor-approximate-snappy-preparation.v1"
MAX_SECONDS=900
MAX_COMBINED_CELLS=1_000_000  # Strictly below this independently measured bound.
MAX_COMBINED_POINTS=1_000_000
MAX_COMPONENT_CELLS=450_000
MAX_LOG=surface.MAX_LOG
PROGRAMS=("blockMesh","surfaceFeatures","snappyHexMesh","splitMeshRegions","topoSet","checkMesh","mergeMeshes","createNonConformalCouples")
MESH_FILES=("points","faces","owner","neighbour","boundary","cellZones")
SELECTION_FILES=MESH_FILES[:5]
SELECTION_MAPS=("pointRegionAddressing","faceRegionAddressing","cellRegionAddressing")
SELECTION_SCHEMA="untirta.frozen-rotor-native-region-selection.v1"
MINIMUM_RETAINED_VOLUME_FRACTION=.999
COMPONENTS=surface.COMPONENTS
ROTOR,STATOR="interface-impeller-outlet","interface-volute-inlet"


def require(condition,action):
    surface.require(condition,action)


def selection_command(output,component):
    seed=component["insidePointEvidence"]["pointNativeUnits"]
    require(isinstance(seed,list) and len(seed)==3 and all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in seed),
            "Use the finite actual measured three-coordinate source seed.")
    vector="("+" ".join(format(float(value),".17g") for value in seed)+")"
    return ["splitMeshRegions","-case",str(Path(output).absolute()/(component["component"]+"-case")),"-insidePoint",vector,"-overwrite"]


def commands(output,components=None):
    root=Path(output).absolute();rows=[]
    if components is None:
        report=json.loads(base.read_regular(root/"snappy-preparation.json",base.inventory.MAX_REPORT_BYTES))
        require(report.get("schemaVersion")==PREPARATION_SCHEMA,"Read measured selection seeds only from the exact preparation schema.")
        components=report.get("components")
    require(isinstance(components,list) and [row.get("component") for row in components]==list(COMPONENTS),
            "Two source-verified component seeds in fixed order are required before authoring commands.")
    for name in COMPONENTS:
        case=str(root/(name+"-case"))
        component=next(row for row in components if row["component"]==name)
        rows.extend([["blockMesh","-case",case],["surfaceFeatures","-case",case],
            ["snappyHexMesh","-case",case,"-overwrite"],
            selection_command(root,component),
            ["topoSet","-case",case,"-constant"],
            ["checkMesh","-case",case,"-allGeometry","-allTopology","-writeSets"]])
    case=str(root/"impeller-case");other=str(root/"volute-case")
    rows.extend([["mergeMeshes","-case",case,"-addCases",'(\"'+other+'\")',"-overwrite"],
        ["createNonConformalCouples","-case",case,"-overwrite"],
        ["checkMesh","-case",case,"-allGeometry","-allTopology","-writeSets"]])
    return rows


def record(root,relative,limit=surface.MAX_FILE):
    require(isinstance(relative,str) and 0<len(relative)<=256 and "\\" not in relative
            and not PurePosixPath(relative).is_absolute() and all(part not in {"",".",".."} for part in relative.split("/"))
            and all(re.fullmatch(r"[A-Za-z0-9_.-]+",part) for part in relative.split("/")),
            "Use an exact safe relative native evidence path.")
    path=Path(root)/relative
    for item in (path,*path.parents):
        require(not item.is_symlink() and not getattr(item,"is_junction",lambda:False)(),"Native evidence cannot traverse links or junctions.")
        if item==Path(root):break
    return surface.descriptor(path,relative,limit)


def runtime_identity(deadline):
    require(time.monotonic()<deadline,"The approximate mesh deadline was reached before native binding.")
    def query(arguments):
        remaining=deadline-time.monotonic()
        require(remaining>0,"The bounded geometry deadline was reached during package binding.")
        result=subprocess.run(arguments,env=native.child_environment(),capture_output=True,text=True,
                              timeout=min(10,remaining),check=True)
        require(len(result.stdout)<=8192 and len(result.stderr)<=8192,"Package identity output exceeds its byte bound.")
        return result.stdout
    require(query(["dpkg-query","--show","--showformat=${Version}","openfoam12"])=="20250206",
            "Use only the exact official Foundation-12 package 20250206.")
    binaries=[]
    for program in PROGRAMS:
        executable=shutil.which(program)
        require(executable,"An actual native geometry utility is missing: "+program)
        path=Path(executable).resolve()
        require(path.is_relative_to(Path("/opt/openfoam12")),"Every geometry executable must resolve inside the official package.")
        owner=query(["dpkg-query","--search",str(path)])
        require(owner.strip()=="openfoam12: "+str(path),"Every actual geometry binary must belong to the pinned official package.")
        _,row=surface.descriptor(path)
        binaries.append({**row,"program":program,"path":str(path),"packageOwnership":owner.strip()})
    require(time.monotonic()<deadline,"The geometry deadline was reached after runtime binding.")
    return {"schemaVersion":"untirta.frozen-rotor-snappy-runtime-identity.v1","openFoamFoundationVersion":12,
            "packageVersion":"20250206","officialDebSha256":surface.PACKAGE_SHA,
            "expectedSourceCommit":surface.SOURCE_COMMIT,"binaries":binaries,"mainApplicationWrites":False}


def completed_log(text):
    require(len(re.findall(r"(?m)^End\s*$",text))==1
            and not re.search(r"FOAM FATAL|Segmentation fault|Floating point exception",text,re.I),
            "Complete fatal-free native utility output with End is required.")
    builds=re.findall(r"(?m)^Build\s*:\s*(\S+)\s*$",text)
    require(builds==["12-"+surface.SOURCE_COMMIT[:12]],"The actual utility build must match the reviewed Foundation-12 source.")


def execute(arguments,output,deadline,runtime,index):
    authored=commands(output)
    require(isinstance(index,int) and not isinstance(index,bool) and 0<=index<len(authored)
            and arguments==authored[index],"Only the fifteen indexed authored native geometry commands may execute.")
    root=base.regular_directory(output)
    expected=root/"logs"/(f"{index:02d}-{arguments[0]}.log")
    require(not expected.exists() and not expected.is_symlink(),"Each native geometry log is fresh and immutable.")
    matches=[row for row in runtime["binaries"] if row["program"]==arguments[0]]
    require(len(matches)==1,"An unambiguous actual executable identity is required.")
    row=matches[0];binary=Path(row["path"])
    require(binary.resolve().is_relative_to(Path("/opt/openfoam12")),"Run only the hash-bound official native utility.")
    _,actual=surface.descriptor(binary)
    require(actual["bytes"]==row["bytes"] and actual["sha256"]==row["sha256"],"An actual native executable changed after binding.")
    require(time.monotonic()<deadline,"The geometry serial deadline was reached before utility startup.")
    surface.workspace_size(root)
    process,total,started=None,0,time.monotonic();next_progress=started+30
    selector=selectors.DefaultSelector()
    try:
        with expected.open("xb") as destination:
            process=subprocess.Popen([str(binary),*arguments[1:]],cwd=arguments[2],stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,env=native.child_environment(),preexec_fn=surface.child_limits,
                start_new_session=True,bufsize=0)
            selector.register(process.stdout,selectors.EVENT_READ)
            while selector.get_map():
                now=time.monotonic()
                require(now<deadline,"The bounded approximate native mesh campaign timed out.")
                surface.workspace_size(root)
                if now>=next_progress:
                    print(json.dumps({"stageIndex":index,"program":arguments[0],"status":"NativeGeometryRunning",
                        "elapsedSeconds":round(now-started,1),"simulationExecuted":False,"mainApplicationWrites":False}),flush=True)
                    next_progress=now+30
                for key,_ in selector.select(timeout=.25):
                    chunk=os.read(key.fileobj.fileno(),4096)
                    if not chunk:
                        selector.unregister(key.fileobj);continue
                    remaining=MAX_LOG-total
                    destination.write(chunk[:remaining]);destination.flush();total+=min(remaining,len(chunk))
                    require(len(chunk)<=remaining,"The native geometry log reached its 4 MiB bound.")
            code=process.wait(timeout=3)
            surface.workspace_size(root)
            require(time.monotonic()<deadline,"The approximate mesh deadline was reached at process completion.")
    finally:
        if process is not None:
            native.stop_owned(process);process.stdout.close()
        selector.close()
    raw,row=record(root,"logs/"+expected.name,MAX_LOG)
    return {"stageIndex":index,"program":arguments[0],"arguments":arguments,"exitCode":code,
            "elapsedSeconds":round(time.monotonic()-started,3),"logFile":row},raw.decode("utf-8",errors="strict")


def check_log(text):
    completed_log(text)
    require(len(re.findall(r"(?m)^Mesh OK\.[ \t]*\r?$",text))==1,
            "Actual native checkMesh acceptance is required; exit zero alone is insufficient.")
    require(not re.search(r"Failed\s+[1-9]\d*\s+mesh checks|\*\*\*",text),"The unchanged native geometry/topology checks must all pass.")
    require("Enabling all (cell, face, edge, point) topology checks." in text and "Enabling all geometry checks." in text,
            "Actual allTopology and allGeometry checks must be enabled.")
    counts={}
    for key in ("points","faces","internal faces","cells"):
        values=re.findall(r"(?m)^\s*"+re.escape(key)+r":\s*(\d+)\s*$",text)
        require(len(values)==1,"Actual checkMesh mesh counts must be unique: "+key)
        counts[key]=int(values[0])
    volume=re.findall(r"Min volume = ("+surface.FLOAT+r")\. Max volume = ("+surface.FLOAT+r")\.\s+Total volume = ("+surface.FLOAT+r")\.\s+Cell volumes OK\.",text)
    require(len(volume)==1,"Actual native positive volume range and total are required.")
    values=[float(value) for value in volume[0]]
    require(all(math.isfinite(value) and value>0 for value in values) and values[0]<=values[1],"The actual native cell volumes must be finite and positive.")
    determinant=re.findall(r"Cell determinant \(wellposedness\) : minimum: ("+surface.FLOAT+r") average: ("+surface.FLOAT+r")",text)
    require(len(determinant)==1 and all(math.isfinite(float(value)) and float(value)>0 for value in determinant[0]),
            "Retain the actual native cell-determinant range with the unchanged threshold.")
    regions=re.findall(r"(?m)^\s*\*?Number of regions:\s*(\d+)(?:\s*\(OK\)\.)?[ \t]*$",text)
    require(len(regions)==1,"The actual native volume-region count must be measured.")
    return {"counts":counts,"minimumNativeCellVolumeAssumedM3":values[0],"maximumNativeCellVolumeAssumedM3":values[1],
            "totalNativeCellVolumeAssumedM3":values[2],"minimumNativeCellDeterminant":float(determinant[0][0]),
            "meanNativeCellDeterminant":float(determinant[0][1]),"nativeCellDeterminantThreshold":.001,
            "polyMeshConnectedRegionCount":int(regions[0]),"nativeGeometryQualityVerified":True,
            "claimBoundary":"Actual checkMesh allGeometry/allTopology result on this generated mesh; no physical performance, wall resolution or mesh-convergence claim."}


def boundary_entries(path):
    count,body=base.native_list(path)
    entries=re.findall(r'"?([A-Za-z0-9_-]+)"?\s*\{([^{}]*)\}',body)
    require(len(entries)==count and not re.sub(r'"?[A-Za-z0-9_-]+"?\s*\{[^{}]*\}',"",body).strip(),
            "The actual native boundary list must contain complete plain patch dictionaries.")
    result={}
    for name,values in entries:
        require(name not in result,"Native boundary names must be unique.")
        row={}
        for key,pattern in (("type",r"\w+"),("nFaces",r"\d+"),("startFace",r"\d+")):
            matches=re.findall(r"\b"+key+r"\s+("+pattern+r")\s*;",values)
            require(len(matches)==1,"Native boundary patch roles and ranges must be explicit.")
            row[key]=matches[0] if key=="type" else int(matches[0])
        result[name]={**row,"dictionary":values}
    return result


def native_zones(path):
    """Accept the actual Foundation-12 written zone class without changing old guards."""
    raw=base.read_regular(path,surface.MAX_FILE)
    body=quality_parser.native_body(raw,"cellZones",{"regIOobject","cellZoneMesh","cellZoneList"})
    match=re.fullmatch(r"(\d+)\s*\((.*)\)\s*;?",body,re.S)
    require(match,"A complete actual counted native cell-zone list is required.")
    return int(match[1]),match[2].strip()


def selection_labels(path,*,signed=False):
    raw=base.read_regular(path,surface.MAX_FILE)
    body=quality_parser.native_body(raw,Path(path).name,{"labelList"})
    match=re.fullmatch(r"(\d+)\s*\((.*)\)\s*;?",body,re.S)
    require(match,"A complete counted native selection labelList is required.")
    count=int(match[1]);words=match[2].split()
    require(count<=3_000_000 and len(words)==count and all(re.fullmatch(r"-?\d+" if signed else r"\d+",word) for word in words),
            "Native selection addressing must contain only its exact bounded integer count.")
    return array("q",map(int,words))


def selection_primitives(folder,component):
    """Read actual polygon primitives; no mesh rewriting or quality promotion."""
    folder=base.regular_directory(folder)
    n_points,body=base.native_list(folder/"points");points=array("d");end=0
    require(0<n_points<MAX_COMBINED_POINTS,"Selection requires bounded actual native point counts.")
    for match in re.finditer(r"\(([^()]*)\)",body):
        require(not body[end:match.start()].strip(),"Unparsed native point data is prohibited.")
        words=match[1].split()
        require(len(words)==3 and all(re.fullmatch(surface.FLOAT,word) for word in words),"Selection points require three plain numbers.")
        values=tuple(map(float,words));require(all(math.isfinite(v) for v in values),"Selection coordinates must all be finite.")
        points.extend(values);end=match.end()
    require(not body[end:].strip() and len(points)==3*n_points,"Every declared selection point must be present.")
    owners=selection_labels(folder/"owner");neighbours=selection_labels(folder/"neighbour")
    require(0<=len(neighbours)<len(owners)<=3_000_000,"Selection requires complete internal and boundary face ranges.")
    n_cells=max(max(owners),max(neighbours,default=-1))+1
    require(0<n_cells<=MAX_COMPONENT_CELLS and set(owners)|set(neighbours)==set(range(n_cells))
            and all(owners[i]<neighbours[i] for i in range(len(neighbours))),"Selection addressing must cover every ordered bounded native cell.")
    n_faces,body=base.native_list(folder/"faces");vertices=array("I");offsets=array("I",[0]);end=0
    require(n_faces==len(owners),"Selection face and owner counts must agree.")
    for match in re.finditer(r"(\d+)\s*\(([^()]*)\)",body):
        require(not body[end:match.start()].strip(),"Unparsed native polygon data is prohibited.")
        words=match[2].split();count=int(match[1])
        require(3<=count<=1024 and len(words)==count and all(re.fullmatch(r"\d+",word) for word in words),"Every native selection face needs a bounded counted polygon.")
        ids=tuple(map(int,words));require(len(set(ids))==count and max(ids)<n_points,"Every selection polygon needs distinct in-range actual vertices.")
        vertices.extend(ids);offsets.append(len(vertices));end=match.end()
    require(not body[end:].strip() and len(offsets)==n_faces+1,"Every declared native selection face must be parsed.")
    patches=boundary_entries(folder/"boundary");start=len(neighbours);patch_ids=array("i",[-1])*n_faces;names=list(patches)
    expected=component["patchTypes"]
    require(set(expected)<=set(patches) and set(patches)<=set(expected)|{"backgroundBox"},"Selection cannot introduce or rename source physical patches.")
    for index,(name,row) in enumerate(patches.items()):
        require(row["startFace"]==start and 0<=row["nFaces"]<=n_faces-start,"Selection boundary ranges must remain contiguous and complete.")
        require(row["type"]==expected.get(name,"patch"),"Selection preserves every source physical patch type.")
        if name=="backgroundBox":require(row["nFaces"]==0,"Selection cannot retain a positive background-box boundary.")
        else:require(row["nFaces"]>0,"Selection requires every source physical patch to remain nonempty.")
        patch_ids[start:start+row["nFaces"]]=array("i",[index])*row["nFaces"];start+=row["nFaces"]
    require(start==n_faces,"Every selection boundary face must retain a measured original patch role.")
    cell_faces=[array("i") for _ in range(n_cells)]
    for face,owner in enumerate(owners):cell_faces[owner].append(face+1)
    for face,neighbour in enumerate(neighbours):cell_faces[neighbour].append(-face-1)
    return {"points":points,"vertices":vertices,"offsets":offsets,"owners":owners,"neighbours":neighbours,
            "cellFaces":cell_faces,"patches":patches,"patchNames":names,"patchIds":patch_ids,"cellCount":n_cells,"pointCount":n_points}


def selection_face(mesh,index):
    return mesh["vertices"][mesh["offsets"][index]:mesh["offsets"][index+1]]


def selection_measures(mesh):
    """Complete directed-edge closure and signed polygon-fan volume, in native units."""
    points=mesh["points"];origin=tuple((min(points[i::3])+max(points[i::3]))/2 for i in range(3))
    volumes=array("d");face_volumes=array("d");areas={name:[] for name in mesh["patchNames"]}
    for face in range(len(mesh["owners"])):
        ids=selection_face(mesh,face);xyz=[tuple(points[3*v+i]-origin[i] for i in range(3)) for v in ids]
        # The chosen fan is stated explicitly; exact retained polygons give an
        # identical fan integral before/after selection. Native checkMesh later
        # remains the independent determinant/concavity/quality authority.
        a=xyz[0];terms=[];crosses=[]
        for index in range(1,len(xyz)-1):
            b,c=xyz[index:index+2];ab=tuple(b[i]-a[i] for i in range(3));ac=tuple(c[i]-a[i] for i in range(3))
            crosses.append((ab[1]*ac[2]-ab[2]*ac[1],ab[2]*ac[0]-ab[0]*ac[2],ab[0]*ac[1]-ab[1]*ac[0]))
            bc=(b[1]*c[2]-b[2]*c[1],b[2]*c[0]-b[0]*c[2],b[0]*c[1]-b[1]*c[0]);terms.append(math.fsum(a[i]*bc[i] for i in range(3))/6)
        area=.5*math.hypot(*(math.fsum(cross[i] for cross in crosses) for i in range(3)))
        require(math.isfinite(area) and area>0,"Every native selection polygon must have positive finite area.")
        face_volumes.append(math.fsum(terms))
        patch=mesh["patchIds"][face]
        if patch>=0:areas[mesh["patchNames"][patch]].append(area)
    for signed_faces in mesh["cellFaces"]:
        edge_counts={};terms=[]
        for signed_face in signed_faces:
            face=abs(signed_face)-1;ids=selection_face(mesh,face)
            if signed_face<0:ids=ids[::-1]
            for index,a in enumerate(ids):
                b=ids[(index+1)%len(ids)];key=(min(a,b),max(a,b));counts=edge_counts.setdefault(key,[0,0])
                counts[a>b]+=1
            terms.append(face_volumes[face]*(1 if signed_face>0 else -1))
        require(edge_counts and all(counts==[1,1] for counts in edge_counts.values()),"Every actual native cell must have complete directed-edge closure without nonmanifold edges.")
        volume=math.fsum(terms)
        require(math.isfinite(volume) and volume>0,"Every actual native selection cell must have positive finite polygon-fan volume.")
        volumes.append(volume)
    return volumes,{name:math.fsum(values) for name,values in areas.items()}


def snapshot_region_source(output,component):
    """Freeze five raw pre-selection primitives before the utility overwrites them."""
    root=base.regular_directory(output);case=root/component["caseDirectory"];folder=case/"constant/polyMesh"
    source_names=[*SELECTION_FILES,"cellLevel"]
    zones=folder/"cellZones"
    if zones.exists():
        count,body=native_zones(zones)
        require(count==0 and not body.strip(),"Select the unnamed default mesh before topoSet; existing cell zones may redirect -overwrite into a named region.")
        source_names.append("cellZones")
    require(not any((folder/name).exists() for name in SELECTION_MAPS),"Pre-selection native maps must be fresh, not evidence from an earlier region selection.")
    destination=root/"region-selection"/component["component"]
    require(not destination.exists(),"Each component region-selection evidence directory must be fresh.")
    rows=[]
    for name in source_names:
        raw,_=record(root,component["caseDirectory"]+"/constant/polyMesh/"+name)
        relative="region-selection/"+component["component"]+"/source-polyMesh/"+name
        base.write_exclusive(root/relative,raw);rows.append(record(root,relative)[1]);surface.workspace_size(root)
    return rows


def _cycle(ids):
    ids=list(ids);first=ids.index(min(ids));return tuple(ids[first:]+ids[:first])


def _region_selection(output,component,text,*,command_root=None):
    root=base.regular_directory(output);name=component["component"];prefix="region-selection/"+name+"/"
    folder=root/prefix;source=selection_primitives(folder/"source-polyMesh",component)
    selected=selection_primitives(folder/"selected-polyMesh",component)
    completed_log(text)
    found=re.findall(r"(?m)^Found point\s+\([^()]+\)\s+in cell\s+(-?\d+)\s*$",text)
    region=re.findall(r"(?m)^Subsetting region\s+(-?\d+)\s+containing point\s+\([^()]+\)\s*$",text)
    require(len(found)==len(region)==1 and 0<=int(found[0])<source["cellCount"] and int(region[0])>=0,
            "An actual completed native seed lookup and region-subsetting record are required.")
    seed_cell=int(found[0]);maps={role:selection_labels(folder/"maps"/role,signed=role=="faceRegionAddressing") for role in SELECTION_MAPS}
    points,faces,cells=(maps[role] for role in SELECTION_MAPS)
    for values,count,maximum,role in ((points,selected["pointCount"],source["pointCount"],"point"),(cells,selected["cellCount"],source["cellCount"],"cell")):
        require(len(values)==count and len(set(values))==count and all(0<=v<maximum for v in values),"Every selected "+role+" requires a unique exact in-range native source mapping.")
    require(len(faces)==len(selected["owners"]) and len({abs(v) for v in faces})==len(faces)
            and all(0<abs(v)<=len(source["owners"]) for v in faces),"Signed native face maps must be unique, nonzero and complete.")
    require(all(selected["points"][3*i:3*i+3]==source["points"][3*old:3*old+3] for i,old in enumerate(points)),
            "Selection must preserve every mapped point coordinate exactly at native ASCII precision.")
    kept=set(cells);require(seed_cell in kept,"The selected native component must contain the actual source seed cell.")
    for index,neighbour in enumerate(source["neighbours"]):
        require((source["owners"][index] in kept)==(neighbour in kept),"No original internal face may cross the selected/removed cells; selection only removes disconnected regions.")
    reached={seed_cell};queue=deque([seed_cell])
    while queue:
        cell=queue.popleft()
        for signed in source["cellFaces"][cell]:
            face=abs(signed)-1
            if face<len(source["neighbours"]):
                other=source["neighbours"][face] if signed>0 else source["owners"][face]
                if other not in reached:reached.add(other);queue.append(other)
    require(reached==kept,"Retain exactly the face-connected actual native region containing the measured seed.")
    expected_faces={face for face,owner in enumerate(source["owners"]) if owner in kept}
    require({abs(v)-1 for v in faces}==expected_faces,"The selected mesh must retain every face of the seed-connected region exactly once.")
    expected_points=set()
    for index,signed in enumerate(faces):
        old=abs(signed)-1;old_ids=selection_face(source,old)
        mapped=[points[v] for v in selection_face(selected,index)];expected_points.update(old_ids)
        require(_cycle(mapped)==_cycle(old_ids if signed>0 else old_ids[::-1]),"Every native face must retain its exact mapped polygon and signed winding.")
        internal=index<len(selected["neighbours"]);old_internal=old<len(source["neighbours"])
        require(internal==old_internal,"Region selection cannot turn an old internal face into a new physical boundary.")
        old_owner=source["owners"][old];old_neighbour=source["neighbours"][old] if old_internal else None
        require(cells[selected["owners"][index]]==(old_owner if signed>0 else old_neighbour),"Native owner mapping must agree with each signed oriented face.")
        if internal:require(cells[selected["neighbours"][index]]==(old_neighbour if signed>0 else old_owner),"Native neighbour mapping must agree with the signed oriented face.")
        else:
            new_patch=selected["patchNames"][selected["patchIds"][index]];old_patch=source["patchNames"][source["patchIds"][old]]
            require(new_patch==old_patch and selected["patches"][new_patch]["type"]==source["patches"][old_patch]["type"],"Every selected boundary face must retain its actual source patch name and physical role.")
    require(set(points)==expected_points,"Selection may neither invent nor retain an unused native point.")
    levels=selection_labels(folder/"source-polyMesh/cellLevel")
    require(len(levels)==source["cellCount"] and all(0<=v<=2 for v in levels) and all(levels[cell]==2 for cell in cells),
            "Every actual retained cell must have measured source refinement level2; do not infer uniform refinement from authored settings.")
    volumes,old_areas=selection_measures(source);selected_volume=math.fsum(volumes[cell] for cell in cells)
    original_volume=math.fsum(volumes);removed_volume=math.fsum(volume for cell,volume in enumerate(volumes) if cell not in kept)
    fraction=selected_volume/original_volume
    require(math.isfinite(fraction) and MINIMUM_RETAINED_VOLUME_FRACTION<=fraction<=1,
            "The measured seed region must retain at least99.9% of generated component volume; a larger geometric subset requires a separate study.")
    # Exact point/polygon/cell mapping has already proved every selected cell's
    # closure and positive volume equal to its corresponding source cell.
    _,new_areas=selection_measures(selected)
    relative_difference=abs((original_volume-selected_volume)-removed_volume)/original_volume
    require(relative_difference<=1e-12,"Measured retained and removed polygon volumes must close the actual source-volume balance.")
    patch_changes={patch:{"beforePolygonAreaVectorMagnitudeAssumedM2":area,"afterPolygonAreaVectorMagnitudeAssumedM2":new_areas.get(patch,0.),
                          "areaDifferenceAssumedM2":new_areas.get(patch,0.)-area} for patch,area in old_areas.items()}
    source_names=[*SELECTION_FILES,"cellLevel"]
    zones=folder/"source-polyMesh/cellZones"
    if zones.exists():
        count,body=native_zones(zones);require(count==0 and not body.strip(),"The saved actual pre-selection cellZones must be empty.");source_names.append("cellZones")
    source_rows=[record(root,prefix+"source-polyMesh/"+role)[1] for role in source_names]
    selected_rows=[record(root,prefix+"selected-polyMesh/"+role)[1] for role in SELECTION_FILES]
    map_rows=[record(root,prefix+"maps/"+role)[1] for role in SELECTION_MAPS]
    stage=3 if name=="impeller" else 9;log_path=f"logs/{stage:02d}-splitMeshRegions.log"
    log_raw,log_row=record(root,log_path,MAX_LOG)
    require(log_raw.decode("utf-8",errors="strict")==text,"Use the actual immutable native split log, not supplied replacement text.")
    command_root=root if command_root is None else Path(command_root)
    require(command_root.is_absolute() and len(str(command_root))<=512 and not any(c in str(command_root) for c in "\r\n\x00"),
            "Historical native command provenance must be an explicit bounded absolute directory; it is never executed during replay.")
    arguments=selection_command(command_root,component)
    return {"schemaVersion":SELECTION_SCHEMA,"component":name,"status":"NATIVE_DISCONNECTED_REGION_SELECTION_VERIFIED",
        "sourceNativeFileRecords":source_rows,"selectedNativeFileRecords":selected_rows,"nativeAddressingFileRecords":map_rows,
        "nativeSplitLogFile":log_row,"nativeCommandArguments":arguments,"sourceSeedPointNativeUnits":component["insidePointEvidence"]["pointNativeUnits"],
        "nativeSeedCellIdBeforeSelection":seed_cell,"nativeSelectedRegionId":int(region[0]),"sourceCellCount":source["cellCount"],
        "selectedCellCount":selected["cellCount"],"removedCellCount":source["cellCount"]-selected["cellCount"],
        "sourcePolygonFanVolumeAssumedM3":original_volume,"selectedPolygonFanVolumeAssumedM3":selected_volume,
        "removedPolygonFanVolumeAssumedM3":removed_volume,"retainedGeneratedVolumeFraction":fraction,
        "minimumRequiredRetainedGeneratedVolumeFraction":MINIMUM_RETAINED_VOLUME_FRACTION,"relativeVolumeBalanceError":relative_difference,
        "sourceCellLevelHistogram":dict(sorted(Counter(levels).items())),"selectedMappedCellLevelHistogram":{2:len(cells)},
        "selectedRefinementLevelMeasuredFromSourceAddressing":2,"sourcePatchAreaChanges":patch_changes,
        "cellLevelBasis":"Immutable pre-selection native cellLevel projected through verified actual cellRegionAddressing; current native cellLevel may be stale after split and is not used as selected-mesh evidence.",
        "sourceCellZonesAbsent":not zones.exists(),"sourceCellZonesEmptyVerified":True,"nativeDefaultMeshOverwrittenVerified":True,
        "mappedPointCoordinatesExact":True,"mappedOrientedFacesExact":True,"mappedOwnerNeighbourExact":True,"mappedBoundaryRolesExact":True,
        "noSelectedRemovedInternalFaceVerified":True,"selectedSeedRegionConnectivityVerified":True,
        "allCellDirectedEdgeClosureVerified":True,"allCellPolygonFanVolumesPositiveVerified":True,
        "nativeCoordinateUnitsVerified":False,"classroomMetresPerSourceCoordinateUnit":1,"sourceHdf5OrStlModified":False,
        "geometryApproximationQualified":False,"meshQualityCheckedBySolver":False,"simulationExecuted":False,"actualCfdRunVerified":False,
        "claimBoundary":"Actual native disconnected-region subset of the generated approximate mesh, preserving exact mapped points, oriented polygons and physical patch roles. Source seed lookup is native; graph connectivity and source-to-selected topology/volume are independently replayed. Polygon fan integrals describe this subset only; full checkMesh, source geometry deviation and CFD remain independent gates."}


def retain_region_selection(output,component,text):
    root=base.regular_directory(output);prefix="region-selection/"+component["component"]+"/"
    for role in (*SELECTION_FILES,*SELECTION_MAPS):
        raw,_=record(root,component["caseDirectory"]+"/constant/polyMesh/"+role)
        relative=prefix+("maps/" if role in SELECTION_MAPS else "selected-polyMesh/")+role
        base.write_exclusive(root/relative,raw);surface.workspace_size(root)
    report=_region_selection(root,component,text)
    raw=base.inventory.bounded_json(report)
    base.write_exclusive(root/prefix/"selection-report.json",raw)
    return {**json.loads(raw),"reportFile":record(root,prefix+"selection-report.json",base.inventory.MAX_REPORT_BYTES)[1]}


def verify_region_selection(output,component,text=None,*,command_root=None):
    """Read-only replay from immutable snapshots, even after native merge/NCC."""
    root=base.regular_directory(output);prefix="region-selection/"+component["component"]+"/"
    raw,row=record(root,prefix+"selection-report.json",base.inventory.MAX_REPORT_BYTES)
    saved=json.loads(raw);require(saved.get("schemaVersion")==SELECTION_SCHEMA,"Use the exact native selection evidence schema.")
    if text is None:text=record(root,saved["nativeSplitLogFile"]["path"],MAX_LOG)[0].decode("utf-8",errors="strict")
    expected=_region_selection(root,component,text,command_root=command_root)
    # JSON object keys of the measured level histograms are serialized strings.
    require(saved==json.loads(base.inventory.bounded_json(expected)),"Every saved selection mapping, count, volume, role and byte hash must replay exactly.")
    return {**saved,"reportFile":row}


def selection_file_records(report):
    """Exact immutable source/selected/map/report inventory for downstream copy."""
    return [*report["sourceNativeFileRecords"],*report["selectedNativeFileRecords"],
            *report["nativeAddressingFileRecords"],report["nativeSplitLogFile"],report["reportFile"]]


def native_mesh(case,expected_components,text):
    """Scan every actual native primitive, zone and source boundary ownership."""
    case=base.regular_directory(case);mesh=case/"constant/polyMesh";quality=check_log(text)
    expected_zones={row["cellZone"]:row for row in expected_components}
    patch_types={name:kind for row in expected_components for name,kind in row["patchTypes"].items()}
    patch_zone={name:row["cellZone"] for row in expected_components for name in row["patchTypes"]}
    require(len(patch_types)==sum(len(row["patchTypes"]) for row in expected_components),"Exact source patch identities must remain distinct.")
    n_points,body=base.native_list(mesh/"points");points=[];end=0
    require(0<n_points<MAX_COMBINED_POINTS,"The actual native point count must remain below one million.")
    for match in re.finditer(r"\(([^()]*)\)",body):
        require(not body[end:match.start()].strip(),"Every actual native point requires a complete vector.")
        words=match[1].split()
        require(len(words)==3 and all(re.fullmatch(surface.FLOAT,value) for value in words),"Actual native coordinates require three plain numeric values.")
        xyz=tuple(map(float,words));require(all(math.isfinite(value) for value in xyz),"All actual native points must be finite.")
        points.append(xyz);end=match.end()
    require(not body[end:].strip() and len(points)==n_points,"Every declared actual native point must be present.")
    n_faces,owner_body=base.native_list(mesh/"owner");n_internal,neighbour_body=base.native_list(mesh/"neighbour")
    def labels(value,count):
        words=value.split()
        require(len(words)==count and all(re.fullmatch(r"\d+",word) for word in words),"Native label lists must cover their exact declared nonnegative count.")
        return list(map(int,words))
    owners=labels(owner_body,n_faces);neighbours=labels(neighbour_body,n_internal)
    require(0<=n_internal<n_faces and owners,"A complete actual finite-volume mesh with a physical boundary is required.")
    n_cells=max(owners+neighbours)+1
    require(0<n_cells<MAX_COMBINED_CELLS and set(owners)|set(neighbours)==set(range(n_cells))
            and all(owners[i]<neighbours[i] for i in range(n_internal)),"Every bounded actual cell must retain ordered valid native owner/neighbour addressing.")
    if len(expected_components)==1:
        require(n_cells<=MAX_COMPONENT_CELLS,"The measured component cell count exceeds the declared 450,000-cell cap.")
    require(quality["counts"]=={"points":n_points,"faces":n_faces,"internal faces":n_internal,"cells":n_cells},
            "Actual checkMesh statistics must match every native primitive count.")
    n_zones,zone_body=native_zones(mesh/"cellZones")
    zone_entries=re.findall(r'"?([A-Za-z0-9_-]+)"?\s*\{([^{}]*)\}',zone_body)
    require(n_zones==len(zone_entries)==len(expected_zones) and {name for name,_ in zone_entries}==set(expected_zones)
            and not re.sub(r'"?[A-Za-z0-9_-]+"?\s*\{[^{}]*\}',"",zone_body).strip(),
            "The exact rotating/stationary fluid cell-zone identities are required.")
    zone_labels={};all_labels=set()
    for name,values in zone_entries:
        match=re.search(r"\bcellLabels\s+(?:List<label>\s+)?(\d+)\s*\(([^()]*)\)\s*;",values,re.S)
        require(match and re.search(r"\btype\s+cellZone\s*;",values),"Each actual native zone requires typed counted cell labels.")
        values=labels(match[2],int(match[1]));unique=set(values)
        require(values and len(values)==len(unique) and min(unique)>=0 and max(unique)<n_cells and not unique&all_labels,
                "Native fluid zones must be nonempty, complete and disjoint.")
        if "cellCount" in expected_zones[name]:require(len(values)==expected_zones[name]["cellCount"],"Merged zones must retain the measured component cell counts.")
        zone_labels[name]=unique;all_labels|=unique
    require(all_labels==set(range(n_cells)),"The actual fluid zones must cover every native cell.")
    patches=boundary_entries(mesh/"boundary");start=n_internal;ranges={};extras={}
    if len(expected_components)==2:
        extras={"pumpInterface_on_"+ROTOR:("nonConformalCyclic",ROTOR,"pumpInterface_on_"+STATOR),
                "pumpInterface_on_"+STATOR:("nonConformalCyclic",STATOR,"pumpInterface_on_"+ROTOR),
                "nonConformalError_on_"+ROTOR:("nonConformalError",ROTOR,None),
                "nonConformalError_on_"+STATOR:("nonConformalError",STATOR,None)}
    for name,row in patches.items():
        require(row["startFace"]==start,"Actual native patch face ranges must be contiguous.")
        if name in patch_types:
            require(row["type"]==patch_types[name] and row["nFaces"]>0,"Every original patch name and physical role must retain actual nonempty faces.")
            expected_count=next((item["patchFaces"][name] for item in expected_components if "patchFaces" in item and name in item["patchFaces"]),None)
            if expected_count is not None:require(row["nFaces"]==expected_count,"Merged native patch face counts must retain actual component counts.")
            require(all(owner in zone_labels[patch_zone[name]] for owner in owners[start:start+row["nFaces"]]),
                    "Every source patch must retain ownership in its declared rotating/stationary component.")
            ranges[name]=(start,row["nFaces"])
        elif name=="backgroundBox":
            require(row["type"]=="patch" and row["nFaces"]==0,"No positive background-box faces may remain in the generated fluid domain.")
        elif name in extras:
            kind,original,opposite=extras[name];body=row["dictionary"]
            require(row["type"]==kind and row["nFaces"]==0 and re.search(r"\boriginalPatch\s+"+original+r"\s*;",body),"The exact native NCC/error placeholders must retain their original interface.")
            if opposite:require(re.search(r"\bneighbourPatch\s+"+opposite+r"\s*;",body) and re.search(r"\btransformType\s+none\s*;",body),"The actual NCC pair must be reciprocal with no transform.")
        else:raise surface.SurfaceCheckIssue("Unassigned or unexpected native boundary patch: "+name)
        start+=row["nFaces"]
    require(start==n_faces and set(patch_types)|set(extras)<=set(patches),"Every boundary face, original patch and required NCC placeholder must be accounted for.")
    if len(expected_components)==2:
        rotating=zone_labels["rotating-fluid"]
        require(all((owners[i] in rotating)==(neighbours[i] in rotating) for i in range(n_internal)),"The separate component volumes cannot gain unproved internal faces across their NCC interface.")
    elif len(expected_components)==1:require(quality["polyMeshConnectedRegionCount"]==1,"This declared single connected component mesh must retain one actual fluid volume region.")
    # Integrate measured native polygon area vectors and boundary fan volume;
    # these diagnostics do not bound surface distances or passage clearances.
    origin=tuple((min(p[i] for p in points)+max(p[i] for p in points))/2 for i in range(3))
    patch_area={name:[] for name in ranges};patch_points={name:set() for name in ranges};volume_terms=[]
    face_to_patch={index:name for name,(first,count) in ranges.items() for index in range(first,first+count)}
    declared,faces_body=base.native_list(mesh/"faces");seen,end=0,0
    require(declared==n_faces,"Actual face and owner counts must agree.")
    for match in re.finditer(r"(\d+)\s*\(([^()]*)\)",faces_body):
        require(not faces_body[end:match.start()].strip(),"Every native face must be a complete counted polygon.")
        ids=labels(match[2],int(match[1]))
        require(len(ids)>=3 and len(set(ids))==len(ids) and max(ids)<n_points,"Every actual face requires distinct in-range native vertices.")
        if seen in face_to_patch:
            name=face_to_patch[seen];vertices=[points[index] for index in ids]
            patch_points[name].update(ids);vectors=[]
            a=tuple(vertices[0][i]-origin[i] for i in range(3))
            for index in range(1,len(vertices)-1):
                b=tuple(vertices[index][i]-origin[i] for i in range(3));c=tuple(vertices[index+1][i]-origin[i] for i in range(3))
                ab=tuple(b[i]-a[i] for i in range(3));ac=tuple(c[i]-a[i] for i in range(3))
                cross=(ab[1]*ac[2]-ab[2]*ac[1],ab[2]*ac[0]-ab[0]*ac[2],ab[0]*ac[1]-ab[1]*ac[0]);vectors.append(cross)
                bc=(b[1]*c[2]-b[2]*c[1],b[2]*c[0]-b[0]*c[2],b[0]*c[1]-b[1]*c[0]);volume_terms.append(math.fsum(a[i]*bc[i] for i in range(3))/6)
            area=.5*math.hypot(*(math.fsum(vector[i] for vector in vectors) for i in range(3)))
            require(math.isfinite(area) and area>0,"Every actual source-bound boundary polygon must have finite positive area.")
            patch_area[name].append(area)
        seen+=1;end=match.end()
    require(seen==n_faces and not faces_body[end:].strip(),"Every actual native face must be present.")
    signed_volume=math.fsum(volume_terms)
    require(math.isfinite(signed_volume) and signed_volume>0,"The actual generated outward boundary integration must have finite positive volume.")
    measures={name:{"polygonAreaVectorMagnitudeAssumedM2":math.fsum(values),
        "coordinateBoundsAssumedM":{"minimum":[min(points[index][i] for index in patch_points[name]) for i in range(3)],
        "maximum":[max(points[index][i] for index in patch_points[name]) for i in range(3)]}} for name,values in patch_area.items()}
    file_records=[{**record(case,"constant/polyMesh/"+name)[1],"path":case.name+"/constant/polyMesh/"+name} for name in MESH_FILES]
    result={"nodeCount":n_points,"cellCount":n_cells,"faceCount":n_faces,"internalFaceCount":n_internal,
        "cellZones":{name:len(values) for name,values in zone_labels.items()},"patchFaces":{name:count for name,(_,count) in ranges.items()},
        "patchMeasures":measures,"signedBoundaryFanVolumeAssumedM3":signed_volume,"nativeFiles":file_records,
        "nativeQuality":quality,"nativeNodeIdentityEquivalent":False,"geometricApproximationQualified":False,
        "surfaceDistanceBoundsVerified":False,"passageClearanceBoundsVerified":False,"meshConvergenceVerified":False,
        "actualCfdRunVerified":False,"simulationExecuted":False}
    if len(expected_components)==1:result.update(component=expected_components[0]["component"],caseDirectory=case.name,cellZone=expected_components[0]["cellZone"],patchTypes=patch_types)
    else:result["nativeCoupledCoverage"]=coupled_coverage(text)
    return result


def coupled_coverage(text):
    pattern=(r"Source min/average/max coverage = ("+surface.FLOAT+r")/("+surface.FLOAT+r")/("+surface.FLOAT+r")\s+"
             r"Target min/average/max coverage = ("+surface.FLOAT+r")/("+surface.FLOAT+r")/("+surface.FLOAT+r")")
    matches=re.findall(pattern,text)
    require(matches,"Actual native source/target NCC coverage statistics are required.")
    result=[]
    for match in matches:
        values=list(map(float,match))
        for triple in (values[:3],values[3:]):
            require(all(math.isfinite(value) for value in triple) and .99<=triple[0]<=triple[1]<=triple[2]<=1.01 and triple[1]>=.999,
                    "The unchanged native NCC min0.99/mean0.999/max1.01 coverage criteria must pass.")
        result.append({"source":values[:3],"target":values[3:]})
    return result


def approximation(measured,source_component):
    require(measured["component"]==source_component["component"],"Compare approximation metrics only for the same source component.")
    patches=[]
    for row in source_component["patches"]:
        native_area=measured["patchMeasures"][row["name"]]["polygonAreaVectorMagnitudeAssumedM2"]
        reference=row["surfaceAreaNativeUnitsSquared"]
        require(math.isfinite(reference) and reference>0,"Actual source patch area must be finite and positive.")
        patches.append({"name":row["name"],"sourceStlTriangleAreaAssumedM2":reference,"nativePolygonAreaAssumedM2":native_area,
                        "relativeAreaDifference":(native_area-reference)/reference,"nativeBoundsAssumedM":measured["patchMeasures"][row["name"]]["coordinateBoundsAssumedM"]})
    reference=abs(source_component["signedTriangleVolumeNativeUnitsCubed"])
    require(math.isfinite(reference) and reference>0,"The declared stored boundary-volume reference must be finite and nonzero.")
    volume=measured["nativeQuality"]["totalNativeCellVolumeAssumedM3"]
    return {"component":measured["component"],"input":{"sourceSurfaceFile":source_component["surfaceFile"],"sourceCoordinateUnits":"UNVERIFIED","classroomMetresPerSourceCoordinateUnit":1},
        "equation":"delta_A/A_source = (A_native-A_source)/A_source; delta_V/V_source = (V_native-abs(V_source_signed))/abs(V_source_signed)",
        "substitution":{"sourceStoredSignedTriangleVolumeNativeUnitsCubed":source_component["signedTriangleVolumeNativeUnitsCubed"],"nativeCellVolumeAssumedM3":volume},
        "result":{"relativeVolumeDifference":(volume-reference)/reference,"nativeBoundaryFanVolumeAssumedM3":measured["signedBoundaryFanVolumeAssumedM3"],"patches":patches},
        "geometricApproximationQualified":False,"surfaceDistanceBoundsVerified":False,"passageClearanceBoundsVerified":False,
        "claimBoundary":"Aggregate area, volume and native patch bounds only; these do not bound every surface deviation, passage clearance or original HDF5 float64 geometry."}


def snapshot_component(output,measured):
    """Retain exact checked component primitives before native merge overwrites them."""
    rows=[]
    for expected in measured["nativeFiles"]:
        raw,row=record(output,expected["path"])
        require(row==expected,"A checked component native primitive changed before its snapshot.")
        relative="component-mesh-snapshots/"+measured["component"]+"/constant/polyMesh/"+Path(row["path"]).name
        base.write_exclusive(Path(output)/relative,raw)
        rows.append({**row,"path":relative})
        surface.workspace_size(output)
    require({Path(row["path"]).name for row in rows}==set(MESH_FILES),"Retain exactly six hash-bound native component primitives.")
    return rows


def bound_records(output,verified):
    report=verified["report"];rows={}
    require(report["schemaVersion"]==PREPARATION_SCHEMA and report["nativeCommandArguments"]==commands(output,report["components"]),"The exact reviewed geometry-only preparation and fifteen native arguments are required.")
    for key in ("authoredFileRecords","sourceSurfaceFileRecords","sourceProofFileRecords"):
        require(isinstance(report[key],list) and report[key],"Bind every actual authored geometry and source-proof file before native execution.")
        for row in report[key]:
            _,actual=record(output,row["path"])
            require(actual==row and (row["path"] not in rows or rows[row["path"]]==row),"An authored input/proof byte binding is missing, duplicate or inconsistent.")
            rows[row["path"]]=actual
    row=verified["reportFile"]
    require(row["path"]=="snappy-preparation.json" and record(output,row["path"],base.inventory.MAX_REPORT_BYTES)[1]==row,"The actual preparation report must be byte-bound.")
    rows[row["path"]]=row
    return rows


def run(surface_union_directory,surface_check_directory,output_directory):
    started=time.monotonic();deadline=started+MAX_SECONDS
    surface.context_guard()
    source=surface.scope_path(surface_union_directory);proof=surface.scope_path(surface_check_directory)
    output=surface.scope_path(output_directory,fresh=True)
    import frozen_rotor_surface_union as union
    import frozen_rotor_snappy_case as preparation
    source_geometry=union.verify_output(source)
    preparation.prepare(source,proof,output)
    verified=preparation.verify_output(output);output=base.regular_directory(output)
    bound=bound_records(output,verified);components=verified["components"]
    require([row["component"] for row in components]==list(COMPONENTS),"Require the two complete source pump components in their fixed order.")
    report={"schemaVersion":SCHEMA,"githubRepository":"simcalc/PyAnsys","githubRunId":os.environ.get("GITHUB_RUN_ID"),
        "githubSourceCommit":os.environ.get("GITHUB_SHA"),"status":"APPROXIMATE_NATIVE_MESH_REQUIRED","stages":[],
        "limits":{"maximumSeconds":MAX_SECONDS,"maximumCpus":1,"maximumMemoryBytes":surface.MAX_MEMORY,
        "maximumWorkspaceBytes":surface.MAX_WORKSPACE,"maximumLogBytes":MAX_LOG,"maximumActualComponentCells":MAX_COMPONENT_CELLS,
        "combinedCellsStrictlyBelow":MAX_COMBINED_CELLS,"combinedPointsStrictlyBelow":MAX_COMBINED_POINTS},
        "preparationReportFile":verified["reportFile"],"components":[],"geometricApproximationMetrics":[],"regionSelectionEvidence":[],
        "volumeMeshGenerated":False,"meshQualityCheckedBySolver":False,"nativeCoupledCoverageVerified":False,
        "inputArtifactsImmutable":False,"fullGeometryQualification":False,"geometricApproximationQualified":False,
        "surfaceDistanceBoundsVerified":False,"passageClearanceBoundsVerified":False,"nativeCoordinateUnitsVerified":False,
        "nativeNodeIdentityEquivalent":False,"classroomMetresPerSourceCoordinateUnit":1,"meshConvergenceVerified":False,
        "numericalBaselineQualified":False,"qualifiedBaseline":False,"npsh3Qualified":False,"simulationExecuted":False,
        "actualCfdRunVerified":False,"mainApplicationWrites":False,
        "claimBoundary":"A geometry-only snappyHexMesh approximation candidate. Actual mesh counts, native quality and NCC coverage are separate from rigorous geometry deviation, wall resolution, mesh convergence, flow solutions or NPSH/transient performance."}
    (output/"logs").mkdir();current="runtime-identity"
    try:
        runtime=runtime_identity(deadline);base.write_exclusive(output/"snappy-runtime-identity.json",base.inventory.bounded_json(runtime))
        runtime_row=record(output,"snappy-runtime-identity.json",base.inventory.MAX_REPORT_BYTES)[1]
        report["runtimeIdentityFile"]=runtime_row
        for index,arguments in enumerate(commands(output,components)):
            current=str(index)+":"+arguments[0]
            component=components[0 if index<6 else 1] if index<12 else None
            raw_region_rows=snapshot_region_source(output,component) if arguments[0]=="splitMeshRegions" else []
            stage,text=execute(arguments,output,deadline,runtime,index);report["stages"].append(stage)
            require(stage["exitCode"]==0,"An actual geometry utility returned native exit "+str(stage["exitCode"]))
            completed_log(text)
            if arguments[0]=="splitMeshRegions":
                require(all(record(output,row["path"])[1]==row for row in raw_region_rows),"An immutable raw native mesh or cellLevel changed during region selection.")
                selection=retain_region_selection(output,component,text)
                require(selection["sourceNativeFileRecords"]==raw_region_rows,"The region-selection source byte bindings must remain exact.")
                report["regionSelectionEvidence"].append(selection)
            elif arguments[0]=="checkMesh" and index in (5,11):
                component=components[0 if index==5 else 1]
                measured=native_mesh(output/component["caseDirectory"],[component],text)
                measured["regionSelectionEvidence"]=report["regionSelectionEvidence"][0 if index==5 else 1]
                selected_by_role={Path(row["path"]).name:row for row in measured["regionSelectionEvidence"]["selectedNativeFileRecords"]}
                require(all(row["bytes"]==selected_by_role[Path(row["path"]).name]["bytes"]
                            and row["sha256"]==selected_by_role[Path(row["path"]).name]["sha256"]
                            for row in measured["nativeFiles"] if Path(row["path"]).name in SELECTION_FILES),
                        "Every checked native primitive must remain byte-identical to its verified selected snapshot before merge.")
                measured["nativeMeshSnapshotRecords"]=snapshot_component(output,measured)
                measured["nativeFilesMeasuredAtStageIndex"]=index
                measured["nativeFilesImmutableAfterMerge"]=False
                report["components"].append(measured)
                source_component=source_geometry["components"][0 if index==5 else 1]
                report["geometricApproximationMetrics"].append(approximation(measured,source_component))
                require(sum(row["cellCount"] for row in report["components"])<MAX_COMBINED_CELLS
                        and sum(row["nodeCount"] for row in report["components"])<MAX_COMBINED_POINTS,
                        "Actual combined component cells and points must remain strictly below one million.")
                if len(report["components"])==2:report["volumeMeshGenerated"]=True
            elif index==14:
                merged=native_mesh(output/"impeller-case",report["components"],text)
                require(merged["cellCount"]==sum(row["cellCount"] for row in report["components"])
                        and merged["nodeCount"]==sum(row["nodeCount"] for row in report["components"]),
                        "Native merge must retain the exact measured component cell and point counts.")
                report.update(mergedNativeMeshEvidence=merged,meshQualityCheckedBySolver=True,nativeCoupledCoverageVerified=True)
        require(all(record(output,path)[1]==row for path,row in bound.items()) and record(output,runtime_row["path"])[1]==runtime_row,
                "An authored native input, source proof or runtime identity changed during geometry execution.")
        require(all(record(output,row["path"])[1]==row for component in report["components"] for row in component["nativeMeshSnapshotRecords"]),
                "An immutable checked pre-merge component mesh snapshot changed during the remaining native stages.")
        require([verify_region_selection(output,component) for component in components]==report["regionSelectionEvidence"],
                "Replay immutable native region-selection maps and geometry after all remaining stages.")
        preparation.verify_output(output);union.verify_output(source)
        require(time.monotonic()<deadline,"The serial geometry deadline was reached before final candidate verification.")
        surface.workspace_size(output)
        report.update(inputArtifactsImmutable=True,status="APPROXIMATE_NATIVE_MESH_CHECKED_GEOMETRY_REVIEW_REQUIRED")
    except Exception as error:
        report.update(status="APPROXIMATE_NATIVE_MESH_CORRECTION_REQUIRED",meshQualityCheckedBySolver=False,nativeCoupledCoverageVerified=False,
                      diagnostic={"stage":current,"type":type(error).__name__,"message":str(error)[:500]})
    try:
        report["retainedNativeLogRecords"]=[surface.descriptor(path,"logs/"+path.name,MAX_LOG,empty=True)[1]
            for path in sorted((output/"logs").glob("*.log"))]
        report["workspaceBytes"]=surface.workspace_size(output)
    except Exception as error:
        report.update(status="APPROXIMATE_NATIVE_MESH_RETENTION_CORRECTION_REQUIRED",meshQualityCheckedBySolver=False,nativeCoupledCoverageVerified=False,
                      diagnostic={"stage":"retention","type":type(error).__name__,"message":str(error)[:500]})
    report["elapsedSeconds"]=round(time.monotonic()-started,3)
    base.write_exclusive(output/"snappy-execution.json",base.inventory.bounded_json(report))
    visible={key:report[key] for key in ("status","volumeMeshGenerated","meshQualityCheckedBySolver","geometricApproximationQualified","simulationExecuted","mainApplicationWrites")}
    if "diagnostic" in report:
        visible["diagnostic"]=report["diagnostic"]
        visible["correctiveAction"]="Review the retained actual native log and geometry/mapping diagnostic, correct the declared mesh preparation, and rerun the same unchanged native acceptance checks."
    print(json.dumps(visible))
    return 0 if report["inputArtifactsImmutable"] and report["meshQualityCheckedBySolver"] and report["nativeCoupledCoverageVerified"] else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface-union-directory",type=Path,required=True)
    parser.add_argument("--surface-check-directory",type=Path,required=True)
    parser.add_argument("--output-directory",type=Path,required=True)
    args=parser.parse_args()
    try:return run(args.surface_union_directory,args.surface_check_directory,args.output_directory)
    except Exception as error:
        print(json.dumps({"status":"APPROXIMATE_NATIVE_MESH_INPUT_CORRECTION_REQUIRED","action":str(error)[:500],
            "volumeMeshGenerated":False,"actualCfdRunVerified":False,"mainApplicationWrites":False}));return 2


if __name__=="__main__":raise SystemExit(main())
