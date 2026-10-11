"""Synthetic bounded-transfer/process fixtures only; no CFD is executed."""
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]/"tools"
spec=importlib.util.spec_from_file_location("original_runner",TOOLS/"run_frozen_rotor_original.py")
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
case=runner.preparation


def converted_fixture(root):
    """Tiny nonphysical inventories exercise transfer gates, not geometry quality."""
    root.mkdir();components=[];counts={};total=0
    for component in ("impeller","volute"):
        names=sorted(case.base.PATCHES[component]);folder=component+"-case";n=len(names)
        counts[component]=(2,4,n+1)
        patches=[{"name":name,"type":"wall" if name in case.base.ROTATING|case.base.STATIONARY else "patch",
                  "nFaces":1,"startFace":index+1,"sourceZoneId":index+1,"sourceMinimumFaceId":index+2,
                  "sourceMaximumFaceId":index+2,"reversedFaceCount":1} for index,name in enumerate(names)]
        role="rotating-fluid" if component=="impeller" else "stationary-fluid"
        contents={"points":("vectorField","4\n((0 0 0)(1 0 0)(0 1 0)(0 0 1))"),
                  "faces":("faceList",str(n+1)+"\n("+"3(0 1 2)\n"*(n+1)+")"),
                  "owner":("labelList",str(n+1)+"\n("+"0\n"*(n+1)+")"),"neighbour":("labelList","1\n(1)"),
                  "boundary":("polyBoundaryMesh",str(n)+"\n("+"\n".join(p["name"]+" {type "+p["type"]+"; nFaces 1; startFace "+str(p["startFace"])+";}" for p in patches)+")"),
                  "cellZones":("regIOobject","1\n("+role+" {type cellZone; cellLabels List<label> 2(0 1);})")}
        files=[]
        for name,(kind,body) in contents.items():
            raw=(case.base.foam_header(name,kind)+body+"\n").encode("ascii")
            relative=folder+"/constant/polyMesh/"+name
            case.base.write_exclusive(root/relative,raw);total+=len(raw)
            files.append({"path":relative,"bytes":len(raw),"sha256":sha256(raw).hexdigest()})
        raw=(case.base.foam_header("sourceFaceIds","labelList")+str(n+1)+"\n("+" ".join(str(i+1) for i in range(n+1))+")\n").encode("ascii")
        relative=folder+"/sourceFaceIds";case.base.write_exclusive(root/relative,raw);total+=len(raw)
        source_bytes,source_sha=case.base.inventory.FILES[component+".msh.h5"]
        components.append({"name":component,"casePath":folder,"cellCount":2,"nodeCount":4,"faceCount":n+1,
            "internalFaceCount":1,"boundaryFaceCount":n,"patches":patches,"cellZone":{"name":role,"sourceName":component,"sourceZoneId":36 if component=="impeller" else 2,"cellCount":2},
            "sourceFile":{"name":component+".msh.h5","bytes":source_bytes,"sha256":source_sha,"sourceUrl":case.base.inventory.mesh_url(component+".msh.h5")},
            "nativeFiles":files,"sourceFaceIdMapFile":{"path":relative,"bytes":len(raw),"sha256":sha256(raw).hexdigest()},
            "checks":{**{key:True for key in ("allOriginalCellsChecked","directedEdgeIncidenceClosed","standardCellShapeIncidenceVerified",
                "allCoordinatesAsciiRoundTripVerified","everyOriginalFaceTransferredExactlyOnce","originalPolygonsRetained","ownerNeighbourOrderingVerified")},
                "originalCellCountChecked":2,"minimumCellVolumeAssumedM3":1,"maximumCellVolumeAssumedM3":1,"totalCellVolumeAssumedM3":2,
                "maximumRelativeAreaVectorClosureError":1e-14,"relativeAreaClosureTolerance":1e-10,"internalFaceCount":1,
                "cellBoundaryVolumeAgreementVerified":True,"boundarySignedVolumeAssumedM3":2,"relativeCellBoundaryVolumeDifference":0},"cellTypeCounts":{"fixture-only":2}})
    report={"schemaVersion":case.CONVERSION_SCHEMA,"exampleDataCommit":case.base.inventory.PIN,"components":components,
        "combinedCounts":{"cellCount":4,"nodeCount":8,"faceCount":sum(row[2] for row in counts.values())},"derivedBytes":total,"coordinateUnits":"UNVERIFIED",
        "originalMeshesImmutable":True,"volumeMeshConverted":True,"nativeCoordinateUnitsVerified":False,"classroomMetresPerSourceCoordinateUnit":1,
        **{key:False for key in ("meshQualityCheckedBySolver","nativeCaseGenerated","simulationExecuted","actualCfdRunVerified","mainApplicationWrites")}}
    (root/"native-conversion.json").write_bytes(case.base.inventory.bounded_json(report))
    return counts,report


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


