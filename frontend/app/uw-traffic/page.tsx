"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { deltaMetres, lerpPath, type LonLat } from "../../lib/motion/path.ts";
import { TRAFFIC_SCENARIOS, UwTraffic, type TrafficScenario } from "../../lib/motion/uwTraffic.ts";
import { UW_BERTH_FRONT, type Lane } from "../../lib/motion/uwLanes.ts";
import styles from "./traffic.module.css";

type Frame = ReturnType<UwTraffic["frame"]>;

function TrafficMap({ lanes, scenario, frame }: { lanes: readonly Lane[]; scenario: TrafficScenario; frame: Frame }) {
  const origin: LonLat = [lanes[0].path[0][0], lanes[0].path[0][1]];
  const project = (p: LonLat) => {
    const [x, y] = deltaMetres(origin, p);
    return [x, -y] as const;
  };
  const points = lanes.flatMap(({ path }) => path.map(([lon, lat]) => project([lon, lat])));
  const xs = points.map(([x]) => x);
  const ys = points.map(([, y]) => y);
  const minX = Math.min(...xs) - 24;
  const minY = Math.min(...ys) - 33;
  const width = Math.max(...xs) + 36 - minX;
  const height = Math.max(...ys) + (scenario === "station" ? 30 : 52) - minY;

  const annotations = scenario === "station" ? [
    { laneId: "phillip-in", distance: 32, text: "Phillip St ↓", dx: 7, dy: -6 },
    { laneId: "ring-entry", distance: 0, text: "Ring Rd entrance", dx: -13, dy: 16 },
    { laneId: "berth-1223", distance: UW_BERTH_FRONT - 6.1, text: "Stop 1223", dx: 0, dy: 16 },
    { laneId: "phillip-right", distance: 1, text: "Bus exit", dx: -46, dy: -8 },
    { laneId: "phillip-out", distance: lanes.find(({ id }) => id === "phillip-out")!.signalAt!, text: "University Ave", dx: -59, dy: 17 },
  ] : [
    { laneId: "fixture-in", distance: 35, text: "Traffic →", dx: -12, dy: -9 },
    { laneId: scenario === "shared" ? "fixture-out" : "fixture-bay", distance: 30,
      text: scenario === "shared" ? "Shared stop" : "Separate bay", dx: -18, dy: 19 },
  ];

  return (
    <svg viewBox={`${minX} ${minY} ${width} ${height}`} role="img" aria-label={`${TRAFFIC_SCENARIOS[scenario]} lane diagram`}>
      {lanes.map((lane) => {
        const line = lane.path.map(([lon, lat]) => project([lon, lat]).join(",")).join(" ");
        const locked = frame.merges.some(({ from }) => from === lane.id);
        return <polyline key={lane.id} points={line} data-lane={lane.id}
          className={locked ? styles.merge : lane.access === "bus" ? styles.busLane : styles.publicLane}>
          <title>{lane.label}</title>
        </polyline>;
      })}
      {annotations.map(({ laneId, distance, text, dx, dy }) => {
        const lane = lanes.find(({ id }) => id === laneId)!;
        const [x, y] = project(lerpPath(lane.path, distance));
        return <text key={text} x={x + dx} y={y + dy}>{text}</text>;
      })}
      {lanes.flatMap((lane) => {
        if (lane.signalAt === undefined) return [];
        const [x, y] = project(lerpPath(lane.path, lane.signalAt));
        return <circle key={lane.id} cx={x} cy={y} r={3} fill={frame.red ? "#ff888a" : "#75e0bd"} />;
      })}
      {frame.vehicles.map(({ id, kind, nose, tail }) => {
        const [x1, y1] = project(nose);
        const [x2, y2] = project(tail);
        return <line key={id} data-vehicle={id} x1={x1} y1={y1} x2={x2} y2={y2}
          stroke={kind === "bus" ? "#75e0bd" : "#add8f6"}
          strokeWidth={kind === "bus" ? 4.4 : 2.8} strokeLinecap="round"><title>{id}</title></line>;
      })}
    </svg>
  );
}

