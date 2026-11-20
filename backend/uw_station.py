"""University of Waterloo Station: buses as agents on the real bus roadway.

Transit Plaza Road (uw_station.json, from OSM and GRT GTFS) runs south-west to
north-east from the Ring Road all-way stop to Phillip Street. Each side has a
through lane and a curb berth lane along its platforms. The simulation runs
in one-second steps.

Dispatch assigns each corridor bus its trip at UW: set down at platform 4
(stop 1223), load at platform 6 (stop 1078), then leave by Phillip Street. The
bus agent decides how to execute it from what it can observe:

- **Follow:** it slows for, and follows, the vehicle ahead in its lane.
- **Level crossing:** it stops at the ION crossing while the lights flash.
- **All-way stop:** it makes a full stop at the mid-station all-way stop and
  goes when the junction is clear.
- **Berth:** it asks station control for its stop's front berth. If that berth
  is occupied or blocked it asks for a compatible alternate berth (another
  position at the same stop), or waits.
- **Pull-out:** it leaves only when the through lane beside and behind it is
  clear for its full length.
- **Merge:** it yields at Phillip Street until there is a gap in the traffic.

Every check uses the bus's full footprint (12.2 m by 2.6 m): whether it fits
in a berth, whether it can pass a stopped vehicle, and when its rear has
cleared a lane or a berth.

Other GRT buses (GTFS, 07:30–08:30 on Wednesday 2 December 2026) use the
same lanes and berths on their own schedules.

Modelled, not observed: speeds, accelerations, gaps, door and boarding times,
the Phillip Street traffic stream and the critical gap, the mapping of the
run's minute 0 to 07:30, the berth assignment of corridor buses, and the
other buses' dwell times when GTFS gives none. See docs/uw-station.md.
"""
import json
import math
import random
from pathlib import Path

GEO = json.loads((Path(__file__).resolve().parent / "uw_station.json").read_text())
L = GEO["length_m"]
BUS_L = GEO["bus"]["length_m"]
THRU, BERTH = GEO["lanes"]["through_offset_m"], GEO["lanes"]["berth_offset_m"]
V0 = 25 / 3.6          # desired speed on the station roadway (modelled; nothing mapped)
ACC, DEC = 1.0, 1.5    # m/s²
GAP = 2.5              # standstill gap to the vehicle ahead, m
HEADWAY = 1.2          # s
TAPER = 15.0           # metres to move from the through lane into a berth
PULL_TAPER = 6.0       # metres to swing out of a berth at walking pace
DOOR_OPEN_S = 4.0      # stop to doors open
ALIGHT_S = 1.5         # per rider setting down
LOAD_DWELL_S = 60.0    # doors open to pull-out request (the corridor's one-minute dwell)
DOOR_CLOSE_S = 5.0
STOP_WAIT_S = 2.0      # full stop at an all-way stop
CRITICAL_GAP_S = 6.0   # merge onto Phillip Street
PHILLIP_VEH_H = 600    # both directions (modelled)
GRT_MIN_DWELL_S = 20.0
CLEAR_BEHIND_S = 4.0   # pull-out: no vehicle in the through lane within this many seconds behind
PLATFORMS = {p["ref"]: p for p in GEO["platforms"]}
BERTHS = {b["id"]: dict(b, platform=p["ref"], side=p["side"]) for p in GEO["platforms"] for b in p["berths"]}
SET_DOWN, LOAD = "4", "6"
LOAD_STOP = "1078"


def simplify(trace, tol=0.05):
    """Drop samples that linear interpolation between the kept ones reproduces
    within `tol` metres (the browser interpolates linearly between samples)."""
    if len(trace) <= 2:
        return list(trace)
    keep = [trace[0]]
    start = 0
    for i in range(2, len(trace)):
        a, b = trace[start], trace[i]
        span = b[0] - a[0]
        bad = span > 30
        for k in range(start + 1, i):
            f = (trace[k][0] - a[0]) / span
            if any(abs(a[d] + (b[d] - a[d]) * f - trace[k][d]) > tol for d in (1, 2)):
                bad = True
                break
        if bad:
            keep.append(trace[i - 1])
            start = i - 1
    keep.append(trace[-1])
    return keep


def clock(t):
    s = int(round(t))
    return f"{s // 60:02d}:{s % 60:02d}"


def to_p(side, s):
    """Progress along the direction of travel."""
    return s if side == "se" else L - s


