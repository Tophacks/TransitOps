"""Incidents that happen during an ordinary run, drawn from the run's seed.

Generation is separate from every decision: the list is drawn once, from
the seed, the run length and the rates, before any driver or dispatcher
acts. Both policies (baseline and adaptive) receive the same list, so they
are compared against the same incidents, and a replay of the seed (with the
same dispatcher orders) reproduces them exactly.

Kinds and rates (per hour of run, modelled; docs/incidents.md):

- bus_breakdown: an in-service bus is withdrawn. Its target is fixed by
  the generator, not by the outcome: a Route 7 block that has a trip
  scheduled then, or one of the corridor run's buses by number. If that bus
  is not in service at that minute in a given run, the incident is recorded
  as not applied there, with the reason.
- train_fault: an ION train arriving at a terminal from that minute is found
  faulty and withdrawn to Dutton yard (at most one per run: rail_ops models
  one fault).
- passenger_surge: a crowd arrives at once at UW Station (60-180 riders).
- station_delay: a stop's berths on a scheduled line are unavailable for
  5-12 minutes (a stalled vehicle or a curb obstruction).

Driver errors (overshoots, missed berths, missed stops, wrong turns) are
drawn per opportunity by drivers.Errors from the same seed, at
DRIVER_ERROR_RATE.
"""
import json
import math
import random
from pathlib import Path

from drivers import _unit

LINES = json.loads((Path(__file__).resolve().parent / "lines.json").read_text())
HOUR0 = 7 * 3600 + 30 * 60

RATES = {"bus_breakdown": 0.30, "train_fault": 0.10, "passenger_surge": 0.25, "station_delay": 0.30}
DRIVER_ERROR_RATE = 0.04
CAPS = {"train_fault": 1}
FIRST_MINUTE = 5          # nothing in the first minutes, while the run warms up


def _rng(seed, *parts):
    return random.Random(int(_unit(seed, "incidents", *parts) * 2 ** 53))


def generate(seed, duration_min, rates=None, fleet_size=3, routes=("7",)):
    rates = {**RATES, **(rates or {})}
    out = []
    span = max(1, duration_min - FIRST_MINUTE - 2)
    for kind in sorted(rates):
        rate = float(rates[kind])
        rng = _rng(seed, kind)
        n = _poisson(rng, rate * duration_min / 60.0)
        n = min(n, CAPS.get(kind, n))
        minutes = sorted(FIRST_MINUTE + int(rng.random() * span) for _ in range(n))
        for m in minutes:
            inc = {"minute": m, "kind": kind}
            if kind == "bus_breakdown":
                blocks = _blocks_running(m, routes)
                if blocks and rng.random() < 0.6:
                    inc.update(target="line", block=rng.choice(blocks),
                               detail="a Route 7 bus (the block that is due to be running then)")
                else:
                    inc.update(target="corridor", vehicle=f"B{101 + rng.randrange(fleet_size)}",
                               detail="one of the corridor run's buses")
            elif kind == "train_fault":
                inc.update(terminal=rng.choice(["fairway", "conestoga"]))
            elif kind == "passenger_surge":
                inc.update(stop="UW", riders=rng.randrange(60, 181, 10))
            elif kind == "station_delay":
                stations = _line_stations(routes)
                inc.update(stop=rng.choice(stations), minutes=rng.randrange(5, 13))
            out.append(inc)
    out.sort(key=lambda x: (x["minute"], x["kind"]))
    for i, inc in enumerate(out):
        inc["id"] = f"I{i + 1:02d}"
    return out


def _blocks_running(minute, routes):
    t = HOUR0 + minute * 60
    out = set()
    for r in routes:
        for trip in LINES["routes"].get(r, {}).get("trips", []):
            if trip["gtfs"][0][1] <= t < trip["gtfs"][-1][0]:
                out.add(trip["block"])
    return sorted(out)


def _line_stations(routes):
    out = set()
    for r in routes:
        for p in LINES["routes"].get(r, {}).get("patterns", []):
            if p["trips"] < 10:
                continue
            for st in p["stops"][1:-1]:
                if "ion" in LINES["stops"][st["stop"]]:
                    out.add(st["stop"])
    return sorted(out)


def _poisson(rng, mean):
    k, p, limit = 0, 1.0, math.exp(-mean)
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1
