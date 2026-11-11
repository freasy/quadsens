"""Write the four multipliers into the settings of RawAccel (no Qt).

RawAccel (https://github.com/RawAccelOfficial/rawaccel) is the mouse driver
QuadSens configures on Windows, as yeetmouse is on Linux. Its settings are a
JSON file, settings.json, next to rawaccel.exe; "writer.exe settings.json"
checks the file and makes the driver use it.

What was confirmed in the RawAccel sources (wrapper/wrapper.cpp,
common/rawaccel.hpp, writer/Program.cs at the tags v1.7.1 and v1.6.1):

* The file is one object with "version", "defaultDeviceConfig", "profiles" (a
  list) and "devices". Every profile has a "name" and these four numbers:

      1.7.x  "Output DPI"                                       (default 1000)
             "Y/X output DPI ratio (vertical sens multiplier)"  (default 1)
             "L/R output DPI ratio (left sens multiplier)"      (default 1)
             "U/D output DPI ratio (up sens multiplier)"        (default 1)
      1.6.x  "Sensitivity multiplier"                           (default 1)
             "Y/X sensitivity ratio (vertical sens multiplier)"
             "L/R sensitivity ratio (left sens multiplier)"
             "U/D sensitivity ratio (up sens multiplier)"

  1.7 requires all of its keys, so it does not read a 1.6 file.
* They are applied after the acceleration curve:

      x *= s;  y *= s * yx;  if x < 0: x *= lr;  if y < 0: y *= ud

  with s = "Output DPI" / 1000 in 1.7 and s = "Sensitivity multiplier" in 1.6.
  Negative y is up. So right and down are scaled by s and s*yx, and left and
  up additionally by lr and ud.
* writer.exe takes one argument, the settings file. On any problem it shows a
  message box and exits with status 1.

Mapping of the multipliers R (right), L (left), D (down), U (up) onto a
profile whose four numbers were (s0, yx0, lr0, ud0) before QuadSens touched it:

      s = s0 * R     yx = yx0 * D / R     lr = lr0 * L / R     ud = ud0 * U / D

This is exact for a profile without acceleration and an approximation with
one, because RawAccel scales after its curve and the Linux driver before.
"New" is always computed from that remembered baseline, never from the
values currently in the file, so applying twice does not compound.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from json.decoder import scanstring
from pathlib import Path
from typing import Any

from .model import Scalars

SETTINGS_NAME = "settings.json"
WRITER_NAME = "writer.exe"
GUI_NAME = "rawaccel.exe"
BACKUP_PATTERN = "settings.quadsens-backup-%Y%m%d-%H%M%S.json"
WRITER_TIMEOUT = 20.0   # seconds; the driver itself delays a write by one second
ACCEL_X = "Whole or horizontal accel parameters"
ACCEL_Y = "Vertical accel parameters"
NO_ACCEL = "noaccel"
SAME = 1e-9


class RawAccelError(Exception):
    """The settings cannot be read, understood or written; the message says why."""


@dataclass(frozen=True)
class Schema:
    name: str            # "1.7" or "1.6"
    sens: str
    yx: str
    lr: str
    ud: str
    sens_label: str      # short names for the table
    unit: float          # value of "sens" that means factor 1

    @property
    def keys(self) -> tuple[str, str, str, str]:
        return (self.sens, self.yx, self.lr, self.ud)

    @property
    def labels(self) -> tuple[str, str, str, str]:
        return (self.sens_label, "Y/X ratio", "L/R ratio", "U/D ratio")


SCHEMA_17 = Schema("1.7", "Output DPI", "Y/X output DPI ratio (vertical sens multiplier)",
                   "L/R output DPI ratio (left sens multiplier)",
                   "U/D output DPI ratio (up sens multiplier)", "Output DPI", 1000.0)
SCHEMA_16 = Schema("1.6", "Sensitivity multiplier",
                   "Y/X sensitivity ratio (vertical sens multiplier)",
                   "L/R sensitivity ratio (left sens multiplier)",
                   "U/D sensitivity ratio (up sens multiplier)", "Sensitivity", 1.0)
SCHEMAS: tuple[Schema, ...] = (SCHEMA_17, SCHEMA_16)


@dataclass(frozen=True)
class Four:
    """The four numbers of a profile, in the order of Schema.keys."""

    sens: float
    yx: float
    lr: float
    ud: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.sens, self.yx, self.lr, self.ud)

    def is_close(self, other: Four, tolerance: float = 5e-7) -> bool:
        return all(abs(a - b) <= tolerance * max(1.0, abs(b))
                   for a, b in zip(self.as_tuple(), other.as_tuple()))

    def to_list(self) -> list[float]:
        return list(self.as_tuple())

    @classmethod
    def from_list(cls, raw: object) -> Four | None:
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            return None
        try:
            numbers = [float(item) for item in raw]
        except (TypeError, ValueError):
            return None
        if not all(n == n and abs(n) != float("inf") and n > 0 for n in numbers):
            return None
        return cls(*numbers)


# ---- reading -----------------------------------------------------------------------


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass(frozen=True)
class Document:
    """A parsed settings file."""

    text: str
    data: dict[str, Any]
    schema: Schema

    @property
    def version(self) -> str:
        version = self.data.get("version")
        return version if isinstance(version, str) else ""

    @property
    def profile_names(self) -> list[str]:
        return [str(profile.get("name", f"profile {index + 1}"))
                for index, profile in enumerate(self.data["profiles"])]

    def profile(self, index: int) -> dict[str, Any]:
        profiles = self.data["profiles"]
        if not 0 <= index < len(profiles):
            raise RawAccelError(f"there is no profile number {index + 1}")
        return profiles[index]

    def values(self, index: int = 0) -> Four:
        profile = self.profile(index)
        numbers = [float(profile[key]) for key in self.schema.keys]
        if not all(n > 0 for n in numbers):
            raise RawAccelError("the profile has a value that is zero or negative; "
                                "QuadSens does not change such a profile")
        return Four(*numbers)

    def has_acceleration(self, index: int = 0) -> bool:
        """Whether the profile uses an acceleration curve (then the mapping is approximate)."""
        profile = self.profile(index)
        for key in (ACCEL_X, ACCEL_Y):
            args = profile.get(key)
            if isinstance(args, dict) and str(args.get("mode", NO_ACCEL)) != NO_ACCEL:
                # The vertical parameters only count in "by component" mode, but a
                # curve that is configured there is reason enough for the note.
                if key == ACCEL_X or not _whole(profile):
                    return True
        return False


def _whole(profile: Mapping[str, Any]) -> bool:
    key = "Whole/combined accel (set false for 'by component' mode)"
    for holder in (profile, profile.get("Input speed calculation parameters")):
        if isinstance(holder, dict) and key in holder:
            return bool(holder[key])
    return True


def parse(text: str) -> Document:
    """Parse settings text and recognise its naming scheme; refuse anything else."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RawAccelError(f"settings.json is not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise RawAccelError("settings.json does not hold a JSON object")
    profiles = data.get("profiles")
    if not isinstance(profiles, list) or not profiles \
            or not all(isinstance(profile, dict) for profile in profiles):
        raise RawAccelError("settings.json has no list of profiles. It was not written "
                            "by RawAccel 1.6 or 1.7.")
    version = data.get("version")
    for schema in SCHEMAS:
        if all(all(key in profile and _is_number(profile[key]) for key in schema.keys)
               for profile in profiles):
            return Document(text, data, schema)
    told = f" (it says version {version})" if isinstance(version, str) else ""
    raise RawAccelError(
        f"settings.json{told} has neither the setting names of RawAccel 1.7 nor those "
        "of 1.6. QuadSens does not guess: nothing was changed.")


