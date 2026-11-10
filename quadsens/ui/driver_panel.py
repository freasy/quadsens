""""Driver (yeetmouse)" area: show, edit and apply the four DirScale values."""

from __future__ import annotations

from collections.abc import Callable

from ..calibration import Calibration, driver_scalars
from ..driver import (
    PARAM_NAMES,
    Driver,
    DriverError,
    DriverState,
    DriverStatus,
    describe_helper_failure,
)
from ..hints import PERSIST, driver_status
from ..model import SCALE_MAX, SCALE_MIN, Scalars
from .qt import QtCore, QtWidgets
from .scale_spin import ScaleSpinBox
from .status import MessageLabel, WrapLabel

# Mouse direction each parameter scales; the driver's +Y is "down".
DIRECTIONS: tuple[str, ...] = ("right", "left", "down", "up")
ROW_TITLES: tuple[str, ...] = ("→ &Right:", "← &Left:", "↓ &Down:", "↑ &Up:")
NO_VALUE = "–"
DECIMALS = 4
# Half a step of the last digit shown: closer than this is "the same value".
SAME_VALUE = 0.5 * 10 ** -DECIMALS + 1e-9
CHANGED_MARK = "≠"


class DriverPanel(QtWidgets.QGroupBox):
    def __init__(self, driver: Driver, settings: QtCore.QSettings,
                 get_calibration: Callable[[], Calibration | None],
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Driver (yeetmouse)", parent)
        self._driver = driver
        self._settings = settings
        self._get_calibration = get_calibration
        self._process: QtCore.QProcess | None = None
        self._pending: Scalars | None = None
        self.state: DriverState = DriverState(DriverStatus.NOT_LOADED)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        for column, title in enumerate(("Direction", "Driver reports", "New value")):
            header = QtWidgets.QLabel(title)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            grid.addWidget(header, 0, column)
        self.current_labels: list[QtWidgets.QLabel] = []
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
            spin.valueChanged.connect(self._update_pending)
            label = QtWidgets.QLabel(title)
            label.setBuddy(spin)
            label.setToolTip(purpose)
            current = QtWidgets.QLabel(NO_VALUE)
            current.setAccessibleName(f"Driver reports for {name} ({direction})")
            current.setToolTip(f"Value of {name} the loaded module reports")
            current.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            mark = QtWidgets.QLabel()
            mark.setAccessibleName(f"Change marker for {name} ({direction})")
            mark.setFixedWidth(mark.fontMetrics().horizontalAdvance(CHANGED_MARK) + 2)
            grid.addWidget(label, row, 0)
            grid.addWidget(current, row, 1)
            grid.addWidget(spin, row, 2)
            grid.addWidget(mark, row, 3)
            self.current_labels.append(current)
            self.spins.append(spin)
            self.change_marks.append(mark)
        grid.setColumnStretch(2, 1)

        self.pending_label = QtWidgets.QLabel()
        self.pending_label.setAccessibleName("Unsaved changes")
        self.pending_label.setToolTip(
            "Whether the new values differ from what the driver reports. " + PERSIST)

        self.use_button = QtWidgets.QPushButton("Use cali&bration")
        self.use_button.setToolTip("Fill the new values from the calibration file")
        self.use_button.clicked.connect(self.use_calibration)
        self.flip_check = QtWidgets.QCheckBox("Flip &Y")
        self.flip_check.setToolTip(
            "Exchange the down and up values. Use it if up and down feel swapped "
            "after applying a calibration.")
        self.flip_check.setChecked(bool(settings.value("driver/flip_y", False, type=bool)))
        self.flip_check.toggled.connect(self._flip_toggled)
        self.reset_button = QtWidgets.QPushButton("Reset to 1.&0")
        self.reset_button.setToolTip(
            "Set all four new values to 1.0 (no scaling). Apply this before a new "
            "calibration, so that it measures the unscaled mouse.")
        self.reset_button.clicked.connect(self.reset_values)
        self.read_button = QtWidgets.QPushButton("Read agai&n")
        self.read_button.setToolTip("Read the current values from the driver again")
        self.read_button.clicked.connect(lambda: self.refresh())
        self.apply_button = QtWidgets.QPushButton("&Apply")
        self.apply_button.setToolTip("Write the new values to the driver. " + PERSIST)
        self.apply_button.clicked.connect(self.apply)

        buttons = QtWidgets.QGridLayout()
        buttons.addWidget(self.use_button, 0, 0)
        buttons.addWidget(self.reset_button, 0, 1)
        buttons.addWidget(self.read_button, 1, 0)
        buttons.addWidget(self.apply_button, 1, 1)

        self.auto_check = QtWidgets.QCheckBox("Apply sa&ved on start")
        self.auto_check.setAccessibleName("Apply saved values on start")
        self.auto_check.setToolTip("Write the last applied values to the driver when "
                                   "the application starts")
        self.auto_check.setChecked(
            bool(settings.value("driver/apply_on_start", False, type=bool)))
        self.auto_check.toggled.connect(
            lambda checked: settings.setValue("driver/apply_on_start", checked))

        checks = QtWidgets.QHBoxLayout()
        checks.addWidget(self.flip_check)
        checks.addSpacing(8)
        checks.addWidget(self.auto_check)
        checks.addStretch(1)

        for control in (self.use_button, self.flip_check, self.reset_button,
                        self.read_button, self.apply_button, self.auto_check):
            control.setAccessibleDescription(control.toolTip())

        self.status_label = WrapLabel("Driver status")
        self.message = MessageLabel("Driver message")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addLayout(grid)
        layout.addWidget(self.pending_label)
        layout.addLayout(buttons)
        layout.addLayout(checks)
        layout.addWidget(self.message)
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

    # ---- actions ----------------------------------------------------------

    def refresh(self) -> DriverState:
        """Read the driver and show what it reports."""
        self.state = state = self._driver.read()
        values = state.scalars.as_tuple() if state.scalars is not None else None
        for index, label in enumerate(self.current_labels):
            label.setText(NO_VALUE if values is None else f"{values[index]:.6f}")
        self.status_label.setText(driver_status(state, self._driver.helper_installed()))
        # While everything works, the line below the values says so; the status
        # line appears when there is something to do about the driver.
        self.status_label.setVisible(state.status is not DriverStatus.OK)
        self._update_pending()
        return state

    def _update_pending(self, *_args: object) -> None:
        """Mark edited rows and enable Apply only when there is something to write."""
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
            self.pending_label.setText(
                "Driver values unknown; Apply writes these.")
        elif changed:
            self.pending_label.setText(
                "%s Unsaved changes: %s" % (CHANGED_MARK, ", ".join(changed)))
        else:
            self.pending_label.setText("Driver values read. No unsaved changes.")
        self.apply_button.setEnabled(
            self._process is None and (self.state.scalars is None or bool(changed)))

    def use_calibration(self) -> None:
        calibration = self._get_calibration()
        if calibration is None:
            self.message.show_message("No usable calibration file to take values from.",
                                      error=True)
            return
        scalars = driver_scalars(calibration.firmware, self.flip_y())
        try:
            scalars.validate()
        except ValueError as exc:
            self.message.show_message(f"Calibration not used: {exc}", error=True)
            return
        self.set_values(scalars)
        self.message.show_message(
            "Filled from the calibration file%s. Press Apply to write them."
            % (" with Y flipped" if self.flip_y() else ""))

    def reset_values(self) -> None:
        self.set_values(Scalars.unity())
        self.message.show_message("New values reset to 1.0. Press Apply to write them.")

    def _flip_toggled(self, checked: bool) -> None:
        self._settings.setValue("driver/flip_y", checked)
        self.set_values(self.values().flipped_y())
        self.message.show_message(
            "Flip Y %s: the down and up values were exchanged. Press Apply to write them."
            % ("on" if checked else "off"))

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
        process = QtCore.QProcess(self)
        process.finished.connect(self._helper_finished)
        process.errorOccurred.connect(self._helper_error)
        self._process = process
        self._pending = scalars
        self.apply_button.setEnabled(False)
        self.message.show_message("Waiting for authorization (pkexec) ...")
        process.start(command[0], command[1:])

    def _helper_error(self, error: QtCore.QProcess.ProcessError) -> None:
        if error != QtCore.QProcess.ProcessError.FailedToStart:
            return  # a crash is reported through finished()
        self._finish_process()
        self.message.show_message("pkexec could not be started.", error=True)

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

    def _finish_process(self) -> None:
        if self._process is not None:
            self._process.deleteLater()
        self._process = None
        self._pending = None
        self._update_pending()

    def _applied(self, scalars: Scalars, how: str) -> None:
        self._settings.setValue("driver/saved_values",
                                [f"{value:.6f}" for value in scalars.as_tuple()])
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
