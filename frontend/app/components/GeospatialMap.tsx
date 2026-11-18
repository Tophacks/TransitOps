"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { GeoJSONSource, Map as MapInstance, Popup } from "maplibre-gl";

import type { Network, Snapshot, Selection, LiveSumoSnapshot, SimulationEvent, StopState } from "../../lib/types";
import type { GrtMode } from "../../lib/grtTypes";
import { useGrtFleet } from "../../lib/useGrtFleet";

type Props = {
  network: Network;
  snapshot: Snapshot | null;
  decision?: SimulationEvent;
  selected: Selection;
  onSelect: (selection: Selection) => void;
  live: boolean;
  onModeChange: (live: boolean) => void;
  sumoSnapshot?: LiveSumoSnapshot | null;
};

const MAP_STYLE = "https://tiles.openfreemap.org/styles/dark";
const SIMULATION_SOURCES = ["route", "stops", "intersections", "buses", "traffic", "yard"];
const SIMULATION_LAYERS = [
  "route-stroke", "stops-circle", "signals-circle", "yard-circle", "sumo-traffic", "sim-bus-markers",
];
const REGION_CAMERA = { center: [-80.518, 43.462] as [number, number], zoom: 13.35 };
const STREET_CAMERA = { pitch: 48, bearing: -24 };
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

function addVehicleIcon(map: MapInstance, name: string, color: string, train = false) {
  const canvas = document.createElement("canvas");
  canvas.width = 56;
  canvas.height = 56;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Vehicle icons require a 2D canvas");
  context.scale(2, 2);

  context.fillStyle = color;
  context.strokeStyle = "#101c28";
  context.lineWidth = 1.5;
  context.beginPath();
  context.roundRect(1, 1, 26, 26, train ? 10 : 7);
  context.fill();
  context.stroke();

  context.fillStyle = "#111e2c";
  context.beginPath();
  context.roundRect(train ? 8 : 6, 5, train ? 12 : 16, 15, train ? 5 : 4);
  context.fill();
  context.fillRect(9, 19, 3, 3);
  context.fillRect(16, 19, 3, 3);
  context.fillStyle = color;
  context.fillRect(9, 8, 10, 5);
  for (const x of [10, 16]) context.fillRect(x, 16, 2, 2);
  if (train) {
    context.strokeStyle = "#111e2c";
    context.beginPath();
    context.moveTo(10, 20);
    context.lineTo(8, 23);
    context.moveTo(18, 20);
    context.lineTo(20, 23);
    context.stroke();
  }
  map.addImage(name, context.getImageData(0, 0, 56, 56), { pixelRatio: 2 });
}

