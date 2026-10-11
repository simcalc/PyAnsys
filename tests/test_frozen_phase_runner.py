"""Synthetic field files and process mocks only; these are not pump CFD evidence."""
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools/run_frozen_rotor_phase.py"
spec = importlib.util.spec_from_file_location("phase_runner_tests", TOOL)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def field(name, values="1", *, nonuniform=False, count=2, dimensions=None):
    kind = "volVectorField" if name == "U" else "volScalarField"
    declaration = ("nonuniform List<" + ("vector" if name == "U" else "scalar") + "> " + str(count)
                   + "\n(\n" + values + "\n)") if nonuniform else "uniform " + values
    return (runner.preparation.base.foam_header(name, kind) + "dimensions [" + (dimensions or runner.FIELD_DIMENSIONS[name])
            + "];\ninternalField " + declaration + ";\nboundaryField {}\n").encode("ascii")


def write_points(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(runner.preparation.base.foam_header("points", "vectorField") + str(len(values)) + "\n(\n"
                    + "\n".join("(" + " ".join(str(v) for v in row) + ")" for row in values) + "\n)\n", encoding="ascii")


MOTION_POINTS=((1,0,0),(2,0,0),(1,1,0),(1,0,1),(4,0,0),(5,0,0),(4,1,0),(4,0,1))


def motion_mesh_files(point_order=None):
    """Minimal independent labelled rotor/stator volumes, synthetic not CFD."""
    base=runner.preparation.base
    order=tuple(range(8)) if point_order is None else point_order;inverse={old:new for new,old in enumerate(order)}
    faces=((0,2,1),(0,1,3),(1,2,3),(2,0,3),(4,6,5),(4,5,7),(5,6,7),(6,4,7))
    contents={"faces":('faceList','8\n('+' '.join('3('+' '.join(str(inverse[v]) for v in f)+')' for f in faces)+')\n'),
              "owner":('labelList','8\n(0 0 0 0 1 1 1 1)\n'),"neighbour":('labelList','0\n()\n'),
              "boundary":('polyBoundaryMesh','2\n(inlet {type patch; nFaces 4; startFace 0;} outlet {type patch; nFaces 4; startFace 4;})\n'),
              "cellZones":('cellZoneList','2\n(rotating-fluid {type cellZone; cellLabels List<label> 1(0);}\nstationary-fluid {type cellZone; cellLabels List<label> 1(1);})\n')}
    contents['points']=('vectorField','8\n(\n'+'\n'.join('('+' '.join(str(v) for v in MOTION_POINTS[i])+')' for i in order)+'\n)\n')
    return {name:base.foam_header(name,kind)+body for name,(kind,body) in contents.items()}


def motion_fixture(root, *, stage4=False, start=0., point_order=None):
    """Two labelled synthetic tetrahedra; this is not native CFD evidence."""
    original=MOTION_POINTS if point_order is None else tuple(MOTION_POINTS[i] for i in point_order);base=runner.preparation.base
    mesh_path=root/'case/constant/polyMesh';mesh_path.mkdir(parents=True)
    for name,content in motion_mesh_files(point_order).items():(mesh_path/name).write_bytes(content.encode('ascii'))
    study=runner.preparation.protocol.prepare_study();end=.15;dt=60/study['input']['speedRpm']/360
    files=runner.preparation.native_recipe(study,300000,"transient-sliding",dt,end)
    files['system/controlDict']=files['system/controlDict'].replace('startTime 0;','startTime '+format(start,'.17g')+';')
    def record(relative):
        raw=(root/relative).read_bytes();return {'path':relative,'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
    authored=[]
    for name in ('constant/dynamicMeshDict','system/controlDict'):
        path=root/'case'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(files[name].encode('ascii'));authored.append({**record('case/'+name),'path':name})
    meshes=[{**record('case/constant/polyMesh/'+name),'path':'constant/polyMesh/'+name} for name in sorted(runner.preparation.MESH_FILES)]
    geometry={'schemaVersion':'untirta.frozen-rotor-fixed-geometry-identity.v1','exampleDataCommit':base.inventory.PIN,
              'fixtureKind':'SYNTHETIC-UNIT-TEST-NOT-CFD','mainApplicationWrites':False}
    (root/'geometry-identity.json').write_bytes(runner.preparation.canonical(geometry));g=record('geometry-identity.json')
    prep_name='stage4-branch-preparation.json' if stage4 else 'phase-change-preparation.json'
    manifest_name='stage4-input-manifest.json' if stage4 else 'phase-input-manifest.json'
    study_name='stage4-classroom-study.json' if stage4 else 'phase-classroom-study.json'
    prep={'schemaVersion':'untirta.frozen-rotor-stage4-branch-preparation.v1' if stage4 else runner.preparation.SCHEMA,
          'mainApplicationWrites':False,'declaredStudyInputs':study['input'],'physicalBasis':study['physicalBasis'],
          'sourceGeometryIdentityFile':g,'sourceGeometryControlSha256':sha256(runner.preparation.canonical([g])).hexdigest(),
          'authoredInputFiles':authored,'referenceMeshFiles' if stage4 else 'preservedNativeMeshFiles':meshes}
    if start>0:
        initial_path='case/'+format(start,'.17g')+'/polyMesh/points'
        write_points(root/initial_path,moved_points(original,start))
        checkpoint_files=[record(initial_path)];checkpoint_sha=sha256(runner.preparation.canonical(checkpoint_files)).hexdigest()
        checkpoint={'schemaVersion':'untirta.frozen-rotor-stage4-native-checkpoint.v1','physicalTimeS':start,
                    'files':checkpoint_files,'checkpointSha256':checkpoint_sha,'mainApplicationWrites':False,
                    'fixtureKind':'SYNTHETIC-MOTION-CHECKPOINT-ONLY-NOT-CFD-STATE'}
        (root/'stage4-checkpoint.json').write_bytes(runner.preparation.canonical(checkpoint));prep['resumeCheckpointSha256']=checkpoint_sha
    (root/prep_name).write_bytes(runner.preparation.canonical(prep))
    allrows=[record(prep_name),g]+[{**r,'path':'case/'+r['path']} for r in meshes+authored]
    manifest={'schemaVersion':'untirta.frozen-rotor-stage4-input-manifest.v1' if stage4 else 'untirta.frozen-rotor-phase-input-manifest.v1',
              'mode':'transient-sliding','studyInputs':study['input'],'physicalBasis':study['physicalBasis'],
              'preparationReportSha256':record(prep_name)['sha256'],'files':allrows,
              'temporalPlan':{'initialTimeS':start,'endTimeS':end,'rotationPeriodS':60/study['input']['speedRpm']}}
    (root/manifest_name).write_bytes(runner.preparation.canonical(manifest));(root/study_name).write_bytes(runner.preparation.canonical(study))
    return original,study,prep_name,manifest_name


def moved_points(original,time_s,rpm=1450):
    angle=2*math.pi*rpm*time_s/60;c,s=math.cos(angle),math.sin(angle)
    return tuple((c*x-s*y,s*x+c*y,z) if i<4 else (x,y,z) for i,(x,y,z) in enumerate(original))


def motion_log(*times):
    return 'Selecting solidBodyMotionFunction rotatingMotion\n'+''.join('Time = '+format(t,'.17g')+'s\n' for t in times)


class MockStream:
    def fileno(self):
        return 4242

    def close(self):
        pass


class MockSelector:
    def __init__(self):
        self.stream = None

    def register(self, stream, event):
        self.stream = stream

    def get_map(self):
        return {1: self.stream} if self.stream else {}

    def select(self, timeout):
        key = type("Key", (), {"fileobj": self.stream})()
        return [(key, 1)]

    def unregister(self, stream):
        self.stream = None

    def close(self):
        pass


class MockProcess:
    def __init__(self, *, running=False, code=0, ignore_term=False):
        self.pid, self.running, self.code = 424242, running, code
        self.ignore_term, self.waits, self.stdout = ignore_term, 0, MockStream()

    def poll(self):
        return None if self.running else self.code

    def wait(self, timeout):
        self.waits += 1
        if self.running and self.ignore_term and self.waits == 1:
            raise subprocess.TimeoutExpired("synthetic-process", timeout)
        self.running = False
        return self.code


class PhaseFieldContracts(unittest.TestCase):
    def test_absolute_pressure_dimensions_are_distinct_from_single_phase_kinematic_pressure(self):
        row = runner.finite_field(field("p", "50000 51000", nonuniform=True), "p", 2)
        self.assertTrue(row["allInternalValuesChecked"])
        self.assertEqual((row["minimum"], row["maximum"]), (50000, 51000))
        with self.assertRaises(runner.PhaseExecutionIssue):
            runner.finite_field(field("p", dimensions="0 2 -2 0 0 0 0"), "p", 2)

    def test_vector_shape_count_and_uniform_scalar_syntax_are_exact(self):
        self.assertEqual(runner.finite_field(field("U", "(1 2 3) (4 5 6)", nonuniform=True), "U", 2)["internalValueCount"], 6)
        for raw, name in ((field("U", "1 2 3"), "U"), (field("U", "(1 2 3 4 5 6)", nonuniform=True), "U"),
                          (field("p", "(1)"), "p"), (field("p", "1", nonuniform=True), "p")):
            with self.subTest(name=name), self.assertRaises(runner.PhaseExecutionIssue):
                runner.finite_field(raw, name, 2)

    def test_nonfinite_overflow_negative_turbulence_and_unbounded_phase_fraction_reject(self):
        for name, value in (("p", "NaN"), ("p", "1e309"), ("k", "-1"), ("omega", "-1"),
                            ("nut", "-1"), ("alpha.water", "1.00001"), ("alpha.water", "-0.00001")):
            with self.subTest(name=name, value=value), self.assertRaises(runner.PhaseExecutionIssue):
                runner.finite_field(field(name, value), name, 2)
        raw = field("p").replace(b"boundaryField {}", b"boundaryField { inlet { value uniform 1e309; } }")
        with self.assertRaises(runner.PhaseExecutionIssue):
            runner.finite_field(raw, "p", 2)
        row = runner.finite_field(field("alpha.water", "1.0000000005"), "alpha.water", 2)
        self.assertFalse(row["boundaryPhaseFractionBoundsVerified"])

    def test_actual_endpoint_requires_all_fields_and_matching_native_solver_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); endpoint = root / "case/0.3"; endpoint.mkdir(parents=True)
            for name in runner.END_FIELDS:
                (endpoint / name).write_bytes(field(name, "(0 0 0)" if name == "U" else "1"))
            rows, times = runner.final_fields(root, {"endTimeS": .3}, 2, "Time = 0.3\nEnd\n")
            self.assertEqual(len(rows), 7)
            self.assertEqual(times[-1][0], .3)
            self.assertEqual(len(runner.final_fields(root, {"endTimeS": .3}, 2, "Time = 0.3s\nEnd\n")[0]), 7)
            for log, end in (("Time = 0.2\nEnd\n", .3), ("Time = 0.3\nEnd\n", .4)):
                with self.assertRaises(runner.PhaseExecutionIssue):
                    runner.final_fields(root, {"endTimeS": end}, 2, log)
            (endpoint / "p_rgh").unlink()
            with self.assertRaises(runner.preparation.base.CaseIssue):
                runner.final_fields(root, {"endTimeS": .3}, 2, "Time = 0.3\nEnd\n")

    def test_sliding_requires_actual_nonroundoff_motion_not_only_dictionary_or_log(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); original,_,_,_=motion_fixture(root);time_s=.03
            endpoint = root / "case/0.03"; write_points(endpoint / "polyMesh/points", original)
            log = motion_log(time_s)
            with self.assertRaises(runner.PhaseExecutionIssue):
                runner.rotor_motion(root, [(time_s, endpoint)], log)
            write_points(endpoint / "polyMesh/points", moved_points(original,time_s))
            row = runner.rotor_motion(root, [(time_s, endpoint)], log)
            self.assertTrue(row["nativeMovedPointsObserved"])
            self.assertTrue(row['rigidRotorMotionVerified']);self.assertTrue(row['stationaryPointIdentityVerified'])
            self.assertEqual((row['rotorPointCount'],row['statorPointCount']),(4,4))
            self.assertFalse(row["periodicTransientQualified"])
            self.assertFalse(row["nativeCoordinateUnitsVerified"])
            self.assertFalse(row['actualWallVelocityChecked']);self.assertFalse(row['actualMeshFluxChecked']);self.assertFalse(row['fullStateRestartVerified'])
            with self.assertRaises(runner.PhaseExecutionIssue):
                runner.rotor_motion(root, [(time_s, endpoint)], "motion configured but not selected")

    def test_wrong_axis_direction_double_rotation_stator_drift_and_missing_motion_reject(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);original,_,_,_=motion_fixture(root);t=.03;endpoint=root/'case/0.03'
            correct=moved_points(original,t)
            wrong_axis=tuple((x,math.cos(2*math.pi*1450*t/60)*y-math.sin(2*math.pi*1450*t/60)*z,math.sin(2*math.pi*1450*t/60)*y+math.cos(2*math.pi*1450*t/60)*z) if i<4 else (x,y,z) for i,(x,y,z) in enumerate(original))
            drift=list(correct);drift[4]=(correct[4][0]+1e-4,*correct[4][1:])
            for values in (wrong_axis,moved_points(original,-t),moved_points(original,2*t),drift,original):
                write_points(endpoint/'polyMesh/points',values)
                with self.assertRaisesRegex(runner.PhaseExecutionIssue,'rotor/stator|moved points'):
                    runner.rotor_motion(root,[(t,endpoint)],motion_log(t))
            (endpoint/'polyMesh/points').unlink()
            with self.assertRaisesRegex(runner.PhaseExecutionIssue,'actual moved-point'):runner.rotor_motion(root,[(t,endpoint)],motion_log(t))

    def test_restart_uses_global_noninteger_phase_and_retains_original_reference_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);original,_,_,_=motion_fixture(root,stage4=True,start=.02);t=.03;endpoint=root/'case/0.03'
            source=root/'case/constant/polyMesh/points';before=source.read_bytes()
            write_points(endpoint/'polyMesh/points',moved_points(original,t))
            initial=root/'case'/format(.02,'.17g')
            result=runner.rotor_motion(root,[(.02,initial),(t,endpoint)],motion_log(t))
            self.assertAlmostEqual(result['nativeMovedPointRecords'][-1]['rotationAngleRad'],2*math.pi*1450*t/60)
            self.assertEqual(source.read_bytes(),before)
            write_points(endpoint/'polyMesh/points',moved_points(original,t-.02))
            with self.assertRaises(runner.PhaseExecutionIssue):runner.rotor_motion(root,[(t,endpoint)],motion_log(t))

    def test_resumed_checkpoint_point_hash_is_required_before_clock_exception(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);original,_,_,_=motion_fixture(root,stage4=True,start=.02);t=.03;endpoint=root/'case/0.03'
            write_points(endpoint/'polyMesh/points',moved_points(original,t));initial=root/'case'/format(.02,'.17g')
            write_points(initial/'polyMesh/points',original)
            with self.assertRaisesRegex(runner.PhaseExecutionIssue,'immutable copied checkpoint'):
                runner.rotor_motion(root,[(.02,initial),(t,endpoint)],motion_log(t))

    def test_copied_checkpoint_alone_cannot_establish_new_native_motion(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);motion_fixture(root,stage4=True,start=.02);initial=root/'case'/format(.02,'.17g')
            with self.assertRaisesRegex(runner.PhaseExecutionIssue,'new logged post-start'):
                runner.rotor_motion(root,[(.02,initial)],motion_log(.03))

    def test_nonzero_declared_initial_phase_cannot_be_silently_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);original,_,prep_name,manifest_name=motion_fixture(root);t=.03;endpoint=root/'case/0.03'
            write_points(endpoint/'polyMesh/points',moved_points(original,t))
            prep=json.loads((root/prep_name).read_bytes());manifest=json.loads((root/manifest_name).read_bytes())
            study_path=root/'phase-classroom-study.json';study=json.loads(study_path.read_bytes())
            for values in (prep['declaredStudyInputs'],manifest['studyInputs'],study['input']):values['initialAngleRad']=.1
            (root/prep_name).write_bytes(runner.preparation.canonical(prep));manifest['preparationReportSha256']=sha256((root/prep_name).read_bytes()).hexdigest()
            (root/manifest_name).write_bytes(runner.preparation.canonical(manifest));study_path.write_bytes(runner.preparation.canonical(study))
            with self.assertRaisesRegex(runner.PhaseExecutionIssue,'zero initial rotor phase'):runner.rotor_motion(root,[(t,endpoint)],motion_log(t))

    def test_motion_labels_come_from_bound_cell_membership_not_point_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);original,_,prep_name,manifest_name=motion_fixture(root);t=.03;endpoint=root/'case/0.03'
            source=root/'case/constant/polyMesh/cellZones';source.write_text(source.read_text().replace('1(0)','1(9)'))
            write_points(endpoint/'polyMesh/points',moved_points(original,t))
            with self.assertRaisesRegex(runner.PhaseExecutionIssue,'SHA256'):runner.rotor_motion(root,[(t,endpoint)],motion_log(t))
            # Even a caller rehashing a malformed zone cannot create valid labels.
            prep=json.loads((root/prep_name).read_bytes());manifest=json.loads((root/manifest_name).read_bytes())
            for row in prep['preservedNativeMeshFiles']:
                if row['path'].endswith('cellZones'):row.update(bytes=len(source.read_bytes()),sha256=sha256(source.read_bytes()).hexdigest())
            (root/prep_name).write_bytes(runner.preparation.canonical(prep))
            for row in manifest['files']:
                if row['path'].endswith('cellZones'):row.update(bytes=len(source.read_bytes()),sha256=sha256(source.read_bytes()).hexdigest())
            manifest['preparationReportSha256']=sha256((root/prep_name).read_bytes()).hexdigest();(root/manifest_name).write_bytes(runner.preparation.canonical(manifest))
            with self.assertRaises(ValueError):runner.rotor_motion(root,[(t,endpoint)],motion_log(t))

    def test_interleaved_native_point_indices_preserve_zone_derived_motion(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);order=(0,4,1,5,2,6,3,7);original,_,_,_=motion_fixture(root,point_order=order);t=.03;endpoint=root/'case/0.03'
            expected=moved_points(MOTION_POINTS,t);write_points(endpoint/'polyMesh/points',tuple(expected[i] for i in order))
            result=runner.rotor_motion(root,[(t,endpoint)],motion_log(t))
            self.assertEqual(result['sourcePointLabelsSha256'],sha256(bytes(old<4 for old in order)).hexdigest())
            self.assertTrue(result['rigidRotorMotionVerified']);self.assertTrue(result['stationaryPointIdentityVerified'])

    def test_shared_rotor_stator_cell_point_adjacency_is_unsupported(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);original,_,prep_name,manifest_name=motion_fixture(root);t=.03;endpoint=root/'case/0.03'
            source=root/'case/constant/polyMesh/faces';source.write_bytes(source.read_bytes().replace(b'3(4 6 5)',b'3(0 6 5)'))
            prep=json.loads((root/prep_name).read_bytes());manifest=json.loads((root/manifest_name).read_bytes())
            for rows in (prep['preservedNativeMeshFiles'],manifest['files']):
                for row in rows:
                    if row['path'].endswith('/faces'):row.update(bytes=len(source.read_bytes()),sha256=sha256(source.read_bytes()).hexdigest())
            (root/prep_name).write_bytes(runner.preparation.canonical(prep));manifest['preparationReportSha256']=sha256((root/prep_name).read_bytes()).hexdigest();(root/manifest_name).write_bytes(runner.preparation.canonical(manifest))
            write_points(endpoint/'polyMesh/points',moved_points(original,t))
            with self.assertRaisesRegex(runner.PhaseExecutionIssue,'disjoint rotor/stator'):runner.rotor_motion(root,[(t,endpoint)],motion_log(t))

    def test_current_clock_paths_identity_and_declared_axis_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);original,_,prep_name,manifest_name=motion_fixture(root);t=.03;endpoint=root/'case/0.03'
            write_points(endpoint/'polyMesh/points',moved_points(original,t))
            for times,log in (([(t,endpoint)],motion_log(.04)), ([(.04,endpoint)],motion_log(.04)),
                              ([(t,endpoint),(t,endpoint)],motion_log(t)), ([(float('nan'),endpoint)],motion_log(t))):
                with self.assertRaises(runner.PhaseExecutionIssue):runner.rotor_motion(root,times,log)
            axis=root/'case/constant/dynamicMeshDict';axis.write_text(axis.read_text().replace('axis (0 0 1)','axis (1 0 0)'))
            prep=json.loads((root/prep_name).read_bytes());manifest=json.loads((root/manifest_name).read_bytes())
            for rows in (prep['authoredInputFiles'],manifest['files']):
                for row in rows:
                    if row['path'].endswith('dynamicMeshDict'):row.update(bytes=len(axis.read_bytes()),sha256=sha256(axis.read_bytes()).hexdigest())
            (root/prep_name).write_bytes(runner.preparation.canonical(prep));manifest['preparationReportSha256']=sha256((root/prep_name).read_bytes()).hexdigest();(root/manifest_name).write_bytes(runner.preparation.canonical(manifest))
            with self.assertRaisesRegex(runner.PhaseExecutionIssue,'exact source-bound'):runner.rotor_motion(root,[(t,endpoint)],motion_log(t))

    def test_motion_tolerance_and_input_change_during_comparison_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);original,_,_,_=motion_fixture(root);t=.03;endpoint=root/'case/0.03';correct=list(moved_points(original,t))
            correct[0]=(correct[0][0]+1e-12,*correct[0][1:]);write_points(endpoint/'polyMesh/points',correct)
            self.assertTrue(runner.rotor_motion(root,[(t,endpoint)],motion_log(t))['rigidRotorMotionVerified'])
            correct[0]=(correct[0][0]+1e-6,*correct[0][1:]);write_points(endpoint/'polyMesh/points',correct)
            with self.assertRaises(runner.PhaseExecutionIssue):runner.rotor_motion(root,[(t,endpoint)],motion_log(t))
            write_points(endpoint/'polyMesh/points',moved_points(original,t));read_points=runner.points
            def mutate(path):
                result=read_points(path)
                if Path(path)==endpoint/'polyMesh/points':(root/'case/constant/polyMesh/owner').write_text('changed after binding')
                return result
            with patch.object(runner,'points',side_effect=mutate),self.assertRaisesRegex(runner.PhaseExecutionIssue,'changed during'):
                runner.rotor_motion(root,[(t,endpoint)],motion_log(t))

    def test_point_count_and_no_trailing_payload_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "points"; write_points(path, ((0, 0, 0),))
            raw = path.read_text(); path.write_text(raw.replace("(0 0 0)", "(0 0 0) unexpected"))
            with self.assertRaises(runner.PhaseExecutionIssue):
                runner.points(path)

    def test_only_actual_authored_sampler_names_and_bounded_dat_files_are_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); path = root / "case/postProcessing/weightedInlet/0/surfaceFieldValue.dat"
            path.parent.mkdir(parents=True); path.write_text("# SYNTHETIC-NOT-CFD\n0.3 1 2 3\n")
            records = runner.sampling_records(root)
            self.assertEqual(records[0]["sha256"], sha256(path.read_bytes()).hexdigest())
            bad = root / "case/postProcessing/unknownSampler/0/surfaceFieldValue.dat"
            bad.parent.mkdir(parents=True); bad.write_text("0 0\n")
            with self.assertRaises(runner.PhaseExecutionIssue):
                runner.sampling_records(root)


