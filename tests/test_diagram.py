"""Tests of the direction diagram's geometry (no Qt)."""

from __future__ import annotations

import math
import unittest

from quadsens import diagram
from quadsens.diagram import EXAGGERATION, MAX_RADIUS, MIN_RADIUS, geometry
from quadsens.model import Scalars

# A real result: right, left, down, up.
REAL = Scalars(0.9855, 1.0012, 0.9953, 1.0016)


def arrows(scalars: Scalars) -> dict[str, diagram.Arrow]:
    return {arrow.name: arrow for arrow in geometry(scalars).arrows}


class RadiusTest(unittest.TestCase):
    def test_one_fixed_factor(self) -> None:
        self.assertEqual(EXAGGERATION, 20.0)
        self.assertAlmostEqual(diagram.drawn_radius(1.0), 1.0)
        self.assertAlmostEqual(diagram.drawn_radius(1.01), 1.2)
        self.assertAlmostEqual(diagram.drawn_radius(0.99), 0.8)
        self.assertAlmostEqual(diagram.drawn_radius(1.02) - 1.0,
                               2 * (diagram.drawn_radius(1.01) - 1.0))  # linear

    def test_clamping(self) -> None:
        self.assertEqual(diagram.drawn_radius(10.0), MAX_RADIUS)
        self.assertEqual(diagram.drawn_radius(0.1), MIN_RADIUS)
        self.assertTrue(diagram.is_clamped(10.0))
        self.assertTrue(diagram.is_clamped(0.1))
        self.assertTrue(diagram.is_clamped(1.08))
        self.assertFalse(diagram.is_clamped(1.03))
        self.assertFalse(diagram.is_clamped(0.9855))
        self.assertEqual(diagram.clamp_limit_percent(), 3.5)
        self.assertGreater(MIN_RADIUS, 0.2)   # never collapses into the centre

    def test_side_of_ring(self) -> None:
        self.assertEqual(diagram.side_of_ring(1.0), diagram.ON)
        self.assertEqual(diagram.side_of_ring(1.00004), diagram.ON)
        self.assertEqual(diagram.side_of_ring(1.0001), diagram.OUTSIDE)
        self.assertEqual(diagram.side_of_ring(0.9999), diagram.INSIDE)


