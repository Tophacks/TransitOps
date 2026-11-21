// Scheduled GRT lines and the regional fleet: replays what the Python simulator
// drove (backend/lines.py, backend/line_driver.py, backend/fleet.py). Each line
// bus moves along its GTFS route variant from the simulator's trace [t, s, v]
// (constant acceleration between samples, exactly as the backend simplified it);
// it moves to the curb at the stops it served. A spare moves along its
// out-of-service road route by the times the simulator gave each point.

import lineData from "../geo/lines.json" with { type: "json" };
import { bearing, lerpPath, type LonLat } from "./path.ts";

type PathPoint = [number, number, number];
export type LinePattern = {
  id: string; route: string; direction: number; headsign: string; shape_id: string;
  stops: { stop: string; s: number }[]; length_m: number; path: PathPoint[]; trips: number;
};
export type LineStop = { name: string; lon: number; lat: number; ion?: { station: string; walk_m: number }; corridor?: string };
type LineData = {
  date: string;
  routes: Record<string, { id: string; short_name: string; name: string; patterns: LinePattern[] }>;
  stops: Record<string, LineStop>;
  blocks: Record<string, { trip_id: string; route: string; modelled: boolean; from: string; to: string; start: number; end: number }[]>;
};
export const LINES = lineData as unknown as LineData;
export const PATTERNS: Record<string, LinePattern> = Object.fromEntries(
  Object.values(LINES.routes).flatMap((r) => r.patterns.map((p) => [p.id, p])),
);
export const HOUR0 = 7 * 3600 + 30 * 60;

export type Deadhead = { key: string; t0: number; t1: number; path: [number, number, number, number][] };
export type LineSegment = [number, number, string, number, number, "run" | "dwell" | "wait" | "layover" | "deadhead" | "broken"];
export type LineStopLog = {
  trip: string; stop: string; sched: number; arr: number; dep: number; on: number; off: number; passed: boolean;
  missed?: boolean; handover?: string; released?: boolean;
};
export type LineVehicle = {
  vehicle_id: string; block: string; driver: string; operator: string; segments?: LineSegment[]; stops: LineStopLog[];
  /** the motion the simulator drove: [t, metres along the route variant, speed m/s] */
  trace: [number, number, number][];
  /** [t0, t1 (null: still running), trip, pattern, headsign, route] */
  trips: [number, number | null, string, string, string, string][];
  unmodelled: { trip_id: string; route: string; start_s: number; late_s: number }[];
  deadhead: Deadhead | null; replaces: string | null; broken: boolean;
};
export type LinePose = {
  id: string; centre: LonLat; heading: number; state: string; route: string; pattern: string;
  speed: number; s: number; headsign: string; label: string;
};
export type ZoneCar = { id: string; trace: [number, number][] };
export type LineZone = {
  id: string; kind: "curbside" | "bay"; stop: string; stop_name: string; road: string; lane: PathPoint[];
  merge_rear_m: number; demand: string; pattern: string; hold_s: number | null; pull_in_s: number | null; offset_m: number;
  cars: ZoneCar[];
};

export type FleetVehicle = {
  id: string; kind: "conventional" | "mobilityplus" | "ion" | "scenario"; garage: string | null; role: string | null;
  block: string | null; block_start_s: number | null; driver: string | null; simulated: boolean;
  history: [number | null, string, string][]; deadhead: Deadhead | null;
};
export type FleetCounts = Record<string, { allocation: number; in_garage: number; out: number; states: Record<string, number> }>;
export type FleetData = {
  summary: {
    conventional: number; mobilityplus: number; ion: number; allocation: Record<string, number>;
    roles: Record<string, number>; shortfall_blocks: string[]; blocks_today: number;
    counts_start: FleetCounts; counts_end: FleetCounts; modelled: Record<string, unknown>;
  };
  vehicles: FleetVehicle[];
  events: [number, string | null, string, string, string][];
};
export type LineMetrics = Record<string, number>;
export type LinesResult = {
  network: { routes: Record<string, { name: string; patterns: { id: string; direction: number; headsign: string; trips: number }[] }> };
  vehicles: LineVehicle[];
  metrics: LineMetrics;
  block: { stop: string; from_s: number; to_s: number } | null;
  zones?: LineZone[];
};

