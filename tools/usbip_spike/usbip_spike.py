#!/usr/bin/env python3
"""SPIKE: a USB/IP server that exports the emulated HID mouse as a USB device.

Throwaway test program. Question it answers: if the virtual mouse is offered
over USB/IP instead of /dev/uhid, does a USB/IP client (usbip-win2 on Windows,
vhci-hcd on Linux) attach it, and does the game then find it?

    python3 tools/usbip_spike/usbip_spike.py              serve on 127.0.0.1:3240
    python3 tools/usbip_spike/usbip_spike.py --selftest   loopback check, no driver needed

This side is the USB/IP *server*: it plays the machine that owns the device.
The client attaches with "usbip attach -r 127.0.0.1 -b 1-1". All answers of
the device to its host protocol come from quadsens.emulator.BragiDevice, the
same code the Linux uhid device uses; only the transport differs.

Wire format: Documentation/usb/usbip_protocol.rst of the Linux kernel,
protocol version 1.1.1 (0x0111). All header fields are big endian; USB
descriptors and setup packets are little endian as always.

Standard library only.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from quadsens import emulator  # noqa: E402

# ---------------------------------------------------------------------------
# USB/IP wire format
# ---------------------------------------------------------------------------

USBIP_VERSION = 0x0111
OP_REQ_DEVLIST, OP_REP_DEVLIST = 0x8005, 0x0005
OP_REQ_IMPORT, OP_REP_IMPORT = 0x8003, 0x0003
CMD_SUBMIT, CMD_UNLINK, RET_SUBMIT, RET_UNLINK = 1, 2, 3, 4
DIR_OUT, DIR_IN = 0, 1
HEADER_SIZE = 48           # every URB message starts with 48 bytes
OP_HEADER = struct.Struct(">HHI")
BASIC = struct.Struct(">IIIII")                 # command, seqnum, devid, direction, ep
SUBMIT_TAIL = struct.Struct(">IiiiI8s")          # flags, length, start_frame, packets, interval, setup
RET_SUBMIT_TAIL = struct.Struct(">iiiii8x")      # status, actual, start_frame, packets, errors
UNLINK_TAIL = struct.Struct(">I24x")
RET_UNLINK_TAIL = struct.Struct(">i24x")
USB_SPEED_FULL = 2
# Linux error numbers, which is what the status field carries (negative).
EPIPE, ECONNRESET, ENOENT = 32, 104, 2
DEFAULT_PORT = 3240
BUSID = "1-1"
BUSNUM, DEVNUM = 1, 2
DEVICE_PATH = "/sys/devices/platform/quadsens-usbip/usb1/1-1"
MAX_TRANSFER = 1 << 16     # nothing this device does needs more


class ProtocolError(Exception):
    """The peer sent something that is not USB/IP; the connection is dropped."""


def pack_op(code: int, status: int = 0) -> bytes:
    return OP_HEADER.pack(USBIP_VERSION, code, status)


def pack_string(text: str, size: int) -> bytes:
    raw = text.encode("ascii", "replace")[:size - 1]
    return raw.ljust(size, b"\0")


def pack_ret_submit(seqnum: int, status: int = 0, actual_length: int = 0,
                    data: bytes = b"") -> bytes:
    """USBIP_RET_SUBMIT. ``data`` follows the header only for IN transfers."""
    return (BASIC.pack(RET_SUBMIT, seqnum, 0, 0, 0)
            + RET_SUBMIT_TAIL.pack(status, actual_length, 0, 0, 0) + data)


def pack_ret_unlink(seqnum: int, status: int) -> bytes:
    return BASIC.pack(RET_UNLINK, seqnum, 0, 0, 0) + RET_UNLINK_TAIL.pack(status)


def pack_cmd_submit(seqnum: int, direction: int, ep: int, length: int,
                    setup: bytes = b"", data: bytes = b"", interval: int = 0,
                    devid: int = (BUSNUM << 16) | DEVNUM) -> bytes:
    """USBIP_CMD_SUBMIT as a client sends it (used by the self-test and the tests)."""
    return (BASIC.pack(CMD_SUBMIT, seqnum, devid, direction, ep)
            # start_frame -1 and number_of_packets 0 for a non-isochronous request:
            # that is what the capture in the kernel document shows on the wire.
            + SUBMIT_TAIL.pack(0, length, -1, 0, interval, setup.ljust(8, b"\0"))
            + (data if direction == DIR_OUT else b""))


def pack_cmd_unlink(seqnum: int, unlink_seqnum: int,
                    devid: int = (BUSNUM << 16) | DEVNUM) -> bytes:
    return BASIC.pack(CMD_UNLINK, seqnum, devid, 0, 0) + UNLINK_TAIL.pack(unlink_seqnum)


# ---------------------------------------------------------------------------
# The USB device: descriptors and requests
# ---------------------------------------------------------------------------

MANUFACTURER = "Corsair"
LANG_EN_US = 0x0409
BCD_DEVICE = 0x0100
ONE_INTERFACE, TWO_INTERFACES = "one-interface", "two-interfaces"
# bRequest values
GET_STATUS, CLEAR_FEATURE, SET_FEATURE, SET_ADDRESS = 0, 1, 3, 5
GET_DESCRIPTOR, SET_DESCRIPTOR, GET_CONFIGURATION, SET_CONFIGURATION = 6, 7, 8, 9
GET_INTERFACE, SET_INTERFACE = 10, 11
HID_GET_REPORT, HID_GET_IDLE, HID_GET_PROTOCOL = 1, 2, 3
HID_SET_REPORT, HID_SET_IDLE, HID_SET_PROTOCOL = 9, 10, 11
DT_DEVICE, DT_CONFIG, DT_STRING, DT_INTERFACE, DT_ENDPOINT = 1, 2, 3, 4, 5
DT_QUALIFIER, DT_BOS, DT_HID, DT_REPORT = 6, 15, 0x21, 0x22
DESCRIPTOR_NAMES = {DT_DEVICE: "DEVICE", DT_CONFIG: "CONFIGURATION", DT_STRING: "STRING",
                    DT_QUALIFIER: "DEVICE_QUALIFIER", DT_BOS: "BOS", DT_HID: "HID",
                    DT_REPORT: "HID_REPORT"}
STANDARD_NAMES = {GET_STATUS: "GET_STATUS", CLEAR_FEATURE: "CLEAR_FEATURE",
                  SET_FEATURE: "SET_FEATURE", SET_ADDRESS: "SET_ADDRESS",
                  GET_DESCRIPTOR: "GET_DESCRIPTOR", SET_DESCRIPTOR: "SET_DESCRIPTOR",
                  GET_CONFIGURATION: "GET_CONFIGURATION",
                  SET_CONFIGURATION: "SET_CONFIGURATION", GET_INTERFACE: "GET_INTERFACE",
                  SET_INTERFACE: "SET_INTERFACE"}
HID_NAMES = {HID_GET_REPORT: "HID GET_REPORT", HID_GET_IDLE: "HID GET_IDLE",
             HID_GET_PROTOCOL: "HID GET_PROTOCOL", HID_SET_REPORT: "HID SET_REPORT",
             HID_SET_IDLE: "HID SET_IDLE", HID_SET_PROTOCOL: "HID SET_PROTOCOL"}

Log = Callable[[dict[str, Any]], None]


def _ignore(_entry: dict[str, Any]) -> None:
    pass


def string_descriptor(text: str) -> bytes:
    raw = text.encode("utf-16-le")
    return bytes([2 + len(raw), DT_STRING]) + raw


class UsbDevice:
    """The emulated USB HID device: what it says about itself and how it answers.

    One HID interface per top-level collection (usage 1, optionally usage 2),
    each with an interrupt IN and an interrupt OUT endpoint of 64 bytes. An
    output report goes to the protocol handler; its reply is queued as the
    next input report of the same interface.
    """

    def __init__(self, product_id: int = emulator.DEFAULT_PRODUCT_ID,
                 serial: str = emulator.SERIAL, layout: str = ONE_INTERFACE,
                 log: Log | None = None, state_path: str | None = None) -> None:
        if layout not in (ONE_INTERFACE, TWO_INTERFACES):
            raise ValueError("unknown layout %r" % layout)
        self.product_id = product_id
        self.serial = serial
        self.layout = layout
        self.log: Log = log or _ignore
        self.usages = (1,) if layout == ONE_INTERFACE else (1, 2)
        self.bragi = emulator.BragiDevice(product_id=product_id, state_path=state_path,
                                          log=self._bragi_log)
        self.reset()

    def reset(self) -> None:
        """Back to the state of a freshly plugged device."""
        self.configuration = 0
        self.inputs: list[collections.deque[bytes]] = [collections.deque()
                                                       for _ in self.usages]
        self.last_input = [bytes(emulator.REPORT_LEN) for _ in self.usages]

    def _bragi_log(self, entry: dict[str, Any]) -> None:
        entry = dict(entry)
        entry.setdefault("t", round(time.time(), 3))
        self.log(entry)

    # ---- descriptors --------------------------------------------------------

    @property
    def interface_count(self) -> int:
        return len(self.usages)

    def report_descriptor(self, interface: int) -> bytes:
        return emulator.report_descriptor(self.usages[interface])

    def device_descriptor(self) -> bytes:
        return struct.pack("<BBHBBBBHHHBBBB", 18, DT_DEVICE, 0x0200, 0, 0, 0, 64,
                           emulator.VENDOR_ID, self.product_id, BCD_DEVICE, 1, 2, 3, 1)

    def hid_descriptor(self, interface: int) -> bytes:
        return struct.pack("<BBHBBBH", 9, DT_HID, 0x0111, 0, 1, DT_REPORT,
                           len(self.report_descriptor(interface)))

    @staticmethod
    def endpoint_in(interface: int) -> int:
        return 0x81 + interface

    @staticmethod
    def endpoint_out(interface: int) -> int:
        return 0x01 + interface

    def configuration_descriptor(self) -> bytes:
        body = b""
        for interface in range(self.interface_count):
            body += struct.pack("<BBBBBBBBB", 9, DT_INTERFACE, interface, 0, 2, 3, 0, 0, 0)
            body += self.hid_descriptor(interface)
            # Interrupt endpoints, 64 bytes, polled every millisecond (full speed).
            body += struct.pack("<BBBBHB", 7, DT_ENDPOINT, self.endpoint_in(interface), 3, 64, 1)
            body += struct.pack("<BBBBHB", 7, DT_ENDPOINT, self.endpoint_out(interface), 3, 64, 1)
        # Bus powered, 100 mA.
        head = struct.pack("<BBHBBBBB", 9, DT_CONFIG, 9 + len(body), self.interface_count,
                           1, 0, 0x80, 50)
        return head + body

    def string(self, index: int) -> bytes | None:
        if index == 0:
            return struct.pack("<BBH", 4, DT_STRING, LANG_EN_US)
        texts = {1: MANUFACTURER, 2: emulator.PRODUCT_NAME, 3: self.serial}
        return string_descriptor(texts[index]) if index in texts else None

    # ---- data ------------------------------------------------------------------

    def interface_of_endpoint(self, ep: int) -> int | None:
        interface = ep - 1
        return interface if 0 <= interface < self.interface_count else None

    def output_report(self, interface: int, data: bytes, source: str) -> None:
        """An output report arrived: let the protocol handler answer it."""
        self.bragi.current_node = "if%d" % interface
        self.bragi.current_source = source
        reply = self.bragi.handle_output_report(data)
        if reply is not None:
            self.inputs[interface].append(reply)

    def next_input(self, interface: int) -> bytes | None:
        if not self.inputs[interface]:
            return None
        report = self.inputs[interface].popleft()
        self.last_input[interface] = report
        return report

    # ---- control transfers --------------------------------------------------------

    def control(self, setup: bytes, data: bytes) -> tuple[int, bytes]:
        """Answer one control transfer: (status, data for the host).

        Status 0 is success; -EPIPE is a stall, the USB way of saying "not
        supported". Nothing here raises for an unknown request.
        """
        request_type, request, value, index, length = struct.unpack("<BBHHH", setup)
        kind = (request_type >> 5) & 3            # 0 standard, 1 class, 2 vendor
        recipient = request_type & 0x1F           # 0 device, 1 interface, 2 endpoint
        to_host = bool(request_type & 0x80)
        entry: dict[str, Any] = {"event": "control", "value": "0x%04X" % value,
                                 "index": index, "length": length}
        status, answer = -EPIPE, b""

        if kind == 0 and request == GET_DESCRIPTOR:
            descriptor_type, descriptor_index = value >> 8, value & 0xFF
            entry["name"] = "GET_DESCRIPTOR %s" % DESCRIPTOR_NAMES.get(
                descriptor_type, "type 0x%02X" % descriptor_type)
            found: bytes | None = None
            if recipient == 0:
                if descriptor_type == DT_DEVICE:
                    found = self.device_descriptor()
                elif descriptor_type == DT_CONFIG and descriptor_index == 0:
                    found = self.configuration_descriptor()
                elif descriptor_type == DT_STRING:
                    entry["name"] += " %d" % descriptor_index
                    found = self.string(descriptor_index)
                # DEVICE_QUALIFIER and BOS: stall. A full-speed USB 2.0 device has neither.
            elif recipient == 1 and index < self.interface_count:
                if descriptor_type == DT_REPORT:
                    found = self.report_descriptor(index)
                elif descriptor_type == DT_HID:
                    found = self.hid_descriptor(index)
            if found is not None:
                status, answer = 0, found[:length]
        elif kind == 0:
            entry["name"] = STANDARD_NAMES.get(request, "standard request %d" % request)
            if request == SET_CONFIGURATION and value in (0, 1):
                self.configuration = value
                status = 0
            elif request == GET_CONFIGURATION:
                status, answer = 0, bytes([self.configuration])
            elif request == SET_INTERFACE and value == 0 and index < self.interface_count:
                status = 0
            elif request == GET_INTERFACE and index < self.interface_count:
                status, answer = 0, b"\0"
            elif request == GET_STATUS:
                status, answer = 0, b"\0\0"
            elif request in (CLEAR_FEATURE, SET_FEATURE, SET_ADDRESS):
                status = 0
        elif kind == 1 and recipient == 1 and index < self.interface_count:
            entry["name"] = HID_NAMES.get(request, "HID request %d" % request)
            report_type = value >> 8              # 1 input, 2 output, 3 feature
            if request == HID_SET_IDLE or request == HID_SET_PROTOCOL:
                status = 0
            elif request == HID_GET_IDLE:
                status, answer = 0, b"\0"
            elif request == HID_GET_PROTOCOL:
                status, answer = 0, b"\1"          # report protocol
            elif request == HID_GET_REPORT:
                source = self.last_input[index] if report_type == 1 else b""
                status, answer = 0, source.ljust(emulator.REPORT_LEN, b"\0")
            elif request == HID_SET_REPORT:
                status = 0
                if report_type == 2:
                    # The same as data on the interrupt OUT endpoint.
                    self.log(entry | {"status": 0, "raw": data.hex()})
                    self.output_report(index, data, "SET_REPORT/output")
                    return 0, b""
        else:
            entry["name"] = "request type 0x%02X request %d" % (request_type, request)

        if status == 0 and to_host:
            answer = answer[:length]
        entry["status"] = "ok" if status == 0 else "STALL"
        if answer:
            entry["answer"] = answer.hex() if len(answer) <= 32 else "%d bytes" % len(answer)
        self.log(entry)
        return status, answer


# ---------------------------------------------------------------------------
# One client connection
# ---------------------------------------------------------------------------


@dataclass
class Session:
    """Protocol state of one connection: bytes in, bytes out, no socket.

    A connection starts in the "operation" phase (list or import). After a
    successful import it carries URBs until it closes.
    """

    device: UsbDevice
    log: Log = _ignore
    attached: bool = False
    buffer: bytes = b""
    # Interrupt IN requests waiting for an input report: endpoint -> (seqnum, length).
    pending: dict[int, collections.deque[tuple[int, int]]] = field(default_factory=dict)

    def exported(self, with_interfaces: bool) -> bytes:
        """The device record of OP_REP_DEVLIST (with interfaces) and OP_REP_IMPORT."""
        device = self.device
        record = pack_string(DEVICE_PATH, 256) + pack_string(BUSID, 32)
        record += struct.pack(">IIIHHHBBBBBB", BUSNUM, DEVNUM, USB_SPEED_FULL,
                              emulator.VENDOR_ID, device.product_id, BCD_DEVICE,
                              0, 0, 0, 1, 1, device.interface_count)
        if with_interfaces:
            record += bytes([3, 0, 0, 0]) * device.interface_count
        return record

    def feed(self, data: bytes) -> bytes:
        """Take received bytes, return what is to be sent. Raises ProtocolError."""
        self.buffer += data
        out = b""
        while True:
            reply = self._one_urb() if self.attached else self._one_operation()
            if reply is None:
                return out
            out += reply

    # ---- operation phase -----------------------------------------------------------

    def _one_operation(self) -> bytes | None:
        if len(self.buffer) < OP_HEADER.size:
            return None
        version, code, _status = OP_HEADER.unpack_from(self.buffer)
        if version != USBIP_VERSION:
            raise ProtocolError("USB/IP version 0x%04X, expected 0x%04X"
                                % (version, USBIP_VERSION))
        if code == OP_REQ_DEVLIST:
            self.buffer = self.buffer[OP_HEADER.size:]
            self.log({"event": "usbip", "op": "OP_REQ_DEVLIST", "answer": "1 device, busid %s"
                      % BUSID})
            return pack_op(OP_REP_DEVLIST) + struct.pack(">I", 1) + self.exported(True)
        if code == OP_REQ_IMPORT:
            if len(self.buffer) < OP_HEADER.size + 32:
                return None
            busid = self.buffer[OP_HEADER.size:OP_HEADER.size + 32].split(b"\0")[0]
            self.buffer = self.buffer[OP_HEADER.size + 32:]
            wanted = busid.decode("ascii", "replace")
            if wanted != BUSID:
                self.log({"event": "usbip", "op": "OP_REQ_IMPORT", "busid": wanted,
                          "answer": "refused: no such busid"})
                return pack_op(OP_REP_IMPORT, 1)
            self.attached = True
            self.device.reset()
            self.log({"event": "usbip", "op": "OP_REQ_IMPORT", "busid": wanted,
                      "answer": "attached"})
            return pack_op(OP_REP_IMPORT) + self.exported(False)
        raise ProtocolError("unknown operation 0x%04X" % code)

    # ---- URB phase --------------------------------------------------------------------

    def _one_urb(self) -> bytes | None:
        if len(self.buffer) < HEADER_SIZE:
            return None
        command, seqnum, _devid, direction, ep = BASIC.unpack_from(self.buffer)
        if command == CMD_UNLINK:
            (unlink_seqnum,) = UNLINK_TAIL.unpack_from(self.buffer, BASIC.size)
            self.buffer = self.buffer[HEADER_SIZE:]
            return self._unlink(seqnum, unlink_seqnum)
        if command != CMD_SUBMIT:
            raise ProtocolError("unknown command %d" % command)
        _flags, length, _start, _packets, _interval, setup = SUBMIT_TAIL.unpack_from(
            self.buffer, BASIC.size)
        if not 0 <= length <= MAX_TRANSFER:
            raise ProtocolError("transfer length %d" % length)
        payload = length if direction == DIR_OUT else 0
        if len(self.buffer) < HEADER_SIZE + payload:
            return None
        data = self.buffer[HEADER_SIZE:HEADER_SIZE + payload]
        self.buffer = self.buffer[HEADER_SIZE + payload:]
        if ep == 0:
            status, answer = self.device.control(setup, data)
            if direction == DIR_IN:
                return pack_ret_submit(seqnum, status, len(answer), answer) + self._complete()
            return pack_ret_submit(seqnum, status, length if status == 0 else 0) \
                + self._complete()
        interface = self.device.interface_of_endpoint(ep)
        if interface is None:
            self.log({"event": "usbip", "op": "SUBMIT", "seqnum": seqnum, "ep": ep,
                      "answer": "STALL: no such endpoint"})
            return pack_ret_submit(seqnum, -EPIPE)
        if direction == DIR_OUT:
            self.log({"event": "usbip", "op": "SUBMIT OUT", "seqnum": seqnum, "ep": ep,
                      "bytes": len(data)})
            self.device.output_report(interface, data, "interrupt OUT")
            return pack_ret_submit(seqnum, 0, len(data)) + self._complete()
        self.pending.setdefault(ep, collections.deque()).append((seqnum, length))
        return self._complete()

    def _complete(self) -> bytes:
        """Finish waiting interrupt IN requests for which an input report is ready."""
        out = b""
        for ep, waiting in self.pending.items():
            interface = self.device.interface_of_endpoint(ep)
            while waiting and interface is not None:
                report = self.device.next_input(interface)
                if report is None:
                    break
                seqnum, length = waiting.popleft()
                report = report[:length]
                self.log({"event": "usbip", "op": "RET_SUBMIT IN", "seqnum": seqnum,
                          "ep": ep, "bytes": len(report)})
                out += pack_ret_submit(seqnum, 0, len(report), report)
        return out

    def _unlink(self, seqnum: int, unlink_seqnum: int) -> bytes:
        for waiting in self.pending.values():
            for item in list(waiting):
                if item[0] == unlink_seqnum:
                    waiting.remove(item)
                    self.log({"event": "usbip", "op": "CMD_UNLINK", "seqnum": seqnum,
                              "unlinked": unlink_seqnum, "answer": "-ECONNRESET"})
                    # The unlinked request itself gets no RET_SUBMIT.
                    return pack_ret_unlink(seqnum, -ECONNRESET)
        self.log({"event": "usbip", "op": "CMD_UNLINK", "seqnum": seqnum,
                  "unlinked": unlink_seqnum, "answer": "already completed"})
        return pack_ret_unlink(seqnum, 0)


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
        stamp = time.strftime("%H:%M:%S", time.localtime(entry.get("t", time.time())))
        event = entry.get("event")
        if event == "usbip":
            rest = " ".join("%s=%s" % (k, v) for k, v in entry.items()
                            if k not in ("t", "event", "op"))
            return "%s usbip   %-14s %s" % (stamp, entry.get("op"), rest)
        if event == "control":
            rest = " ".join("%s=%s" % (k, v) for k, v in entry.items()
                            if k not in ("t", "event", "name", "status"))
            return "%s control %-32s %-5s %s" % (stamp, entry.get("name"),
                                                 entry.get("status"), rest)
        if event == "server":
            return "%s server  %s" % (stamp, entry.get("message"))
        return "%s hid     %s" % (stamp, emulator.format_entry(entry).split(" ", 1)[1])

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class Server:
    """Listens, serves one client at a time, and takes the next one afterwards."""

    def __init__(self, device: UsbDevice, log: Log, host: str = "127.0.0.1",
                 port: int = DEFAULT_PORT) -> None:
        self.device = device
        self.log = log
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((host, port))
        self.socket.listen(1)
        self.socket.settimeout(0.5)
        self.address = self.socket.getsockname()
        self.stopping = threading.Event()

    def serve_forever(self) -> None:
        self.log({"event": "server", "message": "listening on %s:%d, busid %s"
                  % (self.address[0], self.address[1], BUSID)})
        try:
            while not self.stopping.is_set():
                try:
                    connection, peer = self.socket.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                self._serve(connection, "%s:%d" % peer[:2])
        finally:
            self.socket.close()

    def _serve(self, connection: socket.socket, peer: str) -> None:
        session = Session(self.device, self.log)
        self.log({"event": "server", "message": "connection from %s" % peer})
        try:
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            connection.settimeout(0.5)
            while not self.stopping.is_set():
                try:
                    data = connection.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    break
                reply = session.feed(data)
                if reply:
                    connection.sendall(reply)
        except ProtocolError as exc:
            self.log({"event": "server", "message": "dropping %s: %s" % (peer, exc)})
        except OSError as exc:
            self.log({"event": "server", "message": "connection to %s lost: %s" % (peer, exc)})
        finally:
            connection.close()
            if session.attached:
                self.device.bragi.save()
                self.log({"event": "server", "message":
                          "client detached; the device can be attached again"})
            else:
                self.log({"event": "server", "message": "connection from %s closed" % peer})

    def stop(self) -> None:
        self.stopping.set()


# ---------------------------------------------------------------------------
# A minimal client, for the self-test and the tests
# ---------------------------------------------------------------------------


class Client:
    """Talks USB/IP to the server over a socket, the way a host driver would."""

    def __init__(self, host: str, port: int, timeout: float = 5.0) -> None:
        self.socket = socket.create_connection((host, port), timeout=timeout)
        self.seqnum = 0

    def close(self) -> None:
        self.socket.close()

    def read(self, count: int) -> bytes:
        data = b""
        while len(data) < count:
            chunk = self.socket.recv(count - len(data))
            if not chunk:
                raise ConnectionError("server closed the connection")
            data += chunk
        return data

    def devlist(self) -> dict[str, Any]:
        self.socket.sendall(pack_op(OP_REQ_DEVLIST))
        version, code, status = OP_HEADER.unpack(self.read(8))
        (count,) = struct.unpack(">I", self.read(4))
        record = self.read(0x138)
        interfaces = self.read(4 * record[-1])
        return {"version": version, "code": code, "status": status, "count": count,
                "busid": record[256:288].split(b"\0")[0].decode(),
                "path": record[:256].split(b"\0")[0].decode(),
                "speed": struct.unpack_from(">I", record, 296)[0],
                "ids": struct.unpack_from(">HHH", record, 300),
                "interfaces": [tuple(interfaces[i:i + 3]) for i in range(0, len(interfaces), 4)]}

    def import_device(self, busid: str = BUSID) -> int:
        self.socket.sendall(pack_op(OP_REQ_IMPORT) + pack_string(busid, 32))
        _version, _code, status = OP_HEADER.unpack(self.read(8))
        if status == 0:
            self.read(0x138)
        return status

    def submit(self, direction: int, ep: int, length: int, setup: bytes = b"",
               data: bytes = b"") -> int:
        """Send a CMD_SUBMIT and return its seqnum; the reply is read separately."""
        self.seqnum += 1
        self.socket.sendall(pack_cmd_submit(self.seqnum, direction, ep, length, setup, data))
        return self.seqnum

    def reply(self) -> dict[str, Any]:
        header = self.read(HEADER_SIZE)
        command, seqnum, _devid, _direction, _ep = BASIC.unpack_from(header)
        if command == RET_UNLINK:
            (status,) = RET_UNLINK_TAIL.unpack_from(header, BASIC.size)
            return {"command": command, "seqnum": seqnum, "status": status, "data": b""}
        status, actual, _start, _packets, _errors = RET_SUBMIT_TAIL.unpack_from(
            header, BASIC.size)
        return {"command": command, "seqnum": seqnum, "status": status, "actual": actual,
                "header": header}

    def control(self, request_type: int, request: int, value: int, index: int,
                length: int, data: bytes = b"") -> tuple[int, bytes]:
        setup = struct.pack("<BBHHH", request_type, request, value, index, length)
        to_host = bool(request_type & 0x80)
        seqnum = self.submit(DIR_IN if to_host else DIR_OUT, 0, length, setup, data)
        reply = self.reply()
        if reply["seqnum"] != seqnum:
            raise ConnectionError("reply for seqnum %d, expected %d" % (reply["seqnum"], seqnum))
        answer = self.read(reply["actual"]) if to_host else b""
        return reply["status"], answer

    def unlink(self, target: int) -> int:
        self.seqnum += 1
        self.socket.sendall(pack_cmd_unlink(self.seqnum, target))
        return self.reply()["status"]


def enumerate_device(client: Client, interfaces: int = 1) -> dict[str, Any]:
    """The requests a host sends when a HID device is plugged in."""
    found: dict[str, Any] = {}
    status, found["device"] = client.control(0x80, GET_DESCRIPTOR, DT_DEVICE << 8, 0, 18)
    status, head = client.control(0x80, GET_DESCRIPTOR, DT_CONFIG << 8, 0, 9)
    total = struct.unpack_from("<H", head, 2)[0]
    status, found["configuration"] = client.control(0x80, GET_DESCRIPTOR, DT_CONFIG << 8,
                                                    0, total)
    status, found["languages"] = client.control(0x80, GET_DESCRIPTOR, DT_STRING << 8, 0, 255)
    for name, index in (("manufacturer", 1), ("product", 2), ("serial", 3)):
        status, raw = client.control(0x80, GET_DESCRIPTOR, (DT_STRING << 8) | index,
                                     LANG_EN_US, 255)
        found[name] = raw[2:].decode("utf-16-le")
    found["qualifier_status"], _ = client.control(0x80, GET_DESCRIPTOR, DT_QUALIFIER << 8,
                                                  0, 10)
    found["set_configuration"], _ = client.control(0x00, SET_CONFIGURATION, 1, 0, 0)
    found["reports"] = []
    for interface in range(interfaces):
        client.control(0x21, HID_SET_IDLE, 0, interface, 0)
        status, report = client.control(0x81, GET_DESCRIPTOR, DT_REPORT << 8, interface, 255)
        found["reports"].append(report)
    found["status"] = status
    return found


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
