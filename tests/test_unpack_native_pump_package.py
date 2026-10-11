"""Synthetic package/native-binding fixtures only; no solver was executed."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED, ZipInfo

BASE = Path(__file__).resolve().parents[1]
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

unpack = module("unpack_native_fixture_tests", BASE / "tools/unpack_native_pump_package.py")
fixture_module = module("unpack_native_parser_fixture", BASE / "tests/test_frozen_native_reduction.py")
native = fixture_module.native
package = unpack.package
package_fixtures = module("original_package_transport_fixture", BASE / "tests/test_native_pump_package.py")


class UnpackNativeContracts(unittest.TestCase):
    def test_original_transfer_schema_and_polygon_maps_reach_independent_review(self):
        # Transport-only synthetic checksum fixture. Native engineering review
        # is explicitly mocked, never interpreted as actual CFD qualification.
        with tempfile.TemporaryDirectory() as temp:
            parent=Path(temp);builder=package_fixtures.NativePackageContracts()
            source=builder.original_fixture(parent);builder.preliminary(source)
            builder.build(source,parent)
            target=parent/'original-transfer'
            with patch.object(unpack.phase,'verify_emitted_baseline') as review:
                result=self.unpack(source,target,parent)
            review.assert_called_once()
            self.assertTrue(result['preliminaryBaselineQualified'])
            self.assertFalse(result['physicalValidationVerified'])
            self.assertEqual((target/'volute-case/sourceFaceIds').read_bytes(),(source/'volute-case/sourceFaceIds').read_bytes())
            self.assertFalse((target/'remesh-report.json').exists())

    def fixture(self, parent):
        source = parent / "source-execution"
        source.mkdir()
        builder = fixture_module.NativeReductionContracts()
        builder.fixture(source)
        for name in ("points", "faces", "owner", "neighbour", "boundary", "cellZones"):
            builder.write(source, "impeller-case/constant/polyMesh/" + name,
                          "FoamFile {format ascii; class syntheticFixture; object " + name + ";}\n2 (0 1)\n")
        with patch.object(native, "binary_identity"):
            bound = native.build_manifest(source)
        builder.write_json(source, "native-input-manifest.json", bound["manifest"])
        builder.write_json(source, "classroom-study.json", bound["study"])
        execution = json.loads((source / "openfoam-execution.json").read_text())
        execution.update(qualifiedBaseline=False, syntheticFixture=True, researchEvidence=False)
        builder.write_json(source, "openfoam-execution.json", execution)
        with patch.object(native, "binary_identity"):
            reduction = native.reduce_native_baseline(source)
        self.assertTrue(reduction["preliminaryBaselineQualified"], reduction["diagnostics"])
        builder.write_json(source, "native-baseline-reduction.json", reduction)
        with patch.dict(os.environ, {"RUNNER_TEMP": str(parent)}):
            package.build_package(source)
        return source, parent / "transferred-execution"

    def unpack(self, source, target, parent):
        with patch.dict(os.environ, {"RUNNER_TEMP": str(parent), "GITHUB_REPOSITORY": "simcalc/PyAnsys"}):
            return unpack.unpack_package(source / package.ARCHIVE, source / package.MANIFEST, target)

    def manifest(self, source):
        return json.loads((source / package.MANIFEST).read_text())

    def save_manifest(self, source, value):
        (source / package.MANIFEST).write_bytes(package.canonical(value))

    def rewrite_zip(self, source, change):
        path = source / package.ARCHIVE
        with ZipFile(path) as bundle:
            entries = [(info, bundle.read(info)) for info in bundle.infolist()]
        with ZipFile(path, "w", ZIP_DEFLATED) as bundle:
            for info, raw in change(entries):
                bundle.writestr(info, raw)
        value = self.manifest(source)
        value["archive"].update(bytes=path.stat().st_size, sha256=sha256(path.read_bytes()).hexdigest())
        self.save_manifest(source, value)

    def test_verified_transfer_preserves_every_raw_case_and_evidence_byte(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            source, target = self.fixture(parent)
            result = self.unpack(source, target, parent)
            manifest = self.manifest(source)
            for row in manifest["files"]:
                raw = (target / row["path"]).read_bytes()
                self.assertEqual(raw, (source / row["path"]).read_bytes())
                self.assertEqual(sha256(raw).hexdigest(), row["sha256"])
            self.assertEqual(result["executionDirectory"], str(target.resolve()))
            self.assertTrue(result["preliminaryBaselineQualified"])  # synthetic upstream reduction only
            self.assertFalse(result["rawQualifiedBaseline"])
            self.assertFalse(result["restartVerified"])
            self.assertFalse(result["physicalValidationVerified"])
            self.assertFalse((target / package.ARCHIVE).exists())
            self.assertFalse((target / package.MANIFEST).exists())
            self.assertTrue((source / package.ARCHIVE).is_file())

    def test_stale_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            source, target = self.fixture(parent)
            target.mkdir()
            (target / "owner-evidence.txt").write_text("preserve")
            with self.assertRaisesRegex(unpack.UnpackIssue, "fresh"):
                self.unpack(source, target, parent)
            self.assertEqual((target / "owner-evidence.txt").read_text(), "preserve")

    def test_archive_flag_alone_and_unqualified_transfer_are_rejected_before_persistence(self):
        for key in ("actualCfdRunVerified", "simulationExecuted", "preliminaryBaselineQualified"):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                source, target = self.fixture(parent)
                value = self.manifest(source); value[key] = False
                self.save_manifest(source, value)
                with self.subTest(key=key), self.assertRaisesRegex(unpack.UnpackIssue, "archive alone"):
                    self.unpack(source, target, parent)
                self.assertFalse(target.exists())

    def test_zip_and_each_uncompressed_member_need_exact_size_and_checksum(self):
        for kind in ("zip", "member", "bytes", "missing", "unexpected", "duplicate"):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                source, target = self.fixture(parent)
                if kind == "zip":
                    with (source / package.ARCHIVE).open("ab") as stream:
                        stream.write(b"changed zip")
                elif kind == "member":
                    self.rewrite_zip(source, lambda entries: [(info, raw + b"changed") if info.filename == "logs/00-gmshToFoam.log" else (info, raw) for info, raw in entries])
                elif kind == "bytes":
                    value = self.manifest(source); value["files"][0]["bytes"] += 1
                    self.save_manifest(source, value)
                elif kind == "missing":
                    self.rewrite_zip(source, lambda entries: entries[:-1])
                elif kind == "unexpected":
                    self.rewrite_zip(source, lambda entries: entries + [(ZipInfo("unexpected.txt"), b"arbitrary bytes")])
                else:
                    with self.assertWarns(UserWarning):
                        self.rewrite_zip(source, lambda entries: entries + [entries[0]])
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    self.unpack(source, target, parent)
                self.assertFalse(target.exists())

    def test_paths_roles_links_executables_and_credentials_are_rejected(self):
        for name in ("../outside", "/outside", "C:/outside", "impeller-case/../outside", ".env.local", "impeller-case/system/run.py", "volute-case/0/p"):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                source, target = self.fixture(parent)
                value = self.manifest(source); value["files"][0]["path"] = name
                self.save_manifest(source, value)
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.unpack(source, target, parent)
                self.assertFalse(target.exists())
        for mode, content in ((stat.S_IFLNK | 0o644, None), (stat.S_IFREG | 0o755, None), (None, b"\x7fELFbinary"),
                              (None, b"Authorization: Bearer never-publish-example")):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                source, target = self.fixture(parent)
                def change(entries):
                    for info, raw in entries:
                        if info.filename == "logs/00-gmshToFoam.log":
                            if mode:
                                info.external_attr = mode << 16
                            if content:
                                raw = content
                        yield info, raw
                self.rewrite_zip(source, change)
                if content:
                    value = self.manifest(source)
                    for row in value["files"]:
                        if row["path"] == "logs/00-gmshToFoam.log":
                            row.update(bytes=len(content), sha256=sha256(content).hexdigest())
                    value["archivedBytes"] = sum(row["bytes"] for row in value["files"])
                    value["inputWorkspaceBytes"] = value["archivedBytes"]
                    self.save_manifest(source, value)
                with self.subTest(mode=mode, content=content), self.assertRaises(ValueError):
                    self.unpack(source, target, parent)
                self.assertFalse(target.exists())

    def test_numerical_protocol_and_bound_native_evidence_are_reverified_after_transfer(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            source, target = self.fixture(parent)
            # Alter only a structural study value while maintaining archive/member
            # integrity: the independent numerical binding gate must still reject.
            def change(entries):
                for info, raw in entries:
                    if info.filename == "native-baseline-reduction.json":
                        value = json.loads(raw)
                        value["study"]["input"]["speedRpm"] = 1200
                        raw = package.canonical(value)
                    yield info, raw
            self.rewrite_zip(source, change)
            value = self.manifest(source)
            with ZipFile(source / package.ARCHIVE) as bundle:
                for row in value["files"]:
                    raw = bundle.read(row["path"])
                    row.update(bytes=len(raw), sha256=sha256(raw).hexdigest())
            value["archivedBytes"] = sum(row["bytes"] for row in value["files"])
            value["inputWorkspaceBytes"] = value["archivedBytes"]
            self.save_manifest(source, value)
            with self.assertRaises(ValueError):
                self.unpack(source, target, parent)
            self.assertFalse(target.exists())
            self.assertTrue((source / package.ARCHIVE).is_file())

    def test_resource_bombs_schema_and_json_are_rejected(self):
        for kind in ("file", "workspace", "zip", "entries", "ratio", "nan", "duplicate-json", "wrong-pin", "unknown-key"):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                source, target = self.fixture(parent)
                value = self.manifest(source)
                patcher = None
                if kind == "file": value["files"][0]["bytes"] = package.MAX_FILE + 1
                elif kind == "workspace": value["archivedBytes"] = package.MAX_WORKSPACE + 1
                elif kind == "zip": value["archive"]["bytes"] = package.MAX_ZIP + 1
                elif kind == "entries": value["files"] *= 256
                elif kind == "ratio": patcher = patch.object(unpack, "MAX_RATIO", 1)
                elif kind == "wrong-pin": value["exampleDataCommit"] = "0" * 40
                elif kind == "unknown-key": value["executeScript"] = "forbidden"
                self.save_manifest(source, value)
                if kind == "nan": (source / package.MANIFEST).write_text('{"value":NaN}')
                elif kind == "duplicate-json": (source / package.MANIFEST).write_text('{"value":1,"value":2}')
                if patcher: patcher.start()
                try:
                    with self.subTest(kind=kind), self.assertRaises(ValueError):
                        self.unpack(source, target, parent)
                finally:
                    if patcher: patcher.stop()
                self.assertFalse(target.exists())

    def test_sources_and_targets_need_own_public_runner_and_no_parent_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            source, target = self.fixture(parent)
            with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(unpack.UnpackIssue, "RUNNER_TEMP"):
                unpack.unpack_package(source / package.ARCHIVE, source / package.MANIFEST, target)
            with patch.dict(os.environ, {"RUNNER_TEMP": str(parent), "GITHUB_REPOSITORY": "simcalc/npsh3"}), self.assertRaisesRegex(unpack.UnpackIssue, "public"):
                unpack.unpack_package(source / package.ARCHIVE, source / package.MANIFEST, target)
            with self.assertRaisesRegex(unpack.UnpackIssue, "strictly"):
                self.unpack(source, parent / ".." / "escaped-native-execution", parent)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
