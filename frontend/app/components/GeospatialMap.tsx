"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { GeoJSONSource, Map as MapInstance, Popup } from "maplibre-gl";

import type { Network, Snapshot, Selection, LiveSumoSnapshot, SimulationEvent, StopState } from "../../lib/types";
import type { GrtMode, GrtShapes, GrtVehicle } from "../../lib/grtTypes";
import { useGrtFleet } from "../../lib/useGrtFleet";
import { ION_DEPOT, ION_DEPOT_BOUNDARY } from "../../lib/geo/ion-depot";
import type { PlaybackClock } from "../../lib/motion/clock.ts";
import { busPoses, type BusPose } from "../../lib/motion/playback.ts";
import { GARAGES, garagePoses, type GarageVehicle } from "../../lib/motion/garages.ts";
import { LINES, linePoses, sparePoses, type FleetVehicle, type LineVehicle } from "../../lib/motion/lines.ts";
import { UW, UW_ROADS, berthStates, carPoses, inStation, onStationRoad, roadPoint, stationClamp, uwPoses, type UwCar, type UwVehicle } from "../../lib/motion/uwStation.ts";
import { ION_LENGTH, IonTimetable, articulatePath, type IonSchedule, type TrainPose } from "../../lib/motion/ion.ts";
import { busBody, carBody, emptyBodies, gateBodies, lighting, trainBody, type BodyLayers } from "../../lib/motion/bodies.ts";
import { CAR_LENGTH, CAR_WIDTH, TrafficStream, queuedCars, type CarPose } from "../../lib/motion/traffic.ts";
import { gateStates } from "../../lib/motion/gates.ts";
import { LiveTracker, type LivePing, type LivePose } from "../../lib/motion/live.ts";
import { encodePath, offsetLonLat, type PathPoint } from "../../lib/motion/path.ts";
import { corridor } from "../../lib/motion/scene.ts";
import { runningRails } from "../../lib/motion/railGeometry.ts";
import DecisionInspector from "./DecisionInspector";
import { NETWORK, TERMINALS } from "../../lib/motion/railScene.ts";
import { lerpPath as lerpAlong, type LonLat } from "../../lib/motion/path.ts";
import type { RailDecision, RailMetrics } from "../../lib/motion/ion.ts";

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
  /** ION timetable from the Python simulator (null before a run) */
  ionSchedule?: IonSchedule | null;
  /** the run's traffic multiplier, for the simulated background cars */
  trafficMultiplier?: number;
  /** rail service of the holds-only baseline run, for the Decision Inspector */
  ionBaselineMetrics?: RailMetrics | null;
  /** playback time for the panels (seconds; the map itself reads the clock) */
  seconds?: number;
  /** pause and jump the playback (Decision Inspector) */
  onSeek?: (seconds: number) => void;
  /** bus dispatch decisions (connection holds), shown with the rail decisions */
  busDecisions?: RailDecision[];
  /** every vehicle on UW Station's roadway, from the simulator (uw_station.py) */
  uwVehicles?: UwVehicle[];
  /** private cars on Phillip Street at the UW exit, from the simulator (road_traffic.py) */
  uwCars?: UwCar[];
  /** GRT garage pull-outs and returns, from the simulator (garage_ops.py) */
  garageVehicles?: GarageVehicle[];
  /** buses on the scheduled lines (lines.py) and the regional fleet's spares (fleet.py) */
  lineVehicles?: LineVehicle[];
  fleetVehicles?: FleetVehicle[];
  /** a fleet vehicle picked in the Fleet panel or tapped on the map */
  focusVehicle?: string | null;
  onFocusVehicle?: (id: string | null) => void;
  /** start a simulation run when the map asks for one (the Fairway button with nothing running) */
  onRequestRun?: () => void;
  /** the run's seed, shown on the simulation badge */
  seed?: number | null;
};

type Camera = { center: [number, number]; zoom: number; pitch: number; bearing: number };

