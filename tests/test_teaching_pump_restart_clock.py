"""Synthetic native-format clock facts; no solver or matched restart claims."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location("teaching_restart_clock_tests",Path(__file__).resolve().parents[1]/"tools/teaching_pump_restart_clock.py")
tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)


def native_time(name="0.5",index=90,dt=.02,dt0=.01,begin=0):
    return (f'/* Native writer shape, synthetic values */\nFoamFile\n{{format ascii;class dictionary;location "{name}/uniform";object time;}}\n'
        f'beginTime {begin:.17g};value {name};name "{name}";index {index};deltaT {dt:.17g};deltaT0 {dt0:.17g};\n').encode()


def controls(adaptive=False,dt=.01,end=.52):
    return (f'FoamFile {{format ascii;class dictionary;location "system";object controlDict;}}\n'
        f'application foamRun;solver incompressibleVoF;startFrom startTime;startTime .5;stopAt endTime;endTime {end:.17g};'
        f'deltaT {dt:.17g};adjustTimeStep {"yes" if adaptive else "no"};runTimeModifiable false;timeFormat general;timePrecision 17;'
        'functions { sample {type volFieldValue;libs ("libfieldFunctionObjects.so");operation min;fields (p);} }\n').encode()


def log(times=(.51,.52),deltas=None):
    rows=['Build : 12-86e126a7bc4d\nCreate mesh for time = 0.5\nSelecting solver incompressibleVoF\nStarting time loop\n']
    for i,time in enumerate(times):
        if deltas is not None:rows.append(f'deltaT = {deltas[i]:.17g}\n')
        rows.append(f'Time = {time:.17g}s\nPIMPLE: iteration 1\nExecutionTime = {i+1} s  ClockTime = {i+1} s\n')
    return (''.join(rows)+'End\n').encode()


def write(root,path,raw):
    raw=raw.encode() if isinstance(raw,str) else raw;p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    return {"path":path,"bytes":len(raw),"sha256":sha256(raw).hexdigest()}


def fixture(root,adaptive=False):
    for role,(kind,obj) in tool.ROLES.items():
        if role not in tool.REQUIRED_STATE:continue
        loc='0.5'+('/'+role.rsplit('/',1)[0] if '/' in role else '')
        raw=native_time() if role=='uniform/time' else f'FoamFile {{format ascii;class {kind};location "{loc}";object {obj};}}\nSYNTHETIC HEADER ONLY;\n'.encode()
        write(root,'case/0.5/'+role,raw)
    endpoint=native_time('0.52',92,.01,.01)
    ctr=write(root,'case/system/controlDict',controls(adaptive))
    logfile=write(root,'logs/01-foamRun.log',log(deltas=(.01,.01) if adaptive else None))
    end=write(root,'case/0.52/uniform/time',endpoint)
    identity=write(root,'teaching-geometry-identity.json',tool.encoded({'schemaVersion':'untirta.classroom-authored-pump-geometry-identity.v1',
        'provenance':'CLASSROOM_AUTHORED_GEOMETRY','units':'SI','manufacturerIdentity':None,'originalFluentSourceIdentity':None}))
    rows,_=tool.inventory(root,'case/0.5')
    return {'schema':tool.INPUT_SCHEMA,'expectedSourceCommit':tool.SOURCE_COMMIT,'checkpointDirectory':'case/0.5',
        'checkpointInventory':rows,'checkpointInventorySha256':sha256(tool.encoded(rows)).hexdigest(),
        'controlDictFile':ctr,'nativeLogFile':logfile,'endpointTimeStateFile':end,'sourceGeometryIdentityFile':identity}


class SeededClockTests(unittest.TestCase):
    def parse(self,rawlog=None,rawcontrol=None,rawend=None,rawseed=None):
        seed=tool.parse_time_state(rawseed or native_time(),'0.5')
        ctr=tool.parse_controls(rawcontrol or controls(),seed)
        end=tool.parse_time_state(rawend or native_time('0.52',92,.01,.01),'0.52')
        return tool.parse_resumed_log(rawlog or log(),seed,ctr,end)

    def test_fixed_no_printed_dt_uses_actual_time_increments_and_seed_index(self):
        rows=self.parse();self.assertEqual([r['index'] for r in rows],[91,92]);self.assertEqual(rows[0]['physicalTimeS'],.51)
        self.assertFalse(rows[0]['printedDeltaT']);self.assertAlmostEqual(rows[0]['deltaT0S'],.01)
        # A different stored checkpoint dt is legitimate fixed-clock fact,
        # and does not confer matched restart equivalence.
        self.assertNotEqual(tool.parse_time_state(native_time(),'0.5')['deltaTS'],rows[0]['deltaT0S'])

    def test_native_header_sixkeys_location_quoted_name_index_types_fail_closed(self):
        raw=native_time();self.assertEqual(tool.parse_time_state(raw,'0.5')['index'],90)
        changes=((b'index 90;',b'index true;'),(b'index 90;',b'index 90.0;'),(b'index 90;',b'index -1;'),
            (b'name "0.5";',b'name "0.50";'),(b'0.5/uniform',b'0.5'),(b'format ascii',b'format binary'),
            (b'class dictionary',b'class labelList'),(b'object time',b'object U'),(b'index 90;',b'index 90;index 91;'),
            (b'value 0.5;',b'value .6;'),(b'beginTime 0;',b'beginTime 1;'),(b'index 90;',b'index 2147483648;'),
            (b'deltaT 0.02;',b'deltaT 0;'),(b'deltaT0 0.01;',b'deltaT0 -1;'))
        for old,new in changes:
            with self.subTest(change=new),self.assertRaises(tool.ClockIssue):tool.parse_time_state(raw.replace(old,new),'0.5')
        for added in (b'#include foo',b'\x00',b'code {};',b'codedFixedValue {};',b'extra 1;'):
            with self.subTest(added=added),self.assertRaises(tool.ClockIssue):tool.parse_time_state(raw+added,'0.5')

    def test_adaptive_dt_print_and_previous_dt0_seed_effective_dt(self):
        seed=tool.parse_time_state(native_time(dt=.02,dt0=.007),'0.5');ctr=tool.parse_controls(controls(True,dt=.1,end=.515),seed)
        end=tool.parse_time_state(native_time('0.515',92,.005,.01),'0.515')
        rows=tool.parse_resumed_log(log((.51,.515),(.01,.005)),seed,ctr,end)
        self.assertAlmostEqual(rows[0]['deltaT0S'],.02);self.assertAlmostEqual(rows[1]['deltaT0S'],.01)
        self.assertNotEqual(rows[0]['deltaT0S'],seed['deltaT0S'])
        with self.assertRaisesRegex(tool.ClockIssue,'ADAPTIVE_DT'):tool.parse_resumed_log(log((.51,.515)),seed,ctr,end)

    def test_fixed_optional_dt_must_agree_and_effective_initial_dt0_endpoint(self):
        self.assertEqual(len(self.parse(log(deltas=(.01,.01)))),2)
        with self.assertRaisesRegex(tool.ClockIssue,'INCREMENT'):self.parse(log(deltas=(.02,.01)))
        seed=tool.parse_time_state(native_time(),'0.5');ctr=tool.parse_controls(controls(end=.51),seed)
        end=tool.parse_time_state(native_time('0.51',91,.01,.01),'0.51')
        tool.parse_resumed_log(log((.51,)),seed,ctr,end)
        with self.assertRaisesRegex(tool.ClockIssue,'ENDPOINT'):tool.parse_resumed_log(log((.51,)),seed,ctr,dict(end,deltaT0S=.02))

    def test_missing_rows_endpoint_index_catches_adaptive_coalescing(self):
        seed=tool.parse_time_state(native_time(),'0.5');ctr=tool.parse_controls(controls(True),seed)
        endpoint=tool.parse_time_state(native_time('0.52',92,.02,.02),'0.52')
        with self.assertRaisesRegex(tool.ClockIssue,'ENDPOINT'):
            tool.parse_resumed_log(log((.52,),(.02,)),seed,ctr,endpoint)

    def test_clock_order_partial_rows_fatal_literal_and_trailing_rows_rejected(self):
        good=log()
        bad=(log((.51,.51)),log((.52,.51)),good.replace(b'ExecutionTime = 1 s  ClockTime = 1 s\n',b''),
            good.replace(b'End\n',b''),good+b'Time = .53s\n',re.sub(rb'Time = [^\n]+',b'Time = nan',good,count=1),
            good+b'FOAM FATAL ERROR\n',good.replace(b'End\n',b'deltaT = .01\nEnd\n'),
            good.replace(b'Starting time loop',b'Starting time loop\nStarting time loop'),
            good.replace(b'Create mesh for time = 0.5',b'Create mesh for time = 0'))
        for raw in bad:
            with self.subTest(raw=raw[-100:]),self.assertRaises(tool.ClockIssue):self.parse(raw)

    def test_controls_require_si_immutable_explicit_seed_and_native_family(self):
        seed=tool.parse_time_state(native_time(),'0.5');raw=controls()
        changes=((b'adjustTimeStep no;',b'adjustTimeStep maybe;'),(b'startTime .5;',b'startTime 0;'),
            (b'runTimeModifiable false;',b'runTimeModifiable true;'),(b'solver incompressibleVoF;',b'solver incompressibleFluid;'),
            (b'timePrecision 17;',b'timePrecision 6;'),(b'deltaT 0.01;',b'deltaT 0;'),
            (b'startFrom startTime;',b'startFrom latestTime;'),(b'timeFormat general;',b'timeFormat fixed;'))
        for old,new in changes:
            with self.subTest(change=new),self.assertRaises(tool.ClockIssue):tool.parse_controls(raw.replace(old,new),seed)
        with self.assertRaisesRegex(tool.ClockIssue,'SI_SECONDS'):tool.parse_controls(raw+b'userTime {type engine;rpm 1450;}',seed)

    def test_full_current_inventory_and_facts_preserve_global_time_false_qualification(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);packet=fixture(root);out=tool.inspect(root,packet)
            self.assertEqual(out['nativeStepCount'],2);self.assertEqual(out['endpoint']['index'],92)
            self.assertEqual(out['checkpointRoleCount'],len(tool.REQUIRED_STATE));self.assertFalse(out['globalTimeRebased'])
            self.assertTrue(out['nativeClockVerified']);self.assertFalse(out['storedDeltaTOverridesControl'])
            self.assertAlmostEqual(out['equationStages']['substitution']['sumObservedDeltaTS'],.02)
            for flag in tool.FALSE_FLAGS:self.assertIs(out[flag],False)
            self.assertLess(len(tool.encoded(out)),tool.MAX_JSON)

    def test_missing_state_unknown_role_byte_mutation_or_name_alias_rejects(self):
        for change in ('missing','unknown','mutate','alias','metadata','old-source'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);p=fixture(root)
                if change=='missing':(root/'case/0.5/meshPhi').unlink()
                elif change=='unknown':write(root,'case/0.5/U_0',b'unknown')
                elif change=='mutate':(root/'case/0.5/p').write_bytes(b'mutated')
                elif change=='alias':(root/'case/0.50').mkdir()
                elif change=='metadata':p['checkpointInventorySha256']='a'*64
                else:p['sourceGeometryIdentityFile']=write(root,'teaching-geometry-identity.json',b'{"schema":"original-source"}')
                with self.assertRaises(tool.ClockIssue):tool.inspect(root,p)

    def test_role_header_mutation_rehashed_inventory_still_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=fixture(root);path=root/'case/0.5/U';path.write_bytes(path.read_bytes().replace(b'class volVectorField',b'class volScalarField'))
            p['checkpointInventory'],_=tool.inventory(root,'case/0.5');p['checkpointInventorySha256']=sha256(tool.encoded(p['checkpointInventory'])).hexdigest()
            with self.assertRaisesRegex(tool.ClockIssue,'ROLE_HEADER'):tool.inspect(root,p)

    def test_current_control_log_endpoint_descriptor_mutation_rejected(self):
        for key in ('controlDictFile','nativeLogFile','endpointTimeStateFile','sourceGeometryIdentityFile'):
            with self.subTest(key=key),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);p=fixture(root);path=root/p[key]['path'];path.write_bytes(path.read_bytes()+b' ')
                with self.assertRaises(tool.ClockIssue):tool.inspect(root,p)

    def test_checkpoint_inventory_race_including_empty_directory_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=fixture(root);real=tool.parse_resumed_log
            def mutate(*args):
                rows=real(*args);(root/'case/0.5/empty').mkdir();return rows
            with patch.object(tool,'parse_resumed_log',side_effect=mutate),self.assertRaisesRegex(tool.ClockIssue,'IMMUTABLE'):
                tool.inspect(root,p)

    def test_safe_paths_typed_descriptor_packet_and_limits_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=fixture(root)
            for path in ('../case/0.5','case\\0.5','C:/case/0.5','case//0.5','/case/0.5'):
                with self.subTest(path=path),self.assertRaises(tool.ClockIssue):tool.safe_path(root,path)
            for key,value in (('bytes',True),('sha256','Z'*64)):
                row=dict(p['nativeLogFile']);row[key]=value
                with self.assertRaises(tool.ClockIssue):tool.read_bound(root,row)
            changed=deepcopy(p);changed['expectedSourceCommit']='a'*40
            with self.assertRaises(tool.ClockIssue):tool.inspect(root,changed)
            with patch.object(tool,'MAX_FILES',2),self.assertRaises(tool.ClockIssue):tool.inspect(root,p)

    def test_parsed_state_boolean_index_or_dt_cannot_be_metadata_clock(self):
        seed=tool.parse_time_state(native_time(),'0.5');ctr=tool.parse_controls(controls(),seed)
        end=tool.parse_time_state(native_time('0.52',92,.01,.01),'0.52')
        for key,value in (('index',True),('deltaTS',True),('valueS',float('nan'))):
            bad=dict(seed);bad[key]=value
            with self.subTest(key=key),self.assertRaises(tool.ClockIssue):tool.parse_resumed_log(log(),bad,ctr,end)
        with self.assertRaises(tool.ClockIssue):tool.parse_resumed_log(log(),seed,dict(ctr,adaptive=0),end)
        with self.assertRaises(tool.ClockIssue):tool.parse_resumed_log(log(),seed,dict(ctr,effectiveInitialDeltaTS=.02),end)

    def test_time_increment_below_global_clock_resolution_is_explicitly_rejected(self):
        seed=tool.parse_time_state(native_time('1000000000',90,.0000002,.0000002),'1000000000')
        end=tool.parse_time_state(native_time('1000000000.0000002',91,.0000002,.0000002),'1000000000.0000002')
        ctr={'startTimeS':1e9,'endTimeS':end['valueS'],'controlDeltaTS':.0000002,'effectiveInitialDeltaTS':.0000002,'adaptive':False}
        raw=b'Build : 12-86e126a7bc4d\nCreate mesh for time = 1000000000\nSelecting solver incompressibleVoF\nStarting time loop\nTime = 1000000000.0000002s\nExecutionTime = 1 s ClockTime = 1 s\nEnd\n'
        with self.assertRaisesRegex(tool.ClockIssue,'RESOLVED_PHYSICAL_INCREMENT'):tool.parse_resumed_log(raw,seed,ctr,end)

    def test_json_duplicates_nonfinite_unknown_initial_state_and_code_reject(self):
        for raw in (b'{"schema":1,"schema":2}',b'{"value":NaN}',b'{"value":1e500}'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):tool.decode_json(raw)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=fixture(root);path=root/'case/0.5/p';path.write_bytes(path.read_bytes()+b'codedFixedValue {}')
            p['checkpointInventory'],_=tool.inventory(root,'case/0.5');p['checkpointInventorySha256']=sha256(tool.encoded(p['checkpointInventory'])).hexdigest()
            with self.assertRaisesRegex(tool.ClockIssue,'DATA_ONLY'):tool.inspect(root,p)

    def test_actual_build_and_selected_solver_must_bind_source_before_loop(self):
        good=log()
        changes=((b'Build : 12-86e126a7bc4d\n',b''),(b'12-86e126a7bc4d',b'13-86e126a7bc4d'),
            (b'12-86e126a7bc4d',b'12-000000000000'),(b'Build : 12-86e126a7bc4d\n',b'Build : 12-86e126a7bc4d\nBuild : 12-86e126a7bc4d\n'),
            (b'Selecting solver incompressibleVoF\n',b''),(b'Selecting solver incompressibleVoF',b'Selecting solver incompressibleFluid'),
            (b'Selecting solver incompressibleVoF\n',b'Selecting solver incompressibleVoF\nSelecting solver incompressibleVoF\n'))
        for old,new in changes:
            with self.subTest(change=new),self.assertRaises(tool.ClockIssue):self.parse(good.replace(old,new))
        late=good.replace(b'Build : 12-86e126a7bc4d\n',b'').replace(b'Starting time loop\n',b'Starting time loop\nBuild : 12-86e126a7bc4d\n')
        with self.assertRaisesRegex(tool.ClockIssue,'PINNED_NATIVE_BUILD'):self.parse(late)

    def test_case_directory_alias_scan_has_a_separate_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=fixture(root)
            for i in range(tool.MAX_FILES*4): (root/'case'/f'not-time-{i}').mkdir()
            with self.assertRaisesRegex(tool.ClockIssue,'BOUNDED_CASE_DIRECTORY'):tool.inspect(root,p)


if __name__=='__main__':unittest.main()
