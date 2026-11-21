// Browser check of bus connection holds (Step 2a): B103 held at Central for
// the ION-N0 transfer wave, explained in the Decision Inspector.
//
//   node e2e/verify-holds.mjs [BASE_URL] [OUT_DIR]
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
const metres = (a, b) => Math.hypot((a[0] - b[0]) * 111320 * Math.cos((43.46 * Math.PI) / 180), (a[1] - b[1]) * 111320);

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
  await page.waitForFunction(() => window.__transitops.scene().buses.length > 0, null, { timeout: 60_000 });
  await page.getByRole("button", { name: /Pause/ }).click();

  const holds = await page.evaluate(() => window.__transitops.busDecisions());
  const hold = holds.find((d) => d.chosen.startsWith("hold"));
  check(`${name}: dispatch holds a bus at Central for the ION transfer wave`,
    Boolean(hold && hold.terminal === "central" && hold.chosen === "hold 2 min" && /ION-N0/.test(hold.trigger)),
    hold ? `${hold.vehicle_id} ${hold.chosen}: ${hold.trigger}` : "no hold");

  if (name === "phone") await page.locator(".inspectorToggle").click();
  await seek(page, hold.t + 1);
  await page.waitForTimeout(600);
  await page.locator(".decisionList button", { hasText: "hold 2 min" }).first().click();
  await page.waitForTimeout(2500);
  const card = await page.locator(".decisionCard").innerText();
  const centre = await page.evaluate(() => window.__transitops.map.getCenter());
  check(`${name}: the inspector shows the hold with its costs and savings, framed at the stop`,
    /hold 1 min/.test(card) && /doors would close 21:55/.test(card) && /net \+/.test(card) && /boarded during the hold/.test(card)
      && metres([centre.lng, centre.lat], hold.location) < 80,
    `camera ${metres([centre.lng, centre.lat], hold.location).toFixed(0)} m from the stop`);
  await page.locator(".geoMapShell").screenshot({ path: path.join(outDir, `holds-${name}.png`) });

  // The held bus stands at the stop until 23:00, then leaves.
  const where = async (t) => {
    await seek(page, t);
    await page.waitForTimeout(250);
    return page.evaluate((id) => window.__transitops.scene().buses.find((b) => b.id === id), hold.vehicle_id);
  };
  const during = [];
  for (const t of [hold.t + 5, hold.t + 60, hold.t + 115]) during.push(await where(t));
  const after = await where(hold.t + 150);
  const still = during.every((b) => b && metres(b.centre, during[0].centre) < 0.5);
  check(`${name}: ${hold.vehicle_id} waits at the stop through the hold and then departs`,
    still && after && metres(after.centre, during[0].centre) > 5,
    `moved ${after ? metres(after.centre, during[0].centre).toFixed(0) : "?"} m by ${Math.round((hold.t + 150) / 60)} min`);
  check(`${name}: no page errors`, errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
}
const failed = results.filter((ok) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
