"""Build the ION operating network: both running tracks, every mapped crossover
and the single-track diversions they support.

Usage:
    python3 -I build_network.py [--check]

Reads sources/ion-network-osm.json (OpenStreetMap, ODbL: every ION track way,
crossover and Dutton yard track along the line), the corridor's GTFS line
paths (backend/waterloo_corridor.json) and the terminal layouts
(backend/ion_terminals.json), and writes backend/ion_network.json and an
identical frontend/lib/geo/ion-network.json.

Tracks. OSM tags each ION way with the direction that normally uses it
(direction=north / south). The builder follows each direction's ways from the
Fairway exit switch to the Conestoga entry switch (and back), without using a
crossover or yard track, and fails if either direction is not continuous.

Crossovers. A single crossover between two parallel tracks can be taken by a
train in one direction from track A to track B, and in the other direction
from B to A; anything else needs a reversal. The builder finds which end of
each crossover is further north along the line and so which moves it supports.
A crossover that is not electrified, or not operated by GRT, is in the network
but disabled for passenger moves (south of Mill: CN, electrified=no).

Diversions (single-track working). With one track closed, trains of that
direction can use the other track only between a crossover that lets them
cross over and a later one that lets them cross back, both in their direction
of travel. The builder lists every such pair of enabled crossovers and builds
the complete diversion route: from the last station before the first
crossover, onto the other track, past that track's platforms, back over the
second crossover, to the first station after it. Each route is one path (GTFS
line joined onto the OSM track where they agree within 2.5 m and 6 degrees),
with its stops, its speed zones and the shared section it occupies.

Contains information provided by the Regional Municipality of Waterloo under
the Region's Open Data Licence. Map data © OpenStreetMap contributors (ODbL).
"""
import argparse
import heapq
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import cumulative, dist, heading, project, to_latlon, to_xy, turn  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = HERE / "sources" / "ion-network-osm.json"
CORRIDOR = ROOT / "backend" / "waterloo_corridor.json"
TERMINALS = ROOT / "backend" / "ion_terminals.json"
OUTPUTS = [ROOT / "backend" / "ion_network.json", ROOT / "frontend" / "lib" / "geo" / "ion-network.json"]

ION_LENGTH = 41.8
FOUL_M = 8.0
CROSSOVER_SPEED = 25 / 3.6     # modelled where no maxspeed is tagged
SPLICE_M = 30.0
HANDOVER_WINDOW = (40.0, 600.0)  # metres from the switch to look for a join onto the GTFS line
STATION_MATCH_M = 5.0          # a platform whose OSM and GTFS positions agree within this can start or end a diversion
# Crossover sites named in the brief (OSM way -> place).
SITES = {
    750968494: "Northfield Station",
    754590776: "South of Dutton, near Albert Street",
    582281483: "Uptown, Erb and Caroline",
    754590777: "Grand River Hospital toward Central, near Green Street",
    754590778: "Kitchener Market toward Borden, near Betzner Avenue",
    754590784: "South of Mill",
}
FAIRWAY_EXIT, FAIRWAY_ENTRY = 7049507415, 7049507417
CONESTOGA_EXIT, CONESTOGA_ENTRY = 7805105215, 7805105217


def load():
    data = json.loads(SOURCE.read_text())
    nodes = {e["id"]: e for e in data["elements"] if e["type"] == "node"}
    ways = {e["id"]: e for e in data["elements"] if e["type"] == "way"}
    return data, nodes, ways


def encode(points):
    cum = cumulative(points)
    out = []
    for (x, y), s in zip(points, cum):
        lat, lon = to_latlon(x, y)
        out.append([round(lon, 7), round(lat, 7), round(s, 2)])
    return out


def point_at(points, cum, s):
    s = max(0.0, min(cum[-1], s))
    lo, hi = 0, len(cum) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cum[mid] <= s:
            lo = mid
        else:
            hi = mid
    span = cum[hi] - cum[lo]
    f = 0.0 if span == 0 else (s - cum[lo]) / span
    a, b = points[lo], points[hi]
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


