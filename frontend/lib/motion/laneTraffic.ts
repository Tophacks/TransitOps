import { lerpPath } from "./path.ts";
import { followingSpeed, mergeBlockers, ROAD_GAP, ROAD_STEP, type LaneOccupant } from "./road.ts";
import type { Lane, VehicleKind } from "./uwLanes.ts";

const EPSILON = 1e-7;
const STOP_TOLERANCE = 0.31;
const STOP_SECONDS = 1;

export type VehicleEntry = {
  id: string;
  kind: VehicleKind;
  route: readonly string[];
  length: number;
  speed: number;
  front: number;
};
export type LaneBody = LaneOccupant & { laneId: string; kind: VehicleKind; length: number };
export type MotionOrder = { stopAt?: { laneId: string; front: number }; speedLimit?: number };
type Vehicle = VehicleEntry & { index: number; stoppedFor: number; order: MotionOrder };
export type MergeEvent = {
  time: number;
  vehicleId: string;
  from: string;
  to: string;
  status: "waiting" | "entered" | "cleared";
  reason?: "traffic-gap" | "rear-in-merge";
  blockers: string[];
};
type Merge = Pick<MergeEvent, "vehicleId" | "from" | "to">;

export class LaneTraffic {
  time = 0;
  private readonly lanes: Map<string, Lane>;
  private readonly incoming = new Map<string, Lane[]>();
  private readonly vehicles = new Map<string, Vehicle>();
  private readonly activeMerges = new Map<string, Merge>();
  private readonly waits = new Map<string, MergeEvent>();
  private readonly events: MergeEvent[] = [];

  constructor(lanes: readonly Lane[]) {
    this.lanes = new Map(lanes.map((lane) => [lane.id, lane]));
    for (const lane of lanes) {
      for (const { to } of lane.next) {
        const list = this.incoming.get(to) ?? [];
        list.push(lane);
        this.incoming.set(to, list);
      }
    }
  }

  lane(id: string) {
    const lane = this.lanes.get(id);
    if (!lane) throw new Error(`Unknown lane ${id}`);
    return lane;
  }

  /** Returns false if the entry footprint is occupied. Invalid routes throw. */
  addVehicle(entry: VehicleEntry): boolean {
    if (this.vehicles.has(entry.id)) throw new Error(`Duplicate vehicle ${entry.id}`);
    if (!entry.route.length || !Number.isFinite(entry.length) || entry.length <= 0 ||
      !Number.isFinite(entry.speed) || entry.speed < 0 || !Number.isFinite(entry.front) ||
      entry.front < 0 || entry.front > this.lane(entry.route[0]).length) {
      throw new Error(`Invalid entry for ${entry.id}`);
    }
    for (const [i, id] of entry.route.entries()) {
      const lane = this.lane(id);
      if (lane.access === "bus" && entry.kind !== "bus") throw new Error(`Bus-only lane ${id}`);
      if (i > 0 && !this.lane(entry.route[i - 1]).next.some(({ to }) => to === id)) {
        throw new Error(`No connection to ${id}`);
      }
    }
    const rear = entry.front - entry.length;
    if (this.occupancy(entry.route[0]).some((body) =>
      body.rear - entry.front < ROAD_GAP && rear - body.front < ROAD_GAP)) return false;
    this.vehicles.set(entry.id, { ...entry, route: [...entry.route], index: 0, stoppedFor: 0, order: {} });
    return true;
  }

  /** Orders are replaced as a unit. The engine remains the sole writer of positions. */
  setMotion(id: string, order: MotionOrder) {
    const vehicle = this.vehicles.get(id);
    if (vehicle) vehicle.order = { ...order, stopAt: order.stopAt && { ...order.stopAt } };
  }

  private body(vehicle: Vehicle, index: number, front: number, rear: number): LaneBody {
    return {
      id: vehicle.id, kind: vehicle.kind, laneId: vehicle.route[index],
      length: vehicle.length, speed: vehicle.speed, front, rear,
    };
  }

