"""Synthetic byte/parser fixtures only. No fixture is native CFD evidence."""
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]/"tools"
spec = importlib.util.spec_from_file_location("snappy_phase_reduction", TOOLS/"reduce_frozen_rotor_snappy_phase.py")
tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)


def write(root, relative, data):
    raw = data if isinstance(data, bytes) else data.encode("ascii")
    path = root/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    return {"path":relative, "bytes":len(raw), "sha256":sha256(raw).hexdigest()}


def json_write(root, relative, data): return write(root, relative, tool.encoded(data))


def context():
    """Only source admission/native binary/mesh tests are mocked, never numerics."""
    stack = ExitStack()
    stack.enter_context(patch.object(tool.preparation,"verify_output",side_effect=lambda root:{
        "report":tool.record(root,tool.preparation.REPORT)[0],"reportFile":tool.record(root,tool.preparation.REPORT)[1]}))
    stack.enter_context(patch.object(tool,"binary_identity"))
    stack.enter_context(patch.object(tool.geometry,"native_mesh",return_value={"cellCount":2,"nodeCount":2,"fixtureKind":"SYNTHETIC-MESH-NOT-RESEARCH"}))
    return stack


def fixture(root, *, residual=1e-6, vapor=0., outlet_vapor=0., missing_sampler=None, minimum_pressure=200000.,
            outlet_flow=90., interface_flow=-90., max_co=.1, continuity=1e-6, head_drift=0.):
    study=tool.protocol.prepare_study(); helper=tool.preparation; period=helper.PERIOD_S; dt=helper.MAX_DT_S; end=helper.DEFAULT_DURATION_S
    authored=[]
    for name,raw in sorted(helper.native_recipe(study).items()):
        row=write(root,"case/"+name,raw); authored.append({**row,"path":name})
    meshes=[]
    for name in helper.MESH_FILES:
        row=write(root,"case/constant/polyMesh/"+name,"SYNTHETIC-BOUND-MESH-NOT-RESEARCH:"+name)
        meshes.append({**row,"path":"constant/polyMesh/"+name})
    identity={"schemaVersion":"untirta.frozen-rotor-snappy-geometry-identity.v1","fixtureKind":"SYNTHETIC-NOT-RESEARCH",
              "geometricApproximationQualified":False,"nativeCoordinateUnitsVerified":False,"mainApplicationWrites":False}
    geometry_row=json_write(root,"geometry-identity.json",identity)
    source=json_write(root,"source-baseline/synthetic-source-marker.json",{"fixtureKind":"SYNTHETIC-NOT-RESEARCH","singlePhaseHeadM":-999})
    model_rows=[r for r in authored if r['path'] in helper.MODEL_FILES]
    prep={"schemaVersion":helper.SCHEMA,"mode":helper.MODE,"inletAbsoluteTotalPressurePa":300000.,
          "sourceNumericalBaselineQualified":True,"sourceActualCfdRunVerified":True,**{k:False for k in helper.FALSE_FLAGS},
          "sourceGeometryIdentityFile":geometry_row,"sourceGeometryControlSha256":sha256(tool.encoded([geometry_row])).hexdigest(),
          "sourceBaselineEvidenceSha256":"a"*64,"sourceEvidenceFileRecords":[source],"preservedNativeMeshFiles":meshes,
          "authoredInputFiles":authored,"authoredCavitationModelSha256":sha256(tool.encoded(model_rows)).hexdigest(),
          "declaredStudyInputs":study['input'],"physicalBasis":study['physicalBasis'],"temporalPlan":helper.temporal_plan(dt,end),
          "absolutePressureMapping":{"nativeField":"p_rgh","nativeUnit":"Pa","nativeGravityMS2":[0,0,0],"referenceAdditionPa":0},
          "components":[{"component":"impeller","cellCount":1,"nodeCount":1},{"component":"volute","cellCount":1,"nodeCount":1}]}
    prep_row=json_write(root,helper.REPORT,prep)
    runtime_row=json_write(root,"phase-runtime-identity.json",{"fixtureKind":"SYNTHETIC-NOT-A-BINARY-IDENTITY"})
    bound=tool.build_manifest(root)
    manifest_row=json_write(root,"phase-input-manifest.json",bound['manifest']); study_row=json_write(root,"phase-classroom-study.json",bound['study'])
    rho,g=study['input']['densityKgM3'],study['input']['gravityMS2']; times=[i*dt for i in range(1,round(end/dt)+1)];times[-1]=end
    values={"weightedInlet":(300000-2*rho,300000,4,1),"weightedOutlet":(300000+10*rho*g-4.5*rho,300000+10*rho*g,9,1-outlet_vapor),
            "massFlowInlet":(-90,),"massFlowOutlet":(outlet_flow,),"massFlowMagnitudeInlet":(90,),"massFlowMagnitudeOutlet":(abs(outlet_flow),),
            "waterMinimumInlet":(1,),"waterMinimumOutlet":(1-outlet_vapor,),"globalPressureMinimum":(minimum_pressure,),
            "globalWaterMinimum":(1-vapor,),"globalWaterMaximum":(1,),"massFlowRotorInterface":(90,),
            "massFlowStatorInterface":(interface_flow,),"massFlowMagnitudeRotorInterface":(90,),"massFlowMagnitudeStatorInterface":(abs(interface_flow),)}
    for name,(columns,section) in tool.sampler_roles().items():
        if name==missing_sampler:continue
        header=(f"# Selection : patch({section})\n# Faces : 3\n# Area : 0.5\n" if section else "")+"# Time "+" ".join(columns)+"\n"
        body=[]
        for t in times:
            row=list(values[name])
            if name=="weightedOutlet":row[0]+=head_drift*rho*g*t/end;row[1]+=head_drift*rho*g*t/end
            body.append(format(t,'.17g')+' '+' '.join(format(v,'.17g') for v in row)+'\n')
        write(root,'case/postProcessing/'+name+'/0/'+('surfaceFieldValue.dat' if section else 'volFieldValue.dat'),header+''.join(body))
    prefix="Build : 12-"+tool.geometry.surface.SOURCE_COMMIT[:12]+"\n"
    coverage="Source min/average/max coverage = 1/1/1\nTarget min/average/max coverage = 1/1/1\n"
    check=prefix+"cells: 2\nMin volume = 1e-6. Max volume = 2e-6. Total volume = 3e-6.\nMesh OK.\n"+coverage+"End\n"
    chunks=[prefix,"Selecting cavitation model SchnerrSauer\n"]
    for t in times:
        chunks.append(f"Courant Number mean: 0.01 max: {max_co}\nInterface Courant Number mean: 0.001 max: 0.01\ndeltaT = {dt:.17g}\nTime = {t:.17g}s\n")
        for outer in range(1,4):
            chunks.append(f"PIMPLE: iteration {outer}\n")
            for name in (("Ux","Uy","Uz","p_rgh","k","omega") if outer==3 else ("Ux","Uy","Uz","p_rgh")):
                chunks.append(f"solver: Solving for {name}, Initial residual = {residual}, Final residual = 1e-10, No Iterations 2\n")
            chunks.append(f"GAMG: Solving for p_rgh, Initial residual = 1e-10, Final residual = 1e-12, No Iterations 1\ntime step continuity errors : sum local = {continuity}, global = -1e-7, cumulative = -1e-5\n")
    solver=''.join(chunks)+"End\n";stages=[]
    for i,(program,text) in enumerate((("checkMesh",check),("foamRun",solver))):
        row=write(root,f'logs/{i:02d}-{program}.log',text)
        stages.append({'program':program,'exitCode':0,'elapsedSeconds':1,'log':Path(row['path']).name,'logBytes':row['bytes'],'logSha256':row['sha256']})
    endpoint=format(end,'.17g')
    for name in tool.fields.END_FIELDS:
        kind='volVectorField' if name=='U' else 'volScalarField';value='(1 2 3)' if name=='U' else '300000' if name in {'p','p_rgh'} else '1'
        write(root,f'case/{endpoint}/{name}',f'FoamFile {{format ascii; class {kind}; object {name};}}\ndimensions [{tool.fields.FIELD_DIMENSIONS[name]}];\ninternalField uniform {value};\nboundaryField {{}}\n')
    final,_=tool.fields.final_fields(root,prep['temporalPlan'],2,solver)
    import run_frozen_rotor_snappy_phase as runner
    raw={"schemaVersion":tool.EXECUTION_SCHEMA,"status":"SNAPPY_ACTIVE_PHASE_REFERENCE_EXECUTED_ACCEPTANCE_REQUIRED",
         "mode":helper.MODE,"physicalModel":"steady-cavitation-MRF","inletAbsoluteTotalPressurePa":300000.,
         "absolutePressureMapping":prep['absolutePressureMapping'],"classroomMetresPerSourceCoordinateUnit":1,
         "exampleDataCommit":tool.native.case_tool.inventory.PIN,"githubRunId":"synthetic-parser-fixture-not-research",
         **{k:False for k in runner.FALSE_FLAGS},**{k:True for k in ("actualCfdRunVerified","simulationExecuted","nativeCheckMeshVerified",
            "nativeGeometryQualityVerified","physicalWallTypesVerified","coupledInterfaceCoverageVerified","sourceNumericalBaselineQualified",
            "stationaryMrfRelaxationExecuted","nativeSolverCompleted","cavitationModelExecuted","finiteNativeEndFieldsVerified","inputArtifactsImmutable","sourceBaselineImmutable")},
         "preparationReportSha256":prep_row['sha256'],"phaseRuntimeIdentitySha256":runtime_row['sha256'],
         "phaseInputManifestSha256":manifest_row['sha256'],"phaseStudySha256":study_row['sha256'],"sourceBaselineEvidenceSha256":"a"*64,
         "authoredCavitationModelSha256":prep['authoredCavitationModelSha256'],"temporalPlan":prep['temporalPlan'],"stages":stages,
         "mergedNativeMeshEvidence":{"cellCount":2,"nodeCount":2,"fixtureKind":"SYNTHETIC-MESH-NOT-RESEARCH"},
         "nativeMeshQuality":tool.native.parse_mesh_quality(check),"finalNativeFieldRecords":final,
         "nativeEndTimeS":end,"nativeSamplingRecords":tool.fields.sampling_records(root),
         "limits":runner.LIMITS,"workspaceBytes":1048576,"elapsedSeconds":1}
    json_write(root,'phase-change-execution.json',raw)
    return bound


