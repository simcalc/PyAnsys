"""Authored recipe/byte/data tests only. No solver or accepted H0 is executed."""
from copy import deepcopy
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import teaching_pump_sliding_case as case
from test_teaching_pump_pressure_case import source_fixture as upstream_fixture


def source_fixture(root):
    source,upstream=upstream_fixture(root)
    state=source/'case/0.165517241379/U';state.parent.mkdir();state.write_bytes(b'SYNTHETIC-POSITIVE-STATE-NOT-NATIVE\n')
    return source,upstream


def toy_mesh(root):
    """Only 2-cell label/field shape, not valid pump or CFD geometry."""
    mesh=root/'case/constant/polyMesh';mesh.mkdir(parents=True,exist_ok=True)
    for name,kind,body in (('points','vectorField','3 ((0 0 0)(1 0 0)(0 1 0))'),('owner','labelList','5 (0 0 0 1 1)'),('neighbour','labelList','1 (1)'),('faces','faceList','5 (3(0 1 2)3(0 1 2)3(0 1 2)3(0 1 2)3(0 1 2))')):
        (mesh/name).write_text(case.base.foam_header(name,kind,'constant/polyMesh')+body)
    patches=[];start=1
    for name in sorted(set().union(*case.base.PATCHES.values())):
        size=1 if name in case.base.INTERFACES|{'inlet','mass-flow-inlet-11'} else 0
        kind='wall' if name in case.base.ROTATING|case.base.STATIONARY else 'patch'
        patches.append(f'{name} {{type {kind}; nFaces {size}; startFace {start};}}');start+=size
    for name,kind in sorted(case.phase.NCC_PATCHES.items()):
        original=name.split('_on_',1)[1];patches.append(f'{name} {{type {kind}; nFaces 0; startFace 5; originalPatch {original};}}')
    (mesh/'boundary').write_text(case.base.foam_header('boundary','polyBoundaryMesh','constant/polyMesh')+str(len(patches))+' ('+' '.join(patches)+')')
    (mesh/'cellZones').write_text(case.base.foam_header('cellZones','cellZoneList','constant/polyMesh')+'2 (rotating-fluid {type cellZone; cellLabels List<label> 1(0);} stationary-fluid {type cellZone; cellLabels List<label> 1(1);})')
    return mesh


def cache(root,time_name):
    mesh=root/'case/constant/polyMesh';patches=case.phase.geometry.boundary_entries(mesh/'boundary');blocks=[]
    for name,p in patches.items():
        if name in case.phase.NCC_PATCHES:
            original=name.split('_on_',1)[1];count=1 if p['type']=='nonConformalCyclic' else 0
            label=patches[original]['startFace'];value=f'nonuniform List<label> {count}('+(str(label) if count else '')+')'
            body=f'type nonConformalPolyFaces; patchType {p["type"]}; size {count}; value {value};'
        else:body='type polyFaces;'
        blocks.append(name+' {'+body+'}')
    return (case.base.foam_header('polyFaces','surfaceLabelField',time_name+'/fvMesh')+'dimensions []; internalField uniform -1; boundaryField {'+' '.join(blocks)+'}\n').encode()