def read(path: str | os.PathLike[str]) -> Document:
    path = Path(path)
    try:
        # utf-8-sig: a byte order mark is accepted and not passed on.
        text = path.read_text(encoding="utf-8-sig", newline="")
    except FileNotFoundError as exc:
        raise RawAccelError(f"{path} does not exist. Start RawAccel once, so that it "
                            "writes its settings.") from exc
    except (OSError, UnicodeError) as exc:
        raise RawAccelError(f"{path} cannot be read ({exc})") from exc
    return parse(text)


# ---- the mapping ---------------------------------------------------------------------


def map_values(baseline: Four, scalars: Scalars, flip: bool = False) -> Four:
    """The four settings for the multipliers right, left, down, up on top of a baseline."""
    try:
        scalars.validate()
    except ValueError as exc:
        raise RawAccelError(str(exc)) from exc
    right, left, down, up = scalars.as_tuple()
    if flip:
        down, up = up, down
    return Four(baseline.sens * right, baseline.yx * down / right,
                baseline.lr * left / right, baseline.ud * up / down)


def effective(four: Four, schema: Schema) -> Scalars:
    """Multipliers right, left, down, up that a profile applies (without its curve)."""
    s = four.sens / schema.unit
    return Scalars(s, s * four.lr, s * four.yx, s * four.yx * four.ud)


