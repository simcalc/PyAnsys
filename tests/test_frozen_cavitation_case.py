"""Synthetic file/physics contracts only; no fixture is actual CFD evidence."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import frozen_rotor_cavitation_case as campaign


def record(path, root):
    raw = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def synthetic_baseline(root):
    """Analytic baseline plus toy native bytes, deliberately not a simulation."""
    root.mkdir()
    prepared = campaign.base.case_files("impeller")
    for name in sorted(campaign.INPUT_FILES):
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prepared[name], encoding="ascii")
    for name in sorted(campaign.MESH_FILES):
        path = root / "constant/polyMesh" / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(campaign.base.foam_header(name) + "0\n()\n", encoding="ascii")
    paths = [root / name for name in campaign.INPUT_FILES]
    paths += [root / "constant/polyMesh" / name for name in campaign.MESH_FILES]
    records = sorted([record(path, root) for path in paths], key=lambda row: row["path"])
    mesh_records = [row for row in records if row["path"].startswith("constant/polyMesh/")]
    controls = {key: "a" * 64 for key in campaign.protocol.CONTROL_KEYS}
    controls["meshSha256"] = sha256(campaign.canonical(mesh_records)).hexdigest()
    study = campaign.protocol.prepare_study(controls=controls)
    rho, g = study["input"]["densityKgM3"], study["input"]["gravityMS2"]
    sampling = root / "baseline-sampling.json"
    sampling.write_text(json.dumps({"fixtureKind": "SYNTHETIC-UNIT-TEST-NOT-CFD"}), encoding="ascii")
    sampling_hash = sha256(sampling.read_bytes()).hexdigest()
    actual_controls = deepcopy(controls); actual_controls["cavitationModelSha256"] = None
    observation = {"studyInputs": deepcopy(study["input"]), "controls": actual_controls,
                   "samplingDefinition": deepcopy(study["samplingDefinition"]), "physicalBasis": deepcopy(study["physicalBasis"]),
                   "runRevision": "Current", "physicalModel": "steady-single-phase-MRF", "pressureLevelAbsolutePa": 101325,
                   "nativeEvidence": {"kind": "native-solver-sampling", "upstreamVerified": True, "sha256": sampling_hash,
                                      "runId": "SYNTHETIC-UNIT-TEST-NOT-CFD"},
                   "pressureMapping": {"nativeField": "p", "nativeUnit": "m2/s2", "absolutePressureReferencePa": 101325,
                                       "absoluteReconstructionEquation": "p_abs=rho*p_kinematic+101325", "hydrostaticReconstructionApplied": False},
                   "measurements": {"inletStaticPressureAbsolutePa": 101325 - 2 * rho, "inletMeanSquaredAbsoluteSpeedM2S2": 4,
                                    "inletTotalPressureAbsolutePa": 101325, "outletTotalPressureAbsolutePa": 101325 + 10*rho*g,
                                    "inletElevationM": 0, "outletElevationM": 0, "minimumStaticPressureAbsolutePa": 50000},
                   "quality": {"nativeMeshQualityChecked": True, "minimumCellVolumeM3": 1e-8,
                               "outwardInletMassFlowKgS": -90, "outwardOutletMassFlowKgS": 90, "reverseFlowFraction": 0,
                               "finalResidualHistory": {key: [1e-6]*5 for key in ("p", "U", "k", "omega")},
                               "residualDefinition": "initial-solver-residual-per-outer-iteration-final-window",
                               "samplingWindowDefinition": "final-converged-window", "finalFlowRelativeSpan": 0.0001,
                               "headHistoryM": [10]*5}}
    manifest_hash = sha256(campaign.canonical(records)).hexdigest()
    observation["controlledVariationProof"] = {"upstreamVerified": True, "samplingEvidenceSha256": sampling_hash,
          "structuralControls": deepcopy(actual_controls), "signatureScope": deepcopy(study["signatureScope"]),
          "physicalModel": observation["physicalModel"], "pressureBoundaryDefinition": study["controlledPressureDefinition"],
          "pressureLevelAbsolutePa": 101325, "physicalTimeStepS": None, "nativeInputManifestSha256": manifest_hash}
    packet = {"schemaVersion": campaign.BASELINE_SCHEMA, "exampleDataCommit": campaign.base.inventory.PIN,
              "openFoamFoundationVersion": 12, "mainApplicationWrites": False, "study": study, "baselineObservation": observation,
              "sourceCaseFiles": records, "sourceCaseManifestSha256": manifest_hash,
              "samplingFile": record(sampling, root),
              "nativeAcceptance": {key: True for key in ("conversionVerified", "physicalWallTypesVerified", "checkMeshVerified", "nonConformalCoverageVerified")}}
    return packet


class NativeCampaignContracts(unittest.TestCase):
    def test_recipe_absolute_pressure_phase_model_and_liquid_boundaries(self):
        study = campaign.protocol.prepare_study()
        files = campaign.native_recipe(study, 50000, "steady-sweep", 0.0001, 0.3)
        self.assertIn("solver incompressibleVoF", files["system/controlDict"])
        self.assertIn("ddtSchemes {default Euler;}", files["system/fvSchemes"])
        self.assertIn("PIMPLE", files["system/fvSolution"])
        self.assertIn("SchnerrSauer", files["constant/fvModels"])
        self.assertIn("pSat 2339", files["constant/fvModels"])
        self.assertIn("[1 -1 -2", files["0/p_rgh"])
        self.assertIn("prghTotalPressure; p0 uniform 50000", files["0/p_rgh"])
        self.assertIn("value (0 0 0)", files["constant/g"])
        self.assertIn("phases (water vapour)", files["constant/phaseProperties"])
        self.assertIn("internalField uniform 1", files["0/alpha.water"])
        self.assertIn("omega 1450 [rpm]", files["constant/MRFProperties"])
        self.assertNotIn("constant/dynamicMeshDict", files)
        self.assertNotIn("0/p", files)

    def test_physical_sliding_excludes_mrf_and_changes_actual_wall_motion(self):
        files = campaign.native_recipe(campaign.protocol.prepare_study(), 50000, "transient-sliding", 0.0001, 0.3)
        self.assertNotIn("constant/MRFProperties", files)
        motion = files["constant/dynamicMeshDict"]
        for expected in ("cellZone rotating-fluid", "motionSolver solidBody", "rotatingMotion", "axis (0 0 1)", "omega 1450 [rpm]"):
            self.assertIn(expected, motion)
        self.assertIn("movingWallVelocity", files["0/U"])
        self.assertIn("movingWallSlipVelocity", files["0/U"])
        self.assertIn("type noSlip", files["0/U"])
        self.assertNotIn("MRFnoSlip", files["0/U"])
        self.assertIn("correctMeshPhi yes", files["system/fvSolution"])
        self.assertIn("MeshPhi", files["system/fvSolution"])

    def test_sampling_uses_native_mass_flux_absolute_pa_and_phase_extrema(self):
        text = campaign.phase_functions()
        for token in ("rho rho", "pRef 0", "fields (rhoPhi)", "weightField rhoPhi", "fields (p totalPressurePa U2 alpha.water)",
                      "operation min; fields (alpha.water)", "operation max; fields (alpha.water)", "operation min; fields (p)",
                      "pumpInterface_on_interface-impeller-outlet", "pumpInterface_on_interface-volute-inlet"):
            self.assertIn(token, text)
        self.assertNotIn("pRef 101325", text)

    def test_time_pressure_and_mode_require_explicit_valid_declarations(self):
        study = campaign.protocol.prepare_study()
        for p0, mode, dt, duration in ((float("nan"),"steady-sweep",.0001,.3), (50000,"fake",.0001,.3),
                                       (50000,"steady-sweep",.01,.3), (50000,"steady-sweep",.0001,.01),
                                       (50000,"steady-sweep",.0001,1)):
            with self.subTest(mode=mode,dt=dt), self.assertRaises((campaign.CampaignIssue,campaign.protocol.ProtocolError)):
                campaign.native_recipe(study,p0,mode,dt,duration)

    def test_conditional_preparation_binds_real_fixture_bytes_and_never_claims_cfd(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp); source=root/"baseline";packet=synthetic_baseline(source)
            before={row["path"]:sha256((source/row["path"]).read_bytes()).hexdigest() for row in packet["sourceCaseFiles"]}
            report=campaign.prepare(source,packet,root/"new-case",pressure_absolute_pa=50000,mode="transient-sliding",dt_s=.0001,duration_s=.3)
            for key in ("simulationExecuted","actualCfdRunVerified","qualifiedBaseline","npsh3Verified","restartVerified","mainApplicationWrites"):
                self.assertIs(report[key],False)
            self.assertTrue(report["temporalPlan"]["physicalRotorMotion"])
            self.assertEqual(report["absolutePressureMapping"]["referenceAdditionPa"],0)
            self.assertIn("not automatically",report["sourceBaselineRole"])
            self.assertIn("same authored controls",report["phaseFamilyReferenceRequired"])
            self.assertTrue((root/"new-case/case/constant/polyMesh/points").is_file())
            self.assertFalse((root/"new-case/case/constant/MRFProperties").exists())
            self.assertFalse((root/"new-case/case/0/p").exists())
            self.assertEqual(before,{row["path"]:sha256((source/row["path"]).read_bytes()).hexdigest() for row in packet["sourceCaseFiles"]})
            with self.assertRaises((campaign.CampaignIssue,campaign.base.inventory.InspectionIssue)):
                campaign.prepare(source,packet,root/"new-case",pressure_absolute_pa=50000,mode="steady-sweep",dt_s=.0001,duration_s=.3)

    def test_missing_failed_stale_or_changed_native_evidence_rejects_before_output(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);source=root/"baseline";packet=synthetic_baseline(source)
            changes=[("nativeAcceptance","nonConformalCoverageVerified",False),
                     ("baselineObservation","runRevision","Stale"),
                     ("baselineObservation","physicalModel","transient-cavitation-sliding-mesh")]
            for group,key,value in changes:
                wrong=deepcopy(packet);wrong[group][key]=value
                with self.subTest(group=group,key=key),self.assertRaises((campaign.CampaignIssue,campaign.protocol.ProtocolError)):
                    campaign.prepare(source,wrong,root/"rejected",pressure_absolute_pa=50000,mode="steady-sweep",dt_s=.0001,duration_s=.3)
                self.assertFalse((root/"rejected").exists())
            (source/"constant/polyMesh/points").write_text("changed")
            with self.assertRaises(campaign.CampaignIssue):campaign.verify_baseline(source,packet)

    def test_emitted_native_reducer_contract_binds_exact_manifest_and_evidence_aggregate(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp)/"execution";root.mkdir()
            source=root/"impeller-case";wrapper=synthetic_baseline(source)
            files=[]
            for row in wrapper["sourceCaseFiles"]:
                name=row["path"]
                control="meshSha256" if name.startswith("constant/polyMesh/") else "discretizationSha256" if name.startswith("system/") else "boundarySha256"
                files.append({**row,"path":"impeller-case/"+name,"control":control})
            for name,control,body in (("openfoam-preparation.json","geometrySha256",{"exampleDataCommit":campaign.base.inventory.PIN,"mainApplicationWrites":False}),
                                      ("runtime-identity.json","runtimeSha256",{"fixtureKind":"SYNTHETIC-NOT-NATIVE"})):
                path=root/name;path.write_text(json.dumps(body),encoding="ascii")
                files.append({**record(path,root),"control":control})
            controls={"cavitationModelSha256":None}
            for key in campaign.protocol.CONTROL_KEYS-{"cavitationModelSha256"}:
                rows=sorted([{column:row[column] for column in ("path","bytes","sha256")} for row in files if row["control"]==key],key=lambda row:row["path"])
                controls[key]=sha256(campaign.canonical(rows)).hexdigest()
            study=campaign.protocol.prepare_study(controls=controls)
            observation=deepcopy(wrapper["baselineObservation"])
            observation.update(studyInputs=study["input"],controls=controls,samplingDefinition=study["samplingDefinition"],physicalBasis=study["physicalBasis"])
            manifest={"schemaVersion":"untirta.frozen-rotor-native-input-manifest.v1","controls":controls,"files":files,"physicalBasis":study["physicalBasis"],"studyInputs":study["input"]}
            manifest_path=root/"native-input-manifest.json";manifest_path.write_bytes(campaign.canonical(manifest))
            execution={key:True for key in ("openFoamConversionVerified","physicalWallTypesVerified","nativeGeometryQualityVerified","simulationExecuted","actualCfdRunVerified")}
            execution["mainApplicationWrites"]=False
            execution_path=root/"openfoam-execution.json";execution_path.write_bytes(campaign.canonical(execution))
            native_sampler=root/"impeller-case/postProcessing/weightedInlet/1/surfaceFieldValue.dat"
            native_sampler.parent.mkdir(parents=True,exist_ok=True)
            native_sampler.write_text("# SYNTHETIC-CONTRACT-NOT-CFD\n500 0 0 0\n",encoding="ascii")
            evidence=sorted([record(manifest_path,root),record(execution_path,root),record(native_sampler,root)],key=lambda row:row["path"])
            aggregate=sha256(campaign.canonical(evidence)).hexdigest()
            observation["nativeEvidence"]["sha256"]=aggregate
            proof=observation["controlledVariationProof"]
            proof.update(nativeInputManifestSha256=sha256(manifest_path.read_bytes()).hexdigest(),samplingEvidenceSha256=aggregate,
                         structuralControls=controls,signatureScope=study["signatureScope"])
            packet={"schemaVersion":campaign.NATIVE_REDUCTION_SCHEMA,"study":study,"manifest":manifest,"nativeObservation":observation,
                    "qualifiedBaseline":True,"preliminaryBaselineQualified":True,"actualCfdRunVerified":True,"mainApplicationWrites":False,
                    "nativeEvidenceRecords":evidence}
            verified=campaign.verify_baseline(root,packet)
            self.assertEqual(verified[0],source)
            self.assertEqual(verified[4],sha256(manifest_path.read_bytes()).hexdigest())
            wrong_directory=deepcopy(packet)
            for row in wrong_directory["nativeEvidenceRecords"]:
                if "/postProcessing/" in row["path"]:
                    row["path"]=row["path"].replace("/1/","/2/")
            with self.assertRaisesRegex(campaign.CampaignIssue,"Only exact native baseline"):
                campaign.verify_baseline(root,wrong_directory)
            wrong=deepcopy(packet);wrong["qualifiedBaseline"]=False
            with self.assertRaises(campaign.CampaignIssue):campaign.verify_baseline(root,wrong)
            execution_path.write_text("changed execution bytes")
            with self.assertRaises((campaign.CampaignIssue,ValueError)):campaign.verify_baseline(root,packet)

    def test_unsupported_native_include_and_path_traversal_reject(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/"points"
            path.write_text('FoamFile {}\n#include "outside"\n')
            with self.assertRaises(campaign.CampaignIssue):campaign.native_bytes(path)
        for name in ("../secret", "0/.env", "/absolute", "0/a\\b", "0/a;command"):
            with self.subTest(name=name),self.assertRaises(campaign.CampaignIssue):campaign.relative_path(name)

    def checkpoint_fixture(self, root):
        root.mkdir()
        files=campaign.native_recipe(campaign.protocol.prepare_study(),50000,"transient-sliding",.0001,.3)
        native_controls={name:text for name,text in files.items() if not name.startswith("0/")}
        for name,text in native_controls.items():
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding="ascii")
        for name in campaign.MESH_FILES:
            path=root/"constant/polyMesh"/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(campaign.base.foam_header(name)+"0\n()\n",encoding="ascii")
        paths=[root/name for name in native_controls]
        paths += [root/"constant/polyMesh"/name for name in campaign.MESH_FILES]
        records=sorted([record(path,root) for path in paths],key=lambda row:row["path"])
        time_name="0.1"
        checkpoints=[]
        for name in ("p","p_rgh","U","alpha.water","k","omega","nut","phi","Uf","rAU","alphaPhi.water","polyMesh/points","uniform/time"):
            path=root/time_name/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(campaign.base.foam_header(path.name)+"0\n()\n",encoding="ascii")
            checkpoints.append(record(path,root))
        checkpoints.sort(key=lambda row:row["path"])
        return {"schemaVersion":campaign.CHECKPOINT_SCHEMA,"physicalModel":"transient-cavitation-sliding-mesh",
                "exampleDataCommit":campaign.base.inventory.PIN,"upstreamVerified":True,"mainApplicationWrites":False,
                "nativeTimeDirectory":time_name,"physicalTimeS":.1,"physicalTimeStepS":.0001,
                "nativeCaseFiles":records,"nativeCaseManifestSha256":sha256(campaign.canonical(records)).hexdigest(),
                "meshSha256":sha256(campaign.canonical([row for row in records if row["path"].startswith("constant/polyMesh/")])).hexdigest(),
                "files":checkpoints,"checkpointSha256":sha256(campaign.canonical(checkpoints)).hexdigest()}

    def test_checkpoint_inventory_binds_moved_points_fluxes_time_and_inputs_without_restart_claim(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp)/"sliding";packet=self.checkpoint_fixture(root)
            result=campaign.verify_checkpoint(root,packet)
            self.assertTrue(result["checkpointInventoryVerified"])
            self.assertFalse(result["restartVerified"])
            (root/"0.1/polyMesh/points").write_text("changed moved points")
            with self.assertRaises(campaign.CampaignIssue):campaign.verify_checkpoint(root,packet)

    def test_checkpoint_incomplete_inventory_changed_case_or_double_rotation_rejects(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp)/"sliding";packet=self.checkpoint_fixture(root)
            missing=deepcopy(packet);missing["files"]=missing["files"][:-1]
            with self.assertRaises(campaign.CampaignIssue):campaign.verify_checkpoint(root,missing)
            (root/"constant/MRFProperties").write_text("double rotation")
            with self.assertRaises(campaign.CampaignIssue):campaign.verify_checkpoint(root,packet)
            (root/"constant/MRFProperties").unlink()
            (root/"system/controlDict").write_text("changed case")
            with self.assertRaises(campaign.CampaignIssue):campaign.verify_checkpoint(root,packet)


if __name__ == "__main__":
    unittest.main()
