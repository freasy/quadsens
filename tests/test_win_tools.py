"""Tests of fetching, checking, unpacking and installing the Windows tools (no Qt).

Nothing here touches the network, Windows or a real installer: the transport,
the Windows prompt, PowerShell and the file locking are stand-ins.
Platform-independent; also run on Windows in CI.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from quadsens import win_download, win_install, win_tools
from quadsens.win_download import DownloadError
from quadsens.win_tools import PINS, RAWACCEL, USBIP

BODY = b"installer bytes " * 4096


def pin_for(body: bytes, key: str = USBIP, **changes) -> win_tools.Pin:
    return replace(PINS[key], size=len(body), sha256=hashlib.sha256(body).hexdigest(),
                   **changes)


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"", location: str = "",
                 fail_after: int | None = None, error: Exception | None = None) -> None:
        self.status = status
        self.headers = {"Location": location} if location else {}
        self.stream = io.BytesIO(body)
        self.fail_after = fail_after
        self.error = error or TimeoutError("timed out")
        self.closed = False

    def read(self, count: int) -> bytes:
        if self.fail_after is not None and self.stream.tell() >= self.fail_after:
            raise self.error
        return self.stream.read(count)

    def close(self) -> None:
        self.closed = True


class FakeNet:
    """A transport: url -> response, remembering what was asked."""

    def __init__(self, pages: dict[str, FakeResponse]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    def __call__(self, url: str, timeout: float) -> FakeResponse:
        self.asked.append(url)
        self.timeout = timeout
        if url not in self.pages:
            raise OSError("no route to host")
        return self.pages[url]


def served(pin: win_tools.Pin, body: bytes, **kwargs) -> FakeNet:
    """GitHub as it answers: a redirect to the host of the release files."""
    final = "https://release-assets.githubusercontent.com/github-production-release-asset/1/2"
    return FakeNet({pin.download_url: FakeResponse(302, location=final),
                    final: FakeResponse(200, body, **kwargs)})


class PinTest(unittest.TestCase):
    def test_pins_are_complete_and_well_formed(self) -> None:
        self.assertEqual(set(PINS), {USBIP, RAWACCEL})
        for key, pin in PINS.items():
            self.assertEqual(pin.key, key)
            self.assertRegex(pin.sha256, r"^[0-9a-f]{64}$")
            self.assertGreater(pin.size, 1000)
            self.assertRegex(pin.repo, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
            self.assertTrue(pin.download_url.startswith("https://github.com/" + pin.repo))
            self.assertTrue(pin.download_url.endswith("/" + pin.asset))
            self.assertIn(pin.tag, pin.release_url)
            self.assertEqual(pin.latest_api_url,
                             "https://api.github.com/repos/%s/releases/latest" % pin.repo)
            self.assertIsNotNone(win_tools.parse_version(pin.version))
            self.assertEqual(win_tools.compare(pin.tag, pin), win_tools.SAME)
            self.assertIn(pin.version, pin.asset)
            self.assertTrue(pin.license and pin.tested)
            win_download.check_url(pin.download_url, win_tools.DOWNLOAD_HOSTS)
        usbip, rawaccel = PINS[USBIP], PINS[RAWACCEL]
        self.assertTrue(usbip.asset.endswith("-x64.exe"))
        self.assertTrue(usbip.signer)
        self.assertEqual(usbip.files, {})
        self.assertEqual((rawaccel.version, rawaccel.asset, rawaccel.license),
                         ("1.7.1", "RawAccel_v1.7.1.zip", "MIT"))
        self.assertEqual(rawaccel.signer, "")        # its installer is not signed
        for name in ("installer.exe", "driver/rawaccel.sys", "rawaccel.exe", "writer.exe"):
            size, digest = rawaccel.files[name]
            self.assertGreater(size, 1000)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertLess(sum(size for size, _ in rawaccel.files.values()),
                        win_tools.RAWACCEL_MAX_BYTES)
        self.assertEqual(win_tools.DOWNLOAD_HOSTS,
                         ("github.com", "release-assets.githubusercontent.com"))

    def test_versions_compare_by_numbers(self) -> None:
        pin = PINS[USBIP]
        self.assertEqual(win_tools.parse_version("v.0.9.8.1"), (0, 9, 8, 1))
        self.assertEqual(win_tools.parse_version("usbip (usbip-win2) 0.9.7.7\n"), (0, 9, 7, 7))
        self.assertIsNone(win_tools.parse_version("none"))
        self.assertEqual(win_tools.compare("0.9.7.7", pin), win_tools.OLDER)
        self.assertEqual(win_tools.compare("0.9.8.1", pin), win_tools.SAME)
        self.assertEqual(win_tools.compare("0.9.8.1.0", pin), win_tools.SAME)
        self.assertEqual(win_tools.compare("0.9.10", pin), win_tools.NEWER)
        self.assertEqual(win_tools.compare("1.0", pin), win_tools.NEWER)
        self.assertEqual(win_tools.compare("", pin), win_tools.UNKNOWN)

    def test_update_notice(self) -> None:
        pin = PINS[RAWACCEL]
        self.assertEqual(win_tools.update_notice(pin, "v1.8.0"),
                         "A newer RawAccel 1.8.0 is available; QuadSens was tested with 1.7.1.")
        for tag in ("v1.7.1", "v1.6.1", "", None, "nightly"):
            self.assertEqual(win_tools.update_notice(pin, tag), "", tag)
        self.assertTrue(win_install.may_install_tested(USBIP, "0.9.7.7"))
        for version in ("0.9.8.1", "0.9.9", "", "unknown"):
            self.assertFalse(win_install.may_install_tested(USBIP, version), version)

    def test_how_to_remove_names_both_ways(self) -> None:
        text = " ".join(win_tools.HOW_TO_REMOVE)
        self.assertIn("Installed apps", text)
        self.assertIn("uninstaller.exe", text)
        self.assertIn("does not remove", text)


class DownloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name) / "QuadSens" / "downloads"
        self.pin = pin_for(BODY)

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.folder.iterdir()) if self.folder.exists() else []

    def fails(self, net: FakeNet, kind: str, pin: win_tools.Pin | None = None,
              **kwargs) -> DownloadError:
        with self.assertRaises(DownloadError) as caught:
            win_download.download(pin or self.pin, self.folder, net, **kwargs)
        self.assertEqual(caught.exception.kind, kind, str(caught.exception))
        self.assertEqual(self.leftovers(), [])          # nothing partial is left behind
        return caught.exception

    def test_success_follows_the_one_redirect_and_reports_progress(self) -> None:
        net = served(self.pin, BODY)
        steps: list[tuple[int, int]] = []
        path = win_download.download(self.pin, self.folder, net,
                                     progress=lambda done, total: steps.append((done, total)))
        self.assertEqual(path, self.folder / self.pin.asset)
        self.assertEqual(path.read_bytes(), BODY)
        self.assertEqual(self.leftovers(), [self.pin.asset])
        self.assertEqual(len(net.asked), 2)
        self.assertEqual(net.asked[0], self.pin.download_url)
        self.assertEqual(steps[-1], (len(BODY), len(BODY)))
        self.assertTrue(all(a[0] <= b[0] for a, b in zip(steps, steps[1:])))
        self.assertTrue(all(page.closed for page in net.pages.values()))
        self.assertEqual(net.timeout, win_download.TIMEOUT)
        # The verified copy is used again without asking the network.
        again = FakeNet({})
        self.assertEqual(win_download.download(self.pin, self.folder, again), path)
        self.assertEqual(again.asked, [])

    def test_wrong_hash_is_deleted_and_said(self) -> None:
        error = self.fails(served(self.pin, BODY[:-1] + b"X"), win_download.HASH)
        self.assertIn("nothing was run", str(error))
        self.assertIn("SHA-256", str(error))

    def test_too_short_and_too_long(self) -> None:
        error = self.fails(served(self.pin, BODY[:1000]), win_download.SIZE)
        self.assertIn("1000 of %d bytes" % len(BODY), str(error))
        net = served(self.pin, BODY + b"more" * 100000)
        self.fails(net, win_download.SIZE)
        # It stopped reading once the limit was passed.
        final = [page for page in net.pages.values() if page.status == 200][0]
        self.assertLess(final.stream.tell(), len(BODY) + 2 * win_download.CHUNK)

    def test_redirect_to_another_host_is_refused_before_it_is_asked(self) -> None:
        for location in ("https://example.org/USBip.exe",
                         "http://release-assets.githubusercontent.com/x",       # not HTTPS
                         "https://release-assets.githubusercontent.com.evil.example/x",
                         "https://user@github.com/x", "https://github.com:8443/x",
                         "https://objects.githubusercontent.com/x", "ftp://github.com/x",
                         "file:///C:/Windows/x"):
            net = FakeNet({self.pin.download_url: FakeResponse(302, location=location)})
            error = self.fails(net, win_download.HOST)
            self.assertEqual(net.asked, [self.pin.download_url], location)
            self.assertIn("Nothing was downloaded", str(error))
        with self.assertRaises(DownloadError):
            win_download.check_url("https://api.github.com/x", win_tools.DOWNLOAD_HOSTS)

    def test_redirect_loops_end(self) -> None:
        url = self.pin.download_url
        net = FakeNet({url: FakeResponse(302, location=url)})
        self.fails(net, win_download.NETWORK)
        self.assertEqual(len(net.asked), win_download.MAX_REDIRECTS + 1)

    def test_timeouts_and_network_errors(self) -> None:
        self.fails(served(self.pin, BODY, fail_after=win_download.CHUNK), win_download.NETWORK)
        self.fails(served(self.pin, BODY, fail_after=0,
                          error=ConnectionResetError("reset")), win_download.NETWORK)
        self.fails(FakeNet({}), win_download.NETWORK)                 # no connection at all

        def slow(url: str, timeout: float) -> FakeResponse:
            raise TimeoutError("timed out")
        error = self.fails(slow, win_download.NETWORK)                # type: ignore[arg-type]
        self.assertIn("did not answer in time", str(error))
        self.fails(FakeNet({self.pin.download_url: FakeResponse(404)}), win_download.NETWORK)

    def test_cancel_removes_the_partial_file(self) -> None:
        seen: list[int] = []

        def progress(done: int, _total: int) -> None:
            seen.append(done)
        error = self.fails(served(self.pin, BODY), win_download.CANCELLED,
                           progress=progress, cancelled=lambda: len(seen) >= 1)
        self.assertEqual(len(seen), 1)
        self.assertIn("cancelled", str(error))

    def test_a_stored_copy_that_does_not_verify_is_replaced(self) -> None:
        self.folder.mkdir(parents=True)
        (self.folder / self.pin.asset).write_bytes(b"tampered")
        path = win_download.download(self.pin, self.folder, served(self.pin, BODY))
        self.assertEqual(path.read_bytes(), BODY)
        (self.folder / self.pin.asset).write_bytes(BODY[:-1] + b"Y")
        self.fails(FakeNet({}), win_download.NETWORK)   # and never used unverified

    def test_verify_file(self) -> None:
        path = Path(self._tmp.name) / "f"
        path.write_bytes(BODY)
        self.assertTrue(win_download.verify_file(path, self.pin.size, self.pin.sha256))
        self.assertTrue(win_download.verify_file(path, self.pin.size, self.pin.sha256.upper()))
        self.assertFalse(win_download.verify_file(path, self.pin.size, "0" * 64))
        self.assertFalse(win_download.verify_file(path, self.pin.size - 1, self.pin.sha256))
        path.write_bytes(BODY + b"x")
        self.assertFalse(win_download.verify_file(path, self.pin.size, self.pin.sha256))
        self.assertFalse(win_download.verify_file(path.with_name("missing"), 1, "0" * 64))

    def test_cache_folder_is_the_users_own(self) -> None:
        folder = win_download.cache_dir({"LOCALAPPDATA": os.path.join("C:", "L")})
        self.assertEqual(folder.parts[-2:], ("QuadSens", "downloads"))
        self.assertIn("L", str(folder))

    def test_the_real_transport_does_not_follow_redirects_by_itself(self) -> None:
        import inspect
        source = inspect.getsource(win_download.http_transport)
        self.assertIn("_NoRedirect", source)
        self.assertNotIn("verify=False", inspect.getsource(win_download))
        self.assertNotIn("_create_unverified_context", inspect.getsource(win_download))


class LatestReleaseTest(unittest.TestCase):
    def ask(self, body: bytes | None, status: int = 200):
        pin = PINS[RAWACCEL]
        pages = {} if body is None else {pin.latest_api_url: FakeResponse(status, body)}
        net = FakeNet(pages)
        return win_download.latest_release(pin, net), net

    def test_newer_release_gives_tag_and_our_own_link(self) -> None:
        latest, net = self.ask(b'{"tag_name": "v1.8.0", "html_url": "https://evil.example/"}')
        assert latest is not None
        self.assertEqual(latest.tag, "v1.8.0")
        self.assertEqual(latest.url,
                         "https://github.com/RawAccelOfficial/rawaccel/releases/tag/v1.8.0")
        self.assertEqual(net.asked, ["https://api.github.com/repos/RawAccelOfficial/"
                                     "rawaccel/releases/latest"])
        self.assertEqual(net.timeout, win_download.API_TIMEOUT)
        self.assertLessEqual(win_download.API_TIMEOUT, 10)

    def test_every_failure_is_silence(self) -> None:
        for body, status in ((None, 200), (b"not json", 200), (b"[]", 200), (b"{}", 200),
                             (b'{"tag_name": 5}', 200), (b'{"tag_name": "v9"}', 403),
                             (b'{"tag_name": "v9", "prerelease": true}', 200),
                             (b'{"tag_name": "' + b"9" * 100 + b'"}', 200)):
            self.assertIsNone(self.ask(body, status)[0], body)

        def slow(url: str, timeout: float) -> FakeResponse:
            raise TimeoutError
        self.assertIsNone(win_download.latest_release(PINS[USBIP], slow))  # type: ignore

    def test_a_redirect_away_from_the_api_is_not_followed(self) -> None:
        pin = PINS[USBIP]
        net = FakeNet({pin.latest_api_url: FakeResponse(302, location="https://example.org/")})
        self.assertIsNone(win_download.latest_release(pin, net))
        self.assertEqual(len(net.asked), 1)


def make_zip(path: Path, entries: list[tuple[str, bytes, int]]) -> Path:
    """A zip of (name, data, unix mode); mode 0 leaves the attributes empty."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            archive.writestr(info, data)
    return path


