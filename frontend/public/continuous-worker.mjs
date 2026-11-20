import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/pyodide.mjs";

let advance;

self.onmessage = async ({ data }) => {
  try {
    if (data.type === "start") {
      const [python, response] = await Promise.all([
        loadPyodide({ indexURL: "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/" }),
        fetch("/api/simulate/continuous", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data.controls),
        }),
      ]);
      if (!response.ok) throw new Error(`Continuous setup failed (${response.status}).`);
      const { sources, ...settings } = await response.json();
      const root = "/transitops";
      python.FS.mkdir(root);
      for (const [name, source] of Object.entries(sources)) {
        python.FS.writeFile(`${root}/${name}`, source);
      }
      python.globals.set("run_settings", JSON.stringify(settings));
      advance = python.runPython(`
import json
import sys
sys.path.insert(0, "${root}")
from continuous import ContinuousRun
continuous_run = ContinuousRun(json.loads(run_settings))
continuous_run.next_json
`);
    }
    self.postMessage({ result: JSON.parse(advance()) });
  } catch (error) {
    self.postMessage({ error: error.message || "Continuous simulation failed." });
  }
};
