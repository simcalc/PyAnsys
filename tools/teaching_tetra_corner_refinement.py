"""Conservatively subdivide authored boundary-corner tetrahedra.

Only a new interior centroid is inserted. Every pre-existing point, exterior
triangle and physical role remains exact. This is a declared mesh operation,
not native quality or CFD acceptance. Published cells replay the input recipe.
"""
from __future__ import annotations
from collections import Counter
from hashlib import sha256
import json
import math

SCHEMA = 'untirta.teaching-tetra-corner-subdivision.v1'


def require(value, message):
    if not value: raise ValueError(message)


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')


def faces(tet):
    a,b,c,d=tet
    return ((b,c,d),(a,d,c),(a,b,d),(a,c,b))


def volume(tet,points):
    a,b,c,d=(points[n] for n in tet)
    u,v,w=(tuple(p[i]-a[i] for i in range(3)) for p in (b,c,d))
    return math.fsum((u[0]*(v[1]*w[2]-v[2]*w[1]),-u[1]*(v[0]*w[2]-v[2]*w[0]),u[2]*(v[0]*w[1]-v[1]*w[0])))/6


def exterior_inventory(data):
    counts=Counter(tuple(sorted(f)) for t in data['tets'] for f in faces(t))
    require(all(c in (1,2) for c in counts.values()),'Corner subdivision requires a manifold input volume.')
    exterior={key for key,count in counts.items() if count==1}
    declared=[tuple(sorted(t)) for ts in data['patches'].values() for t in ts]
    require(len(declared)==len(set(declared)) and set(declared)==exterior,'Every original exterior face must retain its one physical role.')
    return exterior


def refine_component(data,next_node,original_global_maximum_node):
    points=dict(data['points']);tets=data['tets'];exterior=exterior_inventory(data)
    require(type(original_global_maximum_node) is int and max(points)<=original_global_maximum_node
        and type(next_node) is int and next_node>original_global_maximum_node,'New centroid labels must follow every original global node label.')
    initial_next=next_node;result=[];selected=0
    original_volumes=[volume(t,points) for t in tets]
    require(all(math.isfinite(v) and v>0 for v in original_volumes),'Each original tetrahedron must have positive finite volume.')
    boundary_counts=[sum(tuple(sorted(f)) in exterior for f in faces(t)) for t in tets]
    for tet,count in zip(tets,boundary_counts):
        if count<2:
            result.append(tet)
            continue
        require(next_node not in points,'New centroid labels cannot replace original points.')
        points[next_node]=tuple(math.fsum(data['points'][n][i] for n in tet)/4 for i in range(3))
        result.extend((next_node,*face) for face in faces(tet))
        next_node+=1;selected+=1
    current_volumes=[volume(t,points) for t in result]
    require(all(math.isfinite(v) and v>0 for v in current_volumes),'Each centroid child must have positive finite volume.')
    output={'points':points,'tets':result,'patches':data['patches']}
    new_exterior=exterior_inventory(output)
    require(new_exterior==exterior and all(points[n]==xyz for n,xyz in data['points'].items()),'Subdivision must preserve every original point and exterior facet exactly.')
    after_boundary_counts=[sum(tuple(sorted(f)) in exterior for f in faces(t)) for t in result]
    require(max(after_boundary_counts,default=0)<=1,'Every final cell must have at least three internal volume faces.')
    old_total,new_total=math.fsum(original_volumes),math.fsum(current_volumes)
    relative=abs(new_total-old_total)/old_total
    require(relative<=1e-12,'Centroid subdivision must conserve the actual original fluid volume.')
    evidence={'schemaVersion':SCHEMA,'method':'INTERIOR_CENTROID_ONE_TO_FOUR_FOR_MULTIPLE_BOUNDARY_FACES',
        'originalGlobalMaximumNodeLabel':original_global_maximum_node,'firstInsertedNodeLabel':initial_next,
        'originalTetraCount':len(tets),'finalTetraCount':len(result),'splitParentTetraCount':selected,
        'newInteriorNodeCount':selected,'additionalCells':3*selected,
        'originalUsedNodeCount':len(data['points']),'finalUsedNodeCount':len(points),
        'originalConnectivitySha256':sha256(encoded(tets)).hexdigest(),
        'originalPointsSha256':sha256(encoded([[n,*xyz] for n,xyz in sorted(data['points'].items())])).hexdigest(),
        'physicalExteriorTrianglesSha256':sha256(encoded(data['patches'])).hexdigest(),
        'maximumBoundaryFacesPerCellBefore':max(boundary_counts,default=0),'maximumBoundaryFacesPerCellAfter':max(after_boundary_counts,default=0),
        'originalFluidVolumeM3':old_total,'finalFluidVolumeM3':new_total,'relativeVolumeDifference':relative,
        'originalPointCoordinatesPreserved':True,'physicalExteriorTrianglesPreserved':True,
        'nativeMeshQualityVerified':False,'actualCfdRunVerified':False,'mainApplicationWrites':False}
    return output,evidence,next_node


def replay_component(data,evidence):
    require(isinstance(evidence,dict) and evidence.get('schemaVersion')==SCHEMA,'A declared corner subdivision evidence packet is required.')
    threshold=evidence.get('originalGlobalMaximumNodeLabel');next_node=evidence.get('firstInsertedNodeLabel')
    require(type(threshold) is int and type(next_node) is int and 0<threshold<next_node,'Bound the original and inserted node-label intervals.')
    raw_tets=[];rows=data['tets'];index=0
    while index<len(rows):
        tet=rows[index]
        if all(n<=threshold for n in tet):
            raw_tets.append(tet);index+=1
            continue
        require(tet[0]==next_node and all(n<=threshold for n in tet[1:]) and index+4<=len(rows),'Inserted cells must be a complete ordered attempt-owned centroid star.')
        group=rows[index:index+4]
        parent=(group[1][1],*tet[1:])
        require(len(set(parent))==4 and group==[(next_node,*f) for f in faces(parent)],'All four child faces must reconstruct one exact original tetrahedron.')
        raw_tets.append(parent);next_node+=1;index+=4
    raw_nodes={n for tet in raw_tets for n in tet}
    require(raw_nodes and max(raw_nodes)<=threshold and set(data['points'])==raw_nodes|set(range(evidence['firstInsertedNodeLabel'],next_node)),
        'Published points must contain only complete original and inserted cell nodes.')
    original={'points':{n:data['points'][n] for n in sorted(raw_nodes)},'tets':raw_tets,'patches':data['patches']}
    replay,expected,end=refine_component(original,evidence['firstInsertedNodeLabel'],threshold)
    require(replay=={'points':data['points'],'tets':data['tets'],'patches':data['patches']} and expected==evidence and end==next_node,
        'Published topology, centroids, volume and complete evidence must reproduce the original corner recipe exactly.')
    return original,end
