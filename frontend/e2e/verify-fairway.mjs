// Browser check of the Fairway terminal replay: entry and exit, the train's
// turnback on the drawn trackwork, the bus layover and its signs, the
// selected-vehicle details, and that the simulation still runs afterwards.
//
//   node e2e/verify-fairway.mjs [BASE_URL] [OUT_DIR]
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
const data = JSON.parse(readFileSync(new URL("../lib/geo/fairway.json", import.meta.url)));
const results = [];
const check = (name, ok, detail = "") => {
  results.push(ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` · ${detail}` : ""}`);
};
const frames = (page, n = 3) => page.evaluate((count) => new Promise((resolve) => {
  let left = count;
  const tick = () => (--left <= 0 ? resolve() : requestAnimationFrame(tick));
  requestAnimationFrame(tick);
}), n);
const seek = async (page, t) => {
  await page.evaluate((value) => window.__transitops.fairway.clock.seek(value), t);
  await frames(page, 3);
};

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
  const shot = (label) => page.locator(".geoMapShell").screenshot({ path: path.join(outDir, `fairway-${name}-${label}.png`) });

  const visibility = () => page.evaluate(() => Object.fromEntries(["vehicle-bodies", "live-bodies", "grt-points", "ion-rail-heads"]
    .map((id) => [id, window.__transitops.map.getLayoutProperty(id, "visibility") ?? "visible"])));
  await page.waitForTimeout(1500); // let the mode's layer visibility settle
  const beforeDemo = await visibility();

  // 1. Entry: a Fairway button opens the replay, labelled modelled, other vehicles hidden.
  await page.getByRole("button", { name: "Fairway", exact: true }).click();
  await page.waitForFunction(() => window.__transitops.fairway, null, { timeout: 20_000 });
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await page.waitForFunction(() => !window.__transitops.map.isMoving(), null, { timeout: 20_000 });
  const hidden = await page.evaluate(() => ["vehicle-bodies", "live-bodies", "grt-points", "ion-rail-heads"]
    .filter((id) => window.__transitops.map.getLayer(id))
    .every((id) => window.__transitops.map.getLayoutProperty(id, "visibility") === "none"));
  const badge = await page.locator(".fairwayModeBadge").innerText();
  check(`${name}: Fairway entry hides live and simulated vehicles and is labelled modeled`, hidden && /modeled/i.test(badge) && /not live/i.test(badge), badge.split("\n")[0]);

  // 2. The drawn trackwork is the move graph: crossover ways 754590785/6 drawn, every move vertex on a drawn edge.
  const drawn = await page.evaluate(() => window.__transitops.map.querySourceFeatures("fairway-track").map((f) => f.properties.name));
  const needed = Object.keys(data.track.edges);
  check(`${name}: switches, both crossover branches and platform tracks are drawn`, needed.every((e) => drawn.includes(e)), `${new Set(drawn).size}/${needed.length} edges`);

  // 3. Through the whole replay: one train id, sections on the rails, no jumps, lights swap only at rest.
  const duration = await page.evaluate(() => window.__transitops.fairway.replay.duration);
  let worstJump = 0;
  let ids = new Set();
  let lightSwapMoving = 0;
  let prev = null;
  const frameCheck = [];
  for (let t = 0; t <= duration; t += 0.5) {
    await seek(page, t);
    const s = await page.evaluate(() => {
      const { scene, replay } = window.__transitops.fairway;
      const solids = window.__transitops.map.querySourceFeatures("fairway-solids").map((f) => f.properties);
      return {
        t: scene.t,
        id: scene.train.id,
        speed: scene.train.speed,
        headlights: scene.train.headlights,
        centres: scene.train.sections.map((x) => x.centre),
        heads: solids.filter((p) => p.role === "headlight").length,
        trainParts: solids.filter((p) => p.id === scene.train.id).length,
        busParts: solids.filter((p) => p.id === scene.bus.id).length,
        bus: scene.bus,
        stopAt: replay.stopAt,
      };
    });
    ids.add(s.id);
    if (prev) {
      for (let i = 0; i < s.centres.length; i += 1) {
        const dx = (s.centres[i][0] - prev.centres[i][0]) * 111320 * Math.cos((43.42 * Math.PI) / 180);
        const dy = (s.centres[i][1] - prev.centres[i][1]) * 111320;
        worstJump = Math.max(worstJump, Math.hypot(dx, dy) / 0.5);
      }
      if (s.headlights !== prev.headlights && (s.speed > 0 || prev.speed > 0)) lightSwapMoving += 1;
    }
    // Only in view: querySourceFeatures covers the tiles on screen (the
    // train enters the frame from the west).
    if (t > 15) frameCheck.push(s.trainParts > 20 && (s.bus.opacity === 0 || s.busParts > 10));
    prev = s;
  }
  check(`${name}: one train id, never faster than its top speed between frames, lights swap only when stopped`,
    ids.size === 1 && worstJump < 13 && lightSwapMoving === 0 && frameCheck.every(Boolean),
    `${[...ids][0]}, fastest section ${worstJump.toFixed(1)} m/s, ${frameCheck.filter(Boolean).length}/${frameCheck.length} frames drawn`);

  // 4. The bus on layover: Layover in the details card, arrival sign, parked in its bay.
  const times = await page.evaluate(() => {
    const r = window.__transitops.fairway.replay;
    return { stop: r.stopAt, sign: r.signChangeAt, pull: r.pullOutAt, off: r.lightsOffAt, on: r.lightsOnAt, depart: r.departAt };
  });
  await seek(page, times.stop + 2);
  await page.waitForTimeout(400);
  const card = await page.locator(".fairwayCard").innerText();
  const signs = async () => page.evaluate(() => window.__transitops.map.queryRenderedFeatures({ layers: ["fairway-signs"] }).map((f) => f.properties));
  const before = (await signs()).find((p) => p.id === "GRT-7-FW");
  check(`${name}: parked bus shows Layover and its arrival sign`, /Layover/.test(card) && before?.full === "7  Fairway", `${before?.full} · ${card.split("\n").slice(2, 4).join(" · ")}`);
  await shot("layover");

  // 5. Sign changes before the bus moves.
  await seek(page, times.sign + 1);
  await page.waitForTimeout(300);
  const changed = await page.evaluate(() => window.__transitops.fairway.scene.bus);
  const after = (await signs()).find((p) => p.id === "GRT-7-FW");
  check(`${name}: sign changes to the next trip while still parked`, after?.full === "7  Conestoga" && changed.phase === "layover" && changed.speed === 0,
    `${after?.full}, ${(times.pull - times.sign).toFixed(0)} s before pull-out`);
  await seek(page, times.on + 1);
  await page.waitForTimeout(500);
  await shot("ends-changed");

  // 6. Pull-out: moves only forwards along the one-way lane.
  let s0 = -1;
  let forwards = true;
  for (let t = times.pull; t <= times.pull + 30; t += 1) {
    await seek(page, t);
    const s = await page.evaluate(() => window.__transitops.fairway.scene.bus.s);
    if (s < s0 - 1e-6) forwards = false;
    s0 = s;
  }
  await seek(page, times.pull + 6);
  await page.waitForTimeout(400);
  const moving = await page.locator(".fairwayCard").innerText();
  check(`${name}: bus pulls out forwards along the lane and leaves Layover`, forwards && !/Layover/.test(moving), moving.split("\n")[2]);

  // 7. Sign text: route only at wider zoom, legible size at street zoom.
  const textField = await page.evaluate(() => JSON.stringify(window.__transitops.map.getLayoutProperty("fairway-signs", "text-field")));
  check(`${name}: route number alone at wider zoom, route and destination at street zoom`, /"step",\["zoom"\],\["get","short"\],16,\["get","full"\]/.test(textField), textField);

  // 8. Train departs through the crossover; screenshot it there.
  let crossing = null;
  for (let t = times.depart; t <= duration && crossing === null; t += 0.5) {
    await seek(page, t);
    const edge = await page.evaluate(() => window.__transitops.fairway.scene.train.edge);
    if (edge === "crossover_nw_se") crossing = t;
  }
  await page.waitForTimeout(500);
  await shot("crossover");
  check(`${name}: train leaves through crossover 754590785`, crossing !== null, `on the crossover at ${crossing?.toFixed(1)} s`);

  // 9. Vehicles stay clear of the replay panels in the default framing.
  const clear = await page.evaluate(() => {
    const map = window.__transitops.map;
    const shell = document.querySelector(".geoMapShell").getBoundingClientRect();
    const boxes = [...document.querySelectorAll(".fairwayCard, .fairwayBar, .geoMapOverlay")].map((el) => el.getBoundingClientRect());
    const { replay } = window.__transitops.fairway;
    const covered = [];
    for (const t of [replay.stopAt, replay.signChangeAt, replay.pullOutAt + 6, replay.departAt + 14]) {
      const scene = replay.scene(t);
      const vehicles = [["train", scene.train.sections[2].centre]];
      if (t <= replay.pullOutAt + 6) vehicles.push(["bus", scene.bus.centre]);
      for (const [label, p] of vehicles) {
        const q = map.project(p);
        const x = shell.left + q.x;
        const y = shell.top + q.y;
        const inside = x > shell.left && x < shell.right && y > shell.top && y < shell.bottom;
        if (!inside || boxes.some((b) => x > b.left && x < b.right && y > b.top && y < b.bottom)) covered.push(`${label}@${t.toFixed(0)}`);
      }
    }
    return covered;
  });
  check(`${name}: train and bus in view and clear of the panels at the key moments`, clear.length === 0, clear.join(", ") || "all visible");

  // 10. Exit restores the map: Escape leaves, the simulation still runs.
  await page.keyboard.press("Escape");
  await frames(page, 4);
  const restored = await page.evaluate(() => ({
    demo: Boolean(window.__transitops.fairway),
    layers: window.__transitops.map.getStyle().layers.filter((l) => l.id.startsWith("fairway-")).length,
  }));
  const afterDemo = await visibility();
  const layersBack = JSON.stringify(afterDemo) === JSON.stringify(beforeDemo);
  await page.getByRole("button", { name: /Start simulation|Apply & restart/ }).click();
  await page.waitForFunction(() => window.__transitops.scene().buses.length > 0, null, { timeout: 30_000 });
  const buses = await page.evaluate(() => window.__transitops.scene().buses.length);
  check(`${name}: exit removes the replay and the simulation plays again`, !restored.demo && restored.layers === 0 && layersBack && buses > 0,
    `layer visibility ${layersBack ? "restored" : `changed ${JSON.stringify(beforeDemo)} -> ${JSON.stringify(afterDemo)}`}, ${buses} simulated buses after exit`);
  check(`${name}: no page errors`, errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
}
const failed = results.filter((ok) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
