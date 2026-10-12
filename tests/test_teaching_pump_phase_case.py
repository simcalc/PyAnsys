"""Synthetic byte/topology and real reducer maths; never native CFD evidence."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import teaching_pump_phase_case as case
import test_reduce_teaching_pump_baseline as baseline_tests
from test_teaching_pump_baseline_case import native_component, write_native
from test_teaching_pump_native_case import source_fixture


def write(root, relative, raw):
    path = root / relative; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw if isinstance(raw, bytes) else raw.encode("ascii"))
    return case.read(root, relative)[1]


def save(root, relative, value):
    return write(root, relative, case.encoded(value))


def region_fixture():
    """Synthetic four-cell connectivity/diagnostic data; no volume/CFD proof."""
    def labels(name, body, location="constant/polyMesh"):
        return (case.base.foam_header(name, "labelList", location) + body + "\n").encode("ascii")
    raw = labels("cellToRegion", "4 (0 0 1 1)", "0")
    owner = labels("owner", "8 (0 2 0 1 1 2 3 3)")
    neighbour = labels("neighbour", "2 (1 3)")
    zones = (case.base.foam_header("cellZones", "cellZoneList", "constant/polyMesh")
        + "2 (rotating-fluid {type cellZone; cellLabels List<label> 2 (0 1);} stationary-fluid {type cellZone; cellLabels List<label> 2 (2 3);})\n").encode("ascii")
    log = b'cells: 4\n   *Number of regions: 2\n  <<Writing region information to "0/cellToRegion"\n  <<Writing region 0 (fully disconnected) with 2 cells to cellSet region0\n  <<Writing region 1 (fully disconnected) with 2 cells to cellSet region1\nMesh OK.\nEnd\n'
    return raw, owner, neighbour, zones, log


def write_region_fixture(root):
    raw, owner, neighbour, zones, log = region_fixture()
    for name, data in ((case.NATIVE_REGION_PATH, raw), ("case/constant/polyMesh/owner", owner),
        ("case/constant/polyMesh/neighbour", neighbour), ("case/constant/polyMesh/cellZones", zones), ("logs/00-checkMesh.log", log)):
        write(root, name, data)
    return raw


def mocked_producer_admission():
    """Mock only original CAD/native producer admission and installed binaries.

