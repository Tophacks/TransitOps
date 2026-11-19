"""ION light rail in the simulator: a timetable of real trains on the real track.

Trains leave each terminal (Fairway northbound, Conestoga southbound) on a
fixed headway, stop at every station for a dwell, and run between stations on
the same trip model buses use (corridor.Run), with ION acceleration, braking,
line speed and the track's own curve limits. Each leg takes the time the
train needs at line speed, so positions come from the trip model, not from a
guessed timetable.

The frontend ports `IonPlan.nose` line for line (frontend/lib/motion/ion.ts)
and reproduces the simulator's positions exactly at every minute; see
parity_fixture.py.

Passengers: each train arriving at Central Station or University of Waterloo
Station brings riders who transfer to the corridor bus there. The scenario's
two big transfer waves ride on two real trips (the first northbound trains due
at Central after minutes 12 and 32), which the scenario can hold late at
Fairway. A late train delays the ones behind it: trains never overtake.
"""
import math
from dataclasses import dataclass, field

import corridor

ION_ACCEL = 1.0     # m/s^2
ION_DECEL = 1.1     # m/s^2
ION_TOP_SPEED = 16.7  # m/s (60 km/h): line speed, lower than the 70 km/h maximum, for street running
ION_LENGTH = 41.8   # m: sections 9.2 + 7.0 + 7.4 + 7.0 + 9.2 and four 0.5 m bellows (frontend ION_LENGTH)
DWELL_S = 30.0
MIN_SEPARATION_S = 180.0  # trains behind a late one stay at least 3 min apart
HUBS = {"Central Station": "CENTRAL", "University Of Waterloo Station": "UW"}
# Of the scenario's transfer demand, this share rides the two event trains
# (split 56/44 as before); the rest is spread over every other hub arrival.
EVENT_SHARE = 0.7
EVENT_SPLIT = (0.56, 0.44)
# An event train's riders leave it at Central (first stop of the two
# northbound) and at UW.
EVENT_HUB_SPLIT = {"CENTRAL": 0.7, "UW": 0.3}


def _leg_curves(curves, start, end):
    return tuple((s - start, cap) for s, cap in curves if start <= s <= end)


def _natural_seconds(length, curves):
    """Time at line speed, rounded up to 0.1 s (the Run is then fitted to it)."""
    v = corridor._speed_profile(length, curves, ION_TOP_SPEED, None, ION_ACCEL, ION_DECEL)
    return math.ceil(corridor._run_time(length, v) * 10.0) / 10.0


@dataclass
class Leg:
    from_station: str
    to_station: str
    from_m: float
    to_m: float
    run_s: float
    curves: tuple

    def run(self):
        return _leg_run(self.to_m - self.from_m, self.curves, self.run_s)


_RUNS = {}


def _leg_run(length, curves, run_s):
    key = (length, curves, run_s)
    if key not in _RUNS:
        _RUNS[key] = corridor.Run(length, curves, run_s, accel=ION_ACCEL, decel=ION_DECEL,
                                  top_speed=ION_TOP_SPEED, rush_speed=ION_TOP_SPEED)
    return _RUNS[key]


class Line:
    """One direction of the track: station stops and the legs between them."""

    def __init__(self, direction):
        track = corridor.DATA["ion"][direction]
        self.direction = direction
        self.length = track["length_m"]
        half = ION_LENGTH / 2.0
        # The train stops with its centre on the platform: nose half a train ahead.
        self.stops = [
            (station["name"], min(self.length - 0.5, max(ION_LENGTH, station["at_m"] + half)))
            for station in track["stations"]
        ]
        self.legs = []
        for (a, sa), (b, sb) in zip(self.stops, self.stops[1:]):
            curves = _leg_curves(track["curves"], sa, sb)
            self.legs.append(Leg(a, b, sa, sb, _natural_seconds(sb - sa, curves), curves))
        self.offsets = []  # seconds from terminal departure to each stop's arrival
        t = 0.0
        self.offsets.append(t)
        for leg in self.legs:
            t += leg.run_s
            self.offsets.append(t)
            t += DWELL_S
        self.trip_seconds = self.offsets[-1]

    def arrival_offset(self, name):
        return next(self.offsets[i] for i, (station, _) in enumerate(self.stops) if station == name)


@dataclass
class Trip:
    train_id: str
    direction: str
    scheduled_s: float  # terminal departure on the timetable
    depart_s: float     # actual terminal departure (after holds and spacing)
    hold_reason: str = ""
    transfers: dict = field(default_factory=dict)  # bus stop id -> riders transferring there

    @property
    def delay_s(self):
        return self.depart_s - self.scheduled_s


