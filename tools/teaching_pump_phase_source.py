"""Transfer a completed public teaching phase artifact, without executing data.

The archive and metadata are supplied locally. Current installed-binary phase
H0 replay is a separate prerequisite: teaching_pump_pressure_case.verify_reference.
The transferred tree is byte-exact and has no new sidecar inside its inventory.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_snappy_phase_source as paths  # Ordinary local path/cleanup only.

REPOSITORY = "simcalc/PyAnsys"
WORKFLOW = ".github/workflows/teaching-pump-free-phase.yml"
JOB = "FREE authored pump baseline and active phase reference"
ARTIFACT_PREFIX = "authored-teaching-pump-phase-"
SCHEMA = "untirta.teaching-pump-phase-source-transfer.v1"
REPORT = "teaching-phase-source-transfer.json"
SOURCE_DIRECTORY = "source-reference"
EXECUTION = "teaching-phase-execution.json"
ACCEPTANCE = "teaching-phase-acceptance.json"
MANIFEST = "teaching-phase-input-manifest.json"
PREPARATION = "teaching-phase-preparation.json"
RUNTIME = "teaching-phase-runtime-identity.json"
STUDY = "teaching-phase-study.json"
IDENTITY = "teaching-geometry-identity.json"
TOP_FILES = {EXECUTION, ACCEPTANCE, MANIFEST, PREPARATION, RUNTIME, STUDY, IDENTITY}
MAX_ARCHIVE_BYTES, MAX_EXPANDED_BYTES = 300 * 1024**2, 1024**3
MAX_FILE_BYTES, MAX_JSON_BYTES, MAX_ACCEPTANCE_BYTES = 128 * 1024**2, 128 * 1024, 16 * 1024**2
MAX_REPORT_BYTES, MAX_ENTRIES, MAX_DEPTH = 128 * 1024, 1024, 12
CHUNK = 1024**2
STEPS = ("Set up job", "Check out the independent public repository",
    "Verify the exact public FREE standard runner and authored resolution", "Set up CPython 3.12.10",
    "Install the isolated pinned CAD and source readers", "Verify engineering, geometry, provenance and native admission contracts",
    "Generate explicitly authored SI CAD and one shared interface mesh", "Install checksum-pinned OpenFOAM Foundation 12",
    "Require actual conversion, every-cell quality and shared-interface coverage",
    "Execute a fresh bounded authored steady SST MRF study",
    "Reduce actual authored MRF evidence and require numerical acceptance",
    "Execute fresh bounded active Schnerr Sauer high pressure MRF reference",
    "Independently qualify actual active phase reference before any pressure sweep",
    "Retain authored CAD, parameters and actual component meshes",
    "Retain separate actual native quality and interface diagnostics",
    "Retain separate authored physics and numerical acceptance evidence",
    "Retain separate active phase reference evidence")
TRUE_NATIVE = ("nativeCheckMeshVerified", "nativeGeometryQualityVerified", "physicalWallTypesVerified",
    "coupledInterfaceCoverageVerified", "nativeSolverCompleted", "cavitationModelExecuted",
    "finiteNativeEndFieldsVerified", "inputArtifactsImmutable", "sourceBaselineImmutable",
    "stationaryMrfRelaxationExecuted", "actualCfdRunVerified", "simulationExecuted")
FALSE_TRANSFER = ("currentSourceH0Verified", "sourceNativeEvidenceVerified", "sourceNumericalPhaseReferenceQualified",
    "actualCfdRunVerified", "simulationExecuted", "cavitationModelExecuted", "phaseH0Eligible",
    "phaseFamilyReferenceQualified", "cavitationSweepExecuted", "npshrCalculated", "npsh3Qualified",
    "physicalTransientExecuted", "restartVerified", "mainApplicationWrites")
TIME = r"(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?"
SCALAR_NAMES = {"p", "p_rgh", "alpha.water", "alpha.vapour", "rho", "k", "omega", "nut", "rAU", "totalPressurePa", "U2", "yPlus"}
SURFACE_NAMES = {"phi", "alphaPhi.water", "alphaPhi.vapour", "rhoPhi"}
INPUTS = {"0/"+n for n in ("U", "p_rgh", "alpha.water", "k", "omega", "nut")} | {
    "constant/g", "constant/phaseProperties", "constant/physicalProperties.water", "constant/physicalProperties.vapour",
    "constant/momentumTransport", "constant/MRFProperties", "constant/fvModels", "system/fvSchemes", "system/fvSolution", "system/controlDict"}
PRIMITIVES = "(?:points|faces|owner|neighbour|boundary|cellZones)"
SAMPLERS = r"(?:massFlow(?:Magnitude)?(?:Inlet|Outlet|RotorInterface|StatorInterface)|weighted(?:Inlet|Outlet)|waterMinimum(?:Inlet|Outlet)|globalPressureMinimum|globalWaterMinimum|globalWaterMaximum|wallYPlus)"
SET_NAMES = r"(?:rotating-fluid|stationary-fluid|region[01]|nearPoints|concaveFaces|warpedFaces|nonOrthoFaces|skewFaces)"


def require(value, action):
    if not value: raise ValueError(action)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def json_data(raw, limit=MAX_JSON_BYTES):
    require(0 < len(raw) <= limit, "Bound every actual metadata/report JSON.")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON keys are prohibited."); result[key] = value
        return result
    def finite(item, depth=0):
        require(depth <= 48, "Bound JSON nesting.")
        if isinstance(item, float): require(math.isfinite(item), "Finite JSON numbers are required.")
        elif isinstance(item, dict):
            for child in item.values(): finite(child, depth+1)
        elif isinstance(item, list):
            for child in item: finite(child, depth+1)
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Finite JSON constants are required.")))
    finite(value); require(isinstance(value, dict), "Require one JSON object."); return value


def read_json(path):
    path = paths.scope_path(path)
    raw = paths.read_regular(path, MAX_JSON_BYTES)
    return json_data(raw), raw


def validate_metadata(run_id, source_commit, run, jobs, artifacts):
    require(isinstance(run_id, str) and re.fullmatch(r"[1-9][0-9]{0,18}", run_id)
        and isinstance(source_commit, str) and re.fullmatch(r"[0-9a-f]{40}", source_commit),
        "Pin one positive source run and exact immutable 40-hex source HEAD.")
    require(isinstance(run, dict) and type(run.get("id")) is int and str(run["id"]) == run_id
        and run.get("head_sha") == source_commit and run.get("path") == WORKFLOW
        and run.get("event") == "workflow_dispatch" and run.get("status") == "completed" and run.get("conclusion") == "success",
        "Require the exact successful completed authored active-phase workflow.")
    for name in ("repository", "head_repository"):
        repo = run.get(name)
        require(isinstance(repo, dict) and repo.get("full_name") == REPOSITORY and repo.get("private") is False
            and repo.get("visibility", "public") == "public", "Admit only the exact approved public source repository.")
    rows = jobs.get("jobs") if isinstance(jobs, dict) else None
    require(isinstance(jobs, dict) and type(jobs.get("total_count")) is int and jobs["total_count"] == 1 and isinstance(rows, list) and len(rows) == 1,
        "Require one complete actual standard-runner job listing.")
    job = rows[0]
    require(isinstance(job, dict) and type(job.get("id")) is int and 0 < job["id"] < 10**20
        and type(job.get("run_id")) is int and str(job["run_id"]) == run_id and job.get("head_sha") == source_commit
        and job.get("name") == JOB and job.get("labels") == ["ubuntu-24.04"]
        and job.get("status") == "completed" and job.get("conclusion") == "success",
        "Bind the exact completed successful FREE standard Ubuntu job to this run/head.")
    steps = job.get("steps")
    require(isinstance(steps, list) and len(STEPS) <= len(steps) <= 32 and all(isinstance(s, dict)
        and type(s.get("number")) is int and 1 <= s["number"] <= 32 for s in steps)
        and len({s["number"] for s in steps}) == len(steps), "Require bounded uniquely numbered actual job steps.")
    numbered = {s["number"]: s for s in steps}
    require(all(numbered.get(i, {}).get("name") == name and numbered[i].get("status") == "completed"
        and numbered[i].get("conclusion") == "success" for i, name in enumerate(STEPS, 1))
        and all(s.get("status") == "completed" and s.get("conclusion") == "success" for s in steps),
        "Every exact reviewed preparation, native run, numerical reducer and retention step must pass.")
    listings = artifacts.get("artifacts") if isinstance(artifacts, dict) else None
    require(isinstance(listings, list) and 1 <= len(listings) <= 32 and all(isinstance(r, dict) for r in listings),
        "Require bounded actual artifact metadata.")
    matches = [r for r in listings if r.get("name") == ARTIFACT_PREFIX + run_id]
    require(len(matches) == 1, "Require exactly the same-run active-phase evidence artifact.")
    artifact = matches[0]; workflow_run = artifact.get("workflow_run")
    require(type(artifact.get("id")) is int and 0 < artifact["id"] < 10**20 and artifact.get("expired") is False
        and type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= MAX_ARCHIVE_BYTES
        and isinstance(workflow_run, dict) and type(workflow_run.get("id")) is int and str(workflow_run["id"]) == run_id
        and isinstance(artifact.get("digest"), str) and re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["digest"]),
        "Bind the unexpired bounded artifact to the exact run and GitHub SHA256.")
    return {"repository": REPOSITORY, "sourceWorkflow": WORKFLOW, "sourceRunId": run_id, "sourceCommit": source_commit,
        "jobId": job["id"], "jobName": JOB, "artifactId": artifact["id"], "artifactName": artifact["name"],
        "artifactBytes": artifact["size_in_bytes"], "githubReportedArchiveDigest": artifact["digest"]}


def canonical_path(name, *, directory=False):
    require(isinstance(name, str) and 0 < len(name.encode("utf-8")) <= 512 and "\\" not in name and "\x00" not in name,
        "Require a bounded canonical relative data path.")
    parts = name.split("/")
    require(len(parts) <= MAX_DEPTH and all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) and p not in {"", ".", ".."}
        and not p.startswith(".") and not p.endswith(".") and p.split(".")[0].upper() not in paths.WINDOWS_DEVICES for p in parts)
        and str(PurePosixPath(name)) == name, "Reject traversal, hidden/directive/platform-alias paths.")
    require(directory or PurePosixPath(name).suffix.lower() not in paths.FORBIDDEN_SUFFIXES | {".key", ".pem", ".env"},
        "Executable, browser-code and credential roles are prohibited.")
    return name


def members(archive):
    infos = archive.infolist(); require(0 < len(infos) <= MAX_ENTRIES, "Bound the complete central directory to 1024 members.")
    by_name, prefixes, expanded = {}, {}, 0
    for info in infos:
        require(info.filename == info.orig_filename, "Do not normalize hostile ZIP names.")
        directory = info.filename.endswith("/"); name = canonical_path(info.filename[:-1] if directory else info.filename, directory=directory)
        mode = (info.external_attr >> 16) & 0xffff
        require(info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED} and not info.flag_bits & 0x41,
            "Reject encrypted/unsupported ZIP payloads.")
        require(stat.S_IFMT(mode) in ({0, stat.S_IFDIR} if directory else {0, stat.S_IFREG})
            and (info.file_size == info.CRC == 0 if directory else not mode & 0o111 and not info.external_attr & 0x10
                and 0 < info.file_size <= MAX_FILE_BYTES), "Require regular nonexecutable data or empty ordinary directory entries.")
        require(name.casefold() not in {n.casefold() for n in by_name}, "Reject duplicate and case-aliased archive members.")
        for parent in [name, *map(str, PurePosixPath(name).parents)]:
            if parent == ".": continue
            key = parent.casefold(); require(key not in prefixes or prefixes[key] == parent, "Reject alternate case aliases in every ancestor."); prefixes[key] = parent
        by_name[name] = (info, directory); expanded += info.file_size
        require(expanded <= MAX_EXPANDED_BYTES, "Bound all expanded archive data to one GiB.")
    for name, (_, directory) in by_name.items():
        require(all(str(parent) not in by_name or by_name[str(parent)][1] for parent in PurePosixPath(name).parents if str(parent) != "."),
            "A data file cannot also be a directory ancestor.")
    files = {name: info for name, (info, directory) in by_name.items() if not directory}
    require(TOP_FILES <= set(files), "Retain all seven exact phase metadata/identity roles at artifact root.")
    parents = {str(parent) for name in files for parent in PurePosixPath(name).parents if str(parent) != "."}
    require(all(name in parents for name, (_, directory) in by_name.items() if directory), "Unknown empty directory roles are prohibited.")
    return files, expanded, len(infos)


def member_json(archive, files, name):
    require(name in files, "Missing current source JSON role: " + name)
    limit = MAX_ACCEPTANCE_BYTES if name.endswith("-acceptance.json") else MAX_JSON_BYTES
    require(files[name].file_size <= limit, "Bound each source JSON before allocation.")
    with archive.open(files[name]) as stream: raw = stream.read(limit+1)
    require(len(raw) == files[name].file_size, "JSON payload must match its actual ZIP descriptor.")
    return json_data(raw, limit), {"path": name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def descriptor_rows(rows):
    require(isinstance(rows, list) and len(rows) <= MAX_ENTRIES, "Use bounded actual file descriptor rows.")
    result = {}
    for row in rows:
        require(isinstance(row, dict) and all(k in row for k in ("path", "bytes", "sha256")), "Each native/data role needs its actual path/bytes/SHA.")
        name = canonical_path(row["path"])
        require(name not in result and type(row["bytes"]) is int and 0 < row["bytes"] <= MAX_FILE_BYTES
            and isinstance(row["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "Require unique bounded typed native file records.")
        result[name] = {k: row[k] for k in ("path", "bytes", "sha256")}
    return result


def bound_role(name):
    """Known source/native data hierarchy; a supplied hash cannot invent a role."""
    if name in TOP_FILES or name in {"logs/00-checkMesh.log", "logs/01-foamRun.log"}: return True
    if name.startswith("case/"):
        role = name.removeprefix("case/")
        if role.startswith("0/"): return role in INPUTS or role in {"0/"+n for n in ("totalPressurePa", "U2", "yPlus")}
        return role in INPUTS or bool(re.fullmatch("constant/polyMesh/"+PRIMITIVES, role)
            or re.fullmatch("constant/polyMesh/sets/"+SET_NAMES, role)
            or role == "constant/fvMesh/polyFaces" or role in {"0/"+n for n in ("totalPressurePa", "U2", "yPlus")}
            or re.fullmatch("postProcessing/"+SAMPLERS+"/"+TIME+r"/(?:surfaceFieldValue|volFieldValue|yPlus)\.dat", role)
            or re.fullmatch(r"postProcessing/checkMesh/0/(?:interface-impeller-outlet|interface-volute-inlet)_coverage\.vtk", role)
            or role == "postProcessing/checkMesh/constant/nearPoints.vtk"
            or re.fullmatch(TIME+r"/(?:"+"|".join(map(re.escape, sorted(SCALAR_NAMES|SURFACE_NAMES|{"U", "Uf"})))+r"|uniform/time|fvMesh/polyFaces)", role))
    if not name.startswith("source-baseline/"): return False
    role = name.removeprefix("source-baseline/")
    if role in {"teaching-baseline-"+n+".json" for n in ("preparation", "execution", "acceptance", "runtime-identity", "input-manifest", "study")} | {IDENTITY}: return True
    if role.startswith("source-teaching/source-geometry/"):
        return role.removeprefix("source-teaching/source-geometry/") in {"impeller.msh", "volute.msh", "teaching-pump.brep", "teaching-input-manifest.json", "teaching-geometry.json"}
    if role.startswith("source-teaching/"):
        role = role.removeprefix("source-teaching/")
        if role in {"teaching-native-"+n+".json" for n in ("preparation", "execution", "runtime-identity", "input-manifest")}: return True
        if re.fullmatch(r"component-mesh-snapshots/(?:impeller|volute)/constant/polyMesh/"+PRIMITIVES, role): return True
    if re.fullmatch(r"logs/\d{2}-(?:gmshToFoam|changeDictionary|checkMesh|mergeMeshes|createNonConformalCouples|foamRun)\.log", role): return True
    role = role.removeprefix("authored-physics/")
    match = re.fullmatch(r"(?:impeller|volute)-case/(.+)", role)
    if not match: return False
    role = match[1]
    if role == "500/Uf": return True  # Exact observed Foundation12 MRF writer state.
    return bool(role in {"impeller.msh", "volute.msh"} or re.fullmatch(r"system/(?:fvSchemes|fvSolution|controlDict|createNonConformalCouplesDict|changeDictionaryDict)", role)
        or re.fullmatch(r"constant/(?:physicalProperties|momentumTransport|MRFProperties|fvMesh/polyFaces)", role)
        or re.fullmatch("constant/polyMesh/"+PRIMITIVES, role) or re.fullmatch("constant/polyMesh/sets/"+SET_NAMES, role)
        or re.fullmatch(r"(?:0|500)/(?:p|U|k|omega|nut|phi|cellToRegion|staticPressurePa|totalPressurePa|U2|yPlus|uniform/time)", role)
        or re.fullmatch(r"postProcessing/[A-Za-z][A-Za-z0-9]*/"+TIME+r"/(?:surfaceFieldValue|volFieldValue|yPlus)\.dat", role)
        or re.fullmatch(r"postProcessing/checkMesh/0/(?:interface-impeller-outlet|interface-volute-inlet)_coverage\.vtk", role)
        or role == "postProcessing/checkMesh/constant/nearPoints.vtk")


def phase_bindings(archive, files, admitted):
    packets = {name: member_json(archive, files, name) for name in TOP_FILES}
    raw, acceptance, manifest, prep = (packets[n][0] for n in (EXECUTION, ACCEPTANCE, MANIFEST, PREPARATION))
    require(raw.get("schemaVersion") == "untirta.teaching-pump-phase-reference-execution.v1"
        and raw.get("status") == "TEACHING_ACTIVE_PHASE_REFERENCE_EXECUTED_ACCEPTANCE_REQUIRED"
        and raw.get("githubRepository") == REPOSITORY and raw.get("githubRunId") == admitted["sourceRunId"]
        and raw.get("githubSourceCommit") == admitted["sourceCommit"]
        and raw.get("mode") == "high-pressure-active-phase-MRF-reference" and raw.get("inletAbsoluteTotalPressurePa") == 300000
        and all(raw.get(k) is True for k in TRUE_NATIVE) and all(raw.get(k) is False for k in (
            "phaseH0Eligible", "phaseFamilyReferenceQualified", "cavitationSweepExecuted", "npsh3Qualified",
            "physicalTransientExecuted", "restartVerified", "mainApplicationWrites")),
        "Bind truthful raw completion to this exact authored fixed-MRF high-pressure run/head; numerical replay remains pending.")
    # Raw facts legitimately record execution; transfer facts always remain false.
    require(acceptance.get("schemaVersion") == "untirta.teaching-pump-phase-reference-acceptance.v1"
        and acceptance.get("status") == "AuthoredActivePhaseReferenceNumericallyQualified"
        and acceptance.get("sourceGeometryRoute") == "CLASSROOM_AUTHORED_GEOMETRY"
        and all(acceptance.get(k) is True for k in ("actualCfdRunVerified", "simulationExecuted", "phaseObservationQualified",
            "numericalPhaseReferenceQualified", "phaseH0Eligible", "interfaceCoverageVerified", "endpointGlobalFieldTableConsistencyVerified"))
        and acceptance.get("diagnostics") == [] and type(acceptance.get("activePhaseReferenceHeadM")) in (float, int)
        and math.isfinite(acceptance["activePhaseReferenceHeadM"]) and acceptance["activePhaseReferenceHeadM"] > 0
        and all(acceptance.get(k) is False for k in ("qualifiedBaseline", "npsh3Qualified", "physicalTransientExecuted", "manufacturerPublishedGeometry", "mainApplicationWrites")),
        "Retain successful active-reference metadata without treating its flags as current H0 proof.")
    require(manifest.get("schemaVersion") == "untirta.teaching-pump-phase-input-manifest.v1"
        and acceptance.get("manifest") == manifest == acceptance.get("inputManifest")
        and packets[STUDY][0] == acceptance.get("study") and prep.get("schemaVersion") == "untirta.teaching-pump-phase-reference-preparation.v1"
        and prep.get("sourceGeometryRoute") == manifest.get("sourceGeometryRoute") == "CLASSROOM_AUTHORED_GEOMETRY",
        "Retain exact distinct authored source schemas and matching persisted scientific packets.")
    require(acceptance.get("nativeInputManifestSha256") == packets[MANIFEST][1]["sha256"]
        and packets[STUDY][0].get("schemaVersion") == "untirta.teaching-pump-phase-study.v1"
        and packets[RUNTIME][0].get("schemaVersion") == "untirta.teaching-pump-phase-runtime-identity.v1"
        and packets[IDENTITY][0].get("provenance") == "CLASSROOM_AUTHORED_GEOMETRY" and packets[IDENTITY][0].get("units") == "SI",
        "Retain the source's actual typed runtime, authored SI identity and input-manifest SHA.")
    for key, name in (("preparationReportSha256", PREPARATION), ("runtimeIdentitySha256", RUNTIME), ("inputManifestSha256", MANIFEST), ("studySha256", STUDY)):
        require(raw.get(key) == packets[name][1]["sha256"], "Raw native packet SHA changed: " + name)
    bound = {name: row for name, (_, row) in packets.items()}
    inventories = [manifest.get("files"), acceptance.get("nativeEvidenceRecords"), prep.get("sourceEvidenceFileRecords")]
    inventories += [raw.get(k, []) for k in ("finalNativeFieldRecords", "nativeSamplingRecords", "nativeNccDiagnosticRecords",
        "nativeCheckMeshDiagnosticRecords", "nativeGeneratedDataRecords", "retainedNativeLogRecords")]
    for rows in inventories:
        for name, row in descriptor_rows(rows).items():
            require(bound_role(name), "A bound SHA cannot admit an unknown source/native file role: " + name)
            require(name not in bound or bound[name] == row, "Conflicting byte identities for one current source file."); bound[name] = row
    required_case = {"case/"+name for name in INPUTS} | {"case/constant/polyMesh/"+name for name in ("points", "faces", "owner", "neighbour", "boundary", "cellZones")}
    require(required_case <= set(bound) and {"logs/00-checkMesh.log", "logs/01-foamRun.log"} <= set(bound),
        "Retain all exact initial/model controls, six mesh primitives and both actual native logs.")
    final = descriptor_rows(raw.get("finalNativeFieldRecords"))
    require(len(final) == 7 and {PurePosixPath(n).name for n in final} == {"p", "p_rgh", "U", "alpha.water", "k", "omega", "nut"}
        and len({str(PurePosixPath(n).parent) for n in final}) == 1
        and all(re.fullmatch(r"case/("+TIME+r")/[^/]+", n) and math.isclose(float(n.split('/')[1]), 4*60/1450, rel_tol=5e-11, abs_tol=1e-12) for n in final),
        "Retain exactly seven measured native fields at the authored positive endpoint.")
    require(all(name in bound for name in files if name.startswith("source-baseline/"))
        and set(n for n in files if n.startswith("source-baseline/")) == set(n for n in bound if n.startswith("source-baseline/")),
        "Copy exactly the full source-baseline inventory, including all original provenance and native evidence.")
    require(set(bound) <= set(files), "Every persisted evidence descriptor must exist in the actual archive.")
    return bound, packets[EXECUTION][1], packets[ACCEPTANCE][1]


def unbound_native_role(name, raw):
    """Permit additional actual checkpoint data, never controls or source roles."""
    match = re.fullmatch(r"case/(" + TIME + r")/(?:([^/]+)|(uniform/time)|(fvMesh/polyFaces))", name)
    require(match and math.isfinite(float(match[1])) and 0 < float(match[1]) <= 4*60/1450,
        "Unknown artifact roles are prohibited; only bounded positive-time native state is additional data.")
    field = match[2] or ("time" if match[3] else "polyFaces")
    classes = {"volScalarField"} if field in SCALAR_NAMES else {"surfaceScalarField"} if field in SURFACE_NAMES else \
        {"volVectorField"} if field == "U" else {"surfaceVectorField"} if field == "Uf" else \
        {"dictionary"} if field == "time" else {"surfaceLabelField"} if field == "polyFaces" else set()
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw.decode("ascii"), flags=re.S)
    require("#" not in text and not re.search(r"\b(?:coded|code|codeInclude|codeOptions|codeLibs)\b", text), "Native state must remain data-only.")
    header = re.search(r"\bFoamFile\s*\{([^{}]*)\}", text, re.S)
    require(header and len(re.findall(r"\bFoamFile\b", text)) == 1, "Require one native state FoamFile header.")
    def one(key):
        values = re.findall(r"\b" + key + r"\s+([^;]+);", header[1]); require(len(values) == 1, "Require one actual native header role."); return values[0].strip().strip('"')
    expected_location = "case/" + match[1] + ("/uniform" if field == "time" else "/fvMesh" if field == "polyFaces" else "")
    require(one("format") == "ascii" and one("class") in classes and one("object") == field
        and one("location") == expected_location.removeprefix("case/"), "Native header/object/location must match the actual state data role.")


def archive_digest(stream):
    size, digest = 0, sha256()
    while chunk := stream.read(CHUNK):
        size += len(chunk); require(size <= MAX_ARCHIVE_BYTES, "Bound streamed source ZIP bytes to300MiB."); digest.update(chunk)
    return size, digest.hexdigest()


def source_face_velocity(raw, neighbour_prefix):
    """Exact observed source-baseline500/Uf role, not a new scientific claim."""
    def plain(value):
        text = re.sub(r"/\*.*?\*/|//[^\n]*", "", value.decode("ascii"), flags=re.S)
        require("#" not in text and "\x00" not in text and not re.search(r"\b(?:code|coded[A-Za-z0-9_]*|codeStream|codeExecute|codeInclude|codeOptions|codeLibs|codeContext)\b", text), "Native source face velocity remains data-only.")
        return text
    def header(text, kind, object_name):
        matches = re.findall(r"\bFoamFile\s*\{([^{}]*)\}", text, re.S)
        require(len(matches) == 1, "Require one native source field header.")
        body = matches[0]
        for key, expected in (("format", "ascii"), ("class", kind), ("object", object_name)):
            entries = re.findall(r"\b"+key+r"\s+([^;]+);", body)
            require(len(entries) == 1 and entries[0].strip().strip('"') == expected, "Native source field class/object/ASCII role changed.")
        return body
    text, neighbour = plain(raw), plain(neighbour_prefix)
    body = header(text, "surfaceVectorField", "Uf"); header(neighbour, "labelList", "neighbour")
    locations = re.findall(r'\blocation\s+([^;]+);', body)
    require(len(locations) == 1 and locations[0].strip().strip('"') == "500", "Bind the actual source face-velocity endpoint500.")
    dimensions = re.findall(r"\bdimensions\s*\[([^\[\]]*)\]\s*;", text)
    require(len(dimensions) == 1 and " ".join(dimensions[0].split()) == "0 1 -1 0 0 0 0", "Source Uf requires SI velocity dimensions.")
    count = re.search(r"\}\s*(\d+)\s*\(", neighbour)
    require(count and 0 <= int(count[1]) <= 2_000_000, "Bind bounded actual internal-face count from source neighbour.")
    match = re.search(r"\binternalField\s+(?:uniform\s+(\([^()]*\))|nonuniform\s+List<vector>\s+(\d+)\s*\((.*?)\))\s*;", text, re.S)
    require(match and len(re.findall(r"\binternalField\b", text)) == 1, "Source Uf requires a complete native vector field.")
    values = match[1] if match[1] is not None else match[3]
    expected = 1 if match[1] is not None else int(count[1])
    require(match[1] is not None or int(match[2]) == expected, "Source Uf must cover every current internal face.")
    vectors = re.findall(r"\(([^()]*)\)", values)
    require(len(vectors) == expected and not re.sub(r"\([^()]*\)", "", values).strip(), "Source Uf vector-list shape changed.")
    for vector in vectors:
        tokens = vector.split()
        require(len(tokens) == 3 and all(re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", v)
            and math.isfinite(float(v)) for v in tokens), "Every source Uf vector component must be finite.")
    return {"nativeRole":"surfaceVectorField/Uf", "internalFaceCount":int(count[1]), "storedVectorCount":expected,
        "allStoredInternalVectorsChecked":True,"dimensions":"0 1 -1 0 0 0 0","currentNativeAcceptanceInherited":False}


def verified_payloads(archive, files, bound):
    """Check every member SHA/CRC/role before creating any destination."""
    rows = []
    for name, info in sorted(files.items()):
        source_uf = re.fullmatch(r"source-baseline/(?:impeller|volute)-case/500/Uf", name)
        digest, size, native_data = sha256(), 0, bytearray() if name not in bound or source_uf else None
        with archive.open(info) as stream:
            while chunk := stream.read(CHUNK):
                size += len(chunk); require(size <= info.file_size <= MAX_FILE_BYTES, "Bound each decompressed member before persistence.")
                digest.update(chunk)
                if native_data is not None: native_data.extend(chunk)
        require(size == info.file_size, "Every full member must match its CRC-checked declared size.")
        row = {"path": name, "bytes": size, "sha256": digest.hexdigest()}
        if name in bound:
            require(row == bound[name], "Current archived native/source evidence differs from its bound SHA: " + name)
            if source_uf:
                neighbour = name.removesuffix("500/Uf")+"constant/polyMesh/neighbour"
                require(neighbour in bound and neighbour in files, "Source Uf must bind its actual native mesh neighbour role.")
                with archive.open(files[neighbour]) as stream: prefix = stream.read(65536)
                source_face_velocity(native_data, prefix)
        else: unbound_native_role(name, native_data)
        rows.append(row)
    return rows


def extract(archive_path, run_json, jobs_json, artifacts_json, output_directory, *, source_run_id, source_commit):
    archive_path, output = paths.scope_path(archive_path), paths.scope_path(output_directory, fresh=True)
    metadata_paths = [paths.scope_path(p) for p in (run_json, jobs_json, artifacts_json)]
    metadata = [read_json(p) for p in metadata_paths]
    admitted = validate_metadata(source_run_id, source_commit, *(p[0] for p in metadata))
    require(archive_path.is_file() and archive_path.stat().st_size == admitted["artifactBytes"], "Downloaded ZIP size differs from actual GitHub artifact metadata.")
    created = False; runner = Path(os.environ["RUNNER_TEMP"]).absolute()
    try:
        with archive_path.open("rb") as stream:
            size, digest = archive_digest(stream)
            require(size == admitted["artifactBytes"] and "sha256:" + digest == admitted["githubReportedArchiveDigest"], "Streamed whole ZIP SHA256 must match GitHub.")
            stream.seek(0); require(stream.read(4) == b"PK\x03\x04", "Reject ZIP executable preambles."); stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                files, expanded, entries = members(archive)
                bound, execution_row, acceptance_row = phase_bindings(archive, files, admitted)
                rows = verified_payloads(archive, files, bound)
                report = {"schemaVersion": SCHEMA, "status": "PHASE_BYTES_TRANSFERRED_CURRENT_NATIVE_H0_REPLAY_REQUIRED", **admitted,
                    "sourceDirectory": SOURCE_DIRECTORY, "artifactBytesVerified": True, "sourceExecutionMetadataBound": True,
                    "sourceH0MetadataBound": True, "actualArchiveBytes": size, "actualArchiveSha256": digest,
                    "archiveEntryCount": entries, "archiveExpandedBytes": expanded,
                    "sourceExecutionReport": execution_row, "sourceAcceptanceReport": acceptance_row,
                    "sourceMetadataFileRecords": [{"role": role, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}
                        for role, (_, raw) in zip(("run", "jobs", "artifacts"), metadata, strict=True)],
                    "transferredFileRecords": rows, "limits": {"archiveBytes": MAX_ARCHIVE_BYTES, "expandedBytes": MAX_EXPANDED_BYTES,
                        "entries": MAX_ENTRIES, "fileBytes": MAX_FILE_BYTES, "reportBytes": MAX_REPORT_BYTES},
                    "requiredCurrentReplay": "After installing the checksum-pinned Foundation12 runtime, call teaching_pump_pressure_case.verify_reference(source-reference) before pressure work; do not overwrite its existing acceptance report.",
                    "claimBoundary": "Offline metadata/CRC/byte transfer only. Original reports are preserved, but no current native, numerical H0, cavitation or NPSHr qualification is conferred.",
                    **dict.fromkeys(FALSE_TRANSFER, False)}
                require(len(encoded(report)) <= MAX_REPORT_BYTES, "Bound transfer proof before creating output.")
                paths.scope_path(output, fresh=True); output.mkdir(parents=True); created = True
                source_root = output / SOURCE_DIRECTORY; source_root.mkdir()
                for row in rows:
                    target = source_root.joinpath(*PurePosixPath(row["path"]).parts); paths.scope_path(target.parent)
                    target.parent.mkdir(parents=True, exist_ok=True); paths.scope_path(target)
                    copied, current = 0, sha256()
                    with archive.open(files[row["path"]]) as source, target.open("xb") as destination:
                        while chunk := source.read(CHUNK):
                            copied += len(chunk); require(copied <= row["bytes"], "Bound exact copied member bytes.")
                            current.update(chunk); require(destination.write(chunk) == len(chunk), "Require a complete transfer write.")
                    require((copied, current.hexdigest()) == (row["bytes"], row["sha256"]), "Member changed between CRC verification and copying.")
                for row in rows:
                    target = paths.scope_path(source_root / row["path"])
                    require(target.is_file() and target.stat().st_size == row["bytes"], "Copied file changed before publication.")
                    with target.open("rb") as current: require(archive_digest(current) == (row["bytes"], row["sha256"]), "Copied evidence SHA changed before publication.")
            with archive_path.open("rb") as current: require(archive_digest(current) == (size, digest), "Original ZIP changed during transfer.")
            require(all(paths.read_regular(path, MAX_JSON_BYTES) == raw for path, (_, raw) in zip(metadata_paths, metadata, strict=True)), "GitHub admission metadata changed during transfer.")
            with (output / REPORT).open("xb") as destination:
                raw = encoded(report); require(destination.write(raw) == len(raw), "Require complete sidecar persistence.")
        return report
    except Exception:
        if created: paths.cleanup_owned(output, runner)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive", "run-json", "jobs-json", "artifacts-json", "output-directory"): parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-run-id", required=True); parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    try:
        report = extract(args.archive, args.run_json, args.jobs_json, args.artifacts_json, args.output_directory,
            source_run_id=args.source_run_id, source_commit=args.source_commit)
        print(json.dumps({k: report[k] for k in ("status", "sourceRunId", "sourceCommit", "artifactBytesVerified", "currentSourceH0Verified", "mainApplicationWrites")})); return 0
    except Exception as error:
        print(json.dumps({"status": "TEACHING_PHASE_SOURCE_TRANSFER_CORRECTION_REQUIRED", "action": str(error)[:500],
            "actualCfdRunVerified": False, "phaseH0Eligible": False, "mainApplicationWrites": False})); return 2


if __name__ == "__main__": raise SystemExit(main())
