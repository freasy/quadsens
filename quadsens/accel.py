"""The acceleration settings of both drivers, read-only (no Qt).

One neutral description (Accel) and a reader per driver. Nothing here writes
acceleration settings; writers and a curve preview are meant to plug in
later (see docs/accel-modes.md for the mapping between the drivers).

Rules for what is shown: a mode is named only when its number or name is
one this module knows from the sources; anything else is shown by its raw
name. Parameters keep the names their driver gives them, because the same
name does not mean the same thing in both drivers.

yeetmouse (fork), read in driver/accel.c, shared_definitions.h and
install_files/yeetmouse.conf.sample: the module parameter AccelerationMode
holds the number of the mode; Acceleration, Exponent, Midpoint, Motivity,
Offset, InputCap, OutputCap and UseSmoothing are shared by all modes, each
mode using some of them. The config file names the mode "AccelMode_<Name>"
under the key accelMode.

RawAccel, read in wrapper/wrapper.cpp at v1.7.1 and v1.6.1: a profile has
"Whole or horizontal accel parameters" and "Vertical accel parameters", each
with "mode" and the parameters of all modes; whether both are used is the
"Whole/combined accel" switch.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OFF, LINEAR, POWER, CLASSIC, MOTIVITY, SYNCHRONOUS, NATURAL, JUMP, LUT, CUSTOM = (
    "off", "linear", "power", "classic", "motivity", "synchronous", "natural", "jump",
    "lut", "custom curve")
TITLES = {OFF: "Off", LINEAR: "Linear", POWER: "Power", CLASSIC: "Classic",
          MOTIVITY: "Motivity", SYNCHRONOUS: "Synchronous", NATURAL: "Natural",
          JUMP: "Jump", LUT: "Look-up table", CUSTOM: "Custom curve"}
YEETMOUSE, RAWACCEL = "yeetmouse", "RawAccel"


@dataclass(frozen=True)
class Accel:
    """What a driver is set to, as far as it could be read."""

    driver: str
    mode: str = ""                 # one of the names above, "" if not known
    raw_mode: str = ""             # the mode as the driver names it
    params: tuple[tuple[str, float], ...] = ()     # main parameters, driver's own names
    extras: tuple[str, ...] = ()   # short remarks: caps, offsets, per-axis, smoothing
    problem: str = ""              # why nothing could be read, "" if something was
    source: str = ""               # where it was read from

    @property
    def known(self) -> bool:
        return bool(self.mode)

    @property
    def is_off(self) -> bool:
        return self.mode == OFF

    def title(self) -> str:
        """Name of the mode; an unknown one by its raw name, never a guess."""
        if self.known:
            return TITLES[self.mode]
        return "mode %s (not known to QuadSens)" % (self.raw_mode or "?")

    def summary(self) -> str:
        """One line, for example "Acceleration: Linear, Acceleration 0.05; cap none"."""
        if self.problem:
            return "Acceleration: not read (%s)" % self.problem
        if self.is_off:
            return "Acceleration: off"
        parts = [self.title()] + ["%s %s" % (name, number(value))
                                  for name, value in self.params]
        text = "Acceleration: " + ", ".join(parts)
        return text + ("; " + "; ".join(self.extras) if self.extras else "")

    def short(self) -> str:
        """The compact form, for example "Acceleration: Linear, 0.05; cap none"."""
        if self.problem or self.is_off or not self.known:
            return self.summary() if not self.problem else "Acceleration: not read"
        values = ", ".join(number(value) for _name, value in self.params)
        text = "Acceleration: " + self.title() + (", " + values if values else "")
        caps = [extra for extra in self.extras if "cap" in extra]
        return text + ("; " + caps[0] if caps else "")


def number(value: float) -> str:
    return ("%.6f" % value).rstrip("0").rstrip(".") if value == value else "?"


# ---- yeetmouse -----------------------------------------------------------------

# shared_definitions.h: enum AccelMode. 0 ("Current") is what the driver sets
# when a mode cannot work; it then applies no curve.
YEETMOUSE_MODES = {0: OFF, 1: LINEAR, 2: POWER, 3: CLASSIC, 4: MOTIVITY, 5: SYNCHRONOUS,
                   6: NATURAL, 7: JUMP, 8: LUT, 9: CUSTOM}
YEETMOUSE_MODE_NAMES = {"AccelMode_Current": 0, "AccelMode_Linear": 1, "AccelMode_Power": 2,
                        "AccelMode_Classic": 3, "AccelMode_Motivity": 4,
                        "AccelMode_Synchronous": 5, "AccelMode_Natural": 6,
                        "AccelMode_Jump": 7, "AccelMode_Lut": 8,
                        "AccelMode_CustomCurve": 9}
# Which of the shared parameters a mode reads (driver/accel_modes.c; the
# formulas are written out in docs/accel-modes.md, section 1.5).
YEETMOUSE_USES = {
    LINEAR: ("Acceleration",),
    CLASSIC: ("Acceleration", "Exponent"),
    POWER: ("Acceleration", "Exponent", "Midpoint"),
    NATURAL: ("Acceleration", "Exponent", "Midpoint"),
    JUMP: ("Acceleration", "Midpoint", "Exponent"),
    MOTIVITY: ("Acceleration", "Exponent", "Midpoint", "Motivity"),
    SYNCHRONOUS: ("Acceleration", "Exponent", "Midpoint", "Motivity"),
    LUT: (), CUSTOM: (),
}
# Read in addition while UseSmoothing is 1: the limit the curve is bent towards.
YEETMOUSE_USES_SMOOTHED = {LINEAR: ("Midpoint",), CLASSIC: ("Midpoint",), POWER: ("Motivity",)}
# Module parameter -> key in /etc/yeetmouse.conf (install_files/yeetmouse.conf.sample).
YEETMOUSE_CONFIG_KEYS = {
    "AccelerationMode": "accelMode", "Acceleration": "accel", "Exponent": "exponent",
    "Midpoint": "midpoint", "Motivity": "motivity", "Offset": "offset",
    "InputCap": "inCap", "OutputCap": "outCap", "UseSmoothing": "useSmoothing",
}
YEETMOUSE_GUI = "yeetmouse"       # the fork's Makefile installs its GUI as /usr/bin/yeetmouse


def _float(text: object) -> float | None:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float("inf") else None


def _yeetmouse(values: Mapping[str, str], source: str) -> Accel:
    raw = str(values.get("AccelerationMode", "")).strip()
    if not raw:
        return Accel(YEETMOUSE, problem="the mode is not there", source=source)
    if raw in YEETMOUSE_MODE_NAMES:
        code: int | None = YEETMOUSE_MODE_NAMES[raw]
    else:
        code = int(raw) if raw.isdigit() else None
    mode = YEETMOUSE_MODES.get(code, "") if code is not None else ""
    if not mode:
        return Accel(YEETMOUSE, raw_mode=raw, source=source)
    if mode == OFF:
        return Accel(YEETMOUSE, OFF, raw, source=source)
    smoothing = str(values.get("UseSmoothing", "")).strip()
    names = YEETMOUSE_USES[mode]
    if smoothing == "1":
        names += YEETMOUSE_USES_SMOOTHED.get(mode, ())
    params = []
    for name in names:
        value = _float(values.get(name))
        if value is not None:
            params.append((name, value))
    extras = []
    offset = _float(values.get("Offset"))
    if offset:
        extras.append("offset %s" % number(offset))
    caps = [(title, _float(values.get(name)))
            for title, name in (("input cap", "InputCap"), ("output cap", "OutputCap"))]
    set_caps = ["%s %s" % (title, number(value)) for title, value in caps if value]
    extras.append(", ".join(set_caps) if set_caps else "cap none")
    # The fork's UseSmoothing switch means something else in every mode (a
    # limit for linear, classic and power; gain instead of velocity for the
    # others), so it is shown under its own name.
    if smoothing in ("0", "1") and mode not in (LUT, CUSTOM):
        extras.append("UseSmoothing " + ("on" if smoothing == "1" else "off"))
    acceleration = _float(values.get("Acceleration"))
    if mode == LINEAR and acceleration == 0:
        # driver/accel_modes.c: Linear with an acceleration of 0 applies no curve.
        return Accel(YEETMOUSE, OFF, raw, source=source)
    return Accel(YEETMOUSE, mode, raw, tuple(params), tuple(extras), source=source)


def read_yeetmouse_params(params_dir: str | os.PathLike[str]) -> Accel:
    """From the parameters of the loaded module. Never raises."""
    folder = Path(params_dir)
    source = str(folder)
    if not folder.is_dir():
        return Accel(YEETMOUSE, problem="the module is not loaded", source=source)
    values: dict[str, str] = {}
    refused = False
    for name in YEETMOUSE_CONFIG_KEYS:
        try:
            values[name] = (folder / name).read_text(encoding="ascii", errors="replace")
        except PermissionError:
            refused = True
        except OSError:
            continue
    if "AccelerationMode" not in values:
        return Accel(YEETMOUSE, problem="not readable for you" if refused
                     else "this module has no acceleration mode parameter", source=source)
    return _yeetmouse(values, source)


def read_yeetmouse_config(path: str | os.PathLike[str]) -> Accel:
    """From the config file, for when the module cannot be read. Never raises."""
    source = str(path)
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return Accel(YEETMOUSE, problem="%s cannot be read: %s"
                     % (path, exc.strerror or exc), source=source)
    found: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            key, value = line.split("=", 1)
            found[key.strip()] = value.strip()
    values = {name: found[key] for name, key in YEETMOUSE_CONFIG_KEYS.items() if key in found}
    return _yeetmouse(values, source)


def read_yeetmouse(params_dir: str | os.PathLike[str],
                   config_path: str | os.PathLike[str] | None = None) -> Accel:
    """The loaded module if it can be read, else the config file, else the reason."""
    live = read_yeetmouse_params(params_dir)
    if not live.problem or config_path is None:
        return live
    stored = read_yeetmouse_config(config_path)
    if stored.problem:
        return live
    return Accel(stored.driver, stored.mode, stored.raw_mode, stored.params,
                 stored.extras + ("from the config file, not from the driver",),
                 source=stored.source)


# ---- RawAccel ------------------------------------------------------------------

# wrapper/wrapper.cpp: the names of the accel modes in settings.json. "motivity"
# is the name in 1.6, "synchronous" in 1.7.
RAWACCEL_MODES = {"noaccel": OFF, "classic": CLASSIC, "jump": JUMP, "natural": NATURAL,
                  "synchronous": SYNCHRONOUS, "motivity": MOTIVITY, "power": POWER,
                  "lut": LUT}
# The parameters each mode reads (common/accel-*.hpp), by their names in settings.json.
RAWACCEL_USES = {
    CLASSIC: ("acceleration", "exponentClassic"),
    JUMP: ("smooth",),
    NATURAL: ("decayRate", "limit"),
    SYNCHRONOUS: ("gamma", "motivity", "syncSpeed", "smooth"),
    MOTIVITY: ("gamma", "motivity", "syncSpeed", "smooth"),
    POWER: ("scale", "exponentPower"),
    LUT: (),
}
WHOLE_ARGS, VERTICAL_ARGS = "Whole or horizontal accel parameters", "Vertical accel parameters"
WHOLE_KEY = "Whole/combined accel (set false for 'by component' mode)"
SPEED_ARGS = "Input speed calculation parameters"
RAWACCEL_GUI = "rawaccel.exe"


def _rawaccel_args(args: Mapping[str, Any]) -> Accel:
    raw = args.get("mode")
    raw = raw if isinstance(raw, str) else ""
    mode = RAWACCEL_MODES.get(raw.lower(), "")
    if not mode:
        return Accel(RAWACCEL, raw_mode=raw or "?")
    if mode == OFF:
        return Accel(RAWACCEL, OFF, raw)
    params = [(name, value) for name in RAWACCEL_USES[mode]
              if (value := _float(args.get(name))) is not None]
    extras = []
    for title, key in (("input offset", "inputOffset"), ("output offset", "outputOffset")):
        value = _float(args.get(key))
        if value:
            extras.append("%s %s" % (title, number(value)))
    cap = args.get("Cap / Jump")
    cap_mode = args.get("Cap mode")
    if isinstance(cap, Mapping) and mode in (CLASSIC, POWER, JUMP):
        x, y = _float(cap.get("x")), _float(cap.get("y"))
        what = "jump at" if mode == JUMP else "cap (%s)" % cap_mode \
            if isinstance(cap_mode, str) else "cap"
        if x is not None and y is not None:
            extras.append("%s input %s, output %s" % (what, number(x), number(y)))
    gain = args.get("Gain / Velocity")
    if isinstance(gain, bool):
        extras.append("gain" if gain else "legacy (velocity)")
    if mode == LUT and isinstance(args.get("data"), list):
        extras.append("%d values" % len(args["data"]))
    return Accel(RAWACCEL, mode, raw, tuple(params), tuple(extras))


def read_rawaccel_profile(profile: Mapping[str, Any], source: str = "settings.json") -> Accel:
    """From one profile of a settings.json (1.6 or 1.7). Never raises."""
    whole = profile.get(WHOLE_ARGS)
    if not isinstance(whole, Mapping):
        return Accel(RAWACCEL, problem="the profile has no acceleration settings",
                     source=source)
    first = _rawaccel_args(whole)
    speed = profile.get(SPEED_ARGS)
    combined = speed.get(WHOLE_KEY) if isinstance(speed, Mapping) else profile.get(WHOLE_KEY)
    extras = first.extras
    vertical = profile.get(VERTICAL_ARGS)
    if combined is False and isinstance(vertical, Mapping):
        second = _rawaccel_args(vertical)
        extras += ("horizontal only; vertical: " + second.summary()[len("Acceleration: "):],)
        if first.is_off and not second.is_off:
            # Not off as a whole: say so instead of "off".
            return Accel(RAWACCEL, "", "by component", (), extras[-1:], source=source)
    return Accel(RAWACCEL, first.mode, first.raw_mode, first.params, extras, source=source)
