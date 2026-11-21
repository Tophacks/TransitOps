import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from continuous import ContinuousRun, REPLAY_MINUTES
from main import SimulationRequest, continuous_settings


class ContinuousRunTests(unittest.TestCase):
    def test_run_advances_across_the_hour_without_restarting(self):
        payload = SimulationRequest(fleet_size=3, train_delay=4, passenger_demand=110, surge_minute=20,
                                    surge_passengers=30)
        run = ContinuousRun(continuous_settings(payload))
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
        self.assertTrue(all(x[0] >= cut for v in lines["vehicles"] for x in v["trace"]))
        self.assertTrue(any(v["trace"] for v in lines["vehicles"]))
        for z in lines["zones"]:
            self.assertTrue(z["cars"])
            self.assertTrue(all(v["trace"][-1][0] >= cut for v in z["cars"]))
        self.assertIsNotNone(result["fleet"])

    def test_natural_incidents_reach_both_continuous_policies(self):
        payload = SimulationRequest(seed=11, duration_min=120, incidents="natural")
        settings = continuous_settings(payload)
        run = ContinuousRun(settings)
        self.assertTrue(settings["incidents"]["list"])
        self.assertEqual(run.cases[0].incidents, run.cases[1].incidents)
        self.assertEqual(run.cases[0].incidents, settings["incidents"]["list"])

        while run.minute < 25:
            output = json.loads(run.next_json())
        self.assertEqual(output["incidents"], settings["incidents"])
        for case in ("baseline", "smart"):
            self.assertTrue(output[case]["incidents"])
            self.assertIn("incidents_applied", output[case]["report"])

    def test_worker_bundle_runs_in_isolation(self):
        settings = continuous_settings(SimulationRequest())
        with tempfile.TemporaryDirectory() as root:
            for name, contents in settings.pop("sources").items():
                (Path(root) / name).write_text(contents, encoding="utf-8")
            # No site packages, PYTHONPATH, working tree or inherited imports.
            subprocess.run([sys.executable, "-I", "-S", "-c",
                            "import sys,json;sys.path.insert(0,'.');"
                            "from continuous import ContinuousRun;"
                            "r=ContinuousRun(json.load(sys.stdin)).extend();"
                            "assert r['smart']['lines']['vehicles']"],
                           cwd=root, input=json.dumps(settings), text=True, check=True)

    def test_zone_pruning_preserves_live_traffic(self):
        sim = ContinuousRun(continuous_settings(SimulationRequest())).cases[1]
        sim.lines.advance_to(900)
        lanes = [z["lane_model"] for z in sim.lines.zones]
        # Isolate the next lane step from bus-controller changes in both copies.
        controls = copy.deepcopy(lanes)
        active = [[v for v in lane.vehicles if not v.done] for lane in lanes]
        expired = [{v.id for v in lane.vehicles if v.kind == "car" and v.done and v.trace[-1][0] < 870}
                   for lane in lanes]
        self.assertTrue(all(expired))
        # The public prune API takes minutes. Cut through active traces.
        ContinuousRun.prune(sim, 14.5)
        replay = {z["id"]: z["cars"] for z in sim.lines.zone_cars()}
        def occupancy(model):
            return [(v.id, v.front, v.speed) for v in model.ordered()]

        for lane, control, live, gone in zip(lanes, controls, active, expired):
            self.assertTrue(live)
            self.assertFalse(gone.intersection(v["id"] for v in replay[lane.name]))
            self.assertEqual([v for v in lane.vehicles if not v.done], live)
            self.assertTrue(all(sum(t <= 870 for t, _ in v.trace) <= 1 for v in lane.vehicles))
            for t in range(900, 960):
                lane.step(t)
                control.step(t)
                self.assertEqual(occupancy(lane), occupancy(control))
                self.assertEqual(lane.cars, control.cars)
