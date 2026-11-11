""""Calibration result" area: the four multipliers of the newest result as a diagram."""

from __future__ import annotations

import time

from ..calibration import (
    DIRECTION_LABELS,
    Calibration,
    CalibrationError,
    Result,
    display_path,
    driver_scalars,
    find_latest,
    load,
    pick_result,
    watch_paths,
)
from ..emulator import DirectionalWrites
from ..hints import NO_RESULT
from .compass import DirectionDiagram
from .qt import QtCore, QtWidgets, Signal
from .status import SPACE_L, SPACE_M, SPACE_S, Disclosure, MessageLabel, column_header, heading

ROWS: tuple[tuple[str, str], ...] = (
    ("XPos", "x_pos"), ("XNeg", "x_neg"), ("YPos", "y_pos"), ("YNeg", "y_neg"))
NO_VALUE = "–"


def _stamp(seconds: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(seconds))


class CalibrationPanel(QtWidgets.QWidget):
    calibration_changed = Signal()

    def __init__(self, pattern: str, settings: QtCore.QSettings | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Calibration result")
        self._pattern = pattern
        self._settings = settings
        self._flip_y = False
        self._writes = DirectionalWrites()
        self.calibration: Calibration | None = None

        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("no calibration file found yet")
        self.path_edit.setAccessibleName("Calibration file path")
        self.path_edit.setAccessibleDescription(
            "The newest calibration result file. It is found and re-read automatically.")
        path_label = QtWidgets.QLabel("Result f&ile:")
        path_label.setBuddy(self.path_edit)
        self.refresh_button = QtWidgets.QPushButton("R&efresh")
        self.refresh_button.setAccessibleName("Refresh calibration")
        self.refresh_button.setToolTip("Look for the newest calibration file again")
        self.refresh_button.setAccessibleDescription(self.refresh_button.toolTip())
        self.refresh_button.clicked.connect(self.refresh)
        self.mtime_label = QtWidgets.QLabel()
        self.mtime_label.setAccessibleName("Calibration file modification time")

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(SPACE_M)
        top.addWidget(path_label)
        top.addWidget(self.path_edit, 1)
        top.addWidget(self.refresh_button)

        # Main message: the four multipliers as they go to the driver.
        self.diagram = DirectionDiagram()
        self.compass = self.diagram  # former name

        # Secondary: the raw numbers, hidden until asked for.
        self.details = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(self.details)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE_L)
        grid.setVerticalSpacing(2)
        for column, title in enumerate(("Scalar", "From file", "Sent to virtual mouse")):
            grid.addWidget(column_header(title), 0, column)
        self.file_labels: dict[str, QtWidgets.QLabel] = {}
        self.emulator_labels: dict[str, QtWidgets.QLabel] = {}
        for row, (title, key) in enumerate(ROWS, start=1):
            grid.addWidget(QtWidgets.QLabel(title), row, 0)
            for column, (store, source) in enumerate(
                    ((self.file_labels, "from file"),
                     (self.emulator_labels, "sent to virtual mouse")), start=1):
                label = QtWidgets.QLabel(NO_VALUE)
                label.setAccessibleName(f"{title} {source}")
                label.setTextInteractionFlags(
                    QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(label, row, column)
                store[key] = label
        grid.addWidget(QtWidgets.QLabel("Enabled"), len(ROWS) + 1, 0)
        grid.addWidget(QtWidgets.QLabel(NO_VALUE), len(ROWS) + 1, 1)
        self.enabled_label = QtWidgets.QLabel(NO_VALUE)
        self.enabled_label.setAccessibleName("Enabled flag sent to virtual mouse")
        grid.addWidget(self.enabled_label, len(ROWS) + 1, 2)
        self.emulator_time_label = QtWidgets.QLabel("Nothing sent this session")
        self.emulator_time_label.setAccessibleName("Time of last write to virtual mouse")
        grid.addWidget(self.emulator_time_label, len(ROWS) + 2, 0, 1, 3)
        self.directional_label = MessageLabel("Eight measured directional values")
        grid.addWidget(self.directional_label, len(ROWS) + 3, 0, 1, 3)
        grid.setColumnStretch(2, 1)

        self.details_toggle = Disclosure("&Measured values", self.details)
        self.details_toggle.setToolTip(
            "Show the raw numbers: the four scalars from the file and from the virtual "
            "mouse, and the eight measured directions")
        self.details_toggle.setAccessibleDescription(self.details_toggle.toolTip())
        if settings is not None:
            self.details_toggle.setChecked(
                bool(settings.value("ui/show_measured", False, type=bool)))
            self.details_toggle.toggled.connect(
                lambda shown: settings.setValue("ui/show_measured", shown))

        self.message = MessageLabel("Calibration message")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S + 2)
        head = QtWidgets.QHBoxLayout()
        head.addWidget(heading("Calibration result"), 1)
        head.addWidget(self.details_toggle)
        layout.addLayout(head)
        layout.addLayout(top)
        layout.addWidget(self.mtime_label)
        layout.addWidget(self.diagram, 1)
        layout.addWidget(self.details)
        layout.addWidget(self.message)

        self._watcher = QtCore.QFileSystemWatcher(self)
        self._debounce = QtCore.QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self.refresh)
        self._watcher.fileChanged.connect(self._on_path_changed)
        self._watcher.directoryChanged.connect(self._on_path_changed)
        self.refresh()

    # ---- result -----------------------------------------------------------

    def result(self) -> Result | None:
        """The result to take values from: the file or what was sent to the mouse."""
        return pick_result(self.calibration, self._writes.scalars(), self._writes.updated)

    def set_flip_y(self, flip: bool) -> None:
        """Show the multipliers with the two Y directions exchanged."""
        self._flip_y = flip
        self._show_result()

    def _show_result(self) -> None:
        result = self.result()
        self.diagram.set_scalars(
            driver_scalars(result.scalars, self._flip_y) if result is not None else None)

    def refresh(self) -> None:
        """Re-read the newest calibration file and re-arm the file watcher."""
        calibration: Calibration | None = None
        error = ""
        path = find_latest(self._pattern)
        if path is not None:
            try:
                calibration = load(path)
            except CalibrationError as exc:
                error = str(exc)
        self.calibration = calibration

        self.path_edit.setText(display_path(path) if path is not None else "")
        self.path_edit.setCursorPosition(len(self.path_edit.text()))
        self.path_edit.setToolTip(str(path) if path is not None else "")
        if calibration is not None:
            self.mtime_label.setText("Modified: " + _stamp(calibration.mtime))
            for _title, key in ROWS:
                self.file_labels[key].setText(f"{getattr(calibration.firmware, key):.6f}")
            self.diagram.set_values(calibration.directional)
            if calibration.directional is not None:
                self.directional_label.show_message("Measured: " + "  ".join(
                    f"{label} {value:.3f}"
                    for label, value in zip(DIRECTION_LABELS, calibration.directional)))
                self.message.show_message("")
            else:
                self.directional_label.show_message("")
                self.message.show_message(
                    "The file has no usable directionalScalars; only the four "
                    "scalars are shown.")
        else:
            self.mtime_label.setText("Modified: " + NO_VALUE)
            for label in self.file_labels.values():
                label.setText(NO_VALUE)
            self.diagram.set_values(None)
            self.directional_label.show_message("")
            if error:
                self.message.show_message(error, error=True)
            else:
                self.message.show_message(NO_RESULT)
        self._show_result()
        self._rearm_watcher()
        self.calibration_changed.emit()

    def _on_path_changed(self, _path: str) -> None:
        self._debounce.start()

    def stop_watching(self) -> None:
        """Stop reacting to file changes (used when the window closes)."""
        self._debounce.stop()
        watched = self._watcher.files() + self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)

    def _rearm_watcher(self) -> None:
        wanted = set(watch_paths(self._pattern))
        current = set(self._watcher.files()) | set(self._watcher.directories())
        obsolete = sorted(current - wanted)
        if obsolete:
            self._watcher.removePaths(obsolete)
        missing = sorted(wanted - current)
        if missing:
            self._watcher.addPaths(missing)

    def set_emulator_writes(self, writes: DirectionalWrites) -> None:
        self._writes = writes
        for _title, key in ROWS:
            value = getattr(writes, key)
            self.emulator_labels[key].setText(NO_VALUE if value is None else f"{value:.6f}")
        if writes.enabled is None:
            self.enabled_label.setText(NO_VALUE)
        else:
            self.enabled_label.setText("yes" if writes.enabled else "no")
        if writes.updated is not None:
            self.emulator_time_label.setText("Last write: " + _stamp(writes.updated))
        self._show_result()
        self.calibration_changed.emit()
