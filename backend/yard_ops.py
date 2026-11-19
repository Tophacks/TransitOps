"""Dutton Drive yard operations (ION OMSF, 518 Dutton Drive, Waterloo).

The yard has a finite fleet of physical trains with persistent IDs. Each is in
one of these states:

    stored        parked, needs a full preparation before it can leave
    ready         prepared spare, can leave after a short departure check
    unavailable   in the shop or withdrawn with a fault
    pulling out   moving from its storage position to the line
    in service    running trips
    returning     moving from the line back into the yard

Track: storage positions, the two leads and the routes between them come from
the OSM yard graph in ion_network.json["yard"] (tools/waterloo_geometry/yard.py).
A move is enabled only when its whole route is usable:

- the storage position is not blocked in by a train nearer the lead;
- every yard resource on the route (stub, internal switches, the lead) is free,
  including a lead blocked by the scenario;
- the main-line block it enters is empty: the northbound Northfield block
  (Research and Technology to Northfield, which holds both lead switches) and,
  for the south lead, the southbound Research and Technology block it joins
  over the Albert Street crossover.

Yard resources are released when the train's rear has cleared them by the
fouling distance, as in the terminals.

Dispatch (RailOps) asks this module for the earliest feasible yard release
for a trip and compares it with reassigning a train already on the line.

Modelled, not observed: the yard fleet allocation (14 LRVs in all; the
in-service fleet is sized from the timetable, the rest are in the yard: one
ready spare, then stored, the last in the shop), preparation times, storage
positions of individual trains, the 15 km/h yard speed and the stop short of
the track end.
"""
import math
from types import SimpleNamespace

import rail_ops as ro

YARD = ro.NETWORK["yard"]
ROUTES = {r["id"]: r for r in YARD["routes"]}
TRACKS = {t["id"]: t for t in YARD["tracks"]}

FLEET_TOTAL = 14            # GRT ION fleet (14 Bombardier Flexity Freedom LRVs)
READY_PREP_S = 180.0        # modelled: operator boards, brake and door tests on a prepared spare
STORED_PREP_S = 1200.0      # modelled: wake-up and full departure inspection of a stored train
LABEL = {"north": "north lead (node 7020996626)", "south": "south lead (node 7020977372)"}


def nonrevenue(kind, vehicle, direction, t):
    """An out-of-service movement, shaped like a timetabled trip for the line code."""
    return SimpleNamespace(train_id=f"{vehicle}-{'OUT' if kind == 'out' else 'IN'}", direction=direction,
                           scheduled_s=t, depart_s=None, hold_reason="", transfers={}, trip_hold_s=0.0,
                           vehicle_id=None, platform=None, station_times={}, decisions=[], dispatch_note="",
                           nonrevenue=kind, arrived_s=None)