class OriginalCaseContracts(unittest.TestCase):
    def test_original_budget_is_distinct_and_does_not_change_lighter_guard(self):
        self.assertEqual(case.base.MAX_COMBINED_CELLS,1_000_000)
        self.assertEqual(case.MAX_COMBINED_CELLS,2_000_000)
        self.assertEqual(case.EXPECTED_COUNTS["impeller"],(773352,837936,2383488))
        self.assertEqual(case.EXPECTED_COUNTS["volute"],(368411,122551,831758))

    def test_pressure_reference_is_explicit_and_recipe_preserves_other_physics(self):
        baseline=case.base.case_files("impeller")
        original=case.case_files("impeller",300000)
        self.assertEqual(original["system/controlDict"].replace("pRef 300000;","pRef 101325;"),baseline["system/controlDict"])
        self.assertEqual({key:value for key,value in original.items() if key!="system/controlDict"},
                         {key:value for key,value in baseline.items() if key not in {"system/controlDict","system/changeDictionaryDict"}})
        self.assertNotIn("system/changeDictionaryDict",original)
        self.assertEqual(case.base.P_ABSOLUTE,101325)
        for value in (None,True,0,2339,float("nan"),float("inf"),10_000_001):
            with self.subTest(value=value),self.assertRaises(case.base.CaseIssue):case.case_files("impeller",value)

    def test_transfer_keeps_source_files_immutable_and_authors_exact_four_stages(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);counts,source=converted_fixture(root/"converted")
            before={p.relative_to(root/"converted").as_posix():p.read_bytes() for p in (root/"converted").rglob("*") if p.is_file()}
            with patch.object(case,"EXPECTED_COUNTS",counts):
                prepared=case.prepare(root/"converted",root/"execution",pressure_reference_absolute_pa=450000)
            self.assertEqual([row[0] for row in prepared["nativeCommandArguments"]],["mergeMeshes","createNonConformalCouples","checkMesh","foamRun"])
            self.assertEqual(prepared["nativeCommandArguments"][2][-3:],["-allGeometry","-allTopology","-writeSets"])
            self.assertEqual(prepared["limits"]["memoryBytes"],4*1024**3)
            self.assertEqual(prepared["limits"]["maximumWorkspaceBytes"],1024**3)
            self.assertEqual(prepared["classroomAssumptions"]["externalTotalPressureAbsolutePa"],450000)
            self.assertFalse(prepared["classroomAssumptions"]["nativeUnitsVerified"])
            self.assertFalse(prepared["actualCfdRunVerified"]);self.assertFalse(prepared["qualifiedBaseline"])
            for path,raw in before.items():
                self.assertEqual((root/"converted"/path).read_bytes(),raw)
                self.assertEqual((root/"execution"/path).read_bytes(),raw)
            with self.assertRaises((case.base.CaseIssue,case.base.inventory.InspectionIssue)),patch.object(case,"EXPECTED_COUNTS",counts):case.prepare(root/"converted",root/"execution")

    def test_source_pin_map_hash_count_and_cell_closure_reject_before_output(self):
        mutations=[lambda r:r["components"][0]["sourceFile"].update(sha256="0"*64),
                   lambda r:r["components"][0].update(cellCount=3),
                   lambda r:r["components"][0]["checks"].update(directedEdgeIncidenceClosed=False),
                   lambda r:r["components"][0]["sourceFaceIdMapFile"].update(sha256="0"*64),
                   lambda r:r["components"][0]["checks"].update(cellBoundaryVolumeAgreementVerified=False),
                   lambda r:r["components"][0]["checks"].update(relativeCellBoundaryVolumeDifference=1e-5),
                   lambda r:r.update(derivedBytes=r["derivedBytes"]+1),
                   lambda r:r["components"][0]["patches"][0].update(type="patch")]
        for mutation in mutations:
            with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
                root=Path(temp);counts,source=converted_fixture(root/"converted");mutation(source)
                (root/"converted/native-conversion.json").write_bytes(case.base.inventory.bounded_json(source))
                with patch.object(case,"EXPECTED_COUNTS",counts),self.assertRaises(case.base.CaseIssue):case.prepare(root/"converted",root/"output")
                self.assertFalse((root/"output").exists())

    def test_unapproved_source_or_target_path_rejects_before_mutation(self):
        with tempfile.TemporaryDirectory() as temp,tempfile.TemporaryDirectory() as outside,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);counts,_=converted_fixture(root/"converted")
            with patch.object(case,"EXPECTED_COUNTS",counts),self.assertRaises(case.base.inventory.InspectionIssue):case.prepare(root/"converted",Path(outside)/"output")
            self.assertFalse((Path(outside)/"output").exists())


