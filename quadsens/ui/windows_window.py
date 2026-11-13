"""Main window on Windows: virtual mouse over USB/IP, values in, RawAccel out.

The virtual mouse is served over USB/IP and attached through usbip-win2.
The first lines of the window say whether usbip-win2 and RawAccel are there
and can install the tested versions, always after asking. Without usbip-win2 the window still does everything else:
the multipliers can be imported or typed and applied to RawAccel. Nothing of
the Linux chain (/dev/uhid, yeetmouse, pkexec, the driver installer) is
imported here. The calibration card, the diagram, the stepper and the themes
are shared with Linux.
"""

from __future__ import annotations

import html
import os
import queue
import threading
import time
from collections.abc import Callable, Mapping

from .. import (
    about,
    platforms,
    rawaccel_live,
    update_check,
    usbip_client,
    win_autostart,
    win_download,
    win_install,
    win_tools,
)
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
from .install_dialog import ToolInstallDialog
from .tools_strip import RawAccelInfo, ToolsStrip
from .usbip_panel import UsbipMousePanel
from .values_io import ValuesIO
from .window_common import WindowCommon, about_html, fit_to_screen

DEFAULT_SIZE = (900, 1080)      # everything visible; smaller screens scroll
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
ONLINE_NOTE = (
    "When it starts, QuadSens asks api.github.com for the newest release of QuadSens, "
    "and, then and on Check again, for the newest release of usbip-win2 and of "
    "RawAccel, to tell you when there is a newer QuadSens or a tool newer than the "
    "version it was tested with. Nothing else is sent and nothing is downloaded by "
    "that. Help > Check for newer versions online switches it off. Files are "
    "downloaded from github.com only when you start an installation.")
ACCEL_HINT = ("The acceleration curve of RawAccel is edited in the Acceleration card at "
              "the bottom of the window (View > Acceleration… jumps there). Set it before "
              "you calibrate.")
WITHOUT_MOUSE = ("Without usbip-win2: import a values file exported by QuadSens on Linux "
                 "(File > Import values) or type the four multipliers, then Apply.")


