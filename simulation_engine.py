from models import BusState, SimulationMetrics


class TransitSimulation:
    def __init__(
        self,
        buses,
        trains,
        controller,
        duration=60,
        base_bus_travel_time=8,
        dispatch_interval=12,
        traffic_profile=None,
        initial_queue=15,
    ):
        self.buses = buses
        self.trains = sorted(trains, key=lambda t: t.actual_arrival)
        self.controller = controller
        self.duration = duration
        self.base_bus_travel_time = base_bus_travel_time
        self.dispatch_interval = dispatch_interval
        self.station_queue = initial_queue
        self.waiting_passengers = [0] * initial_queue
        self.metrics = SimulationMetrics(
            total_passengers_arrived=initial_queue,
            max_queue=initial_queue,
        )
        self.events = []
        self.time = 0

        self.traffic_profile = traffic_profile or {
            0: 1.0,
            15: 1.25,
            30: 1.55,
            45: 1.15,
        }

    def log(self, message):
        self.events.append((self.time, message))

    def traffic_multiplier(self):
        relevant_times = [t for t in self.traffic_profile if t <= self.time]
        key = max(relevant_times) if relevant_times else min(self.traffic_profile)
        return self.traffic_profile[key]

    def current_bus_travel_time(self):
        return max(
            1,
            round(self.base_bus_travel_time * self.traffic_multiplier())
        )

    def process_train_arrivals(self):
        for train in self.trains:
            if train.actual_arrival == self.time:
                count = train.passengers_for_bus
                self.station_queue += count
                self.waiting_passengers.extend([0] * count)
                self.metrics.total_passengers_arrived += count
                self.metrics.max_queue = max(
                    self.metrics.max_queue, self.station_queue
                )
                self.log(
                    f"{train.train_id} ARRIVED "
                    f"(delay={train.delay_minutes}m), +{count} passengers"
                )

    def dispatch_buses(self):
        if self.time % self.dispatch_interval == 0:
            for bus in self.buses:
                if bus.state == BusState.YARD:
                    bus.state = BusState.EN_ROUTE
                    bus.eta_minutes = self.current_bus_travel_time()
                    self.log(
                        f"DISPATCH {bus.bus_id}, ETA={bus.eta_minutes}m "
                        f"(traffic x{self.traffic_multiplier():.2f})"
                    )
                    break

        if self.controller.maybe_prestage_bus(self):
            for bus in self.buses:
                if bus.state == BusState.STAGED:
                    bus.state = BusState.EN_ROUTE
                    bus.eta_minutes = max(
                        2, self.current_bus_travel_time() // 2
                    )
                    self.log(
                        f"{bus.bus_id} leaves staging area, ETA={bus.eta_minutes}m"
                    )
                    break

    def move_buses(self):
        for bus in self.buses:
            if bus.state == BusState.EN_ROUTE:
                bus.eta_minutes -= 1
                if bus.eta_minutes <= 0:
                    bus.state = BusState.AT_STATION
                    self.log(f"{bus.bus_id} ARRIVED at station")

    def process_station_buses(self):
        for bus in self.buses:
            if bus.state != BusState.AT_STATION:
                continue

            if bus.hold_minutes > 0:
                bus.hold_minutes -= 1
                self.log(
                    f"HOLDING {bus.bus_id}, {bus.hold_minutes}m remaining"
                )
                continue

            hold = self.controller.should_hold_bus(self, bus)
            if hold > 0:
                bus.hold_minutes = hold
                self.metrics.holds_issued += 1
                self.log(f"HOLD {bus.bus_id} for {hold}m awaiting train")
                continue

            boarded = min(bus.capacity, self.station_queue)

            if boarded:
                waits = self.waiting_passengers[:boarded]
                self.metrics.cumulative_wait_minutes += sum(waits)
                self.waiting_passengers = self.waiting_passengers[boarded:]
                self.station_queue -= boarded
                self.metrics.total_passengers_boarded += boarded
                self.log(
                    f"{bus.bus_id} BOARDS {boarded}; queue={self.station_queue}"
                )
            else:
                self.log(f"{bus.bus_id} departs empty")

            bus.passengers = 0
            bus.state = BusState.YARD

    def age_waiting_passengers(self):
        self.waiting_passengers = [w + 1 for w in self.waiting_passengers]

    def run(self):
        self.log("SIMULATION START")

        for minute in range(self.duration + 1):
            self.time = minute
            self.process_train_arrivals()
            self.dispatch_buses()
            self.move_buses()
            self.process_station_buses()
            self.metrics.max_queue = max(
                self.metrics.max_queue, self.station_queue
            )
            self.age_waiting_passengers()

        self.metrics.missed_connections = self.station_queue
        self.log("SIMULATION END")
        return self.metrics