class OriginalProcessContracts(unittest.TestCase):
    def fixture(self,root,payload=b"End\n",code=0,clock=None):
        output=root/"execution";(output/"logs").mkdir(parents=True)
        raw=b"SYNTHETIC-EXECUTABLE-NOT-NATIVE-CFD"
        runtime={"binaries":[{"program":"foamRun","path":"/opt/openfoam12/bin/foamRun","bytes":len(raw),"sha256":sha256(raw).hexdigest()}]}
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(runner,"print",create=True))
        original_read=runner.BASE.read_regular
        stack.enter_context(patch.object(runner,"Path",side_effect=lambda value:Path(value).resolve() if str(value).startswith("/opt/") else Path(value)))
        stack.enter_context(patch.object(runner.BASE,"read_regular",side_effect=lambda path,limit:raw if path.name=="foamRun" else original_read(path,limit)))
        stack.enter_context(patch.object(runner.selectors,"DefaultSelector",Selector))
        stack.enter_context(patch.object(runner.os,"read",side_effect=[payload,b""]))
        stack.enter_context(patch.object(runner.time,"monotonic",side_effect=clock if clock else lambda:1))
        launch=stack.enter_context(patch.object(runner.subprocess,"Popen",return_value=Process(code)))
        stop=stack.enter_context(patch.object(runner.native,"stop_owned"))
        return output,runtime,launch,stop,lambda:runner.execute(case.commands(output)[3],output/"logs/03-foamRun.log",output,2,runtime)

    def test_capture_is_hash_bound_owned_serial_and_credential_free(self):
        with tempfile.TemporaryDirectory() as temp:
            output,runtime,launch,stop,invoke=self.fixture(Path(temp))
            with patch.dict(os.environ,{"GH_TOKEN":"SYNTHETIC","PATH":"native-path","FLUENT_LICENSE":"SYNTHETIC"},clear=True):
                row,text=invoke()
            self.assertEqual(text,"End\n");self.assertEqual(row["logSha256"],sha256(b"End\n").hexdigest())
            self.assertEqual(launch.call_args.kwargs["preexec_fn"],runner.child_limits)
            self.assertTrue(launch.call_args.kwargs["start_new_session"])
            self.assertFalse(set(launch.call_args.kwargs["env"])&{"GH_TOKEN","FLUENT_LICENSE"})
            self.assertEqual(launch.call_args.kwargs["env"]["OMP_NUM_THREADS"],"1");stop.assert_called_once()

    def test_failure_fatal_missing_end_timeout_and_fast_completion_timeout_reject(self):
        cases=[(b"End\n",2,None),(b"FOAM FATAL ERROR\nEnd\n",0,None),(b"not complete\n",0,None),
               (b"End\n",0,[1,1,2]),(b"End\n",0,[1,1,1,1,3])]
        for payload,code,clock in cases:
            with self.subTest(payload=payload,clock=clock),tempfile.TemporaryDirectory() as temp:
                _,_,_,stop,invoke=self.fixture(Path(temp),payload,code,clock)
                with self.assertRaises(case.base.CaseIssue):invoke()
                stop.assert_called_once()

    def test_log_workspace_and_command_bounds_reject(self):
        with tempfile.TemporaryDirectory() as temp:
            output,_,_,stop,invoke=self.fixture(Path(temp),b"X"*10)
            with patch.object(runner,"MAX_LOG",9),self.assertRaises(case.base.CaseIssue):invoke()
            self.assertEqual((output/"logs/03-foamRun.log").stat().st_size,9);stop.assert_called_once()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/"file").write_bytes(b"12345")
            with patch.object(runner,"MAX_WORKSPACE",4),self.assertRaises(case.base.CaseIssue):runner.workspace_size(root)
            with patch.object(runner.BASE,"MAX_NATIVE_BYTES",4),self.assertRaises(case.base.CaseIssue):runner.workspace_size(root)
        with tempfile.TemporaryDirectory() as temp:
            output,runtime,launch,_,_=self.fixture(Path(temp))
            with self.assertRaises(case.base.CaseIssue):runner.execute(["bash","script.sh"],output/"logs/03-foamRun.log",output,2,runtime)
            launch.assert_not_called()

    def test_actual_public_manual_repository_event_gate(self):
        for repo in ({"full_name":"simcalc/PyAnsys","private":False},{"full_name":"simcalc/PyAnsys","private":True},{"full_name":"simcalc/npsh","private":False}):
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/"event.json";path.write_text(json.dumps({"repository":repo}))
                with patch.object(runner.native,"context_guard"),patch.dict(os.environ,{"GITHUB_EVENT_PATH":str(path)}):
                    if repo.get("full_name")=="simcalc/PyAnsys" and repo.get("private") is False:runner.context_guard()
                    else:
                        with self.assertRaises(case.base.CaseIssue):runner.context_guard()

    def test_exact_installed_package_and_binary_identity_are_bound_before_execution(self):
        raw=b"SYNTHETIC-INSTALLED-BINARY-IDENTITY-NOT-CFD"
        for package,path_prefix in (("20250206","/opt/openfoam12/bin/"),("changed-version","/opt/openfoam12/bin/"),("20250206","/tmp/")):
            with self.subTest(package=package,path=path_prefix),tempfile.TemporaryDirectory() as temp,ExitStack() as stack:
                root=Path(temp)
                stack.enter_context(patch.object(runner.subprocess,"run",return_value=SimpleNamespace(stdout=package)))
                stack.enter_context(patch.object(runner.shutil,"which",side_effect=lambda name:path_prefix+name))
                stack.enter_context(patch.object(runner,"Path",side_effect=lambda value:Path(value).resolve() if str(value).startswith("/opt/") else Path(value)))
                original_read=runner.BASE.read_regular
                stack.enter_context(patch.object(runner.BASE,"read_regular",side_effect=lambda path,limit:raw if path.name in runner.PROGRAMS else original_read(path,limit)))
                if package=="20250206" and path_prefix.startswith("/opt/"):
                    identity=runner.runtime_identity(root)
                    self.assertEqual({row["program"] for row in identity["binaries"]},set(runner.PROGRAMS))
                    self.assertTrue(all(row["bytes"]==len(raw) and row["sha256"]==sha256(raw).hexdigest() for row in identity["binaries"]))
                else:
                    with self.assertRaises(case.base.CaseIssue):runner.runtime_identity(root)
                    self.assertFalse((root/"runtime-identity.json").exists())

    def test_child_address_space_is_four_gib_and_binary_mutation_prevents_launch(self):
        import sys
        calls=[];fake=SimpleNamespace(RLIMIT_AS=1,RLIMIT_CORE=2,setrlimit=lambda key,value:calls.append((key,value)))
        with patch.dict(sys.modules,{"resource":fake}):runner.child_limits()
        self.assertEqual(calls,[(1,(4*1024**3,4*1024**3)),(2,(0,0))])
        with tempfile.TemporaryDirectory() as temp:
            _,runtime,launch,stop,invoke=self.fixture(Path(temp))
            runtime["binaries"][0]["sha256"]="0"*64
            with self.assertRaises(case.base.CaseIssue):invoke()
            launch.assert_not_called();stop.assert_not_called()

    def test_every_primary_internal_value_is_checked_at_actual_endpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);directory=root/"impeller-case/500";directory.mkdir(parents=True)
            for name,dimension,initial in (("p","0 2 -2 0 0 0 0","0"),("U","0 1 -1 0 0 0 0","(0 0 0)"),
                                         ("k","0 2 -2 0 0 0 0",".375"),("omega","0 0 -1 0 0 0 0","100"),("nut","0 2 -1 0 0 0 0","0")):
                (directory/name).write_text(case.base.field(name,dimension,initial,{}).replace('#includeEtc "caseDicts/setConstraintTypes"',""))
            primary,rows,scans=runner.final_fields(root,2)
            self.assertEqual({row["name"] for row in primary},{"p","U","k","omega"})
            self.assertEqual(len(rows),5);self.assertEqual(len(scans),5)
            (directory/"k").write_text((directory/"k").read_text().replace("internalField uniform .375;","internalField nonuniform List<scalar> 2(0.1 nan);"))
            with self.assertRaises(case.base.CaseIssue):runner.final_fields(root,2)


