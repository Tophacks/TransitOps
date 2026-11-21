"""The shared bus driving physics (bus_physics.py): geometry, steering,
off-tracking, body sweep, braking, following, blocked paths and reverse."""
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bus_physics as bp  # noqa: E402

NOVA = bp.GEOMETRY["nova_lfs_40"]
XD40 = bp.GEOMETRY["nfi_xd40"]


def corner(radius, straight=60):
    """A right-angle right turn of `radius` metres between two straights."""
    pts = [(float(x), 0.0) for x in range(-straight, 1)]
    for i in range(1, 91):
        a = math.radians(i)
        pts.append((radius * math.sin(a), -radius * (1 - math.cos(a))))
    pts += [(radius, -radius - float(y)) for y in range(1, straight + 1)]
    return bp.Path(pts)


def drive(path, geometry, intent_of, seconds=120, v0=0.0, s0=None):
    veh = bp.Vehicle("t", geometry)
    bp.place_on_path(veh, path, geometry.wheelbase + 1 if s0 is None else s0)
    veh.v = v0
    ctl = bp.Controller(veh, bp.Driver(1.0, 1.3, 0))
    log, t = [], 0.0
    while t < seconds:
        r = ctl.step(intent_of(ctl, t), t)
        t += ctl.DT
        fx, fy = veh.front_axle
        _, e_front = path.project(fx, fy, (ctl.s or 0) + geometry.wheelbase, 15)
        log.append({"t": t, "s": r.s, "v": veh.v, "a": veh.a, "steer": veh.steer, "e_front": e_front,
                    "e_rear": r.d, "conflict": r.conflict, "x": veh.x, "y": veh.y, "yaw": veh.yaw,
                    "emergency": r.emergency})
        if r.s >= path.length - 2 * geometry.length:
            break
    return veh, log


class GeometryTests(unittest.TestCase):
    def test_published_dimensions_and_derived_lock(self):
        self.assertAlmostEqual(NOVA.wheelbase, 244 * 0.0254, places=2)
        self.assertAlmostEqual(XD40.wheelbase, 283.75 * 0.0254, places=2)
        for g in (NOVA, XD40):
            self.assertAlmostEqual(g.front_overhang + g.wheelbase + g.rear_overhang, g.length, places=2)
        # The lock is derived so the published turning radius comes back out.
        r_rear = NOVA.wheelbase / math.tan(NOVA.max_steer)
        self.assertAlmostEqual(math.hypot(r_rear + NOVA.track / 2, NOVA.wheelbase), 12.45, places=2)
        _, _, wall = XD40.swept_radii(XD40.max_steer)
        self.assertAlmostEqual(wall, 13.4, places=2)

    def test_reference_point_is_the_rear_axle(self):
        v = bp.Vehicle("t", NOVA, x=10.0, y=5.0, yaw=0.0)
        fp = v.footprint()
        xs = [p[0] for p in fp]
        self.assertAlmostEqual(max(xs), 10.0 + NOVA.wheelbase + NOVA.front_overhang)
        self.assertAlmostEqual(min(xs), 10.0 - NOVA.rear_overhang)
        self.assertAlmostEqual(v.front_axle[0], 10.0 + NOVA.wheelbase)


class SteeringTests(unittest.TestCase):
    def test_steering_angle_and_rate_limits_and_tracking_in_a_tight_turn(self):
        for g, radius in ((NOVA, 12.0), (XD40, 11.2)):
            path = corner(radius)
            _, log = drive(path, g, lambda c, t: bp.Intent(path, 11.0))
            steer = [x["steer"] for x in log]
            self.assertLessEqual(max(abs(s) for s in steer), g.max_steer + 1e-9)
            rates = [abs(b - a) / bp.Controller.DT for a, b in zip(steer, steer[1:])]
            self.assertLessEqual(max(rates), g.max_steer_rate + 1e-6)
            # the front axle stays on the lane path
            self.assertLess(max(abs(x["e_front"]) for x in log), 0.15, g.key)

    def test_rear_axle_off_tracks_inside_the_turn(self):
        radius = 15.0
        path = corner(radius)
        _, log = drive(path, NOVA, lambda c, t: bp.Intent(path, 11.0))
        inside = max(x["e_rear"] for x in log)      # right turn: inside is to the right (+)
        steady = radius - math.sqrt(radius ** 2 - NOVA.wheelbase ** 2)
        self.assertGreater(inside, steady * 0.8)
        self.assertLess(inside, steady * 1.15)

    def test_front_overhang_swings_outside_the_front_axle_path(self):
        radius = 13.0
        path = corner(radius)
        veh = bp.Vehicle("t", NOVA)
        bp.place_on_path(veh, path, 60 + radius * math.pi / 4)     # mid-turn, steady
        fl = veh.footprint()[0]                                       # front-left corner: outside of a right turn
        _, d = path.project(*fl)
        self.assertLess(d, -NOVA.width / 2 - 0.2)                     # beyond half a width outside the path

    def test_a_turn_tighter_than_the_bus_can_steer_is_reported_before_moving(self):
        path = corner(7.0)
        plan = bp.sweep(path, NOVA)
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["issues"][0]["kind"], "steering")
        eased, report = bp.lane_path(path, 0.0, NOVA)
        self.assertTrue(report["corners_eased"])
        self.assertTrue(bp.sweep(eased, NOVA)["ok"])


