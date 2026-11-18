import GtfsRealtimeBindings from "gtfs-realtime-bindings";

import type { GrtFleet, GrtMode, GrtVehicle } from "../../../lib/grtTypes";
import { GRT_FEEDS, getSchedule, matchShape, readFeed, tripProgress } from "../../../lib/grt/schedule.ts";

const MAX_POSITION_AGE = 120;
const { FeedMessage, VehiclePosition } = GtfsRealtimeBindings.transit_realtime;

type FleetPart = { status: GrtFleet["feeds"][GrtMode]; vehicles: GrtVehicle[] };

async function loadFleet(mode: GrtMode): Promise<FleetPart> {
  try {
    const bytes = await readFeed(`vehiclepositions/${GRT_FEEDS[mode]}`);
    const feed = FeedMessage.decode(bytes);
    const now = Date.now() / 1000;
    const published = Number(feed.header.timestamp ?? 0);
    const fresh = (timestamp: number) =>
      timestamp >= now - MAX_POSITION_AGE && timestamp <= now + 30;

    if (!fresh(published)) return { status: "stale", vehicles: [] };
    const entries = feed.entity.filter((entity) => !entity.isDeleted && entity.vehicle?.position);
    if (!entries.length) return { status: "current", vehicles: [] };

    const schedule = await getSchedule(mode);
    const vehicles = new Map<string, GrtVehicle>();
    for (const entity of entries) {
      const vehicle = entity.vehicle;
      const position = vehicle?.position;
      if (!vehicle || !position) continue;
      const { latitude: lat, longitude: lon } = position;
      if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) continue;
      // The protobuf decoder represents an omitted vehicle timestamp as zero.
      const timestamp = Number(vehicle.timestamp) || published;
      if (!fresh(timestamp)) continue;

      const id = vehicle.vehicle?.id || entity.id;
      const tripId = vehicle.trip?.tripId ?? "";
      const trip = schedule?.trips.get(tripId);
      const sequence = Object.hasOwn(vehicle, "currentStopSequence") ? vehicle.currentStopSequence : undefined;
      const stopId = vehicle.stopId || (sequence == null
        ? undefined
        : schedule?.stopSequences.get(tripId)?.[sequence]);
      const routeId = vehicle.trip?.routeId || trip?.route_id;
      const shape = matchShape(schedule, tripId, routeId ?? undefined, [lon, lat]);
      const status = VehiclePosition.VehicleStopStatus;
      vehicles.set(id, {
        id,
        mode,
        lon,
        lat,
        route: routeId ? schedule?.routes.get(routeId) || routeId : null,
        headsign: trip?.trip_headsign || null,
        stop: schedule?.stops.get(stopId ?? "") || null,
        stopStatus: vehicle.currentStatus === status.STOPPED_AT ? "At stop"
          : vehicle.currentStatus === status.INCOMING_AT ? "Approaching" : "Next stop",
        tripId: tripId || null,
        shapeId: shape?.shapeId ?? null,
        shapeMatch: shape?.match ?? null,
        progress: tripProgress(schedule, tripId, sequence ?? undefined),
        timestamp,
      });
    }
    return { status: vehicles.size ? "current" : "stale", vehicles: [...vehicles.values()] };
  } catch {
    return { status: "unavailable", vehicles: [] };
  }
}

let cachedFleet: { expires: number; data: Promise<GrtFleet> } | undefined;

export async function GET() {
  if (!cachedFleet || cachedFleet.expires <= Date.now()) {
    const data = Promise.all([loadFleet("bus"), loadFleet("ion")]).then(([bus, ion]): GrtFleet => ({
      feeds: { bus: bus.status, ion: ion.status },
      vehicles: [...bus.vehicles, ...ion.vehicles],
    }));
    cachedFleet = { expires: Date.now() + 15_000, data };
  }
  const fleet = await cachedFleet.data;
  const available = Object.values(fleet.feeds).includes("current");
  return Response.json(fleet, {
    headers: { "Cache-Control": available ? "public, max-age=0, s-maxage=15" : "no-store" },
  });
}
