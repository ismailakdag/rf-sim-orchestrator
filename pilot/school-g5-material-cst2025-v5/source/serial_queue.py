"""Finite serial runner for pre-written fixtured G5 CST jobs.

Usage::

    python serial_queue.py plan.json

The plan is an object with ``jobs`` (absolute job-JSON paths),
``deadline_utc`` (offset-aware ISO timestamp), ``max_runtime_seconds``, and
``min_free_gb``.  Optional ``prerequisite_archives`` contains absolute archive
paths that must become fully verified before any queued worker starts.  State,
``STOP``, and the controller lock are siblings of the plan.  Jobs are never
generated, retried, deleted, or overwritten here.

STOP is graceful and never kills the current worker.  If a worker exceeds its
own timeout, this campaign may terminate only the worker and CST processes that
were created after that job began.  Pre-existing CST processes are never
terminated.  If ownership or the post-cleanup idle state is uncertain, the
queue enters ``needs_attention`` and starts no further solver.
"""

from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any
import uuid
import psutil


SCRIPT = Path(__file__).resolve().parent
WORKER = SCRIPT / "night_case.py"
COMPLETED_STATES = {"completed", "completed_quality_flag"}
REQUIRED_PLAN_KEYS = {"jobs", "deadline_utc", "max_runtime_seconds", "min_free_gb"}
REQUIRED_HASHED_FILES = {"record.json", "model.vba", "sparameters.csv.gz"}
POLL_SECONDS = 5.0
WORKER_CLOSE_GRACE_SECONDS = 360.0


def _is_cst_process(name: str) -> bool:
    lowered = name.lower()
    return (
        "cst design environment" in lowered
        or "solver_hf" in lowered
        or lowered.startswith("cstd")
        or lowered.startswith("cstdc")
    )


def process_snapshot() -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for process in psutil.process_iter(["pid", "ppid", "name", "create_time"]):
        try:
            info = process.info
            if _is_cst_process(str(info.get("name") or "")):
                result[int(info["pid"])] = info
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def cleanup_owned_worker(
    child: subprocess.Popen[Any], baseline: dict[int, dict[str, Any]], started_epoch: float
) -> dict[str, Any]:
    """Stop only this job's Python tree and newly created CST processes."""
    selected: dict[int, psutil.Process] = {}
    try:
        root = psutil.Process(child.pid)
        selected[root.pid] = root
        for descendant in root.children(recursive=True):
            selected[descendant.pid] = descendant
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    for process in psutil.process_iter(["pid", "name", "create_time"]):
        try:
            info = process.info
            pid = int(info["pid"])
            created = float(info.get("create_time") or 0.0)
            if (
                pid not in baseline
                and created >= started_epoch - 2.0
                and _is_cst_process(str(info.get("name") or ""))
            ):
                selected[pid] = process
        except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError, ValueError):
            continue

    actions: list[dict[str, Any]] = []
    for process in sorted(selected.values(), key=lambda item: item.pid, reverse=True):
        try:
            actions.append({"pid": process.pid, "name": process.name(), "action": "terminate"})
            process.terminate()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            actions.append({"pid": process.pid, "action": "denied", "error": repr(exc)})
    _, alive_after_terminate = psutil.wait_procs(list(selected.values()), timeout=20)
    for process in alive_after_terminate:
        try:
            actions.append({"pid": process.pid, "name": process.name(), "action": "kill"})
            process.kill()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            actions.append({"pid": process.pid, "action": "kill_denied", "error": repr(exc)})
    _, still_alive = psutil.wait_procs(alive_after_terminate, timeout=10)
    return {
        "baseline_cst_pids": sorted(baseline),
        "selected_pids": sorted(selected),
        "actions": actions,
        "still_alive_pids": sorted(process.pid for process in still_alive),
        "ownership_rule": "worker descendants plus post-start CST processes absent from baseline",
    }


