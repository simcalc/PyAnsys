"""Pure, byte-bound 2000-iteration/1 MPa inlet-flow research recipe.

The existing 300 kPa/500-iteration family remains unchanged. This fresh profile
changes only three controlDict entries and supplies no numerical acceptance.
The absolute reference affects pressure reconstruction, not kinematic gradients
or a promise of convergence in the fixed-density single-phase solver.
"""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
import teaching_pump_inlet_flow_recipe as previous

PROFILE={"id":"teaching-inlet-flow-outlet-static-high-pressure-2000-v1","iterations":2000,
    "outletStaticAbsoluteReferencePa":1000000.,"samplingWindowIterations":20}
BC_FAMILY=FAMILY=PROFILE["id"]
SCHEMA="untirta.teaching-pump-high-pressure-recipe.v1"
RHO,MASS_FLOW,RPM,Q=previous.RHO,previous.MASS_FLOW,previous.RPM,previous.Q
OUTLET_STATIC_REFERENCE_PA=1000000.
ITERATIONS,SAMPLING_WINDOW_ITERATIONS=2000,20
INLET,OUTLET=previous.INLET,previous.OUTLET
PARAMETERS,LIMITS=deepcopy(previous.PARAMETERS),deepcopy(previous.LIMITS)
FALSE_FLAGS=previous.FALSE_FLAGS
SOURCE_BASE,PIN=previous.SOURCE_BASE,previous.PIN
SOURCES={**previous.SOURCES,
    "pressureFunction":SOURCE_BASE+"src/functionObjects/field/pressure/pressure.H",
    "kinematicPressureCorrection":SOURCE_BASE+"applications/modules/incompressibleFluid/correctPressure.C"}
PREVIOUS_RECIPE_SHA256={
    ("impeller",False):"7e7fedc098b160a93a37e3ce04a93bfbc51b4d2e95ca358d49e94b6f9ad38984",
    ("impeller",True):"d738da4742d3085165730e518a8b6e9c37fc96d1934295fa9903df32125d285c",
    ("volute",False):"398c13ea299f61f65861a2ef27d28dd08871f17d6ec2351dbd39aac41331f58a",
    ("volute",True):"c6a1d476812b8979a8069cc47ed319832e1bc9917fc7a58e359ed66f4da47dfc"}
ASSUMPTIONS=deepcopy(previous.ASSUMPTIONS)
ASSUMPTIONS.update(boundaryFamilyId=BC_FAMILY,researchProfile=deepcopy(PROFILE),
    outletStaticAbsoluteReferencePa=OUTLET_STATIC_REFERENCE_PA,
    absolutePressureEquation="p_static_absolute_Pa = 998.2*p_kinematic_m2s2 + 1000000; outlet static reference added exactly once",
    steadyIterationCount=ITERATIONS,iterationIndexIsPhysicalTime=False,
    numericalControlsUnchanged=False,
    numericalControlsDifference="Only controlDict endTime/writeInterval become2000 and totalPressurePa.pRef becomes1000000; all physical BCs, models, schemes, solution coupling and resource bounds remain unchanged.",
    pressureReferenceIsConvergenceCorrection=False,convergenceImprovementPredicted=False,
    referencePressureBasis="Explicit owner-authorised classroom static outlet pressure; not a measured pump condition or certification.")
SAMPLING=deepcopy(previous.SAMPLING)
SAMPLING.update(totalPressureField="998.2*pKinematic + 0.5*998.2*mean(U_absolute_squared) + 1000000",
    staticPressureField="998.2*pKinematic; add outlet static1000000Pa reference exactly once in reduction",
    samplingIterationWindow=[1981,2000],samplingWindowIterations=20)
CLAIM="Fresh source-bound single-phase research inputs only. The explicit2000-iteration/1 MPa profile supplies no convergence, head, NPSHA, phase H0, cavitation, restart, refinement or physical-validation result. Kinematic outlet pressure stays0; absolute pressure is reconstructed with the declared1 MPa reference exactly once."
stage,inventory=previous.stage,previous.inventory


