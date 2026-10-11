"""Synthetic metadata admission tests, never actual CFD evidence."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('phase_preflight_tests', Path(__file__).resolve().parents[1] / 'tools/phase_workflow_preflight.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class AdmissionContracts(unittest.TestCase):
    def inputs(self, **changed):
        return tool.validate_inputs({'BASELINE_RUN_ID': '123456789', 'PHASE_MODE': 'steady-sweep',
                                     'PRESSURE_ABSOLUTE_PA': '101325', 'TIME_STEP_S': '0.0001',
                                     'DURATION_S': '0.3', **changed})

    def fixture(self):
        repo = {'full_name': 'simcalc/PyAnsys', 'private': False}
        run = {'id': 123456789, 'status': 'completed', 'conclusion': 'success', 'event': 'workflow_dispatch',
               'path': '.github/workflows/remesh-frozen-rotor.yml', 'head_sha': 'a' * 40,
               'repository': dict(repo), 'head_repository': dict(repo)}
        artifacts = {'artifacts': [{'name': 'frozen-rotor-remesh-123456789', 'expired': False,
                                  'size_in_bytes': 500, 'workflow_run': {'id': 123456789}}]}
        return run, artifacts

    def test_admission_does_not_claim_numerical_or_physical_qualification(self):
        run, artifacts = self.fixture()
        result = tool.validate_source(self.inputs(), run, artifacts)
        self.assertEqual(result['sourceCommit'], 'a' * 40)
        self.assertFalse(result['sourceBaselineQualified'])
        self.assertFalse(result['actualCfdRunVerified'])
        self.assertFalse(result['mainApplicationWrites'])

    def test_exact_original_polygon_lane_has_its_own_bound_artifact_name(self):
        run, artifacts = self.fixture()
        run['path'] = '.github/workflows/original-frozen-rotor.yml'
        with self.assertRaises(ValueError):tool.validate_source(self.inputs(),run,artifacts)
        artifacts['artifacts'][0]['name'] = 'frozen-rotor-original-123456789'
        result=tool.validate_source(self.inputs(),run,artifacts)
        self.assertEqual(result['artifactName'],'frozen-rotor-original-123456789')
        self.assertFalse(result['sourceBaselineQualified'])

    def test_untrusted_input_values_and_physical_time_bounds_rejected(self):
        for field, value in [('BASELINE_RUN_ID', '../Main'), ('BASELINE_RUN_ID', '0'),
                             ('PHASE_MODE', 'run arbitrary'), ('PRESSURE_ABSOLUTE_PA', 'nan'),
                             ('PRESSURE_ABSOLUTE_PA', '1e999'), ('PRESSURE_ABSOLUTE_PA', '-10'),
                             ('TIME_STEP_S', '0.0002'), ('TIME_STEP_S', '0'),
                             ('DURATION_S', '0.12'), ('DURATION_S', '0.15'), ('DURATION_S', '0.5')]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.inputs(**{field: value})

    def test_wrong_repository_workflow_state_commit_and_run_id_rejected(self):
        for key, value in [('id', 222), ('status', 'in_progress'), ('conclusion', 'failure'),
                           ('event', 'pull_request'), ('path', '.github/workflows/other.yml'), ('head_sha', 'a' * 39)]:
            run, artifacts = self.fixture()
            run[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                tool.validate_source(self.inputs(), run, artifacts)
        for field in ('repository', 'head_repository'):
            for bad in ({'full_name': 'simcalc/npsh', 'private': True},
                        {'full_name': 'simcalc/PyAnsys', 'private': True}):
                run, artifacts = self.fixture()
                run[field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    tool.validate_source(self.inputs(), run, artifacts)

    def test_missing_duplicate_expired_large_or_mismatched_artifact_rejected(self):
        for change in ('missing', 'duplicate', 'expired', 'oversize', 'bool-size', 'wrong-run'):
            run, artifacts = self.fixture()
            row = artifacts['artifacts'][0]
            if change == 'missing':
                artifacts['artifacts'] = []
            elif change == 'duplicate':
                artifacts['artifacts'].append(dict(row))
            elif change == 'expired':
                row['expired'] = True
            elif change == 'oversize':
                row['size_in_bytes'] = tool.MAX_ARTIFACT_BYTES + 1
            elif change == 'bool-size':
                row['size_in_bytes'] = True
            else:
                row['workflow_run']['id'] = 555
            with self.subTest(change=change), self.assertRaises(ValueError):
                tool.validate_source(self.inputs(), run, artifacts)

    def test_outer_artifact_can_include_the_bounded_zip_and_separate_meshes(self):
        run, artifacts = self.fixture()
        artifacts['artifacts'][0]['size_in_bytes'] = 280 * 1024**2
        result = tool.validate_source(self.inputs(), run, artifacts)
        self.assertEqual(result['artifactBytes'], 280 * 1024**2)
        self.assertFalse(result['sourceBaselineQualified'])


if __name__ == '__main__':
    unittest.main()
