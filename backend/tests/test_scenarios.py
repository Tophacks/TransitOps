import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from models import Bus,Train
from simulation_engine import TransitSimulation
class Scenarios(unittest.TestCase):
 def run_case(self, smart=False, **kwargs):
  s=TransitSimulation([Bus('B101'),Bus('B102'),Bus('B103'),Bus('B104')],[Train('T1',12,0,50)],intelligent=smart,duration=50,**kwargs)
  s.run()
  return s
 def test_reproducible(self):
  a=self.run_case(seed=99,demand_multiplier=1.8,surge_minute=20,surge_passengers=80)
  b=self.run_case(seed=99,demand_multiplier=1.8,surge_minute=20,surge_passengers=80)
  self.assertEqual(a.timeline,b.timeline)
  self.assertEqual(a.events,b.events)
 def test_surge_raises_arrivals(self):
  a=self.run_case(seed=33)
  b=self.run_case(seed=33,surge_minute=20,surge_passengers=120)
  self.assertGreaterEqual(b.metrics.total_passengers_arrived,a.metrics.total_passengers_arrived+120)
  self.assertTrue(any(e['kind']=='surge' for e in b.events))
 def test_breakdown_and_emergency(self):
  a=self.run_case(breakdown_minute=20,emergency_dispatch_minute=15)
  self.assertTrue(any(e['kind']=='disruption' for e in a.events))
  self.assertTrue(any(e['kind']=='recovery' for e in a.events))
  self.assertTrue(any(e['kind']=='emergency' for e in a.events))
  self.assertTrue(any(x['state']=='BROKEN_DOWN' for tick in a.timeline for x in tick['buses']))
if __name__=='__main__': unittest.main()
