// ION level-crossing gates: they lower as a simulated train approaches and
// rise once it has cleared. Gate state is a pure function of the trains'
// positions, so it follows the playback clock exactly.
//
// Crossings come from the geometry builder (ion_crossings): roads with a GRT
// bus route that cross the track at grade, excluding street-running sections
// where traffic signals control the junction. They are illustrative, not an
// inventory of the Region's crossings.

import { ION_LENGTH, type TrainPose } from "./ion.ts";

export type IonCrossing = {
  id: string;
  lon: number;
  lat: number;
  /** compass bearing of the road (0-180) */
  road_bearing: number;
  at_m: { north: number; south: number };
  near: string;
};

/** Gates start down this far ahead of the train's nose, fully down by DOWN_M. */
export const WARN_M = 260;
export const DOWN_M = 200;
/** They start rising this far after the train's tail clears, fully up by UP_M. */
export const CLEAR_M = 15;
export const UP_M = 50;

export type GateState = {
  crossing: IonCrossing;
  /** 0 = up, 1 = down */
  lowered: number;
  /** warning lights flashing */
  active: boolean;
};

function clamp01(x: number) {
  return Math.min(1, Math.max(0, x));
}

/** How far down a crossing's gates are for one train (0..1). */
export function loweredFor(crossing: IonCrossing, train: Pick<TrainPose, "direction" | "nose">) {
  const at = crossing.at_m[train.direction];
  const ahead = at - train.nose;
  if (ahead >= 0) return clamp01((WARN_M - ahead) / (WARN_M - DOWN_M));
  const cleared = train.nose - ION_LENGTH - at;
  if (cleared <= CLEAR_M) return 1;
  return clamp01(1 - (cleared - CLEAR_M) / (UP_M - CLEAR_M));
}

export function gateStates(crossings: readonly IonCrossing[], trains: readonly TrainPose[]): GateState[] {
  return crossings.map((crossing) => {
    const lowered = trains.reduce((most, train) => Math.max(most, loweredFor(crossing, train)), 0);
    return { crossing, lowered, active: lowered > 0 };
  });
}
