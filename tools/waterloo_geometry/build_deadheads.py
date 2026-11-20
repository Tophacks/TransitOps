"""Out-of-service routes for spare buses: from each GRT garage to every relief
point where a spare can take over service, from those points back to each
garage, and between relief points (a bus reassigned from one line to another).

Usage:
    python3 -I build_deadheads.py [--tiles DIR] [--check]

Relief points:
- every ION station stop on Route 7's two main variants (lines.json), served
  heading the way the line goes there;
- Central Station (GRT 1901), where the corridor buses enter service
  (simulation_engine.py releases every corridor bus towards Central).

Roads: the same directed graph and rules as the garage pull-outs
(build_garages.Roads): one-way streets and roundabouts, closed roads skipped
unless open to buses, every turn restriction, U-turns only at dead ends,
service roads only inside a garage site or near a service stop, speeds from
maxspeed or a default by road class, plus 6 s per turn sharper than 45
degrees (modelled). Sources: sources/garages-osm.json (#18) and
sources/deadheads-osm.json (OSM API map calls along Route 7, the corridor
and between the garages and their nearest relief points, 2026-11-20,
filtered to drivable roads).

Map data © OpenStreetMap contributors (ODbL). Stops from GRT GTFS: contains
information provided by the Regional Municipality of Waterloo under the
Region's Open Data Licence.
"""
import argparse
import heapq
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_garages as bg  # noqa: E402
from geo import cumulative, dist, heading, to_latlon, to_xy, turn  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = HERE / "sources" / "deadheads-osm.json"
OUTPUTS = [ROOT / "backend" / "deadheads.json"]
SERVICE_NEAR_M = 250.0
CORRIDOR_JOIN = "1901"


def filter_tiles(tiles, relief):
    d = bg.filter_tiles(tiles)
    N = {e["id"]: e for e in d["elements"] if e["type"] == "node"}

    def keep(e):
        t = e["tags"]
        if t.get("highway") != "service":
            return True
        if t.get("bus") in ("yes", "designated") or t.get("psv") in ("yes", "designated"):
            return True
        return any(dist(to_xy(N[n]["lat"], N[n]["lon"]), r) < SERVICE_NEAR_M for n in e["nodes"] if n in N for r in relief)
    ways = [e for e in d["elements"] if e["type"] == "way" and keep(e)]
    for w in ways:
        w["tags"] = {k: v for k, v in w["tags"].items() if k in (
            "highway", "name", "oneway", "junction", "access", "bus", "psv", "motor_vehicle", "service", "maxspeed")}
    wid = {w["id"] for w in ways}
    rels = [e for e in d["elements"] if e["type"] == "relation" and any(m["type"] == "way" and m["ref"] in wid
                                                                        for m in e["members"])]
    used = {n for w in ways for n in w["nodes"]} | {m["ref"] for r in rels for m in r["members"] if m["type"] == "node"}
    nodes = [{"type": "node", "id": n, "lat": round(N[n]["lat"], 7), "lon": round(N[n]["lon"], 7)}
             for n in sorted(used) if n in N]
    d["elements"] = nodes + ways + rels
    d["generator"] = "OSM API 0.6 map calls (2026-11-20), filtered to drivable roads"
    return d


def relief_points():
    lines = json.loads((ROOT / "backend" / "lines.json").read_text())
    out = {}
    for p in lines["routes"]["7"]["patterns"]:
        if p["trips"] < 50:
            continue
        xy = [to_xy(la, lo) for lo, la, _ in p["path"]]
        cum = [s for _, _, s in p["path"]]
        for st in p["stops"]:
            sid = st["stop"]
            s = lines["stops"][sid]
            if "ion" not in s and sid != CORRIDOR_JOIN:
                continue
            i = max(0, min(len(cum) - 2, next((j for j in range(len(cum) - 1) if cum[j + 1] >= st["s"] + 0.5), len(cum) - 2)))
            out[sid] = {"stop": sid, "name": s["name"], "lon": s["lon"], "lat": s["lat"],
                        "bearing": round(heading(xy[i], xy[i + 1]), 1), "route": "7", "direction": p["direction"]}
    out[CORRIDOR_JOIN]["corridor"] = "CENTRAL"
    return out


def load_roads(relief):
    els = {}
    for src in (bg.SOURCE, SOURCE):
        for e in json.loads(src.read_text())["elements"]:
            els[(e["type"], e["id"])] = e
    N = {e["id"]: e for (k, _), e in els.items() if k == "node"}
    W = {e["id"]: e for (k, _), e in els.items() if k == "way"}
    R = [e for (k, _), e in els.items() if k == "relation"]
    return N, W, bg.Roads(N, W, R, [g["site"] for g in bg.GARAGES], [to_xy(r["lat"], r["lon"]) for r in relief.values()])


