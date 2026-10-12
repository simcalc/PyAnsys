"""Synthetic native-format contracts, never solver or restart evidence."""
from copy import deepcopy
from hashlib import sha256
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import teaching_pump_seeded_frames as tool


def native(name, kind, location, body):
    return (f'/* SYNTHETIC FORMAT FIXTURE: no native execution */\nFoamFile {{format ascii; class {kind}; location "{location}"; object {name};}}\n' + body).encode()


def write(root, name, raw):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"path": name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def time_bytes(name, index, dt, dt0):
    return native("time", "dictionary", name + "/uniform",
        f'beginTime 0; value {name}; name "{name}"; index {index}; deltaT {dt:.17g}; deltaT0 {dt0:.17g};\n')


def points_bytes(points, location):
    return native("points", "vectorField", location, str(len(points)) + "\n(\n" +
                  "\n".join("(" + " ".join(format(v, ".17g") for v in p) + ")" for p in points) + "\n)\n")


def moved(points, time, *, rebased=False):
    angle = 2 * math.pi * 1450 * (time - .5 if rebased else time) / 60
    s, c = math.sin(angle), math.cos(angle)
    return [(c*x-s*y, s*x+c*y, z) if i < 4 else (x,y,z) for i, (x,y,z) in enumerate(points)]


def fixture(root, *, adaptive=False, rebased=False):
    # Two disconnected synthetic tetrahedra model only the disjoint labels of
    # rotor/stator points. No native quality, physical pump or CAD claim follows.
    points = [(.10,0,0),(.11,0,0),(.10,.01,0),(.10,0,.01),
              (.20,0,0),(.21,0,0),(.20,.01,0),(.20,0,.01)]
    faces = [(0,2,1),(0,1,3),(1,2,3),(2,0,3),(4,6,5),(4,5,7),(5,6,7),(6,4,7)]
    mesh = {"points": points_bytes(points, "constant/polyMesh"),
        "faces": native("faces", "faceList", "constant/polyMesh", "8\n(" + " ".join("3("+" ".join(map(str,f))+")" for f in faces)+")"),
        "owner": native("owner", "labelList", "constant/polyMesh", "8(0 0 0 0 1 1 1 1)"),
        "neighbour": native("neighbour", "labelList", "constant/polyMesh", "0()"),
        "cellZones": native("cellZones", "cellZoneList", "constant/polyMesh", "2(rotating-fluid {type cellZone;cellLabels List<label> 1(0);} stationary-fluid {type cellZone;cellLabels List<label> 1(1);})")}
    owner = "pumpInterface_on_interface-impeller-outlet"
    neighbour = "pumpInterface_on_interface-volute-inlet"
    rows = ["inlet {type patch;nFaces 1;startFace 0;}", "blade {type wall;nFaces 2;startFace 1;}",
        "interface-impeller-outlet {type wall;nFaces 1;startFace 3;}", "volute-wall {type wall;nFaces 3;startFace 4;}",
        "interface-volute-inlet {type wall;nFaces 1;startFace 7;}",
        f"{owner} {{type nonConformalCyclic;nFaces 0;startFace 8;neighbourPatch {neighbour};originalPatch interface-impeller-outlet;transformType none;}}",
        f"{neighbour} {{type nonConformalCyclic;nFaces 0;startFace 8;neighbourPatch {owner};originalPatch interface-volute-inlet;transformType none;}}"]
    mesh["boundary"] = native("boundary", "polyBoundaryMesh", "constant/polyMesh", "7(\n" + "\n".join(rows) + "\n)")
    references = [write(root, "case/constant/polyMesh/" + n, mesh[n]) for n in tool.MESH_FILES]
    dynamic = write(root, "case/constant/dynamicMeshDict", native("dynamicMeshDict", "dictionary", "constant",
        'mover {type motionSolver;libs ("libfvMotionSolvers.so");motionSolver solidBody;cellZone rotating-fluid;solidBodyMotionFunction rotatingMotion;origin (0 0 0);axis (0 0 1);omega 1450 [rpm];}'))
    for role, (kind, obj) in tool.clock.ROLES.items():
        if role not in tool.clock.REQUIRED_STATE:
            continue
        location = "0.5" + ("/" + role.rsplit("/",1)[0] if "/" in role else "")
        raw = native(obj, kind, location, "SYNTHETIC_HEADER_ONLY;\n")
        if role == "uniform/time": raw = time_bytes("0.5", 90, .02, .007)
        if role == "polyMesh/points": raw = points_bytes(moved(points,.5,rebased=rebased), location)
        write(root, "case/0.5/" + role, raw)
    for name, index in (("0.51",91),("0.52",92)):
        write(root, "case/"+name+"/polyMesh/points", points_bytes(moved(points,float(name),rebased=rebased),name+"/polyMesh"))
        write(root, "case/"+name+"/uniform/time", time_bytes(name,index,.01,.02 if adaptive and index==91 else .01))
    control = write(root,"case/system/controlDict",native("controlDict","dictionary","system",
        'application foamRun;solver incompressibleVoF;startFrom startTime;startTime .5;stopAt endTime;endTime .52;'
        'deltaT .01;adjustTimeStep '+('yes' if adaptive else 'no')+';runTimeModifiable false;timeFormat general;timePrecision 17;writePrecision 17;'))
    log = "Build : 12-86e126a7bc4d\nCreate mesh for time = 0.5\nSelecting solver incompressibleVoF\nSelecting solid-body motion function rotatingMotion\nStarting time loop\n"
    for i,name in enumerate(("0.51","0.52"),1):
        if adaptive:log += "deltaT = .01\n"
        log += f"Time = {name}s\nExecutionTime = {i} s ClockTime = {i} s\n"
    log += "End\n"
    logfile = write(root,"logs/01-foamRun.log",log.encode())
    identity = write(root,"teaching-geometry-identity.json",tool.encoded({"schemaVersion":"untirta.classroom-authored-pump-geometry-identity.v1",
        "provenance":"CLASSROOM_AUTHORED_GEOMETRY","units":"SI","axis":[0,0,1],"originM":[0,0,0],
        "manufacturerIdentity":None,"originalFluentSourceIdentity":None}))
    checkpoint,_ = tool.clock.inventory(root,"case/0.5")
    endpoint = next(r for r in tool.clock.inventory(root,"case/0.52")[0] if r["path"].endswith("uniform/time"))
    clock_input = {"schema":tool.clock.INPUT_SCHEMA,"expectedSourceCommit":tool.clock.SOURCE_COMMIT,"checkpointDirectory":"case/0.5",
        "checkpointInventory":checkpoint,"checkpointInventorySha256":sha256(tool.encoded(checkpoint)).hexdigest(),
        "controlDictFile":control,"nativeLogFile":logfile,"endpointTimeStateFile":endpoint,"sourceGeometryIdentityFile":identity}
    frames = []
    for name in ("0.5","0.51","0.52"):
        inventory,_ = tool.clock.inventory(root,"case/"+name)
        frames.append({"timeDirectory":name,"inventory":inventory,"inventorySha256":sha256(tool.encoded(inventory)).hexdigest()})
    return {"schemaVersion":tool.INPUT_SCHEMA,"clockInput":clock_input,"referenceMeshFiles":references,
            "dynamicMeshDictFile":dynamic,"frames":frames},points


