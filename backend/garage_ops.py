"""GRT bus garages: out-of-service pull-outs and returns on real roads.

Three garages (bus_garages.json, from OSM, the Region's 2026 imagery and GRT
GTFS): Northfield Drive (Waterloo), Strasburg Road / Chandler Drive
(Kitchener) and Conestoga Boulevard (Cambridge). In the simulated hour
(minute 0 = 07:30 on a GTFS Wednesday) each garage sends one bus out to the
first stop of its block and receives one back from the last stop of another.

Each bus keeps its fleet ID (fleet.py) from the garage to the stop and through its
trip; the route 13 bus returning to Northfield is the same vehicle the UW
Station simulation moved through Transit Plaza Road. Each garage keeps an
inventory: the number of buses there (modelled) and which of the scenario's
buses are in.

Dispatch pulls a bus out early enough to reach its first stop LAYOVER_S before
departure, and chooses the driveway: the same move is planned through each of
the garage's driveways (the others closed) and the fastest legal one wins.
Returning buses do the same coming in.

Modelled, not observed: which garage runs which block (the nearest), the
inventories, the layovers, road speeds where none is mapped, and the turn
penalty. See docs/bus-garages.md.
"""
import json
from pathlib import Path

GARAGES = json.loads((Path(__file__).resolve().parent / "bus_garages.json").read_text())
LAYOVER_S = 120.0          # a pulled-out bus reaches its first stop this long before departure
RETURN_LAYOVER_S = 60.0    # a bus finishing its block waits this long before deadheading in


def clock(t):
    s = int(round(t))
    sign = "-" if s < 0 else ""
    s = abs(s)
    return f"{sign}{s // 60:02d}:{s % 60:02d}"


def deadhead_samples(dh, t0):
    """[t, s] at every road node: constant speed on each edge (bus_garages.json)."""
    out = [[round(t0, 1), 0.0]]
    t = t0
    for (_, _, s), dt in zip(dh["path"][1:], dh["speeds"]):
        t += dt
        out.append([round(t, 1), s])
    return out


def trip_samples(trip, t_from=None, t_to=None):
    """[t, s] along the trip shape: at each stop's arrival and departure."""
    out = []
    for st, m in zip(trip["stops"], trip["stop_m"]):
        for t in (st["arrive_s"], st["depart_s"]):
            if not out or t > out[-1][0]:
                out.append([float(t), m])
    if t_to is not None:
        out = [x for x in out if x[0] <= t_to] + ([[t_to, _s_at(out, t_to)]] if out and out[-1][0] > t_to else [])
    return out


def _s_at(samples, t):
    for (t0, s0), (t1, s1) in zip(samples, samples[1:]):
        if t0 <= t <= t1:
            return s0 + (s1 - s0) * ((t - t0) / (t1 - t0) if t1 > t0 else 0.0)
    return samples[-1][1]


