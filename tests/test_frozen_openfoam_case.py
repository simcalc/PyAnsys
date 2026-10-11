"""Focused preparation/identity regressions; fixtures are not pump CFD evidence."""
import copy
import importlib.util
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools/frozen_rotor_openfoam_case.py"
spec = importlib.util.spec_from_file_location("frozen_openfoam_case", TOOL)
case = importlib.util.module_from_spec(spec)
spec.loader.exec_module(case)

MSH = b'''$MeshFormat
2.2 0 8
$EndMeshFormat
$PhysicalNames
2
2 15 "inlet"
3 1 "rotating-fluid"
$EndPhysicalNames
$Nodes
4
1 0 0 0
2 1 0 0
3 0 1 0
4 0 0 1
$EndNodes
$Elements
5
1 2 2 15 1 1 3 2
2 2 2 15 1 1 2 4
3 2 2 15 1 2 3 4
4 2 2 15 1 3 1 4
5 4 2 1 1 1 2 3 4
$EndElements
'''


class PreparationContracts(unittest.TestCase):
    def test_actual_ascii_counts_positions_and_zone_identity(self):
        inventory = case.msh_inventory(MSH)
        self.assertEqual(inventory["nodeCount"], 4)
        self.assertEqual(inventory["cellCount"], 1)
        self.assertEqual(inventory["patchFaces"], {"inlet": 4})
        self.assertEqual(inventory["cellZone"], "rotating-fluid")
        self.assertEqual(inventory["nodeCoordinatesInFileOrder"][-1], (0,0,1))

    def test_actual_mesh_rejects_wrong_format_counts_links_and_geometry(self):
        cases = [MSH.replace(b"2.2 0 8", b"4.1 0 8"), MSH.replace(b"$Nodes\n4", b"$Nodes\n5"),
                 MSH.replace(b"2 1 0 0", b"2 nan 0 0"), MSH.replace(b"5 4 2 1 1 1 2 3 4", b"5 4 2 1 1 1 3 2 4"),
                 MSH.replace(b"3 1 \"rotating-fluid\"", b"3 15 \"rotating-fluid\""),
                 MSH.replace(b"2 2 2 15 1 1 2 4", b"2 2 2 15 1 1 3 2"),
                 MSH.replace(b"2 2 2 15 1 1 2 4", b"2 2 2 15 1 1 2 99")]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises((case.CaseIssue, ValueError)):
                case.msh_inventory(raw)

    def test_dictionary_engineering_units_wall_frames_and_fixed_iterations(self):
        files = case.case_files("impeller")
        self.assertIn("omega 1450 [rpm]", files["constant/MRFProperties"])
        self.assertIn("cellZone rotating-fluid", files["constant/MRFProperties"])
        self.assertIn("axis (0 0 1)", files["constant/MRFProperties"])
        self.assertIn("steadyState", files["system/fvSchemes"])
        self.assertIn("SIMPLE", files["system/fvSolution"])
        self.assertIn("endTime 500", files["system/controlDict"])
        self.assertIn("writeInterval 500", files["system/controlDict"])
        self.assertNotIn("dynamicMeshDict", files)
        self.assertIn("[0 2 -2", files["0/p"])
        self.assertIn("type totalPressure; p0 uniform 0", files["0/p"])
        self.assertIn("MRFnoSlip", files["0/U"])
        self.assertIn("inblock-shroud", files["0/U"])
        self.assertIn("type noSlip", files["0/U"])
        self.assertIn("kOmegaSST", files["constant/momentumTransport"])
        self.assertIn(format(case.MU / case.RHO, ".17g"), files["constant/physicalProperties"])
        self.assertAlmostEqual(case.Q * case.RHO, 90)
        self.assertIn("volumetricFlowRate " + format(case.Q, ".17g"), case.case_files("volute")["0/U"])
        self.assertNotIn("MRFProperties", case.case_files("volute"))

    def test_sampling_uses_foundation_native_weighting_pressure_and_reverse_flow(self):
        functions = case.function_objects()
        self.assertIn("select patch", functions)
        self.assertIn("operation average; weightField phi", functions)
        self.assertIn("operation sumMag; fields (phi)", functions)
        self.assertIn("pRef 101325", functions)
        self.assertIn("calcTotal yes", functions)
        self.assertIn("rho rhoInf; rhoInf 998.2", functions)
        self.assertIn("field U; result U2", functions)
        self.assertNotIn("weightedAverage", functions)
        self.assertNotIn("regionType", functions)

    def input_fixture(self, root):
        root.mkdir()
        sources = [{"name": name, "bytes": case.inventory.FILES[name][0], "sha256": case.inventory.FILES[name][1]} for name in case.inventory.MESH_NAMES]
        meshes = []
        for component in ("impeller", "volute"):
            raw = MSH if component == "impeller" else MSH.replace(b'"inlet"', b'"mass-flow-inlet-11"').replace(b'"rotating-fluid"', b'"stationary-fluid"')
            (root / (component + ".msh")).write_bytes(raw)
            name = "inlet" if component == "impeller" else "mass-flow-inlet-11"
            meshes.append({"sourceMesh": component + ".msh.h5", "generatedNodeCount":4, "generatedCellCount":1,
                "generatedFile":{"name":component + ".msh", "format":"gmsh-msh2.2-ascii", "bytes":len(raw), "sha256":case.sha256(raw).hexdigest()},
                "boundaryZones":[{"sourceZoneName":name,"generated":{"triangleCount":4}}]})
        report = {"schemaVersion":"untirta.frozen-rotor-classroom-remesh.v1", "exampleDataCommit":case.inventory.PIN,
                  "status":"VOLUME_MESH_GENERATED", "volumeMeshGenerated":True, "originalMeshesImmutable":True,
                  "simulationExecuted":False,"actualCfdRunVerified":False,"mainApplicationWrites":False,
                  "classroomScaleAssumption":{"nativeUnitsToMetres":1,"verifiedFromSource":False}, "sources":sources,"meshes":meshes}
        (root / "remesh-report.json").write_text(json.dumps(report))
        return report

    def test_source_pins_actual_file_hashes_and_missing_meshes_reject(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/"meshes"
            report = self.input_fixture(root)
            with patch.object(case, "PATCHES", {"impeller":{"inlet"},"volute":{"mass-flow-inlet-11"}}):
                inputs,_ = case.validate_inputs(root)
                self.assertEqual(set(inputs), {"impeller","volute"})
                for key,value in (("actualCfdRunVerified",True),("volumeMeshGenerated",False),("exampleDataCommit","0"*40)):
                    changed = copy.deepcopy(report); changed[key]=value
                    (root/"remesh-report.json").write_text(json.dumps(changed))
                    with self.subTest(key=key), self.assertRaises(case.CaseIssue):
                        case.validate_inputs(root)
                (root/"remesh-report.json").write_text(json.dumps(report))
                (root/"volute.msh").write_bytes(b"not a real mesh")
                with self.assertRaises(case.CaseIssue):
                    case.validate_inputs(root)

    def test_preparation_new_runner_directory_no_execution_and_honest_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = root/"meshes"; self.input_fixture(inputs)
            with patch.dict(os.environ,{"RUNNER_TEMP":str(root)}), patch.object(case,"PATCHES",{"impeller":{"inlet"},"volute":{"mass-flow-inlet-11"}}):
                result = case.prepare(inputs, root/"cases")
                report = json.loads((root/"cases/openfoam-preparation.json").read_text())
                self.assertFalse(report["commandExecutionPerformedByThisTool"])
                self.assertFalse(report["qualifiedBaseline"])
                self.assertFalse(report["actualCfdRunVerified"])
                self.assertFalse(report["mainApplicationWrites"])
                self.assertFalse(report["sampling"]["iterationIndexIsPhysicalTime"])
                self.assertFalse(report["classroomAssumptions"]["nativeUnitsVerified"])
                self.assertIn("wall defaults were not verified",report["classroomAssumptions"]["wallAdaptation"])
                self.assertEqual(report["nativeCommandArguments"][-1][0],"foamRun")
                self.assertIn("-addCases",report["nativeCommandArguments"][4])
                self.assertEqual(len(result["caseDirectories"]),2)
                with self.assertRaises((case.CaseIssue, case.inventory.InspectionIssue)):
                    case.prepare(inputs,root/"cases")
                with self.assertRaises(case.inventory.InspectionIssue):
                    case.prepare(inputs,root.parent/"outside-runner-case")

    def native_fixture(self, root):
        mesh = root/"constant/polyMesh"
        mesh.mkdir(parents=True)
        contents = {"points": "4\n((0 0 0)(1 0 0)(0 1 0)(0 0 1))", "owner":"4\n(0 0 0 0)", "neighbour":"0\n()",
                    "faces":"4\n(3(0 2 1)3(0 1 3)3(1 2 3)3(2 0 3))",
                    "boundary":"1\n(inlet {type patch; nFaces 4; startFace 0;})",
                    "cellZones":"1\n(rotating-fluid {type cellZone; cellLabels List<label> 1(0);})"}
        for name,body in contents.items():
            kind={"points":"vectorField","faces":"faceList","owner":"labelList","neighbour":"labelList","boundary":"polyBoundaryMesh","cellZones":"regIOobject"}[name]
            (mesh/name).write_text(case.foam_header(name,kind)+body)
        log=root/"log.gmshToFoam";log.write_text("Create mesh\nEnd\n")
        return mesh,log

    def test_native_converter_readback_requires_real_topology_zone_and_coordinates(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh,log=self.native_fixture(root)
            expected=case.msh_inventory(MSH)
            actual=case.verify_conversion(root,expected,log)
            self.assertTrue(actual["conversionVerified"])
            self.assertFalse(actual["actualCfdRunVerified"])
            self.assertFalse(actual["nativeGeometryQualityVerified"])
            self.assertEqual(actual["maximumCoordinateDeviationAssumedM"],0)
            replacements={"points":("(1 0 0)","(1.001 0 0)"),"owner":("0 0 0 0","0 0 0 1"),
                          "faces":("3(0 2 1)","3(0 2 99)"),"boundary":("nFaces 4","nFaces 3"),
                          "cellZones":("rotating-fluid","other-fluid")}
            for name,(before,after) in replacements.items():
                original=(mesh/name).read_text();(mesh/name).write_text(original.replace(before,after))
                with self.subTest(name=name),self.assertRaises(case.CaseIssue):
                    case.verify_conversion(root,expected,log)
                (mesh/name).write_text(original)
            log.write_text("FOAM FATAL ERROR\nEnd\n")
            with self.assertRaises(case.CaseIssue):case.verify_conversion(root,expected,log)

    def test_native_header_class_or_object_mismatch_rejects(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh,log=self.native_fixture(root)
            expected=case.msh_inventory(MSH)
            original=(mesh/"points").read_text()
            for wrong in (original.replace("class vectorField","class labelList"),original.replace("object points","object other"),original.replace("format ascii","format binary")):
                (mesh/"points").write_text(wrong)
                with self.subTest(wrong=wrong),self.assertRaises(case.CaseIssue):case.verify_conversion(root,expected,log)

    def test_native_empty_default_placeholder_allowed_positive_unassigned_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh,log=self.native_fixture(root)
            expected=case.msh_inventory(MSH)
            header=case.foam_header("boundary","polyBoundaryMesh")
            (mesh/"boundary").write_text(header+"2\n(inlet {type patch; nFaces 4; startFace 0;} defaultFaces {type patch; nFaces 0; startFace 4;})")
            report=case.verify_conversion(root,expected,log)
            self.assertEqual(report["emptyDefaultFacesPlaceholder"]["nFaces"],0)
            self.assertEqual(report["patchFaces"],{"inlet":4})
            for body in ("defaultFaces {type wall; nFaces 0; startFace 4;}","defaultFaces {type patch; nFaces 1; startFace 4;}"):
                (mesh/"boundary").write_text(header+"2\n(inlet {type patch; nFaces 4; startFace 0;} "+body+")")
                with self.subTest(body=body),self.assertRaises(case.CaseIssue):case.verify_conversion(root,expected,log)


if __name__ == "__main__":
    unittest.main()