def cut(points, s0, s1):
    cum = cumulative(points)
    inner = [p for p, s in zip(points, cum) if s0 < s < s1]
    return [point_at(points, cum, s0)] + inner + [point_at(points, cum, s1)]


def locate(points, p, cum=None):
    cum = cum or cumulative(points)
    best = None
    for i, (a, b) in enumerate(zip(points, points[1:])):
        t, _, d = project(p, a, b)
        if best is None or d < best[1]:
            best = (cum[i] + t * (cum[i + 1] - cum[i]), d)
    return best


def curve_caps(points, lateral=1.0, cap=16.7, step=2.0):
    """[s, v] where curvature limits the speed (as build_corridor/build_fairway)."""
    cum = cumulative(points)
    n = max(2, int(cum[-1] / step))
    dense = [point_at(points, cum, cum[-1] * i / n) for i in range(n + 1)]
    dcum = [cum[-1] * i / n for i in range(n + 1)]
    out = []
    for i in range(2, len(dense) - 2):
        a, b, c = dense[i - 2], dense[i], dense[i + 2]
        ab, bc, ca = dist(a, b), dist(b, c), dist(c, a)
        cross = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
        if cross < 1e-6:
            continue
        v = math.sqrt(lateral * ab * bc * ca / (2 * cross))
        if v < cap:
            out.append([round(dcum[i], 1), round(v, 2)])
    return out


class Network:
    def __init__(self):
        self.data, self.nodes, self.ways = load()
        self.xy = {n: to_xy(e["lat"], e["lon"]) for n, e in self.nodes.items()}
        self.adj = {}
        for wid, w in self.ways.items():
            for a, b in zip(w["nodes"], w["nodes"][1:]):
                d = dist(self.xy[a], self.xy[b])
                self.adj.setdefault(a, []).append((b, d, wid))
                self.adj.setdefault(b, []).append((a, d, wid))
        self.corridor = json.loads(CORRIDOR.read_text())
        self.terminals = json.loads(TERMINALS.read_text())["terminals"]

    def track(self, direction, start, end):
        """Node sequence of a direction's running track (no crossover or yard ways)."""
        dist_to = {start: 0.0}
        prev = {}
        queue = [(0.0, start)]
        while queue:
            d, u = heapq.heappop(queue)
            if u == end:
                break
            if d > dist_to[u]:
                continue
            for v, length, wid in self.adj.get(u, []):
                tags = self.ways[wid]["tags"]
                if tags.get("service") in ("crossover", "yard"):
                    continue
                if tags.get("direction") not in (None, direction):
                    continue
                if d + length < dist_to.get(v, math.inf):
                    dist_to[v] = d + length
                    prev[v] = u
                    heapq.heappush(queue, (d + length, v))
        if end not in dist_to:
            raise RuntimeError(f"the {direction} track is not continuous")
        seq = [end]
        while seq[-1] != start:
            seq.append(prev[seq[-1]])
        return list(reversed(seq))


def handover(gtfs, osm, osm_s, before, report_label, report):
    """Where to join the GTFS line path and an OSM track near osm_s.

    before=True: the join is on the approach (GTFS -> OSM), searched before
    osm_s; else on the way out (OSM -> GTFS), after it. Returns
    (gtfs_s, osm_s_at_join)."""
    gpts, gcum = gtfs
    opts, ocum = osm
    best = None
    lo, hi = HANDOVER_WINDOW
    s = osm_s - hi if before else osm_s + lo
    end = osm_s - lo if before else osm_s + hi
    while s <= end:
        if 0 <= s <= ocum[-1]:
            p = point_at(opts, ocum, s)
            g_s, off = locate(gpts, p, gcum)
            if off <= 2.5:
                a = point_at(opts, ocum, s - 20)
                b = point_at(opts, ocum, s + 20)
                ga = point_at(gpts, gcum, g_s - 20)
                gb = point_at(gpts, gcum, g_s + 20)
                bend = abs(turn(heading(a, b), heading(ga, gb)))
                score = off + bend / 2 + abs(s - osm_s) / 400
                if bend <= 6 and (best is None or score < best[2]):
                    best = (g_s, s, score, off, bend)
        s += 5.0
    if best is None:
        raise RuntimeError(f"no join between the GTFS line and the OSM track {report_label}")
    report.append(f"    join {report_label}: GTFS {best[0]:.0f} m <-> OSM {best[1]:.0f} m ({best[3]:.1f} m, {best[4]:.1f} deg)")
    return best[0], best[1]


