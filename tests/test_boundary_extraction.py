"""Synthetic topology/section contracts only; fixtures are not pump CFD results."""
from hashlib import sha256
import importlib.util
import os
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools" / "extract_frozen_rotor_boundaries.py"
spec = importlib.util.spec_from_file_location("reviewed_boundary_extraction", TOOL)
extractor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extractor)
try:
    import h5py
    import numpy as np
except (ImportError, OSError):
    h5py = np = None

POINTS = {1: (0., 0., 0.), 2: (1., 0., 0.), 3: (0., 1., 0.), 4: (0., 0., 1.)}
TETRA_FACES = [(1, 3, 2), (1, 2, 4), (2, 3, 4), (3, 1, 4)]


class PureBoundaryContracts(unittest.TestCase):
    def test_complete_global_section_map_ignores_section_number_order(self):
        rows = [{"path": "/coords/2", "minimumId": 3, "maximumId": 4, "recordCount": 2},
                {"path": "/coords/9", "minimumId": 1, "maximumId": 2, "recordCount": 2}]
        self.assertEqual([row["path"] for row in extractor.checked_range_sections(rows, 4)], ["/coords/9", "/coords/2"])

    def test_section_gap_overlap_count_and_incomplete_coverage_rejected(self):
        for lows, highs, counts, total in (((1, 4), (2, 5), (2, 2), 5), ((1, 2), (2, 4), (2, 3), 4),
                                         ((1, 3), (2, 4), (2, 1), 4), ((1,), (2,), (2,), 3)):
            with self.subTest(lows=lows):
                rows = [{"minimumId": low, "maximumId": high, "recordCount": count} for low, high, count in zip(lows, highs, counts)]
                with self.assertRaises(extractor.ExtractionIssue):
                    extractor.checked_range_sections(rows, total)

    def test_boundary_classification_uses_only_cell_adjacency(self):
        self.assertTrue(extractor.is_true_boundary(1, 0, 2))
        self.assertTrue(extractor.is_true_boundary(0, 2, 2))
        self.assertFalse(extractor.is_true_boundary(1, 2, 2))
        for pair in ((0, 0), (1, 1), (3, 0), (-1, 0), (True, 0)):
            with self.subTest(pair=pair), self.assertRaises(extractor.ExtractionIssue):
                extractor.is_true_boundary(*pair, 2)

    def test_closed_oriented_native_edges_are_narrower_than_mesh_validity(self):
        audit = extractor.audit_native_topology(TETRA_FACES)
        self.assertTrue(audit["closedEdgeIncidence"])
        self.assertTrue(audit["consistentNativeWinding"])
        self.assertEqual(audit["uniqueNativeEdgeCount"], 6)
        self.assertFalse(audit["outwardOrientationConventionVerified"])
        self.assertFalse(audit["geometricSelfIntersectionChecked"])
        self.assertFalse(audit["manifoldVertexLinksChecked"])

    def test_open_orientation_conflict_duplicates_and_nonmanifold_are_reported(self):
        opened = extractor.audit_native_topology(TETRA_FACES[:-1])
        self.assertEqual(opened["openEdgeCount"], 3)
        self.assertFalse(opened["closedEdgeIncidence"])
        changed = extractor.audit_native_topology([tuple(reversed(TETRA_FACES[0])), *TETRA_FACES[1:]])
        self.assertTrue(changed["closedEdgeIncidence"])
        self.assertEqual(changed["orientationConflictEdgeCount"], 3)
        self.assertFalse(changed["consistentNativeWinding"])
        duplicate = extractor.audit_native_topology([*TETRA_FACES, tuple(reversed(TETRA_FACES[0]))])
        self.assertEqual(duplicate["duplicateFaceCount"], 1)
        self.assertEqual(duplicate["nonManifoldEdgeCount"], 3)

    def test_repeated_nodes_degenerate_and_unsupported_projected_polygons_reject(self):
        with self.assertRaises(extractor.ExtractionIssue):
            extractor.audit_native_topology([(1, 1, 2)])
        for face, points in (((1, 1, 2), POINTS), ((1, 2, 3), {1: (0,0,0), 2: (1,0,0), 3: (2,0,0)}),
                             ((1,2,3,4), {1:(0,0,0),2:(1,1,0),3:(1,0,0),4:(0,1,0)}),
                             ((1,2,3,4,5), {1:(0,0,0),2:(2,0,0),3:(1,.5,0),4:(2,2,0),5:(0,2,0)})):
            with self.subTest(face=face), self.assertRaises(extractor.ExtractionIssue):
                extractor.face_geometry(face, points)

    def test_native_coordinates_nonplanarity_quantization_and_fan_preserved(self):
        points = {1:(0,0,0),2:(1,0,0),3:(1,1,.1),4:(0,1,0)}
        native, stl, audit = extractor.face_geometry((1,2,3,4), points)
        self.assertEqual(native, [(points[1],points[2],points[3]), (points[1],points[3],points[4])])
        self.assertEqual(len(stl), 2)
        self.assertTrue(audit["nonplanarAtRelative1e12"])
        self.assertGreater(audit["maximumCoordinateQuantizationErrorNativeUnits"], 0)
        self.assertEqual(audit["projectionAxis"], 2)

    def test_float32_overflow_or_collapsed_triangle_reject(self):
        with self.assertRaises(extractor.ExtractionIssue):
            extractor.face_geometry((1,2,3), {1:(1e39,0,0),2:(0,1e39,0),3:(0,0,1e39)})
        with self.assertRaisesRegex(extractor.ExtractionIssue, "float32"):
            extractor.face_geometry((1,2,3), {1:(1e20,1e20,0),2:(1e20+1e12,1e20,0),3:(1e20,1e20+1e12,0)})

    def _model(self, faces):
        zone = {"zoneId": 4, "name": "interface-original-tag", "minimumId": 1, "maximumId": len(faces), "inclusiveCount": len(faces),
            "faceCount": len(faces), "triangleCount": sum(len(face)-2 for face in faces), "c0ExteriorCount": 0,
            "c1ExteriorCount": len(faces), "internalFaceCount": 0, "faces": list(enumerate(faces, 1)),
            "zoneType": 3, "faceType": 3, "shadowZoneId": 0, "childZoneId": 0}
        return {"zones": [zone], "coordinates": POINTS, "nativeCounts": {"nodeCount": 4, "faceCount": len(faces), "cellCount": 1},
            "nativeTopology": {}, "sections": {}, "boundaryFaceCount": len(faces)}

    def test_binary_stl_header_count_hash_native_tags_and_signed_volume(self):
        report, files = extractor.prepare_surface(self._model(TETRA_FACES), "impeller.msh.h5")
        descriptor, raw = files[0]
        self.assertEqual(descriptor["name"], "impeller-zone-4.stl")
        self.assertEqual(descriptor["zoneName"], "interface-original-tag")
        self.assertEqual(raw[:80], extractor.STL_HEADER)
        self.assertEqual(struct.unpack("<I", raw[80:84])[0], 4)
        self.assertEqual(len(raw), 84+50*4)
        self.assertEqual(descriptor["sha256"], sha256(raw).hexdigest())
        self.assertAlmostEqual(report["signedEnclosedVolumeNativeUnitsCubed"], 1/6)
        self.assertEqual(set(report["signedVolumeEquationStages"][0]), {"input", "equation", "substitution", "result", "claim"})
        self.assertFalse(report["triangulationAudit"]["nodeWeldingPerformed"])
        self.assertFalse(report["triangulationAudit"]["topologyHealingPerformed"])
        for offset in range(84, len(raw), 50):
            self.assertEqual(extractor.TRIANGLE.unpack(raw[offset:offset+50])[-1], 0)

    def test_signed_volume_null_for_open_or_inconsistent_shell(self):
        for faces in (TETRA_FACES[:-1], [tuple(reversed(TETRA_FACES[0])), *TETRA_FACES[1:]]):
            with self.subTest(faces=faces):
                report, files = extractor.prepare_surface(self._model(faces), "volute.msh.h5")
                self.assertIsNone(report["signedEnclosedVolumeNativeUnitsCubed"])
                self.assertTrue(files)

    def test_output_new_inside_runner_temp_and_validation_precedes_download(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}), \
                patch.object(extractor.inventory, "download_mesh", side_effect=AssertionError("no download")):
            valid = Path(temp) / "nested" / "new-surfaces"
            self.assertEqual(extractor.output_directory(valid), valid)
            self.assertFalse(valid.parent.exists())
            with self.assertRaises(extractor.ExtractionIssue):
                extractor.output_directory(Path(temp))
            with self.assertRaises(extractor.inventory.InspectionIssue):
                extractor.run(Path(temp).parent / "outside-surfaces")

    def test_stl_byte_budget_rejects_before_output(self):
        with patch.object(extractor, "MAX_BUNDLE_BYTES", 100):
            with self.assertRaisesRegex(extractor.ExtractionIssue, "25 MiB"):
                extractor.prepare_surface(self._model(TETRA_FACES), "impeller.msh.h5")


