"""The stepper on top of the window, with the button for the next step."""

from __future__ import annotations

from ..steps import (
    ALL_DONE,
    FIX_ACTIONS,
    SHORT_TITLES,
    Status,
    Step,
    StepInfo,
    current_step,
)
from . import theme
from .qt import QtCore, QtGui, QtWidgets, Signal
from .status import SPACE_M, SPACE_S, WrapLabel

CIRCLE = 22  # diameter of a step's circle


class StepChip(QtWidgets.QWidget):
    """One step: a numbered circle and its title.

    Done steps show a check mark in an outlined circle, the current step a
    filled circle and a bold title, pending steps a muted outline. The state is
    therefore readable from shape and weight; colour only supports it.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._info: StepInfo | None = None
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed,
                           QtWidgets.QSizePolicy.Policy.Fixed)

    def set_info(self, info: StepInfo) -> None:
        self._info = info
        self.setAccessibleName(info.describe())
        self.setToolTip(info.describe())
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        """Marker, number and title, e.g. "✓ 1 Start mouse"."""
        return self._info.chip() if self._info is not None else ""

    def is_current(self) -> bool:
        return self._info is not None and self._info.status is Status.CURRENT

    def _title_font(self, bold: bool) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setWeight(QtGui.QFont.Weight.DemiBold if bold else QtGui.QFont.Weight.Normal)
        return font

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 (Qt name)
        title = SHORT_TITLES[self._info.step] if self._info is not None else ""
        # Sized for the bold form, so that the row does not move when the step changes.
        width = QtGui.QFontMetrics(self._title_font(True)).horizontalAdvance(title)
        return QtCore.QSize(CIRCLE + SPACE_S + 2 + width + 2, CIRCLE + 2)

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802 (Qt name)
        return self.sizeHint()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:  # noqa: N802 (Qt name)
        info = self._info
        if info is None:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        accent, on_accent = theme.color("accent"), theme.color("on_accent")
        line = theme.color("accent_line")
        text, muted, outline = theme.color("text"), theme.color("muted"), theme.color("outline")
        circle = QtCore.QRectF(1, (self.height() - CIRCLE) / 2, CIRCLE, CIRCLE)
        font = self._title_font(True)
        painter.setFont(font)
        if info.status is Status.CURRENT:
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(circle)
            painter.setPen(on_accent)
            painter.drawText(circle, int(QtCore.Qt.AlignmentFlag.AlignCenter), str(info.number))
        elif info.status is Status.DONE:
            painter.setPen(QtGui.QPen(line, 1.6))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawEllipse(circle.adjusted(0.8, 0.8, -0.8, -0.8))
            pen = QtGui.QPen(line, 2.0, QtCore.Qt.PenStyle.SolidLine,
                             QtCore.Qt.PenCapStyle.RoundCap, QtCore.Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            cx, cy = circle.center().x(), circle.center().y()
            painter.drawPolyline(QtGui.QPolygonF([
                QtCore.QPointF(cx - 4.5, cy + 0.3), QtCore.QPointF(cx - 1.3, cy + 3.4),
                QtCore.QPointF(cx + 4.6, cy - 3.2)]))
        else:
            painter.setPen(QtGui.QPen(outline, 1.2))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawEllipse(circle.adjusted(0.6, 0.6, -0.6, -0.6))
            painter.setPen(muted)
            painter.setFont(self._title_font(False))
            painter.drawText(circle, int(QtCore.Qt.AlignmentFlag.AlignCenter), str(info.number))
        current = info.status is Status.CURRENT
        painter.setFont(self._title_font(current))
        painter.setPen(muted if info.status is Status.PENDING else text)
        left = circle.right() + SPACE_S + 2
        painter.drawText(QtCore.QRectF(left, 0, self.width() - left, self.height()),
                         int(QtCore.Qt.AlignmentFlag.AlignVCenter
                             | QtCore.Qt.AlignmentFlag.AlignLeft),
                         SHORT_TITLES[info.step])
        painter.end()


class StepLine(QtWidgets.QWidget):
    """The line between two steps: solid in the accent once the step before is done."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.done = False
        self.setMinimumWidth(8)
        self.setFixedHeight(CIRCLE + 2)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:  # noqa: N802 (Qt name)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        if self.done:
            pen = QtGui.QPen(theme.color("accent_line"), 2.0)
        else:
            pen = QtGui.QPen(theme.color("outline"), 1.2, QtCore.Qt.PenStyle.DashLine)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        y = self.height() / 2
        painter.drawLine(QtCore.QPointF(1, y), QtCore.QPointF(self.width() - 1, y))
        painter.end()


