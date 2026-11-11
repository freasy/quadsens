"""The virtual mouse on Windows: USB/IP server plus the attach through usbip-win2 (no Qt).

Starting means: serve the device on this PC, then ask usbip-win2 to attach
it; one call, no further action by the user. Stopping means: detach the port
this application attached, and only that, then stop serving. The calls to
usbip.exe can take a moment, so they run in a worker thread; the window asks
poll() for what happened.

If the attach fails or usbip.exe is not found, the server keeps serving, and
an attach made elsewhere (the USBip app) is noticed, because the server sees
the client connect. Such an attach is not ours to undo: stopping then only
ends the connection, and the device disappears on the Windows side.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from . import usbip_client
from .usbip_client import UsbipClient
from .usbip_server import ServerError, VirtualMouseServer

STOPPED, SERVING, ATTACHED, IN_USE, ERROR = (
    "stopped", "serving", "attached", "in use", "error")
STATE_TEXT = {
    STOPPED: "Stopped",
    SERVING: "Serving on 127.0.0.1:%d, waiting for attach",
    ATTACHED: "Attached: Windows sees the device",
    IN_USE: "In use: a program is talking to the device",
    ERROR: "Stopped after an error",
}


EXTERNAL_STOP = ("Stopped serving. The device was attached outside QuadSens, so nothing "
                 "was detached; it disappears on the Windows side now.")


class VirtualMouse:
    def __init__(self, client: UsbipClient,
                 server: VirtualMouseServer | None = None) -> None:
        self.client = client
        self.server = server or VirtualMouseServer(port=client.tcp_port)
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._notes: list[dict[str, Any]] = []
        self.message = ""          # last thing worth telling the user
        self.problem = ""          # usbip_client kind of the last failed attach, or ""
        self.port = 0              # USB/IP hub port our device is attached to
        self.ours = False          # this application attached it, so it detaches it
        self.traffic = False       # the device answered a command since it was attached
        self.started_at: float | None = None
        self.failed = False

    # ---- state ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.server.running

    @property
    def busy(self) -> bool:
        """True while usbip.exe is being run."""
        return self._worker is not None and self._worker.is_alive()

    @property
    def state(self) -> str:
        if not self.server.running:
            return ERROR if self.failed else STOPPED
        if self.server.attached:
            return IN_USE if self.traffic else ATTACHED
        return SERVING

    @property
    def external(self) -> bool:
        """Attached, but not by this application (for example with the USBip app)."""
        return self.server.attached and not self.ours and not self.busy

    def state_text(self) -> str:
        text = STATE_TEXT[self.state]
        return text % self.server.port if "%d" in text else text

    def attach_command_text(self) -> str:
        if not self.client.installed:
            return ""
        return usbip_client.command_line(self.client.attach_command())

    def _note(self, message: str, problem: str = "", show: bool = False) -> None:
        """Record what happened; ``show`` asks the window to display it although
        nothing went wrong."""
        with self._lock:
            self.message, self.problem = message, problem
            self._notes.append({"event": "mouse", "message": message, "problem": problem,
                                "show": show, "t": round(time.time(), 3)})

    # ---- start and stop ----------------------------------------------------------------

    def start(self) -> bool:
        """Serve the device and, in the background, attach it. False if serving failed."""
        if self.running:
            return True
        self.failed = False
        self.traffic = False
        self.port = 0
        self.ours = False
        try:
            self.server.start()
        except ServerError as exc:
            self.failed = True
            self._note(str(exc), exc.kind)
            return False
        self.client.tcp_port = self.server.port
        self.started_at = time.time()
        self._note("Serving the virtual mouse on this PC.")
        self._run(self._attach)
        return True

    def _run(self, work: Callable[[], None]) -> None:
        self._worker = threading.Thread(target=work, name="usbip-client", daemon=True)
        self._worker.start()

    def _attach(self) -> None:
        outcome = self.client.attach()
        if outcome.ok:
            self.port, self.ours = outcome.port, True
            self._note(outcome.message)
        else:
            self._note(outcome.message, outcome.kind)

    def attach_again(self) -> None:
        """Try the attach once more (after the user fixed something)."""
        if self.running and not self.busy and not self.server.attached:
            self._run(self._attach)

    def stop(self, wait: bool = False) -> None:
        """Detach our port and stop serving. With ``wait`` it returns when that is done."""
        if not self.running and not self.busy:
            return
        worker = self._worker

        def work() -> None:
            show = False
            if worker is not None and worker.is_alive():
                worker.join(5)
            if self.ours:
                # Ours: detach that port, and nothing else.
                outcome = self.client.detach(self.port)
                text = "Stopped." if outcome.ok or not outcome.message \
                    else "Stopped. " + outcome.message
                problem = "" if outcome.ok else outcome.kind
            elif self.server.attached:
                # Attached elsewhere: not ours to detach. Ending the connection
                # makes the device disappear on the Windows side.
                text, problem, show = EXTERNAL_STOP, "", True
            else:
                text, problem = "Stopped.", ""
            self.server.stop()
            self.port, self.ours = 0, False
            self.traffic = False
            self._note(text, problem, show)
        self._worker = threading.Thread(target=work, name="usbip-client", daemon=True)
        self._worker.start()
        if wait:
            self._worker.join(15)

    def close(self) -> None:
        """For the end of the application: detach and stop, and wait for both."""
        self.stop(wait=True)

    # ---- events -----------------------------------------------------------------------

    def poll(self) -> list[dict[str, Any]]:
        """What happened since the last call: server entries and notes, oldest first."""
        entries = self.server.poll()
        for entry in entries:
            if entry.get("event") == "client":
                if entry.get("state") == "detached":
                    self.traffic = False
                elif not self.ours and not self.busy:
                    self._note("Attached from outside QuadSens.")
            elif "cmd" in entry and self.server.attached:
                self.traffic = True
        with self._lock:
            notes, self._notes = self._notes, []
        return sorted(entries + notes, key=lambda entry: entry.get("t", 0))
