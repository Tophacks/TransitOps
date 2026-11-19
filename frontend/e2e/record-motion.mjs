// Record a short GIF of the map by stepping the paused timeline frame by frame.
// Each frame is exactly what the playback clock draws at that simulated time,
// so the clip is smooth even where WebGL runs in software.
//
//   node e2e/record-motion.mjs OUT.gif '{"from":930,"to":1090,"step":1.5,
//     "camera":{"center":[-80.5394,43.4744],"zoom":18.6,"pitch":62,"bearing":35},
//     "viewport":[960,600],"mode":"simulation"}'
//
// mode "live" records the live fleet in real time instead (one frame per `step` seconds).
// mode "fairway" records the Fairway terminal replay in its own framing
// (from/to/step in replay seconds; `camera` optional; "next": "garage" for
// the Not in Service variant).
// Needs Playwright and ffmpeg.
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
let playwright;
try {
  playwright = require("playwright");
} catch {
  playwright = require(process.env.PLAYWRIGHT_MODULE ?? "/opt/node-tools/node_modules/playwright");
}

const out = path.resolve(process.argv[2]);
const options = JSON.parse(process.argv[3] ?? "{}");
const base = new URL(process.env.BASE_URL ?? "http://localhost:3000");
base.searchParams.set("e2e", "1");
for (const [key, value] of Object.entries(options.query ?? {})) base.searchParams.set(key, value);
const [width, height] = options.viewport ?? [960, 600];
const frames = mkdtempSync(path.join(tmpdir(), "transitops-frames-"));

const browser = await playwright.chromium.launch({
  args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
await page.goto(base.href);
await page.waitForFunction(() => window.__transitops?.map?.isStyleLoaded(), null, { timeout: 90_000 });
await page.waitForTimeout(2500);
if (options.mode === "fairway") {
  await page.getByRole("button", { name: "Fairway", exact: true }).click();
  await page.waitForFunction(() => window.__transitops.fairway, null, { timeout: 20_000 });
  if (options.next === "garage") await page.getByRole("button", { name: "Garage", exact: true }).click();
  await page.getByRole("button", { name: "Pause", exact: true }).click();
} else if (options.mode !== "live") {
  if (options.preset) await page.getByRole("button", { name: options.preset }).click();
  await page.getByRole("button", { name: /Start simulation|Apply & restart/ }).click();
  await page.waitForFunction(() => window.__transitops.scene().buses.length > 0, null, { timeout: 30_000 });
  await page.getByRole("button", { name: /Pause/ }).click();
}
// Let the mode switch's camera ease finish before placing our camera.
await page.waitForTimeout(500);
await page.waitForFunction(() => !window.__transitops.map.isMoving(), null, { timeout: 20_000 });
if (options.camera) await page.evaluate((camera) => window.__transitops.map.jumpTo(camera), options.camera);
if (options.setup) await page.evaluate(options.setup);
const map = page.locator(".geoMapShell");
let index = 0;
const settle = () => page.evaluate(() => new Promise((resolve) => {
  const map = window.__transitops.map;
  map.triggerRepaint();
  map.once("idle", resolve);
  setTimeout(resolve, 4000);
}));
if (options.mode === "live") {
  await page.waitForFunction(() => window.__transitops.live().length > 0, null, { timeout: 120_000 });
  await page.waitForTimeout(options.warmup ?? 4000);
  // follow: a vehicle key, or "auto" for a bus moving near `camera.center`.
  let follow = options.follow;
  if (follow === "auto") {
    follow = await page.evaluate(async (centre) => {
      const near = (p) => Math.hypot((p.centre[0] - centre[0]) * 80_000, (p.centre[1] - centre[1]) * 111_000);
      for (let i = 0; i < 120; i += 1) {
        const moving = window.__transitops.live()
          .filter((p) => p.mode === "bus" && p.s != null && p.speed > 4 && near(p) < 3000)
          .sort((a, b) => near(a) - near(b));
        if (moving.length) return moving[0].key;
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      return null;
    }, options.camera.center);
    console.log(`following ${follow}`);
  }
  for (let t = 0; t <= (options.to ?? 30); t += options.step ?? 1) {
    if (follow) {
      await page.evaluate((key) => {
        const pose = window.__transitops.live().find((p) => p.key === key);
        if (pose) window.__transitops.map.jumpTo({ center: pose.centre });
      }, follow);
    }
    await page.waitForTimeout((options.step ?? 1) * 1000);
    await map.screenshot({ path: path.join(frames, `f${String(index++).padStart(4, "0")}.png`) });
  }
} else if (options.mode === "fairway") {
  for (let t = options.from ?? 0; t <= (options.to ?? 111); t += options.step ?? 1) {
    await page.evaluate((value) => window.__transitops.fairway.clock.seek(value), t);
    await settle();
    await page.evaluate(() => window.scrollTo(0, 0));
    await map.screenshot({ path: path.join(frames, `f${String(index++).padStart(4, "0")}.png`) });
  }
} else {
  for (let t = options.from ?? 0; t <= (options.to ?? 60); t += options.step ?? 1) {
    await page.evaluate((value) => {
      const input = document.querySelector('input[aria-label="Simulation timeline"]');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, String(Math.round(value * 10) / 10));
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }, t);
    // The range input steps in whole seconds; nudge the clock for sub-second frames.
    if (!Number.isInteger(t)) {
      await page.evaluate((value) => window.__transitops.seek?.(value), t);
    }
    if (options.follow) {
      await page.evaluate((id) => {
        const { map, scene } = window.__transitops;
        const { buses, trains } = scene();
        const target = buses.find((b) => b.id === id)?.centre ?? trains.find((tr) => tr.id === id)?.sections[2].centre;
        if (target) map.jumpTo({ center: target });
      }, options.follow);
    }
    await settle();
    await page.evaluate(() => window.scrollTo(0, 0));
    await map.screenshot({ path: path.join(frames, `f${String(index++).padStart(4, "0")}.png`) });
  }
}
await browser.close();

const fps = options.fps ?? 12;
const scale = options.gifWidth ?? Math.min(width, 720);
execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-framerate", String(fps), "-i", path.join(frames, "f%04d.png"),
  "-vf", `scale=${scale}:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4`,
  out]);
rmSync(frames, { recursive: true, force: true });
console.log(`wrote ${out} (${index} frames)`);
