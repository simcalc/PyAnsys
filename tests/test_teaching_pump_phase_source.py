"""Synthetic archive/metadata tests; no fixture is actual native CFD evidence."""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import importlib.util
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

spec = importlib.util.spec_from_file_location("teaching_phase_source_tests", Path(__file__).resolve().parents[1]/"tools/teaching_pump_phase_source.py")
tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
RUN, HEAD = "123456789", "a"*40


def record(name, raw): return {"path":name,"bytes":len(raw),"sha256":sha256(raw).hexdigest()}


def info(name, *, mode=stat.S_IFREG|0o600, compression=zipfile.ZIP_DEFLATED):
    value=zipfile.ZipInfo(name);value.filename=name;value.orig_filename=name;value.create_system=3
    value.external_attr=mode<<16;value.compress_type=compression;return value


def phase_files(*, source_extras=None):
    """Only relational byte fixtures. Native/science replay is not mocked here."""
    files={"case/"+name:b"synthetic authored data; no solver execution\n" for name in tool.INPUTS}
    for name in ("points","faces","owner","neighbour","boundary","cellZones"):
        files["case/constant/polyMesh/"+name]=b"synthetic mesh bytes; not actual geometry\n"
    source="source-baseline/teaching-baseline-execution.json";files[source]=b'{"fixture":"not native evidence"}\n'
    files.update(source_extras or {})
    end=format(4*60/1450,".12g")
    for name in ("p","p_rgh","U","alpha.water","k","omega","nut"):
        files[f"case/{end}/{name}"]=b"synthetic endpoint bytes; not measured CFD\n"
    for name in ("logs/00-checkMesh.log","logs/01-foamRun.log"):
        files[name]=b"Synthetic fixture only; no OpenFOAM execution\n"
    prep={"schemaVersion":"untirta.teaching-pump-phase-reference-preparation.v1","sourceGeometryRoute":"CLASSROOM_AUTHORED_GEOMETRY",
        "sourceEvidenceFileRecords":[record(name,raw) for name,raw in sorted(files.items()) if name.startswith('source-baseline/')]}
    files[tool.PREPARATION]=tool.encoded(prep)
    files[tool.RUNTIME]=tool.encoded({"schemaVersion":"untirta.teaching-pump-phase-runtime-identity.v1"})
    files[tool.STUDY]=tool.encoded({"schemaVersion":"untirta.teaching-pump-phase-study.v1"})
    files[tool.IDENTITY]=tool.encoded({"provenance":"CLASSROOM_AUTHORED_GEOMETRY","units":"SI"})
    manifest={"schemaVersion":"untirta.teaching-pump-phase-input-manifest.v1","sourceGeometryRoute":"CLASSROOM_AUTHORED_GEOMETRY",
        "files":[record(name,raw) for name,raw in sorted(files.items()) if not name.startswith("logs/") and f"case/{end}/" not in name]}
    files[tool.MANIFEST]=tool.encoded(manifest)
    execution={"schemaVersion":"untirta.teaching-pump-phase-reference-execution.v1","githubRepository":tool.REPOSITORY,
        "githubRunId":RUN,"githubSourceCommit":HEAD,"status":"TEACHING_ACTIVE_PHASE_REFERENCE_EXECUTED_ACCEPTANCE_REQUIRED",
        "mode":"high-pressure-active-phase-MRF-reference","inletAbsoluteTotalPressurePa":300000,
        **dict.fromkeys(tool.TRUE_NATIVE,True), **dict.fromkeys(("phaseH0Eligible","phaseFamilyReferenceQualified","cavitationSweepExecuted",
            "npsh3Qualified","physicalTransientExecuted","restartVerified","mainApplicationWrites"),False),
        "preparationReportSha256":record(tool.PREPARATION,files[tool.PREPARATION])["sha256"],
        "runtimeIdentitySha256":record(tool.RUNTIME,files[tool.RUNTIME])["sha256"],
        "inputManifestSha256":record(tool.MANIFEST,files[tool.MANIFEST])["sha256"],
        "studySha256":record(tool.STUDY,files[tool.STUDY])["sha256"],
        "finalNativeFieldRecords":[record(name,raw) for name,raw in sorted(files.items()) if f"case/{end}/" in name],
        "retainedNativeLogRecords":[record(name,raw) for name,raw in sorted(files.items()) if name.startswith("logs/")],
        "fixture":"synthetic metadata only; flags are not CFD proof"}
    files[tool.EXECUTION]=tool.encoded(execution)
    acceptance={"schemaVersion":"untirta.teaching-pump-phase-reference-acceptance.v1","status":"AuthoredActivePhaseReferenceNumericallyQualified",
        "sourceGeometryRoute":"CLASSROOM_AUTHORED_GEOMETRY","manifest":manifest,"inputManifest":manifest,"study":tool.json_data(files[tool.STUDY]),
        "nativeInputManifestSha256":record(tool.MANIFEST,files[tool.MANIFEST])["sha256"],
        "nativeEvidenceRecords":[record(name,files[name]) for name in (tool.EXECUTION,tool.MANIFEST)],"diagnostics":[],"activePhaseReferenceHeadM":1.,
        **dict.fromkeys(("actualCfdRunVerified","simulationExecuted","phaseObservationQualified","numericalPhaseReferenceQualified",
            "phaseH0Eligible","interfaceCoverageVerified","endpointGlobalFieldTableConsistencyVerified"),True),
        **dict.fromkeys(("qualifiedBaseline","npsh3Qualified","physicalTransientExecuted","manufacturerPublishedGeometry","mainApplicationWrites"),False)}
    files[tool.ACCEPTANCE]=tool.encoded(acceptance);return files


