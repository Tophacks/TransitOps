// University of Waterloo Station: replays the buses the Python simulator
// moved on Transit Plaza Road (backend/uw_station.py), second by second.
// Positions come only from the simulator's traces; the browser interpolates
// between its one-second samples and draws them.

import geoData from "../geo/uw-station.json";
import { bearing, type LonLat } from "./path.ts";
import type { Corridor } from "./corridor.ts";

type PathPoint = [number, number, number];
export type UwBerth = { id: string; stop: string; position: number; front: number; rear: number };
export const UW = geoData as unknown as {
  path: PathPoint[];
  length_m: number;
  lanes: { through_offset_m: number; berth_offset_m: number; lane_width_m: number };
  bus: { length_m: number; width_m: number };
  platforms: { ref: string; side: "se" | "nw"; s0: number; s1: number; berths: UwBerth[] }[];
  features: { level_crossing: number[]; mid_stop: number; exit: number };
};

export type UwVehicle = {
  id: string;
  kind: "corridor" | "grt" | "oos";
  route: string;
  label: string;
  side: "se" | "nw";
  t_enter: number;
  /** [t, s along the roadway (front of the bus), signed offset (+ right of SW→NE)] */
  trace: [number, number, number][];
  exit_s: number | null;
  berths: [string, number, number | null][];
};

export type UwPose = { id: string; kind: UwVehicle["kind"]; label: string; centre: LonLat; heading: number; speed: number };

const M_LAT = 111200;
const mLon = (lat: number) => 111320 * Math.cos((lat * Math.PI) / 180);

/** Point at distance s along the roadway, moved `offset` metres to the right of SW→NE. */
export function roadPoint(s: number, offset: number): { point: LonLat; heading: number } {
  const path = UW.path;
  const x = Math.max(0, Math.min(UW.length_m, s));
  let i = 0;
  while (i < path.length - 2 && path[i + 1][2] < x) i += 1;
  const a = path[i];
  const b = path[i + 1];
  const f = b[2] === a[2] ? 0 : (x - a[2]) / (b[2] - a[2]);
  const lon = a[0] + (b[0] - a[0]) * f;
  const lat = a[1] + (b[1] - a[1]) * f;
  const dx = (b[0] - a[0]) * mLon(lat);
  const dy = (b[1] - a[1]) * M_LAT;
  const n = Math.hypot(dx, dy) || 1;
  const rx = dy / n;
  const ry = -dx / n;
  return { point: [lon + (rx * offset) / mLon(lat), lat + (ry * offset) / M_LAT], heading: bearing([a[0], a[1]], [b[0], b[1]]) };
}

function sample(trace: UwVehicle["trace"], t: number) {
  if (!trace.length || t < trace[0][0] || t > trace[trace.length - 1][0] + 1) return null;
  let lo = 0;
  let hi = trace.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (trace[mid][0] <= t) lo = mid; else hi = mid;
  }
  const a = trace[lo];
  const b = trace[Math.min(hi, trace.length - 1)];
  if (t >= b[0]) return { s: b[1], offset: b[2], speed: 0 };
  const f = b[0] === a[0] ? 0 : (t - a[0]) / (b[0] - a[0]);
  return { s: a[1] + (b[1] - a[1]) * f, offset: a[2] + (b[2] - a[2]) * f, speed: Math.abs(b[1] - a[1]) / Math.max(1e-6, b[0] - a[0]) };
}

/** Every vehicle on the roadway at t, as the simulator placed it. */
export function uwPoses(vehicles: readonly UwVehicle[], t: number): UwPose[] {
  const out: UwPose[] = [];
  for (const v of vehicles) {
    const at = sample(v.trace, t);
    if (!at) continue;
    const dir = v.side === "se" ? 1 : -1;
    const centre = at.s - (dir * UW.bus.length_m) / 2;
    const { point, heading } = roadPoint(centre, at.offset);
    out.push({ id: v.id, kind: v.kind, label: v.label, centre: point, heading: v.side === "se" ? heading : (heading + 180) % 360, speed: at.speed });
  }
  return out;
}

/** Is a corridor bus inside the station (from its entry time until it turns onto Phillip St)? */
export function inStation(vehicles: readonly UwVehicle[], id: string, t: number) {
  return vehicles.some((v) => v.kind === "corridor" && v.id === id && t >= v.t_enter && (v.exit_s === null || t <= v.exit_s + 1));
}

/** Berths and who holds them at t: free, a bus, or blocked (scenario). */
export function berthStates(vehicles: readonly UwVehicle[], t: number) {
  const held = new Map<string, { by: string; kind: UwVehicle["kind"] }>();
  for (const v of vehicles) {
    for (const [berth, from, to] of v.berths) {
      if (t >= from && (to === null || t < to)) held.set(berth, { by: v.id, kind: v.kind });
    }
  }
  return held;
}

const M = (a: LonLat, b: LonLat) => Math.hypot((a[0] - b[0]) * mLon(a[1]), (a[1] - b[1]) * M_LAT);

/** Corridor segments touching UW Station end at its entrance (Ring Road) and
 * start at its exit (Phillip Street): inside, the station replay draws the bus. */
export function stationClamp(corridor: Corridor) {
  const cache = new Map<string, [number, number] | null>();
  const nearest = (from: string, to: string, target: LonLat, fromEnd: boolean) => {
    const segment = corridor.segment(from, to);
    const length = segment.length_m;
    let best = fromEnd ? length : 0;
    let bestD = Infinity;
    const lo = fromEnd ? Math.max(0, length - 500) : 0;
    const hi = fromEnd ? length : Math.min(length, 500);
    for (let d = lo; d <= hi; d += 0.5) {
      const p = corridor.point(segment, d);
      const dist = M(p, target);
      if (dist < bestD) { bestD = dist; best = d; }
    }
    return { best, length };
  };
  return (from: string, to: string): [number, number] | null => {
    const key = `${from}>${to}`;
    if (!cache.has(key)) {
      let limits: [number, number] | null = null;
      if (to === "UW") {
        const { best } = nearest(from, to, [UW.path[0][0], UW.path[0][1]], true);
        limits = [0, best];
      } else if (from === "UW") {
        const end = UW.path[UW.path.length - 1];
        const { best, length } = nearest(from, to, [end[0], end[1]], false);
        limits = [best, length];
      }
      cache.set(key, limits);
    }
    return cache.get(key) ?? null;
  };
}

/** Is a point on the bus-only station roadway (Transit Plaza Road: access=no, bus=yes)? */
export function onStationRoad(p: LonLat, margin = 9) {
  const path = UW.path;
  for (let i = 0; i + 1 < path.length; i += 1) {
    const a: LonLat = [path[i][0], path[i][1]];
    const b: LonLat = [path[i + 1][0], path[i + 1][1]];
    const k = mLon(a[1]);
    const ax = 0, ay = 0;
    const bx = (b[0] - a[0]) * k, by = (b[1] - a[1]) * M_LAT;
    const px = (p[0] - a[0]) * k, py = (p[1] - a[1]) * M_LAT;
    const len = bx * bx + by * by;
    const f = len ? Math.max(0, Math.min(1, ((px - ax) * bx + (py - ay) * by) / len)) : 0;
    if (Math.hypot(px - bx * f, py - by * f) < margin) return true;
  }
  return false;
}