GOOD = [("RawAccel/", b"", stat.S_IFDIR | 0o777),
        ("RawAccel/installer.exe", b"installer", stat.S_IFREG | 0o777),
        ("RawAccel/driver/", b"", stat.S_IFDIR | 0o777),
        ("RawAccel/driver/rawaccel.sys", b"driver", stat.S_IFREG | 0o666),
        ("RawAccel/rawaccel.exe", b"gui", stat.S_IFREG | 0o777),
        ("RawAccel/writer.exe", b"writer", stat.S_IFREG | 0o777),
        ("RawAccel/doc/Guide.md", b"guide", 0)]


def zip_pin(entries: list[tuple[str, bytes, int]], archive: Path) -> win_tools.Pin:
    files = {name.split("/", 1)[1]: (len(data), hashlib.sha256(data).hexdigest())
             for name, data, _mode in entries
             if name.endswith((".exe", ".sys")) and name.startswith("RawAccel/")}
    return pin_for(archive.read_bytes(), RAWACCEL, files=files)


class UnpackTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.archive = make_zip(self.tmp / "ra.zip", GOOD)
        self.pin = zip_pin(GOOD, self.archive)
        self.target = self.tmp / "Programs" / "RawAccel"

    def refused(self, entries: list[tuple[str, bytes, int]], expect: str) -> None:
        archive = make_zip(self.tmp / "bad.zip", entries)
        with self.assertRaises(win_install.UnpackError) as caught:
            win_install.unpack_rawaccel(archive, self.target, self.pin)
        self.assertIn(expect, str(caught.exception))
        left = [p for p in self.target.rglob("*") if p.is_file()] if self.target.exists() else []
        self.assertEqual(left, [])
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if p.name not in
                                ("ra.zip", "bad.zip", "Programs")), [])   # nothing escaped

    def test_unpacks_without_the_top_folder(self) -> None:
        written = win_install.unpack_rawaccel(self.archive, self.target, self.pin)
        self.assertEqual(sorted(str(p.relative_to(self.target)).replace(os.sep, "/")
                                for p in written),
                         ["doc/Guide.md", "driver/rawaccel.sys", "installer.exe",
                          "rawaccel.exe", "writer.exe"])
        self.assertEqual((self.target / "driver" / "rawaccel.sys").read_bytes(), b"driver")
        # Again over the same folder: files replaced, the user's settings stay.
        (self.target / "settings.json").write_text("{}")
        (self.target / "writer.exe").write_bytes(b"old")
        win_install.unpack_rawaccel(self.archive, self.target, self.pin)
        self.assertEqual((self.target / "writer.exe").read_bytes(), b"writer")
        self.assertEqual((self.target / "settings.json").read_text(), "{}")

    def test_paths_that_leave_the_folder_are_refused(self) -> None:
        ok = stat.S_IFREG | 0o666
        for name in ("RawAccel/../evil.exe", "../evil.exe", "/etc/evil", "RawAccel//../../x",
                     "C:/Windows/evil.exe", "RawAccel/C:evil.exe", "RawAccel\\..\\evil.exe",
                     "Other/evil.exe", "evil.exe", "RawAccel/./x", "RawAccel/sub/../../x",
                     "RawAccel/con. ", "\\\\server\\share\\x"):
            self.refused(GOOD + [(name, b"evil", ok)], "archive holds")

    def test_links_and_special_files_are_refused(self) -> None:
        self.refused(GOOD + [("RawAccel/link", b"/etc/passwd", stat.S_IFLNK | 0o777)],
                     "link or a special file")
        self.refused(GOOD + [("RawAccel/pipe", b"", stat.S_IFIFO | 0o666)],
                     "link or a special file")

    def test_too_many_too_large_and_duplicates(self) -> None:
        ok = stat.S_IFREG | 0o666
        many = [("RawAccel/doc/%d.txt" % n, b"x", ok)
                for n in range(win_tools.RAWACCEL_MAX_FILES)]
        self.refused(GOOD + many, "more files than expected")
        self.refused(GOOD + [("RawAccel/big.bin", b"\0" * (win_tools.RAWACCEL_MAX_BYTES + 1),
                              ok)], "more than the")
        self.refused(GOOD + [("RawAccel/Writer.EXE", b"other", ok)], "twice")

    def test_files_that_are_not_the_tested_ones(self) -> None:
        changed = [(name, b"patched" if name.endswith("installer.exe") else data, mode)
                   for name, data, mode in GOOD]
        self.refused(changed, "not the tested file")
        self.refused([entry for entry in GOOD if "rawaccel.sys" not in entry[0]], "lacks")
        (self.tmp / "bad.zip").write_bytes(b"this is not a zip")
        with self.assertRaises(win_install.UnpackError):
            win_install.unpack_rawaccel(self.tmp / "bad.zip", self.target, self.pin)

    def test_only_an_empty_or_a_rawaccel_folder_is_used(self) -> None:
        self.assertEqual(win_install.folder_usable(self.target), "")
        self.target.mkdir(parents=True)
        self.assertEqual(win_install.folder_usable(self.target), "")
        (self.target / "holiday.jpg").write_bytes(b"x")
        self.assertIn("other files", win_install.folder_usable(self.target))
        with self.assertRaises(win_install.UnpackError):
            win_install.unpack_rawaccel(self.archive, self.target, self.pin)
        (self.target / "rawaccel.exe").write_bytes(b"x")
        self.assertEqual(win_install.folder_usable(self.target), "")
        self.assertIn("full path", win_install.folder_usable("RawAccel"))
        self.assertIn("is a file", win_install.folder_usable(self.archive))

    def test_rawaccel_version_of_a_folder(self) -> None:
        win_install.unpack_rawaccel(self.archive, self.target, self.pin)
        self.assertEqual(win_install.rawaccel_version(self.target, self.pin), "1.7.1")
        (self.target / "rawaccel.exe").write_bytes(b"another build")
        self.assertEqual(win_install.rawaccel_version(self.target, self.pin), "")
        (self.target / "settings.json").write_text('{"version": "1.6.1"}')
        self.assertEqual(win_install.rawaccel_version(self.target, self.pin), "1.6.1")
        self.assertEqual(win_install.rawaccel_version(self.tmp / "none", self.pin), "")


