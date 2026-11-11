"""Editing the acceleration curve: the model, its limits, and both drivers (no Qt).

QuadSens edits six modes that both drivers compute the same way once the
parameters are renamed: Off, Linear, Classic, Power, Natural and Jump (see
docs/accel-modes.md). Everything else stays in the drivers' own settings
programs and is shown read-only, with the reason.

The limits are stricter than either driver's own. They come from an
independent check of both drivers' sources with numeric comparison (see the
document, "Rules the editor enforces"): the fixed-point code of the yeetmouse
fork divides without range checks, and a userspace build of its unmodified
sources stopped with a divide error for parameter sets that its own settings
program allows. What a running kernel does in that case was not tested.
QuadSens therefore refuses every set for which one of the fork's internal
points could leave the safe range, on both platforms, so that the same
curves are available on both.

There is one gate: checked(). Everything that produces driver values calls
it and raises AccelError when the curve is not allowed; there is no other way
to get those values.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

OFF, LINEAR, CLASSIC, POWER, NATURAL, JUMP = (
    "off", "linear", "classic", "power", "natural", "jump")
MODES = (OFF, LINEAR, CLASSIC, POWER, NATURAL, JUMP)
MODE_TITLES = {OFF: "Off", LINEAR: "Linear", CLASSIC: "Classic", POWER: "Power",
               NATURAL: "Natural", JUMP: "Jump"}
MODE_HELP = {
    OFF: "No acceleration: the multiplier is 1 at every speed.",
    LINEAR: "The multiplier grows in a straight line with the speed.",
    CLASSIC: "The multiplier grows with a power of the speed.",
    POWER: "The multiplier is a power of the speed, starting below 1 for slow movement.",
    NATURAL: "The multiplier approaches a limit, quickly at first and then ever slower.",
    JUMP: "The multiplier steps from 1 to another value at one speed.",
}
SPEED_UNIT = "counts/ms"
# No internal point of the curve may lie beyond this speed (rule R1), and the
# multiplier at 1000 counts/ms may not exceed it (rule R5).
SAFE_LIMIT = 1.0e5


@dataclass(frozen=True)
class Field:
    key: str
    label: str            # neutral words
    low: float
    high: float
    default: float
    step: float
    decimals: int
    help: str
    yeetmouse: str        # the yeetmouse parameter that holds it
    rawaccel: str         # the key in RawAccel's settings


# The ranges are the intersection of what RawAccel accepts, what the fork
# accepts and what its fixed-point code computes safely (rule R4).
FIELDS: dict[str, tuple[Field, ...]] = {
    OFF: (),
    LINEAR: (
        Field("accel", "Acceleration", 1e-4, 1.0, 0.005, 0.001, 4,
              "How much the multiplier grows per count/ms.", "Acceleration", "acceleration"),
    ),
    CLASSIC: (
        Field("accel", "Acceleration", 1e-4, 2.0, 0.005, 0.001, 4,
              "Scales the speed before the power is taken.", "Acceleration", "acceleration"),
        Field("exponent", "Exponent", 1.01, 5.0, 2.0, 0.05, 3,
              "2 is the linear curve; larger grows faster.", "Exponent", "exponentClassic"),
    ),
    POWER: (
        Field("scale", "Scale", 1e-3, 5.0, 1.0, 0.01, 3,
              "Scales the speed before the power is taken.", "Acceleration", "scale"),
        Field("exponent", "Exponent", 0.01, 1.0, 0.05, 0.01, 3,
              "The power the scaled speed is raised to.", "Exponent", "exponentPower"),
        Field("output_offset", "Output offset", 0.0, 5.0, 0.0, 0.05, 3,
              "The multiplier for slow movement, held until the curve reaches it.",
              "Midpoint", "outputOffset"),
    ),
    NATURAL: (
        Field("decay_rate", "Decay rate", 1e-3, 5.0, 0.1, 0.01, 3,
              "How quickly the limit is approached.", "Acceleration", "decayRate"),
        Field("limit", "Limit", 0.01, 8.0, 1.5, 0.05, 3,
              "The multiplier the curve approaches. It cannot be 1.", "Exponent", "limit"),
        Field("offset", "Input offset", 0.0, 50.0, 0.0, 0.5, 2,
              "Below this speed the multiplier stays 1.", "Midpoint", "inputOffset"),
    ),
    JUMP: (
        Field("input", "Jump at speed", 0.1, 50.0, 15.0, 0.5, 2,
              "The speed at which the multiplier changes.", "Midpoint", "Cap / Jump: x"),
        Field("output", "Multiplier after the jump", 0.01, 10.0, 1.5, 0.05, 3,
              "The multiplier above that speed.", "Acceleration", "Cap / Jump: y"),
        Field("smooth", "Smoothness", 0.0, 1.0, 0.5, 0.05, 3,
              "0 is a hard step; 1 spreads the change widest.", "Exponent", "smooth"),
    ),
}
# The optional cap ("gain cap by output") of three modes.
CAP = {
    LINEAR: Field("cap", "Cap", 0.1, 10.0, 1.5, 0.05, 3,
                  "The multiplier the curve is bent towards and never passes.",
                  "Midpoint (with UseSmoothing 1)", "Cap / Jump: y"),
    CLASSIC: Field("cap", "Cap", 0.1, 10.0, 1.5, 0.05, 3,
                   "The multiplier the curve is bent towards and never passes.",
                   "Midpoint (with UseSmoothing 1)", "Cap / Jump: y"),
    POWER: Field("cap", "Cap", 0.0, 10.0, 1.5, 0.05, 3,
                 "The multiplier the curve is bent towards and never passes. It must be "
                 "above the output offset.", "Motivity (with UseSmoothing 1)",
                 "Cap / Jump: y"),
}
GAIN_MODES = (NATURAL, JUMP)
GAIN_HELP = ("On: the parameters describe how fast the pointer speeds up (the slope of "
             "output speed over input speed), which feels smoother. Off: they describe the "
             "multiplier directly. yeetmouse calls this UseSmoothing, RawAccel \"Gain\".")
MIN_LIMIT_DISTANCE = 0.01          # Natural: |limit - 1| must be at least this


class AccelError(Exception):
    """The curve may not be written; ``reasons`` say why, in words for the user."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(" ".join(reasons))
        self.reasons = reasons


