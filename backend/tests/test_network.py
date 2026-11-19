"""ION network operations: crossovers, closures, single-track sections."""
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ion  # noqa: E402
from ion import IonPlan  # noqa: E402
from rail_ops import NETWORK  # noqa: E402


def closure(track, a, b, start, end):
    return [{"id": "C", "track": track, "from_station": f"{a} Station", "to_station": f"{b} Station",
             "from_minute": start, "to_minute": end, "reason": "track work"}]


def run(closures=None):
    return IonPlan(duration_min=60, headway_min=8, transfer_demand=110, delay_min=4, closures=closures)


GRH_CENTRAL = closure("south", "Grand River Hospital", "Central", 15, 45)
MARKET_BORDEN = closure("south", "Kitchener Market", "Borden", 15, 35)


class NetworkTests(unittest.TestCase):
    def test_crossovers_and_the_moves_they_support(self):
        by_way = {c["way"]: c for c in NETWORK["crossovers"]}
        self.assertEqual(sorted(by_way), [582281483, 750968494, 754590776, 754590777, 754590778, 754590784])
        # Northfield takes northbound trains north->south; every other one south->north.
        north = {w: next(m for m in c["moves"] if m["travel"] == "north") for w, c in by_way.items()}
        self.assertEqual((north[750968494]["from"], north[750968494]["to"]), ("north", "south"))
        for w in (754590776, 582281483, 754590777, 754590778, 754590784):
            self.assertEqual((north[w]["from"], north[w]["to"]), ("south", "north"), w)
        # South of Mill: in the network, not for passengers.
        self.assertFalse(by_way[754590784]["passenger"])
        self.assertFalse(by_way[754590784]["electrified"])

    def test_diversions_cross_over_and_back_in_the_direction_of_travel(self):
        self.assertTrue(NETWORK["diversions"])
        by_way = {c["way"]: c for c in NETWORK["crossovers"]}
        for d in NETWORK["diversions"]:
            first, second = (by_way[w] for w in d["via"])
            self.assertTrue(first["passenger"] and second["passenger"])
            travel, closed, other = d["travel"], d["closed"], d["single_track"]
            self.assertIn({"travel": travel, "from": closed, "to": other}, first["moves"])
            self.assertIn({"travel": travel, "from": other, "to": closed}, second["moves"])
            # A complete route: from a platform to a platform, stops in order.
            at = [s["at_m"] for s in d["stops"]]
            self.assertEqual(at, sorted(at))
            self.assertAlmostEqual(at[-1], d["length_m"], places=0)
        # No mid-line diversion exists for the northbound track.
        self.assertFalse([d for d in NETWORK["diversions"] if d["closed"] == "north"])

    def test_turnbacks_need_an_enabled_crossover_behind_the_platform(self):
        tb = {x["station"]: x for x in NETWORK["turnbacks"]["north"]}
        self.assertFalse(tb["Borden Station"]["valid"])
        south = {x["station"]: x for x in NETWORK["turnbacks"]["south"]}
        self.assertTrue(south["Kitchener Market Station"]["valid"])
        self.assertEqual(south["Kitchener Market Station"]["crossover"], 754590777)


