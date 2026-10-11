"""Read actual failed Foundation-12 quality sets; never change a mesh or gate.

Diagnostic completion does not mean mesh acceptance. Complete native cell IDs
remain in the hash-bound cellSet; this compact report projects samples/counts.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_openfoam_case as base
import frozen_rotor_cavitation_case as safety

SCHEMA = "untirta.frozen-rotor-native-quality-diagnostic.v1"
MAX_FILE = 128*1024**2
MAX_LOG = 4*1024**2
MAX_CELLS = 2_000_000
SAMPLE_COUNT = 32
FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
SOURCES = ["https://github.com/OpenFOAM/OpenFOAM-12/blob/master/applications/utilities/mesh/manipulation/checkMesh/checkMesh.C",
           "https://github.com/OpenFOAM/OpenFOAM-12/blob/master/src/meshCheck/checkGeometry.C",
           "https://github.com/OpenFOAM/OpenFOAM-12/blob/master/src/meshCheck/polyMeshCheck/polyMeshCheck.C",
           "https://github.com/OpenFOAM/OpenFOAM-12/blob/master/src/meshTools/sets/topoSets/topoSet.C"]


class QualityIssue(ValueError):
    pass


def require(condition, action):
    if not condition:
        raise QualityIssue(action)


def read(path, runner_root, limit):
    path = Path(path).absolute()
    require(path.resolve().is_relative_to(runner_root.resolve()), "Native diagnostics must stay inside the independent RUNNER_TEMP.")
    for item in (path, *path.parents):
        require(not item.is_symlink() and not getattr(item,"is_junction",lambda:False)(), "Native diagnostic evidence cannot traverse links or junctions.")
    descriptor = os.open(path, os.O_RDONLY | getattr(os,"O_NOFOLLOW",0))
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit, "An ordinary nonempty bounded native evidence file is required.")
        with os.fdopen(descriptor,"rb",closefd=False) as stream:
            raw = stream.read(limit+1)
        after = os.fstat(descriptor)
        require(len(raw)==before.st_size and len(raw)<=limit and before.st_size==after.st_size and before.st_mtime_ns==after.st_mtime_ns,
                "Actual native diagnostic bytes changed while being read.")
    finally:
        os.close(descriptor)
    current = path.stat()
    require(stat.S_ISREG(current.st_mode) and not path.is_symlink() and current.st_size==after.st_size and current.st_mtime_ns==after.st_mtime_ns,
            "The actual native diagnostic file changed at its persistence boundary.")
    return raw, {"path":path.relative_to(runner_root).as_posix(),"bytes":len(raw),"sha256":sha256(raw).hexdigest()}


def native_body(raw, object_name, allowed_classes):
    try:
        text=raw.decode("ascii")
    except UnicodeError as error:
        raise QualityIssue("Plain ASCII native quality evidence is required.") from error
    text=re.sub(r"/\*.*?\*/|//[^\n]*","",text,flags=re.S)
    require(not re.search(r"#|\$|\bcodeStream\b|\bcodeExecute\b|\bcodeInclude\b",text),
            "Native quality evidence cannot contain includes, substitutions or executable directives.")
    header=re.match(r"\s*FoamFile\s*\{([^{}]*)\}",text)
    require(header,"An actual native FoamFile header is required.")
    kind=re.search(r"\bclass\s+(\w+)\s*;",header[1])
    require(kind and kind[1] in allowed_classes and re.search(r"\bformat\s+ascii\s*;",header[1])
            and re.search(r'\bobject\s+"?'+re.escape(object_name)+r'"?\s*;',header[1]), "Native quality object/class/storage must match the exact evidence role.")
    return text[header.end():].strip()


def labels(body, maximum, *, expected=None):
    match=re.fullmatch(r"(\d+)\s*\(([^()]*)\)\s*;?",body,re.S)
    require(match,"A complete counted native integer set/list is required.")
    count=int(match[1]);require(0 < count <= maximum,"A native label count exceeds the declared actual cell domain.")
    if expected is not None:
        require(count==expected,"The actual native cellSet count differs from the completed checkMesh log.")
    tokens=match[2].split()
    require(len(tokens)==count and all(re.fullmatch(r"\d+",value) for value in tokens),"Every native cell ID must be a complete nonnegative integer.")
    ids={int(value) for value in tokens}
    require(len(ids)==count and all(value < maximum for value in ids), "Native cell IDs must be unique and inside the actual merged cell domain.")
    return ids


def log_metrics(raw):
    text=raw.decode("utf-8",errors="strict")
    require(re.search(r"(?m)^End\s*$",text) and not re.search(r"FOAM FATAL|Segmentation fault|Floating point exception",text,re.I),
            "The diagnostic needs a completed actual checkMesh log without a fatal error.")
    cells=re.findall(r"(?m)^\s*cells:\s*(\d+)\s*$",text)
    failures=re.findall(r"(?m)^Failed (\d+) mesh checks\.\s*$",text)
    determinant=re.findall(r"Cell determinant \(wellposedness\) : minimum: ("+FLOAT+r") average: ("+FLOAT+r")",text)
    small=re.findall(r"\*\*\*Cells with small determinant \(< ("+FLOAT+r")\) found, number of cells: (\d+)",text)
    require(len(cells)==len(failures)==len(determinant)==len(small)==1 and "Mesh OK." not in text,
            "Exactly one failed native determinant diagnostic and merged-cell count are required.")
    total,bad,failed=int(cells[0]),int(small[0][1]),int(failures[0])
    low,average,threshold=float(determinant[0][0]),float(determinant[0][1]),float(small[0][0])
    require(0 < bad <= total <= MAX_CELLS and failed>0 and all(math.isfinite(value) for value in (low,average,threshold))
            and threshold==.001 and low < threshold and low <= average,
            "Retain the exact native 0.001 wellposedness threshold, finite metrics and actual failed-cell count.")
    return {"mergedCellCount":total,"underdeterminedCellCount":bad,"failedMeshCheckCount":failed,
            "minimumCellDeterminant":low,"averageCellDeterminant":average,"nativeDeterminantThreshold":threshold,
            "cellDeterminantUnit":"dimensionless","nativeQualityAccepted":False}


def zones(raw, cell_count):
    # Foundation-12 native mergeMeshes writes this actual zone container as
    # cellZoneList. The authored converter uses regIOobject; both still require
    # the exact object and complete disjoint two-zone cell membership.
    body=native_body(raw,"cellZones",{"regIOobject","cellZoneMesh","cellZoneList"})
    match=re.fullmatch(r"2\s*\((.*)\)\s*;?",body,re.S)
    require(match,"Exactly two actual merged fluid cell zones are required.")
    entries=list(re.finditer(r'"?([A-Za-z0-9_-]+)"?\s*\{([^{}]*)\}',match[1]))
    require(len(entries)==2 and {entry[1] for entry in entries}=={"rotating-fluid","stationary-fluid"},
            "Both exact native merged fluid-zone identities are required.")
    result,used,end={},set(),0
    for entry in entries:
        require(not match[1][end:entry.start()].strip(),"Every actual merged cell-zone dictionary must be accounted for.")
        block=entry[2];kind=re.search(r"\btype\s+cellZone\s*;",block)
        ids=re.search(r"\bcellLabels\s+(?:List<label>\s+)?(\d+\s*\([^()]*\))\s*;",block,re.S)
        require(kind and ids,"An actual cellZone type and complete native cellLabels list are required.")
        local=labels(ids[1],cell_count)
        require(not used & local,"The actual merged fluid zones must not overlap.")
        used |= local;result[entry[1]]=local;end=entry.end()
    require(not match[1][end:].strip() and len(used)==cell_count,"Both actual fluid zones must cover every merged cell exactly once.")
    return result


def _inspect(case_directory, check_mesh_log):
    case=safety.inside_runner(case_directory)
    runner=base.regular_directory(os.environ.get("RUNNER_TEMP",""))
    log_raw,log_row=read(check_mesh_log,runner,MAX_LOG)
    metrics=log_metrics(log_raw)
    set_path=case/"constant/polyMesh/sets/underdeterminedCells"
    set_raw,set_row=read(set_path,runner,MAX_FILE)
    ids=labels(native_body(set_raw,"underdeterminedCells",{"cellSet"}),metrics["mergedCellCount"],expected=metrics["underdeterminedCellCount"])
    zone_path=case/"constant/polyMesh/cellZones"
    zone_raw,zone_row=read(zone_path,runner,MAX_FILE)
    actual_zones=zones(zone_raw,metrics["mergedCellCount"])
    groups={name:{"actualZoneCellCount":len(values),"underdeterminedCellCount":len(values & ids),
                  "sampleMergedCellIds":sorted(values & ids)[:SAMPLE_COUNT]} for name,values in sorted(actual_zones.items())}
    require(sum(row["underdeterminedCellCount"] for row in groups.values())==len(ids),"Every failed native cell must bind exactly one actual merged fluid zone.")
    for path,row,limit in ((Path(check_mesh_log),log_row,MAX_LOG),(set_path,set_row,MAX_FILE),(zone_path,zone_row,MAX_FILE)):
        require(read(path,runner,limit)[1]==row,"Native log, cellSet or cellZones bytes changed while inspecting the diagnostic.")
    report={"schemaVersion":SCHEMA,"status":"NativeQualityDiagnosticComplete","diagnosticComplete":True,**metrics,
            "nativeEvidenceRecords":[log_row,set_row,zone_row],"nativeCellSetFile":set_row,"cellZonesFile":zone_row,"checkMeshLogFile":log_row,
            "failedCellIdConvention":"Actual zero-based merged OpenFOAM cell labels; component assignment comes from current cellZones, never inferred offsets.",
            "sampleMergedCellIds":sorted(ids)[:SAMPLE_COUNT],"cellZoneClassification":groups,
            "completeCellIdsRetainedInNativeCellSet":True,"jsonIncludesAllCellIds":False,"sampleMaximumCountPerGroup":SAMPLE_COUNT,
            "nativeFailedCellSetVerified":True,"sourceCellOrderVerified":False,
            "equationStage":{"input":{"nativeCellSetCount":len(ids),"nativeLogCount":metrics["underdeterminedCellCount"]},
                "equation":"N_cellSet = N_failed_native_log = N_rotating_bad + N_stationary_bad",
                "substitution":str(len(ids))+" = "+str(metrics["underdeterminedCellCount"])+" = "+" + ".join(str(groups[name]["underdeterminedCellCount"]) for name in ("rotating-fluid","stationary-fluid")),
                "result":{"completeNativeCountBindingVerified":True,"unit":"cells"},"calculationAuthority":"Python"},
            "nativeGeometryQualityVerified":False,"qualifiedBaseline":False,"actualCfdRunVerified":False,"simulationExecuted":False,
            "meshModified":False,"acceptanceThresholdChanged":False,"mainApplicationWrites":False,
            "claimBoundary":"A completed native quality diagnostic locates cells that failed the unchanged Foundation-12 determinant criterion. It does not accept the mesh, run CFD or establish pump performance.",
            "references":SOURCES}
    return report,{name:values & ids for name,values in actual_zones.items()}


def inspect(case_directory, check_mesh_log):
    """Return compact current native evidence; no original-source ID mapping."""
    return _inspect(case_directory,check_mesh_log)[0]


def complete_failed_ids_by_zone(case_directory, check_mesh_log):
    """Return complete failed merged labels grouped by actual native cellZones.

    These zero-based labels are not original component cell IDs. A downstream
    source comparison must separately prove the original-to-merged mapping.
    The same bounded reads, counts and immutable byte checks as inspect apply.
    """
    return _inspect(case_directory,check_mesh_log)[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-directory",type=Path,required=True)
    parser.add_argument("--check-mesh-log",type=Path,required=True)
    parser.add_argument("--output-report",type=Path,required=True)
    args=parser.parse_args()
    try:
        target=base.inventory.output_path(args.output_report)
        result=inspect(args.case_directory,args.check_mesh_log)
        base.write_exclusive(target,base.inventory.bounded_json(result))
        print(json.dumps({key:result[key] for key in ("status","diagnosticComplete","nativeQualityAccepted","underdeterminedCellCount","actualCfdRunVerified")}));return 0
    except Exception as error:
        print(json.dumps({"status":"NativeQualityDiagnosticCorrectionRequired","diagnosticComplete":False,"nativeQualityAccepted":False,
                          "action":str(error)[:500],"actualCfdRunVerified":False,"mainApplicationWrites":False}));return 2


if __name__=="__main__":
    raise SystemExit(main())
