"""Pure arithmetic/current-byte fixtures only; none represents actual CFD."""
from array import array
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location("teaching_transient_audit_tests",Path(__file__).resolve().parents[1]/"tools/teaching_pump_transient_audit.py")
tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)


def tetra():
    return {"points":[(0.,0.,0.),(1.,0.,0.),(0.,1.,0.),(0.,0.,1.)],
        "faces":[(0,2,1),(0,1,3),(0,3,2),(1,2,3)],"owner":array('I',[0]*4),"neighbour":array('I'),
        "cellCount":1,"boundary":{"blade":{"type":"wall","nFaces":4,"startFace":0,"dictionary":"type wall; nFaces4; startFace0;"}},
        "topologySha256":"a"*64}


def rotate(points,angle):
    from math import cos,sin
    return [(cos(angle)*x-sin(angle)*y,sin(angle)*x+cos(angle)*y,z) for x,y,z in points]


def write(root,name,data):
    raw=data.encode('ascii') if isinstance(data,str) else data
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    return {"path":name,"bytes":len(raw),"sha256":sha256(raw).hexdigest()}


def field(name,time='0.1',*,value=None,empty_dimensions=False):
    kind,dims,role=tool.ROLES[name];vector=kind.endswith('VectorField')
    value=value if value is not None else '(0 0 0)' if vector else '300000' if name in {'p','p_rgh'} else '1'
    dimensions='' if empty_dimensions else dims
    return f'FoamFile {{format ascii; class {kind}; location "{time}"; object {name};}}\ndimensions [{dimensions}];\ninternalField uniform {value};\nboundaryField {{blade {{type calculated; value uniform {value};}}}}\n'


def snapshot_fixture(root,*,complete=True,times=('0.1',),motion_dictionary=True):
    mesh=tetra();records={}
    for name in ('points','faces','owner','neighbour','boundary','cellZones'):
        row=write(root,'case/constant/polyMesh/'+name,'SYNTHETIC TOPOLOGY; heavy source/motion proof is mocked '+name)
        records[row['path']]=row
    log=[]
    for i in range(1,3):log.append(f'deltaT = 0.1\nTime = {i/10:.17g}s\n')
    log=''.join(log)+'End\n';logrow=write(root,'logs/01-foamRun.log',log)
    moved=[]
    for time in times:
        names=set(tool.science.END_FIELDS)|({'phi','Uf','rAU','alphaPhi.water','meshPhi'} if complete else set())
        for name in names:write(root,'case/'+time+'/'+name,field(name,time,empty_dimensions=name=='alpha.water'))
        points=rotate(mesh['points'],float(time));entries=''.join('('+ ' '.join(format(v,'.17g') for v in p)+')' for p in points)
        moved.append(write(root,'case/'+time+'/polyMesh/points',f'FoamFile {{format ascii; class vectorField; location "{time}/polyMesh"; object points;}}\n4({entries})\n'))
        write(root,'case/'+time+'/fvMesh/polyFaces',f'FoamFile {{format ascii; class surfaceLabelField; location "{time}/fvMesh"; object polyFaces;}}\ndimensions [];internalField uniform -1;boundaryField {{blade {{type polyFaces;}}}}\n')
        write(root,'case/'+time+'/uniform/time',f'FoamFile {{format ascii; class dictionary; location "{time}/uniform"; object time;}}\nbeginTime 0;value {time};name "{time}";index {round(float(time)*10)};deltaT .1;deltaT0 .1;\n')
    inputs={**mesh,'records':records if motion_dictionary else list(records.values())}
    proof={'coordinateMotionVerified':True,'dynamicNccClockCoverageVerified':True,'nativeEvidenceRecords':list(records.values())+[logrow,*moved],
        'fixtureKind':'SYNTHETIC UPSTREAM MOTION ADMISSION ONLY; NOT NATIVE CFD'}
    module=types.SimpleNamespace(motion_inputs=lambda _:inputs,inspect=lambda *args:proof)
    return mesh,module


