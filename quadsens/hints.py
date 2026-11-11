"""What to tell the user when a part of the chain is not ready yet (no Qt).

Each problem has a short form that fits into the window without pushing the
layout around, and a long form with the exact commands for Help > Setup help.
"""

from __future__ import annotations

from .calibration import NO_RESULT, NO_RESULT_FOLDER  # noqa: F401 (kept for importers)
from .driver import DriverState, DriverStatus
from .uhid_text import (  # noqa: F401 (kept for importers)
    UHID_DENIED,
    UHID_MISSING,
    UHID_OK,
    uhid_hint,
    uhid_short,
)
from .persist import TOOL_NAME, TRUSTED_DIRS, SaveState

SETUP_HELP = "Help > Setup help"
DRIVER_SETUP = "Help > Driver setup"

FIRST_STEPS: tuple[str, ...] = (
    "Start the virtual mouse, then start the game.",
    "Reset the driver values to 1.0 and apply, so that the calibration measures the "
    "unscaled mouse.",
    "Run the per-direction sensitivity calibration in the game. The result shows up "
    "in the window on its own.",
    "Press Use result.",
    "Press Apply.",
    "Press Save to config, so that the values survive a module reload.",
)

ACCEL_HINT = ("The acceleration curve of the driver is edited in the Acceleration card "
              "at the bottom of the window (View > Acceleration… jumps there). Set it "
              "before you calibrate.")

PERSIST = (
    "Applied values last until the yeetmouse module is reloaded (or the next boot). "
    "Save to config keeps them: it runs the fork's \"yeetmousectl save\".")

FLIP_Y = (
    "The default mapping is verified against real results; switch this on only if up "
    "and down feel swapped on your setup.")

def driver_status(state: DriverState, helper_installed: bool = True) -> str:
    """Short status line for the driver panel; at most two lines in the window."""
    status = state.status
    if status is DriverStatus.OK:
        return "Status: values read from the driver."
    if status is DriverStatus.NOT_LOADED:
        return "Status: module not loaded (see the driver check above)."
    if status is DriverStatus.UNSUPPORTED:
        return "Status: not the fork's module (see the driver check above)."
    if status is DriverStatus.NOT_READABLE:
        return "Status: values not readable (see the driver check above)."
    return f"Status: error reading the driver ({state.message})."


def driver_help(state: DriverState, helper_installed: bool = True) -> str:
    """Long form of driver_status with the commands, "" when the driver is fine."""
    status = state.status
    if status is DriverStatus.OK:
        return ""
    if status is DriverStatus.NOT_LOADED:
        return ("The yeetmouse module is not loaded. If the fork is installed, load it "
                "(sudo modprobe yeetmouse) and press Read again; otherwise install it "
                f"with {DRIVER_SETUP}.")
    if status is DriverStatus.UNSUPPORTED:
        return ("The loaded yeetmouse module has no DirScale parameters. Install the "
                f"forked module with {DRIVER_SETUP} (or by hand from the branch "
                "feature/directional-scalars) and reload it.")
    if status is DriverStatus.NOT_READABLE:
        text = ("The driver values are not readable. Join group yeetmouse (sudo usermod "
                "-aG yeetmouse $USER) and log in again. If you are a member already, the "
                "module was reloaded: sudo systemctl restart yeetmouse.service.")
        if helper_installed:
            return text + " Until then Apply asks for authorization."
        return text + " Apply cannot write either: the helper is not installed system-wide."
    return f"Reading the driver failed: {state.message}."


def tool_missing() -> str:
    """Why Save to config is not available."""
    return (f"{TOOL_NAME} was not found in {', '.join(TRUSTED_DIRS)}. Install the "
            f"userspace tools of the yeetmouse fork ({DRIVER_SETUP} does that); Save to "
            "config needs them.")


def save_state_text(state: SaveState, tool_found: bool = True) -> str:
    """One line that says whether the driver's values are in the config file."""
    if state is SaveState.SAVED:
        return "✓ Saved to config."
    if not tool_found:
        return f"⚠ Cannot save: {TOOL_NAME} not found ({SETUP_HELP})."
    if state is SaveState.DIFFERS:
        return "⚠ Applied, not saved: lost on module reload."
    if state is SaveState.MISSING_KEYS:
        return "⚠ Not saved: the config has no directional values."
    if state is SaveState.NO_CONFIG:
        return "⚠ Not saved: there is no readable config file yet."
    return "Saved state unknown (driver values not readable)."


def setup_problems(uhid_access: str, driver: DriverState, helper_installed: bool,
                   tool_found: bool) -> list[str]:
    """Long forms of everything that is not ready, for the Setup help dialog."""
    problems = [uhid_hint(uhid_access), driver_help(driver, helper_installed),
                "" if tool_found else tool_missing()]
    return [text for text in problems if text]
