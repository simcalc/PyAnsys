"""Educational reductions of supplied, upstream-verified native solver sampling.

No solver, source script, file transfer, or Main calculation is executed here.
Input declarations cannot establish that a physical CFD run actually occurred.
"""
import math
import re

SOURCE = "https://fluent.docs.pyansys.com/version/stable/examples/00-fluent/frozen_rotor_workflow.html"
MODELS = {"steady-single-phase-MRF", "steady-cavitation-MRF", "transient-cavitation-sliding-mesh"}
CONTROL_KEYS = {"geometrySha256", "meshSha256", "boundarySha256", "discretizationSha256",
                "runtimeSha256", "cavitationModelSha256"}
SAMPLING = {"pressureUnit": "Pa", "pressureBasis": "absolute",
            "totalPressureDefinition": "p_static_absolute + 0.5*rho*mean(U_absolute_squared)",
            "totalPressureDensityBasis": "study-liquid-density-at-liquid-section-cuts",
            "weighting": "signed-outward-mass-flux", "velocityFrame": "absolute",
            "kineticMoment": "mean(U_absolute_squared)", "inletSection": "inlet",
            "outletSection": "outlet", "elevationUnit": "m", "positiveElevationAxis": "+z"}


class ProtocolError(ValueError):
    """A dependent reduction lacks qualified engineering input."""


