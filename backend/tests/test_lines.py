"""Scheduled GRT lines end to end (lines.py, lines.json).

Acceptance 1: a scheduled Route 7 bus completes an end-to-end run; a station
delay changes downstream arrivals and connections, and can make the bus late
for its next assignment; the next route goes through the same data model.
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "tools" / "waterloo_geometry"))

import lines  # noqa: E402
import main  # noqa: E402
from ion import IonPlan  # noqa: E402

GTFS = Path("/home/user/gtfs/bus")
CONESTOGA, FAIRWAY, CENTRAL = "1130", "3490", "1901"


def run(**kw):
    return main.run_case(main.SimulationRequest(duration_min=120, **kw), True)


def stops_by(r):
    out = {}
    for v in r["lines"]["vehicles"]:
        for x in v["stops"]:
            out[(v["vehicle_id"], x["trip"], x["stop"])] = x
    return out


class DataTests(unittest.TestCase):
    def test_lines_json_is_built_from_gtfs(self):
        if not GTFS.exists():
            self.skipTest("GTFS not available")
        subprocess.run([sys.executable, "-I", str(ROOT.parent / "tools" / "waterloo_geometry" / "build_lines.py"), "--check"],
                       check=True, capture_output=True)

    def test_route_7_both_directions_and_its_variants(self):
        r7 = lines.LINES["routes"]["7"]
        main_patterns = {p["direction"]: p for p in r7["patterns"] if p["trips"] > 50}
        self.assertEqual(set(main_patterns), {0, 1})
        south, north = main_patterns[1], main_patterns[0]
        self.assertEqual((south["stops"][0]["stop"], south["stops"][-1]["stop"]), (CONESTOGA, FAIRWAY))
        self.assertEqual((north["stops"][0]["stop"], north["stops"][-1]["stop"]), (FAIRWAY, CONESTOGA))
        self.assertEqual(len(south["stops"]), 40)
        self.assertEqual(len(r7["trips"]), 150)
        # Stops at increasing distances along the shape.
        for p in r7["patterns"]:
            s = [x["s"] for x in p["stops"]]
            self.assertEqual(s, sorted(s))

    def test_ids_are_distinct(self):
        r7 = lines.LINES["routes"]["7"]
        trips = {t["trip_id"] for t in r7["trips"]}
        blocks = {t["block"] for t in r7["trips"]}
        stops = set(lines.LINES["stops"])
        svc = lines.LineService(("7",), duration_s=600)
        vehicles = {b.vehicle_id for b in svc.buses}
        for a, b in ((trips, blocks), (trips, stops), (trips, vehicles), (blocks, vehicles), (stops, vehicles)):
            self.assertFalse(a & b)
        self.assertNotIn("7", trips | blocks | stops | vehicles)

    def test_shared_stations_are_verified_links(self):
        st = lines.LINES["stops"]
        self.assertEqual(st[CENTRAL]["corridor"], "CENTRAL")
        self.assertEqual(st["4048"]["corridor"], "WATERLOO_SQUARE")
        self.assertEqual(st[FAIRWAY]["ion"]["station"], "Fairway Station")
        self.assertEqual(st[CONESTOGA]["ion"]["station"], "Conestoga Station")
        for sid, s in st.items():
            if "ion" in s:
                self.assertIn(s["ion"]["station"].replace(" Station", "").lower(), s["name"].lower())
                self.assertLessEqual(s["ion"]["walk_m"], 300)


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = run()

    def test_a_route_7_bus_runs_conestoga_to_fairway_and_lays_over_for_its_next_trip(self):
        south = next(p for p in lines.LINES["routes"]["7"]["patterns"] if p["direction"] == 1 and p["trips"] > 50)
        want = [x["stop"] for x in south["stops"]]
        trips = {t["trip_id"]: t for t in lines.LINES["routes"]["7"]["trips"]}
        found = None
        for v in self.base["lines"]["vehicles"]:
            by_trip = {}
            for x in v["stops"]:
                by_trip.setdefault(x["trip"], []).append(x)
            for tid, xs in by_trip.items():
                if trips[tid]["pattern"] == south["id"] and [x["stop"] for x in xs] == want:
                    found = (v, tid, xs)
                    break
            if found:
                break
        self.assertIsNotNone(found, "no complete Conestoga -> Fairway trip in the run")
        v, tid, xs = found
        times = [x["arr"] for x in xs]
        self.assertEqual(times, sorted(times))
        self.assertFalse(xs[0]["passed"])
        self.assertFalse(xs[-1]["passed"])
        # Its block's next trip leaves Fairway after a layover there.
        rows = lines.LINES["blocks"][v["block"]]
        k = next(i for i, r in enumerate(rows) if r["trip_id"] == tid)
        nxt = rows[k + 1]
        self.assertEqual(nxt["from"], FAIRWAY)
        later = [x for x in v["stops"] if x["trip"] == nxt["trip_id"]]
        self.assertTrue(later)
        self.assertGreaterEqual(later[0]["dep"], xs[-1]["dep"] + lines.MIN_LAYOVER_S - 1)
        self.assertGreaterEqual(later[0]["dep"], nxt["start"] - lines.HOUR0)

    def test_on_schedule_by_default(self):
        m = self.base["lines"]["metrics"]
        self.assertEqual(m["trips_covered"], m["trips_scheduled"])
        self.assertLess(abs(m["arrival_delay_avg_s"]), 120)
        self.assertGreater(m["connections_made"], 5 * m["connections_missed"])

    def test_same_seed_same_run(self):
        again = run()
        self.assertEqual(again["lines"]["vehicles"], self.base["lines"]["vehicles"])
        self.assertEqual(again["lines"]["metrics"], self.base["lines"]["metrics"])


class StationDelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = run()
        cls.short = run(line_block_stop=CENTRAL, line_block_from_minute=20, line_block_to_minute=30)
        cls.long = run(line_block_stop=CENTRAL, line_block_from_minute=20, line_block_to_minute=40)

    def test_a_blocked_station_makes_later_stops_late_and_misses_connections(self):
        a, b = stops_by(self.base), stops_by(self.short)
        late = {k: b[k]["dep"] - a[k]["dep"] for k in a if k in b}
        at_central = [k for k, d in late.items() if k[2] == CENTRAL and d >= 60]
        self.assertTrue(at_central)
        vid, tid, _ = at_central[0]
        trips = {t["trip_id"]: t for t in lines.LINES["routes"]["7"]["trips"]}
        pat = next(p for r in lines.LINES["routes"].values() for p in r["patterns"] if p["id"] == trips[tid]["pattern"])
        after = [x["stop"] for x in pat["stops"]][[x["stop"] for x in pat["stops"]].index(CENTRAL) + 1:]
        downstream = [late[(vid, tid, s)] for s in after if (vid, tid, s) in late]
        self.assertTrue(downstream)
        self.assertTrue(all(d > 30 for d in downstream), downstream)
        self.assertGreater(self.short["lines"]["metrics"]["connections_missed"],
                           self.base["lines"]["metrics"]["connections_missed"])
        wait = next(d for d in self.short["decisions"] if d["vehicle_id"] == vid and d["trigger"].startswith("Berth at Central"))
        self.assertIn("blocked", wait["saw"])

    def test_a_long_delay_makes_the_bus_late_for_its_next_trip(self):
        late = [d for d in self.long["decisions"] if d["terminal"] == "route 7" and d["level"] == "dispatch"
                and "late" in d["chosen"]]
        self.assertTrue(late)
        self.assertGreater(self.long["lines"]["metrics"]["departure_delay_max_s"],
                           self.base["lines"]["metrics"]["departure_delay_max_s"] + 120)


class MissedStopTests(unittest.TestCase):
    def test_a_missed_lane_change_passes_the_stop_and_continues(self):
        r = run(driver_error="missed_stop", missed_stop_stop="2540", missed_stop_after_minute=10)
        d = next(d for d in r["decisions"] if d.get("error", "").startswith("driver error: missed the lane change"))
        self.assertEqual(d["chosen"], "continue to the next stop")
        self.assertEqual(r["lines"]["metrics"]["missed_stops"], 1)
        v = next(v for v in r["lines"]["vehicles"] if v["vehicle_id"] == d["vehicle_id"])
        x = next(x for x in v["stops"] if x.get("missed"))
        self.assertEqual(x["stop"], "2540")
        nxt = [y for y in v["stops"] if y["trip"] == x["trip"] and y["arr"] > x["arr"]]
        self.assertTrue(nxt)                     # the trip continues from where the bus is
        # The next bus at that stop picks up the riders it left, and says so.
        later = next(d for d in r["decisions"] if "left behind" in d["trigger"] and "(2540)" in d["trigger"])
        self.assertNotEqual(later["vehicle_id"], d["vehicle_id"])
        self.assertIn(d["vehicle_id"], later["outcome"])
        self.assertGreater(later["t"], d["t"])


class NextRouteTests(unittest.TestCase):
    def test_route_31_goes_through_the_same_data_model(self):
        if not GTFS.exists():
            self.skipTest("GTFS not available")
        import build_lines
        data, _ = build_lines.build(GTFS, Path("/home/user/gtfs/lrt"), {"31"})
        svc = lines.LineService(("31",), duration_s=3600, data=data,
                                ion_plan=IonPlan(duration_min=60, transfer_demand=0))
        svc.advance_to(3600)
        m = svc.finish()
        self.assertGreater(m["trips_covered"], 0)
        self.assertGreater(m["boarded"], 0)


if __name__ == "__main__":
    unittest.main()
