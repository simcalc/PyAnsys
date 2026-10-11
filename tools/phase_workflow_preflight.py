"""Validate public phase job inputs and the exact native baseline artifact source.

No artifact download, native execution or Main write occurs in this preflight.
An admitted run ID is not evidence that its baseline is numerically qualified.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re

REPOSITORY = 'simcalc/PyAnsys'
# One source artifact retains the bounded native ZIP (128 MiB), separate mesh
# candidates (128 MiB), at most eight 4 MiB logs and small identity reports.
# This outer download limit does not alter the unpacker's 128 MiB ZIP limit.
MAX_ARTIFACT_BYTES = 300 * 1024**2


def validate_inputs(env):
    run_id = env.get('BASELINE_RUN_ID', '')
    mode = env.get('PHASE_MODE', '')
    if not re.fullmatch(r'[1-9][0-9]{0,18}', run_id) or mode not in {'steady-sweep', 'transient-sliding'}:
        raise ValueError('Use a completed baseline run ID and the exact authored physical mode.')
    values = {}
    for key, name in (('PRESSURE_ABSOLUTE_PA', 'pressureAbsolutePa'),
                      ('TIME_STEP_S', 'timeStepS'), ('DURATION_S', 'durationS')):
        raw = env.get(key, '')
        if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?', raw):
            raise ValueError('A finite positive SI scalar is required: ' + key)
        value = float(raw)
        if not math.isfinite(value) or value <= 0:
            raise ValueError('A finite positive SI scalar is required: ' + key)
        values[name] = value
    period = 60 / 1450
    if values['timeStepS'] > period / 360 or not 4 * period <= values['durationS'] <= 10 * period:
        raise ValueError('Use at most one rotor degree per step and four to ten revolutions to support the final three-revolution window; actual Co and convergence still need checking.')
    return {'baselineRunId': run_id, 'mode': mode, **values, 'sourceBaselineQualified': False,
            'actualCfdRunVerified': False, 'mainApplicationWrites': False}


def validate_source(inputs, run, artifact_listing):
    run_id = inputs['baselineRunId']
    repository = run.get('repository', {})
    head_repository = run.get('head_repository', {})
    if (str(run.get('id')) != run_id or run.get('status') != 'completed'
            or run.get('conclusion') != 'success' or run.get('event') != 'workflow_dispatch'
            or run.get('path') != '.github/workflows/remesh-frozen-rotor.yml'
            or not re.fullmatch(r'[0-9a-f]{40}', run.get('head_sha', ''))
            or any(row.get('full_name') != REPOSITORY or row.get('private') is not False
                   for row in (repository, head_repository))):
        raise ValueError('Require the actual completed manual volume/MRF workflow from the exact public repository.')
    expected = 'frozen-rotor-remesh-' + run_id
    matches = [row for row in artifact_listing.get('artifacts', []) if row.get('name') == expected]
    if len(matches) != 1:
        raise ValueError('One exact retained native evidence artifact is required.')
    row = matches[0]
    size = row.get('size_in_bytes')
    if (row.get('expired') is not False or isinstance(size, bool) or not isinstance(size, int)
            or not 0 < size <= MAX_ARTIFACT_BYTES or str(row.get('workflow_run', {}).get('id')) != run_id):
        raise ValueError('The source artifact must be unexpired, bounded and bound to the same actual run.')
    return {**inputs, 'artifactName': expected, 'sourceCommit': run['head_sha'],
            'artifactBytes': size, 'sourceBaselineQualified': False,
            'claimBoundary': 'Source admission only; native ZIP byte verification and numerical baseline qualification remain mandatory.'}


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 128 * 1024:
        raise ValueError('Use a bounded regular source metadata JSON file.')
    return json.loads(path.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-json', type=Path)
    parser.add_argument('--artifacts-json', type=Path)
    args = parser.parse_args()
    values = validate_inputs(os.environ)
    if bool(args.run_json) != bool(args.artifacts_json):
        raise ValueError('Supply both exact source metadata files together.')
    if args.run_json:
        values = validate_source(values, read_json(args.run_json), read_json(args.artifacts_json))
    print(json.dumps(values, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
