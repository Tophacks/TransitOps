"""Dutton Drive yard (ION Operations, Maintenance and Storage Facility, 518 Dutton
Drive, Waterloo): track graph, storage positions and the routes between each
storage track and the running line. Used by build_network.py.

Sources:
- sources/ion-network-osm.json: every OSM way tagged service=yard and the two
  lead switches on the northbound running track (nodes 7020996626 and
  7020977372);
- sources/dutton-omsf-osm.json: the OMSF building (way 754590775, 518 Dutton
  Drive), the wash building (754590774) and the facility site (421343538), used
  to tell shop tracks (inside the building) from outdoor storage.

Map data © OpenStreetMap contributors (ODbL).

Rules applied:
1. A train cannot reverse at a switch: from one node to the next the track may
   turn by at most MAX_TURN_DEG. Reversing is possible only at a track end.
2. A storage track is a stub that ends at a buffer stop or a mapped track end.
   Its usable length runs from STOP_SHORT_M before the end to FOUL_M before
   the first switch towards the lead. Trains are stored nose to tail
   STORE_GAP_M apart; the inner one can leave only once the outer ones have.
3. Each lead joins a running track in one direction. The route from the lead
   to service follows the track in that direction to the first station
   platform (or, if the lead points against the track's direction, to the
   first enabled crossover that takes it onto the correct track, then to the
   next platform). A lead is used only if that whole route exists.
"""
import json
import math
from pathlib import Path

from geo import cumulative, dist, heading, to_xy, turn

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "sources" / "dutton-omsf-osm.json"
LEADS = {"north": 7020996626, "south": 7020977372}
ION_LENGTH = 41.8
FOUL_M = 8.0
STOP_SHORT_M = 5.0           # modelled: a stored train stops this far before the track end
STORE_GAP_M = 5.0            # modelled: gap between trains stored on one track
YARD_SPEED = 15 / 3.6        # modelled: no maxspeed is mapped on the yard tracks
MAX_TURN_DEG = 40.0
SPIKE_M = 10.0               # a mapped node that doubles the line back within this is a mapping error
OMSF_WAY, WASH_WAY, SITE_WAY = 754590775, 754590774, 421343538


def _inside(p, poly):
    x, y = p
    c = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def _polygons():
    data = json.loads(SOURCE.read_text())
    nodes = {e["id"]: e for e in data["elements"] if e["type"] == "node"}
    ways = {e["id"]: e for e in data["elements"] if e["type"] == "way"}
    poly = lambda w: [to_xy(nodes[n]["lat"], nodes[n]["lon"]) for n in ways[w]["nodes"]]  # noqa: E731
    return {"building": poly(OMSF_WAY), "wash": poly(WASH_WAY), "site": poly(SITE_WAY)}, ways


