import { strict as assert } from "node:assert";
import { test } from "node:test";

import { LaneTraffic } from "../lib/motion/laneTraffic.ts";
import { canMerge, ROAD_GAP, ROAD_STEP } from "../lib/motion/road.ts";
import { fixtureLanes, UW_BUS_ROUTE, UW_LANES } from "../lib/motion/uwLanes.ts";
import { FIXTURE_BUS_ID, UwTraffic } from "../lib/motion/uwTraffic.ts";

function advance(sim, demand, seconds) {
  return Array.from({ length: seconds / ROAD_STEP }, () => sim.step(demand));
}

function assertClearance(frames, lanes) {
  for (const frame of frames) {
    for (const { id, access } of lanes) {
      const bodies = frame.lanes[id];
      for (const [i, body] of bodies.entries()) {
        assert.ok(body.front >= body.rear);
        if (access === "bus") assert.equal(body.kind, "bus");
        if (i > 0) assert.ok(bodies[i - 1].rear - body.front >= ROAD_GAP - 1e-7,
          `time ${frame.time}, ${id}, ${bodies[i - 1].id} ahead of ${body.id}`);
      }
    }
  }
}

test("merge requires clearance ahead and time for drivers approaching behind", () => {
  const bus = { id: "bus", rear: 94, front: 106, speed: 0 };
  const car = { id: "car", rear: 73, front: 78, speed: 11 };
  assert.equal(canMerge([car], bus), false);
  assert.equal(canMerge([{ ...car, speed: 1 }], bus), true);
  assert.equal(canMerge([{ ...car, rear: 103, front: 108 }], bus), false);
  assert.equal(canMerge([{ ...car, rear: 107, front: 112 }], bus), false);
  assert.equal(canMerge([{ ...car, rear: 110, front: 115 }], bus), true);
});

test("synthetic shared lane queues cars at the bus rear and releases them after dwell", () => {
  const sim = new UwTraffic("shared", 42);
  const frames = advance(sim, 2.5, 65);
  const queued = frames.find((frame) => frame.carsBehindBus >= 2);
  assert.ok(queued?.dwelling);
  const [bus, car] = queued.lanes["fixture-out"];
  assert.equal(bus.id, FIXTURE_BUS_ID);
  assert.equal(bus.speed, 0);
  assert.equal(car.speed, 0);
  assert.ok(bus.rear - car.front >= ROAD_GAP - 1e-7);
  assert.ok(bus.rear - car.front <= ROAD_GAP + 0.32);
  assert.ok(frames.some((frame) => !frame.dwelling && frame.vehicles.some((v) => v.id === car.id && v.speed > 1)));
  assertClearance(frames, sim.lanes);
});

test("synthetic separate bay leaves the travel lane open through the same dwell", () => {
  const sim = new UwTraffic("bay", 42);
  const frames = advance(sim, 2.5, 65);
  const passing = frames.find((frame) => frame.dwelling && frame.lanes["fixture-exit"].length > 0);
  assert.ok(passing);
  assert.equal(passing.lanes["fixture-bay"][0].id, FIXTURE_BUS_ID);
  assert.equal(passing.lanes["fixture-bay"][0].speed, 0);
  assert.ok(passing.lanes["fixture-exit"].every(({ speed }) => speed > 1));
  assert.ok(frames.every((frame) => frame.carsBehindBus === 0));
  assertClearance(frames, sim.lanes);
});

test("same UW berth departure yields to identified Phillip Street cars in heavy traffic", () => {
  const light = new UwTraffic("station", 42);
  const heavy = new UwTraffic("station", 42);
  const lightFrames = advance(light, 0.55, 150);
  const heavyFrames = advance(heavy, 2.5, 150);
  const firstWaiting = heavyFrames.find((frame) => frame.waiting);
  assert.ok(firstWaiting);
  assert.equal(firstWaiting.waiting.from, "phillip-right");
  assert.equal(firstWaiting.waiting.reason, "traffic-gap");
  assert.equal(firstWaiting.bus.laneId, "phillip-right");
  assert.equal(firstWaiting.lanes["phillip-out"].some((body) => body.kind === "bus"), false);
  for (const id of firstWaiting.waiting.blockers) {
    assert.ok(firstWaiting.vehicles.some((v) => v.id === id && v.kind === "car"));
  }
  const dwellEnd = (frames) => frames.findIndex((frame, i) => !frame.dwelling && frames[i - 1]?.dwelling);
  assert.equal(dwellEnd(lightFrames), dwellEnd(heavyFrames));
  const entered = (sim) => sim.road.mergeEvents().find(({ status }) => status === "entered");
  assert.ok(entered(light));
  assert.ok(entered(heavy).time > entered(light).time);
  assert.ok(heavy.frame().busWait > light.frame().busWait);
  assert.ok(heavy.road.mergeEvents().some(({ status, blockers }) => status === "waiting" && blockers.length > 0));
  assertClearance(heavyFrames, UW_LANES);
});

