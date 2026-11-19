import math
import random

import corridor
import ion
from models import BusState, Intersection, Passenger, SimulationMetrics, Stop


ROUTE = [
    "UW",
    "WATERLOO_SQUARE",
    "GRAND_RIVER_HOSPITAL",
    "CENTRAL",
]

STOP_LAYOUT = {
    "UW": ("University of Waterloo", 20.0, 18.0),
    "WATERLOO_SQUARE": ("Waterloo Public Square", 40.0, 40.0),
    "GRAND_RIVER_HOSPITAL": ("Grand River Hospital", 60.0, 61.0),
    "CENTRAL": ("Central Station", 80.0, 82.0),
}

INTERSECTION_LAYOUT = {
    "I1": ("University corridor", 31.0, 30.0, 0),
    "I2": ("Uptown Waterloo", 50.0, 51.0, 1),
    "I3": ("King / Victoria", 70.0, 72.0, 2),
}

# Average running speed of each leg, stops and signals included, taken from
# GRT's own timetable (GTFS) for the routes on those streets, measured over the
# street path. Each leg's minutes follow from its length (see segment_minutes).
SEGMENT_SPEED_KMH = {
    # University Ave and Phillip St: 202 iXpress, scheduled 21.6-22.2 km/h.
    ("UW", "WATERLOO_SQUARE"): 22.0,
    # King St: route 7 is scheduled 4 min between these exact stops (18.7 km/h).
    ("WATERLOO_SQUARE", "GRAND_RIVER_HOSPITAL"): 18.7,
    ("GRAND_RIVER_HOSPITAL", "CENTRAL"): 18.7,
    # Back up King St through Uptown: route 7 northbound, scheduled 18.5 km/h.
    ("CENTRAL", "UW"): 18.5,
    ("YARD", "CENTRAL"): 18.7,
}


def segment_minutes(origin, target):
    """Typical minutes for a leg: street length at its scheduled average speed."""
    length = corridor.segment(origin, target)["length_m"]
    return max(1, round(length / (SEGMENT_SPEED_KMH[(origin, target)] / 3.6) / 60.0))


SEGMENT_BASE_MINUTES = [segment_minutes(a, b) for a, b in zip(
    ["UW", "WATERLOO_SQUARE", "GRAND_RIVER_HOSPITAL", "CENTRAL"],
    ["WATERLOO_SQUARE", "GRAND_RIVER_HOSPITAL", "CENTRAL", "UW"],
)]
YARD_BASE_MINUTES = segment_minutes("YARD", "CENTRAL")
SEGMENT_INTERSECTIONS = ["I1", "I2", "I3", "I2"]

# Real GRT curb stops and signalised intersections on the street path
# (see tools/waterloo_geometry). Buses travel between them along the streets,
# never in a straight line across blocks.
GEO_LAYOUT = {
    stop_id: (corridor.STOPS[stop_id]["lon"], corridor.STOPS[stop_id]["lat"])
    for stop_id in ROUTE
}

INTERSECTION_GEO = {
    iid: (corridor.SIGNALS[iid]["lon"], corridor.SIGNALS[iid]["lat"])
    for iid in INTERSECTION_LAYOUT
}

YARD_GEO = (corridor.STOPS["YARD"]["lon"], corridor.STOPS["YARD"]["lat"])


# Rail-to-bus connections are timed in seconds, not whole minutes. A transfer
# rider can board from the moment their train stops plus the walk to the bus
# stop; a bus boards everyone already eligible when its doors open, and keeps
# boarding riders who become eligible while it dwells, until its doors close
# DOOR_CLOSE_S before it pulls out. Both values are modelled.
TRANSFER_WALK_S = {"CENTRAL": 60.0, "UW": 60.0}
DOOR_CLOSE_S = 5.0


