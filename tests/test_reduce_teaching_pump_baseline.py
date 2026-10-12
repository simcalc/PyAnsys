"""Synthetic source/topology mocks and real parser maths; never native CFD proof."""
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import reduce_teaching_pump_baseline as tool


class TeachingBaselineReduction(unittest.TestCase):
    def write(self, root, name, value):
        raw = value if isinstance(value, bytes) else value.encode("ascii")
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        return {"path": name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}

    def json(self, root, name, value):
        return self.write(root, name, tool.encoded(value))

    def context(self):
        stack = ExitStack(); helper = tool.preparer()
        def checked(root, **_):
            p, row = tool.record(root, tool.PREPARATION_FILE)
            return {"report": p, "reportFile": row, "components": p["components"]}
        stack.enter_context(patch.object(helper, "verify_output", side_effect=checked))
        stack.enter_context(patch.object(tool, "binary_identity"))
        stack.enter_context(patch.object(tool.native.case_tool, "verify_merged_mesh", return_value={
            "mergedCellCount": 2, "mergedPointCount": 2, "fixtureKind": "SYNTHETIC-NOT-NATIVE-MESH"}))
        return stack

    def table(self, patch, columns, values):
        header = f"# Selection : patch({patch})\n# Faces : 3\n# Area : 0.5\n# Time " + " ".join(columns) + "\n"
        return header + "".join(str(i) + " " + " ".join(format(v, ".17g") for v in values) + "\n" for i in range(481, 501))

    def fixture(self, root, *, residual=1e-5, pressure_min=10):
        helper = tool.preparer(); plan = tool.author.teaching_plan()
        src = self.json(root, "source-teaching/source-geometry/teaching-input-manifest.json", {
            "schemaVersion": tool.author.MANIFEST_SCHEMA, "provenance": tool.ROUTE, "units": "SI",
            "parameters": plan["parameters"], "meshLevel": "coarse", "geometryIdentity": plan["geometryIdentity"],
            "geometryIdentitySha256": plan["geometryIdentitySha256"], "generatorSourceCommit": "a" * 40,
            "fixtureKind": "SYNTHETIC-NOT-CAD-EVIDENCE"})
        source_exec = self.json(root, "source-teaching/teaching-native-execution.json", {"githubRepository": "simcalc/PyAnsys",
            "githubRunId": "123456789", "githubSourceCommit": "a" * 40, "fixtureKind": "SYNTHETIC-NOT-ACTUAL-EXECUTION"})
        authored = []; retained = []
        for comp in ("impeller", "volute"):
            for path, raw in helper.case_files(comp).items():
                authored.append(self.write(root, comp + "-case/" + path, raw))
                retained.append(self.write(root, "authored-physics/" + comp + "-case/" + path, raw))
        for name in tool.MESH_FILES:
            self.write(root, "impeller-case/constant/polyMesh/" + name, "SYNTHETIC-TOPOLOGY-MOCK:" + name)
        prep = {"schemaVersion": tool.PREPARATION_SCHEMA, "sourceGeometryRoute": tool.ROUTE, "mainApplicationWrites": False,
            "classroomAssumptions": helper.ASSUMPTIONS, "sourceGeometryIdentitySha256": plan["geometryIdentitySha256"],
            "sourceGeometryExecutionReportSha256": source_exec["sha256"], "sourceEvidenceFileRecords": [src, source_exec],
            "authoredInputFiles": authored, "authoredPhysicsFileRecords": retained,
            "components": [{"component": "impeller", "cellCount": 1, "nodeCount": 1}, {"component": "volute", "cellCount": 1, "nodeCount": 1}]}
        prep_row = self.json(root, tool.PREPARATION_FILE, prep)
        runtime_row = self.json(root, tool.RUNTIME_FILE, {"schemaVersion": "SYNTHETIC-NOT-INSTALLED-RUNTIME"})
        bound = tool.build_manifest(root); rows = {}
        for name, key in ((tool.GEOMETRY_FILE, "geometryIdentity"), (tool.MANIFEST_FILE, "manifest"), (tool.STUDY_FILE, "study")):
            rows[name] = self.json(root, name, bound[key])
        prefix = "Build : 12-" + tool.geometry_runner.safety.SOURCE_COMMIT[:12] + "\n"
        solver = prefix
        for i in range(481, 501):
            solver += f"Time = {i}s\n"
            for name in ("Ux", "Uy", "Uz", "p", "k", "omega"):
                solver += f"solver: Solving for {name}, Initial residual = {residual}, Final residual = 1e-10, No Iterations 2\n"
            solver += "solver: Solving for p, Initial residual = 1e-10, Final residual = 1e-12, No Iterations 1\n"
            solver += "time step continuity errors : sum local = 1e-6, global = -1e-7, cumulative = -1e-5\n"
        solver += "End\n"
        mesh = prefix + "cells: 2\n*Number of regions: 2\nMin volume = 1e-6. Max volume = 2e-6. Total volume = 3e-6.\nMesh OK.\nSource min/average/max coverage = 1/1/1\nTarget min/average/max coverage = 1/1/1\nEnd\n"
        stages = []
        for i, program in enumerate(tool.PROGRAMS):
            row = self.write(root, f"logs/{i:02d}-{program}.log", solver if program == "foamRun" else mesh if program == "checkMesh" else prefix + "End\n")
            stages.append({"program": program, "exitCode": 0, "elapsedSeconds": 1,
                "log": Path(row["path"]).name, "logBytes": row["bytes"], "logSha256": row["sha256"]})
        dims = {"p": "0 2 -2 0 0 0 0", "U": "0 1 -1 0 0 0 0", "k": "0 2 -2 0 0 0 0", "omega": "0 0 -1 0 0 0 0", "nut": "0 2 -1 0 0 0 0"}
        for name, dim in dims.items():
            kind = "volVectorField" if name == "U" else "volScalarField"
            value = "(1 2 3)" if name == "U" else "100" if name == "p" else "0" if name == "nut" else "1"
            self.write(root, "impeller-case/500/" + name, f"FoamFile {{format ascii; class {kind}; object {name};}}\ndimensions [{dim}];\ninternalField uniform {value};\nboundaryField {{}}\n")
        rho, q = 998.2, 90 / 998.2
        values = {"flowInlet": [-q], "flowOutlet": [q], "flowMagnitudeInlet": [q], "flowMagnitudeOutlet": [q],
            "weightedInlet": [10, tool.PRESSURE + rho * 10 + rho * 4 / 2, 4],
            "weightedOutlet": [210, tool.PRESSURE + rho * 210 + rho * 9 / 2, 9]}
        for name, (patch_name, columns) in tool.native.SAMPLERS.items():
            self.write(root, f"impeller-case/postProcessing/{name}/0/surfaceFieldValue.dat", self.table(patch_name, columns, values[name]))
        self.write(root, "impeller-case/postProcessing/globalPressureMinimum/0/volFieldValue.dat", "# Time min(p)\n" + "".join(f"{i} {pressure_min}\n" for i in range(481, 501)))
        for name, patch_name, value in zip(("flowRotorInterface", "flowStatorInterface"), tool.native.COUPLED_PATCHES, (q, -q)):
            self.write(root, f"impeller-case/postProcessing/{name}/0/surfaceFieldValue.dat", self.table(patch_name, ("sum(phi)",), [value]))
        import run_teaching_pump_baseline as runner
        fields, field_rows, scans = runner.final_fields(root, 2)
        execution = {"schemaVersion": tool.EXECUTION_SCHEMA, "status": "TEACHING_MRF_EXECUTED_NUMERICAL_ACCEPTANCE_REQUIRED",
            "sourceGeometryRoute": tool.ROUTE, "preparationReportSha256": prep_row["sha256"], "runtimeIdentitySha256": runtime_row["sha256"],
            "sourceGeometryExecutionReportSha256": source_exec["sha256"], "inputManifestSha256": rows[tool.MANIFEST_FILE]["sha256"],
            "studySha256": rows[tool.STUDY_FILE]["sha256"], "geometryIdentitySha256": rows[tool.GEOMETRY_FILE]["sha256"],
            **{k: False for k in tool.RAW_FALSE_CLAIMS}, **{k: True for k in ("actualCfdRunVerified", "simulationExecuted",
                "nativeGeometryQualityVerified", "physicalWallTypesVerified", "coupledInterfaceCoverageVerified", "nativeSolverCompleted",
                "finiteNativeFieldsVerified", "inputArtifactsImmutable", "sourceGeometryImmutable")},
            "githubRepository": "simcalc/PyAnsys", "githubRunId": "123456789", "githubSourceCommit": "a" * 40, "stages": stages,
            "finalNativeFields": fields, "finalNativeFieldRecords": field_rows, "finiteNativeFieldEvidence": scans,
            "nativeSamplingRecords": runner.sampling_records(root),
            "nativeNccDiagnosticRecords": runner.ncc_diagnostic_records(root),
            "mergedNativeMeshEvidence": {"mergedCellCount": 2, "mergedPointCount": 2, "fixtureKind": "SYNTHETIC-NOT-NATIVE-MESH"}}
        execution.update(limits=dict(runner.LIMITS), elapsedSeconds=20, workspaceBytes=runner.workspace_size(root))
        self.json(root, tool.EXECUTION_FILE, execution)
        return bound

    def update_execution(self, root, value):
        import run_teaching_pump_baseline as runner
        old = (root / tool.EXECUTION_FILE).stat().st_size
        value["workspaceBytes"] = runner.workspace_size(root) - old
        self.json(root, tool.EXECUTION_FILE, value)

    def update_sample_inventory(self, root):
        import run_teaching_pump_baseline as runner
        value, _ = tool.record(root, tool.EXECUTION_FILE); value["nativeSamplingRecords"] = runner.sampling_records(root)
        self.update_execution(root, value)

    def test_new_authored_reference_and_pure_mathematical_gates(self):
        with tempfile.TemporaryDirectory() as temp, self.context():
            root = Path(temp); self.fixture(root); result = tool.reduce_baseline(root)
            self.assertTrue(result["numericalBaselineQualified"], result["diagnostics"])
            self.assertTrue(result["actualCfdRunVerified"]); self.assertFalse(result["qualifiedBaseline"])
            self.assertEqual(result["study"]["schemaVersion"], tool.STUDY_SCHEMA)
            self.assertEqual(result["study"]["sourceReference"]["kind"], "classroom-authored-SI-pump-CAD")
            self.assertNotIn("fluent.docs", str(result["study"])); self.assertNotIn("exampleDataCommit", result["manifest"])
            self.assertAlmostEqual(result["stages"]["head"]["result"]["value"], 202.5 / 9.81)
            self.assertAlmostEqual(result["stages"]["npsha"]["result"]["value"], (300000 + 9982 - 2339) / (998.2 * 9.81) + 4 / 19.62)
            for key in ("physicalValidationVerified", "meshConvergenceVerified", "wallResolutionVerified", "timeStepConvergenceVerified", "npsh3Qualified", "restartVerified"):
                self.assertFalse(result[key], key)
            self.assertEqual(result["nativeObservation"]["nativeEvidence"]["sha256"], sha256(tool.encoded(result["nativeEvidenceRecords"])).hexdigest())

    def test_missing_actual_source_cannot_generate_results(self):
        with tempfile.TemporaryDirectory() as temp:
            result = tool.reduce_baseline(temp)
            self.assertFalse(result["actualCfdRunVerified"]); self.assertFalse(result["numericalBaselineQualified"])
            self.assertEqual(result["stages"], {})

    def test_native_completion_is_preserved_when_initial_outer_residuals_fail(self):
        with tempfile.TemporaryDirectory() as temp, self.context():
            root = Path(temp); self.fixture(root, residual=.1); result = tool.reduce_baseline(root)
            self.assertTrue(result["actualCfdRunVerified"]); self.assertFalse(result["numericalBaselineQualified"])
            self.assertIn("CFD_RESIDUAL_CONVERGENCE_REQUIRED", str(result["diagnostics"]))
            self.assertEqual(set(result["stages"]), {"head", "npsha"})

    def test_missing_outlet_keeps_independent_inlet_npsha(self):
        with tempfile.TemporaryDirectory() as temp, self.context():
            root = Path(temp); self.fixture(root)
            (root / "impeller-case/postProcessing/weightedOutlet/0/surfaceFieldValue.dat").unlink(); self.update_sample_inventory(root)
            result = tool.reduce_baseline(root)
            self.assertTrue(result["actualCfdRunVerified"]); self.assertFalse(result["numericalBaselineQualified"])
            self.assertEqual(set(result["stages"]), {"npsha"})

    def test_absolute_reference_cannot_be_added_twice(self):
        with tempfile.TemporaryDirectory() as temp, self.context():
            root = Path(temp); self.fixture(root)
            self.write(root, "impeller-case/postProcessing/weightedInlet/0/surfaceFieldValue.dat", self.table("inlet", tool.native.SAMPLERS["weightedInlet"][1], [10, 600000 + 9982 + 1996.4, 4]))
            self.update_sample_inventory(root); result = tool.reduce_baseline(root)
            self.assertFalse(result["numericalBaselineQualified"]); self.assertIn("ABSOLUTE_TOTAL_PRESSURE_MAPPING", str(result["diagnostics"]))

    def test_global_vapour_domain_and_actual_signed_conservation_are_required(self):
        with tempfile.TemporaryDirectory() as temp, self.context():
            root = Path(temp); self.fixture(root, pressure_min=-400); result = tool.reduce_baseline(root)
            self.assertTrue(result["actualCfdRunVerified"]); self.assertFalse(result["numericalBaselineQualified"])
            self.assertIn("NONCAVITATING_BASELINE_REQUIRED", str(result["diagnostics"]))
        with tempfile.TemporaryDirectory() as temp, self.context():
            root = Path(temp); self.fixture(root)
            self.write(root, "impeller-case/postProcessing/flowOutlet/0/surfaceFieldValue.dat", self.table("mass-flow-inlet-11", ("sum(phi)",), [80 / 998.2]))
            self.update_sample_inventory(root); result = tool.reduce_baseline(root)
            self.assertFalse(result["numericalBaselineQualified"]); self.assertIn("MASS_CONSERVATION", str(result["diagnostics"]))

    def test_current_preexecution_packet_and_full_endpoint_fields_must_match(self):
        for mode in ("control", "field", "proof"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp, self.context():
                root = Path(temp); self.fixture(root)
                if mode == "control": (root / "impeller-case/system/controlDict").write_bytes(b"Changed current input\n")
                elif mode == "field": (root / "impeller-case/500/U").write_bytes(b"Changed endpoint\n")
                else:
                    raw, _ = tool.record(root, tool.EXECUTION_FILE); raw["inputManifestSha256"] = "f" * 64; self.json(root, tool.EXECUTION_FILE, raw)
                result = tool.reduce_baseline(root); self.assertFalse(result["actualCfdRunVerified"])
                self.assertFalse(result["numericalBaselineQualified"])

    def test_unknown_sampler_and_omitted_actual_record_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp, self.context():
            root = Path(temp); self.fixture(root)
            self.write(root, "impeller-case/postProcessing/unknownSampler/0/surfaceFieldValue.dat", "# SYNTHETIC\n500 1\n")
            result = tool.reduce_baseline(root); self.assertFalse(result["actualCfdRunVerified"])
            self.update_sample_inventory(root); result = tool.reduce_baseline(root)
            self.assertFalse(result["actualCfdRunVerified"]); self.assertIn("REVIEWED_NATIVE_SAMPLER_PATH", str(result["diagnostics"]))

    def test_native_ncc_local_coverage_remains_an_independent_gate(self):
        with tempfile.TemporaryDirectory() as temp, self.context():
            root = Path(temp); self.fixture(root); path = root / "logs/02-checkMesh.log"
            path.write_text(path.read_text().replace("1/1/1", "0.68/0.9998/1"))
            raw, _ = tool.record(root, tool.EXECUTION_FILE); r = tool.read(root, "logs/02-checkMesh.log")[1]
            raw["stages"][2].update(logBytes=r["bytes"], logSha256=r["sha256"]); self.update_execution(root, raw)
            result = tool.reduce_baseline(root); self.assertFalse(result["numericalBaselineQualified"])
            self.assertIn("NCC_INTERFACE_COVERAGE", str(result["diagnostics"]))

    def test_relocated_current_evidence_keeps_the_new_identity(self):
        with tempfile.TemporaryDirectory() as temp, self.context():
            root = Path(temp); first = root / "original"; first.mkdir(); self.fixture(first)
            expected = tool.reduce_baseline(first); moved = root / "relocated"; shutil.copytree(first, moved)
            self.assertEqual(expected, tool.reduce_baseline(moved))

    def test_geometry_identity_does_not_accept_old_source_or_operating_mismatch(self):
        controls = {k: "a" * 64 for k in tool.protocol.CONTROL_KEYS}; controls["cavitationModelSha256"] = None
        with self.assertRaises(tool.protocol.ProtocolError): tool.study_packet({"speedRpm": 1800}, controls)
        study = tool.study_packet({"bladeCount": 8}, controls)
        self.assertEqual(study["input"]["speedRpm"], 1450)
        with tempfile.TemporaryDirectory() as temp, self.context():
            root = Path(temp); self.fixture(root); path = "source-teaching/source-geometry/teaching-input-manifest.json"
            raw, _ = tool.record(root, path); raw["geometryIdentity"]["originalFluentSourceIdentity"] = "not-authored"
            self.json(root, path, raw); result = tool.reduce_baseline(root)
            self.assertFalse(result["actualCfdRunVerified"]); self.assertFalse(result["numericalBaselineQualified"])

    def test_execution_profile_deadline_and_measured_workspace_are_not_metadata_waivers(self):
        for kind in ("limit", "bool", "deadline", "workspace", "serial", "run", "head"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp, self.context():
                root = Path(temp); self.fixture(root); value, _ = tool.record(root, tool.EXECUTION_FILE)
                if kind == "limit": value["limits"]["maximumSeconds"] = 2400
                elif kind == "bool": value["limits"]["cpus"] = True
                elif kind == "deadline": value["elapsedSeconds"] = 1200
                elif kind == "workspace": value["workspaceBytes"] += 1
                elif kind == "serial": value["elapsedSeconds"] = 1
                elif kind == "run": value["githubRunId"] = "987654321"
                else: value["githubSourceCommit"] = "b" * 40
                self.json(root, tool.EXECUTION_FILE, value); result = tool.reduce_baseline(root)
                self.assertFalse(result["actualCfdRunVerified"]); self.assertFalse(result["numericalBaselineQualified"])

    def test_existing_ncc_diagnostic_cannot_be_omitted_or_changed(self):
        with tempfile.TemporaryDirectory() as temp, self.context():
            root = Path(temp); self.fixture(root)
            for name in ("interface-impeller-outlet", "interface-volute-inlet"):
                self.write(root, "impeller-case/postProcessing/checkMesh/0/" + name + "_coverage.vtk",
                           "# vtk DataFile Version 2.0\nSYNTHETIC-DIAGNOSTIC-NOT-MESH\nASCII\n")
            value, _ = tool.record(root, tool.EXECUTION_FILE); self.update_execution(root, value)
            result = tool.reduce_baseline(root); self.assertFalse(result["actualCfdRunVerified"])
            self.assertIn("NCC_DIAGNOSTIC_INVENTORY", str(result["diagnostics"]))

    def test_verified_near_points_diagnostic_is_separate_bound_native_evidence(self):
        import run_teaching_pump_baseline as runner
        with tempfile.TemporaryDirectory() as temp, self.context():
            root = Path(temp); self.fixture(root)
            rows = [self.write(root, "impeller-case/postProcessing/checkMesh/constant/nearPoints.vtk",
                               b"SYNTHETIC-DATA-ROLE-NOT-NATIVE-VTK"),
                    self.write(root, "impeller-case/constant/polyMesh/sets/nearPoints",
                               b"SYNTHETIC-DATA-ROLE-NOT-NATIVE-POINTSET")]
            proof = {"files": rows, "evidence": {"fixtureKind": "MOCKED-GEOMETRY-ONLY-NOT-RESEARCH"}}
            # The runner's own focused tests verify full native label/VTK/source
            # semantics. Here only that heavy geometry proof is mocked; current
            # bytes, raw report linkage and aggregate evidence remain real.
            with patch.object(runner, "near_points_evidence", return_value=proof):
                value, _ = tool.record(root, tool.EXECUTION_FILE)
                value.update(nativeCheckMeshDiagnosticRecords=rows, nativeNearPointsEvidence=proof["evidence"])
                self.update_execution(root, value); result = tool.reduce_baseline(root)
                self.assertTrue(result["numericalBaselineQualified"], result["diagnostics"])
                self.assertTrue(all(row in result["nativeEvidenceRecords"] for row in rows))
                value["nativeCheckMeshDiagnosticRecords"] = []; self.update_execution(root, value)
                result = tool.reduce_baseline(root); self.assertFalse(result["actualCfdRunVerified"])
                self.assertIn("NEAR_POINTS_DIAGNOSTIC", str(result["diagnostics"]))

    def test_teaching_yplus_empty_dimensions_and_face_velocity_are_exact_bound_data(self):
        import run_teaching_pump_baseline as runner
        with tempfile.TemporaryDirectory() as temp, self.context():
            root = Path(temp); self.fixture(root)
            extra = [self.write(root, "impeller-case/500/yPlus",
                "FoamFile {format ascii; class volScalarField; object yPlus;}\ndimensions [];\ninternalField uniform 30;\nboundaryField {}\n"),
                self.write(root, "impeller-case/500/Uf",
                "FoamFile {format ascii; class surfaceVectorField; object Uf;}\ndimensions [0 1 -1 0 0 0 0];\ninternalField uniform (1 2 3);\nboundaryField {}\n")]
            primary, rows, scans = runner.final_fields(root, 2)
            raw, _ = tool.record(root, tool.EXECUTION_FILE)
            raw.update(finalNativeFields=primary, finalNativeFieldRecords=rows, finiteNativeFieldEvidence=scans)
            self.update_execution(root, raw)
            # Only the new teaching adapter is used. Legacy original/snappy
            # endpoint admission must not be relaxed by this compatibility fix.
            with patch.object(tool.equations, "endpoint_fields", side_effect=AssertionError("Legacy reader must not be called")):
                result = tool.reduce_baseline(root)
            self.assertTrue(result["numericalBaselineQualified"], result["diagnostics"])
            self.assertTrue(all(row in result["nativeEvidenceRecords"] for row in extra))
            self.assertFalse(result["wallResolutionVerified"])
            for role in ("inventory", "scan", "primary"):
                with self.subTest(role=role):
                    changed = dict(raw)
                    if role == "inventory": changed["finalNativeFieldRecords"] = rows[:-1]
                    elif role == "scan": changed["finiteNativeFieldEvidence"] = scans[:-1]
                    else: changed["finalNativeFields"] = primary[:-1]
                    self.update_execution(root, changed)
                    result = tool.reduce_baseline(root)
                    self.assertFalse(result["actualCfdRunVerified"])
                    self.assertIn("COMPLETE_ENDPOINT_SCAN", str(result["diagnostics"]))

    def test_endpoint_scan_cannot_relabel_unknown_fields_or_mutated_hashes(self):
        import run_teaching_pump_baseline as runner
        with tempfile.TemporaryDirectory() as temp, self.context():
            root = Path(temp); self.fixture(root)
            raw, _ = tool.record(root, tool.EXECUTION_FILE)
            raw["finalNativeFieldRecords"][0]["sha256"] = "0" * 64
            self.update_execution(root, raw)
            self.assertFalse(tool.reduce_baseline(root)["actualCfdRunVerified"])
            self.write(root, "impeller-case/500/unknownField",
                "FoamFile {format ascii; class volScalarField; object unknownField;}\ndimensions [];\ninternalField uniform 0;\nboundaryField {}\n")
            with self.assertRaises(ValueError): runner.final_fields(root, 2)


if __name__ == "__main__": unittest.main()
