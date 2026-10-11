"""Prepare isolated Foundation-12 phase-change cases from qualified native evidence.

No solver, source script, network transfer or Main calculation is executed.
The baseline, NCC coverage and native observations must be verified upstream.
An authored case does not establish a pump baseline, NPSH3 or a restart result.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_openfoam_case as base
import frozen_rotor_research_protocol as protocol

SCHEMA = "untirta.frozen-rotor-phase-change-preparation.v1"
BASELINE_SCHEMA = "untirta.frozen-rotor-qualified-native-baseline.v1"
NATIVE_REDUCTION_SCHEMA = "untirta.frozen-rotor-native-baseline-reduction.v1"
CHECKPOINT_SCHEMA = "untirta.frozen-rotor-native-checkpoint.v1"
MAX_FILE_BYTES = 128 * 1024**2
MAX_PACKAGE_BYTES = 512 * 1024**2
MESH_FILES = {"points", "faces", "owner", "neighbour", "boundary", "cellZones"}
OPTIONAL_MESH_FILES = {"faceZones", "pointZones"}
INPUT_FILES = {"system/controlDict", "system/fvSchemes", "system/fvSolution",
               "constant/physicalProperties", "constant/momentumTransport", "constant/MRFProperties",
               "0/U", "0/p", "0/k", "0/omega", "0/nut"}
MODES = {"steady-sweep", "transient-sliding"}
MODEL_ASSUMPTIONS = {"vaporDensityKgM3": 0.02308, "vaporNuM2S": 0.0004273,
                     "surfaceTensionNm": 0.07, "nucleiNumberM3": 1.6e13,
                     "nucleiDiameterM": 2e-6, "condensationCoefficient": 1,
                     "vaporizationCoefficient": 1}
REFERENCES = ["https://github.com/OpenFOAM/OpenFOAM-12/tree/master/tutorials/incompressibleVoF/cavitatingBullet",
              "https://github.com/OpenFOAM/OpenFOAM-12/tree/master/tutorials/incompressibleVoF/mixerVessel2DMRF",
              "https://github.com/OpenFOAM/OpenFOAM-12/tree/master/tutorials/incompressibleVoF/propeller"]


class CampaignIssue(ValueError):
    pass


def require(condition, action):
    if not condition:
        raise CampaignIssue(action)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def json_file(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate evidence JSON keys are unsupported.")
            result[key] = value
        return result
    return json.loads(base.read_regular(path, base.inventory.MAX_REPORT_BYTES), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(CampaignIssue("Finite evidence JSON is required.")))


def inside_runner(value):
    directory = base.regular_directory(value)
    import os
    root = base.regular_directory(os.environ.get("RUNNER_TEMP", ""))
    require(bool(os.environ.get("RUNNER_TEMP")) and directory.resolve().is_relative_to(root.resolve()),
            "The source native case must remain inside the independent RUNNER_TEMP.")
    return directory


def relative_path(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value)
            and all(part not in {".", ".."} and not part.startswith(".") for part in value.split("/")),
            "Use an exact safe relative native file path.")
    return value


def native_bytes(path):
    raw = base.read_regular(path, MAX_FILE_BYTES)
    text = raw.decode("ascii")
    # Native generated geometry/state must not contain executable dictionary
    # directives or external include/substitution paths.
    require("FoamFile" in text and not re.search(r"(?m)^\s*#|\bcodeStream\b|\bcodeInclude\b|\bcodeExecute\b", text),
            "Only plain native generated ASCII geometry/state without code or include directives is supported.")
    return raw


def file_records(directory, records, allowed, *, plain_native=False):
    require(isinstance(records, list) and records and len(records) <= 64, "A bounded exact native file inventory is required.")
    seen, total, result = set(), 0, []
    for row in records:
        require(isinstance(row, dict), "Every source record must name actual bytes and SHA256.")
        name = relative_path(row.get("path"))
        require(name in allowed and name not in seen and protocol.digest(row.get("sha256"))
                and isinstance(row.get("bytes"), int) and not isinstance(row["bytes"], bool)
                and 0 < row["bytes"] <= MAX_FILE_BYTES, "Native source file names, sizes and hashes must be exact and supported.")
        for parent in (directory / name).parents:
            if parent == directory:
                break
            require(not parent.is_symlink() and not getattr(parent, "is_junction", lambda: False)(), "Native file paths cannot contain links or junctions.")
        raw = native_bytes(directory / name) if plain_native else base.read_regular(directory / name, MAX_FILE_BYTES)
        require(len(raw) == row["bytes"] and sha256(raw).hexdigest() == row["sha256"], "An actual native source file changed after its evidence binding.")
        total += len(raw)
        require(total <= MAX_PACKAGE_BYTES, "The native source inventory exceeds the independent 512 MiB budget.")
        result.append({"path": name, "bytes": len(raw), "sha256": row["sha256"]})
        seen.add(name)
    return sorted(result, key=lambda row: row["path"])


def verify_baseline(case_directory, packet):
    """Validate files and a supplied upstream-qualified baseline, never execute CFD."""
    if isinstance(packet, dict) and packet.get("schemaVersion") == NATIVE_REDUCTION_SCHEMA:
        return verify_emitted_baseline(case_directory, packet)
    directory = inside_runner(case_directory)
    require(isinstance(packet, dict) and packet.get("schemaVersion") == BASELINE_SCHEMA
            and packet.get("exampleDataCommit") == base.inventory.PIN
            and packet.get("openFoamFoundationVersion") == 12 and packet.get("mainApplicationWrites") is False,
            "An exact independent Foundation-12 source-pinned baseline packet is required.")
    study, observation = packet.get("study"), packet.get("baselineObservation")
    require(isinstance(study, dict) and isinstance(observation, dict), "Supply the study and actual native baseline observation.")
    reduction = protocol.reduce_run(study, observation)
    require(reduction["status"] == "QualifiedSuppliedObservation" and observation["physicalModel"] == "steady-single-phase-MRF"
            and reduction["stages"]["head"]["result"]["value"] > 0
            and protocol.number(observation["measurements"].get("minimumStaticPressureAbsolutePa"), positive=True) > study["input"]["vaporPressurePa"],
            "A qualified positive-head noncavitating single-phase MRF baseline is required first.")
    require(study["physicalBasis"]["gravityInSolver"] is False and study["physicalBasis"]["gravityVectorMS2"] == [0, 0, 0]
            and study["physicalBasis"]["rotationAxis"] == [0, 0, 1] and study["physicalBasis"]["rotationOriginM"] == [0, 0, 0],
            "This adapter supports only the declared full-pump +z MRF and native g=0 classroom adaptation.")
    acceptance = packet.get("nativeAcceptance", {})
    require(isinstance(acceptance, dict) and all(acceptance.get(key) is True for key in
            ("conversionVerified", "physicalWallTypesVerified", "checkMeshVerified", "nonConformalCoverageVerified")),
            "Actual conversion, wall types, checkMesh and coupled coverage must pass upstream before preparation.")
    allowed = INPUT_FILES | {"constant/polyMesh/" + name for name in MESH_FILES | OPTIONAL_MESH_FILES}
    records = file_records(directory, packet.get("sourceCaseFiles"), allowed)
    names = {row["path"] for row in records}
    require(INPUT_FILES | {"constant/polyMesh/" + name for name in MESH_FILES} <= names,
            "The exact native mesh and baseline input dictionaries/fields must all be bound.")
    require(sha256(canonical(records)).hexdigest() == packet.get("sourceCaseManifestSha256")
            == observation["controlledVariationProof"]["nativeInputManifestSha256"],
            "The actual source-case file inventory must match the baseline native input proof.")
    mesh_records = [row for row in records if row["path"].startswith("constant/polyMesh/")]
    require(sha256(canonical(mesh_records)).hexdigest() == observation["controls"]["meshSha256"],
            "The actual source polyMesh inventory must match the baseline mesh signature.")
    for row in mesh_records:
        native_bytes(directory / row["path"])
    sampling = packet.get("samplingFile", {})
    require(isinstance(sampling, dict) and relative_path(sampling.get("path")) == "baseline-sampling.json",
            "Supply the separately persisted actual baseline sampling file.")
    raw = base.read_regular(directory / "baseline-sampling.json", base.inventory.MAX_REPORT_BYTES)
    require(len(raw) == sampling.get("bytes") and sha256(raw).hexdigest() == sampling.get("sha256")
            == observation["nativeEvidence"]["sha256"], "The baseline observation must bind the actual native sampling bytes.")
    return directory, study, observation, records, packet["sourceCaseManifestSha256"]


def verify_emitted_baseline(execution_directory, packet):
    """Consume the actual native reducer's packet and persisted input/evidence bytes."""
    root = inside_runner(execution_directory)
    require(packet.get("qualifiedBaseline") is True and packet.get("preliminaryBaselineQualified") is True
            and packet.get("actualCfdRunVerified") is True and packet.get("mainApplicationWrites") is False,
            "Actual preliminary numerical qualification is required; mesh/time/experimental validation remain separate.")
    study, observation = packet.get("study"), packet.get("nativeObservation")
    require(isinstance(study, dict) and isinstance(observation, dict), "The native reducer must supply its study and actual observation.")
    result = protocol.reduce_run(study, observation)
    require(result["status"] == "QualifiedSuppliedObservation" and observation["physicalModel"] == "steady-single-phase-MRF"
            and result["stages"]["head"]["result"]["value"] > 0
            and protocol.number(observation["measurements"].get("minimumStaticPressureAbsolutePa"), positive=True) > study["input"]["vaporPressurePa"],
            "The actual native baseline must be numerically qualified and noncavitating.")
    require(study["physicalBasis"]["gravityInSolver"] is False and study["physicalBasis"]["gravityVectorMS2"] == [0, 0, 0]
            and study["physicalBasis"]["rotationAxis"] == [0, 0, 1] and study["physicalBasis"]["rotationOriginM"] == [0, 0, 0],
            "This adapter requires the declared g=0 and +z full-pump classroom basis.")
    raw_manifest = base.read_regular(root / "native-input-manifest.json", base.inventory.MAX_REPORT_BYTES)
    manifest = json_file(root / "native-input-manifest.json")
    require(manifest.get("schemaVersion") == "untirta.frozen-rotor-native-input-manifest.v1"
            and manifest == packet.get("manifest", packet.get("inputManifest"))
            and manifest.get("controls") == observation["controls"]
            and manifest.get("physicalBasis") == study["physicalBasis"]
            and manifest.get("studyInputs") == study["input"]
            and sha256(raw_manifest).hexdigest() == observation["controlledVariationProof"]["nativeInputManifestSha256"],
            "The exact persisted native input manifest must bind the reducer observation.")
    allowed_case = INPUT_FILES | {"system/createNonConformalCouplesDict"} | {"constant/polyMesh/" + name for name in MESH_FILES | OPTIONAL_MESH_FILES}
    allowed_master = {"openfoam-preparation.json", "runtime-identity.json", "geometry-identity.json", "remesh-report.json"}
    rows = manifest.get("files")
    require(isinstance(rows, list) and rows and all(isinstance(row, dict) for row in rows), "Actual manifest file records are required.")
    allowed = allowed_master | {"impeller-case/" + name for name in allowed_case}
    verified = file_records(root, [{key: row.get(key) for key in ("path", "bytes", "sha256")} for row in rows], allowed)
    for control in protocol.CONTROL_KEYS - {"cavitationModelSha256"}:
        selected = sorted([{key: row[key] for key in ("path", "bytes", "sha256")} for row in rows if row.get("control") == control], key=lambda row: row["path"])
        require(selected and sha256(canonical(selected)).hexdigest() == observation["controls"][control],
                "Every invariant control group must hash its actual sorted native file records.")
    case_records = [{**row, "path": row["path"].removeprefix("impeller-case/")} for row in verified if row["path"].startswith("impeller-case/")]
    require(INPUT_FILES | {"constant/polyMesh/" + name for name in MESH_FILES} <= {row["path"] for row in case_records},
            "All actual mesh and baseline case inputs must be included in the native manifest.")
    directory = inside_runner(root / "impeller-case")
    for row in case_records:
        if row["path"].startswith("constant/polyMesh/"):
            native_bytes(directory / row["path"])
    preparation = json_file(root / "openfoam-preparation.json")
    require(preparation.get("exampleDataCommit") == base.inventory.PIN and preparation.get("mainApplicationWrites") is False,
            "Preserve the exact official source pin and independent case provenance.")
    execution = json_file(root / "openfoam-execution.json")
    require(all(execution.get(key) is True for key in ("openFoamConversionVerified", "physicalWallTypesVerified", "nativeGeometryQualityVerified", "simulationExecuted", "actualCfdRunVerified"))
            and execution.get("mainApplicationWrites") is False, "The actual execution record must confirm the native conversion and engineering gates upstream.")
    evidence = packet.get("nativeEvidenceRecords")
    require(isinstance(evidence, list) and evidence and all(isinstance(row, dict) for row in evidence), "The actual native sampling/log/final-field evidence inventory is required.")
    evidence_allowed = {relative_path(row.get("path")) for row in evidence}
    require(all(name in {"native-input-manifest.json", "openfoam-execution.json"} or re.fullmatch(r"logs/[0-9]{2}-[A-Za-z]+\.log|impeller-case/(?:500/[A-Za-z]+|postProcessing/[A-Za-z]+/[01]/[A-Za-z]+\.dat)", name)
                for name in evidence_allowed), "Only exact native baseline logs, final fields and numerical sampler tables may bind the observation.")
    verified_evidence = file_records(root, evidence, evidence_allowed)
    require(sha256(canonical(verified_evidence)).hexdigest() == observation["nativeEvidence"]["sha256"],
            "The aggregate actual native evidence bytes must match the sampling SHA256.")
    return directory, study, observation, case_records, sha256(raw_manifest).hexdigest()


