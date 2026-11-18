export type GrtMode = "bus" | "ion";

export type GrtVehicle = {
  id: string;
  mode: GrtMode;
  lon: number;
  lat: number;
  route: string | null;
  headsign: string | null;
  stop: string | null;
  stopStatus: "At stop" | "Approaching" | "Next stop";
  /** GTFS trip from the realtime feed, if it sent one */
  tripId: string | null;
  /** route shape the vehicle is snapped to */
  shapeId: string | null;
  /** how the shape was found: the feed's trip id, or the nearest shape of its route */
  shapeMatch: "trip" | "route" | null;
  /** 0..1 through the trip's stops, to place the fix on looping shapes */
  progress: number | null;
  /** Unix seconds of the GPS fix (the feed header time when the vehicle has none) */
  timestamp: number;
};

export type GrtFleet = {
  vehicles: GrtVehicle[];
  feeds: Record<GrtMode, "current" | "stale" | "unavailable">;
};

/** Simplified GTFS route shapes: id -> [lon, lat, cumulative metres] */
export type GrtShapes = { mode: GrtMode; shapes: Record<string, [number, number, number][]> };
