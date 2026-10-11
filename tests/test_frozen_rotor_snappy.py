"""Synthetic parser/process fixtures only; no native mesh or CFD evidence."""
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

TOOLS=Path(__file__).resolve().parents[1]/"tools"
spec=importlib.util.spec_from_file_location("snappy_runner",TOOLS/"run_frozen_rotor_snappy.py")
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


def log():
    return """Build  : 12-86e126a7bc4d
Enabling all (cell, face, edge, point) topology checks.
Enabling all geometry checks.
Mesh stats
    points: 5
    faces: 7
    internal faces: 1
    cells: 2
Number of regions: 1 (OK).
    Min volume = 0.16666666666666666. Max volume = 0.16666666666666666.  Total volume = 0.33333333333333333.  Cell volumes OK.
    Cell determinant (wellposedness) : minimum: 0.1 average: 1
Mesh OK.

End
"""


def mesh(root,*,background=True):
    case=root/"impeller-case";folder=case/"constant/polyMesh";folder.mkdir(parents=True)
    payloads={
        "points":("vectorField","5\n(\n(0 0 0)\n(1 0 0)\n(0 1 0)\n(0 0 1)\n(0 0 -1)\n)\n"),
        "faces":("faceList","7\n(\n3(0 2 1)\n3(0 1 3)\n3(0 3 2)\n3(1 2 3)\n3(0 4 1)\n3(0 2 4)\n3(1 4 2)\n)\n"),
        "owner":("labelList","7\n(0 0 0 0 1 1 1)\n"),
        "neighbour":("labelList","1\n(1)\n"),
        "boundary":("polyBoundaryMesh",("2" if background else "1")+"\n(\nfixture-wall { type wall; nFaces 6; startFace 1; }\n"+
                    ("backgroundBox { type patch; nFaces 0; startFace 7; }\n" if background else "")+")\n"),
        "cellZones":("cellZoneList","1\n(\nrotating-fluid { type cellZone; cellLabels List<label> 2(0 1); }\n)\n")}
    for name,(kind,body) in payloads.items():
        (folder/name).write_text(runner.base.foam_header(name,kind)+body,encoding="ascii")
    return case


def component():
    return {"component":"impeller","caseDirectory":"impeller-case","cellZone":"rotating-fluid","patchTypes":{"fixture-wall":"wall"},
            "insidePointEvidence":{"pointNativeUnits":[.12345678901234567,.1,.1]}}


def command_components():
    return [{**component(),"component":name,"caseDirectory":name+"-case"} for name in runner.COMPONENTS]


def fixture_commands(root):
    """Synthetic command authoring fixtures, never native source seed evidence."""
    if root.exists() and not (root/"snappy-preparation.json").exists():
        (root/"snappy-preparation.json").write_text(json.dumps({"schemaVersion":runner.PREPARATION_SCHEMA,"components":command_components()}))
    return runner.commands(root,command_components())


