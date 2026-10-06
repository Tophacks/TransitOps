"use client";

import { useEffect, useMemo, useState } from "react";
import MapWorld from "./components/MapWorld";
import RegionalMap from "./components/RegionalMap";

const defaults = {
  traffic_multiplier: 1.25,
  train_delay: 5,
  passenger_demand: 135,
  fleet_size: 4,
};

const fallbackNetwork = {
  yard: { id: "YARD", name: "Regional Staging Yard", x: 10, y: 69 },
  stops: [
    { id: "CONESTOGA", name: "Conestoga", x: 17, y: 12 },
    { id: "NORTHFIELD", name: "Northfield", x: 24, y: 20 },
    { id: "RESEARCH_TECH", name: "Research & Technology", x: 30, y: 29 },
    { id: "UW", name: "University of Waterloo", x: 35.5, y: 38 },
    { id: "LAURIER", name: "Laurier-Waterloo Park", x: 41, y: 44 },
    { id: "WATERLOO_SQUARE", name: "Waterloo Public Square", x: 47, y: 50 },
    { id: "GRAND_RIVER_HOSPITAL", name: "Grand River Hospital", x: 54, y: 57 },
    { id: "CENTRAL", name: "Central Station", x: 60, y: 63 },
    { id: "CITY_HALL", name: "Kitchener City Hall", x: 65, y: 68 },
    { id: "KITCHENER_MARKET", name: "Kitchener Market", x: 72, y: 73 },
    { id: "FAIRWAY", name: "Fairway", x: 84, y: 86 },
  ],
  intersections: [
    { id: "I1", name: "King / Northfield", x: 22, y: 17 },
    { id: "I2", name: "University corridor", x: 34, y: 35 },
    { id: "I3", name: "Uptown Waterloo", x: 45, y: 48 },
    { id: "I4", name: "King / Victoria", x: 59, y: 62 },
    { id: "I5", name: "Downtown Kitchener", x: 67.5, y: 70 },
    { id: "I6", name: "Fairway corridor", x: 80, y: 82 },
  ],
  route_order: [
    "CONESTOGA", "NORTHFIELD", "RESEARCH_TECH", "UW", "LAURIER",
    "WATERLOO_SQUARE", "GRAND_RIVER_HOSPITAL", "CENTRAL",
    "CITY_HALL", "KITCHENER_MARKET", "FAIRWAY"
  ],
  cities: [
    { name: "Waterloo", x: 29, y: 18 },
    { name: "Kitchener", x: 69, y: 59 },
  ],
  passengers_per_sprite: 6,
  corridor_name: "Waterloo-Kitchener regional sandbox",
};

