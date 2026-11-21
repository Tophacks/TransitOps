// Browser check of ION closures (#17 part 1): a southbound closure between
// Grand River Hospital and Central; diverted trains on the northbound track,
// opposing trains waiting for the single-track section, explained.
//
//   node e2e/verify-network.mjs [BASE_URL] [OUT_DIR]
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
  await page.getByRole("button", { name: /ION track closure/ }).click();
  await page.getByRole("button", { name: /Start simulation|Apply & restart/ }).click();
  await page.waitForFunction(() => window.__transitops.scene().trains.length > 0, null, { timeout: 90_000 });
  await page.getByRole("button", { name: /Pause/ }).click();
  const ion = await page.evaluate(() => window.__transitops.ion());
  check(`${name}: the closure resolves to the Northfield-Betzner diversion`,
    ion.closures?.[0]?.diversion === "south-closed:750968494-754590778", JSON.stringify(ion.closures?.[0] ?? null));
  const logs = Object.values(ion.sections ?? {}).flat();
  const diverted = logs.find((e) => e.role === "divert" && e.from_s >= 0);
  const wait = ion.decisions.find((d) => /single-track section/.test(d.chosen) && d.chosen.startsWith("hold") && d.t >= 0);
  check(`${name}: a train waits for the single-track section until the other's rear clears`,
    Boolean(wait && /granted at/.test(wait.outcome) && wait.options.some((o) => !o.feasible && /rear clears/.test(o.reason))),
    wait ? `${wait.vehicle_id}: ${wait.chosen} → ${wait.outcome}` : "none");
  const closed = await page.evaluate(() => window.__transitops.map.getSource("ion-closure").serialize().data.features.map((f) => f.properties.kind));
  check(`${name}: the map shows the closed stretch and the diversion`, closed.includes("closed") && closed.includes("diversion"), closed.join(","));

  // The diverted train runs on the northbound track (its plan's diversion route).
  await seek(page, diverted.from_s + 400);
  await page.waitForTimeout(600);
  const pose = await page.evaluate((id) => window.__transitops.scene().trains.find((t) => t.id === id), diverted.vehicle_id);
  check(`${name}: ${diverted.vehicle_id} is drawn on the diversion`,
    Boolean(pose && pose.route === ion.closures[0].diversion && pose.direction === "south"),
    pose ? `${pose.route} · ${pose.activity}` : "not in service");

  // Inspector explains it; picking it frames the train.
  if (name === "phone") await page.locator(".inspectorToggle").click();
  await seek(page, wait.t + 1);
  await page.waitForTimeout(600);
  const item = page.locator(".decisionList button", { hasText: "single-track section" }).first();
  if (await item.count()) await item.click(); else await page.getByRole("button", { name: "Show on map" }).click();
  await page.waitForTimeout(2500);
  const card = await page.locator(".decisionCard").innerText();
  check(`${name}: the inspector explains the wait`, /ION line/.test(card) && /single-track section/.test(card) && /rear clears/.test(card),
    card.split("\n").slice(0, 3).join(" | "));
  await page.locator(".geoMapShell").screenshot({ path: path.join(outDir, `network-${name}.png`) });
  check(`${name}: no page errors`, errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
}
const failed = results.filter((ok) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
