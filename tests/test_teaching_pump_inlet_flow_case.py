"""Fresh input/source/serialization tests, never native CFD qualification."""
from contextlib import contextmanager
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import teaching_pump_inlet_flow_case as case
import teaching_pump_geometry as author
from test_teaching_pump_baseline_case import native_source, synthetic_outlet_proof
from test_teaching_pump_native_case import source_fixture
from test_teaching_pump_phase_case import startup_field


def synthetic_inlet_proof(root):
    """Copy tests use a miniature cube, not the authored inlet annulus.

    Only the inlet geometric proof is mocked here. Independent annulus tests
    below exercise its real complete-triangle/count/normal/area/hash parser.
    The source admission otherwise replays its real native topology and logs,
    with the producer CAD and installed binary checks explicitly mocked.
    """
    _,row=case.json_file(root,"source-geometry/teaching-geometry.json")
    return {"schemaVersion":"synthetic-inlet-geometry-not-native-evidence","geometryReportFile":row,
        "nativeMeshFileRecords":[case.record(root,"component-mesh-snapshots/impeller/constant/polyMesh/"+n)[1] for n in case.MESH_FILES],
        "nativeFlowExecuted":False}


def annulus_source(root, segments=64):
    """Native-list annulus fixture for geometry mathematics only, not a mesh."""
    (root/"source-geometry").mkdir(parents=True)
    packet={"parameters":dict(author.DEFAULTS),"units":"SI","provenance":case.ROUTE}
    (root/"source-geometry/teaching-geometry.json").write_bytes(case.encoded(packet))
    mesh=root/"component-mesh-snapshots/impeller/constant/polyMesh";mesh.mkdir(parents=True)
    points=[(r*math.cos(2*math.pi*i/segments),r*math.sin(2*math.pi*i/segments),-.06)
        for r in (.01968,.0844) for i in range(segments)]
    triangles=[]
    for i in range(segments):
        j=(i+1)%segments
        # Every normal is -Z. The exact ring vertices are retained.
        triangles.extend(((i,segments+j,segments+i),(i,j,segments+j)))
    cells=len(triangles)
    def counted(rows): return str(len(rows))+"\n(\n"+"\n".join(rows)+"\n)\n"
    patches=["inlet {type patch; nFaces "+str(cells)+"; startFace 0;}"]
    patches.extend(name+" {type "+("wall" if name in case.base.ROTATING|case.base.STATIONARY else "patch")+
        "; nFaces 0; startFace "+str(cells)+";}" for name in sorted(case.base.PATCHES["impeller"]-{"inlet"}))
    bodies={"points":counted(["("+" ".join(format(x,".17g") for x in p)+")" for p in points]),
        "faces":counted(["3("+" ".join(map(str,t))+")" for t in triangles]),
        "owner":counted(list(map(str,range(cells)))),"neighbour":counted([]),"boundary":counted(patches),
        "cellZones":counted(["rotating-fluid {type cellZone; cellLabels List<label> "+str(cells)+"("+" ".join(map(str,range(cells)))+");}"])}
    kinds={"points":"vectorField","faces":"faceList","owner":"labelList","neighbour":"labelList","boundary":"polyBoundaryMesh","cellZones":"cellZoneList"}
    for name,body in bodies.items():(mesh/name).write_text(case.base.foam_header(name,kinds[name])+body,encoding="ascii")
    return mesh,triangles


