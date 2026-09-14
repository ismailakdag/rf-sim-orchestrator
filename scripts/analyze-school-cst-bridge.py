from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import numpy as np


CURVES = ("S1,1", "S1,2", "S2,1", "S2,2")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_curves(data: bytes) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    with gzip.open(io.BytesIO(data), "rt", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    frequency = np.asarray([float(row["frequency_GHz"]) for row in rows])
    curves = {
        name: np.asarray(
            [complex(float(row[f"{name}_real"]), float(row[f"{name}_imag"])) for row in rows]
        )
        for name in CURVES
    }
    return frequency, curves


def rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.abs(values) ** 2)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare a verified remote CST package with a local bridge reference.")
    parser.add_argument("remote_zip", type=Path)
    parser.add_argument("baseline_archive", type=Path)
    parser.add_argument("--paired-contrast-archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    remote_zip = args.remote_zip.resolve(strict=True)
    baseline = args.baseline_archive.resolve(strict=True)

    with ZipFile(remote_zip) as archive:
        package_manifest = json.loads(archive.read("manifest.json"))
        remote_record = json.loads(archive.read("parameters/record.json"))
        remote_verified = json.loads(archive.read("quality/verified.json"))
        remote_frequency, remote_curves = load_curves(archive.read("results/sparameters.csv.gz"))
        remote_mesh = archive.read("source/cst-archive/mesh-grid.bin")
        remote_vba = archive.read("source/model.vba").decode("utf-8")
    baseline_record = json.loads((baseline / "record.json").read_text(encoding="utf-8"))
    baseline_frequency, baseline_curves = load_curves((baseline / "sparameters.csv.gz").read_bytes())
    baseline_mesh = (baseline / "mesh-grid.bin").read_bytes()
    baseline_vba = (baseline / "model.vba").read_text(encoding="utf-8")

    if not np.array_equal(remote_frequency, baseline_frequency):
        raise RuntimeError("remote and baseline frequency axes differ")
    curve_differences = {}
    for name in CURVES:
        difference = remote_curves[name] - baseline_curves[name]
        baseline_rms = rms(baseline_curves[name])
        db_remote = 20 * np.log10(np.maximum(np.abs(remote_curves[name]), 1e-15))
        db_baseline = 20 * np.log10(np.maximum(np.abs(baseline_curves[name]), 1e-15))
        curve_differences[name] = {
            "complex_rms": rms(difference),
            "complex_rms_percent_of_baseline": 100 * rms(difference) / baseline_rms,
            "max_absolute_complex_difference": float(np.max(np.abs(difference))),
            "magnitude_db_rms": rms(db_remote - db_baseline),
            "magnitude_db_max_absolute": float(np.max(np.abs(db_remote - db_baseline))),
        }
    remote_all = np.concatenate([remote_curves[name] for name in CURVES])
    baseline_all = np.concatenate([baseline_curves[name] for name in CURVES])
    all_difference = remote_all - baseline_all

    def canonical_vba(text: str) -> list[str]:
        return [
            line.rstrip()
            for line in text.replace("\r\n", "\n").splitlines()
            if line.strip()
            and "HardwareAcceleration" not in line
            and "FrequencySampleRuleLin" not in line
        ]

    report = {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "remote_package": {"path": str(remote_zip), "sha256": sha256(remote_zip.read_bytes())},
        "remote": {
            "job_id": remote_record["run_id"],
            "simulator": remote_record["simulator"],
            "results_validated": remote_record["results_validated"],
            "project_closed": remote_record["project_closed"],
            "energy_criterion_met": remote_record["energy_criterion_met"],
            "solver_state": remote_record["solver_run_info"]["state"],
            "solver_seconds": remote_record["solve_seconds"],
            "total_seconds": remote_record["total_seconds"],
            "gpu": remote_record["gpu"],
            "host_package_artifact_count": len(package_manifest["artifacts"]),
            "cst_verified_artifact_count": len(remote_verified["files"]),
        },
        "baseline": {
            "run_id": baseline_record["run_id"],
            "simulator": baseline_record["simulator"],
            "solver_seconds": baseline_record["solve_seconds"],
            "total_seconds": baseline_record["total_seconds"],
            "gpu": baseline_record["gpu"],
        },
        "identity_checks": {
            "frequency_axis_exact": True,
            "frequency_samples": len(remote_frequency),
            "frequency_range_GHz": [float(remote_frequency[0]), float(remote_frequency[-1])],
            "mesh_cells_remote": remote_record["mesh_cells"],
            "mesh_cells_baseline": baseline_record["mesh_cells"],
            "mesh_grid_sha256_remote": sha256(remote_mesh),
            "mesh_grid_sha256_baseline": sha256(baseline_mesh),
            "mesh_grid_exact": remote_mesh == baseline_mesh,
            "lesion_volume_mm3_remote": remote_record["cst_lesion_geometry_validation"]["cst_lesion_volume_mm3"],
            "lesion_volume_mm3_baseline": baseline_record["cst_lesion_geometry_validation"]["cst_lesion_volume_mm3"],
            "geometry_vba_equal_after_expected_runner_lines_removed": canonical_vba(remote_vba) == canonical_vba(baseline_vba),
        },
        "numerical_comparison": {
            "all_four_S_complex_rms": rms(all_difference),
            "all_four_S_complex_rms_percent_of_baseline": 100 * rms(all_difference) / rms(baseline_all),
            "max_absolute_complex_difference": float(np.max(np.abs(all_difference))),
            "per_curve": curve_differences,
        },
        "runtime_comparison": {
            "remote_to_baseline_solver_ratio": remote_record["solve_seconds"] / baseline_record["solve_seconds"],
            "estimated_combined_throughput_factor_vs_baseline_machine_for_this_case": 1 + baseline_record["solve_seconds"] / remote_record["solve_seconds"],
            "caveat": "One CST 2025 CPU run versus one CST 2026 GPU run; not a general hardware benchmark.",
        },
    }
    if args.paired_contrast_archive:
        _, contrast_curves = load_curves((args.paired_contrast_archive.resolve(strict=True) / "sparameters.csv.gz").read_bytes())
        paired = np.concatenate([contrast_curves[name] - baseline_curves[name] for name in CURVES])
        paired_s21 = rms(contrast_curves["S2,1"] - baseline_curves["S2,1"])
        report["local_paired_contrast_context"] = {
            "all_four_S_complex_rms": rms(paired),
            "S21_complex_rms": paired_s21,
            "remote_baseline_shift_to_local_paired_effect_ratio_all_four_S": rms(all_difference) / rms(paired),
            "remote_baseline_shift_to_local_paired_effect_ratio_S21": curve_differences["S2,1"]["complex_rms"] / paired_s21,
            "interpretation": "Absolute cross-node baselines cannot be pooled; compare matched control-contrast effects within each node.",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
