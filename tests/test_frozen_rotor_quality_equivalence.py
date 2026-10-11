"""Synthetic current-file correspondence fixtures; no native mesh/CFD is run."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools/verify_frozen_rotor_quality_equivalence.py"
SPEC = importlib.util.spec_from_file_location("quality_equivalence", TOOL)
eq = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(eq)


def write(root, relative, raw):
    raw = raw.encode("ascii") if isinstance(raw, str) else raw
    target = root / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
    return {"path": relative, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def native(name, kind, body):
    return eq.preparation.base.foam_header(name, kind) + body + "\n"


def labels(name, values):
    return native(name, "labelList", str(len(values)) + "(" + " ".join(map(str, values)) + ")")


def faces(rows):
    return native("faces", "faceList", str(len(rows)) + "(" + " ".join(str(len(row)) + "(" + " ".join(map(str, row)) + ")" for row in rows) + ")")


def vector_points(rows):
    return native("points", "vectorField", str(len(rows)) + "(" + " ".join("(" + " ".join(map(str, row)) + ")" for row in rows) + ")")


def fixture(root):
    """Small metadata/geometry fixtures bypass ONLY real pin-domain admission.

    validate_inputs is separately tested in the original-case suite; this mock
    enables complete current-file/multiset/zone/replay tests without 1.14M cells.
    binary_identity is mocked because the test never runs native executables.
    """
    converted, execution = root / "converted", root / "execution"
    converted.mkdir(); execution.mkdir()
    verified, source_components, data, source_rows = {}, [], {}, []
    xyz = []
    for name, cell_count, internal_count, zone in (("impeller", 3, 2, "rotating-fluid"), ("volute", 2, 1, "stationary-fluid")):
        folder = name + "-case"; offset = 0 if name == "impeller" else 5
        points = [(offset, 0, 0), (offset + 1, 0, 0), (offset, 1, 0), (offset, 0, 1)]
        xyz += points
        owner = list(range(internal_count)); neighbour = [i + 1 for i in range(internal_count)]
        patches, first = [], internal_count
        for i, patch_name in enumerate(sorted(eq.preparation.base.PATCHES[name])):
            patches.append({"name": patch_name, "type": "wall" if patch_name in eq.preparation.base.ROTATING | eq.preparation.base.STATIONARY else "patch", "nFaces": 1, "startFace": first})
            owner.append(i % cell_count); first += 1
        face_rows = [[0, 1, 2] if i % 2 == 0 else [0, 2, 3] for i in range(first)]
        body = str(len(patches)) + "(" + " ".join(item["name"] + " {type " + item["type"] + "; nFaces 1; startFace " + str(item["startFace"]) + ";}" for item in patches) + ")"
        raws = {"points": vector_points(points), "faces": faces(face_rows), "owner": labels("owner", owner), "neighbour": labels("neighbour", neighbour),
                "boundary": native("boundary", "polyBoundaryMesh", body),
                "cellZones": native("cellZones", "regIOobject", "1(" + zone + " {type cellZone; cellLabels List<label> " + str(cell_count) + "(" + " ".join(map(str, range(cell_count))) + ");})")}
        files = [write(converted, folder + "/constant/polyMesh/" + key, raws[key]) for key in eq.preparation.MESH_NAMES]
        mapping = write(converted, folder + "/sourceFaceIds", labels("sourceFaceIds", list(range(1, first + 1))))
        size, checksum = eq.preparation.base.inventory.FILES[name + ".msh.h5"]
        source_file = {"name": name + ".msh.h5", "bytes": size, "sha256": checksum, "sourceUrl": eq.preparation.base.inventory.mesh_url(name + ".msh.h5")}
        source_components.append({"name": name, "cellCount": cell_count, "nodeCount": 4, "faceCount": first, "internalFaceCount": internal_count,
                                  "sourceFile": source_file, "patches": patches})
        verified[name] = {"component": name, "caseDirectory": folder, "cellCount": cell_count, "nodeCount": 4, "faceCount": first,
                          "patchFaces": {p["name"]: 1 for p in patches}, "cellZone": zone, "nativeFiles": files, "sourceFaceIdMapFile": mapping}
        source_rows += files + [mapping]
        data[name] = {"owner": owner, "neighbour": neighbour, "faces": face_rows, "patches": patches}
        for row in files + [mapping]: write(execution, row["path"], (converted / row["path"]).read_bytes())
    conversion = {"schemaVersion": eq.preparation.CONVERSION_SCHEMA, "exampleDataCommit": eq.preparation.base.inventory.PIN, "components": source_components}
    conversion_row = write(converted, "native-conversion.json", encoded(conversion)); write(execution, "native-conversion.json", encoded(conversion))
    original = {"report": conversion, "reportFile": conversion_row, "components": verified, "sourceDirectory": converted}
    authored = []
    for name in ("impeller", "volute"):
        for relative, content in eq.preparation.case_files(name, 300000).items():
            authored.append(write(execution, name + "-case/" + relative, content))
    prepared = {"schemaVersion": eq.preparation.SCHEMA, "exampleDataCommit": eq.preparation.base.inventory.PIN,
                "nativeConversionReportSha256": conversion_row["sha256"], "originalMeshesImmutable": True,
                **{key: False for key in ("commandExecutionPerformedByThisTool", "nativeGeometryQualityVerified", "actualCfdRunVerified", "simulationExecuted", "mainApplicationWrites")},
                "components": [{key: row[key] for key in ("component", "caseDirectory", "cellCount", "nodeCount", "faceCount", "patchFaces", "cellZone")} for row in verified.values()],
                "sourceComponentFiles": source_rows, "authoredInputFiles": authored, "nativeCommandArguments": eq.preparation.commands(execution),
                "classroomAssumptions": {"externalTotalPressureAbsolutePa": 300000}}
    preparation_row = write(execution, "openfoam-preparation.json", encoded(prepared))
    runtime = {"schemaVersion": "untirta.openfoam-native-runtime-identity.v1", "binaries": [{"program": command, "path": "/opt/openfoam12/bin/" + command} for command in ("mergeMeshes", "createNonConformalCouples", "checkMesh")]}
    runtime_row = write(execution, "runtime-identity.json", encoded(runtime))
    stages = []
    for index, arguments in enumerate(eq.preparation.commands(execution)[:3]):
        name = arguments[0]
        text = "SYNTHETIC FIXTURE - NO NATIVE EXECUTION\nBuild : 12-86e126a7bc4d\nnProcs : 1\nExec : /opt/openfoam12/bin/" + " ".join(arguments) + "\nCase : " + str(execution / "impeller-case") + "\n"
        if name == "mergeMeshes":
            text += 'Create polyMesh for case "' + str(execution / "volute-case") + '"\nAdding mesh "' + str(execution / "volute-case") + '/0/region0"\nMerging all meshes\n'
        if name == "checkMesh":
            text += "    cells: 5\nCell determinant (wellposedness) : minimum: 0.00005 average: 2\n ***Cells with small determinant (< 0.001) found, number of cells: 3\nFailed 1 mesh checks.\n"
        text += "End\n"
        row = write(execution, f"logs/{index:02d}-{name}.log", text)
        stages.append({"program": name, "exitCode": 0, "elapsedSeconds": 1, "log": Path(row["path"]).name, "logBytes": row["bytes"], "logSha256": row["sha256"]})
    executed = {"schemaVersion": eq.EXECUTION_SCHEMA, "exampleDataCommit": eq.preparation.base.inventory.PIN,
                "status": "ORIGINAL_NATIVE_STAGE_CORRECTION_REQUIRED", "diagnostic": {"program": "checkMesh"},
                **{key: False for key in ("actualCfdRunVerified", "qualifiedBaseline", "nativeSolverCompleted", "simulationExecuted", "nativeGeometryQualityVerified", "mainApplicationWrites")},
                "preparationReportSha256": preparation_row["sha256"], "nativeConversionReportSha256": conversion_row["sha256"],
                "runtimeIdentitySha256": runtime_row["sha256"], "stages": stages}
    write(execution, "openfoam-execution.json", encoded(executed))
    # Native point/face row order is deliberately changed. Canonical coordinate
    # classes + cyclic oriented polygons permit this exact native preservation.
    order = [4, 0, 6, 3, 1, 5, 7, 2]; inverse = {old: new for new, old in enumerate(order)}
    merged_owner, merged_neighbour, merged_faces = [], [], []
    for name, offset, node_offset in (("impeller", 0, 0), ("volute", 3, 4)):
        item = data[name]; n = len(item["neighbour"])
        merged_owner += [v + offset for v in item["owner"][:n]]; merged_neighbour += [v + offset for v in item["neighbour"]]
        merged_faces += [[inverse[v + node_offset] for v in row[1:] + row[:1]] for row in item["faces"][:n]]
    boundary_entries, start = [], len(merged_neighbour)
    for name, offset, node_offset in (("impeller", 0, 0), ("volute", 3, 4)):
        item = data[name]; n = len(item["neighbour"])
        for i, patch_row in enumerate(item["patches"]):
            merged_owner.append(item["owner"][n + i] + offset)
            row = item["faces"][n + i]; merged_faces.append([inverse[v + node_offset] for v in row[1:] + row[:1]])
            boundary_entries.append(patch_row["name"] + " {type " + patch_row["type"] + "; nFaces 1; startFace " + str(start) + ";}"); start += 1
    rotor, stator = "interface-impeller-outlet", "interface-volute-inlet"
    for original_patch, opposite in ((rotor, stator), (stator, rotor)):
        boundary_entries.append("pumpInterface_on_" + original_patch + " {type nonConformalCyclic; nFaces 0; startFace " + str(start) + "; originalPatch " + original_patch + "; neighbourPatch pumpInterface_on_" + opposite + "; transformType none;}")
        boundary_entries.append("nonConformalError_on_" + original_patch + " {type nonConformalError; nFaces 0; startFace " + str(start) + "; originalPatch " + original_patch + ";}")
    mesh = "impeller-case/constant/polyMesh/"
    for name, content in {"points": vector_points([xyz[i] for i in order]), "faces": faces(merged_faces), "owner": labels("owner", merged_owner), "neighbour": labels("neighbour", merged_neighbour),
            "boundary": native("boundary", "polyBoundaryMesh", "15(" + "\n".join(boundary_entries) + ")"),
            "cellZones": native("cellZones", "cellZoneList", "2(stationary-fluid {type cellZone; cellLabels List<label> 2(4 3);}\nrotating-fluid {type cellZone; cellLabels List<label> 3(2 0 1);})")}.items(): write(execution, mesh + name, content)
    write(execution, mesh + "sets/underdeterminedCells", native("underdeterminedCells", "cellSet", "3(4 2 0)"))
    source = {"schemaVersion": eq.localization.SCHEMA, "exampleDataCommit": eq.preparation.base.inventory.PIN, "openFoamSourceCommit": eq.localization.FOAM_COMMIT,
              "determinantThreshold": .001, "sourceMeshesImmutable": True,
              **{key: False for key in ("sourceMeshModified", "thresholdModified", "solverExecutedByThisTool", "actualCfdRunVerified", "nativeGeometryQualityVerified", "mainApplicationWrites")},
              "combinedCellCount": 5, "combinedFailedCellCount": 3, "minimumReplicatedDeterminant": .00005, "meanReplicatedDeterminant": 2,
              "components": [{"component": name, "cellCount": component["cellCount"], "sourceFile": component["sourceFile"], "failedSourceCellIds": failed,
                "failedCells": [{"sourceCellId": value, "replicatedWellposednessDeterminant": .00005} for value in failed],
                "cellDeterminantSummary": {"minimum": .00005, "mean": 2, "threshold": .001, "failedCellCount": len(failed)}}
                for name, component, failed in zip(("impeller", "volute"), source_components, ([1, 3], [2]))]}
    source_path = root / "source-localization.json"; write(root, source_path.name, encoded(source))
    return {"converted": converted, "execution": execution, "source": source_path, "verified": original,
            "owner": merged_owner, "neighbour": merged_neighbour, "faceRows": merged_faces}


class OriginalCorrespondence(unittest.TestCase):
    def invoke(self, packet):
        with patch.object(eq.preparation, "validate_inputs", return_value=packet["verified"]), patch.object(eq.native, "binary_identity"):
            return eq.verify(packet["execution"], packet["converted"], packet["source"])

    def change_json(self, path, change):
        value = json.loads(path.read_bytes()); change(value); path.write_bytes(encoded(value))

    def test_complete_current_original_correspondence_accepts_ordered_cells_not_zone_row_order(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
            packet = fixture(Path(temp)); before = {p: p.read_bytes() for p in Path(temp).rglob("*") if p.is_file()}
            result = self.invoke(packet)
            for key in ("nativeFailedCellSetVerified", "sourceCellOrderVerified", "originalBoundaryUncoupledMaskVerified", "exactFailedCellSetsMatch", "allOriginalOrientedPolygonCellBindingsVerified", "allCoordinatesExactMultisetVerified", "currentEvidenceByteBindingVerified"):
                self.assertTrue(result[key])
            self.assertEqual([c["failedSourceCellIds"] for c in result["components"]], [[1, 3], [2]])
            self.assertEqual(result["openFoamNativeBuildSourceCommit"], eq.FOAM_COMMIT)
            self.assertFalse(result["sourceOrderProof"]["zoneRowOrderUsed"])
            for key in ("nativeQualityAccepted", "nativeGeometryQualityVerified", "qualifiedBaseline", "simulationExecuted", "actualCfdRunVerified", "mainApplicationWrites"):
                self.assertFalse(result[key])
            self.assertEqual(before, {p: p.read_bytes() for p in before})
            for row in result["nativeEvidenceRecords"]:
                raw = (Path(temp) / row["path"]).read_bytes(); self.assertEqual(sha256(raw).hexdigest(), row["sha256"])

    def test_current_same_zone_cell_permutation_is_rejected_by_actual_polygon_cell_bindings(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
            packet = fixture(Path(temp)); owner = packet["owner"][:]; owner[0], owner[1] = owner[1], owner[0]
            # Retain ordered pairs by swapping boundary-only same-zone owners.
            owner = packet["owner"][:]; owner[3], owner[4] = owner[4], owner[3]
            write(packet["execution"], "impeller-case/constant/polyMesh/owner", labels("owner", owner))
            with self.assertRaisesRegex(eq.EquivalenceIssue, "boundary.*ownership"): self.invoke(packet)

    def test_changed_or_reversed_actual_polygon_rejected_despite_unchanged_counts(self):
        for change in (lambda rows: rows[0].reverse(), lambda rows: rows[0].__setitem__(1, rows[0][1] + 1)):
            with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
                packet = fixture(Path(temp)); rows = packet["faceRows"]; change(rows)
                write(packet["execution"], "impeller-case/constant/polyMesh/faces", faces(rows))
                with self.assertRaises(eq.EquivalenceIssue): self.invoke(packet)

    def test_native_points_multiplicity_or_finite_domain_is_not_a_tolerance_gate(self):
        for replacement in ("(5.000000000000001 0 0)", "(nan 0 0)", "(0 0 0)"):
            with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
                packet = fixture(Path(temp)); path = packet["execution"] / "impeller-case/constant/polyMesh/points"
                path.write_text(path.read_text().replace("(5 0 0)", replacement))
                with self.assertRaises(eq.EquivalenceIssue): self.invoke(packet)

    def test_zone_membership_permutation_rejects_instead_of_guessed_sorted_rank(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
            packet = fixture(Path(temp)); path = packet["execution"] / "impeller-case/constant/polyMesh/cellZones"
            path.write_text(path.read_text().replace("2(4 3)", "2(4 2)").replace("3(2 0 1)", "3(3 0 1)"))
            with self.assertRaisesRegex(eq.EquivalenceIssue, "complete cell-zone membership"): self.invoke(packet)

    def test_positive_ncc_wrong_source_wall_or_unassigned_boundary_reject(self):
        changes = [lambda text: text.replace("type nonConformalCyclic; nFaces 0", "type nonConformalCyclic; nFaces 1", 1),
                   lambda text: text.replace("blade {type wall", "blade {type patch"),
                   lambda text: text.replace("transformType none;", "transformType rotational;", 1),
                   lambda text: text.replace("nonConformalError_on_interface-volute-inlet", "unproved-patch", 1)]
        for change in changes:
            with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
                packet = fixture(Path(temp)); path = packet["execution"] / "impeller-case/constant/polyMesh/boundary"; path.write_text(change(path.read_text()))
                with self.assertRaises(eq.EquivalenceIssue): self.invoke(packet)

    def test_complete_failed_ids_must_match_exactly_not_only_total_and_minimum(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
            packet = fixture(Path(temp)); path = packet["execution"] / "impeller-case/constant/polyMesh/sets/underdeterminedCells"
            path.write_text(path.read_text().replace("3(4 2 0)", "3(4 1 0)"))
            with self.assertRaisesRegex(eq.EquivalenceIssue, "failed-cell IDs disagree"): self.invoke(packet)

    def test_missing_current_native_set_mesh_or_raw_log_rejects(self):
        for relative in ("impeller-case/constant/polyMesh/sets/underdeterminedCells", "impeller-case/constant/polyMesh/faces", "logs/01-createNonConformalCouples.log"):
            with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
                packet = fixture(Path(temp)); (packet["execution"] / relative).unlink()
                with self.assertRaises(Exception): self.invoke(packet)

    def test_actual_log_build_arguments_cell_count_threshold_and_bindings_are_required(self):
        changes = [lambda text: text.replace("12-86e126a7bc4d", "12-unreviewed"), lambda text: text.replace("-allGeometry ", ""),
                   lambda text: text.replace("cells: 5", "cells: 4"), lambda text: text.replace("< 0.001", "< 0.00001"),
                   lambda text: text.replace("End", "Incomplete")]
        for change in changes:
            with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
                packet = fixture(Path(temp)); path = packet["execution"] / "logs/02-checkMesh.log"; path.write_text(change(path.read_text()))
                self.change_json(packet["execution"] / "openfoam-execution.json", lambda data: data["stages"][2].update(logBytes=path.stat().st_size, logSha256=sha256(path.read_bytes()).hexdigest()))
                with self.assertRaises(Exception): self.invoke(packet)

    def test_source_formula_pin_threshold_complete_rows_and_false_claim_boundary(self):
        changes = [lambda d: d.update(openFoamSourceCommit="0" * 40), lambda d: d.update(determinantThreshold=.00001),
                   lambda d: d.update(exampleDataCommit="0" * 40), lambda d: d.update(actualCfdRunVerified=True),
                   lambda d: d["components"][0]["failedCells"].pop(), lambda d: d["components"][0]["failedCells"][0].update(replicatedWellposednessDeterminant=.001),
                   lambda d: d["components"][0].update(failedSourceCellIds=[1, 1]), lambda d: d.update(combinedFailedCellCount=2),
                   lambda d: d.update(meanReplicatedDeterminant=4)]
        for change in changes:
            with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
                packet = fixture(Path(temp)); self.change_json(packet["source"], change)
                with self.assertRaises(eq.EquivalenceIssue): self.invoke(packet)

    def test_preparation_runtime_conversion_report_hashes_and_schema_cannot_be_replayed(self):
        changes = [lambda d: d.update(preparationReportSha256="0" * 64), lambda d: d.update(runtimeIdentitySha256="0" * 64),
                   lambda d: d.update(nativeConversionReportSha256="0" * 64), lambda d: d.update(schemaVersion="derived-pilot"),
                   lambda d: d.update(nativeSolverCompleted=True), lambda d: d["stages"].reverse()]
        for change in changes:
            with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
                packet = fixture(Path(temp)); self.change_json(packet["execution"] / "openfoam-execution.json", change)
                with self.assertRaises(eq.EquivalenceIssue): self.invoke(packet)

    def test_independent_original_or_appended_source_map_file_mutation_rejects(self):
        for folder, relative in (("execution", "volute-case/constant/polyMesh/owner"), ("execution", "impeller-case/sourceFaceIds"), ("converted", "impeller-case/constant/polyMesh/faces")):
            with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
                packet = fixture(Path(temp)); path = packet[folder] / relative; path.write_bytes(path.read_bytes() + b"\n//changed\n")
                with self.assertRaises(eq.EquivalenceIssue): self.invoke(packet)

    def test_nonmaster_authored_inputs_must_still_match_exact_recipe(self):
        for relative in ("volute-case/0/U", "impeller-case/system/createNonConformalCouplesDict", "impeller-case/system/controlDict"):
            with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
                packet = fixture(Path(temp)); path = packet["execution"] / relative; path.write_bytes(path.read_bytes() + b"\n// changed\n")
                with self.assertRaisesRegex(eq.EquivalenceIssue, "authored control"): self.invoke(packet)

    def test_preexisting_solver_log_or_endpoint_stops_diagnostic_scope(self):
        for relative in ("logs/03-foamRun.log", "impeller-case/500/value"):
            with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
                packet = fixture(Path(temp)); write(packet["execution"], relative, "not a source diagnostic\n")
                with self.assertRaisesRegex(eq.EquivalenceIssue, "before any native solver endpoint"): self.invoke(packet)

    def test_late_current_evidence_mutation_rejected_at_persistence_boundary(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
            packet = fixture(Path(temp)); actual = eq.face_digests; changed = False
            def mutate(*args, **kwargs):
                nonlocal changed
                result = actual(*args, **kwargs)
                if not changed:
                    changed = True; path = packet["execution"] / "runtime-identity.json"; path.write_bytes(path.read_bytes() + b"\n")
                return result
            with patch.object(eq, "face_digests", side_effect=mutate), self.assertRaisesRegex(eq.EquivalenceIssue, "changed before correspondence persistence"):
                self.invoke(packet)

    def test_native_payload_directive_binary_wrong_header_duplicate_dictionary_and_size_reject(self):
        for transform in (lambda raw: raw + b"\n#include outside\n", lambda raw: raw.replace(b"format ascii", b"format binary"), lambda raw: raw.replace(b"class faceList", b"class labelList")):
            with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
                packet = fixture(Path(temp)); path = packet["execution"] / "impeller-case/constant/polyMesh/faces"; path.write_bytes(transform(path.read_bytes()))
                with self.assertRaises(Exception): self.invoke(packet)
        with self.assertRaises(eq.EquivalenceIssue): eq.decoded(b'{"schema":1,"schema":2}')
        with self.assertRaises(eq.EquivalenceIssue): eq.decoded(b'{"x":NaN}')
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
            packet = fixture(Path(temp))
            with patch.object(eq, "MAX_FILE", 20), self.assertRaises(Exception): self.invoke(packet)

    def test_cli_fresh_report_exit_zero_never_promotes_quality_or_overwrites(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
            packet = fixture(Path(temp)); target = Path(temp) / "correspondence.json"
            args = [str(TOOL), "--execution-directory", str(packet["execution"]), "--converted-mesh-directory", str(packet["converted"]), "--source-localization-report", str(packet["source"]), "--output-report", str(target)]
            with patch("sys.argv", args), patch.object(eq.preparation, "validate_inputs", return_value=packet["verified"]), patch.object(eq.native, "binary_identity"), patch.object(eq, "print", create=True):
                self.assertEqual(eq.main(), 0); raw = target.read_bytes(); self.assertFalse(json.loads(raw)["actualCfdRunVerified"])
                self.assertEqual(eq.main(), 2); self.assertEqual(target.read_bytes(), raw)

    def test_links_and_outside_runner_input_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as other, patch.dict(os.environ, {"RUNNER_TEMP": temp}):
            packet = fixture(Path(temp)); outside = Path(other) / "source.json"; outside.write_bytes(packet["source"].read_bytes())
            with patch.object(eq.preparation, "validate_inputs", return_value=packet["verified"]), self.assertRaises(Exception): eq.verify(packet["execution"], packet["converted"], outside)
            with patch.object(eq.Path, "is_symlink", return_value=True), self.assertRaises(Exception): self.invoke(packet)


if __name__ == "__main__":
    unittest.main()
