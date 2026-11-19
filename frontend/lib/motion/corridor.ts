import { Run, type Curve } from "./kinematics.ts";
import { lateralOffset, pointAt, type LonLat, type PathPoint } from "./path.ts";

export type CorridorSegment = {
  from: string;
  to: string;
  length_m: number;
  curb_m: [number, number];
  signal: { id: string; stop_line_m: number };
  curves: Curve[];
  path: PathPoint[];
};

export type IonTrack = {
  shape_id: string;
  length_m: number;
  stations: { id: string; name: string; at_m: number }[];
  curves: Curve[];
  path: PathPoint[];
};

/** A GRT route through campus, Laurier or Uptown, drawn for context. */
export type ContextRoute = {
  route: string;
  name: string;
  label: string;
  direction: number;
  headsign: string;
  shape_id: string;
  length_m: number;
  stops: { id: string; name: string; at_m: number }[];
  path: PathPoint[];
};

export type CorridorData = {
  version: number;
  attribution: string;
  lane_offset_m: number;
  curb_blend_m: number;
  stops: Record<string, { grt_stop_id: string; grt_name: string; lon: number; lat: number }>;
  signals: Record<string, { name: string; lon: number; lat: number }>;
  segments: CorridorSegment[];
  ion: Record<"north" | "south", IonTrack>;
  routes: ContextRoute[];
};

export class Corridor {
  readonly data: CorridorData;
  private readonly segments = new Map<string, CorridorSegment>();
  private readonly runs = new Map<string, Run>();

  constructor(data: CorridorData) {
    this.data = data;
    for (const segment of data.segments) this.segments.set(`${segment.from}>${segment.to}`, segment);
  }

  segment(from: string, to: string) {
    const found = this.segments.get(`${from}>${to}`);
    if (!found) throw new Error(`No street path for ${from} -> ${to}`);
    return found;
  }

  /** Same trip model the simulator used for this segment (cached). */
  run(from: string, to: string, minutes: number, holdMinutes = 0) {
    const key = `${from}>${to}:${minutes}:${holdMinutes}`;
    let run = this.runs.get(key);
    if (!run) {
      const segment = this.segment(from, to);
      run = new Run(segment.length_m, segment.curves, minutes * 60, {
        holdAt: holdMinutes ? segment.signal.stop_line_m : null,
        holdS: holdMinutes * 60,
      });
      this.runs.set(key, run);
    }
    return run;
  }

  /** Lateral position of the bus centre at distance s (right of centreline). */
  offset(segment: CorridorSegment, s: number) {
    return lateralOffset(segment.length_m, segment.curb_m, this.data.lane_offset_m, this.data.curb_blend_m, s);
  }

  point(segment: CorridorSegment, s: number): LonLat {
    return pointAt(segment.path, s, this.offset(segment, s));
  }
}
