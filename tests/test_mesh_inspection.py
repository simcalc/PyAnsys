"""Bounded native mesh inspection contracts; synthetic data is never CFD evidence."""
from hashlib import sha256
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools" / "inspect_frozen_rotor_mesh.py"
spec = importlib.util.spec_from_file_location("reviewed_mesh_inspection", TOOL)
inspection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspection)
try:
    import h5py
    import numpy as np
except (ImportError, OSError):
    h5py = np = None


class TransferContracts(unittest.TestCase):
    def test_exact_immutable_https_allowlist(self):
        url = inspection.mesh_url("impeller.msh.h5")
        inspection.validate_url(url)
        for bad in (url.replace("https:", "http:"), url + "?token=x", url + "#x",
                    url.replace("media.githubusercontent.com", "example.org"),
                    url.replace("https://", "https://user@"), url.replace(inspection.PIN, "main")):
            with self.subTest(url=bad), self.assertRaises(inspection.InspectionIssue):
                inspection.validate_url(bad)
        with self.assertRaises(inspection.InspectionIssue):
            inspection.mesh_url("setup.cas.h5")

    def test_checksum_checked_before_any_hdf5_import_or_open(self):
        raw = inspection.SIGNATURE + b"synthetic-not-a-mesh"
        with TemporaryDirectory() as temp:
            path = Path(temp) / "impeller.msh.h5"
            path.write_bytes(raw)
            with patch.dict(inspection.FILES, {path.name: (len(raw), "0" * 64)}), \
                    patch.object(inspection, "inspect_hdf5", side_effect=AssertionError("must not open")):
                with self.assertRaisesRegex(inspection.InspectionIssue, "SHA-256"):
                    inspection.inspect_verified_file(path, path.name)

    def test_lfs_pointer_is_not_a_native_mesh(self):
        raw = b"version https://git-lfs.github.com/spec/v1\n"
        with TemporaryDirectory() as temp:
            path = Path(temp) / "impeller.msh.h5"
            path.write_bytes(raw)
            with patch.dict(inspection.FILES, {path.name: (len(raw), sha256(raw).hexdigest())}):
                with self.assertRaisesRegex(inspection.InspectionIssue, "LFS"):
                    inspection.verify_file(path, path.name)

    def test_bounded_json_and_finite_values(self):
        with self.assertRaises(inspection.InspectionIssue):
            inspection.bounded_json({"oversized": "x" * inspection.MAX_REPORT_BYTES})
        with self.assertRaises(ValueError):
            inspection.bounded_json({"invalid": float("nan")})
        self.assertEqual(inspection._json_value([1, 2.0, b"native"]), [1, 2.0, "native"])

    def test_nested_runner_output_and_rejected_outside_or_existing(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
            root = Path(temp)
            path = root / "mesh-review" / "mesh-inspection.json"
            self.assertEqual(inspection.output_path(path), path)
            self.assertFalse(path.parent.exists())
            for bad in (root.parent / "outside.json", root / "report.txt"):
                with self.assertRaises(inspection.InspectionIssue):
                    inspection.output_path(bad)
            existing = root / "existing.json"
            existing.write_text("{}", encoding="utf-8")
            with self.assertRaises(inspection.InspectionIssue):
                inspection.output_path(existing)

    def test_symlink_output_is_rejected(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
            root = Path(temp)
            directory = root / "real"
            directory.mkdir()
            link = root / "link"
            try:
                link.symlink_to(directory, target_is_directory=True)
            except OSError:
                self.skipTest("Creating a symlink is unavailable in this test environment")
            with self.assertRaises(inspection.InspectionIssue):
                inspection.output_path(link / "report.json")

    def _transfer(self, directory, body, expected, declared=None):
        class Response(BytesIO):
            headers = {} if declared is None else {"Content-Length": str(declared)}

            def geturl(self):
                return inspection.mesh_url("impeller.msh.h5")

        with patch.dict(inspection.FILES, {"impeller.msh.h5": expected}), \
                patch.object(inspection.urllib.request, "build_opener", return_value=SimpleNamespace(open=lambda *a, **k: Response(body))):
            return inspection.download_mesh(directory, "impeller.msh.h5")

    def test_exact_download_and_existing_pinned_file(self):
        raw = inspection.SIGNATURE + b"synthetic"
        pin = (len(raw), sha256(raw).hexdigest())
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            path = self._transfer(directory, raw, pin, len(raw))
            self.assertEqual(path.read_bytes(), raw)
            with patch.dict(inspection.FILES, {path.name: pin}), \
                    patch.object(inspection.urllib.request, "build_opener", side_effect=AssertionError("must reuse")):
                self.assertEqual(inspection.download_mesh(directory, path.name), path)
            self.assertFalse((directory / (path.name + ".part")).exists())

    def test_download_size_and_hash_reject_without_partial_residue(self):
        raw = inspection.SIGNATURE + b"synthetic"
        for body, pin, declared in ((raw + b"excess", (len(raw), sha256(raw).hexdigest()), None),
                                    (raw, (len(raw), "0" * 64), None),
                                    (raw, (len(raw), sha256(raw).hexdigest()), len(raw) + 1)):
            with self.subTest(declared=declared, pin=pin), TemporaryDirectory() as temp:
                directory = Path(temp)
                with self.assertRaises(inspection.InspectionIssue):
                    self._transfer(directory, body, pin, declared)
                self.assertEqual(list(directory.iterdir()), [])

    def test_invalid_destination_has_no_download_or_directory_creation(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}), \
                patch.object(inspection, "download_mesh", side_effect=AssertionError("must not transfer")):
            outside = Path(temp).parent / "outside" / "report.json"
            with self.assertRaises(inspection.InspectionIssue):
                inspection.run(outside)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_writer_has_separate_false_solver_claims_and_no_environment_dump(self):
        runtime = SimpleNamespace(__version__="test-only", version=SimpleNamespace(hdf5_version="test-only"))
        def fake_inventory(path, name):
            return {"name": name, "counts": {"cellCount": None, "faceCount": None, "nodeCount": None}}
        with TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp, "PRIVATE_TOKEN": "must-never-appear"}), \
                patch.dict("sys.modules", {"h5py": runtime}), \
                patch.object(inspection, "download_mesh", side_effect=lambda directory, name: directory / name), \
                patch.object(inspection, "inspect_verified_file", side_effect=fake_inventory):
            output = Path(temp) / "mesh-review" / "mesh-inspection.json"
            summary = inspection.run(output)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(summary["reportSha256"], sha256(output.read_bytes()).hexdigest())
            self.assertLessEqual(summary["reportBytes"], inspection.MAX_REPORT_BYTES)
            self.assertEqual(report["schemaVersion"], inspection.SCHEMA)
            self.assertTrue(report["readOnlyInspection"])
            for field in ("nativeCoordinateUnitsVerified", "meshQualityCheckedBySolver", "nativeCaseGenerated",
                          "fluentSessionConnected", "licenseVerified", "actualCfdRunVerified", "simulationExecuted", "mainApplicationWrites"):
                self.assertIs(report[field], False)
            self.assertNotIn("must-never-appear", output.read_text(encoding="utf-8"))


