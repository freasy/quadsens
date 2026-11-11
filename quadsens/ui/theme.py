"""The application's look: Fusion plus one stylesheet generated from the palette.

The colours come from quadsens.colors.derive_theme(), fed with five colours of
the system palette, so the look follows light and dark schemes and the
desktop's accent colour. The stylesheet is generated again whenever the
palette changes. Nothing here animates.

Escape hatch: with QUADSENS_NATIVE_STYLE=1 in the environment, or "Use system
style" in the View menu, neither Fusion nor the stylesheet is applied.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

from ..colors import Theme, derive_theme
from .qt import QtCore, QtGui, QtWidgets

ENV_NATIVE = "QUADSENS_NATIVE_STYLE"
RADIUS = 6          # controls
CARD_RADIUS = 8
CONTROL_HEIGHT = 28  # smallest height of anything that can be clicked

_CHEVRON = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
    '<path d="{path}" fill="none" stroke="{color}" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>')
_PATHS = {"down": "M2.5 4.5 L6 8 L9.5 4.5", "up": "M2.5 7.5 L6 4 L9.5 7.5"}


def env_forces_native() -> bool:
    return os.environ.get(ENV_NATIVE, "").strip().lower() in ("1", "true", "yes", "on")


def theme_from_palette(palette: QtGui.QPalette) -> Theme:
    role = QtGui.QPalette.ColorRole
    return derive_theme(
        palette.color(role.Window).name(), palette.color(role.Base).name(),
        palette.color(role.WindowText).name(), palette.color(role.Highlight).name(),
        palette.color(role.HighlightedText).name())


_cache: tuple[int, Theme] | None = None


def current_theme() -> Theme:
    """The colour set for the application palette as it is right now."""
    global _cache
    palette = QtWidgets.QApplication.palette()
    key = int(palette.cacheKey())
    if _cache is None or _cache[0] != key:
        _cache = (key, theme_from_palette(palette))
    return _cache[1]


def color(name: str) -> QtGui.QColor:
    """One colour of the current theme as a QColor, for widgets that paint themselves."""
    return QtGui.QColor(getattr(current_theme(), name))


def stylesheet(theme: Theme, icons: dict[str, str]) -> str:
    """The application stylesheet for a colour set. ``icons`` maps names to file paths."""
    values = dict(theme.as_dict())
    values.update(icons)
    values.update(radius=RADIUS, card_radius=CARD_RADIUS,
                  inner=CONTROL_HEIGHT - 8,  # content height: minus padding and border
                  # Qt adds its own frame allowance to these two.
                  spin_inner=CONTROL_HEIGHT - 11, tool_inner=CONTROL_HEIGHT - 11)
    return _TEMPLATE % values


_TEMPLATE = """
/* ---- text ------------------------------------------------------------ */
QLabel, QCheckBox, QToolButton { color: %(text)s; }
QLabel:disabled, QCheckBox:disabled, QToolButton:disabled { color: %(disabled)s; }
QLabel[role="caption"] { color: %(muted)s; }

/* ---- cards ----------------------------------------------------------- */
QFrame#card {
    background: %(surface)s;
    border: 1px solid %(border)s;
    border-radius: %(card_radius)dpx;
}

/* ---- buttons ---------------------------------------------------------- */
QPushButton {
    background: %(control)s;
    color: %(text)s;
    border: 1px solid %(outline)s;
    border-radius: %(radius)dpx;
    padding: 3px 12px;
    min-height: %(inner)dpx;
}
QPushButton:hover { background: %(control_hover)s; }
QPushButton:pressed { background: %(control_pressed)s; }
QPushButton:focus { border: 2px solid %(focus)s; padding: 2px 11px; }
QPushButton:disabled {
    background: transparent;
    color: %(disabled)s;
    border: 1px solid %(border)s;
}
QPushButton[primary="true"] {
    background: %(accent)s;
    color: %(on_accent)s;
    border: 1px solid %(accent)s;
    font-weight: 600;
}
QPushButton[primary="true"]:hover { background: %(accent_hover)s; }
QPushButton[primary="true"]:pressed { background: %(accent_pressed)s; }
QPushButton[primary="true"]:focus { border: 2px solid %(focus_on_accent)s; }
QPushButton[primary="true"]:disabled {
    background: %(control)s;
    color: %(disabled)s;
    border: 1px solid %(border)s;
}

