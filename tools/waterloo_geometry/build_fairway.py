"""Build the Fairway terminal demo geometry: ION trackwork and the bus layover.

Usage:
    python3 -I build_fairway.py GTFS_DIR [--check]

Reads sources/fairway-osm.json, a trimmed OpenStreetMap extract (ODbL) of the
Fairway terminal, and GRT's GTFS (for the bus layover), and writes
frontend/lib/geo/fairway.json.

Track. The ION tracks are built as a graph of connected edges that meet at
OSM nodes: the two running tracks, the scissors crossover (ways 754590785 and
754590786, which cross at a diamond), the straight legs through it, and the
two platform tracks ending at buffer stops. The train's moves are chains of
those edges, and the renderer draws the same edges, so the map and the train
agree on where a train can go. The builder fails if a move uses two edges
that do not share a node, or passes through a switch on legs that do not
meet there.

Turnback. Trains arrive on the south running track (right-hand running) and
the GTFS shapes run straight towards platform 1; departures join the north
running track. Crossing on departure through crossover 754590785 is the move
modelled here; the operating practice is not verified.

Bus. GRT's station map (May 2026) and GTFS put route 7 at bay 3490 on the
one-way bus lane. GTFS blocks give the layover: a route 7 bus arrives as
"Fairway Station" and leaves the same bay as "Conestoga Station". The bus
pulls out along the lane in its mapped one-way direction and leaves by the
service roads route 7's GTFS shape uses.

Contains information provided by the Regional Municipality of Waterloo under
the Region's Open Data Licence. Map data © OpenStreetMap contributors (ODbL).
"""
import argparse
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import cumulative, dist, heading, project, resample, simplify, to_latlon, to_xy, turn  # noqa: E402

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "sources" / "fairway-osm.json"
OUTPUT = HERE.parents[1] / "frontend" / "lib" / "geo" / "fairway.json"

# Node ids at the terminal (OpenStreetMap).
NW, NE, SW, SE = 7049507415, 7049507418, 7049507417, 7049507416   # crossover switches
DIAMOND = 7049507419
BUFFER_1, BUFFER_2 = 6576561512, 4770394467
STOP_1, STOP_2 = 11961583353, 11346231696                       # platform stop positions
# Track edges: name -> (way ids in order, start node, end node).
EDGES = {
    "running_south": ([754590788], None, SW),          # arrivals, from the west
    "running_north": ([466396475], NW, None),          # departures, to the west
    "straight_south": ([1290108392], SW, SE),
    "straight_north": ([1290108393], NE, NW),
    "crossover_nw_se": ([754590785], NW, SE),
    "crossover_sw_ne": ([754590786], SW, NE),
    "platform_1": ([1290108394, 836238397], SE, BUFFER_1),
    "platform_2": ([836238396, 1223195057], NE, BUFFER_2),
}
RUNNING_KEEP_M = 420           # how much running track west of the switches to keep
# The modelled turnback: (edge, reversed?) in travel order.
ARRIVAL = [("running_south", False), ("straight_south", False), ("platform_1", False)]
DEPARTURE = [("platform_1", True), ("crossover_nw_se", True), ("running_north", False)]
STOP_SHORT_OF_BUFFER_M = 7.0   # front of the train stops this far short of the buffer stop
TRAIN_LENGTH_M = 41.8

BUS_LANE = [673987693, 1360763345, 1360763346, 1360763347, 1360763348, 1360763349]
BUS_EXIT = [35526174, 1193661698]
BAY_STOP = "3490"
BUS_ROUTE = "7"


def load():
    data = json.loads(SOURCE.read_text())
    nodes = {e["id"]: e for e in data["elements"] if e["type"] == "node"}
    ways = {e["id"]: e for e in data["elements"] if e["type"] == "way"}
    return data, nodes, ways


