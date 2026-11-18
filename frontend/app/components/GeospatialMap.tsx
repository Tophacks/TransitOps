"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { GeoJSONSource, Map as MapInstance, Popup } from "maplibre-gl";

import type { Network, Snapshot, Selection, LiveSumoSnapshot, SimulationEvent, StopState } from "../../lib/types";
import type { GrtMode, GrtShapes, GrtVehicle } from "../../lib/grtTypes";
import { useGrtFleet } from "../../lib/useGrtFleet";
import type { PlaybackClock } from "../../lib/motion/clock.ts";
import { busPoses, type BusPose } from "../../lib/motion/playback.ts";
import { ION_LENGTH, articulatePath, ionTrains, type TrainPose } from "../../lib/motion/ion.ts";
import { busBody, emptyBodies, trainBody, type BodyLayers } from "../../lib/motion/bodies.ts";
import { LiveTracker, type LivePing, type LivePose } from "../../lib/motion/live.ts";
import { encodePath, offsetLonLat, type PathPoint } from "../../lib/motion/path.ts";
import { corridor, ion } from "../../lib/motion/scene.ts";

type Props = {
  network: Network;
  snapshot: Snapshot | null;
  decision?: SimulationEvent;
  selected: Selection;
  onSelect: (selection: Selection) => void;
  live: boolean;
  onModeChange: (live: boolean) => void;
  sumoSnapshot?: LiveSumoSnapshot | null;
  timeline: Snapshot[];
  clock: PlaybackClock;
};

type Camera = { center: [number, number]; zoom: number; pitch: number; bearing: number };

const MAP_STYLE = "https://tiles.openfreemap.org/styles/dark";
const SIMULATION_SOURCES = ["route", "ion-track", "stops", "intersections", "buses", "traffic", "yard", "vehicle-solids", "vehicle-ground"];
const SIMULATION_LAYERS = [
  "route-stroke", "ion-track-line", "stops-circle", "stops-label", "signals-circle", "yard-circle", "sumo-traffic",
  "sim-bus-markers", "vehicle-ground", "vehicle-bodies",
];
// Live GPS vehicles drawn as 3D bodies at street zoom.
const LIVE_LAYERS = ["live-ground", "live-bodies"];
const SHAPE_BATCH = 40;
// Bodies take over from flat markers once a 12 m bus is legible.
const BODY_MIN_ZOOM = 15;
const REGION_CAMERA = { center: [-80.518, 43.462] as [number, number], zoom: 13.35 };
const STREET_CAMERA = { pitch: 48, bearing: -24 };
const FEED_NAMES = { bus: "GRT bus", ion: "ION train" };

/** Close-up over the Phillip St / University Ave corner and the UW Station bus loop. */
function campusCamera(width: number): Camera {
  const narrow = width < 640;
  // On a phone the overlay covers the top of the map: look further along the
  // bus loop so UW Station and the run toward Phillip St stay in view.
  return narrow
    ? { center: [-80.5393, 43.47425], zoom: 17.7, pitch: 60, bearing: 35 }
    : { center: [-80.539, 43.4743], zoom: 18.3, pitch: 62, bearing: 20 };
}

/** Where ION turns from King St onto Erb St at Waterloo Public Square. */
function ionCurveCamera(width: number): Camera {
  return { center: [-80.52228, 43.46422], zoom: width < 640 ? 17.7 : 18.35, pitch: 60, bearing: 28 };
}

function featureCollection(features: GeoJSON.Feature[]): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features };
}

function point(lon: number, lat: number, properties: Record<string, string | number> = {}): GeoJSON.Feature {
  return { type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties };
}

function line(coordinates: number[][], properties: Record<string, string> = {}): GeoJSON.Feature {
  return { type: "Feature", geometry: { type: "LineString", coordinates }, properties };
}

/** The simulated buses' street paths (not straight stop-to-stop lines). */
function streetRoutes(): GeoJSON.FeatureCollection {
  return featureCollection(corridor.data.segments
    .filter((segment) => segment.from !== "YARD")
    .map((segment) => line(segment.path.map(([lon, lat]) => [lon, lat]), { id: `${segment.from}>${segment.to}` })));
}

