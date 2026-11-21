"""How a Route 7 driver drives: speed and position every half second.

The motion is the corridor buses' (corridor.py): comfortable pull-away
(BUS_ACCEL), comfortable service braking (BUS_DECEL), the usual cruising cap
(BUS_TOP_SPEED, up to BUS_RUSH_SPEED to keep the schedule), and speed caps from
the street's curvature with the bus braking ahead of each curve. Each segment's
cruising speed is fitted, as for the corridor, so the run between two stops
takes the time the schedule allows (corridor.Run). Following another vehicle
uses the UW Station lane rules (road_traffic.following_speed).

At a stop the driver brakes progressively at the service rate, creeps the
last CREEP_M metres at no more than CREEP_V, comes to a full stop, and only
then opens the doors (DOORS_AFTER_STOP_S later).
"""
import math

import corridor
from road_traffic import following_speed

DT = 0.5                    # integration step within the simulation's one-second clock
ACCEL = corridor.BUS_ACCEL
DECEL = corridor.BUS_DECEL
HARD_DECEL = 2.5            # the most the driver brakes for a vehicle ahead (modelled)
TOP = corridor.BUS_TOP_SPEED
RUSH = corridor.BUS_RUSH_SPEED
CREEP_M = 3.0               # the last metres into the stopping position (modelled)
CREEP_V = 0.8               # m/s while creeping into position (modelled)
DOORS_AFTER_STOP_S = 1.0    # doors open this long after the bus is stationary (modelled)
LANE_OFFSET_M = corridor.LANE_OFFSET_M
CURB_OFFSET_M = 3.4         # bus centre from the street centreline at a curbside stop (modelled)
CURB_BLEND_M = 25.0         # distance over which the bus moves to the curb and back
BUS_L = 12.2
GAP = 2.5
GRID = corridor.GRID_M


class Envelope:
    """Speed caps along a route variant: its curves, and braking ahead of each."""

    def __init__(self, pattern):
        self.length = pattern["length_m"]
        self.curves = [tuple(c) for c in pattern.get("curves", [])]
        n = max(1, math.ceil(self.length / GRID))
        cap = [RUSH] * (n + 1)
        for s, limit in self.curves:
            i = int(round(s / GRID))
            if 0 <= i <= n:
                cap[i] = min(cap[i], limit)
        for i in range(n - 1, -1, -1):
            cap[i] = min(cap[i], math.sqrt(cap[i + 1] ** 2 + 2 * DECEL * GRID))
        self.cap = cap

    def at(self, s):
        i = min(len(self.cap) - 1, max(0, int(s / GRID)))
        j = min(len(self.cap) - 1, i + 1)
        return min(self.cap[i], self.cap[j])

    def cruise(self, s0, s1, seconds):
        """The cruising cap that makes the run from s0 to s1 (stopping at both)
        take `seconds`, as the corridor fits its runs."""
        length = max(1.0, s1 - s0)
        curves = tuple((s - s0, v) for s, v in self.curves if s0 < s < s1)
        run = corridor.Run(length, curves, max(1.0, seconds))
        return max(2.0, min(RUSH, max(run.speed) if run.speed else TOP))


def stop_speed(d):
    """The most a bus may do `d` metres before its stopping point: service
    braking down to the creep speed, then the creep."""
    if d <= 0:
        return 0.0
    if d <= CREEP_M:
        return min(CREEP_V, max(0.25, CREEP_V * d / CREEP_M + 0.2))
    return math.sqrt(CREEP_V ** 2 + 2 * DECEL * (d - CREEP_M))


def advance(s, v, cap, envelope, target=None, leader_gap=None, leader_v=0.0, headway=1.3, dt=DT):
    """One step: the speed the driver chooses and where the bus gets to.
    `target`: metres to the stopping point (None: no stop ahead);
    `leader_gap`: metres to the rear of the vehicle ahead (None: none)."""
    want = min(cap, envelope.at(s), envelope.at(s + max(0.0, v) * 2.0))
    if target is not None:
        # Where it will be after this step: braking is planned one step ahead, so it
        # reaches the creep zone at creep speed.
        if target > CREEP_M:
            # Braking planned one step ahead, never past the start of the creep.
            want = min(want, stop_speed(target), stop_speed(max(CREEP_M, target - max(0.0, v) * dt)))
        else:
            want = min(want, stop_speed(target))
    if leader_gap is not None:
        want = min(want, following_speed(v, cap, max(0.0, leader_gap - GAP), headway, dt))
    v_new = min(want, v + ACCEL * dt)
    v_new = max(v_new, v - HARD_DECEL * dt, 0.0)
    step = (v + v_new) / 2.0 * dt
    if target is not None and step >= target - 0.02:
        return s + max(0.0, target), 0.0, True        # in position: a full stop
    if leader_gap is not None:
        step = min(step, max(0.0, leader_gap - GAP))
    return s + step, v_new, False


def lateral(d_to_stop, d_from_stop):
    """Metres right of the street centreline: in lane, easing to the curb at a stop."""
    def ease(x):
        x = min(1.0, max(0.0, x))
        return x * x * (3 - 2 * x)
    k = 0.0
    if d_to_stop is not None:
        k = max(k, ease(1.0 - d_to_stop / CURB_BLEND_M))
    if d_from_stop is not None:
        k = max(k, ease(1.0 - d_from_stop / CURB_BLEND_M))
    return LANE_OFFSET_M + (CURB_OFFSET_M - LANE_OFFSET_M) * k


def _between(a, b, t):
    """Position and speed at t between samples a and b, as the map reconstructs
    them: constant acceleration that lands exactly on b."""
    dt = b[0] - a[0]
    if dt <= 0:
        return b[1], b[2]
    tau = t - a[0]
    acc = 2.0 * (b[1] - a[1] - a[2] * dt) / (dt * dt)
    return a[1] + a[2] * tau + 0.5 * acc * tau * tau, a[2] + acc * tau


def simplify(trace, pos_tol=0.03, speed_tol=0.03, max_span=30.0):
    """Keep only the samples the map needs ([t, s, v]): a sample is dropped when
    constant acceleration between its kept neighbours reproduces its position
    within `pos_tol` and its speed within `speed_tol`. Gaps in time (off the
    modelled network) are kept."""
    if len(trace) <= 2:
        return trace
    out = [trace[0]]
    k = 0
    j = 1
    while j < len(trace):
        if j == len(trace) - 1:
            out.append(trace[j])
            break
        cand = trace[j + 1]
        ok = cand[0] - trace[j][0] <= 1.01 and cand[0] - trace[k][0] <= max_span
        if ok:
            for m in range(k + 1, j + 1):
                x = trace[m]
                sp, vp = _between(trace[k], cand, x[0])
                if abs(sp - x[1]) > pos_tol or abs(vp - x[2]) > speed_tol:
                    ok = False
                    break
        if ok:
            j += 1
            continue
        out.append(trace[j])
        k = j
        j += 1
    return out
