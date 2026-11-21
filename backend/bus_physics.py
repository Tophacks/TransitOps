"""Bus driving physics shared by every simulated bus (docs/bus-physics.md).

One controller drives the blue Route 7 buses, the yellow corridor buses, the
buses inside UW Station, garage pull-outs and returns, and spares sent to a
relief point. A movement system (lines.py, uw_station.py, ...) gives the
driver an *intent*: a legal lane path, the drivable area around it, a speed
limit, where to stop, the vehicle ahead, and whether Control has authorised a
reverse. The controller turns that into steering and acceleration commands
and integrates the bus's position and heading from them.

Position reference: the **centre of the rear axle**, in metres on a local
plane (east x, north y) around LON0/LAT0, with the scale the corridor already
uses (corridor.py, frontend path.ts). Heading `yaw` is radians anticlockwise
from east. Speed is metres per second along the heading (always >= 0; `gear`
is +1 forward, -1 reverse). Steering `steer` is the equivalent front-wheel
angle of a bicycle model, radians, positive to the left.

The model is the kinematic bicycle on the rear axle:
    x' = v cos(yaw) * gear,  y' = v sin(yaw) * gear,  yaw' = gear * v tan(steer) / wheelbase,
with the steering angle and its rate limited, and acceleration changing at a
bounded jerk. Low-speed station and urban driving keeps tyre slip small, so a
kinematic model is appropriate (it is not used above 60 km/h).

Steering: forward, the driver steers the front axle along the lane path with
a look-ahead Stanley law; the rear axle then follows inside every curve
(off-tracking) and the front overhang swings outside it, as on a real bus. In
reverse, the rear axle tracks the path.

Every step, the body footprint swept during the step is checked against the
drivable area and against the footprints of other vehicles; a step that would
leave the area or touch another vehicle is not taken: the bus stops and the
movement system is told why.
"""
import math
from bisect import bisect_right
from collections import deque

# --- the local plane ------------------------------------------------------------------
LON0, LAT0 = -80.5, 43.45
M_PER_DEG = 111_320.0
KX = M_PER_DEG * math.cos(math.radians(43.465))   # as corridor.py and frontend/lib/motion/path.ts
KY = M_PER_DEG


def to_xy(lon, lat):
    return (lon - LON0) * KX, (lat - LAT0) * KY


def to_lonlat(x, y):
    return LON0 + x / KX, LAT0 + y / KY


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


# --- vehicle geometry -------------------------------------------------------------------
class Geometry:
    """Dimensions of one vehicle class, metres and radians. `sources` says
    where each figure comes from; `derived` and `assumed` mark the rest."""

    def __init__(self, key, name, length, width, wheelbase, front_overhang, rear_overhang,
                 max_steer, max_steer_rate, track, height, curb_mass, capacity, sources):
        self.key, self.name = key, name
        self.length, self.width = length, width
        self.wheelbase = wheelbase
        self.front_overhang, self.rear_overhang = front_overhang, rear_overhang
        assert abs(front_overhang + wheelbase + rear_overhang - length) < 0.02, key
        self.max_steer, self.max_steer_rate = max_steer, max_steer_rate
        self.track, self.height = track, height
        self.curb_mass, self.capacity = curb_mass, capacity
        self.sources = sources

    # Rear axle centre to the body's extremes, along and across the heading.
    @property
    def front(self):
        return self.wheelbase + self.front_overhang

    @property
    def rear(self):
        return -self.rear_overhang

    @property
    def centre_offset(self):
        """Body centre ahead of the rear axle."""
        return (self.front + self.rear) / 2

    @property
    def min_radius_rear(self):
        """Smallest radius the rear axle centre can follow (full lock)."""
        return self.wheelbase / math.tan(self.max_steer)

    @property
    def min_radius_front(self):
        """Smallest radius the front axle centre can follow."""
        return self.wheelbase / math.sin(self.max_steer)

    def swept_radii(self, steer):
        """Radii at a steady steering angle: rear axle centre, inner rear wheel,
        outer front body corner (the wall-to-wall radius)."""
        r = self.wheelbase / math.tan(abs(steer))
        inner_rear = r - self.track / 2
        outer = math.hypot(r + self.width / 2, self.front)
        return r, inner_rear, outer

    def describe(self):
        return {"key": self.key, "name": self.name, "length_m": self.length, "width_m": self.width,
                "wheelbase_m": self.wheelbase, "front_overhang_m": self.front_overhang,
                "rear_overhang_m": self.rear_overhang, "max_steer_deg": round(math.degrees(self.max_steer), 1),
                "max_steer_rate_deg_s": round(math.degrees(self.max_steer_rate), 1),
                "track_m": self.track, "height_m": self.height, "reference": "rear axle centre",
                "sources": self.sources}


def _lock_from_radius(wheelbase, track, radius_outer_front_wheel):
    """Bicycle steering lock that puts the outside front wheel on `radius`."""
    r_rear = math.sqrt(radius_outer_front_wheel ** 2 - wheelbase ** 2) - track / 2
    return math.atan(wheelbase / r_rear)


def _lock_from_body_radius(wheelbase, width, front, radius_body):
    r_rear = math.sqrt(radius_body ** 2 - front ** 2) - width / 2
    return math.atan(wheelbase / r_rear)


STEER_RATE = math.radians(20.0)   # road-wheel rate a driver reaches (assumed: ~400 deg/s at the wheel, ~20:1)

