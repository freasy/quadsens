"""Locate data files that ship with QuadSens (no Qt).

The same relative path works in three situations: a source checkout, the
single-file binary (PyInstaller unpacks its data below ``sys._MEIPASS``) and an
installed package (``pip install`` copies the icon below ``<prefix>/share``).
"""

from __future__ import annotations

import sys
import sysconfig
from pathlib import Path

ICON_FILE = "assets/quadsens.svg"
# Where pyproject.toml installs the icon, relative to the installation prefix.
_INSTALLED = {ICON_FILE: "share/icons/hicolor/scalable/apps/quadsens.svg"}


def resource_root() -> Path:
    """Directory that the relative resource paths are resolved against."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> Path:
    """Absolute path of a shipped file. The file is not guaranteed to exist."""
    path = resource_root() / relative
    if path.exists() or getattr(sys, "frozen", False) or relative not in _INSTALLED:
        return path
    for scheme in (None, "posix_user"):
        try:
            base = (sysconfig.get_path("data") if scheme is None
                    else sysconfig.get_path("data", scheme))
        except KeyError:  # scheme unknown on this platform
            continue
        candidate = Path(base) / _INSTALLED[relative]
        if candidate.exists():
            return candidate
    return path
