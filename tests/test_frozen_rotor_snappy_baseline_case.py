"""Synthetic evidence/mesh fixtures only; none is native CFD research evidence."""
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("snappy_baseline_case", TOOLS / "frozen_rotor_snappy_baseline_case.py")
case = importlib.util.module_from_spec(spec); spec.loader.exec_module(case)
fixture_spec = importlib.util.spec_from_file_location("snappy_small_surface", Path(__file__).parent / "test_frozen_rotor_snappy_case.py")
small = importlib.util.module_from_spec(fixture_spec); fixture_spec.loader.exec_module(small)


def tetra_cube():
    points = [tuple(map(float, p)) for p in small.CUBE_POINTS]
    cells = ((0,1,2,6),(0,2,3,6),(0,3,7,6),(0,7,4,6),(0,4,5,6),(0,5,1,6))
    faces = {}
    for i, (a,b,c,d) in enumerate(cells):
        for triangle in ((b,c,d),(a,d,c),(a,b,d),(a,c,b)):
            key = tuple(sorted(triangle))
            if key in faces:
                faces[key][2] = i
            else:
                faces[key] = [triangle,i,None]
    internal = [row for row in faces.values() if row[2] is not None]
    boundary = [row for row in faces.values() if row[2] is None]
    return points, internal, boundary


def write_mesh(root, components, *, background_box=True):
    points, internal, boundary = tetra_cube()
    merged = len(components) == 2
    all_points, all_internal, all_boundary = [], [], []
    for i, component in enumerate(components):
        all_points.extend(points)
        all_internal.extend([(tuple(v+8*i for v in f), owner+6*i, neighbour+6*i) for f,owner,neighbour in internal])
        all_boundary.extend([(tuple(v+8*i for v in f), owner+6*i, None) for f,owner,_ in boundary])
    faces = all_internal + all_boundary
    patches = []
    start = len(all_internal)
    for component in components:
        counts = (2,2,2,2,2,1,1) if component["component"] == "impeller" else (3,3,3,3)
        for name, count in zip(component["patchNames"], counts):
            patches.append(name + " {type " + component["patchTypes"][name] + "; nFaces " + str(count) + "; startFace " + str(start) + ";}")
            start += count
    if background_box:
        patches.append("backgroundBox {type patch; nFaces 0; startFace " + str(start) + ";}")
    if merged:
        for name in (case.geometry.ROTOR, case.geometry.STATOR):
            other = case.geometry.STATOR if name == case.geometry.ROTOR else case.geometry.ROTOR
            patches.append("pumpInterface_on_" + name + " {type nonConformalCyclic; nFaces 0; startFace " + str(start)
                + "; originalPatch " + name + "; neighbourPatch pumpInterface_on_" + other + "; transformType none;}")
            patches.append("nonConformalError_on_" + name + " {type nonConformalError; nFaces 0; startFace " + str(start) + "; originalPatch " + name + ";}")
    zones = [row["cellZone"] + " {type cellZone; cellLabels List<label> 6(" + " ".join(str(i+6*j) for i in range(6)) + ");}" for j,row in enumerate(components)]
    payloads = {
        "points": ("vectorField", str(len(all_points))+"\n(\n"+"\n".join(case.mesher.vector(p) for p in all_points)+"\n)\n"),
        "faces": ("faceList", str(len(faces))+"\n(\n"+"\n".join("3"+case.mesher.vector(f) for f,_,_ in faces)+"\n)\n"),
        "owner": ("labelList", str(len(faces))+"\n("+" ".join(str(o) for _,o,_ in faces)+")\n"),
        "neighbour": ("labelList", str(len(all_internal))+"\n("+" ".join(str(n) for _,_,n in all_internal)+")\n"),
        "boundary": ("polyBoundaryMesh", str(len(patches))+"\n(\n"+"\n".join(patches)+"\n)\n"),
        "cellZones": ("cellZoneList", str(len(zones))+"\n(\n"+"\n".join(zones)+"\n)\n")}
    mesh = root / "constant/polyMesh"; mesh.mkdir(parents=True,exist_ok=True)
    for name, (kind, body) in payloads.items():
        (mesh/name).write_bytes((case.base.foam_header(name,kind)+body).encode("ascii"))


