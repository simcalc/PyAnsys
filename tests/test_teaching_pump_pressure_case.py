"""Synthetic source/recipe contracts only; no active phase or H0 is executed."""
from copy import deepcopy
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import teaching_pump_pressure_case as case


def source_fixture(root):
    """The expensive upstream-native proof is mocked; authored bytes are real."""
    source=root/"reference";source.mkdir()
    study=case.reference_case.baseline.study_packet({}, {k:"a"*64 for k in case.protocol.CONTROL_KEYS})
    authored=case.reference_case.native_recipe(study,default_faces=False);rows=[]
    for name,raw in sorted(authored.items()):
        target=source/"case"/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        rows.append({"path":name,"bytes":len(raw),"sha256":sha256(raw).hexdigest()})
    mesh=[]
    for name in case.MESH_FILES:
        path="case/constant/polyMesh/"+name;target=source/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(("SYNTHETIC-MESH-"+name+"\n").encode())
        mesh.append(case.read(source,path)[1])
    identity={"schemaVersion":"untirta.classroom-authored-pump-geometry-identity.v1","provenance":case.ROUTE,"units":"SI","syntheticFixture":True}
    (source/case.reference_runner.IDENTITY).write_bytes(case.encoded(identity))
    packet={"activePhaseReferenceHeadM":10.,"study":study,"nativeObservation":{"nativeEvidence":{"sha256":"b"*64}},"syntheticFixture":True}
    (source/"teaching-phase-acceptance.json").write_bytes(case.encoded(packet));(source/case.reference_runner.MANIFEST).write_bytes(b'{"syntheticFixture":true}')
    (source/case.reference_runner.RUNTIME).write_bytes(b'{"syntheticFixture":true}')
    prepared={"schemaVersion":case.reference_case.SCHEMA,"inletAbsoluteTotalPressurePa":300000.,"authoredInputFiles":rows,
        "declaredStudyInputs":study["input"],"physicalBasis":study["physicalBasis"],"sourceReference":study["sourceReference"],"defaultFacesPresent":False,
        "preservedNativeMeshFiles":[{**row,"path":row["path"].removeprefix("case/")} for row in mesh]}
    (source/case.reference_case.REPORT).write_bytes(case.encoded(prepared))
    components=[{"component":name,"cellCount":2,"nodeCount":5} for name in ("impeller","volute")]
    def verify(directory):
        directory=Path(directory)
        return {"directory":directory,"packet":deepcopy(packet),"report":deepcopy(packet),"reportFile":case.read(directory,"teaching-phase-acceptance.json")[1],"study":deepcopy(study),
            "manifest":{"syntheticFixture":True},"manifestFile":case.read(directory,case.reference_runner.MANIFEST)[1],"runtime":{"syntheticFixture":True},"preparation":deepcopy(prepared),"components":components,
            "coupledMeshFiles":[case.read(directory,row["path"])[1] for row in mesh],"geometryIdentity":identity,"geometryFile":case.read(directory,case.reference_runner.IDENTITY)[1],
            "sourceRecords":case.source_inventory(directory),"defaultFacesPresent":False}
    return source,verify


class TrustedPressureRecipeTests(unittest.TestCase):
    def study(self):return case.reference_case.baseline.study_packet({}, {k:"a"*64 for k in case.protocol.CONTROL_KEYS})

    def test_only_pressure_field_changes_and_fixed300k_reference_api_stays_unchanged(self):
        study=self.study();high=case.reference_case.native_recipe(study,default_faces=False);point=case.native_recipe(study,150000.,default_faces=False)
        self.assertEqual(set(high),set(point))
        self.assertEqual({name for name in high if high[name]!=point[name]},{"0/p_rgh"})
        self.assertIn(b"internalField uniform 150000;",point["0/p_rgh"])
        self.assertIn(b"p0 uniform 150000;",point["0/p_rgh"])
        self.assertIn(b"internalField uniform 300000;",case.reference_case.native_recipe(study,default_faces=False)["0/p_rgh"])
        self.assertIn(b"wallDist {method meshWave;}",point["system/fvSchemes"])
        self.assertNotIn(b"nonRotatingPatches",point["constant/MRFProperties"])
        self.assertNotIn("0/p",point);self.assertNotIn("constant/dynamicMeshDict",point)

    def test_pressure_domain_is_explicit_positive_absolute_and_reference_bounded(self):
        for value in (True,None,math.nan,math.inf,0,2339,300001):
            with self.subTest(value=value),self.assertRaises(ValueError):case.pressure_value(value)
        self.assertEqual(case.pressure_value(2500),2500.)
        self.assertEqual(case.pressure_value(300000),300000.)

    def test_current_reference_and_point_have_same_control_family_after_only_pressure_normalization(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);source,verify=source_fixture(root)
            with patch.object(case,"verify_reference",side_effect=verify):point=case.prepare(source,root/"point",pressure_absolute_pa=150000)
            with patch.object(case,"runtime_family",return_value={"actualBinaryFixture":"mocked-source-and-point-identical"}):
                high=case.reference_family(verify(source));low=case.family_rows(root/"point",point,{"syntheticFixture":True})
            self.assertEqual(high["groups"],low["groups"])
            self.assertNotEqual(high["pressureNormalizationProof"]["nativeFileSha256"],low["pressureNormalizationProof"]["nativeFileSha256"])
            self.assertEqual(high["pressureNormalizationProof"]["normalizedFileSha256"],low["pressureNormalizationProof"]["normalizedFileSha256"])
            self.assertEqual(low["pressureNormalizationProof"]["structuralSentinelPa"],1.)
            self.assertFalse(low["pressureNormalizationProof"]["sentinelIsNativeInput"])

    def test_trusted_recipe_reconstruction_rejects_nonpressure_change_even_with_rehashed_metadata(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);source,verify=source_fixture(root)
            with patch.object(case,"verify_reference",side_effect=verify):point=case.prepare(source,root/"point",pressure_absolute_pa=150000)
            target=root/"point/case/0/p_rgh";target.write_bytes(target.read_bytes().replace(b"fixedFluxPressure",b"zeroGradient",1))
            for row in point["authoredInputFiles"]:
                if row["path"]=="0/p_rgh":row.update(bytes=target.stat().st_size,sha256=sha256(target.read_bytes()).hexdigest())
            with self.assertRaisesRegex(ValueError,"reconstruct independently"):case.family_rows(root/"point",point,{})


