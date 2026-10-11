"""Synthetic file/ZIP integrity fixtures; no solver or restart was performed."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

TOOL = Path(__file__).resolve().parents[1] / "tools/build_native_pump_package.py"
spec = importlib.util.spec_from_file_location("native_pump_package_tests", TOOL)
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class NativePackageContracts(unittest.TestCase):
    def write(self, root, relative, payload):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload if isinstance(payload, bytes) else payload.encode())
        return path

    def fixture(self, parent, *, executed=True):
        root = parent / "native-execution"
        root.mkdir()
        inputs = set(package.REQUIRED_INPUTS)
        if executed:
            inputs |= package.REQUIRED_COMPLETED
        for relative in inputs:
            if relative.endswith(".json"):
                value = {"syntheticFixture": True, "researchEvidence": False}
                if relative == "openfoam-preparation.json":
                    value.update(exampleDataCommit=package.PIN, mainApplicationWrites=False)
                self.write(root, relative, package.canonical(value))
            else:
                self.write(root, relative, "FoamFile {format ascii; class dictionary; object syntheticFixture;}\nvalue 1;\n")
        self.write(root, "openfoam-preparation.json", package.canonical({"exampleDataCommit": package.PIN, "mainApplicationWrites": False,
                    "syntheticFixture": True, "researchEvidence": False}))
        execution = {"schemaVersion": "untirta.frozen-rotor-openfoam-execution.v1", "simulationExecuted": executed,
                     "actualCfdRunVerified": executed, "qualifiedBaseline": False, "mainApplicationWrites": False,
                     "status": "MRF_EXECUTED_ACCEPTANCE_REQUIRED" if executed else "NATIVE_STAGE_CORRECTION_REQUIRED",
                     "syntheticFixture": True, "researchEvidence": False}
        self.write(root, "openfoam-execution.json", package.canonical(execution))
        self.write(root, "logs/00-gmshToFoam.log", "Synthetic log fixture, never native execution\nEnd\n")
        if executed:
            self.write(root, "impeller-case/postProcessing/weightedInlet/1/surfaceFieldValue.dat", "# Time average(p)\n500 1\n")
        self.write(root, "impeller-case/impeller.msh", "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n")
        self.write(root, "volute-case/volute.msh", "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n")
        self.write(root, "volute-case/0/p", "FoamFile {format ascii; class volScalarField; object p;}\ninternalField uniform 0;\n")
        return root

    def build(self, root, parent):
        with patch.dict(os.environ, {"RUNNER_TEMP": str(parent)}):
            return package.build_package(root)

    def test_actual_archive_bytes_readback_hashes_and_nested_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.fixture(parent)
            metadata = self.build(root, parent)
            manifest = json.loads((root / package.MANIFEST).read_text())
            self.assertEqual(metadata["archive"]["sha256"], sha256((root / package.ARCHIVE).read_bytes()).hexdigest())
            self.assertEqual(metadata["manifest"]["sha256"], sha256((root / package.MANIFEST).read_bytes()).hexdigest())
            with ZipFile(root / package.ARCHIVE) as bundle:
                self.assertEqual(bundle.namelist(), [row["path"] for row in manifest["files"]])
                self.assertIn("impeller-case/500/p", bundle.namelist())
                self.assertIn("impeller-case/postProcessing/weightedInlet/1/surfaceFieldValue.dat", bundle.namelist())
                for row in manifest["files"]:
                    raw = bundle.read(row["path"])
                    self.assertEqual(len(raw), row["bytes"])
                    self.assertEqual(sha256(raw).hexdigest(), row["sha256"])
                    self.assertEqual(raw, (root / row["path"]).read_bytes())
                self.assertFalse(any(name.startswith("volute-case") or name.endswith(".msh") for name in bundle.namelist()))
            self.assertEqual(len(manifest["excludedDuplicates"]), 3)
            self.assertTrue(metadata["actualCfdRunVerified"])  # preserved synthetic test flag, not real CFD evidence
            self.assertFalse(metadata["qualifiedBaseline"])
            self.assertFalse(metadata["restartVerified"])
            self.assertFalse(manifest["physicalValidationVerified"])

    def test_failed_native_stage_can_retain_diagnostic_evidence_without_success_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.fixture(parent, executed=False)
            metadata = self.build(root, parent)
            manifest = json.loads((root / package.MANIFEST).read_text())
            self.assertFalse(metadata["actualCfdRunVerified"])
            self.assertFalse(metadata["simulationExecuted"])
            self.assertTrue(manifest["missingOptionalEvidence"])
            self.assertEqual(manifest["executionStatus"], "NATIVE_STAGE_CORRECTION_REQUIRED")

    def original_fixture(self,parent):
        root=self.fixture(parent)
        (root/'remesh-report.json').unlink()
        (root/'impeller-case/system/changeDictionaryDict').unlink()
        self.write(root,'native-conversion.json',package.canonical({'syntheticFixture':True,'researchEvidence':False}))
        for component in ('impeller','volute'):
            self.write(root,component+'-case/sourceFaceIds','FoamFile {format ascii; class labelList; object sourceFaceIds;}\n1\n(1)\n')
        self.write(root,'openfoam-preparation.json',package.canonical({'schemaVersion':package.ORIGINAL_PREPARATION,
                    'exampleDataCommit':package.PIN,'mainApplicationWrites':False,'syntheticFixture':True}))
        self.write(root,'openfoam-execution.json',package.canonical({'schemaVersion':package.ORIGINAL_EXECUTION,
                    'status':'ORIGINAL_MRF_EXECUTED_ACCEPTANCE_REQUIRED','simulationExecuted':True,'actualCfdRunVerified':True,
                    'qualifiedBaseline':False,'mainApplicationWrites':False,'syntheticFixture':True}))
        return root

    def test_original_lane_preserves_polygon_maps_without_inventing_remesh_report(self):
        with tempfile.TemporaryDirectory() as temp:
            parent=Path(temp);root=self.original_fixture(parent)
            result=self.build(root,parent)
            manifest=json.loads((root/package.MANIFEST).read_bytes())
            with ZipFile(root/package.ARCHIVE) as bundle:
                self.assertNotIn('remesh-report.json',bundle.namelist())
                self.assertIn('native-conversion.json',bundle.namelist())
                self.assertIn('volute-case/sourceFaceIds',bundle.namelist())
            self.assertFalse(result['preliminaryBaselineQualified'])
            self.assertEqual(manifest['missingOptionalEvidence'],[])
            self.assertEqual(manifest['limits']['workspaceBytes'],package.ORIGINAL_WORKSPACE)

    def test_original_lane_requires_exact_schema_pair_and_both_source_maps(self):
        for changed in ('schema','impeller-case/sourceFaceIds','volute-case/sourceFaceIds','native-conversion.json'):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as temp:
                parent=Path(temp);root=self.original_fixture(parent)
                if changed=='schema':
                    value=json.loads((root/'openfoam-execution.json').read_bytes())
                    value['schemaVersion']='untirta.frozen-rotor-openfoam-execution.v1'
                    self.write(root,'openfoam-execution.json',package.canonical(value))
                else:(root/changed).unlink()
                with self.assertRaises(package.PackageIssue):self.build(root,parent)

    def test_existing_evidence_is_never_overwritten(self):
        for output in (package.ARCHIVE, package.MANIFEST):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                root = self.fixture(parent)
                self.write(root, output, "existing-owner-evidence")
                with self.assertRaisesRegex(package.PackageIssue, "fresh"):
                    self.build(root, parent)
                self.assertEqual((root / output).read_text(), "existing-owner-evidence")

    def test_missing_required_inputs_final_fields_and_report_flags_reject(self):
        for missing in ("openfoam-preparation.json", "impeller-case/0/U", "impeller-case/500/p", "classroom-study.json"):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                root = self.fixture(parent)
                (root / missing).unlink()
                with self.assertRaises(package.PackageIssue):
                    self.build(root, parent)
                self.assertFalse((root / package.ARCHIVE).exists())
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.fixture(parent)
            report = json.loads((root / "openfoam-execution.json").read_text())
            report["simulationExecuted"] = False
            self.write(root, "openfoam-execution.json", package.canonical(report))
            with self.assertRaisesRegex(package.PackageIssue, "unexecuted"):
                self.build(root, parent)

    def test_scripts_credentials_arbitrary_files_and_executable_content_reject(self):
        for name, payload in (("credentials.env", "secret"), (".env.local", "secret"), ("impeller-case/system/run.py", "print(1)"),
                              ("notes.txt", "unrecognized"), ("impeller-case/0/p", b"\x7fELFbinary"),
                              ("impeller-case/system/controlDict", "FoamFile {format ascii;} #codeStream { code; }"),
                              ("logs/00-gmshToFoam.log", "Authorization: Bearer example-secret-never-publish")):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                root = self.fixture(parent)
                self.write(root, name, payload)
                with self.subTest(name=name), self.assertRaises(package.PackageIssue):
                    self.build(root, parent)
                self.assertFalse((root / package.ARCHIVE).exists())
                self.assertFalse((root / package.MANIFEST).exists())

    def test_actual_output_and_input_limits_cleanup_exact_fresh_assets(self):
        for key, limit in (("MAX_WORKSPACE", 1), ("MAX_FILE", 32), ("MAX_ZIP", 64), ("MAX_ENTRIES", 1), ("MAX_SCAN_ENTRIES", 1)):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                root = self.fixture(parent)
                with patch.object(package, key, limit), self.assertRaises(package.PackageIssue):
                    self.build(root, parent)
                self.assertFalse((root / package.ARCHIVE).exists())
                self.assertFalse((root / package.MANIFEST).exists())
                self.assertTrue((root / "openfoam-execution.json").is_file())

    def test_optional_license_and_immutable_acceptance_report(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.fixture(parent)
            official = TOOL.parents[1] / "resources/EXAMPLE_DATA_LICENSE.txt"
            self.write(root, "EXAMPLE_DATA_LICENSE.txt", official.read_bytes())
            self.write(root, "openfoam-acceptance.json", package.canonical({"status": "SyntheticPendingFixture", "restartVerified": False}))
            self.build(root, parent)
            manifest = json.loads((root / package.MANIFEST).read_text())
            self.assertTrue(manifest["upstreamLicenseIncluded"])
            self.assertEqual(manifest["nativeAcceptanceReport"], "openfoam-acceptance.json")
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.fixture(parent)
            self.write(root, "EXAMPLE_DATA_LICENSE.txt", "changed-license")
            with self.assertRaisesRegex(package.PackageIssue, "unchanged"):
                self.build(root, parent)

    def preliminary(self, root):
        # Explicit synthetic checksum binding fixture; no numerical/CFD evidence.
        paths = ("native-input-manifest.json", "openfoam-execution.json", "logs/00-gmshToFoam.log")
        records = [{"path": name, "bytes": (root / name).stat().st_size,
                    "sha256": sha256((root / name).read_bytes()).hexdigest()} for name in paths]
        observation = {"controlledVariationProof": {"nativeInputManifestSha256": records[0]["sha256"]},
                       "nativeEvidence": {"sha256": sha256(package.canonical(sorted(records, key=lambda row: row["path"]))).hexdigest()}}
        reduction = {"schemaVersion": "untirta.frozen-rotor-native-baseline-reduction.v1", "status": "PreliminaryBaselineQualified",
                     "preliminaryBaselineQualified": True, "qualifiedBaseline": True, "actualCfdRunVerified": True,
                     "mainApplicationWrites": False, "qualificationScope": "Synthetic preliminary scope, no native simulation occurred in this test.",
                     "nativeEvidenceRecords": records, "nativeObservation": observation, "syntheticFixture": True, "researchEvidence": False}
        self.write(root, "native-baseline-reduction.json", package.canonical(reduction))
        return reduction

    def test_bound_reducer_claim_is_separate_from_immutable_raw_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.fixture(parent)
            self.preliminary(root)
            result = self.build(root, parent)
            manifest = json.loads((root / package.MANIFEST).read_text())
            self.assertTrue(result["preliminaryBaselineQualified"])  # a preserved synthetic fixture claim only
            self.assertFalse(result["qualifiedBaseline"])
            self.assertFalse(manifest["qualifiedBaseline"])
            self.assertFalse(result["restartVerified"])
            self.assertIn("Synthetic preliminary", result["preliminaryQualificationScope"])
        for tamper in ("evidence", "input", "aggregate", "actual"):
            with tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                root = self.fixture(parent)
                reduction = self.preliminary(root)
                if tamper == "evidence":
                    self.write(root, "logs/00-gmshToFoam.log", "changed evidence\nEnd\n")
                elif tamper == "input":
                    reduction["nativeObservation"]["controlledVariationProof"]["nativeInputManifestSha256"] = "0" * 64
                elif tamper == "aggregate":
                    reduction["nativeObservation"]["nativeEvidence"]["sha256"] = "0" * 64
                else:
                    reduction["actualCfdRunVerified"] = False
                self.write(root, "native-baseline-reduction.json", package.canonical(reduction))
                with self.subTest(tamper=tamper), self.assertRaises(package.PackageIssue):
                    self.build(root, parent)
                self.assertFalse((root / package.ARCHIVE).exists())
                self.assertFalse((root / package.MANIFEST).exists())

    def test_an_actual_file_change_during_zip_write_is_rejected_and_cleaned(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.fixture(parent)
            original = ZipFile.open
            def changing_open(bundle, name, mode="r", *args, **kwargs):
                if mode == "w" and getattr(name, "filename", name) == "openfoam-execution.json":
                    target = root / "impeller-case/0/p"
                    target.write_bytes(target.read_bytes() + b"\nchanged after the earlier file was archived\n")
                return original(bundle, name, mode, *args, **kwargs)
            with patch.object(ZipFile, "open", changing_open), self.assertRaisesRegex(package.PackageIssue, "changed"):
                self.build(root, parent)
            self.assertFalse((root / package.ARCHIVE).exists())
            self.assertFalse((root / package.MANIFEST).exists())
            self.assertIn(b"changed", (root / "impeller-case/0/p").read_bytes())

    def test_packaging_requires_own_independent_runner_directory_and_regular_links(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = self.fixture(parent)
            with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(package.PackageIssue, "RUNNER_TEMP"):
                package.build_package(root)
            with patch.dict(os.environ, {"RUNNER_TEMP": str(root)}), self.assertRaisesRegex(package.PackageIssue, "strictly"):
                package.build_package(root)
            link = root / "impeller-case/0/linked-p"
            try:
                link.symlink_to(root / "impeller-case/0/p")
            except OSError:
                self.skipTest("Windows account does not have permission to create a symbolic link.")
            with self.assertRaisesRegex(package.PackageIssue, "links"):
                self.build(root, parent)


if __name__ == "__main__":
    unittest.main()
