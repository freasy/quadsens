"""Which platform QuadSens runs on, and where things are there (no Qt).

Linux has the whole chain: virtual mouse, result file, yeetmouse driver.
Windows applies values only: it reads or imports the four multipliers and
writes them into the settings of RawAccel.

QUADSENS_PLATFORM=windows|linux overrides the detection, so that the Windows
interface can be shown and tested on Linux.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

LINUX, WINDOWS = "linux", "windows"
ENV_PLATFORM = "QUADSENS_PLATFORM"
# Below the user's profile on Windows (Unity's "LocalLow" data folder); the
# rest follows the layout seen inside the Proton prefix on Linux.
_WINDOWS_RESULT = ("AppData", "LocalLow", "Statespace", "aimlab_tb", "Users", "*",
                   "ASFData", "*", "ASFSettings.json")


def current(environ: Mapping[str, str] | None = None, system: str | None = None) -> str:
    """"windows" or "linux"; anything that is not Windows counts as Linux."""
    environ = os.environ if environ is None else environ
    wanted = environ.get(ENV_PLATFORM, "").strip().lower()
    if wanted in (LINUX, WINDOWS):
        return wanted
    system = sys.platform if system is None else system
    return WINDOWS if system.startswith(("win32", "cygwin")) else LINUX


def windows_result_glob(environ: Mapping[str, str] | None = None) -> str:
    """Pattern of the calibration result file on Windows.

    Built with backslashes whatever the host is; the pattern is only used with
    the file system on Windows itself.
    """
    environ = os.environ if environ is None else environ
    home = environ.get("USERPROFILE") or ""
    if not home:
        drive, path = environ.get("HOMEDRIVE", ""), environ.get("HOMEPATH", "")
        home = drive + path if drive and path else ""
    if not home:
        home = os.path.expanduser("~")
    separator = "/" if "/" in home and "\\" not in home else "\\"
    return home.rstrip("\\/") + separator + separator.join(_WINDOWS_RESULT)


def default_result_glob(platform: str | None = None,
                        environ: Mapping[str, str] | None = None) -> str:
    """Where the newest calibration result is looked for on the given platform."""
    platform = platform or current(environ)
    if platform == WINDOWS:
        return windows_result_glob(environ)
    from .calibration import DEFAULT_GLOB
    return DEFAULT_GLOB
