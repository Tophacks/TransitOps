"""Build road-following geometry for the Waterloo corridor and the ION scene.

Usage:
    python3 -I build_corridor.py GTFS_DIR [--lanelets LANELET_OSM]

GTFS_DIR is a Grand River Transit GTFS snapshot with `bus/` and `lrt/`
subfolders (for example a branch of github.com/jason897/grt-gtfs-history).

The builder:
1. Nodes every GRT bus shape into one directed street graph, so a path can
   change routes only where the shapes actually meet.
2. Routes each simulator segment between the real curb stops served by GRT
   (route 7 on King Street, the UW Station bus loop and Phillip Street).
3. Rounds intersection corners with tangent arcs sized for a 12 m bus.
4. Extracts both ION light rail tracks from the GTFS rail shapes and removes the
   short out-and-back spurs that GTFS uses to touch each platform.
5. Writes identical JSON to the backend and the frontend, and prints a
   verification report. With --lanelets it also measures how far the campus
   part of the bus path sits from independently surveyed lane centrelines.

Contains information provided by the Regional Municipality of Waterloo under
the Region's Open Data Licence.
"""
import argparse
import csv
import heapq
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import (  # noqa: E402
    cumulative, dedupe, dist, fillet, heading, point_at, project, resample, simplify,
    to_latlon, to_xy, turn,
)

REPO = Path(__file__).resolve().parents[2]
OUTPUTS = [
    REPO / "backend" / "waterloo_corridor.json",
    REPO / "frontend" / "lib" / "geo" / "waterloo-corridor.json",
]

# Simulator stop id -> GRT stop_id of the curb stop the corridor bus uses.
CORRIDOR_STOPS = {
    "UW": "1078",                    # University of Waterloo Station bus loop
    "WATERLOO_SQUARE": "4048",       # Waterloo Public Square, King St southbound
    "GRAND_RIVER_HOSPITAL": "2540",  # Grand River Hospital, King St southbound
    "CENTRAL": "1901",               # Central Station, King St southbound
}
YARD_STOP = "3227"  # Victoria / Joseph (northbound curb): staging point south of Central
# (from, to, signal the simulator applies to the segment, pass-through stops).
# The return leg runs back up King Street through Uptown (GRT route 7
# northbound) instead of whatever side street is shortest.
SEGMENTS = [
    ("UW", "WATERLOO_SQUARE", "I1", []),
    ("WATERLOO_SQUARE", "GRAND_RIVER_HOSPITAL", "I2", []),
    ("GRAND_RIVER_HOSPITAL", "CENTRAL", "I3", []),
    ("CENTRAL", "UW", "I2", ["1907"]),
    ("YARD", "CENTRAL", "I3", []),
]
# Signalised intersections the simulator's priority logic refers to.
SIGNALS = {
    "I1": ("University Ave / Phillip St", (43.47207, -80.53628)),
    "I2": ("King St / William St (Uptown)", (43.46168, -80.52147)),
    "I3": ("King St / Victoria St", (43.45287, -80.49815)),
}
STOP_LINE_BACKOFF_M = 9.0
LANE_OFFSET_M = 1.9      # bus centre right of the street centreline in traffic
BUS_HALF_WIDTH_M = 1.3
CURB_MAX_M = 9.0
# Lateral-acceleration limits used to cap speed through curves (m/s^2).
BUS_LATERAL = 1.4
ION_LATERAL = 1.0
CURVE_CAP = 16.0  # constraints above this speed are not stored
BUS_TURN_RADIUS = 11.0
ION_STATIONS = {
    "north": ["6117", "6118", "6119", "6120", "6121"],  # Allen, WPS, Laurier, UW, R&T (northbound)
    "south": ["6003", "6004", "6005", "6006", "6007"],  # R&T, UW, Laurier, Willis Way, Allen (southbound)
}
ION_SHAPES = {"north": "[@1.0.5502541@]11", "south": "[@1.0.5502541@]21"}
ION_WINDOW = {"north": (43.4595, 43.4840), "south": (43.4595, 43.4840)}


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


CORRIDOR_BOX = (43.444, 43.485, -80.553, -80.488)  # lat0, lat1, lon0, lon1