@dataclass(frozen=True)
class Curve:
    """One acceleration setting in neutral terms.

    ``values`` holds the numbers of the mode's fields; ``cap`` is None for
    "no cap" (and for modes without one); ``gain`` only matters for Natural
    and Jump.
    """

    mode: str = OFF
    values: tuple[tuple[str, float], ...] = ()
    cap: float | None = None
    gain: bool = True

    def get(self, key: str) -> float:
        for name, value in self.values:
            if name == key:
                return value
        raise KeyError(key)

    def as_dict(self) -> dict[str, Any]:
        """The form the test vectors use."""
        data: dict[str, Any] = dict(self.values)
        if self.mode in CAP and self.cap is not None:
            data["cap"] = self.cap
        if self.mode in GAIN_MODES:
            data["gain"] = self.gain
        return data

    def describe(self) -> str:
        """In words, for example "Classic: Acceleration 0.005, Exponent 2; cap 1.5"."""
        if self.mode == OFF:
            return "Off"
        fields = {field.key: field for field in FIELDS[self.mode]}
        parts = ["%s %s" % (fields[key].label, number(value)) for key, value in self.values]
        text = "%s: %s" % (MODE_TITLES[self.mode], ", ".join(parts))
        if self.mode in CAP:
            text += "; cap %s" % (number(self.cap) if self.cap is not None else "off")
        if self.mode in GAIN_MODES:
            text += "; gain %s" % ("on" if self.gain else "off")
        return text

    def same(self, other: Curve | None) -> bool:
        return other is not None and self.describe() == other.describe()


def number(value: float) -> str:
    return ("%.10f" % value).rstrip("0").rstrip(".")


def make(mode: str, cap: float | None = None, gain: bool = True, **values: float) -> Curve:
    """A curve from keyword values; fields not given take their defaults."""
    if mode not in FIELDS:
        raise ValueError("unknown mode %r" % mode)
    given = tuple((field.key, float(values.pop(field.key, field.default)))
                  for field in FIELDS[mode])
    if values:
        raise ValueError("unknown fields for %s: %s" % (mode, ", ".join(sorted(values))))
    return Curve(mode, given, float(cap) if cap is not None and mode in CAP else None,
                 bool(gain) if mode in GAIN_MODES else True)


def from_dict(mode: str, data: Mapping[str, Any]) -> Curve:
    """A curve from the form the test vectors use."""
    values = {key: value for key, value in data.items() if key not in ("cap", "gain")}
    return make(mode, data.get("cap"), bool(data.get("gain", True)), **values)


