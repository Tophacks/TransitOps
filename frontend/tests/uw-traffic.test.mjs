import { strict as assert } from "node:assert";
import { test } from "node:test";

import { canMerge, ROAD_GAP, ROAD_STEP } from "../lib/motion/road.ts";
import { UW_ROAD, UwTraffic } from "../lib/motion/uwTraffic.ts";

function advance(sim, demand, seconds) {
  return Array.from({ length: seconds / ROAD_STEP }, () => sim.step(demand));
}

test("merge requires clearance ahead and enough time from a car approaching behind", () => {
  const bus = { id: "bus", rear: 94, front: 106, speed: 0 };
  const car = { id: "car", rear: 73, front: 78, speed: 11 };
  assert.equal(canMerge([car], bus), false);
  assert.equal(canMerge([{ ...car, speed: 1 }], bus), true);
  assert.equal(canMerge([{ ...car, rear: 103, front: 108 }], bus), false);
  assert.equal(canMerge([{ ...car, rear: 107, front: 112 }], bus), false);
  assert.equal(canMerge([{ ...car, rear: 110, front: 115 }], bus), true);
});

test("reducing traffic mid-run releases the yielding bus and cars queue behind its dwell", () => {
  const changing = new UwTraffic();
  const heavy = new UwTraffic();
  advance(heavy, 2.5, 16);
  const waiting = advance(changing, 2.5, 16).at(-1);
  assert.deepEqual(waiting, heavy.frame());
  assert.equal(waiting.busPhase, "waiting");
  assert.equal(waiting.lane.some(({ id }) => id === "fixture-bus"), false);
  assert.ok(waiting.waits.bus > 0);

  const lighterFrames = advance(changing, 0.55, 50);
  const stillHeavy = advance(heavy, 2.5, 50).at(-1);
  const released = lighterFrames.find((frame) => frame.busPhase === "driving");
  assert.ok(released);
  assert.ok(released.time > waiting.time);
  assert.ok(released.waits.bus < stillHeavy.waits.bus);
  assert.equal(stillHeavy.busPhase, "waiting");

  const queued = lighterFrames.find((frame) => frame.carsBehindBus >= 2);
  assert.ok(queued);
  assert.equal(queued.busPhase, "dwelling");
  const bus = queued.lane.find(({ id }) => id === "fixture-bus");
  assert.equal(bus.speed, 0);
  const first = queued.lane[queued.lane.indexOf(bus) + 1];
  assert.equal(first.speed, 0);
  assert.ok(bus.rear - first.front >= ROAD_GAP - 1e-7);
  assert.ok(bus.rear - first.front <= ROAD_GAP + 0.31);

  const resumed = lighterFrames.find((frame) =>
    frame.time > queued.time && frame.lane.find(({ id }) => id === first.id)?.speed > 1);
  assert.ok(resumed);
  assert.equal(resumed.busPhase, "driving");
});

test("an access car stays off the through lane to yield, then enters when a gap opens", () => {
  const sim = new UwTraffic();
  let waiting = sim.frame();
  while (waiting.time < 35 && waiting.waits.access === 0) waiting = sim.step(2.5);
  assert.ok(waiting.waits.access > 0);
  assert.equal(waiting.accessCar.progress, 1);
  const id = waiting.accessCar.id;
  assert.ok(!waiting.lane.some((body) => body.id === id));
  const admitted = advance(sim, 0.55, 55).find((frame) => frame.lane.some((body) => body.id === id));
  assert.ok(admitted);
  assert.equal(admitted.vehicles.find((car) => car.id === id).kind, "car");
});

test("cars stop before red, resume on green and preserve clearance under changing demand", () => {
  const sim = new UwTraffic();
  const frames = [...advance(sim, 2.5, 16), ...advance(sim, 0.55, 70), ...advance(sim, 2.5, 110)];
  let prev = new Map();
  for (const frame of frames) {
    for (const [index, body] of frame.lane.entries()) {
      if (index > 0) assert.ok(frame.lane[index - 1].rear - body.front >= ROAD_GAP - 1e-7);
      const prior = prev.get(body.id);
      if (prior) {
        assert.ok(body.front >= prior.front);
        if (frame.red && prior.front <= UW_ROAD.stopLine) assert.ok(body.front <= UW_ROAD.stopLine);
      }
    }
    prev = new Map(frame.lane.map((body) => [body.id, body]));
  }
  const red = frames.find((frame) => frame.red && frame.carsAtRed > 0);
  assert.ok(red);
  const front = red.lane.find((body) => body.speed === 0 && UW_ROAD.stopLine - body.front < 1);
  assert.ok(front);
  assert.ok(frames.some((frame) => frame.time > red.time && !frame.red &&
    frame.lane.some((body) => body.id === front.id && body.front > UW_ROAD.stopLine)));
});
