// Live GRT and ION vehicles moving along their GTFS route shapes.
//
// The public feed reports one GPS ping per vehicle every 15-30 s. Drawn
// as-is, vehicles jump between pings and cut across blocks. Instead each
// ping is snapped onto the vehicle's route shape as a distance along it, and
// between pings the vehicle glides along the shape with eased motion, so it
// follows the streets and arrives at the latest ping when the next one is due.
//
// Nothing here is simulated: every glide ends exactly at a reported position.

import {
  bearing, deltaMetres, lerpPath, pathLength, pointAt, projections,
  type LonLat, type PathPoint, type PathProjection,
} from "./path.ts";

export type LiveMode = "bus" | "ion";

export type LivePing = {
  /** stable key, e.g. "bus:20907" */
  key: string;
  id: string;
  mode: LiveMode;
  lon: number;
  lat: number;
  /** feed timestamp of the GPS fix (Unix seconds) */
  timestamp: number;
  shapeId: string | null;
  /** 0..1 progress through the trip's stop list, if known (disambiguates loops) */
  progress?: number | null;
  /** at a stop: the glide eases to rest there */
  stopped?: boolean;
};

/** Further than this from the shape, the vehicle is off its route (detour). */
export const DETOUR_M = 45;
/** Backward GPS jitter we absorb by holding still. */
export const BACK_TOLERANCE_M = 250;
/**
 * No glide is drawn faster than this at its peak (highway iXpress and ION bus
 * runs reach ~100 km/h). A glide is stretched up to 1.5 ping intervals to stay
 * under it; if that is not enough we missed pings, and the vehicle jumps.
 */
export const MAX_GLIDE_SPEED = 30; // m/s (108 km/h)
/** Short straight moves (re-anchoring, detours) glide; longer ones jump. */
export const STRAIGHT_GLIDE_M = 60;
/** Pings older than this are drawn as stale. */
export const STALE_S = 75;
/** Keep a vehicle that vanished from the feed this long before fading it out. */
export const GONE_GRACE_S = 45;
export const FADE_S = 1.5;
export const DEFAULT_INTERVAL_S = 20;
/** Bus centre this far right of the shape (shapes follow the street centreline). */
export const BUS_LANE_M = 1.9;

type Glide = {
  s0: number;
  s1: number;
  v0: number;
  v1: number;
  start: number;
  duration: number;
};

type Straight = { from: LonLat; to: LonLat; start: number; duration: number };

type Track = {
  key: string;
  id: string;
  mode: LiveMode;
  shapeId: string | null;
  path: PathPoint[] | null;
  ping: LivePing;
  /** distance along the shape of the last on-shape ping */
  s: number | null;
  glide: Glide | null;
  straight: Straight | null;
  /** where it is drawn when not on its shape */
  free: LonLat;
  heading: number;
  detour: boolean;
  /** off the shape past one of its ends: a terminal layover, not a detour */
  terminal: boolean;
  intervals: number[];
  bornAt: number;
  goneAt: number | null;
};

export type LivePose = {
  key: string;
  id: string;
  mode: LiveMode;
  centre: LonLat;
  heading: number;
  /** metres per second along the street */
  speed: number;
  /** 0..1, fades vehicles in when they appear and out when they leave the feed */
  opacity: number;
  stale: boolean;
  detour: boolean;
  /** off the shape beyond its first or last point (layover at a terminal) */
  terminal: boolean;
  /** seconds since the GPS fix, at the moment of drawing */
  age: number;
  shapeId: string | null;
  /** distance along the shape (null when off it) */
  s: number | null;
  path: PathPoint[] | null;
};

/**
 * Snap a GPS fix onto a route shape. A shape can pass the same street twice
 * (out and back, or a loop that starts and ends on the same street), so among
 * projections nearly as close as the best one take the one nearest `hint`.
 *
 * `reach` is how far the vehicle can plausibly have gone since `hint` was its
 * position: candidates from BACK_TOLERANCE_M behind to `reach` ahead are
 * preferred. Without `reach` the hint is only a rough estimate (from the
 * trip's stop progress), so a candidate behind it is as good as one ahead.
 */
