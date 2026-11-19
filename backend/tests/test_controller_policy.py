"""The adaptive controller must not do worse than the fixed schedule.

Before the pre-staging rules (spacing, pull-forward, reserve) the adaptive
controller left more riders waiting than the plain 10-minute schedule in the
rush-hour and emergency presets on every seed: it released reserve buses early
and together, and with ~40-minute loops none came back to Central for most of
the hour. Averaged over several seeds, each playground preset must now leave
no more riders waiting than the schedule.
"""
import statistics
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import SimulationRequest, ion_plan  # noqa: E402
from models import Bus  # noqa: E402
from simulation_engine import TransitSimulation  # noqa: E402

DEFAULTS = dict(traffic_multiplier=1.15, train_delay=4, passenger_demand=110, fleet_size=3, demand_multiplier=1)
PRESETS = {
    "weekday": DEFAULTS,
    "rush": {**DEFAULTS, "traffic_multiplier": 2.1, "demand_multiplier": 1.8, "fleet_size": 5},
    "event": {**DEFAULTS, "surge_minute": 20, "surge_passengers": 180, "passenger_demand": 160, "fleet_size": 5},
    "breakdown": {**DEFAULTS, "breakdown_minute": 20, "fleet_size": 4},
    "emergency": {**DEFAULTS, "emergency_dispatch_minute": 15, "passenger_demand": 185, "fleet_size": 6},
}
SEEDS = range(1, 6)


def left_waiting(controls, seed, intelligent):
    req = SimulationRequest(**controls, seed=seed)
    sim = TransitSimulation(
        [Bus(f"B{101 + i}") for i in range(req.fleet_size)], ion_plan(req), intelligent=intelligent, duration=60,
        traffic_multiplier=req.traffic_multiplier, seed=req.seed, demand_multiplier=req.demand_multiplier,
        surge_minute=req.surge_minute, surge_passengers=req.surge_passengers,
        breakdown_minute=req.breakdown_minute, emergency_dispatch_minute=req.emergency_dispatch_minute,
    )
    return sim.run().passengers_left_waiting


class ControllerPolicyTests(unittest.TestCase):
    def test_adaptive_is_never_worse_than_the_schedule_on_average(self):
        for name, controls in PRESETS.items():
            schedule = statistics.mean(left_waiting(controls, s, False) for s in SEEDS)
            adaptive = statistics.mean(left_waiting(controls, s, True) for s in SEEDS)
            self.assertLessEqual(adaptive, schedule, f"{name}: adaptive leaves {adaptive:.1f} waiting vs {schedule:.1f}")


if __name__ == "__main__":
    unittest.main()
