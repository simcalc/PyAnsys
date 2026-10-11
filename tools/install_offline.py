"""Install the verified release wheelhouse into a NEW Linux Python 3.12 venv."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def isolated_environment(source):
    env = {key: value for key, value in source.items()
           if not key.upper().startswith(("PIP_", "PYTHON"))}
    env["PIP_CONFIG_FILE"] = os.devnull
    return env


def verify_wheels(root, manifest):
    expected = {row["filename"]: row for row in manifest["wheels"]}
    actual = {p.name for p in (root / "wheelhouse").iterdir()}
    if set(expected) != actual:
        raise ValueError("Wheelhouse inventory differs from the signed-off manifest")
    for name, row in expected.items():
        if Path(name).name != name or not name.endswith(".whl"):
            raise ValueError("Unsafe wheel filename")
        file = root / "wheelhouse" / name
        if file.is_symlink() or not file.is_file():
            raise ValueError("Wheel must be a regular file")
        if file.stat().st_size != row["bytes"] or hashlib.sha256(file.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError(f"Wheel integrity mismatch: {name}")


def main():
    root = Path(__file__).resolve().parent
    if platform.python_implementation() != "CPython" or platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64") or sys.version_info[:2] != (3, 12):
        raise SystemExit("This offline bundle requires Linux x64 and CPython 3.12. Use the online instructions on Windows/macOS.")
    manifest = json.loads((root / "wheel-manifest.json").read_text())
    verify_wheels(root, manifest)
    destination = root / ".venv"
    if destination.exists():
        raise SystemExit("Destination .venv already exists; choose a fresh extracted folder.")
    env = isolated_environment(os.environ)
    subprocess.run([sys.executable, "-I", "-m", "venv", str(destination)], env=env, check=True)
    python = destination / "bin" / "python"
    subprocess.run([str(python), "-I", "-m", "pip", "install", "--no-index", "--require-hashes",
                    "--find-links", str(root / "wheelhouse"), "-r", str(root / "requirements-resolved.txt")], env=env, check=True)
    subprocess.run([str(python), "-I", "-m", "pip", "check"], env=env, check=True)
    subprocess.run([str(python), "-I", "-c",
                    "import ansys.fluent.core; import importlib.metadata as m; assert m.version('ansys-fluent-core') == '0.42.2'"], env=env, check=True)
    print("PyFluent SDK installed. Ansys Fluent solver and license are separate; no simulation was run.")


if __name__ == "__main__":
    main()