class HighPressureRecipeIssue(ValueError):pass


def require(value,action):
    if not value:raise HighPressureRecipeIssue(action)


def encoded(value):return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode("ascii")


def verify_profile(value):
    expected={"id":BC_FAMILY,"iterations":ITERATIONS,"outletStaticAbsoluteReferencePa":OUTLET_STATIC_REFERENCE_PA,
        "samplingWindowIterations":SAMPLING_WINDOW_ITERATIONS}
    require(isinstance(value,dict) and set(value)==set(expected)
        and all(type(value[k]) is type(v) and value[k]==v for k,v in expected.items()),
        "Only the exact typed predeclared2000-iteration/1 MPa research profile is supported.")
    return deepcopy(expected)


def case_files(component,*,default_faces=True):
    verify_profile(PROFILE)
    require(type(component) is str and component in {"impeller","volute"} and type(default_faces) is bool,
        "Choose an exact component and typed measured zero-face placeholder.")
    require(previous.BC_FAMILY=="teaching-inlet-flow-outlet-static-v1" and previous.OUTLET_STATIC_REFERENCE_PA==300000.
        and previous.Q==Q and previous.PARAMETERS==PARAMETERS and previous.LIMITS==LIMITS,
        "The prior physical family, flow, fluid and resource basis must remain exact.")
    result=previous.case_files(component,default_faces=default_faces)
    require(sha256(previous.upstream.encoded(inventory(result))).hexdigest()==PREVIOUS_RECIPE_SHA256[(component,default_faces)],
        "Require every byte of the reviewed previous recipe before the explicit new control profile.")
    result=dict(result);control=result["system/controlDict"]
    for before,after in ((b"endTime 500;",b"endTime 2000;"),(b"writeInterval 500;",b"writeInterval 2000;")):
        require(control.count(before)==1,"Require exactly one reviewed control entry before the research override.")
        control=control.replace(before,after,1)
    require(control.count(b"pRef 300000;")==(1 if component=="impeller" else 0),
        "The master alone owns one pressure function; the auxiliary must contain none.")
    if component=="impeller":control=control.replace(b"pRef 300000;",b"pRef 1000000;",1)
    result["system/controlDict"]=control
    return result


def verify_files(files,component,*,default_faces=True):
    expected=case_files(component,default_faces=default_faces)
    require(isinstance(files,dict) and set(files)==set(expected) and all(files[k]==v and type(files[k]) is bytes for k,v in expected.items()),
        "The complete research recipe byte inventory must match before any interpretation.")
    return inventory(files)


def teaching_plan():
    verify_profile(PROFILE)
    return {"schemaVersion":SCHEMA,"boundaryFamilyId":BC_FAMILY,"profile":deepcopy(PROFILE),
        "status":"FRESH_RESEARCH_RECIPE_SOURCE_AND_NATIVE_EVIDENCE_REQUIRED",
        "parameters":deepcopy(PARAMETERS),"assumptions":deepcopy(ASSUMPTIONS),"sampling":deepcopy(SAMPLING),
        "limits":deepcopy(LIMITS),"sources":deepcopy(SOURCES),"claimBoundary":CLAIM,
        "caseInventories":{c+":"+str(d).lower():inventory(case_files(c,default_faces=d))
            for c in ("impeller","volute") for d in (False,True)},
        "engineeringStages":{"outletAbsoluteReference":stage({"outletKinematicPressureM2S2":0.,"rhoKgM3":RHO,
            "referencePa":OUTLET_STATIC_REFERENCE_PA},"p_out_abs=rho*p_out_kinematic+P_out_reference",
            "998.2*0+1000000",OUTLET_STATIC_REFERENCE_PA,"Pa")},**dict.fromkeys(FALSE_FLAGS,False)}
