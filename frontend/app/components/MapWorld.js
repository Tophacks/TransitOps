"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const MAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";

function normalizeNetwork(network) {
  if (!network) return { stops: [], intersections: [], route_order: [] };
  return network;
}

export default function MapWorld({ network, snapshot, selected, onSelect }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const overlayRef = useRef(null);
  const modulesRef = useRef(null);
  const [ready, setReady] = useState(false);
  const safeNetwork = normalizeNetwork(network);

  const stopById = useMemo(
    () => Object.fromEntries((safeNetwork.stops || []).map((stop) => [stop.id, stop])),
    [safeNetwork.stops]
  );

  useEffect(() => {
    let cancelled = false;

    async function initialize() {
      const [maplibregl, { MapLibreOverlay }, layers] = await Promise.all([
        import("maplibre-gl"),
        import("@deck.gl/maplibre"),
        import("@deck.gl/layers"),
      ]);

      if (cancelled || !containerRef.current || mapRef.current) return;

      modulesRef.current = {
        maplibregl,
        MapLibreOverlay,
        PathLayer: layers.PathLayer,
        ScatterplotLayer: layers.ScatterplotLayer,
        TextLayer: layers.TextLayer,
      };

      maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");

      const map = new maplibregl.Map({
        container: containerRef.current,
        style: MAP_STYLE,
        center: [-80.505, 43.46],
        zoom: 11.4,
        pitch: 32,
        bearing: -8,
        attributionControl: true,
      });

      map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");

      const overlay = new MapLibreOverlay({
        interleaved: false,
        layers: [],
        getTooltip: ({ object }) => {
          if (!object) return null;
          if (object._kind === "bus") {
            return {
              text: `${object.id} · ${object.occupancy}/${object.capacity} riders · next ${object.target}`,
            };
          }
          if (object._kind === "stop") {
            return { text: `${object.name} · ${object.queue ?? 0} waiting` };
          }
          if (object._kind === "intersection") {
            return { text: `${object.name} · signal ${object.phase}` };
          }
          return null;
        },
        onClick: ({ object }) => {
          if (!object || !onSelect) return;
          if (object._kind === "bus") onSelect({ type: "bus", data: object });
          if (object._kind === "stop") onSelect({ type: "stop", data: object });
          if (object._kind === "intersection") onSelect({ type: "intersection", data: object });
          if (object._kind === "yard") onSelect({ type: "yard", data: object });
        },
      });

      map.addControl(overlay);
      mapRef.current = map;
      overlayRef.current = overlay;

      map.once("load", () => {
        const geoStops = (safeNetwork.stops || []).filter(
          (stop) => Number.isFinite(stop.lon) && Number.isFinite(stop.lat)
        );
        if (geoStops.length) {
          const bounds = geoStops.reduce(
            (acc, stop) => acc.extend([stop.lon, stop.lat]),
            new maplibregl.LngLatBounds(
              [geoStops[0].lon, geoStops[0].lat],
              [geoStops[0].lon, geoStops[0].lat]
            )
          );
          map.fitBounds(bounds, { padding: 65, duration: 0, pitch: 32 });
        }
        if (!cancelled) setReady(true);
      });
    }

    initialize().catch((error) => {
      console.error("TransitOps map failed to initialize", error);
    });

    return () => {
      cancelled = true;
      setReady(false);
      if (overlayRef.current && mapRef.current) {
        try {
          mapRef.current.removeControl(overlayRef.current);
        } catch {}
      }
      overlayRef.current = null;
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!ready || !overlayRef.current || !modulesRef.current) return;

    const { PathLayer, ScatterplotLayer, TextLayer } = modulesRef.current;

    const routePath = (safeNetwork.route_order || [])
      .map((id) => stopById[id])
      .filter((stop) => stop && Number.isFinite(stop.lon) && Number.isFinite(stop.lat))
      .map((stop) => [stop.lon, stop.lat]);

    const liveStops = (safeNetwork.stops || []).map((stop) => {
      const live = snapshot?.stops?.find((item) => item.id === stop.id);
      return { ...stop, ...live, _kind: "stop" };
    });

    const liveIntersections = (safeNetwork.intersections || []).map((intersection) => {
      const live = snapshot?.intersections?.find((item) => item.id === intersection.id);
      return { ...intersection, ...live, _kind: "intersection" };
    });

    const buses = (snapshot?.buses || [])
      .filter((bus) => Number.isFinite(bus.lng) && Number.isFinite(bus.lat))
      .map((bus) => ({ ...bus, _kind: "bus" }));

    const yard = safeNetwork.yard && Number.isFinite(safeNetwork.yard.lon)
      ? [{ ...safeNetwork.yard, _kind: "yard" }]
      : [];

    const keyStopIds = new Set([
      "CONESTOGA",
      "UW",
      "WATERLOO_SQUARE",
      "CENTRAL",
      "KITCHENER_MARKET",
      "FAIRWAY",
    ]);

    const layers = [
      new PathLayer({
        id: "ion-spine",
        data: routePath.length > 1 ? [{ path: routePath }] : [],
        getPath: (d) => d.path,
        getColor: [30, 135, 120, 210],
        getWidth: 7,
        widthUnits: "pixels",
        jointRounded: true,
        capRounded: true,
        antialiasing: true,
      }),

      new ScatterplotLayer({
        id: "passenger-demand-halo",
        data: liveStops.filter((stop) => (stop.queue || 0) > 0),
        getPosition: (d) => [d.lon, d.lat],
        getRadius: (d) => Math.min(420, 55 + (d.queue || 0) * 3.2),
        radiusUnits: "meters",
        getFillColor: (d) =>
          (d.queue || 0) > 60 ? [220, 105, 85, 55] : [232, 194, 116, 42],
        stroked: false,
        pickable: false,
      }),

      new ScatterplotLayer({
        id: "stations",
        data: liveStops,
        getPosition: (d) => [d.lon, d.lat],
        getRadius: (d) => (keyStopIds.has(d.id) ? 56 : 38),
        radiusUnits: "meters",
        getFillColor: (d) =>
          selected?.data?.id === d.id ? [117, 224, 189, 255] : [235, 243, 246, 245],
        getLineColor: [8, 23, 32, 255],
        lineWidthMinPixels: 2,
        stroked: true,
        pickable: true,
        autoHighlight: true,
      }),

      new TextLayer({
        id: "station-labels",
        data: liveStops.filter((stop) => keyStopIds.has(stop.id)),
        getPosition: (d) => [d.lon, d.lat],
        getText: (d) => `${d.name}\n${d.queue ?? 0} waiting`,
        getSize: 12,
        sizeUnits: "pixels",
        getColor: [235, 243, 246, 255],
        getPixelOffset: [0, -28],
        getTextAnchor: "middle",
        getAlignmentBaseline: "bottom",
        fontFamily: "Inter, system-ui, sans-serif",
        outlineWidth: 3,
        outlineColor: [5, 14, 20, 230],
        billboard: true,
      }),

      new ScatterplotLayer({
        id: "smart-intersections",
        data: liveIntersections.filter(
          (item) => Number.isFinite(item.lon) && Number.isFinite(item.lat)
        ),
        getPosition: (d) => [d.lon, d.lat],
        getRadius: 32,
        radiusUnits: "meters",
        getFillColor: (d) =>
          d.phase === "GREEN" ? [117, 224, 189, 235] : [240, 142, 142, 235],
        getLineColor: [7, 18, 25, 255],
        lineWidthMinPixels: 2,
        stroked: true,
        pickable: true,
      }),

      new ScatterplotLayer({
        id: "fleet-buses",
        data: buses,
        getPosition: (d) => [d.lng, d.lat],
        getRadius: (d) => (selected?.data?.id === d.id ? 86 : 66),
        radiusUnits: "meters",
        getFillColor: [27, 120, 88, 255],
        getLineColor: [210, 247, 236, 255],
        lineWidthMinPixels: 2,
        stroked: true,
        pickable: true,
        autoHighlight: true,
      }),

      new TextLayer({
        id: "fleet-labels",
        data: buses,
        getPosition: (d) => [d.lng, d.lat],
        getText: (d) => `${d.id}  ${d.occupancy}/${d.capacity}`,
        getSize: 11,
        sizeUnits: "pixels",
        getColor: [225, 247, 239, 255],
        getPixelOffset: [0, 18],
        outlineWidth: 3,
        outlineColor: [5, 14, 20, 220],
        getTextAnchor: "middle",
        billboard: true,
      }),

      new ScatterplotLayer({
        id: "regional-yard",
        data: yard,
        getPosition: (d) => [d.lon, d.lat],
        getRadius: 70,
        radiusUnits: "meters",
        getFillColor: [85, 105, 116, 220],
        getLineColor: [180, 197, 204, 255],
        lineWidthMinPixels: 2,
        stroked: true,
        pickable: true,
      }),
    ];

    overlayRef.current.setProps({ layers });
  }, [ready, safeNetwork, snapshot, stopById, selected, onSelect]);

  return (
    <div className="geoWorld">
      <div ref={containerRef} className="mapCanvas" />
      <div className="mapHud">
        <div>
          <span>REGIONAL NETWORK</span>
          <strong>Waterloo ↔ Kitchener</strong>
        </div>
        <div>
          <span>LIVE QUEUE</span>
          <strong>{snapshot?.waiting ?? 0} passengers</strong>
        </div>
        <div>
          <span>MAP MODE</span>
          <strong>OpenStreetMap + simulation</strong>
        </div>
      </div>
      <div className="mapLegend">
        <span><i className="legendDot stationLegend" /> station</span>
        <span><i className="legendDot busLegend" /> simulated fleet</span>
        <span><i className="legendDot signalLegend" /> smart signal</span>
        <span><i className="legendLine" /> regional transit spine</span>
      </div>
      {!ready && <div className="mapLoading">Loading Waterloo–Kitchener map…</div>}
    </div>
  );
}
