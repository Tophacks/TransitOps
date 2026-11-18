import type { IonTrack } from "./corridor.ts";
import { bearing, deltaMetres, lerpPath, type LonLat, type PathPoint } from "./path.ts";

// Illustrative ION service for the campus scene. The Python simulator does not
// model light rail positions, so this timetable is generated here and labelled
// as simulated. It is a pure function of simulated time, so scrubbing and
// speed changes stay exact.

export const ION_HEADWAY_S = 8 * 60;
export const ION_DWELL_S = 25;
const LINE_SPEED = 15.0; // m/s (54 km/h) where the street allows
const ACCEL = 1.0;
const DECEL = 1.1;
const GRID = 2.0;

/** Section lengths, front to back (m), and the bellows gap between them. */
export const ION_SECTIONS = [9.2, 7.0, 7.4, 7.0, 9.2];
export const ION_GAP = 0.5;
export const ION_LENGTH = ION_SECTIONS.reduce((a, b) => a + b, 0) + ION_GAP * (ION_SECTIONS.length - 1);

export class IonTrip {
  readonly track: IonTrack;
  readonly duration: number;
  private readonly times: number[];
  private readonly speeds: number[];
  private readonly dwellAt: Map<number, number>;
  private readonly n: number;
  private readonly startIndex: number;

  constructor(track: IonTrack) {
    this.track = track;
    // The train starts its trip one train-length onto the track so the whole
    // consist is on real track from the first frame.
    const length = track.length_m;
    const n = Math.ceil(length / GRID);
    this.n = n;
    const v: number[] = new Array(n + 1).fill(LINE_SPEED);
    for (const [s, limit] of track.curves) {
      const i = Math.round(s / GRID);
      if (i >= 0 && i <= n) v[i] = Math.min(v[i], limit);
    }
    this.dwellAt = new Map();
    for (const station of track.stations) {
      // Stop with the train centred on the platform (nose half a train past it).
      const i = Math.round(Math.min(length - GRID, station.at_m + ION_LENGTH / 2) / GRID);
      v[i] = 0;
      this.dwellAt.set(i, ION_DWELL_S);
    }
    const startIndex = Math.ceil(ION_LENGTH / GRID);
    v[startIndex] = Math.min(v[startIndex], LINE_SPEED);
    const ds = (i: number) => (i === n ? length - (n - 1) * GRID : GRID);
    for (let i = 1; i <= n; i += 1) v[i] = Math.min(v[i], Math.sqrt(v[i - 1] ** 2 + 2 * ACCEL * ds(i)));
    for (let i = n - 1; i >= 0; i -= 1) v[i] = Math.min(v[i], Math.sqrt(v[i + 1] ** 2 + 2 * DECEL * ds(i + 1)));
    this.speeds = v;
    this.times = new Array(n + 1).fill(0);
    for (let i = startIndex + 1; i <= n; i += 1) {
      const avg = (v[i - 1] + v[i]) / 2 || 0.01;
      this.times[i] = this.times[i - 1] + ds(i) / avg + (this.dwellAt.get(i - 1) ?? 0);
    }
    this.startIndex = startIndex;
    this.duration = this.times[n];
  }

  /** Distance of the train's nose along the track `t` seconds into the trip. */
  noseAt(t: number) {
    const { times, speeds, n, startIndex } = this;
    if (t <= 0) return startIndex * GRID;
    if (t >= this.duration) return this.track.length_m;
    let lo = startIndex;
    let hi = n;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (times[mid] <= t) lo = mid;
      else hi = mid;
    }
    let t0 = times[lo];
    const dwell = this.dwellAt.get(lo) ?? 0;
    if (t - t0 <= dwell) return lo * GRID;
    t0 += dwell;
    const v0 = speeds[lo];
    const v1 = speeds[hi];
    const dt = times[hi] - t0;
    const tau = t - t0;
    const a = dt > 0 ? (v1 - v0) / dt : 0;
    return Math.min(hi === n ? this.track.length_m : hi * GRID, lo * GRID + v0 * tau + 0.5 * a * tau * tau);
  }

  /** Station the train is standing at, if any. */
  stationAt(nose: number) {
    return this.track.stations.find((station) => Math.abs(station.at_m + ION_LENGTH / 2 - nose) < GRID * 0.75);
  }
}

export type SectionPose = {
  /** chord endpoints on the track centreline */
  front: LonLat;
  rear: LonLat;
  centre: LonLat;
  heading: number;
  length: number;
};

export type TrainPose = {
  id: string;
  direction: "north" | "south";
  nose: number;
  speed: number;
  station?: string;
  sections: SectionPose[];
};

/**
 * Place each of the five sections at its own distance along the track.
 * Every section is a rigid chord between two points on the centreline, so on
 * a curve the leading section turns while the trailing ones are still on the
 * straight, and neighbouring sections meet at the same bellows gap.
 */
export function articulate(trip: IonTrip, nose: number): SectionPose[] {
  return articulatePath(trip.track.path, nose);
}

/** The same articulation along any track path (live trains use their GTFS shape). */
export function articulatePath(path: readonly PathPoint[], nose: number): SectionPose[] {
  const sections: SectionPose[] = [];
  let s = nose;
  for (const length of ION_SECTIONS) {
    const front = lerpPath(path, s);
    const rear = lerpPath(path, s - length);
    sections.push({
      front,
      rear,
      centre: [(front[0] + rear[0]) / 2, (front[1] + rear[1]) / 2],
      heading: bearing(rear, front),
      // The chord is a little shorter than the arc on a curve.
      length: Math.hypot(...deltaMetres(rear, front)),
    });
    s -= length + ION_GAP;
  }
  return sections;
}

export type IonService = { north: IonTrip; south: IonTrip };

export function ionService(tracks: Record<"north" | "south", IonTrack>): IonService {
  return { north: new IonTrip(tracks.north), south: new IonTrip(tracks.south) };
}

/** Every ION train on the campus tracks at `seconds` of simulated time. */
export function ionTrains(service: IonService, seconds: number): TrainPose[] {
  const trains: TrainPose[] = [];
  for (const direction of ["north", "south"] as const) {
    const trip = service[direction];
    const offset = direction === "north" ? 0 : ION_HEADWAY_S / 2;
    const first = Math.floor((seconds - offset - trip.duration) / ION_HEADWAY_S);
    const lastStart = Math.floor((seconds - offset) / ION_HEADWAY_S);
    for (let k = Math.max(first, -50); k <= lastStart; k += 1) {
      const t = seconds - offset - k * ION_HEADWAY_S;
      if (t < 0 || t > trip.duration) continue;
      const nose = trip.noseAt(t);
      if (nose >= trip.track.length_m - 0.01) continue;
      trains.push({
        id: `ION-${direction === "north" ? "N" : "S"}${k}`,
        direction,
        nose,
        speed: Math.max(0, (trip.noseAt(t + 0.25) - trip.noseAt(t - 0.25)) / 0.5),
        station: trip.stationAt(nose)?.name,
        sections: articulate(trip, nose),
      });
    }
  }
  return trains;
}
