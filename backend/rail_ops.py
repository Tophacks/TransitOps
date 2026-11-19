"""ION rail operations: physical trains, terminal station control and dispatch.

The timetable (ion.IonPlan) lists trips. This module runs them with physical
trains, as a deterministic discrete-event simulation in seconds:

Vehicle agents (Train)
    A train has a persistent vehicle ID (LRV501, ...) separate from the trip
    IDs it runs (ION-N3, ION-S7). It carries one trip at a time, turns back
    at each terminal (stops, changes its active cab, departs on its next
    trip) and keeps its movement plan as a list of segments: line runs
    between stations, moves through a terminal on the real trackwork, and
    stands (dwell, cab change, waiting at a signal). Approaching a terminal
    it asks station control for a platform; when the answer is "wait" it
    plans a stop at the home signal (or behind the train already waiting).

Station control (StationControl)
    Owns a terminal's interlocking. Every move (arrive at platform P, depart
    from platform P) needs a set of resources: switches, the straight and
    crossover legs, the diamond and the platform track (ion_terminals.json).
    A move is granted only when no other train holds any of its resources.
    Grants create reservations; each one is released when the train's rear
    clears that resource (plus the fouling distance), not when the move ends.
    Occupancy (nose in to rear out) is recorded separately from reservations.
    Requests are served in an explicit order: departures before arrivals
    (a departure frees a platform), then earliest request, then vehicle ID.
    Approaching trains are served first come, first served: they are on the
    same track and cannot overtake. Platforms are tried in the terminal's
    preference order; the alternate is used only through a mapped route.

Dispatch (Dispatch)
    Assigns each outbound trip to a train at the terminal (first in, first
    out), holds departures to keep MIN_SEPARATION_S behind the previous
    departure in the same direction, and, when a train's departure is
    threatened (a platform hold, a late arrival), may give its trip to
    another train already standing at the terminal and ready, under a
    documented rule (SWAP_GAIN_S). There are no rail spares in this model
    yet: the Dutton Drive yard is not connected to the simulated track.

Every choice is a decision record (who, which trip, what changed, the options
considered and why each was rejected, what was chosen, and what happened
next). The simulation event log and the map's Decision Inspector read them.

Modelled, not observed: turnaround and cab-change times, platform preference,
the swap rule, request distance and the home-signal position. See
docs/ion-terminal-operations.md.
"""
import heapq
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

TERMINALS = json.loads((Path(__file__).resolve().parent / "ion_terminals.json").read_text())["terminals"]
NETWORK = json.loads((Path(__file__).resolve().parent / "ion_network.json").read_text())
DIVERSIONS = {d["id"]: d for d in NETWORK["diversions"]}


def route_def(terminal, name):
    """A move's route: a terminal layout's, or a line diversion ("network")."""
    if terminal == "network":
        return DIVERSIONS[name]
    return TERMINALS[terminal]["routes"][name]

# Modelled operating parameters (seconds unless noted).
CAB_OFF_S = 40.0          # after stopping: doors, then the operator shuts down the arriving cab
CAB_CHANGE_S = 100.0      # walking to the other cab and bringing it up
TURN_S = 240.0            # minimum time from stopping to departing (riders off, cab change, checks)
REQUEST_M = 400.0         # an approaching train asks for its route this far before the home signal
QUEUE_GAP_M = 15.0        # a second waiting train stops this far behind the first
SWAP_GAIN_S = 120.0       # dispatch swaps trips only if that saves at least this much
PLATFORM_GAP_S = 30.0     # a train may arrive at a platform this long after the previous one has left
FLEET_MARGIN_S = 60.0     # recovery margin per terminal when sizing the fleet
SPAWN_UNTIL_S = None      # set by RailOps: trains may enter service (from the yard) only during warm-up


@dataclass
class Decision:
    id: str
    t: float
    level: str              # "vehicle", "station" or "dispatch"
    terminal: str
    vehicle_id: str
    trip_id: str | None
    trigger: str            # what changed
    options: list           # [{"option", "feasible", "reason", "estimate_s"}]
    chosen: str
    outcome: str = ""       # what happened next (filled in later)

    def to_dict(self):
        return {
            "id": self.id, "t": round(self.t, 1), "minute": math.floor(self.t / 60.0), "level": self.level,
            "terminal": self.terminal, "vehicle_id": self.vehicle_id, "trip_id": self.trip_id,
            "trigger": self.trigger, "options": self.options, "chosen": self.chosen, "outcome": self.outcome,
        }


class Profile:
    """Speed profile on a 1 m grid with constant acceleration per cell (a port of
    frontend/lib/motion/fairway.ts Profile; keep the arithmetic in step)."""

    def __init__(self, length, cap, v0, stop, accel, decel):
        n = max(1, math.ceil(length))
        self.ds = length / n
        self.length = length
        v = [cap(i * self.ds) for i in range(n + 1)]
        v[0] = min(v[0], v0)
        if stop:
            v[n] = 0.0
        for i in range(1, n + 1):
            v[i] = min(v[i], math.sqrt(v[i - 1] ** 2 + 2.0 * accel * self.ds))
        for i in range(n - 1, -1, -1):
            v[i] = min(v[i], math.sqrt(v[i + 1] ** 2 + 2.0 * decel * self.ds))
        self.v = v
        self.t = [0.0]
        for i in range(1, n + 1):
            self.t.append(self.t[i - 1] + (2.0 * self.ds) / max(1e-6, v[i - 1] + v[i]))
        self.duration = self.t[n]

    def _cell(self, time):
        lo, hi = 0, len(self.t) - 1
        while hi - lo > 1:
            mid = (lo + hi) >> 1
            if self.t[mid] <= time:
                lo = mid
            else:
                hi = mid
        return lo, hi

    def distance(self, time):
        if time <= 0:
            return 0.0
        if time >= self.duration:
            return self.length
        lo, hi = self._cell(time)
        v0, v1 = self.v[lo], self.v[hi]
        a = (v1 * v1 - v0 * v0) / (2.0 * self.ds)
        tau = time - self.t[lo]
        return min(hi * self.ds, lo * self.ds + v0 * tau + 0.5 * a * tau * tau)

    def speed(self, time):
        if time <= 0:
            return self.v[0]
        if time >= self.duration:
            return self.v[-1]
        lo, hi = self._cell(time)
        v0, v1 = self.v[lo], self.v[hi]
        a = (v1 * v1 - v0 * v0) / (2.0 * self.ds)
        return max(0.0, v0 + a * (time - self.t[lo]))

    def time_at(self, s):
        """Seconds until the profile has covered `s` metres (inf if it never does)."""
        if s <= 0:
            return 0.0
        if s > self.length + 1e-9:
            return math.inf
        i = min(len(self.v) - 2, int(s / self.ds))
        v0, v1 = self.v[i], self.v[i + 1]
        d = s - i * self.ds
        a = (v1 * v1 - v0 * v0) / (2.0 * self.ds)
        if abs(a) < 1e-12:
            tau = d / max(1e-9, v0)
        else:
            tau = (-v0 + math.sqrt(max(0.0, v0 * v0 + 2.0 * a * d))) / a
        return self.t[i] + tau


def route_cap(route, s0, top):
    """Speed limit at metre x of a move that starts at s0 on `route`'s path:
    zones (whole-train limits through the interlocking) and point curve caps."""
    zones = route["zones"]
    curves = route["curves"]

    def cap(x):
        s = s0 + x
        v = top
        for a, b, vz in zones:
            if a <= s <= b and vz < v:
                v = vz
        for c, vc in curves:
            if abs(s - c) <= 0.5 and vc < v:
                v = vc
        return v

    return cap


_PROFILES = {}


def move_profile(terminal, route_name, s0, s1, stop=True):
    import ion
    key = (terminal, route_name, round(s0, 3), round(s1, 3), stop)
    if key not in _PROFILES:
        route = route_def(terminal, route_name)
        _PROFILES[key] = Profile(s1 - s0, route_cap(route, s0, ion.ION_TOP_SPEED), 0.0, stop, ion.ION_ACCEL, ion.ION_DECEL)
    return _PROFILES[key]


@dataclass
class Segment:
    kind: str               # "move", "run" or "stand"
    t0: float
    t1: float
    lead: str               # "A" or "B": the cab at the front
    trip_id: str | None
    terminal: str | None = None
    route: str | None = None
    s0: float = 0.0
    s1: float = 0.0
    line: str | None = None
    leg: int | None = None
    station: str | None = None
    note: str = ""
    lights: dict = field(default_factory=dict)  # stand: {"off": t, "on": t} for a cab change

    def to_dict(self):
        out = {"kind": self.kind, "t0": self.t0, "t1": self.t1, "lead": self.lead,
               "trip": self.trip_id}
        if self.kind in ("move", "stand") and self.route:
            out.update({"terminal": self.terminal, "route": self.route, "s0": self.s0, "s1": self.s1})
        if self.kind == "run" or (self.kind == "stand" and self.line):
            out.update({"line": self.line, "leg": self.leg, "s0": self.s0, "s1": self.s1})
        if self.station:
            out["station"] = self.station
        if self.note:
            out["note"] = self.note
        if self.lights:
            out["lights"] = dict(self.lights)
        return out


