"""Geometry of the direction diagram (no Qt).

The diagram shows one thing: what the driver applies. The driver multiplies
each axis by the multiplier that belongs to the sign of the movement, so a
unit circle of raw movement comes out as four quarter-ellipse arcs: the
quadrant between right and up has the right and the up multiplier as its
semi-axes, and so on. The arcs meet on the axes, at the tips of the arrows.

Multipliers differ from 1.0 by a percent or two, which would not be visible
at true scale. The deviation from 1.0 is therefore drawn enlarged by one fixed
factor, EXAGGERATION, applied to the four semi-axes before anything is drawn,
so that curve and arrows always agree. Values whose enlarged radius would
leave the drawable range are drawn at its edge and flagged as clamped.

All lengths are in units of the reference circle's radius (1.0 = no change);
x grows to the right and y grows downwards, as on screen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .model import Scalars, format_deviation

EXAGGERATION = 20.0
# Drawn radius of a clamped value. 0.3 keeps the curve clear of the centre,
# 1.7 keeps it inside the widget: +-3.5 % at the factor above.
MIN_RADIUS, MAX_RADIUS = 0.3, 1.7
# A value this close to 1.0 counts as "no deviation" (half of the fourth decimal).
ON_RING = 5e-5
NAMES: tuple[str, ...] = ("Right", "Left", "Down", "Up")
# Unit vectors of the four arrows on screen, in the order of NAMES.
VECTORS: tuple[tuple[int, int], ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))
LEGEND = "Outside the dashed ring: faster than 1.0. Inside: slower."

OUTSIDE, INSIDE, ON = "outside", "inside", "on"
WORDS = {OUTSIDE: "faster", INSIDE: "slower", ON: "unchanged"}


def clamp_limit_percent() -> float:
    """Largest deviation in percent that is still drawn to scale."""
    return round((MAX_RADIUS - 1.0) / EXAGGERATION * 100, 2)


def drawn_radius(value: float) -> float:
    """Radius at which a multiplier is drawn: deviation enlarged, then clamped."""
    return min(MAX_RADIUS, max(MIN_RADIUS, 1.0 + (value - 1.0) * EXAGGERATION))


def is_clamped(value: float) -> bool:
    exact = 1.0 + (value - 1.0) * EXAGGERATION
    return exact < MIN_RADIUS - 1e-9 or exact > MAX_RADIUS + 1e-9


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
    guides: tuple[tuple[int, float], ...]       # (deviation in percent, radius) of guide rings
    extent: float                               # largest radius that has to fit
    caption: str

    @property
    def flat(self) -> bool:
        """True when nothing deviates: the curve is the reference circle."""
        return all(arrow.side == ON for arrow in self.arrows)

    def describe(self) -> str:
        return "; ".join(arrow.describe() for arrow in self.arrows)


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


def guide_rings(radii: tuple[float, ...]) -> tuple[tuple[int, float], ...]:
    """Guide rings at a round deviation that suits what is drawn: one inside, one outside."""
    reach = max(abs(radius - 1.0) for radius in radii) / EXAGGERATION * 100
    percent = 1 if reach <= 1.2 else 2 if reach <= 2.6 else 3
    step = percent / 100 * EXAGGERATION
    return ((-percent, 1.0 - step), (percent, 1.0 + step))


def geometry(scalars: Scalars, per_quadrant: int = 24) -> Geometry:
    """Everything the diagram draws for the four multipliers right, left, down, up."""
    values = scalars.as_tuple()
    arrows = tuple(
        Arrow(name, value, drawn_radius(value), is_clamped(value), side_of_ring(value), vector)
        for name, value, vector in zip(NAMES, values, VECTORS))
    radii = tuple(arrow.radius for arrow in arrows)
    guides = guide_rings(radii)
    extent = max(*radii, *(radius for _percent, radius in guides), 1.0)
    factor = f"×{EXAGGERATION:g}"
    if all(arrow.side == ON for arrow in arrows):
        caption = f"No deviation from 1.0 (a deviation would be drawn {factor})."
    elif any(arrow.clamped for arrow in arrows):
        caption = (f"Deviation from 1.0 drawn {factor}; beyond ±{clamp_limit_percent():g} % "
                   "it is drawn at the edge (double arrow head).")
    else:
        caption = f"Deviation from 1.0 drawn {factor}."
    return Geometry(arrows, curve_points(radii, per_quadrant), guides,  # type: ignore[arg-type]
                    extent, caption)