class PhaseProcessContracts(unittest.TestCase):
    def execute_fixture(self, root, payload=b"End\n", process=None, extra=None, clock=None, deadline=10):
        (root / "logs").mkdir()
        process = process or MockProcess()
        raw = b"SYNTHETIC-NATIVE-EXECUTABLE-NOT-INSTALLED"
        runtime = {"binaries": [{"program": "foamRun", "path": "/opt/openfoam12/bin/foamRun", "bytes": len(raw), "sha256": sha256(raw).hexdigest()}]}
        real_read = runner.preparation.base.read_regular
        stack = ExitStack()
        stack.enter_context(patch.object(runner, "Path", side_effect=lambda value: Path(value).resolve()
                                         if str(value).startswith("/opt/") else Path(value)))
        stack.enter_context(patch.object(runner.preparation.base, "read_regular", side_effect=lambda path, limit: raw if path.name == "foamRun" else real_read(path, limit)))
        stack.enter_context(patch.object(runner.selectors, "DefaultSelector", MockSelector))
        stack.enter_context(patch.object(runner.os, "read", side_effect=[payload, b""]))
        stack.enter_context(patch.object(runner.os, "getpgid", return_value=process.pid, create=True))
        kills = stack.enter_context(patch.object(runner.os, "killpg", create=True))
        stack.enter_context(patch.object(signal, "SIGKILL", 9, create=True))
        stack.enter_context(patch.object(runner.time, "monotonic", side_effect=clock if clock else lambda: 1))
        def launch(*args, **options):
            if extra:
                extra(root)
            return process
        factory = stack.enter_context(patch.object(runner.subprocess, "Popen", side_effect=launch))
        self.addCleanup(stack.close)
        return process, factory, kills, lambda: runner.execute(runner.commands(root)[1], root / "logs/01-foamRun.log", root, deadline, runtime)

    def test_fixed_native_success_is_hashed_and_never_qualified_by_process_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            _, factory, kills, invoke = self.execute_fixture(Path(temp))
            row, text = invoke()
            self.assertEqual(row["sha256"], sha256(b"End\n").hexdigest())
            self.assertEqual(text, "End\n")
            self.assertEqual(row["path"], "logs/01-foamRun.log")
            self.assertNotIn("qualifiedBaseline", row)
            self.assertTrue(factory.call_args.kwargs["start_new_session"])
            self.assertNotIn("shell", factory.call_args.kwargs)
            kills.assert_not_called()

    def test_nonzero_fatal_or_missing_End_is_not_success(self):
        for payload, code in ((b"End\n", 2), (b"FOAM FATAL ERROR\nEnd\n", 0), (b"Time = 0.3\n", 0)):
            with tempfile.TemporaryDirectory() as temp:
                _, _, _, invoke = self.execute_fixture(Path(temp), payload, MockProcess(code=code))
                with self.subTest(code=code), self.assertRaises(runner.PhaseExecutionIssue):
                    invoke()

    def test_completed_process_cannot_skip_completion_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            _, _, kills, invoke = self.execute_fixture(Path(temp), clock=[1, 1, 1, 1, 3], deadline=2)
            with self.assertRaisesRegex(runner.PhaseExecutionIssue, "completion"):
                invoke()
            kills.assert_not_called()

    def test_owned_timeout_escalation_and_group_mismatch_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            process, _, kills, invoke = self.execute_fixture(Path(temp), process=MockProcess(running=True, ignore_term=True), clock=[1, 1, 3], deadline=2)
            with self.assertRaises(runner.PhaseExecutionIssue):
                invoke()
            self.assertEqual([call.args for call in kills.call_args_list], [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)])
        process = MockProcess(running=True)
        with patch.object(runner.os, "getpgid", return_value=999, create=True), patch.object(runner.os, "killpg", create=True) as kill:
            with self.assertRaises(runner.PhaseExecutionIssue):
                runner.stop_owned(process)
            kill.assert_not_called()

    def test_fast_log_and_workspace_overflow_stop_without_unbounded_capture(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, "MAX_LOG", 16):
            process, _, kills, invoke = self.execute_fixture(Path(temp), b"x" * 128, MockProcess(running=True))
            with self.assertRaises(runner.PhaseExecutionIssue):
                invoke()
            self.assertEqual((Path(temp) / "logs/01-foamRun.log").stat().st_size, 16)
            kills.assert_called_once_with(process.pid, signal.SIGTERM)
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, "MAX_WORKSPACE", 32):
            _, _, _, invoke = self.execute_fixture(Path(temp), extra=lambda root: (root / "large.bin").write_bytes(b"x" * 64))
            with self.assertRaises(runner.PhaseExecutionIssue):
                invoke()

    def test_unapproved_commands_reject_before_launch_and_child_environment_filters_secrets(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(runner.subprocess, "Popen") as factory:
            with self.assertRaises(runner.PhaseExecutionIssue):
                runner.execute(["foamRun", "-case", temp, "-postProcess"], Path(temp) / "log", Path(temp), 100, {})
            factory.assert_not_called()
        with patch.dict(os.environ, {"GH_TOKEN": "SYNTHETIC", "FLUENT_LICENSE": "SYNTHETIC", "CLOUDFLARE_API_TOKEN": "SYNTHETIC", "PATH": "native-path"}, clear=True):
            environment = runner.child_environment()
            self.assertEqual(environment["PATH"], "native-path")
            self.assertEqual(environment["OMP_NUM_THREADS"], "1")
            self.assertFalse(set(environment) & {"GH_TOKEN", "FLUENT_LICENSE", "CLOUDFLARE_API_TOKEN"})

    def test_wrong_runtime_context_rejects_before_preparation(self):
        expected = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "simcalc/PyAnsys", "GITHUB_EVENT_NAME": "workflow_dispatch",
                    "RUNNER_OS": "Linux", "RUNNER_ENVIRONMENT": "github-hosted", "WM_PROJECT_VERSION": "12", "WM_PROJECT_DIR": "/opt/openfoam12"}
        for key, value in (("GITHUB_REPOSITORY", "simcalc/npsh"), ("GITHUB_EVENT_NAME", "push"), ("WM_PROJECT_VERSION", "13")):
            with patch.object(runner.sys, "platform", "linux"), patch.dict(os.environ, {**expected, key: value}, clear=True), \
                    patch.object(runner.preparation, "prepare") as prepare:
                with self.assertRaises(runner.PhaseExecutionIssue):
                    runner.run("unused", {}, "unused", pressure_absolute_pa=50000, mode="steady-sweep", dt_s=.0001, duration_s=.3)
                prepare.assert_not_called()

    def test_exact_package_and_two_hash_bound_native_identities_are_required(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(runner, "Path", side_effect=lambda value: Path(value).resolve()
                                             if str(value).startswith("/opt/") else Path(value)))
            query = stack.enter_context(patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "20250206", "")))
            stack.enter_context(patch.object(runner.shutil, "which", side_effect=lambda name: "/opt/openfoam12/bin/" + name))
            stack.enter_context(patch.object(runner.preparation.base, "read_regular", side_effect=lambda path, limit: b"SYNTHETIC-NATIVE-BYTES:" + path.name.encode()))
            row = runner.program_identity()
            self.assertEqual(row["packageVersion"], "20250206")
            self.assertEqual([record["program"] for record in row["binaries"]], ["checkMesh", "foamRun"])
            for record in row["binaries"]:
                raw = b"SYNTHETIC-NATIVE-BYTES:" + record["program"].encode()
                self.assertEqual(record["bytes"], len(raw))
                self.assertEqual(record["sha256"], sha256(raw).hexdigest())
            query.assert_called_once()

    def test_wrong_package_missing_binary_and_other_installation_are_rejected(self):
        for version, executable in (("20250206\n", "/opt/openfoam12/bin/checkMesh"), ("20250206", None),
                                    ("20250206", "/usr/bin/checkMesh"), ("20250206", "/opt/openfoam120/bin/checkMesh")):
            with ExitStack() as stack:
                stack.enter_context(patch.object(runner, "Path", side_effect=lambda value: Path(value).resolve()
                                                 if str(value).startswith("/opt/") else Path(value)))
                stack.enter_context(patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, version, "")))
                stack.enter_context(patch.object(runner.shutil, "which", return_value=executable))
                with self.subTest(version=version, path=executable), self.assertRaises(runner.PhaseExecutionIssue):
                    runner.program_identity()


