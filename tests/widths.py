"""Helpers for the tests that guard the minimum width of the windows.

Texts that are not known in advance (paths, versions, names from files,
messages of other programs) must never raise the minimum width of a window.
When a width test fails, report() names the widest widgets, so that the log
of a CI run on another platform says which one it was.
"""

from __future__ import annotations

from quadsens.ui.qt import QtGui, QtWidgets
from quadsens.ui.status import ElideLabel, MessageLabel, WrapLabel

# A word no layout can break, a path as Windows has them, and a long sentence.
LONG_WORD = "W" * 300
LONG_PATH = "C:\\Users\\" + "a-very-long-user-name-" * 4 + "\\AppData\\Local\\" \
    + "\\".join("folder-number-%02d" % n for n in range(12)) + "\\settings.json"
LONG_SENTENCE = " ".join(["something went wrong in an unexpected way"] * 12)
LONG_TEXTS = (LONG_WORD, LONG_PATH, LONG_SENTENCE)
# Width of this text in the font the limits were set with (Noto Sans 9 pt at
# 96 dpi). Other fonts scale the limits; see font_scale().
REFERENCE_TEXT = "The quick brown fox jumps over the lazy dog 0123456789"
REFERENCE_WIDTH = 323


def font_scale() -> float:
    """How much wider text is here than where the limits were set, at least 1."""
    app = QtWidgets.QApplication.instance()
    width = QtGui.QFontMetrics(app.font()).horizontalAdvance(REFERENCE_TEXT)
    return max(1.0, width / REFERENCE_WIDTH)


def describe(widget: QtWidgets.QWidget) -> str:
    text = ""
    getter = getattr(widget, "text", None)
    if callable(getter) and not isinstance(widget, QtWidgets.QAbstractSpinBox):
        try:
            text = str(getter())
        except (TypeError, RuntimeError):
            text = ""
    hint = widget.minimumSizeHint()
    return "%4d x %-4d %s name=%r accessible=%r text=%r" % (
        max(hint.width(), widget.minimumWidth()), hint.height(), type(widget).__name__,
        widget.objectName(), widget.accessibleName()[:50], text[:60])


def report(root: QtWidgets.QWidget, count: int = 14) -> str:
    """The font in use and the widest visible widgets below ``root``."""
    app = QtWidgets.QApplication.instance()
    info = QtGui.QFontInfo(app.font())
    children = [child for child in root.findChildren(QtWidgets.QWidget)
                if not child.isHidden()]
    children.sort(key=lambda child: max(child.minimumSizeHint().width(),
                                        child.minimumWidth()), reverse=True)
    lines = ["minimum size hint of the content: %d x %d" % (
                 root.minimumSizeHint().width(), root.minimumSizeHint().height()),
             "font: %s %.1f pt, fixed pitch %s, style %s, text scale %.2f" % (
                 info.family(), info.pointSizeF(), info.fixedPitch(),
                 app.style().objectName(), font_scale()),
             "widest widgets (minimum width x height):"]
    lines += ["  " + describe(child) for child in children[:count]]
    return "\n".join(lines)


def fill_dynamic(root: QtWidgets.QWidget, text: str) -> int:
    """Put ``text`` into every slot for text that is not known in advance.

    Those are the labels that elide or wrap, the message banners and the line
    edits. Returns how many were filled.
    """
    filled = 0
    for banner in root.findChildren(MessageLabel):
        banner.show_message(text, error=True)
        banner.setVisible(True)
        filled += 1
    for label in root.findChildren(QtWidgets.QLabel):
        if isinstance(label, (ElideLabel, WrapLabel)) and not label.isHidden():
            label.setText(text)
            filled += 1
    for edit in root.findChildren(QtWidgets.QLineEdit):
        if not edit.objectName().startswith("qt_"):
            edit.setText(text)
            filled += 1
    return filled


def plain_labels_with(root: QtWidgets.QWidget, markers: tuple[str, ...]) -> list[str]:
    """Labels that neither wrap nor elide but show one of ``markers``."""
    found = []
    for label in root.findChildren(QtWidgets.QLabel):
        if isinstance(label, (ElideLabel, WrapLabel)):
            continue
        if any(marker in label.text() for marker in markers):
            found.append(describe(label))
    return found
