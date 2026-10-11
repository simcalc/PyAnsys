"""Synthetic surface contracts; fixture triangles are never CFD evidence."""
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools/frozen_rotor_surface_union.py"
spec = importlib.util.spec_from_file_location("surface_union", TOOL)
union = importlib.util.module_from_spec(spec)
spec.loader.exec_module(union)

POINTS = ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.), (0., 0., 1.))
INWARD = ((0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0))


def binary(vertices, *, normal=(0., 0., 1.), attribute=0):
    return union.source.STL_HEADER + struct.pack("<I", 1) + struct.pack("<12fH", *normal, *(x for p in vertices for x in p), attribute)


def fixture(root):
    directory = root / "surface-input"
    directory.mkdir()
    pins, files, meshes = {}, [], []
    for component, original in union.STL_PINS.items():
        pins[component], zones = [], []
        for i, (zone, name, kind, _, _, _) in enumerate(original):
            raw = binary([POINTS[index] for index in INWARD[i % 4]])
            pins[component].append((zone, name, kind, 1, 1, sha256(raw).hexdigest()))
            filename = f"{component}-zone-{zone}.stl"
            (directory / filename).write_bytes(raw)
            descriptor = {"name": filename, "bytes": len(raw), "sha256": sha256(raw).hexdigest(), "triangleCount": 1,
                          "format": "binary-stl", "sourceMesh": component + ".msh.h5", "zoneId": zone, "zoneName": name}
            files.append(descriptor)
            zones.append({"zoneId": zone, "name": name, "zoneType": kind, "faceCount": 1, "triangleCount": 1, "stlFile": descriptor})
        mesh = {key: [] for key in union.source.MESH_KEYS}
        source_name = component + ".msh.h5"
        size, digest = union.source.MESH_SOURCES[source_name]
        mesh.update(name=source_name, bytes=size, sha256=digest, sourceSha256AfterExtraction=digest,
                    sourceUrl=f"https://media.githubusercontent.com/media/ansys/example-data/{union.source.PIN}/pyfluent/examples/pump-volute/{source_name}",
                    boundaryZones=zones, signedEnclosedVolumeNativeUnitsCubed=-1 / 6)
        meshes.append(mesh)
    report = {key: None for key in union.source.REPORT_KEYS}
    report.update(schemaVersion=union.source.SCHEMA, files=files, meshes=meshes,
                  exampleDataRepository="https://github.com/ansys/example-data", exampleDataCommit=union.source.PIN,
                  githubRepository=union.source.REPOSITORY, githubRunId="12345", githubRunAttempt="1", githubSourceCommit="a" * 40,
                  extractedAt="2026-10-11T16:29:44.699374+00:00", h5pyVersion="3.16.0", hdf5Version="2.0.0",
                  coordinateUnits="UNVERIFIED", sourceFileBytes=sum(size for size, _ in union.source.MESH_SOURCES.values()),
                  derivedSurfaceBytes=sum(item["bytes"] for item in files))
    report.update({key: False for key in union.source.FALSE_FLAGS})
    report.update({key: True for key in union.source.TRUE_FLAGS})
    raw = union.encoded(report)
    (directory / "topology-report.json").write_bytes(raw)
    stack = ExitStack()
    stack.enter_context(patch.object(union, "STL_PINS", pins))
    stack.enter_context(patch.object(union, "SOURCE_REPORT_BYTES", len(raw)))
    stack.enter_context(patch.object(union, "SOURCE_REPORT_SHA256", sha256(raw).hexdigest()))
    stack.enter_context(patch.object(union, "SOURCE_TOPOLOGY_SHA256", union.topology_fingerprint(report)))
    return directory, report, stack