GEOMETRY = {
    # GRT's most numerous model (148 of 259 buses in a dated fleet list).
    "nova_lfs_40": Geometry(
        "nova_lfs_40", "Nova Bus LFS 40 ft", length=12.19, width=2.59, wheelbase=6.20,
        front_overhang=2.10, rear_overhang=3.89,
        max_steer=_lock_from_radius(6.20, 2.16, 12.45), max_steer_rate=STEER_RATE,
        track=2.16, height=3.20, curb_mass=12_500.0, capacity=70,
        sources={
            "length_m": "Nova Bus LFS brochure: 40 ft (12.2 m)",
            "width_m": "Nova Bus LFS brochure: 102 in (2.6 m)",
            "wheelbase_m": "Nova Bus LFS brochure: 244 in front to rear axle",
            "turning_radius_m": "Nova Bus LFS brochure: 40 ft 10 in (12.45 m), read as the outside front wheel",
            "max_steer": "derived from the turning radius, wheelbase and track",
            "front_overhang_m": "assumed: AASHTO CITY-BUS design vehicle front overhang (2.1 m)",
            "rear_overhang_m": "derived: length - wheelbase - front overhang",
            "track_m": "assumed: width less tyre and wheel-well allowance",
            "height_m": "Nova Bus LFS diesel brochure: 10 ft 6 in (3.20 m)",
            "curb_mass_kg": "assumed: typical 40 ft diesel low-floor bus",
            "max_steer_rate": "assumed: 20 deg/s road-wheel rate",
        }),
    "nfi_xd40": Geometry(
        "nfi_xd40", "New Flyer Xcelsior XD40", length=12.50, width=2.59, wheelbase=7.21,
        front_overhang=2.10, rear_overhang=3.19,
        max_steer=_lock_from_body_radius(7.21, 2.59, 7.21 + 2.10, 13.4), max_steer_rate=STEER_RATE,
        track=2.16, height=3.25, curb_mass=12_700.0, capacity=70,
        sources={
            "length_m": "New Flyer Xcelsior spec: 41 ft 0 in (12.50 m) over bumpers",
            "width_m": "New Flyer Xcelsior: 102 in (2.59 m)",
            "wheelbase_m": "New Flyer Xcelsior spec: 283.75 in (7.21 m), 40 ft",
            "turning_radius_m": "New Flyer Xcelsior 40 ft: 44 ft (13.4 m) to the body",
            "max_steer": "derived from the body turning radius, wheelbase, overhang and width",
            "front_overhang_m": "assumed: AASHTO CITY-BUS design vehicle front overhang (2.1 m)",
            "rear_overhang_m": "derived: length - wheelbase - front overhang",
            "track_m": "assumed: width less tyre and wheel-well allowance",
            "height_m": "assumed",
            "curb_mass_kg": "assumed: typical 40 ft diesel low-floor bus",
            "max_steer_rate": "assumed: 20 deg/s road-wheel rate",
        }),
}
DEFAULT_CLASS = "nova_lfs_40"
XD40_SHARE = 62 / 210      # 62 XD40 against 148 LFS in the same dated fleet list (modelled assignment)

# A car through the same interface (road_traffic.py moves cars in one dimension today).
CAR = Geometry("car", "passenger car (modelled)", length=4.6, width=1.85, wheelbase=2.75,
               front_overhang=0.95, rear_overhang=0.90, max_steer=math.radians(35), max_steer_rate=math.radians(60),
               track=1.58, height=1.45, curb_mass=1_500.0, capacity=5, sources={"all": "assumed: mid-size car"})


def bus_class(vehicle_id, seed=0):
    """The model a fleet number drives: a stable, seeded share (fleet numbers are modelled)."""
    import hashlib
    h = hashlib.sha256(f"{vehicle_id}|bus-class".encode()).digest()
    return "nfi_xd40" if int.from_bytes(h[:8], "big") / 2 ** 64 < XD40_SHARE else DEFAULT_CLASS


# --- driving limits ----------------------------------------------------------------------
class Limits:
    """Longitudinal and lateral limits a bus driver keeps to (m, s)."""
    accel = 0.9            # m/s^2 comfortable pull-away (corridor.BUS_ACCEL)
    service = 1.1          # m/s^2 service braking (corridor.BUS_DECEL)
    firm = 2.5             # m/s^2 firm braking for a vehicle ahead (line_driver.HARD_DECEL)
    emergency = 4.0        # m/s^2 emergency braking (assumed: ~0.4 g, standing passengers)
    jerk = 1.0             # m/s^3 comfortable jerk (assumed: standing-passenger comfort)
    jerk_emergency = 8.0   # m/s^3 brake application in an emergency (assumed)
    lateral = 1.4          # m/s^2 cornering (corridor curve caps)
    creep_v = 0.8          # m/s into the stopping position (line_driver.CREEP_V)
    creep_m = 3.0
    final_decel = 0.25     # m/s^2 easing off the brake as it comes to rest (assumed)
    reverse_v = 1.0        # m/s backing (uw_station.REVERSE_SPEED)
    perception_s = 0.7     # s to perceive a new hazard before braking (assumed, plus the driver's own)
    gap = 2.5              # m standstill gap to the vehicle ahead
    power_w = 208_000 * 0.8   # Cummins L9 280 hp class engine, 80 % at the wheels (assumed)
    rolling = 0.008        # rolling resistance coefficient (assumed)
    passenger_kg = 68.0    # per passenger: 150 lb, the bus-testing convention (assumed for GRT)


G = 9.81


class Driver:
    """The driver's own way of driving: pace (share of the limit), headway,
    reaction before moving off (drivers.py), and perception before braking."""

    def __init__(self, pace=0.94, headway=1.3, reaction=1.0):
        self.pace, self.headway, self.reaction = pace, headway, reaction

    @classmethod
    def of(cls, profile):
        if profile is None:
            return cls()
        return cls(profile.pace, profile.headway, float(profile.reaction))


