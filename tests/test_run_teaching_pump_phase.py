"""Synthetic process/field contracts only: no CAD, phase solver or H0 evidence."""
from contextlib import ExitStack,redirect_stdout
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import run_teaching_pump_phase as runner
from test_run_teaching_pump_baseline import near_points_fixture


def field(name,values="300000",*,uniform=True):
    if isinstance(values,list):values=" ".join(map(str,values))
    internal="uniform "+values if uniform else "nonuniform List<scalar> 4 ("+values+")"
    kind="volVectorField" if name=="U" else "volScalarField"
    return ("FoamFile {version 2.0; format ascii; class "+kind+"; object "+name+";}\n"
        "dimensions ["+runner.FIELD_DIMENSIONS[name]+"];\ninternalField "+internal+";\nboundaryField {}\n").encode("ascii")


class Process:
    pid=123456
    class Stream:
        def fileno(self):return 123
        def close(self):pass
    stdout=Stream()
    def __init__(self,code=0):self.code=code
    def poll(self):return self.code
    def wait(self,timeout):return self.code


class Selector:
    def __init__(self):self.active=True
    def register(self,stream,event):self.stream=stream
    def get_map(self):return {1:True} if self.active else {}
    def select(self,timeout):return [(SimpleNamespace(fileobj=self.stream),None)]
    def unregister(self,stream):self.active=False
    def close(self):pass


