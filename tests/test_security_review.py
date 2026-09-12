from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from rfsim.common import ValidationError, safe_member_name, sha256_file, validate_job, validate_result_zip
from rfsim.host import Store
from rfsim.worker import ExecutionUncertain, _validate_parameter_schema, package_result, run_fixed_python, run_mock


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

    def test_worker_with_unresolved_expired_lease_is_quarantined(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp), 60, {"mock-v1"}, 1_000_000, 2_000_000)
            store.submit(job("uncertain"))
            store.submit(job("next"))
            self.assertIsNotNone(store.lease("school-pc", ["mock-v1"]))
            with store.connect() as db:
                db.execute("UPDATE jobs SET lease_expires_utc='2000-01-01T00:00:00Z' WHERE job_id='uncertain'")
            self.assertEqual(store.status("uncertain")["state"], "needs_attention")
            self.assertIsNone(store.lease("school-pc", ["mock-v1"]))
            self.assertIsNotNone(store.lease("different-pc", ["mock-v1"]))

    def test_result_after_deadline_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            deadline_job = job("late", future(seconds=0.15))
            deadline_job["parameters"] = {"mock_delay_seconds": 0.2}
            store = Store(root / "host", 60, {"mock-v1"}, 1_000_000, 2_000_000)
            store.submit(deadline_job)
            lease = store.lease("school-pc", ["mock-v1"])
            run_dir = root / "worker" / "runs" / "late"
            run_mock(deadline_job, run_dir, {"max_mock_delay_seconds": 1})
            bundle = package_result(run_dir, deadline_job)
            with self.assertRaises(PermissionError):
                store.accept_result("late", "school-pc", lease["lease_token"], bundle, sha256_file(bundle))
            self.assertEqual(store.status("late")["state"], "needs_attention")

    def test_result_is_bound_to_full_immutable_job_document(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            submitted = job("binding")
            store = Store(root / "host", 60, {"mock-v1"}, 1_000_000, 2_000_000)
            store.submit(submitted)
            lease = store.lease("school-pc", ["mock-v1"])
            altered = json.loads(json.dumps(submitted))
            altered["parameters"] = {"mock_delay_seconds": 0}
            run_dir = root / "worker" / "runs" / "binding"
            run_mock(altered, run_dir, {"max_mock_delay_seconds": 1})
            bundle = package_result(run_dir, altered)
            with self.assertRaisesRegex(ValidationError, "job"):
                store.accept_result("binding", "school-pc", lease["lease_token"], bundle, sha256_file(bundle))


class ArtifactSafetyTests(unittest.TestCase):
    def test_archive_paths_are_portable_to_windows(self):
        unsafe = (
            "model//file.cst",
            "model/./file.cst",
            "model/file.cst.",
            "model/file.cst ",
            "model/file.cst:stream",
            "model/CON",
            "logs/nul.txt",
            "results/COM1.csv",
        )
        for name in unsafe:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                safe_member_name(name)

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
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr("manifest.json", json.dumps({"schema_version": 1, "job_id": "dup", "artifacts": []}))
                    archive.writestr("manifest.json", json.dumps({"schema_version": 1, "job_id": "dup", "artifacts": []}))
            with self.assertRaises(ValidationError):
                validate_result_zip(archive_path, "dup", 1_000_000)

    def test_zip_rejects_case_insensitive_aliases(self):
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "aliases.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("model/File.cst", b"first")
                archive.writestr("model/file.cst", b"second")
                archive.writestr("manifest.json", json.dumps({"schema_version": 1, "job_id": "alias", "artifacts": []}))
            with self.assertRaises(ValidationError):
                validate_result_zip(archive_path, "alias", 1_000_000)

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

    def test_manifest_has_an_independent_memory_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "large-manifest.zip"
            artifacts: list[dict] = []
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for prefix in ("model", "source", "parameters", "results", "mesh", "logs", "quality"):
                    name = f"{prefix}/evidence.bin"
                    archive.writestr(name, b"x")
                    artifacts.append({"path": name, "size": 1, "sha256": "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"})
                manifest = {
                    "schema_version": 1,
                    "job_id": "large",
                    "artifacts": artifacts,
                    "padding": "x" * (5 * 1024 * 1024),
                }
                archive.writestr("manifest.json", json.dumps(manifest))
            with self.assertRaisesRegex(ValidationError, "manifest"):
                validate_result_zip(archive_path, "large", 10 * 1024 * 1024)


class ParameterSafetyTests(unittest.TestCase):
    def test_non_finite_number_is_rejected(self):
        schema = {"value": {"type": "number", "min": 0, "max": 10}}
        with self.assertRaises(ValidationError):
            _validate_parameter_schema({"value": float("nan")}, schema)

    def test_unknown_schema_type_fails_closed(self):
        with self.assertRaises(ValidationError):
            _validate_parameter_schema({"value": "anything"}, {"value": {"type": "typo"}})

    def test_boolean_is_not_an_integer_job_field(self):
        document = job("boolean-schema")
        document["schema_version"] = True
        with self.assertRaises(ValidationError):
            validate_job(document, {"mock-v1"})

        document = job("boolean-priority")
        document["priority"] = True
        with self.assertRaises(ValidationError):
            validate_job(document, {"mock-v1"})

    def test_invalid_deadline_type_is_a_validation_error(self):
        document = job("bad-deadline")
        document["deadline_utc"] = 123
        with self.assertRaises(ValidationError):
            validate_job(document, {"mock-v1"})


class FixedRunnerSafetyTests(unittest.TestCase):
    def test_absolute_job_deadline_bounds_external_runner_wait(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "slow_adapter.py"
            script.write_text("import time\ntime.sleep(1)\n", encoding="utf-8")
            document = job("deadline-runner", future(seconds=0.2))
            config = {
                "script": str(script),
                "python": sys.executable,
                "script_sha256": sha256_file(script),
                "source_sha256": document["source"]["sha256"],
                "parameter_schema": {},
                "arguments": [],
                "timeout_seconds": 60,
            }
            started = time.monotonic()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ResourceWarning)
                with self.assertRaises(ExecutionUncertain):
                    run_fixed_python(document, root / "run", config)
            self.assertLess(time.monotonic() - started, 0.8)
            runtime = json.loads((root / "run" / "logs" / "external-process.json").read_text(encoding="utf-8"))
            self.assertEqual(runtime["state"], "timeout_process_state_unknown")
            self.assertFalse(runtime["automatic_termination"])

            # The production code deliberately leaves an uncertain external
            # process alone. This test owns its harmless fixture and cleans up
            # only the exact PID recorded by the runner.
            pid = runtime["pid"]
            if os.name == "nt":
                cleanup = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertIn(cleanup.returncode, (0, 128))
                time.sleep(0.1)
            else:
                os.kill(pid, signal.SIGTERM)
                os.waitpid(pid, 0)

if __name__ == "__main__":
    unittest.main()
