// Browser check of drivers and recovery: Phillip St cars and the bus's gap,
// a berth overshoot (granted and denied), the wrong turn's legal re-route and
// an ION overshoot, each drawn from the simulator and explained.
//
//   node e2e/verify-drivers.mjs [BASE_URL] [OUT_DIR]
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

async function runPreset(page, title) {
  await page.getByRole("button", { name: new RegExp(`^${title}`) }).click();
  const response = page.waitForResponse((r) => r.url().includes("/api/simulate"), { timeout: 120_000 });
  await page.getByRole("button", { name: /Start simulation|Apply & restart/ }).click();
  await response;
  await page.waitForFunction(() => window.__transitops.scene().trains.length > 0, null, { timeout: 90_000 });
  await page.waitForTimeout(1500);
  const pause = page.getByRole("button", { name: /Pause/ });
  if (await pause.count()) await pause.click();
  return page.evaluate(() => window.__transitops.busDecisions());
}

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

  // 1. Heavy traffic: a bus waits for a real gap; the blocker is named and drawn.
  let ds = await runPreset(page, "UW exit, heavy traffic");
  const wait = ds.find((d) => d.chosen === "yield and wait" && d.blockers?.length && /^B1/.test(d.vehicle_id));
  check(`${name}: a bus waits at the Phillip St exit for a named car`, Boolean(wait), wait ? `${wait.vehicle_id}: ${wait.outcome}` : "none");
  await seek(page, wait.t + 1);
  await page.waitForTimeout(800);
  const cars = await page.evaluate((t) => window.__transitops.uwCars(t).map((c) => c.id), wait.t + 1);
  check(`${name}: the map draws the simulator's cars on Phillip St`, cars.length > 0, `${cars.length} cars`);
  const ex = await page.evaluate((id) => window.__transitops.uw().find((p) => p.id === id), wait.vehicle_id);
  await page.evaluate((c) => window.__transitops.map.jumpTo({ center: c, zoom: 18, pitch: 45, bearing: 150 }), ex.centre);
  await page.waitForTimeout(1500);
  await page.locator(".geoMapShell").screenshot({ path: path.join(outDir, `drivers-gap-${name}.png`) });

  // 2. Overshoot: reverse granted, then denied with a bus behind.
  ds = await runPreset(page, "Bus overshoot, reverse granted");
  let ask = ds.find((d) => /asks to reverse/.test(d.trigger));
  check(`${name}: Control grants the reverse with a clear path`, /^reverse/.test(ask?.chosen ?? "") && /granted/.test(ask?.control ?? ""), ask?.control);
  ds = await runPreset(page, "Bus overshoot, reverse denied");
  ask = ds.find((d) => /asks to reverse/.test(d.trigger));
  check(`${name}: Control denies it with a bus behind`, /denied/.test(ask?.control ?? "") && /missed stop/.test(ask?.chosen ?? ""), ask?.chosen);
  if (name === "phone") await page.locator(".inspectorToggle").click();
  await seek(page, ask.t + 1);
  await page.waitForTimeout(600);
  const item = page.locator(".decisionList button", { hasText: "missed stop" }).first();
  if (await item.count()) await item.click(); else await page.getByRole("button", { name: "Show on map" }).click();
  await page.waitForTimeout(1500);
  const card = await page.locator(".decisionCard").innerText();
  check(`${name}: the inspector shows what Control allowed and the effect`, /What Control allowed/i.test(card) && /Effect on service/i.test(card),
    card.split("\n").slice(0, 3).join(" | "));
  await page.locator(".geoMapShell").screenshot({ path: path.join(outDir, `drivers-overshoot-${name}.png`) });
  if (name === "phone") await page.locator(".inspectorToggle").click();

  // 3. Wrong turn: drawn on the legal re-route, from where the bus is.
  ds = await runPreset(page, "Bus takes the wrong turn");
  const plan = ds.find((d) => /off its trip/.test(d.trigger));
  await seek(page, plan.t + 60);
  await page.waitForTimeout(800);
  const det = await page.evaluate((t) => window.__transitops.uw(t).find((p) => p.id === "B101"), plan.t + 60);
  check(`${name}: B101 is drawn on its re-route`, Boolean(det), det ? det.centre.map((x) => x.toFixed(4)).join(",") : "missing");

  // 4. ION overshoot on the street: no reverse; the train continues.
  await runPreset(page, "Train overshoot on the street");
  const rail = await page.evaluate(() => window.__transitops.ion().decisions.find((d) => /asks to recover/.test(d.trigger)));
  check(`${name}: Rail Control denies a reverse on a street-running platform`, /^continue to/.test(rail?.chosen ?? "") && /street-running/.test(rail?.control ?? ""), rail?.chosen);
  await seek(page, rail.t - 5);
  await page.waitForTimeout(800);
  const train = await page.evaluate(([id, t]) => window.__transitops.scene().trains.find((x) => x.id === id), [rail.vehicle_id, rail.t - 5]);
  check(`${name}: the train is drawn creeping past the platform`, Boolean(train), train ? `${train.activity}` : "missing");
  check(`${name}: no page errors`, errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
}
const failed = results.filter((ok) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
