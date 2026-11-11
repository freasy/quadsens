"""Read what RawAccel's driver holds right now (no Qt). Read-only.

Found in the RawAccel sources at the tag v1.7.1 (common/rawaccel-io-def.h,
common/rawaccel-io.hpp, common/rawaccel.hpp, common/rawaccel-base.hpp,
driver/driver.cpp, driver/driver.h):

* The driver has a control device, \\\\.\\rawaccel. It is created with a
  security descriptor that lets every user read and write it, and it is not
  exclusive. RawAccel's own programs open it with no access rights asked.
* Three control codes, all buffered and usable with any access: READ, WRITE
  and GET_VERSION. READ copies out a header (io_base) and, when the buffer is
  large enough, one modifier_settings per profile and one device_settings per
  device. It changes nothing. Only WRITE is delayed by one second.
* GET_VERSION gives three integers. The sources of the 1.7.1 release say
  1.7.0 for both the programs and the driver.
* The header says how many profiles the driver holds. Zero means: nothing
  was written since Windows started, the driver passes the mouse through.
* No shipped program prints these values: writer.exe only writes, and
  rawaccel.exe shows them in its window.

The four numbers QuadSens changes sit at fixed places in each profile. The
places below follow from the struct definitions and the alignment rules of
the 64-bit Microsoft compiler; they are derived, not measured. To stay safe
with that, the answer is only believed when it passes several checks: a
driver version this layout was derived for, sizes that add up, readable
profile names, and finite positive numbers. Anything else is reported as
"cannot verify" and never as a value.

Only used on Windows; the device access is injected, so the parsing is
tested with bytes built from the same struct definitions. The device access
itself has not run on Windows.
"""

from __future__ import annotations

import math
import os
import struct
from collections.abc import Callable
from dataclasses import dataclass

from .rawaccel import Four

DEVICE = "\\\\.\\rawaccel"


def _ctl_code(function: int) -> int:
    """CTL_CODE(0x8888, function, METHOD_BUFFERED, FILE_ANY_ACCESS)."""
    return (0x8888 << 16) | (0 << 14) | (function << 2) | 0


IOCTL_READ = _ctl_code(0x888)
IOCTL_GET_VERSION = _ctl_code(0x88A)

# Driver versions whose layout is the one below.
KNOWN_VERSIONS = {(1, 7, 0), (1, 7, 1)}

# Layout of 1.7 (bytes). device_config: three bools, padding, two ints, two
# doubles. io_base: device_config and two unsigned counts.
IO_BASE_SIZE = 40
MODIFIER_COUNT_AT, DEVICE_COUNT_AT = 32, 36
# device_settings: name[256], profile[256], id[200] in 16-bit characters, then
# a device_config.
DEVICE_SETTINGS_SIZE = (256 + 256 + 200) * 2 + 32
# profile: name[256] (512 bytes), two pairs of doubles, two accel_args, the
# speed arguments, then the numbers that matter here.
NAME_BYTES = 512
ACCEL_ARGS_SIZE = 8 + 12 * 8 + 16 + 4 + 4 + 514 * 4      # 2184
SPEED_ARGS_SIZE = 8 + 4 * 8                               # 40
OUTPUT_DPI_AT = NAME_BYTES + 16 + 16 + 2 * ACCEL_ARGS_SIZE + SPEED_ARGS_SIZE    # 4952
PROFILE_SIZE = OUTPUT_DPI_AT + 8 * 8                      # 5016
# A profile is followed by data the driver computes from it; how much is not
# needed, only that it is not absurd.
MAX_MODIFIER_SIZE = 64 * 1024
MAX_PROFILES, MAX_DEVICES = 64, 64

# What a read can come to.
OK, EMPTY, NOT_LOADED, OTHER_VERSION, UNREADABLE, UNAVAILABLE = (
    "ok", "empty", "not loaded", "other version", "unreadable", "unavailable")


@dataclass(frozen=True)
class Live:
    kind: str
    version: tuple[int, int, int] | None = None
    profiles: tuple[tuple[str, Four], ...] = ()      # name and the four numbers, in order
    detail: str = ""

    def values(self, name: str) -> Four | None:
        for profile_name, four in self.profiles:
            if profile_name == name:
                return four
        return None


class DeviceError(Exception):
    """The device could not be opened or did not answer."""

    def __init__(self, message: str, missing: bool = False) -> None:
        super().__init__(message)
        self.missing = missing        # the device is not there: the driver is not loaded


# (control code, size of the output buffer) -> the bytes the driver returned
DeviceIo = Callable[[int, int], bytes]


