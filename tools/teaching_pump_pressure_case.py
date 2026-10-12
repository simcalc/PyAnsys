"""Prepare one pressure-only native point after a qualified active-phase H0.

The distinct authored classroom lane never changes the fixed300000Pa reference
API or supplies a calculated point, pressure bracket or NPSHr result.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
import teaching_pump_phase_case as reference_case
import reduce_teaching_pump_phase as reference_reduction
import run_teaching_pump_phase as reference_runner
import frozen_rotor_research_protocol as protocol

base,safety=reference_case.base,reference_case.safety
SCHEMA="untirta.teaching-pump-pressure-point-preparation.v1"
REPORT="teaching-pressure-preparation.json"
MODE="pressure-point-active-phase-MRF"
STATUS="CLASSROOM_AUTHORED_PRESSURE_POINT_PREPARED_EXECUTION_REQUIRED"
RUNTIME_SCHEMA="untirta.teaching-pump-pressure-runtime-identity.v1"
RUNTIME="teaching-pressure-runtime-identity.json"
ROUTE="CLASSROOM_AUTHORED_GEOMETRY"
MAX_SOURCE_RECORDS=320
MAX_FILE=128*1024**2
MAX_REPORT=128*1024
MAX_ACCEPTANCE=16*1024**2
MAX_WORKSPACE=1024**3
MESH_FILES=reference_case.MESH_FILES
MODEL_FILES=reference_case.MODEL_FILES
PRESSURE_SENTINEL=1.
FALSE_FLAGS=reference_case.FALSE_FLAGS
LIMITS={"maximumSourceRecords":MAX_SOURCE_RECORDS,"maximumFileBytes":MAX_FILE,"maximumReportBytes":MAX_REPORT,
    "maximumSourceAcceptanceBytes":MAX_ACCEPTANCE,"maximumWorkspaceBytes":MAX_WORKSPACE,
    "plannedNativeCpus":1,"plannedNativeMemoryBytes":4*1024**3,"plannedNativeMaximumSeconds":1200,"maximumNativeLogBytes":8*1024**2}
encoded=reference_case.encoded


class PressureCaseIssue(ValueError):pass


def require(value,action):
    if not value:raise PressureCaseIssue(action)


def read(root,path,limit=MAX_FILE):return reference_case.read(root,path,limit=limit)


def json_file(root,path,limit=MAX_REPORT):
    def pairs(rows):
        result={}
        for key,value in rows:require(key not in result,"Duplicate pressure evidence keys are prohibited.");result[key]=value
        return result
    raw,row=read(root,path,limit)
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(PressureCaseIssue("Finite pressure evidence JSON is required."))),row


def source_inventory(root):
    root=base.regular_directory(root);rows=[];total=0
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink() and not getattr(path,"is_junction",lambda:False)() and (path.is_file() or path.is_dir()),"Source reference evidence must contain ordinary owned paths.")
        if path.is_dir():continue
        relative=path.relative_to(root).as_posix()
        require(len(rows)<MAX_SOURCE_RECORDS and len(path.relative_to(root).parts)<=10 and all(not p.startswith(".") for p in path.relative_to(root).parts)
            and path.suffix.lower() not in {".py",".sh",".bat",".ps1",".exe",".dll",".so",".pem",".key",".env"},"Retain only bounded native/data reference roles, never scripts or credential files.")
        limit=MAX_ACCEPTANCE if path.name in {"teaching-phase-acceptance.json","teaching-baseline-acceptance.json"} else MAX_REPORT if path.suffix==".json" else MAX_FILE
        raw,row=read(root,relative,limit);total+=len(raw);require(total<=MAX_WORKSPACE,"The complete current active reference exceeds one GiB.");rows.append(row)
    require(rows,"A complete current active reference is required.");return rows


def verify_reference(directory):
    root=safety.scope_path(directory)
    packet,row=json_file(root,reference_reduction.REPORT_FILE,MAX_ACCEPTANCE)
    current=reference_reduction.reduce_phase(root)
    require(packet==current and packet.get("schemaVersion")==reference_reduction.SCHEMA and packet.get("sourceGeometryRoute")==ROUTE
        and packet.get("status")=="AuthoredActivePhaseReferenceNumericallyQualified"
        and all(packet.get(k) is True for k in ("actualCfdRunVerified","simulationExecuted","phaseObservationQualified","numericalPhaseReferenceQualified","phaseH0Eligible","interfaceCoverageVerified","endpointGlobalFieldTableConsistencyVerified"))
        and all(packet.get(k) is False for k in ("qualifiedBaseline","physicalValidationVerified","physicalTransientExecuted","npsh3Qualified","manufacturerPublishedGeometry","mainApplicationWrites"))
        and packet.get("diagnostics")==[],"Independently replay the actual qualified active-phase reference; no single-phase head or proposed point can supply H0.")
    study,observation=packet["study"],packet["nativeObservation"]
    reduced=protocol.reduce_run(study,observation)
    h0=protocol.number(packet.get("activePhaseReferenceHeadM"),positive=True)
    require(reduced==packet["protocolReduction"] and reduced["status"]=="QualifiedSuppliedObservation"
        and h0==reduced["stages"]["head"]["result"]["value"]
        and observation["physicalModel"]=="steady-cavitation-MRF"
        and observation["quality"]["maximumVaporFraction"]<=1e-4
        and observation["measurements"]["minimumStaticPressureAbsolutePa"]>study["input"]["vaporPressurePa"],"H0 must be the actual matched noncavitating active-model head.")
    verified=reference_case.verify_output(root,after_native_execution=True);prepared=verified["report"]
    require(prepared["inletAbsoluteTotalPressurePa"]==reference_case.PRESSURE_PA,"Retain the fixed original active reference pressure.")
    manifest,manifest_row=json_file(root,reference_runner.MANIFEST)
    runtime,_=json_file(root,reference_runner.RUNTIME)
    require(manifest==packet["manifest"] and manifest_row["sha256"]==packet["nativeInputManifestSha256"],"The current source phase manifest must remain byte-bound.")
    identity,identity_row=json_file(root,reference_runner.IDENTITY)
    mesh=[read(root,"case/constant/polyMesh/"+name)[1] for name in MESH_FILES]
    return {"directory":root,"packet":packet,"report":packet,"reportFile":row,"study":study,"manifest":manifest,"manifestFile":manifest_row,
        "runtime":runtime,"preparation":prepared,"components":verified["components"],"coupledMeshFiles":mesh,
        "geometryIdentity":identity,"geometryFile":identity_row,"sourceRecords":source_inventory(root),"defaultFacesPresent":prepared["defaultFacesPresent"]}


def pressure_value(value,reference_pressure=300000.):
    require(type(value) in (int,float) and math.isfinite(value) and reference_case.baseline_case.ASSUMPTIONS["vapourPressurePa"]<value<=reference_pressure,
        "Declare a finite absolute total pressure above the assumed vapour pressure and no higher than the actual active H0 reference.")
    return float(value)


def _pressure_field(study,pressure,default_faces):
    # The reviewed pure native writer is parameterized. Only its pressure field
    # is used; every other input comes from the current teaching wrapper.
    authored=reference_case.physics.native_recipe(study,pressure,"steady-sweep",reference_case.MAX_DT_S,reference_case.DEFAULT_DURATION_S)
    text=authored["0/p_rgh"]
    if not default_faces:
        text,count=re.subn(r'    "defaultFaces"\s*\{[^{}]*\}\s*',"",text);require(count==1,"Omit only the actually absent empty placeholder.")
    scalar=format(pressure,".17g")
    constraints="".join('    "'+patch+'"\n    {\n        type '+kind+'; value uniform '+scalar+';\n    }\n' for patch,kind in sorted(reference_case.NCC_PATCHES.items()))
    require(text.endswith("}\n"),"Use the complete reviewed pressure boundary field writer.")
    text=text[:-2]+constraints+"}\n"
    _,blocks=reference_case.baseline_case.fields.boundary_blocks(text.encode("ascii"),"p_rgh")
    require(set(blocks)==set().union(*base.PATCHES.values())|set(reference_case.NCC_PATCHES)|({"defaultFaces"} if default_faces else set()),"Every original physical/NCC patch must retain its pressure-only field role.")
    return text.encode("ascii")


def native_recipe(study,pressure_absolute_pa,*,default_faces=False):
    require(type(default_faces) is bool,"Use the actual optional zero-face placeholder presence.")
    pressure=pressure_value(pressure_absolute_pa)
    authored=reference_case.native_recipe(study,default_faces=default_faces)
    authored["0/p_rgh"]=_pressure_field(study,pressure,default_faces)
    require("0/p" not in authored and "constant/dynamicMeshDict" not in authored,"A pressure point cannot import gauge p or activate physical sliding.")
    return authored


def runtime_family(runtime):
    require(isinstance(runtime,dict) and set(runtime)=={"schemaVersion","openFoamFoundationVersion","packageVersion","officialDebSha256","expectedSourceCommit","binaries","mainApplicationWrites"}
        and runtime["schemaVersion"] in {reference_runner.RUNTIME_SCHEMA,RUNTIME_SCHEMA}
        and type(runtime["openFoamFoundationVersion"]) is int and runtime["openFoamFoundationVersion"]==12
        and runtime["packageVersion"]=="20250206" and runtime["officialDebSha256"]==safety.PACKAGE_SHA
        and runtime["expectedSourceCommit"]==safety.SOURCE_COMMIT and runtime["mainApplicationWrites"] is False,
        "Admit the exact actual phase/pressure runtime schemas and pinned native package before deriving a common runtime family.")
    rows=runtime["binaries"]
    require(isinstance(rows,list) and [r.get("program") for r in rows]==["checkMesh","foamRun"],"Bind exactly the two actual native utilities.")
    for row in rows:
        require(set(row)=={"program","path","bytes","sha256","packageOwnership"} and type(row["bytes"]) is int and 0<row["bytes"]<=MAX_FILE
            and protocol.digest(row["sha256"]) and isinstance(row["path"],str),"Actual native binary identities need typed bounded byte/SHA records.")
        path=Path(row["path"]).resolve()
        require(path.is_relative_to(Path("/opt/openfoam12")) and path.name==row["program"] and row["packageOwnership"]=="openfoam12: "+str(path),"Every utility must retain actual pinned-package ownership.")
        raw=base.read_regular(path,MAX_FILE);require(len(raw)==row["bytes"] and sha256(raw).hexdigest()==row["sha256"],"The actual native executable changed after source/reference binding.")
    return {key:runtime[key] for key in ("openFoamFoundationVersion","packageVersion","officialDebSha256","expectedSourceCommit","binaries")}


def family_rows(root,prep,runtime,*,source=False):
    require(type(source) is bool and prep.get("schemaVersion")== (reference_case.SCHEMA if source else SCHEMA),"Use the exact reference or pressure-point preparation schema for family derivation.")
    pressure=prep["inletAbsoluteTotalPressurePa"];study={"input":prep["declaredStudyInputs"],"physicalBasis":prep["physicalBasis"],"sourceReference":prep["sourceReference"],"controls":{}}
    authored=native_recipe(study,pressure,default_faces=prep["defaultFacesPresent"])
    declared=prep["authoredInputFiles"]
    require(declared==[{"path":p,"bytes":len(raw),"sha256":sha256(raw).hexdigest()} for p,raw in sorted(authored.items())],"Every pressure-point recipe byte must reconstruct independently before normalization.")
    groups={key:[] for key in protocol.CONTROL_KEYS};identity=read(root,reference_runner.IDENTITY,MAX_REPORT)[1];groups["geometrySha256"]=[identity]
    for row in prep["preservedNativeMeshFiles"]:
        raw,actual=read(root,"case/"+row["path"]);require(actual=={**row,"path":"case/"+row["path"]},"The actual source-bound coupled mesh bytes changed.")
        groups["meshSha256"].append(row)
    require({r["path"] for r in groups["meshSha256"]}=={"constant/polyMesh/"+p for p in MESH_FILES},"Bind exactly six current native mesh primitives.")
    canonical=_pressure_field(study,PRESSURE_SENTINEL,prep["defaultFacesPresent"])
    for row in declared:
        raw,actual=read(root,"case/"+row["path"]);require(actual=={**row,"path":"case/"+row["path"]} and raw==authored[row["path"]],"Current pressure/BC/model bytes must match the trusted authored recipe exactly.")
        key="cavitationModelSha256" if row["path"] in MODEL_FILES else "discretizationSha256" if row["path"].startswith("system/") else "boundarySha256"
        normalized=canonical if row["path"]=="0/p_rgh" else raw
        groups[key].append({"path":row["path"],"bytes":len(normalized),"sha256":sha256(normalized).hexdigest()})
    family=runtime_family(runtime);raw=encoded(family);groups["runtimeSha256"]=[{"path":"runtime-family.json","bytes":len(raw),"sha256":sha256(raw).hexdigest()}]
    groups={key:sorted(rows,key=lambda r:r["path"]) for key,rows in groups.items()};require(all(groups.values()),"Every immutable scientific control family requires actual rows.")
    proof={"method":"Complete trusted recipe replay; only declared pressure scalar slots in0/p_rgh use a structural sentinel",
        "nativePressureAbsolutePa":pressure,"structuralSentinelPa":PRESSURE_SENTINEL,"sentinelIsNativeInput":False,
        "nativeRelativeFile":"0/p_rgh","nativeFileSha256":sha256(authored["0/p_rgh"]).hexdigest(),"normalizedFileSha256":sha256(canonical).hexdigest(),
        "allOtherAuthoredInputBytesUnchanged":True,"runtimeNormalization":"Only document schema/role are excluded after actual package/binary byte verification; clocks and native inputs remain exact"}
    return {"groups":groups,"pressureNormalizationProof":proof,"runtimeFamily":family}


def reference_family(reference):return family_rows(reference["directory"],reference["preparation"],reference["runtime"],source=True)


def commands(directory):return reference_runner.commands(directory)


def historical_commands(command_root):return reference_case.historical_commands(command_root)


def prepare(reference_directory,output_directory,*,pressure_absolute_pa):
    source=safety.scope_path(reference_directory);output=safety.scope_path(output_directory,fresh=True);verified=verify_reference(source)
    pressure=pressure_value(pressure_absolute_pa,verified["preparation"]["inletAbsoluteTotalPressurePa"])
    authored=native_recipe(verified["study"],pressure,default_faces=verified["defaultFacesPresent"])
    output.mkdir(parents=True)
    try:
        source_rows=[]
        for row in verified["sourceRecords"]:
            raw,current=read(source,row["path"],MAX_ACCEPTANCE if row["path"].endswith("-acceptance.json") else MAX_FILE)
            require(current==row,"Actual active reference changed while copying.");target="source-reference/"+row["path"];base.write_exclusive(output/target,raw);source_rows.append({**row,"path":target})
        base.write_exclusive(output/reference_runner.IDENTITY,read(source,reference_runner.IDENTITY,MAX_REPORT)[0])
        mesh_rows=[]
        for row in verified["coupledMeshFiles"]:
            raw=read(source,row["path"])[0];base.write_exclusive(output/row["path"],raw);mesh_rows.append({**row,"path":row["path"].removeprefix("case/")})
        authored_rows=[]
        for path,raw in sorted(authored.items()):base.write_exclusive(output/"case"/path,raw);authored_rows.append({"path":path,"bytes":len(raw),"sha256":sha256(raw).hexdigest()})
        model=[r for r in authored_rows if r["path"] in MODEL_FILES]
        report={"schemaVersion":SCHEMA,"createdAt":datetime.now(timezone.utc).isoformat(),"status":STATUS,"mode":MODE,"sourceGeometryRoute":ROUTE,
            "sourceEvidenceDirectory":"source-reference","sourceEvidenceFileRecords":source_rows,
            "sourceReferenceAcceptanceFile":{**verified["reportFile"],"path":"source-reference/"+verified["reportFile"]["path"]},
            "sourceReferenceInputManifestSha256":verified["manifestFile"]["sha256"],"sourceActivePhaseEvidenceSha256":verified["packet"]["nativeObservation"]["nativeEvidence"]["sha256"],
            "sourceActivePhaseHeadM":verified["packet"]["activePhaseReferenceHeadM"],"sourceReferencePressureAbsolutePa":verified["preparation"]["inletAbsoluteTotalPressurePa"],
            "sourceGeometryIdentityFile":verified["geometryFile"],"sourceNativeMeshFiles":verified["coupledMeshFiles"],"preservedNativeMeshFiles":mesh_rows,
            "authoredInputFiles":authored_rows,"authoredCavitationModelSha256":sha256(encoded(model)).hexdigest(),"defaultFacesPresent":verified["defaultFacesPresent"],
            "components":verified["components"],"declaredStudyInputs":verified["study"]["input"],"physicalBasis":verified["study"]["physicalBasis"],"sourceReference":verified["study"]["sourceReference"],
            "temporalPlan":reference_case.TEMPORAL_PLAN,"pressureAbsolutePa":pressure,"inletAbsoluteTotalPressurePa":pressure,"absolutePressureMapping":reference_case.ABSOLUTE_PRESSURE_MAPPING,
            "nativeCommandArguments":commands(output),"limits":LIMITS,"sourceInputsImmutable":True,"sourceActivePhaseH0NumericallyQualified":True,
            "claimBoundary":"One new pressure-only input on the exact current authored SI active-model family; H0 is an independently replayed native reference. No point result, bracket, NPSHr, moving mesh or restart is supplied.",**{k:False for k in FALSE_FLAGS}}
        # Mapping pressure labels follow this new point without adding a gauge reference.
        report["absolutePressureMapping"]={**reference_case.ABSOLUTE_PRESSURE_MAPPING,"boundary":"prghTotalPressure p0="+format(pressure,".17g")+" Pa absolute inlet total pressure"}
        require(len(encoded(report))<=MAX_REPORT and source_inventory(source)==verified["sourceRecords"],"The current reference/source preparation report must remain bounded and immutable.")
        base.write_exclusive(output/REPORT,encoded(report));verify_output(output);return report
    except Exception:
        safety.scope_path(output);shutil.rmtree(output);raise


def verify_output(directory,*,after_native_execution=False):
    require(type(after_native_execution) is bool,"Declare the native verification stage explicitly.")
    root=safety.scope_path(directory);report,row=json_file(root,REPORT);source=verify_reference(root/"source-reference")
    pressure=pressure_value(report.get("pressureAbsolutePa"),source["preparation"]["inletAbsoluteTotalPressurePa"])
    require(report.get("schemaVersion")==SCHEMA and report.get("mode")==MODE and report.get("status")==STATUS and report.get("sourceGeometryRoute")==ROUTE
        and report.get("limits")==LIMITS and report.get("inletAbsoluteTotalPressurePa")==pressure
        and all(report.get(k) is False for k in FALSE_FLAGS) and report.get("sourceInputsImmutable") is True and report.get("sourceActivePhaseH0NumericallyQualified") is True,
        "Replay the distinct pressure-only input schema without inherited point or NPSH qualification.")
    require(report.get("sourceEvidenceFileRecords")==[{**r,"path":"source-reference/"+r["path"]} for r in source["sourceRecords"]]
        and report.get("sourceReferenceAcceptanceFile")=={**source["reportFile"],"path":"source-reference/"+source["reportFile"]["path"]}
        and report.get("sourceReferenceInputManifestSha256")==source["manifestFile"]["sha256"]
        and report.get("sourceActivePhaseEvidenceSha256")==source["packet"]["nativeObservation"]["nativeEvidence"]["sha256"]
        and report.get("sourceActivePhaseHeadM")==source["packet"]["activePhaseReferenceHeadM"] and report.get("sourceReferencePressureAbsolutePa")==source["preparation"]["inletAbsoluteTotalPressurePa"]
        and report.get("sourceGeometryIdentityFile")==source["geometryFile"] and read(root,reference_runner.IDENTITY,MAX_REPORT)[1]==source["geometryFile"],"All source active H0/mesh/identity evidence must remain actual and bound.")
    require(report.get("components")==source["components"] and report.get("defaultFacesPresent")==source["defaultFacesPresent"] and report.get("sourceNativeMeshFiles")==source["coupledMeshFiles"]
        and report.get("declaredStudyInputs")==source["study"]["input"] and report.get("physicalBasis")==source["study"]["physicalBasis"] and report.get("sourceReference")==source["study"]["sourceReference"]
        and report.get("temporalPlan")==reference_case.TEMPORAL_PLAN,"Geometry, flow, RPM, properties, rotation, datum and all clocks must remain the exact active reference family.")
    mesh=[{**r,"path":r["path"].removeprefix("case/")} for r in source["coupledMeshFiles"]]
    require(report.get("preservedNativeMeshFiles")==mesh and all(read(root,"case/"+r["path"])[1]=={**r,"path":"case/"+r["path"]} for r in mesh),"The exact coupled source mesh must remain byte-identical.")
    authored=native_recipe(source["study"],pressure,default_faces=source["defaultFacesPresent"])
    rows=[{"path":p,"bytes":len(raw),"sha256":sha256(raw).hexdigest()} for p,raw in sorted(authored.items())]
    require(report.get("authoredInputFiles")==rows and all(read(root,"case/"+p)[0]==raw for p,raw in authored.items())
        and report.get("authoredCavitationModelSha256")==sha256(encoded([r for r in rows if r["path"] in MODEL_FILES])).hexdigest(),"Only the trusted declared pressure slots may vary; every other current model/BC/solver byte is exact.")
    require(report.get("absolutePressureMapping")=={**reference_case.ABSOLUTE_PRESSURE_MAPPING,"boundary":"prghTotalPressure p0="+format(pressure,".17g")+" Pa absolute inlet total pressure"},"Pressure remains absolute Pa/g0 with no atmospheric addition.")
    records,evidence=reference_case.verify_case_input_roles(root,authored,after_native_execution=after_native_execution)
    command_root=reference_case.historical_command_root(report);require(report["nativeCommandArguments"]==historical_commands(command_root),"Historical commands describe only the exact current two-command role.")
    return {"report":report,"reportFile":row,"sourceReference":source,"sourceReferencePacket":source,"components":source["components"],"nativeGeneratedDataRecords":records,"nativeGeneratedDataEvidence":evidence}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--reference-directory",required=True);parser.add_argument("--output-directory",required=True);parser.add_argument("--pressure-absolute-pa",type=float,required=True)
    args=parser.parse_args()
    report=prepare(args.reference_directory,args.output_directory,pressure_absolute_pa=args.pressure_absolute_pa)
    print(json.dumps({"status":report["status"],"pressureAbsolutePa":report["pressureAbsolutePa"],"simulationExecuted":False,"npsh3Qualified":False,"mainApplicationWrites":False}))


if __name__=="__main__":main()
