from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one {label}; found {count}")
    return text.replace(old, new)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a pinned CST-source derivative that selects the local API path and records the actual target release.")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source-version", required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    destination = args.destination.resolve()
    if destination.exists():
        raise RuntimeError("destination already exists; source packages are immutable")
    manifest_path = source / "source-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    hashes = manifest.get("sha256", manifest)
    for name, expected in hashes.items():
        path = source / name
        if not path.is_file() or digest(path) != expected:
            raise RuntimeError(f"source manifest verification failed: {name}")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    night_path = destination / "night_case.py"
    night = night_path.read_text(encoding="utf-8")
    if "import os\n" not in night:
        night = replace_once(night, "import json\n", "import json\nimport os\n", "night_case import insertion point")
    old_version = manifest.get("source_version")
    night = replace_once(night, 'CST_LIBS = r"E:\\CST Studio Suite 2026\\AMD64\\python_cst_libraries"', 'CST_LIBS = os.environ.get("CST_PYTHON_LIBRARIES")\nif not CST_LIBS:\n    raise RuntimeError("CST_PYTHON_LIBRARIES must select the locally approved CST Python API")', "CST library assignment")
    night = replace_once(night, '"version": "2026"', '"version": os.environ.get("CST_EXPECTED_VERSION", "unreported")', "simulator version field")
    if old_version:
        night = replace_once(night, f'record["source_version"] = "{old_version}"', f'record["source_version"] = "{args.source_version}"', "source version field")
    night = night.replace("CST 2026 connect API", "CST local connect API").replace("CST 2026 shipped", "Selected local CST release shipped")
    night_path.write_text(night, encoding="utf-8", newline="\n")

    safety_path = destination / "safety_probe.py"
    if safety_path.is_file():
        safety = safety_path.read_text(encoding="utf-8")
        safety = replace_once(safety, "import json,sys,psutil", "import json,os,sys,psutil", "safety_probe import line")
        safety = replace_once(safety, "sys.path.insert(0,r'E:\\CST Studio Suite 2026\\AMD64\\python_cst_libraries')", "libraries=os.environ.get('CST_PYTHON_LIBRARIES')\n    if not libraries:raise RuntimeError('CST_PYTHON_LIBRARIES is not configured')\n    sys.path.insert(0,libraries)", "safety_probe CST path")
        safety_path.write_text(safety, encoding="utf-8", newline="\n")

    files = {path.relative_to(destination).as_posix(): digest(path) for path in sorted(destination.rglob("*")) if path.is_file() and path.name != "source-manifest.json"}
    new_manifest = {"source_version": args.source_version, "derived_from": {"source_version": old_version, "manifest_sha256": digest(manifest_path)}, "sha256": files}
    (destination / "source-manifest.json").write_text(json.dumps(new_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"source_version": args.source_version, "destination": str(destination), "source_manifest_sha256": digest(destination / "source-manifest.json"), "files": len(files)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
