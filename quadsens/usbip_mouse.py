"""The virtual mouse on Windows: USB/IP server plus the attach through usbip-win2 (no Qt).

Starting means: serve the device on this PC, then ask usbip-win2 to attach
it. Stopping means: detach our port, and only ours, then stop serving. The
calls to usbip.exe can take a moment, so they run in a worker thread; the
window asks poll() for what happened.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from . import usbip_client
from .usbip_client import UsbipClient
from .usbip_server import ServerError, VirtualMouseServer

STOPPED, SERVING, ATTACHED, IN_USE, ERROR = (
    "stopped", "serving", "attached", "in use", "error")
STATE_TEXT = {
    STOPPED: "Stopped",
    SERVING: "Serving on this PC, not attached",
    ATTACHED: "Attached: Windows sees the device",
    IN_USE: "In use: a program is talking to the device",
    ERROR: "Stopped after an error",
}


class VirtualMouse:
    def __init__(self, client: UsbipClient, server: VirtualMouseServer | None = None,
                 elevate: Callable[[list[str]], bool] = usbip_client.run_elevated,
                 environ: Mapping[str, str] | None = None) -> None:
        self.client = client
        self.server = server or VirtualMouseServer(port=client.tcp_port)
        self._elevate = elevate
        self._environ = environ
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._notes: list[dict[str, Any]] = []
        self.message = ""          # last thing worth telling the user
        self.problem = ""          # usbip_client kind of the last failed attach, or ""
        self.port = 0              # USB/IP hub port our device is attached to
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

    def can_elevate(self) -> bool:
        """Whether "Run as administrator" may be offered for the last refusal."""
        return (self.problem == usbip_client.DENIED and self.running
                and usbip_client.can_offer_elevation(self.client.tool, self._environ))

    def attach_command_text(self) -> str:
        if not self.client.installed:
            return ""
        return usbip_client.command_line(self.client.attach_command())

    def _note(self, message: str, problem: str = "") -> None:
        with self._lock:
            self.message, self.problem = message, problem
            self._notes.append({"event": "mouse", "message": message, "problem": problem,
                                "t": round(time.time(), 3)})

    # ---- start and stop ----------------------------------------------------------------

    def start(self) -> bool:
        """Serve the device and, in the background, attach it. False if serving failed."""
        if self.running:
            return True
        self.failed = False
        self.traffic = False
        self.port = 0
        try:
            self.server.start()
        except ServerError as exc:
            self.failed = True
            self._note(str(exc), "server")
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
            self.port = outcome.port
            self._note(outcome.message)
        else:
            self._note(outcome.message, outcome.kind)

    def attach_again(self) -> None:
        """Try the attach once more (after the user fixed something)."""
        if self.running and not self.busy and not self.server.attached:
            self._run(self._attach)

    def attach_elevated(self) -> bool:
        """Run the attach through the Windows administrator prompt, if that is allowed.

        Only for a usbip.exe in a protected system folder, and only on request.
        """
        if not self.can_elevate() or self.busy:
            return False
        if not self._elevate(self.client.attach_command()):
            self._note("The administrator prompt was declined or could not be shown. "
                       "Run this in a console opened as administrator: "
                       + self.attach_command_text(), usbip_client.DENIED)
            return False
        self._note("Asked Windows to run the attach as administrator ...")

        def wait() -> None:
            for _ in range(20):
                time.sleep(0.25)
                if self.server.attached:
                    self.port = self.client.our_port()
                    self._note("Attached to USB/IP port %d." % self.port if self.port
                               else "Attached.")
                    return
            self._note("The device did not get attached. Run this in a console opened as "
                       "administrator: " + self.attach_command_text(), usbip_client.DENIED)
        self._run(wait)
        return True

    def stop(self, wait: bool = False) -> None:
        """Detach our port and stop serving. With ``wait`` it returns when that is done."""
        if not self.running and not self.busy:
            return
        worker = self._worker

        def work() -> None:
            if worker is not None and worker.is_alive():
                worker.join(5)
            outcome = self.client.detach(self.port)
            self.server.stop()
            self.port = 0
            self.traffic = False
            self._note("Stopped." if outcome.ok or not outcome.message
                       else "Stopped. " + outcome.message,
                       "" if outcome.ok or outcome.kind == usbip_client.NOT_INSTALLED
                       else outcome.kind)
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
            elif "cmd" in entry and self.server.attached:
                self.traffic = True
        with self._lock:
            notes, self._notes = self._notes, []
        return sorted(entries + notes, key=lambda entry: entry.get("t", 0))
