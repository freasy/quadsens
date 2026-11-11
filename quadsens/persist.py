"""Persist the driver values with the yeetmouse fork's own tool (no Qt).

Values written to sysfs are lost when the module is reloaded. The fork's
``yeetmousectl save FILE`` dumps the values the driver currently uses to a
``key=value`` config file, which ``yeetmouse.service`` applies again at start.
The stock yeetmousectl knows nothing about the four directional keys and
silently leaves them out, so a save is only believed after the file was read
back and the keys were found.

The config file normally belongs to root. If the user may write it, the tool is
run as the user; otherwise through one ``pkexec`` call. Only a system-installed
tool is ever run that way.
"""

from __future__ import annotations

import enum
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import Scalars

DEFAULT_CONFIG = Path("/etc/yeetmouse.conf")
TOOL_NAME = "yeetmousectl"
# Config keys in the order right, left, down, up (the driver's +Y is "down").
CONFIG_KEYS: tuple[str, ...] = ("dirScaleXPos", "dirScaleXNeg", "dirScaleYPos", "dirScaleYNeg")
# The only places a tool may live to be run with root rights.
TRUSTED_DIRS: tuple[str, ...] = ("/usr/bin", "/usr/local/bin", "/bin")
# The driver reports six decimals; the tool writes up to six significant ones.
SAME_VALUE = 2e-6

Runner = Callable[..., Any]


class PersistError(Exception):
    """Saving or reading back the config file failed."""


class SaveState(enum.Enum):
    SAVED = "saved"                # the config file holds the driver's values
    DIFFERS = "differs"            # it holds other directional values
    MISSING_KEYS = "missing keys"  # it has no (complete) directional values
    NO_CONFIG = "no config"        # the file does not exist or cannot be read
    UNKNOWN = "unknown"            # the driver values are not known


