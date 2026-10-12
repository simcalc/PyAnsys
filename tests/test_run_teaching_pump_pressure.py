"""Mocked bounded process/point contracts only; never native pressure results."""
from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import run_teaching_pump_pressure as runner
from test_teaching_pump_pressure_case import source_fixture
from test_run_teaching_pump_phase import field


class PressurePointProcessTests(unittest.TestCase):
    def test_current_exact_two_command_executor_reuse_preserves_native_guard(self):
        root=Path("fresh")
        with patch.object(runner.phase,"execute",return_value=({"syntheticFixture":True},"")) as execute:
            runner.execute(runner.commands(root)[0],root,1000,{"fixture":True},0)
            execute.assert_called_once_with(runner.commands(root)[0],root,1000,{"fixture":True},0)
            for args,index in ((["foamRun","-case","other"],1),(runner.commands(root)[0],True),(runner.commands(root)[0],1)):
                with self.assertRaises(ValueError):runner.execute(args,root,1000,{},index)
            self.assertEqual(execute.call_count,1)
        self.assertEqual(runner.LIMITS,runner.phase.LIMITS)
        self.assertEqual(runner.LIMITS["maximumSeconds"],1200)

    def test_new_runtime_role_retains_actual_binary_identity_without_faking_source_document_sha(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in runner.PROGRAMS:(root/name).write_bytes(("SYNTHETIC-"+name).encode())
            replies=[SimpleNamespace(stdout="20250206",stderr=""),*[SimpleNamespace(stdout="openfoam12: "+str((root/name).resolve()),stderr="") for name in runner.PROGRAMS]]
            with patch.object(Path,"is_relative_to",return_value=True),patch.object(runner.shutil,"which",side_effect=lambda name:str(root/name)),patch.object(runner.subprocess,"run",side_effect=replies):
                identity=runner.runtime_identity(root,runner.time.monotonic()+100)
            self.assertEqual(identity["schemaVersion"],runner.RUNTIME_SCHEMA)
            self.assertNotEqual(identity["schemaVersion"],runner.phase.RUNTIME_SCHEMA)
            self.assertEqual([row["program"] for row in identity["binaries"]],["checkMesh","foamRun"])
            self.assertEqual(json.loads((root/runner.RUNTIME).read_bytes()),identity)

    def test_current_manifest_and_study_use_new_canonical_role_and_actual_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/"input").write_bytes(b"SYNTHETIC-INPUT")
            bound={"manifest":{"files":[{**runner.record(root,"input")[1],"control":"fixture"}]},"study":{"syntheticFixture":True}}
            reducer=SimpleNamespace(build_manifest=lambda _:bound,encoded=lambda x:json.dumps(x,sort_keys=True,separators=(",",":")).encode())
            with patch.dict(sys.modules,{"reduce_teaching_pump_pressure":reducer}):rows,_=runner.bind_manifest(root)
            self.assertEqual(rows[runner.MANIFEST]["sha256"],sha256((root/runner.MANIFEST).read_bytes()).hexdigest())
            self.assertTrue((root/runner.STUDY).is_file())
            self.assertFalse((root/runner.phase.MANIFEST).exists())

    def test_unknown_vtk_or_unauthored_table_is_not_a_scientific_sample(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/"case/system").mkdir(parents=True);(root/"case/system/controlDict").write_bytes(b"massFlowInlet {type surfaceFieldValue;}\n")
            folder=root/"case/postProcessing/unrecognized/0";folder.mkdir(parents=True);(folder/"surfaceFieldValue.dat").write_bytes(b"SYNTHETIC")
            with self.assertRaises(ValueError):runner.sampling_records(root)
            (folder/"surfaceFieldValue.dat").unlink();(folder/"extra.vtk").write_bytes(b"UNREVIEWED")
            with self.assertRaises(ValueError):runner.postprocessing_records(root)