# --- paths and the drivable area ---------------------------------------------------------
class Path:
    """A legal lane path, resampled every STEP metres: positions, headings and
    curvature (positive left), with the lateral offset convention positive to
    the right of the direction of travel."""

    STEP = 1.0

    def __init__(self, points, name="", resample=True):
        pts = [(float(x), float(y)) for x, y in points]
        clean = [pts[0]]
        for p in pts[1:]:
            if math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > 1e-6:
                clean.append(p)
        if len(clean) < 2:
            clean.append((clean[0][0] + 0.01, clean[0][1]))
        cum = [0.0]
        for a, b in zip(clean, clean[1:]):
            cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        self.name = name
        self.length = cum[-1]
        if resample:
            n = max(1, int(math.ceil(self.length / self.STEP)))
            xs, ys = [], []
            j = 0
            for i in range(n + 1):
                s = min(self.length, i * self.length / n)
                while j + 1 < len(cum) - 1 and cum[j + 1] < s:
                    j += 1
                a, b = clean[j], clean[j + 1]
                f = (s - cum[j]) / (cum[j + 1] - cum[j]) if cum[j + 1] > cum[j] else 0.0
                xs.append(a[0] + (b[0] - a[0]) * f)
                ys.append(a[1] + (b[1] - a[1]) * f)
            self.ds = self.length / n
        else:
            xs, ys = [p[0] for p in clean], [p[1] for p in clean]
            self.ds = None
        self.x, self.y = xs, ys
        self.s = [i * self.ds for i in range(len(xs))] if self.ds else cum
        m = len(xs)
        self.h = []
        for i in range(m):
            a, b = max(0, i - 1), min(m - 1, i + 1)
            self.h.append(math.atan2(ys[b] - ys[a], xs[b] - xs[a]))
        self.k = [0.0] * m
        for i in range(1, m - 1):
            span = self.s[i + 1] - self.s[i - 1]
            if span > 0:
                self.k[i] = wrap(self.h[i + 1] - self.h[i - 1]) / span
        if m > 2:
            self.k[0], self.k[-1] = self.k[1], self.k[-2]
        # light smoothing of curvature (two passes of a 3-tap filter)
        for _ in range(2):
            k = self.k[:]
            for i in range(1, m - 1):
                self.k[i] = (k[i - 1] + 2 * k[i] + k[i + 1]) / 4

    @classmethod
    def from_lonlat(cls, coords, name=""):
        return cls([to_xy(lon, lat) for lon, lat in coords], name)

    def _i(self, s):
        if self.ds:
            return min(len(self.x) - 2, max(0, int(s / self.ds)))
        return min(len(self.x) - 2, max(0, bisect_right(self.s, s) - 1))

    def at(self, s):
        s = min(self.length, max(0.0, s))
        i = self._i(s)
        seg = self.s[i + 1] - self.s[i]
        f = (s - self.s[i]) / seg if seg > 0 else 0.0
        x = self.x[i] + (self.x[i + 1] - self.x[i]) * f
        y = self.y[i] + (self.y[i + 1] - self.y[i]) * f
        h = self.h[i] + wrap(self.h[i + 1] - self.h[i]) * f
        return x, y, h

    def curvature(self, s):
        i = self._i(min(self.length, max(0.0, s)))
        return self.k[i]

    def offset_point(self, s, d):
        """The point `d` metres right of the path at s."""
        x, y, h = self.at(s)
        return x + math.sin(h) * d, y - math.cos(h) * d

    def project(self, x, y, hint=None, window=40.0):
        """(s, d): distance along the path of the nearest point and the
        lateral offset of (x, y), positive to the right."""
        if hint is None:
            lo, hi = 0, len(self.x) - 1
        else:
            lo, hi = self._i(hint - window), min(len(self.x) - 1, self._i(hint + window) + 1)
        best = (1e18, 0.0, 0.0)
        for i in range(lo, hi):
            ax, ay = self.x[i], self.y[i]
            bx, by = self.x[i + 1], self.y[i + 1]
            vx, vy = bx - ax, by - ay
            l2 = vx * vx + vy * vy
            f = 0.0 if l2 == 0 else max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / l2))
            px, py = ax + vx * f, ay + vy * f
            d2 = (x - px) ** 2 + (y - py) ** 2
            if d2 < best[0]:
                side = (vx * (y - ay) - vy * (x - ax))   # > 0: left of travel
                best = (d2, self.s[i] + f * math.sqrt(l2), -math.copysign(math.sqrt(d2), side) if side else 0.0)
        return best[1], best[2]

    def shifted(self, offset, name=None):
        """A parallel path `offset(s)` metres to the right (a function or a number)."""
        f = offset if callable(offset) else (lambda s, o=offset: o)
        pts = []
        for i in range(len(self.x)):
            d = f(self.s[i])
            pts.append((self.x[i] + math.sin(self.h[i]) * d, self.y[i] - math.cos(self.h[i]) * d))
        return Path(pts, name or self.name)

    def reversed(self):
        return Path(list(zip(reversed(self.x), reversed(self.y))), self.name + " (reverse)")

    def lonlat(self, every=1):
        return [list(to_lonlat(self.x[i], self.y[i])) for i in range(0, len(self.x), every)]


class Area:
    """The drivable area around a reference path: lateral bounds (metres,
    positive right) from `left(s)` to `right(s)`, e.g. the road centreline to
    the right-hand curb. Platforms, kerbs and islands are the bounds; other
    vehicles are obstacles (footprints) given per step."""

    def __init__(self, ref, left, right, name=""):
        self.ref, self.name = ref, name
        self.left = left if callable(left) else (lambda s, v=left: v)
        self.right = right if callable(right) else (lambda s, v=right: v)

    def violations(self, points, hint=None, tol=0.02):
        out = []
        for (px, py) in points:
            s, d = self.ref.project(px, py, hint)
            if s <= 0.0 or s >= self.ref.length:
                continue          # beyond the ends: the next area takes over
            lo, hi = self.left(s), self.right(s)
            if d < lo - tol:
                out.append({"s": round(s, 1), "side": "left", "by_m": round(lo - d, 2)})
            elif d > hi + tol:
                out.append({"s": round(s, 1), "side": "right", "by_m": round(d - hi, 2)})
        return out


