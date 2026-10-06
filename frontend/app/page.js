"use client";

import { useMemo, useState } from "react";

const defaults = {
  traffic_multiplier: 1.25,
  train_delay: 5,
  passenger_demand: 135,
  fleet_size: 4,
};

function Metric({ label, value, accent = false }) {
  return (
    <div className={`metric ${accent ? "metricAccent" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function EventFeed({ events }) {
  return (
    <div className="eventFeed">
      {events.slice(-12).reverse().map((event, index) => (
        <div className="eventRow" key={`${event.minute}-${index}-${event.message}`}>
          <span className="eventTime">{String(event.minute).padStart(2, "0")}:00</span>
          <span>{event.message}</span>
        </div>
      ))}
    </div>
  );
}

export default function Home() {
  const [controls, setControls] = useState(defaults);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const smart = data?.smart?.metrics;
  const baseline = data?.baseline?.metrics;

  const improvement = useMemo(() => {
    if (!data) return "—";
    const n = data.impact.average_wait_reduction;
    return `${n >= 0 ? "−" : "+"}${Math.abs(n).toFixed(1)} min`;
  }, [data]);

  function update(key, value) {
    setControls((current) => ({ ...current, [key]: value }));
  }

  async function runSimulation(event) {
    event.preventDefault();
    setLoading(true);
    setError("");

    try {
      const response = await fetch("/api/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(controls),
      });

      if (!response.ok) {
        throw new Error(`Simulation failed (${response.status})`);
      }

      setData(await response.json());
    } catch (err) {
      setError(err.message || "Could not run the simulation.");
    } finally {
      setLoading(false);
    }
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
          <span className="statusDot" />
          Simulation environment
        </div>
      </header>

      <section className="hero">
        <div>
          <p className="eyebrow">TRANSIT CONTROL SYSTEM</p>
          <h1>Coordinate the network, not just the vehicle.</h1>
          <p className="heroCopy">
            Test how pre-staging, connection holding and traffic-aware dispatch
            affect passenger outcomes across a stressed transit corridor.
          </p>
        </div>
        <div className="heroBadge">
          <span>CONTROL MODE</span>
          <strong>AI-assisted</strong>
        </div>
      </section>

      <div className="workspace">
        <form className="controlPanel" onSubmit={runSimulation}>
          <div className="panelHeading">
            <div>
              <p className="eyebrow">SCENARIO</p>
              <h2>Operations controls</h2>
            </div>
            <span>60 min</span>
          </div>

          <label>
            <div className="labelRow">
              <span>Traffic load</span>
              <strong>{Number(controls.traffic_multiplier).toFixed(2)}×</strong>
            </div>
            <input
              type="range"
              min="0.5"
              max="2.5"
              step="0.05"
              value={controls.traffic_multiplier}
              onChange={(e) => update("traffic_multiplier", Number(e.target.value))}
            />
          </label>

          <label>
            <div className="labelRow">
              <span>Train delay</span>
              <strong>{controls.train_delay} min</strong>
            </div>
            <input
              type="range"
              min="0"
              max="20"
              step="1"
              value={controls.train_delay}
              onChange={(e) => update("train_delay", Number(e.target.value))}
            />
          </label>

          <label>
            <div className="labelRow">
              <span>Transfer demand</span>
              <strong>{controls.passenger_demand}</strong>
            </div>
            <input
              type="range"
              min="20"
              max="300"
              step="5"
              value={controls.passenger_demand}
              onChange={(e) => update("passenger_demand", Number(e.target.value))}
            />
          </label>

          <label>
            <div className="labelRow">
              <span>Fleet size</span>
              <strong>{controls.fleet_size} buses</strong>
            </div>
            <input
              type="range"
              min="1"
              max="10"
              step="1"
              value={controls.fleet_size}
              onChange={(e) => update("fleet_size", Number(e.target.value))}
            />
          </label>

          <button className="runButton" disabled={loading}>
            {loading ? "Running model…" : "Run simulation"}
          </button>

          {error && <p className="error">{error}</p>}
        </form>

        <section className="networkPanel">
          <div className="panelHeading">
            <div>
              <p className="eyebrow">LIVE NETWORK</p>
              <h2>Intermodal corridor</h2>
            </div>
            <span className="modePill">CONTROL ACTIVE</span>
          </div>

          <div className="network">
            <div className="railLine" />
            <div className="train">TRAIN</div>
            <div className="stationNode major">
              <span>HUB</span>
              <strong>Central</strong>
            </div>
            <div className="roadLine" />
            <div className="stationNode stop stopA"><span>01</span><strong>King</strong></div>
            <div className="stationNode stop stopB"><span>02</span><strong>Market</strong></div>
            <div className="stationNode stop stopC"><span>03</span><strong>University</strong></div>
            <div className="bus busOne">B1</div>
            <div className="bus busTwo">B2</div>
            <div className="yard">YARD</div>
          </div>

          <div className="metricsGrid">
            <Metric
              label="Avg wait"
              value={smart ? `${smart.average_wait.toFixed(1)} min` : "—"}
              accent
            />
            <Metric label="Max queue" value={smart ? smart.max_queue : "—"} />
            <Metric label="Pre-staged" value={smart ? smart.buses_staged : "—"} />
            <Metric label="Hold commands" value={smart ? smart.holds_issued : "—"} />
          </div>
        </section>
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
          <div className="comparisonCard">
            <span>Baseline average wait</span>
            <strong>{baseline ? `${baseline.average_wait.toFixed(1)} min` : "—"}</strong>
          </div>
          <div className="comparisonCard active">
            <span>TransitOps average wait</span>
            <strong>{smart ? `${smart.average_wait.toFixed(1)} min` : "—"}</strong>
          </div>
          <div className="comparisonCard">
            <span>Queue reduction</span>
            <strong>{data ? data.impact.max_queue_reduction : "—"}</strong>
          </div>
          <div className="comparisonCard">
            <span>Passengers recovered</span>
            <strong>{data ? data.impact.fewer_left_waiting : "—"}</strong>
          </div>
        </div>
      </section>

      <section className="eventPanel">
        <div className="panelHeading">
          <div>
            <p className="eyebrow">DECISION LOG</p>
            <h2>Controller actions</h2>
          </div>
          <span>{data ? `${data.smart.events.length} events` : "Awaiting run"}</span>
        </div>
        {data ? (
          <EventFeed events={data.smart.events} />
        ) : (
          <div className="emptyState">
            Run a scenario to see dispatch, pre-staging, train arrivals and hold decisions.
          </div>
        )}
      </section>

      <footer>
        <span>TransitOps prototype</span>
        <span>Python DES · FastAPI · Next.js · Vercel</span>
      </footer>
    </main>
  );
}