@dataclass
class Train:
    vehicle_id: str
    lead: str = "A"
    segments: list = field(default_factory=list)
    trip: object = None           # the trip it is running or about to run
    next_trip: object = None      # its next assigned trip at the terminal
    terminal: str | None = None   # terminal it is standing at
    platform: str | None = None
    arrived_s: float | None = None
    ready_s: float | None = None
    hold_until: float = -math.inf
    hold_reason: str = ""
    waiting_at: float | None = None   # path position where it waits for its route
    approach: dict | None = None      # pending arrival: {"t0", "trip"}

    def add(self, segment):
        if self.segments and segment.t0 < self.segments[-1].t1 - 1e-6:
            raise RuntimeError(f"{self.vehicle_id}: overlapping segments")
        self.segments.append(segment)


class StationControl:
    """Interlocking at one terminal: reservations, occupancy and route grants."""

    def __init__(self, ops, key):
        self.ops = ops
        self.key = key
        self.layout = TERMINALS[key]
        self.reservations = {}   # resource -> list of [vehicle, t_from, t_to or None, move]
        self.occupancy = []      # [resource, vehicle, t_in, t_out or None, move]
        self.approach = []       # trains approaching / waiting, in arrival order

    def route(self, name):
        return self.layout["routes"][name]

    def holders(self, resource, t, vehicle):
        """Other trains holding `resource` at time t."""
        out = []
        for holder, t_from, t_to, move in self.reservations.get(resource, []):
            if holder != vehicle and t_from <= t and (t_to is None or t_to > t + 1e-9):
                out.append((holder, move))
        return out

    def blockers(self, route_name, t, vehicle):
        """Why a move cannot be granted now: [(resource, holder, holder's move)]."""
        out = []
        for res in self.route(route_name)["resources"]:
            for holder, move in self.holders(res["id"], t, vehicle):
                out.append((res["id"], holder, move))
        return out

    def reserve(self, route_name, vehicle, t_grant, release_at, occupancy):
        """Reserve every resource of a move from t_grant; release_at(res) gives the
        release time (None: held until a later move releases it)."""
        for res in self.route(route_name)["resources"]:
            entries = self.reservations.setdefault(res["id"], [])
            # A train's own open reservation (its platform) is closed by this move.
            for entry in entries:
                if entry[0] == vehicle and entry[2] is None:
                    entry[2] = release_at(res)
                    entry[3] = route_name  # released by this move
                    break
            else:
                entries.append([vehicle, t_grant, release_at(res), route_name])
            t_in, t_out = occupancy(res)
            for occ in self.occupancy:
                if occ[0] == res["id"] and occ[1] == vehicle and occ[3] is None:
                    occ[3] = t_out
                    occ[4] = route_name
                    break
            else:
                if t_in is not None:
                    self.occupancy.append([res["id"], vehicle, t_in, t_out, route_name])

    def reserve_platform(self, platform, vehicle, t):
        rid = self.layout["platforms"][platform]["edge"]
        self.reservations.setdefault(rid, []).append([vehicle, t, None, "stand"])
        self.occupancy.append([rid, vehicle, t, None, "stand"])

    def release_times(self):
        times = set()
        for entries in self.reservations.values():
            for entry in entries:
                if entry[2] is not None:
                    times.add(entry[2])
        return times