def resolve_baseline(current: Four, stored: Four | None,
                     last_written: Four | None) -> tuple[Four, str]:
    """Which values count as the user's own: (baseline, note for the user).

    The stored baseline holds while the file still has either it or what
    QuadSens wrote last. If the file has something else, the user changed it
    in RawAccel since; those values are the new baseline.
    """
    if stored is None:
        return current, ""
    if current.is_close(stored) or (last_written is not None
                                    and current.is_close(last_written)):
        return stored, ""
    return current, ("The four settings were changed outside QuadSens since the last "
                     "apply; the values now in the file are taken as your own.")


def baseline_key(settings_path: str | os.PathLike[str], profile: str) -> str:
    """Stable name for remembering the baseline of one profile of one file."""
    text = os.path.normcase(os.path.abspath(str(settings_path))) + "\n" + profile
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def number_text(value: float) -> str:
    """A number as it is written into the file: at most six decimals, never an integer."""
    return repr(round(float(value), 6))


def accuracy_note(document: Document, index: int = 0) -> str:
    if document.has_acceleration(index):
        return ("This profile uses an acceleration curve. RawAccel applies the four "
                "settings after the curve, so the result is an approximation of the "
                "calibrated multipliers.")
    return ("This profile has no acceleration curve, so the four settings reproduce the "
            "multipliers exactly.")


# ---- changing the text ------------------------------------------------------------------

_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?")
_LITERAL = re.compile(r"true|false|null")
_SPACE = re.compile(r"[ \t\r\n]*")


def _space(text: str, index: int) -> int:
    return _SPACE.match(text, index).end()   # type: ignore[union-attr]


def _skip(text: str, index: int) -> int:
    """Index just after the JSON value that starts at ``index``."""
    char = text[index]
    if char == '"':
        return scanstring(text, index + 1)[1]
    if char in "{[":
        close = "}" if char == "{" else "]"
        index = _space(text, index + 1)
        if text[index] == close:
            return index + 1
        while True:
            if char == "{":
                index = _space(text, scanstring(text, index + 1)[1])
                index = _space(text, index + 1)   # the colon
            index = _space(text, _skip(text, index))
            if text[index] == close:
                return index + 1
            index = _space(text, index + 1)       # the comma
    match = _NUMBER.match(text, index) or _LITERAL.match(text, index)
    if match is None:
        raise ValueError(f"unexpected text at offset {index}")
    return match.end()


def _span(text: str, index: int, path: Sequence[str | int]) -> tuple[int, int]:
    """Start and end of the value reached by ``path`` from the value at ``index``."""
    index = _space(text, index)
    if not path:
        return index, _skip(text, index)
    head = path[0]
    if isinstance(head, str) and text[index] == "{":
        index = _space(text, index + 1)
        while text[index] != "}":
            key, index = scanstring(text, index + 1)
            index = _space(text, _space(text, index) + 1)
            if key == head:
                return _span(text, index, path[1:])
            index = _space(text, _skip(text, index))
            if text[index] == ",":
                index = _space(text, index + 1)
    elif isinstance(head, int) and text[index] == "[":
        index = _space(text, index + 1)
        position = 0
        while text[index] != "]":
            if position == head:
                return _span(text, index, path[1:])
            index = _space(text, _skip(text, index))
            if text[index] == ",":
                index = _space(text, index + 1)
            position += 1
    raise ValueError(f"{head!r} not found")


def patch(document: Document, index: int, new: Four) -> str:
    """The settings text with the four numbers of one profile replaced.

    Only those four number tokens change. Every other byte of the file stays:
    keys, their order, indentation, line endings and the other values.
    """
    text = document.text
    spans = []
    try:
        for key in document.schema.keys:
            start, end = _span(text, 0, ["profiles", index, key])
            if _NUMBER.fullmatch(text, start, end) is None:
                raise ValueError(f"{key!r} is not a number")
            spans.append((start, end))
    except (ValueError, IndexError) as exc:
        raise RawAccelError(f"settings.json could not be changed safely ({exc})") from exc
    for (start, end), value in sorted(zip(spans, new.as_tuple()), reverse=True):
        if not value > 0 or value != value or value == float("inf"):
            raise RawAccelError("a computed setting is not a positive number")
        text = text[:start] + number_text(value) + text[end:]
    # Proof: parsed again, the result differs from the original in those four only.
    expected = json.loads(document.text)
    for key, value in zip(document.schema.keys, new.as_tuple()):
        expected["profiles"][index][key] = float(number_text(value))
    if json.loads(text) != expected:
        raise RawAccelError("settings.json could not be changed safely (the result "
                            "did not match)")
    return text


# ---- files ------------------------------------------------------------------------------


def settings_path(folder: str | os.PathLike[str]) -> Path:
    return Path(folder) / SETTINGS_NAME


def writer_path(folder: str | os.PathLike[str]) -> Path | None:
    path = Path(folder) / WRITER_NAME
    return path if path.is_file() else None


