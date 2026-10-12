"""Synthetic source-derived writer/geometry contracts, never native CFD proof."""
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import teaching_pump_motion_evidence as motion


def native(name,kind,body):return ("FoamFile {version 2.0; format ascii; class "+kind+"; object "+name+";}\n"+body+"\n").encode("ascii")


def point_bytes(points,*,location="constant/polyMesh"):
    return native("points","vectorField",str(len(points))+"\n(\n"+"\n".join("("+" ".join(format(x,".17g") for x in p)+")" for p in points)+"\n)").replace(b"object points;",('location "'+location+'"; object points;').encode())


def fixture(root,*,wrong_motion=None,partial=None,shared=False,coverage_change=None):
    """Two independent tetrahedra with a mocked upstream source admission."""
    period=60/1450;dt=period/360;writes=[x*period for x in (.25,.5,.75,1,1.25)]
    plan={"endTimeS":1.25*period,"maximumTimeStepS":dt,"expectedWriteTimesS":writes}
    mp={"axis":[0,0,1],"originM":[0,0,0],"speedRpm":1450.,"initialPhaseRad":0.,"rotationPeriodS":period,
        "angularVelocityRadS":2*math.pi*1450/60,"rotatingCellZone":"rotating-fluid","stationaryCellZone":"stationary-fluid","method":"solidBody/rotatingMotion"}
    points=[(.10,0,0),(.11,0,0),(.10,.01,0),(.10,0,.01),(.20,0,0),(.21,0,0),(.20,.01,0),(.20,0,.01)]
    faces=[(0,2,1),(0,1,3),(1,2,3),(2,0,3),(4,6,5),(4,5,7),(5,6,7),(6,4,7)]
    if shared:faces[4]=(0,6,5)
    # Physical patch identity is synthetic. Upstream CAD/native acceptance is
    # mocked; this checker independently validates source topology and motion.
    boundary='6\n(\ninterface-impeller-outlet {type patch;nFaces 1;startFace 0;}\nblade {type wall;nFaces 3;startFace 1;}\ninterface-volute-inlet {type patch;nFaces 1;startFace 4;}\nvolute-wall {type wall;nFaces 3;startFace 5;}\n'
    for name,other,original in (("pumpInterface_on_interface-impeller-outlet","pumpInterface_on_interface-volute-inlet","interface-impeller-outlet"),
        ("pumpInterface_on_interface-volute-inlet","pumpInterface_on_interface-impeller-outlet","interface-volute-inlet")):
        boundary+=name+' {type nonConformalCyclic;nFaces 0;startFace 8;neighbourPatch '+other+';originalPatch '+original+';transformType none;}\n'
    boundary+=')'
    data={"points":point_bytes(points),"owner":native("owner","labelList","8 (0 0 0 0 1 1 1 1)"),"neighbour":native("neighbour","labelList","0 ()"),
        "faces":native("faces","faceList","8 ("+"".join("3("+" ".join(map(str,f))+")" for f in faces)+")"),
        "boundary":native("boundary","polyBoundaryMesh",boundary),
        "cellZones":native("cellZones","cellZoneList","2(rotating-fluid {type cellZone;cellLabels List<label> 1(0);} stationary-fluid {type cellZone;cellLabels List<label> 1(1);})")}
    authored={"constant/dynamicMeshDict":b"SYNTHETIC-TRUSTED-ROTATING-RECIPE","system/controlDict":b"SYNTHETIC-TRUSTED-PILOT-CLOCK"}
    records=[]
    for name,raw in data.items():
        path="constant/polyMesh/"+name;target=root/"case"/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        records.append({"path":path,"bytes":len(raw),"sha256":sha256(raw).hexdigest()})
    authored_rows=[]
    for name,raw in authored.items():
        path=root/"case"/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        authored_rows.append({"path":name,"bytes":len(raw),"sha256":sha256(raw).hexdigest()})
    identity={"schemaVersion":motion.author.IDENTITY_SCHEMA,"provenance":"CLASSROOM_AUTHORED_GEOMETRY","units":"SI","axis":[0,0,1],"originM":[0,0,0],"manufacturerIdentity":None,"originalFluentSourceIdentity":None}
    raw=json.dumps(identity).encode();(root/motion.IDENTITY).write_bytes(raw)
    prep={"schemaVersion":motion.PREPARATION_SCHEMA,"mode":"physical-sliding-active-phase-pilot","sourceGeometryRoute":"CLASSROOM_AUTHORED_GEOMETRY","mainApplicationWrites":False,
        "motionPlan":mp,"temporalPlan":plan,"declaredStudyInputs":{"initialAngleRad":0},"physicalBasis":{"rotationAxis":[0,0,1],"rotationOriginM":[0,0,0]},
        "sourceGeometryIdentityFile":{"path":motion.IDENTITY,"bytes":len(raw),"sha256":sha256(raw).hexdigest()},"sourceReference":{"syntheticUpstreamFixture":True},
        "preservedNativeMeshFiles":records,"defaultFacesPresent":False,"authoredInputFiles":authored_rows}
    (root/motion.REPORT).write_text(json.dumps(prep))
    module=SimpleNamespace(MOTION_PLAN=mp,native_recipe=lambda *_,**__:authored,
        verify_output=lambda *_,**__: {"report":json.loads((root/motion.REPORT).read_bytes())})
    times=[]
    for instant in writes:
        changed=[];angle=2*math.pi*1450*instant/60
        if wrong_motion=="double":angle*=2
        if wrong_motion=="wrong-axis":angle=-angle
        for index,p in enumerate(points):
            q=(math.cos(angle)*p[0]-math.sin(angle)*p[1],math.sin(angle)*p[0]+math.cos(angle)*p[1],p[2]) if index<4 and wrong_motion!="stationary" else p
            if wrong_motion=="stator-drift" and index==7:q=(p[0]+.001,p[1],p[2])
            changed.append(q)
        path=root/"case"/format(instant,".17g");(path/"polyMesh").mkdir(parents=True);(path/"polyMesh/points").write_bytes(point_bytes(changed,location=path.name+"/polyMesh"));times.append((instant,path))
    log="Build : 12-86e126a7bc4d\nSelecting solid-body motion function rotatingMotion\n"
    for index in range(1,451):
        block="Time = "+format(index*dt,".17g")+"s\npatchToPatch: Calculating couplings between 1 source faces and 1 target faces\n    Source min/average/max coverage = 1/1/1\n    Target min/average/max coverage = 1/1/1\n    2 couplings calculated in 0.01s\nfvMeshStitcher: Connecting\n    pumpInterface_on_interface-impeller-outlet min/avg/max mesh flux error = 0/0/0\n"
        if coverage_change and index==91:block=coverage_change(block)
        log+=block
    log+="End\n";(root/"logs").mkdir();(root/"logs/01-foamRun.log").write_bytes(log.encode("utf-8"))
    if partial=="missing":times=times[:-1]
    return module,times,log


