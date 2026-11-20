// GRT bus garages: replays the pull-outs and returns the Python simulator
// planned (backend/garage_ops.py) on the roads and trip shapes in
// bus-garages.json. Positions come from the simulator's (time, distance)
// samples; the browser only interpolates them along the path.

import geoData from "../geo/bus-garages.json";
import { bearing, lerpPath, type LonLat } from "./path.ts";

type PathPoint = [number, number, number];
type Move = {
  block: string; route: string; headsign: string; note: string;
  service_stop: { stop: string; name: string; lat: number; lon: number; arrive_s: number; depart_s: number };
  deadhead: { path: PathPoint[]; length_m: number; time_s: number; via: string[]; driveway: number | null };
  trip: { shape: PathPoint[]; stop_m: number[] };
};
export type Garage = {
  id: string; name: string; address: string; site_osm_way: number; outline: PathPoint[]; park: LonLat; inventory: number;
  driveways: { node: number; lon: number; lat: number; road: string; exits: string[]; entries: string[] }[];
  out: Move; in: Move;
};
export const GARAGES = (geoData as unknown as { garages: Garage[] }).garages;

export type GarageSegment = {
  kind: "garage" | "deadhead" | "layover" | "service";
  ref?: string; t0: number | null; t1: number | null; samples?: [number, number][]; at?: LonLat;
};
export type GarageVehicle = {
  id: string; garage: string; kind: "out" | "in"; block: string; route: string;
  segments: GarageSegment[]; states: { t: number; state: string }[];
};
export type GaragePose = { id: string; garage: string; state: string; centre: LonLat; heading: number };

function path(ref: string): PathPoint[] | null {
  const [gid, kind, part] = ref.split(":");
  const g = GARAGES.find((x) => x.id === gid);
  if (!g) return null;
  const move = g[kind as "out" | "in"];
  return part === "trip" ? move.trip.shape : move.deadhead.path;
}

function sAt(samples: [number, number][], t: number) {
  if (t <= samples[0][0]) return samples[0][1];
  for (let i = 0; i + 1 < samples.length; i += 1) {
    const [t0, s0] = samples[i];
    const [t1, s1] = samples[i + 1];
    if (t <= t1) return t1 > t0 ? s0 + ((s1 - s0) * (t - t0)) / (t1 - t0) : s1;
  }
  return samples[samples.length - 1][1];
}

/** Every garage bus on the road or in its garage at t. */
export function garagePoses(vehicles: readonly GarageVehicle[], t: number): GaragePose[] {
  const out: GaragePose[] = [];
  for (const v of vehicles) {
    const seg = v.segments.find((s) => (s.t0 === null || t >= s.t0) && (s.t1 === null || t < s.t1));
    if (!seg) continue;
    const state = v.states.filter((s) => s.t <= t).at(-1)?.state ?? v.states[0]?.state ?? "";
    if (seg.kind === "garage" && seg.at) {
      out.push({ id: v.id, garage: v.garage, state, centre: seg.at, heading: 0 });
      continue;
    }
    const p = seg.ref ? path(seg.ref) : null;
    if (!p || !seg.samples?.length) continue;
    const s = sAt(seg.samples, t);
    const ahead = lerpPath(p, Math.min(p[p.length - 1][2], s + 2));
    const behind = lerpPath(p, Math.max(0, s - 8));
    const centre = lerpPath(p, Math.max(0, s - 6));
    out.push({ id: v.id, garage: v.garage, state, centre, heading: bearing(behind, ahead) });
  }
  return out;
}
