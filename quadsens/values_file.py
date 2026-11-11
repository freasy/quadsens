"""The QuadSens values file: four multipliers to carry from one machine to another (no Qt).

A small JSON document. It holds the four multipliers, optionally the eight
measured directions, when it was written and by which QuadSens version, and
nothing else: no user name, no path, no device serial.

    {
      "format": "quadsens-values",
      "version": 1,
      "multipliers": {"right": 1.011, "left": 0.991, "down": 1.006, "up": 0.996},
      "measured": [1.012, 1.006, 0.991, 0.996, 0.988, 0.992, 1.009, 1.014],
      "created": "2026-11-11T10:54:39Z",
      "quadsens": "0.4.0"
    }

"multipliers" are what the driver applies to movement in that direction;
"measured" is in the order E, NE, N, NW, W, SW, S, SE.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass

from .model import SCALE_MAX, SCALE_MIN, Scalars

FORMAT = "quadsens-values"
VERSION = 1
EXTENSION = ".quadsens.json"
FILE_FILTER = "QuadSens values (*.quadsens.json *.json);;All files (*)"
DIRECTIONS: tuple[str, ...] = ("right", "left", "down", "up")
MAX_SIZE = 64 * 1024   # a values file is a few hundred bytes


class ValuesError(Exception):
    """The file is not a usable QuadSens values file; the message says why."""


@dataclass(frozen=True)
class Values:
    scalars: Scalars                           # right, left, down, up
    measured: tuple[float, ...] | None = None  # eight directions, if known
    created: str = ""                          # UTC, ISO 8601
    app_version: str = ""


def _number(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValuesError(f"{what} is not a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValuesError(f"{what} is not a finite number")
    return number


def dump(scalars: Scalars, measured: tuple[float, ...] | None = None,
         app_version: str = "", created: float | None = None) -> str:
    """The values file as text. Raises ValuesError for values outside the range."""
    try:
        scalars.validate()
    except ValueError as exc:
        raise ValuesError(str(exc)) from exc
    document: dict[str, object] = {
        "format": FORMAT,
        "version": VERSION,
        "multipliers": {name: round(value, 6)
                        for name, value in zip(DIRECTIONS, scalars.as_tuple())},
    }
    if measured is not None:
        if len(measured) != 8:
            raise ValuesError("there must be eight measured values")
        document["measured"] = [round(_number(v, "a measured value"), 6) for v in measured]
    stamp = time.gmtime(time.time() if created is None else created)
    document["created"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", stamp)
    document["quadsens"] = app_version
    return json.dumps(document, indent=2) + "\n"


def load(text: str) -> Values:
    """Parse and check a values file. Raises ValuesError with a readable reason."""
    if len(text) > MAX_SIZE:
        raise ValuesError("the file is too large to be a QuadSens values file")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValuesError(f"not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ValuesError("the top level is not a JSON object")
    if data.get("format") != FORMAT:
        raise ValuesError("this is not a QuadSens values file (no \"format\": "
                          f"\"{FORMAT}\")")
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValuesError("the file has no usable version number")
    if version > VERSION:
        raise ValuesError(f"the file has version {version}; this QuadSens reads up to "
                          f"version {VERSION}. Update QuadSens.")
    if version < 1:
        raise ValuesError(f"the file has the unknown version {version}")
    multipliers = data.get("multipliers")
    if not isinstance(multipliers, dict):
        raise ValuesError("the file has no \"multipliers\" block")
    unknown = sorted(set(multipliers) - set(DIRECTIONS))
    if unknown:
        raise ValuesError("unknown direction in \"multipliers\": " + ", ".join(
            str(name) for name in unknown))
    numbers = []
    for name in DIRECTIONS:
        if name not in multipliers:
            raise ValuesError(f"the multiplier for \"{name}\" is missing")
        number = _number(multipliers[name], f"the multiplier for \"{name}\"")
        if not SCALE_MIN <= number <= SCALE_MAX:
            raise ValuesError(f"the multiplier for \"{name}\" ({number:g}) is outside "
                              f"{SCALE_MIN:g} to {SCALE_MAX:g}")
        numbers.append(number)
    measured: tuple[float, ...] | None = None
    raw = data.get("measured")
    if raw is not None:
        if not isinstance(raw, list) or len(raw) != 8:
            raise ValuesError("\"measured\" must be a list of eight numbers")
        measured = tuple(_number(item, "a measured value") for item in raw)
    created = data.get("created", "")
    app_version = data.get("quadsens", "")
    return Values(Scalars(*numbers), measured,
                  created if isinstance(created, str) else "",
                  app_version if isinstance(app_version, str) else "")
