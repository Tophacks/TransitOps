import terminalsData from "../geo/ion-terminals.json";
import type { TerminalsData } from "./terminals.ts";

/** ION terminal layouts shared by the map and the rail playback. */
export const TERMINALS = terminalsData as unknown as TerminalsData;
