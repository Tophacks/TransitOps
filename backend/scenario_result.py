def metrics_to_dict(metrics):
    return {
        "passengers_arrived": metrics.total_passengers_arrived,
        "passengers_boarded": metrics.total_passengers_boarded,
        "completed_trips": metrics.completed_trips,
        "left_waiting": metrics.passengers_left_waiting,
        "average_wait": round(metrics.average_wait_minutes, 2),
        "max_queue": metrics.max_queue,
        "buses_staged": metrics.buses_staged,
        "signal_priority_requests": metrics.signal_priority_requests,
        "ion_transfers": metrics.ion_transfers,
        "ion_transfers_boarded": metrics.ion_transfers_boarded,
        "average_transfer_wait": round(metrics.average_transfer_wait_minutes, 2),
        "ion_trains_peak": metrics.ion_trains_peak,
        "ion_max_delay": round(metrics.ion_max_delay_minutes, 1),
        "connection_holds": metrics.connection_holds,
        "missed_stops": getattr(metrics, "missed_stops", 0),
        "connection_hold_minutes": metrics.connection_hold_minutes,
        "hold_onboard_rider_minutes": round(metrics.hold_onboard_rider_minutes, 1),
        "hold_downstream_rider_minutes": round(metrics.hold_downstream_rider_minutes, 1),
        # Every rider's wait, including riders still waiting at the checkpoint.
        "total_wait_rider_minutes": round(metrics.cumulative_wait_minutes + metrics.unserved_wait_minutes, 1),
        "total_transfer_wait_rider_minutes": round(metrics.cumulative_transfer_wait + metrics.unserved_transfer_wait_minutes, 1),
    }


def case_result(sim, metrics, car_demand, garages, garage_decisions=()):
    plan = sim.ion
    start = sim.timeline[0]["minute"] * 60.0 if plan.continuous else None
    return {
        "metrics": metrics_to_dict(metrics),
        "events": sim.events,
        "timeline": sim.timeline,
        "ion": plan.schedule(replay_start=start),
        # bus decisions: UW Station agents, station reports, holds, capacity
        "decisions": sorted([*sim.decisions, *garage_decisions], key=lambda d: d["t"]),
        # every vehicle on UW Station's roadway, second by second (uw_station.py)
        "uw": {"block": sim.uw.block if sim.uw else None, "vehicles": sim.uw.plans() if sim.uw else [],
               "cars": sim.uw.cars() if sim.uw else [], "car_demand": car_demand,
               "errors": sim.uw.errors.used if sim.uw else [],
               "metrics": sim.uw.metrics() if sim.uw else None},
        # GRT garages: pull-outs and returns, inventories (garage_ops.py)
        "garages": garages.plans(),
    }


def simulation_result(scenario, network, baseline, smart):
    impact = {
        "average_wait_reduction": round(
            baseline["metrics"]["average_wait"] - smart["metrics"]["average_wait"], 2
        ),
        "max_queue_reduction": (
            baseline["metrics"]["max_queue"] - smart["metrics"]["max_queue"]
        ),
        "additional_completed_trips": smart["metrics"]["completed_trips"] - baseline["metrics"]["completed_trips"],
        "fewer_left_waiting": (
            baseline["metrics"]["left_waiting"] - smart["metrics"]["left_waiting"]
        ),
        "transfer_wait_reduction": round(
            baseline["metrics"]["average_transfer_wait"] - smart["metrics"]["average_transfer_wait"], 2
        ),
    }

    return {
        "scenario": scenario,
        "network": network,
        # The map plays the adaptive run; each case also carries its own ION run.
        "ion": smart["ion"],
        "baseline": baseline,
        "smart": smart,
        "impact": impact,
    }
