"""Build the University of Waterloo Station bus roadway (Transit Plaza Road):
lanes, platforms and berths, stop lines, the ION level crossing, the exit to
Phillip Street, and the GRT buses that use it in one weekday hour.

Usage:
    python3 -I build_uw_station.py [--gtfs /home/user/gtfs] [--check]

Sources:
- sources/uw-station-osm.json (OpenStreetMap, ODbL): the Transit Plaza Road
  ways (highway=service, access=no, bus=yes, lanes=2, two-way), the GRT
  platform areas (refs 3, 4, 5, 6) and stop poles, the GO platforms, the
  all-way stops, crossings and the ION level crossing.
- GRT GTFS (Region of Waterloo Open Data Licence): stops 1078, 1223, 1260,
  1262 and 1264 with their platform_code, and every call at them between
  08:00 and 09:00 on Wednesday 2 December 2026, including pickup_type=1
  (drop-off only) calls.
- GRT's University of Waterloo Station map (station-maps page, uw-station.pdf):
  the same stops on the same sides of the roadway; used as a cross-check.

Geometry. The roadway runs south-west to north-east, from the Ring Road
all-way stop (s = 0) to Phillip Street. OSM maps two travel lanes on its
centreline; the platform edges are 7.2–7.5 m from it, so each side has a
curb berth lane outside its through lane (lane widths modelled at 3.5 m).
Buses travelling north-east use the south-east side (platforms 4 and 6),
those travelling south-west the north-west side (platforms 3 and 5).

Berths. Each platform's berth positions run back from its stop pole (the
front of the first bus) in steps of a 12.2 m bus plus 6 m to swing out past the bus in front (modelled), as far as
the mapped platform reaches.

Contains information provided by the Regional Municipality of Waterloo under
the Region's Open Data Licence. Map data © OpenStreetMap contributors (ODbL).
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import cumulative, project, to_latlon, to_xy  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = HERE / "sources" / "uw-station-osm.json"
OUTPUTS = [ROOT / "backend" / "uw_station.json", ROOT / "frontend" / "lib" / "geo" / "uw-station.json"]

ROAD = [1040958965, 137281106, 1361353506, 1361353507, 1021015929, 1361353508, 1021015930, 1046609782,
        1046609786, 1040958964]
RING_STOP, PHILLIP = 298025667, 298025663
LEVEL_CROSSING = (4211142321, 1715025806)
MID_STOP = 8001833438
CROSSWALKS = (1715025801, 9412123864, 9412123865, 9536179447)
PLATFORMS = {1020484623: "3", 1020484622: "4", 1020484625: "5", 1020484624: "6"}
GO_PLATFORMS = {1528331878: "GO 6", 1020484620: "GO 7"}
STOP_POLES = {"1260": 9536179462, "1262": 9536179463, "1223": 9536179464, "1264": 9536179465, "1078": 9536179466}
BUS_LENGTH = 12.2          # GRT 40-ft bus (modelled for every route)
BUS_WIDTH = 2.6
BERTH_GAP = 6.0          # room to swing out of a stacked berth (modelled)
LANE_W = 3.5
DATE, HOUR = "20261202", ("08:00:00", "09:00:00")
STOPS = ("1078", "1223", "1260", "1262", "1264")


def load():
    data = json.loads(SOURCE.read_text())
    nodes = {e["id"]: e for e in data["elements"] if e["type"] == "node"}
    ways = {e["id"]: e for e in data["elements"] if e["type"] == "way"}
    return nodes, ways


def encode(points):
    out = []
    for (x, y), s in zip(points, cumulative(points)):
        lat, lon = to_latlon(x, y)
        out.append([round(lon, 7), round(lat, 7), round(s, 2)])
    return out


def gtfs_calls(root):
    """Every call at the station's stops in the hour, by trip, with route and block."""
    services = set()
    with open(root / "calendar_dates.txt") as f:
        for r in csv.DictReader(f):
            if r["date"] == DATE and r["exception_type"] == "1":
                services.add(r["service_id"])
    trips = {}
    with open(root / "trips.txt") as f:
        for r in csv.DictReader(f):
            if r["service_id"] in services:
                trips[r["trip_id"]] = r
    calls = defaultdict(list)
    stops = {}
    with open(root / "stops.txt") as f:
        for r in csv.DictReader(f):
            if r["stop_id"] in STOPS:
                stops[r["stop_id"]] = {"name": r["stop_name"], "platform": r["platform_code"],
                                       "lon": float(r["stop_lon"]), "lat": float(r["stop_lat"])}
    with open(root / "stop_times.txt") as f:
        for r in csv.DictReader(f):
            if r["trip_id"] in trips and r["stop_id"] in STOPS:
                calls[r["trip_id"]].append(r)
    out = []
    for trip_id, cs in calls.items():
        if not any(HOUR[0] <= c["arrival_time"] < HOUR[1] or HOUR[0] <= c["departure_time"] < HOUR[1] for c in cs):
            continue
        t = trips[trip_id]
        def sec(x):
            h, m, s = (int(v) for v in x.split(":"))
            return h * 3600 + m * 60 + s - 8 * 3600
        out.append({
            "trip_id": trip_id, "route": t["route_id"], "headsign": t["trip_headsign"], "block": t["block_id"],
            "calls": [{"stop": c["stop_id"], "arrive_s": sec(c["arrival_time"]), "depart_s": sec(c["departure_time"]),
                       "drop_off_only": c["pickup_type"] == "1", "first": c["stop_sequence"] == "1"}
                      for c in sorted(cs, key=lambda c: int(c["stop_sequence"]))],
        })
    out.sort(key=lambda x: (x["calls"][0]["arrive_s"], x["trip_id"]))
    return out, stops