export function snap(path: readonly PathPoint[], p: LonLat, hint?: number | null, reach?: number): PathProjection {
  const all = projections(path, p);
  const best = all[0];
  if (hint == null) return best;
  const close = all.filter((c) => c.offset <= best.offset + 20);
  const plausible = reach == null ? [] : close.filter((c) => c.s >= hint - BACK_TOLERANCE_M && c.s <= hint + reach);
  const pool = plausible.length ? plausible : close;
  return pool.reduce((a, c) => (Math.abs(c.s - hint) < Math.abs(a.s - hint) ? c : a));
}

/**
 * Monotone cubic Hermite from s0 to s1 over `duration` seconds, starting at
 * speed v0 and arriving at speed v1. Slopes are clamped to the Fritsch-Carlson
 * bound, so distance never decreases and never overshoots s1.
 */
export function glideAt(g: Glide, now: number): { s: number; v: number } {
  const span = g.s1 - g.s0;
  if (g.duration <= 0 || span <= 0) return { s: g.s1, v: 0 };
  const u = Math.min(1, Math.max(0, (now - g.start) / g.duration));
  if (u >= 1) return { s: g.s1, v: 0 };
  const scale = span / g.duration;
  const m0 = Math.min(3, Math.max(0, g.v0 / scale));
  const m1 = Math.min(3, Math.max(0, g.v1 / scale));
  const u2 = u * u;
  const u3 = u2 * u;
  const h = (u3 - 2 * u2 + u) * m0 + (-2 * u3 + 3 * u2) + (u3 - u2) * m1;
  const dh = (3 * u2 - 4 * u + 1) * m0 + (-6 * u2 + 6 * u) + (3 * u2 - 2 * u) * m1;
  return { s: g.s0 + span * h, v: Math.max(0, dh * scale) };
}

function atEnd(path: readonly PathPoint[] | null, hit: PathProjection | null) {
  return Boolean(path && hit) && (hit!.s <= 1 || hit!.s >= pathLength(path!) - 1);
}

/** Fastest point of a glide (m/s). */
export function glidePeak(g: Glide) {
  let peak = 0;
  for (let k = 0; k <= 32; k += 1) peak = Math.max(peak, glideAt(g, g.start + (g.duration * k) / 32 - 1e-9).v);
  return peak;
}

function ease(u: number) {
  const t = Math.min(1, Math.max(0, u));
  return t * t * (3 - 2 * t);
}

function lerp(a: LonLat, b: LonLat, f: number): LonLat {
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
}

function metres(a: LonLat, b: LonLat) {
  return Math.hypot(...deltaMetres(a, b));
}

function median(values: number[]) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

export class LiveTracker {
  private readonly tracks = new Map<string, Track>();

  /** Vehicles currently tracked (including ones fading out). */
  get size() {
    return this.tracks.size;
  }

  private alongNow(track: Track, now: number) {
    if (track.s == null) return null;
    return track.glide ? glideAt(track.glide, now) : { s: track.s, v: 0 };
  }

  private freeNow(track: Track, now: number): LonLat {
    const st = track.straight;
    if (!st) return track.free;
    return lerp(st.from, st.to, ease((now - st.start) / st.duration));
  }

  /** Where the vehicle is drawn right now. */
  private positionNow(track: Track, now: number): LonLat {
    const along = this.alongNow(track, now);
    if (track.path && along && !track.detour && !track.terminal && !track.straight) {
      return pointAt(track.path, along.s, track.mode === "bus" ? BUS_LANE_M : 0);
    }
    return this.freeNow(track, now);
  }

  private interval(track: Track) {
    return track.intervals.length ? median(track.intervals) : DEFAULT_INTERVAL_S;
  }

