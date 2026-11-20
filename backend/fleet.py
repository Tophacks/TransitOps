"""GRT's regional fleet: every modelled vehicle, where it is kept, what it is
doing, and the spares dispatch can send.

Inventory (grt.ca, About GRT: "more than 300 buses, 15 light rail vehicles
and 35 specialized MobilityPLUS vehicles"):

- conventional buses: `conventional` (300 by default; the exact total is a
  setting), split between the three garages in modelled shares and numbered
  B101, B102, ... garage by garage (modelled fleet numbers, not GRT's);
- MobilityPLUS: 35 vehicles, M01-M35, kept apart: they never cover
  fixed-route service;
- ION: the 15 light rail vehicles of rail_ops.py (Dutton yard), by reference.

Every GTFS block that runs on the modelled day gets one bus for the day from
the garage nearest its first stop (modelled, as for the garage pull-outs),
or the next nearest when that garage has none left. The corridor run's buses
(the request's fleet_size) are the first of Strasburg's buses, staged for
the corridor (B101, B102, ...). A modelled share of each garage is in
maintenance (seeded). The rest are ready spares; a bus held for a block that
starts within SPARE_HORIZON_S is not offered as a spare.

Only the buses active in the simulation drive (Route 7 blocks, the
corridor buses, UW Station's GTFS buses, spares sent out); the others'
status comes from their block's schedule and is labelled so.

Spares: dispatch compares the first ready spare of each garage, with the
driver's sign-on and checks (PREP_S) plus the out-of-service route from that
garage to the relief point (deadheads.json, real roads), and sends the one
that arrives first. Every change of state is logged per garage, and the
counts reconcile: buses in the garage plus buses out equals its allocation.
"""
import json
import math
from pathlib import Path

from drivers import Driver

HERE = Path(__file__).resolve().parent
DEADHEADS = json.loads((HERE / "deadheads.json").read_text())
LINES = json.loads((HERE / "lines.json").read_text())
GARAGE_SITES = json.loads((HERE / "bus_garages.json").read_text())
HOUR0 = 7 * 3600 + 30 * 60

# Modelled values (docs/regional-fleet.md).
GARAGE_ORDER = ("strasburg", "northfield", "conestoga-blvd")
GARAGE_SHARE = {"strasburg": 0.45, "northfield": 0.35, "conestoga-blvd": 0.20}
MAINTENANCE_SHARE = 0.08
MOBILITY_PLUS = 35
ION_VEHICLES = 15
PREP_S = 300.0              # sign-on, walk-round check and leaving the garage
SPARE_HORIZON_S = 3 * 3600  # a bus whose block starts sooner than this is held for it
DEADHEAD_KMH = 30.0         # for blocks not simulated: their pull-out estimated from distance
CORRIDOR_STAGED = "strasburg"


def clock(t):
    s = int(round(HOUR0 + t))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}"


def _dist_m(a, b):
    k = 111320 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[1] - b[1]) * k, (a[0] - b[0]) * 111200)


def _unit(*parts):
    import hashlib
    h = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


ROAD_CLASSES = {"service", "garage driveway", "motorway_link", "trunk_link", "primary_link", "secondary_link",
                "tertiary_link", "secondary", "tertiary", "primary", "residential", "unclassified", "motorway", "trunk"}


def _via(names):
    """The named roads of a route, each once, in order."""
    out = []
    for n in names:
        if n and n not in ROAD_CLASSES and n not in out:
            out.append(n)
    return ", ".join(out[:6]) + (", ..." if len(out) > 6 else "")


class Vehicle:
    def __init__(self, vid, garage, kind="conventional"):
        self.id, self.garage, self.kind = vid, garage, kind
        self.block = None           # GTFS block worked today
        self.role = None            # "block", "corridor", "spare", "maintenance", "mobilityplus"
        self.driver = None          # operator id
        self.history = []           # [t, state, detail]
        self.simulated = False
        self.deadhead = None        # an out-of-service move: {key, t0, t1, path}

    def state_at(self, t):
        cur = None
        for row in self.history:
            if row[0] <= t + 1e-9:
                cur = row
            else:
                break
        return cur

    def set(self, t, state, detail=""):
        self.history.append([round(t, 1), state, detail])
        self.history.sort(key=lambda r: r[0])


