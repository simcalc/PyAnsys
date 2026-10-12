"""Read-only, branch-specific evidence audit for authored SI sliding pump data.

Native data, coordinate motion, ALE identities, checkpoint completeness and
an actually matched full-state comparison are distinct. This pure audit never
runs a solver and never confers CFD, head, restart or convergence acceptance.
"""
from __future__ import annotations
import argparse
from array import array
from hashlib import sha256
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_native_reduction as io
import run_teaching_pump_phase as science

SCHEMA = "untirta.teaching-pump-transient-evidence-audit.v1"
INPUT_SCHEMA = "untirta.teaching-pump-transient-audit-input.v1"
REPORT = "teaching-transient-audit.json"
MAX_FILE, MAX_TOTAL, MAX_RECORD, MAX_FRAMES = 128*1024**2, 1024**3, 128*1024, 16
MAX_CELLS, MAX_FACES, MAX_POINTS = 500000, 2000000, 1000000
SOURCE_COMMIT = "86e126a7bc4dbfaed949bbc58c9f0fdd67acc059"
BASE_URL = "https://github.com/OpenFOAM/OpenFOAM-12/blob/"+SOURCE_COMMIT+"/"
SOURCE_POLICY = {
    "p": {"read":"NO_READ", "write":"AUTO_WRITE", "rule":"Reconstructed p_rgh+rho*gh; retained p still required for the independent scientific pressure check", "source":BASE_URL+"applications/modules/incompressibleVoF/incompressibleVoF.C"},
    "phi": {"read":"READ_IF_PRESENT", "write":"AUTO_WRITE", "source":BASE_URL+"applications/modules/VoFSolver/VoFSolver.C"},
    "Uf": {"read":"READ_IF_PRESENT", "write":"AUTO_WRITE", "source":BASE_URL+"applications/modules/VoFSolver/VoFSolver.C"},
    "rAU": {"read":"READ_IF_PRESENT", "write":"AUTO_WRITE", "condition":"Exact authored correctPhi yes", "source":BASE_URL+"applications/modules/incompressibleVoF/incompressibleVoF.C"},
    "alphaPhi.water": {"read":"READ_IF_PRESENT", "write":"AUTO_WRITE", "source":BASE_URL+"applications/modules/twoPhaseSolver/twoPhaseSolver.C"},
    "rhoPhi": {"read":"NO_READ", "write":"NO_WRITE", "rule":"Regenerated from phase fluxes and densities; absence is not a missing restart input", "source":BASE_URL+"applications/modules/VoFSolver/VoFSolver.C"},
    "alphaPhi.vapour": {"read":"NO_READ", "write":"NO_WRITE", "rule":"Regenerated phi-alphaPhi.water; not a mandatory stored field", "source":BASE_URL+"applications/modules/twoPhaseSolver/twoPhaseSolver.C"},
    "meshPhi": {"read":"MUST_READ_IF_STORED", "write":"fvMesh::writeObject when present", "source":BASE_URL+"src/finiteVolume/fvMesh/fvMesh.C"},
    "Vc0": {"read":"MUST_READ_IF_STORED", "write":"Only when V00 exists; not required by the authored Euler recipe", "source":BASE_URL+"src/finiteVolume/fvMesh/fvMesh.C"},
}
REQUIRED_STATE = set(science.END_FIELDS)|{"phi","Uf","rAU","alphaPhi.water","meshPhi","polyMesh/points","fvMesh/polyFaces","uniform/time"}
ROLES = {n: ("volVectorField" if n=="U" else "volScalarField", science.FIELD_DIMENSIONS[n], "cells") for n in science.END_FIELDS}
ROLES.update({"phi":("surfaceScalarField","0 3 -1 0 0 0 0","faces"),
    "meshPhi":("surfaceScalarField","0 3 -1 0 0 0 0","faces"),
    "alphaPhi.water":("surfaceScalarField","0 3 -1 0 0 0 0","faces"),
    "alphaPhi.vapour":("surfaceScalarField","0 3 -1 0 0 0 0","faces"),
    "Uf":("surfaceVectorField","0 1 -1 0 0 0 0","faces"),
    "Sf":("surfaceVectorField","0 2 0 0 0 0 0","faces"),
    "Cf":("surfaceVectorField","0 1 0 0 0 0 0","faces"),
    "rAU":("volScalarField","-1 3 1 0 0 0 0","cells"),
    "rho":("volScalarField","1 -3 0 0 0 0 0","cells"),
    "rhoPhi":("surfaceScalarField","1 0 -1 0 0 0 0","faces"),
    "alpha.vapour":("volScalarField","0 0 0 0 0 0 0","cells"),
    "totalPressurePa":("volScalarField","1 -1 -2 0 0 0 0","cells"),
    "U2":("volScalarField","0 2 -2 0 0 0 0","cells"),
    "yPlus":("volScalarField","0 0 0 0 0 0 0","cells")})
PATCH_TYPES = {"calculated","fixedValue","zeroGradient","noSlip","movingWallVelocity","movingWallSlipVelocity","slip",
    "inletOutlet","pressureInletOutletVelocity","flowRateOutletVelocity","prghTotalPressure","fixedFluxPressure",
    "kqRWallFunction","omegaWallFunction","nutkWallFunction","nonConformalCyclic","nonConformalError","nonConformalCalculated","empty"}
FALSE_FLAGS = ("actualCfdRunVerified","simulationExecuted","qualifiedBaseline","headQualified","phaseH0Eligible","npshrCalculated",
    "npsh3Qualified","restartVerified","timeStepConvergenceVerified","meshConvergenceVerified","physicalValidationVerified","mainApplicationWrites")
FLOAT = io.FLOAT


class AuditIssue(ValueError): pass
def require(value, code):
    if not value: raise AuditIssue(code)


def finite(value, *, positive=False):
    require(type(value) in (float,int) and math.isfinite(value) and (not positive or value>0),"TEACHING_AUDIT_FINITE_TYPED_NUMBER_REQUIRED")
    return float(value)


def vector(value):
    require(isinstance(value,(list,tuple,array)) and len(value)==3,"TEACHING_AUDIT_COMPLETE_VECTOR_REQUIRED")
    return tuple(finite(v) for v in value)


