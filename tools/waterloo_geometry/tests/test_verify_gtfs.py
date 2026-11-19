import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRIPT = FIXTURES.parent.parent / "verify_gtfs.py"
GEOMETRY = FIXTURES / "corridor.json"
spec = importlib.util.spec_from_file_location("verify_gtfs", SCRIPT)
verify_gtfs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_gtfs)


class VerifyGtfsTests(unittest.TestCase):
    def run_cli(self, geometry=GEOMETRY, copy=GEOMETRY, *extra):
        process = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), str(FIXTURES / "gtfs"), str(geometry),
             "--frontend-copy", str(copy), "--json", *map(str, extra)],
            capture_output=True, text=True, check=False, timeout=10,
        )
        self.assertEqual(process.stderr, "")
        report = json.loads(process.stdout)
        self.assertEqual(process.returncode, 0 if report["ok"] else 1)
        return report

    def test_fixture_passes_all_required_checks(self):
        report = self.run_cli()
        self.assertTrue(report["ok"], report)
        checks = {check["id"] for check in report["checks"]}
        self.assertTrue({"geometry.frontend-copy", "ion.north.alignment", "ion.south.alignment",
                         "routes.coverage", "routes.7.1.alignment"} <= checks)

    def test_shortened_rail_path_and_missing_routes_fail(self):
        data = json.loads(GEOMETRY.read_bytes())
        data["ion"]["north"]["stations"] = data["ion"]["north"]["stations"][:5]
        del data["routes"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "short.json"
            path.write_text(json.dumps(data))
            report = self.run_cli(path, path)
        failed = {check["id"] for check in report["checks"] if not check["ok"]}
        self.assertEqual(failed, {"ion.north.station-order", "routes.coverage"})

    def test_swapped_stops_and_out_of_bounds_geometry_fail(self):
        data = json.loads(GEOMETRY.read_bytes())
        route = data["routes"][0]
        route["path"][0][0] -= 0.0001
        route["stops"].reverse()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(data))
            report = self.run_cli(path, path)
        failed = {check["id"] for check in report["checks"] if not check["ok"]}
        self.assertTrue({"routes.201.0.bounds", "routes.201.0.stop-order", "routes.201.0.stop-distances"} <= failed)

    def test_copy_comparison_is_byte_exact_and_feed_drift_is_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "copy.json"
            path.write_bytes(GEOMETRY.read_bytes() + b"\n")
            mismatch = self.run_cli(GEOMETRY, path)
            warning = self.run_cli(GEOMETRY, GEOMETRY, "--official-ion-zip", path)
        self.assertFalse(mismatch["ok"])
        self.assertEqual([check["id"] for check in mismatch["checks"] if not check["ok"]], ["geometry.frontend-copy"])
        self.assertTrue(warning["ok"])
        self.assertIn("Warning:", warning["checks"][-1]["detail"])

    def test_path_check_detects_a_shortcut_with_matching_endpoints(self):
        chord = [((0, 0), (10, 0))]
        turn = [((0, 0), (5, 5)), ((5, 5), (10, 0))]
        self.assertEqual(verify_gtfs.compare_paths(chord, turn, 1.7), (False, False))
        self.assertTrue(verify_gtfs.within_distance(chord, chord, 0.5))


if __name__ == "__main__":
    unittest.main()
