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
from ..model import SCALE_MAX, SCALE_MIN, Scalars
from .qt import QtCore, QtWidgets
from .status import MessageLabel

# Mouse direction each parameter scales; the driver's +Y is "down".
ROW_TITLES: tuple[str, ...] = (
    "DirScaleXPos (&right):", "DirScaleXNeg (l&eft):",
    "DirScaleYPos (&down):", "DirScaleYNeg (&up):")
NO_VALUE = "–"
DECIMALS = 4


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
        for column, title in enumerate(("Parameter", "Driver reports", "New value")):
            header = QtWidgets.QLabel(title)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            grid.addWidget(header, 0, column)
        self.current_labels: list[QtWidgets.QLabel] = []
        self.spins: list[QtWidgets.QDoubleSpinBox] = []
        for row, (title, name) in enumerate(zip(ROW_TITLES, PARAM_NAMES), start=1):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(SCALE_MIN, SCALE_MAX)
            spin.setDecimals(DECIMALS)
            spin.setSingleStep(0.001)
            spin.setValue(1.0)
            spin.setAccessibleName(f"New value for {name}")
            label = QtWidgets.QLabel(title)
            label.setBuddy(spin)
            current = QtWidgets.QLabel(NO_VALUE)
            current.setAccessibleName(f"Driver reports for {name}")
            grid.addWidget(label, row, 0)
            grid.addWidget(current, row, 1)
            grid.addWidget(spin, row, 2)
            self.current_labels.append(current)
            self.spins.append(spin)
        grid.setColumnStretch(2, 1)

        self.use_button = QtWidgets.QPushButton("Use &calibration")
        self.use_button.setToolTip("Fill the new values from the calibration file")
        self.use_button.clicked.connect(self.use_calibration)
        self.flip_check = QtWidgets.QCheckBox("Flip &Y")
        self.flip_check.setToolTip(
            "Exchange YPos and YNeg. Use it if up and down feel swapped: the Y sign "
            "convention of the calibration is not confirmed.")
        self.flip_check.setChecked(bool(settings.value("driver/flip_y", False, type=bool)))
        self.flip_check.toggled.connect(self._flip_toggled)
        self.reset_button = QtWidgets.QPushButton("Reset to 1.&0")
        self.reset_button.setToolTip("Set all four new values to 1.0 (no scaling)")
        self.reset_button.clicked.connect(self.reset_values)
        self.read_button = QtWidgets.QPushButton("Read a&gain")
        self.read_button.setToolTip("Read the current values from the driver again")
        self.read_button.clicked.connect(lambda: self.refresh())
        self.apply_button = QtWidgets.QPushButton("&Apply")
        self.apply_button.setToolTip("Write the new values to the driver")
        self.apply_button.clicked.connect(self.apply)

        buttons = QtWidgets.QGridLayout()
        buttons.addWidget(self.use_button, 0, 0)
        buttons.addWidget(self.flip_check, 0, 1)
        buttons.addWidget(self.reset_button, 0, 2)
        buttons.addWidget(self.read_button, 1, 0)
        buttons.addWidget(self.apply_button, 1, 2)

        self.auto_check = QtWidgets.QCheckBox("Apply saved &values on start")
        self.auto_check.setToolTip("Write the last applied values to the driver when "
                                   "the application starts")
        self.auto_check.setChecked(
            bool(settings.value("driver/apply_on_start", False, type=bool)))
        self.auto_check.toggled.connect(
            lambda checked: settings.setValue("driver/apply_on_start", checked))

        self.status_label = QtWidgets.QLabel()
        self.status_label.setAccessibleName("Driver status")
        self.status_label.setWordWrap(True)
        self.message = MessageLabel("Driver message")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addLayout(grid)
        layout.addLayout(buttons)
        layout.addWidget(self.auto_check)
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

    # ---- actions ----------------------------------------------------------

    def refresh(self) -> DriverState:
        """Read the driver and show what it reports."""
        self.state = state = self._driver.read()
        values = state.scalars.as_tuple() if state.scalars is not None else None
        for index, label in enumerate(self.current_labels):
            label.setText(NO_VALUE if values is None else f"{values[index]:.6f}")
        if state.status is DriverStatus.OK:
            text = "Status: values read from the driver."
        elif state.status is DriverStatus.NOT_LOADED:
            text = "Status: module not loaded. Load yeetmouse before applying."
        elif state.status is DriverStatus.NOT_READABLE:
            text = ("Status: not readable (root-only parameters). Apply will ask for "
                    "authorization.")
        elif state.status is DriverStatus.UNSUPPORTED:
            text = "Status: module loaded, but it has no DirScale parameters (not patched)."
        else:
            text = f"Status: error reading the driver ({state.message})."
        self.status_label.setText(text)
        return state

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
            "Flip Y %s: the two Y values were exchanged. Press Apply to write them."
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
        self.apply_button.setEnabled(True)

    def _applied(self, scalars: Scalars, how: str) -> None:
        self._settings.setValue("driver/saved_values",
                                [f"{value:.6f}" for value in scalars.as_tuple()])
        state = self.refresh()
        if state.scalars is None:
            self.message.show_message(
                f"Applied {how}. The driver values cannot be read back here, so the "
                "result is not confirmed.")
        elif state.scalars.is_close(scalars):
            self.message.show_message(f"Applied {how}. The driver reports the new values.")
        else:
            self.message.show_message(
                f"Written {how}, but the driver reports different values (see "
                "\"Driver reports\").", error=True)
