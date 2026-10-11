"""Synthetic parser/unit fixtures only; no test represents a native CFD run."""
from copy import deepcopy
from hashlib import sha256
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import reduce_stage4_campaign as reduction
import frozen_rotor_stage4_case as builder
import run_frozen_rotor_phase as phase_runner
import run_stage4_campaign as runner


def write(root,name,raw):
    raw=raw if isinstance(raw,bytes) else raw.encode("ascii")
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    return {"path":name,"bytes":len(raw),"sha256":sha256(raw).hexdigest()}


def js(root,name,value):return write(root,name,reduction.encoded(value))


def prepared(root,index=0,pressure=50000):
    study=reduction.protocol.prepare_study()
    variant=builder.temporal_plan(study)["variants"][index]
    authored=[]
    for name,text in sorted(builder.fixed_recipe(study,pressure,variant["timeStepS"],variant["endTimeS"],write_interval_steps=variant["writeIntervalSteps"]).items()):
        row=write(root,"case/"+name,text);authored.append({**row,"path":name})
    meshes=[]
    for name in sorted(reduction.case.MESH_FILES):
        text=reduction.case.base.foam_header(name,"vectorField" if name=="points" else "dictionary")
        text+="2\n((0 0 0)(1 0 0))\n" if name=="points" else "0\n()\n"
        row=write(root,"case/constant/polyMesh/"+name,text);meshes.append({**row,"path":"constant/polyMesh/"+name})
    geometry=js(root,"geometry-identity.json",{"schemaVersion":"untirta.frozen-rotor-fixed-geometry-identity.v1",
        "exampleDataCommit":reduction.case.base.inventory.PIN,"fixtureKind":"SYNTHETIC-UNIT-TEST-NOT-CFD","mainApplicationWrites":False})
    runtime=js(root,"stage4-runtime-identity.json",{"fixtureKind":"SYNTHETIC-UNIT-TEST-NOT-NATIVE-RUNTIME"})
    controls={key:sha256(reduction.encoded(rows)).hexdigest() for key,rows in builder.control_groups(study,pressure,meshes,geometry,runtime).items()}
    study=reduction.protocol.prepare_study(controls=controls)
    prep={"schemaVersion":builder.BRANCH_SCHEMA,"variant":{**variant,"controls":controls},"study":study,"controls":controls,
        "declaredStudyInputs":study["input"],"physicalBasis":study["physicalBasis"],"pressureLevelAbsolutePa":pressure,
        "authoredInputFiles":authored,"referenceMeshFiles":meshes,"authoredCavitationModelSha256":controls["cavitationModelSha256"],
        "sourceGeometryIdentityFile":geometry,"sourceGeometryControlSha256":sha256(reduction.encoded([geometry])).hexdigest(),
        "sourceBaselineEvidenceSha256":"a"*64,"sourceCaseManifestSha256":"b"*64,"runtimeIdentityFile":runtime,
        "fixedClock":True,"adaptiveTimeStepping":False,"simulationExecuted":False,"mainApplicationWrites":False,
        "fixtureKind":"SYNTHETIC-UNIT-TEST-NOT-CFD"}
    js(root,"stage4-branch-preparation.json",prep)
    with patch.object(reduction.phase,"phase_binary_identity"):
        packet=reduction.build_manifest(root)
    js(root,"stage4-input-manifest.json",packet["manifest"]);js(root,"stage4-classroom-study.json",packet["study"])
    return packet,prep


def log_fixture(variant,*,dt_lines=False,residual=1e-6):
    dt=variant["timeStepS"];start=variant["initialTimeS"];end=variant["endTimeS"]
    pieces=["Selecting cavitation model SchnerrSauer\nSelecting solidBodyMotionFunction rotatingMotion\n"]
    coverage="Source min/average/max coverage = 1/1/1\nTarget min/average/max coverage = 1/1/1\n"
    for i in range(1,round((end-start)/dt)+1):
        t=start+i*dt
        pieces.append("Courant Number mean: 0.01 max: 0.1\nInterface Courant Number mean: 0.001 max: 0.01\n")
        if dt_lines:pieces.append(f"deltaT = {dt:.17g}\n")
        pieces.append(f"Time = {t:.17g}s\n"+coverage)
        for outer in range(1,4):
            pieces.append(f"PIMPLE: iteration {outer}\n")
            for field in ("p_rgh","Ux","Uy","Uz")+(("k","omega") if outer==3 else ()):
                pieces.append(f"solver: Solving for {field}, Initial residual = {residual}, Final residual = 1e-10, No Iterations 2\n")
            pieces.append("time step continuity errors : sum local = 1e-6, global = -1e-7, cumulative = -1e-5\n")
    return "".join(pieces)+"End\n"


