// Geometry along an encoded path: [lon, lat, cumulative metres] triples.
// Mirrors the helpers in backend/corridor.py.

export type PathPoint = [number, number, number];
export type LonLat = [number, number];

export const M_PER_DEG = 111_320.0;
const K = Math.cos((43.465 * Math.PI) / 180);

export function pathLength(path: readonly PathPoint[]) {
  return path[path.length - 1][2];
}

export function lerpPath(path: readonly PathPoint[], s: number): LonLat {
  // Beyond either end, extend along the end direction so axle chords and
  // tangents near a stop stay straight instead of collapsing to a point.
  if (s < path[0][2]) {
    const first = path[0];
    const second = path[1];
    const span = second[2] - first[2] || 1;
    const f = (s - first[2]) / span;
    return [first[0] + (second[0] - first[0]) * f, first[1] + (second[1] - first[1]) * f];
  }
  const last = path[path.length - 1];
  if (s >= last[2]) {
    const prev = path[path.length - 2];
    const span = last[2] - prev[2] || 1;
    const f = (s - last[2]) / span;
    return [last[0] + (last[0] - prev[0]) * f, last[1] + (last[1] - prev[1]) * f];
  }
  let lo = 0;
  let hi = path.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (path[mid][2] <= s) lo = mid;
    else hi = mid;
  }
  const a = path[lo];
  const b = path[hi];
  const f = b[2] > a[2] ? (s - a[2]) / (b[2] - a[2]) : 0;
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
}

export function lerpPathClamped(path: readonly PathPoint[], s: number): LonLat {
  const length = pathLength(path);
  return lerpPath(path, Math.min(length, Math.max(0, s)));
}

/** Metric delta (east, north) between two lon/lat points. */
export function deltaMetres(a: LonLat, b: LonLat): [number, number] {
  return [(b[0] - a[0]) * M_PER_DEG * K, (b[1] - a[1]) * M_PER_DEG];
}

/** Move a lon/lat point by (east, north) metres. */
export function offsetLonLat(p: LonLat, east: number, north: number): LonLat {
  return [p[0] + east / (M_PER_DEG * K), p[1] + north / M_PER_DEG];
}

/** Unit tangent (east, north) at s, from a chord `half` metres either side. */
export function tangentAt(path: readonly PathPoint[], s: number, half = 3): [number, number] {
  const a = lerpPath(path, s - half);
  const b = lerpPath(path, s + half);
  const [dx, dy] = deltaMetres(a, b);
  const norm = Math.hypot(dx, dy) || 1;
  return [dx / norm, dy / norm];
}

/** Point `s` metres along the path, shifted `right` metres to the right of travel. */
export function pointAt(path: readonly PathPoint[], s: number, right = 0): LonLat {
  const p = lerpPath(path, s);
  if (!right) return p;
  const [tx, ty] = tangentAt(path, s);
  return offsetLonLat(p, ty * right, -tx * right);
}

/** Compass bearing in degrees (0 = north, 90 = east) of the chord a -> b. */
export function bearing(a: LonLat, b: LonLat) {
  const [dx, dy] = deltaMetres(a, b);
  return ((Math.atan2(dx, dy) * 180) / Math.PI + 360) % 360;
}

function smoothstep(e0: number, e1: number, x: number) {
  if (e1 <= e0) return x >= e1 ? 1 : 0;
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
}

/** Metres right of the street centreline: in lane, easing to the curb at stops. */
export function lateralOffset(
  length: number, curb: readonly [number, number], lane: number, blend: number, s: number,
) {
  const lead = 1 - smoothstep(0, blend, s);
  const tail = smoothstep(length - blend, length, s);
  return lane + (curb[0] - lane) * lead + (curb[1] - lane) * tail;
}
