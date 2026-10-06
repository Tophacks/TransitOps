GEO_ANCHORS = [
    (0.0, -80.54128, 43.47336),
    (300.0, -80.52280, 43.46419),
    (600.0, -80.51185, 43.45723),
    (900.0, -80.49944, 43.45333),
]


def corridor_geo(x, y):
    clamped_y = max(0.0, min(900.0, float(y)))

    lower = GEO_ANCHORS[0]
    upper = GEO_ANCHORS[-1]
    for index in range(len(GEO_ANCHORS) - 1):
        a = GEO_ANCHORS[index]
        b = GEO_ANCHORS[index + 1]
        if a[0] <= clamped_y <= b[0]:
            lower, upper = a, b
            break

    span = max(1.0, upper[0] - lower[0])
    t = (clamped_y - lower[0]) / span
    lon = lower[1] + (upper[1] - lower[1]) * t
    lat = lower[2] + (upper[2] - lower[2]) * t

    # Synthetic east/west cross streets are projected around the corridor.
    lon += float(x) * 0.0000125
    return round(lon, 6), round(lat, 6)


def vehicle_snapshot(traci_connection):
    vehicles = []

    for vehicle_id in traci_connection.vehicle.getIDList():
        x, y = traci_connection.vehicle.getPosition(vehicle_id)
        lon, lat = corridor_geo(x, y)

        vehicles.append(
            {
                "id": vehicle_id,
                "type": traci_connection.vehicle.getTypeID(vehicle_id),
                "speed_mps": round(traci_connection.vehicle.getSpeed(vehicle_id), 2),
                "road_id": traci_connection.vehicle.getRoadID(vehicle_id),
                "lane_id": traci_connection.vehicle.getLaneID(vehicle_id),
                "x": round(x, 2),
                "y": round(y, 2),
                "lon": lon,
                "lat": lat,
            }
        )

    return vehicles


def signal_snapshot(traci_connection):
    signals = []

    signal_geo = {
        "tls1": corridor_geo(0, 150),
        "tls2": corridor_geo(0, 450),
        "tls3": corridor_geo(0, 750),
    }

    for signal_id in traci_connection.trafficlight.getIDList():
        lon, lat = signal_geo.get(signal_id, (None, None))
        signals.append(
            {
                "id": signal_id,
                "phase": traci_connection.trafficlight.getPhase(signal_id),
                "state": traci_connection.trafficlight.getRedYellowGreenState(signal_id),
                "lon": lon,
                "lat": lat,
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
