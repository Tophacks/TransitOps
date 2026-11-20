// Browser check of UW Station (#17 part 3): with platform 6's front berth held
// by an out-of-service bus, each corridor bus reacts (alternate berth), the
// Decision Inspector explains it, and the map draws the simulator's buses.
//
//   node e2e/verify-uw.mjs [BASE_URL] [OUT_DIR]
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
  await page.getByRole("button", { name: /^UW berth blocked/ }).click();
  await page.getByRole("button", { name: /Start simulation|Apply & restart/ }).click();
  await page.waitForFunction(() => window.__transitops.scene().trains.length > 0, null, { timeout: 90_000 });
  await page.getByRole("button", { name: /Pause/ }).click();
  const decisions = await page.evaluate(() => window.__transitops.busDecisions());
  const alt = decisions.filter((d) => d.terminal === "uw" && /^alternate berth P6/.test(d.chosen));
  const buses = [...new Set(alt.map((d) => d.vehicle_id))];
  check(`${name}: three corridor buses take an alternate berth at platform 6`, buses.length >= 3, buses.join(", "));
  check(`${name}: each explains the blocked berth`, alt.every((d) => d.options.some((o) => !o.feasible && /out-of-service/.test(o.reason) || /OOS/.test(o.reason))),
    alt[0]?.options.map((o) => `${o.feasible ? "✓" : "✗"} ${o.option}`).join(" | ") ?? "");
  const reports = decisions.filter((d) => d.level === "station" && /Station report/.test(d.trigger));
  check(`${name}: stations report queues, predicted transfers and blocked berths`,
    reports.some((d) => d.options.some((o) => o.option === "blocked berths")) && reports.some((d) => d.options.some((o) => /predicted/.test(o.reason))),
    `${reports.length} reports`);

  // At B101's alternate berth the map draws it there, with the out-of-service bus in front.
  const d = alt[0];
  await seek(page, d.t + 25);
  await page.waitForTimeout(800);
  const berths = await page.evaluate(() => window.__transitops.map.getSource("uw-berths").serialize().data.features.map((f) => `${f.properties.id}:${f.properties.state}`));
  check(`${name}: the map shows the blocked and occupied berths`, berths.includes("P6-1078-1:blocked"), berths.filter((b) => b.startsWith("P6")).join(" "));
  const bus = await page.evaluate((id) => window.__transitops.scene().buses.find((b) => b.id === id), d.vehicle_id);
  check(`${name}: ${d.vehicle_id} is drawn in the station`, Boolean(bus), bus ? bus.centre.map((x) => x.toFixed(5)).join(",") : "missing");
  await page.evaluate(([lon, lat]) => window.__transitops.map.jumpTo({ center: [lon, lat], zoom: 18.2, pitch: 45, bearing: 63 }), bus.centre);
  await page.waitForTimeout(1500);
  await page.locator(".geoMapShell").screenshot({ path: path.join(outDir, `uw-${name}.png`) });

  if (name === "phone") await page.locator(".inspectorToggle").click();
  await seek(page, d.t + 1);
  await page.waitForTimeout(600);
  const item = page.locator(".decisionList button", { hasText: "alternate berth" }).first();
  if (await item.count()) await item.click(); else await page.getByRole("button", { name: "Show on map" }).click();
  await page.waitForTimeout(1500);
  const card = await page.locator(".decisionCard").innerText();
  check(`${name}: the inspector explains the alternate berth`, /UW Station/.test(card) && /alternate berth/.test(card) && /occupied by/.test(card),
    card.split("\n").slice(0, 4).join(" | "));
  check(`${name}: no page errors`, errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
}
const failed = results.filter((ok) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