class PureAuditTests(unittest.TestCase):
    def test_rigid_rotation_preserves_every_actual_indexed_tetra_volume(self):
        mesh=tetra();result=tool.rigid_volume_check(mesh,mesh['points'],rotate(mesh['points'],.37))
        self.assertEqual(result['allCellsChecked'],1);self.assertAlmostEqual(result['oldVolumes'][0],1/6)
        changed=list(mesh['points']);changed[3]=(0.,0.,1.01)
        with self.assertRaisesRegex(tool.AuditIssue,'VOLUME_PRESERVATION'):tool.rigid_volume_check(mesh,mesh['points'],changed)

    def test_gcl_uses_actual_delta_t_and_every_signed_face_not_global_cancellation(self):
        # Two cells share face0. Their equal/opposite fluxes cannot be hidden
        # by a zero global sum while individual cell GCL is wrong.
        result=tool.discrete_gcl_check([1.,1.],[1.01,.99],[0,0,1],[1],[.1,0.,0.],.1)
        self.assertLess(result['maximumVolumeNormalizedResidual'],1e-15)
        with self.assertRaisesRegex(tool.AuditIssue,'DISCRETE_EULER_GCL'):
            tool.discrete_gcl_check([1.,1.],[1.,1.],[0,0,1],[1],[.1,0.,0.],.1)
        with self.assertRaises(tool.AuditIssue):tool.discrete_gcl_check([1.],[1.],[0],[],[0.],True)

    def test_relative_flux_requires_mesh_flux_and_absolute_face_velocity(self):
        result=tool.relative_flux_check([2.],[1.],[(3.,0.,0.)],[(1.,0.,0.)])
        self.assertEqual(result['allFacesChecked'],1)
        with self.assertRaisesRegex(tool.AuditIssue,'RELATIVE_FLUX_IDENTITY'):
            tool.relative_flux_check([3.],[1.],[(3.,0.,0.)],[(1.,0.,0.)])
        with self.assertRaises(tool.AuditIssue):tool.relative_flux_check([2.],[],[(3.,0.,0.)],[(1.,0.,0.)])

    def test_discrete_wall_chord_and_normal_correction_not_instantaneous_omega_cross_r(self):
        result=tool.moving_wall_check([(1.,0.,0.)],[(0.,1.,0.)],[(1.,0.,0.)],[.25],[(.25,1.,0.)],1.)
        self.assertFalse(result['instantaneousOmegaCrossRSubstituted'])
        with self.assertRaisesRegex(tool.AuditIssue,'DISCRETE_MOVING_WALL'):
            tool.moving_wall_check([(1.,0.,0.)],[(0.,1.,0.)],[(1.,0.,0.)],[.25],[(-1.,0.,0.)],1.)

    def test_native_no_slip_omitted_value_does_not_fabricate_zero_on_moving_mesh(self):
        mesh=tetra();raw=field('U').replace('type calculated; value uniform (0 0 0);','type noSlip;').encode()
        parsed=tool.field(raw,'U',mesh,{'blade':4},'0.1')
        self.assertNotIn('blade',parsed['boundary']);self.assertIn('blade',parsed['implicitBoundaryRoles'])
        checked=tool.stationary_wall_check([(1.,0.,0.)],[.25])
        self.assertFalse(checked['absoluteZeroVelocityEstablished']);self.assertTrue(checked['nativeBoundaryRuleReconstruction'])
        self.assertFalse(checked['storedVectorComparisonPerformed'])
        with self.assertRaisesRegex(tool.AuditIssue,'NOSLIP_NORMAL_CORRECTION'):
            tool.stationary_wall_check([(1.,0.,0.)],[.25],[(0.,0.,0.)])
        measured=tool.stationary_wall_check([(1.,0.,0.)],[.25],[(.25,0.,0.)])
        self.assertTrue(measured['storedVectorComparisonPerformed'])

    def test_native_roles_dimensions_shapes_and_code_are_not_metadata_waivers(self):
        mesh=tetra();raw=field('alpha.water',empty_dimensions=True).encode()
        parsed=tool.field(raw,'alpha.water',mesh,{'blade':4},'0.1');self.assertEqual(parsed['internal'][0],1.)
        for altered in (raw.replace(b'dimensions [];',b'dimensions [0 0 1 0 0 0 0];'),raw.replace(b'uniform 1',b'uniform 1e500'),
            raw.replace(b'type calculated;',b'type codedFixedValue;'),raw+b'#include bad\n',raw+b'\x00'):
            with self.subTest(raw=altered[-45:]),self.assertRaises((ValueError,UnicodeError)):tool.field(altered,'alpha.water',mesh,{'blade':4},'0.1')
        with self.assertRaises(tool.AuditIssue):tool.values('nonuniform List<vector> 1 ((1 2))',1,is_vector=True)

    def test_native_constant_outlet_function_is_data_only_and_exactly_reviewed(self):
        mesh=tetra();raw=field('U').replace('type calculated;','type flowRateOutletVelocity; volumetricFlowRate {type constant; value .09;}').encode()
        parsed=tool.field(raw,'U',mesh,{'blade':4},'0.1');self.assertEqual(parsed['patchTypes']['blade'],'flowRateOutletVelocity')
        with self.assertRaises(tool.AuditIssue):tool.field(raw.replace(b'type constant;',b'type table;'),'U',mesh,{'blade':4},'0.1')

    def test_adaptive_time_state_binds_logged_dt_not_authored_maximum(self):
        clock=tool.clock_history('deltaT = .01\nTime = .01s\ndeltaT = .005\nTime = .015s\nEnd\n')
        raw=b'FoamFile {format ascii;class dictionary;object time;location "0.015/uniform";}beginTime 0;value .015;name "0.015";index 2;deltaT .005;deltaT0 .01;'
        checked=tool.native_time(raw,'0.015',clock);self.assertAlmostEqual(checked['dtS'],.005)
        with self.assertRaises(tool.AuditIssue):tool.native_time(raw.replace(b'deltaT .005;',b'deltaT .01;'),'0.015',clock)
        with self.assertRaises(tool.AuditIssue):tool.clock_history('deltaT = .01\nTime = .02s\nEnd\n')

    def test_checkpoint_contains_scientific_and_flux_clock_ncc_points_not_four_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh,_=snapshot_fixture(root)
            records={};clock=tool.clock_history((root/'logs/01-foamRun.log').read_text())
            result=tool.read_snapshot(root,'0.1',mesh,clock,records)
            self.assertEqual(result['missingState'],[]);self.assertEqual(len(result['fields']),12)
            before=(root/'case/0.1/alpha.water').read_bytes();self.assertIn(b'dimensions [];',before)
            self.assertEqual((root/'case/0.1/alpha.water').read_bytes(),before)
            (root/'case/0.1/meshPhi').unlink();result=tool.read_snapshot(root,'0.1',mesh,clock)
            self.assertEqual(result['missingState'],['meshPhi'])
            self.assertEqual(tool.SOURCE_POLICY['rhoPhi']['read'],'NO_READ')
            self.assertNotIn('rhoPhi',tool.REQUIRED_STATE);self.assertNotIn('Vc0',tool.REQUIRED_STATE)

    def test_full_state_comparison_reads_all_cells_faces_boundary_and_moved_points(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh,_=snapshot_fixture(root);clock=tool.clock_history((root/'logs/01-foamRun.log').read_text())
            left=tool.read_snapshot(root,'0.1',mesh,clock);right=deepcopy(left)
            result=tool.compare_full_state(left,right,{'relative':1e-8,'absolute':1e-10})
            self.assertTrue(result['allCellAndFaceFieldsCompared']);self.assertFalse(result['restartVerified'])
            right['fields']['alphaPhi.water']['boundary']['blade'][2]+=.01
            with self.assertRaisesRegex(tool.AuditIssue,'RESTART_FIELD_DIFFERENCE'):tool.compare_full_state(left,right,{'relative':1e-8,'absolute':1e-10})
            right=deepcopy(left);right['fields']={n:v for n,v in right['fields'].items() if n in {'p','U','k','omega'}}
            with self.assertRaises(tool.AuditIssue):tool.compare_full_state(left,right,{'relative':1e-8,'absolute':1e-10})
            right=deepcopy(left);right['topologySha256']='b'*64
            with self.assertRaises(tool.AuditIssue):tool.compare_full_state(left,right,{'relative':1e-8,'absolute':1e-10})

    def test_real_motion_record_mapping_and_missing_ale_preserve_coordinate_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);_,module=snapshot_fixture(root,complete=False)
            with patch.dict(sys.modules,{'teaching_pump_motion_evidence':module}):result=tool.audit_transient(root)
            self.assertTrue(result['coordinateMotionVerified'],result['diagnostics']);self.assertTrue(result['scientificEndpointFieldsVerified'])
            self.assertFalse(result['checkpointStateComplete']);self.assertFalse(result['eulerGclEvidenceVerified'])
            self.assertIn('meshPhi',str(result['diagnostics']));self.assertFalse(result['coordinateEvidence']['fullProofEmbedded'])
            self.assertLess(len(tool.io.encoded(result)),tool.MAX_RECORD)
            for name in tool.FALSE_FLAGS:self.assertFalse(result[name],name)

    def test_unknown_checkpoint_code_and_mutated_current_records_fail_own_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);_,module=snapshot_fixture(root)
            write(root,'case/0.1/unbound.json',b'{}')
            with patch.dict(sys.modules,{'teaching_pump_motion_evidence':module}):result=tool.audit_transient(root)
            self.assertTrue(result['coordinateMotionVerified']);self.assertTrue(result['scientificEndpointFieldsVerified'])
            self.assertFalse(result['checkpointStateComplete']);self.assertIn('KNOWN_FULL_STATE_ROLES',str(result['diagnostics']))
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);_,module=snapshot_fixture(root)
            (root/'case/constant/polyMesh/faces').write_bytes(b'changed')
            with patch.dict(sys.modules,{'teaching_pump_motion_evidence':module}):result=tool.audit_transient(root)
            self.assertFalse(result['coordinateMotionVerified']);self.assertIn('SOURCE_BYTES',str(result['diagnostics']))

    def test_sparse_write_interval_cannot_be_used_as_last_native_delta_t(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);_,module=snapshot_fixture(root,times=('0.1','0.2'))
            # Add a native .15 step, so .1 is not .2's preceding timestep.
            log=write(root,'logs/01-foamRun.log','deltaT = .1\nTime = .1s\ndeltaT = .05\nTime = .15s\ndeltaT = .05\nTime = .2s\nEnd\n')
            proof=module.inspect()
            proof['nativeEvidenceRecords']=[log if r['path']==log['path'] else r for r in proof['nativeEvidenceRecords']]
            path=root/'case/0.2/uniform/time'
            path.write_bytes(path.read_bytes().replace(b'index 2;',b'index 3;').replace(b'deltaT .1;',b'deltaT .05;').replace(b'deltaT0 .1;',b'deltaT0 .05;'))
            # The .1 and .2 writes are current, with genuine intervening .15
            # native clock data. Their gap cannot stand in for the last .05dt.
            with patch.dict(sys.modules,{'teaching_pump_motion_evidence':module}):result=tool.audit_transient(root,{'schemaVersion':tool.INPUT_SCHEMA,'aleStepPairs':[{'previousTimeDirectory':'0.1','timeDirectory':'0.2'}]})
            self.assertFalse(result['eulerGclEvidenceVerified']);self.assertFalse(result['actualCfdRunVerified'])
            self.assertTrue(result['coordinateMotionVerified']);self.assertTrue(result['scientificEndpointFieldsVerified'])
            self.assertIn('CONSECUTIVE_NATIVE_STEPS',str(result['diagnostics']))


if __name__=='__main__':unittest.main()
