"use client";

import { useEffect, useMemo, useState } from "react";

const defaults = {
  traffic_multiplier: 1.25,
  train_delay: 5,
  passenger_demand: 135,
  fleet_size: 4,
};

const fallbackNetwork = {
  yard: { id: "YARD", name: "North Yard", x: 8, y: 83 },
  stops: [
    { id: "CENTRAL", name: "Central Terminal", x: 24, y: 45 },
    { id: "KING", name: "King", x: 46, y: 67 },
    { id: "MARKET", name: "Market", x: 67, y: 58 },
    { id: "UNIVERSITY", name: "University", x: 82, y: 74 },
  ],
  intersections: [
    { id: "I1", name: "Central / King", x: 36, y: 56 },
    { id: "I2", name: "King / Market", x: 57, y: 63 },
    { id: "I3", name: "Market / University", x: 75, y: 66 },
  ],
  passengers_per_sprite: 5,
};

function Metric({ label, value, accent = false }) {
  return (
    <div className={`metric ${accent ? "metricAccent" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function PersonSprite({ index }) {
  return (
    <span
      className="pixelPerson"
      style={{
        "--dx": `${(index % 6) * 7}px`,
        "--dy": `${Math.floor(index / 6) * 10}px`,
      }}
      aria-hidden="true"
    >
      <i />
    </span>
  );
}

function NetworkWorld({ network, snapshot, selected, onSelect }) {
  const stopById = Object.fromEntries(network.stops.map((stop) => [stop.id, stop]));
  const route = ["CENTRAL", "KING", "MARKET", "UNIVERSITY"];
  const points = [network.yard, ...route.map((id) => stopById[id])];

  return (
    <div className="world" aria-label="Schematic real-time transit simulation">
      <svg className="worldRoads" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <polyline
          className="roadShadow"
          points={points.map((p) => `${p.x},${p.y}`).join(" ")}
        />
        <polyline
          className="road"
          points={points.map((p) => `${p.x},${p.y}`).join(" ")}
        />
        <line className="rail" x1="9" y1="27" x2="91" y2="27" />
        <line className="railSleeper" x1="9" y1="30" x2="91" y2="30" />
      </svg>

      <div className="trainSprite" style={{ left: "15%", top: "19%" }}>
        <span className="trainCars">▰▰▰</span>
        <small>Regional rail</small>
      </div>

      <button
        type="button"
        className={`yardNode ${selected?.type === "yard" ? "selectedNode" : ""}`}
        style={{ left: `${network.yard.x}%`, top: `${network.yard.y}%` }}
        onClick={() => onSelect({ type: "yard", data: network.yard })}
      >
        <span className="yardIcon">▤</span>
        <strong>YARD</strong>
      </button>

      {snapshot?.intersections.map((intersection) => (
        <button
          type="button"
          key={intersection.id}
          className={`signalNode ${intersection.phase === "GREEN" ? "green" : "red"} ${selected?.data?.id === intersection.id ? "selectedNode" : ""}`}
          style={{ left: `${intersection.x}%`, top: `${intersection.y}%` }}
          onClick={() => onSelect({ type: "intersection", data: intersection })}
          title={`${intersection.name}: ${intersection.phase}`}
        >
          <span />
          <span />
          <span />
        </button>
      ))}

      {network.stops.map((stop) => {
        const live = snapshot?.stops.find((item) => item.id === stop.id);
        const sprites = live?.sprites ?? 0;
        return (
          <div
            className={`stopCluster ${stop.id === "CENTRAL" ? "centralStop" : ""}`}
            key={stop.id}
            style={{ left: `${stop.x}%`, top: `${stop.y}%` }}
          >
            <button
              type="button"
              className={`stopNode ${selected?.data?.id === stop.id ? "selectedNode" : ""}`}
              onClick={() => onSelect({ type: "stop", data: { ...stop, ...live } })}
            >
              <span className="stopDisc" />
              <strong>{stop.name}</strong>
              <small>{live ? `${live.queue} waiting` : "station"}</small>
            </button>
            <div className="peoplePatch" aria-label={live ? `${live.queue} people waiting` : "No queue data"}>
              {Array.from({ length: sprites }).map((_, index) => (
                <PersonSprite key={index} index={index} />
              ))}
            </div>
          </div>
        );
      })}

      {snapshot?.buses.map((bus) => (
        <button
          type="button"
          key={bus.id}
          className={`busSprite ${bus.state === "YARD" ? "busIdle" : ""} ${selected?.data?.id === bus.id ? "selectedBus" : ""}`}
          style={{ left: `${bus.x}%`, top: `${bus.y}%` }}
          onClick={() => onSelect({ type: "bus", data: bus })}
          title={`${bus.id} → ${bus.target}`}
        >
          <span className="busWindows">▪▪▪</span>
          <strong>{bus.id}</strong>
        </button>
      ))}

      <div className="worldLegend">
        <span><i className="legendPerson" /> = {network.passengers_per_sprite || 5} passengers</span>
        <span>schematic / not to scale</span>
      </div>
    </div>
  );
}

function Inspector({ selected, snapshot }) {
  if (!selected) {
    return (
      <div className="inspectorEmpty">
        Click a bus, station, signal, or the yard to inspect its live state.
      </div>
    );
  }

  if (selected.type === "bus") {
    const live = snapshot?.buses.find((bus) => bus.id === selected.data.id) || selected.data;
    return (
      <div className="inspectorBody">
        <p className="eyebrow">VEHICLE</p>
        <h3>{live.id}</h3>
        <dl>
          <div><dt>Status</dt><dd>{live.state.replaceAll("_", " ")}</dd></div>
          <div><dt>Occupancy</dt><dd>{live.occupancy} / {live.capacity}</dd></div>
          <div><dt>Next stop</dt><dd>{live.target}</dd></div>
          <div><dt>Schedule deviation</dt><dd>+{live.schedule_deviation} min</dd></div>
          <div><dt>Segment progress</dt><dd>{Math.round((live.progress || 0) * 100)}%</dd></div>
        </dl>
      </div>
    );
  }

  if (selected.type === "stop") {
    const live = snapshot?.stops.find((stop) => stop.id === selected.data.id) || selected.data;
    return (
      <div className="inspectorBody">
        <p className="eyebrow">STATION</p>
        <h3>{live.name}</h3>
        <dl>
          <div><dt>Waiting</dt><dd>{live.queue ?? 0}</dd></div>
          <div><dt>Visible crowd units</dt><dd>{live.sprites ?? 0}</dd></div>
          <div><dt>Mode</dt><dd>{live.id === "CENTRAL" ? "Intermodal hub" : "Bus stop"}</dd></div>
        </dl>
      </div>
    );
  }

  if (selected.type === "intersection") {
    const live = snapshot?.intersections.find((item) => item.id === selected.data.id) || selected.data;
    return (
      <div className="inspectorBody">
        <p className="eyebrow">INTERSECTION</p>
        <h3>{live.name}</h3>
        <dl>
          <div><dt>Signal</dt><dd className={live.phase === "GREEN" ? "goodText" : "badText"}>{live.phase}</dd></div>
          <div><dt>Node</dt><dd>{live.id}</dd></div>
          <div><dt>Priority capable</dt><dd>Yes</dd></div>
        </dl>
      </div>
    );
  }

  return (
    <div className="inspectorBody">
      <p className="eyebrow">DEPOT</p>
      <h3>{selected.data.name}</h3>
      <p className="inspectorCopy">Buses wait here until scheduled dispatch or a demand-triggered pre-staging decision.</p>
    </div>
  );
}

export default function Home() {
  const [controls, setControls] = useState(defaults);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const [selected, setSelected] = useState(null);

  const timeline = data?.smart?.timeline || [];
  const snapshot = timeline[frame] || null;
  const network = data?.network || fallbackNetwork;
  const smart = data?.smart?.metrics;
  const baseline = data?.baseline?.metrics;

  useEffect(() => {
    if (!playing || timeline.length === 0) return;

    const delay = Math.max(110, 900 / speed);
    const timer = window.setInterval(() => {
      setFrame((current) => {
        if (current >= timeline.length - 1) {
          setPlaying(false);
          return current;
        }
        return current + 1;
      });
    }, delay);

    return () => window.clearInterval(timer);
  }, [playing, speed, timeline.length]);

  const visibleEvents = useMemo(() => {
    if (!data || !snapshot) return [];
    return data.smart.events
      .filter((event) => event.minute <= snapshot.minute)
      .slice(-10)
      .reverse();
  }, [data, snapshot]);

  const improvement = useMemo(() => {
    if (!data) return "—";
    const n = data.impact.average_wait_reduction;
    return `${n >= 0 ? "−" : "+"}${Math.abs(n).toFixed(1)} min`;
  }, [data]);

  function update(key, value) {
    setControls((current) => ({ ...current, [key]: value }));
  }

  async function runSimulation(event) {
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

      const result = await response.json();
      setData(result);
      setFrame(0);
      setPlaying(true);
    } catch (err) {
      setError(err.message || "Could not run the simulation.");
    } finally {
      setLoading(false);
    }
  }

  function togglePlayback() {
    if (!data) return;
    if (frame >= timeline.length - 1) setFrame(0);
    setPlaying((value) => !value);
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
        <div className="status">
          <span className={`statusDot ${playing ? "pulse" : ""}`} />
          {data ? (playing ? "Simulation running" : "Simulation paused") : "Simulation environment"}
        </div>
      </header>

      <section className="hero compactHero">
        <div>
          <p className="eyebrow">2D OPERATIONS DIGITAL TWIN</p>
          <h1>See the network make decisions.</h1>
          <p className="heroCopy">
            A schematic transit world where passengers queue, buses move stop-to-stop,
            signals change, and the controller reacts to demand and delay.
          </p>
        </div>
        <div className="clockCard">
          <span>SIM TIME</span>
          <strong>{String(snapshot?.minute ?? 0).padStart(2, "0")}:00</strong>
          <small>1 frame = 1 simulated minute</small>
        </div>
      </section>

      <div className="gameLayout">
        <aside className="sidePanel">
          <form className="controlPanel" onSubmit={runSimulation}>
            <div className="panelHeading">
              <div>
                <p className="eyebrow">SCENARIO</p>
                <h2>Control room</h2>
              </div>
              <span>60 min</span>
            </div>

            <label>
              <div className="labelRow"><span>Traffic load</span><strong>{Number(controls.traffic_multiplier).toFixed(2)}×</strong></div>
              <input type="range" min="0.5" max="2.5" step="0.05" value={controls.traffic_multiplier} onChange={(e) => update("traffic_multiplier", Number(e.target.value))} />
            </label>
            <label>
              <div className="labelRow"><span>Train delay</span><strong>{controls.train_delay} min</strong></div>
              <input type="range" min="0" max="20" step="1" value={controls.train_delay} onChange={(e) => update("train_delay", Number(e.target.value))} />
            </label>
            <label>
              <div className="labelRow"><span>Transfer demand</span><strong>{controls.passenger_demand}</strong></div>
              <input type="range" min="20" max="300" step="5" value={controls.passenger_demand} onChange={(e) => update("passenger_demand", Number(e.target.value))} />
            </label>
            <label>
              <div className="labelRow"><span>Fleet</span><strong>{controls.fleet_size} buses</strong></div>
              <input type="range" min="1" max="10" step="1" value={controls.fleet_size} onChange={(e) => update("fleet_size", Number(e.target.value))} />
            </label>

            <button className="runButton" disabled={loading}>
              {loading ? "Building scenario…" : data ? "Restart scenario" : "Start simulation"}
            </button>
            {error && <p className="error">{error}</p>}
          </form>

          <div className="inspector">
            <Inspector selected={selected} snapshot={snapshot} />
          </div>
        </aside>

        <section className="networkPanel mainWorld">
          <div className="worldToolbar">
            <div>
              <p className="eyebrow">LIVE NETWORK</p>
              <h2>Demo corridor</h2>
            </div>
            <div className="playback">
              <button type="button" onClick={togglePlayback} disabled={!data}>
                {playing ? "Ⅱ Pause" : "▶ Play"}
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
          </div>

          <NetworkWorld
            network={network}
            snapshot={snapshot}
            selected={selected}
            onSelect={setSelected}
          />

          <div className="timelineControl">
            <span>00:00</span>
            <input
              aria-label="Simulation timeline"
              type="range"
              min="0"
              max={Math.max(0, timeline.length - 1)}
              value={Math.min(frame, Math.max(0, timeline.length - 1))}
              onChange={(e) => {
                setFrame(Number(e.target.value));
                setPlaying(false);
              }}
              disabled={!data}
            />
            <span>60:00</span>
          </div>

          <div className="metricsGrid">
            <Metric label="Waiting now" value={snapshot ? snapshot.waiting : "—"} accent />
            <Metric label="Avg wait" value={smart ? `${smart.average_wait.toFixed(1)} min` : "—"} />
            <Metric label="Pre-staged" value={smart ? smart.buses_staged : "—"} />
            <Metric label="Signal priority" value={smart ? smart.signal_priority_requests : "—"} />
          </div>
        </section>

        <aside className="rightRail">
          <section className="miniPanel">
            <div className="panelHeading">
              <div>
                <p className="eyebrow">OPERATIONS</p>
                <h2>Live state</h2>
              </div>
            </div>
            <div className="opsRows">
              <div><span>Buses active</span><strong>{snapshot ? snapshot.buses.filter((b) => b.state !== "YARD").length : 0}</strong></div>
              <div><span>Passengers waiting</span><strong>{snapshot?.waiting ?? 0}</strong></div>
              <div><span>Completed trips</span><strong>{smart?.completed_trips ?? 0}</strong></div>
              <div><span>Traffic multiplier</span><strong>{controls.traffic_multiplier.toFixed(2)}×</strong></div>
            </div>
          </section>

          <section className="miniPanel eventMini">
            <div className="panelHeading">
              <div>
                <p className="eyebrow">DECISION FEED</p>
                <h2>Controller events</h2>
              </div>
            </div>
            <div className="eventFeed compactFeed">
              {visibleEvents.length ? visibleEvents.map((event, index) => (
                <div className={`eventRow event-${event.kind || "info"}`} key={`${event.minute}-${index}-${event.message}`}>
                  <span className="eventTime">{String(event.minute).padStart(2, "0")}:00</span>
                  <span>{event.message}</span>
                </div>
              )) : (
                <div className="inspectorEmpty">Start the simulation to populate the operations feed.</div>
              )}
            </div>
          </section>
        </aside>
      </div>

      <section className="comparison">
        <div className="panelHeading">
          <div>
            <p className="eyebrow">CONTROL IMPACT</p>
            <h2>Baseline vs TransitOps</h2>
          </div>
          <div className="impactNumber">{improvement}</div>
        </div>
        <div className="comparisonGrid">
          <div className="comparisonCard"><span>Baseline avg wait</span><strong>{baseline ? `${baseline.average_wait.toFixed(1)} min` : "—"}</strong></div>
          <div className="comparisonCard active"><span>TransitOps avg wait</span><strong>{smart ? `${smart.average_wait.toFixed(1)} min` : "—"}</strong></div>
          <div className="comparisonCard"><span>Maximum queue</span><strong>{smart?.max_queue ?? "—"}</strong></div>
          <div className="comparisonCard"><span>Still waiting at end</span><strong>{smart?.left_waiting ?? "—"}</strong></div>
        </div>
      </section>

      <footer>
        <span>TransitOps concept simulator · schematic network, not a vehicle-dynamics model</span>
        <span>Python DES · FastAPI · Next.js · Vercel</span>
      </footer>
    </main>
  );
}
