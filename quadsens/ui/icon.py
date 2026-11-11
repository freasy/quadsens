"""The application icon, built from the two shipped SVG drawings."""

from __future__ import annotations

from ..resources import ICON_FILE, ICON_SMALL_FILE, SMALL_ICON_UP_TO, resource_path
from .qt import QtGui

SIZES = (16, 24, 32, 48, 64, 128, 256)


def app_icon() -> QtGui.QIcon:
    """The QuadSens icon; a theme icon if the SVG cannot be loaded or rendered.

    One icon with a pixmap per size: the simplified drawing up to 24 pixels,
    where the full one gets muddy, and the full drawing above. Qt then picks
    the right one for the title bar, the task bar and the About dialog.
    """
    full_path, small_path = resource_path(ICON_FILE), resource_path(ICON_SMALL_FILE)
    if full_path.is_file():
        full = QtGui.QIcon(str(full_path))
        # Without Qt's SVG plugin the icon object exists but renders nothing.
        if not full.isNull() and not full.pixmap(32, 32).isNull():
            small = QtGui.QIcon(str(small_path)) if small_path.is_file() else full
            if small.isNull() or small.pixmap(16, 16).isNull():
                small = full
            icon = QtGui.QIcon()
            for size in SIZES:
                source = small if size <= SMALL_ICON_UP_TO else full
                icon.addPixmap(source.pixmap(size, size))
            return icon
    return QtGui.QIcon.fromTheme("quadsens", QtGui.QIcon.fromTheme("input-mouse"))
