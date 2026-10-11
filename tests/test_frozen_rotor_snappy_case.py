"""Synthetic preparation checks; no fixture is native geometry/CFD evidence."""
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

TOOL = Path(__file__).resolve().parents[1] / "tools/frozen_rotor_snappy_case.py"
spec = importlib.util.spec_from_file_location("snappy_case", TOOL)
case = importlib.util.module_from_spec(spec)
spec.loader.exec_module(case)

CUBE_POINTS = np.array(((0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1)), dtype=float)
CUBE_FACES = np.array(((0,1,2),(0,2,3),(4,6,5),(4,7,6),(0,5,1),(0,4,5),
                      (3,2,6),(3,6,7),(0,3,7),(0,7,4),(1,5,6),(1,6,2)))
CUBE = CUBE_POINTS[CUBE_FACES]  # Inward winding; parity must not assume normals.


def stl(triangles):
    return case.union.source.STL_HEADER + struct.pack("<I", len(triangles)) + b"".join(
        struct.pack("<12fH", 0, 0, 1, *points.flatten(), 0) for points in triangles)


def log_for(component):
    regions = "\n".join(p["name"] + " " + str(p["triangleCount"]) for p in component["patches"])
    return ("Build : 12-" + case.SOURCE_COMMIT[:12] + "\nTriangles : " + str(component["triangleCount"])
        + "\nVertices : " + str(component["vertexCount"]) + "\nRegion Size\n-----------\n" + regions + "\n\n"
        + "Surface has no illegal triangles.\nSurface is closed. All edges connected to two faces.\n"
        + "Number of unconnected parts : 1\nNumber of zones (connected area with consistent normal) : 1\n"
        + "Checking self-intersection.\nSurface is not self-intersecting\n"
        + "Triangle quality (equilateral=1, collapsed=0):\n min 0.8 for triangle 0\n max 0.9 for triangle 1\nEdges:\nEnd\n").encode()


