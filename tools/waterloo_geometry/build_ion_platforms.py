"""Mark each ION platform as street-running or on its own right-of-way.

Usage:
    python3 -I build_ion_platforms.py [--gtfs /home/user/gtfs/bus] [--check]

The same test the corridor builder uses to drop gates at street-running
crossings (build_corridor.find_ion_crossings): the track runs along a GRT bus
street, within 12 m and within 20 degrees, 20 to 40 m either side of the
platform. Rail recovery uses it: a train is not allowed to reverse along a
street-running platform (simulation assumption; agency practice unverified).

Contains information provided by the Regional Municipality of Waterloo under
the Region's Open Data Licence.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo import cumulative, heading, project, to_xy, turn  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUTPUTS = [ROOT / "backend" / "ion_platforms.json"]
STREET_RUNNING_M = 12.0
ANGLE = 20.0


def build(gtfs):
    corridor = json.loads((ROOT / "backend" / "waterloo_corridor.json").read_text())
    shapes = {}
    with open(gtfs / "shapes.txt") as f:
        for r in csv.DictReader(f):
            shapes.setdefault(r["shape_id"], []).append((int(r["shape_pt_sequence"]),
                                                         to_xy(float(r["shape_pt_lat"]), float(r["shape_pt_lon"]))))
    cells = {}
    segs = []
    for pts in shapes.values():
        pts = [p for _, p in sorted(pts)]
        for a, b in zip(pts, pts[1:]):
            segs.append((a, b))
            for gx in range(int(min(a[0], b[0]) // 100), int(max(a[0], b[0]) // 100) + 1):
                for gy in range(int(min(a[1], b[1]) // 100), int(max(a[1], b[1]) // 100) + 1):
                    cells.setdefault((gx, gy), []).append(len(segs) - 1)
    out, report = {}, []
    for d in ("north", "south"):
        line = corridor["ion"][d]
        path = [to_xy(p[1], p[0]) for p in line["path"]]
        cum = cumulative(path)
        rows = []
        for st in line["stations"]:
            hits = 0
            for ds in (-40, -20, 20, 40):
                s = min(cum[-1], max(0.0, st["at_m"] - 20.9 + ds))
                i = min(len(cum) - 2, next(j for j in range(len(cum) - 1) if cum[j + 1] >= s))
                f = (s - cum[i]) / max(1e-9, cum[i + 1] - cum[i])
                p = (path[i][0] + (path[i + 1][0] - path[i][0]) * f, path[i][1] + (path[i + 1][1] - path[i][1]) * f)
                h = heading(path[i], path[i + 1])
                near = set()
                for gx in range(int(p[0] // 100) - 1, int(p[0] // 100) + 2):
                    for gy in range(int(p[1] // 100) - 1, int(p[1] // 100) + 2):
                        near.update(cells.get((gx, gy), []))
                for j in near:
                    q1, q2 = segs[j]
                    a = abs(turn(h, heading(q1, q2)))
                    if min(a, 180 - a) < ANGLE and project(p, q1, q2)[2] < STREET_RUNNING_M:
                        hits += 1
                        break
            rows.append({"name": st["name"], "street_running": hits >= 2})
        out[d] = rows
        report.append(f"{d}: street-running platforms: " + ", ".join(r["name"].replace(" Station", "") for r in rows
                                                                   if r["street_running"]))
    return {"attribution": "Bus streets from GRT GTFS shapes (Region of Waterloo Open Data Licence).",
            "rule": "track within 12 m and 20 degrees of a GRT bus street at two or more of four points 20-40 m either "
                    "side of the platform", "platforms": out}, report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gtfs", default="/home/user/gtfs/bus")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    out, report = build(Path(args.gtfs))
    for line in report:
        print(line)
    text = json.dumps(out, indent=1) + "\n"
    if args.check:
        for p in OUTPUTS:
            if p.read_text() != text:
                raise SystemExit(f"{p} is stale")
        return
    for p in OUTPUTS:
        p.write_text(text)
    print("wrote", ", ".join(str(p.relative_to(ROOT)) for p in OUTPUTS))


if __name__ == "__main__":
    main()
