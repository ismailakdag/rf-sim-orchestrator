from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from rfsim.common import ValidationError, validate_result_zip
from rfsim.host import Store
from rfsim.worker import _validate_parameter_schema, package_result


def future(seconds: int = 3_600) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def job(job_id: str, deadline_utc: str | None = None) -> dict:
    return {
        "schema_version": 1,
        "job_id": job_id,
        "study_id": "security-review",
        "runner": "mock-v1",
        "source": {"version": "fixture-v1", "sha256": "a" * 64},
        "parameters": {},
        "deadline_utc": deadline_utc or future(),
    }


class LeaseSafetyTests(unittest.TestCase):
    def test_one_active_job_per_worker_id(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp), 60, {"mock-v1"}, 1_000_000, 2_000_000)
            store.submit(job("one"))
            store.submit(job("two"))
            self.assertIsNotNone(store.lease("school-pc", ["mock-v1"]))
            self.assertIsNone(store.lease("school-pc", ["mock-v1"]))

    def test_initial_lease_never_exceeds_job_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            deadline = future(seconds=30)
            store = Store(Path(temp), 3_600, {"mock-v1"}, 1_000_000, 2_000_000)
            store.submit(job("deadline", deadline))
            lease = store.lease("school-pc", ["mock-v1"])
            self.assertIsNotNone(lease)
            lease_expiry = datetime.fromisoformat(lease["lease_expires_utc"].replace("Z", "+00:00"))
            job_deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            self.assertLessEqual(lease_expiry, job_deadline)


class ArtifactSafetyTests(unittest.TestCase):
    def test_package_rejects_symlink_artifact(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "run"
            run_dir.mkdir()
            outside = root / "outside-secret.txt"
            outside.write_text("must not enter result package", encoding="utf-8")
            link = run_dir / "leak.txt"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")
            with self.assertRaises(ValidationError):
                package_result(run_dir, job("symlink"))

    def test_zip_rejects_duplicate_member_names(self):
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "duplicate.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"schema_version": 1, "job_id": "dup", "artifacts": []}))
                archive.writestr("manifest.json", json.dumps({"schema_version": 1, "job_id": "dup", "artifacts": []}))
            with self.assertRaises(ValidationError):
                validate_result_zip(archive_path, "dup", 1_000_000)

    def test_zip_rejects_unix_symlink_member(self):
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "symlink.zip"
            info = zipfile.ZipInfo("model/link")
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(info, "../../outside")
                archive.writestr("manifest.json", json.dumps({"schema_version": 1, "job_id": "link", "artifacts": []}))
            with self.assertRaises(ValidationError):
                validate_result_zip(archive_path, "link", 1_000_000)


class ParameterSafetyTests(unittest.TestCase):
    def test_non_finite_number_is_rejected(self):
        schema = {"value": {"type": "number", "min": 0, "max": 10}}
        with self.assertRaises(ValidationError):
            _validate_parameter_schema({"value": float("nan")}, schema)

    def test_unknown_schema_type_fails_closed(self):
        with self.assertRaises(ValidationError):
            _validate_parameter_schema({"value": "anything"}, {"value": {"type": "typo"}})


if __name__ == "__main__":
    unittest.main()