class ClosureTests(unittest.TestCase):
    def sections(self, plan):
        return [e for sec in plan.ops.sections.values() for e in sec["log"]]

    def test_opposing_trains_never_share_the_single_track(self):
        for c in (GRH_CENTRAL, closure("south", "Grand River Hospital", "Central", 25, 45),
                  closure("south", "Grand River Hospital", "Central", 10, 30)):
            plan = run(c)
            log = self.sections(plan)
            self.assertTrue(any(e["role"] == "divert" for e in log))
            for a in log:
                for b in log:
                    if a is b or a["direction"] == b["direction"]:
                        continue
                    overlap = a["from_s"] < b["release_s"] and b["from_s"] < a["release_s"]
                    self.assertFalse(overlap, f"{a['vehicle_id']} and {b['vehicle_id']} share the section")

    def test_a_train_waits_until_the_other_trains_rear_clears(self):
        plan = run(GRH_CENTRAL)
        log = sorted(self.sections(plan), key=lambda e: e["from_s"])
        waits = [d for d in plan.ops.decisions if d.chosen.startswith("hold at") and "single-track section" in d.chosen]
        self.assertTrue(waits)
        d = waits[0]
        granted = next(e for e in log if e["vehicle_id"] == d.vehicle_id)
        before = [e for e in log if e["direction"] != granted["direction"] and e["release_s"] <= granted["from_s"] + 1e-6]
        self.assertTrue(before)
        self.assertAlmostEqual(max(e["release_s"] for e in before), granted["from_s"], places=6)
        # The release is when the rear has passed the far end of the section.
        rel = max(before, key=lambda e: e["release_s"])
        train = next(t for t in plan.ops.trains if t.vehicle_id == rel["vehicle_id"])
        seg, nose, _ = plan.ops.where(train, rel["release_s"] - 0.5)
        self.assertTrue(seg.kind in ("run", "move"))

    def test_closure_timing_changes_the_outcome(self):
        outcomes = []
        for start, end in ((15, 45), (25, 45), (10, 30)):
            plan = run(closure("south", "Grand River Hospital", "Central", start, end))
            diverted = [e["vehicle_id"] for e in self.sections(plan) if e["role"] == "divert"]
            outcomes.append((tuple(diverted), plan.ops.metrics([t for t in plan.trips if 0 <= t.scheduled_s < 3600])["avg_departure_delay_s"]))
        self.assertEqual(len(set(outcomes)), 3, outcomes)

    def test_diverted_trains_stop_at_the_other_tracks_platforms(self):
        plan = run(GRH_CENTRAL)
        e = next(e for e in self.sections(plan) if e["role"] == "divert")
        trip = next(t for t in plan.trips if t.train_id == e["trip"])
        # Southbound on the northbound track downtown: City Hall and Frederick, not Victoria Park and Queen.
        self.assertIn("Kitchener City Hall Station", trip.station_times)
        self.assertNotIn("Queen Station", trip.station_times)
        self.assertIn("Central Station", trip.station_times)

    def test_no_diversion_means_holding_at_the_closure(self):
        plan = run(MARKET_BORDEN)
        d = next(d for d in plan.ops.decisions if "track reopens" in " ".join(o["option"] for o in d.options) and d.terminal == "line")
        self.assertEqual(d.chosen, "hold at Kitchener Market until 35:00")
        tb = next(o for o in d.options if o["option"].startswith("turn back"))
        self.assertFalse(tb["feasible"])
        self.assertIn("route valid", tb["reason"])
        # No southbound train is on the closed stretch while it is closed.
        line = plan.lines["south"]
        a = dict(line.stops)["Kitchener Market Station"]
        b = dict(line.stops)["Borden Station"]
        for t in range(15 * 60, 35 * 60, 10):
            for pose in plan.trains_at(float(t)):
                if pose["direction"] == "south" and pose["kind"] == "run":
                    self.assertFalse(a + 1 < pose["nose_line"] < b - 1, f"{pose['vehicle_id']} on the closed track at {t}s")

    def test_trains_never_share_a_platform(self):
        for c in (None, GRH_CENTRAL, MARKET_BORDEN):
            plan = run(c)
            stands = {}
            for train in plan.ops.trains:
                for seg in train.segments:
                    if seg.kind == "stand" and seg.line and seg.station:
                        stands.setdefault((seg.line, seg.station), []).append((seg.t0, seg.t1, train.vehicle_id))
            for key, spans in stands.items():
                spans.sort()
                for (a0, a1, va), (b0, b1, vb) in zip(spans, spans[1:]):
                    self.assertLessEqual(a1, b0 + 1e-6, f"{va} and {vb} both at {key}")

    def test_same_closure_same_decisions(self):
        a = json.dumps(run(GRH_CENTRAL).schedule(), sort_keys=True, default=str)
        b = json.dumps(run(GRH_CENTRAL).schedule(), sort_keys=True, default=str)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