def search(roads, sources, targets):
    """One multi-target search under the garage router's rules: the fastest legal
    path from `sources` to each target edge set. Returns {key: (nodes, ways, t)}."""
    want = {}
    for key, edges in targets.items():
        for e in edges:
            want.setdefault(e, []).append(key)
    left = set(targets)
    found = {}
    best, pred = {}, {}
    pq = []
    seq = 0
    for n in sources:
        st = (n, None, None)
        best[st] = 0.0
        heapq.heappush(pq, (0.0, seq, st))
    while pq and left:
        t, _, st = heapq.heappop(pq)
        if t > best.get(st, math.inf) + 1e-9:
            continue
        u, prev_n, prev_w = st
        if prev_n is not None:
            for key in want.get((prev_n, u), ()):
                if key in left:
                    left.discard(key)
                    nodes, ways, cur = [u], [], st
                    while cur in pred:
                        cur, w = pred[cur]
                        nodes.append(cur[0])
                        ways.append(w)
                    found[key] = (nodes[::-1], ways[::-1], t)
        for v, wid, d, speed in roads.adj.get(u, []):
            if not roads.allowed(prev_w, u, wid, prev_n, v):
                continue
            cost = d / (speed / 3.6)
            if prev_n is not None and abs(turn(heading(roads.xy[prev_n], roads.xy[u]), heading(roads.xy[u], roads.xy[v]))) > 45:
                cost += bg.TURN_S
            ns = (v, u, wid)
            if t + cost < best.get(ns, math.inf) - 1e-9:
                best[ns] = t + cost
                pred[ns] = (st, wid)
                seq += 1
                heapq.heappush(pq, (t + cost, seq, ns))
    return found


def encode(roads, W, nodes, ways):
    pts = [roads.xy[n] for n in nodes]
    times = [0.0]
    for a, b, wid in zip(nodes, nodes[1:], ways):
        spd = next(sp for v, w2, _, sp in roads.adj[a] if v == b and w2 == wid)
        times.append(times[-1] + dist(roads.xy[a], roads.xy[b]) / (spd / 3.6))
    cum = cumulative(pts)
    out = []
    last = None
    for i, (p, s, t) in enumerate(zip(pts, cum, times)):
        if last is not None and i < len(pts) - 1 and dist(p, last) < 15.0:
            continue
        lat, lon = to_latlon(*p)
        out.append([round(lon, 6), round(lat, 6), round(s, 1), round(t, 1)])
        last = p
    names = []
    for wid in ways:
        nm = W[wid]["tags"].get("name") or ("garage driveway" if wid in roads.site_of_way else W[wid]["tags"]["highway"])
        if not names or names[-1] != nm:
            names.append(nm)
    return {"length_m": round(cum[-1], 1), "time_s": round(times[-1], 1), "via": names, "path": out}


def build():
    relief = relief_points()
    N, W, roads = load_roads(relief)
    garages = {g["id"]: g for g in bg.GARAGES}
    targets = {f"stop:{sid}": roads.edges_near(to_xy(r["lat"], r["lon"]), r["bearing"]) for sid, r in relief.items()}
    for k, v in targets.items():
        if not v:
            raise SystemExit(f"no road edge near {k}")
    into = {f"garage:{g}": [(a, b) for a, lst in roads.adj.items() for b, _, _, _ in lst if b == garages[g]["park"]]
            for g in garages}
    routes, report = {}, []
    for gid, g in garages.items():
        found = search(roads, [g["park"]], targets)
        for key, (nodes, ways, _) in found.items():
            routes[f"garage:{gid}|{key}"] = encode(roads, W, nodes, ways)
        missing = set(targets) - set(found)
        if missing:
            raise SystemExit(f"{gid}: no legal route to {sorted(missing)}")
    for sid, r in relief.items():
        start = [b for _, b in targets[f"stop:{sid}"]]
        others = {k: v for k, v in targets.items() if k != f"stop:{sid}"}
        found = search(roads, start, {**others, **into})
        for key, (nodes, ways, _) in found.items():
            routes[f"stop:{sid}|{key}"] = encode(roads, W, nodes, ways)
        missing = (set(others) | set(into)) - set(found)
        if missing:
            raise SystemExit(f"stop {sid}: no legal route to {sorted(missing)}")
    for gid in garages:
        times = sorted((routes[f"garage:{gid}|stop:{s}"]["time_s"], s) for s in relief)
        report.append(f"{gid}: to {len(relief)} relief points, {times[0][0] / 60:.1f}-{times[-1][0] / 60:.1f} min "
                      f"(nearest {relief[times[0][1]]['name']})")
    report.append(f"{len(routes)} routes")
    return {"attribution": "Roads © OpenStreetMap contributors (ODbL); stops from GRT GTFS (Region of Waterloo Open Data "
                           "Licence).",
            "rules": "build_garages.Roads: one-way, access, turn restrictions, no U-turns except at dead ends; maxspeed or "
                     "a default by road class, plus 6 s per turn over 45 degrees (modelled)",
            "garages": {gid: {"name": g["name"], "park": g["park"]} for gid, g in garages.items()},
            "points": relief, "routes": routes}, report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tiles", help="directory of OSM API map tiles to filter into the source file")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.tiles:
        relief = [to_xy(r["lat"], r["lon"]) for r in relief_points().values()]
        SOURCE.write_text(json.dumps(filter_tiles(args.tiles, relief), separators=(",", ":")) + "\n")
        print("wrote", SOURCE.relative_to(ROOT))
    out, report = build()
    for line in report:
        print(line)
    text = json.dumps(out, separators=(",", ":")) + "\n"
    if args.check:
        for p in OUTPUTS:
            if p.read_text() != text:
                raise SystemExit(f"{p} is stale")
        return
    for p in OUTPUTS:
        p.write_text(text)
    print("wrote", ", ".join(f"{p.relative_to(ROOT)} ({len(text) // 1024} KiB)" for p in OUTPUTS))


if __name__ == "__main__":
    main()
