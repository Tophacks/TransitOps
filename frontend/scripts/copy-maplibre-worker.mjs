import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = join(here, "..");
const distRoot = join(frontendRoot, "node_modules", "maplibre-gl", "dist");
const publicRoot = join(frontendRoot, "public");

mkdirSync(publicRoot, { recursive: true });

copyFileSync(
  join(distRoot, "maplibre-gl-worker.mjs"),
  join(publicRoot, "maplibre-gl-worker.mjs")
);

copyFileSync(
  join(distRoot, "maplibre-gl-shared.mjs"),
  join(publicRoot, "maplibre-gl-shared.mjs")
);

console.log("Copied MapLibre worker assets to frontend/public");
