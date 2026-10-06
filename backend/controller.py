from models import BusState


class TransitController:
    def __init__(
        self,
        enabled=True,
        staging_threshold=55,
        hold_threshold=6,
        max_hold=5,
    ):
        self.enabled = enabled
        self.staging_threshold = staging_threshold
        self.hold_threshold = hold_threshold
        self.max_hold = max_hold

    def estimate_near_term_demand(self, sim, lookahead=12):
        demand = sim.station_queue

        for train in sim.trains:
            minutes_until_arrival = train.actual_arrival - sim.time
            if 0 < minutes_until_arrival <= lookahead:
                demand += train.passengers_for_bus

        return demand

    def maybe_prestage_bus(self, sim):
        if not self.enabled:
            return False

        expected_demand = self.estimate_near_term_demand(sim)

        staged_capacity = sum(
            bus.capacity
            for bus in sim.buses
            if bus.state in (BusState.STAGED, BusState.EN_ROUTE, BusState.AT_STATION)
        )

        if expected_demand <= max(self.staging_threshold, staged_capacity):
            return False

        for bus in sim.buses:
            if bus.state == BusState.YARD:
                bus.state = BusState.STAGED
                sim.metrics.buses_staged += 1
                sim.log(f"PRE-STAGE {bus.bus_id}: expected demand={expected_demand}")
                return True

        return False

    def should_hold_bus(self, sim, bus):
        if not self.enabled or bus.state != BusState.AT_STATION:
            return 0

        next_train = None
        for train in sim.trains:
            if train.actual_arrival >= sim.time:
                next_train = train
                break

        if next_train is None:
            return 0

        time_to_train = next_train.actual_arrival - sim.time

        if 0 < time_to_train <= self.hold_threshold and next_train.passengers_for_bus > 0:
            return min(time_to_train, self.max_hold)

        return 0
