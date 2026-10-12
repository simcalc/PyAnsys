"""Author a distinct SI classroom pump CAD and mesh; never run a flow solver.

The geometry is a teaching design, not a Grundfos product or a reconstruction of
the official Fluent example. Native Gmsh generation is restricted to the public
manual FREE worker. Pure parameter, equation and file checks launch no software.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
SCHEMA = "untirta.teaching-pump-geometry.v1"
MANIFEST_SCHEMA = "untirta.teaching-pump-input-manifest.v1"
IDENTITY_SCHEMA = "untirta.classroom-authored-pump-geometry-identity.v1"
REPORT = "teaching-geometry.json"
MANIFEST = "teaching-input-manifest.json"
FILES = ("impeller.msh", "volute.msh", "teaching-pump.brep")
GMSH_VERSION = "4.15.2"
LIMITS = {"maximumSeconds":900,"cpus":1,"maximumMemoryBytes":4*1024**3,"maximumWorkspaceBytes":512*1024**2,
          "maximumFileBytes":128*1024**2,"maximumReportBytes":128*1024,"maximumLogBytes":4*1024**2,
          "maximumCellsPerComponent":450000,"maximumCombinedCells":500000,"maximumNodesPerComponent":500000,"maximumCombinedNodes":1000000}
LEGACY_MESH_LEVELS = {"coarse":.0055,"medium":.0042,"fine":.0032}
LEAN_MESH_LEVELS = {"lean-coarse":.0095,"lean-medium":.0075,"lean-fine":.0055}
MESH_LEVELS = {**LEGACY_MESH_LEVELS, **LEAN_MESH_LEVELS}
DEFAULTS = {"impellerTipRadiusM":.13,"interfaceRadiusM":.1405,"diskHeightM":.04363,"hubRadiusM":.01968,
            "eyeRadiusM":.0844,"inletLengthM":.06,"bladeStartRadiusM":.0875,"bladeThicknessM":.004,
            "bladeSweepRad":-.7,"bladeCount":6,"voluteWidthStartM":.01,"voluteWidthEndM":.12,
            "outletLengthM":.15,"outletOverlapM":.02,"speedRpm":1450.,"massFlowKgS":90.,
            "densityKgM3":998.2,"viscosityPaS":.001002,"temperatureK":293.15,"vaporPressurePa":2339.,"gravityMS2":9.81}
GEOMETRY_KEYS = tuple(key for key in DEFAULTS if key not in {"speedRpm","massFlowKgS","densityKgM3","viscosityPaS","temperatureK","vaporPressurePa","gravityMS2"})
ROLES = {"impeller":{"inlet":15,"inblock-shroud":14,"inblock-hub":13,"interface-impeller-outlet":10,"blade":9,"impeller-shroud":7,"impeller-hub":3},
         "volute":{"mass-flow-inlet-11":11,"volute-wall":12,"volute-inlet-wall":8,"interface-volute-inlet":5}}
INTERFACE = {"impeller":"interface-impeller-outlet","volute":"interface-volute-inlet"}
REFERENCES = ["https://gmsh.info/doc/texinfo/gmsh.html#t16","https://gmsh.info/doc/texinfo/gmsh.html#Namespace-gmsh_002fmodel_002focc",
              "https://gmsh.info/doc/texinfo/gmsh.html#MSH-file-format-version-2-_0028Legacy_0029"]
FALSE_FLAGS = ("manufacturerGeometryVerified","originalFluentGeometryReproduced","meshQualityCheckedBySolver","nativeQualityAccepted",
               "cfdReady","qualifiedBaseline","actualCfdRunVerified","simulationExecuted","npsh3Verified","physicalTransientQualified","restartVerified","mainApplicationWrites")


class GeometryIssue(ValueError): pass


def require(value, action):
    if not value: raise GeometryIssue(action)


def encoded(value): return json.dumps(value, sort_keys=True, separators=(",",":"), allow_nan=False).encode("ascii")


def number(value):
    require(type(value) in (int,float) and math.isfinite(value), "Every authored parameter must be a finite real SI value.")
    return float(value)


def validate_parameters(values=None):
    require(values is None or isinstance(values,dict), "Use an explicit parameter object; absent values use declared classroom defaults.")
    supplied = values or {}
    require(set(supplied) <= set(DEFAULTS), "Unknown geometry, credential and executable fields are prohibited.")
    p = {**DEFAULTS, **supplied}
    require(type(p["bladeCount"]) is int and 4 <= p["bladeCount"] <= 12, "Declare an integer blade count between4and12.")
    for key in p:
        if key != "bladeCount":p[key] = number(p[key])
    require(all(p[key] > 0 for key in p if key not in {"bladeSweepRad","bladeCount"}), "Authored lengths, operating inputs and liquid properties must be positive.")
    require(.005 <= p["hubRadiusM"] < p["eyeRadiusM"] < p["bladeStartRadiusM"] < p["impellerTipRadiusM"] < p["interfaceRadiusM"] <= .3,
            "Hub, eye, blade start, impeller tip and circular interface radii must be strictly ordered.")
    require(.01 <= p["diskHeightM"] <= .1 and .02 <= p["inletLengthM"] <= .2 and .002 <= p["bladeThicknessM"] <= .01,
            "Use bounded educational disk, inlet and blade dimensions in metres.")
    require(p["bladeStartRadiusM"]-p["eyeRadiusM"] > p["bladeThicknessM"]/2
            and p["interfaceRadiusM"]-p["impellerTipRadiusM"] > p["bladeThicknessM"]
            and p["bladeThicknessM"] < .1*(2*math.pi*p["bladeStartRadiusM"]/p["bladeCount"]),
            "Preserve explicit eye, rotor/stator and neighbouring blade clearances.")
    require(-1.2 <= p["bladeSweepRad"] <= -.2 and .005 <= p["voluteWidthStartM"] < p["voluteWidthEndM"] <= .2
            and .04 <= p["outletLengthM"] <= .3 and .005 <= p["outletOverlapM"] <= .04,
            "Use backward curved blades and a growing bounded volute with an explicit tangential outlet overlap.")
    require(p["interfaceRadiusM"]+p["voluteWidthEndM"] <= .6 and p["outletOverlapM"] < p["outletLengthM"], "The authored casing must remain within the bounded CAD domain.")
    require(100 <= p["speedRpm"] <= 5000 and .01 <= p["massFlowKgS"] <= 150 and 500 <= p["densityKgM3"] <= 1500
            and 1e-5 <= p["viscosityPaS"] <= .1 and 273.15 <= p["temperatureK"] <= 373.15
            and 0 < p["vaporPressurePa"] <= 150000 and 1 <= p["gravityMS2"] <= 20,
            "Operating declarations must remain inside the bounded classroom input domain; they do not predict pump performance.")
    return p


def equation(inputs, expression, substitution, result, unit):
    require(math.isfinite(result), "An authored equation result must be finite.")
    return {"input":inputs,"equation":expression,"substitution":substitution,"result":{"value":result,"unit":unit},"calculationAuthority":"Python"}


def resolution_sizing(mesh_level):
    """Lean planning metadata only; actual count and all native gates are separate."""
    require(isinstance(mesh_level,str) and mesh_level in MESH_LEVELS,"Choose an exact reviewed mesh resolution ID.")
    if mesh_level not in LEAN_MESH_LEVELS:return None
    return {"family":"lean-three","declaredCombinedCellTargetRange":[50000,250000],
        "targetIsMeasuredCellCount":False,"targetIsAcceptanceGate":False,"countsPredictedOrGuaranteed":False,
        "nativeLimitsUnchanged":True,"meshConvergenceDemonstrated":False,"sameDiscreteBoundaryClaimed":False,
        "oldNativePacketCompatibilityClaimed":False,
        "scope":"Coarser sizing candidates on the exact same authored CAD. Actual counts, surface tessellation, native quality/interface coverage and refinement results must be measured independently; the target range is not an acceptance or count promise."}


def teaching_plan(values=None, mesh_level="coarse"):
    p = validate_parameters(values); require(isinstance(mesh_level,str) and mesh_level in MESH_LEVELS, "Choose coarse, medium or fine explicitly.")
    rpm,rho,mass=p["speedRpm"],p["densityKgM3"],p["massFlowKgS"]
    omega=2*math.pi*rpm/60;area=math.pi*(p["eyeRadiusM"]**2-p["hubRadiusM"]**2);q=mass/rho
    stages={"angularSpeed":equation({"N_rpm":rpm},"omega=2*pi*N/60",f"2*pi*{rpm}/60",omega,"rad/s"),
            "volumeFlow":equation({"massFlowKgS":mass,"densityKgM3":rho},"Q=massFlow/rho",f"{mass}/{rho}",q,"m^3/s"),
            "inletArea":equation({"eyeRadiusM":p["eyeRadiusM"],"hubRadiusM":p["hubRadiusM"]},"A_in=pi*(R_eye^2-R_hub^2)",f"pi*({p['eyeRadiusM']}^2-{p['hubRadiusM']}^2)",area,"m^2"),
            "declaredMeanInletSpeed":equation({"Qm3S":q,"AinM2":area},"U_mean=Q/A_in",f"{q}/{area}",q/area,"m/s"),
            "tipSpeed":equation({"omegaRadS":omega,"tipRadiusM":p["impellerTipRadiusM"]},"U_tip=omega*R_tip",f"{omega}*{p['impellerTipRadiusM']}",omega*p["impellerTipRadiusM"],"m/s"),
            "interfaceArea":equation({"interfaceRadiusM":p["interfaceRadiusM"],"diskHeightM":p["diskHeightM"]},"A_interface=2*pi*R_interface*h",f"2*pi*{p['interfaceRadiusM']}*{p['diskHeightM']}",2*math.pi*p["interfaceRadiusM"]*p["diskHeightM"],"m^2")}
    identity={"schemaVersion":IDENTITY_SCHEMA,"provenance":"CLASSROOM_AUTHORED_GEOMETRY","units":"SI","recipeVersion":1,
              "parameters":{key:p[key] for key in GEOMETRY_KEYS},"axis":[0,0,1],"originM":[0,0,0],
              "construction":"OCC annular axial eye + blade-subtracted disk; growing polar volute plus tangential outlet; one shared cylindrical CAD interface from fragments",
              "manufacturerIdentity":None,"originalFluentSourceIdentity":None}
    plan={"schemaVersion":"untirta.teaching-pump-cad-plan.v1","status":"AuthoredPlanNativeCADAndMeshRequired","provenance":"CLASSROOM_AUTHORED_GEOMETRY",
            "units":"SI","parameters":p,"parametersSha256":sha256(encoded(p)).hexdigest(),"geometryIdentity":identity,
            "geometryIdentitySha256":sha256(encoded(identity)).hexdigest(),"meshLevel":mesh_level,"meshSizeM":MESH_LEVELS[mesh_level],
            "meshCellTargetRange":[200000,500000],"limits":dict(LIMITS),"equationStages":stages,"boundaryRoleMap":ROLES,
            "fluidBasis":"Declared classroom water at20C; density/viscosity/vapour pressure are assumptions, not a property-source validation.",
            "requiredAcceptance":["Actual OCC topology, nonempty named patches and connected fluid regions","Positive actual cells, exact face incidence and CAD/mesh volume agreement",
                                  "Same initial interface coordinates/triangles with opposite outward ownership","Independent native full checkMesh and NCC","Actual numerical flow/cavitation/transient gates"],
            "references":REFERENCES,**{key:False for key in FALSE_FLAGS},"nativeCADConstructed":False,"volumeMeshGenerated":False,
            "claimBoundary":"Geometry and independent input equations only. No head, NPSHR, pump curve, solver result or CFD readiness is calculated."}
    sizing=resolution_sizing(mesh_level)
    if sizing is not None:
        plan["meshCellTargetRange"]=list(sizing["declaredCombinedCellTargetRange"])
        plan["resolutionSizing"]=sizing
    return plan


def blade_edges(p, blade_index):
    """Smooth backwards polar centreline with a constant normal thickness."""
    start,tip=p["bladeStartRadiusM"],p["impellerTipRadiusM"];slope=p["bladeSweepRad"]/(tip-start)
    left,right=[],[]
    for i in range(13):
        r=start+(tip-start)*i/12;theta=2*math.pi*blade_index/p["bladeCount"]+p["bladeSweepRad"]*i/12
        x,y=r*math.cos(theta),r*math.sin(theta);dx,dy=math.cos(theta)-r*slope*math.sin(theta),math.sin(theta)+r*slope*math.cos(theta)
        norm=math.hypot(dx,dy);nx,ny=-dy/norm,dx/norm
        left.append((x+nx*p["bladeThicknessM"]/2,y+ny*p["bladeThicknessM"]/2,0.))
        right.append((x-nx*p["bladeThicknessM"]/2,y-ny*p["bladeThicknessM"]/2,0.))
    return left,right


def build_cad(gmsh, parameters):
    """Construct CAD only. No mesh generation, solver, original file or network."""
    p=validate_parameters(parameters);occ=gmsh.model.occ;gmsh.model.add("classroom-authored-pump")
    ri,rh,re,h,L=p["interfaceRadiusM"],p["hubRadiusM"],p["eyeRadiusM"],p["diskHeightM"],p["inletLengthM"]
    def annulus(r,z,height):
        outer=occ.addCylinder(0,0,z,0,0,height,r);inner=occ.addCylinder(0,0,z,0,0,height,rh)
        result,_=occ.cut([(3,outer)],[(3,inner)]);require(len(result)==1 and result[0][0]==3,"Each annular CAD region must be one volume.");return result[0][1]
    rotor=annulus(ri,0,h);intake=annulus(re,-L,L);blades=[]
    for k in range(p["bladeCount"]):
        left,right=blade_edges(p,k);a=[occ.addPoint(*v) for v in left];b=[occ.addPoint(*v) for v in right]
        curves=[occ.addSpline(a),occ.addLine(a[-1],b[-1]),occ.addSpline(list(reversed(b))),occ.addLine(b[0],a[0])]
        surface=occ.addPlaneSurface([occ.addCurveLoop(curves)]);extruded=occ.extrude([(2,surface)],0,0,h)
        volumes=[tag for dim,tag in extruded if dim==3];require(len(volumes)==1,"Each authored blade must be one closed extruded solid.");blades.append((3,volumes[0]))
    result,_=occ.cut([(3,rotor)],blades);require(len(result)==1 and result[0][0]==3,"The blade-subtracted rotor must remain connected.");rotor=result[0][1]
    spiral=[]
    for i in range(65):
        theta=2*math.pi*i/64;radius=ri+p["voluteWidthStartM"]+(p["voluteWidthEndM"]-p["voluteWidthStartM"])*i/64
        xy=(radius,0.,0.) if i in (0,64) else (radius*math.cos(theta),radius*math.sin(theta),0.)
        spiral.append(occ.addPoint(*xy))
    curve=occ.addSpline(spiral);close=occ.addLine(spiral[-1],spiral[0]);surface=occ.addPlaneSurface([occ.addCurveLoop([curve,close])])
    volume=[tag for dim,tag in occ.extrude([(2,surface)],0,0,h) if dim==3];require(len(volume)==1,"The authored growing volute must be one closed solid.")
    x0=ri+p["voluteWidthStartM"];width=p["voluteWidthEndM"]-p["voluteWidthStartM"]
    outlet=occ.addBox(x0,-p["outletOverlapM"],0,width,p["outletLengthM"]+p["outletOverlapM"],h)
    casing,_=occ.fuse([(3,volume[0])],[(3,outlet)]);require(len(casing)==1,"The tangential outlet must actually join the volute.")
    centre=occ.addCylinder(0,0,0,0,0,h,ri);stator,_=occ.cut(casing,[(3,centre)]);require(len(stator)==1,"The stationary casing must be one annular fluid region.")
    fragment,mapping=occ.fragment([(3,rotor),(3,intake)],[stator[0]]);occ.synchronize()
    require(len(mapping)==3 and all(row and all(dim==3 for dim,tag in row) for row in mapping),"Every authored region must survive the common OCC fragmentation.")
    groups={"impeller":sorted({tag for row in mapping[:2] for dim,tag in row}),"volute":sorted({tag for dim,tag in mapping[2]})}
    require(not set(groups["impeller"]) & set(groups["volute"]) and set(fragment)=={(3,tag) for row in groups.values() for tag in row},"Fluid regions must be distinct and account for every fragmented volume.")
    def surfaces(volumes):
        counts=Counter(abs(tag) for volume in volumes for dim,tag in gmsh.model.getBoundary([(3,volume)],combined=False,oriented=True) if dim==2)
        require(all(c in (1,2) for c in counts.values()),"Fluid CAD surface incidence cannot exceed two volumes.")
        return {tag for tag,count in counts.items() if count==1}
    ext={name:surfaces(tags) for name,tags in groups.items()};common=ext["impeller"] & ext["volute"]
    require(common,"The two fluid groups must share actual CAD surfaces, not a projected guessed interface.")
    roles={name:{key:[] for key in ROLES[name]} for name in ROLES};tol=3e-7
    for component in groups:
        for tag in sorted(ext[component]):
            box=gmsh.model.getBoundingBox(2,tag);cx,cy,cz=occ.getCenterOfMass(2,tag)
            def plane(z):return abs(box[2]-z)<=tol and abs(box[5]-z)<=tol
            if tag in common:role=INTERFACE[component]
            elif component=="volute":
                if abs(box[1]-p["outletLengthM"])<=tol and abs(box[4]-p["outletLengthM"])<=tol:role="mass-flow-inlet-11"
                elif plane(0) or plane(h):role="volute-inlet-wall"
                else:role="volute-wall"
            elif plane(-L):role="inlet"
            elif box[5]<=tol and box[2]<-tol:
                radius=max(abs(box[0]),abs(box[1]),abs(box[3]),abs(box[4]))
                require(gmsh.model.getType(2,tag)=="Cylinder" and min(abs(radius-rh),abs(radius-re))<=tol,"Stationary intake walls must remain explicit coaxial cylinders.")
                role="inblock-hub" if abs(radius-rh)<abs(radius-re) else "inblock-shroud"
            elif plane(h):role="impeller-shroud"
            elif plane(0):role="impeller-hub"
            elif gmsh.model.getType(2,tag)=="Cylinder" and abs(max(abs(box[0]),abs(box[1]),abs(box[3]),abs(box[4]))-rh)<=tol:role="impeller-hub"
            else:
                require(abs(box[2])<=tol and abs(box[5]-h)<=tol,"Blade faces must span the declared rotor disk without unknown CAD caps.");role="blade"
            roles[component][role].append(tag)
    require(all(tags for row in roles.values() for tags in row.values()),"Every declared physical patch role must be nonempty in actual CAD.")
    for component,tags in groups.items():
        gmsh.model.addPhysicalGroup(3,tags,1 if component=="impeller" else 2)
        gmsh.model.setPhysicalName(3,1 if component=="impeller" else 2,"rotating-fluid" if component=="impeller" else "stationary-fluid")
    # A common surface has one CAD/mesh identity. Per-component aliases are
    # assigned by the exporter using its actual owning tetrahedron orientation.
    masses={name:math.fsum(occ.getMass(3,tag) for tag in tags) for name,tags in groups.items()}
    require(all(math.isfinite(value) and value>0 for value in masses.values()),"Both actual authored CAD fluid volumes must be finite and positive.")
    return {"volumeGroups":groups,"surfaceRoles":roles,"commonInterfaceSurfaceIds":sorted(common),
            "cadFluidVolumesM3":masses,
            "cadSurfaceClassificationToleranceM":tol,"cadSurfaceRolesBasedOnAuthoredAnalyticRegions":True}


def record(path, name=None):
    raw=read_regular(path,LIMITS["maximumFileBytes"]);return {"name":name or Path(path).name,"bytes":len(raw),"sha256":sha256(raw).hexdigest()}


def read_regular(path, limit):
    path=Path(path)
    for item in (path,*path.parents):require(not item.is_symlink() and not getattr(item,"is_junction",lambda:False)(),"Teaching evidence paths cannot contain links/junctions.")
    require(path.is_file() and 0<path.stat().st_size<=limit,"A bounded regular teaching evidence file is required.")
    raw=path.read_bytes();require(0<len(raw)<=limit,"Actual teaching evidence bytes exceed their bound.");return raw


def json_file(path):
    def pairs(rows):
        result={}
        for key,value in rows:require(key not in result,"Duplicate parameter/evidence keys are prohibited.");result[key]=value
        return result
    return json.loads(read_regular(path,LIMITS["maximumReportBytes"]),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(GeometryIssue("Finite JSON is required.")))


def outward_faces(t):
    a,b,c,d=t;return ((b,c,d),(a,d,c),(a,b,d),(a,c,b))


def determinant(t, points):
    a,b,c,d=(points[n] for n in t);u=tuple(b[i]-a[i] for i in range(3));v=tuple(c[i]-a[i] for i in range(3));w=tuple(d[i]-a[i] for i in range(3))
    return math.fsum((u[0]*(v[1]*w[2]-v[2]*w[1]),-u[1]*(v[0]*w[2]-v[2]*w[0]),u[2]*(v[0]*w[1]-v[1]*w[0])))


def component_data(component, points, tets, tagged_surfaces):
    """Assign each boundary triangle from its actual positive owner tetrahedron."""
    import verify_tetra_face_incidence as incidence
    require(0<len(tets)<=LIMITS["maximumCellsPerComponent"],"Actual authored component cell count exceeds its fixed bound.")
    used={n for t in tets for n in t};require(0<len(used)<=LIMITS["maximumNodesPerComponent"] and used<=set(points),"Actual component point labels must be bounded and complete.")
    require(all(len(t)==4 and len(set(t))==4 for t in tets),"Use only unique linear tetrahedron point labels.")
    volumes=[determinant(t,points)/6 for t in tets];require(all(math.isfinite(v) and v>0 for v in volumes),"Every actual generated tetrahedron must have finite positive volume.")
    faces=Counter(tuple(sorted(f)) for t in tets for f in outward_faces(t));owners={tuple(sorted(f)):f for t in tets for f in outward_faces(t) if faces[tuple(sorted(f))]==1}
    require(all(count in (1,2) for count in faces.values()),"Actual tetra face incidence must be manifold.")
    patches={name:[] for name in ROLES[component]};seen=set()
    for name,triangles in tagged_surfaces.items():
        require(name in patches,"Only authored named patch roles may be exported.")
        for triangle in triangles:
            key=tuple(sorted(triangle));require(key in owners and key not in seen,"Every CAD boundary triangle must bind exactly one component owner.")
            patches[name].append(owners[key]);seen.add(key)
    require(seen==set(owners) and all(patches.values()),"Every actual external face must retain one nonempty authored CAD patch role.")
    audit=incidence.audit_incidence(tets,[t for rows in patches.values() for t in rows]);require(audit["accepted"],"Full actual tetra face incidence/orientation must pass.")
    return {"points":{n:points[n] for n in sorted(used)},"tets":tets,"patches":patches,"metrics":{"cellCount":len(tets),"nodeCount":len(used),
            "boundaryTriangleCount":sum(map(len,patches.values())),"patchFaces":{k:len(v) for k,v in patches.items()},
            "patchTypes":{k:"patch" if k in {"inlet","mass-flow-inlet-11",INTERFACE[component]} else "wall" for k in patches},
            "minimumCellVolumeM3":min(volumes),"totalCellVolumeM3":math.fsum(volumes),"positiveTetrahedraVerified":True,
            "finiteCoordinatesVerified":True,"exactFaceIncidence":audit}}


def common_interface(left, right, cad_surfaces):
    a=left["patches"][INTERFACE["impeller"]];b=right["patches"][INTERFACE["volute"]]
    def cycles(triangles):return {tuple(sorted(t)):min((t,t[1:]+t[:1],t[2:]+t[:2])) for t in triangles}
    aa,bb=cycles(a),cycles(b);require(aa and aa.keys()==bb.keys(),"Interfaces must be the exact same actual CAD mesh triangles.")
    for key,t in aa.items():
        reverse=(t[0],t[2],t[1]);require(bb[key]==min((reverse,reverse[1:]+reverse[:1],reverse[2:]+reverse[:2])),"The interface faces must have opposite outward owner winding.")
    nodes=sorted({n for tr in a for n in tr});require(all(left["points"][n]==right["points"][n] for n in nodes),"Both exported interfaces must share every actual node coordinate exactly.")
    return {"sameCadSurfaceEntitiesVerified":True,"sharedCadSurfaceEntityIds":cad_surfaces,"sharedTriangleCount":len(a),"sharedNodeCount":len(nodes),
            "sharedNodeCoordinatesSha256":sha256(encoded([[n,*left["points"][n]] for n in nodes])).hexdigest(),
            "sharedUnorientedTrianglesSha256":sha256(encoded(sorted(aa))).hexdigest(),"oppositeOutwardWindingVerified":True,
            "matchedInitialInterfaceVerified":True,"dynamicCoverageVerified":False}


def msh_bytes(component, data):
    rows=["$MeshFormat","2.2 0 8","$EndMeshFormat","$PhysicalNames",str(len(ROLES[component])+1)]
    volume=1 if component=="impeller" else 2;zone="rotating-fluid" if component=="impeller" else "stationary-fluid"
    rows += ['3 '+str(volume)+' "'+zone+'"']+['2 '+str(tag)+' "'+name+'"' for name,tag in ROLES[component].items()]
    rows += ["$EndPhysicalNames","$Nodes",str(len(data["points"]))]+[str(n)+" "+" ".join(format(v,".17g") for v in xyz) for n,xyz in data["points"].items()]
    rows += ["$EndNodes","$Elements",str(len(data["tets"])+data["metrics"]["boundaryTriangleCount"])];eid=0
    for name,tag in ROLES[component].items():
        for tri in data["patches"][name]:eid+=1;rows.append(f"{eid} 2 2 {tag} {tag} "+" ".join(map(str,tri)))
    for tet in data["tets"]:eid+=1;rows.append(f"{eid} 4 2 {volume} {volume} "+" ".join(map(str,tet)))
    rows += ["$EndElements"];raw=("\n".join(rows)+"\n").encode("ascii");require(len(raw)<=LIMITS["maximumFileBytes"],"Actual MSH2 ASCII output exceeds128MiB.");return raw


def parse_msh(component, raw):
    """Independent published-byte admission, using the reviewed pure MSH reader."""
    import frozen_rotor_openfoam_case as parser
    actual=parser.msh_inventory(raw);require(actual["cellZone"]==("rotating-fluid" if component=="impeller" else "stationary-fluid") and set(actual["patchFaces"])==set(ROLES[component]),"The actual component must contain exactly its authored volume and patch roles.")
    sections={m[1]:m[2].strip().splitlines() for m in re.finditer(rb"(?m)^\$(\w+)\s*\n(.*?)^\$End\1\s*$",raw,re.S)}
    points={int(row.split()[0]):tuple(float(v) for v in row.split()[1:]) for row in sections[b"Nodes"][1:]};tets=[];surfaces={name:[] for name in ROLES[component]};reverse={v:k for k,v in ROLES[component].items()}
    for row in sections[b"Elements"][1:]:
        tokens=[int(v) for v in row.split()];ids=tuple(tokens[3+tokens[2]:])
        if tokens[1]==4:tets.append(ids)
        else:surfaces[reverse[tokens[3]]].append(ids)
    return component_data(component,points,tets,surfaces)


def mesh_data(gmsh, cad):
    import teaching_tetra_corner_refinement as corner
    tags,xyz,_=gmsh.model.mesh.getNodes();require(len(tags)<=LIMITS["maximumCombinedNodes"] and len(xyz)==3*len(tags),"Actual whole-model points must remain bounded.")
    points={int(n):tuple(float(v) for v in xyz[i*3:i*3+3]) for i,n in enumerate(tags)}
    require(len(points)==len(tags) and all(all(math.isfinite(v) for v in p) for p in points.values()),"Every native CAD mesh node must be finite and uniquely labelled.")
    result={}
    for name,volumes in cad["volumeGroups"].items():
        tets=[]
        for volume in volumes:
            kinds,element_tags,connectivity=gmsh.model.mesh.getElements(3,volume)
            require(all(int(kind)==4 for kind in kinds),"Author only linear tetrahedron fluid volumes.")
            for labels in connectivity:require(len(labels)%4==0,"Complete four-node cells are required.");tets.extend(tuple(int(v) for v in labels[i:i+4]) for i in range(0,len(labels),4))
        surfaces={}
        for role,entities in cad["surfaceRoles"][name].items():
            triangles=[]
            for entity in entities:
                kinds,element_tags,conn=gmsh.model.mesh.getElements(2,entity);require(all(int(kind)==2 for kind in kinds),"Use linear triangles on every authored CAD boundary.")
                for labels in conn:require(len(labels)%3==0,"Complete boundary triangles are required.");triangles.extend(tuple(int(v) for v in labels[i:i+3]) for i in range(0,len(labels),3))
            surfaces[role]=triangles
        result[name]=component_data(name,points,tets,surfaces)
    original_max=max(n for item in result.values() for n in item["points"])
    next_node=original_max+1
    for name,item in list(result.items()):
        refined,proof,next_node=corner.refine_component(item,next_node,original_max)
        result[name]=component_data(name,refined["points"],refined["tets"],refined["patches"])
        result[name]["cornerSubdivisionEvidence"]=proof
    require(sum(v["metrics"]["cellCount"] for v in result.values())<=LIMITS["maximumCombinedCells"],"Actual combined classroom cells exceed500000; no automatic mesh-size fallback.")
    require(sum(v["metrics"]["nodeCount"] for v in result.values())<=LIMITS["maximumCombinedNodes"],"Duplicated interface node storage must remain within the one-million combined node bound.")
    return result


def context_guard():
    expected={"GITHUB_ACTIONS":"true","GITHUB_EVENT_NAME":"workflow_dispatch","GITHUB_REPOSITORY":"simcalc/PyAnsys","RUNNER_OS":"Linux","RUNNER_ENVIRONMENT":"github-hosted"}
    require(sys.platform=="linux" and all(os.environ.get(k)==v for k,v in expected.items()),"Native CAD/mesh generation requires the standard manual public FREE Linux worker.")
    event=json_file(os.environ.get("GITHUB_EVENT_PATH",""));repo=event.get("repository",{})
    require(repo.get("full_name")=="simcalc/PyAnsys" and repo.get("private") is False and repo.get("visibility","public")=="public", "The authored geometry worker must use the exact public repository; no paid/custom fallback.")
    require(re.fullmatch(r"[0-9a-f]{40}",os.environ.get("GITHUB_SHA","")),"Bind the actual public generator source commit.")


def output_path(value):
    root=Path(os.environ.get("RUNNER_TEMP",""));path=Path(value).absolute()
    require(os.environ.get("RUNNER_TEMP") and root.is_dir() and ".." not in path.parts
            and path.is_relative_to(root.absolute()) and path!=root.absolute(),"Use a fresh isolated RUNNER_TEMP child directory.")
    for item in (path,*path.parents):require(not item.is_symlink() and not getattr(item,"is_junction",lambda:False)(),"Output paths cannot contain links/junctions.")
    return path


def generate(parameters, output_directory, mesh_level="coarse"):
    plan=teaching_plan(parameters,mesh_level);context_guard();destination=output_path(output_directory)
    require(not destination.exists(),"Authored native generation requires a fresh output directory.")
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(LIMITS["maximumMemoryBytes"],LIMITS["maximumMemoryBytes"]));resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    import gmsh
    require(gmsh.__version__==GMSH_VERSION,"Use the pinned official Gmsh4.15.2 Python distribution.")
    started=time.monotonic();destination.mkdir();gmsh.initialize();gmsh.option.setNumber("General.Terminal",1)
    try:
        for key,value in {"General.NumThreads":1,"Mesh.MaxNumThreads1D":1,"Mesh.MaxNumThreads2D":1,"Mesh.MaxNumThreads3D":1,
                          "Geometry.Tolerance":1e-9,"Mesh.Algorithm3D":10,"Mesh.Optimize":1,"Mesh.ElementOrder":1,
                          "Mesh.MeshSizeMin":plan["meshSizeM"]/2,"Mesh.MeshSizeMax":plan["meshSizeM"],
                          "Mesh.MeshSizeFromPoints":0,"Mesh.MeshSizeFromCurvature":40,"Mesh.MeshSizeExtendFromBoundary":0}.items():gmsh.option.setNumber(key,value)
        cad=build_cad(gmsh,plan["parameters"]);gmsh.write(str(destination/"teaching-pump.brep"));gmsh.model.mesh.generate(3);data=mesh_data(gmsh,cad)
        interface=common_interface(data["impeller"],data["volute"],cad["commonInterfaceSurfaceIds"]);components=[]
        for name,item in data.items():
            raw=msh_bytes(name,item);target=destination/(name+".msh")
            with target.open("xb") as stream:stream.write(raw)
            rechecked=parse_msh(name,raw);require(rechecked["metrics"]==item["metrics"],"Published17-digit component bytes must reproduce actual native mesh metrics exactly.")
            cad_volume=cad["cadFluidVolumesM3"][name];difference=abs(item["metrics"]["totalCellVolumeM3"]-cad_volume)/cad_volume
            require(math.isfinite(cad_volume) and cad_volume>0 and difference<=.005,"Actual tessellated fluid volume must agree with authored CAD within declared0.5%; quality is separate.")
            components.append({"component":name,"cellZone":"rotating-fluid" if name=="impeller" else "stationary-fluid",**item["metrics"],
                               "cornerSubdivisionEvidence":item["cornerSubdivisionEvidence"],
                               "generatedFile":{**record(target),"format":"gmsh-msh2.2-ascii"},"cadFluidVolumeM3":cad_volume,"cadMeshRelativeVolumeDifference":difference})
        source=Path(__file__).resolve();generator={"path":"tools/teaching_pump_geometry.py","bytes":source.stat().st_size,"sha256":sha256(source.read_bytes()).hexdigest()}
        post_source=source.with_name("teaching_tetra_corner_refinement.py")
        processor={"path":"tools/teaching_tetra_corner_refinement.py","bytes":post_source.stat().st_size,"sha256":sha256(post_source.read_bytes()).hexdigest()}
        manifest={"schemaVersion":MANIFEST_SCHEMA,"provenance":"CLASSROOM_AUTHORED_GEOMETRY","units":"SI","parameters":plan["parameters"],
                  "parametersSha256":plan["parametersSha256"],"geometryIdentity":plan["geometryIdentity"],"geometryIdentitySha256":plan["geometryIdentitySha256"],
                  "meshLevel":mesh_level,"meshSizeM":plan["meshSizeM"],"generatorSourceCommit":os.environ["GITHUB_SHA"],"generatorSourceFile":generator,"meshPostProcessorSourceFile":processor,
                  "cadEvidenceSha256":sha256(encoded(cad)).hexdigest(),"limits":LIMITS,"files":[record(destination/name) for name in FILES],"mainApplicationWrites":False}
        if "resolutionSizing" in plan:manifest["resolutionSizing"]=plan["resolutionSizing"]
        with (destination/MANIFEST).open("xb") as stream:stream.write(encoded(manifest))
        report={"schemaVersion":SCHEMA,"createdAt":datetime.now(timezone.utc).isoformat(),"status":"AUTHORED_CAD_VOLUME_MESH_NATIVE_CHECKS_REQUIRED",
                "provenance":"CLASSROOM_AUTHORED_GEOMETRY","units":"SI","generatorSourceCommit":manifest["generatorSourceCommit"],"generatorSourceFile":generator,"meshPostProcessorSourceFile":processor,
                "parametersSha256":plan["parametersSha256"],"geometryIdentitySha256":plan["geometryIdentitySha256"],"inputManifestSha256":sha256(encoded(manifest)).hexdigest(),
                "parameters":plan["parameters"],"meshLevel":mesh_level,"limits":LIMITS,"gmshVersion":gmsh.__version__,"nativeCADConstructed":True,"volumeMeshGenerated":True,
                "components":components,"commonInterfaceEvidence":interface,"cadEvidence":cad,"cadEvidenceSha256":manifest["cadEvidenceSha256"],"equationStages":plan["equationStages"],
                "elapsedSeconds":time.monotonic()-started,"references":REFERENCES,**{key:False for key in FALSE_FLAGS},
                "claimBoundary":"New authored SI teaching pump CAD and actual geometric mesh evidence only. Native OpenFOAM quality/coupling, flow, NPSH3, physical transient and numerical validation remain separate."}
        if "resolutionSizing" in plan:report["resolutionSizing"]=plan["resolutionSizing"]
        raw=encoded(report);require(len(raw)<=LIMITS["maximumReportBytes"] and time.monotonic()-started<LIMITS["maximumSeconds"],"Bounded native generation report/time limit reached.")
        with (destination/REPORT).open("xb") as stream:stream.write(raw)
        verify_output(destination);require(time.monotonic()-started<LIMITS["maximumSeconds"],"The native teaching deadline was reached during final source/mesh verification.");return report
    finally:gmsh.finalize()


def verify_output(directory):
    import teaching_tetra_corner_refinement as corner
    root=Path(directory).absolute();require(root.is_dir() and {p.name for p in root.iterdir()}=={*FILES,REPORT,MANIFEST},"Retain only the exact authored CAD/mesh manifest inventory.")
    report=json_file(root/REPORT);manifest=json_file(root/MANIFEST);plan=teaching_plan(manifest.get("parameters"),manifest.get("meshLevel"))
    sizing=plan.get("resolutionSizing")
    require((manifest.get("resolutionSizing")==report.get("resolutionSizing")==sizing if sizing is not None
        else "resolutionSizing" not in manifest and "resolutionSizing" not in report),
        "Replay the exact lean sizing declaration, separate from actual cell counts and unchanged native acceptance.")
    require(report.get("schemaVersion")==SCHEMA and manifest.get("schemaVersion")==MANIFEST_SCHEMA and report.get("provenance")==manifest.get("provenance")=="CLASSROOM_AUTHORED_GEOMETRY"
            and report.get("units")==manifest.get("units")=="SI" and report.get("status")=="AUTHORED_CAD_VOLUME_MESH_NATIVE_CHECKS_REQUIRED"
            and report.get("nativeCADConstructed") is True and report.get("volumeMeshGenerated") is True
            and all(report.get(k) is False for k in FALSE_FLAGS) and manifest.get("mainApplicationWrites") is False,
            "Only the new authored-SI geometry schema and honest geometric execution facts may be admitted.")
    require(report.get("limits")==manifest.get("limits")==LIMITS and report.get("parameters")==manifest.get("parameters")==plan["parameters"]
            and report.get("parametersSha256")==manifest.get("parametersSha256")==plan["parametersSha256"]
            and report.get("geometryIdentitySha256")==manifest.get("geometryIdentitySha256")==plan["geometryIdentitySha256"]
            and manifest.get("geometryIdentity")==plan["geometryIdentity"] and manifest.get("meshSizeM")==plan["meshSizeM"]
            and report.get("meshLevel")==plan["meshLevel"] and report.get("equationStages")==plan["equationStages"]
            and report.get("inputManifestSha256")==sha256(read_regular(root/MANIFEST,LIMITS["maximumReportBytes"])).hexdigest(),"Actual authored parameters/recipe/manifest must agree exactly.")
    require(report.get("gmshVersion")==GMSH_VERSION and 0<=number(report.get("elapsedSeconds"))<LIMITS["maximumSeconds"]
            and all(type(report["limits"].get(k)) is int and type(manifest["limits"].get(k)) is int for k in LIMITS)
            and report.get("cadEvidenceSha256")==manifest.get("cadEvidenceSha256")==sha256(encoded(report.get("cadEvidence"))).hexdigest(),
            "The native CAD evidence, actual Gmsh version, elapsed time and resource caps must remain bound.")
    generator={"path":"tools/teaching_pump_geometry.py","bytes":Path(__file__).stat().st_size,"sha256":sha256(Path(__file__).read_bytes()).hexdigest()}
    require(report.get("generatorSourceFile")==manifest.get("generatorSourceFile")==generator and report.get("generatorSourceCommit")==manifest.get("generatorSourceCommit")
            and isinstance(report.get("generatorSourceCommit"),str) and re.fullmatch(r"[0-9a-f]{40}",report["generatorSourceCommit"]),"The actual generator source bytes and public commit must remain bound.")
    post_source=Path(__file__).with_name("teaching_tetra_corner_refinement.py")
    processor={"path":"tools/teaching_tetra_corner_refinement.py","bytes":post_source.stat().st_size,"sha256":sha256(post_source.read_bytes()).hexdigest()}
    require(report.get("meshPostProcessorSourceFile")==manifest.get("meshPostProcessorSourceFile")==processor,
            "The actual conservative corner subdivision source bytes must remain bound.")
    expected=[record(root/name) for name in FILES];require(manifest.get("files")==expected,"Every actual BREP/component mesh must match its manifest bytes and SHA256.")
    require(sum(p.stat().st_size for p in root.iterdir())<=LIMITS["maximumWorkspaceBytes"],"Actual authored workspace exceeds512MiB.")
    rows=report.get("components",[]);require(isinstance(rows,list) and [r.get("component") for r in rows]==["impeller","volute"],"Both ordered authored fluid components are required.")
    data={};original_nodes=set();threshold=None;next_node=None
    for row in rows:
        name=row["component"];data[name]=parse_msh(name,read_regular(root/(name+".msh"),LIMITS["maximumFileBytes"]))
        require(all(row.get(k)==v for k,v in data[name]["metrics"].items()) and row.get("generatedFile")=={**record(root/(name+".msh")),"format":"gmsh-msh2.2-ascii"}
                and row.get("cellZone")==("rotating-fluid" if name=="impeller" else "stationary-fluid"),"Actual published component topology/metrics changed after native generation.")
        volume=number(row.get("cadFluidVolumeM3"));require(volume>0 and row.get("cadMeshRelativeVolumeDifference")==abs(volume-row["totalCellVolumeM3"])/volume<=.005,"The actual CAD/mesh volume check must remain bound.")
        evidence=row.get("cornerSubdivisionEvidence")
        require(isinstance(evidence,dict),"Declared boundary corner subdivision evidence is required.")
        if threshold is None:
            threshold=evidence.get("originalGlobalMaximumNodeLabel");next_node=threshold+1 if type(threshold) is int else None
        require(evidence.get("originalGlobalMaximumNodeLabel")==threshold and evidence.get("firstInsertedNodeLabel")==next_node,
                "Both component centroid label intervals must follow the same actual original fluid node domain.")
        original,next_node=corner.replay_component(data[name],evidence);original_nodes.update(original["points"])
    require(original_nodes and max(original_nodes)==threshold,"The retained original global node threshold must reproduce from both actual fluid components.")
    require(sum(row["cellCount"] for row in rows)<=LIMITS["maximumCombinedCells"] and sum(row["nodeCount"] for row in rows)<=LIMITS["maximumCombinedNodes"],"Actual combined cells/nodes exceed the fixed authored mesh bound.")
    surface_ids=report.get("cadEvidence",{}).get("commonInterfaceSurfaceIds")
    require(isinstance(surface_ids,list) and surface_ids and all(type(v) is int and v>0 for v in surface_ids),"Shared native CAD surface entity identities are required.")
    require(report.get("commonInterfaceEvidence")==common_interface(data["impeller"],data["volute"],surface_ids),"The same actual interface geometry and opposite ownership must reproduce from published bytes.")
    cad=report["cadEvidence"]
    require(set(cad)=={"volumeGroups","surfaceRoles","commonInterfaceSurfaceIds","cadFluidVolumesM3","cadSurfaceClassificationToleranceM","cadSurfaceRolesBasedOnAuthoredAnalyticRegions"}
            and cad.get("cadSurfaceClassificationToleranceM")==3e-7 and cad.get("cadSurfaceRolesBasedOnAuthoredAnalyticRegions") is True
            and set(cad.get("volumeGroups",{}))==set(cad.get("surfaceRoles",{}))==set(cad.get("cadFluidVolumesM3",{}))==set(ROLES),"The retained CAD ownership/role/volume evidence must match the exact authored recipe.")
    for component in ROLES:
        groups=cad["volumeGroups"][component];roles=cad["surfaceRoles"][component]
        require(isinstance(groups,list) and 0<len(groups)<=16 and len(set(groups))==len(groups) and all(type(v) is int and v>0 for v in groups)
                and isinstance(roles,dict) and set(roles)==set(ROLES[component]) and all(isinstance(tags,list) and tags and len(set(tags))==len(tags)
                    and all(type(v) is int and v>0 for v in tags) for tags in roles.values())
                and roles[INTERFACE[component]]==surface_ids and cad["cadFluidVolumesM3"][component]==next(r["cadFluidVolumeM3"] for r in rows if r["component"]==component),
                "Current CAD surface aliases, volumes and component ownership must remain explicit and bound.")
    require(not set(cad["volumeGroups"]["impeller"]) & set(cad["volumeGroups"]["volute"]),"Rotating/stationary CAD volume identities must remain disjoint.")
    created=datetime.fromisoformat(report.get("createdAt",""));require(created.tzinfo is not None and created.utcoffset().total_seconds()==0,"The actual native generation time must be an explicit UTC timestamp.")
    return {"report":report,"reportFile":{"path":REPORT,**{k:v for k,v in record(root/REPORT).items() if k!="name"}},"components":rows}


def supervise(parameters, destination, mesh_level):
    """External native deadline; Python signals alone cannot stop a C mesher."""
    context_guard();target=output_path(destination);require(not target.exists(),"Use a fresh native teaching output target.")
    log=target.with_name(target.name+"-native.log");require(not log.exists(),"Use a fresh bounded native teaching log.")
    command=[sys.executable,"-B",str(Path(__file__).resolve()),"--worker","--output-directory",str(target),"--mesh-level",mesh_level,"--parameter-values",encoded(parameters).decode("ascii")]
    allowed=("PATH","LD_LIBRARY_PATH","LANG","LC_ALL","TMPDIR","RUNNER_TEMP","GITHUB_ACTIONS","GITHUB_EVENT_NAME","GITHUB_REPOSITORY","RUNNER_OS","RUNNER_ENVIRONMENT","GITHUB_EVENT_PATH","GITHUB_SHA")
    env={k:os.environ[k] for k in allowed if k in os.environ};env.update(OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1",NUMEXPR_NUM_THREADS="1")
    started=time.monotonic();process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env,start_new_session=True);selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
    written=0
    try:
        with log.open("xb") as stream:
            while selector.get_map():
                require(time.monotonic()-started<LIMITS["maximumSeconds"],"The external native teaching geometry deadline was reached.")
                for key,mask in selector.select(.5):
                    block=os.read(key.fileobj.fileno(),8192)
                    if not block:selector.unregister(key.fileobj);continue
                    require(written+len(block)<=LIMITS["maximumLogBytes"],"The native teaching log limit was reached.");stream.write(block);written+=len(block)
                if target.exists():require(sum(p.stat().st_size for p in target.iterdir() if p.is_file())<=LIMITS["maximumWorkspaceBytes"],"The native teaching workspace cap was reached.")
            code=process.wait(timeout=2);require(time.monotonic()-started<LIMITS["maximumSeconds"] and code==0,"The bounded native teaching worker did not complete successfully; inspect its retained log.")
        result=verify_output(target)["report"];require(time.monotonic()-started<LIMITS["maximumSeconds"],"The total native teaching deadline was reached during final readback.");return result
    finally:
        selector.close()
        if process.poll() is None:
            require(os.getpgid(process.pid)==process.pid,"Only this attempt-owned native process group may be stopped.")
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=2)
        process.stdout.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output-directory");parser.add_argument("--mesh-level",choices=tuple(MESH_LEVELS),default="coarse")
    parser.add_argument("--parameters-json");parser.add_argument("--plan-only",action="store_true");parser.add_argument("--worker",action="store_true",help=argparse.SUPPRESS);parser.add_argument("--parameter-values",help=argparse.SUPPRESS)
    args=parser.parse_args()
    try:
        require(not(args.parameters_json and args.parameter_values),"Use one explicit parameter input.")
        values=json_file(args.parameters_json) if args.parameters_json else json.loads(args.parameter_values) if args.parameter_values else None
        plan=teaching_plan(values,args.mesh_level)
        if args.plan_only:print(json.dumps(plan,allow_nan=False));return 0
        require(args.output_directory,"Declare the new isolated output directory before native generation.")
        report=generate(plan["parameters"],args.output_directory,args.mesh_level) if args.worker else supervise(plan["parameters"],args.output_directory,args.mesh_level)
        print(json.dumps({k:report[k] for k in ("status","provenance","units","volumeMeshGenerated","cfdReady","actualCfdRunVerified","mainApplicationWrites")},allow_nan=False));return 0
    except (ValueError,OSError,KeyError,TypeError,ImportError,RuntimeError,subprocess.SubprocessError) as error:
        print(json.dumps({"status":"AUTHORED_GEOMETRY_CORRECTION_REQUIRED","action":str(error)[:500],"cfdReady":False,"actualCfdRunVerified":False,"mainApplicationWrites":False}));return 2


if __name__=="__main__":raise SystemExit(main())