  /**
   * Feed a fresh poll of the live feed. `shapes` returns the route shape for a
   * shape id (or undefined while it is still loading). `now` is the wall clock
   * in seconds (e.g. performance.now() / 1000).
   */
  update(pings: readonly LivePing[], shapes: (shapeId: string) => PathPoint[] | undefined, now: number) {
    const seen = new Set<string>();
    for (const ping of pings) {
      seen.add(ping.key);
      const path = ping.shapeId ? shapes(ping.shapeId) ?? null : null;
      const track = this.tracks.get(ping.key);
      if (!track) {
        this.tracks.set(ping.key, this.create(ping, path, now));
        continue;
      }
      track.goneAt = null;
      if (ping.timestamp < track.ping.timestamp) continue; // out of order
      const samePing = ping.timestamp === track.ping.timestamp && ping.lon === track.ping.lon && ping.lat === track.ping.lat;
      if (samePing && path === track.path) continue; // nothing new: it rests at the last ping
      this.advance(track, ping, path, now);
    }
    for (const track of this.tracks.values()) {
      if (!seen.has(track.key) && track.goneAt == null) track.goneAt = now;
    }
  }

  private create(ping: LivePing, path: PathPoint[] | null, now: number): Track {
    const p: LonLat = [ping.lon, ping.lat];
    const hint = path && ping.progress != null ? ping.progress * pathLength(path) : null;
    const snapped = path ? snap(path, p, hint) : null;
    const onShape = Boolean(snapped && snapped.offset <= DETOUR_M);
    const track: Track = {
      key: ping.key,
      id: ping.id,
      mode: ping.mode,
      shapeId: ping.shapeId,
      path,
      ping,
      s: onShape ? snapped!.s : null,
      glide: null,
      straight: null,
      free: p,
      heading: 0,
      detour: Boolean(path) && !onShape && !atEnd(path, snapped),
      terminal: Boolean(path) && !onShape && atEnd(path, snapped),
      intervals: [],
      bornAt: now,
      goneAt: null,
    };
    track.heading = this.headingNow(track, now);
    return track;
  }

  private advance(track: Track, ping: LivePing, path: PathPoint[] | null, now: number) {
    const here = this.positionNow(track, now);
    const along = this.alongNow(track, now);
    const gap = ping.timestamp - track.ping.timestamp;
    if (gap > 0) {
      track.intervals.push(Math.min(60, Math.max(5, gap)));
      if (track.intervals.length > 6) track.intervals.shift();
    }
    track.heading = this.headingNow(track, now);
    const p: LonLat = [ping.lon, ping.lat];
    const newShape = ping.shapeId !== track.shapeId || path !== track.path;
    const hint = newShape
      ? (path && ping.progress != null ? ping.progress * pathLength(path) : null)
      : along?.s ?? track.s;
    // Same shape: the vehicle can only have moved so far since the last fix.
    const reach = newShape ? undefined : Math.max(600, MAX_GLIDE_SPEED * Math.max(gap, this.interval(track)) * 1.5);
    const snapped = path ? snap(path, p, hint, reach) : null;
    const onShape = Boolean(snapped && snapped.offset <= DETOUR_M);
    const duration = Math.min(40, Math.max(4, this.interval(track)));
    track.ping = ping;
    track.shapeId = ping.shapeId;

    const moveStraight = (to: LonLat) => {
      // Off the shape we don't know the streets: glide only short hops,
      // otherwise jump, so the vehicle never sweeps across a block.
      const d = metres(here, to);
      track.straight = d > 0.5 && d <= STRAIGHT_GLIDE_M ? { from: here, to, start: now, duration: Math.min(duration, 8) } : null;
      track.free = to;
    };

    if (!onShape) {
      // Detour, layover past the end of the shape, or no shape: show the raw GPS fix.
      track.terminal = Boolean(path) && atEnd(path, snapped);
      track.detour = Boolean(path) && !track.terminal;
      track.path = path;
      track.s = null;
      track.glide = null;
      moveStraight(p);
      return;
    }

    const target = snapped!.s;
    if (newShape || track.detour || track.s == null || !along) {
      // New trip, or back on route after a detour: re-anchor on the shape.
      const anchor = pointAt(path!, target, track.mode === "bus" ? BUS_LANE_M : 0);
      track.path = path;
      track.detour = false;
      track.terminal = false;
      track.s = target;
      track.glide = null;
      moveStraight(anchor);
      return;
    }

    track.straight = null;
    const from = along.s;
    if (target <= from) {
      if (from - target <= BACK_TOLERANCE_M) {
        // GPS jitter or a fix taken just before the last one: hold, never reverse.
        track.s = from;
        track.glide = null;
        return;
      }
      // A big step backwards on the same shape is a data error: re-anchor.
      track.s = target;
      track.glide = null;
      track.free = pointAt(path!, target, track.mode === "bus" ? BUS_LANE_M : 0);
      return;
    }
    let glide: Glide = { s0: from, s1: target, v0: along.v, v1: 0, start: now, duration };
    for (let stretch = 0; stretch < 6; stretch += 1) {
      glide.v1 = ping.stopped ? 0 : Math.min(along.v + 3, (target - from) / glide.duration);
      if (glidePeak(glide) <= MAX_GLIDE_SPEED) break;
      glide = { ...glide, duration: glide.duration * 1.08 };
    }
    track.s = target;
    if (glidePeak(glide) > MAX_GLIDE_SPEED) {
      // Missed pings (or a long stale gap): gliding would look like a rocket.
      track.glide = null;
      return;
    }
    track.glide = glide;
  }

