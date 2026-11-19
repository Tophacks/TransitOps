// Stylised 3D vehicle bodies, built as MapLibre fill-extrusion footprints.
//
// Bodies are extruded in world space, so they stay planted on the street when
// the camera pans, pitches or rotates, and they share the depth buffer with
// the basemap's extruded buildings, which can therefore hide them.

import { offsetLonLat, type LonLat } from "./path.ts";
import type { TrainPose } from "./ion.ts";
import type { CarPose } from "./traffic.ts";
import type { GateState } from "./gates.ts";

/** What a bus body needs: simulated (BusPose) and live poses both fit. */
export type BodyPose = { id: string; centre: LonLat; heading: number; state?: string };

/**
 * "sim": gold buses / teal-striped trains from the Python simulator.
 * "live": GRT's blue bus livery and blue-striped ION trains for real GPS.
 * "stale": a live vehicle whose last GPS fix is old.
 */
export type Livery = "sim" | "live" | "stale";

type Local = [number, number]; // x forward, y left (metres)
type Feature = GeoJSON.Feature<GeoJSON.Polygon, Record<string, string | number>>;

export const BUS_LENGTH = 12.2;
export const BUS_WIDTH = 2.55;
export const ION_WIDTH = 2.65;

const SUN: [number, number] = [0.85, -0.65]; // shadow offset east, north (m)

const COLORS = {
  busBody: "#f1bf4b",
  busBodyShade: "#d9a63a",
  chassis: "#1c2731",
  glass: "#152330",
  windshield: "#4f93b8",
  frontMask: "#f7f1e3",
  sign: "#ffe08a",
  roof: "#e7ecef",
  wheel: "#0c1116",
  head: "#fff4cf",
  tail: "#ff5f52",
  ionLower: "#26333c",
  ionStripe: "#36c9a6",
  ionUpper: "#edf3f1",
  ionRoofGear: "#97a5ab",
  ionPanto: "#55626a",
  bellows: "#151b21",
  ionSign: "#86f2cf",
};

const LIVERY = {
  sim: { body: COLORS.busBody, shade: COLORS.busBodyShade, sign: COLORS.sign, stripe: COLORS.ionStripe, ionSign: COLORS.ionSign },
  live: { body: "#2f7ee0", shade: "#235fb0", sign: "#ffb547", stripe: "#3f8cff", ionSign: "#a9cdff" },
  stale: { body: "#61758f", shade: "#4b5c72", sign: "#b9a57e", stripe: "#6d84a3", ionSign: "#b5c3d6" },
};

const STATE_BEACON: Record<string, string> = {
  EN_ROUTE: COLORS.roof,
  DWELLING: "#59e3a7",
  BROKEN_DOWN: "#ff6a55",
};

function place(centre: LonLat, heading: number, ring: Local[]): GeoJSON.Position[] {
  const h = (heading * Math.PI) / 180;
  const fx = Math.sin(h);
  const fy = Math.cos(h);
  const lx = -fy;
  const ly = fx;
  const out = ring.map(([x, y]) => offsetLonLat(centre, fx * x + lx * y, fy * x + ly * y) as GeoJSON.Position);
  out.push(out[0]);
  return out;
}

function box(x0: number, x1: number, y0: number, y1: number): Local[] {
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
}

/** Rectangle with the front corners chamfered (reads as the vehicle's nose). */
function nosed(x0: number, x1: number, half: number, chamfer: number, rearChamfer = 0.15): Local[] {
  return [
    [x0 + rearChamfer, -half], [x1 - chamfer, -half], [x1, -half + chamfer],
    [x1, half - chamfer], [x1 - chamfer, half], [x0 + rearChamfer, half],
    [x0, half - rearChamfer], [x0, -half + rearChamfer],
  ];
}