def selection_fixture(root,*,tiny_size=.01,seed_cell=0,reverse_internal=False,background=True):
    """Three synthetic polyhedra; the third is a tiny disconnected tetrahedron."""
    case=mesh(root,background=background);folder=case/"constant/polyMesh"
    selected_bytes={name:(folder/name).read_bytes() for name in runner.SELECTION_FILES}
    s=format(tiny_size,".17g");x=format(2+tiny_size,".17g")
    values={
        "points":("vectorField",f"9\n(\n(0 0 0)(1 0 0)(0 1 0)(0 0 1)(0 0 -1)(2 0 0)({x} 0 0)(2 {s} 0)(2 0 {s})\n)\n"),
        "faces":("faceList","11\n(\n3(0 2 1)3(0 1 3)3(0 3 2)3(1 2 3)3(0 4 1)3(0 2 4)3(1 4 2)3(5 7 6)3(5 6 8)3(5 8 7)3(6 7 8)\n)\n"),
        "owner":("labelList","11\n(0 0 0 0 1 1 1 2 2 2 2)\n"),
        "boundary":("polyBoundaryMesh",("2" if background else "1")+"\n(\nfixture-wall {type wall; nFaces 10; startFace 1;}\n"+
                    ("backgroundBox {type patch; nFaces 0; startFace 11;}\n" if background else "")+")\n"),
        "cellZones":("cellZoneList","0\n()\n"),"cellLevel":("labelList","3\n(2 2 2)\n")}
    for name,(kind,body) in values.items():(folder/name).write_text(runner.base.foam_header(name,kind)+body,encoding="ascii")
    raw_rows=runner.snapshot_region_source(root,component())
    for name,raw in selected_bytes.items():(folder/name).write_bytes(raw)
    maps={"pointRegionAddressing":[0,1,2,3,4],"faceRegionAddressing":[1,2,3,4,5,6,7],"cellRegionAddressing":[0,1]}
    if reverse_internal:
        (folder/"faces").write_bytes(selected_bytes["faces"].replace(b"3(0 2 1)",b"3(1 2 0)"))
        (folder/"owner").write_bytes(selected_bytes["owner"].replace(b"0 0 0 0 1 1 1",b"0 1 1 1 0 0 0"))
        maps["faceRegionAddressing"][0]=-1;maps["cellRegionAddressing"]=[1,0]
    for name,ids in maps.items():
        (folder/name).write_text(runner.base.foam_header(name,"labelList")+str(len(ids))+"\n("+" ".join(map(str,ids))+")\n",encoding="ascii")
    (root/"logs").mkdir()
    text=f"Build : 12-86e126a7bc4d\nFound point (0.123457 0.1 0.1) in cell {seed_cell}\nSubsetting region 0 containing point (0.123457 0.1 0.1)\nWriting addressing to base mesh\nEnd\n"
    (root/"logs/03-splitMeshRegions.log").write_bytes(text.encode("ascii"))
    return case,raw_rows,text


