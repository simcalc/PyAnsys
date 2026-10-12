"""Synthetic byte/topology contracts only; no native CAD/CFD is executed."""
from collections import defaultdict
from contextlib import contextmanager
from hashlib import sha256
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import teaching_pump_baseline_case as case
import teaching_pump_native_case as native_case
import run_teaching_pump_geometry as producer
from test_teaching_pump_native_case import source_fixture


def synthetic_outlet_proof(root,**kwargs):
    """Miniature topology fixture is NOT the authored rectangle/native evidence.

    Only the independent geometric profile proof is mocked here. Its complete
    parser/math/mutation gates are exercised in test_teaching_pump_outlet_profile.
    These recipe/copy/SHA tests confer no actual geometry or CFD qualification.
    """
    report,row=case.json_file(root,'source-geometry/teaching-geometry.json')
    return {'schemaVersion':case.outlet.SCHEMA,'syntheticFixtureNotNativeEvidence':True,'profile':case.outlet.PROFILE,
        'geometryReportFile':row,'nativeMeshFileRecords':[case.record(root,'component-mesh-snapshots/volute/constant/polyMesh/'+n)[1] for n in case.MESH_FILES]}


def native_component(component,raw):
    text=raw.decode();sections={m[1]:m[2].strip().splitlines() for m in case.re.finditer(r"(?m)^\$(\w+)\s*\n(.*?)^\$End\1\s*$",text,case.re.S)}
    physical={int(row.split()[1]):row.split('"')[1] for row in sections['PhysicalNames'][1:]}
    coords=[tuple(map(float,row.split()[1:])) for row in sections['Nodes'][1:]];tets=[];tags={};face_occ=defaultdict(list)
    for row in sections['Elements'][1:]:
        values=list(map(int,row.split()));nodes=tuple(v-1 for v in values[3+values[2]:])
        if values[1]==4:tets.append(nodes)
        else:tags[tuple(sorted(nodes))]=physical[values[3]]
    for cell,tet in enumerate(tets):
        for slots in ((0,2,1),(0,1,3),(1,2,3),(2,0,3)):
            face=tuple(tet[i] for i in slots);face_occ[tuple(sorted(face))].append((cell,face))
    internal=[];patches={p:[] for p in sorted(case.base.PATCHES[component])}
    for key,rows in face_occ.items():
        if len(rows)==2:internal.append((rows[0][1],rows[0][0],rows[1][0]))
        else:patches[tags[key]].append((rows[0][1],rows[0][0]))
    return {'points':coords,'internal':internal,'patches':patches,'cells':len(tets)}


def write_native(folder,components,*,merged=False):
    root=folder/'constant/polyMesh';root.mkdir(parents=True,exist_ok=True);points=[];internals=[];patches={};zones=[];node_offset=cell_offset=0
    for name,data in components.items():
        points.extend(data['points']);internals.extend([(tuple(n+node_offset for n in face),own+cell_offset,nei+cell_offset) for face,own,nei in data['internal']])
        for patch,rows in data['patches'].items():patches[patch]=[(tuple(n+node_offset for n in face),own+cell_offset) for face,own in rows]
        zones.append(('rotating-fluid' if name=='impeller' else 'stationary-fluid',list(range(cell_offset,cell_offset+data['cells']))))
        node_offset+=len(data['points']);cell_offset+=data['cells']
    faces=[r[0] for r in internals];owners=[r[1] for r in internals];neighbours=[r[2] for r in internals];boundary=[];start=len(faces)
    for name,rows in patches.items():
        kind='wall' if name in case.base.ROTATING|case.base.STATIONARY else 'patch'
        boundary.append(f'{name} {{type {kind}; nFaces {len(rows)}; startFace {start};}}');start+=len(rows)
        faces.extend(face for face,_ in rows);owners.extend(own for _,own in rows)
    boundary.append(f'defaultFaces {{type patch; nFaces 0; startFace {start};}}')
    if merged:
        names=('interface-impeller-outlet','interface-volute-inlet')
        for name,opposite in zip(names,reversed(names)):
            boundary.append('pumpInterface_on_'+name+f' {{type nonConformalCyclic; nFaces 0; startFace {start}; originalPatch {name}; neighbourPatch pumpInterface_on_{opposite}; transformType none;}}')
        for name in names:boundary.append('nonConformalError_on_'+name+f' {{type nonConformalError; nFaces 0; startFace {start}; originalPatch {name};}}')
    def counted(rows):return str(len(rows))+'\n('+' '.join(rows)+')'
    bodies={'points':counted(['('+' '.join(map(str,p))+')' for p in points]),'faces':counted([str(len(f))+'('+' '.join(map(str,f))+')' for f in faces]),
        'owner':counted(list(map(str,owners))),'neighbour':counted(list(map(str,neighbours))),'boundary':counted(boundary),
        'cellZones':counted([name+' {type cellZone; cellLabels List<label> '+str(len(ids))+'('+' '.join(map(str,ids))+');}' for name,ids in zones])}
    kinds={'points':'vectorField','faces':'faceList','owner':'labelList','neighbour':'labelList','boundary':'polyBoundaryMesh','cellZones':'cellZoneList'}
    for name,body in bodies.items():(root/name).write_text(case.base.foam_header(name,kinds[name])+body)


