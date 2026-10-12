"""Prepare fresh source-bound 2000-iteration/1 MPa research cases.

Only current geometry admission is reused. This module neither consumes a
previous physics acceptance nor starts a native program. Its recipe belongs to
a distinct boundary family; outlet reference pressure is not suction pressure.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import teaching_pump_lower_flow_source as source_io
import teaching_pump_lower_flow_recipe as recipe

base, safety, geometry, fields = source_io.base, source_io.safety, source_io.geometry, source_io.fields
encoded, record, json_file = source_io.encoded, source_io.record, source_io.json_file
SourceProofTransaction = source_io.SourceProofTransaction
validate_source, validate_runtime, source_inventory = source_io.validate_source, source_io.validate_runtime, source_io.source_inventory
MESH_FILES, PARAMETERS, LIMITS = source_io.MESH_FILES, deepcopy(recipe.PARAMETERS), deepcopy(recipe.LIMITS)
MAX_REPORT, MAX_FILE = source_io.MAX_REPORT, source_io.MAX_FILE
MAX_SOURCE_RECORDS, MAX_SOURCE_BYTES, MAX_WORKSPACE = source_io.MAX_SOURCE_RECORDS, source_io.MAX_SOURCE_BYTES, source_io.MAX_WORKSPACE
ASSUMPTIONS, SAMPLING, CLAIM = deepcopy(recipe.ASSUMPTIONS), deepcopy(recipe.SAMPLING), recipe.CLAIM
case_files = recipe.case_files
PROFILE = deepcopy(recipe.PROFILE)
SCHEMA = "untirta.teaching-pump-lower-flow-preparation.v1"
REPORT = "teaching-lower-flow-preparation.json"
ROUTE, BC_FAMILY = "CLASSROOM_AUTHORED_GEOMETRY", recipe.BC_FAMILY
STATUS = "CLASSROOM_LOWER_FLOW_CASE_NATIVE_EXECUTION_REQUIRED"
FALSE_FLAGS = source_io.FALSE_FLAGS
FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
NCC_PATCHES = {"pumpInterface_on_interface-impeller-outlet", "pumpInterface_on_interface-volute-inlet",
    "nonConformalError_on_interface-impeller-outlet", "nonConformalError_on_interface-volute-inlet"}
STARTUP_FIELDS = {"totalPressurePa":("totalPressurePa","pressure","p"),
    "staticPressurePa":("gaugeStaticPressurePa","pressure","p"),
    "U2":("absoluteVelocitySquared","magSqr","U"),"yPlus":("wallYPlus","yPlus",None)}
NATIVE_SOURCE = "https://github.com/OpenFOAM/OpenFOAM-12/blob/86e126a7bc4dbfaed949bbc58c9f0fdd67acc059/"


class HighPressureCaseIssue(ValueError): pass


def require(condition, action):
    if not condition: raise HighPressureCaseIssue(action)


def _prefix(proof, prefix):
    result = deepcopy(proof)
    result["geometryReportFile"]["path"] = prefix + "/" + proof["geometryReportFile"]["path"]
    result["nativeMeshFileRecords"] = [{**r, "path": prefix + "/" + r["path"]} for r in proof["nativeMeshFileRecords"]]
    return result


def outlet_geometry(proof):
    """Keep the measured rectangle proof without the old velocity candidate."""
    require(isinstance(proof, dict), "Require the independently replayed source outlet geometry proof.")
    # These are geometry facts only. Deliberately omit the old profile and its
    # Q/A/vector equations: this lane does not prescribe outlet velocity.
    result = {k: deepcopy(v) for k, v in proof.items() if k not in {"schemaVersion", "profile", "engineeringStages"}}
    result.update(schemaVersion="untirta.teaching-pump-lower-flow-static-geometry-proof.v1",
        outletPlaneYM=.30, headMeasurement=recipe.PROFILE["headMeasurement"],
        boundaryFamilyId=BC_FAMILY, velocityProfilePrescribed=False,
        staticPressureBCM2S2=0., staticAbsoluteReferencePa=recipe.OUTLET_STATIC_REFERENCE_PA)
    return result


def verify_inlet_geometry(directory):
    """Check every native inlet triangle, not an analytic-area velocity guess.

    Annular CAD is tessellated: the measured polygon area may differ from pi RÂ².
    The analytic area is diagnostic only; native velocity uses its own actual
    patch magSf. It is never an executed flow result or a new quality gate.
    """
    root = base.regular_directory(directory)
    report, report_row = json_file(root, "source-geometry/teaching-geometry.json")
    p = report.get("parameters")
    require(isinstance(p, dict) and all(type(p.get(k)) is float and p[k] == value for k, value in
        {"hubRadiusM": .01968, "eyeRadiusM": .0844, "inletLengthM": .06}.items())
        and report.get("units") == "SI" and report.get("provenance") == ROUTE,
        "Use the exact authored SI inlet annulus, not guessed dimensions.")
    relative = "component-mesh-snapshots/impeller/constant/polyMesh"
    rows = [record(root, relative + "/" + n)[1] for n in MESH_FILES]
    payloads = {n: base.native_list(root / relative / n) for n in MESH_FILES}
    np, point_body = payloads["points"]; nf, face_body = payloads["faces"]
    no, owner_body = payloads["owner"]; nn, neighbour_body = payloads["neighbour"]
    require(0 < np <= 500000 and 0 < nf == no <= 2000000 and 0 <= nn < nf,
        "Bound the complete native source topology before interpreting inlet geometry.")
    points = []; last = 0
    for m in re.finditer(r"\(([^()]*)\)", point_body):
        words = m[1].split()
        require(not point_body[last:m.start()].strip() and len(words) == 3 and all(re.fullmatch(FLOAT, w)
            and math.isfinite(float(w)) for w in words), "Parse all current native SI points completely.")
        points.append(tuple(map(float, words))); last = m.end()
    require(len(points) == np and not point_body[last:].strip(), "The full native point count must match its header.")
    def labels(body, count, maximum):
        words = body.split()
        require(len(words) == count and all(re.fullmatch(r"\d+", w) and int(w) < maximum for w in words),
            "Require complete bounded native adjacency labels.")
        return list(map(int, words))
    owners = labels(owner_body, no, 500000); neighbours = labels(neighbour_body, nn, 500000)
    cells = max(owners + neighbours) + 1
    require(set(owners) | set(neighbours) == set(range(cells)) and all(a < b for a, b in zip(owners, neighbours)),
        "Preserve source cell coverage and ordered internal adjacency.")
    nz, zone_body = payloads["cellZones"]
    zone = re.fullmatch(r"\s*rotating-fluid\s*\{\s*type\s+cellZone\s*;\s*cellLabels\s+List<label>\s+(\d+)\s*\(([^()]*)\)\s*;?\s*\}\s*", zone_body, re.S)
    require(nz == 1 and zone and int(zone[1]) == cells and labels(zone[2], cells, cells) == list(range(cells)),
        "Bind the actual inlet owners to the complete original rotating-fluid component.")
    patches = geometry.boundary_entries(root / relative / "boundary")
    require(set(patches) in (base.PATCHES["impeller"], base.PATCHES["impeller"] | {"defaultFaces"})
        and patches["inlet"]["type"] == "patch" and patches["inlet"]["nFaces"] > 0,
        "Require exact physical inlet and source patch identities.")
    offset = nn
    for name, patch in patches.items():
        require(patch["startFace"] == offset and patch["nFaces"] >= 0,
            "Native exterior patch ranges must cover faces without overlap or gaps.")
        if name == "defaultFaces": require(patch["type"] == "patch" and patch["nFaces"] == 0, "Only a zero-face placeholder is allowed.")
        offset += patch["nFaces"]
    require(offset == nf, "Bind every source face to its actual patch range.")
    first = patches["inlet"]["startFace"]; end = first + patches["inlet"]["nFaces"]
    triangles = []; face_count = last = 0; seen = set()
    for m in re.finditer(r"(\d+)\s*\(([^()]*)\)", face_body):
        ids = m[2].split()
        require(not face_body[last:m.start()].strip() and int(m[1]) == len(ids) == 3
            and all(re.fullmatch(r"\d+", w) and int(w) < np for w in ids), "Parse every source triangular face and point label.")
        ids = tuple(map(int, ids)); require(len(set(ids)) == 3, "Native faces cannot repeat a point.")
        if first <= face_count < end:
            key = tuple(sorted(ids)); require(key not in seen, "The inlet must have no duplicate triangle.")
            seen.add(key); triangles.append(ids)
        last = m.end(); face_count += 1
    require(face_count == nf and not face_body[last:].strip() and len(triangles) == end - first, "Check the whole actual inlet patch.")
    edges = Counter(); directed = Counter(); areas = []; maximum_plane = maximum_normal = 0.; used = set()
    for ids in triangles:
        a, b, c = (points[i] for i in ids)
        maximum_plane = max(maximum_plane, *(abs(pt[2] + p["inletLengthM"]) for pt in (a,b,c)))
        ab = tuple(b[i]-a[i] for i in range(3)); ac = tuple(c[i]-a[i] for i in range(3))
        sf = ((ab[1]*ac[2]-ab[2]*ac[1])/2, (ab[2]*ac[0]-ab[0]*ac[2])/2, (ab[0]*ac[1]-ab[1]*ac[0])/2)
        area = math.hypot(*sf); require(math.isfinite(area) and area > 0, "Every inlet triangle must have positive finite area.")
        maximum_normal = max(maximum_normal, math.dist(tuple(s/area for s in sf), (0.,0.,-1.)))
        require(all(p["hubRadiusM"]-1e-12 <= math.hypot(pt[0],pt[1]) <= p["eyeRadiusM"]+1e-12 for pt in (a,b,c)),
            "Inlet vertices must remain within the authored annular cap.")
        areas.append(area); used.update(ids)
        for a, b in zip(ids, ids[1:]+ids[:1]): edges[tuple(sorted((a,b)))]+=1; directed[a,b]+=1
    require(maximum_plane <= 1e-12 and maximum_normal <= 1e-12, "The entire native inlet must lie at z=-.06 with outward -Z winding.")
    boundary = [e for e,n in edges.items() if n == 1]; degree = Counter(i for e in boundary for i in e)
    require(boundary and all(n in (1,2) for n in edges.values()) and all(n == 2 for n in degree.values())
        and all(directed[a,b] == directed[b,a] == 1 for (a,b),n in edges.items() if n == 2),
        "Require an oriented manifold annular triangulation without open or inconsistent edges.")
    adjacency = {i: [] for i in degree}
    for a,b in boundary: adjacency[a].append(b); adjacency[b].append(a)
    unseen = set(adjacency); rings = []
    while unseen:
        todo = [next(iter(unseen))]; ring = set()
        while todo:
            i = todo.pop()
            if i in ring: continue
            ring.add(i); unseen.discard(i); todo.extend(adjacency[i])
        rings.append(ring)
    require(len(rings) == 2 and {radius for ring in rings for radius in (p["hubRadiusM"],p["eyeRadiusM"])
        if all(abs(math.hypot(points[i][0],points[i][1])-radius) <= 1e-12 for i in ring)} == {p["hubRadiusM"],p["eyeRadiusM"]},
        "Both complete boundary rings must retain the authored hub and eye circles.")
    area = math.fsum(areas); analytic = math.pi*(p["eyeRadiusM"]**2-p["hubRadiusM"]**2)
    relative_error = abs(area-analytic)/analytic
    require(json_file(root, "source-geometry/teaching-geometry.json")[1] == report_row
        and [record(root,r["path"])[1] for r in rows] == rows, "Source bytes changed during inlet geometry inspection.")
    return {"schemaVersion":"untirta.teaching-pump-inlet-flow-geometry-proof.v1", "boundaryFamilyId":BC_FAMILY,
        "geometryReportFile":report_row, "nativeMeshFileRecords":rows, "patch":"inlet", "actualInletFaceRange":[first,end],
        "actualInletTriangleCount":len(triangles), "actualInletNodeCount":len(used), "actualInletOwnerCellCount":len(set(owners[first:end])),
        "actualInletAreaM2":area, "analyticAnnulusAreaM2":analytic, "analyticAreaIsExecutedPatchArea":False,
        "annulusAreaRelativeError":relative_error, "analyticAreaComparisonIsAcceptanceGate":False,
        "maximumPlaneErrorM":maximum_plane, "maximumOutwardNormalError":maximum_normal,
        "allPhysicalInletFacesChecked":True, "twoOrientedBoundaryRingsVerified":True, "sourceBytesImmutable":True,
        "nativeFlowBoundary":"flowRateInletVelocity", "declaredVolumetricFlowM3S":recipe.Q,
        "declaredOutwardFlowM3S":-recipe.Q, "nativePatchAreaAndNormalsUsedAtExecution":True,
        "nativeFlowExecuted":False, "mainApplicationWrites":False,
        "engineeringStages":{"measuredArea":recipe.stage({"triangleCount":len(triangles)}, "A_native=sum_f|Sf|",
            "Sum of actual source inlet triangle areas",area,"m2"),
            "declaredNormalSpeed":recipe.stage({"Q_m3s":recipe.Q,"A_native_m2":area},"U_n=-Q/A_native",
                "-"+format(recipe.Q,".17g")+"/"+format(area,".17g"),-recipe.Q/area,"m/s")}}


def authored_case_files(source, components):
    defaults = {}
    for component in components:
        name = component["component"]
        patches = geometry.boundary_entries(Path(source)/"component-mesh-snapshots"/name/"constant/polyMesh/boundary")
        extra = patches.get("defaultFaces")
        require(extra is None or extra["type"] == "patch" and extra["nFaces"] == 0, "Only actual empty defaultFaces may receive placeholder BCs.")
        defaults[name] = extra is not None
    return {name+"-case/"+p:raw for component in components for name in (component["component"],)
        for p,raw in recipe.case_files(name,default_faces=any(defaults.values()) if name=="impeller" else defaults[name]).items()}


def commands(directory): return source_io.commands(directory)
def historical_commands(report): return source_io.historical_commands(report)


def verify_fields_after_ncc(root, authored):
    """Admit only new exact physical BCs plus pinned native writer defaults."""
    patches = {folder:geometry.boundary_entries(Path(root)/folder/"constant/polyMesh/boundary") for folder in ("impeller-case","volute-case")}
    for relative, expected in authored.items():
        if "/0/" not in relative: continue
        field = Path(relative).name; prefix, original = fields.boundary_blocks(expected, field)
        if relative.startswith("impeller-case/"):
            _, other = fields.boundary_blocks(authored["volute-case/0/"+field],field)
            require(all(n not in original or original[n] == r for n,r in other.items()), "Both components' common field roles must agree.")
            original = {**original, **other}
        actual_prefix, actual = fields.boundary_blocks(record(root,relative)[0],field)
        headers=re.findall(r"\bFoamFile\s*\{([^{}]*)\}",actual_prefix)
        require(len(headers)==1,"Retain one unique initial native FoamFile header.")
        pairs=re.findall(r"([A-Za-z][A-Za-z0-9_]*)\s+([^;{}]+);",headers[0]);header=dict(pairs)
        expected_header={"format":"ascii","class":"volVectorField" if field=="U" else "volScalarField",
            "location":'"0"',"object":field}
        # Pinned Foundation12 IOobjectWriteHeader.C:43-46 omits version for
        # the current stream version. Authored inputs explicitly declare2.0.
        # Keep exactly those two header forms; no extra native metadata roles.
        require(len(header)==len(pairs) and header in (expected_header,{"version":"2.0",**expected_header})
            and not re.sub(r"[A-Za-z][A-Za-z0-9_]*\s+[^;{}]+;","",headers[0]).strip(),
            "Initial data header keys/class/location/object must retain the exact reviewed native role.")
        for key in ("dimensions","internalField"):
            pattern = r"\b"+key+r"\s+([^;{}]+);"
            before, after = re.findall(pattern,prefix), re.findall(pattern,actual_prefix)
            require(len(before)==len(after)==1 and " ".join(before[0].split())==" ".join(after[0].split()), "NCC cannot alter fresh field SI units or internal state.")
        initial = fields.canonical_field_value(re.findall(r"\binternalField\s+([^;{}]+);",prefix)[0])
        require(set(original) <= set(actual), "All new authored physical BCs must survive NCC.")
        for name, original_row in original.items():
            row = actual[name].copy(); defaults = {}
            kind = original_row["type"]
            if kind in {"pressureInletOutletVelocity","inletOutlet"}: defaults["phi"] = "phi"
            if kind == "MRFnoSlip": defaults["value"] = "uniform (0 0 0)"
            if kind == "fixedFluxPressure": defaults.update(gradient="uniform 0",value="uniform 0")
            if field == "omega" and kind == "omegaWallFunction": defaults.update(beta1="0.074999999999999997",blended="false")
            if field == "nut" and kind == "nutkWallFunction": defaults.update({k:fields.canonical_field_value(v) for k,v in {"Cmu":".09","kappa":".41","E":"9.8"}.items()})
            if name=="defaultFaces" and field=="nut" and kind=="calculated" and re.fullmatch(r"nonuniform\s+List<scalar>\s+0\s*\(\s*\)",row.get("value","")):
                patch = patches[relative.split("/",1)[0]].get(name)
                require(patch is not None and patch["type"]=="patch" and patch["nFaces"]==0,"Empty values require a measured zero-face placeholder.")
                row["value"] = original_row["value"]
            require(all(row.get(k)==v for k,v in original_row.items()) and all(k in original_row or defaults.get(k)==v for k,v in row.items()),
                "Keep exact new physical BCs/constant inlet flow and only pinned native writer defaults.")
        for name in set(actual)-set(original):
            require(name in NCC_PATCHES and actual[name].get("type") == ("nonConformalCyclic" if name.startswith("pumpInterface") else "nonConformalError")
                and all(k=="type" or k=="value" and v in {initial,"uniform (0 0 0)" if field=="U" else "uniform 0"} for k,v in actual[name].items()),
                "Only exact NCC constraint fields may be introduced.")


def native_zero_time(raw, expected_delta_t):
    """Typed initial TimeIO data only; caller must bind its control and producer."""
    import teaching_pump_restart_clock as parser
    require(type(expected_delta_t) in (int,float) and math.isfinite(expected_delta_t) and expected_delta_t>0,
        "A zero-clock proof needs an explicit finite positive authored initial step.")
    text=parser.data_text(raw);header=re.match(r"\s*FoamFile\s*\{([^{}]*)\}",text)
    require(header,"The initial clock requires its one native dictionary header.")
    h=parser.entries(header[1]);wanted={"format":["ascii"],"class":["dictionary"],"location":['"0/uniform"'],"object":["time"]}
    require(h==wanted or h=={"version":["2.0"],**wanted},"Initial clock class/location/object/ASCII keys must remain exact.")
    values=parser.entries(text[header.end():])
    require(set(values)=={"beginTime","value","name","index","deltaT","deltaT0"}
        and values["name"]==['"0"'] and values["index"]==["0"]
        and parser.scalar(values,"beginTime")==parser.scalar(values,"value")==0
        and parser.scalar(values,"deltaT")==parser.scalar(values,"deltaT0")==expected_delta_t,
        "The exact six native initial clock keys must match zero start and the authored initial step.")
    return {"nativeRole":"derived-initial-clock-state","name":"0","index":0,"beginTime":0,"value":0,
        "deltaT":float(expected_delta_t),"deltaT0":float(expected_delta_t),"dataOnlyVerified":True,"authoredInput":False,
        "iterationIndexIsPhysicalTime":False,"physicalTimeIntegrationVerified":False,"restartVerified":False,
        "sourceUrl":NATIVE_SOURCE+"src/OpenFOAM/db/Time/TimeIO.C#L205"}


def native_static_pressure(raw,cells,patches,*,ncc_counts=None):
    """Exact gauge-Pa result, with no pressure-reference addition or source alias."""
    import teaching_pump_restart_clock as parser
    text=parser.data_text(raw)
    match=re.fullmatch(r"\s*FoamFile\s*\{([^{}]*)\}\s*dimensions\s*\[\s*1\s+-1\s+-2\s+0\s+0\s+0\s+0\s*\]\s*;\s*internalField\s+(.+?)\s*;\s*boundaryField\s*\{(.*)\}\s*",text,re.S)
    require(match,"The generated static pressure needs complete native scalar data and exact Pa dimensions.")
    header=parser.entries(match[1]);wanted={"format":["ascii"],"class":["volScalarField"],"location":['"0"'],"object":["staticPressurePa"]}
    require(header==wanted or header=={"version":["2.0"],**wanted},"Generated gauge pressure native header must retain its exact role.")
    def scalar_values(source,count):
        value=re.fullmatch(r"\s*(?:uniform\s+("+FLOAT+r")|nonuniform\s+List<scalar>\s+(\d+)\s*\(([^()]*)\))\s*",source,re.S)
        require(value,"Generated gauge pressure values must be plain finite scalars.")
        tokens=[value[1]] if value[1] is not None else value[3].split()
        require(value[1] is not None or len(tokens)==int(value[2])==count,"Each full gauge pressure value list must cover its current native cells/faces.")
        require(len(tokens)<=1000000 and all(re.fullmatch(FLOAT,v) and math.isfinite(float(v)) for v in tokens),"Every gauge-pressure scalar must be finite and bounded.")
        return [float(v) for v in tokens]
    values=scalar_values(match[2],cells);require(values and min(values)==max(values)==0,"Current fresh p=0 initial state requires zero internal gauge-Pa output.")
    blocks=list(re.finditer(r'"?([A-Za-z0-9_-]+)"?\s*\{([^{}]*)\}',match[3]))
    require(len(blocks)==len(patches) and len({b[1] for b in blocks})==len(blocks) and {b[1] for b in blocks}==set(patches)
        and not re.sub(r'"?[A-Za-z0-9_-]+"?\s*\{[^{}]*\}',"",match[3]).strip(),"Generated gauge pressure must cover every actual native patch exactly once.")
    count=0;allowed={"calculated","extrapolatedCalculated","zeroGradient","fixedValue","nonConformalCyclic","nonConformalError","nonConformalCalculated"}
    for block in blocks:
        entries=parser.entries(block[2]);kind=entries.pop("type",None)
        require(kind is not None and len(kind)==1 and kind[0] in allowed,"Only known non-executable gauge-pressure patch roles are supported.")
        if kind[0].startswith("nonConformal"):
            require(block[1] in NCC_PATCHES and (kind[0]=="nonConformalCalculated" or kind[0]==patches[block[1]]["type"]),"Gauge-pressure NCC constraints must match their actual native patch role.")
        override=entries.pop("patchType",None);require(override is None or override==[patches[block[1]]["type"]],"Any native gauge-pressure patch override must match the mesh.")
        if not entries:
            require(kind[0] in {"zeroGradient","nonConformalCyclic","nonConformalError"},"Calculated gauge-pressure patches must store their values.");continue
        require(set(entries)=={"value"},"Gauge-pressure patch dictionaries cannot contain code or unknown keys.")
        expected=patches[block[1]]["nFaces"]
        if block[1] in NCC_PATCHES and entries["value"][0]=="nonuniform":
            require(isinstance(ncc_counts,dict) and block[1] in ncc_counts,"Gauge-pressure nonuniform NCC values need current native cache face counts.")
            expected=ncc_counts[block[1]]
        count+=len(scalar_values(" ".join(entries["value"]),expected))
    return {"dimensions":"1 -1 -2 0 0 0 0","allInternalCellsRepresented":cells,"storedInternalScalarCount":len(values),
        "internalMinimum":min(values),"internalMaximum":max(values),"nativePatchCount":len(blocks),"storedBoundaryScalarCount":count,
        "allStoredValuesFinite":True,"allNativePatchRolesChecked":True,"pressureBasis":"gauge-Pa","referenceAdditionPa":0,
        "equation":"staticPressurePa=rhoInf*p_kinematic; calcTotal=false does not add pRef", "sourceUrl":NATIVE_SOURCE+"src/functionObjects/field/pressure/pressure.C#L61"}


def generated_data(directory, *, after_native_execution=False):
    """Read only known native data; never call the phase admission/constructor.

    The same pinned static NCC/NO_READ region and scalar data writers serve
    multiple solver families. Their pure role parsers are reused here, with
    this lane's own kinematic p/rhoInf/outlet-static-offset function proof.
    Actual byte records remain separate from scientific input signatures.
    """
    import teaching_pump_phase_case as data
    require(type(after_native_execution) is bool,"Declare whether native-generated data is permitted.")
    root=base.regular_directory(directory)
    report,_=json_file(root,REPORT)
    require(report.get("schemaVersion")==SCHEMA and report.get("boundaryFamilyId")==BC_FAMILY
        and recipe.verify_profile(report.get("researchProfile"))==recipe.verify_profile(PROFILE),
        "Inspect generated data only in the distinct inlet-flow preparation namespace.")
    authored=authored_case_files(root/"source-teaching",report.get("components",[]))
    require(set(row["component"] for row in report.get("components",[]))=={"impeller","volute"},"Both exact source components are required.")
    present=set()
    cache=root/"impeller-case/constant/fvMesh"
    for component in ("impeller-case","volute-case"):
        for folder in ("0","constant","system"):
            path=base.regular_directory(root/component/folder)
            expected={Path(name).name for name in authored if name.startswith(component+"/"+folder+"/")}
            if folder=="constant":expected.add("polyMesh")
            if component=="impeller-case" and after_native_execution:
                if folder=="constant" and cache.exists():expected.add("fvMesh")
                if folder=="0":
                    present={n for n in {*STARTUP_FIELDS,"cellToRegion"} if (path/n).exists() or (path/n).is_symlink()}
                    expected|=present
                    if (path/"uniform").exists() or (path/"uniform").is_symlink():expected.add("uniform")
            entries=list(path.iterdir());require(len(entries)<=64 and {p.name for p in entries}==expected,
                "Unknown or prefilled initial data/native dictionary roles are prohibited.")
            for entry in entries:
                require(not entry.is_symlink() and not getattr(entry,"is_junction",lambda:False)()
                    and (entry.is_dir() if folder=="constant" and entry.name in {"polyMesh","fvMesh"}
                        or component=="impeller-case" and after_native_execution and folder=="0" and entry.name=="uniform" else entry.is_file()),
                    "Keep every initial/control path an exact ordinary native role.")
    rows=[];proofs=[];bound={};ncc_counts=None
    def read(relative,limit=MAX_FILE):
        raw,row=record(root,relative,limit=limit);bound[relative]=row;return raw,row
    mesh_relative="impeller-case/constant/polyMesh"
    if cache.exists():
        base.regular_directory(cache)
        require(after_native_execution and {p.name for p in cache.iterdir()}=={"polyFaces"},"Only the actual static NCC polyFaces cache is permitted after native execution.")
        raw,row=read("impeller-case/constant/fvMesh/polyFaces",data.MAX_NATIVE_CACHE_BYTES)
        for name in MESH_FILES:read(mesh_relative+"/"+name)
        proof=data.native_polyfaces(raw,root/mesh_relative);proof["path"]=row["path"]
        proof["sourceMeshFileRecords"]=[bound[mesh_relative+"/"+n] for n in MESH_FILES]
        ncc_counts={p["patch"]:p["nativeFaceCount"] for p in proof["nonconformalPatchMappings"]}
        rows.append(row);proofs.append(proof)
    log_relative="logs/02-checkMesh.log";log_path=root/log_relative
    log=log_row=None
    if log_path.exists() or log_path.is_symlink():log,log_row=read(log_relative,LIMITS["maximumLogBytes"])
    region_expected=log is not None and (b"cellToRegion" in log or re.search(rb"\*Number of regions:\s*2\s*$",log,re.M))
    if "cellToRegion" in present or region_expected:
        require(after_native_execution and "cellToRegion" in present and log is not None,
            "Retain the complete native region diagnostic plus generating checkMesh log; never prefill or omit it.")
        raw,row=read("impeller-case/0/cellToRegion")
        topology=[read(mesh_relative+"/"+n) for n in ("owner","neighbour","cellZones")]
        proof=data.native_cell_to_region(raw,*(item[0] for item in topology),log)
        proof.update(path=row["path"],sourceCheckMeshLog=log_row,sourceMeshFileRecords=[item[1] for item in topology])
        rows.append(row);proofs.append(proof)
    startup=sorted(present & set(STARTUP_FIELDS))
    if startup:
        require(after_native_execution,"Generated function-object results cannot be prefilled.")
        for name in MESH_FILES:read(mesh_relative+"/"+name)
        no,owner=base.native_list(root/mesh_relative/"owner");nn,neighbour=base.native_list(root/mesh_relative/"neighbour")
        tokens=owner.split()+neighbour.split()
        require(len(tokens)==no+nn and tokens and all(re.fullmatch(r"\d+",n) for n in tokens),"Startup fields need complete current native addressing.")
        cells=max(map(int,tokens))+1;require(0<cells<=500000,"Bound every represented native cell.")
        patches=geometry.boundary_entries(root/mesh_relative/"boundary")
        control,control_row=read("impeller-case/system/controlDict")
        require(control==authored["impeller-case/system/controlDict"],"Generated function objects must use this exact trusted boundary-family recipe.")
        text=control.decode("ascii")
        for name in startup:
            raw,row=read("impeller-case/0/"+name)
            proof=native_static_pressure(raw,cells,patches,ncc_counts=ncc_counts) if name=="staticPressurePa" else data.startup_scalar_field(raw,name,cells,patches,ncc_counts=ncc_counts)
            function,kind,field=STARTUP_FIELDS[name]
            blocks=re.findall(r"(?m)^"+re.escape(function)+r"\s*\{([^{}]*)\}",text)
            require(len(blocks)==1 and re.findall(r"\btype\s+(\w+)\s*;",blocks[0])==[kind],"Generated data binds its exact authored function name/type.")
            if field is not None:
                require(re.findall(r"\bfield\s+(\w+)\s*;",blocks[0])==[field] and re.findall(r"\bresult\s+(\w+)\s*;",blocks[0])==[name],"Generated field/result roles must match.")
            if name=="totalPressurePa":
                require(re.findall(r"\bcalcTotal\s+(\w+)\s*;",blocks[0])==["yes"]
                    and re.findall(r"\brho\s+(\w+)\s*;",blocks[0])==["rhoInf"]
                    and re.findall(r"\brhoInf\s+([^;{}]+);",blocks[0])==["998.2"]
                    and re.findall(r"\bpRef\s+([^;{}]+);",blocks[0])==["1000000"],
                    "Single-phase total pressure maps rhoInf*p plus outlet-static1000000Pa exactly once, not absolute phase p.")
            if name=="staticPressurePa":
                require(re.findall(r"\bcalcTotal\s+(\w+)\s*;",blocks[0])==["no"]
                    and re.findall(r"\bcalcCoeff\s+(\w+)\s*;",blocks[0])==["no"]
                    and re.findall(r"\brho\s+(\w+)\s*;",blocks[0])==["rhoInf"]
                    and re.findall(r"\brhoInf\s+([^;{}]+);",blocks[0])==["998.2"] and not re.search(r"\bpRef\b",blocks[0]),
                    "Static pressure is the exact gauge-Pa function result, with no absolute reference addition.")
                pressure,pressure_row=read("impeller-case/0/p");prefix,_=fields.boundary_blocks(pressure,"p")
                require(re.findall(r"\bdimensions\s+([^;{}]+);",prefix)==["[0 2 -2 0 0 0 0]"]
                    and re.findall(r"\binternalField\s+([^;{}]+);",prefix)==["uniform 0"],"Generated gauge-Pa output must bind the actual fresh zero kinematic-p initial state.")
                proof["sourceInitialPressureFile"]=pressure_row
            proof.update(path=row["path"],nativeRole="derived-startup-volScalarField",dataOnlyVerified=True,authoredInput=False,
                sourceFunctionObject=function,sourceFunctionType=kind,resultField=name,sourceControlFile=control_row,
                numericalAcceptanceInherited=False,sourceUrl=proof.get("sourceUrl",data.STARTUP_FIELD_SOURCE),
                pressureReferenceLocation="outlet" if name=="totalPressurePa" else None)
            rows.append(row);proofs.append(proof)
    zero_clock=root/"impeller-case/0/uniform"
    if zero_clock.exists() or zero_clock.is_symlink():
        require(after_native_execution and {p.name for p in base.regular_directory(zero_clock).iterdir()}=={"time"},"Only the exact postnative initial clock file may occur under0/uniform.")
        raw,row=read("impeller-case/0/uniform/time",MAX_REPORT);control,control_row=read("impeller-case/system/controlDict")
        require(control==authored["impeller-case/system/controlDict"],"The initial clock must bind the unchanged trusted control dictionary.")
        text=control.decode("ascii")
        require(re.findall(r"\bstartFrom\s+([^;{}]+);",text)==["startTime"] and re.findall(r"\bstartTime\s+([^;{}]+);",text)==["0"]
            and re.findall(r"\bdeltaT\s+([^;{}]+);",text)==["1"] and not re.search(r"\buserTime\b|\badjustTimeStep\s+yes\s*;",text),"This initial clock is a fixed steady iteration clock, not adaptive physical time or restart.")
        log,solver_row=read("logs/03-foamRun.log",LIMITS["maximumLogBytes"]);log_text=log.decode("utf-8")
        geometry.completed_log(log_text)
        require(re.findall(r"(?m)^Selecting solver (\w+)\s*$",log_text)==["incompressibleFluid"],"The initial clock data needs the actual completed pinned single-phase solver producer.")
        proof=native_zero_time(raw,1);proof.update(path=row["path"],sourceControlFile=control_row,sourceSolverLog=solver_row,numericalAcceptanceInherited=False)
        rows.append(row);proofs.append(proof)
    require(all(record(root,path,limit=LIMITS["maximumLogBytes"] if path.startswith("logs/") else MAX_FILE)[1]==row
        for path,row in bound.items()),"Native data or generating mesh/control/log bytes changed during inspection.")
    paired=sorted(zip(rows,proofs),key=lambda pair:pair[0]["path"])
    return [r for r,_ in paired],[p for _,p in paired]


def _bindings(verified):
    return {"sourceGeometryExecutionReportSha256":verified["reportFile"]["sha256"],
        "sourceGeometryPreparationReportSha256":verified["preparation"]["reportFile"]["sha256"],
        "sourceGeometryInputManifestSha256":verified["preparation"]["report"]["nativeInputManifestSha256"],
        "sourceAuthoredGeometryReportSha256":verified["authoredGeometry"]["reportFile"]["sha256"],
        "sourceGeometryIdentitySha256":verified["authoredGeometry"]["report"]["geometryIdentitySha256"]}


def prepare(geometry_native_directory, output_directory):
    source = safety.scope_path(geometry_native_directory); output = safety.scope_path(output_directory,fresh=True)
    require(not output.resolve().is_relative_to(source.resolve()), "Use a fresh separate inlet-flow physics case.")
    verified = validate_source(source); authored = authored_case_files(source,verified["components"])
    inlet_proof = verify_inlet_geometry(source); outlet_proof = outlet_geometry(verified["outletProfileEvidence"])
    require(sum(r["bytes"] for r in verified["sourceFileRecords"]+verified["snapshots"])+2*sum(map(len,authored.values()))+MAX_REPORT <= MAX_WORKSPACE,
        "Bound complete source copies, native components and fresh authored inputs before creating output.")
    output.mkdir(parents=True)
    try:
        copied = []; mesh = []; current = []; retained = []
        for row in verified["sourceFileRecords"]:
            raw, actual = record(source,row["path"]); require(actual==row,"Geometry source changed while copying.")
            target="source-teaching/"+row["path"];base.write_exclusive(output/target,raw);copied.append(record(output,target)[1])
        for row in verified["snapshots"]:
            parts=row["path"].split("/");target=parts[1]+"-case/"+"/".join(parts[2:])
            raw,actual=record(source,row["path"]);require(actual==row,"The source snapshot changed while copying.")
            base.write_exclusive(output/target,raw);mesh.append(record(output,target)[1])
        for path,raw in authored.items():
            base.write_exclusive(output/path,raw);current.append(record(output,path)[1])
            target="authored-physics/"+path;base.write_exclusive(output/target,raw);retained.append(record(output,target)[1])
        report={"schemaVersion":SCHEMA,"createdAt":datetime.now(timezone.utc).isoformat(),"status":STATUS,
            "sourceGeometryRoute":ROUTE,"boundaryFamilyId":BC_FAMILY,"sourceEvidenceDirectory":"source-teaching",
            "researchProfile":recipe.verify_profile(PROFILE),
            "outletStaticAbsoluteReferencePa":recipe.OUTLET_STATIC_REFERENCE_PA,"inletTotalPressurePrescribed":False,
            **_bindings(verified),"sourceEvidenceFileRecords":copied,
            "sourceComponentFiles":[{**r,"path":"source-teaching/"+r["path"]} for r in verified["snapshots"]],
            "initialCaseMeshFiles":mesh,"authoredInputFiles":current,"authoredPhysicsFileRecords":retained,
            "components":verified["components"],"nativeCommandArguments":commands(output),
            "classroomAssumptions":deepcopy(ASSUMPTIONS),"sampling":deepcopy(SAMPLING),"claimBoundary":CLAIM,
            "inletFlowGeometryEvidence":_prefix(inlet_proof,"source-teaching"),"outletStaticGeometryEvidence":_prefix(outlet_proof,"source-teaching"),
            "absolutePressureMapping":{"nativeField":"p","nativePressureUnits":"m2/s2","referenceLocation":"outlet",
                "referenceQuantity":"static absolute pressure","referencePa":recipe.OUTLET_STATIC_REFERENCE_PA,"referenceAdditionCount":1,
                "equation":"p_absolute_Pa=998.2*p_kinematic_m2s2+1000000"},
            "sourceNativeGeometryQualityVerified":True,"sourceCoupledCoverageVerified":True,"sourceInputsImmutable":True,
            "limits":deepcopy(LIMITS),**dict.fromkeys(FALSE_FLAGS,False)}
        require(source_inventory(source)==verified["sourceFileRecords"],"Source inventory changed before final preparation.")
        raw=encoded(report);require(len(raw)<=MAX_REPORT,"Bound the complete immutable preparation report.")
        base.write_exclusive(output/REPORT,raw);verify_output(output);return report
    except Exception:
        safety.scope_path(output);shutil.rmtree(output);raise


def verify_output(directory, *, after_native_ncc=False):
    require(type(after_native_ncc) is bool,"Declare the native verification stage explicitly.")
    root=safety.scope_path(directory);report,meta=json_file(root,REPORT);source=root/"source-teaching";verified=validate_source(source)
    expected_keys=set(FALSE_FLAGS)|{"schemaVersion","createdAt","status","sourceGeometryRoute","boundaryFamilyId","sourceEvidenceDirectory",
        "outletStaticAbsoluteReferencePa","inletTotalPressurePrescribed",*_bindings(verified),"sourceEvidenceFileRecords","sourceComponentFiles",
        "initialCaseMeshFiles","authoredInputFiles","authoredPhysicsFileRecords","components","nativeCommandArguments","classroomAssumptions",
        "sampling","claimBoundary","inletFlowGeometryEvidence","outletStaticGeometryEvidence","absolutePressureMapping",
        "sourceNativeGeometryQualityVerified","sourceCoupledCoverageVerified","sourceInputsImmutable","limits","researchProfile"}
    require(set(report)==expected_keys,"Unknown preparation fields or inherited result claims are prohibited.")
    require(recipe.verify_profile(report.get("researchProfile"))==recipe.verify_profile(PROFILE),"The exact typed research profile must remain current and source-bound.")
    require(report.get("schemaVersion")==SCHEMA and report.get("sourceGeometryRoute")==ROUTE and report.get("sourceEvidenceDirectory")=="source-teaching"
        and report.get("boundaryFamilyId")==BC_FAMILY and report.get("status")==STATUS
        and type(report.get("outletStaticAbsoluteReferencePa")) is float and report["outletStaticAbsoluteReferencePa"]==recipe.OUTLET_STATIC_REFERENCE_PA
        and report.get("inletTotalPressurePrescribed") is False
        and encoded(report.get("limits"))==encoded(LIMITS) and encoded(report.get("classroomAssumptions"))==encoded(ASSUMPTIONS)
        and encoded(report.get("sampling"))==encoded(SAMPLING) and report.get("claimBoundary")==CLAIM
        and all(report.get(k) is False for k in FALSE_FLAGS) and all(report.get(k) is True for k in
            ("sourceNativeGeometryQualityVerified","sourceCoupledCoverageVerified","sourceInputsImmutable")),
        "Use the distinct current inlet-flow family without inherited flow or qualification claims.")
    timestamp=report.get("createdAt");require(isinstance(timestamp,str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00",timestamp),"Retain a typed UTC preparation timestamp.")
    require(all(report.get(k)==v for k,v in _bindings(verified).items()) and report.get("components")==verified["components"],"Current accepted geometry/source identities must remain bound.")
    require(report.get("inletFlowGeometryEvidence")==_prefix(verify_inlet_geometry(source),"source-teaching")
        and report.get("outletStaticGeometryEvidence")==_prefix(outlet_geometry(verified["outletProfileEvidence"]),"source-teaching"),
        "Both source-bound section geometry proofs must reproduce; neither supplies executed flow.")
    mapping={"nativeField":"p","nativePressureUnits":"m2/s2","referenceLocation":"outlet","referenceQuantity":"static absolute pressure",
        "referencePa":recipe.OUTLET_STATIC_REFERENCE_PA,"referenceAdditionCount":1,"equation":"p_absolute_Pa=998.2*p_kinematic_m2s2+1000000"}
    require(encoded(report.get("absolutePressureMapping"))==encoded(mapping),"The fixed reference is outlet static pressure, never prescribed inlet total pressure.")
    require(report.get("sourceEvidenceFileRecords")==[{**r,"path":"source-teaching/"+r["path"]} for r in verified["sourceFileRecords"]]
        and report.get("sourceComponentFiles")==[{**r,"path":"source-teaching/"+r["path"]} for r in verified["snapshots"]],"Full copied source/current snapshots must reproduce.")
    authored=authored_case_files(source,verified["components"]); expected=[];retained=[];mesh=[]
    for path,raw in authored.items():
        actual,row=record(root,"authored-physics/"+path);require(actual==raw,"Immutable new authored recipe bytes changed.");retained.append(row)
        expected.append({"path":path,"bytes":len(raw),"sha256":sha256(raw).hexdigest()})
        if "/0/" not in path or not after_native_ncc:require(record(root,path)[0]==raw,"Fresh input dictionary or initial field changed.")
    for row in verified["snapshots"]:
        parts=row["path"].split("/");target=parts[1]+"-case/"+"/".join(parts[2:]);mesh.append({**row,"path":target})
        if not after_native_ncc:require(record(root,target)[0]==record(source,row["path"])[0],"Initial native components changed before merge/NCC.")
    require(report.get("authoredInputFiles")==expected and report.get("authoredPhysicsFileRecords")==retained and report.get("initialCaseMeshFiles")==mesh,
        "Retain exact recipe and native mesh inventories without importing solver results.")
    if after_native_ncc:verify_fields_after_ncc(root,authored)
    generated_rows,generated_proof=generated_data(root,after_native_execution=after_native_ncc)
    historical_commands(report)
    return {"report":report,"reportFile":meta,"components":verified["components"],"source":verified,
        "nativeGeneratedDataRecords":generated_rows,"nativeGeneratedDataEvidence":generated_proof}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--geometry-native-directory",required=True);parser.add_argument("--output-directory",required=True)
    args=parser.parse_args();result=prepare(args.geometry_native_directory,args.output_directory)
    print(json.dumps({"status":result["status"],"reportFile":REPORT,"boundaryFamilyId":BC_FAMILY,"actualCfdRunVerified":False,"mainApplicationWrites":False}))


if __name__=="__main__":main()
