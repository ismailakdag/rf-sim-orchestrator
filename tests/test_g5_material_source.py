from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "pilot/school-g5-material-cst2025-v4/source"


class G5MaterialSourceTest(unittest.TestCase):
    def test_manifest_and_catalog_are_frozen(self):
        manifest = json.loads((SOURCE / "source-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_version"], "fr4-g5-material-cst2025-v4")
        for name, expected in manifest["sha256"].items():
            path = SOURCE / name
            self.assertTrue(path.is_file())
            data = path.read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), expected)
            if path.suffix.lower() in {".py", ".json", ".md", ".txt"}:
                self.assertEqual(data, data.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
        catalog = json.loads((SOURCE / "case-catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(len(catalog["cases"]), 42)
        self.assertTrue(all(case["legacy_job"]["gpu"] is False for case in catalog["cases"].values()))
        self.assertTrue(all(case["legacy_job"]["timeout_seconds"] == 2700 for case in catalog["cases"].values()))

    def test_cst2025_sampling_is_compatible(self):
        previous = os.environ.get("CST_EXPECTED_VERSION")
        previous_libraries = os.environ.get("CST_PYTHON_LIBRARIES")
        try:
            os.environ["CST_EXPECTED_VERSION"] = "2025"
            os.environ["CST_PYTHON_LIBRARIES"] = str(SOURCE)
            spec = importlib.util.spec_from_file_location("g5_material_night_case", SOURCE / "night_case.py")
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            vba = module.solver_history({"mesh": 32, "gpu": False})
            self.assertNotIn("FrequencySampleRuleLin", vba)
            self.assertIn('.FrequencySamples "4001"', vba)
        finally:
            if previous is None:
                os.environ.pop("CST_EXPECTED_VERSION", None)
            else:
                os.environ["CST_EXPECTED_VERSION"] = previous
            if previous_libraries is None:
                os.environ.pop("CST_PYTHON_LIBRARIES", None)
            else:
                os.environ["CST_PYTHON_LIBRARIES"] = previous_libraries


if __name__ == "__main__":
    unittest.main()
