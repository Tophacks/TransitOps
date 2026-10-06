import os

import traci
import sumolib

from network_manager import network_file, network_ready, route_file


def main():
    if not network_ready():
        raise SystemExit("SUMO corridor assets are missing.")

    label = "transitops-smoke"

    traci.start(
        [
            os.getenv("SUMO_BINARY") or sumolib.checkBinary("sumo"),
            "-n",
            str(network_file()),
            "-r",
            str(route_file()),
            "--begin",
            "0",
            "--end",
            "180",
            "--step-length",
            "1",
            "--no-step-log",
            "true",
            "--quit-on-end",
        ],
        label=label,
    )

    connection = traci.getConnection(label)
    max_vehicles = 0
    buses_seen = set()
    tls_seen = set(connection.trafficlight.getIDList())

    try:
        for _ in range(180):
            connection.simulationStep()
            ids = set(connection.vehicle.getIDList())
            max_vehicles = max(max_vehicles, len(ids))
            buses_seen.update(vehicle_id for vehicle_id in ids if vehicle_id.startswith("B"))
    finally:
        connection.close(False)

    if max_vehicles <= 0:
        raise SystemExit("SUMO started, but no road traffic entered the corridor.")
    if not buses_seen:
        raise SystemExit("SUMO started, but no transit buses entered the corridor.")
    if len(tls_seen) < 3:
        raise SystemExit(f"Expected 3 traffic lights, found {len(tls_seen)}.")

    print(
        "TransitOps corridor smoke test passed: "
        f"network={network_file().name}, max_vehicles={max_vehicles}, "
        f"buses={sorted(buses_seen)}, signals={sorted(tls_seen)}"
    )


if __name__ == "__main__":
    main()