def refresh(root):
    """Update synthetic producer descriptors, permitting semantic negative tests."""
    raw=tool.record(root,'phase-change-execution.json')[0]
    for stage in raw['stages']:
        data=(root/'logs'/stage['log']).read_bytes();stage.update(logBytes=len(data),logSha256=sha256(data).hexdigest())
    raw['nativeSamplingRecords']=tool.fields.sampling_records(root)
    json_write(root,'phase-change-execution.json',raw)


class ApproximateActivePhaseReducerTests(unittest.TestCase):
    def test_missing_actual_evidence_never_creates_phase_head(self):
        with tempfile.TemporaryDirectory() as temp:
            result=tool.reduce_native_phase(temp)
            self.assertFalse(result['actualCfdRunVerified']);self.assertFalse(result['phaseH0Eligible']);self.assertEqual(result['stages'],{})

    def test_scoped_synthetic_qualification_uses_active_head_and_absolute_pa(self):
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root);result=tool.reduce_native_phase(root)
            self.assertTrue(result['numericalPhaseReferenceQualified'],result['diagnostics'])
            self.assertAlmostEqual(result['activePhaseReferenceHeadM'],10)
            self.assertAlmostEqual(result['stages']['npsha']['result']['value'],(300000-2*998.2-2339)/(998.2*9.81)+4/(2*9.81))
            self.assertAlmostEqual(result['statisticalWindowS'][1]-result['statisticalWindowS'][0],3*tool.preparation.PERIOD_S)
            self.assertTrue(result['actualCfdRunVerified']);self.assertTrue(result['interfaceCoverageVerified'])
            for claim in tool.FALSE_CLAIMS:self.assertFalse(result[claim],claim)
            self.assertEqual(result['nativeObservation']['nativeEvidence']['sha256'],sha256(tool.encoded(result['nativeEvidenceRecords'])).hexdigest())

    def test_large_initial_residual_does_not_hide_behind_small_linear_final_residual(self):
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root,residual=.1);result=tool.reduce_native_phase(root)
            self.assertTrue(result['actualCfdRunVerified']);self.assertFalse(result['phaseH0Eligible'])
            self.assertIn('CFD_RESIDUAL_CONVERGENCE_REQUIRED',str(result['diagnostics']));self.assertEqual(set(result['stages']),{'head','npsha'})

    def test_real_global_vapor_or_flashing_pressure_prevents_high_pressure_h0(self):
        for kwargs in ({'vapor':.002},{'minimum_pressure':2000}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root,**kwargs);result=tool.reduce_native_phase(root)
                self.assertTrue(result['actualCfdRunVerified']);self.assertFalse(result['phaseH0Eligible']);self.assertNotIn('activePhaseReferenceHeadM',result)

    def test_liquid_section_head_gate_keeps_valid_independent_npsha(self):
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root,outlet_vapor=.01);result=tool.reduce_native_phase(root)
            self.assertTrue(result['actualCfdRunVerified']);self.assertEqual(set(result['stages']),{'npsha'});self.assertFalse(result['phaseH0Eligible'])

    def test_missing_outlet_keeps_valid_inlet_but_never_active_reference(self):
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root,missing_sampler='weightedOutlet');result=tool.reduce_native_phase(root)
            self.assertTrue(result['actualCfdRunVerified']);self.assertEqual(set(result['stages']),{'npsha'});self.assertFalse(result['phaseH0Eligible'])

    def test_flow_interface_continuity_courant_and_head_stability_are_independent_gates(self):
        checks=({'outlet_flow':80},{'interface_flow':-80},{'continuity':.01},{'max_co':.8},{'head_drift':2})
        for kwargs in checks:
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root,**kwargs);result=tool.reduce_native_phase(root)
                self.assertTrue(result['actualCfdRunVerified']);self.assertFalse(result['phaseH0Eligible']);self.assertTrue(result['diagnostics'])

    def test_source_qualification_model_mesh_or_manifest_tamper_is_not_native_acceptance(self):
        for relative,old,new in ((tool.preparation.REPORT,b'"sourceNumericalBaselineQualified":true',b'"sourceNumericalBaselineQualified":false'),
                                 ('case/constant/fvModels',b'SchnerrSauer',b'OtherModel'),
                                 ('case/constant/polyMesh/owner',b'owner',b'changed'),
                                 ('phase-input-manifest.json',b'300000.0',b'299999.0')):
            with self.subTest(relative=relative),tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root);path=root/relative;before=path.read_bytes();assert old in before;path.write_bytes(before.replace(old,new))
                result=tool.reduce_native_phase(root);self.assertFalse(result['actualCfdRunVerified']);self.assertFalse(result['phaseH0Eligible'])

    def test_actual_cavitation_selection_required_and_sliding_not_silently_accepted(self):
        for replacement in ('Selecting cavitation model OtherModel','Selecting cavitation model SchnerrSauer\nSelecting solidBodyMotionFunction rotatingMotion'):
            with self.subTest(replacement=replacement),tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root);path=root/'logs/01-foamRun.log';path.write_bytes(path.read_bytes().replace(b'Selecting cavitation model SchnerrSauer',replacement.encode()))
                refresh(root);result=tool.reduce_native_phase(root);self.assertFalse(result['actualCfdRunVerified'])

    def test_actual_endpoint_units_or_digest_mismatch_never_passes(self):
        for replacement in (b'dimensions [0 2 -2 0 0 0 0]',b'dimensions [1 -1 -2 0 0 0 0];\ninternalField uniform nan'):
            with self.subTest(replacement=replacement),tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root);end=format(tool.preparation.DEFAULT_DURATION_S,'.17g');path=root/f'case/{end}/p_rgh'
                path.write_bytes(path.read_bytes().replace(b'dimensions [1 -1 -2 0 0 0 0]',replacement))
                result=tool.reduce_native_phase(root);self.assertFalse(result['actualCfdRunVerified']);self.assertFalse(result['phaseH0Eligible'])
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root);end=format(tool.preparation.DEFAULT_DURATION_S,'.17g');path=root/f'case/{end}/p_rgh'
            path.write_bytes(path.read_bytes().replace(b'300000',b'401325'))
            raw=tool.record(root,'phase-change-execution.json')[0]
            raw['finalNativeFieldRecords']=tool.fields.final_fields(root,raw['temporalPlan'],2,(root/'logs/01-foamRun.log').read_text())[0]
            json_write(root,'phase-change-execution.json',raw)
            result=tool.reduce_native_phase(root);self.assertFalse(result['actualCfdRunVerified'])
            self.assertIn('CFD_SNAPPY_PHASE_ABSOLUTE_P_PRGH_RECONSTRUCTION_REQUIRED',str(result['diagnostics']))

    def test_all_internal_pressure_values_match_g_zero_pa_reconstruction(self):
        def scalar(name, body):
            return (f'FoamFile {{format ascii; class volScalarField; object {name};}}\ndimensions [1 -1 -2 0 0 0 0];\ninternalField '+body+';\nboundaryField {}\n').encode()
        p=scalar('p','nonuniform List<scalar> 2 (300000 400000)')
        prgh=scalar('p_rgh','nonuniform List<scalar> 2 (300000 400000)')
        result=tool.absolute_pressure_reconstruction(p,prgh,2);self.assertEqual(result['allInternalCellsChecked'],2);self.assertEqual(result['maximumAbsoluteDifferencePa'],0)
        with self.assertRaises(tool.protocol.ProtocolError):tool.absolute_pressure_reconstruction(p,scalar('p_rgh','uniform 300000'),2)

    def test_wrong_sampling_units_total_pressure_and_incomplete_clock_reject(self):
        for role,old,new in (('massFlowInlet',b'sum(rhoPhi)',b'sum(phi)'),('weightedInlet',b'300000',b'401325')):
            with self.subTest(role=role),tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root);path=root/f'case/postProcessing/{role}/0/surfaceFieldValue.dat'
                path.write_bytes(path.read_bytes().replace(old,new));refresh(root);result=tool.reduce_native_phase(root)
                self.assertTrue(result['actualCfdRunVerified']);self.assertFalse(result['phaseH0Eligible'])
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root);path=root/'logs/01-foamRun.log';path.write_bytes(path.read_bytes().replace(b'deltaT = ',b'unboundTimeStep = ',1))
            refresh(root);result=tool.reduce_native_phase(root);self.assertFalse(result['phaseH0Eligible'])

    def test_relocation_uses_current_bytes_and_preserves_historical_preparation(self):
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp)/'original';root.mkdir();fixture(root);moved=Path(temp)/'copy';shutil.copytree(root,moved)
            result=tool.reduce_native_phase(moved);self.assertTrue(result['phaseH0Eligible'],result['diagnostics'])

    def test_raw_resource_bounds_and_unperformed_claims_are_verified(self):
        changes=(lambda v:v.update(elapsedSeconds=1200),lambda v:v.update(workspaceBytes=1024**3+1),
                 lambda v:v['limits'].update(maximumCpus=2),lambda v:v['stages'][0].update(elapsedSeconds=1200),
                 lambda v:v.update(phaseFamilyReferenceEligible=True))
        for change in changes:
            with tempfile.TemporaryDirectory() as temp,context():
                root=Path(temp);fixture(root);raw=tool.record(root,'phase-change-execution.json')[0];change(raw);json_write(root,'phase-change-execution.json',raw)
                result=tool.reduce_native_phase(root);self.assertFalse(result['actualCfdRunVerified']);self.assertFalse(result['phaseH0Eligible'])

    def test_actual_evidence_change_during_reduction_revokes_current_projection(self):
        with tempfile.TemporaryDirectory() as temp,context():
            root=Path(temp);fixture(root);original=tool.observation
            def changed(*args):
                original(*args)
                path=root/'case/postProcessing/globalPressureMinimum/0/volFieldValue.dat';path.write_bytes(path.read_bytes()+b'\n# SYNTHETIC-MUTATION\n')
            with patch.object(tool,'observation',side_effect=changed):result=tool.reduce_native_phase(root)
            self.assertEqual(result['status'],'NativeEvidenceChangedDuringReduction');self.assertFalse(result['actualCfdRunVerified'])
            self.assertFalse(result['phaseH0Eligible']);self.assertEqual(result['stages'],{});self.assertNotIn('nativeObservation',result)


