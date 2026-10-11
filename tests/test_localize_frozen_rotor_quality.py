"""Synthetic metric/topology contracts, never native CFD evidence."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import localize_frozen_rotor_quality as quality


def triangle_model():
    # Two tetrahedra share one original triangle; each tetra has only one
    # internal normal, so the default wellposedness tensor is rank deficient.
    points = np.asarray([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.], [0., 0., -1.]])
    faces = [[0, 1, 2], [0, 3, 1], [1, 3, 2], [2, 3, 0], [0, 1, 4], [1, 2, 4], [2, 0, 4]]
    return {"component": "impeller", "sourceFile": {"name": "synthetic.msh.h5", "sha256": "a" * 64},
        "coordinates": points, "faceWidths": np.full(7, 3, dtype="u2"), "faceOffsets": np.arange(8, dtype="i8") * 3,
        "faceNodes": np.asarray(faces, dtype="u4").reshape(-1), "c0": np.asarray([1, 1, 1, 1, 2, 2, 2], dtype="u4"),
        "c1": np.asarray([2, 0, 0, 0, 0, 0, 0], dtype="u4"), "cellTypes": np.asarray([2, 2], dtype="u1"),
        "cellCount": 2, "faceCount": 7, "nodeCount": 5,
        "topology": {"cells": {"zones": [{"zoneId": 36, "name": "impeller"}]}},
        "faceZones": [{"name": "internal", "zoneType": 2, "zoneId": 4, "minimumId": 1, "maximumId": 1},
            {"name": "blade", "zoneType": 3, "zoneId": 9, "minimumId": 2, "maximumId": 4},
            {"name": "interface-impeller-outlet", "zoneType": 24, "zoneId": 10, "minimumId": 5, "maximumId": 7}]}


class SourceQualityContracts(unittest.TestCase):
    def test_default_formula_is_not_meshqualitydict_overload(self):
        areas = np.asarray([[1., 0, 0], [0, 1., 0], [0, 0, 1.]])
        result = quality.cell_determinants(areas, np.ones(3, dtype="u4"), np.full(3, 2, dtype="u4"), 2)
        np.testing.assert_allclose(result["determinants"], [1., 1.])
        self.assertEqual(result["internalFaceCounts"].tolist(), [3, 3])

    def test_boundary_area_does_not_enter_default_metric(self):
        areas = np.asarray([[1., 0, 0], [0, 1., 0], [0, 0, 1.], [100., 50., 20.]])
        result = quality.cell_determinants(areas, np.ones(4, dtype="u4"), np.asarray([2, 2, 2, 0], dtype="u4"), 2)
        np.testing.assert_allclose(result["determinants"], [1., 1.])

    def test_six_equal_axis_normals_have_dimensionless_determinant_eight(self):
        areas = np.asarray([[1., 0, 0], [-1., 0, 0], [0, 1., 0], [0, -1., 0], [0, 0, 1.], [0, 0, -1.]])
        result = quality.cell_determinants(areas, np.ones(6, dtype="u4"), np.full(6, 2, dtype="u4"), 2)
        np.testing.assert_allclose(result["determinants"], [8., 8.])

    def test_uniform_coordinate_scaling_and_winding_do_not_change_metric(self):
        areas = np.asarray([[1., 0, 0], [0, 2., 0], [0, 0, 3.]])
        adjacent = (np.ones(3, dtype="u4"), np.full(3, 2, dtype="u4"), 2)
        expected = quality.cell_determinants(areas, *adjacent)["determinants"]
        np.testing.assert_allclose(quality.cell_determinants(-areas * 100, *adjacent)["determinants"], expected)

    def test_rank_deficient_and_no_internal_face_cells_are_zero(self):
        result = quality.cell_determinants(np.asarray([[1., 0, 0], [0, 1., 0]]), np.asarray([1, 3], dtype="u4"), np.asarray([2, 0], dtype="u4"), 3)
        np.testing.assert_allclose(result["determinants"], [0., 0., 0.])
        self.assertEqual(result["internalFaceCounts"].tolist(), [1, 1, 0])

    def test_metric_rejects_invalid_adjacency_nonfinite_and_boolean_count(self):
        for areas, c0, c1, count in (([[float("nan"), 0, 0]], [1], [2], 2), ([[1., 0, 0]], [1], [1], 2),
                                   ([[1., 0, 0]], [1], [3], 2), ([[1., 0, 0]], [1], [0], True)):
            with self.subTest(count=count), self.assertRaises(quality.LocalizationIssue):
                quality.cell_determinants(np.asarray(areas), np.asarray(c0), np.asarray(c1), count)

    def test_native_triangle_and_nonplanar_polygon_area_vectors(self):
        model = triangle_model()
        np.testing.assert_allclose(quality.polygon_areas(model)[0], [0., 0., .5])
        model.update(coordinates=np.asarray([[0., 0, 0], [1., 0, 0], [1., 1., .2], [0., 1., 0]]),
                     faceCount=1, faceWidths=np.asarray([4], dtype="u2"), faceOffsets=np.asarray([0, 4]), faceNodes=np.asarray([0, 1, 2, 3], dtype="u4"))
        np.testing.assert_allclose(quality.polygon_areas(model)[0], [-.1, -.1, 1.])
        model["faceNodes"] = model["faceNodes"][::-1]
        np.testing.assert_allclose(quality.polygon_areas(model)[0], [.1, .1, -1.])

    def test_polygon_rejects_zero_area_or_missing_connectivity(self):
        model = triangle_model()
        model["coordinates"][:] = 0
        with self.assertRaisesRegex(quality.LocalizationIssue, "nonzero"):
            quality.polygon_areas(model)
        model = triangle_model(); model["faceOffsets"][-1] -= 1
        with self.assertRaisesRegex(quality.LocalizationIssue, "complete"):
            quality.polygon_areas(model)

    def test_graph_hops_are_minimal_and_do_not_cross_source_components(self):
        # 1--2--3--4 and an independent 5--6 pair.
        c0 = np.asarray([1, 2, 3, 5], dtype="u4"); c1 = np.asarray([2, 3, 4, 6], dtype="u4")
        result = quality.adjacency_hops(c0, c1, 6, np.asarray([True, False, False, False, False, False]))
        self.assertEqual(result["distances"].tolist(), [0, 1, 2, 3, -1, -1])
        self.assertTrue(result["allReachableDistancesExhausted"])

    def test_graph_cap_is_explicit_and_not_unreachable_claim(self):
        result = quality.adjacency_hops(np.asarray([1, 2, 3]), np.asarray([2, 3, 4]), 4,
            np.asarray([True, False, False, False]), maximum_layers=1)
        self.assertEqual(result["distances"].tolist(), [0, 1, -1, -1])
        self.assertFalse(result["allReachableDistancesExhausted"])
        self.assertEqual(result["maximumComputedLayer"], 1)

    def test_graph_rejects_noninteger_or_self_neighbour_rows(self):
        for a, b in (([1], [1]), ([1.], [2.]), ([1], [4])):
            with self.assertRaises(quality.LocalizationIssue):
                quality.adjacency_hops(np.asarray(a), np.asarray(b), 3, np.asarray([True, False, False]))

    def test_boundary_roles_are_actual_source_names_and_types(self):
        groups, contacts = quality.boundary_groups(triangle_model())
        self.assertEqual(groups["wall"].tolist(), [True, False])
        self.assertEqual(groups["interface"].tolist(), [False, True])
        self.assertEqual(contacts["blade"].tolist(), [0])

    def test_all_failed_source_ids_and_contacts_are_retained(self):
        report = quality.diagnose_model(triangle_model())
        self.assertEqual(report["failedSourceCellIds"], [1, 2])
        self.assertEqual(report["failedCells"][0]["internalFaceAdjacencyHops"]["interface"], 1)
        self.assertEqual(report["failedCells"][0]["directBoundaryPatches"], ["blade"])
        self.assertEqual(report["failedCells"][0]["sourceCellZoneId"], 36)
        self.assertEqual(report["failedCells"][0]["diagnosticVertexMeanNativeCoordinates"], [.25, .25, .25])
        self.assertFalse(report["nativeFailedCellSetVerified"])
        self.assertFalse(report["nativeGeometryQualityVerified"])
        self.assertFalse(report["actualCfdRunVerified"])

    def test_gate_equality_is_preserved_without_tolerance_relaxation(self):
        metrics = {"determinants": np.asarray([.001, .000999]), "internalFaceCounts": np.asarray([1, 1]),
                   "normalTensors": np.tile(np.eye(3), (2, 1, 1))}
        with patch.object(quality, "cell_determinants", return_value=metrics):
            report = quality.diagnose_model(triangle_model())
        self.assertEqual(report["failedSourceCellIds"], [2])
        self.assertEqual(report["cellDeterminantSummary"]["threshold"], .001)

    def test_comparison_requires_real_native_proofs_and_exact_ids(self):
        report = {"components": [{"cellCount": 2, "failedSourceCellIds": [1, 2]}, {"cellCount": 2, "failedSourceCellIds": []}]}
        actual = {"exampleDataCommit": quality.original.inventory.PIN, "determinantThreshold": .001,
            "nativeFailedCellSetVerified": True, "sourceCellOrderVerified": True, "originalBoundaryUncoupledMaskVerified": True,
            "components": [{"component": "impeller", "failedSourceCellIds": [1, 2]}, {"component": "volute", "failedSourceCellIds": []}]}
        self.assertTrue(quality.compare_native(report, actual)["exactFailedCellSetsMatch"])
        actual["sourceCellOrderVerified"] = False
        with self.assertRaisesRegex(quality.LocalizationIssue, "proof"):
            quality.compare_native(report, actual)
        actual["sourceCellOrderVerified"] = True; actual["components"][0]["failedSourceCellIds"] = [1]
        with self.assertRaisesRegex(quality.LocalizationIssue, "disagree"):
            quality.compare_native(report, actual)

    def test_native_comparison_rejects_duplicates_boolean_and_changed_threshold(self):
        report = {"components": [{"cellCount": 2, "failedSourceCellIds": [1]}, {"cellCount": 2, "failedSourceCellIds": []}]}
        actual = {"exampleDataCommit": quality.original.inventory.PIN, "determinantThreshold": .001,
            "nativeFailedCellSetVerified": True, "sourceCellOrderVerified": True, "originalBoundaryUncoupledMaskVerified": True,
            "components": [{"component": "impeller", "failedSourceCellIds": [1]}, {"component": "volute", "failedSourceCellIds": []}]}
        for ids in ([1, 1], [True], [3]):
            actual["components"][0]["failedSourceCellIds"] = ids
            with self.subTest(ids=ids), self.assertRaises(quality.LocalizationIssue):
                quality.compare_native(report, actual)
        actual["components"][0]["failedSourceCellIds"] = [1]; actual["determinantThreshold"] = .000001
        with self.assertRaisesRegex(quality.LocalizationIssue, "unchanged"):
            quality.compare_native(report, actual)

    def test_output_is_bounded_exclusive_and_never_replaces_evidence(self):
        with TemporaryDirectory() as folder, patch.object(quality.original.inventory, "output_path"):
            target = Path(folder) / "diagnostic.json"
            row = quality.write_report(target, {"status": "SYNTHETIC"})
            self.assertEqual(row["bytes"], target.stat().st_size)
            with self.assertRaisesRegex(quality.LocalizationIssue, "fresh"):
                quality.write_report(target, {})
            with patch.object(quality, "MAX_REPORT", 2), self.assertRaisesRegex(quality.LocalizationIssue, "32 MiB"):
                quality.write_report(Path(folder) / "oversize.json", {"long": "value"})
            self.assertFalse((Path(folder) / "oversize.json").exists())


if __name__ == "__main__":
    unittest.main()