def plain(raw, name, kind, location=None):
    require(isinstance(raw,(bytes,bytearray)) and 0<len(raw)<=MAX_FILE,"TEACHING_AUDIT_BOUNDED_NATIVE_BYTES_REQUIRED")
    text=re.sub(r"/\*.*?\*/|//[^\n]*","",raw.decode("ascii"),flags=re.S)
    require(not re.search(r"[#\x00$]|\b(?:code\w*|coded\w*|libs|functionObjectLibs|systemCall|include)\b",text)
        and not re.search(r"\b(?:nan|inf|infinity)\b",text,re.I),"TEACHING_AUDIT_DATA_ONLY_NATIVE_REQUIRED")
    headers=re.findall(r"\bFoamFile\s*\{([^{}]*)\}",text,re.S)
    require(len(headers)==1,"TEACHING_AUDIT_ONE_NATIVE_HEADER_REQUIRED")
    for key,expected in (("format","ascii"),("class",kind),("object",name)):
        entries=re.findall(r"\b"+key+r"\s+([^;]+);",headers[0])
        require(len(entries)==1 and entries[0].strip().strip('"')==expected,"TEACHING_AUDIT_EXACT_NATIVE_ROLE_REQUIRED")
    if location is not None:
        entries=re.findall(r"\blocation\s+([^;]+);",headers[0])
        require(len(entries)==1 and entries[0].strip().strip('"')==location,"TEACHING_AUDIT_EXACT_NATIVE_LOCATION_REQUIRED")
    require(all(math.isfinite(float(m[0])) for m in re.finditer(FLOAT,text)),"TEACHING_AUDIT_ALL_NUMERIC_LITERALS_FINITE_REQUIRED")
    return re.sub(r"\bFoamFile\s*\{[^{}]*\}","",text,count=1).strip()


def blocks(body):
    """Flat native patch dictionaries; nested Function1 parameters remain data."""
    result={};position=0
    pattern=re.compile(r'\s*"?([A-Za-z0-9_.-]+)"?\s*\{')
    while body[position:].strip():
        match=pattern.match(body,position);require(match,"TEACHING_AUDIT_COMPLETE_PATCH_DICTIONARY_REQUIRED")
        name=match[1];require(name not in result,"TEACHING_AUDIT_UNIQUE_PATCH_REQUIRED")
        depth=1;end=match.end()
        while depth and end<len(body):
            depth += (body[end]=="{")-(body[end]=="}");end+=1
        require(depth==0,"TEACHING_AUDIT_CLOSED_PATCH_DICTIONARY_REQUIRED")
        result[name]=body[match.end():end-1];position=end
    return result


def values(declaration,count,*,is_vector=False,is_label=False):
    require(type(count) is int and 0<=count<=max(MAX_FACES,MAX_CELLS),"TEACHING_AUDIT_BOUNDED_FULL_VALUE_COUNT_REQUIRED")
    datatype="vector" if is_vector else "label" if is_label else "scalar"
    match=re.fullmatch(r"\s*(?:uniform\s+([^;]+)|nonuniform\s+List<"+datatype+r">\s+(\d+)\s*\((.*?)\))\s*",declaration,re.S)
    require(match,"TEACHING_AUDIT_TYPED_NATIVE_VALUE_LIST_REQUIRED")
    uniform=match[1] is not None;source=match[1] if uniform else match[3]
    require(uniform or int(match[2])==count,"TEACHING_AUDIT_COMPLETE_NATIVE_VALUE_COUNT_REQUIRED")
    n=1 if uniform else count
    if is_vector:
        triples=re.findall(r"\(([^()]*)\)",source)
        require(len(triples)==n and not re.sub(r"\([^()]*\)","",source).strip() and all(len(x.split())==3 for x in triples),"TEACHING_AUDIT_NATIVE_VECTOR_SHAPE_REQUIRED")
        tokens=[v for x in triples for v in x.split()]
    else:
        tokens=source.split();require(len(tokens)==n and "(" not in source and ")" not in source,"TEACHING_AUDIT_NATIVE_SCALAR_SHAPE_REQUIRED")
    require(all(re.fullmatch(r"-?\d+" if is_label else FLOAT,v) and math.isfinite(float(v)) for v in tokens),"TEACHING_AUDIT_FINITE_NATIVE_VALUES_REQUIRED")
    result=array("q" if is_label else "d",(int(v) if is_label else float(v) for v in tokens))
    return result*count if uniform else result


def field(raw,name,mesh,patch_counts,location):
    require(name in ROLES,"TEACHING_AUDIT_KNOWN_NATIVE_FIELD_REQUIRED")
    kind,dimensions,role=ROLES[name];body=plain(raw,name,kind,location)
    dims=re.findall(r"\bdimensions\s*\[([^\[\]]*)\]\s*;",body)
    require(len(dims)==1 and (" ".join(dims[0].split())==dimensions or dimensions=="0 0 0 0 0 0 0" and not dims[0].strip()),"TEACHING_AUDIT_EXACT_SI_DIMENSIONS_REQUIRED")
    declarations=re.findall(r"\binternalField\s+(.*?);",body,re.S)
    require(len(declarations)==1,"TEACHING_AUDIT_ONE_FULL_INTERNAL_FIELD_REQUIRED")
    internal=values(declarations[0],len(mesh["neighbour"]) if role=="faces" else mesh["cellCount"],is_vector=kind.endswith("VectorField"))
    bound=re.findall(r"\bboundaryField\s*\{(.*)\}\s*$",body,re.S)
    require(len(bound)==1,"TEACHING_AUDIT_COMPLETE_BOUNDARY_FIELD_REQUIRED")
    patches=blocks(bound[0]);require(set(patches)==set(mesh["boundary"]),"TEACHING_AUDIT_ALL_NATIVE_PATCHES_REQUIRED")
    boundary={};types={};implicit={}
    for patch,payload in patches.items():
        # Native Function1 writes the prescribed outlet flow as a nested
        # constant. It is data and must not be confused with the patch type.
        def constant_flow(match):
            entries=match[1]
            require(re.fullmatch(r"\s*type\s+constant\s*;\s*value\s+"+FLOAT+r"\s*;\s*",entries),"TEACHING_AUDIT_REVIEWED_CONSTANT_OUTLET_FUNCTION_REQUIRED")
            require(float(re.search(r"\bvalue\s+("+FLOAT+r")",entries)[1])>0,"TEACHING_AUDIT_POSITIVE_OUTLET_FLOW_REQUIRED")
            return ""
        payload=re.sub(r"\bvolumetricFlowRate\s*\{([^{}]*)\}",constant_flow,payload)
        require("{" not in payload and "}" not in payload,"TEACHING_AUDIT_NO_UNREVIEWED_NESTED_PATCH_DATA_REQUIRED")
        kinds=re.findall(r"\btype\s+([^;]+);",payload)
        require(len(kinds)==1 and kinds[0].strip() in PATCH_TYPES,"TEACHING_AUDIT_REVIEWED_PATCH_FIELD_TYPE_REQUIRED")
        types[patch]=kinds[0].strip();decl=re.findall(r"\bvalue\s+(.*?);",payload,re.S)
        require(len(decl)<=1,"TEACHING_AUDIT_ONE_STORED_PATCH_VALUE_REQUIRED")
        if decl:boundary[patch]=values(decl[0],patch_counts[patch],is_vector=kind.endswith("VectorField"))
        elif kinds[0].strip()=="noSlip":implicit[patch]="Pinned native noSlip: tangential0, normal meshPhi/|Sf|; no value written"
    all_values=[internal,*boundary.values()]
    if name in {"k","omega","nut","rAU","rho","U2","yPlus"}:require(all(v>=0 for a in all_values for v in a),"TEACHING_AUDIT_NONNEGATIVE_PHYSICAL_VALUES_REQUIRED")
    if name.startswith("alpha."):require(all(-1e-9<=v<=1+1e-9 for a in all_values for v in a),"TEACHING_AUDIT_PHASE_BOUNDS_REQUIRED")
    return {"name":name,"internal":internal,"boundary":boundary,"patchTypes":types,"implicitBoundaryRoles":implicit,"class":kind,"dimensions":dimensions}


