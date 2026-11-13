"""Is there a newer QuadSens? (no Qt)

One request to GitHub's API for the newest release of this project. The
answer is only ever a notice with a link to the release page: nothing is
downloaded, installed or restarted here. The transport is injected, so all
of this is tested without a network.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import __version__, platforms
from .about import APP_NAME
from .github import DownloadError, Transport, fetch_latest, http_transport, parse_version

REPO = "freasy/quadsens"
NEWER, CURRENT, FAILED = "newer", "current", "failed"


@dataclass(frozen=True)
class Result:
    kind: str               # NEWER, CURRENT or FAILED
    version: str = ""       # of the newest release, as numbers ("0.5.0")
    url: str = ""           # its release page
    reason: str = ""        # why the check failed, one sentence


def check(current: str = __version__, transport: Transport = http_transport) -> Result:
    """Compare ``current`` with the newest release. Never raises."""
    try:
        latest = fetch_latest(REPO, transport)
    except DownloadError as exc:
        return Result(FAILED, reason=str(exc))
    except Exception as exc:      # noqa: BLE001 - a notice is never worth an error
        return Result(FAILED, reason="%s." % (str(exc) or type(exc).__name__).rstrip("."))
    newest, mine = parse_version(latest.tag), parse_version(current)
    if newest is None or mine is None:
        return Result(FAILED, reason="The version \"%s\" of the newest release could not "
                                     "be compared with this one." % latest.tag)
    length = max(len(newest), len(mine))
    padded_newest = newest + (0,) * (length - len(newest))
    padded_mine = mine + (0,) * (length - len(mine))
    version = ".".join(str(part) for part in newest)
    return Result(NEWER if padded_newest > padded_mine else CURRENT, version, latest.url)


def headline(result: Result, current: str = __version__) -> str:
    return "%s %s is available. You have %s." % (APP_NAME, result.version, current)


def how_to_update(system: str) -> str:
    """What the user does with a newer version, as things are today.

    There is no setup program and no distribution package yet. When there
    are, this is the text that changes (and on Windows the button).
    """
    if system == platforms.WINDOWS:
        return ("Download the new quadsens.exe from the release page, close QuadSens and "
                "put the new file in place of the old one.")
    return ("Download the new quadsens file from the release page, close QuadSens, put "
            "the new file in place of the old one and make it executable.")


def manual_answer(result: Result, current: str = __version__) -> str:
    """What a check the user asked for says when there is nothing newer."""
    if result.kind == FAILED:
        return "QuadSens could not look for a newer version. %s" % result.reason
    return "You have the newest version (%s %s)." % (APP_NAME, current)
