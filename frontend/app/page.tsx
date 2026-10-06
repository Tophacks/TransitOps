"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";

import RegionalMap from "./components/RegionalMap";
import { useSumoStream } from "../lib/useSumoStream";
import type {
  Controls,
  Network,
  Selection,
  SimulationResponse,
  Snapshot,
} from "../lib/types";

const DEFAULTS: Controls = {
  traffic_multiplier: 1.15,
  train_delay: 4,
  passenger_demand: 110,
  fleet_size: 3,
};

const FALLBACK_NETWORK: Network = {
  yard: {
    id: "YARD",
    name: "Corridor Staging Yard",
    lon: -80.5035,
    lat: 43.4515,
  },
  stops: [
    { id: "UW", name: "University of Waterloo", lon: -80.54128, lat: 43.47336 },
    { id: "WATERLOO_SQUARE", name: "Waterloo Public Square", lon: -80.5228, lat: 43.46419 },
    { id: "GRAND_RIVER_HOSPITAL", name: "Grand River Hospital", lon: -80.51185, lat: 43.45723 },
    { id: "CENTRAL", name: "Central Station", lon: -80.49944, lat: 43.45333 },
  ],
  intersections: [
    { id: "I1", name: "University corridor", lon: -80.533, lat: 43.4695 },
    { id: "I2", name: "Uptown Waterloo", lon: -80.52, lat: 43.462 },
    { id: "I3", name: "King / Victoria", lon: -80.505, lat: 43.455 },
  ],
  route_order: [
    "UW",
    "WATERLOO_SQUARE",
    "GRAND_RIVER_HOSPITAL",
    "CENTRAL",
  ],
  passengers_per_sprite: 6,
  corridor_name: "Waterloo Corridor Test Scenario",
};

function Inspector({
  selected,
  snapshot,
}: {
  selected: Selection;
  snapshot: Snapshot | null;
}) {
  if (!selected) {
    return (
      <div className="inspectorEmpty">
        Select a bus, station, signal, or staging yard on the map.
      </div>
    );
  }

  if (selected.type === "bus") {
    const live =
      snapshot?.buses.find((bus) => bus.id === selected.data.id) ?? selected.data;
    const load = Math.round((live.occupancy / live.capacity) * 100);

    return (
      <div className="inspectorBody">
        <p className="eyebrow">VEHICLE TELEMETRY</p>
        <div className="inspectTitle">
          <h3>{live.id}</h3>
          <span>{live.state.replaceAll("_", " ")}</span>
        </div>
        <div className="occupancyBar">
          <i style={{ width: `${load}%` }} />
        </div>
        <dl>
          <div><dt>Occupancy</dt><dd>{live.occupancy} / {live.capacity} · {load}%</dd></div>
          <div><dt>Next stop</dt><dd>{live.target}</dd></div>
          <div><dt>Schedule deviation</dt><dd>+{live.schedule_deviation} min</dd></div>
          <div><dt>Segment progress</dt><dd>{Math.round(live.progress * 100)}%</dd></div>
        </dl>
      </div>
    );
  }

  if (selected.type === "stop") {
    const live =
      snapshot?.stops.find((stop) => stop.id === selected.data.id) ?? selected.data;

    return (
      <div className="inspectorBody">
        <p className="eyebrow">STATION OPERATIONS</p>
        <h3>{live.name}</h3>
        <dl>
          <div><dt>Passenger queue</dt><dd>{live.queue ?? 0}</dd></div>
          <div><dt>Demand state</dt><dd>{(live.queue ?? 0) > 40 ? "High" : "Normal"}</dd></div>
          <div><dt>Longitude</dt><dd>{live.lon.toFixed(4)}</dd></div>
          <div><dt>Latitude</dt><dd>{live.lat.toFixed(4)}</dd></div>
        </dl>
      </div>
    );
  }

  if (selected.type === "intersection") {
    const live =
      snapshot?.intersections.find((node) => node.id === selected.data.id) ??
      selected.data;

    return (
      <div className="inspectorBody">
        <p className="eyebrow">SMART INTERSECTION</p>
        <h3>{live.name}</h3>
        <dl>
          <div><dt>Signal phase</dt><dd className={live.phase === "GREEN" ? "goodText" : "badText"}>{live.phase ?? "—"}</dd></div>
          <div><dt>Transit priority</dt><dd>Enabled</dd></div>
          <div><dt>Control mode</dt><dd>Network coordinated</dd></div>
        </dl>
      </div>
    );
  }

  return (
    <div className="inspectorBody">
      <p className="eyebrow">FLEET STAGING</p>
      <h3>{selected.data.name}</h3>
      <p className="inspectorCopy">
        Dispatch logic remains in the Python simulation engine. The TypeScript client only renders the resulting state.
      </p>
    </div>
  );
}

