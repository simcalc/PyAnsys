"""Tiny synthetic transfer/metadata fixtures; none is actual native CFD evidence."""
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import teaching_pump_torque_probe_source as source


def metadata():
    repo={"full_name":source.REPOSITORY,"private":False,"visibility":"public"}
    run={"id":source.RUN_ID,"head_sha":source.HEAD,"path":source.WORKFLOW,"run_attempt":1,
        "event":"workflow_dispatch","status":"completed","conclusion":"failure","repository":repo,"head_repository":repo}
    steps=[{"number":n,"name":name,"status":"completed","conclusion":"failure" if n==10 else "skipped" if n==11 else "success"}
        for n,name in enumerate(source.STEPS,1)]
    jobs={"total_count":1,"jobs":[{"id":source.JOB_ID,"run_id":source.RUN_ID,"head_sha":source.HEAD,
        "name":"FREE authored CAD, native geometry and numerical MRF baseline","labels":["ubuntu-24.04"],"status":"completed","conclusion":"failure","steps":steps}]}
    artifact={"id":source.ARTIFACT_ID,"name":source.ARTIFACT_NAME,"expired":False,"size_in_bytes":source.ARTIFACT_BYTES,
        "digest":"sha256:"+source.ARTIFACT_SHA,"workflow_run":{"id":source.RUN_ID}}
    return run,jobs,artifact


class MetadataTests(unittest.TestCase):
    def test_only_fixed_failed_source_with_skipped_reducer_no_source_promotion(self):
        record=source.validate_metadata(*metadata());self.assertEqual(source.admission(record),record)
        self.assertEqual(record["sourceConclusion"],"failure")
        self.assertTrue(all(record[k] is False for k in source.FALSE_FLAGS))
        self.assertEqual(record["artifactBytes"],69191600)
        self.assertEqual(record["artifactSha256"],"c633fa042dc9091e5f8dccf12d68456b6c34bfb986681fac5778a296f5d0d25e")

    def test_other_head_run_success_paid_runner_missing_failed_stage_or_digest_reject(self):
        cases=[(0,"head_sha","0"*40),(0,"id",source.RUN_ID+1),(0,"conclusion","success"),
            (2,"expired",True),(2,"size_in_bytes",source.ARTIFACT_BYTES+1),(2,"digest","sha256:"+"0"*64)]
        for which,key,value in cases:
            packet=list(metadata());packet[which][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):source.validate_metadata(*packet)
        run,jobs,artifact=metadata();jobs["jobs"][0]["labels"]=["self-hosted"]
        with self.assertRaises(ValueError):source.validate_metadata(run,jobs,artifact)
        run,jobs,artifact=metadata();jobs["jobs"][0]["steps"][9]["conclusion"]="success"
        with self.assertRaises(ValueError):source.validate_metadata(run,jobs,artifact)
        bad=source.validate_metadata(*metadata());bad["sourceNumericalBaselineQualified"]=True
        with self.assertRaises(ValueError):source.admission(bad)

    def test_plain_source_dictionary_includes_code_binary_and_unknown_role_rejected(self):
        native=b"FoamFile {format ascii; class dictionary; object controlDict;}\nsolver incompressibleFluid;\n"
        self.assertIn("incompressibleFluid",source.plain_native(native))
        for raw in (native+b"#include evil\n",native+b"type codedFixedValue;\n",native+b"codeStream {};",native+b"\0"):
            with self.assertRaises(ValueError):source.plain_native(raw)
        notice=native+b'#includeEtc "caseDicts/setConstraintTypes"\n'
        with self.assertRaises(ValueError):source.plain_native(notice)
        source.plain_native(notice,source_constraint_notice=True)
        with self.assertRaises(ValueError):source.plain_native(native+b'#includeEtc "evil"\n',source_constraint_notice=True)
        for name in ("impeller-case/500/script.py","impeller-case/constant/secret.key","teaching-baseline-acceptance.json", "source-teaching/impeller-case/system/unknown"):
            self.assertFalse(source.native_role(name))
        self.assertTrue(source.native_role("impeller-case/500/Uf"))
        self.assertTrue(source.native_role("impeller-case/0/cellToRegion"))


