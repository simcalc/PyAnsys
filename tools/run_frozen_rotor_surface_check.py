"""Run only bounded Foundation-12 surface geometry preflight on the FREE worker.

The source-bound float32-derived surfaces are not native volume meshes. Native
surface diagnostics, volume mesh admission and CFD qualification remain separate.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_openfoam_case as base
import run_frozen_rotor_phase as native

SCHEMA = "untirta.frozen-rotor-native-surface-check.v1"
UNION_SCHEMA = "untirta.frozen-rotor-tagged-surface-union.v1"
SOURCE_COMMIT = "86e126a7bc4dbfaed949bbc58c9f0fdd67acc059"
SOURCE_URL = ("https://github.com/OpenFOAM/OpenFOAM-12/blob/" + SOURCE_COMMIT
              + "/applications/utilities/surface/surfaceCheck/surfaceCheck.C")
PACKAGE_SHA = "b3dcd9a636a5a827bf43ffcf0156922ba6db492123f035eae1046ee759602dd1"
MAX_SECONDS, MAX_MEMORY, MAX_WORKSPACE = 300, 4*1024**3, 1024**3
MAX_LOG, MAX_FILE, MAX_PATHS = 4*1024**2, 128*1024**2, 1024
COMPONENTS = ("impeller", "volute")
FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


class SurfaceCheckIssue(ValueError):
    pass


def require(condition, action):
    if not condition:
        raise SurfaceCheckIssue(action)


def descriptor(path, relative=None, limit=MAX_FILE, *, empty=False):
    path = Path(path)
    require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)()
            and path.is_file(), "A regular immutable surface evidence file is required.")
    stat = path.stat()
    require((0 if empty else 1) <= stat.st_size <= limit, "A surface evidence file exceeds its byte bound.")
    raw = path.read_bytes()
    after = path.stat()
    require(len(raw) == stat.st_size and (stat.st_size, stat.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
            "Surface evidence changed while reading.")
    return raw, {"path": relative or path.name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def scope_path(value, *, fresh=False):
    require(os.environ.get("RUNNER_TEMP"), "Use the authorized runner's existing RUNNER_TEMP.")
    root = base.regular_directory(os.environ["RUNNER_TEMP"])
    path = Path(value).absolute()
    require(path != root and path.resolve().is_relative_to(root.resolve()), "Surface paths must stay inside RUNNER_TEMP.")
    for item in (path, *path.parents):
        require(not item.is_symlink() and not getattr(item, "is_junction", lambda: False)(), "Surface paths cannot contain links or junctions.")
        if item == root:
            break
    require(not fresh or not path.exists(), "Use a fresh separate surface-check output directory.")
    return path


def context_guard():
    native.context_guard()
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    require(event_path, "Verify the actual manual public GitHub event before creating output.")
    event = json.loads(descriptor(event_path, limit=1024**2)[0])
    repo = event.get("repository", {})
    require(repo.get("full_name") == "simcalc/PyAnsys" and repo.get("private") is False
            and repo.get("visibility", "public") == "public", "Use only the independent public FREE repository; no paid fallback.")


def workspace_size(value):
    root = base.regular_directory(value)
    total, paths = 0, 0
    for path in root.rglob("*"):
        paths += 1
        require(paths <= MAX_PATHS and len(path.relative_to(root).parts) <= 8, "The surface workspace path bound was reached.")
        require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)(), "Surface output cannot contain links or junctions.")
        require(path.is_dir() or path.is_file(), "Only ordinary native surface files and directories are allowed.")
        if path.is_file():
            size = path.stat().st_size
            require(size <= MAX_FILE, "A native surface output exceeds 128 MiB.")
            total += size
            require(total <= MAX_WORKSPACE, "The isolated surface workspace exceeds 1 GiB.")
    return total


def child_limits():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY, MAX_MEMORY))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE, MAX_FILE))
    # A fresh child uses exactly one currently allowed CPU, without moving or
    # terminating any existing process or changing the runner's global policy.
    allowed = os.sched_getaffinity(0)
    require(allowed, "A native child requires an allowed CPU.")
    os.sched_setaffinity(0, {min(allowed)})


def runtime_identity(deadline):
    require(time.monotonic() < deadline, "The surface preflight deadline was reached before runtime binding.")
    env = native.child_environment()
    def query(arguments):
        remaining = deadline-time.monotonic()
        require(remaining > 0, "The surface deadline was reached during package binding.")
        result = subprocess.run(arguments, env=env, capture_output=True, text=True,
                                timeout=min(10, remaining), check=True)
        require(len(result.stdout) <= 8192 and len(result.stderr) <= 8192, "Package identity output exceeds its bound.")
        return result.stdout
    require(query(["dpkg-query", "--show", "--showformat=${Version}", "openfoam12"]) == "20250206",
            "Use the exact checksum-pinned official Foundation-12 package 20250206.")
    executable = shutil.which("surfaceCheck")
    require(executable, "The actual official surfaceCheck executable is required.")
    path = Path(executable).resolve()
    require(path.is_relative_to(Path("/opt/openfoam12")), "surfaceCheck must resolve inside the official package.")
    ownership = query(["dpkg-query", "--search", str(path)])
    require(ownership.strip() == "openfoam12: " + str(path), "The actual surfaceCheck binary must belong to openfoam12.")
    raw, row = descriptor(path)
    require(time.monotonic() < deadline, "The surface deadline was reached after runtime binding.")
    return {"schemaVersion": "untirta.frozen-rotor-surface-runtime-identity.v1", "openFoamFoundationVersion": 12,
            "packageVersion": "20250206", "officialDebSha256": PACKAGE_SHA,
            "expectedSourceCommit": SOURCE_COMMIT, "packageOwnership": ownership.strip(),
            "binaries": [{**row, "path": str(path), "program": "surfaceCheck"}], "mainApplicationWrites": False}


def commands(output):
    root = Path(output).absolute()
    return [["surfaceCheck", "-checkSelfIntersection", str(root/"components"/name/(name+"-union.obj"))]
            for name in COMPONENTS]


def parse_log(text, component):
    """Interpret complete pinned-source diagnostics, never native exit alone."""
    require(isinstance(text, str) and len(text.encode("utf-8")) <= MAX_LOG, "Use a bounded actual native surface log.")
    require(re.search(r"(?m)^End\s*$", text) and not re.search(r"FOAM FATAL|Segmentation fault|Floating point exception", text, re.I),
            "Complete fatal-free native surface diagnostics with End are required.")
    def one(pattern):
        matches = re.findall(pattern, text, flags=re.M)
        require(len(matches) == 1, "An exact unambiguous native surface diagnostic is required: " + pattern[:64])
        return matches[0]
    require(one(r"^Build\s*:\s*(\S+)\s*$") == "12-"+SOURCE_COMMIT[:12], "The actual native build must match the reviewed Foundation-12 source.")
    triangles = int(one(r"^Triangles\s*:\s*(\d+)\s*$"))
    vertices = int(one(r"^Vertices\s*:\s*(\d+)\s*$"))
    require(triangles == component["triangleCount"] and vertices == component["vertexCount"],
            "Native surface counts must match the actual bound whole-component union.")
    regions = one(r"^Region[ \t]+Size[ \t]*\r?\n[- \t]+\r?\n([\s\S]*?)\r?\n[ \t]*\r?\n")
    rows = [line.split() for line in regions.splitlines() if line.strip()]
    require(all(len(row)==2 and row[1].isdigit() for row in rows), "Native surface region counts must be explicit.")
    actual = {row[0]: int(row[1]) for row in rows}
    expected = {row["name"]: row["triangleCount"] for row in component["patches"]}
    require(len(actual) == len(rows) and actual == expected, "Native patch tags and triangle counts must match the bound OBJ groups.")
    illegal = one(r"^Surface has (no illegal triangles\.|\d+ illegal triangles\.)\s*$")
    illegal_count = 0 if illegal == "no illegal triangles." else int(illegal.split()[0])
    closed = len(re.findall(r"^Surface is closed\. All edges connected to two faces\.\s*$", text, re.M))
    open_branch = len(re.findall(r"^Surface is not closed since not all edges connected to two faces:\s*$", text, re.M))
    require(closed+open_branch == 1, "The actual native edge-manifold/closure branch is required.")
    single = int(one(r"^\s*connected to one face\s*:\s*(\d+)\s*$")) if open_branch else 0
    multiple = int(one(r"^\s*connected to >2 faces\s*:\s*(\d+)\s*$")) if open_branch else 0
    parts = int(one(r"^Number of unconnected parts\s*:\s*(\d+)\s*$"))
    normal_zones = int(one(r"^Number of zones \(connected area with consistent normal\)\s*:\s*(\d+)\s*$"))
    require(len(re.findall(r"^Checking self-intersection\.\s*$", text, re.M)) == 1, "The authored native self-intersection test must actually run.")
    intersections = one(r"^Surface is (not self-intersecting|self-intersecting at \d+ locations\.)\s*$")
    intersection_count = 0 if intersections == "not self-intersecting" else int(intersections.split()[2])
    quality = re.search(r"Triangle quality \(equilateral=1, collapsed=0\):(.*?)^Edges:\s*$", text, re.M|re.S)
    require(quality, "The actual native triangle-quality range is required.")
    minima = re.findall(r"^\s*min ("+FLOAT+r") for triangle \d+\s*$", quality[1], re.M)
    maxima = re.findall(r"^\s*max ("+FLOAT+r") for triangle \d+\s*$", quality[1], re.M)
    require(len(minima)==len(maxima)==1, "Actual triangle quality extrema must be unique.")
    q_min, q_max = float(minima[0]), float(maxima[0])
    require(math.isfinite(q_min) and math.isfinite(q_max) and 0 <= q_min <= q_max <= 1, "Native triangle quality extrema must be finite and in their defined domain.")
    reliability_warning = "This might give problems in self-intersection testing later on." in text
    # Connected boundary shells are not connected fluid volumes. Multiple
    # consistently oriented closed shells can enclose legitimate obstacles.
    accepted = (illegal_count == single == multiple == intersection_count == 0 and closed == 1
                and parts > 0 and normal_zones == parts and q_min > 0 and not reliability_warning)
    return {"triangleCount": triangles, "vertexCount": vertices, "patchTriangleCounts": actual,
            "illegalTriangleCount": illegal_count, "singleFaceEdgeCount": single, "moreThanTwoFaceEdgeCount": multiple,
            "connectedPartCount": parts, "consistentNormalZoneCount": normal_zones, "selfIntersectionLocationCount": intersection_count,
            "minimumTriangleQuality": q_min, "maximumTriangleQuality": q_max, "nativeEndVerified": True,
            "nativeBuildSourceCommit": SOURCE_COMMIT, "nativeEdgeManifoldVerified": closed == 1,
            "nativeSelfIntersectionTestPassed": intersection_count == 0 and not reliability_warning,
            "nativeSelfIntersectionReliabilityWarning": reliability_warning,
            "boundaryShellConnectivityOnly": True, "fluidVolumeConnectivityVerified": False, "vertexManifoldVerified": False,
            "nativeSurfaceGeometryAccepted": accepted}


def execute(arguments, log_path, output, deadline, runtime):
    authored = commands(output)
    require(arguments in authored, "Only the two authored surfaceCheck self-intersection commands may execute.")
    index = authored.index(arguments); component = COMPONENTS[index]
    expected = Path(output)/"logs"/(f"{index:02d}-{component}-surfaceCheck.log")
    require(Path(log_path).absolute() == expected.absolute() and not expected.exists(), "Use the fresh fixed immutable component log.")
    row = runtime["binaries"][0]
    require(row["program"] == "surfaceCheck", "Only the bound actual surfaceCheck binary may execute.")
    executable = Path(row["path"])
    require(executable.resolve().is_relative_to(Path("/opt/openfoam12")), "Execute only the official surfaceCheck binary.")
    raw, record = descriptor(executable)
    require(record["bytes"] == row["bytes"] and record["sha256"] == row["sha256"], "The actual native binary changed after binding.")
    require(time.monotonic() < deadline, "The serial surface deadline was reached before native startup.")
    workspace_size(output)
    process, total, started = None, 0, time.monotonic()
    next_progress = started+30
    selector = selectors.DefaultSelector()
    try:
        with expected.open("xb") as destination:
            process = subprocess.Popen([str(executable), *arguments[1:]], cwd=str(expected.parent.parent/"components"/component),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=native.child_environment(),
                preexec_fn=child_limits, start_new_session=True, bufsize=0)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                now = time.monotonic()
                require(now < deadline, "The bounded native surface preflight timed out.")
                workspace_size(output)
                if now >= next_progress:
                    print(json.dumps({"component":component,"status":"NativeSurfaceCheckRunning","elapsedSeconds":round(now-started,1),
                                      "simulationExecuted":False,"mainApplicationWrites":False}),flush=True)
                    next_progress = now+30
                for key, _ in selector.select(timeout=.25):
                    chunk = os.read(key.fileobj.fileno(),4096)
                    if not chunk:
                        selector.unregister(key.fileobj); continue
                    remaining=MAX_LOG-total
                    destination.write(chunk[:remaining]); destination.flush(); total += min(len(chunk),remaining)
                    require(len(chunk)<=remaining,"The immutable native surface log reached its 4 MiB bound.")
            code=process.wait(timeout=3)
            workspace_size(output)
            require(time.monotonic()<deadline,"The native surface deadline was reached at process completion.")
    finally:
        if process is not None:
            native.stop_owned(process); process.stdout.close()
        selector.close()
    raw, record=descriptor(expected,"logs/"+expected.name,MAX_LOG)
    return {"program":"surfaceCheck","component":component,"arguments":arguments,"exitCode":code,
            "elapsedSeconds":round(time.monotonic()-started,3),"logFile":record},raw.decode("utf-8",errors="strict")


def run(surface_directory, output_directory):
    started=time.monotonic(); deadline=started+MAX_SECONDS
    context_guard()
    source=scope_path(surface_directory); output=scope_path(output_directory,fresh=True)
    require(not output.resolve().is_relative_to(source.resolve()) and not source.resolve().is_relative_to(output.resolve()),
            "Surface source and execution output must be separate directories.")
    import frozen_rotor_surface_union as union
    verified=union.verify_output(source)
    require(verified["report"]["schemaVersion"]==UNION_SCHEMA and [row["component"] for row in verified["components"]]==list(COMPONENTS),
            "The exact source-bound impeller and volute surface-union contract is required.")
    output.mkdir(parents=True); (output/"logs").mkdir(); (output/"components").mkdir()
    bound={}; copied={}
    for name in ("surface-union-report.json","source-topology-report.json"):
        raw,row=descriptor(source/name,name,base.inventory.MAX_REPORT_BYTES)
        bound[name]=row; base.write_exclusive(output/name,raw); copied[name]=row
    for component in verified["components"]:
        name=component["component"]; relative=name+"-union.obj"
        require(component["surfaceFile"]["path"]==relative,"Only the fixed whole-component OBJ union may be checked.")
        raw,row=descriptor(source/relative,relative)
        require(row==component["surfaceFile"],"The actual source union bytes must match the verified report.")
        bound[relative]=row; folder=output/"components"/name; folder.mkdir()
        base.write_exclusive(folder/relative,raw)
        copied[f"components/{name}/{relative}"]={**row,"path":f"components/{name}/{relative}"}
    report={"schemaVersion":SCHEMA,"checkedAt":datetime.now(timezone.utc).isoformat(),"githubRepository":"simcalc/PyAnsys",
            "githubRunId":os.environ.get("GITHUB_RUN_ID"),"githubSourceCommit":os.environ.get("GITHUB_SHA"),
            "status":"NATIVE_SURFACE_CHECK_REQUIRED","limits":{"maximumSeconds":MAX_SECONDS,"maximumCpus":1,
            "maximumMemoryBytes":MAX_MEMORY,"maximumWorkspaceBytes":MAX_WORKSPACE,"maximumLogBytes":MAX_LOG},
            "sourceFileRecords":list(bound.values()),"authoredCommands":commands(output),"components":[],"stages":[],
            "nativeSurfaceCheckExecuted":False,
            "originalSurfaceInputsImmutable":False,"executionSurfaceInputsImmutable":False,"nativeSurfaceGeometryAccepted":False,
            "nativeCoordinateUnitsVerified":False,"classroomMetresPerSourceCoordinateUnit":1,"nativeNodeIdentityEquivalent":False,
            "fluidVolumeConnectivityVerified":False,"vertexManifoldVerified":False,
            "volumeMeshGenerated":False,"meshQualityCheckedBySolver":False,"numericalQualityVerified":False,
            "qualifiedBaseline":False,"npsh3Qualified":False,"simulationExecuted":False,"actualCfdRunVerified":False,
            "mainApplicationWrites":False,"primarySource":SOURCE_URL,
            "claimBoundary":"Native edge topology, orientation and edge-based self-intersection checks on source-bound float32-derived component surfaces only. No volume mesh, numerical baseline, physical CFD, native-node equivalence or physical-unit validation."}
    current="runtime-identity"
    try:
        runtime=runtime_identity(deadline)
        base.write_exclusive(output/"surface-runtime-identity.json",base.inventory.bounded_json(runtime))
        report["runtimeIdentityFile"]=descriptor(output/"surface-runtime-identity.json",limit=base.inventory.MAX_REPORT_BYTES)[1]
        for index,component in enumerate(verified["components"]):
            current=component["component"]
            stage,text=execute(report["authoredCommands"][index],output/"logs"/f"{index:02d}-{current}-surfaceCheck.log",output,deadline,runtime)
            report["stages"].append(stage)
            require(stage["exitCode"]==0,"surfaceCheck returned native exit "+str(stage["exitCode"]))
            report["components"].append({"component":current,"sourceSurfaceFile":component["surfaceFile"],"nativeGeometry":parse_log(text,component)})
        report["nativeSurfaceCheckExecuted"]=True
        require(all(descriptor(source/name,name,row["bytes"])[1]==row for name,row in bound.items()),"An original source surface/report changed during native preflight.")
        union.verify_output(source)
        report["originalSurfaceInputsImmutable"]=True
        require(all(descriptor(output/name,name,row["bytes"])[1]==row for name,row in copied.items()),"A hash-bound execution surface/report changed during native preflight.")
        report["executionSurfaceInputsImmutable"]=True
        workspace_size(output)
        require(time.monotonic()<deadline,"The surface deadline was reached before final acceptance.")
        accepted=all(row["nativeGeometry"]["nativeSurfaceGeometryAccepted"] for row in report["components"])
        report.update(nativeSurfaceGeometryAccepted=accepted,status="NATIVE_SURFACE_GEOMETRY_CHECKED" if accepted else "NATIVE_SURFACE_CORRECTION_REQUIRED")
    except Exception as error:
        report.update(status="NATIVE_SURFACE_PREFLIGHT_CORRECTION_REQUIRED",nativeSurfaceGeometryAccepted=False,
                      diagnostic={"component":current,"type":type(error).__name__,"message":str(error)[:500]})
    try:
        report["retainedNativeLogRecords"]=[descriptor(path,"logs/"+path.name,MAX_LOG,empty=True)[1]
            for path in sorted((output/"logs").glob("*.log"))]
        report["workspaceBytes"]=workspace_size(output)
    except Exception as error:
        report.update(status="NATIVE_SURFACE_RETENTION_CORRECTION_REQUIRED",nativeSurfaceGeometryAccepted=False,
                      diagnostic={"component":"retention","type":type(error).__name__,"message":str(error)[:500]})
    report["elapsedSeconds"]=round(time.monotonic()-started,3)
    base.write_exclusive(output/"surface-check-execution.json",base.inventory.bounded_json(report))
    print(json.dumps({key:report[key] for key in ("status","nativeSurfaceGeometryAccepted","simulationExecuted","mainApplicationWrites")}))
    return 0 if report["nativeSurfaceGeometryAccepted"] else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface-directory",type=Path,required=True)
    parser.add_argument("--output-directory",type=Path,required=True)
    args=parser.parse_args()
    try:
        return run(args.surface_directory,args.output_directory)
    except Exception as error:
        print(json.dumps({"status":"NATIVE_SURFACE_INPUT_CORRECTION_REQUIRED","action":str(error)[:500],
                          "simulationExecuted":False,"actualCfdRunVerified":False,"mainApplicationWrites":False}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
