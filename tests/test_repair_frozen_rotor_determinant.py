"""Synthetic topology/arithmetic contracts, never pump or native CFD evidence."""
from copy import deepcopy
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import repair_frozen_rotor_determinant as repair
from test_agglomerate_frozen_rotor_native import grid, pin, counts


def barrel_grid():
    """Test-only thin, convex loft pair embedded in a complete 3x3x3 grid."""
    model = grid(3)
    x = model["coordinates"][:, 0].copy()
    scale = 1 - .1 * (x - 1.5) ** 2
    model["coordinates"][:, 1:] = (model["coordinates"][:, 1:] - 1.5) * scale[:, None]
    model["coordinates"][:, 0] *= .02
    return model


def metric(model):
    areas = repair.quality.polygon_areas(model)
    packet = repair.quality.cell_determinants(areas, model["c0"], model["c1"], model["cellCount"])
    return areas, packet


def diagnostic(models, metrics):
    rows = []
    for name, model in models.items():
        failed = np.flatnonzero(metrics[name]["determinants"] < repair.THRESHOLD) + 1
        rows.append({"component": name, "sourceFile": model["sourceFile"], "cellCount": model["cellCount"],
            "failedSourceCellIds": failed.tolist(), "cellDeterminantSummary": {"failedCellCount": len(failed), "threshold": repair.THRESHOLD}})
    return {"schemaVersion": repair.quality.SCHEMA, "exampleDataCommit": repair.original.inventory.PIN,
        "determinantThreshold": repair.THRESHOLD, "sourceMeshesImmutable": True, "sourceMeshModified": False,
        "thresholdModified": False, "solverExecutedByThisTool": False, "actualCfdRunVerified": False,
        "mainApplicationWrites": False, "components": rows, "combinedFailedCellCount": sum(len(r["failedSourceCellIds"]) for r in rows)}


class RepairGeometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = barrel_grid(); cls.areas, cls.metric = metric(cls.model)

    def _selected(self):
        selected, selection = repair.select_pairs({"impeller": self.model}, {"impeller": self.areas}, {"impeller": self.metric})
        return selected["impeller"], selection

    def test_exact_mask_excludes_physical_boundary_and_removed_shared_face(self):
        pairs, _ = self._selected(); item = pairs[0]; result = item["selectionUnionMetric"]
        self.assertNotIn(item["removedSourceFaceId"], result["includedSourceFaceIds"])
        self.assertEqual(len(result["includedSourceFaceIds"]) + len(result["excludedPhysicalBoundarySourceFaceIds"]), 10)
        ids = np.asarray(result["includedSourceFaceIds"]) - 1
        v = self.areas[ids]; average = np.linalg.norm(v, axis=1).sum() / len(v)
        tensor = sum((np.outer(row / average, row / average) for row in v), np.zeros((3, 3)))
        np.testing.assert_array_equal(tensor, result["includedFaceNormalTensor"])
        self.assertEqual(abs(np.linalg.det(tensor)), result["replicatedDeterminant"])

    def test_formula_rank_deficient_and_boundary_only_unions_are_not_improvements(self):
        model = grid(1); areas, _ = metric(model)
        value = repair.union_metric(model, areas, list(range(model["faceCount"])))
        self.assertEqual(value["replicatedDeterminant"], 0.)
        self.assertEqual(value["includedInternalFaceCount"], 0)
        with self.assertRaises(repair.RepairIssue):
            repair.admissible_metric([0., 0.], 0.)

    def test_threshold_equality_passes_healthy_partner_and_strict_bad_target(self):
        repair.admissible_metric([.0009, .001], .001)
        for before, after in (([.001, .001], .002), ([.0009, .001], .000999), ([.0009, .00095], .00094),
                              ([.0009, .0008], .0009), ([float("nan"), .0008], .01), ([True, .0008], .01), ([.0008, .002], True)):
            with self.subTest(before=before, after=after), self.assertRaises(repair.RepairIssue):
                repair.admissible_metric(before, after)

    def test_selected_union_uses_highest_valid_neighbour_and_deterministic_tie(self):
        pairs, selection = self._selected(); again, _ = self._selected()
        self.assertEqual(pairs, again); self.assertEqual(len(pairs), 1)
        item = pairs[0]; failing = next(v - 1 for v, d in zip(item["sourceCellIds"], item["sourceDeterminants"]) if d < .001)
        incidence, offsets = repair.pilot.cell_faces(self.model); candidates = []
        for face in incidence[offsets[failing]:offsets[failing + 1]]:
            a, b = sorted((int(self.model["c0"][face]) - 1, int(self.model["c1"][face]) - 1))
            if a < 0:
                continue
            try:
                candidates.append(repair.candidate(self.model, int(face), a, b, incidence, offsets, self.areas, self.metric))
            except (repair.RepairIssue, repair.pilot.AgglomerationIssue):
                pass
        best = min(candidates, key=lambda p: (-p["selectionUnionMetric"]["replicatedDeterminant"], p["removedSourceFaceId"]))
        self.assertEqual(item, best); self.assertFalse(selection["maximumMatchingComputed"])

    def test_explicit_new_policy_allows_boundary_pair_without_weakening_old_pilot(self):
        pairs, _ = self._selected(); self.assertTrue(pairs[0]["boundaryAdjacent"])
        with self.assertRaisesRegex(repair.pilot.AgglomerationIssue, "protected"):
            base = {k: pairs[0][k] for k in ("sourceCellIds", "removedSourceFaceId", "sourceCellVolumesAssumedM3",
                "unionVolumeAssumedM3", "relativePairVolumeDifference", "closedOrientedManifoldUnionVerified")}
            repair.pilot.derive_model(self.model, [base])
        derived, faces, cells, removed = repair.derive_model(self.model, pairs, self.areas, self.metric)
        self.assertEqual(derived["cellCount"], 26); self.assertEqual(len(removed), 1)
        np.testing.assert_array_equal(derived["coordinates"], self.model["coordinates"])
        for i, source_id in enumerate(faces):
            np.testing.assert_array_equal(repair.pilot.polygon(derived, i), repair.pilot.polygon(self.model, int(source_id) - 1))
        actual = repair.quality.cell_determinants(self.areas[faces - 1], derived["c0"], derived["c1"], derived["cellCount"])
        proof = repair.impact(self.metric, actual, cells, pairs)
        self.assertEqual(proof["sourceFailedCellCount"], 25)
        self.assertEqual(proof["derivedFailedCellCount"], 24)
        self.assertEqual(proof["sourceFailedCellsMappedToPassingDerivedCellCount"], 1)
        self.assertEqual(proof["healthySourceCellsMappedToFailingDerivedCellCount"], 0)
        self.assertEqual(repair.original.audit_cells(derived)["totalCellVolumeAssumedM3"], repair.original.audit_cells(self.model)["totalCellVolumeAssumedM3"])

    def test_convexity_and_all_pyramids_are_positive_with_native_face_centres(self):
        pairs, _ = self._selected(); checks = pairs[0]["geometryChecks"]
        self.assertTrue(checks["foundation12FacePlaneReplicationPassed"])
        self.assertLess(checks["maximumOtherFaceCentreOutwardCosine"], -1e-6)
        self.assertGreater(checks["minimumCentroidFacePyramidVolumeAssumedM3"], 0)
        self.assertGreater(checks["minimumFanTetrahedronVolumeAssumedM3"], 0)
        xyz = np.asarray([[0., 0, 0], [2., 0, 0], [1., 1., 0], [0., 1., 0]])
        centre = repair.face_centre(xyz, np.asarray([0., 0, 1.5]))
        np.testing.assert_allclose(centre, [7 / 9, 4 / 9, 0.])
        self.assertFalse(np.array_equal(centre, xyz.mean(axis=0)))

    def test_native_concavity_replication_rejects_coplanar_split_faces(self):
        centres = np.asarray([[0., 0, 0], [1., 0, 0]])
        areas = np.asarray([[0., 1., 0], [0., 1., 0]])
        with self.assertRaisesRegex(repair.RepairIssue, "ConcaveOrPlanar") as caught:
            repair.foundation_face_plane_check(centres, areas)
        self.assertEqual(caught.exception.evidence["maximumOtherFaceCentreOutwardCosine"], 0.)
        # Physical inward offset makes the convex barrel pass; no tolerance
        # or source-face coalescing workaround is introduced for flat facets.
        pairs, _ = self._selected(); self.assertTrue(pairs)

    def test_rectangular_union_metric_improvement_does_not_bypass_native_concavity(self):
        model = grid(3); model["coordinates"][:, 0] *= .02
        areas, packet = metric(model)
        selected, proof = repair.select_pairs({"impeller": model}, {"impeller": areas}, {"impeller": packet})
        self.assertFalse(selected["impeller"])
        self.assertGreater(proof["rejectedCandidateReasonCounts"].get("Foundation12ConcaveOrPlanarSplitFaceUnion", 0), 0)

    def test_nonplanar_or_concave_union_cannot_use_metric_as_geometry_authority(self):
        pairs, _ = self._selected(); item = pairs[0]; face = item["removedSourceFaceId"] - 1
        members = {v for v in item["sourceCellIds"]}; incidence, offsets = repair.pilot.cell_faces(self.model)
        outside = sorted(set(int(f) for c in members for f in incidence[offsets[c - 1]:offsets[c]]) - {face})
        altered = deepcopy(self.model)
        vertex = int(repair.pilot.polygon(altered, outside[0])[0]); altered["coordinates"][vertex, 2] += .1
        with self.assertRaises(repair.RepairIssue):
            repair.geometry_checks(altered, outside, members, repair.quality.polygon_areas(altered), item["unionVolumeAssumedM3"])

    def test_partial_neighbour_scan_never_claims_highest_candidate(self):
        with patch.object(repair, "MAX_CANDIDATE_CHECKS", 1):
            selected, proof = repair.select_pairs({"impeller": self.model}, {"impeller": self.areas}, {"impeller": self.metric})
        self.assertTrue(proof["candidateScanCapped"]); self.assertFalse(proof["targetInventoryExhausted"])
        self.assertFalse(selected["impeller"])

    def test_manual_pair_tamper_and_duplicate_membership_are_rejected(self):
        pairs, _ = self._selected()
        with self.assertRaisesRegex(repair.RepairIssue, "disjoint"):
            repair.derive_model(self.model, pairs * 2, self.areas, self.metric)
        pairs[0]["sourceDeterminants"][0] = 0
        with self.assertRaisesRegex(repair.RepairIssue, "current"):
            repair.derive_model(self.model, pairs, self.areas, self.metric)

    def test_two_bad_cells_still_bad_union_is_not_reported_as_two_repairs(self):
        pair = {"sourceCellIds": [1, 2], "sourceDeterminants": [.0003, .0004],
            "selectionUnionMetric": {"replicatedDeterminant": .0005}}
        before = {"determinants": np.asarray([.0003, .0004, .005])}
        after = {"determinants": np.asarray([.0005, .005])}
        result = repair.impact(before, after, np.asarray([0, 0, 1]), [pair])
        self.assertEqual(result["sourceFailedCellCount"], 2)
        self.assertEqual(result["derivedFailedCellCount"], 1)
        self.assertEqual(result["sourceFailedCellsMappedToPassingDerivedCellCount"], 0)
        self.assertEqual(result["sourceFailedCellsMappedToStillFailingDerivedCellCount"], 2)

    def test_pair_cap_cannot_exceed_32_or_accept_boolean(self):
        for maximum in (0, 33, True):
            with self.subTest(maximum=maximum), self.assertRaises(repair.RepairIssue):
                repair.select_pairs({"impeller": self.model}, {"impeller": self.areas}, {"impeller": self.metric}, maximum_pairs=maximum)