class RailOps:
    """Runs every trip of an IonPlan with physical trains (see module docstring)."""

    def __init__(self, plan, platform_hold=None, reassign=True, start_s=None, end_s=None, closures=None):
        import ion
        self.ion = ion
        self.plan = plan
        self.reassign = reassign
        self.hold_spec = platform_hold
        cycle = self.trip_seconds("north") + self.trip_seconds("south") + 2 * TURN_S
        self.start_s = start_s if start_s is not None else min(t.scheduled_s for t in plan.trips) - 60.0
        # Fleet for the timetable: one round trip with the minimum turnaround
        # and a minute's margin at each end, at the headway. Trains enter
        # service from the yard (warm-up) until the fleet is complete.
        self.fleet = math.ceil((cycle + 2 * FLEET_MARGIN_S) / plan.headway_s)
        self.spawn_until = math.inf
        self.end_s = end_s if end_s is not None else plan.duration_s + cycle
        self.stations = {key: StationControl(self, key) for key in TERMINALS}
        self.trains = []
        self.decisions = []
        self.events = []
        self.queue = []
        self.seq = 0
        self.last_departure = {"north": -math.inf, "south": -math.inf}
        self.departed = []          # (trip, t)
        self.holds = []             # applied platform holds
        self.waiting_order = []     # trains waiting for an earlier trip to leave
        self.sections = {}          # single-track sections: holders, waiting trains, log
        self.platform_at = {}       # (direction, stop index) -> (train, leaves at)
        self.platform_waiters = {}
        self.closures = []
        self.terminal_of = {"north": "fairway", "south": "conestoga"}  # where a direction's trips start
        for trip in sorted(plan.trips, key=lambda t: (t.scheduled_s, t.train_id)):
            trip.vehicle_id = None
            trip.platform = None
            trip.station_times = {}
            trip.arrived_s = None
            trip.dispatch_note = ""
            trip.decisions = []
            trip.trip_hold_s = getattr(trip, "trip_hold_s", 0.0)
            self.push(trip.scheduled_s, 3, "due", trip=trip)
        if platform_hold:
            self.push(platform_hold["minute"] * 60.0, 0, "hold", spec=platform_hold)
        self.closures = self.resolve_closures(closures)
        for cp in self.closures:
            if cp.get("section"):
                self.push(cp["end"], 1, "closure_end", section=cp["section"])

    def trip_seconds(self, direction):
        """Platform to platform on the preferred routes, dwells included."""
        line = self.plan.lines[direction]
        start = "fairway" if direction == "north" else "conestoga"
        end = "conestoga" if direction == "north" else "fairway"
        dep = TERMINALS[start]["routes"][f"depart:{TERMINALS[start]['preference'][0]}"]
        arr = TERMINALS[end]["routes"][f"arrive:{TERMINALS[end]['preference'][0]}"]
        total = move_profile(start, f"depart:{dep['platform']}", dep["start_m"], dep["length_m"]).duration
        total += move_profile(end, f"arrive:{arr['platform']}", 0.0, arr["stop_m"]).duration
        total += sum(leg.run_s for leg in line.legs[1:-1]) + self.ion.DWELL_S * (len(line.legs) - 1)
        return total

    # --- event queue -------------------------------------------------------
    def push(self, t, priority, kind, **data):
        self.seq += 1
        heapq.heappush(self.queue, (t, priority, self.seq, kind, data))

    def decide(self, t, level, terminal, train, trip, trigger, options, chosen):
        d = Decision(f"D{len(self.decisions) + 1:03d}", t, level, terminal,
                     train.vehicle_id if train else "-", trip.train_id if trip else None, trigger, options, chosen)
        self.decisions.append(d)
        return d

    def run(self):
        self.now = self.start_s
        while self.queue:
            t, _, _, kind, data = heapq.heappop(self.queue)
            if t > self.end_s:
                break
            self.now = t
            getattr(self, f"on_{kind}")(t, **data)
        return self

    # --- departures ----------------------------------------------------------
    def depart_route(self, train):
        return f"depart:{train.platform}"

    def not_before(self, train, trip):
        """Earliest departure for `train` on `trip`, with the reason that binds."""
        sep = self.last_departure[trip.direction] + self.ion.MIN_SEPARATION_S
        # Trips leave in timetable order: the one before must have gone.
        before = self.previous_trip(trip)
        if before is not None and before.depart_s is not None and not before.station_times:
            return (math.inf, f"waits for {before.train_id} to leave first (timetable order)")
        limits = [
            (trip.scheduled_s, "timetable"),
            (train.ready_s if train.ready_s is not None else math.inf, "turnaround"),
            (sep, f"{self.ion.MIN_SEPARATION_S / 60:.0f}-minute spacing behind the previous departure"),
            (train.hold_until, train.hold_reason or "platform hold"),
            (trip.scheduled_s + trip.trip_hold_s, trip.hold_reason or "scenario hold"),
        ]
        return max(limits, key=lambda x: x[0])

    def schedule_departure(self, t, train, at):
        """Try to depart at `at`; if it waits on an earlier trip, try again when that trip leaves."""
        if math.isinf(at):
            if train not in self.waiting_order:
                self.waiting_order.append(train)
            return
        self.push(max(t, at), 2, "try_depart", train=train)

    def previous_trip(self, trip):
        earlier = [x for x in self.plan.trips if x.direction == trip.direction and x.scheduled_s < trip.scheduled_s]
        return max(earlier, key=lambda x: x.scheduled_s, default=None) if earlier else None

    def on_due(self, t, trip):
        """A trip's scheduled departure time: dispatch checks it is covered."""
        if trip.station_times or trip.vehicle_id is not None:
            return  # already left
        key = self.terminal_of[trip.direction]
        st = self.stations[key]
        train = next((tr for tr in self.trains if tr.next_trip is trip), None)
        if train is None:
            # No train assigned: in warm-up a train enters service from the yard.
            if len(self.trains) < self.fleet:
                self.spawn(t, key, trip)
            else:
                waiting = [tr for tr in self.trains if tr.terminal == key and tr.next_trip is None]
                if waiting:
                    self.assign(t, waiting[0], trip, "a train standing here had no trip")
                else:
                    self.decide(t, "dispatch", key, None, trip, "No train available for this departure",
                                [{"option": "wait for the next arriving train", "feasible": True, "reason": "", "estimate_s": None}],
                                "wait for the next arriving train")
            return
        self.review(t, train, trip, "scheduled departure time")

    def review(self, t, train, trip, why):
        """Dispatch review: keep this train on the trip, or give it to another one."""
        key = self.terminal_of[trip.direction]
        est_a, reason_a = self.estimate(train, trip)
        if not self.reassign or trip.trip_hold_s or est_a - trip.scheduled_s < SWAP_GAIN_S:
            if train.terminal == key:
                self.schedule_departure(t, train, est_a)
            return
        if math.isinf(est_a):
            # Waiting on an earlier trip: swapping cannot make this one leave sooner.
            if train.terminal == key:
                self.schedule_departure(t, train, est_a)
            return
        options = [{"option": f"keep {train.vehicle_id}", "feasible": True,
                    "reason": f"departs {fmt(est_a)} ({reason_a}), {plus(est_a - trip.scheduled_s)}",
                    "estimate_s": round(est_a, 1)}]
        best = None
        for other in sorted(self.trains, key=lambda x: x.vehicle_id):
            if other is train or other.terminal != key or other.next_trip is None:
                continue
            other_trip = other.next_trip
            if other_trip.scheduled_s <= trip.scheduled_s:
                options.append({"option": f"swap with {other.vehicle_id}", "feasible": False,
                                "reason": f"its own trip {other_trip.train_id} leaves first", "estimate_s": None})
                continue
            if other_trip.trip_hold_s:
                options.append({"option": f"swap with {other.vehicle_id}", "feasible": False,
                                "reason": f"its trip {other_trip.train_id} is held by the scenario", "estimate_s": None})
                continue
            est_b, reason_b = self.estimate(other, trip)
            if math.isinf(est_b[0] if isinstance(est_b, tuple) else est_b):
                options.append({"option": f"swap with {other.vehicle_id}", "feasible": False,
                                "reason": reason_b, "estimate_s": None})
                continue
            if est_b > est_a - SWAP_GAIN_S:
                options.append({"option": f"swap with {other.vehicle_id}", "feasible": False,
                                "reason": f"would leave {fmt(est_b)} ({reason_b}): saves under {SWAP_GAIN_S / 60:.0f} min",
                                "estimate_s": round(est_b, 1)})
                continue
            options.append({"option": f"swap with {other.vehicle_id}", "feasible": True,
                            "reason": f"ready at platform {other.platform}, departs {fmt(est_b)} ({reason_b})",
                            "estimate_s": round(est_b, 1)})
            if best is None or est_b < best[1]:
                best = (other, est_b)
        options.append({"option": "send a spare train", "feasible": False,
                        "reason": "no spare: the Dutton Drive yard is not connected to the simulated track yet",
                        "estimate_s": None})
        if best is None:
            d = self.decide(t, "dispatch", key, train, trip, f"{trip.train_id} threatened: {why}", options,
                            f"keep {train.vehicle_id}")
            trip.decisions.append(d)
            if train.terminal == key:
                self.schedule_departure(t, train, est_a)
            return
        other, est_b = best
        other_trip = other.next_trip
        d = self.decide(t, "dispatch", key, train, trip, f"{trip.train_id} threatened: {why}", options,
                        f"swap with {other.vehicle_id}")
        for earlier in trip.decisions + other_trip.decisions:
            if earlier.level == "dispatch" and not earlier.outcome:
                earlier.outcome = f"superseded by {d.id}"
        trip.decisions.append(d)
        other_trip.decisions.append(d)
        d.swap = (other, trip, train, other_trip)
        other.next_trip, train.next_trip = trip, other_trip
        trip.dispatch_note = f"reassigned from {train.vehicle_id} to {other.vehicle_id}"
        other_trip.dispatch_note = f"reassigned from {other.vehicle_id} to {train.vehicle_id}"
        self.schedule_departure(t, other, est_b)
        if train.terminal == key:
            est, _ = self.estimate(train, other_trip)
            self.schedule_departure(t, train, est)

    def estimate(self, train, trip):
        if train.terminal == self.terminal_of[trip.direction]:
            return self.not_before(train, trip)
        # Still approaching: its expected stop plus the turnaround.
        return (self.expected_arrival(train) + TURN_S, "late arrival")

    def expected_arrival(self, train):
        """When an approaching train should stop at its platform if given a clear road now."""
        last = train.segments[-1] if train.segments else None
        if last is None:
            return math.inf
        if math.isinf(last.t1) and train.waiting_at is not None:
            st = self.stations[train.approach["terminal"]]
            route = st.route(f"arrive:{st.layout['preference'][0]}")
            return max(self.now, last.t0) + move_profile(st.key, route["kind"] + ":" + route["platform"],
                                                        train.waiting_at, route["stop_m"]).duration
        if train.approach is None and train.terminal is None:
            dest = "conestoga" if train.trip.direction == "north" else "fairway"
            st = self.stations[dest]
            pref = st.layout["preference"][0]
            return last.t1 + move_profile(dest, f"arrive:{pref}", 0.0, st.route(f"arrive:{pref}")["stop_m"]).duration
        return last.t1

    def on_try_depart(self, t, train):
        trip = train.next_trip
        if trip is None or train.terminal is None:
            return
        at, reason = self.not_before(train, trip)
        st = self.stations[train.terminal]
        if math.isinf(at):
            # Waiting on another trip: retried when that trip leaves.
            self.schedule_departure(t, train, at)
            return
        if at > t + 1e-6:
            self.push(at, 2, "try_depart", train=train)
            return
        route_name = self.depart_route(train)
        # The first platform on the line must be free when the train gets there.
        holder = self.platform_at.get((trip.direction, 1))
        if holder is not None and holder[0] is not train:
            route = st.route(route_name)
            arrive = t + move_profile(train.terminal, route_name, route["start_m"], route["length_m"]).duration
            if True:  # the platform ahead must actually be empty
                key = (trip.direction, 1)
                if ("depart", train) not in self.platform_waiters.get(key, []):
                    self.platform_waiters.setdefault(key, []).append(("depart", train))
                if not getattr(train, "logged_platform", False):
                    train.logged_platform = True
                    station = self.plan.lines[trip.direction].stops[1][0].replace(" Station", "")
                    d = self.decide(t, "vehicle", train.terminal, train, trip, f"{station} platform ahead occupied by "
                                    f"{holder[0].vehicle_id}",
                                    [{"option": f"depart for {station}", "feasible": False,
                                      "reason": f"{holder[0].vehicle_id} is held there" if holder[1] == math.inf else
                                      f"{holder[0].vehicle_id} leaves at {fmt(holder[1])}", "estimate_s": None},
                                     {"option": "wait at the terminal platform", "feasible": True,
                                      "reason": "trains never share a stopping point", "estimate_s": None}],
                                    "wait at the terminal platform")
                    trip.decisions.append(d)
                return
        train.logged_platform = False
        blocked = st.blockers(route_name, t, train.vehicle_id)
        if blocked:
            if not getattr(train, "logged_block", False):
                train.logged_block = True
                d = self.decide(t, "station", train.terminal, train, trip,
                                f"Departure from platform {train.platform} requested",
                                [{"option": f"depart from platform {train.platform}", "feasible": False,
                                  "reason": self.why_blocked(st, blocked, t), "estimate_s": None}],
                                "hold at the platform until the route clears")
                trip.decisions.append(d)
            st.pending = getattr(st, "pending", [])
            if ("depart", train) not in st.pending:
                st.pending.append(("depart", train))
            return
        train.logged_block = False
        self.grant_departure(t, train, trip, route_name, reason)

    def grant_departure(self, t, train, trip, route_name, reason):
        key = train.terminal
        st = self.stations[key]
        route = st.route(route_name)
        lead = "B" if train.lead == "A" else "A"
        prof = move_profile(key, route_name, route["start_m"], route["length_m"])
        s0 = route["start_m"]

        def release(res):
            return t + prof.time_at(res["s1"] + self.ion.ION_LENGTH + 0.0 - s0)

        def occ(res):
            t_in = t if res["s0"] <= s0 else t + prof.time_at(res["s0"] - s0)
            return (t_in, release(res))

        st.reserve(route_name, train.vehicle_id, t, release, occ)
        for res in route["resources"]:
            self.push(release(res), 1, "release", terminal=key)
        late = t - trip.scheduled_s
        if late > 30.0:
            d = self.decide(t, "dispatch", key, train, trip, f"{trip.train_id} departure released",
                            [{"option": f"depart at {fmt(trip.scheduled_s)} (timetable)", "feasible": False,
                              "reason": f"held: {reason}", "estimate_s": round(trip.scheduled_s, 1)},
                             {"option": f"depart at {fmt(t)}", "feasible": True, "reason": "every condition met",
                              "estimate_s": round(t, 1)}],
                            f"depart at {fmt(t)}")
            trip.decisions.append(d)
            if "spacing" in reason:
                prev = max((x for x in self.departed if x[0].direction == trip.direction), key=lambda x: x[1], default=None)
                if prev is not None:
                    trip.hold_reason = trip.hold_reason or (
                        f"held {self.ion.MIN_SEPARATION_S / 60:.0f} min behind {prev[0].train_id}")
        # The stand at the platform ends now.
        stand = train.segments[-1]
        stand.t1 = t
        train.lead = lead
        trip.vehicle_id = train.vehicle_id
        trip.platform = train.platform
        trip.depart_s = t
        trip.depart_reason = reason if late > 1 else "on time"
        self.last_departure[trip.direction] = max(self.last_departure[trip.direction], t)
        self.departed.append((trip, t))

        train.add(Segment("move", t, t + prof.duration, lead, trip.train_id, key, route_name, s0, route["length_m"],
                          note=f"departs platform {train.platform}"))
        for d in trip.decisions:
            if not d.outcome and d.level == "dispatch":
                d.outcome = f"{train.vehicle_id} left platform {train.platform} at {fmt(t)} on {trip.train_id} ({plus(late)})"
        line = self.plan.lines[trip.direction]
        trip.station_times = {line.stops[0][0]: t}
        tt = t + prof.duration
        trip.station_times[line.stops[1][0]] = tt
        train.add(Segment("stand", tt, tt + self.ion.DWELL_S, lead, trip.train_id, line=trip.direction, leg=1,
                          s0=line.stops[1][1], s1=line.stops[1][1], station=line.stops[1][0]))
        tt += self.ion.DWELL_S
        self.hold_platform(trip.direction, 1, train, tt)
        train.terminal = None
        train.platform = None
        train.trip = trip
        train.next_trip = None
        train.ready_s = None
        self.plan_line(train, trip, 1, tt)
        # Trains waiting for this trip to leave first may go now (after the spacing).
        for other in [tr for tr in self.waiting_order if tr.next_trip is not None]:
            self.waiting_order.remove(other)
            nb, _ = self.not_before(other, other.next_trip)
            self.schedule_departure(t, other, nb)
        self.retry(t, key)

    # --- line running, closures and single-track sections ------------------------
    def plan_line(self, train, trip, index, tt):
        """The train stands at stops[index] and can leave at tt: decide its next leg then."""
        self.push(tt, 2, "line_depart", train=train, trip=trip, index=index)

    def on_line_depart(self, t, train, trip, index):
        """Leaving a platform on the line: closures first, then the next platform
        must be free when the train gets there (trains never share a stopping point)."""
        line = self.plan.lines[trip.direction]
        if index >= len(line.legs) - 1:
            self.release_platform(t, trip.direction, index, train)
            dest = "conestoga" if trip.direction == "north" else "fairway"
            self.push(t, 4, "approach", train=train, terminal=dest, t0=t)
            return
        if getattr(train, "gate_done", None) != (trip.train_id, index):
            gate = self.closure_gate(trip.direction, index, t)
            if gate is not None:
                self.on_section_request(t, train, trip, index, gate)
                return
        self.run_leg(t, train, trip, index)

    def run_leg(self, t, train, trip, index):
        line = self.plan.lines[trip.direction]
        leg = line.legs[index]
        nxt = (trip.direction, index + 1)
        holder = self.platform_at.get(nxt)
        if holder is not None and holder[0] is not train:
            # Movement authority: the platform ahead must actually be empty (the
            # train there may still be held), not just predicted to be.
            arrive = t + leg.run_s
            leaves = holder[1]
            if True:
                # Wait here until the train ahead has left the next platform.
                stand = train.segments[-1]
                stand.t1 = math.inf
                stand.note = f"waiting: {leg.to_station.replace(' Station', '')} platform occupied by {holder[0].vehicle_id}"
                self.hold_platform(trip.direction, index, train, math.inf)
                self.platform_waiters.setdefault(nxt, []).append((train, trip, index))
                d = self.decide(t, "vehicle", "line", train, trip,
                                f"{leg.to_station.replace(' Station', '')} platform ahead occupied by {holder[0].vehicle_id}",
                                [{"option": f"run to {leg.to_station.replace(' Station', '')}", "feasible": False,
                                  "reason": (f"{holder[0].vehicle_id} is held there" if leaves == math.inf else
                                             f"{holder[0].vehicle_id} leaves at {fmt(leaves)}; arriving at {fmt(arrive)} "
                                             f"would leave under {PLATFORM_GAP_S:.0f} s"), "estimate_s": None},
                                 {"option": f"wait at {line.stops[index][0].replace(' Station', '')}", "feasible": True,
                                  "reason": "trains never share a stopping point", "estimate_s": None}],
                                f"wait at {line.stops[index][0].replace(' Station', '')}")
                trip.decisions.append(d)
                train.platform_wait = d
                return
        stand = train.segments[-1]
        if stand.kind == "stand" and (stand.t1 == math.inf or stand.t1 < t):
            stand.t1 = t
        if getattr(train, "platform_wait", None) is not None:
            train.platform_wait.outcome = f"left at {fmt(t)}"
            train.platform_wait = None
        self.release_platform(t, trip.direction, index, train)
        lead = train.lead
        train.add(Segment("run", t, t + leg.run_s, lead, trip.train_id, line=trip.direction, leg=index,
                          s0=leg.from_m, s1=leg.to_m))
        tt = t + leg.run_s
        trip.station_times[leg.to_station] = tt
        pending = getattr(train, "pending_release", None)
        if pending is not None and leg.from_m <= pending["line_m"] <= leg.to_m:
            release = t + run_time_at(leg.run(), pending["line_m"] - leg.from_m)
            self.push(release, 1, "section_release", section=pending["section"], vehicle=train.vehicle_id)
            if getattr(train, "section_log", None) is not None:
                train.section_log["release_s"] = release
            if getattr(train, "section_decision", None) is not None:
                train.section_decision.outcome += f"; section released at {fmt(release)} when its rear cleared"
            train.pending_release = None
        train.add(Segment("stand", tt, tt + self.ion.DWELL_S, lead, trip.train_id, line=trip.direction, leg=index,
                          s0=leg.to_m, s1=leg.to_m, station=leg.to_station))
        self.platform_at[nxt] = (train, tt + self.ion.DWELL_S)
        self.plan_line(train, trip, index + 1, tt + self.ion.DWELL_S)

    def release_platform(self, t, direction, index, train):
        key = (direction, index)
        holder = self.platform_at.get(key)
        if holder is not None and holder[0] is train:
            del self.platform_at[key]
            for waiter in self.platform_waiters.pop(key, []):
                self.push(t + PLATFORM_GAP_S, 2, "platform_free", waiter=waiter)

    def on_platform_free(self, t, waiter):
        if waiter[0] == "depart":
            self.on_try_depart(t, waiter[1])
            return
        train, trip, index = waiter
        self.run_leg(t, train, trip, index)

    def hold_platform(self, direction, index, train, until):
        self.platform_at[(direction, index)] = (train, until)

    def line_seconds(self, direction, i0, i1):
        """Normal running time from leaving stops[i0] to leaving stops[i1] (dwells included)."""
        line = self.plan.lines[direction]
        return sum(line.legs[i].run_s + self.ion.DWELL_S for i in range(i0, i1))

    def stop_index(self, direction, name):
        return next(i for i, (n, _) in enumerate(self.plan.lines[direction].stops) if n == name)

    def resolve_closures(self, closures):
        """For each closure: the shortest valid diversion that covers it, or none."""
        plans = []
        for c in closures or []:
            closed = c["track"]
            travel = closed
            stations = {st["name"]: st["track_m"] for st in NETWORK["stations"][closed]}
            a, b = sorted((stations[c["from_station"]], stations[c["to_station"]]))
            start, end = c["from_minute"] * 60.0, c["to_minute"] * 60.0
            options = []
            for d in NETWORK["diversions"]:
                if d["closed"] != closed:
                    continue
                entry_m = stations[d["entry_station"]]
                exit_m = stations[d["exit_station"]]
                covers = entry_m < a and exit_m > b
                options.append((covers, d["shared_m"], d))
            covering = sorted((x for x in options if x[0]), key=lambda x: x[1])
            line = self.plan.lines[travel]
            names = [n for n, _ in line.stops]
            # Last platform before the closure, in the direction of travel.
            closed_stops = [n for n in names if n in stations and stations[n] >= a]
            first_closed = names.index(closed_stops[0]) if closed_stops else len(names) - 1
            plan = {"closure": c, "closed": closed, "travel": travel, "start": start, "end": end,
                    "span_m": (a, b), "rejected": [(x[2]["id"], "does not cover the closure") for x in options if not x[0]]}
            if covering:
                d = covering[0][2]
                i_entry = self.stop_index(travel, d["entry_station"])
                i_exit = self.stop_index(travel, d["exit_station"])
                opp = d["opposing"]["direction"]
                oline = self.plan.lines[opp]
                sec0, sec1 = d["opposing"]["section_line_m"]
                o_entry = max(i for i, (_, nose) in enumerate(oline.stops) if nose < sec0)
                o_exit = min(i for i, (_, nose) in enumerate(oline.stops) if nose - self.ion.ION_LENGTH > sec1)
                div_prof = self.diversion_seconds(d)
                plan.update({
                    "kind": "diversion", "route": d, "section": d["section"],
                    "entry_index": i_entry, "exit_index": i_exit,
                    # How long before the closure starts a train leaving the entry platform would
                    # still be in the closed stretch by normal running.
                    "lead_s": self.line_seconds(travel, i_entry, max(i_entry, first_closed)) + 60.0,
                    "divert_s": div_prof,
                    "opposing": opp, "opp_entry_index": o_entry, "opp_exit_index": o_exit,
                    "opp_s": self.line_seconds(opp, o_entry, o_exit),
                })
            else:
                # Hold where the closed stretch begins, in the direction of travel.
                i_block = min(self.stop_index(travel, c["from_station"]), self.stop_index(travel, c["to_station"]))
                plan.update({"kind": "blocked", "block_index": i_block})
            plans.append(plan)
            self.sections.setdefault(plan.get("section"), {"holders": {}, "waiting": [], "log": []}) if plan.get("section") else None
        return plans

    def diversion_seconds(self, d):
        total, s = 0.0, 0.0
        for st in d["stops"]:
            total += move_profile("network", d["id"], s, st["at_m"]).duration + self.ion.DWELL_S
            s = st["at_m"]
        return total

    def closure_gate(self, direction, i, tt):
        """Does a closure need a decision before leaving stops[i] at tt?"""
        for cp in self.closures:
            if cp["kind"] == "diversion":
                if direction == cp["travel"] and i == cp["entry_index"]:
                    if tt < cp["end"] and tt + cp["lead_s"] > cp["start"]:
                        return {"role": "divert", "plan": cp}
                if direction == cp["opposing"] and i == cp["opp_entry_index"]:
                    # Diverted trains can be in the section from (start - lead) until the
                    # last of them clears it: reserve whenever this run could overlap that.
                    busy_from = cp["start"] - cp["lead_s"]
                    sec = self.sections[cp["section"]]
                    diverted_left = any(h["role"] == "divert" for h in sec["holders"].values()) or any(
                        w["gate"]["role"] == "divert" for w in sec["waiting"])
                    if tt >= cp["end"] and not diverted_left:
                        continue
                    if tt + cp["opp_s"] > busy_from:
                        return {"role": "opposing", "plan": cp}
            elif direction == cp["travel"] and i == cp["block_index"]:
                if tt < cp["end"] and tt + self.line_seconds(direction, i, min(i + 2, len(self.plan.lines[direction].legs) - 1)) > cp["start"]:
                    return {"role": "blocked", "plan": cp}
        return None

    def on_section_request(self, t, train, trip, index, gate):
        cp = gate["plan"]
        line = self.plan.lines[trip.direction]
        station = line.stops[index][0]
        c = cp["closure"]
        what = (f"{c['track'].capitalize()}bound track closed between {c['from_station'].replace(' Station', '')} and "
                f"{c['to_station'].replace(' Station', '')}, {fmt(cp['start'])}-{fmt(cp['end'])}")
        if gate["role"] == "blocked":
            options = []
            if cp["rejected"]:
                options.append({"option": "divert over the other track", "feasible": False,
                                "reason": f"{len(cp['rejected'])} diversions exist for this track, none covering this "
                                          "stretch: each crosses back inside it or before it", "estimate_s": None})
            else:
                options.append({"option": "divert over the other track", "feasible": False,
                                "reason": f"no pair of enabled crossovers lets {trip.direction}bound trains cross over "
                                          "and back around this stretch; south of Mill is disabled (CN, not electrified)",
                                "estimate_s": None})
            tb = next((x for x in NETWORK["turnbacks"][trip.direction] if x["station"] == station), None)
            if tb and tb["valid"]:
                options.append({"option": f"turn back at {station.replace(' Station', '')}", "feasible": False,
                                "reason": f"route valid: reverse {tb['reverse_m'] / 1000:.1f} km against the usual direction "
                                          f"to the {tb['site']} crossover. Rejected: it strands riders beyond the closure "
                                          "(no bus bridge modelled) and takes trains away from the far terminal's departures",
                                "estimate_s": None})
            else:
                options.append({"option": f"turn back at {station.replace(' Station', '')}", "feasible": False,
                                "reason": (tb or {}).get("reason", "no validated turnback here"), "estimate_s": None})
            options.append({"option": f"hold at {station.replace(' Station', '')} until the track reopens", "feasible": True,
                            "reason": f"reopens at {fmt(cp['end'])}", "estimate_s": round(cp["end"], 1)})
            d = self.decide(t, "vehicle", "line", train, trip, what, options,
                            f"hold at {station.replace(' Station', '')} until {fmt(cp['end'])}")
            trip.decisions.append(d)
            stand = train.segments[-1]
            stand.t1 = max(t, cp["end"])
            stand.note = f"held: {what}"
            self.hold_platform(trip.direction, index, train, stand.t1)
            d.outcome = f"left {station.replace(' Station', '')} at {fmt(stand.t1)} ({fmt_dur(stand.t1 - t)} later)"
            self.resume_line(train, trip, index, stand.t1, after_gate=cp)
            return
        sec = self.sections[cp["section"]]
        direction = trip.direction
        holders = sec["holders"]
        opposite_inside = [v for v, h in holders.items() if h["direction"] != direction]
        opposite_waiting = [w for w in sec["waiting"] if w["trip"].direction != direction and w["t"] < t - 1e-9]
        ok = not opposite_inside and not opposite_waiting
        if (train, trip) not in [(w["train"], w["trip"]) for w in sec["waiting"]]:
            sec["waiting"].append({"t": t, "train": train, "trip": trip, "index": index, "gate": gate, "station": station})
        if not ok:
            if not getattr(train, "section_logged", None) == cp["section"]:
                train.section_logged = cp["section"]
                other_dir = "northbound" if direction == "south" else "southbound"
                if opposite_inside:
                    blocker = opposite_inside[0]
                    until = holders[blocker]["release"]
                    reason = (f"occupied by {blocker} ({other_dir}) until its rear clears the far crossover"
                              + (f" at {fmt(until)}" if until is not None else ""))
                else:
                    blocker = opposite_waiting[0]["train"].vehicle_id
                    reason = f"{blocker} ({other_dir}) asked first and goes before this train"
                options = self.section_options(cp, gate, station, reason)
                d = self.decide(t, "station", "line", train, trip, f"{what}: single-track section requested", options,
                                f"hold at {station.replace(' Station', '')} for the single-track section")
                trip.decisions.append(d)
                train.section_wait = d
            stand = train.segments[-1]
            stand.t1 = math.inf
            stand.note = "waiting for the single-track section"
            self.hold_platform(trip.direction, index, train, math.inf)
            return
        sec["waiting"] = [w for w in sec["waiting"] if w["train"] is not train]
        self.grant_section(t, train, trip, index, gate, station, what)

    def section_options(self, cp, gate, station, blocked_reason):
        d = cp["route"]
        if gate["role"] == "divert":
            return [
                {"option": "continue on the southbound track" if cp["closed"] == "south" else "continue on the northbound track",
                 "feasible": False, "reason": "closed", "estimate_s": None},
                {"option": f"divert: cross at {d['sites'][0]}, single track to {d['sites'][1]}", "feasible": False,
                 "reason": blocked_reason, "estimate_s": None},
                {"option": f"hold at {station.replace(' Station', '')} for the single-track section", "feasible": True,
                 "reason": "first come, first served; it goes as soon as the section is free in its direction",
                 "estimate_s": None},
                {"option": f"hold at {station.replace(' Station', '')} until the track reopens", "feasible": True,
                 "reason": f"reopens at {fmt(cp['end'])}; waiting for the section is expected to be sooner", "estimate_s": round(cp["end"], 1)},
            ]
        return [{"option": "continue on its own track", "feasible": False,
                 "reason": f"now single track shared with diverted trains: {blocked_reason}", "estimate_s": None},
                {"option": f"hold at {station.replace(' Station', '')} for the section", "feasible": True,
                 "reason": "first come, first served; no train enters while one waits in the other direction",
                 "estimate_s": None}]

    def grant_section(self, t, train, trip, index, gate, station, what):
        cp = gate["plan"]
        sec = self.sections[cp["section"]]
        stand = train.segments[-1]
        waited = max(0.0, t - stand.t0 - self.ion.DWELL_S) if stand.kind == "stand" else 0.0
        stand.t1 = max(t, stand.t0)
        if stand.note == "waiting for the single-track section":
            stand.note = f"waited {fmt_dur(waited)} for the single-track section"
        start = stand.t1
        d = cp["route"]
        lead = train.lead
        if gate["role"] == "divert":
            # The whole diversion, stop by stop, over the other track's platforms.
            s0, tt = 0.0, start
            release = None
            sec_end = next(r["s1"] for r in d["resources"] if r["id"] == cp["section"])
            for st in d["stops"]:
                prof = move_profile("network", d["id"], s0, st["at_m"])
                if release is None and s0 <= sec_end + self.ion.ION_LENGTH <= st["at_m"]:
                    release = tt + prof.time_at(sec_end + self.ion.ION_LENGTH - s0)
                train.add(Segment("move", tt, tt + prof.duration, lead, trip.train_id, "network", d["id"], s0, st["at_m"],
                                  note=f"diverted: single track to {d['sites'][1]}"))
                tt += prof.duration
                trip.station_times[st["name"]] = tt
                train.add(Segment("stand", tt, tt + self.ion.DWELL_S, lead, trip.train_id, "network", d["id"], st["at_m"],
                                  st["at_m"], station=st["name"], note=f"{st['track']}bound platform" if st["track"] != trip.direction else ""))
                tt += self.ion.DWELL_S
                s0 = st["at_m"]
            release = release if release is not None else tt
            next_index = cp["exit_index"]
            choice = f"divert: cross at {d['sites'][0]}, single track to {d['sites'][1]}"
            options = [
                {"option": "continue on the southbound track" if cp["closed"] == "south" else "continue on the northbound track",
                 "feasible": False, "reason": "closed", "estimate_s": None},
                {"option": choice, "feasible": True,
                 "reason": f"section free; {d['shared_m'] / 1000:.1f} km of shared track, {len(d['stops']) - 1} stops on the "
                           f"{d['single_track']}bound platforms", "estimate_s": round(tt, 1)},
                {"option": f"hold at {station.replace(' Station', '')} until the track reopens",
                 "feasible": True, "reason": f"would leave at {fmt(cp['end'])}", "estimate_s": round(cp["end"], 1)},
            ]
            # The agent diverts if that gets it to the exit platform before the track reopens.
            if tt - self.ion.DWELL_S > cp["end"] + self.line_seconds(trip.direction, index, next_index):
                # Holding is quicker: undo the plan and wait for the reopening instead.
                while train.segments and train.segments[-1].t0 >= start:
                    train.segments.pop()
                stand = train.segments[-1]
                stand.t1 = cp["end"]
                dd = self.decide(t, "vehicle", "line", train, trip, what, options,
                                 f"hold at {station.replace(' Station', '')} until the track reopens")
                trip.decisions.append(dd)
                dd.outcome = f"left at {fmt(cp['end'])}"
                self.resume_line(train, trip, index, cp["end"], after_gate=cp)
                return
        else:
            # Normal running on its own (now shared) track; the section is held
            # until the rear clears the far crossover (worked out leg by leg, as the
            # train actually runs: see run_leg).
            sec1 = cp["route"]["opposing"]["section_line_m"][1]
            train.pending_release = {"section": cp["section"], "line_m": sec1 + self.ion.ION_LENGTH}
            release = None
            next_index = None
            choice = "enter the single-track section"
            options = [{"option": "enter the single-track section", "feasible": True,
                        "reason": "no train in it or waiting in the other direction", "estimate_s": None}]
        sec["holders"][train.vehicle_id] = {"direction": trip.direction, "release": release, "from": start,
                                            "role": gate["role"]}
        entry = {"vehicle_id": train.vehicle_id, "trip": trip.train_id, "direction": trip.direction,
                 "from_s": start, "release_s": release, "role": gate["role"]}
        sec["log"].append(entry)
        train.section_log = entry
        if release is not None:
            self.push(release, 1, "section_release", section=cp["section"], vehicle=train.vehicle_id)
        prior = getattr(train, "section_wait", None)
        dd = self.decide(t, "station" if prior else "vehicle", "line", train, trip, f"{what}: single-track section", options, choice)
        trip.decisions.append(dd)
        dd.outcome = (f"entered at {fmt(start)}; section released at {fmt(release)} when its rear cleared"
                      if release is not None else f"entered at {fmt(start)}")
        train.section_decision = dd
        if prior is not None:
            prior.outcome = f"granted at {fmt(start)} after waiting {fmt_dur(waited)}"
            train.section_wait = None
        train.section_logged = None
        if gate["role"] == "divert":
            self.release_platform(start, trip.direction, index, train)
            self.hold_platform(trip.direction, next_index, train, tt)
            train.gate_done = None
            self.plan_line(train, trip, next_index, tt)
        else:
            self.resume_line(train, trip, index, start, after_gate=cp)

    def resume_line(self, train, trip, index, tt, after_gate):
        """Continue from stops[index] at tt past a gate that has been settled."""
        train.gate_done = (trip.train_id, index)
        self.plan_line(train, trip, index, tt)

    def on_section_release(self, t, section, vehicle):
        sec = self.sections[section]
        sec["holders"].pop(vehicle, None)
        self.section_drain(t, section)

    def section_drain(self, t, section):
        """After the track reopens: diverted trains still waiting run normally, and
        once no diverted train is in the section the shared track is no longer needed."""
        sec = self.sections[section]
        cp = next(c for c in self.closures if c.get("section") == section)
        if t < cp["end"] - 1e-9:
            return self.section_admit(t, section)
        for w in list(sec["waiting"]):
            if w["gate"]["role"] == "divert" or not any(h["role"] == "divert" for h in sec["holders"].values()):
                sec["waiting"].remove(w)
                d = getattr(w["train"], "section_wait", None)
                if d is not None:
                    d.outcome = (f"track reopened at {fmt(cp['end'])}: left at {fmt(t)} on its own track" if w["gate"]["role"] == "divert" else f"left at {fmt(t)}: the last diverted train had cleared the single track")
                    w["train"].section_wait = None
                w["train"].section_logged = None
                self.resume_line(w["train"], w["trip"], w["index"], t, after_gate=cp)

    def on_closure_end(self, t, section):
        self.section_drain(t, section)

    def section_admit(self, t, section):
        sec = self.sections[section]
        for w in sorted(list(sec["waiting"]), key=lambda w: (w["t"], w["train"].vehicle_id)):
            if w not in sec["waiting"]:
                continue
            direction = w["trip"].direction
            if any(h["direction"] != direction for h in sec["holders"].values()):
                continue
            earlier_opposite = [x for x in sec["waiting"] if x["trip"].direction != direction and x["t"] < w["t"]]
            if earlier_opposite:
                continue
            sec["waiting"].remove(w)
            self.grant_section(t, w["train"], w["trip"], w["index"], w["gate"], w["station"],
                               self.closure_text(w["gate"]["plan"]))

    def closure_text(self, cp):
        c = cp["closure"]
        return (f"{c['track'].capitalize()}bound track closed between {c['from_station'].replace(' Station', '')} and "
                f"{c['to_station'].replace(' Station', '')}, {fmt(cp['start'])}-{fmt(cp['end'])}")

    # --- arrivals -------------------------------------------------------------
    def on_approach(self, t, train, terminal, t0):
        """The train leaves the station before the terminal: plan its arrival leg.
        It asks for a route when it reaches the request point."""
        st = self.stations[terminal]
        layout = st.layout
        pref = layout["preference"][0]
        route = layout["routes"][f"arrive:{pref}"]
        s_req = max(0.0, route["home_signal_m"] - REQUEST_M)
        clear = move_profile(terminal, f"arrive:{pref}", 0.0, route["stop_m"])
        train.approach = {"t0": t0, "terminal": terminal, "t_req": t0 + clear.time_at(s_req)}
        st.approach.append(train)
        self.push(train.approach["t_req"], 5, "request", train=train, terminal=terminal)

    def wait_point(self, st, train):
        """Where an approaching train stops if it cannot have a route: at the home
        signal, or behind the trains already waiting there."""
        route = st.route(f"arrive:{st.layout['preference'][0]}")
        ahead = st.approach.index(train)
        return route["home_signal_m"] - ahead * (self.ion.ION_LENGTH + QUEUE_GAP_M)

    def on_request(self, t, train, terminal):
        st = self.stations[terminal]
        trip = train.trip
        options, choice = self.arrival_options(st, train, t)
        first = st.approach[0] is train
        if choice and first:
            d = self.decide(t, "vehicle", terminal, train, trip, f"Approaching {st.layout['name']}: route request",
                            options, f"platform {choice}")
            trip.decisions.append(d)
            self.grant_arrival(t, st, train, choice, from_rest=False, decision=d)
            return
        if not first:
            options = [dict(o, feasible=False, reason=f"{st.approach[0].vehicle_id} is ahead on the same track")
                       for o in options]
        d = self.decide(t, "vehicle", terminal, train, trip, f"Approaching {st.layout['name']}: route request", options,
                        "stop at the home signal and wait" if first else f"stop behind {st.approach[0].vehicle_id} and wait")
        trip.decisions.append(d)
        train.wait_decision = d
        # Plan the stop: same path as the clear move until the request point.
        s_wait = self.wait_point(st, train)
        pref = st.layout["preference"][0]
        prof = move_profile(terminal, f"arrive:{pref}", 0.0, s_wait)
        t0 = train.approach["t0"]
        train.add(Segment("move", t0, t0 + prof.duration, train.lead, trip.train_id, terminal, f"arrive:{pref}", 0.0, s_wait,
                          note="to the home signal" if first else "to a stop behind the waiting train"))
        train.waiting_at = s_wait
        train.add(Segment("stand", t0 + prof.duration, math.inf, train.lead, trip.train_id, terminal, f"arrive:{pref}",
                          s_wait, s_wait, note="waiting for a route"))
        st.pending = getattr(st, "pending", [])
        st.pending.append(("arrive", train))

    def arrival_options(self, st, train, t):
        options = []
        choice = None
        for platform in st.layout["preference"]:
            route_name = f"arrive:{platform}"
            route = st.route(route_name)
            blocked = st.blockers(route_name, t, train.vehicle_id)
            via = " + ".join(v["edge"].replace("_", " ") for v in route["via"])
            if blocked:
                options.append({"option": f"platform {platform} via {via}", "feasible": False,
                                "reason": self.why_blocked(st, blocked, t), "estimate_s": None})
            else:
                options.append({"option": f"platform {platform} via {via}", "feasible": True,
                                "reason": "route and platform free" if choice is None else "free, but not preferred",
                                "estimate_s": None})
                choice = choice or platform
        return options, choice

    def why_blocked(self, st, blocked, t):
        """Plain words for the first resource that blocks a move."""
        res, holder, move = blocked[0]
        other = next((tr for tr in self.trains if tr.vehicle_id == holder), None)
        name = res.replace("_", " ")
        if other is not None and other.terminal == st.key and res == st.layout["platforms"].get(other.platform, {}).get("edge"):
            extra = f", held until {fmt(other.hold_until)}" if other.hold_until > t else ""
            nxt = f", next trip {other.next_trip.train_id} at {fmt(other.next_trip.scheduled_s)}" if other.next_trip else ""
            return f"{name} occupied by {holder} (standing{extra}{nxt})"
        if move.startswith("depart"):
            return f"{name} reserved for {holder} departing from platform {move.split(':')[1]}"
        if move.startswith("arrive"):
            return f"{name} reserved for {holder} arriving at platform {move.split(':')[1]}"
        return f"{name} held by {holder}"

    def grant_arrival(self, t, st, train, platform, from_rest, decision=None):
        key = st.key
        route_name = f"arrive:{platform}"
        route = st.route(route_name)
        trip = train.trip
        if from_rest:
            s0 = train.waiting_at
            wait = train.segments[-1]
            start = max(t, wait.t0)
            wait.t1 = start
            wait.note = f"waited {fmt_dur(start - wait.t0)} for a route"
        else:
            s0 = 0.0
            start = train.approach["t0"]
        prof = move_profile(key, route_name, s0, route["stop_m"])
        stop_t = start + prof.duration

        def release(res):
            if res["s1"] + self.ion.ION_LENGTH > route["stop_m"] + 1e-6:
                return None  # still under the standing train
            return start + prof.time_at(res["s1"] + self.ion.ION_LENGTH - s0)

        def occ(res):
            t_in = start + prof.time_at(max(0.0, res["s0"] - s0))
            return (t_in, release(res))

        st.reserve(route_name, train.vehicle_id, t, release, occ)
        for res in route["resources"]:
            r = release(res)
            if r is not None:
                self.push(r, 1, "release", terminal=key)
        train.add(Segment("move", start, stop_t, train.lead, trip.train_id, key, route_name, s0, route["stop_m"],
                          note=f"into platform {platform}"))
        st.approach.remove(train)
        train.waiting_at = None
        train.terminal = key
        train.platform = platform
        train.arrived_s = stop_t
        train.ready_s = stop_t + TURN_S
        trip.arrived_s = stop_t
        trip.arrival_platform = platform
        trip.station_times[st.layout["station"]] = stop_t
        if decision is not None:
            decision.outcome = f"stopped at platform {platform} at {fmt(stop_t)}"
        if getattr(train, "wait_decision", None) is not None:
            train.wait_decision.outcome = (f"granted platform {platform} at {fmt(start)} after waiting "
                                           f"{fmt_dur(start - (train.segments[-2].t0 if from_rest else start))}; "
                                           f"stopped at {fmt(stop_t)}")
            train.wait_decision = None
        # Stand: cab change, then wait for the departure.
        train.add(Segment("stand", stop_t, math.inf, train.lead, trip.train_id, key, route_name, route["stop_m"], route["stop_m"],
                          station=st.layout["station"], note=f"platform {platform}: turnback",
                          lights={"off": stop_t + CAB_OFF_S, "on": stop_t + CAB_OFF_S + CAB_CHANGE_S}))
        # A pending platform hold for the next train at this platform.
        spec = self.hold_spec
        if spec and getattr(self, "hold_pending", False) and spec["terminal"] == key and spec["platform"] == platform:
            self.hold_pending = False
            self.apply_hold(stop_t, train, spec)
        # Dispatch assigns the next outbound trip (first in, first out).
        self.assign_next(stop_t, train)
        for later in st.approach:
            # The trains behind may move up to the signal when they get a route.
            pass

    def assign_next(self, t, train):
        if train.next_trip is not None:
            est, _ = self.not_before(train, train.next_trip)
            self.schedule_departure(t, train, est)
            return
        key = train.terminal
        direction = TERMINALS[key]["depart_line"]
        taken = {id(tr.next_trip) for tr in self.trains if tr.next_trip is not None}
        candidates = [trip for trip in self.plan.trips if trip.direction == direction and trip.vehicle_id is None
                      and id(trip) not in taken and not getattr(trip, "gone", False)]
        candidates.sort(key=lambda trip: (trip.scheduled_s, trip.train_id))
        if not candidates:
            return
        trip = candidates[0]
        self.assign(t, train, trip, "first train in, first trip out")

    def assign(self, t, train, trip, why):
        train.next_trip = trip
        self.events.append({"t": t, "kind": "assign", "vehicle_id": train.vehicle_id, "trip_id": trip.train_id, "why": why})
        if trip.scheduled_s <= t:
            self.review(t, train, trip, "assigned after its scheduled time")
        else:
            est, _ = self.not_before(train, trip)
            self.schedule_departure(t, train, est)

    def on_release(self, t, terminal):
        self.retry(t, terminal)

    def retry(self, t, terminal):
        """Re-examine waiting requests: departures first, then the first approaching train."""
        st = self.stations[terminal]
        pending = getattr(st, "pending", [])
        departures = sorted((tr for kind, tr in pending if kind == "depart"), key=lambda tr: (tr.next_trip.scheduled_s, tr.vehicle_id))
        for train in departures:
            st.pending.remove(("depart", train))
            self.on_try_depart(t, train)
        if st.approach:
            head = st.approach[0]
            if ("arrive", head) in getattr(st, "pending", []):
                options, choice = self.arrival_options(st, head, t)
                if choice:
                    st.pending.remove(("arrive", head))
                    d = self.decide(t, "station", terminal, head, head.trip, "Route cleared for a waiting train", options,
                                    f"platform {choice}")
                    head.trip.decisions.append(d)
                    self.grant_arrival(t, st, head, choice, from_rest=True, decision=d)
                    # The next waiting train becomes the head; it may also go now.
                    self.retry(t, terminal)

    # --- warm-up and scenario ------------------------------------------------
    def spawn(self, t, key, trip):
        st = self.stations[key]
        for platform in st.layout["preference"]:
            if not st.blockers(f"depart:{platform}", t, None) and not st.holders(st.layout["platforms"][platform]["edge"], t, None):
                break
        else:
            self.push(t + 30.0, 3, "due", trip=trip)
            return
        train = Train(f"LRV{501 + len(self.trains)}", lead="A")
        self.trains.append(train)
        route = st.route(f"depart:{platform}")
        st.reserve_platform(platform, train.vehicle_id, t)
        # Standing at the platform, facing out (the departing cab leads).
        train.lead = "B"
        train.add(Segment("stand", t, math.inf, "A", trip.train_id, key, f"depart:{platform}", route["start_m"],
                          route["start_m"], station=st.layout["station"], note="entered service from the yard (warm-up)"))
        train.terminal = key
        train.platform = platform
        train.ready_s = t
        train.arrived_s = t
        self.assign(t, train, trip, "entered service (warm-up)")

    def on_hold(self, t, spec):
        st = self.stations[spec["terminal"]]
        rid = st.layout["platforms"][spec["platform"]]["edge"]
        holders = [h for h, move in st.holders(rid, t, None) if move == "stand" or True]
        train = next((tr for tr in self.trains if tr.terminal == spec["terminal"] and tr.platform == spec["platform"]), None)
        if train is None:
            self.hold_pending = True
            return
        self.apply_hold(t, train, spec)

    def apply_hold(self, t, train, spec):
        until = t + spec["minutes"] * 60.0
        train.hold_until = until
        train.hold_reason = spec.get("reason", "platform hold")
        self.holds.append({"vehicle_id": train.vehicle_id, "terminal": spec["terminal"], "platform": spec["platform"],
                           "from_s": t, "until_s": until, "reason": train.hold_reason})
        trip = train.next_trip
        d = self.decide(t, "vehicle", spec["terminal"], train, trip,
                        f"Held at platform {spec['platform']}: {train.hold_reason} (scenario)",
                        [{"option": "stay at the platform", "feasible": True,
                          "reason": f"held until {fmt(until)}", "estimate_s": round(until, 1)}],
                        "stay at the platform and tell dispatch")
        if trip is not None:
            trip.decisions.append(d)
            self.review(t, train, trip, f"{train.vehicle_id} held at platform {spec['platform']}")
        d.outcome = f"released at {fmt(until)}"

    # --- results ---------------------------------------------------------------
    def segment_at(self, train, t):
        for seg in train.segments:
            if seg.t0 <= t < seg.t1 or (seg.t0 <= t and math.isinf(seg.t1)):
                return seg
        return None

    def where(self, train, t):
        """(path kind, key, nose s, speed) for a train at t, or None if not in service."""
        seg = self.segment_at(train, t)
        if seg is None:
            return None
        x = t - seg.t0
        if seg.kind == "move":
            prof = move_profile(seg.terminal, seg.route, seg.s0, seg.s1)
            return seg, seg.s0 + prof.distance(x), prof.speed(x)
        if seg.kind == "run":
            run = self.plan.lines[seg.line].legs[seg.leg].run()
            return seg, seg.s0 + run.distance(x), run.speed_at(x) if hasattr(run, "speed_at") else 0.0
        return seg, seg.s0, 0.0

    def line_nose(self, seg, s):
        """A path position as a distance along the line (approximate in the trackwork)."""
        if seg.line:
            return s
        if seg.terminal == "network":
            route = DIVERSIONS[seg.route]
            return _interp_line_s(route, s)
        route = TERMINALS[seg.terminal]["routes"][seg.route]
        line = route["line"]
        direction = line["direction"]
        length = self.ion.corridor.DATA["ion"][direction]["length_m"]
        if route["kind"] == "arrive":
            if s <= line["line_part_m"]:
                return line["from_m"] + s
            return min(length, line["handover_m"] + (s - line["line_part_m"]))
        if s >= line["line_start_m"]:
            return line["handover_m"] + (s - line["line_start_m"])
        return max(0.0, line["handover_m"] - (line["line_start_m"] - s))

    def lonlat(self, seg, s):
        """Lon/lat of the middle of the train whose nose is at s."""
        if seg.line:
            return self.ion.position(seg.line, s)
        path = route_def(seg.terminal, seg.route)["path"]
        lon, lat = self.ion.corridor._lerp_path(path, s - self.ion.ION_LENGTH / 2.0)
        return round(lon, 6), round(lat, 6)

    def poses(self, t):
        out = []
        for train in self.trains:
            found = self.where(train, t)
            if found is None:
                continue
            seg, s, speed = found
            trip = next((x for x in self.plan.trips if x.train_id == seg.trip_id), None)
            direction = trip.direction if trip else None
            station = seg.station if seg.kind == "stand" else None
            out.append({
                "vehicle_id": train.vehicle_id, "trip_id": seg.trip_id, "direction": direction,
                "kind": seg.kind, "terminal": seg.terminal, "route": seg.route, "s": s, "speed": speed,
                "nose_line": self.line_nose(seg, s), "station": station, "note": seg.note,
                "lonlat": self.lonlat(seg, s), "lead": seg.lead, "trip": trip,
            })
        return out

    def trip_pose(self, trip, t):
        """A trip's position from its departure to its stop at the far terminal."""
        train = next((tr for tr in self.trains if tr.vehicle_id == trip.vehicle_id), None)
        if train is None or trip.depart_s is None:
            return None
        segs = [x for x in train.segments if x.trip_id == trip.train_id]
        if not segs:
            return None
        start = next((x.t0 for x in segs if x.kind == "move" and x.route and x.route.startswith("depart")), None)
        end = trip.arrived_s
        if start is None or t < start or (end is not None and t > end):
            return None
        found = self.where(train, t)
        if found is None:
            return None
        seg, s, _ = found
        line = self.plan.lines[trip.direction]
        if seg.kind == "run" or (seg.kind == "stand" and seg.line):
            leg = seg.leg
        elif seg.route and seg.route.startswith("depart"):
            leg = 0
        else:
            leg = len(line.legs) - 1
        station = seg.station if seg.kind == "stand" else None
        if seg.kind == "move" and seg.route and seg.route.startswith("arrive") and end is not None and t >= end:
            station = TERMINALS[seg.terminal]["station"]
        return {"nose_line": self.line_nose(seg, s), "station": station, "leg": leg}

    def metrics(self, trips):
        """Service in the window: departure delays, queueing at terminals, platform use."""
        departed = [t for t in trips if t.station_times]
        delays = [max(0.0, t.depart_s - t.scheduled_s) for t in departed]
        waits = []
        for train in self.trains:
            for seg in train.segments:
                if seg.kind == "stand" and seg.note.startswith("waited") and 0 <= seg.t0 < self.plan.duration_s and seg.t1 - seg.t0 >= 1.0:
                    waits.append(seg.t1 - seg.t0)
        arrivals = [t for t in self.plan.trips if getattr(t, "arrived_s", None) is not None and 0 <= t.arrived_s < self.plan.duration_s]
        alternate = sum(1 for t in arrivals
                        if getattr(t, "arrival_platform", None) != TERMINALS["conestoga" if t.direction == "north" else "fairway"]["preference"][0])
        swaps = sum(1 for d in self.decisions if d.chosen.startswith("swap") and 0 <= d.t < self.plan.duration_s)
        return {
            "departures": len(departed),
            "late_departures": sum(1 for d in delays if d > 60.0),
            "missed_departures": sum(1 for d in delays if d > 180.0),
            "avg_departure_delay_s": round(sum(delays) / len(delays), 1) if delays else 0.0,
            "max_departure_delay_s": round(max(delays), 1) if delays else 0.0,
            "signal_waits": len(waits),
            "platform_queue_s": round(sum(waits), 1),
            "alternate_platform_arrivals": alternate,
            "trip_swaps": swaps,
        }

    def plans(self):
        out = []
        for train in self.trains:
            segs = []
            for s in train.segments:
                seg = s.to_dict()
                if math.isinf(s.t1):
                    seg["t1"] = None
                segs.append(seg)
            out.append({"id": train.vehicle_id, "segments": segs})
        return out


def run_time_at(run, d):
    """Seconds into a corridor.Run until it has covered d metres."""
    lo, hi = 0.0, run.duration
    for _ in range(60):
        mid = (lo + hi) / 2
        if run.distance(mid) < d:
            lo = mid
        else:
            hi = mid
    return hi


def _interp_line_s(route, s):
    """A diversion path position as a distance along its direction's GTFS line."""
    path, line_s = route["path"], route["line_s"]
    if s <= path[0][2]:
        return line_s[0]
    for i in range(len(path) - 1):
        if path[i][2] <= s <= path[i + 1][2]:
            span = path[i + 1][2] - path[i][2]
            f = 0.0 if span == 0 else (s - path[i][2]) / span
            return line_s[i] + (line_s[i + 1] - line_s[i]) * f
    return line_s[-1]


def fmt(t):
    sign = "-" if t < 0 else ""
    t = abs(t)
    return f"{sign}{int(t // 60):02d}:{int(t % 60):02d}"


def fmt_dur(s):
    return f"{int(s // 60)} min {int(round(s % 60))} s" if s >= 60 else f"{int(round(s))} s"


def plus(late):
    return "on time" if late < 1 else f"+{late / 60:.1f} min"
