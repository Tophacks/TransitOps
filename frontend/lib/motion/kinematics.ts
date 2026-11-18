// Line-for-line port of backend/corridor.py. The simulator computes each bus's
// position at whole minutes with this model; the browser uses the same model
// to draw every frame in between, so the two agree exactly at each minute.
// Keep the arithmetic in the same order as the Python version.

export const GRID_M = 2.0;
export const BUS_ACCEL = 0.9;
export const BUS_DECEL = 1.1;
export const BUS_TOP_SPEED = 16.0;
export const BUS_RUSH_SPEED = 24.0;
const FIT_ITERATIONS = 40;
export const MIN_CRUISE = 2.5;

export type Curve = [number, number];

function gridS(i: number, n: number, length: number) {
  return i === n ? length : i * GRID_M;
}

function limits(length: number, curves: readonly Curve[], cap: number, holdAt: number | null) {
  const n = Math.max(1, Math.ceil(length / GRID_M));
  const v: number[] = new Array(n + 1).fill(cap);
  for (const [s, limit] of curves) {
    const i = Math.floor(s / GRID_M + 0.5);
    if (i >= 0 && i <= n && limit < v[i]) v[i] = limit;
  }
  v[0] = 0;
  v[n] = 0;
  if (holdAt !== null) v[Math.min(n, Math.max(0, Math.floor(holdAt / GRID_M + 0.5)))] = 0;
  return v;
}

export function speedProfile(
  length: number, curves: readonly Curve[], cap: number, holdAt: number | null, accel: number, decel: number,
) {
  const v = limits(length, curves, cap, holdAt);
  const n = v.length - 1;
  for (let i = 1; i <= n; i += 1) {
    const ds = gridS(i, n, length) - gridS(i - 1, n, length);
    const reach = Math.sqrt(v[i - 1] * v[i - 1] + 2.0 * accel * ds);
    if (reach < v[i]) v[i] = reach;
  }
  for (let i = n - 1; i >= 0; i -= 1) {
    const ds = gridS(i + 1, n, length) - gridS(i, n, length);
    const reach = Math.sqrt(v[i + 1] * v[i + 1] + 2.0 * decel * ds);
    if (reach < v[i]) v[i] = reach;
  }
  return v;
}

function runTime(length: number, v: number[]) {
  const n = v.length - 1;
  let total = 0.0;
  for (let i = 1; i <= n; i += 1) {
    const ds = gridS(i, n, length) - gridS(i - 1, n, length);
    total += 2.0 * ds / (v[i - 1] + v[i]);
  }
  return total;
}

export type RunOptions = {
  holdAt?: number | null;
  holdS?: number;
  accel?: number;
  decel?: number;
  topSpeed?: number;
  rushSpeed?: number;
};

/** One stop-to-stop trip: distance travelled as a function of time. */
export class Run {
  readonly length: number;
  readonly holdS: number;
  readonly n: number;
  readonly speed: number[];
  readonly times: number[];
  readonly holdIndex: number | null;
  readonly holdPosition: number | null;
  readonly startHold: number;
  readonly duration: number;

  constructor(length: number, curves: readonly Curve[], durationS: number, options: RunOptions = {}) {
    const holdAt = options.holdAt ?? null;
    const accel = options.accel ?? BUS_ACCEL;
    const decel = options.decel ?? BUS_DECEL;
    const topSpeed = options.topSpeed ?? BUS_TOP_SPEED;
    const rushSpeed = options.rushSpeed ?? BUS_RUSH_SPEED;
    this.length = length;
    this.holdS = holdAt !== null ? options.holdS ?? 0 : 0.0;
    const moving = Math.max(1.0, durationS - this.holdS);
    let lo = 0.3;
    let hi = topSpeed;
    let v = speedProfile(length, curves, hi, holdAt, accel, decel);
    if (runTime(length, v) > moving) {
      // Behind schedule at the usual cap: go faster on the straights
      // (corners and braking stay comfortable) before compressing time.
      lo = topSpeed;
      hi = rushSpeed;
      v = speedProfile(length, curves, hi, holdAt, accel, decel);
    }
    if (runTime(length, v) < moving) {
      for (let k = 0; k < FIT_ITERATIONS; k += 1) {
        const mid = (lo + hi) / 2.0;
        const trial = speedProfile(length, curves, mid, holdAt, accel, decel);
        if (runTime(length, trial) > moving) lo = mid;
        else hi = mid;
      }
      v = speedProfile(length, curves, hi, holdAt, accel, decel);
    }
    // A long allowance for a short hop would mean crawling at walking pace;
    // instead the bus waits at its stop and then drives at a modest speed.
    let startHold = 0.0;
    let movingTime = moving;
    if (hi < MIN_CRUISE) {
      v = speedProfile(length, curves, MIN_CRUISE, holdAt, accel, decel);
      startHold = Math.max(0.0, movingTime - runTime(length, v));
      movingTime -= startHold;
    }
    this.startHold = startHold;
    const scale = movingTime / runTime(length, v);
    const n = v.length - 1;
    this.n = n;
    this.speed = v.map((x) => x / scale);
    this.times = [startHold];
    this.holdIndex = null;
    this.holdPosition = null;
    if (holdAt !== null) {
      this.holdIndex = Math.min(n, Math.max(0, Math.floor(holdAt / GRID_M + 0.5)));
      this.holdPosition = gridS(this.holdIndex, n, length);
    }
    for (let i = 1; i <= n; i += 1) {
      const ds = gridS(i, n, length) - gridS(i - 1, n, length);
      let t = this.times[this.times.length - 1] + 2.0 * ds / (this.speed[i - 1] + this.speed[i]);
      if (this.holdIndex !== null && i - 1 === this.holdIndex) t += this.holdS;
      this.times.push(t);
    }
    this.duration = this.times[this.times.length - 1];
  }

  /** Metres travelled `t` seconds after departure. */
  distance(t: number) {
    if (t <= this.times[0]) return 0.0;
    if (t >= this.duration) return this.length;
    let lo = 0;
    let hi = this.n;
    while (hi - lo > 1) {
      const mid = Math.floor((lo + hi) / 2);
      if (this.times[mid] <= t) lo = mid;
      else hi = mid;
    }
    const s0 = gridS(lo, this.n, this.length);
    const s1 = gridS(hi, this.n, this.length);
    let t0 = this.times[lo];
    if (this.holdIndex !== null && lo === this.holdIndex) {
      if (t - t0 <= this.holdS) return s0;
      t0 += this.holdS;
    }
    const v0 = this.speed[lo];
    const v1 = this.speed[hi];
    const dt = this.times[hi] - t0;
    const tau = t - t0;
    const a = dt > 0 ? (v1 - v0) / dt : 0.0;
    return Math.min(s1, s0 + v0 * tau + 0.5 * a * tau * tau);
  }

  speedAt(t: number) {
    if (t <= this.times[0] || t >= this.duration) return 0.0;
    const eps = 0.25;
    return Math.max(0.0, (this.distance(t + eps) - this.distance(t - eps)) / (2 * eps));
  }
}
