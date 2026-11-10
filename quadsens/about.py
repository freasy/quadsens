"""Facts shown in the About dialog (no Qt). The version has one source: the package."""

from __future__ import annotations

from . import __version__

APP_NAME = "QuadSens"
SUMMARY = "Carries a per-direction mouse sensitivity calibration into the yeetmouse driver."
LICENSE_ID = "GPL-3.0-or-later"
PROJECT_URL = "https://github.com/freasy/quadsens"
NOT_AFFILIATED = (
    "QuadSens is an independent project. It is not affiliated with or endorsed by the "
    "makers of any mouse, game or driver it works with. All trademarks belong to their "
    "owners.")


def about_lines() -> list[str]:
    """The About text as plain lines; the dialog adds the markup."""
    return [
        f"{APP_NAME} {__version__}",
        SUMMARY,
        f"License: {LICENSE_ID}",
        PROJECT_URL,
        NOT_AFFILIATED,
    ]
