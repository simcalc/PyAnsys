"""Current-source and native-format contracts for fresh research inputs only."""
from contextlib import contextmanager
from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import teaching_pump_lower_flow_case as case
import teaching_pump_inlet_flow_case as old_case
import test_teaching_pump_inlet_flow_case as fixtures
from test_teaching_pump_phase_case import startup_field
synthetic_inlet_proof,annulus_source=fixtures.synthetic_inlet_proof,fixtures.annulus_source


class HighPressureCaseTests(unittest.TestCase):
    @contextmanager
    def fixture(self,root):
        # Source native topology/logs are replayed. Only producer CAD/current
        # installed binaries and the tiny cube's absent annular inlet are mocked,
        # as explicitly in the geometry-source fixture; never physics results.
        with patch.object(__import__("test_teaching_pump_baseline_case").case,"PARAMETERS",case.recipe.PARAMETERS), \
            fixtures.TeachingInletFlowCaseTests().fixture(root) as source,patch.object(case,"verify_inlet_geometry",side_effect=synthetic_inlet_proof), \
            patch.object(case.source_io,"validate_runtime"),patch.object(case.source_io.outlet,"verify_outlet",side_effect=__import__("test_teaching_pump_baseline_case").synthetic_outlet_proof):
            yield source

    def test_fresh_current_geometry_no_old_physics_constructor_or_results(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source,patch.object(old_case,"prepare",side_effect=AssertionError("Old physics constructor forbidden")),\
                patch.object(old_case,"verify_output",side_effect=AssertionError("Old admission forbidden")):
                before=case.source_inventory(source);output=root/"research";packet=case.prepare(source,output)
                self.assertEqual(packet,case.verify_output(output)["report"])
                self.assertEqual(packet["researchProfile"],case.recipe.PROFILE)
                self.assertEqual(packet["boundaryFamilyId"],case.recipe.PROFILE["id"])
                self.assertEqual(packet["outletStaticAbsoluteReferencePa"],1000000.)
                self.assertEqual(packet["absolutePressureMapping"]["equation"],"p_absolute_Pa=998.2*p_kinematic_m2s2+1000000")
                self.assertFalse(packet["inletTotalPressurePrescribed"])
                self.assertEqual(len(packet["initialCaseMeshFiles"]),12)
                self.assertTrue(all(packet[k] is False for k in case.FALSE_FLAGS))
                self.assertFalse((output/"impeller-case/500").exists());self.assertFalse((output/"impeller-case/2000").exists())
                self.assertEqual(case.source_inventory(source),before)
                self.assertEqual(packet["limits"],old_case.LIMITS)

    def test_profile_reference_source_and_immutable_recipe_mutations_reject(self):
        for kind in ("profile","typed","pressure","family","mapping","sourceSha","recipe","extra"):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                with self.fixture(root) as source:
                    output=root/"research";case.prepare(source,output);packet=json.loads((output/case.REPORT).read_bytes())
                    if kind=="profile":packet["researchProfile"]["iterations"]=500
                    if kind=="typed":packet["researchProfile"]["iterations"]=2000.
                    if kind=="pressure":packet["outletStaticAbsoluteReferencePa"]=300000.
                    if kind=="family":packet["boundaryFamilyId"]=old_case.BC_FAMILY
                    if kind=="mapping":packet["absolutePressureMapping"]["referenceAdditionCount"]=2
                    if kind=="sourceSha":packet["sourceGeometryIdentitySha256"]="a"*64
                    if kind=="recipe":(output/"authored-physics/impeller-case/system/controlDict").write_bytes(b"CHANGED")
                    if kind=="extra":packet["numericalBaselineQualified"]=True
                    (output/case.REPORT).write_bytes(case.encoded(packet))
                    with self.assertRaises(ValueError):case.verify_output(output)

    def test_nested_native_inlet_writer_and_header_defaults_preserve_si_physics(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                output=root/"research";case.prepare(source,output);path=output/"impeller-case/0/U"
                q=format(case.recipe.Q,".17g");raw=path.read_bytes()
                path.write_bytes(raw.replace(("volumetricFlowRate "+q+";").encode(),("volumetricFlowRate {type constant; value "+q+";}").encode()))
                for component in ("impeller-case","volute-case"):
                    for name in ("p","U","k","omega","nut"):
                        path=output/component/"0"/name;path.write_bytes(path.read_bytes().replace(b"version 2.0;",b""))
                self.assertEqual(case.verify_output(output,after_native_ncc=True)["report"]["researchProfile"],case.recipe.PROFILE)
                path=output/"impeller-case/0/U";raw=path.read_bytes();path.write_bytes(raw.replace(b"type constant;",b"type table;"))
                with self.assertRaises(ValueError):case.verify_output(output,after_native_ncc=True)

    def test_preexecuted_unknown_model_data_or_stale_clock_cannot_enter(self):
        for role in ("impeller-case/0/staticPressurePa","impeller-case/0/uniform/time","impeller-case/0/unknown",
            "impeller-case/system/unknown","impeller-case/constant/fvConstraints","impeller-case/constant/fvMesh/other"):
            with self.subTest(role=role),tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                with self.fixture(root) as source:
                    output=root/"research";case.prepare(source,output);path=output/role;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"UNBOUND")
                    with self.assertRaises(ValueError):case.generated_data(output)
                    with self.assertRaises(ValueError):case.generated_data(output,after_native_execution=True)

    def test_generated_static_is_gauge_pa_and_total_uses_own_million_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                output=root/"research";case.prepare(source,output)
                for name in case.MESH_FILES:shutil.copyfile(source/"impeller-case/constant/polyMesh"/name,output/"impeller-case/constant/polyMesh"/name)
                patches=case.geometry.boundary_entries(output/"impeller-case/constant/polyMesh/boundary")
                for name in case.STARTUP_FIELDS:
                    raw=startup_field("totalPressurePa" if name=="staticPressurePa" else name,patches,dimensions="" if name=="yPlus" else None)
                    if name=="staticPressurePa":raw=raw.replace(b"object totalPressurePa;",b"object staticPressurePa;")
                    (output/"impeller-case/0"/name).write_bytes(raw)
                records,proofs=case.generated_data(output,after_native_execution=True)
                self.assertEqual(len(records),4)
                static=next(p for p in proofs if p["resultField"]=="staticPressurePa")
                self.assertEqual(static["pressureBasis"],"gauge-Pa");self.assertEqual(static["referenceAdditionPa"],0)
                self.assertTrue(all(p["authoredInput"] is False and p["numericalAcceptanceInherited"] is False for p in proofs))
                control=output/"impeller-case/system/controlDict";before=control.read_bytes();control.write_bytes(before.replace(b"pRef 1000000;",b"pRef 300000;"))
                with self.assertRaisesRegex(ValueError,"exact trusted"):case.generated_data(output,after_native_execution=True)

    def test_generated_static_shape_si_count_initial_source_and_current_hashes_reject(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                output=root/"research";case.prepare(source,output)
                for name in case.MESH_FILES:shutil.copyfile(source/"impeller-case/constant/polyMesh"/name,output/"impeller-case/constant/polyMesh"/name)
                patches=case.geometry.boundary_entries(output/"impeller-case/constant/polyMesh/boundary")
                raw=startup_field("totalPressurePa",patches).replace(b"object totalPressurePa;",b"object staticPressurePa;")
                path=output/"impeller-case/0/staticPressurePa"
                for before,after in ((b"[1 -1 -2 0 0 0 0]",b"[0 2 -2 0 0 0 0]"),
                    (b"internalField uniform 0;",b"internalField uniform 1000000;"),
                    (b"internalField uniform 0;",b"internalField nonuniform List<scalar> 2(0 0);"),
                    (b"type calculated;",b"type codedFixedValue;")):
                    with self.subTest(after=after):
                        path.write_bytes(raw.replace(before,after))
                        with self.assertRaises(ValueError):case.generated_data(output,after_native_execution=True)
                path.write_bytes(raw);original=case.record;count=0
                def late(directory,relative,**kwargs):
                    nonlocal count
                    result=original(directory,relative,**kwargs)
                    if relative.endswith("staticPressurePa"):
                        count+=1
                        if count==1:path.write_bytes(raw+b" ")
                    return result
                with patch.object(case,"record",side_effect=late),self.assertRaisesRegex(ValueError,"changed during"):case.generated_data(output,after_native_execution=True)

    def test_zero_clock_remains_seed_zero_dt_one_not_2000_physical_seconds(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                output=root/"research";case.prepare(source,output);folder=output/"impeller-case/0/uniform";folder.mkdir()
                raw=(case.base.foam_header("time","dictionary","0/uniform")+'beginTime 0; value 0; name "0"; index 0; deltaT 1; deltaT0 1;\n').encode()
                (folder/"time").write_bytes(raw);(output/"logs").mkdir();log=output/"logs/03-foamRun.log"
                log.write_bytes(b"Build : 12-86e126a7bc4d\nSelecting solver incompressibleFluid\nTime = 2000s\nEnd\n")
                with self.assertRaises(ValueError):case.generated_data(output)
                rows,proof=case.generated_data(output,after_native_execution=True)
                self.assertEqual(rows,[case.record(output,"impeller-case/0/uniform/time")[1]])
                self.assertEqual(proof[0]["deltaT"],1)
                self.assertFalse(proof[0]["iterationIndexIsPhysicalTime"]);self.assertFalse(proof[0]["restartVerified"])
                (folder/"time").write_bytes(raw.replace(b"deltaT0 1;",b"deltaT0 2;"))
                with self.assertRaises(ValueError):case.generated_data(output,after_native_execution=True)

    def test_current_region_and_ncc_data_can_never_supply_scientific_controls(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                output=root/"research";case.prepare(source,output)
                for name in case.MESH_FILES:shutil.copyfile(source/"impeller-case/constant/polyMesh"/name,output/"impeller-case/constant/polyMesh"/name)
                region=output/"impeller-case/0/cellToRegion"
                region.write_bytes((case.base.foam_header("cellToRegion","labelList","0")+"12(0 0 0 0 0 0 1 1 1 1 1 1)\n").encode())
                (output/"logs").mkdir();log=output/"logs/02-checkMesh.log"
                log.write_bytes(b'cells: 12\n *Number of regions: 2\n <<Writing region information to "0/cellToRegion"\n <<Writing region 0 (fully disconnected) with 6 cells to cellSet region0\n <<Writing region 1 (fully disconnected) with 6 cells to cellSet region1\nMesh OK.\nEnd\n')
                rows,proof=case.generated_data(output,after_native_execution=True)
                self.assertEqual(rows,[case.record(output,"impeller-case/0/cellToRegion")[1]])
                self.assertEqual(proof[0]["connectedRegionCount"],2)
                self.assertEqual(proof[0]["allNativeCellLabelsChecked"],12)
                region.write_bytes(region.read_bytes().replace(b"12(0 0",b"12(1 0"))
                with self.assertRaises(ValueError):case.generated_data(output,after_native_execution=True)

    def test_real_annular_source_geometry_replay_retains_new_family_not_analytic_guess(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);annulus_source(root);proof=case.verify_inlet_geometry(root)
            self.assertEqual(proof["boundaryFamilyId"],case.BC_FAMILY)
            self.assertEqual(proof["actualInletTriangleCount"],128)
            self.assertFalse(proof["nativeFlowExecuted"]);self.assertFalse(proof["analyticAreaComparisonIsAcceptanceGate"])
            self.assertEqual(len(proof["nativeMeshFileRecords"]),6)

    def test_source_failure_race_and_scope_never_receive_fresh_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.fixture(root) as source:
                with self.assertRaises(ValueError):case.prepare(source,source/"nested")
                self.assertFalse((source/"nested").exists())
                original=case.base.write_exclusive;mutated=False
                def race(path,raw):
                    nonlocal mutated
                    original(path,raw)
                    if not mutated and "source-teaching" in str(path):
                        mutated=True;(source/"logs/08-checkMesh.log").write_bytes(b"MUTATED")
                with patch.object(case.base,"write_exclusive",side_effect=race),self.assertRaises(ValueError):case.prepare(source,root/"research")
                self.assertFalse((root/"research").exists());self.assertTrue(source.exists())


if __name__=="__main__":unittest.main()
