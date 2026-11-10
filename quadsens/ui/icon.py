"""The application icon, loaded from the shipped SVG file."""

from __future__ import annotations

from ..resources import ICON_FILE, resource_path
from .qt import QtGui


def app_icon() -> QtGui.QIcon:
    """The QuadSens icon; a theme icon if the SVG cannot be loaded or rendered."""
    path = resource_path(ICON_FILE)
    if path.is_file():
        icon = QtGui.QIcon(str(path))
        # Without Qt's SVG plugin the icon object exists but renders nothing.
        if not icon.isNull() and not icon.pixmap(32, 32).isNull():
            return icon
    return QtGui.QIcon.fromTheme("quadsens", QtGui.QIcon.fromTheme("input-mouse"))
