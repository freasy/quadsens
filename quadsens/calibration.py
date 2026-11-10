"""Find and parse the calibration result file written by Aimlabs (no Qt).

The file is ``ASFSettings.json`` inside the game's Proton prefix. Two keys are
used: ``firmwareScalars`` {XPos, XNeg, YPos, YNeg} and ``directionalScalars``,
eight floats in the order E, NE, N, NW, W, SW, S, SE.
"""

from __future__ import annotations

import glob
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from .model import Scalars

# Location inside the Steam/Proton prefix of app 714010; the directory names are
# fixed by the game.
DEFAULT_GLOB = os.path.expanduser(
    "~/.local/share/Steam/steamapps/compatdata/714010/pfx/drive_c/users/steamuser/"
    "AppData/LocalLow/Statespace/aimlab_tb/Users/*/ASFData/*/ASFSettings.json"
)

DIRECTION_LABELS: tuple[str, ...] = ("E", "NE", "N", "NW", "W", "SW", "S", "SE")
_FIRMWARE_KEYS: tuple[str, ...] = ("XPos", "XNeg", "YPos", "YNeg")


class CalibrationError(Exception):
    """The calibration file is missing, unreadable or malformed."""


@dataclass(frozen=True)
class Calibration:
    path: Path
    mtime: float
    firmware: Scalars
    # Eight values in DIRECTION_LABELS order, or None when absent or malformed.
    directional: tuple[float, ...] | None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def find_latest(pattern: str = DEFAULT_GLOB) -> Path | None:
    """The most recently modified file matching ``pattern``, or None."""
    newest: tuple[float, str] | None = None
    for name in glob.glob(pattern):
        try:
            mtime = os.path.getmtime(name)
        except OSError:
            continue
        if newest is None or (mtime, name) > newest:
            newest = (mtime, name)
    return Path(newest[1]) if newest else None


def load(path: str | os.PathLike[str]) -> Calibration:
    """Parse one calibration file. Raises CalibrationError with a readable reason."""
    path = Path(path)
    try:
        mtime = path.stat().st_mtime
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise CalibrationError(f"{path}: file not found") from exc
    except OSError as exc:
        raise CalibrationError(f"{path}: cannot read ({exc.strerror or exc})") from exc
    except ValueError as exc:
        raise CalibrationError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise CalibrationError(f"{path}: top level is not a JSON object")
    firmware = data.get("firmwareScalars")
    if not isinstance(firmware, dict):
        raise CalibrationError(f"{path}: no firmwareScalars block")
    values: list[float] = []
    for key in _FIRMWARE_KEYS:
        number = _number(firmware.get(key))
        if number is None:
            raise CalibrationError(f"{path}: firmwareScalars.{key} is missing or not a number")
        values.append(number)

    directional: tuple[float, ...] | None = None
    raw = data.get("directionalScalars")
    if isinstance(raw, list) and len(raw) == len(DIRECTION_LABELS):
        numbers = [_number(item) for item in raw]
        if all(n is not None for n in numbers):
            directional = tuple(n for n in numbers if n is not None)
    return Calibration(path, mtime, Scalars(*values), directional)


def load_latest(pattern: str = DEFAULT_GLOB) -> Calibration | None:
    """Parse the newest matching file. None if there is none; raises if it is invalid."""
    path = find_latest(pattern)
    return load(path) if path is not None else None


def driver_scalars(firmware: Scalars, flip_y: bool = False) -> Scalars:
    """Map calibration scalars to the driver's DirScale parameters.

    X maps directly. The driver's +Y is "down"; whether the calibration's YPos
    means down as well is not confirmed, hence ``flip_y`` exchanges the two.
    """
    return firmware.flipped_y() if flip_y else firmware


def _fixed_prefix(pattern: str) -> str:
    """The directory part of ``pattern`` in front of its first wildcard."""
    for index, char in enumerate(pattern):
        if char in "*?[":
            return os.path.dirname(pattern[:index])
    return os.path.dirname(pattern)


def result_folder(pattern: str = DEFAULT_GLOB) -> Path | None:
    """The folder to show the user: where the newest result file lives.

    Without a result file it is the wildcard-free part of the pattern, provided
    that directory exists; otherwise None.
    """
    latest = find_latest(pattern)
    if latest is not None:
        return latest.parent
    prefix = _fixed_prefix(pattern)
    return Path(prefix) if prefix and os.path.isdir(prefix) else None


def display_path(path: str | os.PathLike[str]) -> str:
    """``path`` with the home directory abbreviated to "~"."""
    text = str(path)
    home = os.path.expanduser("~")
    if home not in ("", "/") and (text == home or text.startswith(home + os.sep)):
        return "~" + text[len(home):]
    return text


def watch_paths(pattern: str = DEFAULT_GLOB) -> list[str]:
    """Paths to watch so that new or replaced calibration files are noticed.

    Every matching file, its directory, and the deepest existing directory of
    the pattern's wildcard-free prefix (covers "nothing calibrated yet").
    """
    paths: set[str] = set()
    for name in glob.glob(pattern):
        paths.add(name)
        paths.add(os.path.dirname(name))
    prefix = _fixed_prefix(pattern)
    while prefix and not os.path.isdir(prefix):
        parent = os.path.dirname(prefix)
        if parent == prefix:
            break
        prefix = parent
    if prefix and os.path.isdir(prefix):
        paths.add(prefix)
    return sorted(paths)
