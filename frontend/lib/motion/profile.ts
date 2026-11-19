// Speed profile along a move: the shared kinematics of terminal moves (rail
// operations) and the Fairway replay. A port of rail_ops.Profile in
// backend/rail_ops.py: keep the arithmetic in step with it.

/**
 * A speed profile along one move, on a 1 m grid with constant acceleration in
 * each cell: the train or bus enters at `v0`, keeps under `cap(s)`, and comes
 * to a stop at the end when `stop` is set.
 */
export class Profile {
  readonly length: number;
  readonly duration: number;
  private readonly v: number[];
  private readonly t: number[];
  private readonly ds: number;

  constructor(length: number, cap: (s: number) => number, options: { v0: number; stop: boolean; accel: number; decel: number }) {
    const n = Math.max(1, Math.ceil(length));
    this.ds = length / n;
    this.length = length;
    const v = Array.from({ length: n + 1 }, (_, i) => cap(i * this.ds));
    v[0] = Math.min(v[0], options.v0);
    if (options.stop) v[n] = 0;
    for (let i = 1; i <= n; i += 1) v[i] = Math.min(v[i], Math.sqrt(v[i - 1] ** 2 + 2 * options.accel * this.ds));
    for (let i = n - 1; i >= 0; i -= 1) v[i] = Math.min(v[i], Math.sqrt(v[i + 1] ** 2 + 2 * options.decel * this.ds));
    if (v[0] < options.v0 - 1e-6 && options.v0 > 0) throw new Error("entry speed too high to stop in the distance");
    this.v = v;
    this.t = [0];
    for (let i = 1; i <= n; i += 1) this.t.push(this.t[i - 1] + (2 * this.ds) / Math.max(1e-6, v[i - 1] + v[i]));
    this.duration = this.t[n];
  }

  distance(time: number) {
    if (time <= 0) return 0;
    if (time >= this.duration) return this.length;
    let lo = 0;
    let hi = this.t.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (this.t[mid] <= time) lo = mid;
      else hi = mid;
    }
    const v0 = this.v[lo];
    const v1 = this.v[hi];
    const a = (v1 * v1 - v0 * v0) / (2 * this.ds);
    const tau = time - this.t[lo];
    return Math.min(hi * this.ds, lo * this.ds + v0 * tau + 0.5 * a * tau * tau);
  }

  speed(time: number) {
    if (time <= 0) return this.v[0];
    if (time >= this.duration) return this.v[this.v.length - 1];
    const eps = 0.05;
    return Math.max(0, (this.distance(time + eps) - this.distance(time - eps)) / (2 * eps));
  }

  /** Seconds until the profile has covered `s` metres (Infinity if it never does). */
  timeAt(s: number) {
    if (s <= 0) return 0;
    if (s > this.length + 1e-9) return Infinity;
    const i = Math.min(this.v.length - 2, Math.floor(s / this.ds));
    const v0 = this.v[i];
    const v1 = this.v[i + 1];
    const d = s - i * this.ds;
    const a = (v1 * v1 - v0 * v0) / (2 * this.ds);
    const tau = Math.abs(a) < 1e-12 ? d / Math.max(1e-9, v0) : (-v0 + Math.sqrt(Math.max(0, v0 * v0 + 2 * a * d))) / a;
    return this.t[i] + tau;
  }
}