def phase_functions():
    """Absolute p is already Pa in VoF; do not add atmospheric pressure twice."""
    blocks = ["totalPressurePa {type pressure; libs (\"libfieldFunctionObjects.so\"); field p; U U; rho rho; calcTotal yes; calcCoeff no; pRef 0; result totalPressurePa; executeControl timeStep; executeInterval 1; writeControl writeTime;}",
              "absoluteVelocitySquared {type magSqr; libs (\"libfieldFunctionObjects.so\"); field U; result U2; executeControl timeStep; executeInterval 1; writeControl writeTime;}",
              "wallYPlus {type yPlus; libs (\"libfieldFunctionObjects.so\"); writeControl writeTime; log true;}"]
    for label, operation, native_field in (("globalPressureMinimum", "min", "p"),
                                         ("globalWaterMinimum", "min", "alpha.water"),
                                         ("globalWaterMaximum", "max", "alpha.water")):
        blocks.append(label + ' {type volFieldValue; libs ("libfieldFunctionObjects.so"); select all; operation ' + operation + '; fields (' + native_field + '); writeFields false; writeLocation false; log false; writeControl timeStep; writeInterval 1;}')
    for suffix, name in (("Inlet", "inlet"), ("Outlet", "mass-flow-inlet-11")):
        common = 'type surfaceFieldValue; libs ("libfieldFunctionObjects.so"); select patch; name ' + name + '; writeFields false; log false; writeControl timeStep; writeInterval 1; '
        blocks += ["massFlow" + suffix + " {" + common + "operation sum; fields (rhoPhi);}",
                   "massFlowMagnitude" + suffix + " {" + common + "operation sumMag; fields (rhoPhi);}",
                   "weighted" + suffix + " {" + common + "operation average; weightField rhoPhi; fields (p totalPressurePa U2 alpha.water);}",
                   "waterMinimum" + suffix + " {" + common + "operation min; fields (alpha.water);}"]
    for suffix, name in (("RotorInterface", "pumpInterface_on_interface-impeller-outlet"),
                         ("StatorInterface", "pumpInterface_on_interface-volute-inlet")):
        common = 'type surfaceFieldValue; libs ("libfieldFunctionObjects.so"); select patch; name ' + name + '; writeFields false; log false; writeControl timeStep; writeInterval 1; '
        blocks.append("massFlow" + suffix + " {" + common + "operation sum; fields (rhoPhi);}")
        blocks.append("massFlowMagnitude" + suffix + " {" + common + "operation sumMag; fields (rhoPhi);}")
    return "\n".join(blocks) + "\n"