/* ---- disclosure toggles ------------------------------------------------ */
QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: %(radius)dpx;
    padding: 3px 8px;
    min-height: %(tool_inner)dpx;
}
QToolButton:hover { background: %(control_hover)s; }
QToolButton:pressed { background: %(control_pressed)s; }
QToolButton:checked { background: %(control)s; border: 1px solid %(border)s; }
QToolButton:focus { border: 2px solid %(focus)s; padding: 2px 7px; }

/* ---- inputs ------------------------------------------------------------ */
QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit {
    background: %(base)s;
    color: %(text)s;
    border: 1px solid %(outline)s;
    border-radius: %(radius)dpx;
    selection-background-color: %(accent)s;
    selection-color: %(on_accent)s;
}
QLineEdit, QComboBox { padding: 3px 7px; min-height: %(inner)dpx; }
QAbstractSpinBox { padding: 3px 7px; min-height: %(spin_inner)dpx; }
QPlainTextEdit { padding: 3px; }
QLineEdit:read-only { background: %(control)s; }
QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus { border: 2px solid %(focus)s;
    padding: 2px 6px; }
QPlainTextEdit:focus { border: 2px solid %(focus)s; padding: 2px; }
QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {
    color: %(disabled)s;
    border: 1px solid %(border)s;
    background: %(surface)s;
}

/* The editor inside a spin box is part of it, not a field of its own. */
QAbstractSpinBox QLineEdit, QAbstractSpinBox QLineEdit:focus,
QAbstractSpinBox QLineEdit:disabled {
    background: transparent;
    border: none;
    padding: 0;
    min-height: 0;
}

QComboBox { padding-right: 24px; }
QComboBox:focus { padding-right: 23px; }
QComboBox::drop-down {
    subcontrol-origin: border;
    subcontrol-position: center right;
    width: 24px;
    border: none;
}
QComboBox::down-arrow { image: url(%(chevron_down)s); width: 12px; height: 12px; }
QComboBox::down-arrow:disabled { image: url(%(chevron_down_off)s); }
QComboBox QAbstractItemView {
    background: %(base)s;
    color: %(text)s;
    border: 1px solid %(outline)s;
    selection-background-color: %(accent)s;
    selection-color: %(on_accent)s;
    outline: 0;
}

QAbstractSpinBox { padding-right: 22px; }
QAbstractSpinBox:focus { padding-right: 21px; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    subcontrol-origin: border;
    width: 18px;
    border: none;
    border-radius: 3px;
    background: transparent;
}
QAbstractSpinBox::up-button { subcontrol-position: top right; margin: 3px 3px 0 0; }
QAbstractSpinBox::down-button { subcontrol-position: bottom right; margin: 0 3px 3px 0; }
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {
    background: %(control_hover)s; }
QAbstractSpinBox::up-button:pressed, QAbstractSpinBox::down-button:pressed {
    background: %(control_pressed)s; }
QAbstractSpinBox::up-arrow { image: url(%(chevron_up)s); width: 9px; height: 9px; }
QAbstractSpinBox::down-arrow { image: url(%(chevron_down)s); width: 9px; height: 9px; }
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {
    image: url(%(chevron_up_off)s); }
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {
    image: url(%(chevron_down_off)s); }

/* ---- check boxes -------------------------------------------------------- */
QCheckBox { spacing: 6px; padding: 2px; min-height: %(inner)dpx;
    border: 2px solid transparent; border-radius: %(radius)dpx; }
QCheckBox:focus { border: 2px solid %(focus)s; }