def to_s(side, p):
    return p if side == "se" else L - p


class Agent:
    def __init__(self, vid, kind, side, t_enter, calls, route="", label=""):
        self.id, self.kind, self.side = vid, kind, side
        self.t_enter = t_enter
        self.calls = calls          # [{"stop", "platform", "purpose", "depart_s"|None}]
        self.route, self.label = route, label
        self.p, self.v, self.lane = 0.0, 0.0, "thru"
        self.lat = THRU             # lateral offset magnitude (through to berth)
        self.state = "pending"
        self.call = 0
        self.berth = None
        self.dwell_until = None
        self.stopped_at = {}        # stop line -> time it came to rest there
        self.passed = set()
        self.trace = []
        self.episode = {}           # open decision episodes: kind -> decision dict
        self.bus = None             # engine Bus for corridor buses
        self.exit_s = None
        self.berth_log = []         # [berth, from, to]

    def lanes(self):
        """The lanes its footprint covers now, from its lateral position."""
        if self.lat <= THRU + 0.3:
            return {"thru"}
        if self.lat >= BERTH - 0.3:
            return {"berth"}
        return {"thru", "berth"}

    @property
    def rear(self):
        return self.p - BUS_L

    def berth_p(self, berth):
        b = BERTHS[berth]
        return to_p(self.side, b["front"]), to_p(self.side, b["rear"])


