"""Direction diagram: what the driver applies, as one curve with four arrows."""

from __future__ import annotations

from collections.abc import Sequence

from ..calibration import DIRECTION_LABELS
from ..diagram import LEGEND, Geometry, geometry
from ..model import Scalars
from . import theme
from .qt import QtCore, QtGui, QtWidgets
from .status import FONT_CAPTION


class DirectionDiagram(QtWidgets.QWidget):
    """Shows the four multipliers the driver applies, and nothing else.

    The dashed ring is 1.0. The filled curve is what a circle of raw movement
    becomes: four quarter-ellipse arcs that meet at the tips of the arrows. An
    arrow that ends outside the ring speeds its direction up, one that ends
    inside slows it down; each is labelled with its value, the signed percent
    and the word "faster" or "slower", so nothing depends on reading lengths or
    colours. Deviations are drawn enlarged by one fixed factor, which the
    caption states (see quadsens.diagram for the geometry).

    The eight values the calibration measured are not drawn; they are listed
    under "Measured values" and remain part of the accessible description.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: tuple[float, ...] | None = None
        self._scalars: Scalars | None = None
        self._geometry: Geometry | None = None
        self.setMinimumSize(230, 150)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Multipliers the driver applies, per direction")
        self._describe()

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 (Qt name)
        return QtCore.QSize(300, 210)

    def values(self) -> tuple[float, ...] | None:
        """The eight measured values (E, NE, N, ... counter-clockwise), if any."""
        return self._values

    def scalars(self) -> Scalars | None:
        return self._scalars

    def shape(self) -> Geometry | None:
        """The geometry that is drawn, None without multipliers."""
        return self._geometry

    def set_values(self, values: Sequence[float] | None) -> None:
        """Remember the eight measured values for the description; they are not drawn."""
        self._values = tuple(values) if values is not None else None
        self._describe()

    def set_scalars(self, scalars: Scalars | None) -> None:
        """The four multipliers as the driver takes them: right, left, down, up."""
        self._scalars = scalars
        self._geometry = geometry(scalars) if scalars is not None else None
        self._describe()

    def describe(self) -> str:
        parts: list[str] = []
        if self._geometry is not None:
            parts.append(self._geometry.describe())
        if self._values is not None:
            parts.append("measured: " + ", ".join(
                f"{label} {value:.4f}" for label, value in zip(DIRECTION_LABELS, self._values)))
        return "; ".join(parts) if parts else "No calibration result available."

    def _describe(self) -> None:
        self.setAccessibleDescription(self.describe())
        if self._geometry is not None:
            self.setToolTip("\n".join(
                [arrow.describe() for arrow in self._geometry.arrows]
                + [self._geometry.caption, LEGEND]))
        else:
            self.setToolTip(self.describe())
        self.update()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:  # noqa: N802 (Qt name)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        text_color, muted = theme.color("text"), theme.color("muted")
        accent, ring = theme.color("accent_line"), theme.color("outline")
        flags = QtCore.Qt.AlignmentFlag

        # Typographic hierarchy: the value carries the weight, the rest are captions.
        value_font = QtGui.QFont(painter.font())
        value_font.setWeight(QtGui.QFont.Weight.DemiBold)
        small = QtGui.QFont(painter.font())
        small.setPointSizeF(small.pointSizeF() * FONT_CAPTION)
        tiny = QtGui.QFont(small)
        tiny.setPointSizeF(small.pointSizeF() * 0.9)
        value_metrics = QtGui.QFontMetricsF(value_font)
        small_metrics = QtGui.QFontMetricsF(small)
        tiny_metrics = QtGui.QFontMetricsF(tiny)
        line = value_metrics.height()
        small_line = small_metrics.height()

        shape = self._geometry
        if shape is None:
            painter.setPen(muted)
            painter.setFont(small)
            painter.drawText(QtCore.QRectF(self.rect()), int(flags.AlignCenter),
                             "No result yet")
            painter.end()
            return

        # Caption and legend at the bottom, wrapped into at most two lines each side by side.
        note = f"{shape.caption} {LEGEND}"
        note_height = small_line * (2 if small_metrics.horizontalAdvance(note)
                                    > self.width() - 4 else 1)
        if small_metrics.horizontalAdvance(note) > 2 * (self.width() - 4):
            note_height = small_line * 3
        note_rect = QtCore.QRectF(2, self.height() - note_height, self.width() - 4,
                                  note_height)
        painter.setFont(small)
        painter.setPen(muted)
        painter.drawText(note_rect, int(flags.AlignHCenter | flags.AlignTop
                                        | QtCore.Qt.TextFlag.TextWordWrap), note)

        # Room beside the drawing for the left and the right label, as wide as needed.
        side = max([value_metrics.horizontalAdvance("0.0000")]
                   + [small_metrics.horizontalAdvance(arrow.label())
                      for arrow in shape.arrows if arrow.vector[0]]) + 6
        area = QtCore.QRectF(0, 0, self.width(), self.height() - note_height - 2)
        center = QtCore.QPointF(area.center().x(), area.center().y())
        reach = max(12.0, min(area.width() / 2 - side - 8, area.height() / 2 - line - 8))
        unit = reach / shape.extent          # pixels of the reference radius

        def at(x: float, y: float) -> QtCore.QPointF:
            return QtCore.QPointF(center.x() + x * unit, center.y() + y * unit)

        # Guide rings at round deviations, labelled on the left diagonals.
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        faint = QtGui.QColor(ring)
        faint.setAlphaF(0.55)
        painter.setFont(tiny)
        for index, (percent, radius) in enumerate(shape.guides):
            painter.setPen(QtGui.QPen(faint, 1))
            painter.drawEllipse(center, radius * unit, radius * unit)
            label = f"{percent:+d} %"
            width = tiny_metrics.horizontalAdvance(label)
            diagonal = 0.7071 * radius * unit
            # Outer ring: label above-left outside it. Inner ring: just inside it in
            # the same direction, left out when the ring is too small to hold it.
            if percent > 0:
                spot = QtCore.QPointF(center.x() - diagonal - width - 3,
                                      center.y() - diagonal - tiny_metrics.height())
            elif radius * unit >= 30:
                inset = 0.7071 * (radius * unit - 11)
                spot = QtCore.QPointF(center.x() - inset - width / 2,
                                      center.y() - inset - tiny_metrics.height() / 2)
            else:
                continue
            painter.setPen(muted)
            painter.drawText(QtCore.QRectF(spot.x(), spot.y(), width + 2,
                                           tiny_metrics.height()),
                             int(flags.AlignCenter), label)

        # What is applied: one smooth closed curve, lightly filled.
        path = QtGui.QPainterPath()
        first = True
        for x, y in shape.curve:
            if first:
                path.moveTo(at(x, y))
                first = False
            else:
                path.lineTo(at(x, y))
        path.closeSubpath()
        fill = theme.color("accent")
        fill.setAlphaF(0.22)
        painter.setPen(QtGui.QPen(accent, 1.6))
        painter.setBrush(fill)
        painter.drawPath(path)

        # The reference: dashed ring at 1.0, labelled once, with a tick on each axis.
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.setPen(QtGui.QPen(text_color, 1.2, QtCore.Qt.PenStyle.DashLine))
        painter.drawEllipse(center, unit, unit)
        painter.setPen(QtGui.QPen(text_color, 1.2))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            painter.drawLine(at(dx - dy * 0.06, dy - dx * 0.06),
                             at(dx + dy * 0.06, dy + dx * 0.06))
        painter.setFont(tiny)
        painter.setPen(text_color)
        diagonal = 0.7071 * unit
        painter.drawText(QtCore.QRectF(center.x() + diagonal + 3,
                                       center.y() - diagonal - tiny_metrics.height(),
                                       tiny_metrics.horizontalAdvance("1.0") + 2,
                                       tiny_metrics.height()),
                         int(flags.AlignCenter), "1.0")

        # Arrows from the centre to the curve, and their labels.
        reach_px = shape.extent * unit
        for arrow in shape.arrows:
            dx, dy = arrow.vector
            tip = at(*arrow.tip)
            head = min(9.0, max(5.0, unit * 0.16))
            base = QtCore.QPointF(tip.x() - dx * head, tip.y() - dy * head)
            painter.setPen(QtGui.QPen(accent, 3, QtCore.Qt.PenStyle.SolidLine,
                                      QtCore.Qt.PenCapStyle.RoundCap))
            painter.drawLine(center, base)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            heads = [(tip, base)]
            if arrow.clamped:  # off the scale: a second head behind the first
                heads.append((QtCore.QPointF(tip.x() - dx * head * 0.8,
                                             tip.y() - dy * head * 0.8),
                              QtCore.QPointF(base.x() - dx * head * 0.8,
                                             base.y() - dy * head * 0.8)))
            for point, foot in heads:
                painter.drawPolygon(QtGui.QPolygonF([
                    point,
                    QtCore.QPointF(foot.x() - dy * head * 0.6, foot.y() + dx * head * 0.6),
                    QtCore.QPointF(foot.x() + dy * head * 0.6, foot.y() - dx * head * 0.6)]))

            number = f"{arrow.value:.4f}"
            label = arrow.label()
            vcenter = flags.AlignVCenter
            if dx:  # beside the diagram: name, value, percent and word stacked
                x = center.x() + dx * (reach_px + 8) + (0 if dx > 0 else -side)
                top = center.y() - line * 1.5
                align = flags.AlignLeft if dx > 0 else flags.AlignRight
                painter.setFont(small)
                painter.setPen(muted)
                painter.drawText(QtCore.QRectF(x, top, side, line), int(align | vcenter),
                                 arrow.name)
                painter.drawText(QtCore.QRectF(x, top + 2 * line, side, line),
                                 int(align | vcenter), label)
                painter.setFont(value_font)
                painter.setPen(text_color)
                painter.drawText(QtCore.QRectF(x, top + line, side, line),
                                 int(align | vcenter), number)
            else:   # above and below: one line
                gap = small_metrics.horizontalAdvance("  ")
                widths = (small_metrics.horizontalAdvance(arrow.name),
                          value_metrics.horizontalAdvance(number),
                          small_metrics.horizontalAdvance(label))
                y = center.y() + dy * (reach_px + 6) - (line if dy < 0 else 0)
                x = center.x() - (sum(widths) + 2 * gap) / 2
                for width, font, pen, piece in ((widths[0], small, muted, arrow.name),
                                                (widths[1], value_font, text_color, number),
                                                (widths[2], small, muted, label)):
                    painter.setFont(font)
                    painter.setPen(pen)
                    painter.drawText(QtCore.QRectF(x, y, width + 2, line), int(vcenter),
                                     piece)
                    x += width + gap
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(text_color)
        painter.drawEllipse(center, 3.0, 3.0)
        painter.end()


# Former name of the widget; the calibration panel still exposes it as "compass".
CompassWidget = DirectionDiagram
