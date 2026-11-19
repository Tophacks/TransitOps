import { Run, type Curve } from "./kinematics.ts";
import type { IonTrack } from "./corridor.ts";
import { bearing, deltaMetres, lerpPath, type LonLat, type PathPoint } from "./path.ts";
import { lineNose, moveProfile, type TerminalsData } from "./terminals.ts";

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
    vehicle_id?: string | null;
    platform?: string | null;
    arrival_platform?: string | null;
    dispatch?: string;
    decisions?: string[];
  }[];
  /** Each physical train's movement plan (backend/rail_ops.py). */
  vehicles: VehiclePlan[];
  /** Every rail decision: vehicle agents, station control and dispatch. */
  decisions: RailDecision[];
  holds: { vehicle_id: string; terminal: string; platform: string; from_s: number; until_s: number; reason: string }[];
  metrics: RailMetrics;
  platform_hold: { terminal: string; platform: string; minute: number; minutes: number; reason: string } | null;
};

export type PlanSegment = {
  kind: "move" | "run" | "stand";
  t0: number;
  /** null: open (the run ended while it stood here) */
  t1: number | null;
  /** the cab at the path's forward end */
  lead: "A" | "B";
  trip: string | null;
  terminal?: string;
  route?: string;
  line?: Direction;
  leg?: number;
  s0: number;
  s1: number;
  station?: string;
  note?: string;
  lights?: { off: number; on: number };
};

export type VehiclePlan = { id: string; segments: PlanSegment[] };

export type RailDecision = {
  id: string;
  t: number;
  minute: number;
  level: "vehicle" | "station" | "dispatch";
  terminal: string;
  vehicle_id: string;
  trip_id: string | null;
  trigger: string;
  options: { option: string; feasible: boolean; reason: string; estimate_s: number | null }[];
  chosen: string;
  outcome: string;
};