function ionTracks(): GeoJSON.FeatureCollection {
  return featureCollection((["north", "south"] as const).map((direction) =>
    line(corridor.data.ion[direction].path.map(([lon, lat]) => [lon, lat]), { id: direction })));
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
        // Opaque so buildings hide vehicles that drive behind them.
        "fill-extrusion-opacity": 1,
      },
    }, firstLabel);
  }

  for (const id of [...SIMULATION_SOURCES, "grt-vehicles", "live-solids", "live-ground"]) {
    map.addSource(id, { type: "geojson", data: featureCollection([]) });
  }
  setData(map, "route", streetRoutes());
  setData(map, "ion-track", ionTracks());
  map.addLayer({
    id: "ion-track-line", type: "line", source: "ion-track",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#7d8f9b", "line-width": ["interpolate", ["exponential", 2], ["zoom"], 12, 1, 19, 9], "line-opacity": 0.55 },
  }, firstLabel);
  map.addLayer({
    id: "route-stroke", type: "line", source: "route",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#76e0bd", "line-width": ["interpolate", ["exponential", 2], ["zoom"], 12, 2.5, 19, 20], "line-opacity": ["interpolate", ["linear"], ["zoom"], 14, 0.9, 17, 0.28] },
  }, firstLabel);
  map.addLayer({
    id: "vehicle-ground", type: "fill", source: "vehicle-ground", minzoom: BODY_MIN_ZOOM,
    paint: { "fill-color": ["get", "color"], "fill-opacity": ["get", "opacity"], "fill-antialias": true },
  }, firstLabel);
  map.addLayer({
    id: "vehicle-bodies", type: "fill-extrusion", source: "vehicle-solids", minzoom: BODY_MIN_ZOOM,
    paint: {
      "fill-extrusion-color": ["get", "color"],
      "fill-extrusion-base": ["get", "base"],
      "fill-extrusion-height": ["get", "height"],
      "fill-extrusion-opacity": 1,
      "fill-extrusion-vertical-gradient": true,
    },
  }, firstLabel);
  map.addLayer({
    id: "live-ground", type: "fill", source: "live-ground", minzoom: BODY_MIN_ZOOM,
    paint: { "fill-color": ["get", "color"], "fill-opacity": ["get", "opacity"], "fill-antialias": true },
  }, firstLabel);
  map.addLayer({
    id: "live-bodies", type: "fill-extrusion", source: "live-solids", minzoom: BODY_MIN_ZOOM,
    paint: {
      "fill-extrusion-color": ["get", "color"],
      "fill-extrusion-base": ["get", "base"],
      "fill-extrusion-height": ["get", "height"],
      "fill-extrusion-opacity": 1,
      "fill-extrusion-vertical-gradient": true,
    },
  }, firstLabel);
  map.addLayer({ id: "stops-circle", type: "circle", source: "stops", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 6, 18, 9], "circle-color": "#e8fff2", "circle-stroke-color": "#177957", "circle-stroke-width": 3, "circle-pitch-alignment": "map" } });
  map.addLayer({ id: "signals-circle", type: "circle", source: "intersections", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 4, 18, 8], "circle-color": ["match", ["get", "phase"], "GREEN", "#63c68f", "#e9a064"], "circle-pitch-alignment": "map" } });
  map.addLayer({ id: "yard-circle", type: "circle", source: "yard", paint: { "circle-radius": 9, "circle-color": "#adbed3", "circle-stroke-color": "#203541", "circle-stroke-width": 3 } });
  map.addLayer({ id: "sumo-traffic", type: "circle", source: "traffic", paint: { "circle-radius": 3, "circle-color": "#748d9c", "circle-opacity": 0.8 } });
  map.addLayer({
    id: "sim-bus-markers",
    type: "symbol",
    source: "buses",
    maxzoom: BODY_MIN_ZOOM + 0.2,
    layout: { "icon-image": "sim-bus", "icon-allow-overlap": true },
  });
  map.addLayer({
    id: "grt-points",
    type: "symbol",
    source: "grt-vehicles",
    // Icons when zoomed out; 3D bodies take over at street zoom.
    maxzoom: BODY_MIN_ZOOM + 0.2,
    layout: {
      "icon-image": ["match", ["get", "mode"], "ion", "ion-train", "grt-bus"],
      "icon-size": ["interpolate", ["linear"], ["zoom"], 9, 0.55, 13, 0.82, 16, 1],
      "icon-allow-overlap": true,
    },
    paint: { "icon-opacity": ["get", "opacity"] },
  });
  if (map.getStyle()?.glyphs) {
    map.addLayer({
      id: "stops-label",
      type: "symbol",
      source: "stops",
      minzoom: 15.5,
      layout: {
        "text-field": ["get", "name"],
        "text-font": ["Noto Sans Regular"],
        "text-size": 12,
        "text-anchor": "top",
        "text-offset": [0, 1.1],
        "text-optional": true,
      },
      paint: { "text-color": "#e8fff2", "text-halo-color": "#0c1d18", "text-halo-width": 2 },
    });
    map.addLayer({
      id: "grt-route-labels",
      type: "symbol",
      source: "grt-vehicles",
      minzoom: 12,
      layout: {
        // Labelled as live GPS once the 3D bodies show.
        "text-field": ["step", ["zoom"], ["get", "label"], BODY_MIN_ZOOM, ["concat", ["get", "label"], " · live GPS"]],
        "text-font": ["Noto Sans Regular"],
        "text-anchor": "bottom",
        "text-offset": [0, -1.05],
        "text-optional": true,
        "text-ignore-placement": true,
        "text-size": 13,
      },
      paint: { "text-color": "#eef6ff", "text-halo-color": "#101f31", "text-halo-width": 2, "text-opacity": ["get", "opacity"] },
    });
  }
}

