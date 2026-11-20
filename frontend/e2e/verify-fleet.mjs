// Browser check of the scheduled lines and the regional fleet: Route 7 buses
// drawn from the simulator, a station delay, a breakdown covered by a named
// spare out of a real garage (drawn on its road route), and dispatcher orders
// from the Fleet panel, accepted and refused with their reasons.
//
//   node e2e/verify-fleet.mjs [BASE_URL] [OUT_DIR]
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

async function settle(page) {
  await page.waitForFunction(() => window.__transitops.scene().trains.length > 0, null, { timeout: 90_000 });
  await page.waitForTimeout(1500);
  const pause = page.getByRole("button", { name: /Pause/ });
  if (await pause.count()) await pause.click();
}

async function runPreset(page, title) {
  await page.getByRole("button", { name: new RegExp(`^${title}`) }).click();
  const response = page.waitForResponse((r) => r.url().includes("/api/simulate"), { timeout: 180_000 });
  await page.getByRole("button", { name: /Start simulation|Apply & restart/ }).click();
  const body = await (await response).json();
  await settle(page);
  return body;
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

  // 1. Route 7 end to end: its buses are drawn from the simulator on the line.
  let body = await runPreset(page, "Route 7 end to end");
  const lines = body.smart.lines;
  const complete = lines.vehicles.some((v) => {
    const byTrip = {};
    for (const x of v.stops) (byTrip[x.trip] ??= []).push(x);
    return Object.values(byTrip).some((xs) => xs[0].stop === "1130" && xs.at(-1).stop === "3490" && xs.length === 40);
  });
  check(`${name}: a Route 7 bus runs Conestoga to Fairway, all 40 stops`, complete, `${lines.metrics.trips_covered}/${lines.metrics.trips_scheduled} trips covered`);
  await seek(page, 2400);
  await page.waitForTimeout(800);
  const poses = await page.evaluate(() => window.__transitops.lines(2400));
  check(`${name}: Route 7 buses are on the map at 08:10`, poses.length >= 6, poses.map((p) => p.id).join(" "));
  const hasLayer = await page.evaluate(() => Boolean(window.__transitops.map.getLayer("sched-lines-route")));
  check(`${name}: the line is drawn from GTFS`, hasLayer);

  // 2. Pick a Route 7 bus in the Fleet panel: its status, driver and decisions.
  const busId = poses[0].id;
  await page.getByLabel("Vehicle", { exact: true }).selectOption(busId);
  const status = await page.locator(".fleetVehicle").innerText();
  check(`${name}: the Fleet panel shows the bus's status and driver`, status.includes(busId) && /block \d+/.test(status) && /OP\d+/.test(status),
    status.split("\n").slice(0, 6).join(" | "));
  const inspecting = await page.evaluate(() => window.__transitops.inspecting());
  check(`${name}: the inspector follows the picked bus`, inspecting === busId, String(inspecting));

  // 3. Breakdown: a named spare leaves a real garage and is drawn on its road route.
  body = await runPreset(page, "Breakdown, spare from a garage");
  const ask = body.smart.decisions.find((d) => d.terminal === "fleet" && /broke down/.test(d.trigger));
  check(`${name}: dispatch sends a specific ready spare`, Boolean(ask?.vehicle_id), ask ? `${ask.chosen} · ${ask.outcome}` : "none");
  const spare = body.smart.fleet.vehicles.find((v) => v.id === ask?.vehicle_id);
  const mid = spare?.deadhead ? (spare.deadhead.t0 + spare.deadhead.t1) / 2 : 0;
  await seek(page, mid);
  await page.waitForTimeout(800);
  const drawn = await page.evaluate((t) => window.__transitops.spares(t), mid);
  const onCorridor = await page.evaluate(() => window.__transitops.scene().buses.map((b) => b.id));
  check(`${name}: the spare is drawn on its out-of-service route, not on the corridor`,
    drawn.some((p) => p.id === spare.id) && !onCorridor.includes(spare.id), drawn.map((p) => `${p.id}@${p.centre.map((x) => x.toFixed(4)).join(",")}`).join(" "));
  const counts = body.smart.fleet.summary.counts_end;
  check(`${name}: every garage reconciles (in + out = allocation)`,
    Object.values(counts).every((c) => c.in_garage + c.out === c.allocation), JSON.stringify(Object.fromEntries(Object.entries(counts).map(([k, c]) => [k, `${c.in_garage}+${c.out}`]))));

  // 4. Fleet panel orders: a ready spare accepted, a MobilityPLUS vehicle refused.
  await page.getByRole("button", { name: "Ready spares" }).click();
  const first = await page.getByLabel("Vehicle", { exact: true }).locator("option").nth(1).getAttribute("value");
  await page.getByLabel("Vehicle", { exact: true }).selectOption(first);
  let response = page.waitForResponse((r) => r.url().includes("/api/simulate"), { timeout: 180_000 });
  await page.getByRole("button", { name: /^Dispatch spare at/ }).click();
  body = await (await response).json();
  await settle(page);
  let order = body.smart.decisions.find((d) => d.trigger.startsWith("Dispatcher order") && d.vehicle_id === first);
  check(`${name}: an order for a ready spare is accepted`, order?.chosen === "accepted", order ? `${order.outcome} · ${order.effect}` : "none");
  await page.getByRole("button", { name: "All" }).click();
  await page.getByLabel("Vehicle", { exact: true }).selectOption("M01");
  response = page.waitForResponse((r) => r.url().includes("/api/simulate"), { timeout: 180_000 });
  await page.getByRole("button", { name: /Try: dispatch as a spare/ }).click();
  body = await (await response).json();
  await settle(page);
  order = body.smart.decisions.find((d) => d.trigger.startsWith("Dispatcher order") && d.vehicle_id === "M01");
  const shown = await page.locator(".fleetOrders").innerText();
  check(`${name}: a MobilityPLUS vehicle is refused, with the reason shown`, order?.chosen === "refused" && /MobilityPLUS/.test(shown), order?.control ?? "none");
  check(`${name}: orders replay with the run (both kept)`, (body.scenario.commands ?? []).length === 2, JSON.stringify(body.scenario.commands));
  await page.screenshot({ path: path.join(outDir, `fleet-${name}.png`), fullPage: false });

  check(`${name}: no page errors`, errors.length === 0, errors.join(" | "));
  await browser.close();
}

const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} checks passed`);
process.exit(passed === results.length ? 0 : 1);
