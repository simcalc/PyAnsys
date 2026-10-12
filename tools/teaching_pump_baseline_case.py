"""Author a conditional MRF baseline for new SI classroom pump CAD only.

All actual geometry evidence is replayed before physics inputs are copied. This
preparer launches no process and inherits no flow, head or baseline result.
"""
from __future__ import annotations
import argparse
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
import teaching_pump_native_case as geometry_case
import run_teaching_pump_geometry as geometry_runner
import frozen_rotor_original_case as physics
import frozen_rotor_snappy_baseline_case as fields
import teaching_pump_outlet_profile as outlet

base, safety, geometry = geometry_case.base, geometry_case.safety, geometry_case.native_geometry
SCHEMA = "untirta.teaching-pump-baseline-preparation.v1"
REPORT = "teaching-baseline-preparation.json"
ROUTE = "CLASSROOM_AUTHORED_GEOMETRY"
MAX_REPORT, MAX_FILE, MAX_SOURCE_RECORDS, MAX_SOURCE_BYTES, MAX_WORKSPACE = 128*1024,128*1024**2,320,512*1024**2,1024**3
MESH_FILES = geometry_runner.MESH_FILES
PARAMETERS = {"speedRpm":1450.,"massFlowKgS":90.,"densityKgM3":998.2,"viscosityPaS":.001002,
    "temperatureK":293.15,"vaporPressurePa":2339.,"gravityMS2":9.81}
FALSE_FLAGS = ("commandExecutionPerformedByThisTool","simulationExecuted","actualCfdRunVerified","qualifiedBaseline",
    "numericalBaselineQualified","preliminaryBaselineQualified","noncavitatingBaselineVerified","wallResolutionVerified",
    "yPlusResolutionQualified","meshConvergenceVerified","timeStepConvergenceVerified","physicalValidationVerified",
    "cavitationModelExecuted","cavitationSweepExecuted","npshrCalculated","npsh3Qualified","physicalTransientExecuted",
    "restartVerified","manufacturerPublishedGeometry","originalFluentGeometryReproduced","baselineFieldsCopied","mainApplicationWrites")
LIMITS = {"maximumSeconds":1200,"maximumCpus":1,"maximumMemoryBytes":4*1024**3,"maximumWorkspaceBytes":MAX_WORKSPACE,
    "maximumFileBytes":MAX_FILE,"maximumReportBytes":MAX_REPORT,"maximumLogBytes":4*1024**2,"maximumSourceRecords":MAX_SOURCE_RECORDS}
NUMERICAL_PROFILE = {"id":"teaching-upwind-pressure-gradient-stabilization-v2","convection":"bounded Gauss upwind for U, k and omega",
    "convectiveOrder":1,"pressureLinearTolerance":1e-8,"pressureRelativeTolerance":0.,"pressureMaximumIterations":100,
    "transportLinearTolerance":1e-7,"transportRelativeTolerance":.01,"transportMinimumIterations":1,"transportMaximumIterations":100,
    "pressureRelaxation":.2,"transportRelaxation":.3,"steadyIterationCount":500,
    "pressureGradient":{"field":"p","scheme":"cellLimited Gauss linear 1","limiterCoefficient":1,
        "basis":"Native cell/neighbour and coupled-patch bounds limit extrapolated face values by scaling the pressure gradient; no stored pressure or velocity field is clipped.",
        "nativeFieldClippingPerformed":False},
    "velocityGradient":"cellLimited Gauss linear 1","velocityGradientChanged":False,
    "basis":"A predeclared fresh first-order candidate after run38197674270 reached500/End with the correct uniform outlet but retained large near-cap owner velocity and failed pressure residual, head stationarity and liquid-domain criteria. The new delta is only the pressure gradient scheme; this is not a proven diagnosis or convergence prediction. Physical BCs, operating inputs, mesh,500 iterations,1200s budget and acceptance criteria are unchanged.",
    "accuracyQualified":False,"convergencePredicted":False,"priorRejectedRunId":"38197674270",
    "priorNumericalProfileId":"teaching-upwind-stabilization-v1",
    "sources":["https://github.com/OpenFOAM/OpenFOAM-12/blob/86e126a7bc4dbfaed949bbc58c9f0fdd67acc059/src/finiteVolume/interpolation/surfaceInterpolation/limitedSchemes/upwind/upwind.H",
        "https://github.com/OpenFOAM/OpenFOAM-12/blob/86e126a7bc4dbfaed949bbc58c9f0fdd67acc059/src/finiteVolume/finiteVolume/convectionSchemes/boundedConvectionScheme/boundedConvectionScheme.C",
        "https://github.com/OpenFOAM/OpenFOAM-12/blob/86e126a7bc4dbfaed949bbc58c9f0fdd67acc059/src/MomentumTransportModels/momentumTransportModels/Base/kOmegaSST/kOmegaSSTBase.C",
        "https://github.com/OpenFOAM/OpenFOAM-12/blob/86e126a7bc4dbfaed949bbc58c9f0fdd67acc059/src/finiteVolume/finiteVolume/gradSchemes/limitedGradSchemes/cellLimitedGrad/cellLimitedGrad.C",
        "https://github.com/OpenFOAM/OpenFOAM-12/blob/86e126a7bc4dbfaed949bbc58c9f0fdd67acc059/applications/modules/incompressibleFluid/momentumPredictor.C",
        "https://github.com/OpenFOAM/OpenFOAM-12/blob/86e126a7bc4dbfaed949bbc58c9f0fdd67acc059/applications/modules/incompressibleFluid/correctPressure.C"]}
