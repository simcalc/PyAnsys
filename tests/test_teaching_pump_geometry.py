"""Synthetic geometric/parser contracts only; fixtures are not native CAD or CFD."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import teaching_pump_geometry as geometry


def cubes():
    """Two six-tet unit cubes with one exact common, oppositely owned interface."""
    points = {1 + 4*x + 2*y + z: (float(x), float(y), float(z))
              for x in range(3) for y in range(2) for z in range(2)}
    result = {}
    for component, x in (("impeller", 0), ("volute", 1)):
        corner = [(x,0,0),(x+1,0,0),(x+1,1,0),(x,1,0),
                  (x,0,1),(x+1,0,1),(x+1,1,1),(x,1,1)]
        labels = [1 + 4*a + 2*b + c for a,b,c in corner]
        tets = [tuple(labels[i] for i in row) for row in
                ((0,1,2,6),(0,2,3,6),(0,3,7,6),(0,7,4,6),(0,4,5,6),(0,5,1,6))]
        counts = geometry.Counter(tuple(sorted(face)) for tet in tets for face in geometry.outward_faces(tet))
        faces = [face for tet in tets for face in geometry.outward_faces(tet) if counts[tuple(sorted(face))] == 1]
        role = geometry.INTERFACE[component]
        others = [name for name in geometry.ROLES[component] if name != role]
        patches = {name: [] for name in geometry.ROLES[component]}
        index = 0
        for face in faces:
            if all(points[n][0] == 1 for n in face): patches[role].append(face)
            else: patches[others[index % len(others)]].append(face); index += 1
        result[component] = geometry.component_data(component, points, tets, patches)
    return result


def retained_fixture(root):
    """Exercise byte replay with explicit synthetic BREP; no OCC has executed."""
    root.mkdir()
    data = cubes(); plan = geometry.teaching_plan(); components = []
    for name, item in data.items():
        (root / (name + ".msh")).write_bytes(geometry.msh_bytes(name, item))
        components.append({"component":name,"cellZone":"rotating-fluid" if name == "impeller" else "stationary-fluid",
            **item["metrics"],"generatedFile":{**geometry.record(root / (name + ".msh")),"format":"gmsh-msh2.2-ascii"},
            "cadFluidVolumeM3":1.,"cadMeshRelativeVolumeDifference":0.})
    (root / "teaching-pump.brep").write_bytes(b"SYNTHETIC-ONLY-NO-NATIVE-OCC\n")
    roles = {}; tag = 20
    for component in geometry.ROLES:
        roles[component] = {}
        for role in geometry.ROLES[component]:
            roles[component][role] = [10] if role == geometry.INTERFACE[component] else [tag]
            tag += 1
    cad = {"volumeGroups":{"impeller":[1],"volute":[2]},"surfaceRoles":roles,
        "commonInterfaceSurfaceIds":[10],"cadFluidVolumesM3":{"impeller":1.,"volute":1.},
        "cadSurfaceClassificationToleranceM":3e-7,"cadSurfaceRolesBasedOnAuthoredAnalyticRegions":True}
    source = Path(geometry.__file__)
    generator = {"path":"tools/teaching_pump_geometry.py","bytes":source.stat().st_size,"sha256":sha256(source.read_bytes()).hexdigest()}
    manifest = {"schemaVersion":geometry.MANIFEST_SCHEMA,"provenance":"CLASSROOM_AUTHORED_GEOMETRY","units":"SI",
        "parameters":plan["parameters"],"parametersSha256":plan["parametersSha256"],"geometryIdentity":plan["geometryIdentity"],
        "geometryIdentitySha256":plan["geometryIdentitySha256"],"meshLevel":"coarse","meshSizeM":plan["meshSizeM"],
        "generatorSourceCommit":"a"*40,"generatorSourceFile":generator,"cadEvidenceSha256":sha256(geometry.encoded(cad)).hexdigest(),
        "limits":dict(geometry.LIMITS),"files":[geometry.record(root / name) for name in geometry.FILES],"mainApplicationWrites":False}
    (root / geometry.MANIFEST).write_bytes(geometry.encoded(manifest))
    report = {"schemaVersion":geometry.SCHEMA,"createdAt":"2026-10-12T12:00:00+00:00",
        "status":"AUTHORED_CAD_VOLUME_MESH_NATIVE_CHECKS_REQUIRED","provenance":"CLASSROOM_AUTHORED_GEOMETRY","units":"SI",
        "generatorSourceCommit":manifest["generatorSourceCommit"],"generatorSourceFile":generator,
        "parametersSha256":plan["parametersSha256"],"geometryIdentitySha256":plan["geometryIdentitySha256"],
        "inputManifestSha256":sha256(geometry.encoded(manifest)).hexdigest(),"parameters":plan["parameters"],"meshLevel":"coarse",
        "limits":dict(geometry.LIMITS),"gmshVersion":geometry.GMSH_VERSION,"nativeCADConstructed":True,"volumeMeshGenerated":True,
        "components":components,"commonInterfaceEvidence":geometry.common_interface(data["impeller"],data["volute"],[10]),
        "cadEvidence":cad,"cadEvidenceSha256":manifest["cadEvidenceSha256"],"equationStages":plan["equationStages"],
        "elapsedSeconds":1.,"references":geometry.REFERENCES,**{key:False for key in geometry.FALSE_FLAGS}}
    (root / geometry.REPORT).write_bytes(geometry.encoded(report))
    return report, manifest


class TeachingParametersTests(unittest.TestCase):
    def test_equations_are_actual_python_input_reductions_with_no_solver_claim(self):
        plan = geometry.teaching_plan(); p = plan["parameters"]
        self.assertAlmostEqual(plan["equationStages"]["angularSpeed"]["result"]["value"], 2*math.pi*1450/60)
        self.assertAlmostEqual(plan["equationStages"]["volumeFlow"]["result"]["value"], 90/998.2)
        self.assertAlmostEqual(plan["equationStages"]["interfaceArea"]["result"]["value"], 2*math.pi*p["interfaceRadiusM"]*p["diskHeightM"])
        for row in plan["equationStages"].values():
            self.assertEqual(row["calculationAuthority"], "Python")
            self.assertEqual(set(row), {"input","equation","substitution","result","calculationAuthority"})
        self.assertTrue(all(plan[key] is False for key in geometry.FALSE_FLAGS))
        self.assertFalse(plan["volumeMeshGenerated"])
        self.assertIn("assumptions", plan["fluidBasis"])
        self.assertEqual(plan["provenance"], "CLASSROOM_AUTHORED_GEOMETRY")

    def test_geometry_identity_is_stable_across_refinements_and_operating_inputs(self):
        coarse = geometry.teaching_plan()
        fine = geometry.teaching_plan({"speedRpm":1800.,"massFlowKgS":40.}, "fine")
        self.assertEqual(coarse["geometryIdentitySha256"], fine["geometryIdentitySha256"])
        self.assertNotEqual(coarse["parametersSha256"], fine["parametersSha256"])
        self.assertNotEqual(coarse["meshSizeM"], fine["meshSizeM"])
        changed = geometry.teaching_plan({"diskHeightM":.05})
        self.assertNotEqual(coarse["geometryIdentitySha256"], changed["geometryIdentitySha256"])

    def test_bad_units_values_unknowns_clearances_and_forward_blades_reject(self):
        bad = ({"password":"secret"},{"speedRpm":True},{"densityKgM3":math.nan},{"hubRadiusM":math.inf},
               {"bladeCount":6.0},{"bladeCount":13},{"interfaceRadiusM":.129}, {"eyeRadiusM":.087},
               {"bladeSweepRad":.7},{"voluteWidthEndM":.009},{"outletLengthM":.01},{"viscosityPaS":0.})
        for values in bad:
            with self.subTest(values=values), self.assertRaises(geometry.GeometryIssue): geometry.teaching_plan(values)
        for level in ("ultra", {}, None):
            with self.assertRaises(geometry.GeometryIssue): geometry.teaching_plan(mesh_level=level)

    def test_blade_strip_has_declared_thickness_backward_sweep_and_clearance(self):
        p = geometry.validate_parameters(); left, right = geometry.blade_edges(p,0)
        self.assertEqual(len(left),13)
        centres = [tuple((a[i]+b[i])/2 for i in range(3)) for a,b in zip(left,right)]
        self.assertAlmostEqual(math.atan2(centres[-1][1],centres[-1][0]), p["bladeSweepRad"])
        for a,b in zip(left,right):
            self.assertAlmostEqual(math.dist(a,b),p["bladeThicknessM"])
            self.assertTrue(all(p["eyeRadiusM"] < math.hypot(v[0],v[1]) < p["interfaceRadiusM"] for v in (a,b)))

    def test_plan_only_launches_no_gmsh_or_files(self):
        with mock.patch.object(sys,"argv",["geometry","--plan-only"]), mock.patch.object(geometry,"supervise") as native, mock.patch("sys.stdout",new_callable=io.StringIO) as stdout:
            self.assertEqual(geometry.main(),0)
            self.assertEqual(json.loads(stdout.getvalue())["status"],"AuthoredPlanNativeCADAndMeshRequired")
            native.assert_not_called()


class TeachingTopologyTests(unittest.TestCase):
    def test_independent_export_replay_has_positive_cells_and_shared_outward_interface(self):
        data = cubes()
        for component,item in data.items():
            replay = geometry.parse_msh(component,geometry.msh_bytes(component,item))
            self.assertEqual(replay,item)
            self.assertEqual(item["metrics"]["cellCount"],6)
            self.assertAlmostEqual(item["metrics"]["totalCellVolumeM3"],1.)
            self.assertTrue(item["metrics"]["exactFaceIncidence"]["accepted"])
        proof = geometry.common_interface(data["impeller"],data["volute"],[10])
        self.assertEqual(proof["sharedTriangleCount"],2)
        self.assertEqual(proof["sharedNodeCount"],4)
        self.assertTrue(proof["oppositeOutwardWindingVerified"])
        self.assertFalse(proof["dynamicCoverageVerified"])

    def test_wrong_interface_orientation_coordinate_or_triangle_reject(self):
        for kind in ("orientation","coordinate","triangle"):
            data = cubes(); right = data["volute"]; role = geometry.INTERFACE["volute"]
            if kind == "orientation": right["patches"][role] = [face[::-1] for face in right["patches"][role]]
            elif kind == "coordinate":
                node = right["patches"][role][0][0]; right["points"][node] = (1.000000001,*right["points"][node][1:])
            else: right["patches"][role].pop()
            with self.subTest(kind=kind), self.assertRaises(geometry.GeometryIssue): geometry.common_interface(data["impeller"],right,[10])

    def test_negative_degenerate_nonmanifold_or_missing_boundary_reject(self):
        data = cubes()["impeller"]
        negative = list(data["tets"]); negative[0] = negative[0][::-1]
        # Reversing four entries is even; explicitly swap one pair.
        negative[0] = (data["tets"][0][1],data["tets"][0][0],*data["tets"][0][2:])
        for tets,patches in ((negative,data["patches"]),([tuple([data["tets"][0][0]]*4)],data["patches"]),
                             (data["tets"]+data["tets"][:1],data["patches"]), (data["tets"],{k:v for k,v in data["patches"].items() if k!="blade"})):
            with self.assertRaises(geometry.GeometryIssue): geometry.component_data("impeller",data["points"],tets,patches)

    def test_positive_volume_does_not_replace_cell_cap(self):
        item = cubes()["impeller"]
        with mock.patch.dict(geometry.LIMITS,{"maximumCellsPerComponent":5}):
            with self.assertRaises(geometry.GeometryIssue): geometry.component_data("impeller",item["points"],item["tets"],item["patches"])

    def test_unknown_physical_role_is_rejected(self):
        item = cubes()["impeller"]; patches = deepcopy(item["patches"]); patches["unknown"] = patches.pop("blade")
        with self.assertRaises(geometry.GeometryIssue): geometry.component_data("impeller",item["points"],item["tets"],patches)


class TeachingEvidenceTests(unittest.TestCase):
    def test_complete_synthetic_inventory_replays_without_gmsh(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/"mesh"; report,_ = retained_fixture(root)
            actual = geometry.verify_output(root)
            self.assertEqual(actual["report"],report)
            self.assertEqual(actual["reportFile"]["path"],geometry.REPORT)
            self.assertTrue(all(actual["report"][key] is False for key in geometry.FALSE_FLAGS))

    def test_mesh_brep_manifest_or_extra_file_tamper_is_rejected(self):
        for name in ("impeller.msh","teaching-pump.brep",geometry.MANIFEST,"arbitrary.py"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)/"mesh"; retained_fixture(root)
                if name == geometry.MANIFEST:
                    value = geometry.json_file(root/name); value["parameters"]["diskHeightM"] = .05; (root/name).write_bytes(geometry.encoded(value))
                elif name == "arbitrary.py": (root/name).write_bytes(b"print('unrecognized')\n")
                else: (root/name).write_bytes((root/name).read_bytes()+b"changed\n")
                with self.assertRaises(geometry.GeometryIssue): geometry.verify_output(root)

    def test_false_native_quality_claim_wrong_version_and_missing_roles_reject(self):
        for change in ("claim","version","role","elapsed","units","utc","generator"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)/"mesh"; report,_ = retained_fixture(root)
                if change == "claim": report["cfdReady"] = True
                elif change == "version": report["gmshVersion"] = "4.15.1"
                elif change == "role": report["cadEvidence"]["surfaceRoles"]["impeller"]["blade"] = []
                elif change == "elapsed": report["elapsedSeconds"] = 900.
                elif change == "units": report["units"] = "mm"
                elif change == "utc": report["createdAt"] = "2026-10-12T12:00:00"
                else: report["generatorSourceFile"]["sha256"] = "f"*64
                (root/geometry.REPORT).write_bytes(geometry.encoded(report))
                with self.assertRaises(geometry.GeometryIssue): geometry.verify_output(root)

    def test_duplicate_and_nonfinite_json_reject(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"data.json"
            for raw in (b'{"x":1,"x":2}',b'{"x":NaN}'):
                path.write_bytes(raw)
                with self.assertRaises(geometry.GeometryIssue): geometry.json_file(path)

    def test_linked_evidence_rejects(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/"file"; source.write_bytes(b"bounded")
            link = Path(temp)/"link"
            try: link.symlink_to(source)
            except OSError: self.skipTest("Windows symlink privilege is unavailable")
            with self.assertRaises(geometry.GeometryIssue): geometry.read_regular(link,100)

    def test_guard_rejects_nonpublic_before_output_or_native_import(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ,{"RUNNER_TEMP":temp},clear=True), mock.patch.object(geometry.sys,"platform","linux"):
            target = Path(temp)/"mesh"
            with self.assertRaises(geometry.GeometryIssue): geometry.generate({},target)
            self.assertFalse(target.exists())

    def test_output_requires_fresh_temp_child_and_no_escape(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            self.assertEqual(geometry.output_path(Path(temp)/"fresh"),Path(temp)/"fresh")
            for path in (Path(temp),Path(temp).parent/"outside",Path(temp)/".."/"outside"):
                with self.assertRaises(geometry.GeometryIssue): geometry.output_path(path)

    def test_final_replay_deadline_is_inside_native_budget_and_environment_has_no_secret(self):
        process = SimpleNamespace(stdout=io.BytesIO(),wait=mock.Mock(return_value=0),poll=lambda:0)
        selector = SimpleNamespace(register=lambda *args:None,get_map=lambda:{},close=lambda:None)
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ,{"RUNNER_TEMP":temp,"GH_TOKEN":"NEVER-FORWARD"}), \
             mock.patch.object(geometry,"context_guard"), mock.patch.object(geometry.subprocess,"Popen",return_value=process) as popen, \
             mock.patch.object(geometry.selectors,"DefaultSelector",return_value=selector), \
             mock.patch.object(geometry.time,"monotonic",side_effect=[0.,1.,900.]), \
             mock.patch.object(geometry,"verify_output",return_value={"report":{"synthetic":True}}):
            with self.assertRaisesRegex(geometry.GeometryIssue,"final readback"):
                geometry.supervise(geometry.validate_parameters(),Path(temp)/"mesh","coarse")
            self.assertNotIn("GH_TOKEN",popen.call_args.kwargs["env"])
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertEqual(popen.call_args.kwargs["env"]["OMP_NUM_THREADS"],"1")


class OptionalNativeCADTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("gmsh"), "Pinned Gmsh is not installed locally; do not install or mesh on laptop")
    def test_existing_pinned_gmsh_lightweight_cad_only(self):
        import gmsh
        if gmsh.__version__ != geometry.GMSH_VERSION: self.skipTest("Only existing pinned Gmsh4.15.2 is supported")
        gmsh.initialize()
        try:
            cad = geometry.build_cad(gmsh,geometry.validate_parameters())
            self.assertTrue(cad["commonInterfaceSurfaceIds"])
            self.assertTrue(all(v>0 for v in cad["cadFluidVolumesM3"].values()))
            self.assertTrue(all(tags for roles in cad["surfaceRoles"].values() for tags in roles.values()))
            self.assertEqual(len(gmsh.model.mesh.getElements(3)[0]),0)
        finally: gmsh.finalize()


if __name__ == "__main__": unittest.main()
