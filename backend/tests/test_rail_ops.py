"""ION terminal operations: physical trains, station control and dispatch."""
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ion  # noqa: E402
import rail_ops  # noqa: E402
from ion import IonPlan  # noqa: E402
from rail_ops import TERMINALS  # noqa: E402

HOLD = {"fairway": {"terminal": "fairway", "platform": "1", "minute": 20, "minutes": 14, "reason": "door fault"},
        "conestoga": {"terminal": "conestoga", "platform": "1", "minute": 20, "minutes": 14, "reason": "door fault"}}


def plan(hold=None, reassign=True, **kw):
    return IonPlan(duration_min=60, headway_min=8, transfer_demand=110, delay_min=0,
                   platform_hold=hold, reassign=reassign, **kw)


def window(p):
    return [t for t in p.trips if 0 <= t.scheduled_s < p.duration_s]


def pos(path, s):
    import corridor
    return corridor._lerp_path(path, s)


def metres(a, b):
    k = math.cos(math.radians(43.465))
    return math.hypot((a[0] - b[0]) * 111320 * k, (a[1] - b[1]) * 111320)


class LayoutTests(unittest.TestCase):
    def test_conflicting_moves_share_a_resource_and_the_straight_pair_does_not(self):
        for key, t in TERMINALS.items():
            res = {name: {r["id"] for r in route["resources"]} for name, route in t["routes"].items()}
            straight_in = next(n for n, r in t["routes"].items() if r["kind"] == "arrive" and not r["crossover"])
            straight_out = next(n for n, r in t["routes"].items() if r["kind"] == "depart" and not r["crossover"])
            # The two straight moves run side by side on parallel tracks.
            self.assertFalse(res[straight_in] & res[straight_out], key)
            # Both crossover moves pass the diamond, so they conflict.
            crossing = [n for n, r in t["routes"].items() if r["crossover"]]
            self.assertEqual(len(crossing), 2)
            self.assertIn("diamond", res[crossing[0]] & res[crossing[1]])
            # A platform's arrival and departure share the platform track.
            for p in ("1", "2"):
                self.assertIn(t["platforms"][p]["edge"], res[f"arrive:{p}"] & res[f"depart:{p}"])

    def test_conestoga_crossovers_are_the_mapped_osm_ways(self):
        edges = TERMINALS["conestoga"]["edges"]
        self.assertEqual(sorted(edges["crossover_a"]["ways"] + edges["crossover_b"]["ways"]), [836238400, 836238401])
        self.assertEqual(TERMINALS["conestoga"]["diamond"]["node"], 7805105214)
        fair = TERMINALS["fairway"]["edges"]
        self.assertEqual(sorted(fair["crossover_a"]["ways"] + fair["crossover_b"]["ways"]), [754590785, 754590786])

    def test_arrival_and_departure_meet_at_the_same_platform_position(self):
        for key, t in TERMINALS.items():
            for p in ("1", "2"):
                arr, dep = t["routes"][f"arrive:{p}"], t["routes"][f"depart:{p}"]
                # The arriving nose (cab A) and the departing tail (cab A) stand at the same spot.
                a = pos(arr["path"], arr["stop_m"])
                d = pos(dep["path"], dep["start_m"] - ion.ION_LENGTH)
                self.assertLess(metres(a, d), 0.05, f"{key} platform {p}")
                a_tail = pos(arr["path"], arr["stop_m"] - ion.ION_LENGTH)
                d_nose = pos(dep["path"], dep["start_m"])
                self.assertLess(metres(a_tail, d_nose), 0.05)