def fixture(root):
    """Patch only explicit source pins to tiny synthetic closed tagged cubes."""
    raw_input = root / "source-raw"
    raw_input.mkdir()
    pins, files, meshes = {}, [], []
    for component, original in case.union.STL_PINS.items():
        distribution = (2,2,2,2,2,1,1) if component == "impeller" else (3,3,3,3)
        patches, offset, pins[component] = [], 0, []
        for (zone,name,kind,_,_,_), count in zip(original, distribution):
            separated = CUBE + (3. if component == "volute" else 0.)
            raw = stl(separated[offset:offset+count]); offset += count
            digest = sha256(raw).hexdigest()
            pins[component].append((zone,name,kind,count,count,digest))
            filename = f"{component}-zone-{zone}.stl"
            (raw_input / filename).write_bytes(raw)
            row = {"name":filename,"bytes":len(raw),"sha256":digest,"triangleCount":count,"format":"binary-stl",
                   "sourceMesh":component+".msh.h5","zoneId":zone,"zoneName":name}
            files.append(row)
            patches.append({"zoneId":zone,"name":name,"zoneType":kind,"faceCount":count,"triangleCount":count,"stlFile":row})
        mesh = {key: [] for key in case.union.source.MESH_KEYS}
        source_name = component + ".msh.h5"
        size,digest = case.union.source.MESH_SOURCES[source_name]
        mesh.update(name=source_name,bytes=size,sha256=digest,sourceSha256AfterExtraction=digest,
            sourceUrl=f"https://media.githubusercontent.com/media/ansys/example-data/{case.union.source.PIN}/pyfluent/examples/pump-volute/{source_name}",
            boundaryZones=patches,signedEnclosedVolumeNativeUnitsCubed=-1.)
        meshes.append(mesh)
    topology = {key: None for key in case.union.source.REPORT_KEYS}
    topology.update(schemaVersion=case.union.source.SCHEMA,files=files,meshes=meshes,
        exampleDataRepository="https://github.com/ansys/example-data",exampleDataCommit=case.union.source.PIN,
        githubRepository=case.union.source.REPOSITORY,githubRunId="12345",githubRunAttempt="1",githubSourceCommit="a"*40,
        extractedAt="2026-10-12T00:00:00+00:00",h5pyVersion="3.16.0",hdf5Version="2.0.0",
        coordinateUnits="UNVERIFIED",sourceFileBytes=sum(n for n,_ in case.union.source.MESH_SOURCES.values()),
        derivedSurfaceBytes=sum(row["bytes"] for row in files))
    topology.update({key:False for key in case.union.source.FALSE_FLAGS})
    topology.update({key:True for key in case.union.source.TRUE_FLAGS})
    raw_topology = case.encoded(topology)
    (raw_input/"topology-report.json").write_bytes(raw_topology)
    stack=ExitStack()
    stack.enter_context(patch.dict(os.environ,{"RUNNER_TEMP":str(root)}))
    stack.enter_context(patch.object(case.union,"STL_PINS",pins))
    # Exercise the downstream contract separately from the production original
    # source fingerprint, which the union tool's own tests cover.
    stack.enter_context(patch.object(case.union,"SOURCE_REPORT_BYTES",len(raw_topology)))
    stack.enter_context(patch.object(case.union,"SOURCE_REPORT_SHA256",sha256(raw_topology).hexdigest()))
    stack.enter_context(patch.object(case.union,"SOURCE_TOPOLOGY_SHA256",case.union.topology_fingerprint(topology)))
    surfaces=root/"surface-union"
    case.union.prepare(raw_input,surfaces,runner_temp=root)
    verified=case.union.verify_output(surfaces,runner_temp=root)
    proof=root/"surface-proof"
    proof.mkdir()
    for name in (case.union.REPORT,case.union.SOURCE_REPORT):
        (proof/name).write_bytes((surfaces/name).read_bytes())
    binaries=[{"path":"/opt/openfoam12/platforms/linux64GccDPInt32Opt/bin/surfaceCheck","bytes":1234,"sha256":"b"*64,"program":"surfaceCheck"}]
    runtime={"schemaVersion":"untirta.frozen-rotor-surface-runtime-identity.v1","openFoamFoundationVersion":12,
        "packageVersion":"20250206","officialDebSha256":case.surface_check.PACKAGE_SHA,"expectedSourceCommit":case.SOURCE_COMMIT,
        "packageOwnership":"openfoam12: "+binaries[0]["path"],"binaries":binaries,"mainApplicationWrites":False}
    runtime_raw=case.encoded(runtime)
    (proof/"surface-runtime-identity.json").write_bytes(runtime_raw)
    report={key:None for key in case.SURFACE_PROOF_KEYS}
    report.update(schemaVersion=case.surface_check.SCHEMA,checkedAt="2026-10-12T00:00:00+00:00",githubRepository="simcalc/PyAnsys",
        githubRunId="12345",githubSourceCommit="a"*40,status="NATIVE_SURFACE_GEOMETRY_CHECKED",
        limits={"maximumSeconds":case.surface_check.MAX_SECONDS,"maximumCpus":1,"maximumMemoryBytes":case.surface_check.MAX_MEMORY,
            "maximumWorkspaceBytes":case.surface_check.MAX_WORKSPACE,"maximumLogBytes":case.surface_check.MAX_LOG},
        sourceFileRecords=[verified["reportFile"],case.read(surfaces,case.union.SOURCE_REPORT)[1],*(row["surfaceFile"]for row in verified["components"])],
        authoredCommands=[],components=[],stages=[],nativeSurfaceCheckExecuted=True,originalSurfaceInputsImmutable=True,
        executionSurfaceInputsImmutable=True,nativeSurfaceGeometryAccepted=True,nativeCoordinateUnitsVerified=False,
        classroomMetresPerSourceCoordinateUnit=1,nativeNodeIdentityEquivalent=False,fluidVolumeConnectivityVerified=False,
        vertexManifoldVerified=False,volumeMeshGenerated=False,meshQualityCheckedBySolver=False,numericalQualityVerified=False,
        qualifiedBaseline=False,npsh3Qualified=False,simulationExecuted=False,actualCfdRunVerified=False,mainApplicationWrites=False,
        primarySource=case.surface_check.SOURCE_URL,claimBoundary=case.SURFACE_CLAIM,
        runtimeIdentityFile=case.union.file_row("surface-runtime-identity.json",runtime_raw),retainedNativeLogRecords=[],workspaceBytes=12345,elapsedSeconds=2.)
    for i,component in enumerate(verified["components"]):
        name=component["component"]
        folder=proof/"components"/name;folder.mkdir(parents=True)
        (folder/(name+"-union.obj")).write_bytes((surfaces/(name+"-union.obj")).read_bytes())
        log=log_for(component);log_name=f"logs/{i:02d}-{name}-surfaceCheck.log"
        (proof/"logs").mkdir(exist_ok=True);(proof/log_name).write_bytes(log)
        arguments=["surfaceCheck","-checkSelfIntersection",f"/tmp/synthetic/components/{name}/{name}-union.obj"]
        row=case.union.file_row(log_name,log)
        report["authoredCommands"].append(arguments)
        report["stages"].append({"program":"surfaceCheck","component":name,"arguments":arguments,"exitCode":0,"elapsedSeconds":1.,"logFile":row})
        report["retainedNativeLogRecords"].append(row)
        report["components"].append({"component":name,"sourceSurfaceFile":component["surfaceFile"],"nativeGeometry":case.surface_check.parse_log(log.decode(),component)})
    (proof/"surface-check-execution.json").write_bytes(case.encoded(report))
    return surfaces,proof,stack


