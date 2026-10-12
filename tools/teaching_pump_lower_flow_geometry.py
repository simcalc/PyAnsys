"""Read-only proof for one authored extended .30 m classroom outlet cap.

Every current native outlet facet is checked against the exact authored SI
rectangle. This verifies input geometry, never a measured/developed flow profile,
CFD execution, pump head or convergence. No file or process is created.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import teaching_pump_native_case as source_io
import teaching_pump_geometry as author
import teaching_pump_lower_flow_recipe as recipe

SCHEMA = "untirta.teaching-pump-lower-flow-geometry-proof.v1"
PATCH = "mass-flow-inlet-11"
MESH_FILES = ("points", "faces", "owner", "neighbour", "boundary", "cellZones")
MASS, RHO, HEIGHT, WIDTH_START, WIDTH_END = 45., 998.2, .04363, .01, .12
PLANE_Y, INTERFACE_RADIUS = .30, .1405
AREA = HEIGHT * (WIDTH_END-WIDTH_START)
Q, UN = MASS/RHO, (MASS/RHO)/AREA


def stage(inputs, equation, substitution, value, unit):
    require_values=value if isinstance(value,list) else [value]
    if not all(type(v) in (int,float) and math.isfinite(v) for v in require_values):
        raise ValueError("Outlet engineering equations require finite typed Python results.")
    return {"input":inputs,"equation":equation,"substitution":substitution,
        "result":{"value":value,"unit":unit},"calculationAuthority":"Python"}


STAGES = {
    "flowRate":stage({"massFlow":{"value":MASS,"unit":"kg/s"},"density":{"value":RHO,"unit":"kg/m3"}},
        "Q=m_dot/rho","Q=45/998.2",Q,"m3/s"),
    "authoredArea":stage({"height":{"value":HEIGHT,"unit":"m"},"widthStart":{"value":WIDTH_START,"unit":"m"},
        "widthEnd":{"value":WIDTH_END,"unit":"m"}},"A=h*(w_end-w_start)","A=0.04363*(0.12-0.01)",AREA,"m2"),
    "outletVelocity":stage({"Q":{"value":Q,"unit":"m3/s"},"authoredArea":{"value":AREA,"unit":"m2"}},
        "U_n=Q/A","U_n="+format(Q,".17g")+"/"+format(AREA,".17g"),UN,"m/s"),
    "velocityVector":stage({"normalVelocity":{"value":UN,"unit":"m/s"},"outwardUnitNormal":{"value":[0,1,0],"unit":"1"}},
        "U=U_n*n_out","U="+format(UN,".17g")+"*(0,1,0)",[0,UN,0],"m/s")}

PROFILE = {"id":"teaching-45kg-s-extended-030-outlet-cap-geometry-v1", "patch":PATCH,
    "massFlowKgS":MASS, "densityKgM3":RHO, "volumetricFlowM3S":Q,
    "rectangleHeightM":HEIGHT, "rectangleWidthM":WIDTH_END-WIDTH_START,
    "rectangleAreaM2":AREA, "planeYM":PLANE_Y, "outwardUnitNormal":[0,1,0],
    "uniformNormalVelocityMS":UN, "uniformVelocityMS":[0,UN,0],
    "equation":"Q=m_dot/rho; A=h*(w_end-w_start); U_n=Q/A; U=U_n*n_out",
    "substitution":{"massFlowKgS":MASS,"densityKgM3":RHO,"heightM":HEIGHT,
        "widthEndM":WIDTH_END,"widthStartM":WIDTH_START},
    "nativeVelocityPatchType":"fixedValue", "nativePressurePatchType":"zeroGradient",
    "assumption":"Predeclared uniform normal plug outlet for this classroom candidate; no extrapolated normal/tangential outlet profile.",
    "profileMeasured":False, "developedProfileVerified":False,
    "convergencePredicted":False, "physicalAccuracyQualified":False,
    "priorRejectedRunId":"38193184426", "nativeFieldClippingPerformed":False,
    "sourceGeometryChanged":True, "mainApplicationWrites":False}
PROFILE["engineeringStages"]=deepcopy(STAGES)
PLANE_TOLERANCE_M, NORMAL_TOLERANCE, AREA_RELATIVE_TOLERANCE = 1e-12, 1e-12, 1e-12
FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


class OutletProfileIssue(ValueError): pass


def require(value, action):
    if not value: raise OutletProfileIssue(action)


def uniform_outlet_body():
    return ("type fixedValue; value uniform (0 "+format(UN,".17g")+" 0);").encode("ascii")


def prefix_evidence(proof, prefix):
    """Relocate descriptor paths only; measured/source identities remain exact."""
    require(isinstance(prefix,str) and re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*",prefix),
        "Prefix copied evidence with a plain relative data directory.")
    result=deepcopy(proof)
    result["geometryReportFile"]["path"]=prefix+"/"+proof["geometryReportFile"]["path"]
    result["nativeMeshFileRecords"]=[{**row,"path":prefix+"/"+row["path"]} for row in proof["nativeMeshFileRecords"]]
    return result


def counted(root,relative,name):
    path=Path(root)/relative;source_io.record(root,relative)
    return source_io.base.native_list(path)


def integer_labels(payload,count,maximum):
    words=payload.split()
    require(len(words)==count and all(re.fullmatch(r"\d+",w) and int(w)<maximum for w in words),
        "Retain every exact bounded native owner/neighbour label.")
    return list(map(int,words))


def verify_outlet(directory, *, geometry_report_relative="source-geometry/teaching-geometry.json",
    mesh_relative="component-mesh-snapshots/volute/constant/polyMesh"):
    root=source_io.base.regular_directory(directory)
    report,report_row=source_io.json_file(root,geometry_report_relative)
    parameters=report.get("parameters")
    require(report.get("schemaVersion")==author.SCHEMA and report.get("provenance")=="CLASSROOM_AUTHORED_GEOMETRY"
        and report.get("units")=="SI" and isinstance(parameters,dict) and set(parameters)==set(author.DEFAULTS)
        and all(type(parameters[k]) is type(v) and parameters[k]==v for k,v in recipe.EXPECTED_GEOMETRY_PARAMETERS.items()),
        "This candidate requires exactly the .30 m authored outlet and all other unchanged SI inputs.")
    parameter_sha=sha256(author.encoded(parameters)).hexdigest()
    plan=author.teaching_plan(parameters,report.get("meshLevel"))
    require(report.get("parametersSha256")==parameter_sha
        and report.get("geometryIdentitySha256")==plan["geometryIdentitySha256"]
        and all(isinstance(report.get(k),str) and re.fullmatch(r"[0-9a-f]{64}",report[k]) for k in ("geometryIdentitySha256","inputManifestSha256")),
        "Bind the actual full CAD parameter identity and current geometry/input manifest digests.")
    rows=[source_io.record(root,mesh_relative+"/"+name)[1] for name in MESH_FILES]
    counts={};payloads={}
    for name in MESH_FILES:counts[name],payloads[name]=counted(root,mesh_relative+"/"+name,name)
    require(0<counts["points"]<=500000 and 0<counts["faces"]==counts["owner"]<=2000000
        and 0<counts["neighbour"]<counts["faces"],"Bound the complete uncoupled source component topology.")
    points=[];last=0;payload=payloads["points"]
    for match in re.finditer(r"\(([^()]*)\)",payload):
        words=match[1].split()
        require(not payload[last:match.start()].strip() and len(words)==3
            and all(re.fullmatch(FLOAT,w) and math.isfinite(float(w)) for w in words),"Every actual native point needs three finite SI coordinates.")
        points.append(tuple(map(float,words)));last=match.end()
    require(len(points)==counts["points"] and not payload[last:].strip(),"Retain the complete native node domain.")
    patches=source_io.native_geometry.boundary_entries(root/mesh_relative/"boundary")
    require(set(patches) in (source_io.base.PATCHES["volute"],source_io.base.PATCHES["volute"]|{"defaultFaces"})
        and all(patches[p]["nFaces"]>0 for p in source_io.base.PATCHES["volute"])
        and ("defaultFaces" not in patches or patches["defaultFaces"]["nFaces"]==0 and patches["defaultFaces"]["type"]=="patch")
        and patches[PATCH]["type"]=="patch", "Require all four unique physical volute patch roles and only an optional zero-face placeholder.")
    offset=counts["neighbour"]
    for patch in patches.values():
        require(patch["startFace"]==offset and patch["nFaces"]>=0,"Native patch ranges must cover the complete ordered exterior without aliases.")
        offset+=patch["nFaces"]
    require(offset==counts["faces"],"Every source exterior face must have one current physical patch role.")
    owners=integer_labels(payloads["owner"],counts["owner"],450000)
    neighbours=integer_labels(payloads["neighbour"],counts["neighbour"],450000)
    cells=max(owners+neighbours)+1
    require(set(owners)|set(neighbours)==set(range(cells)) and all(a<b for a,b in zip(owners,neighbours)),
        "Preserve every source cell and ordered internal adjacency.")
    # The source geometry admission checks cells against the actual CAD MSH.
    # This separate profile gate additionally binds the native outlet owners.
    zone=re.fullmatch(r"\s*stationary-fluid\s*\{\s*type\s+cellZone\s*;\s*cellLabels\s+List<label>\s+(\d+)\s*\(([^()]*)\)\s*;?\s*\}\s*",payloads["cellZones"],re.S)
    require(counts["cellZones"]==1 and zone and int(zone[1])==cells
        and integer_labels(zone[2],cells,cells)==list(range(cells)),"The actual uncoupled volute must retain exactly its stationary-fluid cell zone/order.")
    patch=patches[PATCH];first,end=patch["startFace"],patch["startFace"]+patch["nFaces"]
    triangles=[];seen=set();payload=payloads["faces"];last=0;face_count=0
    for match in re.finditer(r"(\d+)\s*\(([^()]*)\)",payload):
        words=match[2].split()
        require(not payload[last:match.start()].strip() and int(match[1])==len(words)==3
            and all(re.fullmatch(r"\d+",w) and int(w)<len(points) for w in words),"The authored source retains complete triangular faces and native point labels.")
        ids=tuple(map(int,words));require(len(set(ids))==3,"Native triangular faces cannot repeat a node.")
        if first<=face_count<end:
            require(tuple(sorted(ids)) not in seen,"Each physical outlet triangle must be unique.")
            seen.add(tuple(sorted(ids)));triangles.append(ids)
        face_count+=1;last=match.end()
    require(face_count==counts["faces"] and not payload[last:].strip() and len(triangles)==patch["nFaces"],"Check every actual face in the whole physical outlet patch.")
    xmin,xmax=INTERFACE_RADIUS+WIDTH_START,INTERFACE_RADIUS+WIDTH_END
    areas=[];maximum_plane_error=maximum_normal_error=0.;edges=Counter();directed=Counter();used=set()
    for ids in triangles:
        a,b,c=(points[i] for i in ids)
        require(all(xmin-PLANE_TOLERANCE_M<=p[0]<=xmax+PLANE_TOLERANCE_M and -PLANE_TOLERANCE_M<=p[2]<=HEIGHT+PLANE_TOLERANCE_M for p in (a,b,c)),"Every outlet node must lie within the actual declared rectangular CAD cap.")
        plane_error=max(abs(p[1]-PLANE_Y) for p in (a,b,c));maximum_plane_error=max(maximum_plane_error,plane_error)
        ab,ac=tuple(b[i]-a[i] for i in range(3)),tuple(c[i]-a[i] for i in range(3))
        sf=((ab[1]*ac[2]-ab[2]*ac[1])/2,(ab[2]*ac[0]-ab[0]*ac[2])/2,(ab[0]*ac[1]-ab[1]*ac[0])/2)
        area=math.hypot(*sf);require(math.isfinite(area) and area>0,"Every actual outlet facet must have positive finite area.")
        normal_error=math.dist(tuple(x/area for x in sf),(0,1,0));maximum_normal_error=max(maximum_normal_error,normal_error)
        require(plane_error<=PLANE_TOLERANCE_M and normal_error<=NORMAL_TOLERANCE,"The actual entire outlet must lie at y=.30 with outward +Y winding, not a guessed normal.")
        areas.append(area);used.update(ids)
        for aa,bb in zip(ids,ids[1:]+ids[:1]):edges[tuple(sorted((aa,bb)))]+=1;directed[aa,bb]+=1
    boundary=[edge for edge,count in edges.items() if count==1];degree=Counter(i for e in boundary for i in e)
    require(boundary and all(n in (1,2) for n in edges.values()) and all(count==2 for count in degree.values())
        and all(directed[a,b]==directed[b,a]==1 for (a,b),n in edges.items() if n==2),"The full planar outlet must be an oriented triangulated disk without duplicate/nonmanifold edges.")
    perimeter=0.
    for a,b in boundary:
        pa,pb=points[a],points[b]
        require(any(abs(pa[d]-v)<=PLANE_TOLERANCE_M and abs(pb[d]-v)<=PLANE_TOLERANCE_M for d,v in ((0,xmin),(0,xmax),(2,0),(2,HEIGHT))),"Only the four authored CAD rectangle edges may bound the actual outlet.")
        perimeter+=math.dist(pa,pb)
    area=math.fsum(areas);relative_error=abs(area-AREA)/AREA
    require(relative_error<=AREA_RELATIVE_TOLERANCE and math.isclose(perimeter,2*(WIDTH_END-WIDTH_START+HEIGHT),rel_tol=1e-12,abs_tol=1e-15),"Complete actual outlet area/perimeter must equal the current authored rectangular cap.")
    for x,z in ((xmin,0),(xmin,HEIGHT),(xmax,0),(xmax,HEIGHT)):
        require(any(abs(points[i][0]-x)<=PLANE_TOLERANCE_M and abs(points[i][2]-z)<=PLANE_TOLERANCE_M for i in used),"Retain all four actual CAD cap corners.")
    require(source_io.record(root,geometry_report_relative)[1]==report_row
        and [source_io.record(root,r["path"])[1] for r in rows]==rows,"Source CAD/native bytes changed during the read-only outlet proof.")
    engineering=deepcopy(STAGES)
    engineering["measuredArea"]=stage({"actualOutletTriangleCount":{"value":len(triangles),"unit":"1"},
        "nativeMeshRoleInventorySha256":sha256(source_io.encoded([{**r,"path":"constant/polyMesh/"+Path(r["path"]).name} for r in rows])).hexdigest()},
        "A_native=sum_f |0.5*((x1-x0) cross (x2-x0))|",
        "Sum of "+str(len(triangles))+" current native triangular areas = "+format(area,".17g"),area,"m2")
    return {"schemaVersion":SCHEMA,"profile":deepcopy(PROFILE),"geometryReportFile":report_row,
        "sourceGeometryIdentitySha256":report["geometryIdentitySha256"],"sourceGeometryInputManifestSha256":report["inputManifestSha256"],
        "sourceCadParametersSha256":parameter_sha,"nativeMeshFileRecords":rows,"patch":PATCH,
        "actualOutletFaceRange":[first,end],"actualOutletTriangleCount":len(triangles),"actualOutletNodeCount":len(used),
        "actualOutletOwnerCellCount":len(set(owners[first:end])),"actualOutletAreaM2":area,
        "actualOutletPerimeterM":perimeter,"rectangleAreaRelativeError":relative_error,
        "maximumPlaneErrorM":maximum_plane_error,"maximumOutwardNormalError":maximum_normal_error,
        "allPhysicalOutletFacesChecked":True,"allOutletOwnersStationaryVerified":True,"orientedRectangleEdgeIncidenceVerified":True,
        "planeToleranceM":PLANE_TOLERANCE_M,"normalTolerance":NORMAL_TOLERANCE,"areaRelativeTolerance":AREA_RELATIVE_TOLERANCE,
        "geometryUnchanged":True,"sourceBytesImmutable":True,"profileMeasured":False,
        "engineeringStages":engineering,
        "flowExecuted":False,"convergenceVerified":False,"mainApplicationWrites":False}
