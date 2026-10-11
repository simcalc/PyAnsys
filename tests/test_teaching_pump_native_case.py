"""Synthetic contracts only; fixtures are not generated teaching CAD/native CFD."""
import io
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import numpy as np  # Load before temporary sys.modules patches; never reload it.

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import teaching_pump_native_case as case
import run_teaching_pump_geometry as runner


def mesh(component, *, offset=None, reverse_interface=False):
    x=0 if component=="impeller" else 1
    coords=[(x+a,b,c) for a,b,c in ((0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1))]
    if offset is not None:coords=[(a+offset,b,c) for a,b,c in coords]
    cells=[(0,1,2,6),(0,2,3,6),(0,3,7,6),(0,7,4,6),(0,4,5,6),(0,5,1,6)]
    occurrences={}
    for tet in cells:
        for slots in ((0,2,1),(0,1,3),(1,2,3),(2,0,3)):
            face=tuple(tet[i] for i in slots);occurrences.setdefault(tuple(sorted(face)),[]).append(face)
    exterior=[rows[0] for rows in occurrences.values() if len(rows)==1]
    interface="interface-impeller-outlet" if component=="impeller" else "interface-volute-inlet"
    names=sorted(case.base.PATCHES[component]-{interface});tags={p:i+1 for i,p in enumerate([interface,*names])};volume=100
    groups=[f'2 {tag} "{p}"' for p,tag in tags.items()]+[f'3 {volume} "'+("rotating-fluid" if component=="impeller" else "stationary-fluid")+'"']
    rows=[];index=0
    for face in exterior:
        shared=all(coords[i][0]==1+(offset or 0) for i in face)
        patch=interface if shared else names[index%len(names)]
        if not shared:index+=1
        if shared and reverse_interface:face=face[::-1]
        rows.append('2 2 '+str(tags[patch])+' '+str(tags[patch])+' '+' '.join(str(i+1) for i in face))
    rows+=['4 2 100 100 '+' '.join(str(i+1) for i in tet) for tet in cells]
    elements=[str(i+1)+' '+row for i,row in enumerate(rows)]
    return ('$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$PhysicalNames\n'+str(len(groups))+'\n'+'\n'.join(groups)+
        '\n$EndPhysicalNames\n$Nodes\n8\n'+'\n'.join(str(i+1)+' '+' '.join(map(str,p)) for i,p in enumerate(coords))+
        '\n$EndNodes\n$Elements\n'+str(len(elements))+'\n'+'\n'.join(elements)+'\n$EndElements\n').encode('ascii')


def source_fixture(root):
    source=root/"source";source.mkdir();components=[]
    for name in case.COMPONENTS:
        raw=mesh(name);(source/(name+".msh")).write_bytes(raw);actual=case.base.msh_inventory(raw)
        components.append({"component":name,"cellZone":actual["cellZone"],"cellCount":actual["cellCount"],"nodeCount":actual["nodeCount"],
            "patchFaces":actual["patchFaces"],"patchTypes":{p:"wall" if p in case.base.ROTATING|case.base.STATIONARY else "patch" for p in actual["patchFaces"]},
            "generatedFile":{"name":name+".msh","bytes":len(raw),"sha256":sha256(raw).hexdigest(),"format":"gmsh-msh2.2-ascii"},
            "positiveTetrahedraVerified":True,"finiteCoordinatesVerified":True})
    report={"schemaVersion":"untirta.teaching-pump-geometry.v1","provenance":case.SOURCE_ROUTE,"units":"SI",
        "generatorSourceCommit":"a"*40,"parametersSha256":"b"*64,"inputManifestSha256":"c"*64,"components":components,
        "parameters":{"speedRpm":1450.,"massFlowKgS":90.}}
    (source/"teaching-geometry.json").write_bytes(case.encoded(report));(source/"teaching-input-manifest.json").write_bytes(b'{"synthetic":true}\n')
    (source/"teaching-pump.brep").write_bytes(b'SYNTHETIC-NOT-ACTUAL-OCC\n')
    def verify(directory):
        packet=json.loads((Path(directory)/"teaching-geometry.json").read_bytes())
        return {"report":packet,"reportFile":case.record(directory,"teaching-geometry.json")[1],"components":packet["components"]}
    return source,SimpleNamespace(SCHEMA=report["schemaVersion"],verify_output=verify)


