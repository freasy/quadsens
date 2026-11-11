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
# The limits of the windows are written for one font: the one they were
# designed with (Noto Sans 9 pt at 96 dpi), in which REFERENCE_TEXT is
# REFERENCE_WIDTH pixels wide and a line REFERENCE_HEIGHT pixels high. On a
# machine with a wider or taller font the limits grow by the same factor
# (never shrink): widths by the width of that text; heights by the line
# height or by the width factor, whichever is larger, because wrapped text
# takes more lines when the font is wider. That is the one rule;
# assert_fits() applies it.
REFERENCE_TEXT = "The quick brown fox jumps over the lazy dog 0123456789"
REFERENCE_WIDTH = 323
REFERENCE_HEIGHT = 17
# What a window may need at most, in the reference font, before it scrolls.
LINUX_FIT = (720, 900)             # the whole Linux window, acceleration card included
WINDOWS_CONTENT_WIDTH = 760 - 16   # the page of the Windows window: no sideways scrolling
WINDOWS_DEFAULT_WIDTH = 900 - 16   # ... and with every table filled, at the default size


def apply_test_font(app: QtWidgets.QApplication) -> None:
    """Use the font named in QUADSENS_TEST_FONT ("Family:points"), if any.

    For running the size tests with other fonts than this machine's, to see
    what another machine or a CI runner sees.
    """
    import os
    wanted = os.environ.get("QUADSENS_TEST_FONT", "")
    if wanted:
        family, _, points = wanted.partition(":")
        app.setFont(QtGui.QFont(family, int(points or 9)))


def height_scale() -> float:
    """How much more height text needs here than where the limits were set, at least 1.

    The larger of the line height factor and the width factor: a wider font
    makes wrapped text run to more lines.
    """
    app = QtWidgets.QApplication.instance()
    return max(1.0, QtGui.QFontMetrics(app.font()).height() / REFERENCE_HEIGHT, font_scale())


def assert_fits(case, size, limit: tuple[float | None, float | None],
                root: QtWidgets.QWidget, what: str = "") -> None:
    """Fail unless ``size`` is within ``limit`` (width, height; None: not checked).

    Width and height are checked one by one, against limits scaled for the
    font in use. The failure names the font and the largest widgets below
    ``root``.
    """
    for name, got, allowed, scale in (
            ("width", size.width(), limit[0], font_scale()),
            ("height", size.height(), limit[1], height_scale())):
        if allowed is not None and got > allowed * scale:
            case.fail("%s%s %d is more than %d (limit %d in the reference font, times "
                      "%.2f for this font)\n%s" % (what + ": " if what else "", name, got,
                                                    allowed * scale, allowed, scale,
                                                    report(root)))


def assert_not_wider(case, root: QtWidgets.QWidget, size, baseline: int,
                     what: str) -> None:
    """Fail when ``size`` is wider than the ``baseline`` measured before in the same test."""
    if size.width() > baseline:
        case.fail("%s made the minimum width grow from %d to %d\n%s"
                  % (what, baseline, size.width(), report(root)))


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
    tallest = sorted(children, key=lambda child: child.minimumSizeHint().height(),
                     reverse=True)[:6]
    children.sort(key=lambda child: max(child.minimumSizeHint().width(),
                                        child.minimumWidth()), reverse=True)
    lines = ["minimum size hint of the content: %d x %d" % (
                 root.minimumSizeHint().width(), root.minimumSizeHint().height()),
             "font: %s %.1f pt, fixed pitch %s, style %s, width scale %.2f, height "
             "scale %.2f" % (info.family(), info.pointSizeF(), info.fixedPitch(),
                             app.style().objectName(), font_scale(), height_scale()),
             "widest widgets (minimum width x height):"]
    lines += ["  " + describe(child) for child in children[:count]]
    lines.append("tallest widgets:")
    lines += ["  " + describe(child) for child in tallest]
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
