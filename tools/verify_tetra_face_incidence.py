"""Exact tetrahedral face admission, independent of positive cell volume.

No topology repair, tolerance, source execution or native solver is used.
Every exterior face needs one tagged surface triangle; every interior face
needs exactly two opposite oriented incidences. This is narrower than a native
mesh-quality or geometric self-intersection proof.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import re

SCHEMA="untirta.tetra-face-incidence-verification.v1"
MAX_CELLS=800_000
MAX_SURFACES=1_000_000
MAX_NODES=500_000
MAX_FILE_BYTES=128*1024**2
MAX_LINE_BYTES=4096
MAX_SAMPLES=8


class TetraIncidenceIssue(ValueError):
    def __init__(self,message,report=None):
        super().__init__(message);self.report=report


def require(condition,message):
    if not condition:raise TetraIncidenceIssue(message)


def _rows(value,width,limit,name):
    import numpy as np
    value=np.asarray(value)
    require(value.ndim==2 and value.shape[1]==width and 0<len(value)<=limit and value.dtype.kind in "iu",
            "Require complete bounded integer "+name+" connectivity rows.")
    require((value>=0).all(),"Native node labels must be nonnegative integers.")
    return value.astype("u8",copy=False)


def audit_incidence(tetrahedra,surface_triangles,*,tetra_element_ids=None,surface_element_ids=None):
    """Return exact facts for caller-proven positive Gmsh tetra node ordering.

    Node labels may be sparse source IDs or compact zero-based IDs. Their
    order is compacted only for exact equality keys; coordinates/topology are
    not changed or returned as a derived mesh.
    """
    import numpy as np
    tetra=_rows(tetrahedra,4,MAX_CELLS,"tetrahedron");surface=_rows(surface_triangles,3,MAX_SURFACES,"surface triangle")
    labels=np.unique(np.concatenate((tetra.reshape(-1),surface.reshape(-1))))
    require(0<len(labels)<=MAX_NODES,"Complete actual mesh node labels exceed the independent bounded incidence inventory.")
    bits=max(1,(len(labels)-1).bit_length());require(3*bits<=64,"The complete compact face key must fit uint64 without collisions.")
    t=np.searchsorted(labels,tetra).astype("u4");s=np.searchsorted(labels,surface).astype("u4")
    def element_ids(value,length):
        if value is None:return np.arange(1,length+1,dtype="u8")
        rows=np.asarray(value)
        require(rows.ndim==1 and len(rows)==length and rows.dtype.kind in "iu" and (rows>0).all() and len(np.unique(rows))==length,
                "Actual element identifiers must be positive, unique and cover every connectivity row.")
        return rows.astype("u8",copy=False)
    tids=element_ids(tetra_element_ids,len(t));sids=element_ids(surface_element_ids,len(s))
    sorted_t=np.sort(t,axis=1);sorted_s=np.sort(s,axis=1)
    repeated_t=int(np.count_nonzero(np.any(sorted_t[:,1:]==sorted_t[:,:-1],axis=1)))
    repeated_s=int(np.count_nonzero(np.any(sorted_s[:,1:]==sorted_s[:,:-1],axis=1)))
    duplicate_t=len(t)-len(np.unique(sorted_t,axis=0))
    faces=t[:,[(0,2,1),(0,1,3),(1,2,3),(2,0,3)]].reshape(-1,3)
    def keys(rows):
        canonical=np.sort(rows,axis=1).astype("u8")
        return (canonical[:,0]<<(2*bits))|(canonical[:,1]<<bits)|canonical[:,2]
    def parity(rows):
        inversions=(rows[:,0]>rows[:,1]).astype("i1")+(rows[:,0]>rows[:,2]).astype("i1")+(rows[:,1]>rows[:,2]).astype("i1")
        return 1-2*(inversions%2)
    face_keys=keys(faces);order=np.argsort(face_keys,kind="stable");ordered=face_keys[order]
    starts=np.flatnonzero(np.r_[True,ordered[1:]!=ordered[:-1]])
    sizes=np.diff(np.r_[starts,len(ordered)]);unique=ordered[starts]
    orientation=parity(faces);sums=np.add.reduceat(orientation[order],starts)
    exterior=unique[sizes==1];internal=np.flatnonzero(sizes==2)
    source_keys=keys(s);source_unique,source_counts=np.unique(source_keys,return_counts=True)
    missing=np.setdiff1d(exterior,source_unique,assume_unique=True)
    extra=np.setdiff1d(source_unique,exterior,assume_unique=True)
    nonmanifold=np.flatnonzero(sizes>2);conflicts=internal[sums[internal]!=0]
    duplicate_s=len(s)-len(source_unique)
    def group_sample(index):
        incidence=order[starts[index]:starts[index]+min(int(sizes[index]),MAX_SAMPLES)]
        return {"nodeLabels":labels[faces[incidence[0]]].tolist(),"incidenceCount":int(sizes[index]),
                "tetraElementIds":tids[incidence//4].tolist(),"tetraFaceSlots":(incidence%4).tolist(),
                "outwardOrientationParity":orientation[incidence].tolist(),"incidenceSampleTruncated":int(sizes[index])>MAX_SAMPLES}
    def group_index(key):return int(np.searchsorted(unique,key))
    def source_sample(key):
        incidence=np.flatnonzero(source_keys==key);index=group_index(key);sample=incidence[:MAX_SAMPLES]
        return {"nodeLabels":labels[s[sample[0]]].tolist(),"surfaceElementIds":sids[sample].tolist(),
                "taggedSurfaceIncidenceCount":len(incidence),"incidenceSampleTruncated":len(incidence)>MAX_SAMPLES,
                "volumeIncidenceCount":int(sizes[index]) if index<len(unique) and unique[index]==key else 0}
    accepted=not any((repeated_t,repeated_s,duplicate_t,duplicate_s,len(missing),len(extra),len(nonmanifold),len(conflicts)))
    return {"schemaVersion":SCHEMA,"accepted":accepted,"actualTetraCount":len(t),"actualTaggedSurfaceTriangleCount":len(s),
            "actualReferencedNodeCount":len(labels),"tetraFaceIncidenceCount":len(faces),"uniqueTetraFaceCount":len(unique),
            "incidenceMultiplicityCounts":{str(int(n)):int(np.count_nonzero(sizes==n)) for n in np.unique(sizes)},
            "singleIncidenceExteriorFaceCount":len(exterior),"doubleIncidenceInteriorFaceCount":len(internal),
            "missingTaggedSurfaceForExteriorFaceCount":len(missing),"taggedSurfaceWithoutSingleVolumeIncidenceCount":len(extra),
            "higherThanTwoIncidenceFaceCount":len(nonmanifold),"twoIncidenceOrientationConflictCount":len(conflicts),
            "duplicateTetraCount":duplicate_t,"duplicateTaggedSurfaceTriangleCount":duplicate_s,
            "repeatedNodeTetraCount":repeated_t,"repeatedNodeSurfaceTriangleCount":repeated_s,
            "allActualTetraFacesAudited":True,"exactExteriorSurfaceMatchVerified":accepted,
            "everyInternalFaceOppositeOrientedPairVerified":accepted,
            "elementIdentifierBasis":"Caller-supplied native IDs; otherwise one-based connectivity row indices for diagnostics.",
            "samples":{"missingExteriorTag":[group_sample(group_index(key)) for key in missing[:MAX_SAMPLES]],
                       "taggedNotExterior":[source_sample(key) for key in extra[:MAX_SAMPLES]],
                       "nonmanifold":[group_sample(int(i)) for i in nonmanifold[:MAX_SAMPLES]],
                       "orientationConflict":[group_sample(int(i)) for i in conflicts[:MAX_SAMPLES]]},
            "limits":{"maximumCells":MAX_CELLS,"maximumSurfaceTriangles":MAX_SURFACES,"maximumNodes":MAX_NODES,"maximumFileBytes":MAX_FILE_BYTES},
            "geometryChanged":False,"topologyChanged":False,"nativeOpenFOAMQualityVerified":False,
            "geometricSelfIntersectionChecked":False,"vertexLinkManifoldVerified":False,
            "simulationExecuted":False,"actualCfdRunVerified":False,"mainApplicationWrites":False,
            "claimBoundary":"Exact node-label face incidence and relative orientation only. Positive volumes, original-boundary identity, native quality and solver qualification remain independent checks."}


def verify_msh(path):
    """Read every bounded actual MSH2.2 row and reject any incidence defect.

    On topology rejection the exception's report retains measured counts and
    bounded samples for the caller's correction evidence.
    """
    import numpy as np
    path=Path(path).absolute()
    for item in (path,*path.parents):
        require(not item.is_symlink() and not getattr(item,"is_junction",lambda:False)(),"Actual mesh evidence cannot traverse links or junctions.")
    require(path.is_file() and 0<path.stat().st_size<=MAX_FILE_BYTES,"A bounded ordinary actual MSH file is required.")
    size=path.stat().st_size;digest=sha256();seen_bytes=0
    with path.open("rb") as stream:
        def line():
            nonlocal seen_bytes
            raw=stream.readline(MAX_LINE_BYTES+1);seen_bytes+=len(raw);digest.update(raw)
            require(raw and len(raw)<=MAX_LINE_BYTES and seen_bytes<=size,"Each native mesh row must be complete and bounded.")
            return raw.decode("ascii").strip()
        def expect(text):require(line()==text,"Require the exact complete native MSH2.2 ASCII section: "+text)
        def count(limit):
            raw=line();require(re.fullmatch(r"[1-9]\d*",raw) is not None and int(raw)<=limit,"Require a positive bounded actual native section count.")
            return int(raw)
        expect("$MeshFormat");expect("2.2 0 8");expect("$EndMeshFormat");expect("$PhysicalNames")
        physical={}
        for _ in range(count(64)):
            match=re.fullmatch(r'(2|3) ([1-9]\d*) "([A-Za-z0-9_-]+)"',line())
            require(match is not None,"Require safe exact actual physical source tags.")
            key=(int(match[1]),int(match[2]));require(key not in physical,"Physical source tags must be unique.");physical[key]=match[3]
        expect("$EndPhysicalNames");expect("$Nodes");nn=count(MAX_NODES)
        node_labels=np.empty(nn,dtype="u8");coordinates=np.empty((nn,3),dtype="f8")
        for index in range(nn):
            values=line().split();require(len(values)==4 and re.fullmatch(r"[1-9]\d*",values[0]) is not None,"Every actual node needs an explicit positive ID and three coordinates.")
            identifier=int(values[0]);require(identifier<=2**63-1,"Actual native node labels must fit reviewed integer storage.")
            node_labels[index]=identifier;coordinates[index]=[float(v) for v in values[1:]]
        require(np.isfinite(coordinates).all() and len(np.unique(node_labels))==nn,"Actual native node IDs must be unique and all coordinates finite.")
        expect("$EndNodes");expect("$Elements");ne=count(MAX_CELLS+MAX_SURFACES)
        tetra=np.empty((min(ne,MAX_CELLS),4),dtype="u8");surface=np.empty((min(ne,MAX_SURFACES),3),dtype="u8")
        tids=np.empty(len(tetra),dtype="u8");sids=np.empty(len(surface),dtype="u8");ids=np.empty(ne,dtype="u8");nt=ns=0
        for index in range(ne):
            tokens=line().split();require(all(re.fullmatch(r"\d+",v) is not None for v in tokens),"Native elements must contain only actual nonnegative integer labels.")
            values=[int(v) for v in tokens];require(len(values)>=8 and 0<values[0]<=2**63-1 and values[1] in (2,4) and 2<=values[2]<=32,"Only explicit physical-tagged linear triangle and tetra elements are supported.")
            dimension,width=(2,3) if values[1]==2 else (3,4);nodes=values[3+values[2]:]
            require((dimension,values[3]) in physical and len(nodes)==width and all(0<v<=2**63-1 for v in nodes),"Every actual native element must preserve its valid tag and complete node labels.")
            ids[index]=values[0]
            if dimension==3:
                require(nt<MAX_CELLS,"Actual tetra count exceeds the bounded one-component worker budget.")
                tetra[nt]=nodes;tids[nt]=values[0];nt+=1
            else:
                require(ns<MAX_SURFACES,"Actual surface count exceeds its bounded one-component budget.")
                surface[ns]=nodes;sids[ns]=values[0];ns+=1
        expect("$EndElements");tail=stream.read(4097);seen_bytes+=len(tail);digest.update(tail)
        require(not tail.strip() and len(tail)<=4096 and seen_bytes==size and path.stat().st_size==size,"Actual native mesh file must end completely without another section or concurrent size change.")
    require(nt>0 and ns>0 and len(np.unique(ids))==ne,"Every native element ID must be unique and both volume and tagged surfaces nonempty.")
    tetra=tetra[:nt];surface=surface[:ns];tids=tids[:nt];sids=sids[:ns]
    node_order=np.argsort(node_labels);sorted_labels=node_labels[node_order]
    def indices(rows):
        found=np.searchsorted(sorted_labels,rows)
        require((found<nn).all(),"Every actual element must reference an existing native node.")
        require(np.array_equal(sorted_labels[found],rows),"Every actual element must reference an existing native node.")
        return node_order[found]
    ti=indices(tetra);si=indices(surface)
    require(len(np.unique(np.r_[ti.reshape(-1),si.reshape(-1)]))==nn,"Every declared native node must be accounted for by actual mesh elements.")
    minimum=math.inf;maximum=0.
    for first in range(0,nt,20_000):
        points=coordinates[ti[first:first+20_000]]
        determinants=np.einsum("ij,ij->i",np.cross(points[:,1]-points[:,0],points[:,2]-points[:,0]),points[:,3]-points[:,0])
        require(np.isfinite(determinants).all() and (determinants>0).all(),"Every actual tetrahedron must have finite strictly positive signed volume before oriented incidence admission.")
        minimum=min(minimum,float(determinants.min()));maximum=max(maximum,float(determinants.max()))
    result=audit_incidence(tetra,surface,tetra_element_ids=tids,surface_element_ids=sids)
    # Bind current bytes again after the complete audit; another writer cannot
    # make a prior read appear to describe a different current file.
    current=sha256();current_size=0
    with path.open("rb") as stream:
        while part:=stream.read(1024**2):
            current_size+=len(part);require(current_size<=MAX_FILE_BYTES,"Actual current mesh grew beyond its file bound.");current.update(part)
    require(current_size==size and current.hexdigest()==digest.hexdigest(),"Actual mesh bytes changed during the read-only incidence audit.")
    result.update(meshFile={"name":path.name,"bytes":size,"sha256":digest.hexdigest()},finitePositiveSignedTetraVolumeVerified=True,
                  minimumSignedTetraDeterminant=minimum,maximumSignedTetraDeterminant=maximum,actualDeclaredNodeCount=nn,
                  elementIdentifierBasis="Actual MSH2.2 element IDs",meshBytesImmutableDuringAudit=True)
    if not result["accepted"]:
        raise TetraIncidenceIssue("Reject actual tetra mesh: exterior/source mismatch, nonmanifold incidence, duplicate connectivity or same-oriented interior faces.",result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--mesh",type=Path,required=True);args=parser.parse_args()
    try:
        print(json.dumps(verify_msh(args.mesh),sort_keys=True,allow_nan=False));return 0
    except (ValueError,OSError,OverflowError,MemoryError) as error:
        report=getattr(error,"report",None) or {"schemaVersion":SCHEMA,"accepted":False,"action":str(error)[:500],"actualCfdRunVerified":False,"mainApplicationWrites":False}
        print(json.dumps(report,sort_keys=True,allow_nan=False));return 2


if __name__=="__main__":raise SystemExit(main())
