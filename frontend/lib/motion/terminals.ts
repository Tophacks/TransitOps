// ION terminal layouts (tools/waterloo_geometry/build_terminals.py): the
// trackwork at Fairway and Conestoga and every move a train can make there.
// The simulator (backend/rail_ops.py) plans trains along these moves; the map
// draws the same paths and replays the plans with the same kinematics.

import { Profile } from "./profile.ts";
import type { PathPoint } from "./path.ts";

export type TerminalKey = "fairway" | "conestoga";

export type TerminalRoute = {
  kind: "arrive" | "depart";
  platform: string;
  via: { edge: string; reversed: boolean }[];
  crossover: boolean;
  path: PathPoint[];
  length_m: number;
  stop_m?: number;
  start_m?: number;
  home_signal_m?: number;
  switch_m: number;
  line: {
    direction: "north" | "south";
    from_station?: string;
    from_m?: number;
    to_station?: string;
    to_m?: number;
    handover_m: number;
    line_part_m?: number;
    line_start_m?: number;
  };
  resources: { id: string; s0: number; s1: number }[];
  curves: [number, number][];
  zones: [number, number, number][];
};

export type Terminal = {
  name: string;
  station: string;
  arrive_line: "north" | "south";
  depart_line: "north" | "south";
  preference: string[];
  edges: Record<string, { ways: number[]; from: number; to: number; maxspeed_mps: number | null; path: PathPoint[] }>;
  running: Record<"arrive" | "depart", { way: number; path: PathPoint[] }>;
  switches: { node: number; lon: number; lat: number; legs: string[] }[];
  diamond: { node: number; lon: number; lat: number };
  track_ends: { platform: string; node: number; lon: number; lat: number }[];
  platforms: Record<string, { edge: string }>;
  routes: Record<string, TerminalRoute>;
  conflicts: Record<string, string[]>;
};

export type TerminalsData = {
  attribution: string;
  train_length_m: number;
  terminals: Record<TerminalKey, Terminal>;
};

export const ION_ACCEL = 1.0;
export const ION_DECEL = 1.1;
export const ION_TOP_SPEED = 16.7;

/** Speed limit at metre x of a move starting at s0 (rail_ops.route_cap). */
export function routeCap(route: TerminalRoute, s0: number, top = ION_TOP_SPEED) {
  return (x: number) => {
    const s = s0 + x;
    let v = top;
    for (const [a, b, vz] of route.zones) if (a <= s && s <= b && vz < v) v = vz;
    for (const [c, vc] of route.curves) if (Math.abs(s - c) <= 0.5 && vc < v) v = vc;
    return v;
  };
}

const profiles = new Map<string, Profile>();

/** The profile of a move from s0 to s1 along a terminal route (rail_ops.move_profile). */
export function moveProfile(data: TerminalsData, terminal: string, route: string, s0: number, s1: number) {
  const key = `${terminal}|${route}|${s0.toFixed(3)}|${s1.toFixed(3)}`;
  let profile = profiles.get(key);
  if (!profile) {
    const r = data.terminals[terminal as TerminalKey].routes[route];
    profile = new Profile(s1 - s0, routeCap(r, s0), { v0: 0, stop: true, accel: ION_ACCEL, decel: ION_DECEL });
    profiles.set(key, profile);
  }
  return profile;
}

/** A position on a terminal route as a distance along the line (approximate in the trackwork). */
export function lineNose(route: TerminalRoute, s: number, lineLength: number) {
  const line = route.line;
  if (route.kind === "arrive") {
    if (s <= (line.line_part_m ?? 0)) return (line.from_m ?? 0) + s;
    return Math.min(lineLength, line.handover_m + (s - (line.line_part_m ?? 0)));
  }
  if (s >= (line.line_start_m ?? 0)) return line.handover_m + (s - (line.line_start_m ?? 0));
  return Math.max(0, line.handover_m - ((line.line_start_m ?? 0) - s));
}