class StationControlTests(unittest.TestCase):
    def check_no_overlap(self, p):
        for st in p.ops.stations.values():
            by_res = {}
            for res, vehicle, t_in, t_out, move in st.occupancy:
                by_res.setdefault(res, []).append((t_in, math.inf if t_out is None else t_out, vehicle, move))
            for res, spans in by_res.items():
                spans.sort()
                for (a0, a1, va, ma), (b0, b1, vb, mb) in zip(spans, spans[1:]):
                    if va != vb:
                        self.assertLessEqual(a1, b0 + 1e-6, f"{st.key} {res}: {va} ({ma}) and {vb} ({mb}) overlap")
            for res, entries in st.reservations.items():
                spans = sorted((e[1], math.inf if e[2] is None else e[2], e[0]) for e in entries)
                for (a0, a1, va), (b0, b1, vb) in zip(spans, spans[1:]):
                    if va != vb:
                        self.assertLessEqual(a1, b0 + 1e-6, f"{st.key} {res}: reservations of {va} and {vb} overlap")

    def test_conflicting_moves_never_overlap(self):
        for hold in (None, HOLD["fairway"], HOLD["conestoga"]):
            for reassign in (True, False):
                self.check_no_overlap(plan(hold, reassign))

    def test_space_is_released_when_the_rear_clears_it(self):
        p = plan(HOLD["fairway"])
        checked = 0
        for st in p.ops.stations.values():
            for res, vehicle, t_in, t_out, move in st.occupancy:
                if t_out is None or move == "stand":
                    continue
                route = st.route(move)
                r = next(x for x in route["resources"] if x["id"] == res)
                train = next(tr for tr in p.ops.trains if tr.vehicle_id == vehicle)
                seg, nose, _ = p.ops.where(train, t_out - 1e-3)
                # Just before release the rear is still on the resource; at release it has cleared it.
                self.assertLessEqual(nose - ion.ION_LENGTH, r["s1"] + 0.05)
                seg2, nose2, _ = p.ops.where(train, t_out + 1e-3)
                if seg2 is seg:
                    self.assertGreaterEqual(nose2 - ion.ION_LENGTH, r["s1"] - 0.05)
                checked += 1
        self.assertGreater(checked, 50)

    def test_a_following_train_takes_the_other_platform_or_waits(self):
        for key in ("fairway", "conestoga"):
            other = "2"
            held = HOLD[key]
            for reassign, expect in ((True, "platform"), (False, "wait")):
                p = plan(held, reassign)
                after = [d for d in p.ops.decisions if d.terminal == key and d.t >= held["minute"] * 60
                         and d.trigger.startswith("Approaching")]
                first = after[0]
                rejected = [o for o in first.options if not o["feasible"]]
                self.assertTrue(any(f"platform {held['platform']}" in o["option"] for o in rejected), key)
                self.assertIn("occupied by", rejected[0]["reason"])
                if expect == "platform":
                    self.assertEqual(first.chosen, f"platform {other}")
                waited = [d for d in after if "wait" in d.chosen]
                if expect == "wait":
                    self.assertTrue(waited, f"{key}: without dispatch a later train waits at the signal")
                    self.assertTrue(waited[0].outcome.startswith("granted"))

    def test_one_vehicle_through_the_reversal(self):
        p = plan()
        turned = 0
        for train in p.ops.trains:
            segs = train.segments
            for a, b, c in zip(segs, segs[1:], segs[2:]):
                if not (a.kind == "move" and a.route and a.route.startswith("arrive") and b.kind == "stand"
                        and c.kind == "move" and c.route and c.route.startswith("depart")):
                    continue
                turned += 1
                self.assertEqual(b.t0, a.t1)
                self.assertEqual(c.t0, b.t1)
                self.assertNotEqual(a.trip_id, c.trip_id, "a turnback changes trip")
                self.assertNotEqual(a.lead, c.lead, "the other cab leads out")
                # Lights swap while standing: off at the arriving cab, then on at the other end.
                self.assertLess(b.t0, b.lights["off"])
                self.assertLess(b.lights["off"], b.lights["on"])
                self.assertLessEqual(b.lights["on"], b.t1)
                # The carbody does not move: same footprint before and after.
                key = a.terminal
                arr = TERMINALS[key]["routes"][a.route]
                dep = TERMINALS[key]["routes"][c.route]
                self.assertEqual(a.route.split(":")[1], c.route.split(":")[1])
                self.assertLess(metres(pos(arr["path"], a.s1), pos(dep["path"], c.s0 - ion.ION_LENGTH)), 0.05)
        self.assertGreater(turned, 20)


