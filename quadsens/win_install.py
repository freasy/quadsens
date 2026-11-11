"""Install the two Windows tools from their verified release files (no Qt).

Nothing here runs by itself: every flow is started by the user and, where
Windows needs administrator rights, goes through the standard Windows prompt.
QuadSens never restarts the computer and never removes a tool.

What is checked before anything of a download is run:
* the release file against the pinned size and SHA-256 (quadsens.win_download);
* for the usbip-win2 installer also its Authenticode signature and signer,
  asked from Windows through PowerShell;
* right before the start, the file again, through a handle that is kept open
  and denies writing and deleting until the program has been started, so the
  checked file cannot be exchanged in between;
* for RawAccel, whose installer is not signed, each unpacked program file
  against its pinned checksum, the installer and the driver file again right
  before the start, in the same way.

What remains, said plainly: the handle trick relies on Windows file sharing
rules and has not been run on Windows yet; on other systems the files are
only read and checked. A program that already runs as the same user and is
fast enough could still interfere in ways this cannot rule out, for example
by replacing other files of the RawAccel folder that are loaded later
without elevation. And the checks say that the files are the ones QuadSens
was tested with, not that those are free of faults.

Everything that touches Windows is injected (command runner, launcher, file
system probes), so the logic is tested on Linux. None of it has run on
Windows.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import ntpath
import os
import stat
import subprocess
import time
import zipfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from . import win_download
from .win_download import DownloadError
from .win_tools import (
    OLDER,
    PINS,
    RAWACCEL,
    RAWACCEL_DRIVER,
    RAWACCEL_INSTALLER,
    RAWACCEL_MAX_BYTES,
    RAWACCEL_MAX_FILES,
    RAWACCEL_TOP_FOLDER,
    USBIP,
    Pin,
    compare,
)

# ---------------------------------------------------------------------------
# Unpacking
# ---------------------------------------------------------------------------


class UnpackError(Exception):
    """The archive is not what it should be; nothing of it was kept."""


def _member_path(name: str, top: str) -> PurePosixPath | None:
    """Path of an archive member below the target folder, None for the top folder itself.

    Raises UnpackError for anything that could leave the target folder.
    """
    if "\\" in name or ":" in name or name.startswith("/") or "\x00" in name:
        raise UnpackError("The archive holds a path that is not allowed: %r." % name[:80])
    parts = [part for part in name.split("/") if part != ""]
    if not parts or parts[0] != top:
        raise UnpackError("The archive holds something outside its %s folder: %r."
                          % (top, name[:80]))
    if any(part in (".", "..") or part.endswith((" ", ".")) for part in parts):
        raise UnpackError("The archive holds a path that is not allowed: %r." % name[:80])
    return PurePosixPath(*parts[1:]) if len(parts) > 1 else None


def plan_unpack(archive: zipfile.ZipFile, top: str = RAWACCEL_TOP_FOLDER,
                max_files: int = RAWACCEL_MAX_FILES,
                max_bytes: int = RAWACCEL_MAX_BYTES) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    """Check every member of the archive; return the files to write and where."""
    members = archive.infolist()
    if len(members) > max_files:
        raise UnpackError("The archive holds more files than expected (%d)." % len(members))
    plan: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    seen: set[str] = set()
    total = 0
    for info in members:
        relative = _member_path(info.filename, top)
        mode = info.external_attr >> 16
        # Archives made on Windows carry no file type at all; that is a plain file.
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise UnpackError("The archive holds a link or a special file: %r."
                              % info.filename[:80])
        if info.is_dir() or relative is None:
            continue
        key = str(relative).lower()
        if key in seen:
            raise UnpackError("The archive holds %r twice." % str(relative))
        seen.add(key)
        total += info.file_size
        if total > max_bytes:
            raise UnpackError("The archive unpacks to more than the %d bytes expected."
                              % max_bytes)
        plan.append((info, relative))
    return plan


def folder_usable(folder: str | os.PathLike[str]) -> str:
    """"" if RawAccel may be unpacked into ``folder``, else why not.

    Allowed: a folder that does not exist yet, an empty one, or one that
    already holds RawAccel (then its files are replaced and settings.json
    stays).
    """
    folder = Path(folder)
    if not folder.is_absolute():
        return "Give the folder with its full path."
    if not folder.exists():
        return ""
    if not folder.is_dir():
        return "%s is a file, not a folder." % folder
    try:
        names = {entry.name.lower() for entry in folder.iterdir()}
    except OSError as exc:
        return "%s cannot be read: %s." % (folder, exc.strerror or exc)
    if names and "rawaccel.exe" not in names:
        return ("%s already holds other files. Choose an empty folder or one that holds "
                "RawAccel." % folder)
    return ""


def unpack_rawaccel(archive_path: str | os.PathLike[str], folder: str | os.PathLike[str],
                    pin: Pin | None = None) -> list[Path]:
    """Unpack the verified RawAccel archive into ``folder``; return the files written.

    The archive's one top folder is left out. Every program file is checked
    against the pin while it is written. On any failure the files written by
    this call are removed again.
    """
    pin = pin or PINS[RAWACCEL]
    folder = Path(folder)
    reason = folder_usable(folder)
    if reason:
        raise UnpackError(reason)
    written: list[Path] = []
    try:
        with zipfile.ZipFile(archive_path) as archive:
            plan = plan_unpack(archive)
            names = {str(relative) for _info, relative in plan}
            missing = sorted(set(pin.files) - names)
            if missing:
                raise UnpackError("The archive lacks %s." % ", ".join(missing))
            folder.mkdir(parents=True, exist_ok=True)
            for info, relative in plan:
                target = folder.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_symlink():
                    raise UnpackError("%s is a link; it is not overwritten." % target)
                with archive.open(info) as source:
                    data = source.read(info.file_size + 1)
                if len(data) != info.file_size:
                    raise UnpackError("%s is not as large as the archive says." % relative)
                expected = pin.files.get(str(relative))
                if expected is not None:
                    if not win_download.matches(len(data), hashlib.sha256(data).hexdigest(),
                                                *expected):
                        raise UnpackError("%s in the archive is not the tested file."
                                          % relative)
                partial = target.with_name(target.name + ".quadsens-part")
                partial.write_bytes(data)
                os.replace(partial, target)
                written.append(target)
    except (OSError, zipfile.BadZipFile, UnpackError) as exc:
        for path in written:
            with contextlib.suppress(OSError):
                path.unlink()
        if isinstance(exc, UnpackError):
            raise
        raise UnpackError("Unpacking failed: %s. Nothing was started."
                          % (getattr(exc, "strerror", None) or exc)) from exc
    return written


# ---------------------------------------------------------------------------
# Holding a checked file, the Windows prompt, the signature
# ---------------------------------------------------------------------------


def _open_locked(path: str) -> Any:
    """Open for reading so that nobody can write, rename or delete the file meanwhile.

    On Windows: a handle that shares reading only. Starting the program still
    works, because that only reads. Elsewhere there is no such thing; the
    file is simply opened.
    """
    if os.name != "nt":
        return open(path, "rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes
    create = ctypes.windll.kernel32.CreateFileW        # type: ignore[attr-defined]
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    generic_read, share_read, open_existing = 0x80000000, 0x00000001, 3
    handle = create(path, generic_read, share_read, None, open_existing, 0, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error() or 5, "the file cannot be opened exclusively")
    return os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY), "rb")  # type: ignore


@contextlib.contextmanager
def held_verified(checks: list[tuple[str, int, str]],
                  opener: Callable[[str], Any] = _open_locked) -> Iterator[None]:
    """Check files (path, size, sha256) and keep them from changing inside the block.

    Raises DownloadError of kind HASH if one of them is not the expected file.
    """
    with contextlib.ExitStack() as stack:
        for path, size, sha256 in checks:
            try:
                stream = stack.enter_context(opener(path))
                ok = win_download.matches(*win_download.file_digest(stream, size),
                                          size, sha256)
            except OSError as exc:
                raise DownloadError(win_download.HASH,
                                    "%s cannot be checked (%s). Nothing was started."
                                    % (ntpath.basename(path), exc.strerror or exc)) from exc
            if not ok:
                raise DownloadError(
                    win_download.HASH,
                    "%s has changed since it was checked. Nothing was started. Delete "
                    "the file and try again." % ntpath.basename(path))
        yield


# What starting a program through the Windows prompt can come to.
FINISHED, DECLINED, NOT_STARTED = "finished", "declined", "not started"


@dataclass(frozen=True)
class Launch:
    kind: str
    exit_code: int = 0
    detail: str = ""


def launch_elevated(path: str, folder: str = "") -> Launch:
    """Start ``path`` through the Windows administrator prompt and wait for it to end.

    The program is named by its path alone: no parameters, no command line,
    no shell. ``folder`` becomes its working directory. Windows only.
    """
    if os.name != "nt":
        return Launch(NOT_STARTED, detail="This works on Windows only.")
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", wintypes.LPVOID),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]

    see_mask_nocloseprocess, sw_shownormal, error_cancelled = 0x00000040, 1, 1223
    info = Info()
    info.cbSize = ctypes.sizeof(Info)
    info.fMask = see_mask_nocloseprocess
    info.lpVerb = "runas"
    info.lpFile = path
    info.lpParameters = None
    info.lpDirectory = folder or None
    info.nShow = sw_shownormal
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)      # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)    # type: ignore[attr-defined]
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()                           # type: ignore[attr-defined]
        if error == error_cancelled:
            return Launch(DECLINED)
        return Launch(NOT_STARTED, detail="Windows error %d" % error)
    if not info.hProcess:
        return Launch(FINISHED)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    code = wintypes.DWORD(0)
    kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(info.hProcess)
    return Launch(FINISHED, int(code.value))


Runner = Callable[[list[str], float, Mapping[str, str]], tuple[int, str]]
SIGNED, NOT_SIGNED, UNCHECKED = "signed", "not signed", "unchecked"
SIGNATURE_SCRIPT = (
    "$s = Get-AuthenticodeSignature -LiteralPath $env:QUADSENS_CHECK_FILE; "
    "Write-Output ('status=' + $s.Status); "
    "Write-Output ('subject=' + $s.SignerCertificate.Subject)")


def run_quiet(argv: list[str], timeout: float, extra_env: Mapping[str, str]) -> tuple[int, str]:
    """Run a program without a console window; (status, output). Never raises."""
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False,
                                timeout=timeout, creationflags=flags,
                                stdin=subprocess.DEVNULL, env={**os.environ, **extra_env})
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError as exc:
        return 127, str(exc)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def signature_state(path: str, signer: str, environ: Mapping[str, str] | None = None,
                    runner: Runner = run_quiet) -> tuple[str, str]:
    """Ask Windows whether ``path`` is validly signed by ``signer``.

    Returns (SIGNED | NOT_SIGNED | UNCHECKED, detail). PowerShell is started
    by its full path with a fixed list of arguments; the file name travels in
    an environment variable, never inside the command text. UNCHECKED means
    the question could not be asked (no PowerShell, no answer); the caller
    then relies on the checksum alone and says so.
    """
    environ = os.environ if environ is None else environ
    root = environ.get("SystemRoot") or environ.get("windir")
    if not root:
        return UNCHECKED, "Windows folder unknown"
    shell = ntpath.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    code, output = runner([shell, "-NoProfile", "-NonInteractive", "-Command",
                           SIGNATURE_SCRIPT], 30.0, {"QUADSENS_CHECK_FILE": path})
    status = subject = ""
    for line in output.splitlines():
        if line.startswith("status="):
            status = line[7:].strip()
        elif line.startswith("subject="):
            subject = line[8:].strip()
    if code != 0 or not status:
        return UNCHECKED, "PowerShell gave no answer"
    names = [part.strip()[3:] for part in _split_subject(subject)
             if part.strip().upper().startswith("CN=")]
    if status == "Valid" and signer in [name.strip('"') for name in names]:
        return SIGNED, signer
    return NOT_SIGNED, "status %s, signer %s" % (status, ", ".join(names) or "none")


def _split_subject(subject: str) -> list[str]:
    """Split a certificate subject at commas that are not inside quotes."""
    parts, current, quoted = [], "", False
    for char in subject:
        if char == '"':
            quoted = not quoted
        if char == "," and not quoted:
            parts.append(current)
            current = ""
        else:
            current += char
    return parts + [current]


# ---------------------------------------------------------------------------
# What is installed
# ---------------------------------------------------------------------------

DRIVER_MISSING, DRIVER_PENDING, DRIVER_READY, DRIVER_UNKNOWN = (
    "missing", "restart pending", "ready", "installed")


def boot_time() -> float | None:
    """When Windows was started, as a unix time; None where that cannot be told."""
    if os.name != "nt":
        return None
    import ctypes
    tick = ctypes.windll.kernel32.GetTickCount64            # type: ignore[attr-defined]
    tick.restype = ctypes.c_uint64
    return time.time() - tick() / 1000.0


def rawaccel_driver_state(environ: Mapping[str, str] | None = None,
                          started: float | None = None,
                          installed_at: float | None = None,
                          created: Callable[[str], float | None] | None = None) -> str:
    """Whether RawAccel's driver is installed, and whether Windows was restarted since.

    Installed means: rawaccel.sys lies in the system's driver folder, where
    RawAccel's installer puts it. It is taken as pending when that file was
    created after Windows started, or when QuadSens started the installer
    after Windows started (``installed_at``). Without a start time of Windows
    (``started``) the answer is DRIVER_UNKNOWN: installed, restart not known.
    """
    environ = os.environ if environ is None else environ
    root = environ.get("SystemRoot") or environ.get("windir")
    if not root:
        return DRIVER_MISSING
    path = ntpath.join(root, "System32", "drivers", "rawaccel.sys")
    when = (created or _created)(path)
    if when is None:
        return DRIVER_MISSING
    if started is None:
        return DRIVER_UNKNOWN
    if when > started or (installed_at is not None and installed_at > started):
        return DRIVER_PENDING
    return DRIVER_READY


def _created(path: str) -> float | None:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return float(getattr(info, "st_birthtime", info.st_ctime))


def rawaccel_version(folder: str | os.PathLike[str], pin: Pin | None = None) -> str:
    """Version of the RawAccel in ``folder``: the pinned one if rawaccel.exe is the
    tested file, else what settings.json says, else ""."""
    pin = pin or PINS[RAWACCEL]
    folder = Path(folder)
    size, digest = pin.files["rawaccel.exe"]
    if win_download.verify_file(folder / "rawaccel.exe", size, digest):
        return pin.version
    try:
        data = json.loads((folder / "settings.json").read_text(encoding="utf-8-sig"))
        version = data.get("version") if isinstance(data, dict) else None
        return version if isinstance(version, str) else ""
    except (OSError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# The two flows
# ---------------------------------------------------------------------------

# How a flow ended.
DONE, CANCELLED, REFUSED, FAILED = "done", "cancelled", "declined by the user", "failed"


@dataclass(frozen=True)
class Result:
    kind: str
    message: str
    folder: str = ""          # RawAccel: where it was unpacked


@dataclass
class Tools:
    """Everything a flow needs from outside; the defaults are the real things."""

    download: Callable[..., Path] = win_download.download
    launch: Callable[[str, str], Launch] = launch_elevated
    signature: Callable[[str, str], tuple[str, str]] = signature_state
    hold: Callable[[list[tuple[str, int, str]]], Any] = held_verified
    cache: Callable[[], Path] = win_download.cache_dir


Say = Callable[[str], None]

USBIP_CONFIRM = (
    "QuadSens will download {asset} ({megabytes} MB) from the usbip-win2 project on "
    "GitHub, check it against the checksum of the tested version {version} and its "
    "signature, and then start that installer.",
    "Windows will ask for administrator rights for the installer.",
    "While it installs, all USB devices can disconnect for a moment. At its end the "
    "installer asks to restart the computer.",
    "An older usbip-win2 is replaced by the installer itself.",
)
RAWACCEL_CONFIRM = (
    "QuadSens will download {asset} ({megabytes} MB) from the RawAccel project on "
    "GitHub, check it against the checksum of the tested version {version}, and unpack "
    "it into the folder below.",
    "Then it starts RawAccel's installer.exe, which installs the mouse driver. Windows "
    "will ask for administrator rights for it. That program is not signed by its "
    "makers; the checksum is the check there is.",
    "RawAccel works only after the computer was restarted. QuadSens does not restart "
    "it. Apply needs that restart first.",
    "RawAccel needs the Visual C++ 2019 runtime and the .NET Framework 4.7.2 or newer; "
    "Windows 10 and 11 usually have both.",
)


def confirm_text(key: str) -> list[str]:
    pin = PINS[key]
    lines = USBIP_CONFIRM if key == USBIP else RAWACCEL_CONFIRM
    return [line.format(asset=pin.asset, version=pin.version,
                        megabytes="%.1f" % (pin.size / 1e6)) for line in lines]


def default_rawaccel_folder(environ: Mapping[str, str] | None = None) -> str:
    """Per-user programs folder: writable for the user, because settings.json lives there."""
    environ = os.environ if environ is None else environ
    base = environ.get("LOCALAPPDATA") or ntpath.join(
        environ.get("USERPROFILE", "C:\\"), "AppData", "Local")
    return ntpath.join(base, "Programs", "RawAccel")


def _fetch(pin: Pin, tools: Tools, say: Say, progress: Any, cancelled: Any) -> Path | Result:
    say("Downloading %s ..." % pin.asset)
    try:
        return tools.download(pin, tools.cache(), progress=progress, cancelled=cancelled)
    except DownloadError as exc:
        kind = CANCELLED if exc.kind == win_download.CANCELLED else FAILED
        return Result(kind, str(exc))


def _launch_result(launch: Launch, what: str) -> Result | None:
    if launch.kind == DECLINED:
        return Result(REFUSED, "The administrator prompt was declined, so %s was not "
                               "started. Nothing was changed." % what)
    if launch.kind == NOT_STARTED:
        return Result(FAILED, "%s could not be started (%s)." % (what, launch.detail))
    return None


def install_usbip(tools: Tools | None = None, say: Say = lambda text: None,
                  progress: Any = None, cancelled: Any = lambda: False,
                  detect: Callable[[], str] = lambda: "") -> Result:
    """Download, check and start the usbip-win2 installer; then look for usbip.exe.

    ``detect`` returns the version of the installed usbip.exe, "" if none.
    """
    pin, tools = PINS[USBIP], tools or Tools()
    fetched = _fetch(pin, tools, say, progress, cancelled)
    if isinstance(fetched, Result):
        return fetched
    path = str(fetched)
    say("Checking the signature ...")
    state, detail = tools.signature(path, pin.signer)
    if state == NOT_SIGNED:
        return Result(FAILED, "The installer is not signed as expected (%s). It was not "
                              "started." % detail)
    note = "" if state == SIGNED else \
        " (Its signature could not be asked from Windows; the checksum matched.)"
    say("Waiting for the installer. Windows asks for administrator rights ...")
    try:
        with tools.hold([(path, pin.size, pin.sha256)]):
            launch = tools.launch(path, "")
    except DownloadError as exc:
        return Result(FAILED, str(exc))
    failed = _launch_result(launch, "the installer")
    if failed is not None:
        return failed
    version = detect()
    if version:
        return Result(DONE, "usbip-win2 %s is installed.%s" % (version, note))
    return Result(DONE, "The installer has ended, but usbip.exe is not found yet. If you "
                        "cancelled it, nothing was installed; if it asked for a restart, "
                        "restart the computer and press Check again.%s" % note)


def install_rawaccel(folder: str, tools: Tools | None = None, say: Say = lambda text: None,
                     progress: Any = None, cancelled: Any = lambda: False,
                     driver_state: Callable[[], str] = lambda: DRIVER_UNKNOWN) -> Result:
    """Download, check, unpack RawAccel into ``folder`` and start its installer.exe."""
    pin, tools = PINS[RAWACCEL], tools or Tools()
    reason = folder_usable(folder)
    if reason:
        return Result(FAILED, reason)
    fetched = _fetch(pin, tools, say, progress, cancelled)
    if isinstance(fetched, Result):
        return fetched
    if cancelled():
        return Result(CANCELLED, "Cancelled before anything was unpacked.")
    say("Unpacking into %s ..." % folder)
    try:
        with tools.hold([(str(fetched), pin.size, pin.sha256)]):
            unpack_rawaccel(fetched, folder, pin)
    except (UnpackError, DownloadError) as exc:
        return Result(FAILED, str(exc))
    installer = os.path.join(folder, RAWACCEL_INSTALLER)
    driver = os.path.join(folder, *RAWACCEL_DRIVER.split("/"))
    say("Waiting for RawAccel's installer. Windows asks for administrator rights ...")
    try:
        with tools.hold([(installer, *pin.files[RAWACCEL_INSTALLER]),
                         (driver, *pin.files[RAWACCEL_DRIVER])]):
            launch = tools.launch(installer, folder)
    except DownloadError as exc:
        return Result(FAILED, str(exc), folder)
    failed = _launch_result(launch, "RawAccel's installer")
    if failed is not None:
        return Result(failed.kind, failed.message + " RawAccel is unpacked in %s; the "
                      "driver is not installed." % folder, folder)
    state = driver_state()
    if state == DRIVER_MISSING:
        return Result(FAILED, "RawAccel's installer has ended, but its driver is not in "
                              "the Windows driver folder. Read what the installer wrote, "
                              "or run installer.exe in %s yourself." % folder, folder)
    return Result(DONE, "RawAccel %s is unpacked in %s and its driver is installed. "
                        "Restart the computer now: RawAccel works only after that. Then "
                        "start rawaccel.exe once, so that it writes its settings.json."
                  % (pin.version, folder), folder)


def may_install_tested(key: str, installed_version: str) -> bool:
    """"Install tested version" is offered only for an older installation."""
    return compare(installed_version, PINS[key]) == OLDER
