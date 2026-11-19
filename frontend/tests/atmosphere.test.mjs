import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { BUS_FRONT_M, Corridor, holdPosition } from "../lib/motion/corridor.ts";
import { busPoses } from "../lib/motion/playback.ts";
import { CAR_LENGTH, TrafficStream, queuedCars } from "../lib/motion/traffic.ts";
import { DOWN_M, UP_M, WARN_M, gateStates, loweredFor } from "../lib/motion/gates.ts";
import { IonTimetable, ION_LENGTH } from "../lib/motion/ion.ts";

const data = JSON.parse(readFileSync(new URL("../lib/geo/waterloo-corridor.json", import.meta.url)));
const parity = JSON.parse(readFileSync(new URL("./fixtures/kinematics-parity.json", import.meta.url)));
const corridor = new Corridor(data);
const { timeline, traffic_multiplier: traffic } = parity.traffic;
const phaseAt = (id, minute) => timeline[Math.min(timeline.length - 1, minute)].phases[id] ?? "GREEN";
const BUS_REAR = 6.45;

test("at a red signal the bus waits behind its queued cars, which fill the space to the stop line", () => {
  let checked = 0;
  for (let t = 0; t <= 3600; t += 5) {
    for (const bus of busPoses(corridor, timeline, t)) {
      if (!bus.queueCars || bus.holdAt === null || Math.abs(bus.distance - bus.holdAt) > 0.01) continue;
      const segment = corridor.segment(bus.from, bus.to);
      const cars = queuedCars(corridor, [bus]).map((car) => car.s).sort((a, b) => a - b);
      assert.equal(cars.length, bus.queueCars);
      const line = segment.signal.stop_line_m;
      // Nose to tail: bus front, cars, stop line. Nothing overlaps; nothing crosses the line.
      assert.ok(cars[0] - CAR_LENGTH / 2 >= bus.distance + BUS_FRONT_M - 0.01, "first car clears the bus's nose");
      for (let i = 1; i < cars.length; i += 1) assert.ok(cars[i] - cars[i - 1] >= CAR_LENGTH, "cars do not overlap");
      assert.ok(cars.at(-1) + CAR_LENGTH / 2 <= line + 0.01, "front car stops at the line");
      assert.ok(line - (cars.at(-1) + CAR_LENGTH / 2) < 3, "and the queue reaches the line");
      // The trip model waits at its 2 m grid node nearest the queue position.
      assert.ok(Math.abs(holdPosition(segment, bus.queueCars) - bus.holdAt) <= 1.01);
      checked += 1;
    }
  }
  assert.ok(checked > 20, `${checked} queued holds checked`);
});

test("queued cars pull away with the bus when the light changes", () => {
  for (let t = 0; t <= 3600; t += 1) {
    const [before] = busPoses(corridor, timeline, t).filter((b) => b.queueCars && b.holdAt !== null && b.distance > b.holdAt + 1);
    if (!before) continue;
    const later = busPoses(corridor, timeline, t + 3).find((b) => b.id === before.id);
    if (!later || later.from !== before.from) continue;
    const a = queuedCars(corridor, [before]);
    const b = queuedCars(corridor, [later]);
    if (!a.length || !b.length) continue;
    assert.ok(b[0].s > a[0].s, "the queue moves off ahead of the bus");
    return;
  }
  assert.fail("no departure from a queued hold found");
});

const stream = new TrafficStream(corridor, timeline, { traffic, phaseAt });

test("background cars keep their distance, stop for red signals and never sit inside a bus", () => {
  assert.ok(stream.size > 100, `${stream.size} cars over the hour`);
  let samples = 0;
  for (let t = 30; t <= 3600; t += 7) {
    const cars = stream.cars(t).filter((car) => car.opacity >= 0.999);
    const buses = busPoses(corridor, timeline, t);
    const bySegment = new Map();
    for (const car of cars) (bySegment.get(car.segment) ?? bySegment.set(car.segment, []).get(car.segment)).push(car);
    for (const [key, list] of bySegment) {
      list.sort((a, b) => a.s - b.s);
      for (let i = 1; i < list.length; i += 1) {
        assert.ok(list[i].s - list[i - 1].s >= CAR_LENGTH + 0.5, `${key} cars overlap at ${t}s`);
      }
      const [from, to] = key.split(">");
      for (const bus of buses.filter((b) => b.from === from && b.to === to)) {
        for (const car of list) {
          const inside = car.s + CAR_LENGTH / 2 > bus.distance - BUS_REAR && car.s - CAR_LENGTH / 2 < bus.distance + BUS_FRONT_M;
          assert.ok(!inside, `${car.id} inside ${bus.id} at ${t}s`);
        }
      }
      // During a red minute (after the first 10 s, so cars that were already
      // committed have cleared), no car is stopped just past the stop line.
      const segment = corridor.segment(from, to);
      if (phaseAt(segment.signal.id, Math.floor(t / 60)) === "RED" && t % 60 > 10) {
        const line = segment.signal.stop_line_m;
        for (const car of list) {
          if (car.speed < 0.5) assert.ok(!(car.s > line && car.s < line + 10), `${car.id} stopped past the line`);
        }
      }
      samples += list.length;
    }
  }
  assert.ok(samples > 200, `${samples} car positions checked`);
});

test("the stream is a pure function of time (scrubbing back gives the same cars)", () => {
  const a = stream.cars(1234.5).map((car) => [car.id, car.s.toFixed(3)]);
  stream.cars(3000);
  const b = stream.cars(1234.5).map((car) => [car.id, car.s.toFixed(3)]);
  assert.deepEqual(a, b);
});

test("crossing gates lower ahead of a train and rise once it has cleared", () => {
  const crossing = data.ion_crossings[0];
  const at = crossing.at_m.north;
  const lowered = (nose) => loweredFor(crossing, { direction: "north", nose });
  assert.equal(lowered(at - WARN_M - 10), 0);
  assert.equal(lowered(at - DOWN_M + 5), 1);
  assert.ok(lowered(at - (WARN_M + DOWN_M) / 2) > 0 && lowered(at - (WARN_M + DOWN_M) / 2) < 1);
  assert.equal(lowered(at + 5), 1, "down while the train is on the crossing");
  assert.equal(lowered(at + ION_LENGTH + UP_M + 1), 0);
  // With the real timetable, every crossing on the simulated line closes at least once an hour.
  const terminals = JSON.parse(readFileSync(new URL("../lib/geo/ion-terminals.json", import.meta.url)));
  const timetable = new IonTimetable(data.ion, parity.ion.schedule, terminals);
  const closed = new Set();
  for (let t = 0; t <= 3600; t += 4) {
    for (const gate of gateStates(data.ion_crossings, timetable.trains(t))) if (gate.lowered === 1) closed.add(gate.crossing.id);
  }
  assert.deepEqual([...closed].sort(), data.ion_crossings.map((c) => c.id).sort());
});