def looks_like_rawaccel(folder: str | os.PathLike[str]) -> bool:
    folder = Path(folder)
    return any((folder / name).is_file() for name in (SETTINGS_NAME, WRITER_NAME, GUI_NAME))


def guess_folders(environ: Mapping[str, str] | None = None) -> list[Path]:
    """Places where people commonly unpack RawAccel; those that look like it, in order."""
    environ = os.environ if environ is None else environ
    home = environ.get("USERPROFILE", "")
    roots = [Path(home) / name for name in ("Downloads", "Desktop", "Documents")] if home \
        else []
    roots += [Path(environ[name]) for name in ("ProgramFiles", "ProgramFiles(x86)",
                                               "LOCALAPPDATA") if environ.get(name)]
    roots.append(Path(environ.get("SystemDrive", "C:") + "\\"))
    found: list[Path] = []
    for root in roots:
        for name in ("RawAccel", "rawaccel", "Raw Accel"):
            candidate = root / name
            try:
                if candidate.is_dir() and looks_like_rawaccel(candidate) \
                        and candidate not in found:
                    found.append(candidate)
            except OSError:
                continue
    return found


def backup_path(settings: str | os.PathLike[str], now: float | None = None) -> Path:
    stamp = time.localtime(time.time() if now is None else now)
    return Path(settings).with_name(time.strftime(BACKUP_PATTERN, stamp))


def write(settings: str | os.PathLike[str], text: str, backup: bool = False,
          now: float | None = None) -> Path | None:
    """Replace the settings file atomically. Returns the backup made first, if any.

    The new text goes to a temporary file in the same folder, which then takes
    the place of the old one, so the file is never half written.
    """
    settings = Path(settings)
    made: Path | None = None
    try:
        if backup:
            made = backup_path(settings, now)
            shutil.copy2(settings, made)
        temporary = settings.with_name(settings.name + ".quadsens-new")
        with open(temporary, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, settings)
    except OSError as exc:
        raise RawAccelError(f"{settings} could not be written ({exc.strerror or exc}). "
                            "Is the folder write-protected?") from exc
    return made


# ---- writer.exe ---------------------------------------------------------------------------

APPLIED, FAILED, TIMEOUT, MISSING, NOT_STARTED = (
    "applied", "failed", "timeout", "missing", "not started")


@dataclass(frozen=True)
class WriterResult:
    kind: str
    message: str

    @property
    def ok(self) -> bool:
        return self.kind == APPLIED


def writer_command(folder: str | os.PathLike[str]) -> list[str] | None:
    """The command that makes RawAccel load the settings, None without writer.exe."""
    writer = writer_path(folder)
    if writer is None:
        return None
    return [str(writer), str(settings_path(folder))]


def describe_writer(kind: str, exit_code: int = 0, output: str = "") -> WriterResult:
    """The outcome of a writer.exe run in words."""
    detail = " ".join(output.split())
    detail = f" ({detail})" if detail else ""
    if kind == APPLIED:
        return WriterResult(APPLIED, "RawAccel has loaded the new settings.")
    if kind == MISSING:
        return WriterResult(MISSING, "The settings are written, but writer.exe is not in "
                                     "that folder: open RawAccel and press Apply there, "
                                     "or run writer.exe with the settings file.")
    if kind == TIMEOUT:
        return WriterResult(TIMEOUT, "The settings are written, but writer.exe has not "
                                     "finished. It probably shows a message window; read "
                                     "it and close it.")
    if kind == NOT_STARTED:
        return WriterResult(NOT_STARTED, "The settings are written, but writer.exe could "
                                         f"not be started{detail}.")
    return WriterResult(FAILED, "The settings are written, but writer.exe did not accept "
                                f"them (exit status {exit_code}){detail}. RawAccel still "
                                "uses the previous settings.")


def run_writer(folder: str | os.PathLike[str],
               runner: Callable[..., Any] = subprocess.run,
               timeout: float = WRITER_TIMEOUT) -> WriterResult:
    """Run writer.exe and report (blocking; the window uses QProcess instead)."""
    command = writer_command(folder)
    if command is None:
        return describe_writer(MISSING)
    try:
        result = runner(command, capture_output=True, text=True, check=False,
                        timeout=timeout, cwd=str(folder))
    except subprocess.TimeoutExpired:
        return describe_writer(TIMEOUT)
    except OSError as exc:
        return describe_writer(NOT_STARTED, output=str(exc))
    if result.returncode == 0:
        return describe_writer(APPLIED)
    return describe_writer(FAILED, result.returncode,
                           (result.stderr or "") + (result.stdout or ""))
