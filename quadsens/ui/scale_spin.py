"""Spin box for a scale factor that reads and writes numbers the same way everywhere."""

from __future__ import annotations

from ..model import format_decimal, parse_decimal
from .qt import QtGui, QtWidgets


class ScaleSpinBox(QtWidgets.QDoubleSpinBox):
    """A QDoubleSpinBox that ignores the locale.

    The value is always shown with a dot, like the driver reports it, and both
    "." and "," are accepted when typing. The stock widget follows the locale:
    in a comma locale it shows "1,0375" and treats a typed dot as a thousands
    separator, which silently turns 1.0375 into a different number.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        # Styled spin boxes place their editor at the very edge of the frame.
        self.lineEdit().setTextMargins(6, 0, 0, 0)

    def textFromValue(self, value: float) -> str:  # noqa: N802 (Qt name)
        return format_decimal(value, self.decimals())

    def valueFromText(self, text: str) -> float:  # noqa: N802 (Qt name)
        value = parse_decimal(text)
        return self.value() if value is None else value

    def validate(self, text: str, pos: int) -> object:
        state = QtGui.QValidator.State
        stripped = text.strip()
        if stripped in ("", ".", ","):
            return (state.Intermediate, text, pos)
        value = parse_decimal(stripped)
        if value is None or value > self.maximum():
            return (state.Invalid, text, pos)
        fraction = stripped.replace(",", ".").partition(".")[2]
        if len(fraction) > self.decimals():
            return (state.Invalid, text, pos)
        if value < self.minimum():
            return (state.Intermediate, text, pos)  # "0.0" on the way to "0.05"
        return (state.Acceptable, text, pos)

    def fixup(self, text: str) -> str:
        # The stock fixup strips the locale's group separator, which is "." in
        # many comma locales. Unfinished input is reverted by Qt instead.
        return text
