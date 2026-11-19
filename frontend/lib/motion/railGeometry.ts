import { pointAt, type LonLat, type PathPoint } from "./path.ts";

// Nominal standard gauge as a visual offset about the GTFS vehicle centreline.
// The resulting rails are illustrative and do not encode surveyed trackwork.
export const STANDARD_GAUGE_M = 1.435;

export function runningRails(path: readonly PathPoint[]): [LonLat[], LonLat[]] {
  const halfGauge = STANDARD_GAUGE_M / 2;
  return [
    path.map(([, , distance]) => pointAt(path, distance, -halfGauge)),
    path.map(([, , distance]) => pointAt(path, distance, halfGauge)),
  ];
}
