"""Offscreen tests of the notice about a newer QuadSens. No network."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from quadsens.ui.qt import QtCore, QtWidgets
except ImportError:  # pragma: no cover - depends on the environment
    raise unittest.SkipTest("no Qt binding (PySide6) installed") from None

import quadsens
from quadsens import platforms, update_check
from quadsens.ui.update_bar import ONLINE_KEY, SKIP_KEY, UpdateNotice

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])
URL = "https://github.com/freasy/quadsens/releases/tag/v9.0.0"
NEWER = update_check.Result(update_check.NEWER, "9.0.0", URL)
CURRENT = update_check.Result(update_check.CURRENT, quadsens.__version__)
FAILED = update_check.Result(update_check.FAILED, reason="The server did not answer in time.")


class Host(UpdateNotice, QtWidgets.QMainWindow):
    """As little of a main window as the notice needs."""

    def __init__(self, settings: QtCore.QSettings, check, environ) -> None:
        super().__init__()
        self._settings = settings
        self.opened: list[str] = []
        self.boxes: list[str] = []
        self.open_url = lambda url: self.opened.append(url.toString())
        self.init_update_notice(check, environ, platforms.LINUX)
        self.setCentralWidget(self.update_bar)

    def _open_box(self, title: str, rich_text: str) -> None:
        self.boxes.append(rich_text)


class UpdateNoticeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.answer = NEWER
        self.asked = 0
        self.environ: dict[str, str] = {}
        self.hosts: list[Host] = []

    def tearDown(self) -> None:
        for host in self.hosts:
            self.settle(host)
            host.close()
            host.deleteLater()
        _app.processEvents()

    def check(self) -> update_check.Result:
        self.asked += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def make(self, check: object = "default") -> Host:
        settings = QtCore.QSettings(str(Path(self._tmp.name) / "settings.ini"),
                                    QtCore.QSettings.Format.IniFormat)
        host = Host(settings, self.check if check == "default" else check, self.environ)
        self.hosts.append(host)
        return host

    def settle(self, host: Host) -> None:
        """Wait for the check that is running, and let the window take its answer."""
        deadline = time.monotonic() + 30
        thread = host._update_thread
        while thread is not None and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.005)
        host._take_update_results()

    def test_nothing_is_shown_or_asked_before_a_check(self) -> None:
        host = self.make()
        self.assertTrue(host.update_bar.isHidden())
        self.assertEqual(self.asked, 0)
        self.assertFalse(self.make(check=None).check_for_update(manual=True))

    def test_a_newer_version_shows_the_line_and_the_button_opens_its_page(self) -> None:
        host = self.make()
        self.assertTrue(host.check_for_update())
        self.settle(host)
        bar = host.update_bar
        self.assertFalse(bar.isHidden())
        self.assertEqual(bar.headline.text(), "QuadSens 9.0.0 is available. You have %s."
                         % quadsens.__version__)
        self.assertIn("release page", bar.detail.text())
        self.assertEqual(bar.accessibleName(), bar.headline.text())
        bar.open_button.click()
        self.assertEqual(host.opened, [URL])
        self.assertEqual(host.boxes, [])              # no dialog for an automatic check
        self.assertFalse(host._update_timer.isActive())

    def test_skip_hides_this_version_but_not_the_next(self) -> None:
        host = self.make()
        host.check_for_update()
        self.settle(host)
        host.update_bar.skip_button.click()
        self.assertTrue(host.update_bar.isHidden())
        self.assertEqual(host._settings.value(SKIP_KEY), "9.0.0")
        again = self.make()                           # the next start
        again.check_for_update()
        self.settle(again)
        self.assertTrue(again.update_bar.isHidden())
        self.answer = update_check.Result(update_check.NEWER, "9.1.0", URL)
        again.check_for_update()
        self.settle(again)
        self.assertFalse(again.update_bar.isHidden())
        self.assertIn("9.1.0", again.update_bar.headline.text())

    def test_a_manual_check_shows_a_skipped_version_again(self) -> None:
        host = self.make()
        host._settings.setValue(SKIP_KEY, "9.0.0")
        host.check_for_update(manual=True)
        self.settle(host)
        self.assertFalse(host.update_bar.isHidden())

    def test_automatic_check_is_silent_when_current_or_failing(self) -> None:
        for answer in (CURRENT, FAILED, OSError("no network")):
            self.answer = answer
            host = self.make()
            self.assertTrue(host.check_for_update())
            self.settle(host)
            self.assertTrue(host.update_bar.isHidden())
            self.assertEqual(host.boxes, [])

    def test_manual_check_always_answers(self) -> None:
        host = self.make()
        self.answer = CURRENT
        host.check_for_update(manual=True)
        self.settle(host)
        self.assertIn("newest version", host.boxes[-1])
        self.answer = FAILED
        host.check_for_update(manual=True)
        self.settle(host)
        self.assertIn("could not", host.boxes[-1])
        self.assertIn("did not answer in time", host.boxes[-1])
        self.answer = RuntimeError("<boom>")
        host.check_for_update(manual=True)
        self.settle(host)
        self.assertIn("&lt;boom&gt;", host.boxes[-1])     # text of others is escaped
        self.assertTrue(host.update_bar.isHidden())

    def test_the_switch_and_the_environment_stop_the_automatic_check(self) -> None:
        host = self.make()
        host.check_for_update()
        self.settle(host)
        host.set_online_check(False)
        self.assertTrue(host.update_bar.isHidden())
        self.assertFalse(host._settings.value(ONLINE_KEY, True, type=bool))
        self.asked = 0
        self.assertFalse(host.check_for_update())
        self.assertEqual(self.asked, 0)
        self.assertTrue(host.check_for_update(manual=True))    # asked for: still answered
        self.settle(host)
        self.assertEqual(self.asked, 1)
        host.set_online_check(True)                            # asks at once
        self.settle(host)
        self.assertEqual(self.asked, 2)
        self.environ["QUADSENS_NO_ONLINE_CHECK"] = "1"
        self.assertFalse(host.online_check_enabled())
        self.assertFalse(host.check_for_update())

    def test_only_one_check_runs_at_a_time(self) -> None:
        import threading
        gate = threading.Event()

        def slow() -> update_check.Result:
            self.asked += 1
            gate.wait(30)
            return NEWER
        host = self.make(check=slow)
        self.assertTrue(host.check_for_update())
        self.assertFalse(host.check_for_update(manual=True))
        gate.set()
        self.settle(host)
        self.assertEqual(self.asked, 1)


if __name__ == "__main__":
    unittest.main()
