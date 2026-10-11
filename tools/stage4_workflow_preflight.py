"""Admit the fixed-clock Stage4 pilot and exact native baseline artifact.

Admission is not physical execution, restart verification or convergence.
"""
import argparse
import json
import os
from pathlib import Path

import phase_workflow_preflight as source


def validate_inputs(env):
    period = 60 / 1450
    values = source.validate_inputs({
        'BASELINE_RUN_ID': env.get('BASELINE_RUN_ID', ''),
        'PHASE_MODE': 'transient-sliding',
        'PRESSURE_ABSOLUTE_PA': env.get('PRESSURE_ABSOLUTE_PA', ''),
        'TIME_STEP_S': repr(period / 360),
        'DURATION_S': repr(5.5 * period),
    })
    return {**values, 'adjustTimeStep': False, 'timeStepRefinementRatio': 2,
            'requestedStepsPerRevolution': [360, 720, 1440],
            'checkpointRevolutions': 2.75, 'endpointRevolutions': 5.5,
            'commonSamplingWindowRevolutions': [2.5, 5.5],
            'actualClockVerified': False, 'restartVerified': False,
            'headTimeConvergenceVerified': False, 'npsh3ConvergenceVerified': False,
            'claimBoundary': 'Fixed pilot settings only. Actual clocks, native fields, numerical gates and restart comparison remain mandatory.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-json', type=Path)
    parser.add_argument('--artifacts-json', type=Path)
    args = parser.parse_args()
    values = validate_inputs(os.environ)
    if bool(args.run_json) != bool(args.artifacts_json):
        raise ValueError('Supply both exact source metadata files together.')
    if args.run_json:
        values = source.validate_source(values, source.read_json(args.run_json),
                                        source.read_json(args.artifacts_json))
    print(json.dumps(values, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
