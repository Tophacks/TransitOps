import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { Corridor } from "../lib/motion/corridor.ts";
import { PlaybackClock } from "../lib/motion/clock.ts";
import { busPoses } from "../lib/motion/playback.ts";
import { ION_GAP, ION_LENGTH, ION_SECTIONS, IonTimetable } from "../lib/motion/ion.ts";
import { deltaMetres, encodePath, lerpPath } from "../lib/motion/path.ts";
import { runningRails, STANDARD_GAUGE_M } from "../lib/motion/railGeometry.ts";

const data = JSON.parse(readFileSync(new URL("../lib/geo/waterloo-corridor.json", import.meta.url)));
const parity = JSON.parse(readFileSync(new URL("./fixtures/kinematics-parity.json", import.meta.url)));
const corridor = new Corridor(data);
const metres = (a, b) => Math.hypot(...deltaMetres(a, b));

test("browser kinematics reproduce the Python simulator exactly", () => {
  for (const c of parity.cases) {
    const run = corridor.run(c.from, c.to, c.minutes, c.hold, c.queue ?? 0);
    c.times.forEach((t, i) => {
      assert.ok(Math.abs(run.distance(t) - c.distances[i]) < 1e-9, `${c.from}->${c.to} at ${t}s`);
    });
    const segment = corridor.segment(c.from, c.to);
    c.positions.forEach(([lon, lat], i) => {
      const p = corridor.point(segment, c.distances[i * 5]);
      assert.ok(metres(p, [lon, lat]) < 0.15, `${c.from}->${c.to} position ${i}`);
    });
  }
});

// A hand-made two-minute timeline: depart UW, brake into Waterloo Public Square.
function frame(minute, elapsed, state = "EN_ROUTE") {
  const segment = { from: "UW", to: "WATERLOO_SQUARE", elapsed, minutes: 5, hold_minutes: 0, length_m: 0, distance_m: 0 };
  return { minute, buses: [{ id: "B101", state, segment }] };
}

test("playback moves continuously between minute snapshots", () => {
  const timeline = [0, 1, 2, 3, 4].map((m) => frame(m, m)).concat([frame(5, 5, "DWELLING")]);
  let previous = -1;
  let biggestStep = 0;
  for (let t = 0; t <= 300; t += 0.5) {
    const [pose] = busPoses(corridor, timeline, t);
    assert.ok(pose.distance >= previous - 1e-9, "never moves backwards");
    if (previous >= 0) biggestStep = Math.max(biggestStep, pose.distance - previous);
    previous = pose.distance;
  }
  // 0.5 s steps at no more than ~20 m/s: nothing teleports.
  assert.ok(biggestStep < 10.5, `largest half-second step ${biggestStep}`);
  const end = busPoses(corridor, timeline, 300)[0];
  assert.equal(end.distance, corridor.segment("UW", "WATERLOO_SQUARE").length_m);
  assert.equal(end.state, "DWELLING");
});

test("a dwelling bus holds still, then pulls away on the next segment", () => {
  const dwell = { minute: 0, buses: [{ id: "B1", state: "DWELLING", segment: { from: "UW", to: "WATERLOO_SQUARE", elapsed: 5, minutes: 5, hold_minutes: 0 } }] };
  const depart = { minute: 1, buses: [{ id: "B1", state: "EN_ROUTE", segment: { from: "WATERLOO_SQUARE", to: "GRAND_RIVER_HOSPITAL", elapsed: 0, minutes: 5, hold_minutes: 0 } }] };
  const moving = { minute: 2, buses: [{ id: "B1", state: "EN_ROUTE", segment: { from: "WATERLOO_SQUARE", to: "GRAND_RIVER_HOSPITAL", elapsed: 1, minutes: 5, hold_minutes: 0 } }] };
  const timeline = [dwell, depart, moving];
  const a = busPoses(corridor, timeline, 10)[0];
  const b = busPoses(corridor, timeline, 55)[0];
  assert.equal(a.speed, 0);
  assert.ok(metres(a.centre, b.centre) < 0.01, "held at the curb for the whole dwell");
  assert.equal(a.state, "DWELLING");
  const c = busPoses(corridor, timeline, 70)[0];
  const d = busPoses(corridor, timeline, 90)[0];
  assert.ok(c.speed > 0 && d.speed > c.speed, "accelerates away from the stop");
});

