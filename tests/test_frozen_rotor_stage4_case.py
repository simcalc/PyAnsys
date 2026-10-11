"""Synthetic native byte fixtures only; no test establishes pump CFD/restart."""
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import frozen_rotor_stage4_case as stage


def put(root, name, content):
    raw = content if isinstance(content, bytes) else content.encode("ascii")
    path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    return stage.descriptor(name, raw)


def counted(name, kind, count, body):
    return stage.phase.base.foam_header(name, kind) + str(count) + "\n(\n" + body + "\n)\n"


def mesh_fixture(case):
    inputs = {
        "points": counted("points", "vectorField", 5, "(0 0 0) (1 0 0) (0 1 0) (0 0 1) (0 0 -1)"),
        "owner": counted("owner", "labelList", 7, "0 0 0 0 1 1 1"),
        "neighbour": counted("neighbour", "labelList", 1, "1"),
        "faces": counted("faces", "faceList", 7, "3(0 1 2) 3(0 3 1) 3(0 2 3) 3(1 3 2) 3(0 1 4) 3(0 4 2) 3(1 2 4)"),
        "boundary": counted("boundary", "polyBoundaryMesh", 1, "wall {type wall; nFaces 6; startFace 1;}"),
        "cellZones": counted("cellZones", "regIOobject", 1, "rotating-fluid {type cellZone; cellLabels List<label> 1(0);}")}
    return sorted([put(case, "constant/polyMesh/"+name, text) for name,text in inputs.items()], key=lambda row:row["path"])


def state(name, value=None, *, nonuniform=False, count=2, dimensions=None, kind=None, patch_type="calculated"):
    actual_kind, dimension, _ = stage.FIELD_ROLES[name]
    kind = kind or actual_kind; vector = kind.endswith("VectorField")
    value = ("(0 0 0)" if vector else "1") if value is None else value
    declaration = "nonuniform List<"+("vector" if vector else "scalar")+"> "+str(count)+"\n(\n"+value+"\n)" if nonuniform else "uniform "+value
    return (stage.phase.base.foam_header(name,kind) + "dimensions ["+(dimensions or dimension)+"];\ninternalField "+declaration
            + ";\nboundaryField {wall {type "+patch_type+"; value uniform "+("(0 0 0)" if vector else "0")+";}}\n").encode("ascii")


def uniform_time(name, dt, index, *, begin=0):
    return (stage.phase.base.foam_header("time", "dictionary") + "beginTime "+str(begin)+";\nvalue "+name+
            ";\nname \""+name+"\";\nindex "+str(index)+";\ndeltaT "+format(dt,".17g")+
            ";\ndeltaT0 "+format(dt,".17g")+";\n").encode("ascii")


def campaign_fixture(root):
    """Mock ONLY upstream native qualification/runtime, never emit CFD evidence."""
    source_root = root / "baseline"; source_case = source_root / "impeller-case"
    mesh = mesh_fixture(source_case)
    source_study = stage.phase.protocol.prepare_study()
    source_rows = mesh + [put(source_case,"system/source-fixture", "SYNTHETIC-TEST-NOT-NATIVE")]
    geometry = put(source_root,"geometry-identity.json",stage.encoded({"fixtureKind":"SYNTHETIC-TEST-NOT-CFD"}))
    evidence = [put(source_root,"logs/fixture.log", "SYNTHETIC-TEST-NOT-CFD\n")]
    observation = {"pressureLevelAbsolutePa":101325.,"controls":{"geometrySha256":sha256(stage.encoded([geometry])).hexdigest()},
                   "nativeEvidence":{"sha256":"a"*64}}
    packet = {"schemaVersion":stage.phase.NATIVE_REDUCTION_SCHEMA,"nativeEvidenceRecords":evidence}
    runtime = {"schemaVersion":"untirta.frozen-rotor-phase-runtime-identity.v1","fixtureKind":"SYNTHETIC-TEST-NOT-NATIVE","mainApplicationWrites":False}
    with patch.object(stage.phase,"verify_baseline",return_value=(source_case,source_study,observation,source_rows,"b"*64)), \
         patch.object(stage.phase_reduction,"phase_binary_identity"):
        report = stage.prepare_campaign(source_root,packet,root/"campaign",pressure_absolute_pa=80000.,runtime_identity=runtime)
    return report, source_root, source_case, source_rows


