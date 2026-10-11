"""Read verified official Fluent mesh metadata without starting Fluent.

This tool inspects two immutable upstream mesh files with h5py in read-only
mode. Counts and coordinate bounds describe stored mesh data. They do not
establish a native setup case, mesh quality, a licensed session or CFD results.
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
import sys
from urllib.parse import urlsplit
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_learning_package import FILES, PIN

MESH_NAMES = ("impeller.msh.h5", "volute.msh.h5")
MEDIA_PREFIX = "https://media.githubusercontent.com/media/ansys/example-data/" + PIN + "/pyfluent/examples/pump-volute/"
SCHEMA = "untirta.frozen-rotor-mesh-inventory.v1"
SIGNATURE = b"\x89HDF\r\n\x1a\n"
MAX_REPORT_BYTES = 128 * 1024
MAX_HDF5_OBJECTS = 1024
MAX_HDF5_DEPTH = 64
MAX_SMALL_VALUES = 256
MAX_TEXT_BYTES = 16 * 1024
MAX_COORDINATE_ROWS = 5_000_000
COORDINATE_CHUNK = 4096


class InspectionIssue(ValueError):
    pass


def mesh_url(name: str) -> str:
    if name not in MESH_NAMES:
        raise InspectionIssue("Inspect only the two exact pinned official mesh files.")
    return MEDIA_PREFIX + name


def validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if url not in {mesh_url(name) for name in MESH_NAMES} or parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise InspectionIssue("Use the exact pinned HTTPS official mesh media URLs.")


class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        validate_url(url)
        return super().redirect_request(request, response, code, message, headers, url)


def verify_file(path: Path, name: str) -> dict:
    mesh_url(name)
    path = Path(path)
    expected_bytes, expected_sha = FILES[name]
    if path.is_symlink() or not path.is_file() or path.stat().st_size != expected_bytes:
        raise InspectionIssue("Use the exact regular size-pinned official mesh: " + name)
    digest = sha256()
    with path.open("rb") as stream:
        first = stream.read(8)
        digest.update(first)
        if first != SIGNATURE:
            raise InspectionIssue("Actual HDF5 mesh bytes are required; a Git LFS pointer is not a mesh.")
        while part := stream.read(1024 * 1024):
            digest.update(part)
    if digest.hexdigest() != expected_sha:
        raise InspectionIssue("Official mesh SHA-256 mismatch: " + name)
    return {"name": name, "bytes": expected_bytes, "sha256": expected_sha, "sourceUrl": mesh_url(name)}


def download_mesh(directory: Path, name: str) -> Path:
    directory = Path(directory)
    if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)() or not directory.is_dir():
        raise InspectionIssue("Use a regular independent runner mesh input directory.")
    target = directory / name
    url = mesh_url(name)
    if target.is_symlink():
        raise InspectionIssue("Mesh inputs cannot be symbolic links.")
    if target.exists():
        verify_file(target, name)
        return target
    temporary = directory / (name + ".part")
    if temporary.exists() or temporary.is_symlink():
        raise InspectionIssue("An earlier mesh transfer is present; inspect it before another attempt.")
    expected_bytes, expected_sha = FILES[name]
    opener = urllib.request.build_opener(RestrictedRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "PyAnsys-official-mesh-inventory", "Accept-Encoding": "identity"})
    try:
        digest, total, signature = sha256(), 0, b""
        with opener.open(request, timeout=30) as response, temporary.open("xb") as output:
            validate_url(response.geturl())
            declared = response.headers.get("Content-Length")
            if declared and (not declared.isdigit() or int(declared) != expected_bytes):
                raise InspectionIssue("The official mesh response differs from its pinned byte size.")
            while part := response.read(min(1024 * 1024, expected_bytes - total + 1)):
                total += len(part)
                if total > expected_bytes:
                    raise InspectionIssue("The official mesh response exceeded its pinned byte limit.")
                signature = (signature + part)[:8]
                digest.update(part)
                output.write(part)
        if total != expected_bytes or signature != SIGNATURE or digest.hexdigest() != expected_sha:
            raise InspectionIssue("The official mesh transfer failed size, HDF5 or SHA-256 verification.")
        temporary.rename(target)
        return target
    finally:
        if temporary.is_file() and not temporary.is_symlink():
            temporary.unlink()


def _json_value(value):
    """Bound small native metadata values and preserve their basic types."""
    if hasattr(value, "shape"):
        if value.size > MAX_SMALL_VALUES:
            raise InspectionIssue("A native metadata attribute exceeds the small-value limit.")
        return _json_value(value.tolist())
    if hasattr(value, "item"):
        return _json_value(value.item())
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_SMALL_VALUES:
            raise InspectionIssue("A native metadata value exceeds the small-value limit.")
        return [_json_value(item) for item in value]
    if isinstance(value, bytes):
        if len(value) > MAX_TEXT_BYTES:
            raise InspectionIssue("A native metadata string exceeds the text limit.")
        return value.decode("utf-8", errors="strict")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
            raise InspectionIssue("A native metadata string exceeds the text limit.")
        return value
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise InspectionIssue("Unsupported or nonfinite native metadata cannot be published as a value.")


def _attributes(obj) -> dict:
    if len(obj.attrs) > 32:
        raise InspectionIssue("A native HDF5 object exceeds the bounded attribute count.")
    result = {}
    for name in sorted(obj.attrs.keys()):
        value = obj.attrs[name]
        shape = list(getattr(value, "shape", ()))
        dtype = str(getattr(value, "dtype", type(value).__name__))
        if getattr(value, "size", 1) > MAX_SMALL_VALUES:
            result[name] = {"shape": shape, "dtype": dtype, "rawValueOmitted": "Exceeds small metadata value limit"}
        else:
            try:
                result[name] = {"shape": shape, "dtype": dtype, "rawValue": _json_value(value)}
            except (InspectionIssue, UnicodeError):
                result[name] = {"shape": shape, "dtype": dtype, "rawValueOmitted": "Unsupported or oversized native metadata value"}
    return result


def _scan_objects(handle, h5py) -> list[dict]:
    """Validate every link/storage descriptor before reading dataset payloads."""
    rows, visited = [], set()

    def visit(obj, path, depth=0):
        if len(rows) >= MAX_HDF5_OBJECTS or len(path) > 1024 or depth > MAX_HDF5_DEPTH:
            raise InspectionIssue("The HDF5 tree exceeds the bounded object/path inventory.")
        address = int(h5py.h5o.get_info(obj.id).addr)
        if address in visited:
            rows.append({"path": path, "type": "hard-link-alias", "targetObjectAddress": address})
            return
        visited.add(address)
        row = {"path": path, "type": "dataset" if isinstance(obj, h5py.Dataset) else "group", "attributes": _attributes(obj)}
        if isinstance(obj, h5py.Dataset):
            if obj.is_virtual or obj.external:
                raise InspectionIssue("External-storage and virtual HDF5 datasets are prohibited.")
            row.update(shape=None if obj.shape is None else list(obj.shape), dtype=str(obj.dtype), compression=obj.compression)
        rows.append(row)
        if isinstance(obj, h5py.Group):
            for name in sorted(obj.keys()):
                link = obj.get(name, getlink=True)
                if not isinstance(link, h5py.HardLink):
                    raise InspectionIssue("External and soft HDF5 links are prohibited.")
                visit(obj[name], path.rstrip("/") + "/" + name, depth + 1)

    visit(handle, "/")
    return rows


def _topology(handle, category: str) -> dict:
    path = "/meshes/1/" + category + "/zoneTopology"
    result = {"path": path, "rawDatasets": {}, "zones": [], "count": None, "countStatus": "Unconfirmed"}
    if path not in handle:
        result["countReason"] = "No stored zoneTopology group"
        return result
    group = handle[path]
    if not hasattr(group, "keys"):
        result["countReason"] = "Stored zoneTopology is not a group"
        return result
    for name in sorted(group.keys()):
        dataset = group[name]
        if not hasattr(dataset, "dtype"):
            continue
        description = {"shape": None if dataset.shape is None else list(dataset.shape), "dtype": str(dataset.dtype), "attributes": _attributes(dataset)}
        if dataset.size is not None and dataset.size <= MAX_SMALL_VALUES:
            try:
                description["rawValues"] = _json_value(dataset[()])
            except (InspectionIssue, UnicodeError):
                description["rawValuesOmitted"] = "Unsupported or oversized native topology values"
        else:
            description["rawValuesOmitted"] = "Exceeds small topology value limit"
        result["rawDatasets"][name] = description
    values = result["rawDatasets"]
    ids = values.get("id", {}).get("rawValues")
    minima = values.get("minId", {}).get("rawValues")
    maxima = values.get("maxId", {}).get("rawValues")
    if not all(isinstance(value, list) for value in (ids, minima, maxima)) or not ids or not len(ids) == len(minima) == len(maxima):
        result["countReason"] = "Explicit same-length id/minId/maxId vectors are required"
        return result
    if any(isinstance(value, bool) or not isinstance(value, int) for vector in (ids, minima, maxima) for value in vector) or len(set(ids)) != len(ids):
        result["countReason"] = "Integral unique zone IDs and integral ranges are required"
        return result
    names_raw = values.get("name", {}).get("rawValues")
    if isinstance(names_raw, list) and len(names_raw) == 1 and isinstance(names_raw[0], str):
        names = names_raw[0].split(";")
        if names and names[-1] == "":
            names = names[:-1]
            result["trimmedTerminalEmptyName"] = True
    elif isinstance(names_raw, str):
        names = names_raw.split(";")
    else:
        names = []
    names_matched = len(names) == len(ids)
    result["namesMatchZoneCount"] = names_matched
    for index, identifier in enumerate(ids):
        low, high = minima[index], maxima[index]
        result["zones"].append({"zoneId": identifier, "name": names[index] if names_matched else None,
                                "minimumId": low, "maximumId": high,
                                "inclusiveCount": high - low + 1 if 0 < low <= high else None})
    ranges = sorted(zip(minima, maxima))
    next_id = 1
    for low, high in ranges:
        if low != next_id or high < low:
            result["countReason"] = "Stored ranges do not confirm contiguous nonoverlapping 1-based IDs"
            return result
        next_id = high + 1
    result.update(count=next_id - 1, countStatus="ConfirmedFromStoredRanges",
                  countReason="Inclusive stored ranges cover contiguous IDs starting at one")
    return result


def _coordinate_bounds(handle, h5py) -> dict:
    import numpy as np
    path = "/meshes/1/nodes/coords"
    result = {"path": path, "unit": "native-unverified", "nativeUnitsVerified": False,
              "datasets": [], "minimum": None, "maximum": None, "coordinateRecordCount": 0}
    if path not in handle:
        result["reason"] = "No stored coordinate datasets"
        return result
    if not isinstance(handle[path], h5py.Group):
        result["reason"] = "Stored coordinates are not a dataset group"
        return result
    dimension, combined_min, combined_max = None, None, None
    for name in sorted(handle[path].keys()):
        dataset = handle[path][name]
        if not isinstance(dataset, h5py.Dataset):
            raise InspectionIssue("Coordinates must be explicit native numeric datasets.")
        shape = () if dataset.shape is None else tuple(dataset.shape)
        if len(shape) != 2 or shape[1] not in (2, 3) or shape[0] > MAX_COORDINATE_ROWS or dataset.dtype.kind not in "fiu":
            raise InspectionIssue("Unsupported coordinate shape/type exceeds this bounded metadata profile.")
        if dimension is not None and dimension != shape[1]:
            raise InspectionIssue("Native coordinate datasets have incompatible dimensions.")
        dimension = shape[1]
        low, high = None, None
        for start in range(0, shape[0], COORDINATE_CHUNK):
            chunk = dataset[start:min(start + COORDINATE_CHUNK, shape[0]), :]
            if not np.isfinite(chunk).all():
                raise InspectionIssue("Nonfinite stored coordinates cannot produce valid bounds.")
            local_min, local_max = chunk.min(axis=0), chunk.max(axis=0)
            low = local_min if low is None else np.minimum(low, local_min)
            high = local_max if high is None else np.maximum(high, local_max)
        if low is not None:
            combined_min = low if combined_min is None else np.minimum(combined_min, low)
            combined_max = high if combined_max is None else np.maximum(combined_max, high)
        result["coordinateRecordCount"] += shape[0]
        if result["coordinateRecordCount"] > MAX_COORDINATE_ROWS:
            raise InspectionIssue("The total coordinate inventory exceeds this bounded metadata profile.")
        result["datasets"].append({"path": path + "/" + name, "shape": list(shape), "dtype": str(dataset.dtype),
                                   "minimum": None if low is None else low.tolist(), "maximum": None if high is None else high.tolist()})
    result.update(dimension=dimension, minimum=None if combined_min is None else combined_min.tolist(),
                  maximum=None if combined_max is None else combined_max.tolist())
    return result


def inspect_hdf5(path: Path) -> dict:
    import h5py
    with h5py.File(path, "r") as handle:
        objects = _scan_objects(handle, h5py)
        topology = {category: _topology(handle, category) for category in ("cells", "faces", "nodes")}
        bounds = _coordinate_bounds(handle, h5py)
        result = {"metadataObjectCount": len(objects), "objects": objects, "topology": topology,
                  "counts": {"cellCount": topology["cells"]["count"], "faceCount": topology["faces"]["count"], "nodeCount": topology["nodes"]["count"]},
                  "coordinateBounds": bounds, "readOnlyInspection": True, "nativeCoordinateUnitsVerified": False,
                  "meshQualityCheckedBySolver": False, "nativeCaseGenerated": False, "fluentSessionConnected": False,
                  "licenseVerified": False, "actualCfdRunVerified": False, "simulationExecuted": False}
    bounded_json(result)
    return result


def inspect_verified_file(path: Path, name: str) -> dict:
    provenance = verify_file(path, name)
    # Pin/checksum checks complete before h5py opens any file.
    return {**provenance, **inspect_hdf5(path)}


def bounded_json(value: dict) -> bytes:
    raw = (json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_REPORT_BYTES:
        raise InspectionIssue("The metadata JSON exceeds its 128 KiB publication limit.")
    return raw


def output_path(value: Path) -> Path:
    runner = os.environ.get("RUNNER_TEMP")
    if not runner:
        raise InspectionIssue("Set the GitHub runner's existing RUNNER_TEMP for this independent metadata output.")
    root = Path(runner)
    value = Path(value).absolute()
    if not root.is_absolute() or not root.is_dir() or root.is_symlink() or getattr(root, "is_junction", lambda: False)() or value.suffix != ".json" or value.exists():
        raise InspectionIssue("Use a new regular JSON file inside the existing runner temporary directory.")
    root = root.resolve()
    if not value.resolve().is_relative_to(root):
        raise InspectionIssue("Metadata output must remain inside RUNNER_TEMP.")
    for parent in (value, *value.parents):
        if parent == root:
            break
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise InspectionIssue("Metadata output directories cannot contain links or junctions.")
    return value


def run(destination: Path) -> dict:
    destination = output_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    inputs = destination.parent / "frozen-rotor-mesh-inputs"
    if inputs.is_symlink() or getattr(inputs, "is_junction", lambda: False)():
        raise InspectionIssue("Use the regular independent runner mesh input directory.")
    inputs.mkdir(exist_ok=True)
    meshes = [inspect_verified_file(download_mesh(inputs, name), name) for name in MESH_NAMES]
    import h5py
    run_id, attempt, commit = os.environ.get("GITHUB_RUN_ID"), os.environ.get("GITHUB_RUN_ATTEMPT"), os.environ.get("GITHUB_SHA")
    report = {"schemaVersion": SCHEMA, "inspectionRole": "native-mesh-metadata-read-only",
              "exampleDataRepository": "https://github.com/ansys/example-data", "exampleDataCommit": PIN,
              "inspectedAt": datetime.now(timezone.utc).isoformat(), "h5pyVersion": h5py.__version__,
              "hdf5Version": h5py.version.hdf5_version, "meshes": meshes,
              "githubRepository": "simcalc/PyAnsys", "githubRunId": run_id if run_id and run_id.isdigit() else None,
              "githubRunAttempt": attempt if attempt and attempt.isdigit() else None,
              "githubSourceCommit": commit if commit and re.fullmatch(r"[0-9a-f]{40}", commit) else None,
              "readOnlyInspection": True, "nativeCoordinateUnitsVerified": False,
              "meshQualityCheckedBySolver": False, "nativeCaseGenerated": False,
              "fluentSessionConnected": False, "licenseVerified": False, "actualCfdRunVerified": False,
              "simulationExecuted": False, "mainApplicationWrites": False,
              "claimBoundary": "Native mesh inventory and stored coordinate bounds only; no mesh quality, generated case, Fluent session or CFD result."}
    raw = bounded_json(report)
    with destination.open("xb") as stream:
        stream.write(raw)
    return {"schemaVersion": SCHEMA, "reportFile": destination.name, "reportBytes": len(raw),
            "reportSha256": sha256(raw).hexdigest(), "meshFiles": len(meshes),
            "counts": [{"name": mesh["name"], **mesh["counts"]} for mesh in meshes], "simulationExecuted": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bounded read-only metadata from the two checksum-pinned official Fluent meshes")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.output), ensure_ascii=True, allow_nan=False))
    except (InspectionIssue, OSError, ImportError, UnicodeError) as error:
        print(json.dumps({"schemaVersion": SCHEMA, "status": "InputRequired", "reasonCode": "CFD_MESH_METADATA_INSPECTION_REQUIRED",
                          "action": str(error), "simulationExecuted": False, "actualCfdRunVerified": False}))
        raise SystemExit(2)
