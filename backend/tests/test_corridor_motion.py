import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import corridor  # noqa: E402
from models import Bus, Train  # noqa: E402
from simulation_engine import ROUTE, TransitSimulation  # noqa: E402

M_PER_DEG = 111_320.0


def metres(a, b):
    k = math.cos(math.radians(43.465))
    return math.hypot((a[0] - b[0]) * M_PER_DEG * k, (a[1] - b[1]) * M_PER_DEG)


def distance_to_path(point, path):
    best = math.inf
    k = math.cos(math.radians(43.465))
    px, py = point[0] * M_PER_DEG * k, point[1] * M_PER_DEG
    for a, b in zip(path, path[1:]):
        ax, ay = a[0] * M_PER_DEG * k, a[1] * M_PER_DEG
        bx, by = b[0] * M_PER_DEG * k, b[1] * M_PER_DEG
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy or 1.0
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
        best = min(best, math.hypot(px - ax - dx * t, py - ay - dy * t))
    return best


def simulate(**kwargs):
    sim = TransitSimulation(
        [Bus(f"B{101 + i}") for i in range(4)],
        [Train("T1", 12, 3, 60), Train("T2", 32, 1, 40)],
        intelligent=True,
        duration=60,
        traffic_multiplier=1.3,
        **kwargs,
    )
    sim.run()
    return sim


class CorridorGeometryTests(unittest.TestCase):
    def test_frontend_and_backend_share_one_geometry_file(self):
        frontend = ROOT.parent / "frontend" / "lib" / "geo" / "waterloo-corridor.json"
        self.assertEqual(json.loads(frontend.read_text()), corridor.DATA)

    def test_every_simulated_leg_has_a_street_path(self):
        for a, b in zip(ROUTE, ROUTE[1:] + ROUTE[:1]):
            seg = corridor.segment(a, b)
            self.assertGreater(seg["length_m"], 500)
            # A street path is longer than the straight line between stops.
            start, end = seg["path"][0], seg["path"][-1]
            self.assertGreater(seg["length_m"], metres(start, end))
        self.assertIn(("YARD", "CENTRAL"), corridor.SEGMENTS)

    def test_paths_turn_with_rounded_corners_not_kinks(self):
        k = math.cos(math.radians(43.465))
        for seg in corridor.DATA["segments"]:
            pts = [(lon * M_PER_DEG * k, lat * M_PER_DEG) for lon, lat, _ in seg["path"]]
            for a, b, c in zip(pts, pts[1:], pts[2:]):
                h0 = math.atan2(b[0] - a[0], b[1] - a[1])
                h1 = math.atan2(c[0] - b[0], c[1] - b[1])
                turn = abs((math.degrees(h1 - h0) + 540) % 360 - 180)
                self.assertLess(turn, 25, f"{seg['from']}->{seg['to']} has a {turn:.0f}° kink")

    def test_ion_tracks_reach_campus_and_include_the_uptown_curve(self):
        north = corridor.DATA["ion"]["north"]
        names = [station["name"] for station in north["stations"]]
        self.assertEqual(names[:2], ["Allen Station", "Waterloo Public Square Station"])
        self.assertIn("University Of Waterloo Station", names)
        self.assertTrue(any(limit < 6 for _, limit in north["curves"]))


