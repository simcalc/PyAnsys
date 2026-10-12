"""Transfer one fixed FAILED teaching baseline for data-only torque diagnosis.

This lane deliberately admits the known failed run as an immutable data source.
It does not reuse or weaken the successful baseline/phase-H0 admission routes.
No downloaded dictionary or source code is executed by this importer.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import teaching_pump_phase_source as shared

paths, require, encoded, json_data = shared.paths, shared.require, shared.encoded, shared.json_data
REPOSITORY = "simcalc/PyAnsys"
RUN_ID = 38197674270
HEAD = "834fc53cd1ad582496608d3549a3f18a0461f72f"
ARTIFACT_ID, ARTIFACT_BYTES = 11724094001, 69191600
ARTIFACT_SHA = "c633fa042dc9091e5f8dccf12d68456b6c34bfb986681fac5778a296f5d0d25e"
JOB_ID = 114643908599
WORKFLOW = ".github/workflows/teaching-pump-free-baseline.yml"
ARTIFACT_NAME = "authored-teaching-pump-baseline-38197674270"
SCHEMA = "untirta.teaching-pump-failed-baseline-torque-source.v1"
ADMISSION, REPORT = "fixed-source-admission.json", "torque-source-transfer.json"
ARCHIVE, SOURCE = "github-artifact.zip", "source-baseline"
MAX_FILE, MAX_JSON, MAX_BYTES = 128*1024**2, 128*1024, 1024**3
MAX_ENTRIES, MAX_DEPTH, CHUNK = 320, 12, 1024**2
FALSE_FLAGS = ("sourceNumericalBaselineQualified", "sourcePhaseH0Verified", "actualCfdRunVerified",
    "simulationExecuted", "numericalBaselineQualified", "phaseH0Eligible", "npshrCalculated", "npsh3Qualified",
    "physicalTransientExecuted", "restartVerified", "physicalValidationVerified", "mainApplicationWrites")
STEPS = ("Set up job", "Check out the independent public repository",
    "Verify the exact public FREE standard runner and authored resolution", "Set up CPython 3.12.10",
    "Install the isolated pinned CAD and source readers", "Verify engineering, geometry, provenance and native admission contracts",
    "Generate explicitly authored SI CAD and one shared interface mesh", "Install checksum-pinned OpenFOAM Foundation 12",
    "Require actual conversion, every-cell quality and shared-interface coverage",
    "Execute a fresh bounded authored steady SST MRF study",
    "Reduce actual authored MRF evidence and require numerical acceptance",
    "Retain authored CAD, parameters and actual component meshes", "Retain separate actual native quality and interface diagnostics",
    "Retain separate authored physics and numerical acceptance evidence")
PRIMITIVES = ("points", "faces", "owner", "neighbour", "boundary", "cellZones")
INPUTS = {"0/"+n for n in ("p", "U", "k", "omega", "nut")} | {
    "constant/MRFProperties", "constant/momentumTransport", "constant/physicalProperties",
    "system/controlDict", "system/fvSchemes", "system/fvSolution", "system/createNonConformalCouplesDict"}
END_NAMES = {"p", "U", "k", "omega", "nut", "Uf", "phi", "U2", "yPlus", "staticPressurePa", "totalPressurePa"}
TOP = {"teaching-baseline-"+n+".json" for n in ("preparation", "runtime-identity", "input-manifest", "study")} | {"teaching-geometry-identity.json"}
SETS = r"(?:nearPoints|region[01]|rotating-fluid|stationary-fluid)"
SAMPLERS = r"(?:flow(?:Magnitude)?(?:Inlet|Outlet|RotorInterface|StatorInterface)|weighted(?:Inlet|Outlet)|globalPressure(?:Minimum|Maximum)|wallYPlus)"


def validate_metadata(run, jobs, artifact):
    require(isinstance(run, dict) and type(run.get("id")) is int and run["id"] == RUN_ID
        and run.get("head_sha") == HEAD and run.get("path") == WORKFLOW and run.get("run_attempt") == 1
        and run.get("event") == "workflow_dispatch" and run.get("status") == "completed"
        and run.get("conclusion") == "failure", "Admit only the exact known FAILED source run/HEAD, never a qualified baseline.")
    for key in ("repository", "head_repository"):
        row = run.get(key)
        require(isinstance(row, dict) and row.get("full_name") == REPOSITORY and row.get("private") is False
            and row.get("visibility", "public") == "public", "The immutable diagnostic source repository must remain public.")
    rows = jobs.get("jobs") if isinstance(jobs, dict) else None
    require(isinstance(rows, list) and len(rows) == jobs.get("total_count") == 1, "Require the complete one-job source listing.")
    job = rows[0]
    require(job.get("id") == JOB_ID and type(job.get("id")) is int and job.get("run_id") == RUN_ID
        and job.get("head_sha") == HEAD and job.get("labels") == ["ubuntu-24.04"]
        and job.get("name") == "FREE authored CAD, native geometry and numerical MRF baseline"
        and job.get("status") == "completed" and job.get("conclusion") == "failure", "Bind the fixed failed FREE hosted source job.")
    steps = job.get("steps")
    require(isinstance(steps, list) and 14 <= len(steps) <= 32 and all(isinstance(s, dict)
        and type(s.get("number")) is int for s in steps) and len({s["number"] for s in steps}) == len(steps), "Bound uniquely numbered source steps.")
    actual = {s["number"]: s for s in steps}
    for number, name in enumerate(STEPS, 1):
        expected = "failure" if number == 10 else "skipped" if number == 11 else "success"
        require(actual.get(number, {}).get("name") == name and actual[number].get("status") == "completed"
            and actual[number].get("conclusion") == expected, "Retain the actual failed native wrapper/skipped numerical reducer, without promoting it.")
    require(isinstance(artifact, dict) and type(artifact.get("id")) is int and artifact["id"] == ARTIFACT_ID
        and artifact.get("name") == ARTIFACT_NAME and artifact.get("expired") is False
        and type(artifact.get("size_in_bytes")) is int and artifact["size_in_bytes"] == ARTIFACT_BYTES
        and artifact.get("digest") == "sha256:"+ARTIFACT_SHA and artifact.get("workflow_run", {}).get("id") == RUN_ID,
        "Require the fixed whole archive's exact GitHub size/SHA/run identity.")
    return {"schemaVersion": SCHEMA, "repository": REPOSITORY, "sourceWorkflow": WORKFLOW, "sourceRunId": RUN_ID,
        "sourceCommit": HEAD, "sourceJobId": JOB_ID, "sourceConclusion": "failure", "sourceReducerConclusion": "skipped",
        "artifactId": ARTIFACT_ID, "artifactName": ARTIFACT_NAME, "artifactBytes": ARTIFACT_BYTES,
        "artifactSha256": ARTIFACT_SHA, **{key: False for key in FALSE_FLAGS}}


def admission(value):
    expected = {"schemaVersion": SCHEMA, "repository": REPOSITORY, "sourceWorkflow": WORKFLOW, "sourceRunId": RUN_ID,
        "sourceCommit": HEAD, "sourceJobId": JOB_ID, "sourceConclusion": "failure", "sourceReducerConclusion": "skipped",
        "artifactId": ARTIFACT_ID, "artifactName": ARTIFACT_NAME, "artifactBytes": ARTIFACT_BYTES,
        "artifactSha256": ARTIFACT_SHA, **{key: False for key in FALSE_FLAGS}}
    require(value == expected and set(value) == set(expected)
        and all(type(value[k]) is type(v) for k,v in expected.items()), "The fixed source diagnostic admission must remain exact and nonqualifying.")
    return value


def role(name):
    if name in TOP or re.fullmatch(r"logs/0[0-3]-(?:mergeMeshes|createNonConformalCouples|checkMesh|foamRun)\.log", name): return True
    if name.startswith("source-teaching/source-geometry/"):
        return name.removeprefix("source-teaching/source-geometry/") in {"teaching-geometry.json", "teaching-input-manifest.json", "teaching-pump.brep", "impeller.msh", "volute.msh"}
    if name.startswith("source-teaching/component-mesh-snapshots/"):
        return bool(re.fullmatch(r"source-teaching/component-mesh-snapshots/(?:impeller|volute)/constant/polyMesh/(?:"+"|".join(PRIMITIVES)+")", name))
    if name.startswith("source-teaching/"):
        name = name.removeprefix("source-teaching/")
        if name in {"teaching-native-"+n+".json" for n in ("preparation", "execution", "runtime-identity", "input-manifest")}: return True
        if re.fullmatch(r"logs/0[0-8]-(?:gmshToFoam|changeDictionary|checkMesh|mergeMeshes|createNonConformalCouples)\.log", name): return True
    authored = name.startswith("authored-physics/")
    name = name.removeprefix("authored-physics/")
    match = re.fullmatch(r"(?:impeller|volute)-case/(.+)", name)
    if not match: return False
    native = match[1]
    if native in INPUTS: return True
    if authored: return False
    return native in {"impeller.msh", "volute.msh", "system/changeDictionaryDict", "constant/fvMesh/polyFaces"}


def native_role(name):
    if role(name): return True
    name = name.removeprefix("source-teaching/")
    match = re.fullmatch(r"(?:impeller|volute)-case/(.+)", name)
    if not match: return False
    path = match[1]
    return bool(re.fullmatch(r"constant/polyMesh/(?:"+"|".join(PRIMITIVES)+")", path)
        or re.fullmatch("constant/polyMesh/sets/"+SETS, path)
        or re.fullmatch(r"0/(?:U2|yPlus|staticPressurePa|totalPressurePa|cellToRegion|uniform/time)", path)
        or re.fullmatch(r"500/(?:"+"|".join(map(re.escape, sorted(END_NAMES)))+r"|uniform/time)", path)
        or re.fullmatch("postProcessing/"+SAMPLERS+r"/0/(?:surfaceFieldValue|volFieldValue|yPlus)\.dat", path)
        or re.fullmatch(r"postProcessing/checkMesh/(?:0/(?:interface-impeller-outlet|interface-volute-inlet)_coverage|constant/nearPoints)\.vtk", path))


def plain_native(raw, *, source_constraint_notice=False):
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw.decode("ascii"), flags=re.S)
    if source_constraint_notice:
        # These immutable pre-NCC authored notices remain outside the executed
        # merged case. They are neither expanded nor copied into native inputs.
        text = re.sub(r'(?m)^[ \t]*#includeEtc "caseDicts/setConstraintTypes"[ \t]*\r?$', "", text)
    require("\x00" not in text and not re.search(r"(?m)^\s*#|\b(?:codeStream|coded\w*|codeExecute|codeInclude|systemCall|dynamicCode)\b", text),
        "Imported native data must be plain ASCII without code, includes or system calls.")
    require(re.search(r"\bFoamFile\s*\{[^{}]*\bformat\s+ascii\s*;", text), "Require actual plain ASCII native headers.")
    return text


def descriptor(path, name, limit=MAX_FILE):
    path = Path(path); before = path.stat()
    require(not path.is_symlink() and not getattr(path, "is_junction", lambda:False)(), "Read no linked source files.")
    raw = paths.read_regular(path, limit); after = path.stat()
    require((before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns) and len(raw)==after.st_size,
        "Actual source data changed during read.")
    return raw, {"path": name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def inventory(directory):
    root = paths.scope_path(directory); rows = []
    for path in sorted(root.rglob("*"),key=lambda p:p.relative_to(root).as_posix()):
        require(not path.is_symlink() and not getattr(path, "is_junction", lambda:False)() and (path.is_file() or path.is_dir()), "Source inventory prohibits links/devices.")
        if not path.is_file(): continue
        name = shared.canonical_path(path.relative_to(root).as_posix())
        require(native_role(name) and len(rows) < MAX_ENTRIES, "Unknown or unbounded failed-source data roles are prohibited: "+name)
        _, row = descriptor(path, name); rows.append(row)
        require(sum(r["bytes"] for r in rows) <= MAX_BYTES, "Bound all immutable failed-source bytes.")
    require(TOP <= {r["path"] for r in rows}, "Retain all exact source study/manifest/geometry bindings.")
    return rows


def verify_tree(directory):
    """Pure current-byte checks, with no installed-binary or numerical waiver."""
    import run_teaching_pump_baseline as baseline
    root = paths.scope_path(directory); rows = inventory(root); by_path = {r["path"]:r for r in rows}
    values = {name:json_data(descriptor(root/name, name, MAX_JSON)[0]) for name in TOP}
    prep = values["teaching-baseline-preparation.json"]; manifest = values["teaching-baseline-input-manifest.json"]
    require(prep.get("schemaVersion") == "untirta.teaching-pump-baseline-preparation.v1"
        and manifest.get("schemaVersion") == "untirta.teaching-pump-baseline-input-manifest.v1"
        and prep.get("sourceGeometryRoute") == manifest.get("sourceGeometryRoute") == "CLASSROOM_AUTHORED_GEOMETRY"
        and manifest.get("sourceGeneratorCommit") == HEAD and str(manifest.get("sourceGithubRunId")) == str(RUN_ID)
        and manifest.get("pressureLevelAbsolutePa") == 300000, "Bind the exact authored source study and pressure convention.")
    require(manifest.get("preparationReportSha256") == by_path["teaching-baseline-preparation.json"]["sha256"]
        and manifest.get("runtimeIdentitySha256") == by_path["teaching-baseline-runtime-identity.json"]["sha256"], "Keep actual pre-execution metadata byte bindings.")
    inputs=manifest.get("studyInputs",{});basis=manifest.get("physicalBasis",{})
    require(all(type(inputs.get(key)) in {int,float} and inputs[key]==value for key,value in
        {"densityKgM3":998.2,"gravityMS2":9.81,"speedRpm":1450,"massFlowKgS":90,"viscosityPaS":.001002}.items())
        and basis.get("rotationAxis")==[0,0,1] and basis.get("rotationOriginM")==[0,0,0]
        and basis.get("gravityInSolver") is False, "Bind torque density, speed, SI reference and source axis/origin to the actual historical study.")
    geometry=json_data(descriptor(root/"source-teaching/source-geometry/teaching-geometry.json","source-teaching/source-geometry/teaching-geometry.json",MAX_JSON)[0])
    require(geometry.get("schemaVersion")=="untirta.teaching-pump-geometry.v1" and geometry.get("provenance")=="CLASSROOM_AUTHORED_GEOMETRY"
        and geometry.get("units")=="SI", "Use the actual authored-SI geometry; no original or manufacturer geometry claim.")
    control=plain_native(descriptor(root/"impeller-case/system/controlDict","impeller-case/system/controlDict")[0])
    require(re.findall(r"\bwritePrecision\s+(\d+)\s*;",control)==["17"], "Require the actual bound17-digit writer before imposing native component-sum roundoff criteria.")
    for row in shared.descriptor_rows(manifest.get("files")).values():
        require(by_path.get(row["path"]) == row, "Every actual source manifest file must retain its bytes and SHA.")
    for row in shared.descriptor_rows(prep.get("sourceEvidenceFileRecords")).values():
        require(by_path.get(row["path"]) == row, "Every original geometry proof/data file must remain immutable.")
    for row in rows:
        name = row["path"]
        if (re.match(r"(?:authored-physics/|source-teaching/)?(?:impeller|volute)-case/", name)
            and not name.endswith((".msh", ".vtk", ".dat"))):
            historical_notice = bool(re.fullmatch(r"(?:authored-physics/(?:impeller|volute)-case|volute-case)/0/(?:U|p|k|omega|nut)", name))
            plain_native(descriptor(root/name, name)[0], source_constraint_notice=historical_notice)
    case = root/"impeller-case"
    measured = baseline.base.verify_merged_mesh(case, prep["components"], root/"logs/02-checkMesh.log")
    require((measured["mergedCellCount"], measured["mergedPointCount"], measured["mergedFaceCount"]) == (205611,45897,433700), "Require the exact fixed source native counts.")
    primary, fields, scans = baseline.final_fields(root, measured["mergedCellCount"])
    log, log_row = descriptor(root/"logs/03-foamRun.log", "logs/03-foamRun.log", 8*1024**2)
    text = log.decode("ascii")
    require(re.findall(r"(?m)^Build\s*:\s*(\S+)\s*$", text) == ["12-86e126a7bc4d"]
        and re.search(r"(?m)^Time = 500s?\s*$", text) and re.search(r"(?m)^End\s*$", text)
        and not re.search(r"FOAM FATAL|Floating point exception|Segmentation fault", text), "Require the actual500/End trace; completion alone is not qualification.")
    saved_time = plain_native(descriptor(case/"500/uniform/time", "impeller-case/500/uniform/time")[0])
    require(re.search(r"\bvalue\s+500\s*;", saved_time) and re.search(r'\bname\s+"500"\s*;', saved_time), "Bind the actual saved endpoint clock.")
    return {"files":rows, "mergedNativeMeshEvidence":measured, "finalNativeFieldRecords":fields,
        "finiteFieldScans":scans, "sourceLog":log_row, "sourceLogReached500End":True,
        "installedRuntimeReverified":False, **{key:False for key in FALSE_FLAGS}}


def archive_tree_binding(archive_path, actual_rows):
    """Recompute membership from fixed ZIP bytes; a changed sidecar cannot rebind data."""
    path=paths.scope_path(archive_path);_,record=descriptor(path,ARCHIVE)
    require((record["bytes"],record["sha256"])==(ARTIFACT_BYTES,ARTIFACT_SHA), "The fixed whole ZIP identity is required for current-source replay.")
    records=[]
    with zipfile.ZipFile(path) as archive:
        infos=archive.infolist();require(0<len(infos)<=MAX_ENTRIES,"Bound the actual fixed archive central directory.")
        for info in infos:
            name=shared.canonical_path(info.filename)
            require(not info.is_dir() and native_role(name) and 0<info.file_size<=MAX_FILE, "Require exact current source ZIP data roles.")
            with archive.open(info) as stream:raw=stream.read(MAX_FILE+1)
            require(len(raw)==info.file_size,"Re-read full member CRC and size before native diagnosis.")
            records.append({"path":name,"bytes":len(raw),"sha256":sha256(raw).hexdigest()})
    require(len({r["path"] for r in records})==len(records) and sorted(records,key=lambda r:r["path"])==actual_rows,
        "Current source fields/controls must match actual fixed archive members, not a rewritten transfer sidecar.")
    require(descriptor(path,ARCHIVE)[1]==record,"The original source ZIP changed during membership replay.")
    return record


def extract(archive_path, admission_path, output_directory):
    archive_path = paths.scope_path(archive_path); admission_path = paths.scope_path(admission_path)
    target = paths.scope_path(output_directory, fresh=True)
    admitted = admission(json_data(descriptor(admission_path, ADMISSION, MAX_JSON)[0]))
    raw,row = descriptor(archive_path, ARCHIVE)
    require((row["bytes"],row["sha256"]) == (ARTIFACT_BYTES,ARTIFACT_SHA), "The whole fixed source ZIP size or SHA256 differs.")
    files = {}; expanded = 0
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist();require(0<len(infos)<=MAX_ENTRIES, "Bound the full source central directory.")
        aliases = {}
        for info in infos:
            require(info.filename == info.orig_filename and not info.is_dir(), "The fixed source has only ordinary file members.")
            name = shared.canonical_path(info.filename);mode = info.external_attr>>16
            require(name.casefold() not in aliases and native_role(name), "Reject duplicate/unknown source archive roles.")
            for parent in (Path(name),*Path(name).parents):
                if str(parent)==".":continue
                key = parent.as_posix().casefold();require(key not in aliases or aliases[key] == parent.as_posix(), "Reject case-aliased source ancestors.")
                aliases[key]=parent.as_posix()
            require(stat.S_IFMT(mode) in {0,stat.S_IFREG} and not mode&0o111 and not info.flag_bits&0x41
                and info.compress_type in {zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED} and 0<info.file_size<=MAX_FILE,
                "Require bounded regular nonexecutable source ZIP data.")
            require(name not in files and all(str(p).replace('\\','/') not in files for p in Path(name).parents if str(p)!='.'), "Reject file/directory collisions.")
            files[name]=info;expanded+=info.file_size;require(expanded<=MAX_BYTES, "Bound expanded source data to1GiB.")
        require(TOP<=set(files), "The complete fixed source metadata roles are required.")
        # Complete CRC and content checks precede any extraction.
        records=[]
        for name,info in files.items():
            with archive.open(info) as stream: payload=stream.read(MAX_FILE+1)
            require(len(payload)==info.file_size, "Every full source ZIPmember size/CRC must match.")
            records.append({"path":name,"bytes":len(payload),"sha256":sha256(payload).hexdigest()})
        target.mkdir()
        try:
            for name,info in files.items():
                path=target/name;path.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(info) as origin,path.open("xb") as destination:
                    for chunk in iter(lambda:origin.read(CHUNK),b""):destination.write(chunk)
            checked=verify_tree(target)
            require(checked["files"]==sorted(records,key=lambda r:r["path"]), "Extracted source bytes must match every independently verified archive member.")
        except BaseException:
            import shutil
            require(target.resolve().is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()), "Failed source cleanup must remain scoped.")
            shutil.rmtree(target);raise
    require(descriptor(archive_path,ARCHIVE)[1]==row, "The source ZIP changed during transfer.")
    return {"schemaVersion":SCHEMA,"status":"FAILED_SOURCE_DIAGNOSTIC_TRANSFERRED_NOT_QUALIFIED","admission":admitted,
        "archiveFile":row,"sourceFiles":checked["files"],"sourceDirectory":SOURCE,
        "fullZipCrcVerified":True,"byteExactTransferVerified":True,**{key:False for key in FALSE_FLAGS}}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*_):return None


def fetch(output_directory):
    """Only this guard step sees a temporary read token; signed URLs stay in memory."""
    root=paths.scope_path(output_directory,fresh=True)
    token=os.environ.get("PYANSYS_REPOSITORY_READ_TOKEN")
    require(isinstance(token,str) and 0<len(token)<=1024 and token.isascii() and not any(c.isspace() for c in token), "Use the temporary workflow read credential only in this source guard step.")
    opener=urllib.request.build_opener(NoRedirect())
    def request(endpoint):
        url="https://api.github.com/repos/"+REPOSITORY+endpoint
        return urllib.request.Request(url,headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json","User-Agent":"PyAnsys-fixed-torque-diagnostic"})
    def api(endpoint):
        req=request(endpoint)
        with opener.open(req,timeout=20) as response:
            require(response.status==200 and response.geturl()==req.full_url,"Use only exact source GitHub metadata endpoints.")
            return json_data(response.read(MAX_JSON+1))
    admitted=validate_metadata(api(f"/actions/runs/{RUN_ID}"),api(f"/actions/runs/{RUN_ID}/jobs?per_page=100"),api(f"/actions/artifacts/{ARTIFACT_ID}"))
    try:opener.open(request(f"/actions/artifacts/{ARTIFACT_ID}/zip"),timeout=20)
    except urllib.error.HTTPError as error:
        require(error.code==302,"The exact GitHub archive endpoint must return one ephemeral HTTPS data redirect.")
        location=error.headers.get("Location","");error.close()
    else:raise ValueError("Require the actual GitHub artifact data redirect.")
    parsed=urllib.parse.urlsplit(location)
    require(parsed.scheme=="https" and not parsed.username and not parsed.password and parsed.port in {None,443}
        and parsed.hostname and (re.fullmatch(r"productionresultssa\d+\.blob\.core\.windows\.net",parsed.hostname)
            or parsed.hostname.endswith(".actions.githubusercontent.com")), "Only the known HTTPS GitHub artifact data service is permitted.")
    root.mkdir();(root/ADMISSION).write_bytes(encoded(admitted))
    digest,total=sha256(),0;started=time.monotonic()
    with opener.open(urllib.request.Request(location,headers={"User-Agent":"PyAnsys-fixed-torque-diagnostic"}),timeout=20) as response,(root/ARCHIVE).open("xb") as output:
        require(response.status==200 and response.geturl()==location, "The artifact data must not redirect again; no credential is forwarded.")
        while True:
            chunk=response.read(CHUNK)
            if not chunk:break
            total+=len(chunk);require(total<=ARTIFACT_BYTES and time.monotonic()-started<180,"Bound the whole artifact download size/time.")
            output.write(chunk);digest.update(chunk)
    require(total==ARTIFACT_BYTES and digest.hexdigest()==ARTIFACT_SHA,"The complete downloaded source ZIP must match its fixed digest.")
    result=extract(root/ARCHIVE,root/ADMISSION,root/SOURCE)
    (root/REPORT).write_bytes(encoded(result));return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output-directory",required=True)
    parser.add_argument("--fetch",action="store_true");parser.add_argument("--archive");parser.add_argument("--admission")
    args=parser.parse_args()
    try:
        if args.fetch:result=fetch(args.output_directory)
        else:
            require(args.archive and args.admission,"Provide exact local archive+admission, or the fixed source fetch mode.")
            result=extract(args.archive,args.admission,args.output_directory)
        print(json.dumps({"status":result["status"],"artifactId":ARTIFACT_ID,"sourceNumericalBaselineQualified":False,"mainApplicationWrites":False}));return 0
    except (ValueError,OSError,zipfile.BadZipFile,urllib.error.URLError):
        print(json.dumps({"status":"FIXED_TORQUE_SOURCE_REQUIRED","correctiveAction":"Verify the exact unexpired public source artifact size/SHA, safe data roles and current native500 evidence.","mainApplicationWrites":False}));return 2


if __name__=="__main__":raise SystemExit(main())
