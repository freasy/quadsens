"""Main window: virtual mouse on top, calibration and driver below."""

from __future__ import annotations

import html
from collections.abc import Callable

from .. import about
from ..calibration import result_folder
from ..driver import Driver
from ..emulator import (
    DEFAULT_PRODUCT_ID,
    UHID_PATH,
    EmulatorSession,
    LogEntry,
    uhid_access,
)
from ..hints import FIRST_STEPS, NO_RESULT_FOLDER, PERSIST
from .calibration_panel import CalibrationPanel
from .driver_panel import DriverPanel
from .icon import app_icon
from .mouse_panel import MousePanel
from .qt import QtCore, QtGui, QtWidgets

MARGIN = 8


def about_html() -> str:
    """The About text with markup: bold title and a clickable project link."""
    title, summary, license_line, url, disclaimer = (
        html.escape(line) for line in about.about_lines())
    return (f"<h3>{title}</h3><p>{summary}</p>"
            f"<p>{license_line}<br><a href=\"{url}\">{url}</a></p>"
            f"<p>{disclaimer}</p>")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, settings: QtCore.QSettings, calibration_glob: str, driver: Driver,
                 emulator_state_path: str | None = None,
                 parent: QtWidgets.QWidget | None = None,
                 uhid_path: str = UHID_PATH) -> None:
        super().__init__(parent)
        self._settings = settings
        self._calibration_glob = calibration_glob
        self.info_box: QtWidgets.QMessageBox | None = None
        # Replaceable, so that tests do not start a file manager.
        self.open_url: Callable[[QtCore.QUrl], object] = QtGui.QDesktopServices.openUrl
        self.setWindowTitle(about.APP_NAME)
        self.setWindowIcon(app_icon())

        def make_session(pid: int, on_log: Callable[[LogEntry], None]) -> EmulatorSession:
            return EmulatorSession(product_id=pid, state_path=emulator_state_path,
                                   on_log=on_log, uhid_path=uhid_path)

        self.mouse_panel = MousePanel(make_session, lambda: uhid_access(uhid_path))
        self.calibration_panel = CalibrationPanel(calibration_glob)
        self.driver_panel = DriverPanel(driver, settings,
                                        lambda: self.calibration_panel.calibration)
        self.mouse_panel.writes_changed.connect(
            lambda: self.calibration_panel.set_emulator_writes(self.mouse_panel.writes))

        lower = QtWidgets.QHBoxLayout()
        lower.setSpacing(MARGIN)
        lower.addWidget(self.calibration_panel, 3)
        lower.addWidget(self.driver_panel, 2)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        layout.setSpacing(MARGIN)
        layout.addWidget(self.mouse_panel, 1)
        layout.addLayout(lower)
        self.setCentralWidget(central)

        self._build_menus()
        # Keyboard order follows the workflow: mouse, calibration, driver.
        order: list[QtWidgets.QWidget] = [
            self.mouse_panel.pid_combo, self.mouse_panel.toggle_button,
            self.mouse_panel.copy_button, self.mouse_panel.clear_button,
            self.mouse_panel.log_view,
            self.calibration_panel.path_edit, self.calibration_panel.refresh_button,
            *self.driver_panel.spins,
            self.driver_panel.use_button, self.driver_panel.flip_check,
            self.driver_panel.reset_button, self.driver_panel.read_button,
            self.driver_panel.apply_button, self.driver_panel.auto_check,
        ]
        for first, second in zip(order, order[1:]):
            QtWidgets.QWidget.setTabOrder(first, second)
        self.tab_order = order

        self.mouse_panel.set_product_id(
            int(settings.value("emulator/pid", DEFAULT_PRODUCT_ID, type=int)))
        self.resize(900, 660)
        geometry = settings.value("window/geometry")
        if isinstance(geometry, QtCore.QByteArray) and not geometry.isEmpty():
            self.restoreGeometry(geometry)

    # ---- menus ------------------------------------------------------------

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.open_folder_action = QtGui.QAction("&Open result folder", self)
        self.open_folder_action.setShortcut(
            QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Open))
        self.open_folder_action.triggered.connect(self.open_result_folder)
        file_menu.addAction(self.open_folder_action)
        file_menu.addSeparator()
        self.quit_action = QtGui.QAction("&Quit", self)
        self.quit_action.setShortcut(QtGui.QKeySequence("Ctrl+Q"))
        self.quit_action.setMenuRole(QtGui.QAction.MenuRole.QuitRole)
        self.quit_action.triggered.connect(self.close)
        file_menu.addAction(self.quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        self.steps_action = QtGui.QAction("&First steps", self)
        self.steps_action.setShortcut(
            QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.HelpContents))
        self.steps_action.triggered.connect(self.show_first_steps)
        help_menu.addAction(self.steps_action)
        help_menu.addSeparator()
        self.about_action = QtGui.QAction("&About %s" % about.APP_NAME, self)
        self.about_action.setMenuRole(QtGui.QAction.MenuRole.AboutRole)
        self.about_action.triggered.connect(self.show_about)
        help_menu.addAction(self.about_action)

        # Qt adds this button itself; it shows up when the menus do not fit.
        for button in self.menuBar().findChildren(QtWidgets.QToolButton):
            if button.objectName() == "qt_menubar_ext_button":
                button.setAccessibleName("More menus")
                button.setFocusPolicy(QtCore.Qt.FocusPolicy.TabFocus)

    def show_first_steps(self) -> None:
        self._open_box("First steps", "<h3>First steps</h3><ol>%s</ol><p>%s</p>" % (
            "".join("<li>%s</li>" % html.escape(step) for step in FIRST_STEPS),
            html.escape(PERSIST)))

    def open_result_folder(self) -> None:
        """Open the folder of the newest result file in the file manager."""
        folder = result_folder(self._calibration_glob)
        if folder is None:
            self.calibration_panel.message.show_message(NO_RESULT_FOLDER)
            return
        self.open_url(QtCore.QUrl.fromLocalFile(str(folder)))

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

    # ---- life cycle -------------------------------------------------------

    def apply_saved_if_enabled(self) -> None:
        if self.driver_panel.auto_check.isChecked():
            self.driver_panel.apply_saved()

    def shutdown(self) -> None:
        """Destroy the virtual device and store the settings. Safe to call twice."""
        self.mouse_panel.stop()
        self.calibration_panel.stop_watching()
        self._settings.setValue("emulator/pid", self.mouse_panel.product_id())
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.sync()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 (Qt name)
        self.shutdown()
        super().closeEvent(event)
