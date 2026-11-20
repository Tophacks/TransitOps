"""Drivers, private-car traffic and recovery through Control (#19 scope):

1. the same UW departure in light and heavy Phillip Street traffic: the bus
   waits for a real gap and names the blocking vehicle;
2. a berth overshoot with the backing path clear (reverse granted) and
   occupied (denied, missed stop recorded);
3. a missed berth entry and a legal unintended turn, continued from where the
   bus actually is;
4. train recovery granted only with a clear, reserved route; otherwise an
   authorized forward alternative;
5. the same seed reproduces the same mistakes and decisions.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import rail_ops  # noqa: E402
import road_traffic  # noqa: E402
import simulation_engine as se  # noqa: E402
from drivers import Driver, Errors  # noqa: E402
from ion import IonPlan  # noqa: E402
from models import Bus  # noqa: E402

OVERSHOOT = {"mode": "demo", "kind": "overshoot", "vehicle": "B101", "visit": 1, "where": "P6", "metres": 6.0}


def run(intelligent=True, **kw):
    plan_kw = {k: kw.pop(k) for k in list(kw) if k in ("rail_overshoot",)}
    plan = IonPlan(duration_min=60, headway_min=8, transfer_demand=110, delay_min=4, **plan_kw)
    s = se.TransitSimulation([Bus(f"B{101 + i}") for i in range(3)], plan, intelligent=intelligent, duration=60,
                             traffic_multiplier=1.15, seed=17, **kw)
    m = s.run()
    return s, m


def uw(s, vehicle="B101"):
    return [d for d in s.decisions if d["terminal"] == "uw" and d["vehicle_id"] == vehicle]


class DriverProfileTests(unittest.TestCase):
    def test_profiles_are_seeded_and_within_the_rules(self):
        a, b = Driver("B101", 17), Driver("B101", 17)
        self.assertEqual(a.describe(), b.describe())
        self.assertNotEqual(Driver("B101", 17).describe(), Driver("B101", 18).describe())
        for vid in ("B101", "B102", "GRT-1", "car-9"):
            d = Driver(vid, 17)
            self.assertTrue(0.88 <= d.pace <= 1.0)
            self.assertTrue(1.0 <= d.headway <= 1.8)
            self.assertIn(d.reaction, (0, 1, 2))

    def test_following_keeps_the_standstill_gap(self):
        road = road_traffic.Phillip("heavy", seed=3)
        for t in range(600):
            road.step(float(t))
            cars = road.ordered()
            for lead, follow in zip(cars, cars[1:]):
                self.assertGreaterEqual(lead.rear - follow.front, road_traffic.ROAD_GAP - 1e-6)


class GapTests(unittest.TestCase):
    """Acceptance 1."""

    def test_heavy_traffic_makes_the_same_departure_wait_for_a_real_gap(self):
        light, _ = run(uw_cars="light")
        heavy, _ = run(uw_cars="heavy")

        def waits(s):
            out = {}
            for d in s.decisions:
                if d["terminal"] == "uw" and d["chosen"] == "yield and wait" and d["vehicle_id"].startswith("B1") \
                        and "after waiting" in d["outcome"]:
                    out[d["vehicle_id"]] = out.get(d["vehicle_id"], 0) + int(d["outcome"].split("after waiting ")[1].split(" s")[0])
            return out
        wl, wh = waits(light), waits(heavy)
        bus = max(wh, key=wh.get)
        self.assertGreater(wh[bus], wl.get(bus, 0), (wl, wh))
        d = next(d for d in heavy.decisions if d["vehicle_id"] == bus and d["chosen"] == "yield and wait")
        self.assertTrue(d["blockers"])
        self.assertIn(d["blockers"][0], d["outcome"])
        # The blocker was really on Phillip Street at that second, near the merge.
        car = next(v for v in heavy.uw.road.vehicles if v.id == d["blockers"][0])
        self.assertTrue(any(abs(t - d["t"]) <= 30 for t, _ in car.trace))
        # Same scheduled departure: the bus reached the station at the same second in both runs.
        enter = lambda s: next(a.t_enter for a in s.uw.agents if a.id == bus)  # noqa: E731
        self.assertEqual(enter(light), enter(heavy))

    def test_no_cars_no_wait(self):
        s, _ = run(uw_cars=0)
        self.assertFalse([d for d in s.decisions if d["chosen"] == "yield and wait" and d["vehicle_id"].startswith("B1")])


class OvershootTests(unittest.TestCase):
    """Acceptance 2."""

    def test_reverse_granted_when_the_backing_path_is_clear(self):
        s, m = run(driver_errors=OVERSHOOT)
        ask = next(d for d in uw(s) if d["trigger"].startswith("B101 asks to reverse"))
        self.assertTrue(ask["chosen"].startswith("reverse"))
        self.assertIn("granted", ask["control"])
        self.assertIn("doors opened", ask["outcome"])
        agent = next(a for a in s.uw.agents if a.id == "B101")
        self.assertEqual(agent.missed, [])
        # Doors only at a valid position: the dwell started after the reverse.
        stop = next(d for d in uw(s) if d["trigger"].startswith("Stopped 6.0 m past"))
        self.assertIn("open the doors here", [o["option"] for o in stop["options"] if not o["feasible"]])

    def test_reverse_denied_with_a_bus_behind(self):
        s, m = run(driver_errors={**OVERSHOOT, "follower": True})
        ask = next(d for d in uw(s) if d["trigger"].startswith("B101 asks to reverse"))
        self.assertFalse(ask["chosen"].startswith("reverse"))
        self.assertIn("GRT-behind-B101", ask["control"])
        self.assertTrue(ask["chosen"].startswith("record the missed stop"))
        self.assertEqual(m.missed_stops, 1)
        agent = next(a for a in s.uw.agents if a.id == "B101")
        self.assertEqual(agent.missed, ["1078"])

    def test_missed_stop_costs_transfers(self):
        _, clear = run(driver_errors=OVERSHOOT)
        _, blocked = run(driver_errors={**OVERSHOOT, "follower": True})
        self.assertLess(blocked.ion_transfers_boarded, clear.ion_transfers_boarded)


class RecoveryTests(unittest.TestCase):
    """Acceptance 3: continue from the actual location."""

    def test_missed_berth_entry_sets_down_at_the_next_stop(self):
        s, m = run(driver_errors={"mode": "demo", "kind": "missed_berth", "vehicle": "B101", "visit": 1, "where": "P4"})
        d = next(d for d in uw(s) if "missed the turn" in d["trigger"])
        self.assertTrue(d["chosen"].startswith("set down at platform 6"))
        self.assertFalse(next(o for o in d["options"] if o["option"].startswith("reverse"))["feasible"])
        agent = next(a for a in s.uw.agents if a.id == "B101")
        self.assertTrue(all(b.startswith("P6") for b, _, _ in agent.berth_log))
        self.assertEqual(m.missed_stops if hasattr(m, "missed_stops") else 0, 0)

    def test_wrong_turn_reroutes_on_legal_roads_to_the_next_stop(self):
        s, _ = run(driver_errors={"mode": "demo", "kind": "wrong_turn", "vehicle": "B101", "visit": 1, "where": "exit"})
        turn = next(d for d in uw(s) if d["chosen"] == "turn left onto northbound Phillip St")
        self.assertIn("legal", next(o for o in turn["options"] if o["option"].startswith("turn left"))["reason"])
        plan = next(d for d in uw(s) if "off its trip" in d["trigger"])
        self.assertTrue(plan["chosen"].startswith("route to Waterloo Public Square"))
        agent = next(a for a in s.uw.agents if a.id == "B101")
        trace = agent.detour_trace
        self.assertEqual(trace[0][1], 0.0)
        self.assertAlmostEqual(trace[-1][1], road_traffic.ROADS["wrong_turn"]["length_m"], delta=0.01)
        # The engine continues the trip from Waterloo Public Square.
        ev = [e for e in s.events if e.get("message", "").startswith("B101 at Waterloo Public Square")]
        self.assertTrue(ev)
        self.assertGreaterEqual(ev[0]["minute"], int(agent.exit_s // 60))
        # A wrong turn is never a shortcut: the re-route is at least as slow as
        # its length at the corridor leg's own congested pace.
        import corridor
        leg_s = s.uw.detour_slowdown() * corridor.free_flow_seconds("UW", "WATERLOO_SQUARE")
        per_m = leg_s / corridor.segment("UW", "WATERLOO_SQUARE")["length_m"]
        self.assertGreater(agent.exit_s - agent.detour["t0"], 0.9 * per_m * road_traffic.ROADS["wrong_turn"]["length_m"])

    def test_a_bus_that_has_left_does_not_block_the_exit(self):
        """Once its whole length is past the exit, a bus on Phillip Street or on
        a re-route no longer occupies Transit Plaza Road."""
        base, _ = run()
        wrong, _ = run(driver_errors={"mode": "demo", "kind": "wrong_turn", "vehicle": "B101", "visit": 1, "where": "exit"})
        exits = lambda s: {a.id: a.exit_s for a in s.uw.agents if a.id != "B101" and a.exit_s}  # noqa: E731
        eb, ew = exits(base), exits(wrong)
        self.assertEqual(set(eb), set(ew))
        # Phillip Street traffic differs slightly without B101 on it; nobody is held behind it.
        self.assertLessEqual(max(abs(eb[k] - ew[k]) for k in eb), 10.0)


class TrainRecoveryTests(unittest.TestCase):
    """Acceptance 4."""

    def test_reverse_granted_on_its_own_right_of_way_with_a_clear_path(self):
        plan = IonPlan(duration_min=60, transfer_demand=110, delay_min=4,
                       rail_overshoot={"station": "Research and Technology Station", "direction": "north",
                                       "after_minute": 10, "metres": 12})
        d = next(x for x in plan.ops.decisions if x.trigger.endswith("asks to recover at Research and Technology"))
        self.assertTrue(d.chosen.startswith("reverse"))
        self.assertIn("reserved", d.control)
        train = next(t for t in plan.ops.trains if t.vehicle_id == d.vehicle_id)
        creeps = [s for s in train.segments if s.kind == "creep"]
        self.assertEqual(len(creeps), 2)
        self.assertAlmostEqual(creeps[0].s1 - creeps[0].s0, 12.0)
        self.assertAlmostEqual(creeps[1].s1 - creeps[1].s0, -12.0)

    def test_no_reverse_on_a_street_running_platform(self):
        plan = IonPlan(duration_min=60, transfer_demand=110, delay_min=4,
                       rail_overshoot={"station": "Waterloo Public Square Station", "direction": "north",
                                       "after_minute": 10, "metres": 12})
        d = next(x for x in plan.ops.decisions if "asks to recover at Waterloo Public Square" in x.trigger)
        self.assertTrue(d.chosen.startswith("continue to"))
        self.assertIn("street-running", d.control)
        trip = next(t for t in plan.trips if t.train_id == d.trip_id)
        self.assertIn("Waterloo Public Square Station", trip.skipped)
        self.assertNotIn("Waterloo Public Square Station", trip.station_times)

    def test_a_train_behind_blocks_the_reverse(self):
        plan = IonPlan(duration_min=60, transfer_demand=0)
        ops = plan.ops
        line = plan.lines["north"]
        i = ops.stop_index("north", "Research and Technology Station")
        me = next(t for t in ops.trains if t.trip is not None)
        other = next(t for t in ops.trains if t is not me)
        for tr in ops.trains:
            if tr is not me:
                tr.segments = []          # an otherwise empty line, to isolate the check
        t = 1200.0
        # Another train standing just behind the platform at t.
        other.segments = [rail_ops.Segment("stand", 0.0, 1e9, "A", None, line="north", leg=i - 1,
                                           s0=line.stops[i][1] - 80.0, s1=line.stops[i][1] - 80.0)]
        area_ok, blockers = ops.reverse_check(me, "north", i, line.stops[i][1], 12.0, t)
        self.assertTrue(area_ok)
        self.assertEqual([b[0] for b in blockers], [other.vehicle_id])
        other.segments = []
        self.assertEqual(ops.reverse_check(me, "north", i, line.stops[i][1], 12.0, t)[1], [])


class ReproducibilityTests(unittest.TestCase):
    """Acceptance 5."""

    def test_same_seed_same_mistakes_and_decisions(self):
        spec = {"mode": "seeded", "rate": 0.5, "seed": 5}
        a, ma = run(driver_errors=dict(spec))
        b, mb = run(driver_errors=dict(spec))
        self.assertTrue(a.uw.errors.used)
        self.assertEqual(a.uw.errors.used, b.uw.errors.used)
        self.assertEqual([d for d in a.decisions if d["terminal"] == "uw"], [d for d in b.decisions if d["terminal"] == "uw"])
        self.assertEqual(ma.ion_transfers_boarded, mb.ion_transfers_boarded)
        c, _ = run(driver_errors={**spec, "seed": 6})
        self.assertNotEqual(a.uw.errors.used, c.uw.errors.used)

    def test_errors_off_by_default(self):
        self.assertIsNone(Errors(None, 17).happens("overshoot", "B101", 1, "P6"))


class ApiTests(unittest.TestCase):
    """The scenario controls reach the simulator through /api/simulate."""

    def test_rail_overshoot_and_driver_errors_through_the_api(self):
        from fastapi.testclient import TestClient
        import main
        client = TestClient(main.app)
        r = client.post("/api/simulate", json={"rail_overshoot_station": "Waterloo Public Square Station"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any("asks to recover" in d["trigger"] for d in r.json()["ion"]["decisions"]))
        self.assertEqual(client.post("/api/simulate", json={"rail_overshoot_station": "Nowhere"}).status_code, 422)
        r = client.post("/api/simulate", json={"driver_error": "overshoot_blocked"})
        self.assertEqual(r.json()["smart"]["metrics"]["missed_stops"], 1)


if __name__ == "__main__":
    unittest.main()