function Metric({ label, value, accent = false }) {
  return (
    <div className={`metric ${accent ? "metricAccent" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function PersonSprite({ index, moving = false }) {
  return (
    <span
      className={`pixelPerson ${moving ? "walkingPerson" : ""}`}
      style={{
        "--dx": `${(index % 6) * 7}px`,
        "--dy": `${Math.floor(index / 6) * 10}px`,
        "--delay": `${(index % 8) * -0.22}s`,
      }}
      aria-hidden="true"
    >
      <i />
    </span>
  );
}

function interpolate(points, progress) {
  const usable = points.filter(Boolean);
  if (usable.length < 2) return usable[0] || { x: 0, y: 0 };
  const p = ((progress % 1) + 1) % 1;
  const segmentFloat = p * (usable.length - 1);
  const index = Math.min(usable.length - 2, Math.floor(segmentFloat));
  const local = segmentFloat - index;
  const a = usable[index];
  const b = usable[index + 1];
  return {
    x: a.x + (b.x - a.x) * local,
    y: a.y + (b.y - a.y) * local,
  };
}

function AmbientTraffic({ points, minute = 0, traffic = 1 }) {
  const count = Math.max(7, Math.min(24, Math.round(9 * traffic)));
  return (
    <>
      {Array.from({ length: count }).map((_, index) => {
        const pos = interpolate(points, (minute * 0.013 + index / count) % 1);
        const reverse = index % 2 === 1;
        return (
          <span
            className={`ambientCar ${reverse ? "reverseCar" : ""}`}
            key={index}
            style={{
              left: `${pos.x}%`,
              top: `${pos.y + (reverse ? 1.3 : -1.3)}%`,
            }}
            aria-hidden="true"
          >
            <i />
          </span>
        );
      })}
    </>
  );
}

function TopDownBus({ bus, selected, onSelect }) {
  const load = Math.min(100, Math.round((bus.occupancy / bus.capacity) * 100));
  return (
    <button
      type="button"
      className={`busSprite ${bus.state === "YARD" ? "busIdle" : ""} ${selected?.data?.id === bus.id ? "selectedBus" : ""}`}
      style={{ left: `${bus.x}%`, top: `${bus.y}%` }}
      onClick={() => onSelect({ type: "bus", data: bus })}
      title={`${bus.id} → ${bus.target}`}
    >
      <span className="busRoute">R2</span>
      <span className="busBody">
        <span className="busWindshield" />
        <span className="busRoof">
          <i /><i /><i />
        </span>
        <span className="busRear" />
      </span>
      <span className="busTag">{bus.id}</span>
      <span className="busLoad"><i style={{ width: `${load}%` }} /></span>
    </button>
  );
}

function NetworkWorld({ network, snapshot, events, selected, onSelect, traffic }) {
  const stopById = Object.fromEntries(network.stops.map((stop) => [stop.id, stop]));
  const routeOrder = network.route_order || [];
  const points = [network.yard, ...routeOrder.map((id) => stopById[id])];
  const recentTrain = events?.some(
    (event) => event.kind === "train" && snapshot && snapshot.minute - event.minute >= 0 && snapshot.minute - event.minute <= 4
  );

  return (
    <div className="world" aria-label="Schematic real-time transit simulation">
      {(network.cities || []).map((city) => (
        <div
          className="cityLabel"
          key={city.name}
          style={{ left: `${city.x}%`, top: `${city.y}%` }}
        >
          <span>{city.name}</span>
        </div>
      ))}
      <div className="district districtA"><span>North Waterloo</span></div>
      <div className="district districtB"><span>South Kitchener</span></div>
      <div className="district districtC"><span>Downtown / Innovation</span></div>

      <svg className="worldRoads" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <polyline className="arterialEdge" points={points.map((p) => `${p.x},${p.y}`).join(" ")} />
        <polyline className="arterial" points={points.map((p) => `${p.x},${p.y}`).join(" ")} />
        <polyline className="laneDivider" points={points.map((p) => `${p.x},${p.y}`).join(" ")} />
        <line className="sideStreet" x1="46" y1="50" x2="46" y2="88" />
        <line className="sideStreet" x1="67" y1="38" x2="67" y2="84" />
        <line className="sideStreet" x1="75" y1="48" x2="88" y2="48" />
        <line className="railTrack" x1="5" y1="22" x2="95" y2="22" />
        <line className="railTrack railTrackTwo" x1="5" y1="25" x2="95" y2="25" />
        {Array.from({ length: 22 }).map((_, i) => (
          <line key={i} className="railTie" x1={7 + i * 4} y1="20.5" x2={7 + i * 4} y2="26.5" />
        ))}
      </svg>

      <AmbientTraffic points={points} minute={snapshot?.minute || 0} traffic={traffic} />

      <div className="railPlatform">
        <span>PLATFORM 2</span>
        <strong>Regional Rail</strong>
      </div>

      <div className="trainSprite" style={{ left: recentTrain ? "29%" : "13%", top: "15.5%" }}>
        <span className="trainNose" />
        <span className="trainCar" /><span className="trainCar" /><span className="trainCar" />
        <small>{recentTrain ? "ARRIVING" : "IN SERVICE"}</small>
      </div>

      <div className="transferWalkway" aria-hidden="true">
        {recentTrain && Array.from({ length: 10 }).map((_, index) => (
          <PersonSprite key={index} index={index} moving />
        ))}
      </div>

      <div className="hubComplex">
        <div className="hubHeader">
          <span>CENTRAL STATION / REGIONAL RAIL</span>
          <strong>regional transfer node</strong>
        </div>
        <div className="hubBays">
          {["A", "B", "C", "D"].map((bay, index) => (
            <div className="bay" key={bay}>
              <span>BAY {bay}</span>
              <i className={index < 2 ? "bayActive" : ""} />
            </div>
          ))}
        </div>
      </div>

      <button
        type="button"
        className={`yardNode ${selected?.type === "yard" ? "selectedNode" : ""}`}
        style={{ left: `${network.yard.x}%`, top: `${network.yard.y}%` }}
        onClick={() => onSelect({ type: "yard", data: network.yard })}
      >
        <span className="yardIcon">▥</span>
        <strong>NORTH YARD</strong>
        <small>staging</small>
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
          <span /><span /><span />
          <b>{intersection.id}</b>
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
              <span className="stopText">
                <strong>{stop.name}</strong>
                <small>{live ? `${live.queue} waiting` : "station"}</small>
              </span>
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
        <TopDownBus key={bus.id} bus={bus} selected={selected} onSelect={onSelect} />
      ))}

      <div className="worldHud">
        <div>
          <span>NETWORK LOAD</span>
          <strong>{snapshot?.waiting ?? 0} waiting</strong>
        </div>
        <div>
          <span>ROAD CONDITIONS</span>
          <strong>{traffic >= 1.6 ? "HEAVY" : traffic >= 1.15 ? "MODERATE" : "LIGHT"}</strong>
        </div>
        <div>
          <span>VISIBLE SCALE</span>
          <strong>1 person = {network.passengers_per_sprite || 5}</strong>
        </div>
      </div>

      <div className="worldLegend">
        <span>schematic operational digital twin · not to scale</span>
      </div>
    </div>
  );
}

function Inspector({ selected, snapshot }) {
  if (!selected) {
    return (
      <div className="inspectorEmpty">
        Select a bus, station, signal, or staging yard to inspect the live state.
      </div>
    );
  }

  if (selected.type === "bus") {
    const live = snapshot?.buses.find((bus) => bus.id === selected.data.id) || selected.data;
    const load = Math.round((live.occupancy / live.capacity) * 100);
    return (
      <div className="inspectorBody">
        <p className="eyebrow">VEHICLE TELEMETRY</p>
        <div className="inspectTitle"><h3>{live.id}</h3><span>Route R2</span></div>
        <div className="occupancyBar"><i style={{ width: `${load}%` }} /></div>
        <dl>
          <div><dt>Status</dt><dd>{live.state.replaceAll("_", " ")}</dd></div>
          <div><dt>Occupancy</dt><dd>{live.occupancy} / {live.capacity} · {load}%</dd></div>
          <div><dt>Next stop</dt><dd>{live.target}</dd></div>
          <div><dt>Schedule deviation</dt><dd>+{live.schedule_deviation} min</dd></div>
          <div><dt>Road segment</dt><dd>{Math.round((live.progress || 0) * 100)}%</dd></div>
        </dl>
      </div>
    );
  }

  if (selected.type === "stop") {
    const live = snapshot?.stops.find((stop) => stop.id === selected.data.id) || selected.data;
    return (
      <div className="inspectorBody">
        <p className="eyebrow">STATION OPERATIONS</p>
        <h3>{live.name}</h3>
        <dl>
          <div><dt>Passenger queue</dt><dd>{live.queue ?? 0}</dd></div>
          <div><dt>Displayed crowd units</dt><dd>{live.sprites ?? 0}</dd></div>
          <div><dt>Facility</dt><dd>{live.id === "CENTRAL" ? "Intermodal terminal" : "Surface stop"}</dd></div>
          <div><dt>Boarding state</dt><dd>{(live.queue ?? 0) > 40 ? "High demand" : "Normal"}</dd></div>
        </dl>
      </div>
    );
  }

  if (selected.type === "intersection") {
    const live = snapshot?.intersections.find((item) => item.id === selected.data.id) || selected.data;
    return (
      <div className="inspectorBody">
        <p className="eyebrow">SMART INTERSECTION</p>
        <h3>{live.name}</h3>
        <dl>
          <div><dt>Signal phase</dt><dd className={live.phase === "GREEN" ? "goodText" : "badText"}>{live.phase}</dd></div>
          <div><dt>Node</dt><dd>{live.id}</dd></div>
          <div><dt>Transit priority</dt><dd>Enabled</dd></div>
          <div><dt>Control mode</dt><dd>Local + network</dd></div>
        </dl>
      </div>
    );
  }

  return (
    <div className="inspectorBody">
      <p className="eyebrow">FLEET STAGING</p>
      <h3>{selected.data.name}</h3>
      <p className="inspectorCopy">Vehicles remain staged until scheduled dispatch or a demand-triggered release command reduces predicted capacity shortfall.</p>
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
    const delay = Math.max(100, 900 / speed);
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
      .slice(-8)
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

      if (!response.ok) throw new Error(`Simulation failed (${response.status})`);

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
          <div><strong>TransitOps</strong><span>Network Intelligence</span></div>
        </div>
        <div className="topStatus">
          <span className="scenarioName">Scenario 01 · Waterloo–Kitchener regional disruption</span>
          <span className="status"><i className={`statusDot ${playing ? "pulse" : ""}`} />{data ? (playing ? "Running" : "Paused") : "Ready"}</span>
        </div>
      </header>

      <section className="hero compactHero">
        <div>
          <p className="eyebrow">OPERATIONAL DIGITAL TWIN</p>
          <h1>Watch the system respond.</h1>
          <p className="heroCopy">
            A real interactive Waterloo–Kitchener street map with simulated fleet, passenger demand, station queues and smart-intersection state layered on top. Python remains the operational simulation engine.
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
            <button type="button" className="primaryPlayback" onClick={togglePlayback} disabled={!data}>
              {playing ? "Ⅱ  Pause" : "▶  Play"}
            </button>
            {[1, 2, 5, 10].map((value) => (
              <button type="button" key={value} className={speed === value ? "activeSpeed" : ""} onClick={() => setSpeed(value)}>
                {value}×
              </button>
            ))}
          </div>

          <div className="topMetrics">
            <div><span>WAITING</span><strong>{snapshot?.waiting ?? "—"}</strong></div>
            <div><span>ACTIVE BUSES</span><strong>{snapshot ? snapshot.buses.filter((b) => b.state !== "YARD").length : "—"}</strong></div>
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
        />

        <div className="timelineControl">
          <span>00:00</span>
          <input
            aria-label="Simulation timeline"
            type="range"
            min="0"
            max={Math.max(0, timeline.length - 1)}
            value={Math.min(frame, Math.max(0, timeline.length - 1))}
            onChange={(e) => { setFrame(Number(e.target.value)); setPlaying(false); }}
            disabled={!data}
          />
          <span>90:00</span>
        </div>
      </section>

      <div className="operationsDeck">
        <form className="controlPanel" onSubmit={runSimulation}>
          <div className="panelHeading">
            <div><p className="eyebrow">SCENARIO</p><h2>Operating conditions</h2></div>
            <span>editable</span>
          </div>
          <label>
            <div className="labelRow"><span>Road traffic</span><strong>{controls.traffic_multiplier.toFixed(2)}×</strong></div>
            <input type="range" min="0.5" max="2.5" step="0.05" value={controls.traffic_multiplier} onChange={(e) => update("traffic_multiplier", Number(e.target.value))} />
          </label>
          <label>
            <div className="labelRow"><span>Rail delay</span><strong>{controls.train_delay} min</strong></div>
            <input type="range" min="0" max="20" step="1" value={controls.train_delay} onChange={(e) => update("train_delay", Number(e.target.value))} />
          </label>
          <label>
            <div className="labelRow"><span>Transfer demand</span><strong>{controls.passenger_demand}</strong></div>
            <input type="range" min="20" max="300" step="5" value={controls.passenger_demand} onChange={(e) => update("passenger_demand", Number(e.target.value))} />
          </label>
          <label>
            <div className="labelRow"><span>Available fleet</span><strong>{controls.fleet_size}</strong></div>
            <input type="range" min="1" max="10" step="1" value={controls.fleet_size} onChange={(e) => update("fleet_size", Number(e.target.value))} />
          </label>
          <button className="runButton" disabled={loading}>{loading ? "Rebuilding scenario…" : data ? "Apply & restart" : "Start simulation"}</button>
          {error && <p className="error">{error}</p>}
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
            {visibleEvents.length ? visibleEvents.map((event, index) => (
              <div className={`eventRow event-${event.kind || "info"}`} key={`${event.minute}-${index}-${event.message}`}>
                <span className="eventTime">{String(event.minute).padStart(2, "0")}:00</span>
                <span>{event.message}</span>
              </div>
            )) : <div className="inspectorEmpty">Start the simulation to generate controller decisions and network events.</div>}
          </div>
        </section>

        <section className="impactPanel">
          <div className="panelHeading">
            <div><p className="eyebrow">RESULT</p><h2>Control impact</h2></div>
            <strong className="impactNumber">{improvement}</strong>
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
        <span>TransitOps concept simulator · Waterloo–Kitchener geography, experimental operations</span>
        <span>Python DES · FastAPI · Next.js · Vercel</span>
      </footer>
    </main>
  );
}
