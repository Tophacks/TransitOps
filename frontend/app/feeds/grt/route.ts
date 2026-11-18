import { parse } from "csv-parse/sync";
import { unzipSync } from "fflate";
import GtfsRealtimeBindings from "gtfs-realtime-bindings";

import type { GrtFleet, GrtMode, GrtVehicle } from "../../../lib/grtTypes";

const BASE = "https://webapps.regionofwaterloo.ca/api/grt-routes/api";
const FEEDS = { bus: 1, ion: 2 } as const;
const MAX_POSITION_AGE = 120;
const { FeedMessage, VehiclePosition } = GtfsRealtimeBindings.transit_realtime;

type Schedule = {
  routes: Map<string, string>;
  stops: Map<string, string>;
  stopSequences: Map<string, string[]>;
  trips: Map<string, { route_id: string; trip_headsign: string }>;
};
type FleetPart = { status: GrtFleet["feeds"][GrtMode]; vehicles: GrtVehicle[] };

const schedules = new Map<GrtMode, { expires: number; data: Promise<Schedule | null> }>();

async function readFeed(path: string) {
  const response = await fetch(`${BASE}/${path}`, {
    signal: AbortSignal.timeout(8_000),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`GRT feed returned HTTP ${response.status}`);
  return new Uint8Array(await response.arrayBuffer());
}

async function loadSchedule(mode: GrtMode): Promise<Schedule> {
  const files = unzipSync(await readFeed(`staticfeeds/${FEEDS[mode]}`), {
    filter: ({ name }) => ["routes.txt", "stops.txt", "trips.txt", "stop_times.txt"].includes(name),
  });
  const rows = (filename: string) =>
    parse<Record<string, string>>(files[filename], {
      columns: true,
      bom: true,
      skip_empty_lines: true,
    });
  const stopSequences = new Map<string, string[]>();
  parse<Record<string, string>>(files["stop_times.txt"], {
    columns: true,
    bom: true,
    skip_empty_lines: true,
    on_record(row) {
      const sequence = stopSequences.get(row.trip_id) ?? [];
      sequence[Number(row.stop_sequence)] = row.stop_id;
      stopSequences.set(row.trip_id, sequence);
      return null;
    },
  });
  return {
    stopSequences,
    routes: new Map(rows("routes.txt").map((row) => [row.route_id, row.route_short_name || row.route_long_name])),
    stops: new Map(rows("stops.txt").map((row) => [row.stop_id, row.stop_name])),
    trips: new Map(rows("trips.txt").map((row) => [row.trip_id, {
      route_id: row.route_id,
      trip_headsign: row.trip_headsign,
    }])),
  };
}

function getSchedule(mode: GrtMode) {
  const cached = schedules.get(mode);
  if (cached && cached.expires > Date.now()) return cached.data;

  const data = loadSchedule(mode).catch(() => {
    schedules.set(mode, { expires: Date.now() + 60_000, data: Promise.resolve(null) });
    return null;
  });
  schedules.set(mode, { expires: Date.now() + 3_600_000, data });
  return data;
}

async function loadFleet(mode: GrtMode): Promise<FleetPart> {
  try {
    const bytes = await readFeed(`vehiclepositions/${FEEDS[mode]}`);
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
      if (!fresh(Number(vehicle.timestamp) || published)) continue;

      const id = vehicle.vehicle?.id || entity.id;
      const tripId = vehicle.trip?.tripId ?? "";
      const trip = schedule?.trips.get(tripId);
      const sequence = Object.hasOwn(vehicle, "currentStopSequence") ? vehicle.currentStopSequence : undefined;
      const stopId = vehicle.stopId || (sequence == null
        ? undefined
        : schedule?.stopSequences.get(tripId)?.[sequence]);
      const routeId = vehicle.trip?.routeId || trip?.route_id;
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