class LongitudinalTests(unittest.TestCase):
    def test_stops_exactly_with_service_braking_bounded_jerk_and_a_creep(self):
        path = bp.Path([(float(x), 0.0) for x in range(0, 400)])
        for v0 in (12.0, 6.0, 2.0):
            veh, log = drive(path, NOVA, lambda c, t: bp.Intent(path, 14.0, stop_s=200.0), v0=v0, seconds=90)
            stopped = next(x for x in log if x["v"] == 0.0 and x["s"] > 150)
            self.assertAlmostEqual(stopped["s"], 200.0, delta=0.05)
            moving = [x for x in log if x["v"] > 0 and x["t"] <= stopped["t"]]
            self.assertGreaterEqual(min(x["a"] for x in moving), -bp.Limits.service * 1.06)
            jerks = [abs(b["a"] - a["a"]) / bp.Controller.DT for a, b in zip(moving, moving[1:])]
            self.assertLessEqual(max(jerks), bp.Limits.jerk + 1e-6)
            creep = [x["v"] for x in moving if 200.0 - x["s"] <= bp.Limits.creep_m]
            self.assertTrue(creep and max(creep) <= bp.Limits.creep_v + 1e-6)
            last = [x for x in moving if 200.0 - x["s"] < 15]
            self.assertTrue(all(b["v"] <= a["v"] + 1e-6 for a, b in zip(last, last[1:])), "no re-acceleration")

    def test_stopping_distance_matches_service_braking(self):
        # From 12 m/s, the braking curve starts about v^2/2b plus the jerk ramp before the stop.
        path = bp.Path([(float(x), 0.0) for x in range(0, 400)])
        _, log = drive(path, NOVA, lambda c, t: bp.Intent(path, 12.5, stop_s=300.0), v0=12.0, seconds=90)
        start = next(x for x in log if x["a"] < -0.2)
        d = 300.0 - start["s"]
        ideal = 12.0 ** 2 / (2 * bp.Limits.service)
        self.assertGreater(d, ideal)
        self.assertLess(d, ideal + 12.0 * bp.Limits.service / bp.Limits.jerk + 10)

    def test_follows_the_vehicle_ahead_at_the_drivers_headway(self):
        path = bp.Path([(float(x), 0.0) for x in range(0, 2000)])
        lead = {"x": 100.0}
        g = NOVA

        def intent(c, t):
            lead["x"] = 100.0 + 8.0 * t
            front = (c.s or 0) + g.front
            return bp.Intent(path, 14.0, leader_gap=lead["x"] - front, leader_v=8.0)
        _, log = drive(path, g, intent, v0=12.0, seconds=60)
        gap = 100.0 + 8.0 * log[-1]["t"] - (log[-1]["s"] + g.front)      # the leader where it is now
        self.assertAlmostEqual(gap, bp.Limits.gap + 8.0 * 1.3, delta=0.3)

    def test_a_hazard_that_appears_close_ahead_brings_emergency_braking_without_contact(self):
        path = bp.Path([(float(x), 0.0) for x in range(0, 600)])
        g = NOVA
        car_x = 220.0
        car = [(car_x, -0.9), (car_x + 4.6, -0.9), (car_x + 4.6, 0.9), (car_x, 0.9)]

        def intent(c, t):
            there = t >= 12.0
            gap = car_x - ((c.s or 0) + g.front) if there else None
            return bp.Intent(path, 14.0, leader_gap=gap, leader_v=0.0, obstacles=[("car", car)] if there else [])
        veh, log = drive(path, g, intent, v0=12.0, seconds=30)
        self.assertTrue(any(x["emergency"] for x in log))
        self.assertFalse(any(x["conflict"] for x in log))
        self.assertGreaterEqual(min(x["a"] for x in log), -bp.Limits.emergency - 1e-6)
        self.assertGreater(car_x - (log[-1]["s"] + g.front), 0.5)

    def test_load_lowers_the_available_acceleration(self):
        empty = bp.Controller(bp.Vehicle("e", NOVA, v=10.0))
        full = bp.Controller(bp.Vehicle("f", NOVA, v=10.0, load=70))
        self.assertLess(full.available_accel(), empty.available_accel())
        self.assertLessEqual(empty.available_accel(), bp.Limits.accel)


