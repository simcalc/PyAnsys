"""Bind the original polygon-volume route before the shared native reduction.

No remesh report is invented, no solver is launched, and conversion evidence
does not stand in for actual OpenFOAM checkMesh or numerical qualification.
"""
from __future__ import annotations
from hashlib import sha256
import math
from pathlib import Path
import re

import frozen_rotor_native_reduction as native
import frozen_rotor_original_case as original

SCHEMA = original.SCHEMA
CONVERSION_SCHEMA = original.CONVERSION_SCHEMA
GEOMETRY_ROUTE = "original-volume-polyMesh-transfer"
MESH_FILES = ("points", "faces", "owner", "neighbour", "boundary", "cellZones")


def positive_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def geometry_identity(conversion):
    """Identity of exact native polygons and nodes, with explicit owner winding."""
    native.require(conversion.get("schemaVersion") == CONVERSION_SCHEMA and
                   conversion.get("exampleDataCommit") == native.case_tool.inventory.PIN and
                   conversion.get("originalMeshesImmutable") is True and conversion.get("volumeMeshConverted") is True and
                   all(conversion.get(key) is False for key in ("nativeCoordinateUnitsVerified", "meshQualityCheckedBySolver",
                       "nativeCaseGenerated", "simulationExecuted", "actualCfdRunVerified", "mainApplicationWrites")) and
                   conversion.get("classroomMetresPerSourceCoordinateUnit") == 1,
                   "CFD_EXACT_ORIGINAL_VOLUME_CONVERSION_REQUIRED")
    components = conversion.get("components", [])
    native.require(isinstance(components, list) and [row.get("name") for row in components] == ["impeller", "volute"],
                   "CFD_EXACT_ORIGINAL_COMPONENTS_REQUIRED")
    sources, boundaries = [], []
    for row in components:
        component = row["name"]
        source_name = component + ".msh.h5"
        size, digest = native.case_tool.inventory.FILES[source_name]
        source = {"name": source_name, "bytes": size, "sha256": digest,
                  "sourceUrl": native.case_tool.inventory.mesh_url(source_name)}
        native.require(row.get("sourceFile") == source and row.get("casePath") == component + "-case" and
                       tuple(row.get(key) for key in ("cellCount", "nodeCount", "faceCount")) == original.EXPECTED_COUNTS[component],
                       "CFD_OFFICIAL_ORIGINAL_SOURCE_PIN_COUNTS_REQUIRED")
        checks = row.get("checks", {})
        native.require(all(checks.get(key) is True for key in ("allOriginalCellsChecked",
                       "directedEdgeIncidenceClosed", "standardCellShapeIncidenceVerified", "allCoordinatesAsciiRoundTripVerified",
                       "everyOriginalFaceTransferredExactlyOnce", "originalPolygonsRetained", "ownerNeighbourOrderingVerified")) and
                       checks.get("originalCellCountChecked") == row["cellCount"] and
                       conversion.get("coordinateUnits") == "UNVERIFIED",
                       "CFD_EVERY_ORIGINAL_CELL_FACE_COORDINATE_REQUIRED")
        low, high, total = (native.protocol.number(checks.get(key), positive=True) for key in
                           ("minimumCellVolumeAssumedM3", "maximumCellVolumeAssumedM3", "totalCellVolumeAssumedM3"))
        closure = native.protocol.number(checks.get("maximumRelativeAreaVectorClosureError"), nonnegative=True)
        tolerance = native.protocol.number(checks.get("relativeAreaClosureTolerance"), positive=True)
        native.require(low <= high <= total and tolerance == 1e-10 and closure <= tolerance and
                       checks.get("internalFaceCount") == row.get("internalFaceCount"),
                       "CFD_ORIGINAL_CELL_VOLUME_AND_CLOSURE_REQUIRED")
        boundary_volume = native.protocol.number(checks.get("boundarySignedVolumeAssumedM3"), positive=True)
        volume_difference = native.protocol.number(checks.get("relativeCellBoundaryVolumeDifference"), nonnegative=True)
        measured_difference = abs(total-boundary_volume)/max(total,boundary_volume)
        native.require(checks.get("cellBoundaryVolumeAgreementVerified") is True and volume_difference <= 1e-10 and
                       measured_difference <= 1e-10 and math.isclose(volume_difference, measured_difference, rel_tol=1e-12, abs_tol=1e-15),
                       "CFD_ORIGINAL_CELL_BOUNDARY_VOLUME_AGREEMENT_REQUIRED")
        patches = row.get("patches", [])
        native.require(isinstance(patches, list) and len(patches) == len(native.case_tool.PATCHES[component]) and
                       {patch.get("name") for patch in patches} == native.case_tool.PATCHES[component],
                       "CFD_EXACT_ORIGINAL_BOUNDARY_ZONE_REQUIRED")
        zones, first = [], row["internalFaceCount"]
        native.require(isinstance(first, int) and not isinstance(first, bool) and 0 < first < row["faceCount"],
                       "CFD_ORIGINAL_FACE_PARTITION_REQUIRED")
        for patch in patches:
            count, minimum, maximum, zone_id = (patch.get(key) for key in
                                               ("nFaces", "sourceMinimumFaceId", "sourceMaximumFaceId", "sourceZoneId"))
            native.require(all(positive_integer(value) for value in (count, minimum, maximum, zone_id)) and
                           maximum-minimum+1 == count and patch.get("startFace") == first and
                           patch.get("reversedFaceCount") == count and
                           patch.get("type") == ("wall" if patch["name"] in native.case_tool.ROTATING | native.case_tool.STATIONARY else "patch"),
                           "CFD_ORIGINAL_POLYGON_IDENTITY_WINDING_REQUIRED")
            first += count
            zones.append({"sourceZoneId": zone_id, "sourceZoneName": patch["name"], "sourcePolygonCount": count,
                          "sourceMinimumFaceId": minimum, "sourceMaximumFaceId": maximum,
                          "orientationTransfer": "Source c0 inward polygon reversed to native owner outward polygon"})
        native.require(first == row["faceCount"] and sum(zone["sourcePolygonCount"] for zone in zones) == row.get("boundaryFaceCount") and
                       len({zone["sourceZoneId"] for zone in zones}) == len(zones), "CFD_COMPLETE_ORIGINAL_BOUNDARY_IDENTITY_REQUIRED")
        cell_zone = row.get("cellZone", {})
        native.require(cell_zone.get("name") == ("rotating-fluid" if component == "impeller" else "stationary-fluid") and
                       cell_zone.get("sourceName") == component and cell_zone.get("cellCount") == row["cellCount"] and
                       cell_zone.get("sourceZoneId") == (36 if component == "impeller" else 2), "CFD_ORIGINAL_CELL_ZONE_IDENTITY_REQUIRED")
        sources.append({key: source[key] for key in ("name", "bytes", "sha256")})
        boundaries.append({"sourceMesh": source_name, "zones": sorted(zones, key=lambda zone: zone["sourceZoneId"])})
    counts = {key: sum(row[key] for row in components) for key in ("cellCount", "nodeCount", "faceCount")}
    native.require(conversion.get("combinedCounts") == counts, "CFD_ORIGINAL_COMBINED_COUNTS_REQUIRED")
    return {"schemaVersion": "untirta.frozen-rotor-fixed-geometry-identity.v1", "exampleDataCommit": native.case_tool.inventory.PIN,
            "sources": sources, "boundaries": boundaries, "sourceGeometryRoute": GEOMETRY_ROUTE,
            "nativeUnitsToMetres": 1, "nativeCoordinateUnitsVerified": False,
            "geometryPolicy": "Every original node coordinate and polygon retained; c0/c1 winding explicitly transferred to OpenFOAM owner/neighbor convention. No remeshing or geometry healing.",
            "mainApplicationWrites": False}


