"""Reference values the browser's kinematics port must reproduce.

Run `python3 parity_fixture.py` from backend/ after changing corridor.py or the
geometry; tests/test_corridor_motion.py fails while the fixture is stale.
"""
import json
from pathlib import Path

import corridor
from ion import IonPlan
from models import Bus
from simulation_engine import TransitSimulation

FIXTURE = Path(__file__).resolve().parents[1] / "frontend" / "tests" / "fixtures" / "kinematics-parity.json"
CASES = [
    ("UW", "WATERLOO_SQUARE", 5, 0),
    ("UW", "WATERLOO_SQUARE", 6, 1),
    ("WATERLOO_SQUARE", "GRAND_RIVER_HOSPITAL", 6, 1),
    ("CENTRAL", "UW", 9, 1),
    ("CENTRAL", "UW", 17, 0),
    ("YARD", "CENTRAL", 7, 0),
    # Recalibrated segment times (street length at GRT's scheduled speed).
    ("UW", "WATERLOO_SQUARE", 9, 1),
    ("WATERLOO_SQUARE", "GRAND_RIVER_HOSPITAL", 3, 1),
    ("CENTRAL", "UW", 28, 0),
    ("YARD", "CENTRAL", 1, 0),
]


def build():
    cases = []
    for a, b, minutes, hold in CASES:
        run = corridor.bus_run(a, b, minutes, hold)
        seg = corridor.segment(a, b)
        times = [round(i * 7.3, 1) for i in range(int(minutes * 60 / 7.3) + 2)]
        distances = [run.distance(t) for t in times]
        cases.append({
            "from": a, "to": b, "minutes": minutes, "hold": hold,
            "times": times, "distances": distances,
            "positions": [list(corridor.position(seg, s)) for s in distances[::5]],
        })
    return {"cases": cases, "ion": ion_cases()}


def ion_cases():
    """ION trains: the timetable the API returns, noses every 3.7 s for a few
    trips, and every train's nose at each whole minute of a simulation run."""
    plan = IonPlan(duration_min=60, headway_min=8, transfer_demand=110, delay_min=4)
    samples = []
    for trip in plan.trips[::5]:
        times = [round(trip.depart_s + i * 3.7, 1) for i in range(int(plan.lines[trip.direction].trip_seconds / 3.7) + 2)]
        samples.append({"id": trip.train_id, "times": times, "noses": [plan.nose(trip, t) for t in times]})
    sim = TransitSimulation([Bus(f"B{101 + i}") for i in range(3)], plan, duration=60)
    sim.run()
    minutes = [{"minute": tick["minute"], "trains": {t["id"]: t["nose_m"] for t in tick["trains"]}} for tick in sim.timeline]
    return {"schedule": plan.schedule(), "samples": samples, "minutes": minutes}


def text():
    return json.dumps(build(), indent=1) + "\n"


if __name__ == "__main__":
    FIXTURE.write_text(text())
    print(f"wrote {FIXTURE}")
