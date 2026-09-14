from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "adapters" / "candidate_local_metal_v2.py"


FAKE_NIGHT_CASE = r'''
import csv, gzip, hashlib, json, shutil, sys
from pathlib import Path

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
archive, work = Path(job["archive"]), Path(job["work"])
archive.mkdir(parents=True)
work.mkdir(parents=True)
(work / "model.cst").write_bytes(b"offline fake model")
(archive / "model.vba").write_text("' offline fake; no CST\n", encoding="utf-8")
(archive / "mesh-cells.txt").write_text("128\n", encoding="utf-8")
(archive / "lifecycle.jsonl").write_text('{"event":"offline_fake"}\n', encoding="utf-8")
(archive / "summary.md").write_text("Offline adapter fixture.\n", encoding="utf-8")
shutil.copy2(__file__, archive / "night_case.py")
header = ["frequency_GHz"] + [f"{name}_{part}" for name in ("S1,1", "S1,2", "S2,1", "S2,2") for part in ("real", "imag")]
with gzip.open(archive / "sparameters.csv.gz", "wt", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(header)
    writer.writerow([3.0, .1, -.2, .7, -.1, .7, -.1, .1, -.2])
    writer.writerow([3.5, .08, -.18, .72, -.08, .72, -.08, .09, -.17])
record = {
    "run_id": job["id"], "results_validated": True, "project_closed": True,
    "source_hashes": {"night_case.py": digest(Path(__file__))}, "metrics": {"samples": 2}
}
(archive / "record.json").write_text(json.dumps(record), encoding="utf-8")
names = ["model.vba", "mesh-cells.txt", "lifecycle.jsonl", "summary.md", "night_case.py", "sparameters.csv.gz", "record.json"]
files = {name: {"bytes": (archive / name).stat().st_size, "sha256": digest(archive / name)} for name in names}
verified = {"job_id": job["id"], "results_validated": True, "samples": 2, "files": files}
(archive / "verified.json").write_text(json.dumps(verified), encoding="utf-8")
raise SystemExit(0)
'''


class CstAdapterOfflineContractTest(unittest.TestCase):
    def test_t00009_shaped_job_preserves_raw_archive_and_builds_compact_views(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            source = temp / "frozen-source"
            run_dir = temp / "run"
            source.mkdir()
            run_dir.mkdir()
            night_case = source / "night_case.py"
            night_case.write_text(FAKE_NIGHT_CASE, encoding="utf-8")
            night_hash = hashlib.sha256(night_case.read_bytes()).hexdigest()
            source_manifest = source / "source-manifest.json"
            source_manifest.write_text(json.dumps({"night_case.py": night_hash}, indent=2), encoding="utf-8")
            manifest_hash = hashlib.sha256(source_manifest.read_bytes()).hexdigest()
            deadline = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            # Field shape is based on the retained t00009 temporal-recovery job;
            # archive/work/id are deliberately supplied only by the adapter.
            parameters = {
                "accuracy_dB": -80,
                "capture_view": True,
                "evidence_domain": "phantom",
                "field_monitor_GHz": 3.25,
                "geometry_id": "G5-hilbert_split_ring",
                "gpu": True,
                "group_id": "t9-t10",
                "local_step_mm": 0.1,
                "mesh": 64,
                "number_of_pulse_widths": 200,
                "pair_id": "t9-t10",
                "params": {
                    "lesion_conductivity": 0.03,
                    "lesion_permittivity": 12.0,
                    "ring_scale": 1.0,
                    "sensing_gap_mm": 0.6,
                    "topology": "hilbert_split_ring"
                },
                "phase": "temporal_stopping_control",
                "role": "same_material_insert",
                "timeout_seconds": 3600
            }
            job = {
                "schema_version": 1, "job_id": "t00009", "study_id": "tooth-sensor",
                "runner": "cst-candidate-local-metal-v2", "source": {"version": "candidate-local-metal-v2", "sha256": manifest_hash},
                "parameters": parameters, "deadline_utc": deadline
            }
            job_file = run_dir / "job.json"
            job_file.write_text(json.dumps(job), encoding="utf-8")
            result = subprocess.run([sys.executable, str(ADAPTER), str(job_file), str(run_dir), "--source-root", str(source)], cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            raw_archive = run_dir / "source" / "cst-archive"
            raw_work = run_dir / "model" / "cst-work"
            self.assertTrue((raw_archive / "verified.json").is_file())
            self.assertTrue((raw_archive / "record.json").is_file())
            self.assertTrue((raw_work / "model.cst").is_file())
            retained_record = json.loads((raw_archive / "record.json").read_text(encoding="utf-8"))
            self.assertTrue(retained_record["results_validated"])
            self.assertTrue(retained_record["project_closed"])
            self.assertTrue((run_dir / "results" / "sparameters.csv.gz").is_file())
            self.assertTrue((run_dir / "quality" / "verified.json").is_file())
            self.assertTrue((run_dir / "source" / "source-manifest.json").is_file())
            mapping = json.loads((run_dir / "parameters" / "transport-mapping.json").read_text(encoding="utf-8"))
            self.assertTrue(mapping["raw_archive_preserved"])
            self.assertTrue(mapping["raw_work_preserved"])
            self.assertEqual(mapping["adapter_validation"]["sparameter_rows"], 2)

    def test_compact_mode_accepts_versioned_manifest_and_removes_only_raw_cst_trees(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            source, run_dir = temp / "frozen-source", temp / "run"
            source.mkdir(); run_dir.mkdir()
            night_case = source / "night_case.py"
            night_case.write_text(FAKE_NIGHT_CASE, encoding="utf-8")
            night_hash = hashlib.sha256(night_case.read_bytes()).hexdigest()
            source_manifest = source / "source-manifest.json"
            source_manifest.write_text(json.dumps({"source_version": "fixture-v1", "sha256": {"night_case.py": night_hash}}, indent=2), encoding="utf-8")
            job = {
                "schema_version": 1, "job_id": "compact-1", "study_id": "tooth-sensor", "runner": "cst-fixture",
                "source": {"version": "fixture-v1", "sha256": hashlib.sha256(source_manifest.read_bytes()).hexdigest()},
                "parameters": {}, "deadline_utc": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            }
            job_file = run_dir / "job.json"; job_file.write_text(json.dumps(job), encoding="utf-8")
            result = subprocess.run([sys.executable, str(ADAPTER), str(job_file), str(run_dir), "--source-root", str(source), "--compact"], cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((run_dir / "source" / "cst-archive").exists())
            self.assertFalse((run_dir / "model" / "cst-work").exists())
            self.assertTrue((run_dir / "source" / "model.vba").is_file())
            self.assertTrue((run_dir / "source" / "pinned-source" / "night_case.py").is_file())
            mapping = json.loads((run_dir / "parameters" / "transport-mapping.json").read_text(encoding="utf-8"))
            self.assertFalse(mapping["raw_archive_preserved"])
            self.assertFalse(mapping["raw_work_preserved"])
            self.assertIn("compacted_utc", mapping)


if __name__ == "__main__":
    unittest.main()
