#!/usr/bin/env python3
"""Independently verify a TransitOps map against archived GRT GTFS."""
import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import csv
import io
import json
import math
from pathlib import Path
import sys
from zipfile import BadZipFile, ZipFile

ROUTES = ("201", "202", "31", "7")
SOUTH, NORTH, WEST, EAST = 43.452, 43.492, -80.566, -80.505
METRES_LAT = 111_320.0
METRES_LON = METRES_LAT * math.cos(math.radians(43.465))
ION_DIRECTIONS = {"north": ("Fairway", "Conestoga"), "south": ("Conestoga", "Fairway")}
INPUT_ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError, csv.Error, BadZipFile)


def xy(lon, lat):
    return (lon - WEST) * METRES_LON, (lat - SOUTH) * METRES_LAT


def distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def interpolate(a, b, fraction):
    return tuple(start + (end - start) * fraction for start, end in zip(a, b))


def point_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    norm = dx * dx + dy * dy
    if not norm:
        return distance(p, a)
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / norm
    return distance(p, interpolate(a, b, max(0, min(1, t))))


def clip(a, b):
    """Clip in projected metres to the common comparison window."""
    low, high = 0.0, 1.0
    for start, end, limit in zip(a, b, xy(EAST, NORTH)):
        delta = end - start
        if not delta:
            if not 0 <= start <= limit:
                return None
            continue
        enter, leave = sorted((-start / delta, (limit - start) / delta))
        low, high = max(low, enter), min(high, leave)
        if low >= high:
            return None
    return interpolate(a, b, low), interpolate(a, b, high)


def strip_platform_spurs(points):
    # GTFS platform detours leave the track, then return within a metre of
    # their departure point. Keep the departure point, removing the return.
    cleaned = []
    for point in points:
        if len(cleaned) >= 2 and distance(cleaned[-2], point) < 1.0:
            cleaned.pop()
        else:
            cleaned.append(point)
    return cleaned


class SegmentIndex:
    """Grid index for tolerance queries. Long segments are indexed in chunks."""

    cell_size = 24

    def __init__(self, segments, tolerance):
        self.tolerance = tolerance
        self.cells = defaultdict(list)
        for a, b in segments:
            count = max(1, math.ceil(distance(a, b) / self.cell_size))
            for i in range(count):
                p, q = interpolate(a, b, i / count), interpolate(a, b, (i + 1) / count)
                for x in self.cell_range(p[0], q[0]):
                    for y in self.cell_range(p[1], q[1]):
                        self.cells[x, y].append((p, q))

    def cell_range(self, a, b):
        return range(
            math.floor((min(a, b) - self.tolerance) / self.cell_size),
            math.floor((max(a, b) + self.tolerance) / self.cell_size) + 1,
        )

    def nearby(self, point):
        cell = tuple(math.floor(value / self.cell_size) for value in point)
        return [
            (a, b) for a, b in self.cells.get(cell, [])
            if point_segment(point, a, b) <= self.tolerance + 1e-8
        ]


def within_distance(source, target, tolerance):
    """Check continuous coverage, with conservative failure at sub-mm ambiguity.

    A segment's tolerance neighbourhood is convex. If both endpoints of a
    source segment are in one such neighbourhood, its entire length is too.
    Otherwise bisect until a covering segment or a counterexample is found.
    """
    index = SegmentIndex(target, tolerance)
    for a, b in source:
        pending = [(a, b)]
        while pending:
            p, q = pending.pop()
            near = index.nearby(p)
            if any(point_segment(q, c, d) <= tolerance + 1e-8 for c, d in near):
                continue
            if not near or not index.nearby(q):
                return False
            if distance(p, q) < 0.0001:
                # Avoid unbounded subdivision when two tolerance capsules
                # touch exactly. Do not certify geometry near that boundary.
                return False
            midpoint = interpolate(p, q, 0.5)
            pending.extend(((p, midpoint), (midpoint, q)))
    return bool(source and target)


