"""Synthetic transfer/process tests, never CFD or native quality evidence."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]/"tools"
spec = importlib.util.spec_from_file_location("geometry_pilot_runner",TOOLS/"run_frozen_rotor_geometry_pilot.py")
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
BASE = runner.BASE


class GeometryOnlyLifecycle(unittest.TestCase):
    def execute_fixture(self, root, *, mesh_ok=False, execution_error=False, changed_source=False):
        output = root/"execution"; output.mkdir()
        bound = {"report":{"volumeMeshDerived":True},"reportFile":{"path":"agglomeration-report.json","bytes":1,"sha256":"0"*64}}
        prepared = {"components":{"synthetic":"NOT A NATIVE MESH"},"nativeCommandArguments":runner.recipe.commands(output)[:3],
                    "agglomerationReport":bound["reportFile"]}
        calls = []
        def execute(arguments, log, output, deadline, runtime):
            calls.append(arguments[0])
            if execution_error and arguments[0]=="createNonConformalCouples":
                raise BASE.CaseIssue("SYNTHETIC NATIVE FAILURE")
            raw = ("Mesh OK.\nEnd\n" if mesh_ok and arguments[0]=="checkMesh" else "SYNTHETIC ONLY\nEnd\n").encode()
            BASE.write_exclusive(log,raw)
            return {"program":arguments[0],"exitCode":0,"log":log.name,"logBytes":len(raw),"logSha256":sha256(raw).hexdigest()},raw.decode()
        import frozen_rotor_native_quality as quality
        with patch.dict(os.environ,{"RUNNER_TEMP":str(root)}),patch.object(runner.native,"context_guard"),\
                patch.object(runner,"prepare",return_value=prepared),patch.object(runner.native,"runtime_identity",return_value={}),\
                patch.object(runner.native,"execute",side_effect=execute),\
                patch.object(quality,"inspect",return_value={"status":"SYNTHETIC_DIAGNOSTIC_NOT_CFD","nativeQualityAccepted":False}),\
                patch.object(BASE,"verify_merged_mesh",side_effect=None if mesh_ok else BASE.CaseIssue("SYNTHETIC QUALITY FAILURE"),return_value={"syntheticFixtureNotCFD":True}),\
                patch.object(runner.geometry,"verify_output",return_value={**bound,"reportFile":{**bound["reportFile"],"sha256":"1"*64}} if changed_source else bound),\
                patch.object(runner,"print",create=True):
            code = runner.run(root/"geometry",output)
        result = json.loads((output/"geometry-pilot-execution.json").read_bytes())
        return code,result,calls

    def test_completed_failed_quality_diagnostic_cannot_promote_mesh_or_cfd(self):
        with tempfile.TemporaryDirectory() as temp:
            code,result,calls = self.execute_fixture(Path(temp))
        self.assertEqual(code,0); self.assertTrue(result["nativeDiagnosticCompleted"])
        self.assertFalse(result["nativeQualityAccepted"])
        self.assertEqual(calls,["mergeMeshes","createNonConformalCouples","checkMesh"])
        self.assertEqual(result["status"],"GEOMETRY_PILOT_NATIVE_QUALITY_CORRECTION_REQUIRED")
        for key in ("actualCfdRunVerified","simulationExecuted","qualifiedBaseline","npsh3Qualified","physicalTransientExecuted","mainApplicationWrites"):
            self.assertFalse(result[key])

    def test_actual_quality_acceptance_route_still_never_launches_a_solver(self):
        with tempfile.TemporaryDirectory() as temp:
            code,result,calls = self.execute_fixture(Path(temp),mesh_ok=True)
        self.assertEqual(code,0); self.assertTrue(result["nativeQualityAccepted"])
        self.assertNotIn("foamRun",calls)
        self.assertFalse(result["actualCfdRunVerified"]); self.assertFalse(result["qualifiedBaseline"])

    def test_native_failure_stops_dependents_and_is_not_completed_diagnostic(self):
        with tempfile.TemporaryDirectory() as temp:
            code,result,calls = self.execute_fixture(Path(temp),execution_error=True)
        self.assertEqual(code,2); self.assertFalse(result["nativeDiagnosticCompleted"])
        self.assertEqual(calls,["mergeMeshes","createNonConformalCouples"])
        self.assertFalse(result["nativeQualityAccepted"])

    def test_derived_source_change_revokes_even_a_synthetic_success(self):
        with tempfile.TemporaryDirectory() as temp:
            code,result,calls = self.execute_fixture(Path(temp),mesh_ok=True,changed_source=True)
        self.assertEqual(code,2); self.assertFalse(result["nativeQualityAccepted"])
        self.assertFalse(result["nativeDiagnosticCompleted"])
        self.assertNotIn("foamRun",calls)


if __name__=="__main__":
    unittest.main()
