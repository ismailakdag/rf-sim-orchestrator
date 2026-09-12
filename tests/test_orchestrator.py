from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from rfsim.common import ValidationError, validate_result_zip
from rfsim.host import Store
from rfsim.worker import _validate_parameter_schema


def future() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")


def job(job_id: str = "test-1") -> dict:
    return {
        "schema_version": 1,
        "job_id": job_id,
        "study_id": "test-study",
        "runner": "mock-v1",
        "source": {"version": "fixture-v1", "sha256": "a" * 64},
        "parameters": {"mock_delay_seconds": 0.01},
        "deadline_utc": future(),
    }


class ValidationTests(unittest.TestCase):
    def test_job_is_immutable_and_allowlisted(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp), 60, {"mock-v1"}, 10_000_000, 20_000_000)
            original = job()
            first = store.submit(original)
            self.assertFalse(first["idempotent"])
            self.assertTrue(store.submit(original)["idempotent"])
            changed = json.loads(json.dumps(original))
            changed["parameters"]["mock_delay_seconds"] = 1
            with self.assertRaises(ValidationError):
                store.submit(changed)
            invalid = job("bad-runner")
            invalid["runner"] = "remote-command"
            with self.assertRaises(ValidationError):
                store.submit(invalid)

    def test_expired_lease_needs_attention_without_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp), 60, {"mock-v1"}, 10_000_000, 20_000_000)
            store.submit(job())
            lease = store.lease("worker-a", ["mock-v1"])
            self.assertIsNotNone(lease)
            with store.connect() as db:
                db.execute("UPDATE jobs SET lease_expires_utc='2000-01-01T00:00:00Z' WHERE job_id='test-1'")
            self.assertEqual(store.status("test-1")["state"], "needs_attention")
            self.assertIsNone(store.lease("worker-b", ["mock-v1"]))

    def test_worker_can_hold_only_one_active_job(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp), 60, {"mock-v1"}, 10_000_000, 20_000_000)
            store.submit(job("one"))
            store.submit(job("two"))
            self.assertIsNotNone(store.lease("same-school-pc", ["mock-v1"]))
            self.assertIsNone(store.lease("same-school-pc", ["mock-v1"]))
            self.assertIsNotNone(store.lease("different-school-pc", ["mock-v1"]))

    def test_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../escape.txt", b"bad")
                archive.writestr("manifest.json", json.dumps({"schema_version": 1, "job_id": "test-1", "artifacts": []}))
            with self.assertRaises(ValidationError):
                validate_result_zip(path, "test-1", 1_000_000)

    def test_nested_worker_parameter_allowlist_is_bounded(self):
        schema = {"mesh": {"type": "integer", "min": 16, "max": 96}, "params": {"type": "object", "properties": {"topology": {"type": "string", "enum": ["hilbert"]}}}}
        _validate_parameter_schema({"mesh": 64, "params": {"topology": "hilbert"}}, schema)
        with self.assertRaises(ValidationError):
            _validate_parameter_schema({"mesh": 128, "params": {"topology": "hilbert"}}, schema)
        with self.assertRaises(ValidationError):
            _validate_parameter_schema({"mesh": 64, "params": {"topology": "hilbert", "command": "calc.exe"}}, schema)


class SubprocessEndToEndTest(unittest.TestCase):
    def test_mock_host_worker_and_verified_download(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            token = "e2e-" + "x" * 40
            host_config = temp / "host.toml"
            worker_config = temp / "worker.toml"
            job_file = temp / "job.json"
            host_config.write_text(f'[host]\nbind="127.0.0.1"\nport={port}\ndata_dir="{(temp / "host").as_posix()}"\nlease_seconds=30\nallowed_runners=["mock-v1"]\nmax_result_bytes=10000000\nmax_uncompressed_bytes=20000000\n', encoding="utf-8")
            worker_config.write_text(f'[worker]\nhost_url="http://127.0.0.1:{port}"\nworker_id="subprocess-worker"\ndata_dir="{(temp / "worker").as_posix()}"\nheartbeat_seconds=1\n[runners.mock-v1]\ntype="mock"\nmax_mock_delay_seconds=1\n', encoding="utf-8")
            job_file.write_text(json.dumps(job("e2e-1")), encoding="utf-8")
            env = {**os.environ, "PYTHONPATH": str(SRC), "RF_SIM_TOKEN": token}
            host = subprocess.Popen([sys.executable, "-m", "rfsim", "host", "--config", str(host_config)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True)
            try:
                url = f"http://127.0.0.1:{port}"
                for _ in range(50):
                    try:
                        request = urllib.request.Request(url + "/api/v1/jobs", headers={"Authorization": f"Bearer {token}"})
                        with urllib.request.urlopen(request, timeout=1):
                            break
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.05)
                else:
                    self.fail("host subprocess did not start")

                unauthorized = urllib.request.Request(url + "/api/v1/jobs")
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(unauthorized, timeout=2)
                self.assertEqual(caught.exception.code, 401)

                def run_cli(*arguments):
                    return subprocess.run([sys.executable, "-m", "rfsim", *arguments], cwd=ROOT, env=env, check=True, capture_output=True, text=True)

                run_cli("submit", "--url", url, str(job_file))
                worker_result = json.loads(run_cli("worker", "--config", str(worker_config), "--once").stdout)
                self.assertEqual(worker_result["state"], "completed")
                status = json.loads(run_cli("status", "--url", url, "e2e-1").stdout)
                self.assertEqual(status["state"], "completed")
                destination = temp / "download" / "e2e-1.zip"
                downloaded = json.loads(run_cli("results", "--url", url, "e2e-1", "--output", str(destination)).stdout)
                self.assertTrue(downloaded["verified"])
                self.assertGreaterEqual(downloaded["artifact_count"], 7)
                self.assertTrue(destination.is_file())

                job_file.write_text(json.dumps(job("resolve-e2e")), encoding="utf-8")
                run_cli("submit", "--url", url, str(job_file))

                def post_json(path, payload):
                    request = urllib.request.Request(
                        url + path,
                        data=json.dumps(payload).encode("utf-8"),
                        method="POST",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(request, timeout=2) as response:
                        return json.load(response)

                lease = post_json("/api/v1/lease", {"worker_id": "resolve-worker", "runners": ["mock-v1"]})["lease"]
                post_json(
                    "/api/v1/jobs/resolve-e2e/attention",
                    {
                        "worker_id": "resolve-worker",
                        "lease_token": lease["lease_token"],
                        "reason": "offline fixture entered an uncertain state",
                    },
                )
                resolved = json.loads(
                    run_cli(
                        "resolve",
                        "--url",
                        url,
                        "resolve-e2e",
                        "--reason",
                        "Fixture inspected; no process remains active.",
                    ).stdout
                )
                self.assertEqual(resolved["state"], "resolved")
                resolved_status = json.loads(run_cli("status", "--url", url, "resolve-e2e").stdout)
                self.assertEqual(resolved_status["state"], "resolved")
            finally:
                host.terminate()
                try:
                    host.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    host.kill()
                    host.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
