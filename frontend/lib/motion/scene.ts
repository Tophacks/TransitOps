import corridorData from "../geo/waterloo-corridor.json";
import { Corridor, type CorridorData } from "./corridor.ts";
import { ionService } from "./ion.ts";

/** Street and track geometry shared by the map, schematic and inspector. */
export const corridor = new Corridor(corridorData as unknown as CorridorData);
export const ion = ionService(corridor.data.ion);

export const CORRIDOR_ATTRIBUTION = corridor.data.attribution;
