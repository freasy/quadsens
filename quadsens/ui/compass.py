"""Polar plot of the eight directional scalars."""

from __future__ import annotations

import math
from collections.abc import Sequence

from ..calibration import DIRECTION_LABELS
from .qt import QtCore, QtGui, QtWidgets


class CompassWidget(QtWidgets.QWidget):
    """Eight spokes (E, NE, N, ... counter-clockwise); the dashed ring marks 1.0.

    The radial axis is zoomed to the neighbourhood of 1.0, otherwise deviations
    of a few percent would not be visible. Every spoke is labelled with its
    value, and the same values are available as accessible description.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: tuple[float, ...] | None = None
        self.setMinimumSize(150, 140)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Directional scalars")
        self.set_values(None)

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 (Qt name)
        return QtCore.QSize(210, 180)

    def values(self) -> tuple[float, ...] | None:
        return self._values

    def set_values(self, values: Sequence[float] | None) -> None:
        self._values = tuple(values) if values is not None else None
        self.setAccessibleDescription(self.describe())
        self.setToolTip(self.describe())
        self.update()

    def describe(self) -> str:
        if self._values is None:
            return "No directional scalars available."
        return ", ".join(f"{label} {value:.4f}"
                         for label, value in zip(DIRECTION_LABELS, self._values))

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:  # noqa: N802 (Qt name)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        text_color = palette.color(QtGui.QPalette.ColorRole.WindowText)
        accent = palette.color(QtGui.QPalette.ColorRole.Highlight)
        faint = QtGui.QColor(text_color)
        faint.setAlphaF(0.35)

        font = painter.font()
        font.setPointSizeF(max(6.5, font.pointSizeF() * 0.85))
        painter.setFont(font)
        metrics = QtGui.QFontMetricsF(font)

        if self._values is None:
            painter.setPen(text_color)
            painter.drawText(QtCore.QRectF(self.rect()),
                             int(QtCore.Qt.AlignmentFlag.AlignCenter), "No directional data")
            painter.end()
            return

        label_w = metrics.horizontalAdvance("NW 0.000") + 4
        label_h = metrics.height() * 2 + 2
        center = QtCore.QPointF(self.width() / 2, self.height() / 2)
        outer = max(12.0, min(self.width() / 2 - label_w, self.height() / 2 - label_h))
        inner = outer * 0.25
        low = min(0.9, min(self._values)) - 0.01
        high = max(1.1, max(self._values)) + 0.01

        def radius(value: float) -> float:
            return inner + (value - low) / (high - low) * (outer - inner)

        def point(index: int, r: float) -> QtCore.QPointF:
            angle = math.radians(45 * index)
            return QtCore.QPointF(center.x() + r * math.cos(angle),
                                  center.y() - r * math.sin(angle))

        # Grid: spokes, outer ring, dashed unit ring.
        painter.setPen(QtGui.QPen(faint, 1))
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        for index in range(8):
            painter.drawLine(center, point(index, outer))
        painter.drawEllipse(center, outer, outer)
        unit_pen = QtGui.QPen(text_color, 1, QtCore.Qt.PenStyle.DashLine)
        painter.setPen(unit_pen)
        painter.drawEllipse(center, radius(1.0), radius(1.0))

        # Data polygon.
        points = [point(i, radius(v)) for i, v in enumerate(self._values)]
        fill = QtGui.QColor(accent)
        fill.setAlphaF(0.3)
        painter.setPen(QtGui.QPen(accent, 2))
        painter.setBrush(fill)
        painter.drawPolygon(QtGui.QPolygonF(points))
        painter.setBrush(accent)
        for p in points:
            painter.drawEllipse(p, 2.5, 2.5)

        # Labels: direction and value outside the ring.
        painter.setPen(text_color)
        for index, (label, value) in enumerate(zip(DIRECTION_LABELS, self._values)):
            anchor = point(index, outer + 4)
            angle = math.radians(45 * index)
            dx, dy = math.cos(angle), -math.sin(angle)
            rect = QtCore.QRectF(0, 0, label_w, metrics.height() * 2)
            rect.moveCenter(QtCore.QPointF(anchor.x() + dx * label_w / 2,
                                           anchor.y() + dy * metrics.height()))
            painter.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter),
                             f"{label}\n{value:.3f}")
        painter.end()
