"""Build GRT's three bus garages, their driveways and the out-of-service routes
between each garage and the service stops of the trips it sends and receives.

Usage:
    python3 -I build_garages.py [--tiles DIR] [--gtfs /home/user/gtfs/bus] [--check]

Sources:
- OpenStreetMap (ODbL), via the OSM API map call around each site and route
  (filtered into sources/garages-osm.json: drivable roads, the sites, and
  turn restrictions):
  * Northfield: way 1010416379, "Grand River Transit Northfield Drive
    Maintenance Facility", 300 Northfield Drive East, Waterloo;
  * Strasburg / Chandler: way 166966500 (industrial site holding building
    68282507, "Strasburg Transit Garage", 250 Strasburg Road), reaching
    Chandler Drive on its west side, Kitchener;
  * Conestoga Boulevard: way 827706044, the industrial site holding the
    address point 460 Conestoga Boulevard (node 4035136813), Cambridge.
- Region of Waterloo 2026 orthoimagery (waimagery Imagery_2026), checked by
  eye for each site's driveways (see docs/bus-garages.md); not redistributed.
- GRT GTFS (Region of Waterloo Open Data Licence): the blocks each garage
  sends out and receives in the simulated hour (07:30–08:30, Wednesday
  2 December 2026), their first and last service stops and times, and the
  trip shapes.

Bus position. Each garage's bus stands at a node on its bus apron, chosen on
the 2026 imagery (Northfield: south apron of the building; Strasburg: the
parking rows east of the garage; Conestoga Boulevard: west side of the
garage). Staff car parks are not used.

Roads. A directed graph of drivable OSM ways: one-way tags respected, ways
closed to traffic skipped unless open to buses, service ways used only inside a
garage site, U-turns only at dead ends, and every turn restriction applied.
A driveway is a node where one of a site's service ways meets a public road.
Its permitted movements are read from the road: a one-way carriageway allows
only the turns into and out of its own direction.

Contains information provided by the Regional Municipality of Waterloo under
the Region's Open Data Licence. Map data © OpenStreetMap contributors (ODbL).
"""
import argparse
import csv
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
SOURCE = HERE / "sources" / "garages-osm.json"
OUTPUTS = [ROOT / "backend" / "bus_garages.json", ROOT / "frontend" / "lib" / "geo" / "bus-garages.json"]
DATE = "20261202"
HOUR0 = 7 * 3600 + 30 * 60          # run minute 0 = 07:30
PUBLIC = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
          "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
SPEED = {"motorway": 100, "trunk": 80, "primary": 60, "secondary": 60, "tertiary": 50, "unclassified": 50,
         "residential": 40, "motorway_link": 60, "trunk_link": 50, "primary_link": 40, "secondary_link": 40,
         "tertiary_link": 40, "service": 15}           # km/h where no maxspeed is mapped (modelled)
TURN_S = 6.0                                           # added per turn sharper than 45° (modelled)

GARAGES = [
    {"id": "northfield", "name": "Northfield Drive garage", "address": "300 Northfield Drive East, Waterloo",
     "site": 1010416379, "inventory": 60, "park": 9322733749,
     "out": {"block": "393529", "note": "route 201 to Conestoga College from Conestoga Station"},
     "in": {"block": "393598", "note": "route 13, ends at University of Waterloo Station", "from_uw_exit": True}},
    {"id": "strasburg", "name": "Strasburg Road garage", "address": "250 Strasburg Road / 85 Chandler Drive, Kitchener",
     "site": 166966500, "inventory": 110, "park": 6084834696,
     "out": {"block": "393512", "note": "route 35 to Central Station from Sunrise Centre Station"},
     "in": {"block": "393510", "note": "route 35, ends at Sunrise Centre Station"}},
    {"id": "conestoga-blvd", "name": "Conestoga Boulevard garage", "address": "460 Conestoga Boulevard, Cambridge",
     "site": 827706044, "inventory": 40, "park": 416249602,
     "out": {"block": "394447", "note": "route 61 to Conestoga College from Cambridge Centre Station"},
     "in": {"block": "393474", "note": "route 201, ends at Conestoga College Doon Campus"}},
]
UW_EXIT = 298025663          # Transit Plaza Road meets Phillip Street (uw_station.json)


