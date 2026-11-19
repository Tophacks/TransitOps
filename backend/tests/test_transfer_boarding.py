"""Rail-to-bus connections are timed in seconds: a transfer rider boards a bus
whose doors are still open when they become eligible (train stop + walk), and
misses one whose doors closed first. Nothing is rounded to the minute."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import simulation_engine as se  # noqa: E402
from ion import IonPlan  # noqa: E402
from models import Bus, BusState, Passenger  # noqa: E402

PLAN = IonPlan(duration_min=60, transfer_demand=0)


def sim(**kw):
    return se.TransitSimulation([Bus(f"B{101 + i}") for i in range(3)], PLAN, intelligent=False, duration=60,
                                traffic_multiplier=1.15, seed=17, initial_queue=0, **kw)


def rider(pid, ready_s, minute):
    return Passenger(pid, "CENTRAL", "UW", arrival_time=minute, source="ion", eligible_s=ready_s)


class DoorWindowTests(unittest.TestCase):
    """A bus that reaches Central at minute 10 opens its doors at 600 s, closes
    them at 655 s and pulls out at 660 s."""

    def setUp(self):
        self.s = sim()
        for stop in self.s.stops.values():
            stop.queue = []
        self.bus = self.s.buses[0]
        self.bus.state = BusState.EN_ROUTE
        self.bus.current_stop_index = se.ROUTE.index("GRAND_RIVER_HOSPITAL")
        self.bus.target_stop_index = se.ROUTE.index("CENTRAL")
        self.bus.segment_minutes = 5
        self.bus.segment_elapsed = 4
        for other in self.s.buses[1:]:
            other.state = BusState.YARD
        self.open_s, self.close_s = 600.0, 660.0 - se.DOOR_CLOSE_S

    def run_stop(self, riders):
        self.s.stops["CENTRAL"].queue = list(riders)
        self.s.time = 10
        self.s._advance_buses()          # arrives, doors open
        self.assertEqual(self.bus.state, BusState.DWELLING)
        self.assertEqual(self.bus.doors_open_s, self.open_s)
        self.s.time = 11
        self.s._advance_buses()          # boards late riders, doors close, departs
        self.assertEqual(self.bus.state, BusState.EN_ROUTE)
        return {p.passenger_id for p in self.bus.onboard}

    def test_eligible_just_before_doors_close_boards(self):
        p = rider("JUST_BEFORE", self.close_s - 1.0, 10)
        self.assertIn("JUST_BEFORE", self.run_stop([p]))
        self.assertEqual(p.boarded_s, self.close_s - 1.0)

    def test_eligible_just_after_doors_close_misses_the_bus(self):
        p = rider("JUST_AFTER", self.close_s + 1.0, 10)
        self.assertNotIn("JUST_AFTER", self.run_stop([p]))
        self.assertIn(p, self.s.stops["CENTRAL"].queue)

    def test_exactly_at_close_boards_and_after_departure_does_not(self):
        at_close = rider("AT_CLOSE", self.close_s, 10)
        after_departure = rider("AFTER_DEPARTURE", 661.0, 11)
        onboard = self.run_stop([at_close, after_departure])
        self.assertEqual(onboard, {"AT_CLOSE"})

    def test_a_rider_eligible_later_in_the_arrival_minute_does_not_board_early(self):
        # The old minute model boarded this rider at 600 s, before their train
        # had even arrived. Now they board when they get there.
        p = rider("MID_MINUTE", 645.0, 10)
        self.assertIn("MID_MINUTE", self.run_stop([p]))
        self.assertEqual(p.boarded_s, 645.0)
        self.assertGreaterEqual(p.boarded_s, p.ready_s)

    def test_already_waiting_riders_board_when_the_doors_open(self):
        p = rider("WAITING", 540.0, 9)
        self.assertIn("WAITING", self.run_stop([p]))
        self.assertEqual(p.boarded_s, self.open_s)
        self.assertAlmostEqual(self.s.metrics.cumulative_transfer_wait, 1.0)

    def test_nobody_boards_before_they_are_eligible(self):
        riders = [rider(f"R{i}", self.open_s - 90 + 10 * i, 9 if i < 9 else 10) for i in range(15)]
        self.run_stop(riders)
        for p in riders:
            if p.boarded_s is not None:
                self.assertGreaterEqual(p.boarded_s, p.ready_s)
                self.assertLessEqual(p.boarded_s, self.close_s)


class EndToEndTests(unittest.TestCase):
    def door_window(self, stop_id):
        """A door window at a stop where the bus leaves with seats to spare (bus
        timing does not depend on the riders when the adaptive controller is off),
        so only the timing decides who boards."""
        windows = []

        class Traced(se.TransitSimulation):
            def _board_until_doors_close(self, bus, stop):
                opened = bus.doors_open_s
                super()._board_until_doors_close(bus, stop)
                if stop == stop_id and bus.capacity - bus.occupancy >= 3:
                    windows.append(opened)

        Traced([Bus(f"B{101 + i}") for i in range(3)], PLAN, intelligent=False, duration=60,
               traffic_multiplier=1.15, seed=17).run()
        self.assertTrue(windows, f"no bus left {stop_id} with free seats")
        return windows[0]

    def test_train_riders_just_before_and_just_after_doors_close(self):
        for stop in ("CENTRAL", "UW"):
            open_s = self.door_window(stop)
            close_s = open_s + 60.0 - se.DOOR_CLOSE_S
            walk = se.TRANSFER_WALK_S[stop]
            plan = IonPlan(duration_min=60, transfer_demand=0)
            trips = plan.trips
            # Two trains: their riders become eligible 1 s before and 1 s after the doors close.
            arrivals = [(close_s - 1.0 - walk, trips[0], stop, 1), (close_s + 1.0 - walk, trips[1], stop, 1)]
            plan.hub_arrivals = lambda arrivals=arrivals: arrivals
            boarded = []

            class Traced(se.TransitSimulation):
                def _board(self, bus, stop_, until_s, doors_open_s):
                    got = super()._board(bus, stop_, until_s, doors_open_s)
                    boarded.extend((p.ready_s, p.boarded_s, doors_open_s) for p in got if p.source == "ion")
                    return got

            s = Traced([Bus(f"B{101 + i}") for i in range(3)], plan, intelligent=False, duration=60,
                       traffic_multiplier=1.15, seed=17)
            s.run()
            before = next(b for b in boarded if abs(b[0] - (close_s - 1.0)) < 1e-6)
            after = next((b for b in boarded if abs(b[0] - (close_s + 1.0)) < 1e-6), None)
            self.assertEqual(before[2], open_s, f"{stop}: eligible 1 s before close boards that bus")
            self.assertEqual(before[1], close_s - 1.0)
            self.assertTrue(after is None or after[2] > open_s, f"{stop}: eligible 1 s after close waits for the next bus")

    def test_transfer_riders_never_board_before_their_train_arrives(self):
        plan = IonPlan(duration_min=60, transfer_demand=110, delay_min=4)
        arrivals = {}
        for at, trip, stop, riders in plan.hub_arrivals():
            arrivals.setdefault(stop, []).append(at)
        checked = []

        class Traced(se.TransitSimulation):
            def _board(self, bus, stop, until_s, doors_open_s):
                got = super()._board(bus, stop, until_s, doors_open_s)
                for p in got:
                    if p.source == "ion":
                        train = p.ready_s - se.TRANSFER_WALK_S[stop.stop_id]
                        checked.append((p.boarded_s, train, p.ready_s))
                return got

        Traced([Bus(f"B{101 + i}") for i in range(3)], plan, intelligent=True, duration=60,
               traffic_multiplier=1.15, seed=17).run()
        self.assertGreater(len(checked), 40)
        for boarded, train, ready in checked:
            self.assertGreaterEqual(boarded, ready)
            self.assertGreater(boarded, train)


if __name__ == "__main__":
    unittest.main()
