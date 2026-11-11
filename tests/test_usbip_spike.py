"""Tests of the USB/IP spike (tools/usbip_spike). No root, no driver, no network
beyond the loopback interface: the client is the small one that ships with the
spike. Platform-independent; they also run on Windows in CI.
"""

from __future__ import annotations

import io
import json
import struct
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "usbip_spike"))

import usbip_spike as spike  # noqa: E402

from quadsens import emulator  # noqa: E402

# From the kernel's Documentation/usb/usbip_protocol.rst, section "EXAMPLE":
# an interrupt IN and an interrupt OUT request of a HID device on the wire.
DOC_CMD_INTR_IN = bytes.fromhex(
    "00000001 00000d05 0001000f 00000001 00000001 00000200 00000040 ffffffff"
    " 00000000 00000004 00000000 00000000".replace(" ", ""))
DOC_CMD_INTR_OUT_HEADER = bytes.fromhex(
    "00000001 00000d06 0001000f 00000000 00000001 00000000 00000040 ffffffff"
    " 00000000 00000004 00000000 00000000".replace(" ", ""))
DOC_RET_HEADER_LENGTH = 48


def setup(request_type: int, request: int, value: int, index: int, length: int) -> bytes:
    return struct.pack("<BBHHH", request_type, request, value, index, length)


class WireFormatTest(unittest.TestCase):
    def test_operation_headers(self) -> None:
        self.assertEqual(spike.pack_op(spike.OP_REQ_DEVLIST), bytes.fromhex("0111800500000000"))
        self.assertEqual(spike.pack_op(spike.OP_REP_DEVLIST), bytes.fromhex("0111000500000000"))
        self.assertEqual(spike.pack_op(spike.OP_REQ_IMPORT), bytes.fromhex("0111800300000000"))
        self.assertEqual(spike.pack_op(spike.OP_REP_IMPORT, 1), bytes.fromhex("0111000300000001"))
        self.assertEqual(spike.pack_string("1-1", 32), b"1-1" + bytes(29))
        self.assertEqual(len(spike.pack_string("x" * 400, 256)), 256)
        self.assertEqual(spike.pack_string("x" * 400, 256)[-1], 0)   # always terminated

    def test_urb_headers_are_48_bytes_big_endian(self) -> None:
        self.assertEqual(len(DOC_CMD_INTR_IN), 48)
        self.assertEqual(spike.HEADER_SIZE, 48)
        self.assertEqual(spike.BASIC.size + spike.SUBMIT_TAIL.size, 48)
        self.assertEqual(spike.BASIC.size + spike.RET_SUBMIT_TAIL.size, 48)
        self.assertEqual(spike.BASIC.size + spike.UNLINK_TAIL.size, 48)
        self.assertEqual(spike.BASIC.size + spike.RET_UNLINK_TAIL.size, 48)
        command, seqnum, devid, direction, ep = spike.BASIC.unpack_from(DOC_CMD_INTR_IN)
        self.assertEqual((command, seqnum, devid, direction, ep),
                         (spike.CMD_SUBMIT, 0x0D05, 0x0001000F, spike.DIR_IN, 1))
        flags, length, _start, _packets, interval, setup_bytes = spike.SUBMIT_TAIL.unpack_from(
            DOC_CMD_INTR_IN, spike.BASIC.size)
        self.assertEqual((flags, length, interval, setup_bytes), (0x200, 0x40, 4, bytes(8)))
        _c, seqnum, _d, direction, ep = spike.BASIC.unpack_from(DOC_CMD_INTR_OUT_HEADER)
        self.assertEqual((seqnum, direction, ep), (0x0D06, spike.DIR_OUT, 1))

    def test_replies(self) -> None:
        ret = spike.pack_ret_submit(0x0D05, 0, 4, b"\x01\x02\x03\x04")
        self.assertEqual(ret[:20], bytes.fromhex("00000003 00000d05 00000000 00000000 00000000"
                                                 .replace(" ", "")))
        self.assertEqual(ret[20:28], bytes.fromhex("0000000000000004"))   # status, actual
        self.assertEqual(ret[40:48], bytes(8))                            # padding
        self.assertEqual(ret[48:], b"\x01\x02\x03\x04")
        self.assertEqual(len(spike.pack_ret_submit(1, 0, 64)), DOC_RET_HEADER_LENGTH)
        stall = spike.pack_ret_submit(7, -spike.EPIPE)
        self.assertEqual(stall[20:24], bytes.fromhex("ffffffe0"))          # -32
        unlink = spike.pack_ret_unlink(9, -spike.ECONNRESET)
        self.assertEqual(len(unlink), 48)
        self.assertEqual(unlink[:8], bytes.fromhex("0000000400000009"))
        self.assertEqual(unlink[20:24], bytes.fromhex("ffffff98"))         # -104
        self.assertEqual(unlink[24:], bytes(24))
        cmd = spike.pack_cmd_unlink(10, 9)
        self.assertEqual((len(cmd), cmd[20:24]), (48, bytes.fromhex("00000009")))

    def test_client_request_matches_the_documented_one(self) -> None:
        mine = spike.pack_cmd_submit(0x0D05, spike.DIR_IN, 1, 0x40, interval=4,
                                     devid=0x0001000F)
        # Identical except for transfer_flags, which the documented capture has set.
        self.assertEqual(mine[:20], DOC_CMD_INTR_IN[:20])
        self.assertEqual(mine[24:], DOC_CMD_INTR_IN[24:])


