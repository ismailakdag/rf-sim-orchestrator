"""Local-only bridge from an orchestrator job to the frozen CST 2026 night_case runner.

This adapter is never enabled automatically. The worker administrator must pin
its hash, the frozen source-manifest hash, Python executable and source path in
the local worker configuration. It does not connect to CST during import.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def exclusive_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)


def exclusive_move(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(target)
    shutil.move(str(source), str(target))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_file", type=Path)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    job = json.loads(args.job_file.read_text(encoding="utf-8"))
    run_dir = args.run_dir.resolve()
    source_root = args.source_root.resolve()
    manifest_path = source_root / "source-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if sha256(manifest_path) != job["source"]["sha256"]:
        raise RuntimeError("frozen source-manifest hash differs from submitted job")
    for name, expected in manifest.items():
        if sha256(source_root / name) != expected:
            raise RuntimeError(f"frozen source file hash mismatch: {name}")

    raw_archive = run_dir / "raw-archive"
    raw_work = run_dir / "raw-work"
    legacy = dict(job["parameters"])
    legacy.update({"id": job["job_id"], "deadline_utc": job["deadline_utc"], "archive": str(raw_archive), "work": str(raw_work)})
    legacy_job = run_dir / "legacy-job.json"
    legacy_job.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")
    completed = subprocess.run([sys.executable, str(source_root / "night_case.py"), str(legacy_job)], stdin=subprocess.DEVNULL, shell=False)
    if completed.returncode:
        return completed.returncode

    mapping = {
        "model/model.cst": raw_work / "model.cst",
        "source/model.vba": raw_archive / "model.vba",
        "parameters/job.json": args.job_file,
        "parameters/record.json": raw_archive / "record.json",
        "results/sparameters.csv.gz": raw_archive / "sparameters.csv.gz",
        "mesh/mesh-cells.txt": raw_archive / "mesh-cells.txt",
        "logs/lifecycle.jsonl": raw_archive / "lifecycle.jsonl",
        "quality/verified.json": raw_archive / "verified.json",
    }
    optional = {
        "mesh/mesh-grid.bin": raw_archive / "mesh-grid.bin",
        "logs/summary.md": raw_archive / "summary.md",
        "model/model.png": raw_archive / "model.png",
    }
    missing = [str(source) for source in mapping.values() if not source.is_file()]
    if missing:
        raise RuntimeError(f"validated CST run is missing canonical artifacts: {missing}")
    for name, source in {**mapping, **{k: v for k, v in optional.items() if v.is_file()}}.items():
        exclusive_move(source, run_dir / name)
    for name in manifest:
        archived_source = raw_archive / name
        if archived_source.is_file():
            exclusive_move(archived_source, run_dir / "source" / name)
        else:
            exclusive_copy(source_root / name, run_dir / "source" / name)
    # Retain every unrecognized worker artifact without duplicating large files.
    if raw_archive.exists() and any(raw_archive.iterdir()):
        exclusive_move(raw_archive, run_dir / "logs" / "raw-archive-extra")
    elif raw_archive.exists():
        raw_archive.rmdir()
    if raw_work.exists() and any(raw_work.iterdir()):
        exclusive_move(raw_work, run_dir / "logs" / "raw-work-extra")
    elif raw_work.exists():
        raw_work.rmdir()
    if legacy_job.exists():
        exclusive_move(legacy_job, run_dir / "logs" / "legacy-job.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
