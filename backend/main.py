from copy import deepcopy

from fastapi import FastAPI
from pydantic import BaseModel, Field

from models import Bus, Train
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
            "name": "Regional Staging Yard",
            "x": 10.0,
            "y": 69.0,
            "lon": YARD_GEO[0],
            "lat": YARD_GEO[1],
        },
        "stops": stops,
        "intersections": intersections,
        "route_order": [
            "CONESTOGA", "NORTHFIELD", "RESEARCH_TECH", "UW", "LAURIER",
            "WATERLOO_SQUARE", "GRAND_RIVER_HOSPITAL", "CENTRAL",
            "CITY_HALL", "KITCHENER_MARKET", "FAIRWAY"
        ],
        "passengers_per_sprite": 6,
        "cities": [
            {"name": "Waterloo", "x": 31.0, "y": 23.0},
            {"name": "Kitchener", "x": 68.0, "y": 60.0},
        ],
        "corridor_name": "Waterloo-Kitchener regional sandbox",
    }


def run_case(payload: SimulationRequest, intelligent: bool):
    buses = [Bus(f"B{101 + i}") for i in range(payload.fleet_size)]

    first_wave = round(payload.passenger_demand * 0.56)
    second_wave = payload.passenger_demand - first_wave

    trains = [
        Train(
            train_id="TRAIN-A",
            scheduled_arrival=15,
            delay_minutes=payload.train_delay,
            passengers_for_bus=first_wave,
        ),
        Train(
            train_id="TRAIN-B",
            scheduled_arrival=38,
            delay_minutes=max(0, payload.train_delay // 2),
            passengers_for_bus=second_wave,
        ),
    ]

    sim = TransitSimulation(
        buses=deepcopy(buses),
        trains=trains,
        intelligent=intelligent,
        duration=90,
        traffic_multiplier=payload.traffic_multiplier,
        seed=17,
    )
    metrics = sim.run()

    return {
        "metrics": metrics_to_dict(metrics),
        "events": sim.events,
        "timeline": sim.timeline,
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
        "fewer_left_waiting": (
            baseline["metrics"]["left_waiting"] - smart["metrics"]["left_waiting"]
        ),
    }

    return {
        "scenario": payload.model_dump(),
        "network": network_definition(),
        "baseline": baseline,
        "smart": smart,
        "impact": impact,
    }
