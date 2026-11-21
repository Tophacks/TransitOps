"""Route 7 driven by the simulator (line_driver.py, lines.py zones).

At each stop the bus brakes progressively, creeps into position, stops fully,
then opens its doors; it waits for a gap and rolls out. The motion is the
corridor buses' (accelerating, cruising, curves, braking); where shared traffic
is modelled the UW Station lane rules decide when it can pull out: at the King /
KCI curbside stop and the Fairway Station bay exit.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import corridor  # noqa: E402
import line_driver as ld  # noqa: E402
import lines  # noqa: E402
from ion import IonPlan  # noqa: E402


def run(cars="heavy", minutes=120):
    svc = lines.LineService(("7",), seed=17, duration_s=minutes * 60.0, cars=cars,
                            ion_plan=IonPlan(duration_min=minutes, transfer_demand=110))
    svc.advance_to(minutes * 60.0)
    svc.finish()
    return svc


def samples(bus, t0, t1):
    return [x for x in bus.trace if t0 <= x[0] <= t1]


class StopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svc = run()
        cls.served = [(b, x) for b in cls.svc.buses for x in b.stops_log
                      if not x["passed"] and x.get("doors") is not None and x["arr"] > 60]

    def test_progressive_braking_then_a_creep_then_a_full_stop(self):
        checked = 0
        for bus, x in self.served[:80]:
            before = samples(bus, x["arr"] - 15, x["arr"] + 1.0)
            if len(before) < 10 or before[0][2] < 3.0:
                continue                      # it was already crawling (queue, a leader)
            speeds = [v for _, _, v in before]
            decel = [speeds[i - 1] - speeds[i] for i in range(1, len(speeds))]
            self.assertTrue(all(d >= -0.05 for d in decel[-8:]), (bus.vehicle_id, x["stop"], speeds))
            self.assertLessEqual(max(decel), ld.HARD_DECEL + 1e-6)
            self.assertEqual(before[-1][2], 0.0)
            # The last metres at creep speed.
            creep = [v for t, s, v in before if before[-1][1] - s <= ld.CREEP_M and v > 0]
            self.assertTrue(all(v <= ld.CREEP_V + 0.3 for v in creep), creep)
            checked += 1
        self.assertGreater(checked, 20)

    def test_doors_open_only_after_the_bus_is_stationary(self):
        for bus, x in self.served:
            # Stationary for at least DOORS_AFTER_STOP_S before the doors open, and while they are.
            settle = samples(bus, x["doors"] - ld.DOORS_AFTER_STOP_S, x["dep"])
            self.assertTrue(settle and all(v == 0.0 for _, _, v in settle), (bus.vehicle_id, x["stop"], settle[:3]))
            self.assertGreaterEqual(x["doors"], x["arr"])

    def test_rolls_out_with_the_corridor_pull_away(self):
        checked = 0
        for bus, x in self.served[:80]:
            after = samples(bus, x["dep"], x["dep"] + 8)
            moving = [v for _, _, v in after]
            if len(moving) < 6 or max(moving) < 2:
                continue
            gains = [moving[i] - moving[i - 1] for i in range(1, len(moving))]
            self.assertLessEqual(max(gains), corridor.BUS_ACCEL + 1e-6)
            checked += 1
        self.assertGreater(checked, 20)

    def test_never_faster_than_the_corridor_rules_allow(self):
        for bus in self.svc.buses:
            for i, (t, s, v) in enumerate(bus.trace):
                self.assertLessEqual(v, corridor.BUS_RUSH_SPEED + 1e-6)

    def test_schedule_stops_and_vehicle_ids_are_route_7s(self):
        trips = {t["trip_id"]: t for t in lines.LINES["routes"]["7"]["trips"]}
        for bus in self.svc.buses:
            for row in bus.trips_log:
                self.assertIn(row[2], trips)
                self.assertEqual(row[5], "7")
                self.assertEqual(row[4], trips[row[2]]["headsign"])
            by = {}
            for x in bus.stops_log:
                by.setdefault(x["trip"], []).append(x["stop"])
            for tid, got in by.items():
                pat = next(p for p in lines.LINES["routes"]["7"]["patterns"] if p["id"] == trips[tid]["pattern"])
                order = [st["stop"] for st in pat["stops"]]
                idx = [order.index(sid) for sid in got]
                self.assertEqual(idx, sorted(idx))


class GapTests(unittest.TestCase):
    """Traffic makes the bus wait before pulling out."""

    @classmethod
    def setUpClass(cls):
        cls.svc = run()
        cls.waits = [d for d in cls.svc.decisions if d["trigger"].startswith("Pulling out of")]

    def check(self, kind):
        found = [d for d in self.waits if (kind in d["trigger"]) and d.get("blockers")]
        self.assertTrue(found, kind)
        d = max(found, key=lambda d: int(d["outcome"].split("after waiting ")[1].split(" s")[0]))
        waited = int(d["outcome"].split("after waiting ")[1].split(" s")[0])
        self.assertGreater(waited, 2)
        bus = next(b for b in self.svc.buses if b.vehicle_id == d["vehicle_id"])
        hold = samples(bus, d["t"] + 1, d["t"] + waited - 1)
        self.assertTrue(hold and all(v == 0.0 for _, _, v in hold), "stationary while it waits")
        after = samples(bus, d["t"] + waited + 1, d["t"] + waited + 10)
        self.assertTrue(after and after[-1][2] > 1.0, "then it rolls out")
        # The cars it waited for were really in the lane, at the merge, then.
        zone = next(z for z in self.svc.zones if z["stop_name"] in d["trigger"])
        cars = {v.id: v for v in zone["lane_model"].vehicles}
        for cid in d["blockers"]:
            car = cars[cid]
            near = [s for t, s in car.trace if d["t"] - 1 <= t <= d["t"] + waited + 1]
            self.assertTrue(near, cid)
            self.assertTrue(any(abs(s - zone["merge_rear_m"]) < 120 for s in near), (cid, near[:3]))
        return d

    def test_a_curbside_stop_where_traffic_makes_the_bus_wait(self):
        d = self.check("curbside stop at King / Kitchener-Waterloo Collegiate")
        self.assertIn("UW Station lane rule", d["control"])

    def test_a_terminal_bay_where_traffic_makes_the_bus_wait(self):
        d = self.check("bay exit at Fairway Station onto Fairway Road South")
        bus = next(b for b in self.svc.buses if b.vehicle_id == d["vehicle_id"])
        zone = next(z for z in self.svc.zones if z["kind"] == "bay")
        at = [s for t, s, v in bus.trace if abs(t - d["t"]) <= 1]
        self.assertTrue(any(abs(s - zone["hold_s"]) < 0.1 for s in at))      # stopped at the bay exit

    def test_light_traffic_waits_less(self):
        light = run("light")
        total = lambda svc: sum(int(d["outcome"].split("after waiting ")[1].split(" s")[0])  # noqa: E731
                                for d in svc.decisions if d["trigger"].startswith("Pulling out of") and d["outcome"])
        self.assertLess(total(light), total(self.svc))


class BreakdownTests(unittest.TestCase):
    def test_buses_behind_a_broken_down_bus_drive_round_it(self):
        import main
        r = main.run_case(main.SimulationRequest(duration_min=120, breakdown_minute=20, breakdown_kind="out_of_service",
                                                 breakdown_target="line"), True)
        vs = r["lines"]["vehicles"]
        self.assertTrue(any(v["broken"] for v in vs))
        for v in vs:
            if v["broken"] or not v["trace"]:
                continue
            # Nobody sits stationary behind it for the rest of the run.
            late = [x for x in v["trace"] if x[0] > 1500]
            self.assertTrue(not late or max(x[1] for x in late) - min(x[1] for x in late) > 50, v["vehicle_id"])
        m = r["lines"]["metrics"]
        self.assertEqual(m["trips_covered"], m["trips_scheduled"])


class TraceTests(unittest.TestCase):
    def test_the_map_reconstructs_the_motion_from_the_simplified_trace(self):
        svc = run("moderate", 60)
        bus = max(svc.buses, key=lambda b: len(b.trace))
        raw = bus.trace
        kept = ld.simplify(raw)
        self.assertLess(len(kept), len(raw) * 0.6)
        j = 0
        for t, s, v in raw:
            while j + 1 < len(kept) and kept[j + 1][0] < t:
                j += 1
            a, b = kept[j], kept[min(j + 1, len(kept) - 1)]
            if not a[0] <= t <= b[0] or b[0] - a[0] > 1.5 and not (a[2] == 0 and b[2] == 0):
                continue
            ps, pv = ld._between(a, b, t) if b[0] > a[0] else (b[1], b[2])
            self.assertLess(abs(ps - s), 0.06)


if __name__ == "__main__":
    unittest.main()