def refresh_frame(root, packet, name):
    row = next(r for r in packet["frames"] if r["timeDirectory"]==name)
    row["inventory"],_ = tool.clock.inventory(root,"case/"+name)
    row["inventorySha256"] = sha256(tool.encoded(row["inventory"])).hexdigest()


class SeededFrameTests(unittest.TestCase):
    def test_fixed_positive_seed_without_printed_dt_is_consecutive_global_time_fact(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);packet,_=fixture(root);out=tool.inspect(root,packet)
            self.assertEqual([r["nativeIndex"] for r in out["frames"]],[90,91,92])
            self.assertEqual(out["fullNativeStepCount"],2);self.assertEqual(len(out["consecutivePairs"]),2)
            self.assertEqual(out["frames"][0]["physicalTimeS"],.5)
            self.assertAlmostEqual(out["frames"][1]["deltaT0S"],.01)
            self.assertGreater(out["frames"][0]["rotationAngleRad"],2*math.pi)
            self.assertTrue(out["genuinelyConsecutiveClockPairsVerified"])
            self.assertTrue(out["globalRotationCoordinateEquationMatched"])
            self.assertFalse(out["globalTimeRebased"])
            self.assertFalse(out["frames"][1]["completeStoredStateRoleInventoryPresent"])
            for flag in tool.FALSE_FLAGS:self.assertIs(out[flag],False)

    def test_adaptive_first_previous_dt_comes_from_actual_checkpoint_seed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);packet,_=fixture(root,adaptive=True);out=tool.inspect(root,packet)
            self.assertAlmostEqual(out["frames"][1]["deltaT0S"],.02)
            self.assertAlmostEqual(out["frames"][2]["deltaT0S"],.01)

    def test_consecutive_frame_subset_still_requires_full_native_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);packet,_=fixture(root);packet["frames"]=packet["frames"][1:]
            out=tool.inspect(root,packet);self.assertEqual(out["fullNativeStepCount"],2)
            self.assertEqual(out["consecutivePairs"][0]["previousNativeIndex"],91)
            log=root/"logs/01-foamRun.log";raw=log.read_bytes().replace(b'Time = 0.51s\nExecutionTime = 1 s ClockTime = 1 s\n',b'')
            packet["clockInput"]["nativeLogFile"]=write(root,"logs/01-foamRun.log",raw)
            with self.assertRaises(ValueError):tool.inspect(root,packet)

    def test_skipped_or_duplicate_frame_index_does_not_become_a_step_pair(self):
        for change in ("skip","duplicate","reverse"):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);packet,_=fixture(root)
                if change=="skip":packet["frames"].pop(1)
                elif change=="duplicate":packet["frames"][1]=deepcopy(packet["frames"][0])
                else:packet["frames"].reverse()
                with self.assertRaisesRegex(ValueError,"CONSECUTIVE"):tool.inspect(root,packet)

    def test_prefix_rebased_rotor_points_are_rejected_even_with_rehashed_native_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);packet,_=fixture(root,rebased=True)
            with self.assertRaisesRegex(ValueError,"GLOBAL_ROTATION"):tool.inspect(root,packet)

    def test_unchanged_reference_points_cannot_be_replaced_by_moved_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);packet,points=fixture(root)
            row=write(root,"case/constant/polyMesh/points",points_bytes(moved(points,.5),"constant/polyMesh"))
            packet["referenceMeshFiles"][0]=row
            with self.assertRaisesRegex(ValueError,"GLOBAL_ROTATION"):tool.inspect(root,packet)

    def test_reference_control_and_frame_current_byte_mutations_fail_closed(self):
        for path in ("case/constant/polyMesh/points","case/system/controlDict","case/0.51/polyMesh/points"):
            with self.subTest(path=path),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);packet,_=fixture(root);p=root/path;p.write_bytes(p.read_bytes()+b' ')
                with self.assertRaises(ValueError):tool.inspect(root,packet)

    def test_rebound_inconsistent_frame_clock_index_or_dt0_is_rejected(self):
        for old,new in ((b'index 91;',b'index 92;'),(b'deltaT0 0.01;',b'deltaT0 .02;'),(b'value 0.51;',b'value .50;')):
            with self.subTest(change=new),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);packet,_=fixture(root);p=root/"case/0.51/uniform/time";p.write_bytes(p.read_bytes().replace(old,new));refresh_frame(root,packet,"0.51")
                with self.assertRaises(ValueError):tool.inspect(root,packet)

    def test_native_selector_wrong_dynamic_axis_or_simultaneous_mrf_rejects(self):
        for change in ("selector","axis","rpm","mrf","points0"):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);packet,_=fixture(root)
                if change=="selector":
                    p=root/"logs/01-foamRun.log";packet["clockInput"]["nativeLogFile"]=write(root,p.relative_to(root).as_posix(),p.read_bytes().replace(b'rotatingMotion',b'linearMotion'))
                elif change in ("axis","rpm"):
                    p=root/"case/constant/dynamicMeshDict";raw=p.read_bytes().replace(b'axis (0 0 1)',b'axis (1 0 0)') if change=="axis" else p.read_bytes().replace(b'1450 [rpm]',b'1450')
                    packet["dynamicMeshDictFile"]=write(root,p.relative_to(root).as_posix(),raw)
                elif change=="mrf":write(root,"case/constant/MRFProperties",b'SYNTHETIC forbidden extra control')
                else:write(root,"case/0.51/points0",b'SYNTHETIC forbidden rebased reference')
                with self.assertRaises(ValueError):tool.inspect(root,packet)

    def test_unsupported_optional_vc0_unknown_oldtime_and_wrong_native_header_reject(self):
        for change in ("Vc0","U_0","header"):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);packet,_=fixture(root)
                if change=="header":
                    p=root/"case/0.51/polyMesh/points";p.write_bytes(p.read_bytes().replace(b'class vectorField',b'class volVectorField'))
                else:write(root,"case/0.51/"+change,native(change,"volScalarField::Internal","0.51","SYNTHETIC HEADER;"))
                refresh_frame(root,packet,"0.51")
                with self.assertRaises(ValueError):tool.inspect(root,packet)

    def test_numeric_directory_alias_and_incomplete_inventory_reject(self):
        for change in ("alias","inventory"):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);packet,_=fixture(root)
                if change=="alias":(root/"case/0.510").mkdir()
                else:packet["frames"][1]["inventory"].pop()
                with self.assertRaises(ValueError):tool.inspect(root,packet)

    def test_postread_frame_directory_mutation_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);packet,_=fixture(root);real=tool.motion.point_vectors;calls=0
            def mutate(*a,**kw):
                nonlocal calls
                result=real(*a,**kw);calls+=1
                if calls==2:(root/"case/0.5/added-empty-directory").mkdir()
                return result
            with patch.object(tool.motion,"point_vectors",side_effect=mutate),self.assertRaisesRegex(ValueError,"IMMUTABLE"):
                tool.inspect(root,packet)

    def test_typed_limits_source_family_and_source_commit_are_not_waived(self):
        for change in ("schema","head","record","budget"):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);packet,_=fixture(root)
                if change=="schema":packet["schemaVersion"]="old-original-source"
                elif change=="head":packet["clockInput"]["expectedSourceCommit"]="a"*40
                elif change=="record":packet["referenceMeshFiles"][0]["bytes"]=True
                if change=="budget":
                    with patch.object(tool,"MAX_RECORDS",2),self.assertRaises(ValueError):tool.inspect(root,packet)
                else:
                    with self.assertRaises(ValueError):tool.inspect(root,packet)

    def test_native_writer_max_precision_value_with_rounded_time_name_is_not_silently_relabelled(self):
        # Source-shaped TimeIO formatting, synthetic data. This explicit
        # limitation must be resolved in a future producer, not by relabelling
        # an already-written checkpoint or widening the committed clock gate.
        value = (60 / 1450) / 4
        name = format(value, ".12g")
        raw = native("time", "dictionary", name + "/uniform",
            f'beginTime 0;value {value:.17g};name "{name}";index 90;deltaT .0001;deltaT0 .0001;')
        with self.assertRaisesRegex(ValueError,"CURRENT_TIME"):
            tool.clock.parse_time_state(raw,name)

    def test_boolean_geometry_axis_or_origin_cannot_be_a_numeric_rotation_basis(self):
        import json
        for key,value in (("axis",[False,False,True]),("originM",[False,0,0])):
            with self.subTest(key=key),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);packet,_=fixture(root);path="teaching-geometry-identity.json"
                identity=json.loads((root/path).read_bytes());identity[key]=value
                packet["clockInput"]["sourceGeometryIdentityFile"]=write(root,path,tool.encoded(identity))
                with self.assertRaisesRegex(ValueError,"AUTHORED_IDENTITY"):tool.inspect(root,packet)

    def test_all_reference_header_locations_are_checked_even_after_current_row_rebinding(self):
        for name in tool.MESH_FILES:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);packet,_=fixture(root);path="case/constant/polyMesh/"+name
                row=write(root,path,(root/path).read_bytes().replace(b'location "constant/polyMesh"',b'location "0.51/polyMesh"'))
                packet["referenceMeshFiles"]=[row if r["path"]==path else r for r in packet["referenceMeshFiles"]]
                with self.assertRaisesRegex(ValueError,"REFERENCE_HEADER"):tool.inspect(root,packet)

    def test_known_native_cell_zone_header_classes_remain_role_specific(self):
        for kind in ("regIOobject","cellZoneMesh","cellZoneList"):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);packet,_=fixture(root);path="case/constant/polyMesh/cellZones"
                row=write(root,path,(root/path).read_bytes().replace(b'class cellZoneList',('class '+kind).encode()))
                packet["referenceMeshFiles"]=[row if r["path"]==path else r for r in packet["referenceMeshFiles"]]
                self.assertTrue(tool.inspect(root,packet)["globalRotationCoordinateEquationMatched"])


if __name__ == "__main__":
    unittest.main()
