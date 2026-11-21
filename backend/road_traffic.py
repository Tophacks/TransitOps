"""Phillip Street southbound at UW Station: private cars and buses on one lane.

A Python port of the UW private-car pilot's lane rules
(feat/uw-private-car-agents: frontend/lib/motion/road.ts and laneTraffic.ts,
docs/uw-road-agents.md), run on UW Station's one-second clock so cars and
buses see the same traffic:

- following: the pilot's `followingSpeed` (headway, acceleration and braking
  limits), with each driver's own headway preference and pace;
- the University Avenue signal: a red stops traffic at its stop line;
- the merge: a bus turning right out of Transit Plaza Road onto the lane needs
  room for its whole length ahead and a gap behind it of the standstill
  distance plus 3 s at the approaching car's speed (`mergeBlockers`); the
  vehicles that prevent it are named.

Geometry: uw_roads.json (build_uw_roads.py). Car demand and the signal cycle
are scenario inputs (modelled).
"""
import json
import math
import random
from pathlib import Path

ROADS = json.loads((Path(__file__).resolve().parent / "uw_roads.json").read_text())
PH = ROADS["phillip"]
STEP = 1.0                 # UW Station's clock
ROAD_GAP = 2.4
ACCEL, BRAKE = 1.8, 4.0
MERGE_HEADWAY_S = 3.0
CAR_LENGTH = 4.6
CAR_DEMAND = {"light": 250, "moderate": 500, "heavy": 900}   # vehicles per hour southbound (modelled)


def following_speed(speed, cruise, gap, headway=1.3, step=STEP):
    """The pilot's following rule (road.ts followingSpeed) at a given step."""
    b = BRAKE * 0.85
    clearance = max(0.0, gap - 0.3)
    safe = -b * step + math.sqrt((b * step) ** 2 + 2 * b * clearance)
    want = min(cruise, safe, clearance / headway + 1.5)
    dv = max(-BRAKE * step, min(ACCEL * step, want - speed))
    return max(0.0, speed + dv)


class RoadVehicle:
    def __init__(self, vid, kind, front, length, speed, driver=None, entry=None):
        self.id, self.kind = vid, kind
        self.front, self.length, self.speed = front, length, speed
        self.driver = driver
        # A bus turning in at `entry`: until its whole length is on the lane, the
        # rest of its body is still in the turn, and the merge point is occupied.
        self.entry = entry
        self.trace = []
        self.done = False

    @property
    def rear(self):
        r = self.front - self.length
        return max(r, self.entry) if self.entry is not None else r

    @property
    def turning(self):
        return self.entry is not None and self.front - self.length < self.entry


class Lane:
    """One lane of shared traffic: cars arrive at its start (seeded), follow the
    vehicle ahead, stop at a red signal if it has one, and leave at its end.
    Vehicles driven by their own controller (`external`, e.g. a Route 7 bus) are
    leaders the cars follow; the lane does not move them."""

    def __init__(self, length, merge, demand="moderate", seed=17, speed=None, signal=None, cycle=None, name="lane"):
        self.name = name
        self.length = length
        self.merge = merge
        self.signal = signal
        cyc = cycle or {"period_s": 1, "red_from_s": 0, "red_to_s": 0}
        self.period, self.red0, self.red1 = cyc["period_s"], cyc["red_from_s"], cyc["red_to_s"]
        self.speed_mps = speed or PH["speed_mps"]
        self.flow = CAR_DEMAND.get(demand, demand) if isinstance(demand, str) else float(demand)
        self.seed = seed
        self.rng = random.Random(seed * 104729 + 7 + (0 if name == "lane" else sum(map(ord, name))))
        self.vehicles = []
        self.cars = 0
        self.next_car = self._headway() if self.flow > 0 else math.inf
        self.log = []

    def _headway(self):
        return max(1.5, self.rng.expovariate(self.flow / 3600.0))

    def red(self, t):
        return self.signal is not None and self.red0 <= (t % self.period) < self.red1

    def ordered(self):
        return sorted((v for v in self.vehicles if not v.done), key=lambda v: -v.front)

    def merge_blockers(self, length, t, rear=None, exclude=None):
        """Vehicles that stop a bus of `length` entering with its rear at `rear`
        (the merge point by default) now."""
        rear = self.merge if rear is None else rear
        front = rear + length
        out = []
        for v in self.ordered():
            if v.id == exclude:
                continue
            if v.front <= rear:
                if rear - v.front < ROAD_GAP + MERGE_HEADWAY_S * v.speed:
                    out.append(v)
            elif v.rear - front < ROAD_GAP:
                out.append(v)
        return out

    def enter(self, vid, length, speed, t, driver=None, front=None, entry="merge", external=False):
        """A bus joins the lane: by default turning in at the merge point."""
        front = self.merge if front is None else front
        v = RoadVehicle(vid, "bus", front, length, speed, driver,
                        entry=(self.merge if entry == "merge" else entry))
        v.external = external
        self.vehicles.append(v)
        return v

    def leave(self, vid):
        for v in self.vehicles:
            if v.id == vid and not v.done:
                v.done = True

    def step(self, t):
        # Cars arrive at the north end when the entry is clear (else they wait upstream).
        if t >= self.next_car:
            first = min((v.rear for v in self.vehicles if not v.done), default=math.inf)
            if first > CAR_LENGTH + ROAD_GAP:
                self.cars += 1
                from drivers import Driver
                cid = f"car-{self.cars}" if self.name == "lane" else f"{self.name}-car-{self.cars}"
                car = RoadVehicle(cid, "car", CAR_LENGTH, min(11.0, self.speed_mps), 0, Driver(cid, self.seed))
                self.vehicles.append(car)
                self.next_car = t + self._headway()
        red = self.red(t)
        ahead = None
        for v in self.ordered():
            if getattr(v, "external", False):
                ahead = v                 # driven by its own controller; a leader for those behind
                continue
            limit = ahead.rear - ROAD_GAP if ahead is not None else math.inf
            if red and v.front <= self.signal + 0.01:
                limit = min(limit, self.signal)
            d = v.driver
            cruise = self.speed_mps * (d.pace if d else 1.0)
            headway = d.headway if d else 1.3
            spd = following_speed(v.speed, cruise, limit - v.front, headway)
            adv = min(spd * STEP, max(0.0, limit - v.front))
            v.front += adv
            v.speed = adv / STEP
            v.trace.append([t + STEP, round(v.front, 2)])
            if v.rear >= self.length:
                v.done = True
            ahead = v


class Phillip(Lane):
    """Phillip Street southbound at UW Station: from north of the exit, past the
    merge, to beyond University Ave (the pilot's lane and signal)."""

    def __init__(self, demand="moderate", seed=17):
        super().__init__(PH["length_m"], PH["merge_m"], demand, seed, speed=PH["speed_mps"],
                         signal=PH["signal_m"], cycle=PH["signal_cycle"])
