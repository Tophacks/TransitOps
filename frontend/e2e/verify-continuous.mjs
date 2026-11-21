// Run against a same-origin frontend + simulation API, e.g. Next dev.
// node e2e/verify-continuous.mjs http://localhost:3000
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const url = new URL(process.argv[2] || "http://localhost:3000");
url.searchParams.set("fixed", "1");
url.searchParams.set("e2e", "1");
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(120_000);
  // Listen to the normal worker's checkpoints without altering its messages.
  await page.addInitScript(() => {
    window.Worker = class extends Worker {
      constructor(url, options) {
        super(url, options);
        if (!String(url).includes("continuous-worker")) return;
        this.addEventListener("message", ({ data }) => {
          if (data.error) throw new Error(data.error);
          for (const bus of data.result.smart.lines.vehicles) {
            const trip = bus.trips.find((x, i) => i && bus.trips[i - 1][3] !== x[3]);
            if (trip) window.terminalTurn ??= { id: bus.vehicle_id, time: trip[0] };
          }
        });
      }
    };
  });
  await page.goto(url.href);
  await page.getByRole("button", { name: "Run continuously" }).click();
  await page.getByRole("button", { name: /Pause$/ }).click();
  const time = () => page.evaluate(() => window.__transitops.now());
  const paused = await time();
  await page.waitForTimeout(500);
  assert.equal(await time(), paused);
  await page.getByRole("button", { name: /Play$/ }).click();

  let testedTurn = false;
  for (let minute = 5; minute <= 65; minute += 5) {
    await page.waitForFunction((m) => Number(document.querySelector('[aria-label="Simulation timeline"]').max) >= m * 60,
      minute, { timeout: 180_000 });
    if (!testedTurn && await page.evaluate(() => !!window.terminalTurn)) {
      for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
        await page.setViewportSize(viewport);
        await page.getByRole("button", { name: "1×", exact: true }).click();
        const { samples, elapsed, turn } = await page.evaluate(async () => {
          const api = window.__transitops, turn = window.terminalTurn;
          api.seek(turn.time - 2);
          const samples = [], start = performance.now();
          do {
            const t = api.now();
            samples.push({ t, poses: api.lines(t).filter((p) => p.id === turn.id) });
            await new Promise((resolve) => setTimeout(resolve, 100));
          } while (performance.now() < start + 4500);
          return { samples, elapsed: (performance.now() - start) / 1000, turn };
        });
        assert(samples[0].t < turn.time && samples.at(-1).t > turn.time);
        assert(Math.abs((samples.at(-1).t - samples[0].t) / elapsed - 1) < 0.15);
        const first = samples[0].poses[0];
        assert.notEqual(first.pattern, samples.at(-1).poses[0].pattern);
        for (const { poses } of samples) {
          assert.equal(poses.length, 1);
          const [lon, lat] = poses[0].centre;
          assert(Math.hypot((lon - first.centre[0]) * 81000, (lat - first.centre[1]) * 111200) < 1);
        }
        console.log(`${viewport.width}px: ${turn.id} at ${turn.time}s, ${samples.length} samples at 1×`);
      }
      testedTurn = true;
    }
    // Skip idle playback between checkpoints. The UI requests each extension.
    if (minute < 65) await page.evaluate((m) => window.__transitops.seek(m * 60 - 5), minute);
  }
  assert(testedTurn);
  assert.equal(await page.getByLabel("Simulation timeline").getAttribute("min"), "300");
  console.log("Pause/resume and replay window 05:00–65:00 passed.");
} finally {
  await browser.close();
}
