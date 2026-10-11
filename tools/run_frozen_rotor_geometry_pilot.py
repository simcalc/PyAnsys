"""Measure a fixed original-cell agglomeration prototype with native checkMesh.

This is a diagnostic geometry job, not a baseline job. It never calls foamRun,
never lowers a native quality threshold and cannot qualify performance data.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agglomerate_frozen_rotor_native as geometry
import frozen_rotor_original_case as recipe
import run_frozen_rotor_original as native

BASE = recipe.base
SCHEMA = "untirta.frozen-rotor-native-geometry-pilot.v1"
MAX_SECONDS = 600
GEOMETRY_KINDS = ("protected-union", "determinant-repair")


def require(condition, message):
    recipe.require(condition, message)


def bind_geometry(geometry_directory, geometry_kind):
    require(geometry_kind in GEOMETRY_KINDS, "Choose an explicit supported geometry derivation; no guessed report or fallback.")
    if geometry_kind == "protected-union":
        return geometry.verify_output(geometry_directory)
    import repair_frozen_rotor_determinant as repair
    return repair.verify_output(geometry_directory)


def prepare(geometry_directory, output_directory, geometry_kind="protected-union"):
    """Author only fixed recipes from the independently verified prototype."""
    source = BASE.regular_directory(geometry_directory)
    bound = bind_geometry(source, geometry_kind)
    target = Path(output_directory).absolute()
    BASE.inventory.output_path(target / "geometry-pilot-preparation.json")
    require(not target.exists() and not target.is_symlink(), "Use a fresh isolated geometry diagnostic directory.")
    target.mkdir(parents=True)
    components, records = {}, []
    for row in bound["report"]["components"]:
        name, folder = row["name"], row["casePath"]
        require(name in {"impeller", "volute"} and folder == name+"-case", "Only the two fixed official source components are supported.")
        for file in [*row["nativeFiles"], *row["mappingFiles"]]:
            raw, actual = recipe.descriptor(source, file["path"])
            require(actual == file, "A derived mesh or exact source-membership map changed before the diagnostic.")
            BASE.write_exclusive(target/file["path"], raw); records.append(actual)
        for path, text in recipe.case_files(name, 300000).items():
            raw = text.encode("ascii"); relative = folder+"/"+path
            BASE.write_exclusive(target/relative, raw)
            records.append({"path":relative,"bytes":len(raw),"sha256":sha256(raw).hexdigest()})
        components[name] = {"component":name,"caseDirectory":folder,"cellCount":row["cellCount"],"nodeCount":row["nodeCount"],
            "faceCount":row["faceCount"],"patchFaces":{p["name"]:p["nFaces"] for p in row["patches"]},"cellZone":row["cellZone"]["name"]}
    report_name = "agglomeration-report.json" if geometry_kind == "protected-union" else "determinant-repair-report.json"
    raw, geometry_record = recipe.descriptor(source,report_name,limit=BASE.inventory.MAX_REPORT_BYTES)
    require(geometry_record == bound["reportFile"], "The exact selected derivation report changed before transfer.")
    BASE.write_exclusive(target/report_name,raw)
    if geometry_kind == "determinant-repair":
        for name, key, limit in (("derivation-policy.json","derivationPolicyFile",BASE.inventory.MAX_REPORT_BYTES),
                                 ("source-quality-localization.json","sourceQualityDiagnosticFile",32*1024**2)):
            raw, actual = recipe.descriptor(source,name,limit=limit)
            require(actual == bound["report"][key], "The selected policy or measured source targets changed before transfer.")
            BASE.write_exclusive(target/name,raw); records.append(actual)
    commands = recipe.commands(target)[:3]
    require([c[0] for c in commands] == ["mergeMeshes","createNonConformalCouples","checkMesh"], "No solver command belongs to a geometry diagnostic.")
    report = {"schemaVersion":SCHEMA,"status":"GEOMETRY_PILOT_NATIVE_DIAGNOSTIC_REQUIRED",
        "geometryKind":geometry_kind,"derivedGeometryReport":geometry_record,
        "components":components,"authoredAndCopiedFiles":records,"nativeCommandArguments":commands,
        "classroomMetresPerSourceCoordinateUnit":1,"nativeCoordinateUnitsVerified":False,
        "meshQualityCheckedBySolver":False,"nativeQualityAccepted":False,"simulationExecuted":False,"actualCfdRunVerified":False,
        "qualifiedBaseline":False,"npsh3Qualified":False,"physicalTransientExecuted":False,"mainApplicationWrites":False}
    BASE.write_exclusive(target/"geometry-pilot-preparation.json",BASE.inventory.bounded_json(report))
    require(bind_geometry(source,geometry_kind) == bound, "Actual derived inputs changed while authoring the geometry diagnostic.")
    native.workspace_size(target)
    return report


def run(geometry_directory, output_directory, geometry_kind="protected-union"):
    require(geometry_kind in GEOMETRY_KINDS, "Choose an explicit supported geometry derivation before native execution.")
    native.context_guard()
    start = time.monotonic(); deadline = start+MAX_SECONDS
    prepared = prepare(geometry_directory,output_directory,geometry_kind)
    output = BASE.regular_directory(output_directory); (output/"logs").mkdir()
    report = {"schemaVersion":SCHEMA,"status":"GEOMETRY_PILOT_NATIVE_DIAGNOSTIC_REQUIRED","geometryKind":geometry_kind,
        "githubRunId":os.environ.get("GITHUB_RUN_ID"),"githubSourceCommit":os.environ.get("GITHUB_SHA"),
        "stages":[],"nativeDiagnosticCompleted":False,"nativeQualityAccepted":False,"simulationExecuted":False,
        "actualCfdRunVerified":False,"qualifiedBaseline":False,"npsh3Qualified":False,"physicalTransientExecuted":False,
        "mainApplicationWrites":False,"limits":{"maximumSeconds":MAX_SECONDS,"memoryBytes":4*1024**3,"cpus":1,
        "maximumWorkspaceBytes":native.MAX_WORKSPACE,"maximumLogBytes":native.MAX_LOG},
        "claimBoundary":"Completed native diagnostic means actual geometry checks were observed. Quality acceptance is separate; no CFD solver, baseline, NPSH3, transient or convergence result is produced."}
    current = "runtime-identity"
    try:
        identity = native.runtime_identity(output)
        for index, arguments in enumerate(prepared["nativeCommandArguments"]):
            current = arguments[0]
            require(current in {"mergeMeshes","createNonConformalCouples","checkMesh"}, "Geometry diagnostics cannot execute a CFD solver.")
            stage, text = native.execute(arguments,output/"logs"/f"{index:02d}-{current}.log",output,deadline,identity)
            report["stages"].append(stage)
        if "Mesh OK." not in text:
            import frozen_rotor_native_quality as quality
            diagnostic = quality.inspect(output/"impeller-case",output/"logs/02-checkMesh.log")
            BASE.write_exclusive(output/"native-quality-diagnostic.json",BASE.inventory.bounded_json(diagnostic))
            report["nativeQualityDiagnostic"] = recipe.descriptor(output,"native-quality-diagnostic.json",limit=BASE.inventory.MAX_REPORT_BYTES)[1]
        report["nativeDiagnosticCompleted"] = True
        # The strict acceptance gate is deliberately identical to the original
        # route. A failed geometry check never becomes a qualified mesh.
        try:
            evidence = BASE.verify_merged_mesh(output/"impeller-case",prepared["components"],output/"logs/02-checkMesh.log")
            report.update(nativeQualityAccepted=True,mergedNativeMeshEvidence=evidence,status="GEOMETRY_PILOT_NATIVE_QUALITY_ACCEPTED")
        except BASE.CaseIssue as error:
            report.update(status="GEOMETRY_PILOT_NATIVE_QUALITY_CORRECTION_REQUIRED",qualityAction=str(error)[:500])
        current_bound = bind_geometry(geometry_directory,geometry_kind)
        require(current_bound["reportFile"] == prepared["derivedGeometryReport"] and current_bound["report"]["volumeMeshDerived"] is True,
                "The independent derived mesh report changed during native inspection.")
        require(time.monotonic()<deadline,"The native diagnostic deadline was exceeded.")
    except Exception as error:
        report.update(status="GEOMETRY_PILOT_DIAGNOSTIC_CORRECTION_REQUIRED",nativeDiagnosticCompleted=False,nativeQualityAccepted=False,
            diagnostic={"program":current,"type":type(error).__name__,"message":str(error)[:500]})
    report.update(elapsedSeconds=round(time.monotonic()-start,3),retainedNativeLogRecords=native.retain_logs(output),workspaceBytes=native.workspace_size(output))
    BASE.write_exclusive(output/"geometry-pilot-execution.json",BASE.inventory.bounded_json(report))
    print(json.dumps({k:report[k] for k in ("status","nativeDiagnosticCompleted","nativeQualityAccepted","actualCfdRunVerified","mainApplicationWrites")},allow_nan=False))
    # Only completion of this explicitly named diagnostic task determines its
    # exit status. Consumers must independently admit nativeQualityAccepted.
    return 0 if report["nativeDiagnosticCompleted"] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry-directory",type=Path,required=True)
    parser.add_argument("--output-directory",type=Path,required=True)
    parser.add_argument("--geometry-kind",choices=GEOMETRY_KINDS,default="protected-union")
    args = parser.parse_args()
    try:
        return run(args.geometry_directory,args.output_directory,args.geometry_kind)
    except Exception as error:
        print(json.dumps({"status":"GEOMETRY_PILOT_DIAGNOSTIC_PREPARATION_REQUIRED","action":str(error)[:500],
            "nativeQualityAccepted":False,"actualCfdRunVerified":False,"mainApplicationWrites":False}));return 2


if __name__=="__main__":
    raise SystemExit(main())
