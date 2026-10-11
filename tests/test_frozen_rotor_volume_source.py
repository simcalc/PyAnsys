"""Synthetic transfer/topology fixtures only; no native CFD or research claims."""
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile
from test_reuse_volume_workflow_preflight import fixture

spec=importlib.util.spec_from_file_location("volume_source_tests",Path(__file__).resolve().parents[1]/"tools/frozen_rotor_volume_source.py")
tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)

POINTS={1:(0.,0.,0.),2:(1.,0.,0.),3:(0.,1.,0.),4:(0.,0.,1.)}
INWARD=[(1,2,3),(1,4,2),(2,4,3),(3,4,1)]


def msh(*,changed=False,reverse=False):
    data=b'$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$PhysicalNames\n2\n2 11 "inlet"\n3 1 "rotating-fluid"\n$EndPhysicalNames\n$Nodes\n4\n'
    points={**POINTS,1:(.01,0.,0.)} if changed else POINTS
    data+=b''.join((str(node)+' '+' '.join(format(v,'.17g') for v in xyz)+'\n').encode('ascii') for node,xyz in points.items())
    data+=b'$EndNodes\n$Elements\n5\n'
    for n,face in enumerate(INWARD,1):
        if reverse and n==1:face=tuple(reversed(face))
        data+=(str(n)+' 2 2 11 11 '+' '.join(map(str,face))+'\n').encode('ascii')
    return data+b'5 4 2 1 1 1 2 3 4\n$EndElements\n'


