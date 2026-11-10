"""Single import point for the Qt binding.

PySide6 is the supported binding. PyQt6 is accepted as a fallback because the
two are API compatible for everything used here; only the signal class has a
different name.
"""

from __future__ import annotations

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Signal

    BINDING = "PySide6"
except ImportError:  # pragma: no cover - depends on the installed binding
    from PyQt6 import QtCore, QtGui, QtWidgets  # type: ignore[no-redef]
    from PyQt6.QtCore import pyqtSignal as Signal  # type: ignore[no-redef]

    BINDING = "PyQt6"

__all__ = ["BINDING", "QtCore", "QtGui", "QtWidgets", "Signal"]
