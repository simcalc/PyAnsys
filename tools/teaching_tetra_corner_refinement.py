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
BALANCING_SCHEMA = 'untirta.teaching-tetra-transition-balancing.v1'
MINIMUM_WEIGHT = .05
BALANCING_LIMITS = {'maximumOriginalCells':450000,'maximumFinalCells':450000,
    'maximumFinalPoints':500000,'maximumClosurePasses':64}
WEIGHT_SOURCE = 'https://github.com/OpenFOAM/OpenFOAM-12/blob/86e126a7bc4dbfaed949bbc58c9f0fdd67acc059/src/meshCheck/polyMeshCheck/polyMeshCheck.C#L162'


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


def internal_distances(data):
    """Actual triangular face planes and both tetrahedron centres; no volume proxy."""
    points=data['points'];centres=[tuple(math.fsum(points[n][i] for n in t)/4 for i in range(3)) for t in data['tets']]
    pending={};closed=set();pairs=[]
    for cell,tet in enumerate(data['tets']):
        for face in faces(tet):
            key=tuple(sorted(face));require(key not in closed,'Every internal triangle must have exactly two incident cells.')
            if key not in pending:pending[key]=(cell,face);continue
            neighbour=cell;owner,other=pending.pop(key);closed.add(key)
            # Opposite cyclic triangle winding is required on the two cell sides.
            require(any(face==other[::-1][i:]+other[::-1][:i] for i in range(3)), 'Internal face winding must be opposite for its two positive cells.')
            a,b,c=(points[n] for n in other);u=tuple(b[i]-a[i] for i in range(3));v=tuple(c[i]-a[i] for i in range(3))
            sf=((u[1]*v[2]-u[2]*v[1])/2,(u[2]*v[0]-u[0]*v[2])/2,(u[0]*v[1]-u[1]*v[0])/2)
            area=math.sqrt(math.fsum(s*s for s in sf));cf=tuple(math.fsum(points[n][i] for n in other)/3 for i in range(3))
            require(math.isfinite(area) and area>0,'Each shared original triangle must have positive finite area.')
            do=math.fsum(sf[i]*(cf[i]-centres[owner][i]) for i in range(3))/area
            dn=math.fsum(sf[i]*(centres[neighbour][i]-cf[i]) for i in range(3))/area
            require(all(math.isfinite(d) and d>0 for d in (do,dn)), 'Actual cell centres must lie strictly on opposite sides of their shared triangle.')
            pairs.append((owner,neighbour,key,do,dn))
    return sorted(pairs,key=lambda row:row[2])