def build(gtfs_root):
    nodes, ways = load()
    xy = {n: to_xy(e["lat"], e["lon"]) for n, e in nodes.items()}
    seq = []
    for wid in ROAD:
        ns = ways[wid]["nodes"]
        if seq and ns[0] != seq[-1]:
            ns = list(reversed(ns))
        if seq and ns[0] != seq[-1]:
            raise RuntimeError(f"Transit Plaza Road way {wid} does not join the previous one")
        seq += ns if not seq else ns[1:]
    if seq[0] != RING_STOP or seq[-1] != PHILLIP:
        raise RuntimeError("Transit Plaza Road does not run from the Ring Road stop to Phillip Street")
    pts = [xy[n] for n in seq]
    cum = cumulative(pts)
    report = [f"Transit Plaza Road: {cum[-1]:.1f} m from the Ring Road all-way stop to Phillip Street over {len(ROAD)} ways"]

    def locate(p):
        best = None
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            t, _, d = project(p, a, b)
            cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
            if best is None or d < best[1]:
                best = (cum[i] + t * (cum[i + 1] - cum[i]), d, "nw" if cross > 0 else "se")
        return best

    at = {n: cum[i] for i, n in enumerate(seq)}
    features = {
        "ring_stop": 0.0,
        "level_crossing": [round(at[n], 1) for n in LEVEL_CROSSING],
        "mid_stop": round(at[MID_STOP], 1),
        "crosswalks": [round(at[n], 1) for n in CROSSWALKS],
        "exit": round(cum[-1], 1),
    }
    # Stop lines (modelled): 2 m before the first crosswalk or rail in the direction of travel.
    lc0, lc1 = sorted(features["level_crossing"])
    cw = sorted(features["crosswalks"])
    mid = features["mid_stop"]
    stop_lines = {
        "se": {"level_crossing": round(lc0 - 6.0, 1), "mid_stop": round(max(c for c in cw if c < mid) - 2.0, 1),
               "exit": round(max(cw) - 2.0, 1)},
        "nw": {"level_crossing": round(lc1 + 6.0, 1), "mid_stop": round(min(c for c in cw if c > mid) + 2.0, 1),
               "ring_stop": round(min(c for c in cw) + 2.0, 1)},
    }
    calls, gstops = gtfs_calls(gtfs_root)
    platforms = []
    for wid, ref in PLATFORMS.items():
        located = [locate(xy[n]) for n in ways[wid]["nodes"]]
        sides = {x[2] for x in located}
        if len(sides) != 1:
            raise RuntimeError(f"platform {ref} straddles the roadway")
        side = sides.pop()
        s0, s1 = min(x[0] for x in located), max(x[0] for x in located)
        edge = min(x[1] for x in located)
        poles = []
        for stop_id, node in STOP_POLES.items():
            s, off, pside = locate(xy[node])
            if pside == side and s0 - 3 <= s <= s1 + 3:
                if gstops.get(stop_id, {}).get("platform") != ref:
                    raise RuntimeError(f"stop {stop_id}: GTFS platform {gstops.get(stop_id, {}).get('platform')} "
                                       f"but OSM pole is on platform {ref}")
                poles.append({"stop": stop_id, "s": round(s, 1)})
        berths = []
        # A stop's positions run back from its pole, up to the next pole behind it
        # (in the direction of travel) or the platform's end.
        for pole in sorted(poles, key=lambda p: p["s"], reverse=(side == "se")):
            others = [q["s"] for q in poles if q is not pole]
            limit = (max([x for x in others if x < pole["s"]], default=s0 - 0.5) if side == "se"
                     else min([x for x in others if x > pole["s"]], default=s1 + 0.5))
            k = 0
            while True:
                # South-east side: buses face +s, so the berth runs back (lower s) from the pole.
                front = pole["s"] - k * (BUS_LENGTH + BERTH_GAP) if side == "se" else pole["s"] + k * (BUS_LENGTH + BERTH_GAP)
                rear = front - BUS_LENGTH if side == "se" else front + BUS_LENGTH
                if not (s0 - 0.5 <= min(front, rear) and max(front, rear) <= s1 + 0.5):
                    break
                if (side == "se" and rear < limit) or (side == "nw" and rear > limit):
                    break
                if any(min(front, rear) < max(b["front"], b["rear"]) and max(front, rear) > min(b["front"], b["rear"])
                       for b in berths):
                    break
                berths.append({"id": f"P{ref}-{pole['stop']}-{k + 1}", "stop": pole["stop"], "position": k + 1,
                               "front": round(front, 1), "rear": round(rear, 1)})
                k += 1
        platforms.append({"ref": ref, "side": side, "s0": round(s0, 1), "s1": round(s1, 1), "edge_m": round(edge, 1),
                          "osm_way": wid, "poles": poles, "berths": berths})
        report.append(f"platform {ref} ({side} side): {s1 - s0:.0f} m at {s0:.0f}–{s1:.0f} m, edge {edge:.1f} m from the "
                      f"centreline; stops {', '.join(p['stop'] for p in poles)}; {len(berths)} berth position(s)")
    go = []
    for wid, ref in GO_PLATFORMS.items():
        located = [locate(xy[n]) for n in ways[wid]["nodes"]]
        go.append({"ref": ref, "s0": round(min(x[0] for x in located), 1), "s1": round(max(x[0] for x in located), 1),
                   "side": located[0][2], "osm_way": wid})
    # Drop-off-only calls per route and stop (GTFS pickup_type = 1).
    drop_only = sorted({(c["stop"], t["route"]) for t in calls for c in t["calls"] if c["drop_off_only"]})
    report.append(f"GTFS {DATE} 08:00–09:00: {len(calls)} trips call at the station; drop-off-only calls: "
                  + ", ".join(f"route {r} at {s}" for s, r in drop_only))
    side_of = {p["stop"]: pl["side"] for pl in platforms for p in pl["poles"]}
    return {
        "attribution": "Transit Plaza Road, platforms and crossings © OpenStreetMap contributors (ODbL); stops, platform "
                       "codes and trips from GRT GTFS: contains information provided by the Regional Municipality of "
                       "Waterloo under the Region's Open Data Licence.",
        "name": "University of Waterloo Station", "road": "Transit Plaza Road",
        "path": encode(pts), "length_m": round(cum[-1], 1),
        "lanes": {"through_offset_m": LANE_W / 2, "berth_offset_m": round(LANE_W * 1.5, 2), "lane_width_m": LANE_W,
                  "note": "two travel lanes mapped (lanes=2); curb berth lanes inferred from the platform edges 7.2–7.5 m "
                          "from the centreline"},
        "bus": {"length_m": BUS_LENGTH, "width_m": BUS_WIDTH, "berth_gap_m": BERTH_GAP},
        "features": features, "stop_lines": stop_lines,
        "platforms": platforms, "go_platforms": go,
        "stops": {k: {**v, "side": side_of.get(k)} for k, v in gstops.items()},
        "gtfs": {"date": DATE, "hour": "08:00-09:00", "trips": calls,
                 "drop_off_only": [{"stop": s, "route": r} for s, r in drop_only]},
    }, report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gtfs", default="/home/user/gtfs/bus")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    out, report = build(Path(args.gtfs))
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
