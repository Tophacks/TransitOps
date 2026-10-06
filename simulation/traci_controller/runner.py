import asyncio
import os

import traci

from network_manager import ensure_network, network_file, route_file
from .control import TransitOpsController
from .telemetry import network_snapshot


class SumoSession:
    def __init__(self, step_delay=0.05):
        self.step_delay = step_delay
        self.connection = None
        self.controller = None
        self.running = False
        self.step_count = 0

    def start(self):
        ensure_network()

        sumo_binary = os.getenv("SUMO_BINARY", "sumo")
        label = f"transitops-{id(self)}"

        traci.start(
            [
                sumo_binary,
                "-n",
                str(network_file()),
                "-r",
                str(route_file()),
                "--begin",
                "0",
                "--end",
                os.getenv("SUMO_END", "5400"),
                "--step-length",
                "1",
                "--time-to-teleport",
                "-1",
                "--no-step-log",
                "true",
                "--quit-on-end",
            ],
            label=label,
        )

        self.connection = traci.getConnection(label)
        self.controller = TransitOpsController(self.connection)
        self.running = True
        self.step_count = 0

    async def stream(self):
        if not self.running:
            await asyncio.to_thread(self.start)

        try:
            while (
                self.running
                and self.connection.simulation.getMinExpectedNumber() > 0
            ):
                self.connection.simulationStep()
                self.step_count += 1
                control = self.controller.step()
                snapshot = network_snapshot(self.connection, self.step_count)
                snapshot["control"] = control
                yield snapshot
                await asyncio.sleep(self.step_delay)
        finally:
            self.close()

    def close(self):
        if self.connection is not None:
            try:
                self.connection.close(False)
            except Exception:
                pass
        self.connection = None
        self.controller = None
        self.running = False
