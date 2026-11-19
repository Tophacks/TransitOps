"""Compare ION terminal operations with and without a platform hold, and with
holds only versus dispatch reassignment, under one timetable and demand.

    python3 compare_rail.py            # markdown tables on stdout

The bus corridor runs the adaptive controller in every case, so differences
come from the rail run: departure delays, queueing at the terminals, and when
transfer riders reach the bus at Central and UW.
"""
import math

from ion import IonPlan
from models import Bus
from simulation_engine import TransitSimulation

# The page's default scenario (frontend/app/page.tsx DEFAULTS).
SCENARIO = {"traffic_multiplier": 1.15, "train_delay": 4, "passenger_demand": 110, "fleet_size": 3, "seed": 17}
HOLDS = {
    "no hold": None,
    "Fairway platform 2, 12 min from 20:00": {"terminal": "fairway", "platform": "2", "minute": 20, "minutes": 12, "reason": "door fault"},
    "Conestoga platform 1, 14 min from 20:00": {"terminal": "conestoga", "platform": "1", "minute": 20, "minutes": 14, "reason": "door fault"},
}


def run(hold, reassign):
    plan = IonPlan(duration_min=60, headway_min=8, transfer_demand=SCENARIO["passenger_demand"],
                   delay_min=SCENARIO["train_delay"], platform_hold=hold, reassign=reassign)
    sim = TransitSimulation([Bus(f"B{101 + i}") for i in range(SCENARIO["fleet_size"])], plan, intelligent=True,
                            duration=60, traffic_multiplier=SCENARIO["traffic_multiplier"], seed=SCENARIO["seed"])
    metrics = sim.run()
    window = [t for t in plan.trips if 0 <= t.scheduled_s < plan.duration_s]
    return plan, metrics, plan.ops.metrics(window)


def hub_arrivals(plan):
    return {(trip.train_id, stop): (at, riders) for at, trip, stop, riders in plan.hub_arrivals() if riders}


def main():
    rows = []
    clear_plan, _, _ = run(None, True)
    clear_hubs = hub_arrivals(clear_plan)
    detail = []
    for label, hold in HOLDS.items():
        for policy, reassign in (("holds only", False), ("dispatch", True)):
            plan, m, r = run(hold, reassign)
            hubs = hub_arrivals(plan)
            late_riders = sum(riders for k, (at, riders) in hubs.items() if k in clear_hubs and at > clear_hubs[k][0] + 30)
            rider_minutes = sum(riders * max(0.0, at - clear_hubs[k][0]) / 60 for k, (at, riders) in hubs.items() if k in clear_hubs)
            rows.append((label, policy, r, m, late_riders, rider_minutes))
            if hold and reassign:
                for d in plan.ops.decisions:
                    if d.chosen.startswith("swap") and 0 <= d.t < 3600:
                        trip = next(t for t in plan.trips if t.train_id == d.trip_id)
                        base_plan, _, _ = run(hold, False)
                        base = next(t for t in base_plan.trips if t.train_id == d.trip_id)
                        stops = [(s, trip.station_times.get(s), base.station_times.get(s)) for s in ("Central Station", "University Of Waterloo Station")]
                        detail.append((label, d, trip, base, stops))
    print("| Hold | Rail policy | Late departures (>1 min) | Missed (>3 min) | Avg / max departure delay | Signal queueing | Trips reassigned | Transfer riders reaching the bus late | Rider-minutes of transfer delay | Avg transfer wait for the bus | Avg boarding wait |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for label, policy, r, m, late, rm in rows:
        print(f"| {label} | {policy} | {r['late_departures']} | {r['missed_departures']} | "
              f"{r['avg_departure_delay_s']:.0f} s / {r['max_departure_delay_s'] / 60:.1f} min | {r['platform_queue_s']:.0f} s | "
              f"{r['trip_swaps']} | {late} | {rm:.0f} | {m.average_transfer_wait_minutes:.2f} min | {m.average_wait_minutes:.2f} min |")
    print()
    for label, d, trip, base, stops in detail:
        print(f"**{label}:** {d.id} at {math.floor(d.t / 60):02d}:{int(d.t % 60):02d}, {d.vehicle_id} held with {d.trip_id}; "
              f"dispatch chose `{d.chosen}`.")
        print(f"- {d.trip_id} left at {trip.depart_s / 60:.1f} min with {trip.vehicle_id} "
              f"(holds only: {base.depart_s / 60:.1f} min with {base.vehicle_id}).")
        for name, a, b in stops:
            if a is not None and b is not None:
                riders = trip.transfers.get("CENTRAL" if name.startswith("Central") else "UW", 0)
                print(f"- At {name}: {a / 60:.1f} min vs {b / 60:.1f} min ({(b - a) / 60:.1f} min earlier), "
                      f"{riders} riders transferring to the bus.")
        print()


if __name__ == "__main__":
    main()