class TeachingInletFlowCaseTests(unittest.TestCase):
    @contextmanager
    def environment(self,temp):
        with mock.patch.dict(os.environ,{"RUNNER_TEMP":temp}),mock.patch.object(case.source_io.outlet,"verify_outlet",side_effect=synthetic_outlet_proof):
            yield

    @contextmanager
    def fixture(self,root):
        with self.environment(str(root)):
            placeholder,module=source_fixture(root);placeholder.rename(root/"unused-geometry-fixture")
            with mock.patch.dict(sys.modules,{"teaching_pump_geometry":module}):source,module=native_source(root)
            with mock.patch.dict(sys.modules,{"teaching_pump_geometry":module}),mock.patch.object(case.source_io,"validate_runtime"),\
                mock.patch.object(case,"verify_inlet_geometry",side_effect=synthetic_inlet_proof):
                yield source

    def test_new_recipe_sections_are_distinct_and_all_other_bytes_stay_current(self):
        for component in ("impeller","volute"):
            for placeholder in (False,True):
                old=case.source_io.case_files(component,default_faces=placeholder)
                fresh=case.case_files(component,default_faces=placeholder)
                self.assertEqual(set(old),set(fresh))
                for role in set(old)-{"0/U","0/p"}:self.assertEqual(old[role],fresh[role],role)
                _,u=case.fields.boundary_blocks(fresh["0/U"],"U");_,p=case.fields.boundary_blocks(fresh["0/p"],"p")
                self.assertEqual(u[case.recipe.OUTLET]["type"],"pressureInletOutletVelocity")
                self.assertEqual(p[case.recipe.OUTLET],{"type":"fixedValue","value":"uniform 0"})
                if component=="impeller":
                    self.assertEqual(u["inlet"],{"type":"flowRateInletVelocity","volumetricFlowRate":format(case.recipe.Q,".17g"),"value":"uniform (0 0 0)"})
                    self.assertEqual(p["inlet"]["type"],"fixedFluxPressure")
                else:self.assertNotIn("inlet",u)
        self.assertEqual(case.ASSUMPTIONS,case.recipe.ASSUMPTIONS)
        self.assertNotIn("outletVelocityProfile",case.ASSUMPTIONS)
        self.assertNotIn("externalTotalPressureAbsolutePa",case.ASSUMPTIONS)
        self.assertEqual(case.SAMPLING["pressureReferenceLocation"],"outlet")

    def test_prepare_replays_current_geometry_but_never_old_physics_admission(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source,mock.patch.object(case.source_io,"prepare",side_effect=AssertionError("Old physics preparation forbidden")),\
                mock.patch.object(case.source_io,"verify_output",side_effect=AssertionError("Old physics admission forbidden")):
                before=case.source_inventory(source);output=root/"new-flow";packet=case.prepare(source,output)
                self.assertEqual(case.verify_output(output)["report"],packet)
                self.assertEqual(case.source_inventory(source),before)
                self.assertEqual(packet["boundaryFamilyId"],case.BC_FAMILY)
                self.assertEqual(packet["outletStaticAbsoluteReferencePa"],300000.)
                self.assertFalse(packet["inletTotalPressurePrescribed"])
                self.assertTrue(all(packet[k] is False for k in case.FALSE_FLAGS))
                self.assertEqual(len(packet["initialCaseMeshFiles"]),12)
                self.assertEqual(packet["sourceNativeGeometryQualityVerified"],True)
                self.assertNotIn("profile",packet["outletStaticGeometryEvidence"])
                self.assertFalse(packet["outletStaticGeometryEvidence"]["velocityProfilePrescribed"])
                self.assertFalse((output/"impeller-case/500").exists())
                self.assertFalse((output/"volute-case/500").exists())
                self.assertFalse(packet["baselineFieldsCopied"])
                self.assertEqual([row[0] for row in packet["nativeCommandArguments"]],["mergeMeshes","createNonConformalCouples","checkMesh","foamRun"])

    def test_report_mapping_source_family_and_retained_recipe_mutations_reject(self):
        for change in ("inletTotal","pressureRef","family","outletProfile","sourceSha","physicalRecipe","unknownClaim"):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                with self.fixture(root) as source:
                    output=root/"flow";case.prepare(source,output);packet=json.loads((output/case.REPORT).read_bytes())
                    if change=="inletTotal":packet["inletTotalPressurePrescribed"]=True
                    if change=="pressureRef":packet["absolutePressureMapping"]["referenceLocation"]="inlet"
                    if change=="family":packet["boundaryFamilyId"]="old-outlet-plug"
                    if change=="outletProfile":packet["outletStaticGeometryEvidence"]["velocityProfilePrescribed"]=True
                    if change=="sourceSha":packet["sourceGeometryIdentitySha256"]="a"*64
                    if change=="physicalRecipe":(output/"authored-physics/impeller-case/0/U").write_bytes(b"CHANGED")
                    if change=="unknownClaim":packet["inventedNumericalQualification"]=True
                    (output/case.REPORT).write_bytes(case.encoded(packet))
                    with self.assertRaises(Exception):case.verify_output(output)

    def test_native_constant_function_and_only_reviewed_sst_defaults_are_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                output=root/"flow";case.prepare(source,output)
                path=output/"impeller-case/0/U";raw=path.read_bytes()
                path.write_bytes(raw.replace(("volumetricFlowRate "+format(case.recipe.Q,".17g")+";").encode(),
                    ("volumetricFlowRate {type constant; value "+format(case.recipe.Q,".17g")+";}").encode()))
                for folder in ("impeller-case","volute-case"):
                    path=output/folder/"0/p";raw=path.read_bytes().replace(b"type fixedFluxPressure; value uniform 0;",b"type fixedFluxPressure; value uniform 0; gradient uniform 0;");path.write_bytes(raw)
                    path=output/folder/"0/omega";path.write_bytes(path.read_bytes().replace(b"type omegaWallFunction;",b"type omegaWallFunction; beta1 .075; blended false;"))
                    path=output/folder/"0/nut";path.write_bytes(path.read_bytes().replace(b"type nutkWallFunction;",b"type nutkWallFunction; Cmu .09; kappa .41; E 9.8;"))
                self.assertEqual(case.verify_output(output,after_native_ncc=True)["report"]["boundaryFamilyId"],case.BC_FAMILY)

    def test_foundation12_current_version_header_omission_preserves_exact_roles(self):
        # Actual pinned IOobject::writeHeader omits version at currentVersion;
        # synthetic byte replay here is compatibility, not native CFD evidence.
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                output=root/"flow";case.prepare(source,output)
                for component in ("impeller-case","volute-case"):
                    for field in ("U","p","k","omega","nut"):
                        path=output/component/"0"/field;raw=path.read_bytes()
                        self.assertEqual(raw.count(b"version 2.0;"),1)
                        path.write_bytes(raw.replace(b"version 2.0;",b""))
                before={p.relative_to(output).as_posix():sha256(p.read_bytes()).hexdigest()
                    for p in output.rglob("*") if p.is_file()}
                actual=case.verify_output(output,after_native_ncc=True)
                self.assertEqual(actual["report"]["boundaryFamilyId"],case.BC_FAMILY)
                self.assertTrue(all(actual["report"][k] is False for k in case.FALSE_FLAGS))
                self.assertEqual(before,{p.relative_to(output).as_posix():sha256(p.read_bytes()).hexdigest()
                    for p in output.rglob("*") if p.is_file()})

    def test_omitted_version_does_not_admit_unknown_duplicate_or_wrong_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                output=root/"flow";case.prepare(source,output)
                path=output/"impeller-case/0/U";original=path.read_bytes().replace(b"version 2.0;",b"")
                for before,after in ((b"format ascii;",b"format binary;"),
                    (b"format ascii;",b"format ascii; format ascii;"),
                    (b"format ascii;",b"format ascii; version 2.1;"),
                    (b"format ascii;",b"format ascii; version 2.0; version 2.0;"),
                    (b"format ascii;",b"format ascii; unknown yes;"),
                    (b"format ascii;",b'format ascii; arch "LSB;label=32;scalar=64";'),
                    (b"class volVectorField;",b"class volScalarField;"),
                    (b'location "0";',b'location "500";'),
                    (b"object U;",b"object p;"),
                    (b"object U;",b"object U; malformed")):
                    with self.subTest(after=after):
                        self.assertIn(before,original);path.write_bytes(original.replace(before,after))
                        with self.assertRaises(ValueError):case.verify_output(output,after_native_ncc=True)

    def test_native_inlet_q_weights_code_or_wrong_section_cannot_enter_new_family(self):
        for replacement in ("volumetricFlowRate .1;","volumetricFlowRate {type table; values ((0 .09));}",
                "volumetricFlowRate {type constant; value .090162292125826479 [m3/s];}",
                "volumetricFlowRate {type constant; value .090162292125826479; hidden yes;}",
                "volumetricFlowRate {type constant; type constant; value .090162292125826479;}",
                "volumetricFlowRate .090162292125826483; alpha alpha.water;", "volumetricFlowRate .090162292125826483; profile uniform;",
                "volumetricFlowRate .090162292125826483; rho none;", "volumetricFlowRate .090162292125826483; codedFixedValue yes;",
                "volumetricFlowRate .090162292125826483; volumetricFlowRate .1;"):
            with self.subTest(replacement=replacement),tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                with self.fixture(root) as source:
                    output=root/"flow";case.prepare(source,output);path=output/"impeller-case/0/U"
                    previous=("volumetricFlowRate "+format(case.recipe.Q,".17g")+";").encode()
                    path.write_bytes(path.read_bytes().replace(previous,replacement.encode()))
                    with self.assertRaises(Exception):case.verify_output(output,after_native_ncc=True)

    def test_initial_units_state_and_outlet_pressure_are_not_native_defaults(self):
        for target,before,after in (("0/U",b"dimensions [0 1 -1 0 0 0 0]",b"dimensions [0 0 0 0 0 0 0]"),
                ("0/U",b"internalField uniform (0 0 0)",b"internalField uniform (0 1 0)"),
                ("0/U",b'location "0";',b'location "500";'),
                ("0/U",b"class volVectorField;",b"class volVectorField; class volVectorField;"),
                ("0/p",b"type fixedValue; value uniform 0;",b"type fixedValue; value uniform 1;")):
            with self.subTest(target=target,before=before),tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                with self.fixture(root) as source:
                    output=root/"flow";case.prepare(source,output);path=output/"impeller-case"/target
                    self.assertIn(before,path.read_bytes());path.write_bytes(path.read_bytes().replace(before,after))
                    with self.assertRaises(Exception):case.verify_output(output,after_native_ncc=True)

    def test_source_mutation_during_copy_removes_only_owned_fresh_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                write=case.base.write_exclusive;mutated=False
                def race(path,raw):
                    nonlocal mutated
                    write(path,raw)
                    if not mutated and "source-teaching" in str(path):
                        mutated=True;(source/"logs/08-checkMesh.log").write_bytes(b"MUTATED")
                with mock.patch.object(case.base,"write_exclusive",side_effect=race):
                    with self.assertRaises(Exception):case.prepare(source,root/"flow")
                self.assertFalse((root/"flow").exists());self.assertTrue(source.exists())

    def test_source_native_failure_cannot_be_relabelled_by_new_bc_recipe(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                path=source/case.source_io.geometry_runner.REPORT;packet=json.loads(path.read_bytes());packet["nativeGeometryQualityVerified"]=False;path.write_bytes(case.encoded(packet))
                with self.assertRaises(Exception):case.prepare(source,root/"flow")
                self.assertFalse((root/"flow").exists())

    def test_output_scope_precedes_any_fresh_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                with self.assertRaises(Exception):case.prepare(source,source/"fresh-physics")
                with self.assertRaises(Exception):case.prepare(source,source)
                self.assertFalse((source/"fresh-physics").exists())

    def test_actual_annulus_area_winding_rings_and_byte_descriptors(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);annulus_source(root);before=case.source_inventory(root)
            proof=case.verify_inlet_geometry(root)
            self.assertEqual(proof["actualInletTriangleCount"],128)
            self.assertTrue(proof["twoOrientedBoundaryRingsVerified"])
            self.assertFalse(proof["nativeFlowExecuted"])
            self.assertFalse(proof["analyticAreaIsExecutedPatchArea"])
            expected=.5*64*math.sin(2*math.pi/64)*(.0844**2-.01968**2)
            self.assertAlmostEqual(proof["actualInletAreaM2"],expected,places=14)
            self.assertAlmostEqual(proof["engineeringStages"]["declaredNormalSpeed"]["result"]["value"],-case.recipe.Q/expected)
            self.assertEqual(case.source_inventory(root),before)
            self.assertEqual(len(proof["nativeMeshFileRecords"]),6)

    def test_analytic_area_deviation_is_a_diagnostic_not_a_new_quality_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);annulus_source(root,12);proof=case.verify_inlet_geometry(root)
            self.assertGreater(proof["annulusAreaRelativeError"],.005)
            self.assertFalse(proof["analyticAreaComparisonIsAcceptanceGate"])
            self.assertFalse(proof["nativeFlowExecuted"])

    def test_inlet_wrong_winding_plane_ring_and_adjacency_fail(self):
        for change in ("winding","plane","ring","owner","zone"):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);mesh,triangles=annulus_source(root)
                if change=="winding":
                    path=mesh/"faces";face="3("+" ".join(map(str,triangles[0]))+")";reverse="3("+" ".join(map(str,reversed(triangles[0])))+")";path.write_text(path.read_text().replace(face,reverse,1))
                if change=="plane":
                    path=mesh/"points";path.write_text(path.read_text().replace("-0.059999999999999998","-0.05",1))
                if change=="ring":
                    path=mesh/"points";before=format(.01968,".17g");self.assertIn(before,path.read_text());path.write_text(path.read_text().replace(before,"0.021",1))
                if change=="owner":
                    path=mesh/"owner";path.write_text(path.read_text().replace("\n0\n","\n999999\n",1))
                if change=="zone":
                    path=mesh/"cellZones";path.write_text(path.read_text().replace("rotating-fluid","stationary-fluid"))
                with self.assertRaises(Exception):case.verify_inlet_geometry(root)

    def test_unknown_initial_control_and_cache_roles_cannot_be_prefilled(self):
        for role in ("impeller-case/0/totalPressurePa","impeller-case/0/cellToRegion","impeller-case/0/unbound",
                "impeller-case/0/staticPressurePa","impeller-case/0/uniform/time",
                "impeller-case/system/newFunction","impeller-case/constant/fvConstraints","impeller-case/constant/fvMesh/other"):
            with self.subTest(role=role),tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                with self.fixture(root) as source:
                    output=root/"flow";case.prepare(source,output);path=output/role;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"PREFILLED OR UNKNOWN")
                    with self.assertRaises(Exception):case.generated_data(output)
                    if "unbound" in role or "newFunction" in role or "fvConstraints" in role or "fvMesh/other" in role:
                        with self.assertRaises(Exception):case.generated_data(output,after_native_execution=True)

    def test_startup_data_is_current_finite_known_and_uses_own_static_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                output=root/"flow";case.prepare(source,output)
                for name in case.MESH_FILES:shutil.copyfile(source/"impeller-case/constant/polyMesh"/name,output/"impeller-case/constant/polyMesh"/name)
                patches=case.geometry.boundary_entries(output/"impeller-case/constant/polyMesh/boundary")
                for name in case.STARTUP_FIELDS:
                    raw=startup_field("totalPressurePa" if name=="staticPressurePa" else name,patches,dimensions="" if name=="yPlus" else None)
                    if name=="staticPressurePa":raw=raw.replace(b"object totalPressurePa;",b"object staticPressurePa;")
                    (output/"impeller-case/0"/name).write_bytes(raw)
                rows,proof=case.generated_data(output,after_native_execution=True)
                self.assertEqual([r["path"] for r in rows],["impeller-case/0/U2","impeller-case/0/staticPressurePa","impeller-case/0/totalPressurePa","impeller-case/0/yPlus"])
                self.assertTrue(all(p["dataOnlyVerified"] and not p["authoredInput"] and not p["numericalAcceptanceInherited"] for p in proof))
                total=next(p for p in proof if p["resultField"]=="totalPressurePa")
                self.assertEqual(total["pressureReferenceLocation"],"outlet")
                self.assertEqual(total["allInternalCellsRepresented"],12)
                static=next(p for p in proof if p["resultField"]=="staticPressurePa")
                self.assertEqual(static["pressureBasis"],"gauge-Pa");self.assertEqual(static["referenceAdditionPa"],0)
                self.assertEqual(static["sourceInitialPressureFile"],case.record(output,"impeller-case/0/p")[1])
                control=output/"impeller-case/system/controlDict";control.write_bytes(control.read_bytes().replace(b"pRef 300000;",b"pRef 0;"))
                with self.assertRaisesRegex(case.InletFlowCaseIssue,"exact trusted"):case.generated_data(output,after_native_execution=True)

    def test_static_startup_shape_units_and_source_initial_state_cannot_be_relabelled(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                output=root/"flow";case.prepare(source,output)
                for name in case.MESH_FILES:shutil.copyfile(source/"impeller-case/constant/polyMesh"/name,output/"impeller-case/constant/polyMesh"/name)
                patches=case.geometry.boundary_entries(output/"impeller-case/constant/polyMesh/boundary")
                raw=startup_field("totalPressurePa",patches).replace(b"object totalPressurePa;",b"object staticPressurePa;")
                path=output/"impeller-case/0/staticPressurePa"
                for before,after in ((b"[1 -1 -2 0 0 0 0]",b"[0 2 -2 0 0 0 0]"),
                    (b"object staticPressurePa;",b"object totalPressurePa;"),
                    (b"internalField uniform 0;",b"internalField uniform 300000;"),
                    (b"internalField uniform 0;",b"internalField nonuniform List<scalar> 2(0 0);"),
                    (b"type calculated;",b"type codedFixedValue;")):
                    with self.subTest(after=after):
                        self.assertIn(before,raw);path.write_bytes(raw.replace(before,after))
                        with self.assertRaises(ValueError):case.generated_data(output,after_native_execution=True)
                path.write_bytes(raw)
                initial=output/"impeller-case/0/p";original=initial.read_bytes();initial.write_bytes(original.replace(b"internalField uniform 0;",b"internalField uniform 1;"))
                with self.assertRaisesRegex(case.InletFlowCaseIssue,"fresh zero"):case.generated_data(output,after_native_execution=True)

    def test_zero_time_pure_state_is_exact_typed_and_not_restart_or_physical_clock(self):
        def raw(dt=1):return (case.base.foam_header("time","dictionary","0/uniform")+f'beginTime 0; value 0; name "0"; index 0; deltaT {dt}; deltaT0 {dt};\n').encode()
        for dt in (1,.00011494252873563218):
            proof=case.native_zero_time(raw(dt),dt)
            self.assertEqual(proof["deltaT"],dt)
            for key in ("authoredInput","iterationIndexIsPhysicalTime","physicalTimeIntegrationVerified","restartVerified"):self.assertIs(proof[key],False)
        for before,after in ((b'location "0/uniform";',b'location "500/uniform";'),
            (b"class dictionary;",b"class volScalarField;"),(b"index 0;",b"index 0; index 0;"),
            (b"value 0;",b"value 1;"),(b"beginTime 0;",b"beginTime 1;"),(b'name "0";',b'name "0.0";'),
            (b"deltaT 1;",b"deltaT 2;"),(b"deltaT0 1;",b"deltaT0 2;"),(b"index 0;",b"index 0; hidden yes;"),
            (b"index 0;",b"index 0; #include secret;")):
            with self.subTest(after=after),self.assertRaises(ValueError):case.native_zero_time(raw().replace(before,after),1)
        with self.assertRaises(ValueError):case.native_zero_time(raw(),True)

    def test_initial_clock_is_postnative_only_current_control_log_and_exact_uniform_role(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                output=root/"flow";case.prepare(source,output);folder=output/"impeller-case/0/uniform";folder.mkdir()
                path=folder/"time";raw=(case.base.foam_header("time","dictionary","0/uniform")+'beginTime 0; value 0; name "0"; index 0; deltaT 1; deltaT0 1;\n').encode();path.write_bytes(raw)
                (output/"logs").mkdir();log=output/"logs/03-foamRun.log"
                log.write_bytes(b"Build : 12-86e126a7bc4d\nSelecting solver incompressibleFluid\nTime = 1s\nEnd\n")
                with self.assertRaises(ValueError):case.generated_data(output)
                rows,proofs=case.generated_data(output,after_native_execution=True)
                self.assertEqual(rows,[case.record(output,"impeller-case/0/uniform/time")[1]])
                self.assertEqual(proofs[0]["sourceSolverLog"],case.record(output,"logs/03-foamRun.log")[1])
                self.assertFalse(proofs[0]["numericalAcceptanceInherited"]);self.assertFalse(proofs[0]["restartVerified"])
                (folder/"unknown").write_bytes(b"unbound")
                with self.assertRaises(ValueError):case.generated_data(output,after_native_execution=True)
                (folder/"unknown").unlink();log.write_bytes(log.read_bytes().replace(b"incompressibleFluid",b"incompressibleVoF"))
                with self.assertRaises(ValueError):case.generated_data(output,after_native_execution=True)
                log.write_bytes(b"Build : 12-86e126a7bc4d\nSelecting solver incompressibleFluid\nTime = 1s\nEnd\n")
                control=output/"impeller-case/system/controlDict";control.write_bytes(control.read_bytes().replace(b"deltaT 1;",b"deltaT 2;"))
                with self.assertRaises(ValueError):case.generated_data(output,after_native_execution=True)

    def test_native_region_data_complete_connectivity_log_and_rawbytes_replayed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                output=root/"flow";case.prepare(source,output)
                for name in case.MESH_FILES:shutil.copyfile(source/"impeller-case/constant/polyMesh"/name,output/"impeller-case/constant/polyMesh"/name)
                region=output/"impeller-case/0/cellToRegion"
                region.write_bytes((case.base.foam_header("cellToRegion","labelList","0")+"12(0 0 0 0 0 0 1 1 1 1 1 1)\n").encode())
                (output/"logs").mkdir();log=output/"logs/02-checkMesh.log"
                log.write_bytes(b'cells: 12\n   *Number of regions: 2\n <<Writing region information to "0/cellToRegion"\n <<Writing region 0 (fully disconnected) with 6 cells to cellSet region0\n <<Writing region 1 (fully disconnected) with 6 cells to cellSet region1\nMesh OK.\nEnd\n')
                rows,proof=case.generated_data(output,after_native_execution=True)
                self.assertEqual(rows,[case.record(output,"impeller-case/0/cellToRegion")[1]])
                self.assertEqual(proof[0]["connectedRegionCount"],2)
                self.assertEqual(proof[0]["allNativeCellLabelsChecked"],12)
                region.write_bytes(region.read_bytes().replace(b"12(0 0",b"12(1 0"))
                with self.assertRaises(Exception):case.generated_data(output,after_native_execution=True)
                region.unlink()
                with self.assertRaisesRegex(case.InletFlowCaseIssue,"Retain the complete"):case.generated_data(output,after_native_execution=True)

    def test_native_static_cache_original_patch_ranges_are_current_data_only(self):
        import teaching_pump_phase_case as parser
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                output=root/"flow";case.prepare(source,output)
                for name in case.MESH_FILES:shutil.copyfile(source/"impeller-case/constant/polyMesh"/name,output/"impeller-case/constant/polyMesh"/name)
                patches=case.geometry.boundary_entries(output/"impeller-case/constant/polyMesh/boundary");blocks=[]
                for name,patch in patches.items():
                    if name not in case.NCC_PATCHES:blocks.append(name+" {type polyFaces;}")
                    else:
                        kind=parser.NCC_PATCHES[name];first=patches[name.split("_on_",1)[1]]["startFace"]
                        size,value=(1,str(first)) if kind=="nonConformalCyclic" else (0,"")
                        blocks.append(name+" {type nonConformalPolyFaces; patchType "+kind+"; size "+str(size)+"; value nonuniform List<label> "+str(size)+"("+value+");}")
                path=output/"impeller-case/constant/fvMesh/polyFaces";path.parent.mkdir()
                raw=(case.base.foam_header("polyFaces","surfaceLabelField","constant/fvMesh")+"dimensions [];\ninternalField uniform -1;\nboundaryField {"+"\n".join(blocks)+"}\n").encode();path.write_bytes(raw)
                rows,proof=case.generated_data(output,after_native_execution=True)
                self.assertEqual(rows,[case.record(output,"impeller-case/constant/fvMesh/polyFaces")[1]])
                self.assertTrue(proof[0]["dataOnlyVerified"]);self.assertFalse(proof[0]["authoredInput"])
                path.write_bytes(raw.replace(b'location "constant/fvMesh";',b'location "0/fvMesh";'))
                with self.assertRaises(Exception):case.generated_data(output,after_native_execution=True)

    def test_inlet_source_race_rejects_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mesh,_=annulus_source(root);original=case.record;calls=0
            def race(directory,relative,**kwargs):
                nonlocal calls
                result=original(directory,relative,**kwargs)
                if relative.endswith("cellZones"):
                    calls+=1
                    if calls==1:(mesh/"points").write_bytes((mesh/"points").read_bytes()+b" ")
                return result
            with mock.patch.object(case,"record",side_effect=race),self.assertRaisesRegex(case.InletFlowCaseIssue,"Source bytes changed"):
                case.verify_inlet_geometry(root)


if __name__=="__main__":unittest.main()
