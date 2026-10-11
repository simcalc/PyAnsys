"""Synthetic four-stage contract tests; no original pump CFD is executed here."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import frozen_rotor_native_reduction as native
import frozen_rotor_original_reduction as original

spec = importlib.util.spec_from_file_location("original_route_synthetic_fixture", Path(__file__).with_name("test_frozen_native_reduction.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
fixtures.native = native


class OriginalNativeReduction(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.NativeReductionContracts()

    def write_json(self, root, relative, value):
        return self.helper.write_json(root, relative, value)

    def conversion_fixture(self, root):
        components = []
        counts = {"impeller": [28494,28494,36432,16974,5130,5130,6210],
                  "volute": [3297,196,6433,29991]}
        names = {"impeller": ["impeller-hub","impeller-shroud","blade","interface-impeller-outlet","inblock-hub","inblock-shroud","inlet"],
                 "volute": ["interface-volute-inlet","mass-flow-inlet-11","volute-inlet-wall","volute-wall"]}
        for component in ("impeller", "volute"):
            cells, nodes, faces = original.original.EXPECTED_COUNTS[component]
            internal = faces - sum(counts[component]); first = internal
            patches = []
            for index, (name, count) in enumerate(zip(names[component], counts[component]), 1):
                patches.append({"name": name, "type": "wall" if name in native.case_tool.ROTATING | native.case_tool.STATIONARY else "patch",
                                "nFaces": count, "startFace": first, "sourceZoneId": index,
                                "sourceMinimumFaceId": first+1, "sourceMaximumFaceId": first+count, "reversedFaceCount": count})
                first += count
            source_name = component + ".msh.h5"
            size, digest = native.case_tool.inventory.FILES[source_name]
            source = {"name": source_name, "bytes": size, "sha256": digest, "sourceUrl": native.case_tool.inventory.mesh_url(source_name)}
            mapping = self.helper.write(root, component + "-case/sourceFaceIds", "FoamFile {object sourceFaceIds; class labelList; format ascii;}\n1\n(1)\n")
            components.append({"name": component, "casePath": component + "-case", "sourceFile": source,
                               "cellCount": cells, "nodeCount": nodes, "faceCount": faces, "internalFaceCount": internal,
                               "boundaryFaceCount": sum(counts[component]), "patches": patches,
                               "cellZone": {"name": "rotating-fluid" if component == "impeller" else "stationary-fluid", "sourceName": component,
                                            "sourceZoneId": 36 if component == "impeller" else 2, "cellCount": cells},
                               "sourceFaceIdMapFile": mapping,
                               "checks": {**{key: True for key in ("allOriginalCellsChecked","directedEdgeIncidenceClosed","standardCellShapeIncidenceVerified",
                                   "allCoordinatesAsciiRoundTripVerified","everyOriginalFaceTransferredExactlyOnce","originalPolygonsRetained","ownerNeighbourOrderingVerified")},
                                   "originalCellCountChecked": cells, "minimumCellVolumeAssumedM3": 1e-12, "maximumCellVolumeAssumedM3": 1e-5,
                                   "totalCellVolumeAssumedM3": .01, "maximumRelativeAreaVectorClosureError": 1e-14,
                                   "relativeAreaClosureTolerance": 1e-10, "internalFaceCount": internal,
                                   "boundarySignedVolumeAssumedM3": .01, "relativeCellBoundaryVolumeDifference": 0.,
                                   "cellBoundaryVolumeAgreementVerified": True}})
        return {"schemaVersion": original.CONVERSION_SCHEMA, "exampleDataCommit": native.case_tool.inventory.PIN,
                "components": components, "combinedCounts": {key: sum(row[key] for row in components) for key in ("cellCount","nodeCount","faceCount")},
                "originalMeshesImmutable": True, "volumeMeshConverted": True, "classroomMetresPerSourceCoordinateUnit": 1, "coordinateUnits": "UNVERIFIED",
                **{key: False for key in ("nativeCoordinateUnitsVerified","meshQualityCheckedBySolver","nativeCaseGenerated","simulationExecuted","actualCfdRunVerified","mainApplicationWrites")}}

    def fixture(self, root, *, initial_residual=1e-5):
        self.helper.fixture(root, initial_residual=initial_residual)
        for name in ("geometry-identity.json", "native-input-manifest.json", "remesh-report.json"):
            (root/name).unlink()
        conversion = self.conversion_fixture(root)
        conversion_row = self.write_json(root, "native-conversion.json", conversion)
        prep = json.loads((root/"openfoam-preparation.json").read_text())
        prep.update(schemaVersion=original.SCHEMA, status="ORIGINAL_CASE_PREPARED_NATIVE_EXECUTION_REQUIRED",
                    nativeConversionReportSha256=conversion_row["sha256"], originalMeshesImmutable=True,
                    openFoamConversionVerified=True, nativeGeometryQualityVerified=False, meshQualityCheckedBySolver=False,
                    qualifiedBaseline=False, actualCfdRunVerified=False, simulationExecuted=False, commandExecutionPerformedByThisTool=False,
                    nativeCommandArguments=original.original.commands(root), components=[{
                        "component": row["name"], "caseDirectory": row["casePath"],
                        **{key: row[key] for key in ("cellCount","nodeCount","faceCount")},
                        "patchFaces": {patch["name"]: patch["nFaces"] for patch in row["patches"]}, "cellZone": row["cellZone"]["name"]}
                        for row in conversion["components"]])
        prep.pop("remeshReportSha256")
        prep["classroomAssumptions"].update(externalTotalPressureAbsolutePa=300000, pressureSelectedBeforeExecution=True, volumetricFlowM3S=90/998.2)
        prep_row = self.write_json(root, "openfoam-preparation.json", prep)
        for local, text in original.original.case_files("impeller",300000).items():
            self.helper.write(root,"impeller-case/"+local,text)
        with patch.object(native,"binary_identity"):
            bound = native.build_manifest(root)
        self.write_json(root,"geometry-identity.json",bound["geometryIdentity"])
        self.write_json(root,"native-input-manifest.json",bound["manifest"])
        execution = json.loads((root/"openfoam-execution.json").read_text())
        execution.update(schemaVersion="untirta.frozen-rotor-original-openfoam-execution.v1",status="ORIGINAL_MRF_EXECUTED_ACCEPTANCE_REQUIRED",
                         preparationReportSha256=prep_row["sha256"],nativeConversionReportSha256=conversion_row["sha256"],
                         nativeSolverCompleted=True,finiteNativeFieldsVerified=True,inputArtifactsImmutable=True,stages=[])
        for index, program in enumerate(("mergeMeshes","createNonConformalCouples","checkMesh","foamRun")):
            old_index = index+4
            raw = (root/f"logs/{old_index:02d}-{program}.log").read_text()
            if program == "checkMesh": raw = raw.replace("cells: 2", "cells: 1141763")
            name = f"{index:02d}-{program}.log"
            row = self.helper.write(root,"logs/"+name,raw)
            execution["stages"].append({"program":program,"exitCode":0,"log":name,"logBytes":row["bytes"],"logSha256":row["sha256"]})
        self.write_json(root,"openfoam-execution.json",execution)
        for suffix, p, u2 in (("Inlet",10,4),("Outlet",210,9)):
            values = [p,300000+998.2*p+998.2*u2/2,u2]
            table = self.helper.table("inlet" if suffix == "Inlet" else "mass-flow-inlet-11",native.SAMPLERS["weighted"+suffix][1],values)
            self.helper.write(root,f"impeller-case/postProcessing/weighted{suffix}/0/surfaceFieldValue.dat",table)
        return bound

    def reduce(self, root):
        with patch.object(native,"binary_identity"):
            return native.reduce_native_baseline(root)

    def test_distinct_four_stage_route_uses_same_scientific_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bound=self.fixture(root);result=self.reduce(root)
            self.assertTrue(result["qualifiedBaseline"],result["diagnostics"])
            self.assertEqual(result["manifest"]["sourceGeometryRoute"],original.GEOMETRY_ROUTE)
            self.assertEqual(result["nativeObservation"]["quality"]["meshCellCount"],1141763)
            self.assertAlmostEqual(result["stages"]["head"]["result"]["value"],202.5/9.81)
            self.assertAlmostEqual(result["stages"]["npsha"]["result"]["value"],(300000+998.2*10-2339)/(998.2*9.81)+4/(2*9.81))
            self.assertFalse(result["meshConvergenceVerified"])
            self.assertFalse(result["physicalValidationVerified"])
            self.assertNotIn("remesh-report.json",[row["path"] for row in bound["manifest"]["files"]])

    def test_original_guard_rejects_old_raw_execution_or_counterfeit_eight_stages(self):
        for change in ("schema","stages"):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);self.fixture(root)
                packet=json.loads((root/"openfoam-execution.json").read_text())
                if change=="schema": packet["schemaVersion"]="untirta.frozen-rotor-openfoam-execution.v1"
                else: packet["stages"]=[{"program":"gmshToFoam","exitCode":0}]+packet["stages"]
                self.write_json(root,"openfoam-execution.json",packet)
                result=self.reduce(root);self.assertFalse(result["actualCfdRunVerified"]);self.assertEqual(result["stages"],{})

    def test_tetra_route_still_requires_eight_actual_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.helper.fixture(root)
            packet=json.loads((root/"openfoam-execution.json").read_text());packet["stages"]=packet["stages"][4:]
            self.write_json(root,"openfoam-execution.json",packet)
            result=self.reduce(root);self.assertFalse(result["actualCfdRunVerified"])
            self.assertIn("COMPLETE_NATIVE_EXECUTION_STAGES",str(result["diagnostics"]))

    def test_original_native_completion_and_conversion_bindings_are_required(self):
        for key in ("nativeSolverCompleted","finiteNativeFieldsVerified","inputArtifactsImmutable","nativeConversionReportSha256"):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);self.fixture(root)
                packet=json.loads((root/"openfoam-execution.json").read_text());packet[key]=False if key.endswith("Verified") or key in {"nativeSolverCompleted","inputArtifactsImmutable"} else "a"*64
                self.write_json(root,"openfoam-execution.json",packet)
                result=self.reduce(root);self.assertFalse(result["actualCfdRunVerified"])
                self.assertIn("ORIGINAL_NATIVE_COMPLETION_BINDING",str(result["diagnostics"]))

    def test_every_original_cell_coordinate_polygon_proof_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);packet=self.conversion_fixture(root)
            self.assertEqual(original.geometry_identity(packet)["sourceGeometryRoute"],original.GEOMETRY_ROUTE)
            for key in ("allCoordinatesAsciiRoundTripVerified","originalPolygonsRetained","ownerNeighbourOrderingVerified","allOriginalCellsChecked"):
                mutated=json.loads(json.dumps(packet));mutated["components"][0]["checks"][key]=False
                with self.assertRaises(native.protocol.ProtocolError): original.geometry_identity(mutated)
            mutated=json.loads(json.dumps(packet));mutated["components"][0]["checks"]["originalCellCountChecked"]-=1
            with self.assertRaises(native.protocol.ProtocolError): original.geometry_identity(mutated)
            mutated=json.loads(json.dumps(packet));mutated["components"][0]["sourceFile"]["sha256"]="a"*64
            with self.assertRaises(native.protocol.ProtocolError): original.geometry_identity(mutated)

    def test_relative_closure_tolerance_cannot_be_weakened(self):
        with tempfile.TemporaryDirectory() as temp:
            packet=self.conversion_fixture(Path(temp));packet["components"][0]["checks"]["relativeAreaClosureTolerance"]=1e-5
            with self.assertRaises(native.protocol.ProtocolError): original.geometry_identity(packet)

    def test_cell_boundary_volume_claim_cannot_replace_measured_agreement(self):
        changes = ({"cellBoundaryVolumeAgreementVerified":False}, {"boundarySignedVolumeAssumedM3":-.01},
                   {"relativeCellBoundaryVolumeDifference":1e-5}, {"boundarySignedVolumeAssumedM3":.02,"relativeCellBoundaryVolumeDifference":0.})
        for changed in changes:
            with tempfile.TemporaryDirectory() as temp:
                packet=self.conversion_fixture(Path(temp));packet["components"][0]["checks"].update(changed)
                with self.assertRaises(native.protocol.ProtocolError): original.geometry_identity(packet)

    def test_changed_source_face_mapping_or_pressure_recipe_rejects_current_binding(self):
        for relative in ("impeller-case/sourceFaceIds","impeller-case/system/controlDict"):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);self.fixture(root);path=root/relative;path.write_bytes(path.read_bytes()+b"\n// changed\n")
                result=self.reduce(root);self.assertFalse(result["actualCfdRunVerified"]);self.assertEqual(result["stages"],{})

    def test_bad_residual_preserves_valid_numerical_outputs_without_qualification(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root,initial_residual=.1);result=self.reduce(root)
            self.assertTrue(result["actualCfdRunVerified"]);self.assertFalse(result["qualifiedBaseline"])
            self.assertIn("RESIDUAL_CONVERGENCE_REQUIRED",str(result["diagnostics"]))
            self.assertTrue(result["stages"]["head"])

    def test_missing_global_pressure_still_preserves_independent_head_npsha(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            (root/"impeller-case/postProcessing/globalPressureMinimum/0/volFieldValue.dat").unlink()
            result=self.reduce(root);self.assertFalse(result["qualifiedBaseline"])
            self.assertEqual(set(result["stages"]),{"head","npsha"})

    def test_current_endpoint_hash_is_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            path=root/"impeller-case/500/p";path.write_bytes(path.read_bytes().replace(b"uniform 100",b"uniform 101"))
            result=self.reduce(root);self.assertFalse(result["actualCfdRunVerified"])
            self.assertIn("CURRENT_NATIVE_FIELD_BINDING",str(result["diagnostics"]))

    def test_unknown_preparation_schema_cannot_select_original_adapter(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            prep=json.loads((root/"openfoam-preparation.json").read_text());prep["schemaVersion"]+="-unknown"
            self.write_json(root,"openfoam-preparation.json",prep)
            result=self.reduce(root);self.assertFalse(result["actualCfdRunVerified"])
            self.assertIn("SOURCE_PINNED_PREPARATION",str(result["diagnostics"]))


if __name__ == "__main__": unittest.main()
