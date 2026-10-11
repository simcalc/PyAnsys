"""Run four bounded native stages on preserved original educational volumes.

This isolated public FREE worker never runs user scripts or writes Main. Native
completion facts, preliminary numerical qualification and publication differ.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_rotor_original_case as preparation
import frozen_rotor_native_reduction as reduction
import run_frozen_rotor_phase as native

BASE = preparation.base
SCHEMA = "untirta.frozen-rotor-original-openfoam-execution.v1"
MAX_SECONDS = 1200
MAX_WORKSPACE = 1024**3
MAX_LOG = 4*1024**2
MAX_PATHS = 2048
PROGRAMS = ("gmshToFoam", "changeDictionary", "mergeMeshes", "createNonConformalCouples", "checkMesh", "foamRun")


def require(condition, action):
    preparation.require(condition, action)


def context_guard():
    native.context_guard()
    event = os.environ.get("GITHUB_EVENT_PATH")
    require(event, "The actual manual public GitHub event must be verified before preparation.")
    path = Path(event)
    require(not path.is_symlink(), "The public runner event cannot be a symbolic link.")
    record = json.loads(BASE.read_regular(path, 1024**2))
    repository = record.get("repository", {})
    require(repository.get("full_name") == "simcalc/PyAnsys" and repository.get("private") is False
            and repository.get("visibility", "public") == "public", "Only the authorized independent public repository may execute; no paid fallback.")


def workspace_size(directory):
    root = BASE.regular_directory(directory)
    total, paths = 0, 0
    for path in root.rglob("*"):
        paths += 1
        require(paths <= MAX_PATHS and len(path.relative_to(root).parts) <= 8, "The original native workspace path/depth limit was reached.")
        require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)(), "The original native workspace cannot contain links or junctions.")
        if path.is_file():
            size = path.stat().st_size
            require(size <= BASE.MAX_NATIVE_BYTES, "An original native file exceeds the 128 MiB limit.")
            total += size
            require(total <= MAX_WORKSPACE, "The original native workspace exceeds its explicit 1 GiB limit.")
    return total


def child_limits():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (4*1024**3, 4*1024**3))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def runtime_identity(output):
    package = subprocess.run(["dpkg-query", "--show", "--showformat=${Version}", "openfoam12"],
                             env=native.child_environment(), capture_output=True, text=True, timeout=10, check=True)
    require(package.stdout == "20250206", "The exact checksum-pinned Foundation-12 package is required.")
    records = []
    for name in PROGRAMS:
        executable = shutil.which(name)
        require(executable is not None, "An official native program is absent: " + name)
        path = Path(executable).resolve()
        require(path.is_relative_to(Path("/opt/openfoam12")), "Every installed native program must resolve inside the official Foundation-12 package.")
        raw = BASE.read_regular(path, BASE.MAX_NATIVE_BYTES)
        records.append({"program": name, "path": str(path), "bytes": len(raw), "sha256": sha256(raw).hexdigest()})
    identity = {"schemaVersion": "untirta.openfoam-native-runtime-identity.v1", "openFoamFoundationVersion": "12",
                "packageVersion": package.stdout, "officialDebSha256": "b3dcd9a636a5a827bf43ffcf0156922ba6db492123f035eae1046ee759602dd1",
                "binaries": records, "mainApplicationWrites": False}
    BASE.write_exclusive(Path(output) / "runtime-identity.json", BASE.inventory.bounded_json(identity))
    return identity


def execute(arguments, log_path, workspace, deadline, runtime):
    commands = preparation.commands(workspace)
    require(arguments in commands, "Only the four authored original-native command argument lists may execute.")
    index = commands.index(arguments)
    expected = Path(workspace).absolute() / "logs" / f"{index:02d}-{arguments[0]}.log"
    require(Path(log_path).absolute() == expected and not expected.exists() and not expected.is_symlink(), "Use a fresh fixed immutable native log path.")
    row = next((item for item in runtime["binaries"] if item["program"] == arguments[0]), None)
    require(row is not None, "An actual native executable identity is required.")
    executable = Path(row["path"])
    require(executable.resolve().is_relative_to(Path("/opt/openfoam12")), "Execute only the official Foundation-12 program.")
    raw = BASE.read_regular(executable, BASE.MAX_NATIVE_BYTES)
    require(len(raw) == row["bytes"] and sha256(raw).hexdigest() == row["sha256"], "The native executable bytes changed after binding.")
    require(time.monotonic() < deadline, "The original native serial deadline was reached before startup.")
    workspace_size(workspace)
    process, total, started = None, 0, time.monotonic()
    next_progress = started+30
    selector = selectors.DefaultSelector()
    try:
        with expected.open("xb") as output:
            process = subprocess.Popen([str(executable), *arguments[1:]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=native.child_environment(), preexec_fn=child_limits, start_new_session=True, bufsize=0)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                now = time.monotonic()
                require(now < deadline, "The bounded original native execution timed out.")
                workspace_size(workspace)
                if now >= next_progress:
                    print(json.dumps({"stage": arguments[0], "elapsedSeconds": round(now-started, 1), "status": "NativeRunning",
                                      "actualCfdRunVerified": False, "mainApplicationWrites": False}), flush=True)
                    next_progress = now+30
                for key, _ in selector.select(timeout=.25):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj); continue
                    remaining = MAX_LOG-total
                    output.write(chunk[:remaining]); output.flush(); total += min(remaining, len(chunk))
                    require(len(chunk) <= remaining, "The immutable original native log reached its 4 MiB limit.")
            code = process.wait(timeout=3)
            workspace_size(workspace)
            require(time.monotonic() < deadline, "The original native deadline was reached at process completion.")
            require(code == 0, arguments[0] + " returned native exit " + str(code))
    finally:
        if process is not None:
            native.stop_owned(process); process.stdout.close()
        selector.close()
    raw, metadata = preparation.descriptor(workspace, "logs/"+expected.name, limit=MAX_LOG)
    text = raw.decode("utf-8", errors="strict")
    require(re.search(r"(?m)^End\s*$", text) and not re.search(r"FOAM FATAL|Segmentation fault|Floating point exception", text, re.I),
            "Actual native completion without a fatal error is required: " + arguments[0])
    return {"program": arguments[0], "exitCode": 0, "elapsedSeconds": round(time.monotonic()-started, 3),
            "log": expected.name, "logBytes": metadata["bytes"], "logSha256": metadata["sha256"]}, text


def bind_manifest(output):
    bound = reduction.build_manifest(output)
    for name, key in (("geometry-identity.json", "geometryIdentity"), ("native-input-manifest.json", "manifest"), ("classroom-study.json", "study")):
        BASE.write_exclusive(output / name, reduction.encoded(bound[key]))
    return {name: preparation.descriptor(output, name, limit=BASE.inventory.MAX_REPORT_BYTES)[1]
            for name in ("geometry-identity.json", "native-input-manifest.json", "classroom-study.json")}


def final_fields(output, cells):
    """Scan every actual endpoint field; four primary fields retain reduction roles."""
    directory = BASE.regular_directory(output / "impeller-case/500")
    dimensions = {"nut": "0 2 -1 0 0 0 0", "totalPressurePa": "1 -1 -2 0 0 0 0", "staticPressurePa": "1 -1 -2 0 0 0 0",
                  "U2": "0 2 -2 0 0 0 0", "yPlus": "0 0 0 0 0 0 0", "phi": "0 3 -1 0 0 0 0"}
    primary, all_rows, scans = [], [], []
    require(all((directory/name).is_file() for name in ("p", "U", "k", "omega", "nut")), "Actual 500-iteration native fields are required, including turbulent viscosity.")
    for path in sorted(directory.iterdir()):
        require(not path.is_symlink() and not getattr(path, "is_junction", lambda: False)(), "Actual final native fields cannot be links.")
        if path.is_dir():
            require(path.name in {"uniform", "polyMesh"}, "Unrecognized final native state directory."); continue
        name = path.name
        raw, row = preparation.descriptor(output, "impeller-case/500/"+name)
        text = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw.decode("ascii"), flags=re.S)
        require(not re.search(r"(?m)^\s*#|\bcodeStream\b|\bcodeExecute\b|\b(?:nan|inf|infinity)\b", text, re.I), "Final native field values must be finite plain ASCII.")
        if name in {"p", "U", "k", "omega"}:
            scans.append(reduction.finite_field(raw, name, cells)); primary.append({"name": name, "bytes": row["bytes"], "sha256": row["sha256"]})
        else:
            require(name in dimensions and re.search(r"\bformat\s+ascii\s*;", text) and re.search(r"\bobject\s+"+name+r"\s*;", text)
                    and re.search(r"\bclass\s+"+("surfaceScalarField" if name=="phi" else "volScalarField")+r"\s*;", text)
                    and re.search(r"\bdimensions\s*\[\s*"+dimensions[name]+r"\s*\]\s*;", text), "The actual endpoint field has an unsupported native role or SI dimensions.")
            match = re.search(r"\binternalField\s+(uniform\s+([^;]+)|nonuniform\s+List<scalar>\s+(\d+)\s*\((.*?)\))\s*;", text, re.S)
            require(match, "A complete actual scalar internal field is required.")
            count = 1 if match[2] is not None else int(match[3])
            if match[2] is None:
                expected_count = BASE.native_list(output/"impeller-case/constant/polyMesh/neighbour")[0] if name=="phi" else cells
                require(count == expected_count, "Every actual endpoint scalar field must cover its native cells or internal faces.")
            tokens = (match[2] if match[2] is not None else match[4]).split()
            require(len(tokens) == count and all(re.fullmatch(reduction.FLOAT, token) and math_finite(token) for token in tokens), "Every actual endpoint scalar value must be finite.")
            if name in {"nut", "U2", "yPlus"}:
                require(all(float(token) >= 0 for token in tokens), "Turbulent viscosity, squared speed and yPlus cannot be negative.")
            scans.append({"name": name, "internalValueCount": count, "finiteValuesVerified": True})
        all_rows.append(row)
    return primary, all_rows, scans


def math_finite(token):
    import math
    return math.isfinite(float(token))


def retain_logs(output):
    result = []
    for index, arguments in enumerate(preparation.commands(output)):
        relative = f"logs/{index:02d}-{arguments[0]}.log"
        path = output/relative
        if path.exists():
            require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_LOG, "Only bounded ordinary diagnostic logs may be retained.")
            raw = BASE.read_regular(path, MAX_LOG) if path.stat().st_size else b""
            result.append({"path": relative, "bytes": len(raw), "sha256": sha256(raw).hexdigest()})
    return result


def run(converted_mesh_directory, output_directory, *, pressure_reference_absolute_pa=300000):
    context_guard()
    started = time.monotonic(); deadline = started+MAX_SECONDS
    prepared = preparation.prepare(converted_mesh_directory, output_directory, pressure_reference_absolute_pa=pressure_reference_absolute_pa)
    output = BASE.regular_directory(output_directory)
    report = {"schemaVersion": SCHEMA, "githubRunId": os.environ.get("GITHUB_RUN_ID"), "githubSourceCommit": os.environ.get("GITHUB_SHA"),
              "status": "ORIGINAL_NATIVE_EXECUTION_REQUIRED", "exampleDataCommit": BASE.inventory.PIN,
              "preparationReportSha256": preparation.descriptor(output, "openfoam-preparation.json", limit=BASE.inventory.MAX_REPORT_BYTES)[1]["sha256"],
              "nativeConversionReportSha256": prepared["nativeConversionReportSha256"], "stages": [], "limits": {**prepared["limits"], "maximumLogBytes": MAX_LOG},
              "openFoamConversionVerified": True, "nativeGeometryQualityVerified": False, "physicalWallTypesVerified": False,
              "coupledInterfaceCoverageVerified": False, "nativeSolverCompleted": False, "finiteNativeFieldsVerified": False,
              "inputArtifactsImmutable": False, "simulationExecuted": False, "actualCfdRunVerified": False, "qualifiedBaseline": False,
              "cavitationSweepExecuted": False, "npsh3Qualified": False, "physicalTransientExecuted": False, "mainApplicationWrites": False,
              "claimBoundary": "Original-volume native execution facts under explicit classroom assumptions. Numerical baseline qualification, mesh/time convergence and physical validation require separate actual evidence."}
    current, bound_rows = "runtime-identity", {}
    (output/"logs").mkdir()
    try:
        runtime = runtime_identity(output)
        immutable = {name: preparation.descriptor(output,name,limit=BASE.inventory.MAX_REPORT_BYTES)[1] for name in
                     ("openfoam-preparation.json", "native-conversion.json", "runtime-identity.json")}
        report["runtimeIdentitySha256"] = immutable["runtime-identity.json"]["sha256"]
        for index, arguments in enumerate(preparation.commands(output)):
            current = arguments[0]
            if current == "foamRun":
                bound_rows = bind_manifest(output)
                # The complete current manifest binds actual rewritten NCC inputs.
                manifest = reduction.record(output,"native-input-manifest.json")[0]
                bound_inputs = manifest["files"]
            stage, text = execute(arguments, output/"logs"/f"{index:02d}-{current}.log", output, deadline, runtime)
            report["stages"].append(stage)
            if current == "checkMesh":
                # Native quality diagnostics never replace the unchanged mesh
                # admission gate. Retain an actual bounded cell set even when
                # checkMesh exits zero but reports failed geometry checks.
                try:
                    if "Mesh OK." not in text:
                        import frozen_rotor_native_quality as quality
                        diagnostic = quality.inspect(output/"impeller-case", output/"logs"/stage["log"])
                        BASE.write_exclusive(output/"native-quality-diagnostic.json", BASE.inventory.bounded_json(diagnostic))
                        report["nativeQualityDiagnostic"] = preparation.descriptor(output,"native-quality-diagnostic.json",limit=BASE.inventory.MAX_REPORT_BYTES)[1]
                except Exception as diagnostic_error:
                    report["nativeQualityDiagnostic"] = {"status":"QUALITY_DIAGNOSTIC_CORRECTION_REQUIRED", "message":str(diagnostic_error)[:500]}
                report["mergedNativeMeshEvidence"] = BASE.verify_merged_mesh(output/"impeller-case", {row["component"]:row for row in prepared["components"]}, output/"logs"/stage["log"])
                report.update(nativeGeometryQualityVerified=True, physicalWallTypesVerified=True, coupledInterfaceCoverageVerified=True)
            elif current == "foamRun":
                report["nativeSolverCompleted"] = True
                history = reduction.parse_solver_log(text)
                require(max(history)==500, "The actual steady solver must reach the declared 500 outer iterations.")
                cells = sum(row["cellCount"] for row in prepared["components"])
                workspace_size(output)
                primary, fields, scans = final_fields(output,cells)
                report.update(finalNativeFields=primary, finalNativeFieldRecords=fields, finiteNativeFieldEvidence=scans, finiteNativeFieldsVerified=True, simulationExecuted=True)
                require(all(preparation.descriptor(output,row["path"])[1] == {key:row[key] for key in ("path","bytes","sha256")} for row in bound_inputs),
                        "Actual bound native inputs changed during the solver.")
        require(all(preparation.descriptor(output,name,limit=BASE.inventory.MAX_REPORT_BYTES)[1]==row for name,row in {**immutable,**bound_rows}.items()),
                "An immutable input report or source geometry identity changed during native execution.")
        preparation.validate_inputs(converted_mesh_directory)
        require(time.monotonic()<deadline, "The original native deadline was reached before final evidence verification.")
        report.update(inputArtifactsImmutable=True, status="ORIGINAL_MRF_EXECUTED_ACCEPTANCE_REQUIRED", actualCfdRunVerified=True)
    except Exception as error:
        report.update(status="ORIGINAL_NATIVE_STAGE_CORRECTION_REQUIRED", reasonCode="CFD_ORIGINAL_NATIVE_STAGE_CORRECTION_REQUIRED", actualCfdRunVerified=False,
                      diagnostic={"program":current,"type":type(error).__name__,"message":str(error)[:500]})
    try:
        report.update(retainedNativeLogRecords=retain_logs(output), workspaceBytes=workspace_size(output))
    except Exception as error:
        report.update(status="ORIGINAL_NATIVE_RETENTION_CORRECTION_REQUIRED",actualCfdRunVerified=False,
                      diagnostic={"program":"evidence-retention","type":type(error).__name__,"message":str(error)[:500]})
    report["elapsedSeconds"] = round(time.monotonic()-started,3)
    raw = BASE.inventory.bounded_json(report); BASE.write_exclusive(output/"openfoam-execution.json",raw)
    summary = {**report,"nativeExecutionReport":{"name":"openfoam-execution.json","bytes":len(raw),"sha256":sha256(raw).hexdigest()}}
    # Root's reviewed additive reducer/package adapters handle this exact lane.
    # Native execution evidence stays immutable even if later acceptance fails.
    if report["actualCfdRunVerified"]:
        try:
            result = reduction.reduce_native_baseline(output)
            reduced = BASE.inventory.bounded_json(result); BASE.write_exclusive(output/"native-baseline-reduction.json",reduced)
            summary.update(qualifiedBaseline=result.get("qualifiedBaseline") is True,
                           nativeReduction={"name":"native-baseline-reduction.json","bytes":len(reduced),"sha256":sha256(reduced).hexdigest()})
        except Exception as error:
            summary.update(status="ORIGINAL_NATIVE_ACCEPTANCE_CORRECTION_REQUIRED",qualifiedBaseline=False,
                           diagnostic={"program":"native-reduction","type":type(error).__name__,"message":str(error)[:500]})
    BASE.write_exclusive(output/"openfoam-acceptance.json",BASE.inventory.bounded_json(summary))
    display = {key:summary[key] for key in ("status","actualCfdRunVerified","qualifiedBaseline","mainApplicationWrites")}
    for key in ("diagnostic", "nativeQualityDiagnostic"):
        if key in summary:
            display[key] = summary[key]
    print(json.dumps(display))
    return 0 if report["actualCfdRunVerified"] else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--converted-mesh-directory",type=Path,required=True)
    parser.add_argument("--output-directory",type=Path,required=True)
    parser.add_argument("--pressure-reference-absolute-pa",type=float,default=300000)
    args=parser.parse_args()
    try:
        return run(args.converted_mesh_directory,args.output_directory,pressure_reference_absolute_pa=args.pressure_reference_absolute_pa)
    except Exception as error:
        print(json.dumps({"status":"ORIGINAL_NATIVE_PREPARATION_CORRECTION_REQUIRED","action":str(error)[:500],
                          "actualCfdRunVerified":False,"mainApplicationWrites":False}));return 2


if __name__=="__main__":
    raise SystemExit(main())