class SlidingRecipeTests(unittest.TestCase):
    def study(self):return case.phase.baseline.study_packet({}, {k:'a'*64 for k in case.reference.protocol.CONTROL_KEYS})

    def test_only_declared_motion_velocity_clock_change_from_active_reference(self):
        study=self.study();fixed=case.phase.native_recipe(study,default_faces=False);moving=case.native_recipe(study,False)
        self.assertEqual(set(fixed)-set(moving),{'constant/MRFProperties'})
        self.assertEqual(set(moving)-set(fixed),{'constant/dynamicMeshDict'})
        self.assertEqual({p for p in fixed if p in moving and fixed[p]!=moving[p]},{'0/U','system/controlDict'})
        self.assertTrue(all(fixed[p]==moving[p] for p in case.MODEL_FILES))
        self.assertIn(b'cellZone rotating-fluid;',moving['constant/dynamicMeshDict']);self.assertIn(b'omega 1450 [rpm];',moving['constant/dynamicMeshDict'])
        self.assertNotIn(b'MRFnoSlip',moving['0/U']);self.assertNotIn('0/p',moving)
        _,bc=case.phase.baseline_case.fields.boundary_blocks(moving['0/U'],'U')
        for p in case.base.ROTATING:self.assertEqual(bc[p]['type'],'movingWallVelocity')
        for p in case.base.INTERFACES:self.assertEqual(bc[p]['type'],'movingWallSlipVelocity')
        for p in case.base.STATIONARY:self.assertEqual(bc[p]['type'],'noSlip')
        self.assertEqual(bc['inlet']['type'],'pressureInletOutletVelocity')
        self.assertEqual(bc['mass-flow-inlet-11']['type'],'flowRateOutletVelocity')
        self.assertIn(b'internalField uniform 300000;',moving['0/p_rgh']);self.assertIn(b'p0 uniform 300000;',moving['0/p_rgh'])

    def test_five_writes_clock_is_explicit_short_pilot_never_time_convergence(self):
        files=case.native_recipe(self.study());control=files['system/controlDict'].decode()
        self.assertIn('purgeWrite 0;',control);self.assertIn('endTime '+format(1.25*60/1450,'.17g')+';',control)
        self.assertIn('writeInterval '+format(60/1450/4,'.17g')+';',control)
        self.assertIn('adjustTimeStep yes;',control);self.assertEqual(len(case.TEMPORAL_PLAN['expectedWriteTimesS']),5)
        self.assertTrue(case.TEMPORAL_PLAN['adaptiveTimeStep']);self.assertFalse(case.TEMPORAL_PLAN['fixedGridClockVerified'])
        self.assertFalse(case.TEMPORAL_PLAN['durationIsAcceptanceProof']);self.assertEqual(case.MOTION_PLAN['initialPhaseRad'],0)
        self.assertEqual(case.MOTION_PLAN['axis'],[0,0,1]);self.assertEqual(case.LIMITS['plannedNativeMaximumSeconds'],1200)
        self.assertEqual(case.LIMITS['maximumWorkspaceBytes'],1024**3)

    def test_wrong_si_study_or_unknown_initial_wall_recipe_fails(self):
        study=self.study();study['input']['speedRpm']=1800
        with self.assertRaises(ValueError):case.native_recipe(study)
        original=case.phase.native_recipe
        def changed(*args,**kwargs):
            authored=original(*args,**kwargs);authored['0/U']=authored['0/U'].replace(b'type MRFnoSlip;',b'type slip;',1);return authored
        with patch.object(case.phase,'native_recipe',side_effect=changed),self.assertRaises(ValueError):case.native_recipe(self.study())


