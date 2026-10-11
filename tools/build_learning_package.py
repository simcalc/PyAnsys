"""Package reviewed official tutorial files as data; never execute source or Fluent."""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

PIN = '8ef505f6ef699eeec68a16001df91ba353342f40'
FILES = {
 'frozen_rotor_workflow.py': (16936,'4ba6f56c2c84c18cbe662bf13c93d4001670644e59eea81547bc4649bea82afc'),
 'frozen_rotor_workflow.ipynb': (17723,'79205bb393349099895a83e246c030aceca5a0b17e404f3b68629177f3fdb5c7'),
 'impeller.msh.h5': (35217934,'541e06f11cf8aac89d18fe1fe26572667879f3b24b40ea5223cac63e10eb8d50'),
 'volute.msh.h5': (13302081,'edc570cae6d2a7a1ad8d1753ebf5dd5b68accbff1819df2a8b3e20fe2c46c8b3'),
 'EXAMPLE_DATA_LICENSE.txt': (1114,'46617266d39d9ecdd829d29ef6e9a237e9312e71531ddc258e2303e28ab041db'),
}
SOURCE_SHA = '7c2a8aec9be706bc3ce35113b84e63ec5d55e6ab8a3cf8aa24650fcc0a053ca6'
README = '''Impeller-Volute Frozen Rotor learning package

Includes the unmodified official Python script and notebook, both actual HDF5
meshes (not Git LFS pointer files), original MIT notices and upstream provenance.
The source-only ZIP is also preserved as a separate release asset.

This package is prepared learning input. No solver was executed and no native
case/data, head, residuals, cavitation, NPSHr or physical transient result is
provided. The notebook contains no executed cells or stored outputs.

Verify every SHA-256 in example-provenance.json before use. Install the separate
PyFluent SDK in its own Python environment. Actual tutorial execution requires
native licensed Fluent 2025 R2 or later. Do not launch it from the Main application.
Copy source/meshes into a separate run directory to preserve these originals.

Before running, review the active native angular units and omega: source changes
display units to rev/min but assigns the rad/s expression for 1450 rpm. Verify
material density/viscosity against report constants, the active pressure inlet
and absolute operating-pressure datum, mesh quality/interface and wall settings.
The source uses four processes: apply the project's separate bounded worker
resource policy before execution. No worker settings are changed by this package.

The 200 steady iterations and pseudo-time factor10 do not define physical
transient time. Head uses mass-averaged total pressures and 998.2kg/m3,9.81m/s2.
A pressure sweep and numerical/physical validation are separate research steps.

Original source:
https://fluent.docs.pyansys.com/version/stable/examples/00-fluent/frozen_rotor_workflow.html
Pinned meshes:
https://github.com/ansys/example-data/tree/8ef505f6ef699eeec68a16001df91ba353342f40/pyfluent/examples/pump-volute
'''

def build(directory: Path, source_zip: Path, output: Path) -> dict:
    report_path=directory/'installation.json'
    if report_path.is_symlink() or report_path.stat().st_size>65536: raise ValueError('Use a bounded regular installation manifest.')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('schemaVersion') != 'untirta.pyfluent-frozen-rotor-example.v1' or report.get('exampleDataCommit') != PIN:
        raise ValueError('Use the exact reviewed official example installation.')
    for key in ('simulationExecuted','actualCfdRunVerified','mainApplicationWrites','licenseVerified','meshQualityCheckedBySolver'):
        if report.get(key) is not False: raise ValueError('Keep installation and solver claims separate.')
    inventory=report.get('files',[])
    if len(inventory)!=len(FILES) or {row.get('name') for row in inventory}!=set(FILES): raise ValueError('Use the exact reviewed inventory.')
    for row in inventory:
        if (row.get('bytes'),row.get('sha256'))!=FILES[row['name']]: raise ValueError('Preserve the pinned file provenance.')
    data = {}
    for name,(size,digest) in FILES.items():
        path=directory/name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            raise ValueError('Use only the exact regular official file: '+name)
        raw=path.read_bytes()
        if sha256(raw).hexdigest()!=digest: raise ValueError('Official checksum mismatch: '+name)
        if name.endswith('.msh.h5') and raw[:8]!=b'\x89HDF\r\n\x1a\n': raise ValueError('Mesh must contain actual HDF5 bytes.')
        data[name]=raw
    if source_zip.is_symlink() or source_zip.stat().st_size!=34935: raise ValueError('Use the regular official source ZIP.')
    source=source_zip.read_bytes()
    if len(source)!=34935 or sha256(source).hexdigest()!=SOURCE_SHA: raise ValueError('Preserve the exact official source ZIP.')
    notebook=json.loads(data['frozen_rotor_workflow.ipynb'])
    cells=[cell for cell in notebook['cells'] if cell['cell_type']=='code']
    if any(cell.get('execution_count') is not None or cell.get('outputs') for cell in cells): raise ValueError('No notebook results may be published.')
    provenance={key:report[key] for key in ('schemaVersion','title','documentationUrl','sourceArchiveUrl','sourceArchiveSha256','exampleDataRepository','exampleDataCommit','installedFileBytes','requiredSdkVersion','minimumFluentRelease','physicalModel','sourceRole','referenceBinding','requestedTutorialCpuCount','iterationBudget','tutorialOperatingInputs','inputAuthority','rotationAxisOriginM','rotationAxisDirection','viscousModel','sourceReviewChecks','nativeCaseGenerated','cavitationModelEnabled','transientSimulationExecuted','pumpNpshrValidated','licenseVerified','actualCfdRunVerified','simulationExecuted','mainApplicationWrites','meshQualityCheckedBySolver')}
    provenance.update(evidenceScope='Verified upstream source and actual native mesh bytes; no solver execution or numerical qualification',notebookCodeCells=len(cells),notebookExecutedCells=0)
    provenance['files']=[{key:value for key,value in row.items() if key!='localRelativePath'} for row in report['files']]
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()): raise ValueError('Use a new empty release output directory; existing assets are never overwritten.')
    manifest=(json.dumps(provenance,indent=2,allow_nan=False)+'\n').encode()
    data['example-provenance.json']=manifest; data['README-learning.txt']=README.encode()
    archive=output/'frozen-rotor-learning-20261011.zip'
    with ZipFile(archive,'w',ZIP_DEFLATED,compresslevel=6) as bundle:
        for name,raw in data.items(): bundle.writestr(name,raw)
    (output/'frozen_rotor_workflow.zip').write_bytes(source)
    (output/'example-provenance.json').write_bytes(manifest)
    (output/'README-learning.txt').write_bytes(README.encode())
    (output/'EXAMPLE_DATA_LICENSE.txt').write_bytes(data['EXAMPLE_DATA_LICENSE.txt'])
    assets=sorted(output.iterdir())
    (output/'SHA256SUMS').write_text(''.join(sha256(path.read_bytes()).hexdigest()+'  '+path.name+'\n' for path in assets),encoding='utf-8')
    return {'bundle':archive.name,'bytes':archive.stat().st_size,'sha256':sha256(archive.read_bytes()).hexdigest(),'meshBytes':sum(FILES[name][0] for name in ('impeller.msh.h5','volute.msh.h5')),'simulationExecuted':False}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--example-directory',required=True,type=Path);parser.add_argument('--source-zip',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(build(args.example_directory,args.source_zip,Path(__file__).resolve().parents[1]/'dist/frozen-rotor-learning-20261011')))