def checkpoint_fixture(root, report):
    variant = report["variants"][-1]
    name = format(report["checkpointPlan"]["physicalTimeS"],".17g")
    folder = root/"campaign"/variant["casePath"]/"case"/name
    for field_name in stage.REQUIRED_STATE:
        put(folder,field_name,state(field_name))
    put(folder,"polyMesh/points",counted("points","vectorField",5,"(0 0 0) (0 1 0) (-1 0 0) (0 0 1) (0 0 -1)"))
    put(folder,"uniform/time",uniform_time(name,variant["timeStepS"],variant["checkpointStepIndex"]))
    return variant,name,folder


class FixedClockTests(unittest.TestCase):
    def setUp(self):
        self.study=stage.phase.protocol.prepare_study()

    def test_three_actual_authored_clocks_share_end_window_and_checkpoint(self):
        plan=stage.temporal_plan(self.study)
        variants=plan["variants"]
        self.assertEqual([v["totalStepCount"] for v in variants],[1980,3960,7920])
        self.assertEqual([v["checkpointStepIndex"] for v in variants],[990,1980,3960])
        self.assertEqual(variants[0]["timeStepS"]/variants[1]["timeStepS"],2)
        self.assertEqual(variants[1]["timeStepS"]/variants[2]["timeStepS"],2)
        for variant in variants:
            self.assertEqual(variant["commonWindowS"],plan["commonWindowS"])
            self.assertAlmostEqual(variant["writeIntervalSteps"]*variant["timeStepS"],plan["checkpointTimeS"])
        self.assertFalse(plan["adaptiveTimeStepping"])
        self.assertEqual(plan["revolutions"],5.5)

    def test_clock_rejects_misalignment_nonfinite_oversized_steps_and_integer_motion_writes(self):
        for value,dt in ((.31,.1),(-1,.1),(1,0),(float("nan"),.1),(100001,1)):
            with self.subTest(value=value),self.assertRaises(ValueError):stage.aligned_steps(value,dt)
        for turns in (4.5,6,10.5):
            with self.subTest(turns=turns),self.assertRaises(ValueError):stage.temporal_plan(self.study,revolutions=turns)
        with self.assertRaises(ValueError):stage.temporal_plan(self.study,coarse_dt_s=.1)

    def test_recipe_is_fixed_dt_and_only_control_dict_differs_from_physical_recipe(self):
        variant=stage.temporal_plan(self.study)["variants"][0]
        authored=stage.fixed_recipe(self.study,80000.,variant["timeStepS"],variant["endTimeS"],write_interval_steps=990)
        source=stage.phase.native_recipe(self.study,80000.,"transient-sliding",variant["timeStepS"],variant["endTimeS"])
        self.assertEqual(set(authored),set(source))
        self.assertEqual([name for name in authored if authored[name]!=source[name]],["system/controlDict"])
        controls=authored["system/controlDict"]
        for value in ("adjustTimeStep no;","writeControl timeStep;","writeInterval 990;","purgeWrite 2;","timePrecision 17;","writePrecision 17;"):
            self.assertIn(value,controls)
        self.assertIn("correctPhi yes;",authored["system/fvSolution"])
        self.assertIn("rAU",stage.REQUIRED_STATE)
        for interval in (True,0,991):
            with self.subTest(interval=interval),self.assertRaises(ValueError):stage.fixed_recipe(self.study,80000.,variant["timeStepS"],variant["endTimeS"],write_interval_steps=interval)

    def test_signatures_exclude_only_declared_scalar_variations_actual_recipes_differ(self):
        rows=[stage.descriptor("constant/polyMesh/points",b"points")]
        geo=stage.descriptor("geometry-identity.json",b"geometry")
        runtime=stage.descriptor("stage4-runtime-identity.json",b"native runtime")
        first=stage.control_groups(self.study,80000.,rows,geo,runtime)
        self.assertEqual(first,stage.control_groups(self.study,50000.,rows,geo,runtime))
        for key,metadata in first.items():self.assertEqual(metadata,sorted(metadata,key=lambda row:row["path"]))
        p=stage.temporal_plan(self.study)
        coarse,fine=p["variants"][0],p["variants"][2]
        a=stage.fixed_recipe(self.study,80000.,coarse["timeStepS"],coarse["endTimeS"],write_interval_steps=990)
        b=stage.fixed_recipe(self.study,50000.,fine["timeStepS"],fine["endTimeS"],initial_s=p["checkpointTimeS"],write_interval_steps=3960)
        self.assertNotEqual(a["system/controlDict"],b["system/controlDict"])
        self.assertNotEqual(a["0/p_rgh"],b["0/p_rgh"])