class NativeRegionSelectionTests(unittest.TestCase):
    def test_exact_native_subset_measures_small_disconnected_volume_and_replays(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);case,raw_rows,text=selection_fixture(root)
            report=runner.retain_region_selection(root,component(),text)
            self.assertEqual(report,runner.verify_region_selection(root,component()))
            self.assertEqual(report["sourceNativeFileRecords"],raw_rows)
            self.assertEqual((report["sourceCellCount"],report["selectedCellCount"],report["removedCellCount"]),(3,2,1))
            self.assertGreaterEqual(report["retainedGeneratedVolumeFraction"],.999)
            self.assertAlmostEqual(report["removedPolygonFanVolumeAssumedM3"],.01**3/6,places=14)
            self.assertEqual(report["selectedMappedCellLevelHistogram"],{"2":2})
            self.assertLess(report["sourcePatchAreaChanges"]["fixture-wall"]["areaDifferenceAssumedM2"],0)
            # Native metadata deliberately remains stale, not mislabelled as
            # a newly measured selected cell-level file.
            self.assertEqual(list(runner.selection_labels(case/"constant/polyMesh/cellLevel")),[2,2,2])
            self.assertTrue(report["noSelectedRemovedInternalFaceVerified"])
            self.assertTrue(report["allCellDirectedEdgeClosureVerified"])
            self.assertTrue(report["allCellPolygonFanVolumesPositiveVerified"])
            for flag in ("geometryApproximationQualified","meshQualityCheckedBySolver","simulationExecuted","actualCfdRunVerified","sourceHdf5OrStlModified"):
                self.assertFalse(report[flag])
            self.assertEqual(len(runner.selection_file_records(report)),17)

    def test_signed_face_addressing_allows_exact_native_internal_flip(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);_,_,text=selection_fixture(root,reverse_internal=True)
            result=runner.retain_region_selection(root,component(),text)
            self.assertTrue(result["mappedOrientedFacesExact"])
            self.assertTrue(result["mappedOwnerNeighbourExact"])

    def test_actual_removed_volume_over_point_one_percent_rejects(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);_,_,text=selection_fixture(root,tiny_size=1)
            with self.assertRaisesRegex(runner.surface.SurfaceCheckIssue,"99.9%"):
                runner.retain_region_selection(root,component(),text)

    def test_bad_maps_coordinates_owner_face_winding_and_patch_roles_reject(self):
        mutations={"pointRegionAddressing":("0 1 2 3 4","0 1 2 3 3"),
            "faceRegionAddressing":("1 2 3 4 5 6 7","-1 2 3 4 5 6 7"),
            "cellRegionAddressing":("0 1","0 2"),"points":("(0 0 -1)","(0 0 -1.01)"),
            "owner":("0 0 0 0 1 1 1","0 1 0 0 1 1 1"),"faces":("3(0 4 1)","3(0 1 4)"),
            "boundary":("type wall;","type patch;")}
        for name,(old,new) in mutations.items():
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);case,_,text=selection_fixture(root);path=case/"constant/polyMesh"/name
                path.write_text(path.read_text().replace(old,new))
                with self.assertRaises((runner.surface.SurfaceCheckIssue,runner.base.CaseIssue)):
                    runner.retain_region_selection(root,component(),text)

    def test_native_seed_cell_and_uniform_mapped_levels_are_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);_,_,text=selection_fixture(root,seed_cell=2)
            with self.assertRaisesRegex(runner.surface.SurfaceCheckIssue,"seed cell"):runner.retain_region_selection(root,component(),text)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);_,_,text=selection_fixture(root);path=root/"region-selection/impeller/source-polyMesh/cellLevel"
            path.write_text(path.read_text().replace("(2 2 2)","(2 1 2)"))
            with self.assertRaisesRegex(runner.surface.SurfaceCheckIssue,"level2"):runner.retain_region_selection(root,component(),text)

    def test_nonempty_cell_zones_and_stale_maps_reject_before_native_selection(self):
        for role in ("cellZones","pointRegionAddressing"):
            with self.subTest(role=role),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);case=mesh(root);folder=case/"constant/polyMesh"
                if role=="pointRegionAddressing":(folder/role).write_text("SYNTHETIC-STALE-MAP")
                with self.assertRaises(runner.surface.SurfaceCheckIssue):runner.snapshot_region_source(root,component())

    def test_native_empty_background_patch_can_disappear_without_role_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);case,_,text=selection_fixture(root)
            path=case/"constant/polyMesh/boundary"
            path.write_text(path.read_text().replace("2\n(","1\n(").replace("backgroundBox { type patch; nFaces 0; startFace 7; }\n",""))
            result=runner.retain_region_selection(root,component(),text)
            self.assertEqual(result["sourcePatchAreaChanges"]["backgroundBox"]["afterPolygonAreaVectorMagnitudeAssumedM2"],0)

    def test_saved_map_log_snapshot_or_report_tamper_rejects(self):
        targets=("region-selection/impeller/maps/cellRegionAddressing","region-selection/impeller/source-polyMesh/points",
                 "region-selection/impeller/selected-polyMesh/faces","logs/03-splitMeshRegions.log","region-selection/impeller/selection-report.json")
        for relative in targets:
            with self.subTest(relative=relative),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);_,_,text=selection_fixture(root);runner.retain_region_selection(root,component(),text)
                path=root/relative;path.write_bytes(path.read_bytes()+b"\nSYNTHETIC-TAMPER\n")
                with self.assertRaises((runner.surface.SurfaceCheckIssue,runner.base.CaseIssue,runner.quality_parser.QualityIssue,json.JSONDecodeError)):
                    runner.verify_region_selection(root,component())

    def test_transferred_immutable_evidence_replays_only_with_bound_historical_command_root(self):
        import shutil
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"native";root.mkdir();_,_,text=selection_fixture(root)
            expected=runner.retain_region_selection(root,component(),text)
            transferred=Path(temp)/"copy";shutil.copytree(root,transferred)
            self.assertEqual(expected,runner.verify_region_selection(transferred,component(),command_root=root))
            with self.assertRaises(runner.surface.SurfaceCheckIssue):runner.verify_region_selection(transferred,component())


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


