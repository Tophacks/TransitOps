// Fairway terminal replay: one ION train turning back through the real
// trackwork and one GRT bus laying over, changing its sign and pulling out.
//
// Geometry comes from tools/waterloo_geometry/build_fairway.py (OpenStreetMap
// track, platforms and bus lane; GRT GTFS for the layover). The operating
// sequence is modelled: trains arrive straight into platform 1 and leave
// through crossover 754590785; the timings are compressed for the replay.
//
// Everything here is a pure function of replay time, so pausing, scrubbing and
// replaying stay exact.

import { BUS_ACCEL, BUS_DECEL } from "./kinematics.ts";
import { ION_ACCEL, ION_DECEL, ION_LENGTH, ION_SECTIONS, articulatePath, type SectionPose, type TrainPose } from "./ion.ts";
import { BUS_LENGTH } from "./bodies.ts";
import { pointAt, tangentAt, type LonLat, type PathPoint } from "./path.ts";

export type FairwayEdge = { ways: number[]; from: number | null; to: number | null; path: PathPoint[] };
export type FairwayMove = {
  edges: { edge: string; reversed: boolean; from_m: number }[];
  path: PathPoint[];
  curves: [number, number][];
};
export type FairwayData = {
  attribution: string;
  track: {
    edges: Record<string, FairwayEdge>;
    switches: { node: number; lon: number; lat: number; legs: string[] }[];
    diamond: { node: number; lon: number; lat: number };
    buffers: { node: number; lon: number; lat: number }[];
  };
  turnback: {
    modeled: boolean;
    platform: string;
    arrival: FairwayMove & { stop_m: number };
    departure: FairwayMove & { start_m: number };
    train_length_m: number;
  };
  bus: {
    path: PathPoint[];
    curves: [number, number][];
    lane_end_m: number;
    bay_pole_m: number;
    lane_ways: number[];
    exit_ways: number[];
    layover: {
      route: string;
      arriving_headsign: string;
      next_headsign: string;
      layovers_counted: number;
      median_layover_min: number;
      blocks_ending_here: number;
      stop: { id: string; name: string; lon: number; lat: number };
    };
  };
};

export { Profile } from "./profile.ts";
import { Profile } from "./profile.ts";

// Speeds (m/s). The ION approach is slowed to terminal speed well before the
// switches; the whole train stays at switch speed until its tail clears them.
const APPROACH_SPEED = 12.0;
const SWITCH_SPEED = 6.9;     // 25 km/h through the crossover and along the platform
const BUS_LANE_SPEED = 7.0;
const BUS_MIN_CORNER = 3.0;

/** Replay timetable (seconds). Dwell and layover are compressed: modelled. */
export const FAIRWAY_TIMES = {
  arrivalStartM: 250,         // the train enters the replay this far along the arrival move
  dwellS: 38,                 // stopped at platform 1
  cabOffS: 8,                 // headlights off at the arrival end (operator leaves the cab)
  cabOnS: 15,                 // headlights on at the departure end
  signChangeS: 17,            // after the train stops: bus sign changes to the next trip
  pullOutS: 25,               // after the train stops: bus pulls out
  departTailS: 34,            // seconds of the departure shown
};

export type NextTrip = "route" | "garage";

export type Sign = { route: string; destination: string; short: string };

export type FairwayTrain = TrainPose & {
  phase: "arriving" | "stopped" | "departing";
  /** index of the cab with its headlights on (null: both ends show tail lights) */
  headlights: number | null;
  /** the move the train is on and its nose position along it */
  move: "arrival" | "departure";
  s: number;
  edge: string;
};

export type FairwayBus = {
  id: string;
  centre: LonLat;
  front: LonLat;
  heading: number;
  speed: number;
  /** front of the bus along the lane path */
  s: number;
  phase: "layover" | "departing";
  sign: Sign;
  nextSign: Sign;
  opacity: number;
  /** seconds until the bus leaves (layover only) */
  leavesIn: number;
};

export type FairwayScene = {
  t: number;
  train: FairwayTrain;
  bus: FairwayBus;
  caption: string;
};