class GeometryTest(unittest.TestCase):
    def test_real_values(self) -> None:
        got = arrows(REAL)
        self.assertEqual(got["Down"].side, diagram.INSIDE)
        self.assertLess(got["Down"].radius, 1.0)
        self.assertAlmostEqual(got["Down"].radius, 0.906)
        self.assertEqual((got["Up"].side, got["Left"].side),
                         (diagram.OUTSIDE, diagram.OUTSIDE))
        self.assertAlmostEqual(got["Up"].radius, 1.032)     # just outside
        self.assertAlmostEqual(got["Left"].radius, 1.024)
        self.assertEqual(got["Right"].side, diagram.INSIDE)
        self.assertAlmostEqual(got["Right"].radius, 0.71)   # clearly inside
        self.assertFalse(any(arrow.clamped for arrow in got.values()))
        self.assertEqual(got["Right"].label(), "-1.5 % slower")
        self.assertEqual(got["Up"].label(), "+0.2 % faster")
        self.assertAlmostEqual(got["Right"].tip[0], 0.71)
        self.assertEqual(got["Right"].tip[1], 0.0)
        self.assertEqual(got["Up"].tip[0], 0)
        self.assertAlmostEqual(got["Up"].tip[1], -1.032)    # up is negative y on screen
        shape = geometry(REAL)
        self.assertEqual(shape.caption, "Deviation from 1.0 drawn ×20.")
        self.assertEqual([percent for percent, _radius in shape.guides], [-2, 2])
        self.assertAlmostEqual(shape.extent, 1.4)
        self.assertEqual(
            shape.describe(),
            "Right 0.9855, 1.5 % slower than 1.0; Left 1.0012, 0.1 % faster than 1.0; "
            "Down 0.9953, 0.5 % slower than 1.0; Up 1.0016, 0.2 % faster than 1.0")

    def test_curve_passes_through_the_arrow_tips(self) -> None:
        for scalars in (REAL, Scalars(1.08, 0.93, 1.05, 0.97), Scalars.unity()):
            shape = geometry(scalars, per_quadrant=16)
            self.assertEqual(len(shape.curve), 64)
            for arrow in shape.arrows:
                self.assertTrue(any(math.isclose(x, arrow.tip[0], abs_tol=1e-12)
                                    and math.isclose(y, arrow.tip[1], abs_tol=1e-12)
                                    for x, y in shape.curve), arrow.name)

    def test_curve_is_four_quarter_ellipses(self) -> None:
        shape = geometry(REAL)
        right, left, down, up = (arrow.radius for arrow in shape.arrows)
        for x, y in shape.curve:
            rx = right if x >= 0 else left
            ry = down if y >= 0 else up
            self.assertAlmostEqual((x / rx) ** 2 + (y / ry) ** 2, 1.0, places=9)
        # Continuous: neighbouring points are close together all the way round.
        closed = (*shape.curve, shape.curve[0])
        longest = max(math.dist(a, b) for a, b in zip(closed, closed[1:]))
        self.assertLess(longest, 0.12)

    def test_all_ones_is_the_reference_circle(self) -> None:
        shape = geometry(Scalars.unity())
        self.assertTrue(shape.flat)
        for x, y in shape.curve:
            self.assertAlmostEqual(math.hypot(x, y), 1.0)
        self.assertIn("No deviation", shape.caption)
        self.assertIn("×20", shape.caption)
        self.assertEqual([arrow.label() for arrow in shape.arrows], ["0.0 % unchanged"] * 4)
        self.assertEqual([percent for percent, _radius in shape.guides], [-1, 1])
        self.assertIn("Right 1.0000, unchanged", shape.describe())

    def test_extreme_inputs_stay_inside_the_scale(self) -> None:
        shape = geometry(Scalars(10.0, 0.1, 10.0, 0.1))
        self.assertEqual([arrow.radius for arrow in shape.arrows],
                         [MAX_RADIUS, MIN_RADIUS, MAX_RADIUS, MIN_RADIUS])
        self.assertTrue(all(arrow.clamped for arrow in shape.arrows))
        self.assertEqual(shape.extent, MAX_RADIUS)
        for x, y in shape.curve:
            self.assertLessEqual(math.hypot(x, y), MAX_RADIUS + 1e-9)
            self.assertGreaterEqual(math.hypot(x, y), MIN_RADIUS - 1e-9)
        self.assertIn("beyond ±3.5 %", shape.caption)
        self.assertIn("double arrow head", shape.caption)
        self.assertEqual(shape.arrows[0].label(), "+900.0 % faster")

    def test_lopsided_values(self) -> None:
        got = arrows(Scalars(1.08, 0.93, 1.05, 0.97))
        self.assertEqual([got[name].clamped for name in diagram.NAMES],
                         [True, True, True, False])
        self.assertAlmostEqual(got["Up"].radius, 0.4)
        self.assertEqual([p for p, _r in geometry(Scalars(1.08, 0.93, 1.05, 0.97)).guides],
                         [-3, 3])

    def test_guide_rings_fit_the_scale(self) -> None:
        for scalars in (Scalars.unity(), REAL, Scalars(1.011, 0.991, 1.006, 0.996),
                        Scalars(10.0, 0.1, 1.0, 1.0)):
            shape = geometry(scalars)
            (low_percent, low), (high_percent, high) = shape.guides
            self.assertEqual(low_percent, -high_percent)
            self.assertAlmostEqual(low, 1 - high_percent / 100 * EXAGGERATION)
            self.assertAlmostEqual(high, 1 + high_percent / 100 * EXAGGERATION)
            self.assertGreaterEqual(low, MIN_RADIUS)
            self.assertLessEqual(high, MAX_RADIUS)
            self.assertGreaterEqual(shape.extent, high)

    def test_legend(self) -> None:
        self.assertEqual(diagram.LEGEND,
                         "Outside the dashed ring: faster than 1.0. Inside: slower.")


if __name__ == "__main__":
    unittest.main()