class TransitSimulation:
    # Adaptive pre-staging rules (only when intelligent). Releasing reserve buses
    # early and together left Central unserved for most of the hour once loops
    # took ~40 min, so a pre-staged bus must keep its distance, takes the next
    # scheduled slot instead of adding one, and never empties the yard.
    # With the 10-minute schedule a 7-minute gap means a bus can be brought
    # forward by at most 3 minutes. Over 20 seeds of every preset this beats the
    # fixed schedule on average in all five; a 5-minute gap still lost rush hour
    # and the emergency preset.
    STAGING_MIN_GAP = 7          # minutes since the previous yard release
    STAGING_PULLS_FORWARD = True  # the next 10-minute release is skipped
    STAGING_RESERVE = 1           # buses that must stay in the yard
    def __init__(
        self,
        buses,
        ion_plan,
        intelligent=True,
        duration=90,
        traffic_multiplier=1.0,
        initial_queue=20,
        seed=17,
        demand_multiplier=1.0,
        breakdown_minute=None,
        surge_minute=None,
        surge_passengers=0,
        emergency_dispatch_minute=None,
    ):
        self.buses = buses
        # ION trains on the real track: their arrivals at Central and UW bring
        # riders who transfer to the corridor bus (see ion.py).
        self.ion = ion_plan
        self.hub_arrivals = ion_plan.hub_arrivals()
        self.intelligent = intelligent
        self.duration = duration
        self.traffic_multiplier = max(0.5, min(float(traffic_multiplier), 3.0))
        self.demand_multiplier = max(0.5, min(float(demand_multiplier), 3.0))
        self.breakdown_minute = breakdown_minute
        self.surge_minute = surge_minute
        self.surge_passengers = max(0, int(surge_passengers))
        self.emergency_dispatch_minute = emergency_dispatch_minute
        self.breakdown_bus_id = None
        self.breakdown_until = -1
        self.breakdown_minutes = 6
        self.metrics = SimulationMetrics()
        self.events = []
        self.timeline = []
        self.time = 0
        self.last_release = None      # minute a bus last left the yard
        self.pulled_forward_slot = None  # scheduled release already taken by a staged bus
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
        self._spawn_passengers("UW", 12, initial=True)
        self._spawn_passengers("WATERLOO_SQUARE", 8, initial=True)

    def log(self, message, kind="info", *, bus_id=None, reason=None, train_id=None):
        event = {"minute": self.time, "message": message, "kind": kind}
        if bus_id is not None:
            event["bus_id"] = bus_id
        if train_id is not None:
            event["train_id"] = train_id
        if reason is not None:
            event["reason"] = reason
        self.events.append(event)

    def _destination_for(self, origin):
        origin_index = ROUTE.index(origin)
        forward = ROUTE[origin_index + 1:]
        backward = ROUTE[:origin_index]

        if forward and self._rng.random() < 0.72:
            return self._rng.choice(forward)
        if backward:
            return self._rng.choice(backward)
        return ROUTE[-1]

    def _spawn_passengers(self, stop_id, count, initial=False, source="walk-up", eligible_s=None):
        stop = self.stops[stop_id]
        for _ in range(max(0, count)):
            passenger = Passenger(
                passenger_id=f"P{self._next_passenger:04d}",
                origin=stop_id,
                destination=self._destination_for(stop_id),
                arrival_time=0 if initial else self.time,
                source=source,
                eligible_s=eligible_s,
            )
            self._next_passenger += 1
            stop.queue.append(passenger)
        self.metrics.total_passengers_arrived += max(0, count)

    def _spawn_background_demand(self):
        rates = {
            "UW": 1.35,
            "WATERLOO_SQUARE": 1.0,
            "GRAND_RIVER_HOSPITAL": 0.9,
            "CENTRAL": 1.15,
        }
        for stop_id, rate in rates.items():
            expected = rate * self.demand_multiplier * (0.8 + 0.3 * self.traffic_multiplier)
            base = int(expected)
            extra = 1 if self._rng.random() < (expected - base) else 0
            count = base + extra
            if count:
                self._spawn_passengers(stop_id, count)

    def _process_train_arrivals(self):
        for trip in self.ion.trips:
            # Announce a held train when it should have left its terminal.
            if trip.hold_reason and math.floor(max(0.0, trip.scheduled_s) / 60) == self.time:
                self.log(
                    f"{trip.train_id} {trip.hold_reason} (+{trip.delay_s / 60:.0f} min)",
                    "disruption",
                    train_id=trip.train_id,
                    reason="Scenario train delay: this trip carries a transfer wave." if "Fairway" in trip.hold_reason
                    else "ION trains cannot overtake, so the train behind keeps a 3-minute gap.",
                )
        for at, trip, stop_id, riders in self.hub_arrivals:
            if math.floor(at / 60) == self.time:
                self.metrics.ion_max_delay_minutes = max(self.metrics.ion_max_delay_minutes, trip.delay_s / 60)
                late = f", {trip.delay_s / 60:.0f} min late" if trip.delay_s else ""
                ready = at + TRANSFER_WALK_S[stop_id]
                self.log(
                    f"{trip.train_id} at {self.stops[stop_id].name}{late}: +{riders} transferring to the bus "
                    f"(can board from {int(ready // 60):02d}:{int(ready % 60):02d})",
                    "train",
                    train_id=trip.train_id,
                )
            # Riders join the bus stop queue at the exact second they can board:
            # the train's stop plus the walk (never rounded down to the minute).
            ready = at + TRANSFER_WALK_S[stop_id]
            if math.floor(ready / 60) == self.time:
                self._spawn_passengers(stop_id, riders, source="ion", eligible_s=ready)
                self.metrics.ion_transfers += riders

    def _scenario_events(self):
        if self.surge_minute is not None and self.time == self.surge_minute:
            self._spawn_passengers("UW", self.surge_passengers)
            self.log(f"Event surge: +{self.surge_passengers} passengers at UW", "surge")
        if self.breakdown_minute is not None and self.time == self.breakdown_minute:
            active = next((bus for bus in self.buses if bus.state != BusState.YARD), None)
            if active:
                self.breakdown_bus_id = active.bus_id
                self.breakdown_until = self.time + self.breakdown_minutes
                self.log(f"{active.bus_id} breakdown: six-minute roadside hold", "disruption", bus_id=active.bus_id)
            else:
                self.log("Breakdown skipped: no active vehicle", "disruption")
        if self.emergency_dispatch_minute is not None and self.time == self.emergency_dispatch_minute:
            yard = next((bus for bus in self.buses if bus.state == BusState.YARD), None)
            if yard:
                self._dispatch_bus(yard, staged=True)
                self.log(
                    f"Emergency dispatch: {yard.bus_id} deployed toward Central",
                    "emergency",
                    bus_id=yard.bus_id,
                    reason="The scenario requested a reserve bus at this minute.",
                )
            else:
                self.log("Emergency dispatch requested, but no bus is available", "emergency")

    def _upcoming_transfers(self, lookahead=10):
        """ION arrivals at Central and UW in the next `lookahead` minutes.

        The controller sees the live ION timetable, delays included.
        """
        upcoming = []
        for at, trip, stop_id, riders in self.hub_arrivals:
            until = math.floor(at / 60) - self.time
            if 0 < until <= lookahead and riders:
                upcoming.append((until, trip, stop_id, riders))
        return upcoming

    def _near_term_hub_demand(self, lookahead=10):
        demand = (
            len(self.stops["CENTRAL"].queue)
            + len(self.stops["UW"].queue)
            + len(self.stops["WATERLOO_SQUARE"].queue)
        )
        return demand + sum(riders for _, _, _, riders in self._upcoming_transfers(lookahead))

    def _congested_minutes(self, origin, target, base, stop_at_signal=False):
        """Traffic stretches a leg, but light traffic never squeezes it below
        free flow (including a stop at a red signal), so buses stay under ~58 km/h."""
        return max(corridor.free_flow_minutes(origin, target, stop_at_signal), round(base * self.traffic_multiplier))

    def _segment_base_minutes(self, from_index, to_index):
        if from_index == -1:
            return YARD_BASE_MINUTES
        return SEGMENT_BASE_MINUTES[from_index % len(SEGMENT_BASE_MINUTES)]

    def _intersection_for_segment(self, from_index, to_index):
        if from_index == -1:
            return "I3"
        return SEGMENT_INTERSECTIONS[from_index % len(SEGMENT_INTERSECTIONS)]

    def _travel_minutes(self, bus):
        base = self._segment_base_minutes(bus.current_stop_index, bus.target_stop_index)
        origin, target = self._segment_ids(bus)
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
                        f"{bus.bus_id} signal priority approved at {intersection.name}",
                        "priority",
                        bus_id=bus.bus_id,
                        reason=f"The signal was red, the bus was {bus.schedule_deviation} min late, "
                               f"and carried {bus.occupancy} riders (threshold {max(10, bus.capacity // 3)}).",
                    )

        bus.segment_hold_minutes = signal_delay
        # At a red light the bus waits behind the cars already queued there.
        bus.segment_queue_cars = corridor.queue_cars(self.traffic_multiplier) if signal_delay else 0
        congestion = self._congested_minutes(origin, target, base, stop_at_signal=bool(signal_delay))
        return congestion + signal_delay

    def _dispatch_bus(self, bus, staged=False, reason=None):
        self.last_release = self.time
        bus.state = BusState.EN_ROUTE
        bus.current_stop_index = -1
        bus.target_stop_index = 3
        bus.segment_elapsed = 0
        bus.segment_minutes = self._congested_minutes("YARD", "CENTRAL", YARD_BASE_MINUTES)
        bus.segment_progress = 0.0
        bus.segment_hold_minutes = 0
        bus.segment_queue_cars = 0
        bus.schedule_deviation = 1 if staged else 0

        if staged:
            self.metrics.buses_staged += 1
            self.log(f"{bus.bus_id} pre-staged toward Central Station", "decision", bus_id=bus.bus_id, reason=reason)
        else:
            self.log(
                f"{bus.bus_id} dispatched from regional staging yard",
                "dispatch",
                bus_id=bus.bus_id,
                reason="A bus was available for the scheduled ten-minute dispatch.",
            )

    def _dispatch_logic(self):
        yard = [bus for bus in self.buses if bus.state == BusState.YARD]
        if self.time % 10 == 0 and yard:
            if self.intelligent and self.pulled_forward_slot == self.time:
                self.log(
                    f"Scheduled {self.time:02d}:00 release skipped: its bus already left early",
                    "decision",
                    reason=f"A bus was pre-staged at minute {self.last_release}, taking this slot so the "
                           "buses stay spread out instead of leaving together.",
                )
            else:
                self._dispatch_bus(yard.pop(0))

        if self.intelligent and yard:
            demand = self._near_term_hub_demand()
            arriving_capacity = sum(
                max(0, bus.capacity - bus.occupancy)
                for bus in self.buses
                if bus.state != BusState.YARD and bus.target_stop_index in (0, 1, 3)
            )
            threshold = max(70, arriving_capacity + 25)
            since = None if self.last_release is None else self.time - self.last_release
            spaced = since is None or since >= self.STAGING_MIN_GAP
            spare = len(yard) > self.STAGING_RESERVE
            if demand > threshold and spaced and spare:
                trains = sorted(self._upcoming_transfers(), key=lambda item: -item[3])
                ion_note = ""
                if trains and trains[0][3] >= 10:
                    until, trip, stop_id, riders = trains[0]
                    ion_note = (f" Largest ION transfer: {trip.train_id} due at {self.stops[stop_id].name} "
                                f"in {until} min with {riders} riders.")
                self._dispatch_bus(
                    yard[0],
                    staged=True,
                    reason=f"Forecast demand of {demand} riders exceeded the staging threshold "
                           f"of {threshold}, with {arriving_capacity} seats expected to arrive.{ion_note}",
                )
                if self.STAGING_PULLS_FORWARD and self.time % 10:
                    self.pulled_forward_slot = (self.time // 10 + 1) * 10

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
        bus.doors_open_s = self.time * 60.0
        boarding = self._board(bus, stop, bus.doors_open_s, bus.doors_open_s)

        if alighted or boarding:
            self.log(
                f"{bus.bus_id} at {stop.name}: {alighted} off / {len(boarding)} on",
                "boarding",
            )

    def _board(self, bus, stop, until_s, doors_open_s):
        """Board riders (queue order) who are eligible by `until_s`, up to the
        free seats. Each boards at max(eligible, doors open)."""
        seats = max(0, bus.capacity - bus.occupancy)
        boarding, staying = [], []
        for passenger in stop.queue:
            if len(boarding) < seats and passenger.ready_s <= until_s:
                boarding.append(passenger)
            else:
                staying.append(passenger)
        stop.queue = staying
        for passenger in boarding:
            board_s = max(passenger.ready_s, doors_open_s)
            passenger.boarded_time = math.floor(board_s / 60)
            passenger.boarded_s = board_s
            wait_min = max(0.0, board_s - passenger.ready_s) / 60.0
            self.metrics.total_passengers_boarded += 1
            self.metrics.cumulative_wait_minutes += wait_min
            if passenger.source == "ion":
                self.metrics.ion_transfers_boarded += 1
                self.metrics.cumulative_transfer_wait += wait_min
        bus.onboard.extend(boarding)
        return boarding

    def _board_until_doors_close(self, bus, stop_id):
        """Riders who became eligible while the bus dwelt board until its doors
        close, DOOR_CLOSE_S before it departs at this minute."""
        if bus.doors_open_s is None:
            return
        stop = self.stops[stop_id]
        close_s = self.time * 60.0 - DOOR_CLOSE_S
        late = self._board(bus, stop, close_s, bus.doors_open_s)
        if late:
            self.log(f"{bus.bus_id} at {stop.name}: {len(late)} more on before the doors closed", "boarding")
        bus.doors_open_s = None

    def _advance_buses(self):
        for bus in self.buses:
            if bus.state == BusState.YARD:
                continue
            if bus.bus_id == self.breakdown_bus_id and self.time < self.breakdown_until:
                continue
            if bus.bus_id == self.breakdown_bus_id and self.time == self.breakdown_until:
                self.log(f"{bus.bus_id} returned to service", "recovery")
                self.breakdown_bus_id = None

            if bus.state == BusState.DWELLING:
                bus.dwell_minutes -= 1
                if bus.dwell_minutes <= 0:
                    self._board_until_doors_close(bus, ROUTE[bus.target_stop_index])
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
                    0,
                    bus.segment_minutes
                    - self._segment_base_minutes(
                        bus.current_stop_index, bus.target_stop_index
                    ),
                )

    def _bus_xy(self, bus):
        yard = (88.0, 88.0)
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

    def _segment_ids(self, bus):
        origin = "YARD" if bus.current_stop_index == -1 else ROUTE[bus.current_stop_index]
        return origin, ROUTE[bus.target_stop_index]

    def _bus_motion(self, bus):
        """Where the bus is along the street path of its current segment.

        Progress is distance travelled divided by segment length. The bus
        accelerates, slows for corners and a red signal, and brakes into the
        stop; the profile is fitted to the minutes the simulator allotted.
        """
        if bus.state == BusState.YARD:
            return None
        origin, target = self._segment_ids(bus)
        run = corridor.bus_run(origin, target, max(1, bus.segment_minutes), bus.segment_hold_minutes,
                               bus.segment_queue_cars)
        elapsed = bus.segment_minutes if bus.state == BusState.DWELLING else bus.segment_elapsed
        distance = run.distance(elapsed * 60.0)
        return {
            "from": origin,
            "to": target,
            "elapsed": elapsed,
            "minutes": bus.segment_minutes,
            "hold_minutes": bus.segment_hold_minutes,
            "queue_cars": bus.segment_queue_cars,
            "length_m": run.length,
            "distance_m": round(distance, 1),
        }

    def _bus_geo(self, bus, motion=None):
        motion = motion if motion is not None else self._bus_motion(bus)
        if motion is None:
            return YARD_GEO
        seg = corridor.segment(motion["from"], motion["to"])
        return corridor.position(seg, motion["distance_m"])

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
                    "lon": GEO_LAYOUT[stop.stop_id][0],
                    "lat": GEO_LAYOUT[stop.stop_id][1],
                    "queue": len(stop.queue),
                    "sprites": min(16, math.ceil(len(stop.queue) / 6)),
                }
                for stop in self.stops.values()
            ],
            "intersections": [
                {
                    "id": intersection.intersection_id,
                    "name": intersection.name,
                    "x": intersection.x,
                    "y": intersection.y,
                    "lon": INTERSECTION_GEO[intersection.intersection_id][0],
                    "lat": INTERSECTION_GEO[intersection.intersection_id][1],
                    "phase": intersection.phase(self.time),
                }
                for intersection in self.intersections.values()
            ],
            "buses": [],
        }

        for bus in self.buses:
            x, y = self._bus_xy(bus)
            motion = self._bus_motion(bus)
            lon, lat = self._bus_geo(bus, motion)
            target_name = (
                self.stops[ROUTE[bus.target_stop_index]].name
                if bus.state != BusState.YARD
                else "Central Station"
            )
            snapshot["buses"].append(
                {
                    "id": bus.bus_id,
                    "x": x,
                    "y": y,
                    "lon": lon,
                    "lat": lat,
                    "state": "BROKEN_DOWN" if bus.bus_id == self.breakdown_bus_id and self.time < self.breakdown_until else bus.state.name,
                    "occupancy": bus.occupancy,
                    "capacity": bus.capacity,
                    "target": target_name,
                    "progress": round(motion["distance_m"] / motion["length_m"], 3) if motion else 0.0,
                    "schedule_deviation": bus.schedule_deviation,
                    "segment": motion,
                }
            )

        snapshot["trains"] = self._train_snapshot()
        self.metrics.ion_trains_peak = max(self.metrics.ion_trains_peak, len(snapshot["trains"]))
        self.timeline.append(snapshot)

    def _train_snapshot(self):
        """ION trains in service at this minute: physical train, its trip, where,
        and who is transferring."""
        out = []
        t = self.time * 60.0
        for pose in self.ion.trains_at(t):
            trip = pose["trip"]
            if trip is None:
                continue
            line = self.ion.lines[trip.direction]
            ahead = [
                (stop_id, riders) for stop_id, riders in trip.transfers.items()
                if self.ion.arrival_s(trip, next(n for n, s in ion.HUBS.items() if s == stop_id)) >= t
            ]
            found = self.ion.state(trip, t)
            leg = found[2] if found else (0 if pose["route"] and pose["route"].startswith("depart") else len(line.legs) - 1)
            station = pose["station"]
            lon, lat = pose["lonlat"]
            out.append({
                "id": pose["vehicle_id"],
                "trip_id": trip.train_id,
                "direction": trip.direction,
                "nose_m": pose["nose_line"],
                "station": station,
                "next_station": station or line.legs[min(leg, len(line.legs) - 1)].to_station,
                "lon": lon,
                "lat": lat,
                "delay_min": round(max(0.0, trip.depart_s - trip.scheduled_s) / 60.0, 1),
                "hold": trip.hold_reason,
                "terminal": pose["terminal"],
                "activity": pose["note"],
                "transfers_ahead": {stop_id: riders for stop_id, riders in ahead if riders},
            })
        return out

    def _log_rail_decisions(self):
        """Rail decisions taken during this minute, into the event log."""
        for d in self.ion.ops.decisions:
            if math.floor(d.t / 60) == self.time:
                self.log(f"{d.vehicle_id}{' · ' + d.trip_id if d.trip_id else ''}: {d.trigger} → {d.chosen}",
                         "rail", train_id=d.vehicle_id,
                         reason="; ".join(f"{o['option']}: {o['reason']}" for o in d.options if not o["feasible"]) or None)
                self.events[-1]["decision_id"] = d.id
                self.events[-1]["level"] = d.level

    def run(self):
        self.log("Waterloo Corridor Test Scenario started", "system")

        for minute in range(self.duration + 1):
            self.time = minute
            if minute > 0:
                self._spawn_background_demand()
            self._process_train_arrivals()
            self._log_rail_decisions()
            self._scenario_events()
            self._dispatch_logic()
            self._advance_buses()
            self._capture_snapshot()

        self.metrics.passengers_left_waiting = sum(
            len(stop.queue) for stop in self.stops.values()
        )
        self.log("Corridor simulation complete", "system")
        return self.metrics