// As backend/line_driver.py: in lane, easing to the curb over CURB_BLEND_M at a stop.
const LANE_OFFSET_M = 1.9;
const CURB_OFFSET_M = 3.4;
const CURB_BLEND_M = 25;
const ease = (x: number) => { const k = Math.min(1, Math.max(0, x)); return k * k * (3 - 2 * k); };

/** Position and speed at t between two samples: constant acceleration landing on b. */
export function between(a: [number, number, number], b: [number, number, number], t: number) {
  const dt = b[0] - a[0];
  if (dt <= 0) return { s: b[1], v: b[2] };
  const tau = t - a[0];
  const acc = (2 * (b[1] - a[1] - a[2] * dt)) / (dt * dt);
  // Position lands exactly on b; speed changes linearly between the recorded speeds, as the
  // simulator's does between kept samples (line_driver.simplify keeps every change of rate).
  return { s: a[1] + a[2] * tau + 0.5 * acc * tau * tau, v: Math.max(0, a[2] + ((b[2] - a[2]) * tau) / dt) };
}

function offsetPose(path: PathPoint[], s: number, metres: number): { centre: LonLat; heading: number } {
  const { centre, heading } = poseOn(path, s);
  const h = (heading * Math.PI) / 180;
  const kLon = 111320 * Math.cos((centre[1] * Math.PI) / 180);
  // right of the direction of travel (heading clockwise from north)
  const east = Math.cos(h) * metres, north = -Math.sin(h) * metres;
  return { centre: [centre[0] + east / kLon, centre[1] + north / 111200], heading };
}


function poseOn(path: PathPoint[], s: number): { centre: LonLat; heading: number } {
  const end = path[path.length - 1][2];
  const ahead = lerpPath(path, Math.min(end, s + 2));
  const behind = lerpPath(path, Math.max(0, s - 8));
  return { centre: lerpPath(path, Math.max(0, s - 6)), heading: bearing(behind, ahead) };
}

/** Where a vehicle is along an out-of-service route at t (by the simulator's times). */
export function deadheadPose(d: Deadhead, t: number): { centre: LonLat; heading: number } | null {
  if (t < d.t0 || t > d.t1 || d.path.length < 2) return null;
  const rel = t - d.t0;
  const pts = d.path;
  const scale = (d.t1 - d.t0) / Math.max(1e-6, pts[pts.length - 1][3]);
  let i = 0;
  while (i + 1 < pts.length - 1 && pts[i + 1][3] * scale < rel) i += 1;
  const a = pts[i], b = pts[i + 1];
  const f = b[3] > a[3] ? Math.max(0, Math.min(1, (rel - a[3] * scale) / ((b[3] - a[3]) * scale))) : 1;
  const s = a[2] + (b[2] - a[2]) * f;
  const path: PathPoint[] = pts.map(([lon, lat, ss]) => [lon, lat, ss]);
  return poseOn(path, s);
}

// Samples describe the end of a simulation step. A sample stamped at a
// shared boundary therefore belongs to the outgoing trip, while the incoming
// trip owns the playback instant itself.
function afterTime(trace: LineVehicle["trace"], t: number) {
  let lo = 0, hi = trace.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (trace[mid][0] <= t) lo = mid + 1; else hi = mid;
  }
  return lo;
}

