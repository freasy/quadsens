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
    summarize_entry,
)
from ..uhid_text import UHID_OK, uhid_short
from .qt import QtCore, QtGui, QtWidgets, Signal
from .status import (
    SPACE_M,
    SPACE_S,
    Card,
    Disclosure,
    MessageLabel,
    StatusDot,
    caption,
    heading,
)

LOG_LINES = 500
# Lines that report a directional write start with this marker. The marker is
# what identifies them; the highlighting only makes them easier to spot.
WRITE_PREFIX = ">>> DIRECTIONAL WRITE: "

SessionFactory = Callable[[int, Callable[[LogEntry], None]], EmulatorSession]


def write_line_colors(palette: QtGui.QPalette) -> tuple[QtGui.QColor, QtGui.QColor]:
    """(background, text) for directional-write lines, derived from ``palette``.

    The background is the view's base colour tinted with the highlight colour,
    so it follows light and dark themes and keeps the normal text colour legible.
    """
    base = palette.color(QtGui.QPalette.ColorRole.Base)
    accent = palette.color(QtGui.QPalette.ColorRole.Highlight)
    share = 0.28
    background = QtGui.QColor(
        round(base.red() + (accent.red() - base.red()) * share),
        round(base.green() + (accent.green() - base.green()) * share),
        round(base.blue() + (accent.blue() - base.blue()) * share))
    return background, palette.color(QtGui.QPalette.ColorRole.Text)


class WriteHighlighter(QtGui.QSyntaxHighlighter):
    """Sets the directional-write lines apart: bold on a tinted background."""

    def __init__(self, view: QtWidgets.QPlainTextEdit) -> None:
        super().__init__(view.document())
        self._view = view

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt name)
        if not text.startswith(WRITE_PREFIX):
            return
        background, foreground = write_line_colors(self._view.palette())
        fmt = QtGui.QTextCharFormat()
        fmt.setFontWeight(QtGui.QFont.Weight.Bold)
        fmt.setBackground(background)
        fmt.setForeground(foreground)
        self.setFormat(0, len(text), fmt)


