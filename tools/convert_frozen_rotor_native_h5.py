"""Preserve two pinned original Fluent volume meshes as native OpenFOAM meshes.

No remeshing, node welding, face triangulation, solver or Main operation occurs.
Stored mesh winding points toward Fluent c0. The derived owner points outward
and into neighbour, with every original polygon, coordinate and cell retained.
Native checkMesh, NCC coupling and solver acceptance remain independent gates.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
import extract_frozen_rotor_boundaries as source
import frozen_rotor_cavitation_case as guards
import inspect_frozen_rotor_mesh as inventory

SCHEMA="untirta.frozen-rotor-original-polyMesh-conversion.v1"
MAX_CELLS=2_000_000
MAX_NODES=1_000_000
MAX_FACES=5_000_000
MAX_CONNECTIVITY=32_000_000
MAX_FILE=128*1024**2
MAX_OUTPUT=512*1024**2
MAX_MEMORY=4*1024**3
AUDIT_CELLS=20_000
MESH_FILES={"points","faces","owner","neighbour","boundary","cellZones"}
COMPONENTS={"impeller":("impeller.msh.h5","rotating-fluid",36),"volute":("volute.msh.h5","stationary-fluid",2)}
# The separate read-only inventory of these exact immutable pins established
# these ranges. They are not a new mesh target or a Student licence allowance.
SOURCE_COUNTS={"impeller":{"cellCount":773352,"nodeCount":837936,"faceCount":2383488,"internalFaceCount":2256624},
               "volute":{"cellCount":368411,"nodeCount":122551,"faceCount":831758,"internalFaceCount":791841}}


class ConversionIssue(ValueError):pass


def require(condition,action):
    if not condition:raise ConversionIssue(action)


def encoded(value):return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode("ascii")


def read_mesh(path,component):
    """Validate immutable pins and complete HDF5 ranges before any derivation."""
    import h5py
    import numpy as np
    require(component in COMPONENTS,"Select one of the exact two official source components.")
    source_file=inventory.verify_file(Path(path),COMPONENTS[component][0])
    with h5py.File(path,"r") as handle:
        inventory._scan_objects(handle,h5py)  # No external/soft links or external dataset storage.
        require(set(handle["/meshes"].keys())=={"1"},"Only the observed single-domain native mesh is supported.")
        mesh=handle["/meshes/1"]
        require(set(mesh.keys())=={"cells","faces","nodes"},"Unsupported native hierarchy/periodic/adaptation groups must be reviewed explicitly.")
        topology={kind:inventory._topology(handle,kind) for kind in ("cells","faces","nodes")}
        counts={kind:topology[kind]["count"] for kind in topology}
        require(all(counts[kind]==SOURCE_COUNTS[component][key] for kind,key in (("cells","cellCount"),("faces","faceCount"),("nodes","nodeCount"))),
                "The source-pinned entity counts must match the independently inspected original volume inventory.")
        for kind,bound in (("cells",MAX_CELLS),("faces",MAX_FACES),("nodes",MAX_NODES)):
            require(topology[kind]["countStatus"]=="ConfirmedFromStoredRanges" and topology[kind]["namesMatchZoneCount"]
                    and 0<counts[kind]<=bound,"Every original native entity range must be complete and bounded: "+kind)
            require(source._attribute_int(mesh,{"cells":"cellCount","faces":"faceCount","nodes":"nodeCount"}[kind])==counts[kind],
                    "Declared native count disagrees with its complete zone ranges.")
        for key in ("cellOffset","faceOffset","nodeOffset"):
            value=mesh.attrs.get(key)
            require(value is not None and getattr(value,"size",0)==1 and getattr(getattr(value,"dtype",None),"kind","") in ("i","u") and int(value.reshape(-1)[0])==0,
                    "Only the observed zero-offset global native entity IDs are supported.")
        require(source._attribute_int(mesh,"dimension")==3,"A complete three-dimensional volume is required.")
        zones=topology["cells"]["zones"]
        require(len(zones)==1 and zones[0]["name"]==component and zones[0]["zoneId"]==COMPONENTS[component][2],
                "Preserve the exact original educational fluid cell-zone identity.")
        cell_child=topology["cells"]["rawDatasets"]["childZoneId"]["rawValues"]
        require(cell_child==[0],"Native child/adapted cells require a separate explicit importer.")
        require(set(mesh["cells"].keys())=={"ctype","zoneTopology"} and set(mesh["faces"].keys())=={"nodes","c0","c1","zoneTopology"},
                "Unsupported parent faces/cells, periodic links or stored intersection faces must not be silently discarded.")
        sections={}
        for label,path_name,total,options in (("coordinates","nodes/coords",counts["nodes"],{"coordinate":True}),
                ("faceNodes","faces/nodes",counts["faces"],{"face_nodes":True}),
                ("c0","faces/c0",counts["faces"],{}),("c1","faces/c1",counts["faces"],{})):
            sections[label]=source.checked_range_sections(source._section_rows(mesh[path_name],h5py,**options),total)
        adjacency={key:{(row["minimumId"],row["maximumId"]):row for row in sections[key]} for key in ("faceNodes","c0","c1")}
        require(set(adjacency["faceNodes"])==set(adjacency["c0"])==set(adjacency["c1"]),"Every native polygon and adjacency section requires matching complete global ranges.")
        xyz=np.empty((counts["nodes"],3),dtype="f8")
        for row in sections["coordinates"]:
            for start in range(0,row["recordCount"],4096):
                values=handle[row["path"]][start:start+4096,:]
                require(np.isfinite(values).all(),"Every original coordinate must be finite.")
                low=row["minimumId"]-1+start;xyz[low:low+len(values)]=values
        widths=np.empty(counts["faces"],dtype="u2");c0=np.empty(counts["faces"],dtype="u4");c1=np.empty(counts["faces"],dtype="u4")
        total=0
        for row in sections["faceNodes"]:
            low,high=row["minimumId"]-1,row["maximumId"];group=handle[row["path"]]
            values=group["nnodes"][()]
            declared=group["nodes"].shape[0]
            require(np.all((values>=3)&(values<=128)) and int(values.sum(dtype="i8"))==declared and total+declared<=MAX_CONNECTIVITY,
                    "Native complete polygon connectivity must be bounded and match every face width before allocation.")
            widths[low:high]=values;total+=declared
            for key,array in (("c0",c0),("c1",c1)):
                values=handle[adjacency[key][(row["minimumId"],row["maximumId"])]["path"]][()]
                require(np.all((values>=0)&(values<=counts["cells"])),"Every stored adjacent-cell ID must lie in the full original cell range.")
                array[low:high]=values
        require(np.all(c0!=c1),"Each native face needs distinct adjacent cells; a zero/zero surface face is not a volume face.")
        offsets=np.empty(counts["faces"]+1,dtype="i8");offsets[0]=0;np.cumsum(widths,dtype="i8",out=offsets[1:])
        nodes=np.empty(total,dtype="u4")
        for row in sections["faceNodes"]:
            low,high=row["minimumId"]-1,row["maximumId"]
            values=handle[row["path"]+"/nodes"][()]
            require(np.all((values>=1)&(values<=counts["nodes"])),"Every native face node must reference the original complete coordinate map.")
            nodes[offsets[low]:offsets[high]]=values-1
        for first in range(0,counts["faces"],20_000):
            last=min(first+20_000,counts["faces"])
            keys=(np.repeat(np.arange(first,last,dtype="u8"),widths[first:last])<<20)|nodes[offsets[first]:offsets[last]].astype("u8")
            require(len(np.unique(keys))==len(keys),"Every original polygon needs distinct node IDs; do not repair repeated vertices implicitly.")
        used=np.zeros(counts["nodes"],dtype=bool);used[nodes]=True
        require(used.all(),"An unused original node needs an explicitly reviewed remapping; do not drop it silently.")
        ctypes=np.empty(counts["cells"],dtype="u1");ctype=mesh["cells/ctype"]
        require(1<=len(ctype)<=128 and source._attribute_int(ctype,"nSections")==len(ctype),"Every native cell-type section must be retained.")
        type_rows=[]
        for name in ctype:
            require(re.fullmatch(r"[1-9]\d*",name),"Native cell-type sections must retain numeric source IDs.")
            group=ctype[name];low=source._attribute_int(group,"minId");high=source._attribute_int(group,"maxId")
            require(counts["cells"]>=high>=low,"Native cell-type range must remain within its complete original cell inventory.")
            native_type=group.attrs.get("elementType")
            require(native_type is not None and getattr(native_type,"size",0)==1 and getattr(getattr(native_type,"dtype",None),"kind","") in ("i","u"),
                    "An explicit scalar integral native cell type is required.")
            kind=int(native_type.reshape(-1)[0])
            if kind==0:
                require(set(group.keys())=={"cell-types"},"A mixed native cell section requires its complete per-cell type dataset.")
                dataset=source._dataset(group["cell-types"],h5py,integer=True,count=high-low+1);values=dataset[()]
            else:
                require(not len(group),"Uniform native cell types cannot contain unreviewed child datasets.");values=np.full(high-low+1,kind,dtype="i2")
            require(np.all(np.isin(values,(2,4,5,6,7))),"Only original 3-D tetra/hex/pyramid/prism/polyhedral types are supported.")
            ctypes[low-1:high]=values;type_rows.append({"minimumId":low,"maximumId":high,"recordCount":high-low+1})
        source.checked_range_sections(type_rows,counts["cells"])
        face_zones=[]
        native_zones=topology["faces"]["zones"];raw=topology["faces"]["rawDatasets"]
        for index,zone in enumerate(native_zones):
            extra={key:raw[key]["rawValues"][index] for key in ("zoneType","faceType","childZoneId","shadowZoneId")}
            require(extra["childZoneId"]==extra["shadowZoneId"]==0 and extra["zoneType"] in (2,3,4,20,24),
                    "Review native hanging/shadow/periodic or intersected face-zone semantics before conversion.")
            require(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}",zone["name"]),"Preserve safe exact original zone names without guessing replacements.")
            low,high=zone["minimumId"]-1,zone["maximumId"];exterior=(c0[low:high]==0)^(c1[low:high]==0)
            require((not exterior.any()) if extra["zoneType"]==2 else exterior.all(),"The actual adjacency must match each original interior or boundary zone.")
            if extra["faceType"] not in (0,5):require(np.all(widths[low:high]==extra["faceType"]),"Native face type disagrees with polygon widths.")
            face_zones.append({**zone,**extra})
    return {"component":component,"sourceFile":source_file,"topology":topology,"sections":sections,"coordinates":xyz,
            "faceWidths":widths,"faceOffsets":offsets,"faceNodes":nodes,"c0":c0,"c1":c1,"cellTypes":ctypes,"faceZones":face_zones,
            "cellCount":counts["cells"],"nodeCount":counts["nodes"],"faceCount":counts["faces"]}


def face_order(model):
    import numpy as np
    c0,c1=model["c0"],model["c1"];interior=np.flatnonzero((c0>0)&(c1>0)).astype("u4")
    owner=np.where(c0==0,c1,np.where(c1==0,c0,np.minimum(c0,c1))).astype("u4")-1
    neighbour=np.maximum(c0[interior],c1[interior])-1
    interior=interior[np.lexsort((interior,neighbour,owner[interior]))]
    order=[interior];patches=[];cursor=len(interior)
    for zone in model["faceZones"]:
        if zone["zoneType"]==2:continue
        ids=np.arange(zone["minimumId"]-1,zone["maximumId"],dtype="u4")
        require(np.all((c0[ids]==0)^(c1[ids]==0)),"Every native boundary polygon requires exactly one original owning cell.")
        patches.append({"name":zone["name"],"type":"wall" if zone["zoneType"]==3 else "patch","nFaces":len(ids),"startFace":cursor,
            "sourceZoneId":zone["zoneId"],"sourceMinimumFaceId":zone["minimumId"],"sourceMaximumFaceId":zone["maximumId"],
            "reversedFaceCount":int(np.count_nonzero(c0[ids]==owner[ids]+1))})
        cursor+=len(ids);order.append(ids)
    ordered=np.concatenate(order)
    require(cursor==model["faceCount"] and len(ordered)==model["faceCount"] and len(np.unique(ordered))==model["faceCount"],
            "Every original face must be transferred exactly once, with internal faces before exact contiguous patches.")
    reverse=c0==owner+1
    return ordered,owner,np.maximum(c0[interior],c1[interior])-1,reverse,patches


def audit_cells(model):
    """Compact CSR/chunk audit of every original cell, without Python edge maps."""
    import numpy as np
    count=model["cellCount"];c0,c1=model["c0"],model["c1"]
    ix0=np.flatnonzero(c0>0).astype("u4");ix1=np.flatnonzero(c1>0).astype("u4")
    cells=np.concatenate((c0[ix0]-1,c1[ix1]-1));faces=np.concatenate((ix0,ix1))
    directions=np.concatenate((np.full(len(ix0),-1,dtype="i1"),np.full(len(ix1),1,dtype="i1")))
    sort=np.lexsort((faces,cells));faces=faces[sort];directions=directions[sort]
    degree=np.bincount(cells,minlength=count);del ix0,ix1,cells,sort
    require(np.all(degree>=4),"Every original volume cell requires at least four faces.")
    for kind,nfaces in ((2,4),(4,6),(5,5),(6,5)):
        require(np.all(degree[model["cellTypes"]==kind]==nfaces),"Original cell type and complete adjacent-face incidence disagree.")
    offsets=np.empty(count+1,dtype="i8");offsets[0]=0;np.cumsum(degree,dtype="i8",out=offsets[1:])
    xyz=model["coordinates"];node_ids=model["faceNodes"];fo=model["faceOffsets"];width=model["faceWidths"]
    volume_min=math.inf;volume_max=0.;volume_total=0.;closure_max=0.;cells_checked=0
    for first in range(0,count,AUDIT_CELLS):
        last=min(first+AUDIT_CELLS,count);lo,hi=offsets[first],offsets[last]
        incidence=faces[lo:hi];sign=directions[lo:hi];edge_counts=width[incidence].astype("i8")
        nedge=int(edge_counts.sum());require(nedge<=8_000_000,"One bounded cell audit chunk cannot exceed its explicit edge budget.")
        ci=np.repeat(np.arange(first,last,dtype="u4"),degree[first:last])
        edge_cell=np.repeat(ci,edge_counts);edge_face=np.repeat(incidence,edge_counts);edge_sign=np.repeat(sign,edge_counts)
        edge_start=np.cumsum(edge_counts)-edge_counts
        local=np.arange(nedge,dtype="i8")-np.repeat(edge_start,edge_counts)
        a=node_ids[fo[edge_face]+local];b=node_ids[fo[edge_face]+(local+1)%width[edge_face]]
        require(np.all(a!=b),"Zero-length native cell edges are prohibited.")
        # Bounded nodes<2^20, cells<2^21 make this exact injective uint64 key.
        keys=(edge_cell.astype("u8")<<40)|(np.minimum(a,b).astype("u8")<<20)|np.maximum(a,b).astype("u8")
        order=np.argsort(keys,kind="stable");ordered=keys[order]
        groups=np.flatnonzero(np.r_[True,ordered[1:]!=ordered[:-1]])
        sizes=np.diff(np.r_[groups,nedge])
        signed=((a<b).astype("i1")*2-1)*edge_sign
        require(np.all(sizes==2) and np.all(np.add.reduceat(signed[order],groups)==0),
                "Every original cell edge must occur exactly twice with opposite outward orientation.")
        # Full unique vertex counts for standard source cell shapes.
        vertex_keys=(edge_cell.astype("u8")<<20)|a.astype("u8");unique=np.unique(vertex_keys)
        vertex_count=np.bincount((unique>>20).astype("i8")-first,minlength=last-first)
        for kind,nverts in ((2,4),(4,8),(5,5),(6,6)):
            require(np.all(vertex_count[model["cellTypes"][first:last]==kind]==nverts),"Original cell type and complete native vertex incidence disagree.")
        starts=offsets[first:last]-lo
        references=xyz[node_ids[fo[incidence[starts]]]]
        origins=references[edge_cell-first]
        va,vb=xyz[a]-origins,xyz[b]-origins;cross=np.cross(va,vb)
        pivot=xyz[node_ids[fo[edge_face]]]-origins
        terms=np.einsum("ij,ij->i",pivot,cross)*edge_sign/6
        cell_edges=np.add.reduceat(edge_counts,starts);edge_offsets=np.r_[0,np.cumsum(cell_edges)[:-1]]
        volumes=np.add.reduceat(terms,edge_offsets)
        area_sum=np.add.reduceat(cross*edge_sign[:,None]/2,edge_offsets,axis=0)
        area_scale=np.add.reduceat(np.linalg.norm(cross,axis=1)/2,edge_offsets)
        require(np.isfinite(volumes).all() and np.all(volumes>0) and np.isfinite(area_sum).all() and np.isfinite(area_scale).all() and np.all(area_scale>0),
                "Every original cell must have finite strictly positive outward divergence volume and finite nonzero area scale.")
        relative=np.linalg.norm(area_sum,axis=1)/area_scale
        require(np.all(relative<=1e-10),"Every original cell must close geometrically to its explicit relative area-vector tolerance.")
        volume_min=min(volume_min,float(volumes.min()));volume_max=max(volume_max,float(volumes.max()));volume_total+=float(volumes.sum())
        closure_max=max(closure_max,float(relative.max()));cells_checked+=len(volumes)
    # An independent exterior integration checks that complete internal faces
    # cancel across their two cell incidences, without triangulating the output.
    exterior=np.flatnonzero((c0==0)^(c1==0));boundary_volume=0.
    reference=xyz.min(axis=0)+(xyz.max(axis=0)-xyz.min(axis=0))/2
    for first in range(0,len(exterior),20_000):
        face=exterior[first:first+20_000];edge_counts=width[face].astype("i8")
        edge_face=np.repeat(face,edge_counts);edge_start=np.cumsum(edge_counts)-edge_counts
        local=np.arange(int(edge_counts.sum()),dtype="i8")-np.repeat(edge_start,edge_counts)
        a=node_ids[fo[edge_face]+local];b=node_ids[fo[edge_face]+(local+1)%width[edge_face]]
        pivot=xyz[node_ids[fo[edge_face]]]-reference;va,vb=xyz[a]-reference,xyz[b]-reference
        sign=np.where(c0[edge_face]>0,-1,1)
        boundary_volume+=float(np.sum(np.einsum("ij,ij->i",pivot,np.cross(va,vb))*sign/6))
    volume_difference=abs(volume_total-boundary_volume)/max(abs(volume_total),abs(boundary_volume))
    require(math.isfinite(boundary_volume) and boundary_volume>0 and math.isfinite(volume_difference) and volume_difference<=1e-10,
            "The sum of all original positive cell volumes must agree with independent outward boundary integration.")
    return {"allOriginalCellsChecked":True,"originalCellCountChecked":cells_checked,"directedEdgeIncidenceClosed":True,
        "standardCellShapeIncidenceVerified":True,"minimumCellVolumeAssumedM3":volume_min,"maximumCellVolumeAssumedM3":volume_max,
        "totalCellVolumeAssumedM3":volume_total,"maximumRelativeAreaVectorClosureError":closure_max,"relativeAreaClosureTolerance":1e-10,
        "boundarySignedVolumeAssumedM3":boundary_volume,"relativeCellBoundaryVolumeDifference":volume_difference,"cellBoundaryVolumeAgreementVerified":True,
        "volumeDefinition":"V_c=sum outward oriented native polygon-fan tetra volumes, with a per-cell vertex as numerical origin. Original polygons are written unchanged, never fan-triangulated in polyMesh.",
        "nativeCoordinateUnitsVerified":False,"classroomMetresPerSourceCoordinateUnit":1,
        "geometricSelfIntersectionChecked":False,"meshQualityCheckedBySolver":False}


class Writer:
    def __init__(self,root,relative,inventory_rows,budget):
        guards.relative_path(relative);self.path=root/relative;self.path.parent.mkdir(parents=True,exist_ok=True)
        guards.base.regular_directory(self.path.parent)
        self.stream=self.path.open("xb");self.sha=sha256();self.total=0;self.relative=relative;self.rows=inventory_rows;self.budget=budget
    def write(self,text):
        raw=text.encode("ascii");require(self.total+len(raw)<=MAX_FILE and self.budget[0]+len(raw)<=MAX_OUTPUT,"The native conversion reached its declared per-file or aggregate output bound.")
        self.stream.write(raw);self.sha.update(raw);self.total+=len(raw);self.budget[0]+=len(raw)
    def close(self):
        self.stream.close();self.rows.append({"path":self.relative,"bytes":self.total,"sha256":self.sha.hexdigest()})


def write_mesh(root,model,checks,budget):
    import numpy as np
    ordered,owner,neighbour,reverse,patches=face_order(model)
    name=model["component"];case_path=name+"-case";prefix=case_path+"/constant/polyMesh/";files=[]
    def emit(file,kind,lines):
        stream=Writer(root,prefix+file,files,budget)
        try:
            stream.write(guards.base.foam_header(file,kind,location="constant/polyMesh"))
            for line in lines:stream.write(line)
        finally:stream.close()
    def point_lines():
        yield str(model["nodeCount"])+"\n(\n"
        for start in range(0,model["nodeCount"],2048):
            values=model["coordinates"][start:start+2048]
            tokens=[[format(float(v),".17g") for v in point] for point in values]
            require(np.array_equal(np.asarray(tokens,dtype="f8"),values),"Every original float64 coordinate must round-trip exactly through native ASCII17-digit output.")
            yield "".join("("+" ".join(point)+")\n" for point in tokens)
        yield ")\n"
    emit("points","vectorField",point_lines())
    def face_lines():
        yield str(model["faceCount"])+"\n(\n"
        for start in range(0,len(ordered),2048):
            lines=[]
            for face in ordered[start:start+2048]:
                ids=model["faceNodes"][model["faceOffsets"][face]:model["faceOffsets"][face+1]]
                if reverse[face]:ids=ids[::-1]
                lines.append(str(len(ids))+"("+" ".join(str(int(v)) for v in ids)+")\n")
            yield "".join(lines)
        yield ")\n"
    emit("faces","faceList",face_lines())
    def label_lines(values):
        yield str(len(values))+"\n(\n"
        for start in range(0,len(values),4096):yield "".join(str(int(v))+"\n" for v in values[start:start+4096])
        yield ")\n"
    emit("owner","labelList",label_lines(owner[ordered]));emit("neighbour","labelList",label_lines(neighbour))
    boundary=[str(len(patches))+"\n(\n"]+[p["name"]+"\n{\ntype "+p["type"]+";\nnFaces "+str(p["nFaces"])+";\nstartFace "+str(p["startFace"])+";\n}\n" for p in patches]+[")\n"]
    emit("boundary","polyBoundaryMesh",iter(boundary))
    role=COMPONENTS[name][1]
    def zones():
        yield "1\n(\n"+role+"\n{\ntype cellZone;\ncellLabels List<label>\n"
        yield from label_lines(range(model["cellCount"]))
        yield ";\n}\n)\n"
    emit("cellZones","regIOobject",zones())
    rows=[];mapping=Writer(root,case_path+"/sourceFaceIds",rows,budget)
    try:
        mapping.write(guards.base.foam_header("sourceFaceIds","labelList"))
        for line in label_lines(ordered+1):mapping.write(line)
    finally:mapping.close()
    kinds,kindcounts=np.unique(model["cellTypes"],return_counts=True)
    return {"name":name,"sourceFile":model["sourceFile"],"casePath":case_path,"cellCount":model["cellCount"],"nodeCount":model["nodeCount"],
        "faceCount":model["faceCount"],"internalFaceCount":len(neighbour),"boundaryFaceCount":sum(p["nFaces"] for p in patches),
        "patches":patches,"cellZone":{"name":role,"sourceName":name,"sourceZoneId":COMPONENTS[name][2],"cellCount":model["cellCount"]},
        "nativeFiles":files,"sourceFaceIdMapFile":rows[0],"checks":{**checks,"allCoordinatesAsciiRoundTripVerified":True,
            "everyOriginalFaceTransferredExactlyOnce":True,"originalPolygonsRetained":True,"ownerNeighbourOrderingVerified":True,
            "internalFaceCount":len(neighbour),"reversedFaceCount":int(np.count_nonzero(reverse))},
        "cellTypeCounts":{str(int(kind)):int(n) for kind,n in zip(kinds,kindcounts)}}


def convert(mesh_directory,output_directory):
    inputs=guards.inside_runner(mesh_directory);destination=Path(output_directory).absolute()
    inventory.output_path(destination/"native-conversion.json")
    require(not destination.exists() and not destination.is_symlink(),"Use a fresh independent RUNNER_TEMP original-volume output directory.")
    sources=[inventory.verify_file(inputs/name,name) for name in inventory.MESH_NAMES]
    destination.mkdir();budget=[0];components=[];report=None
    try:
        for component in COMPONENTS:
            model=read_mesh(inputs/COMPONENTS[component][0],component)
            checks=audit_cells(model);components.append(write_mesh(destination,model,checks,budget));del model
        counts={key:sum(row[key] for row in components) for key in ("cellCount","nodeCount","faceCount")}
        require(counts["cellCount"]<=MAX_CELLS and counts["nodeCount"]<=MAX_NODES and counts["faceCount"]<=MAX_FACES,
                "The combined original volume exceeds the separate declared OpenFOAM worker inventory bound.")
        require([inventory.verify_file(inputs/name,name) for name in inventory.MESH_NAMES]==sources,"Original source bytes changed during conversion.")
        report={"schemaVersion":SCHEMA,"createdAt":datetime.now(timezone.utc).isoformat(),"exampleDataCommit":inventory.PIN,
            "status":"OriginalPolyMeshConvertedNativeAcceptanceRequired","components":components,"combinedCounts":counts,
            "indexMapping":{"nodes":"OpenFOAM point index = original 1-based native node ID minus one; all original float64 coordinates retained.",
                "cells":"OpenFOAM cell index = original 1-based native cell ID minus one; every original cell retained.",
                "faces":"sourceFaceIds[OpenFOAM face index] is the original 1-based native face ID. Internal faces sorted(owner,neighbour,sourceFaceId), followed by exact source boundary zones/native IDs.",
                "winding":"Stored face normal points toward Fluent c0. Owner=min(positive c0,c1), neighbour=max; reverse polygon iff chosen owner is c0. Boundary exterior zero has no OpenFOAM neighbour."},
            "coordinateUnits":"UNVERIFIED","nativeCoordinateUnitsVerified":False,"classroomMetresPerSourceCoordinateUnit":1,
            "limits":{"maximumCombinedCells":MAX_CELLS,"maximumCombinedNodes":MAX_NODES,"maximumCombinedFaces":MAX_FACES,
                "maximumFileBytes":MAX_FILE,"maximumDerivedBytes":MAX_OUTPUT,"maximumWorkerAddressSpaceBytes":MAX_MEMORY,"auditCellChunk":AUDIT_CELLS},
            "derivedBytes":budget[0],"originalMeshesImmutable":True,"volumeMeshConverted":True,"meshQualityCheckedBySolver":False,
            "nativeCaseGenerated":False,"fluentSessionConnected":False,"licenseVerified":False,"simulationExecuted":False,"actualCfdRunVerified":False,
            "studentCellCapMet":False,"studentLicenceRouteUsed":False,"mainApplicationWrites":False,
            "claimBoundary":"Exact original educational volume topology converted under the independent FREE OpenFOAM budget. Cell closure/positive divergence checks are conversion checks, not native checkMesh, NCC coverage, baseline, cavitation, NPSH3 or physical validation. Coordinate metre scale remains an explicit classroom assumption."}
        guards.base.write_exclusive(destination/"native-conversion.json",inventory.bounded_json(report))
        return report
    except Exception:
        # Preserve the failed owned workspace for inspection. Never delete source
        # or publish a success packet for a partial mesh.
        for name in inventory.MESH_NAMES:inventory.verify_file(inputs/name,name)
        raise


def verify_output(converted_directory):
    """Recheck current ordinary output files; no HDF5, solver or source execution."""
    from frozen_rotor_native_reduction import record,read
    root=guards.base.regular_directory(converted_directory);report,report_row=record(root,"native-conversion.json")
    require(report.get("schemaVersion")==SCHEMA and report.get("exampleDataCommit")==inventory.PIN
            and report.get("originalMeshesImmutable") is True and report.get("volumeMeshConverted") is True
            and report.get("meshQualityCheckedBySolver") is False and report.get("simulationExecuted") is False
            and report.get("actualCfdRunVerified") is False and report.get("mainApplicationWrites") is False,
            "A current source-pinned original-volume conversion packet is required.")
    require(report.get("nativeCoordinateUnitsVerified") is False and report.get("coordinateUnits")=="UNVERIFIED"
            and report.get("classroomMetresPerSourceCoordinateUnit")==1 and report.get("nativeCaseGenerated") is False
            and report.get("fluentSessionConnected") is False and report.get("licenseVerified") is False
            and report.get("studentCellCapMet") is False and report.get("studentLicenceRouteUsed") is False,
            "Original conversion must preserve the classroom unit assumption and separate native/license acceptance boundaries.")
    require([item.get("name") for item in report.get("components",[])]==list(COMPONENTS),"Both exact original independent component meshes are required.")
    total=0;components=[]
    for item in report["components"]:
        component=item["name"];file_name,zone_name,zone_id=COMPONENTS[component];source_item=item.get("sourceFile",{})
        expected_bytes,expected_sha=inventory.FILES[file_name]
        require(source_item=={"name":file_name,"bytes":expected_bytes,"sha256":expected_sha,"sourceUrl":inventory.mesh_url(file_name)},"Exact official original source pins must be preserved.")
        require(item.get("casePath")==component+"-case" and item.get("cellZone",{}).get("name")==zone_name
                and item["cellZone"].get("sourceZoneId")==zone_id and item["cellZone"].get("sourceName")==component,"Exact source-to-native fluid zone roles are required.")
        require(all(item.get(key)==expected for key,expected in SOURCE_COUNTS[component].items())
                and item["cellZone"].get("cellCount")==item["cellCount"],"Native original counts must match the independent immutable source inventory.")
        cursor=item["internalFaceCount"];names=set();source_zones=set()
        for patch in item.get("patches",[]):
            require(isinstance(patch.get("nFaces"),int) and not isinstance(patch["nFaces"],bool) and patch["nFaces"]>0
                    and patch.get("startFace")==cursor and patch.get("type") in ("wall","patch")
                    and isinstance(patch.get("name"),str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}",patch["name"])
                    and patch["name"] not in names and patch.get("sourceZoneId") not in source_zones
                    and patch.get("sourceMaximumFaceId",0)-patch.get("sourceMinimumFaceId",0)+1==patch["nFaces"],
                    "Complete exact original boundary zone identities and contiguous output ranges are required.")
            cursor+=patch["nFaces"];names.add(patch["name"]);source_zones.add(patch["sourceZoneId"])
        require(names and cursor==item["faceCount"] and item.get("boundaryFaceCount")==item["faceCount"]-item["internalFaceCount"],
                "Every output face must be assigned to original internal incidence or a complete native boundary patch.")
        rows=item.get("nativeFiles",[])
        require(len(rows)==6 and {row.get("path") for row in rows}=={component+"-case/constant/polyMesh/"+name for name in MESH_FILES},"The complete exact six native mesh roles are required.")
        all_rows=rows+[item["sourceFaceIdMapFile"]]
        require(all_rows[-1]["path"]==component+"-case/sourceFaceIds","The original native face-ID mapping must be retained.")
        for row in all_rows:
            raw,actual=read(root,row["path"],MAX_FILE);require(actual==row,"Current original native output bytes no longer match their bound SHA256.")
            require(b"#" not in raw and b"$" not in raw and b"\x00" not in raw,"Native converted mesh may contain no executable directives, substitution or binary payload.")
            raw.decode("ascii")
            total+=len(raw)
        checks=item.get("checks",{})
        require(all(checks.get(key) is True for key in ("allOriginalCellsChecked","directedEdgeIncidenceClosed","standardCellShapeIncidenceVerified",
            "allCoordinatesAsciiRoundTripVerified","everyOriginalFaceTransferredExactlyOnce","originalPolygonsRetained","ownerNeighbourOrderingVerified"))
            and checks.get("originalCellCountChecked")==item.get("cellCount") and checks.get("internalFaceCount")==item["internalFaceCount"]
            and all(isinstance(checks.get(key),(float,int)) and not isinstance(checks[key],bool) and math.isfinite(checks[key]) and checks[key]>0
                    for key in ("minimumCellVolumeAssumedM3","maximumCellVolumeAssumedM3","totalCellVolumeAssumedM3"))
            and 0<=checks.get("maximumRelativeAreaVectorClosureError",math.inf)<=checks.get("relativeAreaClosureTolerance",0)==1e-10
            and checks.get("geometricSelfIntersectionChecked") is False and checks.get("meshQualityCheckedBySolver") is False
            and checks.get("nativeCoordinateUnitsVerified") is False and checks.get("classroomMetresPerSourceCoordinateUnit")==1,
            "Every original cell and complete native coordinate/topology derivation must have passed its independent conversion checks.")
        components.append({"component":component,"caseDirectory":item["casePath"],"cellCount":item["cellCount"],"nodeCount":item["nodeCount"],
            "faceCount":item["faceCount"],"internalFaceCount":item["internalFaceCount"],"patchFaces":{row["name"]:row["nFaces"] for row in item["patches"]},
            "cellZone":zone_name,"files":rows})
    require(total==report.get("derivedBytes") and total<=MAX_OUTPUT,"Current native output inventory must match the declared aggregate byte bound.")
    require(report.get("combinedCounts")=={key:sum(item[key] for item in report["components"]) for key in ("cellCount","nodeCount","faceCount")}
            and report["combinedCounts"]["cellCount"]<=MAX_CELLS and report["combinedCounts"]["nodeCount"]<=MAX_NODES
            and report["combinedCounts"]["faceCount"]<=MAX_FACES,
            "Combined original entity counts must remain within the separate OpenFOAM inventory limits.")
    return {"report":report,"reportFile":report_row,"components":components}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--mesh-directory",type=Path,required=True)
    parser.add_argument("--output-directory",type=Path,required=True);args=parser.parse_args()
    try:
        import os
        require(os.name=="posix","Actual original HDF5 conversion uses the independent standard public Ubuntu worker; no Windows policy change.")
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(MAX_MEMORY,MAX_MEMORY))
        report=convert(args.mesh_directory,args.output_directory)
        verify_output(args.output_directory)
        print(json.dumps({"status":report["status"],"combinedCounts":report["combinedCounts"],"derivedBytes":report["derivedBytes"],
            "volumeMeshConverted":True,"actualCfdRunVerified":False,"mainApplicationWrites":False},allow_nan=False));return 0
    except (ValueError,OSError,KeyError,TypeError,ImportError,MemoryError,OverflowError) as error:
        print(json.dumps({"status":"OriginalNativeConversionRequired","action":str(error)[:500],"volumeMeshConverted":False,
            "actualCfdRunVerified":False,"mainApplicationWrites":False}));return 2


if __name__=="__main__":raise SystemExit(main())