class SnappyPreparationTests(unittest.TestCase):
    def test_ray_parity_is_winding_independent_and_edge_ambiguity_is_visible(self):
        inside=(.3,.4,.5);direction=(1.,2**.5,3**.5)
        for triangles in (CUBE,CUBE[:,::-1]):
            row=case.ray_parity(inside,triangles,direction)
            self.assertTrue(row["insideByOddParity"])
            self.assertEqual(row["ambiguousEdgeOrVertexHitCount"],0)
            self.assertFalse(case.ray_parity((2.,2.,2.),triangles,direction)["insideByOddParity"])
        self.assertGreater(case.ray_parity((.5,.5,.5),CUBE,(1.,1.,0.))["ambiguousEdgeOrVertexHitCount"],0)
        self.assertGreater(case.ray_parity((.2,.3,0.),CUBE,(1.,1.,0.))["ambiguousCoplanarTriangleCount"],0)
        with self.assertRaises(case.SnappyIssue):
            case.ray_parity((.2,.3,.4),CUBE,(0.,0.,0.))

    def test_nearest_triangle_distance_includes_face_edge_and_vertex_regions(self):
        for point,expected in (((.5,.5,.5),.5),((2.,.3,.4),1.),((2.,2.,2.),3**.5),((0.,0.,0.),0.)):
            with self.subTest(point=point):
                self.assertAlmostEqual(case.nearest_triangle_distance(point,CUBE),expected)

    def test_seed_is_measured_inside_surface_and_avoids_refined_grid_planes(self):
        background=case.background_mesh(CUBE)
        seed=case.find_inside_point(CUBE,background)
        self.assertEqual(seed["candidateCountEvaluated"],48)
        self.assertTrue(all(row["insideByOddParity"] for row in seed["rayParityEvidence"]))
        self.assertGreater(seed["nearestTriangleDistanceNativeUnits"],0)
        self.assertGreater(case.grid_clearance(seed["pointNativeUnits"],background),1e-7)
        self.assertFalse(seed["originalVolumeCellCentreClaimed"])
        self.assertFalse(seed["nativeInsidePointTestExecuted"])
        index=seed["sourceTriangleIndex"]
        measured=CUBE[index].mean(axis=0)+np.array(seed["sourceTriangleUnitNormal"])*seed["signedNormalOffsetNativeUnits"]
        np.testing.assert_array_equal(measured,seed["pointNativeUnits"])

    def test_no_inside_candidate_is_a_diagnostic_not_an_invented_point(self):
        with patch.object(case,"ray_parity",return_value={"insideByOddParity":False,"ambiguousEdgeOrVertexHitCount":0,"ambiguousCoplanarTriangleCount":0}):
            with self.assertRaisesRegex(case.SnappyIssue,"No robust interior seed"):
                case.find_inside_point(CUBE,case.background_mesh(CUBE))
        with self.assertRaises(case.SnappyIssue):
            case.find_inside_point(np.zeros((1,3,3)),case.background_mesh(CUBE))

    def test_scale_is_explicit_and_candidates_track_measured_geometry(self):
        for scale in (.001,1000.):
            triangles=CUBE*scale
            seed=case.find_inside_point(triangles,case.background_mesh(triangles))
            self.assertTrue(np.all(np.array(seed["pointNativeUnits"])>0))
            self.assertTrue(np.all(np.array(seed["pointNativeUnits"])<scale))
            self.assertGreater(seed["nearestTriangleDistanceNativeUnits"],1e-8*scale)

    def test_exact_geometry_recipe_stages_tags_zones_and_no_flow_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);surfaces,proof,stack=fixture(root)
            with stack:
                output=root/"snappy"
                report=case.prepare(surfaces,proof,output)
                self.assertEqual(case.verify_output(output)["report"],report)
                self.assertEqual(len(report["nativeCommandArguments"]),15)
                self.assertEqual([row[0]for row in report["nativeCommandArguments"]],
                    ["blockMesh","surfaceFeatures","snappyHexMesh","splitMeshRegions","topoSet","checkMesh"]*2+["mergeMeshes","createNonConformalCouples","checkMesh"])
                self.assertEqual(report["nativeCommandArguments"][4][-1],"-constant")
                self.assertTrue(all(row[-1] == "-writeSets" for row in report["nativeCommandArguments"] if row[0] == "checkMesh"))
                self.assertEqual(report["nativeCommandArguments"][12],
                    ["mergeMeshes","-case",str(output/"impeller-case"),"-addCases",'("'+str(output/"volute-case")+'")',"-overwrite"])
                self.assertFalse((output/"impeller-case/0").exists())
                for flag in case.FALSE_FLAGS:
                    self.assertIs(report[flag],False)
                self.assertTrue(report["sourceSurfaceGeometryPrerequisiteVerified"])
                self.assertEqual(report["components"][0]["backgroundMesh"],report["components"][1]["backgroundMesh"])
                self.assertLess(report["components"][0]["backgroundMesh"]["minimum"][0],0)
                self.assertGreater(report["components"][0]["backgroundMesh"]["maximum"][0],4)
                for component in report["components"]:
                    folder=output/component["caseDirectory"]
                    snappy=(folder/"system/snappyHexMeshDict").read_text()
                    self.assertIn("castellatedMesh true; snap false; addLayers false;",snappy)
                    self.assertIn("level (2 2)",snappy)
                    self.assertIn("mode distance; levels ((",snappy)
                    self.assertIn("insidePoint ",snappy)
                    self.assertNotIn("locationInMesh",snappy)
                    self.assertIn("minDeterminant 0.001",snappy)
                    self.assertNotIn("relaxed",snappy)
                    self.assertTrue(all(name+" {name "+name+";}"in snappy for name in component["patchNames"]))
                    self.assertIn(component["cellZone"],(folder/"system/topoSetDict").read_text())
                    self.assertIn("boundary\n(\nbackgroundBox",(folder/"system/blockMeshDict").read_text())
                self.assertIn("fields false;",(output/"impeller-case/system/createNonConformalCouplesDict").read_text())
                (output/"impeller-case/constant/polyMesh").mkdir()
                (output/"impeller-case/constant/polyMesh/points").write_text("synthetic native extra; not accepted as proof")
                self.assertEqual(case.verify_output(output)["report"],report)

    def test_authored_geometry_recipe_or_report_mutation_prevents_verification(self):
        for mutation in ("dictionary","seed","command","CFD"):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);surfaces,proof,stack=fixture(root)
                with stack:
                    output=root/"snappy";case.prepare(surfaces,proof,output)
                    if mutation=="dictionary":
                        path=output/"impeller-case/system/snappyHexMeshDict";path.write_bytes(path.read_bytes()+b"#codeStream\n")
                    else:
                        path=output/case.REPORT;report=json.loads(path.read_bytes())
                        if mutation=="seed":report["components"][0]["insidePointEvidence"]["pointNativeUnits"]=[.5,.5,.5]
                        if mutation=="command":report["nativeCommandArguments"].append(["foamRun"])
                        if mutation=="CFD":report["actualCfdRunVerified"]=True
                        path.write_bytes(case.encoded(report))
                    with self.assertRaises(case.SnappyIssue):case.verify_output(output)

    def test_native_surface_proof_must_match_actual_logs_and_checked_surface_bytes(self):
        for mutation in ("log","surface","runtime","report","unknown-key","claim","timing","workspace","volume"):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);surfaces,proof,stack=fixture(root)
                with stack:
                    if mutation=="log":(proof/"logs/00-impeller-surfaceCheck.log").write_bytes(b"synthetic incomplete\n")
                    elif mutation=="surface":(proof/"components/impeller/impeller-union.obj").write_bytes(b"synthetic changed\n")
                    elif mutation=="runtime":(proof/"surface-runtime-identity.json").write_bytes(b'{}')
                    else:
                        path=proof/"surface-check-execution.json";report=json.loads(path.read_bytes())
                        if mutation=="report":report["nativeSurfaceGeometryAccepted"]=False
                        elif mutation=="claim":report["claimBoundary"]="synthetic broader unbound claim"
                        elif mutation=="timing":report["elapsedSeconds"]=case.surface_check.MAX_SECONDS+1
                        elif mutation=="workspace":report["workspaceBytes"]=case.surface_check.MAX_WORKSPACE+1
                        elif mutation=="volume":report["fluidVolumeConnectivityVerified"]=True
                        else:report["secret"]="synthetic forbidden"
                        path.write_bytes(case.encoded(report))
                    with self.assertRaises((case.SnappyIssue,case.union.source.PackageIssue)):
                        case.prepare(surfaces,proof,root/"snappy")
                    self.assertFalse((root/"snappy").exists())

    def test_paths_are_fresh_fixed_runner_scope_and_prohibit_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);surfaces,proof,stack=fixture(root)
            with stack:
                for output in (root,surfaces,proof,surfaces/"nested",root.parent/"escape"):
                    with self.subTest(output=output),self.assertRaises((case.SnappyIssue,case.union.source.PackageIssue)):
                        case.prepare(surfaces,proof,output)
                for value in ("../secret","/tmp/secret","a//b","a/./b","a\\b"):
                    with self.subTest(value=value),self.assertRaises(case.SnappyIssue):case.safe_relative(value)


if __name__=="__main__":
    unittest.main()
