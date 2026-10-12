"""Pure, distinct inlet-flow/static-outlet classroom BC candidate; no execution.

The current v3 steady recipe is byte-bound before four section BC changes.
Geometry admission, source-package preparation, native execution and scientific
reduction for this new family are deliberately not supplied by this module.
"""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import math
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
import teaching_pump_baseline_case as upstream

FAMILY = "teaching-inlet-flow-outlet-static-v1"
BC_FAMILY = FAMILY
SCHEMA = "untirta.teaching-pump-inlet-flow-outlet-static-plan.v1"
UPSTREAM_PROFILE = "teaching-upwind-consistent-coupling-v3"
RHO, MASS_FLOW, RPM, OUTLET_STATIC_REFERENCE_PA = 998.2, 90., 1450., 300000.
Q = MASS_FLOW/RHO
INLET, OUTLET = "inlet", "mass-flow-inlet-11"
PIN = "86e126a7bc4dbfaed949bbc58c9f0fdd67acc059"
SOURCE_BASE = "https://github.com/OpenFOAM/OpenFOAM-12/blob/"+PIN+"/"
SOURCES = {
    "inletFlow":SOURCE_BASE+"src/finiteVolume/fields/fvPatchFields/derived/flowRateInletVelocity/flowRateInletVelocityFvPatchVectorField.C",
    "inletPressure":SOURCE_BASE+"src/finiteVolume/cfdTools/general/constrainPressure/constrainPressure.C",
    "outletVelocity":SOURCE_BASE+"src/finiteVolume/fields/fvPatchFields/derived/pressureInletOutletVelocity/pressureInletOutletVelocityFvPatchVectorField.H",
    "outletStaticPressure":SOURCE_BASE+"src/finiteVolume/fields/fvPatchFields/basic/fixedValue/fixedValueFvPatchField.H"}
# Canonical path/size/SHA inventory of the exact reviewed pure v3 recipe,
# including numerical controls, walls, initial state and optional placeholder.
# These hashes confer no historical geometry, execution or flow acceptance.
UPSTREAM_RECIPE_SHA256 = {
    ("impeller",False):"7a2dda0ba09abc9170efc6acf8ef52d9279691e10b825f5f5879e7c4f2d78a20",
    ("impeller",True):"9b839ff23de77c8a3a451e071a9b2acbe41bbcb10169e56ce60804aa56b7660f",
    ("volute",False):"6d48d692a51d53fb66116566ca7479646dca202405bbb38239b4cee4285e5901",
    ("volute",True):"2c1a8c0953cdfa358f69435355afc942c5568426d5085a954e94ee948c9d905a"}
FALSE_FLAGS = tuple(sorted(set(upstream.FALSE_FLAGS)|{"geometryVerified","headCalculated","npshaCalculated",
    "sourcePackagePrepared","nativeRunnerAvailable","numericalReducerAvailable","pressureSweepExecuted"}))
PARAMETERS = deepcopy(upstream.PARAMETERS)
LIMITS = deepcopy(upstream.LIMITS)
ASSUMPTIONS = {"provenance":"CLASSROOM_AUTHORED_GEOMETRY","boundaryFamilyId":BC_FAMILY,"coordinateUnits":"SI_METRES",
    "rhoKgM3":RHO,"muPaS":.001002,"gMS2":9.81,"speedRpm":RPM,"massFlowKgS":MASS_FLOW,"volumetricFlowM3S":Q,
    "temperatureK":293.15,"vapourPressurePa":2339.,"axis":[0,0,1],"originM":[0,0,0],
    "solverGravityMS2":[0,0,0],"gravityInSolver":False,"pressureControlLocation":"outlet",
    "outletStaticAbsoluteReferencePa":OUTLET_STATIC_REFERENCE_PA,"outletKinematicStaticPressureBCM2S2":0.,
    "pressureSelectedBeforeExecution":True,"inletTotalPressurePrescribed":False,
    "absolutePressureEquation":"p_static_absolute_Pa = 998.2*p_kinematic_m2s2 + 300000; outlet static reference added exactly once",
    "inletTurbulence":{"kM2S2":.375,"omegaSInverse":100.,"basis":"Unchanged prescribed classroom initial/section values; not measured."},
    "inletVelocity":"Native flowRateInletVelocity sets U_f=-(Q/sum_f|Sf|)*n_f on actual inlet faces; no guessed inlet axis or area.",
    "outletVelocity":"Pressure-coupled pressureInletOutletVelocity; zeroGradient outflow and zero prescribed tangential reverse inflow. No uniform plug or backflow prevention claim.",
    "headDatum":"Unchanged declared common zero inlet/outlet section reference with native gravity neglected; actual section elevations remain unmeasured.",
    "wallAdaptation":"Unchanged blade/impeller-hub/impeller-shroud MRFnoSlip; intake/volute noSlip; source physical roles require independent geometry admission.",
    "numericalControlsProfileId":UPSTREAM_PROFILE,"numericalControlsUnchanged":True,
    "geometryBasis":"New authored SI classroom CAD is the intended geometry, pending current source-package admission; not official Ansys/Grundfos geometry or measured pump performance."}
