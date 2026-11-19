// Longitudinal coordinates increase in the lane's direction of travel.
export const ROAD_STEP = 0.5;
export const ROAD_GAP = 2.4;

const HEADWAY_S = 1.3;
const ACCEL = 1.8;
const BRAKE = 4.0;
const MERGE_HEADWAY_S = 3;

export type LaneOccupant = Readonly<{
  id: string;
  rear: number;
  front: number;
  speed: number;
}>;

/** Existing corridor following rule. Gap is the remaining travel after clearance. */
export function followingSpeed(speed: number, cruise: number, gap: number) {
  const b = BRAKE * 0.85;
  const clearance = Math.max(0, gap - 0.3);
  const safe = -b * ROAD_STEP + Math.sqrt((b * ROAD_STEP) ** 2 + 2 * b * clearance);
  const want = Math.min(cruise, safe, clearance / HEADWAY_S + 1.5);
  const dv = Math.max(-BRAKE * ROAD_STEP, Math.min(ACCEL * ROAD_STEP, want - speed));
  return Math.max(0, speed + dv);
}

/** A merge needs space now and time for the nearest approaching driver to react. */
export function canMerge(lane: readonly LaneOccupant[], entrant: LaneOccupant): boolean {
  return mergeBlockers(lane, entrant).length === 0;
}

export function mergeBlockers(lane: readonly LaneOccupant[], entrant: LaneOccupant) {
  return lane.filter((other) => {
    if (other.id === entrant.id) return false;
    if (other.front <= entrant.rear) {
      return entrant.rear - other.front < ROAD_GAP + MERGE_HEADWAY_S * other.speed;
    }
    return other.rear - entrant.front < ROAD_GAP;
  });
}
