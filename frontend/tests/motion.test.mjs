import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { Corridor } from "../lib/motion/corridor.ts";
import { PlaybackClock } from "../lib/motion/clock.ts";
import { busPoses } from "../lib/motion/playback.ts";
import { ION_GAP, ION_SECTIONS, ionService, ionTrains } from "../lib/motion/ion.ts";
import { deltaMetres } from "../lib/motion/path.ts";

const data = JSON.parse(readFileSync(new URL("../lib/geo/waterloo-corridor.json", import.meta.url)));
const parity = JSON.parse(readFileSync(new URL("./fixtures/kinematics-parity.json", import.meta.url)));
const corridor = new Corridor(data);
const metres = (a, b) => Math.hypot(...deltaMetres(a, b));

test("browser kinematics reproduce the Python simulator exactly", () => {
  for (const c of parity.cases) {
    const run = corridor.run(c.from, c.to, c.minutes, c.hold);
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

test("ION sections each follow the track and stay joined through the Uptown curve", () => {
  const service = ionService(data.ion);
  let sawSplitHeading = false;
  for (let t = 0; t < 900; t += 2) {
    for (const train of ionTrains(service, t)) {
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
