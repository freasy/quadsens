"""Is the driver applying the values right now? The words for it (no Qt).

Both platforms get the same kind of answer (the Linux side is in
quadsens.active_linux, so that Windows does not load the Linux driver code): a mark, a headline, one line of
detail and a kind that picks the tint. The answer is only ever as strong as
what was actually read back; where nothing can be read it says what was
sent, and that it cannot be verified.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import rawaccel_live
from .model import Scalars
from .rawaccel import Four

# Kinds; the tint of the indicator follows the kind, the meaning is in the words.
ACTIVE, NEUTRAL, DIFFERENT, SENT, NOT_APPLIED, INACTIVE, UNVERIFIED = (
    "active", "neutral", "different", "sent", "not applied", "inactive", "unverified")
TINTS = {ACTIVE: "success", SENT: "success", NEUTRAL: "info", UNVERIFIED: "info",
         DIFFERENT: "warning", NOT_APPLIED: "warning", INACTIVE: "warning"}
MARKS = {ACTIVE: "✓", SENT: "✓", NEUTRAL: "○", UNVERIFIED: "?", DIFFERENT: "≠",
         NOT_APPLIED: "⚠", INACTIVE: "✕"}
DIRECTION_TITLES = ("Right", "Left", "Down", "Up")
SETTING_TITLES = ("output DPI", "Y/X", "L/R", "U/D")


@dataclass(frozen=True)
class Indicator:
    kind: str
    headline: str
    detail: str = ""

    @property
    def mark(self) -> str:
        return MARKS[self.kind]

    @property
    def tint(self) -> str:
        return TINTS[self.kind]

    def short(self) -> str:
        """For the front-page strip: mark and headline."""
        return "%s %s" % (self.mark, self.headline)

    def spoken(self) -> str:
        return "%s. %s" % (self.headline, self.detail) if self.detail else self.headline


def clock(when: float | None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(when)) if when else ""


def directions_text(scalars: Scalars) -> str:
    return ", ".join("%s %.4f" % (title, value)
                     for title, value in zip(DIRECTION_TITLES, scalars.as_tuple()))


def _number(value: float) -> str:
    return ("%.6f" % value).rstrip("0").rstrip(".")


def settings_text(four: Four) -> str:
    return ", ".join("%s %s" % (title, _number(value))
                     for title, value in zip(SETTING_TITLES, four.as_tuple()))


# ---- Windows -----------------------------------------------------------------

# States of RawAccel's driver file, as quadsens.win_install names them.
DRIVER_MISSING, DRIVER_PENDING = "missing", "restart pending"


def windows_indicator(driver: str, live: rawaccel_live.Live, profile: str,
                      sent: Four | None = None, sent_at: float | None = None,
                      baseline: Four | None = None,
                      started: float | None = None,
                      in_file: Four | None = None) -> Indicator:
    """What RawAccel's driver is doing, as far as it can be told.

    ``driver`` is the state of the driver file, ``live`` the answer of the
    driver itself, ``sent`` and ``sent_at`` what RawAccel's writer last
    accepted from QuadSens for ``profile``, ``baseline`` the profile's own
    values, ``started`` when Windows started, ``in_file`` what settings.json
    holds for the profile.
    """
    if driver == DRIVER_MISSING:
        return Indicator(INACTIVE, "Not active: RawAccel's driver is not installed",
                         "Install RawAccel with the button on top of the window.")
    if driver == DRIVER_PENDING:
        return Indicator(INACTIVE, "Not active yet: restart Windows",
                         "RawAccel's driver is installed and starts with the next "
                         "restart.")
    told = "Sent %s: %s." % (clock(sent_at), settings_text(sent)) \
        if sent is not None and sent_at else ""
    if live.kind == rawaccel_live.NOT_LOADED:
        return Indicator(INACTIVE, "Not active: RawAccel's driver is not running",
                         "Its device was not found. Restart Windows; if that does not "
                         "help, run RawAccel's installer again.")
    if live.kind == rawaccel_live.EMPTY:
        return Indicator(NOT_APPLIED, "Not applied since Windows started",
                         "The driver runs, but holds no settings: the mouse is passed "
                         "through unchanged. Press Apply.")
    if live.kind == rawaccel_live.OK:
        values = live.values(profile)
        if values is None:
            return Indicator(DIFFERENT, "Active, but without this profile",
                             "The driver holds %s. Press Apply to send this file."
                             % ", ".join(name for name, _four in live.profiles))
        if sent is not None and values.is_close(sent, 1e-6):
            if baseline is not None and sent.is_close(baseline, 1e-6):
                return Indicator(NEUTRAL, "Neutral: the driver holds the profile's own values",
                                 "Read from the driver: %s." % settings_text(values))
            return Indicator(ACTIVE, "Active in driver",
                             "Applied %s, read back from the driver: %s."
                             % (clock(sent_at) or "earlier", settings_text(values)))
        if baseline is not None and values.is_close(baseline, 1e-6):
            return Indicator(NEUTRAL, "Neutral: the driver holds the profile's own values",
                             "Read from the driver: %s." % settings_text(values))
        if in_file is not None and values.is_close(in_file, 1e-6):
            return Indicator(ACTIVE, "Active in driver",
                             "Read back from the driver, equal to settings.json: %s"
                             % settings_text(values))
        return Indicator(DIFFERENT, "Active, but different from QuadSens",
                         "Driver: %s. %s" % (settings_text(values),
                                             told or "QuadSens has not applied values yet."))
    # The driver cannot be read (another version, an answer that does not fit,
    # or not on Windows): say what was sent, and that it is not verified.
    why = {rawaccel_live.OTHER_VERSION:
           "this driver version (%s) is not one QuadSens can read back"
           % ".".join(str(n) for n in live.version or ()),
           rawaccel_live.UNREADABLE: "the driver's answer was not understood",
           }.get(live.kind, "the driver cannot be read here")
    if sent is None or not sent_at:
        return Indicator(NOT_APPLIED, "Not applied by QuadSens yet",
                         "Cannot be verified: %s." % why)
    if started is not None and sent_at < started:
        return Indicator(NOT_APPLIED, "Not applied since Windows started",
                         "RawAccel forgets its settings at a restart; the last Apply was "
                         "before it (%s). Press Apply." % clock(sent_at))
    return Indicator(SENT, "Sent to the driver %s, accepted by RawAccel's writer"
                     % clock(sent_at),
                     "%s Cannot be read back: %s." % (settings_text(sent), why))
