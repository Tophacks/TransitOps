from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException

from pydantic import BaseModel, Field

import rail_ops
from ion import IonPlan
from models import Bus
from scenario_result import case_result, simulation_result
from simulation_engine import (
    GEO_LAYOUT,
    INTERSECTION_GEO,
    INTERSECTION_LAYOUT,
    STOP_LAYOUT,
    YARD_GEO,
    TransitSimulation,
)


app = FastAPI(title="TransitOps API", version="0.2.0")


class SimulationRequest(BaseModel):
    traffic_multiplier: float = Field(default=1.25, ge=0.5, le=3.0)
    train_delay: int = Field(default=5, ge=0, le=30)
    passenger_demand: int = Field(default=135, ge=10, le=500)
    fleet_size: int = Field(default=4, ge=1, le=12)
    demand_multiplier: float = Field(default=1.0, ge=0.5, le=3.0)
    surge_passengers: int = Field(default=0, ge=0, le=350)
    surge_minute: int | None = Field(default=None, ge=1, le=59)
    breakdown_minute: int | None = Field(default=None, ge=1, le=59)
    emergency_dispatch_minute: int | None = Field(default=None, ge=1, le=59)
    seed: int = Field(default=17, ge=0, le=1000000)
    ion_headway: int = Field(default=8, ge=4, le=20)
    # An extra platform hold at an ION terminal (e.g. a door fault): the train
    # standing at, or next to stop at, that platform cannot leave until it ends.
    platform_hold_terminal: Literal["fairway", "conestoga"] | None = None
    platform_hold_platform: Literal["1", "2"] = "2"
    platform_hold_minute: int = Field(default=20, ge=0, le=59)
    platform_hold_minutes: int = Field(default=12, ge=1, le=40)
    # A track closure on the ION line (e.g. track work): trains of that
    # direction divert over the other track if a valid single-track section
    # exists, else hold until it reopens.
    closure_track: Literal["north", "south"] | None = None
    closure_from_station: str = "Grand River Hospital Station"
    closure_to_station: str = "Central Station"
    closure_from_minute: int = Field(default=15, ge=0, le=59)
    closure_to_minute: int = Field(default=45, ge=1, le=90)
    # Dutton yard: a train found faulty on arrival at a terminal from this minute
    # is withdrawn and returns to the yard; a yard lead can be blocked.
    fault_terminal: Literal["fairway", "conestoga"] | None = None
    fault_minute: int = Field(default=15, ge=0, le=59)
    yard_block_lead: Literal["north", "south"] | None = None
    yard_block_from_minute: int = Field(default=10, ge=0, le=59)
    yard_block_to_minute: int = Field(default=50, ge=1, le=90)
    # UW Station: a berth occupied by an out-of-service bus, or the Phillip Street exit blocked.
    uw_block: Literal["berth", "exit"] | None = None
    uw_block_berth: str = "P6-1078-1"
    uw_block_from_minute: int = Field(default=10, ge=0, le=59)
    uw_block_to_minute: int = Field(default=58, ge=1, le=90)
    # Private cars on Phillip Street southbound (UW Station exit) and driver errors.
    uw_cars: Literal["light", "moderate", "heavy"] = "moderate"
    driver_error: Literal["overshoot", "overshoot_blocked", "missed_berth", "wrong_turn", "seeded"] | None = None
    driver_error_rate: float = Field(default=0.2, ge=0.0, le=1.0)
    error_seed: int = 17
    # A train overshooting a platform (opt-in); recovery through Rail Control.
    rail_overshoot_station: str | None = None
    rail_overshoot_direction: Literal["north", "south"] = "north"
    rail_overshoot_minute: int = Field(default=10, ge=0, le=59)
    rail_overshoot_metres: float = Field(default=12.0, ge=2.0, le=40.0)


def network_definition():
    stops = [
        {
            "id": stop_id,
            "name": name,
            "x": x,
            "y": y,
            "lon": GEO_LAYOUT[stop_id][0],
            "lat": GEO_LAYOUT[stop_id][1],
        }
        for stop_id, (name, x, y) in STOP_LAYOUT.items()
    ]
    intersections = [
        {
            "id": iid,
            "name": name,
            "x": x,
            "y": y,
            "lon": INTERSECTION_GEO[iid][0],
            "lat": INTERSECTION_GEO[iid][1],
        }
        for iid, (name, x, y, _offset) in INTERSECTION_LAYOUT.items()
    ]
    return {
        "yard": {
            "id": "YARD",
            "name": "Corridor Staging Yard",
            "x": 88.0,
            "y": 88.0,
            "lon": YARD_GEO[0],
            "lat": YARD_GEO[1],
        },
        "stops": stops,
        "intersections": intersections,
        "route_order": [
            "UW",
            "WATERLOO_SQUARE",
            "GRAND_RIVER_HOSPITAL",
            "CENTRAL",
        ],
        "passengers_per_sprite": 6,
        "corridor_name": "Waterloo Corridor Test Scenario",
    }


def platform_hold(payload: SimulationRequest):
    if payload.platform_hold_terminal is None:
        return None
    return {
        "terminal": payload.platform_hold_terminal,
        "platform": payload.platform_hold_platform,
        "minute": payload.platform_hold_minute,
        "minutes": payload.platform_hold_minutes,
        "reason": "door fault",
    }


def closures(payload: SimulationRequest):
    if payload.closure_track is None:
        return None
    import rail_ops
    names = {st["name"] for st in rail_ops.NETWORK["stations"][payload.closure_track]}
    for name in (payload.closure_from_station, payload.closure_to_station):
        if name not in names:
            raise HTTPException(status_code=422, detail=f"{name!r} is not a {payload.closure_track}bound ION station")
    return [{
        "id": "C1", "track": payload.closure_track,
        "from_station": payload.closure_from_station, "to_station": payload.closure_to_station,
        "from_minute": payload.closure_from_minute, "to_minute": max(payload.closure_from_minute + 1, payload.closure_to_minute),
        "reason": "track work",
    }]