/* ---- banners ------------------------------------------------------------ */
QFrame[banner] { border: 1px solid transparent; border-radius: %(radius)dpx; }
QFrame[banner="info"] { background: %(info_bg)s; border: 1px solid %(info_mark)s; }
QFrame[banner="success"] { background: %(success_bg)s; border: 1px solid %(success_mark)s; }
QFrame[banner="warning"] { background: %(warning_bg)s; border: 1px solid %(warning_mark)s; }
QFrame[banner="error"] { background: %(error_bg)s; border: 1px solid %(error_mark)s; }
QFrame[banner="info"] QLabel#bannerMark { color: %(info_mark)s; }
QFrame[banner="success"] QLabel#bannerMark { color: %(success_mark)s; }
QFrame[banner="warning"] QLabel#bannerMark { color: %(warning_mark)s; }
QFrame[banner="error"] QLabel#bannerMark { color: %(error_mark)s; }

/* ---- scroll bars --------------------------------------------------------- */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle { background: %(handle)s; border-radius: 3px; }
QScrollBar::handle:vertical { min-height: 24px; }
QScrollBar::handle:horizontal { min-width: 24px; }
QScrollBar::handle:hover { background: %(muted)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QAbstractScrollArea::corner { background: transparent; }
"""


class ThemeManager(QtCore.QObject):
    """Applies the look to the application and keeps it in step with the palette."""

    _instance: ThemeManager | None = None

    @classmethod
    def instance(cls) -> ThemeManager:
        """The one manager of the running application."""
        app = QtWidgets.QApplication.instance()
        if cls._instance is None or cls._instance.parent() is not app:
            cls._instance = cls(app)
        return cls._instance

    def __init__(self, app: QtWidgets.QApplication) -> None:
        super().__init__(app)
        self._app = app
        self._system_style = app.style().objectName() or "Fusion"
        self._style = self._system_style
        self._native = env_forces_native()
        self._applying = False
        self._applied: str | None = None
        self._icon_dir = Path(tempfile.mkdtemp(prefix="quadsens-style-"))
        atexit.register(shutil.rmtree, self._icon_dir, True)
        app.installEventFilter(self)
        hints = app.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda *_: self.apply())

    # ---- state --------------------------------------------------------------

    def is_native(self) -> bool:
        """True when the custom look is switched off."""
        return self._native

    def set_native(self, native: bool) -> None:
        native = bool(native) or env_forces_native()
        if native != self._native:
            self._native = native
            self.apply()

    def theme(self) -> Theme:
        return current_theme()

    # ---- applying -----------------------------------------------------------

    def _icons(self, theme: Theme) -> dict[str, str]:
        icons: dict[str, str] = {}
        for suffix, colour in (("", theme.text), ("_off", theme.disabled)):
            for name, path in _PATHS.items():
                # The colour is part of the file name: Qt caches images by path.
                target = self._icon_dir / f"chevron-{name}-{colour.lstrip('#')}.svg"
                if not target.exists():
                    target.write_text(_CHEVRON.format(path=path, color=colour),
                                      encoding="ascii")
                icons[f"chevron_{name}{suffix}"] = target.as_posix()
        return icons

    def apply(self) -> None:
        """Set style and stylesheet for the current palette (or remove them)."""
        if self._applying:
            return
        self._applying = True
        try:
            if self._native:
                wanted = ""
                style = self._system_style
            else:
                theme = current_theme()
                wanted = stylesheet(theme, self._icons(theme))
                style = "Fusion"
            if self._style.lower() != style.lower():
                self._style = style
                self._app.setStyle(style)
            if wanted != self._applied:
                self._applied = wanted
                self._app.setStyleSheet(wanted)
        finally:
            self._applying = False

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # noqa: N802
        if (watched is self._app and not self._applying
                and event.type() == QtCore.QEvent.Type.ApplicationPaletteChange):
            QtCore.QTimer.singleShot(0, self.apply)
        return False


def set_primary(button: QtWidgets.QAbstractButton, primary: bool) -> None:
    """Mark a button as the primary action (accent filled) or as an ordinary one."""
    if bool(button.property("primary")) == primary:
        return
    button.setProperty("primary", primary)
    style = button.style()
    style.unpolish(button)
    style.polish(button)
    button.update()
