""""Virtual mouse" area: start/stop the emulated device and show its traffic."""

from __future__ import annotations

from collections.abc import Callable

from ..emulator import (
    DEFAULT_PRODUCT_ID,
    KNOWN_PRODUCT_IDS,
    VENDOR_ID,
    DirectionalWrites,
    EmulatorError,
    EmulatorSession,
    LogEntry,
    format_entry,
)
from .qt import QtCore, QtGui, QtWidgets, Signal
from .status import MessageLabel, StatusDot

LOG_LINES = 500

SessionFactory = Callable[[int, Callable[[LogEntry], None]], EmulatorSession]


class MousePanel(QtWidgets.QGroupBox):
    """Runs the emulator inside the GUI thread.

    The uhid descriptors are watched with QSocketNotifier, so each kernel event
    is handled as an ordinary Qt event and nothing blocks the interface.
    """

    writes_changed = Signal()

    def __init__(self, session_factory: SessionFactory,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Virtual mouse", parent)
        self._factory = session_factory
        self._session: EmulatorSession | None = None
        self._notifiers: list[QtCore.QSocketNotifier] = []
        self.writes = DirectionalWrites()

        self.pid_combo = QtWidgets.QComboBox()
        self.pid_combo.setAccessibleName("Product ID of the virtual mouse")
        for pid in KNOWN_PRODUCT_IDS:
            suffix = " (default)" if pid == DEFAULT_PRODUCT_ID else ""
            self.pid_combo.addItem("0x%04X%s" % (pid, suffix), pid)
        self.pid_combo.setToolTip("USB product id the virtual mouse reports. "
                                  "Can only be changed while it is stopped.")
        pid_label = QtWidgets.QLabel("&Product ID:")
        pid_label.setBuddy(self.pid_combo)

        self.toggle_button = QtWidgets.QPushButton("&Start virtual mouse")
        self.toggle_button.setAccessibleName("Start or stop the virtual mouse")
        self.toggle_button.clicked.connect(self.toggle)

        self.dot = StatusDot()
        self.status_label = QtWidgets.QLabel()
        self.status_label.setAccessibleName("Virtual mouse status")
        self.message = MessageLabel("Virtual mouse message")

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(LOG_LINES)
        self.log_view.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setFont(
            QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        self.log_view.setAccessibleName("Command log of the virtual mouse")
        self.log_view.setPlaceholderText("Decoded commands appear here while the virtual "
                                         "mouse is running (last %d lines)." % LOG_LINES)
        self.log_view.setMinimumHeight(56)
        self.log_view.setTabChangesFocus(True)
        log_label = QtWidgets.QLabel("Command &log:")
        log_label.setBuddy(self.log_view)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(pid_label)
        controls.addWidget(self.pid_combo)
        controls.addWidget(self.toggle_button)
        controls.addSpacing(12)
        controls.addWidget(self.dot)
        controls.addWidget(self.status_label)
        controls.addStretch(1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.message)
        layout.addWidget(log_label)
        layout.addWidget(self.log_view, 1)
        self.message.hide()
        self._update_status()

    # ---- settings ---------------------------------------------------------

    def product_id(self) -> int:
        return int(self.pid_combo.currentData())

    def set_product_id(self, pid: int) -> None:
        index = self.pid_combo.findData(pid)
        if index >= 0:
            self.pid_combo.setCurrentIndex(index)

    # ---- start / stop -----------------------------------------------------

    def is_running(self) -> bool:
        return self._session is not None and self._session.running

    def toggle(self) -> None:
        if self.is_running():
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        if self.is_running():
            return
        self._show_message("")
        try:
            session = self._factory(self.product_id(), self._on_entry)
            session.start()
        except (EmulatorError, OSError) as exc:
            self._show_message(str(exc), error=True)
            self._update_status()
            return
        self._session = session
        for fd in session.fds:
            notifier = QtCore.QSocketNotifier(fd, QtCore.QSocketNotifier.Type.Read, self)
            notifier.activated.connect(lambda *_args, fd=fd: self._pump(fd))
            self._notifiers.append(notifier)
        self._update_status()

    def stop(self, reason: str = "") -> None:
        """Tear the device down. Safe to call when it is not running."""
        for notifier in self._notifiers:
            notifier.setEnabled(False)
            notifier.deleteLater()
        self._notifiers = []
        session, self._session = self._session, None
        if session is not None:
            session.stop()
        if reason:
            self._show_message(reason, error=True)
        self._update_status()

    def _pump(self, fd: int) -> None:
        session = self._session
        if session is None:
            return
        try:
            session.pump(fd)
        except OSError as exc:
            self.stop("virtual mouse stopped, uhid I/O failed: %s" % exc)
            return
        self._update_status()

    # ---- log --------------------------------------------------------------

    def _on_entry(self, entry: LogEntry) -> None:
        self.log_view.appendPlainText(format_entry(entry))
        if "highlight" in entry:
            self.log_view.appendPlainText(">>> DIRECTIONAL WRITE: %s" % entry["highlight"])
        if self.writes.feed(entry):
            self.writes_changed.emit()

    # ---- presentation -----------------------------------------------------

    def _show_message(self, text: str, error: bool = False) -> None:
        self.message.show_message(text, error)
        self.message.setVisible(bool(text))

    def _update_status(self) -> None:
        session = self._session
        running = session is not None and session.running
        self.pid_combo.setEnabled(not running)
        self.toggle_button.setText("&Stop virtual mouse" if running else "&Start virtual mouse")
        if not running or session is None:
            self.dot.set_state(StatusDot.OFF)
            self.status_label.setText("Stopped")
        elif session.open_count > 0:
            self.dot.set_state(StatusDot.ACTIVE)
            self.status_label.setText("Running: device opened by a client (%04X:%04X)"
                                      % (VENDOR_ID, session.product_id))
        else:
            self.dot.set_state(StatusDot.WAITING)
            self.status_label.setText("Running: waiting for a client (%04X:%04X)"
                                      % (VENDOR_ID, session.product_id))
