import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from rfsim.host import Store
from rfsim.worker import run_fixed_python
from test_orchestrator import job


class NoDeadlineTests(unittest.TestCase):
    def test_lease_and_heartbeat_without_campaign_deadline(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder), 60, {'mock-v1'}, 10000000, 20000000)
            document = job('no-deadline')
            document['deadline_utc'] = None
            store.submit(document)
            lease = store.lease('worker-a', ['mock-v1'])
            self.assertIsNone(lease['job']['deadline_utc'])
            result = store.heartbeat('no-deadline', 'worker-a', lease['lease_token'], 'solving')
            self.assertTrue(result['lease_expires_utc'])

    def test_fixed_runner_waits_for_exit_without_wall_clock_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'fixture.py'
            script.write_text('import time; time.sleep(0.05)\n')
            document = job('unlimited')
            document['parameters'] = {}
            document['deadline_utc'] = None
            config = dict(script=str(script), python=sys.executable,
                          script_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
                          source_sha256=document['source']['sha256'], arguments=[], timeout_seconds=0)
            run_fixed_python(document, root / 'run', config)
            evidence = json.loads((root / 'run/logs/external-process.json').read_text())
            self.assertIsNone(evidence['timeout_seconds'])
            self.assertEqual(evidence['return_code'], 0)

    def test_v5_preserves_geometry_and_removes_case_cutoff(self):
        root = Path(__file__).resolve().parents[1] / 'pilot'
        old = root / 'school-g5-material-cst2025-v4/source'
        new = root / 'school-g5-material-cst2025-v5/source'
        for name in ('base_geometry.py', 'overnight_geometry.py', 'cst_lesion_check.py'):
            self.assertEqual((old / name).read_bytes(), (new / name).read_bytes())
        manifest = json.loads((new / 'source-manifest.json').read_text())
        for name, expected in manifest['sha256'].items():
            self.assertEqual(hashlib.sha256((new / name).read_bytes()).hexdigest(), expected)
        catalog = json.loads((new / 'case-catalog.json').read_text())
        self.assertEqual(len(catalog['cases']), 42)
        self.assertTrue(all(x['legacy_job']['timeout_seconds'] is None for x in catalog['cases'].values()))