def number(value, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProtocolError("CFD_FINITE_SI_INPUT_REQUIRED")
    if (positive and value <= 0) or (nonnegative and value < 0):
        raise ProtocolError("CFD_POSITIVE_SI_INPUT_REQUIRED")
    return float(value)


def require(condition, code):
    if not condition:
        raise ProtocolError(code)


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def stage(inputs, equation, substitution, value, unit):
    return {"input": inputs, "equation": equation, "substitution": substitution,
            "result": {"value": number(value), "unit": unit}, "calculationAuthority": "Python"}


def prepare_study(inputs=None, controls=None, *, gravity_in_solver=False):
    """Prepare explicit classroom assumptions; missing native signatures remain absent."""
    defaults = {"massFlowKgS": 90.0, "speedRpm": 1450.0, "densityKgM3": 998.2,
                "viscosityPaS": 0.001002, "gravityMS2": 9.81, "temperatureK": 293.15,
                "vaporPressurePa": 2339.0, "suctionDatumM": 0.0, "initialAngleRad": 0.0}
    require(not inputs or set(inputs) <= set(defaults), "CFD_UNKNOWN_STUDY_INPUT")
    values = {**defaults, **(inputs or {})}
    require(isinstance(gravity_in_solver, bool), "CFD_EXPLICIT_GRAVITY_MODEL_REQUIRED")
    for key, value in values.items():
        number(value, positive=key not in {"suctionDatumM", "initialAngleRad"})
    require(not controls or set(controls) <= CONTROL_KEYS, "CFD_UNKNOWN_CONTROL_SIGNATURE")
    signatures = {key: (controls or {}).get(key) for key in sorted(CONTROL_KEYS)}
    require(all(value is None or digest(value) for value in signatures.values()), "CFD_SHA256_REQUIRED")
    return {"schemaVersion": "untirta.frozen-rotor-research-protocol.v1", "input": values,
            "controls": signatures, "samplingDefinition": dict(SAMPLING),
            "signatureScope": {"geometrySha256": "geometry and SI scale",
                               "meshSha256": "actual native volume mesh",
                               "boundarySha256": "patches/types/fixed inputs; excludes the declared pressure sweep scalar",
                               "discretizationSha256": "spatial schemes and linear solvers; excludes physical dt",
                               "runtimeSha256": "native executable/container build; excludes run duration and dt",
                               "cavitationModelSha256": "active phase-change model/config; null only for single-phase baseline"},
            "controlledPressureDefinition": "inlet-absolute-total-pressure",
            "physicalBasis": {"rotationAxis": [0, 0, 1], "rotationOriginM": [0, 0, 0],
                              "gravityInSolver": gravity_in_solver,
                              "gravityVectorMS2": [0, 0, -values["gravityMS2"] if gravity_in_solver else 0],
                              "headGravityReferenceMS2": values["gravityMS2"],
                              "elevationDefinition": "+z upward; inlet/outlet section elevations relative to one datum"},
            "sourceReference": {"kind": "official-educational-example", "url": SOURCE,
                                "mainCanonicalReference": False, "rotationAxis": [0, 0, 1],
                                "rotationOriginM": [0, 0, 0], "sourceSpeedRpm": 1450,
                                "sourceOutletMassFlowKgS": 90,
                                "inletSupersonicOrInitialGaugePressurePa": -100},
            "declaredAssumptions": {"fluid": "classroom water; native material properties not verified",
                                    "properties": {key: values[key] for key in
                                                   ("densityKgM3", "viscosityPaS", "temperatureK", "vaporPressurePa")},
                                    "propertySource": "explicit classroom assumptions, not a verified property publication",
                                    "gravityTreatment": "native body-force gravity included" if gravity_in_solver else
                                    "native body-force gravity neglected; reference g is retained for head and explicit elevation correction"},
            "acceptance": {"residualLimit": 1e-4, "massFlowRelativeLimit": 0.005,
                           "headWindowRelativeLimit": 0.005, "reverseFlowFractionLimit": 0.005,
                           "liquidSectionVaporFractionLimit": 1e-4,
                           "maxCo": 1.0, "maxAlphaCo": 0.5, "minimumPeriods": 3,
                           "minimumStepsPerRevolution": 20, "numericalGciFractionLimit": 0.01},
            "phaseDependencies": {"baseline": "actual mesh, native samples, conservation and convergence",
                                  "npsh3": "qualified noncavitating baseline and matched pressure sweep bracket",
                                  "numerics": "three actual matched refinement runs",
                                  "restart": "actual checkpoint and matched continued fields over a rotor revolution"},
            "status": "InputsPrepared" if all(signatures.values()) else "NativeSignaturesRequired",
            "simulationExecuted": False, "actualCfdRunVerified": False, "mainApplicationWrites": False}


def bound(study, run, *, vary_mesh=False):
    require(isinstance(study, dict) and isinstance(run, dict), "CFD_STRUCTURED_RECORD_REQUIRED")
    require(run.get("studyInputs") == study["input"], "CFD_STUDY_INPUT_MISMATCH")
    model = run.get("physicalModel")
    require(run.get("runRevision") == "Current" and model in MODELS, "CFD_CURRENT_NATIVE_RUN_REQUIRED")
    expected, actual = study["controls"], run.get("controls", {})
    keys = CONTROL_KEYS - ({"cavitationModelSha256"} if model == "steady-single-phase-MRF" else set())
    require(isinstance(actual, dict) and set(actual) == CONTROL_KEYS and all(digest(actual[key]) for key in keys)
            and (model != "steady-single-phase-MRF" or actual["cavitationModelSha256"] is None), "CFD_NATIVE_SIGNATURES_REQUIRED")
    require(all(actual[key] == expected[key] for key in keys if not (vary_mesh and key == "meshSha256")), "CFD_CONTROL_MISMATCH")
    require(run.get("samplingDefinition") == study["samplingDefinition"], "CFD_SAMPLING_DEFINITION_MISMATCH")
    require(run.get("physicalBasis") == study["physicalBasis"], "CFD_GRAVITY_ROTATION_DATUM_MISMATCH")
    evidence = run.get("nativeEvidence", {})
    require(isinstance(evidence, dict) and evidence.get("kind") == "native-solver-sampling" and evidence.get("upstreamVerified") is True
            and digest(evidence.get("sha256")) and bool(evidence.get("runId")), "CFD_UPSTREAM_NATIVE_EVIDENCE_REQUIRED")
    proof = run.get("controlledVariationProof", {})
    require(isinstance(proof, dict) and proof.get("upstreamVerified") is True and digest(proof.get("nativeInputManifestSha256"))
            and proof.get("samplingEvidenceSha256") == evidence["sha256"] and proof.get("structuralControls") == actual
            and proof.get("signatureScope") == study["signatureScope"] and proof.get("physicalModel") == model
            and proof.get("pressureBoundaryDefinition") == study["controlledPressureDefinition"]
            and number(proof.get("pressureLevelAbsolutePa"), positive=True) == number(run.get("pressureLevelAbsolutePa"), positive=True), "CFD_NATIVE_CONTROLLED_VARIATION_PROOF_REQUIRED")
    require(proof.get("physicalTimeStepS") == (number(run.get("quality", {}).get("physicalTimeStepS"), positive=True)
            if model.startswith("transient") else None), "CFD_NATIVE_TIME_STEP_PROOF_REQUIRED")
    mapping = run.get("pressureMapping", {})
    require(isinstance(mapping, dict) and mapping.get("nativeField") in {"p", "p_rgh"} and mapping.get("nativeUnit") in {"Pa", "m2/s2"}
            and bool(mapping.get("absoluteReconstructionEquation")), "CFD_ABSOLUTE_PRESSURE_MAPPING_REQUIRED")
    number(mapping.get("absolutePressureReferencePa"), nonnegative=True)
    require(mapping["nativeField"] != "p_rgh" or
            (mapping["nativeUnit"] == "Pa" and mapping.get("hydrostaticReconstructionApplied") is True),
            "CFD_P_RGH_RECONSTRUCTION_REQUIRED")


def quality(study, run):
    q, a, v = run["quality"], study["acceptance"], study["input"]
    require(isinstance(q, dict), "CFD_QUALITY_PACKET_REQUIRED")
    require(q.get("nativeMeshQualityChecked") is True, "CFD_NATIVE_MESH_QUALITY_REQUIRED")
    number(q.get("minimumCellVolumeM3"), positive=True)
    inlet, outlet = number(q.get("outwardInletMassFlowKgS")), number(q.get("outwardOutletMassFlowKgS"))
    require(inlet < 0 < outlet, "CFD_SIGNED_OUTWARD_MASS_FLUX_REQUIRED")
    require(abs(inlet + outlet) / max(-inlet, outlet) <= a["massFlowRelativeLimit"] and
            max(abs(-inlet / v["massFlowKgS"] - 1), abs(outlet / v["massFlowKgS"] - 1)) <= a["massFlowRelativeLimit"],
            "CFD_MASS_CONSERVATION_REQUIRED")
    require(number(q.get("reverseFlowFraction"), nonnegative=True) <= a["reverseFlowFractionLimit"], "CFD_REVERSE_FLOW_TREATMENT_REQUIRED")
    residuals = q.get("finalResidualHistory", {})
    require(q.get("residualDefinition") == "initial-solver-residual-per-outer-iteration-final-window", "CFD_NONLINEAR_RESIDUAL_DEFINITION_REQUIRED")
    require(isinstance(residuals, dict) and {"p", "U", "k", "omega"} <= set(residuals) and all(isinstance(history, list) and len(history) >= 5
            and all(number(x, nonnegative=True) <= a["residualLimit"] for x in history) for history in residuals.values()),
            "CFD_RESIDUAL_CONVERGENCE_REQUIRED")
    require(q.get("samplingWindowDefinition") == "final-converged-window" and
            number(q.get("finalFlowRelativeSpan"), nonnegative=True) <= a["massFlowRelativeLimit"], "CFD_FINAL_SAMPLING_WINDOW_REQUIRED")
    history = q.get("headHistoryM", [])
    require(isinstance(history, list) and len(history) >= 5, "CFD_HEAD_HISTORY_REQUIRED")
    history = [number(x) for x in history]
    scale = abs(math.fsum(x / len(history) for x in history))
    require(scale > 0, "CFD_NONZERO_HEAD_REFERENCE_REQUIRED")
    if run["physicalModel"].startswith("transient"):
        window = q.get("statisticalWindowS", [])
        require(isinstance(window, list) and len(window) == 2 and number(number(window[1]) - number(window[0], nonnegative=True), positive=True) >= a["minimumPeriods"] * 60 / v["speedRpm"],
                "CFD_PERIODIC_SAMPLING_WINDOW_REQUIRED")
        differences = q.get("periodicHeadDifferencesM", [])
        require(isinstance(differences, list) and len(differences) >= 2 and max(abs(number(x)) for x in differences) / scale <= a["headWindowRelativeLimit"], "CFD_PERIODIC_CONVERGENCE_REQUIRED")
        dt = number(q.get("physicalTimeStepS"), positive=True)
        require(dt <= 60 / v["speedRpm"] / a["minimumStepsPerRevolution"] and
                number(q.get("observedMaximumTimeStepS"), positive=True) <= dt and
                number(q.get("observedMaxCo"), nonnegative=True) <= a["maxCo"] and
                number(q.get("observedMaxAlphaCo"), nonnegative=True) <= a["maxAlphaCo"], "CFD_ACTUAL_TIME_STEP_LIMIT_REQUIRED")
    else:
        require((max(history) - min(history)) / scale <= a["headWindowRelativeLimit"], "CFD_HEAD_STABILIZATION_REQUIRED")
    if "cavitation" in run["physicalModel"]:
        require(0 <= number(q.get("minimumVaporFraction")) <= number(q.get("maximumVaporFraction")) <= 1, "CFD_VAPOR_FRACTION_BOUNDEDNESS_REQUIRED")


def reduce_run(study, run, *, vary_mesh=False):
    """Independent numeric branches survive a missing dependent head/NPSHA operand."""
    bound(study, run, vary_mesh=vary_mesh)
    v, m, stages, diagnostics = study["input"], run.get("measurements", {}), {}, []
    if not isinstance(m, dict):
        m = {}
    rho, g, pv = v["densityKgM3"], v["gravityMS2"], v["vaporPressurePa"]
    for branch in ("head", "npsha"):
        try:
            zi = number(m.get("inletElevationM"))
            if "cavitation" in run["physicalModel"]:
                require(0 <= number(m.get("inletMaximumVaporFraction")) <= study["acceptance"]["liquidSectionVaporFractionLimit"], "CFD_LIQUID_INLET_SAMPLING_REQUIRED")
            if branch == "head":
                if "cavitation" in run["physicalModel"]:
                    require(0 <= number(m.get("outletMaximumVaporFraction")) <= study["acceptance"]["liquidSectionVaporFractionLimit"], "CFD_LIQUID_OUTLET_SAMPLING_REQUIRED")
                pi, po, zo = number(m.get("inletTotalPressureAbsolutePa")), number(m.get("outletTotalPressureAbsolutePa")), number(m.get("outletElevationM"))
                value = (po - pi) / (rho * g) + zo - zi
                stages[branch] = stage({"ptInPa": pi, "ptOutPa": po, "rhoKgM3": rho, "gMS2": g, "zInM": zi, "zOutM": zo},
                                       "H = (pt_out - pt_in)/(rho*g) + z_out - z_in",
                                       f"({po} - {pi})/({rho}*{g}) + {zo} - {zi}", value, "m")
            else:
                ps, u2 = number(m.get("inletStaticPressureAbsolutePa"), positive=True), number(m.get("inletMeanSquaredAbsoluteSpeedM2S2"), nonnegative=True)
                value = (ps - pv) / (rho * g) + u2 / (2 * g) + zi - v["suctionDatumM"]
                stages[branch] = stage({"pStaticAbsolutePa": ps, "pvPa": pv, "meanU2M2S2": u2, "rhoKgM3": rho, "gMS2": g,
                                       "zInM": zi, "zDatumM": v["suctionDatumM"]},
                                       "NPSHA = (ps_abs - pv)/(rho*g) + mean(U_absolute^2)/(2*g) + z_in - z_datum",
                                       f"({ps} - {pv})/({rho}*{g}) + {u2}/(2*{g}) + {zi} - {v['suctionDatumM']}", value, "m")
        except ProtocolError as error:
            diagnostics.append({"branch": branch, "reasonCode": str(error)})
    try:
        quality(study, run)
        if "head" in stages:
            history = run["quality"]["headHistoryM"]
            mean = math.fsum(x / len(history) for x in history)
            require(abs(stages["head"]["result"]["value"] - mean) / abs(mean) <= study["acceptance"]["headWindowRelativeLimit"], "CFD_HEAD_SAMPLING_WINDOW_MISMATCH")
    except (ProtocolError, KeyError) as error:
        diagnostics.append({"branch": "run-qualification", "reasonCode": str(error) if isinstance(error, ProtocolError) else "CFD_QUALITY_PACKET_REQUIRED"})
    return {"status": "QualifiedSuppliedObservation" if not diagnostics else "QualificationRequired", "stages": stages,
            "diagnostics": diagnostics, "nativeEvidence": dict(run["nativeEvidence"]),
            "npshaDomainStatus": "FLASHING_CAVITATION_RISK" if "npsha" in stages and
            (stages["npsha"]["result"]["value"] <= 0 or stages["npsha"]["input"]["pStaticAbsolutePa"] <= pv) else "NotAssessedByNPSHRComparison",
            "claimBoundary": "Native evidence was verified upstream; this reducer did not execute or verify a solver run."}


def pressure_sweep(study, baseline, runs):
    reference = reduce_run(study, baseline)
    require(reference["status"] == "QualifiedSuppliedObservation", "CFD_QUALIFIED_BASELINE_REQUIRED")
    h0 = number(reference["stages"]["head"]["result"]["value"], positive=True)
    require(number(baseline["measurements"].get("minimumStaticPressureAbsolutePa"), positive=True) > study["input"]["vaporPressurePa"], "CFD_NONCAVITATING_BASELINE_REQUIRED")
    require(len(runs) >= 2 and all("cavitation" in run.get("physicalModel", "") for run in runs)
            and len({run["physicalModel"] for run in runs}) == 1, "CFD_MATCHED_CAVITATION_SWEEP_REQUIRED")
    require(baseline["physicalModel"].startswith("transient") == runs[0]["physicalModel"].startswith("transient"), "CFD_BASELINE_FRAME_FAMILY_MISMATCH")
    if "cavitation" in baseline["physicalModel"]:
        require(number(baseline["quality"].get("maximumVaporFraction"), nonnegative=True) <= study["acceptance"]["liquidSectionVaporFractionLimit"], "CFD_NONCAVITATING_VAPOR_REFERENCE_REQUIRED")
    require(len({number(run.get("pressureLevelAbsolutePa"), positive=True) for run in runs}) == len(runs), "CFD_DISTINCT_PRESSURE_SWEEP_REQUIRED")
    require(len({run.get("controlledVariationProof", {}).get("nativeInputManifestSha256") for run in runs}) == len(runs), "CFD_DISTINCT_NATIVE_SWEEP_INPUTS_REQUIRED")
    observations = [reduce_run(study, run) for run in runs]
    require(all(row["status"] == "QualifiedSuppliedObservation" for row in observations), "CFD_QUALIFIED_SWEEP_REQUIRED")
    points = sorted((row["stages"]["npsha"]["result"]["value"], row["stages"]["head"]["result"]["value"] / h0) for row in observations)
    require(all(points[i][0] < points[i + 1][0] and points[i][1] <= points[i + 1][1] + study["acceptance"]["headWindowRelativeLimit"]
                for i in range(len(points) - 1)), "CFD_DISTINCT_MONOTONIC_SWEEP_REQUIRED")
    ratios = [(x, 0.97 if math.isclose(y, 0.97, rel_tol=0, abs_tol=1e-12) else y) for x, y in points]
    crossing = next(((a, b) for a, b in zip(ratios, ratios[1:]) if a[1] <= 0.97 <= b[1] and b[1] > a[1]), None)
    if crossing is None:
        return {"status": "BracketRequired", "reasonCode": "CFD_NPSH3_CROSSING_REQUIRED", "points": points,
                "action": "Extend the matched pressure sweep until an actual 0.97 head-ratio bracket exists; do not extrapolate.", "npsh3": None}
    (xa, ya), (xb, yb) = crossing
    value = xa + (0.97 - ya) * (xb - xa) / (yb - ya)
    require(value >= 0, "CFD_NPSH3_LIQUID_DOMAIN_REQUIRED")
    return {"status": "BracketedReduction", "quantityLabel": "CFD-derived educational NPSH3; not manufacturer-published",
            "method": "linear interpolation inside two qualified matched samples; not an extrapolation",
            "ratioEqualityRoundoffTolerance": 1e-12,
            "stages": {"npsh3": stage({"H0M": h0, "NPSHALowM": xa, "NPSHAHighM": xb, "ratioLow": ya, "ratioHigh": yb, "targetRatio": 0.97},
                                         "NPSH3 = xa + (0.97 - ya)*(xb - xa)/(yb - ya)",
                                         f"{xa} + (0.97 - {ya})*({xb} - {xa})/({yb} - {ya})", value, "m")},
            "sourceEvidence": [reference["nativeEvidence"]] + [row["nativeEvidence"] for row in observations]}


def numerical_verification(study, runs, parameter):
    require(parameter in {"mesh", "time-step"} and len(runs) == 3, "CFD_THREE_REFINEMENT_RUNS_REQUIRED")
    observations = [reduce_run(study, run, vary_mesh=parameter == "mesh") for run in runs]
    require(all(row["status"] == "QualifiedSuppliedObservation" for row in observations), "CFD_QUALIFIED_REFINEMENT_RUNS_REQUIRED")
    require(len({run["controlledVariationProof"]["nativeInputManifestSha256"] for run in runs}) == 3, "CFD_THREE_ACTUAL_NATIVE_INPUT_MANIFESTS_REQUIRED")
    require(len({run["physicalModel"] for run in runs}) == 1 and
            len({number(run.get("pressureLevelAbsolutePa"), positive=True) for run in runs}) == 1, "CFD_REFINEMENT_CONDITION_MISMATCH")
    if parameter == "time-step":
        require(all(run["physicalModel"].startswith("transient") for run in runs) and
                all(run["quality"]["statisticalWindowS"] == runs[0]["quality"]["statisticalWindowS"] for run in runs), "CFD_MATCHED_PHYSICAL_TIME_WINDOW_REQUIRED")
    else:
        require(len({run["controls"]["meshSha256"] for run in runs}) == 3, "CFD_THREE_ACTUAL_MESHES_REQUIRED")
        volumes = [number(run["quality"].get("totalCellVolumeM3"), positive=True) for run in runs]
        require(max(volumes) / min(volumes) - 1 <= 1e-6, "CFD_FIXED_FLUID_DOMAIN_VOLUME_REQUIRED")
        counts = [run["quality"].get("meshCellCount") for run in runs]
        require(all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in counts) and len(set(counts)) == 3, "CFD_ACTUAL_CELL_COUNTS_REQUIRED")
        if runs[0]["physicalModel"].startswith("transient"):
            require(all(run["quality"]["physicalTimeStepS"] == runs[0]["quality"]["physicalTimeStepS"] and
                        run["quality"]["statisticalWindowS"] == runs[0]["quality"]["statisticalWindowS"] for run in runs), "CFD_FIXED_TIME_DISCRETIZATION_REQUIRED")
    values = sorted((((number(run["quality"]["totalCellVolumeM3"], positive=True) / run["quality"]["meshCellCount"]) ** (1 / 3)
                      if parameter == "mesh" else number(run["quality"]["physicalTimeStepS"], positive=True),
                      row["stages"]["head"]["result"]["value"]) for run, row in zip(runs, observations)), reverse=True)
    (hc, fc), (hm, fm), (hf, ff) = values
    require(hc > hm > hf and ff > 0, "CFD_DISTINCT_REFINEMENT_LEVELS_REQUIRED")
    coarse, fine, r = fc - fm, fm - ff, hm / hf
    if coarse * fine <= 0 or abs(fine) >= abs(coarse) or not math.isclose(hc / hm, r, rel_tol=0.01):
        return {"status": "ConvergenceNotDemonstrated", "reasonCode": "CFD_MONOTONIC_EQUAL_RATIO_REFINEMENT_REQUIRED", "observations": values}
    require(r > 1, "CFD_DISTINCT_REFINEMENT_LEVELS_REQUIRED")
    quotient = number(abs(coarse / fine), positive=True)
    p = number(math.log(quotient) / math.log(r), positive=True)
    gci = 1.25 * abs(fine / ff) / (quotient - 1)
    return {"status": "CriterionSatisfied" if gci <= study["acceptance"]["numericalGciFractionLimit"] else "RefinementRequired",
            "claimBoundary": "Three-run numerical sensitivity under equal refinement ratios; not experimental or physical CFD validation.",
            "stages": {"observedOrder": stage({"coarseM": fc, "mediumM": fm, "fineM": ff, "r": r},
                                             "p = ln(abs((fc-fm)/(fm-ff)))/ln(r)", f"ln(abs(({fc}-{fm})/({fm}-{ff})))/ln({r})", p, "1"),
                       "fineGci": stage({"mediumM": fm, "fineM": ff, "r": r, "p": p, "safetyFactor": 1.25},
                                        "GCI_fine = 1.25*abs((fm-ff)/ff)/(r^p-1)", f"1.25*abs(({fm}-{ff})/{ff})/({r}^{p}-1)", gci, "1")},
            "sourceEvidence": [row["nativeEvidence"] for row in observations]}