def _sat(poly_a, poly_b):
    """Do two convex polygons overlap (separating-axis test)?"""
    for poly in (poly_a, poly_b):
        n = len(poly)
        for i in range(n):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % n]
            ax, ay = y1 - y2, x2 - x1
            pa = [ax * x + ay * y for x, y in poly_a]
            pb = [ax * x + ay * y for x, y in poly_b]
            if max(pa) < min(pb) or max(pb) < min(pa):
                return False
    return True


# --- the vehicle --------------------------------------------------------------------------
class Vehicle:
    """A bus's physical state (rear-axle reference) and its geometry."""

    def __init__(self, vid, geometry, x=0.0, y=0.0, yaw=0.0, v=0.0, load=0):
        self.id, self.g = vid, geometry
        self.x, self.y, self.yaw = x, y, yaw
        self.v, self.a, self.steer = v, 0.0, 0.0
        self.gear = 1
        self.odo = 0.0
        self.load = load            # passengers aboard (mass)
        self.t = 0.0
        self.jerk_peak = 0.0

    @property
    def mass(self):
        return self.g.curb_mass + self.load * Limits.passenger_kg

    def point(self, along, across=0.0):
        """A point `along` metres ahead of the rear axle and `across` metres to the right."""
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        return self.x + c * along + s * across, self.y + s * along - c * across

    @property
    def front_axle(self):
        return self.point(self.g.wheelbase)

    @property
    def centre(self):
        return self.point(self.g.centre_offset)

    def footprint(self, margin=0.0):
        """Body corners: front-left, front-right, rear-right, rear-left."""
        f, r, w = self.g.front + margin, self.g.rear - margin, self.g.width / 2 + margin
        return [self.point(f, -w), self.point(f, w), self.point(r, w), self.point(r, -w)]

    def outline(self, margin=0.0):
        """Corners and side midpoints, for checking against the area's bounds."""
        f, r, w = self.g.front + margin, self.g.rear - margin, self.g.width / 2 + margin
        m = (f + r) / 2
        pts = []
        for along in (f, (f + m) / 2, m, (m + r) / 2, r):
            pts.append(self.point(along, -w))
            pts.append(self.point(along, w))
        return pts

    def wheels(self):
        """Contact points of the four wheels (front-left, front-right, rear-left, rear-right)."""
        t = self.g.track / 2
        return [self.point(self.g.wheelbase, -t), self.point(self.g.wheelbase, t), self.point(0, -t), self.point(0, t)]

    def state(self):
        return {"x": self.x, "y": self.y, "yaw": self.yaw, "v": self.v, "a": self.a, "steer": self.steer,
                "gear": self.gear, "odo": self.odo, "load": self.load, "class": self.g.key}

    def restore(self, st):
        for k in ("x", "y", "yaw", "v", "a", "steer", "gear", "odo", "load"):
            setattr(self, k, st[k])


def place_on_path(vehicle, path, s_front):
    """Put the vehicle on a path as a driver in steady tracking would have it:
    front axle at s_front, rear axle on the path one wheelbase behind (the
    off-tracking of a steady turn)."""
    fx, fy, fh = path.at(s_front)
    wb = vehicle.g.wheelbase
    lo, hi = max(0.0, s_front - wb * 1.6), s_front
    if s_front < wb:
        rx, ry = fx - math.cos(fh) * wb, fy - math.sin(fh) * wb
    else:
        for _ in range(30):
            mid = (lo + hi) / 2
            mx, my, _h = path.at(mid)
            if math.hypot(fx - mx, fy - my) > wb:
                lo = mid
            else:
                hi = mid
        rx, ry, _h = path.at((lo + hi) / 2)
    vehicle.yaw = math.atan2(fy - ry, fx - rx)
    vehicle.x, vehicle.y = fx - math.cos(vehicle.yaw) * wb, fy - math.sin(vehicle.yaw) * wb
    # the steady steering angle for the curvature at the front axle
    k = path.curvature(s_front)
    vehicle.steer = max(-vehicle.g.max_steer, min(vehicle.g.max_steer, math.asin(max(-1.0, min(1.0, wb * k)))))
    return vehicle


# --- the driver's intent and the controller -------------------------------------------------
class Intent:
    """What the movement system asks of the driver this step."""

    def __init__(self, path, speed_limit, stop_s=None, leader_gap=None, leader_v=0.0, area=None,
                 obstacles=(), gear=1, authority=True, creep=True, emergency_ok=True, hold=False):
        self.path = path              # the legal lane path (front axle forward, rear axle reverse)
        self.speed_limit = speed_limit
        self.stop_s = stop_s          # stop with the reference axle here (s along the path)
        self.leader_gap = leader_gap  # metres from the front bumper to the vehicle ahead
        self.leader_v = leader_v
        self.area = area              # drivable area (None: unchecked)
        self.obstacles = obstacles    # [(id, footprint polygon)] other vehicles near it
        self.gear = gear              # +1 forward; -1 reverse (needs authority)
        self.authority = authority    # Control's movement authority for this path
        self.creep = creep
        self.emergency_ok = emergency_ok
        self.hold = hold              # stay stopped (doors, waiting for a gap)


class StepResult:
    __slots__ = ("s", "d", "moved", "conflict", "stopped", "emergency")

    def __init__(self, s, d, moved, conflict=None, stopped=False, emergency=False):
        self.s, self.d, self.moved = s, d, moved
        self.conflict, self.stopped, self.emergency = conflict, stopped, emergency


