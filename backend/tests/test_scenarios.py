import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ion import IonPlan
from models import Bus
from simulation_engine import TransitSimulation
class Scenarios(unittest.TestCase):
 def run_case(self, smart=False, **kwargs):
  s=TransitSimulation([Bus('B101'),Bus('B102'),Bus('B103'),Bus('B104')],IonPlan(duration_min=50,transfer_demand=50),intelligent=smart,duration=50,**kwargs)
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
 def test_scheduled_and_adaptive_dispatch_explain_distinct_buses(self):
  sim = self.run_case(smart=True, initial_queue=220)
  scheduled = next(event for event in sim.events if event["kind"] == "dispatch")
  staged = next(event for event in sim.events if event["kind"] == "decision" and event.get("bus_id"))
  self.assertEqual((scheduled["minute"], scheduled["bus_id"]), (0, "B101"))
  self.assertIn("scheduled", scheduled["reason"])
  # The pre-staged bus keeps its distance from the last release instead of leaving with it...
  self.assertEqual(staged["bus_id"], "B102")
  self.assertGreaterEqual(staged["minute"] - scheduled["minute"], sim.STAGING_MIN_GAP)
  self.assertIn("Forecast demand", staged["reason"])
  self.assertIn("seats expected to arrive", staged["reason"])
  # ...and takes the next scheduled slot rather than adding a bus beside it.
  skipped = next(event for event in sim.events if "release skipped" in event["message"])
  self.assertEqual(skipped["minute"], (staged["minute"] // 10 + 1) * 10)
  releases = [e["minute"] for e in sim.events if e.get("bus_id") and e["kind"] in ("dispatch", "decision")]
  self.assertTrue(all(b - a >= sim.STAGING_MIN_GAP for a, b in zip(releases, releases[1:])), releases)
 def test_staging_keeps_a_reserve_bus(self):
  # Spacing off, so only the reserve rule can hold buses back.
  class NoSpacing(TransitSimulation):
   STAGING_MIN_GAP = 0
   STAGING_PULLS_FORWARD = False
  sim = NoSpacing([Bus('B101'),Bus('B102'),Bus('B103'),Bus('B104')], IonPlan(duration_min=9, transfer_demand=300),
                  intelligent=True, duration=9, initial_queue=400, demand_multiplier=3.0)
  sim.run()
  staged = [e for e in sim.events if e["kind"] == "decision" and e.get("bus_id")]
  self.assertEqual(len(staged), 4 - 1 - sim.STAGING_RESERVE)
  self.assertEqual(sum(bus.state.name == "YARD" for bus in sim.buses), sim.STAGING_RESERVE)
if __name__=='__main__': unittest.main()