def load_shapes(path, box=None):
    """Shapes as metre polylines, split into the runs that fall inside `box`."""
    shapes = defaultdict(list)
    for row in read_csv(path):
        shapes[row["shape_id"]].append(
            (int(row["shape_pt_sequence"]), float(row["shape_pt_lat"]), float(row["shape_pt_lon"]))
        )
    out = {}
    for sid, points in shapes.items():
        run, part = [], 0
        for _, lat, lon in sorted(points):
            if box is None or (box[0] <= lat <= box[1] and box[2] <= lon <= box[3]):
                run.append(to_xy(lat, lon))
                continue
            if len(run) > 1:
                out[f"{sid}#{part}"] = dedupe(run)
                part += 1
            run = []
        if len(run) > 1:
            out[f"{sid}#{part}"] = dedupe(run)
    return out


class StreetGraph:
    """Directed graph built by noding GTFS bus shapes against each other."""

    SNAP = 7.0  # metres: shape vertices this close are the same street point

    def __init__(self, shapes):
        self.points = []
        self.grid = defaultdict(list)
        self.edges = defaultdict(dict)
        cells = defaultdict(list)
        all_vertices = set()
        for points in shapes.values():
            all_vertices.update((round(x, 1), round(y, 1)) for x, y in points)
        for p in all_vertices:
            cells[(int(p[0] // 50), int(p[1] // 50))].append(p)

        def nearby(p, radius):
            cx, cy = int(p[0] // 50), int(p[1] // 50)
            for gx in range(cx - 1, cx + 2):
                for gy in range(cy - 1, cy + 2):
                    for q in cells[(gx, gy)]:
                        yield q

        for points in shapes.values():
            for a, b in zip(points, points[1:]):
                length = dist(a, b)
                if length < 0.5:
                    continue
                # Split this edge wherever another shape's vertex lies on it.
                cuts = [(0.0, a), (1.0, b)]
                for k in range(int(length // 40) + 1):
                    probe = point_at([a, b], [0.0, length], min(length, k * 40.0))
                    for q in nearby(probe, 60):
                        t, foot, d = project(q, a, b)
                        if d < 2.5 and 0.0 < t < 1.0:
                            cuts.append((t, foot))
                cuts.sort()
                chain = [self.node(p) for _, p in cuts]
                for u, v in zip(chain, chain[1:]):
                    if u != v:
                        w = dist(self.points[u], self.points[v])
                        self.edges[u][v] = min(w, self.edges[u].get(v, math.inf))

    def node(self, p):
        cx, cy = int(p[0] // 20), int(p[1] // 20)
        best, best_d = None, self.SNAP
        for gx in range(cx - 1, cx + 2):
            for gy in range(cy - 1, cy + 2):
                for idx in self.grid[(gx, gy)]:
                    d = dist(self.points[idx], p)
                    if d < best_d:
                        best, best_d = idx, d
        if best is not None:
            return best
        self.points.append(p)
        self.grid[(cx, cy)].append(len(self.points) - 1)
        return len(self.points) - 1

    def anchor(self, p, max_distance=40.0):
        """Edges whose segment passes closest to p (one per direction)."""
        found = []
        for u, targets in self.edges.items():
            for v in targets:
                t, foot, d = project(p, self.points[u], self.points[v])
                if d <= max_distance:
                    found.append((d, u, v, t, foot))
        found.sort()
        return found

    def route(self, start, goal, start_heading=None, goal_heading=None):
        """Shortest path from the curb at `start` to the curb at `goal`.

        A stop is attached to the nearest edges whose direction agrees with the
        side of the street it serves. Returns the polyline in metres.
        """
        def candidates(p, want):
            out = []
            for d, u, v, t, foot in self.anchor(p):
                h = heading(self.points[u], self.points[v])
                if want is not None and abs(turn(h, want)) > 50:
                    continue
                out.append((d, u, v, foot))
            # Only the street the stop actually sits on: drop edges that merely
            # end near it, which would let the path stop short of the curb.
            return [item for item in out if item[0] <= out[0][0] + 1.5] if out else out

        starts = candidates(start, start_heading)
        goals = candidates(goal, goal_heading)
        if not starts or not goals:
            raise RuntimeError("stop could not be attached to the street graph")
        goal_edges = {(u, v): foot for _, u, v, foot in goals}
        best = None
        for d0, u0, v0, foot0 in starts:
            # Goal on the very edge we start on, ahead of the start point.
            if (u0, v0) in goal_edges:
                fg = goal_edges[(u0, v0)]
                if dist(self.points[u0], fg) > dist(self.points[u0], foot0) + 1.0:
                    cand = (dist(foot0, fg) + d0, [foot0, fg])
                    best = min(best, cand) if best else cand
            # Search over traversed edges so the path never reverses on the spot
            # (a bus cannot U-turn in the middle of a street).
            first = (u0, v0)
            cost_to = {first: dist(foot0, self.points[v0])}
            prev = {first: None}
            heap = [(cost_to[first], first)]
            while heap:
                cost, state = heapq.heappop(heap)
                if cost > cost_to.get(state, math.inf):
                    continue
                a, u = state
                for v, w in self.edges[u].items():
                    if v == a:
                        continue
                    if abs(turn(heading(self.points[a], self.points[u]),
                                heading(self.points[u], self.points[v]))) > 135:
                        continue
                    nxt = (u, v)
                    if nxt in goal_edges:
                        fg = goal_edges[nxt]
                        total = cost + dist(self.points[u], fg) + d0
                        if best is None or total < best[0]:
                            chain = [state]
                            while prev[chain[-1]] is not None:
                                chain.append(prev[chain[-1]])
                            chain.reverse()
                            nodes = [s[1] for s in chain]
                            best = (total, [foot0] + [self.points[i] for i in nodes] + [fg])
                    nc = cost + w
                    if nc < cost_to.get(nxt, math.inf):
                        cost_to[nxt] = nc
                        prev[nxt] = state
                        heapq.heappush(heap, (nc, nxt))
        if best is None:
            raise RuntimeError(f"no street path between stops {start} {goal}")
        return dedupe(best[1])


def stop_heading(graph, p):
    """Travel direction served by a curb stop.

    Traffic keeps right, so a stop serves the direction whose right-hand side
    it sits on.
    """
    for _, u, v, _, foot in graph.anchor(p):
        a, b = graph.points[u], graph.points[v]
        right = (b[1] - a[1], -(b[0] - a[0]))
        if (p[0] - foot[0]) * right[0] + (p[1] - foot[1]) * right[1] > 0:
            return heading(a, b)
    raise RuntimeError("stop is not beside any street edge")


def strip_spurs(points):
    """Drop the out-and-back detours GTFS rail shapes make to touch platforms."""
    changed = True
    while changed:
        changed = False
        for i in range(1, len(points) - 1):
            if dist(points[i - 1], points[i + 1]) < 1.0:
                del points[i:i + 2]
                changed = True
                break
    return dedupe(points)


def curve_limits(dense, lateral):
    """Speed caps from path curvature, as [distance_m, max_speed_mps] pairs.

    `dense` is sampled every ~2 m. The radius comes from the circle through
    points 4 m either side; only caps below CURVE_CAP are kept.
    """
    cum = cumulative(dense)
    out = []
    for i in range(2, len(dense) - 2):
        a, b, c = dense[i - 2], dense[i], dense[i + 2]
        ab, bc, ca = dist(a, b), dist(b, c), dist(c, a)
        cross = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
        if cross < 1e-6:
            continue
        radius = ab * bc * ca / (2 * cross)
        cap = math.sqrt(lateral * radius)
        if cap < CURVE_CAP:
            out.append([round(cum[i], 1), round(cap, 2)])
    return out


def encode(points, cum):
    out = []
    for (x, y), s in zip(points, cum):
        lat, lon = to_latlon(x, y)
        out.append([round(lon, 7), round(lat, 7), round(s, 2)])
    return out


def build(gtfs, lanelets=None):
    bus = Path(gtfs) / "bus"
    lrt = Path(gtfs) / "lrt"
    stops = {row["stop_id"]: row for row in read_csv(bus / "stops.txt")}
    graph = StreetGraph(load_shapes(bus / "shapes.txt", CORRIDOR_BOX))

    def stop_xy(stop_id):
        row = stops[stop_id]
        return to_xy(float(row["stop_lat"]), float(row["stop_lon"]))

    stop_points = {name: stop_xy(sid) for name, sid in CORRIDOR_STOPS.items()}
    stop_points["YARD"] = stop_xy(YARD_STOP)
    headings = {name: stop_heading(graph, p) for name, p in stop_points.items()}
    segments = []
    report = []
    signals_out = {}
    for a, b, signal_id, via in SEGMENTS:
        legs = [(stop_points[a], headings[a])]
        for stop_id in via:
            p = stop_xy(stop_id)
            legs.append((p, stop_heading(graph, p)))
        legs.append((stop_points[b], headings[b]))
        raw = []
        for (p, hp), (q, hq) in zip(legs, legs[1:]):
            part = graph.route(p, q, hp, hq)
            raw.extend(part if not raw else part[1:])
        raw = dedupe(raw)
        dense = resample(fillet(raw, BUS_TURN_RADIUS), 2.0)
        smooth = simplify(dense, 0.12)
        cum = cumulative(smooth)
        name, centre = SIGNALS[signal_id]
        c = to_xy(*centre)
        dense_cum = cumulative(dense)
        k = min(range(len(dense)), key=lambda i: dist(dense[i], c))
        if dist(dense[k], c) > 25:
            raise RuntimeError(f"signal {signal_id} is not on segment {a}->{b}")
        stop_line = max(5.0, dense_cum[k] - STOP_LINE_BACKOFF_M)
        if signal_id not in signals_out:
            lat, lon = to_latlon(*dense[k])
            signals_out[signal_id] = {"name": name, "lon": round(lon, 7), "lat": round(lat, 7)}
        # How far right of the street centreline the bus sits at each curb
        # stop: the stop pole's offset minus half a bus width and a gap.
        curb = []
        for p_stop, end in ((stop_points[a], 0), (stop_points[b], -1)):
            near, far = (smooth[0], smooth[1]) if end == 0 else (smooth[-2], smooth[-1])
            foot = smooth[0] if end == 0 else smooth[-1]
            dx, dy = far[0] - near[0], far[1] - near[1]
            norm = math.hypot(dx, dy) or 1.0
            lateral = ((p_stop[0] - foot[0]) * dy - (p_stop[1] - foot[1]) * dx) / norm
            curb.append(round(min(CURB_MAX_M, max(LANE_OFFSET_M, lateral - BUS_HALF_WIDTH_M - 0.4)), 2))
        segments.append({
            "from": a,
            "to": b,
            "length_m": round(cum[-1], 1),
            "curb_m": curb,
            "signal": {"id": signal_id, "stop_line_m": round(stop_line, 1)},
            "curves": curve_limits(dense, BUS_LATERAL),
            "path": encode(smooth, cum),
        })
        turns = []
        for p, q, r in zip(raw, raw[1:], raw[2:]):
            t = turn(heading(p, q), heading(q, r))
            if abs(t) > 35:
                lat, lon = to_latlon(*q)
                turns.append(f"{'left' if t < 0 else 'right'} {abs(t):.0f}° at {lat:.5f},{lon:.5f}")
        report.append(f"{a} -> {b}: {cum[-1]:.0f} m, {len(raw)} street vertices; turns: " + "; ".join(turns))

    ion = {}
    lrt_shapes = {k.split("#")[0]: v for k, v in load_shapes(lrt / "shapes.txt").items()}
    lrt_stops = {row["stop_id"]: row for row in read_csv(lrt / "stops.txt")}
    for direction, shape_id in ION_SHAPES.items():
        lat0, lat1 = ION_WINDOW[direction]
        track = strip_spurs(list(lrt_shapes[shape_id]))
        inside = [i for i, p in enumerate(track) if lat0 <= to_latlon(*p)[0] <= lat1]
        track = track[max(0, inside[0] - 1): inside[-1] + 2]
        dense = resample(track, 2.0)
        curves = curve_limits(dense, ION_LATERAL)
        track = simplify(dense, 0.08)
        cum = cumulative(track)
        stations = []
        for stop_id in ION_STATIONS[direction]:
            row = lrt_stops[stop_id]
            p = to_xy(float(row["stop_lat"]), float(row["stop_lon"]))
            best = min(
                ((project(p, track[i], track[i + 1]), i) for i in range(len(track) - 1)),
                key=lambda item: item[0][2],
            )
            (t, _, offset_m), i = best
            s = cum[i] + t * (cum[i + 1] - cum[i])
            stations.append({
                "id": stop_id,
                "name": row["stop_name"].replace(" - Northbound", "").replace(" - Southbound", ""),
                "at_m": round(s, 1),
            })
            report.append(f"ION {direction} {row['stop_name']}: platform {offset_m:.1f} m from track")
        stations.sort(key=lambda item: item["at_m"])
        ion[direction] = {
            "shape_id": shape_id,
            "length_m": round(cum[-1], 1),
            "stations": stations,
            "curves": curves,
            "path": encode(track, cum),
        }
        sharpest = max(
            (abs(turn(heading(track[i - 5], track[i]), heading(track[i], track[i + 5]))), i)
            for i in range(5, len(track) - 5)
        )
        lat, lon = to_latlon(*track[sharpest[1]])
        report.append(f"ION {direction}: {cum[-1]:.0f} m; sharpest bend {sharpest[0]:.0f}° per 20 m at {lat:.5f},{lon:.5f}")

    stops_out = {}
    for name, p in stop_points.items():
        sid = CORRIDOR_STOPS.get(name, YARD_STOP)
        lat, lon = to_latlon(*p)
        stops_out[name] = {"grt_stop_id": sid, "grt_name": stops[sid]["stop_name"], "lon": round(lon, 7), "lat": round(lat, 7)}

    if lanelets:
        report.extend(verify_against_lanelets(segments, lanelets))

    data = {
        "version": 1,
        "attribution": "Contains information provided by the Regional Municipality of Waterloo under licence (GRT GTFS).",
        "lane_offset_m": LANE_OFFSET_M,
        "curb_blend_m": 45.0,
        "stops": stops_out,
        "signals": signals_out,
        "segments": segments,
        "ion": ion,
    }
    return data, report


def verify_against_lanelets(segments, path):
    """Compare the campus part of the bus path with Lanelet2 lane centrelines.

    Lane-level maps come from the University of Waterloo WISE Lab GeoScenario
    project (MIT). They are independent of GRT's GTFS shapes.
    """
    import xml.etree.ElementTree as ET

    root = ET.parse(path).getroot()
    nodes = {n.get("id"): to_xy(float(n.get("lat")), float(n.get("lon"))) for n in root.iter("node")}
    ways = {w.get("id"): [nodes[nd.get("ref")] for nd in w.iter("nd")] for w in root.iter("way")}
    centres = []
    for rel in root.iter("relation"):
        tags = {t.get("k"): t.get("v") for t in rel.iter("tag")}
        if tags.get("type") != "lanelet" or tags.get("subtype") == "crosswalk":
            continue
        members = {m.get("role"): m.get("ref") for m in rel.iter("member")}
        left, right = ways.get(members.get("left")), ways.get(members.get("right"))
        if not left or not right:
            continue
        a, b = resample(left, 2.0), resample(right, 2.0)
        n = min(len(a), len(b))
        a = [point_at(a, cumulative(a), cumulative(a)[-1] * i / 20) for i in range(21)]
        b = [point_at(b, cumulative(b), cumulative(b)[-1] * i / 20) for i in range(21)]
        centres.append([((p[0] + q[0]) / 2, (p[1] + q[1]) / 2) for p, q in zip(a, b)])
    xs = [p[0] for c in centres for p in c]
    ys = [p[1] for c in centres for p in c]
    box = (min(xs), max(xs), min(ys), max(ys))
    lines = []
    from geo import offset as offset_line
    for segment in segments:
        pts = resample([to_xy(lat, lon) for lon, lat, _ in segment["path"]], 3.0)
        lane = offset_line(pts, 1.9)
        errors, uncovered, far = [], 0, []
        for i in range(1, len(lane) - 1):
            p = lane[i]
            if not (box[0] + 30 < p[0] < box[1] - 30 and box[2] + 30 < p[1] < box[3] - 30):
                continue
            h = heading(lane[i - 1], lane[i + 1])
            best, any_lane = math.inf, math.inf
            for c in centres:
                for a, b in zip(c, c[1:]):
                    d = project(p, a, b)[2]
                    any_lane = min(any_lane, d)
                    if abs(turn(heading(a, b), h)) <= 40:
                        best = min(best, d)
            if any_lane > 12:
                uncovered += 1
            else:
                errors.append(best)
                if best > 8:
                    far.append(to_latlon(*p))
        if far:
            lines.append(
                f"  {len(far)} samples sit more than 8 m from a same-direction lane, around "
                + ", ".join(f"{lat:.5f},{lon:.5f}" for lat, lon in far[:: max(1, len(far) // 4)])
            )
        if errors:
            errors.sort()
            p95 = errors[int(0.95 * (len(errors) - 1))]
            lines.append(
                f"lanelet check {segment['from']} -> {segment['to']}: {len(errors)} samples on mapped lanes "
                f"({uncovered} on roads the lanelet map omits); distance to a same-direction lane centre: "
                f"median {errors[len(errors) // 2]:.1f} m, p95 {p95:.1f} m, max {errors[-1]:.1f} m"
            )
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("gtfs")
    parser.add_argument("--lanelets")
    parser.add_argument("--check", action="store_true", help="fail if outputs differ from a fresh build")
    args = parser.parse_args()
    data, report = build(args.gtfs, args.lanelets)
    text = json.dumps(data, separators=(",", ":")) + "\n"
    for line in report:
        print(line)
    for out in OUTPUTS:
        if args.check:
            if out.read_text() != text:
                raise SystemExit(f"{out} is stale")
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"wrote {out.relative_to(REPO)} ({len(text) // 1024} KiB)")


if __name__ == "__main__":
    main()