# --- sources ------------------------------------------------------------------
def filter_tiles(tiles):
    """Merge OSM API tiles; keep drivable ways, the garage sites and turn restrictions."""
    nodes, ways, rels = {}, {}, {}
    for f in sorted(glob.glob(str(Path(tiles) / "*.json"))):
        data = json.loads(Path(f).read_text())
        if "elements" not in data:
            continue
        for e in data["elements"]:
            {"node": nodes, "way": ways, "relation": rels}[e["type"]][e["id"]] = e
    sites = {g["site"] for g in GARAGES}
    keep_w = {}
    for wid, w in ways.items():
        t = w.get("tags", {})
        if wid in sites or (t.get("highway") in PUBLIC | {"service"} and t.get("area") != "yes"):
            keep_w[wid] = {"type": "way", "id": wid, "nodes": w["nodes"],
                           "tags": {k: v for k, v in t.items() if k in (
                               "highway", "name", "oneway", "junction", "access", "bus", "psv", "motor_vehicle",
                               "service", "maxspeed", "lanes", "landuse", "building", "operator", "addr:housenumber",
                               "addr:street")}}
    keep_r = {}
    for rid, r in rels.items():
        t = r.get("tags", {})
        if t.get("type") == "restriction" and any(m["type"] == "way" and m["ref"] in keep_w for m in r["members"]):
            keep_r[rid] = {"type": "relation", "id": rid, "members": r["members"], "tags": t}
    used = {n for w in keep_w.values() for n in w["nodes"]} | {m["ref"] for r in keep_r.values() for m in r["members"]
                                                                if m["type"] == "node"}
    keep_n = [{"type": "node", "id": n, "lat": nodes[n]["lat"], "lon": nodes[n]["lon"]} for n in sorted(used) if n in nodes]
    return {"version": "0.6", "generator": "OSM API 0.6 map calls (2026-11-20), filtered",
            "copyright": "OpenStreetMap and contributors", "license": "http://opendatacommons.org/licenses/odbl/1-0/",
            "elements": keep_n + sorted(keep_w.values(), key=lambda x: x["id"]) + sorted(keep_r.values(), key=lambda x: x["id"])}


def load():
    data = json.loads(SOURCE.read_text())
    N = {e["id"]: e for e in data["elements"] if e["type"] == "node"}
    W = {e["id"]: e for e in data["elements"] if e["type"] == "way"}
    R = [e for e in data["elements"] if e["type"] == "relation"]
    return N, W, R


def inside(p, poly):
    x, y = p
    c = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def encode(points):
    out = []
    for (x, y), s in zip(points, cumulative(points)):
        lat, lon = to_latlon(x, y)
        out.append([round(lon, 7), round(lat, 7), round(s, 2)])
    return out