class SlidingPreparationTests(unittest.TestCase):
    def fixture(self,root):return source_fixture(root)

    def test_full_current_reference_and_mesh_copy_replay_with_false_pilot_flags(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'RUNNER_TEMP':temp}):
            root=Path(temp);source,verify=self.fixture(root)
            with patch.object(case,'verify_reference',side_effect=verify):
                prepared=case.prepare(source,root/'pilot');current=case.verify_output(root/'pilot')
            self.assertEqual(current['report'],prepared);self.assertTrue(all(prepared[k] is False for k in case.FALSE_FLAGS))
            self.assertTrue(prepared['sourceActivePhaseH0NumericallyQualified']);self.assertFalse(prepared['baselineFieldsCopied'])
            self.assertFalse((root/'pilot/case/0.165517241379/U').exists());self.assertTrue((root/'pilot/source-reference/case/0.165517241379/U').exists())
            self.assertEqual(prepared['sourceActivePhaseHeadM'],10.);self.assertFalse(prepared['slidingH0Qualified'])
            self.assertEqual([a[0] for a in prepared['nativeCommandArguments']],['checkMesh','foamRun'])
            for row in prepared['preservedNativeMeshFiles']:self.assertEqual((root/'pilot/case'/row['path']).read_bytes(),(source/'case'/row['path']).read_bytes())

    def test_output_relocation_without_original_source_preserves_historical_argv(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'RUNNER_TEMP':temp}):
            root=Path(temp);source,verify=self.fixture(root)
            with patch.object(case,'verify_reference',side_effect=verify):
                report=case.prepare(source,root/'pilot');(root/'pilot').rename(root/'moved');source.rename(root/'absent')
                self.assertEqual(case.verify_output(root/'moved')['report'],report)
                p=root/'moved'/case.REPORT;changed=json.loads(p.read_bytes());changed['nativeCommandArguments'][1][2]=str(root/'other/case');p.write_bytes(case.encoded(changed))
                with self.assertRaises(ValueError):case.verify_output(root/'moved')

    def test_model_mesh_clock_or_metadata_mutations_cannot_relabel_a_pilot(self):
        for relative in ('case/constant/polyMesh/points','case/constant/fvModels','case/constant/dynamicMeshDict','case/0/p_rgh','case/system/controlDict',case.IDENTITY):
            with self.subTest(path=relative),tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'RUNNER_TEMP':temp}):
                root=Path(temp);source,verify=self.fixture(root)
                with patch.object(case,'verify_reference',side_effect=verify):
                    case.prepare(source,root/'pilot');path=root/'pilot'/relative;path.write_bytes(path.read_bytes()+b' changed')
                    with self.assertRaises(ValueError):case.verify_output(root/'pilot')

    def test_unqualified_h0_prevents_any_output_before_copy(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'RUNNER_TEMP':temp}):
            root=Path(temp);source,_=self.fixture(root)
            with patch.object(case.reference,'verify_reference',side_effect=ValueError('Current H0 not qualified')):
                with self.assertRaises(ValueError):case.prepare(source,root/'pilot')
            self.assertFalse((root/'pilot').exists())

    def test_capacity_projects_actual_source_state_and_refuses_five_write_overflow(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'RUNNER_TEMP':temp}):
            root=Path(temp);source,verify=self.fixture(root);packet=verify(source);authored=case.native_recipe(packet['study'])
            proof=case.output_capacity(packet,authored);self.assertEqual(proof['plannedRetainedWriteCount'],5);self.assertFalse(proof['projectionIsGuaranteedNativeBound'])
            packet=deepcopy(packet);packet['sourceRecords'].append({'path':'case/0.16/huge','bytes':300*1024**2,'sha256':'a'*64})
            with self.assertRaisesRegex(ValueError,'PILOT_OUTPUT_CAPACITY_REQUIRED'):case.output_capacity(packet,authored)
            with patch.object(case,'verify_reference',side_effect=verify),patch.object(case,'MAX_WORKSPACE',1):
                with self.assertRaisesRegex(ValueError,'PILOT_OUTPUT_CAPACITY_REQUIRED'):case.prepare(source,root/'pilot')
            self.assertFalse((root/'pilot').exists())

    def test_source_change_during_copy_or_unknown_executable_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'RUNNER_TEMP':temp}):
            root=Path(temp);source,verify=self.fixture(root);write=case.base.write_exclusive;changed=False
            def copying(path,raw):
                nonlocal changed
                write(path,raw)
                if not changed and 'source-reference' in str(path):changed=True;(source/'case/0/p_rgh').write_bytes(b'changed')
            with patch.object(case,'verify_reference',side_effect=verify),patch.object(case.base,'write_exclusive',side_effect=copying):
                with self.assertRaises(ValueError):case.prepare(source,root/'pilot')
            self.assertFalse((root/'pilot').exists())
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'RUNNER_TEMP':temp}):
            root=Path(temp);source,verify=self.fixture(root)
            with patch.object(case,'verify_reference',side_effect=verify):
                case.prepare(source,root/'pilot');(root/'pilot/case/constant/MRFProperties').write_bytes(b'rotor{}')
                with self.assertRaises(ValueError):case.verify_output(root/'pilot')


