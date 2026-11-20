"""Build the public roads at UW Station's exit that buses share with private
cars: Phillip Street southbound (the pilot's lanes, feat/uw-private-car-agents,
docs/uw-road-agents.md), the right turn the corridor bus takes onto it, the
legal left-turn alternative onto northbound Phillip Street, and the legal
route from there to the bus's next stop (Waterloo Public Square).

Usage:
    python3 -I build_uw_roads.py [--tiles DIR] [--check]

Sources: OpenStreetMap (ODbL) via the OSM API map call, filtered into
sources/uw-roads-osm.json (drivable roads and turn restrictions):
- Phillip Street ways 1451375463 (approach) and 1455699342 (receiving road),
  the University Avenue signal (node 114158636), the Transit Plaza Road
  exit (way 1040958964, node 298025663);
- the pilot's lane centrelines (offsets from those mapped centrelines; the
  pilot checked them on the Region's 2026 imagery);
- the roads between Phillip Street and Waterloo Public Square for the detour.

The detour is planned on a directed road graph with one-way streets, access
tags (closed roads skipped unless open to buses) and turn restrictions; no
U-turns except at dead ends. The bus's corridor stop (GRT 4048) is reached
on an edge heading the way the corridor bus serves it.

Map data © OpenStreetMap contributors (ODbL).
"""
import argparse
import glob
import heapq
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import cumulative, dist, heading, project, to_latlon, to_xy, turn  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = HERE / "sources" / "uw-roads-osm.json"
OUTPUTS = [ROOT / "backend" / "uw_roads.json", ROOT / "frontend" / "lib" / "geo" / "uw-roads.json"]
DRIVABLE = {"primary", "secondary", "tertiary", "unclassified", "residential", "primary_link", "secondary_link",
            "tertiary_link"}
SPEED = {"primary": 50, "secondary": 50, "tertiary": 40, "unclassified": 40, "residential": 40}
LANE_RIGHT = 2.1           # pilot: lane centre right of the mapped centreline
# Pilot lane centrelines (uwLanes.ts), from the OSM nodes of Phillip Street.
PHILLIP_EXIT = (-80.5384083, 43.4749181)
UNIVERSITY = (-80.5362661, 43.4720445)
APPROACH = [(-80.539195, 43.4759773), (-80.5390681, 43.4758065), (-80.5389636, 43.4756658),
            (-80.5387342, 43.4753569), (-80.5386145, 43.4751957), (-80.5385339, 43.4750871), PHILLIP_EXIT]
RECEIVING = [PHILLIP_EXIT, (-80.5383342, 43.4748205), (-80.5382195, 43.4746621), (-80.5378812, 43.4742049),
             (-80.5375501, 43.4737613), (-80.5371873, 43.4732755), (-80.5369505, 43.4729596),
             (-80.5366067, 43.4724957), UNIVERSITY, (-80.536159, 43.471902)]
PLAZA_END = (-80.5385069, 43.4748822)       # Transit Plaza Road just before its exit (pilot station[9])
WS_STOP = (-80.522231, 43.464398)           # GRT 4048 Waterloo Public Square (corridor stop)


def xy(p):
    return to_xy(p[1], p[0])


def encode(points):
    out = []
    for (x, y), s in zip(points, cumulative(points)):
        lat, lon = to_latlon(x, y)
        out.append([round(lon, 7), round(lat, 7), round(s, 2)])
    return out