class MousePanel(Card):
    """Runs the emulator inside the GUI thread.

    The uhid descriptors are watched with QSocketNotifier, so each kernel event
    is handled as an ordinary Qt event and nothing blocks the interface.
    """

    writes_changed = Signal()
    running_changed = Signal(bool)

    def __init__(self, session_factory: SessionFactory,
                 uhid_access: Callable[[], str] | None = None,
                 settings: QtCore.QSettings | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Virtual mouse", parent)
        self._factory = session_factory
        self._uhid_access = uhid_access
        self._access = UHID_OK
        self._session: EmulatorSession | None = None
        self._notifiers: list[QtCore.QSocketNotifier] = []
        self._follow = True
        self._appending = False
        self.writes = DirectionalWrites()

        self.pid_combo = QtWidgets.QComboBox()
        self.pid_combo.setAccessibleName("Product ID of the virtual mouse")
        for pid in KNOWN_PRODUCT_IDS:
            suffix = " (default)" if pid == DEFAULT_PRODUCT_ID else ""
            self.pid_combo.addItem("0x%04X%s" % (pid, suffix), pid)
        self.pid_combo.setToolTip("USB product id the virtual mouse reports. "
                                  "Can only be changed while it is stopped.")
        self.pid_combo.setAccessibleDescription(self.pid_combo.toolTip())
        self.pid_label = pid_label = QtWidgets.QLabel("&Product ID:")
        pid_label.setBuddy(self.pid_combo)

        self.toggle_button = QtWidgets.QPushButton("&Start virtual mouse")
        self.toggle_button.setAccessibleName("Start or stop the virtual mouse")
        self.toggle_button.setToolTip(
            "Create or remove the virtual mouse. Start it before the game, so that "
            "the game finds it.")
        self.toggle_button.setAccessibleDescription(self.toggle_button.toolTip())
        self.toggle_button.clicked.connect(self.toggle)

        self.dot = StatusDot()
        self.status_label = QtWidgets.QLabel()
        self.status_label.setAccessibleName("Virtual mouse status")
        # A long status must not widen the window; the tooltip has it in full.
        self.status_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                        QtWidgets.QSizePolicy.Policy.Preferred)
        self.status_label.setMinimumWidth(120)
        self.message = MessageLabel("Virtual mouse message")

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(LOG_LINES)
        self.log_view.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        self.log_view.setFont(font)
        self.log_view.setAccessibleName("Command log of the virtual mouse")
        self.log_view.setAccessibleDescription(
            "Decoded commands the virtual mouse received. Lines that start with "
            "\">>> DIRECTIONAL WRITE\" carry the calibration values.")
        self.log_view.setPlaceholderText(
            "Nothing received yet.\n"
            "1. Start the virtual mouse, then the game.\n"
            "2. Run the per-direction calibration in the game.\n"
            "3. The commands it sends appear here (last %d lines); the calibration "
            "values are marked \">>> DIRECTIONAL WRITE\".\n"
            "Help > First steps has the whole procedure." % LOG_LINES)
        self.log_view.setMinimumHeight(48)
        self.log_view.setTabChangesFocus(True)
        self._highlighter = WriteHighlighter(self.log_view)
        self.log_view.verticalScrollBar().valueChanged.connect(self._on_scrolled)

        # The log is protocol detail: hidden until asked for, summarised meanwhile.
        self.log_box = QtWidgets.QWidget()
        self.summary_label = caption(QtWidgets.QLabel("No events yet."))
        self.summary_label.setAccessibleName("Last event of the virtual mouse")
        self.summary_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                         QtWidgets.QSizePolicy.Policy.Preferred)
        self.log_toggle = Disclosure("Sho&w protocol log", self.log_box)
        self.log_toggle.setToolTip(
            "Show every command the virtual mouse received. Not needed for normal use; "
            "the line next to this button names the last event that matters.")
        self.log_toggle.setAccessibleDescription(self.log_toggle.toolTip())

        self.follow_label = caption(QtWidgets.QLabel())
        self.follow_label.setAccessibleName("Log auto-scroll state")
        self.copy_button = QtWidgets.QPushButton("C&opy log")
        self.copy_button.setToolTip("Copy the whole log to the clipboard")
        self.copy_button.setAccessibleDescription(self.copy_button.toolTip())
        self.copy_button.clicked.connect(self.copy_log)
        self.clear_button = QtWidgets.QPushButton("&Clear")
        self.clear_button.setAccessibleName("Clear log")
        self.clear_button.setToolTip("Empty the log. The values under Calibration stay.")
        self.clear_button.setAccessibleDescription(self.clear_button.toolTip())
        self.clear_button.clicked.connect(self.clear_log)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(SPACE_M)
        controls.addWidget(self.toggle_button)
        controls.addWidget(self.dot)
        controls.addWidget(self.status_label, 1)
        controls.addWidget(pid_label)
        controls.addWidget(self.pid_combo)

        head = QtWidgets.QHBoxLayout()
        head.setSpacing(SPACE_M + SPACE_S)
        head.addWidget(heading("Virtual mouse"))
        head.addWidget(self.summary_label, 1)
        head.addWidget(self.log_toggle)

        log_bar = QtWidgets.QHBoxLayout()
        log_bar.setSpacing(SPACE_M)
        log_bar.addWidget(self.follow_label, 1)
        log_bar.addWidget(self.copy_button)
        log_bar.addWidget(self.clear_button)
        log_layout = QtWidgets.QVBoxLayout(self.log_box)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(SPACE_S)
        log_layout.addWidget(self.log_view, 1)
        log_layout.addLayout(log_bar)

        layout = self.card_layout()
        layout.addLayout(head)
        layout.addLayout(controls)
        layout.addWidget(self.message)
        layout.addWidget(self.log_box, 1)
        self.message.hide()
        if settings is not None:
            self.log_toggle.setChecked(bool(settings.value("ui/show_log", False, type=bool)))
            self.log_toggle.toggled.connect(
                lambda shown: settings.setValue("ui/show_log", shown))
        self.check_access()
        self._update_log_controls()

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

    def check_access(self) -> str:
        """Look at the uhid node again; the status line names a problem up front."""
        if self._uhid_access is not None:
            self._access = self._uhid_access()
        self._update_status()
        return self._access

    def access(self) -> str:
        """Result of the last check_access(): "ok", "missing" or "denied"."""
        return self._access

    def log_shown(self) -> bool:
        return bool(self.log_toggle.isChecked())

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
            self.check_access()
            return
        self._session = session
        for fd in session.fds:
            notifier = QtCore.QSocketNotifier(fd, QtCore.QSocketNotifier.Type.Read, self)
            notifier.activated.connect(lambda *_args, fd=fd: self._pump(fd))
            self._notifiers.append(notifier)
        self._update_status()
        self.running_changed.emit(True)

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
        if session is not None:
            self.running_changed.emit(False)

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
        lines = [format_entry(entry)]
        if "highlight" in entry:
            lines.append(WRITE_PREFIX + str(entry["highlight"]))
        self._append(lines)
        summary = summarize_entry(entry)
        if summary is not None:
            self.summary_label.setText("Last event: " + summary)
            self.summary_label.setToolTip(self.summary_label.text())
        if self.writes.feed(entry):
            self.writes_changed.emit()

    def _append(self, lines: list[str]) -> None:
        """Add lines; keep the view where it is unless it follows the end."""
        bar = self.log_view.verticalScrollBar()
        position = bar.value()
        before = 0 if self.log_view.document().isEmpty() else self.log_view.blockCount()
        self._appending = True
        try:
            for line in lines:
                self.log_view.appendPlainText(line)
            if self._follow:
                bar.setValue(bar.maximum())
            else:
                # The scroll bar counts lines; follow the text when old lines
                # were dropped at the top.
                dropped = before + len(lines) - self.log_view.blockCount()
                bar.setValue(max(0, position - max(0, dropped)))
        finally:
            self._appending = False
        self._update_log_controls()

    def _on_scrolled(self, value: int) -> None:
        if self._appending:
            return
        follow = value >= self.log_view.verticalScrollBar().maximum()
        if follow != self._follow:
            self._follow = follow
            self._update_log_controls()

    def follows_end(self) -> bool:
        """Whether new lines scroll the log (False after the user scrolled up)."""
        return self._follow

    def copy_log(self) -> None:
        text = self.log_view.toPlainText()
        QtWidgets.QApplication.clipboard().setText(text)
        count = len(text.splitlines())
        self.follow_label.setText("Copied %d line%s" % (count, "" if count == 1 else "s"))

    def clear_log(self) -> None:
        self.log_view.clear()
        self._follow = True
        self._update_log_controls()

    def _update_log_controls(self) -> None:
        has_text = not self.log_view.document().isEmpty()
        self.copy_button.setEnabled(has_text)
        self.clear_button.setEnabled(has_text)
        if not has_text:
            self.follow_label.setText("")
        elif self._follow:
            self.follow_label.setText("Auto-scroll: on")
        else:
            self.follow_label.setText("Auto-scroll: paused (scroll to the end to resume)")

    def changeEvent(self, event: QtCore.QEvent) -> None:  # noqa: N802 (Qt name)
        super().changeEvent(event)
        if event.type() in (QtCore.QEvent.Type.PaletteChange,
                            QtCore.QEvent.Type.ApplicationPaletteChange):
            highlighter = getattr(self, "_highlighter", None)
            if highlighter is not None:
                highlighter.rehighlight()

    # ---- presentation -----------------------------------------------------

    def _show_message(self, text: str, error: bool = False) -> None:
        self.message.show_message(text, error)
        self.message.setVisible(bool(text))

    def _update_status(self) -> None:
        session = self._session
        running = session is not None and session.running
        self.pid_combo.setEnabled(not running)
        self.toggle_button.setText("&Stop virtual mouse" if running else "&Start virtual mouse")
        self.toggle_button.setEnabled(running or self._access == UHID_OK)
        if not running or session is None:
            self.dot.set_state(StatusDot.OFF)
            problem = uhid_short(self._access)
            self.status_label.setText("Stopped: " + problem if problem else "Stopped")
            self.status_label.setToolTip(self.status_label.text())
        elif session.open_count > 0:
            self.dot.set_state(StatusDot.ACTIVE)
            self.status_label.setText("Running: device opened by a client (%04X:%04X)"
                                      % (VENDOR_ID, session.product_id))
        else:
            self.dot.set_state(StatusDot.WAITING)
            self.status_label.setText("Running: waiting for a client (%04X:%04X)"
                                      % (VENDOR_ID, session.product_id))
        self.status_label.setToolTip(self.status_label.text())