ASSUMPTIONS = {"provenance":ROUTE,"coordinateUnits":"SI_METRES","rhoKgM3":998.2,"muPaS":.001002,"gMS2":9.81,
    "speedRpm":1450.,"massFlowKgS":90.,"volumetricFlowM3S":90/998.2,"temperatureK":293.15,"vapourPressurePa":2339.,
    "axis":[0,0,1],"originM":[0,0,0],"solverGravityMS2":[0,0,0],"gravityInSolver":False,
    "externalTotalPressureAbsolutePa":300000.,"pressureSelectedBeforeExecution":True,
    "absolutePressureEquation":"p_static_absolute_Pa = 998.2*p_kinematic_m2s2 + 300000; reference added exactly once",
    "inletTurbulence":{"kM2S2":.375,"omegaSInverse":100.,"basis":"Prescribed classroom initial/section values; not measured."},
    "headDatum":"Declared common zero inlet/outlet section reference with native gravity neglected; actual section centroid elevations are not measured or verified.",
    "wallAdaptation":"Blade, impeller-hub and impeller-shroud use rotating MRFnoSlip; intake and volute walls use stationary noSlip. Inlet pressureInletOutletVelocity and intake noSlip impose actual stationary BCs; Foundation12 does not use a nonRotatingPatches MRF keyword.",
    "numericalStabilization":NUMERICAL_PROFILE,
    "outletVelocityProfile":outlet.PROFILE,
    "geometryBasis":"New authored CAD, exact current exported mesh and matched initial interface; not official Ansys/Grundfos geometry or measured pump performance."}
SAMPLING = {"flowUnits":"m3/s outward positive","weighting":"signed-outward-phi","velocityFrame":"absolute",
    "kineticMoment":"mean(U_absolute_squared)","pressureUnit":"Pa","pressureBasis":"absolute",
    "totalPressureField":"998.2*pKinematic + 0.5*998.2*mean(U_absolute_squared) + 300000",
    "staticPressureField":"998.2*pKinematic; add300000Pa exactly once in reduction","iterationIndexIsPhysicalTime":False}
CLAIM = "Fresh prescribed single-phase steady SST/MRF physics on independently accepted new classroom CAD, with an explicitly declared first-order upwind pressure-gradient candidate and a uniform normal outlet profile assumption bound to every actual rectangular outlet facet. Only grad(p) adds native cellLimited reconstruction; the existing grad(U), other controls and all acceptance criteria are retained, with no stored-field clipping. The outlet profile is not measured and neither numerical choice predicts convergence. Geometry acceptance and a numerical recipe do not supply flow/head/convergence or accuracy. No manufacturer performance, qualified baseline, phase H0, cavitation sweep, NPSHr, physical transient or numerical refinement is inherited."
encoded,record,json_file = geometry_case.encoded,geometry_case.record,geometry_case.json_file


class BaselineCaseIssue(ValueError):pass


class BaselineDeadlineIssue(BaselineCaseIssue):pass


_SOURCE_TRANSACTION = ContextVar("teaching_baseline_source_transaction", default=None)