class QueueError(RuntimeError):
    """A condition that must stop the finite queue without starting another job."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text() -> str:
    return utc_now().isoformat()


def parse_time(raw: Any, field: str) -> datetime:
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise QueueError(f"{field} is not a valid ISO timestamp: {raw!r}") from exc
    if value.tzinfo is None:
        raise QueueError(f"{field} must contain a UTC offset")
    return value.astimezone(timezone.utc)


def read_json(path: Path) -> dict[str, Any]:
    try:
        # utf-8-sig accepts both plain UTF-8 and PowerShell-authored BOM files.
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QueueError(f"Unreadable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise QueueError(f"Expected a JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any], *, create_only: bool = False) -> None:
    data = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if create_only:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(6):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.1 * (2**attempt))
    finally:
        if temp.exists():
            temp.unlink()


def alive(pid: Any) -> bool:
    try:
        numeric_pid = int(pid)
    except (TypeError, ValueError):
        return False
    if numeric_pid <= 0:
        return False
    if os.name == "nt":
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, numeric_pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        kernel.CloseHandle(ctypes.c_void_p(handle))
        return bool(ok and code.value == 259)
    try:
        os.kill(numeric_pid, 0)
        return True
    except OSError:
        return False


class QueueLock:
    """OS-held advisory lock which also persists live-worker recovery data."""

    def __init__(self, path: Path):
        self.path = path
        self.handle: Any = None
        self.metadata: dict[str, Any] = {}

    def _lock(self) -> None:
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(self) -> None:
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)

    def acquire(self) -> "QueueLock":
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        self.handle = os.fdopen(fd, "r+b")
        try:
            self._lock()
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise QueueError("Controller lock is held; duplicate queue refused") from exc
        if self.path.stat().st_size == 0:
            self.handle.write(b"{}")
            self.handle.flush()
        self.handle.seek(0)
        try:
            self.metadata = json.loads(self.handle.read().decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.release()
            raise QueueError("Runner lock metadata is unreadable; manual audit required") from exc
        if self.metadata.get("worker_starting") or alive(self.metadata.get("worker_pid")):
            worker_pid = self.metadata.get("worker_pid")
            self.release()
            raise QueueError(f"Previous worker may still be active (pid={worker_pid})")
        self.update(controller_pid=os.getpid(), worker_pid=None, worker_starting=False,
                    acquired_utc=utc_text())
        return self

    def update(self, **values: Any) -> None:
        if self.handle is None:
            return
        self.metadata.update(values)
        self.metadata["updated_utc"] = utc_text()
        data = json.dumps(self.metadata, indent=2).encode("utf-8")
        self.handle.seek(0)
        self.handle.write(data)
        self.handle.truncate()
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            self.update(controller_pid=None)
            self._unlock()
        finally:
            self.handle.close()
            self.handle = None

    def __enter__(self) -> "QueueLock":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.release()


def existing_ancestor(path: Path) -> Path:
    current = path.resolve()
    while not current.exists():
        parent = current.parent
        if parent == current:
            raise QueueError(f"No existing ancestor for disk check: {path}")
        current = parent
    return current


def verify_archive(job: dict[str, Any], returncode: int) -> dict[str, Any]:
    """Require worker=0, closed project, validated results, and verified hashes."""
    archive = Path(job["archive"]).resolve()
    if returncode != 0:
        raise QueueError(f"Worker for {job['id']} exited with code {returncode}")
    record = read_json(archive / "record.json")
    if record.get("run_id") != job["id"]:
        raise QueueError(f"record.json run_id mismatch for {job['id']}")
    if record.get("status") not in COMPLETED_STATES:
        raise QueueError(f"Worker status is not complete for {job['id']}: {record.get('status')!r}")
    if record.get("results_validated") is not True:
        raise QueueError(f"results_validated is not true for {job['id']}")
    if record.get("project_closed") is not True:
        raise QueueError(f"project_closed is not true for {job['id']}")

    verified_path = archive / "verified.json"
    verified = read_json(verified_path)
    if verified.get("job_id") != job["id"] or verified.get("results_validated") is not True:
        raise QueueError(f"verified.json identity/status mismatch for {job['id']}")
    hashes = verified.get("files_sha256")
    if not isinstance(hashes, dict) or not REQUIRED_HASHED_FILES.issubset(hashes):
        raise QueueError(f"verified.json lacks required hashes for {job['id']}")
    details = verified.get("files")
    if details is not None and not isinstance(details, dict):
        raise QueueError(f"verified.json files table is invalid for {job['id']}")
    for relative, expected in hashes.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise QueueError(f"Invalid verified hash entry for {job['id']}")
        candidate = (archive / relative).resolve()
        try:
            candidate.relative_to(archive)
        except ValueError as exc:
            raise QueueError(f"Unsafe verified path {relative!r} for {job['id']}") from exc
        if not candidate.is_file() or candidate.stat().st_size <= 0:
            raise QueueError(f"Missing/empty verified file {relative!r} for {job['id']}")
        actual = sha256_file(candidate)
        if actual.lower() != expected.lower():
            raise QueueError(f"Hash mismatch for {job['id']}:{relative}")
        if isinstance(details, dict) and relative in details:
            info = details[relative]
            if not isinstance(info, dict) or info.get("sha256", "").lower() != actual.lower():
                raise QueueError(f"Detailed hash mismatch for {job['id']}:{relative}")
            if int(info.get("bytes", -1)) != candidate.stat().st_size:
                raise QueueError(f"Byte-count mismatch for {job['id']}:{relative}")
    return {
        "id": job["id"],
        "archive": str(archive),
        "status": record["status"],
        "verified_sha256": sha256_file(verified_path),
        "verified_utc": verified.get("verified_utc"),
    }


class SerialQueue:
    def __init__(self, plan_path: Path, lock: QueueLock):
        self.plan_path = plan_path.resolve()
        self.directory = self.plan_path.parent
        self.state_path = self.directory / "state.json"
        self.stop_path = self.directory / "STOP"
        self.lock = lock
        self.plan = read_json(self.plan_path)
        missing = REQUIRED_PLAN_KEYS - set(self.plan)
        if missing:
            raise QueueError(f"Plan lacks required keys: {sorted(missing)}")
        if not WORKER.is_file():
            raise QueueError(f"Worker is missing: {WORKER}")

        jobs_raw = self.plan["jobs"]
        if not isinstance(jobs_raw, list) or not jobs_raw:
            raise QueueError("plan.jobs must be a non-empty finite list")
        self.job_paths: list[Path] = []
        for raw in jobs_raw:
            path = Path(str(raw))
            if not path.is_absolute():
                raise QueueError(f"Every job path must be absolute: {raw!r}")
            path = path.resolve()
            if not path.is_file():
                raise QueueError(f"Job JSON does not exist: {path}")
            self.job_paths.append(path)
        if len(set(self.job_paths)) != len(self.job_paths):
            raise QueueError("plan.jobs contains duplicate paths")

        self.deadline = parse_time(self.plan["deadline_utc"], "plan.deadline_utc")
        try:
            self.max_runtime = float(self.plan["max_runtime_seconds"])
            self.min_free_bytes = float(self.plan["min_free_gb"]) * 1024**3
        except (TypeError, ValueError) as exc:
            raise QueueError("max_runtime_seconds and min_free_gb must be numeric") from exc
        if self.max_runtime <= 0 or self.min_free_bytes < 0:
            raise QueueError("max_runtime_seconds must be positive and min_free_gb non-negative")

        self.jobs = [self._validate_job(path) for path in self.job_paths]
        self._validate_unique_targets()
        prerequisites_raw = self.plan.get("prerequisite_archives", [])
        if not isinstance(prerequisites_raw, list):
            raise QueueError("plan.prerequisite_archives must be a list when present")
        self.prerequisite_archives: list[Path] = []
        for raw in prerequisites_raw:
            path = Path(str(raw))
            if not path.is_absolute():
                raise QueueError(f"Every prerequisite archive must be absolute: {raw!r}")
            self.prerequisite_archives.append(path.resolve())
        if len(set(self.prerequisite_archives)) != len(self.prerequisite_archives):
            raise QueueError("plan.prerequisite_archives contains duplicate paths")
        plan_hash = sha256_file(self.plan_path)
        job_hashes = {str(path): sha256_file(path) for path in self.job_paths}
        if self.state_path.exists():
            self.state = read_json(self.state_path)
            if self.state.get("plan_sha256") != plan_hash:
                raise QueueError("Plan changed after queue state was created")
            if self.state.get("job_sha256") != job_hashes:
                raise QueueError("A job JSON changed after queue state was created")
        else:
            self.state = {
                "schema_version": 1,
                "status": "initialized",
                "stage": "preflight",
                "plan": str(self.plan_path),
                "plan_sha256": plan_hash,
                "job_sha256": job_hashes,
                "jobs": [str(path) for path in self.job_paths],
                "prerequisite_archives": [str(path) for path in self.prerequisite_archives],
                "prerequisites_verified": [],
                "completed": [],
                "started_utc": utc_text(),
                "worker_starting": False,
                "worker_pid": None,
                "active_job": None,
            }
            atomic_json(self.state_path, self.state, create_only=True)

        self.started = parse_time(self.state["started_utc"], "state.started_utc")
        self.runtime_deadline = self.started + timedelta(seconds=self.max_runtime)
        if self.state.get("worker_starting") or self.state.get("active_job"):
            pid = self.state.get("worker_pid")
            state = "live" if alive(pid) else "not live"
            raise QueueError(
                f"Persisted unfinished worker is {state} (pid={pid}); no job will be retried"
            )
        self._verify_completed_state()

    def _validate_job(self, path: Path) -> dict[str, Any]:
        job = read_json(path)
        for key in ("id", "archive", "work", "deadline_utc", "timeout_seconds"):
            if key not in job:
                raise QueueError(f"Job {path} lacks {key!r}")
        if not isinstance(job["id"], str) or not job["id"].strip():
            raise QueueError(f"Job id is invalid: {path}")
        for key in ("archive", "work"):
            target = Path(str(job[key]))
            if not target.is_absolute():
                raise QueueError(f"job.{key} must be absolute for {job['id']}")
            job[key] = str(target.resolve())
        parse_time(job["deadline_utc"], f"job[{job['id']}].deadline_utc")
        try:
            timeout = float(job["timeout_seconds"])
        except (TypeError, ValueError) as exc:
            raise QueueError(f"timeout_seconds is invalid for {job['id']}") from exc
        if timeout <= 0:
            raise QueueError(f"timeout_seconds must be positive for {job['id']}")
        return job

    def _validate_unique_targets(self) -> None:
        ids = [job["id"] for job in self.jobs]
        archives = [job["archive"] for job in self.jobs]
        works = [job["work"] for job in self.jobs]
        if len(ids) != len(set(ids)):
            raise QueueError("Job ids are not unique")
        if len(archives) != len(set(archives)) or len(works) != len(set(works)):
            raise QueueError("Job archive/work targets are not unique")

    def save(self) -> None:
        self.state["updated_utc"] = utc_text()
        self.state["controller_pid"] = os.getpid()
        atomic_json(self.state_path, self.state)
        self.lock.update(
            worker_pid=self.state.get("worker_pid"),
            worker_starting=self.state.get("worker_starting", False),
            active_job=self.state.get("active_job"),
            stage=self.state.get("stage"),
        )

    def _verify_completed_state(self) -> None:
        completed = self.state.get("completed")
        if not isinstance(completed, list):
            raise QueueError("state.completed is invalid")
        jobs_by_id = {job["id"]: job for job in self.jobs}
        seen: set[str] = set()
        for item in completed:
            if not isinstance(item, dict) or item.get("id") not in jobs_by_id:
                raise QueueError("state.completed contains an unknown job")
            ident = item["id"]
            if ident in seen:
                raise QueueError(f"state.completed repeats {ident}")
            seen.add(ident)
            verified = verify_archive(jobs_by_id[ident], 0)
            if item.get("verified_sha256") != verified["verified_sha256"]:
                raise QueueError(f"verified.json changed after completion for {ident}")

    def _attempt_is_fresh(self, job: dict[str, Any]) -> None:
        archive = Path(job["archive"])
        work = Path(job["work"])
        if archive.exists() and any(archive.iterdir()):
            raise QueueError(f"Archive target is occupied; refusing to rerun {job['id']}")
        if work.exists() and any(work.iterdir()):
            raise QueueError(f"Work target is occupied; refusing to rerun {job['id']}")

    def _prerequisite_status(self, archive: Path) -> tuple[str, dict[str, Any] | None]:
        """Return pending/verified; raise when a prerequisite is terminally bad."""
        record_path = archive / "record.json"
        if not record_path.is_file():
            return "pending", None
        try:
            record = read_json(record_path)
        except QueueError:
            # The worker uses atomic replacement, but tolerate a transient read
            # until it advertises a terminal state.
            return "pending", None
        status = record.get("status")
        if status in {"technical_failed", "validation_failed"}:
            raise QueueError(
                f"Prerequisite {archive} ended in {status}: {record.get('failure_reason')!r}"
            )
        if status not in COMPLETED_STATES:
            return "pending", None
        verified_path = archive / "verified.json"
        if not verified_path.is_file():
            return "pending", None
        ident = record.get("run_id")
        if not isinstance(ident, str) or not ident:
            raise QueueError(f"Prerequisite record has no run_id: {archive}")
        summary = verify_archive({"id": ident, "archive": str(archive)}, 0)
        return "verified", summary

    def wait_for_prerequisites(self) -> bool:
        """Wait without touching CST; return false when STOP/time pauses the queue."""
        if not self.prerequisite_archives:
            return True
        verified_by_path = {
            item["archive"]: item
            for item in self.state.get("prerequisites_verified", [])
            if isinstance(item, dict) and "archive" in item
        }
        while True:
            if self.stop_path.exists():
                self.state.update(
                    status="paused_stop",
                    stage="WAITING_PREREQUISITE",
                    stop_reason=f"STOP present: {self.stop_path}",
                )
                self.save()
                return False
            current = utc_now()
            if current >= self.deadline:
                self.state.update(
                    status="paused_deadline",
                    stage="WAITING_PREREQUISITE",
                    stop_reason="Plan deadline reached while waiting for prerequisite",
                )
                self.save()
                return False
            if current >= self.runtime_deadline:
                self.state.update(
                    status="paused_runtime",
                    stage="WAITING_PREREQUISITE",
                    stop_reason="Maximum queue runtime reached while waiting for prerequisite",
                )
                self.save()
                return False

            pending: list[str] = []
            changed = False
            for archive in self.prerequisite_archives:
                key = str(archive)
                if key in verified_by_path:
                    # Recheck on every controller start and before using the
                    # cached prerequisite so later corruption cannot be hidden.
                    status, summary = self._prerequisite_status(archive)
                    if status != "verified" or summary is None:
                        raise QueueError(f"Previously verified prerequisite regressed: {archive}")
                    if summary["verified_sha256"] != verified_by_path[key]["verified_sha256"]:
                        raise QueueError(f"Prerequisite verified.json changed: {archive}")
                    continue
                status, summary = self._prerequisite_status(archive)
                if status == "verified" and summary is not None:
                    verified_by_path[key] = summary
                    changed = True
                else:
                    pending.append(key)
            if changed:
                self.state["prerequisites_verified"] = list(verified_by_path.values())
            if not pending:
                self.state.update(
                    status="running",
                    stage="prerequisites_verified",
                    waiting_prerequisites=[],
                )
                self.save()
                return True
            new_waiting = sorted(pending)
            if (
                self.state.get("status") != "WAITING_PREREQUISITE"
                or self.state.get("waiting_prerequisites") != new_waiting
                or changed
            ):
                self.state.update(
                    status="WAITING_PREREQUISITE",
                    stage="WAITING_PREREQUISITE",
                    waiting_prerequisites=new_waiting,
                    stop_reason=None,
                )
                self.save()
            time.sleep(POLL_SECONDS)

    def _prestart_reason(self, job: dict[str, Any]) -> tuple[str, str] | None:
        if self.stop_path.exists():
            return "paused_stop", f"STOP present: {self.stop_path}"
        current = utc_now()
        if current >= self.deadline:
            return "paused_deadline", "Plan deadline reached before next job"
        if current >= self.runtime_deadline:
            return "paused_runtime", "Maximum queue runtime reached before next job"
        free = shutil.disk_usage(existing_ancestor(Path(job["archive"]))).free
        self.state["last_free_bytes"] = free
        if free < self.min_free_bytes:
            return "paused_disk", (
                f"Free space {free / 1024**3:.2f} GiB is below "
                f"{self.min_free_bytes / 1024**3:.2f} GiB"
            )
        return None

    def _worker_stage(self, job: dict[str, Any]) -> str:
        record_path = Path(job["archive"]) / "record.json"
        if not record_path.is_file():
            return "worker_startup"
        try:
            record = read_json(record_path)
        except QueueError:
            return "worker_record_temporarily_unreadable"
        return str(record.get("last_successful_stage") or record.get("status") or "worker_running")

    def run_job(self, path: Path, job: dict[str, Any]) -> dict[str, Any]:
        self._attempt_is_fresh(job)
        archive = Path(job["archive"])
        archive.mkdir(parents=True, exist_ok=True)
        log_path = archive / "worker.stdout.log"
        self.state.update({
            "status": "running",
            "stage": "launching_worker",
            "active_job": job["id"],
            "active_job_path": str(path),
            "worker_starting": True,
            "worker_pid": None,
        })
        self.save()

        child: subprocess.Popen[Any] | None = None
        baseline = process_snapshot()
        started_epoch = time.time()
        cleanup_record: dict[str, Any] | None = None
        with log_path.open("x", encoding="utf-8") as log:
            try:
                child = subprocess.Popen(
                    [sys.executable, "-u", str(WORKER), str(path)],
                    cwd=str(SCRIPT),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            except Exception:
                self.state.update(worker_starting=False, status="needs_attention",
                                  stage="worker_launch_failed")
                self.save()
                raise
            self.state.update(worker_starting=False, worker_pid=child.pid,
                              stage="worker_running",
                              process_ownership={
                                  "worker_pid": child.pid,
                                  "started_epoch": started_epoch,
                                  "baseline_cst_pids": sorted(baseline),
                              })
            self.save()
            started = time.monotonic()
            last_stage = None
            stop_seen = False
            timeout_limit = float(job["timeout_seconds"]) + WORKER_CLOSE_GRACE_SECONDS
            while child.poll() is None:
                stage = self._worker_stage(job)
                if stage != last_stage:
                    self.state["worker_stage"] = stage
                    self.state["stage"] = "worker_running"
                    self.save()
                    last_stage = stage
                if self.stop_path.exists() and not stop_seen:
                    stop_seen = True
                    self.state["stop_after_current"] = True
                    self.state["stop_reason"] = f"STOP observed while {job['id']} was active"
                    self.save()
                free = shutil.disk_usage(existing_ancestor(archive)).free
                if free < self.min_free_bytes:
                    self.state["stop_after_current"] = True
                    self.state["stop_reason"] = (
                        f"Disk threshold crossed during {job['id']}; current worker retained"
                    )
                    self.save()
                elapsed = time.monotonic() - started
                if elapsed > timeout_limit:
                    cleanup_record = cleanup_owned_worker(child, baseline, started_epoch)
                    self.state["last_owned_cleanup"] = cleanup_record
                    self.state["worker_pid"] = None if child.poll() is not None else child.pid
                    self.save()
                    if cleanup_record["still_alive_pids"] or child.poll() is None:
                        raise QueueError(
                            f"Worker {job['id']} exceeded timeout and owned cleanup was incomplete"
                        )
                    raise QueueError(
                        f"Worker {job['id']} exceeded timeout; owned job processes were stopped"
                    )
                time.sleep(POLL_SECONDS)
            returncode = int(child.returncode)

        if returncode != 0:
            cleanup_record = cleanup_owned_worker(child, baseline, started_epoch)
            self.state["last_owned_cleanup"] = cleanup_record

        self.state.update(worker_pid=None, worker_starting=False, stage="verifying_archive")
        self.save()
        result = verify_archive(job, returncode)
        from pair_gate import check
        check(job)
        return result

    def run(self) -> None:
        completed_ids = {item["id"] for item in self.state["completed"]}
        self.state.setdefault('skipped', [])
        skipped_ids={item['id'] for item in self.state['skipped']}
        self.state.update(status="running", stage="preflight", stop_reason=None)
        self.save()
        try:
            if not self.wait_for_prerequisites():
                return
            for path, job in zip(self.job_paths, self.jobs):
                if job["id"] in completed_ids or job['id'] in skipped_ids:
                    continue
                reason = self._prestart_reason(job)
                if reason:
                    self.state["status"], self.state["stop_reason"] = reason
                    self.state["stage"] = "before_next_job"
                    self.save()
                    return
                try:
                    dependency=job.get('paired_reference_id')
                    if dependency and dependency not in completed_ids:
                        raise QueueError('Reference failed or was skipped; contrast deferred to retry list')
                    result = self.run_job(path, job)
                except Exception as exc:
                    from failure_notes import capture, probe
                    if alive(self.state.get('worker_pid')):
                        safety=dict(safe_to_continue=False,reason='worker_still_alive')
                    else:
                        safety=probe()
                    note=capture(job,exc,safety)
                    self.state['skipped'].append(note)
                    atomic_json(self.directory/'retry-list.json',dict(jobs=self.state['skipped']))
                    with (self.directory/'failures.jsonl').open('a',encoding='utf-8') as handle:
                        handle.write(json.dumps(note,ensure_ascii=False)+'\n')
                    if not safety.get('safe_to_continue'):
                        self.save()
                        raise QueueError('Failed job recorded; next solve blocked because idle state is unconfirmed') from exc
                    skipped_ids.add(job['id'])
                    self.state.update(active_job=None,active_job_path=None,worker_pid=None,
                        worker_starting=False,stage='failed_job_skipped',last_skipped_job=job['id'])
                    self.save()
                    continue
                self.state["completed"].append(result)
                completed_ids.add(job["id"])
                self.state.update(
                    active_job=None,
                    active_job_path=None,
                    worker_pid=None,
                    worker_starting=False,
                    worker_stage="results_validated_project_closed_hashes_verified",
                    stage="job_completed",
                    last_completed_job=job["id"],
                )
                self.save()
                if self.state.get("stop_after_current"):
                    self.state["status"] = "paused_stop"
                    self.state["stage"] = "after_completed_job"
                    self.save()
                    return
            self.state.update(status="finished_with_skips" if self.state['skipped'] else "finished", stage="queue_exhausted",
                              active_job=None, worker_pid=None, worker_starting=False)
            self.save()
        except Exception as exc:
            # Never terminate the worker here.  A live PID remains in state/lock
            # so restart is refused until a human audits the attempt.
            self.state["status"] = "needs_attention"
            self.state["stage"] = "halted_fail_closed"
            self.state["stop_reason"] = repr(exc)
            self.save()
            raise


class WindowsAwake:
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    def __enter__(self) -> "WindowsAwake":
        if os.name == "nt":
            result = ctypes.windll.kernel32.SetThreadExecutionState(
                self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED
            )
            if result == 0:
                raise OSError("Windows refused the system-awake request")
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if os.name == "nt":
            ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    plan_path = args.plan.resolve()
    if not plan_path.is_file():
        raise SystemExit(f"Plan not found: {plan_path}")
    with QueueLock(plan_path.parent / "runner.lock") as lock:
        with WindowsAwake():
            SerialQueue(plan_path, lock).run()


if __name__ == "__main__":
    main()