class SpeedEnvelope:
    """The most a vehicle may do at each metre of a path: the curve limits
    (cornering and steering rate), with braking ahead of each at the service rate."""

    _cache = {}

    def __init__(self, path, geometry, limits=Limits):
        n = len(path.x)
        cap = []
        wb = geometry.wheelbase
        for i in range(n):
            k = abs(path.k[i])
            v = 60 / 3.6
            if k > 1e-4:
                v = min(v, math.sqrt(limits.lateral / k))
            # steering rate: d(steer)/dt ~ wb * dk/ds * v must stay within the rate limit
            if 0 < i < n - 1:
                dk = abs(path.k[i + 1] - path.k[i - 1]) / max(1e-6, path.s[i + 1] - path.s[i - 1])
                if dk > 1e-5:
                    v = min(v, 0.8 * geometry.max_steer_rate / (wb * dk))
            cap.append(max(1.0, v))
        for i in range(n - 2, -1, -1):
            ds = path.s[i + 1] - path.s[i]
            cap[i] = min(cap[i], math.sqrt(cap[i + 1] ** 2 + 2 * limits.service * ds))
        self.path, self.cap = path, cap

    @classmethod
    def of(cls, path, geometry):
        key = (id(path), geometry.key)
        env = cls._cache.get(key)
        if env is None or env.path is not path:
            env = cls._cache[key] = cls(path, geometry)
        return env

    def at(self, s):
        i = self.path._i(max(0.0, min(self.path.length, s)))
        return min(self.cap[i], self.cap[i + 1])


def stop_speed(d, limits=Limits):
    """The most a bus may do `d` metres before its stopping point: service
    braking to the creep, the creep, easing off the brake to rest."""
    if d <= 0:
        return 0.0
    if d <= limits.creep_m:
        return min(limits.creep_v, math.sqrt(2 * limits.final_decel * d) + 0.05)
    return math.sqrt(limits.creep_v ** 2 + 2 * limits.service * (d - limits.creep_m))


