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
};

export type GrtFleet = {
  vehicles: GrtVehicle[];
  feeds: Record<GrtMode, "current" | "stale" | "unavailable">;
};