def build():
    net = Network()
    report = []
    xy = net.xy
    tracks = {
        "north": net.track("north", FAIRWAY_EXIT, CONESTOGA_ENTRY),
        "south": net.track("south", CONESTOGA_EXIT, FAIRWAY_ENTRY),
    }
    pts = {k: [xy[n] for n in v] for k, v in tracks.items()}
    cums = {k: cumulative(v) for k, v in pts.items()}
    index = {k: {n: cums[k][i] for i, n in enumerate(v)} for k, v in tracks.items()}
    for k in tracks:
        report.append(f"{k} track: {cums[k][-1]:.0f} m over OSM ways tagged direction={k} or untagged")
    # Line position (metres along the north track from Fairway) for ordering.
    def north_s(p):
        return locate(pts["north"], p, cums["north"])[0]

    gtfs = {d: ([to_xy(p[1], p[0]) for p in net.corridor["ion"][d]["path"]], [p[2] for p in net.corridor["ion"][d]["path"]])
            for d in ("north", "south")}
    # Stations on each track: that direction's GTFS stops, projected onto its OSM track.
    stations = {}
    for d in ("north", "south"):
        stops = []
        for st in net.corridor["ion"][d]["stations"]:
            g = point_at(*gtfs[d], st["at_m"]) if False else point_at(gtfs[d][0], gtfs[d][1], st["at_m"])
            s, off = locate(pts[d], g, cums[d])
            stops.append({"name": st["name"], "gtfs_m": st["at_m"], "track_m": round(s, 1), "offset_m": round(off, 1)})
        stations[d] = stops
    crossovers = []
    for wid, w in net.ways.items():
        if w["tags"].get("service") != "crossover" or wid not in SITES:
            continue
        ends = []
        for n in (w["nodes"][0], w["nodes"][-1]):
            track = "north" if n in index["north"] else "south" if n in index["south"] else None
            if track is None:
                raise RuntimeError(f"crossover {wid}: end {n} is on neither running track")
            ends.append({"node": n, "track": track, "track_m": round(index[track][n], 2),
                         "line_m": round(north_s(xy[n]), 2), "lon": net.nodes[n]["lon"], "lat": net.nodes[n]["lat"]})
        ends.sort(key=lambda e: e["line_m"])
        lo, hi = ends
        if lo["track"] == hi["track"]:
            raise RuntimeError(f"crossover {wid} joins a track to itself")
        tags = w["tags"]
        electrified = tags.get("electrified") not in (None, "no")
        operator = tags.get("operator", "")
        passenger = electrified
        why = "electrified ION crossover" if passenger else "not electrified (electrified=no); mapped as CN-operated per the brief: passenger moves disabled until verified"
        path = [xy[n] for n in w["nodes"]]
        if dist(path[0], xy[lo["node"]]) > 0.01:
            path.reverse()
        crossovers.append({
            "way": wid, "site": SITES[wid], "electrified": electrified, "operator": operator,
            "passenger": passenger, "status": why,
            "ends": ends,
            # A northbound train (line position increasing) can cross from the
            # lower end's track to the upper end's; a southbound one the reverse.
            "moves": [{"travel": "north", "from": lo["track"], "to": hi["track"]},
                      {"travel": "south", "from": hi["track"], "to": lo["track"]}],
            "maxspeed_mps": float(tags["maxspeed"]) / 3.6 if "maxspeed" in tags else None,
            "path": encode(path),   # from the lower end to the upper end
            "length_m": round(cumulative(path)[-1], 2),
        })
        report.append(f"crossover {wid} ({SITES[wid]}): {lo['track']} track @ {lo['line_m']:.0f} m -> {hi['track']} "
                      f"track @ {hi['line_m']:.0f} m; northbound {lo['track']}->{hi['track']}, southbound "
                      f"{hi['track']}->{lo['track']}; {'enabled' if passenger else 'DISABLED'}")
    crossovers.sort(key=lambda c: c["ends"][0]["line_m"])
    if len(crossovers) != len(SITES):
        raise RuntimeError("a named crossover is missing from the source")

    # Diversions: close track T; trains that use T cross to the other track U
    # at a crossover supporting T->U in their direction, and back at a later
    # one supporting U->T. Enabled crossovers only.
    diversions = []
    enabled = [c for c in crossovers if c["passenger"]]
    for closed in ("north", "south"):
        other = "south" if closed == "north" else "north"
        travel = closed                       # trains that normally use the closed track
        sign = 1 if travel == "north" else -1
        def supports(c, frm, to):
            return any(m["travel"] == travel and m["from"] == frm and m["to"] == to for m in c["moves"])
        order = sorted(enabled, key=lambda c: sign * c["ends"][0]["line_m"])
        for i, first in enumerate(order):
            if not supports(first, closed, other):
                continue
            for second in order[i + 1:]:
                if not supports(second, other, closed):
                    continue
                diversions.append(build_diversion(net, closed, other, travel, first, second, pts, cums, index,
                                                  gtfs, stations, report))
    for c in crossovers:
        if not c["passenger"]:
            continue
    report.append(f"diversions: {', '.join(d['id'] for d in diversions) or 'none'}")
    return {
        "attribution": "ION track and crossovers © OpenStreetMap contributors (ODbL); line paths and stations from GRT GTFS: "
                       "contains information provided by the Regional Municipality of Waterloo under the Region's Open Data Licence.",
        "train_length_m": ION_LENGTH,
        "tracks": {k: {"path": encode(pts[k]), "length_m": round(cums[k][-1], 1)} for k in tracks},
        "stations": stations,
        "crossovers": crossovers,
        "diversions": diversions,
    }, report


