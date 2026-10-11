"""Synthetic native quality-diagnostic fixtures; no mesh or CFD is executed."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TOOL=Path(__file__).resolve().parents[1]/"tools/frozen_rotor_native_quality.py"
spec=importlib.util.spec_from_file_location("native_quality",TOOL)
quality=importlib.util.module_from_spec(spec);spec.loader.exec_module(quality)


def fixture(root):
    case=root/"execution/impeller-case";sets=case/"constant/polyMesh/sets";sets.mkdir(parents=True)
    log=root/"execution/logs/02-checkMesh.log";log.parent.mkdir()
    log.write_text("SYNTHETIC-NATIVE-DIAGNOSTIC-NOT-RESEARCH-EVIDENCE\n    cells: 6\n"
        "Cell determinant (wellposedness) : minimum: 8.9976547569259324e-06 average: 2.2992792823861392\n"
        " ***Cells with small determinant (< 0.001) found, number of cells: 3\nFailed 1 mesh checks.\nEnd\n")
    (sets/"underdeterminedCells").write_text(quality.base.foam_header("underdeterminedCells","cellSet","constant/polyMesh/sets")+"3\n(5 0 2)\n")
    # Non-contiguous actual zone labels demonstrate why guessed cell offsets
    # are invalid. Cells 0 and 5 are stationary; cell 2 is rotating here.
    (case/"constant/polyMesh/cellZones").write_text(quality.base.foam_header("cellZones","regIOobject","constant/polyMesh")+
        "2\n(rotating-fluid {type cellZone; cellLabels List<label> 3(1 2 4);}\n"
        "stationary-fluid {type cellZone; cellLabels List<label> 3(0 3 5);})\n")
    return case,log


class NativeQualityContracts(unittest.TestCase):
    def test_complete_native_count_binding_and_actual_noncontiguous_zone_membership(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);case,log=fixture(root)
            before={path:path.read_bytes() for path in root.rglob("*") if path.is_file()}
            result=quality.inspect(case,log)
            self.assertEqual(result["status"],"NativeQualityDiagnosticComplete")
            self.assertTrue(result["diagnosticComplete"]);self.assertFalse(result["nativeQualityAccepted"])
            self.assertEqual(result["nativeDeterminantThreshold"],.001)
            self.assertEqual(result["sampleMergedCellIds"],[0,2,5])
            self.assertEqual(result["cellZoneClassification"]["rotating-fluid"]["underdeterminedCellCount"],1)
            self.assertEqual(result["cellZoneClassification"]["stationary-fluid"]["sampleMergedCellIds"],[0,5])
            self.assertTrue(result["completeCellIdsRetainedInNativeCellSet"]);self.assertFalse(result["jsonIncludesAllCellIds"])
            self.assertTrue(result["nativeFailedCellSetVerified"]);self.assertFalse(result["sourceCellOrderVerified"])
            self.assertFalse(result["simulationExecuted"]);self.assertFalse(result["actualCfdRunVerified"])
            self.assertFalse(result["meshModified"]);self.assertFalse(result["acceptanceThresholdChanged"])
            for path,raw in before.items():self.assertEqual(path.read_bytes(),raw)
            for row in result["nativeEvidenceRecords"]:
                raw=(root/row["path"]).read_bytes()
                self.assertEqual(row["bytes"],len(raw));self.assertEqual(row["sha256"],sha256(raw).hexdigest())

    def test_complete_api_preserves_actual_labels_without_source_offset_inference(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            case,log=fixture(Path(temp))
            self.assertEqual(quality.complete_failed_ids_by_zone(case,log),
                {"rotating-fluid":{2},"stationary-fluid":{0,5}})
            target=case/"constant/polyMesh/sets/underdeterminedCells"
            target.write_text(target.read_text().replace("3\n(5 0 2)","3\n(5 0 0)"))
            with self.assertRaises(quality.QualityIssue):quality.complete_failed_ids_by_zone(case,log)

    def test_malformed_duplicate_nonnumeric_outside_or_incomplete_native_set_rejects(self):
        for body in ("3(0 0 5)","3(0 2 6)","3(0 -1 5)","3(0 2 nan)","2(0 5)","3(0 2)","3(0 2 5) junk"):
            with self.subTest(body=body),tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
                case,log=fixture(Path(temp));target=case/"constant/polyMesh/sets/underdeterminedCells"
                target.write_text(quality.base.foam_header("underdeterminedCells","cellSet")+body)
                with self.assertRaises(quality.QualityIssue):quality.inspect(case,log)

    def test_native_header_binary_wrong_role_and_code_directive_reject(self):
        changes=[lambda text:text.replace("class cellSet;","class labelList;"),lambda text:text.replace("format ascii;","format binary;"),
                 lambda text:text.replace("object underdeterminedCells;","object otherSet;"),lambda text:text+'\n#include "outside"\n',
                 lambda text:text+'\n$outside\n',lambda text:text+'\ncodeStream {}\n']
        for change in changes:
            with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
                case,log=fixture(Path(temp));target=case/"constant/polyMesh/sets/underdeterminedCells";target.write_text(change(target.read_text()))
                with self.assertRaises(quality.QualityIssue):quality.inspect(case,log)

    def test_changed_native_threshold_success_missing_end_or_log_count_reject(self):
        changes=[lambda text:text.replace("< 0.001","< 0.00001"),lambda text:text.replace("cells: 6","cells: 2"),
                 lambda text:text.replace("number of cells: 3","number of cells: 4"),lambda text:text.replace("Failed 1 mesh checks.","Mesh OK."),
                 lambda text:text.replace("End", "Incomplete"),lambda text:text+'FOAM FATAL ERROR\n']
        for change in changes:
            with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
                case,log=fixture(Path(temp));log.write_text(change(log.read_text()))
                with self.assertRaises(quality.QualityIssue):quality.inspect(case,log)

    def test_missing_overlap_unassigned_or_invalid_actual_cell_zone_reject(self):
        changes=[lambda text:text.replace("stationary-fluid","other-zone"),lambda text:text.replace("3(0 3 5)","3(1 3 5)"),
                 lambda text:text.replace("3(0 3 5)","2(0 5)"),lambda text:text.replace("3(1 2 4)","3(1 2 bad)"),
                 lambda text:text.replace("type cellZone;","type cellSet;",1)]
        for change in changes:
            with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
                case,log=fixture(Path(temp));target=case/"constant/polyMesh/cellZones";target.write_text(change(target.read_text()))
                with self.assertRaises(quality.QualityIssue):quality.inspect(case,log)

    def test_byte_changes_during_inspection_reject_current_evidence_binding(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            case,log=fixture(Path(temp));original=quality.read;changed=False
            def changing(path,runner,limit):
                nonlocal changed
                result=original(path,runner,limit)
                if Path(path).name=="cellZones" and not changed:
                    changed=True;log.write_text(log.read_text()+"\n// changed after first read\n")
                return result
            with patch.object(quality,"read",side_effect=changing),self.assertRaisesRegex(quality.QualityIssue,"changed while inspecting"):
                quality.inspect(case,log)

    def test_input_size_cell_count_and_sample_bounds(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            case,log=fixture(Path(temp))
            with patch.object(quality,"MAX_FILE",10),self.assertRaises(quality.QualityIssue):quality.inspect(case,log)
            with patch.object(quality,"MAX_CELLS",5),self.assertRaises(quality.QualityIssue):quality.inspect(case,log)
            with patch.object(quality,"SAMPLE_COUNT",1):result=quality.inspect(case,log)
            self.assertEqual(result["sampleMergedCellIds"],[0])
            self.assertEqual(result["cellZoneClassification"]["stationary-fluid"]["underdeterminedCellCount"],2)

    def test_source_path_outside_runner_and_link_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp,tempfile.TemporaryDirectory() as other,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            case,log=fixture(Path(temp));outside=Path(other)/"log";outside.write_bytes(log.read_bytes())
            with self.assertRaises(quality.QualityIssue):quality.inspect(case,outside)
            with patch.object(quality.Path,"is_symlink",return_value=True),self.assertRaises(Exception):quality.inspect(case,log)

    def test_cli_diagnostic_completion_returns_zero_without_quality_acceptance_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);case,log=fixture(root);target=root/"quality-diagnostic.json"
            args=[str(TOOL),"--case-directory",str(case),"--check-mesh-log",str(log),"--output-report",str(target)]
            with patch("sys.argv",args),patch.object(quality,"print",create=True):
                self.assertEqual(quality.main(),0)
                raw=target.read_bytes();result=json.loads(raw)
                self.assertFalse(result["nativeQualityAccepted"]);self.assertLess(len(raw),128*1024)
                self.assertEqual(quality.main(),2);self.assertEqual(target.read_bytes(),raw)


if __name__=="__main__":unittest.main()