def default_curve(mode: str) -> Curve:
    return make(mode)


# ---- the reference curve ------------------------------------------------------------


def _softplus(z: float) -> float:
    """log(1 + exp(z)) without overflow."""
    return z + math.log1p(math.exp(-z)) if z > 30 else math.log1p(math.exp(z))


def _logistic(z: float) -> float:
    """1 / (1 + exp(z)) without overflow."""
    if z > 700:
        return 0.0
    return 1.0 / (1.0 + math.exp(z))


def _log_point(numerator: float, exponent_inverse: float, scale: float) -> float:
    """log of (numerator ** exponent_inverse / scale), for positive arguments."""
    return math.log(numerator) * exponent_inverse - math.log(scale)


def cap_point(curve: Curve) -> float | None:
    """The speed at which the cap takes over, None without a cap. May be huge: inf then."""
    if curve.mode not in CAP or curve.cap is None:
        return None
    try:
        if curve.mode == POWER:
            if curve.cap <= 0:
                return 0.0
            exponent = curve.get("exponent")
            return math.exp(_log_point(curve.cap / (exponent + 1), 1 / exponent,
                                       curve.get("scale")))
        exponent = 2.0 if curve.mode == LINEAR else curve.get("exponent")
        height = abs(curve.cap - 1)
        if height == 0:
            return 0.0
        return math.exp(_log_point(height / exponent, 1 / (exponent - 1), curve.get("accel")))
    except (OverflowError, ValueError, ZeroDivisionError):
        return math.inf


def offset_point(curve: Curve) -> float | None:
    """Power: the speed up to which the output offset holds. None for other modes."""
    if curve.mode != POWER:
        return None
    offset, exponent = curve.get("output_offset"), curve.get("exponent")
    if offset <= 0:
        return 0.0
    try:
        return math.exp(_log_point(offset / (exponent + 1), 1 / exponent, curve.get("scale")))
    except (OverflowError, ValueError, ZeroDivisionError):
        return math.inf


def multiplier(curve: Curve, speed: float) -> float:
    """The sensitivity multiplier at ``speed`` (counts/ms, positive).

    One formula per mode, the one both drivers implement. For the preview and
    for the checks; it does not ask whether the curve is allowed.
    """
    mode, v = curve.mode, speed
    if mode == OFF or v <= 0:
        return 1.0
    if mode in (LINEAR, CLASSIC):
        accel = curve.get("accel")
        exponent = 2.0 if mode == LINEAR else curve.get("exponent")
        base = math.pow(accel * v, exponent - 1)
        if curve.cap is None:
            return 1 + base
        height = abs(curve.cap - 1)
        sign = -1.0 if curve.cap < 1 else 1.0
        if height == 0:
            return 1.0
        point = cap_point(curve)
        assert point is not None
        if v < point:
            return 1 + sign * base
        return 1 + sign * (height - height * point * (exponent - 1) / exponent / v)
    if mode == POWER:
        scale, exponent, offset = (curve.get("scale"), curve.get("exponent"),
                                   curve.get("output_offset"))
        start = offset_point(curve) or 0.0
        constant = start * offset * exponent / (exponent + 1)

        def base_power(x: float) -> float:
            return offset if x <= start else math.pow(scale * x, exponent) + constant / x
        if curve.cap is None:
            return base_power(v)
        point = cap_point(curve)
        assert point is not None
        if v < point:
            return base_power(v)
        return curve.cap + (base_power(point) - curve.cap) * point / v
    if mode == NATURAL:
        offset, limit = curve.get("offset"), curve.get("limit") - 1
        rate = curve.get("decay_rate") / abs(limit)
        if v <= offset:
            return 1.0
        u = offset - v
        decay = math.exp(rate * u)
        if curve.gain:
            return 1 + (limit * (decay / rate - u) - limit / rate) / v
        return 1 + limit * (1 - (offset - decay * u) / v)
    if mode == JUMP:
        at, height, smooth = curve.get("input"), curve.get("output") - 1, curve.get("smooth")
        rate = 0.0 if smooth * at < 1 else 2 * math.pi / (smooth * at)
        if rate == 0:
            # Exactly at the jump the drivers differ without gain (RawAccel is
            # already after it, yeetmouse still before); this follows RawAccel.
            if v < at:
                return 1.0
            return 1 + height * (v - at) / v if curve.gain else 1 + height
        if curve.gain:
            def area(x: float) -> float:
                return height * (x + _softplus(rate * (at - x)) / rate)
            return 1 + (area(v) - area(0.0)) / v
        return 1 + height * _logistic(rate * (at - v))
    raise ValueError("unknown mode %r" % mode)