class TeachingMotionTests(unittest.TestCase):
    def check(self,root,**kwargs):
        helper,times,log=fixture(root,**kwargs)
        with patch.object(motion,"preparer",return_value=helper):return motion.inspect(root,times,log)

    def test_actual_shape_rotation_and_every_clock_coverage_are_independent_of_cfd_claims(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);result=self.check(root)
            self.assertTrue(result["coordinateMotionVerified"]);self.assertTrue(result["dynamicNccClockCoverageVerified"])
            self.assertEqual(result["diagnostics"],[]);self.assertEqual(result["coordinateMotion"]["rotorPointCount"],4)
            self.assertEqual(result["coordinateMotion"]["statorPointCount"],4);self.assertEqual(result["dynamicNccCoverage"]["nativeClockCount"],450)
            self.assertEqual(len(result["coordinateMotion"]["nativeMovedPointRecords"]),5)
            for name in ("ALEVerified","wallFluxVerified","physicalTransientQualified","periodicTransientQualified","restartVerified","actualCfdRunVerified"):
                self.assertFalse(result[name])
            for row in result["nativeEvidenceRecords"]:self.assertEqual(row["sha256"],sha256((root/row["path"]).read_bytes()).hexdigest())

    def test_wrong_axis_double_rotation_stationarity_or_stator_drift_rejects_coordinates_only(self):
        for wrong in ("wrong-axis","double","stationary","stator-drift"):
            with self.subTest(wrong=wrong),tempfile.TemporaryDirectory() as temp:
                result=self.check(Path(temp),wrong_motion=wrong)
                self.assertFalse(result["coordinateMotionVerified"]);self.assertTrue(result["dynamicNccClockCoverageVerified"])

    def test_missing_write_or_shared_rotor_stator_adjacency_cannot_prove_motion(self):
        with tempfile.TemporaryDirectory() as temp:result=self.check(Path(temp),partial="missing")
        self.assertFalse(result["coordinateMotionVerified"]);self.assertTrue(result["dynamicNccClockCoverageVerified"])
        with tempfile.TemporaryDirectory() as temp:result=self.check(Path(temp),shared=True)
        self.assertFalse(result["coordinateMotionVerified"]);self.assertFalse(result["dynamicNccClockCoverageVerified"])

    def test_missing_partial_duplicate_wrong_role_wrong_facecount_or_bad_coverage_cannot_pass(self):
        changes=(lambda b:b.replace("Source min/average/max coverage = 1/1/1\n", ""),
            lambda b:b.replace("Target min/average/max coverage = 1/1/1\n", ""),lambda b:b+b[b.index("patchToPatch"):],
            lambda b:b.replace("pumpInterface_on_interface-impeller-outlet min/avg/max", "another-patch min/avg/max"),
            lambda b:b.replace("1 source faces", "2 source faces"),lambda b:b.replace("1/1/1", ".98/1/1",1),
            lambda b:b.replace("1/1/1", "nan/1/1",1),lambda b:b.replace("    2 couplings calculated in 0.01s\n", ""))
        for change in changes:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                result=self.check(Path(temp),coverage_change=change)
                self.assertFalse(result["dynamicNccClockCoverageVerified"]);self.assertTrue(result["coordinateMotionVerified"])

    def test_only_pinned_native_selector_and_actual_log_bytes_are_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);helper,times,log=fixture(root)
            with patch.object(motion,"preparer",return_value=helper):
                result=motion.inspect(root,times,log+"ALTERED")
            self.assertFalse(result["coordinateMotionVerified"]);self.assertFalse(result["dynamicNccClockCoverageVerified"])
            old=log.replace("Selecting solid-body motion function rotatingMotion","Selecting solidBodyMotionFunction rotatingMotion")
            (root/"logs/01-foamRun.log").write_bytes(old.encode("utf-8"))
            with patch.object(motion,"preparer",return_value=helper):result=motion.inspect(root,times,old)
            self.assertFalse(result["coordinateMotionVerified"]);self.assertTrue(result["dynamicNccClockCoverageVerified"])

    def test_source_hash_phase_si_and_mrf_identity_cannot_be_inferred_or_changed(self):
        for change in ("hash","phase","SI","MRF"):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);helper,times,log=fixture(root)
                prep=json.loads((root/motion.REPORT).read_bytes())
                if change=="hash":(root/"case/constant/polyMesh/points").write_bytes(point_bytes([(0,0,0)]))
                elif change=="phase":prep["declaredStudyInputs"]["initialAngleRad"]=1
                elif change=="SI":
                    identity=json.loads((root/motion.IDENTITY).read_bytes());identity["units"]="mm";(root/motion.IDENTITY).write_text(json.dumps(identity))
                    raw=(root/motion.IDENTITY).read_bytes();prep["sourceGeometryIdentityFile"].update(bytes=len(raw),sha256=sha256(raw).hexdigest())
                else:(root/"case/constant/MRFProperties").write_bytes(b"DOUBLE_ROTATION")
                (root/motion.REPORT).write_text(json.dumps(prep))
                with patch.object(motion,"preparer",return_value=helper):result=motion.inspect(root,times,log)
                self.assertFalse(result["coordinateMotionVerified"]);self.assertFalse(result["dynamicNccClockCoverageVerified"])

    def test_native_point_header_shape_and_nonfinite_values_are_strict(self):
        good=point_bytes([(0,0,0),(1,1,1)])
        for raw in (good.replace(b"vectorField",b"labelList"),good.replace(b"2\n(",b"3\n("),good.replace(b"(1 1 1)",b"(1e500 1 1)"),good+b"unexpected"):
            with self.subTest(raw=raw),self.assertRaises(ValueError):motion.point_vectors(raw)

    def test_log_clock_rejects_missing_duplicate_nonfinite_or_oversized_step(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);helper,times,log=fixture(root)
            with patch.object(motion,"preparer",return_value=helper):inputs=motion.motion_inputs(root)
            native_times,_=motion.native_clock(log,inputs["temporalPlan"])
            with self.assertRaises(ValueError):motion.dynamic_ncc_coverage(log,native_times[::2],inputs["nccPair"],inputs["temporalPlan"])
            for changed in (log.replace("Time = ","Time = nan ",1),log.replace("Time = ","Time = 1\nTime = ",1),log.replace("Time = ","Time = ",1).replace("End\n","")):
                with self.assertRaises(ValueError):motion.native_clock(changed,inputs["temporalPlan"])

    def test_changed_evidence_after_independent_math_cannot_keep_current_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);helper,times,log=fixture(root);actual=motion.coordinate_motion
            def changed(*args):
                proof=actual(*args);(times[0][1]/"polyMesh/points").write_bytes(b"CHANGED-AFTER-PROOF");return proof
            with patch.object(motion,"preparer",return_value=helper),patch.object(motion,"coordinate_motion",side_effect=changed):result=motion.inspect(root,times,log)
            self.assertFalse(result["coordinateMotionVerified"]);self.assertFalse(result["dynamicNccClockCoverageVerified"])
            self.assertNotIn("coordinateMotion",result);self.assertNotIn("dynamicNccCoverage",result)
            self.assertTrue(result["nativeEvidenceRecords"])

    def test_missing_actual_mesh_primitive_and_invalid_metadata_cannot_inherit_namespace(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);helper,times,log=fixture(root);(root/"case/constant/polyMesh/cellZones").unlink()
            with patch.object(motion,"preparer",return_value=helper):result=motion.inspect(root,times,log)
            self.assertFalse(result["coordinateMotionVerified"]);self.assertFalse(result["dynamicNccClockCoverageVerified"])
        for raw in (b'{"x":1e500}',b'{"x":NaN}',b'{"x":1,"x":2}'):
            with self.assertRaises(ValueError):motion.json_data(raw)


if __name__=="__main__":unittest.main()