  private footprints(vehicle: Vehicle) {
    const bodies: LaneBody[] = [];
    let front = vehicle.front;
    let rear = front - vehicle.length;
    for (let index = vehicle.index; index >= 0; index -= 1) {
      const length = this.lane(vehicle.route[index]).length;
      if (rear < length && front >= 0) {
        bodies.push(this.body(vehicle, index, Math.min(length, front), Math.max(0, rear)));
      }
      if (rear >= 0) break;
      if (index > 0) {
        front = this.lane(vehicle.route[index - 1]).length;
        rear += front;
      }
    }
    return bodies;
  }

  occupancy(laneId: string): readonly LaneBody[] {
    return [...this.vehicles.values()].flatMap((vehicle) => this.footprints(vehicle))
      .filter((body) => body.laneId === laneId).sort((a, b) => b.front - a.front || a.id.localeCompare(b.id));
  }

  mergeOwner(to: string): Readonly<Merge> | undefined {
    const merge = this.activeMerges.get(to);
    return merge && { ...merge };
  }

  private leaderLimit(vehicle: Vehicle, lane: Lane) {
    const others = this.occupancy(lane.id);
    const leader = others.findLast((body) => body.id !== vehicle.id && body.front >= vehicle.front);
    const next = vehicle.route[vehicle.index + 1];
    const followsThrough = lane.next.some((edge) => edge.to === next && edge.control !== "yield");
    const nextLeader = next && followsThrough ? this.occupancy(next).findLast((body) => body.id !== vehicle.id) : undefined;
    return Math.min(
      leader ? leader.rear - ROAD_GAP : Infinity,
      nextLeader ? lane.length + nextLeader.rear - ROAD_GAP : Infinity,
    );
  }

  private mergeBlock(vehicle: Vehicle, from: Lane, to: string) {
    const lock = this.activeMerges.get(to);
    if (lock && lock.vehicleId !== vehicle.id) {
      return { reason: "rear-in-merge" as const, blockers: [lock.vehicleId] };
    }
    const throughTraffic = (this.incoming.get(to) ?? []).filter((lane) =>
      lane.id !== from.id && lane.next.some((edge) => edge.to === to && !edge.control))
      .flatMap((lane) => this.occupancy(lane.id).map((body) =>
        ({ ...body, rear: body.rear - lane.length, front: body.front - lane.length })));
    // Reserve a full bus length on the receiving side and a time gap behind it.
    const blockers = [...new Set(mergeBlockers([...this.occupancy(to), ...throughTraffic], {
      id: vehicle.id, rear: 0, front: vehicle.length, speed: vehicle.speed,
    }).map(({ id }) => id))];
    return blockers.length ? { reason: "traffic-gap" as const, blockers } : undefined;
  }

  private reportWait(vehicle: Vehicle, from: Lane, to: string, block: NonNullable<ReturnType<LaneTraffic["mergeBlock"]>>) {
    const previous = this.waits.get(vehicle.id);
    if (previous?.reason === block.reason && previous.blockers.join() === block.blockers.join()) return;
    const event = { time: this.time, vehicleId: vehicle.id, from: from.id, to, status: "waiting" as const, ...block };
    this.waits.set(vehicle.id, event);
    this.events.push(event);
  }