class SourceProofTransaction:
    """Reuse a full proof only for unchanged bytes at one admitted source path.

    The value exists only inside this explicit context. Each path needs its own
    initial full replay; copied bytes never inherit another path's cached proof.
    Every boundary hashes the complete ordinary-file inventory and rechecks the
    installed runtime, including binaries outside the evidence directory.
    """
    def __init__(self, *source_paths, deadline=None):
        require(0 < len(source_paths) <= 2, "Declare the exact original/copied source paths for this transaction.")
        self._roots = {safety.scope_path(path).resolve() for path in source_paths}
        require(len(self._roots) == len(source_paths), "Source transaction paths must be distinct.")
        require(deadline is None or type(deadline) in (int,float) and math.isfinite(deadline), "Use one finite monotonic transaction deadline.")
        self._deadline, self._proofs, self._token = deadline, {}, None

    def check_deadline(self):
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise BaselineDeadlineIssue("The baseline deadline expired during source evidence verification.")

    def __enter__(self):
        require(self._token is None and _SOURCE_TRANSACTION.get() is None, "A source proof transaction must be fresh and cannot nest.")
        self.check_deadline(); self._token = _SOURCE_TRANSACTION.set(self)
        return self

    def __exit__(self, *_):
        _SOURCE_TRANSACTION.reset(self._token); self._token = None; self._proofs.clear()

    def inventory(self, root):
        files=source_inventory(root);directories=[];count=0
        for path in sorted(root.rglob("*")):
            count+=1
            require(count<=2048 and len(path.relative_to(root).parts)<=8, "Bound every source directory and file at the transaction boundary.")
            if path.is_dir():directories.append(path.relative_to(root).as_posix())
        return files,tuple(directories)

    def verify(self, root):
        require(self._token is not None and root.resolve() in self._roots, "Never reuse a source proof at a different/unadmitted source path.")
        self.check_deadline(); before = self.inventory(root); self.check_deadline()
        cached = self._proofs.get(root.resolve())
        if cached is None:
            proof = _validate_source(root)
            require(proof["sourceFileRecords"] == before[0], "Source bytes changed while constructing their full proof.")
        else:
            require(before == cached["inventory"], "Source inventory changed after its transaction proof; no stale proof may be reused.")
            identity, _ = json_file(root, geometry_runner.RUNTIME)
            validate_runtime(identity)
            proof = cached["proof"]
        self.check_deadline(); after = self.inventory(root); self.check_deadline()
        require(after == before, "Source bytes changed during the transaction boundary check.")
        if cached is None: self._proofs[root.resolve()] = {"proof":deepcopy(proof),"inventory":deepcopy(before)}
        return deepcopy(proof)


def require(condition,action):
    if not condition:raise BaselineCaseIssue(action)


def source_inventory(directory):
    root=base.regular_directory(directory);rows=[];total=0
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink() and not getattr(path,"is_junction",lambda:False)() and (path.is_file() or path.is_dir()),"Source geometry evidence must have ordinary paths without links.")
        if path.is_dir():continue
        relative=path.relative_to(root).as_posix();_,row=record(root,relative)
        require(len(rows)<MAX_SOURCE_RECORDS and len(path.relative_to(root).parts)<=8,"Retain a bounded complete source geometry inventory.")
        rows.append(row);total+=row["bytes"];require(total<=MAX_SOURCE_BYTES,"Source geometry evidence exceeds512MiB.")
    require(rows,"Actual complete source geometry evidence is required.")
    return rows


def validate_runtime(identity):
    fixed={"schemaVersion":geometry_runner.RUNTIME_SCHEMA,"openFoamFoundationVersion":12,"packageVersion":"20250206",
        "officialDebSha256":safety.PACKAGE_SHA,"expectedSourceCommit":safety.SOURCE_COMMIT,"mainApplicationWrites":False}
    require(isinstance(identity,dict) and set(identity)==set(fixed)|{"binaries"}
        and all(identity.get(k)==v and type(identity.get(k)) is type(v) for k,v in fixed.items()),"Require the exact typed geometry-only Foundation12 runtime identity.")
    rows=identity.get("binaries")
    require(isinstance(rows,list) and len(rows)==5 and {r.get("program") for r in rows}==set(geometry_runner.PROGRAMS),"Exactly the five source geometry binaries are required.")
    for row in rows:
        require(set(row)=={"program","path","bytes","sha256","packageOwnership"} and type(row["bytes"]) is int and 0<row["bytes"]<=MAX_FILE
            and re.fullmatch(r"[0-9a-f]{64}",row.get("sha256","")),"Actual native binary identity rows must be unique and bounded.")
        path=PurePosixPath(row["path"])
        require(path.is_absolute() and path.is_relative_to(PurePosixPath("/opt/openfoam12")) and ".." not in path.parts and "\\" not in row["path"]
            and path.name==row["program"] and row["packageOwnership"]=="openfoam12: "+row["path"],"Bind only the exact package-owned source native program.")
        raw=base.read_regular(Path(row["path"]),MAX_FILE)
        require(len(raw)==row["bytes"] and sha256(raw).hexdigest()==row["sha256"],"Installed native source binary bytes changed.")


def validate_source(directory):
    root=safety.scope_path(directory)
    transaction=_SOURCE_TRANSACTION.get()
    return transaction.verify(root) if transaction is not None else _validate_source(root)