export default function Home() {
  const [controls, setControls] = useState<Controls>(DEFAULTS);
  const [data, setData] = useState<SimulationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const [selected, setSelected] = useState<Selection>(null);
  const sumo = useSumoStream();

  const timeline = data?.smart.timeline ?? [];
  const snapshot = timeline[frame] ?? null;
  const network = data?.network ?? FALLBACK_NETWORK;
  const smart = data?.smart.metrics;
  const baseline = data?.baseline.metrics;

  useEffect(() => {
    if (!playing || timeline.length === 0) return;

    const timer = window.setInterval(() => {
      setFrame((current) => {
        if (current >= timeline.length - 1) {
          setPlaying(false);
          return current;
        }
        return current + 1;
      });
    }, Math.max(80, 900 / speed));

    return () => window.clearInterval(timer);
  }, [playing, speed, timeline.length]);

  const visibleEvents = useMemo(() => {
    if (!data || !snapshot) return [];

    return data.smart.events
      .filter((event) => event.minute <= snapshot.minute)
      .slice(-8)
      .reverse();
  }, [data, snapshot]);

  const improvement = data
    ? data.impact.average_wait_reduction
    : null;

  function update<K extends keyof Controls>(key: K, value: Controls[K]) {
    setControls((current) => ({ ...current, [key]: value }));
  }

  async function runSimulation(event?: FormEvent) {
    event?.preventDefault();
    setLoading(true);
    setError("");
    setPlaying(false);
    setFrame(0);
    setSelected(null);

    try {
      const response = await fetch("/api/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(controls),
      });

      if (!response.ok) {
        throw new Error(`Simulation failed (${response.status})`);
      }

      const result = (await response.json()) as SimulationResponse;
      setData(result);
      setPlaying(true);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not run the simulation."
      );
    } finally {
      setLoading(false);
    }
  }

  function togglePlayback() {
    if (!data) return;

    if (frame >= timeline.length - 1) {
      setFrame(0);
    }

    setPlaying((current) => !current);
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="brandMark">T</div>
          <div>
            <strong>TransitOps</strong>
            <span>Network Intelligence</span>
          </div>
        </div>
        <div className="topStatus">
          <span className="scenarioName">Waterloo Corridor Test Scenario</span>
          <span className="status">
            <i className={`statusDot ${playing || sumo.state === "running" ? "pulse" : ""}`} />
            {sumo.state === "running"
              ? "SUMO live"
              : sumo.state === "building_network"
                ? "Building SUMO network"
                : data
                  ? (playing ? "Prototype running" : "Prototype paused")
                  : "Ready"}
          </span>
        </div>
      </header>

      <section className="hero compactHero">
        <div>
          <p className="eyebrow">OPERATIONAL DIGITAL TWIN</p>
          <h1>Python computes. TypeScript renders.</h1>
          <p className="heroCopy">
            Four stops, three smart signals, a staging yard, background road traffic, and a transfer surge. Python makes the decisions; SUMO handles the traffic.
          </p>
        </div>
        <div className="clockCard">
          <span>SIMULATION CLOCK</span>
          <strong>{String(snapshot?.minute ?? 0).padStart(2, "0")}:00</strong>
          <small>{playing ? `running at ${speed}×` : "paused"}</small>
        </div>
      </section>

      <section className="simulatorShell">
        <div className="simTopbar">
          <div className="playback">
            <button
              type="button"
              className={sumo.state === "running" ? "activeSpeed" : ""}
              onClick={sumo.state === "running" ? sumo.stop : sumo.start}
              disabled={
                (!sumo.available && sumo.state !== "running") ||
                sumo.state === "connecting" ||
                sumo.state === "building_network"
              }
              title={sumo.available ? "Start the cloud SUMO digital twin" : "Set NEXT_PUBLIC_SUMO_WS_URL to your SUMO service"}
            >
              {sumo.state === "running"
                ? "■ Stop SUMO"
                : sumo.state === "building_network"
                  ? "Building network…"
                  : sumo.state === "connecting"
                    ? "Connecting SUMO…"
                    : "◆ Run SUMO twin"}
            </button>
            <button
              type="button"
              className="primaryPlayback"
              onClick={togglePlayback}
              disabled={!data}
            >
              {playing ? "Ⅱ  Pause" : "▶  Play"}
            </button>
            {[1, 2, 5, 10].map((value) => (
              <button
                type="button"
                key={value}
                className={speed === value ? "activeSpeed" : ""}
                onClick={() => setSpeed(value)}
              >
                {value}×
              </button>
            ))}
          </div>

          <div className="topMetrics">
            <div><span>WAITING</span><strong>{snapshot?.waiting ?? "—"}</strong></div>
            <div><span>ACTIVE BUSES</span><strong>{snapshot ? snapshot.buses.filter((bus) => bus.state !== "YARD").length : "—"}</strong></div>
            <div><span>AVG WAIT</span><strong>{smart ? `${smart.average_wait.toFixed(1)}m` : "—"}</strong></div>
            <div><span>TRAFFIC</span><strong>{controls.traffic_multiplier.toFixed(2)}×</strong></div>
          </div>
        </div>

        <RegionalMap
          network={network}
          snapshot={snapshot}
          selected={selected}
          onSelect={setSelected}
          traffic={controls.traffic_multiplier}
          sumoSnapshot={sumo.snapshot}
        />

        <div className="timelineControl">
          <span>00:00</span>
          <input
            aria-label="Simulation timeline"
            type="range"
            min="0"
            max={Math.max(0, timeline.length - 1)}
            value={Math.min(frame, Math.max(0, timeline.length - 1))}
            onChange={(event) => {
              setFrame(Number(event.target.value));
              setPlaying(false);
            }}
            disabled={!data}
          />
          <span>60:00</span>
        </div>
      </section>

      <div className="operationsDeck">
        <form className="controlPanel" onSubmit={runSimulation}>
          <div className="panelHeading">
            <div><p className="eyebrow">SCENARIO</p><h2>Operating conditions</h2></div>
            <span>Python backend</span>
          </div>

          <label>
            <div className="labelRow"><span>Road traffic</span><strong>{controls.traffic_multiplier.toFixed(2)}×</strong></div>
            <input type="range" min="0.5" max="2.5" step="0.05" value={controls.traffic_multiplier} onChange={(event) => update("traffic_multiplier", Number(event.target.value))} />
          </label>

          <label>
            <div className="labelRow"><span>Train delay</span><strong>{controls.train_delay} min</strong></div>
            <input type="range" min="0" max="30" step="1" value={controls.train_delay} onChange={(event) => update("train_delay", Number(event.target.value))} />
          </label>

          <label>
            <div className="labelRow"><span>Transfer demand</span><strong>{controls.passenger_demand}</strong></div>
            <input type="range" min="20" max="250" step="5" value={controls.passenger_demand} onChange={(event) => update("passenger_demand", Number(event.target.value))} />
          </label>

          <label>
            <div className="labelRow"><span>Available fleet</span><strong>{controls.fleet_size}</strong></div>
            <input type="range" min="1" max="6" step="1" value={controls.fleet_size} onChange={(event) => update("fleet_size", Number(event.target.value))} />
          </label>

          <button className="runButton" disabled={loading}>
            {loading ? "Running Python simulation…" : data ? "Apply & restart" : "Start simulation"}
          </button>

          {error && <p className="error">{error}</p>}
          {sumo.error && <p className="error">{sumo.error}</p>}
          {sumo.message && sumo.state !== "error" && (
            <p className="inspectorCopy">{sumo.message}</p>
          )}
        </form>

        <section className="inspector">
          <Inspector selected={selected} snapshot={snapshot} />
        </section>

        <section className="decisionPanel">
          <div className="panelHeading">
            <div><p className="eyebrow">CONTROLLER</p><h2>Decision feed</h2></div>
            <span>{visibleEvents.length} recent</span>
          </div>
          <div className="eventFeed compactFeed">
            {visibleEvents.length ? (
              visibleEvents.map((event, index) => (
                <div className={`eventRow event-${event.kind || "info"}`} key={`${event.minute}-${index}-${event.message}`}>
                  <span className="eventTime">{String(event.minute).padStart(2, "0")}:00</span>
                  <span>{event.message}</span>
                </div>
              ))
            ) : (
              <div className="inspectorEmpty">
                Start the simulation to generate controller decisions.
              </div>
            )}
          </div>
        </section>

        <section className="impactPanel">
          <div className="panelHeading">
            <div><p className="eyebrow">RESULT</p><h2>Control impact</h2></div>
            <strong className="impactNumber">
              {improvement === null
                ? "—"
                : `${improvement >= 0 ? "−" : "+"}${Math.abs(improvement).toFixed(1)} min`}
            </strong>
          </div>
          <div className="impactRows">
            <div><span>Baseline avg wait</span><strong>{baseline ? `${baseline.average_wait.toFixed(1)} min` : "—"}</strong></div>
            <div><span>TransitOps avg wait</span><strong>{smart ? `${smart.average_wait.toFixed(1)} min` : "—"}</strong></div>
            <div><span>Pre-stage actions</span><strong>{smart?.buses_staged ?? "—"}</strong></div>
            <div><span>Signal priority</span><strong>{smart?.signal_priority_requests ?? "—"}</strong></div>
          </div>
        </section>
      </div>

      <footer>
        <span>TransitOps · Python control + SUMO/TraCI + TypeScript visualization</span>
        <span>FastAPI · SUMO · TraCI · MapLibre · deck.gl · Next.js</span>
      </footer>
    </main>
  );
}
