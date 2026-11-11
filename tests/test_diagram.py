"""Tests of the direction diagram's geometry (no Qt)."""

from __future__ import annotations

import math
import unittest

from quadsens import diagram
from quadsens.diagram import MAX_RADIUS, MIN_RADIUS, ZOOMS, geometry
from quadsens.model import Scalars

# A real result: right, left, down, up.
REAL = Scalars(0.9855, 1.0012, 0.9953, 1.0016)
LOPSIDED = Scalars(1.08, 0.93, 1.05, 0.97)


def arrows(scalars: Scalars, centre: float = 0.5) -> dict[str, diagram.Arrow]:
    return {arrow.name: arrow for arrow in geometry(scalars, centre).arrows}


class ScaleTest(unittest.TestCase):
    def test_three_zooms_default_centre_at_half(self) -> None:
        self.assertEqual(ZOOMS, (0.5, 0.9, 0.95))
        self.assertEqual(diagram.DEFAULT_ZOOM, 0.5)
        for centre, enlargement in zip(ZOOMS, (2, 10, 20)):
            self.assertAlmostEqual(diagram.factor(centre), enlargement)
            low, high = diagram.limits(centre)
            self.assertAlmostEqual(low, centre)
            self.assertAlmostEqual(high, 2 - centre)       # symmetric around 1.0
            # The scale is linear: centre value at 0, 1.0 on the ring, limit at 2.
            self.assertEqual(diagram.drawn_radius(centre, centre), MIN_RADIUS)
            self.assertAlmostEqual(diagram.drawn_radius(1.0, centre), 1.0)
            self.assertAlmostEqual(diagram.drawn_radius(high, centre), 2.0)
            middle = (1.0 + high) / 2
            self.assertAlmostEqual(diagram.drawn_radius(middle, centre), 1.5)
            self.assertAlmostEqual(diagram.drawn_radius((centre + 1.0) / 2, centre), 0.5)
        self.assertAlmostEqual(diagram.drawn_radius(0.75), 0.5)
        self.assertAlmostEqual(diagram.drawn_radius(1.25), 1.5)

    def test_clamping(self) -> None:
        self.assertEqual((MAX_RADIUS, MIN_RADIUS), (2.0, 0.08))
        for centre in ZOOMS:
            self.assertEqual(diagram.drawn_radius(10.0, centre), MAX_RADIUS)
            self.assertEqual(diagram.drawn_radius(0.1, centre), MIN_RADIUS)   # never vanishes
            self.assertTrue(diagram.is_clamped(10.0, centre))
            self.assertTrue(diagram.is_clamped(0.1, centre))
            self.assertFalse(diagram.is_clamped(1.0, centre))
            self.assertFalse(diagram.is_clamped(centre, centre))
            self.assertFalse(diagram.is_clamped(2 - centre, centre))
        self.assertFalse(diagram.is_clamped(1.08))
        self.assertTrue(diagram.is_clamped(1.08, 0.95))
        self.assertFalse(diagram.is_clamped(0.93, 0.9))
        self.assertTrue(diagram.is_clamped(0.93, 0.95))
        self.assertTrue(diagram.is_clamped(1.51))
        self.assertTrue(diagram.is_clamped(0.49))

    def test_zoom_of_and_number(self) -> None:
        self.assertEqual(diagram.zoom_of(0.5), 0.5)
        self.assertEqual(diagram.zoom_of(0.94), 0.95)
        self.assertEqual(diagram.zoom_of(0.0), 0.5)
        self.assertEqual([diagram.number(v) for v in (0.5, 0.9, 0.95, 1.0, 1.25, 1.5, 1.05)],
                         ["0.5", "0.9", "0.95", "1.0", "1.25", "1.5", "1.05"])

    def test_side_of_ring(self) -> None:
        self.assertEqual(diagram.side_of_ring(1.0), diagram.ON)
        self.assertEqual(diagram.side_of_ring(1.00004), diagram.ON)
        self.assertEqual(diagram.side_of_ring(1.0001), diagram.OUTSIDE)
        self.assertEqual(diagram.side_of_ring(0.9999), diagram.INSIDE)


