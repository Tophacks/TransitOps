"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { LngLatBounds, Map as MapLibreMap, NavigationControl, setWorkerUrl } from "maplibre-gl";
import { MapLibreOverlay } from "@deck.gl/maplibre";
import { PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import type { Layer } from "@deck.gl/core";

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

type PassengerDot = {
  id: string;
  position: [number, number];
};

const MAP_STYLE = "https://tiles.openfreemap.org/styles/dark";
const DEFAULT_CENTER: [number, number] = [-80.505, 43.46];

function stopPosition(stop: StopState): [number, number] {
  return [stop.lon, stop.lat];
}

function intersectionPosition(node: IntersectionState): [number, number] {
  return [node.lon, node.lat];
}

function busPosition(bus: BusState): [number, number] {
  return [bus.lon, bus.lat];
}

function passengerDots(stops: StopState[]): PassengerDot[] {
  const dots: PassengerDot[] = [];

  for (const stop of stops) {
    const count = Math.min(18, stop.sprites ?? Math.ceil((stop.queue ?? 0) / 6));

    for (let index = 0; index < count; index += 1) {
      const ring = Math.floor(index / 6) + 1;
      const angle = (index * 2.399963229728653) % (Math.PI * 2);
      const scale = 0.00014 * ring;

      dots.push({
        id: `${stop.id}-${index}`,
        position: [
          stop.lon + Math.cos(angle) * scale,
          stop.lat + Math.sin(angle) * scale * 0.72,
        ],
      });
    }
  }

  return dots;
}

export default function RegionalMap({
  network,
  snapshot,
  selected,
  onSelect,
  traffic,
  sumoSnapshot = null,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const overlayRef = useRef<MapLibreOverlay | null>(null);
  const [ready, setReady] = useState(false);
  const [mapError, setMapError] = useState("");

  const stops = snapshot?.stops ?? network.stops;
  const intersections = snapshot?.intersections ?? network.intersections;
  const buses = snapshot?.buses ?? [];
  const sumoVehicles = (sumoSnapshot?.vehicles ?? []).filter(
    (vehicle): vehicle is LiveSumoVehicle & { lon: number; lat: number } =>
      Number.isFinite(vehicle.lon) && Number.isFinite(vehicle.lat)
  );

  const routePath = useMemo<[number, number][]>(() => {
    const byId = new Map(network.stops.map((stop) => [stop.id, stop]));
    return network.route_order
      .map((id) => byId.get(id))
      .filter((stop): stop is StopState => Boolean(stop))
      .map(stopPosition);
  }, [network]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    try {
      setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

      const map = new MapLibreMap({
        container: containerRef.current,
        style: MAP_STYLE,
        center: DEFAULT_CENTER,
        zoom: 11.35,
        pitch: 24,
        bearing: -8,
        attributionControl: true,
      });

      map.addControl(
        new NavigationControl({ visualizePitch: true }),
        "top-right"
      );

      const overlay = new MapLibreOverlay({
        interleaved: false,
        layers: [],
      });

      map.addControl(overlay);
      mapRef.current = map;
      overlayRef.current = overlay;

      map.once("load", () => {
        if (network.stops.length > 0) {
          const bounds = new LngLatBounds(
            stopPosition(network.stops[0]),
            stopPosition(network.stops[0])
          );

          for (const stop of network.stops.slice(1)) {
            bounds.extend(stopPosition(stop));
          }

          map.fitBounds(bounds, {
            padding: 58,
            duration: 0,
            pitch: 24,
          });
        }

        setReady(true);
      });

      map.on("error", (event) => {
        const message = event.error?.message;
        if (message) setMapError(message);
      });
    } catch (error) {
      setMapError(error instanceof Error ? error.message : "Map initialization failed.");
    }

    return () => {
      setReady(false);

      if (overlayRef.current && mapRef.current) {
        try {
          mapRef.current.removeControl(overlayRef.current);
        } catch {
          // Map may already be disposing.
        }
      }

      overlayRef.current = null;

      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
  }, [network]);

  useEffect(() => {
    if (!ready || !overlayRef.current) return;

    const selectedId = selected?.data.id;
    const people = passengerDots(stops);

    const layers: Layer[] = [
      new PathLayer({
        id: "regional-transit-spine",
        data: routePath.length > 1 ? [{ path: routePath }] : [],
        getPath: (d: { path: [number, number][] }) => d.path,
        getColor: [71, 186, 154, 220],
        getWidth: 7,
        widthUnits: "pixels",
        jointRounded: true,
        capRounded: true,
      }),

      new ScatterplotLayer({
        id: "demand-halos",
        data: stops,
        getPosition: stopPosition,
        getRadius: (stop: StopState) =>
          Math.max(40, Math.min(430, Math.sqrt(stop.queue ?? 0) * 24)),
        radiusUnits: "meters",
        getFillColor: (stop: StopState) =>
          (stop.queue ?? 0) > 55
            ? [236, 156, 91, 78]
            : [95, 193, 220, 48],
        stroked: false,
      }),

      new ScatterplotLayer({
        id: "passenger-points",
        data: people,
        getPosition: (dot: PassengerDot) => dot.position,
        getRadius: 3.7,
        radiusUnits: "pixels",
        getFillColor: [225, 234, 238, 210],
      }),

      new ScatterplotLayer({
        id: "stations",
        data: stops,
        getPosition: stopPosition,
        getRadius: (stop: StopState) =>
          stop.id === "CENTRAL" || stop.id === "UW" ? 9 : 7,
        radiusUnits: "pixels",
        getFillColor: (stop: StopState) =>
          stop.id === selectedId
            ? [255, 255, 255, 255]
            : [7, 22, 30, 255],
        getLineColor: [117, 224, 189, 255],
        lineWidthMinPixels: 2,
        stroked: true,
        pickable: true,
        onClick: ({ object }: { object?: StopState }) => {
          if (object) onSelect({ type: "stop", data: object });
        },
      }),

      new TextLayer({
        id: "station-labels",
        data: stops,
        getPosition: stopPosition,
        getText: (stop: StopState) =>
          `${stop.name}\n${stop.queue ?? 0} waiting`,
        getColor: [235, 243, 246, 238],
        getSize: 11,
        getPixelOffset: [0, -22],
        getTextAnchor: "middle",
        getAlignmentBaseline: "bottom",
        background: true,
        getBackgroundColor: [5, 15, 22, 185],
        backgroundPadding: [4, 3],
        fontFamily: "Inter, system-ui, sans-serif",
      }),

      new ScatterplotLayer({
        id: "smart-intersections",
        data: intersections,
        getPosition: intersectionPosition,
        getRadius: 6,
        radiusUnits: "pixels",
        getFillColor: (node: IntersectionState) =>
          node.phase === "GREEN"
            ? [117, 224, 189, 245]
            : [240, 142, 142, 245],
        getLineColor: [8, 15, 20, 255],
        lineWidthMinPixels: 2,
        stroked: true,
        pickable: true,
        onClick: ({ object }: { object?: IntersectionState }) => {
          if (object) onSelect({ type: "intersection", data: object });
        },
      }),

      new ScatterplotLayer({
        id: "regional-yard",
        data: [network.yard],
        getPosition: (yard: Network["yard"]) => [yard.lon, yard.lat],
        getRadius: 9,
        radiusUnits: "pixels",
        getFillColor: [74, 92, 104, 245],
        getLineColor: [177, 194, 201, 235],
        lineWidthMinPixels: 2,
        stroked: true,
        pickable: true,
        onClick: ({ object }: { object?: Network["yard"] }) => {
          if (object) onSelect({ type: "yard", data: object });
        },
      }),

      new ScatterplotLayer({
        id: "sumo-road-traffic",
        data: sumoVehicles,
        getPosition: (vehicle: LiveSumoVehicle & { lon: number; lat: number }) => [
          vehicle.lon,
          vehicle.lat,
        ],
        getRadius: 3.6,
        radiusUnits: "pixels",
        getFillColor: (vehicle: LiveSumoVehicle) =>
          vehicle.type.toLowerCase().includes("bus")
            ? [117, 224, 189, 255]
            : [186, 198, 205, 205],
        getLineColor: [5, 14, 19, 220],
        lineWidthMinPixels: 1,
        stroked: true,
        pickable: false,
      }),

      new TextLayer({
        id: "buses",
        data: buses,
        getPosition: busPosition,
        getText: () => "🚌",
        getSize: 26,
        getTextAnchor: "middle",
        getAlignmentBaseline: "center",
        pickable: true,
        onClick: ({ object }: { object?: BusState }) => {
          if (object) onSelect({ type: "bus", data: object });
        },
      }),

      new TextLayer({
        id: "bus-labels",
        data: buses,
        getPosition: busPosition,
        getText: (bus: BusState) =>
          `${bus.id}  ${bus.occupancy}/${bus.capacity}`,
        getColor: (bus: BusState) =>
          bus.id === selectedId
            ? [117, 224, 189, 255]
            : [233, 242, 245, 245],
        getSize: 11,
        getPixelOffset: [0, 21],
        getTextAnchor: "middle",
        background: true,
        getBackgroundColor: [4, 13, 19, 200],
        backgroundPadding: [4, 2],
      }),
    ];

    overlayRef.current.setProps({ layers });
  }, [
    ready,
    routePath,
    stops,
    intersections,
    buses,
    sumoVehicles,
    selected,
    network.yard,
    onSelect,
  ]);

  return (
    <div className="mapWorld">
      <div ref={containerRef} className="mapContainer" />

      {!ready && !mapError && (
        <div className="mapLoading">
          Loading Waterloo–Kitchener street network…
        </div>
      )}

      {mapError && (
        <div className="mapError">Map renderer error: {mapError}</div>
      )}

      <div className="mapOverlayHud">
        <div>
          <span>NETWORK LOAD</span>
          <strong>{snapshot?.waiting ?? 0} waiting</strong>
        </div>
        <div>
          <span>ROAD CONDITIONS</span>
          <strong>
            {traffic >= 1.6
              ? "HEAVY"
              : traffic >= 1.15
                ? "MODERATE"
                : "LIGHT"}
          </strong>
        </div>
        <div>
          <span>TRAFFIC SOURCE</span>
          <strong>{sumoSnapshot ? `SUMO · ${sumoVehicles.length} vehicles` : "Python DES"}</strong>
        </div>
      </div>

      <div className="mapLegend">
        Real OSM-based streets · {sumoSnapshot ? "live SUMO + TraCI traffic" : "Python prototype traffic"}
      </div>
    </div>
  );
}