class TransferTests(unittest.TestCase):
    def fixture(self,root,*,extra=None,corrupt=False):
        files={name:b'{"syntheticFixture":true}\n' for name in source.TOP}
        files["impeller-case/500/U"]=b"SYNTHETIC-NON-NATIVE-FIELD"
        if extra:files[extra]=b"SYNTHETIC-FORBIDDEN"
        archive=root/"input.zip"
        with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_STORED) as z:
            for name,raw in files.items():z.writestr(name,raw)
        if corrupt:
            raw=archive.read_bytes().replace(b"SYNTHETIC-NON-NATIVE-FIELD",b"XYNTHETIC-NON-NATIVE-FIELD",1);archive.write_bytes(raw)
        blob=archive.read_bytes();rows=sorted(({"path":name,"bytes":len(raw),"sha256":sha256(raw).hexdigest()} for name,raw in files.items()),key=lambda r:r["path"])
        return archive,blob,rows

    def call(self,root,archive,blob,rows,*,verify=None):
        # Patch only tiny test envelope pins; actual production constants remain fixed.
        with patch.dict(os.environ,{"RUNNER_TEMP":str(root)}),patch.object(source,"ARTIFACT_BYTES",len(blob)),patch.object(source,"ARTIFACT_SHA",sha256(blob).hexdigest()):
            admission=source.validate_metadata(*metadata());path=root/source.ADMISSION;path.write_bytes(source.encoded(admission))
            with patch.object(source,"verify_tree",side_effect=verify or (lambda _: {"files":rows,"syntheticFixture":True})):
                return source.extract(archive,path,root/"target")

    def test_whole_size_digest_full_crc_and_exact_member_bytes_before_persistence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive,blob,rows=self.fixture(root);result=self.call(root,archive,blob,rows)
            self.assertTrue(result["fullZipCrcVerified"]);self.assertTrue(result["byteExactTransferVerified"])
            self.assertFalse(result["actualCfdRunVerified"])
            for row in rows:self.assertEqual(sha256((root/"target"/row["path"]).read_bytes()).hexdigest(),row["sha256"])
            self.assertEqual(archive.read_bytes(),blob)

    def test_crc_failure_or_wrong_whole_digest_never_creates_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive,blob,rows=self.fixture(root,corrupt=True)
            with self.assertRaises(zipfile.BadZipFile):self.call(root,archive,blob,rows)
            self.assertFalse((root/"target").exists())
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive,blob,rows=self.fixture(root)
            with patch.dict(os.environ,{"RUNNER_TEMP":str(root)}):
                path=root/source.ADMISSION;path.write_bytes(source.encoded(source.validate_metadata(*metadata())))
                with self.assertRaisesRegex(ValueError,"whole fixed"):source.extract(archive,path,root/"target")
                self.assertFalse((root/"target").exists())

    def test_unknown_code_traversal_symlink_duplicate_paths_reject_before_writes(self):
        for extra in ("../outside", "impeller-case/500/code.py","impeller-case/constant/token.pem"):
            with self.subTest(extra=extra),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);archive,blob,rows=self.fixture(root,extra=extra)
                with self.assertRaises(ValueError):self.call(root,archive,blob,rows)
                self.assertFalse((root/"target").exists())
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive,_,rows=self.fixture(root)
            with zipfile.ZipFile(archive,"a") as z:
                info=zipfile.ZipInfo("impeller-case/500/p");info.create_system=3;info.external_attr=0o120777<<16;z.writestr(info,b"outside")
            with self.assertRaises(ValueError):self.call(root,archive,archive.read_bytes(),rows)
            self.assertFalse((root/"target").exists())

    def test_failed_current_field_proof_cleans_owned_target_keeps_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive,blob,rows=self.fixture(root)
            def invalid(_):raise ValueError("SYNTHETIC missing native cell field")
            with self.assertRaisesRegex(ValueError,"missing native"):self.call(root,archive,blob,rows,verify=invalid)
            self.assertFalse((root/"target").exists());self.assertEqual(archive.read_bytes(),blob)

    def test_source_scope_and_stale_output_rejected(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);(root/"old").mkdir()
            with self.assertRaises(ValueError):source.paths.scope_path(root/"old",fresh=True)
            with self.assertRaises(ValueError):source.paths.scope_path(root.parent/"outside")

    def test_mutated_source_row_cannot_be_promoted_by_rewriting_a_transfer_sidecar(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive,blob,rows=self.fixture(root)
            with patch.dict(os.environ,{"RUNNER_TEMP":str(root)}),patch.object(source,"ARTIFACT_BYTES",len(blob)),patch.object(source,"ARTIFACT_SHA",sha256(blob).hexdigest()):
                source.archive_tree_binding(archive,rows)
                tampered=[dict(row) for row in rows];tampered[0]["sha256"]="0"*64
                with self.assertRaisesRegex(ValueError,"fixed archive members"):source.archive_tree_binding(archive,tampered)


if __name__=="__main__":unittest.main()
