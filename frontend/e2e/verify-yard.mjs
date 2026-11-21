// Browser check of the Dutton yard (#17 part 2): a train found faulty at
// Conestoga goes back to the yard, a ready spare pulls out and takes a trip,
// and with the north lead blocked dispatch reassigns a train on the line.
//
//   node e2e/verify-yard.mjs [BASE_URL] [OUT_DIR]
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
  await page.getByRole("button", { name: /^ION train fault/ }).click();
  await page.getByRole("button", { name: /Start simulation|Apply & restart/ }).click();
  await page.waitForFunction(() => window.__transitops.scene().trains.length > 0, null, { timeout: 90_000 });
  await page.getByRole("button", { name: /Pause/ }).click();
  const ion = await page.evaluate(() => window.__transitops.ion());
  const out = ion.yard?.moves?.find((m) => m.kind === "out");
  const back = ion.yard?.moves?.find((m) => m.kind === "in");
  check(`${name}: a spare pulls out of Dutton for a trip`, Boolean(out && out.route.startsWith("yard-out:north:")),
    out ? `${out.vehicle_id} for ${out.for} at ${Math.round(out.t0)} s` : "none");
  check(`${name}: the faulty train returns to Dutton`, Boolean(back), back ? `${back.vehicle_id} to ${back.track}` : "none");
  const trip = ion.trips.find((t) => t.id === out.for);
  check(`${name}: the spare runs that trip`, trip?.vehicle_id === out.vehicle_id, `${trip?.id} by ${trip?.vehicle_id}`);
  const tracks = await page.evaluate(() => window.__transitops.map.getSource("dutton-yard").serialize().data.features.length);
  check(`${name}: the map draws the yard`, tracks >= 10, `${tracks} features`);

  // The spare is drawn on its yard route while pulling out.
  await seek(page, (out.t0 + out.t1) / 2);
  await page.waitForTimeout(800);
  const pose = await page.evaluate((id) => window.__transitops.scene().trains.find((t) => t.id === id), out.vehicle_id);
  check(`${name}: ${out.vehicle_id} is drawn on the pull-out route`, Boolean(pose && pose.route === out.route),
    pose ? `${pose.route} · ${pose.activity}` : "not drawn");
  await page.evaluate((id) => {
    const train = window.__transitops.scene().trains.find((t) => t.id === id);
    window.__transitops.map.jumpTo({ center: train.sections[2].centre, zoom: 17.4, pitch: 45, bearing: 20 });
  }, out.vehicle_id);
  await page.waitForTimeout(1500);
  await page.locator(".geoMapShell").screenshot({ path: path.join(outDir, `yard-pullout-${name}.png`) });

  // The inspector explains dispatch's choice.
  if (name === "phone") await page.locator(".inspectorToggle").click();
  const d = ion.decisions.find((x) => /has no train/.test(x.trigger));
  await seek(page, d.t + 1);
  await page.waitForTimeout(600);
  const item = page.locator(".decisionList button", { hasText: "spare" }).first();
  if (await item.count()) await item.click(); else await page.getByRole("button", { name: "Show on map" }).click();
  await page.waitForTimeout(1500);
  const card = await page.locator(".decisionCard").innerText();
  check(`${name}: the inspector compares the yard release with reassignment`,
    /reassign the next arriving train/.test(card) && /preparation \(modelled\)/.test(card) && /Dutton/.test(card),
    card.split("\n").slice(0, 3).join(" | "));
  if (name === "phone") await page.locator(".inspectorToggle").click();

  // Blocked north lead: dispatch reassigns, nothing pulls out.
  await page.getByRole("button", { name: /yard lead blocked/ }).click();
  await page.getByRole("button", { name: /Start simulation|Apply & restart/ }).click();
  await page.waitForFunction(() => window.__transitops.ion()?.yard?.block?.lead === "north", null, { timeout: 90_000 });
  const blocked = await page.evaluate(() => window.__transitops.ion());
  const bd = blocked.decisions.find((x) => /has no train/.test(x.trigger));
  check(`${name}: with the lead blocked dispatch reassigns a train on the line`,
    /^reassign the next arriving train/.test(bd?.chosen ?? "") && !blocked.yard.moves.some((m) => m.kind === "out"),
    bd?.chosen ?? "none");
  check(`${name}: no page errors`, errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
}
const failed = results.filter((ok) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