def dot(a,b):return math.fsum(x*y for x,y in zip(a,b,strict=True))
def sub(a,b):return tuple(x-y for x,y in zip(a,b,strict=True))
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])


def triangle_geometry(points,faces):
    require(len(points)<=MAX_POINTS and len(faces)<=MAX_FACES,"TEACHING_AUDIT_BOUNDED_TOPOLOGY_REQUIRED")
    centres=[];areas=[]
    for face in faces:
        require(len(face)==3 and len(set(face))==3 and all(type(i) is int and 0<=i<len(points) for i in face),"TEACHING_AUDIT_EXACT_AUTHORED_TRIANGLES_REQUIRED")
        a,b,c=(vector(points[i]) for i in face);area=tuple(v/2 for v in cross(sub(b,a),sub(c,a)))
        require(dot(area,area)>0,"TEACHING_AUDIT_NONDEGENERATE_FACE_REQUIRED")
        areas.append(area);centres.append(tuple((a[i]+b[i]+c[i])/3 for i in range(3)))
    return centres,areas


def cell_volumes(mesh,points):
    centres,areas=triangle_geometry(points,mesh["faces"]);owners,neighbours=mesh["owner"],mesh["neighbour"]
    count=mesh["cellCount"];require(type(count) is int and 0<count<=MAX_CELLS and len(owners)==len(areas) and len(neighbours)<=len(owners),"TEACHING_AUDIT_EXACT_CELL_TOPOLOGY_REQUIRED")
    result=array("d",[0.])*count
    for face,(cf,sf) in enumerate(zip(centres,areas,strict=True)):
        owner=owners[face];require(0<=owner<count,"TEACHING_AUDIT_OWNER_CELL_RANGE_REQUIRED")
        volume=dot(cf,sf)/3;result[owner]+=volume
        if face<len(neighbours):
            neighbour=neighbours[face];require(0<=neighbour<count and neighbour!=owner,"TEACHING_AUDIT_NEIGHBOUR_CELL_RANGE_REQUIRED");result[neighbour]-=volume
    require(all(v>0 and math.isfinite(v) for v in result),"TEACHING_AUDIT_POSITIVE_ALL_CELL_VOLUMES_REQUIRED")
    return result


def rigid_volume_check(mesh,previous_points,current_points,tolerance=1e-10):
    tolerance=finite(tolerance,positive=True);require(tolerance<=1e-8,"TEACHING_AUDIT_RIGID_VOLUME_TOLERANCE_BOUND_REQUIRED")
    old,new=cell_volumes(mesh,previous_points),cell_volumes(mesh,current_points)
    error=max(abs(a-b)/max(a,b) for a,b in zip(old,new,strict=True))
    require(error<=tolerance,"TEACHING_AUDIT_RIGID_CELL_VOLUME_PRESERVATION_REQUIRED")
    return {"allCellsChecked":len(old),"maximumRelativeCellVolumeChange":error,"relativeTolerance":tolerance,"oldVolumes":old,"newVolumes":new}


def discrete_gcl_check(old_volumes,new_volumes,owners,neighbours,face_fluxes,dt,tolerance=1e-9):
    dt=finite(dt,positive=True);tolerance=finite(tolerance,positive=True)
    require(tolerance<=1e-7 and 0<len(old_volumes)==len(new_volumes)<=MAX_CELLS and len(face_fluxes)==len(owners)<=MAX_FACES and len(neighbours)<=len(owners),"TEACHING_AUDIT_COMPLETE_GCL_DOMAIN_REQUIRED")
    divergence=array("d",[0.])*len(old_volumes)
    for i,flux in enumerate(face_fluxes):
        flux=finite(flux);require(type(owners[i]) is int and 0<=owners[i]<len(divergence),"TEACHING_AUDIT_GCL_OWNER_RANGE_REQUIRED")
        divergence[owners[i]]+=flux
        if i<len(neighbours):
            require(type(neighbours[i]) is int and 0<=neighbours[i]<len(divergence),"TEACHING_AUDIT_GCL_NEIGHBOUR_RANGE_REQUIRED");divergence[neighbours[i]]-=flux
    errors=[abs((finite(b,positive=True)-finite(a,positive=True))-dt*f)/max(a,b) for a,b,f in zip(old_volumes,new_volumes,divergence,strict=True)]
    require(max(errors)<=tolerance,"TEACHING_AUDIT_DISCRETE_EULER_GCL_REQUIRED")
    return {"equation":"(V_new-V_old)/deltaT = sum(outward meshPhi)","allCellsChecked":len(errors),"maximumVolumeNormalizedResidual":max(errors),"relativeTolerance":tolerance,"measuredDeltaTS":dt}


def relative_flux_check(phi_relative,mesh_phi,face_velocity,area_vectors,relative_tolerance=1e-8,absolute_tolerance=1e-12):
    require(0<len(phi_relative)==len(mesh_phi)==len(face_velocity)==len(area_vectors)<=MAX_FACES,"TEACHING_AUDIT_MATCHED_COMPLETE_FACE_FLUX_REQUIRED")
    relative_tolerance=finite(relative_tolerance,positive=True);absolute_tolerance=finite(absolute_tolerance,positive=True)
    require(relative_tolerance<=1e-6 and absolute_tolerance<=1e-9,"TEACHING_AUDIT_FLUX_TOLERANCE_BOUND_REQUIRED")
    errors=[]
    for relative,moving,u,sf in zip(phi_relative,mesh_phi,face_velocity,area_vectors,strict=True):
        absolute=dot(vector(u),vector(sf));reconstructed=finite(relative)+finite(moving);error=abs(absolute-reconstructed)
        require(error<=absolute_tolerance+relative_tolerance*max(abs(absolute),abs(reconstructed)),"TEACHING_AUDIT_ALE_RELATIVE_FLUX_IDENTITY_REQUIRED");errors.append(error)
    return {"equation":"phi_absolute = Uf_absolute dot Sf = phi_relative + meshPhi", "allFacesChecked":len(errors),"maximumAbsoluteFluxResidualM3S":max(errors),"relativeTolerance":relative_tolerance,"absoluteToleranceM3S":absolute_tolerance}


