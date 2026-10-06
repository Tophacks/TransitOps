import asyncio
import os
from pathlib import Path

import traci

from .control import TransitOpsController
from .telemetry import network_snapshot


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "sumo" / "scenarios" / "waterloo_kitchener.sumocfg"


class SumoSession:
    def __init__(self, config_path=None, step_delay=0.05):
        self.config_path = Path(config_path or DEFAULT_CONFIG)
        self.step_delay = step_delay
        self.connection = None
        self.controller = None
        self.running = False
        self.step_count = 0

    def _validate(self):
        if not self.config_path.exists():
            raise FileNotFoundError(f"SUMO configuration not found: {self.config_path}")

        network_path = ROOT / "sumo" / "network" / "waterloo_kitchener.net.xml"
        route_path = ROOT / "sumo" / "routes" / "background.rou.xml"

        missing = [str(path) for path in (network_path, route_path) if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "SUMO network has not been generated yet. Missing: " + ", ".join(missing)
            )

    def start(self):
        self._validate()

        sumo_binary = os.getenv("SUMO_BINARY", "sumo")
        label = f"transitops-{id(self)}"

        traci.start(
            [
                sumo_binary,
                "-c",
                str(self.config_path),
                "--start",
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
            self.start()

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