class DescriptorTest(unittest.TestCase):
    def test_device_descriptor(self) -> None:
        device = spike.UsbDevice()
        raw = device.device_descriptor()
        self.assertEqual((len(raw), raw[0], raw[1]), (18, 18, 1))
        fields = struct.unpack("<BBHBBBBHHHBBBB", raw)
        self.assertEqual(fields[2], 0x0200)                    # USB 2.0
        self.assertEqual(fields[3:7], (0, 0, 0, 64))           # class per interface, EP0 64
        self.assertEqual(fields[7:9], (0x1B1C, 0x2B43))
        self.assertEqual(fields[10:14], (1, 2, 3, 1))          # strings, one configuration
        other = spike.UsbDevice(product_id=0x2B44)
        self.assertEqual(struct.unpack_from("<H", other.device_descriptor(), 10)[0], 0x2B44)

    def test_configuration_is_consistent(self) -> None:
        for layout, interfaces in ((spike.ONE_INTERFACE, 1), (spike.TWO_INTERFACES, 2)):
            device = spike.UsbDevice(layout=layout)
            raw = device.configuration_descriptor()
            total, count = struct.unpack_from("<HB", raw, 2)
            self.assertEqual((total, count), (len(raw), interfaces))
            self.assertEqual(len(raw), 9 + 32 * interfaces)
            offset, seen, endpoints, hid_lengths = 0, [], [], []
            while offset < len(raw):
                length, kind = raw[offset], raw[offset + 1]
                self.assertGreater(length, 1)
                body = raw[offset:offset + length]
                seen.append(kind)
                if kind == spike.DT_INTERFACE:
                    self.assertEqual(tuple(body[4:8]), (2, 3, 0, 0))   # 2 endpoints, HID
                elif kind == spike.DT_HID:
                    self.assertEqual(body[6], spike.DT_REPORT)
                    hid_lengths.append(struct.unpack_from("<H", body, 7)[0])
                elif kind == spike.DT_ENDPOINT:
                    self.assertEqual(body[3], 3)                        # interrupt
                    self.assertEqual(struct.unpack_from("<H", body, 4)[0], 64)
                    self.assertGreaterEqual(body[6], 1)
                    endpoints.append(body[2])
                offset += length
            self.assertEqual(offset, len(raw))
            self.assertEqual(seen, [2] + [4, 0x21, 5, 5] * interfaces)
            self.assertEqual(endpoints, [0x81, 0x01, 0x82, 0x02][:2 * interfaces])
            self.assertEqual(len(set(endpoints)), len(endpoints))
            self.assertEqual(hid_lengths, [len(device.report_descriptor(i))
                                           for i in range(interfaces)])

    def test_report_descriptor_is_the_uhid_one(self) -> None:
        device = spike.UsbDevice(layout=spike.TWO_INTERFACES)
        self.assertEqual(device.report_descriptor(0), emulator.report_descriptor(1))
        self.assertEqual(device.report_descriptor(1), emulator.report_descriptor(2))
        self.assertEqual(device.report_descriptor(0)[:3], bytes([0x06, 0x42, 0xFF]))

    def test_strings(self) -> None:
        device = spike.UsbDevice(serial="TEST123")
        self.assertEqual(device.string(0), bytes([4, 3, 0x09, 0x04]))
        for index, text in ((1, "Corsair"), (2, emulator.PRODUCT_NAME), (3, "TEST123")):
            raw = device.string(index)
            assert raw is not None
            self.assertEqual((raw[0], raw[1]), (len(raw), 3))
            self.assertEqual(raw[2:].decode("utf-16-le"), text)
        self.assertIsNone(device.string(9))
        self.assertEqual(spike.UsbDevice().serial, emulator.SERIAL)


class ControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.entries: list[dict] = []
        self.device = spike.UsbDevice(layout=spike.TWO_INTERFACES, log=self.entries.append)

    def test_descriptors_are_cut_to_the_requested_length(self) -> None:
        status, data = self.device.control(setup(0x80, 6, 0x0100, 0, 8), b"")
        self.assertEqual((status, data), (0, self.device.device_descriptor()[:8]))
        status, data = self.device.control(setup(0x80, 6, 0x0200, 0, 9), b"")
        self.assertEqual(len(data), 9)
        status, data = self.device.control(setup(0x80, 6, 0x0200, 0, 0xFFFF), b"")
        self.assertEqual(data, self.device.configuration_descriptor())
        status, data = self.device.control(setup(0x81, 6, 0x2200, 1, 255), b"")
        self.assertEqual((status, data), (0, emulator.report_descriptor(2)))
        status, data = self.device.control(setup(0x81, 6, 0x2100, 0, 9), b"")
        self.assertEqual((status, data), (0, self.device.hid_descriptor(0)))

    def test_standard_and_hid_requests(self) -> None:
        self.assertEqual(self.device.control(setup(0x80, 8, 0, 0, 1), b""), (0, b"\0"))
        self.assertEqual(self.device.control(setup(0x00, 9, 1, 0, 0), b""), (0, b""))
        self.assertEqual(self.device.control(setup(0x80, 8, 0, 0, 1), b""), (0, b"\1"))
        self.assertEqual(self.device.control(setup(0x80, 0, 0, 0, 2), b""), (0, b"\0\0"))
        self.assertEqual(self.device.control(setup(0x01, 11, 0, 1, 0), b""), (0, b""))
        self.assertEqual(self.device.control(setup(0x81, 10, 0, 0, 1), b""), (0, b"\0"))
        self.assertEqual(self.device.control(setup(0x21, 10, 0, 0, 0), b""), (0, b""))   # SET_IDLE
        self.assertEqual(self.device.control(setup(0xA1, 2, 0, 0, 1), b""), (0, b"\0"))  # GET_IDLE
        self.assertEqual(self.device.control(setup(0x21, 11, 1, 0, 0), b""), (0, b""))   # PROTOCOL
        status, data = self.device.control(setup(0xA1, 1, 0x0100, 0, 64), b"")           # GET_REPORT
        self.assertEqual((status, data), (0, bytes(64)))
        names = [entry["name"] for entry in self.entries]
        for expected in ("GET_CONFIGURATION", "SET_CONFIGURATION", "GET_STATUS",
                         "SET_INTERFACE", "HID SET_IDLE", "HID GET_REPORT"):
            self.assertIn(expected, names)

    def test_unsupported_requests_stall_instead_of_failing(self) -> None:
        for request in (setup(0x80, 6, 0x0600, 0, 10),     # device qualifier
                        setup(0x80, 6, 0x0F00, 0, 5),      # BOS
                        setup(0x80, 6, 0x0309, 0x0409, 255),   # unknown string
                        setup(0x80, 6, 0x0201, 0, 9),      # second configuration
                        setup(0x81, 6, 0x2200, 7, 255),    # no such interface
                        setup(0xC0, 0x33, 0, 0, 16),       # vendor request
                        setup(0x00, 9, 5, 0, 0),           # configuration 5
                        setup(0x21, 0x55, 0, 0, 0)):       # unknown class request
            status, data = self.device.control(request, b"")
            self.assertEqual((status, data), (-spike.EPIPE, b""), request.hex())
        self.assertEqual(self.entries[-1]["status"], "STALL")

    def test_set_report_is_an_output_report(self) -> None:
        command = bytes([0, 1, 0, emulator.CMD_SESSION, 1, 0x40, 0xE2, 0x01, 0x00]).ljust(64, b"\0")
        status, _ = self.device.control(setup(0x21, 9, 0x0200, 0, 64), command)
        self.assertEqual(status, 0)
        reply = self.device.next_input(0)
        assert reply is not None
        self.assertEqual((len(reply), reply[3], reply[4]), (64, emulator.CMD_SESSION, 0))
        status, data = self.device.control(setup(0xA1, 1, 0x0100, 0, 64), b"")
        self.assertEqual(data, reply)                       # GET_REPORT: the last input report


