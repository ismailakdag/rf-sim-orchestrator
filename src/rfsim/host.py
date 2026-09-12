from __future__ import annotations

import json
import os
import secrets
import sqlite3
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .common import JOB_ID_RE, ValidationError, canonical_json, parse_utc, secure_compare, sha256_file, utc_now, validate_job, validate_result_zip


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, submitted_utc TEXT NOT NULL, updated_utc TEXT NOT NULL,
  state TEXT NOT NULL, document TEXT NOT NULL, document_sha256 TEXT NOT NULL,
  worker_id TEXT, lease_token TEXT, lease_expires_utc TEXT, attempt INTEGER NOT NULL DEFAULT 0,
  result_sha256 TEXT, result_bytes INTEGER, note TEXT
);
CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(state);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, utc TEXT NOT NULL,
  event TEXT NOT NULL, details TEXT NOT NULL
);
"""


class Store:
    def __init__(self, root: Path, lease_seconds: int, allowed_runners: set[str], max_result_bytes: int, max_uncompressed_bytes: int):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "results").mkdir(exist_ok=True)
        self.db_path = self.root / "host.sqlite3"
        self.lease_seconds = lease_seconds
        self.allowed_runners = allowed_runners
        self.max_result_bytes = max_result_bytes
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _event(db: sqlite3.Connection, job_id: str, event: str, details: dict) -> None:
        db.execute("INSERT INTO events(job_id,utc,event,details) VALUES(?,?,?,?)", (job_id, utc_now(), event, json.dumps(details, sort_keys=True)))

    def sweep_expired(self, db: sqlite3.Connection) -> None:
        now = utc_now()
        rows = db.execute("SELECT job_id,worker_id,attempt FROM jobs WHERE state='leased' AND lease_expires_utc < ?", (now,)).fetchall()
        for row in rows:
            db.execute("UPDATE jobs SET state='needs_attention',updated_utc=?,note=? WHERE job_id=?", (now, "lease expired; remote solver state is unknown; manual requeue required", row["job_id"]))
            self._event(db, row["job_id"], "lease_expired", {"worker_id": row["worker_id"], "attempt": row["attempt"], "automatic_retry": False})

    def submit(self, raw: dict) -> dict:
        document = validate_job(raw, self.allowed_runners)
        encoded = canonical_json(document)
        digest = __import__("hashlib").sha256(encoded).hexdigest()
        now = utc_now()
        with self.lock, self.connect() as db:
            existing = db.execute("SELECT document_sha256,state FROM jobs WHERE job_id=?", (document["job_id"],)).fetchone()
            if existing:
                if existing["document_sha256"] == digest:
                    return {"job_id": document["job_id"], "state": existing["state"], "idempotent": True, "document_sha256": digest}
                raise ValidationError("job_id already exists with different immutable content")
            db.execute("INSERT INTO jobs(job_id,submitted_utc,updated_utc,state,document,document_sha256) VALUES(?,?,?,?,?,?)", (document["job_id"], now, now, "queued", encoded.decode("utf-8"), digest))
            self._event(db, document["job_id"], "submitted", {"document_sha256": digest})
        return {"job_id": document["job_id"], "state": "queued", "idempotent": False, "document_sha256": digest}

    def status(self, job_id: str | None = None) -> dict:
        if job_id is not None and not JOB_ID_RE.fullmatch(job_id):
            raise ValidationError("invalid job_id")
        with self.lock, self.connect() as db:
            self.sweep_expired(db)
            if job_id:
                row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if not row:
                    raise KeyError(job_id)
                events = [dict(x) for x in db.execute("SELECT utc,event,details FROM events WHERE job_id=? ORDER BY id", (job_id,))]
                result = dict(row)
                result["document"] = json.loads(result["document"])
                result["events"] = [{**x, "details": json.loads(x["details"])} for x in events]
                result.pop("lease_token", None)
                return result
            rows = db.execute("SELECT job_id,state,submitted_utc,updated_utc,worker_id,lease_expires_utc,attempt,result_sha256,result_bytes,note FROM jobs ORDER BY submitted_utc").fetchall()
            return {"jobs": [dict(row) for row in rows]}

    def lease(self, worker_id: str, runners: list[str]) -> dict | None:
        if not worker_id or len(worker_id) > 128 or not all(isinstance(x, str) for x in runners):
            raise ValidationError("invalid worker identity or runner list")
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.sweep_expired(db)
            active = db.execute("SELECT job_id FROM jobs WHERE state IN ('leased','needs_attention') AND worker_id=?", (worker_id,)).fetchone()
            if active:
                return None
            rows = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY json_extract(document,'$.priority') DESC, submitted_utc").fetchall()
            now_dt = datetime.now(timezone.utc)
            for row in rows:
                document = json.loads(row["document"])
                if document["runner"] not in runners:
                    continue
                if parse_utc(document["deadline_utc"]) <= now_dt:
                    db.execute("UPDATE jobs SET state='expired',updated_utc=?,note=? WHERE job_id=?", (utc_now(), "deadline passed before lease", row["job_id"]))
                    self._event(db, row["job_id"], "deadline_expired", {})
                    continue
                token = secrets.token_urlsafe(32)
                expires = min(now_dt + timedelta(seconds=self.lease_seconds), parse_utc(document["deadline_utc"])).isoformat().replace("+00:00", "Z")
                db.execute("UPDATE jobs SET state='leased',updated_utc=?,worker_id=?,lease_token=?,lease_expires_utc=?,attempt=attempt+1,note=NULL WHERE job_id=? AND state='queued'", (utc_now(), worker_id, token, expires, row["job_id"]))
                self._event(db, row["job_id"], "leased", {"worker_id": worker_id, "lease_expires_utc": expires, "attempt": row["attempt"] + 1})
                return {"job": document, "lease_token": token, "lease_expires_utc": expires, "attempt": row["attempt"] + 1}
            return None

    def heartbeat(self, job_id: str, worker_id: str, token: str, stage: str) -> dict:
        if not JOB_ID_RE.fullmatch(job_id):
            raise ValidationError("invalid job_id")
        if len(stage) > 128:
            raise ValidationError("stage is too long")
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.sweep_expired(db)
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            if row["state"] != "leased" or row["worker_id"] != worker_id or not secure_compare(token, row["lease_token"]):
                raise PermissionError("lease is no longer active or does not belong to this worker")
            document = json.loads(row["document"])
            deadline = parse_utc(document["deadline_utc"])
            expires_dt = min(datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds), deadline)
            expires = expires_dt.isoformat().replace("+00:00", "Z")
            db.execute("UPDATE jobs SET updated_utc=?,lease_expires_utc=?,note=? WHERE job_id=?", (utc_now(), expires, f"worker stage: {stage}", job_id))
            self._event(db, job_id, "heartbeat", {"worker_id": worker_id, "stage": stage, "lease_expires_utc": expires})
            return {"job_id": job_id, "lease_expires_utc": expires}

    def fail(self, job_id: str, worker_id: str, token: str, reason: str) -> dict:
        if not JOB_ID_RE.fullmatch(job_id):
            raise ValidationError("invalid job_id")
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.sweep_expired(db)
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            if row["state"] != "leased" or row["worker_id"] != worker_id or not secure_compare(token, row["lease_token"]):
                raise PermissionError("invalid active lease")
            now = utc_now()
            db.execute("UPDATE jobs SET state='failed',updated_utc=?,lease_token=NULL,lease_expires_utc=NULL,note=? WHERE job_id=?", (now, reason[:2000], job_id))
            self._event(db, job_id, "worker_failed", {"worker_id": worker_id, "reason": reason[:2000]})
            return {"job_id": job_id, "state": "failed"}

    def attention(self, job_id: str, worker_id: str, token: str, reason: str) -> dict:
        if not JOB_ID_RE.fullmatch(job_id):
            raise ValidationError("invalid job_id")
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.sweep_expired(db)
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            if row["state"] == "needs_attention" and row["worker_id"] == worker_id:
                return {"job_id": job_id, "state": "needs_attention"}
            if row["state"] != "leased" or row["worker_id"] != worker_id or not secure_compare(token, row["lease_token"]):
                raise PermissionError("invalid active lease")
            now = utc_now()
            note = f"worker reported uncertain execution state: {reason}"[:2000]
            db.execute("UPDATE jobs SET state='needs_attention',updated_utc=?,lease_token=NULL,lease_expires_utc=NULL,note=? WHERE job_id=?", (now, note, job_id))
            self._event(db, job_id, "worker_needs_attention", {"worker_id": worker_id, "reason": reason[:2000], "automatic_retry": False})
            return {"job_id": job_id, "state": "needs_attention"}

    def accept_result(self, job_id: str, worker_id: str, token: str, source: Path, claimed_sha256: str) -> dict:
        if not JOB_ID_RE.fullmatch(job_id):
            raise ValidationError("invalid job_id")
        size = source.stat().st_size
        digest = sha256_file(source)
        if digest != claimed_sha256:
            raise ValidationError("uploaded result SHA-256 does not match header")
        verified = validate_result_zip(source, job_id, self.max_uncompressed_bytes)
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.sweep_expired(db)
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            if row["state"] != "leased" or row["worker_id"] != worker_id or not secure_compare(token, row["lease_token"]):
                raise PermissionError("invalid active lease; result retained only on worker")
            submitted_job = json.loads(row["document"])
            manifest = verified["manifest"]
            if manifest.get("source") != submitted_job["source"] or manifest.get("deadline_utc") != submitted_job["deadline_utc"]:
                raise ValidationError("result manifest source or deadline differs from immutable job")
            if not secure_compare(manifest.get("job_document_sha256"), row["document_sha256"]):
                raise ValidationError("result manifest is not bound to the complete immutable job document")
            if manifest.get("state") != "artifacts_verified":
                raise ValidationError("result manifest does not claim artifacts_verified")
            target = self.root / "results" / f"{job_id}.zip"
            if target.exists():
                raise ValidationError("result already exists")
            os.replace(source, target)
            now = utc_now()
            db.execute("UPDATE jobs SET state='completed',updated_utc=?,lease_token=NULL,lease_expires_utc=NULL,result_sha256=?,result_bytes=?,note=? WHERE job_id=?", (now, digest, size, f"verified {verified['file_count']} artifacts", job_id))
            self._event(db, job_id, "result_verified", {"worker_id": worker_id, "sha256": digest, "bytes": size, "artifact_count": verified["file_count"], "uncompressed_bytes": verified["uncompressed_bytes"]})
        return {"job_id": job_id, "state": "completed", "result_sha256": digest, "result_bytes": size, "artifact_count": verified["file_count"]}

    def manual_requeue(self, job_id: str, reason: str) -> dict:
        if not JOB_ID_RE.fullmatch(job_id):
            raise ValidationError("invalid job_id")
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            if row["state"] not in {"failed", "needs_attention"}:
                raise ValidationError("only failed or needs_attention jobs can be manually requeued")
            document = json.loads(db.execute("SELECT document FROM jobs WHERE job_id=?", (job_id,)).fetchone()["document"])
            if parse_utc(document["deadline_utc"]) <= datetime.now(timezone.utc):
                raise ValidationError("immutable job deadline has passed; submit a new job_id with a new deadline")
            db.execute("UPDATE jobs SET state='queued',updated_utc=?,worker_id=NULL,lease_token=NULL,lease_expires_utc=NULL,note=? WHERE job_id=?", (utc_now(), f"manual requeue: {reason}"[:2000], job_id))
            self._event(db, job_id, "manual_requeue", {"reason": reason[:2000], "previous_state": row["state"]})
        return {"job_id": job_id, "state": "queued"}


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "RFSimHost/0.1"

    @property
    def app(self):
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authenticate(self) -> bool:
        header = self.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else None
        if secure_compare(token, self.app.api_token):
            return True
        self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
        return False

    def read_json(self, limit: int = 1024 * 1024) -> dict:
        length = int(self.headers.get("Content-Length", "-1"))
        if length < 0 or length > limit:
            raise ValidationError("invalid request size")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValidationError("JSON body must be an object")
        return value

    def do_GET(self):
        if not self.authenticate():
            return
        try:
            path = urlparse(self.path).path
            if path == "/api/v1/jobs":
                return self.send_json(200, self.app.store.status())
            if path.startswith("/api/v1/jobs/"):
                job_id = path.removeprefix("/api/v1/jobs/")
                return self.send_json(200, self.app.store.status(job_id))
            if path.startswith("/api/v1/results/"):
                job_id = path.removeprefix("/api/v1/results/")
                status = self.app.store.status(job_id)
                if status["state"] != "completed":
                    return self.send_json(409, {"error": "result is not complete"})
                file = self.app.store.root / "results" / f"{job_id}.zip"
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(file.stat().st_size))
                self.send_header("X-Content-SHA256", status["result_sha256"])
                self.end_headers()
                with file.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        self.wfile.write(chunk)
                return
            self.send_json(404, {"error": "not found"})
        except KeyError:
            self.send_json(404, {"error": "job not found"})
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})

    def do_POST(self):
        if not self.authenticate():
            return
        temp_path = None
        try:
            path = urlparse(self.path).path
            if path == "/api/v1/jobs":
                return self.send_json(201, self.app.store.submit(self.read_json()))
            if path == "/api/v1/lease":
                body = self.read_json()
                leased = self.app.store.lease(body.get("worker_id", ""), body.get("runners", []))
                return self.send_json(200, {"lease": leased})
            if path.endswith("/heartbeat") and path.startswith("/api/v1/jobs/"):
                job_id = path.split("/")[-2]
                body = self.read_json()
                result = self.app.store.heartbeat(job_id, body.get("worker_id", ""), body.get("lease_token", ""), body.get("stage", "running"))
                return self.send_json(200, result)
            if path.endswith("/fail") and path.startswith("/api/v1/jobs/"):
                job_id = path.split("/")[-2]
                body = self.read_json()
                return self.send_json(200, self.app.store.fail(job_id, body.get("worker_id", ""), body.get("lease_token", ""), body.get("reason", "unspecified worker failure")))
            if path.endswith("/attention") and path.startswith("/api/v1/jobs/"):
                job_id = path.split("/")[-2]
                body = self.read_json()
                return self.send_json(200, self.app.store.attention(job_id, body.get("worker_id", ""), body.get("lease_token", ""), body.get("reason", "uncertain external execution state")))
            if path.endswith("/requeue") and path.startswith("/api/v1/jobs/"):
                job_id = path.split("/")[-2]
                body = self.read_json()
                return self.send_json(200, self.app.store.manual_requeue(job_id, body.get("reason", "operator decision")))
            if path.startswith("/api/v1/results/"):
                job_id = path.removeprefix("/api/v1/results/")
                length = int(self.headers.get("Content-Length", "-1"))
                if length < 0 or length > self.app.store.max_result_bytes:
                    raise ValidationError("invalid or excessive result size")
                worker_id = self.headers.get("X-Worker-ID", "")
                lease_token = self.headers.get("X-Lease-Token", "")
                claimed = self.headers.get("X-Content-SHA256", "")
                fd, temp_name = tempfile.mkstemp(prefix="upload-", suffix=".zip", dir=self.app.store.root)
                temp_path = Path(temp_name)
                with os.fdopen(fd, "wb") as handle:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValidationError("truncated result upload")
                        handle.write(chunk)
                        remaining -= len(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                result = self.app.store.accept_result(job_id, worker_id, lease_token, temp_path, claimed)
                temp_path = None
                return self.send_json(201, result)
            self.send_json(404, {"error": "not found"})
        except KeyError:
            self.send_json(404, {"error": "job not found"})
        except PermissionError as exc:
            self.send_json(409, {"error": str(exc)})
        except (ValidationError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            # Bad zip files and malformed input are client failures; avoid leaking internals.
            self.send_json(400, {"error": f"request rejected: {type(exc).__name__}"})
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()


class HostServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store: Store, api_token: str):
        super().__init__(address, ApiHandler)
        self.app = type("App", (), {"store": store, "api_token": api_token})()
