import unittest
from datetime import datetime, timezone
from rfsim.monitor import remote_rows, duration


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc)
        self.worker = dict(worker_id='school', hostname='PC-2', current_job_id='job-1',
                           state='running', last_seen_utc='2026-09-14T19:00:00Z')
        self.job = dict(job_id='job-1', worker_id='school', state='leased',
                        updated_utc='2026-09-14T19:59:45Z', note='worker stage: solver_running:3601s')

    def test_heartbeat_overrides_old_presence(self):
        row = remote_rows([self.worker], [self.job], self.now)[0]
        self.assertEqual(row['connection'], 'Çevrimiçi')
        self.assertEqual(row['activity'], 'Çalışıyor')
        self.assertEqual(row['elapsed'], '01:00:01')

    def test_stale_heartbeat_does_not_claim_running(self):
        self.job['updated_utc'] = '2026-09-14T19:30:00Z'
        row = remote_rows([self.worker], [self.job], self.now)[0]
        self.assertEqual(row['connection'], 'Çevrimdışı')
        self.assertEqual(row['activity'], 'Güncel işlem bilinmiyor')

    def test_wrong_worker_job_does_not_prove_online(self):
        self.job['worker_id'] = 'another-pc'
        self.assertEqual(remote_rows([self.worker], [self.job], self.now)[0]['connection'], 'Çevrimdışı')

    def test_new_workers_are_included_and_idle_is_distinct(self):
        another = dict(worker_id='PC-3', hostname='Third', current_job_id=None,
                       state='idle', last_seen_utc='2026-09-14T19:59:59Z')
        rows = remote_rows([self.worker, another], [self.job], self.now)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['activity'], 'İş bekliyor')
        self.assertEqual(duration(None), '—')