def native_source(root):
    source,geometry_module=source_fixture(root);path=source/'teaching-geometry.json';packet=json.loads(path.read_bytes())
    packet['parameters'].update(case.PARAMETERS);packet['geometryIdentitySha256']='d'*64;path.write_bytes(case.encoded(packet))
    output=root/'geometry-native';native_case.prepare(source,output)
    data={name:native_component(name,(source/(name+'.msh')).read_bytes()) for name in native_case.COMPONENTS}
    for name in data:write_native(output/(name+'-case'),{name:data[name]})
    runtime={'schemaVersion':producer.RUNTIME_SCHEMA,'openFoamFoundationVersion':12,'packageVersion':'20250206','officialDebSha256':case.safety.PACKAGE_SHA,
        'expectedSourceCommit':case.safety.SOURCE_COMMIT,'mainApplicationWrites':False,'binaries':[{'program':name,'path':'/opt/openfoam12/bin/'+name,'bytes':6,
            'sha256':sha256(b'native').hexdigest(),'packageOwnership':'openfoam12: /opt/openfoam12/bin/'+name} for name in producer.PROGRAMS]}
    (output/producer.RUNTIME).write_bytes(case.encoded(runtime));(output/'logs').mkdir();stages=[];logs=[];texts=[]
    for i,args in enumerate(native_case.commands(output)):
        text='SYNTHETIC-CONTRACT-NOT-NATIVE-CFD\nBuild :12-'+case.safety.SOURCE_COMMIT[:12]+'\n'
        if args[0]=='checkMesh':text+='    cells: '+('12' if i==8 else '6')+'\n'+('   *Number of regions: 2\n' if i==8 else '    Number of regions: 1 (OK).\n')+'Min volume = 0.1. Max volume = 0.2. Total volume = 1.\nMesh OK.\n'
        if i==8:text+='Source min/average/max coverage = 1/1/1\nTarget min/average/max coverage = 1/1/1\n'
        text+='End\n';relative=f'logs/{i:02d}-{args[0]}.log';(output/relative).write_text(text);row=case.record(output,relative)[1]
        stages.append({'program':args[0],'exitCode':0,'elapsedSeconds':.001,'log':Path(relative).name,'logBytes':row['bytes'],'logSha256':row['sha256']});logs.append(row);texts.append(text)
    components=native_case.verify_output(output)['components'];conversion=[];walls=[];qualities=[];snapshots=[]
    for name,index in zip(native_case.COMPONENTS,(0,3)):
        msh=case.base.msh_inventory((source/(name+'.msh')).read_bytes());folder=output/(name+'-case')
        conversion.append(case.base.verify_conversion(folder,msh,output/logs[index]['path'],after_wall_type_change=False))
        walls.append(case.base.verify_conversion(folder,msh,output/logs[index]['path'],after_wall_type_change=True))
        qualities.append({'component':name,'connectedFluidRegionCount':1,**producer.reduction.parse_mesh_quality(texts[index+2])})
        snap=output/'component-mesh-snapshots'/name;write_native(snap,{name:data[name]})
        snapshots.append({'component':name,'files':[case.record(output,f'component-mesh-snapshots/{name}/constant/polyMesh/{f}')[1] for f in producer.MESH_FILES]})
    write_native(output/'impeller-case',data,merged=True)
    verified=native_case.verify_output(output)
    execution={'schemaVersion':producer.SCHEMA,'sourceGeometryRoute':case.ROUTE,'githubRepository':'simcalc/PyAnsys','githubRunId':'123','githubSourceCommit':'a'*40,
        'status':'CLASSROOM_AUTHORED_NATIVE_GEOMETRY_ACCEPTED_PHYSICS_REQUIRED','limits':native_case.LIMITS,'stages':stages,'conversionEvidence':conversion,
        'physicalWallEvidence':walls,'componentQualityEvidence':qualities,'componentNativeSnapshots':snapshots,'preparationReportSha256':verified['reportFile']['sha256'],
        'nativeInputManifestSha256':verified['report']['nativeInputManifestSha256'],'sourceGeometryReportSha256':verified['report']['sourceGeometryReportSha256'],
        'nativeRuntimeIdentitySha256':case.record(output,producer.RUNTIME)[1]['sha256'],'commonInterfaceEvidence':verified['report']['commonInterfaceEvidence'],
        **{k:False for k in native_case.FALSE_FLAGS},'openFoamConversionVerified':True,'physicalWallTypesVerified':True,'inputArtifactsImmutable':True,
        'sourceGeometryImmutable':True,'nativeGeometryAccepted':True,'nativeGeometryQualityVerified':True,'coupledInterfaceCoverageVerified':True,
        'commandExecutionPerformedByThisTool':True,'elapsedSeconds':1.,'retainedNativeLogRecords':logs,'mergedFluidRegionCount':2,
        'mergedNativeMeshEvidence':case.base.verify_merged_mesh(output/'impeller-case',components,output/logs[8]['path']),
        'nativeMeshQuality':producer.reduction.parse_mesh_quality(texts[8]),'workspaceBytes':sum(r['bytes'] for r in case.source_inventory(output))}
    (output/producer.REPORT).write_bytes(case.encoded(execution))
    return output,geometry_module