def marks(curve: Curve) -> list[tuple[str, float]]:
    """Named speeds worth marking in a preview: where the cap, offset or jump is."""
    found: list[tuple[str, float]] = []
    point = cap_point(curve)
    if point is not None and 0 < point < math.inf:
        found.append(("cap", point))
    start = offset_point(curve)
    if start:
        found.append(("offset ends", start))
    if curve.mode == NATURAL and curve.get("offset") > 0:
        found.append(("offset", curve.get("offset")))
    if curve.mode == JUMP:
        found.append(("jump", curve.get("input")))
    return found


# ---- the limits ---------------------------------------------------------------------


def _speed_text(value: float) -> str:
    return "more than 1e300" if value == math.inf else "%.3g" % value


def violations(curve: Curve) -> list[str]:
    """Why this curve may not be written; an empty list when it may.

    The rules, as named in docs/accel-modes.md: R4 ranges, R1 internal points
    not beyond 100000 counts/ms, R2 and R3 for Power, R5 the multiplier at
    1000 counts/ms.
    """
    if curve.mode not in FIELDS:
        return ["%r is not a mode QuadSens edits." % curve.mode]
    reasons: list[str] = []
    for field in FIELDS[curve.mode]:                                      # R4
        try:
            value = curve.get(field.key)
        except KeyError:
            reasons.append("%s is missing." % field.label)
            continue
        if not math.isfinite(value) or not field.low <= value <= field.high:
            reasons.append("%s must be between %s and %s (it is %s)."
                           % (field.label, number(field.low), number(field.high),
                              number(value) if math.isfinite(value) else "not a number"))
    cap_field = CAP.get(curve.mode)
    if cap_field is not None and curve.cap is not None:
        if not math.isfinite(curve.cap) or not cap_field.low <= curve.cap <= cap_field.high:
            reasons.append("Cap must be between %s and %s, or off."
                           % (number(cap_field.low), number(cap_field.high)))
    if curve.mode == NATURAL and not reasons \
            and abs(curve.get("limit") - 1) < MIN_LIMIT_DISTANCE - 1e-12:
        reasons.append("Limit must differ from 1 by at least %s: at 1 there is no curve, "
                       "and yeetmouse switches acceleration off without saying so."
                       % number(MIN_LIMIT_DISTANCE))
    if reasons:
        return reasons
    if curve.mode == POWER:
        scale, exponent, offset = (curve.get("scale"), curve.get("exponent"),
                                   curve.get("output_offset"))
        highest = 99 * scale * exponent
        if offset > highest:                                               # R2
            reasons.append(
                "Output offset %s is too large for this scale and exponent (at most %s): "
                "yeetmouse would switch acceleration off without saying so."
                % (number(offset), number(round(highest, 6))))
        if curve.cap is not None and curve.cap <= offset:                  # R3
            reasons.append("Cap must be above the output offset (%s)." % number(offset))
        start = offset_point(curve)
        if start is not None and start > SAFE_LIMIT:                       # R1
            reasons.append(
                "The output offset would hold up to %s counts/ms. yeetmouse cannot compute "
                "a point beyond 100000 safely; lower the offset or raise the scale or the "
                "exponent." % _speed_text(start))
    point = cap_point(curve)
    if point is not None and point > SAFE_LIMIT:                           # R1
        reasons.append(
            "The cap would only be reached at %s counts/ms. yeetmouse cannot compute a "
            "point beyond 100000 safely; lower the cap or make the curve steeper."
            % _speed_text(point))
    if not reasons and curve.mode != OFF:                                  # R5
        try:
            high = multiplier(curve, 1000.0)
        except (OverflowError, ValueError, ZeroDivisionError):
            high = math.inf
        if not high <= SAFE_LIMIT:
            reasons.append(
                "At 1000 counts/ms the multiplier would be %s. yeetmouse cannot compute "
                "values that large safely; add a cap or flatten the curve."
                % _speed_text(high))
    return reasons


def checked(curve: Curve) -> Curve:
    """The gate: return the curve, or raise AccelError with the reasons."""
    reasons = violations(curve)
    if reasons:
        raise AccelError(reasons)
    return curve


