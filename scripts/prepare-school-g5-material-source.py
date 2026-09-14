"""Build the frozen CST 2025 G5 material-sensitivity catalog.

The generated source is committed with this repository.  The input study root
is explicit so the source provenance can be regenerated from the research
workspace without giving the remote worker access to arbitrary parameters.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
from pathlib import Path

MATERIALS = (
    ("control", 10.0, 0.10, "same_material_insert"),
    ("E12-S010", 12.0, 0.10, "synthetic_local_contrast"),
    ("E16-S018", 16.0, 0.18, "synthetic_local_contrast"),
    ("E20-S010", 20.0, 0.10, "synthetic_local_contrast"),
    ("E10-S018", 10.0, 0.18, "synthetic_local_contrast"),
    ("E10-S035", 10.0, 0.35, "synthetic_local_contrast"),
    ("E20-S035", 20.0, 0.35, "synthetic_local_contrast"),
)


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("pilot/school-g5-material-cst2025-v1/source"))
    args = parser.parse_args()
    study = args.study_root.resolve(strict=True)
    source = study / "research/tooth-sensor/fr4-g5-fixtured-loop-v2"
    campaign = study / "research/tooth-sensor/campaigns/20260914-fr4-g5-differential-size-v1"
    original_manifest = read(source / "source-manifest.json")
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError(f"Output already exists: {output}")
    output.mkdir(parents=True)
    for name, expected in original_manifest["sha256"].items():
        source_file = source / name
        if digest(source_file) != expected:
            raise RuntimeError(f"Frozen input hash mismatch: {name}")
        shutil.copy2(source_file, output / name)

    night_case = output / "night_case.py"
    text = night_case.read_text(encoding="utf-8")
    text = text.replace("import json\n", "import json\nimport os\n", 1)
    marker = "    return f'''Solver.FrequencyRange \"1\", \"6\""
    replacement = "    sample_rule = \"\" if os.environ.get(\"CST_EXPECTED_VERSION\") == \"2025\" else ' .FrequencySampleRuleLin \"Samples\"\\n'\n    return f'''Solver.FrequencyRange \"1\", \"6\""
    if marker not in text:
        raise RuntimeError("CST solver history marker not found")
    text = text.replace(marker, replacement, 1)
    if ' .FrequencySampleRuleLin "Samples"\n .FrequencySamples "4001"' not in text:
        raise RuntimeError("Original CST sampling block not found")
    text = text.replace(' .FrequencySampleRuleLin "Samples"\n .FrequencySamples "4001"', '{sample_rule.rstrip()}\n .FrequencySamples "4001"', 1)
    night_case.write_text(text, encoding="utf-8", newline="\n")

    anchors = {}
    for path in sorted(campaign.glob("d*.json")):
        if len(path.stem) != 6 or not path.stem[1:].isdigit():
            continue
        job = read(path)
        p = job["params"]
        morphology = job["morphology"]["id"]
        if morphology not in {"M01_nominal", "M05_compact_root"}:
            continue
        if p["lesion_radius_mm"] != 0.5 or p["tooth_offset_x_mm"] != -0.5:
            continue
        anchors.setdefault((morphology, p["inclusion_site"]), {})[job["role"]] = job
    if len(anchors) != 6 or any(len(pair) != 2 for pair in anchors.values()):
        raise RuntimeError("Expected six complete source anchor pairs")

    cases = {}
    for (morphology, site), pair in sorted(anchors.items()):
        for code, epsilon_r, sigma, role in MATERIALS:
            base = pair["same_material_insert"] if role == "same_material_insert" else pair["synthetic_local_contrast"]
            legacy = copy.deepcopy(base)
            for field in ("id", "deadline_utc", "archive", "work", "paired_reference_archive", "paired_reference_id"):
                legacy.pop(field, None)
            legacy.update(
                phase="g5_material_contrast_response_surface",
                role=role,
                gpu=False,
                capture_view=False,
                timeout_seconds=1200,
            )
            legacy["params"]["lesion_epsilon_r"] = epsilon_r
            legacy["params"]["lesion_sigma_S_per_m"] = sigma
            legacy["material_probe"] = {"code": code, "epsilon_r": epsilon_r, "sigma_S_per_m": sigma}
            case_id = f"g5-{morphology}-{site}-{code}".replace("_", "-")
            cases[case_id] = {
                "description": f"Frozen G5 {morphology} {site} material point {code} on CST 2025 CPU.",
                "derived_from_job": f"20260914-fr4-g5-differential-size-v1/{base['id']}",
                "legacy_job": legacy,
            }
    write(output / "case-catalog.json", {"schema_version": 1, "study_id": "g5-material-sensitivity-v1", "cases": cases})
    hashes = {path.name: digest(path) for path in sorted(output.iterdir()) if path.is_file() and path.name != "source-manifest.json"}
    write(output / "source-manifest.json", {
        "source_version": "fr4-g5-material-cst2025-v1",
        "derived_from": {"source_version": original_manifest["source_version"], "manifest_sha256": digest(source / "source-manifest.json")},
        "cst2025_change": "Omit unsupported FrequencySampleRuleLin while retaining 4001 samples; force CPU in pinned cases.",
        "sha256": hashes,
    })
    print(json.dumps({"output": str(output), "cases": len(cases), "source_manifest_sha256": digest(output / "source-manifest.json")}))


if __name__ == "__main__":
    main()
