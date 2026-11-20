import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const output = path.join(process.cwd(), "public", "pyodide");
mkdirSync(output, { recursive: true });

for (const name of ["pyodide.mjs", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]) {
  copyFileSync(require.resolve(`pyodide/${name}`), path.join(output, name));
}