def native_recipe(study, pressure_absolute_pa, mode, dt_s, duration_s):
    """Author real native input dictionaries; all numeric values are declarations."""
    require(mode in MODES, "Choose steady-sweep or transient-sliding explicitly.")
    p0 = protocol.number(pressure_absolute_pa, positive=True)
    dt, duration = protocol.number(dt_s, positive=True), protocol.number(duration_s, positive=True)
    v = study["input"]
    require(dt <= 60 / v["speedRpm"] / 360 and 3 * 60 / v["speedRpm"] <= duration <= 10 * 60 / v["speedRpm"],
            "Declare a time step no larger than one rotor degree and a bounded 3–10 revolution window; native Co/alphaCo may require a smaller step.")
    names = set().union(*base.PATCHES.values()) | {"defaultFaces"}
    u, pressure, alpha, k, omega, nut = {}, {}, {}, {}, {}, {}
    q = v["massFlowKgS"] / v["densityKgM3"]
    for name in names:
        alpha[name] = "type zeroGradient;"
        if name == "inlet":
            u[name] = "type pressureInletOutletVelocity; value uniform (0 0 0);"
            pressure[name] = "type prghTotalPressure; p0 uniform " + format(p0, ".17g") + "; value uniform " + format(p0, ".17g") + ";"
            alpha[name] = "type fixedValue; value uniform 1;"
            k[name], omega[name] = "type fixedValue; value uniform 0.375;", "type fixedValue; value uniform 100;"
            nut[name] = "type calculated; value uniform 0;"
        elif name == "mass-flow-inlet-11":
            u[name] = "type flowRateOutletVelocity; volumetricFlowRate " + format(q, ".17g") + "; value uniform (0 0 0);"
            pressure[name] = "type zeroGradient;"
            alpha[name] = "type inletOutlet; inletValue uniform 1; value uniform 1;"
            k[name], omega[name], nut[name] = "type zeroGradient;", "type zeroGradient;", "type calculated; value uniform 0;"
        elif name in base.INTERFACES:
            u[name] = "type movingWallSlipVelocity; value uniform (0 0 0);" if mode == "transient-sliding" else "type slip;"
            pressure[name] = "type fixedFluxPressure;"
            k[name], omega[name], nut[name] = "type zeroGradient;", "type zeroGradient;", "type calculated; value uniform 0;"
        elif name == "defaultFaces":
            u[name], pressure[name], k[name], omega[name], nut[name] = "type zeroGradient;", "type zeroGradient;", "type zeroGradient;", "type zeroGradient;", "type calculated; value uniform 0;"
        else:
            u[name] = ("type movingWallVelocity; value uniform (0 0 0);" if mode == "transient-sliding" else "type MRFnoSlip;") if name in base.ROTATING else "type noSlip;"
            pressure[name] = "type fixedFluxPressure;"
            k[name], omega[name], nut[name] = "type kqRWallFunction; value uniform 0.375;", "type omegaWallFunction; value uniform 100;", "type nutkWallFunction; value uniform 0;"
    f = base.foam_header
    files = {"0/U": base.field("U", "0 1 -1 0 0 0 0", "(0 0 0)", u),
             "0/p_rgh": base.field("p_rgh", "1 -1 -2 0 0 0 0", format(p0, ".17g"), pressure),
             "0/alpha.water": base.field("alpha.water", "0 0 0 0 0 0 0", "1", alpha),
             "0/k": base.field("k", "0 2 -2 0 0 0 0", "0.375", k),
             "0/omega": base.field("omega", "0 0 -1 0 0 0 0", "100", omega),
             "0/nut": base.field("nut", "0 2 -1 0 0 0 0", "0", nut),
             "constant/g": f("g", "uniformDimensionedVectorField", "constant") + "dimensions [0 1 -2 0 0 0 0];\nvalue (0 0 0);\n",
             "constant/phaseProperties": f("phaseProperties", location="constant") + "phases (water vapour);\nsigma 0.07;\n",
             "constant/physicalProperties.water": f("physicalProperties.water", location="constant") + "viscosityModel constant;\nnu " + format(v["viscosityPaS"] / v["densityKgM3"], ".17g") + ";\nrho " + format(v["densityKgM3"], ".17g") + ";\n",
             "constant/physicalProperties.vapour": f("physicalProperties.vapour", location="constant") + "viscosityModel constant;\nnu 0.0004273;\nrho 0.02308;\n",
             "constant/momentumTransport": f("momentumTransport", location="constant") + "simulationType RAS;\nRAS {model kOmegaSST; turbulence on; printCoeffs on;}\n",
             "constant/fvModels": f("fvModels", location="constant") + "VoFCavitation {type VoFCavitation; model SchnerrSauer; SchnerrSauerCoeffs {pSat " + format(v["vaporPressurePa"], ".17g") + "; liquid water; n 1.6e13; dNuc 2e-6; Cc 1; Cv 1;}}\n",
             "system/fvSchemes": f("fvSchemes") + "ddtSchemes {default Euler;}\ngradSchemes {default Gauss linear; grad(U) cellLimited Gauss linear 1;}\ndivSchemes {default none; div(phi,alpha) Gauss noInterfaceCompression vanLeer; div(rhoPhi,U) Gauss linearUpwind grad(U); div(phi,k) Gauss upwind; div(phi,omega) Gauss upwind; div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear;}\nlaplacianSchemes {default Gauss linear limited corrected 0.33;}\ninterpolationSchemes {default linear;}\nsnGradSchemes {default limited corrected 0.33;}\n",
             "system/fvSolution": f("fvSolution") + "solvers {\"alpha.water.*\" {nAlphaCorr 2; nAlphaSubCycles 1; MULESCorr yes; nLimiterIter 5; solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0; maxIter 10; minIter 1;} p_rgh {solver GAMG; smoother DICGaussSeidel; tolerance 1e-9; relTol 0.01; cacheAgglomeration no; maxIter 50;} p_rghFinal {$p_rgh; relTol 0;} \"pcorr.*\" {$p_rgh; tolerance 1e-2; relTol 0;} MeshPhi {solver smoothSolver; smoother symGaussSeidel; tolerance 1e-2; relTol 0;} \"(U|k|omega)\" {solver smoothSolver; smoother symGaussSeidel; tolerance 1e-7; relTol 0.1; minIter 1;} \"(U|k|omega)Final\" {$U; relTol 0;}}\nPIMPLE {correctPhi yes; correctMeshPhi yes; nOuterCorrectors 3; nCorrectors 2; nNonOrthogonalCorrectors 2;}\nrelaxationFactors {equations {\"(U|k|omega).*\" 1;}}\n",
             "system/controlDict": f("controlDict") + "application foamRun;\nsolver incompressibleVoF;\nstartFrom startTime;\nstartTime 0;\nstopAt endTime;\nendTime " + format(duration, ".17g") + ";\ndeltaT " + format(dt, ".17g") + ";\nwriteControl adjustableRunTime;\nwriteInterval " + format(duration / 4, ".17g") + ";\npurgeWrite 2;\nwriteFormat ascii;\nwritePrecision 17;\nwriteCompression off;\ntimeFormat general;\ntimePrecision 12;\nrunTimeModifiable false;\nadjustTimeStep yes;\nmaxCo 0.5;\nmaxAlphaCo 0.25;\nmaxDeltaT " + format(dt, ".17g") + ";\nfunctions\n{\n" + phase_functions() + "}\n"}
    if mode == "steady-sweep":
        files["constant/MRFProperties"] = f("MRFProperties", location="constant") + "rotor {cellZone rotating-fluid; origin (0 0 0); axis (0 0 1); omega " + format(v["speedRpm"], ".17g") + " [rpm];}\n"
    else:
        files["constant/dynamicMeshDict"] = f("dynamicMeshDict", location="constant") + "mover {type motionSolver; libs (\"libfvMotionSolvers.so\"); motionSolver solidBody; cellZone rotating-fluid; solidBodyMotionFunction rotatingMotion; origin (0 0 0); axis (0 0 1); omega " + format(v["speedRpm"], ".17g") + " [rpm];}\n"
    return files