SAMPLING = {"flowUnits":"m3/s outward positive","weighting":"signed-outward-phi","velocityFrame":"absolute",
    "kineticMoment":"mean(U_absolute_squared)","pressureUnit":"Pa","pressureBasis":"absolute",
    "totalPressureField":"998.2*pKinematic + 0.5*998.2*mean(U_absolute_squared) + 300000",
    "staticPressureField":"998.2*pKinematic; add outlet static300000Pa reference exactly once in reduction",
    "pressureReferenceLocation":"outlet","inletPressureRole":"Actual sampled suction pressure, not a prescribed inlet total pressure",
    "npshaSource":"Actual inlet static absolute pressure, flux-weighted mean(|U_absolute|^2) and explicit suction datum",
    "iterationIndexIsPhysicalTime":False}
CLAIM = "Pure future physical BC recipe only. Current source geometry/package admission, a bounded native runner and independent numerical reducer are missing. No convergence improvement, flow solution, head, NPSHA, NPSHR, active-phase H0, cavitation sweep or physical validation is supplied."


class InletFlowRecipeIssue(ValueError): pass


def require(condition,action):
    if not condition:raise InletFlowRecipeIssue(action)


def inventory(files):
    require(isinstance(files,dict) and files and all(isinstance(p,str) and isinstance(raw,bytes) and raw
        for p,raw in files.items()),"Require a complete byte-valued pure upstream recipe.")
    return [{"path":p,"bytes":len(raw),"sha256":sha256(raw).hexdigest()} for p,raw in sorted(files.items())]


def section_change(raw,field,patch,previous,replacement):
    prefix,rows=upstream.fields.boundary_blocks(raw,field)
    expected=dict(re.findall(r"([A-Za-z][A-Za-z0-9_]*)\s+([^;{}]+);",previous))
    require(rows.get(patch)==expected,"Require the exact reviewed previous section BC: "+field+"/"+patch)
    matches=list(re.finditer(rb'("'+re.escape(patch.encode())+rb'"\s*\{)([^{}]*)(\})',raw))
    require(len(matches)==1 and matches[0][2].strip()==previous.encode(),
        "Require exactly one plain byte-bound physical section block: "+field+"/"+patch)
    match=matches[0];changed=raw[:match.start(2)]+b"\n        "+replacement.encode()+b"\n    "+raw[match.end(2):]
    after_prefix,after=upstream.fields.boundary_blocks(changed,field)
    desired=dict(re.findall(r"([A-Za-z][A-Za-z0-9_]*)\s+([^;{}]+);",replacement))
    require(prefix==after_prefix and set(rows)==set(after) and after[patch]==desired
        and all(rows[p]==after[p] for p in rows if p!=patch),"Change only the declared section BC, preserving all other physical roles.")
    return changed


def case_files(component,*,default_faces=True):
    """Return fresh bytes; the auxiliary volute has no fictitious inlet patch."""
    require(isinstance(component,str) and component in {"impeller","volute"} and type(default_faces) is bool,
        "Choose an exact teaching component and typed optional empty defaultFaces.")
    require(upstream.NUMERICAL_PROFILE.get("id")==UPSTREAM_PROFILE,
        "Review a changed upstream numerical family before authoring this distinct physical BC candidate.")
    files=upstream.case_files(component,default_faces=default_faces)
    require(sha256(upstream.encoded(inventory(files))).hexdigest()==UPSTREAM_RECIPE_SHA256[(component,default_faces)],
        "Require the exact current-v3 canonical recipe; reject any changed, duplicate or unknown upstream input.")
    files=dict(files)
    if component=="impeller":
        files["0/U"]=section_change(files["0/U"],"U",INLET,
            "type pressureInletOutletVelocity; value uniform (0 0 0);",
            "type flowRateInletVelocity; volumetricFlowRate "+format(Q,".17g")+"; value uniform (0 0 0);")
        files["0/p"]=section_change(files["0/p"],"p",INLET,
            "type totalPressure; p0 uniform 0; value uniform 0;","type fixedFluxPressure; value uniform 0;")
    files["0/U"]=section_change(files["0/U"],"U",OUTLET,
        upstream.outlet.uniform_outlet_body().decode(),"type pressureInletOutletVelocity; value uniform (0 0 0);")
    files["0/p"]=section_change(files["0/p"],"p",OUTLET,"type zeroGradient;","type fixedValue; value uniform 0;")
    return files