def device_io(code: int, out_size: int) -> bytes:
    """One read-only request to RawAccel's control device. Windows only."""
    if os.name != "nt":
        raise DeviceError("only on Windows")
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)     # type: ignore[attr-defined]
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    open_existing = 3
    # As RawAccel's own programs do: no access rights asked, nothing shared.
    handle = kernel32.CreateFileW(DEVICE, 0, 0, None, open_existing, 0, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        error = ctypes.get_last_error()                           # type: ignore[attr-defined]
        raise DeviceError("Windows error %d" % error, missing=error in (2, 3))
    try:
        buffer = ctypes.create_string_buffer(out_size)
        returned = wintypes.DWORD(0)
        if not kernel32.DeviceIoControl(handle, code, None, 0, buffer, out_size,
                                        ctypes.byref(returned), None):
            raise DeviceError("Windows error %d"
                              % ctypes.get_last_error())          # type: ignore[attr-defined]
        return buffer.raw[:returned.value]
    finally:
        kernel32.CloseHandle(handle)


def parse_version(data: bytes) -> tuple[int, int, int] | None:
    if len(data) < 12:
        return None
    major, minor, patch = struct.unpack_from("<iii", data)
    return (major, minor, patch)


def _name(raw: bytes) -> str | None:
    text = raw.decode("utf-16-le", "replace")
    text = text.split("\x00", 1)[0]
    if not text or "�" in text or not text.isprintable():
        return None
    return text


def parse_read(data: bytes) -> Live:
    """Take the answer of READ apart. UNREADABLE whenever something does not fit."""
    if len(data) < IO_BASE_SIZE:
        return Live(UNREADABLE, detail="short answer")
    profiles, devices = struct.unpack_from("<II", data, MODIFIER_COUNT_AT)
    if profiles == 0:
        return Live(EMPTY)
    if profiles > MAX_PROFILES or devices > MAX_DEVICES:
        return Live(UNREADABLE, detail="implausible counts")
    rest = len(data) - IO_BASE_SIZE - devices * DEVICE_SETTINGS_SIZE
    if rest <= 0 or rest % profiles:
        return Live(UNREADABLE, detail="sizes do not add up")
    size = rest // profiles
    if size < PROFILE_SIZE or size > MAX_MODIFIER_SIZE or size % 8:
        return Live(UNREADABLE, detail="unexpected profile size %d" % size)
    found: list[tuple[str, Four]] = []
    for index in range(profiles):
        start = IO_BASE_SIZE + index * size
        name = _name(data[start:start + NAME_BYTES])
        numbers = struct.unpack_from("<4d", data, start + OUTPUT_DPI_AT)
        if name is None or not all(math.isfinite(n) and 0 < abs(n) < 1e7 for n in numbers) \
                or numbers[0] <= 0:
            return Live(UNREADABLE, detail="profile %d does not look right" % index)
        found.append((name, Four(*numbers)))
    return Live(OK, profiles=tuple(found))


def read_driver(io: DeviceIo | None = None) -> Live:
    """What the driver holds, or why that cannot be said. Never raises."""
    if io is None:
        if os.name != "nt":
            return Live(UNAVAILABLE)
        io = device_io
    try:
        version = parse_version(io(IOCTL_GET_VERSION, 12))
    except DeviceError as exc:
        return Live(NOT_LOADED if exc.missing else UNAVAILABLE, detail=str(exc))
    except OSError as exc:
        return Live(UNAVAILABLE, detail=str(exc))
    if version not in KNOWN_VERSIONS:
        return Live(OTHER_VERSION, version)
    try:
        header = io(IOCTL_READ, IO_BASE_SIZE)
        if len(header) < IO_BASE_SIZE:
            return Live(UNREADABLE, version, detail="short answer")
        profiles, devices = struct.unpack_from("<II", header, MODIFIER_COUNT_AT)
        if profiles == 0:
            return Live(EMPTY, version)
        if profiles > MAX_PROFILES or devices > MAX_DEVICES:
            return Live(UNREADABLE, version, detail="implausible counts")
        # Large enough for whatever follows a profile; the driver says how much it used.
        room = IO_BASE_SIZE + profiles * MAX_MODIFIER_SIZE + devices * DEVICE_SETTINGS_SIZE
        answer = parse_read(io(IOCTL_READ, room))
    except (DeviceError, OSError) as exc:
        return Live(UNAVAILABLE, version, detail=str(exc))
    return Live(answer.kind, version, answer.profiles, answer.detail)