class NativeMeshParserTests(unittest.TestCase):
    def test_actual_zone_class_and_empty_background_have_scoped_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            result=runner.native_mesh(mesh(Path(temp)),[component()],log())
        self.assertEqual(result["cellCount"],2)
        self.assertEqual(result["cellZones"],{"rotating-fluid":2})
        self.assertAlmostEqual(result["signedBoundaryFanVolumeAssumedM3"],1/3)
        self.assertTrue(result["nativeQuality"]["nativeGeometryQualityVerified"])
        for key in ("geometricApproximationQualified","surfaceDistanceBoundsVerified","passageClearanceBoundsVerified","meshConvergenceVerified","actualCfdRunVerified","simulationExecuted"):
            self.assertFalse(result[key])

    def test_exit_zero_without_complete_native_acceptance_is_rejected(self):
        for text in (log().replace("Mesh OK.","Failed 1 mesh checks."),log().replace("End", ""),
                     log().replace("Enabling all geometry checks.",""),log()+"FOAM FATAL ERROR\n",
                     log().replace("12-86e126a7bc4d","12-other"),log().replace("minimum: 0.1","minimum: nan"),
                     log().replace("Total volume = 0.33333333333333333","Total volume = 0"),log()+"Mesh OK.\n"):
            with self.subTest(text=text[-60:]),self.assertRaises(runner.surface.SurfaceCheckIssue):runner.check_log(text)

    def test_every_native_primitive_and_patch_role_is_validated(self):
        mutations={"points":("(0 0 -1)","(0 0 nan)"),"faces":("3(0 4 1)","3(0 7 1)"),
            "owner":("0 0 0 0 1 1 1","0 0 0 0 3 3 3"),"neighbour":("1\n(1)","1\n(0)"),
            "boundary":("type wall;","type patch;"),"cellZones":("2(0 1)","2(0 0)")}
        for name,(old,new) in mutations.items():
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                case=mesh(Path(temp));path=case/"constant/polyMesh"/name;path.write_text(path.read_text().replace(old,new))
                with self.assertRaises((runner.surface.SurfaceCheckIssue,runner.base.CaseIssue)):
                    runner.native_mesh(case,[component()],log())

    def test_current_zone_header_rejects_includes_wrong_class_and_trailing_data(self):
        for old,new in (("class cellZoneList;","class scalarField;"),("1\n(\nrotating","#include \"secret\"\n1\n(\nrotating"),
                        ("}\n)\n","}\nUNPARSED\n)\n")):
            with self.subTest(new=new),tempfile.TemporaryDirectory() as temp:
                case=mesh(Path(temp));path=case/"constant/polyMesh/cellZones";path.write_text(path.read_text().replace(old,new))
                with self.assertRaises((runner.surface.SurfaceCheckIssue,runner.quality_parser.QualityIssue)):
                    runner.native_mesh(case,[component()],log())

    def test_positive_background_and_disconnected_fluid_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            case=mesh(Path(temp));path=case/"constant/polyMesh/boundary"
            path.write_text(path.read_text().replace("nFaces 0;","nFaces 1;"))
            with self.assertRaises(runner.surface.SurfaceCheckIssue):runner.native_mesh(case,[component()],log())
        with tempfile.TemporaryDirectory() as temp,self.assertRaises(runner.surface.SurfaceCheckIssue):
            runner.native_mesh(mesh(Path(temp)),[component()],log().replace("regions: 1 (OK).","regions: 2"))

    def test_measured_component_cap_is_separate_from_refinement_target(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(runner,"MAX_COMPONENT_CELLS",1),self.assertRaises(runner.surface.SurfaceCheckIssue):
            runner.native_mesh(mesh(Path(temp)),[component()],log())
        with tempfile.TemporaryDirectory() as temp,patch.object(runner,"MAX_COMBINED_CELLS",2),self.assertRaises(runner.surface.SurfaceCheckIssue):
            runner.native_mesh(mesh(Path(temp)),[component()],log())

    def test_hash_bound_premerge_snapshot_remains_after_native_mesh_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);case=mesh(root);measured=runner.native_mesh(case,[component()],log())
            rows=runner.snapshot_component(root,measured)
            self.assertEqual(len(rows),6)
            old=(case/"constant/polyMesh/points").read_bytes();(case/"constant/polyMesh/points").write_bytes(b"SYNTHETIC-MERGED-CHANGE")
            row=next(r for r in rows if r["path"].endswith("/points"))
            self.assertEqual((root/row["path"]).read_bytes(),old)
            self.assertEqual(row["sha256"],sha256(old).hexdigest())

    def test_changed_checked_primitive_prevents_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);case=mesh(root);measured=runner.native_mesh(case,[component()],log())
            (case/"constant/polyMesh/points").write_bytes(b"SYNTHETIC-CHANGE")
            with self.assertRaises(runner.surface.SurfaceCheckIssue):runner.snapshot_component(root,measured)

    def test_ncc_coverage_requires_actual_finite_bound_metrics(self):
        good="Source min/average/max coverage = 0.999/1/1.0001\nTarget min/average/max coverage = 0.9995/1/1.0002\n"
        self.assertEqual(len(runner.coupled_coverage(good)),1)
        for text in ("",good.replace("0.999/","0.98/"),good.replace("0.9995/1/","0.99/0.998/"),good.replace("1.0002","1.02"),good.replace("0.999/","nan/")):
            with self.subTest(text=text),self.assertRaises(runner.surface.SurfaceCheckIssue):runner.coupled_coverage(text)

    def test_aggregate_geometry_comparison_never_qualifies_surface_distances(self):
        with tempfile.TemporaryDirectory() as temp:measured=runner.native_mesh(mesh(Path(temp)),[component()],log())
        source={"component":"impeller","patches":[{"name":"fixture-wall","surfaceAreaNativeUnitsSquared":measured["patchMeasures"]["fixture-wall"]["polygonAreaVectorMagnitudeAssumedM2"]}],
            "surfaceFile":{"path":"fixture.obj","bytes":1,"sha256":"0"*64},"signedTriangleVolumeNativeUnitsCubed":-1/3}
        result=runner.approximation(measured,source)
        self.assertEqual(result["result"]["relativeVolumeDifference"],0)
        self.assertFalse(result["geometricApproximationQualified"])
        self.assertFalse(result["surfaceDistanceBoundsVerified"])
        self.assertEqual(result["input"]["sourceCoordinateUnits"],"UNVERIFIED")


