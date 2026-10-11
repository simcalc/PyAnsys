"""Read actual fixed-clock and full-field restart evidence; never launch CFD.

The native raw fields stay immutable on disk. Head time-step GCI, full native
restart agreement, spatial refinement and NPSH3 are independent conclusions.
Synthetic tests exercise this reader and are never solver research evidence.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import importlib
import itertools
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_cavitation_case as case
import frozen_rotor_native_reduction as native
import frozen_rotor_phase_reduction as phase
import frozen_rotor_research_protocol as protocol

SCHEMA = "untirta.frozen-rotor-stage4-input-manifest.v1"
REDUCTION_SCHEMA = "untirta.frozen-rotor-stage4-campaign-reduction.v1"
MAX_LOG = 32 * 1024**2
MAX_REDUCTION = 16 * 1024**2
MAX_CELLS = 2_000_000  # Independent OpenFOAM worker bound, never a Student licence claim.
MAX_POINTS = 2_000_000
encoded, require = native.encoded, native.require
TIME_NAME = phase.TIME_NAME
FLOAT = native.FLOAT
MODEL_FILES = phase.MODEL_FILES


def stage4():
    return importlib.import_module("frozen_rotor_stage4_case")


def build_manifest(branch_directory, prepared=None):
    """Return exact {study,manifest}; caller persists it before native startup."""
    root = case.base.regular_directory(branch_directory)
    prep, prep_row = native.record(root, "stage4-branch-preparation.json")
    variant=prep.get("variant",{})
    if prepared is not None:
        temporal_keys=("id","casePath","timeStepS","initialTimeS","endTimeS","writeIntervalSteps","mode","commonWindowS")
        require(all(prepared.get(key)==variant.get(key) for key in temporal_keys),"CFD_CURRENT_STAGE4_VARIANT_REQUIRED")
        require(all(prepared.get(key)==prep.get(key) for key in ("authoredInputFiles","referenceMeshFiles","controls")),
                "CFD_CURRENT_STAGE4_BRANCH_PREPARATION_REQUIRED")
    require(prep.get("schemaVersion") == "untirta.frozen-rotor-stage4-branch-preparation.v1"
            and prep.get("mainApplicationWrites") is False
            and protocol.digest(prep.get("sourceBaselineEvidenceSha256"))
            and protocol.digest(prep.get("sourceCaseManifestSha256")), "CFD_PINNED_STAGE4_PREPARATION_REQUIRED")
    declared = prep.get("declaredStudyInputs")
    initial = protocol.prepare_study(inputs=declared, gravity_in_solver=False)
    require(declared == initial["input"] and prep.get("physicalBasis") == initial["physicalBasis"],
            "CFD_EXACT_STAGE4_CLASSROOM_BASIS_REQUIRED")
    pressure = protocol.number(prep.get("pressureLevelAbsolutePa"), positive=True)
    dt = protocol.number(variant.get("timeStepS"), positive=True)
    start = protocol.number(variant.get("initialTimeS"), nonnegative=True)
    end = protocol.number(variant.get("endTimeS"), positive=True)
    period = 60 / declared["speedRpm"]
    steps = (end-start)/dt
    require(variant.get("mode") == "transient-sliding" and prep.get("fixedClock") is True
            and prep.get("adaptiveTimeStepping") is False and end > start
            and dt <= period/360*(1+1e-12) and 1 <= round(steps) <= 100_000
            and math.isclose(steps, round(steps), rel_tol=0, abs_tol=1e-7)
            and math.isclose(start/dt, round(start/dt), rel_tol=0, abs_tol=1e-7),
            "CFD_ALIGNED_BOUNDED_FIXED_STAGE4_CLOCK_REQUIRED")
    interval = variant.get("writeIntervalSteps")
    require(isinstance(interval,int) and not isinstance(interval,bool) and interval > 0,
            "CFD_EXACT_STAGE4_WRITE_INTERVAL_REQUIRED")
    recipe = stage4().fixed_recipe(initial, pressure, dt, end, initial_s=start, write_interval_steps=interval)
    # Authored fields contain the exact official constraint include. Full byte
    # comparison to our fixed recipe, rather than a generic state-file allowlist,
    # admits solely that reviewed include and those reviewed function libraries.
    rows = case.file_records(root/"case", prep.get("authoredInputFiles"), set(recipe))
    require({row["path"] for row in rows} == set(recipe), "CFD_COMPLETE_STAGE4_AUTHORED_INPUTS_REQUIRED")
    for name, content in recipe.items():
        require(native.read(root,"case/"+name,native.MAX_FIELD)[0] == content.encode("ascii"),
                "CFD_EXACT_FIXED_STAGE4_RECIPE_REQUIRED:"+name)
    paths = {"constant/polyMesh/"+name for name in case.MESH_FILES}
    mesh_rows = case.file_records(root/"case",prep.get("referenceMeshFiles"),paths,plain_native=True)
    require({row["path"] for row in mesh_rows} == paths, "CFD_COMPLETE_STAGE4_REFERENCE_MESH_REQUIRED")
    identity, geometry_row = native.record(root,"geometry-identity.json")
    source = prep.get("sourceGeometryIdentityFile",{})
    require(identity.get("schemaVersion") == "untirta.frozen-rotor-fixed-geometry-identity.v1"
            and identity.get("exampleDataCommit") == case.base.inventory.PIN
            and identity.get("mainApplicationWrites") is False
            and source.get("path") == "geometry-identity.json" and source.get("bytes") == geometry_row["bytes"]
            and source.get("sha256") == geometry_row["sha256"], "CFD_EXACT_STAGE4_SOURCE_GEOMETRY_REQUIRED")
    require(prep.get("sourceGeometryControlSha256") == sha256(encoded([geometry_row])).hexdigest(),
            "CFD_STAGE4_GEOMETRY_CONTROL_MISMATCH")
    runtime, runtime_row = native.record(root,"stage4-runtime-identity.json")
    phase.phase_binary_identity(runtime)
    groups = stage4().control_groups(initial,pressure,mesh_rows,geometry_row,runtime_row)
    require(set(groups) == set(protocol.CONTROL_KEYS), "CFD_COMPLETE_STAGE4_STRUCTURAL_GROUPS_REQUIRED")
    groups = {key: sorted(values,key=lambda row:row["path"]) for key,values in groups.items()}
    controls = {key:sha256(encoded(values)).hexdigest() for key,values in groups.items()}
    require(prep.get("controls") == controls and prep.get("authoredCavitationModelSha256") == controls["cavitationModelSha256"],
            "CFD_ACTUAL_STAGE4_STRUCTURAL_CONTROLS_REQUIRED")
    study = protocol.prepare_study(inputs=declared,controls=controls,gravity_in_solver=False)
    require(prep.get("study") == study, "CFD_CURRENT_STAGE4_STUDY_REQUIRED")
    files = [{**prep_row,"control":None},{**geometry_row,"control":"geometrySha256"},{**runtime_row,"control":"runtimeSha256"}]
    files += [{**row,"path":"case/"+row["path"],"control":"meshSha256"} for row in mesh_rows]
    for row in rows:
        key = "cavitationModelSha256" if row["path"] in MODEL_FILES else "discretizationSha256" if row["path"].startswith("system/") else "boundarySha256"
        files.append({**row,"path":"case/"+row["path"],"control":key})
    window = variant.get("commonWindowS")
    require(isinstance(window,list) and len(window)==2 and all(isinstance(x,(int,float)) and math.isfinite(x) for x in window)
            and window[0] >= 0 and math.isclose(window[1],end,abs_tol=1e-12)
            and math.isclose(window[1]-window[0],3*period,abs_tol=1e-12), "CFD_EXACT_COMMON_THREE_PERIOD_WINDOW_REQUIRED")
    manifest = {"schemaVersion":SCHEMA,"files":sorted(files,key=lambda row:row["path"]),"controls":controls,
        "structuralSignatureRows":groups,"signatureScope":study["signatureScope"],"studyInputs":study["input"],
        "physicalBasis":study["physicalBasis"],"samplingDefinition":study["samplingDefinition"],
        "physicalModel":"transient-cavitation-sliding-mesh","mode":"transient-sliding","pressureLevelAbsolutePa":pressure,
        "temporalPlan":{"initialTimeS":start,"endTimeS":end,"physicalTimeStepS":dt,"rotationPeriodS":period,
                        "nativeCoLimit":.5,"nativeAlphaCoLimit":.25,"adjustTimeStep":False},
        "commonWindowS":window,"preparationReportSha256":prep_row["sha256"],"runtimeIdentitySha256":runtime_row["sha256"],
        "sourceBaselineEvidenceSha256":prep["sourceBaselineEvidenceSha256"],"inletElevationM":0.,"outletElevationM":0.,
        "sourceBaselineRole":"Single-phase prerequisite only; never an active-phase H0 reference.",
        "nativeCoordinateUnitsVerified":False,"classroomMetresPerSourceCoordinateUnit":1,
        "elevationBasis":"Explicit classroom common zero datum; native cut centroids remain unverified.",
        "classroomAcceptance":{"continuityLocalLimit":1e-4,"interfaceMinimumCoverage":.99,"interfaceAverageCoverage":.999,
            "interfaceMaximumCoverage":1.01,"interfaceFluxRelativeLimit":.005,"nativeCoLimit":.5,"nativeAlphaCoLimit":.25},
        "structuralSignatureMethod":"Full actual files bound first. Signature-only pressure=1 Pa, dt=one degree, end=5.5 revolutions, start=0, write interval=990 excludes only declared pressure/time controls.",
        "simulationExecuted":False,"mainApplicationWrites":False}
    return {"study":study,"manifest":manifest}


def fixed_solver_history(text, prepared):
    """Actual fixed clock, then first initial residual per native PIMPLE outer."""
    import run_stage4_campaign as runner
    evidence = runner.fixed_clock(text,prepared)
    require(evidence["courantCriteriaSatisfied"] and re.search(r"(?m)^End\s*$",text)
            and re.search(r"(?m)^Selecting cavitation model SchnerrSauer\s*$",text)
            and not re.search(r"FOAM FATAL|Segmentation fault|Floating point exception",text,re.I),
            "CFD_COMPLETED_FIXED_PHASE_CLOCK_REQUIRED")
    rows,current = {},None
    for line in text.splitlines():
        line=line.strip()
        match=re.fullmatch(r"Time = ("+FLOAT+r")s?",line)
        if match:
            current={"outer":[{}],"continuity":[],"coverage":[]};rows[float(match[1])]=current
        if current is None:
            continue
        if re.fullmatch(r"PIMPLE: iteration [1-9]\d*",line) and current["outer"][-1]:
            current["outer"].append({})
        match=re.search(r"Solving for (p_rgh|Ux|Uy|Uz|k|omega), Initial residual = ("+FLOAT+r"), Final residual = ("+FLOAT+r"), No Iterations (\d+)",line)
        if match:
            initial,final=[protocol.number(float(match[i]),nonnegative=True) for i in (2,3)]
            require(initial<=1 and final<=1,"CFD_FINITE_NORMALIZED_STAGE4_RESIDUAL_REQUIRED")
            current["outer"][-1].setdefault(match[1],initial)
        elif re.search(r"Solving for (p_rgh|Ux|Uy|Uz|k|omega),",line):
            require(False,"CFD_ACTUAL_INITIAL_STAGE4_RESIDUAL_REQUIRED")
        match=re.search(r"time step continuity errors : sum local = ("+FLOAT+r"), global = ("+FLOAT+r")(?:, cumulative = ("+FLOAT+r"))?",line)
        if match:
            values=[protocol.number(float(x)) for x in match.groups() if x is not None]
            require(values[0]>=0,"CFD_NONNEGATIVE_STAGE4_CONTINUITY_REQUIRED")
            current["continuity"].append(values)
        current["coverage"].append(line)
    return rows,evidence


def verified_sampling_clock(table,prepared):
    """Normalize only numeric spelling/roundoff on an already verified fixed grid."""
    dt,start,end=prepared["timeStepS"],prepared["initialTimeS"],prepared["endTimeS"]
    steps=round((end-start)/dt);times=list(table)
    initial=bool(times and math.isclose(times[0],start,abs_tol=1e-12,rel_tol=0))
    require(len(times)==steps+int(initial),"CFD_COMPLETE_ACTUAL_FIXED_SAMPLING_CLOCK_REQUIRED")
    result={}
    for index,instant in enumerate(times):
        step=index if initial else index+1
        target=start+step*dt
        if step==steps:target=end
        require(math.isclose(instant,target,rel_tol=0,abs_tol=max(1e-12,dt*1e-7)),"CFD_ACTUAL_SAMPLER_FIXED_GRID_MISMATCH")
        result[target]=table[instant]
    return result


def scalar_vector_stream(raw, name, count, *, points=False):
    """Bounded raw ASCII reader yields every component without a Python value list."""
    require(isinstance(count,int) and not isinstance(count,bool) and 0<count<=(MAX_POINTS if points else MAX_CELLS),
            "CFD_BOUNDED_COMPLETE_NATIVE_VALUE_COUNT_REQUIRED")
    text = re.sub(r"/\*.*?\*/|//[^\n]*","",raw.decode("ascii"),flags=re.S)
    require(not re.search(r"\b(?:nan|inf|infinity)\b|^\s*#|\bcodeStream\b",text,re.I|re.M)
            and re.search(r"\bformat\s+ascii\s*;",text)
            and re.search(r"\bobject\s+"+re.escape(name)+r"\s*;",text), "CFD_FINITE_NONEXECUTABLE_NATIVE_FIELD_REQUIRED")
    # Reject overflow anywhere, including boundary numeric literals, before a result.
    require(all(math.isfinite(float(m[0])) for m in re.finditer(FLOAT,text)),"CFD_FINITE_ALL_NATIVE_NUMERIC_LITERALS_REQUIRED")
    vector=points or name=="U"
    if points:
        require(re.search(r"\bclass\s+vectorField\s*;",text),"CFD_NATIVE_POINT_CLASS_REQUIRED")
        header_end=text.find("}")
        match=re.fullmatch(r"\s*(\d+)\s*\((.*)\)\s*",text[header_end+1:],re.S)
        require(match is not None and int(match[1])==count,"CFD_COMPLETE_NATIVE_POINT_ORDER_REQUIRED")
        body,uniform=match[2],False
    else:
        dimensions={"p":r"1\s+-1\s+-2\s+0\s+0\s+0\s+0","U":r"0\s+1\s+-1\s+0\s+0\s+0\s+0","alpha.water":r"0\s+0\s+0\s+0\s+0\s+0\s+0"}
        require(name in dimensions and re.search(r"\bclass\s+"+("volVectorField" if vector else "volScalarField")+r"\s*;",text)
                and re.search(r"\bdimensions\s*\[\s*"+dimensions[name]+r"\s*\]\s*;",text),"CFD_EXACT_NATIVE_RESTART_FIELD_UNITS_REQUIRED")
        match=re.search(r"\binternalField\s+(uniform\s+([^;]+)|nonuniform\s+List<(scalar|vector)>\s+(\d+)\s*\((.*?)\))\s*;",text,re.S)
        require(match is not None,"CFD_COMPLETE_NATIVE_INTERNAL_FIELD_REQUIRED")
        uniform=match[2] is not None;body=match[2] if uniform else match[5]
        require(uniform or (int(match[4])==count and match[3]==("vector" if vector else "scalar")),"CFD_EXACT_NATIVE_RESTART_FIELD_ORDER_REQUIRED")
    def tokens():
        expected=(3 if vector else 1)*(1 if uniform else count)
        position,numbers,depth=0,0,0
        for match in re.finditer(r"[()]|"+FLOAT,body):
            require(not body[position:match.start()].strip(),"CFD_NATIVE_VALUE_LIST_SYNTAX_REQUIRED")
            token=match[0];position=match.end()
            if token=="(":
                require(vector and depth==0 and numbers%3==0,"CFD_NATIVE_VECTOR_SHAPE_REQUIRED");depth=1
            elif token==")":
                require(vector and depth==1 and numbers%3==0,"CFD_NATIVE_VECTOR_SHAPE_REQUIRED");depth=0
            else:
                require((depth==1)==vector,"CFD_NATIVE_SCALAR_VECTOR_SHAPE_REQUIRED")
                value=protocol.number(float(token));numbers+=1
                require(numbers<=expected,"CFD_COMPLETE_NATIVE_VALUE_COUNT_REQUIRED")
                if name=="alpha.water":
                    require(-1e-9<=value<=1+1e-9,"CFD_NATIVE_RESTART_PHASE_BOUNDS_REQUIRED")
                yield value
        require(not body[position:].strip() and depth==0 and numbers==expected,"CFD_COMPLETE_NATIVE_VALUE_COUNT_REQUIRED")
    if uniform:
        values=tuple(tokens())
        return itertools.chain.from_iterable(values for _ in range(count))
    return tokens()


def compare_native_values(left_raw,right_raw,name,count,tolerance,*,points=False):
    """Full same-order L-infinity and RMS; the raw complete fields remain evidence."""
    tolerance=protocol.number(tolerance,positive=True)
    total,maximum,n=0.,0.,0
    sentinel=object()
    for a,b in itertools.zip_longest(scalar_vector_stream(left_raw,name,count,points=points),
                                     scalar_vector_stream(right_raw,name,count,points=points),fillvalue=sentinel):
        require(a is not sentinel and b is not sentinel,"CFD_SAME_COMPLETE_NATIVE_FIELD_SHAPE_REQUIRED")
        delta=abs(a-b);maximum=max(maximum,delta);total+=delta*delta;n+=1
    require(n==count*(3 if points or name=="U" else 1) and math.isfinite(total),"CFD_FINITE_FULL_NATIVE_DIFFERENCES_REQUIRED")
    unit="m (assumed native scale)" if points else "Pa" if name=="p" else "m/s" if name=="U" else "1"
    return {"criterionSatisfied":maximum<=tolerance,"allValuesCompared":True,"sampleCount":n,"rmsError":math.sqrt(total/n),
        "stage":protocol.stage({"sampleCount":n,"absoluteTolerance":tolerance},
            "E_inf = max_i(abs(native_continuous_i-native_restarted_i))",
            f"max(abs(delta_i)), i=1..{n}; compare with {tolerance} {unit}",maximum,unit)}


def verify_branch(branch_directory):
    """Read current bytes and actual native facts, not an accepted-looking flag."""
    import run_frozen_rotor_phase as native_runner
    root=case.base.regular_directory(branch_directory)
    current=build_manifest(root);manifest=current["manifest"];study=current["study"]
    persisted,manifest_row=native.record(root,"stage4-input-manifest.json")
    study_raw,study_row=native.read(root,"stage4-classroom-study.json",native.MAX_RECORD)
    require(persisted==manifest and study_raw==encoded(study),"CFD_CURRENT_PREEXECUTION_STAGE4_MANIFEST_REQUIRED")
    execution,execution_row=native.record(root,"stage4-branch-execution.json")
    prep,_=native.record(root,"stage4-branch-preparation.json");variant=prep["variant"]
    require(execution.get("schemaVersion")=="untirta.frozen-rotor-stage4-branch-execution.v1"
            and execution.get("status")=="STAGE4_NATIVE_EXECUTED_ACCEPTANCE_REQUIRED"
            and execution.get("mode")=="transient-sliding" and execution.get("exampleDataCommit")==case.base.inventory.PIN
            and execution.get("mainApplicationWrites") is False and execution.get("id")==variant["id"]
            and all(execution.get(key) is True for key in ("actualCfdRunVerified","simulationExecuted","nativeCheckMeshVerified",
                "nativeSolverCompleted","cavitationModelExecuted","finiteNativeEndFieldsVerified","physicalTransientExecuted",
                "inputArtifactsImmutable","fixedClockVerified"))
            and execution.get("preparationReportSha256")==manifest["preparationReportSha256"]
            and execution.get("runtimeIdentitySha256")==manifest["runtimeIdentitySha256"]
            and execution.get("inputManifestSha256")==manifest_row["sha256"]
            and execution.get("studySha256")==study_row["sha256"]
            and execution.get("temporalPlan")=={key:variant[key] for key in ("initialTimeS","endTimeS","timeStepS")},
            "CFD_ACTUAL_BOUND_STAGE4_NATIVE_EXECUTION_REQUIRED")
    records=[manifest_row,study_row,execution_row]+[{key:row[key] for key in ("path","bytes","sha256")} for row in manifest["files"]]
    stages=execution.get("stages",[]);logs={}
    require(len(stages)==2 and [row.get("program") for row in stages]==["checkMesh","foamRun"],"CFD_COMPLETE_STAGE4_NATIVE_STAGES_REQUIRED")
    for index,item in enumerate(stages):
        require(item.get("exitCode")==0 and item.get("path")==f"logs/{index:02d}-{item['program']}.log","CFD_EXACT_STAGE4_NATIVE_LOG_ROLE_REQUIRED")
        raw,row=native.read(root,item["path"],MAX_LOG)
        require(row["bytes"]==item.get("bytes") and row["sha256"]==item.get("sha256"),"CFD_CURRENT_STAGE4_NATIVE_LOG_REQUIRED")
        logs[item["program"]]=raw.decode("utf-8");records.append(row)
    mesh=native.parse_mesh_quality(logs["checkMesh"])
    require(mesh==execution.get("nativeMeshQuality"),"CFD_ACTUAL_STAGE4_MESH_QUALITY_REQUIRED")
    clock,clock_evidence=fixed_solver_history(logs["foamRun"],variant)
    require(clock_evidence==execution.get("fixedClockEvidence"),"CFD_CURRENT_ACTUAL_FIXED_CLOCK_EVIDENCE_REQUIRED")
    fields=execution.get("finalNativeFieldRecords",[])
    require(len(fields)==len(native_runner.END_FIELDS) and {item.get("path","").split("/")[-1] for item in fields}==set(native_runner.END_FIELDS),
            "CFD_COMPLETE_STAGE4_NATIVE_END_FIELDS_REQUIRED")
    for item in fields:
        path=item.get("path","");parts=path.split("/")
        require(len(parts)==3 and parts[0]=="case" and re.fullmatch(TIME_NAME,parts[1])
                and math.isclose(float(parts[1]),variant["endTimeS"],abs_tol=1e-12),"CFD_EXACT_STAGE4_NATIVE_END_TIME_REQUIRED")
        raw,row=native.read(root,path,native.MAX_FIELD)
        require(row["bytes"]==item.get("bytes") and row["sha256"]==item.get("sha256")
                and native_runner.finite_field(raw,parts[2],mesh["meshCellCount"])==item.get("fieldCheck"),
                "CFD_ACTUAL_FULL_STAGE4_NATIVE_END_FIELD_REQUIRED")
        records.append(row)
    sampling=native_runner.sampling_records(root)
    require(sampling and sampling==execution.get("nativeSamplingRecords"),"CFD_CURRENT_STAGE4_NATIVE_SAMPLERS_REQUIRED")
    records.extend(sampling)
    moved=execution.get("rotorMotionEvidence",{}).get("nativeMovedPointRecords",[])
    time_paths=[]
    for item in moved:
        path=item.get("path","")
        require(re.fullmatch(r"case/"+TIME_NAME+r"/polyMesh/points",path),"CFD_EXACT_STAGE4_MOVED_POINT_ROLE_REQUIRED")
        raw,row=native.read(root,path,native.MAX_FIELD)
        require(row["bytes"]==item.get("bytes") and row["sha256"]==item.get("sha256"),"CFD_CURRENT_STAGE4_MOVED_POINTS_REQUIRED")
        time_paths.append((float(path.split("/")[1]),root/"case"/path.split("/")[1]));records.append(row)
    require(moved and native_runner.rotor_motion(root,time_paths,logs["foamRun"])==execution.get("rotorMotionEvidence"),
            "CFD_ACTUAL_STAGE4_SLIDING_POINT_MOTION_REQUIRED")
    # A resumed branch additionally binds the complete checkpoint copy before
    # accepting native facts. Preparation alone never establishes restart.
    if variant["id"]=="resumed":
        checkpoint,checkpoint_row=native.record(root,"stage4-checkpoint.json")
        resume,resume_row=native.record(root,"stage4-resume-preparation.json")
        require(prep.get("resumeCheckpointSha256")==checkpoint.get("checkpointSha256")==resume.get("checkpointSha256"),
                "CFD_EXACT_RESUMED_CHECKPOINT_BINDING_REQUIRED")
        records.extend((checkpoint_row,resume_row))
        for item in checkpoint.get("files",[]):
            raw,row=native.read(root,item["path"],native.MAX_FIELD)
            require(row==item,"CFD_COMPLETE_RESUMED_CHECKPOINT_COPY_REQUIRED");records.append(row)
        require(re.search(r"\bRestarting alpha\b",logs["foamRun"]),"CFD_NATIVE_PHASE_FLUX_RESTART_READ_REQUIRED")
    records=sorted({row["path"]:row for row in records}.values(),key=lambda row:row["path"])
    return {"root":root,"study":study,"manifest":manifest,"manifestRow":manifest_row,"execution":execution,
            "records":records,"logs":logs,"mesh":mesh,"clock":clock,"clockEvidence":clock_evidence,"prepared":prep}


def observation(packet):
    """Actual three-period samples; preserve independently supported equations."""
    root,study,manifest=packet["root"],packet["study"],packet["manifest"]
    output={"status":"Stage4NumericalAcceptanceRequired","phaseObservationQualified":False,"diagnostics":[],"stages":{}}
    digest=sha256(encoded(packet["records"])).hexdigest()
    dt=manifest["temporalPlan"]["physicalTimeStepS"]
    run={"studyInputs":study["input"],"controls":manifest["controls"],"samplingDefinition":study["samplingDefinition"],
        "physicalBasis":study["physicalBasis"],"physicalModel":manifest["physicalModel"],"runRevision":"Current",
        "pressureLevelAbsolutePa":manifest["pressureLevelAbsolutePa"],
        "nativeEvidence":{"kind":"native-solver-sampling","upstreamVerified":True,"sha256":digest,"runId":digest},
        "controlledVariationProof":{"upstreamVerified":True,"nativeInputManifestSha256":packet["manifestRow"]["sha256"],
            "samplingEvidenceSha256":digest,"structuralControls":manifest["controls"],"signatureScope":study["signatureScope"],
            "physicalModel":manifest["physicalModel"],"pressureBoundaryDefinition":study["controlledPressureDefinition"],
            "pressureLevelAbsolutePa":manifest["pressureLevelAbsolutePa"],"physicalTimeStepS":dt},
        "pressureMapping":{"nativeField":"p_rgh","nativeUnit":"Pa","absolutePressureReferencePa":0,
            "absoluteReconstructionEquation":"p_absolute=p_rgh+rho*gh; authored native g=0, hence p_absolute=p_rgh. No atmospheric addition.",
            "hydrostaticReconstructionApplied":True},"measurements":{},
        "quality":{"nativeMeshQualityChecked":True,**packet["mesh"],"physicalTimeStepS":dt}}
    roles={}
    for suffix,patch in (("Inlet","inlet"),("Outlet","mass-flow-inlet-11"),("RotorInterface",native.COUPLED_PATCHES[0]),("StatorInterface",native.COUPLED_PATCHES[1])):
        roles["massFlow"+suffix]=(("sum(rhoPhi)",),patch)
        roles["massFlowMagnitude"+suffix]=(("sumMag(rhoPhi)",),patch)
        if suffix in {"Inlet","Outlet"}:
            roles["weighted"+suffix]=(("average(p)","average(totalPressurePa)","average(U2)","average(alpha.water)"),patch)
            roles["waterMinimum"+suffix]=(("min(alpha.water)",),patch)
    for label,columns in (("globalPressureMinimum",("min(p)",)),("globalWaterMinimum",("min(alpha.water)",)),("globalWaterMaximum",("max(alpha.water)",))):
        roles[label]=(columns,None)
    tables={}
    for name,(columns,patch) in roles.items():
        try:
            raw_table,_=phase.sampler(root,name,columns,patch)
            tables[name]=verified_sampling_clock(raw_table,packet["prepared"]["variant"])
        except (ValueError,OSError,UnicodeError) as error: output["diagnostics"].append({"branch":name,"reasonCode":str(error)[:500]})
    start,end=manifest["commonWindowS"];rho=study["input"]["densityKgM3"];g=study["input"]["gravityMS2"]
    for suffix in ("Inlet","Outlet"):
        try:
            table=tables["weighted"+suffix];minimum=tables["waterMinimum"+suffix]
            cut=phase.interval_samples(table,start,end);mins=phase.interval_samples(minimum,start,end)
            require(list(cut)==list(mins),"CFD_MATCHED_STAGE4_LIQUID_CUT_TIMES_REQUIRED")
            raw_min=min(row[0] for row in mins.values())
            require(-1e-9<=raw_min<=1+1e-9,"CFD_STAGE4_LIQUID_SECTION_BOUNDS_REQUIRED")
            vapor=min(1.,max(0.,1-raw_min))
            for instant,(ps,pt,u2,liquid) in cut.items():
                require(u2>=0 and -1e-9<=liquid<=1+1e-9 and mins[instant][0]<=liquid+1e-9,"CFD_ACTUAL_STAGE4_SECTION_MOMENTS_REQUIRED")
                if vapor<=study["acceptance"]["liquidSectionVaporFractionLimit"]:
                    require(math.isclose(pt,ps+rho*u2/2,rel_tol=2e-5,abs_tol=2),"CFD_LIQUID_STAGE4_TOTAL_PRESSURE_REQUIRED")
            ps,pt,u2,_=phase.time_mean(table,start,end);prefix=suffix.lower()
            run["measurements"].update({prefix+"TotalPressureAbsolutePa":pt,prefix+"ElevationM":0.,prefix+"MaximumVaporFraction":vapor})
            if suffix=="Inlet":
                if vapor<=study["acceptance"]["liquidSectionVaporFractionLimit"]:
                    require(math.isclose(pt,manifest["pressureLevelAbsolutePa"],rel_tol=2e-5,abs_tol=2),"CFD_ACTUAL_STAGE4_INLET_PRESSURE_REQUIRED")
                run["measurements"].update(inletStaticPressureAbsolutePa=ps,inletMeanSquaredAbsoluteSpeedM2S2=u2)
        except (ValueError,KeyError,OSError) as error: output["diagnostics"].append({"branch":suffix.lower()+"-cut","reasonCode":str(error)[:500]})
    try:
        require(not output["diagnostics"],"CFD_COMPLETE_STAGE4_ACCEPTANCE_SAMPLERS_REQUIRED")
        all_times=list(tables["weightedInlet"])
        require(all(list(table)==all_times for table in tables.values()),"CFD_MATCHED_STAGE4_SAMPLING_CLOCK_REQUIRED")
        clock=packet["clock"];initial=manifest["temporalPlan"]["initialTimeS"]
        sampled=[t for t in all_times if t>initial+1e-12]
        require(len(sampled)==len(clock) and all(math.isclose(a,b,abs_tol=1e-12,rel_tol=5e-10) for a,b in zip(sampled,clock)),
                "CFD_STAGE4_SOLVER_SAMPLING_CLOCK_BINDING_REQUIRED")
        window={name:phase.interval_samples(table,start,end) for name,table in tables.items()}
        times=list(window["weightedInlet"])
        head_table={t:[(window["weightedOutlet"][t][1]-window["weightedInlet"][t][1])/(rho*g)] for t in times}
        edges=[start+(end-start)*i/7 for i in range(8)]
        bins=[phase.time_mean(head_table,a,b)[0] for a,b in zip(edges,edges[1:])]
        flows_in,flows_out,reverse,mismatch=[],[],[],[]
        for instant in times:
            fi,fo=window["massFlowInlet"][instant][0],window["massFlowOutlet"][instant][0]
            mi,mo=window["massFlowMagnitudeInlet"][instant][0],window["massFlowMagnitudeOutlet"][instant][0]
            reverse.extend((phase.reverse_fraction(fi,mi,True),phase.reverse_fraction(fo,mo,False)))
            flows_in.append(fi);flows_out.append(fo)
            fr,fs=window["massFlowRotorInterface"][instant][0],window["massFlowStatorInterface"][instant][0]
            mr,ms=window["massFlowMagnitudeRotorInterface"][instant][0],window["massFlowMagnitudeStatorInterface"][instant][0]
            reverse.extend((phase.reverse_fraction(fr,mr,False),phase.reverse_fraction(fs,ms,True)))
            mismatch.append(abs(fr+fs)/max(abs(fr),abs(fs)))
        residual={name:[] for name in ("p","U","k","omega")};continuity=[]
        for instant,value in clock.items():
            if not start-1e-12<=instant<=end+1e-12:continue
            require(value["continuity"] and len(value["outer"])==3 and {"k","omega"}<=set(value["outer"][-1]),"CFD_STAGE4_AUTHORED_PIMPLE_HISTORY_REQUIRED")
            continuity.extend(value["continuity"])
            native.parse_coverage("\n".join(value["coverage"]))
            for outer in value["outer"]:
                require({"p_rgh","Ux","Uy","Uz"}<=set(outer),"CFD_STAGE4_FIRST_OUTER_RESIDUAL_REQUIRED")
                residual["p"].append(outer["p_rgh"]);residual["U"].append(max(outer[x] for x in ("Ux","Uy","Uz")))
                for name in ("k","omega"):
                    if name in outer:residual[name].append(outer[name])
        raw_min=min(row[0] for row in window["globalWaterMinimum"].values());raw_max=max(row[0] for row in window["globalWaterMaximum"].values())
        require(-1e-9<=raw_min<=raw_max<=1+1e-9 and all(window["globalWaterMinimum"][t][0]<=window["globalWaterMaximum"][t][0] for t in times),"CFD_STAGE4_GLOBAL_PHASE_BOUNDS_REQUIRED")
        limits=manifest["classroomAcceptance"]
        require(continuity and max(row[0] for row in continuity)<=limits["continuityLocalLimit"] and max(mismatch)<=limits["interfaceFluxRelativeLimit"],"CFD_STAGE4_CONTINUITY_INTERFACE_FLUX_REQUIRED")
        coverage=native.parse_coverage(packet["logs"]["checkMesh"]+"\n"+packet["logs"]["foamRun"])
        require(all(values[0]>=limits["interfaceMinimumCoverage"] and values[1]>=limits["interfaceAverageCoverage"] and values[2]<=limits["interfaceMaximumCoverage"] for pair in coverage for values in pair.values()),"CFD_STAGE4_ALL_NATIVE_NCC_COVERAGE_REQUIRED")
        mean_in=phase.time_mean(tables["massFlowInlet"],start,end)[0];mean_out=phase.time_mean(tables["massFlowOutlet"],start,end)[0]
        period=manifest["temporalPlan"]["rotationPeriodS"]
        periods=[phase.time_mean(head_table,end-period*i,end-period*(i-1))[0] for i in (3,2,1)]
        evidence=packet["clockEvidence"]
        run["quality"].update(outwardInletMassFlowKgS=mean_in,outwardOutletMassFlowKgS=mean_out,reverseFlowFraction=max(reverse),
            finalResidualHistory=residual,residualDefinition="initial-solver-residual-per-outer-iteration-final-window",
            samplingWindowDefinition="final-converged-window",finalFlowRelativeSpan=max((max(v)-min(v))/abs(m) for v,m in ((flows_in,mean_in),(flows_out,mean_out))),
            headHistoryM=bins,minimumVaporFraction=max(0.,1-raw_max),maximumVaporFraction=min(1.,1-raw_min),
            observedMaximumTimeStepS=dt,observedMaxCo=evidence["maximumCo"],observedMaxAlphaCo=evidence["maximumAlphaCo"],
            statisticalWindowS=[start,end],periodicHeadDifferencesM=[b-a for a,b in zip(periods,periods[1:])])
        pmin=min(row[0] for row in window["globalPressureMinimum"].values())
        require(pmin>0,"CFD_POSITIVE_ABSOLUTE_STAGE4_GLOBAL_PRESSURE_REQUIRED")
        run["measurements"]["minimumStaticPressureAbsolutePa"]=pmin
        output.update(statisticalWindowS=[start,end],periodMeanHeadM=periods,rawGlobalWaterMinimum=raw_min,rawGlobalWaterMaximum=raw_max,
            alphaRoundoffTolerance=1e-9,nativeHeadSampleExtremaM=[min(v[0] for v in head_table.values()),max(v[0] for v in head_table.values())],
            interfaceFluxRelativeMismatch=max(mismatch),nativeCoverageAtSampledRotorStepsVerified=True,
            physicalRotorAngleStage=protocol.time_step_stages(study,end,dt),
            temporalReduction="Actual bracketing samples, piecewise-linear trapezoid integration; seven equal-time bins preserve the head temporal mean.",
            fixedClockEvidence=evidence,
            protocolClockProjection="Every actual Time increment and every actual sampler time is independently verified against the same authored fixed grid within the recorded floating-point tolerance. Only scalar time spelling/roundoff is normalized to the exact matched grid including its endpoint; raw logs/table bytes and observed minimum/maximum increments are preserved. The protocol receives that verified grid spacing, without adaptive-step assumptions, extrapolation or a missing clock substitution.")
    except (ValueError,KeyError,OSError,ZeroDivisionError) as error:output["diagnostics"].append({"branch":"native-quality","reasonCode":str(error)[:500]})
    projected=protocol.reduce_run(study,run)
    output.update(nativeObservation=run,stages=projected["stages"],protocolReduction=projected,npshaDomainStatus=projected["npshaDomainStatus"])
    output["diagnostics"].extend(projected["diagnostics"])
    output["phaseObservationQualified"]=not output["diagnostics"] and projected["status"]=="QualifiedSuppliedObservation"
    if output["phaseObservationQualified"]:output["status"]="PreliminaryStage4ObservationQualified"
    return output


def full_restart(campaign_root,continuous,restarted,checkpoint,tolerances):
    """Compare every native same-order cell and moved point after actual resume."""
    require(isinstance(tolerances,dict) and set(tolerances)=={"pAbsolutePa","UAbsoluteMS","vaporFraction","pointsAssumedM"},
            "CFD_EXPLICIT_FULL_RESTART_TOLERANCES_REQUIRED")
    tolerances={key:protocol.number(value,positive=True) for key,value in tolerances.items()}
    left,right=continuous["manifest"],restarted["manifest"]
    require(left["controls"]==right["controls"] and left["studyInputs"]==right["studyInputs"]
            and left["pressureLevelAbsolutePa"]==right["pressureLevelAbsolutePa"]
            and left["physicalBasis"]==right["physicalBasis"]
            and left["physicalModel"]==right["physicalModel"]=="transient-cavitation-sliding-mesh",
            "CFD_MATCHED_NATIVE_RESTART_FAMILY_REQUIRED")
    dt=left["temporalPlan"]["physicalTimeStepS"];end=left["temporalPlan"]["endTimeS"]
    beginning=protocol.number(checkpoint.get("physicalTimeS"),positive=True)
    period=60/left["studyInputs"]["speedRpm"]
    require(right["temporalPlan"]["physicalTimeStepS"]==dt and math.isclose(right["temporalPlan"]["initialTimeS"],beginning,abs_tol=1e-13)
            and math.isclose(right["temporalPlan"]["endTimeS"],end,abs_tol=1e-13) and end-beginning>=period-1e-13
            and checkpoint.get("physicalTimeStepS")==dt
            and all(math.isclose(t/dt,round(t/dt),rel_tol=0,abs_tol=1e-7) for t in (beginning,end)),
            "CFD_MEANINGFUL_ALIGNED_NATIVE_RESTART_WINDOW_REQUIRED")
    require(checkpoint.get("schemaVersion")==stage4().CHECKPOINT_SCHEMA
            and checkpoint.get("exampleDataCommit")==case.base.inventory.PIN and checkpoint.get("mainApplicationWrites") is False,
            "CFD_ACTUAL_STAGE4_CHECKPOINT_PACKET_REQUIRED")
    current=stage4().build_checkpoint(campaign_root,checkpoint["variant"],checkpoint["nativeTimeDirectory"])
    require(current==checkpoint,"CFD_CURRENT_COMPLETE_STAGE4_CHECKPOINT_REQUIRED")
    copied,_=native.record(restarted["root"],"stage4-checkpoint.json")
    prep,_=native.record(restarted["root"],"stage4-resume-preparation.json")
    require(copied==checkpoint and prep.get("checkpointSha256")==checkpoint["checkpointSha256"]
            and prep.get("constantReferencePointsPreserved") is True and prep.get("movedCheckpointPointsPreserved") is True
            and prep.get("nativeUniformTimePreserved") is True and prep.get("sourceCheckpointImmutable") is True,
            "CFD_COMPLETE_NATIVE_RESUME_PROVENANCE_REQUIRED")
    reference=[{key:row[key] for key in ("path","bytes","sha256")} for row in left["files"] if row["control"]=="meshSha256"]
    right_reference=[{key:row[key] for key in ("path","bytes","sha256")} for row in right["files"] if row["control"]=="meshSha256"]
    require(reference==right_reference and {row["path"] for row in reference}=={"case/constant/polyMesh/"+name for name in case.MESH_FILES},
            "CFD_EXACT_RESTART_TOPOLOGY_CELL_POINT_ORDER_REQUIRED")
    count=continuous["mesh"]["meshCellCount"]
    require(count==restarted["mesh"]["meshCellCount"]==checkpoint.get("meshCounts",{}).get("cells")
            and 0<count<=MAX_CELLS,"CFD_EXACT_COMPLETE_RESTART_CELL_COUNT_REQUIRED")
    comparison={};records=[]
    for name,key in (("p","pAbsolutePa"),("U","UAbsoluteMS"),("alpha.water","vaporFraction")):
        raw=[]
        for item in (continuous,restarted):
            rows=[row for row in item["execution"]["finalNativeFieldRecords"] if row["path"].endswith("/"+name)]
            require(len(rows)==1,"CFD_COMPLETE_CURRENT_RESTART_FIELD_REQUIRED")
            data,row=native.read(item["root"],rows[0]["path"],native.MAX_FIELD)
            require(row["bytes"]==rows[0]["bytes"] and row["sha256"]==rows[0]["sha256"],"CFD_IMMUTABLE_FULL_RESTART_FIELD_REQUIRED")
            raw.append(data);records.append({"branch":item["prepared"]["variant"]["id"],**row})
        comparison[key]=compare_native_values(*raw,name,count,tolerances[key])
        if name=="alpha.water":comparison[key]["transform"]="alpha_v=1-alpha.water; absolute differences are exactly equal before numerical roundoff. Native liquid values within explicit 1e-9 tolerance are compared without clamping."
    moving=[]
    for item in (continuous,restarted):
        rows=[row for row in item["execution"]["rotorMotionEvidence"]["nativeMovedPointRecords"]
              if math.isclose(row["physicalTimeS"],end,abs_tol=1e-13)]
        require(len(rows)==1,"CFD_CURRENT_RESTART_ENDPOINT_MOVED_POINTS_REQUIRED")
        data,row=native.read(item["root"],rows[0]["path"],native.MAX_FIELD)
        require(row["bytes"]==rows[0]["bytes"] and row["sha256"]==rows[0]["sha256"],"CFD_IMMUTABLE_RESTART_MOVED_POINTS_REQUIRED")
        moving.append(data);records.append({"branch":item["prepared"]["variant"]["id"],**row})
    points=checkpoint["meshCounts"]["points"]
    comparison["pointsAssumedM"]=compare_native_values(*moving,"points",points,tolerances["pointsAssumedM"],points=True)
    angle=protocol.time_step_stages(continuous["study"],end,dt)
    return {"status":"CriterionSatisfied" if all(row["criterionSatisfied"] for row in comparison.values()) else "RestartMismatch",
            "physicalWindowS":[beginning,end],"physicalRevolutions":(end-beginning)/period,"physicalTimeStepS":dt,
            "checkpointSha256":checkpoint["checkpointSha256"],"fieldCellCount":count,"pointCount":points,
            "fieldLocation":"cell-centers","fieldOrderSha256":sha256(encoded(reference)).hexdigest(),
            "fieldOrderDefinition":"Exact immutable ordered native six-file polyMesh inventory including points/faces/owner/neighbour/cellZones; no field sorting, interpolation or mapping.",
            "allNativeCellAndPointValuesCompared":True,"fields":comparison,"nativeEvidenceRecords":records,
            "endpointRotorAngleStages":angle,"nativeCoordinateUnitsVerified":False,"classroomMetresPerSourceCoordinateUnit":1,
            "claimBoundary":"Actual continuous and resumed same-clock full-field/point agreement after at least one revolution under declared tolerances. This does not prove time/mesh convergence, experimental accuracy, NPSH3 or all-angle interface quality."}


def mesh_family(branch_directories):
    """Optional actual independently produced three-mesh branch evidence only."""
    require(len(branch_directories)==3,"CFD_THREE_ACTUAL_SPATIAL_BRANCHES_REQUIRED")
    packets=[verify_branch(path) for path in branch_directories]
    reductions=[observation(packet) for packet in packets]
    require(all(row["phaseObservationQualified"] for row in reductions),"CFD_QUALIFIED_ACTUAL_SPATIAL_OBSERVATIONS_REQUIRED")
    result=protocol.numerical_verification(packets[0]["study"],[row["nativeObservation"] for row in reductions],"mesh")
    return {"headSpatialRefinement":result,"headMeshConvergenceVerified":result["status"]=="CriterionSatisfied",
            "npsh3MeshConvergenceVerified":False,
            "claimBoundary":"Head volume-mesh refinement under fixed preserved boundary facets and declared classroom geometry/units. NPSH3, geometry-resolution, near-wall and physical validation remain separate."}


def reduce_campaign(campaign_directory,*,restart_tolerances=None,mesh_branch_directories=None):
    root=Path(campaign_directory)
    output={"schemaVersion":REDUCTION_SCHEMA,"status":"Stage4NativeEvidenceRequired","actualCfdRunVerified":False,
        "timeStepConvergenceVerified":False,"headTimeStepConvergenceVerified":False,"restartVerified":False,
        "meshConvergenceVerified":False,"headMeshConvergenceVerified":False,"npsh3TimeStepConvergenceVerified":False,
        "npsh3MeshConvergenceVerified":False,"npsh3Verified":False,"wallResolutionVerified":False,"physicalValidationVerified":False,
        "mainApplicationWrites":False,"branches":{},"diagnostics":[],
        "claimBoundary":"Native head time sensitivity and native full-field restart are separate from head mesh sensitivity, NPSH3 convergence and physical/experimental validation. Source single-phase H0 is never an active-phase reference."}
    packets={}
    try:
        parent,parent_row=native.record(root,"stage4-campaign-execution.json")
        prep,prep_row=native.record(root,"stage4-case-preparation.json")
        require(parent.get("schemaVersion")=="untirta.frozen-rotor-stage4-campaign-execution.v1"
                and parent.get("exampleDataCommit")==case.base.inventory.PIN and parent.get("mainApplicationWrites") is False
                and parent.get("preparationReportSha256")==prep_row["sha256"]
                and parent.get("sourceBaselineImmutable") is True
                and prep.get("schemaVersion")==stage4().SCHEMA and prep.get("exampleDataCommit")==case.base.inventory.PIN
                and prep.get("mainApplicationWrites") is False,"CFD_CURRENT_BOUND_STAGE4_CAMPAIGN_REQUIRED")
        branches=parent.get("branches",[])
        require(len(branches)<=3 and len({row.get("id") for row in branches})==len(branches),"CFD_UNIQUE_STAGE4_BRANCHES_REQUIRED")
        output["campaignEvidenceRecords"]=[parent_row,prep_row]
        for branch in branches:
            try:
                label=branch["id"];require(label in {"coarse","medium","fine"} and branch.get("casePath")==label+"-case","CFD_EXACT_STAGE4_BRANCH_ROLE_REQUIRED")
                actual,row=native.record(root,label+"-case/stage4-branch-execution.json")
                require(row==branch.get("report"),"CFD_PARENT_BOUND_STAGE4_BRANCH_REQUIRED")
                packet=verify_branch(root/(label+"-case"));packets[label]=packet
                output["branches"][label]=observation(packet)
                output["campaignEvidenceRecords"].append(row)
            except (ValueError,KeyError,OSError,UnicodeError,ImportError) as error:
                output["diagnostics"].append({"branch":branch.get("id","unknown"),"reasonCode":str(error)[:500]})
        output["actualCfdRunVerified"]=bool(packets)
        try:
            require(set(packets)=={"coarse","medium","fine"},"CFD_THREE_ACTUAL_FIXED_CLOCK_BRANCHES_REQUIRED")
            runs=[output["branches"][key]["nativeObservation"] for key in ("coarse","medium","fine")]
            result=protocol.numerical_verification(packets["coarse"]["study"],runs,"time-step")
            result["quantity"]="Mean pump head over the same final three revolutions"
            result["npsh3ConvergenceVerified"]=False
            output["headTimeStepRefinement"]=result
            output["timeStepConvergenceVerified"]=output["headTimeStepConvergenceVerified"]=result["status"]=="CriterionSatisfied"
        except (ValueError,KeyError) as error:
            output["diagnostics"].append({"branch":"head-time-refinement","reasonCode":str(error)[:500]})
        try:
            item=parent.get("resumedBranch") or {}
            require(item.get("casePath")=="resumed-case" and item.get("id")=="resumed","CFD_ACTUAL_RESUMED_BRANCH_REQUIRED")
            _,row=native.record(root,"resumed-case/stage4-branch-execution.json")
            require(row==item.get("report"),"CFD_PARENT_BOUND_ACTUAL_RESUMED_BRANCH_REQUIRED")
            checkpoint,cp_row=native.record(root,"stage4-native-checkpoint.json")
            require(cp_row==parent.get("checkpoint"),"CFD_PARENT_BOUND_ACTUAL_CHECKPOINT_REQUIRED")
            resumed=verify_branch(root/"resumed-case")
            require("fine" in packets,"CFD_ACTUAL_CONTINUOUS_FINE_BRANCH_REQUIRED")
            output["restartComparison"]=full_restart(root,packets["fine"],resumed,checkpoint,restart_tolerances)
            output["restartVerified"]=output["restartComparison"]["status"]=="CriterionSatisfied"
            output["campaignEvidenceRecords"].extend((row,cp_row))
        except (ValueError,KeyError,OSError,UnicodeError,ImportError) as error:
            output["diagnostics"].append({"branch":"full-native-restart","reasonCode":str(error)[:500]})
        if mesh_branch_directories:
            try:
                output["spatialBranch"]=mesh_family(mesh_branch_directories)
                output["meshConvergenceVerified"]=output["headMeshConvergenceVerified"]=output["spatialBranch"]["headMeshConvergenceVerified"]
            except (ValueError,KeyError,OSError,UnicodeError,ImportError) as error:
                output["diagnostics"].append({"branch":"head-spatial-refinement","reasonCode":str(error)[:500]})
        else:
            output["spatialBranch"]={"status":"IndependentActualMeshFamilyRequired","headMeshConvergenceVerified":False,"npsh3MeshConvergenceVerified":False,
                "action":"Generate and execute three actual controlled volume refinements with the same geometry, boundary, phase family, pressure, fixed dt and statistical window. Optimisation modes on one trial are not a mesh refinement campaign."}
        output["status"]="HeadTimeAndNativeRestartCriteriaSatisfied" if output["headTimeStepConvergenceVerified"] and output["restartVerified"] else "Stage4NumericalAcceptanceRequired"
    except (ValueError,KeyError,OSError,UnicodeError,ImportError) as error:
        output["diagnostics"].append({"branch":"native-campaign","reasonCode":str(error)[:500]})
    return output


reduce_stage4_campaign=reduce_campaign


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-directory",type=Path,required=True)
    parser.add_argument("--mesh-branch-directory",type=Path,nargs=3)
    parser.add_argument("--restart-pressure-tolerance-pa",type=float,required=True)
    parser.add_argument("--restart-velocity-tolerance-ms",type=float,required=True)
    parser.add_argument("--restart-vapor-tolerance",type=float,required=True)
    parser.add_argument("--restart-point-tolerance-m",type=float,required=True)
    args=parser.parse_args()
    try:
        root=case.inside_runner(args.campaign_directory)
        destination=case.base.inventory.output_path(root/"stage4-campaign-reduction.json")
        tolerances={"pAbsolutePa":args.restart_pressure_tolerance_pa,"UAbsoluteMS":args.restart_velocity_tolerance_ms,
                    "vaporFraction":args.restart_vapor_tolerance,"pointsAssumedM":args.restart_point_tolerance_m}
        for value in tolerances.values():protocol.number(value,positive=True)
        result=reduce_campaign(root,restart_tolerances=tolerances,mesh_branch_directories=args.mesh_branch_directory)
        raw=encoded(result);require(len(raw)<=MAX_REDUCTION,"CFD_BOUNDED_STAGE4_REDUCTION_REQUIRED")
        case.base.write_exclusive(destination,raw)
        print(json.dumps({key:result[key] for key in ("status","actualCfdRunVerified","headTimeStepConvergenceVerified","restartVerified",
            "headMeshConvergenceVerified","npsh3Verified","mainApplicationWrites")},allow_nan=False))
        return 0 if result["headTimeStepConvergenceVerified"] and result["restartVerified"] else 2
    except (ValueError,KeyError,OSError,ImportError) as error:
        print(json.dumps({"status":"Stage4NativeEvidenceRequired","reasonCode":"CFD_STAGE4_REDUCTION_OUTPUT_REQUIRED",
                          "action":str(error)[:500],"actualCfdRunVerified":False,"mainApplicationWrites":False}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
