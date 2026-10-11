"""Prepare geometry-only Foundation-12 cases for an authored classroom pump.

This is a distinct CAD source. No official pump data, flow result or previous
geometry acceptance is inherited. Every mesh/interface fact is re-read.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_openfoam_case as base
import run_frozen_rotor_surface_check as safety
import run_frozen_rotor_snappy as native_geometry
import verify_tetra_face_incidence as incidence

SCHEMA = "untirta.teaching-pump-openfoam-preparation.v1"
MANIFEST_SCHEMA = "untirta.teaching-pump-native-input-manifest.v1"
REPORT, MANIFEST = "teaching-native-preparation.json", "teaching-native-input-manifest.json"
SOURCE_ROUTE = "CLASSROOM_AUTHORED_GEOMETRY"
COMPONENTS = ("impeller", "volute")
SOURCE_FILES = ("teaching-geometry.json", "teaching-input-manifest.json", "teaching-pump.brep", "impeller.msh", "volute.msh")
MAX_FILE, MAX_REPORT, MAX_WORKSPACE = 128*1024**2, 128*1024, 512*1024**2
MAX_COMPONENT_CELLS, MAX_COMBINED_CELLS, MAX_COMPONENT_NODES = 450_000, 500_000, 500_000
FALSE_FLAGS = ("commandExecutionPerformedByThisTool", "nativeGeometryQualityVerified", "coupledInterfaceCoverageVerified",
    "numericalBaselineQualified", "qualifiedBaseline", "flowExecuted", "cavitationModelExecuted", "cavitationSweepExecuted",
    "physicalTransientExecuted", "npshrCalculated", "npsh3Qualified", "restartVerified", "meshConvergenceVerified",
    "timeStepConvergenceVerified", "wallResolutionVerified", "physicalValidationVerified", "simulationExecuted",
    "actualCfdRunVerified", "manufacturerPublishedGeometry", "mainApplicationWrites")
LIMITS = {"maximumSeconds": 600, "maximumCpus": 1, "maximumMemoryBytes": 2*1024**3,
    "maximumWorkspaceBytes": MAX_WORKSPACE, "maximumFileBytes": MAX_FILE, "maximumReportBytes": MAX_REPORT,
    "maximumLogBytes": 4*1024**2, "maximumActualComponentCells": MAX_COMPONENT_CELLS,
    "maximumActualCombinedCells": MAX_COMBINED_CELLS, "maximumActualComponentNodes": MAX_COMPONENT_NODES,
    "exclusiveMaximumActualCombinedNodes": 1_000_000}
CLAIM = "New authored classroom centrifugal-pump CAD in explicit SI metres. Geometry-only conversion, wall roles and NCC/native mesh checks; no official Ansys/Grundfos geometry or pump performance, flow, baseline, cavitation, NPSHr or transient result is inherited."


class TeachingCaseIssue(ValueError):
    pass


def require(condition, action):
    if not condition:
        raise TeachingCaseIssue(action)


def encoded(value):
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)+"\n").encode("ascii")
    require(0 < len(raw) <= MAX_REPORT, "Bounded finite canonical teaching metadata is required.")
    return raw


def record(root, relative, *, limit=MAX_FILE):
    require(isinstance(relative, str) and len(relative) <= 256 and "\\" not in relative
        and not relative.startswith("/") and all(re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", p) for p in relative.split("/")),
        "Read only safe relative authored evidence paths.")
    path = Path(root)/relative
    for item in (path, *path.parents):
        require(not item.is_symlink() and not getattr(item,"is_junction",lambda:False)(), "Evidence cannot traverse links or junctions.")
        if item == Path(root): break
    raw, metadata = safety.descriptor(path, relative, limit)
    return raw, metadata


def json_file(root, relative):
    raw, row = record(root, relative, limit=MAX_REPORT)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate teaching metadata keys are prohibited.")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(TeachingCaseIssue("Nonfinite metadata is prohibited.")))
    require(isinstance(value, dict), "A structured teaching evidence document is required.")
    return value, row


def interface_inventory(raw, name):
    """Audit actual outward boundary winding and return exact interface facets.