class Controller:
    """Steering and acceleration commands from an Intent, integrated every DT."""

    DT = 0.1           # integration step (s)
    STANLEY_K = 1.2    # cross-track gain
    STANLEY_SOFT = 1.0 # m/s softening at low speed

    def __init__(self, vehicle, driver=None, limits=Limits):
        self.v, self.driver, self.limits = vehicle, driver or Driver(), limits
        self.s = None            # progress along the current path (reference axle)
        self.path = None
        self.seen = deque()      # perceived hazards: (t_seen, gap, leader_v)
        self.stopped_since = 0.0
        self.conflict = None

    # --- steering ---
    def lookahead(self):
        """Speed-dependent preview for the heading term (m)."""
        return min(10.0, 1.0 + 0.6 * self.v.v)

    def steer_command(self, path, gear):
        veh, g = self.v, self.v.g
        if gear > 0:
            fx, fy = veh.front_axle
            s_f, e = path.project(fx, fy, (self.s or 0.0) + g.wheelbase)
            _, _, h = path.at(s_f)
            # Feed-forward: the steady steering for the path's curvature averaged
            # over a speed-dependent window centred on the front axle, so a
            # rate-limited wheel starts turning before the curve (a transition
            # curve of about that length). Feedback (Stanley): heading error
            # against the steady heading at that curvature, and cross-track error.
            half = self.lookahead() / 2
            n = 5
            k_bar = sum(path.curvature(s_f - half + 2 * half * i / (n - 1)) for i in range(n)) / n
            ff = math.asin(max(-0.99, min(0.99, g.wheelbase * k_bar)))
            psi = wrap(h - ff - veh.yaw)
            delta = ff + psi + math.atan2(self.STANLEY_K * e, self.STANLEY_SOFT + veh.v)
            return delta, s_f
        # reverse: the rear axle tracks the path backwards (pure pursuit behind it)
        s_r, e = path.project(veh.x, veh.y, self.s)
        ld = 4.0
        tx, ty, _ = path.at(s_r - ld)
        back = veh.yaw + math.pi
        alpha = wrap(math.atan2(ty - veh.y, tx - veh.x) - back)
        delta = -math.atan2(2 * g.wheelbase * math.sin(alpha), ld)
        return delta, s_r

    # --- speed ---
    def available_accel(self):
        veh, lim = self.v, self.limits
        a_power = lim.power_w / (veh.mass * max(veh.v, 2.0)) - G * lim.rolling
        return max(0.2, min(lim.accel, a_power))

    def follow_speed(self, gap, v_lead):
        """Fastest speed from which the driver can stop behind the vehicle ahead
        after perceiving it (reaction distance plus firm braking)."""
        lim = self.limits
        tau = lim.perception_s
        b = lim.firm
        room = max(0.0, gap - lim.gap) + v_lead ** 2 / (2 * b)
        v = -b * tau + math.sqrt((b * tau) ** 2 + 2 * b * room)
        # and keep the driver's preferred headway in steady following
        return max(0.0, min(v, max(0.0, gap - lim.gap) / max(0.5, self.driver.headway)))

    def step(self, intent, t, dt=None):
        """Advance the vehicle by `dt` (default DT). Returns a StepResult."""
        dt = dt or self.DT
        veh, g, lim = self.v, self.v.g, self.limits
        path = intent.path
        if path is not self.path:
            self.path, self.s = path, None
        if self.s is None:
            self.s, _ = path.project(veh.x, veh.y) if intent.gear > 0 else path.project(veh.x, veh.y)
        if intent.gear < 0 and not intent.authority:
            intent = Intent(path, 0.0, hold=True, area=intent.area, obstacles=intent.obstacles)
        gear = intent.gear
        if gear != veh.gear:
            if veh.v > 0.05:      # change direction only at rest
                gear = veh.gear
            else:
                veh.gear = gear
        # perceived hazards (the vehicle ahead), with the driver's perception delay
        if intent.leader_gap is not None:
            self.seen.append((t, intent.leader_gap, intent.leader_v))
        else:
            self.seen.append((t, None, 0.0))
        tau = lim.perception_s
        while len(self.seen) > 1 and self.seen[1][0] <= t - tau:
            self.seen.popleft()
        perceived = self.seen[0]
        # desired speed: every applicable limit
        env = SpeedEnvelope.of(path, g)
        s_ref = self.s
        if gear > 0:
            v_des = intent.speed_limit * self.driver.pace
            v_des = min(v_des, env.at(s_ref + g.wheelbase))
        else:
            v_des = min(intent.speed_limit, lim.reverse_v)
        emergency = False
        a_ff = 0.0
        if intent.hold:
            v_des = 0.0
        if intent.stop_s is not None:
            d = (intent.stop_s - s_ref) * gear
            # brakes take time to build at the jerk limit: start the curve that much earlier
            ramp = veh.v * max(0.0, lim.service + veh.a) / lim.jerk / 2 if d > lim.creep_m + 1 else 0.0
            d -= ramp
            prof = (lambda x: stop_speed(x, lim)) if intent.creep else (lambda x: math.sqrt(max(0.0, 2 * lim.service * x)))
            v_stop = prof(d)
            if v_stop <= v_des + 1e-6:
                v_des = v_stop
                # feed-forward: the deceleration the profile asks for at this speed
                eps = 0.25
                a_ff = min(0.0, veh.v * (prof(d - eps) - prof(d)) / eps) if d > eps else -lim.final_decel
        # acceleration command toward v_des
        t_resp = 1.0
        a_des = a_ff + (v_des - veh.v) / t_resp
        a_cap = self.available_accel() if gear > 0 else 0.4
        a_des = max(-lim.firm, min(a_cap, a_des))
        # the vehicle ahead, as the driver perceived it `perception_s` ago:
        # Intelligent Driver Model interaction term (comfortable braking b =
        # service, desired time gap = the driver's headway, standstill gap)
        need = 0.0
        if perceived[1] is not None:
            gap = max(0.1, perceived[1] - (veh.v - perceived[2]) * min(lim.perception_s, t - perceived[0]))
            dv = veh.v - perceived[2]
            s_star = lim.gap + veh.v * self.driver.headway + veh.v * dv / (2 * math.sqrt(lim.accel * lim.service))
            a_idm = lim.accel * (1 - (max(lim.gap, s_star) / gap) ** 2)
            a_des = min(a_des, a_idm)
            # critical: more than service braking needed to stop behind it
            if dv > 0:
                room = gap - lim.gap * 0.6
                need = dv * (veh.v + perceived[2]) / (2 * room) if room > 0.05 else lim.emergency
        if intent.stop_s is not None:
            d = (intent.stop_s - s_ref) * gear
            if d > 0.05 and veh.v > lim.creep_v:
                need = max(need, (veh.v ** 2 - lim.creep_v ** 2) / (2 * max(0.1, d - lim.creep_m * 0.5)))
        if need > lim.service * 1.05:
            a_des = min(a_des, -min(need * 1.1, lim.emergency))
            emergency = need > lim.firm
        a_des = max(-(lim.emergency if emergency else lim.firm), a_des)
        if veh.v < 0.02 and v_des < 0.02:
            a_des = 0.0
        if intent.stop_s is not None and (intent.stop_s - s_ref) * gear < lim.creep_m + 2.0 and veh.v > 0.3:
            a_des = min(a_des, 0.0)       # into the stopping position: roll or brake, never re-accelerate
        elif intent.stop_s is not None and (intent.stop_s - s_ref) * gear < lim.creep_m + 2.0:
            a_des = min(a_des, 0.15)
        # moving off: the driver's reaction before the bus moves
        if veh.v < 0.02 and a_des > 0:
            self.stopped_since += dt
            if self.stopped_since < self.driver.reaction:
                a_des = 0.0
        elif veh.v >= 0.02:
            self.stopped_since = 0.0
        jerk = lim.jerk_emergency if emergency else lim.jerk
        a = veh.a + max(-jerk * dt, min(jerk * dt, a_des - veh.a))
        # steering
        delta_cmd, _ = self.steer_command(path, gear)
        delta_cmd = max(-g.max_steer, min(g.max_steer, delta_cmd))
        rate = g.max_steer_rate * dt
        steer = veh.steer + max(-rate, min(rate, delta_cmd - veh.steer))
        # integrate (midpoint)
        v_new = veh.v + a * dt
        if v_new < 0:
            v_new, a = 0.0, 0.0
        dist = (veh.v + v_new) / 2 * dt
        # a stop exactly where asked
        if intent.stop_s is not None:
            d = (intent.stop_s - s_ref) * gear
            if dist >= d - 0.01 and d < lim.creep_m:
                dist = max(0.0, d)
                v_new, a = 0.0, 0.0
        old = (veh.x, veh.y, veh.yaw, veh.steer)
        yaw_mid = veh.yaw + gear * dist * math.tan((veh.steer + steer) / 2) / g.wheelbase / 2
        nx = veh.x + gear * dist * math.cos(yaw_mid)
        ny = veh.y + gear * dist * math.sin(yaw_mid)
        nyaw = wrap(veh.yaw + gear * dist * math.tan((veh.steer + steer) / 2) / g.wheelbase)
        veh.x, veh.y, veh.yaw, veh.steer = nx, ny, nyaw, steer
        # the swept footprint must stay in the area and clear of other vehicles
        conflict = None
        if dist > 0:
            if intent.area is not None:
                bad = intent.area.violations(veh.outline(), self.s)
                if bad:
                    worst = max(bad, key=lambda b: b["by_m"])
                    conflict = {"kind": "area", "area": intent.area.name, **worst}
            if conflict is None:
                fp = veh.footprint(0.05)
                for oid, poly in intent.obstacles:
                    if _sat(fp, poly):
                        conflict = {"kind": "vehicle", "with": oid}
                        break
        if conflict is not None:
            veh.x, veh.y, veh.yaw, veh.steer = old
            dist, v_new, a = 0.0, 0.0, 0.0
            self.conflict = conflict
        else:
            self.conflict = None
        jerk_now = abs(a - veh.a) / dt
        veh.jerk_peak = max(veh.jerk_peak, jerk_now) if v_new > 0.05 else veh.jerk_peak
        veh.v, veh.a = v_new, a
        veh.odo += dist
        veh.t = t + dt
        if gear > 0:
            self.s, d_lat = path.project(veh.x, veh.y, self.s)
        else:
            self.s, d_lat = path.project(veh.x, veh.y, self.s)
        return StepResult(self.s, d_lat, dist, conflict, veh.v < 0.02, emergency)


