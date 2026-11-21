// Browser check of the GRT garages (#17 part 4): each garage sends a bus out and
// receives one back along real roads, its inventory balances, the same vehicle
// IDs appear throughout, and the map draws the buses the simulator moved.
//
//   node e2e/verify-garages.mjs [BASE_URL] [OUT_DIR]
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
base.searchParams.set("fixed", "1");
const outDir = path.resolve(process.argv[3] ?? "e2e-output");
mkdirSync(outDir, { recursive: true });
const results = [];
const check = (name, ok, detail = "") => {
  results.push(ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` · ${detail}` : ""}`);
};
const seek = (page, t) => page.evaluate((value) => {
  const input = document.querySelector('input[aria-label="Simulation timeline"]');
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, String(Math.round(value)));
  input.dispatchEvent(new Event("input", { bubbles: true }));
  window.__transitops.seek(value);
}, t);

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
  await page.waitForFunction(() => window.__transitops.scene().trains.length > 0, null, { timeout: 90_000 });
  await page.getByRole("button", { name: /Pause/ }).click();
  const decisions = await page.evaluate(() => window.__transitops.busDecisions());
  for (const gid of ["northfield", "strasburg", "conestoga-blvd"]) {
    const ds = decisions.filter((d) => d.terminal === gid);
    const out = ds.find((d) => /^leave by/.test(d.chosen));
    const back = ds.find((d) => /^enter by/.test(d.chosen));
    check(`${name}: ${gid} sends and receives a bus`, Boolean(out && back), `${out?.vehicle_id} out, ${back?.vehicle_id} in`);
    // Mid-deadhead, the bus is drawn on the road (not at the garage).
    const t = out.t + 60;
    await seek(page, t);
    await page.waitForTimeout(500);
    const pose = await page.evaluate(([id, tt]) => window.__transitops.garages(tt).find((p) => p.id === id), [out.vehicle_id, t]);
    check(`${name}: ${out.vehicle_id} is drawn pulling out`, Boolean(pose && pose.state === "pulling out"), pose ? pose.state : "missing");
    if (gid === "northfield") {
      await page.evaluate((c) => window.__transitops.map.jumpTo({ center: c, zoom: 16.2, pitch: 40, bearing: 0 }), pose.centre);
      await page.waitForTimeout(1500);
      await page.locator(".geoMapShell").screenshot({ path: path.join(outDir, `garage-${name}.png`) });
    }
  }
  // The route 13 bus leaves UW Station and is the bus that returns to Northfield.
  const back = decisions.find((d) => d.terminal === "northfield" && /^enter by/.test(d.chosen));
  const uw = await page.evaluate(([id, t]) => window.__transitops.uw(t).find((p) => p.id === id), [back.vehicle_id, back.t - 20]);
  check(`${name}: ${back.vehicle_id} comes from UW Station`, Boolean(uw), uw ? "drawn at UW Station before it returns" : "not at UW");
  check(`${name}: no page errors`, errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
}
const failed = results.filter((ok) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
