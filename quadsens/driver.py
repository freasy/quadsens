"""Read and write the directional scale parameters of the yeetmouse module (no Qt).

The patched module exposes four decimal strings under
/sys/module/yeetmouse/parameters (DirScaleXPos, DirScaleXNeg, DirScaleYPos with
+Y = down, DirScaleYNeg); writing ``1`` to ``update`` applies them. The files
may be writable by the user or root-only. In the second case the write goes
through one ``pkexec`` call of the small helper script ``quadsens-apply``.
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

DEFAULT_PARAMS_DIR = Path("/sys/module/yeetmouse/parameters")
PARAM_NAMES: tuple[str, ...] = ("DirScaleXPos", "DirScaleXNeg", "DirScaleYPos", "DirScaleYNeg")
UPDATE_NAME = "update"
HELPER_NAME = "quadsens-apply"

Runner = Callable[..., Any]


class DriverError(Exception):
    """Reading or writing the driver parameters failed."""


class DriverStatus(enum.Enum):
    OK = "ok"
    NOT_LOADED = "module not loaded"
    UNSUPPORTED = "module loaded, but without directional scale parameters"
    NOT_READABLE = "not readable"
    ERROR = "error"


@dataclass(frozen=True)
class DriverState:
    status: DriverStatus
    scalars: Scalars | None = None
    message: str = ""


def format_value(value: float) -> str:
    """The decimal string written to sysfs and passed to the helper."""
    return f"{value:.6f}"


def find_helper() -> Path | None:
    """Locate the pkexec helper: system install first, then the source checkout."""
    candidates = [
        Path("/usr/local/libexec") / HELPER_NAME,
        Path("/usr/libexec") / HELPER_NAME,
        Path("/usr/lib/quadsens") / HELPER_NAME,
        Path(__file__).resolve().parent.parent / "helper" / HELPER_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _write_existing(path: Path, text: str) -> None:
    # No O_CREAT: a missing parameter must fail instead of creating a file.
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    try:
        os.write(fd, text.encode("ascii"))
    finally:
        os.close(fd)


class Driver:
    def __init__(self, params_dir: str | os.PathLike[str] = DEFAULT_PARAMS_DIR,
                 helper: str | os.PathLike[str] | None = None,
                 pkexec: str = "pkexec") -> None:
        self.params_dir = Path(params_dir)
        self._helper = Path(helper) if helper is not None else None
        self.pkexec = pkexec

    # ---- read -----------------------------------------------------------

    def read(self) -> DriverState:
        """Current values, or a state that says why they are not available."""
        if not self.params_dir.is_dir():
            return DriverState(DriverStatus.NOT_LOADED,
                               message=f"{self.params_dir} does not exist")
        missing = [n for n in PARAM_NAMES if not (self.params_dir / n).exists()]
        if missing:
            return DriverState(DriverStatus.UNSUPPORTED,
                               message="missing: " + ", ".join(missing))
        values: list[float] = []
        for name in PARAM_NAMES:
            try:
                text = (self.params_dir / name).read_text(encoding="ascii").strip()
                values.append(float(text))
            except PermissionError:
                return DriverState(DriverStatus.NOT_READABLE,
                                   message="no permission to read the parameters")
            except (OSError, ValueError) as exc:
                return DriverState(DriverStatus.ERROR, message=f"{name}: {exc}")
        return DriverState(DriverStatus.OK, Scalars(*values))

    # ---- write ----------------------------------------------------------

    def write_direct(self, scalars: Scalars) -> None:
        """Write the four values and the update trigger straight to sysfs.

        Raises PermissionError when the files are not writable by this user,
        DriverError for anything else. Nothing is written if validation fails.
        """
        self._check(scalars)
        try:
            for name, value in zip(PARAM_NAMES, scalars.as_tuple()):
                _write_existing(self.params_dir / name, format_value(value) + "\n")
            _write_existing(self.params_dir / UPDATE_NAME, "1\n")
        except PermissionError:
            raise
        except OSError as exc:
            raise DriverError(f"writing driver parameters failed: {exc}") from exc

    def helper_command(self, scalars: Scalars) -> list[str]:
        """The single pkexec command line that applies ``scalars``."""
        self._check(scalars)
        helper = self._helper if self._helper is not None else find_helper()
        if helper is None or not Path(helper).is_file():
            raise DriverError(f"helper script {HELPER_NAME} not found")
        pkexec = shutil.which(self.pkexec)
        if pkexec is None:
            raise DriverError("pkexec is not installed; cannot write root-only parameters")
        return [pkexec, str(Path(helper).resolve()),
                *(format_value(v) for v in scalars.as_tuple())]

    def write(self, scalars: Scalars, runner: Runner = subprocess.run) -> str:
        """Write directly, falling back to pkexec. Returns "direct" or "pkexec".

        Blocks while pkexec asks for authorization; the GUI uses write_direct()
        and helper_command() with a QProcess instead.
        """
        try:
            self.write_direct(scalars)
            return "direct"
        except PermissionError:
            pass
        command = self.helper_command(scalars)
        try:
            result = runner(command, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise DriverError(f"could not run pkexec: {exc}") from exc
        if result.returncode != 0:
            raise DriverError(describe_helper_failure(result.returncode, result.stderr))
        return "pkexec"

    def _check(self, scalars: Scalars) -> None:
        try:
            scalars.validate()
        except ValueError as exc:
            raise DriverError(str(exc)) from exc
        if not self.params_dir.is_dir():
            raise DriverError("yeetmouse module is not loaded")


def describe_helper_failure(returncode: int, stderr: str | Sequence[str] = "") -> str:
    """Readable message for a non-zero exit of the pkexec call."""
    detail = stderr.strip() if isinstance(stderr, str) else " ".join(stderr).strip()
    if returncode == 126:
        reason = "authorization was dismissed"
    elif returncode == 127:
        reason = "not authorized (or pkexec could not start the helper)"
    else:
        reason = f"helper exited with status {returncode}"
    return f"{reason}: {detail}" if detail else reason