@unittest.skipIf(h5py is None, "h5py unavailable; run actual HDF5 tests on the isolated GitHub Ubuntu worker")
class NativeMetadataContracts(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic-test-only.h5"

    def create_native_structure(self, minima=(1, 4), maxima=(3, 5), rows=6):
        with h5py.File(self.path, "w") as handle:
            for category, lows, highs in (("cells", minima, maxima), ("faces", (1,), (7,)), ("nodes", (1,), (rows,))):
                group = handle.require_group("meshes/1/" + category + "/zoneTopology")
                group["id"] = np.arange(1, len(lows) + 1, dtype="i8")
                group["minId"] = np.asarray(lows, dtype="i8")
                group["maxId"] = np.asarray(highs, dtype="i8")
                group["name"] = np.asarray([(";".join("zone-" + str(i) for i in range(len(lows))) + ";").encode()])
            values = np.tile([1.0, 2.0, 3.0], (rows, 1))
            values[-1] = [-8.0, 17.0, 0.0]
            handle.require_group("meshes/1/nodes/coords")["0"] = values
            handle.attrs["format_note"] = "Synthetic contract fixture only"

    def test_stored_ranges_raw_metadata_chunked_bounds_and_read_only(self):
        self.create_native_structure(rows=4097)
        before = sha256(self.path.read_bytes()).hexdigest()
        report = inspection.inspect_hdf5(self.path)
        self.assertEqual(sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertEqual(report["counts"], {"cellCount": 5, "faceCount": 7, "nodeCount": 4097})
        cells = report["topology"]["cells"]
        self.assertEqual(cells["countStatus"], "ConfirmedFromStoredRanges")
        self.assertEqual(cells["rawDatasets"]["minId"]["rawValues"], [1, 4])
        self.assertEqual([zone["name"] for zone in cells["zones"]], ["zone-0", "zone-1"])
        self.assertEqual(report["coordinateBounds"]["minimum"], [-8.0, 2.0, 0.0])
        self.assertEqual(report["coordinateBounds"]["maximum"], [1.0, 17.0, 3.0])
        self.assertEqual(report["coordinateBounds"]["unit"], "native-unverified")
        self.assertFalse(report["simulationExecuted"])
        self.assertFalse(report["meshQualityCheckedBySolver"])

    def test_gap_and_overlap_never_produce_confirmed_counts(self):
        for minima, maxima in (((1, 5), (3, 6)), ((1, 3), (3, 6)), ((2, 5), (4, 6))):
            with self.subTest(minima=minima):
                self.create_native_structure(minima, maxima)
                report = inspection.inspect_hdf5(self.path)
                self.assertIsNone(report["counts"]["cellCount"])
                self.assertEqual(report["topology"]["cells"]["rawDatasets"]["minId"]["rawValues"], list(minima))

    def test_missing_and_null_topology_is_explicitly_unconfirmed(self):
        with h5py.File(self.path, "w") as handle:
            handle.create_dataset("null", dtype="f8")
        report = inspection.inspect_hdf5(self.path)
        self.assertEqual(report["counts"], {"cellCount": None, "faceCount": None, "nodeCount": None})
        self.assertEqual(report["objects"][1]["shape"], None)

    def test_soft_and_external_links_are_rejected_before_payload_reads(self):
        for link in (h5py.SoftLink("/does-not-exist"), h5py.ExternalLink("not-opened.h5", "/secret")):
            with self.subTest(link=link):
                with h5py.File(self.path, "w") as handle:
                    handle["forbidden"] = link
                with self.assertRaisesRegex(inspection.InspectionIssue, "links"):
                    inspection.inspect_hdf5(self.path)

    def test_external_storage_is_rejected(self):
        with h5py.File(self.path, "w") as handle:
            handle.create_dataset("external", (1,), dtype="f8", external=[("not-read.bin", 0, h5py.h5f.UNLIMITED)])
        with self.assertRaisesRegex(inspection.InspectionIssue, "External-storage"):
            inspection.inspect_hdf5(self.path)

    def test_virtual_dataset_is_rejected(self):
        layout = h5py.VirtualLayout(shape=(1,), dtype="f8")
        layout[:] = h5py.VirtualSource("not-opened.h5", "source", shape=(1,))
        with h5py.File(self.path, "w", libver="latest") as handle:
            handle.create_virtual_dataset("virtual", layout)
        with self.assertRaisesRegex(inspection.InspectionIssue, "virtual"):
            inspection.inspect_hdf5(self.path)

    def test_tree_object_and_depth_limits(self):
        with h5py.File(self.path, "w") as handle:
            for index in range(inspection.MAX_HDF5_OBJECTS):
                handle.create_group("g" + str(index))
        with self.assertRaisesRegex(inspection.InspectionIssue, "bounded"):
            inspection.inspect_hdf5(self.path)
        with h5py.File(self.path, "w") as handle:
            handle.require_group("/".join(["g"] * (inspection.MAX_HDF5_DEPTH + 1)))
        with self.assertRaisesRegex(inspection.InspectionIssue, "bounded"):
            inspection.inspect_hdf5(self.path)

    def test_nonfinite_coordinates_are_rejected(self):
        self.create_native_structure()
        with h5py.File(self.path, "r+") as handle:
            handle["meshes/1/nodes/coords/0"][0, 0] = float("nan")
        with self.assertRaisesRegex(inspection.InspectionIssue, "Nonfinite"):
            inspection.inspect_hdf5(self.path)

    def test_unknown_small_topology_preserved_without_false_count(self):
        with h5py.File(self.path, "w") as handle:
            group = handle.require_group("meshes/1/cells/zoneTopology")
            group["unknown_range_layout"] = [2, 7, 9]
            group["unknown_range_layout"].attrs["kind"] = "unconfirmed native field"
        report = inspection.inspect_hdf5(self.path)
        self.assertIsNone(report["counts"]["cellCount"])
        self.assertEqual(report["topology"]["cells"]["rawDatasets"]["unknown_range_layout"]["rawValues"], [2, 7, 9])


if __name__ == "__main__":
    unittest.main()