# ---- yeetmouse ----------------------------------------------------------------------

FORK_MODE_NUMBERS = {OFF: 0, LINEAR: 1, POWER: 2, CLASSIC: 3, NATURAL: 6, JUMP: 7}
FORK_MODES = {number_: mode for mode, number_ in FORK_MODE_NUMBERS.items()}
# The four slots every mode shares, in the order they are written, then the
# mode. UseSmoothing and update follow, in that order (rule R7).
FORK_SLOTS = ("Acceleration", "Exponent", "Midpoint", "Motivity")
FORK_STRING_PARAMS = (*FORK_SLOTS, "AccelerationMode")
FORK_SWITCH, FORK_UPDATE = "UseSmoothing", "update"
# Must be neutral for QuadSens to edit the curve (they change it, and RawAccel
# has no counterpart): parameter -> the neutral value.
FORK_NEUTRAL = {"Offset": 0.0, "InputCap": 0.0, "OutputCap": 0.0, "PreScale": 1.0}
FORK_READ = (*FORK_STRING_PARAMS, FORK_SWITCH, *FORK_NEUTRAL)
# Values of the slots a mode does not use: harmless and valid.
_FORK_FILL = {"Acceleration": 1.0, "Exponent": 2.0, "Midpoint": 1.0, "Motivity": 1.5}


def fork_decimal(value: float) -> str:
    """A number as the fork's parser reads it reliably (rule R6).

    Fixed notation with ten decimals: the parser fails on exponent notation
    and on a missing leading digit, and then keeps the old value.
    """
    if not math.isfinite(value) or value < 0 or value >= 1e6:
        raise AccelError(["%r cannot be written to yeetmouse." % value])
    return "%.10f" % value


def _fork_slots(curve: Curve) -> tuple[dict[str, float], int]:
    slots = dict(_FORK_FILL)
    smoothing = 0
    mode = curve.mode
    if mode in (LINEAR, CLASSIC):
        slots["Acceleration"] = curve.get("accel")
        if mode == CLASSIC:
            slots["Exponent"] = curve.get("exponent")
        if curve.cap is not None:
            slots["Midpoint"], smoothing = curve.cap, 1
    elif mode == POWER:
        slots["Acceleration"], slots["Exponent"] = curve.get("scale"), curve.get("exponent")
        slots["Midpoint"] = curve.get("output_offset")
        if curve.cap is not None:
            slots["Motivity"], smoothing = curve.cap, 1
    elif mode == NATURAL:
        slots["Acceleration"], slots["Exponent"] = curve.get("decay_rate"), curve.get("limit")
        slots["Midpoint"], smoothing = curve.get("offset"), int(curve.gain)
    elif mode == JUMP:
        slots["Acceleration"], slots["Exponent"] = curve.get("output"), curve.get("smooth")
        slots["Midpoint"], smoothing = curve.get("input"), int(curve.gain)
    return slots, smoothing


def fork_values(curve: Curve) -> dict[str, str]:
    """What to write to the yeetmouse parameters for ``curve``, as strings.

    All four slots, the mode and UseSmoothing, every time: a slot left from
    another mode would change the curve. Raises AccelError for a curve that
    is not allowed.
    """
    checked(curve)
    slots, smoothing = _fork_slots(curve)
    values = {name: fork_decimal(slots[name]) for name in FORK_SLOTS}
    values["AccelerationMode"] = str(FORK_MODE_NUMBERS[curve.mode])
    values[FORK_SWITCH] = str(smoothing)
    return values


def fork_write_order(values: Mapping[str, str]) -> list[tuple[str, str]]:
    """The writes in the order that is safe (rule R7): strings, UseSmoothing, update.

    UseSmoothing takes effect at once, the rest only with update; written the
    other way round, the driver would for a moment combine a new switch with
    old constants.
    """
    order = [(name, values[name]) for name in FORK_STRING_PARAMS]
    return [*order, (FORK_SWITCH, values[FORK_SWITCH]), (FORK_UPDATE, "1")]


@dataclass(frozen=True)
class Found:
    """What a driver is set to: a curve QuadSens can edit, or the reason why not."""

    curve: Curve | None = None
    reason: str = ""              # why it is read-only, "" if editable

    @property
    def editable(self) -> bool:
        return self.curve is not None


