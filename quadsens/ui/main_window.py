"""Main window: virtual mouse on top, calibration and driver below."""

from __future__ import annotations

from collections.abc import Callable

from ..driver import Driver
from ..emulator import DEFAULT_PRODUCT_ID, EmulatorSession, LogEntry
from .calibration_panel import CalibrationPanel
from .driver_panel import DriverPanel
from .mouse_panel import MousePanel
from .qt import QtCore, QtGui, QtWidgets


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, settings: QtCore.QSettings, calibration_glob: str, driver: Driver,
                 emulator_state_path: str | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("QuadSens")

        def make_session(pid: int, on_log: Callable[[LogEntry], None]) -> EmulatorSession:
            return EmulatorSession(product_id=pid, state_path=emulator_state_path,
                                   on_log=on_log)

        self.mouse_panel = MousePanel(make_session)
        self.calibration_panel = CalibrationPanel(calibration_glob)
        self.driver_panel = DriverPanel(driver, settings,
                                        lambda: self.calibration_panel.calibration)
        self.mouse_panel.writes_changed.connect(
            lambda: self.calibration_panel.set_emulator_writes(self.mouse_panel.writes))

        lower = QtWidgets.QHBoxLayout()
        lower.addWidget(self.calibration_panel, 3)
        lower.addWidget(self.driver_panel, 2)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.addWidget(self.mouse_panel, 1)
        layout.addLayout(lower)
        self.setCentralWidget(central)

        # Keyboard order follows the workflow: mouse, calibration, driver.
        order: list[QtWidgets.QWidget] = [
            self.mouse_panel.pid_combo, self.mouse_panel.toggle_button,
            self.mouse_panel.log_view,
            self.calibration_panel.path_edit, self.calibration_panel.refresh_button,
            *self.driver_panel.spins,
            self.driver_panel.use_button, self.driver_panel.flip_check,
            self.driver_panel.reset_button, self.driver_panel.read_button,
            self.driver_panel.apply_button, self.driver_panel.auto_check,
        ]
        for first, second in zip(order, order[1:]):
            QtWidgets.QWidget.setTabOrder(first, second)

        self.mouse_panel.set_product_id(
            int(settings.value("emulator/pid", DEFAULT_PRODUCT_ID, type=int)))
        self.resize(900, 640)
        geometry = settings.value("window/geometry")
        if isinstance(geometry, QtCore.QByteArray) and not geometry.isEmpty():
            self.restoreGeometry(geometry)

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