test("lowering demand while the bus yields opens an earlier gap in the live simulation", () => {
  const sim = new UwTraffic("station", 42);
  while (!sim.frame().waiting && sim.road.time < 112) sim.step(2.5);
  assert.ok(sim.frame().waiting);
  assert.ok(advance(sim, 0.55, 16).some((frame) => frame.merges.length));
  assert.ok(sim.road.mergeEvents().find(({ status }) => status === "entered").time < 116);
});

test("private cars cannot use a connected bus-only branch or spawn at a UW entrance", () => {
  const car = { id: "car", kind: "car", length: 4.6, speed: 0, front: 0 };
  const road = new LaneTraffic(fixtureLanes(true));
  assert.throws(() => road.addVehicle({ ...car, route: ["fixture-in", "fixture-bay"] }), /Bus-only lane/);
  const uw = new LaneTraffic(UW_LANES);
  assert.throws(() => uw.addVehicle({ ...car, route: UW_BUS_ROUTE }), /Bus-only lane ring-entry/);
  assert.throws(() => uw.addVehicle({ ...car, route: ["phillip-in", "plaza-east"] }), /Bus-only lane/);
  assert.deepEqual(uw.frame().vehicles, []);
});

test("a bus holds the merge across ticks until its rear clears the exit connector", () => {
  const sim = new UwTraffic("station", 42);
  const frames = advance(sim, 2.5, 135);
  const entered = frames.findIndex(({ merges }) => merges.length > 0);
  assert.ok(entered >= 0);
  const cleared = frames.findIndex(({ merges }, i) => i > entered && merges.length === 0);
  assert.ok(cleared > entered + 1);
  for (const frame of frames.slice(entered, cleared)) {
    assert.ok(frame.bus.front < frame.bus.length);
    assert.equal(frame.merges[0].vehicleId, FIXTURE_BUS_ID);
    assert.ok(frame.lanes["phillip-right"].some((body) => body.id === FIXTURE_BUS_ID && body.front > body.rear));
    assert.ok(frame.lanes["phillip-out"].some((body) => body.id === FIXTURE_BUS_ID));
  }
  const frame = frames[cleared];
  assert.ok(frame.bus.front >= frame.bus.length);
  assert.ok(frame.lanes["phillip-right"].every((body) => body.id !== FIXTURE_BUS_ID));
  assert.equal(sim.road.mergeEvents().find(({ status }) => status === "cleared").time, frame.time);
});

test("through traffic cannot enter the merge while a stopped bus rear occupies the connector", () => {
  const sim = new UwTraffic("station", 42);
  while (!sim.road.mergeOwner("phillip-out") && sim.road.time < 115) sim.step(0.55);
  const { road } = sim;
  assert.equal(road.mergeOwner("phillip-out")?.vehicleId, FIXTURE_BUS_ID);
  const edge = road.lane("phillip-in").length;
  assert.ok(road.addVehicle({
    id: "through-car", kind: "car", route: ["phillip-in", "phillip-out"],
    front: edge - 5, speed: 2, length: 4.6,
  }));
  road.setMotion(FIXTURE_BUS_ID, { speedLimit: 0 });
  for (let i = 0; i < 8; i += 1) {
    road.step();
    assert.equal(road.mergeOwner("phillip-out")?.vehicleId, FIXTURE_BUS_ID);
    assert.ok(road.occupancy("phillip-in").some(({ id }) => id === "through-car"));
    assert.ok(road.occupancy("phillip-out").every(({ id }) => id !== "through-car"));
  }
  road.setMotion(FIXTURE_BUS_ID, {});
  for (let i = 0; i < 16; i += 1) road.step();
  assert.equal(road.mergeOwner("phillip-out"), undefined);
  assert.ok(road.occupancy("phillip-out").some(({ id }) => id === "through-car"));
});

test("Phillip Street cars stop on red and cross the line after green", () => {
  const sim = new UwTraffic("station", 42);
  const stopLine = sim.road.lane("phillip-out").signalAt;
  const frames = advance(sim, 2.5, 96);
  const red = frames.find((frame) => frame.red && frame.lanes["phillip-out"].some(({ front, speed }) =>
    front <= stopLine && front > stopLine - 0.31 && speed < 0.001));
  assert.ok(red);
  const stopped = red.lanes["phillip-out"].find(({ front, speed }) =>
    front <= stopLine && front > stopLine - 0.31 && speed < 0.001);
  const crossing = frames.find((frame) => frame.time > red.time &&
    frame.lanes["phillip-out"].some(({ id, front }) => id === stopped.id && front > stopLine));
  assert.ok(crossing);
  assert.equal(crossing.red, false);
});

test("seed and demand history reproduce identical frames and decisions", () => {
  function run(seed) {
    const sim = new UwTraffic("station", seed);
    const frames = [...advance(sim, 2.5, 100), ...advance(sim, 0.55, 40)];
    return { frames, events: sim.road.mergeEvents() };
  }
  const first = run(42);
  assert.deepEqual(first, run(42));
  assert.notDeepEqual(first.frames[19], run(17).frames[19]);
});
