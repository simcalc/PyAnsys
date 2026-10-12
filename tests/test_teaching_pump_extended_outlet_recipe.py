"""Distinct geometry family and unchanged physics/acceptance; no CFD executes."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import teaching_pump_extended_outlet_recipe as recipe
import teaching_pump_extended_outlet_geometry as geometry
import reduce_teaching_pump_extended_outlet as reducer
import run_teaching_pump_extended_outlet as runner
import teaching_pump_extended_outlet_parameters as writer

class ExtendedOutletRecipeTests(unittest.TestCase):
    def test_only_geometry_length_changes_and_physics_inventories_remain_exact(self):
        params=recipe.verify_geometry_parameters(recipe.EXPECTED_GEOMETRY_PARAMETERS)
        self.assertEqual({k for k in params if params[k]!=recipe.author.DEFAULTS[k]},{'outletLengthM'})
        self.assertEqual(params['outletLengthM'],.30)
        old=recipe.author.teaching_plan(None,'lean-coarse');new=recipe.author.teaching_plan(params,'lean-coarse')
        self.assertNotEqual(old['geometryIdentitySha256'],new['geometryIdentitySha256'])
        for component in ('impeller','volute'):
            for placeholder in (False,True):
                current=recipe.case_files(component,default_faces=placeholder)
                before=recipe.previous.case_files(component,default_faces=placeholder)
                self.assertEqual(current,before)
                self.assertEqual(sha256(recipe.encoded(recipe.inventory(current))).hexdigest(),
                    recipe.PRIOR_INVENTORY[component+':'+str(placeholder).lower()])
                self.assertEqual(recipe.verify_files(current,component,default_faces=placeholder),recipe.inventory(current))

    def test_whole_parameter_family_rejects_rehashed_old_unknown_or_typed_mismatch(self):
        mutations=[lambda p:p.update(outletLengthM=.15),lambda p:p.update(outletLengthM=.30001),
            lambda p:p.update(outletLengthM=True),lambda p:p.update(outletLengthM=float('nan')),
            lambda p:p.update(massFlowKgS=45.),lambda p:p.update(speedRpm=1450),lambda p:p.update(outletOverlapM=.01),
            lambda p:p.update(bladeSweepRad=-.6),lambda p:p.update(bladeCount=6.),lambda p:p.update(extra=0),
            lambda p:p.pop('eyeRadiusM')]
        for mutate in mutations:
            values=deepcopy(recipe.EXPECTED_GEOMETRY_PARAMETERS);mutate(values)
            with self.assertRaises(ValueError):recipe.verify_geometry_parameters(values)
        self.assertEqual(geometry.PLANE_Y,.30)
        self.assertEqual(recipe.author.DEFAULTS['outletLengthM'],.15)

    def test_own_profile_and_measurement_cannot_alias_old_admission(self):
        for mutate in (lambda p:p.update(id=recipe.previous.BC_FAMILY),lambda p:p.update(outletLengthM=.15),
            lambda p:p.update(iterations=2000.),lambda p:p.update(headMeasurement='pump-only-head'),lambda p:p.pop('outletLengthM')):
            p=deepcopy(recipe.PROFILE);mutate(p)
            with self.assertRaises(ValueError):recipe.verify_profile(p)
        self.assertEqual(reducer.BC_FAMILY,recipe.BC_FAMILY)
        self.assertEqual(runner.recipe.PROFILE,recipe.PROFILE)
        self.assertEqual(runner.LIMITS,runner.low.LIMITS)
        self.assertEqual(recipe.LIMITS,recipe.previous.LIMITS)
        controls={'purpose':'SYNTHETIC-CONTRACT-ONLY'}
        study=reducer.study_packet(recipe.EXPECTED_GEOMETRY_PARAMETERS,controls)
        self.assertFalse(study['physicalBasis']['pumpOnlyHead'])
        self.assertFalse(study['physicalBasis']['addedDuctLossCorrectionApplied'])
        self.assertFalse(study['physicalBasis']['sameSectionComparisonWithPrevious'])
        for key,value in {'residualLimit':1e-4,'massFlowRelativeLimit':.005,'headWindowRelativeLimit':.005,'reverseFlowFractionLimit':.005}.items():
            self.assertEqual(study['acceptance'][key],value)

    def test_changed_upstream_fields_or_schemes_fail_before_trial_recipe(self):
        original=recipe.previous.case_files
        for path in ('0/U','constant/MRFProperties','system/fvSolution','system/fvSchemes'):
            def changed(*args,**kwargs):
                result=original(*args,**kwargs);result[path]+=b'\n';return result
            with patch.object(recipe.previous,'case_files',side_effect=changed),self.assertRaises(ValueError):recipe.case_files('impeller')

    def test_input_stages_do_not_invent_flow_results_or_predict_improvement(self):
        plan=recipe.teaching_plan()
        self.assertEqual(plan['geometryParameters']['outletLengthM'],.30)
        self.assertTrue(all(plan[k] is False for k in recipe.FALSE_FLAGS))
        self.assertFalse(plan['assumptions']['outletDomainTrial']['improvementPredicted'])
        self.assertFalse(plan['sampling']['pumpOnlyHead'])
        self.assertAlmostEqual(plan['engineeringStages']['hydraulicDiameter']['result']['value'],2*.11*.04363/(.11+.04363))
        before=deepcopy(recipe.previous.PROFILE);recipe.teaching_plan();self.assertEqual(before,recipe.previous.PROFILE)

    def test_parameter_writer_rejects_existing_or_outside_runner_file(self):
        import os
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'RUNNER_TEMP':temp}),patch.object(writer.geometry,'context_guard'):
            target=Path(temp)/'parameters.json';value=writer.write(target)
            self.assertEqual(target.read_bytes(),recipe.encoded(value))
            with self.assertRaises(ValueError):writer.write(target)
            nested=Path(temp)/'other';nested.mkdir()
            with self.assertRaises(ValueError):writer.write(nested/'parameters.json')

if __name__=='__main__':unittest.main()