class UwStation:
    def __init__(self, engine, block=None, seed=17, adaptive=True):
        self.engine = engine
        self.adaptive = adaptive
        self.t = 0.0
        self.agents = []
        self.decisions = engine.decisions
        self.block = block
        self.reports = []
        self.gates = self._gates()
        rng = random.Random(seed * 7919 + 11)
        t, self.phillip = 0.0, []
        while t < engine.duration * 60 + 600:
            t += rng.expovariate(PHILLIP_VEH_H / 3600.0)
            self.phillip.append(t)
        self._background()
        if block and block.get("berth"):
            oos = Agent("OOS", "oos", BERTHS[block["berth"]]["side"], block["from_s"], [], label="out-of-service bus (scenario)")
            oos.leave_at = block["to_s"]
            self.agents.append(oos)
        self.seq = 0

    # --- inputs ---------------------------------------------------------------------
    def _gates(self):
        """When the ION crossing's lights flash: a train's nose within 260 m ahead
        of the crossing until its tail is 50 m past (the map's gate rule)."""
        cached = getattr(self.engine.ion, "_uw_gates", None)
        if cached is not None and cached[0] == self.engine.duration:
            return cached[1]
        import corridor
        crossing = next(c for c in corridor.DATA["ion_crossings"] if "Waterloo Station" in c["near"])
        intervals = []
        ops = self.engine.ion.ops
        L_ion = ops.ion.ION_LENGTH
        for t in range(0, self.engine.duration * 60 + 600):
            on = []
            for pose in ops.poses(float(t)):
                trip = pose["trip"]
                if trip is None:
                    continue
                at = crossing["at_m"][trip.direction]
                nose = pose["nose_line"]
                if -260.0 <= nose - at <= L_ion + 50.0:
                    on.append(pose["vehicle_id"])
            if on:
                if intervals and intervals[-1][1] == t - 1:
                    intervals[-1][1] = t
                    intervals[-1][2].update(on)
                else:
                    intervals.append([t, t, set(on)])
        out = [(a, b + 1, sorted(trains)) for a, b, trains in intervals]
        self.engine.ion._uw_gates = (self.engine.duration, out)
        return out

    def gate_at(self, t):
        for a, b, trains in self.gates:
            if a <= t < b:
                return trains
        return None

    def _background(self):
        """GRT buses from GTFS: one agent per vehicle visit to a side of the roadway."""
        trips = GEO["gtfs"]["trips"]
        side_of = {k: v["side"] for k, v in GEO["stops"].items()}
        platform_of = {k: v["platform"] for k, v in GEO["stops"].items()}
        by_block = {}
        for trip in trips:
            by_block.setdefault(trip["block"], []).append(trip)
        visits = {}
        for block, ts in sorted(by_block.items()):
            ts.sort(key=lambda x: x["calls"][0]["arrive_s"])
            current = None
            for trip in ts:
                for c in trip["calls"]:
                    side = side_of[c["stop"]]
                    call = {"stop": c["stop"], "platform": platform_of[c["stop"]],
                            "purpose": "set-down" if c["drop_off_only"] else ("layover" if c["first"] else "call"),
                            "arrive_s": c["arrive_s"], "depart_s": max(c["depart_s"], c["arrive_s"] + GRT_MIN_DWELL_S),
                            "route": trip["route"]}
                    if (current is not None and current.side == side and c["arrive_s"] - current.calls[-1]["depart_s"] <= 600
                            and to_p(side, PLATFORMS[call["platform"]]["s0"]) >= to_p(side, PLATFORMS[current.calls[-1]["platform"]]["s0"]) - 1):
                        # The same bus stays on (a drop-off then the next trip from the same stop).
                        if current.calls[-1]["stop"] == c["stop"]:
                            current.calls[-1]["depart_s"] = max(current.calls[-1]["depart_s"], call["depart_s"])
                            current.calls[-1]["route"] += f"→{trip['route']}"
                        else:
                            current.calls.append(call)
                        continue
                    visits[block] = visits.get(block, 0) + 1
                    vid = f"GRT-{block}" + (f"-{visits[block]}" if visits[block] > 1 else "")
                    first_p = to_p(side, BERTHS[next(b for b in BERTHS if BERTHS[b]["stop"] == c["stop"])]["front"])
                    t_enter = c["arrive_s"] - first_p / V0 - 8.0
                    current = Agent(vid, "grt", side, max(0.0, t_enter), [call], route=trip["route"],
                                    label=f"route {trip['route']} (block {block})")
                    self.agents.append(current)

    # --- corridor buses ----------------------------------------------------------------
    def entry_lead_s(self):
        """Free-flow seconds from the Ring Road entrance to the front set-down berth
        (the corridor leg is timed to reach the station's first stop)."""
        d = to_p("se", PLATFORMS[SET_DOWN]["berths"][0]["front"])
        t_acc = V0 / ACC
        d_acc = V0 * t_acc / 2
        return round(t_acc + max(0.0, d - d_acc) / V0, 1)

    def arrive(self, bus, t):
        """A corridor bus reaches the Ring Road entrance: dispatch assigns its UW trip."""
        n = sum(1 for a in self.agents if a.bus is bus) + 1
        agent = Agent(bus.bus_id, "corridor", "se", t, [
            {"stop": "1223", "platform": SET_DOWN, "purpose": "set-down", "depart_s": None},
            {"stop": LOAD_STOP, "platform": LOAD, "purpose": "load", "depart_s": None},
        ], route="corridor", label=f"{bus.bus_id} trip UW-{n}")
        agent.bus = bus
        agent.trip_id = f"UW-{bus.bus_id}-{n}"
        self.agents.append(agent)
        self.decide(t, "dispatch", agent, f"{bus.bus_id} reached the UW Station entrance (Ring Road)",
                    [{"option": "set down at platform 4 (stop 1223), load at platform 6 (stop 1078), leave by Phillip St",
                      "feasible": True, "reason": "the corridor route's stop is 1078; GRT routes arriving this way "
                      "set down on platform 4 (GTFS drop-off-only calls)", "estimate_s": None}],
                    "set down at platform 4 (stop 1223), load at platform 6 (stop 1078), leave by Phillip St")
        return agent

    # --- decisions ---------------------------------------------------------------------
    def decide(self, t, level, agent, trigger, options, chosen, outcome=""):
        self.seq += 1
        s = to_s(agent.side, agent.p)
        lon, lat = self.lonlat(s, agent.side, agent.lat)
        d = {"id": f"U{self.seq:03d}", "t": round(t, 1), "minute": int(t // 60), "level": level, "terminal": "uw",
             "vehicle_id": agent.id, "trip_id": getattr(agent, "trip_id", None), "trigger": trigger,
             "options": options, "chosen": chosen, "outcome": outcome, "location": [lon, lat]}
        self.decisions.append(d)
        return d

    def open_episode(self, agent, kind, t, level, trigger, options, chosen):
        if agent.kind != "corridor" or kind in agent.episode:
            return None
        d = self.decide(t, level, agent, trigger, options, chosen)
        agent.episode[kind] = (d, t)
        return d

    def close_episode(self, agent, kind, t, text):
        if kind in agent.episode:
            d, t0 = agent.episode.pop(kind)
            d["outcome"] = text.format(waited=f"{t - t0:.0f} s", t=clock(t))

    # --- geometry ----------------------------------------------------------------------
    def lonlat(self, s, side, lat_off):
        path = GEO["path"]
        s = max(0.0, min(L, s))
        lo, hi = 0, len(path) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if path[mid][2] <= s:
                lo = mid
            else:
                hi = mid
        a, b = path[lo], path[hi]
        f = 0.0 if b[2] == a[2] else (s - a[2]) / (b[2] - a[2])
        lon, lat = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        # Right of the south-west → north-east direction for the south-east side.
        dx, dy = (b[0] - a[0]) * 80700, (b[1] - a[1]) * 111200
        n = math.hypot(dx, dy) or 1.0
        rx, ry = dy / n, -dx / n
        off = lat_off if side == "se" else -lat_off
        return round(lon + rx * off / 80700, 7), round(lat + ry * off / 111200, 7)

    # --- occupancy ----------------------------------------------------------------------
    def active(self):
        return [a for a in self.agents if a.state not in ("pending", "gone")]

    def berth_holder(self, berth, t):
        for a in self.active():
            if a.berth == berth:
                return a.id
        return None

    def free_berths(self, agent, platform, t):
        """Berth positions the bus can reach: free, with every position behind it free too
        (the berth lane is entered from behind)."""
        ps = [b for b in PLATFORMS[platform]["berths"]]
        ps.sort(key=lambda b: to_p(agent.side, b["front"]), reverse=True)  # front first
        out = []
        for i, b in enumerate(ps):
            if self.berth_holder(b["id"], t) is not None:
                continue
            behind = ps[i + 1:]
            if all(self.berth_holder(x["id"], t) is None or self.agent(x["id"], t) is agent for x in behind):
                out.append(b["id"])
        return out

    def agent(self, berth, t):
        return next((a for a in self.active() if a.berth == berth), None)

    def leader(self, agent, lanes):
        """Nearest vehicle ahead of `agent` on its side in any of `lanes`."""
        best = None
        for o in self.active():
            if o is agent or o.side != agent.side:
                continue
            if not (o.lanes() & lanes):
                continue
            if o.p > agent.p - 0.1 and (best is None or o.p < best.p):
                best = o
        return best

    # --- one second ---------------------------------------------------------------------
    def advance_to(self, t_end):
        while self.t < t_end - 1e-9:
            self.step(self.t)
            self.t += 1.0

    def step(self, t):
        for a in self.agents:
            if a.kind == "oos":
                if a.state == "pending" and a.t_enter <= t:
                    b = self.block["berth"]
                    a.berth, a.lane, a.lat, a.state = b, "berth", BERTH, "parked"
                    a.p = to_p(a.side, BERTHS[b]["front"])
                    a.berth_log.append([b, t, None])
                elif a.state == "parked" and t >= a.leave_at:
                    a.state, a.berth = "gone", None
                    a.berth_log[-1][2] = t
                continue
            if a.state == "pending" and a.t_enter <= t:
                # Enter only if the entrance is clear.
                behind = [o for o in self.active() if o.side == a.side and o.rear < GAP + 1.0]
                if not behind:
                    a.state = "drive"
                    a.p, a.v = 0.0, 0.0
        for side in ("se", "nw"):
            for a in sorted((x for x in self.active() if x.side == side and x.kind != "oos"), key=lambda x: -x.p):
                self.move(a, t)
        for a in self.active():
            a.trace.append([t, round(to_s(a.side, a.p), 2), round(a.lat if a.side == "se" else -a.lat, 2)])

    def target(self, a, t):
        """(stop position, reason) the bus must not pass this second."""
        limits = [(math.inf, None)]
        lines = GEO["stop_lines"][a.side]
        # Vehicle ahead in its lane (both lanes while it changes lane).
        lanes = set(a.lanes())
        if a.state == "enter_berth":
            # Turning in: the berth lane ahead matters from the start of its taper.
            front, _ = a.berth_p(a.berth)
            if a.p >= front - BUS_L - TAPER - 10.0:
                lanes.add("berth")
        elif a.state == "pullout":
            lanes = {"thru", "berth"}
        lead = self.leader(a, lanes)
        if lead is not None and a.state == "pullout" and lead.lane == "berth" and lead.state not in ("enter_berth", "pullout"):
            # Swinging out of a berth: the bus in the berth ahead matters only while
            # this one's front is still mostly in the berth lane.
            if a.lat < BERTH - GEO["bus"]["width_m"] - 0.3:   # footprints no longer overlap sideways
                lead = self.leader(a, {"thru"})
                gap = GAP
            else:
                gap = 0.3
        else:
            gap = GAP
        if lead is not None:
            gap_stop = lead.rear - gap - HEADWAY * max(0.0, a.v - lead.v)
            limits.append((gap_stop, ("follow", lead)))
        # The ION level crossing (south-east side approaches from Ring Road).
        lc = lines.get("level_crossing")
        if lc is not None:
            lc_p = to_p(a.side, lc)
            far = to_p(a.side, max(GEO["features"]["level_crossing"]) if a.side == "se" else min(GEO["features"]["level_crossing"]))
            trains = self.gate_at(t)
            if trains and a.p <= lc_p + 0.01 and lc_p - a.p >= a.v * a.v / (2 * DEC) - 0.5:
                limits.append((lc_p, ("gate", trains)))
        # All-way stops: a full stop, then go when the junction is clear.
        for name in ("mid_stop", "ring_stop"):
            line = lines.get(name)
            if line is None or name in a.passed:
                continue
            lp = to_p(a.side, line)
            if a.p > lp + 0.5:
                a.passed.add(name)
                continue
            if a.v < 0.05 and abs(a.p - lp) < 0.8 and name not in a.stopped_at:
                a.stopped_at[name] = t
            rested = a.stopped_at.get(name)
            if rested is None or t - rested < STOP_WAIT_S or not self.junction_clear(a, t):
                limits.append((lp, ("stop", name)))
        # The exit: yield to Phillip Street.
        ex = lines.get("exit")
        if ex is not None and "exit" not in a.passed:
            lp = to_p(a.side, ex)
            if a.p > lp + 0.5:
                a.passed.add("exit")
            elif not self.can_merge(a, t):
                limits.append((lp, ("merge", None)))
        # Its berth.
        if a.state in ("drive", "enter_berth") and a.call < len(a.calls):
            if a.berth is not None:
                front, _ = a.berth_p(a.berth)
                limits.append((front, ("berth", a.berth)))
            else:
                plat = PLATFORMS[a.calls[a.call]["platform"]]
                limits.append((self.hold_point(a, plat), ("wait_berth", plat["ref"])))
        if a.state in ("dwell", "wait_pullout"):
            limits.append((a.p, ("dwell", None)))
        return min(limits, key=lambda x: x[0])

    def hold_point(self, a, plat):
        """Where a bus waits for a berth: before the platform's taper, but never
        with any part of it inside the all-way-stop junction (then at its stop line)."""
        hold = to_p(a.side, plat["s0"] if a.side == "se" else plat["s1"]) - TAPER - 2.0
        mid = to_p(a.side, GEO["features"]["mid_stop"])
        line = to_p(a.side, GEO["stop_lines"][a.side]["mid_stop"])
        if hold - BUS_L < mid + 10.0 and hold > line:
            hold = line
        return hold

    def junction_clear(self, a, t):
        mid = GEO["features"]["mid_stop"]
        for o in self.active():
            if o is a:
                continue
            s_front, s_rear = to_s(o.side, o.p), to_s(o.side, o.rear)
            lo, hi = min(s_front, s_rear), max(s_front, s_rear)
            if o.v > 0.3 and lo < mid + 8 and hi > mid - 8:
                return False
        return True

    def can_merge(self, a, t):
        if self.block and self.block.get("exit") and self.block["from_s"] <= t < self.block["to_s"]:
            return False
        return not any(t <= x < t + CRITICAL_GAP_S for x in self.phillip)

    def move(self, a, t):
        self.plan_berth(a, t)
        stop, why = self.target(a, t)
        dist = stop - a.p
        v_brake = math.sqrt(max(0.0, 2 * DEC * max(0.0, dist - 0.3)))
        v_new = max(0.0, min(a.v + ACC, V0, v_brake))
        step = min(max(0.0, dist), (a.v + v_new) / 2)
        if dist <= 0.05:
            v_new, step = 0.0, 0.0
        a.p += step
        a.v = v_new if step > 0 else 0.0
        self.observe(a, t, why)
        # Lane changes.
        if a.state == "enter_berth":
            front, rear = a.berth_p(a.berth)
            a.lat = THRU + (BERTH - THRU) * min(1.0, max(0.0, (a.p - (front - BUS_L - TAPER)) / TAPER))
            if a.lat >= BERTH - 1e-6:
                a.lane = "berth"
            if a.v == 0.0 and abs(a.p - front) < 0.5:
                self.at_berth(a, t)
        elif a.state == "pullout":
            a.lat = max(THRU, a.lat - (BERTH - THRU) * step / PULL_TAPER)
            if a.lat <= THRU + 1e-6:
                a.lane = "thru"
            # The berth is released once the rear has cleared its front.
            if a.berth is not None and a.rear > a.berth_p(a.berth)[0]:
                if a.berth_log and a.berth_log[-1][2] is None:
                    a.berth_log[-1][2] = t
                a.berth = None
            if a.lane == "thru" and a.berth is None:
                a.state = "drive"
        elif a.state in ("dwell", "wait_pullout"):
            self.dwell(a, t)
        if a.state == "drive" and a.call >= len(a.calls) and a.p >= L - 0.5:
            self.leave(a, t)

    # --- berths ------------------------------------------------------------------------
    def plan_berth(self, a, t):
        if a.state != "drive" or a.call >= len(a.calls) or a.berth is not None:
            return
        call = a.calls[a.call]
        plat = PLATFORMS[call["platform"]]
        request_p = to_p(a.side, plat["s0"] if a.side == "se" else plat["s1"]) - 60.0
        if a.p < request_p:
            return
        own = [b["id"] for b in plat["berths"] if b["stop"] == call["stop"]]
        preferred = own[0]
        # Only berths it can still turn into (the taper starts ahead of it) and
        # reach: no vehicle waiting in the through lane between it and the berth.
        def reachable(b):
            front = a.berth_p(b)[0]
            if front - BUS_L - TAPER < a.p - 0.5:
                return False
            for o in self.active():
                if o is a or o.side != a.side or "thru" not in o.lanes():
                    continue
                if a.p < o.p < front and o.v < 0.5:
                    return False
            return True
        free = [b for b in self.free_berths(a, plat["ref"], t) if reachable(b)]
        if preferred in free:
            choice = preferred
        else:
            compatible = [b for b in free if b in own] if call["purpose"] == "load" else free
            choice = compatible[0] if compatible else None
        if a.kind == "corridor":
            self.berth_decision(a, t, call, plat, preferred, free, choice)
        if choice is not None:
            a.berth = choice
            a.state = "enter_berth"

    def berth_decision(self, a, t, call, plat, preferred, free, choice):
        holder = self.berth_holder(preferred, t)
        if choice == preferred and "berth_wait" not in a.episode:
            return
        options = [{"option": f"berth {preferred} (stop {call['stop']}, front)", "feasible": holder is None and preferred in free,
                    "reason": f"occupied by {holder}" if holder else ("free" if preferred in free else
                                                                      "a bus behind it blocks the way in"),
                    "estimate_s": None}]
        for b in plat["berths"]:
            if b["id"] == preferred:
                continue
            h = self.berth_holder(b["id"], t)
            ok = b["id"] in free and (call["purpose"] != "load" or b["stop"] == call["stop"])
            reason = f"occupied by {h}" if h else ("free, same stop" if ok else "not reachable (berth lane blocked behind)")
            options.append({"option": f"alternate berth {b['id']}", "feasible": ok, "reason": reason, "estimate_s": None})
        options.append({"option": "wait in the through lane before the platform", "feasible": True,
                        "reason": "keeps the berth lane clear; vehicles behind wait", "estimate_s": None})
        if choice is None:
            self.open_episode(a, "berth_wait", t, "station", f"Berth request at platform {plat['ref']} ({call['purpose']})",
                              options, "wait in the through lane before the platform")
        else:
            if "berth_wait" in a.episode:
                self.close_episode(a, "berth_wait", t, f"granted {choice} at {{t}} after waiting {{waited}}")
            if choice != preferred:
                d = self.decide(t, "station", a, f"Berth request at platform {plat['ref']} ({call['purpose']}): "
                                f"{preferred} {'occupied by ' + holder if holder else 'not reachable'}",
                                options, f"alternate berth {choice}")
                a.pending_berth_decision = d

    def at_berth(self, a, t):
        call = a.calls[a.call]
        a.berth_log.append([a.berth, t, None])
        a.state = "dwell"
        a.lane = "berth"
        a.doors_open = t + DOOR_OPEN_S
        a.dwell_phase = "opening"
        d = getattr(a, "pending_berth_decision", None)
        if d is not None:
            d["outcome"] = f"stopped at {a.berth} at {clock(t)}"
            a.pending_berth_decision = None

    def dwell(self, a, t):
        call = a.calls[a.call]
        if a.state == "dwell":
            if a.dwell_phase == "opening" and t >= a.doors_open:
                a.dwell_phase = "open"
                if a.kind == "corridor":
                    if call["purpose"] == "set-down":
                        n = self.engine.station_set_down(a.bus, t)
                        a.dwell_until = t + max(8.0, ALIGHT_S * n) + DOOR_CLOSE_S
                    else:
                        self.engine.station_doors_open(a.bus, t)
                        a.dwell_until = t + LOAD_DWELL_S - DOOR_CLOSE_S
                        a.close_due = a.dwell_until
                else:
                    a.dwell_until = max(t, call["depart_s"] - DOOR_CLOSE_S) + DOOR_CLOSE_S
            if a.dwell_phase == "open" and a.dwell_until is not None and t >= a.dwell_until:
                if a.kind == "corridor" and call["purpose"] == "load":
                    hold = self.engine.station_doors_closing(a.bus, t)
                    if hold:
                        a.dwell_until = t + hold
                        return
                    a.dwell_phase = "closed"
                    a.ready = t + DOOR_CLOSE_S
                else:
                    a.dwell_phase = "closed"
                    a.ready = t
            if a.dwell_phase == "closed" and t >= a.ready:
                a.state = "wait_pullout"
        if a.state == "wait_pullout":
            why = self.pullout_blocked(a, t)
            if why is None:
                self.close_episode(a, "pullout", t, "pulled out at {t} after waiting {waited}")
                a.state = "pullout"
                a.call += 1
            else:
                self.open_episode(a, "pullout", t, "vehicle", f"Ready to leave {a.berth}",
                                  [{"option": "pull out into the through lane", "feasible": False, "reason": why,
                                    "estimate_s": None}]
                                  + ([{"option": "leave by Ring Road instead", "feasible": False,
                                       "reason": "no space to turn a 12.2 m bus on the two-lane roadway (no loop mapped)",
                                       "estimate_s": None}] if "exit" in why else [])
                                  + [{"option": "wait at the berth", "feasible": True,
                                      "reason": "it needs the through lane clear for its whole length" if "exit" not in why
                                      else "doors stay closed; riders already aboard", "estimate_s": None}],
                                  "wait at the berth")

    def pullout_blocked(self, a, t):
        if a.call == len(a.calls) - 1 and a.kind == "corridor" and self.block and self.block.get("exit") \
                and self.block["from_s"] <= t < self.block["to_s"]:
            return "the Phillip Street exit is blocked (scenario)"
        for o in self.active():
            if o is a or o.side != a.side or "thru" not in o.lanes():
                continue
            # Ahead: the bus needs its own length plus a gap in front of it.
            if a.rear - 1.0 <= o.rear <= a.p + BUS_L * 0.5 + GAP:
                return f"{o.id} is beside or just ahead in the through lane"
            # Behind: anything that would reach it within CLEAR_BEHIND_S.
            if o.p < a.rear and a.rear - o.p < max(GAP + 2.0, o.v * CLEAR_BEHIND_S):
                return f"{o.id} is approaching in the through lane ({a.rear - o.p:.0f} m behind at {o.v * 3.6:.0f} km/h)"
        return None

    # --- observations: decisions taken by the agent ------------------------------------
    def observe(self, a, t, why):
        if a.v < 0.05 and why is not None and why[0] not in ("dwell", "berth"):
            a.stopped = getattr(a, "stopped", {})
            a.stopped[why[0]] = a.stopped.get(why[0], 0) + 1
        if a.kind != "corridor" or why is None:
            for k in ("follow", "gate", "stop", "merge"):
                if k in a.episode:
                    self.close_episode(a, k, t, "moved on at {t} after {waited}")
            return
        kind, what = why
        moving_freely = a.v >= V0 * 0.95
        if kind == "follow" and a.v < 0.3 and what.v < 0.3:
            self.open_episode(a, "follow", t, "vehicle", f"{what.id} ({what.label or what.kind}) slow or stopped ahead",
                              [{"option": "slow and follow", "feasible": True,
                                "reason": f"{GAP:.1f} m standstill gap and {HEADWAY:.1f} s headway", "estimate_s": None},
                               {"option": "pass in the berth lane", "feasible": False,
                                "reason": "berth lanes are for buses at the platforms", "estimate_s": None}],
                              "slow and follow")
        if kind == "gate":
            self.open_episode(a, "gate", t, "vehicle", f"ION level crossing lights flashing ({', '.join(what)})",
                              [{"option": "cross now", "feasible": False, "reason": "lights flashing, gates lowering or down",
                                "estimate_s": None},
                               {"option": "stop at the stop line", "feasible": True, "reason": "can stop in time",
                                "estimate_s": None}],
                              "stop at the stop line")
        if kind == "merge" and a.v < 0.1:
            blocked = self.block and self.block.get("exit") and self.block["from_s"] <= t < self.block["to_s"]
            self.open_episode(a, "merge", t, "vehicle", "At the Phillip Street exit",
                              [{"option": "turn onto Phillip Street", "feasible": False,
                                "reason": "exit blocked (scenario)" if blocked else
                                f"no {CRITICAL_GAP_S:.0f} s gap in Phillip Street traffic yet", "estimate_s": None},
                               {"option": "yield and wait", "feasible": True, "reason": "", "estimate_s": None}],
                              "yield and wait")
        for k in ("follow", "gate", "stop", "merge"):
            if k in a.episode and k != kind and a.v > 1.0:
                self.close_episode(a, k, t, "moved on at {t} after {waited}")

    def leave(self, a, t):
        a.state = "gone"
        a.exit_s = t
        a.berth = None
        if a.kind == "corridor":
            for k in list(a.episode):
                self.close_episode(a, k, t, "moved on at {t} after {waited}")
            first = next((d for d in self.decisions if d.get("terminal") == "uw" and d["vehicle_id"] == a.id
                          and d.get("trip_id") == a.trip_id and d["level"] == "dispatch" and not d["outcome"]), None)
            if first is not None:
                first["outcome"] = (f"in the station {clock(a.t_enter)}–{clock(t)} ({t - a.t_enter:.0f} s); "
                                    f"left by Phillip St at {clock(t)}")
            self.engine.station_exit(a.bus, t)

    def blocked(self, t):
        out = []
        for a in self.active():
            if a.kind == "oos" and a.berth:
                out.append(f"{a.berth} ({a.label})")
        if self.block and self.block.get("exit") and self.block["from_s"] <= t < self.block["to_s"]:
            out.append("Phillip Street exit (scenario)")
        return out

    def snapshot(self, t):
        """Berth occupancy at t (for the live view)."""
        return {"berths": {b: (self.agent(b, t).id if self.agent(b, t) else None) for b in BERTHS},
                "blocked": self.blocked(t),
                "buses_in_station": [a.id for a in self.active() if a.kind == "corridor"]}

    def metrics(self):
        """Corridor buses at UW: visits, time in the station and seconds stopped by cause."""
        visits = [a for a in self.agents if a.kind == "corridor" and a.exit_s is not None]
        stopped = {}
        for a in self.agents:
            if a.kind == "corridor":
                for k, v in getattr(a, "stopped", {}).items():
                    stopped[k] = stopped.get(k, 0) + v
        grt = [a for a in self.agents if a.kind == "grt"]
        grt_stopped = sum(sum(getattr(a, "stopped", {}).values()) for a in grt)
        return {
            "corridor_departures": len(visits),
            "mean_station_s": round(sum(a.exit_s - a.t_enter for a in visits) / len(visits), 1) if visits else 0.0,
            "corridor_queue_s": sum(stopped.values()),
            "corridor_stopped_by": stopped,
            "grt_buses": len(grt),
            "grt_queue_s": grt_stopped,
        }

    # --- results -----------------------------------------------------------------------
    def plans(self):
        out = []
        for a in self.agents:
            if not a.trace:
                continue
            keep = simplify(a.trace)
            out.append({"id": a.id, "kind": a.kind, "route": a.route, "label": a.label, "side": a.side,
                        "t_enter": a.t_enter, "trace": keep, "exit_s": a.exit_s,
                        "berths": [[b, t0, t1] for b, t0, t1 in a.berth_log]})
        return out