class PhaseSourceTests(unittest.TestCase):
    @contextmanager
    def context(self):
        with tempfile.TemporaryDirectory() as value, patch.dict(os.environ,{"RUNNER_TEMP":value}): yield Path(value)

    def metadata(self,root,archive):
        repo={"full_name":tool.REPOSITORY,"private":False,"visibility":"public"}
        run={"id":int(RUN),"head_sha":HEAD,"path":tool.WORKFLOW,"event":"workflow_dispatch","status":"completed","conclusion":"success",
            "repository":repo,"head_repository":repo}
        jobs={"total_count":1,"jobs":[{"id":654321,"run_id":int(RUN),"head_sha":HEAD,"name":tool.JOB,"labels":["ubuntu-24.04"],
            "status":"completed","conclusion":"success","steps":[{"number":i,"name":name,"status":"completed","conclusion":"success"}
                for i,name in enumerate(tool.STEPS,1)]}]}
        artifacts={"artifacts":[{"id":7654321,"name":tool.ARTIFACT_PREFIX+RUN,"expired":False,"size_in_bytes":archive.stat().st_size,
            "workflow_run":{"id":int(RUN)},"digest":"sha256:"+sha256(archive.read_bytes()).hexdigest()}]}
        paths=[]
        for name,packet in (("run",run),("jobs",jobs),("artifacts",artifacts)):
            path=root/(name+".json");path.write_bytes(tool.encoded(packet));paths.append(path)
        return paths

    def fixture(self,root,*,extras=(),files=None):
        archive=root/"phase.zip";files=phase_files() if files is None else files
        with warnings.catch_warnings():
            warnings.simplefilter("ignore",UserWarning)
            with zipfile.ZipFile(archive,"w") as value:
                for name,raw in files.items(): value.writestr(info(name),raw)
                for name,raw in extras:value.writestr(info(name) if isinstance(name,str) else name,raw)
        return (archive,*self.metadata(root,archive),root/"transferred")

    def extract(self,args):return tool.extract(*args,source_run_id=RUN,source_commit=HEAD)

    def rejected(self,args):
        with self.assertRaises((ValueError,zipfile.BadZipFile,RuntimeError,OSError,UnicodeError,KeyError)):self.extract(args)
        self.assertFalse(args[-1].exists(),"Rejected attempts retain no output tree.")

    def test_success_preserves_every_byte_and_sidecar_never_promotes_source_flags(self):
        with self.context() as root:
            args=self.fixture(root);before=[sha256(p.read_bytes()).hexdigest() for p in args[:-1]]
            result=self.extract(args);output=args[-1]
            self.assertTrue(result["artifactBytesVerified"]);self.assertTrue(result["sourceH0MetadataBound"])
            self.assertTrue(all(result[name] is False for name in tool.FALSE_TRANSFER))
            self.assertEqual(result["archiveExpandedBytes"],sum(len(v) for v in phase_files().values()))
            for name,raw in phase_files().items():self.assertEqual((output/tool.SOURCE_DIRECTORY/name).read_bytes(),raw)
            self.assertFalse((output/tool.SOURCE_DIRECTORY/tool.REPORT).exists())
            self.assertEqual(before,[sha256(p.read_bytes()).hexdigest() for p in args[:-1]])

    def test_exact_run_head_workflow_public_repository_and_success_are_required(self):
        mutations=[("head_sha","b"*40),("path",".github/workflows/other.yml"),("event","push"),("status","in_progress"),
            ("conclusion","failure"),("id",True),("repository",{"full_name":tool.REPOSITORY,"private":True}),
            ("head_repository",{"full_name":"other/PyAnsys","private":False})]
        for key,value in mutations:
            with self.subTest(key=key),self.context() as root:
                args=self.fixture(root);packet=tool.json_data(args[1].read_bytes());packet[key]=value;args[1].write_bytes(tool.encoded(packet));self.rejected(args)
        with self.context() as root:
            args=self.fixture(root)
            with self.assertRaises(ValueError):tool.extract(*args,source_run_id=RUN,source_commit="branch-main")
            self.assertFalse(args[-1].exists())

    def test_standard_job_full_successful_numbered_prefix_and_reducer_step_are_required(self):
        for change in ("runner","name","head","missing","duplicate","number","skipped-reducer","failed-step","total"):
            with self.subTest(change=change),self.context() as root:
                args=self.fixture(root);packet=tool.json_data(args[2].read_bytes());job=packet["jobs"][0]
                if change=="runner":job["labels"]=["self-hosted","linux"]
                elif change=="name":job["name"]="other job"
                elif change=="head":job["head_sha"]="b"*40
                elif change=="missing":job["steps"].pop(0)
                elif change=="duplicate":job["steps"].append(deepcopy(job["steps"][0]))
                elif change=="number":job["steps"][12]["number"]=29
                elif change=="skipped-reducer":job["steps"][12]["conclusion"]="skipped"
                elif change=="failed-step":job["steps"][5]["conclusion"]="failure"
                else:packet["total_count"]=2
                args[2].write_bytes(tool.encoded(packet));self.rejected(args)

    def test_artifact_name_expiry_size_digest_duplicate_and_same_run_are_checked(self):
        for key,value in (("name","authored-teaching-pump-baseline-"+RUN),("expired",True),("size_in_bytes",1),
            ("digest","sha256:"+"0"*64),("workflow_run",{"id":987654}),("id",True),("digest",None)):
            with self.subTest(key=key),self.context() as root:
                args=self.fixture(root);packet=tool.json_data(args[3].read_bytes());packet["artifacts"][0][key]=value;args[3].write_bytes(tool.encoded(packet));self.rejected(args)
        with self.context() as root:
            args=self.fixture(root);packet=tool.json_data(args[3].read_bytes());packet["artifacts"]*=2;args[3].write_bytes(tool.encoded(packet));self.rejected(args)

    def test_json_duplicates_nonfinite_and_overflow_are_rejected_before_output(self):
        for insert in (b'"id":123456789,',b'"unknown":NaN,',b'"unknown":1e999,'):
            with self.subTest(insert=insert),self.context() as root:
                args=self.fixture(root);args[1].write_bytes(args[1].read_bytes().replace(b'{',b'{'+insert,1));self.rejected(args)

    def test_archive_paths_modes_compression_aliases_and_code_roles_fail(self):
        bad=[("/absolute",b"x"),("case/../out",b"x"),("case\\out",b"x"),("case/CON",b"x"),("case/.env",b"x"),
            ("case/a//b",b"x"),("case/a./x",b"x"),("unknown.txt",b"x"),("case/evil.py",b"x"),
            (info("case/link",mode=stat.S_IFLNK|0o777),b"x"),(info("case/device",mode=stat.S_IFCHR|0o600),b"x"),
            (info("case/executable",mode=stat.S_IFREG|0o700),b"x"),(info("case/bzip",compression=zipfile.ZIP_BZIP2),b"x"),
            (info("unknown-directory/",mode=stat.S_IFDIR|0o755),b"")]
        for entry in bad:
            with self.subTest(entry=str(entry[0])),self.context() as root:self.rejected(self.fixture(root,extras=[entry]))
        for entries in ([('case/0/k',b"duplicate")],[('Case/something',b"case alias")],
            [('case',b"file-parent")],[('other/X',b"a"),('other/x',b"b")]):
            with self.subTest(entries=entries),self.context() as root:self.rejected(self.fixture(root,extras=entries))

    def test_inner_run_head_schema_and_bound_bytes_cannot_borrow_another_run(self):
        for key,value in (("githubRunId","987654"),("githubSourceCommit","b"*40),("schemaVersion","old-fluent-schema"),
            ("cavitationModelExecuted",False),("phaseH0Eligible",True),("status","failure")):
            with self.subTest(key=key),self.context() as root:
                files=phase_files();raw=tool.json_data(files[tool.EXECUTION]);raw[key]=value;files[tool.EXECUTION]=tool.encoded(raw);self.rejected(self.fixture(root,files=files))
        with self.context() as root:
            files=phase_files();files['case/0/k']+=b' changed';self.rejected(self.fixture(root,files=files))

    def test_partial_or_relabelled_h0_metadata_and_missing_inventory_roles_fail(self):
        for change in ("status","h0","geometry","manifest","missing-field","missing-source","missing-control"):
            with self.subTest(change=change),self.context() as root:
                files=phase_files();packet=tool.json_data(files[tool.ACCEPTANCE],tool.MAX_ACCEPTANCE_BYTES)
                if change=="status":packet["status"]="PhaseNumericalAcceptanceRequired"
                elif change=="h0":packet["phaseH0Eligible"]=False
                elif change=="geometry":packet["sourceGeometryRoute"]="manufacturer geometry"
                elif change=="manifest":packet["manifest"]={}
                elif change=="missing-field":files.pop(next(n for n in files if n.startswith("case/0.165") and n.endswith('/p')))
                elif change=="missing-source":files.pop("source-baseline/teaching-baseline-execution.json")
                else:files.pop("case/constant/fvModels")
                files[tool.ACCEPTANCE]=tool.encoded(packet);self.rejected(self.fixture(root,files=files))

    def test_known_additional_native_state_is_data_only_and_never_zero_pressure(self):
        native=b'FoamFile {format ascii; class volScalarField; location "0.1"; object rAU;}\ndimensions [-1 3 1 0 0 0 0]; internalField uniform 1; boundaryField {}\n'
        with self.context() as root:
            args=self.fixture(root,extras=[("case/0.1/rAU",native)]);result=self.extract(args)
            self.assertEqual((args[-1]/tool.SOURCE_DIRECTORY/'case/0.1/rAU').read_bytes(),native)
            self.assertFalse(result["currentSourceH0Verified"])
        for name,raw in (("case/0/p",native),("case/0.1/unknown",native),("case/999/rAU",native),
            ("case/0.1/rAU",native.replace(b'volScalarField',b'dictionary')),("case/0.1/rAU",native+b'#include secret\n')):
            with self.subTest(name=name),self.context() as root:self.rejected(self.fixture(root,extras=[(name,raw)]))

    def test_exact_source_baseline500_uf_replays_actual_face_count_role_dimensions_and_bytes(self):
        name='source-baseline/impeller-case/500/Uf';neighbour='source-baseline/impeller-case/constant/polyMesh/neighbour'
        mesh=b'FoamFile {format ascii; class labelList; object neighbour;}\n2 (0 1)\n'
        raw=b'FoamFile {format ascii; class surfaceVectorField; location "500"; object Uf;}\ndimensions [0 1 -1 0 0 0 0]; internalField nonuniform List<vector> 2 ((1 0 0)(0 1 0)); boundaryField {}\n'
        with self.context() as root:
            args=self.fixture(root,files=phase_files(source_extras={name:raw,neighbour:mesh}));result=self.extract(args)
            self.assertEqual((args[-1]/tool.SOURCE_DIRECTORY/name).read_bytes(),raw)
            self.assertFalse(result['currentSourceH0Verified']);self.assertTrue(tool.bound_role(name))
            self.assertFalse(tool.bound_role('source-baseline/impeller-case/0/Uf'))
        for old,new in ((b'surfaceVectorField',b'volVectorField'),(b'[0 1 -1 0 0 0 0]',b'[]'),(b'List<vector> 2',b'List<vector> 3'),
            (b'(1 0 0)',b'(1 0)'),(b'(1 0 0)',b'(1e500 0 0)'),(b'location "500"',b'location "0"'),
            (b'boundaryField {}',b'boundaryField {inlet {type codedFixedValue; code { execute(); };}}'),
            (b'boundaryField {}',b'boundaryField {inlet {type codedMixed;}}'),(b'boundaryField {}',b'boundaryField {}\x00')):
            with self.subTest(change=new),self.context() as root:
                args=self.fixture(root,files=phase_files(source_extras={name:raw.replace(old,new),neighbour:mesh}));self.rejected(args)

    def test_sha_binding_cannot_invent_unknown_native_or_source_roles(self):
        for name in ('case/system/evilDict','source-baseline/unknown.txt','case/0/p'):
            with self.subTest(name=name),self.context() as root:
                files=phase_files();files[name]=b'unknown data';manifest=tool.json_data(files[tool.MANIFEST]);manifest['files'].append(record(name,files[name]))
                files[tool.MANIFEST]=tool.encoded(manifest)
                # Direct contract probe isolates role acceptance from later packet SHA checks.
                self.assertFalse(tool.bound_role(name))
                self.rejected(self.fixture(root,files=files))

    def test_crc_all_members_is_checked_before_any_output_directory(self):
        with self.context() as root:
            args=self.fixture(root)
            with zipfile.ZipFile(args[0]) as archive:member=archive.getinfo('case/constant/polyMesh/points')
            raw=bytearray(args[0].read_bytes());fn,extra=struct.unpack_from('<HH',raw,member.header_offset+26)
            raw[member.header_offset+30+fn+extra+member.compress_size//2]^=0x20;args[0].write_bytes(raw);self.metadata(root,args[0]);self.rejected(args)

    def test_all_budgets_are_independent_and_executable_preamble_fails(self):
        for key,value in (("MAX_ARCHIVE_BYTES",1),("MAX_EXPANDED_BYTES",1),("MAX_FILE_BYTES",1),("MAX_ENTRIES",1),("MAX_REPORT_BYTES",1)):
            with self.subTest(key=key),self.context() as root:
                args=self.fixture(root)
                with patch.object(tool,key,value):self.rejected(args)
        with self.context() as root:
            args=self.fixture(root);args[0].write_bytes(b'MZexecutable'+args[0].read_bytes());self.metadata(root,args[0]);self.rejected(args)

    def test_metadata_archive_or_copied_data_mutation_cleans_owned_output_only(self):
        for change in ('metadata','archive','copied'):
            with self.subTest(change=change),self.context() as root:
                args=self.fixture(root);sentinel=root/'keep.txt';sentinel.write_bytes(b'keep');real=tool.archive_digest;calls=[]
                def mutated(stream):
                    value=real(stream);calls.append(1)
                    # First call wholeZIP; later calls copied file SHA then final originalZIP.
                    if len(calls)==2:
                        if change=='metadata':args[1].write_bytes(b'changed')
                        elif change=='archive':args[0].write_bytes(b'changed')
                        else:return value[0],'0'*64
                    return value
                with patch.object(tool,'archive_digest',mutated):self.rejected(args)
                self.assertEqual(sentinel.read_bytes(),b'keep')

    def test_existing_output_external_path_or_symlink_ancestor_is_not_touched(self):
        with self.context() as root:
            args=self.fixture(root);args[-1].mkdir();keep=args[-1]/'keep';keep.write_bytes(b'preserve')
            with self.assertRaises(ValueError):self.extract(args)
            self.assertEqual(keep.read_bytes(),b'preserve')
            with self.assertRaises(ValueError):self.extract((*args[:-1],root.parent/'outside-phase-source'))
            original=Path.is_symlink
            with patch.object(Path,'is_symlink',lambda p:p==args[0] or original(p)),self.assertRaises(ValueError):self.extract((*args[:-1],root/'unused'))


if __name__=='__main__':unittest.main()