def compare_paths(observed, reference, tolerance):
    return (
        within_distance(observed, reference, tolerance),
        within_distance(reference, observed, tolerance),
    )


@contextmanager
def feed_source(path):
    if path.is_dir():
        def rows(name):
            with (path / name).open(encoding="utf-8-sig", newline="") as source:
                yield from csv.DictReader(source)
        yield rows
    else:
        with ZipFile(path) as archive:
            def rows(name):
                with archive.open(name) as source:
                    yield from csv.DictReader(io.TextIOWrapper(source, encoding="utf-8-sig", newline=""))
            yield rows


class Feed:
    def __init__(self, path, wanted_routes):
        with feed_source(path) as rows:
            ids = {
                row["route_id"]: row["route_short_name"]
                for row in rows("routes.txt") if row["route_short_name"] in wanted_routes
            }
            self.trips = {
                row["trip_id"]: (ids[row["route_id"]], row.get("direction_id", ""), row["shape_id"])
                for row in rows("trips.txt") if row["route_id"] in ids
            }
            wanted_shapes = {trip[2] for trip in self.trips.values()}
            self.stops = {
                row["stop_id"]: (row["stop_name"], xy(float(row["stop_lon"]), float(row["stop_lat"])))
                for row in rows("stops.txt")
            }
            shape_rows = defaultdict(list)
            for row in rows("shapes.txt"):
                if row["shape_id"] in wanted_shapes:
                    shape_rows[row["shape_id"]].append((
                        int(row["shape_pt_sequence"]),
                        xy(float(row["shape_pt_lon"]), float(row["shape_pt_lat"])),
                    ))
            self.shapes = {key: [p for _, p in sorted(values)] for key, values in shape_rows.items()}
            stop_rows = defaultdict(list)
            for row in rows("stop_times.txt"):
                if row["trip_id"] in self.trips:
                    stop_rows[row["trip_id"]].append((int(row["stop_sequence"]), row["stop_id"]))
            self.patterns = defaultdict(set)
            for trip_id, values in stop_rows.items():
                self.patterns[self.trips[trip_id]].add(tuple(stop for _, stop in sorted(values)))

    def patterns_for_shape(self, route, shape_id):
        return {
            pattern for (number, _, shape), patterns in self.patterns.items()
            if number == route and shape == shape_id for pattern in patterns
        }


class Report:
    def __init__(self):
        self.checks = []

    def add(self, check_id, ok, detail):
        self.checks.append({"id": check_id, "ok": bool(ok), "detail": detail})

    @property
    def ok(self):
        return all(check["ok"] for check in self.checks)


def path_metrics(entry):
    points = []
    distances = []
    for lon, lat, metres in entry["path"]:
        if not all(math.isfinite(value) for value in (lon, lat, metres)):
            raise ValueError("coordinates or cumulative metres are non-finite")
        points.append(xy(lon, lat))
        distances.append(metres)
    if len(points) < 2:
        raise ValueError("a path needs at least two vertices")
    length = 0.0
    error = abs(distances[0])
    for a, b, reported in zip(points, points[1:], distances[1:]):
        length += distance(a, b)
        error = max(error, abs(length - reported))
    declared = entry["length_m"]
    monotone = all(a <= b for a, b in zip(distances, distances[1:]))
    return points, length, max(error, abs(length - declared)), monotone and math.isfinite(declared)


def report_stops(report, prefix, feed, stops, segments, tolerance, length):
    positions = [stop["at_m"] for stop in stops]
    increasing = (
        bool(positions) and all(math.isfinite(s) and 0 <= s <= length + 0.5 for s in positions)
        and all(a < b for a, b in zip(positions, positions[1:]))
    )
    report.add(f"{prefix}.stop-distances", increasing, "Stop distances must increase and lie on the path.")
    offsets = []
    missing = []
    for stop in stops:
        stop_id = str(stop["id"])
        if stop_id not in feed.stops:
            missing.append(stop_id)
        else:
            offsets.append(min(point_segment(feed.stops[stop_id][1], a, b) for a, b in segments))
    worst = max(offsets, default=math.inf)
    detail = f"Maximum GTFS stop offset {worst:.2f} m (limit {tolerance:g} m)."
    if missing:
        detail += f" Unknown GTFS stops: {', '.join(missing)}."
    report.add(f"{prefix}.stop-offsets", not missing and worst <= tolerance, detail)


