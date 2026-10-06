from copy import deepcopy

from fastapi import FastAPI
from pydantic import BaseModel, Field

from controller import TransitController
from models import Bus, Train
from simulation_engine import TransitSimulation


app = FastAPI(title="TransitOps API", version="0.1.0")


class SimulationRequest(BaseModel):
    traffic_multiplier: float = Field(default=1.25, ge=0.5, le=3.0)
    train_delay: int = Field(default=5, ge=0, le=30)
    passenger_demand: int = Field(default=135, ge=10, le=500)
    fleet_size: int = Field(default=4, ge=1, le=12)


def metrics_to_dict(metrics):
    return {
        "passengers_arrived": metrics.total_passengers_arrived,
        "passengers_boarded": metrics.total_passengers_boarded,
        "left_waiting": metrics.missed_connections,
        "average_wait": round(metrics.average_wait_minutes, 2),
        "max_queue": metrics.max_queue,
        "buses_staged": metrics.buses_staged,
        "holds_issued": metrics.holds_issued,
    }


def run_case(payload: SimulationRequest, intelligent: bool):
    buses = [Bus(f"BUS-{101 + i}") for i in range(payload.fleet_size)]

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

    controller = TransitController(enabled=intelligent)

    sim = TransitSimulation(
        buses=deepcopy(buses),
        trains=trains,
        controller=controller,
        duration=60,
        traffic_multiplier=payload.traffic_multiplier,
    )

    metrics = sim.run()

    return {
        "metrics": metrics_to_dict(metrics),
        "events": sim.events,
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "TransitOps"}


@app.post("/api/simulate")
def simulate(payload: SimulationRequest):
    baseline = run_case(payload, intelligent=False)
    smart = run_case(payload, intelligent=True)

    impact = {
        "average_wait_reduction": round(
            baseline["metrics"]["average_wait"] - smart["metrics"]["average_wait"], 2
        ),
        "max_queue_reduction": baseline["metrics"]["max_queue"] - smart["metrics"]["max_queue"],
        "fewer_left_waiting": baseline["metrics"]["left_waiting"] - smart["metrics"]["left_waiting"],
    }

    return {
        "scenario": payload.model_dump(),
        "baseline": baseline,
        "smart": smart,
        "impact": impact,
    }
