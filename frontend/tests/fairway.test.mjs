import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { FAIRWAY_BUS_ID, FAIRWAY_TRAIN_ID, FairwayReplay, NOT_IN_SERVICE } from "../lib/motion/fairway.ts";
import { ION_GAP, ION_SECTIONS } from "../lib/motion/ion.ts";
import { busBody, emptyBodies, lighting, trainBody } from "../lib/motion/bodies.ts";
import { deltaMetres, projections } from "../lib/motion/path.ts";

const data = JSON.parse(readFileSync(new URL("../lib/geo/fairway.json", import.meta.url)));
const replay = new FairwayReplay(data);
const STEP = 0.1;
const times = [];
for (let t = 0; t <= replay.duration; t += STEP) times.push(Math.round(t * 1000) / 1000);
const metres = (a, b) => Math.hypot(...deltaMetres(a, b));
const turn = (a, b) => Math.abs(((b - a + 540) % 360) - 180);
const edges = data.track.edges;

/** Distance from a point to the nearest drawn track edge. */
function offTrack(p) {
  let best = Infinity;
  for (const edge of Object.values(edges)) {
    for (const hit of projections(edge.path, p)) best = Math.min(best, Math.abs(hit.offset));
  }
  return best;
}

test("the track edges join at shared OSM nodes, and each move uses only joined edges", () => {
  for (const sw of data.track.switches) {
    assert.equal(sw.legs.length, 3, `switch ${sw.node} has three legs`);
    for (const leg of sw.legs) {
      const e = edges[leg];
      assert.ok(e.from === sw.node || e.to === sw.node);
      // The leg's end really is at the switch.
      const end = e.from === sw.node ? e.path[0] : e.path[e.path.length - 1];
      assert.ok(metres(end, [sw.lon, sw.lat]) < 0.05, `${leg} ends at switch ${sw.node}`);
    }
  }
  // The two crossover branches are OSM ways 754590785 and 754590786.
  assert.deepEqual(edges.crossover_nw_se.ways, [754590785]);
  assert.deepEqual(edges.crossover_sw_ne.ways, [754590786]);
  for (const move of [data.turnback.arrival, data.turnback.departure]) {
    for (let i = 1; i < move.edges.length; i += 1) {
      const a = edges[move.edges[i - 1].edge];
      const b = edges[move.edges[i].edge];
      const aEnd = move.edges[i - 1].reversed ? a.from : a.to;
      const bStart = move.edges[i].reversed ? b.to : b.from;
      assert.equal(aEnd, bStart, `${move.edges[i - 1].edge} -> ${move.edges[i].edge} share a node`);
    }
    // Every vertex of the move lies on a drawn edge: renderer and movement agree.
    for (const p of move.path) assert.ok(offTrack([p[0], p[1]]) < 0.05);
  }
  assert.deepEqual(data.turnback.departure.edges.map((e) => e.edge), ["platform_1", "crossover_nw_se", "running_north"]);
});

test("one train, never off the rails, never spinning or jumping, articulated through the crossover", () => {
  let prev = null;
  let worstOff = 0;
  for (const t of times) {
    const train = replay.train(t);
    assert.equal(train.id, FAIRWAY_TRAIN_ID);
    assert.equal(train.sections.length, ION_SECTIONS.length);
    train.sections.forEach((section, i) => {
      // Both chord ends of every section are on the track centreline.
      worstOff = Math.max(worstOff, offTrack(section.front), offTrack(section.rear));
      assert.ok(Math.abs(section.length - ION_SECTIONS[i]) < 0.05, `section ${i} keeps its length`);
      if (i > 0) {
        // Bellows: section i's A-side end is one gap from section i-1's B-side end.
        const gap = metres(train.sections[i - 1].rear, section.front);
        assert.ok(Math.abs(gap - ION_GAP) < 0.05, `gap ${gap.toFixed(3)} m at ${t}s`);
        assert.ok(turn(train.sections[i - 1].heading, section.heading) < 12, "neighbouring sections stay aligned");
      }
    });
    if (prev) {
      prev.sections.forEach((section, i) => {
        // Physical section i is the same carbody: it moves at most a frame's
        // travel and its heading (B end to A end) never flips.
        const moved = metres(section.centre, train.sections[i].centre);
        assert.ok(moved <= Math.max(prev.speed, train.speed) * STEP + 0.02, `section ${i} jumped ${moved.toFixed(3)} m at ${t}s`);
        assert.ok(turn(section.heading, train.sections[i].heading) < 3, `section ${i} turned at ${t}s`);
      });
    }
    prev = train;
  }
  assert.ok(worstOff < 0.05, `worst section end ${worstOff.toFixed(3)} m off the track`);
});