def build_manifest(execution_directory):
    """Canonical original-volume inputs; caller persists bytes before foamRun."""
    root = native.case_tool.regular_directory(execution_directory)
    preparation, preparation_row = native.record(root, "openfoam-preparation.json")
    native.require(preparation.get("schemaVersion") == SCHEMA and
                   preparation.get("exampleDataCommit") == native.case_tool.inventory.PIN and
                   preparation.get("status") == "ORIGINAL_CASE_PREPARED_NATIVE_EXECUTION_REQUIRED" and
                   preparation.get("mainApplicationWrites") is False and preparation.get("originalMeshesImmutable") is True and
                   preparation.get("commandExecutionPerformedByThisTool") is False and
                   preparation.get("openFoamConversionVerified") is True and
                   all(preparation.get(key) is False for key in ("nativeGeometryQualityVerified", "meshQualityCheckedBySolver",
                       "qualifiedBaseline", "actualCfdRunVerified", "simulationExecuted")), "CFD_ORIGINAL_PREPARATION_REQUIRED")
    conversion, conversion_row = native.record(root, "native-conversion.json")
    native.require(conversion_row["sha256"] == preparation.get("nativeConversionReportSha256"),
                   "CFD_EXACT_ORIGINAL_CONVERSION_REPORT_BINDING_REQUIRED")
    geometry = geometry_identity(conversion)
    geometry_raw = native.encoded(geometry)
    geometry_row = {"path": "geometry-identity.json", "bytes": len(geometry_raw), "sha256": sha256(geometry_raw).hexdigest()}
    if (root / geometry_row["path"]).exists():
        native.require(native.read(root, geometry_row["path"], native.MAX_RECORD)[0] == geometry_raw,
                       "CFD_CANONICAL_ORIGINAL_GEOMETRY_IDENTITY_REQUIRED")
    assumptions = preparation.get("classroomAssumptions", {})
    pressure = original.pressure_reference(assumptions.get("externalTotalPressureAbsolutePa"))
    native.require(assumptions.get("rhoKgM3") == 998.2 and assumptions.get("muPaS") == .001002 and
                   assumptions.get("gMS2") == 9.81 and assumptions.get("speedRpm") == 1450 and assumptions.get("axis") == [0,0,1] and
                   assumptions.get("massFlowKgS") == 90 and assumptions.get("gravityInSolver") is False and
                   assumptions.get("metresPerNativeUnit") == 1 and assumptions.get("nativeUnitsVerified") is False and
                   assumptions.get("pressureSelectedBeforeExecution") is True and
                   math.isclose(native.protocol.number(assumptions.get("volumetricFlowM3S"), positive=True), 90/998.2, rel_tol=1e-12),
                   "CFD_FIXED_ORIGINAL_CLASSROOM_BASIS_REQUIRED")
    components = preparation.get("components", [])
    native.require(isinstance(components, list) and len(components) == 2, "CFD_ORIGINAL_PREPARED_COMPONENTS_REQUIRED")
    for actual, source in zip(components, conversion["components"]):
        native.require(actual == {"component": source["name"], "caseDirectory": source["casePath"],
                       **{key: source[key] for key in ("cellCount", "nodeCount", "faceCount")},
                       "patchFaces": {row["name"]: row["nFaces"] for row in source["patches"]}, "cellZone": source["cellZone"]["name"]},
                       "CFD_ORIGINAL_PREPARED_COMPONENT_BINDING_REQUIRED")
    native.require(preparation.get("nativeCommandArguments") == original.commands(root), "CFD_EXACT_ORIGINAL_NATIVE_COMMANDS_REQUIRED")
    identity, runtime_row = native.record(root, "runtime-identity.json")
    native.binary_identity(identity)
    group_paths = {
        "meshSha256": ["impeller-case/constant/polyMesh/" + name for name in MESH_FILES],
        "boundarySha256": ["impeller-case/0/" + name for name in ("U","p","k","omega","nut")] +
                          ["impeller-case/constant/" + name for name in ("MRFProperties","physicalProperties","momentumTransport")],
        "discretizationSha256": ["impeller-case/system/" + name for name in ("fvSchemes","fvSolution","controlDict","createNonConformalCouplesDict")],
        "runtimeSha256": ["runtime-identity.json"]}
    files = [{**row, "control": None} for row in (preparation_row, conversion_row)] + [{**geometry_row, "control": "geometrySha256"}]
    controls = {"cavitationModelSha256": None, "geometrySha256": sha256(native.encoded([geometry_row])).hexdigest()}
    authored = original.case_files("impeller", pressure)
    for key, paths in group_paths.items():
        rows = []
        for relative in sorted(paths):
            raw, row = native.read(root, relative, native.MAX_FIELD)
            local = relative.removeprefix("impeller-case/")
            if local in authored and not local.startswith("0/"):
                native.require(raw == authored[local].encode("ascii"), "CFD_AUTHORED_ORIGINAL_NATIVE_INPUT_MISMATCH:" + local)
            elif local in {"0/p", "0/U"}:
                text = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw.decode("ascii"), flags=re.S)
                if local == "0/p":
                    body = re.search(r'"?inlet"?\s*\{([^{}]*)\}', text)
                    native.require(body and re.search(r"type\s+totalPressure\s*;", body[1]) and
                                   re.search(r"p0\s+uniform\s+0(?:\.0*)?\s*;", body[1]), "CFD_ORIGINAL_PRESSURE_BC_REQUIRED")
                else:
                    body = re.search(r'"?mass-flow-inlet-11"?\s*\{([^{}]*)\}', text)
                    flow = re.search(r"volumetricFlowRate\s+(?:constant\s+)?(" + native.FLOAT + r")\s*;", body[1] if body else "")
                    native.require(body and re.search(r"type\s+flowRateOutletVelocity\s*;", body[1]) and flow and
                                   math.isclose(float(flow[1]), 90/998.2, rel_tol=1e-12), "CFD_ORIGINAL_FIXED_FLOW_BC_REQUIRED")
            rows.append(row); files.append({**row, "control": key})
        controls[key] = sha256(native.encoded(rows)).hexdigest()
    for component in conversion["components"]:
        declared = component.get("sourceFaceIdMapFile", {})
        native.require(declared.get("path") == component["casePath"] + "/sourceFaceIds", "CFD_ORIGINAL_SOURCE_FACE_MAPPING_REQUIRED")
        raw, row = native.read(root, declared["path"], native.MAX_FIELD)
        native.require(row == declared and b"object sourceFaceIds;" in raw and b"class labelList;" in raw,
                       "CFD_ORIGINAL_SOURCE_FACE_MAPPING_BINDING_REQUIRED")
        files.append({**row, "control": None})
    study = native.protocol.prepare_study(controls=controls, gravity_in_solver=False)
    # This baseline binds its selected pressure; phase campaigns use their own
    # reviewed structural signature and separately bound pressure scalar.
    study["signatureScope"]["boundarySha256"] = "Baseline patches/types/fixed inputs; pressure reference is independently bound by the exact controlDict"
    study["signatureScope"]["discretizationSha256"] = "Baseline spatial schemes/linear solvers/runtime controls and selected absolute pressure reference; phase variation requires its own adapter"
    manifest = {"schemaVersion": native.SCHEMA, "sourceGeometryRoute": GEOMETRY_ROUTE,
                "studyInputs": study["input"], "controls": controls, "signatureScope": study["signatureScope"],
                "samplingDefinition": study["samplingDefinition"], "physicalBasis": study["physicalBasis"],
                "physicalModel": "steady-single-phase-MRF", "files": files,
                "preparationReportSha256": preparation_row["sha256"], "nativeConversionReportSha256": conversion_row["sha256"],
                "runtimeIdentitySha256": runtime_row["sha256"], "pressureLevelAbsolutePa": pressure,
                "inletElevationM": 0., "outletElevationM": 0.,
                "elevationBasis": "Explicit classroom common zero datum; section centroids were not verified.",
                "coordinateBasis": "One metre per source unit is a declared classroom assumption, not a verified native unit.",
                "classroomAcceptance": {"samplingWindowIterations": 20, "continuityLocalLimit": 1e-4,
                    "interfaceMinimumCoverage": .99, "interfaceAverageCoverage": .999, "interfaceMaximumCoverage": 1.01, "interfaceFluxRelativeLimit": .005},
                "claimBoundary": "Original polygon volume content binding; actual native checkMesh and every numerical acceptance gate remain required.",
                "simulationExecuted": False, "mainApplicationWrites": False}
    return {"study": study, "manifest": manifest, "geometryIdentity": geometry}