class SlidingNativeDataTests(unittest.TestCase):
    def test_generated_startup_ncc_counts_use_labelled_source_phase0_proof_not_prefilled_cache(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'RUNNER_TEMP':temp}):
            root=Path(temp);source,verify=source_fixture(root);toy_mesh(source)
            source_cache=source/'case/constant/fvMesh/polyFaces';source_cache.parent.mkdir();source_cache.write_bytes(cache(source,'constant'))
            with patch.object(case,'verify_reference',side_effect=verify):case.prepare(source,root/'pilot')
            output=root/'pilot';self.assertFalse((output/'case/constant/fvMesh').exists())
            patches=case.phase.geometry.boundary_entries(output/'case/constant/polyMesh/boundary');blocks=[]
            for name,p in patches.items():
                if name in case.phase.NCC_PATCHES:
                    count=1 if p['type']=='nonConformalCyclic' else 0
                    body=f'type nonConformalCalculated; patchType {p["type"]}; value nonuniform List<scalar> {count}('+('1' if count else '')+');'
                else:body='type calculated; value uniform 1;'
                blocks.append(name+' {'+body+'}')
            raw=(case.base.foam_header('U2','volScalarField','0')+'dimensions [0 2 -2 0 0 0 0]; internalField uniform 0; boundaryField {'+' '.join(blocks)+'}').encode()
            (output/'case/0/U2').write_bytes(raw)
            with patch.object(case,'verify_reference',side_effect=verify):
                with self.assertRaises(ValueError):case.verify_output(output)
                verified=case.verify_output(output,after_native_execution=True)
            proof=next(p for p in verified['nativeGeneratedDataEvidence'] if p['path']=='case/0/U2')
            self.assertEqual(proof['initialAddressingCountSource']['path'],'source-reference/case/constant/fvMesh/polyFaces')
            self.assertFalse(proof['currentGeneratedAddressingClaim']);self.assertFalse(proof['numericalAcceptanceInherited'])
            self.assertFalse((output/'case/constant/fvMesh').exists())
            (output/'case/0/U2').write_bytes(raw.replace(b'List<scalar> 1(1)',b'List<scalar> 2(1 1)',1))
            with patch.object(case,'verify_reference',side_effect=verify),self.assertRaises(ValueError):case.verify_output(output,after_native_execution=True)

    def test_positive_native_cache_keeps_actual_location_and_exact_patch_ranges(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh=toy_mesh(root);raw=cache(root,'0.01');before=sha256(raw).hexdigest()
            proof=case.dynamic_polyfaces(raw,mesh,'0.01','case/0.01/fvMesh/polyFaces')
            self.assertEqual(proof['nativeTimeS'],.01);self.assertFalse(proof['dynamicCoverageVerified']);self.assertEqual(sha256(raw).hexdigest(),before)
            for changed in (raw.replace(b'"0.01/fvMesh"',b'"constant/fvMesh"'),raw.replace(b'class surfaceLabelField;',b'class dictionary;'),raw.replace(b'value nonuniform List<label> 1(',b'value nonuniform List<label> 2(',1)):
                with self.assertRaises(ValueError):case.dynamic_polyfaces(changed,mesh,'0.01','case/0.01/fvMesh/polyFaces')
            source=case.phase.geometry.boundary_entries(mesh/'boundary');left=source['interface-impeller-outlet']['startFace'];right=source['interface-volute-inlet']['startFace']
            changed=raw.replace(('1('+str(left)+')').encode(),('1('+str(right)+')').encode(),1)
            with self.assertRaises(ValueError):case.dynamic_polyfaces(changed,mesh,'0.01','case/0.01/fvMesh/polyFaces')

    def test_moved_points_aux_fields_and_adaptive_clock_are_bound_without_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh=toy_mesh(root);folder=root/'case/0.01';(folder/'polyMesh').mkdir(parents=True)
            (folder/'polyMesh/points').write_text(case.base.foam_header('points','vectorField','0.01/polyMesh')+'3 ((0 0 0)(0 1 0)(-1 0 0))')
            for name,kind,dims,values in (('Uf','surfaceVectorField','0 1 -1 0 0 0 0','nonuniform List<vector> 1 ((1 0 0))'),
                ('meshPhi','surfaceScalarField','0 3 -1 0 0 0 0','nonuniform List<scalar> 1 (0.1)'),('rAU','volScalarField','-1 3 1 0 0 0 0','uniform 1')):
                (folder/name).write_text(case.base.foam_header(name,kind,'0.01')+'dimensions ['+dims+']; internalField '+values+'; boundaryField {}')
            (folder/'uniform').mkdir();(folder/'uniform/time').write_text(case.base.foam_header('time','dictionary','0.01/uniform')+'beginTime 0; value 0.01; name "0.01"; index 100; deltaT 0.0001; deltaT0 0.0001;')
            rows,proofs=case.positive_time_data(root,folder,mesh);self.assertEqual(len(rows),5)
            self.assertTrue(all(p['checkpointCompletenessVerified'] is False and p['numericalAcceptanceInherited'] is False for p in proofs))
            path=folder/'Uf';path.write_bytes(path.read_bytes().replace(b'List<vector> 1',b'List<vector> 2'))
            with self.assertRaises(ValueError):case.positive_time_data(root,folder,mesh)

    def test_native_data_rejects_unknown_code_dimensions_and_future_clock(self):
        raw=(case.base.foam_header('Uf','surfaceVectorField','0.01')+'dimensions [0 1 -1 0 0 0 0]; internalField uniform (1 0 0); boundaryField {}').encode()
        with self.assertRaises(ValueError):case.native_text(raw+b' code {run();}','Uf','surfaceVectorField','0.01')
        with self.assertRaises(ValueError):case.native_text(raw.replace(b'class surfaceVectorField;',b'class dictionary;'),'Uf','surfaceVectorField','0.01')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh=toy_mesh(root);folder=root/'case/0.01';folder.mkdir();(folder/'Uf').write_bytes(raw.replace(b'[0 1 -1 0 0 0 0]',b'[]'))
            with self.assertRaises(ValueError):case.positive_time_data(root,folder,mesh)
            (folder/'Uf').unlink();(folder/'unknown').write_bytes(raw)
            with self.assertRaises(ValueError):case.positive_time_data(root,folder,mesh)

    def test_only_named_dimensionless_phase_roles_accept_native_empty_dimensions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh=toy_mesh(root);folder=root/'case/0.01';folder.mkdir()
            for name,values in (('alpha.water','nonuniform List<scalar> 2 (0.25 1)'),('alpha.vapour','uniform 0')):
                (folder/name).write_text(case.base.foam_header(name,'volScalarField','0.01')+'dimensions []; internalField '+values+'; boundaryField {}')
            rows,proofs=case.positive_time_data(root,folder,mesh)
            self.assertEqual({Path(r['path']).name for r in rows},{'alpha.water','alpha.vapour'})
            self.assertTrue(all(p['actualInternalElementCount']==2 for p in proofs))
            self.assertTrue(all(p['phaseFractionNumericalTolerance']==1e-9 for p in proofs))
            path=folder/'alpha.water';valid=path.read_bytes()
            path.write_bytes(valid.replace(b'(0.25 1)',b'(-1e-10 1.0000000001)'))
            case.positive_time_data(root,folder,mesh)
            for changed in (valid.replace(b'(0.25 1)',b'(0.25 1.01)'),valid.replace(b'(0.25 1)',b'(-0.01 1)'),
                valid.replace(b'List<scalar> 2',b'List<scalar> 1'),valid.replace(b'(0.25 1)',b'(nan 1)')):
                path.write_bytes(changed)
                with self.assertRaises(ValueError):case.positive_time_data(root,folder,mesh)
            path.write_bytes(valid);(folder/'k').write_text(case.base.foam_header('k','volScalarField','0.01')+'dimensions []; internalField uniform 1; boundaryField {}')
            with self.assertRaises(ValueError):case.positive_time_data(root,folder,mesh)


if __name__=='__main__':unittest.main()
