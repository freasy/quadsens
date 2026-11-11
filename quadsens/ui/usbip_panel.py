"""The "Virtual mouse" card on Windows, and the check for the USB/IP driver.

The card is the Linux one (status, collapsed protocol log, last-event line,
Copy and Clear) with another transport underneath: instead of /dev/uhid it
starts the USB/IP server and asks usbip-win2 to attach the device.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .. import usbip_client, usbip_mouse
from ..emulator import VENDOR_ID
from ..usbip_mouse import VirtualMouse
from ..usbip_server import format_event
from .mouse_panel import MousePanel
from .qt import QtCore, QtWidgets, Signal
from .status import SPACE_M, SPACE_S, StatusDot, WrapLabel, caption
from .theme import set_primary

MouseFactory = Callable[[int], VirtualMouse]
POLL_MS = 100
# Entries of the protocol handler go through the shared log code of the card.
_HANDLER_EVENTS = {"warning", "short_report"}


class UsbipMousePanel(MousePanel):
    """Start: serve and attach. Stop: detach our port and stop serving."""

    def __init__(self, mouse_factory: MouseFactory,
                 settings: QtCore.QSettings | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        self._mouse_factory = mouse_factory
        self._mouse: VirtualMouse | None = None
        self._last_state = usbip_mouse.STOPPED
        super().__init__(lambda pid, on_log: None, None, settings, parent)  # type: ignore
        self.pid_label.setText("Product ID:")   # no mnemonic: P belongs to the profile box
        self.toggle_button.setToolTip(
            "Serve the virtual mouse on this PC and attach it through usbip-win2, in one "
            "step. Start it before the game, so that the game finds it.")
        self.toggle_button.setAccessibleDescription(self.toggle_button.toolTip())
        self.log_view.setPlaceholderText(
            "Nothing received yet. Start the virtual mouse, then the game; the "
            "commands it sends appear here, and the calibration values are marked "
            "\">>> DIRECTIONAL WRITE\".")
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.poll)
        self._update_status()

    # ---- start / stop ---------------------------------------------------------------

    def mouse(self) -> VirtualMouse | None:
        return self._mouse

    def is_running(self) -> bool:
        return self._mouse is not None and self._mouse.running

    def started_at(self) -> float | None:
        return self._mouse.started_at if self._mouse is not None else None

    def check_access(self) -> str:
        self._update_status()
        return "ok"

    def start(self) -> None:
        if self.is_running():
            return
        self._show_message("")
        self._mouse = self._mouse_factory(self.product_id())
        started = self._mouse.start()
        self._timer.start()
        self.poll()
        if started:
            self.running_changed.emit(True)

    def stop(self, reason: str = "") -> None:
        """Detach our port and stop serving; returns when that is done."""
        mouse = self._mouse
        if mouse is None:
            return
        was_running = mouse.running
        mouse.close()
        self.poll()
        self._timer.stop()
        if reason:
            self._show_message(reason, error=True)
        self._update_status()
        if was_running:
            self.running_changed.emit(False)

    # ---- events ------------------------------------------------------------------------

    def poll(self) -> None:
        """Take what the server and the attach thread reported, and show it."""
        mouse = self._mouse
        if mouse is None:
            return
        for entry in mouse.poll():
            self._show_entry(entry)
        self._update_status()
        if mouse.state != self._last_state:
            self._last_state = mouse.state
            self.running_changed.emit(mouse.running)

    def _show_entry(self, entry: dict[str, Any]) -> None:
        event = entry.get("event")
        if "cmd" in entry or event in _HANDLER_EVENTS:
            self._on_entry(entry)          # log line, summary, directional values
            return
        self._append([format_event(entry)])
        stamp = format_event(entry).split(" ", 1)[0]
        if event == "client":
            self.summary_label.setText("Last event: %s Windows %s the device" % (
                stamp, "attached" if entry.get("state") == "attached" else "detached"))
        elif event == "mouse":
            problem = str(entry.get("problem") or "")
            message = str(entry.get("message") or "")
            if problem:
                self._show_message(message, error=problem not in (
                    usbip_client.NOT_INSTALLED,))
            else:
                # An earlier refusal is settled; a few things are worth saying anyway.
                self._show_message(message if entry.get("show") else "")
            self.summary_label.setText("Last event: %s %s" % (stamp, message[:80]))

    # ---- presentation ------------------------------------------------------------------

    def _update_status(self) -> None:
        mouse = self._mouse
        state = mouse.state if mouse is not None else usbip_mouse.STOPPED
        running = mouse is not None and mouse.running
        busy = mouse is not None and mouse.busy
        self.pid_combo.setEnabled(not running)
        self.toggle_button.setEnabled(not busy or running)
        self.toggle_button.setText("&Stop virtual mouse" if running
                                   else "&Start virtual mouse")
        text = mouse.state_text() if mouse is not None else usbip_mouse.STATE_TEXT[state]
        if state in (usbip_mouse.ATTACHED, usbip_mouse.IN_USE):
            self.dot.set_state(StatusDot.ACTIVE if state == usbip_mouse.IN_USE
                               else StatusDot.WAITING)
            assert mouse is not None
            if mouse.port:
                text += " (USB/IP port %d, %04X:%04X)" % (mouse.port, VENDOR_ID,
                                                          self.product_id())
            elif mouse.external:
                text += " (attached outside QuadSens)"
        elif state == usbip_mouse.SERVING:
            self.dot.set_state(StatusDot.WAITING)
            if busy:
                text = "Serving on 127.0.0.1:%d, attaching ..." % mouse.server.port  # type: ignore
        else:
            self.dot.set_state(StatusDot.OFF)
        self.status_label.setText(text)
        self.status_label.setToolTip(text)


class UsbipCheckStrip(QtWidgets.QFrame):
    """First line of the Windows window: is the USB/IP driver there?

    One quiet line when it is. Otherwise it says that usbip-win2 is missing,
    what its installer does, that importing or typing values works without it,
    and offers a button that opens its releases page in the browser. QuadSens
    does not download or install it.
    """

    open_releases = Signal()
    checked = Signal()

    def __init__(self, find_client: Callable[[], usbip_client.UsbipClient],
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._find_client = find_client
        self.found = False
        self.version = ""
        self.mark = QtWidgets.QLabel()
        self.mark.setObjectName("bannerMark")
        font = self.mark.font()
        font.setBold(True)
        self.mark.setFont(font)
        self.mark.setFixedWidth(self.mark.fontMetrics().horizontalAdvance("⚠") + 4)
        self.mark.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.headline = QtWidgets.QLabel()
        bold = self.headline.font()
        bold.setWeight(bold.Weight.DemiBold)
        self.headline.setFont(bold)
        self.detail = caption(WrapLabel("About the USB/IP driver"))
        self.get_button = QtWidgets.QPushButton("Get usbip-win&2…")
        self.get_button.setToolTip(
            "Open the download page of usbip-win2 in your browser. You install it "
            "yourself; its installer needs administrator rights.")
        self.get_button.setAccessibleDescription(self.get_button.toolTip())
        self.get_button.clicked.connect(self.open_releases.emit)
        self.recheck_button = QtWidgets.QToolButton()
        self.recheck_button.setText("Chec&k again")
        self.recheck_button.setAutoRaise(True)
        self.recheck_button.setFocusPolicy(QtCore.Qt.FocusPolicy.TabFocus)
        self.recheck_button.setAccessibleName("Check for the USB/IP driver again")
        self.recheck_button.setToolTip("Look for usbip-win2 again")
        self.recheck_button.setAccessibleDescription(self.recheck_button.toolTip())
        self.recheck_button.clicked.connect(self.check)
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(SPACE_M)
        top.addWidget(self.mark)
        top.addWidget(self.headline, 1)
        top.addWidget(self.get_button)
        top.addWidget(self.recheck_button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addWidget(self.detail)
        self.setProperty("banner", "")
        self.check()

    def check(self) -> None:
        client = self._find_client()
        self.found = client.installed
        self.version = client.version() if client.installed else ""
        if self.found:
            self.mark.setText("✓")
            self.headline.setText("USB/IP driver found (usbip-win2%s)"
                                  % (" " + self.version if self.version else ""))
            self.detail.setText("")
        else:
            self.mark.setText("✕")
            self.headline.setText("usbip-win2 is not installed")
            self.detail.setText(
                "The virtual mouse needs this USB/IP driver (its usbip.exe was not found). "
                "You install it yourself; its installer asks for administrator rights and "
                "restarts the USB hubs for a moment. Without it you can still import or "
                "type the values and apply them.")
        self.detail.setVisible(not self.found)
        self.get_button.setVisible(not self.found)
        set_primary(self.get_button, False)
        self.layout().setContentsMargins(*((SPACE_S, 0, 0, 0) if self.found
                                           else (SPACE_M, 3, SPACE_M, 4)))
        self.setAccessibleName("USB/IP driver check: %s. %s %s" % (
            "passed" if self.found else "needs attention", self.headline.text(),
            self.detail.text()))
        banner = "" if self.found else "warning"
        if self.property("banner") != banner:
            self.setProperty("banner", banner)
            style = self.style()
            style.unpolish(self)
            style.polish(self)
            self.update()
        self.checked.emit()