class GeometryTest(unittest.TestCase):
    def test_real_values_at_every_zoom(self) -> None:
        expected = {  # right, left, down, up
            0.5: (0.971, 1.0024, 0.9906, 1.0032),
            0.9: (0.855, 1.012, 0.953, 1.016),
            0.95: (0.71, 1.024, 0.906, 1.032),
        }
        for centre, radii in expected.items():
            got = arrows(REAL, centre)
            for name, radius in zip(diagram.NAMES, radii):
                self.assertAlmostEqual(got[name].radius, radius, msg=f"{name} at {centre}")
            # Whatever the zoom: Right clearly inside, Down inside, Left and Up outside.
            self.assertLess(got["Right"].radius, got["Down"].radius)
            self.assertLess(got["Down"].radius, 1.0)
            self.assertGreater(got["Left"].radius, 1.0)
            self.assertGreater(got["Up"].radius, got["Left"].radius)
            self.assertEqual([got[name].side for name in diagram.NAMES],
                             [diagram.INSIDE, diagram.OUTSIDE, diagram.INSIDE,
                              diagram.OUTSIDE])
            self.assertFalse(any(arrow.clamped for arrow in got.values()))
            self.assertEqual(got["Right"].label(), "-1.5 % slower")   # same at every zoom
            self.assertEqual(got["Up"].label(), "+0.2 % faster")
        up = arrows(REAL)["Up"]
        self.assertEqual(up.tip[0], 0)
        self.assertAlmostEqual(up.tip[1], -1.0032)    # up is negative y on screen

    def test_captions_and_guides_per_zoom(self) -> None:
        shape = geometry(REAL)
        self.assertEqual(shape.caption, "Centre = 0.5, dashed ring = 1.0.")
        self.assertEqual(shape.centre_label, "0.5")
        self.assertEqual([(label, round(radius, 6)) for label, radius in shape.guides],
                         [("0.75", 0.5), ("1.25", 1.5)])       # multipliers, not percent
        self.assertAlmostEqual(shape.extent, 1.5)
        tenfold = geometry(REAL, 0.9)
        self.assertEqual(tenfold.caption, "Centre = 0.9, dashed ring = 1.0.")
        self.assertEqual([(label, round(radius, 6)) for label, radius in tenfold.guides],
                         [("-2 %", 0.8), ("+2 %", 1.2)])
        twentyfold = geometry(REAL, 0.95)
        self.assertEqual(twentyfold.caption, "Centre = 0.95, dashed ring = 1.0.")
        self.assertEqual(twentyfold.centre_label, "0.95")
        self.assertEqual([(label, round(radius, 6)) for label, radius in twentyfold.guides],
                         [("-2 %", 0.6), ("+2 %", 1.4)])
        for shape in (shape, tenfold, twentyfold):
            self.assertNotIn("×", shape.caption)
            for _label, radius in shape.guides:
                self.assertGreater(radius, MIN_RADIUS)
                self.assertLess(radius, MAX_RADIUS)
                self.assertLessEqual(radius, shape.extent)

    def test_description_states_values_and_scale(self) -> None:
        self.assertEqual(
            geometry(REAL).describe(),
            "Right 0.9855, 1.5 % slower than 1.0; Left 1.0012, 0.1 % faster than 1.0; "
            "Down 0.9953, 0.5 % slower than 1.0; Up 1.0016, 0.2 % faster than 1.0. "
            "Scale: centre 0.5, dashed ring 1.0, edge 1.5")
        self.assertTrue(geometry(REAL, 0.95).describe().endswith(
            "Scale: centre 0.95, dashed ring 1.0, edge 1.05"))

    def test_curve_passes_through_the_arrow_tips(self) -> None:
        for centre in ZOOMS:
            for scalars in (REAL, LOPSIDED, Scalars.unity(), Scalars(10.0, 0.1, 1.2, 0.8)):
                shape = geometry(scalars, centre, per_quadrant=16)
                self.assertEqual(len(shape.curve), 64)
                for arrow in shape.arrows:
                    self.assertTrue(any(math.isclose(x, arrow.tip[0], abs_tol=1e-12)
                                        and math.isclose(y, arrow.tip[1], abs_tol=1e-12)
                                        for x, y in shape.curve), arrow.name)

    def test_curve_is_four_quarter_ellipses(self) -> None:
        for centre in ZOOMS:
            shape = geometry(REAL, centre)
            right, left, down, up = (arrow.radius for arrow in shape.arrows)
            for x, y in shape.curve:
                rx = right if x >= 0 else left
                ry = down if y >= 0 else up
                self.assertAlmostEqual((x / rx) ** 2 + (y / ry) ** 2, 1.0, places=9)
            closed = (*shape.curve, shape.curve[0])
            longest = max(math.dist(a, b) for a, b in zip(closed, closed[1:]))
            self.assertLess(longest, 0.12)   # continuous all the way round

    def test_all_ones_is_the_reference_circle(self) -> None:
        for centre in ZOOMS:
            shape = geometry(Scalars.unity(), centre)
            self.assertTrue(shape.flat)
            for x, y in shape.curve:
                self.assertAlmostEqual(math.hypot(x, y), 1.0)
            self.assertIn("No deviation", shape.caption)
            self.assertIn(f"Centre = {diagram.number(centre)}", shape.caption)
            self.assertEqual([arrow.label() for arrow in shape.arrows],
                             ["0.0 % unchanged"] * 4)
            self.assertIn("Right 1.0000, unchanged", shape.describe())

    def test_extreme_inputs_stay_inside_the_scale(self) -> None:
        for centre in ZOOMS:
            shape = geometry(Scalars(10.0, 0.1, 10.0, 0.1), centre)
            self.assertEqual([arrow.radius for arrow in shape.arrows],
                             [MAX_RADIUS, MIN_RADIUS, MAX_RADIUS, MIN_RADIUS])
            self.assertTrue(all(arrow.clamped for arrow in shape.arrows))
            self.assertEqual(shape.extent, MAX_RADIUS)
            for x, y in shape.curve:
                self.assertLessEqual(math.hypot(x, y), MAX_RADIUS + 1e-9)
                self.assertGreaterEqual(math.hypot(x, y), MIN_RADIUS - 1e-9)
            self.assertIn("double arrow head", shape.caption)
            self.assertEqual(shape.arrows[0].label(), "+900.0 % faster")
        self.assertIn("outside 0.5 to 1.5", geometry(Scalars(10.0, 0.1, 1, 1)).caption)
        self.assertIn("outside 0.95 to 1.05",
                      geometry(Scalars(10.0, 0.1, 1, 1), 0.95).caption)

    def test_lopsided_values(self) -> None:
        got = arrows(LOPSIDED)   # all within 0.5..1.5: drawn to scale
        self.assertEqual([got[name].clamped for name in diagram.NAMES], [False] * 4)
        for name, radius in zip(diagram.NAMES, (1.16, 0.86, 1.1, 0.94)):
            self.assertAlmostEqual(got[name].radius, radius)
        zoomed = arrows(LOPSIDED, 0.95)
        self.assertEqual([zoomed[name].clamped for name in diagram.NAMES],
                         [True, True, False, False])
        self.assertAlmostEqual(zoomed["Up"].radius, 0.4)

    def test_legend(self) -> None:
        self.assertEqual(diagram.LEGEND,
                         "Outside the dashed ring: faster than 1.0. Inside: slower.")


if __name__ == "__main__":
    unittest.main()
