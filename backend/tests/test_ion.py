import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import corridor  # noqa: E402
import ion  # noqa: E402
from ion import IonPlan  # noqa: E402
from models import Bus  # noqa: E402
from simulation_engine import TransitSimulation  # noqa: E402

M_PER_DEG = 111_320.0
K = math.cos(math.radians(43.465))


def metres_to_path(point, path):
    px, py = point[0] * M_PER_DEG * K, point[1] * M_PER_DEG
    best = math.inf
    for a, b in zip(path, path[1:]):
        ax, ay = a[0] * M_PER_DEG * K, a[1] * M_PER_DEG
        bx, by = b[0] * M_PER_DEG * K, b[1] * M_PER_DEG
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1.0)))
        best = min(best, math.hypot(px - ax - dx * t, py - ay - dy * t))
    return best


def run(plan, intelligent=True, **kwargs):
    sim = TransitSimulation([Bus(f"B{101 + i}") for i in range(4)], plan, intelligent=intelligent,
                            duration=60, traffic_multiplier=1.15, **kwargs)
    sim.run()
    return sim


class TimetableTests(unittest.TestCase):
    def test_trains_leave_each_terminal_on_the_headway(self):
        plan = IonPlan(headway_min=8, transfer_demand=0)
        for direction, offset in (("north", 0.0), ("south", 240.0)):
            # Inside the hour (the warm-up before it brings trains into service).
            departures = sorted(t.depart_s for t in plan.trips if t.direction == direction and 0 <= t.scheduled_s < 3600)
            gaps = {round(b - a, 6) for a, b in zip(departures, departures[1:])}
            self.assertEqual(gaps, {480.0})
            self.assertTrue(any(abs(d - offset) < 1e-9 for d in departures))
        # The line is already busy at minute 0 (trains left before the window).
        self.assertGreaterEqual(sum(plan.state(t, 0.0) is not None for t in plan.trips), 6)

    def test_trains_run_the_whole_line_and_dwell_at_every_station(self):
        plan = IonPlan(transfer_demand=0)
        line = plan.lines["north"]
        self.assertEqual(line.stops[0][0], "Fairway Station")
        self.assertEqual(line.stops[-1][0], "Conestoga Station")
        trip = next(t for t in plan.trips if t.direction == "north" and t.depart_s == 0.0)
        for i, (name, nose) in enumerate(line.stops[1:-1], start=1):
            # The first leg leaves the terminal over its real trackwork, so arrival
            # times come from the run, not the line's leg offsets.
            arrive = plan.arrival_s(trip, name)
            if i > 1:
                self.assertAlmostEqual(arrive - plan.arrival_s(trip, line.stops[i - 1][0]),
                                       line.offsets[i] - line.offsets[i - 1], places=6)
            # At the arrival instant the leg has just ended; from then on it dwells.
            self.assertAlmostEqual(plan.state(trip, arrive)[0], nose, places=6)
            held = [plan.state(trip, arrive + dt) for dt in (0.1, 10.0, ion.DWELL_S - 0.5)]
            for state in held:
                self.assertEqual(state[1], name)
                self.assertAlmostEqual(state[0], nose, places=6)
            after = plan.state(trip, arrive + ion.DWELL_S + 5.0)
            self.assertIsNone(after[1])
            self.assertGreater(after[0], nose)

    def test_motion_follows_the_trip_model_within_line_speed(self):
        plan = IonPlan(transfer_demand=0)
        for line in plan.lines.values():
            for leg in line.legs:
                run = leg.run()
                self.assertAlmostEqual(run.duration, leg.run_s, places=6)
                self.assertLessEqual(max(run.speed), ion.ION_TOP_SPEED + 1e-9)
                self.assertEqual(run.distance(0.0), 0.0)
                self.assertAlmostEqual(run.distance(leg.run_s), leg.to_m - leg.from_m, places=6)
        # The tight King St -> Erb St curve slows the train well below line speed.
        north = plan.lines["north"]
        curve = min(corridor.DATA["ion"]["north"]["curves"], key=lambda c: c[1] if 9000 < c[0] < 10500 else 99)
        leg = next(l for l in north.legs if l.from_m <= curve[0] <= l.to_m)
        run = leg.run()
        times = [t / 4 for t in range(int(leg.run_s * 4))]
        at = min(times, key=lambda t: abs(leg.from_m + run.distance(t) - curve[0]))
        self.assertLess(run.speed_at(at), 0.7 * ion.ION_TOP_SPEED)

    def test_train_positions_lie_on_the_real_track(self):
        plan = IonPlan(transfer_demand=0)
        for trip in plan.trips[::3]:
            for t in range(0, 3600, 97):
                state = plan.state(trip, float(t))
                if state is None:
                    continue
                lonlat = ion.position(trip.direction, state[0])
                self.assertLess(metres_to_path(lonlat, corridor.DATA["ion"][trip.direction]["path"]), 0.5)

    def test_a_held_train_delays_the_one_behind_without_overtaking(self):
        plan = IonPlan(headway_min=8, transfer_demand=110, delay_min=12)
        held = plan.events[0]["trip"]
        self.assertEqual(held.delay_s, 720.0)
        self.assertIn("Fairway", held.hold_reason)
        north = sorted((t for t in plan.trips if t.direction == "north"), key=lambda t: t.scheduled_s)
        for a, b in zip(north, north[1:]):
            self.assertGreaterEqual(b.depart_s - a.depart_s, ion.MIN_SEPARATION_S - 1e-9)
        behind = north[north.index(held) + 1]
        self.assertGreater(behind.delay_s, 0)
        self.assertIn(held.train_id, behind.hold_reason)
        # Order along the track never changes.
        for t in range(0, 3600, 30):
            noses = [plan.nose(trip, float(t)) for trip in north]
            on_line = [n for n in noses if n is not None]
            self.assertEqual(on_line, sorted(on_line, reverse=True))


