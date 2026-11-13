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

from .github import (  # noqa: F401 (re-exported: the names the callers know)
    CANCELLED, DISK, HASH, HOST, MAX_REDIRECTS, NETWORK, SIZE, API_TIMEOUT, DownloadError,
    Latest,
    Transport, _open, check_url, fetch_latest, http_transport,
)
from .win_tools import DOWNLOAD_HOSTS, Pin

CHUNK = 64 * 1024
TIMEOUT = 30.0               # seconds without any data, per connection


class Response(Protocol):
    status: int
    headers: Mapping[str, str]

    def read(self, count: int) -> bytes: ...
    def close(self) -> None: ...


Progress = Callable[[int, int], None]


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


def latest_release(pin: Pin, transport: Transport = http_transport,
                   timeout: float = API_TIMEOUT) -> Latest | None:
    """The newest release of the project of ``pin``, None if that cannot be learned.

    This is a request QuadSens makes without being asked (it can be switched
    off). Any failure is silence: the answer is only ever a notice.
    """
    try:
        return fetch_latest(pin.repo, transport, timeout)
    except DownloadError:
        return None
