"""Exact new operating point and sole inlet BC delta; no native CFD executes."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import teaching_pump_lower_flow_recipe as recipe
import teaching_pump_lower_flow_case as case
import reduce_teaching_pump_lower_flow as reducer
import run_teaching_pump_lower_flow as runner

class LowerFlowRecipeTests(unittest.TestCase):
    def test_exactly_one_field_and_only_inlet_flow_value_change(self):
        for component in ('impeller','volute'):
            for placeholder in (False,True):
                before=recipe.previous.case_files(component,default_faces=placeholder)
                after=recipe.case_files(component,default_faces=placeholder)
                self.assertEqual(sha256(recipe.encoded(recipe.inventory(before))).hexdigest(),recipe.PRIOR_INVENTORY[component+':'+str(placeholder).lower()])
                self.assertEqual(set(before),set(after))
                self.assertEqual({k for k in before if before[k]!=after[k]}, {'0/U'} if component=='impeller' else set())
                if component=='impeller':
                    expected=before['0/U'].replace(format(recipe.previous.Q,'.17g').encode(),format(recipe.Q,'.17g').encode(),1)
                    self.assertEqual(after['0/U'],expected)
                    a,rows=case.fields.boundary_blocks(after['0/U'],'U');b,old=case.fields.boundary_blocks(before['0/U'],'U')
                    self.assertEqual(a,b);self.assertEqual({k:v for k,v in rows.items() if k!='inlet'},{k:v for k,v in old.items() if k!='inlet'})
                self.assertEqual(recipe.verify_files(after,component,default_faces=placeholder),recipe.inventory(after))

    def test_complete_parameters_cannot_inherit_old_flow_geometry_or_wrong_types(self):
        self.assertEqual({k for k,v in recipe.EXPECTED_GEOMETRY_PARAMETERS.items() if v!=recipe.previous.EXPECTED_GEOMETRY_PARAMETERS[k]},{'massFlowKgS'})
        for mutate in (lambda p:p.update(massFlowKgS=90.),lambda p:p.update(massFlowKgS=45),lambda p:p.update(massFlowKgS=True),
            lambda p:p.update(massFlowKgS=float('nan')),lambda p:p.update(outletLengthM=.15),lambda p:p.update(bladeCount=8),lambda p:p.pop('densityKgM3')):
            values=deepcopy(recipe.EXPECTED_GEOMETRY_PARAMETERS);mutate(values)
            with self.assertRaises(ValueError):recipe.verify_geometry_parameters(values)
        self.assertEqual(recipe.previous.MASS_FLOW,90.);self.assertEqual(recipe.author.DEFAULTS['massFlowKgS'],90.)
        self.assertEqual(case.source_io.PARAMETERS['massFlowKgS'],45.)

    def test_flow_equations_are_declared_inputs_never_a_simulated_head_or_bep(self):
        plan=recipe.teaching_plan();stages=plan['engineeringStages']
        self.assertAlmostEqual(stages['volumeFlow']['result']['value'],.04508114606291324)
        self.assertAlmostEqual(stages['volumeFlow']['result']['value']*3600,162.29212582648766)
        self.assertAlmostEqual(stages['nominalOutletSpeed']['result']['value'],9.393275282418944)
        self.assertTrue(all(plan[k] is False for k in recipe.FALSE_FLAGS))
        self.assertFalse(plan['assumptions']['lowerFlowTrial']['improvementPredicted'])
        self.assertFalse(plan['sampling']['sameOperatingPointAsPrevious'])
        self.assertNotIn('head',stages)

    def test_changed_prior_physics_is_rejected_even_with_known_geometry(self):
        original=recipe.previous.case_files
        for path in ('0/U','0/p','system/fvSchemes','constant/MRFProperties'):
            def changed(*args,**kwargs):
                result=original(*args,**kwargs);result[path]+=b' ';return result
            with patch.object(recipe.previous,'case_files',side_effect=changed),self.assertRaises(ValueError):recipe.case_files('impeller')

    def test_native_caps_acceptance_turbulence_and_assembly_scope_are_kept(self):
        self.assertEqual(runner.LIMITS,runner.low.LIMITS);self.assertEqual(recipe.LIMITS,recipe.previous.LIMITS)
        study=reducer.study_packet(recipe.EXPECTED_GEOMETRY_PARAMETERS,{'fixtureKind':'SYNTHETIC-NOT-NATIVE-EVIDENCE'})
        self.assertEqual(study['input']['massFlowKgS'],45.)
        self.assertEqual(study['acceptance']['residualLimit'],.0001)
        self.assertEqual(study['acceptance']['reverseFlowFractionLimit'],.005)
        self.assertEqual(study['acceptance']['headWindowRelativeLimit'],.005)
        self.assertFalse(study['physicalBasis']['pumpOnlyHead'])
        self.assertEqual(recipe.ASSUMPTIONS['inletTurbulence'],recipe.previous.ASSUMPTIONS['inletTurbulence'])
        with self.assertRaises(ValueError):reducer.study_packet(recipe.previous.EXPECTED_GEOMETRY_PARAMETERS,{})

    def test_typed_profile_contains_own_flow_and_cannot_alias_previous(self):
        for mutate in (lambda p:p.update(id=recipe.previous.BC_FAMILY),lambda p:p.update(massFlowKgS=90.),
            lambda p:p.pop('massFlowKgS'),lambda p:p.update(outletStaticAbsoluteReferencePa=300000.)):
            p=deepcopy(recipe.PROFILE);mutate(p)
            with self.assertRaises(ValueError):recipe.verify_profile(p)
        self.assertEqual(recipe.verify_profile(recipe.PROFILE),recipe.PROFILE)
        self.assertEqual(reducer.BC_FAMILY,recipe.BC_FAMILY)

if __name__=='__main__':unittest.main()
