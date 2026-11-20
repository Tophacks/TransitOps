import math
import random

import corridor
import ion
from copy import copy

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


def _clock(seconds):
    s = int(round(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


# Rail-to-bus connections are timed in seconds, not whole minutes. A transfer
# rider can board from the moment their train stops plus the walk to the bus
# stop; a bus boards everyone already eligible when its doors open, and keeps
# boarding riders who become eligible while it dwells, until its doors close
# DOOR_CLOSE_S before it pulls out. Both values are modelled.
TRANSFER_WALK_S = {"CENTRAL": 60.0, "UW": 60.0}
DOOR_CLOSE_S = 5.0

# Connection holds (dispatch, adaptive controller only). When a bus with free
# seats is about to close its doors at a transfer hub and ION riders will be
# eligible within CONNECTION_HOLD_MAX_MIN, dispatch weighs holding it for 1..3
# whole minutes: transfer rider-minutes saved (each rider's exact eligibility,
# train stop + walk, against the next bus's predicted arrival) versus the
# rider-minutes the hold costs (riders onboard, and riders waiting at the
# stops the bus serves next). It holds only for a net saving. Modelled policy.
CONNECTION_HOLD_STOPS = ("UW", "CENTRAL")
CONNECTION_HOLD_MAX_MIN = 3


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
    STAGING_MAX_GAP = 15          # minutes from a pulled-forward release to the scheduled one after it
    STAGING_ON_REQUEST = True     # pre-stage only when a station the bus can reach in time asks for capacity
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
        connection_holds=None,
        uw_block=None,
        uw_station=True,
        uw_cars="moderate",
        driver_errors=None,
    ):
        self.buses = buses
        # Connection holds at UW and Central: on with the adaptive controller.
        self.connection_holds = intelligent if connection_holds is None else connection_holds
        self.decisions = []
        # ION trains on the real track: their arrivals at Central and UW bring
        # riders who transfer to the corridor bus (see ion.py).
        self.ion = ion_plan
        self.hub_arrivals = ion_plan.hub_arrivals()
        self.decision_offset = 0
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
        # UW Station in seconds: buses as agents on Transit Plaza Road (uw_station.py).
        self.reports = {}
        self.last_recommendation = {}
        self.last_recommendation_answered = None
        self.capacity_answered = set()
        self.uw = None
        if uw_station:
            from uw_station import UwStation
            self.uw = UwStation(self, block=uw_block, seed=seed, adaptive=intelligent, cars=uw_cars, errors=driver_errors)

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

        Only what is known now: arrived trains and predicted arrivals (ion.predict_arrival).
        """
        upcoming = []
        now = self.time * 60.0
        for stop_id in ("CENTRAL", "UW"):
            for at, trip, riders, _ in self._predicted_arrivals(stop_id, now):
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
            asked = [r for r in self.reports.values() if r["recommendation"] == "request extra capacity"]
            if asked and self.last_recommendation_answered != self.time and \
                    any(self.last_recommendation.get(r["stop"]) == r["recommendation"] for r in asked):
                self._answer_capacity(asked, yard, spaced, spare, since, demand, threshold)
            # Pulling a release forward takes the next scheduled slot, so the gap to the
            # release after that grows: never beyond STAGING_MAX_GAP minutes.
            following = (self.time // 10 + 2) * 10
            bounded = not self.STAGING_PULLS_FORWARD or self.time % 10 == 0 or following - self.time <= self.STAGING_MAX_GAP
            # A bus from the yard reaches Central first; it can answer only a request
            # from a station it reaches within the station's horizon.
            reach = {"CENTRAL": YARD_BASE_MINUTES, "UW": YARD_BASE_MINUTES + 1 + SEGMENT_BASE_MINUTES[3]}
            requested = (any(reach[r["stop"]] * 60.0 <= self.STATION_HORIZON_S for r in asked)
                         if self.STAGING_ON_REQUEST else True)
            if demand > threshold and spaced and spare and bounded and requested:
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

    def _answer_capacity(self, asked, yard, spaced, spare, since, demand, threshold):
        """Dispatch's answer to a station's request for extra capacity: release a
        reserve bus now, or not, under the staging rules."""
        self.last_recommendation_answered = self.time
        # Answer first the station a bus from the yard reaches soonest (Central).
        r = sorted(asked, key=lambda x: x["stop"] != "CENTRAL")[0]
        key = (r["stop"], self.time // 10)
        if key in self.capacity_answered:
            return
        self.capacity_answered.add(key)
        release_ok = spaced and spare
        reasons = []
        if not spaced:
            reasons.append(f"a bus left the yard {since} min ago (minimum {self.STAGING_MIN_GAP})")
        if not spare:
            reasons.append(f"only {len(yard)} bus(es) in the yard, {self.STAGING_RESERVE} kept in reserve")
        nxt = (self.time // 10 + 1) * 10
        options = [
            {"option": f"release {yard[0].bus_id} now", "feasible": release_ok,
             "reason": ("reaches Central in about " f"{YARD_BASE_MINUTES} min" if release_ok else "; ".join(reasons)),
             "estimate_s": None},
            {"option": f"keep the scheduled release at {nxt:02d}:00", "feasible": True,
             "reason": f"forecast demand {demand} against the staging threshold {threshold}", "estimate_s": None},
        ]
        chosen = options[0]["option"] if release_ok and demand > threshold else options[1]["option"]
        if release_ok and demand <= threshold:
            options[0]["reason"] += f"; but demand {demand} is under the threshold {threshold}"
        self.decisions.append({
            "id": f"C{self.decision_offset + len(self.decisions) + 1:03d}", "t": self.time * 60.0, "minute": self.time, "level": "dispatch",
            "terminal": r["stop"].lower(), "vehicle_id": "dispatch", "trip_id": None,
            "trigger": f"{self.stops[r['stop']].name} asks for extra capacity: {r['reason']}",
            "options": options, "chosen": chosen, "outcome": "",
            "location": [GEO_LAYOUT[r['stop']][0], GEO_LAYOUT[r['stop']][1]],
        })

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

    def _board_until_doors_close(self, bus, stop_id, close_s=None):
        """Riders who became eligible while the bus dwelt board until its doors
        close, DOOR_CLOSE_S before it departs at this minute."""
        if bus.doors_open_s is None:
            return
        stop = self.stops[stop_id]
        close_s = self.time * 60.0 - DOOR_CLOSE_S if close_s is None else close_s
        late = self._board(bus, stop, close_s, bus.doors_open_s)
        if late:
            self.log(f"{bus.bus_id} at {stop.name}: {len(late)} more on before the doors closed", "boarding")
        decision = getattr(bus, "pending_decision", None)
        if decision is not None and bus.held_minutes:
            transfers = sum(1 for p in late if p.source == "ion")
            decision["outcome"] = (f"held until {_clock(self.time * 60.0)}: {transfers} ION transfer riders and "
                                   f"{len(late) - transfers} others boarded during the hold")
            self._check_prediction(decision, stop_id, close_s)
            bus.pending_decision = None
        bus.doors_open_s = None

    # --- UW Station callbacks (seconds) -------------------------------------------
    def station_set_down(self, bus, t):
        """Doors open at the set-down berth: riders for UW get off."""
        staying, off = [], 0
        for p in bus.onboard:
            if p.destination == "UW":
                p.completed_time = math.floor(t / 60)
                self.metrics.completed_trips += 1
                off += 1
            else:
                staying.append(p)
        bus.onboard = staying
        if off:
            self.log(f"{bus.bus_id} at UW platform 4: {off} off", "boarding", bus_id=bus.bus_id)
        return off

    def station_doors_open(self, bus, t):
        bus.doors_open_s = t
        stop = self.stops["UW"]
        boarding = self._board(bus, stop, t, t)
        if boarding:
            self.log(f"{bus.bus_id} at UW platform 6: {len(boarding)} on ({_clock(t)})", "boarding", bus_id=bus.bus_id)

    def station_doors_closing(self, bus, t):
        """Doors due to close at t: board late riders, then dispatch may hold.
        Returns the seconds to hold (0: close now)."""
        opened = bus.doors_open_s
        self._board_until_doors_close(bus, "UW", close_s=t)
        hold = self._connection_hold(bus, "UW", opened, close_s=t)
        if hold:
            bus.doors_open_s = opened
            return hold * 60.0
        return 0.0

    def station_missed(self, bus, t, stop):
        """A stop the bus could not serve after a driver error (recorded)."""
        self.metrics.missed_stops = getattr(self.metrics, "missed_stops", 0) + 1
        self.log(f"{bus.bus_id} missed stop {stop} at UW Station", "disruption", bus_id=bus.bus_id,
                 reason="Driver error; Control found no reverse or forward recovery for this stop.")

    def station_exit(self, bus, t, arrived=None):
        """The bus has left the modelled roads at UW: its next leg starts, or,
        after a detour, it is already at the next stop (`arrived`)."""
        if arrived is not None:
            bus.at_station = False
            bus.doors_open_s = None
            bus.hold_evaluated = False
            bus.held_minutes = 0
            bus.current_stop_index = ROUTE.index("UW")
            bus.target_stop_index = ROUTE.index(arrived)
            bus.state = BusState.EN_ROUTE
            bus.segment_minutes = 1
            bus.segment_elapsed = 0
            bus.segment_progress = 0.0
            bus.segment_hold_minutes = 0
            bus.segment_queue_cars = 0
            bus.detour_arrival = True
            bus.station_exit_s = t
            return
        """The bus has turned onto Phillip Street: its next leg starts."""
        bus.at_station = False
        bus.doors_open_s = None
        bus.hold_evaluated = False
        bus.held_minutes = 0
        bus.current_stop_index = ROUTE.index("UW")
        bus.target_stop_index = (bus.current_stop_index + 1) % len(ROUTE)
        bus.state = BusState.EN_ROUTE
        bus.segment_elapsed = 0
        bus.segment_progress = 0.0
        bus.segment_minutes = self._travel_minutes(bus)
        bus.station_exit_s = t

    def _predicted_arrivals(self, stop_id, now):
        """ION arrivals at a hub as known at `now`: confirmed if the train is
        there, else predicted from where it is (ion.predict_arrival)."""
        name = next(n for n, s in ion.HUBS.items() if s == stop_id)
        out = []
        for trip in self.ion.trips:
            riders = trip.transfers.get(stop_id, 0)
            if not riders or not trip.station_times and trip.scheduled_s + trip.trip_hold_s > now + 3600:
                continue
            at, kind = self.ion.predict_arrival(trip, name, now)
            out.append((at, trip, riders, kind))
        return sorted(out, key=lambda x: (x[0], x[1].train_id))

    def _check_prediction(self, decision, stop_id, close_s):
        """After a hold: how the predicted eligibility compared with what happened."""
        name = next(n for n, s in ion.HUBS.items() if s == stop_id)
        notes = []
        for train, predicted in decision.get("predicted", {}).items():
            trip = next(x for x in self.ion.trips if x.train_id == train)
            actual = trip.station_times.get(name)
            if actual is not None:
                actual += TRANSFER_WALK_S[stop_id]
                notes.append(f"{train} riders eligible {_clock(actual)} (predicted {_clock(predicted)})")
        if notes:
            decision["outcome"] += "; " + "; ".join(notes)

    # --- station reports ------------------------------------------------------------
    STATION_HORIZON_S = 600.0      # predicted transfer arrivals looked at
    CAPACITY_MARGIN = 10           # riders beyond the seats coming before a station asks for capacity

    def _station_report(self, stop_id, now):
        """What a hub station reports at `now`, from what it can know then:
        riders confirmed in its queue, transfer riders predicted from the ION
        trains' current positions, the buses coming with their free seats, and
        blocked berths. It recommends a bounded hold or extra capacity."""
        stop = self.stops[stop_id]
        walk = TRANSFER_WALK_S[stop_id]
        queue = [p for p in stop.queue if p.ready_s <= now]
        predicted = [(at + walk, trip, riders, kind) for at, trip, riders, kind in self._predicted_arrivals(stop_id, now)
                     if now < at + walk <= now + self.STATION_HORIZON_S]
        coming = []
        for b in self.buses:
            if b.bus_id == self.breakdown_bus_id:
                continue
            if getattr(b, "at_station", False) and stop_id == "UW":
                eta = 0
            else:
                eta = self._eta_minutes(b, stop_id)
                if eta is None:
                    eta = 0 if b.state == BusState.DWELLING and ROUTE[b.target_stop_index] == stop_id else None
            if eta is not None and eta * 60.0 <= self.STATION_HORIZON_S:
                coming.append((eta, b.bus_id, max(0, b.capacity - b.occupancy)))
        coming.sort()
        demand = len(queue) + sum(r for _, _, r, _ in predicted)
        seats = sum(free for _, _, free in coming)
        blocked = self.uw.blocked(now) if stop_id == "UW" and self.uw is not None else []
        rec = "none"
        why = ""
        at_stop = [c for c in coming if c[0] == 0 and c[2] > 0]
        soon = [x for x in predicted if x[2] and x[0] - now <= CONNECTION_HOLD_MAX_MIN * 60.0 + 60.0]
        if at_stop and soon:
            n = min(CONNECTION_HOLD_MAX_MIN, max(1, math.ceil((soon[0][0] - now - 55.0) / 60.0)))
            rec = f"hold {at_stop[0][1]} up to {n} min"
            why = (f"{soon[0][2]} riders from {soon[0][1].train_id} ({soon[0][3]}) eligible {_clock(soon[0][0])}; "
                   f"{at_stop[0][1]} has {at_stop[0][2]} free seats")
        elif demand > seats + self.CAPACITY_MARGIN:
            rec = "request extra capacity"
            why = f"{demand} riders (queue {len(queue)} + predicted {demand - len(queue)}) for {seats} seats in 10 min"
        return {
            "stop": stop_id, "t": now, "queue": len(queue), "transfers_waiting": sum(1 for p in queue if p.source == "ion"),
            "predicted": [{"train": trip.train_id, "eligible_s": round(at, 1), "riders": r, "basis": kind}
                          for at, trip, r, kind in predicted],
            "buses": [{"bus": bid, "eta_min": eta, "free_seats": free} for eta, bid, free in coming],
            "blocked_berths": blocked, "recommendation": rec, "reason": why,
        }

    def _station_reports(self):
        now = self.time * 60.0
        self.reports = {sid: self._station_report(sid, now) for sid in CONNECTION_HOLD_STOPS}
        for sid, r in self.reports.items():
            last = self.last_recommendation.get(sid)
            # A countdown (hold up to 3, 2, 1 min) is the same recommendation.
            key = lambda rec: rec.split(" up to ")[0]  # noqa: E731
            if r["recommendation"] != "none" and (last is None or key(r["recommendation"]) != key(last)) and self.connection_holds:
                pred = "; ".join(f"{x['train']} {x['riders']} riders eligible {_clock(x['eligible_s'])} ({x['basis']})"
                                 for x in r["predicted"][:3]) or "no ION transfers predicted"
                buses = "; ".join(f"{b['bus']} in {b['eta_min']} min with {b['free_seats']} free seats"
                                  for b in r["buses"][:3]) or "no bus within 10 min"
                options = [
                    {"option": "confirmed queue", "feasible": True, "reason": f"{r['queue']} riders "
                     f"({r['transfers_waiting']} from ION)", "estimate_s": None},
                    {"option": "predicted transfer arrivals", "feasible": True, "reason": pred, "estimate_s": None},
                    {"option": "buses coming", "feasible": True, "reason": buses, "estimate_s": None},
                ]
                if r["blocked_berths"]:
                    options.append({"option": "blocked berths", "feasible": False,
                                    "reason": "; ".join(r["blocked_berths"]), "estimate_s": None})
                self.decisions.append({
                    "id": f"S{self.decision_offset + len(self.decisions) + 1:03d}", "t": now, "minute": self.time, "level": "station",
                    "terminal": sid.lower(), "vehicle_id": self.stops[sid].name, "trip_id": None,
                    "trigger": f"Station report: {r['reason']}", "options": options,
                    "chosen": f"recommend: {r['recommendation']}", "outcome": "",
                    "location": [GEO_LAYOUT[sid][0], GEO_LAYOUT[sid][1]],
                })
            self.last_recommendation[sid] = r["recommendation"]

    def _eta_minutes(self, bus, stop_id):
        """Dispatch's estimate of the minutes until `bus` opens its doors at
        `stop_id`: what is left of its current leg, then scheduled leg times
        and a minute's dwell at each stop on the way. Yard buses: the next
        10-minute release, then the yard leg."""
        target = ROUTE.index(stop_id)
        if bus.state == BusState.YARD:
            release = (self.time // 10 + 1) * 10 - self.time
            minutes, index = release + YARD_BASE_MINUTES, 3
        elif bus.state == BusState.DWELLING:
            minutes, index = bus.dwell_minutes, bus.target_stop_index
            if index == target:
                return None  # standing there now
            nxt = (index + 1) % len(ROUTE)
            minutes += self._segment_base_minutes(index, nxt)
            index = nxt
        else:
            minutes, index = max(0, bus.segment_minutes - bus.segment_elapsed), bus.target_stop_index
        while index != target:
            nxt = (index + 1) % len(ROUTE)
            minutes += 1 + self._segment_base_minutes(index, nxt)
            index = nxt
        return minutes

    def _connection_hold(self, bus, stop_id, opened, close_s=None):
        """Weigh holding a bus at a transfer hub for incoming ION riders.
        Returns the whole minutes to hold (0: leave now) and records the decision.

        Only what is known when the doors are due to close is used: trains that
        have arrived (confirmed) and the others' predicted arrivals from where
        they are now (ion.predict_arrival), never the run's actual future."""
        if not self.connection_holds or stop_id not in CONNECTION_HOLD_STOPS or bus.hold_evaluated or opened is None:
            return 0
        bus.hold_evaluated = True
        close_s = self.time * 60.0 - DOOR_CLOSE_S if close_s is None else close_s
        horizon = close_s + CONNECTION_HOLD_MAX_MIN * 60.0
        walk = TRANSFER_WALK_S[stop_id]
        known = self._predicted_arrivals(stop_id, close_s)
        incoming = [(at + walk, trip, riders) for at, trip, riders, _ in known if close_s < at + walk <= horizon]
        basis = {trip.train_id: kind for _, trip, _, kind in known}
        if not incoming:
            return 0
        stop = self.stops[stop_id]
        seats = max(0, bus.capacity - bus.occupancy)
        others = [b for b in self.buses if b is not bus and b.bus_id != self.breakdown_bus_id]
        etas = [(eta, b.bus_id) for b in others if (eta := self._eta_minutes(b, stop_id)) is not None]
        next_eta, next_bus = min(etas) if etas else (None, None)
        next_open_s = (self.time + next_eta) * 60.0 if next_eta is not None else ((self.time if self.ion.continuous else self.duration) + 30) * 60.0
        onboard = bus.occupancy
        # Riders waiting at the stops this bus serves next also get it later.
        nxt = (bus.target_stop_index + 1) % len(ROUTE)
        downstream = sum(len(self.stops[ROUTE[(nxt + k) % len(ROUTE)]].queue) for k in range(2))
        waiting_now = [p for p in stop.queue]
        riders_in = sorted((ready, trip.train_id) for ready, trip, n in incoming for _ in range(n))
        train_names = sorted({trip.train_id for _, trip, _ in incoming})
        first_ready = riders_in[0][0]
        options = []
        best = (0.0, 0, None)
        depart_cost = sum(max(0.0, max(next_open_s, ready) - ready) for ready, _ in riders_in) / 60.0
        options.append({
            "option": "depart on time", "feasible": True,
            "reason": (f"{len(riders_in)} transfer riders wait for {next_bus or 'the next bus'}"
                       f"{' (' + _clock(next_open_s) + ')' if next_bus else ''}: {depart_cost:.0f} rider-min"),
            "estimate_s": None,
        })
        for h in range(1, CONNECTION_HOLD_MAX_MIN + 1):
            new_close = close_s + h * 60.0
            # Seats go in queue order: riders already waiting, then arrivals by time.
            free = max(0, seats - len(waiting_now))
            gained = [ready for ready, _ in riders_in if ready <= new_close][:free]
            saved = sum(max(next_open_s, ready) - ready for ready in gained) / 60.0
            cost_onboard = onboard * h
            cost_down = downstream * h
            net = saved - cost_onboard - cost_down
            if seats == 0:
                reason = "no free seats"
                feasible = False
            elif free == 0:
                reason = f"its {seats} free seats go to the {len(waiting_now)} riders already waiting"
                feasible = False
            elif not gained:
                reason = f"doors would close {_clock(new_close)}, before the first rider is eligible ({_clock(first_ready)})"
                feasible = False
            else:
                reason = (f"boards {len(gained)} of {len(riders_in)} (doors close {_clock(new_close)}); saves "
                          f"{saved:.0f} rider-min, costs {cost_onboard} onboard + {cost_down} waiting downstream: "
                          f"net {net:+.0f}")
                feasible = net > 0
            options.append({"option": f"hold {h} min", "feasible": feasible, "reason": reason,
                            "estimate_s": round(new_close + DOOR_CLOSE_S, 1)})
            if feasible and net > best[0]:
                best = (net, h, (len(gained), saved, cost_onboard, cost_down))
        chosen = f"hold {best[1]} min" if best[1] else "depart on time"
        decision = {
            "id": f"B{self.decision_offset + len(self.decisions) + 1:03d}", "t": close_s, "minute": int(close_s // 60),
            "level": "dispatch", "terminal": stop_id.lower(), "vehicle_id": bus.bus_id,
            "trip_id": f"{stop_id}→{ROUTE[nxt]}",
            "trigger": (f"{len(riders_in)} transfer riders from "
                        f"{', '.join(n + ' (' + basis[n] + ')' for n in train_names)} eligible from "
                        f"{_clock(first_ready)}; doors due to close {_clock(close_s)}; {seats} free seats, {onboard} onboard"),
            "predicted": {n: round(min(at for at, trip, _ in incoming if trip.train_id == n), 1) for n in train_names},
            "options": options, "chosen": chosen, "outcome": "",
            "location": [GEO_LAYOUT[stop_id][0], GEO_LAYOUT[stop_id][1]],
        }
        rec = self._station_report(stop_id, close_s)["recommendation"]
        if rec.startswith("hold"):
            decision["trigger"] += f"; station recommended: {rec}"
        self.decisions.append(decision)
        bus.pending_decision = decision if best[1] else None
        if not best[1]:
            decision["outcome"] = f"doors closed {_clock(close_s)}"
            self.log(f"{bus.bus_id} left {stop.name} on time: holding would not pay", "hold", bus_id=bus.bus_id,
                     reason=decision["trigger"] + ". " + "; ".join(f"{o['option']}: {o['reason']}" for o in options))
            return 0
        h = best[1]
        gained, saved, c_on, c_down = best[2]
        bus.held_minutes = h
        self.metrics.connection_holds += 1
        self.metrics.connection_hold_minutes += h
        self.metrics.hold_onboard_rider_minutes += c_on
        self.metrics.hold_downstream_rider_minutes += c_down
        decision["outcome"] = f"held at {stop.name} until {_clock(close_s + DOOR_CLOSE_S + h * 60.0)}"
        self.log(f"{bus.bus_id} held {h} min at {stop.name} for {gained} ION transfer riders", "hold",
                 bus_id=bus.bus_id, reason=decision["trigger"] + f". Saves {saved:.0f} rider-min for "
                 f"{c_on + c_down} rider-min of delay ({c_on} onboard, {c_down} waiting downstream).")
        return h

    def _advance_buses(self):
        for bus in self.buses:
            if bus.state == BusState.YARD or getattr(bus, "at_station", False):
                continue
            if bus.bus_id == self.breakdown_bus_id and self.time < self.breakdown_until:
                continue
            if bus.bus_id == self.breakdown_bus_id and self.time == self.breakdown_until:
                self.log(f"{bus.bus_id} returned to service", "recovery")
                self.breakdown_bus_id = None

            if bus.state == BusState.DWELLING:
                bus.dwell_minutes -= 1
                if bus.dwell_minutes <= 0:
                    stop_id = ROUTE[bus.target_stop_index]
                    opened = bus.doors_open_s
                    self._board_until_doors_close(bus, stop_id)
                    hold = self._connection_hold(bus, stop_id, opened)
                    if hold:
                        bus.doors_open_s = opened
                        bus.dwell_minutes = hold
                        continue
                    bus.hold_evaluated = False
                    bus.held_minutes = 0
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
            if (self.uw is not None and ROUTE[bus.target_stop_index] == "UW"
                    and bus.segment_elapsed == bus.segment_minutes - 1):
                # Arriving at UW next minute: the leg was timed to the station, so the
                # bus enters the roadway early enough to reach its set-down berth then.
                bus.state = BusState.DWELLING
                bus.at_station = True
                bus.schedule_deviation = max(0, bus.segment_minutes - self._segment_base_minutes(
                    bus.current_stop_index, bus.target_stop_index))
                self.uw.arrive(bus, (self.time + 1) * 60.0 - self.uw.entry_lead_s())
                continue

            if bus.segment_progress >= 1.0:
                stop_id = ROUTE[bus.target_stop_index]
                if stop_id == "UW" and self.uw is not None:
                    # The station agent takes over at the Ring Road entrance.
                    bus.state = BusState.DWELLING
                    bus.at_station = True
                    bus.segment_progress = 1.0
                    bus.schedule_deviation = max(0, bus.segment_minutes - self._segment_base_minutes(
                        bus.current_stop_index, bus.target_stop_index))
                    self.uw.arrive(bus, self.time * 60.0)
                    continue
                self._alight_and_board(bus, stop_id)
                bus.detour_arrival = False
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
            # After an unintended turn the bus reached this stop on another road
            # (the UW Station replay draws that route).
            **({"detour": True} if getattr(bus, "detour_arrival", False) and origin == "UW" else {}),
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

        snapshot["stations"] = {sid: {k: v for k, v in r.items() if k != "t"} for sid, r in self.reports.items()}
        if self.uw is not None:
            snapshot["uw"] = self.uw.snapshot(self.time * 60.0)
        snapshot["trains"] = self._train_snapshot()
        self.metrics.ion_trains_peak = max(self.metrics.ion_trains_peak, sum(1 for x in snapshot["trains"] if x["in_service"]))
        self.timeline.append(snapshot)

    def _train_snapshot(self):
        """ION trains in service at this minute: physical train, its trip, where,
        and who is transferring."""
        out = []
        t = self.time * 60.0
        for pose in self.ion.trains_at(t):
            trip = pose["trip"]
            if trip is None:
                # A yard train, or one pulling out or returning (out of service).
                lon, lat = pose["lonlat"]
                out.append({
                    "id": pose["vehicle_id"], "trip_id": pose["trip_id"], "direction": None,
                    "nose_m": pose["nose_line"], "station": pose["station"],
                    "next_station": pose["station"] or "Dutton yard", "lon": lon, "lat": lat,
                    "delay_min": 0.0, "hold": "", "terminal": pose["terminal"], "activity": pose["note"],
                    "transfers_ahead": {}, "state": pose["state"], "in_service": False,
                })
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
                "state": pose["state"], "in_service": True,
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
            self.step_minute(minute)

        self.metrics = self.metrics_at(self.duration * 60.0)
        # GRT garages: pull-outs and returns on real roads (garage_ops.py).
        from garage_ops import Garages
        self.garages = Garages(self).run()
        self.decisions.sort(key=lambda d: d["t"])
        self.log("Corridor simulation complete", "system")
        return self.metrics


    def step_minute(self, minute):
        self.time = minute
        if minute > 0:
            self._spawn_background_demand()
        self._process_train_arrivals()
        self._log_rail_decisions()
        self._scenario_events()
        self._station_reports()
        self._dispatch_logic()
        self._advance_buses()
        if self.uw is not None:
            self.uw.advance_to((minute + 1) * 60.0)
        self._capture_snapshot()

    def metrics_at(self, end_s):
        # Reporting at a checkpoint must never charge accrued waiting twice.
        metrics = copy(self.metrics)
        metrics.passengers_left_waiting = sum(len(stop.queue) for stop in self.stops.values())
        for stop in self.stops.values():
            for p in stop.queue:
                if p.ready_s < end_s:
                    still = (end_s - p.ready_s) / 60.0
                    metrics.unserved_wait_minutes += still
                    if p.source == "ion":
                        metrics.unserved_transfer_wait_minutes += still
        return metrics
