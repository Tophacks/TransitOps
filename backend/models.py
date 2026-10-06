from dataclasses import dataclass, field
from enum import Enum, auto


class BusState(Enum):
    YARD = auto()
    EN_ROUTE = auto()
    DWELLING = auto()


@dataclass
class Passenger:
    passenger_id: str
    origin: str
    destination: str
    arrival_time: int
    boarded_time: int | None = None
    completed_time: int | None = None


@dataclass
class Stop:
    stop_id: str
    name: str
    x: float
    y: float
    queue: list[Passenger] = field(default_factory=list)


@dataclass
class Intersection:
    intersection_id: str
    name: str
    x: float
    y: float
    cycle_minutes: int = 4
    offset: int = 0
    priority_until: int = -1

    def phase(self, minute: int) -> str:
        if minute <= self.priority_until:
            return "GREEN"
        return "GREEN" if ((minute + self.offset) % self.cycle_minutes) < 2 else "RED"


@dataclass
class Bus:
    bus_id: str
    capacity: int = 50
    state: BusState = BusState.YARD
    current_stop_index: int = -1
    target_stop_index: int = 0
    segment_progress: float = 0.0
    segment_minutes: int = 1
    segment_elapsed: int = 0
    dwell_minutes: int = 0
    onboard: list[Passenger] = field(default_factory=list)
    schedule_deviation: int = 0

    @property
    def occupancy(self) -> int:
        return len(self.onboard)


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
    completed_trips: int = 0
    cumulative_wait_minutes: int = 0
    max_queue: int = 0
    passengers_left_waiting: int = 0
    buses_staged: int = 0
    signal_priority_requests: int = 0

    @property
    def average_wait_minutes(self) -> float:
        if self.total_passengers_boarded == 0:
            return 0.0
        return self.cumulative_wait_minutes / self.total_passengers_boarded
