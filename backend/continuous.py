"""Stateful run owned by a dedicated browser worker."""
import json

from garage_ops import Garages
from ion import HUBS, IonPlan
from models import Bus
from scenario_result import case_result, simulation_result
from simulation_engine import TransitSimulation

REPLAY_MINUTES = 60
BUFFER_MINUTES = 5


class ContinuousRun:
    def __init__(self, settings):
        self.scenario = settings["scenario"]
        self.network = settings["network"]
        self.demand = settings["rail"]["transfer_demand"]
        self.cases = [
            TransitSimulation(
                [Bus(f"B{101 + i}") for i in range(self.scenario["fleet_size"])],
                IonPlan(continuous=True, reassign=adaptive, **settings["rail"]),
                intelligent=adaptive,
                **settings["bus"],
            )
            for adaptive in (False, True)
        ]
        self.minute = -1
        self.garages = [None, None]
        for sim in self.cases:
            sim.log("Continuous Waterloo corridor simulation started", "system")

    def extend(self):
        end = self.minute + BUFFER_MINUTES + (self.minute < 0)
        start = max(0, end - REPLAY_MINUTES)
        results = []
        for i, sim in enumerate(self.cases):
            plan = sim.ion
            rail_end = (end + 2) * 60.0
            if rail_end > 3600:
                plan.extend_rail(rail_end)
            plan.duration_s = (end + 1) * 60.0

            # Continue hourly transfer demand at the modeled service frequency.
            # Allocate from scheduled trips for predictions, but board from
            # actual station times only. The two initial scenario waves happen once.
            arrivals = []
            for trip in plan.trips:
                for name, stop in HUBS.items():
                    actual = trip.station_times.get(name)
                    estimated = plan.arrival_s(trip, name)
                    if estimated >= 3600 and stop not in trip.transfers:
                        per_call = self.demand * plan.headway_s / (3600 * 2 * len(HUBS))
                        slot = int(trip.scheduled_s // plan.headway_s)
                        trip.transfers[stop] = round((slot + 1) * per_call) - round(slot * per_call)
                    if actual is not None and actual >= (self.minute - 1) * 60:
                        arrivals.append((actual, trip, stop, trip.transfers.get(stop, 0)))
            sim.hub_arrivals = sorted(arrivals, key=lambda a: (a[0], a[1].train_id))

            for minute in range(self.minute + 1, end + 1):
                sim.step_minute(minute)

            if self.garages[i] is None:
                garages = Garages(sim)
                garages.decisions = []
                garages.run()
                if end >= 60:
                    self.garages[i] = garages
            else:
                garages = self.garages[i]
            self.prune(sim, start)
            results.append(case_result(sim, sim.metrics_at(end * 60), self.scenario["uw_cars"],
                                       garages, [d for d in garages.decisions if d["t"] >= start * 60]))
        self.minute = end
        return simulation_result(self.scenario, self.network, *results)

    @staticmethod
    def prune(sim, start):
        sim.timeline[:] = [frame for frame in sim.timeline if frame["minute"] >= start]
        sim.events[:] = [event for event in sim.events if event["minute"] >= start]
        cut = start * 60.0
        keep_decisions = [d for d in sim.decisions if d["t"] >= cut]
        sim.decision_offset += len(sim.decisions) - len(keep_decisions)
        sim.decisions[:] = keep_decisions
        uw = sim.uw
        uw.agents[:] = [a for a in uw.agents if a.state != "gone" or
                        max(a.t_enter, a.exit_s or 0, a.trace[-1][0] if a.trace else 0) >= cut]
        uw.road.vehicles[:] = [v for v in uw.road.vehicles if not v.done or v.trace[-1][0] >= cut]
        uw.errors.used[:] = [e for e in uw.errors.used if e["t"] >= cut]

        # Keep a service-cycle margin behind replay for trip lookup and
        # forecast bookkeeping. Active authorities and incomplete trips stay.
        old = cut - 3600.0
        ops = sim.ion.ops
        for train in ops.trains:
            train.segments[:] = [seg for seg in train.segments if seg.t1 >= old]
        sim.ion.trips[:] = [trip for trip in sim.ion.trips
                            if trip.arrived_s is None or trip.arrived_s >= old]
        ops.departed[:] = [entry for entry in ops.departed if entry[1] >= old]
        ops.decisions[:] = [d for d in ops.decisions if d.t >= old]
        ops.events[:] = [event for event in ops.events if event["t"] >= old]

    def next_json(self):
        return json.dumps(self.extend(), allow_nan=False, separators=(",", ":"))