# --- road graph -----------------------------------------------------------------
class Roads:
    def __init__(self, N, W, R, sites, stop_points=()):
        self.N, self.W = N, W
        self.xy = {n: to_xy(e["lat"], e["lon"]) for n, e in N.items()}
        self.site_poly = {s: [self.xy[n] for n in W[s]["nodes"] if n in self.xy] for s in sites}
        self.site_of_way = {}
        self.adj = {}
        # Service roads reaching a service stop (a terminal or campus loop): those
        # within 80 m of the stop, and the service roads joined to them within 800 m.
        svc = {wid: w for wid, w in W.items() if w["tags"].get("highway") == "service"}
        by_node = {}
        for wid, w in svc.items():
            for n in w["nodes"]:
                by_node.setdefault(n, []).append(wid)
        self.terminal_ways = set()
        for p in stop_points:
            near = lambda wid, r: all(n in self.xy and dist(self.xy[n], p) < r for n in svc[wid]["nodes"])  # noqa: E731
            todo = [wid for wid, w in svc.items() if any(n in self.xy and dist(self.xy[n], p) < 80.0 for n in w["nodes"])]
            seen = set(todo)
            while todo:
                wid = todo.pop()
                for n in svc[wid]["nodes"]:
                    for o in by_node.get(n, []):
                        if o not in seen and near(o, 800.0):
                            seen.add(o)
                            todo.append(o)
            self.terminal_ways |= seen
        for wid, w in W.items():
            t = w["tags"]
            hw = t.get("highway")
            if hw is None:
                continue
            closed = t.get("access") in ("no", "private") or t.get("motor_vehicle") in ("no", "private")
            if closed and t.get("bus") not in ("yes", "designated") and t.get("psv") not in ("yes", "designated"):
                if hw != "service":
                    continue
            if hw == "service":
                site = next((s for s, poly in self.site_poly.items()
                             if any(n in self.xy and inside(self.xy[n], poly) for n in w["nodes"])), None)
                if site is not None:
                    self.site_of_way[wid] = site
                elif wid not in self.terminal_ways:
                    continue          # other service roads: only those reaching a service stop
            ow = t.get("oneway")
            fwd = ow != "-1"
            back = ow not in ("yes", "true", "1", "-1") and t.get("junction") != "roundabout" or ow == "-1"
            speed = float(t["maxspeed"]) if t.get("maxspeed", "").isdigit() else SPEED.get(hw, 40)
            ns = [n for n in w["nodes"] if n in self.xy]
            for a, b in zip(ns, ns[1:]):
                d = dist(self.xy[a], self.xy[b])
                if fwd:
                    self.adj.setdefault(a, []).append((b, wid, d, speed))
                if back:
                    self.adj.setdefault(b, []).append((a, wid, d, speed))
        # Turn restrictions: (from way, via node) -> forbidden / only "to" ways.
        self.no, self.only = {}, {}
        for r in R:
            frm = [m["ref"] for m in r["members"] if m["role"] == "from" and m["type"] == "way"]
            via = [m["ref"] for m in r["members"] if m["role"] == "via" and m["type"] == "node"]
            to = [m["ref"] for m in r["members"] if m["role"] == "to" and m["type"] == "way"]
            if not (frm and via and to):
                continue
            kind = r["tags"].get("restriction", "")
            key = (frm[0], via[0])
            if kind.startswith("no_"):
                self.no.setdefault(key, set()).add(to[0])
            elif kind.startswith("only_"):
                self.only[key] = to[0]

    def allowed(self, prev, via, nxt_way, prev_node, nxt_node):
        if prev is None:
            return True
        if nxt_node == prev_node and len(self.adj.get(via, [])) > 1:
            return False          # no U-turns except at a dead end
        if nxt_way in self.no.get((prev, via), ()):
            return False
        only = self.only.get((prev, via))
        return only is None or only == nxt_way

    def route(self, sources, targets, avoid=()):
        """Fastest path from any of `sources` to a target edge (a, b), never
        along an edge in `avoid` ((a, b) pairs): returns node list, ways and time."""
        tgt = {(a, b) for a, b in targets}
        avoid = set(avoid)
        best = {}
        pq = []
        for n in sources:
            heapq.heappush(pq, (0.0, 0, n, None, None, (n,), ()))
        seq = 0
        while pq:
            t, _, u, prev_w, prev_n, path, wpath = heapq.heappop(pq)
            if len(path) >= 2 and (path[-2], path[-1]) in tgt:
                return list(path), list(wpath), t
            key = (u, prev_w, prev_n)
            if key in best and best[key] <= t:
                continue
            best[key] = t
            for v, wid, d, speed in self.adj.get(u, []):
                if (u, v) in avoid or not self.allowed(prev_w, u, wid, prev_n, v):
                    continue
                cost = d / (speed / 3.6)
                if prev_n is not None:
                    if abs(turn(heading(self.xy[prev_n], self.xy[u]), heading(self.xy[u], self.xy[v]))) > 45:
                        cost += TURN_S
                seq += 1
                heapq.heappush(pq, (t + cost, seq, v, wid, u, path + (v,), wpath + (wid,)))
        return None, None, math.inf

    def edges_near(self, p, bearing_deg, radius=40.0):
        """Directed road edges within `radius` of p heading within 50° of `bearing_deg`
        (public roads, or a terminal's bus roads)."""
        out = []
        for a, lst in self.adj.items():
            for b, wid, d, _ in lst:
                if wid in self.site_of_way:
                    continue
                _, _, dd = project(p, self.xy[a], self.xy[b])
                if dd <= radius and abs(turn(bearing_deg, heading(self.xy[a], self.xy[b]))) <= 50:
                    out.append((dd, a, b))
        out.sort()
        return [(a, b) for _, a, b in out[:3]]