def check_ion(report, data, feed):
    for direction, terminals in ION_DIRECTIONS.items():
        prefix = f"ion.{direction}"
        try:
            entry = data["ion"][direction]
            shape_id = entry["shape_id"]
            reference = feed.shapes.get(shape_id)
            report.add(f"{prefix}.shape", reference is not None, f"GTFS shape {shape_id}.")
            ids = tuple(str(stop["id"]) for stop in entry["stations"])
            patterns = feed.patterns_for_shape("301", shape_id)
            names = [feed.stops.get(stop, ("", None))[0] for stop in ids]
            correct_terminals = bool(names) and names[0].startswith(terminals[0]) and names[-1].startswith(terminals[1])
            report.add(
                f"{prefix}.station-order",
                len(ids) == 16 and ids in patterns and correct_terminals,
                f"{len(ids)} platforms: expected 16 in GTFS trip order, {terminals[0]} to {terminals[1]}.",
            )
            points, length, error, monotone = path_metrics(entry)
            report.add(f"{prefix}.cumulative", monotone and error <= 0.5, f"Maximum cumulative distance error {error:.3f} m (limit 0.5 m).")
            observed = list(zip(points, points[1:]))
            report_stops(report, prefix, feed, entry["stations"], observed, 10, length)
            if reference is None:
                continue
            track = strip_platform_spurs(reference)
            expected = list(zip(track, track[1:]))
            expected_length = sum(distance(a, b) for a, b in expected)
            relative_error = abs(entry["length_m"] - expected_length) / expected_length if expected_length else math.inf
            report.add(
                f"{prefix}.length", relative_error <= 0.005,
                f"Declared {entry['length_m']:.1f} m vs de-spurred GTFS {expected_length:.1f} m ({relative_error:.2%} difference, limit 0.5%).",
            )
            forward, backward = compare_paths(observed, expected, 0.5)
            report.add(f"{prefix}.alignment", forward and backward, alignment_detail(0.5, forward, backward))
        except INPUT_ERRORS as error:
            report.add(f"{prefix}.data", False, f"Invalid ION data: {error}")


def alignment_detail(tolerance, forward, backward):
    return (
        f"Within {tolerance:g} m: geometry to GTFS {'yes' if forward else 'no'}, "
        f"GTFS to geometry {'yes' if backward else 'no'}."
    )


def is_subsequence(stops, pattern):
    remaining = iter(pattern)
    return all(stop in remaining for stop in stops)


def check_routes(report, data, feed):
    routes = data.get("routes", [])
    expected_keys = Counter((number, direction) for number in ROUTES for direction in ("0", "1"))
    keys = Counter((str(route["route"]), str(route["direction"])) for route in routes)
    report.add("routes.coverage", keys == expected_keys, f"Found {len(routes)} entries. Expected routes 201, 202, 31 and 7 once per direction.")
    for entry in routes:
        number, direction = str(entry["route"]), str(entry["direction"])
        prefix = f"routes.{number}.{direction}"
        try:
            shape_id = entry["shape_id"]
            counts = Counter(shape for route, side, shape in feed.trips.values() if (route, side) == (number, direction))
            reference = feed.shapes.get(shape_id)
            top_count = max(counts.values(), default=0)
            report.add(
                f"{prefix}.shape", reference is not None and top_count > 0 and counts[shape_id] == top_count,
                f"Shape {shape_id} has {counts[shape_id]} trips; most frequent has {top_count}.",
            )
            points, length, _, _ = path_metrics(entry)
            inside = all(WEST <= lon <= EAST and SOUTH <= lat <= NORTH for lon, lat, _ in entry["path"])
            report.add(f"{prefix}.bounds", inside, "All vertices must lie inside the Waterloo corridor box.")
            segments = list(zip(points, points[1:]))
            if reference is not None:
                clipped = [part for a, b in zip(reference, reference[1:]) if (part := clip(a, b)) is not None]
                forward, backward = compare_paths(segments, clipped, 1.7)
                report.add(f"{prefix}.alignment", forward and backward, alignment_detail(1.7, forward, backward))
            ids = tuple(str(stop["id"]) for stop in entry["stops"])
            patterns = feed.patterns.get((number, direction, shape_id), ())
            report.add(
                f"{prefix}.stop-order", bool(ids) and any(is_subsequence(ids, pattern) for pattern in patterns),
                f"{len(ids)} stops must occur in order on a GTFS trip using this shape.",
            )
            report_stops(report, prefix, feed, entry["stops"], segments, 25, length)
        except INPUT_ERRORS as error:
            report.add(f"{prefix}.data", False, f"Invalid route data: {error}")