class WindowsMainWindow(WindowCommon, ValuesIO, QtWidgets.QMainWindow):
    def __init__(self, settings: QtCore.QSettings, calibration_glob: str,
                 environ: Mapping[str, str] | None = None,
                 parent: QtWidgets.QWidget | None = None,
                 find_client: Callable[[], usbip_client.UsbipClient] | None = None,
                 mouse_factory: Callable[[int], VirtualMouse] | None = None,
                 install_tools: win_install.Tools | None = None,
                 driver_state: Callable[[float | None], str] | None = None,
                 latest_release: Callable[[win_tools.Pin], win_download.Latest | None]
                 | None = win_download.latest_release,
                 read_live: Callable[[], rawaccel_live.Live] = rawaccel_live.read_driver,
                 registry: win_autostart.Registry | None = None,
                 started: Callable[[], float | None] = win_install.boot_time,
                 check_update: Callable[[], update_check.Result] | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.init_update_notice(check_update, os.environ if environ is None else environ,
                                platforms.WINDOWS)
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

        # Installing the tools: everything that touches Windows or the network
        # can be replaced, which the tests do.
        self._install_tools = install_tools
        self._driver_state = driver_state or (
            lambda installed_at: win_install.rawaccel_driver_state(
                self._environ, win_install.boot_time(), installed_at))
        self._latest_release = latest_release
        self.install_dialog: ToolInstallDialog | None = None
        self._online_thread: threading.Thread | None = None
        self._online_results: queue.Queue[tuple[str, win_download.Latest | None]] = \
            queue.Queue()

        self.steps_bar = StepsBar()
        self.steps_bar.all_done_text = WIN_ALL_DONE
        self.mouse_panel = UsbipMousePanel(mouse_factory or make_mouse, settings)
        self.calibration_panel = CalibrationPanel(calibration_glob, settings,
                                                  no_result_text=NO_RESULT_WINDOWS)
        self.rawaccel_panel = RawAccelPanel(
            settings, self.calibration_panel.result, environ, read_live=read_live,
            driver_state=lambda: self._driver_state(self._installed_at()),
            started=started, registry=registry)
        self.tools_strip = ToolsStrip(self._find_client, self.rawaccel_info)
        self.usbip_strip = self.tools_strip          # its first line is the USB/IP driver
        self.calibration_panel.set_flip_y(self.rawaccel_panel.flip())

        lower = QtWidgets.QHBoxLayout()
        lower.setSpacing(SPACE_M)
        lower.addWidget(self.calibration_panel, 1)
        lower.addWidget(self.rawaccel_panel, 1)
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(SPACE_L, SPACE_M - 2, SPACE_L, SPACE_M - 2)
        layout.setSpacing(SPACE_M - 2)
        layout.addWidget(self.update_bar)
        layout.addWidget(self.usbip_strip)
        layout.addWidget(self.steps_bar)
        layout.addWidget(self.mouse_panel)
        layout.addLayout(lower, 1)
        # Full width below the two cards, so that the curve preview gets real width.
        layout.addWidget(self.rawaccel_panel.accel_card)
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
        self.tools_strip.install_requested.connect(self.install_tool)
        self.tools_strip.choose_folder_requested.connect(
            lambda: self.rawaccel_panel.choose_folder())
        self.tools_strip.link_requested.connect(lambda url: self.open_url(QtCore.QUrl(url)))
        self.tools_strip.checked.connect(self.update_steps)
        self.tools_strip.recheck_button.clicked.connect(self.check_online)
        self.rawaccel_panel.changed.connect(self._rawaccel_changed)
        self._rawaccel_changed()
        self._online_timer = QtCore.QTimer(self)
        self._online_timer.setInterval(200)
        self._online_timer.timeout.connect(self._take_online_results)
        self.mouse_panel.writes_changed.connect(self._writes_changed)
        self.mouse_panel.running_changed.connect(self._mouse_changed)

        panel = self.rawaccel_panel
        order: list[QtWidgets.QWidget] = [
            self.update_bar.open_button, self.update_bar.skip_button,
            *self.tools_strip.buttons(),
            self.steps_bar.next_button,
            self.mouse_panel.toggle_button, self.mouse_panel.pid_combo,
            self.mouse_panel.log_toggle, self.mouse_panel.log_view,
            self.mouse_panel.copy_button, self.mouse_panel.clear_button,
            self.calibration_panel.details_toggle, self.calibration_panel.path_edit,
            self.calibration_panel.refresh_button,
            panel.advanced_toggle, panel.folder_edit, panel.choose_button,
            panel.profile_combo, *panel.spins, panel.use_button, panel.reset_button,
            panel.apply_button, panel.restore_button, panel.autostart_check,
            panel.flip_check,
            # After the direction values and their Apply: the acceleration card.
            *panel.accel_editor.tab_order(),
        ]
        for first, second in zip(order, order[1:]):
            QtWidgets.QWidget.setTabOrder(first, second)
        self.tab_order = order

        self.update_steps()
        self.check_online()
        self.check_for_update()
        self.steps_bar.next_button.setFocus()
        fit_to_screen(self, *DEFAULT_SIZE)
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
        self.add_accel_action(self.rawaccel_panel.open_accel_editor)

        help_menu = self.menuBar().addMenu("&Help")
        self.steps_action = QtGui.QAction("&First steps", self)
        self.steps_action.setShortcut(
            QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.HelpContents))
        self.steps_action.triggered.connect(self.show_first_steps)
        help_menu.addAction(self.steps_action)
        self.setup_help_action = QtGui.QAction("&Setup help", self)
        self.setup_help_action.triggered.connect(self.show_setup_help)
        help_menu.addAction(self.setup_help_action)
        help_menu.addSeparator()
        self.add_update_action(help_menu)
        self.add_online_switch(help_menu, ONLINE_NOTE)
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
            html.escape(WITHOUT_MOUSE) + "</p><p>" + html.escape(ACCEL_HINT)))

    def show_setup_help(self) -> None:
        usbip, raw = win_tools.PINS[win_tools.USBIP], win_tools.PINS[win_tools.RAWACCEL]
        self._open_box("Setup help", (
            "<h3>The two tools</h3>"
            "<p>The virtual mouse needs <b>usbip-win2</b> (tested: %s); applying needs "
            "<b>RawAccel</b> (tested: %s). The first lines of the window say what was "
            "found and can install the tested versions: the file is downloaded from the "
            "project on GitHub, checked against a checksum built into QuadSens, and its "
            "installer is started through the Windows administrator prompt. RawAccel "
            "works only after a restart of the computer; QuadSens never restarts it.</p>"
            "<p>You can also install them yourself from %s and %s, then press "
            "<i>Check again</i> (for RawAccel: <i>Choose folder…</i>).</p>"
            "<h3>How to remove them</h3><ul>%s</ul>"
            "<h3>What goes online</h3><p>%s</p>") % (
                html.escape(usbip.version), html.escape(raw.version),
                html.escape(usbip.releases_url), html.escape(raw.releases_url),
                "".join("<li>%s</li>" % html.escape(line) for line in win_tools.HOW_TO_REMOVE),
                html.escape(ONLINE_NOTE)))

    def show_about(self) -> None:
        self._open_box("About %s" % about.APP_NAME,
                       about_html() + "<p>%s</p>" % html.escape(ONLINE_NOTE))

    # ---- the tools: what is there, installing, newer versions ---------------------

    def _installed_at(self) -> float | None:
        stored = self._settings.value("tools/rawaccel_installed_at")
        try:
            return float(stored) if stored is not None else None
        except (TypeError, ValueError):
            return None

    def rawaccel_info(self) -> RawAccelInfo:
        panel = self.rawaccel_panel
        folder = str(panel.folder) if panel.folder is not None else ""
        return RawAccelInfo(folder, win_install.rawaccel_version(folder) if folder else "",
                            self._driver_state(self._installed_at()))

    def _rawaccel_changed(self) -> None:
        panel = self.rawaccel_panel
        folder = str(panel.folder) if panel.folder is not None else ""
        if folder != self.tools_strip.rawaccel.folder:
            self.tools_strip.refresh_rawaccel()
        shown = panel.active
        self.tools_strip.set_active(shown.short(), shown.detail)

    def install_tool(self, key: str) -> None:
        """Open the install dialog for a tool. Nothing happens before its button."""
        folder = ""
        if key == win_tools.RAWACCEL:
            panel = self.rawaccel_panel
            folder = str(panel.folder) if panel.folder is not None \
                else win_install.default_rawaccel_folder(self._environ)
        dialog = ToolInstallDialog(key, self._run_install, folder, self)
        dialog.finished_install.connect(lambda result, k=key: self._installed(k, result))
        self.install_dialog = dialog
        dialog.open()

    def _run_install(self, key: str, folder: str, say: Callable[[str], None],
                     progress: Callable[[int, int], None],
                     cancelled: Callable[[], bool]) -> win_install.Result:
        """Runs in the dialog's thread."""
        tools = self._install_tools or win_install.Tools(
            cache=lambda: win_download.cache_dir(self._environ),
            signature=lambda path, signer: win_install.signature_state(
                path, signer, self._environ))
        if key == win_tools.USBIP:
            return win_install.install_usbip(
                tools, say, progress, cancelled, detect=lambda: self._find_client().version())
        return win_install.install_rawaccel(
            folder, tools, say, progress, cancelled,
            driver_state=lambda: self._driver_state(time.time()))

    def _installed(self, key: str, result: win_install.Result) -> None:
        if key == win_tools.RAWACCEL and result.folder:
            if result.kind == win_install.DONE:
                self._settings.setValue("tools/rawaccel_installed_at", time.time())
            self.rawaccel_panel.choose_folder(result.folder)
        self.tools_strip.check()

    def online_check_enabled(self) -> bool:
        # The tests change _environ after the window was made.
        self._update_environ = self._environ
        return super().online_check_enabled()

    def set_online_check(self, enabled: bool) -> None:
        super().set_online_check(enabled)
        if enabled:
            self.check_online()
        else:
            for key in win_tools.PINS:
                self.tools_strip.set_notice(key, "")

    def check_online(self) -> bool:
        """Ask, in the background, whether the projects have newer releases.

        Returns whether the question was sent. No answer, for whatever reason,
        changes nothing and is not reported.
        """
        if not self.online_check_enabled() or self._latest_release is None \
                or (self._online_thread is not None and self._online_thread.is_alive()):
            return False
        ask = self._latest_release

        def work() -> None:
            for key, pin in win_tools.PINS.items():
                try:
                    latest = ask(pin)
                except Exception:      # noqa: BLE001 - a notice is never worth an error
                    latest = None
                self._online_results.put((key, latest))
        self._online_thread = threading.Thread(target=work, name="tool-versions", daemon=True)
        self._online_thread.start()
        self._online_timer.start()
        return True

    def _take_online_results(self) -> None:
        while True:
            try:
                key, latest = self._online_results.get_nowait()
            except queue.Empty:
                break
            if latest is not None and self.online_check_enabled():
                self.tools_strip.set_notice(
                    key, win_tools.update_notice(win_tools.PINS[key], latest.tag), latest.url)
        if self._online_thread is None or not self._online_thread.is_alive():
            if self._online_results.empty():
                self._online_timer.stop()

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
            mouse_possible=self.tools_strip.found,
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
