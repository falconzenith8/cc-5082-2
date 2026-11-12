import math
import unittest

from racing_judge.geometry import Vec3, Zone


def rect_zone(zid, center, normal, half_w=3.0, half_h=2.5, thickness=0.6,
              successors=None):
    cx, cy, cz = center
    n = Vec3(*normal).normalize()
    ref = Vec3(0, 1, 0) if abs(n.y) < 0.9 else Vec3(1, 0, 0)
    u = ref.cross(n).normalize()
    v = n.cross(u).normalize()
    c = Vec3(cx, cy, cz)
    pts = [
        c - u * half_w - v * half_h,
        c + u * half_w - v * half_h,
        c + u * half_w + v * half_h,
        c - u * half_w + v * half_h,
    ]
    return Zone(zid, zid, pts, n, thickness, successors or [])


class GeometryTests(unittest.TestCase):
    def test_forward_and_reverse_crossing(self):
        z = rect_zone("g", (0, 0, 0), (0, 0, 1))
        a = Vec3(0, 0, -5)
        b = Vec3(0, 0, 5)
        r = z.analyze_segment(a, b)
        self.assertTrue(r.crossed)
        self.assertEqual(r.direction, 1)
        self.assertTrue(r.inside)
        self.assertAlmostEqual(r.t, 0.5)

        r2 = z.analyze_segment(b, a)
        self.assertTrue(r2.crossed)
        self.assertEqual(r2.direction, -1)

    def test_thin_slab_tunneling_still_detected(self):
        # very thin zone, segment jumps from far behind to far ahead in one step
        z = rect_zone("g", (0, 0, 0), (0, 0, 1), thickness=0.05)
        r = z.analyze_segment(Vec3(0, 0, -20), Vec3(0, 0, 20))
        self.assertTrue(r.crossed)
        self.assertEqual(r.direction, 1)
        self.assertTrue(r.inside)

    def test_miss_polygon_is_not_inside(self):
        z = rect_zone("g", (0, 0, 0), (0, 0, 1), half_w=3.0)
        r = z.analyze_segment(Vec3(10, 0, -5), Vec3(10, 0, 5))
        self.assertTrue(r.crossed)
        self.assertFalse(r.inside)

    def test_grazing_no_cross_when_both_sides_equal(self):
        z = rect_zone("g", (0, 0, 0), (0, 0, 1))
        r = z.analyze_segment(Vec3(0, 0, 1), Vec3(2, 0, 1))
        self.assertFalse(r.crossed)
        self.assertTrue(r.edge_contact or r.edge_distance is None)

    def test_on_plane_endpoint_boundary(self):
        z = rect_zone("g", (0, 0, 0), (0, 0, 1))
        r = z.analyze_segment(Vec3(0, 0, -2), Vec3(0, 0, 0))
        self.assertTrue(r.crossed)
        self.assertEqual(r.direction, 1)

    def test_coplanar_validation(self):
        with self.assertRaises(ValueError):
            Zone("bad", "bad",
                 [Vec3(0, 0, 0), Vec3(1, 0, 0), Vec3(0, 1, 1)],
                 Vec3(0, 0, 1), 0.5, [])


if __name__ == "__main__":
    unittest.main()
