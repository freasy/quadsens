"""Small status widgets: state is always given as text, colour only supports it."""

from __future__ import annotations

from .qt import QtCore, QtGui, QtWidgets


# One spacing scale for the whole window: multiples of 4.
SPACE_S, SPACE_M, SPACE_L = 4, 8, 12
# One typographic scale, as factors of the application font.
FONT_HEADING, FONT_BODY, FONT_CAPTION = 1.1, 1.0, 0.92
# Chevrons of the disclosure toggles: collapsed and expanded.
CHEVRON_CLOSED, CHEVRON_OPEN = "▸", "▾"


def heading(text: str) -> QtWidgets.QLabel:
    """Section heading: semibold, slightly larger than the body text."""
    label = QtWidgets.QLabel(text)
    font = label.font()
    font.setWeight(QtGui.QFont.Weight.DemiBold)
    font.setPointSizeF(font.pointSizeF() * FONT_HEADING)
    label.setFont(font)
    label.setProperty("role", "heading")
    return label


def caption(label: QtWidgets.QLabel) -> QtWidgets.QLabel:
    """Turn a label into secondary text: smaller and, in the custom look, muted."""
    font = label.font()
    font.setPointSizeF(font.pointSizeF() * FONT_CAPTION)
    label.setFont(font)
    label.setProperty("role", "caption")
    return label


def column_header(text: str) -> QtWidgets.QLabel:
    label = caption(QtWidgets.QLabel(text))
    font = label.font()
    font.setWeight(QtGui.QFont.Weight.DemiBold)
    label.setFont(font)
    return label


