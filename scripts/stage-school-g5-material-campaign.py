"""Gate the school CST 2025 material campaign on a differential bridge.

This local controller waits for the two immutable bridge jobs, downloads and
hash-checks them, compares their complex S21 difference with the completed CST
2026 anchor, and submits the remaining pinned catalog only when the declared
cross-version gate passes.  It never retries a solver job.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

CONTROL_ID = "school-g5-bridge-control-v5-20260914"
CONTRAST_ID = "school-g5-bridge-contrast-v5-20260914"
RUNNER = "cst-g5-material-cst2025-v5"
SOURCE = {"version": "fr4-g5-material-cst2025-v5", "sha256": "08bae0e8a97ae24f18e6585ca0333679020b6a76b496bb14390826c5bbb415f7"}
BRIDGE_CASES = {"g5-M01-nominal-near-control", "g5-M01-nominal-near-E16-S018"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


class Api:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token

    def json(self, method: str, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.url + path, data=data, method=method, headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)

    def download(self, job_id: str, target: Path) -> None:
        request = urllib.request.Request(self.url + f"/api/v1/results/{job_id}", headers={"Authorization": f"Bearer {self.token}"})
        temp = target.with_suffix(".zip.tmp")
        with urllib.request.urlopen(request, timeout=300) as response, temp.open("wb") as output:
            expected = response.headers["X-Content-SHA256"]
            while block := response.read(1024 * 1024):
                output.write(block)
        observed = hashlib.sha256(temp.read_bytes()).hexdigest()
        if observed != expected:
            raise RuntimeError(f"Download hash mismatch for {job_id}")
        os.replace(temp, target)


def read_s21_file(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    frequency = np.array([float(row["frequency_GHz"]) for row in rows])
    s21 = np.array([complex(float(row["S2,1_real"]), float(row["S2,1_imag"])) for row in rows])
    return frequency, s21


def read_s21_zip(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with zipfile.ZipFile(path) as archive:
        with archive.open("results/sparameters.csv.gz") as raw:
            data = raw.read()
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as handle:
        text = io.TextIOWrapper(handle, encoding="utf-8", newline="")
        rows = list(csv.DictReader(text))
    frequency = np.array([float(row["frequency_GHz"]) for row in rows])
    s21 = np.array([complex(float(row["S2,1_real"]), float(row["S2,1_imag"])) for row in rows])
    return frequency, s21


def bridge_metrics(local_control: Path, local_contrast: Path, remote_control: Path, remote_contrast: Path) -> dict:
    lf0, ls0 = read_s21_file(local_control)
    lf1, ls1 = read_s21_file(local_contrast)
    rf0, rs0 = read_s21_zip(remote_control)
    rf1, rs1 = read_s21_zip(remote_contrast)
    if not all(np.array_equal(lf0, axis) for axis in (lf1, rf0, rf1)):
        raise RuntimeError("Bridge frequency axes differ")
    mask = (lf0 >= 1.0) & (lf0 <= 4.5)
    local_delta, remote_delta = ls1[mask] - ls0[mask], rs1[mask] - rs0[mask]
    local_rms = float(np.sqrt(np.mean(np.abs(local_delta) ** 2)))
    remote_rms = float(np.sqrt(np.mean(np.abs(remote_delta) ** 2)))
    denominator = float(np.linalg.norm(local_delta) * np.linalg.norm(remote_delta))
    coherence = float(abs(np.vdot(local_delta, remote_delta)) / denominator) if denominator else 0.0
    scale = np.vdot(remote_delta, local_delta) / max(float(np.vdot(remote_delta, remote_delta).real), 1e-30)
    residual = float(np.linalg.norm(local_delta - scale * remote_delta) / max(np.linalg.norm(local_delta), 1e-30))
    ratio = remote_rms / max(local_rms, 1e-30)
    passed = 0.5 <= ratio <= 2.0 and coherence >= 0.95 and residual <= 0.35
    return {
        "band_GHz": [1.0, 4.5], "samples": int(mask.sum()),
        "local_delta_s21_rms": local_rms, "school_delta_s21_rms": remote_rms,
        "school_to_local_rms_ratio": ratio, "complex_coherence": coherence,
        "optimal_scale_residual": residual,
        "gate": {"rms_ratio": [0.5, 2.0], "minimum_coherence": 0.95, "maximum_scale_residual": 0.35, "passed": passed},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--local-anchor-root", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--deadline-utc", default=None)
    parser.add_argument("--worker-id", default="OKUL-PC-01")
    parser.add_argument("--release-remaining", action="store_true", help="submit the remaining catalog only after the bridge passes")
    args = parser.parse_args()
    end = datetime.fromisoformat(args.deadline_utc.replace("Z", "+00:00")).astimezone(timezone.utc) if args.deadline_utc else None
    work = args.work_dir.resolve(); work.mkdir(parents=True, exist_ok=True)
    state_path = work / "stage-state.json"
    api = Api(args.url, args.token_file.read_text(encoding="utf-8").strip())
    state = {"schema_version": 1, "status": "waiting_bridge", "started_utc": now(), "bridge_jobs": [CONTROL_ID, CONTRAST_ID], "automatic_solver_retry": False}
    atomic_json(state_path, state)
    while end is None or datetime.now(timezone.utc) < end:
        try:
            statuses = {job_id: api.json("GET", f"/api/v1/jobs/{job_id}") for job_id in (CONTROL_ID, CONTRAST_ID)}
            terminal_bad = {job_id: item["state"] for job_id, item in statuses.items() if item["state"] in {"failed", "needs_attention", "expired", "resolved"}}
            if terminal_bad:
                state.update(status="bridge_failed_closed", ended_utc=now(), terminal_states=terminal_bad)
                atomic_json(state_path, state); return
            if all(item["state"] == "completed" for item in statuses.values()):
                break
        except Exception as exc:
            state.update(last_connection_error=repr(exc), last_connection_error_utc=now())
            atomic_json(state_path, state)
        time.sleep(30)
    else:
        state.update(status="deadline_before_bridge", ended_utc=now()); atomic_json(state_path, state); return

    downloads = work / "downloads"; downloads.mkdir(exist_ok=True)
    remote_control, remote_contrast = downloads / f"{CONTROL_ID}.zip", downloads / f"{CONTRAST_ID}.zip"
    api.download(CONTROL_ID, remote_control); api.download(CONTRAST_ID, remote_contrast)
    anchors = args.local_anchor_root.resolve()
    metrics = bridge_metrics(
        anchors / "d00025/sparameters.csv.gz", anchors / "d00026/sparameters.csv.gz",
        remote_control, remote_contrast,
    )
    state["bridge_metrics"] = metrics
    if not metrics["gate"]["passed"]:
        state.update(status="bridge_gate_rejected", ended_utc=now()); atomic_json(state_path, state); return

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))["cases"]
    if not args.release_remaining:
        state.update(status="bridge_passed_awaiting_release", ended_utc=now(), planned_remaining_jobs=len(set(catalog) - BRIDGE_CASES))
        atomic_json(state_path, state)
        return
    submitted = []
    for index, case_id in enumerate(sorted(set(catalog) - BRIDGE_CASES), 1):
        slug = case_id.lower().replace("_", "-")
        job_id = f"school-g5m5-{index:02d}-{slug}"
        role = catalog[case_id]["legacy_job"]["role"]
        document = {
            "schema_version": 1, "job_id": job_id, "study_id": "g5-material-sensitivity-v1",
            "runner": RUNNER, "source": SOURCE, "parameters": {"case_id": case_id},
            "deadline_utc": args.deadline_utc, "priority": 80 if role == "same_material_insert" else 50,
            "metadata": {"required_worker_id": args.worker_id, "campaign": "20260914-g5-material-sensitivity-school-v5", "stage": "material_response_surface", "case_id": case_id},
        }
        response = api.json("POST", "/api/v1/jobs", document)
        submitted.append({"job_id": job_id, "case_id": case_id, "document_sha256": response["document_sha256"]})
    state.update(status="bridge_passed_material_jobs_submitted", submitted_utc=now(), submitted_jobs=submitted)
    atomic_json(state_path, state)


if __name__ == "__main__":
    main()
