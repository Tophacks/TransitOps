"""Build complete scheduled GRT bus lines from GTFS: every trip of the service
day, in both directions, with its stop order, times, route variant, block
and shape, and the stops each line shares with ION and the corridor model.

Usage:
    python3 -I build_lines.py [--routes 7] [--gtfs /home/user/gtfs/bus] [--check]

Source: GRT GTFS (Region of Waterloo Open Data Licence): routes, trips,
stop_times, stops, shapes and calendar_dates for Wednesday 2 December 2026
(the day the garages and UW Station use). ION platforms: the GRT LRT GTFS.

What is checked (printed, and the build fails if it does not hold):
- every trip's stops are in GTFS stop-sequence order and lie within 40 m of
  its shape, at increasing distances along it;
- a route variant (pattern) is one stop sequence on one shape;
- a block's trips do not overlap in time;
- a stop is linked to an ION station only if its GTFS name names the
  station and it is within 300 m of one of the station's platforms (the walk
  is kept);
- a stop is linked to a corridor stop only by its GTFS stop id.

Times: GTFS gives whole minutes, so several stops in a row can share a
minute. `sched` spreads each minute change over the stops since the last one
in proportion to distance (stated in the output); the GTFS times are kept.

Contains information provided by the Regional Municipality of Waterloo under
the Region's Open Data Licence.
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import (cumulative, dedupe, dist, fillet, heading, point_at, project, resample, simplify, to_latlon,  # noqa: E402
                 to_xy, turn)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUTPUTS = [ROOT / "backend" / "lines.json", ROOT / "frontend" / "lib" / "geo" / "lines.json"]
DATE = "20261202"
STOP_SHAPE_M = 40.0
ION_WALK_MAX_M = 300.0
CORRIDOR_STOPS = {"1078": "UW", "4048": "WATERLOO_SQUARE", "2540": "GRAND_RIVER_HOSPITAL", "1901": "CENTRAL"}


# Curve speed limits, as for the corridor buses (build_corridor.curve_limits).
BUS_LATERAL = 1.4          # m/s^2 of lateral acceleration a bus driver accepts in a curve (modelled)
BUS_TURN_RADIUS = 11.0     # shape corners rounded to a bus's turning path, as for the corridor
SHAPE_TOLERANCE_M = 2.5    # GTFS shape jitter removed before rounding (Douglas-Peucker)
CURVE_CAP = 16.0
# Shared traffic modelled on Route 7 (the UW Station lane rules, road_traffic.py):
# a curbside stop on King Street and the bay exit at Fairway Station.
ZONES = [
    {"id": "king-kci", "kind": "curbside", "stop": "2542", "direction": 1, "before_m": 300.0, "after_m": 200.0},
    {"id": "fairway-bay", "kind": "bay", "stop": "3490", "direction": 0, "before_m": 300.0, "after_m": 220.0},
]
BUS_L = 12.2
CURB_TAPER_M = 20.0        # the bus leaves the traffic lane this far before a curbside stop
PUBLIC = {"primary", "secondary", "tertiary", "unclassified", "residential", "primary_link", "secondary_link",
          "tertiary_link", "trunk", "trunk_link"}
OSM_ROADS = HERE / "sources" / "deadheads-osm.json"


def curve_limits(dense, lateral=BUS_LATERAL):
    """Speed caps from path curvature, [distance_m, max_speed_mps] (build_corridor.curve_limits)."""
    cum = cumulative(dense)
    out = []
    for i in range(2, len(dense) - 2):
        a, b, c = dense[i - 2], dense[i], dense[i + 2]
        ab, bc, ca = dist(a, b), dist(b, c), dist(c, a)
        cross = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
        if cross < 1e-6:
            continue
        cap = (lateral * ab * bc * ca / (2 * cross)) ** 0.5
        if cap < CURVE_CAP:
            out.append([round(cum[i], 1), round(cap, 2)])
    return out


def encode(points, s0=0.0):
    out = []
    for (x, y), s in zip(points, cumulative(points)):
        lat, lon = to_latlon(x, y)
        out.append([round(lon, 6), round(lat, 6), round(s0 + s, 1)])
    return out


def slice_path(xy, cum, a, b):
    """Points of the polyline between distances a and b."""
    out = [point_at(xy, cum, a)]
    out += [p for p, s in zip(xy, cum) if a < s < b]
    out.append(point_at(xy, cum, b))
    return out


def road_ways():
    d = json.loads(OSM_ROADS.read_text())
    N = {e["id"]: to_xy(e["lat"], e["lon"]) for e in d["elements"] if e["type"] == "node"}
    W = [e for e in d["elements"] if e["type"] == "way" and e["tags"].get("highway")]
    return N, W


def nearest_way(p, N, W, bearing=None):
    best = None
    for w in W:
        ns = [N[n] for n in w["nodes"] if n in N]
        for i, (a, b) in enumerate(zip(ns, ns[1:])):
            _, _, d = project(p, a, b)
            if d > 12:
                continue
            if bearing is not None:
                t = abs(turn(bearing, heading(a, b)))
                if min(t, 180 - t) > 35:
                    continue
            if best is None or d < best[0]:
                best = (d, w, i)
    return best


def build_zone(z, pattern, stops):
    """A shared-traffic lane for one zone, in the bus's direction of travel."""
    xy = [to_xy(la, lo) for lo, la, _ in pattern["path"]]
    cum = [s for _, _, s in pattern["path"]]
    stop_s = next(x["s"] for x in pattern["stops"] if x["stop"] == z["stop"])
    out = {"id": z["id"], "kind": z["kind"], "stop": z["stop"], "pattern": pattern["id"],
           "stop_name": stops[z["stop"]]["stop_name"]}
    if z["kind"] == "curbside":
        a, b = max(0.0, stop_s - z["before_m"]), min(cum[-1], stop_s + z["after_m"])
        lane = slice_path(xy, cum, a, b)
        out.update(lane=encode(lane), offset_m=round(a, 1), shared=[round(a, 1), round(b, 1)],
                   pull_in_s=round(stop_s - CURB_TAPER_M, 1), merge_rear_m=round(stop_s - a - BUS_L, 1),
                   road="the route's street (GTFS shape)", rule="the bus pulls in to the curb and must find a gap to pull out")
        return out
    # Bay: where the route leaves the station's own roads and joins a public road.
    N, W = road_ways()
    dense = resample(xy, 2.0)
    dcum = cumulative(dense)
    join = None
    for i in range(1, len(dense) - 10):
        if dcum[i] > 600:
            break
        h = heading(dense[i - 1], dense[i + 1])
        hits = [nearest_way(dense[j], N, W, h) for j in (i, i + 4, i + 8)]
        if all(x and x[1]["tags"]["highway"] in PUBLIC for x in hits):
            join = (dcum[i], hits[0][1], h)
            break
    if join is None:
        raise SystemExit(f"{z['id']}: the route never joins a public road near {z['stop']}")
    s_join, way, h = join
    name = way["tags"].get("name", way["tags"]["highway"])
    # The road upstream of the join, walked back against the direction of travel.
    up = [point_at(dense, dcum, s_join)]
    p, bearing = up[0], (h + 180) % 360
    walked = 0.0
    while walked < z["before_m"]:
        hit = nearest_way(p, N, W)
        best = None
        for w in [w for w in W if w["tags"].get("name") == way["tags"].get("name")]:
            ns = [N[n] for n in w["nodes"] if n in N]
            for q in ns:
                d = dist(p, q)
                if 4.0 < d < 80.0:
                    t = abs(turn(bearing, heading(p, q)))
                    if t < 40 and (best is None or d < best[0]):
                        best = (d, q)
        if best is None:
            break
        walked += best[0]
        bearing = heading(p, best[1])
        p = best[1]
        up.append(p)
        del hit
    up.reverse()
    down = slice_path(xy, cum, s_join, min(cum[-1], s_join + z["after_m"]))
    lane = up + down[1:]
    lcum = cumulative(lane)
    upstream = lcum[len(up) - 1]
    out.update(lane=encode(lane), offset_m=round(s_join - upstream, 1), shared=[round(s_join, 1), round(s_join + z["after_m"], 1)],
               hold_s=round(s_join, 1), merge_rear_m=round(upstream, 1), road=name,
               rule=f"the bus stops at the bay exit and yields to {name} traffic (modelled stop line)")
    return out