class NativeStateTests(unittest.TestCase):
    counts={"cells":2,"faces":7,"internalFaces":1,"points":5}

    def test_all_required_state_roles_and_actual_counts(self):
        for name in stage.REQUIRED_STATE:
            with self.subTest(name=name):
                result=stage.state_field(state(name),name,self.counts)
                self.assertTrue(result["allInternalValuesChecked"])
        self.assertEqual(stage.state_field(state("Uf","(1 2 3)",nonuniform=True,count=1),"Uf",self.counts)["internalValueCount"],3)
        self.assertEqual(stage.state_field(state("rAU","1 2",nonuniform=True),"rAU",self.counts)["dimensions"],"-1 3 1 0 0 0 0")

    def test_shape_dimensions_unknown_classes_patch_roles_and_nonfinite_reject(self):
        cases=((state("phi","1 2",nonuniform=True,count=2),"phi"),
               (state("Uf","(1 2)",nonuniform=True,count=1),"Uf"),
               (state("rAU",dimensions="0 0 1 0 0 0 0"),"rAU"),
               (state("p",kind="surfaceScalarField"),"p"),
               (state("rAU","-1"),"rAU"),(state("phi","1e309"),"phi"),
               (state("Uf",patch_type="codedFixedValue"),"Uf"),
               (state("p",patch_type="arbitraryRuntimePlugin"),"p"))
        for raw,name in cases:
            with self.subTest(name=name),self.assertRaises(ValueError):stage.state_field(raw,name,self.counts)
        with self.assertRaises(ValueError):stage.state_field(state("p"),"unknownState",self.counts)

    def test_native_field_rejects_inline_directives_duplicate_header_and_library_loading(self):
        source=state("phi")
        for append in (b' #include "/etc/private"',b' libs ("evil.so");',b' codeExecute {};',b'FoamFile {format ascii;class surfaceScalarField;object phi;}'):
            with self.subTest(append=append),self.assertRaises(ValueError):stage.state_field(source+append,"phi",self.counts)
        self.assertTrue(stage.state_field(source+b"// harmless #include comment\n","phi",self.counts)["allInternalValuesChecked"])

    def test_native_field_rejects_duplicate_operands_and_quoted_unknown_patch_types(self):
        source=state("phi")
        for append in (b"dimensions [0 3 -1 0 0 0 0];",b"internalField uniform 1;",b"boundaryField {};",b" $other; "):
            with self.subTest(append=append),self.assertRaises(ValueError):stage.state_field(source+append,"phi",self.counts)
        with self.assertRaises(ValueError):stage.state_field(source.replace(b"type calculated;",b'type "evil";'),"phi",self.counts)

    def test_checkpoint_accepts_exact_authored_sliding_alpha_and_velocity_patch_roles(self):
        study=stage.phase.protocol.prepare_study();variant=stage.temporal_plan(study)["variants"][0]
        recipe=stage.fixed_recipe(study,80000.,variant["timeStepS"],variant["endTimeS"],write_interval_steps=990)
        self.assertIn("type inletOutlet;",recipe["0/alpha.water"])
        self.assertIn("type movingWallSlipVelocity;",recipe["0/U"])
        for name,kind in (("alpha.water","inletOutlet"),("U","movingWallSlipVelocity")):
            with self.subTest(name=name):self.assertTrue(stage.state_field(state(name,patch_type=kind),name,self.counts)["allInternalValuesChecked"])

    def test_native_uniform_time_preserves_exact_original_fixed_clock(self):
        name="0.275";dt=.0001;index=2750
        raw=uniform_time(name,dt,index)
        actual=stage.native_time_state(raw,name,dt)
        self.assertEqual(actual["timeIndex"],2750)
        for modified in (raw.replace(b"index 2750;",b"index 2749;"),raw.replace(b"deltaT0 0.0001;",b"deltaT0 0.0002;"),
                         raw.replace(b'name "0.275";',b'name "0.27";'),raw+b"unknown 1;",raw.replace(b"beginTime 0;",b"beginTime 0.1;")):
            with self.subTest(modified=modified[-40:]),self.assertRaises(ValueError):stage.native_time_state(modified,name,dt)

    def test_ncc_native_label_mapping_roles_counts_ranges_and_patch_names(self):
        with tempfile.TemporaryDirectory() as temp:
            case=Path(temp);mesh_fixture(case)
            put(case,"constant/polyMesh/boundary",counted("boundary","polyBoundaryMesh",3,
                "wall {type wall; nFaces 3; startFace 1;} other {type wall;nFaces 3;startFace 4;} "
                "ncc {type nonConformalCyclic; originalPatch wall; nFaces 0; startFace 7;}"))
            prefix=stage.phase.base.foam_header("polyFaces","surfaceLabelField")+"dimensions [0 0 0 0 0 0 0];\ninternalField uniform -1;\nboundaryField {"
            raw=(prefix+"wall {type polyFaces;} other {type polyFaces;} ncc {type nonConformalPolyFaces;size 3;value nonuniform List<label> 3(1 2 2);} }\n").encode("ascii")
            checked=stage.nonconformal_addressing(raw,case,self.counts)
            self.assertEqual(checked["nativeIntersectionFaceCount"],3)
            self.assertFalse(checked["geometryReconstructed"])
            for bad in (raw.replace(b"3(1 2 2)",b"3(0 2 2)"),raw.replace(b"size 3;",b"size 4;"),
                        raw.replace(b"wall {",b"unknown {"),raw.replace(b"uniform -1;",b"uniform 0;"),
                        raw.replace(b"3(1 2 2)",b"3(4 2 2)"),raw.replace(b"type nonConformalPolyFaces;",b"type coded;")):
                with self.subTest(bad=bad[-80:]),self.assertRaises(ValueError):stage.nonconformal_addressing(bad,case,self.counts)
            implicit=raw.replace(b"type nonConformalPolyFaces;size 3;value nonuniform List<label> 3(1 2 2);",b"type polyFaces;")
            with self.assertRaises(ValueError):stage.nonconformal_addressing(implicit,case,self.counts)


class ActualByteCloneTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,{"RUNNER_TEMP":str(self.root)});self.env.start();self.addCleanup(self.env.stop)
        self.report,self.source_root,self.source_case,self.source_rows=campaign_fixture(self.root)

    def checkpoint(self):
        variant,name,folder=checkpoint_fixture(self.root,self.report)
        packet=stage.build_checkpoint(self.root/"campaign",variant,name)
        return variant,name,folder,packet

    def test_preparation_has_three_wrappers_exact_reference_mesh_and_no_execution_claims(self):
        self.assertEqual(self.report["status"],"FIXED_CLOCK_CASES_PREPARED_NATIVE_EXECUTION_REQUIRED")
        self.assertTrue(self.report["sourceBaselineImmutable"])
        for flag in ("actualCfdRunVerified","physicalTransientExecuted","restartVerified","timeStepConvergenceVerified","phaseFamilyReferenceQualified","mainApplicationWrites"):
            self.assertIs(self.report[flag],False)
        for variant in self.report["variants"]:
            _,prepared,authored,mesh=stage.branch_inputs(self.root/"campaign",variant)
            self.assertEqual(len(mesh),6)
            self.assertEqual(stage.verify_rows(self.source_case,mesh),mesh)
            self.assertTrue(prepared["fixedClock"])
            self.assertEqual(authored,sorted(authored,key=lambda row:row["path"]))

    def test_checkpoint_binds_actual_complete_state_without_restart_claim(self):
        variant,name,folder,packet=self.checkpoint()
        self.assertEqual(len(packet["files"]),len(stage.REQUIRED_STATE)+2)
        self.assertEqual(packet["meshCounts"],{"cells":2,"faces":7,"internalFaces":1,"points":5})
        self.assertEqual(packet["checkpointSha256"],sha256(stage.encoded(packet["files"])).hexdigest())
        self.assertTrue(packet["allRequiredNativeRolesVerified"])
        self.assertFalse(packet["restartVerified"])
        self.assertFalse(packet["actualCfdRunVerified"])
        self.assertEqual(stage.verify_rows(self.root/"campaign"/variant["casePath"],packet["files"]),packet["files"])

    def test_each_restart_specific_role_is_mandatory(self):
        variant,name,folder=checkpoint_fixture(self.root,self.report)
        for role in ("phi","Uf","rAU","alphaPhi.water","meshPhi","polyMesh/points","uniform/time"):
            path=folder/role;raw=path.read_bytes();path.unlink()
            with self.subTest(role=role),self.assertRaises(ValueError):stage.build_checkpoint(self.root/"campaign",variant,name)
            put(folder,role,raw)

    def test_unknown_fields_directories_and_executable_checkpoint_dictionary_reject(self):
        variant,name,folder=checkpoint_fixture(self.root,self.report)
        for role,raw in (("unregisteredField",b"1"),("nested/U",state("U")),("uniform/dynamicCode",b"1")):
            put(folder,role,raw)
            with self.subTest(role=role),self.assertRaises(ValueError):stage.build_checkpoint(self.root/"campaign",variant,name)
            (folder/role).unlink()
            if "/" in role and (folder/role).parent.name=="nested":(folder/role).parent.rmdir()
        put(folder,"phi",state("phi",patch_type="codedFixedValue"))
        with self.assertRaises(ValueError):stage.build_checkpoint(self.root/"campaign",variant,name)

    def test_temporal_variant_tampering_cannot_select_a_different_clock_or_time(self):
        variant,name,folder=checkpoint_fixture(self.root,self.report)
        changed={**variant,"endTimeS":variant["endTimeS"]+variant["timeStepS"]}
        with self.assertRaises(ValueError):stage.build_checkpoint(self.root/"campaign",changed,name)
        for clock in ("../0",str(variant["endTimeS"]),"0","0.15"):
            with self.subTest(clock=clock),self.assertRaises(ValueError):stage.build_checkpoint(self.root/"campaign",variant,clock)

    def test_resume_preserves_full_checkpoint_and_constant_points_changes_only_clock_control(self):
        variant,name,folder,packet=self.checkpoint()
        report=stage.prepare_resume(self.root/"campaign",packet,self.root/"resumed-case")
        original=self.root/"campaign"/variant["casePath"]
        resumed=self.root/"resumed-case"
        self.assertNotIn("authoredInputFiles",report["variant"])
        for row in packet["files"]:self.assertEqual((resumed/row["path"]).read_bytes(),(original/row["path"]).read_bytes())
        for row in packet["nativeCaseFiles"]:
            old=(original/"case"/row["path"]).read_bytes();new=(resumed/"case"/row["path"]).read_bytes()
            if row["path"]=="system/controlDict":self.assertNotEqual(old,new)
            else:self.assertEqual(old,new)
        self.assertNotEqual((resumed/"case/constant/polyMesh/points").read_bytes(),(resumed/"case"/name/"polyMesh/points").read_bytes())
        _,prepared,_,_=stage.branch_inputs(self.root,report["variant"])
        self.assertEqual(prepared["controls"],self.report["controls"])
        self.assertEqual(prepared["variant"]["initialTimeS"],packet["physicalTimeS"])
        self.assertTrue(report["sourceCheckpointImmutable"])
        self.assertFalse(report["restartVerified"])
        self.assertFalse(report["actualCfdRunVerified"])

    def test_checkpoint_mutation_refuses_resume_before_output_creation(self):
        variant,name,folder,packet=self.checkpoint()
        put(folder,"Uf",state("Uf","(1 0 0)"))
        with self.assertRaises(ValueError):stage.prepare_resume(self.root/"campaign",packet,self.root/"resumed-case")
        self.assertFalse((self.root/"resumed-case").exists())

    def test_checkpoint_size_and_sha_metadata_are_exact_no_bool_or_arbitrary_paths(self):
        _,_,_,packet=self.checkpoint()
        branch=self.root/"campaign/fine-case"
        for transform in (lambda r:{**r,"bytes":True},lambda r:{**r,"sha256":"z"*64},lambda r:{**r,"path":"../secret"},lambda r:{**r,"path":[]}):
            with self.subTest(transform=transform),self.assertRaises(ValueError):stage.verify_rows(branch,[transform(packet["files"][0])])

    def test_output_must_be_fresh_under_runner_temp_without_links(self):
        with self.assertRaises(ValueError):stage.fresh_output(self.root/"campaign","stage4-case-preparation.json")
        with self.assertRaises(ValueError):stage.fresh_output(self.root.parent/"not-owned-stage4","stage4-case-preparation.json")

    def test_resumed_budget_failure_removes_only_created_files(self):
        _,_,_,packet=self.checkpoint()
        source_before={row["path"]:(self.root/"campaign/fine-case"/row["path"]).read_bytes() for row in packet["files"]}
        with patch.object(stage,"MAX_BRANCH_BYTES",100),self.assertRaises(ValueError):
            stage.prepare_resume(self.root/"campaign",packet,self.root/"resumed-case")
        self.assertFalse((self.root/"resumed-case").exists())
        for name,raw in source_before.items():self.assertEqual((self.root/"campaign/fine-case"/name).read_bytes(),raw)

    def test_partial_attempt_file_cleanup_does_not_delete_unrelated_source(self):
        target=self.root/"attempt";written=[]
        def fail_write(path,raw):
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw[:2]);raise OSError("synthetic disk failure")
        with patch.object(stage.phase.base,"write_exclusive",side_effect=fail_write),self.assertRaises(OSError):stage.write(target,"case/0/U",b"native",written)
        stage.cleanup(target,written)
        self.assertFalse(target.exists())
        self.assertTrue((self.source_root/"geometry-identity.json").exists())

    def test_campaign_rechecks_upstream_authored_files_and_failed_output_is_removed(self):
        source_study=stage.phase.protocol.prepare_study()
        geometry=stage.read(self.source_root,"geometry-identity.json")[1]
        evidence=[stage.read(self.source_root,"logs/fixture.log")[1]]
        observation={"pressureLevelAbsolutePa":101325.,"controls":{"geometrySha256":sha256(stage.encoded([geometry])).hexdigest()},"nativeEvidence":{"sha256":"a"*64}}
        packet={"schemaVersion":stage.phase.NATIVE_REDUCTION_SCHEMA,"nativeEvidenceRecords":evidence}
        original_write=stage.write
        changed=False
        def mutate_after_copy(root,name,raw,written):
            nonlocal changed
            original_write(root,name,raw,written)
            if not changed:
                put(self.source_case,"system/source-fixture","MUTATED-SOURCE-TEST")
                changed=True
        with patch.object(stage.phase,"verify_baseline",return_value=(self.source_case,source_study,observation,self.source_rows,"b"*64)), \
             patch.object(stage.phase_reduction,"phase_binary_identity"),patch.object(stage,"write",side_effect=mutate_after_copy),self.assertRaises(ValueError):
            stage.prepare_campaign(self.source_root,packet,self.root/"second-campaign",pressure_absolute_pa=80000.,runtime_identity={"fixtureKind":"NOT-NATIVE"})
        self.assertFalse((self.root/"second-campaign").exists())
        self.assertTrue((self.root/"campaign").exists())


if __name__=="__main__":unittest.main()
