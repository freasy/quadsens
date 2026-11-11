"""Tests of the "is the driver applying it" logic and the read of RawAccel's driver
(no Qt). The driver answers are bytes laid out as the structs of the RawAccel
1.7.1 sources say; the device is a stand-in.
Platform-independent; also run on Windows in CI.
"""

from __future__ import annotations

import struct
import time
import unittest

from quadsens import active, rawaccel_live
from quadsens.rawaccel import Four

VALUES = Four(1011.0, 0.995054, 0.980218, 0.99006)
OWN = Four(1000.0, 1.0, 1.0, 1.0)


# ---- the structs of common/rawaccel-base.hpp and common/rawaccel.hpp, as bytes ----

def accel_args() -> bytes:
    """accel_args: mode, gain, twelve doubles, cap, cap mode, length, 514 floats."""
    data = struct.pack("<iB3x", 6, 1)                         # noaccel, gain (+ padding)
    data += struct.pack("<12d", 0, 0, 0.005, 0.1, 1, 1.5, 2, 1, 0.05, 1.5, 5, 0.5)
    data += struct.pack("<2d", 15, 1.5) + struct.pack("<ii", 2, 0)
    data += b"\0" * (514 * 4)
    assert len(data) == rawaccel_live.ACCEL_ARGS_SIZE
    return data


def profile(name: str, four: Four) -> bytes:
    data = name.encode("utf-16-le").ljust(512, b"\0")
    data += struct.pack("<4d", 1, 1, 1, 1)                    # domain and range weights
    data += accel_args() + accel_args()
    data += struct.pack("<B7x4d", 1, 2, 0, 0, 0)              # speed_args
    assert len(data) == rawaccel_live.OUTPUT_DPI_AT
    data += struct.pack("<4d", *four.as_tuple())              # output DPI and the three ratios
    data += struct.pack("<4d", 0, 0, 0, 0)                    # rotation, snap, speed min/max
    assert len(data) == rawaccel_live.PROFILE_SIZE
    return data


def io_base(profiles: int, devices: int = 0) -> bytes:
    data = struct.pack("<3Bxii4x2d", 0, 0, 0, 0, 0, 0.0625, 100.0)   # device_config
    data += struct.pack("<II", profiles, devices)
    assert len(data) == rawaccel_live.IO_BASE_SIZE
    return data


def answer(profiles: list[tuple[str, Four]], devices: int = 0, computed: int = 4136) -> bytes:
    """A whole READ answer; ``computed`` bytes of driver data follow each profile."""
    data = io_base(len(profiles), devices)
    for name, four in profiles:
        data += profile(name, four) + b"\x07" * computed
    return data + b"\0" * (devices * rawaccel_live.DEVICE_SETTINGS_SIZE)


class FakeDevice:
    def __init__(self, version: tuple[int, int, int] = (1, 7, 0), data: bytes = b"",
                 error: Exception | None = None) -> None:
        self.version, self.data, self.error = version, data, error
        self.calls: list[tuple[int, int]] = []

    def __call__(self, code: int, size: int) -> bytes:
        self.calls.append((code, size))
        if self.error:
            raise self.error
        if code == rawaccel_live.IOCTL_GET_VERSION:
            return struct.pack("<iii", *self.version)
        # As the driver does: only the header when the buffer is too small.
        return self.data if size >= len(self.data) else self.data[:rawaccel_live.IO_BASE_SIZE]


