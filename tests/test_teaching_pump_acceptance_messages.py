"""Actionable logs cannot hide missing evidence, failed gates or alter science."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import teaching_pump_acceptance_messages as messages

def fixture():
    return {'status':'NumericalAcceptanceRequired','actualCfdRunVerified':True,'numericalBaselineQualified':False,
        'qualifiedBaseline':False,'mainApplicationWrites':False,'diagnostics':[{'reasonCode':'CFD_REVERSE_FLOW_TREATMENT_REQUIRED'}],
        'stages':{'head':{'result':{'value':-1.0728556502022475,'unit':'m'}}},
        'study':{'acceptance':{'reverseFlowFractionLimit':.005,'headWindowRelativeLimit':.005,'residualLimit':.0001}},
        'nativeObservation':{'physicalBasis':{'headMeasurement':'inlet-to-extended-outlet-assembly'},
            'quality':{'reverseFlowFraction':.01645109349502972,'headHistoryM':[-1.05,-1.08],
                'residuals':{'p':[.002154188437698948],'U':[4e-5],'k':[5e-5],'omega':[3e-5]}}}}

class AcceptanceMessageTests(unittest.TestCase):
    def test_numeric_rejection_is_explained_and_original_flags_never_changed(self):
        source=fixture();before=deepcopy(source);out=messages.describe(source)
        self.assertEqual(source,before);self.assertTrue(out['actualCfdRunVerified'])
        self.assertFalse(out['numericalBaselineQualified']);self.assertFalse(out['qualifiedBaseline'])
        self.assertEqual([r['criterionSatisfied'] for r in out['criteria']],[False,False,False,False,True,True,True])
        self.assertEqual(out['reasonCodes'],['CFD_REVERSE_FLOW_TREATMENT_REQUIRED'])
        self.assertEqual(out['criteria'][0]['actual'],-1.0728556502022475)
        self.assertEqual(out['criteria'][3]['limit'],.0001);self.assertIn('Exit2',out['explanation'])
        json.dumps(out,allow_nan=False)

    def test_equal_limits_pass_but_zero_head_and_missing_arrays_do_not(self):
        self.assertTrue(messages.criterion('residual',.0001,.0001,'<=','1')['criterionSatisfied'])
        self.assertFalse(messages.criterion('head',0.,0.,'>','m')['criterionSatisfied'])
        source=fixture();source['nativeObservation']['quality']['residuals']['p']=[]
        out=messages.describe(source);self.assertFalse(out['criteria'][3]['evidenceAvailable'])
        self.assertFalse(out['criteria'][3]['criterionSatisfied'])

    def test_missing_nonfinite_boolean_or_native_failure_never_gets_pass_or_success(self):
        for value in (None,float('nan'),float('inf'),True):
            out=messages.criterion('head',value,0.,'>','m')
            self.assertIsNone(out['actual']);self.assertFalse(out['criterionSatisfied'])
        source=fixture();source['actualCfdRunVerified']=False;source.pop('nativeObservation');source.pop('study');source.pop('stages')
        out=messages.describe(source);self.assertFalse(out['actualCfdRunVerified'])
        self.assertTrue(all(not c['criterionSatisfied'] for c in out['criteria']))
        self.assertNotIn('execution completed',out['explanation'])

if __name__=='__main__':unittest.main()
