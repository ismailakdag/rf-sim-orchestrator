"""Local-only bridge from an orchestrator job to a pinned CST night_case runner.

This adapter is never enabled automatically. The worker administrator must pin
its hash, the frozen source-manifest hash, Python executable and source path in
the local worker configuration. It does not connect to CST during import.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path, PurePosixPath


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


def portable_relative(name: str, label: str) -> PurePosixPath:
    if not isinstance(name, str) or "\\" in name or "\x00" in name or "//" in name or name != unicodedata.normalize("NFC", name):
        raise RuntimeError(f"unsafe {label} path: {name!r}")
    relative = PurePosixPath(name)
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if relative.is_absolute() or not relative.parts or any(part in ("", ".", "..") or ":" in part or part.endswith((".", " ")) or part.split(".", 1)[0].upper() in reserved for part in relative.parts):
        raise RuntimeError(f"unsafe {label} path: {name!r}")
    return relative


def safe_source(source_root: Path, name: str) -> Path:
    relative = portable_relative(name, "source manifest entry")
    if "__pycache__" in relative.parts or relative.suffix.lower() in {".pyc", ".pyo"}:
        raise RuntimeError(f"generated Python cache is not a valid frozen source: {name!r}")
    candidate = (source_root / Path(*relative.parts)).resolve(strict=True)
    if Path(os.path.commonpath((str(source_root), str(candidate)))) != source_root:
        raise RuntimeError(f"source manifest entry escapes source root: {name!r}")
    if not candidate.is_file() or candidate.is_symlink():
        raise RuntimeError(f"source manifest entry is not a regular file: {name!r}")
    return candidate


def safe_retained_file(root: Path, name: str) -> Path:
    relative = portable_relative(name, "verified artifact")
    candidate = (root / Path(*relative.parts)).resolve(strict=True)
    if Path(os.path.commonpath((str(root), str(candidate)))) != root or not candidate.is_file() or candidate.is_symlink():
        raise RuntimeError(f"verified artifact escapes archive or is not a regular file: {name!r}")
    return candidate


def validate_completed_archive(raw_archive: Path, raw_work: Path, job: dict, source_manifest: dict) -> dict:
    record = json.loads((raw_archive / "record.json").read_text(encoding="utf-8"))
    verified = json.loads((raw_archive / "verified.json").read_text(encoding="utf-8"))
    if record.get("run_id") != job["job_id"] or record.get("results_validated") is not True:
        raise RuntimeError("CST record does not identify a validated result for this job")
    if record.get("project_closed") is not True:
        raise RuntimeError("CST record does not prove that the project was closed")
    if verified.get("job_id") != job["job_id"] or verified.get("results_validated") is not True:
        raise RuntimeError("verified.json does not identify a validated result for this job")
    source_hashes = record.get("source_hashes", {})
    for name, observed in source_hashes.items():
        if name not in source_manifest or observed != source_manifest[name]:
            raise RuntimeError(f"record source hash is not pinned by source manifest: {name}")
    if not source_hashes:
        raise RuntimeError("record has no source hash evidence")
    files = verified.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("verified.json has no retained-file manifest")
    for name, info in files.items():
        path = safe_retained_file(raw_archive, name)
        expected_size = info.get("bytes")
        if path.stat().st_size != expected_size or sha256(path) != info.get("sha256"):
            raise RuntimeError(f"retained CST artifact hash or size mismatch: {name}")
    expected_header = ["frequency_GHz"] + [f"{name}_{part}" for name in ("S1,1", "S1,2", "S2,1", "S2,2") for part in ("real", "imag")]
    row_count = 0
    previous_frequency = None
    with gzip.open(raw_archive / "sparameters.csv.gz", "rt", newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        if next(reader, None) != expected_header:
            raise RuntimeError("complex S-parameter CSV header is not the pinned four-curve schema")
        for row in reader:
            if len(row) != len(expected_header):
                raise RuntimeError("complex S-parameter CSV row width is invalid")
            values = [float(value) for value in row]
            if not all(math.isfinite(value) for value in values):
                raise RuntimeError("complex S-parameter CSV contains non-finite data")
            if previous_frequency is not None and values[0] <= previous_frequency:
                raise RuntimeError("complex S-parameter frequency axis is not strictly increasing")
            previous_frequency = values[0]
            row_count += 1
    if row_count == 0 or verified.get("samples") != row_count:
        raise RuntimeError("complex S-parameter sample count is empty or differs from verified.json")
    if not (raw_work / "model.cst").is_file():
        raise RuntimeError("saved CST model is missing from raw work directory")
    return {"record": record, "verified": verified, "sparameter_rows": row_count}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_file", type=Path)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--case-template", help="manifest-pinned JSON template selected by an exact case_id")
    parser.add_argument("--case-catalog", help="manifest-pinned JSON catalog selected by an exact case_id")
    parser.add_argument("--compact", action="store_true", help="retain reproducible evidence and remove bulky local CST work after validation")
    args = parser.parse_args()
    # Resolve both sides before recording relative paths: Windows may supply a
    # short (8.3) TEMP path while resolve() expands the run directory's spelling.
    job_file = args.job_file.resolve(strict=True)
    run_dir = args.run_dir.resolve(strict=True)
    job_file.relative_to(run_dir)
    job = json.loads(job_file.read_text(encoding="utf-8"))
    source_root = args.source_root.resolve(strict=True)
    manifest_path = source_root / "source-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise RuntimeError("source-manifest.json must be a regular local file")
    manifest_document = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = manifest_document.get("sha256", manifest_document)
    if not isinstance(manifest, dict) or not manifest or not all(isinstance(name, str) and isinstance(digest, str) for name, digest in manifest.items()):
        raise RuntimeError("source-manifest.json has no valid SHA-256 mapping")
    if sha256(manifest_path) != job["source"]["sha256"]:
        raise RuntimeError("frozen source-manifest hash differs from submitted job")
    verified_sources = {}
    for name, expected in manifest.items():
        source_file = safe_source(source_root, name)
        if sha256(source_file) != expected:
            raise RuntimeError(f"frozen source file hash mismatch: {name}")
        verified_sources[name] = source_file

    raw_archive = run_dir / "source" / "cst-archive"
    raw_work = run_dir / "model" / "cst-work"
    if args.case_template and args.case_catalog:
        raise RuntimeError("select either --case-template or --case-catalog")
    if args.case_catalog:
        catalog_path = safe_source(source_root, args.case_catalog)
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        cases = catalog.get("cases")
        case_id = job.get("parameters", {}).get("case_id")
        if (
            not isinstance(cases, dict)
            or not isinstance(case_id, str)
            or job.get("parameters") != {"case_id": case_id}
            or case_id not in cases
        ):
            raise RuntimeError("remote job does not select an exact pinned catalog case")
        entry = cases[case_id]
        legacy = entry.get("legacy_job") if isinstance(entry, dict) else None
        if not isinstance(legacy, dict):
            raise RuntimeError("pinned catalog case has no legacy_job object")
        legacy = dict(legacy)
    elif args.case_template:
        template_path = safe_source(source_root, args.case_template)
        template = json.loads(template_path.read_text(encoding="utf-8"))
        case_id = template.get("case_id")
        if not isinstance(case_id, str) or job.get("parameters") != {"case_id": case_id}:
            raise RuntimeError("remote job does not select the exact pinned case template")
        legacy = template.get("legacy_job")
        if not isinstance(legacy, dict):
            raise RuntimeError("pinned case template has no legacy_job object")
        legacy = dict(legacy)
    else:
        legacy = dict(job["parameters"])
    forbidden = {"id", "deadline_utc", "archive", "work"} & set(legacy)
    if forbidden:
        raise RuntimeError(f"case parameters contain adapter-owned fields: {sorted(forbidden)}")
    legacy.update({"id": job["job_id"], "deadline_utc": job["deadline_utc"], "archive": str(raw_archive), "work": str(raw_work)})
    legacy_job = run_dir / "legacy-job.json"
    legacy_job.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")
    completed = subprocess.run([sys.executable, str(source_root / "night_case.py"), str(legacy_job)], stdin=subprocess.DEVNULL, shell=False)
    if completed.returncode:
        return completed.returncode

    validation = validate_completed_archive(raw_archive, raw_work, job, manifest)

    compact_mapping = {
        "parameters/job.json": job_file,
        "parameters/record.json": raw_archive / "record.json",
        "results/sparameters.csv.gz": raw_archive / "sparameters.csv.gz",
        "mesh/mesh-cells.txt": raw_archive / "mesh-cells.txt",
        "logs/lifecycle.jsonl": raw_archive / "lifecycle.jsonl",
        "quality/verified.json": raw_archive / "verified.json",
    }
    optional_compact = {
        "logs/summary.md": raw_archive / "summary.md",
        "source/model.vba": raw_archive / "model.vba",
    }
    missing = [str(source) for source in compact_mapping.values() if not source.is_file()]
    if missing:
        raise RuntimeError(f"validated CST run is missing canonical artifacts: {missing}")
    for name, source in {**compact_mapping, **{k: v for k, v in optional_compact.items() if v.is_file()}}.items():
        exclusive_copy(source, run_dir / name)
    exclusive_copy(manifest_path, run_dir / "source" / "source-manifest.json")
    for name, source_path in verified_sources.items():
        exclusive_copy(source_path, run_dir / "source" / "pinned-source" / name)
    large_artifacts = {
        "model": {"path": str((raw_work / "model.cst").relative_to(run_dir)), "size": (raw_work / "model.cst").stat().st_size, "sha256": sha256(raw_work / "model.cst")},
        "mesh_grid": ({"path": str((raw_archive / "mesh-grid.bin").relative_to(run_dir)), "size": (raw_archive / "mesh-grid.bin").stat().st_size, "sha256": sha256(raw_archive / "mesh-grid.bin")} if (raw_archive / "mesh-grid.bin").is_file() else None),
    }
    transport = {
        "raw_archive": str(raw_archive),
        "raw_work": str(raw_work),
        "raw_archive_preserved": not args.compact,
        "raw_work_preserved": not args.compact,
        "compact_copies": {name: str(source.relative_to(run_dir)) for name, source in compact_mapping.items()},
        "large_artifacts": large_artifacts,
        "verified_source_files": {name: {"path": str(path), "sha256": manifest[name]} for name, path in verified_sources.items()},
        "adapter_validation": {"results_validated": True, "project_closed": True, "sparameter_rows": validation["sparameter_rows"]},
    }
    (run_dir / "parameters" / "transport-mapping.json").write_text(json.dumps(transport, ensure_ascii=False, indent=2), encoding="utf-8")
    exclusive_copy(legacy_job, run_dir / "logs" / "legacy-job.json")
    if args.compact:
        required = [run_dir / "source" / "source-manifest.json", run_dir / "source" / "pinned-source" / "night_case.py", run_dir / "source" / "model.vba", run_dir / "parameters" / "record.json", run_dir / "results" / "sparameters.csv.gz", run_dir / "quality" / "verified.json"]
        if not all(path.is_file() for path in required):
            raise RuntimeError("compact cleanup refused because reproducibility evidence is incomplete")
        for target in (raw_archive, raw_work):
            resolved = target.resolve(strict=True)
            if resolved.parent.parent != run_dir and resolved.parent != run_dir / "model":
                raise RuntimeError(f"compact cleanup target is outside the owned run tree: {target}")
            if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
                raise RuntimeError(f"compact cleanup target is a link: {target}")
            shutil.rmtree(target)
        transport["compacted_utc"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z")
        (run_dir / "parameters" / "transport-mapping.json").write_text(json.dumps(transport, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
