import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  BACK_TOLERANCE_M, BUS_LANE_M, FADE_S, GONE_GRACE_S, LiveTracker, STALE_S, glideAt, snap,
} from "../lib/motion/live.ts";
import { deltaMetres, encodePath, pointAt, projections } from "../lib/motion/path.ts";

const data = JSON.parse(readFileSync(new URL("../lib/geo/waterloo-corridor.json", import.meta.url)));
// UW Station -> Phillip St -> University Ave: a real street path with two corners.
const street = data.segments.find((s) => s.from === "UW" && s.to === "WATERLOO_SQUARE").path;
const shapes = new Map([["uw", street]]);
const lookup = (id) => shapes.get(id);
const metres = (a, b) => Math.hypot(...deltaMetres(a, b));
const T0 = 1_795_000_000;

function ping(s, t, extra = {}) {
  const [lon, lat] = pointAt(street, s, 0.8); // a GPS fix a little off the centreline
  return { key: "bus:1", id: "1", mode: "bus", lon, lat, timestamp: T0 + t, shapeId: "uw", ...extra };
}

test("a GPS fix snaps onto the shape as distance along it", () => {
  for (const s of [40, 260, 900, 2000]) {
    const [lon, lat] = pointAt(street, s, 9);
    const hit = snap(street, [lon, lat]);
    assert.ok(Math.abs(hit.s - s) < 2.5, `s ${hit.s.toFixed(1)} vs ${s}`);
    assert.ok(Math.abs(hit.offset - 9) < 1, `offset ${hit.offset.toFixed(2)}`);
  }
});

test("on a shape that passes the same street twice, snapping prefers the pass ahead of the vehicle", () => {
  // Out along the street and back on the same centreline.
  const out = street.slice(0, 120).map(([lon, lat]) => [lon, lat]);
  const loop = encodePath([...out, ...out.slice(0, -1).reverse()]);
  const length = loop.at(-1)[2];
  const half = out.length ? encodePath(out).at(-1)[2] : 0;
  const p = pointAt(loop, 150);
  const near = projections(loop, p).filter((c) => c.offset < 1).map((c) => c.s);
  assert.ok(near.some((s) => s < half) && near.some((s) => s > half), "the fix lies on both passes");
  assert.ok(Math.abs(snap(loop, p, 120).s - 150) < 1, "outbound vehicle stays outbound");
  const back = length - 150;
  assert.ok(Math.abs(snap(loop, p, back - 60).s - back) < 1, "returning vehicle stays on the return pass");
});

test("the glide is monotone, starts at the current speed and ends exactly at the target", () => {
  const g = { s0: 100, s1: 400, v0: 14, v1: 12, start: 10, duration: 20 };
  let previous = -Infinity;
  for (let t = 10; t <= 30.001; t += 0.05) {
    const { s } = glideAt(g, t);
    assert.ok(s >= previous - 1e-9 && s <= 400 + 1e-9);
    previous = s;
  }
  assert.equal(glideAt(g, 30).s, 400);
  assert.ok(Math.abs(glideAt(g, 10).v - 14) < 1e-6);
  // Absurd speeds are clamped rather than overshooting.
  const wild = { s0: 0, s1: 10, v0: 80, v1: 80, start: 0, duration: 20 };
  for (let t = 0; t <= 20; t += 0.1) assert.ok(glideAt(wild, t).s <= 10 + 1e-9);
});

test("between pings the bus glides along the street, never across the block, and ends at the latest ping", () => {
  const tracker = new LiveTracker();
  tracker.update([ping(100, 0)], lookup, 0);
  tracker.update([ping(420, 20)], lookup, 20); // around the corner onto Phillip St
  let previous = -Infinity;
  let worstOff = 0;
  let biggestStep = 0;
  let last;
  for (let now = 20; now <= 44; now += 0.1) {
    const [pose] = tracker.poses(now, T0 + 20);
    assert.ok(pose.s >= previous - 1e-9, "never backwards");
    if (last) biggestStep = Math.max(biggestStep, metres(last, pose.centre));
    previous = pose.s;
    last = pose.centre;
    // On the street: the bus centre stays in its lane beside the shape.
    const off = projections(street, pose.centre)[0].offset;
    worstOff = Math.max(worstOff, Math.abs(off - BUS_LANE_M));
  }
  assert.ok(worstOff < 0.6, `strays ${worstOff.toFixed(2)} m from its lane`);
  assert.ok(biggestStep < 3.5, `largest 0.1 s step ${biggestStep.toFixed(2)} m`);
  const [end] = tracker.poses(41, T0 + 20);
  const target = snap(street, [ping(420, 20).lon, ping(420, 20).lat]).s;
  assert.ok(Math.abs(end.s - target) < 1e-6, "arrives at the snapped ping");
});

test("a new ping mid-glide hands over without a jump in position or speed", () => {
  const tracker = new LiveTracker();
  tracker.update([ping(100, 0)], lookup, 0);
  tracker.update([ping(400, 20)], lookup, 20);
  const before = tracker.poses(29.99, T0 + 20)[0];
  tracker.update([ping(700, 30)], lookup, 30);
  const after = tracker.poses(30.01, T0 + 30)[0];
  assert.ok(metres(before.centre, after.centre) < 0.5);
  assert.ok(Math.abs(before.speed - after.speed) < 0.6, `${before.speed.toFixed(2)} → ${after.speed.toFixed(2)} m/s`);
});