Native programs do not exist in the fixture. Current mesh parsing, controls,
field/table/log binding, reducer equations and source-copy checks remain real.
"""
    stack = ExitStack()
    def checked(root, **_):
        report, row = case.json_file(root, case.baseline.PREPARATION_FILE)
        return {"report": report, "reportFile": row, "components": report["components"]}
    stack.enter_context(patch.object(case.baseline_case, "verify_output", side_effect=checked))
    stack.enter_context(patch.object(case.baseline, "binary_identity"))
    # This miniature producer lacks the full discharge CAD/snapshot. The
    # dedicated profile suite verifies that geometry; only upstream proof is
    # mocked here while current source/physics/math/copy checks remain real.
    stack.enter_context(patch.object(case.outlet, "verify_outlet",
        side_effect=lambda root, **_: case.json_file(root, case.baseline.PREPARATION_FILE)[0]["outletProfileEvidence"]))
    return stack


def fixture(root, *, default_faces=True):
    """Genuine miniature polyMesh plus synthetic, labelled native histories."""
    source = root / "baseline"; source.mkdir()
    helper = baseline_tests.TeachingBaselineReduction(); helper.fixture(source)
    mesh_root, _ = source_fixture(root)
    data = {name: native_component(name, (mesh_root / (name + ".msh")).read_bytes()) for name in ("impeller", "volute")}
    write_native(source / "impeller-case", data, merged=True)
    if not default_faces:
        path = source / "impeller-case/constant/polyMesh/boundary"
        count, body = case.base.native_list(path)
        body = case.re.sub(r"defaultFaces\s*\{[^{}]*\}", "", body)
        path.write_text(case.base.foam_header("boundary", "polyBoundaryMesh") + str(count - 1) + "\n(" + body + ")\n")
    components = []
    for name in ("impeller", "volute"):
        components.append({"component": name, "caseDirectory": name + "-case", "cellCount": data[name]["cells"],
            "nodeCount": len(data[name]["points"]), "patchFaces": {p: len(rows) for p, rows in data[name]["patches"].items()},
            "cellZone": "rotating-fluid" if name == "impeller" else "stationary-fluid"})
    prep, _ = case.json_file(source, case.baseline.PREPARATION_FILE); prep["components"] = components
    prep["outletProfileEvidence"] = {"profile": deepcopy(case.outlet.PROFILE),
        "geometryReportFile": case.read(source, "source-teaching/source-geometry/teaching-input-manifest.json")[1],
        "nativeMeshFileRecords": [], "fixtureKind": "SYNTHETIC-UPSTREAM-OUTLET-GEOMETRY-PROOF-ONLY"}
    prep_row = save(source, case.baseline.PREPARATION_FILE, prep)
    bound = case.baseline.build_manifest(source)
    packet_rows = {}
    for path, key in ((case.baseline.GEOMETRY_FILE, "geometryIdentity"), (case.baseline.MANIFEST_FILE, "manifest"), (case.baseline.STUDY_FILE, "study")):
        packet_rows[path] = save(source, path, bound[key])
    path = source / "logs/02-checkMesh.log"
    path.write_text(path.read_text().replace("cells: 2", "cells: 12"))
    execution, _ = case.json_file(source, case.baseline.EXECUTION_FILE)
    log = case.read(source, "logs/02-checkMesh.log")[1]
    execution["stages"][2].update(logBytes=log["bytes"], logSha256=log["sha256"])
    execution.update(preparationReportSha256=prep_row["sha256"], inputManifestSha256=packet_rows[case.baseline.MANIFEST_FILE]["sha256"],
        studySha256=packet_rows[case.baseline.STUDY_FILE]["sha256"], geometryIdentitySha256=packet_rows[case.baseline.GEOMETRY_FILE]["sha256"],
        mergedNativeMeshEvidence=case.base.verify_merged_mesh(source / "impeller-case", components, path))
    helper.update_execution(source, execution)
    packet = case.baseline.reduce_baseline(source)
    if not packet.get("numericalBaselineQualified"):
        raise AssertionError(packet["diagnostics"])
    save(source, case.baseline.REPORT_FILE, packet)
    return source


def cache_fixture(root):
    mesh_root, _ = source_fixture(root)
    data = {name: native_component(name, (mesh_root / (name + ".msh")).read_bytes()) for name in ("impeller", "volute")}
    write_native(root / "case", data, merged=True)
    patches = case.geometry.boundary_entries(root / "case/constant/polyMesh/boundary")
    body = []
    for name, row in patches.items():
        if name not in case.NCC_PATCHES:
            body.append(name + " {type polyFaces;}")
        else:
            kind = case.NCC_PATCHES[name]
            first = patches[name.split("_on_", 1)[1]]["startFace"]
            value = "size 1; value nonuniform List<label> 1(" + str(first) + ");" if kind == "nonConformalCyclic" else "size 0; value nonuniform List<label> 0();"
            body.append(name + " {type nonConformalPolyFaces; patchType " + kind + "; " + value + "}")
    raw = (case.base.foam_header("polyFaces", "surfaceLabelField", "constant/fvMesh")
        + "dimensions [];\ninternalField uniform -1;\nboundaryField\n{\n" + "\n".join(body) + "\n}\n").encode("ascii")
    return raw, patches


def startup_field(name, patches, *, internal="uniform 0", dimensions=None):
    dimension = case.STARTUP_FIELDS[name]["dimensions"] if dimensions is None else dimensions
    blocks = []
    for patch_name, row in patches.items():
        body = "type " + case.NCC_PATCHES[patch_name] + ";" if patch_name in case.NCC_PATCHES else "type calculated; value uniform 0;"
        blocks.append(patch_name + " {" + body + "}")
    return (case.base.foam_header(name, "volScalarField", "0") + "dimensions [" + dimension + "];\ninternalField "
        + internal + ";\nboundaryField\n{\n" + "\n".join(blocks) + "\n}\n").encode("ascii")


class TeachingPhaseCaseTests(unittest.TestCase):
    def context(self, temp):
        stack = mocked_producer_admission()
        stack.enter_context(patch.dict(os.environ, {"RUNNER_TEMP": temp}))
        return stack

    def test_native_region_diagnostic_binds_complete_connectivity_log_and_zones(self):
        data = region_fixture(); proof = case.native_cell_to_region(*data)
        self.assertEqual(proof["allNativeCellLabelsChecked"], 4)
        self.assertEqual(proof["regionToCellZone"], {"0": {"cellZone": "rotating-fluid", "cellCount": 2},
            "1": {"cellZone": "stationary-fluid", "cellCount": 2}})
        self.assertFalse(proof["authoredInput"]); self.assertFalse(proof["initialSolverState"])
        self.assertFalse(proof["flowCalculated"]); self.assertFalse(proof["componentOffsetsAssumed"])
        swapped = data[0].replace(b"4 (0 0 1 1)", b"4 (1 1 0 0)")
        remapped = case.native_cell_to_region(swapped, *data[1:])
        self.assertEqual(remapped["regionToCellZone"]["0"]["cellZone"], "stationary-fluid")

    def test_native_region_wrong_role_count_labels_adjacency_zone_or_log_rejects(self):
        data = region_fixture()
        mutations = [(0, b"labelList", b"volScalarField"), (0, b'location "0";', b'location "0.1";'),
            (0, b"4 (0 0 1 1)", b"3 (0 1 1)"), (0, b"4 (0 0 1 1)", b"4 (0 0 1 2)"),
            (0, b"4 (0 0 1 1)", b"4 (0 1 1 1)"), (0, b"4 (0 0 1 1)", b"4 (0 0 1.0 1)"),
            (0, b"4 (0 0 1 1)", b"4 (0 0 1 1)\n#include forbidden"),
            (0, b"object cellToRegion;", b"object cellToRegion; secret credential;"),
            (2, b"2 (1 3)", b"1 (1)"), (3, b"List<label> 2 (0 1)", b"List<label> 2 (0 2)"),
            (4, b"cells: 4", b"cells: 5"), (4, b"*Number of regions: 2", b"*Number of regions: 3"),
            (4, b"region 1 (fully disconnected) with 2", b"region 1 (fully disconnected) with 1"),
            (4, b'"0/cellToRegion"', b'"0/other"'), (4, b"End\n", b""),
            (4, b"Mesh OK.", b"FOAM FATAL ERROR")]
        for index, old, new in mutations:
            with self.subTest(index=index, replacement=new):
                altered = list(data); altered[index] = altered[index].replace(old, new)
                with self.assertRaises(ValueError): case.native_cell_to_region(*altered)

    def test_generated_region_record_is_postnative_only_and_missing_writer_data_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); write_region_fixture(root)
            with self.assertRaises(ValueError): case.native_region_data(root)
            rows, evidence = case.native_region_data(root, after_native_execution=True)
            self.assertEqual(rows, [case.read(root, case.NATIVE_REGION_PATH)[1]])
            self.assertEqual(evidence[0]["sourceCheckMeshLog"], case.read(root, "logs/00-checkMesh.log")[1])
            self.assertEqual(len(evidence[0]["sourceMeshFileRecords"]), 3)
            self.assertEqual(case.native_generated_data(root, after_native_execution=True), (rows, evidence))
            (root / case.NATIVE_REGION_PATH).unlink()
            with self.assertRaises(ValueError): case.native_region_data(root, after_native_execution=True)
            write_region_fixture(root); (root / "logs/00-checkMesh.log").unlink()
            with self.assertRaises(ValueError): case.native_region_data(root, after_native_execution=True)

    def test_pure_recipe_uses_active_absolute_pa_and_stationary_intake(self):
        study = case.baseline.study_packet({}, {})
        with patch.object(case.protocol, "prepare_study", side_effect=AssertionError("Old source constructor must never be used")):
            authored = case.native_recipe(study)
        self.assertIn(b"dimensions [1 -1 -2 0 0 0 0]", authored["0/p_rgh"])
        self.assertIn(b"internalField uniform 300000;", authored["0/p_rgh"])
        self.assertIn(b"p0 uniform 300000;", authored["0/p_rgh"])
        self.assertIn(b"pRef 0;", authored["system/controlDict"])
        self.assertNotIn(b"600000", authored["0/p_rgh"])
        self.assertIn(b"model SchnerrSauer;", authored["constant/fvModels"])
        self.assertIn(b"pSat 2339;", authored["constant/fvModels"])
        self.assertNotIn(b"nonRotatingPatches", authored["constant/MRFProperties"])
        _, walls = case.baseline_case.fields.boundary_blocks(authored['0/U'], 'U')
        self.assertEqual(walls['inlet']['type'], 'pressureInletOutletVelocity')
        self.assertEqual(walls['mass-flow-inlet-11']['type'], 'fixedValue')
        self.assertEqual(walls['mass-flow-inlet-11']['value'],
            'uniform (0 ' + format(90 / 998.2 / (.04363 * (.12 - .01)), '.17g') + ' 0)')
        self.assertNotIn(b'flowRateOutletVelocity', authored['0/U'])
        self.assertEqual(walls['inblock-hub']['type'], 'noSlip'); self.assertEqual(walls['inblock-shroud']['type'], 'noSlip')
        for wall in ('blade', 'impeller-hub', 'impeller-shroud'): self.assertEqual(walls[wall]['type'], 'MRFnoSlip')
        self.assertIn(b"omega 1450 [rpm];", authored["constant/MRFProperties"])
        self.assertIn(b"wallDist {method meshWave;}", authored["system/fvSchemes"])
        self.assertEqual(len(re.findall(rb"\bwallDist\s*\{", authored["system/fvSchemes"])), 1)
        self.assertNotIn("0/p", authored); self.assertNotIn("constant/dynamicMeshDict", authored)
        self.assertIn(b"internalField uniform 1;", authored["0/alpha.water"])
        self.assertIn(b"internalField uniform (0 0 0);", authored["0/U"])
        expected = set().union(*case.base.PATCHES.values()) | set(case.NCC_PATCHES)
        for name in ("U", "p_rgh", "alpha.water", "k", "omega", "nut"):
            _, rows = case.baseline_case.fields.boundary_blocks(authored["0/" + name], name)
            self.assertEqual(set(rows), expected)
            for patch_name, kind in case.NCC_PATCHES.items(): self.assertEqual(rows[patch_name]["type"], kind)
        self.assertFalse(case.TEMPORAL_PLAN["durationIsAcceptanceProof"])
        self.assertFalse(case.TEMPORAL_PLAN["physicalRotorMotion"])

    def test_current_real_math_and_fixture_topology_are_reduced_before_and_after_copy(self):
        with tempfile.TemporaryDirectory() as temp, self.context(temp):
            root = Path(temp); source = fixture(root)
            with patch.object(case.baseline, "reduce_baseline", wraps=case.baseline.reduce_baseline) as reducer:
                report = case.prepare(source, root / "phase")
                verified = case.verify_output(root / "phase")
                self.assertGreaterEqual(reducer.call_count, 3)
            self.assertEqual(verified["report"], report)
            self.assertTrue(all(report[key] is False for key in case.FALSE_FLAGS))
            self.assertTrue(all(report[key] is True for key in case.SOURCE_TRUE_FLAGS))
            self.assertEqual(report["sourceGeometryRoute"], "CLASSROOM_AUTHORED_GEOMETRY")
            self.assertEqual(report["coordinateUnits"], "SI_METRES")
            self.assertEqual(report["sourceReference"]["kind"], "classroom-authored-SI-pump-CAD")
            self.assertNotIn("exampleDataCommit", report); self.assertNotIn("fluent.docs", str(report))
            self.assertEqual(report["absolutePressureMapping"]["referenceAdditionPa"], 0)
            self.assertFalse((root / "phase/case/500").exists()); self.assertFalse((root / "phase/case/0/p").exists())
            self.assertTrue((root / "phase/source-baseline/impeller-case/500/U").exists())
            for row in report["sourceEvidenceFileRecords"]: self.assertEqual(case.read(root / "phase", row["path"])[1], row)
            for row in report["preservedNativeMeshFiles"]:
                self.assertEqual(case.read(root / "phase", "case/" + row["path"])[1], {**row, "path": "case/" + row["path"]})

    def test_relocated_complete_phase_output_reads_only_current_bytes(self):
        with tempfile.TemporaryDirectory() as temp, self.context(temp):
            root = Path(temp); source = fixture(root); output = root / "phase"; report = case.prepare(source, output)
            before = {p.relative_to(output).as_posix(): sha256(p.read_bytes()).hexdigest() for p in output.rglob("*") if p.is_file()}
            shutil.rmtree(source); moved = root / "moved-phase"; output.rename(moved)
            self.assertEqual(case.verify_output(moved)["report"], report)
            self.assertEqual(before, {p.relative_to(moved).as_posix(): sha256(p.read_bytes()).hexdigest() for p in moved.rglob("*") if p.is_file()})
            self.assertNotEqual(report["nativeCommandArguments"], case.commands(moved))
            self.assertEqual(case.commands(moved)[0][2], str(moved / "case"))

    def test_complete_historical_tuple_is_lexical_and_exact(self):
        root = "/home/runner/work/_temp/phase"
        report = {"nativeCommandArguments": case.historical_commands(root)}
        self.assertEqual(case.historical_command_root(report), root)
        for kind in ("other-root", "missing-flag", "wrong-program", "traversal", "directive"):
            with self.subTest(kind=kind):
                bad = deepcopy(report)
                if kind == "other-root": bad["nativeCommandArguments"][1][2] = "/home/other/case"
                elif kind == "missing-flag": bad["nativeCommandArguments"][0].pop()
                elif kind == "wrong-program": bad["nativeCommandArguments"][1][0] = "bash"
                elif kind == "traversal": bad["nativeCommandArguments"][0][2] = "/home/../case"
                else: bad["nativeCommandArguments"][0][2] = "/home/bad;command/case"
                with self.assertRaises(ValueError): case.historical_command_root(bad)

    def test_optional_empty_default_is_measured_and_no_other_placeholder_is_added(self):
        for present in (True, False):
            with self.subTest(present=present), tempfile.TemporaryDirectory() as temp, self.context(temp):
                root = Path(temp); source = fixture(root, default_faces=present); report = case.prepare(source, root / "phase")
                self.assertEqual(report["defaultFacesPresent"], present)
                self.assertEqual(b'"defaultFaces"' in (root / "phase/case/0/U").read_bytes(), present)
                self.assertNotIn(b'"backgroundBox"', (root / "phase/case/0/U").read_bytes())

    def test_failed_or_relabelled_baseline_is_rejected_before_mkdir(self):
        for kind in ("old-schema", "false-actual", "false-numerical", "false-preliminary", "false-liquid", "false-coverage", "true-qualified", "stale"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp, self.context(temp):
                root = Path(temp); source = fixture(root); value, _ = case.json_file(source, case.baseline.REPORT_FILE)
                if kind == "old-schema": value["schemaVersion"] = "untirta.frozen-rotor-snappy-baseline-reduction.v1"
                elif kind == "true-qualified": value["qualifiedBaseline"] = True
                elif kind != "stale": value[{"false-actual": "actualCfdRunVerified", "false-numerical": "numericalBaselineQualified",
                    "false-preliminary": "preliminaryBaselineQualified", "false-liquid": "noncavitatingBaselineVerified", "false-coverage": "interfaceCoverageVerified"}[kind]] = False
                save(source, case.baseline.REPORT_FILE, value)
                if kind == "stale":
                    fresh = deepcopy(value); fresh["numericalBaselineQualified"] = False
                    with patch.object(case.baseline, "reduce_baseline", return_value=fresh), self.assertRaises(ValueError): case.prepare(source, root / "phase")
                else:
                    with self.assertRaises(ValueError): case.prepare(source, root / "phase")
                self.assertFalse((root / "phase").exists())

    def test_source_mesh_control_or_endpoint_bytes_cannot_inherit_stale_acceptance(self):
        for relative in ("impeller-case/constant/polyMesh/points", "impeller-case/constant/MRFProperties", "impeller-case/500/U"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temp, self.context(temp):
                root = Path(temp); source = fixture(root); (source / relative).write_bytes(b"Changed actual source bytes\n")
                with self.assertRaises(ValueError): case.prepare(source, root / "phase")
                self.assertFalse((root / "phase").exists())

    def test_changed_si_model_mapping_and_missing_physical_bc_are_rejected(self):
        for kind in ("rpm", "density", "gravity", "datum", "old-reference"):
            with self.subTest(kind=kind):
                study = case.baseline.study_packet({}, {})
                if kind == "gravity": study["physicalBasis"]["gravityVectorMS2"] = [0, 0, -9.81]
                elif kind == "old-reference": study["sourceReference"]["kind"] = "official-educational-example"
                else: study["input"][{"rpm": "speedRpm", "density": "densityKgM3", "datum": "suctionDatumM"}[kind]] += 1
                with self.assertRaises(ValueError): case.native_recipe(study)
        for kind in ("double-pressure", "missing-wall", "outlet-profile", "model", "reference", "source", "mesh", "new-p", "dynamic", "extra-dictionary"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp, self.context(temp):
                root = Path(temp); source = fixture(root); output = root / "phase"; case.prepare(source, output)
                if kind == "double-pressure":
                    path = output / "case/0/p_rgh"; path.write_bytes(path.read_bytes().replace(b"300000", b"600000"))
                elif kind == "missing-wall":
                    path = output / "case/0/U"; path.write_bytes(re.sub(rb'    "blade"\s*\{[^{}]*\}', b"", path.read_bytes()))
                elif kind == "outlet-profile":
                    value, _ = case.json_file(output, case.REPORT); value["outletProfileEvidence"]["profile"]["fixtureTamper"] = True; save(output, case.REPORT, value)
                elif kind == "model":
                    path = output / "case/constant/fvModels"; path.write_bytes(path.read_bytes().replace(b"SchnerrSauer", b"otherModel"))
                elif kind == "reference":
                    value, _ = case.json_file(output, case.REPORT); value["absolutePressureMapping"]["referenceAdditionPa"] = 300000; save(output, case.REPORT, value)
                elif kind == "source": (output / "source-baseline/impeller-case/500/U").write_bytes(b"Changed source end state")
                elif kind == "mesh": (output / "case/constant/polyMesh/points").write_bytes(b"Changed coupled mesh")
                elif kind == "new-p": (output / "case/0/p").write_bytes(b"Baseline gauge pressure cannot be imported")
                elif kind == "dynamic": (output / "case/constant/dynamicMeshDict").write_bytes(b"Unexpected sliding control")
                else: (output / "case/constant/fvConstraints").write_bytes(b"Unexpected unbound physical constraint")
                with self.assertRaises(ValueError): case.verify_output(output)

    def test_source_mutation_during_copy_cleans_only_fresh_output(self):
        with tempfile.TemporaryDirectory() as temp, self.context(temp):
            root = Path(temp); source = fixture(root); original_write = case.base.write_exclusive; changed = False
            def copying(path, raw):
                nonlocal changed
                original_write(path, raw)
                if not changed and "source-baseline" in path.parts:
                    changed = True; (source / "logs/02-checkMesh.log").write_bytes(b"Changed original after first transfer")
            with patch.object(case.base, "write_exclusive", side_effect=copying), self.assertRaises(ValueError): case.prepare(source, root / "phase")
            self.assertFalse((root / "phase").exists()); self.assertTrue(source.is_dir())
            self.assertEqual((source / "logs/02-checkMesh.log").read_bytes(), b"Changed original after first transfer")

    def test_inventory_paths_limits_and_unknown_report_keys_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp, self.context(temp):
            root = Path(temp); source = fixture(root)
            for path in ("../escape", "/absolute", "bad\\path", ".hidden"):
                with self.subTest(path=path), self.assertRaises(ValueError): case.records(source, [{"path": path, "bytes": 1, "sha256": "a" * 64}])
            with patch.object(case, "MAX_SOURCE_RECORDS", 1), self.assertRaises(ValueError): case.prepare(source, root / "too-many")
            self.assertFalse((root / "too-many").exists())
            output = root / "phase"; case.prepare(source, output); value, _ = case.json_file(output, case.REPORT)
            value["unreviewedClaim"] = True; save(output, case.REPORT, value)
            with self.assertRaises(ValueError): case.verify_output(output)

    def test_native_polyfaces_data_only_header_ranges_and_dimensionless_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); raw, patches = cache_fixture(root); mesh = root / "case/constant/polyMesh"
            for dimension in (b"[]", b"[0 0 0 0 0 0 0]"):
                checked = case.native_polyfaces(raw.replace(b"[]", dimension), mesh)
                self.assertTrue(checked["dataOnlyVerified"]); self.assertFalse(checked["authoredInput"])
                self.assertFalse(checked["geometryReconstructed"]); self.assertEqual(checked["nativePatchCount"], len(patches))
                self.assertEqual(checked["nativeIntersectionFaceCount"], 2)
                self.assertTrue(all(row["allMappingLabelsChecked"] for row in checked["nonconformalPatchMappings"]))
            first = patches["interface-impeller-outlet"]["startFace"]
            self.assertTrue(case.native_polyfaces(raw.replace(f"nonuniform List<label> 1({first})".encode(), f"uniform {first}".encode()), mesh)["dataOnlyVerified"])

    def test_native_cache_unknown_roles_code_and_wrong_original_patch_labels_reject(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); raw, patches = cache_fixture(root); mesh = root / "case/constant/polyMesh"
            first = patches["interface-impeller-outlet"]["startFace"]
            other = patches["interface-volute-inlet"]["startFace"]
            variants = [raw.replace(b"surfaceLabelField", b"volScalarField"), raw.replace(b"object polyFaces;", b"object another;"),
                raw.replace(b"ascii", b"binary"), raw.replace(b'"constant/fvMesh"', b'"constant/polyMesh"'),
                raw.replace(b"[]", b"[1 0 0 0 0 0 0]"), raw.replace(b"uniform -1", b"uniform 0"),
                raw.replace(b"dimensions []", b"dimensions []; dimensions []"),
                raw.replace(b"inlet {type polyFaces;}", b"inlet {type polyFaces; codeStream execute;}"),
                raw.replace(b"inlet {type polyFaces;}", b"inlet {type polyFaces;} inlet {type polyFaces;}"),
                raw.replace(b"inlet {type polyFaces;}", b""),
                raw.replace(f"1({first})".encode(), f"1({other})".encode()),
                raw.replace(f"1({first})".encode(), f"2({first})".encode()),
                raw.replace(b"patchType nonConformalCyclic;", b"patchType coded;"),
                raw + b"\n#include external\n"]
            for index, changed in enumerate(variants):
                with self.subTest(index=index), self.assertRaises(ValueError): case.native_polyfaces(changed, mesh)

    def test_cache_is_postnative_evidence_and_unknown_cache_files_remain_prohibited(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); raw, _ = cache_fixture(root)
            self.assertEqual(case.native_generated_data(root), ([], []))
            row = write(root, case.NATIVE_CACHE_PATH, raw)
            with self.assertRaises(ValueError): case.native_generated_data(root)
            rows, proof = case.native_generated_data(root, after_native_execution=True)
            self.assertEqual(rows, [row]); self.assertEqual(proof[0]["path"], case.NATIVE_CACHE_PATH)
            write(root, "case/constant/fvMesh/fvConstraints", b"Unknown native dictionary")
            with self.assertRaises(ValueError): case.native_generated_data(root, after_native_execution=True)
            with self.assertRaises(ValueError): case.native_generated_data(root, after_native_execution=1)

    def test_completed_cache_does_not_change_authored_controls_or_false_preparation_flags(self):
        with tempfile.TemporaryDirectory() as temp, self.context(temp):
            root = Path(temp); source = fixture(root); output = root / "phase"; report = case.prepare(source, output)
            cache_root = root / "cache-only"; cache_root.mkdir(); raw, _ = cache_fixture(cache_root)
            row = write(output, case.NATIVE_CACHE_PATH, raw)
            with self.assertRaises(ValueError): case.verify_output(output)
            current = case.verify_output(output, after_native_execution=True)
            self.assertEqual(current["report"], report); self.assertEqual(current["nativeGeneratedDataRecords"], [row])
            self.assertTrue(all(report[key] is False for key in case.FALSE_FLAGS))
            self.assertNotIn("constant/fvMesh/polyFaces", {r["path"] for r in report["authoredInputFiles"]})
            write(output, "case/constant/fvConstraints", b"Unknown physical constraint")
            with self.assertRaises(ValueError): case.verify_output(output, after_native_execution=True)

    def test_native_startup_results_bind_exact_fields_si_shape_and_source_objects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, patches = cache_fixture(root)
            authored = case.native_recipe(case.baseline.study_packet({}, {}), default_faces=True)
            write(root, "case/system/controlDict", authored["system/controlDict"])
            expected = []
            for name in case.STARTUP_FIELDS:
                expected.append(write(root, "case/0/" + name, startup_field(name, patches)))
            with self.assertRaises(ValueError): case.native_startup_fields(root)
            rows, proof = case.native_startup_fields(root, after_native_execution=True)
            self.assertEqual({r["path"] for r in rows}, {r["path"] for r in expected})
            self.assertTrue(all(p["allInternalCellsRepresented"] == 12 for p in proof))
            self.assertTrue(all(p["dataOnlyVerified"] and not p["authoredInput"] and not p["numericalAcceptanceInherited"] for p in proof))
            self.assertEqual({p["resultField"] for p in proof}, set(case.STARTUP_FIELDS))
            for item in proof: self.assertEqual(item["sourceControlFile"], case.read(root, "case/system/controlDict")[1])

    def test_generated_startup_wrong_shapes_units_names_negative_values_and_code_reject(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, patches = cache_fixture(root)
            good = startup_field("U2", patches)
            variants = [startup_field("U2", patches, internal="uniform -1"), startup_field("U2", patches, dimensions="1 -1 -2 0 0 0 0"),
                startup_field("U2", patches, internal="nonuniform List<scalar> 2(0 0)"),
                startup_field("U2", patches, internal="uniform 1e999"), good.replace(b"object U2;", b"object another;"),
                good.replace(b"volScalarField", b"volVectorField"), good.replace(b"ascii", b"binary"),
                good.replace(b'type calculated;', b'type codedFixedValue;'), good + b"\n#codeStream execute\n",
                good.replace(b"inlet {type calculated; value uniform 0;}", b"inlet {type calculated; value uniform 0; codeExecute anything;}"),
                good.replace(b"inlet {type calculated; value uniform 0;}", b""),
                good.replace(b'dimensions [0 2 -2 0 0 0 0];', b'dimensions [0 2 -2 0 0 0 0]; dimensions [];')]
            for index, raw in enumerate(variants):
                with self.subTest(index=index), self.assertRaises(ValueError): case.startup_scalar_field(raw, "U2", 12, patches)
            self.assertEqual(case.startup_scalar_field(startup_field("U2", patches, internal="nonuniform List<scalar> 12(" + "0 "*12 + ")"), "U2", 12, patches)["storedInternalScalarCount"], 12)
            with self.assertRaises(ValueError): case.startup_scalar_field(good, "p", 12, patches)
            ncc = 'pumpInterface_on_interface-impeller-outlet'
            mapped = good.replace((ncc+' {type nonConformalCyclic;}').encode(),
                (ncc+' {type nonConformalCyclic; value nonuniform List<scalar> 1(0);}').encode())
            with self.assertRaises(ValueError): case.startup_scalar_field(mapped, 'U2', 12, patches)
            self.assertTrue(case.startup_scalar_field(mapped, 'U2', 12, patches, ncc_counts={ncc:1})['allStoredValuesFinite'])
            with self.assertRaises(ValueError): case.startup_scalar_field(mapped, 'U2', 12, patches, ncc_counts={ncc:2})
            authored = case.native_recipe(case.baseline.study_packet({}, {})); write(root, "case/system/controlDict", authored["system/controlDict"].replace(b"result U2;", b"result another;"))
            write(root, "case/0/U2", good)
            with self.assertRaises(ValueError): case.native_startup_fields(root, after_native_execution=True)

    def test_generated_startup_is_postnative_only_and_never_becomes_initial_p_or_model(self):
        with tempfile.TemporaryDirectory() as temp, self.context(temp):
            root = Path(temp); source = fixture(root); output = root / "phase"; report = case.prepare(source, output)
            patches = case.geometry.boundary_entries(output / "case/constant/polyMesh/boundary")
            for name in case.STARTUP_FIELDS: write(output, "case/0/" + name, startup_field(name, patches))
            with self.assertRaises(ValueError): case.verify_output(output)
            verified = case.verify_output(output, after_native_execution=True)
            self.assertEqual(verified["report"], report); self.assertEqual(len(verified["nativeGeneratedDataRecords"]), 3)
            self.assertTrue(all(row["path"].startswith("case/0/") for row in verified["nativeGeneratedDataRecords"]))
            self.assertTrue(all(report[key] is False for key in case.FALSE_FLAGS))
            self.assertTrue(all("0/" + name not in {r["path"] for r in report["authoredInputFiles"]} for name in case.STARTUP_FIELDS))
            write(output, "case/0/p", b"No source-proven initial p writer allowance")
            with self.assertRaises(ValueError): case.verify_output(output, after_native_execution=True)


if __name__ == "__main__": unittest.main()