No matching tolerance, welding or node-ID equivalence between files is used.
Shared-interface equality uses the actual float64 coordinate triplets.
"""
    sections = {m[1]: m[2].strip().splitlines() for m in re.finditer(r"(?m)^\$(\w+)\s*\n(.*?)^\$End\1\s*$", raw.decode("ascii"), re.S)}
    groups = {}
    for line in sections["PhysicalNames"][1:]:
        match = re.fullmatch(r'(2|3) (\d+) "([A-Za-z0-9_-]+)"',line.strip())
        groups[(int(match[1]),int(match[2]))] = match[3]
    nodes = {int(row[0]): tuple(map(float,row[1:])) for row in (line.split() for line in sections["Nodes"][1:])}
    boundary, tets = {}, []
    def parity(values):
        return -1 if sum(values[i] > values[j] for i in range(3) for j in range(i+1,3))%2 else 1
    for line in sections["Elements"][1:]:
        row = list(map(int,line.split())); ids = tuple(row[3+row[2]:])
        if row[1] == 2:
            key = tuple(sorted(ids)); require(key not in boundary, "Each tagged boundary triangle is unique.")
            boundary[key] = (groups[(2,row[3])],parity(ids),ids)
        elif row[1] == 4: tets.append(ids)
    observed = {}
    for tet in tets:
        for slots in ((0,2,1),(0,1,3),(1,2,3),(2,0,3)):
            face = tuple(tet[i] for i in slots); key = tuple(sorted(face))
            if key in boundary:
                require(key not in observed and parity(face) == boundary[key][1], "Stored boundary winding must equal its one actual outward tetrahedron face.")
                observed[key] = True
    require(set(observed) == set(boundary), "Every authored boundary facet must be an actual exterior volume face.")
    facets = {}
    for patch, _, ids in boundary.values():
        if patch != name: continue
        xyz = tuple(nodes[i] for i in ids); key = tuple(sorted(xyz))
        require(key not in facets, "The shared interface cannot contain geometrically duplicate facets.")
        facets[key] = parity(xyz)
    require(facets, "A nonempty exact authored shared interface is required.")
    return facets


def validate_inputs(directory):
    import teaching_pump_geometry as geometry
    source = safety.scope_path(directory)
    verified = geometry.verify_output(source)
    report = verified["report"]
    require(report.get("schemaVersion") == geometry.SCHEMA and report.get("provenance") == SOURCE_ROUTE
        and report.get("units") == "SI" and re.fullmatch(r"[0-9a-f]{40}",report.get("generatorSourceCommit", ""))
        and all(re.fullmatch(r"[0-9a-f]{64}",report.get(k,"")) for k in ("parametersSha256","inputManifestSha256")),
        "Only the new authored classroom SI CAD schema with current parameter/code/manifest binding is admitted.")
    # The geometry author owns the complete parameter/manifest/code provenance
    # verifier. Native admission still independently checks actual MSH topology.
    require(set(p.name for p in source.iterdir()) == set(SOURCE_FILES), "The authored source package must have its five exact immutable files.")
    rows = [record(source,name,limit=MAX_REPORT if name.endswith(".json") else MAX_FILE)[1] for name in SOURCE_FILES]
    require(sum(r["bytes"] for r in rows) <= MAX_FILE, "The combined teaching CAD/mesh input is bounded to128MiB.")
    components, interfaces = [], []
    expected = report.get("components")
    require(isinstance(expected,list) and [r.get("component") for r in expected] == list(COMPONENTS), "Exact rotor/stator component order is required.")
    for component, described in zip(COMPONENTS,expected):
        raw, mesh_row = record(source,component+".msh")
        actual = base.msh_inventory(raw)
        metadata = described.get("generatedFile",{})
        require(metadata == {"name":component+".msh","bytes":mesh_row["bytes"],"sha256":mesh_row["sha256"],"format":"gmsh-msh2.2-ascii"}, "Actual authored MSH bytes must match the source manifest; MSH2.2 ASCII format is independently parsed.")
        require(0 < actual["cellCount"] <= MAX_COMPONENT_CELLS and 0 < actual["nodeCount"] <= MAX_COMPONENT_NODES
            and actual["cellCount"] == described.get("cellCount") and actual["nodeCount"] == described.get("nodeCount")
            and actual["patchFaces"] == described.get("patchFaces") and set(actual["patchFaces"]) == base.PATCHES[component]
            and described.get("patchTypes") == {p:"wall" if p in base.ROTATING|base.STATIONARY else "patch" for p in base.PATCHES[component]}
            and described.get("positiveTetrahedraVerified") is True and described.get("finiteCoordinatesVerified") is True
            and actual["cellZone"] == ("rotating-fluid" if component=="impeller" else "stationary-fluid"), "Actual tagged mesh counts, all11 roles and single fluid zone must agree.")
        proof = incidence.verify_msh(source/(component+".msh"))
        interfaces.append(interface_inventory(raw,"interface-impeller-outlet" if component=="impeller" else "interface-volute-inlet"))
        components.append({"component":component,"caseDirectory":component+"-case",**{k:actual[k] for k in ("cellCount","nodeCount","patchFaces","cellZone")},
            "generatedFile":mesh_row,"tetraFaceIncidence":proof})
    require(sum(r["cellCount"] for r in components) <= MAX_COMBINED_CELLS and sum(r["nodeCount"] for r in components) < 1_000_000,
        "Actual combined teaching cells/nodes exceed the fixed native profile.")
    require(set(interfaces[0]) == set(interfaces[1]) and all(interfaces[0][key] == -interfaces[1][key] for key in interfaces[0]),
        "Both authored interface exports must have exactly the same coordinate facets and opposite outward owner winding.")
    return {"report":report,"reportFile":rows[0],"sourceFileRecords":rows,"components":components,
        "commonInterfaceEvidence":{"exactCoordinateFacetIdentityVerified":True,"oppositeOutwardOwnerWindingVerified":True,
            "triangleCountPerSide":len(interfaces[0]),"matchingToleranceUsed":False,"dynamicCoverageVerified":False}}


def case_files(component):
    require(component in COMPONENTS,"Use one of the two exact authored fluid components.")
    header=base.foam_header
    files={"system/controlDict":header("controlDict")+"application checkMesh;\nstartFrom startTime;\nstartTime 0;\nstopAt endTime;\nendTime 0;\ndeltaT 1;\nwriteControl timeStep;\nwriteInterval 1;\nwriteFormat ascii;\nwritePrecision 17;\nwriteCompression off;\ntimeFormat general;\ntimePrecision 17;\nrunTimeModifiable false;\nfunctions {}\n",
        "system/fvSchemes":header("fvSchemes")+"ddtSchemes {default steadyState;}\ngradSchemes {default Gauss linear;}\ndivSchemes {default none;}\nlaplacianSchemes {default Gauss linear corrected;}\ninterpolationSchemes {default linear;}\nsnGradSchemes {default corrected;}\n",
        "system/fvSolution":header("fvSolution")+"solvers {}\n",
        "system/changeDictionaryDict":header("changeDictionaryDict")+"boundary\n{\n"+"".join('    "'+p+'" {type wall;}\n' for p in sorted(base.PATCHES[component] & (base.ROTATING|base.STATIONARY)))+"}\n"}
    if component=="impeller":
        files["system/createNonConformalCouplesDict"]=header("createNonConformalCouplesDict")+"fields false;\nnonConformalCouples {pumpInterface {patches (interface-impeller-outlet interface-volute-inlet); transform none;}}\n"
    return files


def _commands(root):
    rows=[]
    for name in COMPONENTS:
        case=str(root/(name+"-case"))
        rows.extend([["gmshToFoam","-case",case,str(root/(name+"-case")/(name+".msh"))],
            ["changeDictionary","-case",case],["checkMesh","-case",case,"-allGeometry","-allTopology","-writeSets"]])
    case=str(root/"impeller-case");other=str(root/"volute-case")
    return rows+[["mergeMeshes","-case",case,"-addCases",'(\"'+other+'\")',"-overwrite"],
        ["createNonConformalCouples","-case",case,"-overwrite"],
        ["checkMesh","-case",case,"-allGeometry","-allTopology","-writeSets"]]


def commands(directory):
    return _commands(Path(directory).absolute())


def declared_report(verified,authored,source_rows):
    parameters=verified["report"].get("parameters",{})
    require(isinstance(parameters,dict) and all(type(parameters.get(key)) in (int,float) and math.isfinite(parameters[key]) and parameters[key]>0
        for key in ("speedRpm","massFlowKgS")),"Declare the verified authored speed/flow scale without substituting defaults.")
    return {"schemaVersion":SCHEMA,"status":"CLASSROOM_CAD_NATIVE_GEOMETRY_EXECUTION_REQUIRED","sourceGeometryRoute":SOURCE_ROUTE,
        "sourceEvidenceDirectory":"source-geometry","sourceGeometryReportSha256":verified["reportFile"]["sha256"],
        "sourceFileRecords":source_rows,"authoredInputFiles":authored,"components":verified["components"],
        "commonInterfaceEvidence":verified["commonInterfaceEvidence"],"coordinateUnits":"SI_METRES",
        "declaredOperatingScale":{"rotationalSpeedRpm":parameters["speedRpm"],"massFlowKgS":parameters["massFlowKgS"],"physicsInputsAuthored":False},
        "limits":LIMITS,"claimBoundary":CLAIM,**{k:False for k in FALSE_FLAGS}}


def prepare(source_directory,output_directory):
    source=safety.scope_path(source_directory);output=safety.scope_path(output_directory,fresh=True)
    require(not output.resolve().is_relative_to(source.resolve()),"Use a separate fresh native workspace.")
    verified=validate_inputs(source);output.mkdir(parents=True)
    try:
        rows=[];authored=[]
        for source_row in verified["sourceFileRecords"]:
            raw,current=record(source,source_row["path"])
            require(current==source_row,"Authored source changed during preparation.")
            relative="source-geometry/"+source_row["path"];base.write_exclusive(output/relative,raw)
            rows.append({**source_row,"path":relative})
        for name in COMPONENTS:
            (output/(name+"-case")/"0").mkdir(parents=True)
            for relative,content in case_files(name).items():
                target=name+"-case/"+relative;base.write_exclusive(output/target,content.encode("ascii"));authored.append(record(output,target)[1])
            target=name+"-case/"+name+".msh";base.write_exclusive(output/target,record(source,name+".msh")[0]);authored.append(record(output,target)[1])
        report=declared_report(verified,sorted(authored,key=lambda r:r["path"]),rows)
        report.update(createdAt=datetime.now(timezone.utc).isoformat(),nativeCommandArguments=commands(output))
        manifest={"schemaVersion":MANIFEST_SCHEMA,"sourceGeometryRoute":SOURCE_ROUTE,"sourceGeometryReportSha256":report["sourceGeometryReportSha256"],
            "files":sorted(rows+authored,key=lambda r:r["path"]),"limits":LIMITS,"coordinateUnits":"SI_METRES","claimBoundary":CLAIM}
        base.write_exclusive(output/MANIFEST,encoded(manifest));report["nativeInputManifestSha256"]=record(output,MANIFEST)[1]["sha256"]
        require(validate_inputs(source)==verified,"The original authored source changed while being copied.")
        base.write_exclusive(output/REPORT,encoded(report));verify_output(output)
        return report
    except Exception:
        import shutil
        safety.scope_path(output)
        shutil.rmtree(output)
        raise


def verify_output(directory):
    output=safety.scope_path(directory);report,_=json_file(output,REPORT)
    verified=validate_inputs(output/"source-geometry")
    authored=[]
    for name in COMPONENTS:
        for relative,content in case_files(name).items():
            raw,row=record(output,name+"-case/"+relative);require(raw==content.encode("ascii"),"Authored native geometry dictionary changed.");authored.append(row)
        relative=name+"-case/"+name+".msh";raw,row=record(output,relative)
        require(raw==record(output/"source-geometry",name+".msh")[0],"Native input MSH changed from its authored source.");authored.append(row)
    source_rows=[{**row,"path":"source-geometry/"+row["path"]} for row in verified["sourceFileRecords"]]
    expected=declared_report(verified,sorted(authored,key=lambda r:r["path"]),source_rows)
    args=report.get("nativeCommandArguments")
    require(isinstance(args,list) and args and isinstance(args[0],list) and len(args[0])==4
        and isinstance(args[0][2],str) and args[0][2].endswith(("/impeller-case","\\impeller-case")),"A recorded geometry command root is required.")
    historical=native_geometry.historical_command_root(str(args[0][2])[:-len("/impeller-case")])
    require(args==_commands(historical),"Only the exact historical nine-command geometry recipe is admitted.")
    manifest,row=json_file(output,MANIFEST)
    expected_manifest={"schemaVersion":MANIFEST_SCHEMA,"sourceGeometryRoute":SOURCE_ROUTE,"sourceGeometryReportSha256":expected["sourceGeometryReportSha256"],
        "files":sorted(source_rows+authored,key=lambda r:r["path"]),"limits":LIMITS,"coordinateUnits":"SI_METRES","claimBoundary":CLAIM}
    require(manifest==expected_manifest,"The authored geometry input manifest changed.")
    expected.update(nativeInputManifestSha256=row["sha256"],nativeCommandArguments=args,createdAt=report.get("createdAt"))
    require(isinstance(report.get("createdAt"),str) and report==expected,"Preparation evidence must exactly replay all current authored bytes and claims.")
    return {"report":report,"reportFile":record(output,REPORT,limit=MAX_REPORT)[1],"components":verified["components"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--geometry-directory",required=True);parser.add_argument("--output-directory",required=True)
    args=parser.parse_args();result=prepare(args.geometry_directory,args.output_directory)
    print(json.dumps({"status":result["status"],"reportFile":REPORT,"actualCfdRunVerified":False,"mainApplicationWrites":False}))


if __name__=="__main__":main()
