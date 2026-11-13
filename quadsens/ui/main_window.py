"""Main window: the steps on top, then the virtual mouse, the result and the driver."""

from __future__ import annotations

import html
import os
import time
from collections.abc import Callable, Mapping

from .. import about
from .. import driver_setup, platforms, update_check
from ..driver import Driver
from ..emulator import (
    DEFAULT_PRODUCT_ID,
    UHID_PATH,
    EmulatorSession,
    LogEntry,
    uhid_access,
)
from ..calibration import NO_RESULT_FOLDER, result_folder
from ..hints import (
    ACCEL_HINT,
    FIRST_STEPS,
    PERSIST,
    setup_problems,
    tool_missing,
    uhid_short,
)
from ..model import Scalars
from ..persist import Saver
from ..steps import FlowState, Status, Step, StepInfo, derive, is_newer, is_unity
from .calibration_panel import CalibrationPanel
from .driver_panel import SAME_VALUE, DriverPanel
from .driver_strip import DriverCheckStrip
from .icon import app_icon
from .mouse_panel import MousePanel
from .qt import QtCore, QtGui, QtWidgets
from .setup_dialog import DriverSetupDialog
from .status import SPACE_L, SPACE_M
from .steps_bar import StepsBar
from .theme import ThemeManager, set_primary, stored_mode
from .update_bar import UPDATE_NOTE
from .values_io import ValuesIO
from .window_common import WindowCommon, about_html, fit_to_screen  # noqa: F401 (about_html re-exported)

DEFAULT_SIZE = (860, 930)       # everything visible; smaller screens scroll
# Below the size at which everything fits, the page scrolls instead of being cut.
MINIMUM_SIZE = (700, 480)       # wide enough for the page beside its scroll bar