# --- GTFS ------------------------------------------------------------------------
def gtfs(root, blocks):
    services = set()
    with open(root / "calendar_dates.txt") as f:
        for r in csv.DictReader(f):
            if r["date"] == DATE and r["exception_type"] == "1":
                services.add(r["service_id"])
    trips = {}
    with open(root / "trips.txt") as f:
        for r in csv.DictReader(f):
            if r["service_id"] in services and r["block_id"] in blocks:
                trips[r["trip_id"]] = r
    times = {}
    with open(root / "stop_times.txt") as f:
        for r in csv.DictReader(f):
            if r["trip_id"] in trips:
                times.setdefault(r["trip_id"], []).append(r)
    stops = {}
    with open(root / "stops.txt") as f:
        for r in csv.DictReader(f):
            stops[r["stop_id"]] = r
    shape_ids = {t["shape_id"] for t in trips.values()}
    shapes = {}
    with open(root / "shapes.txt") as f:
        for r in csv.DictReader(f):
            if r["shape_id"] in shape_ids:
                shapes.setdefault(r["shape_id"], []).append((int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]),
                                                              float(r["shape_pt_lon"])))
    def sec(x):
        h, m, s = (int(v) for v in x.split(":"))
        return h * 3600 + m * 60 + s - HOUR0
    by_block = {}
    for tid, t in trips.items():
        st = sorted(times[tid], key=lambda r: int(r["stop_sequence"]))
        by_block.setdefault(t["block_id"], []).append({
            "trip_id": tid, "route": t["route_id"], "headsign": t["trip_headsign"], "shape_id": t["shape_id"],
            "stops": [{"stop": r["stop_id"], "name": stops[r["stop_id"]]["stop_name"],
                       "lat": float(stops[r["stop_id"]]["stop_lat"]), "lon": float(stops[r["stop_id"]]["stop_lon"]),
                       "arrive_s": sec(r["arrival_time"]), "depart_s": sec(r["departure_time"])} for r in st],
        })
    for b in by_block.values():
        b.sort(key=lambda x: x["stops"][0]["depart_s"])
    return by_block, {k: [to_xy(lat, lon) for _, lat, lon in sorted(v)] for k, v in shapes.items()}


def shape_with_stops(trip, shape):
    """The trip's shape with each stop's distance along it (monotone)."""
    cum = cumulative(shape)
    out = []
    lo = 0
    for st in trip["stops"]:
        p = to_xy(st["lat"], st["lon"])
        best = None
        for i in range(lo, len(shape) - 1):
            t, _, d = project(p, shape[i], shape[i + 1])
            s = cum[i] + t * (cum[i + 1] - cum[i])
            if best is None or d < best[1] - 1e-6:
                best = (s, d, i)
            if best and d > best[1] + 400 and s > best[0] + 400:
                break
        out.append(round(best[0], 1))
        lo = best[2]
    return encode(shape), out