class FakeWindows:
    """Stand-ins for the download, the Windows prompt, PowerShell and the file lock."""

    def __init__(self, tmp: Path, files: dict[str, bytes]) -> None:
        self.tmp = tmp
        self.files = files                      # asset name -> bytes the "download" gives
        self.launched: list[tuple[str, str]] = []
        self.held: list[list[str]] = []
        self.launch_result = win_install.Launch(win_install.FINISHED, 0)
        self.signature = (win_install.SIGNED, "ok")
        self.download_error: DownloadError | None = None
        self.before_launch = lambda: None
        self.said: list[str] = []
        self.holding = False
        self.launched_while_held: list[bool] = []

    def tools(self) -> win_install.Tools:
        def download(pin, folder, progress=None, cancelled=lambda: False):
            if self.download_error:
                raise self.download_error
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / pin.asset
            path.write_bytes(self.files[pin.asset])
            return path

        def launch(path: str, folder: str) -> win_install.Launch:
            self.launched.append((path, folder))
            self.launched_while_held.append(self.holding)
            return self.launch_result

        import contextlib

        @contextlib.contextmanager
        def hold(checks):
            self.before_launch()
            self.held.append([path for path, _size, _digest in checks])
            with win_install.held_verified(checks, opener=lambda p: open(p, "rb")):
                self.holding = True
                try:
                    yield
                finally:
                    self.holding = False

        return win_install.Tools(download=download, launch=launch,
                                 signature=lambda path, signer: self.signature,
                                 hold=hold, cache=lambda: self.tmp / "cache")


class FlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.archive = make_zip(self.tmp / "ra.zip", GOOD)
        self.exe = b"MZ signed installer"
        self.pins = {USBIP: pin_for(self.exe),
                     RAWACCEL: zip_pin(GOOD, self.archive)}
        original = dict(win_tools.PINS)
        win_tools.PINS.update(self.pins)
        self.addCleanup(win_tools.PINS.update, original)
        self.windows = FakeWindows(self.tmp, {
            self.pins[USBIP].asset: self.exe,
            self.pins[RAWACCEL].asset: self.archive.read_bytes()})
        self.target = str(self.tmp / "Programs" / "RawAccel")

    def usbip(self, detect: str = "0.9.8.1") -> win_install.Result:
        return win_install.install_usbip(self.windows.tools(), self.windows.said.append,
                                         detect=lambda: detect)

    def rawaccel(self, state: str = win_install.DRIVER_PENDING) -> win_install.Result:
        return win_install.install_rawaccel(self.target, self.windows.tools(),
                                            self.windows.said.append,
                                            driver_state=lambda: state)

    # ---- usbip-win2 ---------------------------------------------------------------

    def test_usbip_happy_path(self) -> None:
        result = self.usbip()
        self.assertEqual(result.kind, win_install.DONE)
        self.assertEqual(result.message, "usbip-win2 0.9.8.1 is installed.")
        installer = str(self.tmp / "cache" / self.pins[USBIP].asset)
        # Started by its path alone, the verified file, while it was held.
        self.assertEqual(self.windows.launched, [(installer, "")])
        self.assertEqual(self.windows.held, [[installer]])
        self.assertEqual(self.windows.launched_while_held, [True])
        self.assertTrue(any("administrator" in line for line in self.windows.said))

    def test_usbip_user_declines_the_prompt(self) -> None:
        self.windows.launch_result = win_install.Launch(win_install.DECLINED)
        result = self.usbip()
        self.assertEqual(result.kind, win_install.REFUSED)
        self.assertIn("Nothing was changed", result.message)

    def test_usbip_prompt_cannot_be_shown(self) -> None:
        self.windows.launch_result = win_install.Launch(win_install.NOT_STARTED,
                                                        detail="Windows error 5")
        result = self.usbip()
        self.assertEqual(result.kind, win_install.FAILED)
        self.assertIn("Windows error 5", result.message)

    def test_usbip_wrong_signer_is_not_started(self) -> None:
        self.windows.signature = (win_install.NOT_SIGNED, "status Valid, signer Someone")
        result = self.usbip()
        self.assertEqual(result.kind, win_install.FAILED)
        self.assertIn("not signed as expected", result.message)
        self.assertEqual(self.windows.launched, [])

    def test_usbip_signature_that_cannot_be_asked_is_said(self) -> None:
        self.windows.signature = (win_install.UNCHECKED, "no PowerShell")
        result = self.usbip()
        self.assertEqual(result.kind, win_install.DONE)
        self.assertIn("could not be asked", result.message)
        self.assertEqual(len(self.windows.launched), 1)

    def test_usbip_file_exchanged_after_the_download_is_not_started(self) -> None:
        installer = self.tmp / "cache" / self.pins[USBIP].asset
        self.windows.before_launch = lambda: installer.write_bytes(b"MZ something else..")
        result = self.usbip()
        self.assertEqual(result.kind, win_install.FAILED)
        self.assertIn("has changed since it was checked", result.message)
        self.assertEqual(self.windows.launched, [])

    def test_usbip_download_failures_and_cancel(self) -> None:
        self.windows.download_error = DownloadError(win_download.HASH, "checksum differs")
        result = self.usbip()
        self.assertEqual((result.kind, result.message), (win_install.FAILED, "checksum differs"))
        self.windows.download_error = DownloadError(win_download.CANCELLED, "cancelled")
        self.assertEqual(self.usbip().kind, win_install.CANCELLED)
        self.assertEqual(self.windows.launched, [])

    def test_usbip_installer_ended_without_installing(self) -> None:
        result = self.usbip(detect="")
        self.assertEqual(result.kind, win_install.DONE)
        self.assertIn("not found yet", result.message)

    # ---- RawAccel -----------------------------------------------------------------

    def test_rawaccel_happy_path_ends_with_restart_pending(self) -> None:
        result = self.rawaccel()
        self.assertEqual(result.kind, win_install.DONE)
        self.assertEqual(result.folder, self.target)
        self.assertIn("Restart the computer", result.message)
        self.assertIn("rawaccel.exe once", result.message)
        installer = os.path.join(self.target, "installer.exe")
        driver = os.path.join(self.target, "driver", "rawaccel.sys")
        # installer.exe finds the driver file relative to its working directory.
        self.assertEqual(self.windows.launched, [(installer, self.target)])
        self.assertEqual(self.windows.held[-1], [installer, driver])
        self.assertEqual(self.windows.launched_while_held, [True])
        self.assertTrue(os.path.isfile(driver))

    def test_rawaccel_user_declines_the_prompt(self) -> None:
        self.windows.launch_result = win_install.Launch(win_install.DECLINED)
        result = self.rawaccel()
        self.assertEqual(result.kind, win_install.REFUSED)
        self.assertIn("driver is not installed", result.message)
        self.assertEqual(result.folder, self.target)          # it is unpacked all the same

    def test_rawaccel_installer_reports_nothing_so_the_driver_file_decides(self) -> None:
        result = self.rawaccel(win_install.DRIVER_MISSING)
        self.assertEqual(result.kind, win_install.FAILED)
        self.assertIn("driver is not in the Windows driver folder", result.message)

    def test_rawaccel_installer_exchanged_after_unpacking_is_not_started(self) -> None:
        calls = []

        def swap() -> None:
            calls.append(1)
            if len(calls) == 2:                 # the second hold is the one before the start
                Path(self.target, "installer.exe").write_bytes(b"installe!")
        self.windows.before_launch = swap
        result = self.rawaccel()
        self.assertEqual(result.kind, win_install.FAILED)
        self.assertIn("installer.exe has changed", result.message)
        self.assertEqual(self.windows.launched, [])

    def test_rawaccel_bad_folder_and_bad_archive(self) -> None:
        os.makedirs(self.target)
        Path(self.target, "notes.txt").write_text("mine")
        result = self.rawaccel()
        self.assertEqual(result.kind, win_install.FAILED)
        self.assertIn("other files", result.message)
        os.remove(os.path.join(self.target, "notes.txt"))
        bad = make_zip(self.tmp / "bad.zip", GOOD + [("RawAccel/../x.exe", b"x", 0)])
        self.pins[RAWACCEL] = replace(pin_for(bad.read_bytes(), RAWACCEL),
                                      files=self.pins[RAWACCEL].files)
        win_tools.PINS[RAWACCEL] = self.pins[RAWACCEL]
        self.windows.files[self.pins[RAWACCEL].asset] = bad.read_bytes()
        result = self.rawaccel()
        self.assertEqual(result.kind, win_install.FAILED)
        self.assertIn("not allowed", result.message)
        self.assertEqual(self.windows.launched, [])

    def test_confirmation_texts_say_what_will_happen(self) -> None:
        usbip = " ".join(win_install.confirm_text(USBIP))
        for words in ("administrator rights", "USB devices can disconnect", "checksum",
                      "signature", "restart the computer"):
            self.assertIn(words, usbip)
        rawaccel = " ".join(win_install.confirm_text(RAWACCEL))
        for words in ("administrator rights", "not signed", "after the computer was restarted",
                      "does not restart", "checksum", ".NET Framework"):
            self.assertIn(words, rawaccel)
        self.assertEqual(win_install.default_rawaccel_folder({"LOCALAPPDATA": "C:\\U\\L"}),
                         "C:\\U\\L\\Programs\\RawAccel")


