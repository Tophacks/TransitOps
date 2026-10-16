"""Hardware-independent miniature fleet simulation. No real motor or charging control."""
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional

class State(str, Enum):
    IN_SERVICE = "IN_SERVICE"
    HOLDING = "HOLDING"
    RETURNING = "RETURNING"
    DOCKING = "DOCKING"
    PARKED = "PARKED"
    READY = "READY"
    FAULT = "FAULT"

@dataclass
class Bus:
    id: str
    state: State = State.IN_SERVICE
    station: Optional[str] = None
    distance: float = 0.0
    battery: float = 100.0
    bay: Optional[str] = None
    trips: int = 0

@dataclass
class Bay:
    id: str
    automated: bool
    occupied_by: Optional[str] = None
    reserved_for: Optional[str] = None

class Dispatcher:
    STATIONS = {"Station A": 0.20, "Station B": 0.70}
    def __init__(self):
        self.tick_no = 0
        self.events = []
        self.buses = {"BUS-01": Bus("BUS-01")}
        self.bays = {
            "DEPOT-1": Bay("DEPOT-1", automated=True),
            "P1": Bay("P1", automated=False),
            "P2": Bay("P2", automated=False),
        }
        self.interlock_fault = False
        self.mode = "NORMAL"

    def _event(self, kind, bus=None, **fields):
        record = {"tick": self.tick_no, "type": kind, "bus": bus, **fields}
        self.events.append(record)
        self.events = self.events[-100:]
        return record

    def _bus(self, identifier):
        if identifier not in self.buses: raise ValueError("Unknown bus")
        return self.buses[identifier]

    def add_bus(self, identifier):
        if not identifier or identifier in self.buses: raise ValueError("Bus ID must be unique and nonempty")
        self.buses[identifier] = Bus(identifier, state=State.PARKED)
        self._event("BUS_REGISTERED", identifier)

    def request_depot(self, identifier):
        bus = self._bus(identifier)
        if bus.state not in (State.IN_SERVICE, State.HOLDING): raise ValueError("Bus is not operating")
        bay = self.bays["DEPOT-1"]
        if bay.occupied_by or bay.reserved_for or self.interlock_fault: raise ValueError("Automated bay unavailable")
        bay.reserved_for = identifier
        bus.state = State.RETURNING
        bus.station = None
        self._event("DEPOT_RESERVED", identifier, bay=bay.id)

    def confirm_docked(self, identifier, sensor_id="DEPOT_ENTRY"):
        """SIMULATION ONLY: caller injects a mock docking confirmation."""
        bus = self._bus(identifier)
        bay = self.bays["DEPOT-1"]
        if bus.state != State.RETURNING or bay.reserved_for != identifier: raise ValueError("No matching depot reservation")
        if bay.occupied_by or self.interlock_fault: raise ValueError("Docking interlock active")
        bus.state, bus.bay = State.PARKED, bay.id
        bay.occupied_by, bay.reserved_for = identifier, None
        self._event("DOCK_CONFIRMED", identifier, bay=bay.id, sensor=sensor_id)

    def dispatch(self, identifier):
        bus = self._bus(identifier)
        if bus.state not in (State.PARKED, State.READY): raise ValueError("Vehicle is not parked or ready")
        if self.interlock_fault: raise ValueError("Depot interlock active")
        if bus.bay:
            bay = self.bays[bus.bay]
            if bay.occupied_by != identifier: raise ValueError("Bay occupancy mismatch")
            bay.occupied_by = None
        bus.bay, bus.state, bus.station = None, State.IN_SERVICE, None
        self._event("VEHICLE_RELEASED", identifier)

    def emergency_stop(self, reason="operator"):
        self.interlock_fault = True
        for bus in self.buses.values():
            if bus.state in (State.IN_SERVICE, State.HOLDING, State.RETURNING, State.DOCKING):
                bus.state = State.FAULT
        for bay in self.bays.values(): bay.reserved_for = None
        self._event("EMERGENCY_STOP", reason=reason)

    def clear_interlock(self):
        self.interlock_fault = False
        self._event("INTERLOCK_CLEARED")

    def reset_bus(self, identifier):
        bus = self._bus(identifier)
        if bus.state != State.FAULT or self.interlock_fault: raise ValueError("Reset requires fault state and cleared interlock")
        bus.state = State.HOLDING
        self._event("BUS_RESET_TO_HOLD", identifier)

    def tick(self, steps=1):
        if not isinstance(steps, int) or not 1 <= steps <= 100: raise ValueError("steps must be integer from 1 to 100")
        for _ in range(steps):
            self.tick_no += 1
            for bus in self.buses.values():
                if bus.state != State.IN_SERVICE or self.interlock_fault: continue
                previous = bus.distance
                bus.distance = (bus.distance + 0.025) % 1.0
                bus.battery = max(0.0, round(bus.battery - 0.05, 3))
                for name, position in self.STATIONS.items():
                    if (previous <= position < bus.distance) or (bus.distance < previous and (position >= previous or position < bus.distance)):
                        bus.station = name
                        self._event("STATION_ARRIVAL", bus.id, station=name)
                if bus.distance < previous: bus.trips += 1
                if bus.battery == 0:
                    bus.state = State.HOLDING
                    self._event("BATTERY_EMPTY_SIM", bus.id)
        return self.snapshot()

    def snapshot(self):
        return {
            "tick": self.tick_no, "mode": self.mode, "interlock_fault": self.interlock_fault,
            "stations": self.STATIONS,
            "buses": {k: {**asdict(v), "state": v.state.value} for k, v in self.buses.items()},
            "bays": {k: asdict(v) for k, v in self.bays.items()},
            "events": self.events[-20:],
        }