def native_log(n):
    return ("Build : 12-86e126a7bc4d\nEnabling all (cell, face, edge, point) topology checks.\nEnabling all geometry checks.\n"
        + "    points: " + str(8*n) + "\n    faces: " + str(18*n) + "\n    internal faces: " + str(6*n) + "\n    cells: " + str(6*n)
        + "\nNumber of regions: " + str(n) + " (OK).\n"
        + "Min volume = 0.16666666666666666. Max volume = 0.16666666666666666.  Total volume = " + str(float(n)) + ".  Cell volumes OK.\n"
        + "Cell determinant (wellposedness) : minimum: 0.1 average: 1\n"
        + ("Source min/average/max coverage = 1/1/1\nTarget min/average/max coverage = 1/1/1\n" if n == 2 else "")
        + "Mesh OK.\nEnd\n")


def source_fixture(root, *, background_box=True):
    surfaces, proof, stack = small.fixture(root)
    stack.__enter__()
    source = root / "snappy-source"
    prepared = case.mesher.prepare(surfaces, proof, source)
    components, measures, selections = prepared["components"], [], []
    commands = case.geometry.commands(source, components)
    quality_indices = [i for i, args in enumerate(commands) if args[0] == "checkMesh"]
    for i, component in enumerate(components):
        write_mesh(source / component["caseDirectory"], [component], background_box=background_box)
        mesh=source/component["caseDirectory"]/"constant/polyMesh"
        zones=(mesh/"cellZones").read_bytes();(mesh/"cellZones").unlink()
        (mesh/"cellLevel").write_bytes((case.base.foam_header("cellLevel","labelList")+"6\n(2 2 2 2 2 2)\n").encode())
        case.geometry.snapshot_region_source(source,component)
        for role,count,first in (("pointRegionAddressing",8,0),("faceRegionAddressing",18,1),("cellRegionAddressing",6,0)):
            (mesh/role).write_bytes((case.base.foam_header(role,"labelList")+str(count)+"\n("+" ".join(str(j+first) for j in range(count))+")\n").encode())
        split_index=[j for j,args in enumerate(commands) if args[0]=="splitMeshRegions"][i]
        point=case.mesher.vector(component["insidePointEvidence"]["pointNativeUnits"])
        split_text="Build : 12-86e126a7bc4d\nFound point "+point+" in cell 0\nSubsetting region 0 containing point "+point+"\nEnd\n"
        (source/"logs").mkdir(exist_ok=True)
        (source/f"logs/{split_index:02d}-splitMeshRegions.log").write_bytes(split_text.encode())
        selection=case.geometry.retain_region_selection(source,component,split_text)
        selections.append(selection);(mesh/"cellZones").write_bytes(zones)
        measured = case.geometry.native_mesh(source / component["caseDirectory"], [component], native_log(1))
        measured.update(nativeMeshSnapshotRecords=case.geometry.snapshot_component(source, measured),
            nativeFilesMeasuredAtStageIndex=quality_indices[i], nativeFilesImmutableAfterMerge=False,regionSelectionEvidence=selection)
        measures.append(measured)
    write_mesh(source / "impeller-case", components, background_box=background_box)
    merged = case.geometry.native_mesh(source / "impeller-case", measures, native_log(2))
    (source / "logs").mkdir(exist_ok=True)
    stages = []
    for i, arguments in enumerate(commands):
        if arguments[0]=="splitMeshRegions":text=(source/f"logs/{i:02d}-splitMeshRegions.log").read_text()
        else:text = native_log(2 if i == quality_indices[-1] else 1) if arguments[0] == "checkMesh" else "Build : 12-86e126a7bc4d\nEnd\n"
        path = f"logs/{i:02d}-{arguments[0]}.log"; (source/path).write_bytes(text.encode())
        stages.append({"stageIndex": i, "program": arguments[0], "arguments": arguments, "exitCode": 0,
            "elapsedSeconds": 1., "logFile": case.read(source,path)[1]})
    runtime = {"schemaVersion": "untirta.frozen-rotor-snappy-runtime-identity.v1", "openFoamFoundationVersion":12,
        "packageVersion":"20250206", "officialDebSha256":case.geometry.surface.PACKAGE_SHA,
        "expectedSourceCommit":case.mesher.SOURCE_COMMIT, "mainApplicationWrites":False,
        "binaries": [{"program":p, "path":"/opt/openfoam12/platforms/linux64GccDPInt32Opt/bin/"+p,
            "bytes":1234, "sha256":"a"*64, "packageOwnership":"openfoam12: /opt/openfoam12/platforms/linux64GccDPInt32Opt/bin/"+p} for p in case.geometry.PROGRAMS]}
    (source/"snappy-runtime-identity.json").write_bytes(case.encoded(runtime))
    union = case.mesher.union.verify_output(source/"source-surface")
    report = {key: False for key in case.SOURCE_REPORT_KEYS}
    report.update(schemaVersion=case.geometry.SCHEMA,githubRepository="simcalc/PyAnsys",githubRunId="12345",githubSourceCommit="a"*40,
        status="APPROXIMATE_NATIVE_MESH_CHECKED_GEOMETRY_REVIEW_REQUIRED",stages=stages,
        limits={"maximumSeconds":900,"maximumCpus":1,"maximumMemoryBytes":case.geometry.surface.MAX_MEMORY,
            "maximumWorkspaceBytes":case.geometry.surface.MAX_WORKSPACE,"maximumLogBytes":case.geometry.MAX_LOG,
            "maximumActualComponentCells":case.geometry.MAX_COMPONENT_CELLS,"combinedCellsStrictlyBelow":case.geometry.MAX_COMBINED_CELLS,
            "combinedPointsStrictlyBelow":case.geometry.MAX_COMBINED_POINTS},
        preparationReportFile=case.read(source,case.mesher.REPORT)[1],components=measures,
        geometricApproximationMetrics=[case.geometry.approximation(m,u) for m,u in zip(measures,union["components"])],
        volumeMeshGenerated=True,meshQualityCheckedBySolver=True,nativeCoupledCoverageVerified=True,inputArtifactsImmutable=True,
        classroomMetresPerSourceCoordinateUnit=1,claimBoundary="Synthetic unit-test packet; never native research evidence.",
        runtimeIdentityFile=case.read(source,"snappy-runtime-identity.json")[1],mergedNativeMeshEvidence=merged,
        retainedNativeLogRecords=[row["logFile"] for row in stages],workspaceBytes=100000,elapsedSeconds=20.,regionSelectionEvidence=selections)
    (source/"snappy-execution.json").write_bytes(case.encoded(report))
    return source, stack


