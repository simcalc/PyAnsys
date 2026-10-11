"""Synthetic GitHub metadata only, never native volume or baseline evidence."""
import copy
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

spec=importlib.util.spec_from_file_location("volume_source_preflight_tests",Path(__file__).resolve().parents[1]/"tools/reuse_volume_workflow_preflight.py")
tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)


def fixture(conclusion="success",*,archive_bytes=500,digest="a"*64):
    inputs=tool.validate_inputs({"VOLUME_RUN_ID":"38181682156"})
    repo={"full_name":tool.REPOSITORY,"private":False,"visibility":"public"}
    run={"id":38181682156,"status":"completed","conclusion":conclusion,"event":"workflow_dispatch",
         "path":tool.SOURCE_WORKFLOW,"head_sha":tool.SOURCE_COMMIT,"repository":dict(repo),"head_repository":dict(repo)}
    steps=[{"number":n,"name":name,"status":"completed","conclusion":"success"} for n,name in enumerate(tool.PREREQUISITE_STEPS,1)]
    steps.append({"number":tool.STEP_NUMBER,"name":tool.STEP_NAME,"status":"completed","conclusion":"success"})
    steps.append({"number":9,"name":"Convert and run preliminary MRF only after actual volume admission",
                  "status":"completed","conclusion":conclusion})
    jobs={"total_count":1,"jobs":[{"id":987,"run_id":38181682156,"head_sha":tool.SOURCE_COMMIT,"name":tool.JOB_NAME,
        "status":"completed","conclusion":conclusion,"labels":["ubuntu-24.04"],"steps":steps}]}
    artifacts={"artifacts":[{"id":456,"name":"frozen-rotor-extended-free-hxt-38181682156","expired":False,
        "size_in_bytes":archive_bytes,"workflow_run":{"id":38181682156},"digest":"sha256:"+digest}]}
    return inputs,run,jobs,artifacts


class VolumeAdmissionTests(unittest.TestCase):
    def test_successful_mesher_may_precede_failed_downstream_but_never_qualifies_baseline(self):
        for conclusion in ("success","failure"):
            data=fixture(conclusion);admitted=tool.validate_source(*data)
            self.assertEqual(admitted["sourceRunConclusion"],conclusion)
            self.assertTrue(admitted["volumeProvenanceAdmitted"])
            self.assertEqual(admitted["sourceCommit"],tool.SOURCE_COMMIT)
            for key in tool.FALSE_FLAGS:self.assertIs(admitted[key],False)
            self.assertEqual(tool.validate_admission(admitted),admitted)

    def test_exact_commit_workflow_repo_status_and_artifact_digest_remain_required(self):
        for key,value in (("head_sha","b"*40),("path",".github/workflows/remesh-frozen-rotor.yml"),
            ("status","in_progress"),("conclusion","cancelled"),("event","pull_request"),("id",1)):
            data=list(fixture());data[1][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):tool.validate_source(*data)
        for role in ("repository","head_repository"):
            data=list(fixture());data[1][role]["private"]=True
            with self.subTest(role=role),self.assertRaises(ValueError):tool.validate_source(*data)
        for key,value in (("digest",None),("digest","sha256:"+"g"*64),("digest","a"*64),
            ("expired",True),("id",True),("size_in_bytes",True),("size_in_bytes",tool.MAX_ARTIFACT_BYTES+1),
            ("name","frozen-rotor-extended-free-hxt-38181682157")):
            data=list(fixture());data[3]["artifacts"][0][key]=value
            with self.subTest(key=key,value=value),self.assertRaises((ValueError,TypeError)):tool.validate_source(*data)

    def test_mesher_failure_upstream_failure_or_unreviewed_downstream_failure_rejected(self):
        for mutation in ("mesher-failed","upstream-failed","missing-prerequisite","wrong-prerequisite","wrong-step","wrong-number","wrong-job", "wrong-labels","duplicate-job","other-failure"):
            data=list(fixture("failure"));job=data[2]["jobs"][0]
            if mutation=="mesher-failed":job["steps"][6]["conclusion"]="failure"
            elif mutation=="upstream-failed":job["steps"][1]["conclusion"]="failure"
            elif mutation=="missing-prerequisite":job["steps"].pop(2)
            elif mutation=="wrong-prerequisite":job["steps"][2]["name"]="Other capacity command"
            elif mutation=="wrong-step":job["steps"][6]["name"]="An arbitrary mesher"
            elif mutation=="wrong-number":job["steps"][6]["number"]=6
            elif mutation=="wrong-job":job["name"]="Other hosted job"
            elif mutation=="wrong-labels":job["labels"]=["self-hosted"]
            elif mutation=="duplicate-job":data[2]["jobs"].append(copy.deepcopy(job));data[2]["total_count"]=2
            else:job["steps"][-1]["name"]="Retain artifacts"
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):tool.validate_source(*data)

    def test_admission_mutation_and_qualification_flag_injection_rejected(self):
        accepted=tool.validate_source(*fixture())
        for key,value in (("sourceBaselineQualified",True),("sourceBaselineQualified",0),("volumeProvenanceAdmitted",1),
                          ("artifactSha256","b"*64),("artifactName","wrong"),
                          ("sourceCommit","b"*40),("extraSecret","not permitted")):
            with self.subTest(key=key),self.assertRaises(ValueError):tool.validate_admission({**accepted,key:value})
        for value in ("0","../main",True,None,"38181682156;echo"):
            with self.subTest(value=value),self.assertRaises(ValueError):tool.validate_inputs({"VOLUME_RUN_ID":value})

    def test_bounded_duplicate_or_nonfinite_metadata_is_rejected(self):
        with TemporaryDirectory() as root:
            path=Path(root)/"metadata.json"
            for raw in (b'{"id":1,"id":2}',b'{"id":NaN}',b"x"*(128*1024+1)):
                path.write_bytes(raw)
                with self.subTest(raw=raw[:30]),self.assertRaises(ValueError):tool.read_json(path)


if __name__=="__main__":unittest.main()
