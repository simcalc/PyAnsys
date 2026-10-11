"""Synthetic ZIP transfer fixtures; none is actual native CFD evidence."""
from contextlib import contextmanager
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

spec = importlib.util.spec_from_file_location("snappy_phase_source_tests", Path(__file__).resolve().parents[1] / "tools/frozen_rotor_snappy_phase_source.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def execution():
    return {"schemaVersion": tool.EXECUTION_SCHEMA, "githubRepository": "simcalc/PyAnsys",
        "githubRunId": "123456789", "githubSourceCommit": "a"*40, "status": "SNAPPY_MRF_EXECUTED_ACCEPTANCE_REQUIRED",
        **{key: True for key in tool.NATIVE_TRUE_FLAGS},
        **{key: False for key in ("qualifiedBaseline", "numericalBaselineQualified", "fullGeometryQualification",
            "geometricApproximationQualified", "mainApplicationWrites")}, "fixture": "synthetic-metadata-not-native-evidence"}


def info(name, *, mode=stat.S_IFREG | 0o600, compression=zipfile.ZIP_DEFLATED):
    value = zipfile.ZipInfo(name)
    # Preserve deliberately hostile raw archive names. ZipInfo's constructor
    # otherwise normalizes Windows separators or truncates NUL before writing.
    value.filename = name; value.orig_filename = name
    value.create_system = 3; value.external_attr = mode << 16
    value.compress_type = compression
    return value


class SafeTransferTests(unittest.TestCase):
    @contextmanager
    def context(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RUNNER_TEMP": directory}):
            yield Path(directory)

    def fixture(self, root, *, extras=(), report=None):
        archive = root / "source.zip"
        header = tool.encoded(execution()) if report is None else report
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr(info("frozen-snappy-baseline/openfoam-execution.json"), header)
                output.writestr(info("frozen-snappy-baseline/impeller-case/500/p"), b"synthetic result data, not a solver run\n")
                output.writestr(info("frozen-snappy-native/impeller-case/constant/polyMesh/points"), b"unselected synthetic data\n")
                for entry, raw in extras: output.writestr(info(entry) if isinstance(entry, str) else entry, raw)
        admission = root / "source-admission.json"
        self.admit(archive, admission)
        return archive, admission, root / "source-artifact"

    def admit(self, archive, admission):
        raw = archive.read_bytes()
        inputs = tool.admission_tool.validate_inputs({"BASELINE_RUN_ID": "123456789"})
        repo = {"full_name": "simcalc/PyAnsys", "private": False}
        run = {"id": 123456789, "status": "completed", "conclusion": "success", "event": "workflow_dispatch",
            "path": tool.admission_tool.SOURCE_WORKFLOW, "head_sha": "a"*40, "repository": repo, "head_repository": repo}
        artifacts = {"artifacts": [{"id": 7654321, "name": "frozen-rotor-snappy-baseline-123456789", "expired": False,
            "size_in_bytes": len(raw), "workflow_run": {"id": 123456789}, "digest": "sha256:" + sha256(raw).hexdigest()}]}
        admission.write_bytes(tool.encoded(tool.admission_tool.validate_source(inputs, run, artifacts)))

    def rejected(self, archive, admission, output):
        with self.assertRaises((ValueError, zipfile.BadZipFile, RuntimeError, OSError)):
            tool.extract(archive, admission, output)
        self.assertFalse(output.exists(), "A rejected transfer must retain no attempt-owned output tree.")

    def test_exact_archive_bytes_and_selected_data_transfer_without_numerical_authority(self):
        with self.context() as root:
            archive, admission, output = self.fixture(root)
            before = {p.name: sha256(p.read_bytes()).hexdigest() for p in (archive, admission)}
            result = tool.extract(archive, admission, output)
            self.assertTrue(result["artifactBytesVerified"]); self.assertTrue(result["sourceExecutionMetadataBound"])
            self.assertEqual(result["actualArchiveSha256"], sha256(archive.read_bytes()).hexdigest())
            self.assertEqual(result["actualArchiveBytes"], archive.stat().st_size)
            self.assertEqual(result["archiveEntryCount"], 3)
            self.assertTrue(all(result[key] is False for key in tool.FALSE_FLAGS))
            self.assertFalse((output / "frozen-snappy-native").exists())
            self.assertEqual(result, tool.json_data((output / tool.REPORT).read_bytes()))
            for row in result["extractedFileRecords"]:
                raw = (output / row["path"]).read_bytes()
                self.assertEqual(row["bytes"], len(raw)); self.assertEqual(row["sha256"], sha256(raw).hexdigest())
            self.assertEqual(before, {p.name: sha256(p.read_bytes()).hexdigest() for p in (archive, admission)})

    def test_regular_zero_type_and_stored_deflated_modes_are_supported(self):
        with self.context() as root:
            entries = [(info("frozen-snappy-baseline/case/data0", mode=0, compression=zipfile.ZIP_STORED), b"stored"),
                (info("frozen-snappy-baseline/case/data1", mode=0o600), b"zero type bits"),
                (info("frozen-snappy-baseline/case/data2", mode=stat.S_IFREG | 0o644), b"regular"),
                (info("frozen-snappy-baseline/empty-dir/", mode=stat.S_IFDIR | 0o755), b"")]
            archive, admission, output = self.fixture(root, extras=entries)
            result = tool.extract(archive, admission, output)
            self.assertEqual(len(result["extractedFileRecords"]), 5)
            self.assertFalse((output / "frozen-snappy-baseline/empty-dir").exists())

    def test_size_digest_changed_admission_or_archive_fail_before_any_output(self):
        for mutation in ("size", "digest", "source-workflow", "numeric-claim", "head", "bytes"):
            with self.subTest(mutation=mutation), self.context() as root:
                archive, admission, output = self.fixture(root)
                value = json.loads(admission.read_bytes())
                if mutation == "bytes":
                    raw = bytearray(archive.read_bytes()); raw[-1] ^= 1; archive.write_bytes(raw)
                else:
                    if mutation == "size": value["artifactBytes"] += 1
                    elif mutation == "digest": value["githubReportedArchiveDigest"] = "sha256:" + "0"*64
                    elif mutation == "source-workflow": value["sourceWorkflow"] = ".github/workflows/original-frozen-rotor.yml"
                    elif mutation == "numeric-claim": value["sourceNumericalBaselineQualified"] = True
                    else: value["sourceCommit"] = "b"*40
                    admission.write_bytes(tool.encoded(value))
                self.rejected(archive, admission, output)

    def test_inner_run_head_schema_or_native_flags_cannot_borrow_other_evidence(self):
        mutations = [("githubRunId", "987"), ("githubSourceCommit", "b"*40), ("githubRepository", "simcalc/npsh"),
            ("schemaVersion", "untirta.frozen-rotor-native-baseline-reduction.v1"), ("status", "failure"),
            ("qualifiedBaseline", True), ("numericalBaselineQualified", True),
            *[(key, False) for key in tool.NATIVE_TRUE_FLAGS]]
        for key, value in mutations:
            with self.subTest(key=key), self.context() as root:
                report = execution(); report[key] = value
                archive, admission, output = self.fixture(root, report=tool.encoded(report))
                self.rejected(archive, admission, output)

    def test_duplicate_keys_and_nonfinite_json_in_admission_or_execution_fail(self):
        for target in ("admission", "execution"):
            for change in ("duplicate", "nan", "overflow"):
                with self.subTest(target=target, change=change), self.context() as root:
                    raw = tool.encoded(execution())
                    if change == "duplicate": raw = raw.replace(b'{', b'{"githubRunId":"123456789",', 1)
                    elif change == "nan": raw = raw.replace(b'{', b'{"unexpected":NaN,', 1)
                    else: raw = raw.replace(b'{', b'{"unexpected":1e999,', 1)
                    archive, admission, output = self.fixture(root, report=raw if target == "execution" else None)
                    if target == "admission":
                        raw = admission.read_bytes()
                        insert = b'"baselineRunId":"123456789",' if change == "duplicate" else b'"unexpected":'+(b'NaN,' if change == "nan" else b'1e999,')
                        admission.write_bytes(raw.replace(b'{', b'{'+insert, 1))
                    self.rejected(archive, admission, output)

    def test_all_members_including_unselected_roots_receive_path_validation(self):
        for name in ("/frozen-snappy-baseline/out", "C:/outside/out", "frozen-snappy-baseline/../out",
            "frozen-snappy-native/a/../../out", "frozen-snappy-baseline\\case\\out", "other-root/file",
            "frozen-snappy-baseline/.env.local", "frozen-snappy-native/evil.py", "frozen-snappy-baseline/a//b",
            "frozen-snappy-baseline/a/./b", "frozen-snappy-baseline/a\nrun", "frozen-snappy-baseline/a\x00hidden",
            "frozen-snappy-baseline/case/CON", "frozen-snappy-baseline/case/file."):
            with self.subTest(name=name), self.context() as root:
                archive, admission, output = self.fixture(root, extras=[(name, b"prohibited")])
                self.rejected(archive, admission, output)

    def test_duplicate_case_alias_and_file_parent_collisions_fail(self):
        examples = [[("frozen-snappy-baseline/openfoam-execution.json", tool.encoded(execution()))],
            [("frozen-snappy-baseline/case/X", b"one"), ("frozen-snappy-baseline/case/x", b"two")],
            [("frozen-snappy-baseline/Case/X", b"one"), ("frozen-snappy-baseline/case/y", b"two")],
            [("frozen-snappy-baseline/case", b"parent"), ("frozen-snappy-baseline/case/x", b"child")],
            [(info("frozen-snappy-baseline/case/", mode=stat.S_IFDIR | 0o755), b""), ("frozen-snappy-baseline/case", b"collision")]]
        for entries in examples:
            with self.subTest(entries=entries), self.context() as root:
                archive, admission, output = self.fixture(root, extras=entries)
                self.rejected(archive, admission, output)

    def test_links_devices_executable_modes_or_unsupported_compression_fail(self):
        entries = [info("frozen-snappy-baseline/link", mode=stat.S_IFLNK | 0o777),
            info("frozen-snappy-baseline/device", mode=stat.S_IFCHR | 0o600),
            info("frozen-snappy-baseline/exec", mode=stat.S_IFREG | 0o700),
            info("frozen-snappy-baseline/data", compression=zipfile.ZIP_BZIP2),
            info("frozen-snappy-baseline/dir/", mode=stat.S_IFDIR | 0o755)]
        for entry in entries:
            with self.subTest(name=entry.filename), self.context() as root:
                archive, admission, output = self.fixture(root, extras=[(entry, b"prohibited")])
                self.rejected(archive, admission, output)
        encrypted = info("frozen-snappy-baseline/data"); encrypted.flag_bits = 1
        encrypted.file_size = 1
        with self.assertRaises(ValueError): tool.member_path(encrypted)

    def test_archive_entry_expanded_file_and_report_budgets_are_independent(self):
        for constant, value in (("MAX_ARCHIVE_BYTES", 1), ("MAX_ENTRIES", 2), ("MAX_EXPANDED_BYTES", 1),
                ("MAX_FILE_BYTES", 1), ("MAX_REPORT_BYTES", 1)):
            with self.subTest(constant=constant), self.context() as root:
                archive, admission, output = self.fixture(root)
                with patch.object(tool, constant, value): self.rejected(archive, admission, output)

    def test_forged_declared_expanded_member_size_is_rejected_without_allocation(self):
        with self.context() as root:
            archive, admission, output = self.fixture(root)
            real = zipfile.ZipFile.infolist
            def inflated(instance):
                rows = real(instance)
                rows[-1].file_size = tool.MAX_FILE_BYTES+1
                return rows
            with patch.object(zipfile.ZipFile, "infolist", inflated): self.rejected(archive, admission, output)

    def test_invalid_local_header_or_executable_preamble_cannot_create_output(self):
        for change in ("header", "preamble"):
            with self.subTest(change=change), self.context() as root:
                archive, admission, output = self.fixture(root)
                raw = bytearray(archive.read_bytes())
                if change == "header": raw[0:4] = b"BAD!"
                else: raw = bytearray(b"MZexecutable") + raw
                archive.write_bytes(raw); self.admit(archive, admission)
                self.rejected(archive, admission, output)

    def test_crc_failure_after_owned_write_removes_only_attempt_output(self):
        with self.context() as root:
            sentinel = root / "sentinel.txt"; sentinel.write_bytes(b"keep this input")
            archive, admission, output = self.fixture(root)
            with zipfile.ZipFile(archive) as value: member = value.getinfo("frozen-snappy-baseline/impeller-case/500/p")
            raw = bytearray(archive.read_bytes())
            filename_len, extra_len = struct.unpack_from("<HH", raw, member.header_offset+26)
            position = member.header_offset+30+filename_len+extra_len
            raw[position+member.compress_size//2] ^= 0x20
            archive.write_bytes(raw); self.admit(archive, admission)
            self.rejected(archive, admission, output)
            self.assertEqual(sentinel.read_bytes(), b"keep this input")

    def test_archive_or_admission_change_during_transfer_cleans_owned_output(self):
        for change in ("archive", "admission"):
            with self.subTest(change=change), self.context() as root:
                archive, admission, output = self.fixture(root)
                real = tool.stream_digest; calls = []
                def changed(stream):
                    result = real(stream); calls.append(1)
                    if len(calls) == 2:
                        if change == "archive": return result[0], "0"*64
                        admission.write_bytes(b"changed admission")
                    return result
                with patch.object(tool, "stream_digest", changed): self.rejected(archive, admission, output)

    def test_existing_output_outside_scope_and_linked_ancestor_are_rejected(self):
        with self.context() as root:
            archive, admission, output = self.fixture(root)
            output.mkdir(); marker = output/"keep"; marker.write_bytes(b"preexisting")
            with self.assertRaises(ValueError): tool.extract(archive, admission, output)
            self.assertEqual(marker.read_bytes(), b"preexisting")
            with self.assertRaises(ValueError): tool.extract(archive, admission, root.parent / "outside-transfer")
            original = Path.is_symlink
            with patch.object(Path, "is_symlink", lambda path: path == archive or original(path)), self.assertRaises(ValueError):
                tool.extract(archive, admission, root/"unused")
            with patch.dict(os.environ, {"RUNNER_TEMP": ""}), self.assertRaises(ValueError):
                tool.extract(archive, admission, root/"unused")

    def test_required_inner_execution_is_unique_and_bounded_before_writes(self):
        with self.context() as root:
            archive, admission, output = self.fixture(root, report=b" "*(tool.MAX_METADATA_BYTES+1))
            self.rejected(archive, admission, output)
        with self.context() as root:
            archive = root/"source.zip"
            with zipfile.ZipFile(archive, "w") as value:
                value.writestr(info("frozen-snappy-baseline/case/data"), b"no execution report")
            admission = root/"admission.json"; self.admit(archive, admission)
            self.rejected(archive, admission, root/"source-artifact")


if __name__ == "__main__":
    unittest.main()