test("the bus heading follows the corner from Phillip onto University", () => {
  const segment = corridor.segment("UW", "WATERLOO_SQUARE");
  const corner = segment.curves.reduce((a, b) => (b[1] < a[1] ? b : a));
  const headings = [];
  for (let s = corner[0] - 30; s <= corner[0] + 30; s += 1) {
    const front = corridor.point(segment, s + 2.7);
    const rear = corridor.point(segment, s - 3.4);
    headings.push((Math.atan2(...deltaMetres(rear, front)) * 180) / Math.PI);
  }
  const turn = Math.abs(headings.at(-1) - headings[0]);
  assert.ok(turn > 70 && turn < 110, `turns about 90 degrees (${turn.toFixed(0)})`);
  for (let i = 1; i < headings.length; i += 1) {
    assert.ok(Math.abs(headings[i] - headings[i - 1]) < 9, "no sudden heading jump");
  }
});

const terminals = JSON.parse(readFileSync(new URL("../lib/geo/ion-terminals.json", import.meta.url)));
const timetable = new IonTimetable(data.ion, parity.ion.schedule, terminals);

test("browser ION trains reproduce the Python simulator exactly", () => {
  // Every physical train's nose (along its line run or terminal move) and
  // centre, through the turnbacks, against the simulator's own plan.
  let rows = 0;
  for (const sample of parity.ion.samples) {
    const plan = parity.ion.schedule.vehicles.find((v) => v.id === sample.id);
    for (const [t, s, centre] of sample.rows) {
      const point = timetable.locate(plan, t);
      assert.ok(point, `${sample.id} at ${t}s`);
      assert.ok(Math.abs(point.s - s) < 1e-6, `${sample.id} at ${t}s: ${point.s} vs ${s} (${point.segment.kind} ${point.segment.route ?? point.segment.line})`);
      // The centre is compared where it lies on the path (leaving the station
      // before a terminal, the tail is still behind the move's first point).
      const at = point.s - ION_LENGTH / 2;
      if (at >= 0 && at <= point.path.at(-1)[2]) {
        const mid = lerpPath(point.path, at);
        assert.ok(metres(mid, centre) < 0.15, `${sample.id} centre at ${t}s`);
      }
      rows += 1;
    }
  }
  assert.ok(rows > 2000, `${rows} positions compared`);
  // At every whole minute of a run the browser draws exactly the trains the
  // simulator reported, at exactly the same place along the line.
  for (const { minute, trains } of parity.ion.minutes) {
    const drawn = timetable.trains(minute * 60);
    assert.deepEqual(drawn.map((train) => train.id).sort(), Object.keys(trains).sort(), `minute ${minute}`);
    for (const train of drawn) assert.ok(Math.abs(train.nose - trains[train.id]) < 1e-6, `${train.id} minute ${minute}`);
  }
});

test("ION sections each follow the track and stay joined through the Uptown curve", () => {
  let sawSplitHeading = false;
  for (let t = 0; t < 3600; t += 2) {
    for (const train of timetable.trains(t)) {
      assert.equal(train.sections.length, ION_SECTIONS.length);
      for (let i = 0; i + 1 < train.sections.length; i += 1) {
        const gap = metres(train.sections[i].rear, train.sections[i + 1].front);
        assert.ok(gap <= ION_GAP + 0.05, `bellows gap ${gap.toFixed(2)} m`);
      }
      const spread = Math.abs(((train.sections[0].heading - train.sections.at(-1).heading + 540) % 360) - 180);
      if (spread > 45) sawSplitHeading = true;
    }
  }
  assert.ok(sawSplitHeading, "front section turned while the rear was still straight");
});

test("the playback clock pauses, changes speed and seeks without drift", () => {
  let now = 0;
  const clock = new PlaybackClock(3600, () => now);
  clock.setSpeed(10);
  clock.play();
  now = 1000;
  assert.equal(clock.time(), 10);
  clock.pause();
  now = 5000;
  assert.equal(clock.time(), 10);
  clock.setSpeed(60);
  clock.play();
  now = 6000;
  assert.equal(clock.time(), 70);
  clock.seek(1800);
  now = 7000;
  assert.equal(clock.time(), 1860);
  clock.setSpeed(1);
  now = 8000;
  assert.equal(clock.time(), 1861);
  now = 10_000_000;
  assert.equal(clock.time(), 3600);
  assert.equal(clock.playing, false);
});

