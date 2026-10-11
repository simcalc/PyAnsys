"""Author an isolated MRF case from the two original polygonal volume meshes.

The original mesh transfer and an authored setup are distinct from native
checkMesh, CFD execution and numerical qualification. No solver is launched.
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
import frozen_rotor_cavitation_case as safety

SCHEMA = "untirta.frozen-rotor-original-openfoam-preparation.v1"
CONVERSION_SCHEMA = "untirta.frozen-rotor-original-polyMesh-conversion.v1"
MAX_COMBINED_CELLS = 2_000_000
MAX_COMBINED_NODES = 1_000_000
MAX_WORKSPACE_BYTES = 1024**3
MESH_NAMES = ("points", "faces", "owner", "neighbour", "boundary", "cellZones")
EXPECTED_COUNTS = {"impeller": (773352, 837936, 2383488), "volute": (368411, 122551, 831758)}


def require(condition, action):
    if not condition:
        raise base.CaseIssue(action)


def pressure_reference(value):
    require(not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
            and 2339 < value <= 10_000_000, "Declare a finite absolute pressure reference above the classroom vapour pressure and at most 10 MPa before running.")
    return float(value)


def case_files(component, pressure_reference_absolute_pa=300000):
    """Copy the accepted kinematic-pressure recipe; change only explicit pRef."""
    require(component in EXPECTED_COUNTS, "Use only the original impeller and volute components.")
    pressure = pressure_reference(pressure_reference_absolute_pa)
    result = base.case_files(component).copy()
    result.pop("system/changeDictionaryDict")
    if component == "impeller":
        text = result["system/controlDict"]
        require(text.count("pRef 101325;") == 1, "The reviewed total-pressure function object must have exactly one explicit reference.")
        result["system/controlDict"] = text.replace("pRef 101325;", "pRef " + format(pressure, ".17g") + ";")
    return result


def descriptor(root, relative, *, limit=base.MAX_NATIVE_BYTES):
    relative = safety.relative_path(relative)
    path = Path(root) / relative
    for item in (path, *path.parents):
        require(not item.is_symlink() and not getattr(item, "is_junction", lambda: False)(), "Original native files cannot traverse links or junctions.")
    raw = base.read_regular(path, limit)
    return raw, {"path": relative, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def validate_inputs(directory):
    """Bind every actual converted file to the exact immutable source report."""
    root = safety.inside_runner(directory)
    report = safety.json_file(root / "native-conversion.json")
    raw, report_row = descriptor(root, "native-conversion.json", limit=base.inventory.MAX_REPORT_BYTES)
    require(report.get("schemaVersion") == CONVERSION_SCHEMA and report.get("exampleDataCommit") == base.inventory.PIN,
            "The exact original-volume conversion schema and official source pin are required.")
    require(all(report.get(key) is True for key in ("originalMeshesImmutable", "volumeMeshConverted"))
            and all(report.get(key) is False for key in ("meshQualityCheckedBySolver", "nativeCaseGenerated", "simulationExecuted", "actualCfdRunVerified", "mainApplicationWrites")),
            "A source-pinned conversion report cannot claim native solver execution or quality acceptance.")
    require(report.get("nativeCoordinateUnitsVerified") is False and report.get("classroomMetresPerSourceCoordinateUnit") == 1,
            "Retain the unverified native units and explicit one-metre-per-source-unit classroom assumption.")
    components = report.get("components", [])
    require(isinstance(components, list) and [row.get("name") for row in components] == ["impeller", "volute"],
            "Two ordered original component inventories are required.")
    result, total_bytes = {}, 0
    for row in components:
        name, folder = row["name"], row.get("casePath")
        require(folder == name + "-case", "Original component paths must use the fixed isolated layout.")
        source = row.get("sourceFile", {})
        expected_bytes, expected_hash = base.inventory.FILES[name + ".msh.h5"]
        require(source == {"name": name + ".msh.h5", "bytes": expected_bytes, "sha256": expected_hash,
                           "sourceUrl": base.inventory.mesh_url(name + ".msh.h5")}, "Every immutable official source mesh identity must match exactly.")
        require(tuple(row.get(key) for key in ("cellCount", "nodeCount", "faceCount")) == EXPECTED_COUNTS[name],
                "The original native cell, node and face counts must remain exact; this lane is not a remesher.")
        patches = row.get("patches", [])
        require(isinstance(patches, list) and len(patches) == len(base.PATCHES[name])
                and {item.get("name") for item in patches} == base.PATCHES[name], "Every source boundary patch must retain its original identity.")
        first = row.get("internalFaceCount")
        require(isinstance(first, int) and 0 <= first < row["faceCount"], "An actual original internal-face count is required.")
        for item in patches:
            require(item.get("type") == ("wall" if item["name"] in base.ROTATING | base.STATIONARY else "patch")
                    and isinstance(item.get("nFaces"), int) and not isinstance(item["nFaces"], bool) and item["nFaces"] > 0
                    and item.get("startFace") == first, "Original wall types and contiguous nonempty patch face ranges must be exact.")
            first += item["nFaces"]
        require(first == row["faceCount"] and sum(item["nFaces"] for item in patches) == row.get("boundaryFaceCount"),
                "Every original boundary face must be accounted for.")
        zone = row.get("cellZone", {})
        expected_zone = "rotating-fluid" if name == "impeller" else "stationary-fluid"
        require(zone.get("name") == expected_zone and zone.get("sourceName") == name and zone.get("cellCount") == row["cellCount"],
                "Retain the exact source component-to-fluid-cell-zone mapping.")
        checks = row.get("checks", {})
        require(all(checks.get(key) is True for key in ("allOriginalCellsChecked", "directedEdgeIncidenceClosed", "standardCellShapeIncidenceVerified",
                    "allCoordinatesAsciiRoundTripVerified", "everyOriginalFaceTransferredExactlyOnce", "originalPolygonsRetained", "ownerNeighbourOrderingVerified"))
                and checks.get("originalCellCountChecked") == row["cellCount"]
                and isinstance(checks.get("minimumCellVolumeAssumedM3"), (int, float))
                and not isinstance(checks["minimumCellVolumeAssumedM3"], bool)
                and math.isfinite(checks["minimumCellVolumeAssumedM3"]) and checks["minimumCellVolumeAssumedM3"] > 0,
                "Every original cell closure, positive volume, polygon and coordinate derivation must pass the independent conversion checks.")
        require(checks.get("cellBoundaryVolumeAgreementVerified") is True
                and isinstance(checks.get("boundarySignedVolumeAssumedM3"), (int, float))
                and not isinstance(checks["boundarySignedVolumeAssumedM3"], bool) and math.isfinite(checks["boundarySignedVolumeAssumedM3"])
                and checks["boundarySignedVolumeAssumedM3"] > 0
                and isinstance(checks.get("relativeCellBoundaryVolumeDifference"), (int, float))
                and not isinstance(checks["relativeCellBoundaryVolumeDifference"], bool)
                and math.isfinite(checks["relativeCellBoundaryVolumeDifference"]) and 0 <= checks["relativeCellBoundaryVolumeDifference"] <= 1e-10,
                "The actual positive-cell volume sum must agree with the independent outward boundary integration.")
        files = row.get("nativeFiles", [])
        allowed = {folder + "/constant/polyMesh/" + item for item in MESH_NAMES}
        require(isinstance(files, list) and len(files) == 6 and {item.get("path") for item in files} == allowed,
                "Exactly six actual original polyMesh files are required per component.")
        verified = []
        for item in files:
            actual, metadata = descriptor(root, item["path"])
            require(metadata == item, "The original converted native file bytes or SHA256 changed.")
            safety.native_bytes(root / item["path"])
            count, _ = base.native_list(root / item["path"])
            expected_count = {"points": row["nodeCount"], "faces": row["faceCount"], "owner": row["faceCount"],
                              "neighbour": row["internalFaceCount"], "boundary": len(patches), "cellZones": 1}[Path(item["path"]).name]
            require(count == expected_count, "An actual native counted-list declaration differs from the source conversion inventory.")
            total_bytes += len(actual); verified.append(metadata)
        mapping = row.get("sourceFaceIdMapFile", {})
        require(mapping.get("path") == folder + "/sourceFaceIds", "The original face-index mapping must remain in its source component.")
        map_raw, map_row = descriptor(root, mapping["path"])
        require(map_row == mapping and b"object sourceFaceIds;" in map_raw and b"class labelList;" in map_raw,
                "The actual source-face mapping bytes and plain native role must remain exact.")
        safety.native_bytes(root / mapping["path"])
        total_bytes += len(map_raw)
        require(total_bytes <= 512 * 1024**2, "The combined original conversion transfer exceeds 512 MiB.")
        result[name] = {"component": name, "caseDirectory": folder, "cellCount": row["cellCount"], "nodeCount": row["nodeCount"],
                        "faceCount": row["faceCount"], "patchFaces": {item["name"]: item["nFaces"] for item in patches},
                        "cellZone": expected_zone, "nativeFiles": verified, "sourceFaceIdMapFile": map_row}
    counts = {key: sum(row[key] for row in result.values()) for key in ("cellCount", "nodeCount", "faceCount")}
    require(report.get("derivedBytes") == total_bytes and report.get("combinedCounts") == counts and counts["cellCount"] <= MAX_COMBINED_CELLS
            and counts["nodeCount"] <= MAX_COMBINED_NODES, "The exact original combined counts must fit the independently declared OpenFOAM budget.")
    return {"report": report, "reportFile": report_row, "components": result, "sourceDirectory": root}


def commands(output):
    case, other = Path(output).absolute() / "impeller-case", Path(output).absolute() / "volute-case"
    return [["mergeMeshes", "-case", str(case), "-addCases", '("' + str(other) + '")', "-overwrite"],
            ["createNonConformalCouples", "-case", str(case), "-overwrite"],
            ["checkMesh", "-case", str(case), "-allGeometry", "-allTopology", "-writeSets"], ["foamRun", "-case", str(case)]]


def prepare(converted_mesh_directory, output_directory, *, pressure_reference_absolute_pa=300000):
    pressure = pressure_reference(pressure_reference_absolute_pa)
    original = validate_inputs(converted_mesh_directory)
    output = Path(output_directory).absolute()
    base.inventory.output_path(output / "openfoam-preparation.json")
    require(not output.exists() and not output.is_symlink(), "Use a fresh independent RUNNER_TEMP original-native execution directory.")
    output.mkdir(parents=True)
    source = original["sourceDirectory"]
    source_files, authored, rows = [], [], []
    for name, component in original["components"].items():
        for row in [*component["nativeFiles"], component["sourceFaceIdMapFile"]]:
            raw, actual = descriptor(source, row["path"])
            require(actual == row, "Source converted bytes changed while copying.")
            base.write_exclusive(output / row["path"], raw); source_files.append(actual)
        for relative, content in case_files(name, pressure).items():
            path = component["caseDirectory"] + "/" + relative
            raw = content.encode("ascii"); base.write_exclusive(output / path, raw)
            authored.append({"path": path, "bytes": len(raw), "sha256": sha256(raw).hexdigest()})
        rows.append({key: component[key] for key in ("component", "caseDirectory", "cellCount", "nodeCount", "faceCount", "patchFaces", "cellZone")})
    raw, actual_report = descriptor(source, "native-conversion.json", limit=base.inventory.MAX_REPORT_BYTES)
    require(actual_report == original["reportFile"], "The immutable native conversion report changed during transfer.")
    base.write_exclusive(output / "native-conversion.json", raw)
    report = {"schemaVersion": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(), "exampleDataCommit": base.inventory.PIN,
              "status": "ORIGINAL_CASE_PREPARED_NATIVE_EXECUTION_REQUIRED", "nativeConversionReportSha256": actual_report["sha256"],
              "components": rows, "sourceComponentFiles": source_files, "authoredInputFiles": authored,
              "nativeCommandArguments": commands(output), "commandExecutionPerformedByThisTool": False,
              "classroomAssumptions": {"metresPerNativeUnit": 1, "nativeUnitsVerified": False, "rhoKgM3": base.RHO, "muPaS": base.MU,
                  "gMS2": base.G, "speedRpm": base.RPM, "axis": [0, 0, 1], "massFlowKgS": 90, "volumetricFlowM3S": base.Q,
                  "externalTotalPressureAbsolutePa": pressure, "kinematicGaugePressureDefinition": "pAbsolutePa=998.2*pKinematic+" + format(pressure, ".17g") + "; inlet external total gauge pressure=0 m2/s2.",
                  "pressureSelectedBeforeExecution": True, "gravityInSolver": False,
                  "headDatum": "Declared common zero section datum; no gravitational force in the steady solver; section elevations are not measured.",
                  "wallAdaptation": "Blade/impeller-hub/impeller-shroud rotate; other source walls are stationary. Classroom adaptation of the official example.",
                  "inletTurbulence": {"kM2S2": .375, "omegaSInverse": 100, "basis": "Declared classroom input, not measured."}},
              "limits": {"iterations": 500, "cpus": 1, "memoryBytes": 4*1024**3, "maximumSeconds": 1200,
                         "maximumWorkspaceBytes": MAX_WORKSPACE_BYTES, "maximumFileBytes": base.MAX_NATIVE_BYTES,
                         "maximumCells": MAX_COMBINED_CELLS, "maximumNodes": MAX_COMBINED_NODES},
              "openFoamConversionVerified": True, "nativeGeometryQualityVerified": False, "meshQualityCheckedBySolver": False,
              "qualifiedBaseline": False, "nativeCaseGenerated": False, "actualCfdRunVerified": False, "simulationExecuted": False,
              "originalMeshesImmutable": True, "mainApplicationWrites": False,
              "claimBoundary": "Prepared original polygonal-volume case with explicit classroom units and pressure reference. The combined 1,141,763-cell source exceeds the Fluent Student limit; this independent OpenFOAM lane uses a separately bounded public FREE runner. No native checkMesh or CFD result is claimed."}
    base.write_exclusive(output / "openfoam-preparation.json", base.inventory.bounded_json(report))
    # Recheck source transfer at the persistence boundary; working merged cases
    # subsequently change, while the independent converted source stays immutable.
    require(validate_inputs(source)["reportFile"] == original["reportFile"], "Original converted input evidence changed while preparing.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--converted-mesh-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--pressure-reference-absolute-pa", type=float, default=300000)
    args = parser.parse_args()
    try:
        report = prepare(args.converted_mesh_directory, args.output_directory,
                         pressure_reference_absolute_pa=args.pressure_reference_absolute_pa)
        print(json.dumps({key: report[key] for key in ("status", "actualCfdRunVerified", "mainApplicationWrites")})); return 0
    except Exception as error:
        print(json.dumps({"status": "ORIGINAL_PREPARATION_CORRECTION_REQUIRED", "action": str(error)[:500],
                          "actualCfdRunVerified": False, "mainApplicationWrites": False})); return 2


if __name__ == "__main__":
    raise SystemExit(main())
