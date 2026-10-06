from dataclasses import dataclass
from enum import Enum, auto


class BusState(Enum):
    YARD = auto()
    STAGED = auto()
    EN_ROUTE = auto()
    AT_STATION = auto()


@dataclass
class Bus:
    bus_id: str
    capacity: int = 50
    state: BusState = BusState.YARD
    eta_minutes: int = 0
    hold_minutes: int = 0
    passengers: int = 0


@dataclass
class Train:
    train_id: str
    scheduled_arrival: int
    delay_minutes: int
    passengers_for_bus: int

    @property
    def actual_arrival(self) -> int:
        return self.scheduled_arrival + self.delay_minutes


@dataclass
class SimulationMetrics:
    total_passengers_arrived: int = 0
    total_passengers_boarded: int = 0
    cumulative_wait_minutes: int = 0
    max_queue: int = 0
    missed_connections: int = 0
    buses_staged: int = 0
    holds_issued: int = 0

    @property
    def average_wait_minutes(self) -> float:
        if self.total_passengers_boarded == 0:
            return 0.0
        return self.cumulative_wait_minutes / self.total_passengers_boarded
