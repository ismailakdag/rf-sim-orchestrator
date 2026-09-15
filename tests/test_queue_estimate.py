import unittest
from rfsim.monitor import queue_estimate
class EstimateTests(unittest.TestCase):
 def test_partial(self):
  r=queue_estimate(10,2,1,[{'total_seconds':100},{'total_seconds':200}],50)
  self.assertEqual(r['average'],'00:02:30');self.assertEqual(r['remaining'],'≈ 00:16:40')
 def test_stale_and_overrun(self):
  self.assertEqual(queue_estimate(3,1,0,[{'total_seconds':100}],120)['remaining'],'Aktif iş ortalamayı aştı')
  self.assertEqual(queue_estimate(3,1,0,[{'total_seconds':100}],10,False)['remaining'],'Durakladı / güncel değil')
 def test_missing_and_finished(self):
  self.assertEqual(queue_estimate(3,0,0,[])['remaining'],'—')
  self.assertEqual(queue_estimate(3,2,1,[])['remaining'],'Tamamlandı')
 def test_invalid_duration(self):
  self.assertEqual(queue_estimate(3,1,0,[{'total_seconds':float('nan')},{'total_seconds':-1}])['average'],'—')
