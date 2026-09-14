from __future__ import annotations

import csv
import json
import os
import socket
import subprocess
import sys
import threading
import time
import platform
import math
import stat
import shutil
from datetime import datetime, timezone
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from .common import ValidationError, canonical_json, parse_utc, safe_member_name, sha256_bytes, sha256_file, utc_now, write_json_atomic
from .capabilities import detect_cst_installations


class ExecutionUncertain(RuntimeError):
    """An external process or transport may have completed despite lost control."""


def _known_cst_dialogs() -> list[str]:
    """Return a small allowlist of visible CST dialogs without interacting with them."""
    if os.name != "nt":
        return []
    try:
        import ctypes
        import psutil

        user32 = ctypes.windll.user32
        titles: set[str] = set()
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def visit(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            process_id = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            try:
                process_name = psutil.Process(process_id.value).name().lower()
            except (psutil.Error, OSError):
                return True
            if "cst" not in process_name and "solver" not in process_name:
                return True
            lowered = title.casefold()
            if "abort" in lowered:
                titles.add("abort")
            elif "license" in lowered or "licence" in lowered:
                titles.add("license")
            return True

        user32.EnumWindows(callback_type(visit), 0)
        return sorted(titles)
    except Exception:
        return []


def _runner_stage(run_dir: Path) -> str:
    dialogs = _known_cst_dialogs()
    if dialogs:
        return ("interaction_required:" + ",".join(dialogs))[:128]
    heartbeat = run_dir / "model" / "cst-work" / "heartbeat.json"
    try:
        progress = json.loads(heartbeat.read_text(encoding="utf-8"))
        if progress.get("state") == "solving":
            elapsed = max(0, int(float(progress.get("elapsed_seconds", 0))))
            return f"solver_running:{elapsed}s"
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    record_path = run_dir / "source" / "cst-archive" / "record.json"
    try:
        status = str(json.loads(record_path.read_text(encoding="utf-8")).get("status", "")).strip()
        if status:
            return ("cst_" + status)[:128]
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return "runner_active"


class ApiClient:
    def __init__(self, base_url: str, token: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def json(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = canonical_json(payload) if payload is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.load(response)

    def upload(self, path: str, file: Path, worker_id: str, lease_token: str) -> dict:
        # http.client streams file chunks; the archive is never loaded into RAM.
        from urllib.parse import urlsplit
        import http.client
        url = urlsplit(self.base_url + path)
        conn_type = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        conn = conn_type(url.hostname, url.port, timeout=max(self.timeout, 300))
        digest = sha256_file(file)
        conn.putrequest("POST", url.path)
        conn.putheader("Authorization", f"Bearer {self.token}")
        conn.putheader("Content-Type", "application/zip")
        conn.putheader("Content-Length", str(file.stat().st_size))
        conn.putheader("X-Worker-ID", worker_id)
        conn.putheader("X-Lease-Token", lease_token)
        conn.putheader("X-Content-SHA256", digest)
        conn.endheaders()
        with file.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                conn.send(chunk)
        response = conn.getresponse()
        body = response.read()
        conn.close()
        if response.status >= 300:
            raise RuntimeError(f"upload rejected ({response.status}): {body.decode('utf-8', 'replace')}")
        return json.loads(body)


def _artifact_manifest(root: Path, job: dict, state: str) -> dict:
    artifacts = []
    resolved_root = root.resolve(strict=True)
    aliases: set[str] = set()
    for path in sorted(root.rglob("*")):
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        is_junction = getattr(path, "is_junction", lambda: False)()
        if path.is_symlink() or is_junction or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValidationError(f"result tree contains a forbidden link or reparse point: {path.relative_to(root)}")
        try:
            contained = os.path.commonpath((str(resolved_root), str(path.resolve(strict=True)))) == str(resolved_root)
        except (OSError, ValueError) as exc:
            raise ValidationError(f"result artifact cannot be safely resolved: {path}") from exc
        if not contained:
            raise ValidationError(f"result artifact resolves outside run directory: {path.relative_to(root)}")
        if path.is_file() and path.name != "manifest.json":
            name = safe_member_name(path.relative_to(root).as_posix())
            alias = name.casefold()
            if alias in aliases:
                raise ValidationError(f"result tree contains a case-insensitive path collision: {name}")
            aliases.add(alias)
            artifacts.append({"path": name, "size": path.stat().st_size, "sha256": sha256_file(path)})
    return {
        "schema_version": 1,
        "job_id": job["job_id"],
        "study_id": job["study_id"],
        "source": job["source"],
        "deadline_utc": job["deadline_utc"],
        "job_document_sha256": sha256_bytes(canonical_json(job)),
        "state": state,
        "created_utc": utc_now(),
        "artifacts": artifacts,
    }


def package_result(run_dir: Path, job: dict, state: str = "artifacts_verified") -> Path:
    write_json_atomic(run_dir / "manifest.json", _artifact_manifest(run_dir, job, state))
    target = run_dir.parent / f"{job['job_id']}.zip"
    temp = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(run_dir).as_posix())
    os.replace(temp, target)
    return target


def run_mock(job: dict, run_dir: Path, config: dict) -> None:
    _validate_parameter_schema(job["parameters"], {
        "mock_delay_seconds": {"type": "number", "min": 0, "max": float(config.get("max_mock_delay_seconds", 5))}
    })
    delay = float(job["parameters"].get("mock_delay_seconds", 0.05))
    if not 0 <= delay <= float(config.get("max_mock_delay_seconds", 5)):
        raise ValidationError("mock_delay_seconds is outside the local runner limit")
    run_dir.mkdir(parents=True, exist_ok=False)
    for folder in ("model", "source", "parameters", "results", "mesh", "logs", "quality"):
        (run_dir / folder).mkdir()
    write_json_atomic(run_dir / "parameters" / "parameters.json", job["parameters"])
    write_json_atomic(run_dir / "source" / "source.json", job["source"])
    (run_dir / "source" / "model.vba").write_text("' Mock runner: no CST commands were executed.\n", encoding="utf-8")
    (run_dir / "model" / "model.cst.mock").write_bytes(b"MOCK-CST-MODEL\n")
    (run_dir / "mesh" / "mesh.json").write_text(json.dumps({"type": "mock", "cells": 128, "converged": False}, indent=2), encoding="utf-8")
    time.sleep(delay)
    with (run_dir / "results" / "complex-s.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["run_id", "case_id", "geometry_id", "specimen_id", "scenario_id", "frequency_Hz", "port_i", "port_j", "s_real", "s_imag", "evidence_domain", "label", "label_source"])
        for frequency, values in (
            (3e9, {(1, 1): (.1, -.2), (2, 1): (.8, -.1), (1, 2): (.8, -.1), (2, 2): (.12, -.19)}),
            (3.5e9, {(1, 1): (.08, -.18), (2, 1): (.82, -.08), (1, 2): (.82, -.08), (2, 2): (.1, -.17)}),
        ):
            for (port_i, port_j), (real, imag) in values.items():
                writer.writerow([job["job_id"], "mock-case", "mock-geometry", "mock-specimen", "mock-scenario", f"{frequency:.0f}", port_i, port_j, real, imag, "synthetic", "mock", "runner"])
    write_json_atomic(run_dir / "quality" / "quality.json", {
        "result_integrity": {"outcome": "pass", "method": "mock deterministic fixture"},
        "passivity": {"outcome": "pass", "tolerance": 1e-9},
        "energy_decay": {"outcome": "not_applicable"},
        "mesh_convergence": {"outcome": "not_established"},
        "port_convergence": {"outcome": "not_established"},
    })
    (run_dir / "logs" / "runner.log").write_text(f"{utc_now()} mock run completed; no CST launched\n", encoding="utf-8")


def _validate_parameter_schema(parameters: dict, schema: dict) -> None:
    unknown = set(parameters) - set(schema)
    if unknown:
        raise ValidationError(f"parameters not allowed by local runner: {sorted(unknown)}")
    for name, spec in schema.items():
        if name not in parameters:
            if spec.get("required", False):
                raise ValidationError(f"missing required parameter: {name}")
            continue
        value = parameters[name]
        kind = spec["type"]
        if kind not in {"number", "integer", "string", "boolean", "object"}:
            raise ValidationError(f"unsupported local schema type for {name}: {kind}")
        if kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ValidationError(f"{name} must be numeric")
        if kind == "number" and not math.isfinite(value):
            raise ValidationError(f"{name} must be finite")
        if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValidationError(f"{name} must be an integer")
        if kind == "string" and not isinstance(value, str):
            raise ValidationError(f"{name} must be a string")
        if kind == "boolean" and not isinstance(value, bool):
            raise ValidationError(f"{name} must be a boolean")
        if kind == "object":
            if not isinstance(value, dict):
                raise ValidationError(f"{name} must be an object")
            _validate_parameter_schema(value, spec.get("properties", {}))
        if ("min" in spec and value < spec["min"]) or ("max" in spec and value > spec["max"]):
            raise ValidationError(f"{name} is outside local bounds")
        if "enum" in spec and value not in spec["enum"]:
            raise ValidationError(f"{name} is not in the local allowlist")


def run_fixed_python(job: dict, run_dir: Path, config: dict) -> None:
    script = Path(config["script"]).resolve()
    python = Path(config.get("python", sys.executable)).resolve()
    expected_script = config["script_sha256"]
    expected_source = config["source_sha256"]
    if sha256_file(script) != expected_script:
        raise ValidationError("configured adapter script SHA-256 differs from local allowlist")
    if job["source"]["sha256"] != expected_source:
        raise ValidationError("job source SHA-256 differs from local allowlist")
    _validate_parameter_schema(job["parameters"], config.get("parameter_schema", {}))
    run_dir.mkdir(parents=True, exist_ok=False)
    job_path = run_dir / "job.json"
    write_json_atomic(job_path, job)
    # Command and placeholders come only from local configuration. The remote job cannot add flags or a shell command.
    replacements = {"{job_file}": str(job_path), "{run_dir}": str(run_dir)}
    args = [replacements.get(item, item) for item in config.get("arguments", ["{job_file}", "{run_dir}"])]
    remaining = (parse_utc(job["deadline_utc"]) - datetime.now(timezone.utc)).total_seconds() if job["deadline_utc"] is not None else None
    configured_timeout = float(config.get("timeout_seconds", 28_800))
    if not math.isfinite(configured_timeout) or configured_timeout < 0:
        raise ValidationError("timeout_seconds must be finite and nonnegative; zero disables the local wall-clock limit")
    limits = [value for value in (configured_timeout or None, remaining) if value is not None]
    timeout = min(limits) if limits else None
    if timeout is not None and timeout <= 0:
        raise ExecutionUncertain("job deadline passed before external process start")
    runtime_path = run_dir / "logs" / "external-process.json"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    started_utc = utc_now()
    child_env = os.environ.copy()
    cst_libraries = config.get("cst_python_libraries")
    if cst_libraries:
        libraries = Path(cst_libraries).resolve(strict=True)
        if not libraries.is_dir():
            raise ValidationError("configured CST Python library path is not a directory")
        child_env["PYTHONPATH"] = str(libraries) + (os.pathsep + child_env["PYTHONPATH"] if child_env.get("PYTHONPATH") else "")
        child_env["CST_PYTHON_LIBRARIES"] = str(libraries)
    if expected_cst_major := config.get("expected_cst_major"):
        child_env["CST_EXPECTED_VERSION"] = str(int(expected_cst_major))
    with (run_dir / "adapter.stdout.log").open("wb") as output:
        guard = None
        if config.get("handle_cst_abort_dialog", False):
            from .cst_abort_guard import AbortGuard
            guard = AbortGuard(run_dir)
        process = subprocess.Popen([str(python), str(script), *args], stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, shell=False, env=child_env)
        expires = time.monotonic() + timeout if timeout is not None else None
        write_json_atomic(runtime_path, {"pid": process.pid, "started_utc": started_utc, "timeout_seconds": timeout, "state": "running"})
        try:
            while True:
                if guard is not None:
                    try:
                        guard.tick()
                    except Exception as exc:
                        write_json_atomic(run_dir / 'logs/abort-guard-error.json', {"utc": utc_now(), "error": repr(exc)})
                left = expires - time.monotonic() if expires is not None else None
                if left is not None and left <= 0:
                    raise subprocess.TimeoutExpired(process.args, timeout)
                try:
                    return_code = process.wait(timeout=min(2, left) if guard is not None and left is not None else (2 if guard is not None else left))
                    break
                except subprocess.TimeoutExpired:
                    if expires is not None and time.monotonic() >= expires:
                        raise
        except subprocess.TimeoutExpired as exc:
            write_json_atomic(runtime_path, {"pid": process.pid, "started_utc": started_utc, "timeout_seconds": timeout, "state": "timeout_process_state_unknown", "automatic_termination": False, "last_observed_stage": _runner_stage(run_dir)})
            raise ExecutionUncertain(f"fixed runner exceeded {timeout:.1f} s; PID {process.pid} and descendants were not terminated automatically") from exc
    write_json_atomic(runtime_path, {"pid": process.pid, "finished_utc": utc_now(), "timeout_seconds": timeout, "state": "exited", "return_code": return_code})
    if return_code:
        raise ExecutionUncertain(f"configured runner exited with code {return_code}; descendant solver state is unknown")


RUNNER_TYPES = {"mock": run_mock, "fixed_python": run_fixed_python}


class Worker:
    def __init__(self, client: ApiClient, worker_id: str, root: Path, runners: dict[str, dict], heartbeat_seconds: int = 30, min_free_bytes: int = 0, cleanup_after_upload: bool = False, cst_roots: list[str] | None = None):
        self.client = client
        self.worker_id = worker_id
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.runners = runners
        self.heartbeat_seconds = heartbeat_seconds
        self.min_free_bytes = min_free_bytes
        self.cleanup_after_upload = cleanup_after_upload
        self.cst_roots = cst_roots or []

    def disk_usage(self):
        return shutil.disk_usage(self.root)

    def presence(self, state: str, current_job_id: str | None = None, note: str | None = None) -> dict:
        usage = self.disk_usage()
        capabilities = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cst_installations": detect_cst_installations(self.cst_roots),
            "gpu_required": False,
            "cleanup_after_upload": self.cleanup_after_upload,
        }
        payload = {
            "worker_id": self.worker_id,
            "state": state,
            "hostname": socket.gethostname(),
            "current_job_id": current_job_id,
            "free_bytes": usage.free,
            "total_bytes": usage.total,
            "runners": sorted(self.runners),
            "capabilities": capabilities,
            "note": note,
        }
        return self.client.json("POST", "/api/v1/workers/presence", payload)

    def _safe_cleanup(self, run_dir: Path, bundle: Path, job: dict, result: dict) -> Path:
        local_hash = sha256_file(bundle)
        if result.get("state") != "completed" or result.get("job_id") != job["job_id"] or result.get("result_sha256") != local_hash:
            raise ExecutionUncertain("host acceptance did not exactly match the uploaded result; local files retained")
        runs_root = (self.root / "runs").resolve()
        resolved_run = run_dir.resolve(strict=True)
        if resolved_run.parent != runs_root or resolved_run.name != job["job_id"] or run_dir.is_symlink():
            raise ValidationError("refusing cleanup outside the owned per-job run directory")
        manifest = json.loads((resolved_run / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("job_id") != job["job_id"]:
            raise ValidationError("refusing cleanup because local manifest ownership differs")
        receipts = self.root / "receipts"
        receipt = receipts / f"{job['job_id']}.json"
        write_json_atomic(receipt, {
            "job_id": job["job_id"], "host_state": "completed", "result_sha256": local_hash,
            "result_bytes": bundle.stat().st_size, "uploaded_utc": utc_now(), "local_payload_deleted": True,
        })
        shutil.rmtree(resolved_run)
        bundle.unlink()
        return receipt

    def once(self) -> dict | None:
        usage = self.disk_usage()
        if usage.free < self.min_free_bytes:
            self.presence("low_disk", note=f"free disk below configured gate: {usage.free} < {self.min_free_bytes}")
            return None
        self.presence("idle")
        response = self.client.json("POST", "/api/v1/lease", {"worker_id": self.worker_id, "runners": sorted(self.runners)})
        lease = response["lease"]
        if lease is None:
            return None
        job = lease["job"]
        token = lease["lease_token"]
        runner_config = self.runners[job["runner"]]
        self.presence("running", current_job_id=job["job_id"])
        stop = threading.Event()
        heartbeat_error: list[Exception] = []
        run_dir = self.root / "runs" / job["job_id"]

        def heartbeats():
            while not stop.wait(self.heartbeat_seconds):
                try:
                    self.client.json("POST", f"/api/v1/jobs/{job['job_id']}/heartbeat", {"worker_id": self.worker_id, "lease_token": token, "stage": _runner_stage(run_dir)})
                except Exception as exc:
                    heartbeat_error.append(exc)
                    return

        thread = threading.Thread(target=heartbeats, daemon=True)
        thread.start()
        try:
            if run_dir.exists():
                raise ValidationError("local immutable run directory already exists; operator review required")
            runner_type = runner_config.get("type")
            if runner_type not in RUNNER_TYPES:
                raise ValidationError(f"unknown locally configured runner type: {runner_type}")
            RUNNER_TYPES[runner_type](job, run_dir, runner_config)
            if heartbeat_error:
                raise ExecutionUncertain("heartbeat failed while runner was active; result retained locally without upload")
            write_json_atomic(run_dir / "logs" / "orchestrator-runtime.json", {
                "worker_id": self.worker_id,
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "python": sys.version,
                "runner_name": job["runner"],
                "acceleration_observed": "runner-specific; inspect retained record",
                "packaged_utc": utc_now(),
            })
            bundle = package_result(run_dir, job)
            try:
                result = self.client.upload(f"/api/v1/results/{job['job_id']}", bundle, self.worker_id, token)
            except Exception as exc:
                raise ExecutionUncertain(f"result upload outcome is unknown; verified bundle retained at {bundle}") from exc
            write_json_atomic(run_dir / "host-acceptance.json", result)
            if self.cleanup_after_upload:
                receipt = self._safe_cleanup(run_dir, bundle, job, result)
                result = {**result, "local_cleanup": "completed", "receipt": str(receipt)}
            try:
                self.presence("idle")
            except Exception:
                pass
            return result
        except ExecutionUncertain as exc:
            try:
                self.presence("needs_attention", current_job_id=job["job_id"], note=str(exc))
            except Exception:
                pass
            try:
                self.client.json("POST", f"/api/v1/jobs/{job['job_id']}/attention", {"worker_id": self.worker_id, "lease_token": token, "reason": str(exc)})
            except Exception:
                pass
            raise
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            try:
                self.presence("needs_attention", current_job_id=job["job_id"], note=reason)
            except Exception:
                pass
            if job["runner"] in self.runners and self.runners[job["runner"]].get("type") == "fixed_python" and (run_dir / "logs" / "external-process.json").is_file():
                uncertain = f"external runner started and later handling failed; state requires inspection: {reason}"
                try:
                    self.client.json("POST", f"/api/v1/jobs/{job['job_id']}/attention", {"worker_id": self.worker_id, "lease_token": token, "reason": uncertain})
                except Exception:
                    pass
                raise ExecutionUncertain(uncertain) from exc
            # If the host is unreachable, preserve local evidence and do not claim a safe retry.
            try:
                self.client.json("POST", f"/api/v1/jobs/{job['job_id']}/fail", {"worker_id": self.worker_id, "lease_token": token, "reason": reason})
            except Exception:
                pass
            raise
        finally:
            stop.set()
            thread.join(timeout=2)

    def loop(self, poll_seconds: int = 10, stop_event: threading.Event | None = None) -> None:
        stop_event = stop_event or threading.Event()
        while not stop_event.is_set():
            result = self.once()
            if result is None:
                stop_event.wait(poll_seconds)
        try:
            self.presence("stopping")
        except Exception:
            pass


def default_worker_id() -> str:
    return socket.gethostname()