/** Section footprint whose cab end narrows into a rounded-looking nose. */
function cab(x0: number, x1: number, half: number, noseAtFront: boolean): Local[] {
  const taper = 1.35;
  const narrow = half * 0.68;
  if (noseAtFront) {
    return [[x0, -half], [x1 - taper, -half], [x1 - 0.35, -narrow - 0.18], [x1, -narrow * 0.55],
      [x1, narrow * 0.55], [x1 - 0.35, narrow + 0.18], [x1 - taper, half], [x0, half]];
  }
  return [[x1, half], [x0 + taper, half], [x0 + 0.35, narrow + 0.18], [x0, narrow * 0.55],
    [x0, -narrow * 0.55], [x0 + 0.35, -narrow - 0.18], [x0 + taper, -half], [x1, -half]];
}

function part(
  centre: LonLat, heading: number, ring: Local[], base: number, height: number, color: string, id: string,
  role = "body",
): Feature {
  return {
    type: "Feature",
    geometry: { type: "Polygon", coordinates: [place(centre, heading, ring)] },
    properties: { base, height, color, id, role },
  };
}

function shade(centre: LonLat, heading: number, ring: Local[], opacity: number, id: string): Feature {
  const shifted = offsetLonLat(centre, SUN[0], SUN[1]);
  return {
    type: "Feature",
    geometry: { type: "Polygon", coordinates: [place(shifted, heading, ring)] },
    properties: { opacity, id, role: "shadow", color: "#000000" },
  };
}

export type BodyLayers = { solids: Feature[]; ground: Feature[] };

/**
 * Night lighting (illustrative): lit windows, headlight beams and tail-light
 * glow. Set by the map before it builds the frame's bodies.
 */
export const lighting = { night: false };
const LIT_GLASS = "#f5c86a";
const LIT_WINDSHIELD = "#ffe7a8";
const BEAM = "#fff1c2";

function glass() {
  return lighting.night ? LIT_GLASS : COLORS.glass;
}

function windshield() {
  return lighting.night ? LIT_WINDSHIELD : COLORS.windshield;
}

/** Headlight beams on the road ahead and a red glow behind (night only). */
function lights(
  centre: LonLat, heading: number, front: number, rear: number, half: number, id: string, out: BodyLayers,
  ends: { beams?: boolean; glow?: boolean } = {},
) {
  if (!lighting.night) return;
  if (ends.beams === false) {
    glowBehind(centre, heading, rear, half, id, out);
    return;
  }
  const beam = (y: number): Local[] => [[front, y - 0.35], [front + 16, y - 2.6], [front + 16, y + 2.6], [front, y + 0.35]];
  for (const y of [half - 0.5, -(half - 0.5)]) {
    out.ground.push({
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [place(centre, heading, beam(y))] },
      properties: { opacity: 0.2, id, role: "beam", color: BEAM },
    });
  }
  if (ends.glow !== false) glowBehind(centre, heading, rear, half, id, out);
}

function glowBehind(centre: LonLat, heading: number, rear: number, half: number, id: string, out: BodyLayers) {
  out.ground.push({
    type: "Feature",
    geometry: { type: "Polygon", coordinates: [place(centre, heading, box(rear - 2.2, rear, -half, half))] },
    properties: { opacity: 0.22, id, role: "glow", color: "#ff3b30" },
  });
}

/**
 * Scale everything added since `from` by `k` (0..1): a vehicle joining or
 * leaving the live feed grows out of / sinks into the street.
 */
function fadeFrom(out: BodyLayers, solids: number, ground: number, k: number) {
  if (k >= 1) return;
  for (let i = solids; i < out.solids.length; i += 1) {
    const p = out.solids[i].properties;
    p.base = (p.base as number) * k;
    p.height = (p.height as number) * k;
  }
  for (let i = ground; i < out.ground.length; i += 1) {
    const p = out.ground[i].properties;
    p.opacity = (p.opacity as number) * k;
  }
}

export function busBody(pose: BodyPose, selected: boolean, out: BodyLayers, livery: Livery = "sim", fade = 1) {
  const solids = out.solids.length;
  const ground = out.ground.length;
  drawBus(pose, selected, out, LIVERY[livery], livery === "sim");
  fadeFrom(out, solids, ground, fade);
}