def chain(ways, ids, start=None, end=None):
    """Node ids of consecutive ways joined end to end, oriented start -> end."""
    seq = []
    for wid in ids:
        ns = list(ways[wid]["nodes"])
        if seq:
            if ns[0] == seq[-1]:
                pass
            elif ns[-1] == seq[-1]:
                ns.reverse()
            elif ns[0] == seq[0] or ns[-1] == seq[0]:
                seq.reverse()
                if ns[-1] == seq[-1]:
                    ns.reverse()
            else:
                raise RuntimeError(f"way {wid} does not connect to the previous way")
            if ns[0] != seq[-1]:
                raise RuntimeError(f"way {wid} does not connect to the previous way")
            seq.extend(ns[1:])
        else:
            seq = ns
    if start is not None and seq[0] != start:
        if seq[-1] == start:
            seq.reverse()
        else:
            raise RuntimeError(f"edge does not start at node {start}")
    if end is not None and seq[-1] != end:
        if seq[0] == end and start is None:
            seq.reverse()
        else:
            raise RuntimeError(f"edge does not end at node {end}")
    return seq


def encode(points):
    cum = cumulative(points)
    out = []
    for (x, y), s in zip(points, cum):
        lat, lon = to_latlon(x, y)
        out.append([round(lon, 7), round(lat, 7), round(s, 2)])
    return out