def moving_wall_check(old_centres,new_centres,area_vectors,mesh_phi,wall_velocity,dt,tolerance=1e-8):
    dt=finite(dt,positive=True);tolerance=finite(tolerance,positive=True)
    require(tolerance<=1e-6 and 0<len(old_centres)==len(new_centres)==len(area_vectors)==len(mesh_phi)==len(wall_velocity)<=MAX_FACES,"TEACHING_AUDIT_ALL_PHYSICAL_WALL_FACES_REQUIRED")
    errors=[]
    for old,new,sf,flux,actual in zip(old_centres,new_centres,area_vectors,mesh_phi,wall_velocity,strict=True):
        sf=vector(sf);magnitude=math.sqrt(dot(sf,sf));require(magnitude>0,"TEACHING_AUDIT_NONZERO_PHYSICAL_WALL_AREA_REQUIRED")
        normal=tuple(v/magnitude for v in sf);chord=tuple(v/dt for v in sub(vector(new),vector(old)))
        correction=finite(flux)/magnitude-dot(normal,chord)
        expected=tuple(chord[i]+normal[i]*correction for i in range(3));actual=vector(actual)
        error=math.sqrt(dot(sub(actual,expected),sub(actual,expected)))
        require(error<=tolerance*max(1.,math.sqrt(dot(expected,expected))),"TEACHING_AUDIT_NATIVE_DISCRETE_MOVING_WALL_RULE_REQUIRED");errors.append(error)
    return {"equation":"U_wall=(Cf_new-Cf_old)/deltaT + n*(meshPhi/|Sf|-n dot (Cf_new-Cf_old)/deltaT)","allWallFacesChecked":len(errors),"maximumVelocityResidualMS":max(errors),"relativeTolerance":tolerance,"instantaneousOmegaCrossRSubstituted":False,"source":BASE_URL+"src/finiteVolume/fields/fvPatchFields/derived/movingWallVelocity/movingWallVelocityFvPatchVectorField.C"}


def stationary_wall_check(area_vectors,mesh_phi,stored_velocity=None,tolerance=1e-8):
    require(0<len(area_vectors)==len(mesh_phi)<=MAX_FACES and (stored_velocity is None or len(stored_velocity)==len(mesh_phi)),"TEACHING_AUDIT_ACTUAL_STATIONARY_WALL_FLUX_REQUIRED")
    tolerance=finite(tolerance,positive=True);require(tolerance<=1e-6,"TEACHING_AUDIT_STATIONARY_WALL_TOLERANCE_BOUND_REQUIRED")
    normals=[];errors=[]
    for index,(sf,flux) in enumerate(zip(area_vectors,mesh_phi,strict=True)):
        sf=vector(sf);area=math.sqrt(dot(sf,sf));require(area>0,"TEACHING_AUDIT_NONZERO_STATIONARY_WALL_AREA_REQUIRED")
        normal_velocity=finite(flux)/area;expected=tuple(x/area*normal_velocity for x in sf);normals.append(abs(normal_velocity))
        if stored_velocity is not None:
            actual=vector(stored_velocity[index]);error=math.sqrt(dot(sub(actual,expected),sub(actual,expected)))
            require(error<=tolerance*max(1.,abs(normal_velocity)),"TEACHING_AUDIT_NATIVE_NOSLIP_NORMAL_CORRECTION_REQUIRED");errors.append(error)
    return {"equation":"U_noSlip=n*(meshPhi/|Sf|); tangential velocity0 on moving native mesh", "allFacesChecked":len(normals),
        "maximumNormalMeshVelocityMS":max(normals),"absoluteZeroVelocityEstablished":max(normals)<=tolerance,
        "storedVectorComparisonPerformed":stored_velocity is not None,"nativeBoundaryRuleReconstruction":stored_velocity is None,
        "maximumStoredVectorResidualMS":max(errors,default=None),"source":BASE_URL+"src/finiteVolume/fields/fvPatchFields/derived/noSlip/noSlipFvPatchVectorField.C"}


def compare_full_state(left,right,tolerances):
    require(set(tolerances)=={"relative","absolute"},"TEACHING_AUDIT_EXPLICIT_FULL_STATE_TOLERANCES_REQUIRED")
    relative=finite(tolerances["relative"],positive=True);absolute=finite(tolerances["absolute"],positive=True)
    require(relative<=1e-6 and absolute<=1e-8 and left["topologySha256"]==right["topologySha256"] and left["cellCount"]==right["cellCount"]
        and left["timeState"]==right["timeState"] and left["addressing"]==right["addressing"],"TEACHING_AUDIT_MATCHED_TOPOLOGY_CLOCK_ADDRESSING_REQUIRED")
    require(not left["missingState"] and not right["missingState"] and set(left["fields"])==set(right["fields"])
        and REQUIRED_STATE-{"polyMesh/points","fvMesh/polyFaces","uniform/time"}<=set(left["fields"]),"TEACHING_AUDIT_COMPLETE_RESTART_STATE_REQUIRED")
    checked=[]
    for name in sorted(left["fields"]):
        a,b=left["fields"][name],right["fields"][name]
        require(a["patchTypes"]==b["patchTypes"] and a.get("implicitBoundaryRoles",{})==b.get("implicitBoundaryRoles",{})
            and set(a["boundary"])==set(b["boundary"]),"TEACHING_AUDIT_MATCHED_FULL_BOUNDARY_STATE_REQUIRED")
        for role,va,vb in [("internal",a["internal"],b["internal"]),*[(p,a["boundary"][p],b["boundary"][p]) for p in a["boundary"]]]:
            require(len(va)==len(vb),"TEACHING_AUDIT_MATCHED_FULL_STATE_SHAPE_REQUIRED")
            errors=[]
            for x,y in zip(va,vb,strict=True):
                x,y=finite(x),finite(y);error=abs(x-y)
                require(error<=absolute+relative*max(abs(x),abs(y)),"TEACHING_AUDIT_FULL_RESTART_FIELD_DIFFERENCE_REQUIRED:"+name+"/"+role);errors.append(error)
            checked.append({"field":name,"role":role,"allStoredComponentsChecked":len(va),"maximumAbsoluteDifference":max(errors,default=0.)})
    require(len(left["points"])==len(right["points"]),"TEACHING_AUDIT_MATCHED_FULL_RESTART_POINTS_REQUIRED")
    for a,b in zip(left["points"],right["points"],strict=True):
        require(all(abs(x-y)<=absolute+relative*max(abs(x),abs(y)) for x,y in zip(vector(a),vector(b),strict=True)),"TEACHING_AUDIT_FULL_RESTART_POINT_DIFFERENCE_REQUIRED")
    return {"allCellAndFaceFieldsCompared":True,"allMovedPointsCompared":True,"checks":checked,"tolerances":dict(tolerances),
        "executionProvenanceVerified":False,"runtimeFamilyVerified":False,"minimumPhysicalRestartDurationVerified":False,"restartVerified":False,
        "claimBoundary":"Complete supplied state-value comparison only; actual native checkpoint copying, identical installed runtime/control family and at least one physical revolution remain mandatory upstream."}