class NativePhaseProcessTests(unittest.TestCase):
    def fixture(self,root,*,code=0,times=None):
        output=root/"execution";output.mkdir();(output/"logs").mkdir();(output/"case").mkdir()
        binary=root/"checkMesh";binary.write_bytes(b"SYNTHETIC-NON-NATIVE-BINARY")
        runtime={"binaries":[{"program":"checkMesh","path":str(binary),"packageOwnership":"openfoam12: "+str(binary),
            "bytes":binary.stat().st_size,"sha256":sha256(binary.read_bytes()).hexdigest()}]}
        chunks=iter([b"Build : 12-86e126a7bc4d\nEnd\n",b""])
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path,"is_relative_to",return_value=True))
            stack.enter_context(patch.object(runner.selectors,"DefaultSelector",Selector))
            popen=stack.enter_context(patch.object(runner.subprocess,"Popen",return_value=Process(code)))
            stack.enter_context(patch.object(runner.os,"read",side_effect=lambda *_:next(chunks)))
            stop=stack.enter_context(patch.object(runner.native,"stop_owned"))
            if times is not None:stack.enter_context(patch.object(runner.time,"monotonic",side_effect=times))
            result=runner.execute(runner.commands(output)[0],output,1000 if times is not None else runner.time.monotonic()+100,runtime,0)
        return result,popen,stop,output

    def test_exact_real_preparation_commands_keep_coupled_mesh_without_duplicate_ncc(self):
        import teaching_pump_phase_case as preparation
        self.assertEqual(runner.commands(Path("fixture")),preparation.commands(Path("fixture")))
        self.assertEqual([row[0] for row in runner.commands(Path("fixture"))],["checkMesh","foamRun"])
        self.assertEqual(runner.commands(Path("fixture"))[0][3:],["-allGeometry","-allTopology","-writeSets"])
        self.assertNotIn("createNonConformalCouples",repr(runner.commands(Path("fixture"))))

    def test_current_package_binary_owned_process_and_secret_free_child(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"GH_TOKEN":"NEVER-FORWARD"}):
            (stage,text),popen,stop,output=self.fixture(Path(temp))
        args,kwargs=popen.call_args
        self.assertEqual(args[0][1:],runner.commands(output)[0][1:]);self.assertEqual(kwargs["cwd"],str(output/"case"))
        self.assertTrue(kwargs["start_new_session"]);self.assertIs(kwargs["preexec_fn"],runner.child_limits)
        self.assertNotIn("GH_TOKEN",kwargs["env"]);stop.assert_called_once()
        self.assertEqual(stage["logSha256"],sha256(text.encode()).hexdigest())

    def test_nonzero_native_exit_is_not_erased(self):
        with tempfile.TemporaryDirectory() as temp:(stage,_),_,_,_=self.fixture(Path(temp),code=8)
        self.assertEqual(stage["exitCode"],8)

    def test_native_log_and_completion_deadline_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(runner.LIMITS,{"maximumLogBytes":5}),self.assertRaises(ValueError):self.fixture(Path(temp))
        with tempfile.TemporaryDirectory() as temp,self.assertRaisesRegex(ValueError,"native completion"):self.fixture(Path(temp),times=[0,0,0,0,1001])

    def test_wrong_command_or_changed_binary_reject_before_native_start(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(runner.subprocess,"Popen") as popen:
            root=Path(temp);(root/"logs").mkdir();binary=root/"checkMesh";binary.write_bytes(b"SYNTHETIC")
            for args,index in ((["createNonConformalCouples","-case",str(root)],0),(runner.commands(root)[0],1),(runner.commands(root)[0],True)):
                with self.assertRaises(ValueError):runner.execute(args,root,1000,{},index)
            runtime={"binaries":[{"program":"checkMesh","path":str(binary),"packageOwnership":"openfoam12: "+str(binary),"bytes":1,"sha256":"f"*64}]}
            with patch.object(Path,"is_relative_to",return_value=True),self.assertRaisesRegex(ValueError,"changed after binding"):
                runner.execute(runner.commands(root)[0],root,1000,runtime,0)
            popen.assert_not_called()

    def test_two_actual_executable_sha_and_package_ownership_are_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in runner.PROGRAMS:(root/name).write_bytes(("SYNTHETIC-"+name).encode())
            replies=[SimpleNamespace(stdout="20250206",stderr=""),*[SimpleNamespace(stdout="openfoam12: "+str((root/name).resolve()),stderr="") for name in runner.PROGRAMS]]
            with patch.object(runner.shutil,"which",side_effect=lambda name:str(root/name)),patch.object(Path,"is_relative_to",return_value=True),patch.object(runner.subprocess,"run",side_effect=replies):
                identity=runner.runtime_identity(root,runner.time.monotonic()+100)
            self.assertEqual(identity["schemaVersion"],runner.RUNTIME_SCHEMA);self.assertEqual(identity["openFoamFoundationVersion"],12)
            self.assertEqual([row["program"] for row in identity["binaries"]],list(runner.PROGRAMS))
            self.assertEqual(json.loads((root/runner.RUNTIME).read_bytes()),identity)


class AbsolutePressureAndEvidenceTests(unittest.TestCase):
    def test_native_empty_alpha_dimensions_are_semantically_equal_without_rewriting_bytes(self):
        for uniform,values in ((True,"1"),(False,[0,.25,.75,1])):
            with self.subTest(uniform=uniform):
                original=field("alpha.water",values,uniform=uniform)
                native=original.replace(b"[0 0 0 0 0 0 0]",b"[]")
                self.assertEqual(runner.finite_field(native,"alpha.water",4),runner.finite_field(original,"alpha.water",4))
                self.assertIn(b"dimensions [];",native)
                with self.assertRaises(ValueError):runner.fields.finite_field(native,"alpha.water",4)

    def test_dimensionless_support_does_not_admit_wrong_role_bad_count_alpha_or_boundary_values(self):
        good=field("alpha.water",[0,.25,.75,1],uniform=False).replace(b"[0 0 0 0 0 0 0]",b"[]")
        cases=((good.replace(b"4 (",b"3 ("),"alpha.water"),
            (good.replace(b"0.75",b"1.1"),"alpha.water"),
            (good.replace(b"0.75",b"nan"),"alpha.water"),
            (good.replace(b"boundaryField {}",b"boundaryField { inlet {type fixedValue;value uniform 1e500;} }"),"alpha.water"),
            (good.replace(b"dimensions [];",b"dimensions []; dimensions [];"),"alpha.water"),
            (field("p").replace(b"[1 -1 -2 0 0 0 0]",b"[]"),"p"))
        for raw,name in cases:
            with self.subTest(name=name,raw=raw),self.assertRaises(ValueError):runner.finite_field(raw,name,4)

    def test_seven_endpoint_fields_keep_native_hashes_and_exact_clock_with_empty_alpha_dimensions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);endpoint=root/"case/0.16";endpoint.mkdir(parents=True);original={}
            for name in runner.END_FIELDS:
                value="(0 0 0)" if name=="U" else "1" if name=="alpha.water" else "100" if name=="omega" else "0.375" if name=="k" else "0" if name=="nut" else "300000"
                raw=field(name,value)
                if name=="alpha.water":raw=raw.replace(b"[0 0 0 0 0 0 0]",b"[]")
                (endpoint/name).write_bytes(raw);original[name]=raw
            rows,times=runner.final_fields(root,{"endTimeS":.16},4,"Time = 0.16\nEnd\n")
            self.assertEqual(len(rows),7);self.assertEqual(times,[(.16,endpoint)])
            for row in rows:
                name=Path(row["path"]).name;self.assertEqual(row["sha256"],sha256(original[name]).hexdigest())
                self.assertEqual((root/row["path"]).read_bytes(),original[name]);self.assertTrue(row["fieldCheck"]["allInternalValuesChecked"])
            with self.assertRaisesRegex(ValueError,"physical end time"):runner.final_fields(root,{"endTimeS":.16},4,"Time = 0.15\nEnd\n")
            (endpoint/"alpha.water").unlink()
            with self.assertRaises(ValueError):runner.final_fields(root,{"endTimeS":.16},4,"Time = 0.16\nEnd\n")

    def test_g0_pressure_pair_checks_all_nonuniform_cells_without_atmospheric_addition(self):
        values=[300000,300001,299900,300500]
        result=runner.absolute_pressure_reconstruction(field("p",values,uniform=False),field("p_rgh",values,uniform=False),4)
        self.assertEqual(result["allInternalCellsChecked"],4);self.assertEqual(result["maximumAbsoluteDifferencePa"],0)
        self.assertEqual(result["atmosphericPressureAddedPa"],0)
        self.assertTrue(result["verified"])

    def test_uniform_pressure_pair_and_declared_arithmetic_roundoff_are_supported(self):
        result=runner.absolute_pressure_reconstruction(field("p"),field("p_rgh","300000.00000001"),4)
        self.assertEqual(result["allInternalCellsChecked"],4)
        self.assertLess(result["maximumAbsoluteDifferencePa"],1e-7)

    def test_pressure_offset_wrong_units_or_last_cell_mismatch_reject(self):
        cases=((field("p","401325"),field("p_rgh")),
            (field("p").replace(b"[1 -1 -2",b"[0 2 -2"),field("p_rgh")),
            (field("p",[300000]*3+[300001],uniform=False),field("p_rgh",[300000]*4,uniform=False)),
            (field("p","nan"),field("p_rgh")))
        for p,prgh in cases:
            with self.assertRaises(ValueError):runner.absolute_pressure_reconstruction(p,prgh,4)

    def test_authored_table_and_initial_ncc_roles_are_separate_unknown_vtk_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/"case/system").mkdir(parents=True);(root/"case/system/controlDict").write_bytes(b"massFlowInlet {type surfaceFieldValue;}\n")
            tables=root/"case/postProcessing/massFlowInlet/0";tables.mkdir(parents=True);(tables/"surfaceFieldValue.dat").write_bytes(b"# SYNTHETIC\n0 90\n")
            ncc=root/"case/postProcessing/checkMesh/0";ncc.mkdir(parents=True)
            for name in ("interface-impeller-outlet","interface-volute-inlet"):(ncc/(name+"_coverage.vtk")).write_bytes(b"SYNTHETIC-NCC")
            self.assertEqual(len(runner.sampling_records(root)),1);self.assertEqual(len(runner.ncc_diagnostic_records(root)),2)
            unknown=ncc/"unknown_coverage.vtk";unknown.write_bytes(b"never-accept")
            with self.assertRaises(ValueError):runner.sampling_records(root)

    def test_phase_near_points_replays_only_current_copied_authored_source_and_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            near_points_fixture(root,case="case",log="logs/00-checkMesh.log",source="source-baseline/source-teaching/source-geometry/teaching-geometry.json")
            (root/"case/system").mkdir();(root/"case/system/controlDict").write_bytes(b"massFlowInlet {type surfaceFieldValue;}\n")
            actual=runner.near_points_evidence(root)
            self.assertEqual(len(actual["files"]),2)
            self.assertEqual(actual["evidence"]["nativeNearPointCount"],6)
            self.assertEqual(actual["evidence"]["authoredGeometryReport"]["path"],"source-baseline/source-teaching/source-geometry/teaching-geometry.json")
            self.assertEqual(runner.postprocessing_records(root),([],[]))
            (root/"case/constant/polyMesh/sets/nearPoints").unlink()
            with self.assertRaises(ValueError):runner.postprocessing_records(root)

    def test_canonical_manifest_binds_actual_file_bytes_before_native_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/"input").write_bytes(b"SYNTHETIC-INPUT")
            bound={"manifest":{"files":[{**runner.record(root,"input")[1],"control":"fixture"}]},"study":{"syntheticFixture":True}}
            reducer=SimpleNamespace(build_manifest=lambda _:bound,encoded=lambda x:json.dumps(x,sort_keys=True,separators=(",",":")).encode())
            with patch.dict(sys.modules,{"reduce_teaching_pump_phase":reducer}):
                rows,_=runner.bind_manifest(root)
                self.assertEqual(rows[runner.STUDY]["sha256"],sha256((root/runner.STUDY).read_bytes()).hexdigest())
            other=root/"other";other.mkdir();bound["manifest"]["files"][0]["sha256"]="f"*64
            with patch.dict(sys.modules,{"reduce_teaching_pump_phase":reducer}),self.assertRaises(ValueError):runner.bind_manifest(other)


