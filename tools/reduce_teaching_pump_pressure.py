"""Read actual pressure points and a bracketed NPSH3 on authored classroom CAD.

Only an independently current active-phase H0 can be the reference. Signature
normalization describes pressure-only comparisons; its virtual bytes are never
executed. Native completion, numerical point acceptance, bracket interpolation
and unperformed physical/refinement qualification are separate facts.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
import frozen_rotor_native_reduction as native
import frozen_rotor_research_protocol as protocol
import frozen_rotor_phase_reduction as tables
import reduce_frozen_rotor_snappy_phase as maths
import reduce_teaching_pump_phase as reference_reducer
import run_frozen_rotor_phase as fields

SCHEMA = "untirta.teaching-pump-pressure-point-acceptance.v1"
SWEEP_SCHEMA = "untirta.teaching-pump-pressure-sweep-acceptance.v1"
MANIFEST_SCHEMA = "untirta.teaching-pump-pressure-input-manifest.v1"
STUDY_SCHEMA = "untirta.teaching-pump-pressure-study.v1"
PREPARATION_SCHEMA = "untirta.teaching-pump-pressure-point-preparation.v1"
EXECUTION_SCHEMA = "untirta.teaching-pump-pressure-point-execution.v1"
RUNTIME_SCHEMA = "untirta.teaching-pump-pressure-runtime-identity.v1"
REPORT_FILE, SWEEP_FILE = "teaching-pressure-acceptance.json", "teaching-pressure-sweep-acceptance.json"
MANIFEST_FILE, STUDY_FILE = "teaching-pressure-input-manifest.json", "teaching-pressure-study.json"
RUNTIME_FILE, EXECUTION_FILE = "teaching-pressure-runtime-identity.json", "teaching-pressure-execution.json"
ROUTE, MAX_REDUCTION_BYTES = "CLASSROOM_AUTHORED_GEOMETRY", 16*1024**2
PROGRAMS = ("checkMesh","foamRun")
FALSE_CLAIMS = ("qualifiedBaseline","numericalBaselineQualified","preliminaryBaselineQualified","physicalValidationVerified",
    "wallResolutionVerified","meshConvergenceVerified","timeStepConvergenceVerified","restartVerified",
    "physicalTransientExecuted","npsh3Qualified","manufacturerPublishedGeometry","originalFluentGeometryReproduced",
    "baselineHeadUsedAsPhaseH0","baselineFieldsCopied","mainApplicationWrites")
require, encoded, read, record = native.require, native.encoded, native.read, native.record
add_record, evidence_hash = maths.add_record, maths.evidence_hash


def preparer():
    import teaching_pump_pressure_case
    return teaching_pump_pressure_case


def controls_from_groups(family):
    groups=family.get("groups")
    require(isinstance(groups,dict) and set(groups)==protocol.CONTROL_KEYS,
        "TEACHING_PRESSURE_COMPLETE_FAMILY_GROUPS_REQUIRED")
    controls={}
    for key,rows in groups.items():
        require(isinstance(rows,list) and rows and len(rows)<=32 and len({r.get("path") for r in rows})==len(rows)
            and all(isinstance(r,dict) and set(r)=={"path","bytes","sha256"}
                and type(r["bytes"]) is int and 0<r["bytes"]<=native.MAX_FIELD and protocol.digest(r["sha256"]) for r in rows),
            "TEACHING_PRESSURE_UNIQUE_ACTUAL_FAMILY_DESCRIPTOR_REQUIRED")
        controls[key]=sha256(encoded(sorted(rows,key=lambda r:r["path"]))).hexdigest()
    return controls


def study_packet(source_study,controls):
    study=deepcopy(source_study)
    require(study.get("schemaVersion")==reference_reducer.STUDY_SCHEMA
        and study["sourceReference"]["kind"]=="classroom-authored-SI-pump-CAD"
        and study["sourceReference"].get("manufacturerPublished") is False
        and study["physicalBasis"]["gravityVectorMS2"]==[0,0,0],
        "TEACHING_PRESSURE_AUTHORED_ACTIVE_PHASE_STUDY_REQUIRED")
    study["schemaVersion"]=STUDY_SCHEMA;study["controls"]=controls
    study["signatureScope"].update({"boundarySha256":"Exact trusted actual BCs; only reviewed 0/p_rgh pressure slots use a signature-only1Pa sentinel",
        "discretizationSha256":"Exact source clock, samplers, schemes and nonlinear controls; no time variation normalized",
        "runtimeSha256":"Canonical actual Foundation/package/two executable descriptors; only validated document schema/role excluded",
        "cavitationModelSha256":"Exact same active SchnerrSauer/fluid model files; never a single-phase reference"})
    study["declaredAssumptions"]["phaseInitialization"]=("Fresh uniform liquid and zero velocity; each run's separately bound absolute native pressure is used, never copied result state")
    return study


def binary_identity(identity):
    helper=preparer()
    fixed={"schemaVersion":RUNTIME_SCHEMA,"openFoamFoundationVersion":12,"packageVersion":"20250206",
        "officialDebSha256":helper.safety.PACKAGE_SHA,"expectedSourceCommit":helper.safety.SOURCE_COMMIT,"mainApplicationWrites":False}
    require(isinstance(identity,dict) and set(identity)==set(fixed)|{"binaries"}
        and all(identity.get(k)==v and type(identity.get(k)) is type(v) for k,v in fixed.items()),
        "TEACHING_PRESSURE_EXACT_TYPED_RUNTIME_REQUIRED")
    rows=identity.get("binaries")
    require(isinstance(rows,list) and len(rows)==2 and [r.get("program") for r in rows]==list(PROGRAMS),
        "TEACHING_PRESSURE_TWO_CURRENT_NATIVE_BINARIES_REQUIRED")
    for row in rows:
        require(isinstance(row,dict) and set(row)=={"program","path","bytes","sha256","packageOwnership"}
            and type(row.get("bytes")) is int and 0<row["bytes"]<=native.MAX_FIELD and protocol.digest(row.get("sha256")),
            "TEACHING_PRESSURE_NATIVE_BINARY_DESCRIPTOR_REQUIRED")
        path=PurePosixPath(row.get("path",""))
        require(path.is_absolute() and path.is_relative_to(PurePosixPath("/opt/openfoam12")) and ".." not in path.parts
            and "\\" not in row["path"] and path.name==row["program"] and row["packageOwnership"]=="openfoam12: "+row["path"],
            "TEACHING_PRESSURE_OFFICIAL_NATIVE_PROGRAM_REQUIRED")
        raw=native.case_tool.read_regular(Path(row["path"]),native.MAX_FIELD)
        require(len(raw)==row["bytes"] and sha256(raw).hexdigest()==row["sha256"],"TEACHING_PRESSURE_CURRENT_NATIVE_BINARY_BYTES_REQUIRED")


def reference_projection(reference,family,study):
    """Verified comparison projection; retain original actual preexecution SHA."""
    packet=reference["packet"];run=deepcopy(packet["nativeObservation"])
    require(packet["schemaVersion"]==reference_reducer.SCHEMA and packet["phaseH0Eligible"] is True
        and packet["actualCfdRunVerified"] is True and packet["phaseObservationQualified"] is True
        and packet["nativeInputManifestSha256"]==run["controlledVariationProof"]["nativeInputManifestSha256"]
        and protocol.number(packet["activePhaseReferenceHeadM"],positive=True)>0,
        "TEACHING_PRESSURE_ACTUAL_ACTIVE_PHASE_H0_REQUIRED")
    require(controls_from_groups(family)==study["controls"] and run["studyInputs"]==study["input"]
        and run["physicalBasis"]==study["physicalBasis"] and run["samplingDefinition"]==study["samplingDefinition"]
        and run["physicalModel"]=="steady-cavitation-MRF", "TEACHING_PRESSURE_CURRENT_SOURCE_FAMILY_REQUIRED")
    original=deepcopy(run["controlledVariationProof"])
    run["controls"]=dict(study["controls"])
    run["controlledVariationProof"].update(structuralControls=dict(study["controls"]),signatureScope=study["signatureScope"])
    proof={"kind":"verified-pressure-only-signature-projection","originalActualNativeInputManifestSha256":packet["nativeInputManifestSha256"],
        "originalActualControlledVariationProof":original,"actualReferenceAcceptanceFile":reference["reportFile"],
        "pressureNormalizationProof":family["pressureNormalizationProof"],"runtimeFamily":family["runtimeFamily"],
        "structuralSignatureRows":family["groups"],"normalizedNativeCaseExecuted":False,"singlePhaseHeadUsed":False}
    result=protocol.reduce_run(study,run)
    require(result["status"]=="QualifiedSuppliedObservation"
        and result["stages"]["head"]["result"]["value"]==packet["activePhaseReferenceHeadM"]
        and run["quality"]["maximumVaporFraction"]<=study["acceptance"]["liquidSectionVaporFractionLimit"]
        and run["measurements"]["minimumStaticPressureAbsolutePa"]>study["input"]["vaporPressurePa"],
        "TEACHING_PRESSURE_RECOMPUTED_NONCAVITATING_ACTIVE_H0_REQUIRED")
    return {"nativeObservation":run,"projectionEvidence":proof,"protocolReduction":result}


def build_manifest(execution_directory,*,after_native_execution=False):
    """Persist exact actual inputs and a distinct pressure-only family before run."""
    require(type(after_native_execution) is bool,"TEACHING_PRESSURE_EXPLICIT_VERIFICATION_STAGE_REQUIRED")
    root=native.case_tool.regular_directory(execution_directory);helper=preparer()
    verified=helper.verify_output(root,after_native_execution=after_native_execution)
    prep,prep_row=record(root,helper.REPORT)
    require(prep==verified["report"] and prep.get("schemaVersion")==PREPARATION_SCHEMA
        and prep.get("sourceGeometryRoute")==ROUTE and prep.get("mode")==helper.MODE
        and prep.get("sourceActivePhaseH0NumericallyQualified") is True and prep.get("sourceInputsImmutable") is True
        and all(prep.get(k) is False for k in helper.FALSE_FLAGS),"TEACHING_PRESSURE_CURRENT_POINT_PREPARATION_REQUIRED")
    runtime,runtime_row=record(root,RUNTIME_FILE);binary_identity(runtime)
    reference=verified["sourceReferencePacket"]
    family=helper.family_rows(root,prep,runtime);source_family=helper.reference_family(reference)
    controls=controls_from_groups(family)
    require(controls==controls_from_groups(source_family) and family["groups"]==source_family["groups"]
        and family["runtimeFamily"]==source_family["runtimeFamily"],"TEACHING_PRESSURE_ONLY_PRESSURE_VARIATION_REQUIRED")
    pressure=helper.pressure_value(prep["inletAbsoluteTotalPressurePa"],reference["preparation"]["inletAbsoluteTotalPressurePa"])
    require(pressure==prep["pressureAbsolutePa"],"TEACHING_PRESSURE_SELECTED_ABSOLUTE_PRESSURE_REQUIRED")
    study=study_packet(reference["study"],controls);projection=reference_projection(reference,source_family,study)
    files=[{**prep_row,"control":None},{**runtime_row,"control":"runtimeSha256"}]
    source_rows=prep["sourceEvidenceFileRecords"]
    require(isinstance(source_rows,list) and 0<len(source_rows)<=320,"TEACHING_PRESSURE_COMPLETE_ACTIVE_SOURCE_INVENTORY_REQUIRED")
    total=0
    for row in source_rows:
        require(set(row)=={"path","bytes","sha256"} and row["path"].startswith("source-reference/"),
            "TEACHING_PRESSURE_SOURCE_FILE_ROLE_REQUIRED")
        actual=read(root,row["path"],native.MAX_FIELD)[1];require(actual==row,"TEACHING_PRESSURE_ACTIVE_SOURCE_BYTES_CHANGED")
        total+=actual["bytes"];require(total<=1024**3,"TEACHING_PRESSURE_SOURCE_BYTE_BOUND_REQUIRED")
        files.append({**actual,"control":None})
    identity=read(root,"teaching-geometry-identity.json",native.MAX_RECORD)[1]
    require(identity==prep["sourceGeometryIdentityFile"],"TEACHING_PRESSURE_EXACT_COPIED_SI_IDENTITY_REQUIRED")
    files.append({**identity,"control":"geometrySha256"})
    for key in ("preservedNativeMeshFiles","authoredInputFiles"):
        for row in prep[key]:
            actual=read(root,"case/"+row["path"],native.MAX_FIELD)[1]
            require(actual=={**row,"path":"case/"+row["path"]},"TEACHING_PRESSURE_CURRENT_NATIVE_INPUT_BYTES_REQUIRED")
            control="meshSha256" if key=="preservedNativeMeshFiles" else "cavitationModelSha256" if row["path"] in helper.MODEL_FILES \
                else "discretizationSha256" if row["path"].startswith("system/") else "boundarySha256"
            files.append({**actual,"control":control})
    require(len(files)<=384 and len({r["path"] for r in files})==len(files),"TEACHING_PRESSURE_UNIQUE_BOUNDED_NATIVE_MANIFEST_REQUIRED")
    require(controls["cavitationModelSha256"]==prep["authoredCavitationModelSha256"],"TEACHING_PRESSURE_EXACT_ACTIVE_MODEL_REQUIRED")
    manifest={"schemaVersion":MANIFEST_SCHEMA,"sourceGeometryRoute":ROUTE,"files":sorted(files,key=lambda r:r["path"]),
        "controls":controls,"structuralSignatureRows":family["groups"],"pressureNormalizationProof":family["pressureNormalizationProof"],
        "runtimeFamily":family["runtimeFamily"],"signatureScope":study["signatureScope"],"studyInputs":study["input"],
        "physicalBasis":study["physicalBasis"],"samplingDefinition":study["samplingDefinition"],"physicalModel":"steady-cavitation-MRF",
        "mode":helper.MODE,"pressureLevelAbsolutePa":pressure,"temporalPlan":prep["temporalPlan"],
        "absolutePressureMapping":prep["absolutePressureMapping"],"preparationReportSha256":prep_row["sha256"],
        "runtimeIdentitySha256":runtime_row["sha256"],"sourceReferenceAcceptanceFile":prep["sourceReferenceAcceptanceFile"],
        "sourceReferenceInputManifestSha256":prep["sourceReferenceInputManifestSha256"],
        "sourceActivePhaseEvidenceSha256":prep["sourceActivePhaseEvidenceSha256"],"sourceActivePhaseHeadM":prep["sourceActivePhaseHeadM"],
        "sourceReferencePressureAbsolutePa":prep["sourceReferencePressureAbsolutePa"],"sourceReferenceFamilyProjection":projection["projectionEvidence"],
        "normalizationScope":"Actual full trusted recipe verified first. Only declared pressure slots are virtual1Pa; runtime schema/document role excluded after installed binary verification. No normalized native case is run.",
        "inletElevationM":0.,"outletElevationM":0.,"elevationBasis":study["physicalBasis"]["elevationDefinition"],
        "classroomAcceptance":dict(maths.LIMITS),"simulationExecuted":False,"mainApplicationWrites":False}
    require(len(encoded(manifest))<=native.MAX_RECORD,"TEACHING_PRESSURE_BOUNDED_CANONICAL_MANIFEST_REQUIRED")
    return {"manifest":manifest,"study":study}


def numerical_observation(root,output,manifest,study,execution,logs,mesh,records):
    """Actual independent formula branches and unchanged three-period criteria."""
    pressure=manifest["pressureLevelAbsolutePa"];digest=evidence_hash(records)
    run={"studyInputs":study["input"],"controls":manifest["controls"],"samplingDefinition":study["samplingDefinition"],
        "physicalBasis":study["physicalBasis"],"physicalModel":"steady-cavitation-MRF","runRevision":"Current",
        "pressureLevelAbsolutePa":pressure,"nativeEvidence":{"kind":"native-solver-sampling","upstreamVerified":True,
            "sha256":digest,"runId":execution["githubRunId"]},
        "controlledVariationProof":{"upstreamVerified":True,"nativeInputManifestSha256":output["nativeInputManifestSha256"],
            "samplingEvidenceSha256":digest,"structuralControls":manifest["controls"],"signatureScope":study["signatureScope"],
            "physicalModel":"steady-cavitation-MRF","pressureBoundaryDefinition":study["controlledPressureDefinition"],
            "pressureLevelAbsolutePa":pressure,"physicalTimeStepS":None},
        "pressureMapping":{"nativeField":"p_rgh","nativeUnit":"Pa","absolutePressureReferencePa":0,
            "absoluteReconstructionEquation":"p_abs=p_rgh+rho*gh; authored g0, hence p_abs=p_rgh; no atmospheric addition",
            "hydrostaticReconstructionApplied":True},"measurements":{},"quality":{"nativeMeshQualityChecked":True,**mesh}}
    q=run["quality"];data={};roles=maths.sampler_roles()
    for name,(columns,patch) in roles.items():
        try:data[name],row=tables.sampler(root,name,columns,patch);add_record(records,row)
        except (protocol.ProtocolError,OSError,UnicodeError,ValueError) as error:
            output["diagnostics"].append({"branch":name,"reasonCode":str(error)[:500]})
    end=manifest["temporalPlan"]["endTimeS"];period=manifest["temporalPlan"]["rotationPeriodS"];start=end-3*period
    rho,g=study["input"]["densityKgM3"],study["input"]["gravityMS2"]
    for suffix in ("Inlet","Outlet"):
        try:
            table=data["weighted"+suffix];minimum=data["waterMinimum"+suffix]
            require(math.isclose(next(reversed(table)),end,rel_tol=5e-11,abs_tol=1e-12),"TEACHING_PRESSURE_SECTION_END_REQUIRED")
            actual_end=next(reversed(table));cut,liquid=(tables.interval_samples(t,start,actual_end) for t in (table,minimum))
            require(set(cut)==set(liquid),"TEACHING_PRESSURE_LIQUID_SECTION_CLOCK_REQUIRED")
            lowest=min(v[0] for v in liquid.values());require(-1e-9<=lowest<=1+1e-9,"TEACHING_PRESSURE_SECTION_ALPHA_BOUNDS_REQUIRED")
            vapor=min(1.,max(0.,1-lowest))
            for instant,(ps,pt,u2,alpha) in cut.items():
                require(u2>=0 and -1e-9<=alpha<=1+1e-9 and liquid[instant][0]<=alpha+1e-9,"TEACHING_PRESSURE_SECTION_MOMENTS_REQUIRED")
                if vapor<=study["acceptance"]["liquidSectionVaporFractionLimit"]:
                    require(math.isclose(pt,ps+rho*u2/2,rel_tol=2e-5,abs_tol=2),"TEACHING_PRESSURE_ABSOLUTE_TOTAL_PRESSURE_REQUIRED")
            ps,pt,u2,_=tables.time_mean(table,start,actual_end);prefix=suffix.lower()
            run["measurements"].update({prefix+"TotalPressureAbsolutePa":pt,prefix+"ElevationM":0.,prefix+"MaximumVaporFraction":vapor})
            if suffix=="Inlet":
                require(vapor>study["acceptance"]["liquidSectionVaporFractionLimit"] or math.isclose(pt,pressure,rel_tol=2e-5,abs_tol=2),
                    "TEACHING_PRESSURE_SELECTED_INLET_TOTAL_PRESSURE_REQUIRED")
                run["measurements"].update(inletStaticPressureAbsolutePa=ps,inletMeanSquaredAbsoluteSpeedM2S2=u2)
        except (protocol.ProtocolError,OSError,KeyError,ValueError) as error:
            output["diagnostics"].append({"branch":suffix.lower()+"-section","reasonCode":str(error)[:500]})
    try:
        require(set(data)==set(roles),"TEACHING_PRESSURE_ALL_SAMPLERS_REQUIRED")
        clock,co,alpha_co=tables.parse_solver_log(logs["foamRun"])
        require(math.isclose(max(clock),end,rel_tol=5e-11,abs_tol=1e-12),"TEACHING_PRESSURE_NATIVE_CLOCK_END_REQUIRED")
        times=list(data["weightedInlet"]);require(all(list(t)==times for t in data.values()),"TEACHING_PRESSURE_MATCHED_NATIVE_SAMPLE_CLOCKS_REQUIRED")
        positive=[t for t in times if t>0]
        require(len(positive)==len(clock) and all(math.isclose(t,c,rel_tol=5e-10,abs_tol=1e-12) for t,c in zip(positive,clock)),
            "TEACHING_PRESSURE_NATIVE_CLOCK_TABLE_BINDING_REQUIRED")
        previous=0.
        for instant,row in clock.items():
            require(math.isclose(instant-previous,row["dt"],rel_tol=5e-8,abs_tol=1e-12),"TEACHING_PRESSURE_ACTUAL_DELTA_T_REQUIRED");previous=instant
        actual_end=times[-1];require(start>=times[0] and actual_end-start>=3*period-1e-12,"TEACHING_PRESSURE_THREE_SUPPORTED_PERIODS_REQUIRED")
        window={name:tables.interval_samples(t,start,actual_end) for name,t in data.items()};indexes=list(window["weightedInlet"])
        heads={t:[(window["weightedOutlet"][t][1]-window["weightedInlet"][t][1])/(rho*g)] for t in indexes}
        bins=[start+(actual_end-start)*i/7 for i in range(8)];head_bins=[tables.time_mean(heads,a,b)[0] for a,b in zip(bins,bins[1:])]
        inlet,outlet,reverse,mismatch=[],[],[],[]
        for instant in indexes:
            fi,fo=window["massFlowInlet"][instant][0],window["massFlowOutlet"][instant][0]
            mi,mo=window["massFlowMagnitudeInlet"][instant][0],window["massFlowMagnitudeOutlet"][instant][0]
            inlet.append(fi);outlet.append(fo);reverse.extend((tables.reverse_fraction(fi,mi,True),tables.reverse_fraction(fo,mo,False)))
            fr,fs=window["massFlowRotorInterface"][instant][0],window["massFlowStatorInterface"][instant][0]
            mr,ms=window["massFlowMagnitudeRotorInterface"][instant][0],window["massFlowMagnitudeStatorInterface"][instant][0]
            reverse.extend((tables.reverse_fraction(fr,mr,False),tables.reverse_fraction(fs,ms,True)))
            mismatch.append(abs(fr+fs)/max(abs(fr),abs(fs)))
        residuals={n:[] for n in ("p","U","k","omega")};continuity=[];actual_dt=[]
        for instant,row in clock.items():
            if not start<=instant<=actual_end+1e-12:continue
            actual_dt.append(row["dt"])
            require(row["continuity"] and len(row["outer"])==3 and {"k","omega"}<=set(row["outer"][-1]),
                "TEACHING_PRESSURE_ALL_OUTER_TRANSPORT_REQUIRED")
            continuity.extend(row["continuity"])
            for outer in row["outer"]:
                require({"p_rgh","Ux","Uy","Uz"}<=set(outer),"TEACHING_PRESSURE_FIRST_OUTER_RESIDUALS_REQUIRED")
                residuals["p"].append(outer["p_rgh"]);residuals["U"].append(max(outer[n] for n in ("Ux","Uy","Uz")))
                for name in ("k","omega"):
                    if name in outer:residuals[name].append(outer[name])
        require(len(actual_dt)>=5 and max(v[0] for v in continuity)<=maths.LIMITS["continuityLocalLimit"],"TEACHING_PRESSURE_LOCAL_CONTINUITY_REQUIRED")
        lowest=min(v[0] for v in window["globalWaterMinimum"].values());highest=max(v[0] for v in window["globalWaterMaximum"].values())
        require(-1e-9<=lowest<=highest<=1+1e-9 and all(window["globalWaterMinimum"][t][0]<=window["globalWaterMaximum"][t][0] for t in indexes),
            "TEACHING_PRESSURE_GLOBAL_ALPHA_BOUNDS_REQUIRED")
        mean_in=tables.time_mean(data["massFlowInlet"],start,actual_end)[0];mean_out=tables.time_mean(data["massFlowOutlet"],start,actual_end)[0]
        q.update(outwardInletMassFlowKgS=mean_in,outwardOutletMassFlowKgS=mean_out,reverseFlowFraction=max(reverse),
            finalResidualHistory=residuals,residualDefinition="initial-solver-residual-per-outer-iteration-final-window",
            samplingWindowDefinition="final-converged-window",finalFlowRelativeSpan=max((max(v)-min(v))/abs(m) for v,m in ((inlet,mean_in),(outlet,mean_out))),
            headHistoryM=head_bins,minimumVaporFraction=max(0.,1-highest),maximumVaporFraction=min(1.,1-lowest),
            observedMaximumTimeStepS=max(actual_dt),observedMaxCo=co,observedMaxAlphaCo=alpha_co)
        pmin=min(v[0] for v in window["globalPressureMinimum"].values());require(pmin>0,"TEACHING_PRESSURE_POSITIVE_ABSOLUTE_PRESSURE_REQUIRED")
        run["measurements"]["minimumStaticPressureAbsolutePa"]=pmin
        require(co<=.5 and alpha_co<=.25 and max(actual_dt)<=manifest["temporalPlan"]["maximumTimeStepS"]*(1+1e-9),
            "TEACHING_PRESSURE_COURANT_DELTA_T_REQUIRED")
        require(max(mismatch)<=maths.LIMITS["interfaceFluxRelativeLimit"],"TEACHING_PRESSURE_INTERFACE_FLUX_CONSERVATION_REQUIRED")
        coverage=native.parse_coverage(logs["checkMesh"]+"\n"+logs["foamRun"])
        require(all(v[0]>=.99 and v[1]>=.999 and v[2]<=1.01 for pair in coverage for v in pair.values()),"TEACHING_PRESSURE_NATIVE_NCC_COVERAGE_REQUIRED")
        extrema=[v[0] for v in heads.values()]
        require((max(extrema)-min(extrema))/abs(math.fsum(head_bins)/len(head_bins))<=study["acceptance"]["headWindowRelativeLimit"],
            "TEACHING_PRESSURE_NATIVE_HEAD_STATIONARITY_REQUIRED")
        output.update(interfaceCoverageVerified=True,interfaceFluxRelativeMismatch=max(mismatch),nativeCoupledCoverage=coverage,
            minimumStaticPressureAbsolutePa=pmin,rawGlobalWaterMinimum=lowest,rawGlobalWaterMaximum=highest,
            statisticalWindowS=[start,actual_end],continuityHistory=continuity,nativeHeadSampleExtremaM=[min(extrema),max(extrema)])
    except (protocol.ProtocolError,OSError,ValueError,KeyError,ZeroDivisionError) as error:
        output["diagnostics"].append({"branch":"pressure-point-numerical-quality","reasonCode":str(error)[:500]})
    run["nativeEvidence"]["sha256"]=evidence_hash(records);run["controlledVariationProof"]["samplingEvidenceSha256"]=evidence_hash(records)
    projected=protocol.reduce_run(study,run)
    output.update(nativeObservation=run,protocolReduction=projected,stages=projected["stages"],npshaDomainStatus=projected["npshaDomainStatus"])
    output["diagnostics"].extend(projected["diagnostics"])
    output["pressurePointObservationQualified"]=not output["diagnostics"] and projected["status"]=="QualifiedSuppliedObservation"


def execution_evidence(root,manifest,manifest_row,study,records):
    import run_teaching_pump_pressure as runner
    helper=preparer();execution,execution_row=record(root,EXECUTION_FILE)
    study_raw,study_row=read(root,STUDY_FILE,native.MAX_RECORD)
    require(study_raw==encoded(study),"TEACHING_PRESSURE_CURRENT_PREEXECUTION_STUDY_REQUIRED")
    true_flags=("actualCfdRunVerified","simulationExecuted","nativeCheckMeshVerified","nativeGeometryQualityVerified",
        "physicalWallTypesVerified","coupledInterfaceCoverageVerified","sourceActivePhaseH0NumericallyQualified",
        "stationaryMrfRelaxationExecuted","nativeSolverCompleted","cavitationModelExecuted","finiteNativeEndFieldsVerified",
        "inputArtifactsImmutable","sourceReferenceImmutable")
    require(execution.get("schemaVersion")==EXECUTION_SCHEMA and execution.get("status")==runner.STATUS
        and execution.get("sourceGeometryRoute")==ROUTE and execution.get("mode")==helper.MODE
        and execution.get("physicalModel")=="steady-cavitation-MRF" and execution.get("githubRepository")=="simcalc/PyAnsys"
        and isinstance(execution.get("githubRunId"),str) and re.fullmatch(r"[1-9][0-9]{0,19}",execution["githubRunId"])
        and isinstance(execution.get("githubSourceCommit"),str) and re.fullmatch(r"[0-9a-f]{40}",execution["githubSourceCommit"])
        and all(execution.get(k) is True for k in true_flags) and all(execution.get(k) is False for k in runner.FALSE_FLAGS)
        and execution.get("preparationReportSha256")==manifest["preparationReportSha256"]
        and execution.get("runtimeIdentitySha256")==manifest["runtimeIdentitySha256"]
        and execution.get("inputManifestSha256")==manifest_row["sha256"] and execution.get("studySha256")==study_row["sha256"]
        and execution.get("sourceActivePhaseEvidenceSha256")==manifest["sourceActivePhaseEvidenceSha256"]
        and execution.get("sourceReferenceInputManifestSha256")==manifest["sourceReferenceInputManifestSha256"]
        and execution.get("sourceActivePhaseHeadM")==manifest["sourceActivePhaseHeadM"]
        and execution.get("sourceReferencePressureAbsolutePa")==manifest["sourceReferencePressureAbsolutePa"]
        and execution.get("authoredCavitationModelSha256")==manifest["controls"]["cavitationModelSha256"]
        and execution.get("pressureAbsolutePa")==execution.get("inletAbsoluteTotalPressurePa")==manifest["pressureLevelAbsolutePa"]
        and execution.get("absolutePressureMapping")==manifest["absolutePressureMapping"]
        and execution.get("temporalPlan")==manifest["temporalPlan"],"TEACHING_PRESSURE_ACTUAL_BOUND_NATIVE_EXECUTION_REQUIRED")
    limits=execution.get("limits")
    require(isinstance(limits,dict) and set(limits)==set(runner.LIMITS)
        and all(limits[k]==v and type(limits[k]) is type(v) for k,v in runner.LIMITS.items())
        and 0<=protocol.number(execution.get("elapsedSeconds"),nonnegative=True)<runner.LIMITS["maximumSeconds"]
        and type(execution.get("workspaceBytes")) is int and 0<execution["workspaceBytes"]<=runner.LIMITS["maximumWorkspaceBytes"],
        "TEACHING_PRESSURE_EXACT_NATIVE_RESOURCES_REQUIRED")
    add_record(records,execution_row);add_record(records,study_row)
    stages=execution.get("stages")
    require(isinstance(stages,list) and len(stages)==2 and [s.get("program") for s in stages]==list(PROGRAMS),"TEACHING_PRESSURE_TWO_NATIVE_STAGES_REQUIRED")
    runtime=record(root,RUNTIME_FILE)[0];prep=record(root,helper.REPORT)[0]
    commands=helper.historical_commands(helper.reference_case.historical_command_root(prep))
    logs={};log_rows=[]
    for i,stage in enumerate(stages):
        require(type(stage.get("exitCode")) is int and stage["exitCode"]==0 and stage.get("log")==f"{i:02d}-{PROGRAMS[i]}.log"
            and protocol.number(stage.get("elapsedSeconds"),nonnegative=True)<runner.LIMITS["maximumSeconds"],"TEACHING_PRESSURE_NATIVE_STAGE_COMPLETION_REQUIRED")
        raw,row=read(root,"logs/"+stage["log"],runner.LIMITS["maximumLogBytes"])
        require(stage.get("logBytes")==row["bytes"] and stage.get("logSha256")==row["sha256"],"TEACHING_PRESSURE_CURRENT_NATIVE_LOG_REQUIRED")
        text=raw.decode("utf-8");helper.reference_case.geometry.completed_log(text)
        binary=next(b for b in runtime["binaries"] if b["program"]==PROGRAMS[i])
        require(re.findall(r"(?m)^Build\s*:\s*(.*?)\s*$",text)==["12-86e126a7bc4d"]
            and re.findall(r"(?m)^nProcs\s*:\s*(.*?)\s*$",text)==["1"]
            and re.findall(r"(?m)^Exec\s*:\s*(.*?)\s*$",text)==[" ".join([binary["path"],*commands[i][1:]])],
            "TEACHING_PRESSURE_ACTUAL_NATIVE_ARGV_BUILD_REQUIRED")
        logs[PROGRAMS[i]]=text;log_rows.append(row);add_record(records,row)
    require(execution.get("retainedNativeLogRecords")==log_rows
        and math.fsum(s["elapsedSeconds"] for s in stages)<=execution["elapsedSeconds"]+.01,"TEACHING_PRESSURE_COMPLETE_LOG_TIMING_REQUIRED")
    require(re.findall(r"(?m)^Selecting cavitation model (\w+)\s*$",logs["foamRun"])==["SchnerrSauer"]
        and not re.search(r"Selecting solidBodyMotionFunction|Selecting motion solver",logs["foamRun"]),"TEACHING_PRESSURE_ACTUAL_FIXED_MRF_ACTIVE_MODEL_REQUIRED")
    runner.phase.baseline.geometry.verify_region_count(logs["checkMesh"],2)
    merged=native.case_tool.verify_merged_mesh(root/"case",prep["components"],root/"logs/00-checkMesh.log")
    mesh=native.parse_mesh_quality(logs["checkMesh"])
    require(merged==execution.get("mergedNativeMeshEvidence") and mesh==execution.get("nativeMeshQuality")
        and mesh["meshCellCount"]==merged["mergedCellCount"],"TEACHING_PRESSURE_CURRENT_SOURCE_ALIGNED_MESH_REQUIRED")
    final,_=runner.final_fields(root,manifest["temporalPlan"],mesh["meshCellCount"],logs["foamRun"])
    require(final==execution.get("finalNativeFieldRecords")
        and math.isclose(protocol.number(execution.get("nativeEndTimeS"),positive=True),manifest["temporalPlan"]["endTimeS"],rel_tol=5e-11,abs_tol=1e-12),
        "TEACHING_PRESSURE_SEVEN_CURRENT_ENDPOINT_FIELDS_REQUIRED")
    endpoint={Path(r["path"]).name:r for r in final}
    pair=reference_reducer.absolute_pressure_reconstruction(read(root,endpoint["p"]["path"],native.MAX_FIELD)[0],
        read(root,endpoint["p_rgh"]["path"],native.MAX_FIELD)[0],mesh["meshCellCount"])
    require(pair==execution.get("endpointAbsolutePressureReconstruction"),"TEACHING_PRESSURE_ACTUAL_PA_G0_RECONSTRUCTION_REQUIRED")
    for row in final:add_record(records,{k:row[k] for k in ("path","bytes","sha256")})
    sampling=runner.sampling_records(root);diagnostics=runner.ncc_diagnostic_records(root)
    require(sampling==execution.get("nativeSamplingRecords") and diagnostics==execution.get("nativeNccDiagnosticRecords"),
        "TEACHING_PRESSURE_COMPLETE_SAMPLING_NCC_INVENTORY_REQUIRED")
    roles=maths.sampler_roles()
    for row in sampling:
        m=re.fullmatch(r"case/postProcessing/([A-Za-z0-9_-]+)/("+fields.TIME_NAME+r")/(surfaceFieldValue\.dat|volFieldValue\.dat|yPlus\.dat)",row["path"])
        require(m and 0<=float(m[2])<=manifest["temporalPlan"]["endTimeS"] and (m[1] in roles and m[3]==("surfaceFieldValue.dat" if roles[m[1]][1] else "volFieldValue.dat")
            or m[1]=="wallYPlus" and m[3]=="yPlus.dat"),"TEACHING_PRESSURE_EXACT_NATIVE_SAMPLER_ROLE_REQUIRED")
        add_record(records,row)
    for row in diagnostics:add_record(records,row)
    near=runner.near_points_evidence(root)
    require(execution.get("nativeCheckMeshDiagnosticRecords",[])==near["files"] and execution.get("nativeNearPointsEvidence")==near["evidence"],
        "TEACHING_PRESSURE_CURRENT_NEAR_POINT_EVIDENCE_REQUIRED")
    generated=helper.verify_output(root,after_native_execution=True)
    require(execution.get("nativeGeneratedDataRecords",[])==generated["nativeGeneratedDataRecords"]
        and execution.get("nativeGeneratedDataEvidence",[])==generated["nativeGeneratedDataEvidence"],"TEACHING_PRESSURE_CURRENT_NATIVE_GENERATED_DATA_REQUIRED")
    for row in near["files"]+generated["nativeGeneratedDataRecords"]:
        require(read(root,row["path"],native.MAX_FIELD)[1]==row,"TEACHING_PRESSURE_CURRENT_NATIVE_DIAGNOSTIC_BYTES_REQUIRED");add_record(records,row)
    current_bytes=runner.workspace_size(root)-execution_row["bytes"]
    if (root/REPORT_FILE).exists():current_bytes-=len(native.case_tool.read_regular(root/REPORT_FILE,MAX_REDUCTION_BYTES))
    require(current_bytes==execution["workspaceBytes"],"TEACHING_PRESSURE_CURRENT_WORKSPACE_MEASUREMENT_REQUIRED")
    extrema={"pMinimumAbsolutePa":endpoint["p"]["fieldCheck"]["minimum"],"waterMinimum":endpoint["alpha.water"]["fieldCheck"]["minimum"],
        "waterMaximum":endpoint["alpha.water"]["fieldCheck"]["maximum"],"allInternalCellsChecked":mesh["meshCellCount"]}
    return execution,logs,mesh,pair,extrema


def endpoint_reconciliation(root,manifest,extrema,records):
    for name,columns,key in (("globalPressureMinimum",("min(p)",),"pMinimumAbsolutePa"),
        ("globalWaterMinimum",("min(alpha.water)",),"waterMinimum"),("globalWaterMaximum",("max(alpha.water)",),"waterMaximum")):
        data,row=tables.sampler(root,name,columns,None);add_record(records,row);time,value=next(reversed(data.items()))
        require(math.isclose(time,manifest["temporalPlan"]["endTimeS"],rel_tol=5e-11,abs_tol=1e-12)
            and math.isclose(value[0],extrema[key],rel_tol=2e-8,abs_tol=2e-4 if key.startswith("p") else 2e-8),
            "TEACHING_PRESSURE_ENDPOINT_GLOBAL_FIELD_TABLE_CONSISTENCY_REQUIRED")


def reduce_pressure(execution_directory):
    output={"schemaVersion":SCHEMA,"sourceGeometryRoute":ROUTE,"status":"ActualPressurePointEvidenceRequired",
        **{k:False for k in FALSE_CLAIMS},"actualCfdRunVerified":False,"simulationExecuted":False,
        "pressurePointObservationQualified":False,"numericalPressurePointQualified":False,"npsh3BracketEligible":False,"pressureBracketVerified":False,
        "npshrCalculated":False,"npsh3Calculated":False,"cavitationSweepExecuted":False,"phaseH0Eligible":False,
        "interfaceCoverageVerified":False,"endpointGlobalFieldTableConsistencyVerified":False,"stages":{},"diagnostics":[],
        "qualificationScope":"One numerical active-phase pressure observation on the exact authored SI mesh and pressure-only reference family",
        "claimBoundary":"A qualified cavitating point is not an active H0 or NPSHr result. Only an actual source-bracket reduction can calculate educational NPSH3; physical/refinement/restart qualification remains separate."}
    records={}
    try:
        root=native.case_tool.regular_directory(execution_directory);bound=build_manifest(root,after_native_execution=True)
        manifest,row=record(root,MANIFEST_FILE)
        require(manifest==bound["manifest"] and manifest.get("schemaVersion")==MANIFEST_SCHEMA,"TEACHING_PRESSURE_CURRENT_PREEXECUTION_MANIFEST_REQUIRED")
        study=bound["study"];output.update(manifest=manifest,inputManifest=manifest,study=study,nativeInputManifestSha256=row["sha256"])
        add_record(records,row)
        for declared in manifest["files"]:
            actual=read(root,declared["path"],native.MAX_FIELD)[1]
            require(actual=={k:declared[k] for k in ("path","bytes","sha256")},"TEACHING_PRESSURE_CURRENT_MANIFEST_BYTES_REQUIRED");add_record(records,actual)
        execution,logs,mesh,pair,extrema=execution_evidence(root,manifest,row,study,records)
        output.update(actualCfdRunVerified=True,simulationExecuted=True,status="NativePressureExecutionVerifiedAcceptanceRequired",
            endpointAbsolutePressureReconstruction=pair,endpointInternalFieldExtrema=extrema)
        numerical_observation(root,output,manifest,study,execution,logs,mesh,records)
        try:endpoint_reconciliation(root,manifest,extrema,records);output["endpointGlobalFieldTableConsistencyVerified"]=True
        except (protocol.ProtocolError,OSError,KeyError,ValueError) as error:
            output["diagnostics"].append({"branch":"endpoint-global-fields","reasonCode":str(error)[:500]})
        accepted=not output["diagnostics"] and output["pressurePointObservationQualified"]
        output.update(pressurePointObservationQualified=accepted,numericalPressurePointQualified=accepted,
            npsh3BracketEligible=accepted and output.get("npshaDomainStatus")!="FLASHING_CAVITATION_RISK",
            status="AuthoredPressurePointNumericallyQualified" if accepted else "PressurePointNumericalAcceptanceRequired")
        require(all(read(root,r["path"],native.MAX_FIELD)[1]==r for r in records.values()),"TEACHING_PRESSURE_POSTREDUCTION_IMMUTABILITY_REQUIRED")
    except (protocol.ProtocolError,OSError,ValueError,KeyError,TypeError,UnicodeError) as error:
        output.update(pressurePointObservationQualified=False,numericalPressurePointQualified=False,npsh3BracketEligible=False)
        output["diagnostics"].append({"branch":"native-pressure-evidence","reasonCode":str(error)[:500]})
        if output["actualCfdRunVerified"]:output["status"]="PressurePointNumericalAcceptanceRequired"
        if str(error)=="TEACHING_PRESSURE_POSTREDUCTION_IMMUTABILITY_REQUIRED":
            output.update(actualCfdRunVerified=False,simulationExecuted=False,status="NativeEvidenceChangedDuringReduction",stages={})
            output.pop("nativeObservation",None);output.pop("protocolReduction",None)
    output["nativeEvidenceRecords"]=sorted(records.values(),key=lambda r:r["path"])
    return output


def aggregate_pressure_sweep(reference_directory,point_directories):
    """Use the actual accepted H0 as one measured high-pressure bracket point."""
    output={"schemaVersion":SWEEP_SCHEMA,"sourceGeometryRoute":ROUTE,"status":"ActualPressureBracketRequired",
        **{k:False for k in FALSE_CLAIMS},"actualCfdRunVerified":False,"simulationExecuted":False,
        "cavitationSweepExecuted":False,"numericalPressureSweepAccepted":False,"pressureBracketVerified":False,
        "npshrCalculated":False,"npsh3Calculated":False,"pressureResolutionVerified":False,
        "stages":{},"diagnostics":[],"qualifiedPointRecords":[],"excludedPointRecords":[],
        "quantityLabel":"CFD-derived educational NPSH3 for this authored SI pump model; not manufacturer-published",
        "claimBoundary":"Actual bracket interpolation does not establish pressure resolution, mesh/time independence, wall resolution, physical validation or certified pump NPSHr"}
    try:
        helper=preparer();reference=helper.verify_reference(reference_directory)
        family=helper.reference_family(reference);controls=controls_from_groups(family)
        study=study_packet(reference["study"],controls);projection=reference_projection(reference,family,study)
        points=list(point_directories)
        require(1<=len(points)<=32,"TEACHING_PRESSURE_AT_LEAST_ONE_ACTUAL_LOWER_POINT_REQUIRED")
        roots=[native.case_tool.regular_directory(p) for p in points]
        require(len({p.resolve() for p in roots})==len(roots),"TEACHING_PRESSURE_DISTINCT_POINT_DIRECTORIES_REQUIRED")
        runs=[projection["nativeObservation"]];accepted=[];excluded=[]
        h0=reference["packet"]["activePhaseReferenceHeadM"]
        for root in roots:
            packet=reduce_pressure(root)
            require(packet["schemaVersion"]==SCHEMA and packet["actualCfdRunVerified"] is True
                and packet["numericalPressurePointQualified"] is True and packet["diagnostics"]==[],
                "TEACHING_PRESSURE_ACTUAL_QUALIFIED_POINT_REQUIRED")
            manifest=packet["manifest"]
            source=manifest["sourceReferenceFamilyProjection"]
            require(packet["study"]==study and manifest["controls"]==controls
                and manifest["sourceReferenceInputManifestSha256"]==reference["packet"]["nativeInputManifestSha256"]
                and manifest["sourceActivePhaseEvidenceSha256"]==reference["packet"]["nativeObservation"]["nativeEvidence"]["sha256"]
                and source["originalActualNativeInputManifestSha256"]==projection["projectionEvidence"]["originalActualNativeInputManifestSha256"]
                and source["pressureNormalizationProof"]==family["pressureNormalizationProof"]
                and source["runtimeFamily"]==family["runtimeFamily"] and source["structuralSignatureRows"]==family["groups"],
                "TEACHING_PRESSURE_COMMON_CURRENT_REFERENCE_FAMILY_REQUIRED")
            pressure=packet["nativeObservation"]["pressureLevelAbsolutePa"]
            require(pressure<reference["packet"]["nativeObservation"]["pressureLevelAbsolutePa"],
                "TEACHING_PRESSURE_LOWER_ACTUAL_PRESSURE_REQUIRED")
            head=packet["stages"]["head"]["result"]["value"]
            point={"actualNativeInputManifestSha256":packet["nativeInputManifestSha256"],
                "nativeEvidenceSha256":packet["nativeObservation"]["nativeEvidence"]["sha256"],
                "pressureAbsolutePa":pressure,"headM":head,
                "npshaM":packet["stages"]["npsha"]["result"]["value"],
                "npshaDomainStatus":packet["npshaDomainStatus"],"npsh3BracketEligible":packet["npsh3BracketEligible"],
                "headRatioStage":protocol.stage({"headM":head,"activePhaseReferenceHeadM":h0},
                    "R_head = H/H0",f"{head}/{h0}",head/h0,"1"),
                "nativeEvidenceRecords":packet["nativeEvidenceRecords"],"pressureNormalizationProof":manifest["pressureNormalizationProof"]}
            accepted.append(point)
            if packet["npsh3BracketEligible"]:
                runs.append(packet["nativeObservation"])
            else:
                excluded.append({"actualNativeInputManifestSha256":packet["nativeInputManifestSha256"],
                    "pressureAbsolutePa":pressure,"npshaDomainStatus":packet["npshaDomainStatus"],
                    "reasonCode":"TEACHING_PRESSURE_LIQUID_SUCTION_DOMAIN_REQUIRED",
                    "action":"Retain the measured CFD/head/NPSHA. Use a qualified liquid-suction point for NPSH3 bracketing; positive kinetic head does not remove static-pressure flashing."})
        output.update(study=study,sourceActivePhaseReference=projection,sourceReferenceAcceptanceFile=reference["reportFile"],
            qualifiedPointRecords=accepted,excludedPointRecords=excluded,sourceReferenceIncludedAsMeasuredHighPressurePoint=True,
            actualCfdRunVerified=True,simulationExecuted=True,cavitationSweepExecuted=True,status="PressureSweepNumericalAcceptanceRequired")
        # Revalidate every measured point, including excluded suction-domain points.
        require(helper.verify_reference(reference_directory)["packet"]==reference["packet"],"TEACHING_PRESSURE_REFERENCE_CHANGED_DURING_SWEEP_REQUIRED")
        for root,row in zip(roots,accepted):
            require(all(read(root,r["path"],native.MAX_FIELD)[1]==r for r in row["nativeEvidenceRecords"]),
                "TEACHING_PRESSURE_POINT_CHANGED_DURING_SWEEP_REQUIRED")
        require(len(runs)>=2,"TEACHING_PRESSURE_LIQUID_SUCTION_DOMAIN_REQUIRED")
        reduction=protocol.pressure_sweep(study,projection["nativeObservation"],runs)
        output.update(numericalPressureSweepAccepted=True,protocolPressureSweep=reduction)
        if reduction["status"]=="BracketedReduction":
            stage=reduction["stages"]["npsh3"];low=stage["input"]["NPSHALowM"];high=stage["input"]["NPSHAHighM"]
            output.update(status="AuthoredNpsh3CalculatedInsideActualBracket",pressureBracketVerified=True,npshrCalculated=True,
                npsh3Calculated=True,stages=reduction["stages"],npsh3M=stage["result"]["value"],
                actualNpshaBracketM=[low,high],bracketWidthM=high-low,
                uncertainty={"numericalConfidenceIntervalAvailable":False,"pressureResolutionVerified":False,
                    "observedBracketWidthM":high-low,"basis":"Measured source bracket only; linear interpolation is conditional. More actual pressure points are required to narrow the crossing; no uncertainty magnitude is invented."})
        else:
            output.update(status="PressureCrossingNotYetBracketed",stages={},npsh3M=None,
                correctiveAction=reduction["action"])
        # Revalidate current source/point bytes at the read-only report boundary.
        require(helper.verify_reference(reference_directory)["packet"]==reference["packet"],"TEACHING_PRESSURE_REFERENCE_CHANGED_DURING_SWEEP_REQUIRED")
        for root,row in zip(roots,accepted):
            require(all(read(root,r["path"],native.MAX_FIELD)[1]==r for r in row["nativeEvidenceRecords"]),
                "TEACHING_PRESSURE_POINT_CHANGED_DURING_SWEEP_REQUIRED")
    except (protocol.ProtocolError,OSError,ValueError,KeyError,TypeError,UnicodeError) as error:
        output.update(numericalPressureSweepAccepted=False,pressureBracketVerified=False,npshrCalculated=False,npsh3Calculated=False,stages={})
        output.pop("npsh3M",None);output.pop("actualNpshaBracketM",None);output.pop("bracketWidthM",None);output.pop("uncertainty",None)
        output["diagnostics"].append({"branch":"actual-pressure-sweep","reasonCode":str(error)[:500]})
        if "CHANGED_DURING_SWEEP" in str(error):
            output.update(actualCfdRunVerified=False,simulationExecuted=False,cavitationSweepExecuted=False,status="NativeEvidenceChangedDuringSweep")
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-directory",type=Path)
    parser.add_argument("--reference-directory",type=Path)
    parser.add_argument("--point-execution-directory",action="append",type=Path,default=[])
    parser.add_argument("--output-directory",type=Path)
    args=parser.parse_args();helper=preparer()
    if args.execution_directory is not None:
        require(args.reference_directory is None and not args.point_execution_directory and args.output_directory is None,
            "TEACHING_PRESSURE_CHOOSE_ONE_POINT_OR_SWEEP_MODE_REQUIRED")
        root=helper.safety.scope_path(args.execution_directory);result=reduce_pressure(root);target=root/REPORT_FILE
        success=result["actualCfdRunVerified"] and result["numericalPressurePointQualified"]
    else:
        require(args.reference_directory is not None and args.output_directory is not None and args.point_execution_directory,
            "TEACHING_PRESSURE_ACTUAL_SOURCE_POINTS_AND_FRESH_REPORT_DIRECTORY_REQUIRED")
        root=helper.safety.scope_path(args.output_directory,fresh=True)
        result=aggregate_pressure_sweep(args.reference_directory,args.point_execution_directory);root.mkdir();target=root/SWEEP_FILE
        success=result["actualCfdRunVerified"] and result["pressureBracketVerified"]
    raw=encoded(result);require(len(raw)<=MAX_REDUCTION_BYTES,"TEACHING_PRESSURE_BOUNDED_REDUCTION_REQUIRED")
    native.case_tool.write_exclusive(target,raw)
    print(json.dumps({k:result[k] for k in ("status","actualCfdRunVerified","npshrCalculated","npsh3Qualified","mainApplicationWrites")}))
    return 0 if success else 2


if __name__=="__main__":raise SystemExit(main())