class PressurePreparationTests(unittest.TestCase):
    def test_source_copy_and_actual_mesh_model_pressure_recipe_replay_without_solver(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);source,verify=source_fixture(root)
            with patch.object(case,"verify_reference",side_effect=verify):
                prepared=case.prepare(source,root/"point",pressure_absolute_pa=150000)
                current=case.verify_output(root/"point")
            self.assertEqual(current["report"],prepared)
            self.assertTrue(all(prepared[k] is False for k in case.FALSE_FLAGS))
            self.assertEqual(prepared["pressureAbsolutePa"],150000)
            self.assertEqual(prepared["sourceActivePhaseHeadM"],10.)
            self.assertEqual([row[0] for row in prepared["nativeCommandArguments"]],["checkMesh","foamRun"])
            self.assertEqual(prepared["temporalPlan"],case.reference_case.TEMPORAL_PLAN)
            self.assertEqual(prepared["absolutePressureMapping"]["referenceAdditionPa"],0)

    def test_current_mesh_or_model_mutation_is_rejected(self):
        for relative in ("case/constant/polyMesh/points","case/constant/fvModels","case/system/fvSchemes","case/0/p_rgh",case.reference_runner.IDENTITY):
            with self.subTest(relative=relative),tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
                root=Path(temp);source,verify=source_fixture(root)
                with patch.object(case,"verify_reference",side_effect=verify):
                    case.prepare(source,root/"point",pressure_absolute_pa=150000)
                    path=root/"point"/relative;path.write_bytes(path.read_bytes()+b"SYNTHETIC-MUTATION\n")
                    with self.assertRaises(ValueError):case.verify_output(root/"point")

    def test_invalid_pressure_and_unqualified_source_stop_before_output(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
            root=Path(temp);source,verify=source_fixture(root)
            with patch.object(case,"verify_reference",side_effect=verify),self.assertRaises(ValueError):case.prepare(source,root/"point",pressure_absolute_pa=2339)
            self.assertFalse((root/"point").exists())
            with patch.object(case,"verify_reference",side_effect=ValueError("Actual activeH0 is not qualified")),self.assertRaises(ValueError):case.prepare(source,root/"point",pressure_absolute_pa=150000)
            self.assertFalse((root/"point").exists())

    def test_unreviewed_initial_field_or_native_dictionary_is_rejected(self):
        for extra in ("case/0/p","case/0/script","case/constant/dynamicMeshDict","case/system/customDict"):
            with self.subTest(extra=extra),tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{"RUNNER_TEMP":temp}):
                root=Path(temp);source,verify=source_fixture(root)
                with patch.object(case,"verify_reference",side_effect=verify):
                    case.prepare(source,root/"point",pressure_absolute_pa=150000);(root/"point"/extra).write_bytes(b"UNREVIEWED")
                    with self.assertRaises(ValueError):case.verify_output(root/"point")

    def test_runtime_document_role_can_differ_but_actual_binary_family_cannot(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);rows=[]
            for name in ("checkMesh","foamRun"):
                path=root/name;path.write_bytes(("SYNTHETIC-NATIVE-"+name).encode());rows.append({"program":name,"path":str(path.resolve()),"bytes":path.stat().st_size,"sha256":sha256(path.read_bytes()).hexdigest(),"packageOwnership":"openfoam12: "+str(path.resolve())})
            runtime={"schemaVersion":case.RUNTIME_SCHEMA,"openFoamFoundationVersion":12,"packageVersion":"20250206","officialDebSha256":case.safety.PACKAGE_SHA,
                "expectedSourceCommit":case.safety.SOURCE_COMMIT,"binaries":rows,"mainApplicationWrites":False}
            with patch.object(Path,"is_relative_to",return_value=True):
                point=case.runtime_family(runtime)
                runtime["schemaVersion"]=case.reference_runner.RUNTIME_SCHEMA
                self.assertEqual(point,case.runtime_family(runtime))
                (root/"foamRun").write_bytes(b"CHANGED")
                with self.assertRaises(ValueError):case.runtime_family(runtime)

    def test_unknown_runtime_schema_or_nonfinite_json_never_qualifies(self):
        with self.assertRaises(ValueError):case.runtime_family({"schemaVersion":"other"})
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for raw in (b'{"x":NaN}',b'{"x":1,"x":2}'):
                (root/"data.json").write_bytes(raw)
                with self.assertRaises(ValueError):case.json_file(root,"data.json")


if __name__=="__main__":unittest.main()
