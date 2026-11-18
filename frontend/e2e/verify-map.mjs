// Browser check for the Waterloo map: campus close-up, building occlusion,
// bus braking / dwell / pull-away, playback controls, ION articulation on the
// curve and the switch back to the live fleet.
//
//   node e2e/verify-map.mjs [BASE_URL] [OUT_DIR]
//
// BASE_URL defaults to http://localhost:3000 (FastAPI on :8000 behind `npm run dev`).
// For a protected Vercel preview pass a `?_vercel_share=…` link as BASE_URL.
// Needs Playwright (`npm i -D playwright` or PLAYWRIGHT_MODULE=/path/to/playwright).
// Screenshots and a .webm recording of the motion are written to OUT_DIR.
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
let playwright;
try {
  playwright = require("playwright");
} catch {
  playwright = require(process.env.PLAYWRIGHT_MODULE ?? "/opt/node-tools/node_modules/playwright");
}

const base = new URL(process.argv[2] ?? "http://localhost:3000");
base.searchParams.set("e2e", "1");
const outDir = path.resolve(process.argv[3] ?? "e2e-output");
mkdirSync(outDir, { recursive: true });

const VIEWPORTS = { desktop: { width: 1440, height: 900 }, phone: { width: 390, height: 844 } };
const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` · ${detail}` : ""}`);
}

const metres = (a, b) => Math.hypot((a[0] - b[0]) * 111320 * Math.cos((43.465 * Math.PI) / 180), (a[1] - b[1]) * 111320);

async function scene(page) {
  return page.evaluate(() => {
    const { buses, trains } = window.__transitops.scene();
    return {
      buses: buses.map(({ id, state, from, to, distance, length, speed, centre, heading }) =>
        ({ id, state, from, to, distance, length, speed, centre, heading })),
      trains: trains.map(({ id, direction, nose, speed, sections }) =>
        ({ id, direction, nose, speed, sections: sections.map(({ centre, heading }) => ({ centre, heading })) })),
    };
  });
}

async function seek(page, seconds) {
  await page.locator('input[aria-label="Simulation timeline"]').fill(String(Math.round(seconds)));
  await page.waitForTimeout(250);
}

async function camera(page) {
  return page.evaluate(() => {
    const map = window.__transitops.map;
    const c = map.getCenter();
    return { center: [c.lng, c.lat], zoom: map.getZoom(), pitch: map.getPitch(), moving: map.isMoving() };
  });
}

async function settle(page) {
  await page.waitForFunction(() => !window.__transitops.map.isMoving(), null, { timeout: 20_000 });
  await page.waitForTimeout(600);
}

async function run(name, viewport, record) {
  const browser = await playwright.chromium.launch({
    args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
  });
  const context = await browser.newContext({
    viewport,
    deviceScaleFactor: 1,
    ...(record ? { recordVideo: { dir: outDir, size: viewport } } : {}),
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const shot = (label) => page.screenshot({ path: path.join(outDir, `${name}-${label}.png`) });

  await page.goto(base.href, { waitUntil: "load" });
  await page.waitForFunction(() => window.__transitops?.map?.isStyleLoaded(), null, { timeout: 90_000 });
  await page.waitForTimeout(3000);

  // Simulation opens the campus close-up.
  await page.getByRole("button", { name: /Start simulation|Apply & restart/ }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForFunction(() => window.__transitops.scene().buses.length > 0, null, { timeout: 30_000 });
  await settle(page);
  const cam = await camera(page);
  check(`${name}: Simulation opens the campus close-up`, cam.zoom > 17.5 && metres(cam.center, [-80.539, 43.4743]) < 120,
    `zoom ${cam.zoom.toFixed(2)}, ${metres(cam.center, [-80.539, 43.4743]).toFixed(0)} m from campus`);
  await page.getByRole("button", { name: /Pause/ }).click();
  await shot("campus");

  // A bus arrives at UW Station, dwells and pulls away. Find that minute in the timeline.
  const arrival = await page.evaluate(async () => {
    for (let t = 0; t <= 3600; t += 20) {
      const input = document.querySelector('input[aria-label="Simulation timeline"]');
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      setter.call(input, String(t));
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const bus = window.__transitops.scene().buses.find((b) => b.to === "UW" && b.length - b.distance < 250 && b.speed > 0);
      if (bus) return { t, id: bus.id };
    }
    return null;
  });
  check(`${name}: a bus approaches UW Station in the timeline`, Boolean(arrival), arrival ? `${arrival.id} at ${arrival.t}s` : "");
  if (arrival) {
    const samples = [];
    for (let t = arrival.t; t <= arrival.t + 200; t += 2) {
      await seek(page, t);
      const bus = (await scene(page)).buses.find((b) => b.id === arrival.id);
      if (bus) samples.push({ t, ...bus });
    }
    // The approach: last 120 m before the curb on the leg into UW.
    const approach = samples.filter((s) => s.to === "UW" && s.length - s.distance < 120 && s.length - s.distance > 0.5);
    const decel = approach.every((s, i) => i === 0 || s.speed <= approach[i - 1].speed + 0.3);
    check(`${name}: bus brakes into the stop`, approach.length > 3 && decel,
      approach.map((s) => `${(s.speed * 3.6).toFixed(0)}`).join("→") + " km/h");
    // Dwell: stationary at the curb (playback already shows it on the next leg at 0 m).
    const firstStill = samples.findIndex((s) => s.speed === 0);
    let lastStill = firstStill;
    while (lastStill + 1 < samples.length && samples[lastStill + 1].speed === 0
      && metres(samples[lastStill + 1].centre, samples[firstStill].centre) < 0.05) lastStill += 1;
    const dwellSeconds = firstStill < 0 ? 0 : samples[lastStill].t - samples[firstStill].t;
    check(`${name}: bus dwells at the curb`, dwellSeconds >= 40, `${dwellSeconds}s stationary`);
    // Pull-away: speed rises over the first ~10 s after the dwell.
    const away = samples.slice(lastStill + 1, lastStill + 6);
    const accel = away.length > 3 && away.every((s, i) => i === 0 || s.speed >= away[i - 1].speed - 0.05) && away.at(-1).speed > 2;
    check(`${name}: bus pulls away`, accel, away.map((s) => `${(s.speed * 3.6).toFixed(0)}`).join("→") + " km/h");

    // Pause holds the scene; speed changes keep the timeline continuous.
    await seek(page, arrival.t);
    const before = (await scene(page)).buses.find((b) => b.id === arrival.id);
    await page.waitForTimeout(1500);
    const later = (await scene(page)).buses.find((b) => b.id === arrival.id);
    check(`${name}: pause freezes vehicles`, metres(before.centre, later.centre) < 0.01);

    // Follow the bus at 10× while recording.
    await page.evaluate(({ id }) => {
      const map = window.__transitops.map;
      const bus = window.__transitops.scene().buses.find((b) => b.id === id);
      map.jumpTo({ center: bus.centre, zoom: 18.2, pitch: 60, bearing: 30 });
    }, arrival);
    await settle(page);
    // Measure the playback rate inside the page so click latency doesn't count.
    const rate = () => page.evaluate(async () => {
      const read = () => {
        const [m, sec] = document.querySelector('[aria-label="Current simulation time"]').textContent.split(":").map(Number);
        return m * 60 + sec;
      };
      const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      await wait(400);
      const s0 = read();
      const t0 = performance.now();
      await wait(3000);
      return (read() - s0) / ((performance.now() - t0) / 1000);
    });
    await page.getByRole("button", { name: "10×" }).click();
    await page.getByRole("button", { name: /Play/ }).click();
    const at10 = await rate();
    await page.getByRole("button", { name: "30×" }).click();
    const at30 = await rate();
    await page.getByRole("button", { name: /Pause/ }).click();
    check(`${name}: speed changes advance the clock at the chosen rate`,
      Math.abs(at10 - 10) < 1.5 && Math.abs(at30 - 30) < 3, `${at10.toFixed(1)}× and ${at30.toFixed(1)}× measured`);
    await shot("bus-at-uw");

    // Scrub to whole minutes: the bus is where the simulator put it.
    const api = await page.evaluate(async (payload) => {
      const response = await fetch("/api/simulate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      return (await response.json()).smart.timeline;
    }, { traffic_multiplier: 1.15, train_delay: 4, passenger_demand: 110, fleet_size: 3, demand_multiplier: 1, surge_passengers: 0, seed: 17 });
    let worst = 0;
    for (const minute of [5, 12, 23, 31, 44]) {
      await seek(page, minute * 60);
      const drawn = (await scene(page)).buses;
      for (const bus of api[minute].buses) {
        const pose = drawn.find((b) => b.id === bus.id);
        if (!pose || !bus.segment) continue;
        // Compare map positions: at a dwell the simulator reports the end of the
        // finished leg and playback the start of the next one, the same curb.
        worst = Math.max(worst, metres(pose.centre, [bus.lon, bus.lat]));
      }
    }
    check(`${name}: scrubbing lands on the simulator's minute positions`, worst < 1.0, `worst ${worst.toFixed(2)} m from the simulator's lon/lat`);
  }

  // Buildings hide a vehicle behind them: look at a bus across a tall building.
  const occlusion = await page.evaluate(async () => {
    const { map } = window.__transitops;
    const wait = () => new Promise((resolve) => { map.once("idle", resolve); map.triggerRepaint(); });
    const input = document.querySelector('input[aria-label="Simulation timeline"]');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    for (let t = 0; t <= 3600; t += 15) {
      setter.call(input, String(t));
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      for (const bus of window.__transitops.scene().buses) {
        if (bus.state !== "EN_ROUTE" && bus.state !== "DWELLING") continue;
        map.jumpTo({ center: bus.centre, zoom: 17.6, pitch: 0, bearing: 0 });
        await wait();
        const buildings = map.queryRenderedFeatures({ layers: ["corridor-buildings"] })
          .filter((f) => (f.properties.render_height ?? 0) >= 12);
        for (const building of buildings) {
          const ring = building.geometry.type === "Polygon" ? building.geometry.coordinates[0] : building.geometry.coordinates[0][0];
          const c = ring.reduce((acc, p) => [acc[0] + p[0] / ring.length, acc[1] + p[1] / ring.length], [0, 0]);
          const k = Math.cos((43.465 * Math.PI) / 180);
          const dx = (bus.centre[0] - c[0]) * 111320 * k;
          const dy = (bus.centre[1] - c[1]) * 111320;
          const d = Math.hypot(dx, dy);
          if (d < 25 || d > 70) continue;
          const bearing = (Math.atan2(dx, dy) * 180) / Math.PI;
          map.jumpTo({ center: bus.centre, zoom: 18.6, pitch: 72, bearing });
          await wait();
          const pixel = async () => {
            const p = map.project(bus.centre);
            const canvas = map.getCanvas();
            const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
            map.triggerRepaint();
            await new Promise((resolve) => map.once("render", resolve));
            const out = new Uint8Array(4);
            const ratio = canvas.width / canvas.clientWidth;
            gl.readPixels(Math.round(p.x * ratio), Math.round(canvas.height - (p.y - 8) * ratio), 1, 1, gl.RGBA, gl.UNSAFE_UNSIGNED_BYTE ?? gl.UNSIGNED_BYTE, out);
            return [...out];
          };
          const hidden = await pixel();
          map.setLayoutProperty("corridor-buildings", "visibility", "none");
          await wait();
          const visible = await pixel();
          map.setLayoutProperty("corridor-buildings", "visibility", "visible");
          await wait();
          // Gold (#f1bf4b-ish) body when the building is off; building blue-grey when it is on.
          const gold = (px) => px[0] > 150 && px[1] > 110 && px[2] < 120;
          if (gold(visible)) return { t, bus: bus.id, height: building.properties.render_height, hidden, visible, ok: !gold(hidden) };
        }
      }
    }
    return null;
  });
  check(`${name}: real basemap buildings hide a bus behind them`, Boolean(occlusion?.ok),
    occlusion ? `bus ${occlusion.bus} behind a ${occlusion.height} m building: ${occlusion.visible} → ${occlusion.hidden}` : "no bus/building pair found");
  if (occlusion) {
    await shot("occluded");
    await page.evaluate(() => window.__transitops.map.setLayoutProperty("corridor-buildings", "visibility", "none"));
    await page.waitForTimeout(800);
    await shot("occluded-buildings-off");
    await page.evaluate(() => window.__transitops.map.setLayoutProperty("corridor-buildings", "visibility", "visible"));
  }

  // ION curve: all five sections bend through the King St → Erb St curve.
  await page.getByRole("button", { name: "ION curve" }).click();
  await settle(page);
  const curve = await camera(page);
  let best = null;
  for (let t = 0; t <= 600 && !best; t += 3) {
    await page.evaluate((value) => {
      const input = document.querySelector('input[aria-label="Simulation timeline"]');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, String(value));
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }, t);
    await page.waitForTimeout(60);
    for (const train of (await scene(page)).trains) {
      const mid = train.sections[2].centre;
      if (metres(mid, curve.center) > 25) continue;
      const headings = train.sections.map((s) => s.heading);
      const spread = Math.abs(((headings[0] - headings[4] + 540) % 360) - 180);
      if (spread > 45) best = { t, id: train.id, headings: headings.map((h) => h.toFixed(0)) };
    }
  }
  check(`${name}: ION curve shows five sections bending through the curve`, Boolean(best),
    best ? `${best.id} at ${best.t}s, section headings ${best.headings.join("/")}°` : "no train found on the curve");
  await page.waitForTimeout(500);
  await shot("ion-curve");

  // Back to the live fleet.
  await page.getByRole("button", { name: "Live fleet" }).click();
  await page.waitForFunction(() => (window.__transitops.map.getSource("grt-vehicles")?._data?.features?.length ?? 0) > 0
    || (window.__transitops.map.getSource("grt-vehicles")?.serialize?.().data?.features?.length ?? 0) > 0, null, { timeout: 45_000 }).catch(() => {});
  await settle(page);
  const live = await page.evaluate(() => {
    const { map } = window.__transitops;
    const features = map.querySourceFeatures("grt-vehicles");
    const hidden = ["vehicle-bodies", "sim-bus-markers", "route-stroke"].every((id) => map.getLayoutProperty(id, "visibility") === "none");
    return { count: features.length, modes: [...new Set(features.map((f) => f.properties.mode))], hidden, text: document.querySelector(".geoFeedStatus")?.textContent ?? "" };
  });
  check(`${name}: Live fleet restores live GRT and ION tracking`, live.hidden && /GRT bus · \d+/.test(live.text) && /ION train · \d+/.test(live.text),
    `${live.text.replace(/\s+/g, " ")}; simulation layers hidden: ${live.hidden}`);
  await shot("live");

  check(`${name}: no page errors`, errors.length === 0, errors.slice(0, 3).join(" | "));
  await context.close();
  await browser.close();
}

const only = process.env.VIEWPORT;
for (const [name, viewport] of Object.entries(VIEWPORTS)) {
  if (only && only !== name) continue;
  await run(name, viewport, process.env.RECORD === "1");
}
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
