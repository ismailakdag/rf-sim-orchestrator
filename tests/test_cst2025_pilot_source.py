from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "pilot" / "school-widefield-cst2025-v3" / "source"


class Cst2025PilotSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        previous = os.environ.get("CST_PYTHON_LIBRARIES")
        previous_bytecode = sys.dont_write_bytecode
        os.environ["CST_PYTHON_LIBRARIES"] = str(SOURCE)
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location("cst2025_pilot_night_case", SOURCE / "night_case.py")
            cls.module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(cls.module)
        finally:
            sys.dont_write_bytecode = previous_bytecode
            if previous is None:
                os.environ.pop("CST_PYTHON_LIBRARIES", None)
            else:
                os.environ["CST_PYTHON_LIBRARIES"] = previous

    def test_cst2025_omits_only_unsupported_sampling_rule(self):
        previous = os.environ.get("CST_EXPECTED_VERSION")
        try:
            os.environ["CST_EXPECTED_VERSION"] = "2025"
            vba = self.module.solver_history({"mesh": 32, "gpu": False})
            self.assertNotIn("FrequencySampleRuleLin", vba)
            self.assertIn('.FrequencySamples "4001"', vba)
            self.assertIn('.Set "StepsPerWaveNear", "32"', vba)
            os.environ["CST_EXPECTED_VERSION"] = "2026"
            self.assertIn('.FrequencySampleRuleLin "Samples"', self.module.solver_history({"mesh": 32, "gpu": False}))
        finally:
            if previous is None:
                os.environ.pop("CST_EXPECTED_VERSION", None)
            else:
                os.environ["CST_EXPECTED_VERSION"] = previous

    def test_manifest_has_only_reproducible_sources(self):
        manifest_path = SOURCE / "source-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_version"], "fr4-widefield-txrx-cst2025-pilot-v3")
        self.assertNotEqual(manifest["sha256"], {})
        for name, expected in manifest["sha256"].items():
            self.assertNotIn("__pycache__", Path(name).parts)
            self.assertNotIn(Path(name).suffix.lower(), {".pyc", ".pyo"})
            self.assertEqual(hashlib.sha256((SOURCE / name).read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
