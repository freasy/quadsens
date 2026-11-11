"""Fetch a pinned release file and check it (no Qt).

Only what quadsens.win_tools names is fetched: over HTTPS, from GitHub's
hosts, never more bytes than the pinned size, hashed while it arrives. A file
that does not match is deleted and nothing of it is used. The same check is
made again on the stored file before it is used, because the download folder
belongs to the user and other programs of the user can write there.

The transport is injected, so all of this is tested without a network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from .win_tools import API_HOST, DOWNLOAD_HOSTS, Pin

CHUNK = 64 * 1024
TIMEOUT = 30.0               # seconds without any data, per connection
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


class Response(Protocol):
    status: int
    headers: Mapping[str, str]

    def read(self, count: int) -> bytes: ...
    def close(self) -> None: ...


Transport = Callable[[str, float], Any]
Progress = Callable[[int, int], None]


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


def cache_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Where downloads are kept: a folder of the user, made with the user's own rights."""
    environ = os.environ if environ is None else environ
    base = environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "QuadSens" / "downloads"


def file_digest(stream: BinaryIO, limit: int) -> tuple[int, str]:
    """Size and SHA-256 of what ``stream`` holds, reading at most ``limit`` + 1 bytes."""
    digest = hashlib.sha256()
    size = 0
    while size <= limit:
        block = stream.read(min(CHUNK, limit + 1 - size))
        if not block:
            break
        digest.update(block)
        size += len(block)
    return size, digest.hexdigest()


def matches(size: int, digest: str, want_size: int, want_digest: str) -> bool:
    """Size and digest are the expected ones; the digests are compared in constant time."""
    same = hmac.compare_digest(digest.lower().encode("ascii"),
                               want_digest.lower().encode("ascii"))
    return size == want_size and same


def verify_file(path: str | os.PathLike[str], size: int, sha256: str) -> bool:
    """Whether the file at ``path`` is exactly the expected one. Never raises."""
    try:
        with open(path, "rb") as stream:
            return matches(*file_digest(stream, size), size, sha256)
    except OSError:
        return False


def download(pin: Pin, folder: str | os.PathLike[str],
             transport: Transport = http_transport,
             progress: Progress | None = None,
             cancelled: Callable[[], bool] = lambda: False,
             timeout: float = TIMEOUT) -> Path:
    """Fetch the file of ``pin`` into ``folder`` and return its path once it is verified.

    A verified copy from an earlier run is used again. Raises DownloadError;
    then no file of this download is left behind.
    """
    folder = Path(folder)
    target = folder / pin.asset
    if verify_file(target, pin.size, pin.sha256):
        if progress:
            progress(pin.size, pin.size)
        return target
    partial = folder / (pin.asset + ".part")
    response = None
    try:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)          # a copy that did not verify
        except OSError as exc:
            raise DownloadError(DISK, "The download folder %s cannot be used: %s."
                                % (folder, exc.strerror or exc)) from exc
        response = _open(pin.download_url, DOWNLOAD_HOSTS, transport, timeout)
        digest = hashlib.sha256()
        size = 0
        try:
            with open(partial, "wb") as out:
                while True:
                    if cancelled():
                        raise DownloadError(CANCELLED, "The download was cancelled.")
                    try:
                        block = response.read(CHUNK)
                    except TimeoutError as exc:
                        raise DownloadError(
                            NETWORK, "The download stopped: no data for %d seconds."
                            % timeout) from exc
                    except OSError as exc:
                        raise DownloadError(NETWORK, "The download broke off: %s."
                                            % exc) from exc
                    if not block:
                        break
                    size += len(block)
                    if size > pin.size:
                        raise DownloadError(
                            SIZE, "The file is larger than the %d bytes QuadSens expects. "
                                  "It was deleted and nothing was run." % pin.size)
                    digest.update(block)
                    out.write(block)
                    if progress:
                        progress(size, pin.size)
        except OSError as exc:
            raise DownloadError(DISK, "The file could not be written: %s."
                                % (exc.strerror or exc)) from exc
        if size != pin.size:
            raise DownloadError(
                SIZE, "The download ended after %d of %d bytes. It was deleted and "
                      "nothing was run." % (size, pin.size))
        if not matches(size, digest.hexdigest(), pin.size, pin.sha256):
            raise DownloadError(
                HASH, "The downloaded file is not the one QuadSens was tested with (its "
                      "SHA-256 checksum differs). It was deleted and nothing was run.")
        try:
            os.replace(partial, target)
        except OSError as exc:
            raise DownloadError(DISK, "The file could not be stored: %s."
                                % (exc.strerror or exc)) from exc
        return target
    finally:
        if response is not None:
            response.close()
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass(frozen=True)
class Latest:
    tag: str
    url: str


def latest_release(pin: Pin, transport: Transport = http_transport,
                   timeout: float = API_TIMEOUT) -> Latest | None:
    """The newest release of the project of ``pin``, None if that cannot be learned.

    This is the one request QuadSens makes without being asked (it can be
    switched off). It sends nothing but the request itself. Any failure is
    silence: the answer is only ever a notice.
    """
    try:
        response = _open(pin.latest_api_url, (API_HOST,), transport, timeout)
        try:
            data = json.loads(response.read(512 * 1024).decode("utf-8", "replace"))
        finally:
            response.close()
    except (DownloadError, OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("prerelease") or data.get("draft"):
        return None
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag or len(tag) > 64:
        return None
    # The link shown to the user is built here, not taken from the answer.
    return Latest(tag, "https://github.com/%s/releases/tag/%s"
                  % (pin.repo, urllib.parse.quote(tag, safe=".-_")))