def clock_history(text):
    result={};previous=0.;pending=None
    for line in text.splitlines():
        match=re.fullmatch(r"deltaT = ("+FLOAT+r")",line.strip())
        if match:pending=finite(float(match[1]),positive=True)
        match=re.fullmatch(r"Time = ("+FLOAT+r")s?",line.strip())
        if match:
            current=finite(float(match[1]),positive=True);dt=current-previous
            require(current>previous and pending is not None and math.isclose(dt,pending,rel_tol=5e-10,abs_tol=1e-12),"TEACHING_AUDIT_ACTUAL_NATIVE_STEP_CLOCK_REQUIRED")
            result[current]={"timeS":current,"previousTimeS":previous,"dtS":dt,"previousDtS":result[previous]["dtS"] if previous in result else dt,"index":len(result)+1}
            previous=current;pending=None
            require(len(result)<=20000,"TEACHING_AUDIT_BOUNDED_NATIVE_CLOCK_REQUIRED")
    require(result and re.search(r"(?m)^End\s*$",text),"TEACHING_AUDIT_COMPLETED_NATIVE_CLOCK_REQUIRED")
    return result


def current_read(root,relative,records):
    require(isinstance(relative,str) and "\\" not in relative and not PurePosixPath(relative).is_absolute() and ".." not in PurePosixPath(relative).parts,"TEACHING_AUDIT_SAFE_RELATIVE_DATA_PATH_REQUIRED")
    raw,row=io.read(root,relative,MAX_FILE)
    require(sum(r["bytes"] for r in records.values())+ (0 if relative in records else row["bytes"])<=MAX_TOTAL,"TEACHING_AUDIT_BOUNDED_TOTAL_DATA_REQUIRED")
    require(relative not in records or records[relative]==row,"TEACHING_AUDIT_CURRENT_BYTE_IDENTITY_REQUIRED");records[relative]=row
    return raw


def native_time(raw,name,clock):
    body=plain(raw,"time","dictionary",name+"/uniform");pairs=re.findall(r"\b(beginTime|value|name|index|deltaT|deltaT0)\s+([^;]+);",body)
    require(len(pairs)==6 and len({k for k,_ in pairs})==6 and not re.sub(r"\b(beginTime|value|name|index|deltaT|deltaT0)\s+[^;]+;","",body).strip(),"TEACHING_AUDIT_EXACT_NATIVE_TIME_STATE_REQUIRED")
    data={k:v.strip().strip('"') for k,v in pairs};time=finite(float(name),positive=True)
    match=next((row for t,row in clock.items() if math.isclose(t,time,rel_tol=5e-11,abs_tol=1e-12)),None)
    require(match and data["name"]==name and re.fullmatch(r"[1-9]\d*",data["index"]) and int(data["index"])==match["index"]
        and float(data["beginTime"])==0 and math.isclose(float(data["value"]),time,rel_tol=5e-11,abs_tol=1e-12)
        and math.isclose(float(data["deltaT"]),match["dtS"],rel_tol=5e-10,abs_tol=1e-12)
        and math.isclose(float(data["deltaT0"]),match["previousDtS"],rel_tol=5e-10,abs_tol=1e-12),"TEACHING_AUDIT_LOG_BOUND_NATIVE_TIME_STATE_REQUIRED")
    return match


def addressing(raw,mesh,name):
    body=plain(raw,"polyFaces","surfaceLabelField",name+"/fvMesh")
    require(re.findall(r"\binternalField\s+uniform\s+(-?\d+)\s*;",body)==["-1"],"TEACHING_AUDIT_NATIVE_NCC_INTERNAL_MINUS_ONE_REQUIRED")
    dims=re.findall(r"\bdimensions\s*\[([^\[\]]*)\]\s*;",body)
    require(len(dims)==1 and " ".join(dims[0].split()) in {"","0 0 0 0 0 0 0"},"TEACHING_AUDIT_DIMENSIONLESS_NCC_ADDRESSING_REQUIRED")
    bound=re.fullmatch(r".*?\bboundaryField\s*\{(.*)\}\s*",body,re.S);require(bound,"TEACHING_AUDIT_COMPLETE_NCC_ADDRESSING_REQUIRED")
    patches=blocks(bound[1]);require(set(patches)==set(mesh["boundary"]),"TEACHING_AUDIT_COMPLETE_NCC_PATCH_IDENTITY_REQUIRED")
    mappings={};counts={}
    for patch,payload in patches.items():
        p=mesh["boundary"][patch];kinds=re.findall(r"\btype\s+([^;]+);",payload)
        require(len(kinds)==1 and kinds[0].strip() in {"polyFaces","nonConformalPolyFaces"},"TEACHING_AUDIT_EXACT_NCC_MAPPING_ROLE_REQUIRED")
        if kinds[0].strip()=="polyFaces":
            require(not p["type"].startswith("nonConformal"),"TEACHING_AUDIT_EXPLICIT_NCC_MAPPING_REQUIRED")
            mappings[patch]=array("q",range(p["startFace"],p["startFace"]+p["nFaces"]));counts[patch]=p["nFaces"]
        else:
            original=re.findall(r"\boriginalPatch\s+\"?([A-Za-z0-9_-]+)\"?\s*;",p["dictionary"])
            sizes=re.findall(r"\bsize\s+(\d+)\s*;",payload);decl=re.findall(r"\bvalue\s+(.*?);",payload,re.S)
            require(len(original)==len(sizes)==len(decl)==1 and original[0] in mesh["boundary"],"TEACHING_AUDIT_CURRENT_ORIGINAL_PATCH_RANGE_REQUIRED")
            source=mesh["boundary"][original[0]];count=int(sizes[0]);require(count<=MAX_FACES,"TEACHING_AUDIT_BOUNDED_NCC_INTERSECTIONS_REQUIRED")
            labels=values(decl[0],count,is_label=True)
            require(all(source["startFace"]<=v<source["startFace"]+source["nFaces"] for v in labels),"TEACHING_AUDIT_NCC_LABELS_IN_EXACT_SOURCE_RANGE_REQUIRED")
            mappings[patch]=labels;counts[patch]=count
    return mappings,counts