class SessionTest(unittest.TestCase):
    """A whole conversation, bytes in and bytes out, without a socket."""

    def setUp(self) -> None:
        self.entries: list[dict] = []
        self.device = spike.UsbDevice(log=self.entries.append)
        self.session = spike.Session(self.device, self.entries.append)
        self.seq = 0

    def attach(self) -> None:
        reply = self.session.feed(spike.pack_op(spike.OP_REQ_IMPORT)
                                  + spike.pack_string("1-1", 32))
        self.assertEqual(reply[:8], spike.pack_op(spike.OP_REP_IMPORT))
        self.assertEqual(len(reply), 8 + 0x138)

    def urb(self, direction: int, ep: int, length: int, setup_bytes: bytes = b"",
            data: bytes = b"") -> bytes:
        self.seq += 1
        return self.session.feed(spike.pack_cmd_submit(self.seq, direction, ep, length,
                                                       setup_bytes, data))

    def parse(self, raw: bytes) -> list[tuple[int, int, int, bytes]]:
        """(command, seqnum, status, data) of every reply in ``raw``."""
        out = []
        while raw:
            command, seqnum = struct.unpack_from(">II", raw)
            status, actual = struct.unpack_from(">ii", raw, 20)
            # Data follows only where the server sends it: IN transfers.
            take = actual if command == spike.RET_SUBMIT and len(raw) >= 48 + actual \
                and seqnum in self.in_requests else 0
            out.append((command, seqnum, status, raw[48:48 + take]))
            raw = raw[48 + take:]
        return out

    in_requests: set[int] = set()

    def test_device_list(self) -> None:
        reply = self.session.feed(spike.pack_op(spike.OP_REQ_DEVLIST))
        self.assertEqual(reply[:8], spike.pack_op(spike.OP_REP_DEVLIST))
        self.assertEqual(struct.unpack_from(">I", reply, 8)[0], 1)
        self.assertEqual(len(reply), 12 + 0x138 + 4)
        record = reply[12:]
        self.assertEqual(record[:256].split(b"\0")[0], spike.DEVICE_PATH.encode())
        self.assertEqual(record[256:288], b"1-1" + bytes(29))
        busnum, devnum, speed, vendor, product, bcd = struct.unpack_from(">IIIHHH", record, 288)
        self.assertEqual((busnum, devnum, speed), (1, 2, 2))            # full speed
        self.assertEqual((vendor, product, bcd), (0x1B1C, 0x2B43, 0x0100))
        self.assertEqual(tuple(record[306:312]), (0, 0, 0, 1, 1, 1))
        self.assertEqual(record[312:316], bytes([3, 0, 0, 0]))          # HID, padding
        self.assertFalse(self.session.attached)

    def test_import_of_another_busid_is_refused(self) -> None:
        reply = self.session.feed(spike.pack_op(spike.OP_REQ_IMPORT)
                                  + spike.pack_string("3-2", 32))
        self.assertEqual(reply, spike.pack_op(spike.OP_REP_IMPORT, 1))
        self.assertFalse(self.session.attached)

    def test_enumeration_as_hosts_do_it(self) -> None:
        self.attach()
        # Linux: device 18, config 9, config full, strings, SET_CONFIGURATION,
        # SET_IDLE, report descriptor. Windows asks for the device descriptor
        # with length 64 first and for the qualifier; both are covered.
        requests = [
            (setup(0x80, 6, 0x0100, 0, 64), 18), (setup(0x80, 6, 0x0100, 0, 18), 18),
            (setup(0x80, 6, 0x0600, 0, 10), None), (setup(0x80, 6, 0x0200, 0, 9), 9),
            (setup(0x80, 6, 0x0200, 0, 255), 41), (setup(0x80, 6, 0x0300, 0, 255), 4),
            (setup(0x80, 6, 0x0302, 0x0409, 255), None), (setup(0x80, 6, 0x0303, 0x0409, 255), None),
            (setup(0x80, 6, 0x0F00, 0, 5), None), (setup(0x00, 9, 1, 0, 0), 0),
            (setup(0x21, 10, 0, 0, 0), 0),
            (setup(0x81, 6, 0x2200, 0, 91), len(emulator.report_descriptor(1))),
        ]
        for request, expected in requests:
            to_host = bool(request[0] & 0x80)
            reply = self.urb(spike.DIR_IN if to_host else spike.DIR_OUT, 0,
                             struct.unpack_from("<H", request, 6)[0], request)
            command, seqnum = struct.unpack_from(">II", reply)
            status, actual = struct.unpack_from(">ii", reply, 20)
            self.assertEqual((command, seqnum), (spike.RET_SUBMIT, self.seq))
            self.assertEqual(len(reply), 48 + (actual if to_host else 0))
            if request[3] in (0x06, 0x0F):
                self.assertEqual(status, -spike.EPIPE)
            elif expected is not None:
                self.assertEqual((status, actual), (0, expected), request.hex())
        self.assertEqual(self.device.configuration, 1)

    def test_out_report_completes_a_waiting_in_request(self) -> None:
        self.attach()
        self.assertEqual(self.urb(spike.DIR_IN, 1, 64), b"")          # nothing to send yet
        waiting = self.seq
        start = bytes([0, 1, 0, emulator.CMD_SESSION, 1, 0x40, 0xE2, 0x01, 0x00]).ljust(64, b"\0")
        reply = self.urb(spike.DIR_OUT, 1, 64, data=start)
        self.assertEqual(len(reply), 48 + 48 + 64)
        first, second = reply[:48], reply[48:]
        self.assertEqual(struct.unpack_from(">II", first), (spike.RET_SUBMIT, self.seq))
        self.assertEqual(struct.unpack_from(">ii", first, 20), (0, 64))   # OUT: no data follows
        self.assertEqual(struct.unpack_from(">II", second), (spike.RET_SUBMIT, waiting))
        self.assertEqual(struct.unpack_from(">ii", second, 20), (0, 64))
        report = second[48:]
        self.assertEqual((report[3], report[4]), (emulator.CMD_SESSION, emulator.STATUS_OK))

    def test_reply_waits_for_the_in_request_and_order_is_kept(self) -> None:
        self.attach()
        get = bytes([0, 1, 0, emulator.CMD_GET]) + struct.pack("<H", 0x13)
        for _ in range(3):
            reply = self.urb(spike.DIR_OUT, 1, 64, data=get.ljust(64, b"\0"))
            self.assertEqual(len(reply), 48)                      # only the acknowledgement
        self.assertEqual(len(self.device.inputs[0]), 3)
        for _ in range(3):
            reply = self.urb(spike.DIR_IN, 1, 64)
            self.assertEqual(struct.unpack_from(">II", reply), (spike.RET_SUBMIT, self.seq))
            self.assertEqual(len(reply), 48 + 64)
        self.assertEqual(self.urb(spike.DIR_IN, 1, 64), b"")         # queue empty: waits
        short = self.urb(spike.DIR_OUT, 1, 64, data=get.ljust(64, b"\0"))
        self.assertEqual(len(short), 48 + 48 + 64)

    def test_directional_write_is_decoded_and_flagged(self) -> None:
        self.attach()
        for prop, value in ((0x125, 1.011), (0x126, 0.991), (0x127, 1.006), (0x128, 0.996)):
            request = bytes([0, 1, 0, emulator.CMD_SET]) + struct.pack("<Hf", prop, value)
            self.urb(spike.DIR_OUT, 1, 64, data=request.ljust(64, b"\0"))
        writes = emulator.DirectionalWrites()
        flagged = [entry for entry in self.entries if "highlight" in entry]
        for entry in self.entries:
            writes.feed(entry)
        self.assertEqual(len(flagged), 4)
        scalars = writes.scalars()
        assert scalars is not None
        for got, expected in zip(scalars.as_tuple(), (1.011, 0.991, 1.006, 0.996)):
            self.assertAlmostEqual(got, expected, places=6)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            logger = spike.Logger()
            logger(flagged[0])
        self.assertIn("### DIRECTIONAL VALUE WRITTEN:", buffer.getvalue())
        self.assertIn("#" * 78, buffer.getvalue())
        self.assertEqual(logger.directional, 1)

    def test_unlink(self) -> None:
        self.attach()
        self.urb(spike.DIR_IN, 1, 64)
        pending = self.seq
        reply = self.session.feed(spike.pack_cmd_unlink(50, pending))
        self.assertEqual(reply, spike.pack_ret_unlink(50, -spike.ECONNRESET))
        self.assertEqual(len(self.session.pending[1]), 0)
        # The unlinked request is gone: a report that comes now waits for the next IN.
        get = bytes([0, 1, 0, emulator.CMD_GET]) + struct.pack("<H", 0x13)
        self.assertEqual(len(self.urb(spike.DIR_OUT, 1, 64, data=get.ljust(64, b"\0"))), 48)
        # Unlinking something that was answered already reports status 0.
        self.assertEqual(self.session.feed(spike.pack_cmd_unlink(51, pending)),
                         spike.pack_ret_unlink(51, 0))
        self.assertEqual(self.session.feed(spike.pack_cmd_unlink(52, 9999)),
                         spike.pack_ret_unlink(52, 0))

    def test_messages_may_arrive_in_pieces(self) -> None:
        message = spike.pack_op(spike.OP_REQ_IMPORT) + spike.pack_string("1-1", 32)
        get = bytes([0, 1, 0, emulator.CMD_GET]) + struct.pack("<H", 0x13)
        message += spike.pack_cmd_submit(1, spike.DIR_OUT, 1, 64, data=get.ljust(64, b"\0"))
        message += spike.pack_cmd_submit(2, spike.DIR_IN, 1, 64)
        out = b""
        for index in range(0, len(message), 7):
            out += self.session.feed(message[index:index + 7])
        self.assertEqual(len(out), 8 + 0x138 + 48 + 48 + 64)
        self.assertEqual(self.session.buffer, b"")

    def test_unknown_endpoint_and_short_report(self) -> None:
        self.attach()
        reply = self.urb(spike.DIR_OUT, 5, 4, data=b"abcd")
        self.assertEqual(struct.unpack_from(">i", reply, 20)[0], -spike.EPIPE)
        reply = self.urb(spike.DIR_IN, 2, 64)                  # one-interface layout: no EP 2
        self.assertEqual(struct.unpack_from(">i", reply, 20)[0], -spike.EPIPE)
        reply = self.urb(spike.DIR_OUT, 1, 2, data=b"\0\1")    # too short for a command
        self.assertEqual(struct.unpack_from(">ii", reply, 20), (0, 2))
        self.assertEqual(self.urb(spike.DIR_OUT, 1, 0), spike.pack_ret_submit(self.seq, 0, 0))

    def test_malformed_input_is_a_protocol_error_not_a_crash(self) -> None:
        for garbage in (b"GET / HTTP/1.1\r\n\r\n", bytes.fromhex("0110800500000000"),
                        bytes.fromhex("0111999900000000")):
            session = spike.Session(spike.UsbDevice())
            with self.assertRaises(spike.ProtocolError):
                session.feed(garbage)
        self.attach()
        with self.assertRaises(spike.ProtocolError):
            self.session.feed(struct.pack(">IIIII", 77, 1, 0, 0, 0) + bytes(28))
        fresh = spike.Session(spike.UsbDevice())
        fresh.feed(spike.pack_op(spike.OP_REQ_IMPORT) + spike.pack_string("1-1", 32))
        with self.assertRaises(spike.ProtocolError):
            fresh.feed(spike.BASIC.pack(spike.CMD_SUBMIT, 1, 0, spike.DIR_OUT, 1)
                       + spike.SUBMIT_TAIL.pack(0, -5, 0, 0, 0, bytes(8)))

    def test_two_interface_layout(self) -> None:
        device = spike.UsbDevice(layout=spike.TWO_INTERFACES)
        session = spike.Session(device)
        listed = session.feed(spike.pack_op(spike.OP_REQ_DEVLIST))
        self.assertEqual(len(listed), 12 + 0x138 + 8)
        self.assertEqual(listed[12 + 0x137], 2)
        session = spike.Session(device)
        session.feed(spike.pack_op(spike.OP_REQ_IMPORT) + spike.pack_string("1-1", 32))
        get = bytes([0, 1, 0, emulator.CMD_GET]) + struct.pack("<H", 0x13)
        session.feed(spike.pack_cmd_submit(1, spike.DIR_OUT, 2, 64, data=get.ljust(64, b"\0")))
        # The answer comes back on the interface the command arrived on.
        self.assertEqual(session.feed(spike.pack_cmd_submit(2, spike.DIR_IN, 1, 64)), b"")
        self.assertEqual(len(session.feed(spike.pack_cmd_submit(3, spike.DIR_IN, 2, 64))), 112)


class ServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.entries: list[dict] = []
        self.device = spike.UsbDevice(log=self.entries.append)
        self.server = spike.Server(self.device, self.entries.append, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.address[:2]

    def tearDown(self) -> None:
        self.server.stop()
        self.thread.join(timeout=30)

    def messages(self) -> list[str]:
        return [entry["message"] for entry in self.entries if entry.get("event") == "server"]

    def wait_for(self, text: str) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if any(text in message for message in self.messages()):
                return
            time.sleep(0.02)
        self.fail("no %r in %r" % (text, self.messages()))

    def test_attach_detach_and_attach_again(self) -> None:
        self.assertEqual(self.server.address[0], "127.0.0.1")
        client = spike.Client(self.host, self.port)
        self.assertEqual(client.devlist()["busid"], "1-1")
        client.close()
        for _round in range(2):
            client = spike.Client(self.host, self.port)
            self.assertEqual(client.import_device(), 0)
            found = spike.enumerate_device(client)
            self.assertEqual(found["product"], emulator.PRODUCT_NAME)
            self.assertEqual(found["serial"], emulator.SERIAL)
            self.assertEqual(found["manufacturer"], "Corsair")
            client.close()
            self.wait_for("client detached")
            self.entries.clear()

    def test_garbage_does_not_stop_the_server(self) -> None:
        client = spike.Client(self.host, self.port)
        client.socket.sendall(b"\xff" * 64)
        self.wait_for("dropping")
        client.close()
        client = spike.Client(self.host, self.port)      # still serving
        self.assertEqual(client.import_device(), 0)
        client.close()

    def test_selftest_passes(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            ok = spike.selftest()
            both = spike.selftest(spike.TWO_INTERFACES)
        self.assertTrue(ok and both, buffer.getvalue())
        self.assertIn("SELFTEST PASS", buffer.getvalue())
        self.assertNotIn("FAILED", buffer.getvalue())


class CommandLineTest(unittest.TestCase):
    def test_selftest_option_and_json_log(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(spike.main(["--selftest"]), 0)
        self.assertIn("SELFTEST PASS", buffer.getvalue())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            logger = spike.Logger(str(path), quiet=True)
            device = spike.UsbDevice(log=logger)
            session = spike.Session(device, logger)
            session.feed(spike.pack_op(spike.OP_REQ_IMPORT) + spike.pack_string("1-1", 32))
            session.feed(spike.pack_cmd_submit(
                1, spike.DIR_IN, 0, 18, struct.pack("<BBHHH", 0x80, 6, 0x0100, 0, 18)))
            logger.close()
            lines = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([line["event"] for line in lines], ["usbip", "control"])
            self.assertEqual(lines[1]["name"], "GET_DESCRIPTOR DEVICE")
            self.assertTrue(all("t" in line for line in lines))

    def test_defaults_are_local_only(self) -> None:
        source = (ROOT / "tools" / "usbip_spike" / "usbip_spike.py").read_text()
        self.assertIn('"--listen", default="127.0.0.1"', source)
        self.assertEqual(spike.DEFAULT_PORT, 3240)
        self.assertIn("WARNING: listening on", source)
        for module in ("PySide6", "PyQt6", "fcntl", "requests"):
            self.assertNotIn("import " + module, source)


if __name__ == "__main__":
    unittest.main()
