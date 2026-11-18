"""Reference values the browser's kinematics port must reproduce.

Run `python3 parity_fixture.py` from backend/ after changing corridor.py or the
geometry; tests/test_corridor_motion.py fails while the fixture is stale.
"""
import json
from pathlib import Path

import corridor

FIXTURE = Path(__file__).resolve().parents[1] / "frontend" / "tests" / "fixtures" / "kinematics-parity.json"
CASES = [
    ("UW", "WATERLOO_SQUARE", 5, 0),
    ("UW", "WATERLOO_SQUARE", 6, 1),
    ("WATERLOO_SQUARE", "GRAND_RIVER_HOSPITAL", 6, 1),
    ("CENTRAL", "UW", 9, 1),
    ("CENTRAL", "UW", 17, 0),
    ("YARD", "CENTRAL", 7, 0),
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
    return {"cases": cases}


def text():
    return json.dumps(build(), indent=1) + "\n"


if __name__ == "__main__":
    FIXTURE.write_text(text())
    print(f"wrote {FIXTURE}")