@unittest.skipIf(h5py is None, "h5py unavailable; actual native reader contracts run on GitHub Ubuntu")
class NativeSectionContracts(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic-only.h5"
        self._create()

    def _topology(self, handle, category, ids, lows, highs, names):
        group = handle.require_group("meshes/1/"+category+"/zoneTopology")
        group.attrs["nZones"] = np.asarray([len(ids)], dtype="u8")
        for key, values in (("id", ids), ("minId", lows), ("maxId", highs)):
            group[key] = np.asarray(values, dtype="i8")
        group["name"] = np.asarray([(";".join(names)+";").encode()])
        return group

    def _range(self, obj, low, high):
        obj.attrs["minId"] = np.asarray([low], dtype="u8")
        obj.attrs["maxId"] = np.asarray([high], dtype="u8")

    def _create(self):
        with h5py.File(self.path, "w") as handle:
            self._topology(handle, "cells", [1], [1], [2], ["synthetic-fluid"])
            self._topology(handle, "nodes", [11,12], [1,3], [2,4], ["nodes-part-a","nodes-part-b"])
            zone = self._topology(handle, "faces", [21,22,23], [1,3,5], [2,4,5], ["inlet","interface-original","inlet-misleading-interior"])
            zone["faceType"] = [3,3,3]
            zone["zoneType"] = [3,3,2]
            for number, low, high in (("9",1,2),("2",3,4)):
                dataset = handle.require_group("meshes/1/nodes/coords").create_dataset(number, data=[POINTS[index] for index in range(low,high+1)])
                self._range(dataset, low, high)
            all_faces = [*TETRA_FACES,(1,2,3)]
            for category in ("nodes", "c0", "c1"):
                handle.require_group("meshes/1/faces/"+category).attrs["nSections"] = [2]
            for number, low, high in (("8",1,2),("3",3,5)):
                group = handle.require_group("meshes/1/faces/nodes/"+number)
                self._range(group,low,high)
                group["nnodes"] = np.asarray([3]*(high-low+1),dtype="i2")
                group["nodes"] = np.asarray([node for face in all_faces[low-1:high] for node in face],dtype="u4")
                for category, values in (("c0",[1,1,1,1,1]),("c1",[0,0,0,0,2])):
                    dataset = handle["meshes/1/faces/"+category].create_dataset(number,data=np.asarray(values[low-1:high],dtype="u4"))
                    self._range(dataset,low,high)

    def test_multiple_coordinate_and_face_sections_global_refs_and_no_input_write(self):
        before = sha256(self.path.read_bytes()).hexdigest()
        model = extractor.read_native_mesh(self.path)
        self.assertEqual(sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertEqual(model["coordinates"][4].tolist(), list(POINTS[4]))
        self.assertEqual(model["boundaryFaceCount"],4)
        self.assertEqual([zone["faceCount"] for zone in model["zones"]],[2,2,0])
        self.assertEqual(model["zones"][2]["internalFaceCount"],1)
        report, files = extractor.prepare_surface(model,"impeller.msh.h5")
        self.assertEqual([row["name"] for row,_ in files],["impeller-zone-21.stl","impeller-zone-22.stl"])
        self.assertAlmostEqual(report["signedEnclosedVolumeNativeUnitsCubed"],1/6)

    def test_both_native_exterior_sides_select_boundary_without_winding_guess(self):
        with h5py.File(self.path,"r+") as handle:
            handle["meshes/1/faces/c0/8"][:] = [0,0]
            handle["meshes/1/faces/c1/8"][:] = [1,1]
        model=extractor.read_native_mesh(self.path)
        self.assertEqual(model["zones"][0]["c0ExteriorCount"],2)
        self.assertEqual(model["zones"][0]["faces"][0][1],TETRA_FACES[0])

    def test_invalid_counts_ranges_connectivity_and_references_reject(self):
        mutations = [("meshes/1/nodes/coords/2","attribute",("minId",[4])),
            ("meshes/1/faces/c0/3","attribute",("minId",[2])),
            ("meshes/1/faces/nodes/8/nnodes","data",[4,3]),
            ("meshes/1/faces/nodes/8/nodes","data",[1,3,5,1,2,4]),
            ("meshes/1/faces/c0/8","data",[3,1]),
            ("meshes/1/faces/c0/8","data",[0,1]),
            ("meshes/1/faces/nodes","attribute",("nSections",[3]))]
        for path, kind, value in mutations:
            with self.subTest(path=path,value=value):
                self._create()
                with h5py.File(self.path,"r+") as handle:
                    if kind=="attribute": handle[path].attrs[value[0]]=value[1]
                    else: handle[path][:]=value
                with self.assertRaises(extractor.inventory.InspectionIssue):
                    extractor.read_native_mesh(self.path)

    def test_nonfinite_second_coordinate_section_reject(self):
        with h5py.File(self.path,"r+") as handle:
            handle["meshes/1/nodes/coords/2"][0,0]=float("nan")
        with self.assertRaisesRegex(extractor.ExtractionIssue,"finite"):
            extractor.read_native_mesh(self.path)

    def test_declared_connectivity_budget_rejects_before_flat_payload_read(self):
        read_names = []
        original_getitem = h5py.Dataset.__getitem__
        def tracked_getitem(dataset, key):
            read_names.append(dataset.name)
            return original_getitem(dataset, key)
        with mock.patch.object(extractor,"MAX_CONNECTIVITY_VALUES",5), mock.patch.object(h5py.Dataset,"__getitem__",tracked_getitem):
            with self.assertRaisesRegex(extractor.ExtractionIssue,"bounded per-face"):
                extractor.read_native_mesh(self.path)
        self.assertNotIn("/meshes/1/faces/nodes/8/nodes",read_names)

    def test_unsafe_link_is_rejected_by_shared_scanner(self):
        with h5py.File(self.path,"r+") as handle:
            handle["forbidden"] = h5py.ExternalLink("must-not-open.h5","/secret")
        with self.assertRaisesRegex(extractor.inventory.InspectionIssue,"links"):
            extractor.read_native_mesh(self.path)


if __name__ == "__main__":
    unittest.main()
