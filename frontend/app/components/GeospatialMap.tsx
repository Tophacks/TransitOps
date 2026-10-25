"use client";

import { useEffect, useRef } from "react";
import type { Network, Snapshot, Selection, LiveSumoSnapshot, StopState, IntersectionState, BusState, LiveSumoVehicle } from "../../lib/types";

type Props = {
  network: Network;
  snapshot: Snapshot | null;
  selected: Selection;
  onSelect: (selection: Selection) => void;
  sumoSnapshot?: LiveSumoSnapshot | null;
};

type MapModule = typeof import("maplibre-gl");
type MapInstance = import("maplibre-gl").Map;

const STYLE = {
  version: 8 as const,
  sources: {
    "osm-basemap": {
      type: "raster" as const,
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors"
    }
  },
  layers: [{ id: "osm-basemap", type: "raster" as const, source: "osm-basemap" }]
};

function featureCollection(features: GeoJSON.Feature[]): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features };
}
function point(lon: number, lat: number, properties: Record<string, string | number> = {}): GeoJSON.Feature {
  return { type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties };
}
function route(network: Network, stops: StopState[]): GeoJSON.FeatureCollection {
  const coords = network.route_order.map(id => stops.find(stop => stop.id === id)).filter((stop): stop is StopState => Boolean(stop)).map(stop => [stop.lon, stop.lat]);
  return featureCollection(coords.length > 1 ? [{type:"Feature", geometry:{type:"LineString",coordinates:coords}, properties:{kind:"illustrative stop-to-stop route"}}] : []);
}
function setData(map: MapInstance, id: string, data: GeoJSON.FeatureCollection) {
  const source = map.getSource(id) as import("maplibre-gl").GeoJSONSource | undefined;
  if (source) source.setData(data);
}