def parse_config(text: str) -> dict[str, str]:
    """``key=value`` lines as a dict. Blank lines and ``#`` comments are skipped."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def saved_scalars(text: str) -> Scalars | None:
    """The four directional values in a config text; None unless all four are valid."""
    entries = parse_config(text)
    values: list[float] = []
    for key in CONFIG_KEYS:
        try:
            values.append(float(entries[key]))
        except (KeyError, ValueError):
            return None
    scalars = Scalars(*values)
    try:
        scalars.validate()
    except ValueError:
        return None
    return scalars


def is_trusted_tool(path: str | os.PathLike[str],
                    trusted_dirs: Sequence[str] = TRUSTED_DIRS,
                    owner_uid: int = 0) -> bool:
    """Whether ``path`` may be run with root rights.

    It has to be a regular file directly inside one of the trusted directories
    (also after resolving symlinks, so /bin -> /usr/bin is fine), owned by
    ``owner_uid`` and writable by nobody else.
    """
    path = Path(path)
    if not path.is_absolute():
        return False
    allowed = {Path(d) for d in trusted_dirs} | {Path(d).resolve() for d in trusted_dirs}
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except OSError:
        return False
    if path.parent not in allowed or resolved.parent not in allowed:
        return False
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return False
    return info.st_uid == owner_uid and not info.st_mode & 0o022


@dataclass(frozen=True)
class SaveCommand:
    argv: list[str]
    elevated: bool  # True when it goes through pkexec


class Saver:
    """Knows the config file, finds the tool and builds the save command."""

    def __init__(self, config_path: str | os.PathLike[str] = DEFAULT_CONFIG,
                 tool: str | os.PathLike[str] | None = None, pkexec: str = "pkexec",
                 trusted_dirs: Sequence[str] = TRUSTED_DIRS, owner_uid: int = 0) -> None:
        self.config_path = Path(config_path)
        self._tool = Path(tool) if tool is not None else None
        self.pkexec = pkexec
        self.trusted_dirs = tuple(trusted_dirs)
        self.owner_uid = owner_uid

    # ---- tool -------------------------------------------------------------

    def find_tool(self) -> Path | None:
        """The tool to run: an explicit one, or the one on PATH if it is trusted."""
        if self._tool is not None:
            return self._tool if self._tool.is_file() else None
        found = shutil.which(TOOL_NAME, path=os.pathsep.join(self.trusted_dirs))
        if found is None or not is_trusted_tool(found, self.trusted_dirs, self.owner_uid):
            return None
        return Path(found)

    def can_write_directly(self) -> bool:
        """Whether this user may write the config file without root."""
        return self.config_path.is_file() and os.access(self.config_path, os.W_OK)

    def command(self) -> SaveCommand:
        """The one command that saves the driver's current values."""
        tool = self.find_tool()
        if tool is None:
            raise PersistError(
                f"{TOOL_NAME} was not found in {', '.join(self.trusted_dirs)}")
        save = [str(tool), "save", str(self.config_path)]
        if self.can_write_directly():
            return SaveCommand(save, elevated=False)
        # Root rights only for a tool nobody but root can replace.
        if not is_trusted_tool(tool, self.trusted_dirs, self.owner_uid):
            raise PersistError(
                f"{tool} is not a system-installed {TOOL_NAME}; it is not run as root")
        pkexec = shutil.which(self.pkexec)
        if pkexec is None:
            raise PersistError(
                f"pkexec is not installed and {self.config_path} is not writable for you")
        return SaveCommand([pkexec, *save], elevated=True)

    # ---- config file ------------------------------------------------------

    def read_saved(self) -> Scalars | None:
        """The directional values in the config file, None if they are not there.

        Raises PersistError when the file is missing or unreadable.
        """
        try:
            text = self.config_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise PersistError(
                f"{self.config_path}: cannot read ({exc.strerror or exc})") from exc
        return saved_scalars(text)

    def state(self, driver: Scalars | None) -> SaveState:
        """How the config file relates to the values the driver uses."""
        try:
            saved = self.read_saved()
        except PersistError:
            return SaveState.NO_CONFIG
        if saved is None:
            return SaveState.MISSING_KEYS
        if driver is None:
            return SaveState.UNKNOWN
        return SaveState.SAVED if saved.is_close(driver, SAME_VALUE) else SaveState.DIFFERS

    def verify(self, driver: Scalars | None) -> tuple[SaveState, str]:
        """Check the file after a save. Returns the state and a message for the user."""
        state = self.state(driver)
        if state is SaveState.SAVED:
            return state, f"Saved to {self.config_path}."
        if state is SaveState.MISSING_KEYS:
            return state, (
                f"Nothing directional was saved: {self.config_path} has no dirScale "
                f"values after the save. The installed {TOOL_NAME} is not the fork's.")
        if state is SaveState.NO_CONFIG:
            return state, f"Not saved: {self.config_path} cannot be read after the save."
        if state is SaveState.UNKNOWN:
            return state, (
                f"Saved to {self.config_path}, but the driver values cannot be read "
                "here, so the result is not confirmed.")
        return state, (
            f"Not saved correctly: {self.config_path} holds other values than the "
            "driver reports.")

    def save(self, driver: Scalars | None,
             runner: Runner = subprocess.run) -> tuple[SaveState, str]:
        """Run the save command and verify the result (blocking; the GUI uses QProcess)."""
        command = self.command()
        try:
            result = runner(command.argv, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise PersistError(f"could not run {command.argv[0]}: {exc}") from exc
        if result.returncode != 0:
            raise PersistError(describe_save_failure(
                result.returncode, result.stderr, command.elevated))
        return self.verify(driver)


def describe_save_failure(returncode: int, output: str = "", elevated: bool = False) -> str:
    """Readable message for a non-zero exit of the save command."""
    detail = " ".join(output.split())
    if elevated and returncode == 126:
        reason = "authorization was dismissed"
    elif elevated and returncode == 127:
        reason = "not authorized (or pkexec could not start the tool)"
    else:
        reason = f"{TOOL_NAME} save exited with status {returncode}"
    return f"Not saved: {reason}" + (f" ({detail})" if detail else "")
