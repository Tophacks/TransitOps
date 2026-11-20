import type { Corridor } from "./corridor.ts";
import { bearing, deltaMetres, type LonLat } from "./path.ts";

// Minimal shape of the simulator snapshot this module needs.
export type SegmentMotion = {
  from: string;
  to: string;
  elapsed: number;
  minutes: number;
  hold_minutes: number;
  /** cars queued ahead of the bus at a red signal */
  queue_cars?: number;
  length_m: number;
  distance_m: number;
};

export type TimelineBus = {
  id: string;
  state: string;
  segment?: SegmentMotion | null;
};

export type TimelineFrame = { minute: number; buses: TimelineBus[] };

export type BusPose = {
  id: string;
  state: string;
  from: string;
  to: string;
  /** metres along the current segment */
  distance: number;
  length: number;
  speed: number;
  centre: LonLat;
  heading: number;
  /** cars queued ahead at a red signal on this segment, and where the bus waits */
  queueCars: number;
  holdAt: number | null;
  holdMinutes: number;
};

// Axle positions relative to the bus's route distance (metres, + = ahead).
const FRONT_AXLE = 2.7;
const REAR_AXLE = -3.4;

function sameSegment(a?: SegmentMotion | null, b?: SegmentMotion | null) {
  return Boolean(a && b && a.from === b.from && a.to === b.to && a.minutes === b.minutes && a.elapsed <= b.elapsed);
}

/**
 * Where each simulated bus is at `seconds` of simulated time.
 *
 * Snapshots arrive once per simulated minute and carry the minutes elapsed
 * on the bus's current segment. Between two snapshots the elapsed time runs
 * linearly, and the shared trip model turns it into distance, so the bus
 * eases away from stops, slows for corners and signals, and brakes into the
 * next stop. A held bus (dwelling, broken down, waiting to depart) has the
 * same elapsed time in both snapshots and stays still.
 */
/** Optional limits on where a bus may be drawn along a segment (e.g. it waits
 * at a station's entrance while the station replay draws it inside). */
export type SegmentClamp = (from: string, to: string, motion?: { detour?: boolean; length_m?: number }) => [number, number] | null;

export function busPoses(corridor: Corridor, timeline: readonly TimelineFrame[], seconds: number, clamp?: SegmentClamp): BusPose[] {
  if (!timeline.length) return [];
  const last = timeline.length - 1;
  const offset = seconds / 60 - (timeline[0].minute ?? 0);
  const minute = Math.min(last, Math.max(0, Math.floor(offset)));
  const frac = minute >= last ? 0 : Math.min(1, Math.max(0, offset - minute));
  // (frac is 0 on the final snapshot, where `next` is the same frame)
  const now = timeline[minute];
  const next = timeline[Math.min(last, minute + 1)];
  const poses: BusPose[] = [];

  for (const bus of next.buses) {
    // Always interpolate across [minute, minute + 1] using the later snapshot's
    // segment: at frac = 0 this lands exactly on the earlier snapshot.
    const before = now.buses.find((item) => item.id === bus.id);
    const motion = bus.segment;
    if (!motion) continue;
    const e0 = sameSegment(before?.segment, motion) ? before!.segment!.elapsed : 0;
    const e1 = motion.elapsed;
    const elapsed = e0 + (e1 - e0) * frac;
    const run = corridor.run(motion.from, motion.to, motion.minutes, motion.hold_minutes, motion.queue_cars ?? 0);
    const segment = corridor.segment(motion.from, motion.to);
    const t = elapsed * 60;
    const limits = clamp?.(motion.from, motion.to, motion as { detour?: boolean; length_m?: number });
    const raw = run.distance(t);
    const distance = limits ? Math.min(limits[1], Math.max(limits[0], raw)) : raw;
    const front = corridor.point(segment, distance + FRONT_AXLE);
    const rear = corridor.point(segment, distance + REAR_AXLE);
    const centre: LonLat = [(front[0] + rear[0]) / 2, (front[1] + rear[1]) / 2];
    poses.push({
      id: bus.id,
      state: e1 > e0 ? "EN_ROUTE" : before?.segment ? before.state : bus.state,
      from: motion.from,
      to: motion.to,
      distance,
      length: segment.length_m,
      speed: e1 > e0 && distance === raw ? run.speedAt(t) : 0,
      centre,
      heading: bearing(rear, front),
      queueCars: motion.queue_cars ?? 0,
      holdAt: run.holdPosition,
      holdMinutes: motion.hold_minutes,
    });
  }
  return separate(corridor, poses);
}

const BUS_GAP = 13.5; // centre-to-centre spacing of two buses nose to tail

/**
 * The simulator can bunch two buses onto the same stop in the same minute.
 * Draw the later arrival queued behind the leader instead of overlapping it.
 */
function separate(corridor: Corridor, poses: BusPose[]): BusPose[] {
  for (let pass = 0; pass < 2; pass += 1) {
    for (const a of poses) {
      for (const b of poses) {
        if (a === b) continue;
        const [dx, dy] = deltaMetres(a.centre, b.centre);
        const gap = Math.hypot(dx, dy);
        const turn = Math.abs(((a.heading - b.heading + 540) % 360) - 180);
        if (gap >= BUS_GAP || turn > 40) continue;
        // b is behind a if a lies ahead of b along b's heading.
        const h = (b.heading * Math.PI) / 180;
        const ahead = -(dx * Math.sin(h) + dy * Math.cos(h));
        if (ahead <= 0) continue;
        const segment = corridor.segment(b.from, b.to);
        const back = Math.max(0, b.distance - (BUS_GAP - gap));
        const front = corridor.point(segment, back + FRONT_AXLE);
        const rear = corridor.point(segment, back + REAR_AXLE);
        b.distance = back;
        b.centre = [(front[0] + rear[0]) / 2, (front[1] + rear[1]) / 2];
        b.heading = bearing(rear, front);
      }
    }
  }
  return poses;
}
