// Route 7 buses driven by the simulator, played at 1×: at the King / KCI curbside
// stop and at the Fairway Station bay exit the bus brakes progressively, creeps
// into position, stops fully before its doors open, waits for a gap in real car
// traffic and rolls out. The map draws that motion with the route and destination.
//
//   node e2e/verify-route7-driving.mjs [BASE_URL] [OUT_DIR]
import { mkdirSync, writeFileSync } from "node:fs";
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
const waited = (d) => Number(/after waiting (\d+) s/.exec(d.outcome)?.[1] ?? 0);

async function seek(page, t) {
  await page.evaluate((value) => {
    const input = document.querySelector('input[aria-label="Simulation timeline"]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, String(Math.round(value)));
    input.dispatchEvent(new Event("input", { bubbles: true }));
    window.__transitops.seek(value);
  }, t);
}

/** Play at 1× for `seconds` of real time, sampling the bus and the lane's cars every frame. */
function sample(page, id, seconds) {
  return page.evaluate(([vid, ms]) => new Promise((resolve) => {
    const out = [];
    const start = performance.now();
    const tick = () => {
      const t = window.__transitops.now();
      const bus = window.__transitops.lines(t).find((b) => b.id === vid);
      const cars = window.__transitops.zoneCars(t);
      out.push({ real: (performance.now() - start) / 1000, t, bus, cars: cars.map((c) => ({ id: c.id, centre: c.centre, speed: c.speed })) });
      if (performance.now() - start < ms) requestAnimationFrame(tick); else resolve(out);
    };
    requestAnimationFrame(tick);
  }), [id, seconds * 1000]);
}

const metres = (a, b) => Math.hypot((a[0] - b[0]) * 111320 * Math.cos((a[1] * Math.PI) / 180), (a[1] - b[1]) * 111200);

async function prove(page, name, label, body, decision, stopId, camera) {
  const vid = decision.vehicle_id;
  const v = body.smart.lines.vehicles.find((x) => x.vehicle_id === vid);
  const wait = waited(decision);
  // From before it stops to after it pulls out.
  const visit = v.stops.find((x) => x.stop === stopId && !x.passed && x.dep <= decision.t + 2 && x.arr >= decision.t - 600);
  const from = (visit ? visit.arr : decision.t) - 18;
  const to = decision.t + wait + 10;
  await page.evaluate((c) => window.__transitops.map.jumpTo(c), camera);
  await seek(page, from);
  await page.getByRole("button", { name: "1×", exact: true }).click();
  await page.getByRole("button", { name: /Play/ }).click();
  const run = await sample(page, vid, to - from);
  await page.getByRole("button", { name: /Pause/ }).click();
  writeFileSync(path.join(outDir, `route7-${label}-${name}.json`), JSON.stringify(run.map((x) => ({ real: x.real, t: x.t, s: x.bus?.s, v: x.bus?.speed })), null, 0));

  const span = run.at(-1).t - run[0].t, real = run.at(-1).real - run[0].real;
  check(`${name} ${label}: playback runs at 1×`, Math.abs(span / real - 1) < 0.1, `${span.toFixed(1)} s simulated in ${real.toFixed(1)} s`);
  const drawn = run.filter((x) => x.bus);
  check(`${name} ${label}: ${vid} is drawn with its route and destination`, drawn.length > run.length * 0.9 && /^7 /.test(drawn[0].bus.label),
    drawn[0]?.bus.label ?? "not drawn");
  // Braking: before the stop, speed falls progressively (no jumps), then a creep, then a full stop.
  // The standstill the wait follows: the last change from moving to stopped before it.
  let firstStop = -1;
  for (let i = 1; i < drawn.length; i += 1) {
    if (drawn[i].t > decision.t + 0.5) break;
    if (drawn[i].bus.speed < 0.01 && drawn[i - 1].bus.speed >= 0.01) firstStop = i;
  }
  // The braking: from its last speed peak before the stop, down to the stop.
  let peak = Math.max(0, firstStop);
  for (let i = firstStop - 1; i > 0; i -= 1) {
    if (drawn[i - 1].bus.speed < drawn[i].bus.speed - 0.05) { peak = i; break; }
    peak = i - 1;
  }
  const approach = drawn.slice(peak, Math.max(0, firstStop) + 1);
  const falls = approach.every((x, i) => i === 0 || x.bus.speed <= approach[i - 1].bus.speed + 0.05);
  const brakeTime = approach.length ? approach.at(-1).t - approach[0].t : 0;
  const meanDecel = brakeTime > 0 ? (approach[0].bus.speed - approach.at(-1).bus.speed) / brakeTime : 0;
  const creep = approach.filter((x) => drawn[firstStop].bus.s - x.bus.s < 3 && x.bus.speed > 0);
  const kmh = approach.map((x) => (x.bus.speed * 3.6).toFixed(0)).filter((x, i, a) => i === 0 || x !== a[i - 1]);
  check(`${name} ${label}: brakes progressively, creeps into position and stops`,
    firstStop > 0 && falls && brakeTime > 3 && meanDecel < 1.3 && creep.length > 0 && creep.every((x) => x.bus.speed <= 0.9),
    `${kmh.join("→")} km/h over ${brakeTime.toFixed(1)} s (mean ${meanDecel.toFixed(2)} m/s²), creep ≤ ${Math.max(0, ...creep.map((x) => x.bus.speed)).toFixed(2)} m/s over the last 3 m`);
  // Doors only after a full stop.
  if (visit) {
    const atDoors = drawn.filter((x) => x.t >= visit.doors - 1 && x.t <= visit.doors + 0.5);
    check(`${name} ${label}: stationary before the doors open (${(visit.doors - visit.arr).toFixed(1)} s after stopping)`,
      atDoors.length > 0 && atDoors.every((x) => x.bus.speed < 0.01), `${atDoors.length} frames`);
  }
  // The wait: stationary while cars pass close by; then it rolls out.
  const during = drawn.filter((x) => x.t > decision.t + 1 && x.t < decision.t + wait - 1);
  const passing = new Set(during.flatMap((x) => x.cars.filter((c) => c.speed > 2 && metres(c.centre, x.bus.centre) < 60).map((c) => c.id)));
  check(`${name} ${label}: waits ${wait} s for a gap while traffic passes`, during.length > 0 && during.every((x) => x.bus.speed < 0.01) && passing.size > 0,
    `${passing.size} cars passed: ${[...passing].slice(0, 4).join(", ")}; blocked by ${decision.blockers.slice(0, 3).join(", ")}`);
  const after = drawn.filter((x) => x.t > decision.t + wait + 0.5);
  const rolls = after.length > 3 && after.at(-1).bus.speed > 1 && after.every((x, i) => i === 0 || x.bus.speed >= after[i - 1].bus.speed - 0.05);
  check(`${name} ${label}: rolls out when clear`, rolls, `${(after[0]?.bus.speed ?? 0).toFixed(1)} → ${(after.at(-1)?.bus.speed ?? 0).toFixed(1)} m/s`);
  const labels = await page.evaluate(() => window.__transitops.map.getSource("line-buses").serialize().data.features.map((f) => f.properties.label));
  check(`${name} ${label}: the map labels Route 7 buses`, labels.some((l) => /^7 (Fairway|Conestoga) Station/.test(l)), labels.slice(0, 3).join(" | "));
  await page.screenshot({ path: path.join(outDir, `route7-${label}-${name}.png`) });
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
  await page.getByRole("button", { name: /^Route 7: traffic at a curb and a bay/ }).click();
  const response = page.waitForResponse((r) => r.url().includes("/api/simulate"), { timeout: 180_000 });
  await page.getByRole("button", { name: /Start simulation|Apply & restart/ }).click();
  const body = await (await response).json();
  await page.waitForFunction(() => window.__transitops.scene().trains.length > 0, null, { timeout: 90_000 });
  const pause = page.getByRole("button", { name: /Pause/ });
  if (await pause.count()) await pause.click();
  const waits = body.smart.decisions.filter((d) => d.trigger.startsWith("Pulling out of") && d.blockers?.length);
  const pick = (re) => waits.filter((d) => re.test(d.trigger)).sort((a, b) => waited(b) - waited(a))[0];
  const curb = pick(/curbside stop at King \/ Kitchener-Waterloo Collegiate/);
  const bay = pick(/bay exit at Fairway Station/);
  check(`${name}: traffic forces waits at the curbside stop and the bay`, Boolean(curb && bay),
    `${curb?.vehicle_id} ${waited(curb ?? {})} s at King / KCI; ${bay?.vehicle_id} ${waited(bay ?? {})} s at the Fairway bay`);
  const zone = (id) => body.smart.lines.zones.find((z) => z.id === id);
  const kci = zone("king-kci").lane, fw = zone("fairway-bay").lane;
  const mid = (lane, m) => lane.reduce((best, p) => (Math.abs(p[2] - m) < Math.abs(best[2] - m) ? p : best), lane[0]);
  const zoom = name === "phone" ? 17.6 : 18.2;
  if (curb) {
    const p = mid(kci, zone("king-kci").merge_rear_m + 6);
    await prove(page, name, "curbside", body, curb, "2542", { center: [p[0], p[1]], zoom, pitch: 55, bearing: 20 });
  }
  if (bay) {
    const p = mid(fw, zone("fairway-bay").merge_rear_m);
    await prove(page, name, "bay", body, bay, "3490", { center: [p[0], p[1]], zoom, pitch: 55, bearing: -30 });
  }
  check(`${name}: no page errors`, errors.length === 0, errors.join(" | "));
  await browser.close();
}

const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} checks passed`);
process.exit(passed === results.length ? 0 : 1);
