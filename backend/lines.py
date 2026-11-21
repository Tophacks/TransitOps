"""Scheduled GRT bus lines, run end to end on the simulation's one-second clock.

Every trip of a modelled route comes from GTFS (lines.json, built by
tools/waterloo_geometry/build_lines.py): its block, route variant, stop
order and times. One physical bus works each block, with its own driver.
Route, trip, stop and vehicle IDs stay distinct: a trip belongs to a block;
a vehicle is assigned to the block.

What a driver does, second by second (the motion: line_driver.py):

- drives each stop-to-stop segment with the corridor buses' motion
  (pull-away, cruise fitted to the scheduled running time stretched by
  traffic, its own pace and seeded variation, slowing for curves);
- decides, once the stop is within braking distance, whether riders are
  waiting or want to get off (GRT stops on request) and passes the others;
- brakes progressively, creeps into position, stops fully, then opens the doors;
- where traffic is modelled (lines.json zones, road_traffic.Lane: the UW Station
  lane rules), follows the cars ahead and waits for a gap to pull out of a
  curbside stop or a terminal bay;
- asks for a berth; if the stop's berths are taken or blocked, it waits
  behind the bus that holds one, and says which;
- holds at a timepoint (a station or terminal) rather than leave early;
- lays over at the end of a trip and starts the next trip of its block,
  late if it arrived late.

Delays carry forward: a bus late at one stop reaches the next stops late,
riders transferring to ION may miss the train they planned on, and a late
arrival at the terminal makes the next trip of the block late.

Trips of a block on routes that are not modelled are not driven: the bus
is off the modelled network for that trip and comes back with its lateness,
less any slack in the scheduled layovers (modelled).

Modelled values (rates, dwell, run share, variation) are listed in
docs/regional-lines.md.
"""
from bisect import bisect_right
import json
import math
import random
from pathlib import Path

import line_driver as ld
from drivers import Driver, Errors, _unit
from road_traffic import Lane

LINES = json.loads((Path(__file__).resolve().parent / "lines.json").read_text())
HOUR0 = 7 * 3600 + 30 * 60            # run second 0 = 07:30, as for the garages and UW Station
STEP = 1.0

# Modelled values (docs/regional-lines.md).
RUN_SHARE = 0.68          # share of the scheduled stop-to-stop time spent moving; with CATCH_UP, calibrated so
                          # the median Route 7 trip reaches its terminal on schedule at the default traffic (docs)
CATCH_UP = 0.5            # share of its lateness a driver tries to recover on each segment (modelled)
TRAFFIC_REF = 1.25        # the API's default traffic multiplier: scheduled conditions
MAX_SPEED = 60 / 3.6      # never faster than 60 km/h between stops
VARIATION = 0.08          # seeded run-time variation per segment (log-normal sigma)
PACE_REF = 0.94           # the mean driver pace: a slower driver takes longer
DOORS_S, CLOSE_S = 3.0, 3.0
BOARD_S, ALIGHT_S = 2.5, 1.5
CAPACITY, SEATS = 70, 38
MIN_LAYOVER_S = 180.0     # recovery a driver takes at a terminal before the next trip
STREET_BERTHS, STATION_BERTHS = 1, 2
DEMAND_PER_H = {"street": 4.0, "station": 12.0}      # riders per hour per direction at a stop
DEST_WEIGHT = {"street": 1.0, "station": 3.0}
TO_ION_SHARE = 0.35       # riders getting off at an ION station who continue by train
ION_TO_BUS = 0.5          # mean riders per ION train arrival transferring to each line direction at a station
WALK_MPS = 1.2
ION_EXTRA_S = 30.0        # platform to street (modelled), added to the measured walk


def clock(t):
    s = int(round(HOUR0 + t))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class Rider:
    __slots__ = ("id", "stop", "route", "direction", "dest", "ready_s", "source", "boarded_s", "to_ion", "bus",
                 "planned_alight_s", "stranded", "passed_by")

    def __init__(self, rid, stop, route, direction, dest, ready_s, source="walk-up", to_ion=False):
        self.id, self.stop, self.route, self.direction, self.dest = rid, stop, route, direction, dest
        self.ready_s, self.source, self.to_ion = ready_s, source, to_ion
        self.boarded_s = None
        self.bus = None
        self.planned_alight_s = None
        self.stranded = False       # put off a broken-down bus; boards again without counting twice
        self.passed_by = None       # (vehicle, t) of a bus that passed this rider's stop by mistake


class LineBus:
    """One physical bus working one GTFS block, and its driver."""

    def __init__(self, vehicle_id, block, rows, seed):
        self.vehicle_id, self.block, self.rows = vehicle_id, block, rows
        self.driver = Driver(vehicle_id, seed)
        self.row = 0                 # index into the block's trips
        self.trip = None             # the modelled trip being run (dict from lines.json)
        self.pattern = None
        self.i = 0                   # index of the next (or current) stop on the pattern
        self.state = "off"
        self.s = 0.0                 # metres along the pattern
        self.until = 0.0             # end of the current timed state
        self.v = 0.0
        self.onboard = []
        self.delay_s = 0.0           # carried lateness
        self.segments = []           # [t0, t1, pattern, s0, s1, kind] (coarse: layovers, deadheads)
        self.trace = []              # [t, s, v] every second on a route variant (line_driver.py)
        self.trips_log = []          # [t0, t1, trip, pattern, headsign, route]
        self.cap = ld.TOP            # cruising speed for the current segment
        self.committed = False       # decided to stop at the next stop
        self.berth_ok = False        # a berth is held for it there
        self.skip_i = None           # a stop it will pass by mistake
        self.last_stop_s = None      # where it last stopped (for pulling out to the lane)
        self.lane = None             # (zone, RoadVehicle) while in a shared-traffic lane
        self.hold_passed = False     # past this trip's bay-exit stop line
        self.reacting = 0
        self.merge_episode = None
        self.stops_log = []          # per stop served: trip, stop, sched, arr, dep, on, off, passed
        self.berth_stop = None
        self.waited_for = None
        self.served = 0
        self.unmodelled = []         # trips of the block on routes that are not modelled
        self.broken = False

    def replay_trace(self):
        # Each step ending at the trip-change time still used the old pattern.
        # Compress within trips so even a stationary relief bus keeps its first
        # in-service sample, independent of when the worker sends a checkpoint.
        result = []
        first = 0
        for start, *_ in self.trips_log:
            stop = bisect_right(self.trace, start, lo=first, key=lambda row: row[0])
            result.extend(ld.simplify(self.trace[first:stop]))
            first = stop
        result.extend(ld.simplify(self.trace[first:]))
        return result

    def where(self):
        return {"pattern": self.pattern["id"] if self.pattern else None, "s": round(self.s, 1)}


