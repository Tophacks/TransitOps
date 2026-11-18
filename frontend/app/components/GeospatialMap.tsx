"use client";

import { useEffect, useRef, useState } from "react";
import type { GeoJSONSource, Map as MapInstance } from "maplibre-gl";

import type { Network, Snapshot, Selection, LiveSumoSnapshot, StopState } from "../../lib/types";
import type { GrtMode } from "../../lib/grtTypes";
import { useGrtFleet } from "../../lib/useGrtFleet";

type Props = {
  network: Network;
  snapshot: Snapshot | null;
  onSelect: (selection: Selection) => void;
  sumoSnapshot?: LiveSumoSnapshot | null;
};

const MAP_STYLE = "https://tiles.openfreemap.org/styles/dark";
const SIMULATION_SOURCES = ["route", "stops", "intersections", "buses", "traffic", "yard"];
const SIMULATION_LAYERS = [
  "route-stroke", "stops-circle", "signals-circle", "yard-circle", "sumo-traffic", "buses-circle",
];
const REGION_CAMERA = { center: [-80.52, 43.463] as [number, number], zoom: 12.25 };
const CAMPUS_CAMERA = { center: [-80.5435, 43.4718] as [number, number], zoom: 15.65 };
const FEED_NAMES = { bus: "GRT bus", ion: "ION train" };

function featureCollection(features: GeoJSON.Feature[]): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features };
}

function point(lon: number, lat: number, properties: Record<string, string> = {}): GeoJSON.Feature {
  return { type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties };
}

function route(network: Network, stops: StopState[]): GeoJSON.FeatureCollection {
  const coordinates = network.route_order
    .map((id) => stops.find((stop) => stop.id === id))
    .filter((stop): stop is StopState => Boolean(stop))
    .map(({ lon, lat }) => [lon, lat]);
  return featureCollection(coordinates.length > 1
    ? [{ type: "Feature", geometry: { type: "LineString", coordinates }, properties: {} }]
    : []);
}

function setData(map: MapInstance, id: string, data: GeoJSON.FeatureCollection) {
  map.getSource<GeoJSONSource>(id)?.setData(data);
}

function addLayers(map: MapInstance) {
  const layers = map.getStyle()?.layers ?? [];
  const buildings = layers.findIndex((layer) => layer.id === "building");
  const firstLabel = layers.slice(buildings + 1).find((layer) => layer.type === "symbol")?.id;
  if (map.getSource("openmaptiles")) {
    map.addLayer({
      id: "corridor-buildings",
      type: "fill-extrusion",
      source: "openmaptiles",
      "source-layer": "building",
      minzoom: 14,
      filter: ["!=", ["get", "hide_3d"], true],
      layout: { visibility: "none" },
      paint: {
        "fill-extrusion-color": "#445363",
        "fill-extrusion-height": ["coalesce", ["get", "render_height"], 5],
        "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
        "fill-extrusion-opacity": 0.9,
      },
    }, firstLabel);
  }

  for (const id of [...SIMULATION_SOURCES, "grt-vehicles"]) {
    map.addSource(id, { type: "geojson", data: featureCollection([]) });
  }
  map.addLayer({ id: "route-stroke", type: "line", source: "route", paint: { "line-color": "#76e0bd", "line-width": 5, "line-opacity": 0.95 } });
  map.addLayer({ id: "stops-circle", type: "circle", source: "stops", paint: { "circle-radius": 8, "circle-color": "#e8fff2", "circle-stroke-color": "#177957", "circle-stroke-width": 3 } });
  map.addLayer({ id: "signals-circle", type: "circle", source: "intersections", paint: { "circle-radius": 5, "circle-color": ["match", ["get", "phase"], "GREEN", "#63c68f", "#e9a064"] } });
  map.addLayer({ id: "yard-circle", type: "circle", source: "yard", paint: { "circle-radius": 9, "circle-color": "#adbed3", "circle-stroke-color": "#203541", "circle-stroke-width": 3 } });
  map.addLayer({ id: "sumo-traffic", type: "circle", source: "traffic", paint: { "circle-radius": 3, "circle-color": "#748d9c", "circle-opacity": 0.8 } });
  map.addLayer({ id: "buses-circle", type: "circle", source: "buses", paint: { "circle-radius": 8, "circle-color": "#177957", "circle-stroke-color": "#eaffb5", "circle-stroke-width": 2 } });
  map.addLayer({
    id: "grt-points",
    type: "circle",
    source: "grt-vehicles",
    paint: {
      "circle-radius": ["match", ["get", "mode"], "ion", 8, 6],
      "circle-color": ["match", ["get", "mode"], "ion", "#67dcad", "#83bcff"],
      "circle-stroke-color": "#101f31",
      "circle-stroke-width": 2,
    },
  });
  if (map.getStyle()?.glyphs) {
    map.addLayer({
      id: "grt-route-labels",
      type: "symbol",
      source: "grt-vehicles",
      minzoom: 12,
      layout: {
        "text-field": ["get", "label"],
        "text-font": ["Noto Sans Regular"],
        "text-anchor": "bottom",
        "text-offset": [0, -0.8],
        "text-size": 13,
      },
      paint: { "text-color": "#eef6ff", "text-halo-color": "#101f31", "text-halo-width": 2 },
    });
  }
}