class Card(QtWidgets.QFrame):
    """A section of the window: its own surface with a border and padding."""

    def __init__(self, accessible_name: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setAccessibleName(accessible_name)

    def card_layout(self) -> QtWidgets.QVBoxLayout:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(SPACE_L, SPACE_M - 2, SPACE_L, SPACE_M - 2)
        layout.setSpacing(SPACE_S)
        return layout


class Disclosure(QtWidgets.QToolButton):
    """Button that shows or hides a part of the window.

    A chevron in front of the text points right while the part is hidden and
    down while it is shown; the checked state carries the same information for
    assistive technology.
    """

    def __init__(self, text: str, target: QtWidgets.QWidget,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._target = target
        self._label = text
        self.setAccessibleName(text.replace("&", ""))
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.TabFocus)
        self.toggled.connect(self._apply)
        self._apply(False)

    def label(self) -> str:
        """The text without the chevron."""
        return self._label

    def _apply(self, shown: bool) -> None:
        self.setText("%s %s" % (CHEVRON_OPEN if shown else CHEVRON_CLOSED, self._label))
        self._target.setVisible(shown)


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


class ElideLabel(QtWidgets.QLabel):
    """One-line label for text that is not known in advance (states, versions, paths).

    It never raises the minimum width of the window: when there is not room
    for the text it is shortened with an ellipsis (in the middle for paths),
    and the tooltip then has it in full. text() and what a screen reader gets
    stay the full text; only the painting is shortened.
    """

    MIN_CHARS = 6

    def __init__(self, text: str = "", parent: QtWidgets.QWidget | None = None,
                 mode: QtCore.Qt.TextElideMode = QtCore.Qt.TextElideMode.ElideRight) -> None:
        super().__init__(text, parent)
        self._mode = mode
        self._tip = ""              # a tooltip given from outside wins over the full text
        self._shows_full_text = False
        self.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        policy = self.sizePolicy()
        policy.setHorizontalPolicy(QtWidgets.QSizePolicy.Policy.Preferred)
        self.setSizePolicy(policy)

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802 (Qt name)
        metrics = self.fontMetrics()
        width = min(super().sizeHint().width(),
                    metrics.horizontalAdvance("x" * self.MIN_CHARS))
        return QtCore.QSize(width, super().minimumSizeHint().height())

    def setToolTip(self, text: str) -> None:  # noqa: N802 (Qt name)
        self._tip = text
        super().setToolTip(text)

    def is_elided(self) -> bool:
        margins = self.contentsMargins()
        room = self.width() - margins.left() - margins.right() - 2 * self.margin()
        return self.fontMetrics().horizontalAdvance(self.text()) > room

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:  # noqa: N802 (Qt name)
        rect = self.contentsRect().adjusted(self.margin(), 0, -self.margin(), 0)
        text = self.fontMetrics().elidedText(self.text(), self._mode, max(rect.width(), 0))
        elided = text != self.text()
        if not self._tip and elided != self._shows_full_text:
            self._shows_full_text = elided
            super().setToolTip(self.text() if elided else "")
        painter = QtGui.QPainter(self)
        self.style().drawItemText(painter, rect, int(self.alignment()), self.palette(),
                                  self.isEnabled(), text, self.foregroundRole())
        painter.end()

    def setText(self, text: str) -> None:  # noqa: N802 (Qt name)
        super().setText(text)
        if self._shows_full_text:
            super().setToolTip(text)
        self.update()


class ActiveIndicator(QtWidgets.QFrame):
    """Says whether the driver is applying the values: mark, headline, one line of detail.

    The tint comes from the kind of the state, the meaning from the mark and
    the words. Nothing moves.
    """

    def __init__(self, accessible_name: str, parent: QtWidgets.QWidget | None = None,
                 wrap: bool = False) -> None:
        """``wrap``: let the detail take as many lines as it needs instead of
        cutting it. For places that may grow; the default keeps to one line."""
        super().__init__(parent)
        self._name = accessible_name
        self.kind = ""
        self.mark = QtWidgets.QLabel()
        self.mark.setObjectName("bannerMark")
        font = self.mark.font()
        font.setBold(True)
        self.mark.setFont(font)
        self.mark.setFixedWidth(self.mark.fontMetrics().horizontalAdvance("⚠") + 4)
        self.mark.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.headline = ElideLabel()
        bold = self.headline.font()
        bold.setWeight(QtGui.QFont.Weight.DemiBold)
        self.headline.setFont(bold)
        self.detail: QtWidgets.QLabel = caption(
            WrapLabel(accessible_name + " detail") if wrap else ElideLabel())
        self.detail.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(SPACE_S)
        top.addWidget(self.mark)
        top.addWidget(self.headline, 1)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(SPACE_S, 2, SPACE_S, 2)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addWidget(self.detail)
        self.setProperty("banner", "")

    def show_state(self, mark: str, headline: str, detail: str, tint: str,
                   kind: str = "") -> None:
        self.kind = kind
        self.mark.setText(mark)
        self.headline.setText(headline)
        self.detail.setText(detail)
        self.detail.setToolTip(detail)
        self.setAccessibleName("%s: %s. %s" % (self._name, headline, detail))
        if self.property("banner") != tint:
            self.setProperty("banner", tint)
            style = self.style()
            style.unpolish(self)
            style.polish(self)
            self.update()

    def text(self) -> str:
        return "%s %s" % (self.mark.text(), self.headline.text())


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
        self._tip = ""              # a tooltip given from outside wins
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
        # A word wider than the label (a long path) cannot wrap and is cut off:
        # the tooltip then has the whole text.
        if width > 0 and not self._tip:
            words = self.text().split()
            cut = any(self.fontMetrics().horizontalAdvance(word) > width for word in words)
            if cut != bool(QtWidgets.QLabel.toolTip(self)):
                QtWidgets.QLabel.setToolTip(self, self.text() if cut else "")

    def setToolTip(self, text: str) -> None:  # noqa: N802 (Qt name)
        self._tip = text
        super().setToolTip(text)


# Kinds of banner and the mark shown in front of the text, so that the kind
# never depends on the tint alone.
BANNER_MARKS = {"info": "ℹ", "success": "✓", "warning": "⚠", "error": "✕"}


class MessageLabel(QtWidgets.QFrame):
    """Inline banner for status and hint messages.

    Each kind (info, success, warning, error) has its own mark and, in the
    custom look, its own tint. Errors additionally start with the word "Error".
    An empty banner keeps its place in the layout but shows nothing.
    """

    def __init__(self, accessible_name: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.is_error = False
        self.kind = ""
        self._text = ""
        self.mark = QtWidgets.QLabel()
        self.mark.setObjectName("bannerMark")
        font = self.mark.font()
        font.setBold(True)
        self.mark.setFont(font)
        self.mark.setFixedWidth(self.mark.fontMetrics().horizontalAdvance("⚠") + 4)
        self.mark.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter
                               | QtCore.Qt.AlignmentFlag.AlignTop)
        self.label = WrapLabel(accessible_name)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(SPACE_M - 2, 3, SPACE_M, 3)
        layout.setSpacing(SPACE_S)
        layout.addWidget(self.mark, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.label, 1)
        self.setProperty("banner", "")

    def text(self) -> str:
        """The message as given, including the "Error: " prefix of errors."""
        return self._text

    def show_message(self, text: str, error: bool = False, kind: str = "") -> None:
        """Show ``text``; the kind defaults to error, success (text starts with ✓) or info."""
        self.is_error = error
        if not text:
            kind = ""
        elif error:
            kind = "error"
        elif not kind:
            kind = "success" if text.startswith("✓") else "info"
        self._text = ("Error: " + text) if error and text else text
        shown = self._text[1:].lstrip() if self._text.startswith("✓") else self._text
        self.mark.setText(BANNER_MARKS.get(kind, ""))
        self.label.setText(shown)
        self.label.setToolTip(shown)
        if kind != self.kind:
            self.kind = kind
            self.setProperty("banner", kind)
            style = self.style()
            style.unpolish(self)
            style.polish(self)
            self.update()