class OriginalNativeContractIntegration(unittest.TestCase):
    def fixture(self,root,mutation=None,qualified=False):
        counts,_=converted_fixture(root/"converted")
        original_execute=runner.execute
        def runtime(output):
            value={"schemaVersion":"untirta.openfoam-native-runtime-identity.v1","openFoamFoundationVersion":"12","packageVersion":"20250206",
                   "officialDebSha256":"b3dcd9a636a5a827bf43ffcf0156922ba6db492123f035eae1046ee759602dd1",
                   "binaries":[],"mainApplicationWrites":False}
            case.base.write_exclusive(output/"runtime-identity.json",case.base.inventory.bounded_json(value));return value
        def execute(arguments,log,output,deadline,identity):
            raw=b"SYNTHETIC-NATIVE-PROCESS-TEST-NOT-CFD\nEnd\n";case.base.write_exclusive(log,raw)
            if arguments[0]=="foamRun":
                directory=output/"impeller-case/500";directory.mkdir()
                for name in ("p","U","k","omega","nut"):
                    text=(output/"impeller-case/0"/name).read_text().replace('#includeEtc "caseDicts/setConstraintTypes"',"")
                    (directory/name).write_text(text)
                if mutation:
                    path=output/mutation;path.write_bytes(path.read_bytes()+b"\n// SYNTHETIC-BYTE-MUTATION\n")
            return {"program":arguments[0],"exitCode":0,"log":log.name,"logBytes":len(raw),"logSha256":sha256(raw).hexdigest()},raw.decode()
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(runner,"print",create=True))
        stack.enter_context(patch.dict(os.environ,{"RUNNER_TEMP":str(root)}))
        stack.enter_context(patch.object(case,"EXPECTED_COUNTS",counts))
        stack.enter_context(patch.object(runner,"context_guard"))
        stack.enter_context(patch.object(runner,"runtime_identity",side_effect=runtime))
        stack.enter_context(patch.object(runner,"execute",side_effect=execute))
        stack.enter_context(patch.object(runner.BASE,"verify_merged_mesh",return_value={"syntheticFixtureNotCFD":True}))
        stack.enter_context(patch.object(runner.reduction,"binary_identity"))
        stack.enter_context(patch.object(runner.reduction,"parse_solver_log",return_value={500:{}}))
        stack.enter_context(patch.object(runner.reduction,"reduce_native_baseline",return_value={"schemaVersion":"untirta.frozen-rotor-native-baseline-reduction.v1",
            "qualifiedBaseline":qualified,"syntheticFixtureNotCFD":True}))
        return lambda:runner.run(root/"converted",root/"execution",pressure_reference_absolute_pa=300000)

    def test_actual_preparer_manifest_contract_and_raw_facts_stay_separate_from_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);invoke=self.fixture(root,qualified=True);self.assertEqual(invoke(),0)
            raw=json.loads((root/"execution/openfoam-execution.json").read_text())
            acceptance=json.loads((root/"execution/openfoam-acceptance.json").read_text())
            manifest=json.loads((root/"execution/native-input-manifest.json").read_text())
            self.assertEqual(raw["schemaVersion"],runner.SCHEMA)
            self.assertEqual([row["program"] for row in raw["stages"]],["mergeMeshes","createNonConformalCouples","checkMesh","foamRun"])
            self.assertEqual(manifest["sourceGeometryRoute"],"original-volume-polyMesh-transfer")
            self.assertEqual(manifest["pressureLevelAbsolutePa"],300000)
            self.assertTrue(raw["inputArtifactsImmutable"]);self.assertFalse(raw["qualifiedBaseline"])
            self.assertTrue(acceptance["qualifiedBaseline"])
            self.assertEqual(acceptance["nativeExecutionReport"]["sha256"],sha256((root/"execution/openfoam-execution.json").read_bytes()).hexdigest())
            self.assertFalse(raw["physicalTransientExecuted"]);self.assertFalse(raw["npsh3Qualified"])

    def test_source_geometry_and_authored_input_mutation_never_promote_native_claim(self):
        for relative in ("geometry-identity.json","impeller-case/constant/MRFProperties","native-conversion.json"):
            with self.subTest(relative=relative),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);invoke=self.fixture(root,mutation=relative);self.assertEqual(invoke(),2)
                raw=json.loads((root/"execution/openfoam-execution.json").read_text())
                self.assertFalse(raw["actualCfdRunVerified"]);self.assertFalse(raw["inputArtifactsImmutable"])
                self.assertNotIn("nativeReduction",raw)
                self.assertEqual(raw["diagnostic"]["program"],"foamRun")
                self.assertEqual(len(raw["retainedNativeLogRecords"]),4)


if __name__=="__main__":unittest.main()
