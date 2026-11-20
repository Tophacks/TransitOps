"""Dutton Drive yard: the OSM track graph, storage positions, a finite fleet with
persistent IDs, spares pulling out onto the line and faulty trains returning,
and dispatch weighing a yard release against reassigning a train on the line."""
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import rail_ops as ro  # noqa: E402
import yard_ops  # noqa: E402
from ion import IonPlan  # noqa: E402

YARD = ro.NETWORK["yard"]
FAULT = {"terminal": "conestoga", "minute": 15}
BLOCK = {"lead": "north", "from_minute": 10, "to_minute": 50}


def run(**kw):
    return IonPlan(duration_min=60, transfer_demand=110, delay_min=4, **kw).ops


def train(ops, vid):
    return next(t for t in ops.trains if t.vehicle_id == vid)


def states(tr):
    return [x["state"] for x in tr.state_log]


def metres(a, b):
    import math as m
    dx = (a[0] - b[0]) * 111320 * m.cos(m.radians(a[1]))
    dy = (a[1] - b[1]) * 110540
    return m.hypot(dx, dy)


def nose(seg, s):
    import corridor
    path = corridor.DATA["ion"][seg.line]["path"] if seg.line else ro.route_def(seg.terminal, seg.route)["path"]
    return corridor._lerp_path(path, s)


class YardGraphTests(unittest.TestCase):
    def test_both_mapped_leads_join_the_running_track(self):
        leads = YARD["leads"]
        self.assertEqual(leads["north"]["node"], 7020996626)
        self.assertEqual(leads["south"]["node"], 7020977372)
        self.assertTrue(all(lead["track"] == "north" for lead in leads.values()))
        # The north lead points with northbound traffic, the south lead against it.
        self.assertTrue(leads["north"]["with_traffic"])
        self.assertFalse(leads["south"]["with_traffic"])

    def test_storage_tracks_reach_both_leads_and_have_positions(self):
        tracks = YARD["tracks"]
        self.assertEqual(len(tracks), 8)
        for t in tracks:
            self.assertEqual(sorted(t["leads"]), ["north", "south"], t["id"])
            self.assertGreaterEqual(t["positions"], 1)
            # Positions fit between the stop short of the end and the fouling point.
            used = YARD["modelled"]["stop_short_m"] + t["positions"] * 41.8 + (t["positions"] - 1) * YARD["modelled"]["store_gap_m"]
            self.assertLessEqual(used, t["switch_m"] - YARD["modelled"]["foul_m"] + 1e-6)
        self.assertEqual(sum(1 for t in tracks if t["kind"] == "shop"), 4)
        self.assertEqual({t["end"] for t in tracks if t["kind"] == "outdoor" and t["way"] in (750968491, 750968493, 750968496)},
                         {"buffer stop"})

    def test_routes_to_service_follow_the_valid_moves(self):
        routes = {r["id"]: r for r in YARD["routes"]}
        out_n = routes["yard-out:north:750968491"]
        self.assertEqual(out_n["to_station"], "Northfield Station")
        self.assertEqual(out_n["crossovers"], [])
        out_s = routes["yard-out:south:750968491"]
        # Against the northbound track's direction to the Albert St crossover, then southbound.
        self.assertEqual([c["way"] for c in out_s["crossovers"]], [754590776])
        self.assertEqual(out_s["to_station"], "Research and Technology Station")
        in_n = routes["yard-in:north:750968491"]
        self.assertEqual([c["way"] for c in in_n["crossovers"]], [750968494])
        self.assertEqual(in_n["from_station"], "Northfield Station")
        self.assertEqual(routes["yard-in:south:750968491"]["from_station"], "Research and Technology Station")
        for r in routes.values():
            # Every route is one continuous path.
            pts = r["path"]
            for a, b in zip(pts, pts[1:]):
                self.assertAlmostEqual(metres(a, b), b[2] - a[2], delta=0.2 + 0.01 * (b[2] - a[2]), msg=r["id"])
            self.assertAlmostEqual(pts[-1][2], r["length_m"], delta=0.05)