def transition_selection(data,initial):
    """One-level closure: subdivide only the larger-distance unsplit neighbour."""
    require(0<len(data['tets'])<=BALANCING_LIMITS['maximumOriginalCells'], 'Bound original cells before allocating a lean balancing adjacency.')
    require(len(data['points'])<=BALANCING_LIMITS['maximumFinalPoints'], 'Bound original nodes before lean balancing.')
    require(isinstance(initial,set) and all(type(i) is int and 0<=i<len(data['tets']) for i in initial),'Initial split parents must be exact original tetrahedron rows.')
    pairs=internal_distances(data);selected=set(initial);passes=0
    def weights():
        return [min(a,b)/(a+b) for _,_,_,a,b in effective()]
    def effective():
        return ((a,b,key,do/(4 if a in selected else 1),dn/(4 if b in selected else 1)) for a,b,key,do,dn in pairs)
    def caps():
        require(len(data['tets'])+3*len(selected)<=BALANCING_LIMITS['maximumFinalCells'] and len(data['points'])+len(selected)<=BALANCING_LIMITS['maximumFinalPoints'],
            'The explicit lean balancing cell/node cap cannot be exceeded by a closure proposal.')
    caps();initial_weights=weights()
    while True:
        additions=set();unresolved=[]
        for a,b,key,do,dn in effective():
            if min(do,dn)/(do+dn)>=MINIMUM_WEIGHT:continue
            larger=a if do>dn else b
            if larger not in selected:additions.add(larger)
            else:unresolved.append(key)
        if not additions:
            require(not unresolved,'One-level centroid balancing cannot meet the unchanged .05 interpolation-weight gate for these original cells.')
            break
        require(passes<BALANCING_LIMITS['maximumClosurePasses'],'The fixed lean balancing closure-pass cap was reached; no partial mesh is admitted.')
        selected.update(sorted(additions));passes+=1;caps()
    final_weights=weights();extra=sorted(selected-initial)
    proof={'schemaVersion':BALANCING_SCHEMA,'enabled':True,'method':'ORIGINAL_TET_NORMAL_DISTANCE_ONE_LEVEL_FIXED_POINT',
        'minimumInterpolationWeight':MINIMUM_WEIGHT,'limits':dict(BALANCING_LIMITS),'originalInternalFaceCount':len(pairs),
        'originalAdjacencySha256':sha256(encoded([[a+1,b+1,*key] for a,b,key,_,_ in pairs])).hexdigest(),
        'initialCornerSplitParentCount':len(initial),'additionalBalancedParentCount':len(extra),'closurePasses':passes,
        'selectedParentRowsOneBasedSha256':sha256(encoded([i+1 for i in sorted(selected)])).hexdigest(),
        'additionalParentRowsOneBasedSha256':sha256(encoded([i+1 for i in extra])).hexdigest(),
        'additionalParentRowsOneBasedSample':[i+1 for i in extra[:32]],'sampleIsComplete':len(extra)<=32,
        'minimumProspectiveWeightBeforeBalancing':min(initial_weights,default=None),
        'minimumProspectiveWeightAfterBalancing':min(final_weights,default=None),
        'prospectiveLowWeightFaceCountBeforeBalancing':sum(w<MINIMUM_WEIGHT for w in initial_weights),
        'prospectiveLowWeightFaceCountAfterBalancing':sum(w<MINIMUM_WEIGHT for w in final_weights),
        'equation':'dOwn=abs(Sf.(Cf-Co))/|Sf|; dNei=abs(Sf.(Cn-Cf))/|Sf|; w=min(dOwn,dNei)/(dOwn+dNei); centroid face-child normal distance=dParent/4',
        'source':WEIGHT_SOURCE,'calculationAuthority':'Python geometry only',
        'nativeInterpolationWeightVerified':False,'nativeMeshQualityVerified':False,'actualCfdRunVerified':False}
    return selected,proof


def refine_component(data,next_node,original_global_maximum_node,*,balance_transitions=False):
    require(type(balance_transitions) is bool,'Lean transition balancing requires an explicit boolean opt-in.')
    points=dict(data['points']);tets=data['tets'];exterior=exterior_inventory(data)
    require(type(original_global_maximum_node) is int and max(points)<=original_global_maximum_node
        and type(next_node) is int and next_node>original_global_maximum_node,'New centroid labels must follow every original global node label.')
    initial_next=next_node;result=[];selected=0
    original_volumes=[volume(t,points) for t in tets]
    require(all(math.isfinite(v) and v>0 for v in original_volumes),'Each original tetrahedron must have positive finite volume.')
    boundary_counts=[sum(tuple(sorted(f)) in exterior for f in faces(t)) for t in tets]
    selected_rows={i for i,count in enumerate(boundary_counts) if count>=2};balancing=None
    if balance_transitions:selected_rows,balancing=transition_selection(data,selected_rows)
    for row,tet in enumerate(tets):
        if row not in selected_rows:
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
    if balancing is not None:
        final_weights=[min(do,dn)/(do+dn) for _,_,_,do,dn in internal_distances(output)]
        require(all(w>=MINIMUM_WEIGHT for w in final_weights),'Every actual derived internal face must pass the unchanged geometric .05 weight gate.')
        balancing['finalInternalFaceCount']=len(final_weights)
        balancing['minimumFinalInternalFaceWeight']=min(final_weights,default=None)
        balancing['finalGeometricLowWeightFaceCount']=sum(w<MINIMUM_WEIGHT for w in final_weights)
        balancing['allDerivedInternalFacePlanesChecked']=True
        evidence['transitionBalancing']=balancing
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
    replay,expected,end=refine_component(original,evidence['firstInsertedNodeLabel'],threshold,balance_transitions='transitionBalancing' in evidence)
    if 'transitionBalancing' in evidence:
        require(encoded(expected['transitionBalancing'])==encoded(evidence['transitionBalancing']), 'Every typed balancing input, selection, bound and result must replay exactly.')
    require(replay=={'points':data['points'],'tets':data['tets'],'patches':data['patches']} and expected==evidence and end==next_node,
        'Published topology, centroids, volume and complete evidence must reproduce the original corner recipe exactly.')
    return original,end
