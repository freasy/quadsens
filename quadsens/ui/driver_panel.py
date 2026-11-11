""""Driver (yeetmouse)" area: show, edit, apply and save the four DirScale values."""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable

from .. import accel, active_linux
from ..calibration import Result, driver_scalars
from ..driver import (
    PARAM_NAMES,
    Driver,
    DriverError,
    DriverState,
    DriverStatus,
    describe_helper_failure,
)
from ..hints import FLIP_Y, PERSIST, driver_status, save_state_text, tool_missing
from ..model import SCALE_MAX, SCALE_MIN, Scalars, format_deviation
from ..persist import PersistError, SaveCommand, Saver, SaveState, describe_save_failure
from .qt import QtCore, QtWidgets, Signal
from .scale_spin import ScaleSpinBox
from .status import (
    SPACE_M,
    SPACE_S,
    ActiveIndicator,
    Card,
    ElideLabel,
    Disclosure,
    MessageLabel,
    WrapLabel,
    caption,
    column_header,
    heading,
    start_program,
)

# Mouse direction each parameter scales; the driver's +Y is "down".
DIRECTIONS: tuple[str, ...] = ("right", "left", "down", "up")
ROW_TITLES: tuple[str, ...] = ("→ &Right:", "← &Left:", "↓ &Down:", "↑ &Up:")
NO_VALUE = "–"
def find_yeetmouse_gui() -> str | None:
    """The fork's settings program: on PATH, or where its Makefile installs it."""
    found = shutil.which(accel.YEETMOUSE_GUI)
    if found:
        return found
    fixed = "/usr/bin/" + accel.YEETMOUSE_GUI
    return fixed if os.path.isfile(fixed) and os.access(fixed, os.X_OK) else None


WATCH_MS = 3000          # how often the driver is read again while the card is visible
DECIMALS = 4
# Half a step of the last digit shown: closer than this is "the same value".
SAME_VALUE = 0.5 * 10 ** -DECIMALS + 1e-9
CHANGED_MARK = "≠"