def read_snapshot(root,time_directory,mesh,clock,records=None):
    records={} if records is None else records
    require(isinstance(time_directory,str) and re.fullmatch(science.fields.TIME_NAME,time_directory) and float(time_directory)>0,"TEACHING_AUDIT_EXACT_POSITIVE_TIME_DIRECTORY_REQUIRED")
    folder=io.case_tool.regular_directory(Path(root)/"case"/time_directory)
    seen=set()
    for path in folder.rglob("*"):
        require(not path.is_symlink() and not getattr(path,"is_junction",lambda:False)(),"TEACHING_AUDIT_NO_STATE_LINKS_REQUIRED")
        if path.is_file():seen.add(path.relative_to(folder).as_posix())
        else:require(path.is_dir() and path.relative_to(folder).as_posix() in {"polyMesh","fvMesh","uniform"},"TEACHING_AUDIT_KNOWN_STATE_DIRECTORIES_REQUIRED")
    require(len(seen)<=32 and seen<=set(ROLES)|{"polyMesh/points","fvMesh/polyFaces","uniform/time","Vc0"},"TEACHING_AUDIT_KNOWN_FULL_STATE_ROLES_REQUIRED")
    prefix="case/"+time_directory+"/";missing=sorted(REQUIRED_STATE-seen)
    require(set(science.END_FIELDS)|{"polyMesh/points","fvMesh/polyFaces","uniform/time"}<=seen,"TEACHING_AUDIT_SCIENTIFIC_POINT_CLOCK_NCC_STATE_REQUIRED")
    points_raw=current_read(root,prefix+"polyMesh/points",records);body=plain(points_raw,"points","vectorField",time_directory+"/polyMesh")
    match=re.fullmatch(r"(\d+)\s*\((.*)\)\s*",body,re.S)
    require(match and int(match[1])==len(mesh["points"]),"TEACHING_AUDIT_ALL_NATIVE_POINT_LABELS_REQUIRED")
    flat=values("nonuniform List<vector> "+match[1]+" ("+match[2]+")",len(mesh["points"]),is_vector=True)
    pts=[tuple(flat[i:i+3]) for i in range(0,len(flat),3)]
    mappings,counts=addressing(current_read(root,prefix+"fvMesh/polyFaces",records),mesh,time_directory)
    temporal=native_time(current_read(root,prefix+"uniform/time",records),time_directory,clock)
    parsed={}
    for name in sorted(seen&set(ROLES)):
        parsed[name]=field(current_read(root,prefix+name,records),name,mesh,counts,time_directory)
    if "Vc0" in seen:
        body=plain(current_read(root,prefix+"Vc0",records),"Vc0","volScalarField::Internal",time_directory)
        dims=re.findall(r"\bdimensions\s*\[([^\[\]]*)\]\s*;",body);decl=re.findall(r"\bvalue\s+(.*?);",body,re.S)
        require(len(dims)==len(decl)==1 and " ".join(dims[0].split())=="0 3 0 0 0 0 0","TEACHING_AUDIT_VERIFIED_OPTIONAL_OLD_VOLUME_ROLE_REQUIRED")
        old=values(decl[0],mesh["cellCount"]);require(all(v>0 for v in old),"TEACHING_AUDIT_POSITIVE_OPTIONAL_OLD_VOLUMES_REQUIRED")
        parsed["Vc0"]={"name":"Vc0","internal":old,"boundary":{},"patchTypes":{},"class":"volScalarField::Internal","dimensions":"0 3 0 0 0 0 0"}
    require(all(math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-8) for a,b in zip(parsed["p"]["internal"],parsed["p_rgh"]["internal"],strict=True)),"TEACHING_AUDIT_ALL_CELL_ABSOLUTE_PA_G0_REQUIRED")
    return {"timeDirectory":time_directory,"timeState":temporal,"fields":parsed,"points":pts,"addressing":mappings,
        "patchCounts":counts,"missingState":missing,"cellCount":mesh["cellCount"],"topologySha256":mesh["topologySha256"],"records":records}


def surface_values(snapshot,name,mesh):
    data=snapshot["fields"].get(name);require(data and set(data["boundary"])==set(mesh["boundary"]),"TEACHING_AUDIT_COMPLETE_NATIVE_FACE_FIELD_REQUIRED:"+name)
    is_vector=data["class"].endswith("VectorField")
    chunks=[data["internal"],*[data["boundary"][p] for p in mesh["boundary"]]]
    combined=array("d")
    for chunk in chunks:combined.extend(chunk)
    return [tuple(combined[i:i+3]) for i in range(0,len(combined),3)] if is_vector else combined


def expanded_owners(mesh,snapshot):
    owners=array("q",mesh["owner"][:len(mesh["neighbour"])])
    for patch in mesh["boundary"]:owners.extend(mesh["owner"][f] for f in snapshot["addressing"][patch])
    require(len(owners)<=MAX_FACES,"TEACHING_AUDIT_BOUNDED_EXPANDED_FACE_TOPOLOGY_REQUIRED")
    return owners


def summary_snapshot(snapshot):
    return {"timeDirectory":snapshot["timeDirectory"],"timeState":snapshot["timeState"],"allCellsChecked":snapshot["cellCount"],
        "fields":[{"name":n,"internalComponentCount":len(v["internal"]),"storedBoundaryComponentCount":sum(len(a) for a in v["boundary"].values()),"dimensions":v["dimensions"]} for n,v in snapshot["fields"].items()],
        "missingMandatoryStoredRestartRoles":snapshot["missingState"],"completeStoredRestartState":not snapshot["missingState"]}


def scientific_endpoint(root,name,mesh,records):
    checks=[];pressures={}
    for field_name in science.END_FIELDS:
        raw=current_read(root,"case/"+name+"/"+field_name,records)
        body=plain(raw,field_name,"volVectorField" if field_name=="U" else "volScalarField",name)
        checks.append(science.finite_field(raw,field_name,mesh["cellCount"]))
        if field_name in {"p","p_rgh"}:
            declarations=re.findall(r"\binternalField\s+(.*?);",body,re.S)
            require(len(declarations)==1,"TEACHING_AUDIT_ONE_ABSOLUTE_PRESSURE_FIELD_REQUIRED")
            pressures[field_name]=values(declarations[0],mesh["cellCount"])
    require(all(math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-8) for a,b in zip(pressures["p"],pressures["p_rgh"],strict=True)),"TEACHING_AUDIT_ALL_CELL_ABSOLUTE_PA_G0_REQUIRED")
    return {"timeDirectory":name,"allInternalCellsChecked":mesh["cellCount"],"sevenFieldChecks":checks,"absolutePressureReconstruction":"p_absolute=p_rgh under authored g0; atmospheric addition0Pa","fullNccBoundaryStateCheckedSeparately":True}


