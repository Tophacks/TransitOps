"use client";

// Decision Inspector: why each ION train did what it did. Every rail decision
// the simulator recorded (vehicle agents, station control, dispatch) with the
// options it weighed, why the others were rejected, what it chose and what
// happened next. Picking one seeks the playback to that moment, so the map
// shows the same movement the decision explains.

import { useEffect, useMemo, useState } from "react";

import type { RailDecision, RailMetrics } from "../../lib/motion/ion.ts";

type Props = {
  decisions: RailDecision[];
  /** playback time, seconds */
  now: number;
  /** the train selected on the map, if any */
  vehicle: string | null;
  onPick: (decision: RailDecision) => void;
  metrics?: { adaptive: RailMetrics; baseline: RailMetrics } | null;
  hold?: { terminal: string; platform: string; minute: number; minutes: number } | null;
};

const LEVEL = { vehicle: "Vehicle agent", station: "Station control", dispatch: "Dispatch" } as const;
const PLACE: Record<string, string> = { fairway: "Fairway", conestoga: "Conestoga", uw: "UW Station", central: "Central bus stop", line: "ION line", yard: "Dutton yard" };

export function clock(t: number) {
  const s = Math.round(t);
  const sign = s < 0 ? "−" : "";
  const a = Math.abs(s);
  return `${sign}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`;
}

/** Routine: the preferred option was free and nothing was rejected. */
export function notable(d: RailDecision) {
  return d.level !== "vehicle" || d.options.some((o) => !o.feasible) || d.trigger.startsWith("Held") || Boolean(d.error);
}

export default function DecisionInspector({ decisions, now, vehicle, onPick, metrics, hold }: Props) {
  const [open, setOpen] = useState(true);
  // On a phone the inspector starts folded so the map stays visible.
  useEffect(() => {
    if (window.innerWidth < 640) setOpen(false);
  }, []);
  const [all, setAll] = useState(false);
  const [pinned, setPinned] = useState<string | null>(null);
  const listed = useMemo(() => decisions
    .filter((d) => d.t <= now + 0.5 && d.t >= 0)
    .filter((d) => (vehicle ? d.vehicle_id === vehicle || d.options.some((o) => o.option.includes(vehicle)) : all || notable(d)))
    .slice(-6)
    .reverse(), [decisions, now, vehicle, all]);
  const focus = decisions.find((d) => d.id === pinned) ?? listed[0] ?? null;
  const index = focus ? decisions.indexOf(focus) : -1;
  const step = (dir: 1 | -1) => {
    for (let i = index + dir; i >= 0 && i < decisions.length; i += dir) {
      const d = decisions[i];
      if (d.t >= 0 && (vehicle ? d.vehicle_id === vehicle : all || notable(d))) {
        setPinned(d.id);
        onPick(d);
        return;
      }
    }
  };

  return (
    <aside className={`decisionInspector${open ? "" : " collapsed"}`} aria-label="Decision Inspector">
      <header>
        <button type="button" className="inspectorToggle" aria-expanded={open} onClick={() => setOpen(!open)}>
          <strong>Decision Inspector</strong>
          <span>{vehicle ? `${vehicle} only` : `ION, yard, UW Station and bus connections · ${decisions.filter((d) => d.t >= 0 && notable(d)).length} decisions`}</span>
        </button>
      </header>
      {open && (
        <>
          {hold && (
            <p className="decisionScenario">
              Scenario: {hold.terminal === "fairway" ? "Fairway" : "Conestoga"} platform {hold.platform} held {hold.minutes} min
              from {String(hold.minute).padStart(2, "0")}:00 (door fault, modeled).
            </p>
          )}
          {focus ? (
            <article className="decisionCard" data-decision={focus.id}>
              <div className="decisionMeta">
                <span className={`decisionLevel level-${focus.level}`}>{LEVEL[focus.level]}</span>
                <span>{clock(focus.t)} · {PLACE[focus.terminal] ?? focus.terminal}</span>
              </div>
              <strong>{focus.vehicle_id}{focus.trip_id ? ` · trip ${focus.trip_id}` : ""}</strong>
              <p><em>What changed</em>{focus.trigger}</p>
              {focus.saw && <p><em>What it saw</em>{focus.saw}</p>}
              {focus.error && <p className="decisionError"><em>What went wrong</em>{focus.error}</p>}
              <ul className="decisionOptions">
                {focus.options.map((o) => (
                  <li key={o.option} className={o.option === focus.chosen || focus.chosen.startsWith(o.option) ? "chosen" : o.feasible ? "feasible" : "rejected"}>
                    <b aria-label={o.feasible ? "feasible" : "rejected"}>{o.feasible ? "✓" : "✗"}</b>
                    <span>{o.option}</span>
                    {o.reason && <small>{o.reason}</small>}
                  </li>
                ))}
              </ul>
              <p><em>Chose</em>{focus.chosen}</p>
              {focus.control && <p><em>What Control allowed</em>{focus.control}</p>}
              {focus.outcome && <p><em>What happened next</em>{focus.outcome}</p>}
              {focus.effect && <p><em>Effect on service</em>{focus.effect}</p>}
              <div className="decisionNav">
                <button type="button" onClick={() => step(-1)} aria-label="Previous decision">◀</button>
                <button type="button" onClick={() => { setPinned(focus.id); onPick(focus); }}>Show on map</button>
                <button type="button" onClick={() => step(1)} aria-label="Next decision">▶</button>
              </div>
            </article>
          ) : (
            <p className="decisionEmpty">No decisions yet at {clock(now)}.</p>
          )}
          {listed.length > 1 && (
            <ol className="decisionList">
              {listed.map((d) => (
                <li key={d.id}>
                  <button type="button" aria-pressed={focus?.id === d.id} onClick={() => { setPinned(d.id); onPick(d); }}>
                    <span>{clock(d.t)}</span> {d.vehicle_id} · {d.chosen}
                  </button>
                </li>
              ))}
            </ol>
          )}
          <label className="decisionFilter">
            <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Include routine platform grants
          </label>
          {metrics && (
            <table className="decisionMetrics">
              <thead><tr><th /><th>Holds only</th><th>Dispatch</th></tr></thead>
              <tbody>
                <tr><td>Late departures (&gt;1 min)</td><td>{metrics.baseline.late_departures}</td><td>{metrics.adaptive.late_departures}</td></tr>
                <tr><td>Avg departure delay</td><td>{Math.round(metrics.baseline.avg_departure_delay_s)} s</td><td>{Math.round(metrics.adaptive.avg_departure_delay_s)} s</td></tr>
                <tr><td>Queued at signals</td><td>{Math.round(metrics.baseline.platform_queue_s)} s</td><td>{Math.round(metrics.adaptive.platform_queue_s)} s</td></tr>
                <tr><td>Trips reassigned</td><td>{metrics.baseline.trip_swaps}</td><td>{metrics.adaptive.trip_swaps}</td></tr>
              </tbody>
            </table>
          )}
        </>
      )}
    </aside>
  );
}
