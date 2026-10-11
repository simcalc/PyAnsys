"""Analytic synthetic contracts only: these tests are NOT physical CFD evidence."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from frozen_rotor_research_protocol import (ProtocolError, prepare_study, reduce_run,
                                          pressure_sweep, numerical_verification,
                                          time_step_stages, verify_restart)


def synthetic_study(**inputs):
    return prepare_study(inputs, {key: "a" * 64 for key in
                                 ("geometrySha256", "meshSha256", "boundarySha256",
                                  "discretizationSha256", "runtimeSha256", "cavitationModelSha256")})


def synthetic_run(study, head=10.0, npsha=8.0, model="steady-single-phase-MRF"):
    """Deliberately synthetic samples with analytic expected head/NPSHA."""
    rho, g, pv = (study["input"][key] for key in ("densityKgM3", "gravityMS2", "vaporPressurePa"))
    ps = pv + (npsha - 0.5) * rho * g
    pt = ps + 0.5 * rho * g
    run = {"studyInputs": deepcopy(study["input"]), "controls": deepcopy(study["controls"]),
            "samplingDefinition": deepcopy(study["samplingDefinition"]), "physicalBasis": deepcopy(study["physicalBasis"]),
            "runRevision": "Current", "physicalModel": model, "pressureLevelAbsolutePa": ps,
            "nativeEvidence": {"kind": "native-solver-sampling", "upstreamVerified": True,
                               "sha256": "b" * 64, "runId": "SYNTHETIC-UNIT-TEST-NOT-CFD"},
            "pressureMapping": {"nativeField": "p", "nativeUnit": "m2/s2",
                                "absolutePressureReferencePa": 101325,
                                "absoluteReconstructionEquation": "p_abs = rho*p_kinematic + p_reference",
                                "hydrostaticReconstructionApplied": False},
            "measurements": {"inletStaticPressureAbsolutePa": ps, "inletMeanSquaredAbsoluteSpeedM2S2": g,
                             "inletTotalPressureAbsolutePa": pt, "outletTotalPressureAbsolutePa": pt + head * rho * g,
                             "inletElevationM": 0, "outletElevationM": 0, "minimumStaticPressureAbsolutePa": min(ps, 50000),
                             "inletMaximumVaporFraction": 0, "outletMaximumVaporFraction": 0},
            "quality": {"nativeMeshQualityChecked": True, "minimumCellVolumeM3": 1e-8,
                        "outwardInletMassFlowKgS": -90, "outwardOutletMassFlowKgS": 90,
                        "reverseFlowFraction": 0, "finalResidualHistory": {key: [1e-6] * 5 for key in ("p", "U", "k", "omega")},
                        "residualDefinition": "initial-solver-residual-per-outer-iteration-final-window",
                        "samplingWindowDefinition": "final-converged-window", "finalFlowRelativeSpan": 0.0001,
                        "headHistoryM": [head] * 5, "minimumVaporFraction": 0, "maximumVaporFraction": 0.2,
                        "statisticalWindowS": [0.2, 0.4], "periodicHeadDifferencesM": [1e-5, 1e-5],
                        "physicalTimeStepS": 0.0005, "observedMaximumTimeStepS": 0.0005,
                        "observedMaxCo": 0.5, "observedMaxAlphaCo": 0.2,
                        "meshCellCount": 64, "totalCellVolumeM3": 1.0}}
    if model == "steady-single-phase-MRF":
        run["controls"]["cavitationModelSha256"] = None
    refresh_synthetic_proof(study, run)
    return run


def refresh_synthetic_proof(study, run):
    """Synthetic metadata binding, not proof of native files or a CFD execution."""
    proof = {"upstreamVerified": True, "samplingEvidenceSha256": run["nativeEvidence"]["sha256"],
             "structuralControls": deepcopy(run["controls"]), "signatureScope": deepcopy(study["signatureScope"]),
             "physicalModel": run["physicalModel"], "pressureBoundaryDefinition": study["controlledPressureDefinition"],
             "pressureLevelAbsolutePa": run["pressureLevelAbsolutePa"],
             "physicalTimeStepS": run["quality"]["physicalTimeStepS"] if run["physicalModel"].startswith("transient") else None}
    proof["nativeInputManifestSha256"] = hashlib.sha256(b"SYNTHETIC-UNIT-TEST:" + json.dumps(proof, sort_keys=True).encode()).hexdigest()
    run["controlledVariationProof"] = proof


class FrozenRotorProtocol(unittest.TestCase):
    def setUp(self):
        self.study = synthetic_study()
        self.run = synthetic_run(self.study)

    def test_preparation_labels_source_and_classroom_assumptions_without_execution(self):
        packet = prepare_study()
        self.assertEqual(packet["status"], "NativeSignaturesRequired")
        self.assertEqual(packet["input"]["speedRpm"], 1450)
        self.assertEqual(packet["sourceReference"]["inletSupersonicOrInitialGaugePressurePa"], -100)
        self.assertEqual(packet["physicalBasis"]["gravityVectorMS2"], [0, 0, 0])
        self.assertFalse(packet["physicalBasis"]["gravityInSolver"])
        self.assertEqual(packet["physicalBasis"]["headGravityReferenceMS2"], 9.81)
        forced = prepare_study(gravity_in_solver=True)
        self.assertEqual(forced["physicalBasis"]["gravityVectorMS2"], [0, 0, -9.81])
        self.assertIn("assumptions", packet["declaredAssumptions"]["propertySource"])
        for key in ("simulationExecuted", "actualCfdRunVerified", "mainApplicationWrites"):
            self.assertIs(packet[key], False)
        self.assertIs(packet["sourceReference"]["mainCanonicalReference"], False)

    def test_finite_SI_and_explicit_control_input_validation(self):
        for bad in (True, float("nan"), float("inf"), -1, 0, "998.2"):
            with self.subTest(bad=bad), self.assertRaises(ProtocolError):
                prepare_study({"densityKgM3": bad})
        with self.assertRaises(ProtocolError):
            prepare_study({"certificateAvailable": False})
        with self.assertRaises(ProtocolError):
            prepare_study(controls={"meshSha256": "placeholder"})

    def test_hand_calculated_head_elevation_and_velocity_second_moment(self):
        self.run["measurements"].update(inletElevationM=1, outletElevationM=3,
                                        inletMeanSquaredAbsoluteSpeedM2S2=25,
                                        inletMeanSpeedMS=3)
        self.run["quality"]["headHistoryM"] = [12] * 5
        result = reduce_run(self.study, self.run)
        self.assertEqual(result["status"], "QualifiedSuppliedObservation")
        self.assertAlmostEqual(result["stages"]["head"]["result"]["value"], 12)
        self.assertAlmostEqual(result["stages"]["npsha"]["result"]["value"], 7.5 + 25 / (2 * 9.81) + 1)
        for trace in result["stages"].values():
            self.assertEqual(set(trace), {"input", "equation", "substitution", "result", "calculationAuthority"})
            self.assertEqual(trace["calculationAuthority"], "Python")

    def test_suction_datum_and_negative_NPSHA_are_actual_numeric_domain_results(self):
        study = synthetic_study(suctionDatumM=1)
        run = synthetic_run(study)
        run["measurements"].update(inletStaticPressureAbsolutePa=1,
                                   inletMeanSquaredAbsoluteSpeedM2S2=0)
        result = reduce_run(study, run)
        self.assertLess(result["stages"]["npsha"]["result"]["value"], 0)
        self.assertEqual(result["npshaDomainStatus"], "FLASHING_CAVITATION_RISK")
        self.assertIn("head", result["stages"])
        run["measurements"]["inletMeanSquaredAbsoluteSpeedM2S2"] = 100
        result = reduce_run(study, run)
        self.assertGreater(result["stages"]["npsha"]["result"]["value"], 0)
        self.assertEqual(result["npshaDomainStatus"], "FLASHING_CAVITATION_RISK")

    def test_missing_head_does_not_erase_valid_suction_equation(self):
        del self.run["measurements"]["outletTotalPressureAbsolutePa"]
        result = reduce_run(self.study, self.run)
        self.assertNotIn("head", result["stages"])
        self.assertAlmostEqual(result["stages"]["npsha"]["result"]["value"], 8)
        self.assertEqual(result["diagnostics"][0]["branch"], "head")

    def test_missing_suction_does_not_erase_valid_head(self):
        del self.run["measurements"]["inletStaticPressureAbsolutePa"]
        result = reduce_run(self.study, self.run)
        self.assertNotIn("npsha", result["stages"])
        self.assertAlmostEqual(result["stages"]["head"]["result"]["value"], 10)

    def test_pressure_basis_and_p_rgh_reconstruction_are_explicit(self):
        for mutation in (
                lambda row: row["samplingDefinition"].update(pressureUnit="m2/s2"),
                lambda row: row["pressureMapping"].update(nativeField="p_rgh", nativeUnit="Pa"),
                lambda row: row["pressureMapping"].pop("absolutePressureReferencePa"),
                lambda row: row["physicalBasis"].update(gravityVectorMS2=[0, 0, 9.81])):
            run = deepcopy(self.run)
            mutation(run)
            with self.assertRaises(ProtocolError):
                reduce_run(self.study, run)
        self.run["pressureMapping"].update(nativeField="p_rgh", nativeUnit="Pa", hydrostaticReconstructionApplied=True,
                                           absolutePressureReferencePa=0,
                                           absoluteReconstructionEquation="p = p_rgh + rho*gh; native absolute reference applied")
        self.assertEqual(reduce_run(self.study, self.run)["status"], "QualifiedSuppliedObservation")

    def test_quality_cannot_be_fabricated_from_absent_or_failed_metrics(self):
        changes = (("minimumCellVolumeM3", 0), ("outwardInletMassFlowKgS", 90),
                   ("outwardOutletMassFlowKgS", 91), ("reverseFlowFraction", 0.1),
                   ("headHistoryM", [7, 10, 13, 9, 11]), ("headHistoryM", [11] * 5),
                   ("finalFlowRelativeSpan", 0.02), ("finalResidualHistory", {"p": [1] * 5}),
                   ("residualDefinition", "final-linear-solver-residual"))
        for key, value in changes:
            run = deepcopy(self.run)
            run["quality"][key] = value
            with self.subTest(key=key):
                self.assertEqual(reduce_run(self.study, run)["status"], "QualificationRequired")
        del self.run["quality"]
        self.assertEqual(reduce_run(self.study, self.run)["diagnostics"][-1]["reasonCode"], "CFD_QUALITY_PACKET_REQUIRED")

    def test_stale_or_mismatched_evidence_never_enters_reduction(self):
        for mutation in (lambda row: row.update(runRevision="Stale"),
                         lambda row: row["studyInputs"].update(speedRpm=1500),
                         lambda row: row["controls"].update(meshSha256="c" * 64),
                         lambda row: row["nativeEvidence"].update(upstreamVerified=False),
                         lambda row: row["nativeEvidence"].update(sha256="")):
            run = deepcopy(self.run)
            mutation(run)
            with self.assertRaises(ProtocolError):
                reduce_run(self.study, run)

    def test_phase_change_liquid_section_cut_gate_is_branch_specific(self):
        run = synthetic_run(self.study, model="steady-cavitation-MRF")
        run["measurements"]["outletMaximumVaporFraction"] = 0.1
        result = reduce_run(self.study, run)
        self.assertNotIn("head", result["stages"])
        self.assertIn("npsha", result["stages"])
        self.assertEqual(result["diagnostics"][0]["reasonCode"], "CFD_LIQUID_OUTLET_SAMPLING_REQUIRED")
        run["measurements"]["outletMaximumVaporFraction"] = 0
        run["quality"]["maximumVaporFraction"] = 1.01
        self.assertEqual(reduce_run(self.study, run)["status"], "QualificationRequired")

    def test_pressure_sweep_bracket_reduces_3_percent_threshold_without_certificate(self):
        low = synthetic_run(self.study, 9.5, 2, "steady-cavitation-MRF")
        high = synthetic_run(self.study, 9.9, 4, "steady-cavitation-MRF")
        result = pressure_sweep(self.study, self.run, [high, low])
        self.assertEqual(result["status"], "BracketedReduction")
        self.assertAlmostEqual(result["stages"]["npsh3"]["result"]["value"], 3)
        self.assertIn("educational", result["quantityLabel"])
        self.assertIn("not manufacturer-published", result["quantityLabel"])
        self.assertIn("inside", result["method"])

    def test_non_crossing_and_invalid_baseline_never_extrapolate(self):
        rows = [synthetic_run(self.study, 9.9, 4, "steady-cavitation-MRF"),
                synthetic_run(self.study, 10, 5, "steady-cavitation-MRF")]
        result = pressure_sweep(self.study, self.run, rows)
        self.assertEqual(result["status"], "BracketRequired")
        self.assertIsNone(result["npsh3"])
        self.run["measurements"]["minimumStaticPressureAbsolutePa"] = 2000
        with self.assertRaisesRegex(ProtocolError, "NONCAVITATING_BASELINE"):
            pressure_sweep(self.study, self.run, rows)

    def test_exact_threshold_roundoff_and_pressure_setpoint_uniqueness(self):
        rows = [synthetic_run(self.study, 9.7, 3, "steady-cavitation-MRF"),
                synthetic_run(self.study, 9.9, 4, "steady-cavitation-MRF")]
        self.assertAlmostEqual(pressure_sweep(self.study, self.run, rows)["stages"]["npsh3"]["result"]["value"], 3)
        rows[1]["pressureLevelAbsolutePa"] = rows[0]["pressureLevelAbsolutePa"]
        refresh_synthetic_proof(self.study, rows[1])
        with self.assertRaisesRegex(ProtocolError, "DISTINCT_PRESSURE"):
            pressure_sweep(self.study, self.run, rows)

    def test_transient_sweep_requires_own_nonvapor_transient_reference(self):
        rows = [synthetic_run(self.study, 9.5, 2, "transient-cavitation-sliding-mesh"),
                synthetic_run(self.study, 9.9, 4, "transient-cavitation-sliding-mesh")]
        with self.assertRaisesRegex(ProtocolError, "BASELINE_FRAME_FAMILY_MISMATCH"):
            pressure_sweep(self.study, self.run, rows)
        baseline = synthetic_run(self.study, model="transient-cavitation-sliding-mesh")
        with self.assertRaisesRegex(ProtocolError, "NONCAVITATING_VAPOR_REFERENCE"):
            pressure_sweep(self.study, baseline, rows)
        baseline["quality"]["maximumVaporFraction"] = 0
        self.assertEqual(pressure_sweep(self.study, baseline, rows)["status"], "BracketedReduction")

    def refinement_runs(self, parameter):
        rows = [synthetic_run(self.study, head) for head in (10.16, 10.04, 10.01)]
        for row, count, signature, dt in zip(rows, (64, 512, 4096), ("a", "c", "d"), (0.001, 0.0005, 0.00025)):
            row["pressureLevelAbsolutePa"] = 100000
            if parameter == "mesh":
                row["controls"]["meshSha256"] = signature * 64
                row["quality"]["meshCellCount"] = count
            else:
                row["physicalModel"] = "transient-cavitation-sliding-mesh"
                row["controls"]["cavitationModelSha256"] = self.study["controls"]["cavitationModelSha256"]
                row["quality"].update(physicalTimeStepS=dt, observedMaximumTimeStepS=dt)
            refresh_synthetic_proof(self.study, row)
        return rows

    def test_three_actual_matched_space_or_time_runs_have_hand_known_second_order(self):
        for parameter in ("mesh", "time-step"):
            result = numerical_verification(self.study, self.refinement_runs(parameter), parameter)
            self.assertEqual(result["status"], "CriterionSatisfied")
            self.assertAlmostEqual(result["stages"]["observedOrder"]["result"]["value"], 2)
            self.assertAlmostEqual(result["stages"]["fineGci"]["result"]["value"], 1.25 * (0.03 / 10.01) / 3)
            self.assertIn("not experimental", result["claimBoundary"])

    def test_numerical_nonmonotonic_missing_third_run_and_mismatched_volume(self):
        rows = self.refinement_runs("mesh")
        with self.assertRaisesRegex(ProtocolError, "THREE_REFINEMENT_RUNS"):
            numerical_verification(self.study, rows[:2], "mesh")
        rows[0] = synthetic_run(self.study, 10.02)
        rows[0]["pressureLevelAbsolutePa"] = 100000
        refresh_synthetic_proof(self.study, rows[0])
        result = numerical_verification(self.study, rows, "mesh")
        self.assertEqual(result["status"], "ConvergenceNotDemonstrated")
        rows = self.refinement_runs("mesh")
        rows[1]["quality"]["totalCellVolumeM3"] = 2
        with self.assertRaisesRegex(ProtocolError, "FIXED_FLUID_DOMAIN_VOLUME"):
            numerical_verification(self.study, rows, "mesh")

    def test_transient_requires_physical_window_Co_alphaCo_dt_and_periodic_evidence(self):
        for key, value in (("statisticalWindowS", [0.2, 0.201]), ("observedMaxCo", 2),
                           ("observedMaxAlphaCo", 0.6), ("observedMaximumTimeStepS", 0.005),
                           ("periodicHeadDifferencesM", [2, 2])):
            rows = self.refinement_runs("time-step")
            rows[1]["quality"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ProtocolError, "QUALIFIED_REFINEMENT_RUNS"):
                numerical_verification(self.study, rows, "time-step")
        rows = self.refinement_runs("time-step")
        rows[1]["quality"]["statisticalWindowS"] = [0.3, 0.5]
        with self.assertRaisesRegex(ProtocolError, "MATCHED_PHYSICAL_TIME_WINDOW"):
            numerical_verification(self.study, rows, "time-step")

    def test_rotor_kinematics_are_planned_physical_time_and_correct_SI(self):
        packet = time_step_stages(self.study, 60 / 1450, 0.0005)
        self.assertAlmostEqual(packet["omega"]["result"]["value"], 151.84364492350666)
        self.assertAlmostEqual(packet["rotorAngle"]["result"]["value"], 2 * math.pi)
        self.assertIn("not a transient solution", packet["kind"])

    def restart_records(self):
        checkpoint = synthetic_run(self.study, model="transient-cavitation-sliding-mesh")
        checkpoint.update(physicalTimeS=0.1, physicalTimeStepS=0.0005)
        continuous = deepcopy(checkpoint)
        continuous.update(physicalTimeS=0.2, checkpointSha256=checkpoint["nativeEvidence"]["sha256"],
                          fieldCellCount=4, fieldLocation="cell-centers", fieldOrderSha256="e" * 64,
                          fields={"pAbsolutePa": [100000] * 4, "UAbsoluteMS": [1, 0, 0] * 4, "vaporFraction": [0.1] * 4})
        restarted = deepcopy(continuous)
        for row in (checkpoint, continuous, restarted):
            row["rotorAngleUnwrappedRad"] = time_step_stages(self.study, row["physicalTimeS"], 0.0005)["rotorAngle"]["result"]["value"]
        return checkpoint, continuous, restarted

    def test_restart_compares_fields_after_meaningful_same_time_mesh_and_checkpoint(self):
        rows = self.restart_records()
        tolerances = {"pAbsolutePa": 0.01, "UAbsoluteMS": 1e-6, "vaporFraction": 1e-6}
        self.assertEqual(verify_restart(self.study, *rows, tolerances)["status"], "CriterionSatisfied")
        rows[2]["fields"]["pAbsolutePa"][0] += 1
        self.assertEqual(verify_restart(self.study, *rows, tolerances)["status"], "RestartMismatch")

    def test_control_signature_scope_and_native_variation_proof_are_required(self):
        run = synthetic_run(self.study, model="steady-cavitation-MRF")
        for mutation in (lambda row: row.pop("controlledVariationProof"),
                         lambda row: row["controlledVariationProof"].update(pressureLevelAbsolutePa=1),
                         lambda row: row["controlledVariationProof"].update(samplingEvidenceSha256="f" * 64),
                         lambda row: row["controlledVariationProof"]["signatureScope"].update(boundarySha256="all inputs"),
                         lambda row: row["controls"].update(cavitationModelSha256=None)):
            changed = deepcopy(run)
            mutation(changed)
            with self.assertRaises(ProtocolError):
                reduce_run(self.study, changed)
        rows = self.refinement_runs("time-step")
        rows[0]["controlledVariationProof"]["physicalTimeStepS"] = 1
        with self.assertRaisesRegex(ProtocolError, "NATIVE_TIME_STEP_PROOF"):
            numerical_verification(self.study, rows, "time-step")

    def test_restart_archive_only_short_window_wrong_field_order_time_or_angle_rejected(self):
        mutations = (lambda row: row.pop("fields"), lambda row: row.update(fieldOrderSha256="f" * 64),
                     lambda row: row.update(physicalTimeS=0.20025), lambda row: row.update(rotorAngleUnwrappedRad=0),
                     lambda row: row.update(checkpointSha256="f" * 64),
                     lambda row: row["fields"]["vaporFraction"].__setitem__(0, 2))
        tolerances = {"pAbsolutePa": 0.01, "UAbsoluteMS": 1e-6, "vaporFraction": 1e-6}
        for mutation in mutations:
            rows = self.restart_records()
            mutation(rows[2])
            with self.subTest(mutation=mutation), self.assertRaises(ProtocolError):
                verify_restart(self.study, *rows, tolerances)
        rows = self.restart_records()
        for row in rows[1:]:
            row["physicalTimeS"] = 0.1005
        with self.assertRaisesRegex(ProtocolError, "MEANINGFUL_MATCHED_RESTART"):
            verify_restart(self.study, *rows, tolerances)


if __name__ == "__main__":
    unittest.main()
