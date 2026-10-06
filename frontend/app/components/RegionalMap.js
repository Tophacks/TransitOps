"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const GEO_FALLBACK = {
  CONESTOGA: [-80.52954, 43.49834],
  NORTHFIELD: [-80.54321, 43.49722],
  RESEARCH_TECH: [-80.54514, 43.48144],
  UW: [-80.54128, 43.47336],
  LAURIER: [-80.53467, 43.46901],
  WATERLOO_SQUARE: [-80.52280, 43.46419],
  GRAND_RIVER_HOSPITAL: [-80.51185, 43.45723],
  CENTRAL: [-80.49944, 43.45333],
  CITY_HALL: [-80.49108, 43.45203],
  KITCHENER_MARKET: [-80.48374, 43.44636],
  FAIRWAY: [-80.44186, 43.42231],
};

const INTERSECTION_FALLBACK = {
  I1: [-80.5364, 43.4978],
  I2: [-80.5432, 43.4772],
  I3: [-80.5264, 43.4675],
  I4: [-80.5056, 43.4553],
  I5: [-80.4874, 43.4492],
  I6: [-80.4535, 43.4289],
};

const YARD = [-80.5150, 43.4430];

function stationPosition(stop) {
  if (Number.isFinite(stop?.lon) && Number.isFinite(stop?.lat)) {
    return [stop.lon, stop.lat];
  }
  return GEO_FALLBACK[stop?.id] || [-80.52, 43.46];
}

function intersectionPosition(node) {
  if (Number.isFinite(node?.lon) && Number.isFinite(node?.lat)) {
    return [node.lon, node.lat];
  }
  return INTERSECTION_FALLBACK[node?.id] || [-80.52, 43.46];
}

function busPosition(bus) {
  if (Number.isFinite(bus?.lon) && Number.isFinite(bus?.lat)) {
    return [bus.lon, bus.lat];
  }
  return YARD;
}

