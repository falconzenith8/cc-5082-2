"""Continuous geometry primitives for directed checkpoint zones.

A zone is a planar convex/concave polygon living on an oriented plane, expanded
into a thin slab by ``thickness`` (half depth).  All crossing decisions are
derived from continuous segment-vs-zone intersection, never from per-frame
point proximity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

PLANE_EPS = 1e-7


class Vec3:
    __slots__ = ("x", "y", "z")

    def __init__(self, x: float, y: float, z: float):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    @classmethod
    def from_iter(cls, values: Iterable[float]) -> "Vec3":
        x, y, z = values
        return cls(x, y, z)

    def to_list(self) -> List[float]:
        return [self.x, self.y, self.z]

    def copy(self) -> "Vec3":
        return Vec3(self.x, self.y, self.z)

    def __add__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, scalar: float) -> "Vec3":
        return Vec3(self.x * scalar, self.y * scalar, self.z * scalar)

    __rmul__ = __mul__

    def dot(self, other: "Vec3") -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: "Vec3") -> "Vec3":
        return Vec3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def length(self) -> float:
        return math.sqrt(self.dot(self))

    def distance(self, other: "Vec3") -> float:
        return (self - other).length()

    def normalize(self) -> "Vec3":
        n = self.length()
        if n < PLANE_EPS:
            raise ValueError("cannot normalize near-zero vector")
        return self * (1.0 / n)

    def almost_equal(self, other: "Vec3", tol: float = 1e-6) -> bool:
        return self.distance(other) <= tol

    def __repr__(self) -> str:
        return f"Vec3({self.x:.4f}, {self.y:.4f}, {self.z:.4f})"


@dataclass
class SegmentAnalysis:
    """Result of intersecting one movement segment with a zone."""

    crossed: bool
    direction: Optional[int]  # +1 forward (back->front), -1 reverse, None
    hit: Optional[Vec3]
    t: Optional[float]  # interpolation parameter along the segment
    inside: bool  # hit lies within the polygon (vs. outside edge)
    edge_distance: Optional[float]  # in-plane distance to polygon border
    side_a: int  # -1 / 0 / +1
    side_b: int
    edge_contact: bool  # an endpoint sits in the slab next to the polygon edge


class Plane:
    def __init__(self, point: Vec3, normal: Vec3):
        n = normal.normalize()
        self.point = point
        self.normal = n

    def signed_distance(self, p: Vec3) -> float:
        return (p - self.point).dot(self.normal)

    def side(self, p: Vec3) -> int:
        sd = self.signed_distance(p)
        if sd > PLANE_EPS:
            return 1
        if sd < -PLANE_EPS:
            return -1
        return 0


class Zone:
    """Oriented checkpoint polygon with a finite slab thickness."""

    def __init__(
        self,
        zone_id: str,
        name: str,
        points: Sequence[Vec3],
        normal: Vec3,
        thickness: float,
        successors: Sequence[str],
    ):
        if len(points) < 3:
            raise ValueError(f"zone {zone_id}: polygon needs >= 3 points")
        if thickness <= 0:
            raise ValueError(f"zone {zone_id}: thickness must be positive")
        self.id = zone_id
        self.name = name
        self.points = list(points)
        self.thickness = float(thickness)
        self.successors = list(successors)
        self.plane = Plane(points[0], normal)
        self._validate_coplanar(normal)
        self._u, self._v = self._build_basis()
        self._poly2d = [self._project2d(p) for p in self.points]

    def _validate_coplanar(self, normal: Vec3) -> None:
        for p in self.points[1:]:
            sd = self.plane.signed_distance(p)
            if abs(sd) > 1e-4:
                raise ValueError(
                    f"zone {self.id}: points not coplanar (sd={sd:.4f})"
                )
        if abs(abs(normal.normalize().length()) - 1.0) > 1e-9:
            raise ValueError(f"zone {self.id}: bad normal")

    def _build_basis(self) -> Tuple[Vec3, Vec3]:
        n = self.plane.normal
        ref = Vec3(0, 1, 0) if abs(n.y) < 0.9 else Vec3(1, 0, 0)
        u = ref.cross(n).normalize()
        v = n.cross(u).normalize()
        return u, v

    def _project2d(self, p: Vec3) -> Tuple[float, float]:
        d = p - self.plane.point
        return d.dot(self._u), d.dot(self._v)

    def gate_center(self) -> Vec3:
        cx = sum(p.x for p in self.points) / len(self.points)
        cy = sum(p.y for p in self.points) / len(self.points)
        cz = sum(p.z for p in self.points) / len(self.points)
        return Vec3(cx, cy, cz)

    def in_plane_distance_to_gate(self, p: Vec3) -> float:
        """Lateral distance from a (plane) hit point to the gate center."""
        ux, uy = self._project2d(p)
        cx, cy = self._project2d(self.gate_center())
        return math.hypot(ux - cx, uy - cy)

    def gate_radius(self) -> float:
        c = self.gate_center()
        return max(p.distance(c) for p in self.points)

    def signed_distance(self, p: Vec3) -> float:
        return self.plane.signed_distance(p)

    def in_slab(self, p: Vec3) -> bool:
        return abs(self.plane.signed_distance(p)) <= self.thickness

    def contains(self, p: Vec3, tol: float = 1e-4) -> Tuple[bool, float]:
        """In-plane polygon inclusion; returns (inside, edge distance)."""
        qx, qy = self._project2d(p)
        poly = self._poly2d
        inside = False
        n = len(poly)
        min_edge = math.inf
        j = n - 1
        for i in range(n):
            ax, ay = poly[i]
            bx, by = poly[j]
            if (ay > qy) != (by > qy):
                x_cross = ax + (qy - ay) * (bx - ax) / (by - ay)
                if x_cross > qx:
                    inside = not inside
            # distance to segment (ax,ay)-(bx,by)
            dx, dy = bx - ax, by - ay
            seg_len2 = dx * dx + dy * dy
            if seg_len2 > 0:
                tt = max(0.0, min(1.0, ((qx - ax) * dx + (qy - ay) * dy) / seg_len2))
                ex, ey = ax + tt * dx, ay + tt * dy
                min_edge = min(min_edge, math.hypot(qx - ex, qy - ey))
            j = i
        if min_edge <= tol:
            inside = True
        return inside, min_edge

    def analyze_segment(self, a: Vec3, b: Vec3) -> SegmentAnalysis:
        sda = self.plane.signed_distance(a)
        sdb = self.plane.signed_distance(b)
        side_a = self.plane.side(a)
        side_b = self.plane.side(b)

        edge_contact = False
        for p, sd in ((a, sda), (b, sdb)):
            if abs(sd) <= self.thickness:
                inside, edge_dist = self.contains(p)
                if not inside:
                    edge_contact = True

        # strict crossing: opposite strict signs -> continuous plane intersection
        if side_a != 0 and side_b != 0 and side_a != side_b:
            t = sda / (sda - sdb)
            hit = a + (b - a) * t
            inside, edge_dist = self.contains(hit)
            return SegmentAnalysis(
                crossed=True,
                direction=1 if side_a < 0 and side_b > 0 else -1,
                hit=hit,
                t=t,
                inside=inside,
                edge_distance=edge_dist,
                side_a=side_a,
                side_b=side_b,
                edge_contact=edge_contact,
            )

        # boundary landing: endpoint exactly on the plane, other side strict
        hit = None
        t_val = None
        direction = None
        if side_a == 0 and side_b != 0:
            hit, t_val, direction = a, 0.0, side_b
        elif side_b == 0 and side_a != 0:
            hit, t_val, direction = b, 1.0, -side_a
        if hit is not None:
            inside, edge_dist = self.contains(hit)
            return SegmentAnalysis(
                crossed=True,
                direction=direction,
                hit=hit,
                t=t_val,
                inside=inside,
                edge_distance=edge_dist,
                side_a=side_a,
                side_b=side_b,
                edge_contact=edge_contact,
            )

        return SegmentAnalysis(
            crossed=False,
            direction=None,
            hit=None,
            t=None,
            inside=False,
            edge_distance=None,
            side_a=side_a,
            side_b=side_b,
            edge_contact=edge_contact,
        )
