"""Asking GitHub, for both systems (no Qt).

Requests go over HTTPS to hosts named by the caller and nowhere else;
redirects are followed by our own code, which checks where they lead. The
transport is injected, so all of this is tested without a network.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

API_HOST = "api.github.com"
API_TIMEOUT = 6.0            # the version check must not keep anything waiting
MAX_REDIRECTS = 4
USER_AGENT = "QuadSens"      # GitHub's API refuses requests without one

# Kinds of failure; the texts are for the user, the kinds for the program.
NETWORK, HOST, SIZE, HASH, CANCELLED, DISK = (
    "network", "host", "size", "hash", "cancelled", "disk")


class DownloadError(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind



Transport = Callable[[str, float], Any]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are followed by our own code, which checks where they lead."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def http_transport(url: str, timeout: float) -> Any:
    """One HTTPS request without following redirects.

    Proxy settings and certificates are those of the system, as the standard
    library finds them.
    """
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/octet-stream, application/json"})
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as response:      # 3xx and errors arrive this way
        return response


def check_url(url: str, hosts: tuple[str, ...]) -> None:
    """Refuse anything but HTTPS to one of ``hosts`` (no port, no user name)."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in hosts or parts.port not in (None, 443) \
            or parts.username is not None or parts.password is not None:
        raise DownloadError(HOST, "The download was sent to an address QuadSens does not "
                                  "accept (%s). Nothing was downloaded."
                            % (parts.scheme + "://" + host if host else "an unreadable one"))


def _open(url: str, hosts: tuple[str, ...], transport: Transport, timeout: float) -> Any:
    """Open ``url``, following redirects only within ``hosts``."""
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url, hosts)
        try:
            response = transport(url, timeout)
        except DownloadError:
            raise
        except TimeoutError as exc:
            raise DownloadError(NETWORK, "The server did not answer in time.") from exc
        except (OSError, ValueError) as exc:
            raise DownloadError(NETWORK, "The download failed: %s."
                                % (getattr(exc, "reason", None) or exc)) from exc
        status = int(getattr(response, "status", 0) or 0)
        if status in (301, 302, 303, 307, 308):
            target = response.headers.get("Location", "")
            response.close()
            url = urllib.parse.urljoin(url, target)
            continue
        if status != 200:
            response.close()
            raise DownloadError(NETWORK, "The server answered with status %d." % status)
        return response
    raise DownloadError(NETWORK, "The download was redirected too many times.")


@dataclass(frozen=True)
class Latest:
    tag: str
    url: str


def fetch_latest(repo: str, transport: Transport = http_transport,
                 timeout: float = API_TIMEOUT) -> Latest:
    """The newest release of ``repo`` (owner/name on GitHub); raises DownloadError.

    One request to GitHub's API, which sends nothing but the request itself.
    Pre-releases and drafts do not count.
    """
    url = "https://%s/repos/%s/releases/latest" % (API_HOST, repo)
    try:
        response = _open(url, (API_HOST,), transport, timeout)
        try:
            data = json.loads(response.read(512 * 1024).decode("utf-8", "replace"))
        finally:
            response.close()
    except DownloadError:
        raise
    except TimeoutError as exc:
        raise DownloadError(NETWORK, "The server did not answer in time.") from exc
    except (OSError, ValueError) as exc:
        raise DownloadError(NETWORK, "The answer could not be read: %s." % exc) from exc
    if not isinstance(data, dict) or data.get("prerelease") or data.get("draft"):
        raise DownloadError(NETWORK, "The server named no finished release.")
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag or len(tag) > 64:
        raise DownloadError(NETWORK, "The server named no usable release.")
    # The link shown to the user is built here, not taken from the answer.
    return Latest(tag, "https://github.com/%s/releases/tag/%s"
                  % (repo, urllib.parse.quote(tag, safe=".-_")))



def parse_version(text: str) -> tuple[int, ...] | None:
    """The numbers of a version or release tag ("v.0.9.8.1", "1.7.1"), None if there are none."""
    # Numbers with dots first ("usbip-win2 0.9.8.1" must not give 2); a lone
    # number only when the text holds no other.
    match = re.search(r"\d+(?:\.\d+)+", text or "") \
        or re.fullmatch(r"\D*(\d+)\D*", text or "")
    if not match:
        return None
    if match.re.groups:
        return (int(match.group(1)),)
    return tuple(int(part) for part in match.group(0).split("."))