def audit_transient(execution_directory,packet=None):
    output={"schemaVersion":SCHEMA,"sourceGeometryRoute":"CLASSROOM_AUTHORED_GEOMETRY",**dict.fromkeys(FALSE_FLAGS,False),
        "coordinateMotionVerified":False,"dynamicNccClockCoverageVerified":False,"scientificEndpointFieldsVerified":False,
        "rigidCellVolumesVerified":False,"eulerGclEvidenceVerified":False,"relativeFluxEvidenceVerified":False,
        "physicalWallVelocityEvidenceVerified":False,"checkpointStateComplete":False,"fullRestartStateComparisonVerified":False,
        "status":"TRANSIENT_NATIVE_DATA_REQUIRED","diagnostics":[],"sourceRestartPolicy":SOURCE_POLICY,"evidenceFileRecords":[],
        "limits":{"maximumFileBytes":MAX_FILE,"maximumDistinctBoundEvidenceBytes":MAX_TOTAL,"maximumPositiveTimeFrames":MAX_FRAMES,
            "maximumCells":MAX_CELLS,"maximumFaces":MAX_FACES,"maximumPoints":MAX_POINTS,"maximumReportBytes":MAX_RECORD},
        "claimBoundary":"Current byte-bound data/math audit only. Missing ALE or restart data does not erase independently valid coordinates. This audit confers no executed or qualified CFD/head/physical/restart/convergence claim."}
    records={};mesh=None;clock=None;snapshots={}
    def attempt(branch,action):
        try:return action()
        except (ValueError,OSError,KeyError,TypeError,UnicodeError,OverflowError) as error:
            output["diagnostics"].append({"branch":branch,"reasonCode":str(error)[:500]});return None
    try:
        import teaching_pump_motion_evidence as motion
        root=io.case_tool.regular_directory(execution_directory);inputs=motion.motion_inputs(root)
        mesh=dict(inputs);mesh["cellCount"]=max(max(mesh["owner"]),max(mesh["neighbour"],default=0))+1
        require(0<mesh["cellCount"]<=MAX_CELLS,"TEACHING_AUDIT_BOUNDED_CURRENT_CELLS_REQUIRED")
        require(isinstance(inputs["records"],dict) and 5<=len(inputs["records"])<=384
            and all(isinstance(r,dict) and set(r)=={"path","bytes","sha256"} and r["path"]==p for p,r in inputs["records"].items()),"TEACHING_AUDIT_TYPED_MOTION_RECORD_MAPPING_REQUIRED")
        source_rows=list(inputs["records"].values())
        topology=[r for r in source_rows if re.fullmatch(r"case/constant/polyMesh/(?:faces|owner|neighbour|boundary|cellZones)",r["path"])]
        require(len(topology)==5,"TEACHING_AUDIT_FULL_CURRENT_TOPOLOGY_ORDER_REQUIRED")
        mesh["topologySha256"]=sha256(io.encoded(sorted(topology,key=lambda r:r["path"]))).hexdigest()
        for row in source_rows:
            require(current_read(root,row["path"],records) is not None and records[row["path"]]==row,"TEACHING_AUDIT_CURRENT_MOTION_SOURCE_BYTES_REQUIRED")
        log=current_read(root,"logs/01-foamRun.log",records).decode();clock=clock_history(log)
        time_dirs=sorted((float(p.name),p) for p in (root/"case").iterdir() if p.is_dir() and re.fullmatch(science.fields.TIME_NAME,p.name) and float(p.name)>0)
        require(0<len(time_dirs)<=MAX_FRAMES,"TEACHING_AUDIT_BOUNDED_CURRENT_WRITES_REQUIRED")
        proof=motion.inspect(root,time_dirs,log)
        proof_records=proof.get("nativeEvidenceRecords")
        require(isinstance(proof_records,list) and len(proof_records)<=384,"TEACHING_AUDIT_COMPLETE_MOTION_EVIDENCE_ROWS_REQUIRED")
        for row in proof_records:
            require(current_read(root,row["path"],records) is not None and records[row["path"]]==row,"TEACHING_AUDIT_CURRENT_COORDINATE_SOURCE_BYTES_REQUIRED")
        output.update(coordinateMotionVerified=proof.get("coordinateMotionVerified") is True,
            dynamicNccClockCoverageVerified=proof.get("dynamicNccClockCoverageVerified") is True,
            coordinateEvidence={"recomputedFullProofSha256":sha256(io.encoded(proof)).hexdigest(),"positiveTimeWriteCount":len(time_dirs),
                "nativeClockRecordCount":len(clock),"byteBoundMotionEvidenceRecordCount":len(proof_records),
                "fullProofEmbedded":False,"fullProofRetentionVerified":False,
                "requiredSeparateArtifact":"Execution runner must retain its full current motion proof; compact SHA does not establish artifact retention."})
        endpoint=attempt("scientific-endpoint",lambda:scientific_endpoint(root,time_dirs[-1][1].name,mesh,records))
        if endpoint:output.update(scientificEndpointFieldsVerified=True,scientificEndpointEvidence=endpoint)
        if packet is None:packet={"schemaVersion":INPUT_SCHEMA,"aleStepPairs":[],"checkpointTimeDirectory":time_dirs[0][1].name}
        require(isinstance(packet,dict) and packet.get("schemaVersion")==INPUT_SCHEMA and set(packet)<={"schemaVersion","aleStepPairs","checkpointTimeDirectory","restartComparison"},"TEACHING_AUDIT_TYPED_PACKET_REQUIRED")
        for _,path in time_dirs:
            snapshot=attempt("native-state/"+path.name,lambda p=path:read_snapshot(root,p.name,mesh,clock,records))
            if snapshot:snapshots[path.name]=snapshot
        if time_dirs[-1][1].name in snapshots:
            endpoint=snapshots[time_dirs[-1][1].name];output["endpoint"]=summary_snapshot(endpoint)
        volumes=[]
        for name,snapshot in snapshots.items():
            checked=attempt("rigid-cell-volumes/"+name,lambda s=snapshot:rigid_volume_check(mesh,mesh["points"],s["points"]))
            if checked:volumes.append({k:v for k,v in checked.items() if k not in {"oldVolumes","newVolumes"}})
        output["rigidCellVolumesVerified"]=bool(snapshots) and len(volumes)==len(snapshots);output["rigidVolumeEvidence"]=volumes
        checkpoint=snapshots.get(packet.get("checkpointTimeDirectory"))
        if checkpoint:
            output["checkpoint"]=summary_snapshot(checkpoint);output["checkpointStateComplete"]=not checkpoint["missingState"]
            if checkpoint["missingState"]:output["diagnostics"].append({"branch":"checkpoint","reasonCode":"TEACHING_AUDIT_COMPLETE_STORED_RESTART_STATE_REQUIRED","missing":checkpoint["missingState"]})
        else:output["diagnostics"].append({"branch":"checkpoint","reasonCode":"TEACHING_AUDIT_ACTUAL_CHECKPOINT_REQUIRED"})
        pairs=packet.get("aleStepPairs",[]);require(isinstance(pairs,list) and len(pairs)<=MAX_FRAMES,"TEACHING_AUDIT_BOUNDED_ACTUAL_STEP_PAIRS_REQUIRED")
        ale=[]
        for pair in pairs:
            def step():
                require(isinstance(pair,dict) and set(pair)=={"previousTimeDirectory","timeDirectory"},"TEACHING_AUDIT_TYPED_STEP_PAIR_REQUIRED")
                old,new=(snapshots[n] for n in (pair["previousTimeDirectory"],pair["timeDirectory"]))
                dt=new["timeState"]["dtS"]
                require(math.isclose(float(old["timeDirectory"]),new["timeState"]["previousTimeS"],rel_tol=5e-10,abs_tol=1e-12),"TEACHING_AUDIT_CONSECUTIVE_NATIVE_STEPS_REQUIRED")
                volume=rigid_volume_check(mesh,old["points"],new["points"])
                phir,moving,uf,sf=(surface_values(new,n,mesh) for n in ("phi","meshPhi","Uf","Sf"))
                gcl=discrete_gcl_check(volume["oldVolumes"],volume["newVolumes"],expanded_owners(mesh,new),mesh["neighbour"],moving,dt)
                relative=relative_flux_check(phir,moving,uf,sf)
                old_cf,_=triangle_geometry(old["points"],mesh["faces"]);new_cf,_=triangle_geometry(new["points"],mesh["faces"])
                wall=[];stationary=[]
                for patch in ("blade","impeller-hub","impeller-shroud"):
                    p=mesh["boundary"][patch];ids=range(p["startFace"],p["startFace"]+p["nFaces"])
                    require(new["fields"]["U"]["patchTypes"][patch]=="movingWallVelocity","TEACHING_AUDIT_MOVING_PHYSICAL_WALL_TYPE_REQUIRED")
                    def triples(a):return [tuple(a[i:i+3]) for i in range(0,len(a),3)]
                    wall.append({"patch":patch,**moving_wall_check([old_cf[i] for i in ids],[new_cf[i] for i in ids],triples(new["fields"]["Sf"]["boundary"][patch]),new["fields"]["meshPhi"]["boundary"][patch],triples(new["fields"]["U"]["boundary"][patch]),dt)})
                for patch in ("inblock-hub","inblock-shroud","volute-wall","volute-inlet-wall"):
                    require(new["fields"]["U"]["patchTypes"][patch]=="noSlip","TEACHING_AUDIT_STATIONARY_PHYSICAL_WALL_REQUIRED")
                    stored=new["fields"]["U"]["boundary"].get(patch)
                    stationary.append({"patch":patch,**stationary_wall_check(triples(new["fields"]["Sf"]["boundary"][patch]),new["fields"]["meshPhi"]["boundary"][patch],triples(stored) if stored is not None else None)})
                return {"previousTimeS":float(old["timeDirectory"]),"timeS":float(new["timeDirectory"]),"gcl":gcl,"relativeFlux":relative,"movingWalls":wall,"stationaryWalls":stationary}
            checked=attempt("ALE-step",step)
            if checked:ale.append(checked)
        output["aleStepEvidence"]=ale
        if pairs and len(ale)==len(pairs):output.update(eulerGclEvidenceVerified=True,relativeFluxEvidenceVerified=True,physicalWallVelocityEvidenceVerified=True)
        else:output["diagnostics"].append({"branch":"ALE","reasonCode":"TEACHING_AUDIT_LAST_NATIVE_STEP_GEOMETRY_AND_FLUX_REQUIRED","action":"Sparse quarter-revolution writes do not provide oldPoints for the actual last deltaT. Retain consecutive native-step points and full native meshPhi/Uf/Sf/physical patch U, or a separately reviewed compact per-step native exporter."})
        if packet.get("restartComparison") is not None:
            output["diagnostics"].append({"branch":"restart","reasonCode":"TEACHING_AUDIT_MATCHED_NATIVE_RESTART_EXECUTION_REQUIRED","action":"Use compare_full_state only after actual matching clocks, complete checkpoint-copy provenance, identical runtime/family and at least one physical revolution of separately executed continuous/resumed runs are bound."})
        require(all(io.read(root,r["path"],MAX_FILE)[1]==r for r in records.values()),"TEACHING_AUDIT_POSTREAD_BYTE_IMMUTABILITY_REQUIRED")
    except (ValueError,OSError,KeyError,TypeError,UnicodeError,OverflowError,ImportError) as error:
        output["diagnostics"].append({"branch":"source-and-clock","reasonCode":str(error)[:500]})
        if any(marker in str(error) for marker in ("IMMUTABILITY","BYTE_IDENTITY","SOURCE_BYTES")):
            for key in tuple(output):
                if key.endswith("Verified") or key=="checkpointStateComplete":output[key]=False
            output["nativeEvidenceChangedDuringAudit"]=True
    output["evidenceFileRecords"]=sorted(records.values(),key=lambda r:r["path"])
    output["status"]="PARTIAL_TRANSIENT_EVIDENCE_AUDITED_OTHER_BRANCHES_PENDING" if output["coordinateMotionVerified"] and output["dynamicNccClockCoverageVerified"] and output["scientificEndpointFieldsVerified"] else "TRANSIENT_EVIDENCE_DIAGNOSTIC_REQUIRED"
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--execution-directory",type=Path,required=True);parser.add_argument("--audit-input",type=Path)
    args=parser.parse_args();root=science.safety.scope_path(args.execution_directory)
    packet=None
    if args.audit_input:
        def unique(pairs):
            result={}
            for key,value in pairs:
                require(key not in result,"TEACHING_AUDIT_UNIQUE_INPUT_KEYS_REQUIRED");result[key]=value
            return result
        packet=json.loads(io.case_tool.read_regular(science.safety.scope_path(args.audit_input),MAX_RECORD).decode("utf-8"),object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(AuditIssue("TEACHING_AUDIT_FINITE_INPUT_JSON_REQUIRED")))
    result=audit_transient(root,packet);raw=io.encoded(result)
    require(len(raw)<=MAX_RECORD,"TEACHING_AUDIT_BOUNDED_REPORT_REQUIRED");io.case_tool.write_exclusive(root/REPORT,raw)
    print(json.dumps({k:result[k] for k in ("coordinateMotionVerified","checkpointStateComplete","eulerGclEvidenceVerified","restartVerified","actualCfdRunVerified","mainApplicationWrites")}))
    return 0 if result["coordinateMotionVerified"] and result["dynamicNccClockCoverageVerified"] and result["scientificEndpointFieldsVerified"] else 2


if __name__=="__main__":raise SystemExit(main())