def merge_fields_and_add_constraints(output):
    for field in ("U","p","k","omega","nut"):
        expected = case.case_files("impeller")["0/"+field]
        prefix, rows = case.boundary_blocks(expected,field)
        rows.update(case.boundary_blocks(case.case_files("volute")["0/"+field],field)[1])
        rows.update({name:{"type":"nonConformalCyclic" if name.startswith("pumpInterface") else "nonConformalError"}
            for name in ("pumpInterface_on_interface-impeller-outlet","pumpInterface_on_interface-volute-inlet",
                "nonConformalError_on_interface-impeller-outlet","nonConformalError_on_interface-volute-inlet")})
        text = prefix+"boundaryField\n{\n"+"\n".join('"'+name+'" {'+" ".join(key+" "+value+";" for key,value in row.items())+"}" for name,row in rows.items())+"\n}\n"
        (output/"impeller-case/0"/field).write_bytes(text.encode())


class SnappyBaselineCaseTests(unittest.TestCase):
    def test_exact_physics_recipe_pressure_gravity_roles_and_background(self):
        for component in ("impeller","volute"):
            files = case.case_files(component)
            for field in ("U","p","k","omega","nut"):
                _, blocks = case.boundary_blocks(files["0/"+field],field)
                self.assertIn("backgroundBox",blocks); self.assertNotIn("defaultFaces",blocks)
                names = set().union(*case.base.PATCHES.values()) if component == "impeller" else case.base.PATCHES[component]
                self.assertEqual(set(blocks),names|{"backgroundBox"})
            self.assertNotIn("system/changeDictionaryDict",files)
        self.assertIn(b"pRef 300000;",case.case_files("impeller")["system/controlDict"])
        self.assertIn(b"fields true;",case.case_files("impeller")["system/createNonConformalCouplesDict"])
        self.assertIn(b"omega 1450 [rpm]",case.case_files("impeller")["constant/MRFProperties"])
        self.assertEqual(case.assumptions()["solverGravityMS2"],[0,0,0])
        self.assertNotIn("constant/g",case.case_files("impeller"))
        for pressure in (2339,101325,299999,True,float("nan")):
            with self.subTest(pressure=pressure),self.assertRaises((case.BaselineCaseIssue,case.base.CaseIssue)):
                case.case_files("impeller",pressure)

    def test_distinct_preparation_copies_actual_uncoupled_snapshots_and_all_bound_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,stack=source_fixture(root)
            with stack:
                output=root/"baseline";report=case.prepare(source,output)
                self.assertEqual(case.verify_output(output)["report"],report)
                self.assertEqual(len(report["sourceComponentFiles"]),12)
                self.assertTrue(all(row["path"].startswith("source-snappy/component-mesh-snapshots/") for row in report["sourceComponentFiles"]))
                source_paths={row["path"] for row in report["sourceEvidenceFileRecords"]}
                for component in ("impeller","volute"):
                    self.assertIn("source-snappy/region-selection/"+component+"/selection-report.json",source_paths)
                    self.assertIn("source-snappy/region-selection/"+component+"/source-polyMesh/cellLevel",source_paths)
                    for role in case.geometry.SELECTION_MAPS:
                        self.assertIn("source-snappy/region-selection/"+component+"/maps/"+role,source_paths)
                self.assertEqual([row[0] for row in report["nativeCommandArguments"]],["mergeMeshes","createNonConformalCouples","checkMesh","foamRun"])
                self.assertEqual(report["nativeCommandArguments"][2][-1],"-writeSets")
                self.assertEqual(case.base.native_list(output/"impeller-case/constant/polyMesh/points")[0],8)
                self.assertEqual(case.base.native_list(source/"impeller-case/constant/polyMesh/points")[0],16)
                for key in case.FALSE_FLAGS:self.assertIs(report[key],False)
                self.assertTrue(report["sourceNativeGeometryQualityVerified"])
                self.assertEqual(report["sourceSnappyExecutionReportSha256"],sha256((source/"snappy-execution.json").read_bytes()).hexdigest())
                merge_fields_and_add_constraints(output)
                (output/"impeller-case/constant/polyMesh/points").write_bytes(b"Synthetic working-mesh native rewrite; never valid geometry proof")
                self.assertEqual(case.verify_output(output,after_native_ncc=True)["report"],report)
                with self.assertRaises(case.BaselineCaseIssue):case.verify_output(output)

    def test_absent_empty_background_is_omitted_without_losing_physical_bcs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,stack=source_fixture(root,background_box=False)
            with stack:
                output=root/"baseline";case.prepare(source,output)
                report=case.verify_output(output)["report"]
                self.assertEqual(len(report["sourceComponentFiles"]),12)
                for folder,component in (("impeller-case","impeller"),("volute-case","volute")):
                    for field in ("U","p","k","omega","nut"):
                        _,blocks=case.boundary_blocks((output/folder/"0"/field).read_bytes(),field)
                        expected=set().union(*case.base.PATCHES.values()) if component=="impeller" else case.base.PATCHES[component]
                        self.assertEqual(set(blocks),expected)
                authored=case.authored_case_files(output/"source-snappy",report["components"])
                case.verify_fields_after_ncc(output,authored)
                path=output/"impeller-case/0/U";text=path.read_text()
                path.write_text(text.replace('    "blade"\n    {\n        type MRFnoSlip;\n    }\n',""))
                with self.assertRaises(case.BaselineCaseIssue):case.verify_fields_after_ncc(output,authored)

    def test_source_native_quality_ncc_or_scope_claims_cannot_be_promoted(self):
        for key,value in (("meshQualityCheckedBySolver",False),("nativeCoupledCoverageVerified",False),
                ("geometricApproximationQualified",True),("actualCfdRunVerified",True),("elapsedSeconds",901),
                ("status","APPROXIMATE_NATIVE_MESH_CORRECTION_REQUIRED")):
            with self.subTest(key=key),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source,stack=source_fixture(root)
                with stack:
                    path=source/"snappy-execution.json";value_report=json.loads(path.read_bytes());value_report[key]=value;path.write_bytes(case.encoded(value_report))
                    with self.assertRaises(case.BaselineCaseIssue):case.prepare(source,root/"baseline")
                    self.assertFalse((root/"baseline").exists())

    def test_current_source_snapshot_runtime_raw_log_and_geometry_recipe_are_required(self):
        paths=("component-mesh-snapshots/impeller/constant/polyMesh/points","snappy-runtime-identity.json",
            "logs/05-checkMesh.log",
            "source-surface-check/logs/00-impeller-surfaceCheck.log","impeller-case/system/snappyHexMeshDict")
        for name in paths:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source,stack=source_fixture(root)
                with stack:
                    path=source/name;path.write_bytes(path.read_bytes()+b"SYNTHETIC-CHANGED\n")
                    with self.assertRaises(Exception):case.prepare(source,root/"baseline")
                    self.assertFalse((root/"baseline").exists())

    def test_source_recorded_metrics_and_region_count_are_recomputed(self):
        for mutation in ("component-count","merged-regions","log-SHA","snapshot-SHA"):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source,stack=source_fixture(root)
                with stack:
                    path=source/"snappy-execution.json";report=json.loads(path.read_bytes())
                    if mutation=="component-count":report["components"][0]["cellCount"]+=1
                    elif mutation=="merged-regions":report["mergedNativeMeshEvidence"]["nativeQuality"]["polyMeshConnectedRegionCount"]=1
                    elif mutation=="log-SHA":report["stages"][0]["logFile"]["sha256"]="0"*64
                    else:report["components"][0]["nativeMeshSnapshotRecords"][0]["sha256"]="0"*64
                    path.write_bytes(case.encoded(report))
                    with self.assertRaises(case.BaselineCaseIssue):case.validate_inputs(source)

    def test_actual_region_maps_levels_and_ordered_proofs_are_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,stack=source_fixture(root)
            with stack:
                verified=case.validate_inputs(source)
                self.assertEqual(len(verified["report"]["stages"]),15)
                self.assertEqual(len(case.geometry.PROGRAMS),8)
                self.assertEqual([s["program"] for s in verified["report"]["stages"]].count("splitMeshRegions"),2)
                changes=[]
                level=source/"region-selection/impeller/source-polyMesh/cellLevel";raw=level.read_bytes()
                changes.append((level,raw,raw.replace(b"(2 2 2 2 2 2)",b"(1 2 2 2 2 2)")))
                mapping=source/"region-selection/impeller/maps/pointRegionAddressing";raw=mapping.read_bytes()
                changes.append((mapping,raw,raw.replace(b"(0 1 2 3 4 5 6 7)",b"(1 1 2 3 4 5 6 7)")))
                path=source/"region-selection/impeller/selection-report.json";raw=path.read_bytes();report=json.loads(raw)
                report["selectedRefinementLevelMeasuredFromSourceAddressing"]=1
                changes.append((path,raw,case.encoded(report)))
                path=source/"snappy-execution.json";raw=path.read_bytes()
                for mutation in ("top-region-proof","component-region-proof","missing-split-stage"):
                    report=json.loads(raw)
                    if mutation=="top-region-proof":report["regionSelectionEvidence"][0]["retainedGeneratedVolumeFraction"]=.9999
                    elif mutation=="component-region-proof":report["components"][0]["regionSelectionEvidence"]["mappedOrientedFacesExact"]=False
                    else:report["stages"].pop(3)
                    changes.append((path,raw,case.encoded(report)))
                for path,original,changed in changes:
                    with self.subTest(path=path,sha=sha256(changed).hexdigest()):
                        self.assertNotEqual(changed,original);path.write_bytes(changed)
                        with self.assertRaises(Exception):case.validate_inputs(source)
                        path.write_bytes(original)
                runtime=json.loads((source/"snappy-runtime-identity.json").read_bytes())
                runtime["binaries"]=[row for row in runtime["binaries"] if row["program"]!="splitMeshRegions"]
                with self.assertRaises(case.BaselineCaseIssue):case.validate_runtime(runtime)

    def test_explicit_post_ncc_semantics_rejects_changed_pressure_flow_or_untrusted_patch(self):
        for mutation in ("pressure","flow","unknown-patch","code","unit","initial-state"):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source,stack=source_fixture(root)
                with stack:
                    output=root/"baseline";case.prepare(source,output);merge_fields_and_add_constraints(output)
                    path=output/"impeller-case/0"/("U" if mutation=="flow" else "p")
                    text=path.read_text()
                    if mutation=="pressure":text=text.replace("p0 uniform 0;","p0 uniform 1;")
                    elif mutation=="flow":text=text.replace("volumetricFlowRate ","volumetricFlowRate 2 ")
                    elif mutation=="unknown-patch":text=text.replace("pumpInterface_on_interface-impeller-outlet","untrusted")
                    elif mutation=="code":text += '#codeStream "synthetic"\n'
                    elif mutation=="unit":text=text.replace("dimensions [0 2 -2", "dimensions [1 2 -2")
                    else:text=text.replace("internalField uniform 0;","internalField uniform 1;")
                    path.write_text(text)
                    with self.assertRaises(case.BaselineCaseIssue):case.verify_output(output,after_native_ncc=True)

    def test_ncc_native_constraint_values_must_be_explicit_and_reviewed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,stack=source_fixture(root)
            with stack:
                output=root/"baseline";case.prepare(source,output);merge_fields_and_add_constraints(output)
                path=output/"impeller-case/0/U";text=path.read_text().replace("type nonConformalCyclic;","type nonConformalCyclic; value uniform (0 0 0);")
                path.write_text(text)
                case.verify_output(output,after_native_ncc=True)
                path.write_text(text.replace("value uniform (0 0 0);","value uniform (99 0 0);"))
                with self.assertRaises(case.BaselineCaseIssue):case.verify_output(output,after_native_ncc=True)

    def test_native_constant_function_and_materialized_bc_defaults_preserve_physics(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,stack=source_fixture(root)
            with stack:
                output=root/"baseline";case.prepare(source,output);merge_fields_and_add_constraints(output)
                for folder in ("impeller-case","volute-case"):
                    path=output/folder/"0/U";text=path.read_text()
                    value=format(90/998.2,".17g")
                    text=text.replace("volumetricFlowRate "+value+";","volumetricFlowRate {type constant; value "+value+";}")
                    text=text.replace("type MRFnoSlip;","type MRFnoSlip; value uniform (0.0 0.0 0.0);")
                    path.write_text(text)
                    path=output/folder/"0/p";path.write_text(path.read_text().replace("type fixedFluxPressure;","type fixedFluxPressure; gradient uniform 0; value uniform 0;"))
                case.verify_output(output,after_native_ncc=True)
                path=output/"impeller-case/0/U";text=path.read_text()
                for changed in (text.replace("type constant;","type coded;"),text.replace("value "+value+";","value 1;"),
                        text.replace("type constant;","type table; values ((0 1));")):
                    path.write_text(changed)
                    with self.assertRaises(case.BaselineCaseIssue):case.verify_output(output,after_native_ncc=True)

    def test_pinned_sst_writer_defaults_are_exact_and_role_specific(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,stack=source_fixture(root)
            with stack:
                output=root/"baseline";case.prepare(source,output);merge_fields_and_add_constraints(output)
                authored={folder+"/"+path:raw for folder,component in
                    (("impeller-case","impeller"),("volute-case","volute")) for path,raw in case.case_files(component).items()}
                for folder in ("impeller-case","volute-case"):
                    path=output/folder/"0/omega"
                    path.write_text(path.read_text().replace("type omegaWallFunction;",
                        "type omegaWallFunction; beta1 0.075; blended false;"))
                    path=output/folder/"0/nut"
                    path.write_text(path.read_text().replace("type nutkWallFunction;",
                        "type nutkWallFunction; Cmu 0.09; kappa 0.41; E 9.8;"))
                case.verify_fields_after_ncc(output,authored)
                omega=output/"impeller-case/0/omega";omega_text=omega.read_text()
                nut=output/"impeller-case/0/nut";nut_text=nut.read_text()
                for path,good,bad in ((omega,omega_text,omega_text.replace("beta1 0.075;","beta1 0.076;")),
                        (omega,omega_text,omega_text.replace("blended false;","blended true;")),
                        (omega,omega_text,omega_text.replace("type fixedValue;","type fixedValue; beta1 0.075;")),
                        (nut,nut_text,nut_text.replace("Cmu 0.09;","Cmu 0.1;")),
                        (nut,nut_text,nut_text.replace("kappa 0.41;","kappa 0.42;")),
                        (nut,nut_text,nut_text.replace("E 9.8;","E 10;")),
                        (nut,nut_text,nut_text.replace("type calculated;","type calculated; Cmu 0.09;"))):
                    with self.subTest(mutation=bad):
                        path.write_text(bad)
                        with self.assertRaises(case.BaselineCaseIssue):case.verify_fields_after_ncc(output,authored)
                        path.write_text(good)

    def test_native_empty_calculated_list_requires_actual_empty_background_patch(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,stack=source_fixture(root)
            with stack:
                output=root/"baseline";case.prepare(source,output);merge_fields_and_add_constraints(output)
                authored={folder+"/"+path:raw for folder,component in
                    (("impeller-case","impeller"),("volute-case","volute")) for path,raw in case.case_files(component).items()}
                path=output/"impeller-case/0/nut";original=path.read_text()
                empty=original.replace('"backgroundBox" {type calculated; value uniform 0;}',
                    '"backgroundBox" {type calculated; value nonuniform List<scalar> 0\n(\n);}')
                self.assertNotEqual(empty,original);path.write_text(empty)
                case.verify_fields_after_ncc(output,authored)
                for bad in (empty.replace("List<scalar>","List<vector>"),
                        empty.replace("0\n(\n)","1\n(0\n)"),
                        empty.replace('"inlet" {type calculated; value uniform 0;}',
                            '"inlet" {type calculated; value nonuniform List<scalar> 0();}'),
                        empty.replace("type nonConformalCyclic;","type nonConformalCyclic; value nonuniform List<scalar> 0();")):
                    with self.subTest(mutation=bad):
                        path.write_text(bad)
                        with self.assertRaises(case.BaselineCaseIssue):case.verify_fields_after_ncc(output,authored)
                path.write_text(empty)
                boundary=output/"impeller-case/constant/polyMesh/boundary";text=boundary.read_text()
                boundary.write_text(text.replace("backgroundBox {type patch; nFaces 0;","backgroundBox {type patch; nFaces 1;"))
                with self.assertRaises(case.BaselineCaseIssue):case.verify_fields_after_ncc(output,authored)

    def test_immutable_physical_copies_pure_controls_and_source_snapshots_remain_required(self):
        for name in ("authored-physics/impeller-case/0/U","impeller-case/constant/MRFProperties",
                "source-snappy/component-mesh-snapshots/volute/constant/polyMesh/owner"):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source,stack=source_fixture(root)
                with stack:
                    output=root/"baseline";case.prepare(source,output)
                    path=output/name;path.write_bytes(path.read_bytes()+b"CHANGED")
                    with self.assertRaises(Exception):case.verify_output(output)

    def test_fixed_runner_scope_and_fresh_paths_precede_all_output_mutations(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,stack=source_fixture(root)
            with stack:
                for output in (source,source/"nested",root,root.parent/"escape"):
                    with self.subTest(output=output),self.assertRaises(Exception):case.prepare(source,output)
                with patch.dict(os.environ,{"RUNNER_TEMP":""}),self.assertRaises(Exception):case.prepare(source,root/"baseline")
                self.assertFalse((root/"baseline").exists())


if __name__ == "__main__":
    unittest.main()