export type RailMetrics = {
  departures: number;
  late_departures: number;
  missed_departures: number;
  avg_departure_delay_s: number;
  max_departure_delay_s: number;
  signal_waits: number;
  platform_queue_s: number;
  alternate_platform_arrivals: number;
  trip_swaps: number;
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

/** A train's place in its plan at one instant. */
export type PlanPoint = {
  segment: PlanSegment;
  /** nose position along the segment's path */
  s: number;
  speed: number;
  path: PathPoint[];
};

/**
 * The simulator's ION run, able to place every physical train at any second.
 * Each train follows its movement plan from backend/rail_ops.py: line runs
 * between stations on the trip model (identical to the simulator's), moves
 * through a terminal's trackwork on the same speed profile, and stands.
 * Sections stay in physical order (section 0 is always cab A), so a train
 * never spins when it changes ends.
 */
export class IonTimetable {
  readonly schedule: IonSchedule;
  readonly tracks: Record<Direction, IonTrack>;
  readonly terminals: TerminalsData;
  private readonly lines: Record<Direction, Line>;
  private readonly trips: Map<string, IonSchedule["trips"][number]>;

  constructor(tracks: Record<Direction, IonTrack>, schedule: IonSchedule, terminals: TerminalsData) {
    this.schedule = schedule;
    this.tracks = tracks;
    this.terminals = terminals;
    this.lines = {
      north: new Line(tracks.north, schedule.lines.north, schedule.dwell_s),
      south: new Line(tracks.south, schedule.lines.south, schedule.dwell_s),
    };
    this.trips = new Map(schedule.trips.map((trip) => [trip.id, trip]));
  }

  trip(id: string | null) {
    return id ? this.trips.get(id) : undefined;
  }

  segmentAt(plan: VehiclePlan, t: number) {
    for (const seg of plan.segments) {
      const end = seg.t1 ?? Infinity;
      if (seg.t0 <= t && t < end) return seg;
    }
    return null;
  }

  /** Where a train's nose is at t on the path of its current segment (null: not in service). */
  locate(plan: VehiclePlan, t: number): PlanPoint | null {
    const seg = this.segmentAt(plan, t);
    if (!seg) return null;
    const x = t - seg.t0;
    if (seg.kind === "run" && seg.line !== undefined && seg.leg !== undefined) {
      const run = this.lines[seg.line].legs[seg.leg].run;
      return { segment: seg, s: seg.s0 + run.distance(x), speed: run.speedAt(x), path: this.tracks[seg.line].path };
    }
    if (seg.line) return { segment: seg, s: seg.s0, speed: 0, path: this.tracks[seg.line].path };
    const route = this.terminals.terminals[seg.terminal as "fairway"].routes[seg.route as string];
    if (seg.kind === "move") {
      const profile = moveProfile(this.terminals, seg.terminal as string, seg.route as string, seg.s0, seg.s1);
      return { segment: seg, s: seg.s0 + profile.distance(x), speed: profile.speed(x), path: route.path };
    }
    return { segment: seg, s: seg.s0, speed: 0, path: route.path };
  }

  /** A path position as a distance along the line (rail_ops.line_nose). */
  lineNose(point: PlanPoint) {
    const seg = point.segment;
    if (seg.line) return point.s;
    const route = this.terminals.terminals[seg.terminal as "fairway"].routes[seg.route as string];
    return lineNose(route, point.s, this.tracks[route.line.direction].length_m);
  }

  /** Index of the cab with its headlights on (null while the operator changes ends). */
  headlights(seg: PlanSegment, t: number) {
    const last = ION_SECTIONS.length - 1;
    const front = seg.lead === "A" ? 0 : last;
    if (!seg.lights) return front;
    if (t < seg.lights.off) return front;
    if (t < seg.lights.on) return null;
    return front === 0 ? last : 0;
  }

  /** Every train in service `seconds` into the simulation. */
  trains(seconds: number): TrainPose[] {
    const out: TrainPose[] = [];
    for (const plan of this.schedule.vehicles) {
      const point = this.locate(plan, seconds);
      if (!point) continue;
      const seg = point.segment;
      const trip = this.trip(seg.trip);
      const direction = (trip?.direction ?? seg.line ?? "north") as Direction;
      const sections = articulatePath(point.path, point.s);
      const standing = seg.kind === "stand";
      const nextStation = standing ? seg.station : this.nextStation(seg, direction);
      out.push({
        id: plan.id,
        tripId: seg.trip ?? undefined,
        direction,
        nose: this.lineNose(point),
        speed: point.speed,
        station: standing ? seg.station : undefined,
        nextStation,
        delayMin: trip ? Math.max(0, trip.depart_s - trip.scheduled_s) / 60 : 0,
        hold: trip?.hold,
        sections: seg.lead === "A" ? sections : physical(sections),
        headlights: this.headlights(seg, seconds),
        terminal: seg.terminal,
        activity: seg.note,
        route: seg.route,
        s: point.s,
        moveEnd: seg.kind === "move" ? seg.s1 : undefined,
      });
    }
    return out;
  }

  private nextStation(seg: PlanSegment, direction: Direction) {
    if (seg.kind === "run" && seg.leg !== undefined) return this.lines[direction].legs[seg.leg].toStation;
    if (seg.route) {
      const route = this.terminals.terminals[seg.terminal as "fairway"].routes[seg.route];
      return route.kind === "arrive" ? this.terminals.terminals[seg.terminal as "fairway"].station : route.line.to_station;
    }
    return undefined;
  }

  /** The decisions about one train, in time order. */
  decisionsFor(vehicle: string) {
    return this.schedule.decisions.filter((d) => d.vehicle_id === vehicle);
  }
}

/** Sections of a train running cab B first, back in physical order (cab A first, facing A). */
export function physical(sections: SectionPose[]): SectionPose[] {
  return sections.slice().reverse().map((section) => ({
    front: section.rear,
    rear: section.front,
    centre: section.centre,
    heading: (section.heading + 180) % 360,
    length: section.length,
  }));
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
  /** the physical train (vehicle) */
  id: string;
  /** the trip it is running or about to run */
  tripId?: string;
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
  terminal?: string;
  route?: string;
  /** what the train is doing in a terminal (from its plan) */
  activity?: string;
  /** nose along the current terminal route, and where the granted move ends */
  s?: number;
  moveEnd?: number;
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
