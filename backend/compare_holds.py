"""Do connection holds at UW and Central pay for themselves?

    python3 compare_holds.py

Same timetable, demand and seed with connection holds off and on, under no
platform hold and the Fairway and Conestoga holds (rail dispatch on). Reports
transfer riders boarded, the rider-minutes the holds saved transfer riders
and cost riders onboard and downstream, and every rider's wait (including
riders still waiting when the hour ends).
"""
from compare_rail import HOLDS, SCENARIO
from ion import IonPlan
from models import Bus
from simulation_engine import TransitSimulation


def run(hold, connection_holds):
    plan = IonPlan(duration_min=60, headway_min=8, transfer_demand=SCENARIO["passenger_demand"],
                   delay_min=SCENARIO["train_delay"], platform_hold=hold, reassign=True)
    sim = TransitSimulation([Bus(f"B{101 + i}") for i in range(SCENARIO["fleet_size"])], plan, intelligent=True,
                            duration=60, traffic_multiplier=SCENARIO["traffic_multiplier"], seed=SCENARIO["seed"],
                            connection_holds=connection_holds)
    return sim, sim.run()


def main():
    print("| Platform hold | Connection holds | Holds taken | Transfer riders boarded | Avg transfer wait (boarded) | "
          "Transfer rider-min incl. still waiting | Hold cost: onboard / downstream rider-min | "
          "All riders boarded | All rider-min incl. still waiting | Trips completed | Left waiting |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    detail = []
    for label, hold in HOLDS.items():
        for on in (False, True):
            sim, m = run(hold, on)
            transfer_total = m.cumulative_transfer_wait + m.unserved_transfer_wait_minutes
            all_total = m.cumulative_wait_minutes + m.unserved_wait_minutes
            print(f"| {label} | {'on' if on else 'off'} | {m.connection_holds} ({m.connection_hold_minutes} min) | "
                  f"{m.ion_transfers_boarded} of {m.ion_transfers} | {m.average_transfer_wait_minutes:.2f} min | "
                  f"{transfer_total:.0f} | {m.hold_onboard_rider_minutes:.0f} / {m.hold_downstream_rider_minutes:.0f} | "
                  f"{m.total_passengers_boarded} | {all_total:.0f} | {m.completed_trips} | {m.passengers_left_waiting} |")
            if on:
                detail.append((label, sim.decisions))
    print()
    for label, decisions in detail:
        for d in decisions:
            if d["chosen"] != "depart on time" or any(o["feasible"] and o["option"] != "depart on time" for o in d["options"]):
                print(f"**{label}, {d['vehicle_id']} at {d['terminal'].upper()} {d['minute']:02d}:00:** {d['trigger']}. "
                      f"Chose {d['chosen']}; {d['outcome']}.")
                for o in d["options"]:
                    print(f"- {'✓' if o['feasible'] else '✗'} {o['option']}: {o['reason']}")
                print()


if __name__ == "__main__":
    main()
