"""Full recipe-delta tests; no solver or convergence evidence is produced."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import teaching_pump_high_pressure_recipe as recipe


class HighPressureRecipeTests(unittest.TestCase):
    def test_only_three_control_entries_change_and_v1_full_inventories_stay_pinned(self):
        for component in ("impeller","volute"):
            for placeholder in (False,True):
                with self.subTest(component=component,placeholder=placeholder):
                    before=recipe.previous.case_files(component,default_faces=placeholder)
                    self.assertEqual(sha256(recipe.previous.upstream.encoded(recipe.inventory(before))).hexdigest(),
                        recipe.PREVIOUS_RECIPE_SHA256[component,placeholder])
                    current=recipe.case_files(component,default_faces=placeholder)
                    self.assertEqual(set(current),set(before))
                    self.assertEqual({k for k in before if before[k]!=current[k]},{"system/controlDict"})
                    expected=before["system/controlDict"].replace(b"endTime 500;",b"endTime 2000;").replace(
                        b"writeInterval 500;",b"writeInterval 2000;").replace(b"pRef 300000;",b"pRef 1000000;")
                    self.assertEqual(current["system/controlDict"],expected)
                    self.assertEqual(recipe.previous.case_files(component,default_faces=placeholder),before)
                    self.assertEqual(recipe.verify_files(current,component,default_faces=placeholder),recipe.inventory(current))

    def test_physics_gauge_initial_state_native_flow_and_resource_limits_are_unchanged(self):
        for component in ("impeller","volute"):
            files=recipe.case_files(component,default_faces=False)
            self.assertIn(b"internalField uniform 0;",files["0/p"])
            _,p=recipe.previous.upstream.fields.boundary_blocks(files["0/p"],"p")
            _,u=recipe.previous.upstream.fields.boundary_blocks(files["0/U"],"U")
            self.assertEqual(p[recipe.OUTLET],{"type":"fixedValue","value":"uniform 0"})
            self.assertEqual(u[recipe.OUTLET]["type"],"pressureInletOutletVelocity")
            if component=="impeller":
                self.assertEqual(p["inlet"]["type"],"fixedFluxPressure")
                self.assertEqual(float(u["inlet"]["volumetricFlowRate"]),90/998.2)
            self.assertIn(b"deltaT 1;",files["system/controlDict"])
            self.assertIn(b"purgeWrite 1;",files["system/controlDict"])
            self.assertNotIn(b"pRef",files["0/p"])
        self.assertEqual(recipe.PARAMETERS,recipe.previous.PARAMETERS)
        self.assertEqual(recipe.LIMITS,recipe.previous.LIMITS)
        self.assertEqual(recipe.ASSUMPTIONS["numericalControlsProfileId"],"teaching-upwind-consistent-coupling-v3")
        self.assertFalse(recipe.ASSUMPTIONS["pressureReferenceIsConvergenceCorrection"])
        self.assertEqual(recipe.SAMPLING["samplingIterationWindow"],[1981,2000])

    def test_typed_profile_cannot_be_arbitrary_boolean_partial_or_nonfinite(self):
        self.assertEqual(recipe.verify_profile(recipe.PROFILE),recipe.PROFILE)
        for mutate in (lambda p:p.update(iterations=True),lambda p:p.update(iterations=2000.),
            lambda p:p.update(outletStaticAbsoluteReferencePa=1000000),lambda p:p.update(outletStaticAbsoluteReferencePa=float("nan")),
            lambda p:p.update(outletStaticAbsoluteReferencePa=300000.),lambda p:p.update(samplingWindowIterations=19),
            lambda p:p.update(id=recipe.previous.BC_FAMILY),lambda p:p.pop("id"),lambda p:p.update(hidden=True)):
            current=deepcopy(recipe.PROFILE);mutate(current)
            with self.assertRaises(ValueError):recipe.verify_profile(current)
        copied=recipe.verify_profile(recipe.PROFILE);copied["iterations"]=1
        self.assertEqual(recipe.PROFILE["iterations"],2000)

    def test_changed_prior_scheme_model_bcs_or_duplicate_control_reject_before_rewrite(self):
        original=recipe.previous.case_files
        for path,before,after in (("system/fvSchemes",b"bounded Gauss upwind",b"Gauss linear"),
            ("constant/physicalProperties",b"nu",b"changedNu"),("0/p",b"uniform 0",b"uniform 1"),
            ("system/controlDict",b"endTime 500;",b"endTime 500; endTime 500;")):
            def changed(*args,**kwargs):
                rows=original(*args,**kwargs);self.assertIn(before,rows[path]);rows[path]=rows[path].replace(before,after,1);return rows
            with self.subTest(path=path),patch.object(recipe.previous,"case_files",side_effect=changed):
                with self.assertRaisesRegex(ValueError,"every byte"):recipe.case_files("impeller")

    def test_complete_recipe_verification_rejects_rehashed_unknown_or_modified_field(self):
        files=recipe.case_files("impeller",default_faces=False)
        for change in (lambda f:f.update({"constant/fvConstraints":b"extra"}),lambda f:f.pop("0/nut"),
            lambda f:f.update({"0/U":f["0/U"]+b" "}),lambda f:f.update({"system/controlDict":f["system/controlDict"].replace(b"1000000",b"300000")})):
            current=dict(files);change(current)
            with self.assertRaises(ValueError):recipe.verify_files(current,"impeller",default_faces=False)

    def test_plan_is_fresh_declared_inputs_never_requalified_historical_results(self):
        packet=recipe.teaching_plan()
        self.assertEqual(packet["profile"],recipe.PROFILE)
        self.assertEqual(packet["boundaryFamilyId"],recipe.PROFILE["id"])
        self.assertEqual(packet["engineeringStages"]["outletAbsoluteReference"]["result"]["value"],1000000.)
        self.assertTrue(all(packet[k] is False for k in recipe.FALSE_FLAGS))
        self.assertFalse(packet["assumptions"]["convergenceImprovementPredicted"])
        packet["parameters"]["speedRpm"]=1
        self.assertEqual(recipe.PARAMETERS["speedRpm"],1450.)


if __name__=="__main__":unittest.main()
