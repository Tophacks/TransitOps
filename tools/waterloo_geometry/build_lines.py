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
from geo import cumulative, dist, project, to_xy  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUTPUTS = [ROOT / "backend" / "lines.json", ROOT / "frontend" / "lib" / "geo" / "lines.json"]
DATE = "20261202"
STOP_SHAPE_M = 40.0
ION_WALK_MAX_M = 300.0
CORRIDOR_STOPS = {"1078": "UW", "4048": "WATERLOO_SQUARE", "2540": "GRAND_RIVER_HOSPITAL", "1901": "CENTRAL"}


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
            pts = [(la, lo) for _, la, lo in sorted(shapes[t["shape_id"]])]
            xy = [to_xy(la, lo) for la, lo in pts]
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
        out_routes[pat["route"]]["patterns"].append(pat)

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
    report.append("ION links: " + ", ".join(f"{s} {v['ion']['station']} ({v['ion']['walk_m']} m)"
                                            for s, v in out_stops.items() if "ion" in v))
    return {"date": DATE, "attribution": "GRT GTFS bus and LRT feeds (Region of Waterloo Open Data Licence)",
            "notes": {"sched": "GTFS departure times with each minute change spread over the stops since the "
                               "previous change in proportion to distance (seconds after midnight)",
                      "gtfs": "[arrival, departure] seconds after midnight, as published"},
            "routes": out_routes, "stops": out_stops, "blocks": blocks}, report


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
