"""Value types shared by the calibration, emulator and driver modules."""

from __future__ import annotations

import math
from dataclasses import dataclass

SCALE_MIN = 0.1
SCALE_MAX = 10.0


@dataclass(frozen=True)
class Scalars:
    """Four per-direction sensitivity multipliers (1.0 = unchanged)."""

    x_pos: float
    x_neg: float
    y_pos: float
    y_neg: float

    @classmethod
    def unity(cls) -> Scalars:
        return cls(1.0, 1.0, 1.0, 1.0)

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x_pos, self.x_neg, self.y_pos, self.y_neg)

    def flipped_y(self) -> Scalars:
        """The same values with the two Y directions exchanged."""
        return Scalars(self.x_pos, self.x_neg, self.y_neg, self.y_pos)

    def validate(self) -> None:
        """Raise ValueError unless all four values are finite and within range."""
        for name, value in zip(("XPos", "XNeg", "YPos", "YNeg"), self.as_tuple()):
            if not math.isfinite(value) or not SCALE_MIN <= value <= SCALE_MAX:
                raise ValueError(
                    f"{name}={value!r} is outside the allowed range {SCALE_MIN}..{SCALE_MAX}"
                )

    def is_close(self, other: Scalars, tolerance: float = 1e-4) -> bool:
        return all(abs(a - b) <= tolerance for a, b in zip(self.as_tuple(), other.as_tuple()))
