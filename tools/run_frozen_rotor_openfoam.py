"""Bounded native conversion and MRF execution on an isolated public runner.

An executed solver is recorded separately from a converged/qualified baseline.
This first adapter never publishes NPSHr or physical transient results.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_openfoam_case as case_tool
import inspect_frozen_rotor_mesh as inventory

MAX_SECONDS = 1200
MAX_WORKSPACE = 512 * 1024**2
MAX_LOG = 4 * 1024**2


def workspace_size(root):
    total = 0
    for path in root.rglob('*'):
        if path.is_symlink():
            raise case_tool.CaseIssue('Native workspace cannot contain symbolic links.')
        if path.is_file():
            total += path.stat().st_size
    if total > MAX_WORKSPACE:
        raise case_tool.CaseIssue('Native workspace exceeds the fixed 512 MiB bound.')
    return total


def execute(arguments, log_path, workspace, deadline):
    executable = shutil.which(arguments[0])
    if not executable or not Path(executable).resolve().is_relative_to(Path('/opt/openfoam12')):
        raise case_tool.CaseIssue('Use the exact official Foundation-12 native executable.')
    started = time.monotonic()
    process = None
    with log_path.open('xb') as stream:
        try:
            process = subprocess.Popen([executable, *arguments[1:]], stdout=stream,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            while process.poll() is None:
                if time.monotonic() >= deadline or log_path.stat().st_size > MAX_LOG:
                    raise case_tool.CaseIssue('The native time/log execution bound was reached.')
                workspace_size(workspace)
                time.sleep(.5)
            if process.returncode != 0:
                raise case_tool.CaseIssue(arguments[0] + ' returned exit ' + str(process.returncode))
            # A fast process can exit before the first watchdog iteration.
            workspace_size(workspace)
            if time.monotonic() >= deadline:
                raise case_tool.CaseIssue('The native time execution bound was reached at completion.')
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
    raw = case_tool.read_regular(log_path, MAX_LOG)
    text = raw.decode('utf-8')
    if re.search(r'FOAM FATAL|Segmentation fault|Floating point exception', text) or not re.search(r'(?m)^End\s*$', text):
        raise case_tool.CaseIssue('Native completion and fatal-error checks did not pass: ' + arguments[0])
    return {'program': arguments[0], 'exitCode': 0, 'elapsedSeconds': round(time.monotonic()-started, 3),
            'log': log_path.name, 'logBytes': len(raw), 'logSha256': sha256(raw).hexdigest()}, text


def run(mesh_directory, output_directory):
    if sys.platform != 'linux' or os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('WM_PROJECT_VERSION') != '12':
        raise case_tool.CaseIssue('Require the independent public Linux Foundation-12 execution environment.')
    prepared = case_tool.prepare(mesh_directory, output_directory)
    output = Path(output_directory).absolute()
    manifest = json.loads((output / 'openfoam-preparation.json').read_text())
    meshes, _ = case_tool.validate_inputs(mesh_directory)
    report = {'schemaVersion': 'untirta.frozen-rotor-openfoam-execution.v1',
              'githubSourceCommit': os.environ.get('GITHUB_SHA'), 'githubRunId': os.environ.get('GITHUB_RUN_ID'),
              'preparationReportSha256': sha256((output / 'openfoam-preparation.json').read_bytes()).hexdigest(),
              'limits': {'maximumSeconds': MAX_SECONDS, 'maximumWorkspaceBytes': MAX_WORKSPACE, 'maximumLogBytes': MAX_LOG,
                         'memoryBytes': 2147483648, 'cpus': 1, 'enforcement': 'workflow cgroup plus process/output watchdog'},
              'stages': [], 'conversionEvidence': [], 'physicalWallEvidence': [], 'status': 'NATIVE_EXECUTION_REQUIRED',
              'openFoamConversionVerified': False, 'nativeGeometryQualityVerified': False,
              'physicalWallTypesVerified': False,
              'coupledInterfaceCoverageVerified': False, 'simulationExecuted': False, 'actualCfdRunVerified': False,
              'qualifiedBaseline': False, 'cavitationSweepExecuted': False, 'npsh3Qualified': False,
              'physicalTransientExecuted': False, 'mainApplicationWrites': False,
              'claimBoundary': 'Actual native execution evidence only. Convergence, conservation, interface coverage, wall resolution, numerical sensitivity and physical validation are distinct gates.'}
    started, current = time.monotonic(), 'preflight'
    logs = output / 'logs'
    logs.mkdir()
    commands = manifest['nativeCommandArguments']
    try:
        for index, arguments in enumerate(commands):
            current = arguments[0]
            # All commands are authored by the fixed case preparer, never an uploaded script.
            row, text = execute(arguments, logs / f'{index:02d}-{current}.log', output, started+MAX_SECONDS)
            report['stages'].append(row)
            if current == 'gmshToFoam':
                component = 'impeller' if index == 0 else 'volute'
                report['conversionEvidence'].append(case_tool.verify_conversion(output / (component+'-case'), meshes[component], logs / row['log']))
                report['openFoamConversionVerified'] = len(report['conversionEvidence']) == 2
            elif current == 'changeDictionary':
                component = 'impeller' if index == 1 else 'volute'
                converter_log = logs / f'{index-1:02d}-gmshToFoam.log'
                report['physicalWallEvidence'].append(case_tool.verify_conversion(
                    output / (component+'-case'), meshes[component], converter_log,
                    after_wall_type_change=True))
                report['physicalWallTypesVerified'] = len(report['physicalWallEvidence']) == 2
            elif current == 'checkMesh':
                if 'Mesh OK.' not in text or re.search(r'Failed [1-9][0-9]* mesh checks', text):
                    raise case_tool.CaseIssue('Native mesh geometry/topology checks did not pass.')
                report['nativeGeometryQualityVerified'] = True
            elif current == 'foamRun':
                final = output / 'impeller-case/500'
                workspace_size(output)
                if not final.is_dir() or not all((final / name).is_file() for name in ('p', 'U', 'k', 'omega')):
                    raise case_tool.CaseIssue('The fixed steady iteration endpoint needs actual native final fields.')
                fields = [{'name': name, 'bytes': (final/name).stat().st_size,
                           'sha256': sha256(case_tool.read_regular(final/name, case_tool.MAX_NATIVE_BYTES)).hexdigest()}
                          for name in ('p','U','k','omega')]
                report['finalNativeFields'] = fields
                report.update(simulationExecuted=True, actualCfdRunVerified=True, status='MRF_EXECUTED_ACCEPTANCE_REQUIRED')
        report['workspaceBytes'] = workspace_size(output)
    except Exception as error:
        report.update(status='NATIVE_STAGE_CORRECTION_REQUIRED', reasonCode='CFD_NATIVE_STAGE_CORRECTION_REQUIRED',
                      actualCfdRunVerified=False,
                      diagnostic={'program': current, 'type': type(error).__name__, 'message': str(error)[:500]})
    report['elapsedSeconds'] = round(time.monotonic()-started, 3)
    (output / 'openfoam-execution.json').write_bytes(inventory.bounded_json(report))
    print(json.dumps({key: report[key] for key in ('status','openFoamConversionVerified','nativeGeometryQualityVerified',
                                                'actualCfdRunVerified','qualifiedBaseline','mainApplicationWrites')}))
    return 0 if report['status'] == 'MRF_EXECUTED_ACCEPTANCE_REQUIRED' and report['actualCfdRunVerified'] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mesh-directory', type=Path, required=True)
    parser.add_argument('--output-directory', type=Path, required=True)
    args = parser.parse_args()
    return run(args.mesh_directory, args.output_directory)


if __name__ == '__main__':
    raise SystemExit(main())
