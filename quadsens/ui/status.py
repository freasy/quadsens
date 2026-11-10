"""Small status widgets: state is always given as text, colour only supports it."""

from __future__ import annotations

from .qt import QtCore, QtGui, QtWidgets


class StatusDot(QtWidgets.QWidget):
    """A round marker: hollow = off, half tone = waiting, filled = active.

    Colours come from the palette. The marker is decorative; the label next to
    it carries the same information as text.
    """

    OFF, WAITING, ACTIVE = range(3)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = self.OFF
        self.setFixedSize(14, 14)

    def set_state(self, state: int) -> None:
        self._state = state
        self.update()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:  # noqa: N802 (Qt name)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        painter.setPen(QtGui.QPen(palette.color(QtGui.QPalette.ColorRole.WindowText), 1.2))
        if self._state == self.ACTIVE:
            painter.setBrush(palette.color(QtGui.QPalette.ColorRole.Highlight))
        elif self._state == self.WAITING:
            painter.setBrush(palette.color(QtGui.QPalette.ColorRole.Mid))
        else:
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QtCore.QRectF(2, 2, 10, 10))
        painter.end()


class WrapLabel(QtWidgets.QLabel):
    """Plain-text label that wraps, never widens its parent and is never cut off.

    Qt's height-for-width handling does not work through nested layouts: a
    wrapping label in a group box makes the layouts above it treat preferred
    heights as minimum heights and squeeze everything. This label therefore
    opts out of height-for-width and instead claims, as a fixed height, what
    its text needs at the width it was given.
    """

    def __init__(self, accessible_name: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._needed = self.fontMetrics().height()
        self.setAccessibleName(accessible_name)
        self.setWordWrap(True)
        self.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                           QtWidgets.QSizePolicy.Policy.Fixed)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt name)
        return False

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 (Qt name)
        return QtCore.QSize(super().sizeHint().width(), self._needed)

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802 (Qt name)
        return QtCore.QSize(0, self._needed)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt name)
        super().setText(text)
        self._claim_height()

    def setFont(self, font: QtGui.QFont) -> None:  # noqa: N802 (Qt name)
        super().setFont(font)
        self._claim_height()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802 (Qt name)
        super().resizeEvent(event)
        self._claim_height()

    def _claim_height(self) -> None:
        width = self.width()
        needed = self.heightForWidth(width) if width > 0 else -1
        if needed <= 0:
            needed = self.fontMetrics().height()
        if needed != self._needed:
            self._needed = needed
            self.updateGeometry()


class MessageLabel(WrapLabel):
    """Inline status/error line. Errors are marked by wording and weight, not colour."""

    def __init__(self, accessible_name: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(accessible_name, parent)
        self.is_error = False

    def show_message(self, text: str, error: bool = False) -> None:
        self.is_error = error
        font = self.font()
        font.setBold(error)
        self.setFont(font)
        self.setText(("Error: " + text) if error and text else text)
        self.setToolTip(self.text())