def _validate_source(root):
    prepared=geometry_case.verify_output(root)
    # verify_output already replays validate_inputs and binds this exact report
    # and independently derived interface proof. Read its current metadata once
    # instead of reconstructing the same MSH/centroid/incidence proof again.
    authored_report,authored_row=json_file(root/"source-geometry", "teaching-geometry.json")
    require(authored_row["sha256"]==prepared["report"]["sourceGeometryReportSha256"], "The already replayed authored source report changed.")
    authored={"report":authored_report,"reportFile":authored_row,"components":deepcopy(prepared["components"]),
        "sourceFileRecords":[{**row,"path":row["path"].removeprefix("source-geometry/")}
            for row in prepared["report"]["sourceFileRecords"]],
        "commonInterfaceEvidence":deepcopy(prepared["report"]["commonInterfaceEvidence"])}
    execution,execution_row=json_file(root,geometry_runner.REPORT)
    runtime,runtime_row=json_file(root,geometry_runner.RUNTIME);validate_runtime(runtime)
    required_true=("commandExecutionPerformedByThisTool","nativeGeometryAccepted","nativeGeometryQualityVerified","openFoamConversionVerified",
        "physicalWallTypesVerified","coupledInterfaceCoverageVerified","inputArtifactsImmutable","sourceGeometryImmutable")
    require(execution.get("schemaVersion")==geometry_runner.SCHEMA and execution.get("sourceGeometryRoute")==ROUTE
        and execution.get("status")=="CLASSROOM_AUTHORED_NATIVE_GEOMETRY_ACCEPTED_PHYSICS_REQUIRED" and all(execution.get(k) is True for k in required_true)
        and all(execution.get(k) is False for k in geometry_case.FALSE_FLAGS if k not in required_true)
        and execution.get("limits")==geometry_case.LIMITS and "diagnostic" not in execution,"Actual complete classroom geometry acceptance is required; source quality or flow flags cannot be relabelled.")
    require(execution.get("preparationReportSha256")==prepared["reportFile"]["sha256"] and execution.get("nativeInputManifestSha256")==prepared["report"]["nativeInputManifestSha256"]
        and execution.get("nativeRuntimeIdentitySha256")==runtime_row["sha256"] and execution.get("sourceGeometryReportSha256")==authored["reportFile"]["sha256"]
        and execution.get("commonInterfaceEvidence")==authored["commonInterfaceEvidence"],"Actual geometry reports, runtime, manifest and interface must remain exactly bound.")
    source_report=authored["report"]
    require(isinstance(source_report.get("parameters"),dict) and all(source_report["parameters"].get(k)==v for k,v in PARAMETERS.items()),"This first physics lane requires the declared1450rpm/90kg/s/water SI operating basis.")
    require(execution.get("githubRepository")=="simcalc/PyAnsys" and isinstance(execution.get("githubRunId"),str)
        and re.fullmatch(r"[1-9][0-9]{0,19}",execution["githubRunId"]) and execution.get("githubSourceCommit")==source_report.get("generatorSourceCommit")
        and type(execution.get("elapsedSeconds")) in (int,float) and math.isfinite(execution["elapsedSeconds"]) and 0<=execution["elapsedSeconds"]<600,
        "Use exact same-run authored source code identity and completed bounded native geometry timing.")
    stages=execution.get("stages",[]);commands_saved=prepared["report"]["nativeCommandArguments"]
    require(isinstance(stages,list) and len(stages)==9,"All nine actual source geometry stages must be retained.")
    texts=[];log_rows=[]
    for index,(stage,args) in enumerate(zip(stages,commands_saved)):
        relative=f"logs/{index:02d}-{args[0]}.log";raw,row=record(root,relative,limit=4*1024**2);text=raw.decode("utf-8")
        require(isinstance(stage,dict) and set(stage)=={"program","exitCode","elapsedSeconds","log","logBytes","logSha256"}
            and stage["program"]==args[0] and type(stage["exitCode"]) is int and stage["exitCode"]==0 and stage["log"]==Path(relative).name
            and stage["logBytes"]==row["bytes"] and stage["logSha256"]==row["sha256"]
            and type(stage["elapsedSeconds"]) in (int,float) and math.isfinite(stage["elapsedSeconds"]) and 0<=stage["elapsedSeconds"]<600,
            "Every actual native stage needs its exact successful current log binding.")
        geometry.completed_log(text);texts.append(text);log_rows.append(row)
    require(execution.get("retainedNativeLogRecords")==log_rows,"The complete nine-log source inventory must match current bytes.")
    components=prepared["components"];snapshots=[];conversion=[];walls=[];qualities=[];component_rows=[]
    expected_snapshots=execution.get("componentNativeSnapshots",[])
    require(isinstance(expected_snapshots,list) and len(expected_snapshots)==2,"Both checked uncoupled native component snapshots are required.")
    for component,index in zip(components,(0,3)):
        name=component["component"];folder=root/"component-mesh-snapshots"/name;source_mesh=base.msh_inventory(record(root/"source-geometry",name+".msh")[0])
        rows=[record(root,f"component-mesh-snapshots/{name}/constant/polyMesh/{file}")[1] for file in MESH_FILES]
        require(expected_snapshots[index//3]=={"component":name,"files":rows},"Source snapshot bytes must match their actual premerge native records.")
        conversion.append(base.verify_conversion(folder,source_mesh,root/log_rows[index]["path"],after_wall_type_change=False))
        walls.append(base.verify_conversion(folder,source_mesh,root/log_rows[index]["path"],after_wall_type_change=True))
        q=geometry_runner.reduction.parse_mesh_quality(texts[index+2])
        geometry_runner.verify_region_count(texts[index+2],1);require(q["meshCellCount"]==component["cellCount"],"Actual source component quality count differs from CAD MSH.")
        qualities.append({"component":name,"connectedFluidRegionCount":1,**q});snapshots.extend(rows)
        component_rows.append({k:component[k] for k in ("component","caseDirectory","cellCount","nodeCount","patchFaces","cellZone")})
    require(execution.get("conversionEvidence")==conversion and execution.get("physicalWallEvidence")==walls and execution.get("componentQualityEvidence")==qualities,
        "Actual converter, physical walls and one-region component quality must independently reproduce.")
    geometry_runner.verify_region_count(texts[8],2)
    merged=base.verify_merged_mesh(root/"impeller-case",components,root/log_rows[8]["path"]);quality=geometry_runner.reduction.parse_mesh_quality(texts[8])
    require(execution.get("mergedFluidRegionCount")==2 and execution.get("mergedNativeMeshEvidence")==merged and execution.get("nativeMeshQuality")==quality,
        "Actual current coupled mesh topology/quality and unchanged NCC coverage must reproduce.")
    rows=source_inventory(root);require(execution.get("workspaceBytes")==sum(row["bytes"] for row in rows if row["path"]!=geometry_runner.REPORT),
        "The measured native geometry workspace byte total must reproduce before its immutable execution report.")
    outlet_proof=outlet.verify_outlet(root)
    return {"report":execution,"reportFile":execution_row,"preparation":prepared,"authoredGeometry":authored,"components":component_rows,
        "sourceFileRecords":rows,"snapshots":snapshots,"mergedNativeMeshEvidence":merged,"nativeMeshQuality":quality,"outletProfileEvidence":outlet_proof}


def case_files(component,*,default_faces=True):
    require(component in geometry_case.COMPONENTS and type(default_faces) is bool,"Use an exact component and actual optional empty defaultFaces declaration.")
    result={p:text.encode("ascii") for p,text in physics.case_files(component,300000).items()}
    # Prescribe the explicitly declared normal plug outlet. Actual cap geometry
    # is independently re-read in validate_source BEFORE any fresh case writes.
    q=re.escape(format(outlet.Q,".17g").encode("ascii"))
    pattern=rb'("mass-flow-inlet-11"\s*\{)\s*type\s+flowRateOutletVelocity\s*;\s*volumetricFlowRate\s+'+q+rb'\s*;\s*value\s+uniform\s*\(0\s+0\s+0\)\s*;\s*\}'
    result["0/U"],count=re.subn(pattern,lambda m:m[1]+b"\n        "+outlet.uniform_outlet_body()+b"\n    }",result["0/U"])
    require(count==1,"Override only the exact reviewed prescribed-Q outlet block with the declared uniform normal candidate.")
    # Change only this authored lane's numerical controls. The pure original
    # recipe, physical BCs, prescribed flow and initial fields remain separate.
    for variable,scheme in (("U","limitedLinearV 1"),("k","limitedLinear 1"),("omega","limitedLinear 1")):
        previous=("div(phi,"+variable+") bounded Gauss "+scheme+";").encode("ascii")
        require(result["system/fvSchemes"].count(previous)==1,"Require the reviewed original numerical recipe before this explicit stabilization override.")
        result["system/fvSchemes"]=result["system/fvSchemes"].replace(previous,("div(phi,"+variable+") bounded Gauss upwind;").encode("ascii"))
    # A separate, predeclared candidate: change pressure reconstruction only.
    # Native cellLimited scales its computed gradient, not stored field values.
    gradients=b"gradSchemes {default Gauss linear; grad(U) cellLimited Gauss linear 1;}"
    require(result["system/fvSchemes"].count(gradients)==1
        and len(re.findall(rb"\bgradSchemes\s*\{",result["system/fvSchemes"]))==1
        and not re.search(rb"\bgrad\s*\(\s*p\s*\)",result["system/fvSchemes"]),
        "Require the exact single reviewed gradient block without a prior grad(p) override.")
    result["system/fvSchemes"]=result["system/fvSchemes"].replace(gradients,
        gradients[:-1]+b" grad(p) cellLimited Gauss linear 1;}")
    result["system/fvSolution"]=(base.foam_header("fvSolution")
        + 'solvers {p {solver GAMG; tolerance 1e-8; relTol 0; smoother GaussSeidel; maxIter 100;} "(U|k|omega)" {solver smoothSolver; smoother symGaussSeidel; tolerance 1e-7; relTol 0.01; minIter 1; maxIter 100;}}\n'
        + 'SIMPLE {nNonOrthogonalCorrectors 2;}\nrelaxationFactors {fields {p 0.2;} equations {U 0.3; k 0.3; omega 0.3;}}\n').encode("ascii")
    # Foundation12 wallDist reads fvSchemes.wallDist when SST requests distance.
    # This new authored lane supplies the native method; old routes are unchanged.
    require(not re.search(rb"\bwallDist\s*\{",result["system/fvSchemes"]),"The reviewed baseline recipe must define wallDist exactly once.")
    result["system/fvSchemes"]+=b"wallDist {method meshWave;}\n"
    if component=="impeller":
        result["constant/MRFProperties"]=(base.foam_header("MRFProperties")+"rotor {cellZone rotating-fluid; origin (0 0 0); axis (0 0 1); omega 1450 [rpm];}\n").encode("ascii")
    if not default_faces:
        for path in ("0/U","0/p","0/k","0/omega","0/nut"):
            text,count=re.subn(rb'    "defaultFaces"\s*\{[^{}]*\}\s*',b"",result[path]);require(count==1,"Only the absent empty defaultFaces may be omitted.");result[path]=text
    return result


def authored_case_files(source,components):
    defaults={}
    for component in components:
        name=component["component"];patches=geometry.boundary_entries(Path(source)/"component-mesh-snapshots"/name/"constant/polyMesh/boundary")
        extra=patches.get("defaultFaces");require(extra is None or extra["type"]=="patch" and extra["nFaces"]==0,"Only actual zero-face defaultFaces may receive empty BCs.")
        defaults[name]=extra is not None
    result={}
    for component in components:
        name=component["component"];include=any(defaults.values()) if name=="impeller" else defaults[name]
        result.update({name+"-case/"+p:raw for p,raw in case_files(name,default_faces=include).items()})
    return result


def _commands(root):
    case=str(root/"impeller-case");other=str(root/"volute-case")
    return [["mergeMeshes","-case",case,"-addCases",'(\"'+other+'\")',"-overwrite"],
        ["createNonConformalCouples","-case",case,"-overwrite"],["checkMesh","-case",case,"-allGeometry","-allTopology","-writeSets"],["foamRun","-case",case]]


def commands(directory):return _commands(Path(directory).absolute())


def historical_commands(report):
    rows=report.get("nativeCommandArguments")
    require(isinstance(rows,list) and len(rows)==4 and isinstance(rows[0],list) and len(rows[0])==6 and rows[0][:2]==["mergeMeshes","-case"],"Retain complete four-command native physics provenance.")
    case=geometry.historical_command_root(rows[0][2]);require(case.name=="impeller-case","Use the recorded exact master case.")
    require(rows==_commands(case.parent),"All historical command paths must share one exact reviewed root.")
    return rows


def verify_fields_after_ncc(root,authored):
    """Same pinned Function1/SST defaults; only actual empty defaultFaces here."""
    standard={"phi":"phi","rho":"rho","psi":"none","gamma":"1","rhoInlet":"1"}
    patches={folder:geometry.boundary_entries(Path(root)/folder/"constant/polyMesh/boundary") for folder in ("impeller-case","volute-case")}
    for relative,expected in authored.items():
        if "/0/" not in relative:continue
        field=Path(relative).name;prefix,original=fields.boundary_blocks(expected,field)
        if relative.startswith("impeller-case/"):
            _,other=fields.boundary_blocks(authored["volute-case/0/"+field],field)
            require(all(name not in original or original[name]==row for name,row in other.items()),"Shared placeholder BCs must agree before native merging.")
            original={**original,**other}
        actual_prefix,rows=fields.boundary_blocks(record(root,relative)[0],field)
        for key in ("dimensions","internalField"):
            pattern=r"\b"+key+r"\s+([^;{}]+);";before=re.findall(pattern,prefix);after=re.findall(pattern,actual_prefix)
            require(len(before)==len(after)==1 and " ".join(before[0].split())==" ".join(after[0].split()),"Native NCC cannot change initial units/state.")
        initial=fields.canonical_field_value(re.findall(r"\binternalField\s+([^;{}]+);",prefix)[0])
        require(set(original)<=set(rows),"Every actual authored physical patch must survive native NCC.")
        for name,original_row in original.items():
            row=rows[name].copy();defaults=standard.copy()
            if original_row["type"]=="MRFnoSlip":defaults["value"]="uniform (0 0 0)"
            if original_row["type"]=="fixedFluxPressure":defaults.update(gradient="uniform 0",value="uniform 0")
            if field=="omega" and original_row["type"]=="omegaWallFunction":defaults.update(beta1=fields.canonical_field_value("0.075"),blended="false")
            if field=="nut" and original_row["type"]=="nutkWallFunction":defaults.update({k:fields.canonical_field_value(v) for k,v in {"Cmu":"0.09","kappa":"0.41","E":"9.8"}.items()})
            if name=="defaultFaces" and field=="nut" and original_row["type"]=="calculated" and re.fullmatch(r"nonuniform\s+List<scalar>\s+0\s*\(\s*\)",row.get("value","")):
                patch=patches[relative.split("/",1)[0]].get(name)
                require(patch is not None and patch["type"]=="patch" and patch["nFaces"]==0,"Typed empty calculated values require actual zero-face defaultFaces.")
                row["value"]=original_row["value"]
            require(all(row.get(k)==v for k,v in original_row.items()) and all(k in original_row or defaults.get(k)==v for k,v in row.items()),"Allow only unchanged physical BCs and exact pinned native writer defaults.")
        for name in set(rows)-set(original):
            require(name in {"pumpInterface_on_interface-impeller-outlet","pumpInterface_on_interface-volute-inlet","nonConformalError_on_interface-impeller-outlet","nonConformalError_on_interface-volute-inlet"}
                and rows[name].get("type") == ("nonConformalCyclic" if name.startswith("pumpInterface") else "nonConformalError")
                and all(k=="type" or k=="value" and v in {initial,"uniform (0 0 0)" if field=="U" else "uniform 0"} for k,v in rows[name].items()),
                "Only the exact four native NCC constraint patches may be added.")


def prepare(geometry_native_directory,output_directory):
    source=safety.scope_path(geometry_native_directory);output=safety.scope_path(output_directory,fresh=True)
    require(not output.resolve().is_relative_to(source.resolve()),"Use a fresh separate physics case.")
    verified=validate_source(source);authored=authored_case_files(source,verified["components"])
    output.mkdir(parents=True)
    try:
        source_rows=[];mesh_rows=[];authored_rows=[];retained=[]
        for row in verified["sourceFileRecords"]:
            raw,current=record(source,row["path"]);require(current==row,"Geometry source changed while copying.")
            target="source-teaching/"+row["path"];base.write_exclusive(output/target,raw);source_rows.append({**row,"path":target})
        for row in verified["snapshots"]:
            parts=row["path"].split("/");target=parts[1]+"-case/"+"/".join(parts[2:]);raw=record(source,row["path"])[0]
            base.write_exclusive(output/target,raw);mesh_rows.append(record(output,target)[1])
        for path,raw in authored.items():
            base.write_exclusive(output/path,raw);authored_rows.append(record(output,path)[1])
            target="authored-physics/"+path;base.write_exclusive(output/target,raw);retained.append(record(output,target)[1])
        report={"schemaVersion":SCHEMA,"createdAt":datetime.now(timezone.utc).isoformat(),"status":"CLASSROOM_AUTHORED_MRF_CASE_NATIVE_EXECUTION_REQUIRED",
            "sourceGeometryRoute":ROUTE,"sourceEvidenceDirectory":"source-teaching","sourceEvidenceFileRecords":source_rows,
            "sourceGeometryExecutionReportSha256":verified["reportFile"]["sha256"],"sourceGeometryPreparationReportSha256":verified["preparation"]["reportFile"]["sha256"],
            "sourceGeometryInputManifestSha256":verified["preparation"]["report"]["nativeInputManifestSha256"],
            "sourceAuthoredGeometryReportSha256":verified["authoredGeometry"]["reportFile"]["sha256"],"sourceGeometryIdentitySha256":verified["authoredGeometry"]["report"]["geometryIdentitySha256"],
            "sourceComponentFiles":[{**r,"path":"source-teaching/"+r["path"]} for r in verified["snapshots"]],
            "initialCaseMeshFiles":mesh_rows,"authoredInputFiles":authored_rows,"authoredPhysicsFileRecords":retained,
            "components":verified["components"],"nativeCommandArguments":commands(output),"classroomAssumptions":ASSUMPTIONS,"sampling":SAMPLING,
            "outletProfileEvidence":outlet.prefix_evidence(verified["outletProfileEvidence"],"source-teaching"),
            "sourceNativeGeometryQualityVerified":True,"sourceCoupledCoverageVerified":True,"sourceInputsImmutable":True,"limits":LIMITS,
            "claimBoundary":CLAIM,**{k:False for k in FALSE_FLAGS}}
        require(source_inventory(source)==verified["sourceFileRecords"],"Original geometry source changed before final preparation.")
        base.write_exclusive(output/REPORT,encoded(report));verify_output(output)
        return report
    except Exception:
        safety.scope_path(output);shutil.rmtree(output);raise


def verify_output(directory,*,after_native_ncc=False):
    require(type(after_native_ncc) is bool,"Declare the reviewed verification stage explicitly.")
    root=safety.scope_path(directory);report,row=json_file(root,REPORT);source=root/"source-teaching";verified=validate_source(source)
    require(report.get("schemaVersion")==SCHEMA and report.get("sourceGeometryRoute")==ROUTE and report.get("sourceEvidenceDirectory")=="source-teaching"
        and report.get("status")=="CLASSROOM_AUTHORED_MRF_CASE_NATIVE_EXECUTION_REQUIRED" and report.get("limits")==LIMITS
        and report.get("classroomAssumptions")==ASSUMPTIONS and report.get("sampling")==SAMPLING and report.get("claimBoundary")==CLAIM
        and all(report.get(k) is False for k in FALSE_FLAGS) and all(report.get(k) is True for k in ("sourceNativeGeometryQualityVerified","sourceCoupledCoverageVerified","sourceInputsImmutable")),
        "Replay the exact new authored geometry/physics preparation without inherited CFD or broad qualification.")
    bindings={"sourceGeometryExecutionReportSha256":verified["reportFile"]["sha256"],"sourceGeometryPreparationReportSha256":verified["preparation"]["reportFile"]["sha256"],
        "sourceGeometryInputManifestSha256":verified["preparation"]["report"]["nativeInputManifestSha256"],"sourceAuthoredGeometryReportSha256":verified["authoredGeometry"]["reportFile"]["sha256"],
        "sourceGeometryIdentitySha256":verified["authoredGeometry"]["report"]["geometryIdentitySha256"]}
    require(all(report.get(k)==v for k,v in bindings.items()) and report.get("components")==verified["components"],"All current source geometry identities/components must remain bound.")
    require(report.get("outletProfileEvidence")==outlet.prefix_evidence(verified["outletProfileEvidence"],"source-teaching"),
        "Every current native outlet facet/normal/area and source CAD identity must replay before fixed-profile physics admission.")
    require(report.get("sourceEvidenceFileRecords")==[{**r,"path":"source-teaching/"+r["path"]} for r in verified["sourceFileRecords"]]
        and report.get("sourceComponentFiles")==[{**r,"path":"source-teaching/"+r["path"]} for r in verified["snapshots"]],"Full copied geometry evidence and immutable premerge snapshots must reproduce.")
    authored=authored_case_files(source,verified["components"]);expected_authored=[];expected_retained=[];expected_mesh=[]
    for path,raw in authored.items():
        retained="authored-physics/"+path;actual,meta=record(root,retained);require(actual==raw,"Independent authored physics bytes changed.");expected_retained.append(meta)
        expected_authored.append({"path":path,"bytes":len(raw),"sha256":sha256(raw).hexdigest()})
        if "/0/" not in path or not after_native_ncc:require(record(root,path)[0]==raw,"Authored dictionary/initial physics input changed.")
    for meta in verified["snapshots"]:
        parts=meta["path"].split("/");target=parts[1]+"-case/"+"/".join(parts[2:])
        expected_mesh.append({**meta,"path":target})
        if not after_native_ncc:require(record(root,target)[0]==record(source,meta["path"])[0],"Initial native mesh changed before NCC preparation.")
    require(report.get("authoredInputFiles")==expected_authored and report.get("authoredPhysicsFileRecords")==expected_retained and report.get("initialCaseMeshFiles")==expected_mesh,
        "Exact authored/retained recipe and initial native mesh inventories are required.")
    if after_native_ncc:verify_fields_after_ncc(root,authored)
    historical_commands(report)
    return {"report":report,"reportFile":row,"components":verified["components"],"source":verified}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--geometry-native-directory",required=True);parser.add_argument("--output-directory",required=True)
    args=parser.parse_args();result=prepare(args.geometry_native_directory,args.output_directory)
    print(json.dumps({"status":result["status"],"reportFile":REPORT,"actualCfdRunVerified":False,"mainApplicationWrites":False}))


if __name__=="__main__":main()