class StepsBar(QtWidgets.QWidget):
    """Shows done / current / pending for every step and offers the next action.

    The button on the right always carries out the current step; it is the
    window's primary button. The buttons inside the cards keep working in any
    order.
    """

    action_requested = Signal(object)  # Step
    fix_requested = Signal(str)        # what would unblock the current step

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Steps")
        self._current: StepInfo | None = None
        self.infos: list[StepInfo] = []

        self.chips: list[StepChip] = []
        self.lines: list[StepLine] = []
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(SPACE_S + 2)
        for index, _step in enumerate(Step):
            if index:
                line = StepLine()
                self.lines.append(line)
                row.addWidget(line, 1)
            chip = StepChip()
            self.chips.append(chip)
            row.addWidget(chip)

        self.next_label = WrapLabel("Next step")
        self.next_button = QtWidgets.QPushButton()
        self.next_button.setAutoDefault(True)
        self.next_button.setDefault(True)
        theme.set_primary(self.next_button, True)
        self.next_button.setShortcut(QtGui.QKeySequence("Ctrl+Return"))
        self.next_button.clicked.connect(self._clicked)

        action = QtWidgets.QHBoxLayout()
        action.setSpacing(SPACE_M)
        action.addWidget(self.next_label, 1, QtCore.Qt.AlignmentFlag.AlignVCenter)
        action.addWidget(self.next_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(SPACE_S, 0, SPACE_S, 0)
        layout.setSpacing(SPACE_S)
        layout.addLayout(row)
        layout.addLayout(action)

    def current(self) -> StepInfo | None:
        return self._current

    def set_steps(self, infos: list[StepInfo]) -> None:
        self.infos = list(infos)
        for index, (chip, info) in enumerate(zip(self.chips, infos)):
            chip.set_info(info)
            if index:
                self.lines[index - 1].done = infos[index - 1].status is Status.DONE
                self.lines[index - 1].update()
        self._current = current = current_step(infos)
        if current is None:
            self.next_label.setText(ALL_DONE)
            self.next_button.setText("Done")
            self.next_button.setEnabled(False)
            self.next_button.setAccessibleName("Next step: nothing left to do")
        else:
            text = f"Step {current.number} of {len(infos)}: {current.blocked or current.detail}"
            self.next_label.setText(text)
            action = current.action
            if current.blocked and current.fix in FIX_ACTIONS:
                # The step cannot work yet, but there is a place that fixes it.
                self.next_button.setText(FIX_ACTIONS[current.fix])
                self.next_button.setEnabled(True)
                self.next_button.setAccessibleName(
                    "Before the next step: open the driver setup")
            else:
                self.next_button.setText(action or "Waiting for the result")
                self.next_button.setEnabled(bool(action) and not current.blocked)
                self.next_button.setAccessibleName(
                    f"Next step: {action}" if action
                    else "Next step: waiting for the result")
        self.next_button.setShortcut(QtGui.QKeySequence("Ctrl+Return"))
        self.next_button.setToolTip(
            "Carries out the current step (Ctrl+Return). The buttons below do the same "
            "and can be used in any order.")
        self.next_button.setAccessibleDescription(self.next_label.text())

    def _clicked(self) -> None:
        current = self._current
        if current is not None and current.blocked and current.fix in FIX_ACTIONS:
            self.fix_requested.emit(current.fix)
            return
        if self._current is not None and self._current.action:
            self.action_requested.emit(self._current.step)