class BoundedNativeProcessTests(unittest.TestCase):
    def fixture(self,root,*,content=None,code=0,times=None):
        output=root/"execution";output.mkdir();(output/"logs").mkdir();(output/"impeller-case").mkdir()
        executable=root/"blockMesh";executable.write_bytes(b"SYNTHETIC-NON-NATIVE-BINARY")
        runtime={"binaries":[{"program":"blockMesh","path":str(executable),"bytes":executable.stat().st_size,"sha256":sha256(executable.read_bytes()).hexdigest()}]}
        text=log() if content is None else content;chunks=iter([text.encode(),b""])
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path,"is_relative_to",return_value=True))
            stack.enter_context(patch.object(runner.selectors,"DefaultSelector",Selector))
            popen=stack.enter_context(patch.object(runner.subprocess,"Popen",return_value=Process(code)))
            stack.enter_context(patch.object(runner.os,"read",side_effect=lambda *_:next(chunks)))
            stack.enter_context(patch.object(runner.native,"stop_owned"))
            if times is not None:stack.enter_context(patch.object(runner.time,"monotonic",side_effect=times))
            result=runner.execute(fixture_commands(output)[0],output,1000 if times is not None else runner.time.monotonic()+100,runtime,0)
        return result,popen,output

    def test_author_only_fifteen_native_geometry_stages_and_actual_seed(self):
        rows=fixture_commands(Path("fixture"))
        self.assertEqual(len(rows),15)
        self.assertEqual([r[0] for r in rows[:6]],["blockMesh","surfaceFeatures","snappyHexMesh","splitMeshRegions","topoSet","checkMesh"])
        self.assertEqual(rows[3][3:],["-insidePoint","(0.12345678901234566 0.10000000000000001 0.10000000000000001)","-overwrite"])
        self.assertEqual(rows[4][-1],"-constant")
        self.assertEqual([row[-1] for row in rows if row[0]=="checkMesh"],["-writeSets"]*3)
        self.assertEqual(rows[12][3],"-addCases")
        self.assertEqual(rows[12][4],'("'+str(Path("fixture").absolute()/"volute-case")+'")')
        self.assertNotIn("foamRun",repr(rows));self.assertNotIn("-noFields",repr(rows))

    def test_process_uses_bound_binary_owned_session_and_no_credentials(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"GH_TOKEN":"SYNTHETIC-FORBIDDEN"}):
            (stage,text),popen,output=self.fixture(Path(temp))
        args,kwargs=popen.call_args
        self.assertEqual(args[0][1:],fixture_commands(output)[0][1:])
        self.assertEqual(kwargs["cwd"],str(output/"impeller-case"))
        self.assertTrue(kwargs["start_new_session"])
        self.assertIs(kwargs["preexec_fn"],runner.surface.child_limits)
        self.assertNotIn("GH_TOKEN",kwargs["env"])
        self.assertEqual(stage["logFile"]["sha256"],sha256(text.encode()).hexdigest())

    def test_nonzero_exit_remains_an_actual_failure_fact(self):
        with tempfile.TemporaryDirectory() as temp:(stage,_),_,_=self.fixture(Path(temp),code=7)
        self.assertEqual(stage["exitCode"],7)

    def test_output_and_postcompletion_deadline_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(runner,"MAX_LOG",20),self.assertRaises(runner.surface.SurfaceCheckIssue):self.fixture(Path(temp))
        with tempfile.TemporaryDirectory() as temp,self.assertRaises(runner.surface.SurfaceCheckIssue):self.fixture(Path(temp),times=[0,0,0,0,1001])

    def test_unsupported_command_and_index_reject_before_startup(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(runner.subprocess,"Popen") as popen:
            root=Path(temp)
            for cmd,index in ((["foamRun","-case",str(root)],0),(fixture_commands(root)[0],1),(fixture_commands(root)[0],True)):
                with self.assertRaises(runner.surface.SurfaceCheckIssue):runner.execute(cmd,root,1000,{},index)
            popen.assert_not_called()

    def test_changed_binary_is_rejected_before_startup(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(Path,"is_relative_to",return_value=True),patch.object(runner.subprocess,"Popen") as popen:
            root=Path(temp);(root/"logs").mkdir();binary=root/"blockMesh";binary.write_bytes(b"SYNTHETIC-CHANGED")
            with self.assertRaises(runner.surface.SurfaceCheckIssue):
                runner.execute(fixture_commands(root)[0],root,1000,{"binaries":[{"program":"blockMesh","path":str(binary),"bytes":1,"sha256":"0"*64}]},0)
            popen.assert_not_called()


class EvidenceBindingTests(unittest.TestCase):
    def test_all_eight_actual_binary_records_and_package_ownership_are_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in runner.PROGRAMS:(root/name).write_bytes(("SYNTHETIC-"+name).encode())
            replies=[SimpleNamespace(stdout="20250206",stderr=""),*[SimpleNamespace(stdout="openfoam12: "+str((root/n).resolve())+"\n",stderr="") for n in runner.PROGRAMS]]
            with patch.object(runner.shutil,"which",side_effect=lambda name:str(root/name)),patch.object(Path,"is_relative_to",return_value=True),patch.object(runner.subprocess,"run",side_effect=replies):
                result=runner.runtime_identity(runner.time.monotonic()+100)
            self.assertEqual([r["program"] for r in result["binaries"]],list(runner.PROGRAMS))
            self.assertEqual(result["binaries"][0]["sha256"],sha256((root/runner.PROGRAMS[0]).read_bytes()).hexdigest())

    def test_wrong_package_binary_path_and_owner_reject(self):
        with tempfile.TemporaryDirectory() as temp:
            binary=Path(temp)/"blockMesh";binary.write_bytes(b"SYNTHETIC")
            for version,inside,owner in (("other",True,"openfoam12"),("20250206",False,"openfoam12"),("20250206",True,"other")):
                replies=[SimpleNamespace(stdout=version,stderr=""),SimpleNamespace(stdout=owner+": "+str(binary.resolve()),stderr="")]
                with self.subTest(version=version,inside=inside,owner=owner),patch.object(runner.shutil,"which",return_value=str(binary)),patch.object(Path,"is_relative_to",return_value=inside),patch.object(runner.subprocess,"run",side_effect=replies),self.assertRaises(runner.surface.SurfaceCheckIssue):
                    runner.runtime_identity(runner.time.monotonic()+100)

    def test_authored_source_proof_and_command_list_cannot_be_changed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);rows=[]
            for name in ("authored.dict","source.obj","proof.log"):
                (root/name).write_bytes(b"SYNTHETIC");rows.append(runner.record(root,name)[1])
            report={"schemaVersion":runner.PREPARATION_SCHEMA,"nativeCommandArguments":fixture_commands(root),"components":command_components(),
                "authoredFileRecords":[rows[0]],"sourceSurfaceFileRecords":[rows[1]],"sourceProofFileRecords":[rows[2]]}
            (root/"snappy-preparation.json").write_text(json.dumps(report))
            verified={"report":report,"reportFile":runner.record(root,"snappy-preparation.json")[1]}
            self.assertEqual(len(runner.bound_records(root,verified)),4)
            (root/"source.obj").write_bytes(b"SYNTHETIC-CHANGE")
            with self.assertRaises(runner.surface.SurfaceCheckIssue):runner.bound_records(root,verified)

    def test_evidence_cannot_traverse_or_use_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/"evidence").write_bytes(b"SYNTHETIC")
            for value in ("../evidence","/evidence","a//evidence","a\\evidence","a/$secret"):
                with self.subTest(value=value),self.assertRaises(runner.surface.SurfaceCheckIssue):runner.record(root,value)