class TeachingBaselineCaseTests(unittest.TestCase):
    @contextmanager
    def environment(self,temp):
        with mock.patch.dict(os.environ,{'RUNNER_TEMP':temp}),mock.patch.object(case.outlet,'verify_outlet',side_effect=synthetic_outlet_proof):
            yield

    def test_pure_physics_has_explicit_fixed_operating_pressure_and_stationary_intake(self):
        files=case.case_files('impeller');self.assertIn(b'pRef 300000;',files['system/controlDict']);self.assertNotIn(b'pRef 101325;',files['system/controlDict'])
        self.assertNotIn(b'nonRotatingPatches',files['constant/MRFProperties'])
        _,walls=case.fields.boundary_blocks(files['0/U'],'U')
        self.assertEqual(walls['inlet']['type'],'pressureInletOutletVelocity')
        self.assertEqual(walls['mass-flow-inlet-11']['type'],'fixedValue')
        self.assertEqual(walls['mass-flow-inlet-11']['value'],'uniform (0 18.786550564837889 0)')
        self.assertEqual(walls['inblock-hub']['type'],'noSlip');self.assertEqual(walls['inblock-shroud']['type'],'noSlip')
        for patch in ('blade','impeller-hub','impeller-shroud'):self.assertEqual(walls[patch]['type'],'MRFnoSlip')
        self.assertIn(b'omega 1450 [rpm];',files['constant/MRFProperties']);self.assertIn(b'fields true;',files['system/createNonConformalCouplesDict'])
        self.assertIn(b'endTime 500;',files['system/controlDict']);self.assertIn(b'type MRFnoSlip;',files['0/U']);self.assertIn(b'type noSlip;',files['0/U'])
        self.assertIn('not measured or verified',case.ASSUMPTIONS['headDatum']);self.assertEqual(case.ASSUMPTIONS['solverGravityMS2'],[0,0,0])
        self.assertIn('actual stationary BCs',case.ASSUMPTIONS['wallAdaptation'])
        self.assertEqual([a[0] for a in case.commands('fresh')],['mergeMeshes','createNonConformalCouples','checkMesh','foamRun'])

    def test_native_sst_wall_distance_recipe_is_present_for_both_components(self):
        for component in ('impeller','volute'):
            files=case.case_files(component)
            self.assertEqual(len(case.re.findall(rb'\bwallDist\s*\{',files['system/fvSchemes'])),1)
            self.assertIn(b'wallDist {method meshWave;}',files['system/fvSchemes'])
            self.assertIn(b'model kOmegaSST;',files['constant/momentumTransport'])
            self.assertIn(b'endTime 500;',files['system/controlDict'])
        self.assertNotIn('wallDist',case.physics.case_files('impeller')['system/fvSchemes'])

    def test_stabilization_and_one_explicit_outlet_candidate_keep_other_physics_and_old_recipe(self):
        for component in ('impeller','volute'):
            original={p:text.encode('ascii') for p,text in case.physics.case_files(component,300000).items()}
            before={p:sha256(raw).hexdigest() for p,raw in original.items()}
            fresh=case.case_files(component)
            self.assertEqual(set(fresh),set(original))
            for path in set(original)-{'system/fvSchemes','system/fvSolution','0/U'}:
                self.assertEqual(fresh[path],original[path],path)
            # One reviewed boundary block changes; all other velocity roles,
            # SI/internal state, pressure and immutable original helper remain.
            old_prefix,old_bc=case.fields.boundary_blocks(original['0/U'],'U')
            new_prefix,new_bc=case.fields.boundary_blocks(fresh['0/U'],'U')
            self.assertEqual(old_prefix,new_prefix)
            self.assertEqual({p:r for p,r in old_bc.items() if p!='mass-flow-inlet-11'},
                {p:r for p,r in new_bc.items() if p!='mass-flow-inlet-11'})
            self.assertEqual(old_bc['mass-flow-inlet-11']['type'],'flowRateOutletVelocity')
            self.assertEqual(new_bc['mass-flow-inlet-11']['type'],'fixedValue')
            self.assertEqual(before,{p:sha256(text.encode('ascii')).hexdigest() for p,text in case.physics.case_files(component,300000).items()})
            self.assertIn(b'div(phi,U) bounded Gauss upwind;',fresh['system/fvSchemes'])
            self.assertIn(b'div(phi,k) bounded Gauss upwind;',fresh['system/fvSchemes'])
            self.assertIn(b'div(phi,omega) bounded Gauss upwind;',fresh['system/fvSchemes'])
            self.assertNotIn(b'limitedLinear',fresh['system/fvSchemes'])
            self.assertIn(b'limited corrected 0.5',fresh['system/fvSchemes'])
            self.assertIn(b'nNonOrthogonalCorrectors 2;',fresh['system/fvSolution'])
            self.assertIn(b'endTime 500;',fresh['system/controlDict'])
        profile=case.ASSUMPTIONS['numericalStabilization']
        self.assertEqual(profile['convectiveOrder'],1);self.assertFalse(profile['accuracyQualified']);self.assertFalse(profile['convergencePredicted'])
        self.assertEqual(profile['priorRejectedRunId'],'38197674270')
        self.assertEqual(case.LIMITS['maximumSeconds'],1200)

    def test_pressure_and_transport_linear_stopping_and_underrelaxation_are_separate(self):
        raw=case.case_files('impeller')['system/fvSolution'].decode('ascii')
        pressure=case.re.search(r'\bp\s*\{([^{}]+)\}',raw)[1]
        transport=case.re.search(r'"\(U\|k\|omega\)"\s*\{([^{}]+)\}',raw)[1]
        self.assertIn('tolerance 1e-8;',pressure);self.assertIn('relTol 0;',pressure);self.assertIn('maxIter 100;',pressure)
        self.assertIn('tolerance 1e-7;',transport);self.assertIn('relTol 0.01;',transport)
        self.assertIn('minIter 1;',transport);self.assertIn('maxIter 100;',transport)
        self.assertIn('fields {p 0.2;}',raw);self.assertIn('equations {U 0.3; k 0.3; omega 0.3;}',raw)

    def test_pressure_gradient_candidate_is_the_only_new_native_numerical_delta(self):
        # Complete prior authored scheme contract; no native solver is run.
        previous = (case.base.foam_header('fvSchemes')
            + 'ddtSchemes {default steadyState;}\n'
            + 'gradSchemes {default Gauss linear; grad(U) cellLimited Gauss linear 1;}\n'
            + 'divSchemes {default none; div(phi,U) bounded Gauss upwind; div(phi,k) bounded Gauss upwind; div(phi,omega) bounded Gauss upwind; div((nuEff*dev2(T(grad(U))))) Gauss linear;}\n'
            + 'laplacianSchemes {default Gauss linear limited corrected 0.5;}\n'
            + 'interpolationSchemes {default linear;}\n'
            + 'snGradSchemes {default limited corrected 0.5;}\n'
            + 'wallDist {method meshWave;}\n').encode('ascii')
        for component in ('impeller','volute'):
            with self.subTest(component=component):
                fresh=case.case_files(component)
                self.assertEqual(fresh['system/fvSchemes'].count(b'grad(p) cellLimited Gauss linear 1;'),1)
                self.assertEqual(fresh['system/fvSchemes'].count(b'grad(U) cellLimited Gauss linear 1;'),1)
                self.assertEqual(fresh['system/fvSchemes'].replace(b' grad(p) cellLimited Gauss linear 1;',b''),previous)
                self.assertEqual(fresh['system/controlDict'],case.physics.case_files(component,300000)['system/controlDict'].encode('ascii'))
                self.assertEqual(fresh['0/p'],case.physics.case_files(component,300000)['0/p'].encode('ascii'))
        profile=case.NUMERICAL_PROFILE
        self.assertEqual(profile['id'],'teaching-upwind-pressure-gradient-stabilization-v2')
        self.assertEqual(profile['pressureGradient']['scheme'],'cellLimited Gauss linear 1')
        self.assertEqual(profile['pressureGradient']['limiterCoefficient'],1)
        self.assertFalse(profile['pressureGradient']['nativeFieldClippingPerformed'])
        self.assertFalse(profile['velocityGradientChanged']);self.assertFalse(profile['accuracyQualified'])
        self.assertFalse(profile['convergencePredicted'])
        self.assertEqual((profile['steadyIterationCount'],case.LIMITS['maximumSeconds']),(500,1200))
        self.assertEqual((case.PARAMETERS['speedRpm'],case.PARAMETERS['massFlowKgS']),(1450.,90.))
        self.assertEqual(case.ASSUMPTIONS['externalTotalPressureAbsolutePa'],300000.)

    def test_changed_duplicate_or_predeclared_pressure_gradient_is_not_silently_overridden(self):
        original=case.physics.case_files
        old='gradSchemes {default Gauss linear; grad(U) cellLimited Gauss linear 1;}'
        variants=[old.replace('default Gauss linear','default leastSquares'),
            old.replace('grad(U) cellLimited Gauss linear 1','grad(U) cellLimited Gauss linear 0.5'),
            old+'\ngradSchemes {default Gauss linear;}',
            old+'\ngrad(p) Gauss linear;',
            old.replace('}', ' grad(p) cellLimited Gauss linear 1;}')]
        for variant in variants:
            with self.subTest(variant=variant):
                def changed(component,pressure):
                    result=original(component,pressure)
                    result['system/fvSchemes']=result['system/fvSchemes'].replace(old,variant)
                    return result
                with mock.patch.object(case.physics,'case_files',side_effect=changed),self.assertRaisesRegex(case.BaselineCaseIssue,'gradient block'):
                    case.case_files('impeller')

    def test_unreviewed_upstream_scheme_is_rejected_before_numerical_override(self):
        original=case.physics.case_files
        def changed(component,pressure):
            result=original(component,pressure)
            result['system/fvSchemes']=result['system/fvSchemes'].replace('div(phi,k) bounded Gauss limitedLinear 1;','div(phi,k) Gauss linear;')
            return result
        with mock.patch.object(case.physics,'case_files',side_effect=changed),self.assertRaises(case.BaselineCaseIssue):case.case_files('impeller')

    def fixture(self,root):
        with self.environment(str(root)):
            # The synthetic geometry producer verifier is intentionally mocked;
            # all source/native topology, logs, counters and SHA gates are real.
            source,geometry_module=source_fixture(root);source.rename(root/'unused')
            with mock.patch.dict(sys.modules,{'teaching_pump_geometry':geometry_module}):return native_source(root)

    def test_actual_source_bytes_topology_logs_and_coverage_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry_module=self.fixture(root)
            with self.environment(temp),mock.patch.dict(sys.modules,{'teaching_pump_geometry':geometry_module}),mock.patch.object(case,'validate_runtime'):
                result=case.validate_source(source);self.assertEqual([r['cellCount'] for r in result['components']],[6,6]);self.assertEqual(len(result['snapshots']),12)
                self.assertTrue(result['mergedNativeMeshEvidence']['coupledInterfaceCoverageVerified'])

    def test_transaction_reuses_same_current_path_proof_and_returns_independent_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry_module=self.fixture(root)
            with self.environment(temp),mock.patch.dict(sys.modules,{'teaching_pump_geometry':geometry_module}),\
                 mock.patch.object(case,'validate_runtime') as runtime,\
                 mock.patch.object(case,'_validate_source',wraps=case._validate_source) as replay,\
                 mock.patch.object(native_case,'validate_inputs',wraps=native_case.validate_inputs) as geometry_replay:
                with case.SourceProofTransaction(source):
                    first=case.validate_source(source);first['components'][0]['cellCount']=999
                    second=case.validate_source(source)
                    self.assertEqual(second['components'][0]['cellCount'],6)
                    self.assertEqual(replay.call_count,1);self.assertEqual(geometry_replay.call_count,1)
                    self.assertEqual(runtime.call_count,2)
                case.validate_source(source)
                self.assertEqual(replay.call_count,2);self.assertIsNone(case._SOURCE_TRANSACTION.get())

    def test_transaction_rejects_changed_deleted_or_added_source_bytes_without_replay(self):
        for change in ('changed','deleted','added','renamed','empty-directory'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source,geometry_module=self.fixture(root)
                with self.environment(temp),mock.patch.dict(sys.modules,{'teaching_pump_geometry':geometry_module}),\
                     mock.patch.object(case,'validate_runtime'),mock.patch.object(case,'_validate_source',wraps=case._validate_source) as replay:
                    with case.SourceProofTransaction(source):
                        case.validate_source(source);target=source/'logs/00-gmshToFoam.log'
                        if change=='changed':target.write_bytes(target.read_bytes()+b'CHANGED')
                        elif change=='deleted':target.unlink()
                        elif change=='added':(source/'unexpected-data').write_bytes(b'ADDED')
                        elif change=='renamed':target.rename(source/'logs/renamed-log')
                        else:(source/'added-empty-directory').mkdir()
                        with self.assertRaisesRegex(case.BaselineCaseIssue,'inventory changed'):case.validate_source(source)
                        self.assertEqual(replay.call_count,1)

    def test_transaction_never_promotes_another_path_or_leaks_after_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry_module=self.fixture(root);copy=root/'copied-source';shutil.copytree(source,copy)
            with self.environment(temp),mock.patch.dict(sys.modules,{'teaching_pump_geometry':geometry_module}),\
                 mock.patch.object(case,'validate_runtime'),mock.patch.object(case,'_validate_source',wraps=case._validate_source) as replay:
                with case.SourceProofTransaction(source,copy):
                    case.validate_source(source);case.validate_source(copy)
                    self.assertEqual(replay.call_count,2)
                    with self.assertRaisesRegex(case.BaselineCaseIssue,'different/unadmitted'):case.validate_source(root/'another-path')
                self.assertIsNone(case._SOURCE_TRANSACTION.get())

    def test_transaction_rechecks_real_external_runtime_bytes_on_every_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry_module=self.fixture(root);installed=root/'synthetic-installed-binaries';installed.mkdir()
            for name in producer.PROGRAMS:(installed/name).write_bytes(b'native')
            original_read=case.base.read_regular
            def installed_read(path,limit):
                # Explicit synthetic external package storage; production SHA,
                # schema and exact /opt identity validator itself is unchanged.
                return original_read(installed/Path(path).name if Path(path).as_posix().startswith('/opt/openfoam12/') else path,limit)
            with self.environment(temp),mock.patch.dict(sys.modules,{'teaching_pump_geometry':geometry_module}),\
                 mock.patch.object(case.base,'read_regular',side_effect=installed_read):
                with case.SourceProofTransaction(source):
                    case.validate_source(source);(installed/'checkMesh').write_bytes(b'CHANGED')
                    with self.assertRaisesRegex(case.BaselineCaseIssue,'Installed native source binary bytes changed'):case.validate_source(source)

    def test_transaction_detects_mutation_between_boundary_hashes_and_resets_on_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry_module=self.fixture(root)
            with self.environment(temp),mock.patch.dict(sys.modules,{'teaching_pump_geometry':geometry_module}),mock.patch.object(case,'validate_runtime'):
                with case.SourceProofTransaction(source):
                    case.validate_source(source)
                    def mutate(_):(source/'logs/00-gmshToFoam.log').write_bytes(b'MUTATED DURING REUSE')
                    with mock.patch.object(case,'validate_runtime',side_effect=mutate),self.assertRaisesRegex(case.BaselineCaseIssue,'during the transaction boundary'):
                        case.validate_source(source)
                with self.assertRaises(case.BaselineDeadlineIssue):
                    with case.SourceProofTransaction(source,deadline=case.time.monotonic()+10):
                        with mock.patch.object(case.time,'monotonic',return_value=float('inf')):case.validate_source(source)
                self.assertIsNone(case._SOURCE_TRANSACTION.get())

    def test_prepare_and_replay_preserve_source_and_author_fresh_uniform_physics(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry_module=self.fixture(root)
            with self.environment(temp),mock.patch.dict(sys.modules,{'teaching_pump_geometry':geometry_module}),mock.patch.object(case,'validate_runtime'):
                output=root/'baseline';prepared=case.prepare(source,output);self.assertEqual(case.verify_output(output)['report'],prepared)
                self.assertTrue(all(prepared[k] is False for k in case.FALSE_FLAGS));self.assertEqual(len(prepared['initialCaseMeshFiles']),12)
                self.assertFalse(prepared['baselineFieldsCopied']);self.assertTrue(prepared['sourceNativeGeometryQualityVerified'])
                self.assertEqual(prepared['classroomAssumptions']['numericalStabilization'],case.NUMERICAL_PROFILE)
                self.assertEqual(prepared['classroomAssumptions']['outletVelocityProfile'],case.outlet.PROFILE)
                self.assertTrue(all(r['path'].startswith('source-teaching/') for r in prepared['outletProfileEvidence']['nativeMeshFileRecords']))
                (output/'impeller-case/constant/MRFProperties').write_bytes(b'MUTATED')
                with self.assertRaises(case.BaselineCaseIssue):case.verify_output(output)

    def test_false_quality_missing_stage_or_changed_log_cannot_author_case(self):
        for kind in ('falseQuality','missingStage','changedLog','changedSnapshot','wrongOperatingInput','fakeBaseline'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source,geometry_module=self.fixture(root);report_path=source/producer.REPORT;packet=json.loads(report_path.read_bytes())
                if kind=='falseQuality':packet['nativeGeometryQualityVerified']=False
                if kind=='fakeBaseline':packet['qualifiedBaseline']=True
                if kind=='missingStage':packet['stages'].pop()
                if kind=='changedLog':(source/'logs/08-checkMesh.log').write_text('CORRUPTED')
                if kind=='changedSnapshot':(source/'component-mesh-snapshots/impeller/constant/polyMesh/points').write_text('CORRUPTED')
                if kind=='wrongOperatingInput':
                    path=source/'source-geometry/teaching-geometry.json';geometry_packet=json.loads(path.read_bytes());geometry_packet['parameters']['speedRpm']=1800;path.write_bytes(case.encoded(geometry_packet))
                report_path.write_bytes(case.encoded(packet))
                with self.environment(temp),mock.patch.dict(sys.modules,{'teaching_pump_geometry':geometry_module}),mock.patch.object(case,'validate_runtime'):
                    with self.assertRaises(Exception):case.prepare(source,root/'baseline')
                    self.assertFalse((root/'baseline').exists())

    def test_runtime_binaries_must_match_all_five_exact_package_roles(self):
        identity={'schemaVersion':producer.RUNTIME_SCHEMA,'openFoamFoundationVersion':12,'packageVersion':'20250206','officialDebSha256':case.safety.PACKAGE_SHA,
            'expectedSourceCommit':case.safety.SOURCE_COMMIT,'mainApplicationWrites':False,'binaries':[{'program':name,'path':'/opt/openfoam12/bin/'+name,'bytes':6,'sha256':sha256(b'native').hexdigest(),
            'packageOwnership':'openfoam12: /opt/openfoam12/bin/'+name} for name in producer.PROGRAMS]}
        with mock.patch.object(case.base,'read_regular',return_value=b'native'):
            case.validate_runtime(identity)
            identity['binaries'][0]['sha256']='a'*64
            with self.assertRaises(case.BaselineCaseIssue):case.validate_runtime(identity)

    def test_source_full_inventory_race_during_copy_cannot_accept_preparation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry_module=self.fixture(root);write=case.base.write_exclusive;changed=False
            def copying(path,raw):
                nonlocal changed
                write(path,raw)
                if not changed and 'source-teaching' in str(path):changed=True;(source/'logs/08-checkMesh.log').write_text('MUTATED')
            with self.environment(temp),mock.patch.dict(sys.modules,{'teaching_pump_geometry':geometry_module}),mock.patch.object(case,'validate_runtime'),mock.patch.object(case.base,'write_exclusive',side_effect=copying):
                with self.assertRaises(Exception):case.prepare(source,root/'baseline')
                self.assertFalse((root/'baseline').exists())

    def test_postncc_keeps_every_physical_bc_and_only_native_empty_default(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry_module=self.fixture(root)
            with self.environment(temp),mock.patch.dict(sys.modules,{'teaching_pump_geometry':geometry_module}),mock.patch.object(case,'validate_runtime'):
                output=root/'baseline';case.prepare(source,output)
                # This test models only the reviewed field serialization; native
                # execution and mesh acceptance remain absent in the preparation.
                for folder in ('impeller-case','volute-case'):
                    path=output/folder/'0/nut';raw=path.read_bytes().replace(b'"defaultFaces"\n    {\n        type calculated; value uniform 0;',b'"defaultFaces"\n    {\n        type calculated; value nonuniform List<scalar> 0();');path.write_bytes(raw)
                case.verify_output(output,after_native_ncc=True)
                path=output/'impeller-case/0/U';raw=path.read_bytes();path.write_bytes(raw.replace(case.outlet.uniform_outlet_body(),b'type zeroGradient;'))
                with self.assertRaises(case.BaselineCaseIssue):case.verify_output(output,after_native_ncc=True)

    def test_native_function1_and_sst_defaults_preserve_exact_physics(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry_module=self.fixture(root)
            with self.environment(temp),mock.patch.dict(sys.modules,{'teaching_pump_geometry':geometry_module}),mock.patch.object(case,'validate_runtime'):
                output=root/'baseline';case.prepare(source,output)
                for folder in ('impeller-case','volute-case'):
                    # Native fixedValue serializes this exact prescribed vector;
                    # the rejected old outlet Function1 is no longer authored.
                    path=output/folder/'0/U';raw=path.read_bytes();self.assertIn(case.outlet.uniform_outlet_body(),raw)
                    path=output/folder/'0/omega';raw=path.read_bytes();path.write_bytes(raw.replace(b'type omegaWallFunction;',b'type omegaWallFunction; beta1 0.075; blended false;'))
                    path=output/folder/'0/nut';raw=path.read_bytes();path.write_bytes(raw.replace(b'type nutkWallFunction;',b'type nutkWallFunction; Cmu 0.09; kappa 0.41; E 9.8;'))
                case.verify_output(output,after_native_ncc=True)
                path=output/'impeller-case/0/U';raw=path.read_bytes();path.write_bytes(raw.replace(case.outlet.uniform_outlet_body(),b'type fixedValue; value uniform (0 1 0);'))
                with self.assertRaises(case.BaselineCaseIssue):case.verify_output(output,after_native_ncc=True)

    def test_outlet_geometry_admission_and_bound_evidence_cannot_be_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,geometry_module=self.fixture(root)
            with self.environment(temp),mock.patch.dict(sys.modules,{'teaching_pump_geometry':geometry_module}),mock.patch.object(case,'validate_runtime'):
                with mock.patch.object(case.outlet,'verify_outlet',side_effect=ValueError('Actual outlet normal mismatched')):
                    with self.assertRaisesRegex(ValueError,'normal mismatched'):case.prepare(source,root/'baseline')
                self.assertFalse((root/'baseline').exists())
                output=root/'baseline';case.prepare(source,output)
                packet=json.loads((output/case.REPORT).read_bytes());packet['outletProfileEvidence']['nativeMeshFileRecords'][0]['sha256']='0'*64
                (output/case.REPORT).write_bytes(case.encoded(packet))
                with self.assertRaises(case.BaselineCaseIssue):case.verify_output(output)


if __name__=='__main__':unittest.main()