def trim_running(points, keep, from_end):
    """Keep `keep` metres of a running track next to the switches."""
    pts = points if from_end else list(reversed(points))
    cum = cumulative(pts)
    total = cum[-1]
    start = max(0.0, total - keep)
    kept = [p for p, s in zip(pts, cum) if s >= start]
    # Interpolate the cut so the edge is exactly `keep` long.
    i = next(i for i, s in enumerate(cum) if s >= start)
    if i > 0 and cum[i] > start:
        a, b = pts[i - 1], pts[i]
        f = (start - cum[i - 1]) / (cum[i] - cum[i - 1])
        kept.insert(0, (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return kept if from_end else list(reversed(kept))


def build_track(nodes, ways, report):
    xy = {nid: to_xy(n["lat"], n["lon"]) for nid, n in nodes.items()}
    edges = {}
    for name, (wids, start, end) in EDGES.items():
        seq = chain(ways, wids, start, end)
        pts = [xy[n] for n in seq]
        if name == "running_south":
            pts = trim_running(pts, RUNNING_KEEP_M, from_end=True)
        if name == "running_north":
            pts = trim_running(pts, RUNNING_KEEP_M, from_end=False)
        edges[name] = {"ways": wids, "from": seq[0] if name != "running_south" else None,
                       "to": seq[-1] if name != "running_north" else None,
                       "nodes": seq, "points": pts}
    # Switches: which edges meet at each.
    switches = []
    for nid in (NW, NE, SW, SE):
        legs = sorted(name for name, e in edges.items() if e["from"] == nid or e["to"] == nid)
        n = nodes[nid]
        if n.get("tags", {}).get("railway") != "switch":
            raise RuntimeError(f"node {nid} is not tagged railway=switch")
        if len(legs) != 3:
            raise RuntimeError(f"switch {nid} has {len(legs)} legs: {legs}")
        switches.append({"node": nid, "lon": n["lon"], "lat": n["lat"], "legs": legs})
    # The two crossover ways cross at the mapped diamond.
    a, b = edges["crossover_nw_se"]["points"], edges["crossover_sw_ne"]["points"]
    d = min(project(xy[DIAMOND], p, q)[2] for p, q in zip(a, a[1:]))
    d2 = min(project(xy[DIAMOND], p, q)[2] for p, q in zip(b, b[1:]))
    report.append(f"diamond {DIAMOND} lies {d:.2f} m / {d2:.2f} m from the two crossovers")
    if max(d, d2) > 1.0:
        raise RuntimeError("the crossovers do not cross at the mapped diamond")
    return edges, switches, xy


def route(edges, legs, report, label):
    """Join edges into one path; every joint must be a shared node."""
    points, names, prev_end = [], [], None
    for name, rev in legs:
        e = edges[name]
        pts = list(reversed(e["points"])) if rev else list(e["points"])
        start, end = (e["to"], e["from"]) if rev else (e["from"], e["to"])
        if prev_end is not None and start != prev_end:
            raise RuntimeError(f"{label}: {name} starts at {start}, not at {prev_end}")
        if points and dist(points[-1], pts[0]) > 0.01:
            raise RuntimeError(f"{label}: gap at the joint before {name}")
        points.extend(pts if not points else pts[1:])
        names.append({"edge": name, "reversed": rev, "from_m": round(cumulative(points[: len(points) - len(pts) + 1])[-1], 2) if names else 0.0})
        prev_end = end
    # A move may only change track at a switch, through one of its legs.
    worst = 0.0
    for p, q, r in zip(points, points[1:], points[2:]):
        if dist(p, q) > 0.3 and dist(q, r) > 0.3:
            worst = max(worst, abs(turn(heading(p, q), heading(q, r))))
    report.append(f"{label}: {cumulative(points)[-1]:.1f} m over {' -> '.join(n['edge'] for n in names)}; sharpest vertex turn {worst:.1f} deg")
    return points, names


def curve_caps(points, lateral=1.0, cap=12.0):
    dense = resample(points, 2.0)
    cum = cumulative(dense)
    out = []
    for i in range(2, len(dense) - 2):
        a, b, c = dense[i - 2], dense[i], dense[i + 2]
        ab, bc, ca = dist(a, b), dist(b, c), dist(c, a)
        cross = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
        if cross < 1e-6:
            continue
        v = math.sqrt(lateral * ab * bc * ca / (2 * cross))
        if v < cap:
            out.append([round(cum[i], 1), round(v, 2)])
    return out


def gtfs_layover(gtfs, report):
    """Route 7 blocks at bay 3490: what arrives, what leaves, and how long it waits."""
    bus = Path(gtfs) / "bus"

    def rows(name):
        with open(bus / name, encoding="utf-8-sig", newline="") as handle:
            yield from csv.DictReader(handle)

    def secs(t):
        h, m, s = map(int, t.split(":"))
        return h * 3600 + m * 60 + s

    trips = {r["trip_id"]: r for r in rows("trips.txt") if r["route_id"] == BUS_ROUTE}
    stops = {r["stop_id"]: r for r in rows("stops.txt")}
    first, last = {}, {}
    for r in rows("stop_times.txt"):
        tid = r["trip_id"]
        if tid not in trips:
            continue
        seq = int(r["stop_sequence"])
        if tid not in first or seq < first[tid][0]:
            first[tid] = (seq, r["stop_id"], secs(r["departure_time"]))
        if tid not in last or seq > last[tid][0]:
            last[tid] = (seq, r["stop_id"], secs(r["arrival_time"]))
    blocks = defaultdict(list)
    for tid, t in trips.items():
        blocks[(t["block_id"], t["service_id"])].append(tid)
    pairs, layovers, garage = Counter(), [], 0
    for tids in blocks.values():
        tids.sort(key=lambda x: first[x][2])
        for a, b in zip(tids, tids[1:]):
            if last[a][1] == BAY_STOP and first[b][1] == BAY_STOP:
                pairs[(trips[a]["trip_headsign"], trips[b]["trip_headsign"])] += 1
                layovers.append((first[b][2] - last[a][2]) / 60)
        if last[tids[-1]][1] == BAY_STOP:
            garage += 1
    (arrive, depart), count = pairs.most_common(1)[0]
    stop = stops[BAY_STOP]
    report.append(f"route {BUS_ROUTE} at bay {BAY_STOP}: '{arrive}' -> '{depart}' {count} times, "
                  f"median layover {statistics.median(layovers):.0f} min; {garage} blocks end here (out of service)")
    return {
        "route": BUS_ROUTE,
        "arriving_headsign": arrive,
        "next_headsign": depart,
        "layovers_counted": count,
        "median_layover_min": round(statistics.median(layovers), 1),
        "blocks_ending_here": garage,
        "stop": {"id": BAY_STOP, "name": stop["stop_name"], "lon": float(stop["stop_lon"]), "lat": float(stop["stop_lat"])},
    }


def build_bus(nodes, ways, xy, layover, report):
    lane_nodes = chain(ways, BUS_LANE)
    for wid in BUS_LANE:
        tags = ways[wid]["tags"]
        if tags.get("oneway") != "yes" or tags.get("bus") != "designated":
            raise RuntimeError(f"way {wid} is not a one-way bus lane")
        # The mapped one-way direction must match the order we drive it in.
        ns = ways[wid]["nodes"]
        if lane_nodes.index(ns[0]) > lane_nodes.index(ns[-1]):
            raise RuntimeError(f"way {wid} runs against the lane")
    exit_nodes = chain(ways, BUS_EXIT, start=lane_nodes[-1])
    path = [xy[n] for n in lane_nodes] + [xy[n] for n in exit_nodes[1:]]
    cum = cumulative(path)
    pole = to_xy(layover["stop"]["lat"], layover["stop"]["lon"])
    (t, foot, off), i = min(((project(pole, path[i], path[i + 1]), i) for i in range(len(path) - 1)), key=lambda x: x[0][2])
    pole_s = cum[i] + t * (cum[i + 1] - cum[i])
    lane_end = cumulative([xy[n] for n in lane_nodes])[-1]
    if pole_s > lane_end:
        raise RuntimeError("the bay is not on the one-way lane")
    report.append(f"bay {BAY_STOP}: pole {off:.1f} m from the lane, {pole_s:.0f} m along it; "
                  f"lane {lane_end:.0f} m, exit to the service roads {cum[-1] - lane_end:.0f} m")
    return {"path": encode(path), "curves": curve_caps(path, lateral=1.3, cap=8.0), "lane_end_m": round(lane_end, 1), "bay_pole_m": round(pole_s, 1),
            "bay_pole_offset_m": round(off, 1), "lane_ways": BUS_LANE, "exit_ways": BUS_EXIT}


def build(gtfs):
    data, nodes, ways = load()
    report = []
    edges, switches, xy = build_track(nodes, ways, report)
    arrival, arrival_edges = route(edges, ARRIVAL, report, "arrival")
    departure, departure_edges = route(edges, DEPARTURE, report, "departure")
    arr_len = cumulative(arrival)[-1]
    stop_m = arr_len - STOP_SHORT_OF_BUFFER_M
    platform_1_from = next(e["from_m"] for e in arrival_edges if e["edge"] == "platform_1")
    if stop_m - TRAIN_LENGTH_M < platform_1_from:
        report.append("note: the stopped train's rear is still on the switch approach")
    # Where the stopped train sits on the departure path (which starts at the buffer).
    dep_front = STOP_SHORT_OF_BUFFER_M + TRAIN_LENGTH_M
    layover = gtfs_layover(gtfs, report)
    bus = build_bus(nodes, ways, xy, layover, report)
    out = {
        "attribution": "Track, platforms and bus lane © OpenStreetMap contributors (ODbL). Bus layover from GRT GTFS: "
                       "contains information provided by the Regional Municipality of Waterloo under the Region's Open Data Licence.",
        "source": data["source"],
        "track": {
            "edges": {name: {"ways": e["ways"], "from": e["from"], "to": e["to"], "path": encode(e["points"])} for name, e in edges.items()},
            "switches": switches,
            "diamond": {"node": DIAMOND, "lon": nodes[DIAMOND]["lon"], "lat": nodes[DIAMOND]["lat"]},
            "buffers": [{"node": n, "lon": nodes[n]["lon"], "lat": nodes[n]["lat"]} for n in (BUFFER_1, BUFFER_2)],
        },
        "turnback": {
            "modeled": True,
            "platform": "1",
            "arrival": {"edges": arrival_edges, "path": encode(arrival), "curves": curve_caps(arrival), "stop_m": round(stop_m, 2)},
            "departure": {"edges": departure_edges, "path": encode(departure), "curves": curve_caps(departure),
                          "start_m": round(dep_front, 2)},
            "train_length_m": TRAIN_LENGTH_M,
        },
        "bus": {**bus, "layover": layover},
    }
    report.append(f"turnback: stops {STOP_SHORT_OF_BUFFER_M} m short of buffer {BUFFER_1} on platform 1, "
                  f"departs through crossover 754590785 onto the north running track")
    return out, report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("gtfs")
    parser.add_argument("--check", action="store_true", help="fail if the output differs from a fresh build")
    args = parser.parse_args()
    out, report = build(args.gtfs)
    text = json.dumps(out, separators=(",", ":")) + "\n"
    for line in report:
        print(line)
    if args.check:
        if OUTPUT.read_text() != text:
            raise SystemExit(f"{OUTPUT} is stale")
        return
    OUTPUT.write_text(text)
    print(f"wrote {OUTPUT.relative_to(HERE.parents[1])} ({len(text) // 1024} KiB)")


if __name__ == "__main__":
    main()