class RepairOutput(unittest.TestCase):
    def _run(self, root, *, flat=False, bad_diagnostic=False, bad_post=False):
        impeller = grid(3) if flat else barrel_grid()
        if flat:
            impeller["coordinates"][:, 0] *= .02
        models = {"impeller": impeller, "volute": grid(1, "volute")}
        metrics = {name: metric(model)[1] for name, model in models.items()}
        packet = diagnostic(models, metrics)
        if bad_diagnostic:
            packet["components"][0]["failedSourceCellIds"] = []
        inputs = root / "inputs"; inputs.mkdir()
        for name in repair.original.inventory.MESH_NAMES:
            (inputs / name).write_bytes(b"SYNTHETIC-SOURCE-TEST-ONLY")
        source_json = root / "source-diagnostic.json"; source_json.write_bytes(repair.original.encoded(packet))
        calls = [0]
        def verify(path, name):
            calls[0] += 1
            if bad_post and calls[0] > 2:
                raise ValueError("Test-only source postcheck SHA changed")
            return pin(name.split(".")[0])
        with patch.dict(os.environ, {"RUNNER_TEMP": str(root)}), patch.object(repair.original.inventory, "verify_file", side_effect=verify), \
                patch.object(repair.original, "read_mesh", side_effect=lambda path, name: deepcopy(models[name])):
            report = repair.repair(inputs, root / "repair", source_json)
        return report, models

    def _verify(self, root, models):
        with patch.object(repair.original, "SOURCE_COUNTS", {name: counts(model) for name, model in models.items()}):
            return repair.verify_output(root / "repair")

    def test_real_array_numerics_and_current_output_maps_are_bound_without_native_claim(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); report, models = self._run(root)
            self.assertEqual(report["status"], "DeterminantPrototypeDerivedNativeAcceptanceRequired")
            self.assertTrue(report["originalMeshesImmutable"]); self.assertTrue(report["volumeMeshDerived"])
            self.assertEqual(report["selection"]["selectedPairCount"], 1)
            self.assertEqual(report["combinedCounts"]["cellCount"], 27)
            self.assertEqual(report["sourceReplicatedFailedCellCount"], 26)
            self.assertEqual(report["combinedDeterminantImpact"]["derivedFailedCellCount"], 25)
            for key in repair.FALSE_CLAIMS:
                self.assertIs(report[key], False)
            self.assertFalse(report["allSourceDeterminantFailuresRepaired"])
            self.assertEqual(self._verify(root, models)["report"], report)
            self.assertEqual((root / "repair/derivation-policy.json").read_bytes(), repair.original.encoded(repair.POLICY))
            self.assertEqual((root / "repair/source-quality-localization.json").read_bytes(), (root / "source-diagnostic.json").read_bytes())

    def test_actual_zero_safe_pairs_writes_diagnostic_only(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); report, _ = self._run(root, flat=True)
            self.assertEqual(report["status"], "DeterminantPrototypeEligiblePairsRequired")
            self.assertFalse(report["volumeMeshDerived"]); self.assertEqual(report["selection"]["selectedPairCount"], 0)
            self.assertEqual({p.name for p in (root / "repair").iterdir()}, {repair.REPORT_NAME, "derivation-policy.json", "source-quality-localization.json"})
            self.assertTrue(report["equationStages"]); self.assertEqual(report["sourceReplicatedFailedCellCount"], 26)

    def test_source_target_ids_must_match_complete_recomputation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); report, _ = self._run(root, bad_diagnostic=True)
            self.assertEqual(report["status"], "DeterminantPrototypeNotDerived")
            self.assertIn("failed-cell ID", report["action"])
            self.assertFalse(any(p.is_dir() for p in (root / "repair").iterdir()))

    def test_policy_written_before_source_arrays_read(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); inputs = root / "inputs"; inputs.mkdir()
            models = {"impeller": barrel_grid(), "volute": grid(1, "volute")}
            packet = diagnostic(models, {n: metric(m)[1] for n, m in models.items()})
            source_json = root / "diagnostic.json"; source_json.write_bytes(repair.original.encoded(packet))
            def read(path, name):
                self.assertEqual((root / "repair/derivation-policy.json").read_bytes(), repair.original.encoded(repair.POLICY))
                return deepcopy(models[name])
            with patch.dict(os.environ, {"RUNNER_TEMP": str(root)}), patch.object(repair.original.inventory, "verify_file", side_effect=lambda p, n: pin(n.split(".")[0])), \
                    patch.object(repair.original, "read_mesh", side_effect=read):
                self.assertTrue(repair.repair(inputs, root / "repair", source_json)["volumeMeshDerived"])

    def test_source_sha_postcheck_failure_never_promotes_immutability(self):
        with TemporaryDirectory() as tmp:
            report, _ = self._run(Path(tmp), bad_post=True)
            self.assertFalse(report["originalMeshesImmutable"]); self.assertFalse(report["volumeMeshDerived"])

    def test_policy_tamper_and_false_native_claim_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); report, models = self._run(root)
            for key, value in (("actualCfdRunVerified", True), ("allSourceDeterminantFailuresRepaired", True)):
                changed = deepcopy(report); changed[key] = value
                (root / "repair" / repair.REPORT_NAME).write_bytes(repair.original.inventory.bounded_json(changed))
                with self.subTest(key=key), self.assertRaises(repair.RepairIssue):
                    self._verify(root, models)
            (root / "repair" / repair.REPORT_NAME).write_bytes(repair.original.inventory.bounded_json(report))
            (root / "repair/derivation-policy.json").write_bytes(b"{}")
            with self.assertRaisesRegex(repair.RepairIssue, "policy bytes"):
                self._verify(root, models)

    def test_rehashed_map_membership_and_rejected_geometry_metadata_cannot_pass(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); report, models = self._run(root)
            changed = deepcopy(report); changed["components"][0]["pairs"][0]["geometryChecks"]["maximumOtherFaceCentreOutwardCosine"] = 0
            (root / "repair" / repair.REPORT_NAME).write_bytes(repair.original.inventory.bounded_json(changed))
            with self.assertRaisesRegex(repair.RepairIssue, "geometry"):
                self._verify(root, models)
            changed = deepcopy(report); metadata = changed["components"][0]["mappingFiles"][1]
            path = root / "repair" / metadata["path"]; raw = path.read_bytes(); path.write_bytes(raw.replace(b"0\n", b"1\n", 1))
            metadata.update(bytes=path.stat().st_size, sha256=sha256(path.read_bytes()).hexdigest())
            (root / "repair" / repair.REPORT_NAME).write_bytes(repair.original.inventory.bounded_json(changed))
            with self.assertRaises(repair.RepairIssue):
                self._verify(root, models)

    def test_failure_accounting_cannot_hide_unrepaired_source_cells(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); report, models = self._run(root)
            report["components"][0]["determinantImpact"]["derivedFailedCellCount"] = 0
            (root / "repair" / repair.REPORT_NAME).write_bytes(repair.original.inventory.bounded_json(report))
            with self.assertRaisesRegex(repair.RepairIssue, "hide"):
                self._verify(root, models)

    def test_bad_output_paths_and_existing_destination_fail_before_source_read(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); inputs = root / "inputs"; inputs.mkdir(); output = root / "existing"; output.mkdir()
            marker = output / "keep"; marker.write_bytes(b"UNCHANGED")
            with patch.dict(os.environ, {"RUNNER_TEMP": str(root)}), patch.object(repair.original, "read_mesh") as read:
                for target in (root.parent / "outside-repair", inputs / "nested", output):
                    with self.subTest(target=target), self.assertRaises(ValueError):
                        repair.repair(inputs, target, root / "unused.json")
                read.assert_not_called()
            self.assertEqual(marker.read_bytes(), b"UNCHANGED")

    def test_duplicate_keys_and_nonfinite_diagnostic_rejected(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "diagnostic.json"
            for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}'):
                target.write_bytes(raw)
                with self.assertRaises(repair.RepairIssue):
                    repair.read_source_diagnostic(target)

    def test_unsupported_diagnostic_fields_cannot_enter_public_geometry_artifact(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); models = {"impeller": barrel_grid(), "volute": grid(1, "volute")}
            packet = diagnostic(models, {n: metric(m)[1] for n, m in models.items()})
            packet["unsupportedPayload"] = "REJECT-TEST-ONLY"
            target = root / "diagnostic.json"; target.write_bytes(repair.original.encoded(packet))
            with self.assertRaisesRegex(repair.RepairIssue, "Unsupported"):
                repair.read_source_diagnostic(target)


if __name__ == "__main__":
    unittest.main()