function drawBus(pose: BodyPose, selected: boolean, out: BodyLayers, colors: (typeof LIVERY)["sim"], beacon: boolean) {
  const { centre, heading, id } = pose;
  const L = BUS_LENGTH / 2;
  const W = BUS_WIDTH / 2;
  out.ground.push(shade(centre, heading, box(-L - 1.1, L + 1.1, -W - 1.1, W + 1.1), 0.16, id));
  out.ground.push(shade(centre, heading, box(-L - 0.4, L + 0.4, -W - 0.4, W + 0.4), 0.34, id));
  if (selected) {
    // Ground ring around the selected bus (outer box minus inner box).
    const outer = place(centre, heading, box(-L - 1.5, L + 1.5, -W - 1.5, W + 1.5));
    const inner = place(centre, heading, box(-L - 0.9, L + 0.9, -W - 0.9, W + 0.9)).reverse();
    out.ground.push({
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [outer, inner] },
      properties: { opacity: 0.85, id, role: "halo", color: "#7ee0ff" },
    });
  }
  const s = out.solids;
  for (const ax of [3.05, -3.05]) {
    for (const side of [1, -1]) {
      s.push(part(centre, heading, box(ax - 0.52, ax + 0.52, side * (W - 0.34), side * (W + 0.05)), 0, 0.95, COLORS.wheel, id));
    }
  }
  s.push(part(centre, heading, nosed(-L, L, W - 0.05, 0.42), 0.32, 0.95, COLORS.chassis, id));
  s.push(part(centre, heading, nosed(-L, L, W, 0.45), 0.95, 1.4, colors.shade, id));
  s.push(part(centre, heading, nosed(-L + 0.1, L - 0.55, W + 0.02, 0.3), 1.4, 2.45, glass(), id));
  // Front: cream mask with a light windshield, so the nose reads at a glance.
  s.push(part(centre, heading, nosed(L - 0.7, L, W + 0.01, 0.45, 0), 0.95, 3.02, COLORS.frontMask, id));
  s.push(part(centre, heading, box(L - 0.5, L + 0.03, -W + 0.3, W - 0.3), 1.15, 2.5, windshield(), id));
  lights(centre, heading, L, -L, W, id, out);
  s.push(part(centre, heading, nosed(-L, L, W, 0.45), 2.45, 3.02, colors.body, id));
  s.push(part(centre, heading, box(L - 0.5, L + 0.02, -W + 0.45, W - 0.45), 2.62, 2.92, colors.sign, id));
  const roof = beacon ? STATE_BEACON[pose.state ?? ""] ?? COLORS.roof : COLORS.roof;
  s.push(part(centre, heading, box(-2.6, 1.6, -W + 0.45, W - 0.45), 3.02, 3.34, roof, id));
  for (const side of [1, -1]) {
    s.push(part(centre, heading, box(L - 0.1, L + 0.06, side * (W - 0.75), side * (W - 0.25)), 0.55, 0.82, COLORS.head, id));
    s.push(part(centre, heading, box(-L - 0.06, -L + 0.1, side * (W - 0.75), side * (W - 0.25)), 0.6, 0.9, COLORS.tail, id));
  }
}

export function trainBody(pose: TrainPose, out: BodyLayers, livery: Livery = "sim", fade = 1) {
  const solids = out.solids.length;
  const ground = out.ground.length;
  drawTrain(pose, out, LIVERY[livery]);
  fadeFrom(out, solids, ground, fade);
}

