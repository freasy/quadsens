""""Calibration" area: the newest result file next to what was written to the emulator."""

from __future__ import annotations

import time

from ..calibration import (
    Calibration,
    CalibrationError,
    display_path,
    find_latest,
    load,
    watch_paths,
)
from ..emulator import DirectionalWrites
from ..hints import NO_RESULT
from .compass import CompassWidget
from .qt import QtCore, QtWidgets, Signal
from .status import MessageLabel

ROWS: tuple[tuple[str, str], ...] = (
    ("XPos", "x_pos"), ("XNeg", "x_neg"), ("YPos", "y_pos"), ("YNeg", "y_neg"))
NO_VALUE = "–"


def _stamp(seconds: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(seconds))


class CalibrationPanel(QtWidgets.QGroupBox):
    calibration_changed = Signal()

    def __init__(self, pattern: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Calibration", parent)
        self._pattern = pattern
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
        top.addWidget(path_label)
        top.addWidget(self.path_edit, 1)
        top.addWidget(self.refresh_button)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        for column, title in enumerate(("Scalar", "From file", "Written to\nvirtual mouse")):
            header = QtWidgets.QLabel(title)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            grid.addWidget(header, 0, column)
        self.file_labels: dict[str, QtWidgets.QLabel] = {}
        self.emulator_labels: dict[str, QtWidgets.QLabel] = {}
        for row, (title, key) in enumerate(ROWS, start=1):
            grid.addWidget(QtWidgets.QLabel(title), row, 0)
            for column, (store, source) in enumerate(
                    ((self.file_labels, "from file"),
                     (self.emulator_labels, "written to virtual mouse")), start=1):
                label = QtWidgets.QLabel(NO_VALUE)
                label.setAccessibleName(f"{title} {source}")
                label.setTextInteractionFlags(
                    QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(label, row, column)
                store[key] = label
        grid.addWidget(QtWidgets.QLabel("Enabled"), len(ROWS) + 1, 0)
        grid.addWidget(QtWidgets.QLabel(NO_VALUE), len(ROWS) + 1, 1)
        self.enabled_label = QtWidgets.QLabel(NO_VALUE)
        self.enabled_label.setAccessibleName("Enabled flag written to virtual mouse")
        grid.addWidget(self.enabled_label, len(ROWS) + 1, 2)
        self.emulator_time_label = QtWidgets.QLabel("No writes this session")
        self.emulator_time_label.setAccessibleName("Time of last write to virtual mouse")
        grid.addWidget(self.emulator_time_label, len(ROWS) + 2, 0, 1, 3)
        grid.setRowStretch(len(ROWS) + 3, 1)

        self.compass = CompassWidget()

        body = QtWidgets.QHBoxLayout()
        body.addLayout(grid)
        body.addWidget(self.compass, 1)

        self.message = MessageLabel("Calibration message")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.mtime_label)
        layout.addLayout(body, 1)
        layout.addWidget(self.message)

        self._watcher = QtCore.QFileSystemWatcher(self)
        self._debounce = QtCore.QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self.refresh)
        self._watcher.fileChanged.connect(self._on_path_changed)
        self._watcher.directoryChanged.connect(self._on_path_changed)
        self.refresh()

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
            self.compass.set_values(calibration.directional)
            self.message.show_message(
                "" if calibration.directional is not None
                else "The file has no usable directionalScalars; only the four "
                     "scalars are shown.")
        else:
            self.mtime_label.setText("Modified: " + NO_VALUE)
            for label in self.file_labels.values():
                label.setText(NO_VALUE)
            self.compass.set_values(None)
            if error:
                self.message.show_message(error, error=True)
            else:
                self.message.show_message(NO_RESULT)
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
        for _title, key in ROWS:
            value = getattr(writes, key)
            self.emulator_labels[key].setText(NO_VALUE if value is None else f"{value:.6f}")
        if writes.enabled is None:
            self.enabled_label.setText(NO_VALUE)
        else:
            self.enabled_label.setText("yes" if writes.enabled else "no")
        if writes.updated is not None:
            self.emulator_time_label.setText("Last write: " + _stamp(writes.updated))
