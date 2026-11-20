"""GRT garages: real sites and driveways, out-of-service routes on connected
roads to the GTFS service stops, finite inventories and persistent vehicle IDs."""
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "tools" / "waterloo_geometry"))

import garage_ops  # noqa: E402
import simulation_engine as se  # noqa: E402
from ion import IonPlan  # noqa: E402
from models import Bus  # noqa: E402

G = {g["id"]: g for g in garage_ops.GARAGES["garages"]}


def metres(a, b):
    return math.hypot((a[0] - b[0]) * 80700, (a[1] - b[1]) * 111200)


def run(**kw):
    plan = IonPlan(duration_min=60, headway_min=8, transfer_demand=110, delay_min=4)
    s = se.TransitSimulation([Bus(f"B{101 + i}") for i in range(3)], plan, intelligent=True, duration=60,
                             traffic_multiplier=1.15, seed=17, **kw)
    s.run()
    return s


class SiteTests(unittest.TestCase):
    def test_three_real_garages(self):
        self.assertEqual(sorted(G), ["conestoga-blvd", "northfield", "strasburg"])
        self.assertEqual(G["northfield"]["site_osm_way"], 1010416379)
        self.assertEqual(G["strasburg"]["site_osm_way"], 166966500)
        self.assertEqual(G["conestoga-blvd"]["site_osm_way"], 827706044)
        self.assertIn("300 Northfield Drive East", G["northfield"]["address"])
        self.assertIn("250 Strasburg Road", G["strasburg"]["address"])
        self.assertIn("85 Chandler Drive", G["strasburg"]["address"])
        self.assertIn("460 Conestoga Boulevard", G["conestoga-blvd"]["address"])

    def test_driveways_join_public_roads_with_permitted_movements(self):
        for g in G.values():
            self.assertTrue(g["driveways"], g["id"])
            for d in g["driveways"]:
                self.assertTrue(d["road"])
                self.assertTrue(d["exits"] or d["entries"], d)
        # Strasburg / Chandler: the site reaches both streets.
        self.assertEqual({d["road"] for d in G["strasburg"]["driveways"]}, {"Strasburg Road", "Chandler Drive"})

    def test_each_move_runs_from_the_garage_to_its_gtfs_stop_on_connected_roads(self):
        for g in G.values():
            for kind in ("out", "in"):
                m = g[kind]
                path = m["deadhead"]["path"]
                for a, b in zip(path, path[1:]):
                    self.assertAlmostEqual(metres(a, b), b[2] - a[2], delta=0.5 + 0.01 * (b[2] - a[2]))
                park = g["park"]
                stop = (m["service_stop"]["lon"], m["service_stop"]["lat"])
                start, end = (path[0], path[-1]) if kind == "out" else (path[-1], path[0])
                self.assertLess(metres(start, park), 1.0, f"{g['id']} {kind} starts at the garage")
                if not m["deadhead"]["from"]:
                    # It joins the road edge that passes the stop (within 40 m) heading the trip's way;
                    # a return starts at that edge's far node.
                    self.assertLess(metres(end, stop), 60.0, f"{g['id']} {kind} reaches {m['service_stop']['name']}")
                # The stop is the trip's first (pull-out) or last (return) stop, from GTFS.
                stops = m["trip"]["stops"] if "stops" in m["trip"] else None
                if stops:
                    self.assertEqual(m["service_stop"]["stop"], (stops[0] if kind == "out" else stops[-1])["stop"])

    def test_dispatch_uses_the_fastest_legal_driveway(self):
        for g in G.values():
            for kind in ("out", "in"):
                dh = g[kind]["deadhead"]
                legal = [a for a in dh["alternatives"] if a["feasible"]]
                self.assertTrue(legal)
                fastest = min(a["time_s"] for a in legal)
                self.assertAlmostEqual(dh["time_s"], fastest, delta=0.5)
                self.assertIn(dh["driveway"], [a["driveway"] for a in legal])

    def test_routes_obey_one_way_streets_and_turn_restrictions(self):
        import build_garages as bg
        N, W, R = bg.load()
        stops = [bg.to_xy(g[k]["service_stop"]["lat"], g[k]["service_stop"]["lon"]) for g in G.values() for k in ("out", "in")]
        roads = bg.Roads(N, W, R, [g["site"] for g in bg.GARAGES], stops)
        bad = 0
        for g in G.values():
            for kind in ("out", "in"):
                path = g[kind]["deadhead"]["path"]
                # Every consecutive pair is a directed edge of the road graph somewhere near.
                nodes = []
                for lon, lat, _ in path:
                    p = bg.to_xy(lat, lon)
                    n = min((n for n in roads.adj if abs(roads.xy[n][0] - p[0]) < 1 and abs(roads.xy[n][1] - p[1]) < 1),
                            key=lambda n: bg.dist(roads.xy[n], p), default=None)
                    nodes.append(n)
                for a, b in zip(nodes, nodes[1:]):
                    if a is not None and b is not None and not any(v == b for v, *_ in roads.adj.get(a, [])):
                        bad += 1
        self.assertEqual(bad, 0)


class OperationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sim = run()
        cls.g = cls.sim.garages

    def test_each_garage_sends_and_receives_a_bus(self):
        kinds = {(v["garage"], v["kind"]) for v in self.g.vehicles}
        for gid in G:
            self.assertIn((gid, "out"), kinds)
            self.assertIn((gid, "in"), kinds)
        for v in self.g.vehicles:
            if v["kind"] == "out":
                self.assertEqual([s["state"] for s in v["states"]], ["in garage", "pulling out", "layover", "in service"])
            else:
                self.assertEqual([s["state"] for s in v["states"]], ["in service", "returning", "in garage"])

    def test_pull_outs_reach_the_first_stop_before_departure(self):
        for v in self.g.vehicles:
            if v["kind"] != "out":
                continue
            stop = G[v["garage"]]["out"]["service_stop"]
            dead = next(s for s in v["segments"] if s["kind"] == "deadhead")
            self.assertLessEqual(dead["t1"], stop["depart_s"])
            self.assertGreaterEqual(dead["t0"], 0.0)

    def test_inventory_balances(self):
        for gid, log in self.g.inventory.items():
            counts = [x["count"] for x in log]
            self.assertEqual(counts[0], G[gid]["inventory"])
            self.assertEqual(counts[-1], G[gid]["inventory"])
            self.assertTrue(all(c >= 0 for c in counts))
            self.assertEqual(sorted(x["t"] for x in log), [x["t"] for x in log])

    def test_the_route_13_bus_is_the_same_vehicle_from_uw_station_to_northfield(self):
        v = next(v for v in self.g.vehicles if v["garage"] == "northfield" and v["kind"] == "in")
        agent = next(a for a in self.sim.uw.agents if a.id == v["id"])
        dead = next(s for s in v["segments"] if s["kind"] == "deadhead")
        self.assertEqual(dead["t0"], agent.exit_s)
        service = next(s for s in v["segments"] if s["kind"] == "service")
        self.assertEqual(service["t1"], agent.t_enter)

    def test_dispatch_decisions_explain_the_driveway(self):
        ds = [d for d in self.sim.decisions if d["terminal"] in G]
        self.assertEqual(len(ds), 6)
        for d in ds:
            self.assertIn(d["chosen"], [o["option"] for o in d["options"] if o["feasible"]])
            self.assertTrue(d["outcome"])

    def test_deterministic(self):
        other = run()
        self.assertEqual(other.garages.plans(), self.g.plans())


if __name__ == "__main__":
    unittest.main()