# --- build -----------------------------------------------------------------------
def build(gtfs_root):
    N, W, R = load()
    blocks = {g[k]["block"] for g in GARAGES for k in ("out", "in")}
    by_block, shapes = gtfs(gtfs_root, blocks)
    stop_points = []
    for g in GARAGES:
        for kind in ("out", "in"):
            trips = by_block[g[kind]["block"]]
            st = trips[0]["stops"][0] if kind == "out" else trips[-1]["stops"][-1]
            stop_points.append(to_xy(st["lat"], st["lon"]))
    roads = Roads(N, W, R, [g["site"] for g in GARAGES], stop_points)
    report, out = [], []
    for g in GARAGES:
        site = g["site"]
        poly = roads.site_poly[site]
        # Driveways: a site service way meeting a public road.
        site_nodes = {n for wid, s in roads.site_of_way.items() if s == site for n in W[wid]["nodes"]}
        driveways = []
        for n in sorted(site_nodes):
            pub = [(wid, W[wid]["tags"]) for wid, w in W.items() if n in w["nodes"]
                   and w["tags"].get("highway") in PUBLIC]
            if not pub:
                continue
            svc = next(wid for wid, s in roads.site_of_way.items() if s == site and n in W[wid]["nodes"])
            sn = W[svc]["nodes"]
            k = sn.index(n)
            inner = sn[k - 1] if k > 0 else sn[k + 1]
            out_h = heading(roads.xy[inner], roads.xy[n])
            moves = []
            for v, wid, _, _ in roads.adj.get(n, []):
                if W[wid]["tags"].get("highway") in PUBLIC:
                    side = turn(out_h, heading(roads.xy[n], roads.xy[v]))
                    moves.append(("right" if side > 0 else "left") + f" onto {W[wid]['tags'].get('name', 'the road')}")
            ins = []
            for a, lst in roads.adj.items():
                for b, wid, _, _ in lst:
                    if b == n and W[wid]["tags"].get("highway") in PUBLIC:
                        side = turn(heading(roads.xy[a], roads.xy[n]), heading(roads.xy[n], roads.xy[inner]))
                        ins.append(("right" if side > 0 else "left") + f" in from {W[wid]['tags'].get('name', 'the road')}")
            road = pub[0][1]
            driveways.append({
                "node": n, "lon": N[n]["lon"], "lat": N[n]["lat"], "road": road.get("name"),
                "road_oneway": road.get("oneway") in ("yes", "-1"), "exits": sorted(set(moves)), "entries": sorted(set(ins)),
            })
        # Where the bus stands (chosen on the 2026 imagery: the bus apron beside the garage).
        park = g["park"]
        if park not in site_nodes:
            raise RuntimeError(f"{g['id']}: park node {park} is not on the site's service roads")
        entry = {"id": g["id"], "name": g["name"], "address": g["address"], "site_osm_way": site,
                 "outline": encode(poly + poly[:1]), "park_node": park,
                 "park": [N[park]["lon"], N[park]["lat"]], "inventory": g["inventory"], "driveways": driveways}
        report.append(f"{g['id']}: site way {site}; {len(driveways)} driveway(s): " + "; ".join(
            f"node {d['node']} on {d['road']} (exits {', '.join(d['exits']) or 'none'}; entries {', '.join(d['entries']) or 'none'})"
            for d in driveways))
        for kind in ("out", "in"):
            spec = g[kind]
            trips = by_block[spec["block"]]
            trip = trips[0] if kind == "out" else trips[-1]
            st = trip["stops"][0] if kind == "out" else trip["stops"][-1]
            shape = shapes[trip["shape_id"]]
            enc, stop_m = shape_with_stops(trip, shape)
            # The bus must serve the stop heading the way the trip goes.
            cum = cumulative(shape)
            if kind == "out":
                s_at = stop_m[0]
                i = max(0, min(len(shape) - 2, next(j for j in range(len(cum) - 1) if cum[j + 1] >= s_at + 0.01)))
                bear = heading(shape[i], shape[i + 1])
                targets = roads.edges_near(to_xy(st["lat"], st["lon"]), bear)
                path, wpath, t = roads.route([park], targets)
            else:
                s_at = stop_m[-1]
                i = max(0, min(len(shape) - 2, next(j for j in range(len(cum) - 1) if cum[j + 1] >= s_at - 0.5) ))
                bear = heading(shape[i], shape[i + 1])
                starts = roads.edges_near(to_xy(st["lat"], st["lon"]), bear)
                origin = [b for _, b in starts] if not spec.get("from_uw_exit") else [UW_EXIT]
                site_edges = [(a, b) for a, lst in roads.adj.items() for b, wid, _, _ in lst if b == park]
                path, wpath, t = roads.route(origin, site_edges)
            if path is None:
                raise RuntimeError(f"{g['id']} {kind}: no road path")
            # The same move through each driveway (the others closed), for dispatch to compare.
            alternatives = []
            for d in driveways:
                # Close the site's own service-road links to every other driveway.
                others = {x["node"] for x in driveways if x["node"] != d["node"]}
                others = [(a, b) for a, lst in roads.adj.items() for b, wid, _, _ in lst
                          if wid in roads.site_of_way and (a in others or b in others)]
                if kind == "out":
                    p2, w2, t2 = roads.route([park], targets, avoid=others)
                else:
                    p2, w2, t2 = roads.route(origin, site_edges, avoid=others)
                alternatives.append({"driveway": d["node"], "road": d["road"], "feasible": p2 is not None,
                                     "time_s": round(t2, 1) if p2 else None,
                                     "length_m": round(cumulative([roads.xy[n] for n in p2])[-1], 1) if p2 else None,
                                     "reason": "" if p2 else "no legal route through this driveway"})
            pts = [roads.xy[n] for n in path]
            names = []
            for wid in wpath:
                nm = W[wid]["tags"].get("name") or ("garage driveway" if wid in roads.site_of_way else W[wid]["tags"]["highway"])
                if not names or names[-1] != nm:
                    names.append(nm)
            # The driveway the bus actually uses: the first site exit it crosses (the last on the way in).
            by_node = {d["node"]: d for d in driveways}
            order = path if kind == "out" else list(reversed(path))
            used = next((by_node[n] for n in order if n in by_node), None)
            edge_t = []
            for a, b, wid in zip(path, path[1:], wpath):
                spd = next(sp for v, w2, _, sp in roads.adj[a] if v == b and w2 == wid)
                edge_t.append(dist(roads.xy[a], roads.xy[b]) / (spd / 3.6))
            entry[kind] = {
                "block": spec["block"], "note": spec["note"], "route": trip["route"], "trip_id": trip["trip_id"],
                "headsign": trip["headsign"],
                "service_stop": {k: st[k] for k in ("stop", "name", "lat", "lon", "arrive_s", "depart_s")},
                "deadhead": {"path": encode(pts), "length_m": round(cumulative(pts)[-1], 1), "time_s": round(t, 1),
                             "speeds": [round(x, 2) for x in edge_t], "via": names,
                             "driveway": used["node"] if used else None, "alternatives": alternatives,
                             "from": "UW Station exit (Transit Plaza Road at Phillip Street)" if spec.get("from_uw_exit") else None},
                "trip": {"shape": enc, "stop_m": stop_m, "stops": trip["stops"]},
            }
            report.append(f"  {kind}: block {spec['block']} route {trip['route']} ({trip['headsign']}): "
                          f"{'first' if kind == 'out' else 'last'} stop {st['stop']} {st['name']} at "
                          f"{(HOUR0 + (st['depart_s'] if kind == 'out' else st['arrive_s'])) // 3600:02d}:"
                          f"{(HOUR0 + (st['depart_s'] if kind == 'out' else st['arrive_s'])) % 3600 // 60:02d}; "
                          f"deadhead {cumulative(pts)[-1] / 1000:.1f} km, {t / 60:.1f} min via {' → '.join(names)}"
                          f"{'; driveway ' + str(used['node']) if used else ''}")
        out.append(entry)
    return {
        "attribution": "Roads and garage sites © OpenStreetMap contributors (ODbL); blocks, trips, stops and shapes from GRT "
                       "GTFS: contains information provided by the Regional Municipality of Waterloo under the Region's "
                       "Open Data Licence.",
        "date": DATE, "minute0": "07:30", "garages": out,
        "modelled": {"speeds_kmh": SPEED, "turn_s": TURN_S, "inventory": "buses at each garage at 07:30 (modelled)",
                     "assignment": "each block is assigned to the garage nearest its first or last stop (modelled)"},
    }, report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tiles", help="directory of OSM API map tiles to filter into the source file")
    parser.add_argument("--gtfs", default="/home/user/gtfs/bus")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.tiles:
        SOURCE.write_text(json.dumps(filter_tiles(args.tiles), separators=(",", ":")) + "\n")
        print(f"wrote {SOURCE.relative_to(ROOT)} ({SOURCE.stat().st_size // 1024} KiB)")
    out, report = build(Path(args.gtfs))
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
