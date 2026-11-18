"""Small planar-geometry helpers for the Waterloo corridor builder.

Coordinates are converted to a local equirectangular frame in metres. Over the
6 km corridor the scale error is far below the precision of the source data.
"""
import math

LAT0 = 43.465
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = M_PER_DEG_LAT * math.cos(math.radians(LAT0))


def to_xy(lat, lon):
    return (lon * M_PER_DEG_LON, lat * M_PER_DEG_LAT)


def to_latlon(x, y):
    return (y / M_PER_DEG_LAT, x / M_PER_DEG_LON)


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def heading(a, b):
    """Compass heading in degrees (0 = north, 90 = east)."""
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360


def turn(h0, h1):
    return (h1 - h0 + 540) % 360 - 180


def project(p, a, b):
    """Return (t, closest point, distance) of p projected on segment ab."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    q = (a[0] + dx * t, a[1] + dy * t)
    return t, q, dist(p, q)


def cumulative(points):
    out = [0.0]
    for a, b in zip(points, points[1:]):
        out.append(out[-1] + dist(a, b))
    return out


def point_at(points, cum, s):
    s = max(0.0, min(cum[-1], s))
    lo, hi = 0, len(cum) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cum[mid] <= s:
            lo = mid
        else:
            hi = mid
    span = cum[hi] - cum[lo]
    f = 0.0 if span == 0 else (s - cum[lo]) / span
    a, b = points[lo], points[hi]
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


def resample(points, step):
    cum = cumulative(points)
    n = max(1, int(math.ceil(cum[-1] / step)))
    return [point_at(points, cum, cum[-1] * i / n) for i in range(n + 1)]


def dedupe(points, tolerance=0.05):
    out = [points[0]]
    for p in points[1:]:
        if dist(p, out[-1]) > tolerance:
            out.append(p)
    return out


def fillet(points, radius, min_turn=12.0):
    """Round polyline corners with circular arcs.

    The arc is tangent to both legs, so the path stays inside the junction
    instead of cutting across the block. The radius shrinks when a leg is too
    short to hold the tangent points.
    """
    points = dedupe(points)
    if len(points) < 3:
        return points
    out = [points[0]]
    budget = [dist(a, b) for a, b in zip(points, points[1:])]
    for i in range(1, len(points) - 1):
        a, b, c = out[-1], points[i], points[i + 1]
        h0, h1 = heading(points[i - 1], b), heading(b, c)
        angle = abs(turn(h0, h1))
        if angle < min_turn:
            out.append(b)
            continue
        half = math.radians(angle) / 2
        r = radius
        tangent = r * math.tan(half)
        limit = min(budget[i - 1] * 0.48, budget[i] * 0.48)
        if tangent > limit:
            tangent = limit
            r = tangent / math.tan(half)
        u0 = ((b[0] - points[i - 1][0]), (b[1] - points[i - 1][1]))
        l0 = math.hypot(*u0)
        u0 = (u0[0] / l0, u0[1] / l0)
        u1 = ((c[0] - b[0]), (c[1] - b[1]))
        l1 = math.hypot(*u1)
        u1 = (u1[0] / l1, u1[1] / l1)
        p0 = (b[0] - u0[0] * tangent, b[1] - u0[1] * tangent)
        p1 = (b[0] + u1[0] * tangent, b[1] + u1[1] * tangent)
        # Arc centre lies along the inward normal of the first leg.
        side = 1 if (u0[0] * u1[1] - u0[1] * u1[0]) > 0 else -1
        n0 = (-u0[1] * side, u0[0] * side)
        centre = (p0[0] + n0[0] * r, p0[1] + n0[1] * r)
        a0 = math.atan2(p0[1] - centre[1], p0[0] - centre[0])
        a1 = math.atan2(p1[1] - centre[1], p1[0] - centre[0])
        sweep = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        steps = max(3, int(abs(sweep) / math.radians(7)))
        for k in range(steps + 1):
            ang = a0 + sweep * k / steps
            out.append((centre[0] + r * math.cos(ang), centre[1] + r * math.sin(ang)))
        budget[i] -= tangent
    out.append(points[-1])
    return dedupe(out)


def offset(points, metres):
    """Offset a polyline to the right of travel by `metres` (mitred joins)."""
    out = []
    for i, p in enumerate(points):
        a = points[max(0, i - 1)]
        b = points[min(len(points) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        out.append((p[0] + dy / length * metres, p[1] - dx / length * metres))
    return out


def simplify(points, tolerance):
    """Ramer-Douglas-Peucker simplification (keeps arcs, drops straight runs)."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        worst, index = 0.0, None
        for k in range(i + 1, j):
            d = project(points[k], points[i], points[j])[2]
            if d > worst:
                worst, index = d, k
        if index is not None and worst > tolerance:
            keep[index] = True
            stack.extend([(i, index), (index, j)])
    return [p for p, k in zip(points, keep) if k]