const MAP_STYLE = "https://tiles.openfreemap.org/styles/dark";
const SIMULATION_SOURCES = ["route", "stops", "intersections", "buses", "traffic", "yard", "vehicle-solids", "vehicle-ground"];
const SIMULATION_LAYERS = [
  "terminal-routes", "terminal-signals", "ion-closure",
  "route-stroke", "stops-circle", "stops-label", "signals-circle", "yard-circle", "sumo-traffic",
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
// Camera moves reset any padding left by a Decision Inspector framing.
const NO_PAD = { top: 0, bottom: 0, left: 0, right: 0 };

/** Close-up over the Phillip St / University Ave corner and the UW Station bus loop. */
function campusCamera(width: number): Camera {
  const narrow = width < 640;
  // On a phone the overlay covers the top of the map: look further along the
  // bus loop so UW Station and the run toward Phillip St stay in view.
  return narrow
    ? { center: [-80.5393, 43.47425], zoom: 17.7, pitch: 60, bearing: 35 }
    : { center: [-80.539, 43.4743], zoom: 18.3, pitch: 62, bearing: 20 };
}

/** The whole ION line, Conestoga to Fairway: across a wide screen, up a phone. */
function ionLineCamera(width: number): Camera {
  return width < 640
    ? { center: [-80.4935, 43.4705], zoom: 10.75, pitch: 0, bearing: -38 }
    : { center: [-80.4855, 43.4598], zoom: 12.25, pitch: 0, bearing: 52 };
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

function depotFeatures(): GeoJSON.FeatureCollection {
  const { centre, id, name } = ION_DEPOT;
  return featureCollection([
    point(...centre, { id, name }),
    {
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [ION_DEPOT_BOUNDARY] },
      properties: { id, name },
    },
  ]);
}

// GRT route colours for the context lines (one per route, both directions).
const ROUTE_COLORS: Record<string, string> = { "7": "#e7a35a", "31": "#c58df2", "201": "#5fb7f5", "202": "#f07b8c" };

/** GRT routes through campus, Laurier and Uptown (GTFS shapes, simplified). */
function contextRoutes(): GeoJSON.FeatureCollection {
  return featureCollection(corridor.data.routes.map((route) => line(route.path.map(([lon, lat]) => [lon, lat]), {
    id: `${route.route}:${route.direction}`,
    route: route.route,
    name: `${route.route} ${route.name}`,
    color: ROUTE_COLORS[route.route] ?? "#9fb3c8",
  })));
}

/** The part of a path between distances a and b, as an encoded path. */
function slicePath(path: PathPoint[], a: number, b: number): PathPoint[] {
  const pts: LonLat[] = [lerpAlong(path, a)];
  for (const p of path) if (p[2] > a && p[2] < b) pts.push([p[0], p[1]]);
  pts.push(lerpAlong(path, b));
  return encodePath(pts);
}

/**
 * The GTFS line paths between the terminal handovers: inside each terminal
 * the trains run on the mapped trackwork instead (terminalTrack).
 */
function linePaths() {
  const out = {} as Record<"north" | "south", PathPoint[]>;
  for (const direction of ["north", "south"] as const) {
    const path = corridor.data.ion[direction].path;
    let a = 0;
    let b = path[path.length - 1][2];
    for (const t of Object.values(TERMINALS.terminals)) {
      for (const route of Object.values(t.routes)) {
        if (route.line.direction !== direction) continue;
        if (route.kind === "depart") a = Math.max(a, route.line.handover_m);
        else b = Math.min(b, route.line.handover_m);
      }
    }
    out[direction] = slicePath(path, a, b);
  }
  return out;
}

/** Dutton yard (OMSF) track: the yard part of every storage-to-lead route. */
function yardPaths(): PathPoint[][] {
  return NETWORK.yard.routes.filter((r) => r.kind === "out").map((r) => slicePath(r.path, 0, r.yard_m + 2));
}

function yardFeatures(): GeoJSON.FeatureCollection {
  const y = NETWORK.yard;
  return featureCollection([
    ...(["building", "wash"] as const).map((k) => line(y.outline[k].map(([lon, lat]) => [lon, lat]), { kind: k })),
    ...y.tracks.map((t) => point(t.lon, t.lat, { kind: t.kind, label: `${t.positions} × LRV` })),
    ...(["north", "south"] as const).map((k) => point(y.leads[k].lon, y.leads[k].lat, { kind: "lead", label: `${k} lead` })),
  ]);
}

function ionTracks(): GeoJSON.FeatureCollection {
  const paths = linePaths();
  return featureCollection([
    ...(["north", "south"] as const).map((direction) => line(paths[direction].map(([lon, lat]) => [lon, lat]), { id: direction })),
    ...terminalPaths().map((p, i) => line(p.map(([lon, lat]) => [lon, lat]), { id: `terminal:${i}` })),
    ...NETWORK.crossovers.map((c) => line(c.path.map(([lon, lat]) => [lon, lat]), { id: `crossover:${c.way}` })),
    ...yardPaths().map((p, i) => line(p.map(([lon, lat]) => [lon, lat]), { id: `yard:${i}` })),
  ]);
}

function ionRunningRails(): GeoJSON.FeatureCollection {
  const paths = linePaths();
  return featureCollection([
    ...(["north", "south"] as const).flatMap((direction) =>
      runningRails(paths[direction]).map((track, side) => line(track, { id: `${direction}:${side}` }))),
    ...terminalPaths().flatMap((p, i) => runningRails(p).map((track, side) => line(track, { id: `terminal:${i}:${side}` }))),
    ...NETWORK.crossovers.flatMap((c) => runningRails(c.path).map((track, side) => line(track, { id: `crossover:${c.way}:${side}` }))),
    ...yardPaths().flatMap((p, i) => runningRails(p).map((track, side) => line(track, { id: `yard:${i}:${side}` }))),
  ]);
}

/** Crossover markers: enabled ones amber, the disabled one (south of Mill) red. */
function crossoverPoints(): GeoJSON.FeatureCollection {
  return featureCollection(NETWORK.crossovers.flatMap((c) => c.ends.map((e) => point(e.lon, e.lat, {
    kind: c.passenger ? "switch" : "disabled", terminal: "line", label: "", site: c.site,
  }))));
}

/** The closed stretch and the diversion trains use, for the run being played. */
function closureFeatures(schedule: IonSchedule | null): GeoJSON.FeatureCollection {
  const out: GeoJSON.Feature[] = [];
  for (const c of schedule?.closures ?? []) {
    const dir = c.track as "north" | "south";
    const track = corridor.data.ion[dir];
    const at = (name: string) => track.stations.find((st) => st.name === name)?.at_m ?? 0;
    const [a, b] = [at(c.from_station), at(c.to_station)].sort((x, y) => x - y);
    out.push(line(slicePath(track.path, a, b).map(([lon, lat]) => [lon, lat]), { kind: "closed", id: c.id }));
    const route = c.diversion ? schedule?.routes?.[c.diversion] : undefined;
    if (route) out.push(line(route.path.map(([lon, lat]) => [lon, lat]), { kind: "diversion", id: c.id }));
  }
  return featureCollection(out);
}

/** Terminal trackwork (OSM): every edge, and each move's join from the handover onto it. */
function terminalPaths(): PathPoint[][] {
  const out: PathPoint[][] = [];
  for (const t of Object.values(TERMINALS.terminals)) {
    for (const edge of Object.values(t.edges)) out.push(edge.path);
    const arrive = t.routes[`arrive:${t.preference[0]}`];
    const depart = t.routes[`depart:${t.preference[0]}`];
    out.push(slicePath(arrive.path, arrive.line.line_part_m ?? 0, arrive.switch_m));
    out.push(slicePath(depart.path, depart.switch_m, depart.line.line_start_m ?? depart.length_m));
  }
  return out;
}

/** Switches, diamonds, track ends and home signals, for the terminal markers. */
function terminalPoints(): GeoJSON.FeatureCollection {
  const out: GeoJSON.Feature[] = [];
  for (const [key, t] of Object.entries(TERMINALS.terminals)) {
    for (const sw of t.switches) out.push(point(sw.lon, sw.lat, { kind: "switch", terminal: key, label: "" }));
    out.push(point(t.diamond.lon, t.diamond.lat, { kind: "diamond", terminal: key, label: "" }));
    for (const end of t.track_ends) out.push(point(end.lon, end.lat, { kind: "buffer", terminal: key, label: `Platform ${end.platform}` }));
  }
  return featureCollection(out);
}

function homeSignal(key: string) {
  const t = TERMINALS.terminals[key as "fairway"];
  const route = t.routes[`arrive:${t.preference[0]}`];
  return lerpAlong(route.path, route.home_signal_m ?? 0);
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

  for (const id of [...SIMULATION_SOURCES, "grt-vehicles", "live-solids", "live-ground", "grt-routes"]) {
    map.addSource(id, { type: "geojson", data: featureCollection([]) });
  }
  map.addSource("ion-depot", {
    type: "geojson",
    data: depotFeatures(),
    attribution: 'Yard boundary © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
  });
  map.addLayer({
    id: "ion-depot-boundary", type: "line", source: "ion-depot", minzoom: 15,
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "line-color": "#e6bdff", "line-width": 2, "line-dasharray": [3, 2] },
  }, firstLabel);
  setData(map, "route", streetRoutes());
  map.addSource("ion-track", { type: "geojson", data: ionTracks() });
  map.addSource("ion-running-rails", { type: "geojson", data: ionRunningRails() });
  setData(map, "grt-routes", contextRoutes());
  // Both directions share one GTFS line on most streets: draw them as one.
  map.addLayer({
    id: "grt-routes-line", type: "line", source: "grt-routes",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": ["get", "color"],
      "line-width": ["interpolate", ["exponential", 2], ["zoom"], 12, 1.2, 19, 7],
      "line-opacity": ["interpolate", ["linear"], ["zoom"], 12, 0.75, 15, 0.6, 17, 0.3],
    },
  }, firstLabel);
  map.addLayer({
    id: "ion-track-line", type: "line", source: "ion-track",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": ["interpolate", ["linear"], ["zoom"], 13, "#5fa8e6", 16, "#7d8f9b"],
      "line-width": ["interpolate", ["exponential", 2], ["zoom"], 11, 2.2, 14, 2.6, 19, 9],
      "line-opacity": ["interpolate", ["linear"], ["zoom"], 13, 0.85, 16, 0.55, 17.5, 0],
    },
  }, firstLabel);
  map.addLayer({
    id: "route-stroke", type: "line", source: "route",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#76e0bd", "line-width": ["interpolate", ["exponential", 2], ["zoom"], 12, 2.5, 19, 20], "line-opacity": ["interpolate", ["linear"], ["zoom"], 14, 0.9, 17, 0.28] },
  }, firstLabel);
  // Street detail is on the ground beneath both live and simulated vehicles.
  // A subdued dashed stroke suggests ties once they can be read at this scale.
  map.addLayer({
    id: "ion-track-bed", type: "line", source: "ion-track", minzoom: 16,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#242c33",
      "line-width": ["interpolate", ["exponential", 2], ["zoom"], 16, 2.5, 18, 8.8, 19.5, 24],
      "line-opacity": ["interpolate", ["linear"], ["zoom"], 16, 0, 17.5, 0.94],
    },
  }, firstLabel);
  map.addLayer({
    id: "ion-track-ties", type: "line", source: "ion-track", minzoom: 17.5,
    layout: { "line-cap": "butt", "line-join": "round" },
    paint: {
      "line-color": "#6c6660",
      "line-width": ["interpolate", ["exponential", 2], ["zoom"], 17.5, 5, 18, 6.9, 19.5, 19],
      "line-opacity": ["interpolate", ["linear"], ["zoom"], 17.5, 0, 18, 0.68, 19.5, 0.85],
      "line-dasharray": [0.16, 0.5],
    },
  }, firstLabel);
  map.addLayer({
    id: "ion-rail-heads", type: "line", source: "ion-running-rails", minzoom: 16,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#cdd5db",
      "line-width": ["interpolate", ["exponential", 2], ["zoom"], 16, 0.7, 18, 1.05, 19.5, 1.8],
      "line-opacity": ["interpolate", ["linear"], ["zoom"], 16, 0, 17.5, 0.98],
    },
  }, firstLabel);
  // Terminal interlocking: routes in use (granted moves) and the markers.
  map.addSource("terminal-routes", { type: "geojson", data: featureCollection([]) });
  map.addSource("terminal-points", {
    type: "geojson", data: terminalPoints(),
    attribution: 'ION terminal trackwork © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
  });
  map.addSource("terminal-signals", { type: "geojson", data: featureCollection([]) });
  map.addSource("ion-closure", { type: "geojson", data: featureCollection([]) });
  map.addSource("ion-crossover-points", { type: "geojson", data: crossoverPoints() });
  // Scheduled lines (GTFS route variants with more than a handful of trips) and their stops.
  map.addSource("sched-lines", {
    type: "geojson", data: lineFeatures(),
    attribution: "Scheduled lines: GRT GTFS (Region of Waterloo Open Data Licence)",
  });
  map.addLayer({
    id: "sched-lines-route", type: "line", source: "sched-lines", filter: ["==", ["geometry-type"], "LineString"], minzoom: 11,
    paint: { "line-color": "#f0a04b", "line-width": ["interpolate", ["linear"], ["zoom"], 11, 1.2, 16, 3], "line-opacity": 0.55 },
  }, firstLabel);
  map.addLayer({
    id: "sched-lines-stops", type: "circle", source: "sched-lines", filter: ["==", ["geometry-type"], "Point"], minzoom: 14,
    paint: { "circle-radius": ["match", ["get", "kind"], "station", 4.5, 2.6], "circle-color": "#f0a04b",
      "circle-stroke-color": "#11161c", "circle-stroke-width": 1, "circle-pitch-alignment": "map" },
  });
  map.addSource("bus-garages", {
    type: "geojson", data: garageFeatures(),
    attribution: 'GRT garages and roads © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>; blocks: GRT GTFS',
  });
  map.addLayer({
    id: "bus-garage-outline", type: "line", source: "bus-garages", filter: ["==", ["geometry-type"], "LineString"], minzoom: 13,
    paint: { "line-color": "#9fb6ff", "line-width": 1.4, "line-opacity": 0.75, "line-dasharray": ["literal", [2, 1.5]] },
  }, firstLabel);
  map.addLayer({
    id: "bus-garage-points", type: "circle", source: "bus-garages", filter: ["==", ["geometry-type"], "Point"], minzoom: 14,
    paint: {
      "circle-radius": ["match", ["get", "kind"], "park", 5, 3.5],
      "circle-color": ["match", ["get", "kind"], "park", "#9fb6ff", "#ffcf5a"],
      "circle-stroke-color": "#11161c", "circle-stroke-width": 1.2, "circle-pitch-alignment": "map",
    },
  });
  map.addSource("uw-berths", {
    type: "geojson", data: featureCollection([]),
    attribution: 'UW Station platforms © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>; stops: GRT GTFS',
  });
  map.addLayer({
    id: "uw-berths", type: "line", source: "uw-berths", minzoom: 16,
    paint: {
      "line-color": ["match", ["get", "state"], "blocked", "#ff5a4e", "occupied", "#ffcf5a", "#59e3a7"],
      "line-width": 1.6, "line-opacity": 0.85,
      "line-dasharray": ["match", ["get", "state"], "free", ["literal", [2, 1.5]], ["literal", [1, 0]]],
    },
  }, firstLabel);
  map.addSource("dutton-yard", {
    type: "geojson", data: yardFeatures(),
    attribution: 'Dutton yard (OMSF) © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
  });
  map.addLayer({
    id: "dutton-yard-outline", type: "line", source: "dutton-yard", filter: ["==", ["geometry-type"], "LineString"], minzoom: 14,
    paint: { "line-color": "#8fa3b5", "line-width": 1.2, "line-opacity": 0.7, "line-dasharray": ["literal", [2, 1.5]] },
  }, firstLabel);
  map.addLayer({
    id: "dutton-yard-points", type: "circle", source: "dutton-yard", filter: ["==", ["geometry-type"], "Point"], minzoom: 15,
    paint: {
      "circle-radius": 3.5,
      "circle-color": ["match", ["get", "kind"], "lead", "#ffcf5a", "shop", "#c08cff", "#6fd3ff"],
      "circle-stroke-color": "#11161c", "circle-stroke-width": 1.2, "circle-pitch-alignment": "map",
    },
  });
  map.addLayer({
    id: "ion-closure", type: "line", source: "ion-closure",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": ["match", ["get", "kind"], "closed", "#ff4b3e", "#ffb21e"],
      "line-width": ["interpolate", ["exponential", 2], ["zoom"], 12, ["match", ["get", "kind"], "closed", 5, 2.5], 19, 26],
      "line-opacity": ["match", ["get", "kind"], "closed", 0.8, 0.45],
      "line-dasharray": ["match", ["get", "kind"], "closed", ["literal", [1.2, 0.8]], ["literal", [1, 0]]],
    },
  }, firstLabel);
  map.addLayer({
    id: "ion-crossover-points", type: "circle", source: "ion-crossover-points", minzoom: 14,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 2.5, 18, 5],
      "circle-color": ["match", ["get", "kind"], "disabled", "#ff5a4e", "#ffcf5a"],
      "circle-stroke-color": "#11161c", "circle-stroke-width": 1.5, "circle-pitch-alignment": "map",
    },
  });
  map.addLayer({
    id: "terminal-routes", type: "line", source: "terminal-routes", minzoom: 15,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": ["match", ["get", "kind"], "arrive", "#4fd1b0", "#f2b544"],
      "line-width": ["interpolate", ["exponential", 2], ["zoom"], 15, 3, 19.5, 30],
      "line-opacity": 0.34,
    },
  }, firstLabel);
  map.addLayer({
    id: "terminal-points", type: "circle", source: "terminal-points", minzoom: 16,
    paint: {
      "circle-radius": ["match", ["get", "kind"], "diamond", 3, 4],
      "circle-color": ["match", ["get", "kind"], "switch", "#ffcf5a", "buffer", "#ff5a4e", "#f2f2f2"],
      "circle-stroke-color": "#11161c", "circle-stroke-width": 1.5, "circle-pitch-alignment": "map",
    },
  });
  map.addLayer({
    id: "terminal-signals", type: "circle", source: "terminal-signals", minzoom: 15,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 15, 4, 19, 8],
      "circle-color": ["match", ["get", "aspect"], "stop", "#ff4b3e", "clear", "#4be08a", "#8a96a0"],
      "circle-stroke-color": "#0b1014", "circle-stroke-width": 2, "circle-pitch-alignment": "map",
    },
  });
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
  map.addLayer({
    id: "ion-depot-marker", type: "circle", source: "ion-depot", minzoom: 12,
    filter: ["==", ["geometry-type"], "Point"],
    paint: {
      "circle-color": "#e6bdff", "circle-radius": 7,
      "circle-stroke-color": "#1d1830", "circle-stroke-width": 3,
    },
  });
  if (map.getStyle()?.glyphs) {
    map.addLayer({
      id: "ion-depot-label", type: "symbol", source: "ion-depot", minzoom: 14,
      filter: ["==", ["geometry-type"], "Point"],
      layout: {
        "text-field": ["get", "name"], "text-font": ["Noto Sans Regular"],
        "text-size": 13, "text-anchor": "top", "text-offset": [0, 0.75],
      },
      paint: { "text-color": "#f2dfff", "text-halo-color": "#20152c", "text-halo-width": 2 },
    });
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
      id: "grt-routes-label",
      type: "symbol",
      source: "grt-routes",
      minzoom: 14,
      filter: ["==", ["get", "id"], ["concat", ["get", "route"], ":0"]],
      layout: {
        "symbol-placement": "line",
        "symbol-spacing": 420,
        "text-field": ["get", "name"],
        "text-font": ["Noto Sans Regular"],
        "text-size": 11,
        "text-optional": true,
      },
      paint: { "text-color": ["get", "color"], "text-halo-color": "#0b1520", "text-halo-width": 1.6 },
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

const UW_CLAMP = stationClamp(corridor);

/** Corridor buses: inside UW Station the station replay places them. */
function sceneBuses(timeline: Snapshot[], vehicles: readonly UwVehicle[], t: number): BusPose[] {
  // A spare still out of service from its garage is drawn on its own road route (lines.ts).
  const frame = timeline[Math.min(Math.max(0, timeline.length - 1), Math.floor(t / 60) + 1)];
  const deadheading = new Set((frame?.buses ?? []).filter((b) => (b.segment as { deadhead?: boolean } | undefined)?.deadhead).map((b) => b.id));
  const out = busPoses(corridor, timeline, t, UW_CLAMP).filter((bus) => !inStation(vehicles, bus.id, t) && !deadheading.has(bus.id));
  for (const pose of uwPoses(vehicles, t)) {
    if (pose.kind !== "corridor") continue;
    out.push({ id: pose.id, state: pose.speed > 0.2 ? "EN_ROUTE" : "DWELLING", from: "CENTRAL", to: "UW", distance: 0, length: 0,
      speed: pose.speed, centre: pose.centre, heading: pose.heading, queueCars: 0, holdAt: null, holdMinutes: 0 });
  }
  // A bus waiting at the entrance before the station lets it in.
  for (const v of vehicles) {
    if (v.kind === "corridor" && t >= v.t_enter && v.trace.length && t < v.trace[0][0] && !out.some((b) => b.id === v.id)) {
      const { point, heading } = roadPoint(-UW.bus.length_m / 2, UW.lanes.through_offset_m);
      out.push({ id: v.id, state: "EN_ROUTE", from: "CENTRAL", to: "UW", distance: 0, length: 0, speed: 0, centre: point,
        heading, queueCars: 0, holdAt: null, holdMinutes: 0 });
    }
  }
  return out;
}

/** Is a point on the modelled stretch of Phillip Street (where the simulator's cars run)? */
function onPhillip(p: [number, number]) {
  const path = UW_ROADS.phillip.path;
  for (let i = 0; i + 1 < path.length; i += 1) {
    const k = 111320 * Math.cos((p[1] * Math.PI) / 180);
    const ax = (path[i][0] - p[0]) * k, ay = (path[i][1] - p[1]) * 111200;
    const bx = (path[i + 1][0] - p[0]) * k, by = (path[i + 1][1] - p[1]) * 111200;
    const dx = bx - ax, dy = by - ay;
    const len = dx * dx + dy * dy;
    const f = len ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len)) : 0;
    if (Math.hypot(ax + dx * f, ay + dy * f) < 9) return true;
  }
  return false;
}

