"""Main window on Windows: get the four multipliers and apply them to RawAccel.

There is no virtual mouse and no driver setup here; the modules for those are
not imported. The calibration card, the diagram, the stepper and the themes
are the same as on Linux.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping

from .. import about
from ..calibration import NO_RESULT_FOLDER, result_folder
from ..steps import WIN_ALL_DONE, Status, StepInfo, WinFlowState, WinStep, derive_windows
from .calibration_panel import CalibrationPanel
from .icon import app_icon
from .qt import QtCore, QtGui, QtWidgets
from .rawaccel_panel import RawAccelPanel
from .status import SPACE_L, SPACE_M
from .steps_bar import StepsBar
from .theme import ThemeManager, set_primary, stored_mode
from .values_io import ValuesIO
from .window_common import WindowCommon

DEFAULT_SIZE = (900, 640)
NO_RESULT_WINDOWS = (
    "No calibration file found. Import a values file from your Linux machine (File > "
    "Import values), or type the four multipliers on the right.")
FIRST_STEPS_WINDOWS: tuple[str, ...] = (
    "Get the four multipliers: the newest result file is read on its own; otherwise "
    "import a values file exported by QuadSens on Linux, or type them.",
    "Choose the folder where you unpacked RawAccel (the one with rawaccel.exe and "
    "settings.json).",
    "Look at the preview: current and new value of the four RawAccel settings.",
    "Press Apply. QuadSens writes settings.json and runs writer.exe, which makes "
    "RawAccel load it.",
)


class WindowsMainWindow(WindowCommon, ValuesIO, QtWidgets.QMainWindow):
    def __init__(self, settings: QtCore.QSettings, calibration_glob: str,
                 environ: Mapping[str, str] | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._calibration_glob = calibration_glob
        self.info_box: QtWidgets.QMessageBox | None = None
        # Replaceable, so that tests do not start a file manager.
        self.open_url: Callable[[QtCore.QUrl], object] = QtGui.QDesktopServices.openUrl
        self.setWindowTitle(about.APP_NAME)
        self.setWindowIcon(app_icon())
        self.theme = ThemeManager.instance()
        self.theme.set_mode(stored_mode(settings))

        self.steps_bar = StepsBar()
        self.steps_bar.all_done_text = WIN_ALL_DONE
        self.calibration_panel = CalibrationPanel(calibration_glob, settings,
                                                  no_result_text=NO_RESULT_WINDOWS)
        self.rawaccel_panel = RawAccelPanel(settings, self.calibration_panel.result, environ)
        self.calibration_panel.set_flip_y(self.rawaccel_panel.flip())

        lower = QtWidgets.QHBoxLayout()
        lower.setSpacing(SPACE_M)
        lower.addWidget(self.calibration_panel, 1)
        lower.addWidget(self.rawaccel_panel, 1)
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(SPACE_L, SPACE_M - 2, SPACE_L, SPACE_M - 2)
        layout.setSpacing(SPACE_M - 2)
        layout.addWidget(self.steps_bar)
        layout.addLayout(lower, 1)
        self.setCentralWidget(central)

        self._build_menus()
        self.calibration_panel.calibration_changed.connect(self.update_steps)
        self.rawaccel_panel.changed.connect(self.update_steps)
        self.rawaccel_panel.flip_check.toggled.connect(self.calibration_panel.set_flip_y)
        self.steps_bar.action_requested.connect(self.run_step)

        panel = self.rawaccel_panel
        order: list[QtWidgets.QWidget] = [
            self.steps_bar.next_button,
            self.calibration_panel.details_toggle, self.calibration_panel.path_edit,
            self.calibration_panel.refresh_button,
            panel.advanced_toggle, panel.folder_edit, panel.choose_button,
            panel.profile_combo, *panel.spins, panel.use_button, panel.reset_button,
            panel.apply_button, panel.restore_button, panel.flip_check,
        ]
        for first, second in zip(order, order[1:]):
            QtWidgets.QWidget.setTabOrder(first, second)
        self.tab_order = order

        self.update_steps()
        self.resize(*DEFAULT_SIZE)
        geometry = settings.value("window/geometry")
        if isinstance(geometry, QtCore.QByteArray) and not geometry.isEmpty():
            self.restoreGeometry(geometry)

    # ---- menus ------------------------------------------------------------------

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.open_folder_action = QtGui.QAction("&Open result folder", self)
        self.open_folder_action.setShortcut(
            QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Open))
        self.open_folder_action.triggered.connect(self.open_result_folder)
        file_menu.addAction(self.open_folder_action)
        self.add_values_actions(file_menu)
        file_menu.addSeparator()
        self.quit_action = QtGui.QAction("&Quit", self)
        self.quit_action.setShortcut(QtGui.QKeySequence("Ctrl+Q"))
        self.quit_action.setMenuRole(QtGui.QAction.MenuRole.QuitRole)
        self.quit_action.triggered.connect(self.close)
        file_menu.addAction(self.quit_action)

        self.build_view_menu()

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
        for button in self.menuBar().findChildren(QtWidgets.QToolButton):
            if button.objectName() == "qt_menubar_ext_button":
                button.setAccessibleName("More menus")
                button.setFocusPolicy(QtCore.Qt.FocusPolicy.TabFocus)

    def show_first_steps(self) -> None:
        self._open_box("First steps", "<h3>First steps</h3><ol>%s</ol><p>%s</p>" % (
            "".join("<li>%s</li>" % html.escape(step) for step in FIRST_STEPS_WINDOWS),
            "This version for Windows only applies values. The calibration itself needs "
            "the virtual mouse, which exists on Linux only."))

    def open_result_folder(self) -> None:
        folder = result_folder(self._calibration_glob)
        if folder is None:
            self.calibration_panel.message.show_message(NO_RESULT_FOLDER)
            return
        self.open_url(QtCore.QUrl.fromLocalFile(str(folder)))

    def values_imported(self) -> None:
        """Imported values go straight into the multipliers: that is what they are for."""
        self.rawaccel_panel.use_result()

    # ---- steps --------------------------------------------------------------------

    def flow_state(self) -> WinFlowState:
        panel = self.rawaccel_panel
        return WinFlowState(
            values_ready=panel.values_ready(),
            result_available=self.calibration_panel.result() is not None,
            folder_ok=panel.document is not None and panel.current() is not None,
            folder_problem=panel.problem,
            applied=panel.is_applied())

    def update_steps(self) -> list[StepInfo]:
        infos = derive_windows(self.flow_state())
        self.steps_bar.set_steps(infos)
        panel = self.rawaccel_panel
        if panel.is_busy():
            self.steps_bar.next_button.setEnabled(False)
        current = next((info for info in infos if info.status is Status.CURRENT), None)
        primary = {WinStep.VALUES: panel.use_button, WinStep.FOLDER: panel.choose_button,
                   WinStep.APPLY: panel.apply_button}.get(current.step) if current else None
        for button in (panel.use_button, panel.choose_button, panel.apply_button):
            set_primary(button, button is primary)
        return infos

    def run_step(self, step: WinStep) -> None:
        panel = self.rawaccel_panel
        if step is WinStep.VALUES:
            panel.use_result()
        elif step is WinStep.FOLDER:
            panel.choose_folder()
        elif step is WinStep.APPLY:
            panel.apply()
        self.update_steps()

    # ---- life cycle ---------------------------------------------------------------

    def apply_saved_if_enabled(self) -> None:
        """Nothing is applied by itself on Windows."""

    def shutdown(self) -> None:
        self.calibration_panel.stop_watching()
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.sync()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 (Qt name)
        self.shutdown()
        super().closeEvent(event)