def offset(points, right):
    """Shift a polyline `right` metres to the right of its direction."""
    out = []
    for i, p in enumerate(points):
        a = points[max(0, i - 1)]
        b = points[min(len(points) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy) or 1.0
        out.append((p[0] + dy / n * right, p[1] - dx / n * right))
    return out


def filter_tiles(tiles):
    nodes, ways, rels = {}, {}, {}
    for f in sorted(glob.glob(str(Path(tiles) / "*.json"))):
        data = json.loads(Path(f).read_text())
        for e in data.get("elements", []):
            {"node": nodes, "way": ways, "relation": rels}[e["type"]][e["id"]] = e
    keep_w = {wid: {"type": "way", "id": wid, "nodes": w["nodes"],
                    "tags": {k: v for k, v in w.get("tags", {}).items() if k in (
                        "highway", "name", "oneway", "junction", "access", "bus", "psv", "motor_vehicle", "maxspeed")}}
              for wid, w in ways.items() if w.get("tags", {}).get("highway") in DRIVABLE}
    keep_r = {rid: {"type": "relation", "id": rid, "members": r["members"], "tags": r["tags"]}
              for rid, r in rels.items() if r.get("tags", {}).get("type") == "restriction"
              and any(m["type"] == "way" and m["ref"] in keep_w for m in r["members"])}
    used = {n for w in keep_w.values() for n in w["nodes"]} | {
        m["ref"] for r in keep_r.values() for m in r["members"] if m["type"] == "node"}
    keep_n = [{"type": "node", "id": n, "lat": nodes[n]["lat"], "lon": nodes[n]["lon"]} for n in sorted(used) if n in nodes]
    return {"version": "0.6", "generator": "OSM API 0.6 map calls (2026-11-20), filtered",
            "copyright": "OpenStreetMap and contributors", "license": "http://opendatacommons.org/licenses/odbl/1-0/",
            "elements": keep_n + sorted(keep_w.values(), key=lambda x: x["id"]) + sorted(keep_r.values(), key=lambda x: x["id"])}


class Graph:
    def __init__(self, data):
        self.N = {e["id"]: e for e in data["elements"] if e["type"] == "node"}
        self.W = {e["id"]: e for e in data["elements"] if e["type"] == "way"}
        self.xy = {n: to_xy(e["lat"], e["lon"]) for n, e in self.N.items()}
        self.adj = {}
        for wid, w in self.W.items():
            t = w["tags"]
            if t.get("access") in ("no", "private") and t.get("bus") not in ("yes", "designated"):
                continue
            ow = t.get("oneway")
            fwd = ow != "-1"
            back = (ow not in ("yes", "true", "1", "-1") and t.get("junction") != "roundabout") or ow == "-1"
            spd = float(t["maxspeed"]) if t.get("maxspeed", "").isdigit() else SPEED.get(t["highway"].split("_")[0], 40)
            ns = [n for n in w["nodes"] if n in self.xy]
            for a, b in zip(ns, ns[1:]):
                d = dist(self.xy[a], self.xy[b])
                if fwd:
                    self.adj.setdefault(a, []).append((b, wid, d, spd))
                if back:
                    self.adj.setdefault(b, []).append((a, wid, d, spd))
        self.no, self.only = {}, {}
        for r in (e for e in data["elements"] if e["type"] == "relation"):
            frm = [m["ref"] for m in r["members"] if m["role"] == "from" and m["type"] == "way"]
            via = [m["ref"] for m in r["members"] if m["role"] == "via" and m["type"] == "node"]
            to = [m["ref"] for m in r["members"] if m["role"] == "to" and m["type"] == "way"]
            if frm and via and to:
                kind = r["tags"].get("restriction", "")
                if kind.startswith("no_"):
                    self.no.setdefault((frm[0], via[0]), set()).add(to[0])
                elif kind.startswith("only_"):
                    self.only[(frm[0], via[0])] = to[0]

    def edges_near(self, p, bearing, radius=30.0, first=None):
        out = []
        for a, lst in self.adj.items():
            for b, wid, d, _ in lst:
                if first is not None and wid not in first:
                    continue
                _, _, dd = project(p, self.xy[a], self.xy[b])
                if dd <= radius and abs(turn(bearing, heading(self.xy[a], self.xy[b]))) <= 50:
                    out.append((dd, a, b, wid))
        out.sort()
        return out

    def route(self, start_edge, targets):
        a0, b0, w0 = start_edge
        tgt = {(a, b) for _, a, b, _ in targets}
        pq = [(0.0, 0, b0, w0, a0, (a0, b0), (w0,))]
        best = {}
        seq = 0
        while pq:
            t, _, u, pw, pn, path, ws = heapq.heappop(pq)
            if (path[-2], path[-1]) in tgt:
                return list(path), list(ws), t
            if best.get((u, pw, pn), math.inf) <= t:
                continue
            best[(u, pw, pn)] = t
            for v, wid, d, spd in self.adj.get(u, []):
                if v == pn and len(self.adj.get(u, [])) > 1:
                    continue
                if wid in self.no.get((pw, u), ()):
                    continue
                only = self.only.get((pw, u))
                if only is not None and only != wid:
                    continue
                cost = d / (spd / 3.6)
                if abs(turn(heading(self.xy[pn], self.xy[u]), heading(self.xy[u], self.xy[v]))) > 45:
                    cost += 6.0
                seq += 1
                heapq.heappush(pq, (t + cost, seq, v, wid, u, path + (v,), ws + (wid,)))
        return None, None, math.inf


def build():
    report = []
    approach = offset([xy(p) for p in APPROACH], LANE_RIGHT)
    receiving = offset([xy(p) for p in RECEIVING], LANE_RIGHT)
    merge = approach[-1]
    receiving[0] = merge
    phillip = approach + receiving[1:]
    cum = cumulative(phillip)
    merge_x = cumulative(approach)[-1]
    signal_x = None
    for i, (a, b) in enumerate(zip(phillip, phillip[1:])):
        t, _, d = project(xy(UNIVERSITY), a, b)
        if d < 6 and t <= 1:
            signal_x = cum[i] + t * (cum[i + 1] - cum[i]) - 5.0
            break
    # The bus's right turn from Transit Plaza Road onto the receiving lane.
    right_turn = [xy(PLAZA_END), (lambda p: (p[0], p[1]))(xy((-80.538455, 43.474887))), merge]
    # The legal alternative: left onto northbound Phillip Street (two-way, no
    # turn restriction mapped at the exit), then the route to the next stop.
    northbound = offset(list(reversed([xy(p) for p in APPROACH])), LANE_RIGHT)
    left_turn = [xy(PLAZA_END), northbound[0]]
    data = json.loads(SOURCE.read_text())
    g = Graph(data)
    start = g.edges_near(northbound[-1], heading(northbound[-2], northbound[-1]), radius=25)
    if not start:
        raise RuntimeError("no northbound Phillip Street edge at the end of the left turn")
    corridor = json.loads((ROOT / "backend" / "waterloo_corridor.json").read_text())
    seg = next(s for s in corridor["segments"] if s["from"] == "UW" and s["to"] == "WATERLOO_SQUARE")
    cp = [to_xy(p[1], p[0]) for p in seg["path"]]
    targets = g.edges_near(xy(WS_STOP), heading(cp[-2], cp[-1]), radius=30)
    path, ways, t = g.route(start[0][1:], targets)
    if path is None:
        raise RuntimeError("no legal route from northbound Phillip Street to Waterloo Public Square")
    pts = [g.xy[n] for n in path]
    # Follow the left turn and the northbound lane into the routed path.
    detour = left_turn + northbound[1:] + pts[1:]
    names = []
    for w in ways:
        nm = g.W[w]["tags"].get("name") or g.W[w]["tags"]["highway"]
        if not names or names[-1] != nm:
            names.append(nm)
    speeds = []
    for a, b, w in zip(path, path[1:], ways):
        spd = next(s for v, w2, _, s in g.adj[a] if v == b and w2 == w)
        speeds.append(round(dist(g.xy[a], g.xy[b]) / (spd / 3.6), 2))
    lead = cumulative(left_turn + northbound[1:])[-1]
    report.append(f"Phillip St southbound: {cum[-1]:.0f} m; merge at {merge_x:.0f} m; University Ave stop line at "
                  f"{signal_x:.0f} m")
    report.append(f"left-turn alternative and route to Waterloo Public Square: {cumulative(detour)[-1] / 1000:.2f} km "
                  f"via {' → '.join(names)}, {(t + lead / 4.0) / 60:.1f} min")
    return {
        "attribution": "Phillip Street, the exit and the detour roads © OpenStreetMap contributors (ODbL); lane "
                       "offsets as in the UW private-car pilot (docs/uw-road-agents.md).",
        "phillip": {"path": encode(phillip), "length_m": round(cum[-1], 1), "merge_m": round(merge_x, 1),
                    "signal_m": round(signal_x, 1), "speed_mps": 11.0,
                    "signal_cycle": {"period_s": 54, "red_from_s": 11, "red_to_s": 34,
                                     "note": "representative cycle (the pilot's), not the real University Ave timing"}},
        "right_turn": {"path": encode(right_turn), "speed_mps": 4.0},
        "wrong_turn": {"path": encode(detour), "length_m": round(cumulative(detour)[-1], 1),
                       "lead_m": round(lead, 1), "via": ["left onto Phillip Street northbound"] + names,
                       "edge_s": speeds, "to_stop": "WATERLOO_SQUARE"},
    }, report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tiles")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.tiles:
        SOURCE.write_text(json.dumps(filter_tiles(args.tiles), separators=(",", ":")) + "\n")
        print(f"wrote {SOURCE.relative_to(ROOT)} ({SOURCE.stat().st_size // 1024} KiB)")
    out, report = build()
    for line in report:
        print(line)
    text = json.dumps(out, separators=(",", ":")) + "\n"
    if args.check:
        for path in OUTPUTS:
            if path.read_text() != text:
                raise SystemExit(f"{path} is stale")
        return
    for path in OUTPUTS:
        path.write_text(text)
    print(f"wrote {', '.join(str(p.relative_to(ROOT)) for p in OUTPUTS)} ({len(text) // 1024} KiB)")


if __name__ == "__main__":
    main()