class SyntheticPhaseCampaignTests(unittest.TestCase):
    def campaign(self,root,*,failed_stage=None,missing_model=False,missing_fields=False,pressure_offset=False,mutate=None,bad_mesh=False,bad_log=False,generated_cache=False):
        """Mocked native readers only; no execution or phase H0 is produced."""
        source=root/"source";source.mkdir();(source/"source-proof").write_bytes(b"SYNTHETIC-PROOF");output=root/"phase"
        end=4*60/1450;components=[{"component":name,"cellCount":2,"nodeCount":5} for name in ("impeller","volute")]
        def prepare(*_):
            output.mkdir();(output/"case/system").mkdir(parents=True);(output/"case/0").mkdir();(output/"source-baseline").mkdir()
            inputs={"system/controlDict":b"massFlowInlet {type surfaceFieldValue;}\n","0/p_rgh":field("p_rgh"),"constant/fvModels":b"SYNTHETIC-SCHNERR-MODEL"}
            authored=[]
            for path,raw in inputs.items():
                target=output/"case"/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
                authored.append({**runner.record(output,"case/"+path)[1],"path":path})
            (output/"source-baseline/source-proof").write_bytes(b"SYNTHETIC-PROOF")
            (output/runner.IDENTITY).write_bytes(b'{"syntheticFixture":true}')
            packet={"schemaVersion":runner.PREPARATION_SCHEMA,"mode":runner.MODE,"inletAbsoluteTotalPressurePa":300000.,"nativeCommandArguments":runner.commands(output),
                "authoredInputFiles":authored,"preservedNativeMeshFiles":[],"components":components,"sourceEvidenceFileRecords":[runner.record(output,"source-baseline/source-proof")[1]],
                "sourceBaselineEvidenceSha256":"0"*64,"sourceCaseManifestSha256":"1"*64,"sourceGeometryControlSha256":"2"*64,"authoredCavitationModelSha256":"3"*64,
                "temporalPlan":{"endTimeS":end},"absolutePressureMapping":{"nativeGravityMS2":[0,0,0],"referenceAdditionPa":0}}
            (output/"teaching-phase-preparation.json").write_text(json.dumps(packet));return packet
        def verify(*_,after_native_execution=False):
            cache=output/"case/constant/fvMesh/polyFaces"
            if cache.exists() and not after_native_execution:raise ValueError("No generated cache admitted before native execution")
            return {"report":json.loads((output/"teaching-phase-preparation.json").read_bytes()),"reportFile":runner.record(output,"teaching-phase-preparation.json")[1],"components":components,
                "nativeGeneratedDataRecords":[runner.record(output,"case/constant/fvMesh/polyFaces")[1]] if cache.exists() else [],
                "nativeGeneratedDataEvidence":[{"path":"case/constant/fvMesh/polyFaces","dataOnlyVerified":True,"authoredInput":False,"syntheticFixture":True}] if cache.exists() else []}
        helper=SimpleNamespace(prepare=prepare,verify_output=verify,verify_baseline=lambda _: {"sourceRecords":[runner.record(source,"source-proof")[1]]})
        def runtime(*_):(output/runner.RUNTIME).write_bytes(b'{"syntheticFixture":true}');return {"syntheticFixture":True}
        def manifest(*_):
            rows={}
            for name in (runner.MANIFEST,runner.STUDY):(output/name).write_bytes(b'{"syntheticFixture":true}');rows[name]=runner.record(output,name)[1]
            return rows,[{**runner.record(output,"case/0/p_rgh")[1],"control":"fixture"}]
        def execute(args,workspace,deadline,identity,index):
            text="Build : 12-86e126a7bc4d\n"+("FOAM FATAL ERROR\n" if bad_log else "")+"End\n"
            if index==1 and not missing_model:text="Selecting cavitation model SchnerrSauer\n"+text
            path=output/"logs"/(f"{index:02d}-{args[0]}.log");path.write_text(text);row=runner.record(output,"logs/"+path.name)[1]
            if index==0:
                ncc=output/"case/postProcessing/checkMesh/0";ncc.mkdir(parents=True)
                for name in ("interface-impeller-outlet","interface-volute-inlet"):(ncc/(name+"_coverage.vtk")).write_bytes(b"SYNTHETIC-NCC")
            if mutate and index==1:
                if mutate=="original-source":(source/"source-proof").write_bytes(b"SYNTHETIC-MUTATION")
                else:(output/mutate).write_bytes(b"SYNTHETIC-MUTATION")
            return {"program":args[0],"exitCode":7 if index==failed_stage else 0,"elapsedSeconds":.1,"log":path.name,"logBytes":row["bytes"],"logSha256":row["sha256"]},text
        def final(*_):
            if missing_fields:raise ValueError("Actual endpoint fields missing")
            directory=output/"case"/format(end,".17g");directory.mkdir();rows=[]
            for name in runner.END_FIELDS:
                value="(0 0 0)" if name=="U" else "1" if name=="alpha.water" else "100" if name=="omega" else "0.375" if name=="k" else "0" if name=="nut" else "401325" if name=="p" and pressure_offset else "300000"
                raw=field(name,value);(directory/name).write_bytes(raw);row=runner.record(output,(directory/name).relative_to(output).as_posix())[1]
                rows.append({**row,"fieldCheck":runner.finite_field(raw,name,4)})
            tables=output/"case/postProcessing/massFlowInlet/0";tables.mkdir(parents=True);(tables/"surfaceFieldValue.dat").write_bytes(b"# SYNTHETIC\n0.16551724137931034 90\n")
            if generated_cache:
                folder=output/"case/constant/fvMesh";folder.mkdir();(folder/"polyFaces").write_bytes(b"SYNTHETIC-NATIVE-GENERATED-NCC-CACHE")
            return rows,[(end,directory)]
        with patch.dict(os.environ,{"RUNNER_TEMP":str(root)}),patch.dict(sys.modules,{"teaching_pump_phase_case":helper}),\
             patch.object(runner.safety,"context_guard"),patch.object(runner,"runtime_identity",side_effect=runtime),\
             patch.object(runner,"bind_manifest",side_effect=manifest),patch.object(runner,"execute",side_effect=execute),\
             patch.object(runner.baseline.geometry,"verify_region_count",return_value=2),\
             patch.object(runner.base,"verify_merged_mesh",side_effect=ValueError("Actual mesh failed") if bad_mesh else None,return_value={"mergedCellCount":4,"mergedPointCount":10}),\
             patch.object(runner.baseline.scientific,"parse_mesh_quality",return_value={"syntheticFixture":True}),patch.object(runner,"final_fields",side_effect=final),redirect_stdout(io.StringIO()):
            report=runner.run(source,output)
        return report,output

    def test_native_fact_is_separate_from_phase_h0_sweep_and_physical_transient(self):
        with tempfile.TemporaryDirectory() as temp:
            report,output=self.campaign(Path(temp))
            self.assertTrue(report["actualCfdRunVerified"]);self.assertTrue(report["cavitationModelExecuted"])
            self.assertTrue(report["simulationExecuted"]);self.assertEqual(len(report["finalNativeFieldRecords"]),7)
            self.assertEqual(report["endpointAbsolutePressureReconstruction"]["allInternalCellsChecked"],4)
            self.assertEqual(len(report["nativeNccDiagnosticRecords"]),2)
            self.assertTrue(all(report[key] is False for key in runner.FALSE_FLAGS))
            self.assertFalse((output/"teaching-phase-acceptance.json").exists())
            self.assertEqual(json.loads((output/runner.REPORT).read_bytes()),report)

    def test_final_native_ncc_cache_is_retained_as_data_without_input_promotion(self):
        with tempfile.TemporaryDirectory() as temp:
            report,output=self.campaign(Path(temp),generated_cache=True)
            self.assertTrue(report["actualCfdRunVerified"])
            self.assertEqual(report["nativeGeneratedDataRecords"],[runner.record(output,"case/constant/fvMesh/polyFaces")[1]])
            self.assertFalse(report["nativeGeneratedDataEvidence"][0]["authoredInput"])
            self.assertFalse(report["phaseH0Eligible"])

    def test_failed_mesh_or_native_exit_stops_dependent_phase_solve(self):
        for kwargs in ({"bad_mesh":True},{"failed_stage":0},{"bad_log":True}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as temp:report,_=self.campaign(Path(temp),**kwargs)
            self.assertFalse(report["actualCfdRunVerified"]);self.assertFalse(report["nativeSolverCompleted"])
            self.assertEqual(len(report["stages"]),1)

    def test_missing_active_model_endpoint_or_pressure_identity_cannot_claim_cfd(self):
        for kwargs in ({"missing_model":True},{"missing_fields":True},{"pressure_offset":True}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as temp:report,_=self.campaign(Path(temp),**kwargs)
            self.assertFalse(report["actualCfdRunVerified"]);self.assertTrue(report["nativeSolverCompleted"])
            self.assertFalse(report["finiteNativeEndFieldsVerified"])

    def test_bound_source_mesh_model_identity_initial_state_and_ncc_cannot_change(self):
        for relative in ("case/0/p_rgh","case/constant/fvModels","source-baseline/source-proof",runner.IDENTITY,runner.MANIFEST,"logs/00-checkMesh.log",
                         "case/postProcessing/checkMesh/0/interface-volute-inlet_coverage.vtk","original-source"):
            with self.subTest(relative=relative),tempfile.TemporaryDirectory() as temp:report,_=self.campaign(Path(temp),mutate=relative)
            self.assertFalse(report["actualCfdRunVerified"]);self.assertFalse(report["inputArtifactsImmutable"])

    def test_cli_uses_standing_workflow_baseline_directory_and_exposes_diagnostic(self):
        report={key:False for key in ("simulationExecuted","actualCfdRunVerified","cavitationModelExecuted","phaseH0Eligible","npsh3Qualified","physicalTransientExecuted","mainApplicationWrites")}
        report.update(status="TEACHING_ACTIVE_PHASE_REFERENCE_CORRECTION_REQUIRED",diagnostic={"message":"Synthetic precise native cause"})
        with patch.object(sys,"argv",["phase","--baseline-directory","source","--output-directory","target"]),patch.object(runner,"run",return_value=report) as run,redirect_stdout(io.StringIO()) as output:
            self.assertEqual(runner.main(),2)
            self.assertEqual(run.call_args.args,(Path("source"),Path("target")))
            self.assertEqual(json.loads(output.getvalue())["diagnostic"],report["diagnostic"])


if __name__=="__main__":unittest.main()
