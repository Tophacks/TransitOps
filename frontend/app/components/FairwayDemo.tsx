"use client";

// Fairway terminal replay: a modelled ION turnback and GRT bus layover on the
// real trackwork and bus lane, with its own clock and controls. Mounted by
// GeospatialMap over the same map; it hides the live and simulation layers
// while it runs and puts them back on exit.

import { useEffect, useMemo, useRef, useState } from "react";
import type { GeoJSONSource, Map as MapInstance, MapLayerMouseEvent } from "maplibre-gl";

import fairwayData from "../../lib/geo/fairway.json";
import { PlaybackClock } from "../../lib/motion/clock.ts";
import { busBody, emptyBodies, lighting, trainBody } from "../../lib/motion/bodies.ts";
import {
  FAIRWAY_BUS_ID, FAIRWAY_TRAIN_ID, FairwayReplay, type FairwayData, type FairwayScene, type NextTrip,
} from "../../lib/motion/fairway.ts";
import { lerpPath, type LonLat, type PathPoint } from "../../lib/motion/path.ts";
import { runningRails } from "../../lib/motion/railGeometry.ts";

const DATA = fairwayData as unknown as FairwayData;
const SPEEDS = [1, 2, 4];
const SOURCES = ["fairway-track", "fairway-rails", "fairway-route", "fairway-points", "fairway-solids", "fairway-ground", "fairway-signs"];

type Props = { map: MapInstance; onExit: () => void };

/**
 * Dusk framing: the crossover, both platforms, bay 3490 and the bus's whole
 * pull-out in one view, looking north across the tracks (they run left to
 * right), clear of the replay's panels.
 */
export function fairwayCamera(width: number) {
  const phone = width < 640;
  return phone
    ? { center: [-80.44176, 43.42238] as [number, number], zoom: 16.75, pitch: 48, bearing: -13,
      padding: { top: 70, bottom: 230, left: 0, right: 0 } }
    : { center: [-80.44178, 43.42232] as [number, number], zoom: 18.3, pitch: 55, bearing: -13,
      padding: { top: 60, bottom: 130, left: 0, right: 0 } };
}

const NO_PADDING = { top: 0, bottom: 0, left: 0, right: 0 };

function collection(features: GeoJSON.Feature[]): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features };
}

function lineFeature(coords: LonLat[] | PathPoint[], properties: Record<string, string> = {}): GeoJSON.Feature {
  return { type: "Feature", properties, geometry: { type: "LineString", coordinates: coords.map((p) => [p[0], p[1]]) } };
}

function pointFeature(p: LonLat, properties: Record<string, string | number>): GeoJSON.Feature {
  return { type: "Feature", properties, geometry: { type: "Point", coordinates: [p[0], p[1]] } };
}

function staticLayers() {
  const { edges, switches, diamond, buffers } = DATA.track;
  const track = Object.entries(edges).map(([name, edge]) => lineFeature(edge.path, { name }));
  const rails = Object.entries(edges).flatMap(([name, edge]) => runningRails(edge.path).map((rail) => lineFeature(rail, { name })));
  const mid = (path: PathPoint[]) => lerpPath(path, path[path.length - 1][2] / 2);
  const stop = DATA.bus.layover.stop;
  const points = [
    ...switches.map((s) => pointFeature([s.lon, s.lat], { kind: "switch", label: "" })),
    pointFeature([diamond.lon, diamond.lat], { kind: "diamond", label: "Scissors crossover" }),
    ...buffers.map((b) => pointFeature([b.lon, b.lat], { kind: "buffer", label: "" })),
    pointFeature(mid(edges.platform_1.path), { kind: "label", label: "Platform 1" }),
    pointFeature(mid(edges.platform_2.path), { kind: "label", label: "Platform 2" }),
    pointFeature([stop.lon, stop.lat], { kind: "bay", label: `Bay ${stop.id} · route ${DATA.bus.layover.route}` }),
  ];
  return { track: collection(track), rails: collection(rails), points: collection(points) };
}

function setData(map: MapInstance, id: string, data: GeoJSON.FeatureCollection) {
  (map.getSource(id) as GeoJSONSource | undefined)?.setData(data);
}