def build_yard(net, pts, cums, index, stations, crossovers, gtfs, helpers, report):
    """The yard graph, its storage tracks and every route to and from service."""
    encode, cut, locate, curve_caps, point_at = (helpers[k] for k in ("encode", "cut", "locate", "curve_caps", "point_at"))
    xy = net.xy
    polys, poly_ways = _polygons()
    yard_ways = {wid for wid, w in net.ways.items() if w["tags"].get("service") == "yard"}
    adj = {}
    shared = {}
    for wid, w in net.ways.items():
        for n in w["nodes"]:
            shared[n] = shared.get(n, 0) + 1
    clean = {}
    for wid in yard_ways:
        ns = list(net.ways[wid]["nodes"])
        # Drop mapping spikes: a node (on no other way) where the line doubles
        # back on itself within SPIKE_M. Real track cannot do that.
        changed = True
        while changed:
            changed = False
            for i in range(1, len(ns) - 1):
                a, b, c = ns[i - 1], ns[i], ns[i + 1]
                if shared.get(b, 0) > 1 or (net.nodes[b].get("tags") or {}):
                    continue
                if (abs(turn(heading(xy[a], xy[b]), heading(xy[b], xy[c]))) > 150
                        and min(dist(xy[a], xy[b]), dist(xy[b], xy[c])) < SPIKE_M):
                    report.append(f"yard way {wid}: dropped node {b}, a {dist(xy[a], xy[b]):.1f} m spike where the "
                                  f"mapped line doubles back (mapping error)")
                    del ns[i]
                    changed = True
                    break
        clean[wid] = ns
    for wid in yard_ways:
        ns = clean[wid]
        for a, b in zip(ns, ns[1:]):
            adj.setdefault(a, []).append((b, wid))
            adj.setdefault(b, []).append((a, wid))
    for side, lead in LEADS.items():
        if lead not in adj or lead not in index["north"] and lead not in index["south"]:
            raise RuntimeError(f"yard lead {lead} does not join a yard track to a running track")
    on_track = set(index["north"]) | set(index["south"])
    ends = sorted(n for n, nb in adj.items() if len(nb) == 1 and n not in on_track)
    switches = {n for n, nb in adj.items() if len({v for v, _ in nb}) > 2} | set(LEADS.values())

    def paths_from(start):
        """Shortest path from a track end to each lead without reversing at a switch."""
        best = {}
        first, wid = adj[start][0]
        stack = [([start, first], [wid], dist(xy[start], xy[first]))]
        while stack:
            seq, wids, length = stack.pop()
            cur, prev = seq[-1], seq[-2]
            for side, lead in LEADS.items():
                if cur == lead and (side not in best or length < best[side][2]):
                    best[side] = (seq, wids, length)
            if cur in LEADS.values():
                continue
            h = heading(xy[prev], xy[cur])
            for v, w in adj.get(cur, []):
                if v == prev or v in seq:
                    continue
                if abs(turn(h, heading(xy[cur], xy[v]))) > MAX_TURN_DEG:
                    continue
                stack.append((seq + [v], wids + [w], length + dist(xy[cur], xy[v])))
        return best

    def where(seq):
        cum = cumulative([xy[n] for n in seq])
        samples = [point_at([xy[n] for n in seq], cum, s) for s in range(0, int(cum[-1]) + 1, 2)]
        for kind in ("building", "wash"):
            if any(_inside(p, polys[kind]) for p in samples):
                return kind
        return "outdoor"

    # Which running track and direction each lead joins.
    lead_info = {}
    for side, lead in LEADS.items():
        track = "north" if lead in index["north"] else "south"
        seq = pts[track]
        s_lead = index[track][lead]
        nbr = next(v for v, _ in adj[lead])
        out_h = heading(xy[nbr], xy[lead])          # leaving the yard at the switch
        i = max(0, min(len(seq) - 2, [round(x, 2) for x in cums[track]].index(round(s_lead, 2))))
        along = heading(seq[i], seq[i + 1])         # the track's own direction
        same = abs(turn(along, out_h)) < 60
        lead_info[side] = {"node": lead, "track": track, "track_m": round(s_lead, 2), "with_traffic": same,
                           "lon": net.nodes[lead]["lon"], "lat": net.nodes[lead]["lat"]}
        report.append(f"yard lead {side} (node {lead}): joins the {track} track at {s_lead:.0f} m, pointing "
                      f"{'with' if same else 'against'} its direction ({abs(turn(along, out_h)):.0f} deg)")

    # Storage tracks.
    tracks = []
    connected = set()
    for e in ends:
        found = paths_from(e)
        if not found:
            report.append(f"  yard track end {e}: reaches no lead without reversing (not used)")
            continue
        any_path = min(found.values(), key=lambda x: x[2])[0]
        cum = cumulative([xy[n] for n in any_path])
        sw = next(i for i, n in enumerate(any_path) if n in switches)
        switch_m = cum[sw]
        usable = switch_m - FOUL_M - STOP_SHORT_M
        positions = max(0, int((usable + STORE_GAP_M) // (ION_LENGTH + STORE_GAP_M)))
        tag = (net.nodes[e].get("tags") or {}).get("railway")
        kind = where(any_path[: sw + 1])
        way = net.ways[any_path and adj[e][0][1]]
        tracks.append({
            "id": f"Y{adj[e][0][1]}", "way": adj[e][0][1], "end_node": e,
            "end": "buffer stop" if tag == "buffer_stop" else "track end (no buffer stop mapped)",
            "where": {"building": "inside the OMSF building (shop track)", "wash": "through the wash building",
                      "outdoor": "outdoor storage"}[kind],
            "kind": "shop" if kind == "building" else kind,
            "switch_m": round(switch_m, 1), "usable_m": round(usable, 1), "positions": positions,
            "leads": sorted(found), "lon": net.nodes[e]["lon"], "lat": net.nodes[e]["lat"],
            "layer": way["tags"].get("layer"),
            "_paths": found,
        })
        connected.update(n for seq, _, _ in found.values() for n in seq)
        report.append(f"  yard track {adj[e][0][1]} ({tracks[-1]['where']}, {tracks[-1]['end']}): switch {switch_m:.0f} m "
                      f"from the end, usable {usable:.0f} m, {positions} position(s), reaches lead(s) {', '.join(sorted(found))}")
    unreached = sorted(yard_ways - {w for t in tracks for _, ws, _ in t["_paths"].values() for w in ws})
    report.append(f"  yard ways on no storage-to-lead path: {unreached or 'none'}")

    enabled = [c for c in crossovers if c["passenger"]]
    half = ION_LENGTH / 2

    def station_nose(direction, name):
        st = next(s for s in stations[direction] if s["name"] == name)
        return st["track_m"] + half

    def mainline_out(side):
        """From the lead onto service: (points, stop name, line, description)."""
        info = lead_info[side]
        track, s_lead = info["track"], info["track_m"]
        seq, cum = pts[track], cums[track]
        if info["with_traffic"]:
            st = next(s for s in stations[track] if s["track_m"] > s_lead + half)
            part = cut(seq, s_lead, st["track_m"] + half)
            return part, st["name"], track, f"{track}bound track to {st['name']}", []
        # Against the track's direction: back to a crossover that takes trains of
        # the opposite direction onto their own track.
        other = "south" if track == "north" else "north"
        cands = []
        for c in enabled:
            mv = next((m for m in c["moves"] if m["travel"] == other and m["from"] == track and m["to"] == other), None)
            end = next((x for x in c["ends"] if x["track"] == track), None)
            if mv and end and end["track_m"] < s_lead - half:
                cands.append((s_lead - end["track_m"], c))
        if not cands:
            return None
        _, c = min(cands, key=lambda x: x[0])
        e_own = next(x for x in c["ends"] if x["track"] == track)
        e_oth = next(x for x in c["ends"] if x["track"] == other)
        part1 = list(reversed(cut(seq, e_own["track_m"], s_lead)))
        cx = [to_xy(p[1], p[0]) for p in c["path"]]
        if dist(cx[0], part1[-1]) > dist(cx[-1], part1[-1]):
            cx.reverse()
        st = next(s for s in stations[other] if s["track_m"] > e_oth["track_m"] + half)
        part2 = cut(pts[other], e_oth["track_m"], st["track_m"] + half)
        xs = [{"way": c["way"], "site": c["site"]}]
        return (part1, cx, part2), st["name"], other, (f"{track}bound track against its direction to crossover {c['way']} "
                                                       f"({c['site']}), then the {other}bound track to {st['name']}"), xs

    def mainline_in(side):
        """From a platform onto the lead (facing): (points, start station, line, description)."""
        info = lead_info[side]
        track, s_lead = info["track"], info["track_m"]
        if not info["with_traffic"]:
            # The lead takes trains running with the track's direction (facing).
            st = [s for s in stations[track] if s["track_m"] + half < s_lead - FOUL_M]
            if not st:
                return None
            st = st[-1]
            part = cut(pts[track], st["track_m"] + half, s_lead)
            return [part], st["name"], track, f"{track}bound track from {st['name']}", [], st["offset_m"]
        # The lead points with traffic: trains enter running against it, after a
        # crossover from the other track ahead of the lead.
        other = "south" if track == "north" else "north"
        cands = []
        for c in enabled:
            mv = next((m for m in c["moves"] if m["travel"] == other and m["from"] == other and m["to"] == track), None)
            end = next((x for x in c["ends"] if x["track"] == track), None)
            if mv and end and end["track_m"] > s_lead:
                cands.append((end["track_m"] - s_lead, c))
        if not cands:
            return None
        _, c = min(cands, key=lambda x: x[0])
        e_own = next(x for x in c["ends"] if x["track"] == track)
        e_oth = next(x for x in c["ends"] if x["track"] == other)
        st = [s for s in stations[other] if s["track_m"] + half < e_oth["track_m"] - FOUL_M]
        st = st[-1]
        part1 = cut(pts[other], st["track_m"] + half, e_oth["track_m"])
        cx = [to_xy(p[1], p[0]) for p in c["path"]]
        if dist(cx[0], part1[-1]) > dist(cx[-1], part1[-1]):
            cx.reverse()
        part2 = list(reversed(cut(pts[track], s_lead, e_own["track_m"])))
        return ([part1, cx, part2], st["name"], other,
                f"{other}bound track from {st['name']}, crossover {c['way']} ({c['site']}), then the {track} track "
                f"against its direction to the lead", [{"way": c["way"], "site": c["site"]}], st["offset_m"])

    def join(pieces, label):
        path, marks = [], []
        for piece in pieces:
            if path and dist(path[-1], piece[0]) < 0.05:
                piece = piece[1:]
            elif path and dist(path[-1], piece[0]) > 1.5:
                raise RuntimeError(f"{label}: pieces do not join ({dist(path[-1], piece[0]):.1f} m)")
            marks.append(len(path))
            path.extend(piece)
        return path, marks

    routes = []
    for t in tracks:
        for side in t["leads"]:
            seq, wids, ylen = t["_paths"][side]
            ypts = [xy[n] for n in seq]
            ycum = cumulative(ypts)
            yres = [{"id": f"stub:{t['way']}", "s0": 0.0, "s1": round(t["switch_m"], 1)}]
            for n, s in zip(seq, ycum):
                if n in switches:
                    rid = f"lead:{side}" if n == LEADS[side] else f"switch:{n}"
                    yres.append({"id": rid, "s0": round(max(0.0, s - FOUL_M), 1), "s1": round(s + FOUL_M, 1)})
            out = mainline_out(side)
            if out is not None:
                pieces, stop, line, desc, xs = out
                pieces = pieces if isinstance(pieces, tuple) else (pieces,)
                path, marks = join([ypts] + list(pieces), f"out {t['id']} via {side}")
                cum = cumulative(path)
                zones = [[0.0, round(ylen + ION_LENGTH, 1), round(YARD_SPEED, 3)]]
                if len(marks) > 2:  # a crossover
                    zones.append([round(cum[marks[2]] - 20, 1), round(cum[marks[3]] + ION_LENGTH, 1), round(25 / 3.6, 3)])
                g, gc = gtfs[line]
                routes.append({
                    "id": f"yard-out:{side}:{t['way']}", "kind": "out", "lead": side, "track": t["id"],
                    "yard_m": round(ylen, 1), "length_m": round(cum[-1], 2), "path": encode(path),
                    "stops": [{"name": stop, "at_m": round(cum[-1], 1), "track": line}], "line": line, "to_station": stop,
                    "via": desc, "crossovers": xs, "resources": yres,
                    "line_s": [round(locate(g, p, gc)[0], 1) for p in path],
                    "zones": zones, "curves": curve_caps(path),
                })
            inn = mainline_in(side)
            if inn is not None:
                pieces, start, line, desc, xs, offset = inn
                rpts = list(reversed(ypts))
                path, marks = join(list(pieces) + [rpts], f"in {t['id']} via {side}")
                cum = cumulative(path)
                y0 = cum[-1] - ylen
                zones = [[round(y0 - 20, 1), round(cum[-1], 1), round(YARD_SPEED, 3)]]
                if len(pieces) > 1:
                    zones.append([round(cum[marks[1]] - 20, 1), round(cum[marks[2]] + ION_LENGTH, 1), round(25 / 3.6, 3)])
                g, gc = gtfs[line]
                res = [{"id": r["id"], "s0": round(cum[-1] - r["s1"], 1), "s1": round(cum[-1] - r["s0"], 1)} for r in yres]
                routes.append({
                    "id": f"yard-in:{side}:{t['way']}", "kind": "in", "lead": side, "track": t["id"],
                    "yard_m": round(ylen, 1), "length_m": round(cum[-1], 2), "path": encode(path),
                    "stops": [], "line": line, "from_station": start, "from_offset_m": offset,
                    "via": desc, "crossovers": xs, "resources": res,
                    "line_s": [round(locate(g, p, gc)[0], 1) for p in path],
                    "zones": zones, "curves": curve_caps(path),
                })
    for r in routes:
        report.append(f"  {r['id']}: {r['length_m']:.0f} m ({r['yard_m']:.0f} m in the yard), {r['via']}")
    for t in tracks:
        del t["_paths"]
    return {
        "name": "ION Operations, Maintenance and Storage Facility (OMSF)",
        "address": "518 Dutton Drive, Waterloo",
        "osm": {"building": OMSF_WAY, "wash": WASH_WAY, "site": SITE_WAY},
        "leads": lead_info,
        "tracks": tracks,
        "routes": routes,
        "modelled": {"stop_short_m": STOP_SHORT_M, "store_gap_m": STORE_GAP_M, "yard_speed_mps": round(YARD_SPEED, 3),
                     "foul_m": FOUL_M},
        "outline": {k: encode(v + v[:1]) for k, v in polys.items()},
    }