export function traceAt(trace: LineVehicle["trace"], t: number, start = -Infinity, end = Infinity) {
  const first = afterTime(trace, start), last = afterTime(trace, end) - 1;
  if (last < first || t > trace[last][0] + 0.5) return null;
  if (t < trace[first][0] - 1) {
    // Older encoders could compress a stationary relief across its handover.
    // Only hold the incoming sample when both sides confirm the same standstill.
    const prior = trace[first - 1], incoming = trace[first];
    if (t >= start && prior && prior[2] === 0 && incoming[2] === 0 && prior[1] === incoming[1]) {
      return { s: incoming[1], v: 0 };
    }
    return null;
  }
  const hi = Math.min(last, Math.max(first, afterTime(trace, t)));
  const lo = Math.max(first, hi - 1);
  const a = trace[lo], b = trace[hi];
  if (t <= a[0]) return { s: a[1], v: a[2] };
  if (t >= b[0]) return { s: b[1], v: b[2] };
  if (b[0] - a[0] > 1.5 && a[2] === 0 && b[2] === 0) return { s: a[1], v: 0 };      // standing
  if (b[0] - a[0] > 1.5 && (a[2] > 0 || b[2] > 0) && b[0] - a[0] > 40) return null; // off the network
  return between(a, b, t);
}

/** Every line bus on the modelled network at t, as the simulator drove it. */
export function linePoses(vehicles: readonly LineVehicle[], t: number, zones: readonly LineZone[] = []): LinePose[] {
  const out: LinePose[] = [];
  for (const v of vehicles) {
    const index = v.trips?.findLastIndex((x) => x[0] <= t) ?? -1;
    const trip = v.trips?.[index];
    const next = v.trips?.[index + 1];
    const inService = trip && (trip[1] === null || t < trip[1]);
    if (v.deadhead && !inService && (!trip || v.deadhead.t0 >= (trip[1] ?? Infinity))) {
      // The relief bus waits at the end of its road move until service begins.
      const p = deadheadPose(v.deadhead, Math.min(t, v.deadhead.t1));
      if (p && (next || t <= Math.max(v.deadhead.t1, v.trace?.at(-1)?.[0] ?? 0))) {
        out.push({ id: v.vehicle_id, ...p, state: "deadhead", route: "", pattern: "", speed: 0, s: 0,
          headsign: "Not in service", label: "Not in service" });
        continue;
      }
    }
    if (!trip) continue;
    const pattern = PATTERNS[trip[3]];
    if (!pattern) continue;
    const end = trip[1] ?? Infinity;
    // Older responses may leave a gap before opening the next same-stop trip.
    const waiting = t >= end && next && pattern.stops.at(-1)?.stop === PATTERNS[next[3]]?.stops[0].stop
      && !v.unmodelled?.some((row) => row.start_s >= end && row.start_s < next[0]);
    if (t >= end && !waiting) continue;
    let at = waiting ? { s: pattern.stops.at(-1)!.s, v: 0 } : traceAt(v.trace ?? [], t, trip[0], end);
    // A checkpoint can open the new trip before its first end-of-step sample.
    const previous = v.trips[index - 1];
    if (!at && t <= trip[0] + 1 && previous &&
        PATTERNS[previous[3]]?.stops.at(-1)?.stop === pattern.stops[0].stop) {
      at = { s: pattern.stops[0].s, v: 0 };
    }
    if (!at) continue;
    // To the curb at the stops it served on this trip; the bay roadway is its own lane.
    let lat = LANE_OFFSET_M;
    const bay = zones.find((z) => z.kind === "bay" && z.pattern === pattern.id);
    if (!(bay && bay.hold_s !== null && at.s < bay.hold_s + 1)) {
      for (const x of v.stops) {
        if (x.trip !== trip[2] || x.passed) continue;
        const st = pattern.stops.find((y) => y.stop === x.stop);
        if (!st) continue;
        const d = Math.abs(at.s - st.s);
        if (d < CURB_BLEND_M && t >= x.arr - 40 && t <= x.dep + 40) {
          lat = Math.max(lat, LANE_OFFSET_M + (CURB_OFFSET_M - LANE_OFFSET_M) * ease(1 - d / CURB_BLEND_M));
        }
      }
    }
    const pose = offsetPose(pattern.path, at.s, lat);
    // Opposite shapes share a terminal point but their headings and offsets
    // differ. Ease the drawn centre onto that common point at either end.
    const terminal = at.s < pattern.length_m / 2 ? pattern.stops[0] : pattern.stops.at(-1)!;
    const weight = 1 - ease(Math.abs(at.s - terminal.s) / CURB_BLEND_M);
    if (weight > 0) {
      const anchor = lerpPath(pattern.path, terminal.s);
      pose.centre = [pose.centre[0] + weight * (anchor[0] - pose.centre[0]),
                     pose.centre[1] + weight * (anchor[1] - pose.centre[1])];
    }
    // Road routes end at a snapped road point. The spare remains there
    // through handover, then joins its service shape as it drives away.
    if (v.deadhead && index === 0 && trip[0] >= v.deadhead.t1) {
      const stopId = v.deadhead.key?.split("|")[1]?.replace(/^stop:/, "");
      const relief = pattern.stops.find((st) => st.stop === stopId);
      if (relief && Math.abs(at.s - relief.s) < CURB_BLEND_M) {
        const arrival = deadheadPose(v.deadhead, v.deadhead.t1);
        if (arrival) {
          const f = ease(Math.abs(at.s - relief.s) / CURB_BLEND_M);
          pose.centre = [arrival.centre[0] + f * (pose.centre[0] - arrival.centre[0]),
                         arrival.centre[1] + f * (pose.centre[1] - arrival.centre[1])];
        }
      }
    }
    const state = at.v > 0.05 ? "moving" : "stopped";
    out.push({ id: v.vehicle_id, ...pose, state, route: trip[5], pattern: pattern.id, speed: at.v, s: at.s,
      headsign: trip[4], label: `${trip[5]} ${trip[4]}` });
  }
  return out;
}

