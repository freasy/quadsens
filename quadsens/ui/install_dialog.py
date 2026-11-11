"""The dialog that installs usbip-win2 or RawAccel, after saying what it will do.

Nothing starts before the user presses the button. The work (download,
checks, unpacking, waiting for the installer) runs in a thread; the dialog
shows where it is and can cancel the download.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable

from .. import win_install
from ..win_install import Result
from ..win_tools import PINS, RAWACCEL, USBIP
from .qt import QtCore, QtWidgets, Signal
from .status import SPACE_L, SPACE_M, MessageLabel, WrapLabel, caption, heading

POLL_MS = 100
# The flow to run: (key, folder, say, progress, cancelled) -> Result.
Flow = Callable[[str, str, Callable[[str], None], Callable[[int, int], None],
                 Callable[[], bool]], Result]
TITLES = {USBIP: "Install the USB/IP driver (usbip-win2)", RAWACCEL: "Install RawAccel"}


class ToolInstallDialog(QtWidgets.QDialog):
    finished_install = Signal(object)        # the Result

    def __init__(self, key: str, flow: Flow, folder: str = "",
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self._flow = flow
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.result_value: Result | None = None
        pin = PINS[key]
        self.setWindowTitle(TITLES[key])
        self.setModal(True)

        title = heading(TITLES[key])
        self.what_label = WrapLabel("What will happen")
        self.what_label.setText("\n".join(
            "•  " + line for line in win_install.confirm_text(key)))
        self.source_label = caption(WrapLabel("Where the file comes from"))
        self.source_label.setText(
            "File: %s, %d bytes, SHA-256 %s. From %s (license of the tool: %s). You can "
            "also install it yourself from there; then press Check again in the main "
            "window." % (pin.asset, pin.size, pin.sha256, pin.release_url, pin.license))

        self.folder_edit = QtWidgets.QLineEdit(folder)
        self.folder_edit.setAccessibleName("Folder to unpack RawAccel into")
        self.folder_edit.setToolTip(
            "RawAccel is unpacked here and stays here. The folder must be yours to write "
            "to, because RawAccel keeps its settings.json in it.")
        self.folder_edit.setAccessibleDescription(self.folder_edit.toolTip())
        self.folder_label = QtWidgets.QLabel("Unpack &into:")
        self.folder_label.setBuddy(self.folder_edit)
        self.browse_button = QtWidgets.QPushButton("&Browse…")
        self.browse_button.setToolTip("Choose another folder for RawAccel")
        self.browse_button.setAccessibleDescription(self.browse_button.toolTip())
        self.browse_button.clicked.connect(self.browse)
        folder_row = QtWidgets.QHBoxLayout()
        folder_row.setSpacing(SPACE_M)
        folder_row.addWidget(self.folder_label)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.browse_button)
        for widget in (self.folder_label, self.folder_edit, self.browse_button):
            widget.setVisible(key == RAWACCEL)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setAccessibleName("Download progress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.status_label = WrapLabel("What is happening now")
        self.status_label.setText("Nothing has been downloaded or started yet.")
        self.message = MessageLabel("Result of the installation")
        self.message.hide()

        self.start_button = QtWidgets.QPushButton("&Download and install")
        self.start_button.setProperty("primary", True)
        self.start_button.setToolTip("Do what is described above")
        self.start_button.setAccessibleDescription(self.start_button.toolTip())
        self.start_button.clicked.connect(self.start)
        self.cancel_button = QtWidgets.QPushButton("&Cancel")
        self.cancel_button.setToolTip(
            "Before the start: close without doing anything. While downloading: stop the "
            "download.")
        self.cancel_button.setAccessibleDescription(self.cancel_button.toolTip())
        self.cancel_button.clicked.connect(self.cancel)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        layout.setSpacing(SPACE_M)
        layout.addWidget(title)
        layout.addWidget(self.what_label)
        layout.addWidget(self.source_label)
        layout.addLayout(folder_row)
        layout.addWidget(self.progress)
        layout.addWidget(self.status_label)
        layout.addWidget(self.message)
        layout.addStretch(1)
        layout.addLayout(buttons)
        self.resize(620, 460)
        self.setMinimumWidth(480)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.poll)

    # ---- actions ------------------------------------------------------------------

    def is_busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def browse(self) -> None:
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Folder for RawAccel", self.folder_edit.text())
        if chosen:
            self.folder_edit.setText(QtCore.QDir.toNativeSeparators(chosen))

    def start(self) -> None:
        if self.is_busy():
            return
        folder = self.folder_edit.text().strip()
        if self.key == RAWACCEL:
            reason = win_install.folder_usable(folder) if folder else "Give a folder."
            if reason:
                self.message.show_message(reason, error=True)
                self.message.show()
                return
        self.message.hide()
        self.result_value = None
        self._cancel.clear()
        self.start_button.setEnabled(False)
        self.folder_edit.setEnabled(False)
        self.browse_button.setEnabled(False)
        self.cancel_button.setText("&Cancel download")

        def work() -> None:
            try:
                result = self._flow(
                    self.key, folder,
                    lambda text: self._events.put(("say", text)),
                    lambda done, total: self._events.put(("progress", (done, total))),
                    self._cancel.is_set)
            except Exception as exc:    # noqa: BLE001 - shown to the user, never lost
                result = Result(win_install.FAILED, "Unexpected error: %s. Nothing more "
                                                    "was done." % exc)
            self._events.put(("result", result))
        self._thread = threading.Thread(target=work, name="tool-install", daemon=True)
        self._thread.start()
        self._timer.start()

    def cancel(self) -> None:
        if self.is_busy():
            self._cancel.set()
            self.status_label.setText("Cancelling ...")
        else:
            self.reject()

    def poll(self) -> None:
        while True:
            try:
                kind, value = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "say":
                self.status_label.setText(str(value))
                # Once an installer runs there is nothing left to cancel here.
                self.cancel_button.setEnabled("Downloading" in str(value))
            elif kind == "progress":
                done, total = value            # type: ignore[misc]
                self.progress.setValue(int(done * 100 / total) if total else 0)
            else:
                self._finish(value)            # type: ignore[arg-type]

    def _finish(self, result: Result) -> None:
        self._timer.stop()
        self.result_value = result
        done = result.kind == win_install.DONE
        self.status_label.setText("Finished." if done else "Not installed.")
        self.message.show_message(("✓ " if done else "") + result.message,
                                  error=result.kind == win_install.FAILED)
        self.message.show()
        self.start_button.setEnabled(not done)
        self.start_button.setText("Try &again" if not done else "&Download and install")
        self.folder_edit.setEnabled(not done)
        self.browse_button.setEnabled(not done)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("&Close")
        self.finished_install.emit(result)

    def wait(self, seconds: float = 30.0) -> Result | None:
        """For tests: let the flow finish and take in what it reported."""
        if self._thread is not None:
            self._thread.join(seconds)
        self.poll()
        return self.result_value

    def reject(self) -> None:
        if self.is_busy():
            self._cancel.set()          # a download stops; a running installer is not ours to stop
            return
        super().reject()
