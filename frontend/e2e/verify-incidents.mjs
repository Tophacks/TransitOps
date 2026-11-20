// Browser check of ordinary runs: a fresh seed each run, shown on the map and
// replayable exactly; incidents drawn from the seed, the same for both
// policies; the run report; the Fairway button opening the running
// simulation at Fairway; no scripted replay left on the map.
//
//   node e2e/verify-incidents.mjs [BASE_URL] [OUT_DIR]
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

async function run(page, click) {
  const response = page.waitForResponse((r) => r.url().includes("/api/simulate"), { timeout: 180_000 });
  await click();
  const body = await (await response).json();
  await page.waitForFunction(() => window.__transitops.scene().trains.length > 0, null, { timeout: 90_000 });
  await page.waitForTimeout(1200);
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

  // 1. The Fairway button opens the running simulation at Fairway (starting a run if none).
  const first = await run(page, () => page.getByRole("button", { name: "Fairway", exact: true }).click());
  await page.waitForFunction(() => !window.__transitops.map.isMoving(), null, { timeout: 20_000 });
  const centre = await page.evaluate(() => window.__transitops.map.getCenter().toArray());
  const nearFairway = Math.hypot((centre[0] + 80.441) * 80_000, (centre[1] - 43.4235) * 111_000) < 1500;
  const badge = await page.locator(".simBadge").first().innerText();
  check(`${name}: Fairway opens the running simulation there`, nearFairway && /Simulated · seed \d+ · not live GPS/.test(badge),
    `${centre.map((x) => x.toFixed(4)).join(",")} · ${badge.split("\n")[0]}`);
  check(`${name}: no scripted replay on the map`, (await page.locator(".fairwayDemo, .fairwayModeBadge").count()) === 0);

  // 2. An ordinary run draws a fresh seed and natural incidents; both policies get the same list.
  const second = await run(page, () => page.getByRole("button", { name: /Apply & restart/ }).click());
  check(`${name}: each ordinary run draws a fresh seed`, second.scenario.seed !== first.scenario.seed,
    `${first.scenario.seed} → ${second.scenario.seed}`);
  check(`${name}: incidents happen naturally, the same for both policies`, second.scenario.incidents === "natural"
    && JSON.stringify(second.baseline.incidents.map((i) => i.id)) === JSON.stringify(second.smart.incidents.map((i) => i.id)),
    second.incidents.list.map((i) => `${i.id} ${i.kind}@${i.minute}`).join(", ") || "none drawn for this seed");
  const report = await page.locator(".runReport").innerText();
  check(`${name}: the run report shows the five measures`,
    ["trips covered", "Departure delay", "Missed stops", "ION transfers boarded", "Riders still waiting"].every((k) => report.includes(k)),
    report.split("\n")[0]);

  // 3. Replay the seed: the same incidents, decisions and report.
  const replay = await run(page, () => page.getByRole("button", { name: /^Replay seed/ }).click());
  const same = replay.scenario.seed === second.scenario.seed
    && JSON.stringify(replay.smart.incidents) === JSON.stringify(second.smart.incidents)
    && JSON.stringify(replay.smart.decisions) === JSON.stringify(second.smart.decisions)
    && JSON.stringify(replay.smart.report) === JSON.stringify(second.smart.report);
  check(`${name}: replaying the seed reproduces incidents, decisions and report`, same, `seed ${replay.scenario.seed}`);

  // 4. Live GPS stays separate from the simulation.
  await page.getByRole("button", { name: "Live GPS", exact: true }).click();
  await page.waitForTimeout(800);
  const liveStatus = await page.locator(".geoFeedStatus").innerText().catch(() => "");
  check(`${name}: Live GPS is a separate mode, labelled as the GRT feed`, /Live GPS/.test(liveStatus)
    && (await page.locator(".simBadge").count()) === 0, liveStatus.split("\n")[0]);
  await page.getByRole("button", { name: "Simulation", exact: true }).click();

  await page.screenshot({ path: path.join(outDir, `incidents-${name}.png`), fullPage: false });
  check(`${name}: no page errors`, errors.length === 0, errors.join(" | "));
  await browser.close();
}

const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} checks passed`);
process.exit(passed === results.length ? 0 : 1);
