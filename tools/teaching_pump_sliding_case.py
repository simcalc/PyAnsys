"""Prepare one conditional physical sliding pilot on authored SI classroom CAD.

Current active-model H0 is replayed, never inherited as a sliding result. Fresh
uniform state and physical motion inputs are authored; no process is launched.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import math
import os
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import teaching_pump_pressure_case as reference

phase, base, safety = reference.reference_case, reference.base, reference.safety
SCHEMA = "untirta.teaching-pump-sliding-pilot-preparation.v1"
REPORT = "teaching-sliding-preparation.json"
MODE = "physical-sliding-active-phase-pilot"
STATUS = "CLASSROOM_AUTHORED_SLIDING_PILOT_PREPARED_EXECUTION_REQUIRED"
ROUTE = "CLASSROOM_AUTHORED_GEOMETRY"
MESH_FILES, MODEL_FILES = phase.MESH_FILES, phase.MODEL_FILES
IDENTITY = reference.reference_runner.IDENTITY
PERIOD_S = 60 / 1450
MOTION_PLAN = {"axis":[0,0,1],"originM":[0,0,0],"speedRpm":1450.,"initialPhaseRad":0.,
    "rotationPeriodS":PERIOD_S,"angularVelocityRadS":2*math.pi*1450/60,
    "rotatingCellZone":"rotating-fluid","stationaryCellZone":"stationary-fluid","method":"solidBody/rotatingMotion"}
TEMPORAL_PLAN = {"initialTimeS":0.,"endTimeS":1.25*PERIOD_S,"maximumTimeStepS":PERIOD_S/360,
    "rotationPeriodS":PERIOD_S,"writeIntervalS":PERIOD_S/4,
    "expectedWriteTimesS":[n*PERIOD_S/4 for n in range(1,6)],"retainedPositiveWriteCount":5,
    "timeFormat":"general","timePrecision":17,"writePrecision":17,
    "timeNamePrecisionIsDeclaredInput":True,"storedNativeTimeRoundTripVerified":False,
    "nativeCoLimit":.5,"nativeAlphaCoLimit":.25,"adaptiveTimeStep":True,"stationaryMrfRelaxation":False,
    "physicalRotorMotion":True,"durationIsAcceptanceProof":False,"periodicStateVerified":False,
    "timeStepConvergenceVerified":False,"fixedGridClockVerified":False}
LIMITS = dict(reference.LIMITS)
MAX_FILE, MAX_REPORT, MAX_ACCEPTANCE, MAX_WORKSPACE = reference.MAX_FILE, reference.MAX_REPORT, reference.MAX_ACCEPTANCE, reference.MAX_WORKSPACE
FALSE_FLAGS = tuple(sorted(set(phase.FALSE_FLAGS) | {"slidingH0Qualified","periodicStateVerified","numericalTransientQualified",
    "aleGclVerified","wallNormalFluxVerified","dynamicInterfaceCoverageVerified","checkpointCreated","restartExecuted"}))
TRUE_SOURCE = ("sourceInputsImmutable","sourceActivePhaseH0NumericallyQualified")
TIME_NAME = r"(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?"
encoded, read, json_file = reference.encoded, reference.read, reference.json_file
PIN = "86e126a7bc4dbfaed949bbc58c9f0fdd67acc059"
MOTION_SOURCE = "https://github.com/OpenFOAM/OpenFOAM-12/blob/"+PIN+"/src/motionSolvers/displacement/solidBody/solidBodyMotionSolver/solidBodyMotionSolver.C"


class SlidingCaseIssue(ValueError): pass


def require(value, action):
    if not value: raise SlidingCaseIssue(action)


def verify_reference(directory): return reference.verify_reference(directory)


def native_recipe(study, default_faces=False):
    """Only explicit motion, wall velocity and pilot clock changes from active MRF."""
    authored = phase.native_recipe(study, default_faces=default_faces)
    require("constant/MRFProperties" in authored and "constant/dynamicMeshDict" not in authored,
        "Begin with the exact reviewed active-model teaching family.")
    del authored["constant/MRFProperties"]
    authored["constant/dynamicMeshDict"] = (base.foam_header("dynamicMeshDict",location="constant")
        + 'mover {type motionSolver; libs ("libfvMotionSolvers.so"); motionSolver solidBody; cellZone rotating-fluid; solidBodyMotionFunction rotatingMotion; origin (0 0 0); axis (0 0 1); omega 1450 [rpm];}\n').encode("ascii")
    velocity = authored["0/U"]
    for patch in sorted(base.ROTATING | base.INTERFACES):
        old=b'MRFnoSlip' if patch in base.ROTATING else b'slip'
        pattern = rb'("'+re.escape(patch.encode())+rb'"\s*\{)\s*type\s+'+old+rb'\s*;\s*\}'
        new = b'movingWallVelocity' if patch in base.ROTATING else b'movingWallSlipVelocity'
        velocity, count = re.subn(pattern,lambda m:m[1]+b'\n        type '+new+b'; value uniform (0 0 0);\n    }',velocity)
        require(count == 1,"Change only the exact original rotor/interface velocity patch: "+patch)
    authored["0/U"] = velocity
    control = authored["system/controlDict"]
    # TimeIO stores the actual value at maxPrecision_; fractional write names
    # need 17 significant digits to replay through the strict restart clock.
    # This is an authored input, not proof that a native state was written.
    for key,value in ((b"timeFormat",b"general"),(b"timePrecision",b"12"),(b"writePrecision",b"17")):
        require(len(re.findall(rb"\b"+key+rb"\b",control))==1
            and len(re.findall(rb"(?m)^"+key+b" "+value+rb";$",control))==1,
            "Replay the exact inherited timeFormat general/timePrecision12/writePrecision17 before the pilot name-precision override.")
    control=control.replace(b"timePrecision 12;",b"timePrecision 17;")
    for key, old, new in (("endTime",phase.DEFAULT_DURATION_S,TEMPORAL_PLAN["endTimeS"]),
        ("writeInterval",phase.DEFAULT_DURATION_S/4,TEMPORAL_PLAN["writeIntervalS"]),("purgeWrite",2,0)):
        previous=(key+" "+format(old,".17g")+";").encode(); replacement=(key+" "+format(new,".17g")+";").encode()
        require(control.count(previous)==1,"Replay exact previous clock before the explicit pilot override."); control=control.replace(previous,replacement)
    authored["system/controlDict"] = control
    require(not re.search(rb"\bMRFnoSlip\b",velocity) and "0/p" not in authored,
        "No MRF velocity or source pressure field may enter this physical sliding pilot.")
    return authored


def commands(directory):
    case=str(Path(directory).absolute()/"case")
    return [["checkMesh","-case",case,"-allGeometry","-allTopology","-writeSets"],["foamRun","-case",case]]


def historical_commands(root): return phase.historical_commands(root)


def output_capacity(source, authored):
    """Measured-reference projection, not an output-size or acceptance promise."""
    states={}
    for row in source["sourceRecords"]:
        match=re.fullmatch(r"case/("+TIME_NAME+r")/.+",row["path"])
        if match and 0<float(match[1])<=phase.DEFAULT_DURATION_S:
            states[match[1]]=states.get(match[1],0)+row["bytes"]
    require(states,"Actual retained positive-time reference state is required for the pilot capacity check.")
    mesh_bytes=sum(r["bytes"] for r in source["coupledMeshFiles"])
    source_bytes=sum(r["bytes"] for r in source["sourceRecords"])
    initial=source_bytes+mesh_bytes+source["geometryFile"]["bytes"]+sum(map(len,authored.values()))
    state=max(states.values())+mesh_bytes+phase.MAX_NATIVE_CACHE_BYTES
    reserve=2*LIMITS["maximumNativeLogBytes"]+4*MAX_REPORT
    projected=initial+5*state+reserve
    proof={"basis":"Actual retained active-reference state bytes plus six native moving-mesh primitives/cache allowance per planned write; full source copy, initial inputs and log/report reserve included",
        "referencePositiveTimeBytes":dict(sorted(states.items())),"sourceCopyBytes":source_bytes,
        "referenceMeshBytes":mesh_bytes,"initialPreparedBytesWithoutReport":initial,"perWriteProjectedBytes":state,
        "projectedFiveWriteBytes":projected,"maximumWorkspaceBytes":MAX_WORKSPACE,"plannedRetainedWriteCount":5,
        "projectionIsGuaranteedNativeBound":False,"nativeRuntimeWorkspaceGuardStillRequired":True,
        "capacityGatePassed":projected<MAX_WORKSPACE}
    require(proof["capacityGatePassed"],"TEACHING_SLIDING_PILOT_OUTPUT_CAPACITY_REQUIRED: measured five-write projection "+str(projected)+" reaches one-GiB bound; no automatic write removal or cap increase.")
    return proof


def declared_report(source,output):
    authored=native_recipe(source["study"],source["defaultFacesPresent"])
    rows=[{"path":p,"bytes":len(raw),"sha256":sha256(raw).hexdigest()} for p,raw in sorted(authored.items())]
    report={"schemaVersion":SCHEMA,"createdAt":datetime.now(timezone.utc).isoformat(),"status":STATUS,"mode":MODE,
        "sourceGeometryRoute":ROUTE,"coordinateUnits":"SI_METRES","foundationSourceCommit":PIN,
        "sourceEvidenceDirectory":"source-reference","sourceEvidenceFileRecords":[{**r,"path":"source-reference/"+r["path"]} for r in source["sourceRecords"]],
        "sourceReferenceAcceptanceFile":{**source["reportFile"],"path":"source-reference/"+source["reportFile"]["path"]},
        "sourceReferenceInputManifestSha256":source["manifestFile"]["sha256"],"sourceActivePhaseEvidenceSha256":source["packet"]["nativeObservation"]["nativeEvidence"]["sha256"],
        "sourceActivePhaseHeadM":source["packet"]["activePhaseReferenceHeadM"],"sourceReferencePressureAbsolutePa":300000.,
        "sourceGeometryIdentityFile":source["geometryFile"],"sourceGeometryControlSha256":source["study"]["controls"]["geometrySha256"],
        "outletProfileEvidence":phase.outlet.prefix_evidence(source["preparation"]["outletProfileEvidence"],"source-reference"),
        "sourceNativeMeshFiles":source["coupledMeshFiles"],"preservedNativeMeshFiles":[{**r,"path":r["path"].removeprefix("case/")} for r in source["coupledMeshFiles"]],
        "components":source["components"],"defaultFacesPresent":source["defaultFacesPresent"],"authoredInputFiles":rows,
        "authoredCavitationModelSha256":sha256(encoded([r for r in rows if r["path"] in MODEL_FILES])).hexdigest(),
        "declaredStudyInputs":source["study"]["input"],"physicalBasis":source["study"]["physicalBasis"],"sourceReference":source["study"]["sourceReference"],
        "inletAbsoluteTotalPressurePa":300000.,"absolutePressureMapping":phase.ABSOLUTE_PRESSURE_MAPPING,"motionPlan":MOTION_PLAN,"temporalPlan":TEMPORAL_PLAN,
        "outputCapacityPlan":output_capacity(source,authored),"limits":LIMITS,"nativeCommandArguments":commands(output),
        "sourceReferenceRole":"Current qualified fixed-MRF active-model H0 is provenance/prerequisite only, never a sliding H0 or inherited solution.",
        "initialization":"Fresh t0 liquid alpha.water1/U0/p_rgh300000Pa/k.375/omega100/nut0; no source result or restart state is copied into case.",
        "motionBasis":"rotatingMotion integrates omega from global t0; all native points incident to rotating-fluid cells follow +Z rigid rotation of constant reference points. Intake/volute physical noSlip remains; ALE normal flux is a separate actual audit.",
        "motionSource":MOTION_SOURCE,"claimBoundary":"One 1.25-revolution physical-sliding input pilot only. No executed motion, periodicity, sliding H0, time-step convergence, restart, NPSHr or physical validation is supplied.",
        **dict.fromkeys(TRUE_SOURCE,True),**dict.fromkeys(FALSE_FLAGS,False)}
    return report,authored


def generated_data(root,*,after_native_execution=False):
    """Only reviewed data roles after native work; never additional model inputs."""
    rows,evidence=[],[];root=Path(root);mesh=root/"case/constant/polyMesh"
    region_rows,region_evidence=phase.native_region_data(root,after_native_execution=after_native_execution)
    rows.extend(region_rows);evidence.extend(region_evidence)
    cache=root/phase.NATIVE_CACHE_PATH
    initial_counts=None;count_source=None
    if (root/"case/constant/fvMesh").exists():
        require(after_native_execution,"Native cache cannot be prefilled as a sliding input.")
        require({p.name for p in base.regular_directory(cache.parent).iterdir()}=={"polyFaces"},"Only native polyFaces cache data is supported.")
        raw,row=read(root,phase.NATIVE_CACHE_PATH,phase.MAX_NATIVE_CACHE_BYTES);proof=phase.native_polyfaces(raw,mesh)
        rows.append(row);evidence.append(proof);initial_counts={p["patch"]:p["nativeFaceCount"] for p in proof["nonconformalPatchMappings"]};count_source=row
    for folder in (root/"case").iterdir():
        if not re.fullmatch(TIME_NAME,folder.name) or float(folder.name)<=0:continue
        require(math.isfinite(float(folder.name)) and float(folder.name)<=TEMPORAL_PLAN["endTimeS"]+1e-12,"Bound generated native times to the pilot endpoint.")
        base.regular_directory(folder)
        address=folder/"fvMesh"
        if address.exists():
            require(after_native_execution and {p.name for p in base.regular_directory(address).iterdir()}=={"polyFaces"},"Only actual native time/fvMesh/polyFaces data is supported after execution.")
            relative="case/"+folder.name+"/fvMesh/polyFaces";raw,row=read(root,relative,phase.MAX_NATIVE_CACHE_BYTES)
            rows.append(row);evidence.append(dynamic_polyfaces(raw,mesh,folder.name,relative))
        if any(folder.iterdir()):
            require(after_native_execution,"Positive-time state cannot be prefilled as a pilot input.")
            data_rows,data_proofs=positive_time_data(root,folder,mesh)
            rows.extend(data_rows);evidence.extend(data_proofs)
    startup=[n for n in phase.STARTUP_FIELDS if (root/"case/0"/n).exists()]
    require(not startup or after_native_execution,"Startup results cannot be prefilled.")
    if startup:
        if initial_counts is None:
            relative="source-reference/"+phase.NATIVE_CACHE_PATH
            raw,count_source=read(root,relative,phase.MAX_NATIVE_CACHE_BYTES);proof=phase.native_polyfaces(raw,mesh)
            initial_counts={p["patch"]:p["nativeFaceCount"] for p in proof["nonconformalPatchMappings"]}
        owner_count,owners=base.native_list(mesh/"owner");neighbour_count,neighbours=base.native_list(mesh/"neighbour")
        tokens=owners.split()+neighbours.split();require(len(tokens)==owner_count+neighbour_count and all(re.fullmatch(r"\d+",n) for n in tokens),"Bind startup data to complete native cell labels.")
        cells=max(map(int,tokens))+1;require(0<cells<=500000,"Bound every initial native scalar cell.")
        patches=phase.geometry.boundary_entries(mesh/"boundary");control=read(root,"case/system/controlDict")[1]
        for name in sorted(startup):
            relative="case/0/"+name;raw,row=read(root,relative);proof=phase.startup_scalar_field(raw,name,cells,patches,ncc_counts=initial_counts)
            proof.update(path=relative,nativeRole="derived-startup-volScalarField",dataOnlyVerified=True,authoredInput=False,
                sourceControlFile=control,sourceFunctionObject=phase.STARTUP_FIELDS[name]["function"],resultField=name,
                initialAddressingCountSource=count_source,initialCountBasis="Exact six reference mesh bytes and zero initial physical angle; current source active H0 replay required",
                currentGeneratedAddressingClaim=False,numericalAcceptanceInherited=False)
            rows.append(row);evidence.append(proof)
    return sorted(rows,key=lambda r:r["path"]),sorted(evidence,key=lambda r:r["path"])


def native_text(raw,name,kind,location):
    text=re.sub(r"/\*.*?\*/|//[^\n]*","",raw.decode("ascii"),flags=re.S)
    require("#" not in text and "\x00" not in text and not re.search(r"\b(?:code|coded\w*|codeStream|codeExecute|codeInclude|codeOptions|codeLibs)\b",text),"Generated native state must be plain data, not executable input.")
    header=re.search(r"\bFoamFile\s*\{([^{}]*)\}",text,re.S);require(header and len(re.findall(r"\bFoamFile\b",text))==1,"Require one typed native data header.")
    entries=re.findall(r'([A-Za-z][A-Za-z0-9_]*)\s+("[^"\n]*"|[^;{}]+);',header[1]);keys=[p[0] for p in entries]
    require(len(keys)==len(set(keys)) and set(keys)<={'version','format','class','location','object','arch','note'}
        and not re.sub(r'[A-Za-z][A-Za-z0-9_]*\s+("[^"\n]*"|[^;{}]+);','',header[1]).strip(),"Unknown executable/duplicate native header roles are prohibited.")
    for key,value in (("format","ascii"),("class",kind),("object",name),("location",location)):
        entries=re.findall(r"\b"+key+r"\s+([^;{}]+);",header[1]);require(len(entries)==1 and entries[0].strip().strip('"')==value,"Native field header must match its exact role and actual time location.")
    return text,text[header.end():].strip()


def positive_time_data(root,folder,mesh):
    """Retain actual moved points and known auxiliary state as data-only evidence."""
    owner_count,owners=base.native_list(mesh/"owner");internal_count,neighbours=base.native_list(mesh/"neighbour");point_count,_=base.native_list(mesh/"points")
    labels=owners.split()+neighbours.split();require(len(labels)==owner_count+internal_count and labels and all(re.fullmatch(r"\d+",n) for n in labels),"Bind every generated field count to actual original cell labels.")
    cells=max(map(int,labels))+1;require(0<cells<=500000 and 0<point_count<=1000000,"Bound actual native field/point inventory.")
    dims=dict(reference.reference_runner.FIELD_DIMENSIONS)
    dims.update({"phi":"0 3 -1 0 0 0 0","meshPhi":"0 3 -1 0 0 0 0","alphaPhi.water":"0 3 -1 0 0 0 0",
        "Uf":"0 1 -1 0 0 0 0","rAU":"-1 3 1 0 0 0 0","rho":"1 -3 0 0 0 0","alpha.vapour":"0 0 0 0 0 0 0",
        "totalPressurePa":"1 -1 -2 0 0 0 0","U2":"0 2 -2 0 0 0 0","yPlus":"0 0 0 0 0 0 0","Vc0":"0 3 0 0 0 0 0"})
    vectors={"U","Uf"};surfaces={"phi","meshPhi","alphaPhi.water","Uf"};rows=[];proofs=[]
    for path in sorted(folder.rglob('*')):
        require(not path.is_symlink() and not getattr(path,'is_junction',lambda:False)() and (path.is_file() or path.is_dir()),"Native output must contain only ordinary data paths.")
        if path.is_dir():require(path.parent==folder and path.name in {'polyMesh','fvMesh','uniform'},"Unknown generated state directory.");continue
        local=path.relative_to(folder).as_posix();relative=path.relative_to(root).as_posix()
        if local=='fvMesh/polyFaces':continue  # Full mapped-label proof is separate.
        raw,row=read(root,relative);location=folder.name
        if local.startswith('polyMesh/'):
            name=path.name;require(name in MESH_FILES,"Only observed native points or unchanged six-role topology is allowed.")
            kind={'points':'vectorField','faces':'faceList','owner':'labelList','neighbour':'labelList','boundary':'polyBoundaryMesh','cellZones':'cellZoneList'}[name]
            _,body=native_text(raw,name,kind,location+'/polyMesh')
            if name=='points':
                match=re.fullmatch(r"(\d+)\s*\((.*)\)\s*",body,re.S);require(match and int(match[1])==point_count,"Moved points must retain every source native node.")
                values=re.findall(r"\(([^()]*)\)",match[2]);require(len(values)==point_count and not re.sub(r"\([^()]*\)","",match[2]).strip(),"Moved native point list shape changed.")
                finite_vectors(values)
                proof={'nativeRole':'moved-reference-topology-points','pointCount':point_count,'coordinateMotionVerified':False}
            else:
                reference_raw=read(root,'case/constant/polyMesh/'+name)[0]
                reference_text=re.sub(r"/\*.*?\*/|//[^\n]*","",reference_raw.decode('ascii'),flags=re.S)
                header=re.search(r"\bFoamFile\s*\{[^{}]*\}",reference_text,re.S);require(header,"Retain one original topology header.")
                require(body.split()==reference_text[header.end():].split(),"Generated topology cannot change original cells/faces/zone ordering or patch identities.")
                proof={'nativeRole':'unchanged-reference-topology','role':name,'nativeOrderingUnchanged':True}
        elif local=='uniform/time':
            _,body=native_text(raw,'time','dictionary',location+'/uniform')
            pairs=re.findall(r'\b(beginTime|value|name|index|deltaT|deltaT0)\s+([^;{}]+);',body)
            values={k:v.strip().strip('"') for k,v in pairs}
            require(len(pairs)==len(values)==6 and not re.sub(r'\b(beginTime|value|name|index|deltaT|deltaT0)\s+[^;{}]+;','',body).strip(),"Native adaptive clock may store only the six reviewed time state values.")
            require(values['name']==folder.name and re.fullmatch(r'\d+',values['index']) and all(re.fullmatch(phase.baseline.native.FLOAT,values[k]) and math.isfinite(float(values[k])) for k in ('beginTime','value','deltaT','deltaT0'))
                and float(values['beginTime'])==0 and math.isclose(float(values['value']),float(folder.name),rel_tol=5e-11,abs_tol=1e-12)
                and all(0<float(values[k])<=TEMPORAL_PLAN['maximumTimeStepS']*(1+1e-10) for k in ('deltaT','deltaT0')),"Native adaptive clock must match actual global time and authored step ceiling.")
            proof={'nativeRole':'adaptive-time-state','nativeTimeS':float(folder.name),'fixedGridVerified':False}
        else:
            name=path.name;require(path.parent==folder and name in dims,"Unknown generated native field role.")
            kind='volScalarField::Internal' if name=='Vc0' else 'surfaceVectorField' if name=='Uf' else 'volVectorField' if name=='U' else 'surfaceScalarField' if name in surfaces else 'volScalarField'
            text,_=native_text(raw,name,kind,location);dimension=re.findall(r'\bdimensions\s*\[([^\[\]]*)\]\s*;',text)
            # Foundation dimensionSet's ASCII writer uses [] for dimensionless
            # phase fractions/yPlus. This is role-specific, not a generic wildcard.
            require(len(dimension)==1 and (' '.join(dimension[0].split())==dims[name]
                or name in {'alpha.water','alpha.vapour','yPlus'} and not dimension[0].strip()),"Native state dimensions must match the exact SI role.")
            keyword='value' if name=='Vc0' else 'internalField'
            match=re.search(r'\b'+keyword+r'\s+(?:uniform\s+([^;]+)|nonuniform\s+List<(scalar|vector)>\s+(\d+)\s*\((.*?)\))\s*;',text,re.S)
            require(match,"Generated state needs complete native scalar/vector values.");expected=internal_count if name in surfaces else cells
            if match[1] is not None:stored=1;payload=match[1]
            else:
                stored=int(match[3]);payload=match[4];require(stored==expected and match[2]==('vector' if name in vectors else 'scalar'),"Native state must cover every actual internal cell/face.")
            if name in vectors:
                values=re.findall(r'\(([^()]*)\)',payload);require(len(values)==stored and not re.sub(r'\([^()]*\)','',payload).strip(),"Generated vector shape changed.");finite_vectors(values)
            else:
                values=payload.split();require(len(values)==stored and all(re.fullmatch(phase.baseline.native.FLOAT,n) and math.isfinite(float(n)) for n in values),"Every native internal scalar must remain finite.")
                if name in {'k','omega','nut','U2','yPlus'}:require(all(float(n)>=0 for n in values),"Native turbulence/energy data cannot be negative.")
                if name in {'rho','Vc0'}:require(all(float(n)>0 for n in values),"Native density/volume state must remain positive.")
                if name in {'alpha.water','alpha.vapour'}:require(all(-1e-9<=float(n)<=1+1e-9 for n in values),"Actual phase fractions must remain within [0,1] to the existing explicit 1e-9 numerical tolerance.")
            proof={'nativeRole':kind+'/'+name,'actualInternalElementCount':expected,'storedInternalElementCount':stored,'allStoredInternalValuesFinite':True}
            if name in {'alpha.water','alpha.vapour'}:proof['phaseFractionNumericalTolerance']=1e-9
        rows.append(row);proof.update(path=relative,dataOnlyVerified=True,authoredInput=False,checkpointCompletenessVerified=False,numericalAcceptanceInherited=False);proofs.append(proof)
    return rows,proofs


def finite_vectors(values):
    for value in values:
        tokens=value.split();require(len(tokens)==3 and all(re.fullmatch(phase.baseline.native.FLOAT,n) and math.isfinite(float(n)) for n in tokens),"Every actual native point/vector requires three finite components.")


def dynamic_polyfaces(raw,mesh,time_name,relative):
    """Validate actual time/fvMesh header and labels without changing any byte."""
    require(re.fullmatch(TIME_NAME,time_name) and 0<float(time_name)<=TEMPORAL_PLAN["endTimeS"]+1e-12
        and 0<len(raw)<=phase.MAX_NATIVE_CACHE_BYTES,"Bind bounded actual positive-time NCC data.")
    text=re.sub(r"/\*.*?\*/|//[^\n]*","",raw.decode("ascii"),flags=re.S)
    match=re.fullmatch(r"\s*FoamFile\s*\{([^{}]*)\}\s*dimensions\s*\[\s*(?:0\s+0\s+0\s+0\s+0\s+0\s+0\s*)?\]\s*;\s*internalField\s+uniform\s+-1\s*;\s*boundaryField\s*\{(.*)\}\s*",text,re.S)
    require(match,"Only plain complete native dimensionless label data is supported.")
    pairs=re.findall(r"([A-Za-z][A-Za-z0-9_]*)\s+([^;{}]+);",match[1]);header=dict(pairs)
    require(len(header)==len(pairs) and not re.sub(r"[A-Za-z][A-Za-z0-9_]*\s+[^;{}]+;","",match[1]).strip()
        and set(header) in ({"format","class","location","object"},{"version","format","class","location","object"})
        and {k:v.strip() for k,v in header.items() if k!="version"}=={"format":"ascii","class":"surfaceLabelField","location":'"'+time_name+'/fvMesh"',"object":"polyFaces"}
        and ("version" not in header or header["version"].strip()=="2.0"),"Keep the actual native location/object/class; no renaming or code dictionaries.")
    patches=phase.geometry.boundary_entries(mesh/"boundary");face_count,_=base.native_list(mesh/"owner");internal_count,_=base.native_list(mesh/"neighbour")
    blocks=list(re.finditer(r'"?([A-Za-z0-9_-]+)"?\s*\{([^{}]*)\}',match[2]))
    require(len(blocks)==len(patches) and len({b[1] for b in blocks})==len(blocks) and {b[1] for b in blocks}==set(patches)
        and not re.sub(r'"?[A-Za-z0-9_-]+"?\s*\{[^{}]*\}',"",match[2]).strip(),"Every actual patch requires its exact flat native label role.")
    checked=[];coupled=[];total=0
    for block in blocks:
        name,body=block[1],block[2]
        if name not in phase.NCC_PATCHES:
            require(re.fullmatch(r"\s*type\s+polyFaces\s*;\s*",body),"Physical native label ranges are implicit data only.");continue
        kind=phase.NCC_PATCHES[name];original=name.split('_on_',1)[1]
        require(patches[name]['type']==kind and original in patches and re.search(r'\boriginalPatch\s+'+re.escape(original)+r'\s*;',patches[name]['dictionary']),"Bind each actual cache to its source NCC role and originalPatch.")
        value=re.fullmatch(r"\s*type\s+nonConformalPolyFaces\s*;\s*patchType\s+"+kind+r"\s*;\s*size\s+(\d+)\s*;\s*value\s+(?:uniform\s+(\d+)|nonuniform\s+List<label>\s+(\d+)\s*\(([^()]*)\))\s*;\s*",body,re.S)
        require(value,"Only complete native mapped face-label data is accepted.");count=int(value[1]);require(count<=1000000,"Bound each actual NCC mapping.")
        tokens=[value[2]] if value[2] is not None else value[4].split()
        require((count>0 if value[2] is not None else int(value[3])==count==len(tokens)),"Store every actual native mapping entry.")
        low=patches[original]['startFace'];high=low+patches[original]['nFaces']
        require(internal_count<=low<high<=face_count and all(re.fullmatch(r"\d+",n) and low<=int(n)<high for n in tokens),"Actual mapped labels must belong to this exact original patch, not another interface.")
        total+=count;require(total<=2000000,"Bound aggregate native mappings.")
        if kind=='nonConformalCyclic':coupled.append(count)
        checked.append({"patch":name,"patchType":kind,"originalPatch":original,"originalFaceRange":[low,high],"nativeFaceCount":count,"allMappingLabelsChecked":True})
    require(len(coupled)==2 and coupled[0]==coupled[1]>0,"Actual reciprocal NCC caches require matched positive counts.")
    return {"path":relative,"nativeRole":"surfaceLabelField/polyFaces","dataOnlyVerified":True,"authoredInput":False,
        "nativeTimeS":float(time_name),"nativePatchCount":len(patches),"nativeIntersectionFaceCount":total,
        "nonconformalPatchMappings":checked,"geometryReconstructed":False,"dynamicCoverageVerified":False,"sourceUrl":phase.NATIVE_CACHE_SOURCE}


def verify_output(directory,*,after_native_execution=False):
    require(type(after_native_execution) is bool,"Declare the native verification stage explicitly.")
    root=safety.scope_path(directory);report,row=json_file(root,REPORT);source=verify_reference(root/"source-reference")
    expected,authored=declared_report(source,root)
    timestamp=report.get("createdAt")
    require(isinstance(timestamp,str) and len(timestamp)<=40,"Retain bounded UTC preparation provenance.")
    try:require(datetime.fromisoformat(timestamp).utcoffset().total_seconds()==0,"Preparation timestamp must be UTC.")
    except (ValueError,AttributeError) as error:raise SlidingCaseIssue("Preparation timestamp must be UTC.") from error
    expected["createdAt"]=timestamp;expected["nativeCommandArguments"]=historical_commands(phase.historical_command_root(report))
    require(report==expected,"Current copied H0/source/mesh/model/physical motion/clock inputs must replay exactly; no inherited result flags.")
    require(read(root,IDENTITY,MAX_REPORT)[1]==source["geometryFile"],"Keep the exact authored SI geometry identity.")
    for r in source["coupledMeshFiles"]:
        require(read(root,r["path"])[1]==r,"Constant reference mesh coordinates/topology must stay byte exact.")
    for name,raw in authored.items():require(read(root,"case/"+name)[0]==raw,"Authored sliding input bytes changed.")
    for folder in ("0","system","constant"):
        actual=base.regular_directory(root/"case"/folder);names={Path(p).name for p in authored if p.startswith(folder+"/")}
        if folder=="constant":
            names.add("polyMesh")
            if after_native_execution and (actual/"fvMesh").exists():names.add("fvMesh")
        if folder=="0" and after_native_execution:
            names|={n for n in phase.STARTUP_FIELDS if (actual/n).exists()}
            if (actual/"cellToRegion").exists():names.add("cellToRegion")
        require({p.name for p in actual.iterdir()}==names,"Unreviewed extra initial field or executable dictionary is prohibited.")
        for p in actual.iterdir():require(not p.is_symlink() and not getattr(p,"is_junction",lambda:False)()
            and (p.is_dir() if folder=="constant" and p.name in {"polyMesh","fvMesh"} else p.is_file()),"Use only ordinary reviewed input/data roles.")
    records,evidence=generated_data(root,after_native_execution=after_native_execution)
    require(not (root/"case/constant/MRFProperties").exists() and not (root/"case/0/p").exists(),"Do not combine MRF with physical motion or import source p.")
    return {"report":report,"reportFile":row,"sourceReference":source,"components":source["components"],
        "nativeGeneratedDataRecords":records,"nativeGeneratedDataEvidence":evidence}


def prepare(reference_directory,output_directory):
    source=verify_reference(reference_directory);output=safety.scope_path(output_directory,fresh=True)
    require(not output.resolve().is_relative_to(source["directory"].resolve()) and not source["directory"].resolve().is_relative_to(output.resolve()),"Prepare outside the complete immutable reference source.")
    report,authored=declared_report(source,output);raw_report=encoded(report);require(len(raw_report)<=MAX_REPORT,"Bound pilot preparation metadata to128KiB.")
    output.mkdir(parents=True)
    try:
        for row in source["sourceRecords"]:
            raw,current=read(source["directory"],row["path"],MAX_ACCEPTANCE if row["path"].endswith("-acceptance.json") else MAX_FILE)
            require(current==row,"Active reference evidence changed during its immutable full copy.");base.write_exclusive(output/"source-reference"/row["path"],raw)
        for row in source["coupledMeshFiles"]:
            raw,current=read(source["directory"],row["path"]);require(current==row,"Source coupled mesh changed during transfer.");base.write_exclusive(output/row["path"],raw)
        raw,current=read(source["directory"],IDENTITY,MAX_REPORT);require(current==source["geometryFile"],"Source authored geometry identity changed.");base.write_exclusive(output/IDENTITY,raw)
        for name,raw in authored.items():base.write_exclusive(output/"case"/name,raw)
        base.write_exclusive(output/REPORT,raw_report);verify_output(output)
        require(reference.source_inventory(source["directory"])==source["sourceRecords"],"Original active-reference bytes changed before publication.")
    except Exception:
        safety.scope_path(output);require(output.resolve().is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()),"Cleanup remains within the exact fresh runner output.");shutil.rmtree(output);raise
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--reference-directory",required=True);parser.add_argument("--output-directory",required=True)
    args=parser.parse_args()
    try:
        report=prepare(args.reference_directory,args.output_directory)
        print(__import__('json').dumps({"status":report["status"],"simulationExecuted":False,"physicalTransientExecuted":False,"slidingH0Qualified":False,"mainApplicationWrites":False}));return 0
    except Exception as error:
        print(__import__('json').dumps({"status":"TEACHING_SLIDING_INPUT_CORRECTION_REQUIRED","action":str(error)[:500],"simulationExecuted":False,"physicalTransientExecuted":False,"slidingH0Qualified":False,"mainApplicationWrites":False}));return 2


if __name__=="__main__":raise SystemExit(main())
