"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { deltaMetres, lerpPath, offsetLonLat, tangentAt } from "../../lib/motion/path.ts";
import { ROAD_STEP } from "../../lib/motion/road.ts";
import { corridor } from "../../lib/motion/scene.ts";
import { CAR_LENGTH } from "../../lib/motion/traffic.ts";
import { UW_BUS_LENGTH, UW_ROAD, UwTraffic } from "../../lib/motion/uwTraffic.ts";
import styles from "./traffic.module.css";

const uw = corridor.segment("UW", "WATERLOO_SQUARE");
// Align the diagram with Phillip. Anchor the exit and stop line to the
// outgoing GTFS corridor shape, then extend the through lane north.
const corridorOffset = uw.signal.stop_line_m - UW_ROAD.stopLine;
const mergeDistance = UW_ROAD.busMerge + corridorOffset;
const [east, north] = tangentAt(uw.path, mergeDistance + 30);
const origin = offsetLonLat(lerpPath(uw.path, mergeDistance), -east * UW_ROAD.busMerge, -north * UW_ROAD.busMerge);
const ROAD_X = 266;
const TOP = 40;
const y = (s: number) => TOP + s;
const busMergeY = y(UW_ROAD.busMerge);
const accessY = y(UW_ROAD.accessMerge);
const stopY = y(UW_ROAD.stopLine);
const dwellY = y(UW_ROAD.dwell);
const junctionY = stopY + 8;

// Station approach is projected from the route. The access approach, dwell,
// and turning clearances are illustrative scenario geometry.
const stationPath = uw.path.filter((point) => point[2] <= mergeDistance).map(([lon, lat]) => {
  const [dx, dy] = deltaMetres(origin, [lon, lat]);
  return [ROAD_X - (dx * north - dy * east), TOP + dx * east + dy * north];
});
const [stationX, stationY] = stationPath[0];
const approachPath = `M${stationPath.map(([x, y]) => `${x},${y}`).join(" L")} L${ROAD_X},${busMergeY}`;
const accessPath = `M391,${accessY - 25} L307,${accessY - 25} Q${ROAD_X},${accessY - 25} ${ROAD_X},${accessY}`;

const busLabels = {
  approaching: "Approaching Phillip St",
  waiting: "Yielding to cars",
  driving: "On Phillip St",
  dwelling: "Stopped in the travel lane",
  departed: "Cleared the junction",
};

function Vehicle({ x, y, bus, angle = 0 }: { x: number; y: number; bus: boolean; angle?: number }) {
  const length = (bus ? UW_BUS_LENGTH : CAR_LENGTH) * 1.2;
  const width = bus ? 8 : 5;
  return (
    <g className={styles.vehicle} style={{ transform: `translate(${x}px, ${y}px) rotate(${angle}deg)` }}>
      <rect x={-width / 2} y={-length / 2} width={width} height={length} rx={1.7} fill={bus ? "#75e0bd" : "#add8f6"} />
      <path d={`M${-width / 2 + 1},${length / 2 - 1.5} h${width - 2}`} stroke={bus ? "#123633" : "#324966"} />
    </g>
  );
}

