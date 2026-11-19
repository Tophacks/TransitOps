import { LaneTraffic, type MergeEvent } from "./laneTraffic.ts";
import { ROAD_GAP, ROAD_STEP } from "./road.ts";
import { CAR_LENGTH } from "./traffic.ts";
import { fixtureLanes, UW_BERTH_FRONT, UW_BUS_ROUTE, UW_CAR_ROUTE, UW_LANES } from "./uwLanes.ts";

export const UW_BUS_LENGTH = 12.2;
export const FIXTURE_BUS_ID = "fixture-bus";
export const TRAFFIC_SCENARIOS = {
  station: "UW Station",
  shared: "Synthetic · shared travel lane",
  bay: "Synthetic · separate pullout bay",
} as const;
export type TrafficScenario = keyof typeof TRAFFIC_SCENARIOS;

const DWELL_SECONDS = 30;
const CRUISE = 11;

/** Browser and tests use the same seeded arrivals and fixed-step engine. */
export class UwTraffic {
  readonly road: LaneTraffic;
  readonly lanes;
  private readonly carRoute;
  private readonly stopAt;
  private seed: number;
  private carId = 1;
  private demandCredit = 1;
  private nextArrival = 1;
  private dwellUntil: number | undefined;
  private busWait = 0;
  private lastWait?: MergeEvent;

  readonly scenario: TrafficScenario;

  constructor(scenario: TrafficScenario = "station", seed = 42) {
    this.scenario = scenario;
    this.seed = seed >>> 0;
    this.lanes = scenario === "station" ? UW_LANES : fixtureLanes(scenario === "bay");
    this.road = new LaneTraffic(this.lanes);
    this.carRoute = scenario === "station" ? UW_CAR_ROUTE : ["fixture-in", "fixture-out", "fixture-exit"];
    const route = scenario === "station" ? UW_BUS_ROUTE
      : scenario === "shared" ? this.carRoute.slice(1)
        : ["fixture-bay", "fixture-rejoin", "fixture-exit"];
    this.stopAt = { laneId: scenario === "station" ? "berth-1223" : route[0],
      front: scenario === "station" ? UW_BERTH_FRONT : 36 };
    this.road.addVehicle({
      id: FIXTURE_BUS_ID, kind: "bus", route, length: UW_BUS_LENGTH, speed: 0,
      front: scenario === "station" ? 0 : this.stopAt.front,
    });
    if (scenario !== "station") this.dwellUntil = DWELL_SECONDS;
  }

  private get red() {
    // Representative cycle, independent of the corridor timetable and real timing.
    const phase = this.road.time % 54;
    return this.scenario === "station" && phase >= 11 && phase < 34;
  }

  step(demand: number) {
    this.demandCredit = Math.min(this.nextArrival, this.demandCredit + ROAD_STEP * Math.max(0, demand) / 10);
    if (demand > 0 && this.demandCredit >= this.nextArrival && this.road.addVehicle({
      id: `car-${this.carId}`, kind: "car", route: this.carRoute, length: CAR_LENGTH,
      speed: CRUISE, front: CAR_LENGTH,
    })) {
      this.carId += 1;
      this.demandCredit = 0;
      this.seed = (Math.imul(1664525, this.seed) + 1013904223) >>> 0;
      this.nextArrival = 0.64 + 0.72 * this.seed / 4294967296;
    }

    const leaving = this.dwellUntil !== undefined && this.road.time >= this.dwellUntil;
    if (this.scenario === "station" && this.road.time < 1) {
      this.road.setMotion(FIXTURE_BUS_ID, { stopAt: { laneId: "ring-entry", front: 0 } });
    } else {
      this.road.setMotion(FIXTURE_BUS_ID, leaving ? {} : { stopAt: this.stopAt });
    }
    const redAtNextTick = (this.road.time + ROAD_STEP) % 54;
    this.road.step(this.scenario === "station" && redAtNextTick >= 11 && redAtNextTick < 34
      ? new Set(["phillip-out"]) : undefined);
    const state = this.road.frame();
    const bus = state.vehicles.find(({ id }) => id === FIXTURE_BUS_ID);
    if (this.dwellUntil === undefined && bus?.laneId === this.stopAt.laneId &&
      this.stopAt.front - bus.front <= 0.31 + 1e-7 && bus.speed < 0.001) {
      this.dwellUntil = this.road.time + DWELL_SECONDS;
    }
    const waiting = state.waiting.find(({ vehicleId }) => vehicleId === FIXTURE_BUS_ID);
    if (waiting) {
      this.busWait += ROAD_STEP;
      this.lastWait = waiting;
    }
    return this.frame(state);
  }

  frame(state = this.road.frame()) {
    const bus = state.vehicles.find(({ id }) => id === FIXTURE_BUS_ID);
    const waiting = state.waiting.find(({ vehicleId }) => vehicleId === FIXTURE_BUS_ID);
    const dwelling = this.dwellUntil !== undefined && state.time < this.dwellUntil;
    let carsBehindBus = 0;
    if (dwelling && bus && this.scenario === "shared") {
      const upstreamLength = this.road.lane("fixture-in").length;
      const cars = [...state.lanes["fixture-out"], ...state.lanes["fixture-in"].map((body) =>
        ({ ...body, front: body.front - upstreamLength, rear: body.rear - upstreamLength }))];
      let rear = bus.front - bus.length;
      for (const car of cars) {
        if (car.kind !== "car" || car.front > rear) continue;
        if (car.speed > 0.001 || rear - car.front > ROAD_GAP + 0.32) break;
        carsBehindBus += 1;
        rear = car.rear;
      }
    }
    return { ...state, bus, waiting, dwelling, carsBehindBus, busWait: this.busWait,
      lastBlockers: this.lastWait ? [...this.lastWait.blockers] : [], red: this.red };
  }
}