class ShortRunTests(unittest.TestCase):
    def test_a_run_too_short_for_the_transfer_waves_still_delivers_every_rider(self):
        for minutes in (5, 9, 20, 34):
            plan = IonPlan(duration_min=minutes, transfer_demand=120, delay_min=4)
            self.assertLessEqual(len(plan.events), 2)
            delivered = sum(r for at, _, _, r in plan.hub_arrivals() if at < plan.duration_s)
            self.assertEqual(delivered, 120 if plan.hub_arrivals() else 0, f"{minutes} min run")


class TransferTests(unittest.TestCase):
    def test_every_transfer_rider_arrives_at_uw_or_central(self):
        plan = IonPlan(transfer_demand=110, delay_min=4)
        arrivals = plan.hub_arrivals()
        self.assertEqual(sum(riders for at, _, _, riders in arrivals if at < plan.duration_s), 110)
        self.assertEqual({stop for _, _, stop, riders in arrivals if riders}, {"UW", "CENTRAL"})
        wave = plan.events[0]
        self.assertEqual(sum(wave["trip"].transfers.values()), wave["riders"])

    def test_simulation_spawns_transfers_at_the_minute_trains_arrive(self):
        plan = IonPlan(transfer_demand=110, delay_min=4)
        sim = run(plan)
        self.assertEqual(sim.metrics.ion_transfers, 110)
        self.assertGreater(sim.metrics.ion_transfers_boarded, 0)
        arrivals = [e for e in sim.events if e["kind"] == "train"]
        for at, trip, stop, riders in plan.hub_arrivals():
            if at < 3600 and riders:
                match = [e for e in arrivals if e["train_id"] == trip.train_id and e["minute"] == math.floor(at / 60)]
                self.assertTrue(match, f"{trip.train_id} at {stop} minute {math.floor(at / 60)}")
        held = [e for e in sim.events if e["kind"] == "disruption" and e.get("train_id")]
        self.assertTrue(held)

    def test_snapshot_trains_match_the_timetable_exactly(self):
        plan = IonPlan(transfer_demand=110, delay_min=4)
        sim = run(plan)
        by_id = {trip.train_id: trip for trip in plan.trips}
        for tick in sim.timeline:
            # Trains are physical vehicles; each carries the trip it is running.
            moving = {t.train_id for t in plan.trips if plan.state(t, tick["minute"] * 60.0) is not None}
            self.assertTrue(moving <= {t["trip_id"] for t in tick["trains"]})
            self.assertEqual(len({t["id"] for t in tick["trains"]}), len(tick["trains"]))
            for train in tick["trains"]:
                self.assertTrue(train["id"].startswith("LRV"))
                if train["trip_id"] in moving:
                    self.assertEqual(train["nose_m"], plan.nose(by_id[train["trip_id"]], tick["minute"] * 60.0))

    def test_adaptive_controller_names_the_ion_train_it_is_staging_for(self):
        plan = IonPlan(transfer_demand=240, delay_min=4)
        sim = run(plan)
        decisions = [e for e in sim.events if e["kind"] == "decision" and "ION-" in e.get("reason", "")]
        self.assertTrue(decisions, "a pre-staging decision cites the incoming ION train")
        baseline = run(IonPlan(transfer_demand=240, delay_min=4), intelligent=False)
        self.assertEqual(baseline.metrics.buses_staged, 0)


if __name__ == "__main__":
    unittest.main()
