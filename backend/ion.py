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
    """A timetabled trip. `train_id` is the trip ID (ION-N3); the physical train
    that runs it is `vehicle_id`, chosen by dispatch in rail_ops."""
    train_id: str
    direction: str
    scheduled_s: float  # terminal departure on the timetable
    depart_s: float     # actual terminal departure (after holds, turnbacks and spacing)
    hold_reason: str = ""
    transfers: dict = field(default_factory=dict)  # bus stop id -> riders transferring there
    trip_hold_s: float = 0.0  # scenario hold on this trip at its first terminal
    vehicle_id: str | None = None
    platform: str | None = None
    station_times: dict = field(default_factory=dict)  # station name -> arrival (actual)
    decisions: list = field(default_factory=list)

    @property
    def trip_id(self):
        return self.train_id

    @property
    def delay_s(self):
        return self.depart_s - self.scheduled_s


class IonPlan:
    """Every ION trip on the line during the simulated hour, run by physical
    trains through the terminals (rail_ops.RailOps).

    The timetable starts well before minute 0 (a warm-up of one full cycle),
    so the trains on the line at minute 0 got there through real turnbacks.
    """

    def __init__(self, duration_min=60, headway_min=8, transfer_demand=110, delay_min=0,
                 platform_hold=None, reassign=True, closures=None, yard_block=None, fault=None, rail_overshoot=None, continuous=False):
        import rail_ops
        self.continuous = continuous
        self.headway_s = headway_min * 60.0
        self.duration_s = duration_min * 60.0
        self.lines = {"north": Line("north"), "south": Line("south")}
        self.trips = []
        cycle = self.lines["north"].trip_seconds + self.lines["south"].trip_seconds + 2 * rail_ops.TURN_S
        warm = cycle + 2 * self.headway_s
        self.scheduled_through = self.duration_s
        self.trips.extend(self.timetable(-warm, self.scheduled_through, warmup=True))
        self.platform_hold = platform_hold
        self.events = self._assign_events(transfer_demand, delay_min)
        self.closures = closures or []
        self.yard_block = yard_block
        self.fault = fault
        self.ops = rail_ops.RailOps(self, platform_hold=platform_hold, reassign=reassign, closures=self.closures,
                                    yard_block=yard_block, fault=fault, overshoot=rail_overshoot)
        if continuous:
            self.extend_rail(self.duration_s)
        else:
            self.ops.run()
        for trip in self.trips:
            if trip.station_times:
                trip.depart_s = trip.depart_s  # set by RailOps at departure
                late = trip.depart_s - trip.scheduled_s
                if late > 1 and not trip.hold_reason:
                    trip.hold_reason = getattr(trip, "depart_reason", "")
        self._assign_transfers(transfer_demand)

    def timetable(self, start, end, warmup=False):
        for direction, offset in (("north", 0.0), ("south", self.headway_s / 2.0)):
            first = math.floor((start - offset) / self.headway_s) + (0 if warmup else 1)
            last = math.floor((end - offset) / self.headway_s)
            prefix = "N" if direction == "north" else "S"
            for k in range(first, last + 1):
                t = offset + k * self.headway_s
                yield Trip(f"ION-{prefix}{k}", direction, t, t)

    def extend_rail(self, end_s):
        # Keep trips in the dispatcher’s planning window before processing due events.
        through = end_s + 1800.0
        new_trips = sorted(self.timetable(self.scheduled_through, through), key=lambda trip: trip.scheduled_s)
        self.trips.extend(new_trips)
        for trip in new_trips:
            self.ops.schedule_trip(trip)
        self.scheduled_through = through
        self.ops.advance(end_s)

    def _assign_events(self, demand, delay_min):
        """The two big transfer waves ride the first northbound trains due at
        Central after minutes 12 and 32; the scenario can hold them late."""
        north = self.lines["north"]
        at_central = north.arrival_offset("Central Station")
        events = []
        for minute, share, delay in ((12, EVENT_SPLIT[0], delay_min), (32, EVENT_SPLIT[1], delay_min // 2)):
            candidates = [
                t for t in self.trips if t.direction == "north" and minute * 60.0 <= t.scheduled_s + at_central < self.duration_s
                and all(t is not e["trip"] for e in events)
            ]
            if not candidates:
                # A run too short to reach this wave: its riders are spread
                # over the other arrivals instead.
                continue
            trip = min(candidates, key=lambda t: t.scheduled_s)
            if delay:
                trip.trip_hold_s = delay * 60.0
                trip.hold_reason = f"held {delay} min at Fairway"
            riders = round(demand * EVENT_SHARE * share)
            events.append({"trip": trip, "riders": riders, "minute": minute})
        return events

    def _assign_transfers(self, demand):
        """Give every transfer rider a hub arrival inside the run.

        An event train's riders are split over the hubs it reaches within the
        run (the last hub takes the remainder, so rounding never creates or
        loses a rider). Riders of an event train that reaches neither hub in
        time, and all other demand, are spread evenly over the other arrivals.
        """
        events = {id(e["trip"]): e for e in self.events}
        others = []
        for trip in self.trips:
            if id(trip) not in events:
                for name, stop_id in HUBS.items():
                    at = self.arrival_s(trip, name)
                    if 0 <= at < self.duration_s:
                        others.append((at, trip, stop_id))
        base = demand
        for event in self.events:
            trip, riders = event["trip"], event["riders"]
            reached = [stop_id for name, stop_id in HUBS.items() if 0 <= self.arrival_s(trip, name) < self.duration_s]
            if not reached:
                continue
            share = sum(EVENT_HUB_SPLIT[stop_id] for stop_id in reached)
            left = riders
            for stop_id in reached[:-1]:
                trip.transfers[stop_id] = round(riders * EVENT_HUB_SPLIT[stop_id] / share)
                left -= trip.transfers[stop_id]
            trip.transfers[reached[-1]] = left
            base -= riders
        others.sort(key=lambda item: (item[0], item[1].train_id, item[2]))
        if others:
            each, extra = divmod(max(0, base), len(others))
            for i, (_, trip, stop_id) in enumerate(others):
                trip.transfers[stop_id] = each + (1 if i < extra else 0)

    def arrival_s(self, trip, station):
        """When `trip` reaches `station` (actual, from the rail run; the
        timetable offset for trips the run did not reach)."""
        if station in trip.station_times:
            return trip.station_times[station]
        return trip.depart_s + self.lines[trip.direction].arrival_offset(station)

    def predict_arrival(self, trip, station, now):
        """When `trip` should reach `station`, using only what is known at `now`:
        its actual time if it is already there; otherwise the last station it
        has reached (or its terminal departure, or its scheduled departure plus
        any announced hold if it has not left yet) plus the timetabled running
        time from there. Returns (seconds, "confirmed" | "predicted")."""
        if trip.station_times.get(station, math.inf) <= now:
            return trip.station_times[station], "confirmed"
        line = self.lines[trip.direction]
        seen = [(t, name) for name, t in trip.station_times.items() if t <= now]
        if seen:
            t_last, last = max(seen)
            return t_last + line.arrival_offset(station) - line.arrival_offset(last), "predicted"
        start = max(now, trip.scheduled_s + trip.trip_hold_s)
        return start + line.arrival_offset(station), "predicted"

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

        None before its train leaves the first terminal or after it stops at
        the far one; otherwise (nose_m on the line, station it stands at or
        None, leg index). In a terminal's trackwork the nose is mapped onto
        the line (see rail_ops.line_nose).
        """
        found = self.ops.trip_pose(trip, t)
        if found is None:
            return None
        return found["nose_line"], found["station"], found["leg"]

    def nose(self, trip, t):
        found = self.state(trip, t)
        return None if found is None else found[0]

    def trains_at(self, t):
        """Every train in service at t: vehicle, trip and position."""
        return self.ops.poses(t)

    def schedule(self, replay_start=None):
        """Everything the browser needs to reproduce train positions exactly:
        the line legs, each physical train's movement plan, and the decisions."""
        import rail_ops
        start = 0 if replay_start is None else replay_start
        history_start = -600 if replay_start is None else replay_start
        window = [t for t in self.trips if start <= t.scheduled_s < self.duration_s]
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
                    "vehicle_id": trip.vehicle_id,
                    "platform": trip.platform,
                    "arrival_platform": getattr(trip, "arrival_platform", None),
                    "dispatch": getattr(trip, "dispatch_note", ""),
                    "decisions": [d.id for d in trip.decisions],
                }
                for trip in self.trips
                if trip.station_times and min(trip.station_times.values()) < self.duration_s + 600
                and max(trip.station_times.values()) > history_start
            ],
            "vehicles": self.ops.plans(),
            "decisions": [d.to_dict() for d in self.ops.decisions if history_start <= d.t <= self.duration_s + 600],
            "holds": self.ops.holds,
            "metrics": self.ops.metrics(window, start),
            "platform_hold": self.platform_hold,
            "closures": [
                {**cp["closure"], "plan": cp["kind"], "diversion": cp["route"]["id"] if cp["kind"] == "diversion" else None}
                for cp in self.ops.closures
            ],
            # Line diversions the trains used (paths, stops and speed limits for the map).
            "routes": {**{cp["route"]["id"]: cp["route"] for cp in self.ops.closures if cp["kind"] == "diversion"},
                       **{r: rail_ops.YARD_ROUTES[r] for r in sorted({seg.route for tr in self.ops.trains for seg in tr.segments
                                                                       if seg.route in rail_ops.YARD_ROUTES})}},
            "yard": {
                "block": self.yard_block, "fault": self.fault,
                "moves": self.ops.yard.log,
                "fleet": self.ops.yard.inventory(),
            },
            "sections": {k: v["log"] for k, v in self.ops.sections.items()},
        }


def position(direction, nose_m):
    """Lon/lat of the middle of a train whose nose is at `nose_m`."""
    path = corridor.DATA["ion"][direction]["path"]
    lon, lat = corridor._lerp_path(path, nose_m - ION_LENGTH / 2.0)
    return round(lon, 6), round(lat, 6)