test("the full ION line passes the Dutton Drive depot", async () => {
  const { ION_DEPOT_BOUNDARY } = await import("../lib/geo/ion-depot.ts");
  const { projections } = await import("../lib/motion/path.ts");
  for (const direction of ["north", "south"]) {
    const closest = Math.min(...ION_DEPOT_BOUNDARY.map((p) => projections(data.ion[direction].path, p)[0].offset));
    assert.ok(closest < 25, `${direction} track ${closest.toFixed(1)} m from the yard boundary`);
  }
});

test("ION rails stay centred at standard gauge through a bend", () => {
  const path = encodePath([[-80.54, 43.47], [-80.54, 43.471], [-80.539, 43.472], [-80.538, 43.472]]);
  const [left, right] = runningRails(path);
  assert.equal(left.length, path.length);
  assert.equal(right.length, path.length);
  for (const [index, [lon, lat]] of path.entries()) {
    const [a, b] = [left[index], right[index]];
    assert.ok(Math.abs(Math.hypot(...deltaMetres(a, b)) - STANDARD_GAUGE_M) < 1e-6);
    assert.ok(Math.abs((a[0] + b[0]) / 2 - lon) < 1e-10);
    assert.ok(Math.abs((a[1] + b[1]) / 2 - lat) < 1e-10);
  }
});

import { linePoses, PATTERNS, sparePoses } from "../lib/motion/lines.ts";

for (const [incoming, outgoing, boundary] of [["7-1-2", "7-0-2", 643], ["7-0-2", "7-1-2", 2217]]) {
  test(`Route 7 stays at the terminal when changing from ${outgoing} to ${incoming}`, () => {
    const end = PATTERNS[outgoing].path.at(-1), start = PATTERNS[incoming].path[0];
    const bus = { vehicle_id: "vehicle", stops: [], trips: [
      [0, boundary, "old", outgoing, "arrival", "7"], [boundary, null, "new", incoming, "departure", "7"],
    ], trace: [[boundary - 10, end[2], 0], [boundary, end[2], 0], [boundary + 1, 0, 0],
      [boundary + 20, 0, 0], [boundary + 21, 0.4, 0.8]] };
    for (const t of [boundary + 20, boundary, boundary - 0.01, boundary + 0.01, boundary + 0.5]) {
      const poses = linePoses([bus], t);
      assert.equal(poses.length, 1);
      assert.equal(poses[0].pattern, t < boundary ? outgoing : incoming);
      assert.equal(poses[0].speed, 0);
      assert.ok(metres(poses[0].centre, start) < 0.01);
    }
    const pose = linePoses([bus], boundary)[0];
    for (const trace of [bus.trace.slice(0, 2), bus.trace.slice(1)]) {
      assert.deepEqual(linePoses([{ ...bus, trace }], boundary), [pose], "checkpoint at the turn");
    }
    const departed = linePoses([bus], boundary + 20.5)[0];
    assert.ok(departed.speed > 0 && metres(departed.centre, start) < 1);
    const delayed = { ...bus, trips: [bus.trips[0], [boundary + 12, ...bus.trips[1].slice(1)]] };
    assert.ok(metres(linePoses([delayed], boundary + 6)[0].centre, start) < 0.01);
  });
}

test("a dispatched spare follows its road move, waits, then owns the handover instant", () => {
  const pattern = PATTERNS["7-1-2"];
  const deadhead = { key: "garage:northfield|stop:1130", t0: 10, t1: 20, path: [[-80.53, 43.49, 0, 0], [...pattern.path[0].slice(0, 2), 900, 10]] };
  const spare = { vehicle_id: "spare", deadhead, trace: [[21, 0, 0], [29, 0, 0], [31, 0, 0]],
    trips: [[30, null, "relief", pattern.id, pattern.headsign, "7"]], stops: [] };
  const old = { vehicle_id: "relieved", trips: [[0, 30, "relief", pattern.id, pattern.headsign, "7"]],
    trace: [[1, 0, 0], [30, 0, 0]], stops: [] };
  const at = (t) => linePoses([old, spare], t);
  assert.ok(metres(at(15).at(-1).centre, at(20).at(-1).centre) > 100);
  assert.deepEqual(at(20).at(-1).centre, at(29.5).at(-1).centre);
  assert.deepEqual(at(29.99).at(-1).centre, at(30)[0].centre);
  const legacy = { ...spare, trace: [[21, 0, 0], [48, 0, 0]] };
  assert.deepEqual(linePoses([legacy], 30), at(30));
  assert.deepEqual(at(30).map((b) => [b.id, b.pattern]), [["spare", pattern.id]]);
  assert.deepEqual(sparePoses([{ id: "spare", deadhead }], 15, new Set(["spare"])), []);
});
