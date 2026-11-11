"""Texts about /dev/uhid access (no Qt, no other QuadSens module).

Separate from quadsens.hints so that the virtual mouse card, which is shared
with the Windows window, does not pull in the yeetmouse driver code.
"""

from __future__ import annotations

SETUP_HELP = "Help > Setup help"
UHID_OK, UHID_MISSING, UHID_DENIED = "ok", "missing", "denied"


def uhid_hint(access: str) -> str:
    """Long form: why the virtual mouse cannot be started, or "" when it can."""
    if access == UHID_MISSING:
        return ("/dev/uhid does not exist, so no virtual mouse can be created. Load the "
                "kernel module: sudo modprobe uhid")
    if access == UHID_DENIED:
        return ("/dev/uhid is not writable for you. Install the udev rule "
                "99-quadsens.rules, join group input and log in again (README, "
                "\"One-time setup\").")
    return ""


def uhid_short(access: str) -> str:
    """Short form of uhid_hint for the status line."""
    if access == UHID_MISSING:
        return f"/dev/uhid is missing ({SETUP_HELP})"
    if access == UHID_DENIED:
        return f"/dev/uhid is not writable ({SETUP_HELP})"
    return ""
