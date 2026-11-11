"""Is the yeetmouse module applying the values right now? (no Qt)

From a fresh read of the module parameters; see quadsens.active for the
shared words and kinds.
"""

from __future__ import annotations

from .active import (
    ACTIVE,
    DIFFERENT,
    INACTIVE,
    NEUTRAL,
    UNVERIFIED,
    Indicator,
    clock,
    directions_text,
)
from .driver import DriverState, DriverStatus
from .model import Scalars


def linux_indicator(state: DriverState, applied: Scalars | None = None,
                    applied_at: float | None = None) -> Indicator:
    """From a fresh read of the module parameters.

    ``applied`` and ``applied_at`` are what QuadSens last wrote, and when.
    """
    wrote = ("QuadSens last wrote %s at %s." % (directions_text(applied), clock(applied_at))
             if applied is not None and applied_at else "")
    if state.status is DriverStatus.NOT_LOADED:
        return Indicator(INACTIVE, "Not active: the yeetmouse module is not loaded",
                         "Nothing is scaled. " + wrote if wrote else "Nothing is scaled.")
    if state.status is DriverStatus.UNSUPPORTED:
        return Indicator(INACTIVE, "Not active: the loaded yeetmouse has no directional "
                                   "scaling", "It is not the fork QuadSens needs.")
    if state.status is DriverStatus.NOT_READABLE or state.scalars is None:
        reason = ("the driver values are not readable for you"
                  if state.status is DriverStatus.NOT_READABLE
                  else state.message or "the driver values could not be read")
        return Indicator(UNVERIFIED, "Cannot be verified: " + reason,
                         wrote or "QuadSens has not written values yet.")
    live = state.scalars
    if live.is_close(Scalars.unity()):
        return Indicator(NEUTRAL, "Neutral: all four driver values are 1.0",
                         "The module is loaded and scales nothing. " + wrote
                         if wrote and applied is not None and not applied.is_close(live)
                         else "The module is loaded and scales nothing.")
    if applied is not None and live.is_close(applied):
        return Indicator(ACTIVE, "Active in driver",
                         "Applied %s: %s" % (clock(applied_at) or "earlier",
                                             directions_text(live)))
    if applied is None:
        return Indicator(DIFFERENT, "Active, but not set by QuadSens",
                         "Driver: %s." % directions_text(live))
    return Indicator(DIFFERENT, "Active, but different from QuadSens",
                     "Driver: %s. %s" % (directions_text(live), wrote))
