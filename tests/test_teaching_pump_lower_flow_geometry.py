"""Synthetic native cap geometry/math only; no Gmsh/OpenFOAM/CFD executes."""
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import teaching_pump_lower_flow_geometry as profile


def fixture(root):
    """Two-cell label-shape fixture; only the cap is geometric, not a pump mesh."""
    report_dir=root/'source-geometry';report_dir.mkdir()
    parameters=deepcopy(profile.recipe.EXPECTED_GEOMETRY_PARAMETERS);plan=profile.author.teaching_plan(parameters,'coarse')
    packet={'schemaVersion':profile.author.SCHEMA,'provenance':'CLASSROOM_AUTHORED_GEOMETRY','units':'SI',
        'parameters':parameters,'parametersSha256':plan['parametersSha256'],'geometryIdentitySha256':plan['geometryIdentitySha256'],
        'inputManifestSha256':'b'*64,'meshLevel':'coarse','syntheticFixtureNotNativeAcceptance':True}
    (report_dir/'teaching-geometry.json').write_bytes(profile.source_io.encoded(packet))
    mesh=root/'component-mesh-snapshots/volute/constant/polyMesh';mesh.mkdir(parents=True)
    x0,x1=profile.INTERFACE_RADIUS+profile.WIDTH_START,profile.INTERFACE_RADIUS+profile.WIDTH_END
    points=[(x0,.30,0),(x1,.30,0),(x1,.30,profile.HEIGHT),(x0,.30,profile.HEIGHT)]
    bodies={'points':'4 ('+' '.join('('+' '.join(format(v,'.17g') for v in p)+')' for p in points)+')',
        'faces':'6 (3(0 1 2) 3(1 2 3) 3(0 2 1) 3(0 3 2) 3(0 1 3) 3(0 1 2))',
        'owner':'6 (0 0 1 1 1 0)','neighbour':'1 (1)',
        'boundary':'4 (interface-volute-inlet {type patch; nFaces 1; startFace 1;} mass-flow-inlet-11 {type patch; nFaces 2; startFace 2;} volute-inlet-wall {type wall; nFaces 1; startFace 4;} volute-wall {type wall; nFaces 1; startFace 5;})',
        'cellZones':'1 (stationary-fluid {type cellZone; cellLabels List<label> 2(0 1);})'}
    kinds={'points':'vectorField','faces':'faceList','owner':'labelList','neighbour':'labelList','boundary':'polyBoundaryMesh','cellZones':'cellZoneList'}
    for name,body in bodies.items():(mesh/name).write_text(profile.source_io.base.foam_header(name,kinds[name],'constant/polyMesh')+body)
    return mesh,report_dir/'teaching-geometry.json'


