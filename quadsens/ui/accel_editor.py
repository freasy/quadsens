"""The acceleration editor shared by both driver cards.

Mode picker, the parameters of the chosen mode, a live check against the
limits and a preview of the curve. It writes nothing itself: it asks its card
to apply a curve, to revert, or to open the driver's own settings program.
"""

from __future__ import annotations

import math

from .. import accel_edit as ae
from ..accel_edit import Curve, Found
from .qt import QtCore, QtGui, QtWidgets, Signal
from .scale_spin import ScaleSpinBox
from .status import SPACE_M, SPACE_S, MessageLabel, WrapLabel, caption

RANGES = (20, 50, 100, 150)            # upper end of the speed axis, counts/ms
DEFAULT_RANGE = 100
SAMPLES = 160
CHANGE_NOTE = ("Acceleration changes how the mouse feels far more than the four "
               "directional values do. Nothing changes until you press Apply acceleration.")


class CurvePreview(QtWidgets.QWidget):
    """The multiplier over the input speed, for the edited curve and the current one.

    The two lines differ in style, not only in colour (solid: new, dashed:
    current), and are named in the corner. Points of the curve (cap, offset,
    jump) are marked and named.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._new: Curve | None = None
        self._current: Curve | None = None
        self._range = DEFAULT_RANGE
        self.setMinimumSize(220, 150)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Preview of the acceleration curve")

    def set_curves(self, new: Curve | None, current: Curve | None) -> None:
        self._new, self._current = new, current
        self._describe()
        self.update()

    def set_range(self, upper: int) -> None:
        self._range = upper
        self._describe()
        self.update()

    def speed_range(self) -> int:
        return self._range

    def lines(self) -> list[tuple[str, Curve]]:
        """What is drawn: ("new", curve) and, when it differs, ("current", curve)."""
        drawn: list[tuple[str, Curve]] = []
        if self._new is not None:
            drawn.append(("new", self._new))
        if self._current is not None and not self._current.same(self._new):
            drawn.append(("current", self._current))
        return drawn

    def top(self) -> float:
        """Upper end of the multiplier axis: a round number above every drawn value."""
        highest = 1.0
        for _name, curve in self.lines():
            for step in range(1, SAMPLES + 1):
                value = ae.multiplier(curve, self._range * step / SAMPLES)
                if math.isfinite(value):
                    highest = max(highest, value)
        for limit in (1.25, 1.5, 2, 3, 4, 5, 8, 10, 15, 20, 50, 100, 1000):
            if highest <= limit:
                return float(limit)
        return float(highest)

    def _describe(self) -> None:
        parts = ["Input speed 0 to %d %s across, sensitivity multiplier upwards."
                 % (self._range, ae.SPEED_UNIT)]
        for name, curve in self.lines():
            sample = ", ".join("%s at %d" % (ae.number(round(ae.multiplier(curve, speed), 3)),
                                             speed)
                               for speed in (self._range // 10 or 1, self._range // 2,
                                             self._range))
            parts.append("%s curve: %s; multiplier %s."
                         % (name.capitalize(), curve.describe(), sample))
        if not self.lines():
            parts.append("No curve to show.")
        self.setAccessibleDescription(" ".join(parts))

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:  # noqa: N802 (Qt name)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        text = palette.color(QtGui.QPalette.ColorRole.WindowText)
        muted = QtGui.QColor(text)
        muted.setAlphaF(0.45)
        faint = QtGui.QColor(text)
        faint.setAlphaF(0.15)
        accent = palette.color(QtGui.QPalette.ColorRole.Highlight)
        metrics = painter.fontMetrics()
        left, bottom_gap, top_gap, right = 34, metrics.height() + 6, 8, 10
        plot = QtCore.QRectF(left, top_gap, max(self.width() - left - right, 10),
                             max(self.height() - top_gap - bottom_gap, 10))
        top = self.top()

        def point(speed: float, value: float) -> QtCore.QPointF:
            x = plot.left() + plot.width() * speed / self._range
            y = plot.bottom() - plot.height() * min(max(value, 0.0), top) / top
            return QtCore.QPointF(x, y)

        # Axes, a line at 1.0 and a few numbers.
        painter.setPen(QtGui.QPen(faint, 1))
        for fraction in (0.25, 0.5, 0.75, 1.0):
            x = plot.left() + plot.width() * fraction
            painter.drawLine(QtCore.QPointF(x, plot.top()), QtCore.QPointF(x, plot.bottom()))
        painter.setPen(QtGui.QPen(muted, 1, QtCore.Qt.PenStyle.DotLine))
        painter.drawLine(point(0, 1.0), point(self._range, 1.0))
        painter.setPen(QtGui.QPen(muted, 1))
        painter.drawLine(plot.bottomLeft(), plot.bottomRight())
        painter.drawLine(plot.bottomLeft(), plot.topLeft())
        painter.setPen(text)
        for value in (0.0, 1.0, top):
            y = point(0, value).y()
            painter.drawText(QtCore.QRectF(0, y - metrics.height() / 2, left - 4,
                                           metrics.height()),
                             int(QtCore.Qt.AlignmentFlag.AlignRight
                                 | QtCore.Qt.AlignmentFlag.AlignVCenter), ae.number(value))
        for fraction in (0.0, 0.5, 1.0):
            label = "%d" % round(self._range * fraction)
            if fraction == 1.0:
                label += " " + ae.SPEED_UNIT
            width = metrics.horizontalAdvance(label)
            x = plot.left() + plot.width() * fraction
            x = min(max(x - width / 2, 0), self.width() - width - 1)
            painter.drawText(QtCore.QPointF(x, self.height() - 3), label)

        lines = self.lines()
        for index, (name, curve) in enumerate(reversed(lines)):
            path = QtGui.QPainterPath()
            for step in range(SAMPLES + 1):
                speed = max(self._range * step / SAMPLES, 1e-6)
                spot = point(speed, ae.multiplier(curve, speed))
                if step == 0:
                    path.moveTo(spot)
                else:
                    path.lineTo(spot)
            new = name == "new"
            pen = QtGui.QPen(accent if new else text, 2.2 if new else 1.4)
            if not new:
                pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawPath(path)
            if new:
                for label, speed in ae.marks(curve):
                    if 0 < speed <= self._range:
                        spot = point(speed, ae.multiplier(curve, speed))
                        painter.setBrush(accent)
                        painter.drawEllipse(spot, 3.5, 3.5)
                        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                        painter.setPen(text)
                        width = metrics.horizontalAdvance(label)
                        x = min(spot.x() + 6, plot.right() - width)
                        y = spot.y() + metrics.height() + 2 if spot.y() < plot.top() + 30 \
                            else spot.y() - 6
                        painter.drawText(QtCore.QPointF(x, y), label)
                        painter.setPen(pen)
        # The key: line style and name, so that colour is never needed.
        # It sits low on the right: curves start near 1 and rarely come down there.
        key_width = 44 + metrics.horizontalAdvance("current (dashed)")
        key_left = max(plot.right() - key_width, plot.left())
        y = plot.bottom() - (len(lines) - 1) * (metrics.height() + 2) - 6
        for name, _curve in lines:
            new = name == "new"
            pen = QtGui.QPen(accent if new else text, 2.2 if new else 1.4)
            if not new:
                pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QtCore.QPointF(key_left, y - metrics.height() / 3),
                             QtCore.QPointF(key_left + 26, y - metrics.height() / 3))
            painter.setPen(text)
            painter.drawText(QtCore.QPointF(key_left + 32, y),
                             "new (solid)" if new else "current (dashed)")
            y += metrics.height() + 2
        painter.end()


class _Page(QtWidgets.QWidget):
    """The controls of one mode."""

    changed = Signal()

    def __init__(self, mode: str, driver: str) -> None:
        super().__init__()
        self.mode = mode
        self.spins: dict[str, ScaleSpinBox] = {}
        self.cap_check: QtWidgets.QCheckBox | None = None
        self.cap_spin: ScaleSpinBox | None = None
        self.gain_check: QtWidgets.QCheckBox | None = None
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE_M)
        grid.setVerticalSpacing(SPACE_S)
        grid.setColumnStretch(1, 1)
        row = 0
        if mode == ae.OFF:
            grid.addWidget(caption(QtWidgets.QLabel(ae.MODE_HELP[ae.OFF])), 0, 0, 1, 2)
            return
        for field in ae.FIELDS[mode]:
            spin = self._spin(field, driver)
            label = QtWidgets.QLabel(field.label + ":")
            label.setBuddy(spin)
            grid.addWidget(label, row, 0)
            grid.addWidget(spin, row, 1)
            self.spins[field.key] = spin
            row += 1
        if mode in ae.CAP:
            field = ae.CAP[mode]
            self.cap_check = QtWidgets.QCheckBox("Cap at:")
            self.cap_check.setAccessibleName("Use a cap")
            self.cap_spin = self._spin(field, driver)
            self.cap_spin.setEnabled(False)
            self.cap_check.setToolTip(self.cap_spin.toolTip())
            self.cap_check.setAccessibleDescription(self.cap_spin.toolTip())
            self.cap_check.toggled.connect(self.cap_spin.setEnabled)
            self.cap_check.toggled.connect(lambda _checked: self.changed.emit())
            grid.addWidget(self.cap_check, row, 0)
            grid.addWidget(self.cap_spin, row, 1)
            row += 1
        if mode in ae.GAIN_MODES:
            self.gain_check = QtWidgets.QCheckBox("Gain (smoother)")
            self.gain_check.setChecked(True)
            self.gain_check.setToolTip(ae.GAIN_HELP)
            self.gain_check.setAccessibleDescription(ae.GAIN_HELP)
            self.gain_check.toggled.connect(lambda _checked: self.changed.emit())
            grid.addWidget(self.gain_check, row, 0, 1, 2)

    def _spin(self, field: ae.Field, driver: str) -> ScaleSpinBox:
        spin = ScaleSpinBox()
        spin.setDecimals(max(field.decimals, 4))
        spin.setRange(field.low, field.high)
        spin.setSingleStep(field.step)
        spin.setValue(field.default)
        own = field.yeetmouse if driver == "yeetmouse" else field.rawaccel
        tip = "%s Allowed: %s to %s. In %s this is \"%s\"." % (
            field.help, ae.number(field.low), ae.number(field.high), driver, own)
        spin.setToolTip(tip)
        spin.setAccessibleName("%s (%s)" % (field.label, ae.MODE_TITLES[self.mode]))
        spin.setAccessibleDescription(tip)
        spin.valueChanged.connect(lambda _value: self.changed.emit())
        return spin

    def curve(self) -> Curve:
        values = {key: spin.value() for key, spin in self.spins.items()}
        cap = self.cap_spin.value() if self.cap_check is not None \
            and self.cap_check.isChecked() and self.cap_spin is not None else None
        gain = self.gain_check.isChecked() if self.gain_check is not None else True
        return ae.make(self.mode, cap, gain, **values)

    def show_curve(self, curve: Curve) -> None:
        for key, spin in self.spins.items():
            spin.blockSignals(True)
            spin.setValue(curve.get(key))
            spin.blockSignals(False)
        if self.cap_check is not None and self.cap_spin is not None:
            self.cap_check.blockSignals(True)
            self.cap_check.setChecked(curve.cap is not None)
            self.cap_check.blockSignals(False)
            self.cap_spin.setEnabled(curve.cap is not None)
            if curve.cap is not None:
                self.cap_spin.blockSignals(True)
                self.cap_spin.setValue(curve.cap)
                self.cap_spin.blockSignals(False)
        if self.gain_check is not None:
            self.gain_check.blockSignals(True)
            self.gain_check.setChecked(curve.gain)
            self.gain_check.blockSignals(False)


class AccelEditor(QtWidgets.QWidget):
    apply_requested = Signal(object)        # the Curve to write
    revert_requested = Signal()
    edited = Signal()

    def __init__(self, driver: str, direction_note: str,
                 parent: QtWidgets.QWidget | None = None) -> None:
        """``driver`` is "yeetmouse" or "RawAccel"; ``direction_note`` says how the
        curve and the four directional values interact on this system."""
        super().__init__(parent)
        self._driver = driver
        self._found = Found(reason="Not read yet.")
        self._can_revert = False
        self._busy = False

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.setAccessibleName("Acceleration mode")
        for mode in ae.MODES:
            self.mode_combo.addItem(ae.MODE_TITLES[mode], mode)
            self.mode_combo.setItemData(self.mode_combo.count() - 1, ae.MODE_HELP[mode],
                                        QtCore.Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setToolTip("The shape of the curve. QuadSens edits the six modes "
                                   "that both drivers compute the same way.")
        self.mode_combo.setAccessibleDescription(self.mode_combo.toolTip())
        mode_label = QtWidgets.QLabel("Mode:")
        mode_label.setBuddy(self.mode_combo)
        self.mode_help = caption(WrapLabel("What the chosen mode does"))
        self.pages: dict[str, _Page] = {}
        self.stack = QtWidgets.QStackedWidget()
        for mode in ae.MODES:
            page = _Page(mode, driver)
            page.changed.connect(self._changed)
            self.pages[mode] = page
            self.stack.addWidget(page)
        self.range_combo = QtWidgets.QComboBox()
        self.range_combo.setAccessibleName("Speed range of the preview")
        for upper in RANGES:
            self.range_combo.addItem("0 to %d %s" % (upper, ae.SPEED_UNIT), upper)
        self.range_combo.setCurrentIndex(RANGES.index(DEFAULT_RANGE))
        self.range_combo.setToolTip("How far the speed axis of the preview goes. A fast "
                                    "flick of the mouse is some tens of counts per "
                                    "millisecond.")
        self.range_combo.setAccessibleDescription(self.range_combo.toolTip())
        range_label = QtWidgets.QLabel("Preview:")
        range_label.setBuddy(self.range_combo)
        self.preview = CurvePreview()

        self.readonly_label = MessageLabel("Why the acceleration cannot be edited here")
        self.change_label = WrapLabel("Current and new acceleration")
        self.problem_label = MessageLabel("Why this curve is not allowed")
        self.note_label = caption(WrapLabel("About acceleration and the directional values"))
        self.note_label.setText(direction_note + " " + CHANGE_NOTE)
        self.apply_button = QtWidgets.QPushButton("Apply acceleration")
        self.apply_button.setToolTip("Write the curve shown as \"new\" to the driver.")
        self.apply_button.clicked.connect(self._apply)
        self.revert_button = QtWidgets.QPushButton("Revert to previous")
        self.revert_button.setToolTip("Put back the acceleration that was active before "
                                      "the last Apply acceleration (one step).")
        self.revert_button.clicked.connect(self.revert_requested.emit)
        self.reset_button = QtWidgets.QPushButton("Show current")
        self.reset_button.setToolTip("Drop the edits and show the acceleration that is "
                                     "active now.")
        self.reset_button.clicked.connect(self.show_current)
        for control in (self.apply_button, self.revert_button, self.reset_button):
            control.setAccessibleDescription(control.toolTip())

        controls = QtWidgets.QGridLayout()
        controls.setHorizontalSpacing(SPACE_M)
        controls.setVerticalSpacing(SPACE_S)
        controls.addWidget(mode_label, 0, 0)
        controls.addWidget(self.mode_combo, 0, 1)
        controls.addWidget(range_label, 0, 2)
        controls.addWidget(self.range_combo, 0, 3)
        controls.setColumnStretch(1, 1)
        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(SPACE_M)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.revert_button)
        buttons.addWidget(self.reset_button)
        buttons.addStretch(1)
        self.editing = QtWidgets.QWidget()
        editing = QtWidgets.QVBoxLayout(self.editing)
        editing.setContentsMargins(0, 0, 0, 0)
        editing.setSpacing(SPACE_S)
        editing.addLayout(controls)
        editing.addWidget(self.mode_help)
        editing.addWidget(self.stack)
        editing.addWidget(self.preview, 1)
        editing.addWidget(self.change_label)
        editing.addWidget(self.problem_label)
        editing.addLayout(buttons)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)
        layout.addWidget(self.readonly_label)
        layout.addWidget(self.editing)
        layout.addWidget(self.note_label)

        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.range_combo.currentIndexChanged.connect(
            lambda _index: self.preview.set_range(int(self.range_combo.currentData())))
        self.set_found(self._found)

    # ---- state ------------------------------------------------------------------------

    def found(self) -> Found:
        return self._found

    def curve(self) -> Curve:
        """The curve as edited (it may be one that is not allowed)."""
        return self.pages[str(self.mode_combo.currentData())].curve()

    def is_dirty(self) -> bool:
        return self._found.editable and not self.curve().same(self._found.curve)

    def problems(self) -> list[str]:
        return ae.violations(self.curve())

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh()

    def set_can_revert(self, possible: bool) -> None:
        self._can_revert = possible
        self._refresh()

    def set_found(self, found: Found, keep_edits: bool = False) -> None:
        """Show what the driver is set to. Edits in progress survive if asked to."""
        dirty = keep_edits and self.is_dirty()
        self._found = found
        if found.curve is not None and not dirty:
            self.set_curve(found.curve)
        self._refresh()

    def show_current(self) -> None:
        if self._found.curve is not None:
            self.set_curve(self._found.curve)

    def set_curve(self, curve: Curve) -> None:
        index = ae.MODES.index(curve.mode)
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(index)
        self.mode_combo.blockSignals(False)
        self.stack.setCurrentIndex(index)
        self.pages[curve.mode].show_curve(curve)
        self._refresh()

    def _mode_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self._changed()

    def _changed(self) -> None:
        self._refresh()
        self.edited.emit()

    def _apply(self) -> None:
        if self._found.editable and not self.problems():
            self.apply_requested.emit(self.curve())

    def _refresh(self) -> None:
        found = self._found
        self.editing.setVisible(found.editable)
        self.readonly_label.setVisible(not found.editable)
        self.revert_button.setEnabled(self._can_revert and not self._busy)
        if not found.editable:
            self.readonly_label.show_message(
                "Read-only here: %s Change it in the settings program of %s; QuadSens "
                "does not convert it." % (found.reason, self._driver), kind="warning")
            self.apply_button.setEnabled(False)
            self.setAccessibleDescription("Acceleration is read-only. " + found.reason)
            return
        curve = self.curve()
        self.mode_help.setText(ae.MODE_HELP[curve.mode])
        problems = ae.violations(curve)
        dirty = not curve.same(found.curve)
        assert found.curve is not None
        if dirty:
            self.change_label.setText("Current: %s  →  New: %s"
                                      % (found.curve.describe(), curve.describe()))
        else:
            self.change_label.setText("Current: %s (no changes)" % found.curve.describe())
        self.problem_label.setVisible(bool(problems))
        if problems:
            self.problem_label.show_message("Not allowed: " + " ".join(problems),
                                            kind="warning")
        else:
            self.problem_label.show_message("")
        self.apply_button.setEnabled(dirty and not problems and not self._busy)
        self.reset_button.setEnabled(dirty)
        self.preview.set_curves(None if problems else curve, found.curve)
        self.setAccessibleDescription(
            "Acceleration editor. Current: %s. Edited: %s. %s"
            % (found.curve.describe(), curve.describe(),
               "Not allowed: " + " ".join(problems) if problems else "Allowed."))
