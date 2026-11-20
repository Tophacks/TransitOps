"use client";

import { useMemo, useState } from "react";

import type { RailDecision } from "../../lib/motion/ion.ts";
import { clockAt, isReadySpare, stateAt, type FleetData, type FleetVehicle, type LineVehicle } from "../../lib/motion/lines.ts";
import type { Command } from "../../lib/types";

type Props = {
  fleet: FleetData | null;
  lineVehicles: LineVehicle[];
  decisions: RailDecision[];
  now: number;
  commands: Command[];
  onCommand: (command: Command) => void;
  onClear: () => void;
  selected: string | null;
  onSelect: (id: string | null) => void;
  busy: boolean;
};

const GARAGE_NAMES: Record<string, string> = {
  strasburg: "Strasburg Road", northfield: "Northfield Drive", "conestoga-blvd": "Conestoga Boulevard",
};

/** The regional fleet: pick a modelled bus, see its status, and give dispatch an order.
 * Orders are inputs to the run (seed + orders replay exactly); the simulator checks each
 * against the bus's availability and route access and says why it accepted or refused. */
export default function FleetPanel({ fleet, lineVehicles, decisions, now, commands, onCommand, onClear, selected, onSelect, busy }: Props) {
  const [filter, setFilter] = useState<"active" | "spares" | "all">("active");
  const [target, setTarget] = useState("corridor");
  const minute = Math.floor(now / 60);
  const horizon = Number(fleet?.summary.modelled.spare_horizon_s ?? 10800);

  const blocks = useMemo(() => [...new Set(lineVehicles.filter((v) => !v.broken).map((v) => v.block))].sort(), [lineVehicles]);
  const list = useMemo(() => {
    if (!fleet) return [];
    return fleet.vehicles.filter((v) => {
      const [state] = stateAt(v, now);
      if (filter === "all") return true;
      if (filter === "spares") return isReadySpare(v, now, horizon);
      return v.simulated && v.kind !== "ion";
    });
  }, [fleet, filter, now, horizon]);
  const orders = decisions.filter((d) => d.trigger.startsWith("Dispatcher order"));
  const vehicle: FleetVehicle | undefined = fleet?.vehicles.find((v) => v.id === selected);

  if (!fleet) return null;
  const counts = fleet.summary.counts_start;
  const [state, detail] = vehicle ? stateAt(vehicle, now) : ["", ""];
  const onLine = vehicle ? lineVehicles.find((v) => v.vehicle_id === vehicle.id && !v.broken) : undefined;
  const isSpare = vehicle ? isReadySpare(vehicle, now, horizon) : false;
  const inService = state === "in service" || state === "corridor run";

  function order(action: Command["action"], to: string) {
    if (!vehicle) return;
    onCommand({ minute, action, vehicle: vehicle.id, target: to });
  }

  return (
    <section className="fleetPanel" aria-label="Fleet">
      <div className="panelHeading">
        <div><p className="eyebrow">REGIONAL FLEET</p><h2>Fleet</h2></div>
        <span>{fleet.summary.conventional} buses · {fleet.summary.mobilityplus} MobilityPLUS · {fleet.summary.ion} ION</span>
      </div>
      <div className="fleetGarages">
        {Object.entries(counts).map(([gid, c]) => (
          <div key={gid} className="fleetGarage">
            <strong>{GARAGE_NAMES[gid] ?? gid}</strong>
            <span>{c.allocation} buses (modelled share)</span>
            <span>{fleet.vehicles.filter((v) => v.garage === gid && isReadySpare(v, now, horizon)).length} ready spares at {clockAt(now)}</span>
          </div>
        ))}
      </div>
      <div className="fleetMiddle">
      <div className="fleetFilter" role="group" aria-label="Show">
        {(["active", "spares", "all"] as const).map((f) => (
          <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>
            {f === "active" ? "In this run" : f === "spares" ? "Ready spares" : "All"}
          </button>
        ))}
      </div>
      <label className="fleetPick">
        <span>Vehicle</span>
        <select value={selected ?? ""} onChange={(e) => onSelect(e.target.value || null)} aria-label="Vehicle">
          <option value="">Select a bus…</option>
          {list.map((v) => (
            <option key={v.id} value={v.id}>{v.id} · {stateAt(v, now)[0]}{v.garage ? ` · ${GARAGE_NAMES[v.garage] ?? v.garage}` : ""}</option>
          ))}
        </select>
      </label>
      {vehicle && (
        <div className="fleetVehicle" data-vehicle={vehicle.id}>
          <h3>{vehicle.id}</h3>
          <dl>
            <dt>Status at {clockAt(now)}</dt><dd>{state || "—"}{detail ? ` · ${detail}` : ""}</dd>
            <dt>Garage</dt><dd>{vehicle.garage ? GARAGE_NAMES[vehicle.garage] ?? vehicle.garage : vehicle.kind === "mobilityplus" ? "MobilityPLUS (separate)" : "—"}</dd>
            <dt>Assignment</dt><dd>{vehicle.block ? `block ${vehicle.block}` : "—"}{vehicle.role ? ` · role in this run: ${vehicle.role}` : ""}</dd>
            <dt>Driver</dt><dd>{vehicle.driver ?? "—"}{onLine ? ` · ${onLine.driver}` : ""}</dd>
            <dt>Simulated</dt><dd>{vehicle.simulated ? "yes: drives in this run" : "no: status from its block's schedule"}</dd>
          </dl>
          <div className="fleetActions">
            {isSpare && (
              <>
                <label>
                  <span>Send to</span>
                  <select value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Send to">
                    <option value="corridor">the corridor run (Central)</option>
                    {blocks.map((b) => <option key={b} value={b}>Route 7 block {b}</option>)}
                  </select>
                </label>
                <button type="button" disabled={busy} onClick={() => order("dispatch_spare", target)}>Dispatch spare at {clockAt(minute * 60)}</button>
              </>
            )}
            {onLine && (
              <button type="button" disabled={busy} onClick={() => order("reassign", "corridor")}>Reassign to the corridor at {clockAt(minute * 60)}</button>
            )}
            {inService && (
              <button type="button" disabled={busy} onClick={() => order("breakdown", "corridor")}>Report a breakdown at {clockAt(minute * 60)}</button>
            )}
            {!isSpare && !onLine && !inService && <p className="inspectorCopy">No order applies to a bus that is {state || "not in this run"}. Any order can still be tried below.</p>}
            {!isSpare && (
              <button type="button" className="secondary" disabled={busy} onClick={() => order("dispatch_spare", target)}>Try: dispatch as a spare</button>
            )}
          </div>
        </div>
      )}
      </div>
      <div className="fleetOrders">
        <div className="labelRow"><span>Orders in this run</span>{commands.length > 0 && <button type="button" className="linkButton" onClick={onClear}>Clear orders</button>}</div>
        {orders.length === 0 && <p className="inspectorCopy">None yet. Orders apply at the playback minute and the run replays with the same seed.</p>}
        {orders.map((d) => (
          <article key={d.id} className={`fleetOrder ${d.chosen}`}>
            <strong>{clockAt(d.t)} · {d.vehicle_id} · {d.chosen}</strong>
            <span>{d.trigger.replace(/^Dispatcher order at \d\d:\d\d: /, "")}</span>
            {d.control && <span>{d.chosen === "refused" ? "Why: " : ""}{d.control}</span>}
            {d.effect && <span>Coverage: {d.effect}</span>}
            {d.outcome && <span>{d.outcome}</span>}
          </article>
        ))}
      </div>
    </section>
  );
}