def sec(x):
    h, m, s = map(int, x.split(":"))
    return h * 3600 + m * 60 + s


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def ion_platforms(lrt):
    """ION station name -> platform points, from the LRT GTFS."""
    out = defaultdict(list)
    for r in read(lrt / "stops.txt"):
        if r.get("location_type", "0") not in ("", "0"):
            continue
        name = r["stop_name"].split(" - ")[0].strip()
        out[name].append((float(r["stop_lat"]), float(r["stop_lon"])))
    return out


def project_along(xy, cum, p, start_s):
    """Distance along the polyline of the nearest point to p at or after start_s."""
    best = None
    for i in range(len(xy) - 1):
        if cum[i + 1] < start_s - 1e-6:
            continue
        f, _q, d = project(p, xy[i], xy[i + 1])
        s = cum[i] + f * (cum[i + 1] - cum[i])
        if s < start_s - 1e-6:
            continue
        if best is None or d < best[1] - 1e-9:
            best = (s, d)
        if best[1] < 5.0 and d > 200.0:
            break               # first close match; don't jump ahead to a later pass
    return best


def smooth(times, s):
    """Spread each GTFS minute change over the stops since the previous change, by distance."""
    out = list(times)
    i = 0
    n = len(times)
    while i < n - 1:
        j = i + 1
        while j < n - 1 and times[j] == times[i]:
            j += 1
        span = s[j] - s[i]
        for k in range(i + 1, j):
            f = (s[k] - s[i]) / span if span > 0 else (k - i) / (j - i)
            out[k] = round(times[i] + f * (times[j] - times[i]), 1)
        i = j
    return out