def executed(root,index=0,head=10,missing=None,residual=1e-6):
    packet,prep=prepared(root,index);study=packet["study"];manifest=packet["manifest"];variant=prep["variant"]
    rho,g=study["input"]["densityKgM3"],study["input"]["gravityMS2"];pressure=manifest["pressureLevelAbsolutePa"]
    dt,end=variant["timeStepS"],variant["endTimeS"];times=[i*dt for i in range(1,variant["totalStepCount"]+1)]
    data={"weightedInlet":(pressure-2*rho,pressure,4,1),"weightedOutlet":(pressure+head*rho*g-4.5*rho,pressure+head*rho*g,9,1),
        "massFlowInlet":(-90,),"massFlowOutlet":(90,),"massFlowMagnitudeInlet":(90,),"massFlowMagnitudeOutlet":(90,),
        "waterMinimumInlet":(1,),"waterMinimumOutlet":(1,),"globalPressureMinimum":(30000,),"globalWaterMinimum":(1,),"globalWaterMaximum":(1,),
        "massFlowRotorInterface":(90,),"massFlowStatorInterface":(-90,),"massFlowMagnitudeRotorInterface":(90,),"massFlowMagnitudeStatorInterface":(90,)}
    for name,values in data.items():
        if name==missing:continue
        patch_name="inlet" if name.endswith("Inlet") else "mass-flow-inlet-11" if name.endswith("Outlet") else reduction.native.COUPLED_PATCHES[0] if name.endswith("RotorInterface") else reduction.native.COUPLED_PATCHES[1] if name.endswith("StatorInterface") else None
        columns=("average(p)","average(totalPressurePa)","average(U2)","average(alpha.water)") if name.startswith("weighted") else ("sumMag(rhoPhi)",) if name.startswith("massFlowMagnitude") else ("sum(rhoPhi)",) if name.startswith("massFlow") else ("min(p)",) if name=="globalPressureMinimum" else ("max(alpha.water)",) if name=="globalWaterMaximum" else ("min(alpha.water)",)
        header=(f"# Selection : patch({patch_name})\n# Faces : 3\n# Area : 0.5\n" if patch_name else "")+"# Time "+" ".join(columns)+"\n"
        header+="".join(f"{t:.17g} "+" ".join(f"{v:.17g}" for v in values)+"\n" for t in times)
        write(root,"case/postProcessing/"+name+"/0/"+("surfaceFieldValue.dat" if patch_name else "volFieldValue.dat"),header)
    coverage="Source min/average/max coverage = 1/1/1\nTarget min/average/max coverage = 1/1/1\n"
    check="cells: 2\nMin volume = 1e-6. Max volume = 2e-6. Total volume = 3e-6.\nMesh OK.\n"+coverage+"End\n"
    solver=log_fixture(variant,residual=residual);stages=[]
    for i,(name,text) in enumerate((("checkMesh",check),("foamRun",solver))):
        stages.append({**write(root,f"logs/{i:02d}-{name}.log",text),"program":name,"exitCode":0,"elapsedSeconds":1})
    fields=[];endname=f"{end:.17g}"
    for name in phase_runner.END_FIELDS:
        value="(1 2 3)" if name=="U" else "50000" if name in {"p","p_rgh"} else "1"
        raw=native_field(name,value)
        fields.append({**write(root,"case/"+endname+"/"+name,raw),"fieldCheck":phase_runner.finite_field(raw,name,2)})
    write(root,"case/"+endname+"/polyMesh/points",reduction.case.base.foam_header("points","vectorField")+"2\n((0 0 0)(-1 0 0))\n")
    report={"schemaVersion":runner.BRANCH_SCHEMA,"exampleDataCommit":reduction.case.base.inventory.PIN,
        "fixtureKind":"SYNTHETIC-UNIT-TEST-NOT-CFD","status":"STAGE4_NATIVE_EXECUTED_ACCEPTANCE_REQUIRED","mode":"transient-sliding","id":variant["id"],
        **{k:True for k in ("actualCfdRunVerified","simulationExecuted","nativeCheckMeshVerified","nativeSolverCompleted","cavitationModelExecuted",
            "finiteNativeEndFieldsVerified","physicalTransientExecuted","inputArtifactsImmutable","fixedClockVerified")},"mainApplicationWrites":False,
        "preparationReportSha256":manifest["preparationReportSha256"],"runtimeIdentitySha256":manifest["runtimeIdentitySha256"],
        "inputManifestSha256":sha256((root/"stage4-input-manifest.json").read_bytes()).hexdigest(),"studySha256":sha256((root/"stage4-classroom-study.json").read_bytes()).hexdigest(),
        "temporalPlan":{key:variant[key] for key in ("initialTimeS","endTimeS","timeStepS")},"stages":stages,
        "nativeMeshQuality":reduction.native.parse_mesh_quality(check),"nativeEndTimeS":end,"finalNativeFieldRecords":fields,
        "nativeSamplingRecords":phase_runner.sampling_records(root),"fixedClockEvidence":runner.fixed_clock(solver,variant),
        "rotorMotionEvidence":phase_runner.rotor_motion(root,[(end,root/"case"/endname)],solver)}
    js(root,"stage4-branch-execution.json",report)
    return packet,prep,report


