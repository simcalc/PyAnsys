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
    def test_exact_volume_duplicates_are_measured_without_distance_tolerance(self):
        result=remesh.duplicate_volume_connectivity([(1,2,3,4),(2,3,1,4),(1,2,3,5)],[11,12,13])
        self.assertEqual(result['duplicateConnectivityCount'],1)
        self.assertEqual(result['uniqueConnectivityCount'],2)
        self.assertEqual(result['samples'][0]['globalNodeIds'],[1,2,3,4])
        with self.assertRaises(remesh.RemeshIssue):remesh.duplicate_volume_connectivity([(1,1,3,4)],[1])

    def test_native_duplicate_removal_retains_all_points_unique_cells_and_positive_orientation(self):
        from unittest.mock import Mock
        mesh=SimpleNamespace(removeDuplicateElements=Mock(),rebuildElementCache=Mock())
        gmsh=SimpleNamespace(model=SimpleNamespace(mesh=mesh))
        cells=[(1,2,3,4),(2,3,1,4)]
        with patch.object(remesh,'_quality',return_value={}),patch.object(remesh,'checkpoint'),\
             patch.object(remesh,'inspect_exported_volume',return_value=(POINTS,[11],[cells[0]])):
            progress={}
            result=remesh.remove_measured_volume_duplicates(gmsh,99,'impeller',progress,POINTS,[11,12],cells)
            self.assertEqual(result,(POINTS,[11],[cells[0]]))
            mesh.removeDuplicateElements.assert_called_once_with([(3,99)])
            mesh.rebuildElementCache.assert_called_once_with(onlyIfNecessary=False)
            self.assertTrue(progress['exactVolumeDuplicateRemoval']['everyUniqueVolumeConnectivityRetained'])
        for after in (({**POINTS,4:(0.,0.,2.)},[11],[cells[0]]),(POINTS,[11],[(1,2,3,5)]),(POINTS,[11,12],cells)):
            with self.subTest(after=after),patch.object(remesh,'_quality',return_value={}),patch.object(remesh,'checkpoint'),\
                 patch.object(remesh,'inspect_exported_volume',return_value=after),self.assertRaises(remesh.RemeshIssue):
                remesh.remove_measured_volume_duplicates(gmsh,99,'impeller',{},POINTS,[11,12],cells)
        mesh.removeDuplicateElements.reset_mock()
        with self.assertRaises(remesh.RemeshIssue):
            remesh.remove_measured_volume_duplicates(gmsh,99,'impeller',{},POINTS,[11,12],[cells[0],(1,3,2,4)])
        mesh.removeDuplicateElements.assert_not_called()
        result=remesh.remove_measured_volume_duplicates(gmsh,99,'impeller',{},POINTS,[11],[cells[0]])
        self.assertEqual(result,(POINTS,[11],[cells[0]]))
        mesh.removeDuplicateElements.assert_not_called()

    def test_msh_coordinates_round_trip_without_repairing_topology(self):
        actual = {1:(0.,0.,1.), 2:(1.,0.,1.), 3:(0.,1.,1.), 4:(0.,0.,1.0000000000000002)}
        before = b"$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$Nodes\n4\n"
        before += b"".join((str(node)+" "+" ".join(format(value,'.16g') for value in xyz)+"\n").encode('ascii') for node,xyz in actual.items())
        topology = b"$EndNodes\n$Elements\n1\n1 4 2 99 99 1 2 3 4\n$EndElements\n"
        with TemporaryDirectory() as directory:
            target = Path(directory)/'owned.msh'; target.write_bytes(before+topology)
            proof = remesh.preserve_msh2_float64_nodes(target,actual)
            self.assertTrue(proof['allCoordinatesFloat64RoundTripVerified'])
            self.assertGreater(proof['maximumGmsh16DigitSpellingDifferenceNativeUnits'],0)
            self.assertFalse(proof['sourceCoordinatesMoved'])
            self.assertTrue(target.read_bytes().endswith(topology))
            self.assertIn(b'4 0 0 1.0000000000000002\n',target.read_bytes())
            self.assertFalse((Path(directory)/'owned.msh.roundtrip').exists())

    def test_msh_coordinate_rewrite_rejects_foreign_nodes_without_overwrite(self):
        for raw in (b'$Nodes\n1\n999 0 0 0\n$EndNodes\n', b'$Nodes\n1\n1 1 0 0\n$EndNodes\n',
                    b'$Nodes\n1\n1 nan 0 0\n$EndNodes\n', b'$Nodes\n2\n1 0 0 0\n1 0 0 0\n$EndNodes\n'):
            with self.subTest(raw=raw), TemporaryDirectory() as directory:
                target=Path(directory)/'owned.msh'; target.write_bytes(raw)
                with self.assertRaises(remesh.RemeshIssue):
                    remesh.preserve_msh2_float64_nodes(target,{1:(0.,0.,0.)})
                self.assertEqual(target.read_bytes(),raw)
                self.assertFalse((Path(directory)/'owned.msh.roundtrip').exists())

    def test_written_boundary_requires_every_exact_source_winding_and_zone(self):
        zones=[{'zoneId':11,'triangles':[(1,2,3)]}]
        for face,zone,accepted in (((2,3,1),11,True),((1,3,2),11,False),((1,2,3),12,False)):
            raw=('$Elements\n1\n1 2 2 '+str(zone)+' 99 '+" ".join(map(str,face))+'\n$EndElements\n').encode('ascii')
            with TemporaryDirectory() as directory:
                target=Path(directory)/'owned.msh';target.write_bytes(raw)
                if accepted:
                    proof=remesh.verify_published_boundary(target,zones)
                    self.assertTrue(proof['everyNativeOrientedTriangleAndZoneVerified'])
                else:
                    with self.assertRaises(remesh.RemeshIssue):remesh.verify_published_boundary(target,zones)

    def test_live_progress_does_not_promote_candidate_or_expose_extra_report_fields(self):
        with TemporaryDirectory() as directory:
            target = Path(directory)
            (target / 'remesh-progress.json').write_text(json.dumps({
                'schemaVersion': remesh.SCHEMA, 'stage': 'measured-volume-quality-diagnostics',
                **remesh.algorithm_metadata(),
                'component': 'impeller', 'privateExtra': 'excluded',
                'generatedVolumeDiagnostics': {'exportedVolumeElementCount': 42,
                    'nativeQualityMetrics': {'minSICN': {'minimum': -1e-15, 'negativeCount': 1}}}}))
            row = remesh.live_progress_summary(target, ['a', 'b', 'c', 'd'])
            self.assertEqual(row['actualExportedVolumeCells'], 42)
            self.assertEqual(row['nativeMinSicn']['minimum'], -1e-15)
            self.assertEqual(row['nativeTail'], ['b', 'c', 'd'])
            self.assertFalse(row['volumeMeshAccepted'])
            self.assertNotIn('privateExtra', row)

    def test_live_progress_rejects_wrong_schema_or_unbounded_file(self):
        with TemporaryDirectory() as directory:
            target = Path(directory)
            path = target / 'remesh-progress.json'
            for raw in ('{}', 'x' * (remesh.inventory.MAX_REPORT_BYTES + 1)):
                path.write_text(raw)
                with self.assertRaises(remesh.RemeshIssue):
                    remesh.live_progress_summary(target, [])

    def test_live_progress_rejects_a_different_selected_algorithm(self):
        with TemporaryDirectory() as directory:
            target=Path(directory)
            (target/"remesh-progress.json").write_text(json.dumps({"schemaVersion":remesh.SCHEMA,
                **remesh.algorithm_metadata("hxt")}))
            with self.assertRaisesRegex(remesh.RemeshIssue,"selected algorithm"):
                remesh.live_progress_summary(target,[],"delaunay")

    def test_cli_passes_explicit_algorithm_to_the_owned_worker_or_supervisor(self):
        for native in (False,True):
            arguments=[str(TOOL),"--output-dir","synthetic-output","--algorithm","delaunay","--optimisation-mode","unoptimised"]
            if native:arguments.append("--native-worker")
            with self.subTest(native=native),patch.object(sys,"argv",arguments),\
                 patch.object(remesh,"run",return_value={"volumeMeshGenerated":False}) as worker,\
                 patch.object(remesh,"supervised_run",return_value={"volumeMeshGenerated":False}) as supervisor,patch("builtins.print"):
                self.assertEqual(remesh.main(),2)
                invoked,other=(worker,supervisor) if native else (supervisor,worker)
                invoked.assert_called_once_with(Path("synthetic-output"),"unoptimised","delaunay")
                other.assert_not_called()

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

    def test_native_fan_volume_closure_retains_signed_source_and_explicit_tolerance(self):
        zones,_,_=remesh.source_triangles(self.model())
        for winding in (zones,[{**row,"triangles":[tuple(reversed(triangle)) for triangle in row["triangles"]]} for row in zones]):
            measured=remesh.volume_closure(winding,POINTS,1/6)
            self.assertTrue(measured["passed"])
            self.assertEqual(measured["relativeTolerance"],1e-8)
            self.assertFalse(measured["nativeWindingChanged"])
            self.assertEqual(abs(measured["nativeSignedFanVolumeNativeUnitsCubed"]),1/6)
        self.assertLess(remesh.volume_closure(winding,POINTS,1/6)["nativeSignedFanVolumeNativeUnitsCubed"],0)
        mismatch=remesh.volume_closure(zones,POINTS,1/3)
        self.assertFalse(mismatch["passed"])
        self.assertEqual(mismatch["relativeDifference"],1)
        self.assertEqual(mismatch["absoluteToleranceNativeUnitsCubed"],(1/6)*1e-8)
        for invalid in (0,math.nan,math.inf,-1):
            with self.subTest(invalid=invalid),self.assertRaises(remesh.RemeshIssue):
                remesh.volume_closure(zones,POINTS,invalid)

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

    def test_measured_native_quality_keeps_all_metric_counts_before_first_gate_rejects(self):
        class Mesh:
            calls=[]
            def getElementQualities(self,tags,quantity):
                self.calls.append(quantity)
                return {"minSICN":[.5,-.01,0,float("nan")], "minDetJac":[1,2,3,4], "volume":[.1,.2,.3,.4]}[quantity]
        gmsh=SimpleNamespace(model=SimpleNamespace(mesh=Mesh()))
        observed=remesh.measured_quality_diagnostics(gmsh,[1,2,3,4])
        self.assertEqual(gmsh.model.mesh.calls,["minSICN","minDetJac","volume"])
        sicn=observed["minSICN"]
        self.assertEqual((sicn["minimum"],sicn["maximum"]),(-.01,.5))
        self.assertEqual((sicn["returnedValueCount"],sicn["finiteValueCount"],sicn["nonFiniteCount"],sicn["nonPositiveCount"]),(4,3,1,2))
        self.assertEqual((sicn["negativeCount"],sicn["zeroCount"]),(1,1))
        self.assertEqual(observed["minDetJac"]["nonPositiveCount"],0)
        self.assertEqual(observed["volume"]["minimum"],.1)
        json.dumps(observed,allow_nan=False)
        with self.assertRaisesRegex(remesh.RemeshIssue,"minSICN"):
            remesh._quality(gmsh,[1,2,3,4])
        self.assertEqual(remesh.finite_statistics([float("inf"),float("nan")],2)["minimum"],None)
        with patch.object(gmsh.model.mesh,"getElementQualities",side_effect=remesh.RemeshIssue("Synthetic deadline")),\
             self.assertRaisesRegex(remesh.RemeshIssue,"deadline"):
            remesh.measured_quality_diagnostics(gmsh,[1])

    def test_tetra_diagnostic_distinguishes_global_sign_bad_cells_and_small_relative_volume(self):
        positive=remesh.tetra_coordinate_diagnostics([(1,2,3,4)],POINTS)
        negative=remesh.tetra_coordinate_diagnostics([(1,3,2,4)],POINTS)
        self.assertEqual(positive["signedDeterminant"]["negativeCount"],0)
        self.assertEqual(negative["signedDeterminant"]["negativeCount"],1)
        self.assertEqual(negative["signedDeterminant"]["minimum"],-1)
        self.assertEqual(positive["coordinateBoundsNativeUnits"]["z"],{"minimum":0.,"maximum":1.})
        points={**POINTS,5:(0.,0.,1e-14),6:(.1,.1,0.),7:(float("inf"),0.,1.)}
        observed=remesh.tetra_coordinate_diagnostics([(1,2,3,4),(1,2,3,5),(1,2,3,6),(1,2,3,7),(1,2,3,99)],points)
        self.assertEqual(observed["exportedTetrahedronCount"],5)
        self.assertEqual(observed["signedDeterminant"]["zeroCount"],1)
        self.assertEqual(observed["signedDeterminant"]["nonFiniteCount"],2)
        self.assertEqual(observed["nonFiniteCoordinateNodeCount"],1)
        self.assertEqual(observed["invalidCellNodeCount"],1)
        self.assertEqual(observed["smallScaleRelativeDeterminantCount"],2)
        self.assertTrue(observed["smallScaleRelativeDeterminantIsDiagnosticOnly"])
        json.dumps(observed,allow_nan=False)
        self.assertNotIn("cells",observed)
        with self.assertRaisesRegex(remesh.RemeshIssue,"signed determinants"):
            remesh.tetra_metrics([(1,3,2,4)],POINTS)

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
            progress["generatedVolumeDiagnostics"]={"sourceMesh":name,"exportedVolumeElementCount":5,
                "tetraCoordinateAndDeterminant":{"signedDeterminant":{"negativeCount":1}},
                "nativeQualityMetrics":{"minSICN":{"nonPositiveCount":1}},"qualityAndBoundaryGatesPassed":False}
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
            measured=report["diagnostic"]["generatedVolumeDiagnostics"]
            self.assertEqual(measured["exportedVolumeElementCount"],5)
            self.assertEqual(measured["nativeQualityMetrics"]["minSICN"]["nonPositiveCount"],1)

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

    def test_unoptimised_native_setting_changes_only_the_optimisation_flag(self):
        full=remesh.volume_mesh_options(1/32)
        unoptimised=remesh.volume_mesh_options(1/32,"unoptimised")
        self.assertEqual(full["Mesh.Optimize"],1)
        self.assertEqual(full["Mesh.Renumber"],0)
        self.assertEqual(full["Mesh.PreserveNumberingMsh2"],1)
        self.assertEqual(unoptimised["Mesh.Optimize"],0)
        self.assertEqual({key:value for key,value in full.items() if key!="Mesh.Optimize"},
                         {key:value for key,value in unoptimised.items() if key!="Mesh.Optimize"})
        self.assertEqual(full["Mesh.Algorithm3D"],10)
        self.assertEqual(full["Mesh.MeshOnlyEmpty"],1)
        for mode in remesh.OPTIMISATION_MODES:
            metadata=remesh.optimisation_metadata(mode)
            self.assertEqual(metadata["hxtDefaultQualityTarget"],.3)
            self.assertFalse(metadata["targetIsMeasuredMinSICN"])
            self.assertFalse(metadata["optimisationSuccessVerified"])
        interior=remesh.optimisation_metadata("gmsh-interior")
        self.assertEqual(remesh.volume_mesh_options(1/32,"gmsh-interior"),unoptimised)
        self.assertTrue(interior["postFillDefaultOptimisationRequested"])
        self.assertEqual(interior["postFillDefaultOptimisationThreshold"],.3)
        self.assertEqual(interior["postFillDefaultOptimisationMetric"],"Native gamma quality, not minSICN")
        self.assertFalse(interior["postFillTargetOverride"])
        self.assertFalse(interior["postFillIterationBoundClaimed"])
        for invalid in ("auto","none",None,True):
            with TemporaryDirectory() as root,patch.object(remesh,"enforce_runtime_limits") as invoked:
                target=Path(root)/"invalid-mode"
                with self.subTest(mode=invalid),self.assertRaises(remesh.RemeshIssue):
                    remesh.run(target,invalid)
                self.assertFalse(target.exists())
                invoked.assert_not_called()

    def test_delaunay_alternate_changes_only_algorithm_and_retains_fixed_boundary_settings(self):
        hxt=remesh.volume_mesh_options(1/32,"unoptimised")
        alternate=remesh.volume_mesh_options(1/32,"unoptimised","delaunay")
        self.assertEqual(alternate["Mesh.Algorithm3D"],1)
        self.assertEqual({key:value for key,value in alternate.items() if key!="Mesh.Algorithm3D"},
                         {key:value for key,value in hxt.items() if key!="Mesh.Algorithm3D"})
        self.assertEqual(alternate["Mesh.MeshOnlyEmpty"],1)
        self.assertEqual(alternate["Mesh.Renumber"],0)
        self.assertEqual(alternate["Mesh.PreserveNumberingMsh2"],1)
        metadata=remesh.optimisation_metadata("unoptimised","delaunay")
        self.assertIsNone(metadata["hxtDefaultQualityTarget"])
        self.assertFalse(metadata["optimisationSuccessVerified"])
        self.assertFalse(metadata["postFillDefaultOptimisationRequested"])
        self.assertEqual(remesh.GMSH_ALGORITHM_3D,10)  # No shared constant mutation.

    def test_unknown_algorithms_and_delaunay_optimisation_combinations_reject_before_mutation(self):
        for algorithm,mode in (("automatic","unoptimised"),(1,"unoptimised"),(None,"full"),
                               (True,"full"),("Delaunay","unoptimised"),("delaunay","full"),("delaunay","gmsh-interior")):
            with self.subTest(algorithm=algorithm,mode=mode),TemporaryDirectory() as root:
                target=Path(root)/"trial"
                with patch.object(remesh,"enforce_runtime_limits") as limits,patch.object(remesh.subprocess,"Popen") as native:
                    with self.assertRaises(remesh.RemeshIssue):remesh.run(target,mode,algorithm)
                    with self.assertRaises(remesh.RemeshIssue):remesh.supervised_run(target,mode,algorithm)
                self.assertFalse(target.exists());limits.assert_not_called();native.assert_not_called()

    def test_delaunay_runtime_diagnostic_projects_the_selected_algorithm_without_hxt_target_claim(self):
        with TemporaryDirectory() as root,patch.dict(os.environ,{"RUNNER_TEMP":root},clear=False):
            target=Path(root)/"alternate"
            with patch.object(remesh,"enforce_runtime_limits",side_effect=remesh.RemeshIssue("Synthetic preflight")):
                summary=remesh.run(target,"unoptimised","delaunay")
            self.assertEqual(summary["gmshAlgorithm3D"],1)
            for name in ("remesh-report.json","remesh-progress.json"):
                report=json.loads((target/name).read_bytes())
                self.assertEqual(report["algorithm"],"delaunay")
                self.assertEqual(report["gmshAlgorithmName"],"Delaunay")
                self.assertEqual(report["gmshAlgorithm3D"],1)
                self.assertIsNone(report["optimisation"]["hxtDefaultQualityTarget"])
                self.assertFalse(report["actualCfdRunVerified"])
            report=json.loads((target/"remesh-report.json").read_bytes())
            self.assertEqual(report["limits"]["maximumCombinedCells"],999999)
            self.assertEqual(report["limits"]["maximumSeconds"],900)

    def test_unoptimised_fill_still_checks_every_cell_and_preserves_native_boundary(self):
        # Inert API adapter with one synthetic tetrahedron; no Gmsh/native run.
        options={}
        class Mesh:
            def addNodes(self,*args): pass
            def addElementsByType(self,*args): pass
            def reclassifyNodes(self): pass
            def createTopology(self,**kwargs): pass
            def getNodes(self): return list(POINTS),[x for point in POINTS.values() for x in point],[]
            def generate(self,dimension): self.generated=dimension
            def getElements(self,dimension,entity):
                if dimension==3: return [4],[[100]],[[1,2,3,4]]
                faces=FACES[:2] if entity==11 else FACES[2:]
                return [2],[[1,2]],[[node for face in faces for node in face]]
            def getElementQualities(self,tags,quantity): return [0.]  # Actual gate must reject.
        mesh=Mesh()
        gmsh=SimpleNamespace(option=SimpleNamespace(setNumber=lambda key,value:options.update({key:value})),
            model=SimpleNamespace(mesh=mesh,add=lambda *args:None,addDiscreteEntity=lambda *args:None,
                getEntities=lambda dim:[(2,11),(2,12)],addPhysicalGroup=lambda *args:None,setPhysicalName=lambda *args:None,
                geo=SimpleNamespace(addSurfaceLoop=lambda *args:1,addVolume=lambda *args:1,synchronize=lambda:None)))
        model=self.model();model["nativeCounts"]={"cellCount":2,"nodeCount":4}
        for algorithm in ("hxt","delaunay"):
            progress={"optimisationMode":"unoptimised","algorithm":algorithm}
            with self.subTest(algorithm=algorithm),TemporaryDirectory() as root,patch.object(remesh,"checkpoint") as checkpoint:
                with self.assertRaisesRegex(remesh.RemeshIssue,"Every generated cell"):
                    remesh.remesh_component(model,"impeller.msh.h5",Path(root),gmsh,None,progress)
                self.assertEqual(options,remesh.volume_mesh_options(1/32,"unoptimised",algorithm))
                self.assertEqual(mesh.generated,3)
                self.assertIn("quality-and-preserved-boundary-equality",[call.args[1] for call in checkpoint.call_args_list])
                self.assertFalse((Path(root)/"impeller.msh").exists())
                measured=progress["generatedVolumeDiagnostics"]
                self.assertEqual(measured["algorithm"],algorithm)
                self.assertEqual(measured["exportedVolumeElementCount"],1)
                self.assertEqual(measured["tetraCoordinateAndDeterminant"]["signedDeterminant"]["minimum"],1)
                self.assertEqual(measured["nativeQualityMetrics"]["minSICN"]["nonPositiveCount"],1)
                self.assertFalse(measured["qualityAndBoundaryGatesPassed"])
                self.assertIn("measured-volume-quality-diagnostics",[call.args[1] for call in checkpoint.call_args_list])

    def interior_fixture(self, mutation=None, invalid_final_quality=False):
        """Inert fake native adapter: these tetrahedra are unit fixtures only."""
        options={}
        class Mesh:
            generated=False
            optimised=False
            cache_rebuilt=False
            calls=[]
            def addNodes(self,*args): pass
            def addElementsByType(self,*args): pass
            def reclassifyNodes(self): pass
            def createTopology(self,**kwargs): pass
            def getNodes(self):
                points=dict(POINTS)
                if self.optimised:
                    points[5]=(.25,.25,.25)
                if (self.optimised and mutation=="coordinate") or (self.generated and mutation=="before-coordinate"):
                    points[1]=(1e-15,0.,0.)
                return list(points),[x for point in points.values() for x in point],[]
            def generate(self,dimension): self.generated=True
            def optimize(self,*args,**kwargs):
                self.calls.append((args,kwargs))
                self.optimised=True
            def rebuildElementCache(self,*,onlyIfNecessary):
                self.asserted_forced_rebuild = onlyIfNecessary is False
                self.cache_rebuilt = self.asserted_forced_rebuild
            def getElements(self,dimension,entity):
                if dimension==3:
                    if not self.optimised:
                        return [4],[[100]],[[1,2,3,4]]
                    return [4],[[101,102,103,104]],[[1,2,3,5,1,4,2,5,2,4,3,5,3,4,1,5]]
                faces=list(FACES[:2] if entity==11 else FACES[2:])
                if self.optimised and mutation=="winding" and entity==11:
                    faces[0]=tuple(reversed(faces[0]))
                if self.optimised and mutation=="missing-face" and entity==11:
                    faces=faces[:-1]
                if self.optimised and mutation=="zone-owner":
                    faces=list(FACES[2:] if entity==11 else FACES[:2])
                return [2],[list(range(1,len(faces)+1))],[[node for face in faces for node in face]]
            def getElementQualities(self,tags,quantity):
                if self.optimised and not self.cache_rebuilt:
                    raise RuntimeError('Unknown element after native edge swap')
                value=-1e-15 if quantity=="minSICN" and (not self.optimised or invalid_final_quality) else .5
                return [value]*len(tags)
        mesh=Mesh();mesh.calls=[]
        def fixture_write(path):
            node_tags,coordinates,_=mesh.getNodes()
            raw=b'$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$PhysicalNames\n3\n2 11 "first"\n2 12 "second"\n3 99 "fluid"\n$EndPhysicalNames\n$Nodes\n'+str(len(node_tags)).encode('ascii')+b"\n"
            for index,node in enumerate(node_tags):
                raw+=(str(node)+" "+" ".join(format(value,'.16g') for value in coordinates[3*index:3*index+3])+"\n").encode('ascii')
            raw+=b"$EndNodes\n$Elements\n8\n"
            for index,face in enumerate(FACES):
                tag=11 if index<2 else 12
                raw+=(f'{index+1} 2 2 {tag} {tag} '+" ".join(map(str,face))+"\n").encode('ascii')
            _,tags,nodes=mesh.getElements(3,1)
            for index,tag in enumerate(tags[0]):
                raw+=(f'{tag} 4 2 99 99 '+" ".join(map(str,nodes[0][index*4:index*4+4]))+"\n").encode('ascii')
            Path(path).write_bytes(raw+b"$EndElements\n")
        gmsh=SimpleNamespace(option=SimpleNamespace(setNumber=lambda key,value:options.update({key:value})),
            model=SimpleNamespace(mesh=mesh,add=lambda *args:None,addDiscreteEntity=lambda *args:None,
                getEntities=lambda dim:[(2,11),(2,12)],addPhysicalGroup=lambda *args:None,setPhysicalName=lambda *args:None,
                remove=lambda:None,geo=SimpleNamespace(addSurfaceLoop=lambda *args:1,addVolume=lambda *args:1,synchronize=lambda:None)),
            write=fixture_write)
        model=self.model();model["nativeCounts"]={"cellCount":10,"nodeCount":4}
        return gmsh,model,options

    def test_interior_optimisation_captures_fresh_actual_counts_and_checks_boundary_before_after(self):
        gmsh,model,options=self.interior_fixture()
        progress={"optimisationMode":"gmsh-interior"}
        with TemporaryDirectory() as root,patch.object(remesh,"checkpoint"):
            result=remesh.remesh_component(model,"impeller.msh.h5",Path(root),gmsh,None,progress)
        self.assertEqual(options,remesh.volume_mesh_options(1/32,"unoptimised"))
        self.assertEqual(gmsh.model.mesh.calls,[(('',),{"force":True})])
        evidence=result["interiorOptimisationDiagnostics"]
        self.assertEqual(evidence["before"]["exportedVolumeElementCount"],1)
        self.assertEqual(evidence["after"]["exportedVolumeElementCount"],4)
        self.assertEqual(evidence["before"]["nativeQualityMetrics"]["minSICN"]["nonPositiveCount"],1)
        self.assertEqual(evidence["after"]["nativeQualityMetrics"]["minSICN"]["nonPositiveCount"],0)
        self.assertTrue(evidence["completeNativeBoundaryBeforeVerified"])
        self.assertTrue(evidence["completeNativeBoundaryAfterVerified"])
        self.assertTrue(evidence["elementCacheRebuiltAfterOptimisation"])
        self.assertTrue(gmsh.model.mesh.asserted_forced_rebuild)
        self.assertTrue(evidence["qualityAccepted"])
        self.assertFalse(evidence["iterationBoundClaimed"])
        self.assertFalse(result["optimisation"]["optimisationSuccessVerified"])
        self.assertTrue(result["nativeBoundaryVolumeClosure"]["passed"])
        json.dumps(result,allow_nan=False)

    def test_interior_optimisation_rejects_exact_source_boundary_mutation_before_or_after(self):
        for mutation in ("before-coordinate","coordinate","winding","missing-face","zone-owner"):
            gmsh,model,_=self.interior_fixture(mutation)
            progress={"optimisationMode":"gmsh-interior"}
            with self.subTest(mutation=mutation),TemporaryDirectory() as root,patch.object(remesh,"checkpoint"):
                with self.assertRaises(remesh.RemeshIssue):
                    remesh.remesh_component(model,"impeller.msh.h5",Path(root),gmsh,None,progress)
                self.assertFalse((Path(root)/"impeller.msh").exists())
            if mutation=="before-coordinate":
                self.assertEqual(gmsh.model.mesh.calls,[])
            else:
                self.assertEqual(gmsh.model.mesh.calls,[(('',),{"force":True})])
                evidence=progress["interiorOptimisationDiagnostics"]
                self.assertEqual(evidence["after"]["exportedVolumeElementCount"],4)
                self.assertFalse(evidence["completeNativeBoundaryAfterVerified"])
                self.assertFalse(evidence["qualityAccepted"])

    def test_interior_optimisation_never_tolerates_negative_final_native_quality(self):
        gmsh,model,_=self.interior_fixture(invalid_final_quality=True)
        progress={"optimisationMode":"gmsh-interior"}
        with TemporaryDirectory() as root,patch.object(remesh,"checkpoint"):
            with self.assertRaisesRegex(remesh.RemeshIssue,"positive Gmsh minSICN"):
                remesh.remesh_component(model,"impeller.msh.h5",Path(root),gmsh,None,progress)
            self.assertFalse((Path(root)/"impeller.msh").exists())
        evidence=progress["interiorOptimisationDiagnostics"]
        self.assertTrue(evidence["completeNativeBoundaryAfterVerified"])
        self.assertEqual(evidence["after"]["nativeQualityMetrics"]["minSICN"]["nonPositiveCount"],4)
        self.assertFalse(evidence["qualityAccepted"])

    def test_interior_optimisation_rejects_invalid_volume_face_incidence_before_native_execution(self):
        gmsh,model,_=self.interior_fixture()
        mesh=gmsh.model.mesh
        original=mesh.getElements
        def duplicate_volume(dimension,entity):
            if dimension==3 and not mesh.optimised:
                return [4],[[100,101]],[[1,2,3,4,1,2,3,4]]
            return original(dimension,entity)
        mesh.getElements=duplicate_volume
        progress={"optimisationMode":"gmsh-interior"}
        with TemporaryDirectory() as root,patch.object(remesh,"checkpoint"):
            with self.assertRaisesRegex(remesh.RemeshIssue,"valid tetra face incidence"):
                remesh.remesh_component(model,"impeller.msh.h5",Path(root),gmsh,None,progress)
        self.assertEqual(mesh.calls,[])
        self.assertFalse(progress["preOptimisationFaceIncidence"]["accepted"])
        self.assertEqual(progress["preOptimisationFaceIncidence"]["duplicateTetraCount"],1)

    def test_interrupted_native_worker_report_removes_partial_mesh_and_never_claims_cfd(self):
        with TemporaryDirectory() as root,patch.dict(os.environ,{"RUNNER_TEMP":root},clear=False):
            target=Path(root)/"trial"
            target.mkdir()
            remesh.persist_json(target,"remesh-progress.json",{"schemaVersion":remesh.SCHEMA,**remesh.algorithm_metadata(),
                "optimisation":remesh.optimisation_metadata("full"),"stage":"fixed-boundary-tetrahedral-fill",
                "component":"impeller", "verifiedSources":[],"journal":[]})
            (target/"impeller.msh").write_bytes(b"Interrupted native output")
            report=remesh.interrupted_report(target,"CFD_CLASSROOM_MESH_WORKER_TIMEOUT",["Native geometry recovery"],300,-15)
            self.assertEqual(report["diagnostic"]["stage"],"fixed-boundary-tetrahedral-fill")
            self.assertEqual(report["diagnostic"]["loggerTail"],["Native geometry recovery"])
            self.assertFalse(report["volumeMeshGenerated"])
            self.assertFalse(report["originalMeshesImmutable"])
            self.assertFalse((target/"impeller.msh").exists())
            self.assertEqual(report["files"],[])

    def test_interrupted_recovery_rejects_mismatched_missing_or_invalid_identity_before_consuming_facts(self):
        valid={"schemaVersion":remesh.SCHEMA,**remesh.algorithm_metadata("delaunay"),
            "optimisation":remesh.optimisation_metadata("unoptimised","delaunay"),"stage":"candidate-fill",
            "verifiedSources":[],"journal":[]}
        mismatches=[{**valid,**remesh.algorithm_metadata("hxt")},
            {key:value for key,value in valid.items() if key!="algorithm"},
            {key:value for key,value in valid.items() if key!="gmshAlgorithm3D"},
            {key:value for key,value in valid.items() if key!="gmshAlgorithmName"},
            {**valid,"gmshAlgorithm3D":True},{**valid,"gmshAlgorithmName":"HXT"},
            {**valid,"schemaVersion":"old-schema"},
            {key:value for key,value in valid.items() if key!="schemaVersion"},
            {**valid,"optimisation":remesh.optimisation_metadata("full")},[]]
        for progress in mismatches:
            with self.subTest(progress=progress),TemporaryDirectory() as root,patch.dict(os.environ,{"RUNNER_TEMP":root},clear=False):
                target=Path(root)/"trial";target.mkdir()
                remesh.persist_json(target,"remesh-progress.json",progress)
                partial=target/"impeller.msh";partial.write_bytes(b"untouched foreign-attempt fixture")
                with patch.object(remesh.inventory,"verify_file") as inspect:
                    with self.assertRaises(remesh.RemeshIssue):
                        remesh.interrupted_report(target,"WORKER_TIMEOUT",[],900,-15,"unoptimised","delaunay")
                    inspect.assert_not_called()
                self.assertFalse((target/"remesh-report.json").exists())
                self.assertEqual(partial.read_bytes(),b"untouched foreign-attempt fixture")
        with TemporaryDirectory() as root,patch.dict(os.environ,{"RUNNER_TEMP":root},clear=False):
            target=Path(root)/"trial";target.mkdir()
            remesh.persist_json(target,"remesh-progress.json",valid)
            report=remesh.interrupted_report(target,"WORKER_TIMEOUT",[],900,-15,"unoptimised","delaunay")
            self.assertEqual(report["algorithm"],"delaunay")
            self.assertEqual(report["diagnostic"]["stage"],"candidate-fill")
            self.assertFalse(report["volumeMeshGenerated"])

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
                remesh.persist_json(target,"remesh-report.json",{"schemaVersion":remesh.SCHEMA,**remesh.algorithm_metadata(),"status":"PREPARATION_REQUIRED",
                    "volumeMeshGenerated":False,"actualCfdRunVerified":False,"simulationExecuted":False,"mainApplicationWrites":False,"files":[],"meshes":[],
                    "optimisation":remesh.optimisation_metadata("full")})
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
            self.assertEqual(launch.call_args.args[0][-2:],["--optimisation-mode","full"])
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
                remesh.persist_json(target,"remesh-progress.json",{"schemaVersion":remesh.SCHEMA,**remesh.algorithm_metadata(),"stage":"fixed-boundary-tetrahedral-fill","component":"impeller",
                    "optimisation":remesh.optimisation_metadata("full"),"verifiedSources":verified,"journal":[],"surfaceZoneBindings":[]})
                (target/"impeller.msh").write_bytes(b"Incomplete native output")
                return process
            def stopped(owned):
                self.assertIs(owned,process)
                owned.result=-15
            def verify(path,name): return next(row for row in verified if row["name"]==name)
            with patch.object(remesh.sys,"platform","linux"),patch.object(remesh.subprocess,"Popen",side_effect=launched),\
                 patch.object(remesh.selectors,"DefaultSelector",return_value=Selector()),\
                 patch.object(remesh.os,"read",side_effect=[b"Info : HXT boundary recovery\n",b""]),\
                 patch.object(remesh.time,"monotonic",side_effect=[0]+[remesh.MAX_SECONDS+1]*20),\
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

    def test_supervisor_passes_unoptimised_mode_and_rejects_a_different_worker_mode(self):
        class Stream:
            def fileno(self): return 123
            def close(self): pass
        class Process:
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
        for worker_mode in ("unoptimised","full"):
            with TemporaryDirectory() as root,patch.dict(os.environ,{"RUNNER_TEMP":root},clear=False):
                target=Path(root)/"unoptimised-trial"
                def launched(*arguments,**options):
                    target.mkdir()
                    remesh.persist_json(target,"remesh-report.json",{"schemaVersion":remesh.SCHEMA,**remesh.algorithm_metadata(),"status":"PREPARATION_REQUIRED",
                        "volumeMeshGenerated":False,"actualCfdRunVerified":False,"simulationExecuted":False,"mainApplicationWrites":False,
                        "files":[],"meshes":[],"optimisation":remesh.optimisation_metadata(worker_mode)})
                    return Process()
                with patch.object(remesh.sys,"platform","linux"),patch.object(remesh.subprocess,"Popen",side_effect=launched) as launch,\
                     patch.object(remesh.selectors,"DefaultSelector",return_value=Selector()),patch.object(remesh.os,"read",return_value=b""):
                    if worker_mode=="full":
                        with self.assertRaisesRegex(remesh.RemeshIssue,"claim contract"):
                            remesh.supervised_run(target,"unoptimised")
                    else:
                        summary=remesh.supervised_run(target,"unoptimised")
                        self.assertEqual(summary["optimisationMode"],"unoptimised")
                        self.assertFalse(summary["volumeMeshGenerated"])
                    self.assertEqual(launch.call_args.args[0][-2:],["--optimisation-mode","unoptimised"])
                retained=json.loads((target/"gmsh-native-tail.json").read_text())
                self.assertEqual(retained["optimisation"]["mode"],"unoptimised")
                self.assertFalse(retained["optimisation"]["optimisationSuccessVerified"])

    def test_supervisor_binds_explicit_delaunay_selection_and_rejects_different_worker_algorithm(self):
        # Inert child mocks only: no native Gmsh/solver execution or mesh claim.
        class Stream:
            def fileno(self):return 123
            def close(self):pass
        class Process:
            stdout=Stream()
            def poll(self):return 2
            def wait(self,timeout):return 2
        class Selector:
            active=True
            def register(self,file,event):self.file=file
            def get_map(self):return {1:True} if self.active else {}
            def select(self,timeout):return [(SimpleNamespace(fileobj=self.file),None)]
            def unregister(self,file):self.active=False
            def close(self):pass
        for worker_algorithm in ("delaunay","hxt","missing"):
            with self.subTest(worker_algorithm=worker_algorithm),TemporaryDirectory() as root,patch.dict(os.environ,{"RUNNER_TEMP":root},clear=False):
                target=Path(root)/"alternate"
                def launched(*arguments,**options):
                    target.mkdir()
                    selector={} if worker_algorithm=="missing" else remesh.algorithm_metadata(worker_algorithm)
                    remesh.persist_json(target,"remesh-report.json",{"schemaVersion":remesh.SCHEMA,**selector,
                        "status":"PREPARATION_REQUIRED","volumeMeshGenerated":False,"actualCfdRunVerified":False,
                        "simulationExecuted":False,"mainApplicationWrites":False,"files":[],"meshes":[],
                        # Same declared mode: the algorithm gate must independently reject mismatches.
                        "optimisation":remesh.optimisation_metadata("unoptimised","delaunay")})
                    return Process()
                with patch.object(remesh.sys,"platform","linux"),patch.object(remesh.subprocess,"Popen",side_effect=launched) as launch,\
                     patch.object(remesh.selectors,"DefaultSelector",return_value=Selector()),patch.object(remesh.os,"read",return_value=b""),patch("builtins.print"):
                    if worker_algorithm=="delaunay":
                        summary=remesh.supervised_run(target,"unoptimised","delaunay")
                        self.assertEqual(summary["gmshAlgorithm3D"],1)
                        self.assertFalse(summary["volumeMeshGenerated"])
                    else:
                        with self.assertRaisesRegex(remesh.RemeshIssue,"claim contract"):
                            remesh.supervised_run(target,"unoptimised","delaunay")
                command=launch.call_args.args[0]
                self.assertEqual(command[command.index("--algorithm")+1],"delaunay")
                retained=json.loads((target/"gmsh-native-tail.json").read_bytes())
                self.assertEqual(retained["gmshAlgorithmName"],"Delaunay")
                self.assertIsNone(retained["optimisation"]["hxtDefaultQualityTarget"])

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
                 patch.object(remesh.time,"monotonic",side_effect=[0]+[remesh.MAX_SECONDS+1]*20),patch.object(remesh,"stop_owned_worker") as stop:
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