def _parse(text: object) -> float | None:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def classify_fork(values: Mapping[str, str]) -> Found:
    """Turn the parameters read from the module into a curve, or say why not.

    Nothing is converted silently: a configuration outside the six modes and
    their limits is read-only.
    """
    raw_mode = str(values.get("AccelerationMode", "")).strip()
    if not raw_mode.isdigit():
        return Found(reason="The acceleration mode could not be read.")
    for name, neutral in FORK_NEUTRAL.items():
        value = _parse(values.get(name))
        if value is None:
            return Found(reason="%s could not be read." % name)
        if abs(value - neutral) > 1e-9:
            return Found(reason="%s is %s. QuadSens edits the curve only while it is %s, "
                                "because it changes the curve in a way RawAccel has no "
                                "setting for." % (name, number(value), number(neutral)))
    mode = FORK_MODES.get(int(raw_mode))
    if mode is None:
        return Found(reason="The driver is set to mode %s, which QuadSens does not edit."
                     % raw_mode)
    if mode == OFF:
        return Found(make(OFF))
    slots = {name: _parse(values.get(name)) for name in FORK_SLOTS}
    smoothing = str(values.get(FORK_SWITCH, "")).strip()
    if smoothing not in ("0", "1") or any(slots[name] is None for name in FORK_SLOTS):
        return Found(reason="The parameters of the curve could not be read.")
    a, e, m, v = (slots[name] for name in FORK_SLOTS)
    assert a is not None and e is not None and m is not None and v is not None
    on = smoothing == "1"
    if m < 0:
        return Found(reason="Midpoint is negative.")
    if mode in (LINEAR, CLASSIC):
        if on and m == 0:
            return Found(reason="UseSmoothing is on with Midpoint 0: the driver then bends "
                                "the curve down to 0, which RawAccel cannot do.")
        if mode == CLASSIC and e <= 1:
            return Found(reason="Exponent is %s; Classic needs more than 1." % number(e))
        curve = make(mode, m if on else None, accel=a) if mode == LINEAR \
            else make(mode, m if on else None, accel=a, exponent=e)
    elif mode == POWER:
        curve = make(POWER, v if on else None, scale=a, exponent=e, output_offset=m)
    elif mode == NATURAL:
        curve = make(NATURAL, gain=on, decay_rate=a, limit=e, offset=m)
    else:
        if e > 1 or a <= 0:
            return Found(reason="The Jump parameters are outside what both drivers accept.")
        curve = make(JUMP, gain=on, input=m, output=a, smooth=e)
    reasons = violations(curve)
    if reasons:
        return Found(reason="The driver's current values are outside what QuadSens "
                            "writes: " + " ".join(reasons))
    return Found(curve)


# ---- RawAccel -----------------------------------------------------------------------

RA_ARGS = "Whole or horizontal accel parameters"
RA_VERTICAL = "Vertical accel parameters"
RA_WHOLE = "Whole/combined accel (set false for 'by component' mode)"
RA_SPEED = "Input speed calculation parameters"
RA_GAIN, RA_CAP, RA_CAP_MODE = "Gain / Velocity", "Cap / Jump", "Cap mode"
RA_HALFLIVES = ("Time in ms after which an input is weighted at half its original value.",
                "Time in ms after which scale is weighted at half its original value.",
                "Time in ms after which an output is weighted at half its original value.")
RA_DOMAIN = "Stretches domain for horizontal vs vertical inputs"
RA_RANGE = "Stretches accel range for horizontal vs vertical inputs"
RA_SPEED_CAP = "Input Speed Cap"
RA_DPI_KEYS = ("DPI (normalizes input speed unit: counts/ms -> in/s)",
               "DPI (normalizes sens to 1000dpi and converts input speed unit: "
               "counts/ms -> in/s)")
RA_MODE_NAMES = {OFF: "noaccel", LINEAR: "classic", CLASSIC: "classic", POWER: "power",
                 NATURAL: "natural", JUMP: "jump"}
# Path inside the accel arguments -> value. A path is a tuple of keys.
RaChanges = dict[tuple[str, ...], Any]


