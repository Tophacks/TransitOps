// GRT static GTFS, loaded server-side for the live feed: route names, stops,
// trips and the route shapes live vehicles are snapped to.
//
// Contains information provided by the Regional Municipality of Waterloo under
// the Region's Open Data Licence.

import { parse } from "csv-parse/sync";
import { unzipSync } from "fflate";

import type { GrtMode } from "../grtTypes.ts";
import {
  encodePath, projections, simplifyLonLat, stripSpurs, type LonLat, type PathPoint,
} from "../motion/path.ts";

export const GRT_API = "https://webapps.regionofwaterloo.ca/api/grt-routes/api";
export const GRT_FEEDS = { bus: 1, ion: 2 } as const;
/** Shapes are simplified to this tolerance (metres) before they are served. */
export const SHAPE_TOLERANCE_M = 1.0;

export type Trip = { route_id: string; trip_headsign: string; shape_id: string };

export type Schedule = {
  routes: Map<string, string>;
  stops: Map<string, string>;
  stopSequences: Map<string, string[]>;
  trips: Map<string, Trip>;
  /** shape id -> [lon, lat, cumulative metres] */
  shapes: Map<string, PathPoint[]>;
  /** route id -> the shape ids its trips use */
  routeShapes: Map<string, string[]>;
};

const schedules = new Map<GrtMode, { expires: number; data: Promise<Schedule | null> }>();

export async function readFeed(path: string) {
  const response = await fetch(`${GRT_API}/${path}`, {
    signal: AbortSignal.timeout(8_000),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`GRT feed returned HTTP ${response.status}`);
  return new Uint8Array(await response.arrayBuffer());
}

function readShapes(text: Uint8Array | undefined, mode: GrtMode) {
  const raw = new Map<string, [number, LonLat][]>();
  if (!text) return new Map<string, PathPoint[]>();
  parse<Record<string, string>>(text, {
    columns: true,
    bom: true,
    skip_empty_lines: true,
    on_record(row) {
      const list = raw.get(row.shape_id) ?? [];
      list.push([Number(row.shape_pt_sequence), [Number(row.shape_pt_lon), Number(row.shape_pt_lat)]]);
      raw.set(row.shape_id, list);
      return null;
    },
  });
  const shapes = new Map<string, PathPoint[]>();
  for (const [id, list] of raw) {
    let points = list.sort((a, b) => a[0] - b[0]).map(([, p]) => p);
    // Rail shapes make short out-and-back spurs to touch each platform.
    if (mode === "ion") points = stripSpurs(points);
    if (points.length < 2) continue;
    shapes.set(id, encodePath(simplifyLonLat(points, SHAPE_TOLERANCE_M)));
  }
  return shapes;
}

export async function loadSchedule(mode: GrtMode): Promise<Schedule> {
  const names = ["routes.txt", "stops.txt", "trips.txt", "stop_times.txt", "shapes.txt"];
  const files = unzipSync(await readFeed(`staticfeeds/${GRT_FEEDS[mode]}`), {
    filter: ({ name }) => names.includes(name),
  });
  const rows = (filename: string) =>
    files[filename]
      ? parse<Record<string, string>>(files[filename], { columns: true, bom: true, skip_empty_lines: true })
      : [];
  const stopSequences = new Map<string, string[]>();
  if (files["stop_times.txt"]) {
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
  }
  const trips = new Map<string, Trip>(rows("trips.txt").map((row) => [row.trip_id, {
    route_id: row.route_id,
    trip_headsign: row.trip_headsign,
    shape_id: row.shape_id ?? "",
  }]));
  const routeShapes = new Map<string, string[]>();
  for (const trip of trips.values()) {
    if (!trip.shape_id) continue;
    const list = routeShapes.get(trip.route_id) ?? [];
    if (!list.includes(trip.shape_id)) list.push(trip.shape_id);
    routeShapes.set(trip.route_id, list);
  }
  return {
    stopSequences,
    trips,
    routeShapes,
    shapes: readShapes(files["shapes.txt"], mode),
    routes: new Map(rows("routes.txt").map((row) => [row.route_id, row.route_short_name || row.route_long_name])),
    stops: new Map(rows("stops.txt").map((row) => [row.stop_id, row.stop_name])),
  };
}

export function getSchedule(mode: GrtMode) {
  const cached = schedules.get(mode);
  if (cached && cached.expires > Date.now()) return cached.data;

  const data = loadSchedule(mode).catch(() => {
    schedules.set(mode, { expires: Date.now() + 60_000, data: Promise.resolve(null) });
    return null;
  });
  schedules.set(mode, { expires: Date.now() + 3_600_000, data });
  return data;
}

export type ShapeMatch = { shapeId: string; match: "trip" | "route" } | null;

/**
 * The route shape a live vehicle is following. The trip id names it directly;
 * without a known trip, take the shape of the vehicle's route that passes
 * closest to the GPS fix.
 */
export function matchShape(schedule: Schedule | null, tripId: string, routeId: string | undefined, p: LonLat): ShapeMatch {
  if (!schedule) return null;
  const trip = schedule.trips.get(tripId);
  if (trip?.shape_id && schedule.shapes.has(trip.shape_id)) return { shapeId: trip.shape_id, match: "trip" };
  const candidates = schedule.routeShapes.get(routeId ?? trip?.route_id ?? "") ?? [];
  let best: ShapeMatch = null;
  let bestOffset = Infinity;
  for (const id of candidates) {
    const path = schedule.shapes.get(id);
    if (!path) continue;
    const offset = projections(path, p)[0]?.offset ?? Infinity;
    if (offset < bestOffset) {
      bestOffset = offset;
      best = { shapeId: id, match: "route" };
    }
  }
  return best;
}

/** How far through its stop list the trip is (0..1), from the feed's stop sequence. */
export function tripProgress(schedule: Schedule | null, tripId: string, sequence: number | undefined) {
  if (sequence == null || !schedule) return null;
  const stops = schedule.stopSequences.get(tripId);
  if (!stops) return null;
  const order = Object.keys(stops).map(Number).sort((a, b) => a - b);
  const index = order.indexOf(sequence);
  if (index < 0 || order.length < 2) return null;
  return index / (order.length - 1);
}
