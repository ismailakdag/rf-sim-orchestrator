import unittest
from pathlib import Path
from rfsim.cst_abort_guard import exact_model_argument, is_abort_confirmation, is_frontend_release


class AbortGuardTests(unittest.TestCase):
    def test_frontend_retrieval_requires_exact_title_and_unique_action(self):
        self.assertTrue(is_frontend_release('Frontend License Released', ['Retrieve &License', 'Exit']))
        self.assertFalse(is_frontend_release('License error', ['Retrieve License']))
        self.assertFalse(is_frontend_release('Frontend License Released', ['OK']))
        self.assertFalse(is_frontend_release('Frontend License Released', ['Retrieve License'] * 2))

    def test_only_exact_confirmation_is_accepted(self):
        labels = ['How would you like to abort?', '&Keep results', 'Discard results', 'OK', 'Cancel']
        self.assertTrue(is_abort_confirmation('Abort', labels))
        self.assertFalse(is_abort_confirmation('License', labels))
        self.assertFalse(is_abort_confirmation('Abort', labels[:-1]))
        self.assertFalse(is_abort_confirmation('Abort', labels + ['OK']))
        self.assertFalse(is_abort_confirmation('Abort', ['OK', 'Cancel']))

    def test_model_must_be_a_whole_command_argument(self):
        model = Path('owned/model.cst').resolve()
        self.assertTrue(exact_model_argument(['solver.exe', str(model)], model))
        self.assertFalse(exact_model_argument(['solver.exe', str(model) + '.backup'], model))
        self.assertFalse(exact_model_argument(['solver.exe', 'prefix ' + str(model)], model))
        self.assertFalse(exact_model_argument(['solver.exe', str(model.parent.parent / 'other/model.cst')], model))