function addLayers(map: MapInstance) {
  const fixed = staticLayers();
  map.addSource("fairway-track", { type: "geojson", data: fixed.track, attribution: "Fairway trackwork and bus lane © OpenStreetMap contributors" });
  map.addSource("fairway-rails", { type: "geojson", data: fixed.rails });
  map.addSource("fairway-points", { type: "geojson", data: fixed.points });
  for (const id of ["fairway-route", "fairway-solids", "fairway-ground", "fairway-signs"]) {
    map.addSource(id, { type: "geojson", data: collection([]) });
  }
  const width = (a: number, b: number) => ["interpolate", ["exponential", 2], ["zoom"], 15, a, 19.5, b];
  map.addLayer({
    id: "fairway-route", type: "line", source: "fairway-route",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#36c9a6", "line-opacity": 0.32, "line-width": width(3, 34) as never },
  });
  map.addLayer({
    id: "fairway-bed", type: "line", source: "fairway-track",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#2b2a2a", "line-width": width(1.6, 22) as never, "line-opacity": 0.95 },
  });
  map.addLayer({
    id: "fairway-ties", type: "line", source: "fairway-track", minzoom: 17,
    paint: { "line-color": "#6c6660", "line-width": width(1.2, 17) as never, "line-opacity": 0.7, "line-dasharray": [0.16, 0.5] },
  });
  map.addLayer({
    id: "fairway-rail-heads", type: "line", source: "fairway-rails",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#d6dde2", "line-width": ["interpolate", ["exponential", 2], ["zoom"], 15, 0.5, 19.5, 1.9] },
  });
  map.addLayer({
    id: "fairway-points-circle", type: "circle", source: "fairway-points",
    filter: ["in", ["get", "kind"], ["literal", ["switch", "diamond", "buffer", "bay"]]],
    paint: {
      "circle-radius": ["match", ["get", "kind"], "diamond", 3, "bay", 5, 4],
      "circle-color": ["match", ["get", "kind"], "switch", "#ffcf5a", "buffer", "#ff5a4e", "bay", "#83bcff", "#f2f2f2"],
      "circle-stroke-color": "#11161c", "circle-stroke-width": 1.5,
      "circle-pitch-alignment": "map",
    },
  });
  map.addLayer({
    id: "fairway-ground", type: "fill", source: "fairway-ground",
    paint: { "fill-color": ["get", "color"], "fill-opacity": ["get", "opacity"], "fill-antialias": true },
  });
  map.addLayer({
    id: "fairway-bodies", type: "fill-extrusion", source: "fairway-solids",
    paint: {
      "fill-extrusion-color": ["get", "color"],
      "fill-extrusion-base": ["get", "base"],
      "fill-extrusion-height": ["get", "height"],
      "fill-extrusion-opacity": 1,
      "fill-extrusion-vertical-gradient": true,
    },
  });
  if (!map.getStyle()?.glyphs) return;
  map.addLayer({
    id: "fairway-labels", type: "symbol", source: "fairway-points", minzoom: 16.8,
    filter: ["!=", ["get", "label"], ""],
    layout: {
      "text-field": ["get", "label"], "text-font": ["Noto Sans Regular"], "text-size": 11.5,
      "text-anchor": "top", "text-offset": [0, 0.7], "text-optional": true,
    },
    paint: { "text-color": "#cfe3ee", "text-halo-color": "#0b141c", "text-halo-width": 1.8 },
  });
  // Destination signs: LED amber on black, upright on screen so they read on
  // a phone. Route and short destination at street zoom, route alone wider.
  // (Data-driven layout steps are evaluated at whole tile zooms, so the
  // threshold is an integer.)
  map.addLayer({
    id: "fairway-signs", type: "symbol", source: "fairway-signs",
    layout: {
      "text-field": ["step", ["zoom"], ["get", "short"], 16, ["get", "full"]],
      "text-font": ["Noto Sans Bold", "Noto Sans Regular"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 15, 12, 18, 15, 19.5, 17],
      "text-anchor": "bottom", "text-offset": [0, -0.9],
      "text-allow-overlap": true, "text-ignore-placement": true,
      "text-rotation-alignment": "viewport", "text-pitch-alignment": "viewport",
      "text-letter-spacing": 0.04,
    },
    paint: {
      "text-color": ["get", "color"], "text-halo-color": "#050505", "text-halo-width": 3.2,
      "text-opacity": ["get", "opacity"],
    },
  });
}

function removeLayers(map: MapInstance) {
  const style = map.getStyle();
  for (const layer of style?.layers ?? []) if (layer.id.startsWith("fairway-")) map.removeLayer(layer.id);
  for (const id of SOURCES) if (map.getSource(id)) map.removeSource(id);
}

function signFeatures(scene: FairwayScene) {
  const { bus, train } = scene;
  const out: GeoJSON.Feature[] = [];
  if (bus.opacity > 0) {
    const nis = !bus.sign.route;
    out.push(pointFeature(bus.front, {
      id: bus.id,
      full: nis ? bus.sign.short : `${bus.sign.route}  ${bus.sign.short}`,
      short: nis ? "NIS" : bus.sign.route,
      color: "#ffb21e",
      opacity: bus.opacity,
    }));
  }
  if (train.headlights !== null && train.headlights !== undefined) {
    const cab = train.sections[train.headlights];
    const tip = train.headlights === 0 ? cab.front : cab.rear;
    // The sign at the arrival end reads Fairway; once the operator has
    // changed ends, the new leading cab shows the next trip.
    const dest = train.headlights === 0 ? "Fairway" : "Conestoga";
    out.push(pointFeature(tip, { id: train.id, full: `301  ${dest}`, short: "301", color: "#86f2cf", opacity: 1 }));
  }
  return collection(out);
}

