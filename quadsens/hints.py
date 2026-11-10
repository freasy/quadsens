"""What to tell the user when a part of the chain is not ready yet (no Qt).

Every text names the concrete next step, so that an empty panel is never the
only feedback.
"""

from __future__ import annotations

from .driver import DriverState, DriverStatus

FIRST_STEPS: tuple[str, ...] = (
    "Start the virtual mouse, then start the game.",
    "Set the four driver values to 1 (Reset to 1.0, Apply), so that the calibration "
    "measures the unscaled mouse.",
    "Run the per-direction sensitivity calibration in the game.",
    "Press Use calibration. If up and down feel swapped afterwards, toggle Flip Y.",
    "Press Apply.",
)

NO_RESULT = (
    "No calibration file found. Start the virtual mouse and run the per-direction "
    "calibration in the game; the result shows up here on its own.")

NO_RESULT_FOLDER = (
    "There is no result folder yet. It is created when the game saves its first "
    "calibration.")

PERSIST = (
    "Values applied here last until the yeetmouse module is reloaded (or the next "
    "boot). To keep them, save them with the fork's tool: "
    "yeetmousectl save /etc/yeetmouse.conf")

UHID_OK, UHID_MISSING, UHID_DENIED = "ok", "missing", "denied"


def uhid_hint(access: str) -> str:
    """Why the virtual mouse cannot be started, or "" when it can."""
    if access == UHID_MISSING:
        return ("/dev/uhid does not exist, so no virtual mouse can be created. Load the "
                "kernel module: sudo modprobe uhid")
    if access == UHID_DENIED:
        return ("/dev/uhid is not writable for you. Install the udev rule "
                "99-quadsens.rules, join group input and log in again (README, "
                "\"One-time setup\").")
    return ""


def driver_status(state: DriverState, helper_installed: bool = True) -> str:
    """One status line for the driver panel, including what to do about it."""
    status = state.status
    if status is DriverStatus.OK:
        return "Status: values read from the driver."
    if status is DriverStatus.NOT_LOADED:
        return ("Status: module not loaded. Load the forked yeetmouse module "
                "(sudo modprobe yeetmouse), then press Read again.")
    if status is DriverStatus.UNSUPPORTED:
        return ("Status: module loaded, but it has no DirScale parameters. Install the "
                "forked yeetmouse module (branch feature/directional-scalars) and "
                "reload it.")
    if status is DriverStatus.NOT_READABLE:
        text = ("Status: not readable. Join group yeetmouse (sudo usermod -aG yeetmouse "
                "$USER) and log in again. Already a member? The module was reloaded: "
                "sudo systemctl restart yeetmouse.service.")
        if helper_installed:
            return text + " Until then Apply asks for authorization."
        return text + " Apply cannot write either: the helper is not installed system-wide."
    return f"Status: error reading the driver ({state.message})."
