// Scheduled GRT lines and the regional fleet: replays what the Python simulator
// decided (backend/lines.py, backend/fleet.py). Each line bus moves along its
// GTFS route variant from the simulator's segments [t0, t1, pattern, s0, s1,
// kind]; a spare moves along its out-of-service road route by the times the
// simulator gave each point. The browser only interpolates.

import lineData from "../geo/lines.json";
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
  vehicle_id: string; block: string; driver: string; operator: string; segments: LineSegment[]; stops: LineStopLog[];
  unmodelled: { trip_id: string; route: string; start_s: number; late_s: number }[];
  deadhead: Deadhead | null; replaces: string | null; broken: boolean;
};
export type LinePose = { id: string; centre: LonLat; heading: number; state: string; route: string; pattern: string };

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
};

const ease = (f: number) => f * f * (3 - 2 * f);

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

/** Every line bus on the modelled network at t; off-network periods are not drawn. */
export function linePoses(vehicles: readonly LineVehicle[], t: number): LinePose[] {
  const out: LinePose[] = [];
  for (const v of vehicles) {
    if (v.deadhead) {
      const p = deadheadPose(v.deadhead, t);
      if (p) {
        out.push({ id: v.vehicle_id, ...p, state: "deadhead", route: "", pattern: "" });
        continue;
      }
    }
    const seg = v.segments.find((x) => t >= x[0] - 0.5 && t <= x[1] + 1.5 && x[5] !== "deadhead");
    if (!seg) continue;
    const [t0, t1, pid, s0, s1, kind] = seg;
    const pattern = PATTERNS[pid];
    if (!pattern) continue;
    const f = t1 > t0 ? Math.max(0, Math.min(1, (t - t0) / (t1 - t0))) : 1;
    const s = kind === "run" ? s0 + (s1 - s0) * ease(f) : s1;
    out.push({ id: v.vehicle_id, ...poseOn(pattern.path, s), state: kind, route: pattern.route, pattern: pid });
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