class FleetTests(unittest.TestCase):
    def test_finite_fleet_with_persistent_ids(self):
        ops = run()
        ids = sorted(t.vehicle_id for t in ops.trains)
        self.assertEqual(len(ids), yard_ops.FLEET_TOTAL)
        self.assertEqual(len(set(ids)), len(ids))
        yard = {t.vehicle_id: t.state for t in ops.yard.trains}
        self.assertEqual(yard, {
            "LRV512": "ready", "LRV513": "stored", "LRV514": "stored", "LRV515": "unavailable",
        })
        # Stored trains never share a position.
        self.assertEqual(len(set(ops.yard.occupied)), len(ops.yard.occupied))

    def test_no_yard_movement_without_a_reason(self):
        ops = run()
        self.assertEqual(ops.yard.log, [])


class FaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ops = run(fault=FAULT)

    def test_dispatch_compares_yard_release_with_reassignment(self):
        d = next(x for x in self.ops.decisions if "has no train" in x.trigger)
        names = [o["option"] for o in d.options]
        self.assertTrue(any(n.startswith("reassign the next arriving train") for n in names))
        self.assertTrue(any("ready spare LRV512" in n for n in names))
        self.assertTrue(any("stored spare LRV513" in n for n in names))
        unavailable = next(o for o in d.options if o["option"] == "send LRV515 from Dutton")
        self.assertFalse(unavailable["feasible"])
        south = next(o for o in d.options if o["option"] == "spare via the south lead")
        self.assertFalse(south["feasible"])
        # Preparation, the route via the lead and the departure are in the reason.
        spare = next(o for o in d.options if "ready spare LRV512" in o["option"] and "for ION-S4" in o["option"])
        self.assertIn("3 min preparation (modelled)", spare["reason"])
        self.assertIn("via the north lead", spare["reason"])
        self.assertEqual(d.chosen, "reassign LRV504 to ION-S3, send ready spare LRV512 from Dutton for ION-S4")

    def test_spare_leaves_its_storage_track_and_takes_the_trip(self):
        spare = train(self.ops, "LRV512")
        self.assertEqual(states(spare), ["ready", "pulling out", "in service"])
        out = next(m for m in self.ops.yard.log if m["vehicle_id"] == "LRV512")
        self.assertEqual(out["kind"], "out")
        self.assertTrue(out["route"].startswith("yard-out:north:"))
        trip = next(t for t in self.ops.plan.trips if t.train_id == "ION-S4")
        self.assertEqual(trip.vehicle_id, "LRV512")
        self.assertLessEqual(trip.depart_s - trip.scheduled_s, 1.0)
        # It drove there: storage, pull-out, Northfield, the Conestoga arrival, the trip.
        kinds = [(s.kind, s.terminal, (s.route or "")[:9]) for s in spare.segments[:5]]
        self.assertEqual(kinds[0], ("stand", "network", "yard-out:"))
        self.assertEqual(kinds[1], ("move", "network", "yard-out:"))
        self.assertTrue(any(s.terminal == "conestoga" and s.route.startswith("arrive") for s in spare.segments))

    def test_the_pull_out_waits_for_an_empty_northfield_block(self):
        waits = [d for d in self.ops.decisions if d.vehicle_id == "LRV512" and d.chosen == "wait on the storage track"]
        self.assertTrue(waits)
        self.assertIn("Northfield block occupied by", waits[0].options[0]["reason"])
        grant = next(d for d in self.ops.decisions if d.vehicle_id == "LRV512" and d.chosen.startswith("pull out"))
        self.assertGreater(grant.t, waits[0].t)
        # No other train was in the northbound Northfield block during the pull-out.
        out = next(m for m in self.ops.yard.log if m["vehicle_id"] == "LRV512")
        nf = self.ops.stop_index("north", "Northfield Station")
        for other in self.ops.trains:
            if other.vehicle_id == "LRV512":
                continue
            for s in other.segments:
                if s.kind == "run" and s.line == "north" and s.leg == nf - 1:
                    self.assertFalse(s.t0 < out["t1"] and s.t1 > out["t0"], other.vehicle_id)

    def test_the_faulty_train_returns_to_a_shop_track(self):
        bad = train(self.ops, "LRV502")
        self.assertEqual(states(bad)[-3:], ["unavailable", "returning", "unavailable"])
        back = next(m for m in self.ops.yard.log if m["vehicle_id"] == "LRV502")
        self.assertTrue(back["route"].startswith("yard-in:north:"))
        self.assertEqual(yard_ops.TRACKS[back["track"]]["kind"], "shop")
        self.assertIn((back["track"], back["position"] - 1), self.ops.yard.occupied)
        self.assertEqual(self.ops.yard.occupied[(back["track"], back["position"] - 1)], "LRV502")

    def test_vehicle_paths_are_continuous_through_the_yard(self):
        for vid in ("LRV502", "LRV512"):
            tr = train(self.ops, vid)
            for a, b in zip(tr.segments, tr.segments[1:]):
                self.assertAlmostEqual(a.t1, b.t0, delta=1e-6, msg=vid)
                if a.terminal == "network" or b.terminal == "network":
                    end, start = nose(a, a.s1), nose(b, b.s0)
                    self.assertLess(metres(end, start), 6.0, f"{vid} {a.kind}->{b.kind} at {ro.fmt(b.t0)}")

    def test_same_scenario_same_yard_decisions(self):
        other = run(fault=FAULT)
        self.assertEqual([d.to_dict() for d in other.decisions], [d.to_dict() for d in self.ops.decisions])
        self.assertEqual(other.yard.log, self.ops.yard.log)


class BlockedLeadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ops = run(fault=FAULT, yard_block=BLOCK)

    def test_dispatch_reassigns_when_the_lead_is_blocked(self):
        d = next(x for x in self.ops.decisions if "has no train" in x.trigger)
        spare = next(o for o in d.options if "ready spare LRV512" in o["option"] and "for ION-S4" in o["option"])
        self.assertIn("blocked by track work (scenario) until 50:00", spare["reason"])
        self.assertEqual(d.chosen, "reassign the next arriving train LRV504")
        self.assertEqual(train(self.ops, "LRV512").state, "ready")

    def test_nothing_uses_the_blocked_lead(self):
        for m in self.ops.yard.log:
            if m["route"].split(":")[1] == "north":
                self.assertFalse(m["t0"] < 50 * 60 and m["t1"] > 10 * 60, m)

    def test_the_faulty_train_waits_at_the_terminal_until_its_route_is_usable(self):
        wait = next(d for d in self.ops.decisions if d.vehicle_id == "LRV502" and d.chosen == "stay at the terminal platform")
        self.assertIn("lead north held by track work (scenario)", wait.options[0]["reason"])
        back = next(m for m in self.ops.yard.log if m["vehicle_id"] == "LRV502")
        self.assertGreaterEqual(back["t0"], 50 * 60)

    def test_blocking_the_lead_changes_the_outcome(self):
        open_ = run(fault=FAULT)
        m_open = open_.metrics([t for t in open_.plan.trips if 0 <= t.scheduled_s < 3600])
        m_block = self.ops.metrics([t for t in self.ops.plan.trips if 0 <= t.scheduled_s < 3600])
        self.assertGreater(m_block["avg_departure_delay_s"], m_open["avg_departure_delay_s"])


class PolicyTests(unittest.TestCase):
    def test_holds_only_never_sends_a_spare(self):
        plan = IonPlan(duration_min=60, transfer_demand=110, delay_min=4, fault=FAULT, reassign=False)
        self.assertFalse([m for m in plan.ops.yard.log if m["kind"] == "out"])
        d = next(x for x in plan.ops.decisions if "has no train" in x.trigger)
        self.assertTrue(any(o["reason"] == "dispatch policy off (holds only)" for o in d.options))

    def test_fault_timing_changes_the_response(self):
        early = run(fault={"terminal": "conestoga", "minute": 5})
        late = run(fault=FAULT)
        withdrawn = lambda ops: next(d.vehicle_id for d in ops.decisions if d.chosen == "withdraw and return to Dutton")  # noqa: E731
        self.assertNotEqual(withdrawn(early), withdrawn(late))


if __name__ == "__main__":
    unittest.main()
