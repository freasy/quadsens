"""Unit tests for the protocol handler in quadsens.emulator.

/dev/uhid is never opened; the session tests use a regular file in its place.
"""

from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest

from quadsens import emulator as su
from quadsens.model import Scalars


def frame(cmd: int, payload: bytes = b"", session: int = 0, want_reply: int = 1,
          sub: int = 0) -> bytes:
    return (bytes([sub, want_reply, session, cmd]) + bytes(payload)).ljust(su.REPORT_LEN, b"\0")


class ProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.entries: list[su.LogEntry] = []
        self.dev = su.BragiDevice(log=self.entries.append)

    def call(self, cmd: int, payload: bytes = b"", **kw: int) -> bytes:
        res = self.dev.handle(frame(cmd, payload, **kw))
        assert res is not None
        self.assertEqual(len(res), su.REPORT_LEN)
        return res

    # ---- session ---------------------------------------------------------

    def test_session_start_aimlabs_layout(self) -> None:
        token = struct.pack("<I", 1793800000)
        res = self.call(su.CMD_SESSION, b"\x01" + token, session=0)
        # Header: sub device, error code 0, request session echoed, command, status.
        self.assertEqual(res[:5], bytes([0, 0, 0, su.CMD_SESSION, su.STATUS_OK]))
        self.assertEqual(res[5:9], token)  # token echo (Aimlabs EchoOk)
        self.assertEqual(res[9], 1)        # new session id at response byte 9
        self.assertTrue(self.dev.session_active)
        # A second start hands out the next id.
        self.assertEqual(self.call(su.CMD_SESSION, b"\x01" + token)[9], 2)

    def test_session_stop(self) -> None:
        self.call(su.CMD_SESSION, b"\x01\0\0\0\0")
        res = self.call(su.CMD_SESSION, b"\x02\0\0\0\0\x01", session=1)
        self.assertEqual(res[2], 1)  # correlates with the request session
        self.assertEqual(res[4], su.STATUS_OK)
        self.assertEqual(res[9], 1)
        self.assertFalse(self.dev.session_active)

    def test_session_header_new_mode(self) -> None:
        dev = su.BragiDevice(session_header="new")
        res = dev.handle(frame(su.CMD_SESSION, b"\x01\0\0\0\0"))
        assert res is not None
        self.assertEqual(res[2], 1)
        self.assertEqual(res[9], 1)

    # ---- properties ------------------------------------------------------

    def test_get_property_defaults(self) -> None:
        res = self.call(su.CMD_GET, struct.pack("<H", su.P_VENDOR_ID), session=7)
        self.assertEqual(res[:5], bytes([0, 0, 7, su.CMD_GET, su.STATUS_OK]))
        self.assertEqual(struct.unpack_from("<I", res, 5)[0], 0x1B1C)
        res = self.call(su.CMD_GET, struct.pack("<H", su.P_PROTOCOL_VERSION))
        self.assertEqual(struct.unpack_from("<I", res, 5)[0], 0x0501)
        res = self.call(su.CMD_GET, struct.pack("<H", su.P_DIR_SENS_X_POS))
        self.assertEqual(struct.unpack_from("<f", res, 5)[0], 1.0)
        for prop, expected in ((0x03, 1), (0x1E, 1), (0x1F, 0x1F), (0x129, 0)):
            res = self.call(su.CMD_GET, struct.pack("<H", prop))
            self.assertEqual(struct.unpack_from("<I", res, 5)[0], expected)

    def test_get_unknown_property_returns_zero_ok(self) -> None:
        res = self.call(su.CMD_GET, struct.pack("<H", 0x7777))
        self.assertEqual(res[4], su.STATUS_OK)
        self.assertEqual(res[5:9], b"\0\0\0\0")
        self.assertFalse(self.entries[-1]["known"])

    def test_set_then_get_property(self) -> None:
        res = self.call(su.CMD_SET, struct.pack("<HI", su.P_DPI_1, 1234), session=3)
        self.assertEqual(res[:5], bytes([0, 0, 3, su.CMD_SET, su.STATUS_OK]))
        res = self.call(su.CMD_GET, struct.pack("<H", su.P_DPI_1))
        self.assertEqual(struct.unpack_from("<I", res, 5)[0], 1234)

    def test_get_multi(self) -> None:
        ids = [su.P_DPI_1, su.P_DPI_2, 0x7777]
        res = self.call(su.CMD_GET_MULTI, bytes([len(ids)]) + struct.pack("<3H", *ids))
        self.assertEqual(res[5], 3)
        self.assertEqual(struct.unpack_from("<HIHIHI", res, 6),
                         (su.P_DPI_1, 400, su.P_DPI_2, 800, 0x7777, 0))

    def test_four_scalar_write(self) -> None:
        """The client's write sequence: claim host, four floats, read back."""
        self.call(su.CMD_SESSION, b"\x01" + struct.pack("<I", 42))
        sid = self.dev.session_id
        self.call(su.CMD_SET, struct.pack("<HI", 0x03, 2), session=sid)  # ControlMode.Host
        values = {0x125: 1.0375, 0x126: 0.9625, 0x127: 1.125, 0x128: 0.875}
        for prop, value in values.items():
            res = self.call(su.CMD_SET, struct.pack("<Hf", prop, value), session=sid)
            self.assertEqual(res[:5], bytes([0, 0, sid, su.CMD_SET, su.STATUS_OK]))
            entry = self.entries[-1]
            self.assertAlmostEqual(entry["float"], value, places=6)
            self.assertIn("highlight", entry)
            self.assertIn("0x%X" % prop, entry["highlight"])
        for prop, value in values.items():
            res = self.call(su.CMD_GET, struct.pack("<H", prop), session=sid)
            self.assertEqual(res[5:9], struct.pack("<f", value))  # bit-exact round trip
        self.assertEqual(self.dev.props[0x03], 2)

    def test_enable_flag_write_is_highlighted(self) -> None:
        self.call(su.CMD_SET, struct.pack("<HI", 0x129, 1))
        self.assertIn("ENABLED", self.entries[-1]["highlight"])
        self.assertEqual(self.dev.props[0x129], 1)
        self.call(su.CMD_SET, struct.pack("<HI", su.P_DPI_1, 800))
        self.assertNotIn("highlight", self.entries[-1])

    # ---- files -----------------------------------------------------------

    def test_file_round_trip(self) -> None:
        file_id, index = 0x1234, 0
        content = bytes(range(256)) * 2  # 512 bytes: needs several WRITE packets

        self.assertEqual(self.call(su.CMD_CREATE_FILE, struct.pack("<H", file_id))[4], su.STATUS_OK)
        self.assertEqual(self.call(su.CMD_OPEN_FILE, struct.pack("<BH", index, file_id))[4],
                         su.STATUS_OK)
        # Opening the same buffer index twice is a state error.
        self.assertEqual(self.call(su.CMD_OPEN_FILE, struct.pack("<BH", index, file_id))[4],
                         su.STATUS_INVALID_STATE)

        first = su.REPORT_LEN - su.HEADER - 5
        res = self.call(su.CMD_WRITE_BEGIN,
                        struct.pack("<BI", index, len(content)) + content[:first])
        self.assertEqual(res[4], su.STATUS_OK)
        pos, step = first, su.REPORT_LEN - su.HEADER - 1
        while pos < len(content):
            res = self.call(su.CMD_WRITE, bytes([index]) + content[pos:pos + step])
            self.assertEqual(res[4], su.STATUS_OK)
            pos += step
        self.assertEqual(self.call(su.CMD_UNBIND, bytes([1, index]))[4], su.STATUS_OK)
        self.assertEqual(self.dev.files[file_id], content)

        # Read it back through a fresh buffer.
        self.assertEqual(self.call(su.CMD_OPEN_FILE, struct.pack("<BH", index, file_id))[4],
                         su.STATUS_OK)
        res = self.call(su.CMD_DESCRIBE, bytes([index]))
        self.assertEqual(res[4], su.STATUS_OK)
        self.assertEqual(struct.unpack_from("<I", res, 7)[0], len(content))
        got = b""
        while len(got) < len(content):
            res = self.call(su.CMD_READ, bytes([index]))
            self.assertEqual(res[4], su.STATUS_OK)
            got += res[5:5 + min(su.MAX_DATA, len(content) - len(got))]
        self.assertEqual(got, content)
        self.call(su.CMD_UNBIND, bytes([1, index]))
        self.assertEqual(self.dev.buffers, {})

        self.call(su.CMD_DELETE_FILE, struct.pack("<H", file_id))
        self.assertEqual(self.call(su.CMD_OPEN_FILE, struct.pack("<BH", index, file_id))[4],
                         su.STATUS_INVALID_ARG)

    def test_serial_file_default(self) -> None:
        self.call(su.CMD_OPEN_FILE, struct.pack("<BH", 0, su.FILE_SERIAL))
        res = self.call(su.CMD_READ, bytes([0]))
        length = res[7]
        self.assertEqual(res[8:8 + length].decode(), su.SERIAL)

    def test_file_commands_without_open_buffer(self) -> None:
        for cmd in (su.CMD_DESCRIBE, su.CMD_READ, su.CMD_WRITE_BEGIN, su.CMD_WRITE):
            self.assertEqual(self.call(cmd, bytes([5]))[4], su.STATUS_INVALID_STATE)

    # ---- misc ------------------------------------------------------------

    def test_unknown_command_status(self) -> None:
        res = self.call(0x63, b"\x01\x02", session=9, sub=1)
        self.assertEqual(res[:5], bytes([1, 0, 9, 0x63, su.STATUS_NOT_SUPPORTED]))

    def test_ping(self) -> None:
        self.assertEqual(self.call(su.CMD_PING)[:5], bytes([0, 0, 0, su.CMD_PING, 0]))

    def test_no_reply_flag_still_executes(self) -> None:
        res = self.dev.handle(frame(su.CMD_SET, struct.pack("<HI", su.P_DPI_2, 999), want_reply=0))
        self.assertIsNone(res)
        self.assertEqual(self.dev.props[su.P_DPI_2], 999)

    def test_report_id_prefix_is_stripped(self) -> None:
        bare = frame(su.CMD_GET, struct.pack("<H", su.P_PRODUCT_ID), session=4)
        expected = self.dev.handle_output_report(bare)
        assert expected is not None
        self.assertEqual(self.dev.handle_output_report(b"\0" + bare), expected)
        self.assertEqual(struct.unpack_from("<I", expected, 5)[0], 0x2B43)
        # Truncated frames are padded, runt frames are ignored.
        self.assertEqual(self.dev.handle_output_report(bare[:8]), expected)
        self.assertIsNone(self.dev.handle_output_report(b"\0\1"))

    def test_factory_reset(self) -> None:
        self.call(su.CMD_SET, struct.pack("<Hf", 0x125, 2.5))
        self.call(su.CMD_FACTORY_RESET)
        self.assertEqual(self.dev.props[0x125], su.float_to_bits(1.0))

    def test_state_persistence_and_pid_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            dev = su.BragiDevice(state_path=path)
            dev.handle(frame(su.CMD_SET, struct.pack("<Hf", 0x127, 1.25)))
            dev.handle(frame(su.CMD_CREATE_FILE, struct.pack("<H", 77)))
            with open(path, encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(saved["props"][str(0x127)], su.float_to_bits(1.25))
            self.assertIn("77", saved["files"])

            again = su.BragiDevice(product_id=0x2B44, state_path=path)
            self.assertEqual(again.props[0x127], su.float_to_bits(1.25))
            self.assertEqual(again.props[su.P_PRODUCT_ID], 0x2B44)
            self.assertIn(77, again.files)


class UhidEncodingTest(unittest.TestCase):
    """Byte layout of the uhid structures (no device is created)."""

    def test_descriptor(self) -> None:
        for usage in (1, 2):
            rd = su.report_descriptor(usage)
            self.assertEqual(rd[:5], bytes([0x06, 0x42, 0xFF, 0x09, usage]))
            self.assertEqual(rd[-1], 0xC0)
            self.assertNotIn(0x85, rd[::2])  # no Report ID item

    def test_create2_layout(self) -> None:
        rd = su.report_descriptor(1)
        ev = su.build_create2("n", "p", "u", 0x1B1C, 0x2B43, 0x0100, rd)
        self.assertEqual(struct.unpack_from("<I", ev, 0)[0], su.UHID_CREATE2)
        self.assertEqual(struct.unpack_from("<HHIII", ev, 4 + 256),
                         (len(rd), su.BUS_USB, 0x1B1C, 0x2B43, 0x0100))
        self.assertEqual(ev[4 + 256 + 20:], rd)
        self.assertLessEqual(len(ev), su.UHID_EVENT_SIZE)

    def test_output_event_parsing(self) -> None:
        payload = b"\0" + frame(su.CMD_PING)
        ev = bytearray(su.UHID_EVENT_SIZE)
        struct.pack_into("<I", ev, 0, su.UHID_OUTPUT)
        ev[4:4 + len(payload)] = payload
        struct.pack_into("<HB", ev, 4 + su.UHID_DATA_MAX, len(payload), 1)
        ev_type, info = su.parse_event(bytes(ev))
        self.assertEqual(ev_type, su.UHID_OUTPUT)
        self.assertEqual(info["data"], payload)
        self.assertEqual(info["rtype"], 1)

    def test_input2_layout(self) -> None:
        ev = su.build_input2(bytes(64))
        self.assertEqual(struct.unpack_from("<IH", ev, 0), (su.UHID_INPUT2, 64))
        self.assertEqual(len(ev), 6 + 64)


class DirectionalWritesTest(unittest.TestCase):
    def test_tracks_scalars_and_enable_flag(self) -> None:
        writes = su.DirectionalWrites()
        dev = su.BragiDevice(log=lambda entry: None if writes.feed(entry) else None)
        self.assertIsNone(writes.scalars())
        values = {0x125: 1.5, 0x126: 0.75, 0x127: 1.25, 0x128: 0.5}
        for prop, value in values.items():
            dev.handle(frame(su.CMD_SET, struct.pack("<Hf", prop, value)))
        self.assertEqual(writes.scalars(), Scalars(1.5, 0.75, 1.25, 0.5))
        self.assertIsNone(writes.enabled)
        dev.handle(frame(su.CMD_SET, struct.pack("<HI", 0x129, 1)))
        self.assertIs(writes.enabled, True)
        self.assertIsNotNone(writes.updated)

    def test_ignores_other_entries(self) -> None:
        writes = su.DirectionalWrites()
        self.assertFalse(writes.feed({"event": "OPEN"}))
        self.assertFalse(writes.feed({"cmd": "SET", "id": su.P_DPI_1, "value": 800}))
        self.assertFalse(writes.feed({"cmd": "GET", "id": 0x125, "value": 0}))
        self.assertIsNone(writes.updated)


class SessionTest(unittest.TestCase):
    """EmulatorSession against a regular file standing in for /dev/uhid."""

    def test_start_writes_create_and_stop_writes_destroy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "uhid")
            open(fake, "wb").close()
            entries: list[su.LogEntry] = []
            session = su.EmulatorSession(product_id=0x2B44, on_log=entries.append,
                                         layout="command-only", uhid_path=fake)
            self.assertFalse(session.running)
            session.start()
            self.assertTrue(session.running)
            self.assertEqual(len(session.fds), 1)
            self.assertEqual(session.open_count, 0)
            session.stop()
            session.stop()  # idempotent
            self.assertFalse(session.running)
            with open(fake, "rb") as fh:
                written = fh.read()
            create = su.build_create2(su.PRODUCT_NAME, "quadsens/input0", su.SERIAL,
                                      su.VENDOR_ID, 0x2B44, 0x0100, su.report_descriptor(1))
            self.assertEqual(written, create + struct.pack("<I", su.UHID_DESTROY))
            self.assertEqual([e["event"] for e in entries], ["created", "destroyed"])
            self.assertIn("t", entries[0])

    def test_missing_uhid_gives_readable_error(self) -> None:
        session = su.EmulatorSession(uhid_path="/nonexistent/uhid")
        with self.assertRaises(su.EmulatorError) as ctx:
            session.start()
        self.assertIn("uhid kernel module", str(ctx.exception))
        self.assertFalse(session.running)

    def test_pump_dispatches_output_and_counts_opens(self) -> None:
        r, w = os.pipe()
        try:
            replies: list[bytes] = []
            dev = su.BragiDevice()
            node = su.UhidNode.__new__(su.UhidNode)
            node.label, node.usage, node.device, node.log = "cmd", 1, dev, lambda e: None
            node.open_count, node.fd, node.created = 0, r, False
            node.send_input = replies.append  # type: ignore[method-assign]

            os.write(w, struct.pack("<I", su.UHID_OPEN))
            node.pump()
            self.assertEqual(node.open_count, 1)

            payload = b"\0" + frame(su.CMD_PING, session=5)
            ev = bytearray(su.UHID_EVENT_SIZE)
            struct.pack_into("<I", ev, 0, su.UHID_OUTPUT)
            ev[4:4 + len(payload)] = payload
            struct.pack_into("<HB", ev, 4 + su.UHID_DATA_MAX, len(payload), 1)
            os.write(w, bytes(ev))
            node.pump()
            self.assertEqual(replies[0][:5], bytes([0, 0, 5, su.CMD_PING, su.STATUS_OK]))

            os.write(w, struct.pack("<I", su.UHID_CLOSE))
            node.pump()
            self.assertEqual(node.open_count, 0)
            os.close(w)
            w = -1
            with self.assertRaises(OSError):
                node.pump()  # end of file must not spin silently
        finally:
            os.close(r)
            if w >= 0:
                os.close(w)

    def test_format_entry(self) -> None:
        line = su.format_entry({"t": 0.0, "node": "cmd", "cmd": "PING", "status": 0,
                                "request": "00"})
        self.assertIn("[cmd] PING", line)
        self.assertIn("status=0", line)
        self.assertIn("OPEN", su.format_entry({"t": 0.0, "event": "OPEN"}))


if __name__ == "__main__":
    unittest.main()