class SourceTransferTests(unittest.TestCase):
    def make_archive(self,root,*,extra=None,report=None):
        archive=root/'raw-artifact.zip';admission=root/'source-admission.json'
        report=report if report is not None else {'meshes':[
            {'generatedFile':{'name':name,'bytes':len(msh()),'sha256':sha256(msh()).hexdigest()}}
            for name in ('impeller.msh','volute.msh')]}
        rows={'frozen-rotor-remesh/remesh-report.json':tool.encoded(report),
              'frozen-rotor-remesh/impeller.msh':msh(),'frozen-rotor-remesh/volute.msh':msh(),
              'frozen-openfoam-case/openfoam-execution.json':b'{"actualCfdRunVerified":false}',
              'frozen-openfoam-case/logs/06-checkMesh.log':b'Not CFD evidence\n'}
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
            for name,raw in rows.items():bundle.writestr(name,raw)
            if extra:
                for name,raw in extra:bundle.writestr(name,raw)
        raw=archive.read_bytes();data=fixture('failure',archive_bytes=len(raw),digest=sha256(raw).hexdigest())
        admitted=tool.preflight.validate_source(*data);admission.write_bytes(tool.encoded(admitted))
        return archive,admission,admitted,rows

    def replay_fixture(self):
        return {'remeshReportSha256':'a'*64,'components':[{'component':name,'actualCellCount':1,'actualNodeCount':4}
            for name in ('impeller','volute')],'originalSourceRecords':[]}

    def test_exact_three_file_transfer_retains_failed_run_only_as_volume_provenance(self):
        with TemporaryDirectory() as temporary,patch.dict(os.environ,{'RUNNER_TEMP':temporary}):
            root=Path(temporary);archive,admission,admitted,rows=self.make_archive(root);output=root/'fresh-volume'
            with patch.object(tool,'enforce_limits'),patch.object(tool,'verify_report',return_value={'synthetic':True}),\
                 patch.object(tool,'replay_geometry',return_value=self.replay_fixture()):
                result=tool.extract(archive,admission,output)
            self.assertEqual({p.name for p in output.iterdir()},tool.SELECTED|{tool.REPORT})
            self.assertEqual(result['sourceAdmission']['sourceRunConclusion'],'failure')
            self.assertEqual(result['sourceAdmissionFileSha256'],sha256(admission.read_bytes()).hexdigest())
            self.assertTrue(result['volumeAcceptanceReplayed'])
            for flag in tool.FALSE_FLAGS:self.assertIs(result[flag],False)
            for name in tool.SELECTED:self.assertEqual((output/name).read_bytes(),rows['frozen-rotor-remesh/'+name])
            self.assertFalse((output/'openfoam-execution.json').exists())
            self.assertLessEqual((output/tool.REPORT).stat().st_size,128*1024)
            self.assertEqual(result['limits']['maximumSeconds'],300)

    def test_raw_size_or_github_digest_mismatch_rejects_before_mkdir_or_enforcement(self):
        for mutate in ('zip','sha','size'):
            with self.subTest(mutate=mutate),TemporaryDirectory() as temporary,patch.dict(os.environ,{'RUNNER_TEMP':temporary}):
                root=Path(temporary);archive,admission,admitted,_=self.make_archive(root);output=root/'new'
                if mutate=='zip':archive.write_bytes(archive.read_bytes()+b'changed')
                else:
                    admitted['artifactBytes']+=1 if mutate=='size' else 0
                    if mutate=='sha':admitted['artifactSha256']='b'*64;admitted['githubReportedArchiveDigest']='sha256:'+'b'*64
                    admission.write_bytes(tool.encoded(admitted))
                with patch.object(tool,'enforce_limits') as enforced,self.assertRaises(ValueError):tool.extract(archive,admission,output)
                self.assertFalse(output.exists());enforced.assert_not_called()

    def test_full_central_directory_rejects_unselected_code_alias_links_or_invalid_content_before_mkdir(self):
        link=zipfile.ZipInfo('frozen-openfoam-case/native-case-evidence.zip');link.create_system=3;link.external_attr=(stat.S_IFLNK|0o777)<<16
        bad=[('frozen-openfoam-case/evil.py',b'print("never execute")'),
             ('frozen-openfoam-case/../secret.json',b'{}'),
             ('Frozen-openfoam-case/openfoam-execution.json',b'{}'),
             ('frozen-openfoam-case/unknown.json',b'{}'),
             ('frozen-openfoam-case/runtime-identity.json',b'{"duplicate":1,"duplicate":2}'),
             ('frozen-openfoam-case/native-case-evidence.zip',b'not a ZIP'),(link,b'/tmp/target')]
        for extra in bad:
            with self.subTest(extra=str(extra[0])),TemporaryDirectory() as temporary,patch.dict(os.environ,{'RUNNER_TEMP':temporary}):
                root=Path(temporary);archive,admission,_,_=self.make_archive(root,extra=[extra]);output=root/'new'
                with patch.object(tool,'enforce_limits'),patch.object(tool,'verify_report') as verify,self.assertRaises(ValueError):
                    tool.extract(archive,admission,output)
                verify.assert_not_called();self.assertFalse(output.exists())

    def test_mesh_checksum_mismatch_rejects_before_any_persistence(self):
        with TemporaryDirectory() as temporary,patch.dict(os.environ,{'RUNNER_TEMP':temporary}):
            root=Path(temporary);report={'meshes':[{'generatedFile':{'name':name,'bytes':len(msh()),'sha256':'b'*64}}
                for name in ('impeller.msh','volute.msh')]}
            archive,admission,_,_=self.make_archive(root,report=report);output=root/'new'
            with patch.object(tool,'enforce_limits'),patch.object(tool,'verify_report',return_value={}),self.assertRaises(ValueError):
                tool.extract(archive,admission,output)
            self.assertFalse(output.exists())

    def test_postcopy_admission_mutation_rejected_without_published_transfer(self):
        with TemporaryDirectory() as temporary,patch.dict(os.environ,{'RUNNER_TEMP':temporary}):
            root=Path(temporary);archive,admission,_,_=self.make_archive(root);output=root/'new'
            def replay(*args):admission.write_bytes(b'{"changed":true}');return self.replay_fixture()
            with patch.object(tool,'enforce_limits'),patch.object(tool,'verify_report',return_value={}),\
                 patch.object(tool,'replay_geometry',side_effect=replay),self.assertRaises(ValueError):tool.extract(archive,admission,output)
            self.assertFalse(output.exists());self.assertFalse((root/'new-native-inputs').exists())

    def test_failed_geometry_or_completed_deadline_removes_only_own_fresh_output(self):
        for failed in ('geometry','deadline'):
            with self.subTest(failed=failed),TemporaryDirectory() as temporary,patch.dict(os.environ,{'RUNNER_TEMP':temporary}):
                root=Path(temporary);archive,admission,_,_=self.make_archive(root);output=root/'new'
                sentinel=root/'unrelated';sentinel.write_bytes(b'preserved')
                with patch.object(tool,'enforce_limits'),patch.object(tool,'verify_report',return_value={}),\
                     patch.object(tool,'replay_geometry',side_effect=ValueError('Exact source replay failed') if failed=='geometry' else None,
                                  return_value=self.replay_fixture()),patch.object(tool.time,'monotonic',side_effect=[0,301]),\
                     self.assertRaises(ValueError):tool.extract(archive,admission,output)
                self.assertFalse(output.exists());self.assertEqual(sentinel.read_bytes(),b'preserved')

    def test_actual_complete_coordinate_and_winding_replay_rejects_changed_geometry(self):
        zones=[{'zoneId':11,'triangles':INWARD}]
        with TemporaryDirectory() as temporary:
            path=Path(temporary)/'impeller.msh'
            path.write_bytes(msh())
            positions=tool.coordinate_replay(path,zones,POINTS)
            self.assertEqual(positions['nativeBoundaryNodeCount'],4)
            self.assertTrue(positions['everyNativeBoundaryCoordinateChecked'])
            self.assertTrue(tool.remesh.verify_published_boundary(path,zones)['nativeWindingPreserved'])
            path.write_bytes(msh(changed=True))
            with self.assertRaises(ValueError):tool.coordinate_replay(path,zones,POINTS)
            path.write_bytes(msh(reverse=True))
            with self.assertRaises(ValueError):tool.remesh.verify_published_boundary(path,zones)

    def test_real_tetra_incidence_reader_is_required_for_replay_not_positive_counts_alone(self):
        try:import numpy
        except ImportError:self.skipTest('NumPy actual incidence reader unavailable locally')
        model={'coordinates':POINTS,'zones':[{'zoneId':11,'name':'inlet','faces':[(n,face) for n,face in enumerate(INWARD,1)]}]}
        with TemporaryDirectory() as temporary,patch.dict(os.environ,{'RUNNER_TEMP':temporary}):
            root=Path(temporary);output=root/'volume';cache=root/'inputs';output.mkdir();cache.mkdir()
            for name in ('impeller.msh','volute.msh'):(output/name).write_bytes(msh())
            def download(folder,name):path=folder/name;path.write_bytes(b'explicit synthetic H5 fixture');return path
            def verify(path,name):return {'name':name,'bytes':1,'sha256':'a'*64,'sourceUrl':tool.inventory.mesh_url(name)}
            meshes={name:{'nodeCount':4,'cellCount':1} for name in ('impeller','volute')}
            with patch.object(tool.case,'validate_inputs',return_value=(meshes,'b'*64)),\
                 patch.object(tool.inventory,'download_mesh',side_effect=download),patch.object(tool.inventory,'verify_file',side_effect=verify),\
                 patch.object(tool.boundary,'read_native_mesh',return_value=model):
                result=tool.replay_geometry(output,cache,{})
            self.assertEqual(len(result['components']),2)
            for component in result['components']:
                self.assertTrue(component['exactTetraFaceIncidence']['accepted'])
                self.assertEqual(component['actualCellCount'],1)
                self.assertTrue(component['nativeCoordinateReplay']['everyNativeBoundaryCoordinateChecked'])

    def test_existing_or_outside_output_and_untrusted_runner_reject_without_effects(self):
        with TemporaryDirectory() as temporary,TemporaryDirectory() as outside,patch.dict(os.environ,{'RUNNER_TEMP':temporary}):
            root=Path(temporary);archive,admission,_,_=self.make_archive(root);existing=root/'existing';existing.mkdir()
            for output in (root,existing,Path(outside)/'new'):
                with self.subTest(output=output),patch.object(tool,'enforce_limits') as enforce,self.assertRaises(ValueError):
                    tool.extract(archive,admission,output)
                enforce.assert_not_called()
            with patch.dict(os.environ,{},clear=True),self.assertRaises(ValueError):tool.enforce_limits()


if __name__=='__main__':unittest.main()