export default function UwTrafficPage() {
  const engine = useRef<UwTraffic | null>(null);
  const [frame, setFrame] = useState(() => new UwTraffic().frame());
  const [demand, setDemand] = useState(2.5);
  const demandRef = useRef(demand);
  const [running, setRunning] = useState(true);
  if (!engine.current) engine.current = new UwTraffic();

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setFrame(engine.current!.step(demandRef.current)), 250);
    return () => window.clearInterval(timer);
  }, [running]);

  const busProgress = frame.busApproach;
  const access = frame.accessCar;
  return (
    <main className={styles.page}>
      <Link href="/" className={styles.back}>← Waterloo corridor</Link>
      <header className={styles.header}>
        <h1>UW Station road traffic</h1>
        <p>Cars and a fixture bus share a modeled southbound lane on Phillip St.</p>
      </header>
      <div className={styles.layout}>
        <section className={styles.map} aria-label="Road simulation">
          <svg viewBox="70 0 350 530" role="img" aria-label="UW Station approach, Phillip Street and University Avenue">
            <path d={`M88,${junctionY} H412`} className={styles.crossStreet} />
            <path d={`M${ROAD_X - 7},20 V524`} className={styles.road} />
            <path d={`M${ROAD_X - 7},20 V524`} className={styles.centreline} />
            <path d={approachPath} className={styles.approach} />
            <path d={accessPath} className={styles.approach} />
            <circle cx={stationX} cy={stationY} r={7} fill="#75e0bd" />
            <text x={stationX} y={stationY - 18} textAnchor="middle">UW Station</text>
            <text x={ROAD_X + 22} y={27}>Phillip St ↓</text>
            <text x={313} y={accessY - 38}>Modeled access</text>
            <text x={98} y={junctionY + 24}>University Ave</text>
            <path d={`M${ROAD_X + 12},${dwellY - 10} v20 h32`} className={styles.marker} />
            <text x={ROAD_X + 49} y={dwellY + 14}>In-lane stop</text>
            <path d={`M${ROAD_X - 6},${stopY} h14`} stroke="white" strokeWidth={1.5} />
            <circle cx={ROAD_X + 24} cy={stopY - 7} r={5} fill={frame.red ? "#ff888a" : "#75e0bd"} />
            {frame.vehicles.map((vehicle) => (
              <Vehicle key={vehicle.id} x={ROAD_X} y={y(vehicle.s)} bus={vehicle.kind === "bus"} />
            ))}
            {busProgress !== null && (
              <Vehicle
                key="fixture-bus"
                x={stationX + (ROAD_X - 8 - stationX) * busProgress}
                y={stationY + (busMergeY - 8 - stationY) * busProgress}
                bus
                angle={-66}
              />
            )}
            {access && <Vehicle key={access.id} x={391 - 109 * access.progress} y={accessY - 25} bus={false} angle={90} />}
          </svg>
          <div className={styles.legend}><span>■ Fixture bus</span><span>■ Private car</span></div>
        </section>
        <section className={styles.controls} aria-label="Traffic controls and results">
          <div className={styles.toolbar}>
            <strong>Simulation · <output aria-label="Simulated time">{frame.time.toFixed(1)} s</output></strong>
            <div>
              <button type="button" onClick={() => setRunning(!running)}>{running ? "Pause" : "Resume"}</button>
              <button type="button" onClick={() => {
                engine.current = new UwTraffic();
                setFrame(engine.current.frame());
              }}>Restart</button>
            </div>
          </div>
          <label className={styles.demand}>
            <span>Road traffic <strong>{demand.toFixed(2)}×</strong></span>
            <input type="range" min={0.55} max={2.5} step={0.05} value={demand} onChange={(event) => {
              demandRef.current = Number(event.target.value);
              setDemand(demandRef.current);
            }} />
            <span className={styles.scale}><span>Light</span><span>Heavy</span></span>
          </label>
          <p className={styles.hint}>When the bus is yielding, lower road traffic to open a gap. After it pulls out, watch cars queue at the in-lane stop.</p>
          <dl className={styles.results}>
            <div><dt>Fixture bus</dt><dd data-testid="bus-phase">{busLabels[frame.busPhase]}</dd></div>
            <div><dt>Bus wait for a gap</dt><dd>{frame.waits.bus.toFixed(1)} s</dd></div>
            <div><dt>Cars queued behind bus</dt><dd data-testid="bus-queue">{frame.carsBehindBus}</dd></div>
            <div><dt>Time with cars queued behind bus</dt><dd>{frame.waits.behindBus.toFixed(1)} s</dd></div>
            <div><dt>Phillip / University signal</dt><dd data-testid="signal">{frame.red ? "Red" : "Green"} · {frame.carsAtRed} cars stopped</dd></div>
            <div><dt>Access lane car</dt><dd>{access ? access.progress === 1 ? "Yielding" : "Approaching" : "Clear"}</dd></div>
          </dl>
        </section>
      </div>
    </main>
  );
}