def native_field(name,value,*,count=None):
    kind="volVectorField" if name=="U" else "volScalarField"
    payload="uniform "+value if count is None else f"nonuniform List<{'vector' if name=='U' else 'scalar'}> {count}\n({value})"
    return f"FoamFile {{format ascii; class {kind}; object {name};}}\ndimensions [{phase_runner.FIELD_DIMENSIONS[name]}];\ninternalField {payload};\nboundaryField {{}}\n".encode("ascii")


class Stage4Readers(unittest.TestCase):
    def test_missing_actual_campaign_does_not_make_results(self):
        with tempfile.TemporaryDirectory() as t:
            result=reduction.reduce_campaign(Path(t));self.assertFalse(result["actualCfdRunVerified"]);self.assertFalse(result["npsh3Verified"])

    def test_full_uniform_scalar_expands_exact_count(self):
        result=reduction.compare_native_values(native_field("p","50000"),native_field("p","50000.4"),"p",7,.5)
        self.assertTrue(result["criterionSatisfied"]);self.assertEqual(result["sampleCount"],7)
        self.assertAlmostEqual(result["rmsError"],.4)

    def test_late_velocity_component_is_compared(self):
        left=native_field("U","(1 2 3) (4 5 6)",count=2);right=native_field("U","(1 2 3) (4 5 6.1)",count=2)
        result=reduction.compare_native_values(left,right,"U",2,.01)
        self.assertFalse(result["criterionSatisfied"]);self.assertEqual(result["sampleCount"],6)

    def test_wrong_dimension_rejected(self):
        raw=native_field("p","5").replace(b"[1 -1 -2 0 0 0 0]",b"[0 2 -2 0 0 0 0]")
        with self.assertRaisesRegex(ValueError,"UNITS"):list(reduction.scalar_vector_stream(raw,"p",2))

    def test_truncated_full_list_rejected(self):
        with self.assertRaisesRegex(ValueError,"COUNT"):list(reduction.scalar_vector_stream(native_field("p","1 2",count=3),"p",3))

    def test_malformed_vector_and_payload_rejected(self):
        for value in ("(1 2 3 4)","1 2 3","(1 2 3) ignored"):
            with self.subTest(value=value),self.assertRaises(ValueError):list(reduction.scalar_vector_stream(native_field("U",value),"U",2))

    def test_boundary_overflow_and_executable_rejected(self):
        for suffix in (b" boundaryField {x { value uniform 1e999; }}",b"\n#codeStream ignored\n"):
            with self.subTest(suffix=suffix),self.assertRaises(ValueError):list(reduction.scalar_vector_stream(native_field("p","1")+suffix,"p",2))

    def test_raw_fraction_not_clamped_for_comparison(self):
        result=reduction.compare_native_values(native_field("alpha.water","1"),native_field("alpha.water","1.0000000005"),"alpha.water",2,1e-10)
        self.assertFalse(result["criterionSatisfied"])
        with self.assertRaisesRegex(ValueError,"BOUNDS"):list(reduction.scalar_vector_stream(native_field("alpha.water","1.01"),"alpha.water",2))

    def test_all_moved_point_components_compared(self):
        header=reduction.case.base.foam_header("points","vectorField")
        result=reduction.compare_native_values((header+"2\n((0 0 0)(1 2 3))").encode(),(header+"2\n((0 0 0)(1 2 3.01))").encode(),"points",2,.001,points=True)
        self.assertFalse(result["criterionSatisfied"]);self.assertEqual(result["sampleCount"],6)

    def test_positive_explicit_tolerances_required(self):
        for tol in (0,-1,float("inf"),True):
            with self.subTest(tol=tol),self.assertRaises(ValueError):reduction.compare_native_values(native_field("p","1"),native_field("p","1"),"p",2,tol)

    def test_fixed_clock_without_printed_delta_t_keeps_actual_residuals(self):
        variant=builder.temporal_plan(reduction.protocol.prepare_study())["variants"][0]
        rows,proof=reduction.fixed_solver_history(log_fixture(variant),variant)
        self.assertEqual(len(rows),1980);self.assertEqual(proof["printedDeltaTRecordCount"],0)
        self.assertEqual(next(iter(rows.values()))["outer"][0]["p_rgh"],1e-6)
        self.assertNotIn("k",next(iter(rows.values()))["outer"][0])

    def test_skipped_fixed_clock_rejected(self):
        variant=builder.temporal_plan(reduction.protocol.prepare_study())["variants"][0]
        text=log_fixture(variant).replace(f"Time = {variant['timeStepS']:.17g}s\n","")
        with self.assertRaises(ValueError):reduction.fixed_solver_history(text,variant)

    def test_manifest_time_variation_exact_files_bound_same_family(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);a,_=prepared(root/"a",0);b,_=prepared(root/"b",1)
            self.assertEqual(a["manifest"]["controls"],b["manifest"]["controls"])
            self.assertNotEqual(a["manifest"]["files"],b["manifest"]["files"])
            self.assertNotEqual(a["manifest"]["temporalPlan"],b["manifest"]["temporalPlan"])

    def test_authored_clock_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);prepared(root)
            path=root/"case/system/controlDict";path.write_text(path.read_text().replace("adjustTimeStep no;","adjustTimeStep yes;"))
            with patch.object(reduction.phase,"phase_binary_identity"),self.assertRaises(ValueError):reduction.build_manifest(root)

    def test_actual_binary_identity_gate_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);prepared(root)
            with self.assertRaisesRegex(ValueError,"RUNTIME_IDENTITY"):reduction.build_manifest(root)

    def test_matched_three_period_observation_and_equation_steps(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);executed(root)
            with patch.object(reduction.phase,"phase_binary_identity"):packet=reduction.verify_branch(root)
            result=reduction.observation(packet)
            self.assertTrue(result["phaseObservationQualified"],result["diagnostics"])
            self.assertAlmostEqual(result["stages"]["head"]["result"]["value"],10)
            self.assertIn("equation",result["stages"]["npsha"])
            self.assertEqual(result["fixedClockEvidence"]["printedDeltaTRecordCount"],0)

    def test_missing_outlet_preserves_independent_npsha(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);executed(root,missing="weightedOutlet")
            with patch.object(reduction.phase,"phase_binary_identity"):packet=reduction.verify_branch(root)
            result=reduction.observation(packet)
            self.assertFalse(result["phaseObservationQualified"]);self.assertIn("npsha",result["stages"]);self.assertNotIn("head",result["stages"])

    def test_native_log_byte_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);executed(root)
            with (root/"logs/01-foamRun.log").open("ab") as output:output.write(b"changed\n")
            with patch.object(reduction.phase,"phase_binary_identity"),self.assertRaisesRegex(ValueError,"LOG_REQUIRED"):reduction.verify_branch(root)

    def test_false_final_residual_cannot_replace_large_initial(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);executed(root,residual=.01)
            with patch.object(reduction.phase,"phase_binary_identity"):packet=reduction.verify_branch(root)
            result=reduction.observation(packet)
            self.assertFalse(result["phaseObservationQualified"])
            self.assertTrue(any("RESIDUAL" in row.get("reasonCode","") for row in result["diagnostics"]))

    def test_fixed_sampling_numeric_roundoff_only_is_normalized(self):
        variant=builder.temporal_plan(reduction.protocol.prepare_study())["variants"][0]
        times={i*variant["timeStepS"]:[1] for i in range(1,1981)}
        actual=reduction.verified_sampling_clock(times,variant)
        self.assertEqual(max(actual),variant["endTimeS"])
        first=next(iter(times));value=times.pop(first);times[first+variant["timeStepS"]*.1]=value
        with self.assertRaisesRegex(ValueError,"MISMATCH"):reduction.verified_sampling_clock(dict(sorted(times.items())),variant)

    def test_same_mesh_optimisation_is_not_spatial_refinement(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);executed(root)
            with patch.object(reduction.phase,"phase_binary_identity"),self.assertRaisesRegex(ValueError,"THREE_ACTUAL"):
                reduction.mesh_family([root,root,root])

    def test_head_time_gci_can_pass_without_restart_or_npsh3_claim(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);branches=[]
            for index,head in enumerate((10.05,10.01,10.)):
                label=("coarse","medium","fine")[index];path=root/(label+"-case")
                executed(path,index=index,head=head)
                _,row=reduction.native.record(root,label+"-case/stage4-branch-execution.json")
                branches.append({"id":label,"casePath":label+"-case","report":row})
            prep_row=js(root,"stage4-case-preparation.json",{"schemaVersion":builder.SCHEMA,"exampleDataCommit":reduction.case.base.inventory.PIN,
                "mainApplicationWrites":False,"fixtureKind":"SYNTHETIC-UNIT-TEST-NOT-CFD"})
            js(root,"stage4-campaign-execution.json",{"schemaVersion":runner.SCHEMA,"exampleDataCommit":reduction.case.base.inventory.PIN,
                "mainApplicationWrites":False,"sourceBaselineImmutable":True,"preparationReportSha256":prep_row["sha256"],
                "branches":branches,"resumedBranch":None,"fixtureKind":"SYNTHETIC-UNIT-TEST-NOT-CFD"})
            with patch.object(reduction.phase,"phase_binary_identity"):result=reduction.reduce_campaign(root)
            self.assertTrue(result["headTimeStepConvergenceVerified"],result["diagnostics"])
            self.assertFalse(result["restartVerified"]);self.assertFalse(result["meshConvergenceVerified"])
            self.assertFalse(result["npsh3Verified"]);self.assertFalse(result["npsh3TimeStepConvergenceVerified"])
            self.assertAlmostEqual(result["headTimeStepRefinement"]["stages"]["observedOrder"]["result"]["value"],2)


