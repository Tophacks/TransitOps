import math
import random

from models import BusState, Intersection, Passenger, SimulationMetrics, Stop


ROUTE = ["CENTRAL", "KING", "MARKET", "UNIVERSITY"]

STOP_LAYOUT = {
    "CENTRAL": ("Central Terminal", 24.0, 45.0),
    "KING": ("King", 46.0, 67.0),
    "MARKET": ("Market", 67.0, 58.0),
    "UNIVERSITY": ("University", 82.0, 74.0),
}

INTERSECTION_LAYOUT = {
    "I1": ("Central / King", 36.0, 56.0, 0),
    "I2": ("King / Market", 57.0, 63.0, 1),
    "I3": ("Market / University", 75.0, 66.0, 2),
}


class TransitSimulation:
    def __init__(
        self,
        buses,
        trains,
        intelligent=True,
        duration=60,
        traffic_multiplier=1.0,
        initial_queue=15,
        seed=17,
    ):
        self.buses = buses
        self.trains = sorted(trains, key=lambda t: t.actual_arrival)
        self.intelligent = intelligent
        self.duration = duration
        self.traffic_multiplier = max(0.5, min(float(traffic_multiplier), 3.0))
        self.metrics = SimulationMetrics()
        self.events = []
        self.timeline = []
        self.time = 0
        self._rng = random.Random(seed)
        self._next_passenger = 1

        self.stops = {
            stop_id: Stop(stop_id, name, x, y)
            for stop_id, (name, x, y) in STOP_LAYOUT.items()
        }
        self.intersections = {
            iid: Intersection(iid, name, x, y, offset=offset)
            for iid, (name, x, y, offset) in INTERSECTION_LAYOUT.items()
        }

        self._spawn_passengers("CENTRAL", initial_queue, initial=True)

    def log(self, message, kind="info"):
        self.events.append({"minute": self.time, "message": message, "kind": kind})

    def _destination_for(self, origin):
        origin_index = ROUTE.index(origin)
        choices = [s for s in ROUTE if s != origin]
        forward = [s for s in ROUTE[origin_index + 1:]]
        if forward and self._rng.random() < 0.8:
            return self._rng.choice(forward)
        return self._rng.choice(choices)

    def _spawn_passengers(self, stop_id, count, initial=False):
        stop = self.stops[stop_id]
        for _ in range(max(0, count)):
            passenger = Passenger(
                passenger_id=f"P{self._next_passenger:04d}",
                origin=stop_id,
                destination=self._destination_for(stop_id),
                arrival_time=0 if initial else self.time,
            )
            self._next_passenger += 1
            stop.queue.append(passenger)
        self.metrics.total_passengers_arrived += max(0, count)

    def _spawn_background_demand(self):
        rates = {
            "CENTRAL": 0.7,
            "KING": 1.0,
            "MARKET": 0.8,
            "UNIVERSITY": 1.2,
        }
        for stop_id, rate in rates.items():
            expected = rate * (0.8 + 0.35 * self.traffic_multiplier)
            base = int(expected)
            extra = 1 if self._rng.random() < (expected - base) else 0
            count = base + extra
            if count:
                self._spawn_passengers(stop_id, count)

    def _process_train_arrivals(self):
        for train in self.trains:
            if train.actual_arrival == self.time:
                self._spawn_passengers("CENTRAL", train.passengers_for_bus)
                self.log(
                    f"{train.train_id} arrived +{train.passengers_for_bus} transfer passengers",
                    "train",
                )

    def _near_term_central_demand(self, lookahead=8):
        demand = len(self.stops["CENTRAL"].queue)
        for train in self.trains:
            until = train.actual_arrival - self.time
            if 0 < until <= lookahead:
                demand += train.passengers_for_bus
        return demand

    def _segment_base_minutes(self, from_index, to_index):
        if from_index == -1:
            return 5
        segment_times = {
            (0, 1): 5,
            (1, 2): 4,
            (2, 3): 5,
            (3, 0): 7,
        }
        return segment_times.get((from_index, to_index), 5)

    def _intersection_for_segment(self, from_index, to_index):
        mapping = {
            (-1, 0): "I1",
            (0, 1): "I1",
            (1, 2): "I2",
            (2, 3): "I3",
            (3, 0): "I2",
        }
        return mapping.get((from_index, to_index))

    def _travel_minutes(self, bus):
        base = self._segment_base_minutes(bus.current_stop_index, bus.target_stop_index)
        congestion = max(1, round(base * self.traffic_multiplier))
        intersection_id = self._intersection_for_segment(
            bus.current_stop_index, bus.target_stop_index
        )
        signal_delay = 0

        if intersection_id:
            intersection = self.intersections[intersection_id]
            if intersection.phase(self.time) == "RED":
                signal_delay = 1

                if (
                    self.intelligent
                    and bus.schedule_deviation >= 2
                    and bus.occupancy >= max(10, bus.capacity // 3)
                ):
                    intersection.priority_until = self.time + 1
                    signal_delay = 0
                    self.metrics.signal_priority_requests += 1
                    self.log(
                        f"{bus.bus_id} signal priority approved at {intersection_id}",
                        "priority",
                    )

        return max(2, congestion + signal_delay)

    def _dispatch_bus(self, bus, staged=False):
        bus.state = BusState.EN_ROUTE
        bus.current_stop_index = -1
        bus.target_stop_index = 0
        bus.segment_elapsed = 0
        bus.segment_minutes = max(2, round(5 * self.traffic_multiplier))
        bus.segment_progress = 0.0
        bus.schedule_deviation = 1 if staged else 0

        if staged:
            self.metrics.buses_staged += 1
            self.log(f"{bus.bus_id} pre-staged toward Central Terminal", "decision")
        else:
            self.log(f"{bus.bus_id} dispatched from yard", "dispatch")

    def _dispatch_logic(self):
        active = [b for b in self.buses if b.state != BusState.YARD]
        yard = [b for b in self.buses if b.state == BusState.YARD]

        if self.time % 12 == 0 and yard:
            self._dispatch_bus(yard[0])

        if self.intelligent and yard:
            demand = self._near_term_central_demand()
            arriving_capacity = sum(
                max(0, bus.capacity - bus.occupancy)
                for bus in active
                if bus.target_stop_index == 0
            )
            if demand > max(55, arriving_capacity + 20):
                self._dispatch_bus(yard[0], staged=True)

    def _alight_and_board(self, bus, stop_id):
        staying = []
        alighted = 0
        for passenger in bus.onboard:
            if passenger.destination == stop_id:
                passenger.completed_time = self.time
                self.metrics.completed_trips += 1
                alighted += 1
            else:
                staying.append(passenger)
        bus.onboard = staying

        stop = self.stops[stop_id]
        seats = max(0, bus.capacity - bus.occupancy)
        boarding = stop.queue[:seats]
        stop.queue = stop.queue[seats:]

        for passenger in boarding:
            passenger.boarded_time = self.time
            self.metrics.total_passengers_boarded += 1
            self.metrics.cumulative_wait_minutes += max(
                0, self.time - passenger.arrival_time
            )

        bus.onboard.extend(boarding)

        if alighted or boarding:
            self.log(
                f"{bus.bus_id} at {stop.name}: {alighted} off / {len(boarding)} on",
                "boarding",
            )

    def _advance_buses(self):
        for bus in self.buses:
            if bus.state == BusState.YARD:
                continue

            if bus.state == BusState.DWELLING:
                bus.dwell_minutes -= 1
                if bus.dwell_minutes <= 0:
                    bus.current_stop_index = bus.target_stop_index
                    bus.target_stop_index = (bus.current_stop_index + 1) % len(ROUTE)
                    bus.state = BusState.EN_ROUTE
                    bus.segment_elapsed = 0
                    bus.segment_progress = 0.0
                    bus.segment_minutes = self._travel_minutes(bus)
                continue

            bus.segment_elapsed += 1
            bus.segment_progress = min(
                1.0, bus.segment_elapsed / max(1, bus.segment_minutes)
            )

            if bus.segment_progress >= 1.0:
                stop_id = ROUTE[bus.target_stop_index]
                self._alight_and_board(bus, stop_id)
                bus.state = BusState.DWELLING
                bus.dwell_minutes = 1
                bus.segment_progress = 1.0
                bus.schedule_deviation = max(
                    0, bus.segment_minutes - self._segment_base_minutes(
                        bus.current_stop_index, bus.target_stop_index
                    )
                )

    def _bus_xy(self, bus):
        yard = (8.0, 83.0)
        if bus.state == BusState.YARD:
            return yard

        target_stop = self.stops[ROUTE[bus.target_stop_index]]
        target = (target_stop.x, target_stop.y)

        if bus.current_stop_index == -1:
            start = yard
        else:
            current_stop = self.stops[ROUTE[bus.current_stop_index]]
            start = (current_stop.x, current_stop.y)

        p = bus.segment_progress if bus.state == BusState.EN_ROUTE else 1.0
        x = start[0] + (target[0] - start[0]) * p
        y = start[1] + (target[1] - start[1]) * p
        return round(x, 2), round(y, 2)

    def _capture_snapshot(self):
        total_waiting = sum(len(stop.queue) for stop in self.stops.values())
        self.metrics.max_queue = max(
            self.metrics.max_queue,
            max((len(stop.queue) for stop in self.stops.values()), default=0),
        )

        snapshot = {
            "minute": self.time,
            "waiting": total_waiting,
            "stops": [
                {
                    "id": stop.stop_id,
                    "name": stop.name,
                    "x": stop.x,
                    "y": stop.y,
                    "queue": len(stop.queue),
                    "sprites": min(18, math.ceil(len(stop.queue) / 5)),
                }
                for stop in self.stops.values()
            ],
            "intersections": [
                {
                    "id": intersection.intersection_id,
                    "name": intersection.name,
                    "x": intersection.x,
                    "y": intersection.y,
                    "phase": intersection.phase(self.time),
                }
                for intersection in self.intersections.values()
            ],
            "buses": [],
        }

        for bus in self.buses:
            x, y = self._bus_xy(bus)
            target_name = (
                self.stops[ROUTE[bus.target_stop_index]].name
                if bus.state != BusState.YARD
                else "Central Terminal"
            )
            snapshot["buses"].append(
                {
                    "id": bus.bus_id,
                    "x": x,
                    "y": y,
                    "state": bus.state.name,
                    "occupancy": bus.occupancy,
                    "capacity": bus.capacity,
                    "target": target_name,
                    "progress": round(bus.segment_progress, 3),
                    "schedule_deviation": bus.schedule_deviation,
                }
            )

        self.timeline.append(snapshot)

    def run(self):
        self.log("Simulation started", "system")

        for minute in range(self.duration + 1):
            self.time = minute
            if minute > 0:
                self._spawn_background_demand()
            self._process_train_arrivals()
            self._dispatch_logic()
            self._advance_buses()
            self._capture_snapshot()

        self.metrics.passengers_left_waiting = sum(
            len(stop.queue) for stop in self.stops.values()
        )
        self.log("Simulation complete", "system")
        return self.metrics