/** Cars in the shared-traffic lanes on the lines, as the simulator moved them. */
export function zoneCarPoses(zones: readonly LineZone[], t: number) {
  const out: { id: string; centre: LonLat; heading: number; speed: number }[] = [];
  for (const z of zones) {
    for (const car of z.cars) {
      const tr = car.trace;
      if (!tr.length || t < tr[0][0] || t > tr[tr.length - 1][0]) continue;
      let i = 0;
      while (i + 1 < tr.length && tr[i + 1][0] < t) i += 1;
      const a = tr[i], b = tr[Math.min(tr.length - 1, i + 1)];
      const f = b[0] > a[0] ? (t - a[0]) / (b[0] - a[0]) : 0;
      const s = a[1] + (b[1] - a[1]) * f;
      const pose = offsetPose(z.lane, s - 2.3, LANE_OFFSET_M);
      out.push({ id: car.id, ...pose, speed: b[0] > a[0] ? (b[1] - a[1]) / (b[0] - a[0]) : 0 });
    }
  }
  return out;
}

/** Spares out of service from a garage that are not on a line (e.g. to the corridor). */
export function sparePoses(vehicles: readonly FleetVehicle[], t: number, skip: ReadonlySet<string>) {
  const out: { id: string; centre: LonLat; heading: number }[] = [];
  for (const v of vehicles) {
    if (!v.deadhead || skip.has(v.id)) continue;
    const p = deadheadPose(v.deadhead, t);
    if (p) out.push({ id: v.id, ...p });
  }
  return out;
}

/** A fleet vehicle's state at t (from its history). */
export function stateAt(v: FleetVehicle, t: number): [string, string] {
  let cur: [string, string] = ["", ""];
  for (const [at, state, detail] of v.history) {
    if (at === null || at <= t + 1e-6) cur = [state, detail];
    else break;
  }
  return cur;
}

/** Ready to go out as a spare at t: a spare, or held for a block that starts later than the
 * dispatch horizon (fleet.py SPARE_HORIZON_S, sent in the summary). */
export function isReadySpare(v: FleetVehicle, t: number, horizon: number) {
  const [state] = stateAt(v, t);
  return state === "ready spare" || (state === "held for its block" && v.block_start_s !== null && v.block_start_s - t > horizon);
}

export function clockAt(t: number) {
  const s = Math.round(HOUR0 + t);
  return `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
}