class KinematicsTests(unittest.TestCase):
    def test_run_starts_and_ends_at_rest_and_keeps_schedule(self):
        run = corridor.bus_run("UW", "WATERLOO_SQUARE", 5)
        self.assertAlmostEqual(run.duration, 300.0, places=6)
        self.assertEqual(run.distance(0), 0.0)
        self.assertEqual(run.distance(300), run.length)
        self.assertLess(run.speed_at(1), 1.5)
        self.assertLess(run.speed_at(299), 1.5)

    def test_distance_never_decreases(self):
        run = corridor.bus_run("CENTRAL", "UW", 9, 1)
        previous = 0.0
        for tenth in range(0, 5401):
            s = run.distance(tenth / 10)
            self.assertGreaterEqual(s, previous - 1e-9)
            previous = s

    def test_bus_brakes_before_the_stop(self):
        run = corridor.bus_run("WATERLOO_SQUARE", "GRAND_RIVER_HOSPITAL", 5)
        speeds = [run.speed_at(t) for t in range(240, 300)]
        self.assertTrue(all(b <= a + 0.05 for a, b in zip(speeds[-15:], speeds[-14:])))

    def test_bus_slows_for_the_corner_onto_university(self):
        seg = corridor.segment("UW", "WATERLOO_SQUARE")
        run = corridor.bus_run("UW", "WATERLOO_SQUARE", 5)
        corner = min(seg["curves"], key=lambda item: item[1])
        times = [t / 4 for t in range(0, 1201)]
        at_corner = min(times, key=lambda t: abs(run.distance(t) - corner[0]))
        self.assertLess(run.speed_at(at_corner), max(run.speed) * 0.6)

    def test_red_signal_hold_stops_the_bus_at_the_stop_line(self):
        seg = corridor.segment("UW", "WATERLOO_SQUARE")
        run = corridor.bus_run("UW", "WATERLOO_SQUARE", 6, 1)
        line = seg["signal"]["stop_line_m"]
        self.assertLess(abs(run.hold_position - line), 1.01)
        held = [t for t in range(0, 360) if run.distance(t) == run.hold_position]
        self.assertGreaterEqual(len(held), 59)
        self.assertEqual(held[-1] - held[0], len(held) - 1)
        self.assertAlmostEqual(run.duration, 360.0, places=6)


class ParityFixtureTests(unittest.TestCase):
    def test_browser_parity_fixture_is_current(self):
        import parity_fixture

        self.assertEqual(
            parity_fixture.FIXTURE.read_text(), parity_fixture.text(),
            "run `python3 parity_fixture.py` in backend/ to refresh the browser parity fixture",
        )


class SimulatorMotionTests(unittest.TestCase):
    def test_snapshot_positions_lie_on_the_street_path(self):
        sim = simulate(breakdown_minute=20)
        checked = 0
        for tick in sim.timeline:
            for bus in tick["buses"]:
                motion = bus["segment"]
                if motion is None:
                    continue
                seg = corridor.segment(motion["from"], motion["to"])
                offset = corridor.lateral_offset(seg, motion["distance_m"])
                gap = distance_to_path((bus["lon"], bus["lat"]), seg["path"])
                self.assertLess(abs(gap - offset), 1.0)
                self.assertAlmostEqual(bus["progress"], motion["distance_m"] / motion["length_m"], places=2)
                checked += 1
        self.assertGreater(checked, 100)

    def test_dwelling_bus_waits_at_the_curb_stop(self):
        sim = simulate()
        dwells = [bus for tick in sim.timeline for bus in tick["buses"] if bus["state"] == "DWELLING"]
        self.assertTrue(dwells)
        gaps = []
        for bus in dwells:
            stop = corridor.STOPS[bus["segment"]["to"]]
            gaps.append(metres((bus["lon"], bus["lat"]), (stop["lon"], stop["lat"])))
        # The bus pulls in beside the stop pole; Grand River Hospital's pole
        # sits in a recessed bay, so allow more room there.
        self.assertLess(max(gaps), 9)
        self.assertLess(sorted(gaps)[len(gaps) // 2], 3)

    def test_broken_down_bus_holds_position(self):
        sim = simulate(breakdown_minute=20)
        positions = [
            (bus["lon"], bus["lat"])
            for tick in sim.timeline
            for bus in tick["buses"]
            if bus["state"] == "BROKEN_DOWN"
        ]
        self.assertGreaterEqual(len(positions), 5)
        self.assertEqual(len(set(positions)), 1)

    def test_snapshot_exposes_segment_timing_for_playback(self):
        sim = simulate()
        moving = next(bus for tick in sim.timeline for bus in tick["buses"] if bus["state"] == "EN_ROUTE")
        self.assertEqual(
            set(moving["segment"]),
            {"from", "to", "elapsed", "minutes", "hold_minutes", "length_m", "distance_m"},
        )


if __name__ == "__main__":
    unittest.main()
