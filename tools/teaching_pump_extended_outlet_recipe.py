"""One-factor outlet-domain trial; fresh authored CAD, never historical admission.

All native BCs, RPM, flow, fluid, SST/MRF, numerical controls and acceptance
criteria remain byte-identical to the 1 MPa/2000 family. The outlet plane moves
from y=.15 to y=.30 m. Head covers the extended assembly, including added duct
loss; it is not corrected to the original pump section or rated pump head.
"""
from copy import deepcopy
from hashlib import sha256
import math
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import teaching_pump_high_pressure_recipe as previous
import teaching_pump_geometry as author

PROFILE={"id":"teaching-inlet-flow-outlet-extended-030-high-pressure-2000-v1","iterations":2000,
    "outletStaticAbsoluteReferencePa":1000000.,"samplingWindowIterations":20,"outletLengthM":.30,
    "headMeasurement":"inlet-to-extended-outlet-assembly"}
BC_FAMILY=FAMILY=PROFILE["id"]
SCHEMA="untirta.teaching-pump-extended-outlet-recipe.v1"
EXPECTED_GEOMETRY_PARAMETERS={**author.DEFAULTS,"outletLengthM":.30}
RHO,MASS_FLOW,RPM,Q=previous.RHO,previous.MASS_FLOW,previous.RPM,previous.Q
INLET,OUTLET=previous.INLET,previous.OUTLET
OUTLET_STATIC_REFERENCE_PA=previous.OUTLET_STATIC_REFERENCE_PA
ITERATIONS,SAMPLING_WINDOW_ITERATIONS=previous.ITERATIONS,previous.SAMPLING_WINDOW_ITERATIONS
PARAMETERS,LIMITS=deepcopy(previous.PARAMETERS),deepcopy(previous.LIMITS)
FALSE_FLAGS=previous.FALSE_FLAGS
SOURCE_BASE,PIN,SOURCES=previous.SOURCE_BASE,previous.PIN,deepcopy(previous.SOURCES)
stage,inventory,encoded=previous.stage,previous.inventory,previous.encoded
PRIOR_INVENTORY={'impeller:false': '69817b47eb61f47e3f43192b9b7d93be63af9c4e7f101251cdb8a40a7aa888e0', 'impeller:true': 'e7558fb7a5ca374492780c9bb10cb61fad3504325668534aa5e7a6f5d5651471', 'volute:false': '4d10edeac0dd6e52b7c92d5e604b3d36232fcd005274bf1a30af83366a11b38c', 'volute:true': '173e80f26983998659492e029799778b574683ab9bb384baa1d339b479d6ca3d'}
ASSUMPTIONS=deepcopy(previous.ASSUMPTIONS)
ASSUMPTIONS.update(boundaryFamilyId=BC_FAMILY,researchProfile=deepcopy(PROFILE),
    outletDomainTrial={"previousOutletLengthM":.15,"outletLengthM":.30,"oneFactorChanged":"outlet-duct-length",
        "allOtherGeometryParametersUnchanged":True,"meshRegenerated":True,"sameDiscreteMeshClaimed":False,
        "reason":"Stored run38215415105 has21 reverse-flow faces at the inner outlet edge; move the pressure boundary downstream as a measured trial.",
        "improvementPredicted":False,"baselineHeadComparisonAtSameSection":False},
    headMeasurement=PROFILE["headMeasurement"],
    headDatum="Zero declared elevation correction with native gravity neglected. Head is the total-pressure rise from inlet to the extended outlet at y=.30 m, including extra duct losses; no pump-only head or loss correction is supplied.")
SAMPLING=deepcopy(previous.SAMPLING)
SAMPLING.update(outletPlaneYM=.30,headMeasurement=PROFILE["headMeasurement"],pumpOnlyHead=False,
    addedDuctLossCorrectionApplied=False,sameSectionComparisonWithPrevious=False)
CLAIM="Fresh one-factor classroom outlet-domain trial. Native BCs/flow/RPM/fluid/SST/MRF/numerical controls/acceptance remain unchanged. New SI CAD and every-cell native mesh evidence are required. Head includes the added outlet duct; it is not a manufacturer pump head, corrected pump section result, phase H0 or physical validation. No convergence improvement, NPSHR or transient result is predicted."

class ExtendedOutletRecipeIssue(ValueError):pass
def require(value,reason):
    if not value:raise ExtendedOutletRecipeIssue(reason)

def verify_profile(value):
    require(isinstance(value,dict) and set(value)==set(PROFILE)
        and all(type(value[k]) is type(v) and value[k]==v for k,v in PROFILE.items()),
        "Only the exact typed .30 m outlet-domain research profile is supported.")
    return deepcopy(PROFILE)

def verify_geometry_parameters(value):
    require(isinstance(value,dict) and set(value)==set(EXPECTED_GEOMETRY_PARAMETERS)
        and all(type(value[k]) is type(v) and value[k]==v for k,v in EXPECTED_GEOMETRY_PARAMETERS.items()),
        "Require every exact typed SI geometry/operating declaration with only outletLengthM=.30 changed.")
    return author.validate_parameters(value)

def case_files(component,*,default_faces=True):
    verify_profile(PROFILE)
    require(component in ('impeller','volute') and type(component) is str and type(default_faces) is bool,
        "Require a declared component and typed empty-placeholder choice.")
    files=previous.case_files(component,default_faces=default_faces)
    require(sha256(encoded(inventory(files))).hexdigest()==PRIOR_INVENTORY[component+':'+str(default_faces).lower()],
        "Require the complete reviewed previous physics recipe before this geometry-only trial.")
    return dict(files)

def verify_files(files,component,*,default_faces=True):
    expected=case_files(component,default_faces=default_faces)
    require(isinstance(files,dict) and set(files)==set(expected) and all(type(files[k]) is bytes and files[k]==v for k,v in expected.items()),
        "Complete fresh extended-outlet physics bytes must match the reviewed recipe.")
    return inventory(files)

def teaching_plan():
    verify_profile(PROFILE);p=verify_geometry_parameters(EXPECTED_GEOMETRY_PARAMETERS)
    width=.12-.01;height=.04363;dh=2*width*height/(width+height)
    return {"schemaVersion":SCHEMA,"profile":deepcopy(PROFILE),"boundaryFamilyId":BC_FAMILY,
        "status":"FRESH_EXTENDED_OUTLET_NATIVE_EVIDENCE_REQUIRED","geometryParameters":p,
        "parameters":deepcopy(PARAMETERS),"assumptions":deepcopy(ASSUMPTIONS),"sampling":deepcopy(SAMPLING),
        "limits":deepcopy(LIMITS),"claimBoundary":CLAIM,
        "engineeringStages":{"hydraulicDiameter":stage({"wM":width,"hM":height},"Dh=2*w*h/(w+h)",
            f"2*{width}*{height}/({width}+{height})",dh,"m"),
            "nominalLengthRatio":stage({"lengthM":.30,"DhM":dh},"L/Dh",f".30/{dh}",.30/dh,"1")},
        **dict.fromkeys(FALSE_FLAGS,False)}
