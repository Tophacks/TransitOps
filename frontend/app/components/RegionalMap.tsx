"use client";

import type {
  BusState,
  IntersectionState,
  Network,
  Selection,
  Snapshot,
  StopState,
  LiveSumoSnapshot,
  LiveSumoVehicle,
} from "../../lib/types";

type Props = {
  network: Network;
  snapshot: Snapshot | null;
  selected: Selection;
  onSelect: (selection: Selection) => void;
  traffic: number;
  sumoSnapshot?: LiveSumoSnapshot | null;
};

const BOUNDS = {
  minLon: -80.548,
  maxLon: -80.492,
  minLat: 43.448,
  maxLat: 43.478,
};

function project(lon: number, lat: number) {
  const x = ((lon - BOUNDS.minLon) / (BOUNDS.maxLon - BOUNDS.minLon)) * 100;
  const y = 100 - ((lat - BOUNDS.minLat) / (BOUNDS.maxLat - BOUNDS.minLat)) * 100;
  return { x, y };
}

export default function RegionalMap({
  network,
  snapshot,
  selected,
  onSelect,
  traffic,
  sumoSnapshot = null,
}: Props) {
  const stops = snapshot?.stops ?? network.stops;
  const intersections = snapshot?.intersections ?? network.intersections;
  const buses = snapshot?.buses ?? [];
  const sumoVehicles = (sumoSnapshot?.vehicles ?? []).filter(
    (vehicle): vehicle is LiveSumoVehicle & { lon: number; lat: number } =>
      Number.isFinite(vehicle.lon) && Number.isFinite(vehicle.lat)
  );

  const routeStops = network.route_order
    .map((id) => stops.find((stop) => stop.id === id))
    .filter((stop): stop is StopState => Boolean(stop));

  const routePoints = routeStops
    .map((stop) => {
      const p = project(stop.lon, stop.lat);
      return `${p.x},${p.y}`;
    })
    .join(" ");

  return (
    <div className="mapWorld fallbackMap">
      <div className="fallbackGrid" />

      <div className="fallbackTitle">
        <strong>Waterloo Corridor</strong>
        <span>UW → Uptown → Grand River Hospital → Central</span>
      </div>

      <svg
        className="fallbackMapSvg"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        aria-label="TransitOps Waterloo corridor map"
      >
        <path d="M0 16 C24 19 28 10 54 15 S82 24 100 18" className="fallbackStreet minor" />
        <path d="M0 38 C18 34 30 42 51 39 S78 32 100 40" className="fallbackStreet minor" />
        <path d="M0 61 C18 57 33 66 55 61 S82 52 100 59" className="fallbackStreet minor" />
        <path d="M0 82 C22 77 35 87 56 80 S77 74 100 78" className="fallbackStreet minor" />

        <path d="M12 0 C18 24 16 45 27 63 S43 84 47 100" className="fallbackStreet" />
        <path d="M70 0 C67 22 61 36 63 55 S72 79 77 100" className="fallbackStreet" />
        <path d="M93 0 C86 24 88 50 83 70 S82 88 88 100" className="fallbackStreet" />

        {routePoints && (
          <>
            <polyline points={routePoints} className="fallbackRouteHalo" />
            <polyline points={routePoints} className="fallbackRoute" />
          </>
        )}
      </svg>

      {intersections.map((node) => {
        const p = project(node.lon, node.lat);
        const selectedNode = selected?.type === "intersection" && selected.data.id === node.id;
        return (
          <button
            type="button"
            key={node.id}
            className={`fallbackSignal ${node.phase === "GREEN" ? "green" : "red"} ${selectedNode ? "selectedNode" : ""}`}
            style={{ left: `${p.x}%`, top: `${p.y}%` }}
            onClick={() => onSelect({ type: "intersection", data: node })}
            title={node.name}
          >
            <i />
            <span>{node.id}</span>
          </button>
        );
      })}

      {stops.map((stop) => {
        const p = project(stop.lon, stop.lat);
        const selectedStop = selected?.type === "stop" && selected.data.id === stop.id;
        return (
          <button
            type="button"
            key={stop.id}
            className={`fallbackStop ${selectedStop ? "selectedNode" : ""}`}
            style={{ left: `${p.x}%`, top: `${p.y}%` }}
            onClick={() => onSelect({ type: "stop", data: stop })}
          >
            <i />
            <span>{stop.name}</span>
            <small>{stop.queue ?? 0} waiting</small>
          </button>
        );
      })}

      {!sumoSnapshot &&
        buses.map((bus: BusState) => {
          const p = project(bus.lon, bus.lat);
          const selectedBus = selected?.type === "bus" && selected.data.id === bus.id;
          return (
            <button
              type="button"
              key={bus.id}
              className={`fallbackBus ${selectedBus ? "selectedBus" : ""}`}
              style={{ left: `${p.x}%`, top: `${p.y}%` }}
              onClick={() => onSelect({ type: "bus", data: bus })}
              title={`${bus.id} · ${bus.occupancy}/${bus.capacity}`}
            >
              <span>BUS</span>
              <small>{bus.id}</small>
            </button>
          );
        })}

      {sumoVehicles.map((vehicle) => {
        const p = project(vehicle.lon, vehicle.lat);
        const isBus = vehicle.type.toLowerCase().includes("bus");
        return (
          <div
            key={vehicle.id}
            className={isBus ? "fallbackLiveVehicle bus" : "fallbackLiveVehicle"}
            style={{ left: `${p.x}%`, top: `${p.y}%` }}
            title={`${vehicle.id} · ${vehicle.speed_mps.toFixed(1)} m/s`}
          />
        );
      })}

      {(() => {
        const p = project(network.yard.lon, network.yard.lat);
        return (
          <button
            type="button"
            className="fallbackYard"
            style={{ left: `${p.x}%`, top: `${p.y}%` }}
            onClick={() => onSelect({ type: "yard", data: network.yard })}
          >
            YARD
          </button>
        );
      })()}

      <div className="mapOverlayHud">
        <div>
          <span>NETWORK LOAD</span>
          <strong>{snapshot?.waiting ?? 0} waiting</strong>
        </div>
        <div>
          <span>ROAD CONDITIONS</span>
          <strong>{traffic >= 1.6 ? "HEAVY" : traffic >= 1.15 ? "MODERATE" : "LIGHT"}</strong>
        </div>
        <div>
          <span>TRAFFIC SOURCE</span>
          <strong>{sumoSnapshot ? `SUMO · ${sumoVehicles.length} vehicles` : "Python DES"}</strong>
        </div>
      </div>

      <div className="mapLegend">
        Waterloo corridor schematic · {sumoSnapshot ? "live SUMO + TraCI traffic" : "Python corridor prototype"}
      </div>
    </div>
  );
}
