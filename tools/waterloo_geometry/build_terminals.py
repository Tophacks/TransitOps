"""Build the ION terminal track layouts for the simulator: Fairway and Conestoga.

Usage:
    python3 -I build_terminals.py [--check]

Reads trimmed OpenStreetMap extracts (sources/fairway-osm.json,
sources/conestoga-osm.json, ODbL) and the corridor geometry
(backend/waterloo_corridor.json, built from GRT GTFS), and writes
backend/ion_terminals.json and an identical frontend/lib/geo/ion-terminals.json.

Each terminal is a double (scissors) crossover ahead of two stub platform
tracks. The builder joins the OSM track ways into edges that meet at their
switch and track-end nodes, then lists every move a train can make:

- arrive at platform P: arrival running track, through the interlocking
  (straight or over one crossover branch), into P, stopping short of the
  track end;
- depart from platform P: out of P, through the interlocking (the other
  branch or straight), onto the departure running track.

Each move is a composite path: the GTFS line path from the neighbouring
station, joined onto the OSM running track where the two run within a couple
of metres (the "handover"), then the OSM trackwork to the platform (and the
reverse for departures). Along it the builder records the interlocking
resources a move occupies (switches, crossover and straight legs, the
diamond, the platform track), with the stretch of path each one covers, and
the speed limits (mapped maxspeed where tagged, else a modelled switch speed).

Station control in the simulator grants a move only when none of its
resources is reserved by another train; two moves conflict exactly when they
share a resource (the diamond makes the two crossover branches conflict).

Modelled, not mapped: the home signal position (a fixed distance before the
first switch), the stopping point short of the track end, the switch speed
where no maxspeed is tagged, and which platform is preferred.

Contains information provided by the Regional Municipality of Waterloo under
the Region's Open Data Licence. Map data © OpenStreetMap contributors (ODbL).
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import cumulative, dist, heading, project, to_latlon, to_xy, turn  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CORRIDOR = ROOT / "backend" / "waterloo_corridor.json"
OUTPUTS = [ROOT / "backend" / "ion_terminals.json", ROOT / "frontend" / "lib" / "geo" / "ion-terminals.json"]

ION_LENGTH = 41.8
STOP_SHORT_M = 7.0            # nose stops this far short of the track end (modelled)
HOME_SIGNAL_M = 30.0          # home signal this far before the first switch (modelled)
FOUL_M = 8.0                  # a switch or the diamond is occupied this far either side
SWITCH_SPEED = 25 / 3.6       # through the interlocking where no maxspeed is tagged (modelled)
APPROACH_SPEED = 12.0         # from the handover to the home signal (modelled)
SPLICE_M = 30.0               # length of the join from the GTFS line onto the OSM track
HANDOVER_WINDOW = (120.0, 480.0)  # metres before the first switch to look for the handover

TERMINALS = {
    "fairway": {
        "name": "Fairway",
        "station": "Fairway Station",
        "source": "fairway-osm.json",
        "arrive_line": "south",    # the south line ends here
        "depart_line": "north",    # the north line starts here
        "arrival_running": 754590788,
        "departure_running": 466396475,
        "entry_switch": 7049507417,
        "exit_switch": 7049507415,
        "edges": {
            "straight_in": ([1290108392], 7049507417, 7049507416),
            "straight_out": ([1290108393], 7049507418, 7049507415),
            "crossover_a": ([754590785], 7049507415, 7049507416),
            "crossover_b": ([754590786], 7049507417, 7049507418),
            "platform_1": ([1290108394, 836238397], 7049507416, 6576561512),
            "platform_2": ([836238396, 1223195057], 7049507418, 4770394467),
        },
        "crossovers": ["crossover_a", "crossover_b"],
        "diamond": 7049507419,
        "platforms": {"1": "platform_1", "2": "platform_2"},
        "arrive": {"1": [("straight_in", False)], "2": [("crossover_b", False)]},
        # Departure legs are listed from the exit switch towards the platform.
        "depart": {"1": [("crossover_a", False)], "2": [("straight_out", True)]},
        # Straight in to platform 1 keeps the crossover free for departures.
        "preference": ["1", "2"],
    },
    "conestoga": {
        "name": "Conestoga",
        "station": "Conestoga Station",
        "source": "conestoga-osm.json",
        "arrive_line": "north",
        "depart_line": "south",
        "arrival_running": 750968507,
        "departure_running": 397934412,
        "entry_switch": 7805105217,
        "exit_switch": 7805105215,
        "edges": {
            "straight_in": ([1290108443], 7805105217, 7805105216),
            "straight_out": ([1290108442], 7805105218, 7805105215),
            "crossover_a": ([836238400], 7805105216, 7805105215),
            "crossover_b": ([836238401], 7805105217, 7805105218),
            "platform_2": ([836238398], 7805105216, 5154483993),
            "platform_1": ([836238399], 7805105218, 4770417274),
        },
        "crossovers": ["crossover_a", "crossover_b"],
        "diamond": 7805105214,
        "platforms": {"1": "platform_1", "2": "platform_2"},
        "arrive": {"2": [("straight_in", False)], "1": [("crossover_b", False)]},
        "depart": {"2": [("crossover_a", True)], "1": [("straight_out", True)]},
        "preference": ["2", "1"],
    },
}


def load(name):
    data = json.loads((HERE / "sources" / name).read_text())
    nodes = {e["id"]: e for e in data["elements"] if e["type"] == "node"}
    ways = {e["id"]: e for e in data["elements"] if e["type"] == "way"}
    return data, nodes, ways


def chain(ways, ids, start, end):
    seq = []
    for wid in ids:
        ns = list(ways[wid]["nodes"])
        if not seq:
            seq = ns
            continue
        if ns[0] == seq[-1]:
            pass
        elif ns[-1] == seq[-1]:
            ns.reverse()
        elif ns[0] == seq[0] or ns[-1] == seq[0]:
            seq.reverse()
            if ns[-1] == seq[-1]:
                ns.reverse()
        if ns[0] != seq[-1]:
            raise RuntimeError(f"way {wid} does not join the previous way")
        seq.extend(ns[1:])
    if seq[0] != start:
        seq.reverse()
    if seq[0] != start or seq[-1] != end:
        raise RuntimeError(f"ways {ids} do not run {start} -> {end}")
    return seq


def encode(points):
    cum = cumulative(points)
    out = []
    for (x, y), s in zip(points, cum):
        lat, lon = to_latlon(x, y)
        out.append([round(lon, 7), round(lat, 7), round(s, 2)])
    return out


def maxspeed(ways, ids):
    speeds = [float(ways[w]["tags"]["maxspeed"]) / 3.6 for w in ids if "maxspeed" in ways[w].get("tags", {})]
    return min(speeds) if speeds else None


def locate(points, p):
    """(s along points, offset) of the nearest point to p."""
    cum = cumulative(points)
    best = None
    for i, (a, b) in enumerate(zip(points, points[1:])):
        t, _, d = project(p, a, b)
        if best is None or d < best[1]:
            best = (cum[i] + t * (cum[i + 1] - cum[i]), d)
    return best


def cut(points, s0, s1):
    """The part of a polyline between distances s0 and s1 (s0 < s1)."""
    cum = cumulative(points)

    def at(s):
        for i in range(len(cum) - 1):
            if cum[i] <= s <= cum[i + 1]:
                f = 0.0 if cum[i + 1] == cum[i] else (s - cum[i]) / (cum[i + 1] - cum[i])
                a, b = points[i], points[i + 1]
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        return points[-1] if s >= cum[-1] else points[0]

    inner = [p for p, s in zip(points, cum) if s0 < s < s1]
    return [at(s0)] + inner + [at(s1)]


def point_at_s(points, cum, s):
    s = max(0.0, min(cum[-1], s))
    for i in range(len(cum) - 1):
        if cum[i] <= s <= cum[i + 1]:
            f = 0.0 if cum[i + 1] == cum[i] else (s - cum[i]) / (cum[i + 1] - cum[i])
            a, b = points[i], points[i + 1]
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
    return points[-1]


def line_points(direction, corridor):
    path = corridor["ion"][direction]["path"]
    return [to_xy(p[1], p[0]) for p in path], [p[2] for p in path]


def line_stop(corridor, direction, name):
    """Nose position of a train stopped at `name` on the line (ion.Line.stops)."""
    track = corridor["ion"][direction]
    station = next(s for s in track["stations"] if s["name"] == name)
    return min(track["length_m"] - 0.5, max(ION_LENGTH, station["at_m"] + ION_LENGTH / 2.0))


def build_terminal(key, cfg, corridor, report):
    data, nodes, ways = load(cfg["source"])
    xy = {nid: to_xy(n["lat"], n["lon"]) for nid, n in nodes.items()}
    edges = {}
    for name, (wids, start, end) in cfg["edges"].items():
        seq = chain(ways, wids, start, end)
        edges[name] = {"ways": wids, "from": start, "to": end, "nodes": seq, "points": [xy[n] for n in seq],
                       "maxspeed": maxspeed(ways, wids)}
    for name in cfg["crossovers"]:
        if ways[cfg["edges"][name][0][0]]["tags"].get("service") not in (None, "crossover"):
            raise RuntimeError(f"{name} is not a crossover")
    # The two crossover branches cross at the mapped diamond.
    for name in cfg["crossovers"]:
        pts = edges[name]["points"]
        d = min(project(xy[cfg["diamond"]], a, b)[2] for a, b in zip(pts, pts[1:]))
        if d > 0.5:
            raise RuntimeError(f"{key}: {name} passes {d:.1f} m from the diamond")
    if nodes[cfg["diamond"]]["tags"].get("railway") != "railway_crossing":
        raise RuntimeError(f"{key}: diamond node is not railway=railway_crossing")
    # Every switch joins exactly three of the terminal's legs (running track included).
    switches = {}
    for name, e in edges.items():
        for nid in (e["from"], e["to"]):
            if nodes[nid].get("tags", {}).get("railway") == "switch":
                switches.setdefault(nid, set()).add(name)
    switches.setdefault(cfg["entry_switch"], set()).add("arrival_running")
    switches.setdefault(cfg["exit_switch"], set()).add("departure_running")
    for nid, legs in switches.items():
        if len(legs) != 3:
            raise RuntimeError(f"{key}: switch {nid} has legs {sorted(legs)}")

    arr_run = chain(ways, [cfg["arrival_running"]], None or ways[cfg["arrival_running"]]["nodes"][0], cfg["entry_switch"]) \
        if ways[cfg["arrival_running"]]["nodes"][-1] == cfg["entry_switch"] else \
        list(reversed(ways[cfg["arrival_running"]]["nodes"]))
    dep_run = list(ways[cfg["departure_running"]]["nodes"])
    if dep_run[0] != cfg["exit_switch"]:
        dep_run.reverse()
    if arr_run[-1] != cfg["entry_switch"] or dep_run[0] != cfg["exit_switch"]:
        raise RuntimeError(f"{key}: running tracks do not reach the switches")
    arr_pts = [xy[n] for n in arr_run]
    dep_pts = [xy[n] for n in dep_run]

    # Handover onto the arrival running track: the line vertex nearest the OSM
    # track between HANDOVER_WINDOW metres (along the OSM track) before the switch.
    def handover(direction, osm_pts, arriving):
        pts, cum = line_points(direction, corridor)
        osm_len = cumulative(osm_pts)[-1]
        best = None
        # Sample the line every 5 m near this end (its GTFS vertices are sparse).
        span = (cum[-1] - 1500.0, cum[-1]) if arriving else (0.0, 1500.0)
        samples = []
        s = max(0.0, span[0])
        while s <= min(cum[-1], span[1]):
            samples.append((cut(pts, 0.0, max(1e-6, s))[-1], s))
            s += 5.0
        line_total = cum[-1]
        osm_cum = cumulative(osm_pts)
        for p, s in samples:
            s_osm, off = locate(osm_pts, p)
            before = osm_len - s_osm if arriving else s_osm
            if not (HANDOVER_WINDOW[0] <= before <= HANDOVER_WINDOW[1]) or off > 2.5:
                continue
            # The join runs from the line onto the track (arriving) or off the
            # track onto the line (departing): the line's heading over the next
            # 40 m on the line side must match the track's.
            if arriving:
                la, lb = cut(pts, 0.0, max(1e-6, s - 40.0))[-1], p
                oa, ob = (point_at_s(osm_pts, osm_cum, s_osm + SPLICE_M), point_at_s(osm_pts, osm_cum, s_osm + SPLICE_M + 40.0))
            else:
                la, lb = p, cut(pts, 0.0, min(line_total, s + 40.0))[-1]
                oa, ob = (point_at_s(osm_pts, osm_cum, s_osm - SPLICE_M - 40.0), point_at_s(osm_pts, osm_cum, s_osm - SPLICE_M))
            bend = abs(turn(heading(la, lb), heading(oa, ob)))
            score = off + bend / 2.0
            if bend <= 6.0 and (best is None or score < best[3]):
                best = (s, s_osm, off, score, bend)
        if best is None:
            raise RuntimeError(f"{key}: no handover on the {direction} line")
        return best

    stops = {"arrive": {}, "depart": {}}
    routes = {}
    a_line, d_line = cfg["arrive_line"], cfg["depart_line"]
    a_pts, a_cum = line_points(a_line, corridor)
    d_pts, d_cum = line_points(d_line, corridor)
    a_stations = corridor["ion"][a_line]["stations"]
    d_stations = corridor["ion"][d_line]["stations"]
    prev_station = a_stations[-2]["name"]
    next_station = d_stations[1]["name"]
    prev_m = line_stop(corridor, a_line, prev_station)
    next_m = line_stop(corridor, d_line, next_station)
    h_in = handover(a_line, arr_pts, True)
    h_out = handover(d_line, dep_pts, False)
    report.append(f"{key}: arrival handover at {a_line} line {h_in[0]:.0f} m ({h_in[2]:.1f} m from OSM way "
                  f"{cfg['arrival_running']}, headings within {h_in[4]:.1f} deg); departure handover at {d_line} line "
                  f"{h_out[0]:.0f} m ({h_out[2]:.1f} m from way {cfg['departure_running']}, {h_out[4]:.1f} deg)")

    def interlocking(legs, platform):
        """Points through the interlocking, from the entry/exit switch to the platform track end,
        with (resource id, s0, s1) relative to the switch."""
        pts, res, caps = [], [], []
        for name, rev in legs + [(cfg["platforms"][platform], False)]:
            e = edges[name]
            p = list(reversed(e["points"])) if rev else list(e["points"])
            start_s = cumulative(pts)[-1] if pts else 0.0
            if pts:
                if dist(pts[-1], p[0]) > 0.05:
                    raise RuntimeError(f"{key}: {name} does not join the previous leg")
                p = p[1:]
            pts.extend(p)
            end_s = cumulative(pts)[-1]
            res.append((name, start_s, end_s))
            first_node, last_node = (e["to"], e["from"]) if rev else (e["from"], e["to"])
            for nid, at in ((first_node, start_s), (last_node, end_s)):
                if nodes[nid].get("tags", {}).get("railway") == "switch":
                    res.append((f"switch:{nid}", at - FOUL_M, at + FOUL_M))
            caps.append((start_s, end_s, e["maxspeed"] or SWITCH_SPEED))
            if name in cfg["crossovers"]:
                d_s = start_s + locate(list(reversed(e["points"])) if rev else e["points"], xy[cfg["diamond"]])[0]
                res.append(("diamond", d_s - FOUL_M / 2, d_s + FOUL_M / 2))
        return pts, res, caps

    for platform, legs in cfg["arrive"].items():
        il, res, caps = interlocking(legs, platform)
        first = legs[0][0]
        e0 = edges[first]
        entry = e0["to"] if legs[0][1] else e0["from"]
        if entry != cfg["entry_switch"]:
            raise RuntimeError(f"{key}: arrival to {platform} does not start at the entry switch")
        # Line from the previous station to the handover, a short join, then the OSM track.
        line_part = cut(a_pts, prev_m, h_in[0])
        osm_len = cumulative(arr_pts)[-1]
        osm_part = cut(arr_pts, min(osm_len, h_in[1] + SPLICE_M), osm_len)
        pts = line_part + osm_part[0:] + il[1:]
        cum = cumulative(pts)
        switch_s = cum[len(line_part) + len(osm_part) - 1]
        end = cum[-1]
        line_len = cumulative(line_part)[-1]
        resources = [{"id": rid, "s0": round(switch_s + s0, 2), "s1": round(switch_s + s1, 2)} for rid, s0, s1 in res]
        curves = [[round(s - prev_m, 1), v] for s, v in corridor["ion"][a_line]["curves"] if prev_m <= s <= h_in[0]]
        zones = [[round(line_len, 1), round(switch_s - HOME_SIGNAL_M, 1), APPROACH_SPEED]]
        zones += [[round(switch_s + s0, 2), round(switch_s + s1 + ION_LENGTH, 2), round(v, 3)] for s0, s1, v in caps]
        zones.append([round(switch_s - HOME_SIGNAL_M, 2), round(switch_s + 0.0, 2), round(min(c[2] for c in caps), 3)])
        routes[f"arrive:{platform}"] = {
            "kind": "arrive", "platform": platform,
            "via": [{"edge": n, "reversed": r} for n, r in legs],
            "crossover": any(n in cfg["crossovers"] for n, _ in legs),
            "path": encode(pts),
            "length_m": round(end, 2),
            "stop_m": round(end - STOP_SHORT_M, 2),
            "home_signal_m": round(switch_s - HOME_SIGNAL_M, 2),
            "switch_m": round(switch_s, 2),
            "line": {"direction": a_line, "from_station": prev_station, "from_m": round(prev_m, 3),
                     "handover_m": round(h_in[0], 2), "line_part_m": round(line_len, 2)},
            "resources": sorted(resources, key=lambda r: r["s0"]),
            "curves": curves,
            "zones": zones,
        }
    for platform, legs in cfg["depart"].items():
        il, res, caps = interlocking(legs, platform)
        # interlocking() runs switch -> platform end; a departure runs it backwards.
        il_rev = list(reversed(il))
        il_len = cumulative(il)[-1]
        osm_len = cumulative(dep_pts)[-1]
        osm_part = cut(dep_pts, 0.0, max(0.0, h_out[1] - SPLICE_M))
        line_part = cut(d_pts, h_out[0], next_m)
        if dist(il_rev[-1], osm_part[0]) > 0.05:
            raise RuntimeError(f"{key}: departure from {platform} does not reach the exit switch")
        pts = il_rev + osm_part[1:] + line_part
        cum = cumulative(pts)
        switch_s = il_len
        resources = [{"id": rid, "s0": round(il_len - s1, 2), "s1": round(il_len - s0, 2)} for rid, s0, s1 in res]
        line_start = cum[-1] - cumulative(line_part)[-1]
        curves = [[round(line_start + s - h_out[0], 1), v] for s, v in corridor["ion"][d_line]["curves"] if h_out[0] <= s <= next_m]
        zones = [[round(il_len - s1 - ION_LENGTH, 2), round(il_len - s0, 2), round(v, 3)] for s0, s1, v in caps]
        zones = [[max(0.0, a), b, v] for a, b, v in zones]
        # Until the tail clears the exit switch.
        zones.append([0.0, round(switch_s + ION_LENGTH, 2), round(min(c[2] for c in caps), 3)])
        zones.append([round(switch_s + ION_LENGTH, 2), round(line_start, 2), APPROACH_SPEED])
        start = STOP_SHORT_M + ION_LENGTH
        routes[f"depart:{platform}"] = {
            "kind": "depart", "platform": platform,
            # In the direction of travel: platform first.
            "via": [{"edge": n, "reversed": not r} for n, r in reversed(legs)],
            "crossover": any(n in cfg["crossovers"] for n, _ in legs),
            "path": encode(pts),
            "length_m": round(cum[-1], 2),
            "start_m": round(start, 2),
            "switch_m": round(switch_s, 2),
            "line": {"direction": d_line, "to_station": next_station, "to_m": round(next_m, 3),
                     "handover_m": round(h_out[0], 2), "line_start_m": round(line_start, 2)},
            "resources": sorted(resources, key=lambda r: r["s0"]),
            "curves": curves,
            "zones": zones,
        }
    # Dedupe resources (a switch can be listed twice for a route).
    for r in routes.values():
        seen, out = set(), []
        for item in r["resources"]:
            key2 = (item["id"], item["s0"])
            if key2 not in seen:
                seen.add(key2)
                out.append(item)
        r["resources"] = out
        pts = r["path"]
        worst = 0.0
        P = [to_xy(p[1], p[0]) for p in pts]
        for a, b, c in zip(P, P[1:], P[2:]):
            if dist(a, b) > 0.5 and dist(b, c) > 0.5:
                worst = max(worst, abs(turn(heading(a, b), heading(b, c))))
        report.append(f"  {r['kind']} platform {r['platform']} via "
                      f"{'+'.join(v['edge'] for v in r['via'])}: {r['length_m']:.0f} m, sharpest vertex {worst:.1f} deg, "
                      f"resources {', '.join(x['id'] for x in r['resources'])}")
    # Conflict table: two moves conflict when they share a resource.
    names = sorted(routes)
    conflicts = {a: sorted(b for b in names if b != a and {x["id"] for x in routes[a]["resources"]}
                           & {x["id"] for x in routes[b]["resources"]}) for a in names}
    report.append(f"  conflicts: " + "; ".join(f"{a} x {', '.join(v) or '-'}" for a, v in conflicts.items()))
    return {
        "name": cfg["name"],
        "station": cfg["station"],
        "source": data["source"],
        "arrive_line": a_line,
        "depart_line": d_line,
        "preference": cfg["preference"],
        "edges": {n: {"ways": e["ways"], "from": e["from"], "to": e["to"], "maxspeed_mps": e["maxspeed"],
                      "path": encode(e["points"])} for n, e in edges.items()},
        "running": {"arrive": {"way": cfg["arrival_running"], "path": encode(arr_pts)},
                    "depart": {"way": cfg["departure_running"], "path": encode(dep_pts)}},
        "switches": [{"node": nid, "lon": nodes[nid]["lon"], "lat": nodes[nid]["lat"], "legs": sorted(legs)}
                     for nid, legs in sorted(switches.items())],
        "diamond": {"node": cfg["diamond"], "lon": nodes[cfg["diamond"]]["lon"], "lat": nodes[cfg["diamond"]]["lat"]},
        "track_ends": [{"platform": p, "node": edges[e]["to"], "lon": nodes[edges[e]["to"]]["lon"],
                        "lat": nodes[edges[e]["to"]]["lat"]} for p, e in sorted(cfg["platforms"].items())],
        "platforms": {p: {"edge": e} for p, e in cfg["platforms"].items()},
        "routes": routes,
        "conflicts": conflicts,
    }


def build():
    corridor = json.loads(CORRIDOR.read_text())
    report = []
    out = {
        "attribution": "Terminal trackwork © OpenStreetMap contributors (ODbL); line paths from GRT GTFS: contains "
                       "information provided by the Regional Municipality of Waterloo under the Region's Open Data Licence.",
        "train_length_m": ION_LENGTH,
        "modelled": {
            "stop_short_of_track_end_m": STOP_SHORT_M,
            "home_signal_before_switch_m": HOME_SIGNAL_M,
            "switch_speed_mps_where_untagged": round(SWITCH_SPEED, 3),
            "approach_speed_mps": APPROACH_SPEED,
            "fouling_m": FOUL_M,
        },
        "terminals": {key: build_terminal(key, cfg, corridor, report) for key, cfg in TERMINALS.items()},
    }
    return out, report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the outputs differ from a fresh build")
    args = parser.parse_args()
    out, report = build()
    text = json.dumps(out, separators=(",", ":")) + "\n"
    for line in report:
        print(line)
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
