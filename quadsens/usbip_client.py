"""Drive usbip-win2, the USB/IP client driver for Windows (no Qt).

usbip-win2 (https://github.com/vadimgrn/usbip-win2) is installed by the user,
by hand or through the install dialog of QuadSens (quadsens.win_install).
Its command line tool usbip.exe attaches the device that
quadsens.usbip_server exports.

Confirmed in the usbip-win2 sources and README (userspace/usbip/usbip.cpp,
attach.cpp, detach.cpp, port.cpp, README.md; master branch):

* Commands: "usbip attach -r HOST -b BUSID", "usbip detach -p PORT" (or
  "-a" for all), "usbip list -r HOST", "usbip port", "usbip --version".
  A global "-t PORT" names another TCP port than 3240.
* "attach --terse" prints only the port number; without it the line is
  "successfully attached to port N". "attach --once" does not start automatic
  attach attempts if the server cannot be reached. "attach -r .. -b .. --stop"
  stops such attempts.
* "detach -p N" prints "port N is successfully detached".
* "usbip port" prints, per device, "Port NN: device in use at <speed>", the
  product, and a line "-> usbip://HOST:PORT/BUSID".
* A failed command exits with status 1 and prints the Windows error text.
* The README's uninstall instructions name C:\\Program Files\\USBip as the
  install folder.

* usbip.exe and the USBip app (wusbip.exe) both link libusbip and attach
  through the same calls: vhci::open(), a CreateFile on the device interface
  GUID_DEVINTERFACE_USBIP_VHCI, then vhci::attach(). Neither project file
  asks for an execution level. Whatever rights the app needs, usbip.exe
  needs the same.
* The installer (userspace/innosetup/setup.iss) needs administrator rights,
  installs into {autopf}\<product name> and always installs usbip.exe (the
  components "main" and "client" are fixed; the app is optional). Nothing in
  it adds the folder to PATH, so the look in Program Files is what finds it.

Verified on one Windows 11 VM: attaching this device without elevation
through the usbip-win2 app. The automatic attach through usbip.exe from
QuadSens is not yet verified on Windows.

Assumed, not confirmed: the exact error texts for "driver not installed" and
"access denied" (they are Windows system messages and depend on the system
language, so they are matched loosely).

QuadSens never asks for elevation. When the attach fails or usbip.exe is not
found, the message names the fallback: attach with the USBip app to
127.0.0.1; the server keeps serving and notices an attach made elsewhere.

The command runner is injected, so everything here is tested on Linux.
"""

from __future__ import annotations

import ntpath
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

RELEASES_URL = "https://github.com/vadimgrn/usbip-win2/releases"
TOOL = "usbip"
INSTALL_FOLDER = "USBip"
TIMEOUT = 20.0
ATTACH_TRIES = 3          # the first try can be too early for the server
RETRY_PAUSE = 0.4
MANUAL_STEPS = ("open the USBip app that comes with usbip-win2, enter 127.0.0.1 and attach "
                "the device there; QuadSens keeps serving and notices it.")
MANUAL = "As a fallback, " + MANUAL_STEPS

# How a command ended.
OK, NOT_INSTALLED, DRIVER_MISSING, DENIED, UNREACHABLE, ALREADY, FAILED, TIMED_OUT = (
    "ok", "not installed", "driver missing", "denied", "unreachable", "already", "failed",
    "timed out")

Runner = Callable[[list[str], float], "tuple[int, str]"]


def run_command(argv: list[str], timeout: float = TIMEOUT) -> tuple[int, str]:
    """Run a command and return (exit status, output). Never raises."""
    flags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW: no console flashes up
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False,
                                timeout=timeout, creationflags=flags,
                                stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, "the command did not finish in time"
    except OSError as exc:
        return 127, str(exc)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def find_tool(environ: Mapping[str, str] | None = None,
              which: Callable[[str], str | None] = shutil.which,
              exists: Callable[[str], bool] = os.path.isfile) -> str | None:
    """Path of usbip.exe: on PATH, else in the default install folder."""
    environ = os.environ if environ is None else environ
    found = which(TOOL)
    if found:
        return found
    for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = environ.get(variable)
        if base:
            candidate = ntpath.join(base, INSTALL_FOLDER, "usbip.exe")
            if exists(candidate):
                return candidate
    return None


def classify(output: str) -> str:
    """Put an error text of usbip.exe into one of the kinds above."""
    text = output.lower()
    if re.search(r"access is denied|access denied|zugriff verweigert|privilege|elevat", text):
        return DENIED
    if re.search(r"already|in use|busy", text):
        return ALREADY
    if re.search(r"refused|unreachable|timed out|timeout|no connection|connect", text):
        return UNREACHABLE
    if re.search(r"cannot find the file|not found|no such device|cannot find the device|"
                 r"driver|vhci", text):
        return DRIVER_MISSING
    return FAILED


@dataclass(frozen=True)
class Outcome:
    kind: str
    message: str         # for the user, plain words
    port: int = 0        # hub port of our device, when known
    command: str = ""    # the command line that was run

    @property
    def ok(self) -> bool:
        return self.kind == OK


def command_line(argv: list[str]) -> str:
    """A command as the user would type it."""
    return " ".join('"%s"' % part if " " in part else part for part in argv)