test("GPS jitter backwards holds the vehicle still instead of reversing", () => {
  const tracker = new LiveTracker();
  tracker.update([ping(300, 0)], lookup, 0);
  tracker.update([ping(500, 20)], lookup, 20);
  const arrived = tracker.poses(60, T0 + 20)[0];
  tracker.update([ping(470, 40)], lookup, 60);
  for (let now = 60; now < 90; now += 1) {
    assert.ok(tracker.poses(now, T0 + 40)[0].s >= arrived.s - 1e-9);
  }
  assert.ok(BACK_TOLERANCE_M > 30);
});

test("missed pings jump instead of racing along the street", () => {
  const tracker = new LiveTracker();
  tracker.update([ping(100, 0)], lookup, 0);
  tracker.update([ping(2600, 20)], lookup, 20);
  const pose = tracker.poses(20.1, T0 + 20)[0];
  assert.ok(Math.abs(pose.s - snap(street, [ping(2600, 20).lon, ping(2600, 20).lat]).s) < 1e-6);
});

test("a detour shows the raw GPS fix and re-anchors on the shape afterwards", () => {
  const tracker = new LiveTracker();
  tracker.update([ping(800, 0)], lookup, 0);
  const off = pointAt(street, 900, 160); // a parallel street a block away
  tracker.update([{ ...ping(0, 20), lon: off[0], lat: off[1] }], lookup, 20);
  const detour = tracker.poses(20.05, T0 + 20)[0];
  assert.equal(detour.detour, true);
  assert.ok(metres(detour.centre, off) < 0.01, "jumps to the fix rather than sweeping across the block");
  tracker.update([ping(1100, 40)], lookup, 40);
  const back = tracker.poses(60, T0 + 40)[0];
  assert.equal(back.detour, false);
  assert.ok(Math.abs(back.s - snap(street, [ping(1100, 40).lon, ping(1100, 40).lat]).s) < 1e-6);
});

test("stale pings are flagged, and vehicles fade in and out as they join and leave the feed", () => {
  const tracker = new LiveTracker();
  tracker.update([ping(200, 0)], lookup, 100);
  assert.equal(tracker.poses(100, T0)[0].opacity, 0);
  assert.equal(tracker.poses(100 + FADE_S, T0)[0].opacity, 1);
  assert.equal(tracker.poses(102, T0 + STALE_S + 5)[0].stale, true);
  tracker.update([], lookup, 110);
  const lingering = tracker.poses(110 + GONE_GRACE_S - 1, T0 + 10)[0];
  assert.ok(lingering && lingering.opacity === 1 && lingering.stale, "kept, marked stale, during the grace period");
  const fading = tracker.poses(110 + GONE_GRACE_S + FADE_S / 2, T0 + 10)[0];
  assert.ok(fading.opacity > 0 && fading.opacity < 1);
  assert.equal(tracker.poses(110 + GONE_GRACE_S + FADE_S + 0.1, T0 + 10).length, 0);
  assert.equal(tracker.size, 0);
});

test("a vehicle that starts a new trip re-anchors on the new shape", () => {
  const reverse = encodePath(street.map(([lon, lat]) => [lon, lat]).reverse());
  shapes.set("uw-back", reverse);
  const tracker = new LiveTracker();
  tracker.update([ping(street.at(-1)[2] - 20, 0)], lookup, 0);
  const end = tracker.poses(1, T0)[0].centre;
  const p = pointAt(reverse, 30, 0.8);
  tracker.update([{ ...ping(0, 20), lon: p[0], lat: p[1], shapeId: "uw-back" }], lookup, 20);
  const pose = tracker.poses(40, T0 + 20)[0];
  assert.equal(pose.shapeId, "uw-back");
  assert.ok(Math.abs(pose.s - 30) < 2);
  assert.ok(metres(end, pose.centre) < 25, "short hop, not a sweep across the map");
});

test("a fix past the end of the shape is a terminal layover, not a detour", () => {
  const tracker = new LiveTracker();
  const beyond = pointAt(street, -80, 0); // 80 m before the shape starts, in line with it
  tracker.update([{ ...ping(0, 0), lon: beyond[0], lat: beyond[1] }], lookup, 0);
  const pose = tracker.poses(5, T0)[0];
  assert.equal(pose.terminal, true);
  assert.equal(pose.detour, false);
  assert.ok(metres(pose.centre, beyond) < 0.01);
});

test("glides never peak above highway speed: they stretch, or jump if the gap is too big", async () => {
  const { MAX_GLIDE_SPEED, glidePeak } = await import("../lib/motion/live.ts");
  for (const [gap, seconds] of [[400, 20], [550, 20], [700, 20]]) {
    const tracker = new LiveTracker();
    tracker.update([ping(100, 0)], lookup, 0);
    tracker.update([ping(100 + gap, seconds)], lookup, 20);
    let peak = 0;
    for (let now = 20; now < 60; now += 0.05) peak = Math.max(peak, tracker.poses(now, T0 + seconds)[0].speed);
    assert.ok(peak <= MAX_GLIDE_SPEED + 0.05, `${gap} m: peak ${peak.toFixed(1)} m/s`);
  }
  assert.ok(glidePeak({ s0: 0, s1: 300, v0: 0, v1: 15, start: 0, duration: 20 }) > 15, "eased start peaks above the average");
});