class Garages:
    def __init__(self, engine):
        self.engine = engine
        self.vehicles = []
        self.inventory = {}
        self.decisions = engine.decisions

    def run(self):
        uw = {a.id: a for a in self.engine.uw.agents} if self.engine.uw is not None else {}
        for g in GARAGES["garages"]:
            gid = g["id"]
            out, inn = g["out"], g["in"]
            fleet = getattr(self.engine, "fleet", None)
            out_id = (fleet.vehicle_for_block(out["block"]) if fleet else None) or f"GRT-{out['block']}"
            in_id = (fleet.vehicle_for_block(inn["block"]) if fleet else None) or f"GRT-{inn['block']}"
            count = g["inventory"]
            log = [{"t": 0.0, "count": count, "present": [out_id], "event": "07:30: buses at the garage (modelled)"}]

            # --- pull-out ---
            dh = out["deadhead"]
            dep = out["service_stop"]["depart_s"]
            t_leave = dep - LAYOVER_S - dh["time_s"]
            arrive = t_leave + dh["time_s"]
            segs = [
                {"kind": "garage", "t0": None, "t1": t_leave, "at": g["park"]},
                {"kind": "deadhead", "ref": f"{gid}:out", "t0": t_leave, "t1": arrive, "samples": deadhead_samples(dh, t_leave)},
                {"kind": "layover", "ref": f"{gid}:out:trip", "t0": arrive, "t1": dep, "samples": [[arrive, out["trip"]["stop_m"][0]], [dep, out["trip"]["stop_m"][0]]]},
                {"kind": "service", "ref": f"{gid}:out:trip", "t0": dep, "t1": out["trip"]["stops"][-1]["arrive_s"],
                 "samples": trip_samples(out["trip"])},
            ]
            states = [{"t": 0.0, "state": "in garage"}, {"t": t_leave, "state": "pulling out"},
                      {"t": arrive, "state": "layover"}, {"t": dep, "state": "in service"}]
            self.vehicles.append({"id": out_id, "garage": gid, "kind": "out", "block": out["block"], "route": out["route"],
                                  "segments": segs, "states": states})
            count -= 1
            log.append({"t": t_leave, "count": count, "present": [], "event": f"{out_id} pulled out"})
            self._decide(t_leave, g, out_id, out, "out", arrive, dep)

            # --- return ---
            dh = inn["deadhead"]
            last = inn["service_stop"]["arrive_s"]
            agent = uw.get(in_id) if dh.get("from") else None
            if agent is not None and agent.exit_s is not None:
                t_go, t_service_end = agent.exit_s, agent.t_enter
            else:
                t_go, t_service_end = last + RETURN_LAYOVER_S, last
            back = t_go + dh["time_s"]
            segs = [{"kind": "service", "ref": f"{gid}:in:trip", "t0": inn["trip"]["stops"][0]["depart_s"],
                     "t1": t_service_end, "samples": trip_samples(inn["trip"], t_to=t_service_end)}]
            if agent is None:
                segs.append({"kind": "layover", "ref": f"{gid}:in:trip", "t0": last, "t1": t_go,
                             "samples": [[last, inn["trip"]["stop_m"][-1]], [t_go, inn["trip"]["stop_m"][-1]]]})
            segs += [
                {"kind": "deadhead", "ref": f"{gid}:in", "t0": t_go, "t1": back, "samples": deadhead_samples(dh, t_go)},
                {"kind": "garage", "t0": back, "t1": None, "at": g["park"]},
            ]
            states = [{"t": inn["trip"]["stops"][0]["depart_s"], "state": "in service"},
                      {"t": t_go, "state": "returning"}, {"t": back, "state": "in garage"}]
            self.vehicles.append({"id": in_id, "garage": gid, "kind": "in", "block": inn["block"], "route": inn["route"],
                                  "segments": segs, "states": states,
                                  "station": "UW Station (uw_station.py)" if agent is not None else None})
            count += 1
            log.append({"t": back, "count": count, "present": [in_id], "event": f"{in_id} returned"})
            self._decide(t_go, g, in_id, inn, "in", back, last)
            log.sort(key=lambda x: x["t"])
            self.inventory[gid] = log
        return self

    def _decide(self, t, g, vid, spec, kind, done, service_t):
        dh = spec["deadhead"]
        st = spec["service_stop"]
        opts = []
        for alt in sorted(dh["alternatives"], key=lambda a: (a["time_s"] is None, a["time_s"] or 0, a["driveway"])):
            name = f"{'leave' if kind == 'out' else 'enter'} by the {alt['road']} driveway (node {alt['driveway']})"
            opts.append({"option": name, "feasible": alt["feasible"],
                         "reason": (f"{alt['length_m'] / 1000:.1f} km, {alt['time_s'] / 60:.1f} min" if alt["feasible"]
                                    else alt["reason"]),
                         "estimate_s": alt["time_s"]})
        best = next(o for o in opts if o["feasible"])
        if kind == "out":
            trigger = (f"Block {spec['block']} (route {spec['route']} to {spec['headsign']}) departs {st['name']} "
                       f"at {clock(st['depart_s'])}; {vid} ready at the {g['name']}")
            outcome = f"pulled out {clock(t)}; at {st['name']} {clock(done)}; in service {clock(service_t)}"
        else:
            trigger = (f"{vid} finished block {spec['block']} (route {spec['route']}) at {st['name']} "
                       f"{clock(service_t)}; returns to the {g['name']}")
            outcome = f"left {clock(t)}; in the garage {clock(done)}"
        self.decisions.append({
            "id": f"G{len(self.decisions) + 1:03d}", "t": round(t, 1), "minute": int(t // 60), "level": "dispatch",
            "terminal": g["id"], "vehicle_id": vid, "trip_id": spec["trip_id"], "trigger": trigger,
            "options": opts, "chosen": best["option"], "outcome": outcome, "location": g["park"],
        })

    def plans(self):
        return {"vehicles": self.vehicles, "inventory": self.inventory}