def prepare(case_directory, packet, output_directory, *, pressure_absolute_pa, mode, dt_s, duration_s):
    directory, study, observation, source_files, input_manifest_hash = verify_baseline(case_directory, packet)
    require(protocol.number(pressure_absolute_pa, positive=True) <= observation["pressureLevelAbsolutePa"],
            "Choose an explicit pressure point no higher than the qualified noncavitating baseline; no pressure sweep is selected automatically.")
    files = native_recipe(study, pressure_absolute_pa, mode, dt_s, duration_s)
    destination = Path(output_directory).absolute()
    base.inventory.output_path(destination / "phase-change-preparation.json")
    require(not destination.exists(), "Use a fresh independent RUNNER_TEMP output directory.")
    destination.mkdir(parents=True)
    copied = []
    for row in source_files:
        if not row["path"].startswith("constant/polyMesh/"):
            continue
        raw = native_bytes(directory / row["path"])
        require(sha256(raw).hexdigest() == row["sha256"], "Source geometry changed before copying.")
        base.write_exclusive(destination / "case" / row["path"], raw)
        copied.append(row)
    for name, content in files.items():
        base.write_exclusive(destination / "case" / name, content.encode("ascii"))
    authored = [{"path": name, "bytes": len(content.encode("ascii")), "sha256": sha256(content.encode("ascii")).hexdigest()}
                for name, content in sorted(files.items())]
    phase_model_records = [row for row in authored if row["path"] in {"constant/fvModels", "constant/phaseProperties", "constant/physicalProperties.water", "constant/physicalProperties.vapour"}]
    report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(),
              "status": "PHASE_CHANGE_CASE_PREPARED_NATIVE_EXECUTION_REQUIRED", "mode": mode,
              "exampleDataCommit": base.inventory.PIN, "openFoamFoundationVersion": 12,
              "sourceBaselineEvidenceSha256": observation["nativeEvidence"]["sha256"],
              "sourceBaselineRole": "Conditional single-phase preparation prerequisite and provenance; not automatically the phase-change head reference.",
              "phaseFamilyReferenceRequired": "Execute and qualify a high-pressure active-phase reference with the same authored controls and MRF or sliding family before its pressure sweep; require actual global alpha_v<=1e-4.",
              "sourceCaseManifestSha256": input_manifest_hash, "preservedNativeMeshFiles": copied,
              "declaredStudyInputs": study["input"], "physicalBasis": study["physicalBasis"],
              "authoredInputFiles": authored, "authoredCavitationModelSha256": sha256(canonical(phase_model_records)).hexdigest(),
              "nativeCoordinateUnitsVerified": False, "classroomMetresPerSourceCoordinateUnit": 1,
              "pressurePointSelectedManually": True, "inletAbsoluteTotalPressurePa": pressure_absolute_pa,
              "absolutePressureMapping": {"nativeField": "p_rgh", "nativeUnit": "Pa", "nativeGravityMS2": [0, 0, 0],
                    "equation": "p_abs = p_rgh + rho*gh; native g=0 gives p_abs=p_rgh; no added atmospheric offset.",
                    "referenceAdditionPa": 0, "boundary": "prghTotalPressure p0 is absolute inlet total pressure in Pa"},
              "modelAssumptions": MODEL_ASSUMPTIONS, "modelAssumptionsValidatedForThisPump": False,
              "initialization": "New uniform liquid/zero velocity at t=0; baseline fields are not copied or presented as a phase-change restart.",
              "temporalPlan": {"maximumTimeStepS": dt_s, "endTimeS": duration_s, "rotationPeriodS": 60 / study["input"]["speedRpm"],
                    "stationaryMrfRelaxation": mode == "steady-sweep", "physicalRotorMotion": mode == "transient-sliding",
                    "nativeCoLimit": 0.5, "nativeAlphaCoLimit": 0.25, "timeStepConvergenceVerified": False},
              "nativeCommandArguments": [["checkMesh", "-case", str(destination / "case"), "-allGeometry", "-allTopology"],
                                         ["foamRun", "-case", str(destination / "case")]],
              "nativeExecutionLimits": {"cpus": 1, "memoryBytes": 2*1024**3, "maximumSeconds": 1200,
                    "maximumWorkspaceBytes": MAX_PACKAGE_BYTES, "enforcementOwner": "Independent GitHub workflow; this helper launches no native process."},
              "requiredAcceptance": ["Actual NCC coverage at all rotor angles and finite bounded phase fields",
                    "Signed mass-flow conservation, liquid section cuts, initial residual histories and final-window stationarity/periodicity",
                    "Global noncavitating liquid baseline for the same physical family, followed by an actual bracketed H/H0=0.97 pressure sweep",
                    "Three matched actual mesh/time refinements; qualified full-field native checkpoint and continued/restarted comparison"],
              "samplingDefinition": "Signed-outward rhoPhi mass flux (kg/s), weighted p/pt/U2 and actual global/section alpha.water extrema; alpha_v=1-alpha.water. Total pressure uses native local mixture rho; liquid section cuts must qualify the liquid-density head/NPSHA reduction.",
              "references": REFERENCES, "simulationExecuted": False, "actualCfdRunVerified": False,
              "qualifiedBaseline": False, "npsh3Verified": False, "restartVerified": False, "mainApplicationWrites": False,
              "claimBoundary": "Actual baseline files are hash-bound and qualified upstream; this tool only authors new conditional native cases under declared classroom assumptions."}
    base.write_exclusive(destination / "phase-change-preparation.json", base.inventory.bounded_json(report))
    return report


