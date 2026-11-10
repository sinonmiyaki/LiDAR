"""Keep the scan window in tracking space and project it into each mirror eye."""

from dataclasses import dataclass
from math import hypot, sqrt

import openvr as vr

from gesture import head_local


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def unit(vector):
    length = sqrt(dot(vector, vector))
    return tuple(x / length for x in vector)


def reference_axes(head):
    return tuple(tuple(head[row][axis] for row in range(3)) for axis in (1, 2))


@dataclass(frozen=True)
class ScanWindow:
    center: tuple
    right: tuple
    up: tuple
    normal: tuple
    width: float

    @classmethod
    def between(cls, left, right, axes):
        up, normal = axes
        span = tuple(b - a for a, b in zip(left, right))
        if dot(span, span) > 0.0001:
            direction = unit(span)
            depth = dot(normal, direction)
            # Tilt with the difference in hand depth. Keep the window from
            # turning edge-on when one hand is directly behind the other.
            weight = min(1.0, max(0.0, (1.0 - depth * depth) / 0.25))
            normal = unit(tuple(n - d * depth * weight for n, d in zip(normal, direction)))
        right_axis = unit(cross(up, normal))
        up_axis = cross(normal, right_axis)
        center = tuple((a + b) / 2 for a, b in zip(left, right))
        width = max(0.065, abs(dot(span, right_axis)), abs(dot(span, up_axis)))
        return cls(center, right_axis, up_axis, normal, width)

    def place(self, api, handle):
        matrix = vr.HmdMatrix34_t()
        for row in range(3):
            matrix[row][:] = self.right[row], self.up[row], self.normal[row], self.center[row]
        api.setOverlayTransformAbsolute(handle, vr.TrackingUniverseStanding, matrix)
        api.setOverlayWidthInMeters(handle, self.width)

    def corners(self):
        half = self.width / 2
        return tuple(tuple(c + half * (x * r + y * u) for c, r, u in zip(self.center, self.right, self.up))
                     for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1)))


def project_window(system, eye, window, head):
    eye_to_head = system.getEyeToHeadTransform(eye)
    projection = system.getProjectionMatrix(eye, 0.05, 20.0)
    eye_offset = tuple(eye_to_head[row][3] for row in range(3))
    head_points = [head_local(head, point) for point in (*window.corners(), window.center)]
    projections = []

    for distant in (False, True):
        points = []
        for point in head_points:
            if distant:
                # The physical square is close to the eyes. A QR beyond it is
                # aimed at along a ray from between the eyes, with less parallax.
                point = tuple(a + b for a, b in zip(point, eye_offset))
            eye_point = (*head_local(eye_to_head, point), 1.0)
            x, y, w = (dot(projection[row], eye_point) for row in (0, 1, 3))
            if w <= 0.001:
                break
            points.append((0.5 + 0.5 * x / w, 0.5 - 0.5 * y / w))
        if len(points) == len(head_points):
            projections.append((points[:4], points[4]))
    return projections


def capture_bounds(projections):
    xs, ys = zip(*(point for corners, _ in projections for point in corners))
    pad_x = max(0.04, (max(xs) - min(xs)) * 0.25)
    pad_y = max(0.04, (max(ys) - min(ys)) * 0.25)
    return (max(0.0, min(xs) - pad_x), max(0.0, min(ys) - pad_y),
            min(1.0, max(xs) + pad_x), min(1.0, max(ys) + pad_y))


def aim_distance(point, projections):
    for corners, _ in projections:
        edges = []
        for a, b in zip(corners, corners[1:] + corners[:1]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = hypot(dx, dy)
            if length > 1e-6:
                edges.append((dx * (point[1] - a[1]) - dy * (point[0] - a[0])) / length)
        if edges and (min(edges) >= -0.016 or max(edges) <= 0.016):
            # Rank against the sight line between the eyes consistently.
            center = projections[-1][1]
            return hypot(point[0] - center[0], point[1] - center[1])
    return None
