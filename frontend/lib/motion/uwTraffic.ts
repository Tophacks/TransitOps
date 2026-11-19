import { CAR_LENGTH } from "./traffic.ts";
import { canMerge, followingSpeed, ROAD_GAP, ROAD_STEP, type LaneOccupant } from "./road.ts";

// Distances from the modeled north entrance of the southbound Phillip lane.
export const UW_ROAD = {
  busMerge: 100,
  accessMerge: 166,
  dwell: 242,
  stopLine: 435,
  end: 468,
} as const;

export const UW_BUS_LENGTH = 12.2;
const CRUISE = 11;
const DWELL_SECONDS = 14;
const BUS_READY = 12;
const ACCESS_TRAVEL = 4.5;
const EPSILON = 0.001;

type BusPhase = "approaching" | "waiting" | "driving" | "dwelling" | "departed";
type Vehicle = { id: string; kind: "car" | "bus"; s: number; length: number; speed: number };
type AccessCar = { id: string; arrivedAt: number };
type Waits = { bus: number; access: number; behindBus: number };

function occupant(vehicle: Vehicle): LaneOccupant {
  return {
    id: vehicle.id,
    front: vehicle.s + vehicle.length / 2,
    rear: vehicle.s - vehicle.length / 2,
    speed: vehicle.speed,
  };
}

/** Fixture scenario. Time advances in fixed steps and demand changes affect future arrivals. */
export class UwTraffic {
  private time = 0;
  private lane: Vehicle[] = [];
  private fixtureBus: Vehicle = { id: "fixture-bus", kind: "bus", s: UW_ROAD.busMerge, length: UW_BUS_LENGTH, speed: 0 };
  private phase: BusPhase = "approaching";
  private carId = 0;
  private access: AccessCar | null = null;
  private nextAccess = 7;
  private demandCredit = 1;
  private dwellUntil = 0;
  private hasDwelled = false;
  private waits: Waits = { bus: 0, access: 0, behindBus: 0 };
  private behindBus = 0;
  private redQueue = 0;

  private get red() {
    const phase = this.time % 54;
    return phase >= 11 && phase < 34;
  }

  private enterAtMerge(vehicle: Vehicle) {
    if (!canMerge(this.lane.map(occupant), occupant(vehicle))) return false;
    this.lane.push(vehicle);
    return true;
  }

  step(demand: number) {
    this.time += ROAD_STEP;
    // Light to heavy spacing is about 19 to 4 seconds. Credit carries across
    // control changes. A blocked entrance retains at most one pending arrival.
    this.demandCredit = Math.min(1, this.demandCredit + ROAD_STEP * Math.max(0, demand) / 10);
    if (this.demandCredit >= 1) {
      const entry = { id: `car-${this.carId}`, kind: "car" as const, s: 0, length: CAR_LENGTH, speed: CRUISE };
      if (this.lane.every((vehicle) => occupant(vehicle).rear - CAR_LENGTH / 2 >= ROAD_GAP)) {
        this.lane.push(entry);
        this.carId += 1;
        this.demandCredit = 0;
      }
    }

    if (this.time >= BUS_READY && (this.phase === "approaching" || this.phase === "waiting")) {
      this.phase = this.enterAtMerge(this.fixtureBus) ? "driving" : "waiting";
      if (this.phase === "waiting") this.waits.bus += ROAD_STEP;
    }
    if (!this.access && this.time >= this.nextAccess) {
      this.access = { id: `car-${this.carId++}`, arrivedAt: this.time };
    }
    if (this.access && this.time - this.access.arrivedAt >= ACCESS_TRAVEL) {
      const car = { id: this.access.id, kind: "car" as const, s: UW_ROAD.accessMerge, length: CAR_LENGTH, speed: 0 };
      if (this.enterAtMerge(car)) {
        this.access = null;
        this.nextAccess = this.time + 10;
      } else {
        this.waits.access += ROAD_STEP;
      }
    }
    if (this.phase === "dwelling" && this.time >= this.dwellUntil) this.phase = "driving";

    this.lane.sort((a, b) => b.s - a.s);
    let leader: Vehicle | undefined;
    let behindBus = 0;
    let redQueue = 0;
    let queueCause: "bus" | "red" | null = null;
    // Advance front to back. Read the leader's starting position to brake, then
    // cap movement against its updated rear. No overtaking on this lane.
    let leaderRearBefore = Infinity;
    for (const vehicle of this.lane) {
      const body = occupant(vehicle);
      const signalLimit = this.red && body.front <= UW_ROAD.stopLine
        ? Math.max(vehicle.s, UW_ROAD.stopLine - vehicle.length / 2 - 0.6)
        : Infinity;
      const dwellLimit = vehicle === this.fixtureBus && !this.hasDwelled ? UW_ROAD.dwell : Infinity;
      const aheadLimit = leader ? occupant(leader).rear - vehicle.length / 2 - ROAD_GAP : Infinity;
      const followingLimit = leaderRearBefore - vehicle.length / 2 - ROAD_GAP;
      const gap = Math.min(signalLimit, dwellLimit, followingLimit) - vehicle.s;
      const speed = this.phase === "dwelling" && vehicle === this.fixtureBus
        ? 0
        : followingSpeed(vehicle.speed, vehicle.kind === "bus" ? 9 : CRUISE, gap);
      const advance = Math.min(speed * ROAD_STEP, Math.max(0, Math.min(signalLimit, dwellLimit, aheadLimit) - vehicle.s));
      vehicle.speed = advance / ROAD_STEP <= EPSILON ? 0 : advance / ROAD_STEP;
      vehicle.s += vehicle.speed * ROAD_STEP;
      // Begin the dwell once the bus settles within the following buffer.
      if (dwellLimit - vehicle.s <= 0.31) {
        vehicle.speed = 0;
        this.hasDwelled = true;
        this.phase = "dwelling";
        this.dwellUntil = this.time + DWELL_SECONDS;
      }
      if (vehicle.speed <= EPSILON) {
        if (vehicle === this.fixtureBus && this.phase === "dwelling") queueCause = "bus";
        else if (signalLimit - vehicle.s <= 0.31) queueCause = "red";
        else if (followingLimit - vehicle.s > 0.31 || !leader || leader.speed > EPSILON) queueCause = null;
        if (vehicle.kind === "car" && queueCause === "bus") behindBus += 1;
        if (vehicle.kind === "car" && queueCause === "red") redQueue += 1;
      } else {
        queueCause = null;
      }
      leader = vehicle;
      leaderRearBefore = body.rear;
    }
    if (behindBus > 0) this.waits.behindBus += ROAD_STEP;
    this.behindBus = behindBus;
    this.redQueue = redQueue;
    this.lane = this.lane.filter((vehicle) => {
      if (occupant(vehicle).rear <= UW_ROAD.end) return true;
      if (vehicle === this.fixtureBus) this.phase = "departed";
      return false;
    });
    return this.frame();
  }

  frame() {
    return {
      time: this.time,
      red: this.red,
      carsBehindBus: this.behindBus,
      carsAtRed: this.redQueue,
      busPhase: this.phase,
      waits: { ...this.waits },
      lane: this.lane.map(occupant),
      vehicles: this.lane.map(({ id, s, kind, speed }) => ({ id, s, kind, speed })),
      busApproach: this.phase === "approaching" || this.phase === "waiting"
        ? Math.min(1, this.time / BUS_READY) : null,
      accessCar: this.access && { id: this.access.id, progress: Math.min(1, (this.time - this.access.arrivedAt) / ACCESS_TRAVEL) },
    };
  }
}
