"""The regional fleet and the spares dispatch sends (fleet.py, deadheads.json).

Acceptance 4: start with three assigned buses; when one breaks down, dispatch
sends a specific ready spare from the regional fleet; the inventory
reconciles and the spare takes modelled time to reach service.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fleet  # noqa: E402
import main  # noqa: E402

GTFS = Path("/home/user/gtfs/bus")


def run(**kw):
    return main.run_case(main.SimulationRequest(**kw), True)


class InventoryTests(unittest.TestCase):
    def test_inventory_is_configurable_and_separate(self):
        for n in (300, 320):
            f = fleet.Fleet(n, 17, corridor_buses=["B101", "B102", "B103"])
            conv = [v for v in f.vehicles.values() if v.kind == "conventional"]
            self.assertEqual(len(conv), n)
            self.assertEqual(sum(f.allocation.values()), n)
            self.assertEqual(len([v for v in f.vehicles.values() if v.kind == "mobilityplus"]), 35)
            self.assertEqual(len({v.id for v in f.vehicles.values()}), len(f.vehicles))
        self.assertFalse(f.shortfall)
        # Every block of the day has one bus; no bus has two blocks.
        self.assertEqual(len(f.block_vehicle), len(fleet.LINES["day_blocks"]))
        self.assertEqual(len(set(f.block_vehicle.values())), len(f.block_vehicle))

    def test_mobilityplus_never_covers_fixed_routes(self):
        f = fleet.Fleet(300, 17)
        ok, why = f.availability(600, "M01")
        self.assertFalse(ok)
        self.assertIn("MobilityPLUS", why)
        self.assertNotIn("M01", f.block_vehicle.values())

    def test_counts_reconcile_at_every_minute(self):
        f = fleet.Fleet(300, 17, corridor_buses=["B101", "B102", "B103"])
        for t in range(0, 3600, 300):
            for gid, c in f.counts(t).items():
                self.assertEqual(c["in_garage"] + c["out"], c["allocation"], (t, gid))

    def test_deadheads_are_built_from_osm(self):
        if not GTFS.exists():
            self.skipTest("sources not available")
        subprocess.run([sys.executable, "-I", str(ROOT.parent / "tools" / "waterloo_geometry" / "build_deadheads.py"),
                        "--check"], check=True, capture_output=True)
        dh = json.loads((ROOT / "deadheads.json").read_text())
        r = dh["routes"]["garage:strasburg|stop:1901"]
        self.assertIn("Ottawa Street South", r["via"])
        self.assertGreater(r["time_s"], 120)


class SpareDispatchTests(unittest.TestCase):
    """Acceptance 4."""

    @classmethod
    def setUpClass(cls):
        cls.r = run(fleet_size=3, breakdown_minute=15, breakdown_kind="out_of_service")

    def test_a_specific_ready_spare_is_sent_from_a_real_garage(self):
        d = next(d for d in self.r["decisions"] if d["terminal"] == "fleet" and "broke down" in d["trigger"])
        self.assertTrue(d["trigger"].startswith("B10"))
        spare = d["vehicle_id"]
        self.assertRegex(spare, r"^B\d+$")
        self.assertNotIn(spare, ("B101", "B102", "B103"))
        v = next(v for v in self.r["fleet"]["vehicles"] if v["id"] == spare)
        self.assertIn(v["garage"], ("strasburg", "northfield", "conestoga-blvd"))
        # Options compare the garages; the chosen one arrives first.
        feas = [o for o in d["options"] if o["feasible"]]
        self.assertEqual(min(o["estimate_s"] for o in feas), next(o for o in feas if o["option"] == d["chosen"])["estimate_s"])

    def test_the_spare_takes_modelled_time_to_reach_service(self):
        d = next(d for d in self.r["decisions"] if d["terminal"] == "fleet" and "broke down" in d["trigger"])
        v = next(v for v in self.r["fleet"]["vehicles"] if v["id"] == d["vehicle_id"])
        states = {s: t for t, s, _ in v["history"] if t is not None}
        dh = v["deadhead"]
        self.assertAlmostEqual(states["deadhead"] - states["preparing"], fleet.PREP_S, delta=0.5)
        route = fleet.DEADHEADS["routes"][dh["key"]]
        self.assertAlmostEqual(states["in service"] - states["deadhead"], route["time_s"], delta=0.5)
        # It joins the corridor at Central no earlier than that.
        joined = [e for e in self.r["events"] if e.get("bus_id") == v["id"] and "in service at Central" in e["message"]]
        self.assertTrue(joined)
        self.assertGreaterEqual(joined[0]["minute"] * 60.0, states["in service"] - 60)

    def test_the_inventory_reconciles(self):
        s = self.r["fleet"]["summary"]
        roles = s["roles"]
        self.assertEqual(sum(roles.values()), 300)
        self.assertEqual(roles.get("broken down"), 1)
        for c in (s["counts_start"], s["counts_end"]):
            for gid, x in c.items():
                self.assertEqual(x["in_garage"] + x["out"], x["allocation"])
        # Against the same run without the breakdown: one more bus out of the spare's garage.
        d = next(d for d in self.r["decisions"] if d["terminal"] == "fleet" and "broke down" in d["trigger"])
        gid = next(v["garage"] for v in self.r["fleet"]["vehicles"] if v["id"] == d["vehicle_id"])
        base = run(fleet_size=3)["fleet"]["summary"]["counts_end"]
        self.assertEqual(s["counts_end"][gid]["in_garage"], base[gid]["in_garage"] - 1)
        self.assertEqual(s["counts_end"][gid]["out"], base[gid]["out"] + 1)

    def test_riders_of_the_broken_bus_wait_again_without_being_counted_twice(self):
        base = run(fleet_size=3)
        self.assertLessEqual(self.r["metrics"]["passengers_boarded"], base["metrics"]["passengers_boarded"] + 5)


class LineBreakdownTests(unittest.TestCase):
    def test_a_route_7_breakdown_is_covered_from_the_next_relief_point(self):
        r = run(duration_min=120, breakdown_minute=20, breakdown_kind="out_of_service", breakdown_target="line")
        broke = next(d for d in r["decisions"] if "breaks down on trip" in d["trigger"])
        ask = next(d for d in r["decisions"] if d["terminal"] == "fleet" and "broke down on route 7" in d["trigger"])
        spare = ask["vehicle_id"]
        v = next(v for v in r["lines"]["vehicles"] if v["vehicle_id"] == spare)
        self.assertEqual(v["replaces"], broke["vehicle_id"])
        self.assertEqual(v["block"], next(x["block"] for x in r["lines"]["vehicles"] if x["vehicle_id"] == broke["vehicle_id"]))
        served = [x for x in v["stops"] if x["trip"] == broke["trip_id"]]
        self.assertTrue(served)                    # it carries on with the same trip
        self.assertGreaterEqual(r["lines"]["metrics"]["reboarded"], 1)


class CommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cmds = [{"minute": 10, "action": "dispatch_spare", "vehicle": "B206", "target": "corridor"},
                    {"minute": 11, "action": "dispatch_spare", "vehicle": "M04", "target": "corridor"},
                    {"minute": 11, "action": "dispatch_spare", "vehicle": "B101", "target": "corridor"},
                    {"minute": 12, "action": "reassign", "vehicle": None, "target": "corridor"}]
        probe = run(fleet_size=3)
        bus = next(v["vehicle_id"] for v in probe["lines"]["vehicles"] if v["stops"])
        cls.cmds[3]["vehicle"] = bus
        cls.r = run(fleet_size=3, commands=cls.cmds)
        cls.orders = [d for d in cls.r["decisions"] if d["terminal"] == "fleet" and d["trigger"].startswith("Dispatcher")]

    def test_orders_are_validated_with_reasons(self):
        by = {d["vehicle_id"]: d for d in self.orders}
        self.assertEqual(by["B206"]["chosen"], "accepted")
        self.assertEqual(by["M04"]["chosen"], "refused")
        self.assertIn("MobilityPLUS", by["M04"]["control"])
        self.assertEqual(by["B101"]["chosen"], "refused")
        self.assertIn("corridor run", by["B101"]["control"])

    def test_reassigning_an_in_service_bus_says_which_trips_lose_coverage(self):
        d = next(d for d in self.orders if d["trigger"].startswith("Dispatcher order") and "reassign" in d["trigger"])
        self.assertEqual(d["chosen"], "accepted")
        self.assertIn("loses coverage", d["effect"])
        self.assertIn("left the line", d["outcome"])
        self.assertIn(d["vehicle_id"], [b for b in [e.get("bus_id") for e in self.r["events"]] if b])

    def test_same_seed_and_commands_replay_exactly(self):
        again = run(fleet_size=3, commands=self.cmds)
        self.assertEqual(again["decisions"], self.r["decisions"])
        self.assertEqual(again["fleet"], self.r["fleet"])


if __name__ == "__main__":
    unittest.main()