def fault(payload: SimulationRequest):
    if payload.fault_terminal is None:
        return None
    return {"terminal": payload.fault_terminal, "minute": payload.fault_minute}


def yard_block(payload: SimulationRequest):
    if payload.yard_block_lead is None:
        return None
    return {"lead": payload.yard_block_lead, "from_minute": payload.yard_block_from_minute,
            "to_minute": max(payload.yard_block_from_minute + 1, payload.yard_block_to_minute)}


def driver_errors(payload: SimulationRequest):
    """Driver errors: off, seeded (reproducible from error_seed), or a demo on B101's first UW visit."""
    e = payload.driver_error
    if e is None:
        return None
    if e == "seeded":
        return {"mode": "seeded", "rate": payload.driver_error_rate, "seed": payload.error_seed}
    demo = {"overshoot": ("overshoot", "P6"), "overshoot_blocked": ("overshoot", "P6"),
            "missed_berth": ("missed_berth", "P4"), "wrong_turn": ("wrong_turn", "exit")}[e]
    out = {"mode": "demo", "kind": demo[0], "vehicle": "B101", "visit": 1, "where": demo[1], "metres": 6.0}
    if e == "overshoot_blocked":
        out["follower"] = True
    return out


def rail_overshoot(payload: SimulationRequest):
    if not payload.rail_overshoot_station:
        return None
    names = {st["name"] for st in rail_ops.NETWORK["stations"][payload.rail_overshoot_direction]}
    if payload.rail_overshoot_station not in names:
        raise HTTPException(status_code=422, detail=f"{payload.rail_overshoot_station!r} is not an ION station")
    return {"station": payload.rail_overshoot_station, "direction": payload.rail_overshoot_direction,
            "after_minute": payload.rail_overshoot_minute, "metres": payload.rail_overshoot_metres}


def uw_block(payload: SimulationRequest):
    if payload.uw_block is None:
        return None
    import uw_station
    window = {"from_s": payload.uw_block_from_minute * 60.0,
              "to_s": max(payload.uw_block_from_minute + 1, payload.uw_block_to_minute) * 60.0}
    if payload.uw_block == "exit":
        return {"exit": True, **window}
    if payload.uw_block_berth not in uw_station.BERTHS:
        raise HTTPException(status_code=422, detail=f"{payload.uw_block_berth!r} is not a UW Station berth")
    return {"berth": payload.uw_block_berth, **window}


def ion_plan(payload: SimulationRequest, reassign: bool = True):
    """The ION run: physical trains through both terminals. The adaptive case
    lets dispatch give a threatened trip to another train; the baseline only
    holds departures."""
    return IonPlan(duration_min=60, reassign=reassign, **rail_settings(payload))


def rail_settings(payload):
    return dict(
        headway_min=payload.ion_headway,
        transfer_demand=payload.passenger_demand,
        delay_min=payload.train_delay,
        platform_hold=platform_hold(payload),
        closures=closures(payload),
        fault=fault(payload),
        yard_block=yard_block(payload),
        rail_overshoot=rail_overshoot(payload),
    )


def bus_settings(payload):
    return dict(
        traffic_multiplier=payload.traffic_multiplier,
        seed=payload.seed,
        demand_multiplier=payload.demand_multiplier,
        surge_minute=payload.surge_minute,
        surge_passengers=payload.surge_passengers,
        breakdown_minute=payload.breakdown_minute,
        emergency_dispatch_minute=payload.emergency_dispatch_minute,
        uw_block=uw_block(payload),
        uw_cars=payload.uw_cars,
        driver_errors=driver_errors(payload),
    )


def run_case(payload: SimulationRequest, intelligent: bool):
    sim = TransitSimulation(
        buses=[Bus(f"B{101 + i}") for i in range(payload.fleet_size)],
        ion_plan=ion_plan(payload, reassign=intelligent),
        intelligent=intelligent,
        duration=60,
        **bus_settings(payload),
    )
    metrics = sim.run()
    return case_result(sim, metrics, payload.uw_cars, sim.garages)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "TransitOps", "version": "0.2.0"}


@app.post("/api/simulate")
def simulate(payload: SimulationRequest):
    baseline = run_case(payload, intelligent=False)
    smart = run_case(payload, intelligent=True)

    return simulation_result(payload.model_dump(), network_definition(), baseline, smart)

# The browser worker runs the same standard-library-only engine. Serve an
# explicit list so a request cannot select any other file in the deployment.
WORKER_MODULES = ("continuous.py", "scenario_result.py", "simulation_engine.py", "ion.py",
                  "rail_ops.py", "yard_ops.py", "corridor.py", "uw_station.py", "road_traffic.py",
                  "drivers.py", "models.py", "garage_ops.py", "waterloo_corridor.json",
                  "ion_terminals.json", "ion_network.json", "ion_platforms.json", "uw_station.json",
                  "uw_roads.json", "bus_garages.json")


@lru_cache(maxsize=1)
def worker_sources():
    root = Path(__file__).resolve().parent
    return {name: (root / name).read_text(encoding="utf-8") for name in WORKER_MODULES}


@app.post("/api/simulate/continuous")
def continuous_settings(payload: SimulationRequest):
    return {
        "scenario": payload.model_dump(),
        "network": network_definition(),
        "rail": rail_settings(payload),
        "bus": bus_settings(payload),
        "sources": worker_sources(),
    }
