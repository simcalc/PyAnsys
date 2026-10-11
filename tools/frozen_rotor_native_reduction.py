"""Read bounded actual Foundation-12 MRF evidence; never launch a native solver.

The input manifest is made before execution. Native samples can establish a
preliminary numerical baseline; wall resolution, refinement and physical
validation remain separate. Synthetic tests are not research evidence.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_openfoam_case as case_tool
import frozen_rotor_research_protocol as protocol

SCHEMA = "untirta.frozen-rotor-native-input-manifest.v1"
ORIGINAL_PREPARATION_SCHEMA = "untirta.frozen-rotor-original-openfoam-preparation.v1"
MAX_RECORD = 128 * 1024
MAX_DATA = 4 * 1024**2
MAX_FIELD = 128 * 1024**2
FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
COUPLED_PATCHES = ("pumpInterface_on_interface-impeller-outlet",
                   "pumpInterface_on_interface-volute-inlet")
SAMPLERS = {"flowInlet": ("inlet", ("sum(phi)",)),
            "flowOutlet": ("mass-flow-inlet-11", ("sum(phi)",)),
            "flowMagnitudeInlet": ("inlet", ("sumMag(phi)",)),
            "flowMagnitudeOutlet": ("mass-flow-inlet-11", ("sumMag(phi)",)),
            "weightedInlet": ("inlet", ("average(p)", "average(totalPressurePa)", "average(U2)")),
            "weightedOutlet": ("mass-flow-inlet-11", ("average(p)", "average(totalPressurePa)", "average(U2)"))}


def require(condition, code):
    if not condition:
        raise protocol.ProtocolError(code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def read(root, relative, limit=MAX_DATA):
    """Only relative regular evidence below this independent directory."""
    pure = PurePosixPath(relative)
    require(not pure.is_absolute() and ".." not in pure.parts and "\\" not in relative,
            "CFD_NATIVE_EVIDENCE_PATH_REQUIRED")
    path = Path(root) / relative
    require(path.absolute().is_relative_to(Path(root).absolute()), "CFD_NATIVE_EVIDENCE_PATH_REQUIRED")
    for item in (path, *path.parents):
        require(not item.is_symlink() and not getattr(item, "is_junction", lambda: False)(), "CFD_REGULAR_NATIVE_EVIDENCE_REQUIRED")
    try:
        raw = case_tool.read_regular(path, limit)
    except (OSError, case_tool.CaseIssue) as error:
        raise protocol.ProtocolError("CFD_NATIVE_EVIDENCE_REQUIRED:" + relative) from error
    return raw, {"path": relative, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def record(root, relative):
    raw, row = read(root, relative, MAX_RECORD)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "CFD_DUPLICATE_EVIDENCE_KEY")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(protocol.ProtocolError("CFD_FINITE_NATIVE_JSON_REQUIRED")))
    require(isinstance(value, dict), "CFD_STRUCTURED_NATIVE_EVIDENCE_REQUIRED")
    return value, row


def binary_identity(identity):
    require(identity.get("schemaVersion") == "untirta.openfoam-native-runtime-identity.v1" and
            identity.get("openFoamFoundationVersion") == "12" and identity.get("packageVersion") == "20250206" and
            identity.get("officialDebSha256") == "b3dcd9a636a5a827bf43ffcf0156922ba6db492123f035eae1046ee759602dd1" and
            identity.get("mainApplicationWrites") is False and isinstance(identity.get("binaries"), list), "CFD_NATIVE_RUNTIME_IDENTITY_REQUIRED")
    binaries = identity["binaries"]
    require({row.get("program") for row in binaries} == {"foamRun", "gmshToFoam", "mergeMeshes", "checkMesh", "createNonConformalCouples", "changeDictionary"}
            and len(binaries) == 6, "CFD_EXACT_NATIVE_BINARY_INVENTORY_REQUIRED")
    for row in binaries:
        path = Path(row.get("path", ""))
        require(str(path).startswith("/opt/openfoam12/") and not path.is_symlink(), "CFD_OFFICIAL_NATIVE_BINARY_REQUIRED")
        try:
            raw = case_tool.read_regular(path, MAX_FIELD)
        except (OSError, case_tool.CaseIssue) as error:
            raise protocol.ProtocolError("CFD_NATIVE_BINARY_IDENTITY_UNVERIFIED") from error
        require(len(raw) == row.get("bytes") and sha256(raw).hexdigest() == row.get("sha256"), "CFD_NATIVE_BINARY_IDENTITY_MISMATCH")


def extract_invariant_inputs(preparation):
    a = preparation.get("classroomAssumptions", {})
    require(a.get("rhoKgM3") == 998.2 and a.get("muPaS") == .001002 and a.get("gMS2") == 9.81
            and a.get("speedRpm") == 1450 and a.get("axis") == [0, 0, 1] and a.get("massFlowKgS") == 90
            and a.get("externalTotalPressureAbsolutePa") == 101325 and a.get("gravityInSolver") is False
            and a.get("metresPerNativeUnit") == 1 and a.get("nativeUnitsVerified") is False,
            "CFD_FIXED_CLASSROOM_PHYSICAL_BASIS_REQUIRED")
    return {"pressureLevelAbsolutePa": 101325.0, "inletElevationM": 0.0, "outletElevationM": 0.0,
            "elevationBasis": "Explicit classroom common zero datum; geometric section centroids were not verified.",
            "coordinateBasis": "One metre per source unit is a declared classroom assumption, not a verified native unit."}


def geometry_identity(remesh):
    """One fixed source boundary geometry, independent of volume mesh/attempt paths."""
    try:
        case_tool.validate_remesh_resource_profile(remesh)
    except case_tool.CaseIssue as error:
        raise protocol.ProtocolError("CFD_EXACT_REMESH_RESOURCE_PROFILE_REQUIRED:" + str(error)) from error
    require(remesh.get("schemaVersion") == "untirta.frozen-rotor-classroom-remesh.v1" and
            remesh.get("exampleDataCommit") == case_tool.inventory.PIN and remesh.get("status") == "VOLUME_MESH_GENERATED" and
            all(remesh.get(key) is True for key in ("volumeMeshGenerated", "originalMeshesImmutable")) and
            all(remesh.get(key) is False for key in ("boundaryRemeshed", "surfaceReparametrizationPerformed", "sourceGeometryFabricated",
                                                   "nodeWeldingPerformed", "topologyHealingPerformed", "simulationExecuted", "actualCfdRunVerified", "mainApplicationWrites")) and
            remesh.get("classroomScaleAssumption", {}).get("nativeUnitsToMetres") == 1 and
            remesh.get("classroomScaleAssumption", {}).get("verifiedFromSource") is False, "CFD_IMMUTABLE_GEOMETRY_ADAPTATION_REQUIRED")
    sources = remesh.get("sources", [])
    require(len(sources) == 2 and {row.get("name") for row in sources} == set(case_tool.inventory.MESH_NAMES), "CFD_OFFICIAL_GEOMETRY_SOURCE_PINS_REQUIRED")
    source_records = []
    for row in sorted(sources, key=lambda value: value["name"]):
        size, digest = case_tool.inventory.FILES[row["name"]]
        require(row.get("bytes") == size and row.get("sha256") == digest, "CFD_OFFICIAL_GEOMETRY_SOURCE_PIN_MISMATCH")
        source_records.append({"name": row["name"], "bytes": size, "sha256": digest})
    meshes = remesh.get("meshes", [])
    require(len(meshes) == 2 and {row.get("sourceMesh") for row in meshes} == set(case_tool.inventory.MESH_NAMES), "CFD_TWO_PRESERVED_COMPONENT_BOUNDARIES_REQUIRED")
    boundaries = []
    for mesh in sorted(meshes, key=lambda row: row["sourceMesh"]):
        proof = mesh.get("sourceSeamPartition", {})
        require(proof.get("completeNativeTriangleCoverageVerified") is True and proof.get("nativeGlobalNodeCoordinatesVerified") is True and
                proof.get("mixedSourceZoneEntityCount") == 0 and proof.get("nearestTagGuessPerformed") is False and
                mesh.get("boundaryRemeshed") is False and mesh.get("surfaceReparametrizationPerformed") is False and
                mesh.get("finalPreservedBoundaryAudit", {}).get("consistentNativeWinding") is True, "CFD_NATIVE_BOUNDARY_EQUALITY_REQUIRED")
        zones = mesh.get("boundaryZones", [])
        component = mesh["sourceMesh"].split(".")[0]
        require({zone.get("sourceZoneName") for zone in zones} == case_tool.PATCHES[component] and
                len(zones) == len(case_tool.PATCHES[component]), "CFD_EXACT_NATIVE_GEOMETRY_ZONE_IDENTITY_REQUIRED")
        zone_records = []
        for zone in sorted(zones, key=lambda row: row["sourceZoneId"]):
            equal = zone.get("boundaryEquality", {})
            require(zone.get("nativeTriangleOwnershipVerifiedBeforeRemesh") is True and
                    equal.get("everyNativeTriangleAndBoundaryNodeChecked") is True and equal.get("maximumCoordinateDifferenceNativeUnits") == 0 and
                    equal.get("nativeWindingPreserved") is True and isinstance(zone.get("sourceZoneId"), int) and zone["sourceZoneId"] > 0 and
                    isinstance(zone.get("sourceTriangleCount"), int) and zone["sourceTriangleCount"] > 0, "CFD_EVERY_NATIVE_FACET_COORDINATE_WINDING_REQUIRED")
            zone_records.append({key: zone[key] for key in ("sourceZoneId", "sourceZoneName", "sourceTriangleCount")})
        require(len({row["sourceZoneId"] for row in zone_records}) == len(zone_records), "CFD_UNIQUE_NATIVE_GEOMETRY_ZONE_REQUIRED")
        boundaries.append({"sourceMesh": mesh["sourceMesh"], "zones": zone_records})
    return {"schemaVersion": "untirta.frozen-rotor-fixed-geometry-identity.v1", "exampleDataCommit": case_tool.inventory.PIN,
            "sources": source_records, "boundaries": boundaries, "nativeUnitsToMetres": 1, "nativeCoordinateUnitsVerified": False,
            "geometryPolicy": "Every native boundary triangle, global node coordinate and winding is unchanged; volume-only tetrahedral refinement.",
            "mainApplicationWrites": False}


def build_manifest(execution_directory):
    """Return {study, manifest}; caller persists it before foamRun. No file writes."""
    root = case_tool.regular_directory(execution_directory)
    preparation, preparation_row = record(root, "openfoam-preparation.json")
    if preparation.get("schemaVersion") == ORIGINAL_PREPARATION_SCHEMA:
        # The original polygon volume has a distinct reviewed provenance adapter.
        # Never represent its four native commands as the tetra converter route.
        import frozen_rotor_original_reduction as original
        return original.build_manifest(root)
    require(preparation.get("schemaVersion") == case_tool.SCHEMA and preparation.get("exampleDataCommit") == case_tool.inventory.PIN
            and protocol.digest(preparation.get("remeshReportSha256")), "CFD_SOURCE_PINNED_PREPARATION_REQUIRED")
    invariant = extract_invariant_inputs(preparation)
    remesh, remesh_row = record(root, "remesh-report.json")
    require(remesh_row["sha256"] == preparation["remeshReportSha256"], "CFD_EXACT_REMESH_REPORT_BINDING_REQUIRED")
    geometry = geometry_identity(remesh)
    geometry_raw = encoded(geometry)
    geometry_row = {"path": "geometry-identity.json", "bytes": len(geometry_raw), "sha256": sha256(geometry_raw).hexdigest()}
    if (root / "geometry-identity.json").exists():
        require(read(root, "geometry-identity.json", MAX_RECORD)[0] == geometry_raw, "CFD_CANONICAL_GEOMETRY_IDENTITY_REQUIRED")
    identity, runtime_row = record(root, "runtime-identity.json")
    binary_identity(identity)
    group_paths = {"geometrySha256": [],
                   "meshSha256": ["impeller-case/constant/polyMesh/" + name for name in
                                  ("points", "faces", "owner", "neighbour", "boundary", "cellZones")],
                   "boundarySha256": ["impeller-case/0/" + name for name in ("U", "p", "k", "omega", "nut")] +
                                     ["impeller-case/constant/" + name for name in ("MRFProperties", "physicalProperties", "momentumTransport")],
                   "discretizationSha256": ["impeller-case/system/" + name for name in ("fvSchemes", "fvSolution", "controlDict", "createNonConformalCouplesDict")],
                   "runtimeSha256": ["runtime-identity.json"]}
    files = [{**preparation_row, "control": None}, {**remesh_row, "control": None}, {**geometry_row, "control": "geometrySha256"}]
    controls = {"cavitationModelSha256": None}
    authored = case_tool.case_files("impeller")
    for key, paths in group_paths.items():
        rows = [geometry_row] if key == "geometrySha256" else []
        for relative in sorted(paths):
            raw, row = read(root, relative, MAX_FIELD)
            local = relative.removeprefix("impeller-case/")
            if local in authored and not local.startswith("0/"):
                require(raw == authored[local].encode("utf-8"), "CFD_AUTHORED_NATIVE_INPUT_MISMATCH:" + local)
            elif local in {"0/p", "0/U"}:
                text = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw.decode("ascii"), flags=re.S)
                if local == "0/p":
                    body = re.search(r'"?inlet"?\s*\{([^{}]*)\}', text)
                    require(body and re.search(r"type\s+totalPressure\s*;", body[1]) and
                            re.search(r"p0\s+uniform\s+0(?:\.0*)?\s*;", body[1]), "CFD_NATIVE_ABSOLUTE_PRESSURE_BC_REQUIRED")
                else:
                    body = re.search(r'"?mass-flow-inlet-11"?\s*\{([^{}]*)\}', text)
                    flow = re.search(r"volumetricFlowRate\s+(?:constant\s+)?(" + FLOAT + r")\s*;", body[1] if body else "")
                    require(body and re.search(r"type\s+flowRateOutletVelocity\s*;", body[1]) and flow and
                            math.isclose(float(flow[1]), 90/998.2, rel_tol=1e-12), "CFD_NATIVE_FIXED_FLOW_BC_REQUIRED")
            rows.append(row)
            files.append({**row, "control": key})
        controls[key] = sha256(encoded(rows)).hexdigest()
    study = protocol.prepare_study(controls=controls, gravity_in_solver=False)
    manifest = {"schemaVersion": SCHEMA, "studyInputs": study["input"], "controls": controls,
                "signatureScope": study["signatureScope"], "samplingDefinition": study["samplingDefinition"],
                "physicalBasis": study["physicalBasis"], "physicalModel": "steady-single-phase-MRF",
                "files": files, "preparationReportSha256": preparation_row["sha256"],
                "runtimeIdentitySha256": runtime_row["sha256"], **invariant,
                "classroomAcceptance": {"samplingWindowIterations": 20, "continuityLocalLimit": 1e-4,
                                        "interfaceMinimumCoverage": .99, "interfaceAverageCoverage": .999,
                                        "interfaceMaximumCoverage": 1.01, "interfaceFluxRelativeLimit": .005},
                "claimBoundary": "Exact fixed baseline content binding; future pressure or time variation needs a separately reviewed invariant-input adapter.",
                "simulationExecuted": False, "mainApplicationWrites": False}
    return {"study": study, "manifest": manifest, "geometryIdentity": geometry}


def parse_table(text, columns, patch=None):
    """Foundation12 fieldValue headers, scalar columns and strict increasing indexes."""
    headers = re.findall(r"(?m)^#\s*Time\s+(.+)$", text)
    require(len(headers) == 1 and tuple(headers[0].split()) == tuple(columns), "CFD_NATIVE_SAMPLING_HEADER_MISMATCH")
    if patch is not None:
        require(re.search(r"(?m)^#\s*Selection\s*:\s*patch\(" + re.escape(patch) + r"\)\s*$", text), "CFD_NATIVE_SAMPLING_SECTION_MISMATCH")
        require(re.search(r"(?m)^#\s*Faces\s*:\s*[1-9]\d*\s*$", text) and
                re.search(r"(?m)^#\s*Area\s*:\s*(" + FLOAT + r")\s*$", text), "CFD_NONEMPTY_NATIVE_SECTION_REQUIRED")
        area = float(re.search(r"(?m)^#\s*Area\s*:\s*(" + FLOAT + r")\s*$", text)[1])
        protocol.number(area, positive=True)
    rows = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        require(len(parts) == len(columns) + 1 and all(re.fullmatch(FLOAT, x) for x in parts), "CFD_FINITE_NATIVE_SAMPLING_ROW_REQUIRED")
        values = [protocol.number(float(x)) for x in parts]
        index = values.pop(0)
        require(index >= 0 and index.is_integer() and (not rows or index > next(reversed(rows))), "CFD_ORDERED_STEADY_ITERATIONS_REQUIRED")
        rows[int(index)] = values
    require(rows, "CFD_NATIVE_SAMPLING_ROWS_REQUIRED")
    return rows


def parse_solver_log(text):
    require(re.search(r"(?m)^End\s*$", text) and not re.search(r"FOAM FATAL|Segmentation fault|Floating point exception", text), "CFD_COMPLETED_NATIVE_SOLVER_REQUIRED")
    rows, current = {}, None
    for line in text.splitlines():
        # Foundation12 real userTime appends 's'; steady iteration is still not physical time.
        match = re.fullmatch(r"Time = (\d+)(?:s)?", line.strip())
        require(not line.strip().startswith("Time = ") or match, "CFD_FIXED_REAL_TIME_ITERATION_FORMAT_REQUIRED")
        if match:
            current = int(match[1])
            require(current not in rows and (not rows or current > next(reversed(rows))), "CFD_ORDERED_NATIVE_OUTER_ITERATIONS_REQUIRED")
            rows[current] = {"residuals": {}, "continuity": []}
        if current is None:
            continue
        match = re.search(r"Solving for (p|Ux|Uy|Uz|k|omega), Initial residual = (" + FLOAT + r"), Final residual = (" + FLOAT + r"), No Iterations (\d+)", line)
        if match:
            initial, final = protocol.number(float(match[2]), nonnegative=True), protocol.number(float(match[3]), nonnegative=True)
            require(initial <= 1 and final <= 1, "CFD_FINITE_NORMALIZED_RESIDUAL_REQUIRED")
            # First p solve captures nonlinear outer convergence; later pressure corrections do not replace it.
            rows[current]["residuals"].setdefault(match[1], initial)
        elif "Solving for" in line:
            require(not re.search(r"Solving for (p|Ux|Uy|Uz|k|omega),", line), "CFD_NATIVE_RESIDUAL_RECORD_REQUIRED")
        match = re.search(r"time step continuity errors : sum local = (" + FLOAT + r"), global = (" + FLOAT + r")(?:, cumulative = (" + FLOAT + r"))?", line)
        if match:
            values = [protocol.number(float(value)) for value in match.groups() if value is not None]
            require(values[0] >= 0, "CFD_NONNEGATIVE_NATIVE_LOCAL_CONTINUITY_REQUIRED")
            rows[current]["continuity"].append(values)
    require(rows, "CFD_NATIVE_NONLINEAR_HISTORY_REQUIRED")
    return rows


def sampler(root, name, filename, columns, patch=None):
    """Native logFiles uses the time at first write (0 or 1), not always zero."""
    relative = "impeller-case/postProcessing/" + name
    directory = Path(root) / relative
    try:
        children = list(directory.iterdir())
    except OSError as error:
        raise protocol.ProtocolError("CFD_NATIVE_SAMPLER_REQUIRED:" + name) from error
    require(len(children) == 1 and children[0].name in {"0", "1"} and children[0].is_dir(),
            "CFD_ONE_FRESH_NATIVE_SAMPLER_DIRECTORY_REQUIRED")
    raw, row = read(root, relative + "/" + children[0].name + "/" + filename)
    return parse_table(raw.decode("utf-8"), columns, patch), row


def parse_coverage(text):
    result = []
    pattern = r"Source min/average/max coverage = (" + FLOAT + r")/(" + FLOAT + r")/(" + FLOAT + r")\s+Target min/average/max coverage = (" + FLOAT + r")/(" + FLOAT + r")/(" + FLOAT + r")"
    for match in re.finditer(pattern, text):
        values = [protocol.number(float(x), nonnegative=True) for x in match.groups()]
        require(values[0] <= values[1] <= values[2] and values[3] <= values[4] <= values[5], "CFD_ORDERED_NCC_COVERAGE_REQUIRED")
        result.append({"source": values[:3], "target": values[3:]})
    require(result, "CFD_ACTUAL_NCC_COVERAGE_REQUIRED")
    return result


def parse_mesh_quality(text):
    require("Mesh OK." in text and not re.search(r"Failed [1-9]\d* mesh checks|FOAM FATAL", text), "CFD_NATIVE_CHECKMESH_REQUIRED")
    volume = re.search(r"Min volume = (" + FLOAT + r")\.\s+Max volume = (" + FLOAT + r")\.\s+Total volume = (" + FLOAT + r")\.", text)
    cells = re.search(r"(?m)^\s*cells:\s*(\d+)\s*$", text)
    require(volume and cells and int(cells[1]) > 0, "CFD_ACTUAL_NATIVE_VOLUME_COUNTS_REQUIRED")
    values = [protocol.number(float(x), positive=True) for x in volume.groups()]
    require(values[0] <= values[1] <= values[2], "CFD_POSITIVE_NATIVE_CELL_VOLUMES_REQUIRED")
    return {"minimumCellVolumeM3": values[0], "meshCellCount": int(cells[1]), "totalCellVolumeM3": values[2]}


def finite_field(raw, name, cell_count):
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw.decode("ascii"), flags=re.S)
    dimensions = {"p": "0 2 -2 0 0 0 0", "U": "0 1 -1 0 0 0 0", "k": "0 2 -2 0 0 0 0", "omega": "0 0 -1 0 0 0 0"}
    require(re.search(r"\bformat\s+ascii\s*;", text) and re.search(r"\bobject\s+" + name + r"\s*;", text)
            and re.search(r"\bclass\s+" + ("volVectorField" if name == "U" else "volScalarField") + r"\s*;", text)
            and re.search(r"\bdimensions\s*\[\s*" + dimensions[name] + r"\s*\]\s*;", text)
            and not re.search(r"\b(?:nan|inf|infinity)\b", text, re.I), "CFD_FINITE_NATIVE_FIELD_REQUIRED")
    match = re.search(r"\binternalField\s+(uniform\s+([^;]+)|nonuniform\s+List<(scalar|vector)>\s+(\d+)\s*\((.*?)\))\s*;", text, re.S)
    require(match, "CFD_NATIVE_INTERNAL_FIELD_REQUIRED")
    vector = name == "U"
    values = match[2] if match[2] is not None else match[5]
    if match[2] is None:
        require(int(match[4]) == cell_count and match[3] == ("vector" if vector else "scalar"), "CFD_CURRENT_NATIVE_CELL_FIELD_COUNT_REQUIRED")
    tokens = re.sub(r"[()]", " ", values).split()
    expected = (3 if vector else 1) * (1 if match[2] is not None else cell_count)
    require(len(tokens) == expected and all(re.fullmatch(FLOAT, token) for token in tokens), "CFD_COMPLETE_NATIVE_FIELD_VALUES_REQUIRED")
    values = [protocol.number(float(token), nonnegative=name in {"k", "omega"}) for token in tokens]
    return {"name": name, "internalValueCount": len(values), "minimum": min(values), "maximum": max(values)}


def reduce_native_baseline(execution_directory, study=None):
    """Return independent stages and pending diagnostics when actual evidence is absent."""
    root = Path(execution_directory)
    output = {"schemaVersion": "untirta.frozen-rotor-native-baseline-reduction.v1", "status": "NativeEvidenceRequired",
              "qualifiedBaseline": False, "preliminaryBaselineQualified": False, "wallResolutionVerified": False,
              "qualificationScope": "Preliminary numerical baseline on the actual chosen mesh and declared classroom assumptions; no mesh-independent or physically validated performance claim.",
              "meshConvergenceVerified": False, "timeStepConvergenceVerified": False, "physicalValidationVerified": False,
              "actualCfdRunVerified": False, "mainApplicationWrites": False, "stages": {}, "diagnostics": [],
              "interfaceCoverageVerified": False,
              "claimBoundary": "Read-only preliminary reduction; wall resolution, mesh/time refinement and physical validation remain separate."}
    try:
        manifest, manifest_row = record(root, "native-input-manifest.json")
        read(root, "geometry-identity.json", MAX_RECORD)
        current = build_manifest(root)
        require(manifest == current["manifest"] and manifest.get("schemaVersion") == SCHEMA, "CFD_CURRENT_NATIVE_INPUT_MANIFEST_REQUIRED")
        native_study = current["study"]
        require(study is None or study == native_study, "CFD_NATIVE_STUDY_BINDING_MISMATCH")
        study = native_study
        output.update(study=study, inputManifest=manifest, manifest=manifest)
        execution, execution_row = record(root, "openfoam-execution.json")
        preparation = record(root, "openfoam-preparation.json")[0]
        original_route = preparation.get("schemaVersion") == ORIGINAL_PREPARATION_SCHEMA
        execution_schema = ("untirta.frozen-rotor-original-openfoam-execution.v1" if original_route else
                            "untirta.frozen-rotor-openfoam-execution.v1")
        execution_status = "ORIGINAL_MRF_EXECUTED_ACCEPTANCE_REQUIRED" if original_route else "MRF_EXECUTED_ACCEPTANCE_REQUIRED"
        require(execution.get("schemaVersion") == execution_schema and
                execution.get("status") == execution_status and execution.get("actualCfdRunVerified") is True
                and execution.get("simulationExecuted") is True and execution.get("openFoamConversionVerified") is True
                and execution.get("nativeGeometryQualityVerified") is True and execution.get("physicalWallTypesVerified") is True
                and execution.get("mainApplicationWrites") is False and
                execution.get("preparationReportSha256") == manifest["preparationReportSha256"], "CFD_ACTUAL_NATIVE_EXECUTION_REQUIRED")
        if original_route:
            require(manifest.get("sourceGeometryRoute") == "original-volume-polyMesh-transfer" and
                    execution.get("nativeConversionReportSha256") == manifest.get("nativeConversionReportSha256") and
                    all(execution.get(key) is True for key in ("nativeSolverCompleted", "finiteNativeFieldsVerified", "inputArtifactsImmutable")),
                    "CFD_ORIGINAL_NATIVE_COMPLETION_BINDING_REQUIRED")
        stages = execution.get("stages", [])
        expected = (["mergeMeshes", "createNonConformalCouples", "checkMesh", "foamRun"] if original_route else
                    ["gmshToFoam", "changeDictionary", "gmshToFoam", "changeDictionary", "mergeMeshes", "createNonConformalCouples", "checkMesh", "foamRun"])
        require([row.get("program") for row in stages] == expected and all(row.get("exitCode") == 0 for row in stages), "CFD_COMPLETE_NATIVE_EXECUTION_STAGES_REQUIRED")
        records, logs = [manifest_row, execution_row], {}
        for index, row in enumerate(stages):
            require(row.get("log") == f"{index:02d}-{row['program']}.log", "CFD_EXACT_NATIVE_LOG_ROLE_REQUIRED")
            raw, metadata = read(root, "logs/" + row["log"])
            require(len(raw) == row.get("logBytes") and metadata["sha256"] == row.get("logSha256"), "CFD_NATIVE_LOG_BINDING_MISMATCH")
            text = raw.decode("utf-8")
            require(re.search(r"(?m)^End\s*$", text) and not re.search(r"FOAM FATAL|Segmentation fault|Floating point exception", text), "CFD_COMPLETED_NATIVE_LOG_REQUIRED")
            logs[row["program"]] = text
            records.append(metadata)
        mesh = parse_mesh_quality(logs["checkMesh"])
        expected_cells = sum(row["cellCount"] for row in record(root, "openfoam-preparation.json")[0]["components"])
        require(mesh["meshCellCount"] == expected_cells, "CFD_EXACT_NATIVE_MESH_CELL_COUNT_REQUIRED")
        fields = execution.get("finalNativeFields", [])
        require({row.get("name") for row in fields} == {"p", "U", "k", "omega"} and len(fields) == 4, "CFD_CURRENT_NATIVE_FIELD_INVENTORY_REQUIRED")
        output["finiteNativeFields"] = []
        for row in fields:
            raw, metadata = read(root, "impeller-case/500/" + row["name"], MAX_FIELD)
            require(metadata["bytes"] == row.get("bytes") and metadata["sha256"] == row.get("sha256"), "CFD_CURRENT_NATIVE_FIELD_BINDING_MISMATCH")
            output["finiteNativeFields"].append(finite_field(raw, row["name"], mesh["meshCellCount"]))
            records.append(metadata)
        output["actualCfdRunVerified"] = True
        # The valid inlet branch can still be shown if an independent outlet or
        # conservation sampler is missing. This remains a native-sample reduction,
        # never a substitution from an old or synthetic result.
        inlet_table, inlet_record = sampler(root, "weightedInlet", "surfaceFieldValue.dat", SAMPLERS["weightedInlet"][1], "inlet")
        window_size = manifest["classroomAcceptance"]["samplingWindowIterations"]
        require(isinstance(window_size, int) and window_size >= 5, "CFD_DECLARED_FINAL_WINDOW_REQUIRED")
        window = list(range(501-window_size, 501))
        require(all(i in inlet_table for i in window), "CFD_CURRENT_COMPLETE_INLET_WINDOW_REQUIRED")
        rho, g = study["input"]["densityKgM3"], study["input"]["gravityMS2"]
        pressure_ref, zi = manifest["pressureLevelAbsolutePa"], manifest["inletElevationM"]
        for p, pt, u2 in (inlet_table[i] for i in window):
            require(u2 >= 0 and math.isclose(pt, rho*p+rho*u2/2+pressure_ref, rel_tol=2e-5, abs_tol=2), "CFD_INLET_TOTAL_PRESSURE_DEFINITION_MISMATCH")
        pi = math.fsum(inlet_table[i][0] for i in window)/len(window)
        u2i = math.fsum(inlet_table[i][2] for i in window)/len(window)
        ps, pv, datum = rho*pi+pressure_ref, study["input"]["vaporPressurePa"], study["input"]["suctionDatumM"]
        protocol.number(ps, positive=True)
        npsha = (ps-pv)/(rho*g)+u2i/(2*g)+zi-datum
        output["stages"]["npsha"] = protocol.stage(
            {"pStaticAbsolutePa": ps, "pvPa": pv, "meanU2M2S2": u2i, "rhoKgM3": rho, "gMS2": g, "zInM": zi, "zDatumM": datum},
            "NPSHA = (ps_abs - pv)/(rho*g) + mean(U_absolute^2)/(2*g) + z_in - z_datum",
            f"({ps}-{pv})/({rho}*{g})+{u2i}/(2*{g})+{zi}-{datum}", npsha, "m")
        output["npshaDomainStatus"] = "FLASHING_CAVITATION_RISK" if npsha <= 0 or ps <= pv else "NotAssessedByNPSHRComparison"
        output["independentInletEvidence"] = inlet_record
        tables = {}
        for name, (patch, columns) in SAMPLERS.items():
            tables[name], metadata = sampler(root, name, "surfaceFieldValue.dat", columns, patch)
            records.append(metadata)
        history = parse_solver_log(logs["foamRun"])
        require(max(history) == 500 and all(all(index in table for index in window) for table in [history, *tables.values()]), "CFD_CURRENT_COMPLETE_FINAL_WINDOW_REQUIRED")
        rho, g = study["input"]["densityKgM3"], study["input"]["gravityMS2"]
        pressure_ref = manifest["pressureLevelAbsolutePa"]
        zi, zo = manifest["inletElevationM"], manifest["outletElevationM"]
        heads, inlets, outlets, reverse, residuals, continuity = [], [], [], [], {name: [] for name in ("p", "U", "k", "omega")}, []
        for index in window:
            pi, pti, u2i = tables["weightedInlet"][index]
            po, pto, u2o = tables["weightedOutlet"][index]
            for p, pt, u2 in ((pi, pti, u2i), (po, pto, u2o)):
                require(u2 >= 0 and math.isclose(pt, rho*p + rho*u2/2 + pressure_ref, rel_tol=2e-5, abs_tol=2), "CFD_TOTAL_PRESSURE_DEFINITION_MISMATCH")
            fi, fo = tables["flowInlet"][index][0], tables["flowOutlet"][index][0]
            mi, mo = tables["flowMagnitudeInlet"][index][0], tables["flowMagnitudeOutlet"][index][0]
            require(fi < 0 < fo and mi >= abs(fi)-1e-10 and mo >= abs(fo)-1e-10, "CFD_SIGNED_NATIVE_FLUX_REQUIRED")
            reverse.append(max(max(0, (mi+fi)/2)/mi, max(0, (mo-fo)/2)/mo))
            inlets.append(rho*fi); outlets.append(rho*fo)
            heads.append((pto-pti)/(rho*g)+zo-zi)
            r = history[index]["residuals"]
            require({"p", "Ux", "Uy", "Uz", "k", "omega"} <= set(r) and history[index]["continuity"], "CFD_COMPLETE_NATIVE_NONLINEAR_HISTORY_REQUIRED")
            for name in residuals:
                residuals[name].append(max(r[axis] for axis in ("Ux", "Uy", "Uz")) if name == "U" else r[name])
            continuity.append(history[index]["continuity"][-1])
        mean = lambda rows: math.fsum(rows)/len(rows)
        pi, pti, u2i = [mean([tables["weightedInlet"][i][column] for i in window]) for column in range(3)]
        pto = mean([tables["weightedOutlet"][i][1] for i in window])
        evidence_hash = sha256(encoded(sorted(records, key=lambda row: row["path"]))).hexdigest()
        run = {"studyInputs": study["input"], "controls": manifest["controls"], "samplingDefinition": study["samplingDefinition"],
               "physicalBasis": study["physicalBasis"], "physicalModel": "steady-single-phase-MRF", "runRevision": "Current",
               "pressureLevelAbsolutePa": pressure_ref, "nativeEvidence": {"kind": "native-solver-sampling", "upstreamVerified": True,
                    "sha256": evidence_hash, "runId": execution.get("githubRunId") or execution_row["sha256"]},
               "controlledVariationProof": {"upstreamVerified": True, "nativeInputManifestSha256": manifest_row["sha256"],
                    "samplingEvidenceSha256": evidence_hash, "structuralControls": manifest["controls"], "signatureScope": study["signatureScope"],
                    "physicalModel": "steady-single-phase-MRF", "pressureBoundaryDefinition": study["controlledPressureDefinition"],
                    "pressureLevelAbsolutePa": pressure_ref, "physicalTimeStepS": None},
               "pressureMapping": {"nativeField": "p", "nativeUnit": "m2/s2", "absolutePressureReferencePa": pressure_ref,
                    "absoluteReconstructionEquation": "p_static_absolute_Pa = rho_liquid*p_kinematic + explicit_absolute_reference_Pa"},
               "measurements": {"inletTotalPressureAbsolutePa": pti, "outletTotalPressureAbsolutePa": pto,
                    "inletStaticPressureAbsolutePa": rho*pi+pressure_ref, "inletMeanSquaredAbsoluteSpeedM2S2": u2i,
                    "inletElevationM": zi, "outletElevationM": zo},
               "quality": {"nativeMeshQualityChecked": True, **mesh, "outwardInletMassFlowKgS": mean(inlets), "outwardOutletMassFlowKgS": mean(outlets),
                    "reverseFlowFraction": max(reverse), "finalResidualHistory": residuals,
                    "residualDefinition": "initial-solver-residual-per-outer-iteration-final-window", "samplingWindowDefinition": "final-converged-window",
                    "finalFlowRelativeSpan": max((max(v)-min(v))/abs(mean(v)) for v in (inlets, outlets)), "headHistoryM": heads}}
        reduction = protocol.reduce_run(study, run)
        output.update(stages=reduction["stages"], protocolReduction=reduction, nativeObservation=run,
                      samplingIterationWindow=[window[0], window[-1]], iterationIndexIsPhysicalTime=False,
                      continuityHistory=continuity, nativeEvidenceRecords=records)
        output["diagnostics"].extend(reduction["diagnostics"])
        require(reduction.get("npshaDomainStatus") != "FLASHING_CAVITATION_RISK" and
                reduction.get("stages", {}).get("head", {}).get("result", {}).get("value", 0) > 0,
                "CFD_POSITIVE_HEAD_LIQUID_BASELINE_REQUIRED")
        require(max(row[0] for row in continuity) <= manifest["classroomAcceptance"]["continuityLocalLimit"], "CFD_NATIVE_CONTINUITY_CONVERGENCE_REQUIRED")
        # This is a local finite-volume divergence metric, not boundary mass balance.
        output["continuityDefinition"] = "deltaIteration*volumeWeightedMean(abs(div(phi))); independent of boundary mass-flow balance"
        coverage = parse_coverage(logs["checkMesh"] + "\n" + logs["foamRun"])
        output["nativeCoupledCoverage"] = coverage
        limits = manifest["classroomAcceptance"]
        require(all(values[0] >= limits["interfaceMinimumCoverage"] and values[1] >= limits["interfaceAverageCoverage"] and values[2] <= limits["interfaceMaximumCoverage"]
                    for row in coverage for values in row.values()), "CFD_NCC_INTERFACE_COVERAGE_NOT_SATISFIED")
        # Global p minimum and paired interface flux must be actually sampled.
        minimum, minimum_row = sampler(root, "globalPressureMinimum", "volFieldValue.dat", ("min(p)",))
        require(all(i in minimum for i in window), "CFD_GLOBAL_PRESSURE_WINDOW_REQUIRED")
        minimum_absolute = min(rho*minimum[i][0]+pressure_ref for i in window)
        run["measurements"]["minimumStaticPressureAbsolutePa"] = minimum_absolute
        output["minimumStaticPressureAbsolutePa"] = minimum_absolute
        records.append(minimum_row)
        interface = []
        for name, patch in zip(("flowRotorInterface", "flowStatorInterface"), COUPLED_PATCHES):
            table, row = sampler(root, name, "surfaceFieldValue.dat", ("sum(phi)",), patch)
            require(all(i in table for i in window), "CFD_INTERFACE_FLUX_WINDOW_REQUIRED")
            interface.append(table); records.append(row)
        mismatch = max(abs(interface[0][i][0]+interface[1][i][0])/max(abs(interface[0][i][0]), abs(interface[1][i][0]), 1e-30) for i in window)
        require(all(interface[0][i][0] > 0 > interface[1][i][0] for i in window) and mismatch <= limits["interfaceFluxRelativeLimit"], "CFD_ACTUAL_INTERFACE_FLUX_CONSERVATION_REQUIRED")
        output["interfaceFluxRelativeMismatch"] = mismatch
        output["interfaceCoverageVerified"] = True
        run["quality"]["coupledInterfaceCoverageVerified"] = True
        output["noncavitatingBaselineVerified"] = minimum_absolute > study["input"]["vaporPressurePa"]
        require(output["noncavitatingBaselineVerified"], "CFD_NONCAVITATING_BASELINE_REQUIRED")
        final_evidence_hash = sha256(encoded(sorted(records, key=lambda row: row["path"]))).hexdigest()
        run["nativeEvidence"]["sha256"] = final_evidence_hash
        run["controlledVariationProof"]["samplingEvidenceSha256"] = final_evidence_hash
        # Re-project the exact final evidence binding after all acceptance samplers were read.
        output["protocolReduction"] = protocol.reduce_run(study, run)
        output["preliminaryBaselineQualified"] = reduction["status"] == "QualifiedSuppliedObservation"
        output["qualifiedBaseline"] = output["preliminaryBaselineQualified"]
        output["status"] = "PreliminaryBaselineQualified" if output["preliminaryBaselineQualified"] else "NumericalAcceptanceRequired"
    except (protocol.ProtocolError, OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
        output["diagnostics"].append({"branch": "native-baseline-qualification", "reasonCode": str(error)[:500]})
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-directory", type=Path, required=True)
    args = parser.parse_args()
    result = reduce_native_baseline(args.execution_directory)
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0 if result["preliminaryBaselineQualified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
