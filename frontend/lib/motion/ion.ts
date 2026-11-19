import { Run, type Curve } from "./kinematics.ts";
import type { IonTrack } from "./corridor.ts";
import { bearing, deltaMetres, lerpPath, type LonLat, type PathPoint } from "./path.ts";

// ION light rail from the Python simulator (backend/ion.py). The API returns
// the timetable: each trip's terminal departure and every station-to-station
// leg. Between stations a train follows the same trip model as the buses
// (kinematics.ts), so this reproduces the simulator's positions exactly at
// every minute and draws every frame in between. Keep the arithmetic in the
// same order as the Python version.

export const ION_ACCEL = 1.0;
export const ION_DECEL = 1.1;
export const ION_TOP_SPEED = 16.7;

/** Section lengths, front to back (m), and the bellows gap between them. */
export const ION_SECTIONS = [9.2, 7.0, 7.4, 7.0, 9.2];
export const ION_GAP = 0.5;
export const ION_LENGTH = ION_SECTIONS.reduce((a, b) => a + b, 0) + ION_GAP * (ION_SECTIONS.length - 1);

export type Direction = "north" | "south";

/** The ION part of /api/simulate (IonPlan.schedule() in backend/ion.py). */
export type IonSchedule = {
  headway_min: number;
  dwell_s: number;
  train_length_m: number;
  lines: Record<Direction, {
    stops: { name: string; nose_m: number }[];
    legs: { from_m: number; to_m: number; run_s: number }[];
  }>;
  trips: {
    id: string;
    direction: Direction;
    scheduled_s: number;
    depart_s: number;
    hold: string;
    transfers: Record<string, number>;
  }[];
};

type Leg = { fromStation: string; toStation: string; fromM: number; toM: number; runS: number; run: Run };

class Line {
  readonly legs: Leg[] = [];
  readonly offsets: number[] = [];
  readonly tripSeconds: number;

  constructor(track: IonTrack, line: IonSchedule["lines"][Direction], dwell: number) {
    line.legs.forEach((leg, i) => {
      const curves: Curve[] = track.curves
        .filter(([s]) => leg.from_m <= s && s <= leg.to_m)
        .map(([s, cap]) => [s - leg.from_m, cap]);
      this.legs.push({
        fromStation: line.stops[i].name,
        toStation: line.stops[i + 1].name,
        fromM: leg.from_m,
        toM: leg.to_m,
        runS: leg.run_s,
        run: new Run(leg.to_m - leg.from_m, curves, leg.run_s, {
          accel: ION_ACCEL, decel: ION_DECEL, topSpeed: ION_TOP_SPEED, rushSpeed: ION_TOP_SPEED,
        }),
      });
    });
    let t = 0.0;
    this.offsets.push(t);
    for (const leg of this.legs) {
      t += leg.runS;
      this.offsets.push(t);
      t += dwell;
    }
    this.tripSeconds = this.offsets[this.offsets.length - 1];
  }
}

export type TrainState = { nose: number; station: string | null; leg: number };

/** The simulator's ION timetable, able to place any trip at any second. */
export class IonTimetable {
  readonly schedule: IonSchedule;
  readonly tracks: Record<Direction, IonTrack>;
  private readonly lines: Record<Direction, Line>;

  constructor(tracks: Record<Direction, IonTrack>, schedule: IonSchedule) {
    this.schedule = schedule;
    this.tracks = tracks;
    this.lines = {
      north: new Line(tracks.north, schedule.lines.north, schedule.dwell_s),
      south: new Line(tracks.south, schedule.lines.south, schedule.dwell_s),
    };
  }

  /** Port of IonPlan.state: nose position, station it stands at, leg index. */
  state(trip: IonSchedule["trips"][number], t: number): TrainState | null {
    const line = this.lines[trip.direction];
    const dwell = this.schedule.dwell_s;
    const x = t - trip.depart_s;
    if (x < 0 || x > line.tripSeconds) return null;
    for (let i = 0; i < line.legs.length; i += 1) {
      const leg = line.legs[i];
      const start = line.offsets[i] + (i ? dwell : 0.0);
      if (x < start) return { nose: leg.fromM, station: leg.fromStation, leg: i };
      if (x <= line.offsets[i + 1]) return { nose: leg.fromM + leg.run.distance(x - start), station: null, leg: i };
    }
    const last = line.legs[line.legs.length - 1];
    return { nose: last.toM, station: last.toStation, leg: line.legs.length - 1 };
  }

  nextStation(direction: Direction, state: TrainState) {
    return state.station ?? this.lines[direction].legs[state.leg].toStation;
  }

  /** Every train on the line `seconds` into the simulation. */
  trains(seconds: number): TrainPose[] {
    const out: TrainPose[] = [];
    for (const trip of this.schedule.trips) {
      const state = this.state(trip, seconds);
      if (!state) continue;
      const ahead = this.state(trip, seconds + 0.25);
      const behind = this.state(trip, seconds - 0.25);
      const speed = ahead && behind && !state.station ? Math.max(0, (ahead.nose - behind.nose) / 0.5) : 0;
      const path = this.tracks[trip.direction].path;
      out.push({
        id: trip.id,
        direction: trip.direction,
        nose: state.nose,
        speed,
        station: state.station ?? undefined,
        nextStation: this.nextStation(trip.direction, state),
        delayMin: (trip.depart_s - trip.scheduled_s) / 60,
        hold: trip.hold,
        sections: articulatePath(path, state.nose),
      });
    }
    return out;
  }
}

export type SectionPose = {
  /** chord endpoints on the track centreline */
  front: LonLat;
  rear: LonLat;
  centre: LonLat;
  heading: number;
  length: number;
};

export type TrainPose = {
  id: string;
  direction: Direction;
  nose: number;
  speed: number;
  station?: string;
  nextStation?: string;
  delayMin?: number;
  hold?: string;
  sections: SectionPose[];
  /**
   * Bidirectional trains (the Fairway turnback): index of the cab with its
   * headlights on, or null while the operator changes ends (tail lights at
   * both ends). Sections stay in physical order, so section 0 is always the
   * same cab. Unset: section 0 leads, as on the simulated line.
   */
  headlights?: number | null;
};

/**
 * Place each of the five sections at its own distance along the track.
 * Every section is a rigid chord between two points on the centreline, so on
 * a curve the leading section turns while the trailing ones are still on the
 * straight, and neighbouring sections meet at the same bellows gap.
 */
export function articulatePath(path: readonly PathPoint[], nose: number): SectionPose[] {
  const sections: SectionPose[] = [];
  let s = nose;
  for (const length of ION_SECTIONS) {
    const front = lerpPath(path, s);
    const rear = lerpPath(path, s - length);
    sections.push({
      front,
      rear,
      centre: [(front[0] + rear[0]) / 2, (front[1] + rear[1]) / 2],
      heading: bearing(rear, front),
      // The chord is a little shorter than the arc on a curve.
      length: Math.hypot(...deltaMetres(rear, front)),
    });
    s -= length + ION_GAP;
  }
  return sections;
}
