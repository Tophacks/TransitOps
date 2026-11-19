import terminalsData from "../geo/ion-terminals.json";
import type { TerminalsData } from "./terminals.ts";

/** ION terminal layouts shared by the map and the rail playback. */
export const TERMINALS = terminalsData as unknown as TerminalsData;

import networkData from "../geo/ion-network.json";
import type { PathPoint } from "./path.ts";

export type NetworkCrossover = {
  way: number; site: string; electrified: boolean; passenger: boolean; status: string;
  ends: { node: number; track: string; line_m: number; lon: number; lat: number }[];
  moves: { travel: string; from: string; to: string }[];
  path: PathPoint[];
};

/** The ION operating network: tracks, crossovers and diversions (build_network.py). */
export const NETWORK = networkData as unknown as {
  crossovers: NetworkCrossover[];
  stations: Record<"north" | "south", { name: string; gtfs_m: number; track_m: number }[]>;
};