class LiveReadTest(unittest.TestCase):
    def test_control_codes_are_those_of_the_sources(self) -> None:
        # CTL_CODE(0x8888, 0x888 / 0x88a, METHOD_BUFFERED, FILE_ANY_ACCESS)
        self.assertEqual(rawaccel_live.IOCTL_READ, 0x88882220)
        self.assertEqual(rawaccel_live.IOCTL_GET_VERSION, 0x88882228)
        self.assertNotIn(0x88882224, (rawaccel_live.IOCTL_READ,                # WRITE
                                      rawaccel_live.IOCTL_GET_VERSION))
        import inspect
        source = inspect.getsource(rawaccel_live)
        self.assertNotIn("0x889", source)                    # the write code is not even named
        self.assertEqual(rawaccel_live.DEVICE, "\\\\.\\rawaccel")

    def test_layout_sizes(self) -> None:
        self.assertEqual((rawaccel_live.ACCEL_ARGS_SIZE, rawaccel_live.OUTPUT_DPI_AT,
                          rawaccel_live.PROFILE_SIZE, rawaccel_live.DEVICE_SETTINGS_SIZE),
                         (2184, 4952, 5016, 1456))

    def test_reads_every_profile_by_name(self) -> None:
        device = FakeDevice(data=answer([("default", VALUES), ("flick", OWN)], devices=2))
        live = rawaccel_live.read_driver(device)
        self.assertEqual((live.kind, live.version), (rawaccel_live.OK, (1, 7, 0)))
        self.assertEqual(live.values("default"), VALUES)
        self.assertEqual(live.values("flick"), OWN)
        self.assertIsNone(live.values("other"))
        # Only the two read-only requests were made.
        self.assertEqual({code for code, _size in device.calls},
                         {rawaccel_live.IOCTL_READ, rawaccel_live.IOCTL_GET_VERSION})

    def test_a_driver_without_settings_says_so(self) -> None:
        live = rawaccel_live.read_driver(FakeDevice(data=io_base(0)))
        self.assertEqual(live.kind, rawaccel_live.EMPTY)

    def test_other_versions_are_not_read(self) -> None:
        for version in ((1, 6, 1), (1, 8, 0), (2, 0, 0), (0, 0, 0)):
            device = FakeDevice(version, answer([("default", VALUES)]))
            live = rawaccel_live.read_driver(device)
            self.assertEqual((live.kind, live.version), (rawaccel_live.OTHER_VERSION, version))
            self.assertEqual(len(device.calls), 1)           # nothing was read after that

    def test_driver_not_loaded_or_not_answering(self) -> None:
        missing = rawaccel_live.DeviceError("Windows error 2", missing=True)
        self.assertEqual(rawaccel_live.read_driver(FakeDevice(error=missing)).kind,
                         rawaccel_live.NOT_LOADED)
        denied = rawaccel_live.DeviceError("Windows error 5")
        self.assertEqual(rawaccel_live.read_driver(FakeDevice(error=denied)).kind,
                         rawaccel_live.UNAVAILABLE)
        self.assertEqual(rawaccel_live.read_driver(FakeDevice(error=OSError("x"))).kind,
                         rawaccel_live.UNAVAILABLE)
        import os
        if os.name != "nt":
            self.assertEqual(rawaccel_live.read_driver().kind, rawaccel_live.UNAVAILABLE)

    def test_answers_that_do_not_fit_are_never_turned_into_values(self) -> None:
        good = answer([("default", VALUES)])
        bad = {
            "truncated": good[:-9],
            "one byte more": good + b"\0",
            "profile too small": io_base(1) + profile("default", VALUES)[:-8],
            "name is not text": io_base(1) + b"\xff\xdf" * 256 + good[40 + 512:],
            "empty name": io_base(1) + b"\0" * 512 + good[40 + 512:],
            "not a number": good[:40 + 4952] + struct.pack("<d", float("nan"))
            + good[40 + 4960:],
            "negative DPI": good[:40 + 4952] + struct.pack("<d", -5.0) + good[40 + 4960:],
            "zero ratio": good[:40 + 4960] + struct.pack("<d", 0.0) + good[40 + 4968:],
            "absurd count": struct.pack("<32xII", 5000, 0) + good[40:],
            "short": b"\0" * 10,
        }
        for what, data in bad.items():
            live = rawaccel_live.read_driver(FakeDevice(data=data))
            self.assertEqual(live.kind, rawaccel_live.UNREADABLE, what)
            self.assertEqual(live.profiles, (), what)


