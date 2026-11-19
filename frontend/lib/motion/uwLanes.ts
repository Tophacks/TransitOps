import { encodePath, offsetLonLat, pathLength, pointAt, projections, type LonLat, type PathPoint } from "./path.ts";

export type VehicleKind = "car" | "bus";
export type Connection = { to: string; control?: "yield" | "stop" };
export type Lane = {
  id: string;
  label: string;
  access: "public" | "bus";
  path: PathPoint[];
  length: number;
  speedLimit: number;
  next: Connection[];
  signalAt?: number;
};

function lane(
  id: string, label: string, access: Lane["access"], speedLimit: number, path: PathPoint[], next: Connection[],
): Lane {
  return { id, label, access, speedLimit, path, length: pathLength(path), next };
}

function path(points: LonLat[], right = 0) {
  const center = encodePath(points);
  return right ? encodePath(center.map(([, , s]) => pointAt(center, s, right))) : center;
}

const phillipExit: LonLat = [-80.5384083, 43.4749181];
const university: LonLat = [-80.5362661, 43.4720445];
const approach = path([
  [-80.539195, 43.4759773], [-80.5390681, 43.4758065], [-80.5389636, 43.4756658],
  [-80.5387342, 43.4753569], [-80.5386145, 43.4751957], [-80.5385339, 43.4750871], phillipExit,
], 2.1);
const merge: LonLat = [approach.at(-1)![0], approach.at(-1)![1]];
const publicOutOffset = path([
  phillipExit, [-80.5383342, 43.4748205], [-80.5382195, 43.4746621], [-80.5378812, 43.4742049],
  [-80.5375501, 43.4737613], [-80.5371873, 43.4732755], [-80.5369505, 43.4729596],
  [-80.5366067, 43.4724957], university, [-80.536159, 43.471902],
], 2.1);
const publicOut = path([merge, ...publicOutOffset.slice(1).map(([lon, lat]): LonLat => [lon, lat])]);

const station = path([
  [-80.5419193, 43.4735625], [-80.5416345, 43.4736726], [-80.5412994, 43.4738022],
  [-80.5408593, 43.4739724], [-80.5405163, 43.4741051], [-80.5402013, 43.4742269],
  [-80.5399442, 43.4743264], [-80.5393342, 43.4745623], [-80.5390455, 43.4746739],
  [-80.5385069, 43.4748822], phillipExit,
], 1.75);

function stationPart(start: number, end: number) {
  return station.slice(start, end + 1).map(([lon, lat, s]): PathPoint => [lon, lat, s - station[start][2]]);
}

const berth = stationPart(3, 5);

// The curbside berth is a segment of the eastbound bus lane. All represented
// paths are directed. Unmodeled turns and passing have no graph edges.
export const UW_LANES: readonly Lane[] = [
  lane("phillip-in", "Phillip St southbound", "public", 11, approach, [{ to: "phillip-out" }]),
  {
    ...lane("phillip-out", "Phillip St to University Ave", "public", 11, publicOut, []),
    signalAt: projections(publicOut, university)[0].s - 5,
  },
  lane("ring-entry", "Transit Plaza Road west entrance", "bus", 6, stationPart(0, 3), [{ to: "berth-1223" }]),
  lane("berth-1223", "GRT stop 1223", "bus", 5, berth, [{ to: "plaza-east", control: "stop" }]),
  lane("plaza-east", "Eastbound Transit Plaza Road", "bus", 6, stationPart(5, 9), [{ to: "phillip-right" }]),
  lane("phillip-right", "Transit Plaza Road right-turn exit", "bus", 4, path([
    [station[9][0], station[9][1]], [-80.538455, 43.474887], merge,
  ]), [{ to: "phillip-out", control: "yield" }]),
];

export const UW_BUS_ROUTE = ["ring-entry", "berth-1223", "plaza-east", "phillip-right", "phillip-out"];
export const UW_CAR_ROUTE = ["phillip-in", "phillip-out"];
export const UW_BERTH_FRONT = projections(berth, [-80.5404298, 43.4740482])[0].s + 6.1;

// Synthetic geometry. Neither of these public-road stop designs is a claim
// about UW Station. Both reuse the same road engine and through lane.
const fixtureOrigin: LonLat = [-80.54, 43.474];
const fixturePath = (points: [number, number][]) =>
  path(points.map(([x, y]) => offsetLonLat(fixtureOrigin, x, y)));
export function fixtureLanes(bay: boolean): readonly Lane[] {
  return [
    lane("fixture-in", "Approach", "public", 11, fixturePath([[0, 0], [105, 0]]),
      bay ? [{ to: "fixture-out" }, { to: "fixture-bay" }] : [{ to: "fixture-out" }]),
    lane("fixture-out", "Through lane", "public", 11, fixturePath([[105, 0], [174, 0]]), [{ to: "fixture-exit" }]),
    lane("fixture-exit", "Through lane beyond stop", "public", 11, fixturePath([[174, 0], [290, 0]]), []),
    ...(bay ? [lane("fixture-bay", "Separate pullout bay", "bus", 5,
      fixturePath([[105, 0], [115, -9], [156, -9]]), [{ to: "fixture-rejoin" }]),
    lane("fixture-rejoin", "Bay departure", "bus", 5, fixturePath([[156, -9], [174, 0]]), [{ to: "fixture-exit", control: "yield" }])] : []),
  ];
}
