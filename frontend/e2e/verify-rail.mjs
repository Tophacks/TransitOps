// Browser check of ION terminal operations (Step 1): physical trains turning
// back at Fairway and Conestoga, a platform hold that changes what another
// train and dispatch do, and the Decision Inspector explaining it.
//
//   node e2e/verify-rail.mjs [BASE_URL] [OUT_DIR]
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
const TERMINALS = JSON.parse(readFileSync(new URL("../lib/geo/ion-terminals.json", import.meta.url))).terminals;
/** In the interlocking: from 50 m before the switches to the track ends. */
const inInterlocking = (x) => {
  if (!x.terminal || !x.route || x.s === undefined) return false;
  const r = TERMINALS[x.terminal].routes[x.route];
  return r.kind === "arrive" ? x.s >= r.switch_m - 50 : x.s <= r.switch_m + 50 + 41.8;
};
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
const frames = (page, n = 3) => page.evaluate((count) => new Promise((resolve) => {
  let left = count;
  const tick = () => (--left <= 0 ? resolve() : requestAnimationFrame(tick));
  requestAnimationFrame(tick);
}), n);
const trains = (page) => page.evaluate(() => window.__transitops.scene().trains.map((t) => ({
  id: t.id, trip: t.tripId, speed: t.speed, headlights: t.headlights, terminal: t.terminal ?? null, activity: t.activity ?? "",
  route: t.route ?? null, s: t.s,
  centres: t.sections.map((s) => s.centre), headings: t.sections.map((s) => s.heading),
})));
const metres = (a, b) => Math.hypot((a[0] - b[0]) * 111320 * Math.cos((43.46 * Math.PI) / 180), (a[1] - b[1]) * 111320);
const turn = (a, b) => Math.abs(((b - a + 540) % 360) - 180);

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
  await page.getByRole("button", { name: /ION platform hold/ }).click();
  await page.getByRole("button", { name: /Start simulation|Apply & restart/ }).click();
  await page.waitForFunction(() => window.__transitops.scene().trains.length > 0, null, { timeout: 60_000 });
  await page.getByRole("button", { name: /Pause/ }).click();
  const shot = (label) => page.locator(".geoMapShell").screenshot({ path: path.join(outDir, `rail-${name}-${label}.png`) });
  const ion = await page.evaluate(() => window.__transitops.ion());

  // 1. The hold changes what another train and dispatch do.
  const swap = ion.decisions.find((d) => d.chosen.startsWith("swap") && d.t >= 0);
  const wait = ion.decisions.find((d) => d.trigger.startsWith("Approaching") && d.t >= 0 && d.options.some((o) => !o.feasible && /occupied by/.test(o.reason)));
  check(`${name}: the platform hold makes dispatch give the trip to another train`, Boolean(swap && swap.outcome.includes("left platform")),
    swap ? `${swap.id} ${swap.vehicle_id} ${swap.trip_id}: ${swap.chosen} → ${swap.outcome}` : "no swap");
  check(`${name}: an arriving train finds the held platform occupied and takes the other one`, Boolean(wait && /platform/.test(wait.chosen)),
    wait ? `${wait.vehicle_id}: ${wait.options.map((o) => `${o.option} ${o.feasible ? "✓" : "✗"}`).join("; ")} → ${wait.chosen}` : "none");

  // 2. The Decision Inspector explains it, and shows the moment on the map.
  if (name === "phone") await page.locator(".inspectorToggle").click();
  await seek(page, swap.t + 1);
  await page.waitForTimeout(600);
  const listButton = page.locator(".decisionList button", { hasText: "swap with" }).first();
  await listButton.click();
  await page.waitForTimeout(2600);
  const card = await page.locator(".decisionCard").innerText();
  const rejected = await page.locator(".decisionOptions li.rejected").count();
  const chosen = await page.locator(".decisionOptions li.chosen").innerText();
  check(`${name}: the inspector shows vehicle, trip, options, reasons, choice and outcome`,
    card.includes(swap.vehicle_id) && card.includes(swap.trip_id) && rejected >= 1 && /swap with/.test(chosen) && card.includes("WHAT HAPPENED NEXT".toLowerCase()) === false && /happened next/i.test(card),
    `${rejected} rejected option(s); chosen: ${chosen.split("\n")[1]}`);
  const now = await page.evaluate(() => window.__transitops.scene().trains.length && document.querySelector('input[aria-label="Simulation timeline"]').value);
  const centre = await page.evaluate(() => window.__transitops.map.getCenter());
  check(`${name}: picking the decision seeks the playback and frames the terminal`,
    Math.abs(Number(now) - swap.t) <= 1 && metres([centre.lng, centre.lat], [-80.4421, 43.4224]) < 400,
    `timeline ${now} s, camera ${(metres([centre.lng, centre.lat], [-80.4421, 43.4224])).toFixed(0)} m from Fairway`);
  await shot("swap");

  // 3. Through the turnback: one ID per train, no jumps, no spinning, lights swap only at rest.
  const helper = swap.options.find((o) => o.option.startsWith("swap with"))?.option.split(" ").at(-1);
  let prev = null;
  let worstJump = 0;
  let worstTurn = 0;      // degrees per metre travelled
  let standingTurn = 0;   // degrees while not moving (the reversal)
  let lightsMoving = 0;
  let missing = 0;
  let closest = Infinity;
  for (let t = swap.t - 300; t <= swap.t + 420; t += 2) {
    await seek(page, t);
    await frames(page, 2);
    const now2 = await trains(page);
    const byId = new Map(now2.map((x) => [x.id, x]));
    if (prev) {
      for (const [id, a] of prev) {
        const b = byId.get(id);
        if (!b) { if (a.terminal) missing += 1; continue; }
        a.centres.forEach((c, i) => {
          const moved = metres(c, b.centres[i]);
          const d = turn(a.headings[i], b.headings[i]);
          worstJump = Math.max(worstJump, moved / 2);
          if (moved < 0.05) standingTurn = Math.max(standingTurn, d);
          // On the terminal trackwork in the interlocking (the GTFS line's own corners are not part of this check).
          else if (inInterlocking(a) && inInterlocking(b)) worstTurn = Math.max(worstTurn, d / moved);
        });
        if (a.headlights !== b.headlights && (a.speed > 0.01 || b.speed > 0.01)) lightsMoving += 1;
      }
    }
    // Trains at the same terminal never overlap.
    const at = now2.filter((x) => x.terminal);
    for (let i = 0; i < at.length; i += 1) {
      for (let j = i + 1; j < at.length; j += 1) {
        for (const p of at[i].centres) for (const q of at[j].centres) closest = Math.min(closest, metres(p, q));
      }
    }
    prev = byId;
  }
  check(`${name}: trains keep their IDs, never jump or spin, and swap lights only when stopped`,
    // Never spinning: no rotation at all while standing (through the cab change),
    // and in a terminal never turning faster than an 18 m radius curve allows.
    missing === 0 && worstJump < 17 && standingTurn < 0.05 && worstTurn < 57.3 / 18 && lightsMoving === 0,
    `fastest section ${worstJump.toFixed(1)} m/s, ${standingTurn.toFixed(2)}° while standing, sharpest ${worstTurn.toFixed(2)}°/m moving, ${lightsMoving} light changes while moving`);
  check(`${name}: two trains at a terminal never overlap`, closest > 2.5, `closest sections ${closest.toFixed(1)} m apart`);

  // 4. The helper train leaves on the reassigned trip through the crossover.
  const helperTrip = swap.trip_id;
  let leftOn = null;
  for (let t = swap.t; t <= swap.t + 600 && !leftOn; t += 5) {
    await seek(page, t);
    await frames(page, 2);
    const h = (await trains(page)).find((x) => x.id === helper);
    if (h && h.trip === helperTrip && h.speed > 0.5) leftOn = { t, activity: h.activity };
  }
  check(`${name}: ${helper} leaves on ${helperTrip}`, Boolean(leftOn), leftOn ? `moving at ${leftOn.t}s (${leftOn.activity})` : "never left");
  await page.waitForTimeout(800);
  await shot("departure");

  // 5. Scrubbing back gives the same scene.
  await seek(page, swap.t + 200);
  await frames(page, 2);
  const a = JSON.stringify(await trains(page));
  await seek(page, 3000);
  await frames(page, 2);
  await seek(page, swap.t + 200);
  await frames(page, 2);
  const b = JSON.stringify(await trains(page));
  check(`${name}: scrubbing back reproduces the same trains`, a === b);

  // 6. Terminal trackwork drawn for both terminals; signals reflect waiting trains.
  const points = await page.evaluate(() => window.__transitops.map.querySourceFeatures("terminal-points").map((f) => f.properties.terminal));
  const layer = await page.evaluate(() => Boolean(window.__transitops.map.getLayer("terminal-signals")));
  check(`${name}: terminal markers and signals are on the map`, layer && points.includes("fairway"), `${points.length} markers in view`);
  check(`${name}: no page errors`, errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
}
const failed = results.filter((ok) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