class ActualBinaryDescriptorTests(unittest.TestCase):
    def identity(self):
        raw=b'SYNTHETIC-BINARY-UNIT-FIXTURE-NOT-NATIVE'
        identity={'schemaVersion':tool.RUNTIME_SCHEMA,'openFoamFoundationVersion':'12','packageVersion':'20250206',
                  'officialDebSha256':tool.geometry.surface.PACKAGE_SHA,'expectedSourceCommit':tool.geometry.surface.SOURCE_COMMIT,
                  'mainApplicationWrites':False,'binaries':[{'program':p,'path':'/opt/openfoam12/bin/'+p,'bytes':len(raw),
                  'sha256':sha256(raw).hexdigest(),'packageOwnership':'openfoam12: /opt/openfoam12/bin/'+p} for p in tool.PROGRAMS]}
        return raw,identity

    def test_current_actual_bytes_are_required_for_both_pinned_package_programs(self):
        raw,identity=self.identity()
        with patch.object(tool.native.case_tool,'read_regular',return_value=raw) as reader:tool.binary_identity(identity);self.assertEqual(reader.call_count,2)
        with patch.object(tool.native.case_tool,'read_regular',return_value=raw+b'tampered'),self.assertRaises(tool.protocol.ProtocolError):tool.binary_identity(identity)

    def test_wrong_package_path_or_program_inventory_rejects_before_native_access(self):
        for mutate in (lambda i:i.update(packageVersion='other'),lambda i:i['binaries'][0].update(path='/tmp/checkMesh'),lambda i:i['binaries'].pop()):
            raw,identity=self.identity();mutate(identity)
            with patch.object(tool.native.case_tool,'read_regular',side_effect=AssertionError('Wrong provenance cannot read a native binary.')),\
                 self.assertRaises(tool.protocol.ProtocolError):tool.binary_identity(identity)


if __name__=='__main__':unittest.main()
