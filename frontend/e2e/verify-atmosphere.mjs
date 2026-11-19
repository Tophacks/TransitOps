// Browser check of the step 6 atmosphere: buses queue behind cars at red
// signals, background cars at street zoom, ION crossing gates, night mode, and
// the per-frame JavaScript cost with the CPU throttled like a phone.
//
//   node e2e/verify-atmosphere.mjs [BASE_URL] [OUT_DIR]
import { mkdirSync, readFileSync } from "node:fs";
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
const geo = JSON.parse(readFileSync(new URL("../lib/geo/waterloo-corridor.json", import.meta.url)));
const uwCrossing = geo.ion_crossings.find((c) => c.near.includes("University Of Waterloo Station")) ?? geo.ion_crossings[0];
const results = [];
const check = (name, ok, detail = "") => {
  results.push(ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` · ${detail}` : ""}`);
};

const seek = (page, t) => page.evaluate((value) => {
  const input = document.querySelector('input[aria-label="Simulation timeline"]');
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, String(Math.round(value)));
  input.dispatchEvent(new Event("input", { bubbles: true }));
}, t);
const frames = (page, n = 3) => page.evaluate((count) => new Promise((resolve) => {
  let left = count;
  const tick = () => (--left <= 0 ? resolve() : requestAnimationFrame(tick));
  requestAnimationFrame(tick);
}), n);
const solids = (page) => page.evaluate(() => window.__transitops.map.querySourceFeatures("vehicle-solids").map((f) => f.properties));

for (const [name, viewport] of Object.entries({ desktop: { width: 1440, height: 900 }, phone: { width: 390, height: 844 } })) {
  if (process.env.VIEWPORT && process.env.VIEWPORT !== name) continue;
  const browser = await playwright.chromium.launch({
    args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
  });
  const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(base.href);
  await page.waitForFunction(() => window.__transitops?.map?.isStyleLoaded(), null, { timeout: 120_000 });
  await page.getByRole("button", { name: /Start simulation|Apply & restart/ }).click();
  await page.waitForFunction(() => window.__transitops.scene().buses.length > 0, null, { timeout: 30_000 });
  await page.getByRole("button", { name: /Pause/ }).click();
  await page.waitForFunction(() => !window.__transitops.map.isMoving(), null, { timeout: 20_000 });
  const shot = (label) => page.locator(".geoMapShell").screenshot({ path: path.join(outDir, `${name}-${label}.png`) });

  // 1. A bus waiting at a red signal behind its queued cars.
  let held = null;
  for (let t = 0; t <= 3600 && !held; t += 10) {
    await seek(page, t);
    await frames(page, 2);
    held = await page.evaluate(() => {
      const bus = window.__transitops.scene().buses.find((b) => b.queueCars && b.holdAt !== null && Math.abs(b.distance - b.holdAt) < 0.01);
      return bus ? { id: bus.id, cars: bus.queueCars, centre: bus.centre, heading: bus.heading } : null;
    });
    if (held) held.t = t;
  }
  if (held) {
    await page.evaluate((bus) => window.__transitops.map.jumpTo({ center: bus.centre, zoom: 18.6, pitch: 58, bearing: bus.heading + 25 }), held);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(2500);
    const parts = await solids(page);
    const queued = new Set(parts.filter((p) => p.role === "car" && String(p.id).startsWith(`${held.id}:q`)).map((p) => p.id));
    check(`${name}: a bus waits at a red signal behind queued cars`, queued.size === held.cars,
      `${held.id} at ${held.t}s, ${queued.size} queued cars drawn for ${held.cars} in the simulator`);
    await shot("queue");
  } else {
    check(`${name}: a bus waits at a red signal behind queued cars`, false, "no queued hold found");
  }

  // 2. Background cars at street zoom only.
  const streetCars = new Set((await solids(page)).filter((p) => p.role === "car" && String(p.id).startsWith("car")).map((p) => p.id)).size;
  await page.evaluate(() => window.__transitops.map.jumpTo({ center: [-80.52, 43.465], zoom: 13.4, pitch: 40, bearing: -20 }));
  await page.waitForTimeout(1500);
  const regionCars = (await solids(page)).filter((p) => p.role === "car").length;
  check(`${name}: background cars at street zoom, none zoomed out`, streetCars > 0 && regionCars === 0,
    `${streetCars} cars in view at street zoom, ${regionCars} car parts at zoom 13.4`);

  // 3. ION crossing gates at UW: down while a train passes, up otherwise.
  await page.evaluate((c) => window.__transitops.map.jumpTo({ center: [c.lon, c.lat], zoom: 18.4, pitch: 55, bearing: c.road_bearing + 60 }), uwCrossing);
  await page.waitForTimeout(2500);
  const bands = async () => (await solids(page)).filter((p) => p.id === `gate:${uwCrossing.id}` && p.height === 1.08).length;
  let down = null;
  let up = null;
  for (let t = 0; t <= 1800 && (down === null || up === null); t += 6) {
    await seek(page, t);
    await frames(page, 3);
    const n = await bands();
    if (n >= 12 && down === null) down = t;
    if (n === 0 && up === null) up = t;
  }
  if (down !== null) {
    await seek(page, down);
    await page.evaluate(() => window.scrollTo(0, 0));
    await frames(page, 4);
    await page.waitForTimeout(800);
    await shot("gate-down");
  }
  check(`${name}: crossing gates lower for a passing train and rise again`, down !== null && up !== null,
    `${uwCrossing.id} near ${uwCrossing.near}: fully down at ${down}s, up at ${up}s`);

  // 4. Night mode.
  await page.getByRole("button", { name: "Night", exact: true }).click();
  await frames(page, 4);
  await page.waitForTimeout(800);
  const nightParts = await solids(page);
  const ground = await page.evaluate(() => window.__transitops.map.querySourceFeatures("vehicle-ground").map((f) => f.properties.role));
  const buildings = await page.evaluate(() => window.__transitops.map.getPaintProperty("corridor-buildings", "fill-extrusion-color"));
  const badge = await page.locator(".simBadge").innerText();
  const lit = nightParts.filter((p) => p.color === "#f5c86a").length;
  check(`${name}: night mode lights windows and headlights, labelled illustrative`,
    lit > 0 && ground.includes("beam") && buildings === "#252f3b" && /night/i.test(badge),
    `${lit} lit window parts, ${ground.filter((r) => r === "beam").length} headlight beams, buildings ${buildings}`);
  await shot("night");
  await page.getByRole("button", { name: "Night", exact: true }).click();

  // 5. Frame cost at campus street zoom, CPU throttled 4x (phone-like), playing at 30x.
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  await page.evaluate((c) => window.__transitops.map.jumpTo({ center: [c.lon, c.lat], zoom: 17.6, pitch: 58, bearing: 30 }), uwCrossing);
  await seek(page, (held?.t ?? 600) - 60);
  await page.getByRole("button", { name: "30×" }).click();
  await page.getByRole("button", { name: /Play/ }).click();
  await page.evaluate(() => { window.__transitops.frameCosts.length = 0; });
  await page.waitForTimeout(6000);
  const costs = await page.evaluate(() => [...window.__transitops.frameCosts].sort((a, b) => a - b));
  await page.getByRole("button", { name: /Pause/ }).click();
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 });
  const pct = (q) => costs[Math.min(costs.length - 1, Math.floor(q * costs.length))] ?? Infinity;
  check(`${name}: scene build stays within a phone frame budget (CPU 4x throttled)`, costs.length > 10 && pct(0.95) < 12,
    `${costs.length} frames, median ${pct(0.5).toFixed(1)} ms, p95 ${pct(0.95).toFixed(1)} ms of JavaScript per frame`);

  check(`${name}: no page errors`, errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
}
const failed = results.filter((ok) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
