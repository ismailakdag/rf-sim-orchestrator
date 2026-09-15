import json,tempfile,unittest
from pathlib import Path
from test_orchestrator import job
from rfsim.worker import run_mock,package_result
from rfsim.host import Store
from rfsim.common import sha256_file,validate_result_zip,ValidationError
from rfsim.recover_upload import repair

class RecoveryTest(unittest.TestCase):
 def test_missing_model_repair_and_bound_recovery(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);document=job('recovery-fixture');document.update(priority=0,metadata={});run=root/'run'
   run_mock(document,run,{})
   (run/'model/model.cst.mock').unlink()
   (run/'parameters/record.json').write_text(json.dumps(dict(run_id=document['job_id'],results_validated=True,project_closed=True)))
   original=package_result(run,document);original_hash=sha256_file(original)
   with self.assertRaises(ValidationError):validate_result_zip(original,document['job_id'],10000000)
   fixed=repair(original,root/'repaired.zip',document['job_id'])
   self.assertEqual(original_hash,sha256_file(original))
   store=Store(root/'host',60,{'mock-v1'},10000000,20000000);store.submit(document)
   lease=store.lease('school',['mock-v1']);store.attention(document['job_id'],'school',lease['lease_token'],'fixture upload failed')
   with self.assertRaises(PermissionError):store.accept_result(document['job_id'],'other','',fixed,sha256_file(fixed),recovery=True)
   result=store.accept_result(document['job_id'],'school','',fixed,sha256_file(fixed),recovery=True)
   self.assertEqual(result['state'],'completed')
