"""Meshing contract tests; synthetic tetrahedra are not pump/CFD evidence."""
import importlib.util
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
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
            self.assertEqual([path.name for path in target.iterdir()],["remesh-report.json"])
            report=json.loads((target/"remesh-report.json").read_text())
            self.assertEqual(report["diagnostic"]["stage"],"runtime-preflight")
            self.assertEqual(report["status"],"PREPARATION_REQUIRED")
            self.assertFalse(report["classroomScaleAssumption"]["verifiedFromSource"])
            self.assertEqual(report["coordinateUnits"],"UNVERIFIED")
            self.assertEqual(report["files"],[])
            for flag in ("actualCfdRunVerified","simulationExecuted","nativeCaseGenerated","mainApplicationWrites","openFoamConversionVerified"):
                self.assertIs(report[flag],False)

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
