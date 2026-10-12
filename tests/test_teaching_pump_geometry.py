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
import teaching_tetra_corner_refinement as corner


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


def retained_fixture(root,mesh_level="coarse"):
    """Exercise byte replay with explicit synthetic BREP; no OCC has executed."""
    root.mkdir()
    data = cubes(); plan = geometry.teaching_plan(mesh_level=mesh_level); components = []
    threshold = max(n for item in data.values() for n in item['points']); next_node = threshold + 1
    for name, item in list(data.items()):
        refined, proof, next_node = corner.refine_component(item, next_node, threshold)
        data[name] = geometry.component_data(name, refined['points'], refined['tets'], refined['patches'])
        data[name]['cornerSubdivisionEvidence'] = proof
    for name, item in data.items():
        (root / (name + ".msh")).write_bytes(geometry.msh_bytes(name, item))
        components.append({"component":name,"cellZone":"rotating-fluid" if name == "impeller" else "stationary-fluid",
            **item["metrics"],"cornerSubdivisionEvidence":item['cornerSubdivisionEvidence'],"generatedFile":{**geometry.record(root / (name + ".msh")),"format":"gmsh-msh2.2-ascii"},
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
    processor_source = Path(corner.__file__)
    processor = {"path":"tools/teaching_tetra_corner_refinement.py","bytes":processor_source.stat().st_size,"sha256":sha256(processor_source.read_bytes()).hexdigest()}
    manifest = {"schemaVersion":geometry.MANIFEST_SCHEMA,"provenance":"CLASSROOM_AUTHORED_GEOMETRY","units":"SI",
        "parameters":plan["parameters"],"parametersSha256":plan["parametersSha256"],"geometryIdentity":plan["geometryIdentity"],
        "geometryIdentitySha256":plan["geometryIdentitySha256"],"meshLevel":mesh_level,"meshSizeM":plan["meshSizeM"],
        "generatorSourceCommit":"a"*40,"generatorSourceFile":generator,"meshPostProcessorSourceFile":processor,"cadEvidenceSha256":sha256(geometry.encoded(cad)).hexdigest(),
        "limits":dict(geometry.LIMITS),"files":[geometry.record(root / name) for name in geometry.FILES],"mainApplicationWrites":False}
    if "resolutionSizing" in plan:manifest["resolutionSizing"]=plan["resolutionSizing"]
    (root / geometry.MANIFEST).write_bytes(geometry.encoded(manifest))
    report = {"schemaVersion":geometry.SCHEMA,"createdAt":"2026-10-12T12:00:00+00:00",
        "status":"AUTHORED_CAD_VOLUME_MESH_NATIVE_CHECKS_REQUIRED","provenance":"CLASSROOM_AUTHORED_GEOMETRY","units":"SI",
        "generatorSourceCommit":manifest["generatorSourceCommit"],"generatorSourceFile":generator,"meshPostProcessorSourceFile":processor,
        "parametersSha256":plan["parametersSha256"],"geometryIdentitySha256":plan["geometryIdentitySha256"],
        "inputManifestSha256":sha256(geometry.encoded(manifest)).hexdigest(),"parameters":plan["parameters"],"meshLevel":mesh_level,
        "limits":dict(geometry.LIMITS),"gmshVersion":geometry.GMSH_VERSION,"nativeCADConstructed":True,"volumeMeshGenerated":True,
        "components":components,"commonInterfaceEvidence":geometry.common_interface(data["impeller"],data["volute"],[10]),
        "cadEvidence":cad,"cadEvidenceSha256":manifest["cadEvidenceSha256"],"equationStages":plan["equationStages"],
        "elapsedSeconds":1.,"references":geometry.REFERENCES,**{key:False for key in geometry.FALSE_FLAGS}}
    if "resolutionSizing" in plan:report["resolutionSizing"]=plan["resolutionSizing"]
    (root / geometry.REPORT).write_bytes(geometry.encoded(report))
    return report, manifest


class TeachingParametersTests(unittest.TestCase):
    def test_old_head_pure_plan_bytes_are_unchanged_for_all_legacy_levels(self):
        # Captured before this delta from HEAD c38069d38cd84006a3ff8ba824b59eddec3a8cdd.
        # These are pure canonical plan hashes, not native execution acceptance.
        golden={
            'default:coarse':'58a065793dd5115f68b7c31a59704aeea3bfa69f0b3d5bd82b21ded1a8605794',
            'default:medium':'77b4e6f4882a4e422a7b69ed2191a4b09eea305dc0f53d8923f2ebbdb6b49687',
            'default:fine':'765b6ccb650bd85e3bf9a60dcd9c507444ef62ef590da9566870585811d16b3a',
            'operating:coarse':'50650f14fcf8a6270039aba826e0bf92e73d53477c0bedd254aeec69a2ea5061',
            'operating:medium':'abcf8f18c0a77edea6344c9789a0eaeac99c0b6f94faefcfb3e4025a129c96c4',
            'operating:fine':'59852da358eedc663a3589010e44d15f476bbac94303d268586241fdafd8885b'}
        for tag,values in (('default',None),('operating',{'speedRpm':1800.,'massFlowKgS':40.})):
            for level in ('coarse','medium','fine'):
                plan=geometry.teaching_plan(values,level)
                self.assertEqual(sha256(geometry.encoded(plan)).hexdigest(),golden[tag+':'+level])
                self.assertNotIn('resolutionSizing',plan)
                self.assertEqual(plan['meshCellTargetRange'],[200000,500000])
        self.assertEqual(geometry.teaching_plan(),geometry.teaching_plan(mesh_level='coarse'))

    def test_lean_three_sizing_order_same_cad_and_no_result_or_resource_waiver(self):
        plans=[geometry.teaching_plan(mesh_level=level) for level in ('lean-coarse','lean-medium','lean-fine')]
        old=geometry.teaching_plan()
        self.assertEqual([p['meshSizeM'] for p in plans],[.0095,.0075,.0055])
        self.assertTrue(all(plans[i]['meshSizeM']>plans[i+1]['meshSizeM'] for i in range(2)))
        for plan in plans:
            self.assertEqual(geometry.encoded(plan['geometryIdentity']),geometry.encoded(old['geometryIdentity']))
            self.assertEqual(plan['geometryIdentitySha256'],old['geometryIdentitySha256'])
            self.assertEqual(plan['parameters'],old['parameters']);self.assertEqual(plan['equationStages'],old['equationStages'])
            self.assertEqual(plan['limits'],old['limits']);self.assertEqual(plan['meshCellTargetRange'],[50000,250000])
            sizing=plan['resolutionSizing'];self.assertEqual(sizing['family'],'lean-three')
            self.assertFalse(sizing['targetIsMeasuredCellCount']);self.assertFalse(sizing['targetIsAcceptanceGate'])
            self.assertFalse(sizing['countsPredictedOrGuaranteed']);self.assertFalse(sizing['sameDiscreteBoundaryClaimed'])
            self.assertFalse(sizing['meshConvergenceDemonstrated']);self.assertFalse(sizing['oldNativePacketCompatibilityClaimed'])
            self.assertTrue(sizing['nativeLimitsUnchanged']);self.assertTrue(all(plan[k] is False for k in geometry.FALSE_FLAGS))
            self.assertNotIn('cellCount',plan);self.assertNotIn('actualCombinedCellCount',sizing)
        self.assertEqual(geometry.LEGACY_MESH_LEVELS,{'coarse':.0055,'medium':.0042,'fine':.0032})
        self.assertEqual(geometry.LIMITS['maximumCombinedCells'],500000)
        # Independent plan objects cannot mutate the shared sizing declaration.
        plans[0]['resolutionSizing']['declaredCombinedCellTargetRange'][0]=1
        self.assertEqual(geometry.teaching_plan(mesh_level='lean-coarse')['meshCellTargetRange'],[50000,250000])

    def test_geometry_workflow_keeps_default_old_choices_and_adds_only_lean_three(self):
        path=Path(__file__).resolve().parents[1]/'.github/workflows/teaching-pump-free-geometry.yml';text=path.read_text()
        self.assertIn('default: coarse',text)
        self.assertIn('options: [coarse, medium, fine, lean-coarse, lean-medium, lean-fine]',text)
        self.assertIn('case "$TEACHING_MESH_LEVEL" in coarse|medium|fine|lean-coarse|lean-medium|lean-fine) ;; *) exit 2 ;; esac',text)
        self.assertIn('timeout-minutes: 45',text);self.assertIn('CPUQuota=100%',text)
        self.assertIn('MemoryMax=4294967296',text);self.assertIn('MemoryMax=2147483648',text)
        self.assertIn('tools/public_runner_guard.py',text);self.assertIn('tools/run_teaching_pump_geometry.py',text)
        self.assertNotIn('run_teaching_pump_baseline.py',text);self.assertNotIn('run_teaching_pump_phase.py',text)

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
    def test_lean_sizing_target_does_not_replace_measured_topology_or_become_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'mesh';report,manifest=retained_fixture(root,'lean-coarse')
            result=geometry.verify_output(root)
            self.assertEqual(result['report'],report)
            actual=sum(c['cellCount'] for c in report['components'])
            self.assertLess(actual,50000)  # Explicit small synthetic topology, not a real mesh.
            self.assertNotIn('resolutionSizing',manifest['geometryIdentity'])
            self.assertTrue(all(report[k] is False for k in geometry.FALSE_FLAGS))

    def test_rehashed_lean_sizing_omission_promotion_range_or_label_tamper_rejects(self):
        for change in ('omit','range','promote','old-label'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                root=Path(temp)/'mesh';report,manifest=retained_fixture(root,'lean-medium')
                if change=='omit':report.pop('resolutionSizing');manifest.pop('resolutionSizing')
                elif change=='range':report['resolutionSizing']['declaredCombinedCellTargetRange']=[1,500000];manifest['resolutionSizing']=deepcopy(report['resolutionSizing'])
                elif change=='promote':report['resolutionSizing']['meshConvergenceDemonstrated']=True;manifest['resolutionSizing']=deepcopy(report['resolutionSizing'])
                else:report['meshLevel']=manifest['meshLevel']='medium'
                (root/geometry.MANIFEST).write_bytes(geometry.encoded(manifest));report['inputManifestSha256']=sha256(geometry.encoded(manifest)).hexdigest()
                (root/geometry.REPORT).write_bytes(geometry.encoded(report))
                with self.assertRaises(geometry.GeometryIssue):geometry.verify_output(root)

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