def build(gtfs, lrt, routes):
    on = {r["service_id"] for r in read(gtfs / "calendar_dates.txt") if r["date"] == DATE and r["exception_type"] == "1"}
    stops = {r["stop_id"]: r for r in read(gtfs / "stops.txt")}
    route_rows = {r["route_id"]: r for r in read(gtfs / "routes.txt")}
    all_trips = [r for r in read(gtfs / "trips.txt") if r["service_id"] in on]
    trips = [r for r in all_trips if r["route_id"] in routes]
    ids = {t["trip_id"] for t in trips}
    blocks_needed = {t["block_id"] for t in trips}
    block_trips = [t for t in all_trips if t["block_id"] in blocks_needed]
    bids = {t["trip_id"] for t in block_trips}
    st = defaultdict(list)
    for r in read(gtfs / "stop_times.txt"):
        if r["trip_id"] in bids:
            st[r["trip_id"]].append(r)
    for v in st.values():
        v.sort(key=lambda r: int(r["stop_sequence"]))
    shape_ids = {t["shape_id"] for t in trips}
    shapes = defaultdict(list)
    for r in read(gtfs / "shapes.txt"):
        if r["shape_id"] in shape_ids:
            shapes[r["shape_id"]].append((int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"])))
    ion = ion_platforms(lrt)
    report = []

    out_routes, out_stops, patterns = {}, {}, {}
    worst = 0.0
    for t in sorted(trips, key=lambda t: (t["route_id"], sec(st[t["trip_id"]][0]["departure_time"]))):
        rows = st[t["trip_id"]]
        seq = tuple(r["stop_id"] for r in rows)
        key = (t["route_id"], t["direction_id"], t["shape_id"], seq)
        if key not in patterns:
            raw = dedupe([to_xy(la, lo) for _, la, lo in sorted(shapes[t["shape_id"]])])
            # GTFS shapes zigzag between close points and turn corners in one vertex; a bus
            # follows a smooth, rounded path (as the corridor): simplified, then filleted.
            xy = dedupe(fillet(simplify(raw, SHAPE_TOLERANCE_M), BUS_TURN_RADIUS))
            pts = [to_latlon(x, y) for x, y in xy]
            cum = cumulative(xy)
            along, s0 = [], 0.0
            for sid in seq:
                p = to_xy(float(stops[sid]["stop_lat"]), float(stops[sid]["stop_lon"]))
                hit = project_along(xy, cum, p, s0)
                if hit is None or hit[1] > STOP_SHAPE_M:
                    raise SystemExit(f"route {t['route_id']} trip {t['trip_id']}: stop {sid} is "
                                     f"{'off' if hit is None else f'{hit[1]:.0f} m from'} shape {t['shape_id']}")
                worst = max(worst, hit[1])
                along.append(round(hit[0], 1))
                s0 = hit[0]
            n = sum(1 for k in patterns if k[0] == t["route_id"] and k[1] == t["direction_id"]) + 1
            pid = f"{t['route_id']}-{t['direction_id']}-{n}"
            patterns[key] = {"id": pid, "route": t["route_id"], "direction": int(t["direction_id"]),
                             "headsign": t["trip_headsign"], "shape_id": t["shape_id"],
                             "stops": [{"stop": sid, "s": s} for sid, s in zip(seq, along)],
                             "length_m": round(cum[-1], 1),
                             "path": [[round(lo, 6), round(la, 6), round(s, 1)] for (la, lo), s in zip(pts, cum)],
                             "trips": 0}
        pat = patterns[key]
        pat["trips"] += 1
        arr = [sec(r["arrival_time"]) for r in rows]
        dep = [sec(r["departure_time"]) for r in rows]
        if any(b < a for a, b in zip(dep, dep[1:])):
            raise SystemExit(f"trip {t['trip_id']}: times go backwards")
        s = [x["s"] for x in pat["stops"]]
        r = out_routes.setdefault(t["route_id"], {
            "id": t["route_id"], "short_name": route_rows[t["route_id"]]["route_short_name"],
            "name": route_rows[t["route_id"]]["route_long_name"], "patterns": [], "trips": []})
        r["trips"].append({"trip_id": t["trip_id"], "block": t["block_id"], "pattern": pat["id"],
                           "service": t["service_id"], "headsign": t["trip_headsign"],
                           "gtfs": [[a, b] for a, b in zip(arr, dep)], "sched": smooth(dep, s)})
        for sid in seq:
            out_stops.setdefault(sid, None)
    for pat in patterns.values():
        dense = resample([to_xy(la, lo) for lo, la, _ in pat["path"]], 2.0)
        pat["curves"] = curve_limits(dense)
        out_routes[pat["route"]]["patterns"].append(pat)
    zones = []
    for z in ZONES:
        pat = next((p for p in patterns.values() if p["route"] in routes and p["direction"] == z["direction"]
                    and p["trips"] > 50 and any(x["stop"] == z["stop"] for x in p["stops"])), None)
        if pat is not None:
            zones.append(build_zone(z, pat, stops))

    for sid in out_stops:
        r = stops[sid]
        la, lo = float(r["stop_lat"]), float(r["stop_lon"])
        entry = {"name": r["stop_name"], "lon": round(lo, 6), "lat": round(la, 6)}
        best = None
        for name, plats in ion.items():
            if name.replace(" Station", "").lower() not in r["stop_name"].lower():
                continue        # a station stop names its station; nearby street stops are not linked
            d = min(dist(to_xy(la, lo), to_xy(pa, po)) for pa, po in plats)
            if d <= ION_WALK_MAX_M and (best is None or d < best[1]):
                best = (name, d)
        if best:
            entry["ion"] = {"station": best[0], "walk_m": round(best[1])}
        if sid in CORRIDOR_STOPS:
            entry["corridor"] = CORRIDOR_STOPS[sid]
        out_stops[sid] = entry

    blocks = {}
    for b in sorted(blocks_needed):
        ts = sorted((t for t in block_trips if t["block_id"] == b), key=lambda t: sec(st[t["trip_id"]][0]["departure_time"]))
        rows = []
        for t in ts:
            ss = st[t["trip_id"]]
            rows.append({"trip_id": t["trip_id"], "route": t["route_id"], "modelled": t["trip_id"] in ids,
                         "from": ss[0]["stop_id"], "to": ss[-1]["stop_id"],
                         "start": sec(ss[0]["departure_time"]), "end": sec(ss[-1]["arrival_time"])})
        for x, y in zip(rows, rows[1:]):
            if y["start"] < x["end"]:
                raise SystemExit(f"block {b}: trip {y['trip_id']} starts before {x['trip_id']} ends")
        blocks[b] = rows

    # Every block that runs on the day, all routes: the regional fleet assigns a vehicle to each.
    day = defaultdict(list)
    by_trip = {t["trip_id"]: t for t in all_trips}
    ends = {}
    with open(gtfs / "stop_times.txt", newline="") as f:
        for r in csv.DictReader(f):
            if r["trip_id"] not in by_trip:
                continue
            q = int(r["stop_sequence"])
            e = ends.setdefault(r["trip_id"], [None, None])
            if e[0] is None or q < e[0][0]:
                e[0] = (q, r["stop_id"], sec(r["departure_time"]))
            if e[1] is None or q > e[1][0]:
                e[1] = (q, r["stop_id"], sec(r["arrival_time"]))
    for tid, (a, b) in ends.items():
        day[by_trip[tid]["block_id"]].append((a[2], b[2], a[1], b[1], by_trip[tid]["route_id"]))
    day_blocks = {}
    for bid, rows in sorted(day.items()):
        rows.sort()
        f, l = stops[rows[0][2]], stops[rows[-1][3]]
        day_blocks[bid] = {"start": rows[0][0], "end": rows[-1][1], "routes": sorted({x[4] for x in rows}),
                           "first": [rows[0][2], round(float(f["stop_lat"]), 6), round(float(f["stop_lon"]), 6)],
                           "last": [rows[-1][3], round(float(l["stop_lat"]), 6), round(float(l["stop_lon"]), 6)]}
    report.append(f"{len(day_blocks)} blocks run on {DATE} (all routes)")

    for rid, r in sorted(out_routes.items()):
        bl = {t["block"] for t in r["trips"]}
        inter = sorted({x["route"] for b in bl for x in blocks[b]} - {rid})
        report.append(f"route {rid} {r['name']}: {len(r['trips'])} trips on {DATE}, {len(bl)} blocks, "
                      f"{len(r['patterns'])} variants" + (f"; blocks also run route(s) {', '.join(inter)}" if inter else ""))
        for p in r["patterns"]:
            first, last = stops[p["stops"][0]["stop"]]["stop_name"], stops[p["stops"][-1]["stop"]]["stop_name"]
            report.append(f"  {p['id']}: {first} -> {last}, {len(p['stops'])} stops, {p['length_m'] / 1000:.1f} km, "
                          f"{p['trips']} trips")
    report.append(f"stops within {worst:.1f} m of their shapes")
    for z in zones:
        report.append(f"zone {z['id']}: {z['kind']} at {z['stop_name']} ({z['stop']}) on {z['pattern']}, "
                      f"lane {z['lane'][-1][2]:.0f} m along {z['road']}, shared from {z['shared'][0]:.0f} m"
                      + (f"; bay exit at {z['hold_s']:.0f} m" if z["kind"] == "bay" else ""))
    report.append("ION links: " + ", ".join(f"{s} {v['ion']['station']} ({v['ion']['walk_m']} m)"
                                            for s, v in out_stops.items() if "ion" in v))
    return {"date": DATE, "attribution": "GRT GTFS bus and LRT feeds (Region of Waterloo Open Data Licence)",
            "notes": {"sched": "GTFS departure times with each minute change spread over the stops since the "
                               "previous change in proportion to distance (seconds after midnight)",
                      "gtfs": "[arrival, departure] seconds after midnight, as published"},
            "routes": out_routes, "stops": out_stops, "blocks": blocks, "day_blocks": day_blocks, "zones": zones,
            "modelled": {"curves": f"speed caps from path curvature at {BUS_LATERAL} m/s^2 lateral (as the corridor)",
                         "zones": "shared-traffic lanes: geometry from GTFS shapes and OSM; car flows are a setting"}}, report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--routes", default="7")
    parser.add_argument("--gtfs", default="/home/user/gtfs/bus")
    parser.add_argument("--lrt", default="/home/user/gtfs/lrt")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    out, report = build(Path(args.gtfs), Path(args.lrt), set(args.routes.split(",")))
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
