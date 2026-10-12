"""Fresh45kg/s operating-point trial on the .30 m outlet assembly.

Only declared mass/volume flow and its actual inlet velocity BC change. This is
a different classroom operating point; no90kg/s result or source admission is
inherited. Geometry dimensions, SST/MRF, pressure, numerical and quality gates
remain unchanged. A positive and stable head is required, never presumed.
"""
from copy import deepcopy
from hashlib import sha256
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import teaching_pump_extended_outlet_recipe as previous
import teaching_pump_inlet_flow_recipe as sections
import teaching_pump_geometry as author
PROFILE={"id":"teaching-inlet-flow-45kg-s-extended-030-high-pressure-2000-v1","iterations":2000,
    "outletStaticAbsoluteReferencePa":1000000.,"samplingWindowIterations":20,"outletLengthM":.30,
    "headMeasurement":"inlet-to-extended-outlet-assembly","massFlowKgS":45.}
BC_FAMILY=FAMILY=PROFILE["id"]
SCHEMA="untirta.teaching-pump-lower-flow-recipe.v1"
RHO,MASS_FLOW,RPM=998.2,45.,1450.
Q=MASS_FLOW/RHO
INLET,OUTLET=previous.INLET,previous.OUTLET
OUTLET_STATIC_REFERENCE_PA=previous.OUTLET_STATIC_REFERENCE_PA
ITERATIONS,SAMPLING_WINDOW_ITERATIONS=previous.ITERATIONS,previous.SAMPLING_WINDOW_ITERATIONS
PARAMETERS={**previous.PARAMETERS,"massFlowKgS":MASS_FLOW}
EXPECTED_GEOMETRY_PARAMETERS={**previous.EXPECTED_GEOMETRY_PARAMETERS,"massFlowKgS":MASS_FLOW}
LIMITS=deepcopy(previous.LIMITS)
FALSE_FLAGS=previous.FALSE_FLAGS
SOURCE_BASE,PIN,SOURCES=previous.SOURCE_BASE,previous.PIN,deepcopy(previous.SOURCES)
stage,inventory,encoded=previous.stage,previous.inventory,previous.encoded
PRIOR_INVENTORY={'impeller:false': '69817b47eb61f47e3f43192b9b7d93be63af9c4e7f101251cdb8a40a7aa888e0', 'impeller:true': 'e7558fb7a5ca374492780c9bb10cb61fad3504325668534aa5e7a6f5d5651471', 'volute:false': '4d10edeac0dd6e52b7c92d5e604b3d36232fcd005274bf1a30af83366a11b38c', 'volute:true': '173e80f26983998659492e029799778b574683ab9bb384baa1d339b479d6ca3d'}
ASSUMPTIONS=deepcopy(previous.ASSUMPTIONS)
ASSUMPTIONS.update(boundaryFamilyId=BC_FAMILY,researchProfile=deepcopy(PROFILE),massFlowKgS=MASS_FLOW,volumetricFlowM3S=Q,
    lowerFlowTrial={"previousMassFlowKgS":90.,"massFlowKgS":MASS_FLOW,"oneFactorChanged":"prescribed-flow",
        "mainOperatingPointModified":False,"previousFlowResultInherited":False,"improvementPredicted":False,
        "reason":"Run38246776372 completed native CFD at90kg/s but had negative assembly head and failed reverse-flow/residual/stationarity criteria. Investigate a separately declared lower classroom operating point.",
        "inletTurbulenceValuesScaled":False,"inletTurbulenceBasis":"Prescribed k=.375m2/s2 and omega=100/s remain unchanged; no measured turbulence or constant-intensity scaling claim."})
SAMPLING=deepcopy(previous.SAMPLING)
SAMPLING.update(massFlowKgS=MASS_FLOW,volumetricFlowM3S=Q,sameOperatingPointAsPrevious=False)
CLAIM="Fresh45kg/s classroom operating-point trial. Only flow declaration and the native inlet volumetric-flow control change. New source/parameter/mesh/runtime/field evidence is required. The90kg/s result is not repaired, reused or relabelled. All numerical and physical qualification limits remain. Assembly head includes the .30m duct; no pump-only correction, BEP, rated performance, active-phase H0, NPSHr or transient claim is supplied."

class LowerFlowRecipeIssue(ValueError):pass
def require(value,reason):
    if not value:raise LowerFlowRecipeIssue(reason)
def verify_profile(value):
    require(isinstance(value,dict) and set(value)==set(PROFILE) and all(type(value[k]) is type(v) and value[k]==v for k,v in PROFILE.items()),
        "Require the exact typed45kg/s,.30m,1MPa,2000-iteration trial profile.")
    return deepcopy(PROFILE)
def verify_geometry_parameters(value):
    require(isinstance(value,dict) and set(value)==set(EXPECTED_GEOMETRY_PARAMETERS)
        and all(type(value[k]) is type(v) and value[k]==v for k,v in EXPECTED_GEOMETRY_PARAMETERS.items()),
        "Require the complete exact45kg/s source/geometry parameter family; no old90kg/s source.")
    return author.validate_parameters(value)
def case_files(component,*,default_faces=True):
    verify_profile(PROFILE)
    require(type(component) is str and component in ('impeller','volute') and type(default_faces) is bool,
        "Require a known component and typed empty-placeholder declaration.")
    files=previous.case_files(component,default_faces=default_faces)
    require(sha256(encoded(inventory(files))).hexdigest()==PRIOR_INVENTORY[component+':'+str(default_faces).lower()],
        "Require every byte of the reviewed90kg/s recipe before the sole flow change.")
    files=dict(files)
    if component=='impeller':
        before='type flowRateInletVelocity; volumetricFlowRate '+format(previous.Q,'.17g')+'; value uniform (0 0 0);'
        after='type flowRateInletVelocity; volumetricFlowRate '+format(Q,'.17g')+'; value uniform (0 0 0);'
        files['0/U']=sections.section_change(files['0/U'],'U',INLET,before,after)
    return files
def verify_files(files,component,*,default_faces=True):
    expected=case_files(component,default_faces=default_faces)
    require(isinstance(files,dict) and set(files)==set(expected) and all(type(files[k]) is bytes and files[k]==v for k,v in expected.items()),
        "Require the complete exact new45kg/s physics inventory.")
    return inventory(files)
def teaching_plan():
    p=verify_geometry_parameters(EXPECTED_GEOMETRY_PARAMETERS)
    area=.04363*(.12-.01);dh=2*.11*.04363/(.11+.04363)
    return {"schemaVersion":SCHEMA,"profile":deepcopy(PROFILE),"boundaryFamilyId":BC_FAMILY,"status":"FRESH_LOWER_FLOW_NATIVE_EVIDENCE_REQUIRED",
        "geometryParameters":p,"parameters":deepcopy(PARAMETERS),"assumptions":deepcopy(ASSUMPTIONS),"sampling":deepcopy(SAMPLING),
        "limits":deepcopy(LIMITS),"claimBoundary":CLAIM,
        "engineeringStages":{"volumeFlow":stage({"massFlowKgS":45.,"densityKgM3":998.2},"Q=m_dot/rho","45/998.2",Q,"m3/s"),
            "nominalOutletSpeed":stage({"Q_M3S":Q,"areaM2":area},"U_nominal=Q/A",f"{Q}/{area}",Q/area,"m/s"),
            "hydraulicDiameter":stage({"wM":.11,"hM":.04363},"Dh=2*w*h/(w+h)","2*.11*.04363/(.11+.04363)",dh,"m")},
        **dict.fromkeys(FALSE_FLAGS,False)}
