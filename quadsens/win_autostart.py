"""Load RawAccel's settings again at every Windows sign-in, if the user wants it (no Qt).

RawAccel's driver keeps its settings only until Windows shuts down. Its FAQ
says what brings them back: starting its window, or running "writer.exe
settings.json" (doc/FAQ.md of the 1.7.1 release). It names no particular way
to do that at start and no waiting time.

QuadSens offers one, off by default: a value in the per-user Run key,

    HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
    name:  QuadSens RawAccel settings
    data:  "<folder>\\writer.exe" "<folder>\\settings.json"

Why the Run key and not a shortcut in the Startup folder: writing a registry
value needs nothing but the standard library and no administrator rights,
while creating a shortcut file needs the Windows shell (COM) or another
program. Both paths are quoted, so spaces are fine. writer.exe is a
windowed program (writer/writer.csproj: OutputType WinExe), so no console
appears; it shows its own message box if something is wrong. That the driver
is ready by the time of sign-in is assumed, not verified: it is loaded with
the mouse devices long before.

The entry is written and removed only by QuadSens' checkbox. To remove it by
hand: Task Manager > Startup apps, or delete the value with regedit.

The registry is injected; nothing here has run on Windows.
"""

from __future__ import annotations

import ntpath
import os
from dataclasses import dataclass
from typing import Protocol

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "QuadSens RawAccel settings"
WHERE = "HKEY_CURRENT_USER\\" + RUN_KEY

OFF, ON, OTHER = "off", "on", "other"


class Registry(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class RunKey:
    """The real thing: the Run key of the current user. Windows only."""

    def _open(self, write: bool):   # type: ignore[no-untyped-def]
        import winreg
        access = winreg.KEY_SET_VALUE if write else winreg.KEY_QUERY_VALUE
        return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)

    def get(self, name: str) -> str | None:
        import winreg
        try:
            with self._open(False) as key:
                value, kind = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        return value if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) \
            and isinstance(value, str) else None

    def set(self, name: str, value: str) -> None:
        import winreg
        with self._open(True) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg
        try:
            with self._open(True) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


class NoRegistry:
    """Where there is no registry (not Windows): nothing is there, nothing can be set."""

    def get(self, name: str) -> str | None:
        return None

    def set(self, name: str, value: str) -> None:
        raise OSError("the Windows registry is not available here")

    def delete(self, name: str) -> None:
        return None


def default_registry() -> Registry:
    return RunKey() if os.name == "nt" else NoRegistry()


class AutostartError(Exception):
    """The entry could not be read, written or removed."""


def command(folder: str | os.PathLike[str]) -> str:
    """The command line of the entry for the RawAccel in ``folder``."""
    folder = os.fspath(folder)
    if '"' in folder or "\n" in folder or "\r" in folder or "\x00" in folder or not folder:
        raise AutostartError("This folder name cannot be used in a startup entry.")
    sep = "\\" if ("\\" in folder or ":" in folder[:3]) else os.sep
    base = folder.rstrip("\\/")
    return '"%s%swriter.exe" "%s%ssettings.json"' % (base, sep, base, sep)


def entry_folder(value: str) -> str:
    """The folder an entry points to, "" if it does not look like one of ours."""
    parts = value.split('"')
    if len(parts) < 2 or not parts[1].lower().endswith("writer.exe"):
        return ""
    return ntpath.dirname(parts[1].replace("/", "\\")) if "\\" in parts[1] \
        else os.path.dirname(parts[1])


@dataclass(frozen=True)
class State:
    kind: str            # OFF, ON, or OTHER (an entry for another folder)
    value: str = ""      # the command line found
    folder: str = ""     # the folder it points to

    @property
    def exists(self) -> bool:
        return self.kind != OFF


def state(folder: str | os.PathLike[str] | None, registry: Registry) -> State:
    """What is in the Run key, compared with what the entry for ``folder`` would be."""
    try:
        value = registry.get(VALUE_NAME)
    except OSError as exc:
        raise AutostartError("The startup entry cannot be read: %s."
                             % (exc.strerror or exc)) from exc
    if not value:
        return State(OFF)
    try:
        wanted = command(folder) if folder else ""
    except AutostartError:
        wanted = ""
    if wanted and value.strip().lower() == wanted.lower():
        return State(ON, value, entry_folder(value))
    return State(OTHER, value, entry_folder(value))


def enable(folder: str | os.PathLike[str], registry: Registry) -> State:
    """Create the entry, or point an existing one to ``folder``."""
    value = command(folder)
    try:
        registry.set(VALUE_NAME, value)
    except OSError as exc:
        raise AutostartError("The startup entry could not be written: %s."
                             % (exc.strerror or exc)) from exc
    return state(folder, registry)


def disable(registry: Registry) -> State:
    try:
        registry.delete(VALUE_NAME)
    except OSError as exc:
        raise AutostartError("The startup entry could not be removed: %s."
                             % (exc.strerror or exc)) from exc
    return State(OFF)


def follow_folder(folder: str | os.PathLike[str] | None, registry: Registry) -> State:
    """After the RawAccel folder changed: an existing entry moves with it.

    No entry stays no entry. Without a folder the entry is removed, because
    it would run a writer.exe that QuadSens no longer looks after.
    """
    current = state(folder, registry)
    if current.kind != OTHER:
        return current
    return enable(folder, registry) if folder else disable(registry)


def describe(current: State) -> str:
    """One line for under the checkbox."""
    if current.kind == ON:
        return ("At every sign-in writer.exe loads this settings.json into the driver. "
                "A moment may pass after sign-in before the values are active.")
    if current.kind == OTHER:
        return ("A startup entry exists, but for another folder (%s). Untick to remove "
                "it, then tick again to point it here." % (current.folder or current.value))
    return "Values are lost when Windows restarts."
