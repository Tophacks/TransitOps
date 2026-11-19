// Simulated road traffic around the corridor buses: atmosphere, not data.
//
// Two kinds of car, both labelled simulated on the map:
//
// - Queued cars. At a red signal the Python simulator makes a bus wait behind
//   `queue_cars` cars (backend/corridor.py hold_position). Those cars are drawn
//   here between the bus and the stop line, and pull away with the bus when
//   the light changes, so the bus visibly queues behind them.
// - Background cars. A light stream on each corridor street, computed for the
//   whole run up front (so pausing and scrubbing stay exact): cars cruise,
//   stop at red signals, and follow buses. A car a bus would run into, or one
//   entering the space where a bus queue waits, turns off the street (fades).
//
// When the SUMO twin is running its vehicles are drawn instead (GeospatialMap).

import { BUS_FRONT_M, CAR_SPACING_M, holdPosition, type Corridor, type CorridorSegment } from "./corridor.ts";
import { busPoses, type BusPose, type TimelineFrame } from "./playback.ts";
import { pointAt, tangentAt, type LonLat } from "./path.ts";

import { followingSpeed, ROAD_GAP as MIN_GAP, ROAD_STEP as STEP } from "./road.ts";

export const CAR_LENGTH = 4.6;
export const CAR_WIDTH = 1.85;
const LANE_M = 1.9;           // cars keep to the lane centre, right of the street centreline
const FADE_M = 25;            // metres over which a car turns off the street
const BUS_REAR = 6.45;        // bus rear behind its distance-along reference point

export type CarPose = {
  id: string;
  centre: LonLat;
  heading: number;
  speed: number;
  /** 0..1: cars fade in as they join and out as they turn off */
  opacity: number;
  kind: "queue" | "traffic";
  /** street segment ("FROM>TO") and distance along it */
  segment: string;
  s: number;
};

function place(segment: CorridorSegment, s: number): { centre: LonLat; heading: number } {
  const centre = pointAt(segment.path, s, LANE_M);
  const [tx, ty] = tangentAt(segment.path, s, 2);
  return { centre, heading: ((Math.atan2(tx, ty) * 180) / Math.PI + 360) % 360 };
}

/**
 * The cars a bus is queued behind at a red signal. While the bus waits they
 * stand nose to tail up to the stop line; when it pulls away they move off
 * ahead of it and turn off the street a little past the junction.
 */
export function queuedCars(corridor: Corridor, buses: readonly BusPose[]): CarPose[] {
  const out: CarPose[] = [];
  for (const bus of buses) {
    if (!bus.queueCars || bus.holdAt === null || !bus.holdMinutes) continue;
    const segment = corridor.segment(bus.from, bus.to);
    const line = segment.signal.stop_line_m;
    // Where the bus actually waits (the trip model snaps to its 2 m grid).
    const hold = bus.holdAt;
    const approach = hold - bus.distance;
    if (approach > 160) continue; // the queue only matters once the bus is near
    const moved = Math.max(0, bus.distance - hold);
    for (let k = 0; k < bus.queueCars; k += 1) {
      // Lined up ahead of the waiting bus's nose (k = 0 nearest the bus); the
      // front car ends about a metre short of the stop line.
      const slot = hold + BUS_FRONT_M + 1.2 + CAR_LENGTH / 2 + k * CAR_SPACING_M;
      const s = slot + moved;
      const past = s - (line + 40);
      const opacity = Math.min(1, (160 - approach) / 40, past > 0 ? 1 - past / FADE_M : 1);
      if (opacity <= 0 || s > segment.length_m) continue;
      out.push({
        id: `${bus.id}:q${k}`,
        ...place(segment, s),
        speed: moved > 0 ? bus.speed : 0,
        opacity: Math.max(0, opacity),
        kind: "queue",
        segment: `${segment.from}>${segment.to}`,
        s,
      });
    }
  }
  return out;
}

type Track = { id: string; segment: number; born: number; s: Float32Array; fadeFrom: number };

export type TrafficOptions = {
  traffic: number;          // simulator traffic multiplier
  phaseAt: (signalId: string, minute: number) => "GREEN" | "RED";
};

/**
 * Background cars on every corridor street for the whole run, integrated in
 * half-second steps. Positions for any time are interpolated from the stored
 * tracks, so the stream is a pure function of simulated time.
 */
export class TrafficStream {
  private readonly tracks: Track[] = [];
  private readonly segments: CorridorSegment[];