class SyntheticPressurePointTests(unittest.TestCase):
    def campaign(self,root,*,failed_stage=None,bad_model=False,pressure_offset=False,mutate=None,bad_mesh=False):
        """Only trusted input copying/parsing is real; all upstream/native facts are mocked."""
        source,verify=source_fixture(root);output=root/"point";end=runner.preparation.reference_case.DEFAULT_DURATION_S
        def runtime(*_):(output/runner.RUNTIME).write_bytes(b'{"syntheticFixture":true}');return {"syntheticFixture":True}
        def manifest(*_):
            rows={}
            for name in (runner.MANIFEST,runner.STUDY):(output/name).write_bytes(b'{"syntheticFixture":true}');rows[name]=runner.record(output,name)[1]
            return rows,[{**runner.record(output,"case/0/p_rgh")[1],"control":"fixture"}]
        def execute(args,workspace,deadline,identity,index):
            text="Build : 12-86e126a7bc4d\nEnd\n"
            if index==1 and not bad_model:text="Selecting cavitation model SchnerrSauer\n"+text
            path=output/"logs"/(f"{index:02d}-{args[0]}.log");path.write_text(text);row=runner.record(output,"logs/"+path.name)[1]
            if index==0:
                folder=output/"case/postProcessing/checkMesh/0";folder.mkdir(parents=True)
                for name in ("interface-impeller-outlet","interface-volute-inlet"):(folder/(name+"_coverage.vtk")).write_bytes(b"SYNTHETIC-NCC-NOT-NATIVE")
            if mutate and index==1:
                target=source/"case/0/p_rgh" if mutate=="original-source" else output/mutate
                target.write_bytes(target.read_bytes()+b"SYNTHETIC-MUTATION\n")
            return {"program":args[0],"exitCode":7 if failed_stage==index else 0,"elapsedSeconds":.1,"log":path.name,"logBytes":row["bytes"],"logSha256":row["sha256"]},text
        def final(*_):
            folder=output/"case"/format(end,".17g");folder.mkdir();rows=[]
            for name in runner.phase.END_FIELDS:
                value="(0 0 0)" if name=="U" else "1" if name=="alpha.water" else "100" if name=="omega" else "0.375" if name=="k" else "0" if name=="nut" else "251325" if name=="p" and pressure_offset else "150000"
                raw=field(name,value);(folder/name).write_bytes(raw);record=runner.record(output,(folder/name).relative_to(output).as_posix())[1]
                rows.append({**record,"fieldCheck":runner.phase.finite_field(raw,name,4)})
            table=output/"case/postProcessing/massFlowInlet/0";table.mkdir(parents=True);(table/"surfaceFieldValue.dat").write_bytes(b"# SYNTHETIC\n0 90\n")
            return rows,[(end,folder)]
        with patch.dict(os.environ,{"RUNNER_TEMP":str(root)}),patch.object(runner.safety,"context_guard"),\
             patch.object(runner.preparation,"verify_reference",side_effect=verify),patch.object(runner,"runtime_identity",side_effect=runtime),\
             patch.object(runner,"bind_manifest",side_effect=manifest),patch.object(runner,"execute",side_effect=execute),\
             patch.object(runner.phase.baseline.geometry,"verify_region_count",return_value=2),\
             patch.object(runner.base,"verify_merged_mesh",side_effect=ValueError("Actual native mesh failed") if bad_mesh else None,return_value={"mergedCellCount":4,"mergedPointCount":10}),\
             patch.object(runner.phase.baseline.scientific,"parse_mesh_quality",return_value={"syntheticFixture":True}),patch.object(runner,"final_fields",side_effect=final),redirect_stdout(io.StringIO()):
            report=runner.run(source,output,pressure_absolute_pa=150000)
        return report,output

    def test_actual_point_completion_does_not_qualify_observation_h0_or_bracket(self):
        with tempfile.TemporaryDirectory() as temp:
            report,output=self.campaign(Path(temp))
            self.assertTrue(report["actualCfdRunVerified"]);self.assertTrue(report["cavitationModelExecuted"])
            self.assertEqual(report["pressureAbsolutePa"],150000)
            self.assertEqual(report["sourceActivePhaseHeadM"],10.)
            self.assertEqual(len(report["finalNativeFieldRecords"]),7)
            self.assertTrue(all(report[key] is False for key in runner.FALSE_FLAGS))
            self.assertFalse((output/"teaching-pressure-acceptance.json").exists())
            self.assertEqual(json.loads((output/runner.REPORT).read_bytes()),report)

    def test_failed_quality_or_stage_stops_dependent_solver(self):
        for kwargs in ({"bad_mesh":True},{"failed_stage":0}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as temp:report,_=self.campaign(Path(temp),**kwargs)
            self.assertFalse(report["actualCfdRunVerified"]);self.assertEqual(len(report["stages"]),1)
            self.assertFalse(report["nativeSolverCompleted"])

    def test_missing_actual_model_or_wrong_absolute_pressure_basis_rejects(self):
        for kwargs in ({"bad_model":True},{"pressure_offset":True}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as temp:report,_=self.campaign(Path(temp),**kwargs)
            self.assertFalse(report["actualCfdRunVerified"]);self.assertTrue(report["nativeSolverCompleted"])
            self.assertFalse(report["finiteNativeEndFieldsVerified"])

    def test_actual_pressure_model_mesh_source_initial_clock_or_evidence_cannot_mutate(self):
        for relative in ("case/0/p_rgh","case/constant/fvModels","case/constant/polyMesh/points","case/system/controlDict",runner.phase.IDENTITY,runner.MANIFEST,
                         "logs/00-checkMesh.log","case/postProcessing/checkMesh/0/interface-volute-inlet_coverage.vtk","original-source"):
            with self.subTest(relative=relative),tempfile.TemporaryDirectory() as temp:report,_=self.campaign(Path(temp),mutate=relative)
            self.assertFalse(report["actualCfdRunVerified"]);self.assertFalse(report["inputArtifactsImmutable"])

    def test_cli_one_explicit_point_and_diagnostic_is_reviewable(self):
        report={k:False for k in ("actualCfdRunVerified","simulationExecuted","numericalPressurePointQualified","phaseH0Eligible","npsh3Qualified","physicalTransientExecuted","mainApplicationWrites")}
        report.update(status="TEACHING_PRESSURE_POINT_CORRECTION_REQUIRED",pressureAbsolutePa=150000,diagnostic={"message":"Exact synthetic cause"})
        with patch.object(sys,"argv",["pressure","--reference-directory","source","--output-directory","target","--pressure-absolute-pa","150000"]),patch.object(runner,"run",return_value=report) as run,redirect_stdout(io.StringIO()) as output:
            self.assertEqual(runner.main(),2);self.assertEqual(run.call_args.kwargs,{"pressure_absolute_pa":150000.})
            self.assertEqual(json.loads(output.getvalue())["diagnostic"],report["diagnostic"])


if __name__=="__main__":unittest.main()