export default function UwTrafficPage() {
  const [scenario, setScenario] = useState<TrafficScenario>("station");
  const [seed, setSeed] = useState("42");
  const [demand, setDemand] = useState(2.5);
  const [running, setRunning] = useState(true);
  const [frame, setFrame] = useState(() => new UwTraffic().frame());
  const engine = useRef<UwTraffic | null>(null);
  const demandRef = useRef(demand);
  if (!engine.current) engine.current = new UwTraffic();

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setFrame(engine.current!.step(demandRef.current)), 250);
    return () => window.clearInterval(timer);
  }, [running]);

  function restart(nextScenario = scenario) {
    engine.current = new UwTraffic(nextScenario, Number(seed));
    setFrame(engine.current.frame());
  }

  const busStatus = !frame.bus ? "Cleared the road"
    : frame.waiting ? "Yielding to cars"
    : frame.dwelling ? "Dwelling"
    : frame.merges.length ? "Crossing merge"
    : engine.current.road.lane(frame.bus.laneId).label;

  return (
    <main className={styles.page}>
      <Link href="/" className={styles.back}>← Waterloo corridor</Link>
      <header className={styles.header}>
        <h1>UW Station road traffic</h1>
        <p>{scenario === "station"
          ? "A fixture bus enters at Ring Road, dwells at stop 1223, and turns right onto Phillip Street."
          : "Synthetic road layout for testing how cars respond to a dwelling bus."}</p>
      </header>
      <div className={styles.layout}>
        <section className={styles.map} aria-label="Road simulation">
          <TrafficMap lanes={engine.current.lanes} scenario={scenario} frame={frame} />
          <div className={styles.legend}><span>■ Fixture bus</span><span>■ Private car</span><span>■ Bus-only lane</span></div>
          {scenario === "station" && (
            <a className={styles.back} href="https://www.openstreetmap.org/node/298025663">© OpenStreetMap contributors</a>
          )}
        </section>
        <section className={styles.controls} aria-label="Traffic controls and results">
          <div className={styles.toolbar}>
            <strong>Simulation · <output aria-label="Simulated time">{frame.time.toFixed(1)} s</output></strong>
            <div>
              <button type="button" onClick={() => setRunning(!running)}>{running ? "Pause" : "Resume"}</button>
              <button type="button" onClick={() => restart()}>Restart</button>
            </div>
          </div>
          <div className={styles.options}>
            <label>Scenario
              <select value={scenario} onChange={(event) => {
                const next = event.target.value as TrafficScenario;
                setScenario(next);
                restart(next);
              }}>
                {Object.entries(TRAFFIC_SCENARIOS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select>
            </label>
            <label>Seed
              <input type="number" value={seed} step={1} min={0} max={4294967295}
                onChange={(event) => setSeed(event.target.value)} aria-describedby="seed-help" />
            </label>
          </div>
          <p className={styles.hint} id="seed-help">Restart to apply a new seed or compare traffic levels from the same starting state.</p>
          <label className={styles.demand}>
            <span>Road traffic <strong>{demand.toFixed(2)}×</strong></span>
            <input type="range" min={0.55} max={2.5} step={0.05} value={demand} onChange={(event) => {
              demandRef.current = Number(event.target.value);
              setDemand(demandRef.current);
            }} />
            <span className={styles.scale}><span>Light</span><span>Heavy</span></span>
          </label>
          <dl className={styles.results}>
            <div><dt>Fixture bus</dt><dd data-testid="bus-phase">{busStatus}</dd></div>
            <div><dt>Bus wait for a gap</dt><dd>{frame.busWait.toFixed(1)} s</dd></div>
            <div><dt>{frame.waiting ? "Blocking vehicles" : "Last blocking vehicles"}</dt><dd data-testid="merge-blockers">{frame.lastBlockers.join(", ") || "—"}</dd></div>
            <div><dt>Merge reservation</dt><dd>{frame.merges.length ? "Bus rear still crossing" : "Clear"}</dd></div>
            {scenario !== "station" && <div><dt>Cars queued behind bus</dt><dd data-testid="bus-queue">{frame.carsBehindBus}</dd></div>}
            {scenario === "station" && <div><dt>Phillip / University signal</dt><dd data-testid="signal">{frame.red ? "Red" : "Green"}</dd></div>}
          </dl>
        </section>
      </div>
    </main>
  );
}