function shortName(headsign: string) {
  return headsign.replace(/\s+(Station|Terminal|Loop)$/i, "");
}

export function sign(route: string, headsign: string): Sign {
  return { route, destination: headsign, short: shortName(headsign) };
}

export const NOT_IN_SERVICE: Sign = { route: "", destination: "Not in Service", short: "Not in Service" };

export const FAIRWAY_TRAIN_ID = "ION-FW1";
export const FAIRWAY_BUS_ID = "GRT-7-FW";

function edgeAt(move: FairwayMove, s: number) {
  let name = move.edges[0].edge;
  for (const e of move.edges) if (s >= e.from_m) name = e.edge;
  return name;
}

/** Turn the sections of a train running cab B first back into physical order (cab A first, facing A). */
function physical(sections: SectionPose[]): SectionPose[] {
  return sections.slice().reverse().map((section) => ({
    front: section.rear,
    rear: section.front,
    centre: section.centre,
    heading: (section.heading + 180) % 360,
    length: section.length,
  }));
}

export class FairwayReplay {
  readonly data: FairwayData;
  readonly arrival: Profile;
  readonly departure: Profile;
  readonly pullOut: Profile;
  readonly stopAt: number;      // replay second the train stops
  readonly departAt: number;
  readonly duration: number;
  readonly busParkedFront: number;
  readonly arriving: Sign;
  readonly nextTrip: Sign;

  constructor(data: FairwayData, next: NextTrip = "route") {
    this.data = data;
    const { arrival, departure } = data.turnback;
    const T = FAIRWAY_TIMES;
    const switchFrom = arrival.edges.find((e) => e.edge !== arrival.edges[0].edge)?.from_m ?? 0;
    this.arrival = new Profile(arrival.stop_m - T.arrivalStartM, (x) => {
      const s = x + T.arrivalStartM;
      return s >= switchFrom - 25 ? SWITCH_SPEED : APPROACH_SPEED;
    }, { v0: APPROACH_SPEED, stop: true, accel: ION_ACCEL, decel: ION_DECEL });
    // Departure: switch speed until the tail has cleared the crossover.
    const lastEdge = departure.edges[departure.edges.length - 1].from_m;
    const curveCap = (s: number) => departure.curves
      .filter(([at]) => at <= s && at >= s - ION_LENGTH)
      .reduce((m, [, v]) => Math.min(m, Math.max(v, SWITCH_SPEED * 0.85)), Infinity);
    const depLength = Math.min(departure.path[departure.path.length - 1][2] - departure.start_m, 320);
    this.departure = new Profile(depLength, (x) => {
      const s = x + departure.start_m;
      return Math.min(s <= lastEdge + ION_LENGTH + 2 ? SWITCH_SPEED : APPROACH_SPEED, curveCap(s));
    }, { v0: 0, stop: false, accel: ION_ACCEL, decel: ION_DECEL });
    this.stopAt = this.arrival.duration;
    this.departAt = this.stopAt + T.dwellS;
    this.duration = Math.ceil(this.departAt + T.departTailS);
    // The bus parks with its front just past the bay's pole.
    const bus = data.bus;
    this.busParkedFront = bus.bay_pole_m + 1.0;
    const busLength = bus.path[bus.path.length - 1][2] - this.busParkedFront;
    this.pullOut = new Profile(busLength, (x) => {
      const s = x + this.busParkedFront;
      const corner = bus.curves.filter(([at]) => Math.abs(at - s) < 6).reduce((m, [, v]) => Math.min(m, Math.max(v, BUS_MIN_CORNER)), Infinity);
      return Math.min(BUS_LANE_SPEED, corner);
    }, { v0: 0, stop: false, accel: BUS_ACCEL, decel: BUS_DECEL });
    const layover = bus.layover;
    this.arriving = sign(layover.route, layover.arriving_headsign);
    this.nextTrip = next === "garage" ? NOT_IN_SERVICE : sign(layover.route, layover.next_headsign);
  }

  get signChangeAt() {
    return this.stopAt + FAIRWAY_TIMES.signChangeS;
  }

  get pullOutAt() {
    return this.stopAt + FAIRWAY_TIMES.pullOutS;
  }

