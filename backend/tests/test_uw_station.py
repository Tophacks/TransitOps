"""UW Station: buses as agents on Transit Plaza Road (one-second steps), station
reports, and dispatch predictions that use only what is known at the time."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import simulation_engine as se  # noqa: E402
import uw_station as uw  # noqa: E402
from ion import IonPlan  # noqa: E402
from models import Bus  # noqa: E402

BERTH_BLOCK = {"berth": "P6-1078-1", "from_s": 600.0, "to_s": 3480.0}
EXIT_BLOCK = {"exit": True, "from_s": 600.0, "to_s": 3480.0}


def run(intelligent=True, watch=None, **kw):
    plan = IonPlan(duration_min=60, headway_min=8, transfer_demand=110, delay_min=4)
    s = se.TransitSimulation([Bus(f"B{101 + i}") for i in range(3)], plan, intelligent=intelligent, duration=60,
                             traffic_multiplier=1.15, seed=17, **kw)
    if watch is not None:
        step = s.uw.step

        def watched(t):
            step(t)
            watch(s.uw, t)
        s.uw.step = watched
    s.run()
    return s


def uw_decisions(s, vehicle=None):
    return [d for d in s.decisions if d["terminal"] == "uw" and (vehicle is None or d["vehicle_id"] == vehicle)]


class GeometryTests(unittest.TestCase):
    def test_platforms_agree_with_gtfs_and_osm(self):
        refs = {p["ref"]: p for p in uw.GEO["platforms"]}
        self.assertEqual(sorted(refs), ["3", "4", "5", "6"])
        stops = uw.GEO["stops"]
        # GTFS platform_code for each stop; the builder fails if the OSM pole is elsewhere.
        self.assertEqual({k: v["platform"] for k, v in stops.items()},
                         {"1078": "6", "1223": "4", "1260": "3", "1262": "3", "1264": "5"})
        self.assertEqual(refs["4"]["side"], "se")
        self.assertEqual(refs["6"]["side"], "se")

    def test_berths_fit_on_their_platforms_without_overlap(self):
        for p in uw.GEO["platforms"]:
            spans = []
            for b in p["berths"]:
                lo, hi = sorted((b["front"], b["rear"]))
                self.assertAlmostEqual(hi - lo, uw.BUS_L, places=6)
                self.assertGreaterEqual(lo, p["s0"] - 0.5)
                self.assertLessEqual(hi, p["s1"] + 0.5)
                for a, c in spans:
                    self.assertTrue(hi <= a or lo >= c, b["id"])
                spans.append((lo, hi))
        self.assertEqual([b["id"] for b in uw.PLATFORMS["6"]["berths"]], ["P6-1078-1", "P6-1078-2"])

    def test_drop_off_only_calls_come_from_gtfs(self):
        pairs = {(x["stop"], x["route"]) for x in uw.GEO["gtfs"]["drop_off_only"]}
        self.assertIn(("1223", "13"), pairs)
        self.assertIn(("1078", "30"), pairs)
        self.assertIn(("1262", "9"), pairs)


class AgentTests(unittest.TestCase):
    def test_vehicles_never_overlap_in_a_lane(self):
        bad = []

        def watch(station, t):
            act = station.plaza()      # vehicles still on Transit Plaza Road
            for i, a in enumerate(act):
                for b in act[i + 1:]:
                    if a.side != b.side or not (a.lanes() & b.lanes()):
                        continue
                    if a.rear < b.p - 0.05 and b.rear < a.p - 0.05:
                        bad.append((t, a.id, b.id, round(a.p, 1), round(b.p, 1)))
        run(watch=watch, uw_block=BERTH_BLOCK)
        self.assertEqual(bad[:3], [])

    def test_three_buses_react_to_the_blocked_berth(self):
        s = run(uw_block=BERTH_BLOCK)
        reacted = {}
        for d in uw_decisions(s):
            if d["chosen"].startswith("alternate berth P6"):
                reacted[d["vehicle_id"]] = d
        self.assertEqual(sorted(reacted), ["B101", "B102", "B103"])
        for d in reacted.values():
            front = next(o for o in d["options"] if o["option"].startswith("berth P6-1078-1"))
            self.assertFalse(front["feasible"])
            self.assertIn("OOS", front["reason"])
            self.assertTrue(d["outcome"].startswith("stopped at P6-1078-2"))
        self.assertEqual(s.uw.metrics()["corridor_departures"], 3)

    def test_buses_wait_at_the_berth_while_the_exit_is_blocked(self):
        s = run(uw_block=EXIT_BLOCK)
        waits = [d for d in uw_decisions(s) if d["chosen"] == "wait at the berth"]
        self.assertTrue(waits)
        for d in waits:
            self.assertIn("exit is blocked", d["options"][0]["reason"])
            self.assertTrue(any(o["option"] == "leave by Ring Road instead" and not o["feasible"] for o in d["options"]))
        for a in s.uw.agents:
            if a.kind == "corridor" and a.exit_s is not None:
                self.assertFalse(600 <= a.exit_s < 3480, f"{a.id} left at {a.exit_s}")

    def test_a_bus_stops_for_the_level_crossing(self):
        s = run()
        gate = [d for d in uw_decisions(s) if d["chosen"] == "stop at the stop line"]
        self.assertTrue(gate)
        self.assertIn("LRV", gate[0]["trigger"])
        a, b, trains = next(g for g in s.uw.gates if g[0] <= gate[0]["t"] < g[1])
        self.assertTrue(trains)

    def test_corridor_buses_set_down_then_load(self):
        s = run()
        for a in s.uw.agents:
            if a.kind == "corridor" and a.exit_s is not None:
                berths = [b for b, _, _ in a.berth_log]
                self.assertTrue(berths[0].startswith("P4-"), berths)
                self.assertTrue(berths[-1].startswith("P6-"), berths)

    def test_same_scenario_same_station_decisions(self):
        a = run(uw_block=BERTH_BLOCK)
        b = run(uw_block=BERTH_BLOCK)
        self.assertEqual(uw_decisions(a), uw_decisions(b))
        self.assertEqual(a.uw.plans(), b.uw.plans())


class PredictionTests(unittest.TestCase):
    def test_prediction_ignores_what_has_not_happened_yet(self):
        plan = IonPlan(duration_min=60, transfer_demand=110, delay_min=4)
        trip = next(t for t in plan.trips if t.direction == "north" and "Central Station" in t.station_times
                    and t.station_times["Central Station"] > 600)
        actual = trip.station_times["Central Station"]
        names = list(trip.station_times)
        before = names[names.index("Central Station") - 3]
        now = trip.station_times[before] + 1.0
        predicted, kind = plan.predict_arrival(trip, "Central Station", now)
        self.assertEqual(kind, "predicted")
        line = plan.lines["north"]
        self.assertAlmostEqual(predicted, trip.station_times[before] + line.arrival_offset("Central Station")
                               - line.arrival_offset(before), places=6)
        # Changing the future does not change the prediction.
        trip.station_times["Central Station"] = actual + 999
        self.assertEqual(plan.predict_arrival(trip, "Central Station", now), (predicted, "predicted"))
        trip.station_times["Central Station"] = actual
        self.assertEqual(plan.predict_arrival(trip, "Central Station", actual + 1), (actual, "confirmed"))

    def test_hold_decisions_name_their_basis(self):
        s = run()
        holds = [d for d in s.decisions if d["id"].startswith("B")]
        self.assertTrue(holds)
        for d in holds:
            self.assertRegex(d["trigger"], r"\((predicted|confirmed)\)")


class StationReportTests(unittest.TestCase):
    def test_reports_carry_queue_predictions_buses_and_blocked_berths(self):
        s = run(uw_block=BERTH_BLOCK)
        reports = [d for d in s.decisions if d["level"] == "station" and d["trigger"].startswith("Station report")]
        self.assertTrue(reports)
        uw_reports = [d for d in reports if d["terminal"] == "uw" and d["t"] >= 600]
        self.assertTrue(any(o["option"] == "blocked berths" and "P6-1078-1" in o["reason"] for d in uw_reports
                            for o in d["options"]))
        for d in reports:
            names = [o["option"] for o in d["options"]]
            self.assertEqual(names[:3], ["confirmed queue", "predicted transfer arrivals", "buses coming"])
            self.assertTrue(d["chosen"].startswith("recommend: "))

    def test_capacity_requests_get_an_answer(self):
        s = run()
        answers = [d for d in s.decisions if "asks for extra capacity" in d["trigger"]]
        self.assertTrue(answers)
        for d in answers:
            self.assertIn(d["chosen"], [o["option"] for o in d["options"]])

    def test_baseline_has_no_reports_or_holds(self):
        s = run(intelligent=False)
        self.assertFalse([d for d in s.decisions if d["level"] == "station" and d["trigger"].startswith("Station report")])
        self.assertFalse([d for d in s.decisions if d["id"].startswith("B")])


if __name__ == "__main__":
    unittest.main()