test("the train brakes to a full stop, swaps its lights only while stopped, and leaves the other way", () => {
  const stop = replay.stopAt;
  assert.ok(replay.train(stop - 3).speed > 0.5);
  assert.equal(replay.train(stop + 0.01).speed, 0);
  // Decelerating smoothly into the stop (never harder than the ION service brake).
  for (let t = stop - 8; t < stop; t += 0.2) {
    const dv = replay.train(t + 0.2).speed - replay.train(t).speed;
    assert.ok(dv <= 0.01 && dv >= -1.2 * 0.2 - 0.02, `braking at ${t.toFixed(1)}s`);
  }
  let last = replay.train(0).headlights;
  for (const t of times) {
    const train = replay.train(t);
    if (train.headlights !== last) {
      assert.equal(train.speed, 0, `lights change at ${t}s while moving`);
      assert.equal(train.phase, "stopped");
      last = train.headlights;
    }
  }
  assert.equal(replay.train(1).headlights, 0, "arrives with cab A leading");
  assert.equal(replay.train(replay.departAt + 1).headlights, ION_SECTIONS.length - 1, "departs with cab B leading");
  // Exact same carbody positions either side of the reversal.
  const a = replay.train(replay.departAt - 0.001);
  const b = replay.train(replay.departAt + 0.001);
  a.sections.forEach((s, i) => assert.ok(metres(s.centre, b.sections[i].centre) < 0.01));
  // It departs through crossover 754590785 onto the north running track.
  const seen = new Set(times.filter((t) => t > replay.departAt).map((t) => replay.train(t).edge));
  assert.ok(seen.has("crossover_nw_se") && seen.has("running_north"));
});

test("headlights lead and tail lights trail on the drawn train", () => {
  lighting.night = true;
  const lit = (t) => {
    const out = emptyBodies();
    trainBody(replay.train(t), out);
    return { head: out.solids.filter((f) => f.properties.role === "headlight"), beams: out.ground.filter((f) => f.properties.role === "beam") };
  };
  assert.equal(lit(5).head.length, 2);
  assert.equal(lit(replay.lightsOffAt + 1).head.length, 0, "both ends dark while changing ends");
  assert.equal(lit(replay.lightsOffAt + 1).beams.length, 0);
  assert.equal(lit(replay.departAt + 5).head.length, 2);
  lighting.night = false;
});

test("the bus lays over in its bay, changes its sign before moving, and exits along the lane", () => {
  const bus = data.bus;
  assert.equal(bus.layover.route, "7");
  assert.equal(bus.layover.stop.id, "3490");
  let prev = null;
  let changedAt = null;
  let movedAt = null;
  for (const t of times) {
    const pose = replay.bus(t);
    assert.equal(pose.id, FAIRWAY_BUS_ID);
    if (pose.phase === "layover") {
      assert.equal(pose.s, replay.busParkedFront, "parked in the bay");
      assert.ok(Math.abs(pose.s - bus.bay_pole_m) < 3, "front at the bay's pole");
    }
    if (prev) {
      assert.ok(pose.s >= prev.s, "only ever forwards: the lane is one-way");
      if (pose.sign.short !== prev.sign.short) changedAt ??= t;
      if (pose.s > prev.s) movedAt ??= t;
    }
    prev = pose;
  }
  assert.ok(changedAt !== null && movedAt !== null);
  assert.ok(movedAt - changedAt >= 5, `sign changes ${movedAt - changedAt}s before the bus moves`);
  assert.equal(replay.bus(0).sign.short, "Fairway");
  assert.equal(replay.bus(movedAt).sign.destination, "Conestoga Station");
  // Visible the whole time it is parked, and drawn as a body.
  for (let t = 0; t < movedAt; t += 1) assert.equal(replay.bus(t).opacity, 1);
  const out = emptyBodies();
  busBody(replay.bus(10), true, out);
  assert.ok(out.solids.length > 10);
});

test("a garage trip shows Not in Service", () => {
  const garage = new FairwayReplay(data, "garage");
  assert.equal(garage.bus(0).sign.short, "Fairway");
  assert.deepEqual(garage.bus(garage.pullOutAt).sign, NOT_IN_SERVICE);
});

test("the replay is a pure function of time", () => {
  const a = JSON.stringify(replay.scene(61.3));
  replay.scene(100);
  assert.equal(JSON.stringify(replay.scene(61.3)), a);
});
