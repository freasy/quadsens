"""Geometry of the direction diagram (no Qt).

The diagram shows one thing: what the driver applies. The driver multiplies
each axis by the multiplier that belongs to the sign of the movement, so a
unit circle of raw movement comes out as four quarter-ellipse arcs: the
quadrant between right and up has the right and the up multiplier as its
semi-axes, and so on. The arcs meet on the axes, at the tips of the arrows.

The radial scale is linear and labelled. It is fixed by one number, the
multiplier that the centre of the diagram stands for; the reference ring is
always 1.0 and the outer limit lies as far above 1.0 as the centre lies below:

    centre 0.5   ->  0.5 .. 1.5   (the default)
    centre 0.9   ->  0.9 .. 1.1   (ten times enlarged)
    centre 0.95  ->  0.95 .. 1.05 (twenty times enlarged)

The same mapping is applied to the four semi-axes before anything is drawn, so
curve and arrows always agree, and there is no other enlargement. A value
outside the scale is drawn at its edge and flagged as clamped.

All lengths are in units of the reference ring's radius (1.0 = no change);
x grows to the right and y grows downwards, as on screen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .model import Scalars, format_deviation

# The multipliers the centre can stand for ("diagram zoom").
ZOOMS: tuple[float, ...] = (0.5, 0.9, 0.95)
DEFAULT_ZOOM = 0.5
# Drawn radius of the outer limit, and the shortest arrow that is still drawn:
# a value at or below the centre would otherwise vanish.
MAX_RADIUS, MIN_RADIUS = 2.0, 0.08
# A value this close to 1.0 counts as "no deviation" (half of the fourth decimal).
ON_RING = 5e-5
NAMES: tuple[str, ...] = ("Right", "Left", "Down", "Up")
# Unit vectors of the four arrows on screen, in the order of NAMES.
VECTORS: tuple[tuple[int, int], ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))
LEGEND = "Outside the dashed ring: faster than 1.0. Inside: slower."
# Guide rings to choose from, as deviation from 1.0 in percent. At the default
# zoom they are labelled as multipliers (0.75, 1.25), otherwise in percent.
GUIDE_STEPS: dict[float, tuple[int, ...]] = {0.5: (25,), 0.9: (2, 5), 0.95: (1, 2)}

OUTSIDE, INSIDE, ON = "outside", "inside", "on"
WORDS = {OUTSIDE: "faster", INSIDE: "slower", ON: "unchanged"}


def zoom_of(centre: float) -> float:
    """The nearest supported centre value."""
    return min(ZOOMS, key=lambda zoom: abs(zoom - centre))


def factor(centre: float = DEFAULT_ZOOM) -> float:
    """How much a deviation from 1.0 is enlarged: 2, 10 or 20."""
    return 1.0 / (1.0 - centre)


def limits(centre: float = DEFAULT_ZOOM) -> tuple[float, float]:
    """Smallest and largest multiplier that is drawn to scale."""
    return (centre, 2.0 - centre)


def number(value: float) -> str:
    """A scale value without needless zeros: 0.5, 0.95, 1.25."""
    return f"{value:.2f}".rstrip("0").rstrip(".") if value != int(value) else f"{value:.1f}"


def drawn_radius(value: float, centre: float = DEFAULT_ZOOM) -> float:
    """Radius at which a multiplier is drawn: 0 at the centre value, 1 at 1.0."""
    exact = (value - centre) / (1.0 - centre)
    return min(MAX_RADIUS, max(MIN_RADIUS, exact))


def is_clamped(value: float, centre: float = DEFAULT_ZOOM) -> bool:
    low, high = limits(centre)
    return value < low - 1e-9 or value > high + 1e-9


def side_of_ring(value: float) -> str:
    """Where the arrow tip lies relative to the reference ring."""
    if abs(value - 1.0) < ON_RING:
        return ON
    return OUTSIDE if value > 1.0 else INSIDE


@dataclass(frozen=True)
class Arrow:
    name: str            # Right, Left, Down, Up
    value: float         # the multiplier
    radius: float        # where its tip is drawn
    clamped: bool        # drawn at the edge of the scale, not at its true place
    side: str            # OUTSIDE, INSIDE or ON the reference ring
    vector: tuple[int, int]

    @property
    def tip(self) -> tuple[float, float]:
        return (self.vector[0] * self.radius, self.vector[1] * self.radius)

    @property
    def word(self) -> str:
        """faster, slower or unchanged."""
        return WORDS[self.side]

    def label(self) -> str:
        """Signed percent and the direction in words, e.g. "-1.5 % slower"."""
        if self.side == ON:
            return "0.0 % unchanged"
        return f"{format_deviation(self.value)} {self.word}"

    def describe(self) -> str:
        """Full sentence part for screen readers."""
        if self.side == ON:
            return f"{self.name} {self.value:.4f}, unchanged"
        percent = format_deviation(self.value).lstrip("+-")
        return f"{self.name} {self.value:.4f}, {percent} {self.word} than 1.0"


@dataclass(frozen=True)
class Geometry:
    arrows: tuple[Arrow, ...]                   # right, left, down, up
    curve: tuple[tuple[float, float], ...]      # closed outline of what is applied
    guides: tuple[tuple[str, float], ...]       # (label, radius) of the guide rings
    extent: float                               # largest radius that has to fit
    caption: str
    centre: float = DEFAULT_ZOOM                # the multiplier at the centre

    @property
    def flat(self) -> bool:
        """True when nothing deviates: the curve is the reference circle."""
        return all(arrow.side == ON for arrow in self.arrows)

    @property
    def centre_label(self) -> str:
        return number(self.centre)

    def describe(self) -> str:
        """The four values in words, and how the scale is to be read."""
        low, high = limits(self.centre)
        return ("; ".join(arrow.describe() for arrow in self.arrows)
                + f". Scale: centre {number(low)}, dashed ring 1.0, edge {number(high)}")


def curve_points(radii: tuple[float, float, float, float],
                 per_quadrant: int = 24) -> tuple[tuple[float, float], ...]:
    """Outline of the applied response: four quarter-ellipse arcs, clockwise on screen.

    ``radii`` are the drawn radii for right, left, down, up. The outline starts
    at the right tip and passes the down, left and up tips in that order.
    """
    right, left, down, up = radii
    count = max(2, per_quadrant) * 4
    points = []
    for index in range(count):
        angle = 2 * math.pi * index / count
        cos, sin = math.cos(angle), math.sin(angle)
        # Snap the axis points, so that the tips are hit exactly.
        if index % (count // 4) == 0:
            cos, sin = round(cos), round(sin)
        rx = right if cos >= 0 else left
        ry = down if sin >= 0 else up
        points.append((rx * cos, ry * sin))
    return tuple(points)


def guide_rings(radii: tuple[float, ...],
                centre: float = DEFAULT_ZOOM) -> tuple[tuple[str, float], ...]:
    """Guide rings at a round value that suits what is drawn: one inside, one outside.

    A ring that would fall on the centre or beyond the outer limit is left out.
    """
    scale = factor(centre)
    reach = max(abs(radius - 1.0) for radius in radii) / scale * 100
    steps = GUIDE_STEPS[zoom_of(centre)]
    percent = next((step for step in steps if reach <= step * 1.2), steps[-1])
    rings = []
    for sign in (-1, 1):
        radius = 1.0 + sign * percent / 100 * scale
        if not MIN_RADIUS * 2 < radius < MAX_RADIUS - 1e-9:
            continue
        if zoom_of(centre) == DEFAULT_ZOOM:
            label = number(1.0 + sign * percent / 100)
        else:
            label = f"{sign * percent:+d} %"
        rings.append((label, radius))
    return tuple(rings)


def geometry(scalars: Scalars, centre: float = DEFAULT_ZOOM,
             per_quadrant: int = 24) -> Geometry:
    """Everything the diagram draws for the four multipliers right, left, down, up."""
    centre = zoom_of(centre)
    values = scalars.as_tuple()
    arrows = tuple(
        Arrow(name, value, drawn_radius(value, centre), is_clamped(value, centre),
              side_of_ring(value), vector)
        for name, value, vector in zip(NAMES, values, VECTORS))
    radii = tuple(arrow.radius for arrow in arrows)
    guides = guide_rings(radii, centre)
    extent = max(*radii, *(radius for _label, radius in guides), 1.0)
    low, high = limits(centre)
    caption = f"Centre = {number(low)}, dashed ring = 1.0."
    if all(arrow.side == ON for arrow in arrows):
        caption = "No deviation: the curve lies on the dashed ring. " + caption
    elif any(arrow.clamped for arrow in arrows):
        caption += (f" Values outside {number(low)} to {number(high)} are drawn at the "
                    "edge (double arrow head).")
    return Geometry(arrows, curve_points(radii, per_quadrant), guides,  # type: ignore[arg-type]
                    extent, caption, centre)
