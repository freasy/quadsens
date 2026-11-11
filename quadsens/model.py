"""Value types shared by the calibration, emulator and driver modules."""

from __future__ import annotations

import math
import re
from decimal import ROUND_HALF_UP, Decimal
from dataclasses import dataclass

SCALE_MIN = 0.1
SCALE_MAX = 10.0

# Digits with at most one separator, "." or ",": no sign, grouping or exponent.
_DECIMAL = re.compile(r"[0-9]+(?:[.,][0-9]*)?|[.,][0-9]+")


def parse_decimal(text: str) -> float | None:
    """Parse a plain decimal number typed with either "." or "," as the separator.

    Independent of the process locale: "1,0375" and "1.0375" are the same number
    everywhere. Returns None for anything else (signs, exponents, "1.000,5").
    """
    text = text.strip()
    if _DECIMAL.fullmatch(text) is None:
        return None
    return float(text.replace(",", "."))


def format_decimal(value: float, decimals: int) -> str:
    """Fixed-point text with a dot, whatever the locale."""
    return f"{value:.{decimals}f}"


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


def format_deviation(value: float) -> str:
    """Deviation of a multiplier from 1.0 in percent, e.g. "+3.8 %" or "-1.5 %".

    Rounded half away from zero on the decimal digits, so that 1.0375 and
    0.9625 give the same amount with opposite signs.
    """
    percent = ((Decimal(repr(float(value))) - 1) * 100).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP)
    if percent == 0:
        return "0.0 %"
    return f"{percent:+.1f} %"
