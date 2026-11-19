"use client";

import { useMemo, useState, type FormEvent } from "react";

import RegionalMap from "./components/RegionalMap";
import GeospatialMap from "./components/GeospatialMap";
import { useSumoStream } from "../lib/useSumoStream";
import { PLAYBACK_SPEEDS, usePlayback } from "../lib/usePlayback";
import { busPoses, type BusPose } from "../lib/motion/playback.ts";
import { corridor } from "../lib/motion/scene.ts";
import { IonTimetable, type TrainPose } from "../lib/motion/ion.ts";
import { TERMINALS } from "../lib/motion/railScene.ts";
import type {
  Controls,
  Network,
  Selection,
  SimulationEvent,
  SimulationResponse,
  Snapshot,
} from "../lib/types";

const DEFAULTS: Controls = {
  traffic_multiplier: 1.15,
  train_delay: 4,
  passenger_demand: 110,
  fleet_size: 3,
  demand_multiplier: 1,
  surge_passengers: 0,
  surge_minute: null,
  breakdown_minute: null,
  emergency_dispatch_minute: null,
  seed: 17,
  platform_hold_terminal: null,
  platform_hold_platform: "2",
  platform_hold_minute: 20,
  platform_hold_minutes: 12,
};

const PRESETS: { id: string; title: string; description: string; controls: Controls }[] = [
  { id: "normal", title: "Weekday baseline", description: "Regular corridor demand and traffic", controls: DEFAULTS },
  { id: "rush", title: "Rush-hour pressure", description: "Heavy traffic and 1.8× walk-up demand", controls: { ...DEFAULTS, traffic_multiplier: 2.1, demand_multiplier: 1.8, fleet_size: 5 } },
  { id: "event", title: "Campus event surge", description: "An extra 180 passengers arrive at UW at minute 20", controls: { ...DEFAULTS, surge_minute: 20, surge_passengers: 180, passenger_demand: 160, fleet_size: 5 } },
  { id: "breakdown", title: "Bus breakdown", description: "The first active bus is held roadside at minute 20", controls: { ...DEFAULTS, breakdown_minute: 20, fleet_size: 4 } },
  { id: "emergency", title: "Emergency dispatch", description: "A reserve bus is requested at minute 15", controls: { ...DEFAULTS, emergency_dispatch_minute: 15, passenger_demand: 185, fleet_size: 6 } },
  { id: "platform-hold", title: "ION platform hold", description: "A train is held 12 min at Fairway platform 2 from minute 20", controls: { ...DEFAULTS, platform_hold_terminal: "fairway" } },
];

const STOP_NAMES: Record<string, string> = {
  UW: "University of Waterloo",
  WATERLOO_SQUARE: "Waterloo Public Square",
  GRAND_RIVER_HOSPITAL: "Grand River Hospital",
  CENTRAL: "Central Station",
};
const SIGNAL_NAMES: Record<string, string> = {
  I1: "University corridor",
  I2: "Uptown Waterloo",
  I3: "King / Victoria",
};
const geo = corridor.data;

// Same curb stops and signals the Python backend uses (generated geometry).
const FALLBACK_NETWORK: Network = {
  yard: {
    id: "YARD",
    name: "Corridor Staging Yard",
    lon: geo.stops.YARD.lon,
    lat: geo.stops.YARD.lat,
  },
  stops: Object.entries(STOP_NAMES).map(([id, name]) => ({ id, name, lon: geo.stops[id].lon, lat: geo.stops[id].lat })),
  intersections: Object.entries(SIGNAL_NAMES).map(([id, name]) => ({ id, name, lon: geo.signals[id].lon, lat: geo.signals[id].lat })),
  route_order: [
    "UW",
    "WATERLOO_SQUARE",
    "GRAND_RIVER_HOSPITAL",
    "CENTRAL",
  ],
  passengers_per_sprite: 6,
  corridor_name: "Waterloo Corridor Test Scenario",
};