class UniformOutletGeometryTests(unittest.TestCase):
    def test_python_equations_and_declared_boundary_do_not_claim_measured_flow(self):
        p=profile.PROFILE
        self.assertEqual(p['id'],'teaching-45kg-s-extended-030-outlet-cap-geometry-v1')
        self.assertAlmostEqual(p['volumetricFlowM3S']*p['densityKgM3'],45.,places=12)
        self.assertEqual(p['rectangleAreaM2'],.0047993)
        self.assertAlmostEqual(p['uniformNormalVelocityMS']*p['rectangleAreaM2'],45/998.2,places=15)
        self.assertEqual(profile.uniform_outlet_body(),b'type fixedValue; value uniform (0 9.3932752824189443 0);')
        self.assertFalse(p['profileMeasured']);self.assertFalse(p['convergencePredicted']);self.assertFalse(p['nativeFieldClippingPerformed'])
        for s in p['engineeringStages'].values():
            self.assertEqual(set(s),{'input','equation','substitution','result','calculationAuthority'})
            self.assertEqual(s['calculationAuthority'],'Python')

    def test_all_actual_cap_facets_normal_area_perimeter_source_shas_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh,report=fixture(root);before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            proof=profile.verify_outlet(root)
            self.assertEqual(proof['actualOutletTriangleCount'],2);self.assertEqual(proof['actualOutletFaceRange'],[2,4])
            self.assertAlmostEqual(proof['actualOutletAreaM2'],.0047993,places=16)
            self.assertEqual(proof['maximumPlaneErrorM'],0);self.assertEqual(proof['maximumOutwardNormalError'],0)
            self.assertEqual(len(proof['nativeMeshFileRecords']),6);self.assertEqual(proof['geometryReportFile']['sha256'],sha256(report.read_bytes()).hexdigest())
            self.assertTrue(proof['allPhysicalOutletFacesChecked']);self.assertTrue(proof['allOutletOwnersStationaryVerified'])
            self.assertFalse(proof['flowExecuted']);self.assertFalse(proof['convergenceVerified'])
            self.assertEqual(proof['engineeringStages']['measuredArea']['result']['unit'],'m2')
            self.assertEqual(before,{str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_every_face_not_just_one_normal_is_required_and_duplicates_rejected(self):
        changes=((b'3(0 3 2)',b'3(0 2 3)'),(b'3(0 3 2)',b'3(0 2 1)'),(b'3(0 3 2)',b'3(0 3 0)'))
        for old,new in changes:
            with self.subTest(new=new),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);mesh,_=fixture(root);p=mesh/'faces';p.write_bytes(p.read_bytes().replace(old,new))
                with self.assertRaises(ValueError):profile.verify_outlet(root)

    def test_plane_area_parameter_digest_and_cad_identity_mutations_fail(self):
        for mutation in ('plane','area','parameters','parameterSha','cadIdentity'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);mesh,report=fixture(root)
                if mutation in ('plane','area'):
                    p=mesh/'points';raw=p.read_bytes()
                    old=format(.30 if mutation=='plane' else profile.HEIGHT,'.17g').encode()
                    new=format(.300001 if mutation=='plane' else profile.HEIGHT*1.1,'.17g').encode()
                    p.write_bytes(raw.replace(old,new,1))
                else:
                    packet=json.loads(report.read_bytes())
                    if mutation=='parameters':
                        packet['parameters']['voluteWidthEndM']=.121
                        packet['parametersSha256']=sha256(profile.author.encoded(packet['parameters'])).hexdigest()
                    elif mutation=='parameterSha':packet['parametersSha256']='0'*64
                    else:packet['geometryIdentitySha256']='0'*64
                    report.write_bytes(profile.source_io.encoded(packet))
                with self.assertRaises(ValueError):profile.verify_outlet(root)

    def test_physical_patch_range_role_owner_and_stationary_zone_are_not_guessed(self):
        changes=(('boundary',b'mass-flow-inlet-11',b'unknown-outlet'),('boundary',b'nFaces 2;',b'nFaces 1;'),
            ('boundary',b'mass-flow-inlet-11 {type patch;',b'mass-flow-inlet-11 {type wall;'),
            ('owner',b'6 (0 0 1 1 1 0)',b'6 (0 0 99 1 1 0)'),('cellZones',b'stationary-fluid',b'rotating-fluid'),
            ('points',b'class vectorField;',b'class labelList;'))
        for name,old,new in changes:
            with self.subTest(name=name,new=new),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);mesh,_=fixture(root);p=mesh/name;p.write_bytes(p.read_bytes().replace(old,new))
                with self.assertRaises(ValueError):profile.verify_outlet(root)

    def test_copied_source_prefix_replay_keeps_role_identity_and_every_current_byte(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';source.mkdir();fixture(source);proof=profile.verify_outlet(source)
            copied=root/'source-teaching';shutil.copytree(source,copied)
            current=profile.verify_outlet(root,geometry_report_relative='source-teaching/source-geometry/teaching-geometry.json',
                mesh_relative='source-teaching/component-mesh-snapshots/volute/constant/polyMesh')
            self.assertEqual(current,profile.prefix_evidence(proof,'source-teaching'))
            with self.assertRaises(ValueError):profile.prefix_evidence(proof,'../unsafe')
            with self.assertRaises(ValueError):profile.prefix_evidence(proof,'source-teaching/')

    def test_source_race_is_rejected_before_any_geometry_proof_is_published(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh,_=fixture(root);original=profile.source_io.record;calls=0
            def changing(*args,**kwargs):
                nonlocal calls
                result=original(*args,**kwargs);calls+=1
                if calls==13:(mesh/'owner').write_bytes((mesh/'owner').read_bytes()+b' ')
                return result
            with patch.object(profile.source_io,'record',side_effect=changing),self.assertRaises(ValueError):profile.verify_outlet(root)


if __name__=='__main__':unittest.main()
