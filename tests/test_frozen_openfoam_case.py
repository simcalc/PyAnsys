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


def extended_named_report(original):
    """Declared synthetic profile fields; never proof of native utilization."""
    report = copy.deepcopy(original)
    report.update(resourceProfile="extended_free_hxt", limits=case.remesh_resource_limits("extended_free_hxt"),
                  algorithm="hxt", gmshAlgorithm3D=10, gmshAlgorithmName="HXT", optimisation={"mode":"full", "meshOptimize":1})
    for key in ("boundaryRemeshed", "surfaceReparametrizationPerformed", "sourceGeometryFabricated", "nodeWeldingPerformed", "topologyHealingPerformed"):
        report[key] = False
    for mesh in report["meshes"]:
        mesh.update(algorithm="hxt", gmshAlgorithm3D=10, gmshAlgorithmName="HXT", optimisation={"mode":"full", "meshOptimize":1},
                    boundaryRemeshed=False, surfaceReparametrizationPerformed=False)
    report["runtimeResourceEvidence"] = {"publicRepository":"simcalc/PyAnsys", "manualWorkflowContextVerified":True,
        "runnerEnvironment":"github-hosted", "runnerOs":"Linux", "availableCpuCount":4, "physicalMemoryBytes":16*1024**3,
        "minimumCpuCount":4, "minimumPhysicalMemoryBytes":14*1024**3, "paidFallback":False, "customRunnerAllowed":False,
        "basis":"Actual os.cpu_count and bounded procfs MemTotal; repository public verification is performed by the workflow guard.",
        "addressSpaceSoftLimitBytes":8*1024**3, "addressSpaceHardLimitBytes":8*1024**3,
        "workerOpenMpThreads":3, "workerBlasThreads":{"OPENBLAS_NUM_THREADS":1, "MKL_NUM_THREADS":1, "NUMEXPR_NUM_THREADS":1},
        "nativeThreadOptions":{"General.NumThreads":3, "Mesh.MaxNumThreads1D":3, "Mesh.MaxNumThreads2D":3, "Mesh.MaxNumThreads3D":3}}
    return report


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
        self.assertIn("fields true;", files["system/createNonConformalCouplesDict"])
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
        self.assertIn("type volFieldValue", functions)
        self.assertIn("select all; operation min; fields (p)", functions)
        self.assertIn("select all; operation max; fields (p)", functions)
        self.assertIn("type yPlus", functions)
        self.assertIn("name pumpInterface_on_interface-impeller-outlet", functions)
        self.assertIn("name pumpInterface_on_interface-volute-inlet", functions)
        self.assertNotIn("weightedAverage", functions)
        self.assertNotIn("regionType", functions)

    def test_combined_case_fields_cover_both_components_and_empty_placeholder(self):
        files = case.case_files("impeller")
        combined = set().union(*case.PATCHES.values()) | {"defaultFaces"}
        for name in ("0/U", "0/p", "0/k", "0/omega", "0/nut"):
            for patch_name in combined:
                with self.subTest(field=name, patch=patch_name):
                    self.assertIn('"' + patch_name + '"', files[name])
        self.assertIn("flowRateOutletVelocity", files["0/U"])
        self.assertIn("volute-wall", files["0/omega"])
        self.assertNotIn("volute-wall", files["system/changeDictionaryDict"])
        self.assertNotIn("blade", case.case_files("volute")["0/U"])

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
                  "classroomScaleAssumption":{"nativeUnitsToMetres":1,"verifiedFromSource":False}, "sources":sources,"meshes":meshes,
                  "limits":case.remesh_resource_limits()}
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

    def test_extended_named_profile_admits_current_bytes_without_changing_case_or_solver_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"meshes";default=self.input_fixture(root);extended=extended_named_report(default)
            with patch.object(case,"PATCHES",{"impeller":{"inlet"},"volute":{"mass-flow-inlet-11"}}):
                first,default_hash=case.validate_inputs(root)
                (root/"remesh-report.json").write_text(json.dumps(extended));second,extended_hash=case.validate_inputs(root)
                self.assertEqual(first,second);self.assertNotEqual(default_hash,extended_hash)
            self.assertEqual(case.validate_remesh_resource_profile(extended),"extended_free_hxt")
            self.assertEqual(case.remesh_resource_limits()["maximumSeconds"],900)
            self.assertEqual(case.remesh_resource_limits()["maximumAddressSpaceBytes"],2*1024**3)
            self.assertIn("endTime 500",case.case_files("impeller")["system/controlDict"])

    def test_profile_identity_limits_and_actual_hardware_enforcement_are_exact(self):
        changes=(lambda v:v.pop("resourceProfile"),lambda v:v.update(resourceProfile="other"),lambda v:v.update(resourceProfile=[]),
            lambda v:v.update(resourceProfile={}),
            lambda v:v.pop("limits"),lambda v:v["limits"].update(threads=2),lambda v:v["limits"].update(threads=3.),
            lambda v:v["limits"].update(maximumSeconds=3000),lambda v:v["limits"].update(maximumOutputBytes=256*1024**2),
            lambda v:v["limits"].update(maximumCombinedCells=1000000),lambda v:v.update(algorithm="delaunay",gmshAlgorithm3D=1),
            lambda v:v["optimisation"].update(mode="unoptimised",meshOptimize=0),lambda v:v["meshes"][0].update(gmshAlgorithm3D=1),
            lambda v:v.update(boundaryRemeshed=True),lambda v:v.pop("runtimeResourceEvidence"),
            lambda v:v["runtimeResourceEvidence"].update(availableCpuCount=3),lambda v:v["runtimeResourceEvidence"].update(availableCpuCount=4.),
            lambda v:v["runtimeResourceEvidence"].update(availableCpuCount=4097),
            lambda v:v["runtimeResourceEvidence"].update(physicalMemoryBytes=14*1024**3-1),
            lambda v:v["runtimeResourceEvidence"].update(physicalMemoryBytes=1024**5+1),
            lambda v:v["runtimeResourceEvidence"].update(runnerEnvironment="self-hosted"),lambda v:v["runtimeResourceEvidence"].update(paidFallback=True),
            lambda v:v["runtimeResourceEvidence"].update(addressSpaceSoftLimitBytes=16*1024**3),
            lambda v:v["runtimeResourceEvidence"]["workerBlasThreads"].update(OPENBLAS_NUM_THREADS=3),
            lambda v:v["runtimeResourceEvidence"]["nativeThreadOptions"].update({"General.NumThreads":2}))
        with tempfile.TemporaryDirectory() as temp:
            original=extended_named_report(self.input_fixture(Path(temp)/"meshes"))
            for change in changes:
                report=copy.deepcopy(original);change(report)
                with self.assertRaises(case.CaseIssue):case.validate_remesh_resource_profile(report)

    def test_default_legacy_limits_are_exact_and_missing_metadata_cannot_hide_extension(self):
        default={"limits":case.remesh_resource_limits()}
        self.assertEqual(case.validate_remesh_resource_profile(default),"classroom")
        for report in ({}, {"limits":case.remesh_resource_limits("extended_free_hxt")},
                       {"limits":case.remesh_resource_limits(),"resourceProfile":"classroom"},
                       {"limits":case.remesh_resource_limits(),"runtimeResourceEvidence":{}}):
            with self.subTest(report=report),self.assertRaises(case.CaseIssue):case.validate_remesh_resource_profile(report)

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

    def test_wall_class_change_has_separate_native_pre_and_post_gates(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh,log=self.native_fixture(root)
            expected=case.msh_inventory(MSH)
            expected["patchFaces"]={"blade":4}
            boundary=(mesh/"boundary").read_text().replace("inlet {", "blade {")
            (mesh/"boundary").write_text(boundary)
            before=case.verify_conversion(root,expected,log,after_wall_type_change=False)
            self.assertTrue(before["conversionVerified"])
            self.assertFalse(before["physicalWallTypesVerified"])
            with self.assertRaises(case.CaseIssue):
                case.verify_conversion(root,expected,log,after_wall_type_change=True)
            (mesh/"boundary").write_text(boundary.replace("type patch;", "type wall;"))
            after=case.verify_conversion(root,expected,log,after_wall_type_change=True)
            self.assertTrue(after["physicalWallTypesVerified"])
            with self.assertRaises(case.CaseIssue):
                case.verify_conversion(root,expected,log,after_wall_type_change="false")

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


    def merged_fixture(self, root):
        """Synthetic metadata only; its log is not actual native checkMesh evidence."""
        mesh=root/"constant/polyMesh";mesh.mkdir(parents=True)
        components={component:{"cellZone":"rotating-fluid" if component=="impeller" else "stationary-fluid",
                               "nodeCount":4,"cellCount":1,"patchFaces":{name:1 for name in names}}
                    for component,names in case.PATCHES.items()}
        patches=[];owners=[];faces=[];start=0
        for cell,component in enumerate(("impeller","volute")):
            for name in sorted(case.PATCHES[component]):
                kind="wall" if name in case.ROTATING|case.STATIONARY else "patch"
                patches.append(f"{name} {{type {kind}; nFaces 1; startFace {start};}}")
                owners.append(cell);faces.append("3("+" ".join(str(value+4*cell) for value in (0,1,2))+")")
                start+=1
        patches.append(f"defaultFaces {{type patch; nFaces 0; startFace {start};}}")
        originals=("interface-impeller-outlet","interface-volute-inlet")
        for original,opposite in zip(originals,reversed(originals)):
            patches.append("pumpInterface_on_"+original+f" {{type nonConformalCyclic; nFaces 0; startFace {start}; originalPatch {original}; neighbourPatch pumpInterface_on_{opposite}; transformType none;}}")
        for original in originals:
            patches.append("nonConformalError_on_"+original+f" {{type nonConformalError; nFaces 0; startFace {start}; originalPatch {original};}}")
        contents={"points":"8\n((0 0 0)(1 0 0)(0 1 0)(0 0 1)(2 0 0)(3 0 0)(2 1 0)(2 0 1))",
                  "owner":str(len(owners))+"\n("+" ".join(map(str,owners))+")","neighbour":"0\n()",
                  "faces":str(len(faces))+"\n("+"".join(faces)+")",
                  "boundary":str(len(patches))+"\n("+"\n".join(patches)+")",
                  "cellZones":"2\n(rotating-fluid {type cellZone; cellLabels List<label> 1(0);} stationary-fluid {type cellZone; cellLabels List<label> 1(1);})"}
        for name,body in contents.items():
            kind={"points":"vectorField","faces":"faceList","owner":"labelList","neighbour":"labelList","boundary":"polyBoundaryMesh","cellZones":"regIOobject"}[name]
            (mesh/name).write_text(case.foam_header(name,kind)+body)
        log=root/"log.checkMesh"
        log.write_text("# SYNTHETIC-CONTRACT-NOT-NATIVE-CFD\n    cells: 2\nSource min/average/max coverage = 0.999/1/1\nTarget min/average/max coverage = 1/1/1\nMesh OK.\nEnd\n")
        return mesh,components,log

    def test_merged_gate_binds_actual_zone_ownership_source_patches_and_ncc_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh,expected,log=self.merged_fixture(root)
            result=case.verify_merged_mesh(root,expected,log)
            self.assertEqual(result["mergedCellCount"],2)
            self.assertEqual(result["cellZones"],{"rotating-fluid":1,"stationary-fluid":1})
            self.assertEqual(len(result["sourcePatchFaces"]),11)
            self.assertEqual(len(result["nonConformalCyclicPatches"]),2)
            self.assertEqual(len(result["nonConformalErrorPatches"]),2)
            self.assertTrue(result["coupledInterfaceCoverageVerified"])
            self.assertFalse(result["actualCfdRunVerified"])
            self.assertFalse(result["qualifiedBaseline"])
            self.assertFalse(result["mainApplicationWrites"])
            self.assertEqual(result["checkMeshLogSha256"],case.sha256(log.read_bytes()).hexdigest())

    def test_merged_gate_requires_real_completed_log_matching_cells_and_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh,expected,log=self.merged_fixture(root)
            original=log.read_text()
            for wrong in (original.replace("cells: 2","cells: 3"),original.replace("Mesh OK.","Failed 1 mesh checks."),
                          original.replace("End\n",""),original.replace("Source min/average/max coverage = 0.999/1/1\nTarget min/average/max coverage = 1/1/1\n",""),
                          original.replace("0.999/1/1","0.98/0.998/1"),original.replace("0.999/1/1","0.999/1/1.02"),
                          original.replace("0.999/1/1","1/0.999/1"),original+"FOAM FATAL ERROR\n"):
                log.write_text(wrong)
                with self.subTest(log=wrong),self.assertRaises(case.CaseIssue):case.verify_merged_mesh(root,expected,log)

    def test_merged_gate_rejects_changed_zone_identity_coverage_or_patch_ownership(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh,expected,log=self.merged_fixture(root)
            original=(mesh/"cellZones").read_text()
            for wrong in (original.replace("stationary-fluid","other-fluid"),original.replace("1(1)","1(0)"),
                          original.replace("1(0)","1(9)"),original.replace("1(0)","1(1)").replace("stationary-fluid {type cellZone; cellLabels List<label> 1(1)","stationary-fluid {type cellZone; cellLabels List<label> 1(0)")):
                (mesh/"cellZones").write_text(wrong)
                with self.subTest(zone=wrong),self.assertRaises(case.CaseIssue):case.verify_merged_mesh(root,expected,log)

    def test_merged_gate_rejects_missing_or_wrong_ncc_and_physical_wall_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh,expected,log=self.merged_fixture(root)
            original=(mesh/"boundary").read_text()
            for wrong in (original.replace("blade {type wall","blade {type patch"),
                          original.replace("neighbourPatch pumpInterface_on_interface-volute-inlet","neighbourPatch wrongPair"),
                          original.replace("originalPatch interface-impeller-outlet","originalPatch inlet"),
                          original.replace("transformType none","transformType rotational"),
                          original.replace("type nonConformalError","type patch"),
                          original.replace("pumpInterface_on_interface-impeller-outlet {type nonConformalCyclic","otherCouple {type nonConformalCyclic"),
                          original.replace("defaultFaces {type patch; nFaces 0","defaultFaces {type patch; nFaces 1")):
                (mesh/"boundary").write_text(wrong)
                with self.subTest(boundary=wrong),self.assertRaises(case.CaseIssue):case.verify_merged_mesh(root,expected,log)

    def test_merged_gate_rejects_changed_points_faces_and_owner_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh,expected,log=self.merged_fixture(root)
            for name,before,after in (("points","8\n(","7\n("),("points","(1 0 0)","(nan 0 0)"),
                                      ("faces","3(0 1 2)","3(0 1 9)"),("owner","0 0 0 0 0 0 0","0 0 0 0 0 0 9")):
                original=(mesh/name).read_text();(mesh/name).write_text(original.replace(before,after))
                with self.subTest(native=name),self.assertRaises(case.CaseIssue):case.verify_merged_mesh(root,expected,log)
                (mesh/name).write_text(original)


if __name__ == "__main__":
    unittest.main()