def build_diversion(net, closed, other, travel, first, second, pts, cums, index, gtfs, stations, report):
    """The complete route for trains of `travel` with `closed` shut between two crossovers.

    It runs from the last platform before the first crossover to the first
    platform after the second, along the OSM tracks: the own track, over the
    first crossover, the other track against its usual direction, back over
    the second crossover, the own track. It starts and ends at station
    stopping points whose OSM and GTFS positions agree within STATION_MATCH_M,
    so trains join it from the GTFS line where they stand."""
    xy = net.xy
    by = lambda c: {e["track"]: e for e in c["ends"]}  # noqa: E731
    e1, e2 = by(first), by(second)
    s_in = e1[closed]["track_m"]       # leave the closed track here
    s_on = e1[other]["track_m"]        # join the other track here
    s_off = e2[other]["track_m"]       # leave the other track here
    s_back = e2[closed]["track_m"]     # rejoin the closed track here
    label = f"{closed}-closed:{first['way']}-{second['way']}"
    own = stations[travel]
    half = ION_LENGTH / 2
    # Platforms on the own track (positions increase in the direction of travel).
    usable = [st for st in own if st["offset_m"] <= STATION_MATCH_M]
    before = [st for st in usable if st["track_m"] + half < s_in - 5]
    after = [st for st in usable if st["track_m"] - half > s_back + 5]
    if not before or not after:
        raise RuntimeError(f"{label}: no matched platform before or after the section")
    entry, exit_ = before[-1], after[0]
    own_pts, own_cum = pts[closed], cums[closed]
    oth_pts, oth_cum = pts[other], cums[other]
    nose_in, nose_out = entry["track_m"] + half, exit_["track_m"] + half
    part1 = cut(own_pts, nose_in, s_in)
    cx1 = [to_xy(p[1], p[0]) for p in first["path"]]
    if dist(cx1[0], part1[-1]) > dist(cx1[-1], part1[-1]):
        cx1.reverse()
    lo, hi = sorted((s_on, s_off))
    run = cut(oth_pts, lo, hi)
    if s_on > s_off:
        run = list(reversed(run))
    cx2 = [to_xy(p[1], p[0]) for p in second["path"]]
    if dist(cx2[0], run[-1]) > dist(cx2[-1], run[-1]):
        cx2.reverse()
    part5 = cut(own_pts, s_back, nose_out)
    path, marks = [], []
    for piece in (part1, cx1, run, cx2, part5):
        if path and dist(path[-1], piece[0]) < 0.05:
            piece = piece[1:]
        elif path and dist(path[-1], piece[0]) > 1.0:
            raise RuntimeError(f"{label}: pieces do not join ({dist(path[-1], piece[0]):.1f} m)")
        marks.append(len(path))
        path.extend(piece)
    cum = cumulative(path)
    s_cx1, s_run, s_cx2, s_own = (cum[min(m, len(cum) - 1)] for m in marks[1:])
    worst = 0.0
    for a, b, c in zip(path, path[1:], path[2:]):
        if dist(a, b) > 0.5 and dist(b, c) > 0.5:
            worst = max(worst, abs(turn(heading(a, b), heading(b, c))))
    # Stops: own-track platforms passed before crossing and after crossing back,
    # and the other track's platforms (the opposite direction's) in between.
    stops = []
    for st in usable:
        if nose_in < st["track_m"] + half < s_in - 5:
            stops.append({"name": st["name"], "at_m": round(st["track_m"] + half - nose_in, 1), "track": closed})
        if s_back + 5 < st["track_m"] - half and st["track_m"] + half < nose_out:
            stops.append({"name": st["name"], "at_m": round(s_own + (st["track_m"] + half - s_back), 1), "track": closed})
    for st in stations[other]:
        if st["offset_m"] > STATION_MATCH_M * 3:
            continue
        # Running against the other track's direction, the nose leads half a train past the platform centre.
        pos = abs(st["track_m"] - s_on) + half
        if lo + 30 < st["track_m"] < hi - 30:
            stops.append({"name": st["name"], "at_m": round(s_run + pos, 1), "track": other})
    stops.sort(key=lambda x: x["at_m"])
    stops.append({"name": exit_["name"], "at_m": round(cum[-1], 1), "track": closed})
    gpts, gcum = gtfs[travel]
    line_s = [round(locate(gpts, p, gcum)[0], 1) for p in path]
    zones = [[round(s_cx1 - 20, 1), round(s_run + ION_LENGTH, 1), round(CROSSOVER_SPEED, 3)],
             [round(s_cx2 - 20, 1), round(s_own + ION_LENGTH, 1), round(CROSSOVER_SPEED, 3)]]
    section_id = f"single:{other}:{first['way']}-{second['way']}"
    # The opposing direction normally runs on the shared track: its section,
    # along its own GTFS line, from the crossover it meets first to the other.
    o_pts, o_cum = gtfs[other]
    a = locate(o_pts, xy[e2[other]["node"]], o_cum)[0]
    b = locate(o_pts, xy[e1[other]["node"]], o_cum)[0]
    route = {
        "id": label, "closed": closed, "single_track": other, "travel": travel,
        "via": [first["way"], second["way"]], "sites": [first["site"], second["site"]],
        "section": section_id,
        "entry_station": entry["name"], "exit_station": exit_["name"],
        "line": {"direction": travel, "from_station": entry["name"], "to_station": exit_["name"]},
        "path": encode(path), "length_m": round(cum[-1], 2),
        "stops": stops, "line_s": line_s,
        "resources": [
            {"id": section_id, "s0": round(s_cx1 - FOUL_M, 1), "s1": round(s_own + FOUL_M, 1)},
        ],
        "curves": curve_caps(path), "zones": zones,
        "opposing": {"direction": other, "section_line_m": [round(min(a, b) - FOUL_M, 1), round(max(a, b) + FOUL_M, 1)]},
        "shared_m": round(hi - lo, 1),
    }
    report.append(f"  diversion {label}: {travel}bound cross {closed}->{other} at {first['site']}, back at {second['site']}: "
                  f"{cum[-1]:.0f} m from {entry['name']} to {exit_['name']}, {len(stops) - 1} intermediate stops, "
                  f"shared section {hi - lo:.0f} m, sharpest vertex {worst:.1f} deg")
    return route


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
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