/** The scheduled lines' main route variants and stops (lines.json, from GTFS). */
function lineFeatures(): GeoJSON.FeatureCollection {
  const out: GeoJSON.Feature[] = [];
  for (const route of Object.values(LINES.routes)) {
    for (const p of route.patterns) {
      if (p.trips < 10) continue;
      out.push(line(p.path.map(([lon, lat]) => [lon, lat]), { kind: "route", route: route.id, pattern: p.id }));
    }
  }
  for (const [id, st] of Object.entries(LINES.stops)) {
    out.push(point(st.lon, st.lat, { kind: st.ion || /Station/.test(st.name) ? "station" : "stop", id, name: st.name }));
  }
  return featureCollection(out);
}

/** GRT garages: site outlines (OSM), driveways and where buses stand. */
function garageFeatures(): GeoJSON.FeatureCollection {
  return featureCollection(GARAGES.flatMap((g) => [
    line(g.outline.map(([lon, lat]) => [lon, lat]), { kind: "site", id: g.id, name: g.name }),
    point(g.park[0], g.park[1], { kind: "park", id: g.id, name: g.name }),
    ...g.driveways.map((d) => point(d.lon, d.lat, { kind: "driveway", id: g.id, road: d.road })),
  ]));
}

/** UW Station berth outlines, coloured by who holds them at t. */
function uwBerthFeatures(vehicles: readonly UwVehicle[], t: number): GeoJSON.FeatureCollection {
  const held = berthStates(vehicles, t);
  const out: GeoJSON.Feature[] = [];
  for (const platform of UW.platforms) {
    const sign = platform.side === "se" ? 1 : -1;
    const off = UW.lanes.berth_offset_m * sign;
    for (const b of platform.berths) {
      const lo = Math.min(b.front, b.rear);
      const hi = Math.max(b.front, b.rear);
      const ring = [
        roadPoint(lo, off - 1.5 * sign).point, roadPoint(hi, off - 1.5 * sign).point,
        roadPoint(hi, off + 1.5 * sign).point, roadPoint(lo, off + 1.5 * sign).point,
      ];
      const h = held.get(b.id);
      out.push({ type: "Feature", geometry: { type: "Polygon", coordinates: [[...ring, ring[0]]] },
        properties: { id: b.id, stop: b.stop, state: h ? (h.kind === "oos" ? "blocked" : "occupied") : "free", by: h?.by ?? "" } });
    }
  }
  return featureCollection(out);
}

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
  ionSchedule = null,
  trafficMultiplier = 1,
  ionBaselineMetrics = null,
  seconds = 0,
  onSeek,
  busDecisions = [],
  uwVehicles = [],
  uwCars = [],
  garageVehicles = [],
  lineVehicles = [],
  fleetVehicles = [],
  focusVehicle = null,
  onFocusVehicle,
  onRequestRun,
  seed = null,
}: Props) {
  const lineRef = useRef<LineVehicle[]>([]);
  lineRef.current = lineVehicles;
  const simFleetRef = useRef<FleetVehicle[]>([]);
  simFleetRef.current = fleetVehicles;
  const focusRef = useRef<string | null>(null);
  focusRef.current = focusVehicle;
  const onFocusRef = useRef(onFocusVehicle);
  onFocusRef.current = onFocusVehicle;
  const garageRef = useRef<GarageVehicle[]>([]);
  garageRef.current = garageVehicles;
  const uwRef = useRef<UwVehicle[]>([]);
  uwRef.current = uwVehicles;
  const uwCarsRef = useRef<UwCar[]>([]);
  uwCarsRef.current = uwCars;
  // Same timetable and trip model as backend/ion.py: trains sit exactly where
  // the simulator put them at every minute.
  const timetable = useMemo(
    () => (ionSchedule ? new IonTimetable(corridor.data.ion, ionSchedule, TERMINALS) : null),
    [ionSchedule],
  );
  const timetableRef = useRef(timetable);
  const busDecisionsRef = useRef<RailDecision[]>([]);
  timetableRef.current = timetable;
  // Simulated background cars for the whole run (computed once per run).
  const traffic = useMemo(() => {
    if (timeline.length < 2) return null;
    return new TrafficStream(corridor, timeline, {
      traffic: trafficMultiplier,
      phaseAt: (signalId, minute) =>
        timeline[Math.min(timeline.length - 1, minute)]?.intersections.find((i) => i.id === signalId)?.phase ?? "GREEN",
    });
  }, [timeline, trafficMultiplier]);
  const trafficRef = useRef(traffic);
  trafficRef.current = traffic;
  const [night, setNight] = useState(false);
  const sumoHeadings = useRef(new Map<string, { lon: number; lat: number; heading: number }>());
  const frameCosts = useRef<number[]>([]);
  const nightRef = useRef(night);
  nightRef.current = night;
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
  const selectedTrainRef = useRef<string | null>(null);
  selectedTrainRef.current = selectedTrain;
  // The Fleet panel picks a vehicle: the inspector follows it.
  useEffect(() => {
    if (focusVehicle) setSelectedTrain(focusVehicle);
  }, [focusVehicle]);
  const [follow, setFollow] = useState(false);
  const cameraAfterMode = useRef<((width: number) => Camera) | null>(null);
  const [depotSelected, setDepotSelected] = useState(false);
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
  const trainPose = simulating && selectedTrain && timetable
    ? timetable.trains(now).find((train) => train.id === selectedTrain) : undefined;
  const trainState = trainPose ? snapshot?.trains?.find((train) => train.id === trainPose.id) : undefined;
  busDecisionsRef.current = busDecisions;
  const allDecisions = useMemo(
    () => [...(ionSchedule?.decisions ?? []), ...busDecisions].sort((a, b) => a.t - b.t || a.id.localeCompare(b.id)),
    [ionSchedule, busDecisions],
  );
  const latestTrainDecision = trainPose && timetable
    ? timetable.decisionsFor(trainPose.id).filter((d) => d.t <= seconds + 0.5).at(-1) : undefined;
  const focus = vehicle ?? sumoBus;
  const lon = depotSelected ? ION_DEPOT.centre[0] : focus?.lon;
  const lat = depotSelected ? ION_DEPOT.centre[1] : focus?.lat;
  const livePose = vehicle ? trackerRef.current.pose(fleetKey(vehicle.mode, vehicle.id), performance.now() / 1000) : undefined;
  const animatedFocus = depotSelected ? null
    : simPose ? simPose.id : trainPose ? trainPose.id : vehicle ? fleetKey(vehicle.mode, vehicle.id) : null;
  const callbacks = useRef({ network, snapshot, onSelect, sumoSnapshot, timeline });
  callbacks.current = { network, snapshot, onSelect, sumoSnapshot, timeline };
  const sceneRef = useRef<Scene>({ buses: [], trains: [] });
  const sceneState = useRef({ animatedFocus, follow, selectedBus: simBus?.id ?? null, simulating });
  sceneState.current = { animatedFocus, follow, selectedBus: simBus?.id ?? null, simulating };

  function selectDepot() {
    setFollow(false);
    setSelectedVehicle(null);
    setSelectedTrain(null);
    setSelectedSumo(null);
    callbacks.current.onSelect(null);
    setDepotSelected(true);
  }

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
            // The ION run being played: physical trains, plans and decisions.
            ion: () => timetableRef.current?.schedule ?? null,
            busDecisions: () => busDecisionsRef.current,
            garages: (t?: number) => garagePoses(garageRef.current, t ?? clock.time()),
            uw: (t?: number) => uwPoses(uwRef.current, t ?? clock.time()),
            uwCars: (t?: number) => carPoses(uwCarsRef.current, t ?? clock.time()),
            uwPlans: () => uwRef.current.map((v) => ({ id: v.id, kind: v.kind })),
            lines: (t?: number) => linePoses(lineRef.current, t ?? clock.time()),
            spares: (t?: number) => sparePoses(simFleetRef.current, t ?? clock.time(), new Set(lineRef.current.map((v) => v.vehicle_id))),
            inspecting: () => selectedTrainRef.current,
            // Milliseconds spent building each simulation frame (scene + bodies + setData).
            frameCosts: frameCosts.current,
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
            "grt-points", "grt-route-labels", "live-bodies", "ion-depot-marker", "ion-depot-label",
          ].filter((id) => currentMap.getLayer(id));
          currentMap.on("click", clickable, (event) => {
            const feature = event.features?.[0];
            const id = feature?.properties?.id;
            if (typeof id !== "string") return;
            if (id === ION_DEPOT.id) {
              selectDepot();
              return;
            }
            setDepotSelected(false);
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
                } else if (timetableRef.current?.schedule.vehicles.some((v) => v.id === id)) {
                  setSelectedTrain(id);
                  // The inspector shows the simulator's view of this train.
                  const train = current.snapshot?.trains?.find((item) => item.id === id);
                  current.onSelect(train ? { type: "train", data: train } : null);
                } else {
                  const data = current.snapshot?.buses.find((item) => item.id === id);
                  if (data) {
                    setSelectedTrain(null);
                    current.onSelect({ type: "bus", data });
                    onFocusRef.current?.(id);
                  } else {
                    // A line bus, a spare or a UW Station GTFS bus: the inspector shows its decisions.
                    const vehicle = id.replace(/-\d+$/, "");
                    setSelectedTrain(vehicle);
                    current.onSelect(null);
                    onFocusRef.current?.(vehicle);
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
    // Entering the simulation: the campus, unless a button asked for somewhere else (Fairway).
    const target = cameraAfterMode.current ?? campusCamera;
    cameraAfterMode.current = null;
    if (!live) map.easeTo({ ...target(map.getContainer().clientWidth), padding: NO_PAD, duration: 1600 });
    else if (map.getZoom() > 16) map.easeTo({ ...REGION_CAMERA, ...STREET_CAMERA, padding: NO_PAD, duration: 1200 });
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
      // SUMO's cars as 3D bodies, facing the way they last moved.
      const bodies = emptyBodies();
      lighting.night = nightRef.current;
      const seen = sumoHeadings.current;
      for (const item of traffic) {
        const last = seen.get(item.id);
        let heading = last?.heading ?? 0;
        if (last) {
          const dx = (item.lon - last.lon) * Math.cos((item.lat * Math.PI) / 180);
          const dy = item.lat - last.lat;
          if (Math.hypot(dx, dy) > 2e-6) heading = ((Math.atan2(dx, dy) * 180) / Math.PI + 360) % 360;
        }
        seen.set(item.id, { lon: item.lon, lat: item.lat, heading });
        carBody({ id: item.id, centre: [item.lon, item.lat], heading, speed: item.speed_mps, opacity: 1, kind: "traffic", segment: "", s: 0 },
          bodies, CAR_LENGTH, CAR_WIDTH);
      }
      setData(map, "vehicle-solids", featureCollection(bodies.solids));
      setData(map, "vehicle-ground", featureCollection(bodies.ground));
    }
  }, [network, snapshot, sumoSnapshot, live, ready]);

  // Animation loop: every frame derives the scene from the playback clock.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || live || sumoSnapshot) return;
    let frame = 0;
    let lastKey = "";
    let lastBerthSecond = -1;
    const draw = () => {
      const t = clock.time();
      const state = sceneState.current;
      // Redraw twice a second even when paused, so crossing lights flash.
      const flash = Math.floor(performance.now() / 500);
      const key = `${t}|${state.selectedBus}|${state.animatedFocus}|${state.follow}|${nightRef.current}|${flash}|${map.getZoom() >= BODY_MIN_ZOOM}`;
      if (key !== lastKey) {
        lastKey = key;
        const started = performance.now();
        const scene: Scene = {
          buses: sceneBuses(callbacks.current.timeline, uwRef.current, t),
          trains: timetableRef.current?.trains(t) ?? [],
        };
        sceneRef.current = scene;
        const bodies = emptyBodies();
        lighting.night = nightRef.current;
        for (const bus of scene.buses) busBody(bus, bus.id === state.selectedBus, bodies);
        // GRT garage buses (GTFS blocks, simulated) on their pull-outs and returns.
        for (const bus of garagePoses(garageRef.current, t)) {
          busBody({ id: bus.id, centre: bus.centre, heading: bus.heading }, false, bodies, "stale");
        }
        // Other GRT buses at UW Station (GTFS schedule, simulated) and any blocking bus.
        for (const other of uwPoses(uwRef.current, t)) {
          if (other.kind !== "corridor") busBody({ id: other.id, centre: other.centre, heading: other.heading }, other.id === focusRef.current, bodies, "stale");
        }
        // Scheduled lines (Route 7 and the others modelled), driven by their own drivers,
        // and spares on their way out of a garage.
        const onLine = new Set(lineRef.current.map((v) => v.vehicle_id));
        for (const bus of linePoses(lineRef.current, t)) {
          busBody({ id: bus.id, centre: bus.centre, heading: bus.heading }, bus.id === focusRef.current, bodies,
            bus.state === "broken" ? "stale" : "live");
        }
        for (const bus of sparePoses(simFleetRef.current, t, onLine)) {
          busBody({ id: bus.id, centre: bus.centre, heading: bus.heading }, bus.id === focusRef.current, bodies, "live");
        }
        const second = Math.floor(t);
        if (second !== lastBerthSecond) {
          lastBerthSecond = second;
          setData(map, "uw-berths", uwBerthFeatures(uwRef.current, t));
        }
        for (const train of scene.trains) trainBody(train, bodies);
        // Station control on the map: granted routes ahead of moving trains,
        // and each home signal at stop while a train waits for a route.
        const routes: GeoJSON.Feature[] = [];
        const aspects: Record<string, string> = { fairway: "stop", conestoga: "stop" };
        for (const train of scene.trains) {
          if (!train.terminal || train.terminal === "network" || !train.route || train.s === undefined) continue;
          const route = TERMINALS.terminals[train.terminal as "fairway"].routes[train.route];
          if (train.moveEnd !== undefined) {
            const from = Math.max(train.s - ION_LENGTH, route.kind === "arrive" ? (route.home_signal_m ?? 0) - 20 : 0);
            const to = route.kind === "arrive" ? train.moveEnd : Math.min(train.moveEnd, route.line.line_start_m ?? route.length_m);
            if (to > from + 1) routes.push(line(slicePath(route.path, from, to).map(([lon, lat]) => [lon, lat]), { kind: route.kind }));
            if (route.kind === "arrive" && train.moveEnd > (route.home_signal_m ?? 0) && train.s < (route.home_signal_m ?? 0)) {
              aspects[train.terminal] = "clear";
            }
          }
        }
        setData(map, "terminal-routes", featureCollection(routes));
        setData(map, "terminal-signals", featureCollection(Object.entries(aspects).map(([key, aspect]) => {
          const [lon, lat] = homeSignal(key);
          return point(lon, lat, { aspect, terminal: key });
        })));
        // Simulated cars and crossing gates: street zoom only, and only in view.
        if (map.getZoom() >= BODY_MIN_ZOOM) {
          const bounds = map.getBounds();
          const pad = 0.002;
          const inView = ([lon, lat]: [number, number]) =>
            lon > bounds.getWest() - pad && lon < bounds.getEast() + pad && lat > bounds.getSouth() - pad && lat < bounds.getNorth() + pad;
          const cars: CarPose[] = [...(trafficRef.current?.cars(t) ?? []), ...queuedCars(corridor, scene.buses)];
          // Transit Plaza Road (UW Station) is bus-only: no simulated cars on it. On
          // Phillip Street at the UW exit the simulator's own cars are drawn instead.
          const phillipSim = uwCarsRef.current.length > 0;
          for (const car of cars) {
            if (!inView(car.centre) || onStationRoad(car.centre)) continue;
            if (phillipSim && onPhillip(car.centre)) continue;
            carBody(car, bodies, CAR_LENGTH, CAR_WIDTH);
          }
          for (const car of carPoses(uwCarsRef.current, t)) {
            if (inView(car.centre)) carBody({ ...car, opacity: 1, kind: "traffic" } as CarPose, bodies, CAR_LENGTH, CAR_WIDTH);
          }
          for (const gate of gateStates(corridor.data.ion_crossings ?? [], scene.trains)) {
            if (inView([gate.crossing.lon, gate.crossing.lat])) gateBodies(gate, performance.now() / 1000, bodies);
          }
        }
        setData(map, "vehicle-solids", featureCollection(bodies.solids));
        setData(map, "vehicle-ground", featureCollection(bodies.ground));
        setData(map, "buses", featureCollection(scene.buses.map((bus) => point(bus.centre[0], bus.centre[1], { id: bus.id }))));
        const costs = frameCosts.current;
        costs.push(performance.now() - started);
        if (costs.length > 600) costs.splice(0, costs.length - 600);
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
    setDepotSelected(false);
    setFollow(false);
    setSelectedTrain(null);
    if (live) setSelectedVehicle(null);
    else {
      setSelectedSumo(null);
      onSelect(null);
    }
  }

  // Night: darker buildings, and lit windows, headlights and tail-lights on
  // every vehicle body (lighting.night is read when bodies are built).
  useEffect(() => {
    lighting.night = night;
    const map = mapRef.current;
    if (!map || !ready || !map.getLayer("corridor-buildings")) return;
    map.setPaintProperty("corridor-buildings", "fill-extrusion-color", night ? "#252f3b" : "#445363");
    if (map.getLayer("background")) {
      map.setPaintProperty("background", "background-color", night ? "#05080c" : "rgb(12,12,12)");
    }
  }, [night, ready]);

  // The run's closures: the closed stretch and the diversion path.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    setData(map, "ion-closure", closureFeatures(ionSchedule));
  }, [ionSchedule, ready]);

  /** Fairway: the running simulation at Fairway Station (ION terminal, Route 7 terminus).
   * With nothing running yet, it starts a run. */
  function openFairway() {
    clearDetails();
    const t = TERMINALS.terminals.fairway;
    const route = t.routes[`arrive:${t.preference[0]}`];
    const [lon, lat] = lerpAlong(route.path, route.switch_m + 45);
    const camera = (width: number): Camera => ({ center: [lon, lat], zoom: width < 640 ? 16.4 : 17.1, pitch: 50, bearing: -13 });
    if (!callbacks.current.timeline.length) onRequestRun?.();
    if (live) {
      cameraAfterMode.current = camera;      // the mode switch frames Fairway
      onModeChange(false);
    } else {
      goTo(camera);
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

  /** Decision Inspector: show the moment and place a decision explains. */
  function showDecision(decision: RailDecision) {
    onSeek?.(decision.t);
    const map = mapRef.current;
    const t = TERMINALS.terminals[decision.terminal as "fairway"];
    if (decision.vehicle_id && timetable?.schedule.vehicles.some((v) => v.id === decision.vehicle_id)) {
      setSelectedTrain(decision.vehicle_id);
      onSelect(null);
    }
    if (map && decision.location) {
      // A bus decision at a stop: frame the stop.
      setFollow(false);
      const narrow = map.getContainer().clientWidth < 640;
      map.easeTo({
        center: decision.location, zoom: narrow ? 17 : 17.6, pitch: 50,
        padding: narrow ? { top: 90, bottom: Math.round(map.getContainer().clientHeight * 0.5), left: 0, right: 0 } : { top: 60, bottom: 40, left: 0, right: 380 },
        duration: 1200,
      });
      const bus = callbacks.current.snapshot?.buses.find((b) => b.id === decision.vehicle_id);
      if (bus) {
        setSelectedTrain(null);
        onSelect({ type: "bus", data: bus });
      }
      return;
    }
    if (map && !t && timetable) {
      // A decision on the line: frame the train where it stood.
      const train = timetable.trains(decision.t).find((x) => x.id === decision.vehicle_id);
      if (train) {
        setFollow(false);
        const narrow = map.getContainer().clientWidth < 640;
        map.easeTo({
          center: train.sections[2].centre, zoom: narrow ? 16.4 : 17.2, pitch: 50, bearing: train.sections[2].heading - 90,
          padding: narrow ? { top: 90, bottom: Math.round(map.getContainer().clientHeight * 0.5), left: 0, right: 0 } : { top: 60, bottom: 40, left: 0, right: 380 },
          duration: 1200,
        });
      }
      return;
    }
    if (map && t) {
      setFollow(false);
      const narrow = map.getContainer().clientWidth < 640;
      const route = t.routes[`arrive:${t.preference[0]}`];
      // Between the crossover and the track ends; the inspector covers the
      // right of the map on a desktop and the bottom on a phone.
      const [lon, lat] = lerpAlong(route.path, route.switch_m + 45);
      map.easeTo({
        center: [lon, lat], zoom: narrow ? 16.9 : 17.6, pitch: 50, bearing: decision.terminal === "fairway" ? -13 : 72,
        padding: narrow
          ? { top: 90, bottom: Math.round(map.getContainer().clientHeight * 0.5), left: 0, right: 0 }
          : { top: 60, bottom: 40, left: 0, right: 380 },
        duration: 1200,
      });
    }
  }

  function goTo(camera: (width: number) => Camera) {
    const map = mapRef.current;
    if (!map) return;
    setFollow(false);
    map.easeTo({ ...camera(map.getContainer().clientWidth), padding: NO_PAD, duration: 1400 });
  }

  const showPopup = Boolean(depotSelected || focus || simPose || trainPose);

  return (
    <section className="geoMapShell" aria-label="Waterloo corridor geographic basemap">
      <div ref={host} className="geoMapHost" role="region" aria-label="Interactive Waterloo corridor map" />
      <div className="geoMapOverlay">
        <div className="geoMapControls" role="group" aria-label="Map layers">
          <button type="button" aria-pressed={tilted} disabled={!ready} onClick={toggleTilt}>3D</button>
          <button type="button" aria-pressed={night} disabled={!ready} onClick={() => setNight(!night)}>Night</button>
          <button type="button" disabled={!ready} onClick={() => goTo(campusCamera)}>Campus</button>
          {!live && <button type="button" disabled={!ready} onClick={() => goTo(ionCurveCamera)}>ION curve</button>}
          {!live && <button type="button" disabled={!ready} onClick={() => goTo(ionLineCamera)}>ION line</button>}
          <button type="button" disabled={!ready} onClick={() => {
            goTo((width) => ({
              center: ION_DEPOT.centre, zoom: width < 640 ? 15.8 : 16.6,
              pitch: tilted ? 45 : 0, bearing: 0,
            }));
            selectDepot();
          }}>ION depot</button>
          <button type="button" disabled={!ready} onClick={() => mapRef.current?.easeTo({ ...REGION_CAMERA, ...STREET_CAMERA, padding: NO_PAD })}>Region</button>
          <button type="button" disabled={!ready} onClick={openFairway}>Fairway</button>
          <button type="button" aria-pressed={live} onClick={() => {
            clearDetails();
            onModeChange(true);
          }}>Live GPS</button>
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
              <strong><i aria-hidden="true" />Simulated{seed !== null ? ` · seed ${seed}` : ""} · not live GPS</strong>
              <span>Python simulated bus positions, moving along GRT street paths</span>
              <span>ION trains from the same Python simulator, on the real track</span>
              <span>Background cars and ION crossing gates are simulated too{night ? " · night lighting is illustrative" : ""}</span>
              <span className="simBadgeShort">Python simulator · buses, ION, cars and gates{night ? " · night" : ""}</span>
            </div>
          )}
      </div>
      {simulating && ionSchedule?.decisions && (
        <DecisionInspector
          decisions={allDecisions}
          now={seconds}
          vehicle={selectedTrain}
          onPick={showDecision}
          hold={ionSchedule.platform_hold}
          metrics={ionBaselineMetrics ? { adaptive: ionSchedule.metrics, baseline: ionBaselineMetrics } : null}
        />
      )}
      {popupContent && showPopup && createPortal(
        <aside className="geoVehicleCard" aria-label={depotSelected ? "ION depot details" : "Selected vehicle details"}>
          <button type="button" aria-label={depotSelected ? "Close depot details" : "Close vehicle details"} onClick={clearDetails}>×</button>
          {depotSelected ? (
            <>
              <strong>ION Operations, Maintenance and Storage Facility</strong>
              <span>{ION_DEPOT.address}</span>
              <span>Light rail maintenance and train storage · no passenger boarding</span>
            </>
          ) : vehicle ? (
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
              <strong>Simulated ION train {trainPose.id}{trainPose.tripId
                ? (trainPose.terminal && trainPose.station ? ` · arrived on ${trainPose.tripId}` : ` · trip ${trainPose.tripId}`) : ""}</strong>
              <span>{trainPose.direction === "north" ? "Northbound" : "Southbound"} · {trainPose.station ? `at ${trainPose.station}` : `${Math.round(trainPose.speed * 3.6)} km/h`}</span>
              {trainPose.activity && <span>{trainPose.activity}</span>}
              {(trainPose.delayMin ?? 0) > 0 && <span>Left {Math.round(trainPose.delayMin ?? 0)} min late · {trainPose.hold}</span>}
              {!trainPose.station && trainPose.nextStation && <span>Next · {trainPose.nextStation}</span>}
              {trainState && Object.keys(trainState.transfers_ahead).length > 0 && (
                <span>Transferring to the bus · {Object.entries(trainState.transfers_ahead)
                  .map(([stop, riders]) => `${riders} at ${stop === "UW" ? "UW" : "Central"}`).join(", ")}</span>
              )}
              {latestTrainDecision && (
                <div className="geoDecision">
                  <strong>{latestTrainDecision.trigger}</strong>
                  <span>Chose · {latestTrainDecision.chosen}</span>
                </div>
              )}
              <span>Python simulator · same train ID through every turnback</span>
              <button type="button" className="followButton" aria-pressed={follow} onClick={() => setFollow(!follow)}>
                {follow ? "Stop following" : "Follow"}
              </button>
            </>          ) : simBus && (
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
