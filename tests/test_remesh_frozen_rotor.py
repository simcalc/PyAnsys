"""Meshing contract tests; synthetic tetrahedra are not pump/CFD evidence."""
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]/"tools"/"remesh_frozen_rotor.py"
spec = importlib.util.spec_from_file_location("classroom_remesh", TOOL)
remesh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remesh)
try:
    import numpy as np
except ImportError:
    np = None

POINTS = {1: (0., 0., 0.), 2: (1., 0., 0.), 3: (0., 1., 0.), 4: (0., 0., 1.)}
FACES = [(1, 3, 2), (1, 2, 4), (2, 3, 4), (3, 1, 4)]


class RemeshContracts(unittest.TestCase):
    def model(self):
        return {"coordinates": POINTS, "zones": [{"zoneId": 11, "name": "wall", "faces": [(1,FACES[0]),(2,FACES[1])]},
            {"zoneId": 12, "name": "interface-original", "faces": [(3,FACES[2]),(4,FACES[3])]}]}

    def test_native_float64_ids_zone_names_and_fan_mapping_are_preserved(self):
        zones, owners, audit = remesh.source_triangles(self.model())
        self.assertEqual([zone["zoneId"] for zone in zones],[11,12])
        self.assertEqual([zone["zoneName"] for zone in zones],["wall","interface-original"])
        self.assertEqual(zones[0]["triangles"],FACES[:2])
        self.assertEqual(zones[1]["nativeFaceIds"],[3,4])
        self.assertTrue(audit["consistentNativeWinding"])
        self.assertEqual(remesh.classified_owners({25:[FACES[0]],26:[FACES[1]],30:FACES[2:]},owners),{11:[25,26],12:[30]})

    def test_mixed_zone_classification_missing_changed_and_duplicate_triangles_reject(self):
        _, owners, _ = remesh.source_triangles(self.model())
        for groups in ({25:[FACES[0],FACES[2]],26:[FACES[1],FACES[3]]},
                       {25:FACES[:2],26:[FACES[2]]},
                       {25:FACES[:2],26:[FACES[2],(3,1,99)]},
                       {25:FACES[:2],26:FACES[2:],27:[FACES[0]]}):
            with self.subTest(groups=groups), self.assertRaises(remesh.RemeshIssue):
                remesh.classified_owners(groups,owners)

    def test_open_shell_or_inconsistent_orientation_cannot_generate_volume(self):
        for faces in (FACES[:-1],[tuple(reversed(FACES[0]))]+FACES[1:]):
            model=self.model()
            model["zones"]=[{"zoneId":11,"name":"wall","faces":list(enumerate(faces,1))}]
            with self.assertRaisesRegex(remesh.RemeshIssue,"closed native"):
                remesh.source_triangles(model)

    def test_explicit_source_seam_partition_splits_mixed_entities_with_full_native_coverage(self):
        _,owners,_=remesh.source_triangles(self.model())
        originals={tuple(sorted(row)):row for row in FACES}
        # Geometry classification spans a smooth native BC seam and may reorder.
        groups={25:[tuple(reversed(FACES[0])),FACES[2]],26:[FACES[1],FACES[3]]}
        partitions=remesh.partition_source_seams(groups,owners,originals)
        self.assertEqual([(row["originalClassifiedEntityId"],row["sourceZoneId"]) for row in partitions],[(25,11),(25,12),(26,11),(26,12)])
        self.assertEqual(partitions[0]["triangles"],[FACES[0]])
        checked=remesh.classified_owners({row["entityId"]:row["triangles"] for row in partitions},owners)
        self.assertEqual(checked,{11:[1,3],12:[2,4]})
        self.assertEqual({tuple(sorted(triangle)) for row in partitions for triangle in row["triangles"]},set(owners))
        for invalid in ({25:FACES[:-1]}, {25:FACES,26:[FACES[0]]}, {25:FACES[:-1]+[(3,1,99)]}):
            with self.subTest(invalid=invalid),self.assertRaises(remesh.RemeshIssue):
                remesh.partition_source_seams(invalid,owners,originals)

    def test_size_uses_explicit_trial_bbox_divisor_without_cell_prediction(self):
        box=remesh.bounds(list(POINTS.values()))
        self.assertEqual(remesh.sizing(box),1/32)
        for points in ([],[(0,0,math.inf)],[(0,0,0),(1,1,0)]):
            with self.subTest(points=points), self.assertRaises(remesh.RemeshIssue):
                remesh.sizing(remesh.bounds(points))

    def test_positive_tetra_volume_and_all_cell_checks_are_required(self):
        result=remesh.tetra_metrics([(1,2,3,4)],POINTS)
        self.assertAlmostEqual(result["totalTetraVolumeAssumedM3"],1/6)
        self.assertEqual(result["minimumSignedDeterminant"],1)
        self.assertTrue(result["everyTetrahedronChecked"])
        for cells in ([],[(1,3,2,4)],[(1,1,3,4)],[(1,2,3,99)],[(1,2,3)]):
            with self.subTest(cells=cells), self.assertRaises(remesh.RemeshIssue):
                remesh.tetra_metrics(cells,POINTS)

    def test_patch_area_vector_and_bounds_keep_unverified_normal_convention(self):
        metrics=remesh.patch_metrics([FACES[0]],POINTS)
        self.assertEqual(metrics["areaAssumedM2"],0.5)
        self.assertEqual(metrics["areaVectorAssumedM2"],[0,0,-.5])
        self.assertFalse(metrics["outwardNormalConventionVerified"])

    def test_quality_empty_nonfinite_negative_and_count_mismatch_reject(self):
        class Mesh:
            values=[.5]
            def getElementQualities(self,tags,quantity): return self.values
        class Model: mesh=Mesh()
        class Gmsh: model=Model()
        self.assertEqual(remesh._quality(Gmsh(),[1])["minSICN"]["minimum"],.5)
        for values in ([],[float("nan")],[-.1],[0],[.2,.3]):
            Gmsh.model.mesh.values=values
            with self.subTest(values=values), self.assertRaises(remesh.RemeshIssue):
                remesh._quality(Gmsh(),[1])

    def test_failed_runtime_persists_report_only_without_solver_or_mesh_claims(self):
        with TemporaryDirectory() as root, patch.dict(os.environ,{"RUNNER_TEMP":root},clear=False):
            target=Path(root)/"new"/"remesh"
            with patch.object(remesh,"enforce_runtime_limits",side_effect=remesh.RemeshIssue("Synthetic preflight rejection")):
                summary=remesh.run(target)
            self.assertFalse(summary["volumeMeshGenerated"])
            self.assertEqual(sorted(path.name for path in target.iterdir()),["remesh-progress.json","remesh-report.json"])
            report=json.loads((target/"remesh-report.json").read_text())
            self.assertEqual(report["diagnostic"]["stage"],"runtime-preflight")
            self.assertEqual(report["status"],"PREPARATION_REQUIRED")
            self.assertFalse(report["classroomScaleAssumption"]["verifiedFromSource"])
            self.assertEqual(report["coordinateUnits"],"UNVERIFIED")
            self.assertEqual(report["files"],[])
            self.assertEqual(report["progressJournal"][0]["stage"],"runtime-preflight")
            self.assertFalse(report["originalMeshesImmutable"])
            for flag in ("actualCfdRunVerified","simulationExecuted","nativeCaseGenerated","mainApplicationWrites","openFoamConversionVerified"):
                self.assertIs(report[flag],False)

    def test_fixed_boundary_requires_full_native_coordinate_identity_and_winding(self):
        _,owners,_=remesh.source_triangles(self.model())
        originals={tuple(sorted(row)):row for row in FACES}
        groups={11:FACES[:2],12:FACES[2:]}
        mapped,audit=remesh.preserved_boundary(groups,owners,originals,POINTS,{**POINTS,99:(.1,.1,.1)})
        self.assertEqual(mapped,{11:[11],12:[12]})
        self.assertTrue(audit["consistentNativeWinding"])
        changed={**POINTS,1:(.000001,0,0)}
        with self.assertRaisesRegex(remesh.RemeshIssue,"float64"):
            remesh.preserved_boundary(groups,owners,originals,POINTS,changed)
        with self.assertRaisesRegex(remesh.RemeshIssue,"winding"):
            remesh.preserved_boundary({11:[tuple(reversed(FACES[0])),FACES[1]],12:FACES[2:]},owners,originals,POINTS,POINTS)

    def test_failed_fill_removes_partial_mesh_and_verifies_both_original_hashes_after_attempt(self):
        native=SimpleNamespace(__version__=remesh.GMSH_VERSION, initialize=lambda *a,**k:None,
            option=SimpleNamespace(setNumber=lambda *a:None), logger=SimpleNamespace(start=lambda:None,get=lambda:["Synthetic meshing diagnostic"]),
            model=SimpleNamespace(mesh=SimpleNamespace(getLastEntityError=lambda:[])),isInitialized=lambda:True,finalize=lambda:None)
        def transferred(directory,name):
            target=directory/name
            target.write_bytes(b"Synthetic read-only input placeholder")
            return target
        def verified(path,name): return {"name":name,"bytes":1,"sha256":"a"*64,"sourceUrl":remesh.inventory.mesh_url(name)}
        def failed_fill(model,name,destination,gmsh,np,progress):
            (destination/"impeller.msh").write_bytes(b"Invalid partial mesh must be removed")
            progress["stage"]="fixed-boundary-tetrahedral-fill"
            raise remesh.RemeshIssue("Synthetic fill failure")
        with TemporaryDirectory() as root, patch.dict(os.environ,{"RUNNER_TEMP":root},clear=False),patch.dict(sys.modules,{"gmsh":native}):
            target=Path(root)/"new-remesh"
            with patch.object(remesh,"enforce_runtime_limits"),patch.object(remesh.inventory,"download_mesh",side_effect=transferred),\
                 patch.object(remesh.inventory,"verify_file",side_effect=verified) as checksum,\
                 patch.object(remesh.boundary,"read_native_mesh",return_value={}),patch.object(remesh,"remesh_component",side_effect=failed_fill):
                summary=remesh.run(target)
            report=json.loads((target/"remesh-report.json").read_text())
            self.assertFalse(summary["volumeMeshGenerated"])
            self.assertFalse((target/"impeller.msh").exists())
            self.assertTrue(report["originalMeshesImmutable"])
            self.assertEqual(checksum.call_count,4)
            self.assertEqual(len(report["postAttemptSourceVerification"]),2)
            self.assertEqual(report["diagnostic"]["loggerTail"],["Synthetic meshing diagnostic"])

    def test_hxt_is_fixed_and_child_environment_keeps_runner_identity_without_secrets(self):
        self.assertEqual(remesh.GMSH_ALGORITHM_3D,10)
        with patch.dict(os.environ,{"GITHUB_REPOSITORY":"simcalc/PyAnsys","RUNNER_OS":"Linux","RUNNER_ENVIRONMENT":"github-hosted",
                "GITHUB_ACTIONS":"true","RUNNER_TEMP":"/tmp/runner","PATH":"/bin","LD_LIBRARY_PATH":"/safe/lib",
                "GH_TOKEN":"must-not-inherit","CFD_LICENSE_TOKEN":"must-not-inherit","SECRET_PRIVATE":"must-not-inherit"},clear=True):
            environment=remesh.child_environment()
        self.assertEqual(environment["GITHUB_REPOSITORY"],"simcalc/PyAnsys")
        self.assertEqual(environment["LD_LIBRARY_PATH"],"/safe/lib")
        self.assertEqual(environment["OMP_NUM_THREADS"],"1")
        self.assertNotIn("GH_TOKEN",environment)
        self.assertNotIn("CFD_LICENSE_TOKEN",environment)
        self.assertNotIn("SECRET_PRIVATE",environment)

    def test_interrupted_native_worker_report_removes_partial_mesh_and_never_claims_cfd(self):
        with TemporaryDirectory() as root,patch.dict(os.environ,{"RUNNER_TEMP":root},clear=False):
            target=Path(root)/"trial"
            target.mkdir()
            remesh.persist_json(target,"remesh-progress.json",{"stage":"fixed-boundary-tetrahedral-fill","component":"impeller", "verifiedSources":[],"journal":[]})
            (target/"impeller.msh").write_bytes(b"Interrupted native output")
            report=remesh.interrupted_report(target,"CFD_CLASSROOM_MESH_WORKER_TIMEOUT",["Native geometry recovery"],300,-15)
            self.assertEqual(report["diagnostic"]["stage"],"fixed-boundary-tetrahedral-fill")
            self.assertEqual(report["diagnostic"]["loggerTail"],["Native geometry recovery"])
            self.assertFalse(report["volumeMeshGenerated"])
            self.assertFalse(report["originalMeshesImmutable"])
            self.assertFalse((target/"impeller.msh").exists())
            self.assertEqual(report["files"],[])

    def test_process_group_stop_targets_only_the_owned_fresh_worker_session(self):
        class Process:
            pid=123456
            def poll(self): return None
            def wait(self,timeout): return -15
        with patch.object(remesh.os,"getpgid",return_value=123456,create=True),patch.object(remesh.os,"killpg",create=True) as signalled:
            remesh.stop_owned_worker(Process())
            signalled.assert_called_once_with(123456,remesh.signal.SIGTERM)
        with patch.object(remesh.os,"getpgid",return_value=77,create=True),patch.object(remesh.os,"killpg",create=True) as signalled:
            with self.assertRaisesRegex(remesh.RemeshIssue,"owns its isolated"):
                remesh.stop_owned_worker(Process())
            signalled.assert_not_called()

    def test_supervisor_keeps_only_bounded_native_tail_and_recovers_worker_report(self):
        class Stream:
            def fileno(self): return 123
            def close(self): pass
        class Process:
            pid=123456
            stdout=Stream()
            def poll(self): return 2
            def wait(self,timeout): return 2
        class Selector:
            active=True
            def register(self,file,event): self.file=file
            def get_map(self): return {1:True} if self.active else {}
            def select(self,timeout): return [(SimpleNamespace(fileobj=self.file),None)]
            def unregister(self,file): self.active=False
            def close(self): pass
        data="\n".join("Info : native step"+str(index)+" "+"x"*400 for index in range(30)).encode()+b"\n"
        chunks=[data[index:index+4096] for index in range(0,len(data),4096)]+[b""]
        with TemporaryDirectory() as root,patch.dict(os.environ,{"RUNNER_TEMP":root},clear=False):
            target=Path(root)/"trial"
            def launched(*arguments,**options):
                target.mkdir()
                remesh.persist_json(target,"remesh-report.json",{"schemaVersion":remesh.SCHEMA,"status":"PREPARATION_REQUIRED",
                    "volumeMeshGenerated":False,"actualCfdRunVerified":False,"simulationExecuted":False,"mainApplicationWrites":False,"files":[],"meshes":[]})
                return Process()
            with patch.object(remesh.sys,"platform","linux"),patch.object(remesh.subprocess,"Popen",side_effect=launched) as launch,\
                 patch.object(remesh.selectors,"DefaultSelector",return_value=Selector()),patch.object(remesh.os,"read",side_effect=chunks):
                summary=remesh.supervised_run(target)
            log=json.loads((target/"gmsh-native-tail.json").read_text())
            self.assertEqual(log["receivedNativeLineCount"],30)
            self.assertEqual(len(log["lines"]),24)
            self.assertTrue(all(len(row)<=240 for row in log["lines"]))
            self.assertIn("step29",log["lines"][-1])
            self.assertFalse(summary["volumeMeshGenerated"])
            self.assertEqual(launch.call_args.args[0][2],str(TOOL.resolve()))
            self.assertIn("--native-worker",launch.call_args.args[0])
            self.assertTrue(launch.call_args.kwargs["start_new_session"])

    def test_supervisor_timeout_stops_owned_worker_and_persists_diagnostic_with_source_hashes(self):
        class Stream:
            def fileno(self): return 123
            def close(self): pass
        class Process:
            pid=123456
            stdout=Stream()
            result=None
            def poll(self): return self.result
            def wait(self,timeout): return self.result
        class Selector:
            active=True
            def register(self,file,event): self.file=file
            def get_map(self): return {1:True} if self.active else {}
            def select(self,timeout): return [(SimpleNamespace(fileobj=self.file),None)]
            def unregister(self,file): self.active=False
            def close(self): pass
        process=Process()
        verified=[{"name":name,"bytes":remesh.inventory.FILES[name][0],"sha256":remesh.inventory.FILES[name][1],
                   "sourceUrl":remesh.inventory.mesh_url(name)} for name in remesh.inventory.MESH_NAMES]
        with TemporaryDirectory() as root,patch.dict(os.environ,{"RUNNER_TEMP":root},clear=False):
            target=Path(root)/"trial"
            def launched(*arguments,**options):
                target.mkdir()
                remesh.persist_json(target,"remesh-progress.json",{"stage":"fixed-boundary-tetrahedral-fill","component":"impeller",
                    "verifiedSources":verified,"journal":[],"surfaceZoneBindings":[]})
                (target/"impeller.msh").write_bytes(b"Incomplete native output")
                return process
            def stopped(owned):
                self.assertIs(owned,process)
                owned.result=-15
            def verify(path,name): return next(row for row in verified if row["name"]==name)
            with patch.object(remesh.sys,"platform","linux"),patch.object(remesh.subprocess,"Popen",side_effect=launched),\
                 patch.object(remesh.selectors,"DefaultSelector",return_value=Selector()),\
                 patch.object(remesh.os,"read",side_effect=[b"Info : HXT boundary recovery\n",b""]),\
                 patch.object(remesh.time,"monotonic",side_effect=[0]+[301]*20),\
                 patch.object(remesh,"stop_owned_worker",side_effect=stopped) as stop,\
                 patch.object(remesh.inventory,"verify_file",side_effect=verify) as checked:
                summary=remesh.supervised_run(target)
            report=json.loads((target/"remesh-report.json").read_text())
            log=json.loads((target/"gmsh-native-tail.json").read_text())
            stop.assert_called_once_with(process)
            self.assertEqual(checked.call_count,2)
            self.assertEqual(report["reasonCode"],"CFD_CLASSROOM_MESH_WORKER_TIMEOUT")
            self.assertEqual(report["diagnostic"]["stage"],"fixed-boundary-tetrahedral-fill")
            self.assertEqual(report["diagnostic"]["loggerTail"],["Info : HXT boundary recovery"])
            self.assertTrue(report["originalMeshesImmutable"])
            self.assertTrue(log["nativeWorkerTimedOut"])
            self.assertEqual(log["nativeProcessReturnCode"],-15)
            self.assertFalse(summary["volumeMeshGenerated"])
            self.assertEqual(summary["files"],[])
            self.assertFalse((target/"impeller.msh").exists())

    def test_supervisor_rejects_completed_worker_evidence_past_wall_clock_deadline(self):
        class Stream:
            def fileno(self): return 123
            def close(self): pass
        class Process:
            stdout=Stream()
            def poll(self): return 0
            def wait(self,timeout): return 0
        class Selector:
            active=True
            def register(self,file,event): self.file=file
            def get_map(self): return {1:True} if self.active else {}
            def select(self,timeout): return [(SimpleNamespace(fileobj=self.file),None)]
            def unregister(self,file): self.active=False
            def close(self): pass
        with TemporaryDirectory() as root,patch.dict(os.environ,{"RUNNER_TEMP":root},clear=False):
            target=Path(root)/"trial"
            def launched(*arguments,**options):
                target.mkdir()
                remesh.persist_json(target,"remesh-report.json",{"schemaVersion":remesh.SCHEMA,"status":"VOLUME_MESH_GENERATED",
                    "volumeMeshGenerated":True,"actualCfdRunVerified":False,"simulationExecuted":False,"mainApplicationWrites":False,
                    "files":[],"meshes":[]})
                return Process()
            with patch.object(remesh.sys,"platform","linux"),patch.object(remesh.subprocess,"Popen",side_effect=launched),\
                 patch.object(remesh.selectors,"DefaultSelector",return_value=Selector()),patch.object(remesh.os,"read",return_value=b""),\
                 patch.object(remesh.time,"monotonic",side_effect=[0]+[301]*20),patch.object(remesh,"stop_owned_worker") as stop:
                summary=remesh.supervised_run(target)
            report=json.loads((target/"remesh-report.json").read_text())
            self.assertFalse(summary["volumeMeshGenerated"])
            self.assertEqual(report["reasonCode"],"CFD_CLASSROOM_MESH_WORKER_TIMEOUT")
            self.assertEqual(report["diagnostic"]["nativeProcessReturnCode"],0)
            stop.assert_not_called()

    def test_output_rejects_existing_or_outside_before_runtime(self):
        with TemporaryDirectory() as root, TemporaryDirectory() as outside, patch.dict(os.environ,{"RUNNER_TEMP":root},clear=False):
            for target in (Path(root),Path(outside)/"remesh"):
                with self.subTest(target=target), patch.object(remesh,"enforce_runtime_limits") as invoked:
                    with self.assertRaises(remesh.inventory.InspectionIssue): remesh.run(target)
                    invoked.assert_not_called()


@unittest.skipIf(np is None,"NumPy unavailable locally; point-to-native-triangle tests run on GitHub Ubuntu")
class TriangleDistanceContracts(unittest.TestCase):
    def test_exact_nearest_distance_inside_outside_and_degenerate(self):
        triangles=[(POINTS[1],POINTS[2],POINTS[3])]
        self.assertAlmostEqual(remesh.nearest_triangle_distance((.2,.2,1),triangles,np),1)
        self.assertAlmostEqual(remesh.nearest_triangle_distance((2,0,0),triangles,np),1)
        self.assertAlmostEqual(remesh.nearest_triangle_distance((.2,.2,0),triangles,np),0)
        with self.assertRaises(remesh.RemeshIssue):
            remesh.nearest_triangle_distance((0,0,0),[(POINTS[1],POINTS[1],POINTS[2])],np)


if __name__ == "__main__":
    unittest.main()
