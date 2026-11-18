// A single source of truth for simulated time during playback.
//
// Every frame asks the clock for the current simulated second and derives all
// vehicle positions from it, so pausing, changing speed or scrubbing can never
// leave a vehicle drifting away from the timeline.

export class PlaybackClock {
  private base = 0;
  private anchor = 0;
  private rate = 1;
  private running = false;
  private end: number;
  private readonly now: () => number;

  constructor(end: number, now: () => number = () => performance.now()) {
    this.end = end;
    this.now = now;
  }

  /** Simulated seconds at this instant. */
  time() {
    if (!this.running) return this.base;
    const t = this.base + ((this.now() - this.anchor) / 1000) * this.rate;
    return Math.min(this.end, Math.max(0, t));
  }

  get playing() {
    return this.running && this.time() < this.end;
  }

  get speed() {
    return this.rate;
  }

  get duration() {
    return this.end;
  }

  setDuration(end: number) {
    this.rebase();
    this.end = end;
    this.base = Math.min(this.base, end);
  }

  play() {
    if (this.time() >= this.end) this.base = 0;
    this.rebase();
    this.running = true;
  }

  pause() {
    this.rebase();
    this.running = false;
  }

  /** Simulated seconds per real second (1 = real time). */
  setSpeed(rate: number) {
    this.rebase();
    this.rate = rate;
  }

  seek(t: number) {
    this.base = Math.min(this.end, Math.max(0, t));
    this.anchor = this.now();
  }

  private rebase() {
    this.base = this.time();
    this.anchor = this.now();
  }
}
