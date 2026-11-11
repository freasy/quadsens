"""Direction diagram: the four multipliers as arrows, the eight measured values behind."""

from __future__ import annotations

import math
from collections.abc import Sequence

from ..calibration import DIRECTION_LABELS
from ..model import Scalars, format_deviation
from .qt import QtCore, QtGui, QtWidgets

# (name, unit vector on screen) in the order of Scalars: right, left, down, up.
ARROWS: tuple[tuple[str, tuple[int, int]], ...] = (
    ("Right", (1, 0)), ("Left", (-1, 0)), ("Down", (0, 1)), ("Up", (0, -1)))


class DirectionDiagram(QtWidgets.QWidget):
    """Four arrows whose length shows the multiplier of that direction.

    The dashed ring marks 1.0; an arrow that ends outside it speeds its
    direction up, one that ends inside slows it down. Every arrow is labelled
    with its value and its deviation from 1.0 in percent, so nothing depends on
    reading lengths or colours. The eight values the calibration measured are
    drawn as a faint outline behind the arrows. The radial axis is zoomed to
    the neighbourhood of 1.0, otherwise a few percent would not be visible.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: tuple[float, ...] | None = None
        self._scalars: Scalars | None = None
        self.setMinimumSize(230, 150)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Multipliers per direction")
        self._describe()

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 (Qt name)
        return QtCore.QSize(300, 210)

    def values(self) -> tuple[float, ...] | None:
        """The eight measured values (E, NE, N, ... counter-clockwise), if any."""
        return self._values

    def scalars(self) -> Scalars | None:
        return self._scalars

    def set_values(self, values: Sequence[float] | None) -> None:
        self._values = tuple(values) if values is not None else None
        self._describe()

    def set_scalars(self, scalars: Scalars | None) -> None:
        """The four multipliers as the driver takes them: right, left, down, up."""
        self._scalars = scalars
        self._describe()

    def describe(self) -> str:
        parts: list[str] = []
        if self._scalars is not None:
            parts.append(", ".join(
                f"{name} {value:.4f} ({format_deviation(value)})"
                for (name, _vector), value in zip(ARROWS, self._scalars.as_tuple())))
        if self._values is not None:
            parts.append("measured: " + ", ".join(
                f"{label} {value:.4f}" for label, value in zip(DIRECTION_LABELS, self._values)))
        return "; ".join(parts) if parts else "No calibration result available."

    def _describe(self) -> None:
        self.setAccessibleDescription(self.describe())
        self.setToolTip(self.describe().replace("; ", "\n"))
        self.update()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:  # noqa: N802 (Qt name)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        text_color = palette.color(QtGui.QPalette.ColorRole.WindowText)
        accent = palette.color(QtGui.QPalette.ColorRole.Highlight)
        faint = QtGui.QColor(text_color)
        faint.setAlphaF(0.35)

        font = painter.font()
        bold = QtGui.QFont(font)
        bold.setBold(True)
        metrics = QtGui.QFontMetricsF(bold)
        line = metrics.height()

        if self._scalars is None and self._values is None:
            painter.setPen(text_color)
            painter.drawText(QtCore.QRectF(self.rect()),
                             int(QtCore.Qt.AlignmentFlag.AlignCenter), "No result yet")
            painter.end()
            return

        side = metrics.horizontalAdvance("+00.0 %") + 8   # label column left and right
        center = QtCore.QPointF(self.width() / 2, self.height() / 2)
        outer = max(14.0, min(self.width() / 2 - side - 4, self.height() / 2 - line - 6))
        inner = outer * 0.2
        known = list(self._values or ()) + list(
            self._scalars.as_tuple() if self._scalars is not None else ())
        low = min(0.9, min(known)) - 0.01
        high = max(1.1, max(known)) + 0.01

        def radius(value: float) -> float:
            return inner + (value - low) / (high - low) * (outer - inner)

        # Reference: outer ring and the dashed ring at 1.0.
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.setPen(QtGui.QPen(faint, 1))
        painter.drawEllipse(center, outer, outer)
        painter.setPen(QtGui.QPen(text_color, 1, QtCore.Qt.PenStyle.DashLine))
        painter.drawEllipse(center, radius(1.0), radius(1.0))

        # Secondary: outline of the eight measured values.
        if self._values is not None:
            points = []
            for index, value in enumerate(self._values):
                angle = math.radians(45 * index)
                points.append(QtCore.QPointF(center.x() + radius(value) * math.cos(angle),
                                             center.y() - radius(value) * math.sin(angle)))
            fill = QtGui.QColor(text_color)
            fill.setAlphaF(0.08)
            painter.setPen(QtGui.QPen(faint, 1))
            painter.setBrush(fill)
            painter.drawPolygon(QtGui.QPolygonF(points))
            painter.setBrush(faint)
            for point in points:
                painter.drawEllipse(point, 1.8, 1.8)

        # Main message: one arrow and one label per direction.
        if self._scalars is not None:
            for (name, (dx, dy)), value in zip(ARROWS, self._scalars.as_tuple()):
                length = radius(value)
                tip = QtCore.QPointF(center.x() + dx * length, center.y() + dy * length)
                head = min(9.0, max(5.0, outer * 0.14))
                base = QtCore.QPointF(tip.x() - dx * head, tip.y() - dy * head)
                painter.setPen(QtGui.QPen(accent, 3, QtCore.Qt.PenStyle.SolidLine,
                                          QtCore.Qt.PenCapStyle.RoundCap))
                painter.drawLine(center, base)
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.setBrush(accent)
                painter.drawPolygon(QtGui.QPolygonF([
                    tip,
                    QtCore.QPointF(base.x() - dy * head * 0.6, base.y() + dx * head * 0.6),
                    QtCore.QPointF(base.x() + dy * head * 0.6, base.y() - dx * head * 0.6)]))

                painter.setPen(text_color)
                deviation = format_deviation(value)
                if dx:  # beside the ring: name, value, deviation stacked
                    rect = QtCore.QRectF(0, 0, side, line * 3)
                    x = center.x() + dx * (outer + 4) + (0 if dx > 0 else -side)
                    rect.moveTopLeft(QtCore.QPointF(x, center.y() - line * 1.5))
                    align = (QtCore.Qt.AlignmentFlag.AlignLeft if dx > 0
                             else QtCore.Qt.AlignmentFlag.AlignRight)
                    painter.setFont(bold)
                    painter.drawText(QtCore.QRectF(rect.x(), rect.y(), side, line),
                                     int(align | QtCore.Qt.AlignmentFlag.AlignVCenter), name)
                    painter.setFont(font)
                    painter.drawText(QtCore.QRectF(rect.x(), rect.y() + line, side, line),
                                     int(align | QtCore.Qt.AlignmentFlag.AlignVCenter),
                                     f"{value:.4f}")
                    painter.drawText(QtCore.QRectF(rect.x(), rect.y() + 2 * line, side, line),
                                     int(align | QtCore.Qt.AlignmentFlag.AlignVCenter),
                                     deviation)
                else:   # above and below: one line
                    rest = f"  {value:.4f}  {deviation}"
                    name_w = metrics.horizontalAdvance(name)
                    total = name_w + QtGui.QFontMetricsF(font).horizontalAdvance(rest)
                    y = center.y() + dy * (outer + 4) - (line if dy < 0 else 0)
                    x = center.x() - total / 2
                    painter.setFont(bold)
                    painter.drawText(QtCore.QRectF(x, y, name_w + 1, line),
                                     int(QtCore.Qt.AlignmentFlag.AlignVCenter), name)
                    painter.setFont(font)
                    painter.drawText(QtCore.QRectF(x + name_w, y, total - name_w + 2, line),
                                     int(QtCore.Qt.AlignmentFlag.AlignVCenter), rest)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(text_color)
            painter.drawEllipse(center, 3.0, 3.0)
        painter.end()


# Former name of the widget; the calibration panel still exposes it as "compass".
CompassWidget = DirectionDiagram
