import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import stage4_workflow_preflight as preflight


class Stage4AdmissionTests(unittest.TestCase):
    def test_fixed_clock_plan_is_admission_only(self):
        row = preflight.validate_inputs({'BASELINE_RUN_ID': '123', 'PRESSURE_ABSOLUTE_PA': '300000'})
        self.assertFalse(row['adjustTimeStep'])
        self.assertEqual(row['requestedStepsPerRevolution'], [360, 720, 1440])
        for key in ('actualClockVerified', 'restartVerified', 'headTimeConvergenceVerified',
                    'npsh3ConvergenceVerified', 'sourceBaselineQualified', 'actualCfdRunVerified'):
            self.assertFalse(row[key])
        self.assertEqual(row['commonSamplingWindowRevolutions'], [2.5, 5.5])

    def test_nonfinite_negative_or_shell_pressure_cannot_be_admitted(self):
        for value in ('nan', 'inf', '1e999', '-1', '0', '3; echo bad', '$(bad)', '3\n4'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                preflight.validate_inputs({'BASELINE_RUN_ID': '123', 'PRESSURE_ABSOLUTE_PA': value})

    def test_user_cannot_silently_change_fixed_time_family(self):
        row = preflight.validate_inputs({'BASELINE_RUN_ID': '123', 'PRESSURE_ABSOLUTE_PA': '300000',
                                        'TIME_STEP_S': '100', 'DURATION_S': '1e9', 'PHASE_MODE': 'steady-sweep'})
        self.assertEqual(row['mode'], 'transient-sliding')
        self.assertEqual(row['timeStepS'], (60 / 1450) / 360)
        self.assertEqual(row['durationS'], 5.5 * (60 / 1450))


if __name__ == '__main__':
    unittest.main()