# --- planning a driveable lane path ------------------------------------------------------------
def _dp(points, tol):
    """Douglas-Peucker simplification (iterative)."""
    n = len(points)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = points[a], points[b]
        dx, dy = x2 - x1, y2 - y1
        l2 = dx * dx + dy * dy
        best, idx = -1.0, None
        for k in range(a + 1, b):
            px, py = points[k]
            if l2 == 0:
                d = math.hypot(px - x1, py - y1)
            else:
                f = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / l2))
                d = math.hypot(px - x1 - f * dx, py - y1 - f * dy)
            if d > best:
                best, idx = d, k
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [points[k] for k in range(n) if keep[k]]


def _intersect(p1, p2, p3, p4):
    """Intersection of lines p1->p2 and p3->p4, with the parameters along each."""
    d1 = (p2[0] - p1[0], p2[1] - p1[1])
    d2 = (p4[0] - p3[0], p4[1] - p3[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9:
        return None
    u = ((p3[0] - p1[0]) * d2[1] - (p3[1] - p1[1]) * d2[0]) / den
    w = ((p3[0] - p1[0]) * d1[1] - (p3[1] - p1[1]) * d1[0]) / den
    return (p1[0] + d1[0] * u, p1[1] + d1[1] * u), u, w


def fillet(points, radius, tol=0.6):
    """A path a vehicle can steer: the polyline simplified to its corners, each
    corner rounded with an arc of at least `radius`. Corners too close for
    their arcs are merged into one turn at the intersection of the outer legs.
    Returns (points every ~1 m, corrections, infeasible)."""
    v = _dp([tuple(p) for p in points], tol)

    def turn(i):
        a, b, c = v[i - 1], v[i], v[i + 1]
        h1 = math.atan2(b[1] - a[1], b[0] - a[0])
        h2 = math.atan2(c[1] - b[1], c[0] - b[0])
        return wrap(h2 - h1)

    def tangent(i):
        th = abs(turn(i))
        return radius * math.tan(min(th, math.radians(179)) / 2)

    corrections, infeasible = [], []
    for _ in range(len(v) * 2):
        worst, at = 1.0, None
        for i in range(1, len(v) - 1):
            seg_prev = math.dist(v[i - 1], v[i])
            seg_next = math.dist(v[i], v[i + 1])
            need_prev = tangent(i) + (tangent(i - 1) if i - 1 >= 1 else 0.0)
            need_next = tangent(i) + (tangent(i + 1) if i + 1 <= len(v) - 2 else 0.0)
            for need, seg, side in ((need_prev, seg_prev, -1), (need_next, seg_next, 1)):
                if seg > 0 and need / seg > worst:
                    worst, at = need / seg, (i, side)
        if at is None:
            break
        i, side = at
        a, b = (i - 1, i) if side < 0 else (i, i + 1)
        if a >= 1 and b <= len(v) - 2:
            hit = _intersect(v[a - 1], v[a], v[b + 1], v[b])
            if hit is not None and hit[1] > 0.5 and hit[2] > 0.5:
                corrections.append({"at": [round(c, 1) for c in hit[0]], "merged": 2})
                v[a:b + 1] = [hit[0]]
                continue
        # cannot merge (a reversal, or at the end of the path): the turn stays
        # as tight as the room allows, and is reported
        infeasible.append({"at": [round(c, 1) for c in v[i]], "turn_deg": round(math.degrees(abs(turn(i))), 0)})
        break
    out = [v[0]]
    for i in range(1, len(v) - 1):
        a, b, c = v[i - 1], v[i], v[i + 1]
        th = turn(i)
        la, lc = math.dist(a, b), math.dist(b, c)
        t_len = min(tangent(i), la * (0.5 if i > 1 else 1.0), lc * (0.5 if i < len(v) - 2 else 1.0))
        r = t_len / math.tan(abs(th) / 2) if abs(th) > 1e-4 else 0.0
        if r < radius * 0.999 and abs(th) > math.radians(2):
            infeasible.append({"at": [round(x, 1) for x in b], "radius_m": round(r, 1)})
        if abs(th) < 1e-4 or t_len < 1e-3:
            out.append(b)
            continue
        h1 = math.atan2(b[1] - a[1], b[0] - a[0])
        p1 = (b[0] - math.cos(h1) * t_len, b[1] - math.sin(h1) * t_len)
        side = 1 if th > 0 else -1
        cx, cy = p1[0] - math.sin(h1) * r * side, p1[1] + math.cos(h1) * r * side
        start = math.atan2(p1[1] - cy, p1[0] - cx)
        steps = max(2, int(abs(th) * r / 1.0) + 1)
        for k in range(steps + 1):
            ang = start + th * k / steps
            out.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    out.append(v[-1])
    return out, corrections, infeasible


def lane_path(centre, offset, geometry, name="", margin=1.05):
    """The path a driver of `geometry` follows: the reference line moved
    `offset` metres to the right (a number or a function of s), with every
    corner rounded to at least the class's smallest front-axle radius times
    `margin` (fillet). Returns (Path, report): the corners corrected and any
    the vehicle still cannot make."""
    shifted = centre.shifted(offset) if offset else centre
    radius = geometry.min_radius_front * margin
    pts, merged, infeasible = fillet(list(zip(shifted.x, shifted.y)), radius)
    path = Path(pts, name)
    before = [k for k in shifted.k]
    tight = sum(1 for i in range(1, len(before) - 1) if abs(before[i]) > 1 / radius and abs(before[i - 1]) <= 1 / radius)
    for c in merged + infeasible:
        c["lonlat"] = [round(x, 6) for x in to_lonlat(*c.pop("at"))]
    return path, {"class": geometry.key, "min_front_radius_m": round(radius, 2), "tight_corners_in_source": tight,
                  "corners_merged": merged, "infeasible": infeasible}


# --- feasibility of a planned movement -------------------------------------------------------
def sweep(path, geometry, area=None, obstacles=(), s0=0.0, s1=None, every=0.5):
    """The body's sweep along a path at steady tracking (front axle on the
    path): the first conflicts with the area's bounds or other vehicles, and
    the steering the path needs. Used to plan before moving."""
    s1 = path.length if s1 is None else s1
    veh = Vehicle("plan", geometry)
    issues = []
    steer_needed = 0.0
    s = max(s0, 0.0)
    while s <= s1:
        place_on_path(veh, path, s)
        steer_needed = max(steer_needed, abs(veh.steer))
        k = abs(path.curvature(s))
        if k > 0 and geometry.wheelbase * k > math.sin(geometry.max_steer):
            issues.append({"s": round(s, 1), "kind": "steering",
                           "detail": f"radius {1 / k:.1f} m tighter than the {geometry.min_radius_front:.1f} m the front axle can follow"})
        if area is not None:
            for b in area.violations(veh.outline(), s):
                issues.append({"s": round(s, 1), "kind": "area", "detail": f"{b['side']} by {b['by_m']:.2f} m",
                               "side": b["side"], "by_m": b["by_m"]})
                break
        fp = veh.footprint(0.05)
        for oid, poly in obstacles:
            if _sat(fp, poly):
                issues.append({"s": round(s, 1), "kind": "vehicle", "detail": oid, "with": oid})
                break
        s += every
    return {"ok": not issues, "issues": issues, "max_steer_deg": round(math.degrees(steer_needed), 1)}


def clear_distance(path, geometry, s_from, obstacles, horizon=60.0, every=1.0):
    """Metres the front axle can still advance along `path` before the body
    (placed as it will track) touches one of `obstacles`; None if clear."""
    veh = Vehicle("probe", geometry)
    s = s_from
    while s <= min(path.length, s_from + horizon):
        place_on_path(veh, path, s)
        fp = veh.footprint(0.1)
        for oid, poly in obstacles:
            if _sat(fp, poly):
                return max(0.0, s - s_from - every), oid
        s += every
    return None


# --- recorded motion ------------------------------------------------------------------------
class Recorder:
    """[t, x, y, yaw, v, a, steer, odo] every `every` seconds (and at each
    change of gear or stop), metres on the local plane around LON0/LAT0."""

    def __init__(self, every=0.5):
        self.every = every
        self.samples = []
        self._next = None

    def add(self, veh, t, force=False):
        if not force and self._next is not None and t + 1e-9 < self._next:
            return
        self.samples.append([round(t, 2), round(veh.x, 3), round(veh.y, 3), round(veh.yaw, 4), round(veh.v, 3),
                             round(veh.a, 3), round(veh.steer, 4), round(veh.odo, 2), veh.gear])
        self._next = t + self.every

    def gap(self, t):
        """Mark a gap (the bus left the modelled network)."""
        self._next = None


def hermite(a, b, t):
    """Pose between two samples, as the map reconstructs it: cubic Hermite on
    position with the sampled velocities, linear steering and acceleration."""
    dt = b[0] - a[0]
    if dt <= 0:
        return b
    u = (t - a[0]) / dt
    ga, gb = (a[8] if len(a) > 8 else 1), (b[8] if len(b) > 8 else 1)
    vax, vay = a[4] * math.cos(a[3]) * ga * dt, a[4] * math.sin(a[3]) * ga * dt
    vbx, vby = b[4] * math.cos(b[3]) * gb * dt, b[4] * math.sin(b[3]) * gb * dt
    h00, h10, h01, h11 = 2 * u ** 3 - 3 * u ** 2 + 1, u ** 3 - 2 * u ** 2 + u, -2 * u ** 3 + 3 * u ** 2, u ** 3 - u ** 2
    x = h00 * a[1] + h10 * vax + h01 * b[1] + h11 * vbx
    y = h00 * a[2] + h10 * vay + h01 * b[2] + h11 * vby
    yaw = a[3] + wrap(b[3] - a[3]) * u
    return [t, x, y, yaw, a[4] + (b[4] - a[4]) * u, a[5] + (b[5] - a[5]) * u, a[6] + (b[6] - a[6]) * u,
            a[7] + (b[7] - a[7]) * u, gb]


def simplify(samples, pos_tol=0.04, yaw_tol=0.004, v_tol=0.05, max_span=20.0):
    """Drop samples the map's Hermite reconstruction reproduces within tolerance."""
    if len(samples) <= 2:
        return list(samples)
    out = [samples[0]]
    k = 0
    j = 1
    n = len(samples)
    while j < n:
        if j == n - 1:
            out.append(samples[j])
            break
        cand = samples[j + 1]
        ok = (cand[0] - samples[k][0] <= max_span and cand[0] - samples[j][0] <= 1.01
              and (len(cand) < 9 or cand[8] == samples[k][8]))
        if ok:
            for m in range(k + 1, j + 1):
                x = samples[m]
                p = hermite(samples[k], cand, x[0])
                if (math.hypot(p[1] - x[1], p[2] - x[2]) > pos_tol or abs(wrap(p[3] - x[3])) > yaw_tol
                        or abs(p[4] - x[4]) > v_tol or abs(p[6] - x[6]) > 0.02):
                    ok = False
                    break
        if ok:
            j += 1
            continue
        out.append(samples[j])
        k = j
        j += 1
    return out
