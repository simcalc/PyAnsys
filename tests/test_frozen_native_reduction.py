"""Synthetic ASCII/log fixtures only; never evidence of physical pump CFD."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools/frozen_rotor_native_reduction.py"
spec = importlib.util.spec_from_file_location("frozen_native_reduction_tests", TOOL)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeReductionContracts(unittest.TestCase):
    def write(self, root, relative, value):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = value if isinstance(value, bytes) else value.encode("utf-8")
        path.write_bytes(raw)
        return {"path": relative, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}

    def write_json(self, root, relative, value):
        return self.write(root, relative, native.encoded(value))

    def table(self, patch, columns, values):
        header = f"# Selection : patch({patch})\n# Faces : 3\n# Area : 0.5\n# Time " + " ".join(columns) + "\n"
        return header + "".join(str(index) + " " + " ".join(format(value, ".17g") for value in values) + "\n" for index in range(481, 501))

    def fixture(self, root, *, initial_residual=1e-5):
        """All files and flags here are explicit synthetic parser test inputs."""
        helper = native.case_tool
        remesh = {"schemaVersion": "untirta.frozen-rotor-classroom-remesh.v1", "exampleDataCommit": helper.inventory.PIN,
                  "status": "VOLUME_MESH_GENERATED", "volumeMeshGenerated": True, "originalMeshesImmutable": True,
                  **{key: False for key in ("boundaryRemeshed", "surfaceReparametrizationPerformed", "sourceGeometryFabricated",
                                          "nodeWeldingPerformed", "topologyHealingPerformed", "simulationExecuted", "actualCfdRunVerified", "mainApplicationWrites")},
                  "classroomScaleAssumption": {"nativeUnitsToMetres": 1, "verifiedFromSource": False},
                  "sources": [{"name": name, "bytes": helper.inventory.FILES[name][0], "sha256": helper.inventory.FILES[name][1]} for name in helper.inventory.MESH_NAMES],
                  "meshes": []}
        for component in ("impeller", "volute"):
            remesh["meshes"].append({"sourceMesh": component + ".msh.h5", "boundaryRemeshed": False, "surfaceReparametrizationPerformed": False,
                "sourceSeamPartition": {"completeNativeTriangleCoverageVerified": True, "nativeGlobalNodeCoordinatesVerified": True,
                                        "mixedSourceZoneEntityCount": 0, "nearestTagGuessPerformed": False},
                "finalPreservedBoundaryAudit": {"consistentNativeWinding": True},
                "boundaryZones": [{"sourceZoneId": index+1, "sourceZoneName": name, "sourceTriangleCount": 3,
                    "nativeTriangleOwnershipVerifiedBeforeRemesh": True,
                    "boundaryEquality": {"everyNativeTriangleAndBoundaryNodeChecked": True, "maximumCoordinateDifferenceNativeUnits": 0,
                                         "nativeWindingPreserved": True}} for index, name in enumerate(sorted(helper.PATCHES[component]))]})
        remesh_row = self.write_json(root, "remesh-report.json", remesh)
        preparation = {"schemaVersion": helper.SCHEMA, "exampleDataCommit": helper.inventory.PIN,
                       "mainApplicationWrites": False,
                       "remeshReportSha256": remesh_row["sha256"], "components": [{"cellCount": 1}, {"cellCount": 1}],
                       "classroomAssumptions": {"rhoKgM3": 998.2, "muPaS": .001002, "gMS2": 9.81, "speedRpm": 1450,
                           "axis": [0, 0, 1], "massFlowKgS": 90, "externalTotalPressureAbsolutePa": 101325,
                           "gravityInSolver": False, "metresPerNativeUnit": 1, "nativeUnitsVerified": False}}
        preparation_row = self.write_json(root, "openfoam-preparation.json", preparation)
        self.write_json(root, "runtime-identity.json", {"openFoamFoundationVersion": "12", "packageVersion": "synthetic-test-no-native-binary"})
        for relative, text in helper.case_files("impeller").items():
            self.write(root, "impeller-case/" + relative, text)
        for name in ("points", "faces", "owner", "neighbour", "boundary", "cellZones"):
            self.write(root, "impeller-case/constant/polyMesh/" + name, "synthetic-test-topology-content-bound-only:" + name)
        # Actual binary validation is mocked exclusively inside this synthetic test.
        with patch.object(native, "binary_identity"):
            bound = native.build_manifest(root)
        self.write_json(root, "geometry-identity.json", bound["geometryIdentity"])
        self.write_json(root, "native-input-manifest.json", bound["manifest"])
        logs = {"checkMesh": "cells: 2\nMin volume = 1e-6. Max volume = 2e-6. Total volume = 3e-6.\nMesh OK.\nSource min/average/max coverage = 1/1/1\nTarget min/average/max coverage = 1/1/1\nEnd\n"}
        solver = ""
        for index in range(481, 501):
            solver += f"Time = {index}s\n"
            for field in ("Ux", "Uy", "Uz", "p", "omega", "k"):
                solver += f"solver: Solving for {field}, Initial residual = {initial_residual}, Final residual = 1e-10, No Iterations 2\n"
            solver += "GAMG: Solving for p, Initial residual = 1e-8, Final residual = 1e-12, No Iterations 1\n"
            solver += "time step continuity errors : sum local = 1e-6, global = -1e-7, cumulative = -1e-5\n"
        logs["foamRun"] = solver + "End\n"
        stages = []
        programs = ["gmshToFoam", "changeDictionary", "gmshToFoam", "changeDictionary", "mergeMeshes", "createNonConformalCouples", "checkMesh", "foamRun"]
        for index, program in enumerate(programs):
            name = f"{index:02d}-{program}.log"
            row = self.write(root, "logs/" + name, logs.get(program, "End\n"))
            stages.append({"program": program, "exitCode": 0, "log": name, "logBytes": row["bytes"], "logSha256": row["sha256"]})
        fields = []
        dimensions = {"p": "0 2 -2 0 0 0 0", "U": "0 1 -1 0 0 0 0", "k": "0 2 -2 0 0 0 0", "omega": "0 0 -1 0 0 0 0"}
        for name, dimension in dimensions.items():
            value = "(1 2 3)" if name == "U" else "100" if name == "p" else "1"
            kind = "volVectorField" if name == "U" else "volScalarField"
            field = f"FoamFile {{format ascii; class {kind}; object {name};}}\ndimensions [{dimension}];\ninternalField uniform {value};\nboundaryField {{}}\n"
            row = self.write(root, "impeller-case/500/" + name, field)
            fields.append({"name": name, "bytes": row["bytes"], "sha256": row["sha256"]})
        execution = {"schemaVersion": "untirta.frozen-rotor-openfoam-execution.v1", "status": "MRF_EXECUTED_ACCEPTANCE_REQUIRED",
                     "preparationReportSha256": preparation_row["sha256"], "actualCfdRunVerified": True, "simulationExecuted": True,
                     "openFoamConversionVerified": True, "nativeGeometryQualityVerified": True, "physicalWallTypesVerified": True,
                     "mainApplicationWrites": False, "githubRunId": "synthetic-unit-fixture-not-research", "stages": stages, "finalNativeFields": fields}
        self.write_json(root, "openfoam-execution.json", execution)
        q, rho = 90/998.2, 998.2
        values = {"flowInlet": [-q], "flowOutlet": [q], "flowMagnitudeInlet": [q], "flowMagnitudeOutlet": [q],
                  "weightedInlet": [10, 101325+rho*10+rho*4/2, 4], "weightedOutlet": [210, 101325+rho*210+rho*9/2, 9]}
        for name, (section, columns) in native.SAMPLERS.items():
            self.write(root, f"impeller-case/postProcessing/{name}/0/surfaceFieldValue.dat", self.table(section, columns, values[name]))
        self.write(root, "impeller-case/postProcessing/globalPressureMinimum/0/volFieldValue.dat", "# Time min(p)\n" + "".join(f"{i} 10\n" for i in range(481, 501)))
        for name, section, value in zip(("flowRotorInterface", "flowStatorInterface"), native.COUPLED_PATCHES, (q, -q)):
            self.write(root, f"impeller-case/postProcessing/{name}/0/surfaceFieldValue.dat", self.table(section, ("sum(phi)",), [value]))
        return bound

    def reduce(self, root):
        with patch.object(native, "binary_identity"):
            return native.reduce_native_baseline(root)

    def test_actual_values_and_input_equation_substitution_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            result = self.reduce(root)
            self.assertTrue(result["qualifiedBaseline"], result["diagnostics"])
            self.assertTrue(result["preliminaryBaselineQualified"])
            self.assertFalse(result["meshConvergenceVerified"])
            self.assertFalse(result["wallResolutionVerified"])
            self.assertFalse(result["physicalValidationVerified"])
            self.assertFalse(result["iterationIndexIsPhysicalTime"])
            self.assertEqual(result["samplingIterationWindow"], [481, 500])
            self.assertAlmostEqual(result["stages"]["head"]["result"]["value"], (200+2.5)/9.81)
            npsha = (998.2*10+101325-2339)/(998.2*9.81)+4/(2*9.81)
            self.assertAlmostEqual(result["stages"]["npsha"]["result"]["value"], npsha)
            self.assertEqual(result["nativeObservation"]["quality"]["outwardInletMassFlowKgS"], -90)
            evidence = native.sha256(native.encoded(sorted(result["nativeEvidenceRecords"], key=lambda row: row["path"]))).hexdigest()
            self.assertEqual(result["nativeObservation"]["nativeEvidence"]["sha256"], evidence)
            self.assertTrue(result["interfaceCoverageVerified"])
            self.assertEqual(result["nativeObservation"]["measurements"]["minimumStaticPressureAbsolutePa"], result["minimumStaticPressureAbsolutePa"])

    def test_emitted_native_packet_is_consumed_by_phase_preparation_adapter(self):
        phase_spec = importlib.util.spec_from_file_location("synthetic_cross_phase_adapter", TOOL.parent / "frozen_rotor_cavitation_case.py")
        phase = importlib.util.module_from_spec(phase_spec)
        phase_spec.loader.exec_module(phase)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            emitted = self.reduce(root)
            # Test topology bytes are content-bound only; mock the distinct
            # native header gate here. Real topology was never generated.
            with patch.dict(os.environ, {"RUNNER_TEMP": str(root)}), patch.object(phase, "native_bytes"):
                directory, study, observed, records, manifest_sha = phase.verify_emitted_baseline(root, emitted)
            self.assertEqual(directory, root / "impeller-case")
            self.assertEqual(study, emitted["study"])
            self.assertEqual(observed, emitted["nativeObservation"])
            self.assertTrue(records)
            self.assertEqual(manifest_sha, emitted["nativeObservation"]["controlledVariationProof"]["nativeInputManifestSha256"])

    def test_missing_execution_returns_pending_without_synthetic_results(self):
        with tempfile.TemporaryDirectory() as temp:
            result = native.reduce_native_baseline(temp)
            self.assertEqual(result["status"], "NativeEvidenceRequired")
            self.assertFalse(result["actualCfdRunVerified"])
            self.assertFalse(result["qualifiedBaseline"])
            self.assertEqual(result["stages"], {})

    def test_first_nonlinear_pressure_residual_cannot_be_replaced_by_linear_final(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, initial_residual=.1)
            result = self.reduce(root)
            self.assertFalse(result["qualifiedBaseline"])
            self.assertIn("CFD_RESIDUAL_CONVERGENCE_REQUIRED", str(result["diagnostics"]))
            self.assertTrue(result["stages"]["head"])

    def test_missing_NCC_or_global_pressure_or_interface_flux_keeps_independent_stages(self):
        for missing in ("globalPressureMinimum/0/volFieldValue.dat", "flowRotorInterface/0/surfaceFieldValue.dat"):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.fixture(root)
                (root / "impeller-case/postProcessing" / missing).unlink()
                result = self.reduce(root)
                self.assertFalse(result["qualifiedBaseline"])
                self.assertEqual(set(result["stages"]), {"head", "npsha"})
        with self.assertRaises(native.protocol.ProtocolError):
            native.parse_coverage("NCC created\nEnd\n")

    def test_runtime_and_native_input_mismatch_are_rejected_before_numeric_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            self.assertFalse(native.reduce_native_baseline(root)["qualifiedBaseline"])
            self.write(root, "impeller-case/constant/physicalProperties", "wrong native viscosity")
            result = self.reduce(root)
            self.assertFalse(result["actualCfdRunVerified"])
            self.assertEqual(result["stages"], {})

    def test_geometry_signature_survives_mesh_attempt_metadata_but_binds_source_facets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = self.fixture(root)
            remesh = json.loads((root / "remesh-report.json").read_text())
            remesh.update(createdAt="another-attempt", outputPath="another-volume-mesh-path", generatedVolumeMeshSha256="b"*64)
            remesh_row = self.write_json(root, "remesh-report.json", remesh)
            preparation = json.loads((root / "openfoam-preparation.json").read_text())
            preparation.update(createdAt="another-case-time", nativeCommandArguments=["other-runtime-path"], remeshReportSha256=remesh_row["sha256"])
            self.write_json(root, "openfoam-preparation.json", preparation)
            self.write(root, "impeller-case/constant/polyMesh/points", "different-actual-refined-volume-mesh")
            with patch.object(native, "binary_identity"):
                refined = native.build_manifest(root)
            self.assertEqual(original["study"]["controls"]["geometrySha256"], refined["study"]["controls"]["geometrySha256"])
            self.assertNotEqual(original["study"]["controls"]["meshSha256"], refined["study"]["controls"]["meshSha256"])
            remesh["sources"][0]["sha256"] = "c"*64
            with self.assertRaisesRegex(native.protocol.ProtocolError, "PIN_MISMATCH"):
                native.geometry_identity(remesh)
            remesh["sources"][0]["sha256"] = native.case_tool.inventory.FILES[remesh["sources"][0]["name"]][1]
            remesh["meshes"][0]["boundaryZones"][0]["boundaryEquality"]["maximumCoordinateDifferenceNativeUnits"] = .01
            with self.assertRaisesRegex(native.protocol.ProtocolError, "FACET_COORDINATE"):
                native.geometry_identity(remesh)

    def test_pressure_mapping_and_section_names_are_exact_not_header_guesses(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            path = root / "impeller-case/postProcessing/weightedInlet/0/surfaceFieldValue.dat"
            original = path.read_text()
            path.write_text(original.replace("average(totalPressurePa)", "average(p_rgh)"))
            result = self.reduce(root)
            self.assertFalse(result["qualifiedBaseline"])
            self.assertIn("SAMPLING_HEADER_MISMATCH", str(result["diagnostics"]))
        table = self.table("wrong-section", ("sum(phi)",), [1])
        with self.assertRaisesRegex(native.protocol.ProtocolError, "SECTION_MISMATCH"):
            native.parse_table(table, ("sum(phi)",), "inlet")

    def test_finite_rows_complete_current_window_and_no_stale_endpoint(self):
        for bad in ("# Time sum(phi)\n1 nan\n", "# Time sum(phi)\n1 1\n1 2\n", "# Time sum(phi)\n2 1\n1 2\n"):
            with self.assertRaises(native.protocol.ProtocolError):
                native.parse_table(bad, ("sum(phi)",))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            path = root / "impeller-case/postProcessing/flowOutlet/0/surfaceFieldValue.dat"
            path.write_text(path.read_text().replace("500 ", "501 "))
            result = self.reduce(root)
            self.assertFalse(result["qualifiedBaseline"])

    def test_native_mesh_volume_and_internal_field_math_domain(self):
        for bad in ("Mesh OK.\n", "cells: 2\nMin volume = -1. Max volume = 2. Total volume = 3.\nMesh OK.\n"):
            with self.assertRaises(native.protocol.ProtocolError):
                native.parse_mesh_quality(bad)
        field = b"FoamFile {format ascii; class volScalarField; object p;} dimensions [0 2 -2 0 0 0 0]; internalField nonuniform List<scalar> 2 (1 2);"
        self.assertEqual(native.finite_field(field, "p", 2)["internalValueCount"], 2)
        for bad in (field.replace(b"2 (1 2)", b"3 (1 2 3)"), field.replace(b"1 2", b"nan 2"), field.replace(b"0 2 -2", b"1 -1 -2")):
            with self.assertRaises(native.protocol.ProtocolError):
                native.finite_field(bad, "p", 2)

    def test_global_suction_domain_failure_does_not_fabricate_noncavitating_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            path = root / "impeller-case/postProcessing/globalPressureMinimum/0/volFieldValue.dat"
            path.write_text(path.read_text().replace(" 10\n", " -101\n"))
            result = self.reduce(root)
            self.assertFalse(result["qualifiedBaseline"])
            self.assertFalse(result["noncavitatingBaselineVerified"])
            self.assertIn("CFD_NONCAVITATING_BASELINE_REQUIRED", str(result["diagnostics"]))

    def test_missing_outlet_preserves_actual_valid_inlet_NPSHA(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            (root / "impeller-case/postProcessing/weightedOutlet/0/surfaceFieldValue.dat").unlink()
            result = self.reduce(root)
            self.assertFalse(result["qualifiedBaseline"])
            self.assertEqual(set(result["stages"]), {"npsha"})

    def test_actual_first_write_directory_one_and_realTime_seconds_log(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            for directory in (root / "impeller-case/postProcessing").iterdir():
                old, new = directory / "0", directory / "1"
                self.assertTrue(new.absolute().is_relative_to(root.absolute()))
                old.rename(new)
            self.assertTrue(self.reduce(root)["qualifiedBaseline"])
            (root / "impeller-case/postProcessing/flowInlet/0").mkdir()
            result = self.reduce(root)
            self.assertFalse(result["qualifiedBaseline"])
            self.assertIn("ONE_FRESH_NATIVE_SAMPLER_DIRECTORY", str(result["diagnostics"]))

    def test_evidence_path_and_json_payload_are_bounded_and_no_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for path in ("../outside.json", "/outside.json", "..\\outside.json"):
                with self.assertRaises(native.protocol.ProtocolError):
                    native.read(root, path)
            self.write(root, "record.json", '{"value":1,"value":2}')
            with self.assertRaisesRegex(native.protocol.ProtocolError, "DUPLICATE"):
                native.record(root, "record.json")
            self.write(root, "record.json", '{"value":NaN}')
            with self.assertRaisesRegex(native.protocol.ProtocolError, "FINITE"):
                native.record(root, "record.json")
            self.write(root, "large.dat", b"x"*32)
            with self.assertRaises(native.protocol.ProtocolError):
                native.read(root, "large.dat", limit=16)


if __name__ == "__main__":
    unittest.main()
