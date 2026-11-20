"""Drivers: each simulated driver's own way of driving, and opt-in human errors.

A driver's profile is drawn once, reproducibly, from the run's seed and the
vehicle's ID (modelled ranges, all within the simulated speed and clearance
rules):

- pace: 88–100 % of the road's speed limit;
- headway preference: 1.0–1.8 s to the vehicle ahead;
- reaction: 0–2 s before moving off once the way ahead opens.

Human errors are off unless the scenario asks for them. "seeded" draws them
at `rate` per opportunity from the seed; a demo names the vehicle, the
error and where. Kinds:

- overshoot: stopping a few metres past a berth's stopping point;
- missed_berth: not turning into the berth in time;
- wrong_turn: turning left out of the station where a legal left exists
  (the planned move is right).

The same seed gives the same drivers and the same mistakes.
"""
import hashlib
import random

ERROR_KINDS = ("overshoot", "missed_berth", "wrong_turn")


def _unit(*parts):
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


class Driver:
    def __init__(self, vehicle_id, seed):
        self.vehicle_id = vehicle_id
        self.pace = round(0.88 + 0.12 * _unit(seed, vehicle_id, "pace"), 3)
        self.headway = round(1.0 + 0.8 * _unit(seed, vehicle_id, "headway"), 2)
        self.reaction = int(3 * _unit(seed, vehicle_id, "reaction"))     # 0, 1 or 2 s

    def describe(self):
        return f"pace {self.pace * 100:.0f} % of the limit, {self.headway:.1f} s headway, {self.reaction} s reaction"


class Errors:
    """Which mistakes happen. `spec`: None (off), {"mode": "seeded", "rate": r},
    or {"mode": "demo", "kind": k, "vehicle": id, "visit": n, ...}."""

    def __init__(self, spec, seed):
        self.spec = spec or None
        self.seed = (spec or {}).get("seed", seed)
        self.used = []

    def happens(self, kind, vehicle_id, visit, where):
        s = self.spec
        if not s:
            return None
        if s.get("mode") == "demo":
            if s.get("kind") == kind and s.get("vehicle", vehicle_id) == vehicle_id and s.get("visit", 1) == visit \
                    and s.get("where", where) == where:
                return dict(s)
            return None
        if s.get("mode") == "seeded":
            if _unit(self.seed, vehicle_id, visit, kind, where) < float(s.get("rate", 0.1)):
                return {"kind": kind, "metres": round(3.0 + 6.0 * _unit(self.seed, vehicle_id, visit, kind, "m"), 1)}
        return None

    def record(self, t, vehicle_id, kind, detail):
        self.used.append({"t": round(t, 1), "vehicle_id": vehicle_id, "kind": kind, "detail": detail})


def rng_for(seed, *parts):
    return random.Random(int(_unit(seed, *parts) * 2 ** 53))
