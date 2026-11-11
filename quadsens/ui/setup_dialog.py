"""Driver setup: shows what is installed and installs or reverts the yeetmouse fork."""

from __future__ import annotations

from collections.abc import Callable

from .. import driver_setup as ds
from .qt import QtCore, QtGui, QtWidgets, Signal
from .status import SPACE_L, SPACE_M, SPACE_S, MessageLabel, WrapLabel, caption, heading
from .theme import set_primary

TITLES = {ds.INSTALL: "Install forked driver", ds.REVERT: "Revert to previous driver"}
MARKS = {True: "✓", False: "✕", None: "ℹ"}


def _fixed_font() -> QtGui.QFont:
    font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
    font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    return font


class DriverSetupDialog(QtWidgets.QDialog):
    """Status, prerequisites, the script, and the two actions.

    Nothing runs by itself. An action is started with its button, has to be
    confirmed, and then runs the script that the "Script" tab shows: its text
    is written to the standard input of ``pkexec /bin/sh -s``, so what is shown
    is what runs. With "dry run" ticked the same script only looks and prints,
    without root rights and without a prompt.
    """

    # The script finished (or was refused): the driver may be a different one now.
    changed = Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None,
                 probe: ds.Probe | None = None,
                 launcher: Callable[..., ds.Launch] = ds.launch,
                 confirm: Callable[[str, str], bool] | None = None,
                 environment: dict[str, str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Driver setup")
        self._probe = probe or ds.Probe()
        self._launcher = launcher
        self._confirm = confirm or self._ask
        self._environment = environment
        self._process: QtCore.QProcess | None = None
        self._action = ""
        self._dry = False
        self.state: ds.SetupState | None = None
        self.checks: list[ds.Check] = []
        try:
            self._text = ds.script_text()
            self._pin: ds.Pin | None = ds.pin(self._text)
            self._script_error = ""
        except ds.SetupError as exc:
            self._text, self._pin, self._script_error = "", None, str(exc)

        self.source_label = caption(WrapLabel("Pinned source"))
        if self._pin is not None:
            self.source_label.setText(
                f"Source: {self._pin.url}, commit {self._pin.commit} "
                f"(driver version {self._pin.version})")

        # ---- overview ---------------------------------------------------------
        self.state_label = WrapLabel("Installed now")
        self.checks_label = WrapLabel("Needed for the install")
        self.install_changes = WrapLabel("What Install changes")
        self.revert_changes = WrapLabel("What Revert changes")
        overview = QtWidgets.QWidget()
        overview_layout = QtWidgets.QVBoxLayout(overview)
        overview_layout.setContentsMargins(SPACE_M, SPACE_M, SPACE_M, SPACE_M)
        overview_layout.setSpacing(SPACE_S)
        for title, label in (("Installed now", self.state_label),
                             ("Needed for the install", self.checks_label),
                             ("What \"Install forked driver\" changes", self.install_changes),
                             ("What \"Revert to previous driver\" changes",
                              self.revert_changes)):
            overview_layout.addWidget(heading(title))
            overview_layout.addWidget(label)
            overview_layout.addSpacing(SPACE_M)
        overview_layout.addStretch(1)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(overview)
        scroll.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        scroll.setAccessibleName("Overview: state, prerequisites and changes")
        self.overview = scroll

        # ---- script -------------------------------------------------------------
        self.how_label = caption(WrapLabel("How the script is run"))
        self.script_view = QtWidgets.QPlainTextEdit()
        self.script_view.setReadOnly(True)
        self.script_view.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.script_view.setFont(_fixed_font())
        self.script_view.setPlainText(self._text)
        self.script_view.setTabChangesFocus(True)
        self.script_view.setAccessibleName("The installer script")
        self.script_view.setAccessibleDescription(
            "The complete script that Install and Revert run, read only.")
        script_page = QtWidgets.QWidget()
        script_layout = QtWidgets.QVBoxLayout(script_page)
        script_layout.setContentsMargins(SPACE_M, SPACE_M, SPACE_M, SPACE_M)
        script_layout.setSpacing(SPACE_S)
        script_layout.addWidget(self.how_label)
        script_layout.addWidget(self.script_view, 1)

        # ---- output ---------------------------------------------------------------
        self.output_view = QtWidgets.QPlainTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setFont(_fixed_font())
        self.output_view.setTabChangesFocus(True)
        self.output_view.setPlaceholderText(
            "The numbered steps of the script appear here while it runs.")
        self.output_view.setAccessibleName("Output of the script")
        self.output_view.setAccessibleDescription(
            "What the script printed, step by step, read only.")
        output_page = QtWidgets.QWidget()
        output_layout = QtWidgets.QVBoxLayout(output_page)
        output_layout.setContentsMargins(SPACE_M, SPACE_M, SPACE_M, SPACE_M)
        output_layout.addWidget(self.output_view, 1)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self.overview, "O&verview")
        self.tabs.addTab(script_page, "&Script")
        self.tabs.addTab(output_page, "Out&put")
        self.tabs.setAccessibleName("Driver setup pages")

        self.result = MessageLabel("Result of the last action")
        self.result.hide()   # takes no room until there is something to say

        # ---- actions ----------------------------------------------------------------
        self.dry_check = QtWidgets.QCheckBox("&Dry run: only show what would be done")
        self.dry_check.setToolTip(
            "Run the script in its dry-run mode: it looks at the system and prints every "
            "action without changing anything. Needs no root rights.")
        self.dry_check.toggled.connect(lambda *_: self._update_buttons())
        self.install_button = QtWidgets.QPushButton("&Install forked driver")
        self.install_button.clicked.connect(lambda: self.run(ds.INSTALL))
        self.revert_button = QtWidgets.QPushButton("&Revert to previous driver")
        self.revert_button.clicked.connect(lambda: self.run(ds.REVERT))
        self.recheck_button = QtWidgets.QPushButton("Check &again")
        self.recheck_button.setToolTip("Look at the system again")
        self.recheck_button.clicked.connect(self.refresh)
        self.close_button = QtWidgets.QPushButton("&Close")
        self.close_button.clicked.connect(self.close)
        for control in (self.dry_check, self.recheck_button):
            control.setAccessibleDescription(control.toolTip())
        self.close_button.setAccessibleDescription("Close the driver setup")

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(SPACE_M)
        buttons.addWidget(self.install_button)
        buttons.addWidget(self.revert_button)
        buttons.addStretch(1)
        buttons.addWidget(self.recheck_button)
        buttons.addWidget(self.close_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        layout.setSpacing(SPACE_M)
        layout.addWidget(heading("Driver setup"))
        layout.addWidget(self.source_label)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.result)
        layout.addWidget(self.dry_check)
        layout.addLayout(buttons)

        order = [self.tabs, self.dry_check, self.install_button, self.revert_button,
                 self.recheck_button, self.close_button]
        for first, second in zip(order, order[1:]):
            QtWidgets.QWidget.setTabOrder(first, second)
        self.resize(720, 620)
        self.refresh()

    def _say(self, text: str, error: bool = False, kind: str = "") -> None:
        self.result.show_message(text, error, kind)
        self.result.setVisible(bool(text))

    def focus_on(self, what: str) -> None:
        """Bring the part the user came for to the front. Starts nothing."""
        self.tabs.setCurrentIndex(0)
        if what == ds.ACT_FIX and self.install_button.isEnabled():
            self.install_button.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        else:
            self.overview.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    # ---- state ----------------------------------------------------------------------

    def is_busy(self) -> bool:
        return self._process is not None

    def refresh(self) -> None:
        """Look at the system again and show what was found."""
        if self._pin is None:
            self._say(self._script_error, error=True)
            self.state_label.setText("The installer script is not available.")
            self._update_buttons()
            return
        self.state = state = ds.detect(self._probe, self._pin)
        self.checks = ds.prerequisites(self._probe)
        self.state_label.setText("\n".join(
            f"{MARKS[ok]} {text}" for ok, text in state.lines(self._pin)))
        self.checks_label.setText("\n".join(check.text() for check in self.checks))
        self.install_changes.setText("\n".join(
            "• " + line for line in ds.changes(ds.INSTALL, state, self._pin)))
        self.revert_changes.setText("\n".join(
            "• " + line for line in ds.changes(ds.REVERT, state, self._pin)))
        try:
            self.how_label.setText(self._launcher(ds.INSTALL, text=self._text).how)
        except ds.SetupError as exc:
            self.how_label.setText(f"Cannot be started: {exc}.")
        self._update_buttons()

    def blockers(self, action: str) -> str:
        """Why an action cannot be started for real, "" if it can."""
        if self._pin is None or self.state is None:
            return self._script_error or "The installer script is not available."
        if action == ds.INSTALL:
            if self.state.kind is ds.Kind.STOCK_PACKAGED:
                return (f"The installed driver belongs to the package "
                        f"\"{self.state.package}\"; remove that package first.")
            missing = [check.name for check in self.checks if check.required and not check.ok]
            if missing:
                return "Missing: " + ", ".join(missing) + ". See the Overview page."
            return ""
        if not self.state.can_revert:
            return "There is nothing to revert: the fork is not installed."
        missing = [check.name for check in self.checks
                   if check.name in ("DKMS", "pkexec (polkit)") and not check.ok]
        return ("Missing: " + ", ".join(missing) + ".") if missing else ""

    def _update_buttons(self) -> None:
        dry = self.dry_check.isChecked()
        for action, button in ((ds.INSTALL, self.install_button),
                               (ds.REVERT, self.revert_button)):
            blocker = "" if dry and self._pin is not None else self.blockers(action)
            button.setEnabled(not self.is_busy() and not blocker)
            if blocker:
                hint = "Not available: " + blocker
            elif dry:
                hint = f"Show what \"{TITLES[action]}\" would do, without changing anything."
            elif action == ds.INSTALL:
                hint = ("Download the pinned source, build the kernel module as root and "
                        "install it. Asks for confirmation and for your password.")
            else:
                hint = ("Remove the fork and put the previous driver back, as root. Asks "
                        "for confirmation and for your password.")
            button.setToolTip(hint)
            button.setAccessibleDescription(hint)
        fork = self.state is not None and self.state.kind is ds.Kind.FORK
        set_primary(self.install_button, not fork and self.install_button.isEnabled())
        self.dry_check.setEnabled(not self.is_busy())
        self.recheck_button.setEnabled(not self.is_busy())

    # ---- running ----------------------------------------------------------------------

    def _ask(self, action: str, text: str) -> bool:
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(TITLES[action])
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setText(f"{TITLES[action]} now?")
        box.setInformativeText(text)
        go = box.addButton(TITLES[action], QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is go

    def confirmation_text(self, action: str) -> str:
        if action == ds.INSTALL:
            return ("This downloads the source of the yeetmouse fork at the pinned commit "
                    "and builds a kernel module as root. Mouse acceleration is off for a "
                    "moment while the module is reloaded. You will be asked for your "
                    "password. \"Revert to previous driver\" undoes it.")
        return ("This removes the fork's kernel module as root and puts the previous "
                "driver back, if one is kept. Mouse acceleration is off for a moment while "
                "the module is reloaded. You will be asked for your password.")

    def run(self, action: str) -> None:
        """Start an action: confirm (unless it is a dry run), then feed the script."""
        if self.is_busy() or self._pin is None:
            return
        dry = self.dry_check.isChecked()
        if not dry:
            blocker = self.blockers(action)
            if blocker:
                self._say(blocker, error=True)
                return
            if not self._confirm(action, self.confirmation_text(action)):
                self._say(f"{TITLES[action]}: cancelled, nothing was changed.")
                return
        try:
            launch = self._launcher(action, dry_run=dry, text=self._text)
        except ds.SetupError as exc:
            self._say(f"Not started: {exc}.", error=True)
            return
        self._action, self._dry = action, dry
        self.output_view.clear()
        self.output_view.appendPlainText("$ " + " ".join(launch.argv))
        self.tabs.setCurrentIndex(2)
        self._say(
            f"{TITLES[action]}: " + ("dry run ..." if dry else "running. Waiting for "
                                     "authorization, then the steps appear above ..."))
        process = QtCore.QProcess(self)
        process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        if self._environment is not None:
            env = QtCore.QProcessEnvironment()
            for key, value in self._environment.items():
                env.insert(key, value)
            process.setProcessEnvironment(env)
        process.readyRead.connect(self._read_output)
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._start_failed)
        self._process = process
        self._update_buttons()
        process.start(launch.argv[0], launch.argv[1:])
        if launch.stdin:
            # The script goes in through standard input; closing the channel
            # tells the shell that this is all of it.
            process.write(launch.stdin.encode("utf-8"))
        process.closeWriteChannel()

    def _read_output(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAll().data()).decode("utf-8", "replace")
        if text:
            self.output_view.moveCursor(QtGui.QTextCursor.MoveOperation.End)
            self.output_view.insertPlainText(text)
            self.output_view.moveCursor(QtGui.QTextCursor.MoveOperation.End)

    def _start_failed(self, error: QtCore.QProcess.ProcessError) -> None:
        if error != QtCore.QProcess.ProcessError.FailedToStart or self._process is None:
            return
        self._release()
        self._say("Not started: the command could not be run. Nothing was "
                                 "changed.", error=True)
        self._update_buttons()

    def _release(self) -> None:
        if self._process is not None:
            self._process.deleteLater()
        self._process = None

    def _finished(self, exit_code: int, _status: QtCore.QProcess.ExitStatus) -> None:
        if self._process is None:
            return
        self._read_output()
        self._release()
        title = TITLES[self._action]
        output = self.output_view.toPlainText()
        state_lines = [line[len("State: "):] for line in output.splitlines()
                       if line.startswith("State: ")]
        if exit_code == 0 and self._dry:
            self._say(f"{title}: dry run finished. Nothing was changed; the "
                                     "Output page lists what would be done.")
        elif exit_code == 0:
            tail = (" Log out and in again, so that your session is in group "
                    f"{ds.GROUP}." if self._action == ds.INSTALL else "")
            self._say(f"✓ {title}: finished.{tail}")
        elif not self._dry and exit_code in (126, 127) and "[1/" not in output:
            self._say(
                f"{title}: not authorized or dismissed. Nothing was changed.", error=True)
        else:
            state = state_lines[-1] if state_lines else "see the Output page."
            undo = (" To go back, use \"Revert to previous driver\"."
                    if self._action == ds.INSTALL and not self._dry else "")
            self._say(
                f"{title} failed (exit status {exit_code}). State: {state}{undo}", error=True)
        self.refresh()
        self.changed.emit()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 (Qt name)
        if self.is_busy():
            # Closing must not interrupt a half-done install.
            self._say("The script is still running; wait for it to finish.",
                                     kind="warning")
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if not self.is_busy():
            super().reject()