class Fleet:
    def __init__(self, conventional=300, seed=17, corridor_buses=(), duration_s=3600.0, simulated_blocks=()):
        self.seed = seed
        self.conventional = int(conventional)
        self.duration_s = duration_s
        self.garages = {g["id"]: {"name": g["name"], "address": g["address"], "park": g["park"]}
                        for g in GARAGE_SITES["garages"]}
        self.vehicles = {}
        self.decisions = []
        self.events = []            # [t, garage, vehicle, state, detail]
        self.allocation = {}
        n = 101
        left = self.conventional
        for k, gid in enumerate(GARAGE_ORDER):
            count = left if k == len(GARAGE_ORDER) - 1 else round(self.conventional * GARAGE_SHARE[gid])
            left -= count
            self.allocation[gid] = count
            for _ in range(count):
                v = Vehicle(f"B{n}", gid)
                self.vehicles[v.id] = v
                n += 1
        for i in range(MOBILITY_PLUS):
            v = Vehicle(f"M{i + 1:02d}", None, "mobilityplus")
            v.role = "mobilityplus"
            v.set(-1e9, "MobilityPLUS service", "specialized door-to-door service (not simulated)")
            self.vehicles[v.id] = v
        self.block_vehicle = {}
        self.shortfall = []
        self._assign(list(corridor_buses), set(simulated_blocks))

    # --- the day's assignments -----------------------------------------------------------
    def by_garage(self, gid):
        return [v for v in self.vehicles.values() if v.garage == gid]

    def _assign(self, corridor_buses, simulated):
        # Corridor buses: Strasburg's first buses, staged for the corridor run.
        pool = {gid: [v for v in self.by_garage(gid)] for gid in GARAGE_ORDER}
        for vid in corridor_buses:
            v = self.vehicles.get(vid)
            if v is None or v.garage != CORRIDOR_STAGED:
                # A corridor bus named outside the inventory (tests, old scenarios): kept
                # apart, so the inventory's counts are not changed.
                v = Vehicle(vid, None, "scenario")
                v.role, v.simulated, v.driver = "corridor", True, f"OP-{vid}"
                v.set(-1e9, "corridor run", "a scenario vehicle outside the modelled inventory")
                self.vehicles[vid] = v
                continue
            v.role = "corridor"
            v.simulated = True
            v.driver = f"OP{vid[1:]}"
            v.set(-1e9, "corridor run", "staged at Victoria / Joseph for the corridor run")
            pool[CORRIDOR_STAGED].remove(v)
        # Maintenance: a seeded share of each garage's remaining buses.
        for gid in GARAGE_ORDER:
            k = round(self.allocation[gid] * MAINTENANCE_SHARE)
            ranked = sorted(pool[gid], key=lambda v: _unit(self.seed, "maintenance", v.id))
            for v in ranked[:k]:
                v.role = "maintenance"
                v.set(-1e9, "maintenance", "in the shop today (modelled)")
                pool[gid].remove(v)
        # Blocks, in start order, to the nearest garage with a bus left.
        blocks = sorted(LINES["day_blocks"].items(), key=lambda kv: kv[1]["start"])
        for bid, b in blocks:
            first = (b["first"][1], b["first"][2])
            ranked = sorted(GARAGE_ORDER, key=lambda g: _dist_m(first, (self.garages[g]["park"][1],
                                                                          self.garages[g]["park"][0])))
            gid = next((g for g in ranked if pool[g]), None)
            if gid is None:
                self.shortfall.append(bid)
                continue
            v = pool[gid].pop(0)
            v.role, v.block = "block", bid
            v.driver = f"OP{v.id[1:]}"
            v.simulated = bid in simulated
            self.block_vehicle[bid] = v.id
            v.overflow = gid != ranked[0]
            self._block_states(v, b)
        for gid in GARAGE_ORDER:
            for v in pool[gid]:
                v.role = "spare"
                v.set(-1e9, "ready spare", f"at the {self.garages[gid]['name']}")

    def _block_states(self, v, b):
        """Schedule-based states (refined by the simulation where the block is driven)."""
        start, end = b["start"] - HOUR0, b["end"] - HOUR0
        g = self.garages[v.garage]["park"]
        out = _dist_m((b["first"][1], b["first"][2]), (g[1], g[0])) * 1.4 / (DEADHEAD_KMH / 3.6)
        back = _dist_m((b["last"][1], b["last"][2]), (g[1], g[0])) * 1.4 / (DEADHEAD_KMH / 3.6)
        tag = "" if v.simulated else " (from the schedule; not simulated)"
        v.set(-1e9, "held for its block", f"block {v.block} starts {clock(start)} at stop {b['first'][0]}")
        v.set(start - out - 120, "pulling out", f"to stop {b['first'][0]} for block {v.block}{tag}")
        v.set(start, "in service", f"block {v.block}, routes {', '.join(b['routes'])}{tag}")
        v.set(end, "returning", f"to the {self.garages[v.garage]['name']}{tag}")
        v.set(end + back, "in garage", f"block {v.block} done")

    # --- queries -------------------------------------------------------------------------
    def vehicle_for_block(self, block):
        return self.block_vehicle.get(block)

    def driver(self, vid, seed=None):
        v = self.vehicles[vid]
        return Driver(v.driver or f"OP{vid[1:]}", self.seed if seed is None else seed)

    def ready_spares(self, gid, t):
        out = []
        for v in self.by_garage(gid):
            st = v.state_at(t)
            if st is None:
                continue
            if st[1] == "ready spare":
                out.append(v)
            elif st[1] == "held for its block" and v.block is not None:
                start = LINES["day_blocks"][v.block]["start"] - HOUR0
                if start - t > SPARE_HORIZON_S:
                    out.append(v)
        return sorted(out, key=lambda v: int(v.id[1:]))

    def counts(self, t):
        """Per garage: buses in the garage and buses out, at t (they sum to its allocation)."""
        out = {}
        for gid in GARAGE_ORDER:
            inside = outside = 0
            states = {}
            for v in self.by_garage(gid):
                st = v.state_at(t)
                name = st[1] if st else "?"
                states[name] = states.get(name, 0) + 1
                if name in ("ready spare", "maintenance", "held for its block", "in garage", "preparing"):
                    inside += 1
                else:
                    outside += 1
            out[gid] = {"allocation": self.allocation[gid], "in_garage": inside, "out": outside, "states": states}
        return out

    # --- dispatch ------------------------------------------------------------------------
    def deadhead(self, frm, to):
        return DEADHEADS["routes"].get(f"{frm}|{to}")

    def request_spare(self, t, stop, reason, for_label, decide=None, only=None):
        """Choose and send a ready spare to `stop` (a relief point). Returns the
        plan (vehicle, garage, leave, arrive, route) or None."""
        key = f"stop:{stop}"
        options = []
        for gid in GARAGE_ORDER:
            spares = self.ready_spares(gid, t)
            if only is not None:
                spares = [v for v in spares if v.id == only]
            dh = self.deadhead(f"garage:{gid}", key)
            name = self.garages[gid]["name"]
            if not spares:
                options.append({"option": f"a spare from the {name}", "feasible": False,
                                "reason": "no ready spare there" if only is None else f"{only} is not a ready spare there",
                                "estimate_s": None})
                continue
            if dh is None:
                options.append({"option": f"{spares[0].id} from the {name}", "feasible": False,
                                "reason": "no legal road route to the relief point", "estimate_s": None})
                continue
            eta = t + PREP_S + dh["time_s"]
            options.append({"option": f"{spares[0].id} from the {name}", "feasible": True,
                            "reason": f"{len(spares)} ready; {PREP_S / 60:.0f} min to sign on and leave, then "
                                      f"{dh['length_m'] / 1000:.1f} km out of service via "
                                      f"{_via(dh['via'])} "
                                      f"({dh['time_s'] / 60:.1f} min)",
                            "estimate_s": round(eta, 1), "_v": spares[0], "_g": gid, "_dh": dh})
        feasible = [o for o in options if o["feasible"]]
        best = min(feasible, key=lambda o: o["estimate_s"]) if feasible else None
        pub = [{k: v for k, v in o.items() if not k.startswith("_")} for o in options]
        name = LINES["stops"].get(stop, {}).get("name", stop)
        chosen = best["option"] if best else "no spare available"
        d = {"id": f"F{len(self.decisions) + 1:03d}", "t": round(t, 1), "minute": int(t // 60), "level": "dispatch",
             "terminal": "fleet", "vehicle_id": best["_v"].id if best else None, "trip_id": None,
             "trigger": f"{reason}: a spare is needed at {name} ({stop}) for {for_label}",
             "options": pub, "chosen": chosen, "location": [LINES["stops"][stop]["lon"], LINES["stops"][stop]["lat"]]
             if stop in LINES["stops"] else None,
             "control": ("dispatch sends the spare that reaches the relief point first" if best else
                         "no garage has a ready spare with a legal route"),
             "outcome": (f"{best['_v'].id} leaves the {self.garages[best['_g']]['name']} at {clock(t + PREP_S)}, "
                         f"at {name} {clock(best['estimate_s'])}") if best else "uncovered"}
        self.decisions.append(d)
        if decide is not None:
            decide(d)
        if best is None:
            return None
        plan = self.send(t, best["_v"], f"garage:{best['_g']}", stop, for_label, prep=True)
        plan["decision"] = d
        return plan

    def send(self, t, v, frm, stop, for_label, prep=False):
        """Move vehicle `v` out of service from `frm` (garage:<id> or stop:<id>) to `stop`."""
        key = f"stop:{stop}"
        dh = self.deadhead(frm, key)
        name = LINES["stops"].get(stop, {}).get("name", stop)
        if prep and v.block is not None and v.role == "block":
            self._move_block(t, v)
        leave = t + (PREP_S if prep else 0.0)
        arrive = leave + (dh["time_s"] if dh else 0.0)
        v.role = "spare sent" if prep else "reassigned"
        v.simulated = True
        v.driver = v.driver or f"OP{v.id[1:]}"
        v.history = [r for r in v.history if r[0] < t]
        if prep:
            v.set(t, "preparing", f"spare for {for_label}")
        v.set(leave, "deadhead", f"out of service to {name} ({stop})")
        v.set(arrive, "in service", for_label)
        v.deadhead = {"key": f"{frm}|{key}", "t0": round(leave, 1), "t1": round(arrive, 1),
                      "path": dh["path"] if dh else []}
        gid = v.garage
        if prep:
            self.events.append([t, gid, v.id, "preparing", for_label])
        self.events += [[leave, gid, v.id, "deadhead", name], [arrive, gid, v.id, "in service", for_label]]
        return {"vehicle": v.id, "garage": gid, "leave_s": leave, "arrive_s": arrive, "deadhead": v.deadhead}

    def _move_block(self, t, v):
        """`v` was held for a later block and goes out as a spare: the block passes to
        another ready spare of the same garage (or, failing that, any garage)."""
        block = v.block
        pool = [x for x in self.vehicles.values() if x.role == "spare" and x.kind == "conventional"]
        pool.sort(key=lambda x: (x.garage != v.garage, int(x.id[1:])))
        v.block = None
        if not pool:
            self.shortfall.append(block)
            self.events.append([t, v.garage, v.id, "block uncovered", f"block {block}: no bus left for it"])
            return
        w = pool[0]
        w.role, w.block, w.driver = "block", block, f"OP{w.id[1:]}"
        self.block_vehicle[block] = w.id
        w.history = []
        self._block_states(w, LINES["day_blocks"][block])
        self.events.append([t, w.garage, w.id, "block moved", f"block {block} from {v.id}, sent out as a spare"])

    def availability(self, t, vid):
        """(ok, reason) for sending `vid` as a spare now."""
        v = self.vehicles.get(vid)
        if v is None:
            return False, f"{vid} is not in the modelled fleet"
        if v.kind == "mobilityplus":
            return False, f"{vid} is a MobilityPLUS vehicle: specialized service, never fixed-route capacity"
        st = v.state_at(t)
        if v not in self.ready_spares(v.garage, t):
            why = st[1] if st else "unknown"
            if st and st[1] == "held for its block":
                why += f" ({st[2]}; within {SPARE_HORIZON_S / 3600:.0f} h)"
            return False, f"{vid} is not a ready spare: {why}"
        return True, f"{vid} is a ready spare at the {self.garages[v.garage]['name']}"

    def breakdown(self, t, vid, where):
        v = self.vehicles.get(vid)
        if v is None:
            return
        v.history = [r for r in v.history if r[0] < t]
        v.set(t, "broken down", where)
        v.role = "broken down"
        self.events.append([t, v.garage, vid, "broken down", where])

    # --- output --------------------------------------------------------------------------
    def summary(self, t_end=None):
        t_end = self.duration_s if t_end is None else t_end
        roles = {}
        for v in self.vehicles.values():
            if v.kind == "conventional":
                roles[v.role] = roles.get(v.role, 0) + 1
        return {"conventional": self.conventional, "mobilityplus": MOBILITY_PLUS, "ion": ION_VEHICLES,
                "allocation": self.allocation, "roles": roles, "shortfall_blocks": self.shortfall,
                "blocks_today": len(LINES["day_blocks"]),
                "counts_start": self.counts(0.0), "counts_end": self.counts(t_end),
                "modelled": {"garage_share": GARAGE_SHARE, "maintenance_share": MAINTENANCE_SHARE,
                             "prep_s": PREP_S, "spare_horizon_s": SPARE_HORIZON_S,
                             "block_garage": "nearest garage to the block's first stop, else the next nearest"}}

    def vehicles_out(self, ion_vehicles=()):
        out = []
        for v in self.vehicles.values():
            start = LINES["day_blocks"][v.block]["start"] - HOUR0 if v.block in LINES["day_blocks"] else None
            out.append({"id": v.id, "kind": v.kind, "garage": v.garage, "role": v.role, "block": v.block,
                        "block_start_s": start, "driver": v.driver, "simulated": v.simulated,
                        "history": [[round(a, 1) if a > -1e8 else None, b, c] for a, b, c in v.history],
                        "deadhead": v.deadhead})
        for vid in ion_vehicles:
            out.append({"id": vid, "kind": "ion", "garage": "dutton", "role": "rail", "block": None, "driver": None,
                        "simulated": True, "history": [[None, "ION", "rail_ops.py (Dutton yard)"]], "deadhead": None})
        return out