function formatClock(seconds: number) {
  const whole = Math.floor(seconds);
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

function Inspector({
  selected,
  snapshot,
  decision,
  pose,
  trainPose,
}: {
  selected: Selection;
  snapshot: Snapshot | null;
  decision?: SimulationEvent;
  pose?: BusPose;
  trainPose?: TrainPose;
}) {
  if (!selected) {
    return (
      <div className="inspectorEmpty">
        Select a bus, ION train, station, signal, or staging yard on the map.
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
          <div><dt>Segment progress</dt><dd>{pose
            ? `${Math.round((pose.distance / pose.length) * 100)}% · ${Math.round(pose.distance)} of ${Math.round(pose.length)} m`
            : `${Math.round(live.progress * 100)}%`}</dd></div>
          {pose && <div><dt>Speed</dt><dd>{Math.round(pose.speed * 3.6)} km/h{pose.state !== "EN_ROUTE" ? ` · ${pose.state.replaceAll("_", " ").toLowerCase()}` : ""}</dd></div>}
        </dl>
        {decision && <div className="geoDecision">
          <strong>{decision.message}</strong>
          <span>{decision.reason}</span>
        </div>}
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

  if (selected.type === "train") {
    const live = snapshot?.trains?.find((train) => train.id === selected.data.id) ?? selected.data;
    const ahead = Object.entries(live.transfers_ahead);
    return (
      <div className="inspectorBody">
        <p className="eyebrow">ION TRAIN · PYTHON SIMULATOR</p>
        <div className="inspectTitle">
          <h3>{live.id}{(trainPose?.tripId ?? live.trip_id) ? ` · ${trainPose?.tripId ?? live.trip_id}` : ""}</h3>
          <span>{live.direction === "north" ? "Northbound" : "Southbound"}</span>
        </div>
        <dl>
          {/* Position follows the playback clock; delay and riders come from the minute snapshot. */}
          <div><dt>{(trainPose ? trainPose.station : live.station) ? "At" : "Next station"}</dt>
            <dd>{trainPose ? trainPose.station ?? trainPose.nextStation : live.station ?? live.next_station}</dd></div>
          {trainPose && !trainPose.station && <div><dt>Speed</dt><dd>{Math.round(trainPose.speed * 3.6)} km/h</dd></div>}
          <div><dt>Delay</dt><dd className={live.delay_min > 0 ? "badText" : "goodText"}>{live.delay_min > 0 ? `+${live.delay_min} min · ${live.hold}` : "On time"}</dd></div>
          <div><dt>Transferring to the bus</dt><dd>{ahead.length
            ? ahead.map(([stop, riders]) => `${riders} at ${stop === "UW" ? "UW" : "Central"}`).join(", ")
            : "None still aboard"}</dd></div>
          <div><dt>Along the line</dt><dd>{((trainPose?.nose ?? live.nose_m) / 1000).toFixed(2)} km from the {live.direction === "north" ? "Fairway" : "Conestoga"} end</dd></div>
        </dl>
        {decision && <div className="geoDecision">
          <strong>{decision.message}</strong>
          {decision.reason && <span>{decision.reason}</span>}
        </div>}
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
  const playback = usePlayback(30);
  const { seconds, playing, speed } = playback;
  const [selected, setSelected] = useState<Selection>(null);
  const [mapView, setMapView] = useState<"geographic" | "schematic">("geographic");
  const [liveMap, setLiveMap] = useState(true);
  const sumo = useSumoStream();

  const timeline = useMemo(() => data?.smart.timeline ?? [], [data]);
  const frame = Math.min(Math.floor(seconds / 60), Math.max(0, timeline.length - 1));
  const snapshot = timeline[frame] ?? null;
  const poses = useMemo(() => busPoses(corridor, timeline, seconds), [timeline, seconds]);
  const timetable = useMemo(() => (data?.ion ? new IonTimetable(corridor.data.ion, data.ion, TERMINALS) : null), [data]);
  const network = data?.network ?? FALLBACK_NETWORK;
  const sumoMapSnapshot = sumo.state === "idle" ? null : sumo.snapshot;
  const smart = data?.smart.metrics;
  const baseline = data?.baseline.metrics;
  const selectedBus = selected?.type === "bus" ? selected.data.id : undefined;
  const selectedTrainId = selected?.type === "train" ? selected.data.id : undefined;
  const decision = snapshot
    ? selectedBus
      ? data?.smart.events.findLast((event) =>
          event.bus_id === selectedBus && event.minute <= snapshot.minute && event.reason)
      : selectedTrainId
        ? data?.smart.events.findLast((event) =>
            event.train_id === selectedTrainId && event.minute <= snapshot.minute)
        : undefined
    : undefined;
  // ION arrivals at UW and Central on the timeline: where transfer riders join the bus queues.
  const ionMarks = useMemo(() => (data?.smart.events ?? [])
    .filter((event) => event.kind === "train" && event.train_id)
    .map((event) => ({ minute: event.minute, riders: Number(/\+(\d+)/.exec(event.message)?.[1] ?? 0), label: event.message })),
  [data]);

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
    playback.pause();
    setSelected(null);
    setLiveMap(false);
    sumo.stop();

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
      playback.load(Math.max(0, result.smart.timeline.length - 1) * 60);
      playback.play();
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
    if (playing) playback.pause();
    else playback.play();
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

      <section className="simulatorShell">
        <div className="mapViewControl">
          <h1>Waterloo transit map</h1>
          <div className="mapViewButtons" role="group" aria-label="Map view">
            <button type="button" aria-pressed={mapView === "geographic"} onClick={() => setMapView("geographic")}>Geographic map</button>
            <button type="button" aria-pressed={mapView === "schematic"} onClick={() => setMapView("schematic")}>Schematic</button>
          </div>
        </div>
        {mapView === "geographic" ? (
          <GeospatialMap
            network={network}
            snapshot={snapshot}
            decision={decision}
            selected={selected}
            onSelect={setSelected}
            live={liveMap}
            onModeChange={setLiveMap}
            sumoSnapshot={sumoMapSnapshot}
            timeline={timeline}
            clock={playback.clock}
            ionSchedule={data?.ion ?? null}
            trafficMultiplier={data?.scenario.traffic_multiplier ?? controls.traffic_multiplier}
            ionBaselineMetrics={data?.baseline.ion?.metrics ?? null}
            seconds={seconds}
            onSeek={(t) => { playback.pause(); playback.seek(Math.max(0, t)); }}
          />
        ) : (
          <RegionalMap network={network} snapshot={snapshot} poses={poses} selected={selected} onSelect={setSelected} traffic={controls.traffic_multiplier} sumoSnapshot={sumoMapSnapshot}/>
        )}

        <div className="simTopbar">
          <div className="playback">
            <button
              type="button"
              className={sumo.state === "running" ? "activeSpeed" : ""}
              onClick={sumo.state === "running" ? sumo.stop : () => {
                setLiveMap(false);
                setSelected(null);
                sumo.start();
              }}
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
            <div className="speedGroup" role="group" aria-label="Playback speed (simulated seconds per second)">
              {PLAYBACK_SPEEDS.map((value) => (
                <button
                  type="button"
                  key={value}
                  aria-pressed={speed === value}
                  className={speed === value ? "activeSpeed" : ""}
                  title={value === 1 ? "Real time" : `${value}× real time`}
                  onClick={() => playback.setSpeed(value)}
                >
                  {value}×
                </button>
              ))}
            </div>
          </div>

          <div className="topMetrics">
            <div><span>WAITING</span><strong>{snapshot?.waiting ?? "—"}</strong></div>
            <div><span>ACTIVE BUSES</span><strong>{snapshot ? poses.length : "—"}</strong></div>
            <div><span>ION TRAINS</span><strong>{snapshot ? snapshot.trains?.length ?? 0 : "—"}</strong></div>
            <div><span>AVG WAIT</span><strong>{smart ? `${smart.average_wait.toFixed(1)}m` : "—"}</strong></div>
            <div><span>TRAFFIC</span><strong>{controls.traffic_multiplier.toFixed(2)}×</strong></div>
          </div>
        </div>

        <div className="timelineControl">
          <span aria-label="Current simulation time">{formatClock(seconds)}</span>
          <div className="timelineTrack">
            <input
              aria-label="Simulation timeline"
              type="range"
              min="0"
              step="1"
              max={Math.max(0, timeline.length - 1) * 60}
              value={Math.round(seconds)}
              onChange={(event) => {
                playback.pause();
                playback.seek(Number(event.target.value));
              }}
              disabled={!data}
            />
            {timeline.length > 1 && (
              <div className="ionTicks" aria-hidden="true">
                {ionMarks.map((mark, index) => (
                  <i
                    key={`${mark.minute}-${index}`}
                    className={mark.riders >= 10 ? "wave" : ""}
                    style={{ left: `${(mark.minute / (timeline.length - 1)) * 100}%` }}
                    title={`${String(mark.minute).padStart(2, "0")}:00 · ${mark.label}`}
                  />
                ))}
              </div>
            )}
          </div>
          <span>{formatClock(Math.max(0, timeline.length - 1) * 60)}</span>
        </div>
      </section>

      <section className="scenarioPlayground" aria-label="Simulation playground">
        <div className="panelHeading"><div><p className="eyebrow">EXPERIMENT LAB</p><h2>Scenario playground</h2>
          <p>Use the same seed for a fair baseline-versus-adaptive comparison. These outcomes come from the Python corridor model, not real GRT telemetry.</p>
        </div><span>60 simulated minutes</span></div>
        <div className="scenarioPresets">{PRESETS.map((preset) => (
          <button type="button" key={preset.id} onClick={() => setControls({ ...preset.controls })}>
            <strong>{preset.title}</strong><small>{preset.description}</small>
          </button>
        ))}</div>
        {data && <div className="comparisonDeck">
          <div><span>Baseline waiting</span><strong>{baseline?.left_waiting ?? "—"}</strong></div>
          <div><span>Adaptive waiting</span><strong>{smart?.left_waiting ?? "—"}</strong></div>
          <div><span>Completed trips (baseline → adaptive)</span><strong>{baseline?.completed_trips ?? "—"} → {smart?.completed_trips ?? "—"}</strong></div>
          <div><span>Average boarding wait change</span><strong>{data.impact.average_wait_reduction.toFixed(1)} min</strong></div>
          <div><span>ION transfer wait (baseline → adaptive)</span><strong>{baseline?.average_transfer_wait.toFixed(1)} → {smart?.average_transfer_wait.toFixed(1)} min</strong></div>
        </div>}
        <div className="scenarioInstructions">Selecting a preset sets controls only. Press <strong>Apply &amp; restart</strong> to run both dispatch policies under matching conditions. Positive wait reduction means the adaptive controller improved the result.</div>
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

          <label>
            <div className="labelRow"><span>Background passenger demand</span><strong>{controls.demand_multiplier.toFixed(1)}×</strong></div>
            <input type="range" min="0.5" max="3" step="0.1" value={controls.demand_multiplier} onChange={(event) => update("demand_multiplier", Number(event.target.value))} />
          </label>
          <label>
            <div className="labelRow"><span>Event crowd surge at UW</span><strong>{controls.surge_passengers} people</strong></div>
            <input type="range" min="0" max="350" step="10" value={controls.surge_passengers} onChange={(event) => setControls((current) => ({ ...current, surge_passengers: Number(event.target.value), surge_minute: Number(event.target.value) ? (current.surge_minute ?? 20) : null }))} />
          </label>
          <label>
            <div className="labelRow"><span>Breakdown event</span><strong>{controls.breakdown_minute === null ? "Off" : "Minute " + controls.breakdown_minute}</strong></div>
            <select value={controls.breakdown_minute === null ? "off" : String(controls.breakdown_minute)} onChange={(event) => update("breakdown_minute", event.target.value === "off" ? null : Number(event.target.value))}>
              <option value="off">Disabled</option><option value="15">Minute 15</option><option value="20">Minute 20</option><option value="35">Minute 35</option>
            </select>
          </label>
          <label>
            <div className="labelRow"><span>Emergency dispatch</span><strong>{controls.emergency_dispatch_minute === null ? "Off" : "Minute " + controls.emergency_dispatch_minute}</strong></div>
            <select value={controls.emergency_dispatch_minute === null ? "off" : String(controls.emergency_dispatch_minute)} onChange={(event) => update("emergency_dispatch_minute", event.target.value === "off" ? null : Number(event.target.value))}>
              <option value="off">Disabled</option><option value="15">Minute 15</option><option value="25">Minute 25</option>
            </select>
          </label>
          <label>
            <div className="labelRow"><span>ION platform hold</span><strong>{controls.platform_hold_terminal
              ? `${controls.platform_hold_terminal === "fairway" ? "Fairway" : "Conestoga"} ${controls.platform_hold_platform} · ${controls.platform_hold_minutes} min`
              : "Off"}</strong></div>
            <select aria-label="ION platform hold terminal" value={controls.platform_hold_terminal ?? "off"}
              onChange={(event) => update("platform_hold_terminal", event.target.value === "off" ? null : event.target.value as "fairway" | "conestoga")}>
              <option value="off">No extra hold</option><option value="fairway">Fairway</option><option value="conestoga">Conestoga</option>
            </select>
          </label>
          {controls.platform_hold_terminal && (
            <div className="holdRow">
              <label>
                <span>Platform</span>
                <select aria-label="ION platform hold platform" value={controls.platform_hold_platform} onChange={(event) => update("platform_hold_platform", event.target.value as "1" | "2")}>
                  <option value="1">1</option><option value="2">2</option>
                </select>
              </label>
              <label>
                <span>From minute</span>
                <input aria-label="ION platform hold minute" type="number" min="0" max="59" value={controls.platform_hold_minute}
                  onChange={(event) => update("platform_hold_minute", Number(event.target.value))} />
              </label>
              <label>
                <span>Minutes</span>
                <input aria-label="ION platform hold minutes" type="number" min="1" max="40" value={controls.platform_hold_minutes}
                  onChange={(event) => update("platform_hold_minutes", Number(event.target.value))} />
              </label>
            </div>
          )}
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
          <Inspector
            selected={selected}
            snapshot={snapshot}
            decision={decision}
            pose={selected?.type === "bus" ? poses.find((pose) => pose.id === selected.data.id) : undefined}
            trainPose={selected?.type === "train" ? timetable?.trains(seconds).find((train) => train.id === selected.data.id) : undefined}
          />
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
            <div><span>ION transfers to the bus</span><strong>{smart ? `${smart.ion_transfers_boarded} of ${smart.ion_transfers}` : "—"}</strong></div>
            <div><span>ION transfer wait</span><strong>{smart ? `${smart.average_transfer_wait.toFixed(1)} min` : "—"}</strong></div>
            <div><span>Latest ION train</span><strong>{smart ? `+${smart.ion_max_delay} min` : "—"}</strong></div>
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