class DispatchTests(unittest.TestCase):
    def test_a_hold_changes_departures_and_dispatch_reassigns_a_trip(self):
        for key in ("fairway", "conestoga"):
            clear = plan()
            held = plan(HOLD[key])
            no_dispatch = plan(HOLD[key], reassign=False)
            self.assertEqual(clear.ops.metrics(window(clear))["max_departure_delay_s"], 0.0)
            swaps = [d for d in held.ops.decisions if d.chosen.startswith("swap") and d.terminal == key]
            self.assertTrue(swaps, key)
            d = swaps[0]
            self.assertTrue(any(o["option"].startswith("keep") for o in d.options))
            self.assertTrue(any("spare" in o["option"] and not o["feasible"] for o in d.options))
            self.assertIn("left platform", d.outcome)
            # The trip went with another train, and left earlier than it could have.
            trip = next(t for t in held.trips if t.train_id == d.trip_id)
            base = next(t for t in no_dispatch.trips if t.train_id == d.trip_id)
            self.assertNotEqual(trip.vehicle_id, d.vehicle_id)
            self.assertLess(trip.depart_s, base.depart_s)
            m_held = held.ops.metrics(window(held))
            m_base = no_dispatch.ops.metrics(window(no_dispatch))
            self.assertLess(m_held["avg_departure_delay_s"], m_base["avg_departure_delay_s"])
            self.assertLessEqual(m_held["platform_queue_s"], m_base["platform_queue_s"])

    def test_the_hold_reaches_downstream_transfers(self):
        clear, held, no_dispatch = plan(), plan(HOLD["fairway"]), plan(HOLD["fairway"], reassign=False)

        def hub(p):
            return {(trip.train_id, stop): at for at, trip, stop, riders in p.hub_arrivals() if riders}

        a, b, c = hub(clear), hub(held), hub(no_dispatch)
        later_with = sum(1 for k in a if k in b and b[k] > a[k] + 30)
        later_without = sum(1 for k in a if k in c and c[k] > a[k] + 30)
        self.assertGreater(later_without, 0, "the hold makes transfer arrivals late")
        worst_with = max(b[k] - a[k] for k in a if k in b)
        worst_without = max(c[k] - a[k] for k in a if k in c)
        self.assertLess(worst_with, worst_without, "dispatch limits how late riders reach the bus")

    def test_trips_leave_in_order_with_spacing(self):
        for hold in (None, HOLD["fairway"]):
            for reassign in (True, False):
                p = plan(hold, reassign)
                for direction in ("north", "south"):
                    trips = sorted((t for t in p.trips if t.direction == direction and t.station_times),
                                   key=lambda t: t.scheduled_s)
                    for a, b in zip(trips, trips[1:]):
                        self.assertGreaterEqual(b.depart_s - a.depart_s, ion.MIN_SEPARATION_S - 1e-6)

    def test_same_scenario_same_decisions(self):
        a = json.dumps(plan(HOLD["conestoga"]).schedule(), sort_keys=True)
        b = json.dumps(plan(HOLD["conestoga"]).schedule(), sort_keys=True)
        self.assertEqual(a, b)

    def test_every_decision_lists_its_options_and_outcome(self):
        p = plan(HOLD["fairway"])
        for d in p.ops.decisions:
            if not (0 <= d.t < 3600):
                continue
            self.assertTrue(d.options)
            self.assertTrue(d.vehicle_id)
            if d.chosen.startswith("swap") or d.trigger.startswith("Approaching"):
                self.assertTrue(d.outcome, d.id)
            for o in d.options:
                if not o["feasible"]:
                    self.assertTrue(o["reason"], f"{d.id}: {o['option']} rejected without a reason")


class ProfileTests(unittest.TestCase):
    def test_time_at_inverts_distance(self):
        route = TERMINALS["fairway"]["routes"]["arrive:2"]
        prof = rail_ops.move_profile("fairway", "arrive:2", 0.0, route["stop_m"])
        for s in (0.5, 10.0, 400.0, 1500.0, route["stop_m"] - 1.0):
            self.assertAlmostEqual(prof.distance(prof.time_at(s)), s, places=6)
        self.assertEqual(prof.speed(prof.duration), 0.0)
        # The crossover is taken at the mapped or modelled switch speed.
        r = next(x for x in route["resources"] if x["id"] == "crossover_b")
        self.assertLessEqual(prof.speed(prof.time_at((r["s0"] + r["s1"]) / 2)), 25 / 3.6 + 1e-6)


if __name__ == "__main__":
    unittest.main()
