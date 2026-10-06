def vehicle_snapshot(traci_connection):
    vehicles = []

    for vehicle_id in traci_connection.vehicle.getIDList():
        x, y = traci_connection.vehicle.getPosition(vehicle_id)

        try:
            lon, lat = traci_connection.simulation.convertGeo(x, y)
        except Exception:
            lon, lat = None, None

        vehicles.append(
            {
                "id": vehicle_id,
                "type": traci_connection.vehicle.getTypeID(vehicle_id),
                "speed_mps": round(traci_connection.vehicle.getSpeed(vehicle_id), 2),
                "road_id": traci_connection.vehicle.getRoadID(vehicle_id),
                "lane_id": traci_connection.vehicle.getLaneID(vehicle_id),
                "x": round(x, 2),
                "y": round(y, 2),
                "lon": round(lon, 6) if lon is not None else None,
                "lat": round(lat, 6) if lat is not None else None,
            }
        )

    return vehicles


def signal_snapshot(traci_connection):
    signals = []

    for signal_id in traci_connection.trafficlight.getIDList():
        signals.append(
            {
                "id": signal_id,
                "phase": traci_connection.trafficlight.getPhase(signal_id),
                "state": traci_connection.trafficlight.getRedYellowGreenState(signal_id),
            }
        )

    return signals


def network_snapshot(traci_connection, step):
    return {
        "step": step,
        "sim_time": traci_connection.simulation.getTime(),
        "vehicles": vehicle_snapshot(traci_connection),
        "signals": signal_snapshot(traci_connection),
        "arrived": list(traci_connection.simulation.getArrivedIDList()),
        "departed": list(traci_connection.simulation.getDepartedIDList()),
    }
