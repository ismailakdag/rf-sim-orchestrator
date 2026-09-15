import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('supervisor',Path(__file__).parents[1]/'scripts/school-supervisor.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SupervisorTests(unittest.TestCase):
    def test_resolved_migration_is_noop(self):
        with patch.object(module,'api',return_value={'state':'resolved'}) as api:
            module.recover_known_start_failure({},'old-job')
            self.assertEqual(api.call_count,1)

    def test_live_solver_prevents_recovery(self):
        from types import SimpleNamespace
        with patch.object(module,'api',return_value={'state':'needs_attention'}), patch('psutil.process_iter',return_value=[SimpleNamespace(info={'name':'Solver_HF_TD_AMD64.exe'})]):
            with self.assertRaises(RuntimeError):module.recover_known_start_failure({},'old-job')

    def test_only_expected_repository_is_allowed(self):
        self.assertTrue(module.check_origin('https://github.com/ismailakdag/rf-sim-orchestrator.git'))
        self.assertFalse(module.check_origin('https://github.com/attacker/rf-sim-orchestrator.git'))
        self.assertFalse(module.check_origin('https://github.com/ismailakdag/rf-sim-orchestrator.evil'))

    def test_release_archive_cannot_escape(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); archive=root/'bad.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('../outside.txt','bad')
            with self.assertRaises(RuntimeError):module.extract_release(archive,root/'release')
            self.assertFalse((root/'outside.txt').exists())

    def test_archive_expands_inside_release(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); archive=root/'ok.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('src/test.py','x=1')
            module.extract_release(archive,root/'release')
            self.assertEqual((root/'release/src/test.py').read_text(),'x=1')