class ClearanceTests(unittest.TestCase):
    def test_body_leaving_the_drivable_area_stops_the_bus(self):
        path = corner(13.0)
        # curb 1.0 m inside the lane path: the off-tracking rear wheel would mount it
        area = bp.Area(path, left=-4.0, right=1.0, name="test street")
        plan = bp.sweep(path, NOVA, area)
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["issues"][0]["side"], "right")
        _, log = drive(path, NOVA, lambda c, t: bp.Intent(path, 11.0, area=area))
        hit = next(x for x in log if x["conflict"])
        self.assertEqual(hit["conflict"]["kind"], "area")
        self.assertEqual(log[-1]["v"], 0.0)
        after = [x for x in log if x["t"] > hit["t"]]
        self.assertTrue(all(abs(x["s"] - hit["s"]) < 1e-6 for x in after), "it stays stopped")

    def test_full_body_clearance_to_a_vehicle_alongside(self):
        path = bp.Path([(float(x), 0.0) for x in range(0, 200)])
        # a parked bus whose side is 0.3 m into this bus's lane
        parked = [(80.0, -1.6 - 1.0), (92.2, -1.6 - 1.0), (92.2, -1.6 + 0.3), (80.0, -1.6 + 0.3)]
        res = bp.clear_distance(path, NOVA, 10.0, [("parked", parked)], horizon=150)
        self.assertIsNotNone(res)
        self.assertEqual(res[1], "parked")


class ReverseTests(unittest.TestCase):
    def test_reverse_only_with_authority_and_the_rear_axle_tracks(self):
        path = bp.Path([(float(x), 0.0) for x in range(0, 60)])
        veh = bp.Vehicle("r", NOVA, x=40.0, y=-0.3, yaw=0.0)
        ctl = bp.Controller(veh)
        r = ctl.step(bp.Intent(path, 2.0, stop_s=30.0, gear=-1, authority=False), 0.0)
        self.assertEqual(r.moved, 0.0)
        t = 0.0
        while t < 40:
            r = ctl.step(bp.Intent(path, 2.0, stop_s=30.0, gear=-1, authority=True), t)
            t += ctl.DT
            self.assertLessEqual(veh.v, bp.Limits.reverse_v + 1e-6)
            if r.stopped and t > 3:
                break
        self.assertAlmostEqual(ctl.s, 30.0, delta=0.05)
        self.assertLess(abs(r.d), 0.05)
        self.assertEqual(veh.gear, -1)


class ReproducibilityTests(unittest.TestCase):
    def test_same_inputs_same_motion_and_simplified_record_reconstructs(self):
        path = corner(14.0)
        runs = []
        for _ in range(2):
            veh = bp.Vehicle("t", NOVA)
            bp.place_on_path(veh, path, 8.0)
            ctl = bp.Controller(veh, bp.Driver(0.93, 1.4, 1))
            rec = bp.Recorder(every=0.1)
            t = 0.0
            while t < 40:
                ctl.step(bp.Intent(path, 11.0, stop_s=150.0), t)
                t += ctl.DT
                rec.add(veh, t)
            runs.append(rec.samples)
        self.assertEqual(runs[0], runs[1])
        kept = bp.simplify(runs[0])
        self.assertLess(len(kept), len(runs[0]))
        j = 0
        for x in runs[0]:
            while j + 1 < len(kept) and kept[j + 1][0] < x[0]:
                j += 1
            a, b = kept[j], kept[min(j + 1, len(kept) - 1)]
            if not a[0] <= x[0] <= b[0] or b[0] == a[0]:
                continue
            p = bp.hermite(a, b, x[0])
            self.assertLess(math.hypot(p[1] - x[1], p[2] - x[2]), 0.06)

    def test_bus_class_is_stable_per_vehicle(self):
        self.assertEqual(bp.bus_class("B112"), bp.bus_class("B112"))
        classes = {bp.bus_class(f"B{i}") for i in range(101, 200)}
        self.assertEqual(classes, {"nova_lfs_40", "nfi_xd40"})


if __name__ == "__main__":
    unittest.main()