  step(redSignals: ReadonlySet<string> = new Set()) {
    this.time += ROAD_STEP;
    const ordered = [...this.vehicles.values()].sort((a, b) =>
      Number(a.kind === "bus") - Number(b.kind === "bus") ||
      b.index - a.index || b.front - a.front || a.id.localeCompare(b.id));
    const startLimits = new Map(ordered.map((vehicle) =>
      [vehicle.id, this.leaderLimit(vehicle, this.lane(vehicle.route[vehicle.index]))]));
    for (const vehicle of ordered) {
      const lane = this.lane(vehicle.route[vehicle.index]);
      const to = vehicle.route[vehicle.index + 1];
      const edge = lane.next.find((connection) => connection.to === to);
      const gapToEnd = lane.length - vehicle.front;
      const stop = vehicle.order.stopAt;
      const orderLimit = stop?.laneId === lane.id ? Math.max(vehicle.front, stop.front) : Infinity;
      const signalLimit = lane.signalAt !== undefined && redSignals.has(lane.id) && vehicle.front <= lane.signalAt
        ? lane.signalAt : Infinity;
      const mustStop = edge?.control === "stop" && vehicle.stoppedFor < STOP_SECONDS;
      if (mustStop && gapToEnd <= STOP_TOLERANCE && vehicle.speed <= EPSILON) vehicle.stoppedFor += ROAD_STEP;
      const block = to && edge?.control === "yield" ? this.mergeBlock(vehicle, lane, to) : undefined;
      const lock = to && this.activeMerges.get(to);
      const hasPriorityLock = lock && lock.vehicleId !== vehicle.id;
      const boundaryLimit = mustStop || block || hasPriorityLock ? lane.length : Infinity;
      if (block && to && gapToEnd <= STOP_TOLERANCE) this.reportWait(vehicle, lane, to, block);
      else this.waits.delete(vehicle.id);

      const limit = Math.min(boundaryLimit, signalLimit, orderLimit);
      const gap = Math.min(limit, startLimits.get(vehicle.id)!) - vehicle.front;
      const speed = followingSpeed(vehicle.speed, Math.min(lane.speedLimit, vehicle.order.speedLimit ?? Infinity), gap);
      const cap = Math.min(limit, this.leaderLimit(vehicle, lane));
      let advance = Math.min(speed * ROAD_STEP, Math.max(0, cap - vehicle.front));
      if (to && advance > gapToEnd && !Number.isFinite(boundaryLimit)) {
        // The leader check protects the receiving lane even for a through vehicle.
        // Commit before processing the next vehicle in the tick.
        if (edge?.control === "yield") {
          const merge = { vehicleId: vehicle.id, from: lane.id, to };
          this.activeMerges.set(to, merge);
          this.events.push({ ...merge, time: this.time, status: "entered", blockers: [] });
        }
        vehicle.index += 1;
        vehicle.front = advance - gapToEnd;
        vehicle.stoppedFor = 0;
        this.waits.delete(vehicle.id);
      } else {
        if (Number.isFinite(boundaryLimit)) advance = Math.min(advance, gapToEnd);
        vehicle.front += advance;
      }
      vehicle.speed = advance <= EPSILON ? 0 : advance / ROAD_STEP;
      if (!to && vehicle.front - vehicle.length >= lane.length) this.vehicles.delete(vehicle.id);
      for (const [target, merge] of this.activeMerges) {
        if (merge.vehicleId !== vehicle.id || this.footprints(vehicle).some(({ laneId }) => laneId === merge.from)) continue;
        this.activeMerges.delete(target);
        this.events.push({ ...merge, time: this.time, status: "cleared", blockers: [] });
      }
    }
  }

  frame() {
    return {
      time: this.time,
      lanes: Object.fromEntries([...this.lanes.keys()].map((id) => [id, this.occupancy(id)])),
      vehicles: [...this.vehicles.values()].map((vehicle) => {
        const { index, front, length, speed, id, kind, route } = vehicle;
        const laneId = route[index];
        const path = this.lane(laneId).path;
        let rearIndex = index;
        let rear = front - length;
        while (rear < 0 && rearIndex > 0) rear += this.lane(route[--rearIndex]).length;
        return {
          id, kind, laneId, front, length, speed,
          nose: lerpPath(path, front),
          tail: lerpPath(this.lane(route[rearIndex]).path, rear),
        };
      }),
      waiting: [...this.waits.values()].map((event) => ({ ...event, blockers: [...event.blockers] })),
      merges: [...this.activeMerges.values()].map((merge) => ({ ...merge })),
    };
  }

  mergeEvents(): readonly MergeEvent[] {
    return this.events.map((event) => ({ ...event, blockers: [...event.blockers] }));
  }
}