def verify_checkpoint(case_directory, packet):
    """Hash-bind a complete native checkpoint inventory; never claim restart success."""
    directory = inside_runner(case_directory)
    require(isinstance(packet, dict) and packet.get("schemaVersion") == CHECKPOINT_SCHEMA
            and packet.get("physicalModel") == "transient-cavitation-sliding-mesh"
            and packet.get("upstreamVerified") is True and packet.get("exampleDataCommit") == base.inventory.PIN
            and packet.get("mainApplicationWrites") is False and protocol.digest(packet.get("meshSha256"))
            and protocol.digest(packet.get("nativeCaseManifestSha256")), "A real upstream-verified physical sliding checkpoint is required.")
    t, dt = protocol.number(packet.get("physicalTimeS"), positive=True), protocol.number(packet.get("physicalTimeStepS"), positive=True)
    name = packet.get("nativeTimeDirectory")
    require(isinstance(name, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:e[-+][0-9]+)?", name)
            and math.isclose(float(name), t, rel_tol=0, abs_tol=1e-12), "The exact checkpoint directory must represent its declared physical time.")
    native_time = base.regular_directory(directory / name)
    actual_paths = set()
    for path in native_time.rglob("*"):
        require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)(), "Checkpoint inventories cannot contain links or junctions.")
        if path.is_file():
            relative = relative_path(path.relative_to(directory).as_posix())
            require(len(actual_paths) < 64 and len(path.relative_to(native_time).parts) <= 3, "Native checkpoint inventory must remain bounded.")
            actual_paths.add(relative)
    required = {name + "/" + field for field in ("p", "p_rgh", "U", "alpha.water", "k", "omega", "nut", "phi", "Uf", "rAU", "alphaPhi.water", "polyMesh/points", "uniform/time")}
    require(required <= actual_paths, "Actual moved points, primary fields, phase flux, face velocity and native time state are required for a complete checkpoint.")
    records = file_records(directory, packet.get("files"), actual_paths, plain_native=True)
    require({row["path"] for row in records} == actual_paths and sha256(canonical(records)).hexdigest() == packet.get("checkpointSha256"),
            "Checkpoint hash must bind every actual native file in the exact physical-time directory.")
    require((directory / "constant/dynamicMeshDict").is_file() and not (directory / "constant/MRFProperties").exists(),
            "A physical sliding checkpoint must use native dynamic mesh and exclude double MRF rotation.")
    controls = {"constant/dynamicMeshDict", "constant/phaseProperties", "constant/physicalProperties.water",
                "constant/physicalProperties.vapour", "constant/fvModels", "constant/momentumTransport", "constant/g",
                "system/controlDict", "system/fvSchemes", "system/fvSolution"}
    allowed_inputs = controls | {"constant/polyMesh/" + item for item in MESH_FILES | OPTIONAL_MESH_FILES}
    native_inputs = file_records(directory, packet.get("nativeCaseFiles"), allowed_inputs)
    require(controls | {"constant/polyMesh/" + item for item in MESH_FILES} <= {row["path"] for row in native_inputs}
            and sha256(canonical(native_inputs)).hexdigest() == packet["nativeCaseManifestSha256"],
            "Actual dynamics, phase-change inputs and reference topology must match the checkpoint native case hash.")
    topology = [row for row in native_inputs if row["path"].startswith("constant/polyMesh/")]
    require(sha256(canonical(topology)).hexdigest() == packet["meshSha256"],
            "The checkpoint must bind its actual native reference mesh, separately from moved checkpoint points.")
    return {"checkpointInventoryVerified": True, "checkpointSha256": packet["checkpointSha256"],
            "physicalTimeS": t, "physicalTimeStepS": dt, "fileCount": len(records), "restartVerified": False,
            "claimBoundary": "Actual complete checkpoint bytes and moved points are bound; executing restart and matched-field comparison remain separate."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-case-directory", type=Path, required=True)
    parser.add_argument("--baseline-evidence-json", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--pressure-absolute-pa", type=float, required=True)
    parser.add_argument("--mode", choices=sorted(MODES), required=True)
    parser.add_argument("--time-step-s", type=float, required=True)
    parser.add_argument("--duration-s", type=float, required=True)
    args = parser.parse_args()
    try:
        packet = json_file(args.baseline_evidence_json)
        require(packet.get("schemaVersion") == NATIVE_REDUCTION_SCHEMA,
                "The public CLI accepts only the actual native reducer's qualified packet and original execution directory.")
        result = prepare(args.baseline_case_directory, packet, args.output_directory,
                         pressure_absolute_pa=args.pressure_absolute_pa, mode=args.mode,
                         dt_s=args.time_step_s, duration_s=args.duration_s)
        print(json.dumps({"status": result["status"], "actualCfdRunVerified": False, "mainApplicationWrites": False}, allow_nan=False))
        return 0
    except (CampaignIssue, protocol.ProtocolError, base.CaseIssue, base.inventory.InspectionIssue, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "QUALIFIED_NATIVE_BASELINE_REQUIRED", "reasonCode": "CFD_PHASE_CHANGE_PREPARATION_REQUIRED",
                          "action": str(error)[:500], "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
