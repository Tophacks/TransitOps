"use client";

import { useEffect, useMemo, useRef } from "react";
import { LngLatBounds, Map as MapLibreMap, NavigationControl } from "maplibre-gl";
import { MapLibreOverlay } from "@deck.gl/maplibre";
import { PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";

const STATION_GEO = {
  CONESTOGA: [-80.52954, 43.49834],
  NORTHFIELD: [-80.54321, 43.49722],
  RESEARCH_TECH: [-80.54514, 43.48144],
  UW: [-80.54128, 43.47336],
  LAURIER: [-80.53, 43.47083],
  WATERLOO_SQUARE: [-80.5228, 43.46419],
  GRAND_RIVER_HOSPITAL: [-80.51185, 43.45723],
  CENTRAL: [-80.49944, 43.45333],
  CITY_HALL: [-80.49108, 43.45203],
  KITCHENER_MARKET: [-80.48374, 43.44636],
  FAIRWAY: [-80.44186, 43.42231],
};

const SIGNAL_GEO = {
  I1: [-80.5364, 43.4978],
  I2: [-80.5432, 43.4772],
  I3: [-80.5264, 43.4675],
  I4: [-80.5056, 43.4553],
  I5: [-80.4874, 43.4492],
  I6: [-80.4535, 43.4289],
};

const SYNTHETIC_YARD = [-80.515, 43.443];

function geoForStop(stop) {
  if (Number.isFinite(stop?.lon) && Number.isFinite(stop?.lat)) {
    return [stop.lon, stop.lat];
  }
  return STATION_GEO[stop?.id] || null;
}

export default function NetworkMap({
  network,
  snapshot,
  selected,
  onSelect,
  traffic = 1,
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const overlayRef = useRef(null);

  const stops = useMemo(() => {
    const liveById = new Map((snapshot?.stops || []).map((stop) => [stop.id, stop]));
    return (network?.stops || []).map((stop) => {
      const live = liveById.get(stop.id);
      const position = geoForStop(live || stop);
      return {
        ...stop,
        ...live,
        position,
      };
    }).filter((stop) => stop.position);
  }, [network, snapshot]);

  const routePath = useMemo(() => {
    const byId = new Map(stops.map((stop) => [stop.id, stop]));
    return (network?.route_order || [])
      .map((id) => byId.get(id)?.position)
      .filter(Boolean);
  }, [network, stops]);

  const buses = useMemo(() => {
    return (snapshot?.buses || [])
      .map((bus) => ({
        ...bus,
        position:
          Number.isFinite(bus.lon) && Number.isFinite(bus.lat)
            ? [bus.lon, bus.lat]
            : null,
      }))
      .filter((bus) => bus.position);
  }, [snapshot]);

  const signals = useMemo(() => {
    return (snapshot?.intersections || []).map((signal) => ({
      ...signal,
      position:
        Number.isFinite(signal.lon) && Number.isFinite(signal.lat)
          ? [signal.lon, signal.lat]
          : SIGNAL_GEO[signal.id],
    })).filter((signal) => signal.position);
  }, [snapshot]);

  const yard = useMemo(() => ({
    id: "YARD",
    name: network?.yard?.name || "Synthetic staging yard",
    position: SYNTHETIC_YARD,
  }), [network]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new MapLibreMap({
      container: containerRef.current,
      style: "https://tiles.openfreemap.org/styles/liberty",
      center: [-80.505, 43.462],
      zoom: 11.45,
      pitch: 28,
      bearing: -9,
      antialias: true,
      attributionControl: { compact: true },
    });

    map.addControl(new NavigationControl({ visualizePitch: true }), "top-right");

    const overlay = new MapLibreOverlay({
      interleaved: true,
      layers: [],
      getCursor: ({ isHovering }) => (isHovering ? "pointer" : "grab"),
    });

    map.addControl(overlay);
    mapRef.current = map;
    overlayRef.current = overlay;

    map.once("load", () => {
      const bounds = new LngLatBounds();
      Object.values(STATION_GEO).forEach((coordinate) => bounds.extend(coordinate));
      map.fitBounds(bounds, {
        padding: { top: 70, right: 60, bottom: 70, left: 60 },
        duration: 0,
      });
    });

    return () => {
      overlay.finalize();
      map.remove();
      overlayRef.current = null;
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!overlayRef.current) return;

    const selectedId = selected?.data?.id;

    const layers = [
      new PathLayer({
        id: "ion-spine",
        data: routePath.length > 1 ? [{ path: routePath }] : [],
        getPath: (d) => d.path,
        getColor: [102, 224, 188, 220],
        getWidth: 5,
        widthUnits: "pixels",
        rounded: true,
        capRounded: true,
        jointRounded: true,
      }),

      new ScatterplotLayer({
        id: "passenger-demand",
        data: stops,
        getPosition: (d) => d.position,
        getRadius: (d) => 8 + Math.min(34, Math.sqrt(Math.max(0, d.queue || 0)) * 4),
        radiusUnits: "pixels",
        getFillColor: (d) =>
          (d.queue || 0) > 45
            ? [242, 166, 90, 95]
            : [93, 183, 221, 65],
        stroked: false,
        pickable: false,
      }),

      new ScatterplotLayer({
        id: "stations",
        data: stops,
        getPosition: (d) => d.position,
        getRadius: (d) => (d.id === "CENTRAL" || d.id === "UW" ? 8 : 6),
        radiusUnits: "pixels",
        getFillColor: [8, 21, 28, 245],
        getLineColor: (d) =>
          d.id === selectedId ? [255, 255, 255, 255] : [117, 224, 189, 255],
        lineWidthUnits: "pixels",
        getLineWidth: (d) => (d.id === selectedId ? 4 : 2),
        stroked: true,
        pickable: true,
        onClick: ({ object }) => {
          if (object) onSelect({ type: "stop", data: object });
        },
      }),

      new TextLayer({
        id: "station-labels",
        data: stops,
        getPosition: (d) => d.position,
        getText: (d) => `${d.name} · ${d.queue || 0}`,
        getColor: [236, 245, 248, 240],
        getSize: 12,
        sizeUnits: "pixels",
        getPixelOffset: [0, -17],
        getTextAnchor: "middle",
        getAlignmentBaseline: "bottom",
        fontFamily: "Inter, system-ui, sans-serif",
        outlineWidth: 3,
        outlineColor: [4, 12, 18, 220],
      }),

      new ScatterplotLayer({
        id: "signals",
        data: signals,
        getPosition: (d) => d.position,
        getRadius: 5,
        radiusUnits: "pixels",
        getFillColor: (d) =>
          d.phase === "GREEN" ? [103, 224, 181, 255] : [240, 116, 116, 255],
        getLineColor: [5, 14, 19, 255],
        lineWidthUnits: "pixels",
        getLineWidth: 2,
        stroked: true,
        pickable: true,
        onClick: ({ object }) => {
          if (object) onSelect({ type: "intersection", data: object });
        },
      }),

      new ScatterplotLayer({
        id: "yard",
        data: [yard],
        getPosition: (d) => d.position,
        getRadius: 8,
        radiusUnits: "pixels",
        getFillColor: [232, 194, 116, 230],
        getLineColor: [12, 21, 27, 255],
        getLineWidth: 2,
        lineWidthUnits: "pixels",
        stroked: true,
        pickable: true,
        onClick: ({ object }) => {
          if (object) onSelect({ type: "yard", data: object });
        },
      }),

      new TextLayer({
        id: "yard-label",
        data: [yard],
        getPosition: (d) => d.position,
        getText: () => "SYNTHETIC STAGING",
        getColor: [236, 220, 165, 230],
        getSize: 11,
        sizeUnits: "pixels",
        getPixelOffset: [0, 16],
        getTextAnchor: "middle",
        fontFamily: "Inter, system-ui, sans-serif",
        outlineWidth: 3,
        outlineColor: [4, 12, 18, 220],
      }),

      new ScatterplotLayer({
        id: "buses",
        data: buses,
        getPosition: (d) => d.position,
        getRadius: (d) => (d.id === selectedId ? 10 : 8),
        radiusUnits: "pixels",
        getFillColor: (d) =>
          d.state === "YARD"
            ? [126, 143, 151, 225]
            : [47, 196, 148, 255],
        getLineColor: [235, 250, 245, 255],
        lineWidthUnits: "pixels",
        getLineWidth: (d) => (d.id === selectedId ? 3 : 1),
        stroked: true,
        pickable: true,
        onClick: ({ object }) => {
          if (object) onSelect({ type: "bus", data: object });
        },
      }),

      new TextLayer({
        id: "bus-labels",
        data: buses,
        getPosition: (d) => d.position,
        getText: (d) => `${d.id}  ${d.occupancy}/${d.capacity}`,
        getColor: [255, 255, 255, 255],
        getSize: 11,
        sizeUnits: "pixels",
        getPixelOffset: [0, 15],
        getTextAnchor: "middle",
        fontFamily: "Inter, system-ui, sans-serif",
        outlineWidth: 3,
        outlineColor: [4, 12, 18, 225],
      }),
    ];

    overlayRef.current.setProps({ layers });
  }, [routePath, stops, buses, signals, yard, selected, onSelect, traffic]);

  return (
    <div className="realMapWorld">
      <div ref={containerRef} className="mapCanvas" />
      <div className="mapHud">
        <div>
          <span>NETWORK LOAD</span>
          <strong>{snapshot?.waiting ?? 0} waiting</strong>
        </div>
        <div>
          <span>ROAD CONDITIONS</span>
          <strong>{traffic >= 1.6 ? "HEAVY" : traffic >= 1.15 ? "MODERATE" : "LIGHT"}</strong>
        </div>
        <div>
          <span>BASE MAP</span>
          <strong>OpenStreetMap / OpenFreeMap</strong>
        </div>
      </div>
      <div className="mapLegend">
        <span><i className="legendLine" /> ION corridor context</span>
        <span><i className="legendBus" /> simulated bus</span>
        <span><i className="legendDemand" /> passenger demand</span>
        <span>experimental operations · real geography</span>
      </div>
    </div>
  );
}