function fleetKey(mode: GrtMode, id: string) {
  return `${mode}:${id}`;
}

export default function GeospatialMap({ network, snapshot, onSelect, sumoSnapshot = null }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapInstance | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [tilted, setTilted] = useState(false);
  const [live, setLive] = useState(false);
  const [selectedVehicle, setSelectedVehicle] = useState<string | null>(null);
  const fleet = useGrtFleet(live);
  const vehicle = fleet?.vehicles.find(({ mode, id }) => fleetKey(mode, id) === selectedVehicle);
  const callbacks = useRef({ network, snapshot, onSelect });
  callbacks.current = { network, snapshot, onSelect };

  useEffect(() => {
    let disposed = false;
    let map: MapInstance | undefined;

    async function init() {
      try {
        const maplibre = await import("maplibre-gl");
        if (disposed || !host.current) return;
        maplibre.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");
        const currentMap = new maplibre.Map({
          container: host.current,
          style: MAP_STYLE,
          ...REGION_CAMERA,
          minZoom: 9,
          maxZoom: 18,
          attributionControl: false,
        });
        map = currentMap;
        mapRef.current = currentMap;
        currentMap.addControl(new maplibre.NavigationControl(), "top-right");
        currentMap.addControl(new maplibre.AttributionControl({ compact: true }), "bottom-right");
        currentMap.once("load", () => {
          if (disposed) return;
          addLayers(currentMap);
          const targets = [
            ["stops-circle", "stop"], ["signals-circle", "intersection"],
            ["buses-circle", "bus"], ["yard-circle", "yard"],
          ] as const;
          for (const [layer, type] of targets) {
            currentMap.on("click", layer, (event) => {
              const id = event.features?.[0]?.properties?.id;
              const current = callbacks.current;
              if (type === "stop") {
                const data = (current.snapshot?.stops ?? current.network.stops).find((item) => item.id === id);
                if (data) current.onSelect({ type, data });
              } else if (type === "intersection") {
                const data = (current.snapshot?.intersections ?? current.network.intersections).find((item) => item.id === id);
                if (data) current.onSelect({ type, data });
              } else if (type === "bus") {
                const data = current.snapshot?.buses.find((item) => item.id === id);
                if (data) current.onSelect({ type, data });
              } else {
                current.onSelect({ type, data: current.network.yard });
              }
            });
          }
          const fleetLayers = ["grt-points", "grt-route-labels"].filter((id) => currentMap.getLayer(id));
          currentMap.on("click", fleetLayers, (event) => {
            setSelectedVehicle(event.features?.[0]?.properties?.id ?? null);
          });
          const clickable = [...targets.map(([id]) => id), ...fleetLayers];
          currentMap.on("mouseenter", clickable, () => { currentMap.getCanvas().style.cursor = "pointer"; });
          currentMap.on("mouseleave", clickable, () => { currentMap.getCanvas().style.cursor = ""; });
          setFailed(false);
          setReady(true);
        });
        currentMap.on("error", () => {
          if (!currentMap.getStyle()?.layers?.length) setFailed(true);
        });
      } catch {
        if (!disposed) setFailed(true);
      }
    }

    void init();
    return () => {
      disposed = true;
      if (map) map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    for (const layer of SIMULATION_LAYERS) {
      map.setLayoutProperty(layer, "visibility", live ? "none" : "visible");
    }
    map.resize();

  }, [live, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || live) return;
    const stops = snapshot?.stops ?? network.stops;
    const intersections = snapshot?.intersections ?? network.intersections;
    const sumo = (sumoSnapshot?.vehicles ?? []).flatMap((item) => {
      if (item.lon == null || item.lat == null || !Number.isFinite(item.lon) || !Number.isFinite(item.lat)) return [];
      return [{ ...item, lon: item.lon, lat: item.lat }];
    });
    const buses = sumoSnapshot
      ? sumo.filter((item) => item.type.toLowerCase().includes("bus"))
      : snapshot?.buses ?? [];
    const traffic = sumo.filter((item) => !item.type.toLowerCase().includes("bus"));
    setData(map, "route", route(network, stops));
    setData(map, "stops", featureCollection(stops.map((item) => point(item.lon, item.lat, { id: item.id }))));
    setData(map, "intersections", featureCollection(intersections.map((item) => point(item.lon, item.lat, { id: item.id, phase: item.phase ?? "RED" }))));
    setData(map, "yard", featureCollection([point(network.yard.lon, network.yard.lat, { id: network.yard.id })]));
    setData(map, "buses", featureCollection(buses.map((item) => point(item.lon, item.lat, { id: item.id }))));
    setData(map, "traffic", featureCollection(traffic.map((item) => point(item.lon, item.lat))));
  }, [network, snapshot, sumoSnapshot, live, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const vehicles = live ? fleet?.vehicles ?? [] : [];
    setData(map, "grt-vehicles", featureCollection(vehicles.map((item) => point(item.lon, item.lat, {
      id: fleetKey(item.mode, item.id),
      mode: item.mode,
      label: item.route ?? (item.mode === "ion" ? "ION" : ""),
    }))));
  }, [fleet, live, ready]);

  function toggleTilt() {
    const map = mapRef.current;
    if (!map) return;
    const next = !tilted;
    setTilted(next);
    if (map.getLayer("corridor-buildings")) {
      map.setLayoutProperty("corridor-buildings", "visibility", next ? "visible" : "none");
    }
    map.easeTo(next
      ? { ...(map.getZoom() < 14 ? CAMPUS_CAMERA : {}), pitch: 55, bearing: -35 }
      : { pitch: 0, bearing: 0 });
  }

  return (
    <section className="geoMapShell" aria-label="Waterloo corridor geographic basemap">
      <div ref={host} className="geoMapHost" role="region" aria-label="Interactive Waterloo corridor map" />
      <div className="geoMapOverlay">
        <div className="geoMapControls" role="group" aria-label="Map layers">
          <button type="button" aria-pressed={tilted} disabled={!ready} onClick={toggleTilt}>3D buildings</button>
          <button type="button" aria-pressed={live} disabled={!ready} onClick={() => {
            setSelectedVehicle(null);
            setLive(!live);
          }}>GRT live</button>
        </div>
        {failed ? <span role="status">Basemap unavailable. Switch to Schematic to view the simulation.</span>
          : !ready ? <span role="status">Loading Waterloo map…</span>
          : live ? (
            <div className="geoFeedStatus" role="status">
              <span>GRT live fleet · public GPS feed</span>
              {(["bus", "ion"] as const).map((mode) => (
                <span key={mode} className={`geoFeedMode ${mode}`}>
                  {FEED_NAMES[mode]} · {fleet?.feeds[mode] === "current"
                    ? fleet.vehicles.filter((item) => item.mode === mode).length
                    : fleet?.feeds[mode] ?? "loading"}
                </span>
              ))}
            </div>
          ) : (
            <>
              <strong>{sumoSnapshot ? "SUMO simulator locations" : "Python simulated bus positions"}</strong>
              <span>Geospatial context · stop-to-stop route is illustrative</span>
            </>
          )}
      </div>
      {live && vehicle && (
        <aside className="geoVehicleCard" aria-label="Selected live vehicle">
          <button type="button" aria-label="Close vehicle details" onClick={() => setSelectedVehicle(null)}>×</button>
          <strong>{FEED_NAMES[vehicle.mode]} {vehicle.route}</strong>
          <span>{vehicle.headsign || `Vehicle ${vehicle.id}`}</span>
          <span>{vehicle.stop ? `${vehicle.stopStatus} · ${vehicle.stop}` : "Stop information unavailable"}</span>
        </aside>
      )}
    </section>
  );
}