export default function GeospatialMap({ network, snapshot, selected, onSelect, sumoSnapshot = null }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapInstance | null>(null);
  const maplibreRef = useRef<MapModule | null>(null);
  const callbacks = useRef({ network, snapshot, selected, onSelect, sumoSnapshot });
  callbacks.current = { network, snapshot, selected, onSelect, sumoSnapshot };

  useEffect(() => {
    let disposed = false;
    let map: MapInstance | null = null;
    const init = async () => {
      if (!host.current) return;
      try {
        const maplibregl = await import("maplibre-gl");
        if (disposed || !host.current) return;
        maplibreRef.current = maplibregl;
        map = new maplibregl.Map({
          container: host.current,
          style: STYLE,
          center: [-80.52, 43.463],
          zoom: 12.25,
          minZoom: 10,
          maxZoom: 17,
          attributionControl: false
        });
        mapRef.current = map;
        map.addControl(new maplibregl.NavigationControl({ showCompass: true }), "top-right");
        map.addControl(new maplibregl.AttributionControl({ compact: false }), "bottom-right");
        map.on("load", () => {
          if (disposed || !map) return;
          for (const id of ["route", "stops", "intersections", "buses", "traffic", "yard"]) {
            map.addSource(id, { type: "geojson", data: featureCollection([]) });
          }
          map.addLayer({ id: "route-stroke", type: "line", source: "route", paint: { "line-color": "#76e0bd", "line-width": 5, "line-opacity": 0.95 } });
          map.addLayer({ id: "stops-circle", type: "circle", source: "stops", paint: { "circle-radius": 8, "circle-color": "#e8fff2", "circle-stroke-color": "#177957", "circle-stroke-width": 3 } });
          map.addLayer({ id: "signals-circle", type: "circle", source: "intersections", paint: { "circle-radius": 5, "circle-color": ["match", ["get", "phase"], "GREEN", "#63c68f", "#e9a064"] } });
          map.addLayer({ id: "yard-circle", type: "circle", source: "yard", paint: { "circle-radius": 9, "circle-color": "#adbed3", "circle-stroke-color": "#203541", "circle-stroke-width": 3 } });
          map.addLayer({ id: "sumo-traffic", type: "circle", source: "traffic", paint: { "circle-radius": 3, "circle-color": "#748d9c", "circle-opacity": 0.8 } });
          map.addLayer({ id: "buses-circle", type: "circle", source: "buses", paint: { "circle-radius": 8, "circle-color": "#177957", "circle-stroke-color": "#eaffb5", "circle-stroke-width": 2 } });
          for (const [layer, type] of [["stops-circle","stop"],["signals-circle","intersection"],["buses-circle","bus"],["yard-circle","yard"]] as const) {
            map.on("click",layer,(event)=>{
              const id=event.features?.[0]?.properties?.id;
              if (!id) return;
              const current=callbacks.current;
              const datum=type==="stop" ? (current.snapshot?.stops ?? current.network.stops).find(x=>x.id===id) :
                type==="intersection" ? (current.snapshot?.intersections ?? current.network.intersections).find(x=>x.id===id) :
                type==="bus" ? current.snapshot?.buses.find(x=>x.id===id) :
                current.network.yard;
              if (datum) current.onSelect({type, data:datum} as Selection);
            });
            map.on("mouseenter",layer,()=>{if(map)map.getCanvas().style.cursor="pointer";});
            map.on("mouseleave",layer,()=>{if(map)map.getCanvas().style.cursor="";});
          }
          map?.resize();
          window.dispatchEvent(new Event("transitops-geospatial-ready"));
        });
      } catch (error) {
        if (!disposed && host.current) {
          host.current.textContent = "Basemap unavailable. Switch to the schematic view.";
          console.warn("Geospatial layer unavailable", error);
        }
      }
    };
    void init();
    return () => { disposed=true; map?.remove(); mapRef.current=null; maplibreRef.current=null; };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const update = () => {
      if (!map.isStyleLoaded() || !map.getSource("route")) return;
      const stops = snapshot?.stops ?? network.stops;
      const intersections = snapshot?.intersections ?? network.intersections;
      const live = (sumoSnapshot?.vehicles ?? []).filter((item): item is LiveSumoVehicle & {lon:number;lat:number}=>Number.isFinite(item.lon) && Number.isFinite(item.lat));
      const buses = sumoSnapshot
        ? live.filter(item => item.type.toLowerCase().includes("bus")).map(item=>point(item.lon,item.lat,{id:item.id}))
        : (snapshot?.buses ?? []).map((item: BusState)=>point(item.lon,item.lat,{id:item.id}));
      const traffic = sumoSnapshot ? live.filter(item=>!item.type.toLowerCase().includes("bus")).map(item=>point(item.lon,item.lat,{id:item.id})) : [];
      setData(map,"route",route(network,stops));
      setData(map,"stops",featureCollection(stops.map(item=>point(item.lon,item.lat,{id:item.id}))));
      setData(map,"intersections",featureCollection(intersections.map((item: IntersectionState)=>point(item.lon,item.lat,{id:item.id,phase:item.phase??"RED"}))));
      setData(map,"yard",featureCollection([point(network.yard.lon,network.yard.lat,{id:network.yard.id})]));
      setData(map,"buses",featureCollection(buses));
      setData(map,"traffic",featureCollection(traffic));
    };
    update();
    map.on("load",update);
    return ()=>{map.off("load",update);};
  }, [network,snapshot,sumoSnapshot]);

  return <section className="geoMapShell" aria-label="Waterloo corridor geographic basemap">
    <div className="geoMapOverlay">
      <strong>Geospatial context</strong>
      <span>OSM geographic basemap · stop-to-stop route is illustrative</span>
      <span>{sumoSnapshot ? "SUMO simulator locations" : "Python simulated bus positions"} · NOT live transit feeds</span>
    </div>
    <div ref={host} className="geoMapHost" role="application" aria-label="Interactive OpenStreetMap-based corridor map. Select a station or bus for details." />
  </section>;
}