function drawTrain(pose: TrainPose, out: BodyLayers, colors: (typeof LIVERY)["sim"]) {
  const W = ION_WIDTH / 2;
  const count = pose.sections.length;
  pose.sections.forEach((section, index) => {
    const { centre, heading } = section;
    const L = section.length / 2;
    const id = pose.id;
    const first = index === 0;
    const lastSection = index === count - 1;
    const outline = first ? cab(-L, L, W, true) : lastSection ? cab(-L, L, W, false) : box(-L, L, -W, W);
    out.ground.push(shade(centre, heading, box(-L - 0.9, L + 0.9, -W - 0.9, W + 0.9), 0.15, id));
    out.ground.push(shade(centre, heading, box(-L - 0.3, L + 0.3, -W - 0.3, W + 0.3), 0.3, id));
    const s = out.solids;
    s.push(part(centre, heading, outline, 0.32, 1.08, COLORS.ionLower, id));
    s.push(part(centre, heading, outline, 1.08, 1.28, colors.stripe, id));
    s.push(part(centre, heading, outline, 1.28, 2.42, glass(), id));
    s.push(part(centre, heading, outline, 2.42, 3.28, COLORS.ionUpper, id));
    if (index === 1 || index === 3) {
      s.push(part(centre, heading, box(-L + 1.2, L - 1.2, -W + 0.5, W - 0.5), 3.28, 3.62, COLORS.ionRoofGear, id));
    }
    if (index === 2) {
      s.push(part(centre, heading, box(-0.9, 0.9, -W + 0.85, W - 0.85), 3.28, 3.95, COLORS.ionPanto, id));
    }
    const bidirectional = pose.headlights !== undefined;
    if (!bidirectional) {
      if (first) {
        s.push(part(centre, heading, box(L - 0.75, L - 0.05, -W * 0.5, W * 0.5), 1.25, 2.62, windshield(), id));
        lights(centre, heading, L, -L, W, id, out);
        s.push(part(centre, heading, box(L - 0.6, L + 0.02, -W * 0.38, W * 0.38), 2.62, 2.9, colors.ionSign, id));
        for (const side of [1, -1]) {
          s.push(part(centre, heading, box(L - 0.45, L + 0.05, side * W * 0.22, side * W * 0.5), 0.6, 0.85, COLORS.head, id));
        }
      }
      if (lastSection) {
        for (const side of [1, -1]) {
          s.push(part(centre, heading, box(-L - 0.05, -L + 0.45, side * W * 0.22, side * W * 0.5), 0.6, 0.85, COLORS.tail, id));
        }
      }
    } else if (first || lastSection) {
      // A cab at each end. In the cab's own frame its outer end is +x.
      const cabHeading = first ? heading : (heading + 180) % 360;
      const leading = pose.headlights === index;
      s.push(part(centre, cabHeading, box(L - 0.75, L - 0.05, -W * 0.5, W * 0.5), 1.25, 2.62, windshield(), id));
      // Beams ahead of the leading cab; a red glow off the trailing cab's end.
      if (leading) lights(centre, cabHeading, L, -L, W, id, out, { glow: false });
      else lights(centre, (cabHeading + 180) % 360, L, -L, W, id, out, { beams: false });
      if (leading) {
        s.push(part(centre, cabHeading, box(L - 0.6, L + 0.02, -W * 0.38, W * 0.38), 2.62, 2.9, colors.ionSign, id, "sign"));
      }
      for (const side of [1, -1]) {
        s.push(part(centre, cabHeading, box(L - 0.45, L + 0.05, side * W * 0.22, side * W * 0.5), 0.6, 0.85,
          leading ? COLORS.head : COLORS.tail, id, leading ? "headlight" : "taillight"));
      }
    }
    // Bellows: connect this section's rear face to the next section's front.
    const next = pose.sections[index + 1];
    if (next) {
      const inset = W - 0.2;
      const rearCorners = place(centre, heading, [[-L, inset], [-L, -inset]]).slice(0, 2);
      const nextL = next.length / 2;
      const frontCorners = place(next.centre, next.heading, [[nextL, -inset], [nextL, inset]]).slice(0, 2);
      const ring = [rearCorners[0], rearCorners[1], frontCorners[0], frontCorners[1], rearCorners[0]];
      s.push({
        type: "Feature",
        geometry: { type: "Polygon", coordinates: [ring] },
        properties: { base: 0.45, height: 3.05, color: COLORS.bellows, id, role: "joint" },
      });
    }
  });
}

// Car colours: a muted mix so traffic reads as background, not as transit.
const CAR_PAINT = ["#9aa4ad", "#5d6873", "#c9cdd1", "#3d4651", "#7b3a3a", "#2f4a63", "#b8b0a1"];

