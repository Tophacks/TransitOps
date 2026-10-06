import os

import traci

from network_manager import network_file, network_ready, route_file


def main():
    if not network_ready():
        raise SystemExit("SUMO network assets are missing.")

    label = "transitops-smoke"

    traci.start(
        [
            os.getenv("SUMO_BINARY", "sumo"),
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

    try:
        for _ in range(180):
            connection.simulationStep()
            max_vehicles = max(max_vehicles, len(connection.vehicle.getIDList()))
    finally:
        connection.close(False)

    if max_vehicles <= 0:
        raise SystemExit("SUMO started, but no generated traffic entered the network.")

    print(
        f"TransitOps SUMO smoke test passed: "
        f"network={network_file().name}, max_vehicles={max_vehicles}"
    )


if __name__ == "__main__":
    main()
