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
# Red-signal holds behind queued cars: (from, to, minutes, hold, cars).
QUEUE_CASES = [
    ("UW", "WATERLOO_SQUARE", 9, 1, 2),
    ("CENTRAL", "UW", 29, 1, 3),
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
    for a, b, minutes, hold, cars in QUEUE_CASES:
        run = corridor.bus_run(a, b, minutes, hold, cars)
        times = [round(i * 7.3, 1) for i in range(int(minutes * 60 / 7.3) + 2)]
        cases.append({"from": a, "to": b, "minutes": minutes, "hold": hold, "queue": cars,
                      "times": times, "distances": [run.distance(t) for t in times], "positions": []})
    return {"cases": cases, "ion": ion_cases(), "traffic": traffic_case()}


def traffic_case():
    """A real 60-minute run, trimmed to what the browser's traffic needs: each
    bus's segment timing and every signal's phase, minute by minute."""
    sim = TransitSimulation([Bus(f"B{101 + i}") for i in range(3)],
                            IonPlan(duration_min=60, headway_min=8, transfer_demand=110, delay_min=4),
                            intelligent=True, duration=60, traffic_multiplier=1.15)
    sim.run()
    frames = [{
        "minute": tick["minute"],
        "buses": [{"id": b["id"], "state": b["state"], "segment": b["segment"]} for b in tick["buses"]],
        "phases": {i["id"]: i["phase"] for i in tick["intersections"]},
    } for tick in sim.timeline]
    return {"traffic_multiplier": 1.15, "timeline": frames}


HOLD = {"terminal": "fairway", "platform": "2", "minute": 20, "minutes": 12, "reason": "door fault"}


def ion_cases():
    """ION trains: the schedule the API returns (with every train's movement
    plan), each train's nose and centre every 7.3 s, and every train at each
    whole minute of a simulation run with a platform hold at Fairway."""
    plan = IonPlan(duration_min=60, headway_min=8, transfer_demand=110, delay_min=4, platform_hold=HOLD)
    samples = []
    for train in plan.ops.trains:
        times = [round(-600.0 + i * 7.3, 1) for i in range(int(4800 / 7.3))]
        rows = []
        for t in times:
            found = plan.ops.where(train, t)
            if found is None:
                continue
            seg, s, _ = found
            rows.append([t, round(s, 6), list(plan.ops.lonlat(seg, s))])
        samples.append({"id": train.vehicle_id, "rows": rows[::3] if train.vehicle_id not in ("LRV501", "LRV503") else rows})
    sim = TransitSimulation([Bus(f"B{101 + i}") for i in range(3)], plan, duration=60)
    sim.run()
    minutes = [{"minute": tick["minute"], "trains": {t["id"]: t["nose_m"] for t in tick["trains"]}} for tick in sim.timeline]
    return {"schedule": plan.schedule(), "samples": samples, "minutes": minutes}


def text():
    return json.dumps(build(), indent=1) + "\n"


if __name__ == "__main__":
    FIXTURE.write_text(text())
    print(f"wrote {FIXTURE}")
