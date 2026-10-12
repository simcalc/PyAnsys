"""Pure byte/BC/input mathematics tests; no mesh, solver or CFD is executed."""
from copy import deepcopy
from hashlib import sha256
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import teaching_pump_inlet_flow_recipe as recipe


class InletFlowRecipeTests(unittest.TestCase):
    def test_exact_four_section_deltas_and_auxiliary_roles_preserve_every_other_byte(self):
        for component in ("impeller","volute"):
            for default_faces in (False,True):
                with self.subTest(component=component,default_faces=default_faces):
                    before=recipe.upstream.case_files(component,default_faces=default_faces)
                    after=recipe.case_files(component,default_faces=default_faces)
                    self.assertEqual(set(before),set(after))
                    self.assertEqual({p for p in before if before[p]!=after[p]},{"0/U","0/p"})
                    changes=set()
                    for field in ("U","p"):
                        prefix,old=recipe.upstream.fields.boundary_blocks(before["0/"+field],field)
                        current_prefix,new=recipe.upstream.fields.boundary_blocks(after["0/"+field],field)
                        self.assertEqual(prefix,current_prefix);self.assertEqual(set(old),set(new))
                        for name in old:
                            if old[name]!=new[name]:changes.add((field,name))
                        self.assertEqual(new[recipe.OUTLET],{"type":"pressureInletOutletVelocity","value":"uniform (0 0 0)"}
                            if field=="U" else {"type":"fixedValue","value":"uniform 0"})
                        self.assertEqual("defaultFaces" in new,default_faces)
                    expected={("U",recipe.OUTLET),("p",recipe.OUTLET)}
                    if component=="impeller":
                        expected|={("U",recipe.INLET),("p",recipe.INLET)}
                        _,u=recipe.upstream.fields.boundary_blocks(after["0/U"],"U")
                        _,p=recipe.upstream.fields.boundary_blocks(after["0/p"],"p")
                        self.assertEqual(u[recipe.INLET],{"type":"flowRateInletVelocity","volumetricFlowRate":format(90/998.2,".17g"),"value":"uniform (0 0 0)"})
                        self.assertEqual(p[recipe.INLET],{"type":"fixedFluxPressure","value":"uniform 0"})
                    else:
                        _,u=recipe.upstream.fields.boundary_blocks(after["0/U"],"U");self.assertNotIn(recipe.INLET,u)
                    self.assertEqual(changes,expected)
                    self.assertEqual(recipe.upstream.case_files(component,default_faces=default_faces),before)
                    self.assertEqual(sha256(recipe.upstream.encoded(recipe.inventory(before))).hexdigest(),recipe.UPSTREAM_RECIPE_SHA256[(component,default_faces)])

    def test_changed_unknown_duplicate_or_missing_upstream_is_rejected_before_rewrite(self):
        original=recipe.upstream.case_files
        def unknown(files):files["constant/unknownDict"]=b"unknown;"
        def duplicate(files):files["0/U"]+=b'"mass-flow-inlet-11" {type fixedValue;value uniform (0 0 0);}'
        def changed_outlet(files):files["0/U"]=files["0/U"].replace(b"18.786550564837889",b"19")
        def changed_wall(files):files["0/U"]=files["0/U"].replace(b"type noSlip;",b"type slip;",1)
        def changed_numerics(files):files["system/fvSolution"]=files["system/fvSolution"].replace(b"consistent yes;",b"consistent no;")
        def changed_initial(files):files["0/k"]=files["0/k"].replace(b"internalField uniform 0.375;",b"internalField uniform 1;")
        def missing(files):files.pop("0/p")
        for component in ("impeller","volute"):
            for default_faces in (False,True):
                for mutate in (unknown,duplicate,changed_outlet,changed_wall,changed_numerics,changed_initial,missing):
                    def altered(*args,**kwargs):
                        files=original(*args,**kwargs);mutate(files);return files
                    with self.subTest(component=component,default_faces=default_faces,mutation=mutate.__name__),patch.object(recipe.upstream,"case_files",side_effect=altered):
                        with self.assertRaisesRegex(recipe.InletFlowRecipeIssue,"exact current-v3"):
                            recipe.case_files(component,default_faces=default_faces)
        profile=deepcopy(recipe.upstream.NUMERICAL_PROFILE);profile["id"]="unknown-v4"
        with patch.object(recipe.upstream,"NUMERICAL_PROFILE",profile),self.assertRaisesRegex(recipe.InletFlowRecipeIssue,"numerical family"):
            recipe.case_files("impeller")

    def test_parsed_section_guard_rejects_unknown_or_duplicate_keys_and_preserves_internal_values(self):
        raw=recipe.upstream.case_files("impeller")["0/p"]
        expected="type totalPressure; p0 uniform 0; value uniform 0;"
        for changed in (raw.replace(b"p0 uniform 0;",b"p0 uniform 1;"),
            raw.replace(b"p0 uniform 0;",b"p0 uniform 0; p0 uniform 0;"),
            raw.replace(b"p0 uniform 0;",b"p0 uniform 0; unknown 1;")):
            with self.subTest(change=changed),self.assertRaises(ValueError):
                recipe.section_change(changed,"p",recipe.INLET,expected,"type fixedFluxPressure; value uniform 0;")

    def test_plan_has_new_pressure_identity_and_only_declared_input_reductions(self):
        plan=recipe.teaching_plan()
        self.assertEqual(plan["familyId"],"teaching-inlet-flow-outlet-static-v1")
        self.assertEqual(recipe.BC_FAMILY,plan["familyId"])
        self.assertEqual(plan["boundaryIdentity"]["outletControl"],"static-pressure-controlled")
        self.assertFalse(plan["boundaryIdentity"]["inletTotalPressurePrescribed"])
        self.assertTrue(plan["nativeInletProfile"]["positiveQMeansIntoDomain"])
        self.assertEqual(plan["nativeInletProfile"]["outwardPhiIntegral"],"-Q")
        self.assertFalse(plan["nativeInletProfile"]["guessedInletAxisOrArea"])
        self.assertEqual(plan["absolutePressureMapping"]["referenceAdditionCount"],1)
        self.assertEqual(plan["absolutePressureMapping"]["referenceLocation"],"outlet")
        self.assertFalse(plan["suctionNpshaBasis"]["outletReferenceIsSuctionPressure"])
        self.assertEqual(set(plan["equationStages"]),{"volumeFlow","angularSpeed","declaredOutletPressure"})
        self.assertEqual(plan["equationStages"]["volumeFlow"]["result"],{"value":90/998.2,"unit":"m3/s"})
        self.assertEqual(plan["equationStages"]["angularSpeed"]["result"]["value"],2*math.pi*1450/60)
        self.assertEqual(plan["equationStages"]["declaredOutletPressure"]["result"],{"value":300000.,"unit":"Pa"})
        for row in plan["equationStages"].values():
            self.assertTrue({"input","equation","substitution","result","calculationAuthority"}<=set(row))
            self.assertEqual(row["calculationAuthority"],"Python")
        self.assertEqual(plan["limits"],recipe.upstream.LIMITS)
        self.assertEqual(plan["parameters"],recipe.upstream.PARAMETERS)
        self.assertEqual(plan["assumptions"]["outletStaticAbsoluteReferencePa"],300000.)
        self.assertFalse(plan["assumptions"]["inletTotalPressurePrescribed"])
        self.assertNotIn("externalTotalPressureAbsolutePa",recipe.ASSUMPTIONS)
        self.assertNotIn("outletVelocityProfile",recipe.ASSUMPTIONS)
        self.assertEqual(plan["sampling"]["pressureReferenceLocation"],"outlet")
        self.assertFalse(plan["geometrySourceAdmissionImplemented"]);self.assertFalse(plan["casePreparerImplemented"])
        for flag in recipe.FALSE_FLAGS:self.assertIs(plan[flag],False)
        self.assertTrue(all(recipe.PIN in source for source in plan["references"].values()))
        plan["limits"]["maximumSeconds"]=1
        self.assertEqual(recipe.teaching_plan()["limits"]["maximumSeconds"],1200)

    def test_component_boolean_and_finite_stage_are_strict(self):
        for component,default in (("other",True),(None,True),("impeller",1),("volute",None)):
            with self.subTest(component=component,default=default),self.assertRaises(recipe.InletFlowRecipeIssue):
                recipe.case_files(component,default_faces=default)
        for value in (True,float("nan"),float("inf")):
            with self.assertRaises(recipe.InletFlowRecipeIssue):recipe.stage({},"x","x",value,"1")


if __name__=="__main__":unittest.main()
