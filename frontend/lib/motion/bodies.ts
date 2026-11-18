// Stylised 3D vehicle bodies, built as MapLibre fill-extrusion footprints.
//
// Bodies are extruded in world space, so they stay planted on the street when
// the camera pans, pitches or rotates, and they share the depth buffer with
// the basemap's extruded buildings, which can therefore hide them.

import { offsetLonLat, type LonLat } from "./path.ts";
import type { BusPose } from "./playback.ts";
import type { TrainPose } from "./ion.ts";

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

export function busBody(pose: BusPose, selected: boolean, out: BodyLayers) {
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
  s.push(part(centre, heading, nosed(-L, L, W, 0.45), 0.95, 1.4, COLORS.busBodyShade, id));
  s.push(part(centre, heading, nosed(-L + 0.1, L - 0.55, W + 0.02, 0.3), 1.4, 2.45, COLORS.glass, id));
  // Front: cream mask with a light windshield, so the nose reads at a glance.
  s.push(part(centre, heading, nosed(L - 0.7, L, W + 0.01, 0.45, 0), 0.95, 3.02, COLORS.frontMask, id));
  s.push(part(centre, heading, box(L - 0.5, L + 0.03, -W + 0.3, W - 0.3), 1.15, 2.5, COLORS.windshield, id));
  s.push(part(centre, heading, nosed(-L, L, W, 0.45), 2.45, 3.02, COLORS.busBody, id));
  s.push(part(centre, heading, box(L - 0.5, L + 0.02, -W + 0.45, W - 0.45), 2.62, 2.92, COLORS.sign, id));
  s.push(part(centre, heading, box(-2.6, 1.6, -W + 0.45, W - 0.45), 3.02, 3.34, STATE_BEACON[pose.state] ?? COLORS.roof, id));
  for (const side of [1, -1]) {
    s.push(part(centre, heading, box(L - 0.1, L + 0.06, side * (W - 0.75), side * (W - 0.25)), 0.55, 0.82, COLORS.head, id));
    s.push(part(centre, heading, box(-L - 0.06, -L + 0.1, side * (W - 0.75), side * (W - 0.25)), 0.6, 0.9, COLORS.tail, id));
  }
}

export function trainBody(pose: TrainPose, out: BodyLayers) {
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
    s.push(part(centre, heading, outline, 1.08, 1.28, COLORS.ionStripe, id));
    s.push(part(centre, heading, outline, 1.28, 2.42, COLORS.glass, id));
    s.push(part(centre, heading, outline, 2.42, 3.28, COLORS.ionUpper, id));
    if (index === 1 || index === 3) {
      s.push(part(centre, heading, box(-L + 1.2, L - 1.2, -W + 0.5, W - 0.5), 3.28, 3.62, COLORS.ionRoofGear, id));
    }
    if (index === 2) {
      s.push(part(centre, heading, box(-0.9, 0.9, -W + 0.85, W - 0.85), 3.28, 3.95, COLORS.ionPanto, id));
    }
    if (first) {
      s.push(part(centre, heading, box(L - 0.75, L - 0.05, -W * 0.5, W * 0.5), 1.25, 2.62, COLORS.windshield, id));
      s.push(part(centre, heading, box(L - 0.6, L + 0.02, -W * 0.38, W * 0.38), 2.62, 2.9, COLORS.ionSign, id));
      for (const side of [1, -1]) {
        s.push(part(centre, heading, box(L - 0.45, L + 0.05, side * W * 0.22, side * W * 0.5), 0.6, 0.85, COLORS.head, id));
      }
    }
    if (lastSection) {
      for (const side of [1, -1]) {
        s.push(part(centre, heading, box(-L - 0.05, -L + 0.45, side * W * 0.22, side * W * 0.5), 0.6, 0.85, COLORS.tail, id));
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

export function emptyBodies(): BodyLayers {
  return { solids: [], ground: [] };
}