class Yard:
    def __init__(self, ops, block=None, fault=None):
        self.ops = ops
        self.ion = ops.ion
        L = self.ion.ION_LENGTH
        gap, short = YARD["modelled"]["store_gap_m"], YARD["modelled"]["stop_short_m"]
        self.L, self.gap, self.short = L, gap, short
        self.reservations = {}      # resource -> [[holder, from, to]]
        self.occupied = {}          # (track id, k) -> vehicle id
        self.log = []               # yard movements for the map and tests
        self.block = block
        self.fault = fault
        self.fault_pending = bool(fault)
        if block:
            self.reservations.setdefault(f"lead:{block['lead']}", []).append(
                ["track work (scenario)", block["from_minute"] * 60.0, block["to_minute"] * 60.0])
        # Storage order: outdoor tracks nearest the north lead first, shop tracks for unavailable trains.
        def out_len(t):
            return ROUTES[f"yard-out:north:{t['way']}"]["length_m"]
        self.storage = sorted((t for t in YARD["tracks"] if t["kind"] == "outdoor"), key=out_len)
        self.shop = sorted((t for t in YARD["tracks"] if t["kind"] == "shop"), key=out_len)
        self.trains = []
        n_yard = max(0, FLEET_TOTAL - ops.fleet)
        for i in range(n_yard):
            vid = f"LRV{501 + ops.fleet + i}"
            if i == n_yard - 1 and n_yard >= 3:
                state, track = "unavailable", self.shop[0]
            else:
                state, track = ("ready" if i == 0 else "stored"), self.storage[i % len(self.storage)]
            k = self.free_position(track)
            train = ro.Train(vid, lead="A")
            train.state_log = []
            train.yard_pos = (track["id"], k)
            self.occupied[(track["id"], k)] = vid
            self.set_state(ops.start_s, train, state, f"{track['where']}, track {track['way']} position {k + 1} (modelled)")
            route = ROUTES[f"yard-out:north:{track['way']}"]
            s = self.out_s(k)
            train.add(ro.Segment("stand", ops.start_s, math.inf, "A", None, "network", route["id"], s, s,
                                 note=f"{state} at Dutton: track {track['way']} position {k + 1}"))
            ops.trains.append(train)
            self.trains.append(train)

    # --- geometry -------------------------------------------------------------
    def out_s(self, k):
        """Nose position (from the track end) of a train stored at position k, facing the lead."""
        return self.short + k * (self.L + self.gap) + self.L

    def in_s(self, route, k):
        return route["length_m"] - (self.short + k * (self.L + self.gap))

    def free_position(self, track):
        """Innermost free position with every position nearer the lead free too."""
        used = [k for (tid, k) in self.occupied if tid == track["id"]]
        k = (max(used) + 1) if used else 0
        return k if k < track["positions"] else None

    def blocked_in(self, train):
        tid, k = train.yard_pos
        return next((v for (t2, k2), v in self.occupied.items() if t2 == tid and k2 > k), None)

    # --- state ------------------------------------------------------------------
    def set_state(self, t, train, state, note=""):
        train.state = state
        if not hasattr(train, "state_log"):
            train.state_log = []
        train.state_log.append({"t": round(t, 1), "state": state, "note": note})

    def free_at(self, route, t, vehicle=None):
        """Earliest time >= t at which every yard resource of `route` is free (inf if held open-ended)."""
        at = t
        for _ in range(20):
            moved = False
            for res in route["resources"]:
                for holder, a, b in self.reservations.get(res["id"], []):
                    if holder == vehicle:
                        continue
                    if a <= at + 1e-9 and (b is None or b > at + 1e-9):
                        if b is None:
                            return math.inf
                        at, moved = b, True
            if not moved:
                return at
        return at

    def blocker(self, route, t, vehicle=None):
        for res in route["resources"]:
            for holder, a, b in self.reservations.get(res["id"], []):
                if holder != vehicle and a <= t + 1e-9 and (b is None or b > t + 1e-9):
                    return res["id"], holder, b
        return None

    def reserve(self, route, vehicle, t, prof, s0, end_m=None):
        for res in route["resources"]:
            if res["s1"] + self.L <= s0:
                continue  # behind the train
            if end_m is not None and res["s0"] > end_m:
                continue
            if end_m is not None and res["s1"] + self.L > end_m:
                release = None  # under the train when it stops (released when it leaves)
            else:
                release = t + prof.time_at(max(0.0, res["s1"] + self.L - s0))
            self.reservations.setdefault(res["id"], []).append([vehicle, t, release])
            if release is not None:
                self.ops.push(release, 1, "yard_release")

    def close_open(self, vehicle, t):
        for entries in self.reservations.values():
            for e in entries:
                if e[0] == vehicle and e[2] is None:
                    e[2] = t

    # --- dispatch estimates ----------------------------------------------------------
    def spare_options(self, t, trip):
        """Each yard train against `trip`: (option dict, train, estimate)."""
        key = self.ops.terminal_of[trip.direction]
        side = "north" if key == "conestoga" else "south"
        other = "south" if side == "north" else "north"
        out = []
        for train in self.trains:
            name = f"send {train.state} spare {train.vehicle_id} from Dutton"
            if train.state == "unavailable":
                out.append(({"option": f"send {train.vehicle_id} from Dutton", "feasible": False,
                             "reason": "unavailable (in the shop)", "estimate_s": None}, train, math.inf))
                continue
            if train.state not in ("ready", "stored") or getattr(train, "yard_job", None) is not None:
                continue
            est, why = self.release_estimate(t, train, side, trip)
            if math.isinf(est):
                out.append(({"option": name, "feasible": False, "reason": why, "estimate_s": None}, train, math.inf))
            else:
                out.append(({"option": name, "feasible": True, "reason": why, "estimate_s": round(est, 1)}, train, est))
        if self.trains:
            to = "Conestoga" if side == "north" else "Fairway"
            out.append(({"option": f"spare via the {other} lead", "feasible": False,
                         "reason": f"the {other} lead points away from {to}: reaching it needs a reversal on the running "
                                   f"line, which the model does not permit", "estimate_s": None}, None, math.inf))
        return out

    def release_estimate(self, t, train, side, trip):
        """Earliest departure on `trip` for a yard train: preparation, the yard
        route and lead (blocks included), the run to the terminal and the turnaround."""
        ops = self.ops
        tid, k = train.yard_pos
        track = TRACKS[tid]
        if side not in track["leads"]:
            return math.inf, f"track {track['way']} has no route to the {side} lead"
        inner = self.blocked_in(train)
        if inner is not None:
            return math.inf, f"blocked in on track {track['way']} by {inner}"
        prep = READY_PREP_S if train.state == "ready" else STORED_PREP_S
        route = ROUTES[f"yard-out:{side}:{track['way']}"]
        ready = t + prep
        go = self.free_at(route, ready, train.vehicle_id)
        if math.isinf(go):
            held = self.blocker(route, ready, train.vehicle_id)
            return math.inf, f"{held[0]} held by {held[1]} with no release time"
        s0 = self.out_s(k)
        pull = ro.move_profile("network", route["id"], s0, route["length_m"]).duration
        at = go + pull + self.ion.DWELL_S
        line = ops.plan.lines["north" if side == "north" else "south"]
        if side == "south":
            at += ops.line_seconds("south", ops.stop_index("south", route["to_station"]), len(line.legs) - 1)
        dest = ops.terminal_of[trip.direction]
        st = ops.stations[dest]
        pref = st.layout["preference"][0]
        arrive = ro.move_profile(dest, f"arrive:{pref}", 0.0, st.route(f"arrive:{pref}")["stop_m"]).duration
        depart = max(trip.scheduled_s, at + arrive + ro.TURN_S)
        bits = [f"{'ready' if train.state == 'ready' else 'stored'}: {prep / 60:.0f} min preparation (modelled)"]
        if go > ready + 1:
            held = self.blocker(route, ready, train.vehicle_id)
            bits.append(f"{held[0].replace(':', ' ')} {('blocked by ' + held[1]) if held else 'busy'} until {ro.fmt(go)}")
        bits.append(f"{pull / 60:.1f} min via the {side} lead to {route['to_station'].replace(' Station', '')}")
        bits.append(f"departs {ro.fmt(depart)}")
        return depart, "; ".join(bits)

    # --- pull-out -------------------------------------------------------------------
    def dispatch(self, t, train, trip, decision):
        """Send yard train `train` to take `trip`."""
        side = "north" if self.ops.terminal_of[trip.direction] == "conestoga" else "south"
        train.next_trip = trip
        train.yard_job = {"side": side, "trip": trip, "decision": decision}
        prep = READY_PREP_S if train.state == "ready" else STORED_PREP_S
        if train.state == "stored":
            self.set_state(t, train, "stored", f"preparing for {trip.train_id} ({prep / 60:.0f} min, modelled)")
        self.ops.push(t + prep, 2, "yard_try_out", train=train)

    def on_try_out(self, t, train):
        ops = self.ops
        job = train.yard_job
        side = job["side"]
        tid, k = train.yard_pos
        track = TRACKS[tid]
        route = ROUTES[f"yard-out:{side}:{track['way']}"]
        if train.state != "ready":
            self.set_state(t, train, "ready", "prepared")
        why = None
        inner = self.blocked_in(train)
        if inner is not None:
            why = f"blocked in on track {track['way']} by {inner}"
        held = self.blocker(route, t, train.vehicle_id)
        if why is None and held is not None:
            why = f"{held[0].replace(':', ' ')} held by {held[1]}" + (f" until {ro.fmt(held[2])}" if held[2] else "")
            if held[2] is not None:
                ops.push(held[2], 2, "yard_try_out", train=train)
        nf = ops.stop_index("north", "Northfield Station")
        blocks = [("north", nf)]
        if side == "south":
            blocks.append(("south", ops.stop_index("south", route["to_station"])))
        if why is None:
            for key in blocks:
                holder = ops.platform_at.get(key)
                if holder is not None and holder[0] is not train:
                    station = ops.plan.lines[key[0]].stops[key[1]][0].replace(" Station", "")
                    why = f"{key[0]}bound {station} block occupied by {holder[0].vehicle_id}"
                    waiters = ops.platform_waiters.setdefault(key, [])
                    if ("yard", train) not in waiters:
                        waiters.append(("yard", train))
                    break
        options = [{"option": f"pull out via the {LABEL[side]}", "feasible": why is None,
                    "reason": why or f"route to {route['to_station'].replace(' Station', '')} clear: {route['via']}",
                    "estimate_s": None}]
        if why is not None:
            options.append({"option": "wait on the storage track", "feasible": True, "reason": "", "estimate_s": None})
            if not getattr(train, "logged_wait", None) == why:
                train.logged_wait = why
                d = ops.decide(t, "station", "yard", train, job["trip"], f"Pull-out for {job['trip'].train_id} requested",
                               options, "wait on the storage track")
                job["trip"].decisions.append(d)
            return
        train.logged_wait = None
        self.grant_out(t, train, route, blocks, options)

    def grant_out(self, t, train, route, blocks, options):
        ops = self.ops
        job = train.yard_job
        tid, k = train.yard_pos
        s0 = self.out_s(k)
        prof = ro.move_profile("network", route["id"], s0, route["length_m"])
        stand = train.segments[-1]
        stand.t1 = t
        del self.occupied[(tid, k)]
        train.yard_pos = None
        self.close_open(train.vehicle_id, t)
        self.reserve(route, train.vehicle_id, t, prof, s0)
        for key in blocks:
            ops.hold_platform(key[0], key[1], train, math.inf)
        side = job["side"]
        direction = "north" if side == "north" else "south"
        pseudo = nonrevenue("out", train.vehicle_id, direction, t)
        pseudo.depart_s = t
        train.trip = pseudo
        self.set_state(t, train, "pulling out", f"for {job['trip'].train_id} via the {LABEL[side]}")
        end = t + prof.duration
        train.add(ro.Segment("move", t, end, train.lead, pseudo.train_id, "network", route["id"], s0, route["length_m"],
                             note=f"pulling out of Dutton for {job['trip'].train_id}"))
        stop = route["to_station"]
        pseudo.station_times[stop] = end
        train.add(ro.Segment("stand", end, end + self.ion.DWELL_S, train.lead, pseudo.train_id, "network", route["id"],
                             route["length_m"], route["length_m"], station=stop, note="out of service"))
        d = ops.decide(t, "station", "yard", train, job["trip"], f"Pull-out for {job['trip'].train_id} requested",
                       options, f"pull out via the {LABEL[side]}")
        job["trip"].decisions.append(d)
        d.outcome = f"on the line at {stop.replace(' Station', '')} {ro.fmt(end)}"
        self.log.append({"vehicle_id": train.vehicle_id, "kind": "out", "route": route["id"], "t0": t, "t1": end,
                         "for": job["trip"].train_id, "track": tid, "position": k + 1})
        if side == "north":
            # Release the Northfield block from the lead onwards as a normal line stop.
            idx = ops.stop_index("north", stop)
            ops.hold_platform("north", idx, train, end + self.ion.DWELL_S)
            ops.plan_line(train, pseudo, idx, end + self.ion.DWELL_S)
        else:
            idx = ops.stop_index("south", stop)
            nf = ops.stop_index("north", "Northfield Station")
            # Its rear has cleared the northbound track once it stands at the platform.
            ops.push(end, 1, "yard_release_block", direction="north", index=nf, train=train)
            ops.hold_platform("south", idx, train, end + self.ion.DWELL_S)
            ops.plan_line(train, pseudo, idx, end + self.ion.DWELL_S)
        train.yard_job = None
        train.terminal = None

    def on_release_block(self, t, direction, index, train):
        self.ops.release_platform(t, direction, index, train)

    def on_release(self, t):
        # Yard trains waiting on a released resource try again.
        for train in self.trains + [tr for tr in self.ops.trains if getattr(tr, "yard_in_wait", None)]:
            if getattr(train, "yard_job", None) is not None and train.state in ("ready", "stored"):
                if getattr(train, "logged_wait", None):
                    self.ops.push(t, 2, "yard_try_out", train=train)

    # --- withdrawal and return ------------------------------------------------------
    def withdraw(self, t, train, st):
        """A train found faulty on arrival (scenario): out of service, back to the yard."""
        ops = self.ops
        self.fault_pending = False
        side = "north" if st.key == "conestoga" else "south"
        self.set_state(t, train, "unavailable", f"fault found at {st.layout['name']} (scenario)")
        direction = ro.TERMINALS[st.key]["depart_line"]
        pseudo = nonrevenue("in", train.vehicle_id, direction, t)
        pseudo.side = side
        pseudo.fault = True
        train.next_trip = pseudo
        train.withdrawn = True
        if side not in [ln for t2 in self.shop + self.storage for ln in t2["leads"]]:
            raise RuntimeError("no yard track reaches that lead")
        d = ops.decide(t, "dispatch", st.key, train, train.trip, f"{train.vehicle_id} reports a fault at {st.layout['name']} (scenario)",
                       [{"option": "withdraw and return to Dutton", "feasible": True,
                         "reason": f"via the {LABEL[side]}", "estimate_s": None},
                        {"option": "keep in service", "feasible": False, "reason": "fault (scenario)", "estimate_s": None}],
                       "withdraw and return to Dutton")
        train.trip.decisions.append(d)
        train.withdraw_decision = d
        return pseudo

    def return_route(self, train):
        """(route, track, position) for a returning train: the shop first if faulty."""
        side = train.next_trip.side if train.next_trip is not None and getattr(train.next_trip, "nonrevenue", None) == "in" \
            else train.trip.side
        tracks = (self.shop + self.storage) if getattr(train, "withdrawn", False) else (self.storage + self.shop)
        for track in tracks:
            if side not in track["leads"]:
                continue
            k = self.free_position(track)
            if k is not None:
                return ROUTES[f"yard-in:{side}:{track['way']}"], track, k
        return None, None, None

    def return_ok(self, t, train):
        """Before leaving the terminal: is the whole return route usable now? (reason or None)"""
        route, track, k = self.return_route(train)
        if route is None:
            return "no free storage position", None
        held = self.blocker(route, t, train.vehicle_id)
        if held is not None:
            return (f"{held[0].replace(':', ' ')} held by {held[1]}" + (f" until {ro.fmt(held[2])}" if held[2] else ""),
                    held[2])
        return None, None

    def on_line_stop(self, t, train, trip, index):
        """A returning train stands at the platform where its yard route starts."""
        ops = self.ops
        route, track, k = self.return_route(train)
        nf = ops.stop_index("north", "Northfield Station")
        key = ("north", nf)
        why = None
        if route is None:
            why = "no free storage position"
        else:
            held = self.blocker(route, t, train.vehicle_id)
            if held is not None:
                why = f"{held[0].replace(':', ' ')} held by {held[1]}"
                if held[2] is not None:
                    ops.push(held[2], 2, "line_depart", train=train, trip=trip, index=index)
        if why is None:
            holder = ops.platform_at.get(key)
            if holder is not None and holder[0] is not train:
                why = f"northbound Northfield block occupied by {holder[0].vehicle_id}"
                if ("yard_in", train, trip, index) not in ops.platform_waiters.get(key, []):
                    ops.platform_waiters.setdefault(key, []).append(("yard_in", train, trip, index))
        stand = train.segments[-1]
        if why is not None:
            stand.t1 = math.inf
            stand.note = f"waiting to enter Dutton: {why}"
            ops.hold_platform(trip.direction, index, train, math.inf)
            if getattr(train, "logged_in", None) != why:
                train.logged_in = why
                d = ops.decide(t, "station", "yard", train, trip, "Return to Dutton requested",
                               [{"option": f"enter via the {LABEL[trip.side]}", "feasible": False, "reason": why,
                                 "estimate_s": None},
                                {"option": "wait at the platform", "feasible": True, "reason": "", "estimate_s": None}],
                               "wait at the platform")
                trip.decisions.append(d)
            return
        if stand.kind == "stand" and (math.isinf(stand.t1) or stand.t1 < t):
            stand.t1 = t
        start = max(t, stand.t0)
        ops.release_platform(start, trip.direction, index, train)
        s1 = self.in_s(route, k)
        prof = ro.move_profile("network", route["id"], 0.0, s1)
        self.reserve(route, train.vehicle_id, start, prof, 0.0, end_m=s1)
        lead_m = route["length_m"] - route["yard_m"]
        ops.hold_platform("north", nf, train, math.inf)
        ops.push(start + prof.time_at(min(s1, lead_m + self.L + YARD["modelled"]["foul_m"])), 1, "yard_release_block",
                 direction="north", index=nf, train=train)
        self.occupied[(track["id"], k)] = train.vehicle_id
        train.yard_pos = (track["id"], k)
        end = start + prof.duration
        self.set_state(start, train, "returning", f"via the {LABEL[trip.side]} to track {track['way']}")
        train.add(ro.Segment("move", start, end, train.lead, trip.train_id, "network", route["id"], 0.0, s1,
                             note="returning to Dutton"))
        final = "unavailable" if getattr(train, "withdrawn", False) else "stored"
        train.add(ro.Segment("stand", end, math.inf, train.lead, trip.train_id, "network", route["id"], s1, s1,
                             note=f"{final} at Dutton: track {track['way']} position {k + 1}"))
        ops.push(end, 2, "yard_arrived", train=train, state=final, track=track, k=k)
        d = ops.decide(start, "station", "yard", train, trip, "Return to Dutton requested",
                       [{"option": f"enter via the {LABEL[trip.side]}", "feasible": True,
                         "reason": f"{route['via']}; track {track['way']} position {k + 1} free", "estimate_s": None}],
                       f"enter via the {LABEL[trip.side]}")
        d.outcome = f"stopped on track {track['way']} at {ro.fmt(end)}"
        trip.decisions.append(d)
        if getattr(train, "withdraw_decision", None) is not None:
            train.withdraw_decision.outcome = f"left the line at {ro.fmt(start)}; in the yard {ro.fmt(end)}"
        self.log.append({"vehicle_id": train.vehicle_id, "kind": "in", "route": route["id"], "t0": start, "t1": end,
                         "track": track["id"], "position": k + 1})
        train.trip = trip
        train.next_trip = None
        if train not in self.trains:
            self.trains.append(train)

    def on_arrived(self, t, train, state, track, k):
        self.set_state(t, train, state, f"track {track['way']} position {k + 1}")

    def inventory(self):
        """Fleet by state, at the end of the run, and every train's state history."""
        return [{"vehicle_id": tr.vehicle_id, "state": getattr(tr, "state", "in service"),
                 "log": getattr(tr, "state_log", [])} for tr in self.ops.trains]