def warn_current(report, data, bus_zip, ion_zip):
    for mode, path, routes in (("bus", bus_zip, ROUTES), ("ion", ion_zip, ("301",))):
        if path is None:
            continue
        drift = []
        try:
            feed = Feed(path, routes)
            if mode == "ion":
                for direction, entry in data["ion"].items():
                    shape_id = entry["shape_id"]
                    ids = tuple(str(stop["id"]) for stop in entry["stations"])
                    if ids not in feed.patterns_for_shape("301", shape_id):
                        drift.append(f"{direction} shape or stop list changed")
            else:
                for entry in data.get("routes", []):
                    trip = (str(entry["route"]), str(entry["direction"]), entry["shape_id"])
                    if trip not in feed.patterns:
                        drift.append(f"route {trip[0]} direction {trip[1]} shape changed")
        except INPUT_ERRORS as error:
            drift.append(f"could not read or compare feed: {error}")
        detail = "Warning: " + "; ".join(drift) if drift else "Current feed matches the checked references."
        report.add(f"current.{mode}", True, detail)


def verify(gtfs_dir, corridor_json, frontend_copy=None, official_zip=None, official_ion_zip=None):
    report = Report()
    if frontend_copy is None:
        frontend_copy = corridor_json.parent.parent / "frontend/lib/geo/waterloo-corridor.json"
    try:
        content = corridor_json.read_bytes()
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("the root must be an object")
    except INPUT_ERRORS as error:
        report.add("geometry.read", False, str(error))
        return report
    try:
        report.add("geometry.frontend-copy", content == frontend_copy.read_bytes(), f"Compared bytes with {frontend_copy}.")
    except INPUT_ERRORS as error:
        report.add("geometry.frontend-copy", False, f"Could not read {frontend_copy}: {error}")
    report.add("geometry.size", len(content) < 100 * 1024, f"{len(content):,} bytes (limit under 102,400).")
    for subdir, wanted, check in (("lrt", ("301",), check_ion), ("bus", ROUTES, check_routes)):
        try:
            feed = Feed(gtfs_dir / subdir, wanted)
            check(report, data, feed)
        except INPUT_ERRORS as error:
            report.add(f"{subdir}.read", False, str(error))
    warn_current(report, data, official_zip, official_ion_zip)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gtfs_dir", type=Path)
    parser.add_argument("corridor_json", type=Path)
    parser.add_argument("--frontend-copy", type=Path)
    parser.add_argument("--official-zip", type=Path)
    parser.add_argument("--official-ion-zip", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    report = verify(args.gtfs_dir, args.corridor_json, args.frontend_copy, args.official_zip, args.official_ion_zip)
    if args.as_json:
        print(json.dumps({"ok": report.ok, "checks": report.checks}, indent=2))
    else:
        for check in report.checks:
            status = "WARN" if check["detail"].startswith("Warning:") else "PASS" if check["ok"] else "FAIL"
            print(f"{status} {check['id']}  {check['detail']}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