class MainWindow(WindowCommon, ValuesIO, QtWidgets.QMainWindow):
    def __init__(self, settings: QtCore.QSettings, calibration_glob: str, driver: Driver,
                 emulator_state_path: str | None = None,
                 parent: QtWidgets.QWidget | None = None,
                 uhid_path: str = UHID_PATH, saver: Saver | None = None,
                 setup_probe: driver_setup.Probe | None = None,
                 check_update: Callable[[], update_check.Result] | None = None,
                 environ: Mapping[str, str] | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.init_update_notice(check_update, os.environ if environ is None else environ,
                                platforms.LINUX)
        self._calibration_glob = calibration_glob
        self._driver = driver
        # A result counts as new when it is younger than this: the moment the
        # virtual mouse was started or the driver was reset to 1.0, whichever
        # came last. None until one of the two happened in this session.
        self._baseline: float | None = None
        self.info_box: QtWidgets.QMessageBox | None = None
        self.setup_dialog: DriverSetupDialog | None = None
        # Replaceable, so that tests describe another machine than this one.
        self.make_setup_dialog: Callable[[], DriverSetupDialog] = (
            lambda: DriverSetupDialog(self, setup_probe))
        # Replaceable, so that tests do not start a file manager.
        self.open_url: Callable[[QtCore.QUrl], object] = QtGui.QDesktopServices.openUrl
        self.setWindowTitle(about.APP_NAME)
        self.setWindowIcon(app_icon())
        # The look is application-wide; the window only owns the switch for it.
        self.theme = ThemeManager.instance()
        self.theme.set_mode(stored_mode(settings))

        def make_session(pid: int, on_log: Callable[[LogEntry], None]) -> EmulatorSession:
            return EmulatorSession(product_id=pid, state_path=emulator_state_path,
                                   on_log=on_log, uhid_path=uhid_path)

        self.driver_strip = DriverCheckStrip(setup_probe)
        self.steps_bar = StepsBar()
        self.mouse_panel = MousePanel(make_session, lambda: uhid_access(uhid_path), settings)
        self.calibration_panel = CalibrationPanel(calibration_glob, settings)
        self.driver_panel = DriverPanel(driver, settings, self.calibration_panel.result,
                                        saver)
        self.calibration_panel.set_flip_y(self.driver_panel.flip_y())

        self._lower = QtWidgets.QHBoxLayout()
        self._lower.setSpacing(SPACE_M)
        self._lower.addWidget(self.calibration_panel, 1)
        self._lower.addWidget(self.driver_panel, 1)

        central = QtWidgets.QWidget()
        self._layout = layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(SPACE_L, SPACE_M - 2, SPACE_L, SPACE_M - 2)
        layout.setSpacing(SPACE_M - 2)
        layout.addWidget(self.update_bar)
        layout.addWidget(self.driver_strip)
        layout.addWidget(self.steps_bar)
        layout.addWidget(self.mouse_panel)
        layout.addLayout(self._lower)
        # Full width below the two cards, so that the curve preview gets real width.
        layout.addWidget(self.driver_panel.accel_card)
        # The page scrolls when the window is smaller than what it needs
        # (minimumSizeHint() names that size).
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.scroll.setWidget(central)
        self.setCentralWidget(self.scroll)
        self.setMinimumSize(*MINIMUM_SIZE)

        self._build_menus()

        self.mouse_panel.writes_changed.connect(
            lambda: self.calibration_panel.set_emulator_writes(self.mouse_panel.writes))
        self.mouse_panel.running_changed.connect(self._mouse_running_changed)
        self.mouse_panel.log_toggle.toggled.connect(self._share_space)
        self.calibration_panel.calibration_changed.connect(self.update_steps)
        self.driver_panel.changed.connect(self.update_steps)
        self.driver_panel.changed.connect(self._mirror_active)
        self._mirror_active()
        self.driver_panel.applied.connect(self._driver_applied)
        self.driver_panel.flip_changed.connect(self.calibration_panel.set_flip_y)
        self.steps_bar.action_requested.connect(self.run_step)
        self.driver_strip.action_requested.connect(self.show_driver_setup)
        self.driver_strip.checked.connect(self._driver_checked)
        self.driver_panel.driver_read.connect(self.check_driver)
        self.steps_bar.fix_requested.connect(
            lambda _fix: self.show_driver_setup(driver_setup.ACT_FIX))

        # Keyboard order follows the workflow: next step, mouse, result, driver.
        order: list[QtWidgets.QWidget] = [
            self.update_bar.open_button, self.update_bar.skip_button,
            self.driver_strip.action_button, self.driver_strip.details_button,
            self.driver_strip.recheck_button,
            self.steps_bar.next_button,
            self.mouse_panel.toggle_button, self.mouse_panel.pid_combo,
            self.mouse_panel.log_toggle, self.mouse_panel.log_view,
            self.mouse_panel.copy_button, self.mouse_panel.clear_button,
            self.calibration_panel.details_toggle,
            self.calibration_panel.path_edit, self.calibration_panel.refresh_button,
            self.driver_panel.advanced_toggle,
            *self.driver_panel.spins,
            self.driver_panel.use_button, self.driver_panel.reset_button,
            self.driver_panel.read_button, self.driver_panel.apply_button,
            self.driver_panel.save_button,
            self.driver_panel.flip_check, self.driver_panel.auto_check,
            # After the direction values and their Apply: the acceleration card.
            *self.driver_panel.accel_editor.tab_order(),
        ]
        for first, second in zip(order, order[1:]):
            QtWidgets.QWidget.setTabOrder(first, second)
        self.tab_order = order

        self.mouse_panel.set_product_id(
            int(settings.value("emulator/pid", DEFAULT_PRODUCT_ID, type=int)))
        self._share_space(self.mouse_panel.log_shown())
        self.update_steps()
        self.check_driver()
        self.check_for_update()
        fit_to_screen(self, *DEFAULT_SIZE)
        geometry = settings.value("window/geometry")
        if isinstance(geometry, QtCore.QByteArray) and not geometry.isEmpty():
            self.restoreGeometry(geometry)

    def _share_space(self, log_shown: bool) -> None:
        """Spare height goes to the log while it is shown, else to the lower half."""
        self._layout.setStretchFactor(self.mouse_panel, 1 if log_shown else 0)
        self._layout.setStretchFactor(self._lower, 0 if log_shown else 1)

    # ---- menus ------------------------------------------------------------

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
        self.add_accel_action(self.driver_panel.open_accel_editor)

        help_menu = self.menuBar().addMenu("&Help")
        self.steps_action = QtGui.QAction("&First steps", self)
        self.steps_action.setShortcut(
            QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.HelpContents))
        self.steps_action.triggered.connect(self.show_first_steps)
        help_menu.addAction(self.steps_action)
        self.setup_action = QtGui.QAction("&Setup help", self)
        self.setup_action.triggered.connect(self.show_setup_help)
        help_menu.addAction(self.setup_action)
        self.driver_setup_action = QtGui.QAction("&Driver setup…", self)
        self.driver_setup_action.setToolTip(
            "See which yeetmouse driver is installed, and install or revert the fork")
        self.driver_setup_action.triggered.connect(lambda: self.show_driver_setup())
        help_menu.addAction(self.driver_setup_action)
        help_menu.addSeparator()
        self.add_update_action(help_menu)
        self.add_online_switch(help_menu, UPDATE_NOTE)
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
            html.escape(PERSIST) + "</p><p>" + html.escape(ACCEL_HINT)))

    def show_about(self) -> None:
        self._open_box("About %s" % about.APP_NAME,
                       about_html() + "<p>%s</p>" % html.escape(UPDATE_NOTE))

    def setup_problems(self) -> list[str]:
        """Everything that is not set up yet, each with what to do about it."""
        panel = self.driver_panel
        return setup_problems(self.mouse_panel.check_access(), panel.state,
                              self._driver.helper_installed(), panel.tool_found())

    def show_setup_help(self) -> None:
        problems = self.setup_problems()
        if problems:
            body = "<p>These parts are not ready:</p><ul>%s</ul>" % "".join(
                "<li>%s</li>" % html.escape(text) for text in problems)
        else:
            body = ("<p>Nothing to fix: the virtual mouse can be created, the driver "
                    "values are readable and the save tool is installed.</p>")
        self._open_box("Setup help", "<h3>Setup help</h3>%s<p>Help &gt; Driver setup "
                       "shows which driver is installed and can install the fork. The "
                       "README section \"One-time setup\" has the details.</p>" % body)

    def check_driver(self) -> None:
        """Run the driver check: on start, after reading the driver, after the setup."""
        self.driver_strip.set_readable(self.driver_panel.readable())
        self.driver_strip.check()

    def _driver_checked(self) -> None:
        self.steps_bar.set_waiting_for_driver(self.driver_strip.blocks_steps())

    def show_driver_setup(self, focus: str = "") -> None:
        """Open the driver setup; it never starts anything by itself."""
        if self.setup_dialog is None:
            dialog = self.make_setup_dialog()
            dialog.changed.connect(self._driver_setup_changed)
            dialog.finished.connect(self._driver_setup_closed)
            self.setup_dialog = dialog
        self.setup_dialog.refresh()
        self.setup_dialog.show()
        self.setup_dialog.raise_()
        self.setup_dialog.activateWindow()
        self.setup_dialog.focus_on(focus)

    def _driver_setup_changed(self) -> None:
        """An install or revert ran: the driver panel may see another module now."""
        self.driver_panel.refresh()
        self.update_steps()

    def _driver_setup_closed(self, _result: int = 0) -> None:
        dialog, self.setup_dialog = self.setup_dialog, None
        if dialog is not None:
            dialog.deleteLater()
        self._driver_setup_changed()

    def open_result_folder(self) -> None:
        """Open the folder of the newest result file in the file manager."""
        folder = result_folder(self._calibration_glob)
        if folder is None:
            self.calibration_panel.message.show_message(NO_RESULT_FOLDER)
            return
        self.open_url(QtCore.QUrl.fromLocalFile(str(folder)))

    # ---- guided steps -----------------------------------------------------

    def _mouse_running_changed(self, running: bool) -> None:
        if running:
            self._baseline = time.time()
        self.update_steps()

    def _driver_applied(self, scalars: Scalars) -> None:
        if is_unity(scalars):
            # Back to the raw mouse: only a calibration from now on is a new one.
            self._baseline = time.time()
        self.update_steps()

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802 (Qt name)
        """The size from which nothing scrolls: what the page needs, plus the menu bar.

        The window itself may be made smaller (see MINIMUM_SIZE); the page
        then scrolls.
        """
        content = self.scroll.widget().minimumSizeHint()
        return QtCore.QSize(content.width(),
                            content.height() + self.menuBar().sizeHint().height())

    def _mirror_active(self) -> None:
        shown = self.driver_panel.active
        self.driver_strip.set_active(shown.short(), shown.detail)

    def flow_state(self) -> FlowState:
        """The facts the step display is derived from."""
        mouse, panel = self.mouse_panel, self.driver_panel
        result = self.calibration_panel.result()
        wanted = panel.result_values()
        driver = panel.driver_values()
        return FlowState(
            mouse_running=mouse.is_running(),
            mouse_problem=uhid_short(mouse.access()),
            driver=driver,
            driver_problem=panel.write_problem(),
            driver_missing=panel.fork_missing(),
            result_available=wanted is not None,
            result_is_new=result is not None and is_newer(result.time, self._baseline),
            result_in_editor=wanted is not None and panel.values().is_close(
                wanted, SAME_VALUE),
            result_in_driver=(wanted is not None and driver is not None
                              and driver.is_close(wanted, SAME_VALUE)),
            save_state=panel.save_state,
            save_problem="" if panel.tool_found() else tool_missing(),
        )

    def update_steps(self) -> list[StepInfo]:
        infos = derive(self.flow_state())
        self.steps_bar.set_steps(infos)
        if self.driver_panel.is_busy():
            self.steps_bar.next_button.setEnabled(False)
        # The card button that does the current step is the primary one there.
        primary = self.step_button(infos)
        for button in self._step_buttons():
            set_primary(button, button is primary)
        return infos

    def _step_buttons(self) -> list[QtWidgets.QPushButton]:
        panel = self.driver_panel
        return [self.mouse_panel.toggle_button, panel.use_button, panel.reset_button,
                panel.apply_button, panel.save_button]

    def step_button(self, infos: list[StepInfo]) -> QtWidgets.QPushButton | None:
        """The button inside a card that carries out the current step, if any."""
        current = next((info for info in infos if info.status is Status.CURRENT), None)
        if current is None:
            return None
        panel = self.driver_panel
        if current.step is Step.START:
            return self.mouse_panel.toggle_button
        if current.step is Step.RESET:
            # Two presses in the card: first reset the new values, then apply them.
            return panel.apply_button if is_unity(panel.values()) else panel.reset_button
        if current.step is Step.USE:
            return panel.use_button
        if current.step is Step.APPLY:
            return panel.apply_button
        if current.step is Step.SAVE:
            return panel.save_button
        return None

    def run_step(self, step: Step) -> None:
        """Carry out the action of a step, as the button in its panel would."""
        if step is Step.START:
            self.mouse_panel.start()
        elif step is Step.RESET:
            self.driver_panel.reset_and_apply()
        elif step is Step.USE:
            self.driver_panel.use_calibration()
        elif step is Step.APPLY:
            self.driver_panel.apply()
        elif step is Step.SAVE:
            self.driver_panel.save_config()
        self.update_steps()

    # ---- life cycle -------------------------------------------------------

    def apply_saved_if_enabled(self) -> None:
        if self.driver_panel.auto_check.isChecked():
            self.driver_panel.apply_saved()

    def changeEvent(self, event: QtCore.QEvent) -> None:  # noqa: N802 (Qt name)
        super().changeEvent(event)
        # Permissions may have been fixed while the window was in the background.
        if (event.type() == QtCore.QEvent.Type.ActivationChange and self.isActiveWindow()
                and hasattr(self, "mouse_panel") and not self.mouse_panel.is_running()):
            self.mouse_panel.check_access()
            self.update_steps()

    def shutdown(self) -> None:
        """Destroy the virtual device and store the settings. Safe to call twice."""
        self.mouse_panel.stop()
        self.calibration_panel.stop_watching()
        self._settings.setValue("emulator/pid", self.mouse_panel.product_id())
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.sync()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 (Qt name)
        if self.setup_dialog is not None and self.setup_dialog.is_busy():
            # Quitting now would cut off a half-done driver install.
            self.setup_dialog.raise_()
            self.setup_dialog.close()   # refuses and says why
            event.ignore()
            return
        self.shutdown()
        super().closeEvent(event)