class WindowsIndicatorTest(unittest.TestCase):
    NOW = time.time()

    def show(self, live: rawaccel_live.Live, driver: str = "ready", **kwargs) -> active.Indicator:
        return active.windows_indicator(driver, live, "default", **kwargs)

    def ok(self, four: Four, name: str = "default") -> rawaccel_live.Live:
        return rawaccel_live.Live(rawaccel_live.OK, (1, 7, 0), ((name, four),))

    def test_driver_missing_pending_or_not_running(self) -> None:
        unavailable = rawaccel_live.Live(rawaccel_live.UNAVAILABLE)
        shown = self.show(unavailable, active.DRIVER_MISSING)
        self.assertEqual((shown.kind, shown.mark), (active.INACTIVE, "✕"))
        self.assertIn("not installed", shown.headline)
        shown = self.show(unavailable, active.DRIVER_PENDING)
        self.assertEqual(shown.kind, active.INACTIVE)
        self.assertIn("restart Windows", shown.headline)
        shown = self.show(rawaccel_live.Live(rawaccel_live.NOT_LOADED))
        self.assertEqual(shown.kind, active.INACTIVE)
        self.assertIn("not running", shown.headline)

    def test_read_back_from_the_driver(self) -> None:
        shown = self.show(self.ok(VALUES), sent=VALUES, sent_at=self.NOW, baseline=OWN)
        self.assertEqual((shown.kind, shown.headline, shown.tint),
                         (active.ACTIVE, "Active in driver", "success"))
        self.assertIn("read back from the driver", shown.detail)
        self.assertIn("output DPI 1011, Y/X 0.995054, L/R 0.980218, U/D 0.99006", shown.detail)
        self.assertIn(active.clock(self.NOW), shown.detail)
        self.assertEqual(shown.short(), "✓ Active in driver")
        # The profile's own values are neutral, whoever put them there.
        for sent in (OWN, VALUES, None):
            shown = self.show(self.ok(OWN), sent=sent, sent_at=self.NOW, baseline=OWN)
            self.assertEqual((shown.kind, shown.mark), (active.NEUTRAL, "○"), sent)
        # Something else changed the driver since.
        other = Four(1200.0, 1.0, 1.0, 1.0)
        shown = self.show(self.ok(other), sent=VALUES, sent_at=self.NOW, baseline=OWN)
        self.assertEqual((shown.kind, shown.mark), (active.DIFFERENT, "≠"))
        self.assertIn("output DPI 1200", shown.detail)
        self.assertIn("Sent", shown.detail)
        self.assertIn("output DPI 1011", shown.detail)
        # Applied by an earlier version of QuadSens: the file is the witness.
        shown = self.show(self.ok(VALUES), baseline=OWN, in_file=VALUES)
        self.assertEqual(shown.kind, active.ACTIVE)
        self.assertIn("equal to settings.json", shown.detail)
        shown = self.show(self.ok(VALUES, "another"), sent=VALUES, sent_at=self.NOW)
        self.assertEqual(shown.kind, active.DIFFERENT)
        self.assertIn("another", shown.detail)

    def test_nothing_applied_since_windows_started(self) -> None:
        shown = self.show(rawaccel_live.Live(rawaccel_live.EMPTY), sent=VALUES,
                          sent_at=self.NOW - 5)
        self.assertEqual((shown.kind, shown.mark, shown.tint),
                         (active.NOT_APPLIED, "⚠", "warning"))
        self.assertEqual(shown.headline, "Not applied since Windows started")

    def test_without_a_readable_driver_it_says_sent_not_active(self) -> None:
        for live in (rawaccel_live.Live(rawaccel_live.OTHER_VERSION, (1, 6, 1)),
                     rawaccel_live.Live(rawaccel_live.UNREADABLE),
                     rawaccel_live.Live(rawaccel_live.UNAVAILABLE)):
            shown = self.show(live, sent=VALUES, sent_at=self.NOW, started=self.NOW - 3600)
            self.assertEqual(shown.kind, active.SENT, live.kind)
            self.assertEqual(shown.headline, "Sent to the driver %s, accepted by RawAccel's "
                                             "writer" % active.clock(self.NOW))
            self.assertIn("Cannot be read back", shown.detail)
            self.assertNotIn("Active", shown.headline)
            # The last Apply was before Windows started: the values are gone.
            shown = self.show(live, sent=VALUES, sent_at=self.NOW - 7200,
                              started=self.NOW - 3600)
            self.assertEqual((shown.kind, shown.headline),
                             (active.NOT_APPLIED, "Not applied since Windows started"))
            shown = self.show(live)
            self.assertEqual(shown.kind, active.NOT_APPLIED)
            self.assertIn("Cannot be verified", shown.detail)
        shown = self.show(rawaccel_live.Live(rawaccel_live.OTHER_VERSION, (1, 6, 1)),
                          sent=VALUES, sent_at=self.NOW)
        self.assertIn("1.6.1", shown.detail)

    def test_every_kind_has_a_mark_a_tint_and_words(self) -> None:
        for kind in (active.ACTIVE, active.NEUTRAL, active.DIFFERENT, active.SENT,
                     active.NOT_APPLIED, active.INACTIVE, active.UNVERIFIED):
            shown = active.Indicator(kind, "headline", "detail")
            self.assertTrue(shown.mark and shown.tint)
            self.assertEqual(shown.spoken(), "headline. detail")
        self.assertEqual(len(set(active.MARKS.values())), 6)      # ✓ twice, on purpose


if __name__ == "__main__":
    unittest.main()
