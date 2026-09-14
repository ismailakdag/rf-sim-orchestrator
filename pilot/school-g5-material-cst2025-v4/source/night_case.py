"""Run one isolated CST case for the sequential tooth-sensor campaign.

The worker preserves every attempt. A run is reusable only when it exits zero,
``record.json`` says ``results_validated: true``, and ``verified.json`` exists.
It never deletes or overwrites an earlier run and never retries an ID.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
from typing import Any

import numpy as np


CST_LIBS = os.environ.get("CST_PYTHON_LIBRARIES")
if not CST_LIBS:
    raise RuntimeError("CST_PYTHON_LIBRARIES must select the locally approved CST Python API")
EXPECTED_S_PARAMETERS = ("S1,1", "S1,2", "S2,1", "S2,2")
REQUESTED_BAND_GHZ = (1.0, 6.0)
MIN_SAMPLES = 101
PASSIVITY_TOLERANCE = 0.01
BUILD_CALL_TIMEOUT_SECONDS = 120
START_CALL_TIMEOUT_SECONDS = 120
STATUS_CALL_TIMEOUT_SECONDS = 30
ABORT_CALL_TIMEOUT_SECONDS = 60


class ValidationError(RuntimeError):
    """The solver may have returned, but retained results failed validation."""


class DeadlineError(RuntimeError):
    """The absolute campaign deadline was reached."""


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path | str, obj: Any) -> None:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(6):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.1 * (2**attempt))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _exclusive_bytes(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)


def _deadline(job: dict[str, Any]) -> datetime:
    raw = job.get("deadline_utc")
    if not raw:
        raise ValidationError("job.deadline_utc is required")
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"Invalid absolute deadline_utc: {raw!r}") from exc
    if value.tzinfo is None:
        raise ValidationError("deadline_utc must contain a UTC offset")
    return value.astimezone(timezone.utc)


def ensure_before_deadline(deadline: datetime, stage: str) -> None:
    if datetime.now(timezone.utc) >= deadline:
        raise DeadlineError(f"Absolute deadline reached during {stage}")


def log_event(
    archive: Path,
    run_id: str,
    stage: str,
    event: str,
    **details: Any,
) -> None:
    entry = {"utc": utc(), "run_id": run_id, "stage": stage, "event": event, **details}
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    print(line, flush=True)
    with (archive / "lifecycle.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _message_texts(messages: Any) -> list[str]:
    if not isinstance(messages, (list, tuple)):
        return [str(messages)] if messages else []
    texts = []
    for message in messages:
        if isinstance(message, dict):
            texts.append(str(message.get("text", message)))
        else:
            texts.append(str(message))
    return texts


def gpu_observation(requested: bool, messages: Any) -> dict[str, Any]:
    """Conservatively separate requested acceleration from observed use."""
    evidence = [
        text
        for text in _message_texts(messages)
        if "gpu" in text.lower() or "hardware acceleration" in text.lower()
    ]
    result: dict[str, Any] = {
        "requested": bool(requested),
        "observed": False if not requested else None,
        "fallback_reason": "disabled_by_job" if not requested else None,
        "evidence_messages": evidence,
    }
    if not requested:
        return result

    negative_phrases = (
        "gpu acceleration is disabled",
        "gpu acceleration disabled",
        "hardware acceleration is disabled",
        "hardware acceleration disabled",
        "gpu is not available",
        "gpu not available",
        "no compatible gpu",
        "cuda gpu solver not used",
        "falling back",
        "fallback to cpu",
    )
    positive_phrases = (
        "cuda gpu solver used",
        "gpu acceleration is enabled",
        "gpu acceleration enabled",
        "hardware acceleration is enabled",
        "hardware acceleration enabled",
        "calculation is performed on gpu",
        "solver is running on gpu",
    )
    for text in evidence:
        lower = text.lower()
        if any(phrase in lower for phrase in negative_phrases):
            result["observed"] = False
            result["fallback_reason"] = text
            return result
    for text in evidence:
        lower = text.lower()
        if any(phrase in lower for phrase in positive_phrases):
            result["observed"] = True
            return result
    return result


def set_gpu_record(record: dict[str, Any], requested: bool, messages: Any) -> None:
    """Store structured GPU evidence plus flat fields used by the controller."""
    observation = gpu_observation(requested, messages)
    record["gpu"] = observation
    record["gpu_requested"] = observation["requested"]
    record["gpu_observed"] = observation["observed"]
    record["gpu_fallback_reason"] = observation["fallback_reason"]
    record["gpu_messages"] = observation["evidence_messages"]


def validate_sparameters(
    frequency_ghz: Any,
    curves: dict[str, Any],
    requested_band_ghz: tuple[float, float] = REQUESTED_BAND_GHZ,
) -> dict[str, Any]:
    """Validate a complete two-port S matrix and calculate quality metrics."""
    missing = sorted(set(EXPECTED_S_PARAMETERS) - set(curves))
    if missing:
        raise ValidationError(f"Missing two-port S-parameters: {missing}")

    frequency = np.asarray(frequency_ghz, dtype=float)
    if frequency.ndim != 1 or len(frequency) < MIN_SAMPLES:
        raise ValidationError(f"Need at least {MIN_SAMPLES} one-dimensional samples")
    if not np.isfinite(frequency).all():
        raise ValidationError("Frequency axis contains non-finite values")
    steps = np.diff(frequency)
    if not np.all(steps > 0):
        raise ValidationError("Frequency axis must be strictly increasing")
    spacing = float(np.median(steps))
    band_tolerance = max(spacing * 0.51, 1e-9)
    if frequency[0] > requested_band_ghz[0] + band_tolerance:
        raise ValidationError(
            f"Frequency axis starts at {frequency[0]} GHz, above requested band"
        )
    if frequency[-1] < requested_band_ghz[1] - band_tolerance:
        raise ValidationError(
            f"Frequency axis ends at {frequency[-1]} GHz, below requested band"
        )

    arrays: dict[str, np.ndarray] = {}
    for name in EXPECTED_S_PARAMETERS:
        array = np.asarray(curves[name], dtype=complex)
        if array.ndim != 1 or len(array) != len(frequency):
            raise ValidationError(
                f"{name} shape {array.shape} does not match {len(frequency)} frequencies"
            )
        if not np.isfinite(array.real).all() or not np.isfinite(array.imag).all():
            raise ValidationError(f"{name} contains non-finite complex values")
        arrays[name] = array

    matrix = np.empty((len(frequency), 2, 2), dtype=complex)
    matrix[:, 0, 0] = arrays["S1,1"]
    matrix[:, 0, 1] = arrays["S1,2"]
    matrix[:, 1, 0] = arrays["S2,1"]
    matrix[:, 1, 1] = arrays["S2,2"]
    singular_values = np.linalg.svd(matrix, compute_uv=False)
    max_spectral_norm = float(np.max(singular_values[:, 0]))
    column_power_1 = np.abs(arrays["S1,1"]) ** 2 + np.abs(arrays["S2,1"]) ** 2
    column_power_2 = np.abs(arrays["S1,2"]) ** 2 + np.abs(arrays["S2,2"]) ** 2

    out: dict[str, Any] = {
        "finite": True,
        "complete_two_port_matrix": True,
        "samples": len(frequency),
        "fmin_GHz": float(frequency[0]),
        "fmax_GHz": float(frequency[-1]),
        "frequency_spacing_MHz": spacing * 1000.0,
        "frequency_strictly_increasing": True,
        "requested_band_GHz": list(requested_band_ghz),
        "band_coverage_ok": True,
        "max_abs_S": float(max(np.max(np.abs(a)) for a in arrays.values())),
        "max_power_sum_port1": float(np.max(column_power_1)),
        "max_power_sum_port2": float(np.max(column_power_2)),
        "max_spectral_norm": max_spectral_norm,
        "passivity_method": "largest singular value of complex 2x2 S matrix",
        "passivity_tolerance": PASSIVITY_TOLERANCE,
        "passivity_ok": max_spectral_norm <= 1.0 + PASSIVITY_TOLERANCE,
        "max_reciprocity_error": float(
            np.max(np.abs(arrays["S2,1"] - arrays["S1,2"]))
        ),
    }

    db = 20.0 * np.log10(np.maximum(np.abs(arrays["S2,1"]), 1e-15))
    out["S21_median_dB"] = float(np.median(db))
    candidates = []
    for index in range(1, len(frequency) - 1):
        if not 1.35 < float(frequency[index]) < 5.45:
            continue
        if not (db[index] < db[index - 1] and db[index] <= db[index + 1]):
            continue
        left = np.flatnonzero(
            (frequency >= frequency[index] - 0.5) & (frequency < frequency[index])
        )
        right = np.flatnonzero(
            (frequency > frequency[index]) & (frequency <= frequency[index] + 0.5)
        )
        if not len(left) or not len(right):
            continue
        prominence = float(min(db[left].max(), db[right].max()) - db[index])
        if prominence < 0.3:
            continue
        threshold = float(db[index] + 3.0)
        lo = next(
            (candidate for candidate in range(index - 1, -1, -1) if db[candidate] >= threshold),
            None,
        )
        hi = next(
            (
                candidate
                for candidate in range(index + 1, len(frequency))
                if db[candidate] >= threshold
            ),
            None,
        )
        width = float(frequency[hi] - frequency[lo]) if lo is not None and hi is not None else None
        candidates.append(
            {
                "frequency_GHz": float(frequency[index]),
                "S21_dB": float(db[index]),
                "prominence_dB": prominence,
                "notch_3dB_width_GHz": width,
                "linewidth_Q_proxy": float(frequency[index] / width) if width else None,
            }
        )
    candidates.sort(key=lambda item: item["prominence_dB"], reverse=True)
    out["notches"] = candidates[:6]
    out["selected_notch"] = candidates[0] if candidates else None
    out["notch_tracking_note"] = (
        "Spectral proxy only; not field-verified mode identity or diagnostic sensitivity."
    )
    return out


def metrics(frequency_ghz: Any, curves: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper used by offline checks and campaign analysis."""
    return validate_sparameters(frequency_ghz, curves)