def stage(inputs,equation,substitution,result,unit):
    require(type(result) in (int,float) and math.isfinite(result),"Declared engineering input reductions must be finite.")
    return {"input":inputs,"equation":equation,"substitution":substitution,"result":{"value":result,"unit":unit},
        "calculationAuthority":"Python","role":"Declared input reduction; not a measured CFD result"}


def teaching_plan():
    """New boundary-family identity and input mathematics only; no CFD outputs."""
    return {"schemaVersion":SCHEMA,"familyId":FAMILY,"status":"PURE_BC_CANDIDATE_SOURCE_PREPARATION_AND_EXECUTION_REQUIRED",
        "provenance":"CLASSROOM_AUTHORED_BOUNDARY_RESEARCH_CANDIDATE","units":"SI","upstreamNumericalProfileId":UPSTREAM_PROFILE,
        "boundaryIdentity":{"inletPatch":INLET,"outletPatch":OUTLET,"inletControl":"volumetric-flow-controlled",
            "outletControl":"static-pressure-controlled","inletTotalPressurePrescribed":False},
        "input":{"massFlowKgS":MASS_FLOW,"densityKgM3":RHO,"speedRpm":RPM,"outletStaticAbsoluteReferencePa":OUTLET_STATIC_REFERENCE_PA},
        "parameters":deepcopy(PARAMETERS),"assumptions":deepcopy(ASSUMPTIONS),"sampling":deepcopy(SAMPLING),
        "nativeInletProfile":{"patchType":"flowRateInletVelocity","positiveQMeansIntoDomain":True,
            "formula":"U_f=-(Q/sum_f |S_f|)*n_f","areaAndNormalsSource":"Actual native inlet patch faces at execution",
            "outwardPhiIntegral":"-Q","guessedInletAxisOrArea":False,"profileExecuted":False},
        "outletVelocityAssumption":"pressureInletOutletVelocity: zeroGradient outflow; reverse inflow has zeroGradient normal and zero prescribed tangential velocity. No uniform outlet plug or backflow prevention is claimed.",
        "absolutePressureMapping":{"nativeField":"p","nativePressureUnits":"m2/s2","referenceLocation":"outlet",
            "referenceQuantity":"static absolute pressure","referencePa":OUTLET_STATIC_REFERENCE_PA,"referenceAdditionCount":1,
            "equation":"p_static_absolute_Pa=rho*p_kinematic_m2s2+outletStaticAbsoluteReferencePa"},
        "suctionNpshaBasis":{"pressureSource":"Actual validated flux-weighted inlet static absolute pressure",
            "kineticSource":"Actual flux-weighted mean(|U_absolute|^2), not squared mean velocity",
            "datum":"Explicit suction section/datum required in a future source-bound reducer",
            "outletReferenceIsSuctionPressure":False,"npshaCalculated":False},
        "futureSweep":"A separately authored active-phase high-pressure H0 and outlet-static-pressure-only controlled family are required; no inlet-total-pressure parameter or old H0 is inherited.",
        "equationStages":{"volumeFlow":stage({"massFlowKgS":MASS_FLOW,"densityKgM3":RHO},"Q=m_dot/rho","90/998.2",Q,"m3/s"),
            "angularSpeed":stage({"speedRpm":RPM},"omega=2*pi*N/60","2*pi*1450/60",2*math.pi*RPM/60,"rad/s"),
            "declaredOutletPressure":stage({"outletKinematicPressureBCM2S2":0.,"densityKgM3":RHO,"outletStaticAbsoluteReferencePa":OUTLET_STATIC_REFERENCE_PA},
                "p_out_absolute=rho*p_out_kinematic+P_out_reference","998.2*0+300000",OUTLET_STATIC_REFERENCE_PA,"Pa")},
        "limits":deepcopy(LIMITS),"numericalControlsUnchanged":True,"allNonsectionFieldBytesUnchanged":True,
        "wallsSstMrfUnchanged":True,"geometrySourceAdmissionImplemented":False,"casePreparerImplemented":False,
        "references":dict(SOURCES),"claimBoundary":CLAIM,
        **dict.fromkeys(FALSE_FLAGS,False)}