class IonPlan:
    """Every ION trip that is on the line during the simulated hour."""

    def __init__(self, duration_min=60, headway_min=8, transfer_demand=110, delay_min=0):
        self.headway_s = headway_min * 60.0
        self.duration_s = duration_min * 60.0
        self.lines = {"north": Line("north"), "south": Line("south")}
        self.trips = []
        for direction, offset in (("north", 0.0), ("south", self.headway_s / 2.0)):
            line = self.lines[direction]
            first = math.floor((-line.trip_seconds - offset) / self.headway_s)
            last = math.floor((self.duration_s - offset) / self.headway_s)
            for k in range(first, last + 1):
                t = offset + k * self.headway_s
                prefix = "N" if direction == "north" else "S"
                self.trips.append(Trip(f"ION-{prefix}{k}", direction, t, t))
        self.events = self._assign_events(transfer_demand, delay_min)
        self._space_out()
        self._assign_transfers(transfer_demand)

    def _assign_events(self, demand, delay_min):
        """The two big transfer waves ride the first northbound trains due at
        Central after minutes 12 and 32; the scenario can hold them late."""
        north = self.lines["north"]
        at_central = north.arrival_offset("Central Station")
        events = []
        for minute, share, delay in ((12, EVENT_SPLIT[0], delay_min), (32, EVENT_SPLIT[1], delay_min // 2)):
            trip = min(
                (t for t in self.trips if t.direction == "north" and t.scheduled_s + at_central >= minute * 60.0
                 and all(t is not e["trip"] for e in events)),
                key=lambda t: t.scheduled_s,
            )
            if delay:
                trip.depart_s += delay * 60.0
                trip.hold_reason = f"held {delay} min at Fairway"
            riders = round(demand * EVENT_SHARE * share)
            events.append({"trip": trip, "riders": riders, "minute": minute})
        return events

    def _space_out(self):
        for direction in ("north", "south"):
            previous = None
            for trip in sorted((t for t in self.trips if t.direction == direction), key=lambda t: t.scheduled_s):
                if previous is not None and trip.depart_s < previous.depart_s + MIN_SEPARATION_S:
                    trip.depart_s = previous.depart_s + MIN_SEPARATION_S
                    trip.hold_reason = trip.hold_reason or f"held behind late {previous.train_id}"
                previous = trip

    def _assign_transfers(self, demand):
        event_riders = {id(e["trip"]): e["riders"] for e in self.events}
        others = []
        for trip in self.trips:
            for name, stop_id in HUBS.items():
                at = self.arrival_s(trip, name)
                if 0 <= at < self.duration_s:
                    if id(trip) in event_riders:
                        riders = round(event_riders[id(trip)] * EVENT_HUB_SPLIT[stop_id])
                        trip.transfers[stop_id] = riders
                    else:
                        others.append((at, trip, stop_id))
        base = demand - sum(e["riders"] for e in self.events)
        others.sort(key=lambda item: (item[0], item[1].train_id, item[2]))
        if others:
            each, extra = divmod(max(0, base), len(others))
            for i, (_, trip, stop_id) in enumerate(others):
                trip.transfers[stop_id] = each + (1 if i < extra else 0)

    def arrival_s(self, trip, station):
        return trip.depart_s + self.lines[trip.direction].arrival_offset(station)

    def hub_arrivals(self):
        """(arrival second, trip, bus stop id, riders) at Central and UW, in order."""
        out = []
        for trip in self.trips:
            for name, stop_id in HUBS.items():
                at = self.arrival_s(trip, name)
                if 0 <= at < self.duration_s + 60:
                    out.append((at, trip, stop_id, trip.transfers.get(stop_id, 0)))
        return sorted(out, key=lambda item: (item[0], item[1].train_id))

    def state(self, trip, t):
        """Where `trip` is `t` seconds into the simulation.

        None before it leaves its terminal or after it reaches the far end;
        otherwise (nose_m, station it is standing at or None, leg index).
        A train leaves its first terminal at once and dwells DWELL_S at every
        later stop before running the next leg.
        """
        line = self.lines[trip.direction]
        x = t - trip.depart_s
        if x < 0 or x > line.trip_seconds:
            return None
        for i, leg in enumerate(line.legs):
            start = line.offsets[i] + (DWELL_S if i else 0.0)
            if x < start:
                return leg.from_m, leg.from_station, i
            if x <= line.offsets[i + 1]:
                return leg.from_m + leg.run().distance(x - start), None, i
        last = line.legs[-1]
        return last.to_m, last.to_station, len(line.legs) - 1

    def nose(self, trip, t):
        found = self.state(trip, t)
        return None if found is None else found[0]

    def schedule(self):
        """Everything the browser needs to reproduce train positions exactly."""
        return {
            "headway_min": self.headway_s / 60.0,
            "dwell_s": DWELL_S,
            "train_length_m": ION_LENGTH,
            "lines": {
                direction: {
                    "stops": [{"name": name, "nose_m": round(s, 3)} for name, s in line.stops],
                    "legs": [{"from_m": leg.from_m, "to_m": leg.to_m, "run_s": leg.run_s} for leg in line.legs],
                }
                for direction, line in self.lines.items()
            },
            "trips": [
                {
                    "id": trip.train_id,
                    "direction": trip.direction,
                    "scheduled_s": trip.scheduled_s,
                    "depart_s": trip.depart_s,
                    "hold": trip.hold_reason,
                    "transfers": trip.transfers,
                }
                for trip in self.trips
            ],
        }


def position(direction, nose_m):
    """Lon/lat of the middle of a train whose nose is at `nose_m`."""
    path = corridor.DATA["ion"][direction]["path"]
    lon, lat = corridor._lerp_path(path, nose_m - ION_LENGTH / 2.0)
    return round(lon, 6), round(lat, 6)
