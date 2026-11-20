import json
import unittest

from continuous import ContinuousRun, REPLAY_MINUTES
from main import SimulationRequest, continuous_settings, network_definition, rail_settings, bus_settings


class ContinuousRunTests(unittest.TestCase):
    def test_run_advances_across_the_hour_without_restarting(self):
        payload = SimulationRequest(fleet_size=3, train_delay=4, passenger_demand=110, surge_minute=20,
                                    surge_passengers=30)
        run = ContinuousRun({
            "scenario": payload.model_dump(), "network": network_definition(),
            "rail": rail_settings(payload), "bus": bus_settings(payload),
        })
        while run.minute < 60:
            result = json.loads(run.next_json())["smart"]

        last = result["timeline"][-1]
        metrics = result["metrics"]
        sim = run.cases[1]
        buses, rail = tuple(sim.buses), sim.ion.ops
        next_result = json.loads(run.next_json())["smart"]
        self.assertEqual(next_result["timeline"][-6], last)
        self.assertGreater(next_result["metrics"]["passengers_arrived"], metrics["passengers_arrived"])
        self.assertEqual(tuple(sim.buses), buses)
        self.assertIs(sim.ion.ops, rail)

        while run.minute < 80:
            next_result = json.loads(run.next_json())["smart"]
        self.assertEqual(len(next_result["timeline"]), REPLAY_MINUTES + 1)
        self.assertEqual(next_result["timeline"][0]["minute"], run.minute - REPLAY_MINUTES)
        self.assertTrue(any(trip["scheduled_s"] > 3600 for trip in next_result["ion"]["trips"]))
        self.assertGreater(next_result["metrics"]["ion_transfers"], metrics["ion_transfers"])
        self.assertEqual(sim.metrics.unserved_wait_minutes, 0)
        surges = [e for e in next_result["events"] if e["kind"] == "surge"]
        self.assertEqual([e["minute"] for e in surges], [20])

    def test_route_7_keeps_running_after_the_first_hour(self):
        payload = SimulationRequest(fleet_size=3)
        settings = continuous_settings(payload)
        settings.pop("sources")
        run = ContinuousRun(settings)
        while run.minute < 100:
            result = json.loads(run.next_json())["smart"]
        lines = result["lines"]
        later = [x for v in lines["vehicles"] for x in v["stops"] if x["dep"] > 3600]
        self.assertTrue(later)                                   # served stops after 08:30
        self.assertGreater(lines["metrics"]["trips_covered"], 10)
        cut = (run.minute - REPLAY_MINUTES) * 60
        self.assertTrue(all(seg[1] >= cut for v in lines["vehicles"] for seg in v["segments"]))
        self.assertIsNotNone(result["fleet"])

    def test_worker_bundle_contains_the_engine_and_geometry(self):
        settings = continuous_settings(SimulationRequest())
        self.assertIn("class ContinuousRun", settings["sources"]["continuous.py"])
        self.assertIn("ion_network.json", settings["sources"])
        for name in ("lines.py", "fleet.py", "lines.json", "deadheads.json"):
            self.assertIn(name, settings["sources"])
        self.assertNotIn("main.py", settings["sources"])