function fleetKey(mode: GrtMode, id: string) {
  return `${mode}:${id}`;
}

type Scene = { buses: BusPose[]; trains: TrainPose[] };

function livePing(vehicle: GrtVehicle): LivePing {
  return {
    key: fleetKey(vehicle.mode, vehicle.id),
    id: vehicle.id,
    mode: vehicle.mode,
    lon: vehicle.lon,
    lat: vehicle.lat,
    timestamp: vehicle.timestamp,
    shapeId: vehicle.shapeId ? `${vehicle.mode}:${vehicle.shapeId}` : null,
    progress: vehicle.progress,
    stopped: vehicle.stopStatus === "At stop",
  };
}

/** A short straight track through a train that is off its shape (detour or no shape). */
function straightTrack(centre: [number, number], heading: number): PathPoint[] {
  const h = (heading * Math.PI) / 180;
  const half = ION_LENGTH;
  const back = offsetLonLat(centre, -Math.sin(h) * half, -Math.cos(h) * half);
  const ahead = offsetLonLat(centre, Math.sin(h) * half, Math.cos(h) * half);
  return encodePath([back, ahead]);
}

function liveBody(pose: LivePose, selected: boolean, out: BodyLayers) {
  const livery = pose.stale ? "stale" : "live";
  if (pose.mode === "ion") {
    // The GPS fix is the middle of the train; articulate the five sections behind the nose.
    const path = pose.path ?? straightTrack(pose.centre, pose.heading);
    const nose = (pose.path && pose.s != null ? pose.s : ION_LENGTH) + ION_LENGTH / 2;
    trainBody({ id: pose.key, direction: "north", nose, speed: pose.speed, sections: articulatePath(path, nose) }, out, livery, pose.opacity);
  } else {
    busBody({ id: pose.key, centre: pose.centre, heading: pose.heading }, selected, out, livery, pose.opacity);
  }
}