def time_step_stages(study, physical_time_s, dt_s):
    v = study["input"]
    t, dt = number(physical_time_s, nonnegative=True), number(dt_s, positive=True)
    omega = 2 * math.pi * v["speedRpm"] / 60
    return {"kind": "prescribed-kinematic-plan; not a transient solution",
            "omega": stage({"speedRpm": v["speedRpm"]}, "omega = 2*pi*N/60", f"2*pi*{v['speedRpm']}/60", omega, "rad/s"),
            "rotorAngle": stage({"initialAngleRad": v["initialAngleRad"], "omegaRadS": omega, "physicalTimeS": t},
                                "theta_unwrapped = theta0 + omega*t", f"{v['initialAngleRad']} + {omega}*{t}", v["initialAngleRad"] + omega * t, "rad"),
            "angularStep": stage({"omegaRadS": omega, "dtS": dt}, "delta_theta = omega*dt", f"{omega}*{dt}", omega * dt, "rad")}


def verify_restart(study, checkpoint, continuous, restarted, absolute_tolerances):
    require(isinstance(absolute_tolerances, dict), "CFD_RESTART_TOLERANCES_REQUIRED")
    for record in (checkpoint, continuous, restarted):
        bound(study, record)
        require(record["physicalModel"].startswith("transient"), "CFD_PHYSICAL_TRANSIENT_RESTART_REQUIRED")
    t0, t = number(checkpoint.get("physicalTimeS"), nonnegative=True), number(continuous.get("physicalTimeS"), nonnegative=True)
    dt = number(checkpoint.get("physicalTimeStepS"), positive=True)
    require(restarted.get("physicalTimeS") == t and continuous.get("physicalTimeStepS") == restarted.get("physicalTimeStepS") == dt
            and t - t0 >= 60 / study["input"]["speedRpm"] and dt <= 60 / study["input"]["speedRpm"] / study["acceptance"]["minimumStepsPerRevolution"], "CFD_MEANINGFUL_MATCHED_RESTART_WINDOW_REQUIRED")
    require(all(record.get("checkpointSha256") == checkpoint["nativeEvidence"]["sha256"] for record in (continuous, restarted)), "CFD_CHECKPOINT_BINDING_REQUIRED")
    require(all(math.isclose(number(number(record["physicalTimeS"], nonnegative=True) / dt), round(number(record["physicalTimeS"] / dt)), rel_tol=0, abs_tol=1e-8)
                for record in (checkpoint, continuous, restarted)), "CFD_CHECKPOINT_TIME_GRID_REQUIRED")
    count = continuous.get("fieldCellCount")
    require(isinstance(count, int) and not isinstance(count, bool) and 4 <= count <= 1000000 and restarted.get("fieldCellCount") == count
            and continuous.get("fieldLocation") == restarted.get("fieldLocation") == "cell-centers"
            and digest(continuous.get("fieldOrderSha256")) and continuous["fieldOrderSha256"] == restarted.get("fieldOrderSha256"), "CFD_MATCHED_NATIVE_FIELD_ORDER_REQUIRED")
    require(all(isinstance(record.get("fields"), dict) for record in (continuous, restarted)), "CFD_MATCHED_NATIVE_RESTART_FIELDS_REQUIRED")
    results = {}
    for record in (checkpoint, continuous, restarted):
        expected_angle = time_step_stages(study, record["physicalTimeS"], dt)["rotorAngle"]["result"]["value"]
        require(math.isclose(number(record.get("rotorAngleUnwrappedRad")), expected_angle, rel_tol=0, abs_tol=1e-9), "CFD_ROTOR_ANGLE_TIME_MISMATCH")
    for name, unit in (("pAbsolutePa", "Pa"), ("UAbsoluteMS", "m/s"), ("vaporFraction", "1")):
        left, right = continuous["fields"].get(name), restarted["fields"].get(name)
        require(isinstance(left, list) and isinstance(right, list) and len(left) == len(right) == count * (3 if name == "UAbsoluteMS" else 1), "CFD_MATCHED_NATIVE_RESTART_FIELDS_REQUIRED")
        if name == "vaporFraction":
            require(all(0 <= number(x) <= 1 for x in left + right), "CFD_VAPOR_FRACTION_BOUNDEDNESS_REQUIRED")
        differences = [abs(number(a) - number(b)) for a, b in zip(left, right)]
        tolerance = number(absolute_tolerances.get(name), positive=True)
        error = max(differences)
        results[name] = {"criterionSatisfied": error <= tolerance,
                         "stage": stage({"sampleCount": len(left), "absoluteTolerance": tolerance},
                                        "E_inf = max_i(abs(field_continuous_i - field_restarted_i))",
                                        f"max(abs(delta_field_i)), i=1..{len(left)}; compare with {tolerance} {unit}", error, unit)}
    return {"status": "CriterionSatisfied" if all(row["criterionSatisfied"] for row in results.values()) else "RestartMismatch",
            "fields": results, "claimBoundary": "Matched supplied fields after a full rotor revolution; no solver launched or archive-only restart claim."}
