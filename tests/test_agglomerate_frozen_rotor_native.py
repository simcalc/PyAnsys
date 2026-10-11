"""Synthetic array fixtures test derivation contracts, never physical CFD proof."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import agglomerate_frozen_rotor_native as pilot
import numpy as np


def pin(component):
    name = pilot.original.COMPONENTS[component][0]
    size, digest = pilot.original.inventory.FILES[name]
    return {"name": name, "bytes": size, "sha256": digest, "sourceUrl": pilot.original.inventory.mesh_url(name)}


def grid(n=10, component="impeller"):
    """A labelled Cartesian test grid, unrelated to the official pump geometry."""
    xyz = np.asarray([(i, j, k) for i in range(n + 1) for j in range(n + 1) for k in range(n + 1)], dtype="f8")
    def node(i, j, k):
        return i * (n + 1) ** 2 + j * (n + 1) + k
    records = {}; cells = 0
    for i in range(n):
        for j in range(n):
            for k in range(n):
                cells += 1
                vertices = [node(i, j, k), node(i + 1, j, k), node(i + 1, j + 1, k), node(i, j + 1, k),
                            node(i, j, k + 1), node(i + 1, j, k + 1), node(i + 1, j + 1, k + 1), node(i, j + 1, k + 1)]
                centre = xyz[vertices].mean(axis=0)
                for local in ((0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
                    face = [vertices[v] for v in local]; key = tuple(sorted(face))
                    if key in records:
                        records[key][2] = cells
                    else:
                        v = xyz[face]
                        if np.dot(np.cross(v[1] - v[0], v[2] - v[0]), v.mean(axis=0) - centre) > 0:
                            face.reverse()
                        records[key] = [face, cells, 0]
    interior = [row for row in records.values() if row[2]]
    boundary = [row for row in records.values() if not row[2]]
    rows = interior + boundary
    widths = np.asarray([len(row[0]) for row in rows], dtype="u2")
    zones = []
    if interior:
        zones.append({"name": "interior-original", "zoneType": 2, "zoneId": 4, "minimumId": 1, "maximumId": len(interior)})
    zones.append({"name": "wall-original", "zoneType": 3, "zoneId": 3, "minimumId": len(interior) + 1, "maximumId": len(rows)})
    return {"component": component, "sourceFile": pin(component), "coordinates": xyz, "faceWidths": widths,
            "faceOffsets": np.r_[0, np.cumsum(widths, dtype="i8")], "faceNodes": np.asarray([v for row in rows for v in row[0]], dtype="u4"),
            "c0": np.asarray([row[1] for row in rows], dtype="u4"), "c1": np.asarray([row[2] for row in rows], dtype="u4"),
            "cellTypes": np.full(cells, 4, dtype="u1"), "cellCount": cells, "nodeCount": len(xyz), "faceCount": len(rows), "faceZones": zones}


def counts(model):
    return {**{key: model[key] for key in ("cellCount", "nodeCount", "faceCount")},
            "internalFaceCount": int(np.count_nonzero((model["c0"] > 0) & (model["c1"] > 0)))}


class PilotArrays(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = grid()

    def test_three_full_boundary_adjacency_expansions(self):
        protected, layers = pilot.boundary_protection(self.model)
        self.assertEqual(layers, [488, 784, 936, 992])
        self.assertFalse(protected[4 * 100 + 4 * 10 + 4])
        self.assertTrue(protected[3 * 100 + 4 * 10 + 4])
        self.assertEqual(np.count_nonzero(~protected), 8)

    def test_pair_selection_is_deterministic_disjoint_and_protected(self):
        first, evidence = pilot.select_pairs(self.model, requested=4)
        again, _ = pilot.select_pairs(self.model, requested=4)
        self.assertEqual(first, again)
        self.assertEqual(len(first), 4)
        self.assertEqual(evidence["protectedBoundaryAdjacencyRings"], 3)
        ids = [v for pair in first for v in pair["sourceCellIds"]]
        self.assertEqual(len(ids), len(set(ids)))
        protected, _ = pilot.boundary_protection(self.model)
        self.assertTrue(all(not protected[v - 1] for v in ids))
        self.assertTrue(all(pair["unionVolumeAssumedM3"] == 2 for pair in first))

    def test_measured_insufficient_pairs_does_not_invent_matching(self):
        pairs, selection = pilot.select_pairs(self.model)
        self.assertEqual(len(pairs), 4)
        self.assertEqual(selection["eligibleHexCellCount"], 8)
        self.assertTrue(selection["candidateInventoryExhausted"])
        self.assertFalse(selection["maximumMatchingComputed"])

    def test_scan_limit_is_explicit_and_not_empty_inventory_claim(self):
        with patch.object(pilot, "MAX_CANDIDATE_CHECKS", 1):
            pairs, selection = pilot.select_pairs(self.model)
        self.assertEqual(len(pairs), 1)
        self.assertFalse(selection["candidateInventoryExhausted"])
        self.assertEqual(selection["checkedCandidateCount"], 1)

    def test_only_hex_and_impeller_candidates_are_supported(self):
        model = deepcopy(self.model); model["cellTypes"][:] = 7
        pairs, selection = pilot.select_pairs(model)
        self.assertFalse(pairs); self.assertEqual(selection["eligibleHexCellCount"], 0)
        model["component"] = "volute"
        with self.assertRaises(pilot.AgglomerationIssue):
            pilot.select_pairs(model)

    def test_exact_one_face_union_retains_source_polygons_and_coordinates(self):
        pairs, _ = pilot.select_pairs(self.model, requested=1)
        before = deepcopy(self.model)
        derived, face_map, cell_map, removed = pilot.derive_model(self.model, pairs)
        self.assertEqual(derived["cellCount"], self.model["cellCount"] - 1)
        self.assertEqual(derived["faceCount"], self.model["faceCount"] - 1)
        self.assertEqual(derived["nodeCount"], self.model["nodeCount"])
        np.testing.assert_array_equal(derived["coordinates"], before["coordinates"])
        for face, source_face in enumerate(face_map):
            np.testing.assert_array_equal(pilot.polygon(derived, face), pilot.polygon(before, int(source_face) - 1))
        self.assertEqual(removed.tolist(), [pairs[0]["removedSourceFaceId"]])
        self.assertEqual(np.count_nonzero(np.bincount(cell_map) == 2), 1)
        self.assertEqual(np.count_nonzero(derived["cellTypes"] == 7), 1)
        self.assertAlmostEqual(pilot.original.audit_cells(derived)["totalCellVolumeAssumedM3"], 1000)
        for key in ("faceNodes", "c0", "c1", "cellTypes", "coordinates"):
            np.testing.assert_array_equal(self.model[key], before[key])

    def test_non_disjoint_pair_and_tampered_pair_evidence_rejected(self):
        pairs, _ = pilot.select_pairs(self.model, requested=1)
        with self.assertRaisesRegex(pilot.AgglomerationIssue, "disjoint"):
            pilot.derive_model(self.model, pairs * 2)
        pairs[0]["unionVolumeAssumedM3"] = 3
        with self.assertRaisesRegex(pilot.AgglomerationIssue, "current"):
            pilot.derive_model(self.model, pairs)

    def test_source_polygon_orientation_error_is_not_healed(self):
        model = deepcopy(self.model); model["faceNodes"][:4] = model["faceNodes"][:4][::-1]
        with self.assertRaises(pilot.original.ConversionIssue):
            pilot.original.audit_cells(model)

    def test_boundary_adjacent_pair_cannot_bypass_fixed_protection(self):
        model = self.model
        incidence, offsets = pilot.cell_faces(model)
        a, b = sorted((int(model["c0"][0]) - 1, int(model["c1"][0]) - 1))
        pair = pilot.check_pair(model, 0, a, b, incidence, offsets)
        with self.assertRaisesRegex(pilot.AgglomerationIssue, "protected"):
            pilot.derive_model(model, [pair])

    def test_additional_shared_vertex_contact_rejects_union(self):
        model = deepcopy(self.model); pairs, _ = pilot.select_pairs(model, requested=1)
        a, b = [v - 1 for v in pairs[0]["sourceCellIds"]]; face = pairs[0]["removedSourceFaceId"] - 1
        incidence, offsets = pilot.cell_faces(model)
        fa = incidence[offsets[a]:offsets[a + 1]]; fb = incidence[offsets[b]:offsets[b + 1]]
        va = {int(v) for f in fa for v in pilot.polygon(model, int(f))}
        vb = {int(v) for f in fb for v in pilot.polygon(model, int(f))}
        replacement = next(iter(va - vb)); old = next(iter(vb - va))
        for native_face in fb:
            lo, hi = model["faceOffsets"][native_face:native_face + 2]
            segment = model["faceNodes"][lo:hi]; segment[segment == old] = replacement
        with self.assertRaisesRegex(pilot.AgglomerationIssue, "AdditionalCellContacts"):
            pilot.check_pair(model, face, a, b, incidence, offsets)

    def test_serialized_maps_points_and_boundary_polygons_bind_actual_bytes(self):
        pairs, _ = pilot.select_pairs(self.model, requested=1)
        derived, faces, cells, removed = pilot.derive_model(self.model, pairs)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = pilot.write_component(root, derived, self.model, pilot.original.audit_cells(derived), faces, cells, removed, [0])
            maps = {Path(r["path"]).name: r for r in row["mappingFiles"]}
            actual = pilot.read_map(root, maps["sourceFaceIds"], derived["faceCount"])
            order, _, _, reverse, _ = pilot.original.face_order(derived)
            np.testing.assert_array_equal(actual, faces[order])
            np.testing.assert_array_equal(pilot.read_map(root, maps["sourceCellToDerivedCellIds"], len(cells)), cells)
            self.assertEqual(pilot.read_map(root, maps["removedSourceFaceIds"], 1).tolist(), removed.tolist())
            count, body = pilot.original.guards.base.native_list(root / row["casePath"] / "constant/polyMesh/points")
            points = np.asarray([[float(v) for v in part.split()] for part in re.findall(r"\(([^()]*)\)", body)])
            self.assertEqual(count, derived["nodeCount"]); np.testing.assert_array_equal(points, self.model["coordinates"])
            count, body = pilot.original.guards.base.native_list(root / row["casePath"] / "constant/polyMesh/faces")
            native_faces = [[int(v) for v in part.split()] for part in re.findall(r"\d+\(([^()]*)\)", body)]
            self.assertEqual(count, len(actual))
            for index, source_id in enumerate(actual):
                expected = list(map(int, pilot.polygon(self.model, int(source_id) - 1)))
                if reverse[order[index]]:
                    expected.reverse()
                self.assertEqual(native_faces[index], expected)
            for metadata in row["nativeFiles"] + row["mappingFiles"]:
                raw = (root / metadata["path"]).read_bytes()
                self.assertEqual(metadata["bytes"], len(raw)); self.assertEqual(metadata["sha256"], sha256(raw).hexdigest())
            self.assertEqual(row["patches"][0]["nFaces"], self.model["faceZones"][-1]["maximumId"] - self.model["faceZones"][-1]["minimumId"] + 1)


class PilotOutput(unittest.TestCase):
    def _run(self, root, n=12, mutate_post=False):
        models = {"impeller": grid(n), "volute": grid(1, "volute")}
        inputs = root / "inputs"; inputs.mkdir()
        for name in pilot.original.inventory.MESH_NAMES:
            (inputs / name).write_bytes(b"SYNTHETIC-TEST-ONLY")
        calls = [0]
        def verify(path, name):
            calls[0] += 1
            if mutate_post and calls[0] > 2:
                raise ValueError("Synthetic post-boundary source change")
            return pin(name.split(".")[0])
        with patch.dict(os.environ, {"RUNNER_TEMP": str(root)}), patch.object(pilot.original.inventory, "verify_file", side_effect=verify), \
                patch.object(pilot.original, "read_mesh", side_effect=lambda path, component: deepcopy(models[component])):
            report = pilot.agglomerate(inputs, root / "pilot")
        return report, models

    def test_complete_fixed32_pilot_has_no_native_or_solver_claim(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); report, models = self._run(root)
            self.assertTrue(report["volumeMeshDerived"]); self.assertEqual(report["selection"]["selectedPairCount"], 32)
            self.assertEqual(report["combinedCounts"]["cellCount"], 12 ** 3 + 1 - 32)
            for key in ("meshQualityCheckedBySolver", "qualifiedBaseline", "actualCfdRunVerified", "simulationExecuted", "nativeCoordinateUnitsVerified", "mainApplicationWrites"):
                self.assertIs(report[key], False)
            with patch.object(pilot.original, "SOURCE_COUNTS", {name: counts(model) for name, model in models.items()}):
                verified = pilot.verify_output(root / "pilot")
            self.assertEqual(verified["report"], report)
            self.assertEqual(verified["components"][0]["cellCount"], 12 ** 3 - 32)
            self.assertFalse((root / "pilot/native-conversion.json").exists())

    def test_insufficient_fixed32_writes_diagnostic_only(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); report, _ = self._run(root, 10)
            self.assertEqual(report["status"], "GeometryPilotEligiblePairsRequired")
            self.assertFalse(report["volumeMeshDerived"]); self.assertTrue(report["originalMeshesImmutable"])
            self.assertEqual(report["selection"]["selectedPairCount"], 4)
            self.assertEqual([path.name for path in (root / "pilot").iterdir()], [pilot.REPORT_NAME])
            with self.assertRaises(pilot.AgglomerationIssue):
                pilot.verify_output(root / "pilot")

    def test_failed_source_post_sha_does_not_promote_immutability(self):
        with TemporaryDirectory() as tmp:
            report, _ = self._run(Path(tmp), mutate_post=True)
            self.assertEqual(report["status"], "GeometryPilotNotDerived")
            self.assertFalse(report["originalMeshesImmutable"]); self.assertFalse(report["volumeMeshDerived"])

    def test_current_mapping_bytes_tampered_are_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); report, models = self._run(root)
            target = root / "pilot" / report["components"][0]["mappingFiles"][1]["path"]
            target.write_bytes(target.read_bytes() + b" ")
            with patch.object(pilot.original, "SOURCE_COUNTS", {name: counts(model) for name, model in models.items()}), self.assertRaisesRegex(pilot.AgglomerationIssue, "inventory"):
                pilot.verify_output(root / "pilot")

    def test_even_rehashed_membership_mutation_fails_complete_map_contract(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); report, models = self._run(root); component = report["components"][0]
            metadata = component["mappingFiles"][1]; target = root / "pilot" / metadata["path"]
            raw = target.read_bytes(); target.write_bytes(raw.replace(b"0\n", b"1\n", 1))
            metadata.update(bytes=target.stat().st_size, sha256=sha256(target.read_bytes()).hexdigest())
            (root / "pilot" / pilot.REPORT_NAME).write_bytes(pilot.original.inventory.bounded_json(report))
            with patch.object(pilot.original, "SOURCE_COUNTS", {name: counts(model) for name, model in models.items()}), self.assertRaises(pilot.AgglomerationIssue):
                pilot.verify_output(root / "pilot")

    def test_unknown_or_native_success_claim_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); report, models = self._run(root)
            report["actualCfdRunVerified"] = True
            (root / "pilot" / pilot.REPORT_NAME).write_bytes(pilot.original.inventory.bounded_json(report))
            with patch.object(pilot.original, "SOURCE_COUNTS", {name: counts(model) for name, model in models.items()}), self.assertRaisesRegex(pilot.AgglomerationIssue, "promote"):
                pilot.verify_output(root / "pilot")

    def test_unchecked_positive_label_cannot_override_actual_volume_identity(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); report, models = self._run(root)
            report["components"][0]["pairs"][0]["unionVolumeAssumedM3"] = 3
            (root / "pilot" / pilot.REPORT_NAME).write_bytes(pilot.original.inventory.bounded_json(report))
            with patch.object(pilot.original, "SOURCE_COUNTS", {name: counts(model) for name, model in models.items()}), self.assertRaisesRegex(pilot.AgglomerationIssue, "volume identity"):
                pilot.verify_output(root / "pilot")

    def test_relaxed_cell_closure_tolerance_or_false_native_source_claim_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); report, models = self._run(root)
            for key in ("relativeAreaClosureTolerance", "meshQualityCheckedBySolver"):
                changed = deepcopy(report)
                changed["sourceAudits"]["impeller"][key] = 1e-3 if key == "relativeAreaClosureTolerance" else True
                (root / "pilot" / pilot.REPORT_NAME).write_bytes(pilot.original.inventory.bounded_json(changed))
                with patch.object(pilot.original, "SOURCE_COUNTS", {name: counts(model) for name, model in models.items()}), self.assertRaises(pilot.AgglomerationIssue):
                    pilot.verify_output(root / "pilot")

    def test_invalid_output_location_fails_before_original_read(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); inputs = root / "inputs"; inputs.mkdir()
            with patch.dict(os.environ, {"RUNNER_TEMP": str(root)}), patch.object(pilot.original, "read_mesh") as reader:
                with self.assertRaises(ValueError):
                    pilot.agglomerate(inputs, root.parent / "outside-pilot")
                with self.assertRaisesRegex(pilot.AgglomerationIssue, "independent"):
                    pilot.agglomerate(inputs, inputs / "nested")
                reader.assert_not_called()

    def test_existing_destination_is_not_overwritten(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); inputs = root / "inputs"; inputs.mkdir(); out = root / "pilot"; out.mkdir()
            marker = out / "existing"; marker.write_bytes(b"KEEP")
            with patch.dict(os.environ, {"RUNNER_TEMP": str(root)}), self.assertRaises(pilot.AgglomerationIssue):
                pilot.agglomerate(inputs, out)
            self.assertEqual(marker.read_bytes(), b"KEEP")


if __name__ == "__main__":
    unittest.main()
