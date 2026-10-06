from copy import deepcopy

from controller import TransitController
from models import Bus, Train
from simulation_engine import TransitSimulation


BUSES = [
    Bus("BUS-101"),
    Bus("BUS-102"),
    Bus("BUS-103"),
    Bus("BUS-104"),
]

TRAINS = [
    Train(
        train_id="TRAIN-A",
        scheduled_arrival=15,
        delay_minutes=5,
        passengers_for_bus=75,
    ),
    Train(
        train_id="TRAIN-B",
        scheduled_arrival=38,
        delay_minutes=3,
        passengers_for_bus=60,
    ),
]

TRAFFIC = {
    0: 1.0,
    15: 1.25,
    30: 1.55,
    45: 1.15,
}


def run_scenario(name, intelligent):
    controller = TransitController(enabled=intelligent)

    sim = TransitSimulation(
        buses=deepcopy(BUSES),
        trains=deepcopy(TRAINS),
        controller=controller,
        duration=60,
        traffic_profile=TRAFFIC,
    )

    metrics = sim.run()

    print()
    print("=" * 72)
    print(name)
    print("=" * 72)

    for minute, event in sim.events:
        print(f"{minute:02d}:00 | {event}")

    print()
    print("KPIs")
    print("-" * 40)
    print(f"Passengers arrived:     {metrics.total_passengers_arrived}")
    print(f"Passengers boarded:     {metrics.total_passengers_boarded}")
    print(f"Passengers left waiting:{metrics.missed_connections}")
    print(f"Average wait:           {metrics.average_wait_minutes:.2f} min")
    print(f"Maximum queue:          {metrics.max_queue}")
    print(f"Buses pre-staged:       {metrics.buses_staged}")
    print(f"Hold commands:          {metrics.holds_issued}")

    return metrics


def main():
    baseline = run_scenario("BASELINE CONTROL", intelligent=False)
    smart = run_scenario("TRANSITOPS CONTROL", intelligent=True)

    print()
    print("=" * 72)
    print("IMPACT")
    print("=" * 72)

    wait_change = baseline.average_wait_minutes - smart.average_wait_minutes
    queue_change = baseline.max_queue - smart.max_queue
    stranded_change = baseline.missed_connections - smart.missed_connections

    print(f"Average wait reduction: {wait_change:+.2f} min")
    print(f"Maximum queue reduction:{queue_change:+d}")
    print(f"Fewer left waiting:     {stranded_change:+d}")


if __name__ == "__main__":
    main()