function makePassengerDots(stops) {
  const dots = [];
  for (const stop of stops) {
    const [lon, lat] = stationPosition(stop);
    const count = Math.min(16, stop.sprites || Math.ceil((stop.queue || 0) / 6));
    for (let i = 0; i < count; i += 1) {
      const ring = Math.floor(i / 6) + 1;
      const angle = (i * 2.399963229728653) % (Math.PI * 2);
      const scale = 0.00016 * ring;
      dots.push({
        id: `${stop.id}-p${i}`,
        stopId: stop.id,
        position: [
          lon + Math.cos(angle) * scale,
          lat + Math.sin(angle) * scale * 0.72,
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
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const overlayRef = useRef(null);
  const libsRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [mapError, setMapError] = useState("");

  const stops = snapshot?.stops || network?.stops || [];
  const intersections = snapshot?.intersections || network?.intersections || [];
  const buses = snapshot?.buses || [];

  const routeData = useMemo(() => {
    const byId = Object.fromEntries((network?.stops || []).map((stop) => [stop.id, stop]));
    const path = (network?.route_order || [])
      .map((id) => byId[id])
      .filter(Boolean)
      .map(stationPosition);
    return path.length > 1 ? [{ id: "ION-SPINE", path }] : [];
  }, [network]);

  useEffect(() => {
    let disposed = false;

    async function boot() {
      if (!containerRef.current || mapRef.current) return;

      try {
        const [maplibreModule, maplibreDeck, deckLayers] = await Promise.all([
          import("maplibre-gl"),
          import("@deck.gl/maplibre"),
          import("@deck.gl/layers"),
        ]);

        if (disposed) return;

        const maplibregl = maplibreModule.default || maplibreModule;
        libsRef.current = {
          maplibregl,
          MapLibreOverlay: maplibreDeck.MapLibreOverlay,
          ...deckLayers,
        };

        const map = new maplibregl.Map({
          container: containerRef.current,
          style: "https://tiles.openfreemap.org/styles/dark",
          center: [-80.507, 43.459],
          zoom: 11.4,
          pitch: 22,
          bearing: -8,
          attributionControl: true,
        });

        map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");

        map.on("load", () => {
          if (disposed) return;

          const overlay = new maplibreDeck.MapLibreOverlay({
            interleaved: true,
            layers: [],
          });
          map.addControl(overlay);
          overlayRef.current = overlay;
          mapRef.current = map;

          const coords = Object.values(GEO_FALLBACK);
          const lngs = coords.map((p) => p[0]);
          const lats = coords.map((p) => p[1]);
          map.fitBounds(
            [
              [Math.min(...lngs) - 0.006, Math.min(...lats) - 0.005],
              [Math.max(...lngs) + 0.006, Math.max(...lats) + 0.005],
            ],
            { padding: 44, duration: 0 }
          );

          setReady(true);
        });

        map.on("error", (event) => {
          if (event?.error?.message) setMapError(event.error.message);
        });
      } catch (error) {
        setMapError(error?.message || "Could not initialize the map renderer.");
      }
    }

    boot();

    return () => {
      disposed = true;
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
      overlayRef.current = null;
      libsRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!ready || !overlayRef.current || !libsRef.current) return;

    const {
      LineLayer,
      ScatterplotLayer,
      TextLayer,
    } = libsRef.current;

    const passengerDots = makePassengerDots(stops);
    const routeColor = [80, 208, 172, 220];
    const selectedId = selected?.data?.id;

    const layers = [
      new LineLayer({
        id: "ion-corridor",
        data: routeData,
        getSourcePosition: (d) => d.path[0],
        getTargetPosition: (d) => d.path[d.path.length - 1],
        visible: false,
      }),
      new LineLayer({
        id: "ion-segments",
        data: routeData.flatMap((route) =>
          route.path.slice(0, -1).map((start, index) => ({
            start,
            end: route.path[index + 1],
          }))
        ),
        getSourcePosition: (d) => d.start,
        getTargetPosition: (d) => d.end,
        getColor: routeColor,
        getWidth: 7,
        widthUnits: "pixels",
      }),
      new ScatterplotLayer({
        id: "passenger-queues",
        data: stops,
        getPosition: stationPosition,
        getRadius: (d) => Math.max(45, Math.sqrt(d.queue || 0) * 20),
        radiusUnits: "meters",
        getFillColor: (d) =>
          (d.queue || 0) > 50 ? [237, 176, 87, 75] : [95, 193, 220, 58],
        stroked: false,
      }),
      new ScatterplotLayer({
        id: "passenger-dots",
        data: passengerDots,
        getPosition: (d) => d.position,
        getRadius: 4.5,
        radiusUnits: "pixels",
        getFillColor: [224, 233, 238, 210],
      }),
      new ScatterplotLayer({
        id: "stations",
        data: stops,
        getPosition: stationPosition,
        getRadius: (d) => (d.id === "CENTRAL" || d.id === "UW" ? 9 : 7),
        radiusUnits: "pixels",
        getFillColor: (d) =>
          d.id === selectedId ? [255, 255, 255, 255] : [8, 24, 31, 255],
        getLineColor: [117, 224, 189, 255],
        lineWidthMinPixels: 2,
        stroked: true,
        pickable: true,
        onClick: ({ object }) => object && onSelect({ type: "stop", data: object }),
      }),
      new TextLayer({
        id: "station-labels",
        data: stops,
        getPosition: stationPosition,
        getText: (d) => `${d.name}\n${d.queue || 0} waiting`,
        getColor: [235, 243, 246, 235],
        getSize: 12,
        getPixelOffset: [0, -22],
        getTextAnchor: "middle",
        getAlignmentBaseline: "bottom",
        background: true,
        getBackgroundColor: [5, 15, 22, 185],
        backgroundPadding: [4, 3],
        fontFamily: "Inter, system-ui, sans-serif",
      }),
      new ScatterplotLayer({
        id: "signals",
        data: intersections,
        getPosition: intersectionPosition,
        getRadius: 6,
        radiusUnits: "pixels",
        getFillColor: (d) =>
          d.phase === "GREEN" ? [117, 224, 189, 245] : [240, 142, 142, 245],
        getLineColor: [8, 15, 20, 255],
        lineWidthMinPixels: 2,
        stroked: true,
        pickable: true,
        onClick: ({ object }) =>
          object && onSelect({ type: "intersection", data: object }),
      }),
      new ScatterplotLayer({
        id: "yard",
        data: [{
          id: network?.yard?.id || "YARD",
          name: network?.yard?.name || "Regional Staging Yard",
          lon: network?.yard?.lon ?? YARD[0],
          lat: network?.yard?.lat ?? YARD[1],
        }],
        getPosition: (d) => [d.lon, d.lat],
        getRadius: 10,
        radiusUnits: "pixels",
        getFillColor: [71, 89, 100, 245],
        getLineColor: [160, 180, 190, 220],
        lineWidthMinPixels: 2,
        stroked: true,
        pickable: true,
        onClick: ({ object }) => object && onSelect({ type: "yard", data: object }),
      }),
      new TextLayer({
        id: "yard-label",
        data: [{
          name: "REGIONAL STAGING",
          position: [
            network?.yard?.lon ?? YARD[0],
            network?.yard?.lat ?? YARD[1],
          ],
        }],
        getPosition: (d) => d.position,
        getText: (d) => d.name,
        getColor: [185, 199, 206, 220],
        getSize: 11,
        getPixelOffset: [0, 17],
        getTextAnchor: "middle",
      }),
      new TextLayer({
        id: "buses",
        data: buses,
        getPosition: busPosition,
        getText: () => "🚌",
        getSize: 27,
        getColor: [255, 255, 255, 255],
        getTextAnchor: "middle",
        getAlignmentBaseline: "center",
        pickable: true,
        onClick: ({ object }) => object && onSelect({ type: "bus", data: object }),
      }),
      new TextLayer({
        id: "bus-labels",
        data: buses,
        getPosition: busPosition,
        getText: (d) => `${d.id}  ${d.occupancy}/${d.capacity}`,
        getColor: (d) =>
          d.id === selectedId ? [117, 224, 189, 255] : [233, 242, 245, 245],
        getSize: 11,
        getPixelOffset: [0, 22],
        getTextAnchor: "middle",
        background: true,
        getBackgroundColor: [4, 13, 19, 200],
        backgroundPadding: [4, 2],
      }),
    ];

    overlayRef.current.setProps({ layers });
  }, [ready, network, snapshot, selected, onSelect, routeData, stops, intersections, buses]);

  return (
    <div className="mapWorld">
      <div ref={containerRef} className="mapContainer" />
      {!ready && !mapError && (
        <div className="mapLoading">Loading Waterloo–Kitchener street network…</div>
      )}
      {mapError && (
        <div className="mapError">
          Map renderer error: {mapError}
        </div>
      )}
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
          <span>MAP MODE</span>
          <strong>OSM + simulation</strong>
        </div>
      </div>
      <div className="mapLegend">
        Experimental TransitOps overlay · real street basemap · simulated operations
      </div>
    </div>
  );
}