function addLayers(map: MapInstance) {
  addVehicleIcon(map, "grt-bus", "#83bcff");
  addVehicleIcon(map, "ion-train", "#79e2b9", true);
  addVehicleIcon(map, "sim-bus", "#ead387");
  const layers = map.getStyle()?.layers ?? [];
  const buildings = layers.findIndex((layer) => layer.id === "building");
  const firstLabel = layers.slice(buildings + 1).find((layer) => layer.type === "symbol")?.id;
  if (map.getSource("openmaptiles")) {
    map.addLayer({
      id: "corridor-buildings",
      type: "fill-extrusion",
      source: "openmaptiles",
      "source-layer": "building",
      minzoom: 13,
      filter: ["!=", ["get", "hide_3d"], true],
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
  map.addLayer({
    id: "sim-bus-markers",
    type: "symbol",
    source: "buses",
    layout: { "icon-image": "sim-bus", "icon-allow-overlap": true },
  });
  map.addLayer({
    id: "grt-points",
    type: "symbol",
    source: "grt-vehicles",
    layout: {
      "icon-image": ["match", ["get", "mode"], "ion", "ion-train", "grt-bus"],
      "icon-size": ["interpolate", ["linear"], ["zoom"], 9, 0.55, 13, 0.82, 16, 1],
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
        "text-offset": [0, -1.05],
        "text-optional": true,
        "text-ignore-placement": true,
        "text-size": 13,
      },
      paint: { "text-color": "#eef6ff", "text-halo-color": "#101f31", "text-halo-width": 2 },
    });
  }
}

function fleetKey(mode: GrtMode, id: string) {
  return `${mode}:${id}`;
}

export default function GeospatialMap({
  network, snapshot, decision, selected, onSelect, live, onModeChange, sumoSnapshot = null,
}: Props) {
  const host = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapInstance | null>(null);
  const popupRef = useRef<Popup | null>(null);
  const [popupContent, setPopupContent] = useState<HTMLDivElement | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [tilted, setTilted] = useState(true);
  const [selectedVehicle, setSelectedVehicle] = useState<string | null>(null);
  const [selectedSumo, setSelectedSumo] = useState<string | null>(null);
  const fleet = useGrtFleet(live);
  const vehicle = live ? fleet?.vehicles.find(({ mode, id }) => fleetKey(mode, id) === selectedVehicle) : undefined;
  const sumoBus = !live ? sumoSnapshot?.vehicles.find((item) => item.id === selectedSumo) : undefined;
  const simBus = !live && !sumoSnapshot && selected?.type === "bus"
    ? snapshot?.buses.find((item) => item.id === selected.data.id) : undefined;
  const focus = vehicle ?? sumoBus ?? simBus;
  const lon = focus?.lon;
  const lat = focus?.lat;
  const callbacks = useRef({ network, snapshot, onSelect, sumoSnapshot });
  callbacks.current = { network, snapshot, onSelect, sumoSnapshot };

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
          ...STREET_CAMERA,
          minZoom: 9,
          maxZoom: 18,
          attributionControl: false,
        });
        map = currentMap;
        mapRef.current = currentMap;
        const content = document.createElement("div");
        popupRef.current = new maplibre.Popup({
          closeButton: false, closeOnClick: false, offset: 16, maxWidth: "245px",
          className: "geoVehiclePopup",
        }).setDOMContent(content);
        setPopupContent(content);
        currentMap.addControl(new maplibre.NavigationControl(), "top-right");
        currentMap.addControl(new maplibre.AttributionControl({ compact: true }), "bottom-right");
        currentMap.once("load", () => {
          if (disposed) return;
          addLayers(currentMap);
          const clickable = [
            "stops-circle", "signals-circle", "yard-circle", "sim-bus-markers",
            "grt-points", "grt-route-labels",
          ].filter((id) => currentMap.getLayer(id));
          currentMap.on("click", clickable, (event) => {
            const feature = event.features?.[0];
            const id = feature?.properties?.id;
            if (typeof id !== "string") return;
            const current = callbacks.current;
            switch (feature?.layer.id) {
              case "grt-points":
              case "grt-route-labels":
                setSelectedVehicle(id);
                break;
              case "stops-circle": {
                const data = (current.snapshot?.stops ?? current.network.stops).find((item) => item.id === id);
                if (data) current.onSelect({ type: "stop", data });
                break;
              }
              case "signals-circle": {
                const data = (current.snapshot?.intersections ?? current.network.intersections).find((item) => item.id === id);
                if (data) current.onSelect({ type: "intersection", data });
                break;
              }
              case "sim-bus-markers": {
                if (current.sumoSnapshot) {
                  setSelectedSumo(id);
                  current.onSelect(null);
                } else {
                  const data = current.snapshot?.buses.find((item) => item.id === id);
                  if (data) current.onSelect({ type: "bus", data });
                }
                break;
              }
              case "yard-circle":
                current.onSelect({ type: "yard", data: current.network.yard });
                break;
            }
          });
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
      popupRef.current?.remove();
      popupRef.current = null;
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

  useEffect(() => {
    const map = mapRef.current;
    const popup = popupRef.current;
    if (!map || !popup || !ready) return;
    if (lon == null || lat == null) {
      popup.remove();
      return;
    }
    popup.setLngLat([lon, lat]);
    if (!popup.isOpen()) popup.addTo(map);
  }, [lat, lon, ready]);

  function clearDetails() {
    if (live) setSelectedVehicle(null);
    else {
      setSelectedSumo(null);
      onSelect(null);
    }
  }

  function toggleTilt() {
    const map = mapRef.current;
    if (!map) return;
    const next = !tilted;
    setTilted(next);
    if (map.getLayer("corridor-buildings")) {
      map.setLayoutProperty("corridor-buildings", "visibility", next ? "visible" : "none");
    }
    map.easeTo(next
      ? STREET_CAMERA
      : { pitch: 0, bearing: 0 });
  }

  return (
    <section className="geoMapShell" aria-label="Waterloo corridor geographic basemap">
      <div ref={host} className="geoMapHost" role="region" aria-label="Interactive Waterloo corridor map" />
      <div className="geoMapOverlay">
        <div className="geoMapControls" role="group" aria-label="Map layers">
          <button type="button" aria-pressed={tilted} disabled={!ready} onClick={toggleTilt}>3D</button>
          <button type="button" disabled={!ready} onClick={() => mapRef.current?.easeTo(CAMPUS_CAMERA)}>Campus</button>
          <button type="button" disabled={!ready} onClick={() => mapRef.current?.easeTo(REGION_CAMERA)}>Region</button>
          <button type="button" aria-pressed={live} onClick={() => {
            clearDetails();
            onModeChange(true);
          }}>Live fleet</button>
          <button type="button" aria-pressed={!live} onClick={() => {
            clearDetails();
            onModeChange(false);
          }}>Simulation</button>
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
      {popupContent && focus && createPortal(
        <aside className="geoVehicleCard" aria-label="Selected vehicle details">
          <button type="button" aria-label="Close vehicle details" onClick={clearDetails}>×</button>
          {vehicle ? (
            <>
              <strong>{FEED_NAMES[vehicle.mode]} {vehicle.route}</strong>
              <span>{vehicle.headsign || `Vehicle ${vehicle.id}`}</span>
              <span>{vehicle.stop ? `${vehicle.stopStatus} · ${vehicle.stop}` : "Stop information unavailable"}</span>
            </>
          ) : sumoBus ? (
            <>
              <strong>SUMO bus {sumoBus.id}</strong>
              <span>{Math.round(sumoBus.speed_mps * 3.6)} km/h</span>
            </>
          ) : simBus && (
            <>
              <strong>Simulated bus {simBus.id}</strong>
              <span>{simBus.state.replaceAll("_", " ")} · {simBus.occupancy}/{simBus.capacity} aboard</span>
              {simBus.state !== "YARD" && <span>{simBus.state === "DWELLING" ? "At stop" : "Target stop"} · {simBus.target}</span>}
              {decision ? (
                <div className="geoDecision">
                  <strong>Controller · {String(decision.minute).padStart(2, "0")}:00</strong>
                  <span>{decision.message}</span>
                  <span>{decision.reason}</span>
                </div>
              ) : <span>No dispatch or priority decision recorded by this minute.</span>}
            </>
          )}
        </aside>,
        popupContent,
      )}
    </section>
  );
}
