"""The application's look: Fusion plus one stylesheet generated from a colour set.

There are three themes (View > Theme, remembered):

    quadsens  the built-in black and red colour set; the default. It does not
              change when the desktop switches between light and dark.
    system    the same look, with colours derived from the system palette, so
              it follows light and dark schemes and the desktop's accent.
    native    no custom styling at all: the desktop's own style and palette.

All colours come from quadsens.colors.derive_theme(), fed either with the
built-in base colours or with five colours of the system palette. For the
built-in theme a matching QPalette is set on the application as well, so that
everything the stylesheet does not reach (menus, tooltips, dialogs, check-box
indicators) matches. Nothing here animates.

Environment: QUADSENS_THEME=quadsens|system|native overrides the stored
choice; QUADSENS_NATIVE_STYLE=1 is the older spelling of "native".
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

from ..colors import Theme, derive_theme, mix, quadsens_theme
from .qt import QtCore, QtGui, QtWidgets

ENV_NATIVE = "QUADSENS_NATIVE_STYLE"
ENV_THEME = "QUADSENS_THEME"
QUADSENS, SYSTEM, NATIVE = "quadsens", "system", "native"
MODES: tuple[str, ...] = (QUADSENS, SYSTEM, NATIVE)
MODE_TITLES = {
    QUADSENS: "&QuadSens (black and red)",
    SYSTEM: "&Follow system colours",
    NATIVE: "&System style",
}
DEFAULT_MODE = QUADSENS
RADIUS = 6          # controls
CARD_RADIUS = 8
CONTROL_HEIGHT = 28  # smallest height of anything that can be clicked

_CHEVRON = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
    '<path d="{path}" fill="none" stroke="{color}" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>')
_PATHS = {"down": "M2.5 4.5 L6 8 L9.5 4.5", "up": "M2.5 7.5 L6 4 L9.5 7.5"}
_CHECK_PATH = "M2.6 6.3 L5 8.6 L9.5 3.6"


def env_forces_native() -> bool:
    return os.environ.get(ENV_NATIVE, "").strip().lower() in ("1", "true", "yes", "on")


def env_mode() -> str | None:
    """The theme the environment asks for, None if it does not say."""
    wanted = os.environ.get(ENV_THEME, "").strip().lower()
    if wanted in MODES:
        return wanted
    return NATIVE if env_forces_native() else None


def stored_mode(settings: QtCore.QSettings) -> str:
    """The remembered theme; an old "use system style" switch becomes "native"."""
    stored = settings.value("ui/theme")
    if isinstance(stored, str) and stored in MODES:
        return stored
    if settings.contains("ui/native_style"):
        native = bool(settings.value("ui/native_style", False, type=bool))
        settings.remove("ui/native_style")
        if native:
            settings.setValue("ui/theme", NATIVE)
            return NATIVE
    return DEFAULT_MODE


def theme_from_palette(palette: QtGui.QPalette) -> Theme:
    role = QtGui.QPalette.ColorRole
    return derive_theme(
        palette.color(role.Window).name(), palette.color(role.Base).name(),
        palette.color(role.WindowText).name(), palette.color(role.Highlight).name(),
        palette.color(role.HighlightedText).name())


def palette_for(theme: Theme) -> QtGui.QPalette:
    """A complete QPalette that matches a colour set, for what the stylesheet misses."""
    role, group = QtGui.QPalette.ColorRole, QtGui.QPalette.ColorGroup
    palette = QtGui.QPalette()
    colours = {
        role.Window: theme.window, role.WindowText: theme.text,
        role.Base: theme.base, role.AlternateBase: theme.surface,
        role.Text: theme.text, role.Button: theme.control, role.ButtonText: theme.text,
        role.BrightText: theme.on_accent, role.ToolTipBase: theme.control,
        role.ToolTipText: theme.text, role.PlaceholderText: theme.muted,
        role.Highlight: theme.accent, role.HighlightedText: theme.on_accent,
        role.Link: theme.accent_line, role.LinkVisited: theme.accent_line,
        role.Light: theme.control_pressed, role.Midlight: theme.control_hover,
        role.Mid: theme.outline, role.Dark: mix(theme.window, theme.text, 0.02),
        role.Shadow: theme.window,
    }
    for name, value in colours.items():
        palette.setColor(name, QtGui.QColor(value))
    for name in (role.WindowText, role.Text, role.ButtonText, role.PlaceholderText):
        palette.setColor(group.Disabled, name, QtGui.QColor(theme.disabled))
    palette.setColor(group.Disabled, role.Highlight, QtGui.QColor(theme.control_pressed))
    palette.setColor(group.Disabled, role.HighlightedText, QtGui.QColor(theme.disabled))
    return palette


_cache: tuple[int, Theme] | None = None
_fixed: Theme | None = None   # set while the built-in theme is active


def current_theme() -> Theme:
    """The colour set in use: the built-in one, or the one of the palette as it is now."""
    global _cache
    if _fixed is not None:
        return _fixed
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
QToolTip { background: %(control)s; color: %(text)s; border: 1px solid %(outline)s; }

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
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    background: %(base)s;
    border: 1px solid %(outline)s;
    border-radius: 4px;
}
QCheckBox::indicator:hover { background: %(control_hover)s; }
QCheckBox::indicator:checked {
    background: %(accent)s;
    border: 1px solid %(accent)s;
    image: url(%(check)s);
}
QCheckBox::indicator:disabled { background: %(surface)s; border: 1px solid %(border)s; }
QCheckBox::indicator:checked:disabled {
    background: %(control)s;
    border: 1px solid %(border)s;
    image: url(%(check_off)s);
}

/* ---- banners ------------------------------------------------------------ */
QFrame[banner] { border: 1px solid transparent; border-radius: %(radius)dpx; }
QFrame[banner="info"] { background: %(info_bg)s; border: 1px solid %(info_mark)s; }
QFrame[banner="success"] { background: %(success_bg)s; border: 1px solid %(success_mark)s; }
QFrame[banner="warning"] { background: %(warning_bg)s; border: 1px solid %(warning_mark)s; }
/* An error gets the heaviest frame: it must stand out without a colour of its own. */
QFrame[banner="error"] { background: %(error_bg)s; border: 2px solid %(error_mark)s; }
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
    """Applies a theme to the application and keeps it in step with the palette."""

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
        self._mode = env_mode() or DEFAULT_MODE
        self._own_palette = False   # the application palette is ours, not the system's
        self._applying = False
        self._applied: str | None = None
        self._icon_dir = Path(tempfile.mkdtemp(prefix="quadsens-style-"))
        atexit.register(shutil.rmtree, self._icon_dir, True)
        app.installEventFilter(self)
        hints = app.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda *_: self.apply())

    # ---- state --------------------------------------------------------------

    def mode(self) -> str:
        """quadsens, system or native."""
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Switch the theme now. The environment, if it names a theme, wins."""
        mode = env_mode() or (mode if mode in MODES else DEFAULT_MODE)
        if mode != self._mode:
            self._mode = mode
        self.apply()

    def is_native(self) -> bool:
        """True when no custom styling is applied."""
        return self._mode == NATIVE

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
        for key, colour in (("check", theme.on_accent), ("check_off", theme.disabled)):
            target = self._icon_dir / f"check-{colour.lstrip('#')}.svg"
            if not target.exists():
                target.write_text(_CHEVRON.format(path=_CHECK_PATH, color=colour),
                                  encoding="ascii")
            icons[key] = target.as_posix()
        return icons

    def apply(self) -> None:
        """Set style, palette and stylesheet for the current theme."""
        global _fixed
        if self._applying:
            return
        self._applying = True
        try:
            style = self._system_style if self._mode == NATIVE else "Fusion"
            if self._style.lower() != style.lower():
                self._style = style
                self._app.setStyle(style)
            if self._mode == QUADSENS:
                _fixed = quadsens_theme()
                palette = palette_for(_fixed)
                if not self._own_palette or self._app.palette() != palette:
                    self._own_palette = True
                    self._app.setPalette(palette)
            else:
                _fixed = None
                if self._own_palette:
                    # A default palette hands the colours back to the system.
                    self._own_palette = False
                    self._app.setPalette(QtGui.QPalette())
            if self._mode == NATIVE:
                wanted = ""
            else:
                theme = current_theme()
                wanted = stylesheet(theme, self._icons(theme))
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
