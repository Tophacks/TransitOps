from copy import deepcopy

from fastapi import FastAPI, HTTPException
from typing import Literal

from pydantic import BaseModel, Field

from ion import IonPlan
from models import Bus
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


def metrics_to_dict(metrics):
    return {
        "passengers_arrived": metrics.total_passengers_arrived,
        "passengers_boarded": metrics.total_passengers_boarded,
        "completed_trips": metrics.completed_trips,
        "left_waiting": metrics.passengers_left_waiting,
        "average_wait": round(metrics.average_wait_minutes, 2),
        "max_queue": metrics.max_queue,
        "buses_staged": metrics.buses_staged,
        "signal_priority_requests": metrics.signal_priority_requests,
        "ion_transfers": metrics.ion_transfers,
        "ion_transfers_boarded": metrics.ion_transfers_boarded,
        "average_transfer_wait": round(metrics.average_transfer_wait_minutes, 2),
        "ion_trains_peak": metrics.ion_trains_peak,
        "ion_max_delay": round(metrics.ion_max_delay_minutes, 1),
        "connection_holds": metrics.connection_holds,
        "connection_hold_minutes": metrics.connection_hold_minutes,
        "hold_onboard_rider_minutes": round(metrics.hold_onboard_rider_minutes, 1),
        "hold_downstream_rider_minutes": round(metrics.hold_downstream_rider_minutes, 1),
        # every rider's wait, including riders still waiting when the hour ends
        "total_wait_rider_minutes": round(metrics.cumulative_wait_minutes + metrics.unserved_wait_minutes, 1),
        "total_transfer_wait_rider_minutes": round(metrics.cumulative_transfer_wait + metrics.unserved_transfer_wait_minutes, 1),
    }


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
    return IonPlan(
        duration_min=60,
        headway_min=payload.ion_headway,
        transfer_demand=payload.passenger_demand,
        delay_min=payload.train_delay,
        platform_hold=platform_hold(payload),
        reassign=reassign,
        closures=closures(payload),
        fault=fault(payload),
        yard_block=yard_block(payload),
    )


def run_case(payload: SimulationRequest, intelligent: bool):
    buses = [Bus(f"B{101 + i}") for i in range(payload.fleet_size)]
    plan = ion_plan(payload, reassign=intelligent)

    sim = TransitSimulation(
        buses=deepcopy(buses),
        ion_plan=plan,
        intelligent=intelligent,
        duration=60,
        traffic_multiplier=payload.traffic_multiplier,
        seed=payload.seed,
        demand_multiplier=payload.demand_multiplier,
        surge_minute=payload.surge_minute,
        surge_passengers=payload.surge_passengers,
        breakdown_minute=payload.breakdown_minute,
        emergency_dispatch_minute=payload.emergency_dispatch_minute,
        uw_block=uw_block(payload),
    )
    metrics = sim.run()

    return {
        "metrics": metrics_to_dict(metrics),
        "events": sim.events,
        "timeline": sim.timeline,
        "ion": plan.schedule(),
        # bus decisions: UW Station agents, station reports, holds, capacity
        "decisions": sim.decisions,
        # every vehicle on UW Station's roadway, second by second (uw_station.py)
        "uw": {"block": uw_block(payload), "vehicles": sim.uw.plans() if sim.uw else [],
               "metrics": sim.uw.metrics() if sim.uw else None},
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "TransitOps", "version": "0.2.0"}


@app.post("/api/simulate")
def simulate(payload: SimulationRequest):
    baseline = run_case(payload, intelligent=False)
    smart = run_case(payload, intelligent=True)

    impact = {
        "average_wait_reduction": round(
            baseline["metrics"]["average_wait"] - smart["metrics"]["average_wait"], 2
        ),
        "max_queue_reduction": (
            baseline["metrics"]["max_queue"] - smart["metrics"]["max_queue"]
        ),
        "additional_completed_trips": smart["metrics"]["completed_trips"] - baseline["metrics"]["completed_trips"],
        "fewer_left_waiting": (
            baseline["metrics"]["left_waiting"] - smart["metrics"]["left_waiting"]
        ),
        "transfer_wait_reduction": round(
            baseline["metrics"]["average_transfer_wait"] - smart["metrics"]["average_transfer_wait"], 2
        ),
    }

    return {
        "scenario": payload.model_dump(),
        "network": network_definition(),
        # The map plays the adaptive run; each case also carries its own ION run.
        "ion": smart["ion"],
        "baseline": baseline,
        "smart": smart,
        "impact": impact,
    }