class TeachingNativeTests(unittest.TestCase):
    def test_geometry_recipe_contains_no_flow_solver_and_keeps_all_wall_roles(self):
        for name in case.COMPONENTS:
            files=case.case_files(name)
            self.assertFalse(any(p.startswith("0/") for p in files))
            self.assertFalse(any("foamRun" in text for text in files.values()))
            self.assertNotIn("MRFProperties","\n".join(files))
            for wall in case.base.PATCHES[name]&(case.base.ROTATING|case.base.STATIONARY):self.assertIn('"'+wall+'" {type wall;}',files["system/changeDictionaryDict"])
        self.assertIn("fields false;",case.case_files("impeller")["system/createNonConformalCouplesDict"])
        args=case.commands(Path("fresh"));self.assertEqual(len(args),9)
        self.assertEqual([a[0] for a in args],["gmshToFoam","changeDictionary","checkMesh"]*2+["mergeMeshes","createNonConformalCouples","checkMesh"])
        self.assertTrue(all(a[-3:]==["-allGeometry","-allTopology","-writeSets"] for a in args if a[0]=="checkMesh"))
        self.assertEqual(runner.PROGRAMS,("gmshToFoam","changeDictionary","checkMesh","mergeMeshes","createNonConformalCouples"))

    def test_actual_small_fixture_interface_and_incidence_are_independently_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry=source_fixture(root)
            with mock.patch.dict(os.environ,{"RUNNER_TEMP":temp}),mock.patch.dict(sys.modules,{"teaching_pump_geometry":geometry}):
                verified=case.validate_inputs(source)
                self.assertEqual([r["cellCount"] for r in verified["components"]],[6,6])
                self.assertEqual(verified["commonInterfaceEvidence"]["triangleCountPerSide"],2)
                self.assertTrue(all(r["tetraFaceIncidence"]["accepted"] for r in verified["components"]))

    def test_raw_interface_requires_outward_owner_winding(self):
        with self.assertRaises(case.TeachingCaseIssue):case.interface_inventory(mesh("impeller",reverse_interface=True),"interface-impeller-outlet")

    def test_preparation_replay_and_relocation_do_not_use_historical_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry=source_fixture(root)
            with mock.patch.dict(os.environ,{"RUNNER_TEMP":temp}),mock.patch.dict(sys.modules,{"teaching_pump_geometry":geometry}):
                output=root/"native";prepared=case.prepare(source,output)
                self.assertEqual(case.verify_output(output)["report"],prepared)
                self.assertTrue(all(prepared[key] is False for key in case.FALSE_FLAGS))
                moved=root/"relocated";output.rename(moved);source.rename(root/"unavailable-original")
                self.assertEqual(case.verify_output(moved)["report"],prepared)
                target=moved/"impeller-case/system/controlDict";raw=target.read_bytes();target.write_bytes(raw.replace(b"writePrecision 17",b"writePrecision 16"))
                with self.assertRaises(case.TeachingCaseIssue):case.verify_output(moved)

    def test_bad_source_route_units_code_or_missing_patch_reject_before_output(self):
        for key,value in (("schemaVersion","untirta.frozen-rotor-classroom-remesh.v1"),("provenance","MANUFACTURER"),("units","mm"),("generatorSourceCommit","unknown")):
            with self.subTest(key=key),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source,geometry=source_fixture(root);report=json.loads((source/"teaching-geometry.json").read_bytes());report[key]=value
                (source/"teaching-geometry.json").write_bytes(case.encoded(report))
                with mock.patch.dict(os.environ,{"RUNNER_TEMP":temp}),mock.patch.dict(sys.modules,{"teaching_pump_geometry":geometry}):
                    with self.assertRaises(case.TeachingCaseIssue):case.prepare(source,root/"native")
                    self.assertFalse((root/"native").exists())

    def test_declared_operating_scale_uses_actual_custom_parameters(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry=source_fixture(root);path=source/"teaching-geometry.json";packet=json.loads(path.read_bytes())
            packet["parameters"].update(speedRpm=1800.,massFlowKgS=40.);path.write_bytes(case.encoded(packet))
            with mock.patch.dict(os.environ,{"RUNNER_TEMP":temp}),mock.patch.dict(sys.modules,{"teaching_pump_geometry":geometry}):
                prepared=case.prepare(source,root/"native")
                self.assertEqual(prepared["declaredOperatingScale"],{"rotationalSpeedRpm":1800.,"massFlowKgS":40.,"physicsInputsAuthored":False})

    def test_actual_changed_mesh_size_hash_or_tags_reject(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry=source_fixture(root)
            with mock.patch.dict(os.environ,{"RUNNER_TEMP":temp}),mock.patch.dict(sys.modules,{"teaching_pump_geometry":geometry}):
                original=(source/"impeller.msh").read_bytes();(source/"impeller.msh").write_bytes(original+b'\n')
                with self.assertRaises(case.TeachingCaseIssue):case.validate_inputs(source)
                (source/"impeller.msh").write_bytes(original)
                path=source/"teaching-geometry.json";packet=json.loads(path.read_bytes());packet["components"][0]["patchFaces"].pop("blade");path.write_bytes(case.encoded(packet))
                with self.assertRaises(case.TeachingCaseIssue):case.validate_inputs(source)

    def test_exact_shared_interface_mismatch_is_not_healed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry=source_fixture(root);raw=mesh("volute",offset=.000001);(source/"volute.msh").write_bytes(raw)
            path=source/"teaching-geometry.json";packet=json.loads(path.read_bytes());packet["components"][1]["generatedFile"].update(bytes=len(raw),sha256=sha256(raw).hexdigest());path.write_bytes(case.encoded(packet))
            with mock.patch.dict(os.environ,{"RUNNER_TEMP":temp}),mock.patch.dict(sys.modules,{"teaching_pump_geometry":geometry}):
                with self.assertRaises(case.TeachingCaseIssue):case.validate_inputs(source)

    def test_inconsistent_historical_geometry_command_rejects(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry=source_fixture(root)
            with mock.patch.dict(os.environ,{"RUNNER_TEMP":temp}),mock.patch.dict(sys.modules,{"teaching_pump_geometry":geometry}):
                output=root/"native";case.prepare(source,output);path=output/case.REPORT;packet=json.loads(path.read_bytes());packet["nativeCommandArguments"][-1][2]+="-other";path.write_bytes(case.encoded(packet))
                with self.assertRaises(case.TeachingCaseIssue):case.verify_output(output)

    def test_safe_scope_and_fresh_output_are_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry=source_fixture(root)
            with mock.patch.dict(os.environ,{"RUNNER_TEMP":temp}),mock.patch.dict(sys.modules,{"teaching_pump_geometry":geometry}):
                with self.assertRaises(case.TeachingCaseIssue):case.prepare(source,source/"child")
                (root/"existing").mkdir()
                with self.assertRaises(Exception):case.prepare(source,root/"existing")
                with self.assertRaises(case.TeachingCaseIssue):case.record(source,"../teaching-geometry.json")

    def test_runtime_binds_exact_five_installed_binaries_not_foamrun(self):
        def query(args,**kwargs):
            text="20250206" if args[1]=="--show" else "openfoam12: "+args[-1]+"\n"
            self.assertNotIn("GITHUB_TOKEN",kwargs["env"]);return SimpleNamespace(stdout=text,stderr="")
        with mock.patch.object(runner.subprocess,"run",side_effect=query),mock.patch.object(runner.shutil,"which",side_effect=lambda n:"/opt/openfoam12/bin/"+n),\
            mock.patch.object(runner.safety,"descriptor",return_value=(b"native",{"path":"native","bytes":6,"sha256":"a"*64})),\
            mock.patch.object(Path,"resolve",lambda self:self),\
            mock.patch.dict(os.environ,{"GITHUB_TOKEN":"synthetic-not-a-secret"}):
            result=runner.runtime_identity(runner.time.monotonic()+30)
            self.assertEqual([r["program"] for r in result["binaries"]],list(runner.PROGRAMS));self.assertNotIn("foamRun",runner.PROGRAMS)

    def test_arbitrary_native_command_cannot_spawn(self):
        with mock.patch.object(runner.subprocess,"Popen") as launch:
            with self.assertRaises(case.TeachingCaseIssue):runner.execute(["foamRun","-case","fresh"],Path("fresh"),999999,{},0)
            launch.assert_not_called()

    def test_fast_native_completion_cannot_bypass_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp);(output/"logs").mkdir();args=case.commands(output)[0]
            fake=mock.Mock();fake.wait.return_value=0
            selector=mock.Mock();selector.get_map.side_effect=[{"pipe":True},{}]
            selector.select.return_value=[(SimpleNamespace(fileobj=fake.stdout),None)]
            runtime={"binaries":[{"program":"gmshToFoam","path":"/opt/openfoam12/bin/gmshToFoam","bytes":6,"sha256":"a"*64,
                "packageOwnership":"openfoam12: "+str(Path("/opt/openfoam12/bin/gmshToFoam"))}]}
            with mock.patch.object(Path,"resolve",lambda self:self),mock.patch.object(runner.safety,"descriptor",return_value=(b"native",{"bytes":6,"sha256":"a"*64})),\
                mock.patch.object(runner.subprocess,"Popen",return_value=fake),mock.patch.object(runner.selectors,"DefaultSelector",return_value=selector),\
                mock.patch.object(runner.os,"read",return_value=b""),mock.patch.object(runner,"workspace_size",return_value=0),\
                mock.patch.object(runner.native,"stop_owned") as stop,mock.patch.object(runner.time,"monotonic",side_effect=[0,0,0,101]):
                with self.assertRaisesRegex(case.TeachingCaseIssue,"completion"):runner.execute(args,output,100,runtime,0)
                stop.assert_called_once_with(fake)

    def exercise_native_contract(self,root,source,geometry,*,mutate=False):
        output=root/"native"
        def executed(args,output,deadline,runtime,index):
            text="SYNTHETIC-CONTRACT-NOT-NATIVE-CFD\nBuild :12-"+runner.safety.SOURCE_COMMIT[:12]+"\n"
            if args[0]=="checkMesh":text+="    cells: "+("12" if index==8 else "6")+"\n"+("   *Number of regions: 2\n" if index==8 else "    Number of regions: 1 (OK).\n")+"Min volume = 0.1. Max volume = 0.2. Total volume = 1.\nMesh OK.\n"
            text+="End\n";path=output/"logs"/f"{index:02d}-{args[0]}.log";path.write_text(text)
            if index in (2,5):
                name=case.COMPONENTS[index//3];mesh_root=output/(name+"-case")/"constant/polyMesh";mesh_root.mkdir(parents=True)
                for file in runner.MESH_FILES:(mesh_root/file).write_text("SYNTHETIC-READBACK-MOCK\n")
            if index==8 and mutate:(source/"teaching-pump.brep").write_bytes(b"MUTATED-SYNTHETIC\n")
            raw=path.read_bytes()
            return {"program":args[0],"exitCode":0,"elapsedSeconds":0.001,"log":path.name,"logBytes":len(raw),"logSha256":sha256(raw).hexdigest()},text
        with mock.patch.dict(os.environ,{"RUNNER_TEMP":str(root)}),mock.patch.dict(sys.modules,{"teaching_pump_geometry":geometry}),\
            mock.patch.object(runner.safety,"context_guard"),mock.patch.object(runner,"runtime_identity",return_value={"schemaVersion":runner.RUNTIME_SCHEMA,"binaries":[]}),\
            mock.patch.object(runner,"execute",side_effect=executed),\
            mock.patch.object(runner.base,"verify_conversion",return_value={"conversionVerified":True,"physicalWallTypesVerified":True,"actualCfdRunVerified":False}),\
            mock.patch.object(runner.base,"verify_merged_mesh",return_value={"mergedCellCount":12,"coupledInterfaceCoverageVerified":True,"actualCfdRunVerified":False}):
            result=runner.run(source,output)
        return result,json.loads((output/runner.REPORT).read_bytes())

    def test_nine_stage_contract_keeps_geometry_acceptance_separate_from_all_physics(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry=source_fixture(root);result,packet=self.exercise_native_contract(root,source,geometry)
            self.assertTrue(result["nativeGeometryAccepted"]);self.assertEqual(len(packet["stages"]),9)
            self.assertEqual(len(packet["componentNativeSnapshots"]),2)
            self.assertTrue(packet["commandExecutionPerformedByThisTool"])
            self.assertTrue(packet["inputArtifactsImmutable"]);self.assertTrue(packet["sourceGeometryImmutable"])
            for flag in ("simulationExecuted","actualCfdRunVerified","qualifiedBaseline","numericalBaselineQualified","flowExecuted","cavitationModelExecuted","physicalTransientExecuted"):
                self.assertIs(packet[flag],False)

    def test_original_source_change_during_native_work_prevents_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry=source_fixture(root);result,packet=self.exercise_native_contract(root,source,geometry,mutate=True)
            self.assertFalse(result["nativeGeometryAccepted"]);self.assertFalse(packet["sourceGeometryImmutable"])
            self.assertIn("source changed",packet["diagnostic"]["message"])

    def test_native_connectivity_gate_rejects_islands_even_if_mesh_ok(self):
        self.assertEqual(runner.verify_region_count("    Number of regions: 1 (OK).\nMesh OK.\n",1),1)
        self.assertEqual(runner.verify_region_count("   *Number of regions: 2\nMesh OK.\n",2),2)
        for text,expected in (("Number of regions: 2\nMesh OK.\n",1),("Number of regions: 3\nMesh OK.\n",2),
            ("Number of regions: 1 (OK).\nNumber of regions: 1 (OK).\n",1),("Mesh OK.\n",1)):
            with self.subTest(text=text),self.assertRaises(case.TeachingCaseIssue):runner.verify_region_count(text,expected)

    def test_native_rejection_preserves_truthful_new_report_without_physics_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry=source_fixture(root)
            with mock.patch.dict(os.environ,{"RUNNER_TEMP":temp}),mock.patch.dict(sys.modules,{"teaching_pump_geometry":geometry}),\
                mock.patch.object(runner.safety,"context_guard"),mock.patch.object(runner,"runtime_identity",return_value={"schemaVersion":runner.RUNTIME_SCHEMA,"binaries":[]}),\
                mock.patch.object(runner,"execute",side_effect=case.TeachingCaseIssue("Synthetic native quality rejection")):
                result=runner.run(source,root/"native");packet=json.loads((root/"native"/runner.REPORT).read_bytes())
                self.assertFalse(result["nativeGeometryAccepted"]);self.assertEqual(packet["sourceGeometryRoute"],case.SOURCE_ROUTE)
                self.assertTrue(all(packet[k] is False for k in case.FALSE_FLAGS));self.assertIn("Synthetic",packet["diagnostic"]["message"])


if __name__=="__main__":unittest.main()