def write_sparameter_csv(
    path: Path, frequency: np.ndarray, curves: dict[str, np.ndarray]
) -> None:
    if path.exists():
        raise ValidationError(f"Refusing to overwrite result export: {path}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    header = ["frequency_GHz"] + [
        f"{name}_{part}"
        for name in EXPECTED_S_PARAMETERS
        for part in ("real", "imag")
    ]
    try:
        with gzip.open(tmp, "wt", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            for index, value in enumerate(frequency):
                row: list[float] = [float(value)]
                for name in EXPECTED_S_PARAMETERS:
                    row.extend((float(curves[name][index].real), float(curves[name][index].imag)))
                writer.writerow(row)
        tmp.replace(path)
    except Exception as exc:
        raise ValidationError(f"Complex S-parameter CSV export failed: {exc}") from exc


def validate_sparameter_csv(path: Path) -> dict[str, Any]:
    expected_header = ["frequency_GHz"] + [
        f"{name}_{part}"
        for name in EXPECTED_S_PARAMETERS
        for part in ("real", "imag")
    ]
    try:
        with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            if header != expected_header:
                raise ValidationError(f"Unexpected S-parameter CSV header: {header}")
            numeric_rows = []
            for row_number, row in enumerate(reader, start=2):
                if len(row) != len(expected_header):
                    raise ValidationError(
                        f"CSV row {row_number} has {len(row)} columns; expected {len(expected_header)}"
                    )
                values = [float(value) for value in row]
                if not np.isfinite(values).all():
                    raise ValidationError(f"CSV row {row_number} contains non-finite values")
                numeric_rows.append(values)
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(f"Complex S-parameter CSV validation failed: {exc}") from exc
    if not numeric_rows:
        raise ValidationError("Complex S-parameter CSV has no data rows")

    data = np.asarray(numeric_rows, dtype=float)
    curves = {}
    column = 1
    for name in EXPECTED_S_PARAMETERS:
        curves[name] = data[:, column] + 1j * data[:, column + 1]
        column += 2
    result = validate_sparameters(data[:, 0], curves)
    result["csv_rows"] = len(data)
    result["csv_columns"] = len(expected_header)
    return result


def solver_history(config: dict[str, Any]) -> str:
    mesh = int(config.get("mesh", 24))
    gpu = bool(config.get("gpu", True))
    accuracy = int(config.get("accuracy_dB", -60))
    pulse_widths = int(config.get("number_of_pulse_widths", 100))
    if not 20 <= pulse_widths <= 200:
        raise ValidationError("number_of_pulse_widths must be in [20, 200]")
    sample_rule = "" if os.environ.get("CST_EXPECTED_VERSION") == "2025" else ' .FrequencySampleRuleLin "Samples"\n'
    return f'''Solver.FrequencyRange "1", "6"
ChangeSolverType "HF Time Domain"
With Boundary
 .Xmin "expanded open"
 .Xmax "expanded open"
 .Ymin "expanded open"
 .Ymax "expanded open"
 .Zmin "expanded open"
 .Zmax "expanded open"
End With
With MeshSettings
 .SetMeshType "Hex"
 .Set "StepsPerWaveNear", "{mesh}"
 .Set "WavelengthRefinementSameAsNear", "1"
 .Set "StepsPerBoxNear", "15"
 .Set "GeometryRefinementSameAsNear", "1"
 .Set "UseDielectrics", "0"
End With
With Solver
 .StimulationPort "All"
 .StimulationMode "All"
 .SteadyStateLimit "{accuracy}"
 .NumberOfPulseWidths "{pulse_widths}"
 .HardwareAcceleration "{str(gpu)}"
 .MaximumNumberOfThreads "12"
{sample_rule.rstrip()}
 .FrequencySamples "4001"
End With'''


def geometry_metadata(job: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    geometry_keys = (
        "topology", "include_resonators", "include_tooth", "ring_scale",
        "coupling_gap_mm", "sensing_gap_mm", "ring_trace_mm", "secondary_scale",
        "secondary_offset_x_mm", "tooth_scale", "tooth_offset_x_mm",
        "tooth_offset_y_mm", "airgap_mm", "board_x_mm", "board_y_mm",
        "substrate_h_mm", "substrate_epsilon_r", "substrate_tand",
        "copper_t_mm", "feed_width_mm",
    )
    payload = {key: params.get(key) for key in geometry_keys}
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    group_id = job.get("group_id")
    return {
        "study_id": job.get("study_id", "tooth-sensor"),
        "run_id": job.get("id"),
        "case_id": job.get("case_id", job.get("id")),
        "geometry_id": job.get("geometry_id", f"geom-{fingerprint}"),
        "pair_id": job.get("pair_id", group_id),
        "group_id": group_id,
        "specimen_id": job.get("specimen_id", "analytic-tooth-phantom"),
        "scenario_id": job.get("scenario_id", group_id),
        "phase": job.get("phase"),
        "role": job.get("role"),
        "evidence_domain": job.get("evidence_domain", "synthetic"),
    }


def _mark_failure(
    record: dict[str, Any], exc: BaseException, stage: str, validation: bool
) -> None:
    record["status"] = "validation_failed" if validation else "technical_failed"
    record["run_status"] = "validation_failure" if validation else "technical_failure"
    record["results_validated"] = False
    record["failure_stage"] = stage
    record["failure_reason"] = repr(exc)
    record["error"] = repr(exc)
    record["traceback"] = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )


def _legacy_summary(archive: Path, record: dict[str, Any]) -> None:
    job = record["job"]
    notch = record.get("metrics", {}).get("selected_notch") or {}
    links = []
    for label, filename in (
        ("Tam kayıt", "record.json"),
        ("Model VBA", "model.vba"),
        ("Karmaşık S matrisi", "sparameters.csv.gz"),
        ("Mesh hücre sayısı", "mesh-cells.txt"),
        ("Model görünümü", "model.png"),
        ("Yaşam döngüsü günlüğü", "lifecycle.jsonl"),
        ("Koşucu kaynak görüntüsü", "night_case.py"),
        ("Geometri kaynak görüntüsü", "overnight_geometry.py"),
    ):
        if (archive / filename).exists():
            links.append(f"[{label}]({filename})")
    gpu = record.get("gpu", {})
    text = [
        "---", "type: simulation-run",
        f'date: {record.get("finished_utc", utc())[:10]}',
        f'status: {record.get("status", "unknown")}', "---",
        f'# {job.get("id", "unknown")} â€” {job.get("role", "unspecified")}', "",
        f'Grup: `{job.get("group_id")}`; çift: `{record.get("pair_id")}`; geometri: `{record.get("geometry_id")}`.',
        f'Aşama: `{job.get("phase")}`; toplam süre: {record.get("total_seconds", 0):.1f} s.',
        f'Mesh: {job.get("mesh")}; gerçek hücre: {record.get("mesh_cells", "bilinmiyor")}.',
        f'GPU talebi: {gpu.get("requested")}; gözlenen kullanım: {gpu.get("observed")}; fallback nedeni: {gpu.get("fallback_reason")}.',
        f'Solver enerji kriteri: {record.get("energy_criterion_met", False)}; spektral-norm pasifliği: {record.get("metrics", {}).get("passivity_ok", "bilinmiyor")}.',
        f'Sonuç doğrulandı: {record.get("results_validated", False)}; proje kapandı: {record.get("project_closed")}.',
        f'S21 çukuru (yalnız spektral gösterge): {notch.get("frequency_GHz", "yok")} GHz, {notch.get("S21_dB", "yok")} dB.', "",
        "**Sınır:** Analitik geometri ve sentetik malzemeler. Klinik sağlıklı/çürük etiketi yok; bu tek koşu yakınsama kanıtı değildir.", "",
        "## Parametreler", "", "| Parametre | Değer |", "| --- | --- |",
    ]
    text.extend(
        f"| {key} | {value} |"
        for key, value in record.get("effective_params", job.get("params", {})).items()
    )
    text.extend([
        "", "## Veri", "", " · ".join(links) if links else "Henüz kalıcı çıktı yok.", "",
        f'Başarısızlık aşaması: {record.get("failure_stage", "Yok")}',
        f'Hata: {record.get("failure_reason", "Yok")}',
        f'GPU kanıt mesajları: {gpu.get("evidence_messages", [])}',
    ])
    (archive / "summary.md").write_text("\n".join(text) + "\n", encoding="utf-8")


def write_verified_manifest(archive: Path, record: dict[str, Any]) -> None:
    required = (
        "sparameters.csv.gz", "model.vba", "record.json", "summary.md",
        "night_case.py", "overnight_geometry.py", "base_geometry.py",
    )
    required = required + ("cst_lesion_check.py", "cst-lesion-check.txt", "cst-lesion-check.vba", "cst-lesion-check.json")
    required = required + tuple(name for name in ("mesh-grid.bin", "model.png") if (archive / name).is_file())
    missing = [name for name in required if not (archive / name).is_file()]
    if missing:
        raise ValidationError(f"Required retained artifacts are missing: {missing}")
    files = {
        name: {"bytes": (archive / name).stat().st_size, "sha256": sha256_file(archive / name)}
        for name in required
    }
    if any(info["bytes"] <= 0 for info in files.values()):
        raise ValidationError("A required retained artifact is empty")
    for name, info in files.items():
        if sha256_file(archive / name) != info["sha256"]:
            raise ValidationError(f"Artifact changed during hash validation: {name}")
    payload = {
        "schema_version": 2, "run_id": record["run_id"], "job_id": record["run_id"],
        "geometry_id": record["geometry_id"], "pair_id": record.get("pair_id"),
        "results_validated": True, "samples": record["metrics"]["samples"],
        "expected_s_parameters": list(EXPECTED_S_PARAMETERS), "files": files,
        "files_sha256": {name: info["sha256"] for name, info in files.items()},
        "log_path": "lifecycle.jsonl", "verified_utc": utc(),
    }
    verified_path = archive / "verified.json"
    if verified_path.exists():
        raise ValidationError("Refusing to overwrite verified.json")
    write_json(verified_path, payload)
    parsed = json.loads(verified_path.read_text(encoding="utf-8"))
    if parsed != payload:
        raise ValidationError("verified.json read-back differs from written manifest")
    for name, info in parsed["files"].items():
        if sha256_file(archive / name) != info["sha256"]:
            raise ValidationError(f"Post-write hash mismatch: {name}")


def _fresh_paths(job: dict[str, Any]) -> tuple[Path, Path]:
    archive = Path(job["archive"]).resolve()
    work = Path(job["work"]).resolve()
    archive.mkdir(parents=True, exist_ok=True)
    reserved = (
        "record.json", "verified.json", "night_case.py", "overnight_geometry.py", "base_geometry.py",
        "model.vba", "sparameters.csv.gz", "summary.md", "lifecycle.jsonl",
    )
    collisions = [name for name in reserved if (archive / name).exists()]
    if collisions:
        raise FileExistsError(
            f"Run archive already contains attempt data; use a new ID: {collisions}"
        )
    if work.exists() and any(work.iterdir()):
        raise FileExistsError(f"Run work directory is not empty; use a new ID: {work}")
    work.mkdir(parents=True, exist_ok=True)
    return archive, work


def abort_once(project, record, archive, reason):
    # An abort call may open a modal and time out. Never repeat or close blindly.
    if record.get("abort_requested"):
        raise RuntimeError("Abort already requested; manual recovery required")
    record.update(abort_requested=True, abort_reason=reason, abort_confirmed=False,
                  manual_recovery_required=True)
    write_json(archive / "record.json", record)
    log_event(archive, record["run_id"], "solver_abort", "start", reason=reason)
    project.model3d.abort_solver(timeout=ABORT_CALL_TIMEOUT_SECONDS)
    if project.model3d.is_solver_running(timeout=STATUS_CALL_TIMEOUT_SECONDS):
        raise RuntimeError("Solver remains active after abort request")
    record.update(abort_confirmed=True, manual_recovery_required=False)
    write_json(archive / "record.json", record)


def write_summary(archive: Path, record: dict[str, Any]) -> None:
    from datetime import timedelta
    def local(value):
        return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M:%S Türkiye') if value else 'Henüz kaydedilmedi'
    lines = [f'# {record["run_id"]} — FR-4 ilk pilot', '',
             f'Durum: {record["status"]}.',
             f'Solver başlangıç isteği: {local(record.get("solver_start_requested_utc"))}.',
             f'Solver bitişinin algılandığı saat: {local(record.get("solver_finished_detected_utc"))}.',
             f'Solver süresi: {record.get("solve_seconds", "Henüz bilinmiyor")} saniye.',
             f'Toplam iş süresi: {record.get("total_seconds", "Henüz bilinmiyor")} saniye.',
             f'Sonuç doğrulandı: {record.get("results_validated", False)}. Proje kapandı: {record.get("project_closed", False)}.',
             f'Hata: {record.get("failure_reason")}.', '',
             'Sentetik kontrast ve kaba ağ keşfi; sağlıklı/çürük klinik etiketleri doğrulanmış değildir.', '',
             '[Tam kayıt](record.json) · [Zaman kaydı](timing.json) · [Model](model.vba) · [Kompleks S](sparameters.csv.gz)']
    (archive / 'summary.md').write_text('\n\n'.join(lines)+'\n', encoding='utf-8')


def run(job_path: Path) -> int:
    job = json.loads(job_path.read_text(encoding="utf-8"))
    archive, work = _fresh_paths(job)
    source = Path(__file__).parent
    snapshots = {}
    for filename in ("night_case.py", "overnight_geometry.py", "base_geometry.py", "cst_lesion_check.py"):
        data = (source / filename).read_bytes()
        snapshots[filename] = hashlib.sha256(data).hexdigest()
        _exclusive_bytes(archive / filename, data)

    run_id = str(job.get("id", "unknown"))
    record: dict[str, Any] = {
        "schema_version": 2, "job": job, "run_id": run_id, "started_utc": utc(),
        "status": "created", "run_status": "pending",
        "last_successful_stage": "source_snapshotted", "failure_stage": None,
        "failure_reason": None, "data_origin": "synthetic_EM",
        "diagnostic_label": None, "clinical_validity": False,
        "source_hashes": snapshots, "model_built": False, "solver_started": False,
        "solver_returned": False, "results_exported": False,
        "results_validated": False, "project_creation_started": False,
        "project_created": False, "no_project_created": True,
        "project_close_attempted": False, "project_closed": False,
        "call_timeouts_seconds": {
            "build_history": BUILD_CALL_TIMEOUT_SECONDS, "solver_start": START_CALL_TIMEOUT_SECONDS,
            "solver_status": STATUS_CALL_TIMEOUT_SECONDS, "solver_abort": ABORT_CALL_TIMEOUT_SECONDS,
            "connect": None,
            "connect_note": "CST local connect API exposes no per-call timeout; parent process supervises this worker.",
        },
        "gpu": {"requested": bool(job.get("gpu", True)), "observed": None,
                "fallback_reason": None, "evidence_messages": []},
        "gpu_requested": bool(job.get("gpu", True)), "gpu_observed": None,
        "gpu_fallback_reason": None, "gpu_messages": [],
    }
    write_json(archive / "record.json", record)
    with (work / ".owned-run.json").open("x", encoding="utf-8") as handle:
        json.dump({"job_id": run_id, "archive": str(archive)}, handle)

    start = time.monotonic()
    project = None
    current_stage = "job_validation"
    failure: BaseException | None = None
    validation_failure = False
    try:
        deadline = _deadline(job)
        ensure_before_deadline(deadline, current_stage)
        record["deadline_utc"] = deadline.isoformat()

        current_stage = "source_import"
        sys.path.insert(0, CST_LIBS)
        import cst.interface as ci
        import cst.results as cr
        from overnight_geometry import DEFAULTS, history, geometry_manifest

        params = dict(DEFAULTS)
        params.update(job.get("params", {}))
        record["effective_params"] = params
        record.update(geometry_metadata(job, params))
        record["geometry_manifest"] = geometry_manifest(params)
        record["simulator"] = {"product": "CST Studio Suite", "version": os.environ.get("CST_EXPECTED_VERSION", "unreported"), "api": "official local Python interface"}
        record["source_version"] = "fr4-g5-material-cst2025-v4"
        record["material_provenance"] = job["material_provenance"]
        record["fixture"] = job["fixture"]
        record["mesh_convergence_status"] = "exploratory_unconverged"
        record["local_mesh_control"] = {"enabled": False, "step_mm": None,
            "note": "Common global32 exploratory mesh; no absolute local Step block"}
        record["mesh_control"] = {
            "material_based_refinement": False,
            "command": 'MeshSettings.Set "UseDielectrics", "0"',
            "evidence": "Selected local CST release shipped vba_snippets.py Hex template and Global Mesh Properties Refine help",
            "purpose": "Test equal axes across material pair; equality remains unverified until outputs compared",
        }
        pulse_widths = int(job.get("number_of_pulse_widths", 100))
        record["solver_settings"] = {
            "frequency_range_GHz": list(REQUESTED_BAND_GHZ),
            "number_of_pulse_widths": pulse_widths,
            "steady_state_limit_dB": int(job.get("accuracy_dB", -60)),
            "frequency_samples": 4001,
        }
        write_json(archive / "record.json", record)

        current_stage = "connect"
        ensure_before_deadline(deadline, current_stage)
        log_event(archive, run_id, current_stage, "start", per_call_timeout_seconds=None,
                  parent_supervision_required=True)
        call_start = time.monotonic()
        environment = ci.DesignEnvironment.connect_to_any_or_new()
        log_event(archive, run_id, current_stage, "done",
                  elapsed_seconds=round(time.monotonic() - call_start, 3))
        ensure_before_deadline(deadline, current_stage)

        current_stage = "existing_solver_check"
        log_event(archive, run_id, current_stage, "start")
        for existing in environment.get_open_projects():
            if existing.model3d.is_solver_running(timeout=STATUS_CALL_TIMEOUT_SECONDS):
                raise RuntimeError("Another solver is active; queue will not overlap it")
        log_event(archive, run_id, current_stage, "done")

        current_stage = "new_project"
        ensure_before_deadline(deadline, current_stage)
        # Once the API call starts, an exception cannot prove that CST did not
        # create a project on the server side. Persist the uncertainty first.
        record["project_creation_started"] = True
        record["no_project_created"] = False
        write_json(archive / "record.json", record)
        log_event(archive, run_id, current_stage, "start")
        call_start = time.monotonic()
        project = environment.new_mws()
        record["project_created"] = True
        write_json(archive / "record.json", record)
        log_event(archive, run_id, current_stage, "done",
                  elapsed_seconds=round(time.monotonic() - call_start, 3))
        ensure_before_deadline(deadline, current_stage)

        current_stage = "geometry_generation"
        blocks = history(params) + [("Night solver settings", solver_history(job))]
        if not params.get("include_lesion"):
            raise ValueError("Local mesh pilot requires explicit lesion geometry in both states")
        if job.get("field_monitor_GHz") is not None:
            monitor_frequency = float(job["field_monitor_GHz"])
            blocks.append(("E field monitor", f'''With Monitor
 .Reset
 .Name "E-field (f={monitor_frequency})"
 .Domain "Frequency"
 .FieldType "Efield"
 .Frequency "{monitor_frequency}"
 .Create
End With'''))
        model_vba = archive / "model.vba"
        with model_vba.open("x", encoding="utf-8") as handle:
            handle.write("\n\n".join(vba for _, vba in blocks))
        record["generated_vba_sha256"] = sha256_file(model_vba)

        current_stage = "model_build"
        for index, (label, vba) in enumerate(blocks, start=1):
            ensure_before_deadline(deadline, f"{current_stage}:{label}")
            log_event(archive, run_id, current_stage, "block_start", block_index=index,
                      block_count=len(blocks), label=label,
                      timeout_seconds=BUILD_CALL_TIMEOUT_SECONDS)
            block_start = time.monotonic()
            project.model3d.add_to_history(label, vba, timeout=BUILD_CALL_TIMEOUT_SECONDS)
            log_event(archive, run_id, current_stage, "block_done", block_index=index,
                      label=label, elapsed_seconds=round(time.monotonic() - block_start, 3))
        ensure_before_deadline(deadline, current_stage)
        project.save(str(work / "model.cst"))
        record.update({"model_built": True, "status": "model_built",
                       "last_successful_stage": "model_built",
                       "build_seconds": time.monotonic() - start})
        write_json(archive / "record.json", record)

        current_stage = "cst_lesion_geometry_validation"
        from cst_lesion_check import validate
        record["cst_lesion_geometry_validation"] = validate(project, archive, record["geometry_manifest"])
        write_json(archive / "record.json", record)

        current_stage = "solver_start"
        ensure_before_deadline(deadline, current_stage)
        log_event(archive, run_id, current_stage, "start",
                  timeout_seconds=START_CALL_TIMEOUT_SECONDS,
                  gpu_requested=bool(job.get("gpu", True)), pulse_widths=pulse_widths)
        solve_start = time.monotonic()
        record["solver_start_requested_utc"] = utc()
        record["timing_source"] = "Python UTC clock and monotonic elapsed time; completion detected by CST API polling every 3 seconds, plus API latency"
        write_json(archive / "timing.json", {"start_utc": record["solver_start_requested_utc"], "finish_utc": None, "source": record["timing_source"]})
        project.model3d.start_solver(timeout=START_CALL_TIMEOUT_SECONDS)
        record.update({"solver_started": True, "status": "solver_started",
                       "last_successful_stage": "solver_started"})
        write_json(archive / "record.json", record)
        log_event(archive, run_id, current_stage, "returned",
                  elapsed_seconds=round(time.monotonic() - solve_start, 3))

        current_stage = "solver_wait"
        while project.model3d.is_solver_running(timeout=STATUS_CALL_TIMEOUT_SECONDS):
            elapsed = time.monotonic() - solve_start
            write_json(work / "heartbeat.json", {
                "job_id": run_id, "state": "solving", "elapsed_seconds": round(elapsed, 1),
                "utc": utc(),
            })
            if elapsed > float(job.get("timeout_seconds", 1800)):
                abort_once(project, record, archive, "case_timeout")
                raise TimeoutError("Per-case solver time budget exceeded; solver aborted")
            if datetime.now(timezone.utc) >= deadline:
                abort_once(project, record, archive, "absolute_deadline")
                raise DeadlineError("Absolute deadline reached during solver_wait; solver aborted")
            time.sleep(3)
        ensure_before_deadline(deadline, "solver_return")
        record["solve_seconds"] = time.monotonic() - solve_start
        record["solver_finished_detected_utc"] = utc()
        write_json(archive / "timing.json", {"start_utc": record["solver_start_requested_utc"], "finish_utc": record["solver_finished_detected_utc"], "elapsed_seconds": record["solve_seconds"], "source": record["timing_source"]})
        record["solver_run_info"] = project.model3d.get_solver_run_info()
        record["messages"] = project.get_messages()
        set_gpu_record(record, bool(job.get("gpu", True)), record["messages"])
        if record["solver_run_info"].get("state") != "SUCCESS":
            raise RuntimeError("Solver did not finish successfully")
        record.update({"solver_returned": True, "status": "solver_returned",
                       "last_successful_stage": "solver_returned"})
        project.save()
        write_json(archive / "record.json", record)

        current_stage = "mesh_export"
        mesh_file = archive / "mesh-cells.txt"
        project.model3d._execute_vba_code(f'''Sub Main()
Open "{mesh_file.as_posix()}" For Output As #1
Print #1, Mesh.GetNumberOfMeshCells
Close #1
End Sub''')
        if not mesh_file.is_file():
            raise ValidationError("Mesh cell export did not create mesh-cells.txt")
        try:
            record["mesh_cells"] = int(mesh_file.read_text(encoding="utf-8").strip())
        except Exception as exc:
            raise ValidationError(f"Invalid mesh cell export: {exc}") from exc

        current_stage = "results_extraction"
        ensure_before_deadline(deadline, current_stage)
        results_project = cr.ProjectFile(str(work / "model.cst"), allow_interactive=True).get_3d()
        result_items: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        result_tree_items = []
        for tree in results_project.get_tree_items():
            if not tree.startswith("1D Results\\S-Parameters\\"):
                continue
            name = tree.rsplit("\\", 1)[-1]
            result_tree_items.append(tree)
            if name in result_items:
                raise ValidationError(f"Duplicate S-parameter result item: {name}")
            item = results_project.get_result_item(tree)
            result_items[name] = (
                np.asarray(item.get_xdata(), dtype=float),
                np.asarray(item.get_ydata(), dtype=complex),
            )
        missing = sorted(set(EXPECTED_S_PARAMETERS) - set(result_items))
        if missing:
            raise ValidationError(f"Missing result-tree curves: {missing}")
        frequency = result_items["S1,1"][0]
        curves = {}
        for name in EXPECTED_S_PARAMETERS:
            axis, values = result_items[name]
            if not np.array_equal(axis, frequency):
                raise ValidationError(f"Frequency axis mismatch for {name}")
            curves[name] = values
        record["result_tree_items"] = result_tree_items
        # Preserve the solver-produced mesh file as an opaque grid fingerprint.
        grid_path = work / "model" / "Result" / "PP.msh"
        if grid_path.is_file():
            _exclusive_bytes(archive / "mesh-grid.bin", grid_path.read_bytes())
            record["mesh_grid_fingerprint"] = {
                "file": "mesh-grid.bin", "sha256": sha256_file(archive / "mesh-grid.bin"),
                "source": "model/Result/PP.msh",
                "note": "Exact CST mesh-file equality can be checked across cases; cell count alone does not prove identical discretization.",
            }
        if job.get("capture_view"):
            project.activate()
            target = (archive / "model.png").as_posix()
            project.model3d._execute_vba_code('Sub Main()\nSelectTreeItem "Components"\nPlot.DrawWorkplane "False"\nPlot.DrawBox "False"\nPlot.ZoomToStructure\nPlot.Update\nPlot.ExportImage "'+target+'", 1400, 1000\nEnd Sub')


        current_stage = "results_validation"
        record["metrics"] = validate_sparameters(frequency, curves)
        record["energy_criterion_met"] = (
            "Steady state energy criterion met" in str(record.get("messages", ""))
        )

        current_stage = "results_export"
        output_csv = archive / "sparameters.csv.gz"
        write_sparameter_csv(output_csv, frequency, curves)
        record.update({"results_exported": True, "status": "results_exported",
                       "last_successful_stage": "results_exported"})
        record["output"] = {"sparameters": {
            "path": "sparameters.csv.gz",
            "format": "gzip CSV, complex real/imaginary columns",
            "frequency_unit": "GHz", "bytes": output_csv.stat().st_size,
            "sha256": sha256_file(output_csv),
        }}
        write_json(archive / "record.json", record)

        current_stage = "csv_validation"
        csv_metrics = validate_sparameter_csv(output_csv)
        if csv_metrics["samples"] != record["metrics"]["samples"]:
            raise ValidationError("CSV sample count differs from in-memory result count")
        if csv_metrics["max_spectral_norm"] != record["metrics"]["max_spectral_norm"]:
            raise ValidationError("CSV complex data differs from in-memory passivity result")
        record["csv_validation"] = {
            "rows": csv_metrics["csv_rows"], "columns": csv_metrics["csv_columns"],
            "passed": True,
        }
    except ValidationError as exc:
        failure, validation_failure = exc, True
    except Exception as exc:
        failure, validation_failure = exc, False
    finally:
        if project is not None and record.get("manual_recovery_required"):
            record["project_closed"] = False
            record["cleanup_skipped"] = "Abort outcome unknown; no repeated abort or close"
        elif project is not None:
            if "messages" not in record:
                try:
                    record["messages"] = project.get_messages()
                except Exception as exc:
                    record["message_read_error"] = repr(exc)
            set_gpu_record(
                record, bool(job.get("gpu", True)), record.get("messages", [])
            )
            record["project_close_attempted"] = True
            try:
                try:
                    running = project.model3d.is_solver_running(timeout=STATUS_CALL_TIMEOUT_SECONDS)
                except Exception as exc:
                    running = None
                    record["solver_state_check_error"] = repr(exc)
                if running:
                    abort_once(project, record, archive, "cleanup")
                    record["solver_aborted_during_cleanup"] = True
                project.close()
                record["project_closed"] = True
            except Exception as exc:
                record["project_closed"] = False
                record["close_error"] = repr(exc)
                if failure is None:
                    failure, current_stage, validation_failure = exc, "project_close", False

    record["total_seconds"] = time.monotonic() - start
    record["finished_utc"] = utc()
    write_json(archive / "timing.json", {
        "start_utc": record.get("solver_start_requested_utc"),
        "finish_utc": record.get("solver_finished_detected_utc"),
        "solver_elapsed_seconds": record.get("solve_seconds"),
        "worker_finished_utc": record["finished_utc"],
        "worker_total_seconds": record["total_seconds"],
        "failure": repr(failure) if failure else None,
        "cst_solver_run_info": record.get("solver_run_info"),
        "source": record.get("timing_source"),
    })
    if failure is not None:
        _mark_failure(record, failure, current_stage, validation_failure)
        write_json(archive / "record.json", record)
        try:
            write_summary(archive, record)
        except Exception as exc:
            record["summary_error"] = repr(exc)
            write_json(archive / "record.json", record)
        log_event(archive, run_id, current_stage, "failed", status=record["status"],
                  reason=record["failure_reason"])
        print(json.dumps({"id": run_id, "status": record["status"],
                          "failure_stage": current_stage,
                          "total_seconds": record["total_seconds"]}), flush=True)
        return 1

    try:
        current_stage = "artifact_hash_validation"
        if record.get("project_closed") is not True:
            raise ValidationError("Project closure was not conclusively successful")
        csv_metrics = validate_sparameter_csv(archive / "sparameters.csv.gz")
        if csv_metrics["samples"] != record["metrics"]["samples"]:
            raise ValidationError("Final CSV row count mismatch")
        record["results_validated"] = True
        quality_ok = bool(record["metrics"]["passivity_ok"]) and bool(
            record.get("energy_criterion_met")
        )
        record.update({
            "status": "completed" if quality_ok else "completed_quality_flag",
            "run_status": "solved" if quality_ok else "solved_quality_flag",
            "last_successful_stage": "results_validated",
            "quality_flags": {
                "passivity_ok": bool(record["metrics"]["passivity_ok"]),
                "energy_criterion_met": bool(record.get("energy_criterion_met")),
            },
            "verified_file": "verified.json",
        })
        write_json(archive / "record.json", record)
        write_summary(archive, record)
        write_verified_manifest(archive, record)
    except Exception as exc:
        _mark_failure(record, exc, current_stage, validation=True)
        write_json(archive / "record.json", record)
        try:
            write_summary(archive, record)
        except Exception as summary_exc:
            record["summary_error"] = repr(summary_exc)
            write_json(archive / "record.json", record)
        log_event(archive, run_id, current_stage, "failed", status=record["status"],
                  reason=record["failure_reason"])
        print(json.dumps({"id": run_id, "status": record["status"],
                          "failure_stage": current_stage,
                          "total_seconds": record["total_seconds"]}), flush=True)
        return 1

    print(json.dumps({"id": run_id, "status": record["status"],
                      "results_validated": True, "verified": True,
                      "total_seconds": record["total_seconds"]}), flush=True)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    args = parser.parse_args()
    try:
        exit_code = run(args.job)
    except Exception as exc:
        # Target-collision errors occur before a new record can safely be made.
        print(json.dumps({"status": "technical_failed_before_record", "error": repr(exc),
                          "traceback": traceback.format_exc()}), flush=True)
        exit_code = 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