class LineService:
    def __init__(self, routes=("7",), seed=17, duration_s=3600.0, traffic=TRAFFIC_REF, ion_plan=None,
                 demand=1.0, station_block=None, errors=None, vehicles=None, data=None, fleet=None, cars="moderate"):
        self.data = data or LINES
        self.fleet = fleet
        if fleet is not None and vehicles is None:
            vehicles = fleet.block_vehicle
        self.routes = [r for r in routes if r in self.data["routes"]]
        self.seed, self.duration_s, self.traffic = seed, duration_s, traffic
        self.demand = demand
        self.stops = self.data["stops"]
        self.patterns = {p["id"]: p for r in self.routes for p in self.data["routes"][r]["patterns"]}
        self.envelopes = {pid: ld.Envelope(p) for pid, p in self.patterns.items()}
        self._free = {}
        # Shared traffic where it is modelled (UW Station lane rules): one lane per zone.
        self.cars = cars
        self.zones = []
        for z in self.data.get("zones", []):
            if z["pattern"] in self.patterns:
                lane = Lane(z["lane"][-1][2], z["merge_rear_m"], cars, seed, speed=13.9, name=z["id"])
                self.zones.append(dict(z, lane_model=lane))
        self.trips = {t["trip_id"]: dict(t, route=r) for r in self.routes for t in self.data["routes"][r]["trips"]}
        self.block = station_block           # {"stop", "from_s", "to_s"}: berths unavailable (scenario)
        self.blocks = [station_block] if station_block else []
        self.errors = errors if isinstance(errors, Errors) else Errors(errors, seed)
        self.decisions = []
        self.seq = 0
        self.riders = []
        self.waiting = {}                    # stop -> [Rider]
        self.done_riders = []
        self.berths = {}                     # stop -> [vehicle ids holding a berth]
        self.queues = {}                     # stop -> [vehicle ids waiting, in order]
        self.ion = self._ion_times(ion_plan)
        self.vehicles = vehicles             # block -> vehicle id (fleet.py); else one per block
        self.buses = self._buses()
        self.metrics = {"trips_scheduled": 0, "trips_started": 0, "trips_completed": 0, "missed_stops": 0,
                        "boarded": 0, "alighted": 0, "ion_transfers_boarded": 0, "connections_made": 0,
                        "connections_missed": 0, "connection_extra_wait_s": 0.0}
        self.departures = []                 # (trip, stop, sched_s, actual_s) at each trip's first stop
        self.arrivals = []                   # (trip, stop, sched_s, actual_s) at each trip's last stop
        self._seed_demand()

    # --- inputs --------------------------------------------------------------------------
    def _ion_times(self, plan):
        """Station name -> sorted train times at that station (any direction)."""
        out = {}
        if plan is None:
            return out
        for trip in plan.trips:
            for name, t in trip.station_times.items():
                out.setdefault(name, []).append(t)
        for v in out.values():
            v.sort()
        return out

    def _buses(self):
        out = []
        blocks = self.data["blocks"]
        for b in sorted({t["block"] for t in self.trips.values()}):
            rows = blocks[b]
            last_end = rows[-1]["end"] - HOUR0
            first_start = rows[0]["start"] - HOUR0
            if last_end < 0 or first_start > self.duration_s:
                continue
            vid = (self.vehicles or {}).get(b, f"V-{b}")
            bus = LineBus(vid, b, rows, self.seed)
            if self.fleet is not None and vid in self.fleet.vehicles:
                bus.driver = self.fleet.driver(vid)      # the bus's operator today
            while bus.row < len(rows) and rows[bus.row]["end"] - HOUR0 <= 0:
                bus.row += 1
            out.append(bus)
        return out

    def kind(self, stop):
        s = self.stops[stop]
        return "station" if ("ion" in s or "Station" in s["name"]) else "street"

    def capacity(self, stop):
        return STATION_BERTHS if self.kind(stop) == "station" else STREET_BERTHS

    def _seed_demand(self):
        """Walk-up riders and ION transfers, drawn once from the seed so that two
        policies see the same riders."""
        n = 0
        for r in self.routes:
            for pat in self.data["routes"][r]["patterns"]:
                if pat["trips"] < 3:
                    continue            # short-turn variants share the main variants' stops
                stops = [x["stop"] for x in pat["stops"]]
                for k, sid in enumerate(stops[:-1]):
                    down = stops[k + 1:]
                    w = [DEST_WEIGHT[self.kind(x)] for x in down]
                    rng = random.Random(int(_unit(self.seed, "line-demand", r, pat["direction"], sid) * 2 ** 53))
                    rate = DEMAND_PER_H[self.kind(sid)] * self.demand / 3600.0
                    t = rng.expovariate(rate) if rate > 0 else math.inf
                    while t < self.duration_s:
                        dest = rng.choices(down, w)[0]
                        to_ion = "ion" in self.stops[dest] and rng.random() < TO_ION_SHARE
                        n += 1
                        self.riders.append(Rider(f"L{n}", sid, r, pat["direction"], dest, t, to_ion=to_ion))
                        t += rng.expovariate(rate)
                    # Riders from ION trains at this station.
                    ion = self.stops[sid].get("ion")
                    if ion:
                        walk = ion["walk_m"] / WALK_MPS + ION_EXTRA_S
                        for tt in self.ion.get(ion["station"], []):
                            if 0 <= tt + walk < self.duration_s:
                                for _ in range(_poisson(rng, ION_TO_BUS)):
                                    n += 1
                                    self.riders.append(Rider(f"L{n}", sid, r, pat["direction"], rng.choices(down, w)[0],
                                                             tt + walk, source="ion"))
        self.riders.sort(key=lambda x: x.ready_s)
        self._next_rider = 0

    # --- decisions -----------------------------------------------------------------------
    def lonlat(self, pattern, s):
        path = pattern["path"]
        lo, hi = 0, len(path) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if path[mid][2] <= s:
                lo = mid
            else:
                hi = mid
        a, b = path[lo], path[hi]
        f = 0.0 if b[2] == a[2] else max(0.0, min(1.0, (s - a[2]) / (b[2] - a[2])))
        return [round(a[0] + (b[0] - a[0]) * f, 6), round(a[1] + (b[1] - a[1]) * f, 6)]

    def decide(self, t, level, bus, trigger, options, chosen, outcome="", **extra):
        self.seq += 1
        d = {"id": f"L{self.seq:04d}", "t": round(t, 1), "minute": int(t // 60), "level": level,
             "terminal": f"route {bus.trip['route'] if bus.trip else bus.rows[bus.row]['route']}",
             "vehicle_id": bus.vehicle_id, "trip_id": bus.trip["trip_id"] if bus.trip else None,
             "block": bus.block, "trigger": trigger, "options": options, "chosen": chosen, "outcome": outcome,
             "location": self.lonlat(bus.pattern, bus.s) if bus.pattern else None}
        d.update(extra)
        self.decisions.append(d)
        return d

    # --- the clock -----------------------------------------------------------------------
    def advance_to(self, t_end):
        t = getattr(self, "_t", 0.0)
        while t < t_end - 1e-9:
            self.step(t)
            t += STEP
        self._t = t

    def step(self, t):
        while self._next_rider < len(self.riders) and self.riders[self._next_rider].ready_s <= t:
            r = self.riders[self._next_rider]
            self.waiting.setdefault(r.stop, []).append(r)
            self._next_rider += 1
        for bus in self.buses:
            self.drive(bus, t)
            self.record(bus, t)
        for z in self.zones:
            z["lane_model"].step(t)

    # --- one bus -------------------------------------------------------------------------
    def sched(self, bus, i):
        return bus.trip["sched"][i] - HOUR0

    def gtfs_dep(self, bus, i):
        return bus.trip["gtfs"][i][1] - HOUR0

    def drive(self, bus, t):
        if bus.broken:
            if bus.v > 0 and bus.pattern is not None:          # brakes to a stop where it failed
                for _ in range(int(1 / ld.DT)):
                    v_new = max(0.0, bus.v - ld.HARD_DECEL * ld.DT)
                    bus.s += (bus.v + v_new) / 2 * ld.DT
                    bus.v = v_new
            return
        if bus.state == "done":
            return
        if bus.state == "off":
            self.next_trip(bus, t)
            return
        if bus.state == "layover":
            if t >= bus.until:
                self.depart_terminal(bus, t)
            return
        if bus.state == "deadhead":
            if t >= bus.until:          # the spare is at its relief point
                if getattr(bus, "relieves", None):
                    bus.state = "standby"   # waits there for the bus it replaces
                    return
                bus.state = "queue"         # in service from here
                bus.arrived_at = t
                bus.v = 0.0
                self.open_trip(bus, t)
                self.try_berth(bus, t)
            return
        if bus.state == "standby":
            return
        if bus.state == "run":
            self.run_step(bus, t)
            return
        if bus.state == "stopped":
            if t >= bus.until:                       # stationary: now the doors
                self.open_doors(bus, bus.pattern["stops"][bus.i]["stop"], t)
            return
        if bus.state == "queued":
            self.berth_check(bus, t)
            return
        if bus.state == "merge_wait":
            self.merge_step(bus, t)
            return
        if bus.state == "queue":
            self.try_berth(bus, t)
            return
        if bus.state == "dwell":
            sid = bus.pattern["stops"][bus.i]["stop"]
            self.board(bus, sid, t)
            if t >= bus.until:
                self.leave_stop(bus, t)

    def next_trip(self, bus, t, at_terminal=False):
        """Start the block's next trip when it is due (modelled trips are driven;
        others are worked off the modelled network, carrying lateness)."""
        if bus.row >= len(bus.rows):
            bus.state = "done"
            return
        row = bus.rows[bus.row]
        start = row["start"] - HOUR0
        if not row["modelled"]:
            end = row["end"] - HOUR0
            if t >= start + bus.delay_s:
                if row["trip_id"] not in [u["trip_id"] for u in bus.unmodelled]:
                    bus.unmodelled.append({"trip_id": row["trip_id"], "route": row["route"], "start_s": start,
                                           "late_s": round(bus.delay_s, 0)})
                if t >= end + bus.delay_s:
                    bus.row += 1
                    nxt = bus.rows[bus.row]["start"] - HOUR0 if bus.row < len(bus.rows) else math.inf
                    bus.delay_s = max(0.0, end + bus.delay_s - (nxt - MIN_LAYOVER_S)) if nxt < math.inf else 0.0
            return
        trip = self.trips[row["trip_id"]]
        if not at_terminal and t < start - 600:
            return            # not yet: the bus is elsewhere in its block
        bus.trip = trip
        bus.pattern = self.patterns[trip["pattern"]]
        bus.i = 0
        bus.s = bus.pattern["stops"][0]["s"]
        if start < 0 and t <= 0:
            # Already under way when the run starts: placed where the schedule has it, on time.
            self.place_mid_trip(bus, t)
            return
        bus.state = "layover"
        bus.until = max(start, t)
        bus.v = 0.0
        bus.hold_passed = False
        bus.last_stop_s = None
        bus.segments.append([t, bus.until, bus.pattern["id"], bus.s, bus.s, "layover"])
        self.open_trip(bus, t)

    def place_mid_trip(self, bus, t):
        trip = bus.trip
        times = [x - HOUR0 for x in trip["sched"]]
        if t >= times[-1]:
            bus.row += 1
            bus.state = "off"
            return
        i = next(k for k in range(1, len(times)) if times[k] > t)
        stops = bus.pattern["stops"]
        f = (t - times[i - 1]) / max(1.0, times[i] - times[i - 1])
        bus.i = i
        bus.s = stops[i - 1]["s"] + f * (stops[i]["s"] - stops[i - 1]["s"])
        self.open_trip(bus, t)
        self.start_run(bus, t, remaining=max(1.0, times[i] - t))
        bus.v = bus.cap * 0.8                      # already under way

    def depart_terminal(self, bus, t):
        sched = self.gtfs_dep(bus, 0)
        late = t - sched
        if late >= 60:
            prev = bus.rows[bus.row - 1]["trip_id"] if bus.row > 0 else None
            self.decide(t, "dispatch", bus, f"Trip {bus.trip['trip_id']} due out of "
                        f"{self.stops[bus.pattern['stops'][0]['stop']]['name']} at {clock(sched)}",
                        [{"option": "depart on time", "feasible": False,
                          "reason": f"the bus arrived late from trip {prev}" if prev else "the bus is late", "estimate_s": None},
                         {"option": f"depart {late / 60:.0f} min late after a {MIN_LAYOVER_S / 60:.0f} min recovery",
                          "feasible": True, "reason": "the driver's minimum layover (modelled)", "estimate_s": round(t, 1)}],
                        f"depart {late / 60:.0f} min late", outcome=f"departed {clock(t)}",
                        effect=f"trip {bus.trip['trip_id']} starts {late / 60:.1f} min late")
        self.serve(bus, 0, t)

    def free_flow(self, pattern, i):
        """The fastest legal run from stop i-1 to stop i, stopping at both
        (corridor motion: pull-away, curves, braking, at most BUS_TOP_SPEED)."""
        key = (pattern["id"], i)
        if key not in self._free:
            import corridor
            st = pattern["stops"]
            s0, s1 = st[i - 1]["s"], st[i]["s"]
            env = self.envelopes[pattern["id"]]
            curves = tuple((x - s0, v) for x, v in env.curves if s0 < x < s1)
            v = corridor._speed_profile(max(1.0, s1 - s0), curves, corridor.BUS_TOP_SPEED, None,
                                        corridor.BUS_ACCEL, corridor.BUS_DECEL)
            self._free[key] = corridor._run_time(max(1.0, s1 - s0), v)
        return self._free[key]

    def run_time(self, bus, i, late_s=0.0):
        """Seconds allowed to run from stop i-1 to stop i: a share of the scheduled
        time (the rest is dwell), stretched by traffic, the driver's pace and seeded
        variation. A late driver takes CATCH_UP of the lateness out of the segment,
        never below the fastest legal run (corridor motion)."""
        stops = bus.pattern["stops"]
        d = stops[i]["s"] - stops[i - 1]["s"]
        sched = max(0.0, bus.trip["sched"][i] - bus.trip["sched"][i - 1])
        base = max(d / MAX_SPEED, sched * RUN_SHARE)
        noise = math.exp(VARIATION * _gauss(self.seed, "run", bus.trip["trip_id"], i))
        t = base * (self.traffic / TRAFFIC_REF) * (PACE_REF / bus.driver.pace) * noise
        if late_s > 0:
            t = max(self.free_flow(bus.pattern, i) * 0.85, t - CATCH_UP * late_s)
        return max(1.0, t)

    def open_trip(self, bus, t):
        if not bus.trips_log or bus.trips_log[-1][2] != bus.trip["trip_id"]:
            bus.trips_log.append([t, None, bus.trip["trip_id"], bus.pattern["id"], bus.trip["headsign"], bus.trip["route"]])

    def close_trip(self, bus, t):
        if bus.trips_log and bus.trips_log[-1][1] is None:
            bus.trips_log[-1][1] = t

    def start_run(self, bus, t, remaining=None):
        """Leave a stop for the next: the cruising speed is fitted, as for the
        corridor, to the scheduled running time (stretched by traffic and pace)."""
        stops = bus.pattern["stops"]
        dur = remaining if remaining is not None else self.run_time(bus, bus.i, self.lateness(bus, t))
        bus.cap = self.envelopes[bus.pattern["id"]].cruise(bus.s, stops[bus.i]["s"], dur)
        bus.committed, bus.berth_ok = False, False
        bus.state = "run"
        bus.segments.append([t, t + dur, bus.pattern["id"], round(bus.s, 1), stops[bus.i]["s"], "run"])

    # --- the drive (line_driver.py) ------------------------------------------------------
    def zone_at(self, bus, sid=None, kind=None):
        for z in self.zones:
            if z["pattern"] == bus.pattern["id"] and (sid is None or z["stop"] == sid) and (kind is None or z["kind"] == kind):
                return z
        return None

    def leader_gap(self, bus):
        """Metres to the rear of the nearest vehicle ahead: another line bus on the
        same street, or a car in a shared lane the bus is in."""
        best = None
        for o in self.buses:
            if o is bus or o.pattern is None or o.state in ("off", "done", "deadhead", "standby"):
                continue
            if o.broken:                    # pulled over out of the lane: the buses behind drive round it
                continue
            if o.pattern["direction"] != bus.pattern["direction"] or o.pattern["id"] != bus.pattern["id"]:
                continue
            if o.state in ("layover",) and o.s <= 1.0:
                continue
            gap = o.s - ld.BUS_L - bus.s
            if 0 <= o.s - bus.s < 250 and (best is None or gap < best[0]):
                best = (gap, o.v)
        if bus.lane is not None:
            z, rv = bus.lane
            for v in z["lane_model"].ordered():
                if v is rv or v.done:
                    continue
                if v.rear > rv.front - 0.01:
                    gap = v.rear - rv.front
                    if best is None or gap < best[0]:
                        best = (gap, v.speed)
        return best

    def sync_lane(self, bus):
        """Enter, follow in, and leave a zone's lane as the bus moves along its route."""
        if bus.lane is not None:
            z, rv = bus.lane
            pos = bus.s - z["offset_m"]
            rv.front, rv.speed = pos, bus.v
            pulled_in = z["kind"] == "curbside" and bus.committed and z["stop"] == bus.pattern["stops"][bus.i]["stop"] \
                and bus.s >= z["pull_in_s"]
            if pos - ld.BUS_L > z["lane_model"].length or pulled_in:
                z["lane_model"].leave(bus.vehicle_id)
                bus.lane = None
            return
        for z in self.zones:
            if z["pattern"] != bus.pattern["id"] or z["kind"] != "curbside":
                continue
            a, b = z["shared"]
            near_stop = bus.committed and z["stop"] == bus.pattern["stops"][bus.i]["stop"] and bus.s >= z["pull_in_s"]
            # Driving along the street into the zone (a bus leaving the zone's stop joins it by merging).
            if a <= bus.s < b and bus.state == "run" and not near_stop:
                rv = z["lane_model"].enter(bus.vehicle_id, ld.BUS_L, bus.v, 0, bus.driver, front=bus.s - z["offset_m"],
                                           entry=None, external=True)
                bus.lane = (z, rv)
                return

    def run_step(self, bus, t):
        stops = bus.pattern["stops"]
        env = self.envelopes[bus.pattern["id"]]
        last = len(stops) - 1
        for k in range(int(round(1 / ld.DT))):
            tt = t + k * ld.DT
            # Stops it is passing.
            while not bus.committed and bus.i < last and bus.s >= stops[bus.i]["s"] - 0.01:
                self.pass_stop(bus, tt)
            nxt = stops[bus.i]
            d = nxt["s"] - bus.s
            if not bus.committed and bus.skip_i != bus.i:
                decide_at = bus.v * bus.v / (2 * ld.DECEL) + ld.CREEP_M + 2.0 * bus.v + 5.0
                if d <= decide_at:
                    self.decide_stop(bus, tt)
            target = None
            if bus.committed:
                target = d if bus.berth_ok else max(0.0, d - (ld.BUS_L + ld.GAP) * (1 + self.queue_ahead(bus)))
            bay = self.zone_at(bus, kind="bay")
            if bay is not None and not bus.hold_passed and bus.s < bay["hold_s"]:
                h = bay["hold_s"] - bus.s
                target = h if target is None else min(target, h)
            lead = self.leader_gap(bus)
            s_new, v_new, arrived = ld.advance(bus.s, bus.v, bus.cap, env, target,
                                               lead[0] if lead else None, lead[1] if lead else 0.0, bus.driver.headway)
            bus.s, bus.v = s_new, v_new
            # Every half second while driving: acceleration is constant within each step,
            # so the map reproduces the motion exactly from these samples.
            bus.trace.append([round(tt + ld.DT, 2), round(bus.s, 2), round(bus.v, 2)])
            self.sync_lane(bus)
            if arrived:
                bus.v = 0.0
                if bay is not None and not bus.hold_passed and abs(bus.s - bay["hold_s"]) < 0.05:
                    bus.state, bus.zone_wait = "merge_wait", bay
                    bus.reacting = 0
                    return
                bus.arrived_at = tt + ld.DT
                if bus.berth_ok:
                    bus.state, bus.until = "stopped", tt + ld.DT + ld.DOORS_AFTER_STOP_S
                    bus.last_stop_s = bus.s
                else:
                    bus.state = "queued"
                return

    def lateness(self, bus, t):
        """Seconds behind the schedule at the stop it is leaving (or passing)."""
        i = max(0, bus.i - 1)
        return t - self.gtfs_dep(bus, i)

    def queue_ahead(self, bus):
        sid = bus.pattern["stops"][bus.i]["stop"]
        q = self.queues.get(sid, [])
        return q.index(bus.vehicle_id) if bus.vehicle_id in q else 0

    def pass_stop(self, bus, t):
        stops = bus.pattern["stops"]
        sid = stops[bus.i]["stop"]
        if bus.skip_i != bus.i:
            bus.stops_log.append({"trip": bus.trip["trip_id"], "stop": sid, "sched": self.gtfs_dep(bus, bus.i),
                                  "arr": round(t, 1), "dep": round(t, 1), "on": 0, "off": 0, "passed": True})
        bus.skip_i = None
        bus.i += 1
        bus.cap = max(bus.cap * 0.6, self.envelopes[bus.pattern["id"]].cruise(
            stops[bus.i - 1]["s"], stops[bus.i]["s"], self.run_time(bus, bus.i, self.lateness(bus, t))))

    def decide_stop(self, bus, t):
        """Within braking distance of the next stop: stop, or pass it."""
        stops = bus.pattern["stops"]
        sid = stops[bus.i]["stop"]
        last = bus.i == len(stops) - 1
        alight = [r for r in bus.onboard if r.dest == sid]
        waiting = [r for r in self.waiting.get(sid, []) if self.wants(r, bus)]
        need = last or alight or waiting
        if not need:
            return
        if not last and self.errors.happens("missed_stop", bus.vehicle_id, bus.trip["trip_id"], sid, t):
            self.missed_stop(bus, t, sid, alight, waiting)
            return
        bus.committed = True
        bus.berth_ok = self.reserve_berth(bus, t)

    def reserve_berth(self, bus, t):
        """Ask for a berth at the next stop; wait behind the buses ahead if none."""
        sid = bus.pattern["stops"][bus.i]["stop"]
        q = self.queues.setdefault(sid, [])
        if bus.vehicle_id not in q:
            q.append(bus.vehicle_id)
        held = self.berths.setdefault(sid, [])
        free = 0 if self.blocked(sid, t) else self.capacity(sid) - len(held)
        if free > 0 and q[0] == bus.vehicle_id:
            q.pop(0)
            held.append(bus.vehicle_id)
            if bus.waited_for is not None:
                d, t0 = bus.waited_for
                d["outcome"] = f"berth at {clock(t)} after {t - t0:.0f} s"
                bus.waited_for = None
            return True
        if bus.waited_for is None:
            ahead = held + q[:q.index(bus.vehicle_id)]
            why = ("the berths are blocked (scenario)" if self.blocked(sid, t)
                   else f"held by {', '.join(held)}" + (f"; {', '.join(q[:q.index(bus.vehicle_id)])} ahead in the queue"
                                                        if q.index(bus.vehicle_id) else ""))
            d = self.decide(t, "vehicle", bus, f"Berth at {self.stops[sid]['name']} ({sid})",
                            [{"option": "pull into a berth", "feasible": False, "reason": why, "estimate_s": None},
                             {"option": "serve riders from the traffic lane", "feasible": False,
                              "reason": "not a boarding position (simulation rule)", "estimate_s": None},
                             {"option": "wait for a berth", "feasible": True, "reason": "", "estimate_s": None}],
                            "wait for a berth", saw=f"{self.capacity(sid)} berth(s) (modelled): {why}",
                            blockers=ahead)
            bus.waited_for = (d, t)
        return False

    def berth_check(self, bus, t):
        """Stopped short of a taken berth: when it is free, creep forward into it."""
        if self.reserve_berth(bus, t):
            bus.berth_ok = True
            bus.state = "run"

    def merge_step(self, bus, t):
        """Ready to pull out of a curbside stop or a terminal bay into shared
        traffic: the UW Station gap rule (road_traffic.Lane.merge_blockers)."""
        z = bus.zone_wait
        lane = z["lane_model"]
        blockers = lane.merge_blockers(ld.BUS_L, t, rear=z["merge_rear_m"], exclude=bus.vehicle_id)
        where = (f"the curbside stop at {z['stop_name']}" if z["kind"] == "curbside"
                 else f"the bay exit at {z['stop_name']} onto {z['road']}")
        if blockers:
            bus.reacting = 0
            seen = [f"{v.id} {abs(z['merge_rear_m'] - v.front):.0f} m {'behind' if v.front <= z['merge_rear_m'] else 'ahead'} "
                    f"at {v.speed * 3.6:.0f} km/h" for v in blockers]
            if bus.merge_episode is None:
                d = self.decide(t, "vehicle", bus, f"Pulling out of {where}",
                                [{"option": "pull out now", "feasible": False,
                                  "reason": f"no gap for {ld.BUS_L} m plus 3 s behind: {'; '.join(seen)}", "estimate_s": None},
                                 {"option": "wait for a gap", "feasible": True, "reason": "", "estimate_s": None}],
                                "wait for a gap", saw=f"{lane.name} traffic: " + "; ".join(seen),
                                control="the UW Station lane rule: room for the whole bus ahead, and 2.4 m plus 3 s "
                                        "at the approaching car's speed behind")
                d["blockers"] = []
                bus.merge_episode = (d, t)
            d = bus.merge_episode[0]
            for v in blockers:
                if v.id not in d["blockers"]:
                    d["blockers"].append(v.id)
            return
        if bus.reacting < bus.driver.reaction:
            bus.reacting += 1
            return
        if bus.merge_episode is not None:
            d, t0 = bus.merge_episode
            d["outcome"] = f"pulled out at {clock(t)} after waiting {t - t0:.0f} s; blocked by {', '.join(d['blockers'])}"
            d["effect"] = f"{t - t0:.0f} s later away from {z['stop_name']}"
            bus.merge_episode = None
        if z["kind"] == "bay":
            rv = lane.enter(bus.vehicle_id, ld.BUS_L, 0.0, t, bus.driver, front=z["merge_rear_m"], external=True)
            bus.hold_passed = True
        else:
            rv = lane.enter(bus.vehicle_id, ld.BUS_L, 0.0, t, bus.driver, front=bus.s - z["offset_m"], entry=None,
                            external=True)
        bus.lane = (z, rv)
        bus.zone_wait = None
        bus.state = "run"

    def record(self, bus, t):
        """The bus's motion this second, for the map: [t, metres along its route
        variant, speed]. (The map moves it to the curb at the stops it served.)"""
        if bus.pattern is None or bus.state in ("off", "done", "deadhead"):
            return
        if bus.trace and bus.trace[-1][0] >= t + 1.0 - 1e-6:
            return                                         # driven this second: sampled already
        bus.trace.append([round(t + 1.0, 2), round(bus.s, 2), round(bus.v, 2)])

    def wants(self, r, bus):
        if r.route != bus.trip["route"] or r.direction != bus.pattern["direction"]:
            return False
        later = [x["stop"] for x in bus.pattern["stops"][bus.i + 1:]]
        return r.dest in later

    def missed_stop(self, bus, t, sid, alight, waiting):
        """A driver error: missed the lane change and passed the stop."""
        self.errors.record(t, bus.vehicle_id, "missed_stop", f"passed {sid} on trip {bus.trip['trip_id']}")
        for r in waiting:
            r.passed_by = (bus.vehicle_id, t)
        self.metrics["missed_stops"] += 1
        nxt = bus.pattern["stops"][bus.i + 1]["stop"] if bus.i + 1 < len(bus.pattern["stops"]) else None
        for r in alight:
            r.dest = nxt              # they get off at the next stop instead
        self.decide(t, "vehicle", bus, f"Approaching {self.stops[sid]['name']} ({sid}) in the through lane",
                    [{"option": "change into the curb lane and stop", "feasible": False,
                      "reason": "missed the lane change (driver error)", "estimate_s": None},
                     {"option": "stop in the through lane", "feasible": False,
                      "reason": "not a boarding position (simulation rule)", "estimate_s": None},
                     {"option": "continue to the next stop", "feasible": True, "reason": "legal forward recovery",
                      "estimate_s": None}],
                    "continue to the next stop", outcome="stop missed",
                    error="driver error: missed the lane change and passed the stop",
                    saw=f"{len(waiting)} waiting, {len(alight)} to get off",
                    control="no reverse on a street (simulation rule); the stop is recorded as missed",
                    effect=f"{len(waiting)} riders wait for the next bus; {len(alight)} get off at "
                           f"{self.stops[nxt]['name'] if nxt else 'the terminal'}")
        bus.stops_log.append({"trip": bus.trip["trip_id"], "stop": sid, "sched": self.gtfs_dep(bus, bus.i),
                              "arr": t, "dep": t, "on": 0, "off": 0, "passed": True, "missed": True})
        bus.skip_i = bus.i                               # it drives past, and on from where it is

    def blocked(self, sid, t):
        return any(b["stop"] == sid and b["from_s"] <= t < b["to_s"] for b in self.blocks)

    def add_block(self, block):
        """A station delay during the run (an incident): the stop's berths are unavailable."""
        self.blocks.append(block)

    def try_berth(self, bus, t):
        sid = bus.pattern["stops"][bus.i]["stop"]
        q = self.queues.setdefault(sid, [])
        if bus.vehicle_id not in q:
            q.append(bus.vehicle_id)
        held = self.berths.setdefault(sid, [])
        free = 0 if self.blocked(sid, t) else self.capacity(sid) - len(held)
        if free > 0 and q[0] == bus.vehicle_id:
            q.pop(0)
            held.append(bus.vehicle_id)
            if bus.waited_for is not None:
                d, t0 = bus.waited_for
                d["outcome"] = f"berth at {clock(t)} after {t - t0:.0f} s"
                bus.waited_for = None
            self.open_doors(bus, sid, t)
            return
        if bus.waited_for is None:
            ahead = held + q[:q.index(bus.vehicle_id)]
            why = ("the berths are blocked (scenario)" if self.blocked(sid, t)
                   else f"held by {', '.join(held)}" + (f"; {', '.join(q[:q.index(bus.vehicle_id)])} ahead in the queue"
                                                        if q.index(bus.vehicle_id) else ""))
            d = self.decide(t, "vehicle", bus, f"Berth at {self.stops[sid]['name']} ({sid})",
                            [{"option": "pull into a berth", "feasible": False, "reason": why, "estimate_s": None},
                             {"option": "serve riders from the traffic lane", "feasible": False,
                              "reason": "not a boarding position (simulation rule)", "estimate_s": None},
                             {"option": "wait for a berth", "feasible": True, "reason": "", "estimate_s": None}],
                            "wait for a berth", saw=f"{self.capacity(sid)} berth(s) (modelled): {why}",
                            blockers=ahead)
            bus.waited_for = (d, t)
        if bus.segments and bus.segments[-1][5] == "wait" and bus.segments[-1][1] == t:
            bus.segments[-1][1] = t + STEP
        else:
            bus.segments.append([t, t + STEP, bus.pattern["id"], round(bus.s, 1), round(bus.s, 1), "wait"])

    def open_doors(self, bus, sid, t):
        bus.state = "dwell"
        bus.berth_stop = sid
        alight = [r for r in bus.onboard if r.dest == sid]
        for r in alight:
            bus.onboard.remove(r)
            self.alight(bus, r, sid, t)
        bus.dwell_from = t
        bus.until = t + DOORS_S + ALIGHT_S * len(alight) + CLOSE_S
        bus.boarded_here = 0
        bus.alighted_here = len(alight)
        self.board(bus, sid, t)

    def alight(self, bus, r, sid, t):
        self.metrics["alighted"] += 1
        self.done_riders.append(r)
        if r.to_ion:
            ion = self.stops[sid].get("ion")
            if ion:
                walk = ion["walk_m"] / WALK_MPS + ION_EXTRA_S
                trains = self.ion.get(ion["station"], [])
                plan = next((x for x in trains if x >= (r.planned_alight_s or t) + walk), None)
                got = next((x for x in trains if x >= t + walk), None)
                if plan is not None and got is not None:
                    if got <= plan + 1e-6:
                        self.metrics["connections_made"] += 1
                    else:
                        self.metrics["connections_missed"] += 1
                        self.metrics["connection_extra_wait_s"] += got - plan

    def board(self, bus, sid, t):
        if bus.state != "dwell":
            return
        q = self.waiting.get(sid, [])
        for r in [x for x in q if self.wants(x, bus)]:
            if len(bus.onboard) >= CAPACITY:
                break
            q.remove(r)
            r.boarded_s, r.bus = t, bus.vehicle_id
            # Its scheduled arrival at its stop, for connection planning.
            k = next(j for j, x in enumerate(bus.pattern["stops"]) if j > bus.i and x["stop"] == r.dest)
            r.planned_alight_s = bus.trip["gtfs"][k][0] - HOUR0
            bus.onboard.append(r)
            bus.boarded_here += 1
            if r.passed_by is not None:
                bus.passed_here = getattr(bus, "passed_here", []) + [r.passed_by]
                r.passed_by = None
            if r.stranded:
                r.stranded = False
                self.metrics["reboarded"] = self.metrics.get("reboarded", 0) + 1
            else:
                self.metrics["boarded"] += 1
                if r.source == "ion":
                    self.metrics["ion_transfers_boarded"] += 1
            bus.until = max(bus.until, t + CLOSE_S) + BOARD_S

    def timepoint(self, bus, i):
        sid = bus.pattern["stops"][i]["stop"]
        return self.kind(sid) == "station"

    def leave_stop(self, bus, t):
        i = bus.i
        sid = bus.pattern["stops"][i]["stop"]
        last = i == len(bus.pattern["stops"]) - 1
        rel = getattr(bus, "release", None)
        if rel is not None and rel["k"] == i:
            self.release_here(bus, t, sid)
            return
        ho = getattr(bus, "handover", None)
        if ho is not None and ho["k"] == i and not last:
            spare = next(b for b in self.buses if b.vehicle_id == ho["spare"])
            if spare.state != "standby":
                if not ho.get("waiting"):
                    ho["waiting"] = True
                    ho["since"] = t
                return            # hold at the stop until the relief bus is here
            self.hand_over(bus, spare, t, sid)
            return
        if not last and self.timepoint(bus, i) and t < self.gtfs_dep(bus, i):
            return            # hold: never leave a timepoint early
        held = self.berths.get(sid, [])
        if bus.vehicle_id in held:
            held.remove(bus.vehicle_id)
        bus.segments.append([bus.dwell_from, t, bus.pattern["id"], bus.s, bus.s, "dwell"])
        bus.stops_log.append({"trip": bus.trip["trip_id"], "stop": sid, "sched": self.gtfs_dep(bus, i),
                              "arr": bus.arrived_at, "doors": bus.dwell_from, "dep": t, "on": bus.boarded_here,
                              "off": bus.alighted_here, "passed": False})
        bus.served += 1
        passed = getattr(bus, "passed_here", [])
        if passed:
            bus.passed_here = []
            by = sorted({v for v, _ in passed})
            when = min(t0 for _, t0 in passed)
            self.decide(t, "vehicle", bus, f"Boarding at {self.stops[sid]['name']} ({sid}): riders left behind earlier",
                        [{"option": "board everyone waiting", "feasible": True,
                          "reason": f"{len(passed)} of them were passed by {', '.join(by)} at {clock(when)} (missed lane change)",
                          "estimate_s": None}],
                        "board everyone waiting",
                        outcome=f"{bus.boarded_here} boarded, {len(passed)} of them left by {', '.join(by)}",
                        saw=f"{bus.boarded_here} riders boarding, {len(passed)} waiting since {', '.join(by)} passed the stop",
                        effect=f"{len(passed) * BOARD_S:.0f} s more dwell here; those riders waited "
                               f"{(t - when) / 60:.0f} min longer")
        if i == 0:
            self.departures.append((bus.trip["trip_id"], sid, self.gtfs_dep(bus, 0), t))
        if last:
            self.end_trip(bus, t)
        else:
            self.next_stop(bus, t)

    def next_stop(self, bus, t):
        sid = bus.pattern["stops"][bus.i]["stop"]
        bus.i += 1
        self.start_run(bus, t)
        bus.v = 0.0
        z = self.zone_at(bus, sid=sid, kind="curbside")
        if z is not None:                               # pull out of a curbside stop into traffic
            bus.state, bus.zone_wait, bus.reacting = "merge_wait", z, 0

    def serve(self, bus, i, t):
        """At the first stop of a trip: open the doors, board, then go."""
        sid = bus.pattern["stops"][i]["stop"]
        bus.arrived_at = t
        bus.state = "queue"
        self.try_berth(bus, t)

    def end_trip(self, bus, t):
        trip = bus.trip
        sched = trip["gtfs"][-1][0] - HOUR0
        self.arrivals.append((trip["trip_id"], bus.pattern["stops"][-1]["stop"], sched, t))
        self.metrics["trips_completed"] += 1
        self.close_trip(bus, t)
        if bus.lane is not None:
            bus.lane[0]["lane_model"].leave(bus.vehicle_id)
            bus.lane = None
        for r in list(bus.onboard):         # everyone gets off at the terminal
            bus.onboard.remove(r)
            self.alight(bus, r, bus.pattern["stops"][-1]["stop"], t)
        bus.row += 1
        bus.trip = None
        bus.state = "off"
        nxt = bus.rows[bus.row] if bus.row < len(bus.rows) else None
        if nxt is not None:
            ready = t + MIN_LAYOVER_S
            bus.delay_s = max(0.0, ready - (nxt["start"] - HOUR0))
            if nxt["modelled"]:
                self.next_trip(bus, t, at_terminal=nxt["from"] == bus.pattern["stops"][-1]["stop"])
                if bus.state == "layover":
                    bus.until = max(bus.until, ready)
                    bus.segments[-1][1] = bus.until
        else:
            bus.state = "done"

    # --- dispatcher orders ---------------------------------------------------------------
    def active_bus(self, block=None, vehicle=None):
        for b in self.buses:
            if b.broken or b.trip is None or b.state not in ("run", "dwell", "queue", "layover", "stopped", "queued",
                                                             "merge_wait"):
                continue
            if (block is None or b.block == block) and (vehicle is None or b.vehicle_id == vehicle) \
                    and not getattr(b, "release", None) and not getattr(b, "handover", None):
                return b
        return None

    def later_trips(self, bus):
        """The block's trips after the current one that start inside the run."""
        return [r["trip_id"] for r in bus.rows[bus.row + 1:] if r["modelled"] and r["start"] - HOUR0 < self.duration_s]

    def replace(self, t, bus, k, plan):
        """A spare takes over `bus`'s block at stop k; `bus` then leaves service."""
        stops = bus.pattern["stops"]
        nb = LineBus(plan["vehicle"], bus.block, bus.rows, self.seed)
        if self.fleet is not None:
            nb.driver = self.fleet.driver(plan["vehicle"])
        nb.row, nb.trip, nb.pattern = bus.row, bus.trip, bus.pattern
        nb.i, nb.s = k, stops[k]["s"]
        nb.state, nb.until = "deadhead", plan["arrive_s"]
        nb.relieves = bus.vehicle_id
        nb.segments.append([plan["leave_s"], plan["arrive_s"], bus.pattern["id"], nb.s, nb.s, "deadhead"])
        nb.deadhead = plan["deadhead"]
        self.buses.append(nb)
        bus.handover = {"k": k, "spare": nb.vehicle_id}
        return nb

    def hand_over(self, bus, spare, t, sid):
        """Riders change buses at the relief point; the relieved bus leaves service."""
        moved = len(bus.onboard)
        spare.onboard, bus.onboard = bus.onboard, []
        held = self.berths.get(sid, [])
        if bus.vehicle_id in held:
            held.remove(bus.vehicle_id)
        bus.segments.append([bus.dwell_from, t, bus.pattern["id"], bus.s, bus.s, "dwell"])
        bus.stops_log.append({"trip": bus.trip["trip_id"], "stop": sid, "sched": self.gtfs_dep(bus, bus.i),
                              "arr": bus.arrived_at, "dep": t, "on": bus.boarded_here, "off": bus.alighted_here,
                              "passed": False, "handover": spare.vehicle_id})
        self.close_trip(bus, t)
        bus.state, bus.trip = "done", None
        if self.fleet is not None:
            self.fleet.vehicles[bus.vehicle_id].set(t, "returning", f"relieved by {spare.vehicle_id} at {sid}")
        spare.relieves = None
        self.open_trip(spare, t)
        spare.state = "dwell"
        spare.arrived_at = spare.dwell_from = t
        spare.berth_stop = sid
        held.append(spare.vehicle_id)
        spare.boarded_here, spare.alighted_here = 0, 0
        spare.until = t + 30.0                    # riders step across (modelled)
        wait = t - bus.handover.get("since", t)
        self.decide(t, "vehicle", spare, f"Relief at {self.stops[sid]['name']}: {spare.vehicle_id} takes over from "
                    f"{bus.vehicle_id}", [{"option": "take over the block here", "feasible": True, "reason": "", "estimate_s": None}],
                    "take over the block here", outcome=f"{moved} riders changed buses; {bus.vehicle_id} returns to its garage",
                    effect=f"{bus.vehicle_id} held {wait:.0f} s for the relief" if wait else "no hold")

    def release(self, t, bus, k, on_release):
        bus.release = {"k": k, "cb": on_release}

    def release_here(self, bus, t, sid):
        """A reassigned bus leaves its line at this stop: its riders wait for the next bus."""
        put_off = 0
        for r in list(bus.onboard):
            bus.onboard.remove(r)
            r.stop, r.ready_s, r.bus, r.boarded_s, r.stranded = sid, t, None, None, True
            self.riders.append(r)
            put_off += 1
        self.riders.sort(key=lambda x: x.ready_s)
        held = self.berths.get(sid, [])
        if bus.vehicle_id in held:
            held.remove(bus.vehicle_id)
        bus.segments.append([bus.dwell_from, t, bus.pattern["id"], bus.s, bus.s, "dwell"])
        bus.stops_log.append({"trip": bus.trip["trip_id"], "stop": sid, "sched": self.gtfs_dep(bus, bus.i),
                              "arr": bus.arrived_at, "dep": t, "on": bus.boarded_here, "off": bus.alighted_here,
                              "passed": False, "released": True})
        self.metrics["stranded"] = self.metrics.get("stranded", 0) + put_off
        cb = bus.release["cb"]
        self.close_trip(bus, t)
        bus.state, bus.trip, bus.release = "done", None, None
        cb(bus.vehicle_id, sid, t, put_off)

    # --- breakdowns --------------------------------------------------------------------
    def relief_index(self, bus):
        """The next relief point ahead on its trip (a station a spare can reach), else the terminal."""
        from fleet import DEADHEADS
        stops = bus.pattern["stops"]
        # Laying over at the terminal: the relief happens there, before the trip.
        start = bus.i + 1 if bus.state == "dwell" else bus.i
        for k in range(start, len(stops)):
            if stops[k]["stop"] in DEADHEADS["points"]:
                return k
        return len(stops) - 1

    def breakdown(self, t, vehicle=None):
        """A bus on a line breaks down and is withdrawn; dispatch sends a spare to
        take over its block at the next relief point."""
        cand = [b for b in self.buses if b.trip is not None and not b.broken
                and b.state in ("run", "dwell", "queue", "stopped", "queued", "merge_wait")]
        if vehicle:
            cand = [b for b in cand if b.vehicle_id == vehicle]
        if not cand:
            return None
        bus = cand[0]
        bus.broken = True
        stops = bus.pattern["stops"]
        nxt = min(bus.i + 1, len(stops) - 1) if bus.state == "dwell" else bus.i
        sid = stops[nxt]["stop"]
        walk_m = max(0.0, stops[nxt]["s"] - bus.s)
        walk_s = walk_m / WALK_MPS
        stranded = 0
        for r in list(bus.onboard):
            bus.onboard.remove(r)
            if r.dest == sid:
                self.done_riders.append(r)
                continue
            r.stop, r.ready_s, r.bus = sid, t + walk_s, None
            r.boarded_s = None
            r.stranded = True
            stranded += 1
            self.riders.append(r)
        self.riders.sort(key=lambda x: x.ready_s)
        for q in self.berths.values():
            if bus.vehicle_id in q:
                q.remove(bus.vehicle_id)
        for q in self.queues.values():
            if bus.vehicle_id in q:
                q.remove(bus.vehicle_id)
        bus.segments.append([t, self.duration_s, bus.pattern["id"], round(bus.s, 1), round(bus.s, 1), "broken"])
        if bus.lane is not None:                          # it pulls over out of the traffic lane
            bus.lane[0]["lane_model"].leave(bus.vehicle_id)
            bus.lane = None
        self.metrics["breakdowns"] = self.metrics.get("breakdowns", 0) + 1
        self.metrics["stranded"] = self.metrics.get("stranded", 0) + stranded
        k = self.relief_index(bus)
        relief = stops[k]["stop"]
        self.decide(t, "vehicle", bus, f"{bus.vehicle_id} breaks down on trip {bus.trip['trip_id']}",
                    [{"option": "continue in service", "feasible": False, "reason": "the bus cannot be driven",
                      "estimate_s": None},
                     {"option": "stop, secure the bus and call Control", "feasible": True, "reason": "", "estimate_s": None}],
                    "stop, secure the bus and call Control", outcome=f"withdrawn at {clock(t)}",
                    error="vehicle breakdown (scenario)",
                    saw=f"{len(stops) - nxt} stops left on the trip; {stranded} riders onboard",
                    effect=f"{stranded} riders walk {walk_m:.0f} m to {self.stops[sid]['name']} and wait for another bus")
        if self.fleet is None:
            return None
        if self.fleet is not None:
            self.fleet.breakdown(t, bus.vehicle_id, f"route {bus.trip['route']} trip {bus.trip['trip_id']}")
        label = f"block {bus.block} (route {bus.trip['route']} trip {bus.trip['trip_id']}) from {self.stops[relief]['name']}"
        plan = self.fleet.request_spare(t, relief, f"{bus.vehicle_id} broke down on route {bus.trip['route']}", label,
                                        decide=self.decisions.append)
        if plan is None:
            return None
        nb = LineBus(plan["vehicle"], bus.block, bus.rows, self.seed)
        nb.driver = self.fleet.driver(plan["vehicle"])
        nb.row, nb.trip, nb.pattern = bus.row, bus.trip, bus.pattern
        nb.i, nb.s = k, stops[k]["s"]
        nb.state, nb.until = "deadhead", plan["arrive_s"]
        nb.segments.append([plan["leave_s"], plan["arrive_s"], bus.pattern["id"], nb.s, nb.s, "deadhead"])
        nb.deadhead = plan["deadhead"]
        nb.replaces = bus.vehicle_id
        self.buses.append(nb)
        return plan

    # --- results -------------------------------------------------------------------------
    def finish(self, end_s=None):
        """Metrics up to `end_s` (the end of the run, or a continuous run's checkpoint)."""
        end = self.duration_s if end_s is None else end_s
        left = [r for q in self.waiting.values() for r in q]
        self.metrics["riders_waiting"] = len(left)
        self.metrics["riders_waiting_ion"] = sum(1 for r in left if r.source == "ion")
        dep = [a - s for _, _, s, a in self.departures]
        self.metrics["departure_delay_avg_s"] = round(sum(dep) / len(dep), 1) if dep else 0.0
        self.metrics["departure_delay_max_s"] = round(max(dep), 1) if dep else 0.0
        arr = [a - s for _, _, s, a in self.arrivals]
        self.metrics["arrival_delay_avg_s"] = round(sum(arr) / len(arr), 1) if arr else 0.0
        window = [t for t in self.trips.values() if 0 <= t["gtfs"][0][1] - HOUR0 < end]
        started = {tid for tid, _, _, _ in self.departures}
        self.metrics["trips_scheduled"] = len(window)
        self.metrics["trips_covered"] = sum(1 for t in window if t["trip_id"] in started)
        self.metrics["trips_started"] = len(started)
        self.metrics["end_s"] = end
        return self.metrics

    def plans(self):
        return [{"vehicle_id": b.vehicle_id, "block": b.block, "driver": b.driver.describe(),
                 "operator": b.driver.vehicle_id,
                 # the motion the simulator drove: [t, s, v], simplified (line_driver.simplify)
                 "trace": b.replay_trace(), "trips": b.trips_log,
                 "stops": b.stops_log, "unmodelled": b.unmodelled,
                 "deadhead": getattr(b, "deadhead", None), "replaces": getattr(b, "replaces", None) or
                 getattr(b, "relieved", None), "broken": b.broken}
                for b in self.buses]

    def zone_cars(self):
        """Each shared-traffic zone's lane and its cars: [t, metres along the lane]."""
        from uw_station import simplify as simplify_trace
        return [{"id": z["id"], "kind": z["kind"], "stop": z["stop"], "stop_name": z["stop_name"], "road": z["road"],
                 "lane": z["lane"], "merge_rear_m": z["merge_rear_m"], "demand": self.cars, "pattern": z["pattern"],
                 "hold_s": z.get("hold_s"), "pull_in_s": z.get("pull_in_s"), "offset_m": z["offset_m"],
                 "cars": [{"id": v.id, "trace": simplify_trace(v.trace, dims=(1,))}
                          for v in z["lane_model"].vehicles if v.kind == "car" and v.trace]}
                for z in self.zones]

    def network(self):
        return {"routes": {r: {"name": self.data["routes"][r]["name"],
                               "patterns": [{"id": p["id"], "direction": p["direction"], "headsign": p["headsign"],
                                             "trips": p["trips"]} for p in self.data["routes"][r]["patterns"]]}
                           for r in self.routes}}


def _poisson(rng, mean):
    k, p, L = 0, 1.0, math.exp(-mean)
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def _gauss(*parts):
    """A reproducible standard normal draw from the parts."""
    u1 = max(1e-12, _unit(*parts, "a"))
    u2 = _unit(*parts, "b")
    return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