function clockText(seconds: number) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export default function FairwayDemo({ map, onExit }: Props) {
  const [next, setNext] = useState<NextTrip>("route");
  const replay = useMemo(() => new FairwayReplay(DATA, next), [next]);
  const clock = useMemo(() => new PlaybackClock(replay.duration), [replay]);
  const [selected, setSelected] = useState<string>(FAIRWAY_BUS_ID);
  const [view, setView] = useState<FairwayScene>(() => replay.scene(0));
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(2);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  // Mount: hide the live and simulation layers, add the terminal, frame it.
  useEffect(() => {
    const hidden: [string, "visible" | "none"][] = [];
    for (const layer of map.getStyle()?.layers ?? []) {
      const source = (layer as { source?: string }).source;
      if (!source || source === "openmaptiles" || layer.id.startsWith("fairway-")) continue;
      const visibility = (map.getLayoutProperty(layer.id, "visibility") as "visible" | "none" | undefined) ?? "visible";
      hidden.push([layer.id, visibility]);
      map.setLayoutProperty(layer.id, "visibility", "none");
    }
    addLayers(map);
    const before = { center: map.getCenter(), zoom: map.getZoom(), pitch: map.getPitch(), bearing: map.getBearing() };
    map.easeTo({ ...fairwayCamera(map.getContainer().clientWidth), duration: 1600 });
    const click = (event: MapLayerMouseEvent) => {
      const id = event.features?.[0]?.properties?.id;
      if (id === FAIRWAY_BUS_ID || id === FAIRWAY_TRAIN_ID) setSelected(id);
    };
    const pointer = () => { map.getCanvas().style.cursor = "pointer"; };
    const plain = () => { map.getCanvas().style.cursor = ""; };
    map.on("click", "fairway-bodies", click);
    map.on("mouseenter", "fairway-bodies", pointer);
    map.on("mouseleave", "fairway-bodies", plain);
    const keys = (event: KeyboardEvent) => { if (event.key === "Escape") onExit(); };
    window.addEventListener("keydown", keys);
    return () => {
      window.removeEventListener("keydown", keys);
      map.off("click", "fairway-bodies", click);
      map.off("mouseenter", "fairway-bodies", pointer);
      map.off("mouseleave", "fairway-bodies", plain);
      if (!map.getStyle()) return; // map already removed
      removeLayers(map);
      for (const [id, visibility] of hidden) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visibility);
      map.easeTo({ ...before, padding: NO_PADDING, duration: 1200 });
    };
  }, [map, onExit]);

  // A new replay (next trip changed): start it from the top.
  useEffect(() => {
    clock.setSpeed(speed);
    clock.seek(0);
    clock.play();
    setPlaying(true);
    // speed is applied by its own handler; only a new clock restarts here
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clock]);

  // Frame loop: the scene is a pure function of the replay clock.
  useEffect(() => {
    let frame = 0;
    let lastKey = "";
    let lastUi = 0;
    let uiKey = "";
    let current: FairwayScene | null = null;
    const handle = window as unknown as { __transitops?: Record<string, unknown> };
    const draw = () => {
      const t = clock.time();
      const key = `${t}|${selectedRef.current}`;
      if (key !== lastKey) {
        lastKey = key;
        const scene = replay.scene(t);
        const bodies = emptyBodies();
        const night = lighting.night;
        lighting.night = true; // dusk: headlights, tail lights and lit windows
        trainBody(scene.train, bodies);
        busBody({ id: scene.bus.id, centre: scene.bus.centre, heading: scene.bus.heading },
          selectedRef.current === FAIRWAY_BUS_ID, bodies, "live", scene.bus.opacity);
        lighting.night = night;
        setData(map, "fairway-solids", collection(bodies.solids));
        setData(map, "fairway-ground", collection(bodies.ground));
        setData(map, "fairway-signs", signFeatures(scene));
        const move = DATA.turnback[scene.train.move];
        setData(map, "fairway-route", collection([lineFeature(move.path, { move: scene.train.move })]));
        if (handle.__transitops) handle.__transitops.fairway = { replay, clock, scene };
        current = scene;
      }
      // The panels follow at a few frames a second, and always catch up
      // with the last frame (after a pause or a seek).
      const now = performance.now();
      if (current && uiKey !== lastKey && now - lastUi > 150) {
        lastUi = now;
        uiKey = lastKey;
        setView(current);
        setPlaying(clock.playing);
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      if (handle.__transitops) delete handle.__transitops.fairway;
    };
  }, [clock, map, replay]);

  function togglePlay() {
    if (clock.playing) clock.pause();
    else clock.play();
    setPlaying(clock.playing);
  }

  function replayFromStart() {
    clock.seek(0);
    clock.play();
    setPlaying(true);
  }

  function changeSpeed(value: number) {
    clock.setSpeed(value);
    setSpeed(value);
  }

  const { train, bus } = view;
  const layover = DATA.bus.layover;
  const signText = (s: typeof bus.sign) => (s.route ? `${s.route} ${s.destination}` : s.destination);

  return (
    <div className="fairwayDemo">
      <aside className="fairwayCard" aria-label="Selected vehicle details">
        <div className="fairwayCardTabs" role="group" aria-label="Choose vehicle">
          <button type="button" aria-pressed={selected === FAIRWAY_BUS_ID} onClick={() => setSelected(FAIRWAY_BUS_ID)}>Bus</button>
          <button type="button" aria-pressed={selected === FAIRWAY_TRAIN_ID} onClick={() => setSelected(FAIRWAY_TRAIN_ID)}>Train</button>
        </div>
        {selected === FAIRWAY_BUS_ID ? (
          <>
            <strong>GRT route {layover.route} bus · modeled</strong>
            <span className="fairwayState">{bus.phase === "layover" ? "Layover" : bus.sign.route ? "In service" : "Not in Service"}</span>
            <span>{bus.phase === "layover"
              ? `Bay ${layover.stop.id}, ${layover.stop.name} · leaves in ${clockText(bus.leavesIn)}`
              : `${Math.round(bus.speed * 3.6)} km/h along the one-way bus lane`}</span>
            <span>Sign · {signText(bus.sign)}{bus.phase === "layover" && bus.sign !== bus.nextSign ? ` · next ${signText(bus.nextSign)}` : ""}</span>
            <small>GTFS: {layover.layovers_counted} scheduled route {layover.route} layovers at this bay, median {layover.median_layover_min} min. Shortened for the replay.</small>
          </>
        ) : (
          <>
            <strong>ION train · modeled turnback</strong>
            <span className="fairwayState">{train.phase === "arriving" ? "Arriving" : train.phase === "stopped" ? "Stopped · platform 1" : "Departing northbound"}</span>
            <span>{train.phase === "stopped"
              ? (train.headlights === null ? "Operator changing ends" : `Leading cab ${train.headlights === 0 ? "A" : "B"}`)
              : `${Math.round(train.speed * 3.6)} km/h · ${train.edge.replaceAll("_", " ")}`}</span>
            <span className="fairwayExtra">{train.move === "arrival" ? "Straight in on the south track to platform 1" : "Out through crossover 754590785 to the north track"}</span>
            <small>Same train and carbodies throughout; cab {train.headlights === null ? "change in progress" : train.headlights === 0 ? "A" : "B"} lit.</small>
          </>
        )}
      </aside>
      <div className="fairwayBar" role="group" aria-label="Fairway replay controls">
        <div className="fairwayBadge" role="status">
          <strong><i aria-hidden="true" />Fairway replay · modeled operations</strong>
          <span>Real trackwork and bus lane (OpenStreetMap), GRT route {layover.route} layover (GTFS). Not live vehicles, not the simulation.</span>
        </div>
        <p className="fairwayCaption" aria-live="polite">{view.caption}</p>
        <div className="fairwayControls">
          <button type="button" onClick={togglePlay}>{playing ? "Pause" : "Play"}</button>
          <button type="button" onClick={replayFromStart}>Replay</button>
          <input
            type="range" min={0} max={replay.duration} step={0.1} value={view.t.toFixed(1)}
            aria-label="Fairway replay timeline"
            onChange={(event) => {
              clock.seek(Number(event.target.value));
              setView(replay.scene(clock.time()));
            }}
          />
          <span className="fairwayTime">{clockText(view.t)} / {clockText(replay.duration)}</span>
          <div className="fairwaySpeeds" role="group" aria-label="Replay speed">
            {SPEEDS.map((value) => (
              <button key={value} type="button" aria-pressed={speed === value} onClick={() => changeSpeed(value)}>{value}×</button>
            ))}
          </div>
          <div className="fairwaySpeeds" role="group" aria-label="Bus next trip">
            <button type="button" aria-pressed={next === "route"} onClick={() => setNext("route")}>Next: {layover.route}</button>
            <button type="button" aria-pressed={next === "garage"} onClick={() => setNext("garage")}>Garage</button>
          </div>
          <button type="button" className="fairwayExit" onClick={onExit}>Exit replay</button>
        </div>
      </div>
    </div>
  );
}