function hash(id: string) {
  let h = 0;
  for (let i = 0; i < id.length; i += 1) h = (h * 31 + id.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** A simulated car: body, cabin, lights. Small enough to read as traffic. */
export function carBody(pose: CarPose, out: BodyLayers, length: number, width: number) {
  const solids = out.solids.length;
  const ground = out.ground.length;
  const { centre, heading, id } = pose;
  const L = length / 2;
  const W = width / 2;
  const paint = CAR_PAINT[hash(id) % CAR_PAINT.length];
  out.ground.push(shade(centre, heading, box(-L - 0.3, L + 0.3, -W - 0.3, W + 0.3), 0.25, id));
  const s = out.solids;
  s.push(part(centre, heading, nosed(-L, L, W, 0.35, 0.25), 0.18, 0.78, paint, id, "car"));
  s.push(part(centre, heading, nosed(-L + 1.0, L - 1.2, W - 0.12, 0.25, 0.2), 0.78, 1.42, glass(), id, "car"));
  s.push(part(centre, heading, box(-L + 1.15, L - 1.35, -W + 0.15, W - 0.15), 1.42, 1.48, paint, id, "car"));
  for (const side of [1, -1]) {
    s.push(part(centre, heading, box(L - 0.06, L + 0.04, side * (W - 0.55), side * (W - 0.18)), 0.45, 0.62, COLORS.head, id, "car"));
    s.push(part(centre, heading, box(-L - 0.04, -L + 0.06, side * (W - 0.55), side * (W - 0.18)), 0.5, 0.66, COLORS.tail, id, "car"));
  }
  lights(centre, heading, L, -L, W, id, out);
  fadeFrom(out, solids, ground, pose.opacity);
}

const GATE_ARM = 5.6;       // m, across one road approach
const GATE_ROAD_HALF = 6.2; // m, pivot offset from the road centre
const GATE_SETBACK = 5.0;   // m, from the track centre along the road

/**
 * Two gates per crossing, one for each road approach. Each arm is drawn as
 * a red-and-white bar growing from its post across the lane as it lowers, so
 * the motion reads at street zoom; the warning lights flash while active.
 */
export function gateBodies(gate: GateState, seconds: number, out: BodyLayers) {
  const { crossing, lowered, active } = gate;
  const centre: LonLat = [crossing.lon, crossing.lat];
  const road = crossing.road_bearing;
  const id = `gate:${crossing.id}`;
  const flash = active && Math.floor(seconds * 2) % 2 === 0;
  for (const side of [1, -1]) {
    // Local frame: x along the road (towards this approach), y across it.
    const heading = side === 1 ? road : (road + 180) % 360;
    const post: Local = [GATE_SETBACK, -GATE_ROAD_HALF];
    // Drawn a little chunkier than life so the gate reads at street zoom.
    out.solids.push(part(centre, heading, box(post[0] - 0.3, post[0] + 0.3, post[1] - 0.3, post[1] + 0.3), 0, 1.6, "#d9dde0", id, "gate"));
    out.solids.push(part(centre, heading, box(post[0] - 0.32, post[0] + 0.32, post[1] - 0.95, post[1] - 0.3), 1.35, 1.9,
      flash ? "#ff2d1e" : "#5a1a16", id, "gate"));
    const reach = GATE_ARM * lowered;
    if (reach < 0.25) continue;
    // Alternating red/white 0.8 m bands along the arm.
    for (let y = 0; y < reach; y += 0.8) {
      const end = Math.min(reach, y + 0.8);
      const band = Math.floor(y / 0.8) % 2 === 0 ? "#e8392e" : "#f4f4f2";
      out.solids.push(part(centre, heading, box(post[0] - 0.22, post[0] + 0.22, post[1] + y, post[1] + end), 0.95, 1.08, band, id, "gate"));
    }
  }
}

export function emptyBodies(): BodyLayers {
  return { solids: [], ground: [] };
}
