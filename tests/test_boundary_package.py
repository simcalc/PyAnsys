"""Synthetic packaging security contracts; these fixtures are not CFD evidence."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

TOOL = Path(__file__).resolve().parents[1] / "tools/build_boundary_package.py"
spec = importlib.util.spec_from_file_location("boundary_package", TOOL)
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
LICENSE = TOOL.parents[1] / "resources/EXAMPLE_DATA_LICENSE.txt"


def triangle():
    return package.STL_HEADER + struct.pack("<I", 1) + struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)


def fixture(root):
    directory = root / "synthetic-input"
    directory.mkdir()
    files, meshes = [], []
    for role, zone in (("impeller", 1), ("volute", 2)):
        name = f"{role}-zone-{zone}.stl"
        raw = triangle()
        (directory / name).write_bytes(raw)
        descriptor = {"name": name, "bytes": len(raw), "sha256": sha256(raw).hexdigest(), "triangleCount": 1,
                      "format": "binary-stl", "sourceMesh": role + ".msh.h5", "zoneId": zone, "zoneName": role + "-synthetic-test"}
        files.append(descriptor)
        mesh = {key: [] for key in package.MESH_KEYS}
        source_name = descriptor["sourceMesh"]
        size, digest = package.MESH_SOURCES[source_name]
        mesh.update(name=source_name, bytes=size, sha256=digest, sourceSha256AfterExtraction=digest,
                    sourceUrl=f"https://media.githubusercontent.com/media/ansys/example-data/{package.PIN}/pyfluent/examples/pump-volute/{source_name}",
                    boundaryZones=[{"stlFile": descriptor}], signedEnclosedVolumeNativeUnitsCubed=0.0)
        meshes.append(mesh)
    report = {key: None for key in package.REPORT_KEYS}
    report.update(schemaVersion=package.SCHEMA, files=files, meshes=meshes,
                  exampleDataRepository="https://github.com/ansys/example-data", exampleDataCommit=package.PIN,
                  githubRepository=package.REPOSITORY, githubRunId="12345", githubRunAttempt="1",
                  githubSourceCommit="a" * 40, coordinateUnits="UNVERIFIED",
                  sourceFileBytes=sum(size for size, _ in package.MESH_SOURCES.values()),
                  derivedSurfaceBytes=sum(item["bytes"] for item in files))
    report.update({key: False for key in package.FALSE_FLAGS})
    report.update({key: True for key in package.TRUE_FLAGS})
    store_report(directory, report)
    return directory, report


def store_report(directory, report):
    (directory / "topology-report.json").write_text(json.dumps(report), encoding="utf-8")


class BoundaryPackageContracts(unittest.TestCase):
    def test_exact_manifest_package_keeps_report_license_and_claim_boundaries(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            directory, report = fixture(root)
            before = {path.name: path.read_bytes() for path in directory.iterdir()}
            output = root / "fresh-release"
            result = package.build(directory, output, LICENSE, runner_temp=root)
            self.assertIs(result["packageVerified"], True)
            self.assertIs(result["nativeCoordinateUnitsVerified"], False)
            self.assertIs(result["simulationExecuted"], False)
            self.assertEqual(set(path.name for path in output.iterdir()), {"frozen-rotor-boundaries.zip", "topology-report.json", "EXAMPLE_DATA_LICENSE.txt", "README-boundaries.txt", "SHA256SUMS"})
            with ZipFile(output / result["archive"]) as bundle:
                self.assertEqual(set(bundle.namelist()), set(before) | {"EXAMPLE_DATA_LICENSE.txt", "README-boundaries.txt"})
                for name, raw in before.items():
                    self.assertEqual(bundle.read(name), raw)
            for line in (output / "SHA256SUMS").read_text().splitlines():
                digest, name = line.split("  ")
                self.assertEqual(sha256((output / name).read_bytes()).hexdigest(), digest)
            self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, before)
            self.assertEqual(sha256((output / "EXAMPLE_DATA_LICENSE.txt").read_bytes()).hexdigest(), package.LICENSE_SHA256)
            self.assertIn("Native coordinate units are UNVERIFIED", (output / "README-boundaries.txt").read_text())

    def rejected(self, mutate):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            directory, report = fixture(root)
            mutate(directory, report)
            output = root / "fresh-release"
            with self.assertRaises((package.PackageIssue, FileNotFoundError)):
                package.build(directory, output, LICENSE, runner_temp=root)
            self.assertFalse(output.exists(), "Reject malformed inputs before creating a release directory.")

    def test_no_extra_file_directory_or_secret_payload(self):
        for name in (".env", "token.txt", "native.cas.h5", "unexpected.stl"):
            with self.subTest(name=name):
                self.rejected(lambda directory, report: (directory / name).write_text("synthetic-forbidden-payload"))
        self.rejected(lambda directory, report: (directory / "extra-folder").mkdir())
        self.rejected(lambda directory, report: (report.update(secret="synthetic forbidden field"), store_report(directory, report)))

    def test_no_traversal_duplicate_names_or_source_zone_mismatch(self):
        for field, value in (("name", "../impeller-zone-1.stl"), ("name", "impeller-zone-1.stl/extra"),
                             ("sourceMesh", "main.csv"), ("zoneId", True), ("zoneId", 7), ("zoneName", "unsafe\nsecret")):
            with self.subTest(field=field, value=value):
                self.rejected(lambda directory, report: (report["files"][0].update({field: value}), store_report(directory, report)))
        self.rejected(lambda directory, report: (report["files"].append(report["files"][0]), store_report(directory, report)))

    def test_proprietary_solver_and_native_unit_claims_cannot_enter_package(self):
        for flag in package.FALSE_FLAGS:
            with self.subTest(flag=flag):
                self.rejected(lambda directory, report: (report.update({flag: True}), store_report(directory, report)))
        for flag in package.TRUE_FLAGS:
            with self.subTest(flag=flag):
                self.rejected(lambda directory, report: (report.update({flag: False}), store_report(directory, report)))
        self.rejected(lambda directory, report: (report.update(coordinateUnits="metres"), store_report(directory, report)))

    def test_exact_upstream_provenance_and_boundary_manifest_required(self):
        self.rejected(lambda directory, report: (report.update(exampleDataCommit="main"), store_report(directory, report)))
        self.rejected(lambda directory, report: (report["meshes"][0].update(sourceSha256AfterExtraction="0" * 64), store_report(directory, report)))
        self.rejected(lambda directory, report: (report["meshes"][0].update(boundaryZones=[]), store_report(directory, report)))
        self.rejected(lambda directory, report: (report.update(sourceFileBytes=1), store_report(directory, report)))
        self.rejected(lambda directory, report: (report.update(derivedSurfaceBytes=1), store_report(directory, report)))
        self.rejected(lambda directory, report: (report.update(githubRunId="123?token=synthetic"), store_report(directory, report)))

    def test_report_size_duplicates_non_finite_and_overflow_are_rejected(self):
        self.rejected(lambda directory, report: (directory / "topology-report.json").write_bytes(b" " * (package.MAX_REPORT_BYTES + 1)))
        self.rejected(lambda directory, report: (directory / "topology-report.json").write_text('{"schemaVersion":"x","schemaVersion":"x"}'))
        self.rejected(lambda directory, report: (report.update(derivedSurfaceBytes=float("nan")), store_report(directory, report)))
        self.rejected(lambda directory, report: (directory / "topology-report.json").write_text(json.dumps(report).replace(f'"derivedSurfaceBytes": {report["derivedSurfaceBytes"]}', '"derivedSurfaceBytes": 1e999')))

    def test_binary_stl_length_digest_header_and_triangle_data_are_verified(self):
        with TemporaryDirectory() as temp:
            directory, report = fixture(Path(temp))
            descriptor, raw = report["files"][0], triangle()
            package.verify_stl(raw, descriptor)
            candidates = [raw[:-1], raw + b"x", b"unsafe-header" + raw[13:],
                          raw[:80] + struct.pack("<I", 2) + raw[84:]]
            nonfinite = bytearray(raw)
            nonfinite[84:88] = struct.pack("<f", float("nan"))
            candidates.append(bytes(nonfinite))
            attribute = bytearray(raw)
            attribute[-2:] = struct.pack("<H", 1)
            candidates.append(bytes(attribute))
            for bad in candidates:
                with self.subTest(bytes=len(bad)), self.assertRaises(package.PackageIssue):
                    package.verify_stl(bad, {**descriptor, "bytes": len(bad), "sha256": sha256(bad).hexdigest()})
            with self.assertRaises(package.PackageIssue):
                package.verify_stl(raw, {**descriptor, "sha256": "0" * 64})

    def test_changed_stl_license_and_missing_files_fail_before_output(self):
        self.rejected(lambda directory, report: (directory / report["files"][0]["name"]).write_bytes(triangle() + b"x"))
        self.rejected(lambda directory, report: (directory / report["files"][0]["name"]).unlink())
        with TemporaryDirectory() as temp:
            root = Path(temp)
            directory, report = fixture(root)
            bad_license = root / "bad-license.txt"
            bad_license.write_bytes(b"x" * package.LICENSE_BYTES)
            with self.assertRaises(package.PackageIssue):
                package.build(directory, root / "fresh", bad_license, runner_temp=root)

    def test_path_freshness_containment_symlinks_and_junctions(self):
        with TemporaryDirectory() as temp, TemporaryDirectory() as outside:
            root = Path(temp)
            directory, report = fixture(root)
            existing = root / "existing"
            existing.mkdir()
            for bad in (directory / "nested-release", directory, Path(outside) / "release", existing):
                with self.subTest(path=str(bad)), self.assertRaises(package.PackageIssue):
                    package.build(directory, bad, LICENSE, runner_temp=root)
            with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0, st_file_attributes=0x400)), self.assertRaisesRegex(package.PackageIssue, "junction"):
                package._no_links(root)
            link = root / "symlink-input"
            try:
                link.symlink_to(directory, target_is_directory=True)
            except OSError:
                return  # OS privilege limitation; junction detection above is tested independently.
            with self.assertRaisesRegex(package.PackageIssue, "Symlinks"):
                package.build(link, root / "fresh", LICENSE, runner_temp=root)

    def test_total_budget_rejects_without_publication(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            directory, report = fixture(root)
            with patch.object(package, "MAX_OUTPUT_BYTES", 512), self.assertRaises(package.PackageIssue):
                package.build(directory, root / "fresh", LICENSE, runner_temp=root)

    def test_workflow_keeps_standard_free_runner_budgets_and_publication_scope(self):
        workflow = (TOOL.parents[1] / ".github/workflows/extract-frozen-rotor-boundaries.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("runs-on: ubuntu-22.04", workflow)
        self.assertIn("timeout-minutes: 10", workflow)
        self.assertLess(workflow.index("tools/public_runner_guard.py"), workflow.index("python -m venv"))
        self.assertIn("h5py==3.16.0 numpy==2.5.4", workflow)
        self.assertIn('OPENBLAS_NUM_THREADS: "1"', workflow)
        self.assertIn("ulimit -v 2097152", workflow)
        self.assertIn("timeout --kill-after=10s 300s", workflow)
        self.assertEqual(workflow.count("GH_TOKEN:"), 1)
        self.assertIn("--repo simcalc/PyAnsys", workflow)
        self.assertIn("frozen-rotor-boundaries-${GITHUB_RUN_ID}", workflow)
        self.assertIn("--proto '=https' --proto-redir '=https'", workflow)
        self.assertIn("--max-filesize 131072", workflow)
        self.assertIn("--max-filesize 26214400", workflow)
        self.assertIn("cmp topology-report.json anonymous-topology-report.json", workflow)
        self.assertIn("cmp frozen-rotor-boundaries.zip anonymous-frozen-rotor-boundaries.zip", workflow)
        for forbidden in ("launch_fluent(", "connect_to_fluent(", "self-hosted", "gh secret", "gh variable", "docker run", "gmsh "):
            self.assertNotIn(forbidden, workflow)


if __name__ == "__main__":
    unittest.main()