  get lightsOffAt() {
    return this.stopAt + FAIRWAY_TIMES.cabOffS;
  }

  get lightsOnAt() {
    return this.stopAt + FAIRWAY_TIMES.cabOnS;
  }

  train(t: number): FairwayTrain {
    const { arrival, departure } = this.data.turnback;
    const last = ION_SECTIONS.length - 1;
    if (t < this.departAt) {
      const moving = t < this.stopAt;
      const s = moving ? this.data.turnback.arrival.stop_m - this.arrival.length + this.arrival.distance(t) : arrival.stop_m;
      const headlights = t < this.lightsOffAt ? 0 : t < this.lightsOnAt ? null : last;
      return {
        id: FAIRWAY_TRAIN_ID,
        direction: "south",
        nose: s,
        speed: moving ? this.arrival.speed(t) : 0,
        station: moving ? undefined : "Fairway",
        nextStation: "Fairway",
        sections: articulatePath(arrival.path, s),
        phase: moving ? "arriving" : "stopped",
        headlights,
        move: "arrival",
        s,
        edge: edgeAt(arrival, s),
      };
    }
    const x = t - this.departAt;
    const s = departure.start_m + this.departure.distance(x);
    return {
      id: FAIRWAY_TRAIN_ID,
      direction: "north",
      nose: s,
      speed: this.departure.speed(x),
      nextStation: "Block Line",
      sections: physical(articulatePath(departure.path, s)),
      phase: "departing",
      headlights: last,
      move: "departure",
      s,
      edge: edgeAt(departure, s),
    };
  }

  bus(t: number): FairwayBus {
    const path = this.data.bus.path;
    const moving = t > this.pullOutAt;
    const x = Math.max(0, t - this.pullOutAt);
    const s = this.busParkedFront + (moving ? this.pullOut.distance(x) : 0);
    const end = path[path.length - 1][2];
    const centreS = s - BUS_LENGTH / 2;
    const [tx, ty] = tangentAt(path, centreS, BUS_LENGTH / 2 - 0.5);
    const heading = ((Math.atan2(tx, ty) * 180) / Math.PI + 360) % 360;
    const signNow = t >= this.signChangeAt ? this.nextTrip : this.arriving;
    return {
      id: FAIRWAY_BUS_ID,
      centre: pointAt(path, centreS),
      front: pointAt(path, s),
      heading,
      speed: moving ? this.pullOut.speed(x) : 0,
      s,
      phase: moving ? "departing" : "layover",
      sign: signNow,
      nextSign: this.nextTrip,
      // Fades over the last 20 m, where it leaves the replay's frame.
      opacity: Math.max(0, Math.min(1, (end - s) / 20)),
      leavesIn: Math.max(0, this.pullOutAt - t),
    };
  }

  caption(t: number, train: FairwayTrain, bus: FairwayBus) {
    const parts: string[] = [];
    if (train.phase === "arriving") {
      parts.push(train.speed > SWITCH_SPEED + 0.2 ? "Train approaching on the south track"
        : train.edge === "platform_1" ? "Train braking into platform 1" : "Train through the switches");
    } else if (train.phase === "stopped") {
      parts.push(t < this.lightsOffAt ? "Train stopped at platform 1"
        : t < this.lightsOnAt ? "Operator changing ends" : "Headlights on at the other end");
    } else {
      parts.push(train.edge === "crossover_nw_se" ? "Train crossing over to the north track" : "Train departing northbound");
    }
    if (bus.phase === "layover") {
      parts.push(t >= this.signChangeAt ? `Bus sign changed · ${bus.sign.route ? `${bus.sign.route} ` : ""}${bus.sign.short}` : "Bus on layover");
    } else if (bus.opacity > 0) {
      parts.push("Bus pulling out along the bus lane");
    }
    return parts.join(" · ");
  }

  scene(t: number): FairwayScene {
    const time = Math.min(this.duration, Math.max(0, t));
    const train = this.train(time);
    const bus = this.bus(time);
    return { t: time, train, bus, caption: this.caption(time, train, bus) };
  }
}
