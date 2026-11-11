#!/usr/bin/env python3
"""SPIKE: a USB/IP server that exports the emulated HID mouse as a USB device.

Throwaway test program. Question it answers: if the virtual mouse is offered
over USB/IP instead of /dev/uhid, does a USB/IP client (usbip-win2 on Windows,
vhci-hcd on Linux) attach it, and does the game then find it?

    python3 tools/usbip_spike/usbip_spike.py              serve on 127.0.0.1:3240
    python3 tools/usbip_spike/usbip_spike.py --selftest   loopback check, no driver needed

This side is the USB/IP *server*: it plays the machine that owns the device.
The client attaches with "usbip attach -r 127.0.0.1 -b 1-1".

The server itself lives in quadsens/usbip_server.py, where the application
uses it for the virtual mouse on Windows. This file is the command line
around it, kept for debugging: a console log, --log, and --selftest.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from quadsens import emulator  # noqa: E402
from quadsens.usbip_server import *  # noqa: E402,F401,F403 (the tests reach everything through here)
from quadsens.usbip_server import (  # noqa: E402
    BUSID,
    DEFAULT_PORT,
    DIR_IN,
    DIR_OUT,
    ECONNRESET,
    EPIPE,
    ONE_INTERFACE,
    TWO_INTERFACES,
    Client,
    Server,
    UsbDevice,
    enumerate_device,
    format_event,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class Logger:
    """Readable lines on the console, and optionally JSON lines in a file."""

    def __init__(self, path: str | None = None, quiet: bool = False) -> None:
        self.quiet = quiet
        self.entries: list[dict[str, Any]] = []
        self.file = open(path, "a", encoding="utf-8") if path else None   # noqa: SIM115
        self.directional = 0

    def __call__(self, entry: dict[str, Any]) -> None:
        entry = dict(entry)
        entry.setdefault("t", round(time.time(), 3))
        self.entries.append(entry)
        if self.file is not None:
            self.file.write(json.dumps(entry, default=str) + "\n")
            self.file.flush()
        if self.quiet:
            return
        print(self.text(entry), flush=True)
        if "highlight" in entry:
            self.directional += 1
            bar = "#" * 78
            print("%s\n### DIRECTIONAL VALUE WRITTEN: %s\n%s" % (bar, entry["highlight"], bar),
                  flush=True)

    @staticmethod
    def text(entry: dict[str, Any]) -> str:
        return format_event(entry)

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None


def selftest(layout: str = ONE_INTERFACE, verbose: bool = False) -> bool:
    """Serve on a free local port and attach to it with the built-in client."""
    logger = Logger(quiet=not verbose)
    device = UsbDevice(layout=layout, log=logger)
    server = Server(device, logger, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.address[:2]
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        checks.append((name, bool(ok)))
        print("  %-52s %s" % (name, "ok" if ok else "FAILED"))

    try:
        client = Client(host, port)
        listed = client.devlist()
        client.close()
        check("device list names busid %s" % BUSID, listed["busid"] == BUSID)
        check("vendor and product id", listed["ids"][:2] == (emulator.VENDOR_ID,
                                                              device.product_id))
        check("HID interface(s) listed", listed["interfaces"]
              == [(3, 0, 0)] * device.interface_count)

        client = Client(host, port)
        check("import refused for a wrong busid", client.import_device("9-9") == 1)
        client.close()
        client = Client(host, port)
        check("import accepted", client.import_device() == 0)
        found = enumerate_device(client, device.interface_count)
        check("device descriptor", found["device"] == device.device_descriptor())
        check("configuration descriptor",
              found["configuration"] == device.configuration_descriptor())
        check("product string", found["product"] == emulator.PRODUCT_NAME)
        check("serial string", found["serial"] == device.serial)
        check("device qualifier is stalled", found["qualifier_status"] == -EPIPE)
        check("report descriptor",
              found["reports"][0] == emulator.report_descriptor(1))

        waiting = client.submit(DIR_IN, 1, 64)             # stays pending
        start = bytes([0, 1, 0, emulator.CMD_SESSION, 1, 0x40, 0xE2, 0x01, 0x00])
        sent = client.submit(DIR_OUT, 1, 64, data=start.ljust(64, b"\0"))
        first = client.reply()
        check("output report acknowledged", first["seqnum"] == sent and first["status"] == 0)
        second = client.reply()
        report = client.read(second["actual"])
        check("input report completes the waiting request",
              second["seqnum"] == waiting and len(report) == 64)
        check("reply answers the session command",
              report[3] == emulator.CMD_SESSION and report[4] == emulator.STATUS_OK)
        pending = client.submit(DIR_IN, 1, 64)
        check("unlink of a waiting request", client.unlink(pending) == -ECONNRESET)
        client.close()
        time.sleep(0.1)
        client = Client(host, port)
        check("attach again after detach", client.import_device() == 0)
        client.close()
    except (OSError, ConnectionError, struct.error) as exc:
        check("no error (%s)" % exc, False)
    finally:
        server.stop()
        thread.join(timeout=3)
    passed = all(ok for _name, ok in checks)
    print("SELFTEST %s (%d of %d checks)" % ("PASS" if passed else "FAIL",
                                             sum(ok for _n, ok in checks), len(checks)))
    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SPIKE: export the emulated HID mouse over USB/IP (server side).")
    parser.add_argument("--pid", type=lambda s: int(s, 0), default=emulator.DEFAULT_PRODUCT_ID,
                        help="USB product id (default 0x%04X)" % emulator.DEFAULT_PRODUCT_ID)
    parser.add_argument("--serial", default=emulator.SERIAL, help="serial number string")
    parser.add_argument("--layout", choices=(ONE_INTERFACE, TWO_INTERFACES),
                        default=ONE_INTERFACE,
                        help="one HID interface (default) or a second one for usage 2")
    parser.add_argument("--listen", default="127.0.0.1", metavar="ADDRESS",
                        help="address to listen on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--log", metavar="FILE", help="also write the log as JSON lines")
    parser.add_argument("--selftest", action="store_true",
                        help="attach to itself with a built-in client and print PASS or FAIL")
    parser.add_argument("--verbose", action="store_true", help="with --selftest: show the log")
    args = parser.parse_args(argv)

    if args.selftest:
        return 0 if selftest(args.layout, args.verbose) else 1

    logger = Logger(args.log)
    if args.listen not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: listening on %s. The device can be attached from the network; "
              "USB/IP has no authentication." % args.listen, flush=True)
    try:
        device = UsbDevice(args.pid, args.serial, args.layout, logger)
        server = Server(device, logger, args.listen, args.port)
    except OSError as exc:
        print("error: cannot listen on %s:%d (%s). Is another USB/IP server running?"
              % (args.listen, args.port, exc), file=sys.stderr)
        return 1
    print("Device %04X:%04X \"%s\", serial %s, %s." % (
        emulator.VENDOR_ID, args.pid, emulator.PRODUCT_NAME, args.serial, args.layout))
    print("Attach with:  usbip attach -r %s -b %s      (Ctrl+C stops the server)"
          % ("127.0.0.1" if args.listen == "0.0.0.0" else args.listen, BUSID), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped.")
    finally:
        server.stop()
        logger.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