class SyntheticCampaignOrchestrationTests(unittest.TestCase):
    def campaign(self,root,*,failure=None,mutation=False):
        """Explicit process mocks exercise ordering and claims, never native physics."""
        source=root/"source";source.mkdir();proof=root/"proof";proof.mkdir();output=root/"execution"
        components=[{"component":name,"caseDirectory":name+"-case","cellZone":zone,"patchTypes":{name+"-fixture":"wall"},
                     "insidePointEvidence":{"pointNativeUnits":[.1,.2,.3]}}
            for name,zone in zip(runner.COMPONENTS,("rotating-fluid","stationary-fluid"))]
        def prepare(*_):
            output.mkdir();groups=[]
            for name in ("authored.dict","source.obj","proof.log"):
                (output/name).write_bytes(b"SYNTHETIC-NOT-ACTUAL-SOURCE-PROOF");groups.append([runner.record(output,name)[1]])
            report={"schemaVersion":runner.PREPARATION_SCHEMA,"nativeCommandArguments":runner.commands(output,components),"components":components,
                **dict(zip(("authoredFileRecords","sourceSurfaceFileRecords","sourceProofFileRecords"),groups))}
            (output/"snappy-preparation.json").write_text(json.dumps(report))
            return report
        def verify(_):return {"report":json.loads((output/"snappy-preparation.json").read_bytes()),"reportFile":runner.record(output,"snappy-preparation.json")[1],"components":components}
        union=SimpleNamespace(verify_output=lambda _: {"components":[{"component":name}for name in runner.COMPONENTS]})
        preparer=SimpleNamespace(prepare=prepare,verify_output=verify)
        def execute(arguments,workspace,deadline,runtime,index):
            text=log();path=output/"logs"/(f"{index:02d}-{arguments[0]}.log");path.write_text(text)
            if mutation and index==14:(output/"source.obj").write_bytes(b"SYNTHETIC-MUTATION")
            return {"stageIndex":index,"program":arguments[0],"arguments":arguments,"exitCode":7 if index==failure else 0,"logFile":runner.record(output,"logs/"+path.name)[1]},text
        def native_mesh(case,rows,text):
            if len(rows)==2:return {"cellCount":4,"nodeCount":10,"nativeCoupledCoverage":[{"source":[1,1,1],"target":[1,1,1]}]}
            return {**rows[0],"cellCount":2,"nodeCount":5,"nativeFiles":[]}
        with patch.dict(os.environ,{"RUNNER_TEMP":str(root)}),patch.dict(runner.sys.modules,{"frozen_rotor_surface_union":union,"frozen_rotor_snappy_case":preparer}),\
             patch.object(runner.surface,"context_guard"),patch.object(runner,"runtime_identity",return_value={"syntheticFixture":True}),\
             patch.object(runner,"execute",side_effect=execute),patch.object(runner,"native_mesh",side_effect=native_mesh),\
             patch.object(runner,"snapshot_region_source",return_value=[]),\
             patch.object(runner,"retain_region_selection",side_effect=lambda output,c,text:{"component":c["component"],"sourceNativeFileRecords":[],"selectedNativeFileRecords":[],"syntheticFixture":True}),\
             patch.object(runner,"verify_region_selection",side_effect=lambda output,c:{"component":c["component"],"sourceNativeFileRecords":[],"selectedNativeFileRecords":[],"syntheticFixture":True}),\
             patch.object(runner,"snapshot_component",return_value=[]),patch.object(runner,"approximation",return_value={"syntheticFixture":True,"geometricApproximationQualified":False}):
            result=runner.run(source,proof,output)
        return result,json.loads((output/"snappy-execution.json").read_bytes())

    def test_complete_geometry_campaign_does_not_promote_physical_qualification(self):
        with tempfile.TemporaryDirectory() as temp:code,result=self.campaign(Path(temp))
        self.assertEqual(code,0);self.assertEqual(len(result["stages"]),15)
        self.assertTrue(result["meshQualityCheckedBySolver"])
        for key in ("fullGeometryQualification","geometricApproximationQualified","surfaceDistanceBoundsVerified","passageClearanceBoundsVerified","nativeCoordinateUnitsVerified","meshConvergenceVerified","numericalBaselineQualified","qualifiedBaseline","npsh3Qualified","simulationExecuted","actualCfdRunVerified","mainApplicationWrites"):
            self.assertFalse(result[key])

    def test_failed_native_stage_preserves_raw_exit_and_log_without_later_stages(self):
        with tempfile.TemporaryDirectory() as temp:code,result=self.campaign(Path(temp),failure=2)
        self.assertEqual(code,2);self.assertEqual(len(result["stages"]),3)
        self.assertEqual(result["stages"][-1]["exitCode"],7)
        self.assertEqual(len(result["retainedNativeLogRecords"]),3)
        self.assertFalse(result["meshQualityCheckedBySolver"])
        self.assertFalse(result["simulationExecuted"])

    def test_postnative_input_mutation_prevents_candidate_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:code,result=self.campaign(Path(temp),mutation=True)
        self.assertEqual(code,2);self.assertEqual(len(result["stages"]),15)
        self.assertFalse(result["inputArtifactsImmutable"])
        self.assertFalse(result["meshQualityCheckedBySolver"])
        self.assertFalse(result["actualCfdRunVerified"])


if __name__=="__main__":unittest.main()