class SurfaceUnionTests(unittest.TestCase):
    def test_all_tags_and_ordered_stored_triangles_survive_roundtrip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, _, stack = fixture(root)
            with stack:
                before = {p.name: p.read_bytes() for p in inputs.iterdir()}
                output = root / "union"
                report = union.prepare(inputs, output, runner_temp=root)
                checked = union.verify_output(output, runner_temp=root)
                self.assertEqual(checked["report"], report)
                self.assertEqual({p.name: p.read_bytes() for p in inputs.iterdir()}, before)
                self.assertEqual(set(p.name for p in output.iterdir()), {union.REPORT, union.SOURCE_REPORT, "source-stl", "impeller-union.obj", "volute-union.obj"})
                self.assertEqual(len(list((output / "source-stl").iterdir())), 11)
                for row in checked["components"]:
                    obj = (output / row["surfaceFile"]["path"]).read_text()
                    self.assertEqual([line[2:] for line in obj.splitlines() if line.startswith("g ")], [p["name"] for p in row["patches"]])
                    self.assertEqual(sum(line.startswith("f ") for line in obj.splitlines()), row["triangleCount"])
                volute = report["components"][1]
                self.assertEqual(volute["vertexCount"], 4)
                self.assertAlmostEqual(volute["signedTriangleVolumeNativeUnitsCubed"], -1 / 6)
                self.assertTrue(volute["coordinateIndexAudit"]["closedIndexedEdgeIncidence"])
                self.assertTrue(volute["coordinateIndexAudit"]["consistentIndexedWinding"])
                self.assertEqual(report["coordinateUnits"], "UNVERIFIED")
                self.assertEqual(report["classroomMetresPerSourceCoordinateUnit"], 1)
                for flag in union.FALSE_FLAGS:
                    self.assertIs(report[flag], False)
                for flag in union.TRUE_FLAGS:
                    self.assertIs(report[flag], True)

    def test_production_source_pin_covers_exact_eleven_descriptors(self):
        rows = union.expected_stls()
        self.assertEqual(len(rows), 11)
        self.assertEqual(sum(row["triangleCount"] for row in rows), 296060)
        self.assertEqual(sum(row["bytes"] for row in rows), 14803924)
        self.assertEqual(union.source.PIN, "8ef505f6ef699eeec68a16001df91ba353342f40")

    def test_fresh_utc_and_current_run_provenance_accept_identical_technical_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, report, stack = fixture(root)
            with stack:
                before = (inputs / "topology-report.json").read_bytes()
                original_identity = union.topology_fingerprint(report)
                report.update(extractedAt="2026-10-12T01:23:45Z", githubRunId="38172357434",
                              githubRunAttempt="2", githubSourceCommit="b" * 40)
                # A changed timestamp, run identity and JSON spacing/order do
                # not alter the fully pinned technical document.
                raw = json.dumps(report, separators=(",", ":"), allow_nan=False).encode("ascii")
                self.assertNotEqual(raw, before)
                (inputs / "topology-report.json").write_bytes(raw)
                output = root / "union"
                result = union.prepare(inputs, output, runner_temp=root)
                self.assertEqual(union.topology_fingerprint(report), original_identity)
                self.assertEqual((output / union.SOURCE_REPORT).read_bytes(), raw)
                self.assertEqual(result["sourceTopologyReport"]["sha256"], sha256(raw).hexdigest())
                self.assertEqual(result["sourceTopologyReport"]["bytes"], len(raw))
                identity = result["sourceTopologySemanticIdentity"]
                self.assertEqual(identity["sha256"], original_identity)
                self.assertEqual(identity["excludedTopLevelFields"], list(union.TOPOLOGY_RUN_FIELDS))
                self.assertEqual(identity["reviewedReferenceReport"]["sha256"], sha256(before).hexdigest())
                self.assertEqual(union.verify_output(output, runner_temp=root)["report"], result)

    def test_any_substantive_nested_report_mutation_is_rejected(self):
        def mutate(inputs, report, mutation):
            mutation(report)
            (inputs / "topology-report.json").write_bytes(union.encoded(report))
        mutations = (
            lambda r: r["meshes"][0].update(nativeCounts={"cellCount": 7}),
            lambda r: r["meshes"][0].update(signedEnclosedVolumeNativeUnitsCubed=-.2),
            lambda r: r["meshes"][0]["boundaryZones"][0].update(faceCount=2),
            lambda r: r["meshes"][0].update(boundaryTopologyAudit={"closed": True}),
            lambda r: r.update(limits={"maximumFacesPerMesh": 1}),
            lambda r: r.update(derivationMethod="changed triangulation policy"),
            lambda r: r.update(claimBoundary="changed numerical claim"),
            lambda r: r.update(nativeCoordinateUnitsVerified=True),
            lambda r: r.update(githubRepository="other/PyAnsys"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.reject_inputs(lambda inputs, report: mutate(inputs, report, mutation))

    def test_declared_volatile_fields_still_require_supported_provenance(self):
        invalid = (
            {"extractedAt": None}, {"extractedAt": "2026-10-12"},
            {"extractedAt": "2026-10-12T01:23:45+07:00"},
            {"extractedAt": "2026-02-30T01:23:45+00:00"},
            {"h5pyVersion": "unknown"}, {"hdf5Version": "999.0.0"},
            {"h5pyVersion": "3.16.0", "hdf5Version": "1.14.6"},
            {"githubRunId": None}, {"githubRunId": "0"}, {"githubRunId": "1" * 21},
            {"githubRunAttempt": None}, {"githubRunAttempt": "0"},
            {"githubSourceCommit": None}, {"githubSourceCommit": "b" * 39},
            {"githubSourceCommit": "not-a-commit"},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                self.reject_inputs(lambda inputs, report: (inputs / "topology-report.json").write_bytes(union.encoded({**report, **changes})))

    def test_even_allowed_timestamp_change_requires_new_downstream_raw_byte_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, _, stack = fixture(root)
            with stack:
                output = root / "union"
                union.prepare(inputs, output, runner_temp=root)
                target = output / union.SOURCE_REPORT
                value = json.loads(target.read_bytes())
                value["extractedAt"] = "2026-10-12T01:23:45+00:00"
                target.write_bytes(union.encoded(value))
                with self.assertRaises(union.SurfaceIssue):
                    union.verify_output(output, runner_temp=root)

    def test_signed_zero_bits_are_distinct_index_entries(self):
        raw_files = {}
        original = union.STL_PINS["volute"]
        local = []
        for i, (zone, name, kind, _, _, _) in enumerate(original):
            vertices = [POINTS[index] for index in INWARD[i]]
            if i == 0:
                vertices[0] = (-0., 0., 0.)
            data = binary(vertices)
            raw_files[f"volute-zone-{zone}.stl"] = data
            local.append((zone, name, kind, 1, 1, sha256(data).hexdigest()))
        with patch.object(union, "STL_PINS", {"volute": local}):
            raw, row = union.convert_component("volute", raw_files)
        self.assertEqual(row["vertexCount"], 5)
        self.assertIn(b"v -0 0 0\n", raw)
        self.assertFalse(row["coordinateIndexAudit"]["closedIndexedEdgeIncidence"])

    def test_non_float32_decimals_roundtrip_to_exact_stored_bits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, _, stack = fixture(root)
            with stack:
                data = (inputs / "volute-zone-16.stl").read_bytes()
                # The conversion uses actual stored float32 values, not inferred decimals.
                points = ((.1, .2, .3), (.8, .2, .3), (.1, .9, .3))
                data = binary(points)
                raw_files = {p.name: p.read_bytes() for p in inputs.glob("volute*.stl")}
                raw_files["volute-zone-16.stl"] = data
                obj, _ = union.convert_component("volute", raw_files)
                lines = [line.split()[1:] for line in obj.decode().splitlines() if line.startswith("v ")]
                self.assertEqual(struct.pack("<3f", *(float(x) for x in lines[0])), data[96:108])

    def reject_inputs(self, mutation):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, report, stack = fixture(root)
            with stack:
                mutation(inputs, report)
                output = root / "union"
                with self.assertRaises((union.SurfaceIssue, union.source.PackageIssue, OSError)):
                    union.prepare(inputs, output, runner_temp=root)
                self.assertFalse(output.exists())

    def test_changed_source_stl_is_rejected_before_output(self):
        self.reject_inputs(lambda inputs, _: (inputs / "volute-zone-16.stl").write_bytes(binary((POINTS[0], POINTS[1], POINTS[3]))))

    def test_report_metadata_or_unknown_input_file_cannot_be_copied(self):
        self.reject_inputs(lambda inputs, report: (inputs / "topology-report.json").write_bytes(union.encoded({**report, "secret": "synthetic forbidden"})))
        self.reject_inputs(lambda inputs, _: (inputs / ".env").write_text("synthetic forbidden"))
        self.reject_inputs(lambda inputs, _: (inputs / "other-dir").mkdir())

    def test_nan_inf_attribute_and_collapsed_triangle_are_rejected(self):
        original = union.STL_PINS["volute"]
        variants = [binary((POINTS[0], POINTS[0], POINTS[1])), binary(((0., 0., 0.), (1., 0., 0.), (2., 0., 0.))),
                    binary((POINTS[0], POINTS[1], POINTS[2]), normal=(math.nan, 0., 1.)),
                    binary((POINTS[0], POINTS[1], POINTS[2]), normal=(math.inf, 0., 1.)),
                    binary((POINTS[0], POINTS[1], POINTS[2]), attribute=1)]
        for data in variants:
            raw_files = {f"volute-zone-{z}.stl": data for z, _, _, _, _, _ in original}
            pins = [(z, n, k, 1, 1, sha256(data).hexdigest()) for z, n, k, _, _, _ in original]
            with self.subTest(data=data[-2:]), patch.object(union, "STL_PINS", {"volute": pins}):
                with self.assertRaises(union.SurfaceIssue):
                    union.convert_component("volute", raw_files)

    def test_duplicate_and_orientation_findings_are_reported_without_healing(self):
        data = binary((POINTS[0], POINTS[1], POINTS[2]))
        pins = [(z, n, k, 1, 1, sha256(data).hexdigest()) for z, n, k, _, _, _ in union.STL_PINS["volute"]]
        files = {f"volute-zone-{z}.stl": data for z, _, _, _, _, _ in pins}
        with patch.object(union, "STL_PINS", {"volute": pins}):
            _, row = union.convert_component("volute", files)
        self.assertEqual(row["triangleCount"], 4)
        self.assertEqual(row["coordinateIndexAudit"]["duplicateIndexedTriangleCount"], 3)
        self.assertEqual(row["coordinateIndexAudit"]["nonManifoldEdgeCount"], 3)
        self.assertFalse(row["coordinateIndexAudit"]["consistentIndexedWinding"])

    def test_output_stl_obj_report_or_extra_file_mutations_are_rejected(self):
        for kind in ("obj", "stl", "report", "unknown", "tag", "winding"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                inputs, _, stack = fixture(root)
                with stack:
                    output = root / "union"
                    union.prepare(inputs, output, runner_temp=root)
                    if kind in {"obj", "tag", "winding"}:
                        path = output / "volute-union.obj"
                        raw = path.read_bytes()
                        if kind == "obj":
                            raw += b"# arbitrary hidden payload\n"
                        elif kind == "tag":
                            raw = raw.replace(b"g volute-wall", b"g renamed-wall", 1)
                        else:
                            raw = raw.replace(b"f 1 2 3\n", b"f 1 3 2\n", 1)
                        path.write_bytes(raw)
                    elif kind == "stl":
                        (output / "source-stl/volute-zone-16.stl").write_bytes(binary((POINTS[0], POINTS[1], POINTS[3])))
                    elif kind == "report":
                        path = output / union.REPORT
                        value = json.loads(path.read_bytes())
                        value["nativeSurfaceAccepted"] = True
                        path.write_bytes(union.encoded(value))
                    else:
                        (output / "token.txt").write_text("synthetic forbidden")
                    with self.assertRaises((union.SurfaceIssue, union.source.PackageIssue)):
                        union.verify_output(output, runner_temp=root)

    def test_fresh_runner_temp_and_independent_output_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, _, stack = fixture(root)
            with stack:
                for output in (root, inputs, inputs / "nested", root.parent / "escape"):
                    with self.subTest(output=output), self.assertRaises((union.SurfaceIssue, union.source.PackageIssue)):
                        union.prepare(inputs, output, runner_temp=root)
                existing = root / "existing"
                existing.mkdir()
                with self.assertRaises(union.SurfaceIssue):
                    union.prepare(inputs, existing, runner_temp=root)
                with patch.dict(os.environ, {}, clear=True), self.assertRaises(union.SurfaceIssue):
                    union.prepare(inputs, root / "missing-runner")

    def test_symbolic_inputs_or_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, _, stack = fixture(root)
            with stack:
                linked = root / "linked"
                try:
                    linked.symlink_to(inputs, target_is_directory=True)
                except OSError:
                    self.skipTest("This Windows account does not permit symlink creation.")
                with self.assertRaises(union.source.PackageIssue):
                    union.prepare(linked, root / "out", runner_temp=root)

    def test_combined_obj_budget_checked_before_any_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, _, stack = fixture(root)
            with stack, patch.object(union, "MAX_OBJ_BYTES", 1):
                output = root / "union"
                with self.assertRaises(union.SurfaceIssue):
                    union.prepare(inputs, output, runner_temp=root)
                self.assertFalse(output.exists())

    def test_failed_write_cleans_only_the_fresh_attempt_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, _, stack = fixture(root)
            with stack:
                output = root / "union"
                before = {p.name: p.read_bytes() for p in inputs.iterdir()}
                original_open = Path.open
                def failing(path, mode="r", *args, **kwargs):
                    if path == output / "volute-union.obj" and mode == "xb":
                        path.write_bytes(b"partial synthetic output")
                        raise OSError("Synthetic disk interruption")
                    return original_open(path, mode, *args, **kwargs)
                with patch.object(Path, "open", failing), self.assertRaises(OSError):
                    union.prepare(inputs, output, runner_temp=root)
                self.assertFalse(output.exists())
                self.assertEqual({p.name: p.read_bytes() for p in inputs.iterdir()}, before)

    def test_duplicate_json_and_numeric_overflow_fail_closed(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(union.SurfaceIssue):
                union.read_json(raw)


if __name__ == "__main__":
    unittest.main()
