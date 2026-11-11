"""Main window on Windows: virtual mouse over USB/IP, values in, RawAccel out.

The virtual mouse is served over USB/IP and attached through usbip-win2,
which the user installs. Without it the window still does everything else:
the multipliers can be imported or typed and applied to RawAccel. Nothing of
the Linux chain (/dev/uhid, yeetmouse, pkexec, the driver installer) is
imported here. The calibration card, the diagram, the stepper and the themes
are shared with Linux.
"""

from __future__ import annotations

import html
import os
import time
from collections.abc import Callable, Mapping

from .. import about, usbip_client
from ..calibration import NO_RESULT_FOLDER, result_folder
from ..steps import (
    FIX_FOLDER,
    FIX_IMPORT,
    WIN_ALL_DONE,
    Status,
    StepInfo,
    WinFlowState,
    WinStep,
    derive_windows,
    is_newer,
)
from ..usbip_mouse import VirtualMouse
from ..usbip_server import VirtualMouseServer
from .calibration_panel import CalibrationPanel
from .icon import app_icon
from .qt import QtCore, QtGui, QtWidgets
from .rawaccel_panel import RawAccelPanel
from .status import SPACE_L, SPACE_M
from .steps_bar import StepsBar
from .theme import ThemeManager, set_primary, stored_mode
from .usbip_panel import UsbipCheckStrip, UsbipMousePanel
from .values_io import ValuesIO
from .window_common import WindowCommon

DEFAULT_SIZE = (900, 720)
# Below the size at which everything fits, the page scrolls instead of being cut.
MINIMUM_SIZE = (760, 520)
NO_RESULT_WINDOWS = (
    "No calibration file found yet. Run the calibration with the virtual mouse started, "
    "import a values file (File > Import values), or type the four multipliers on the "
    "right.")
FIRST_STEPS_WINDOWS: tuple[str, ...] = (
    "Start the virtual mouse, then start the game. This needs usbip-win2; the first "
    "line of the window says whether it is installed.",
    "Put RawAccel's own values back (Restore original), so that the calibration "
    "measures the unscaled mouse.",
    "Run the per-direction sensitivity calibration in the game. The result shows up "
    "in the window on its own.",
    "Press Use result.",
    "Check the table (current and new RawAccel settings) and press Apply.",
)
WITHOUT_MOUSE = ("Without usbip-win2: import a values file exported by QuadSens on Linux "
                 "(File > Import values) or type the four multipliers, then Apply.")


