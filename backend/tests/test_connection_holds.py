"""Connection holds at UW and Central: dispatch holds a bus with free seats for
incoming ION riders only when the transfer rider-minutes saved outweigh the
delay to riders onboard and waiting downstream, using each rider's exact
eligibility (train stop + walk)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import simulation_engine as se  # noqa: E402
from ion import IonPlan  # noqa: E402
from models import Bus, BusState, Passenger  # noqa: E402

PLAN = IonPlan(duration_min=60, transfer_demand=0)
OPEN_S, CLOSE_S = 600.0, 660.0 - se.DOOR_CLOSE_S   # a bus reaching Central at minute 10


class Stand(unittest.TestCase):
    """One bus standing at Central from minute 10; the others in the yard."""

    def make(self, ready_offsets, riders=10, onboard=10, downstream=0, holds=True):
        s = se.TransitSimulation([Bus(f"B{101 + i}") for i in range(3)], PLAN, intelligent=True, duration=60,
                                 traffic_multiplier=1.15, seed=17, initial_queue=0, connection_holds=holds)
        for stop in s.stops.values():
            stop.queue = []
        walk = se.TRANSFER_WALK_S["CENTRAL"]
        trip = PLAN.trips[0]
        s.hub_arrivals = [(CLOSE_S + off - walk, trip, "CENTRAL", riders) for off in ready_offsets]
        bus = s.buses[0]
        bus.state = BusState.EN_ROUTE
        bus.current_stop_index = se.ROUTE.index("GRAND_RIVER_HOSPITAL")
        bus.target_stop_index = se.ROUTE.index("CENTRAL")
        bus.segment_minutes, bus.segment_elapsed = 5, 4
        bus.onboard = [Passenger(f"ON{i}", "GRAND_RIVER_HOSPITAL", "UW", 0) for i in range(onboard)]
        for i in range(downstream):
            s.stops["UW"].queue.append(Passenger(f"DW{i}", "UW", "CENTRAL", 0))
        for other in s.buses[1:]:
            other.state = BusState.YARD
        self.s, self.bus = s, bus
        return s, bus

    def step_until_departed(self, last=20):
        s, bus = self.s, self.bus
        for minute in range(10, last):
            s.time = minute
            s._process_train_arrivals()
            s._advance_buses()
            if minute > 10 and bus.state == BusState.EN_ROUTE:
                return minute * 60.0
        return None

    def transfers_aboard(self):
        return [p for p in self.bus.onboard if p.source == "ion"]


class HoldDecisionTests(Stand):
    def test_holds_when_transfers_outweigh_the_onboard_delay(self):
        self.make([30.0], riders=20, onboard=10)
        departed = self.step_until_departed()
        d = self.s.decisions[0]
        self.assertEqual(d["chosen"], "hold 1 min")
        self.assertEqual(departed, 720.0)
        self.assertEqual(len(self.transfers_aboard()), 20)
        for p in self.transfers_aboard():
            self.assertGreaterEqual(p.boarded_s, p.ready_s)
        self.assertEqual(self.s.metrics.hold_onboard_rider_minutes, 10)

    def test_departs_when_the_delay_outweighs_the_transfers(self):
        # Two riders against 46 onboard and 40 waiting downstream.
        self.make([30.0], riders=2, onboard=46, downstream=40)
        departed = self.step_until_departed()
        d = self.s.decisions[0]
        self.assertEqual(d["chosen"], "depart on time")
        self.assertEqual(departed, 660.0)
        hold1 = next(o for o in d["options"] if o["option"] == "hold 1 min")
        self.assertFalse(hold1["feasible"])
        self.assertIn("net -", hold1["reason"])
        self.assertEqual(self.transfers_aboard(), [])

    def test_hold_length_follows_exact_eligibility(self):
        # Eligible 61 s after the doors were due to close: 1 min is too short
        # (doors would close at 11:55), so the hold is 2 min.
        self.make([61.0], riders=20, onboard=10)
        departed = self.step_until_departed()
        d = self.s.decisions[0]
        one = next(o for o in d["options"] if o["option"] == "hold 1 min")
        self.assertFalse(one["feasible"])
        self.assertIn("before the first rider is eligible", one["reason"])
        self.assertEqual(d["chosen"], "hold 2 min")
        self.assertEqual(departed, 780.0)
        self.assertEqual(len(self.transfers_aboard()), 20)

    def test_riders_eligible_just_before_and_after_a_held_door_closes(self):
        # Hold 1 min: doors close at 11:55. Riders at 11:54 board; the one at
        # 11:56 does not (a second minute for one rider is not worth 20 onboard).
        s, bus = self.make([59.0, 61.0], riders=10, onboard=20)
        s.hub_arrivals = s.hub_arrivals[:1] + [(CLOSE_S + 61.0 - se.TRANSFER_WALK_S["CENTRAL"], PLAN.trips[1], "CENTRAL", 1)]
        self.step_until_departed()
        d = s.decisions[0]
        self.assertEqual(d["chosen"], "hold 1 min")
        ready = sorted(p.ready_s for p in self.transfers_aboard())
        self.assertEqual(len(ready), 10)
        self.assertTrue(all(r <= CLOSE_S + 60.0 for r in ready))
        self.assertTrue(any(abs(p.ready_s - (CLOSE_S + 61.0)) < 1e-6 for p in s.stops["CENTRAL"].queue))

    def test_riders_beyond_the_longest_hold_are_not_counted(self):
        self.make([se.CONNECTION_HOLD_MAX_MIN * 60.0 + 1.0], riders=30, onboard=5)
        departed = self.step_until_departed()
        self.assertEqual(self.s.decisions, [])
        self.assertEqual(departed, 660.0)

    def test_no_hold_without_free_seats(self):
        self.make([30.0], riders=20, onboard=50)
        departed = self.step_until_departed()
        d = self.s.decisions[0]
        self.assertEqual(d["chosen"], "depart on time")
        self.assertTrue(all("no free seats" in o["reason"] for o in d["options"] if o["option"].startswith("hold")))
        self.assertEqual(departed, 660.0)

    def test_baseline_never_holds(self):
        self.make([30.0], riders=40, onboard=1, holds=False)
        self.assertEqual(self.step_until_departed(), 660.0)
        self.assertEqual(self.s.decisions, [])

    def test_decision_record_explains_itself(self):
        self.make([30.0], riders=20, onboard=10)
        self.step_until_departed()
        d = self.s.decisions[0]
        for key in ("vehicle_id", "trip_id", "trigger", "options", "chosen", "outcome", "location"):
            self.assertTrue(d[key], key)
        self.assertIn("eligible from", d["trigger"])
        self.assertIn("boarded during the hold", d["outcome"])
        self.assertTrue(all(o["reason"] for o in d["options"]))


class ScenarioTests(unittest.TestCase):
    def run_case(self, holds, hold=None):
        plan = IonPlan(duration_min=60, headway_min=8, transfer_demand=110, delay_min=4, platform_hold=hold)
        s = se.TransitSimulation([Bus(f"B{101 + i}") for i in range(3)], plan, intelligent=True, duration=60,
                                 traffic_multiplier=1.15, seed=17, connection_holds=holds)
        return s, s.run()

    def test_central_wave_hold_uses_the_modelled_eligibility(self):
        s, m = self.run_case(True)
        d = next(x for x in s.decisions if x["terminal"] == "central" and x["chosen"].startswith("hold"))
        # ION-N0 stops at Central 21:00.5; riders are eligible 22:00.5. B103's
        # doors were due to close 20:55, so 1 min is too short and 2 min is held.
        self.assertIn("ION-N0", d["trigger"])
        self.assertIn("22:01", d["trigger"])
        self.assertEqual(d["chosen"], "hold 2 min")
        self.assertIn("doors would close 21:55", next(o for o in d["options"] if o["option"] == "hold 1 min")["reason"])

    def test_holds_board_more_transfers_and_cut_total_waiting(self):
        _, off = self.run_case(False)
        _, on = self.run_case(True)
        self.assertGreater(on.ion_transfers_boarded, off.ion_transfers_boarded)
        total = lambda m: m.cumulative_wait_minutes + m.unserved_wait_minutes  # noqa: E731
        self.assertLess(total(on), total(off))
        self.assertGreater(on.hold_onboard_rider_minutes, 0)

    def test_same_scenario_same_hold_decisions(self):
        a, _ = self.run_case(True)
        b, _ = self.run_case(True)
        self.assertEqual(a.decisions, b.decisions)


if __name__ == "__main__":
    unittest.main()