class DriverPanel(Card):
    # Something the step display depends on changed: values, driver or save state.
    changed = Signal()
    # The driver was read again (start, Read again, after Apply and Save).
    driver_read = Signal()
    # A set of values reached the driver.
    applied = Signal(object)  # Scalars
    flip_changed = Signal(bool)

    def __init__(self, driver: Driver, settings: QtCore.QSettings,
                 get_result: Callable[[], Result | None],
                 saver: Saver | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Driver (yeetmouse)", parent)
        self._driver = driver
        self._settings = settings
        self._get_result = get_result
        self._saver = saver if saver is not None else Saver()
        self._process: QtCore.QProcess | None = None
        self._pending: Scalars | None = None
        self._save_command: SaveCommand | None = None
        # What was last written when the driver cannot be read back.
        self._assumed: Scalars | None = None
        self.state: DriverState = DriverState(DriverStatus.NOT_LOADED)
        self.save_state: SaveState = SaveState.UNKNOWN

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(SPACE_M)
        grid.setVerticalSpacing(2)
        for column, title in enumerate(("Direction", "Driver reports", "", "New value")):
            grid.addWidget(column_header(title), 0, column)
        self.current_labels: list[QtWidgets.QLabel] = []
        self.deviation_labels: list[QtWidgets.QLabel] = []
        self.spins: list[ScaleSpinBox] = []
        self.change_marks: list[QtWidgets.QLabel] = []
        for row, (title, name, direction) in enumerate(
                zip(ROW_TITLES, PARAM_NAMES, DIRECTIONS), start=1):
            purpose = f"{name}: multiplier for mouse movement to the {direction}" \
                if direction in ("right", "left") else \
                f"{name}: multiplier for mouse movement {direction}"
            spin = ScaleSpinBox()
            spin.setRange(SCALE_MIN, SCALE_MAX)
            spin.setDecimals(DECIMALS)
            spin.setSingleStep(0.001)
            spin.setValue(1.0)
            spin.setAccessibleName(f"New value for {name} ({direction})")
            spin.setAccessibleDescription(
                purpose + ". 1 leaves the movement unchanged. Dot or comma as "
                "decimal separator.")
            spin.setToolTip(purpose + " (1 = unchanged)")
            spin.valueChanged.connect(self._values_edited)
            label = QtWidgets.QLabel(title)
            label.setBuddy(spin)
            label.setToolTip(purpose)
            current = QtWidgets.QLabel(NO_VALUE)
            current.setAccessibleName(f"Driver reports for {name} ({direction})")
            current.setToolTip(f"Value of {name} the loaded module reports")
            current.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            deviation = caption(QtWidgets.QLabel())
            deviation.setAccessibleName(f"Deviation from 1.0 for {name} ({direction})")
            deviation.setToolTip("Deviation of the driver value from 1.0 in percent")
            deviation.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight
                                   | QtCore.Qt.AlignmentFlag.AlignVCenter)
            deviation.setMinimumWidth(deviation.fontMetrics().horizontalAdvance("+00.0 %"))
            mark = QtWidgets.QLabel()
            mark.setAccessibleName(f"Change marker for {name} ({direction})")
            mark.setFixedWidth(mark.fontMetrics().horizontalAdvance(CHANGED_MARK) + 2)
            grid.addWidget(label, row, 0)
            grid.addWidget(current, row, 1)
            grid.addWidget(deviation, row, 2)
            grid.addWidget(spin, row, 3)
            grid.addWidget(mark, row, 4)
            self.current_labels.append(current)
            self.deviation_labels.append(deviation)
            self.spins.append(spin)
            self.change_marks.append(mark)
        grid.setColumnStretch(3, 1)

        # Two lines of state. A driver problem takes the place of both, so that
        # a hint never changes the height of the panel.
        self.status_label = MessageLabel("Driver status")
        self.pending_label = ElideLabel()
        self.pending_label.setAccessibleName("Unsaved changes")
        self.pending_label.setToolTip(
            "Whether the new values differ from what the driver reports.")
        self.saved_label = ElideLabel()
        self.saved_label.setAccessibleName("Saved to config state")
        self.saved_label.setToolTip(PERSIST)
        # One line of state while everything works (the two labels side by
        # side); a driver problem takes its place with what to do about it.
        state_box = QtWidgets.QWidget()
        state_layout = QtWidgets.QVBoxLayout(state_box)
        state_layout.setContentsMargins(0, 0, 0, 0)
        state_layout.setSpacing(2)
        state_layout.addWidget(self.status_label)
        state_line = QtWidgets.QHBoxLayout()
        state_line.setSpacing(SPACE_M)
        state_line.addWidget(self.pending_label, 2)
        state_line.addWidget(self.saved_label, 3)
        state_layout.addLayout(state_line)
        state_layout.addStretch(1)

        self.use_button = QtWidgets.QPushButton("Use resul&t")
        self.use_button.setToolTip("Fill the new values from the calibration result")
        self.use_button.clicked.connect(self.use_calibration)
        self.reset_button = QtWidgets.QPushButton("Reset to 1.&0")
        self.reset_button.setToolTip(
            "Set all four new values to 1.0 (no scaling). Apply this before a new "
            "calibration, so that it measures the unscaled mouse.")
        self.reset_button.clicked.connect(self.reset_values)
        self.read_button = QtWidgets.QPushButton("Read agai&n")
        self.read_button.setToolTip(
            "Read the current values from the driver and the config file again")
        self.read_button.clicked.connect(lambda: self.refresh())
        self.apply_button = QtWidgets.QPushButton("&Apply")
        self.apply_button.setToolTip("Write the new values to the driver. " + PERSIST)
        self.apply_button.clicked.connect(self.apply)
        self.save_button = QtWidgets.QPushButton("Save to confi&g")
        self.save_button.clicked.connect(self.save_config)

        buttons = QtWidgets.QGridLayout()
        buttons.setHorizontalSpacing(SPACE_M)
        buttons.setVerticalSpacing(SPACE_S)
        buttons.addWidget(self.use_button, 0, 0)
        buttons.addWidget(self.reset_button, 0, 1)
        buttons.addWidget(self.read_button, 0, 2)
        buttons.addWidget(self.apply_button, 1, 0)
        buttons.addWidget(self.save_button, 1, 1, 1, 2)

        # Rarely needed switches, out of the way.
        self.flip_check = QtWidgets.QCheckBox("Flip &Y")
        self.flip_check.setToolTip("Exchange the down and up values. " + FLIP_Y)
        self.flip_check.setChecked(bool(settings.value("driver/flip_y", False, type=bool)))
        self.flip_check.toggled.connect(self._flip_toggled)
        self.flip_hint = caption(WrapLabel("About Flip Y"))
        self.flip_hint.setText(FLIP_Y)
        self.auto_check = QtWidgets.QCheckBox("Apply saved values on start")
        self.auto_check.setToolTip(
            "Write the values of the last Apply to the driver whenever QuadSens starts. "
            "Not needed once they are saved to the config.")
        self.auto_check.setChecked(
            bool(settings.value("driver/apply_on_start", False, type=bool)))
        self.auto_check.toggled.connect(
            lambda checked: settings.setValue("driver/apply_on_start", checked))
        # The acceleration the driver is set to, read-only: in short in the head
        # of the card, in full here, with the way to the driver's own settings.
        self.accel = accel.Accel(accel.YEETMOUSE, problem="not read yet")
        self.accel_label = caption(ElideLabel())
        self.accel_label.setAccessibleName("Acceleration the driver is set to")
        self.settings_button = QtWidgets.QPushButton("Open yeetmouse settings…")
        self.settings_button.setToolTip(
            "Start the settings program of the driver (yeetmouse), where the "
            "acceleration is changed. QuadSens only shows it.")
        self.settings_button.clicked.connect(self.open_driver_settings)
        # Replaceable, so that tests start nothing.
        self.find_gui: Callable[[], str | None] = find_yeetmouse_gui
        self.launch: Callable[[str, str], bool] = start_program
        self.advanced = QtWidgets.QWidget()
        advanced_layout = QtWidgets.QVBoxLayout(self.advanced)
        advanced_layout.setContentsMargins(SPACE_M * 2, 0, 0, 0)
        advanced_layout.setSpacing(SPACE_S)
        # The button shares the line of the first switch, so that it costs no height.
        first_row = QtWidgets.QHBoxLayout()
        first_row.setSpacing(SPACE_M)
        first_row.addWidget(self.flip_check)
        first_row.addStretch(1)
        first_row.addWidget(self.settings_button)
        advanced_layout.addLayout(first_row)
        advanced_layout.addWidget(self.flip_hint)
        advanced_layout.addWidget(self.auto_check)
        self.advanced_toggle = Disclosure("Ad&vanced", self.advanced)
        self.advanced_toggle.setToolTip("Show the switches that are rarely needed: Flip Y "
                                        "and applying the saved values on start")
        # A switch that is on must not hide: it changes what Use result does.
        self.advanced_toggle.setChecked(self.flip_check.isChecked())

        for control in (self.use_button, self.flip_check, self.reset_button,
                        self.read_button, self.apply_button, self.auto_check,
                        self.advanced_toggle, self.settings_button):
            control.setAccessibleDescription(control.toolTip())

        self.message = MessageLabel("Driver message")
        # Right under Apply: whether the driver is applying the values, from a
        # fresh read of the module parameters.
        self.indicator = ActiveIndicator("Driver activity")
        self.active = active_linux.linux_indicator(DriverState(DriverStatus.NOT_LOADED))
        self._applied_at: float | None = None
        stored_at = settings.value("driver/applied_at")
        try:
            self._applied_at = float(stored_at) if stored_at is not None else None
        except (TypeError, ValueError):
            self._applied_at = None
        self._watch = QtCore.QTimer(self)
        self._watch.setInterval(WATCH_MS)
        self._watch.timeout.connect(self.watch)

        layout = self.card_layout()
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(SPACE_M)
        head.addWidget(heading("Driver (yeetmouse)"))
        head.addWidget(self.accel_label, 1)
        head.addWidget(self.advanced_toggle)
        layout.addLayout(head)
        layout.addLayout(grid)
        layout.addWidget(state_box)
        layout.addLayout(buttons)
        layout.addWidget(self.indicator)
        layout.addWidget(self.message)
        layout.addWidget(self.advanced)
        layout.addStretch(1)

        self.refresh()
        initial = self.state.scalars or self.saved_scalars()
        if initial is not None:
            self.set_values(initial)

    # ---- values -----------------------------------------------------------

    def values(self) -> Scalars:
        return Scalars(*(round(spin.value(), DECIMALS) for spin in self.spins))

    def set_values(self, scalars: Scalars) -> None:
        for spin, value in zip(self.spins, scalars.as_tuple()):
            spin.setValue(value)

    def saved_scalars(self) -> Scalars | None:
        """The values of the last successful Apply, kept in the settings."""
        raw = self._settings.value("driver/saved_values")
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            return None
        try:
            scalars = Scalars(*(float(item) for item in raw))
            scalars.validate()
        except (TypeError, ValueError):
            return None
        return scalars

    def flip_y(self) -> bool:
        return bool(self.flip_check.isChecked())

    def driver_values(self) -> Scalars | None:
        """What the driver uses: as reported, else as last written in this session."""
        return self.state.scalars if self.state.scalars is not None else self._assumed

    def result_values(self) -> Scalars | None:
        """The calibration result as it goes into the new values, None without one."""
        result = self._get_result()
        if result is None:
            return None
        mapped = driver_scalars(result.scalars, self.flip_y())
        return Scalars(*(round(value, DECIMALS) for value in mapped.as_tuple()))

    def changed_directions(self) -> list[str]:
        """Directions whose new value differs from what the driver reports.

        Empty when the driver values are unknown: there is nothing to compare.
        """
        reported = self.state.scalars
        if reported is None:
            return []
        return [direction for direction, spin, value
                in zip(DIRECTIONS, self.spins, reported.as_tuple())
                if abs(spin.value() - value) > SAME_VALUE]

    def has_unsaved_changes(self) -> bool:
        return bool(self.changed_directions())

    def tool_found(self) -> bool:
        return self._saver.find_tool() is not None

    def is_busy(self) -> bool:
        """True while pkexec or the save tool is running."""
        return self._process is not None

    def write_problem(self) -> str:
        """Why nothing can be written to the driver right now, or ""."""
        status = self.state.status
        if status is DriverStatus.NOT_LOADED:
            return "The yeetmouse module is not loaded (see the driver check)."
        if status is DriverStatus.UNSUPPORTED:
            return "The loaded yeetmouse module is not the fork (see the driver check)."
        if status is DriverStatus.NOT_READABLE and not self._driver.helper_installed():
            return "The driver is not writable for you (Help > Setup help)."
        return ""

    # ---- actions ----------------------------------------------------------

    def readable(self) -> bool | None:
        """True if the driver values were read, False if reading was refused."""
        if self.state.status is DriverStatus.OK:
            return True
        if self.state.status is DriverStatus.NOT_READABLE:
            return False
        return None

    def fork_missing(self) -> bool:
        """True when the forked module is not loaded: the driver setup can fix that."""
        return self.state.status in (DriverStatus.NOT_LOADED, DriverStatus.UNSUPPORTED)

    def refresh(self) -> DriverState:
        """Read the driver and the config file and show what they hold."""
        self.state = state = self._driver.read()
        if state.scalars is not None or state.status is not DriverStatus.NOT_READABLE:
            self._assumed = None
        values = state.scalars.as_tuple() if state.scalars is not None else None
        for index, label in enumerate(self.current_labels):
            label.setText(NO_VALUE if values is None else f"{values[index]:.6f}")
            self.deviation_labels[index].setText(
                "" if values is None else format_deviation(values[index]))
        self.status_label.show_message(
            driver_status(state, self._driver.helper_installed()),
            kind="info" if state.status is DriverStatus.OK else "warning")
        self.save_state = self._saver.state(self.driver_values())
        self._update_pending()
        self._update_indicator()
        self._update_accel()
        self.changed.emit()
        self.driver_read.emit()
        return state

    def _update_accel(self) -> bool:
        """Read the acceleration settings again; True if they changed."""
        found = accel.read_yeetmouse(self._driver.params_dir, self._saver.config_path)
        changed = found != self.accel
        self.accel = found
        self.accel_label.setText(found.short())
        self.accel_label.setAccessibleDescription(found.summary())
        self.accel_label.setToolTip(
            found.summary() + ". Read-only here; change it in the driver's own settings "
            "(Advanced > Open yeetmouse settings). This is what the driver was set to; "
            "it applies no curve if it rejects these parameters, and says so only in "
            "the kernel log.")
        return changed

    def open_driver_settings(self) -> None:
        """Start the driver's own settings program, or say how to get it."""
        program = self.find_gui()
        if program is None:
            self.message.show_message(
                "The settings program of the driver (%s) was not found. It is installed "
                "together with the fork (make install, or its package); see the README "
                "of the driver." % accel.YEETMOUSE_GUI)
        elif self.launch(program, ""):
            self.message.show_message(
                "Started %s. What you change there shows up here within a few seconds."
                % program)
        else:
            self.message.show_message("%s could not be started." % program, error=True)

    def _update_indicator(self) -> None:
        self.active = shown = active_linux.linux_indicator(
            self.state, self.saved_scalars(), self._applied_at)
        self.indicator.show_state(shown.mark, shown.headline, shown.detail, shown.tint,
                                  shown.kind)
        # With a driver problem the banner above says what to do; the indicator
        # then keeps to its headline, so that the card does not grow.
        self.indicator.detail.setVisible(self.state.status is DriverStatus.OK)

    def watch(self) -> None:
        """Look at the driver again, cheaply; redraw only when something changed.

        Runs every few seconds while the card is visible, so that a module
        that was unloaded, or values changed by another program, show up
        without pressing anything.
        """
        if self._process is not None or not self.isVisible():
            return
        if self._driver.read() != self.state:
            self.refresh()
        else:
            self._update_accel()

    def showEvent(self, event: object) -> None:  # noqa: N802 (Qt name)
        super().showEvent(event)        # type: ignore[arg-type]
        self._watch.start()

    def hideEvent(self, event: object) -> None:  # noqa: N802 (Qt name)
        self._watch.stop()
        super().hideEvent(event)        # type: ignore[arg-type]

    def _values_edited(self, *_args: object) -> None:
        self._update_pending()
        self.changed.emit()

    def _update_pending(self) -> None:
        """Mark edited rows, show the state lines and enable what can be done."""
        changed = self.changed_directions()
        for direction, spin, mark in zip(DIRECTIONS, self.spins, self.change_marks):
            edited = direction in changed
            mark.setText(CHANGED_MARK if edited else "")
            mark.setToolTip("Differs from the value the driver reports" if edited else "")
            font = spin.font()
            if font.bold() != edited:
                font.setBold(edited)
                spin.setFont(font)
        font = self.pending_label.font()
        font.setBold(bool(changed))
        self.pending_label.setFont(font)
        if self.state.scalars is None:
            self.pending_label.setText("Driver values unknown; Apply writes these.")
        elif changed:
            self.pending_label.setText(
                "%s Unsaved changes: %s" % (CHANGED_MARK, ", ".join(changed)))
        else:
            self.pending_label.setText("No unsaved changes.")
        tool_found = self.tool_found()
        self.saved_label.setText(save_state_text(self.save_state, tool_found))
        # The line may be cut when the card is narrow; the tooltips have it whole.
        self.pending_label.setToolTip(
            self.pending_label.text() + " Whether the new values differ from what the "
            "driver reports.")
        self.saved_label.setToolTip(self.saved_label.text() + " " + PERSIST)

        # While everything works the two state lines are shown; a driver problem
        # replaces them with what to do about it.
        ok = self.state.status is DriverStatus.OK
        self.status_label.setVisible(not ok)
        self.pending_label.setVisible(ok)
        self.saved_label.setVisible(ok)

        idle = self._process is None
        self.apply_button.setEnabled(idle and (self.state.scalars is None or bool(changed)))
        can_save = idle and tool_found and self.state.status not in (
            DriverStatus.NOT_LOADED, DriverStatus.UNSUPPORTED)
        self.save_button.setEnabled(can_save)
        if not tool_found:
            hint = "Not available: " + tool_missing()
        else:
            hint = ("Write the values the driver reports to %s, so that they survive a "
                    "module reload. Asks for authorization unless the file is writable "
                    "for you." % self._saver.config_path)
        self.save_button.setToolTip(hint)
        self.save_button.setAccessibleDescription(hint)

    def use_calibration(self) -> None:
        result = self._get_result()
        if result is None:
            self.message.show_message("No usable calibration result to take values from.",
                                      error=True)
            return
        scalars = driver_scalars(result.scalars, self.flip_y())
        self.set_values(scalars)
        self.message.show_message(
            "Filled from %s%s. Press Apply to write them."
            % (result.source, " with Y flipped" if self.flip_y() else ""))

    def reset_values(self) -> None:
        self.set_values(Scalars.unity())
        self.message.show_message("New values reset to 1.0. Press Apply to write them.")

    def reset_and_apply(self) -> None:
        """One step: set all four values to 1.0 and write them to the driver."""
        self.set_values(Scalars.unity())
        if self.state.scalars is not None and not self.has_unsaved_changes():
            self.message.show_message("The driver values are 1.0 already.")
            self.applied.emit(Scalars.unity())
            self.changed.emit()
            return
        self.apply()

    def _flip_toggled(self, checked: bool) -> None:
        self._settings.setValue("driver/flip_y", checked)
        self.set_values(self.values().flipped_y())
        self.message.show_message(
            "Flip Y %s: the down and up values were exchanged. Press Apply to write them."
            % ("on" if checked else "off"))
        self.flip_changed.emit(checked)
        self.changed.emit()

    def apply(self) -> None:
        """Write the new values: directly if permitted, otherwise through pkexec."""
        if self._process is not None:
            return
        scalars = self.values()
        try:
            self._driver.write_direct(scalars)
        except PermissionError:
            self._apply_with_helper(scalars)
            return
        except DriverError as exc:
            self.message.show_message(str(exc), error=True)
            return
        self._applied(scalars, "directly")

    def apply_saved(self) -> None:
        """Apply the values stored by the last successful Apply, if any."""
        saved = self.saved_scalars()
        if saved is None:
            self.message.show_message("No saved values to apply on start.")
            return
        self.set_values(saved)
        self.apply()

    def _apply_with_helper(self, scalars: Scalars) -> None:
        try:
            command = self._driver.helper_command(scalars)
        except DriverError as exc:
            self.message.show_message(str(exc), error=True)
            return
        self._pending = scalars
        self.message.show_message("Waiting for authorization (pkexec) ...")
        self._start(command, self._helper_finished, "pkexec could not be started.")

    def _helper_finished(self, exit_code: int, _status: QtCore.QProcess.ExitStatus) -> None:
        process, scalars = self._process, self._pending
        if process is None or scalars is None:
            return
        stderr = bytes(process.readAllStandardError().data()).decode("utf-8", "replace")
        self._finish_process()
        if exit_code != 0:
            self.message.show_message(describe_helper_failure(exit_code, stderr), error=True)
            return
        self._applied(scalars, "through pkexec")

    def _applied(self, scalars: Scalars, how: str) -> None:
        self._settings.setValue("driver/saved_values",
                                [f"{value:.6f}" for value in scalars.as_tuple()])
        self._applied_at = time.time()
        self._settings.setValue("driver/applied_at", self._applied_at)
        self._assumed = scalars
        state = self.refresh()
        if state.scalars is None:
            self.message.show_message(
                f"Applied {how}. The driver values cannot be read back here, so the "
                "result is not confirmed.")
        elif state.scalars.is_close(scalars):
            self.message.show_message(
                f"✓ Applied {how}. The driver reports the new values.")
        else:
            self.message.show_message(
                f"Written {how}, but the driver reports different values (see "
                "\"Driver reports\").", error=True)
            return
        self.applied.emit(scalars)

    # ---- save to config ---------------------------------------------------

    def save_config(self) -> None:
        """Persist the values the driver reports with the fork's yeetmousectl."""
        if self._process is not None:
            return
        try:
            command = self._saver.command()
        except PersistError as exc:
            self.message.show_message(f"Not saved: {exc}.", error=True)
            return
        self._save_command = command
        self.message.show_message(
            "Waiting for authorization (pkexec) ..." if command.elevated
            else "Saving to %s ..." % self._saver.config_path)
        self._start(command.argv, self._save_finished,
                    "Not saved: %s could not be started." % command.argv[0])

    def _save_finished(self, exit_code: int, _status: QtCore.QProcess.ExitStatus) -> None:
        process, command = self._process, self._save_command
        if process is None or command is None:
            return
        output = bytes(process.readAllStandardError().data()).decode("utf-8", "replace")
        output += bytes(process.readAllStandardOutput().data()).decode("utf-8", "replace")
        self._finish_process()
        if exit_code != 0:
            self.message.show_message(
                describe_save_failure(exit_code, output, command.elevated), error=True)
            self.refresh()
            return
        self.refresh()
        state, text = self._saver.verify(self.driver_values())
        self.save_state = state
        self._update_pending()
        saved = state is SaveState.SAVED
        self.message.show_message(("✓ " + text) if saved else text,
                                  error=state not in (SaveState.SAVED, SaveState.UNKNOWN))
        self.changed.emit()

    # ---- child processes --------------------------------------------------

    def _start(self, command: list[str],
               finished: Callable[[int, QtCore.QProcess.ExitStatus], None],
               start_error: str) -> None:
        process = QtCore.QProcess(self)
        process.finished.connect(finished)

        def failed(error: QtCore.QProcess.ProcessError) -> None:
            if error != QtCore.QProcess.ProcessError.FailedToStart:
                return  # a crash is reported through finished()
            self._finish_process()
            self.message.show_message(start_error, error=True)

        process.errorOccurred.connect(failed)
        self._process = process
        self._update_pending()
        self.changed.emit()
        process.start(command[0], command[1:])

    def _finish_process(self) -> None:
        if self._process is not None:
            self._process.deleteLater()
        self._process = None
        self._pending = None
        self._save_command = None
        self._update_pending()
        self.changed.emit()