export default function GeospatialMap({
  network, snapshot, decision, selected, onSelect, live, onModeChange, sumoSnapshot = null, timeline, clock,
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
  const [selectedTrain, setSelectedTrain] = useState<string | null>(null);
  const [follow, setFollow] = useState(false);
  const fleet = useGrtFleet(live);
  const trackerRef = useRef(new LiveTracker());
  const shapeCache = useRef(new Map<string, PathPoint[]>());
  const fleetRef = useRef(new Map<string, GrtVehicle>());
  const vehicle = live ? fleet?.vehicles.find(({ mode, id }) => fleetKey(mode, id) === selectedVehicle) : undefined;
  const sumoBus = !live ? sumoSnapshot?.vehicles.find((item) => item.id === selectedSumo) : undefined;
  const simulating = !live && !sumoSnapshot;
  const simBus = simulating && selected?.type === "bus"
    ? snapshot?.buses.find((item) => item.id === selected.data.id) : undefined;
  // Positions at this render; the animation loop keeps the popup glued to the body.
  const now = clock.time();
  const simPose = simBus ? busPoses(corridor, timeline, now).find((pose) => pose.id === simBus.id) : undefined;
  const trainPose = simulating && selectedTrain ? ionTrains(ion, now).find((train) => train.id === selectedTrain) : undefined;
  const focus = vehicle ?? sumoBus;
  const lon = focus?.lon;
  const lat = focus?.lat;
  const livePose = vehicle ? trackerRef.current.pose(fleetKey(vehicle.mode, vehicle.id), performance.now() / 1000) : undefined;
  const animatedFocus = simPose ? simPose.id : trainPose ? trainPose.id : vehicle ? fleetKey(vehicle.mode, vehicle.id) : null;
  const callbacks = useRef({ network, snapshot, onSelect, sumoSnapshot, timeline });
  callbacks.current = { network, snapshot, onSelect, sumoSnapshot, timeline };
  const sceneRef = useRef<Scene>({ buses: [], trains: [] });
  const sceneState = useRef({ animatedFocus, follow, selectedBus: simBus?.id ?? null, simulating });
  sceneState.current = { animatedFocus, follow, selectedBus: simBus?.id ?? null, simulating };

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
          maxZoom: 19.5,
          maxPitch: 75,
          attributionControl: false,
        });
        map = currentMap;
        mapRef.current = currentMap;
        // Opt-in handle for the browser checks in e2e/ (open the page with ?e2e).
        if (new URLSearchParams(window.location.search).has("e2e")) {
          (window as unknown as { __transitops?: unknown }).__transitops = {
            map: currentMap,
            scene: () => sceneRef.current,
            seek: (seconds: number) => clock.seek(seconds),
            live: () => trackerRef.current.poses(performance.now() / 1000)
              .map(({ path: _path, ...pose }) => pose),
          };
        }
        const content = document.createElement("div");
        popupRef.current = new maplibre.Popup({
          closeButton: false, closeOnClick: false, offset: 22, maxWidth: "260px",
          className: "geoVehiclePopup",
        }).setDOMContent(content);
        setPopupContent(content);
        currentMap.addControl(new maplibre.NavigationControl({ visualizePitch: true }), "top-right");
        currentMap.addControl(new maplibre.AttributionControl({
          compact: true,
          customAttribution: "Street paths: GRT GTFS, Region of Waterloo Open Data",
        }), "bottom-right");
        currentMap.on("dragstart", () => setFollow(false));
        // MapLibre opens the compact attribution expanded; on a phone that
        // covers a sixth of the map. Keep it behind its (i) button there.
        if (host.current.clientWidth < 640) {
          host.current.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");
        }
        currentMap.once("load", () => {
          if (disposed) return;
          addLayers(currentMap);
          const clickable = [
            "stops-circle", "signals-circle", "yard-circle", "sim-bus-markers", "vehicle-bodies",
            "grt-points", "grt-route-labels", "live-bodies",
          ].filter((id) => currentMap.getLayer(id));
          currentMap.on("click", clickable, (event) => {
            const feature = event.features?.[0];
            const id = feature?.properties?.id;
            if (typeof id !== "string") return;
            const current = callbacks.current;
            switch (feature?.layer.id) {
              case "grt-points":
              case "grt-route-labels":
              case "live-bodies":
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
              case "vehicle-bodies":
              case "sim-bus-markers": {
                if (current.sumoSnapshot) {
                  setSelectedSumo(id);
                  current.onSelect(null);
                } else if (id.startsWith("ION-")) {
                  setSelectedTrain(id);
                  current.onSelect(null);
                } else {
                  const data = current.snapshot?.buses.find((item) => item.id === id);
                  if (data) {
                    setSelectedTrain(null);
                    current.onSelect({ type: "bus", data });
                  }
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
      if (map.getLayer(layer)) map.setLayoutProperty(layer, "visibility", live ? "none" : "visible");
    }
    for (const layer of LIVE_LAYERS) {
      if (map.getLayer(layer)) map.setLayoutProperty(layer, "visibility", live ? "visible" : "none");
    }
    map.resize();
    if (!live) map.easeTo({ ...campusCamera(map.getContainer().clientWidth), duration: 1600 });
    else if (map.getZoom() > 16) map.easeTo({ ...REGION_CAMERA, ...STREET_CAMERA, duration: 1200 });
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
    const traffic = sumo.filter((item) => !item.type.toLowerCase().includes("bus"));
    setData(map, "stops", featureCollection(stops.map((item: StopState) => point(item.lon, item.lat, { id: item.id, name: item.name }))));
    setData(map, "intersections", featureCollection(intersections.map((item) => point(item.lon, item.lat, { id: item.id, phase: item.phase ?? "RED" }))));
    setData(map, "yard", featureCollection([point(network.yard.lon, network.yard.lat, { id: network.yard.id })]));
    setData(map, "traffic", featureCollection(traffic.map((item) => point(item.lon, item.lat))));
    if (sumoSnapshot) {
      const buses = sumo.filter((item) => item.type.toLowerCase().includes("bus"));
      setData(map, "buses", featureCollection(buses.map((item) => point(item.lon, item.lat, { id: item.id }))));
      setData(map, "vehicle-solids", featureCollection([]));
      setData(map, "vehicle-ground", featureCollection([]));
    }
  }, [network, snapshot, sumoSnapshot, live, ready]);

  // Animation loop: every frame derives the scene from the playback clock.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || live || sumoSnapshot) return;
    let frame = 0;
    let lastKey = "";
    const draw = () => {
      const t = clock.time();
      const state = sceneState.current;
      const key = `${t}|${state.selectedBus}|${state.animatedFocus}|${state.follow}`;
      if (key !== lastKey) {
        lastKey = key;
        const scene: Scene = {
          buses: busPoses(corridor, callbacks.current.timeline, t),
          trains: ionTrains(ion, t),
        };
        sceneRef.current = scene;
        const bodies = emptyBodies();
        for (const bus of scene.buses) busBody(bus, bus.id === state.selectedBus, bodies);
        for (const train of scene.trains) trainBody(train, bodies);
        setData(map, "vehicle-solids", featureCollection(bodies.solids));
        setData(map, "vehicle-ground", featureCollection(bodies.ground));
        setData(map, "buses", featureCollection(scene.buses.map((bus) => point(bus.centre[0], bus.centre[1], { id: bus.id }))));
        const focusPose = scene.buses.find((bus) => bus.id === state.animatedFocus)?.centre
          ?? scene.trains.find((train) => train.id === state.animatedFocus)?.sections[0].centre;
        const popup = popupRef.current;
        if (focusPose && popup) {
          popup.setLngLat(focusPose);
          if (!popup.isOpen()) popup.addTo(map);
          if (state.follow) map.jumpTo({ center: focusPose });
        }
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [clock, live, ready, sumoSnapshot, timeline]);

  // Each poll of the live feed: fetch any route shapes we lack, then hand the
  // pings to the tracker, which glides vehicles along those shapes.
  useEffect(() => {
    if (!live) {
      trackerRef.current = new LiveTracker();
      fleetRef.current = new Map();
      return;
    }
    if (!fleet) return;
    let cancelled = false;
    async function sync(vehicles: GrtVehicle[]) {
      for (const mode of ["bus", "ion"] as const) {
        const missing = [...new Set(vehicles
          .filter((item) => item.mode === mode && item.shapeId && !shapeCache.current.has(`${mode}:${item.shapeId}`))
          .map((item) => item.shapeId as string))];
        for (let i = 0; i < missing.length; i += SHAPE_BATCH) {
          const query = missing.slice(i, i + SHAPE_BATCH).map((id) => `id=${encodeURIComponent(id)}`).join("&");
          try {
            const response = await fetch(`/feeds/grt/shapes?mode=${mode}&${query}`);
            if (!response.ok) continue;
            const body = (await response.json()) as GrtShapes;
            for (const [id, path] of Object.entries(body.shapes)) shapeCache.current.set(`${mode}:${id}`, path);
          } catch {
            // Without a shape the vehicle is drawn at its raw GPS fix.
          }
        }
      }
      if (cancelled) return;
      fleetRef.current = new Map(vehicles.map((item) => [fleetKey(item.mode, item.id), item]));
      trackerRef.current.update(vehicles.map(livePing), (id) => shapeCache.current.get(id), performance.now() / 1000);
    }
    void sync(fleet.vehicles);
    return () => {
      cancelled = true;
    };
  }, [fleet, live]);

  // Live animation loop: icons when zoomed out, 3D bodies at street zoom.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    if (!live) {
      for (const id of ["grt-vehicles", "live-solids", "live-ground"]) setData(map, id, featureCollection([]));
      return;
    }
    let frame = 0;
    let lastIcons = 0;
    let hadBodies = true;
    const draw = (time: number) => {
      const poses = trackerRef.current.poses(time / 1000);
      const zoom = map.getZoom();
      const state = sceneState.current;
      // Icons move a fraction of a pixel per frame when zoomed out: ~12 updates a second is smooth.
      if (time - lastIcons > (zoom < BODY_MIN_ZOOM ? 80 : 400)) {
        lastIcons = time;
        setData(map, "grt-vehicles", featureCollection(poses.map((pose) => {
          const info = fleetRef.current.get(pose.key);
          return point(pose.centre[0], pose.centre[1], {
            id: pose.key,
            mode: pose.mode,
            label: info?.route ?? (pose.mode === "ion" ? "ION" : ""),
            opacity: pose.opacity * (pose.stale ? 0.55 : 1),
          });
        })));
      }
      const bodies = emptyBodies();
      const showBodies = zoom >= BODY_MIN_ZOOM - 0.2;
      if (showBodies) {
        const bounds = map.getBounds();
        const pad = 0.004;
        for (const pose of poses) {
          const [lon, lat] = pose.centre;
          if (lon < bounds.getWest() - pad || lon > bounds.getEast() + pad || lat < bounds.getSouth() - pad || lat > bounds.getNorth() + pad) continue;
          liveBody(pose, pose.key === state.animatedFocus, bodies);
        }
      }
      // Zoomed out there are no bodies: don't re-send empty data every frame.
      if (showBodies || hadBodies) {
        setData(map, "live-solids", featureCollection(bodies.solids));
        setData(map, "live-ground", featureCollection(bodies.ground));
        hadBodies = showBodies;
      }
      const focus = state.animatedFocus ? poses.find((pose) => pose.key === state.animatedFocus) : undefined;
      const popup = popupRef.current;
      if (focus && popup) {
        popup.setLngLat(focus.centre);
        if (!popup.isOpen()) popup.addTo(map);
        if (state.follow) map.jumpTo({ center: focus.centre });
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [live, ready]);

  useEffect(() => {
    const map = mapRef.current;
    const popup = popupRef.current;
    if (!map || !popup || !ready) return;
    if (animatedFocus) return; // positioned by the animation loop
    if (lon == null || lat == null) {
      popup.remove();
      return;
    }
    popup.setLngLat([lon, lat]);
    if (!popup.isOpen()) popup.addTo(map);
  }, [lat, lon, ready, animatedFocus]);

  function clearDetails() {
    setFollow(false);
    setSelectedTrain(null);
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

  function goTo(camera: (width: number) => Camera) {
    const map = mapRef.current;
    if (!map) return;
    setFollow(false);
    map.easeTo({ ...camera(map.getContainer().clientWidth), duration: 1400 });
  }

  const showPopup = Boolean(focus || simPose || trainPose);

  return (
    <section className="geoMapShell" aria-label="Waterloo corridor geographic basemap">
      <div ref={host} className="geoMapHost" role="region" aria-label="Interactive Waterloo corridor map" />
      <div className="geoMapOverlay">
        <div className="geoMapControls" role="group" aria-label="Map layers">
          <button type="button" aria-pressed={tilted} disabled={!ready} onClick={toggleTilt}>3D</button>
          <button type="button" disabled={!ready} onClick={() => goTo(campusCamera)}>Campus</button>
          {!live && <button type="button" disabled={!ready} onClick={() => goTo(ionCurveCamera)}>ION curve</button>}
          <button type="button" disabled={!ready} onClick={() => mapRef.current?.easeTo({ ...REGION_CAMERA, ...STREET_CAMERA })}>Region</button>
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
              <span>Live GPS · GRT public feed, gliding along GTFS route shapes between pings</span>
              {(["bus", "ion"] as const).map((mode) => (
                <span key={mode} className={`geoFeedMode ${mode}`}>
                  {FEED_NAMES[mode]} · {fleet?.feeds[mode] === "current"
                    ? fleet.vehicles.filter((item) => item.mode === mode).length
                    : fleet?.feeds[mode] ?? "loading"}
                </span>
              ))}
            </div>
          ) : sumoSnapshot ? (
            <>
              <strong>SUMO simulator locations</strong>
              <span>Geospatial context from the SUMO twin</span>
            </>
          ) : (
            <div className="simBadge" role="status">
              <strong><i aria-hidden="true" />Simulated · not live vehicles</strong>
              <span>Python simulated bus positions, moving along GRT street paths</span>
              <span>ION trains follow the real track on an illustrative timetable</span>
              <span className="simBadgeShort">Python simulator buses · ION on an illustrative timetable</span>
            </div>
          )}
      </div>
      {popupContent && showPopup && createPortal(
        <aside className="geoVehicleCard" aria-label="Selected vehicle details">
          <button type="button" aria-label="Close vehicle details" onClick={clearDetails}>×</button>
          {vehicle ? (
            <>
              <strong>Live GPS · {FEED_NAMES[vehicle.mode]} {vehicle.route}</strong>
              <span>{vehicle.headsign || `Vehicle ${vehicle.id}`}</span>
              <span>{vehicle.stop ? `${vehicle.stopStatus} · ${vehicle.stop}` : "Stop information unavailable"}</span>
              <span>
                {livePose?.terminal
                  ? "Past the end of its route shape (terminal layover) · showing the raw GPS fix"
                  : livePose?.detour
                  ? "Off its route shape (detour?) · showing the raw GPS fix"
                  : vehicle.shapeId
                    ? `On GTFS shape ${vehicle.shapeId} · ${vehicle.shapeMatch === "trip" ? "from its trip" : "nearest shape of its route"}`
                    : "No route shape · showing the raw GPS fix"}
              </span>
              <span>
                GPS fix {new Date(vehicle.timestamp * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                {livePose?.stale ? " · stale" : ""}
              </span>
              <button type="button" className="followButton" aria-pressed={follow} onClick={() => setFollow(!follow)}>
                {follow ? "Stop following" : "Follow"}
              </button>
            </>
          ) : sumoBus ? (
            <>
              <strong>SUMO bus {sumoBus.id}</strong>
              <span>{Math.round(sumoBus.speed_mps * 3.6)} km/h</span>
            </>
          ) : trainPose ? (
            <>
              <strong>Simulated ION train · {trainPose.direction === "north" ? "northbound" : "southbound"}</strong>
              <span>{trainPose.station ? `At ${trainPose.station}` : `${Math.round(trainPose.speed * 3.6)} km/h`}</span>
              <span>Illustrative 8-minute headway on the real track</span>
              <button type="button" className="followButton" aria-pressed={follow} onClick={() => setFollow(!follow)}>
                {follow ? "Stop following" : "Follow"}
              </button>
            </>
          ) : simBus && (
            <>
              <strong>Simulated bus {simBus.id}</strong>
              <span>{(simPose?.state ?? simBus.state).replaceAll("_", " ")} · {simBus.occupancy}/{simBus.capacity} aboard</span>
              {simBus.state !== "YARD" && <span>{simPose?.state === "DWELLING" ? "At stop" : "Next stop"} · {simBus.target}</span>}
              {simPose && <span>{Math.round(simPose.speed * 3.6)} km/h · {Math.round(simPose.distance)} of {Math.round(simPose.length)} m</span>}
              <button type="button" className="followButton" aria-pressed={follow} onClick={() => setFollow(!follow)}>
                {follow ? "Stop following" : "Follow"}
              </button>
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