class WindowsFactsTest(unittest.TestCase):
    ENV = {"SystemRoot": "C:\\Windows"}

    def test_driver_state(self) -> None:
        seen: list[str] = []

        def created(when):
            def probe(path: str):
                seen.append(path)
                return when
            return probe
        state = win_install.rawaccel_driver_state
        self.assertEqual(state(self.ENV, 1000.0, None, created(None)), win_install.DRIVER_MISSING)
        self.assertEqual(seen[-1], "C:\\Windows\\System32\\drivers\\rawaccel.sys")
        self.assertEqual(state(self.ENV, 1000.0, None, created(500.0)), win_install.DRIVER_READY)
        self.assertEqual(state(self.ENV, 1000.0, None, created(1500.0)),
                         win_install.DRIVER_PENDING)
        # Installed through QuadSens after Windows started, whatever the file says.
        self.assertEqual(state(self.ENV, 1000.0, 1200.0, created(500.0)),
                         win_install.DRIVER_PENDING)
        self.assertEqual(state(self.ENV, 1000.0, 900.0, created(500.0)),
                         win_install.DRIVER_READY)
        # Without a start time of Windows the restart cannot be told.
        self.assertEqual(state(self.ENV, None, None, created(500.0)),
                         win_install.DRIVER_UNKNOWN)
        self.assertEqual(state({}, 1000.0, None, created(500.0)), win_install.DRIVER_MISSING)

    def test_signature_is_asked_with_a_fixed_command(self) -> None:
        calls: list[tuple[list[str], dict]] = []

        def runner(reply: tuple[int, str]):
            def run(argv, timeout, env):
                calls.append((argv, dict(env)))
                return reply
            return run
        signer = PINS[USBIP].signer
        subject = 'CN="%s", O="%s", L=Uppsala, C=SE' % (signer, signer)
        path = "C:\\Users\\x\\a file; rm -rf $(evil) `x`.exe"
        state = win_install.signature_state(
            path, signer, self.ENV, runner((0, "status=Valid\nsubject=%s\n" % subject)))
        self.assertEqual(state, (win_install.SIGNED, signer))
        argv, env = calls[-1]
        self.assertEqual(argv[0], "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe")
        self.assertEqual(argv[1:4], ["-NoProfile", "-NonInteractive", "-Command"])
        self.assertEqual(argv[4], win_install.SIGNATURE_SCRIPT)
        self.assertNotIn(path, " ".join(argv))            # the name is not in the command
        self.assertEqual(env, {"QUADSENS_CHECK_FILE": path})
        for reply, expected in (
                ((0, "status=Valid\nsubject=CN=Someone Else, C=SE\n"), win_install.NOT_SIGNED),
                ((0, "status=NotSigned\nsubject=\n"), win_install.NOT_SIGNED),
                ((0, "status=HashMismatch\nsubject=%s\n" % subject), win_install.NOT_SIGNED),
                ((0, "status=Valid\nsubject=O=%s\n" % signer), win_install.NOT_SIGNED),
                ((1, "error"), win_install.UNCHECKED), ((124, ""), win_install.UNCHECKED),
                ((127, "not found"), win_install.UNCHECKED), ((0, ""), win_install.UNCHECKED)):
            self.assertEqual(win_install.signature_state(path, signer, self.ENV,
                                                         runner(reply))[0], expected, reply)
        self.assertEqual(win_install.signature_state(path, signer, {}, runner((0, "")))[0],
                         win_install.UNCHECKED)

    def test_elevation_takes_a_path_and_nothing_else(self) -> None:
        import inspect
        source = inspect.getsource(win_install.launch_elevated)
        self.assertIn('"runas"', source)
        self.assertIn("info.lpParameters = None", source)
        self.assertNotIn("cmd", source.lower().replace("cmdline", ""))
        self.assertNotIn("shell=True", inspect.getsource(win_install))
        if os.name != "nt":
            self.assertEqual(win_install.launch_elevated("x").kind, win_install.NOT_STARTED)
            self.assertIsNone(win_install.boot_time())

    def test_held_files_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.exe")
            Path(path).write_bytes(BODY)
            good = (path, len(BODY), hashlib.sha256(BODY).hexdigest())
            with win_install.held_verified([good]):
                pass
            for bad in ((path, len(BODY), "0" * 64), (path, len(BODY) - 1, good[2]),
                        (os.path.join(tmp, "missing.exe"), 1, "0" * 64)):
                with self.assertRaises(DownloadError) as caught:
                    with win_install.held_verified([good, bad]):
                        self.fail("the block must not run")
                self.assertEqual(caught.exception.kind, win_download.HASH)


if __name__ == "__main__":
    unittest.main()