class WindowsMainWindow(WindowCommon, ValuesIO, QtWidgets.QMainWindow):
    def __init__(self, settings: QtCore.QSettings, calibration_glob: str,
                 environ: Mapping[str, str] | None = None,
                 parent: QtWidgets.QWidget | None = None,
                 find_client: Callable[[], usbip_client.UsbipClient] | None = None,
                 mouse_factory: Callable[[int], VirtualMouse] | None = None) -> None:
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

        self._environ = os.environ if environ is None else environ
        # A result counts as new when it is younger than the mouse start or the
        # last return to RawAccel's own values, whichever came last.
        self._baseline: float | None = None
        self._find_client = find_client or (lambda: usbip_client.UsbipClient(
            usbip_client.find_tool(self._environ)))

        def make_mouse(product_id: int) -> VirtualMouse:
            # The server listens on this PC only (127.0.0.1); there is no setting for it.
            return VirtualMouse(self._find_client(), VirtualMouseServer(product_id))

        self.usbip_strip = UsbipCheckStrip(self._find_client)
        self.steps_bar = StepsBar()
        self.steps_bar.all_done_text = WIN_ALL_DONE
        self.mouse_panel = UsbipMousePanel(mouse_factory or make_mouse, settings)
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
        layout.addWidget(self.usbip_strip)
        layout.addWidget(self.steps_bar)
        layout.addWidget(self.mouse_panel)
        layout.addLayout(lower, 1)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.scroll.setWidget(central)
        self.setCentralWidget(self.scroll)
        self.setMinimumSize(*MINIMUM_SIZE)

        self._build_menus()
        self.calibration_panel.calibration_changed.connect(self.update_steps)
        self.rawaccel_panel.changed.connect(self.update_steps)
        self.rawaccel_panel.flip_check.toggled.connect(self.calibration_panel.set_flip_y)
        self.steps_bar.action_requested.connect(self.run_step)
        self.steps_bar.fix_requested.connect(self.run_fix)
        self.usbip_strip.open_releases.connect(
            lambda: self.open_url(QtCore.QUrl(usbip_client.RELEASES_URL)))
        self.usbip_strip.checked.connect(self.update_steps)
        self.mouse_panel.writes_changed.connect(self._writes_changed)
        self.mouse_panel.running_changed.connect(self._mouse_changed)

        panel = self.rawaccel_panel
        order: list[QtWidgets.QWidget] = [
            self.usbip_strip.get_button, self.usbip_strip.recheck_button,
            self.steps_bar.next_button,
            self.mouse_panel.toggle_button, self.mouse_panel.pid_combo,
            self.mouse_panel.log_toggle, self.mouse_panel.log_view,
            self.mouse_panel.copy_button, self.mouse_panel.clear_button,
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
        self.steps_bar.next_button.setFocus()
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
            html.escape(WITHOUT_MOUSE)))

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

    def _writes_changed(self) -> None:
        """The game wrote directional values to the virtual mouse: show them."""
        panel = self.calibration_panel
        panel.set_emulator_writes(self.mouse_panel.writes)
        if panel.calibration is None and self.mouse_panel.writes.scalars() is not None \
                and not panel.message.is_error:
            panel.message.show_message(
                "Showing the values the game sent to the virtual mouse.")

    def _mouse_changed(self, _running: bool = False) -> None:
        started = self.mouse_panel.started_at()
        if self.mouse_panel.is_running() and started is not None \
                and (self._baseline is None or started > self._baseline):
            self._baseline = started
        self.update_steps()

    def flow_state(self) -> WinFlowState:
        panel = self.rawaccel_panel
        result = self.calibration_panel.result()
        return WinFlowState(
            mouse_running=self.mouse_panel.is_running(),
            mouse_possible=self.usbip_strip.found,
            folder_ok=panel.document is not None and panel.current() is not None,
            folder_problem=panel.problem,
            neutral=panel.is_neutral(),
            result_available=result is not None,
            result_is_new=result is not None and is_newer(result.time, self._baseline),
            values_ready=panel.values_ready(),
            applied=panel.is_applied())

    def update_steps(self) -> list[StepInfo]:
        infos = derive_windows(self.flow_state())
        self.steps_bar.set_steps(infos)
        panel = self.rawaccel_panel
        if panel.is_busy():
            self.steps_bar.next_button.setEnabled(False)
        current = next((info for info in infos if info.status is Status.CURRENT), None)
        primary = None
        if current is not None and not current.blocked:
            primary = {WinStep.START: self.mouse_panel.toggle_button,
                       WinStep.RESET: panel.restore_button, WinStep.USE: panel.use_button,
                       WinStep.APPLY: panel.apply_button}.get(current.step)
        elif current is not None and current.fix == FIX_FOLDER:
            primary = panel.choose_button
        for button in (self.mouse_panel.toggle_button, panel.restore_button,
                       panel.use_button, panel.apply_button, panel.choose_button):
            set_primary(button, button is primary)
        return infos

    def run_step(self, step: WinStep) -> None:
        panel = self.rawaccel_panel
        if step is WinStep.START:
            self.mouse_panel.start()
        elif step is WinStep.RESET:
            panel.restore()
            # Only a calibration from now on is a new one.
            self._baseline = time.time()
        elif step is WinStep.USE:
            panel.use_result()
        elif step is WinStep.APPLY:
            panel.apply()
        self.update_steps()

    def run_fix(self, fix: str) -> None:
        """What the stepper offers when the current step cannot work yet."""
        if fix == FIX_FOLDER:
            self.rawaccel_panel.choose_folder()
        elif fix == FIX_IMPORT:
            self.import_values()
        self.update_steps()

    # ---- life cycle ---------------------------------------------------------------

    def apply_saved_if_enabled(self) -> None:
        """Nothing is applied by itself on Windows."""

    def shutdown(self) -> None:
        """Detach our port, stop serving, store the settings. Safe to call twice."""
        self.mouse_panel.stop()
        self.calibration_panel.stop_watching()
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.sync()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 (Qt name)
        self.shutdown()
        super().closeEvent(event)