  constructor(corridor: Corridor, timeline: readonly TimelineFrame[], options: TrafficOptions) {
    this.segments = corridor.data.segments.filter((segment) => segment.from !== "YARD");
    const duration = Math.max(0, timeline.length - 1) * 60;
    const traffic = Math.max(0.5, options.traffic);
    const headway = 42 / traffic;
    const cruise = Math.min(13.5, 12.5 / Math.sqrt(traffic));
    const steps = Math.floor(duration / STEP);
    type Live = { track: Track; s: number; v: number; values: number[]; leaving: number | null };
    const live: Live[][] = this.segments.map(() => []);
    const nextSpawn = this.segments.map((_, i) => (i * 11) % headway);
    let serial = 0;
    for (let step = 0; step <= steps; step += 1) {
      const t = step * STEP;
      const buses = busPoses(corridor, timeline, t);
      this.segments.forEach((segment, si) => {
        const cars = live[si];
        const onSegment = buses.filter((bus) => bus.from === segment.from && bus.to === segment.to);
        const line = segment.signal.stop_line_m;
        const red = options.phaseAt(segment.signal.id, Math.floor(t / 60)) === "RED";
        // Space a bus queue will occupy: cars must not enter it.
        const queueZone = onSegment
          .filter((bus) => bus.queueCars && bus.holdMinutes && bus.holdAt !== null && bus.distance <= bus.holdAt + 0.5)
          .map((bus) => [holdPosition(segment, bus.queueCars) + BUS_FRONT_M - 1, line + 1] as const);
        if (t >= nextSpawn[si]) {
          const last = cars[cars.length - 1];
          const blocked = (last && last.s < CAR_LENGTH + MIN_GAP + 2)
            || onSegment.some((bus) => bus.distance - BUS_REAR < CAR_LENGTH + MIN_GAP);
          if (!blocked) {
            serial += 1;
            const track: Track = { id: `car${serial}`, segment: si, born: t, s: new Float32Array(0), fadeFrom: Infinity };
            cars.push({ track, s: 0, v: cruise * 0.8, values: [0], leaving: null });
            nextSpawn[si] = t + headway * (0.75 + 0.5 * ((serial * 7919) % 100) / 100);
          }
        }
        for (let i = 0; i < cars.length; i += 1) {
          const car = cars[i];
          if (car.leaving !== null) continue;
          // Gap to whatever is ahead: the car in front, a bus, or a red stop line.
          let gap = Infinity;
          const ahead = cars.slice(0, i).reverse().find((other) => other.leaving === null);
          if (ahead) gap = ahead.s - car.s - CAR_LENGTH - MIN_GAP;
          for (const bus of onSegment) {
            if (bus.distance > car.s) gap = Math.min(gap, bus.distance - BUS_REAR - car.s - CAR_LENGTH / 2 - MIN_GAP);
          }
          if (red && car.s < line - 1) gap = Math.min(gap, line - car.s - CAR_LENGTH / 2 - 0.6);
          car.v = followingSpeed(car.v, cruise, gap);
          car.s += car.v * STEP;
          // A bus closing in from behind, or a bus queue's space ahead: turn off.
          const busBehind = onSegment.some((bus) => bus.distance < car.s && car.s - CAR_LENGTH / 2 - (bus.distance + BUS_FRONT_M) < 1.5);
          const inQueueZone = queueZone.some(([from, to]) => car.s + CAR_LENGTH / 2 > from && car.s < to);
          if (busBehind || inQueueZone || car.s >= segment.length_m - 5) {
            car.leaving = t;
            car.track.fadeFrom = car.values.length - 1;
          }
          car.values.push(car.s);
        }
        // Retire cars that have finished fading.
        for (let i = cars.length - 1; i >= 0; i -= 1) {
          const car = cars[i];
          if (car.leaving !== null && t - car.leaving >= FADE_M / Math.max(4, cruise)) {
            car.track.s = Float32Array.from(car.values);
            this.tracks.push(car.track);
            cars.splice(i, 1);
          }
        }
      });
    }
    for (const cars of live) {
      for (const car of cars) {
        car.track.s = Float32Array.from(car.values);
        this.tracks.push(car.track);
      }
    }
  }

  get size() {
    return this.tracks.length;
  }

  /** Every background car on the streets at simulated second `t`. */
  cars(t: number): CarPose[] {
    const out: CarPose[] = [];
    for (const track of this.tracks) {
      const x = (t - track.born) / STEP;
      if (x < 0 || x > track.s.length - 1) continue;
      const i = Math.floor(x);
      const f = x - i;
      const a = track.s[i];
      const b = track.s[Math.min(track.s.length - 1, i + 1)];
      const s = a + (b - a) * f;
      const segment = this.segments[track.segment];
      const joined = Math.min(1, x / 4);
      const fading = x > track.fadeFrom ? 1 - (x - track.fadeFrom) / (track.s.length - 1 - track.fadeFrom || 1) : 1;
      out.push({
        id: track.id,
        ...place(segment, s),
        speed: (b - a) / STEP,
        opacity: Math.max(0, Math.min(joined, fading)),
        kind: "traffic",
        segment: `${segment.from}>${segment.to}`,
        s,
      });
    }
    return out;
  }
}