def explain(kind: str, command: str, output: str = "") -> str:
    """What to tell the user about a failed attach or detach."""
    detail = " ".join(output.split())
    detail = f" ({detail[:200]})" if detail else ""
    if kind == NOT_INSTALLED:
        return ("usbip.exe of usbip-win2 was not found, so QuadSens cannot attach the "
                "virtual mouse itself. If usbip-win2 is not installed, the values can "
                "still be imported or typed. If it is installed, " + MANUAL_STEPS)
    if kind == DRIVER_MISSING:
        return ("usbip.exe is there, but its driver did not answer" + detail + ". Install "
                "usbip-win2 again (its installer needs administrator rights). " + MANUAL)
    if kind == DENIED:
        return ("Windows refused the attach" + detail + ". Open a console as "
                "administrator and run: " + command + " " + MANUAL)
    if kind == UNREACHABLE:
        return ("usbip.exe could not reach the virtual mouse on this PC" + detail +
                ". A firewall may be blocking the port on 127.0.0.1. " + MANUAL)
    if kind == ALREADY:
        return "The device is attached already" + detail + "."
    if kind == TIMED_OUT:
        return ("usbip.exe did not answer in time, so the device is not attached. "
                "Stop and start the virtual mouse to try again. " + MANUAL)
    return ("usbip.exe failed" + detail + ". The command was: " + command + " " + MANUAL)


PORT_LINE = re.compile(r"^\s*Port\s+(\d+)\s*:", re.IGNORECASE)
URL_LINE = re.compile(r"usbip://([^:/\s]+):(\d+)/(\S+)")


def parse_ports(output: str) -> list[tuple[int, str, int, str]]:
    """(port, host, tcp port, busid) of every imported device in "usbip port" output."""
    found = []
    current = 0
    for line in output.splitlines():
        match = PORT_LINE.match(line)
        if match:
            current = int(match.group(1))
            continue
        url = URL_LINE.search(line)
        if url and current:
            found.append((current, url.group(1).lower(), int(url.group(2)), url.group(3)))
            current = 0
    return found


class UsbipClient:
    """usbip.exe, for the one device this application exports."""

    def __init__(self, tool: str | None, runner: Runner = run_command,
                 host: str = "127.0.0.1", tcp_port: int = 3240, busid: str = "1-1",
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.sleep = sleep
        self.tool = tool
        self.runner = runner
        self.host = host
        self.tcp_port = tcp_port
        self.busid = busid

    @property
    def installed(self) -> bool:
        return self.tool is not None

    def _argv(self, *args: str) -> list[str]:
        assert self.tool is not None
        base = [self.tool]
        if self.tcp_port != 3240:
            base += ["-t", str(self.tcp_port)]
        return [*base, *args]

    def attach_command(self) -> list[str]:
        """The attach command, also shown to the user when it has to be run by hand."""
        return self._argv("attach", "-r", self.host, "-b", self.busid, "--once", "--terse")

    def version(self) -> str:
        """Version of the installed tool, "" if it cannot be told."""
        if self.tool is None:
            return ""
        code, output = self.runner(self._argv("--version"), 5.0)
        match = re.search(r"\d+(?:\.\d+)+", output)
        return match.group(0) if code == 0 and match else ""

    def our_port(self) -> int:
        """Hub port our device is attached to, 0 if it is not attached."""
        if self.tool is None:
            return 0
        code, output = self.runner(self._argv("port"), TIMEOUT)
        if code != 0:
            return 0
        hosts = {self.host.lower(), "localhost"} if self.host == "127.0.0.1" \
            else {self.host.lower()}
        for port, host, tcp_port, busid in parse_ports(output):
            if host in hosts and tcp_port == self.tcp_port and busid == self.busid:
                return port
        return 0

    def attach(self) -> Outcome:
        """Attach our device. Never asks for elevation."""
        if self.tool is None:
            return Outcome(NOT_INSTALLED, explain(NOT_INSTALLED, ""))
        argv = self.attach_command()
        command = command_line(argv)
        for attempt in range(ATTACH_TRIES):
            code, output = self.runner(argv, TIMEOUT)
            # Only "nobody listens yet" is worth another try.
            if code == 0 or code == 124 or classify(output) != UNREACHABLE:
                break
            if attempt + 1 < ATTACH_TRIES:
                self.sleep(RETRY_PAUSE)
        if code == 124:
            return Outcome(TIMED_OUT, explain(TIMED_OUT, command), 0, command)
        if code == 0:
            match = re.search(r"(\d+)\s*$", output.strip()) or re.search(r"port\s+(\d+)", output)
            port = int(match.group(1)) if match else self.our_port()
            return Outcome(OK, "Attached to USB/IP port %d." % port if port
                           else "Attached.", port, command)
        kind = classify(output)
        if kind == ALREADY:
            port = self.our_port()
            if port:
                return Outcome(OK, "Was attached already, to USB/IP port %d." % port, port,
                               command)
        return Outcome(kind, explain(kind, command, output), 0, command)

    def detach(self, port: int = 0) -> Outcome:
        """Detach our device and only ours: the given port, or the one found for it."""
        if self.tool is None:
            return Outcome(NOT_INSTALLED, "")
        # Whatever happens next, the driver must not keep trying to attach again.
        self.runner(self._argv("attach", "-r", self.host, "-b", self.busid, "--stop"), 5.0)
        port = port or self.our_port()
        if not port:
            return Outcome(OK, "The device was not attached.")
        argv = self._argv("detach", "-p", str(port))
        code, output = self.runner(argv, TIMEOUT)
        if code == 0:
            return Outcome(OK, "Detached from USB/IP port %d." % port, port,
                           command_line(argv))
        kind = classify(output)
        return Outcome(kind, "The device could not be detached (%s). Detach it by hand: %s"
                       % (" ".join(output.split())[:200], command_line(argv)), port,
                       command_line(argv))
