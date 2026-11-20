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
export type YardRoute = { id: string; kind: "out" | "in"; lead: "north" | "south"; track: string; yard_m: number; length_m: number;
  path: [number, number, number][]; via: string };
export const NETWORK = networkData as unknown as {
  crossovers: NetworkCrossover[];
  yard: {
    name: string; address: string;
    leads: Record<"north" | "south", { node: number; lon: number; lat: number; track: string; track_m: number; with_traffic: boolean }>;
    tracks: { id: string; way: number; kind: string; where: string; end: string; positions: number; usable_m: number; lon: number; lat: number }[];
    routes: YardRoute[];
    outline: Record<"building" | "wash" | "site", [number, number, number][]>;
  };
  stations: Record<"north" | "south", { name: string; gtfs_m: number; track_m: number }[]>;
};