class FullRestartContracts(unittest.TestCase):
    """Only endpoint comparator binding is mocked; every field byte is read."""
    tolerances={"pAbsolutePa":.5,"UAbsoluteMS":1e-5,"vaporFraction":1e-6,"pointsAssumedM":1e-8}

    def fixtures(self,root):
        period=60/1450;dt=period/1440;end=5.5*period;start=2.75*period
        mesh_rows=[{"path":"case/constant/polyMesh/"+name,"bytes":10,"sha256":"a"*64,"control":"meshSha256"} for name in sorted(reduction.case.MESH_FILES)]
        controls={key:"a"*64 for key in reduction.protocol.CONTROL_KEYS}
        study=reduction.protocol.prepare_study(controls=controls)
        manifest={"controls":controls,"studyInputs":study["input"],"physicalBasis":study["physicalBasis"],
            "physicalModel":"transient-cavitation-sliding-mesh","pressureLevelAbsolutePa":50000.,"files":mesh_rows,
            "temporalPlan":{"physicalTimeStepS":dt,"endTimeS":end,"initialTimeS":0.}}
        packets=[]
        for label in ("fine","resumed"):
            branch=root/(label+"-case");fields=[]
            for name,value in (("p","50000 50001"),("U","(1 2 3)(4 5 6)"),("alpha.water","1 0.9")):
                fields.append(write(branch,f"case/{end:.17g}/{name}",native_field(name,value,count=2)))
            pts=write(branch,f"case/{end:.17g}/polyMesh/points",reduction.case.base.foam_header("points","vectorField")+"2\n((0 0 0)(-1 0 0))")
            packets.append({"root":branch,"manifest":deepcopy(manifest),"study":study,"mesh":{"meshCellCount":2},
                "prepared":{"variant":{"id":label}},"execution":{"finalNativeFieldRecords":fields,"rotorMotionEvidence":{"nativeMovedPointRecords":[{**pts,"physicalTimeS":end}]}}})
        packets[1]["manifest"]["temporalPlan"]["initialTimeS"]=start
        checkpoint={"schemaVersion":builder.CHECKPOINT_SCHEMA,"exampleDataCommit":reduction.case.base.inventory.PIN,
            "physicalTimeS":start,"physicalTimeStepS":dt,"checkpointSha256":"b"*64,"variant":{"id":"fine"},
            "nativeTimeDirectory":f"{start:.17g}","meshCounts":{"cells":2,"points":2},"mainApplicationWrites":False,
            "fixtureKind":"SYNTHETIC-UNIT-TEST-NOT-CFD"}
        js(packets[1]["root"],"stage4-checkpoint.json",checkpoint)
        js(packets[1]["root"],"stage4-resume-preparation.json",{"checkpointSha256":"b"*64,"constantReferencePointsPreserved":True,
            "movedCheckpointPointsPreserved":True,"nativeUniformTimePreserved":True,"sourceCheckpointImmutable":True})
        return packets,checkpoint

    def test_all_native_endpoint_fields_and_points_match(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);packets,checkpoint=self.fixtures(root)
            with patch.object(builder,"build_checkpoint",return_value=checkpoint):result=reduction.full_restart(root,*packets,checkpoint,self.tolerances)
            self.assertEqual(result["status"],"CriterionSatisfied")
            self.assertEqual(result["fields"]["UAbsoluteMS"]["sampleCount"],6)
            self.assertEqual(result["fields"]["pointsAssumedM"]["sampleCount"],6)
            self.assertGreater(result["physicalRevolutions"],1)

    def test_last_cell_velocity_difference_fails_restart(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);packets,checkpoint=self.fixtures(root)
            item=packets[1]["execution"]["finalNativeFieldRecords"][1]
            item.update(write(packets[1]["root"],item["path"],native_field("U","(1 2 3)(4 5 6.1)",count=2)))
            with patch.object(builder,"build_checkpoint",return_value=checkpoint):result=reduction.full_restart(root,*packets,checkpoint,self.tolerances)
            self.assertEqual(result["status"],"RestartMismatch");self.assertFalse(result["fields"]["UAbsoluteMS"]["criterionSatisfied"])

    def test_mesh_order_or_runtime_mismatch_rejected(self):
        for control in ("meshSha256","runtimeSha256"):
            with tempfile.TemporaryDirectory() as t:
                root=Path(t);packets,checkpoint=self.fixtures(root)
                packets[1]["manifest"]["controls"][control]="c"*64
                with patch.object(builder,"build_checkpoint",return_value=checkpoint),self.assertRaisesRegex(ValueError,"FAMILY"):
                    reduction.full_restart(root,*packets,checkpoint,self.tolerances)

    def test_short_or_misaligned_restart_window_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);packets,checkpoint=self.fixtures(root)
            checkpoint["physicalTimeS"]=packets[0]["manifest"]["temporalPlan"]["endTimeS"]-.001
            packets[1]["manifest"]["temporalPlan"]["initialTimeS"]=checkpoint["physicalTimeS"]
            with self.assertRaisesRegex(ValueError,"MEANINGFUL"):reduction.full_restart(root,*packets,checkpoint,self.tolerances)

    def test_tolerances_never_supplied_as_hidden_defaults(self):
        with self.assertRaisesRegex(ValueError,"TOLERANCES"):reduction.full_restart(Path("."),{},{},{},None)


if __name__=="__main__":unittest.main()