def rawaccel_changes(curve: Curve) -> RaChanges:
    """The values to set inside RawAccel's accel arguments for ``curve``.

    Besides the mode's own parameters, always the cap, the cap mode, the gain
    switch and (classic) the input offset: those slots are shared between
    modes, and what another mode left there would change the curve. Raises
    AccelError for a curve that is not allowed.
    """
    checked(curve)
    mode = curve.mode
    changes: RaChanges = {("mode",): RA_MODE_NAMES[mode]}
    if mode == OFF:
        return changes
    if mode in (LINEAR, CLASSIC):
        changes[("acceleration",)] = curve.get("accel")
        changes[("exponentClassic",)] = 2.0 if mode == LINEAR else curve.get("exponent")
        changes[("inputOffset",)] = 0.0
    elif mode == POWER:
        changes[("scale",)] = curve.get("scale")
        changes[("exponentPower",)] = curve.get("exponent")
        changes[("outputOffset",)] = curve.get("output_offset")
    elif mode == NATURAL:
        changes[("decayRate",)] = curve.get("decay_rate")
        changes[("limit",)] = curve.get("limit")
        changes[("inputOffset",)] = curve.get("offset")
    elif mode == JUMP:
        changes[(RA_CAP, "x")] = curve.get("input")
        changes[(RA_CAP, "y")] = curve.get("output")
        changes[("smooth",)] = curve.get("smooth")
    if mode in CAP:
        changes[(RA_CAP, "y")] = curve.cap if curve.cap is not None else 0.0
        changes[(RA_CAP_MODE,)] = "output"
        changes[(RA_GAIN,)] = True
    else:
        changes[(RA_GAIN,)] = curve.gain
    return changes


def rawaccel_errors(args: Mapping[str, Any]) -> list[str]:
    """RawAccel's own check of one set of accel arguments (rawaccel-validate.hpp, 1.7.1).

    It looks at every field, whatever the mode. An empty list means RawAccel's
    writer would accept the arguments.
    """
    def num(key: str, default: float) -> float:
        value = args.get(key, default)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
            else math.nan
    errors: list[str] = []
    mode = str(args.get("mode", "noaccel"))
    cap = args.get(RA_CAP)
    cap_x = float(cap.get("x", 15.0)) if isinstance(cap, Mapping) else math.nan
    cap_y = float(cap.get("y", 1.5)) if isinstance(cap, Mapping) else math.nan
    cap_mode = str(args.get(RA_CAP_MODE, "output"))
    in_out = mode == "jump" or (mode in ("classic", "power") and cap_mode == "in_out")
    input_offset, output_offset = num("inputOffset", 0.0), num("outputOffset", 0.0)
    if not input_offset >= 0 or not output_offset >= 0:
        errors.append("offset can not be negative")
    if not cap_x >= 0:
        errors.append("cap (input) can not be negative")
    elif cap_x == 0 and in_out:
        errors.append("cap (input) can not be 0")
    if not cap_y >= 0:
        errors.append("cap (output) can not be negative")
    elif cap_y == 0 and in_out:
        errors.append("cap (output) can not be 0")
    if (mode == "classic" and 0 < cap_x < input_offset and cap_mode != "output") or \
            (mode == "power" and 0 < cap_y < output_offset and cap_mode != "input"):
        errors.append("cap < offset")
    for key, name, default in (("acceleration", "acceleration", 0.005), ("scale", "scale", 1.0),
                               ("gamma", "gamma", 1.0), ("decayRate", "decay rate", 0.1)):
        if not num(key, default) > 0:
            errors.append(name + " must be positive")
    if not num("exponentClassic", 2.0) > 1:
        errors.append("exponent must be greater than 1")
    if not num("exponentPower", 0.05) > 0:
        errors.append("exponent must be positive")
    if not num("limit", 1.5) > 0:
        errors.append("limit must be positive")
    if not 0 <= num("smooth", 0.5) <= 1:
        errors.append("smooth must be between 0 and 1")
    return errors