  private headingNow(track: Track, now: number) {
    const along = this.alongNow(track, now);
    if (track.path && along && !track.detour && !track.terminal && !track.straight) {
      return bearing(lerpPath(track.path, along.s - 3.4), lerpPath(track.path, along.s + 2.7));
    }
    const st = track.straight;
    if (st && metres(st.from, st.to) > 2) return bearing(st.from, st.to);
    return track.heading;
  }

  private poseOf(track: Track, now: number, feedNow: number): LivePose | null {
    let opacity = Math.min(1, (now - track.bornAt) / FADE_S);
    if (track.goneAt != null) {
      const fade = 1 - (now - track.goneAt - GONE_GRACE_S) / FADE_S;
      if (fade <= 0) return null;
      opacity = Math.min(opacity, fade);
    }
    // A finished straight hop (re-anchor) hands back to the shape.
    if (track.straight && now >= track.straight.start + track.straight.duration) {
      track.free = track.straight.to;
      track.straight = null;
    }
    const along = this.alongNow(track, now);
    const onPath = Boolean(track.path && along && !track.detour && !track.terminal && !track.straight);
    const heading = this.headingNow(track, now);
    track.heading = heading;
    const age = Math.max(0, feedNow - track.ping.timestamp);
    return {
      key: track.key,
      id: track.id,
      mode: track.mode,
      centre: this.positionNow(track, now),
      heading,
      speed: onPath ? along!.v : 0,
      opacity: Math.max(0, opacity),
      stale: age > STALE_S || track.goneAt != null,
      detour: track.detour,
      terminal: track.terminal,
      age,
      shapeId: track.shapeId,
      s: onPath ? along!.s : null,
      path: onPath ? track.path : null,
    };
  }

  /** One vehicle as drawn at `now`, or undefined if it is not tracked. */
  pose(key: string, now: number, feedNow = Date.now() / 1000) {
    const track = this.tracks.get(key);
    return track ? this.poseOf(track, now, feedNow) ?? undefined : undefined;
  }

  /** Every tracked vehicle as drawn at wall-clock `now` (seconds). */
  poses(now: number, feedNow = Date.now() / 1000): LivePose[] {
    const out: LivePose[] = [];
    for (const track of [...this.tracks.values()]) {
      const pose = this.poseOf(track, now, feedNow);
      if (pose) out.push(pose);
      else this.tracks.delete(track.key);
    }
    return out;
  }
}
