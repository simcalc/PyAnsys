"""Synthetic parser/control fixtures only; no native mesh or CFD is run here."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools/reduce_frozen_rotor_snappy_baseline.py"
spec = importlib.util.spec_from_file_location("synthetic_snappy_baseline_reduction", TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class ApproximateBaselineReduction(unittest.TestCase):
    def write(self, root, path, value):
        raw = value if isinstance(value, bytes) else value.encode("ascii")
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return {"path": path, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}

    def json(self, root, path, value):
        return self.write(root, path, tool.encoded(value))

    def verified(self, root, **_):
        report, row = tool.record(root, "openfoam-preparation.json")
        return {"report": report, "reportFile": row, "components": report["components"]}

    def context(self):
        stack = ExitStack()
        helper = tool.preparer()
        stack.enter_context(patch.object(helper, "verify_output", side_effect=self.verified))
        stack.enter_context(patch.object(tool, "binary_identity"))
        stack.enter_context(patch.object(tool, "source_identity", return_value={
            "schemaVersion":"synthetic-approximate-target-identity-not-research", "geometricApproximationQualified":False,
            "nativeNodeIdentityEquivalent":False,"mainApplicationWrites":False}))
        stack.enter_context(patch.object(tool.geometry, "native_mesh", return_value={"cellCount":2,"nodeCount":2,"source":"synthetic-topology-not-research"}))
        return stack

    def table(self, patch, columns, values):
        header = f"# Selection : patch({patch})\n# Faces : 3\n# Area : 0.5\n# Time " + " ".join(columns) + "\n"
        return header + "".join(str(i)+" "+" ".join(format(v,".17g") for v in values)+"\n" for i in range(481,501))

    def fixture(self, root, *, residual=1e-5, pressure_min=10):
        helper = tool.preparer()
        source = self.write(root,"source-snappy/synthetic-marker.json",b"{\"synthetic\":true}")
        authored, physics = [], []
        for component in ("impeller","volute"):
            for path, raw in helper.case_files(component).items():
                authored.append(self.write(root,component+"-case/"+path,raw))
                if path.startswith("0/"):
                    physics.append(self.write(root,"authored-physics/"+component+"-case/"+path,raw))
        for name in tool.MESH_FILES:
            self.write(root,"impeller-case/constant/polyMesh/"+name,"synthetic-bound-topology-only:"+name)
        preparation = {"schemaVersion":tool.PREPARATION_SCHEMA,"exampleDataCommit":tool.native.case_tool.inventory.PIN,
            "mainApplicationWrites":False,"nativeCommandArguments":helper.commands(root),"classroomAssumptions":helper.assumptions(),
            "components":[{"component":"impeller","cellCount":1,"nodeCount":1},{"component":"volute","cellCount":1,"nodeCount":1}],
            "sourceEvidenceFileRecords":[source],"authoredInputFiles":sorted(authored,key=lambda r:r["path"]),
            "authoredPhysicsFileRecords":sorted(physics,key=lambda r:r["path"]),
            "sourceSnappyExecutionReportSha256":"a"*64,"sourceGeometryPreparationReportSha256":"b"*64}
        prep_row = self.json(root,"openfoam-preparation.json",preparation)
        runtime_row = self.json(root,"runtime-identity.json",{"schemaVersion":"synthetic-runtime-identity-no-native-binary"})
        bound = tool.build_manifest(root)
        for name,key in (("geometry-identity.json","geometryIdentity"),("native-input-manifest.json","manifest"),("classroom-study.json","study")):
            self.json(root,name,bound[key])
        solver = "Build : 12-"+tool.geometry.surface.SOURCE_COMMIT[:12]+"\n"
        for i in range(481,501):
            solver += f"Time = {i}s\n"
            for name in ("Ux","Uy","Uz","p","k","omega"):
                solver += f"solver: Solving for {name}, Initial residual = {residual}, Final residual = 1e-10, No Iterations 2\n"
            solver += "solver: Solving for p, Initial residual = 1e-8, Final residual = 1e-12, No Iterations 1\n"
            solver += "time step continuity errors : sum local = 1e-6, global = -1e-7, cumulative = -1e-5\n"
        solver += "End\n"
        prefix = "Build : 12-"+tool.geometry.surface.SOURCE_COMMIT[:12]+"\n"
        mesh = prefix+"cells: 2\nMin volume = 1e-6. Max volume = 2e-6. Total volume = 3e-6.\nMesh OK.\nSource min/average/max coverage = 1/1/1\nTarget min/average/max coverage = 1/1/1\nEnd\n"
        stages = []
        for i,program in enumerate(tool.PROGRAMS):
            row = self.write(root,f"logs/{i:02d}-{program}.log",mesh if program=="checkMesh" else solver if program=="foamRun" else prefix+"End\n")
            stages.append({"program":program,"exitCode":0,"elapsedSeconds":1,"log":Path(row["path"]).name,"logBytes":row["bytes"],"logSha256":row["sha256"]})
        dims = {"p":"0 2 -2 0 0 0 0","U":"0 1 -1 0 0 0 0","k":"0 2 -2 0 0 0 0","omega":"0 0 -1 0 0 0 0","nut":"0 2 -1 0 0 0 0"}
        for name,dim in dims.items():
            value = "(1 2 3)" if name=="U" else "100" if name=="p" else "0" if name=="nut" else "1"
            kind = "volVectorField" if name=="U" else "volScalarField"
            self.write(root,"impeller-case/500/"+name,f"FoamFile {{format ascii; class {kind}; object {name};}}\ndimensions [{dim}];\ninternalField uniform {value};\nboundaryField {{}}\n")
        import run_frozen_rotor_original as endpoint
        fields, rows, _ = endpoint.final_fields(root,2)
        execution = {"schemaVersion":tool.EXECUTION_SCHEMA,"status":"SNAPPY_MRF_EXECUTED_ACCEPTANCE_REQUIRED",
            "exampleDataCommit":tool.native.case_tool.inventory.PIN,"preparationReportSha256":prep_row["sha256"],
            "runtimeIdentitySha256":runtime_row["sha256"],"sourceSnappyExecutionReportSha256":"a"*64,
            **{k:False for k in tool.FALSE_CLAIMS},
            **{k:True for k in ("actualCfdRunVerified","simulationExecuted","nativeGeometryQualityVerified","physicalWallTypesVerified",
                "coupledInterfaceCoverageVerified","nativeSolverCompleted","finiteNativeFieldsVerified","inputArtifactsImmutable")},
            "classroomMetresPerSourceCoordinateUnit":1,"githubRunId":"synthetic-parser-fixture-not-research",
            "stages":stages,"finalNativeFields":fields,"finalNativeFieldRecords":rows,
            "mergedNativeMeshEvidence":{"cellCount":2,"nodeCount":2,"source":"synthetic-topology-not-research"}}
        self.json(root,"openfoam-execution.json",execution)
        rho,q = 998.2,90/998.2
        values = {"flowInlet":[-q],"flowOutlet":[q],"flowMagnitudeInlet":[q],"flowMagnitudeOutlet":[q],
            "weightedInlet":[10,tool.PRESSURE+rho*10+rho*4/2,4],"weightedOutlet":[210,tool.PRESSURE+rho*210+rho*9/2,9]}
        for name,(section,columns) in tool.native.SAMPLERS.items():
            self.write(root,f"impeller-case/postProcessing/{name}/0/surfaceFieldValue.dat",self.table(section,columns,values[name]))
        self.write(root,"impeller-case/postProcessing/globalPressureMinimum/0/volFieldValue.dat","# Time min(p)\n"+"".join(f"{i} {pressure_min}\n" for i in range(481,501)))
        for name,section,value in zip(("flowRotorInterface","flowStatorInterface"),tool.native.COUPLED_PATCHES,(q,-q)):
            self.write(root,f"impeller-case/postProcessing/{name}/0/surfaceFieldValue.dat",self.table(section,("sum(phi)",),[value]))
        return bound

    def test_real_projection_gates_are_scoped_to_explicit_synthetic_observation(self):
        with tempfile.TemporaryDirectory() as temp,self.context():
            root=Path(temp);self.fixture(root)
            result=tool.reduce_native_baseline(root)
            self.assertTrue(result["numericalBaselineQualified"],result["diagnostics"])
            self.assertFalse(result["qualifiedBaseline"])
            self.assertEqual(result["schemaVersion"],tool.SCHEMA)
            self.assertEqual(result["status"],"ExploratoryNumericalBaselineQualified")
            self.assertEqual(result["samplingIterationWindow"],[481,500])
            self.assertAlmostEqual(result["stages"]["head"]["result"]["value"],202.5/9.81)
            self.assertAlmostEqual(result["stages"]["npsha"]["result"]["value"],(300000+9982-2339)/(998.2*9.81)+4/19.62)
            self.assertEqual(result["nativeObservation"]["quality"]["outwardInletMassFlowKgS"],-90)
            for key in ("geometricApproximationQualified","wallResolutionVerified","yPlusResolutionQualified","meshConvergenceVerified",
                        "timeStepConvergenceVerified","physicalValidationVerified","npsh3Qualified","restartVerified","physicalTransientExecuted"):
                self.assertFalse(result[key],key)
            rows=result["nativeEvidenceRecords"]
            self.assertEqual(len(rows),len({r["path"] for r in rows}))
            self.assertEqual(result["nativeObservation"]["nativeEvidence"]["sha256"],sha256(tool.encoded(rows)).hexdigest())

    def test_missing_evidence_never_creates_calculated_results(self):
        with tempfile.TemporaryDirectory() as temp:
            result=tool.reduce_native_baseline(temp)
            self.assertFalse(result["actualCfdRunVerified"])
            self.assertFalse(result["numericalBaselineQualified"])
            self.assertFalse(result["qualifiedBaseline"])
            self.assertFalse(result["numericalBaselineQualified"])
            self.assertEqual(result["stages"],{})

    def test_first_outer_residual_and_conservation_are_required_after_solver_completion(self):
        with tempfile.TemporaryDirectory() as temp,self.context():
            root=Path(temp);self.fixture(root,residual=.1)
            result=tool.reduce_native_baseline(root)
            self.assertTrue(result["actualCfdRunVerified"])
            self.assertFalse(result["qualifiedBaseline"])
            self.assertFalse(result["numericalBaselineQualified"])
            self.assertIn("CFD_RESIDUAL_CONVERGENCE_REQUIRED",str(result["diagnostics"]))
            self.assertEqual(set(result["stages"]),{"head","npsha"})
        with tempfile.TemporaryDirectory() as temp,self.context():
            root=Path(temp);self.fixture(root)
            target=root/"impeller-case/postProcessing/flowOutlet/0/surfaceFieldValue.dat"
            target.write_text(self.table("mass-flow-inlet-11",("sum(phi)",),[80/998.2]))
            result=tool.reduce_native_baseline(root)
            self.assertFalse(result["qualifiedBaseline"])
            self.assertFalse(result["numericalBaselineQualified"])
            self.assertIn("CFD_MASS_CONSERVATION_REQUIRED",str(result["diagnostics"]))

    def test_missing_outlet_retains_valid_current_inlet_branch(self):
        with tempfile.TemporaryDirectory() as temp,self.context():
            root=Path(temp);self.fixture(root)
            (root/"impeller-case/postProcessing/weightedOutlet/0/surfaceFieldValue.dat").unlink()
            result=tool.reduce_native_baseline(root)
            self.assertTrue(result["actualCfdRunVerified"])
            self.assertFalse(result["qualifiedBaseline"])
            self.assertFalse(result["numericalBaselineQualified"])
            self.assertEqual(set(result["stages"]),{"npsha"})

    def test_absolute_reference_is_applied_exactly_once(self):
        with tempfile.TemporaryDirectory() as temp,self.context():
            root=Path(temp);self.fixture(root)
            self.write(root,"impeller-case/postProcessing/weightedInlet/0/surfaceFieldValue.dat",
                       self.table("inlet",tool.native.SAMPLERS["weightedInlet"][1],[10,600000+9982+1996.4,4]))
            result=tool.reduce_native_baseline(root)
            self.assertFalse(result["qualifiedBaseline"])
            self.assertFalse(result["numericalBaselineQualified"])
            self.assertIn("ABSOLUTE_TOTAL_PRESSURE_MAPPING",str(result["diagnostics"]))
            self.assertEqual(result["stages"],{})

    def test_low_global_pressure_does_not_qualify_noncavitating_reference(self):
        with tempfile.TemporaryDirectory() as temp,self.context():
            root=Path(temp);self.fixture(root,pressure_min=-300)
            result=tool.reduce_native_baseline(root)
            self.assertFalse(result["qualifiedBaseline"])
            self.assertFalse(result["numericalBaselineQualified"])
            self.assertFalse(result["noncavitatingBaselineVerified"])
            self.assertIn("CFD_NONCAVITATING_BASELINE_REQUIRED",str(result["diagnostics"]))
            self.assertEqual(set(result["stages"]),{"head","npsha"})

    def test_ncc_coverage_and_flux_cannot_be_omitted_or_relaxed(self):
        with tempfile.TemporaryDirectory() as temp,self.context():
            root=Path(temp);self.fixture(root)
            self.write(root,"impeller-case/postProcessing/flowStatorInterface/0/surfaceFieldValue.dat",
                       self.table(tool.native.COUPLED_PATCHES[1],("sum(phi)",),[-.98*90/998.2]))
            result=tool.reduce_native_baseline(root)
            self.assertFalse(result["qualifiedBaseline"])
            self.assertIn("CFD_ACTUAL_INTERFACE_FLUX_CONSERVATION_REQUIRED",str(result["diagnostics"]))

    def test_new_route_does_not_accept_old_execution_or_geometric_qualification(self):
        for changed in ({"schemaVersion":"untirta.frozen-rotor-original-openfoam-execution.v1"},{"geometricApproximationQualified":True}):
            with tempfile.TemporaryDirectory() as temp,self.context():
                root=Path(temp);self.fixture(root)
                value=json.loads((root/"openfoam-execution.json").read_text());value.update(changed)
                self.json(root,"openfoam-execution.json",value)
                result=tool.reduce_native_baseline(root)
                self.assertFalse(result["actualCfdRunVerified"])
                self.assertFalse(result["numericalBaselineQualified"])
                self.assertEqual(result["stages"],{})

    def test_endpoint_bytes_and_manifest_current_inputs_cannot_be_stale(self):
        with tempfile.TemporaryDirectory() as temp,self.context():
            root=Path(temp);self.fixture(root)
            with (root/"impeller-case/500/p").open("ab") as stream:stream.write(b"\n")
            result=tool.reduce_native_baseline(root)
            self.assertFalse(result["actualCfdRunVerified"])
            self.assertFalse(result["numericalBaselineQualified"])
            self.assertIn("ENDPOINT_FIELD_BINDING",str(result["diagnostics"]))
        with tempfile.TemporaryDirectory() as temp,self.context():
            root=Path(temp);self.fixture(root)
            with (root/"impeller-case/constant/physicalProperties").open("ab") as stream:stream.write(b"\n")
            result=tool.reduce_native_baseline(root)
            self.assertFalse(result["actualCfdRunVerified"])
            self.assertFalse(result["numericalBaselineQualified"])
            self.assertIn("CURRENT_PREEXECUTION_MANIFEST",str(result["diagnostics"]))

    def test_manifest_requires_prebound_pressure_source_inventory_and_canonical_geometry(self):
        with tempfile.TemporaryDirectory() as temp,self.context():
            root=Path(temp);self.fixture(root)
            prep=json.loads((root/"openfoam-preparation.json").read_text())
            prep["classroomAssumptions"]["externalTotalPressureAbsolutePa"]=101325
            self.json(root,"openfoam-preparation.json",prep)
            with self.assertRaisesRegex(tool.protocol.ProtocolError,"FIXED_CLASSROOM_BASIS"):
                tool.build_manifest(root)
        with tempfile.TemporaryDirectory() as temp,self.context():
            root=Path(temp);self.fixture(root)
            self.write(root,"source-snappy/unlisted.json",b"{}")
            with self.assertRaisesRegex(tool.protocol.ProtocolError,"EXACT_RETAINED_SOURCE_INVENTORY"):
                tool.build_manifest(root)
        with tempfile.TemporaryDirectory() as temp,self.context():
            root=Path(temp);self.fixture(root)
            value=json.loads((root/"geometry-identity.json").read_text())
            self.write(root,"geometry-identity.json",json.dumps(value,indent=2))
            with self.assertRaisesRegex(tool.protocol.ProtocolError,"CANONICAL_GEOMETRY_IDENTITY"):
                tool.build_manifest(root)

    def test_actual_first_write_time_one_is_supported_without_extra_stale_directory(self):
        with tempfile.TemporaryDirectory() as temp,self.context():
            root=Path(temp);self.fixture(root)
            for folder in (root/"impeller-case/postProcessing").iterdir():(folder/"0").rename(folder/"1")
            result=tool.reduce_native_baseline(root)
            self.assertTrue(result["numericalBaselineQualified"])
            self.assertFalse(result["qualifiedBaseline"])
            (root/"impeller-case/postProcessing/weightedInlet/0").mkdir()
            result=tool.reduce_native_baseline(root)
            self.assertFalse(result["qualifiedBaseline"])
            self.assertFalse(result["numericalBaselineQualified"])
            self.assertIn("ONE_FRESH_NATIVE_SAMPLER_DIRECTORY",str(result["diagnostics"]))

    def test_runtime_inventory_is_exact_and_reads_actual_binary_bytes(self):
        identity={"schemaVersion":tool.RUNTIME_SCHEMA,"openFoamFoundationVersion":"12","packageVersion":"20250206",
            "officialDebSha256":tool.geometry.surface.PACKAGE_SHA,"expectedSourceCommit":tool.geometry.surface.SOURCE_COMMIT,
            "mainApplicationWrites":False,"binaries":[{"program":p,"path":"/opt/openfoam12/bin/"+p,"bytes":3,
                "sha256":sha256(b"abc").hexdigest(),"packageOwnership":"openfoam12: /opt/openfoam12/bin/"+p} for p in tool.PROGRAMS]}
        with patch.object(tool.native.case_tool,"read_regular",return_value=b"abc"):
            tool.binary_identity(identity)
            value=deepcopy(identity);value["binaries"][0]["sha256"]="0"*64
            with self.assertRaisesRegex(tool.protocol.ProtocolError,"BINARY_IDENTITY_MISMATCH"):tool.binary_identity(value)
        value=deepcopy(identity);value["binaries"][0]["program"]="gmshToFoam"
        with self.assertRaisesRegex(tool.protocol.ProtocolError,"EXACT_FOUR_NATIVE_BINARIES"):tool.binary_identity(value)

    def test_geometry_identity_keeps_recipe_and_target_distinct_from_raw_run_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            recipe=self.write(root,"source-snappy/impeller-case/system/snappyHexMeshDict",b"synthetic-defined-recipe-not-native")
            recipe["path"]="impeller-case/system/snappyHexMeshDict"
            prep={"schemaVersion":tool.geometry.PREPARATION_SCHEMA,"authoredFileRecords":[recipe],
                  "classroomAssumptions":{"metresPerSourceCoordinateUnit":1,"coordinateUnits":"UNVERIFIED"}}
            prep_row=self.json(root,"source-snappy/snappy-preparation.json",prep)
            execution={"schemaVersion":tool.geometry.SCHEMA,"status":"APPROXIMATE_NATIVE_MESH_CHECKED_GEOMETRY_REVIEW_REQUIRED",
                "preparationReportFile":{**prep_row,"path":"snappy-preparation.json"},
                **{k:True for k in ("volumeMeshGenerated","meshQualityCheckedBySolver","nativeCoupledCoverageVerified","inputArtifactsImmutable")},
                **{k:False for k in ("geometricApproximationQualified","fullGeometryQualification","nativeNodeIdentityEquivalent",
                    "nativeCoordinateUnitsVerified","simulationExecuted","actualCfdRunVerified","mainApplicationWrites")}}
            self.json(root,"source-snappy/snappy-execution.json",execution)
            target={"report":{"sourceTopologySemanticIdentity":{"semanticSha256":"a"*64}},"components":[{
                "component":"impeller","surfaceFile":{"path":"impeller-union.obj","bytes":1,"sha256":"b"*64},
                "patches":[{"name":"inlet","sourceZoneId":1,"sourceZoneType":4,"triangleCount":1}]}]}
            with patch.object(tool.union,"verify_output",return_value=target):
                first=tool.source_identity(root)
                execution.update(createdAt="synthetic-new-attempt",githubRunId="another-synthetic-attempt")
                self.json(root,"source-snappy/snappy-execution.json",execution)
                self.assertEqual(first,tool.source_identity(root))
                changed=self.write(root,"source-snappy/impeller-case/system/snappyHexMeshDict",b"another-synthetic-recipe")
                prep["authoredFileRecords"]=[{**changed,"path":"impeller-case/system/snappyHexMeshDict"}]
                prep_row=self.json(root,"source-snappy/snappy-preparation.json",prep)
                execution["preparationReportFile"]={**prep_row,"path":"snappy-preparation.json"}
                self.json(root,"source-snappy/snappy-execution.json",execution)
                second=tool.source_identity(root)
                self.assertNotEqual(first["generatorRecipeFiles"],second["generatorRecipeFiles"])
                self.assertEqual(first["targetSurfaces"],second["targetSurfaces"])
                self.assertFalse(second["geometricApproximationQualified"])
                execution["preparationReportFile"]["sha256"]="c"*64
                self.json(root,"source-snappy/snappy-execution.json",execution)
                with self.assertRaisesRegex(tool.protocol.ProtocolError,"SOURCE_PREPARATION_BINDING"):
                    tool.source_identity(root)

    def test_current_bytes_changing_during_reduction_invalidate_qualification(self):
        with tempfile.TemporaryDirectory() as temp,self.context():
            root=Path(temp);self.fixture(root)
            original=tool.numerical_observation
            def mutation(*arguments):
                original(*arguments)
                with (root/"impeller-case/500/p").open("ab") as stream:stream.write(b"\n")
            with patch.object(tool,"numerical_observation",side_effect=mutation):
                result=tool.reduce_native_baseline(root)
            self.assertFalse(result["qualifiedBaseline"])
            self.assertFalse(result["numericalBaselineQualified"])
            self.assertFalse(result["actualCfdRunVerified"])
            self.assertEqual(result["status"],"NativeEvidenceChangedDuringReduction")


if __name__ == "__main__":
    unittest.main()
