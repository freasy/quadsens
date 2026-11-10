"""Virtual HID mouse over /dev/uhid that answers Corsair's "Bragi" v1.5 host protocol.

The device identifies itself as a Corsair SABRE V3 PRO because that is what the
host software (Aimlabs' directional sensitivity calibration) looks for. It does
not move a pointer and every value it reports is made up: answers come from an
in-memory property table and file store that is persisted to a JSON file.

Wire format (64-byte reports, no report id):

    request   [subDevice, wantReply, session, command, payload...]
    response  [subDevice, 0,         session, command, status, data...]

Notes marked "Aimlabs:" record what static analysis of that client showed.

Standard library only, no Qt. Needs read/write access to /dev/uhid.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import select
import signal
import struct
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .model import Scalars

LogEntry = dict[str, Any]
LogCallback = Callable[[LogEntry], None]

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

VENDOR_ID = 0x1B1C
DEFAULT_PRODUCT_ID = 0x2B43
# Product ids the Aimlabs client accepts for this mouse family.
KNOWN_PRODUCT_IDS: tuple[int, ...] = (0x2B43, 0x2B3F, 0x2B40, 0x2B41, 0x2B44, 0x2B45)
# The HID name the device must present to be recognised.
PRODUCT_NAME = "Corsair SABRE V3 PRO (emulated)"
SERIAL = "EMU0SABREV3PRO0001"
REPORT_LEN = 64
HEADER = 4
MAX_DATA = REPORT_LEN - HEADER - 1  # response bytes available after the status

CMD_SET = 1
CMD_GET = 2
CMD_GET_MULTI = 3
CMD_UNBIND = 5
CMD_WRITE_BEGIN = 6
CMD_WRITE = 7
CMD_READ = 8
CMD_DESCRIBE = 9
CMD_CALIBRATION = 10
CMD_CREATE_FILE = 11
CMD_DELETE_FILE = 12
CMD_OPEN_FILE = 13
CMD_FACTORY_RESET = 15
CMD_PING = 18
CMD_SESSION = 27

CMD_NAMES: dict[int, str] = {
    CMD_SET: "SET", CMD_GET: "GET", CMD_GET_MULTI: "GET_MULTI", CMD_UNBIND: "UNBIND",
    CMD_WRITE_BEGIN: "WRITE_BEGIN", CMD_WRITE: "WRITE", CMD_READ: "READ",
    CMD_DESCRIBE: "DESCRIBE", CMD_CALIBRATION: "CALIBRATION",
    CMD_CREATE_FILE: "CREATE_FILE", CMD_DELETE_FILE: "DELETE_FILE",
    CMD_OPEN_FILE: "OPEN_FILE", CMD_FACTORY_RESET: "FACTORY_RESET",
    CMD_PING: "PING", CMD_SESSION: "SESSION",
}

STATUS_OK = 0
STATUS_INVALID_ARG = 1
STATUS_INVALID_STATE = 3
STATUS_NOT_SUPPORTED = 4

# Property ids. The first block is what the vendor's web configurator uses, the
# second block is the Aimlabs client's ConfigProperty enum.
P_OPERATING_MODE = 0x03  # Aimlabs "ControlMode": 1 = Self, 2 = Host
P_BATTERY_LEVEL = 15
P_VENDOR_ID = 17
P_PRODUCT_ID = 18
P_FW_VERSION = 19
P_DPI_1, P_DPI_2, P_DPI_3, P_DPI_4, P_DPI_5, P_DPI_SNIPER = 24, 25, 26, 27, 28, 29
P_DPI_ACTIVE_STAGE = 0x1E
P_DPI_ENABLED_MASK = 0x1F
P_DPI_COLOR_1, P_DPI_COLOR_2, P_DPI_COLOR_3 = 47, 48, 49
P_DPI_COLOR_4, P_DPI_COLOR_5, P_DPI_COLOR_SNIPER = 50, 51, 52
P_PROTOCOL_VERSION = 261
P_CALIBRATED_STAGES = 298
P_DIR_SENS_X_POS = 0x125
P_DIR_SENS_X_NEG = 0x126
P_DIR_SENS_Y_POS = 0x127
P_DIR_SENS_Y_NEG = 0x128
P_DIR_SENS_ENABLED = 0x129

FILE_SERIAL = 38

PROP_NAMES: dict[int, str] = {
    P_OPERATING_MODE: "ControlMode", P_BATTERY_LEVEL: "BatteryLevel",
    P_VENDOR_ID: "VendorId", P_PRODUCT_ID: "ProductId", P_FW_VERSION: "FirmwareVersion",
    P_DPI_1: "Dpi1", P_DPI_2: "Dpi2", P_DPI_3: "Dpi3", P_DPI_4: "Dpi4", P_DPI_5: "Dpi5",
    P_DPI_SNIPER: "DpiSniper", P_DPI_ACTIVE_STAGE: "ActiveDpiStage",
    P_DPI_ENABLED_MASK: "DpiStageEnabledMask",
    P_DPI_COLOR_1: "DpiColor1", P_DPI_COLOR_2: "DpiColor2", P_DPI_COLOR_3: "DpiColor3",
    P_DPI_COLOR_4: "DpiColor4", P_DPI_COLOR_5: "DpiColor5",
    P_DPI_COLOR_SNIPER: "DpiColorSniper", P_PROTOCOL_VERSION: "ProtocolVersion",
    P_CALIBRATED_STAGES: "CalibratedStages",
    P_DIR_SENS_X_POS: "DirectionalSensScalarXPos",
    P_DIR_SENS_X_NEG: "DirectionalSensScalarXNeg",
    P_DIR_SENS_Y_POS: "DirectionalSensScalarYPos",
    P_DIR_SENS_Y_NEG: "DirectionalSensScalarYNeg",
    P_DIR_SENS_ENABLED: "DirectionalSensEnabled",
}

# Aimlabs: the four scalars are written as little-endian IEEE-754 singles, i.e.
# the 32-bit value field carries raw float bits.
FLOAT_PROPS: tuple[int, ...] = (
    P_DIR_SENS_X_POS, P_DIR_SENS_X_NEG, P_DIR_SENS_Y_POS, P_DIR_SENS_Y_NEG)
HIGHLIGHT_PROPS: tuple[int, ...] = FLOAT_PROPS + (P_DIR_SENS_ENABLED,)


def float_to_bits(value: float) -> int:
    return int(struct.unpack("<I", struct.pack("<f", value))[0])


def bits_to_float(bits: int) -> float:
    return float(struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0])


def _rgb(r: int, g: int, b: int) -> int:
    # Colour properties are stored blue-first.
    return b | (g << 8) | (r << 16)


def default_props(product_id: int = DEFAULT_PRODUCT_ID) -> dict[int, int]:
    return {
        P_OPERATING_MODE: 1,
        P_BATTERY_LEVEL: 870,  # per mille
        P_VENDOR_ID: VENDOR_ID,
        P_PRODUCT_ID: product_id,
        P_FW_VERSION: 0x00300508,  # bytes: major 8, minor 5, patch 48 (LE16) -> "8.5.48"
        P_DPI_1: 400, P_DPI_2: 800, P_DPI_3: 1600, P_DPI_4: 3200, P_DPI_5: 6400,
        P_DPI_SNIPER: 400,
        P_DPI_ACTIVE_STAGE: 1,
        P_DPI_ENABLED_MASK: 0x1F,
        P_DPI_COLOR_1: _rgb(255, 0, 0), P_DPI_COLOR_2: _rgb(255, 255, 255),
        P_DPI_COLOR_3: _rgb(0, 255, 0), P_DPI_COLOR_4: _rgb(255, 255, 0),
        P_DPI_COLOR_5: _rgb(0, 0, 255), P_DPI_COLOR_SNIPER: _rgb(255, 0, 255),
        P_PROTOCOL_VERSION: 0x0501,  # bytes: 1, 5 -> "1.5"
        P_CALIBRATED_STAGES: 0,  # bit n = DPI stage n has a calibration
        # Directional sensitivity: neutral scalars (1.0), feature switched off.
        P_DIR_SENS_X_POS: float_to_bits(1.0),
        P_DIR_SENS_X_NEG: float_to_bits(1.0),
        P_DIR_SENS_Y_POS: float_to_bits(1.0),
        P_DIR_SENS_Y_NEG: float_to_bits(1.0),
        P_DIR_SENS_ENABLED: 0,
    }


def default_files() -> dict[int, bytes]:
    ascii_serial = SERIAL.encode("ascii")
    return {FILE_SERIAL: bytes([0, 0, len(ascii_serial)]) + ascii_serial}


def _u16(buf: bytes, off: int) -> int:
    return buf[off] | (buf[off + 1] << 8)


def _u32(buf: bytes, off: int) -> int:
    return int(struct.unpack_from("<I", buf, off)[0])


def _le32(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def describe_prop(prop_id: int, value: int) -> str:
    """Human readable rendering of a property value for the logs."""
    name = PROP_NAMES.get(prop_id, "prop")
    if prop_id in FLOAT_PROPS:
        return "%s (0x%X) = %.6g  [raw 0x%08X, as uint %d]" % (
            name, prop_id, bits_to_float(value), value, value)
    if prop_id == P_DIR_SENS_ENABLED:
        # Aimlabs writes this one as an integer 0/1. Should a client ever send
        # float 1.0 instead, the raw bits make that obvious.
        return "%s (0x%X) = %s  [raw 0x%08X, as float %.6g]" % (
            name, prop_id, "ENABLED" if value else "DISABLED", value, bits_to_float(value))
    return "%s (0x%X) = %d (0x%X)" % (name, prop_id, value, value)


# ---------------------------------------------------------------------------
# Protocol handler (no I/O besides the optional state file and log callback)
# ---------------------------------------------------------------------------

@dataclass
class _Buffer:
    file_id: int
    read_pos: int = 0
    pending: bytearray | None = None
    expected: int = 0


def _ignore(_entry: LogEntry) -> None:
    return None


class BragiDevice:
    """Device-side state machine for the Bragi v1.5 host protocol."""

    def __init__(self, product_id: int = DEFAULT_PRODUCT_ID, state_path: str | None = None,
                 log: LogCallback | None = None, session_header: str = "echo") -> None:
        self.product_id = product_id
        self.state_path = state_path
        self.log: LogCallback = log or _ignore
        # What goes into response byte 2 of a SESSION reply:
        #   "echo": the session byte of the request. Aimlabs: VERIFIED required.
        #           Its client drops any response whose session byte differs
        #           from the request's, and the start request is sent with the
        #           old session id (0).
        #   "new":  the freshly allocated session id, which is what the vendor's
        #           web configurator expects.
        self.session_header = session_header
        self.props: dict[int, int] = default_props(product_id)
        self.files: dict[int, bytes] = default_files()
        self.buffers: dict[int, _Buffer] = {}
        self.session_id = 0
        self.session_active = False
        # Set by the transport before each command, for the log only.
        self.current_node: str | None = None
        self.current_source: str | None = None
        self._load()

    # ---- persistence ----------------------------------------------------

    def _load(self) -> None:
        if not self.state_path or not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                raw = json.load(fh)
            for key, value in raw.get("props", {}).items():
                self.props[int(key)] = int(value) & 0xFFFFFFFF
            for key, value in raw.get("files", {}).items():
                self.files[int(key)] = bytes(value)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self.log({"event": "warning", "message":
                      "could not read state file %s (%s); using defaults"
                      % (self.state_path, exc)})
        # The identity always follows the caller, not a stale state file.
        self.props[P_VENDOR_ID] = VENDOR_ID
        self.props[P_PRODUCT_ID] = self.product_id

    def save(self) -> None:
        if not self.state_path:
            return
        payload = {
            "props": {str(k): v for k, v in sorted(self.props.items())},
            "files": {str(k): list(v) for k, v in sorted(self.files.items())},
        }
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1)
            os.replace(tmp, self.state_path)
        except OSError as exc:
            self.log({"event": "warning", "message":
                      "could not write state file %s (%s)" % (self.state_path, exc)})

    # ---- helpers --------------------------------------------------------

    def _commit(self, buf: _Buffer | None) -> None:
        if buf is None or buf.pending is None:
            return
        self.files[buf.file_id] = bytes(buf.pending[:buf.expected])
        buf.pending = None
        self.save()

    # ---- framing --------------------------------------------------------

    @staticmethod
    def normalize(raw: bytes) -> bytes:
        """Turn whatever arrived as an output report into a 64-byte request.

        hidraw, Wine and WebHID all prefix an output report with its report id
        and send 0 when the descriptor has none, so the usual shape is
        [0x00] + 64 bytes. A bare 64-byte (or shorter) frame is accepted too.
        """
        raw = bytes(raw)
        if len(raw) > REPORT_LEN:
            raw = raw[1:]
        return raw[:REPORT_LEN].ljust(REPORT_LEN, b"\0")

    def handle_output_report(self, raw: bytes) -> bytes | None:
        """Process one raw output report. Returns the 64-byte reply or None."""
        raw = bytes(raw)
        if len(raw) < HEADER:
            self.log({"event": "short_report", "raw": raw.hex()})
            return None
        return self.handle(self.normalize(raw), raw_hex=raw.hex())

    # ---- command dispatch -----------------------------------------------

    def handle(self, req: bytes, raw_hex: str | None = None) -> bytes | None:
        """Execute one 64-byte request and return the 64-byte response.

        Returns None when the host cleared the wantReply flag. The command is
        still executed in that case: a write that asks for no acknowledgement
        is still a write.
        """
        req = bytes(req).ljust(REPORT_LEN, b"\0")
        sub_device, want_reply, session, cmd = req[0], req[1], req[2], req[3]
        p = req[HEADER:]
        entry: LogEntry = {"cmd": CMD_NAMES.get(cmd, cmd), "sub": sub_device,
                           "session": session, "want_reply": bool(want_reply)}
        status = STATUS_OK
        data = b""
        reply_session = session

        if cmd == CMD_SESSION:
            # Body: [sub command, 4-byte token]. 1 = start, 2 = stop.
            # Aimlabs: start is [1, u32 unix time]; stop is [2, 0,0,0,0, id].
            # Reply data is the 4 token bytes echoed followed by the session
            # id, which puts the id at response byte 9 for both clients.
            sub_cmd = p[0]
            if sub_cmd == 1:
                self.session_id = (self.session_id % 250) + 1
                self.session_active = True
            elif sub_cmd == 2:
                self.session_active = False
            entry.update(sub_command=sub_cmd, new_session=self.session_id,
                         token=p[1:5].hex())
            data = p[1:5] + bytes([self.session_id])
            if self.session_header == "new":
                reply_session = self.session_id
        elif cmd == CMD_GET:
            prop_id = _u16(p, 0)
            value = self.props.get(prop_id, 0)
            entry.update(id=prop_id, name=PROP_NAMES.get(prop_id), value=value,
                         known=prop_id in self.props)
            if prop_id in FLOAT_PROPS:
                entry["float"] = bits_to_float(value)
            data = _le32(value)
        elif cmd == CMD_SET:
            prop_id = _u16(p, 0)
            value = _u32(p, 2)
            entry.update(id=prop_id, name=PROP_NAMES.get(prop_id), value=value)
            if prop_id in FLOAT_PROPS:
                entry["float"] = bits_to_float(value)
            if prop_id in HIGHLIGHT_PROPS:
                entry["highlight"] = describe_prop(prop_id, value)
            self.props[prop_id] = value
            self.save()
        elif cmd == CMD_GET_MULTI:
            count = min(p[0], (len(p) - 1) // 2)
            ids = [_u16(p, 1 + i * 2) for i in range(count)]
            entry["ids"] = ids
            data = bytes([len(ids)])
            for prop_id in ids:
                data += struct.pack("<HI", prop_id, self.props.get(prop_id, 0))
        elif cmd == CMD_OPEN_FILE:
            index = p[0]
            file_id = _u16(p, 1)
            entry.update(index=index, file_id=file_id)
            if index in self.buffers:
                status = STATUS_INVALID_STATE
            elif file_id not in self.files:
                status = STATUS_INVALID_ARG
            else:
                self.buffers[index] = _Buffer(file_id)
        elif cmd == CMD_CREATE_FILE:
            file_id = _u16(p, 0)
            entry["file_id"] = file_id
            self.files.setdefault(file_id, b"")
            self.save()
        elif cmd == CMD_DELETE_FILE:
            file_id = _u16(p, 0)
            entry["file_id"] = file_id
            self.files.pop(file_id, None)
            self.save()
        elif cmd == CMD_DESCRIBE:
            buf = self.buffers.get(p[0])
            if buf is None:
                status = STATUS_INVALID_STATE
            else:
                entry["file_id"] = buf.file_id
                data = b"\0\0" + _le32(len(self.files.get(buf.file_id, b"")))
        elif cmd == CMD_READ:
            buf = self.buffers.get(p[0])
            if buf is None:
                status = STATUS_INVALID_STATE
            else:
                content = self.files.get(buf.file_id, b"")
                chunk = content[buf.read_pos:buf.read_pos + MAX_DATA]
                buf.read_pos += len(chunk)
                entry.update(file_id=buf.file_id, bytes=len(chunk))
                data = chunk
        elif cmd == CMD_WRITE_BEGIN:
            buf = self.buffers.get(p[0])
            if buf is None:
                status = STATUS_INVALID_STATE
            else:
                buf.expected = _u32(p, 1)
                buf.pending = bytearray(p[5:])
                entry.update(file_id=buf.file_id, length=buf.expected)
                if len(buf.pending) >= buf.expected:
                    self._commit(buf)
        elif cmd == CMD_WRITE:
            buf = self.buffers.get(p[0])
            if buf is None or buf.pending is None:
                status = STATUS_INVALID_STATE
            else:
                buf.pending += p[1:]
                entry["file_id"] = buf.file_id
                if len(buf.pending) >= buf.expected:
                    self._commit(buf)
        elif cmd == CMD_UNBIND:
            indexes = list(p[1:1 + p[0]])
            entry["indexes"] = indexes
            for index in indexes:
                self._commit(self.buffers.get(index))
                self.buffers.pop(index, None)
        elif cmd == CMD_FACTORY_RESET:
            self.props = default_props(self.product_id)
            self.files = default_files()
            self.buffers.clear()
            self.save()
        elif cmd in (CMD_PING, CMD_CALIBRATION):
            pass
        else:
            status = STATUS_NOT_SUPPORTED

        entry["status"] = status
        entry["request"] = req.hex()
        if raw_hex is not None:
            entry["raw"] = raw_hex

        res: bytes | None = None
        if want_reply:
            # Aimlabs: the client reads byte 1 as an error code (must be 0),
            # byte 2 as session, byte 3 as command, byte 4 as status; the body
            # starts at byte 5. Byte 0 is not looked at.
            out = bytearray(REPORT_LEN)
            out[0:5] = bytes([sub_device, 0, reply_session, cmd, status])
            data = data[:MAX_DATA]
            out[HEADER + 1:HEADER + 1 + len(data)] = data
            res = bytes(out)
            entry["response"] = res.hex()
        self.log(entry)
        return res


# ---------------------------------------------------------------------------
# HID report descriptor
# ---------------------------------------------------------------------------
#
# Layout decision: one HID device per collection, reports WITHOUT report ids.
#
#   * Aimlabs (VERIFIED statically): it takes the first HID top-level collection
#     with usage page 0xFF42 and a non-zero output report length (the usage is
#     not checked) and uses report id 0 with 64-byte frames. So it needs
#     unnumbered 64-byte reports.
#   * The vendor's web configurator wants a usage-1 (command) and a usage-2
#     (notification) collection, each with 64-byte input and output reports.
#   * One HID interface can only carry several top-level collections when the
#     reports are numbered. Two collections that both use unnumbered reports
#     therefore means two HID interfaces. A uhid node is one HID interface, so
#     the default "split" layout creates two uhid devices with the same
#     vendor/product/serial: usage 1 and usage 2.
#
# Commands are answered on whichever of the two devices they arrive on.
# The "command-only" layout creates just the usage-1 device.

def report_descriptor(usage: int) -> bytes:
    return bytes([
        0x06, 0x42, 0xFF,  # Usage Page (Vendor 0xFF42)
        0x09, usage,       # Usage (1 = command, 2 = notification)
        0xA1, 0x01,        # Collection (Application)
        0x15, 0x00,        #   Logical Minimum (0)
        0x26, 0xFF, 0x00,  #   Logical Maximum (255)
        0x75, 0x08,        #   Report Size (8)
        0x95, REPORT_LEN,  #   Report Count (64)
        0x09, 0x01,        #   Usage (1)
        0x81, 0x02,        #   Input (Data, Variable, Absolute)
        0x09, 0x01,        #   Usage (1)
        0x91, 0x02,        #   Output (Data, Variable, Absolute)
        0xC0,              # End Collection
    ])


# ---------------------------------------------------------------------------
# /dev/uhid plumbing (see linux/uhid.h; struct uhid_event is packed)
# ---------------------------------------------------------------------------

UHID_PATH = "/dev/uhid"
UHID_DESTROY = 1
UHID_START = 2
UHID_STOP = 3
UHID_OPEN = 4
UHID_CLOSE = 5
UHID_OUTPUT = 6
UHID_GET_REPORT = 9
UHID_GET_REPORT_REPLY = 10
UHID_CREATE2 = 11
UHID_INPUT2 = 12
UHID_SET_REPORT = 13
UHID_SET_REPORT_REPLY = 14

UHID_DATA_MAX = 4096
UHID_EVENT_SIZE = 4 + 128 + 64 + 64 + 2 + 2 + 4 * 4 + UHID_DATA_MAX  # 4376
BUS_USB = 0x03
RTYPE_NAMES: dict[int, str] = {0: "feature", 1: "output", 2: "input"}


def uhid_access(path: str = UHID_PATH) -> str:
    """Whether the uhid node can be opened: "ok", "missing" or "denied".

    Only looks at the permissions; nothing is opened and no device is created.
    """
    if not os.path.exists(path):
        return "missing"
    return "ok" if os.access(path, os.R_OK | os.W_OK) else "denied"


def build_create2(name: str, phys: str, uniq: str, vendor: int, product: int,
                  version: int, descriptor: bytes) -> bytes:
    return struct.pack("<I128s64s64sHHIIII", UHID_CREATE2,
                       name.encode()[:127], phys.encode()[:63], uniq.encode()[:63],
                       len(descriptor), BUS_USB, vendor, product, version, 0) + descriptor


def build_input2(data: bytes) -> bytes:
    return struct.pack("<IH", UHID_INPUT2, len(data)) + bytes(data)


def build_get_report_reply(request_id: int, err: int, data: bytes = b"") -> bytes:
    return struct.pack("<IIHH", UHID_GET_REPORT_REPLY, request_id, err, len(data)) + bytes(data)


def build_set_report_reply(request_id: int, err: int) -> bytes:
    return struct.pack("<IIH", UHID_SET_REPORT_REPLY, request_id, err)


def parse_event(buf: bytes) -> tuple[int, dict[str, Any]]:
    """Decode a kernel->user uhid event into (type, dict)."""
    ev_type = int(struct.unpack_from("<I", buf, 0)[0])
    info: dict[str, Any] = {}
    if ev_type == UHID_START and len(buf) >= 12:
        info["dev_flags"] = struct.unpack_from("<Q", buf, 4)[0]
    elif ev_type == UHID_OUTPUT:
        size, rtype = struct.unpack_from("<HB", buf, 4 + UHID_DATA_MAX)
        info.update(data=bytes(buf[4:4 + size]), rtype=rtype)
    elif ev_type == UHID_GET_REPORT:
        info["id"], info["rnum"], info["rtype"] = struct.unpack_from("<IBB", buf, 4)
    elif ev_type == UHID_SET_REPORT:
        req_id, rnum, rtype, size = struct.unpack_from("<IBBH", buf, 4)
        info.update(id=req_id, rnum=rnum, rtype=rtype, data=bytes(buf[12:12 + size]))
    return ev_type, info


class UhidNode:
    """One virtual HID interface backed by an open /dev/uhid descriptor."""

    def __init__(self, label: str, usage: int, product_id: int, device: BragiDevice,
                 log: LogCallback, uhid_path: str = UHID_PATH) -> None:
        self.label = label
        self.usage = usage
        self.device = device
        self.log = log
        self.open_count = 0  # how many clients currently hold the HID device open
        self.fd = os.open(uhid_path, os.O_RDWR | os.O_CLOEXEC)
        self.created = False
        try:
            os.write(self.fd, build_create2(
                PRODUCT_NAME, "quadsens/input%d" % (usage - 1), SERIAL,
                VENDOR_ID, product_id, 0x0100, report_descriptor(usage)))
            self.created = True
        except OSError:
            os.close(self.fd)
            self.fd = -1
            raise

    def send_input(self, report: bytes) -> None:
        os.write(self.fd, build_input2(report))

    def _command(self, raw: bytes, source: str) -> None:
        self.device.current_node = self.label
        self.device.current_source = source
        reply = self.device.handle_output_report(raw)
        if reply is not None:
            self.send_input(reply)

    def pump(self) -> None:
        """Handle one pending event from the kernel."""
        buf = os.read(self.fd, UHID_EVENT_SIZE + 64)
        if not buf:
            raise OSError(errno.EIO, "uhid descriptor returned end of file")
        if len(buf) < 4:
            return
        ev_type, info = parse_event(buf)
        if ev_type == UHID_START:
            self.log({"node": self.label, "event": "START", "dev_flags": info.get("dev_flags")})
        elif ev_type == UHID_STOP:
            self.log({"node": self.label, "event": "STOP"})
        elif ev_type == UHID_OPEN:
            self.open_count += 1
            self.log({"node": self.label, "event": "OPEN"})
        elif ev_type == UHID_CLOSE:
            self.open_count = max(0, self.open_count - 1)
            self.log({"node": self.label, "event": "CLOSE"})
        elif ev_type == UHID_OUTPUT:
            self._command(info["data"], "OUTPUT/" + RTYPE_NAMES.get(info["rtype"], "?"))
        elif ev_type == UHID_SET_REPORT:
            # HidD_SetOutputReport / HIDIOCSOUTPUT style delivery. Acknowledge
            # first, then treat an output report exactly like UHID_OUTPUT.
            os.write(self.fd, build_set_report_reply(info["id"], 0))
            if info["rtype"] == 1:
                self._command(info["data"], "SET_REPORT/output")
            else:
                self.log({"node": self.label, "event": "SET_REPORT",
                          "rtype": RTYPE_NAMES.get(info["rtype"], info["rtype"]),
                          "rnum": info["rnum"], "raw": info["data"].hex()})
        elif ev_type == UHID_GET_REPORT:
            # No feature reports exist; answer with an all-zero report so that
            # a probing client gets data instead of an I/O error.
            os.write(self.fd, build_get_report_reply(info["id"], 0, bytes(REPORT_LEN)))
            self.log({"node": self.label, "event": "GET_REPORT",
                      "rtype": RTYPE_NAMES.get(info["rtype"], info["rtype"]),
                      "rnum": info["rnum"]})
        else:
            self.log({"node": self.label, "event": "uhid_event_%d" % ev_type})

    def destroy(self) -> None:
        if self.fd < 0:
            return
        try:
            if self.created:
                os.write(self.fd, struct.pack("<I", UHID_DESTROY))
        except OSError:
            pass
        os.close(self.fd)
        self.fd = -1


# ---------------------------------------------------------------------------
# Log formatting and tracking of the directional sensitivity writes
# ---------------------------------------------------------------------------

def format_entry(entry: LogEntry) -> str:
    """One-line rendering of a log entry."""
    stamp = time.strftime("%H:%M:%S", time.localtime(entry.get("t", time.time())))
    node = entry.get("node") or "-"
    if "cmd" not in entry:
        extra = {k: v for k, v in entry.items() if k not in ("t", "node", "event")}
        return "%s [%s] %s %s" % (stamp, node, entry.get("event"), extra or "")
    skip = ("t", "node", "cmd", "request", "response", "raw", "highlight", "via")
    fields = " ".join("%s=%s" % (k, v) for k, v in entry.items() if k not in skip)
    return "%s [%s] %-13s %s | rx %s" % (
        stamp, node, entry["cmd"], fields, entry.get("raw", entry.get("request", "")))


@dataclass
class DirectionalWrites:
    """The directional sensitivity values a client wrote during this session."""

    x_pos: float | None = None
    x_neg: float | None = None
    y_pos: float | None = None
    y_neg: float | None = None
    enabled: bool | None = None
    updated: float | None = None  # unix time of the last write

    _FIELDS = {P_DIR_SENS_X_POS: "x_pos", P_DIR_SENS_X_NEG: "x_neg",
               P_DIR_SENS_Y_POS: "y_pos", P_DIR_SENS_Y_NEG: "y_neg"}

    def feed(self, entry: LogEntry) -> bool:
        """Record a log entry. Returns True when it was a directional write."""
        if entry.get("cmd") != "SET":
            return False
        prop_id = entry.get("id")
        value = entry.get("value")
        if not isinstance(prop_id, int) or not isinstance(value, int):
            return False
        if prop_id in self._FIELDS:
            setattr(self, self._FIELDS[prop_id], bits_to_float(value))
        elif prop_id == P_DIR_SENS_ENABLED:
            self.enabled = bool(value)
        else:
            return False
        self.updated = float(entry.get("t", time.time()))
        return True

    def scalars(self) -> Scalars | None:
        """All four scalars, or None while at least one was not written yet."""
        if (self.x_pos is None or self.x_neg is None
                or self.y_pos is None or self.y_neg is None):
            return None
        return Scalars(self.x_pos, self.x_neg, self.y_pos, self.y_neg)


# ---------------------------------------------------------------------------
# Session: device plus uhid nodes, driven by whoever owns the event loop
# ---------------------------------------------------------------------------

class EmulatorError(Exception):
    """The virtual device could not be created or stopped working."""


class EmulatorSession:
    """Owns the protocol state and the uhid descriptors of one virtual mouse.

    The owner waits for the descriptors in ``fds`` to become readable (poll,
    QSocketNotifier, ...) and calls ``pump(fd)`` for each. Nothing here blocks.
    """

    def __init__(self, product_id: int = DEFAULT_PRODUCT_ID, state_path: str | None = None,
                 on_log: LogCallback | None = None, layout: str = "split",
                 session_header: str = "echo", uhid_path: str = UHID_PATH) -> None:
        if layout not in ("split", "command-only"):
            raise ValueError("unknown layout %r" % layout)
        self.product_id = product_id
        self.layout = layout
        self.uhid_path = uhid_path
        self._on_log: LogCallback = on_log or _ignore
        self.device = BragiDevice(product_id=product_id, state_path=state_path,
                                  log=self._log, session_header=session_header)
        self._nodes: dict[int, UhidNode] = {}

    def _log(self, entry: LogEntry) -> None:
        entry = dict(entry)
        entry.setdefault("t", round(time.time(), 3))
        if "cmd" in entry:
            entry.setdefault("node", self.device.current_node)
            entry.setdefault("via", self.device.current_source)
        self._on_log(entry)

    @property
    def running(self) -> bool:
        return bool(self._nodes)

    @property
    def fds(self) -> list[int]:
        return list(self._nodes)

    @property
    def open_count(self) -> int:
        """Number of client handles currently open on the virtual device."""
        return sum(node.open_count for node in self._nodes.values())

    def start(self) -> None:
        """Create the virtual HID device(s). Raises EmulatorError on failure."""
        if self._nodes:
            return
        usages = (1, 2) if self.layout == "split" else (1,)
        nodes: list[UhidNode] = []
        try:
            for usage in usages:
                label = "cmd" if usage == 1 else "notif"
                nodes.append(UhidNode(label, usage, self.product_id, self.device,
                                      self._log, self.uhid_path))
        except OSError as exc:
            for node in nodes:
                node.destroy()
            hint = ""
            if exc.errno in (errno.EACCES, errno.EPERM):
                hint = " (no permission: install the udev rule, see README.md)"
            elif exc.errno == errno.ENOENT:
                hint = " (is the uhid kernel module loaded?)"
            raise EmulatorError("cannot create uhid device: %s%s" % (exc, hint)) from exc
        self._nodes = {node.fd: node for node in nodes}
        self._log({"event": "created", "devices": len(nodes),
                   "id": "%04X:%04X" % (VENDOR_ID, self.product_id)})

    def pump(self, fd: int) -> None:
        """Handle one pending kernel event on ``fd``. Raises OSError on I/O failure."""
        node = self._nodes.get(fd)
        if node is not None:
            node.pump()

    def stop(self) -> None:
        """Destroy the virtual device(s) and save the state. Safe to call twice."""
        if not self._nodes:
            return
        for node in self._nodes.values():
            node.destroy()
        self._nodes = {}
        self.device.save()
        self._log({"event": "destroyed"})


# ---------------------------------------------------------------------------
# Headless entry point: python -m quadsens.emulator
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the virtual mouse without the GUI.")
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=DEFAULT_PRODUCT_ID,
                    help="USB product id (default 0x%04X)" % DEFAULT_PRODUCT_ID)
    ap.add_argument("--state", default=None, help="JSON file holding properties and files")
    ap.add_argument("--layout", choices=("split", "command-only"), default="split")
    args = ap.parse_args(argv)

    def show(entry: LogEntry) -> None:
        print(format_entry(entry), flush=True)
        if "highlight" in entry:
            print(">>> DIRECTIONAL WRITE: %s" % entry["highlight"], flush=True)

    session = EmulatorSession(args.pid, args.state, show, args.layout)
    stop = {"flag": False}

    def on_signal(_signum: int, _frame: object) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    try:
        session.start()
    except EmulatorError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    poller = select.poll()
    for fd in session.fds:
        poller.register(fd, select.POLLIN)
    exit_code = 0
    try:
        while not stop["flag"]:
            for fd, events in poller.poll(500):
                if events & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                    raise OSError(errno.EIO, "uhid descriptor closed by the kernel")
                session.pump(fd)
    except OSError as exc:
        print("error: uhid I/O failed: %s" % exc, file=sys.stderr)
        exit_code = 1
    finally:
        session.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
