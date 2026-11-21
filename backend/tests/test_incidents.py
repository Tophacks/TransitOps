"""Incidents during ordinary runs (incidents.py), and replay by seed (acceptance 5).

Incidents are drawn from the seed before any driver or dispatcher acts; both
policies receive the same list; the same seed and dispatcher orders replay
the same incidents and decisions; each run reports trips covered, departure
delays, missed stops, transfers boarded and riders still waiting.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import incidents  # noqa: E402
import main  # noqa: E402

SEED = 11      # draws two Route 7 breakdowns, a station delay and a surge at UW in 120 min


def simulate(**kw):
    return main.simulate(main.SimulationRequest(**kw))


class GeneratorTests(unittest.TestCase):
    def test_drawn_from_the_seed(self):
        self.assertEqual(incidents.generate(SEED, 120), incidents.generate(SEED, 120))
        self.assertNotEqual(incidents.generate(SEED, 120), incidents.generate(SEED + 1, 120))
        kinds = {i["kind"] for i in incidents.generate(SEED, 120)}
        self.assertIn("bus_breakdown", kinds)
        self.assertIn("station_delay", kinds)

    def test_rates_are_settings(self):
        none = {k: 0.0 for k in incidents.RATES}
        self.assertEqual(incidents.generate(SEED, 120, none), [])
        many = incidents.generate(SEED, 120, {"passenger_surge": 6.0})
        self.assertGreater(sum(1 for i in many if i["kind"] == "passenger_surge"), 4)

    def test_at_most_one_train_fault(self):
        for sd in range(40):
            self.assertLessEqual(sum(1 for i in incidents.generate(sd, 180, {"train_fault": 3.0}) if i["kind"] == "train_fault"), 1)

    def test_targets_do_not_depend_on_the_run(self):
        # A Route 7 breakdown names a block due to be running then, from the schedule alone.
        for i in incidents.generate(SEED, 120):
            if i["kind"] == "bus_breakdown" and i["target"] == "line":
                self.assertIn(i["block"], incidents._blocks_running(i["minute"], ("7",)))


class NaturalRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kw = {"incidents": "natural", "seed": SEED, "duration_min": 120,
                  "commands": [{"minute": 30, "action": "dispatch_spare", "vehicle": "B206", "target": "corridor"}]}
        cls.r = simulate(**cls.kw)

    def test_both_policies_get_the_same_incidents(self):
        drawn = [(i["id"], i["minute"], i["kind"]) for i in self.r["incidents"]["list"]]
        for case in ("baseline", "smart"):
            self.assertEqual([(i["id"], i["minute"], i["kind"]) for i in self.r[case]["incidents"]], drawn)

    def test_incidents_change_what_drivers_and_dispatch_do(self):
        applied = [i for i in self.r["smart"]["incidents"] if i["applied"]]
        self.assertTrue(applied)
        spares = [d for d in self.r["smart"]["decisions"] if d["terminal"] == "fleet" and "broke down" in d["trigger"]]
        self.assertTrue(spares)                    # a Route 7 breakdown brought a spare out of a garage
        # A station delay is applied to the line (a bus waits only if one needs that stop then).
        delay = next(i for i in self.r["smart"]["incidents"] if i["kind"] == "station_delay")
        self.assertTrue(delay["applied"])
        self.assertIn("unavailable", delay["note"])
        # Driver errors are on in a natural run, drawn from the same seed.
        self.assertTrue(self.r["smart"]["uw"]["errors"])

    def test_same_seed_and_orders_replay_exactly(self):
        again = simulate(**self.kw)
        for case in ("baseline", "smart"):
            self.assertEqual(again[case]["incidents"], self.r[case]["incidents"])
            self.assertEqual(again[case]["decisions"], self.r[case]["decisions"])
            self.assertEqual(again[case]["report"], self.r[case]["report"])
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.r, sort_keys=True))

    def test_another_seed_another_day(self):
        other = simulate(**{**self.kw, "seed": SEED + 13})
        self.assertNotEqual(other["incidents"]["list"], self.r["incidents"]["list"])

    def test_the_run_report(self):
        rep = self.r["smart"]["report"]
        for key in ("trips_covered", "departure_delay_avg_s", "departure_delay_max_s", "missed_stops",
                    "transfers_boarded", "riders_still_waiting"):
            self.assertIn(key, rep)
        self.assertEqual(rep["trips_scheduled"], self.r["smart"]["lines"]["metrics"]["trips_scheduled"])
        self.assertEqual(rep["transfers_boarded"], self.r["smart"]["metrics"]["ion_transfers_boarded"]
                         + self.r["smart"]["lines"]["metrics"]["ion_transfers_boarded"])

    def test_off_by_default_for_the_api(self):
        r = main.run_case(main.SimulationRequest(), True)
        self.assertEqual(r["incidents"], [])


class CapacitySpareTests(unittest.TestCase):
    def test_adaptive_dispatch_brings_a_regional_spare_when_the_yard_is_empty(self):
        r = simulate(capacity_spares=1)
        asks = [d for d in r["smart"]["decisions"] if d["terminal"] == "fleet" and "asks for extra capacity" in d["trigger"]]
        self.assertEqual(len(asks), 1)
        self.assertIn("yard is empty", asks[0]["trigger"])
        self.assertTrue(asks[0]["vehicle_id"])
        # Holds only: no dispatcher answer; off by default.
        self.assertFalse([d for d in r["baseline"]["decisions"] if d["terminal"] == "fleet"])
        self.assertFalse([d for d in main.run_case(main.SimulationRequest(), True)["decisions"] if d["terminal"] == "fleet"])

    def test_at_most_the_allowed_number(self):
        r = main.run_case(main.SimulationRequest(capacity_spares=2, duration_min=120), True)
        asks = [d for d in r["decisions"] if d["terminal"] == "fleet" and "asks for extra capacity" in d["trigger"]]
        self.assertLessEqual(len(asks), 2)
        if len(asks) == 2:
            self.assertGreaterEqual(asks[1]["minute"] - asks[0]["minute"], 20)


if __name__ == "__main__":
    unittest.main()