def apply_changes(args: Mapping[str, Any], changes: RaChanges) -> dict[str, Any]:
    """A copy of the accel arguments with ``changes`` set."""
    result: dict[str, Any] = {key: (dict(value) if isinstance(value, Mapping) else value)
                              for key, value in args.items()}
    for path, value in changes.items():
        holder = result
        for key in path[:-1]:
            holder = holder[key]
        holder[path[-1]] = value
    return result


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def classify_rawaccel(profile: Mapping[str, Any],
                      settings: Mapping[str, Any] | None = None) -> Found:
    """Turn a profile of settings.json into a curve, or say why it is read-only.

    ``settings`` is the whole file; its device settings decide whether speeds
    are in counts/ms.
    """
    args = profile.get(RA_ARGS)
    if not isinstance(args, Mapping):
        return Found(reason="The profile has no acceleration settings.")
    speed = profile.get(RA_SPEED)
    holder = speed if isinstance(speed, Mapping) else profile        # 1.7 / 1.6
    if holder.get(RA_WHOLE, True) is not True:
        return Found(reason="The profile uses separate curves for horizontal and vertical "
                            "movement (\"by component\"). yeetmouse has one curve.")
    if holder.get("lpNorm", 2) != 2:
        return Found(reason="lpNorm is %s; yeetmouse always measures speed with 2."
                     % holder.get("lpNorm"))
    for key in RA_HALFLIVES:
        if holder.get(key, 0) != 0:
            return Found(reason="A smoothing halflife is set; yeetmouse has no such setting.")
    for key, what in ((RA_DOMAIN, "domain"), (RA_RANGE, "range")):
        weights = profile.get(key)
        if isinstance(weights, Mapping) and (weights.get("x", 1) != 1
                                             or weights.get("y", 1) != 1):
            return Found(reason="The %s weights are not 1; yeetmouse has no such setting."
                         % what)
    if profile.get(RA_SPEED_CAP, 0) != 0:
        return Found(reason="An input speed cap is set; yeetmouse has no such setting.")
    if isinstance(settings, Mapping):
        configs = [settings.get("defaultDeviceConfig")]
        configs += [device.get("config") for device in settings.get("devices") or []
                    if isinstance(device, Mapping)]
        for config in configs:
            if isinstance(config, Mapping) and any(config.get(key, 0) != 0
                                                   for key in RA_DPI_KEYS):
                return Found(reason="A device DPI is set, so RawAccel measures speed in "
                                    "another unit than yeetmouse does.")
    mode = str(args.get("mode", "")).lower()
    if mode == "noaccel":
        return Found(make(OFF))
    if mode not in ("classic", "power", "natural", "jump"):
        return Found(reason="The profile uses the mode \"%s\", which QuadSens does not edit."
                     % (mode or "?"))
    cap = args.get(RA_CAP)
    names = {"classic": ("acceleration", "exponentClassic", "inputOffset"),
             "power": ("scale", "exponentPower", "outputOffset"),
             "natural": ("decayRate", "limit", "inputOffset"), "jump": ("smooth",)}[mode]
    if not isinstance(cap, Mapping) or not _is_number(cap.get("x")) \
            or not _is_number(cap.get("y")) or not all(_is_number(args.get(n)) for n in names) \
            or not isinstance(args.get(RA_GAIN), bool):
        return Found(reason="The acceleration settings of the profile are incomplete.")
    gain, cap_x, cap_y = args[RA_GAIN], float(cap["x"]), float(cap["y"])
    if mode in ("classic", "power"):
        if args.get(RA_CAP_MODE) != "output":
            return Found(reason="The cap mode is \"%s\"; QuadSens edits caps by output only."
                         % args.get(RA_CAP_MODE))
        if not gain and cap_y > 0:
            return Found(reason="The profile uses a cap without gain (a hard limit). "
                                "QuadSens edits gain caps only.")
        cap_value = cap_y if cap_y > 0 else None
        if mode == "classic":
            if args["inputOffset"] != 0:
                return Found(reason="Classic with an input offset: yeetmouse computes that "
                                    "differently.")
            exponent = float(args["exponentClassic"])
            curve = make(LINEAR, cap_value, accel=args["acceleration"]) if exponent == 2 \
                else make(CLASSIC, cap_value, accel=args["acceleration"], exponent=exponent)
        else:
            if cap_y > 0 and cap_y == args["outputOffset"]:
                return Found(reason="Cap and output offset are equal; yeetmouse rejects that.")
            curve = make(POWER, cap_value, scale=args["scale"],
                         exponent=args["exponentPower"], output_offset=args["outputOffset"])
    elif mode == "natural":
        if args["limit"] == 1:
            return Found(reason="Limit is 1; yeetmouse rejects that.")
        curve = make(NATURAL, gain=gain, decay_rate=args["decayRate"], limit=args["limit"],
                     offset=args["inputOffset"])
    else:
        curve = make(JUMP, gain=gain, input=cap_x, output=cap_y, smooth=args["smooth"])
    reasons = violations(curve)
    if reasons:
        return Found(reason="The profile's current values are outside what QuadSens "
                            "writes: " + " ".join(reasons))
    return Found(curve)
