from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_RESULT_PREFIXES = ("model/", "source/", "parameters/", "results/", "mesh/", "logs/", "quality/")


class ValidationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValidationError("timestamp must be a string")
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValidationError("deadline_utc must include a timezone")
    return result.astimezone(timezone.utc)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def secure_compare(actual: str | None, expected: str) -> bool:
    return bool(actual) and hmac.compare_digest(actual, expected)


def validate_job(document: dict[str, Any], allowed_runners: set[str] | None = None) -> dict[str, Any]:
    try:
        canonical_json(document)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"job must contain only finite JSON values: {exc}") from exc
    allowed = {"schema_version", "job_id", "study_id", "runner", "source", "parameters", "deadline_utc", "priority", "metadata"}
    unknown = set(document) - allowed
    if unknown:
        raise ValidationError(f"unknown job fields: {sorted(unknown)}")
    required = {"schema_version", "job_id", "study_id", "runner", "source", "parameters", "deadline_utc"}
    missing = required - set(document)
    if missing:
        raise ValidationError(f"missing job fields: {sorted(missing)}")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValidationError("schema_version must be 1")
    for key in ("job_id", "study_id", "runner"):
        if not isinstance(document[key], str) or not JOB_ID_RE.fullmatch(document[key]):
            raise ValidationError(f"invalid {key}")
    if allowed_runners is not None and document["runner"] not in allowed_runners:
        raise ValidationError("runner is not allowed by this host")
    if not isinstance(document["parameters"], dict):
        raise ValidationError("parameters must be an object")
    if len(canonical_json(document["parameters"])) > 256 * 1024:
        raise ValidationError("parameters exceed 256 KiB")
    source = document["source"]
    if not isinstance(source, dict) or set(source) != {"version", "sha256"}:
        raise ValidationError("source must contain exactly version and sha256")
    if not isinstance(source["version"], str) or not source["version"]:
        raise ValidationError("source.version must be non-empty")
    if not isinstance(source["sha256"], str) or not SHA256_RE.fullmatch(source["sha256"]):
        raise ValidationError("source.sha256 must be a lowercase SHA-256")
    deadline = parse_utc(document["deadline_utc"])
    if deadline <= datetime.now(timezone.utc):
        raise ValidationError("deadline_utc is in the past")
    priority = document.get("priority", 0)
    if type(priority) is not int or not -100 <= priority <= 100:
        raise ValidationError("priority must be an integer from -100 to 100")
    if not isinstance(document.get("metadata", {}), dict):
        raise ValidationError("metadata must be an object")
    return {**document, "priority": priority, "metadata": document.get("metadata", {})}


def safe_member_name(name: str) -> str:
    if not isinstance(name, str) or "\\" in name or "\x00" in name:
        raise ValidationError(f"unsafe archive path: {name!r}")
    if name != unicodedata.normalize("NFC", name) or "//" in name:
        raise ValidationError(f"non-canonical archive path: {name!r}")
    raw_parts = name.rstrip("/").split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if any(not part or part in (".", "..") or ":" in part or part.endswith((".", " ")) or part.split(".", 1)[0].upper() in reserved for part in raw_parts):
        raise ValidationError(f"unsafe archive path segment: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ValidationError(f"unsafe archive path: {name!r}")
    if path.parts[0].endswith(":"):
        raise ValidationError(f"unsafe archive path: {name!r}")
    return path.as_posix()


def validate_result_zip(path: Path, expected_job_id: str, max_uncompressed: int) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if not infos or len(infos) > 20_000:
            raise ValidationError("result archive is empty or has too many entries")
        total = 0
        names: set[str] = set()
        aliases: set[str] = set()
        for info in infos:
            name = safe_member_name(info.filename)
            alias = unicodedata.normalize("NFC", name).casefold()
            if name in names or alias in aliases:
                raise ValidationError(f"duplicate archive member: {name}")
            names.add(name)
            aliases.add(alias)
            total += info.file_size
            if total > max_uncompressed:
                raise ValidationError("uncompressed result exceeds configured limit")
            if info.is_dir():
                continue
            # Unix symlink bits are rejected even though extraction is not used here.
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValidationError(f"symlink is forbidden: {name}")
        if "manifest.json" not in names:
            raise ValidationError("result archive has no manifest.json")
        for prefix in REQUIRED_RESULT_PREFIXES:
            if not any(name.startswith(prefix) and not name.endswith("/") for name in names):
                raise ValidationError(f"result archive has no file under {prefix}")
        manifest_info = archive.getinfo("manifest.json")
        manifest_limit = 4 * 1024 * 1024
        if manifest_info.file_size > manifest_limit:
            raise ValidationError("result manifest exceeds 4 MiB limit")
        with archive.open(manifest_info) as manifest_stream:
            manifest_bytes = manifest_stream.read(manifest_limit + 1)
        if len(manifest_bytes) > manifest_limit:
            raise ValidationError("result manifest exceeds 4 MiB limit")
        manifest = json.loads(manifest_bytes)
        if manifest.get("job_id") != expected_job_id:
            raise ValidationError("result manifest job_id mismatch")
        if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 1:
            raise ValidationError("result manifest schema_version must be 1")
        listed = manifest.get("artifacts")
        if not isinstance(listed, list):
            raise ValidationError("result manifest artifacts must be a list")
        listed_paths = set()
        for item in listed:
            if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
                raise ValidationError("each artifact must contain path, size and sha256")
            name = safe_member_name(item["path"])
            if name == "manifest.json" or name not in names or name in listed_paths:
                raise ValidationError(f"invalid artifact listing: {name}")
            if type(item["size"]) is not int or item["size"] < 0 or not SHA256_RE.fullmatch(str(item["sha256"])):
                raise ValidationError(f"invalid artifact metadata: {name}")
            digest = hashlib.sha256()
            observed_size = 0
            with archive.open(name) as member:
                while chunk := member.read(1024 * 1024):
                    digest.update(chunk)
                    observed_size += len(chunk)
            if observed_size != item["size"] or digest.hexdigest() != item["sha256"]:
                raise ValidationError(f"artifact hash or size mismatch: {name}")
            listed_paths.add(name)
        actual_files = {name for name in names if name != "manifest.json" and not name.endswith("/")}
        if listed_paths != actual_files:
            raise ValidationError("manifest artifact list does not exactly cover archive files")
        return {"manifest": manifest, "file_count": len(actual_files), "uncompressed_bytes": total}


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
