// Browser check of live GRT / ION motion: vehicles glide along their GTFS
// shapes between GPS pings, never backwards, and draw as 3D bodies at street
// zoom and as icons when zoomed out.
//
//   node e2e/verify-live.mjs [BASE_URL] [OUT_DIR] [SECONDS]
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
const seconds = Number(process.argv[4] ?? 70);
mkdirSync(outDir, { recursive: true });
const metres = (a, b) => Math.hypot((a[0] - b[0]) * 111320 * Math.cos((43.465 * Math.PI) / 180), (a[1] - b[1]) * 111320);
const results = [];
const check = (name, ok, detail = "") => {
  results.push(ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` · ${detail}` : ""}`);
};

for (const [name, viewport] of Object.entries({ desktop: { width: 1440, height: 900 }, phone: { width: 390, height: 844 } })) {
  if (process.env.VIEWPORT && process.env.VIEWPORT !== name) continue;
  const browser = await playwright.chromium.launch({
    args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
  });
  const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(base.href);
  await page.waitForFunction(() => window.__transitops?.map?.isStyleLoaded() && window.__transitops.live().length > 20, null, { timeout: 120_000 });
  const poses = await page.evaluate(() => window.__transitops.live());
  const onShape = poses.filter((p) => p.s != null).length;
  check(`${name}: live vehicles are snapped to GTFS shapes`, onShape / poses.length > 0.8,
    `${onShape}/${poses.length} on a shape, ${poses.filter((p) => p.detour).length} off-shape (detour)`);
  check(`${name}: buses and ION trains both tracked`, poses.some((p) => p.mode === "bus") && poses.some((p) => p.mode === "ion"));

  // Watch every vehicle for `seconds` across at least two feed polls.
  const samples = await page.evaluate(async (duration) => {
    const out = {};
    const end = performance.now() + duration * 1000;
    while (performance.now() < end) {
      for (const p of window.__transitops.live()) {
        (out[p.key] ??= []).push({ t: performance.now() / 1000, s: p.s, shapeId: p.shapeId, centre: p.centre, detour: p.detour });
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    return out;
  }, seconds);
  let moved = 0;
  let backwards = 0;
  const backwardCases = [];
  let biggestStep = 0;
  let glides = 0;
  for (const list of Object.values(samples)) {
    let distinct = 0;
    for (let i = 1; i < list.length; i += 1) {
      const a = list[i - 1];
      const b = list[i];
      if (a.shapeId !== b.shapeId || a.s == null || b.s == null) continue;
      if (b.s < a.s - 1e-6) {
        backwards += 1;
        backwardCases.push(`${a.shapeId} ${a.s.toFixed(1)}→${b.s.toFixed(1)} m`);
      }
      const step = b.s - a.s;
      if (step > 0) distinct += 1;
      // Ignore deliberate jumps after missed pings; measure the glides.
      if (step > 0 && step / (b.t - a.t) < 40) biggestStep = Math.max(biggestStep, step / (b.t - a.t));
    }
    if (distinct > 0) moved += 1;
    if (distinct > 8) glides += 1;
  }
  check(`${name}: vehicles glide between pings (many small steps, not one jump per ping)`, glides > 10,
    `${glides} vehicles moved in >8 separate 0.25 s steps; ${moved} moved at all`);
  check(`${name}: no vehicle moves backwards along its shape`, backwards === 0, `${backwards} backward steps ${backwardCases.join("; ")}`);
  check(`${name}: glide speeds stay street-like`, biggestStep < 31, `fastest ${(biggestStep * 3.6).toFixed(0)} km/h`);

  // Zoom to a moving bus: it is a blue 3D body, labelled live GPS.
  const target = Object.entries(samples).find(([key, list]) => key.startsWith("bus:") && list.at(-1).s != null
    && list.at(-1).s - list[0].s > 30)?.[0];
  if (target) {
    await page.evaluate((key) => {
      const pose = window.__transitops.live().find((p) => p.key === key);
      window.__transitops.map.jumpTo({ center: pose.centre, zoom: 18.3, pitch: 60, bearing: pose.heading - 30 });
    }, target);
    await page.waitForTimeout(2500);
    const drawn = await page.evaluate((key) => {
      const { map } = window.__transitops;
      const bodies = map.querySourceFeatures("live-solids").filter((f) => f.properties.id === key);
      const colors = [...new Set(bodies.map((f) => f.properties.color))];
      return { parts: bodies.length, colors, icon: map.getLayer("grt-points").maxzoom, zoom: map.getZoom() };
    }, target);
    check(`${name}: a live bus is drawn as a blue 3D body at street zoom`, drawn.parts > 10 && drawn.colors.includes("#2f7ee0"),
      `${drawn.parts} parts, colours ${drawn.colors.slice(0, 4).join(" ")}; icons hidden above zoom ${drawn.icon}`);
    await page.screenshot({ path: path.join(outDir, `${name}-live-bus.png`) });
  } else {
    check(`${name}: found a moving bus to zoom to`, false);
  }
  // Zoom out: icons.
  await page.evaluate(() => window.__transitops.map.jumpTo({ center: [-80.518, 43.462], zoom: 13.3, pitch: 48, bearing: -24 }));
  await page.waitForTimeout(2500);
  // Every tracked vehicle inside the view gets an icon (fleet size varies through the day).
  const icons = await page.evaluate(() => {
    const { map } = window.__transitops;
    // Project to the screen: with the map pitched, getBounds() also covers off-screen corners.
    const canvas = map.getCanvas();
    const inView = window.__transitops.live().filter((p) => {
      const { x, y } = map.project(p.centre);
      return p.opacity > 0 && x >= 0 && y >= 0 && x <= canvas.clientWidth && y <= canvas.clientHeight;
    }).length;
    const rendered = new Set(map.queryRenderedFeatures({ layers: ["grt-points"] }).map((f) => f.properties.id)).size;
    return { inView, rendered };
  });
  check(`${name}: icons when zoomed out`, icons.inView > 0 && icons.rendered >= icons.inView * 0.9,
    `${icons.rendered} icons for ${icons.inView} vehicles in view`);
  await page.screenshot({ path: path.join(outDir, `${name}-live-region.png`) });
  check(`${name}: no page errors`, errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
}
const failed = results.filter((ok) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
