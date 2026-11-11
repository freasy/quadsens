"""What the Linux and the Windows main window share: View menu, information boxes."""

from __future__ import annotations

import html

from .. import about
from ..diagram import DEFAULT_ZOOM, ZOOMS, number, zoom_of
from .qt import QtCore, QtGui, QtWidgets
from .theme import MODE_TITLES, MODES, ThemeManager, env_mode


def about_html() -> str:
    """The About text with markup: bold title and a clickable project link."""
    title, summary, license_line, url, disclaimer = (
        html.escape(line) for line in about.about_lines())
    return (f"<h3>{title}</h3><p>{summary}</p>"
            f"<p>{license_line}<br><a href=\"{url}\">{url}</a></p>"
            f"<p>{disclaimer}</p>")


class WindowCommon:
    """Mixin for a QMainWindow with ``_settings``, ``theme`` and a ``calibration_panel``."""

    _settings: QtCore.QSettings
    theme: ThemeManager
    info_box: QtWidgets.QMessageBox | None

    def build_view_menu(self) -> None:
        # No mnemonic: every letter of "View" belongs to a control of the window.
        view_menu = self.menuBar().addMenu("View")
        theme_menu = view_menu.addMenu("&Theme")
        self.theme_group = QtGui.QActionGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_actions: dict[str, QtGui.QAction] = {}
        for mode in MODES:
            action = QtGui.QAction(MODE_TITLES[mode], self)
            action.setCheckable(True)
            action.setChecked(mode == self.theme.mode())
            # A theme named in the environment cannot be changed from here.
            action.setEnabled(env_mode() is None)
            action.triggered.connect(lambda _checked=False, mode=mode: self.set_theme(mode))
            self.theme_group.addAction(action)
            theme_menu.addAction(action)
            self.theme_actions[mode] = action
        self.theme_menu = theme_menu

        zoom_menu = view_menu.addMenu("Diagram &zoom")
        self.zoom_group = QtGui.QActionGroup(self)
        self.zoom_group.setExclusive(True)
        self.zoom_actions: dict[float, QtGui.QAction] = {}
        stored = self._settings.value("ui/diagram_zoom", DEFAULT_ZOOM, type=float)
        current = zoom_of(float(stored))
        for centre in ZOOMS:
            action = QtGui.QAction("Centre at %s" % number(centre), self)
            action.setCheckable(True)
            action.setChecked(centre == current)
            action.setToolTip("The middle of the direction diagram stands for %s, its "
                              "dashed ring for 1.0" % number(centre))
            action.triggered.connect(
                lambda _checked=False, centre=centre: self.set_diagram_zoom(centre))
            self.zoom_group.addAction(action)
            zoom_menu.addAction(action)
            self.zoom_actions[centre] = action
        zoom_menu.setToolTipsVisible(True)
        self.zoom_menu = zoom_menu
        self.calibration_panel.diagram.set_zoom(current)

    def set_diagram_zoom(self, centre: float) -> None:
        """Choose what the centre of the direction diagram stands for; remembered."""
        centre = zoom_of(centre)
        self._settings.setValue("ui/diagram_zoom", centre)
        self.calibration_panel.diagram.set_zoom(centre)
        for value, action in self.zoom_actions.items():
            action.setChecked(value == centre)

    def set_theme(self, mode: str) -> None:
        """Switch the theme at once and remember it."""
        self._settings.setValue("ui/theme", mode)
        self.theme.set_mode(mode)
        for name, action in self.theme_actions.items():
            action.setChecked(name == self.theme.mode())
        self.update_steps()

    def show_about(self) -> None:
        self._open_box("About %s" % about.APP_NAME, about_html())

    def _open_box(self, title: str, rich_text: str) -> None:
        """Show an information box without blocking the event loop."""
        if self.info_box is not None:
            self.info_box.close()
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(title)
        box.setTextFormat(QtCore.Qt.TextFormat.RichText)
        box.setText(rich_text)
        box.setIconPixmap(self.windowIcon().pixmap(64, 64))
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Close)
        box.finished.connect(self._box_closed)
        self.info_box = box
        box.open()

    def _box_closed(self, _result: int = 0) -> None:
        box, self.info_box = self.info_box, None
        if box is not None:
            box.deleteLater()