class PhaseLifecycleContracts(unittest.TestCase):
    def fixture(self, root, *, failed_mesh=False, failed_solver=False, empty_startup_log=False, changed_input=False, source_changed=False, sliding=False):
        output = root / "execution"
        def prepare(*args, **kwargs):
            output.mkdir(); (output / "case/system").mkdir(parents=True)
            authored = output / "case/system/controlDict"; authored.write_bytes(b"SYNTHETIC-CONTROL-NOT-NATIVE\n")
            rows = [{"path": "system/controlDict", "bytes": authored.stat().st_size, "sha256": sha256(authored.read_bytes()).hexdigest()}]
            mesh_path = output / "case/constant/polyMesh/points"; write_points(mesh_path, ((0, 0, 0),))
            mesh = [{"path": "constant/polyMesh/points", "bytes": mesh_path.stat().st_size, "sha256": sha256(mesh_path.read_bytes()).hexdigest()}]
            report = {"nativeCommandArguments": runner.commands(output), "authoredInputFiles": rows, "preservedNativeMeshFiles": mesh,
                      "authoredCavitationModelSha256": "a" * 64, "sourceBaselineEvidenceSha256": "b" * 64,
                      "temporalPlan": {"endTimeS": .3}, "inletAbsoluteTotalPressurePa": 50000}
            (output / "phase-change-preparation.json").write_text(json.dumps(report))
            return report
        def execute(arguments, log, workspace, deadline, runtime):
            if arguments[0] == "checkMesh":
                text = "Mesh OK.\nFailed 1 mesh checks\nEnd\n" if failed_mesh else "SYNTHETIC-NOT-CFD\nMesh OK.\nEnd\n"
            else:
                text = "SYNTHETIC-NOT-CFD\nSelecting cavitation model SchnerrSauer\nTime = 0.3\nEnd\n"
            log.write_text(text)
            if failed_solver and arguments[0] == "foamRun":
                if empty_startup_log:
                    log.write_bytes(b"")
                raise runner.PhaseExecutionIssue("Synthetic native correction fixture")
            if changed_input and arguments[0] == "foamRun":
                (output / "case/system/controlDict").write_text("changed bytes")
            return {"program": arguments[0], "exitCode": 0, "path": "logs/" + log.name,
                    "bytes": log.stat().st_size, "sha256": sha256(log.read_bytes()).hexdigest()}, text
        stack = ExitStack()
        stack.enter_context(patch.object(runner, "context_guard"))
        stack.enter_context(patch.object(runner, "program_identity", return_value={"fixtureKind": "SYNTHETIC-NOT-NATIVE"}))
        stack.enter_context(patch.object(runner, "phase_manifest", return_value={"manifest": {"fixtureKind": "SYNTHETIC-NOT-NATIVE"}, "study": {"fixtureKind": "SYNTHETIC-NOT-NATIVE"}}))
        stack.enter_context(patch.object(runner.preparation, "prepare", side_effect=prepare))
        stack.enter_context(patch.object(runner.preparation, "verify_baseline", side_effect=runner.PhaseExecutionIssue("Changed synthetic upstream bytes") if source_changed else None))
        execution = stack.enter_context(patch.object(runner, "execute", side_effect=execute))
        stack.enter_context(patch.object(runner.reduction, "parse_mesh_quality", side_effect=runner.PhaseExecutionIssue("Failed mesh fixture") if failed_mesh else None,
                                        return_value={"meshCellCount": 2, "minimumCellVolumeM3": 1, "totalCellVolumeM3": 2}))
        stack.enter_context(patch.object(runner, "final_fields", return_value=([{"fixtureKind": "SYNTHETIC-END-FIELD"}], [(.3, output / "case/0.3")])))
        motion = stack.enter_context(patch.object(runner, "rotor_motion", return_value={"fixtureKind": "SYNTHETIC-MOVED-POINTS"}))
        self.addCleanup(stack.close)
        packet = {"schemaVersion": runner.preparation.NATIVE_REDUCTION_SCHEMA}
        return output, execution, motion, lambda: runner.run(root / "baseline", packet, output, pressure_absolute_pa=50000,
                                                           mode="transient-sliding" if sliding else "steady-sweep", dt_s=.0001, duration_s=.3)

    def test_mock_success_records_execution_scope_without_research_qualification(self):
        for sliding in (False, True):
            with tempfile.TemporaryDirectory() as temp:
                output, _, motion, invoke = self.fixture(Path(temp), sliding=sliding)
                result = invoke(); report = json.loads((output / "phase-change-execution.json").read_bytes())
                self.assertTrue(result["actualCfdRunVerified"])
                self.assertEqual(report["physicalTransientExecuted"], sliding)
                self.assertEqual(report["stationaryMrfRelaxationExecuted"], not sliding)
                self.assertEqual(motion.call_count, int(sliding))
                self.assertTrue(report["inputArtifactsImmutable"])
                self.assertTrue(report["sourceBaselineImmutable"])
                self.assertEqual(len(report["retainedNativeLogRecords"]), 2)
                for key in ("qualifiedBaseline", "phaseFamilyReferenceQualified", "cavitationSweepExecuted", "npsh3Verified", "restartVerified", "timeStepConvergenceVerified", "mainApplicationWrites"):
                    self.assertIs(report[key], False)
                before = (output / "phase-change-execution.json").read_bytes()
                with self.assertRaises(FileExistsError):
                    invoke()
                self.assertEqual((output / "phase-change-execution.json").read_bytes(), before)

    def test_failed_native_stage_retains_actual_fixture_logs_and_stops_dependent_stage(self):
        for mesh_failed in (False, True):
            with tempfile.TemporaryDirectory() as temp:
                output, execute, _, invoke = self.fixture(Path(temp), failed_mesh=mesh_failed, failed_solver=not mesh_failed)
                result = invoke(); report = json.loads((output / "phase-change-execution.json").read_bytes())
                self.assertFalse(result["actualCfdRunVerified"])
                self.assertEqual(execute.call_count, 1 if mesh_failed else 2)
                self.assertEqual(len(report["retainedNativeLogRecords"]), execute.call_count)
                self.assertFalse(report["finiteNativeEndFieldsVerified"])

    def test_changed_authored_inputs_or_source_baseline_cannot_be_clean_execution(self):
        for changed_input in (False, True):
            with tempfile.TemporaryDirectory() as temp:
                output, _, _, invoke = self.fixture(Path(temp), changed_input=changed_input, source_changed=not changed_input)
                self.assertFalse(invoke()["actualCfdRunVerified"])
                report = json.loads((output / "phase-change-execution.json").read_bytes())
                self.assertEqual(report["reasonCode"], "CFD_PHASE_SOURCE_IMMUTABILITY_REQUIRED")

    def test_empty_native_startup_log_is_retained_without_losing_immutable_correction_report(self):
        with tempfile.TemporaryDirectory() as temp:
            output, _, _, invoke = self.fixture(Path(temp), failed_solver=True, empty_startup_log=True)
            self.assertFalse(invoke()["actualCfdRunVerified"])
            report = json.loads((output / "phase-change-execution.json").read_bytes())
            row = report["retainedNativeLogRecords"][-1]
            self.assertEqual(row["bytes"], 0)
            self.assertEqual(row["sha256"], sha256(b"").hexdigest())
            self.assertFalse(report["nativeSolverCompleted"])


if __name__ == "__main__":
    unittest.main()
