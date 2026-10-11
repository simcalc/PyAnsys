"""Synthetic topology/binding tests only; no fixture is native CFD evidence."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("synthetic_snappy_phase_case", TOOLS / "frozen_rotor_snappy_phase_case.py")
case = importlib.util.module_from_spec(spec); spec.loader.exec_module(case)
mesh_spec = importlib.util.spec_from_file_location("synthetic_phase_mesh", Path(__file__).parent / "test_frozen_rotor_snappy_baseline_case.py")
mesh_fixture = importlib.util.module_from_spec(mesh_spec); mesh_spec.loader.exec_module(mesh_fixture)


def write(root, path, raw):
    raw = raw if isinstance(raw, bytes) else raw.encode("ascii")
    target = root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
    return {"path": path, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def save_json(root, path, value):
    return write(root, path, case.encoded(value))


def fixture(root, *, background_box=True):
    """Replace only the native re-reducer: all new topology/SHA/math checks run.

    The miniature source has no installed native binaries or native solver run.
    Its explicit fixture marker and test-only reducer patch cannot be production
    evidence. Source flags exercise strict admission; they prove no execution.
    """
    source = root / "synthetic-baseline"; source.mkdir()
    components = []
    for name in ("impeller", "volute"):
        names = sorted(case.recipe.base.PATCHES[name])
        counts = (2,2,2,2,2,1,1) if name == "impeller" else (3,3,3,3)
        components.append({"component": name, "caseDirectory": name + "-case", "patchNames": names,
            "patchTypes": {p: "wall" if p in case.recipe.base.ROTATING | case.recipe.base.STATIONARY else "patch" for p in names},
            "patchFaces": dict(zip(names, counts)), "cellZone": "rotating-fluid" if name == "impeller" else "stationary-fluid",
            "cellCount": 6, "nodeCount": 8, "faceCount": 18, "internalFaceCount": 6})
    mesh_fixture.write_mesh(source / "impeller-case", components, background_box=background_box)
    all_rows = {}
    for component in components:
        for path, raw in case.baseline_case.case_files(component["component"], background_box=background_box).items():
            row = write(source, component["caseDirectory"] + "/" + path, raw); all_rows[row["path"]] = row
    for name in case.MESH_FILES:
        relative = "impeller-case/constant/polyMesh/" + name
        all_rows[relative] = case.read(source, relative)[1]
    preparation = {"components": components, "fixture": "synthetic-not-native-evidence"}
    all_rows["openfoam-preparation.json"] = save_json(source, "openfoam-preparation.json", preparation)
    identity = {"schemaVersion": "untirta.frozen-rotor-snappy-geometry-identity.v1", "sourceGeometryRoute": case.baseline.GEOMETRY_ROUTE,
        "exampleDataCommit": case.recipe.base.inventory.PIN, "nativeUnitsToMetres": 1,
        **{k: False for k in ("geometricApproximationQualified", "nativeCoordinateUnitsVerified", "nativeNodeIdentityEquivalent", "mainApplicationWrites")},
        "fixture": "synthetic-not-original-geometry"}
    all_rows["geometry-identity.json"] = save_json(source, "geometry-identity.json", identity)
    all_rows["runtime-identity.json"] = save_json(source, "runtime-identity.json", {"fixture": "no-native-binary"})
    all_rows["source-snappy/synthetic-marker.json"] = save_json(source, "source-snappy/synthetic-marker.json", {"fixture": True})
    group_paths = {"geometrySha256": ["geometry-identity.json"], "meshSha256": ["impeller-case/constant/polyMesh/"+n for n in case.MESH_FILES],
        "runtimeSha256": ["runtime-identity.json"],
        "boundarySha256": ["impeller-case/0/"+n for n in ("U","p","k","omega","nut")]+["impeller-case/constant/"+n for n in ("MRFProperties","physicalProperties","momentumTransport")],
        "discretizationSha256": ["impeller-case/system/"+n for n in ("fvSchemes","fvSolution","controlDict","createNonConformalCouplesDict")]}
    controls, files = {"cavitationModelSha256": None}, []
    grouped = {name: key for key, names in group_paths.items() for name in names}
    for key, names in group_paths.items():
        rows = [all_rows[name] for name in sorted(names)]; controls[key] = sha256(case.encoded(rows)).hexdigest()
    for path, row in sorted(all_rows.items()): files.append({**row, "control": grouped.get(path)})
    study = case.protocol.prepare_study(controls=controls, gravity_in_solver=False)
    manifest = {"schemaVersion": case.baseline.MANIFEST_SCHEMA, "sourceGeometryRoute": case.baseline.GEOMETRY_ROUTE,
        "controls": controls, "studyInputs": study["input"], "physicalBasis": study["physicalBasis"], "files": files}
    manifest_row = save_json(source, "native-input-manifest.json", manifest)
    study_row = save_json(source, "classroom-study.json", study)
    log = write(source, "logs/02-checkMesh.log", mesh_fixture.native_log(2))
    end = write(source, "impeller-case/500/U", "synthetic baseline result; not phase initial state\n")
    evidence = sorted([*all_rows.values(), manifest_row, study_row, log, end], key=lambda row: row["path"])
    evidence_sha = sha256(case.encoded(evidence)).hexdigest()
    rho, g = study["input"]["densityKgM3"], study["input"]["gravityMS2"]
    observation = {"studyInputs": study["input"], "controls": controls, "samplingDefinition": study["samplingDefinition"],
        "physicalBasis": study["physicalBasis"], "physicalModel": "steady-single-phase-MRF", "runRevision": "Current",
        "pressureLevelAbsolutePa": case.PRESSURE_PA,
        "nativeEvidence": {"kind": "native-solver-sampling", "upstreamVerified": True, "sha256": evidence_sha, "runId": "synthetic-not-native"},
        "controlledVariationProof": {"upstreamVerified": True, "nativeInputManifestSha256": manifest_row["sha256"],
            "samplingEvidenceSha256": evidence_sha, "structuralControls": controls, "signatureScope": study["signatureScope"],
            "physicalModel": "steady-single-phase-MRF", "pressureBoundaryDefinition": study["controlledPressureDefinition"],
            "pressureLevelAbsolutePa": case.PRESSURE_PA, "physicalTimeStepS": None},
        "pressureMapping": {"nativeField": "p", "nativeUnit": "m2/s2", "absolutePressureReferencePa": case.PRESSURE_PA,
            "absoluteReconstructionEquation": "p_static_absolute_Pa = rho_liquid*p_kinematic + explicit_absolute_reference_Pa"},
        "measurements": {"inletTotalPressureAbsolutePa": case.PRESSURE_PA, "outletTotalPressureAbsolutePa": case.PRESSURE_PA + rho*g*20,
            "inletElevationM": 0., "outletElevationM": 0., "inletStaticPressureAbsolutePa": case.PRESSURE_PA-100,
            "inletMeanSquaredAbsoluteSpeedM2S2": 1., "minimumStaticPressureAbsolutePa": case.PRESSURE_PA-1000},
        "quality": {"nativeMeshQualityChecked": True, "minimumCellVolumeM3": 1/6, "outwardInletMassFlowKgS": -90., "outwardOutletMassFlowKgS": 90.,
            "reverseFlowFraction": 0., "finalResidualHistory": {name: [1e-6]*5 for name in ("p","U","k","omega")},
            "residualDefinition": "initial-solver-residual-per-outer-iteration-final-window", "samplingWindowDefinition": "final-converged-window",
            "finalFlowRelativeSpan": 0., "headHistoryM": [20.]*5}}
    packet = {"schemaVersion": case.baseline.SCHEMA, "sourceGeometryRoute": case.baseline.GEOMETRY_ROUTE,
        "study": study, "manifest": manifest, "inputManifest": manifest, "nativeInputManifestSha256": manifest_row["sha256"],
        "nativeObservation": observation, "protocolReduction": case.protocol.reduce_run(study, observation), "nativeEvidenceRecords": evidence,
        "diagnostics": [], **{k: True for k in ("actualCfdRunVerified", "simulationExecuted", "numericalBaselineQualified",
            "preliminaryBaselineQualified", "noncavitatingBaselineVerified", "interfaceCoverageVerified")},
        **{k: False for k in ("qualifiedBaseline", "geometricApproximationQualified", "fullGeometryQualification", "nativeCoordinateUnitsVerified",
            "nativeNodeIdentityEquivalent", "wallResolutionVerified", "yPlusResolutionQualified", "meshConvergenceVerified", "timeStepConvergenceVerified",
            "physicalValidationVerified", "npsh3Qualified", "restartVerified", "physicalTransientExecuted", "mainApplicationWrites")}}
    save_json(source, "native-baseline-reduction.json", packet)
    return source


class PhaseReferenceTests(unittest.TestCase):
    def source(self, root, **kwargs):
        return fixture(root, **kwargs)

    def mocked_native_reducer(self):
        return patch.object(case.baseline, "reduce_native_baseline", side_effect=lambda root: case.json_file(root, "native-baseline-reduction.json"))

    def test_conditional_preparation_is_portable_and_binds_all_actual_fixture_bytes(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}), self.mocked_native_reducer() as reduction:
            root=Path(temp); source=self.source(root); output=root/"reference"
            report=case.prepare(source, output)
            self.assertEqual(report["mode"], case.MODE); self.assertEqual(report["inletAbsoluteTotalPressurePa"], 300000.)
            self.assertEqual(set(report), case.REPORT_KEYS)
            self.assertTrue(all(report[k] is False for k in case.FALSE_FLAGS))
            self.assertTrue(report["sourceNumericalBaselineQualified"])
            self.assertTrue(report["sourceActualCfdRunVerified"])
            self.assertFalse(report["temporalPlan"]["durationIsAcceptanceProof"])
            self.assertFalse(report["temporalPlan"]["residenceTimeAdequacyVerified"])
            self.assertFalse((output/"case/500").exists()); self.assertFalse((output/"case/0/p").exists())
            self.assertTrue((output/"source-baseline/impeller-case/500/U").is_file())
            source.rename(root/"source-moved-away")
            self.assertEqual(case.verify_output(output)["report"], report)
            self.assertGreaterEqual(reduction.call_count, 3)
            for row in report["sourceEvidenceFileRecords"]: self.assertEqual(case.read(output,row["path"])[1], row)

    def test_absolute_phase_pressure_is_pa_once_without_baseline_head_or_state(self):
        authored=case.native_recipe(case.protocol.prepare_study(), background_box=False)
        text=authored["0/p_rgh"].decode()
        self.assertIn("dimensions [1 -1 -2 0 0 0 0]",text); self.assertIn("internalField uniform 300000;",text)
        self.assertIn("p0 uniform 300000;",text); self.assertNotIn("600000",text)
        self.assertIn("pRef 0;",authored["system/controlDict"].decode())
        self.assertIn("model SchnerrSauer;",authored["constant/fvModels"].decode())
        self.assertIn("pSat 2339;",authored["constant/fvModels"].decode())
        self.assertIn("omega 1450 [rpm]",authored["constant/MRFProperties"].decode())
        self.assertNotIn("constant/dynamicMeshDict",authored)
        for field in ("U","p_rgh","alpha.water","k","omega","nut"):
            _, blocks=case.baseline_case.boundary_blocks(authored["0/"+field],field)
            self.assertEqual(set(blocks),set().union(*case.recipe.base.PATCHES.values()))
            self.assertNotIn("defaultFaces",blocks); self.assertNotIn("backgroundBox",blocks)
        self.assertIn("internalField uniform (0 0 0);",authored["0/U"].decode())
        self.assertIn("internalField uniform 1;",authored["0/alpha.water"].decode())

    def test_whole_output_relocation_preserves_all_bytes_without_original_source(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{"RUNNER_TEMP":temp}),self.mocked_native_reducer():
            root=Path(temp);source=self.source(root);output=root/"original-output";report=case.prepare(source,output)
            inventory={p.relative_to(output).as_posix():sha256(p.read_bytes()).hexdigest() for p in output.rglob("*") if p.is_file()}
            source.rename(root/"original-source-no-longer-present")
            moved=root/"relocated-output";output.rename(moved)
            self.assertEqual(case.verify_output(moved)["report"],report)
            self.assertEqual(inventory,{p.relative_to(moved).as_posix():sha256(p.read_bytes()).hexdigest() for p in moved.rglob("*") if p.is_file()})
            self.assertNotEqual(report["nativeCommandArguments"],case.commands(moved))
            self.assertEqual(case.commands(moved)[0][2],str(moved/"case"))

    def test_historical_tuple_is_pure_cross_platform_and_rejects_inconsistent_commands(self):
        historical="/home/runner/work/_temp/frozen-phase-reference"
        saved={"nativeCommandArguments":case.historical_commands(historical)}
        self.assertEqual(case.historical_command_root(saved),historical)
        self.assertEqual(saved["nativeCommandArguments"][0][2],historical+"/case")
        for kind in ("other-case","missing-flag","other-program","traversal","directive"):
            bad=deepcopy(saved)
            if kind=="other-case":bad["nativeCommandArguments"][1][2]="/home/runner/work/_temp/different/case"
            elif kind=="missing-flag":bad["nativeCommandArguments"][0].pop()
            elif kind=="other-program":bad["nativeCommandArguments"][1][0]="bash"
            elif kind=="traversal":bad["nativeCommandArguments"][0][2]="/home/runner/work/../case"
            else:bad["nativeCommandArguments"][0][2]="/home/runner/work/case;anything"
            with self.subTest(kind=kind),self.assertRaises(ValueError):case.historical_command_root(bad)
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}),self.mocked_native_reducer():
            root=Path(temp);source=self.source(root);output=root/"out";case.prepare(source,output)
            report=case.json_file(output,case.REPORT)
            report["nativeCommandArguments"][1][2]=str(root/"different/case")
            save_json(output,case.REPORT,report)
            with self.assertRaises(ValueError):case.verify_output(output)

    def test_optional_empty_background_is_measured_not_assumed(self):
        for present in (True,False):
            with self.subTest(present=present), tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{"RUNNER_TEMP":temp}), self.mocked_native_reducer():
                root=Path(temp);source=self.source(root,background_box=present);output=root/"phase"
                case.prepare(source,output)
                text=(output/"case/0/U").read_text()
                self.assertEqual('"backgroundBox"' in text,present)

    def test_failed_stale_or_broadly_promoted_source_is_rejected_before_write(self):
        for key in ("actualCfdRunVerified","numericalBaselineQualified","preliminaryBaselineQualified","noncavitatingBaselineVerified","interfaceCoverageVerified","qualifiedBaseline"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{"RUNNER_TEMP":temp}), self.mocked_native_reducer():
                root=Path(temp);source=self.source(root);packet=case.json_file(source,"native-baseline-reduction.json")
                packet[key]=key=="qualifiedBaseline";save_json(source,"native-baseline-reduction.json",packet)
                with self.assertRaises(ValueError): case.prepare(source,root/"out")
                self.assertFalse((root/"out").exists())
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);source=self.source(root);packet=case.json_file(source,"native-baseline-reduction.json")
            fresh=deepcopy(packet);fresh["numericalBaselineQualified"]=False
            with patch.object(case.baseline,"reduce_native_baseline",return_value=fresh),self.assertRaises(ValueError): case.prepare(source,root/"out")

    def test_old_schema_or_changed_si_and_double_pressure_are_not_relabelled(self):
        for change in ("old-schema","pressure-reference","rpm","density","flow","datum","g","absolute-pressure-offset"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{"RUNNER_TEMP":temp}),self.mocked_native_reducer():
                root=Path(temp);source=self.source(root);packet=case.json_file(source,"native-baseline-reduction.json")
                if change=="old-schema":packet["schemaVersion"]="untirta.frozen-rotor-native-baseline-reduction.v1"
                elif change=="pressure-reference":packet["nativeObservation"]["pressureLevelAbsolutePa"]=600000.
                elif change=="absolute-pressure-offset":packet["nativeObservation"]["pressureMapping"]["absolutePressureReferencePa"]=600000.
                elif change=="g":packet["study"]["physicalBasis"]["gravityInSolver"]=True
                else:packet["study"]["input"][{"rpm":"speedRpm","density":"densityKgM3","flow":"massFlowKgS","datum":"suctionDatumM"}[change]]+=1
                save_json(source,"native-baseline-reduction.json",packet)
                with self.assertRaises(ValueError):case.prepare(source,root/"out")

    def test_changed_original_mesh_or_actual_control_signature_is_rejected(self):
        for change in ("mesh","controls"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{"RUNNER_TEMP":temp}),self.mocked_native_reducer():
                root=Path(temp);source=self.source(root)
                if change=="mesh":(source/"impeller-case/constant/polyMesh/points").write_bytes(b"changed actual mesh")
                else:
                    packet=case.json_file(source,"native-baseline-reduction.json")
                    packet["study"]["controls"]["meshSha256"]="0"*64
                    packet["nativeObservation"]["controls"]["meshSha256"]="0"*64
                    packet["manifest"]["controls"]["meshSha256"]="0"*64
                    packet["inputManifest"]["controls"]["meshSha256"]="0"*64
                    save_json(source,"native-baseline-reduction.json",packet)
                with self.assertRaises(ValueError):case.prepare(source,root/"out")

    def test_source_and_prepared_fields_mesh_model_and_qualifier_mutations_fail(self):
        mutations=("source-copy","mesh","inlet-pressure","physical-wall","outlet-flow","phase-model","promoted-flag")
        for change in mutations:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{"RUNNER_TEMP":temp}),self.mocked_native_reducer():
                root=Path(temp);source=self.source(root);output=root/"out";case.prepare(source,output)
                if change=="source-copy":(output/"source-baseline/impeller-case/500/U").write_bytes(b"changed")
                elif change=="mesh":(output/"case/constant/polyMesh/points").write_bytes(b"changed")
                elif change=="promoted-flag":
                    report=case.json_file(output,case.REPORT);report["phaseFamilyReferenceQualified"]=True;save_json(output,case.REPORT,report)
                else:
                    path=output/"case"/({"inlet-pressure":"0/p_rgh","physical-wall":"0/U","outlet-flow":"0/U","phase-model":"constant/fvModels"}[change])
                    raw=path.read_bytes()
                    if change=="inlet-pressure":raw=raw.replace(b"300000",b"600000")
                    elif change=="physical-wall":raw=raw.replace(b'    "blade"\n    {',b'    "missingBlade"\n    {')
                    elif change=="outlet-flow":raw=raw.replace(b"volumetricFlowRate ",b"wrongVolumetricFlowRate ")
                    else:raw=raw.replace(b"pSat 2339",b"pSat 9999")
                    path.write_bytes(raw)
                with self.assertRaises(ValueError):case.verify_output(output)

    def test_bounds_bad_paths_native_results_and_unsupported_modes_fail_closed(self):
        for dt,duration in ((0,case.DEFAULT_DURATION_S),(case.MAX_DT_S*2,case.DEFAULT_DURATION_S),(case.MAX_DT_S,3*case.PERIOD_S),(case.MAX_DT_S,11*case.PERIOD_S)):
            with self.assertRaises(ValueError):case.native_recipe(case.protocol.prepare_study(),dt,duration)
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}),self.mocked_native_reducer():
            root=Path(temp);source=self.source(root);output=root/"out";case.prepare(source,output)
            (output/"case/0/p").write_bytes(b"do not copy baseline result")
            with self.assertRaises(ValueError):case.verify_output(output)
            (output/"case/0/p").unlink()
            report=case.json_file(output,case.REPORT);report["mode"]="transient-sliding";save_json(output,case.REPORT,report)
            with self.assertRaises(ValueError):case.verify_output(output)
            with self.assertRaises(ValueError):case.records(root,[{"path":"../outside","bytes":1,"sha256":"a"*64}])
            with patch.object(case,"MAX_SOURCE_RECORDS",1),self.assertRaises(ValueError):case.verify_baseline(source)
            with patch.object(case,"MAX_WORKSPACE_BYTES",1),self.assertRaises(ValueError):case.verify_baseline(source)

    def test_source_change_during_persistence_cleans_attempt_owned_output(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}),self.mocked_native_reducer():
            root=Path(temp);source=self.source(root);output=root/"out"
            with patch.object(case,"verify_output",side_effect=ValueError("deliberate partial preparation failure")),self.assertRaises(ValueError):case.prepare(source,output)
            self.assertFalse(output.exists());self.assertTrue(source.is_dir())

    def test_actual_source_postcopy_sha_is_checked_before_completion(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}),self.mocked_native_reducer():
            root=Path(temp);source=self.source(root);output=root/"out";verify=case.verify_output
            def change_original_after_copy(directory):
                result=verify(directory)
                (source/"impeller-case/500/U").write_bytes(b"source changed after all copies")
                return result
            with patch.object(case,"verify_output",side_effect=change_original_after_copy),self.assertRaises(ValueError):case.prepare(source,output)
            self.assertFalse(output.exists());self.assertTrue(source.is_dir())


if __name__ == "__main__":
    unittest.main()
