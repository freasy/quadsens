"""The Windows composition of the application, shown on Linux through the override.

The RawAccel folder is a temp directory with a fixture settings.json and a
script that stands in for writer.exe. usbip-win2 is a fake command runner
that really connects the in-process USB/IP client to the server, so the
virtual mouse goes through the same code as with the real driver, minus
Windows itself.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from quadsens.ui.qt import QtCore, QtWidgets
except ImportError:  # pragma: no cover - depends on the environment
    raise unittest.SkipTest("no Qt binding (PySide6) installed") from None

from quadsens import (
    accel_edit,
    emulator,
    rawaccel,
    rawaccel_live,
    win_autostart,
    usbip_mouse,
    usbip_server,
    values_file,
    win_install,
)
from quadsens.model import Scalars
from quadsens.rawaccel import Four
from quadsens import win_download, win_tools
from quadsens.ui import tools_strip, windows_window
from quadsens.ui.windows_window import WindowsMainWindow
from quadsens.win_tools import RAWACCEL, USBIP
from quadsens.usbip_client import UsbipClient
from quadsens.usbip_server import VirtualMouseServer
from tests import widths
from tests.test_usbip_windows import TOOL, FakeDriver, occupy_port
from tests.test_win_tools import GOOD, FakeWindows, make_zip, pin_for, zip_pin

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])
widths.apply_test_font(_app)
VALUES = Scalars(1.011, 0.991, 1.006, 0.996)
# The stand-in for writer.exe is a shell script, which only POSIX can start.
POSIX = os.name == "posix"
needs_fake_writer = unittest.skipUnless(POSIX, "the stand-in for writer.exe is a shell script")


class FakeRegistry:
    """The Run key of the user, in memory."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.fail = False

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        if self.fail:
            raise PermissionError(13, "Access is denied")
        self.values[name] = value

    def delete(self, name: str) -> None:
        if self.fail:
            raise PermissionError(13, "Access is denied")
        self.values.pop(name, None)


class WindowCase(unittest.TestCase):
    """A RawAccel folder, a fake usbip-win2 and helpers; the tests are below."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.folder = self.tmp / "RawAccel"
        self.folder.mkdir()
        self.settings_file = self.folder / "settings.json"
        shutil.copy(FIXTURES / "rawaccel" / "settings-1.7.1-default.json", self.settings_file)
        self.writer(0)
        self.values_path = self.tmp / "mine.quadsens.json"
        self.values_path.write_text(values_file.dump(VALUES, None, "0.4.0"))
        self.pattern = str(self.tmp / "profile" / "Users" / "*" / "ASFData" / "*"
                           / "ASFSettings.json")
        self.windows: list[WindowsMainWindow] = []
        # The tools: nothing is installed, nothing is asked online, unless a test says so.
        self.install_tools: win_install.Tools | None = None
        self.driver_state = win_install.DRIVER_READY
        self.latest_release = None
        # RawAccel's driver and the startup entries of the user.
        self.live = rawaccel_live.Live(rawaccel_live.UNAVAILABLE)
        self.registry = FakeRegistry()
        self.windows_started: float | None = None

    def tearDown(self) -> None:
        for window in self.windows:
            self.wait(window)
            window.close()
            window.deleteLater()
        _app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete.value)
        _app.processEvents()

    def writer(self, status: int, text: str = "") -> None:
        """A stand-in for writer.exe that records its arguments."""
        script = self.folder / "writer.exe"
        script.write_text(f'#!/bin/sh\necho "$@" >> "{self.folder}/writer.log"\n'
                          f'echo "{text}"\nexit {status}\n')
        if POSIX:
            script.chmod(0o755)

    def make(self, choose: bool = True, tool: str | None = None,
             port: int = 0) -> WindowsMainWindow:
        """A window; ``tool`` is where usbip.exe "is", None for not installed."""
        settings = QtCore.QSettings(str(self.tmp / "settings.ini"),
                                    QtCore.QSettings.Format.IniFormat)
        self.tool = tool
        self.driver = FakeDriver()
        self.mice: list[usbip_mouse.VirtualMouse] = []

        def find_client() -> UsbipClient:
            return UsbipClient(self.tool, self.driver)

        def mouse_factory(product_id: int) -> usbip_mouse.VirtualMouse:
            mouse = usbip_mouse.VirtualMouse(
                find_client(), VirtualMouseServer(product_id, port=port))
            self.mice.append(mouse)
            return mouse

        self.environ = {"USERPROFILE": str(self.tmp / "profile"),
                        "SystemDrive": str(self.tmp / "nodrive"),
                        "LOCALAPPDATA": str(self.tmp / "local")}
        window = WindowsMainWindow(settings, self.pattern, self.environ,
                                   find_client=find_client, mouse_factory=mouse_factory,
                                   install_tools=self.install_tools,
                                   driver_state=lambda installed_at: self.driver_state,
                                   latest_release=self.latest_release,
                                   read_live=lambda: self.live,
                                   registry=self.registry,
                                   started=lambda: self.windows_started)
        window.opened: list[str] = []
        window.open_url = lambda url: window.opened.append(url.toString())
        self.windows.append(window)
        if choose and window.rawaccel_panel.folder is None:
            window.rawaccel_panel.choose_folder(str(self.folder))
        return window

    def wait(self, window: WindowsMainWindow) -> None:
        deadline = time.monotonic() + 30
        while window.rawaccel_panel.is_busy() and time.monotonic() < deadline:
            _app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)

    def until(self, window: WindowsMainWindow, condition, seconds: float = 30.0) -> bool:
        """Let the window poll the virtual mouse until ``condition`` holds."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            window.mouse_panel.poll()
            _app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 10)
            if condition():
                return True
            time.sleep(0.005)
        return bool(condition())

    def started(self, window: WindowsMainWindow) -> usbip_mouse.VirtualMouse:
        """Start the virtual mouse and wait until the attach has been tried."""
        window.mouse_panel.toggle_button.click()
        mouse = window.mouse_panel.mouse()
        assert mouse is not None
        # usbip.exe is through, and the server has seen what it did.
        self.assertTrue(self.until(
            window, lambda: not mouse.busy
            and mouse.server.attached == (self.driver.client is not None)))
        window.mouse_panel.poll()
        return mouse

    def write(self, prop: int, payload: bytes) -> None:
        """What the game's software sends: a SET of one property."""
        assert self.driver.client is not None
        request = bytes([0, 1, 0, emulator.CMD_SET]) + struct.pack("<H", prop) + payload
        self.driver.client.submit(usbip_server.DIR_OUT, 1, 64, data=request.ljust(64, b"\0"))
        self.driver.client.reply()

    def calibrate(self, values: Scalars) -> None:
        for prop, value in zip((emulator.P_DIR_SENS_X_POS, emulator.P_DIR_SENS_X_NEG,
                                emulator.P_DIR_SENS_Y_POS, emulator.P_DIR_SENS_Y_NEG),
                               (values.x_pos, values.x_neg, values.y_pos, values.y_neg)):
            self.write(prop, struct.pack("<f", value))
        self.write(emulator.P_DIR_SENS_ENABLED, struct.pack("<I", 1))

    def statuses(self, window: WindowsMainWindow) -> str:
        return "".join(chip.text()[0] for chip in window.steps_bar.chips)

    def file_values(self, index: int = 0) -> Four:
        return rawaccel.read(self.settings_file).values(index)


class WindowsWindowTest(WindowCase):
    """Values in, RawAccel out; usbip-win2 is not installed in these."""

    def test_composition_has_no_linux_parts(self) -> None:
        window = self.make(choose=False)
        self.assertEqual(window.windowTitle(), "QuadSens")
        for name in ("driver_panel", "driver_strip", "setup_dialog"):
            self.assertFalse(hasattr(window, name), name)
        self.assertEqual([a.text() for a in window.menuBar().actions()],
                         ["&File", "View", "&Help"])
        self.assertEqual([chip.text() for chip in window.steps_bar.chips], [
            "○ 1 Start mouse", "○ 2 Neutral", "○ 3 Calibrate", "▶ 4 Use result",
            "○ 5 Apply"])
        self.assertEqual(window.mouse_panel.accessibleName(), "Virtual mouse")
        self.assertEqual(window.mouse_panel.status_label.text(), "Stopped")
        self.assertEqual(window.mouse_panel.toggle_button.text(), "&Start virtual mouse")
        self.assertTrue(window.mouse_panel.log_box.isHidden())       # collapsed
        self.assertIn("import a values file", window.calibration_panel.message.text())
        self.assertIn("Values are lost when Windows restarts",
                      window.rawaccel_panel.boot_label.text())
        self.assertFalse(window.rawaccel_panel.apply_button.isEnabled())
        self.assertIn("Choose the RawAccel folder", window.rawaccel_panel.state_label.text())
        # Small windows scroll, so the minimum stays small whatever the cards need.
        self.assertEqual((window.minimumWidth(), window.minimumHeight()), (760, 520))
        self.assertIs(window.centralWidget(), window.scroll)
        self.assertTrue(window.scroll.widgetResizable())
        # No sideways scrolling at the minimum size, and the default size shows
        # everything. The limits are for the font they were set with; a wider
        # font moves them. On failure the message names the widest widgets.
        content = window.scroll.widget()
        widths.assert_fits(self, content.minimumSizeHint(),
                           (widths.WINDOWS_CONTENT_WIDTH, windows_window.DEFAULT_SIZE[1] - 30),
                           content)
        self.assertLessEqual(windows_window.DEFAULT_SIZE[1], 800)

    @needs_fake_writer
    def test_walk_through_import_folder_preview_apply(self) -> None:
        window = self.make(choose=False)
        panel = window.rawaccel_panel
        # usbip-win2 is not installed: the steps start at the values, not at the mouse.
        self.assertEqual(self.statuses(window), "○○○▶○")
        self.assertEqual(window.steps_bar.next_button.text(), "Import values…")
        self.assertTrue(window.steps_bar.next_button.isEnabled())
        self.assertIn("Import a values file", window.steps_bar.next_label.text())
        self.assertTrue(window.import_values(str(self.values_path)))
        self.assertEqual(panel.values(), VALUES)                      # filled at once
        self.assertEqual(window.calibration_panel.diagram.scalars(), VALUES)
        self.assertEqual(self.statuses(window), "✓✓✓✓▶")
        self.assertEqual(window.steps_bar.next_button.text(), "Choose folder…")
        self.assertTrue(panel.choose_button.property("primary"))

        panel.choose_folder(str(self.folder))
        self.assertEqual(self.statuses(window), "✓✓✓✓▶")
        self.assertFalse(panel.choose_button.property("primary"))
        self.assertTrue(panel.apply_button.property("primary"))
        self.assertEqual(panel.version_label.text(), "settings of RawAccel 1.7.1 (1.7 names)")
        self.assertEqual([label.text() for label in panel.setting_labels],
                         ["Output DPI", "Y/X ratio", "L/R ratio", "U/D ratio"])
        self.assertEqual([label.text() for label in panel.current_labels],
                         ["1000.0", "1.0", "1.0", "1.0"])
        self.assertEqual([label.text() for label in panel.new_labels],
                         ["1011.0  ≠", "0.995054  ≠", "0.980218  ≠", "0.99006  ≠"])
        self.assertIn("Not applied yet", panel.state_label.text())
        self.assertIn("exactly", panel.note_label.text())
        self.assertEqual(window.steps_bar.next_button.text(), "Apply")

        window.steps_bar.next_button.click()
        self.wait(window)
        self.assertEqual(self.statuses(window), "✓✓✓✓✓")
        self.assertIn("All steps are done", window.steps_bar.next_label.text())
        self.assertEqual(self.driver.calls, [])                       # usbip.exe never ran
        self.assertTrue(self.file_values().is_close(Four(1011.0, 0.995054, 0.980218, 0.99006)))
        self.assertTrue(panel.message.text().startswith("✓ The new values are in settings.json"))
        self.assertIn("RawAccel has loaded the new settings", panel.message.text())
        self.assertIn("settings.quadsens-backup-", panel.message.text())
        self.assertEqual((self.folder / "writer.log").read_text().strip(),
                         str(self.settings_file))
        self.assertIn("✓ settings.json holds these values", panel.state_label.text())
        self.assertFalse(panel.apply_button.isEnabled())
        backups = sorted(self.folder.glob("settings.quadsens-backup-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(rawaccel.read(backups[0]).values(0), Four(1000.0, 1.0, 1.0, 1.0))

    @needs_fake_writer
    def test_applying_again_does_not_compound_and_restore_goes_back(self) -> None:
        window = self.make()
        panel = window.rawaccel_panel
        panel.set_values(VALUES)
        panel.apply_button.click()
        self.wait(window)
        first = self.file_values()
        window.close()

        again = self.make()                       # a new start: the baseline is remembered
        panel = again.rawaccel_panel
        self.assertEqual(str(panel.folder), str(self.folder))   # so is the folder
        panel.set_values(VALUES)
        self.assertTrue(panel.is_applied())       # same values: nothing to do
        panel.set_values(Scalars(1.02, 1.0, 1.0, 1.0))
        self.assertEqual(panel.new_labels[0].text(), "1020.0  ≠")   # not 1011 * 1.02
        panel.apply_button.click()
        self.wait(again)
        self.assertAlmostEqual(self.file_values().sens, 1020.0)
        self.assertNotEqual(self.file_values(), first)
        self.assertEqual(len(list(self.folder.glob("settings.quadsens-backup-*"))), 1)

        self.assertTrue(panel.restore_button.isEnabled())
        panel.restore_button.click()
        self.wait(again)
        self.assertEqual(self.file_values(), Four(1000.0, 1.0, 1.0, 1.0))
        self.assertIn("original values are back", panel.message.text())
        self.assertFalse(panel.restore_button.isEnabled())
        # Byte for byte the file RawAccel wrote.
        self.assertEqual(self.settings_file.read_bytes(),
                         (FIXTURES / "rawaccel" / "settings-1.7.1-default.json").read_bytes())

    @needs_fake_writer
    def test_values_changed_in_rawaccel_become_the_new_baseline(self) -> None:
        window = self.make()
        panel = window.rawaccel_panel
        panel.set_values(VALUES)
        panel.apply_button.click()
        self.wait(window)
        data = json.loads(self.settings_file.read_text())
        data["profiles"][0]["Output DPI"] = 1200.0        # the user's own change, later
        self.settings_file.write_text(json.dumps(data, indent=2))
        panel.reload()
        self.assertIn("changed outside QuadSens", panel.note_label.text())
        self.assertEqual(panel.new_labels[0].text(), "1213.2  ≠")
        panel.apply_button.click()
        self.wait(window)
        self.assertEqual(len(list(self.folder.glob("settings.quadsens-backup-*"))) >= 1, True)
        panel.restore_button.click()
        self.wait(window)
        self.assertAlmostEqual(self.file_values().sens, 1200.0)

    @needs_fake_writer
    def test_profiles_acceleration_note_and_flip(self) -> None:
        shutil.copy(FIXTURES / "rawaccel" / "settings-1.7.1-profiles.json", self.settings_file)
        window = self.make()
        panel = window.rawaccel_panel
        self.assertEqual([panel.profile_combo.itemText(i) for i in range(3)],
                         ["default", "flick", "tracking"])
        self.assertEqual(panel.profile_combo.currentIndex(), 0)
        self.assertIn("exactly", panel.note_label.text())
        panel.profile_combo.setCurrentIndex(1)
        self.assertEqual([label.text() for label in panel.current_labels],
                         ["800.0", "1.1", "0.98", "1.02"])
        self.assertIn("approximation", panel.note_label.text())
        panel.set_values(VALUES)
        normal = [label.text() for label in panel.new_labels]
        self.assertTrue(panel.advanced.isHidden())
        self.assertIn("verified on Linux only", panel.flip_hint.text())
        panel.flip_check.setChecked(True)
        flipped = [label.text() for label in panel.new_labels]
        self.assertEqual(normal[0], flipped[0])
        self.assertNotEqual(normal[1], flipped[1])
        self.assertEqual(window.calibration_panel._flip_y, True)
        panel.apply_button.click()
        self.wait(window)
        self.assertEqual(self.file_values(0), Four(1000.0, 1.0, 1.0, 1.0))   # untouched
        self.assertEqual(self.file_values(2), Four(1000.0, 1.0, 1.0, 1.0))
        self.assertAlmostEqual(self.file_values(1).sens, 808.8)

    def test_old_schema_unknown_schema_and_missing_file(self) -> None:
        shutil.copy(FIXTURES / "rawaccel" / "settings-1.6.1-default.json", self.settings_file)
        window = self.make()
        panel = window.rawaccel_panel
        self.assertEqual(panel.setting_labels[0].text(), "Sensitivity")
        self.assertIn("1.6 names", panel.version_label.text())
        panel.set_values(VALUES)
        self.assertEqual(panel.new_labels[0].text(), "1.011  ≠")

        self.settings_file.write_text('{"version": "9.9", "profiles": [{"name": "x"}]}')
        panel.reload()
        self.assertTrue(panel.message.is_error)
        self.assertIn("does not guess", panel.message.text())
        self.assertFalse(panel.apply_button.isEnabled())
        self.assertEqual(self.statuses(window), "✓✓✓✓▶")
        self.assertIn("does not guess", window.steps_bar.next_label.text())
        self.assertEqual(window.steps_bar.next_button.text(), "Choose folder…")

        empty = self.tmp / "Empty"
        empty.mkdir()
        panel.choose_folder(str(empty))
        self.assertIn("does not exist", panel.message.text())
        self.assertIn("Start RawAccel once", panel.message.text())

    @needs_fake_writer
    def test_writer_failure_and_missing_writer(self) -> None:
        self.writer(1, "Bad settings")
        window = self.make()
        panel = window.rawaccel_panel
        panel.set_values(VALUES)
        panel.apply_button.click()
        self.wait(window)
        self.assertTrue(panel.message.is_error)
        self.assertIn("did not accept", panel.message.text())
        self.assertIn("Bad settings", panel.message.text())
        self.assertAlmostEqual(self.file_values().sens, 1011.0)   # the file was written

        (self.folder / "writer.exe").unlink()
        panel.set_values(Scalars(1.02, 1.0, 1.0, 1.0))
        panel.apply_button.click()
        self.assertFalse(panel.is_busy())
        self.assertIn("writer.exe is not in that folder", panel.message.text())
        self.assertFalse(panel.message.is_error)
        self.assertEqual(panel.message.kind, "warning")
        self.assertAlmostEqual(self.file_values().sens, 1020.0)

    @unittest.skipUnless(POSIX and os.geteuid() != 0, "needs POSIX permissions, not root")
    def test_write_protected_folder(self) -> None:
        window = self.make()
        panel = window.rawaccel_panel
        panel.set_values(VALUES)
        self.folder.chmod(0o555)
        self.addCleanup(self.folder.chmod, 0o755)
        panel.apply_button.click()
        self.assertTrue(panel.message.is_error)
        self.assertIn("Nothing was changed", panel.message.text())
        self.assertEqual(self.file_values(), Four(1000.0, 1.0, 1.0, 1.0))

    def test_folder_is_guessed_from_common_places(self) -> None:
        guess = self.tmp / "profile" / "Downloads" / "RawAccel"
        guess.parent.mkdir(parents=True)
        shutil.copytree(self.folder, guess)
        window = self.make(choose=False)
        self.assertEqual(window.rawaccel_panel.folder, guess)
        self.assertIn("Found RawAccel in", window.rawaccel_panel.message.text())

    def test_result_file_on_windows_layout_and_export(self) -> None:
        result = self.tmp / "profile" / "Users" / "u1" / "ASFData" / "mouse"
        result.mkdir(parents=True)
        shutil.copy(FIXTURES / "ASFSettings.json", result)
        window = self.make()
        self.assertIsNotNone(window.calibration_panel.calibration)
        self.assertEqual(window.steps_bar.next_button.text(), "Use result")
        window.steps_bar.next_button.click()
        self.assertEqual(window.rawaccel_panel.values(), Scalars(1.0375, 0.9625, 1.125, 0.875))
        target = self.tmp / "out.quadsens.json"
        self.assertTrue(window.export_values(str(target)))
        exported = values_file.load(target.read_text())
        self.assertEqual(exported.scalars, Scalars(1.0375, 0.9625, 1.125, 0.875))
        self.assertEqual(len(exported.measured or ()), 8)
        self.assertNotIn(str(self.tmp), target.read_text())
        self.assertIn("Exported the four multipliers", window.calibration_panel.message.text())

    def test_import_rejections_are_shown(self) -> None:
        window = self.make()
        bad = self.tmp / "bad.json"
        bad.write_text('{"format": "quadsens-values", "version": 7}')
        self.assertFalse(window.import_values(str(bad)))
        self.assertTrue(window.calibration_panel.message.is_error)
        self.assertIn("Update QuadSens", window.calibration_panel.message.text())
        self.assertFalse(window.import_values(str(self.tmp / "missing.json")))
        self.assertIn("cannot be read", window.calibration_panel.message.text())
        self.assertFalse(window.export_values(str(self.tmp / "x.json")))   # nothing to export
        self.assertIn("no result to export", window.calibration_panel.message.text())

    def test_names_mnemonics_and_view_menu(self) -> None:
        window = self.make()
        seen: dict[str, str] = {}
        texts = [action.text() for action in window.menuBar().actions()]
        texts += [w.text() for w in window.findChildren(QtWidgets.QAbstractButton)]
        texts += [w.text() for w in window.findChildren(QtWidgets.QLabel)
                  if w.buddy() is not None]
        for text in texts:
            cleaned = text.replace("&&", "")
            if "&" in cleaned:
                key = cleaned[cleaned.index("&") + 1].lower()
                self.assertNotIn(key, seen, f"{text!r} and {seen.get(key)!r} share Alt+{key}")
                seen[key] = text
        interactive = (QtWidgets.QAbstractButton, QtWidgets.QAbstractSpinBox,
                       QtWidgets.QComboBox, QtWidgets.QLineEdit)
        for widget in window.centralWidget().findChildren(QtWidgets.QWidget):
            if isinstance(widget, interactive) and not widget.objectName().startswith("qt_"):
                self.assertTrue(widget.accessibleName() or getattr(widget, "text", lambda: "")(),
                                repr(widget))
                self.assertTrue(widget.accessibleDescription(), repr(widget))
        self.assertEqual(list(window.theme_actions), ["quadsens", "system", "native"])
        window.zoom_actions[0.9].trigger()
        self.assertEqual(window.calibration_panel.diagram.zoom(), 0.9)
        window.about_action.trigger()
        assert window.info_box is not None
        window.info_box.close()
        window.steps_action.trigger()
        assert window.info_box is not None
        self.assertIn("Start the virtual mouse", window.info_box.text())
        self.assertIn("Without usbip-win2", window.info_box.text())
        window.info_box.close()


class VirtualMouseWindowTest(WindowCase):
    """The virtual mouse in the window: usbip-win2 is the fake driver."""

    def setUp(self) -> None:
        super().setUp()
        # Without writer.exe the settings are written at once, also on Windows.
        (self.folder / "writer.exe").unlink()

    def commands(self) -> list[str]:
        return self.driver.commands()

    def test_strip_says_what_is_missing_and_what_was_found(self) -> None:
        window = self.make()
        strip = window.tools_strip
        self.assertFalse(strip.found)
        self.assertEqual((strip.mark.text(), strip.headline.text()),
                         ("✕", "usbip-win2 is not installed"))
        self.assertIn("administrator rights", strip.detail.text())
        self.assertIn("restarts the USB hubs", strip.detail.text())
        self.assertIn("still import or type", strip.detail.text())
        self.assertIn("needs attention", strip.accessibleName())
        self.assertFalse(strip.get_button.isHidden())
        self.assertEqual(strip.get_button.text(), "Install USB/IP driver…")
        self.assertEqual(self.commands(), [])            # nothing was run, nothing fetched
        self.assertEqual(self.statuses(window), "○✓○▶○")  # the mouse steps are set aside

        self.tool = TOOL                                  # the user installed it
        self.driver.version = "0.9.8.1"
        strip.recheck_button.click()
        self.assertTrue(strip.found)
        self.assertEqual((strip.mark.text(), strip.headline.text()),
                         ("✓", "USB/IP driver found (usbip-win2 0.9.8.1)"))
        self.assertTrue(strip.get_button.isHidden())
        self.assertTrue(strip.detail.isHidden())
        self.assertIn("passed", strip.accessibleName())
        self.assertEqual(self.statuses(window), "▶✓○○○")
        self.assertEqual(window.steps_bar.next_button.text(), "Start virtual mouse")
        self.assertTrue(window.mouse_panel.toggle_button.property("primary"))

    def test_start_attach_calibrate_use_apply_stop(self) -> None:
        window = self.make(tool=TOOL)
        panel, mouse_panel = window.rawaccel_panel, window.mouse_panel
        window.steps_bar.next_button.click()                       # step 1
        mouse = mouse_panel.mouse()
        assert mouse is not None
        self.assertTrue(self.until(window, lambda: mouse.state == usbip_mouse.ATTACHED
                                   and not mouse.busy))
        mouse_panel.poll()
        self.assertEqual(usbip_server.VirtualMouseServer.HOST, "127.0.0.1")
        attach = [c for c in self.commands() if "attach" in c]
        self.assertEqual(len(attach), 1)
        self.assertTrue(attach[0].endswith("attach -r 127.0.0.1 -b 1-1 --once --terse"))
        self.assertIn("Attached: Windows sees the device (USB/IP port 3, 1B1C:",
                      mouse_panel.status_label.text())
        self.assertEqual(mouse_panel.toggle_button.text(), "&Stop virtual mouse")
        self.assertFalse(mouse_panel.pid_combo.isEnabled())
        self.assertTrue(mouse_panel.message.isHidden())
        # RawAccel holds its own values already, so step 2 is done.
        self.assertEqual(self.statuses(window), "✓✓▶○○")
        self.assertEqual(window.steps_bar.next_button.text(), "Waiting for the result")
        self.assertFalse(window.steps_bar.next_button.isEnabled())

        self.calibrate(VALUES)                                      # step 3, in the game
        self.assertTrue(self.until(window, lambda: mouse_panel.writes.scalars() is not None))
        self.assertIn("In use", mouse_panel.status_label.text())
        self.assertEqual(window.calibration_panel.emulator_labels["x_pos"].text(), "1.011000")
        self.assertEqual(window.calibration_panel.emulator_labels["y_neg"].text(), "0.996000")
        self.assertEqual(window.calibration_panel.enabled_label.text(), "yes")
        self.assertIn("DIRECTIONAL WRITE", mouse_panel.log_view.toPlainText())
        self.assertIn("switched on", mouse_panel.summary_label.text())
        self.assertEqual(self.statuses(window), "✓✓✓▶○")
        self.assertEqual(window.steps_bar.next_button.text(), "Use result")
        self.assertIn("values the game sent to the virtual mouse",
                      window.calibration_panel.message.text())

        window.steps_bar.next_button.click()                        # step 4
        self.assertTrue(panel.values().is_close(VALUES, 1e-6))
        self.assertEqual(self.statuses(window), "✓✓✓✓▶")
        window.steps_bar.next_button.click()                        # step 5
        self.wait(window)
        self.assertEqual(self.statuses(window), "✓✓✓✓✓")
        self.assertTrue(self.file_values().is_close(
            Four(1011.0, 0.995054, 0.980218, 0.99006), 1e-4))

        mouse_panel.toggle_button.click()                           # stop
        self.assertEqual(mouse.state, usbip_mouse.STOPPED)
        self.assertEqual(mouse_panel.status_label.text(), "Stopped")
        self.assertEqual(mouse_panel.toggle_button.text(), "&Start virtual mouse")
        detach = [c for c in self.commands() if "detach" in c]
        self.assertEqual(detach, ["-t %d detach -p 3" % mouse.server.port]
                         if detach[0].startswith("-t") else ["detach -p 3"])
        self.assertFalse(any(" -a" in c or "--all" in c for c in self.commands()))
        self.assertIsNone(self.driver.client)
        self.assertEqual(self.statuses(window), "✓✓✓✓✓")           # the result stays

    def test_old_multipliers_are_taken_out_before_calibrating(self) -> None:
        first = self.make(tool=TOOL)
        first.rawaccel_panel.set_values(VALUES)
        first.rawaccel_panel.apply_button.click()
        self.wait(first)
        first.close()

        window = self.make(tool=TOOL)                # the next day
        self.assertEqual(self.statuses(window), "▶○○○○")
        self.started(window)
        self.assertEqual(self.statuses(window), "✓▶○○○")
        self.assertEqual(window.steps_bar.next_button.text(), "Restore original values")
        self.assertTrue(window.rawaccel_panel.restore_button.property("primary"))
        window.steps_bar.next_button.click()
        self.wait(window)
        self.assertEqual(self.file_values(), Four(1000.0, 1.0, 1.0, 1.0))
        self.assertEqual(self.statuses(window), "✓✓▶○○")
        self.calibrate(Scalars(1.02, 0.98, 1.0, 1.0))
        self.assertTrue(self.until(
            window, lambda: window.steps_bar.next_button.text() == "Use result"))

    def test_closing_the_window_detaches_our_port_and_stops_serving(self) -> None:
        window = self.make(tool=TOOL)
        mouse = self.started(window)
        self.assertEqual(mouse.state, usbip_mouse.ATTACHED)
        assert self.driver.client is not None
        self.driver.client.submit(usbip_server.DIR_IN, 1, 64)       # a waiting request
        window.close()
        self.assertFalse(mouse.running)
        self.assertIsNone(self.driver.client)
        self.assertEqual(len([c for c in self.commands() if c.endswith("detach -p 3")]), 1)
        self.assertFalse(any(" -a" in c for c in self.commands()))
        self.assertFalse(mouse.server.running)                       # nobody listens any more
        window.shutdown()                                            # twice does no harm
        self.assertEqual(len([c for c in self.commands() if "detach" in c]), 1)

    def walk(self, long: bool) -> tuple[WindowsMainWindow, list[tuple[str, int]]]:
        """Put texts from outside through the real inputs; the minimum width after each.

        The same steps with the same numbers, once with short names and once
        with very long ones: a tool path and version, names and a version in
        the settings file, a folder, a message of usbip.exe, a values file.
        """
        def name(short: str, repeated: str, times: int) -> str:
            return repeated * times if long else short
        window = self.make(tool=TOOL)
        window.show()
        content = window.scroll.widget()
        seen: list[tuple[str, int]] = []

        def note(what: str) -> None:
            for _ in range(3):
                _app.processEvents()
            seen.append((what, content.minimumSizeHint().width()))

        self.tool = "C:\\Program Files\\" + name("USBip", "USBip-in-a-long-folder-", 6) \
            + "\\usbip.exe"
        self.driver.version = "0.9.7." + name("7", "1234567890", 6)
        window.usbip_strip.check()
        note("the version of usbip-win2")
        deep = self.tmp / ("long" if long else "short")
        for part in (name("user", "a-very-long-user-name-", 3), "AppData", "Local",
                     name("x", "a-folder-with-a-long-name-", 3), "RawAccel"):
            deep = deep / part
        deep.mkdir(parents=True)
        data = json.loads((FIXTURES / "rawaccel" / "settings-1.7.1-profiles.json").read_text())
        data["version"] = "1.7.1" + name("", "-special-build", 8)
        data["profiles"][0]["name"] = name("default", "profile-with-a-long-name-", 6)
        (deep / "settings.json").write_text(json.dumps(data, indent=2))
        window.rawaccel_panel.choose_folder(str(deep))
        note("the RawAccel folder, version and profile names")
        self.driver.attach_error = "error: " + name(
            "no", widths.LONG_SENTENCE + " " + widths.LONG_PATH, 1)
        self.started(window)
        note("the message of usbip.exe")
        values = deep / (name("m", "multipliers-", 8) + ".quadsens.json")
        values.write_text(values_file.dump(VALUES, None, "0.4.0"))
        self.assertTrue(window.import_values(str(values)))
        window.rawaccel_panel.use_result()
        note("an imported values file")
        return window, seen

    def test_long_texts_never_widen_the_window(self) -> None:
        plain, short = self.walk(long=False)
        plain.close()
        window, long = self.walk(long=True)
        content = window.scroll.widget()
        self.assertIn("1234567890" * 6, window.usbip_strip.headline.text())
        mouse = window.mouse_panel.mouse()
        assert mouse is not None
        self.assertIn("something went wrong", mouse.message)
        # The same steps with long texts need no more width than with short ones.
        for (what, with_short), (_what, with_long) in zip(short, long):
            widths.assert_not_wider(self, content, QtCore.QSize(with_long, 0), with_short,
                                    "Long text in " + what)
        # With every table filled the page fits the window at its default size.
        # (Numbers take what they take; at the smallest window size a wide font
        # can make the page scroll sideways.)
        widths.assert_fits(self, content.minimumSizeHint(),
                           (widths.WINDOWS_DEFAULT_WIDTH, None), content)
        baseline = content.minimumSizeHint().width()

        def check(what: str) -> None:
            for _ in range(3):
                _app.processEvents()
            widths.assert_not_wider(self, content, content.minimumSizeHint(), baseline, what)

        # Whatever came from outside is in a label that wraps or elides.
        self.assertEqual(widths.plain_labels_with(content, (
            "1234567890", "special-build", "a-very-long", "long-name", "multipliers-",
            "something went wrong")), [])

        # And directly: every slot for such text, with texts no layout can break.
        for text in widths.LONG_TEXTS:
            self.assertGreater(widths.fill_dynamic(content, text), 8)
            check("%d characters in every dynamic text" % len(text))
        # The whole text stays reachable: as text for screen readers, and as
        # tooltip where it was cut.
        window.resize(900, 720)
        label = window.mouse_panel.status_label
        label.setText(widths.LONG_PATH)
        for _ in range(3):
            _app.processEvents()
        label.repaint()
        self.assertEqual(label.text(), widths.LONG_PATH)
        self.assertTrue(label.is_elided())

    def test_closing_a_window_that_never_started_the_mouse_runs_nothing(self) -> None:
        window = self.make(tool=TOOL)
        window.close()
        self.assertEqual(self.commands(), ["--version"])

    def test_refused_attach_shows_the_command_and_offers_no_elevation(self) -> None:
        window = self.make(tool=TOOL)
        self.driver.attach_error = "error: Access is denied."
        mouse = self.started(window)
        panel = window.mouse_panel
        self.assertEqual(mouse.state, usbip_mouse.SERVING)
        self.assertEqual(panel.status_label.text(),
                         "Serving on 127.0.0.1:%d, waiting for attach" % mouse.server.port)
        self.assertTrue(panel.message.is_error)
        self.assertIn("console as administrator", panel.message.text())
        self.assertIn("attach -r 127.0.0.1 -b 1-1", panel.message.text())
        self.assertEqual(self.statuses(window)[0], "✓")     # the server does run
        self.assertEqual([b.text() for b in panel.findChildren(QtWidgets.QAbstractButton)
                          if "administrator" in b.text().lower()], [])
        self.assertIn("USBip app", panel.message.text())    # the manual way, as a fallback
        self.assertEqual(len([c for c in self.commands() if "attach" in c]), 1)

    def test_attach_with_the_usbip_app_is_followed_and_not_undone_by_us(self) -> None:
        window = self.make()                                 # usbip.exe was not found
        mouse = self.started(window)
        panel = window.mouse_panel
        self.assertIn("waiting for attach", panel.status_label.text())
        self.assertIn("open the USBip app", panel.message.text())
        outside = usbip_server.Client("127.0.0.1", mouse.server.port)
        self.addCleanup(outside.close)
        self.assertEqual(outside.import_device(), 0)
        self.assertTrue(self.until(window, lambda: mouse.state == usbip_mouse.ATTACHED
                                   and panel.message.isHidden()))
        self.assertEqual(panel.status_label.text(),
                         "Attached: Windows sees the device (attached outside QuadSens)")
        self.driver.client = outside                         # the game talks through it
        self.calibrate(VALUES)
        self.assertTrue(self.until(
            window, lambda: window.steps_bar.next_button.text() == "Use result"))
        self.driver.client = None
        panel.toggle_button.click()
        self.assertEqual(self.commands(), [])                # nothing detached by us
        self.assertEqual(panel.status_label.text(), "Stopped")
        self.assertFalse(panel.message.is_error)
        self.assertIn("disappears on the Windows side", panel.message.text())

    def test_port_in_use_is_said_in_the_card(self) -> None:
        blocker = occupy_port()
        self.addCleanup(blocker.close)
        window = self.make(tool=TOOL, port=blocker.getsockname()[1])
        window.steps_bar.next_button.click()
        panel = window.mouse_panel
        panel.poll()
        self.assertEqual(panel.status_label.text(), "Stopped after an error")
        self.assertTrue(panel.message.is_error)
        mouse = panel.mouse()
        assert mouse is not None
        self.assertEqual(mouse.problem, usbip_server.PORT_UNAVAILABLE)
        self.assertIn(mouse.message, panel.message.text())    # whatever the wording is
        self.assertEqual(panel.toggle_button.text(), "&Start virtual mouse")
        self.assertTrue(panel.toggle_button.isEnabled())
        self.assertEqual(self.statuses(window)[0], "▶")
        self.assertFalse(any("attach" in c for c in self.commands()))

    def test_started_without_usbip_win2_it_serves_and_says_so(self) -> None:
        window = self.make()
        mouse = self.started(window)
        panel = window.mouse_panel
        self.assertEqual(mouse.state, usbip_mouse.SERVING)
        self.assertIn("was not found", panel.message.text())
        self.assertFalse(panel.message.is_error)
        self.assertEqual(self.commands(), [])
        window.close()
        self.assertFalse(mouse.running)
        self.assertEqual(self.commands(), [])


class ToolsTest(WindowCase):
    """Installing the two tools and the notice about newer versions, with stand-ins."""

    def setUp(self) -> None:
        super().setUp()
        (self.folder / "writer.exe").unlink()
        self.archive = make_zip(self.tmp / "ra.zip", GOOD)
        self.exe = b"MZ signed installer"
        pins = {USBIP: pin_for(self.exe), RAWACCEL: zip_pin(GOOD, self.archive)}
        original = dict(win_tools.PINS)
        win_tools.PINS.update(pins)
        self.addCleanup(win_tools.PINS.update, original)
        self.fake = FakeWindows(self.tmp, {pins[USBIP].asset: self.exe,
                                           pins[RAWACCEL].asset: self.archive.read_bytes()})
        self.install_tools = self.fake.tools()

    def tearDown(self) -> None:
        for window in self.windows:
            if window.install_dialog is not None:
                window.install_dialog.wait()
                window.install_dialog.close()
        super().tearDown()

    def run_dialog(self, window: WindowsMainWindow) -> win_install.Result:
        dialog = window.install_dialog
        assert dialog is not None
        dialog.start_button.click()
        result = dialog.wait()
        assert result is not None
        _app.processEvents()
        return result

    def test_lines_for_every_state(self) -> None:
        self.assertEqual(tools_strip.usbip_line(False, ""),
                         ("✕", "usbip-win2 is not installed", "Install USB/IP driver…"))
        pinned = win_tools.PINS[USBIP].version
        self.assertEqual(tools_strip.usbip_line(True, pinned),
                         ("✓", "USB/IP driver found (usbip-win2 %s)" % pinned, ""))
        mark, text, button = tools_strip.usbip_line(True, "0.9.7.7")
        self.assertEqual((mark, button), ("⚠", "Install tested version…"))
        self.assertIn("tested with " + pinned, text)
        mark, text, button = tools_strip.usbip_line(True, "0.9.9.0")
        self.assertEqual((mark, button), ("✓", ""))
        self.assertIn("newer than", text)
        self.assertEqual(tools_strip.usbip_line(True, "")[::2], ("✓", ""))

        info, line = tools_strip.RawAccelInfo, tools_strip.rawaccel_line
        self.assertEqual(line(info())[::2], ("✕", "Install RawAccel…"))
        mark, text, button = line(info("C:\\RA", "1.7.1", win_install.DRIVER_MISSING))
        self.assertEqual((mark, button), ("⚠", "Install RawAccel…"))
        self.assertIn("driver is not installed", text)
        mark, text, button = line(info("C:\\RA", "1.7.1", win_install.DRIVER_PENDING))
        self.assertEqual((mark, button), ("⚠", ""))
        self.assertIn("restart Windows", text)
        self.assertIn("Apply works after that", text)
        self.assertEqual(line(info("C:\\RA", "1.7.1", win_install.DRIVER_READY)),
                         ("✓", "RawAccel 1.7.1 found in C:\\RA", ""))
        mark, text, button = line(info("C:\\RA", "1.7.1", win_install.DRIVER_UNKNOWN))
        self.assertEqual((mark, button), ("✓", ""))
        self.assertIn("is not known", text)            # said, not guessed
        mark, text, button = line(info("C:\\RA", "1.6.1", win_install.DRIVER_READY))
        self.assertEqual((mark, button), ("⚠", "Install tested version…"))
        self.assertEqual(line(info("C:\\RA", "1.9.0", win_install.DRIVER_READY))[2], "")
        self.assertEqual(line(info("C:\\RA", "", win_install.DRIVER_READY))[::2], ("✓", ""))
        mark, text, button = line(info("", "", win_install.DRIVER_READY))
        self.assertEqual((mark, button), ("⚠", ""))
        self.assertIn("choose the folder", text)

    def test_installing_the_usb_ip_driver(self) -> None:
        window = self.make()
        strip = window.tools_strip
        self.assertEqual(self.statuses(window), "○✓○▶○")
        strip.get_button.click()
        dialog = window.install_dialog
        assert dialog is not None
        # Asked first: nothing was fetched or started by opening the dialog.
        self.assertEqual(self.fake.launched, [])
        self.assertFalse((self.tmp / "cache").exists())
        text = dialog.what_label.text()
        for words in ("administrator rights", "USB devices can disconnect", "checksum"):
            self.assertIn(words, text)
        self.assertIn(win_tools.PINS[USBIP].sha256, dialog.source_label.text())
        self.assertIn("install it yourself", dialog.source_label.text())
        self.assertTrue(dialog.folder_edit.isHidden())

        def installed(path: str, folder: str) -> win_install.Launch:
            self.tool = TOOL                     # what the installer brings
            self.driver.version = "0.9.8.1"
            return win_install.Launch(win_install.FINISHED, 0)
        self.install_tools.launch = installed
        result = self.run_dialog(window)
        self.assertEqual(result.kind, win_install.DONE)
        self.assertTrue(dialog.message.text().startswith("✓ usbip-win2 0.9.8.1 is installed"))
        self.assertEqual(dialog.cancel_button.text(), "&Close")
        self.assertFalse(dialog.start_button.isEnabled())
        # The window looked again by itself.
        self.assertEqual(strip.headline.text(), "USB/IP driver found (usbip-win2 0.9.8.1)")
        self.assertEqual(self.statuses(window), "▶✓○○○")
        dialog.cancel_button.click()
        self.assertFalse(dialog.isVisible())

    def test_declined_administrator_prompt_changes_nothing(self) -> None:
        window = self.make()
        window.tools_strip.get_button.click()
        self.fake.launch_result = win_install.Launch(win_install.DECLINED)
        result = self.run_dialog(window)
        dialog = window.install_dialog
        assert dialog is not None
        self.assertEqual(result.kind, win_install.REFUSED)
        self.assertIn("was declined", dialog.message.text())
        self.assertFalse(dialog.message.is_error)
        self.assertEqual(dialog.start_button.text(), "Try &again")
        self.assertTrue(dialog.start_button.isEnabled())
        self.assertEqual(window.tools_strip.headline.text(), "usbip-win2 is not installed")

    def test_a_download_that_does_not_verify_is_an_error_and_nothing_starts(self) -> None:
        window = self.make()
        window.tools_strip.get_button.click()
        self.fake.download_error = win_download.DownloadError(
            win_download.HASH, "The downloaded file is not the one QuadSens was tested "
                               "with. It was deleted and nothing was run.")
        self.run_dialog(window)
        dialog = window.install_dialog
        assert dialog is not None
        self.assertTrue(dialog.message.is_error)
        self.assertIn("nothing was run", dialog.message.text())
        self.assertEqual(self.fake.launched, [])

    def test_installing_rawaccel_ends_at_the_restart(self) -> None:
        self.driver_state = win_install.DRIVER_MISSING
        window = self.make(choose=False)
        strip = window.tools_strip
        self.assertEqual(strip.raw.headline.text(),
                         "RawAccel is not installed, or its folder is not chosen")
        self.assertFalse(strip.choose_button.isHidden())
        strip.raw.install_button.click()
        dialog = window.install_dialog
        assert dialog is not None
        self.assertFalse(dialog.folder_edit.isHidden())
        self.assertIn("Programs", dialog.folder_edit.text())       # the default place
        self.assertIn("RawAccel", dialog.folder_edit.text())
        for words in ("not signed", "after the computer was restarted", "does not restart"):
            self.assertIn(words, dialog.what_label.text())

        # A folder with other things in it is refused before anything is fetched.
        dialog.folder_edit.setText(str(self.tmp))
        dialog.start_button.click()
        self.assertIn("other files", dialog.message.text())
        self.assertFalse(dialog.is_busy())
        self.assertFalse((self.tmp / "cache").exists())

        target = self.tmp / "Programs" / "RawAccel"
        dialog.folder_edit.setText(str(target))

        started: list[tuple[str, str]] = []

        def installed(path: str, folder: str) -> win_install.Launch:
            started.append((path, folder))
            self.driver_state = win_install.DRIVER_PENDING      # the driver file is there
            return win_install.Launch(win_install.FINISHED, 0)
        self.install_tools.launch = installed
        result = self.run_dialog(window)
        self.assertEqual(result.kind, win_install.DONE)
        self.assertIn("Restart the computer", dialog.message.text())
        self.assertTrue((target / "installer.exe").is_file())
        # The folder is the RawAccel folder from now on, and the strip names the restart.
        self.assertEqual(window.rawaccel_panel.folder, target)
        self.assertIn("restart Windows to finish installing its driver",
                      strip.raw.headline.text())
        self.assertEqual(strip.raw.mark.text(), "⚠")
        self.assertTrue(strip.raw.install_button.isHidden())
        self.assertTrue(strip.choose_button.isHidden())
        self.assertIsNotNone(window._settings.value("tools/rawaccel_installed_at"))
        # QuadSens never restarts the computer: nothing but the installer was started,
        # from its own folder.
        self.assertEqual(started, [(str(target / "installer.exe"), str(target))])

    def test_choosing_an_existing_folder_from_the_strip(self) -> None:
        window = self.make(choose=False)
        chosen: list[str] = []
        window.rawaccel_panel.choose_folder = lambda folder="": chosen.append(folder)
        window.tools_strip.choose_button.click()
        self.assertEqual(chosen, [""])
        self.assertIsNone(window.install_dialog)

    def test_newer_release_is_a_quiet_notice_with_a_link(self) -> None:
        asked: list[str] = []

        def latest(pin: win_tools.Pin):
            asked.append(pin.key)
            if pin.key == RAWACCEL:
                return win_download.Latest(
                    "v1.8.0", "https://github.com/RawAccelOfficial/rawaccel/releases/tag/v1.8.0")
            return win_download.Latest(pin.tag, pin.release_url)      # nothing newer
        self.latest_release = latest
        window = self.make()
        strip = window.tools_strip
        self.assertTrue(self.until(window, lambda: not strip.notice_label.isHidden()))
        self.assertEqual(sorted(asked), sorted([USBIP, RAWACCEL]))
        self.assertEqual(strip.notice_label.text(),
                         "A newer RawAccel 1.8.0 is available; QuadSens was tested with 1.7.1.")
        self.assertFalse(strip.link_buttons[RAWACCEL].isHidden())
        self.assertTrue(strip.link_buttons[USBIP].isHidden())
        strip.link_buttons[RAWACCEL].click()
        self.assertEqual(window.opened,
                         ["https://github.com/RawAccelOfficial/rawaccel/releases/tag/v1.8.0"])
        self.assertIsNone(window.install_dialog)          # a notice never installs
        self.assertFalse((self.tmp / "cache").exists())
        # Check again asks again; switching it off removes the notice and stays off.
        asked.clear()
        strip.recheck_button.click()
        self.assertTrue(self.until(window, lambda: len(asked) == 2))
        window.online_action.setChecked(False)
        self.assertTrue(strip.notice_label.isHidden())
        self.assertFalse(window._settings.value("tools/online_check", True, type=bool))
        asked.clear()
        self.assertFalse(window.check_online())
        strip.recheck_button.click()
        self.assertEqual(asked, [])
        window.close()
        again = self.make()
        self.assertFalse(again.online_action.isChecked())
        self.assertEqual(asked, [])

    def test_offline_or_failing_check_is_silent(self) -> None:
        def broken(pin: win_tools.Pin):
            if pin.key == USBIP:
                raise OSError("no network")
            return None
        self.latest_release = broken
        window = self.make()
        self.assertTrue(self.until(
            window, lambda: window._online_thread is not None
            and not window._online_thread.is_alive()))
        window._take_online_results()
        self.assertTrue(window.tools_strip.notice_label.isHidden())
        self.assertFalse(window.calibration_panel.message.is_error)
        self.assertFalse(window.mouse_panel.message.is_error)

    def test_the_environment_can_switch_the_online_check_off(self) -> None:
        asked: list[str] = []
        self.latest_release = lambda pin: asked.append(pin.key)
        window = self.make()
        window.close()
        self.assertTrue(self.until(window, lambda: len(asked) == 2))
        asked.clear()
        window._environ = dict(window._environ, QUADSENS_NO_ONLINE_CHECK="1")
        self.assertFalse(window.online_check_enabled())
        self.assertFalse(window.check_online())
        self.assertEqual(asked, [])

    def test_help_says_how_to_remove_and_what_goes_online(self) -> None:
        window = self.make()
        window.setup_help_action.trigger()
        assert window.info_box is not None
        text = window.info_box.text()
        for words in ("Installed apps", "uninstaller.exe", "does not remove",
                      "api.github.com", "install them yourself", "never restarts"):
            self.assertIn(words, text)
        window.info_box.close()
        window.about_action.trigger()
        assert window.info_box is not None
        self.assertIn("api.github.com", window.info_box.text())
        self.assertIn("Nothing else is sent", window.info_box.text())
        window.info_box.close()
        self.assertFalse(any("ninstall" in button.text() or "emove" in button.text()
                             for button in window.findChildren(QtWidgets.QAbstractButton)))


class ActiveAndAutostartTest(WindowCase):
    """The indicator of RawAccel's driver and the startup entry."""

    APPLIED = Four(1011.0, 0.995054, 0.980218, 0.99006)
    OWN = Four(1000.0, 1.0, 1.0, 1.0)

    def setUp(self) -> None:
        super().setUp()
        self.windows_started = time.time() - 3600

    def live_ok(self, four: Four) -> rawaccel_live.Live:
        return rawaccel_live.Live(rawaccel_live.OK, (1, 7, 0), (("default", four),))

    def apply(self, window: WindowsMainWindow) -> None:
        window.rawaccel_panel.set_values(VALUES)
        window.rawaccel_panel.apply_button.click()
        self.wait(window)

    @needs_fake_writer
    def test_read_back_from_the_driver_after_apply(self) -> None:
        self.live = self.live_ok(self.OWN)
        window = self.make()
        window.show()
        panel = window.rawaccel_panel
        indicator = panel.indicator
        self.assertEqual(indicator.text(), "○ Neutral: the driver holds the profile's own values")
        _app.processEvents()
        self.assertGreater(indicator.y(), panel.apply_button.y())
        self.assertLess(indicator.y() - panel.apply_button.geometry().bottom(), 16)

        self.live = self.live_ok(self.APPLIED)          # what the driver holds after the writer
        self.apply(window)
        self.assertEqual(indicator.text(), "✓ Active in driver")
        self.assertEqual(indicator.property("banner"), "success")
        self.assertRegex(indicator.detail.text(),
                         r"^Applied \d\d:\d\d:\d\d, read back from the driver: output DPI 1011, "
                         r"Y/X 0\.995054, L/R 0\.980218, U/D 0\.99006\. ")
        self.assertIn("acceleration is not read back", indicator.detail.text())
        self.assertIn("Active in driver", indicator.accessibleName())
        self.assertEqual(window.tools_strip.active_label.text(), "✓ Active in driver")

        # RawAccel's own window changes something: the next look notices.
        self.live = self.live_ok(Four(1200.0, 1.0, 1.0, 1.0))
        panel.watch()
        self.assertEqual(indicator.text(), "≠ Active, but different from QuadSens")
        self.assertIn("output DPI 1200", indicator.detail.text())
        self.assertEqual(window.tools_strip.active_label.text(), indicator.text())

        # After a restart of Windows the driver holds nothing.
        self.live = rawaccel_live.Live(rawaccel_live.EMPTY, (1, 7, 0))
        panel.watch()
        self.assertEqual(indicator.text(), "⚠ Not applied since Windows started")
        self.assertEqual(indicator.property("banner"), "warning")

    @needs_fake_writer
    def test_without_a_readable_driver_it_says_sent_and_notices_a_restart(self) -> None:
        self.live = rawaccel_live.Live(rawaccel_live.OTHER_VERSION, (1, 6, 1))
        window = self.make()
        window.show()
        panel = window.rawaccel_panel
        self.assertEqual(panel.indicator.text(), "⚠ Not applied by QuadSens yet")
        self.apply(window)
        self.assertRegex(panel.indicator.text(),
                         r"^✓ Sent to the driver \d\d:\d\d:\d\d, accepted by RawAccel's writer$")
        self.assertIn("Cannot be read back", panel.indicator.detail.text())
        self.assertIn("1.6.1", panel.indicator.detail.text())
        self.assertNotIn("Active", panel.indicator.text())
        self.windows_started = time.time() + 60          # Windows was restarted since
        panel.watch()
        self.assertEqual(panel.indicator.text(), "⚠ Not applied since Windows started")

    @needs_fake_writer
    def test_a_writer_that_refuses_is_not_called_sent(self) -> None:
        self.writer(1, "Bad settings")
        window = self.make()
        window.show()
        self.apply(window)
        self.assertEqual(window.rawaccel_panel.indicator.text(),
                         "⚠ Not applied by QuadSens yet")
        self.assertIsNone(window._settings.value("rawaccel/sent_at"))

    def test_driver_missing_or_restart_pending(self) -> None:
        self.driver_state = win_install.DRIVER_MISSING
        window = self.make()
        window.show()
        self.assertEqual(window.rawaccel_panel.indicator.text(),
                         "✕ Not active: RawAccel's driver is not installed")
        self.driver_state = win_install.DRIVER_PENDING
        window.rawaccel_panel.watch()
        self.assertEqual(window.rawaccel_panel.indicator.text(),
                         "✕ Not active yet: restart Windows")
        self.assertEqual(window.tools_strip.active_label.text(),
                         "✕ Not active yet: restart Windows")

    def test_acceleration_of_the_profile_is_shown_read_only(self) -> None:
        shutil.copy(FIXTURES / "rawaccel" / "settings-1.7.1-profiles.json", self.settings_file)
        original = self.settings_file.read_bytes()
        window = self.make()
        window.show()
        panel = window.rawaccel_panel
        self.assertEqual(panel.accel_label.text(), "Acceleration: off")
        panel.profile_combo.setCurrentIndex(1)
        self.assertEqual(panel.accel_label.text(),
                         "Acceleration: Classic, acceleration 0.005, exponentClassic 2; "
                         "cap (output) input 15, output 1.5; gain")
        panel.profile_combo.setCurrentIndex(2)
        self.assertIn("Natural", panel.accel_label.text())
        self.assertEqual(self.settings_file.read_bytes(), original)     # nothing written

        started: list[tuple[str, str]] = []
        panel.launch = lambda program, folder: started.append((program, folder)) or True
        panel.settings_button.click()
        self.assertEqual(started, [])                          # no rawaccel.exe in the folder
        self.assertIn("rawaccel.exe is not in", panel.message.text())
        (self.folder / "rawaccel.exe").write_bytes(b"MZ")
        panel.settings_button.click()
        self.assertEqual(started, [(str(self.folder / "rawaccel.exe"), str(self.folder))])
        self.assertIn("Started RawAccel", panel.message.text())

    def test_autostart_is_off_until_ticked_and_gone_when_unticked(self) -> None:
        window = self.make()
        panel = window.rawaccel_panel
        check = panel.autostart_check
        self.assertFalse(check.isChecked())
        self.assertEqual(self.registry.values, {})
        self.assertEqual(panel.boot_label.text(), "Values are lost when Windows restarts.")
        for words in ("HKEY_CURRENT_USER", "writer.exe", "Task Manager", "sign-in",
                      "no administrator rights"):
            self.assertIn(words, check.toolTip())
        check.click()
        self.assertTrue(check.isChecked())
        self.assertEqual(self.registry.values, {
            win_autostart.VALUE_NAME: win_autostart.command(self.folder)})
        self.assertIn(str(self.folder / "writer.exe"),
                      self.registry.values[win_autostart.VALUE_NAME])
        self.assertIn("At every sign-in", panel.boot_label.text())
        self.assertIn("At every sign-in", check.accessibleDescription())
        check.click()
        self.assertFalse(check.isChecked())
        self.assertEqual(self.registry.values, {})
        self.assertEqual(panel.boot_label.text(), "Values are lost when Windows restarts.")

    def test_autostart_reflects_what_is_there_and_follows_the_folder(self) -> None:
        self.registry.values[win_autostart.VALUE_NAME] = win_autostart.command(self.folder)
        window = self.make()
        panel = window.rawaccel_panel
        self.assertTrue(panel.autostart_check.isChecked())          # found, not created
        other = self.tmp / "Other" / "RawAccel"
        shutil.copytree(self.folder, other)
        panel.choose_folder(str(other))
        self.assertEqual(self.registry.values[win_autostart.VALUE_NAME],
                         win_autostart.command(other))
        self.assertTrue(panel.autostart_check.isChecked())
        # An entry that points elsewhere (changed by hand) is shown as such.
        self.registry.values[win_autostart.VALUE_NAME] = \
            '"D:\\Old\\RawAccel\\writer.exe" "D:\\Old\\RawAccel\\settings.json"'
        panel.reload()
        self.assertTrue(panel.autostart_check.isChecked())
        self.assertIn("another folder (D:\\Old\\RawAccel)", panel.boot_label.text())
        panel.autostart_check.click()                               # untick removes it
        self.assertEqual(self.registry.values, {})

    def test_autostart_needs_a_folder_and_reports_a_registry_that_refuses(self) -> None:
        window = self.make(choose=False)
        panel = window.rawaccel_panel
        self.assertFalse(panel.settings_button.isEnabled())           # no folder, no RawAccel
        self.assertEqual(panel.accel_label.text(),
                         "Acceleration: not read (no settings file)")
        self.assertFalse(panel.autostart_check.isEnabled())
        self.assertIn("Needs the RawAccel folder", panel.boot_label.text())
        panel.choose_folder(str(self.folder))
        self.assertTrue(panel.autostart_check.isEnabled())
        self.registry.fail = True
        panel.autostart_check.click()
        self.assertFalse(panel.autostart_check.isChecked())
        self.assertIn("could not be written", panel.boot_label.text())
        self.assertIn("Access is denied", panel.boot_label.text())
        self.assertEqual(self.registry.values, {})


class AccelEditTest(WindowCase):
    """Editing the acceleration of a RawAccel profile."""

    def setUp(self) -> None:
        super().setUp()
        (self.folder / "writer.exe").unlink()          # written at once, also on Windows
        shutil.copy(FIXTURES / "rawaccel" / "settings-1.7.1-profiles.json", self.settings_file)
        self.original = self.settings_file.read_bytes()

    def found(self, index: int = 0) -> accel_edit.Found:
        document = rawaccel.read(self.settings_file)
        return accel_edit.classify_rawaccel(document.profile(index), document.data)

    def test_collapsed_by_default_with_the_summary(self) -> None:
        window = self.make()
        window.show()
        panel = window.rawaccel_panel
        self.assertFalse(panel.accel_toggle.isChecked())
        self.assertFalse(panel.accel_editor.isVisible())
        self.assertEqual(panel.accel_label.text(), "Acceleration: off")
        content = window.scroll.widget()
        widths.assert_fits(self, content.minimumSizeHint(),
                           (widths.WINDOWS_CONTENT_WIDTH, windows_window.DEFAULT_SIZE[1] - 30),
                           content)
        panel.accel_toggle.setChecked(True)
        _app.processEvents()
        editor = panel.accel_editor
        self.assertTrue(editor.isVisible())
        self.assertEqual(editor.found().curve, accel_edit.make("off"))
        self.assertIn("after the curve", editor.note_label.text())
        self.assertIn('In RawAccel this is "exponentClassic"',
                      editor.pages["classic"].spins["exponent"].toolTip())
        self.assertEqual(len(window.steps_bar.chips), 5)             # not a step

    def test_each_mode_changes_only_its_tokens_and_restore_puts_every_byte_back(self) -> None:
        window = self.make()
        window.show()
        panel = window.rawaccel_panel
        editor = panel.accel_editor
        curves = [accel_edit.make("linear", accel=0.02),
                  accel_edit.make("classic", accel=0.02, exponent=2.5, cap=3.0),
                  accel_edit.make("power", exponent=0.3, output_offset=1.0, cap=3.0),
                  accel_edit.make("natural", limit=2.0, offset=5.0, gain=False),
                  accel_edit.make("jump", input=30, output=2.0),
                  accel_edit.make("off")]
        before = json.loads(self.original)
        for curve in curves:
            editor.set_curve(curve)
            if not editor.apply_button.isEnabled():                  # off is what is there
                continue
            self.assertIn("→  New: " + curve.describe(), editor.change_label.text())
            editor.apply_button.click()
            self.wait(window)
            self.assertTrue(self.found().curve.same(curve), curve)
            after = json.loads(self.settings_file.read_text())
            # Nothing but the accel arguments of this profile changed, and no key in them
            # was added or dropped.
            got = after["profiles"][0].pop(accel_edit.RA_ARGS)
            want = dict(before["profiles"][0])
            original_args = want.pop(accel_edit.RA_ARGS)
            self.assertEqual(after["profiles"][0], want)
            self.assertEqual(after["profiles"][1:], before["profiles"][1:])
            self.assertEqual(list(got), list(original_args))
            self.assertEqual(got["data"], [])
            self.assertEqual(accel_edit.rawaccel_errors(got), [])
            self.assertEqual(len(self.settings_file.read_bytes().splitlines()),
                             len(self.original.splitlines()))
            # RawAccel has no linear mode of its own: it is classic with exponent 2.
            name = "classic" if curve.mode == "linear" else curve.mode
            self.assertIn(name, panel.accel_label.text().lower())
        self.assertEqual(self.found().curve, accel_edit.make("off"))
        # A cap that is off is written as 0, not left at the 1.5 another mode had.
        editor.set_curve(accel_edit.make("linear"))
        editor.apply_button.click()
        self.wait(window)
        args = json.loads(self.settings_file.read_text())["profiles"][0][accel_edit.RA_ARGS]
        self.assertEqual((args["Cap / Jump"]["y"], args["Cap mode"], args["Gain / Velocity"],
                          args["exponentClassic"]), (0.0, "output", True, 2.0))
        # One backup of the untouched file, made at the first change.
        backups = sorted(self.folder.glob("settings.quadsens-backup-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original)
        # Revert: one step back. Restore original: every byte as it was.
        self.assertTrue(editor.revert_button.isEnabled())
        editor.revert_button.click()
        self.wait(window)
        self.assertEqual(self.found().curve, accel_edit.make("off"))
        editor.set_curve(accel_edit.make("jump"))
        editor.apply_button.click()
        self.wait(window)
        self.assertTrue(panel.restore_button.isEnabled())
        self.assertTrue(panel.is_neutral())               # the four values were not touched
        panel.restore_button.click()
        self.wait(window)
        self.assertEqual(self.settings_file.read_bytes(), self.original)
        self.assertFalse(panel.restore_button.isEnabled())
        self.assertFalse(editor.revert_button.isEnabled())

    def test_restore_original_covers_values_and_acceleration_together(self) -> None:
        window = self.make()
        window.show()
        panel = window.rawaccel_panel
        panel.set_values(VALUES)
        panel.apply_button.click()
        self.wait(window)
        panel.accel_editor.set_curve(accel_edit.make("natural", limit=2.0))
        panel.accel_editor.apply_button.click()
        self.wait(window)
        self.assertNotEqual(self.settings_file.read_bytes(), self.original)
        self.assertAlmostEqual(self.file_values().sens, 1011.0)      # still applied
        panel.restore_button.click()
        self.wait(window)
        self.assertEqual(self.settings_file.read_bytes(), self.original)

    def test_refused_sets_and_read_only_profiles_write_nothing(self) -> None:
        window = self.make()
        window.show()
        panel = window.rawaccel_panel
        editor = panel.accel_editor
        bad = accel_edit.make("classic", accel=0.001, exponent=1.01, cap=5.0)
        editor.set_curve(bad)
        self.assertFalse(editor.apply_button.isEnabled())
        self.assertIn("yeetmouse cannot compute", editor.problem_label.text())
        panel.apply_accel(bad)
        self.assertIn("Nothing was changed", panel.message.text())
        self.assertEqual(self.settings_file.read_bytes(), self.original)

        data = json.loads(self.original)
        data["profiles"][1][accel_edit.RA_ARGS]["Cap mode"] = "input"
        data["profiles"][2][accel_edit.RA_SPEED][accel_edit.RA_WHOLE] = False
        self.settings_file.write_text(json.dumps(data, indent=2))
        changed = self.settings_file.read_bytes()
        panel.reload()
        for index, words in ((1, "cap mode"), (2, "by component")):
            panel.profile_combo.setCurrentIndex(index)
            self.assertFalse(editor.found().editable)
            self.assertFalse(editor.editing.isVisibleTo(editor))
            self.assertIn(words, editor.readonly_label.text())
            self.assertIn("settings program of RawAccel", editor.readonly_label.text())
            panel.apply_accel(accel_edit.make("linear"))
            self.assertIn("does not edit", panel.message.text())
            self.assertEqual(self.settings_file.read_bytes(), changed)
        self.assertTrue(panel.settings_button.isEnabled())           # the way to RawAccel

    def test_indicator_names_the_acceleration_read_back(self) -> None:
        args = dict(json.loads(self.original)["profiles"][1][accel_edit.RA_ARGS])
        cap = args.pop("Cap / Jump")
        args.pop("data")
        items = tuple(args.items()) + (("cap.x", cap["x"]), ("cap.y", cap["y"]))
        self.live = rawaccel_live.Live(
            rawaccel_live.OK, (1, 7, 0), (("flick", Four(800.0, 1.1, 0.98, 1.02)),),
            accels=(("flick", items),))
        window = self.make()
        window.show()
        panel = window.rawaccel_panel
        panel.profile_combo.setCurrentIndex(1)
        panel.watch()
        self.assertIn("Acceleration read back from the driver: Linear: Acceleration 0.005; "
                      "cap 1.5.", panel.indicator.detail.text())
        panel.accel_editor.set_curve(accel_edit.make("jump"))
        panel.accel_editor.apply_button.click()
        self.wait(window)
        panel.watch()                    # the stand-in driver still holds the old curve
        self.assertIn("The driver holds another acceleration than settings.json",
                      panel.indicator.detail.text())


class ImportCleanlinessTest(unittest.TestCase):
    """The Windows path must not pull in anything that only exists on Linux."""

    # quadsens.persist is imported for an enum the step logic shares; it has no
    # POSIX-only import and runs nothing at import time.
    # The protocol handler (quadsens.emulator) and the card of the virtual mouse
    # are shared with Windows since the USB/IP server; what talks to /dev/uhid
    # there is inside functions that Windows never calls. The driver side
    # (yeetmouse, pkexec, the installer, their hints and widgets) stays out.
    LINUX_ONLY = ("quadsens.driver", "quadsens.driver_setup",
                  "quadsens.hints", "quadsens.ui.main_window",
                  "quadsens.ui.driver_panel",
                  "quadsens.ui.driver_strip", "quadsens.ui.setup_dialog")
    SHARED = ("quadsens.emulator", "quadsens.uhid_text", "quadsens.ui.mouse_panel",
              "quadsens.ui.usbip_panel", "quadsens.usbip_client", "quadsens.usbip_mouse",
              "quadsens.usbip_server")
    POSIX_ONLY = ("fcntl", "termios", "pwd", "grp", "resource", "pty", "tty")

    def loaded(self, code: str, platform: str) -> set[str]:
        env = dict(os.environ, QUADSENS_PLATFORM=platform, QT_QPA_PLATFORM="offscreen")
        output = subprocess.run(
            [sys.executable, "-c", code + "\nimport sys\nprint('\\n'.join(sorted(sys.modules)))"],
            capture_output=True, text=True, env=env, cwd=str(ROOT), check=True).stdout
        return set(output.split())

    def test_windows_window_imports_nothing_linux_only(self) -> None:
        modules = self.loaded(
            "import quadsens.__main__, quadsens.platforms, quadsens.rawaccel, "
            "quadsens.values_file\nfrom quadsens.ui.windows_window import WindowsMainWindow",
            "windows")
        self.assertIn("quadsens.ui.windows_window", modules)
        self.assertIn("quadsens.ui.rawaccel_panel", modules)
        self.assertEqual(sorted(modules & set(self.LINUX_ONLY)), [])
        self.assertEqual(sorted(set(self.SHARED) - modules), [])
        # None of the modules on this path may call what only POSIX has. (Looking
        # at sys.modules for fcntl or pwd would prove nothing here: on Linux the
        # standard library loads those itself.)
        missing_on_windows = ("getuid", "geteuid", "getgid", "getegid", "getgroups",
                              "uname", "fork", "chown", "mkfifo", "O_NOFOLLOW", "getpwuid")
        offenders = []
        for name in sorted(modules):
            if not name.startswith("quadsens") or name in ("quadsens.persist",
                                                           "quadsens.emulator"):
                continue
            path = ROOT / (name.replace(".", "/") + ".py")
            if not path.exists():
                path = ROOT / name.replace(".", "/") / "__init__.py"
            source = path.read_text(encoding="utf-8")
            offenders += [f"{name}: {word}" for word in missing_on_windows
                          if f".{word}" in source]
        self.assertEqual(offenders, [])

    def test_the_shared_protocol_handler_keeps_uhid_inside_functions(self) -> None:
        """quadsens.emulator is imported on Windows; /dev/uhid must stay behind calls."""
        import ast
        tree = ast.parse((ROOT / "quadsens" / "emulator.py").read_text(encoding="utf-8"))
        offenders = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    name = getattr(inner.func, "attr", getattr(inner.func, "id", ""))
                    if name in ("open", "ioctl", "geteuid", "getuid", "getgroups"):
                        offenders.append(f"line {inner.lineno}: {name}")
        self.assertEqual(offenders, [])
        for module in ("usbip_server", "usbip_client", "usbip_mouse"):
            source = (ROOT / "quadsens" / (module + ".py")).read_text(encoding="utf-8")
            for word in ("pkexec", "yeetmouse", "PySide", "PyQt", ".ui."):
                self.assertNotIn(word, source, module)

    def test_no_posix_only_import_at_module_level_anywhere(self) -> None:
        import ast
        offenders = []
        for path in sorted((ROOT / "quadsens").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:     # module level only; inside functions is fine
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module.split(".")[0]]
                offenders += [f"{path.name}: {name}" for name in names
                              if name in self.POSIX_ONLY]
        self.assertEqual(offenders, [])

    def test_entry_point_chooses_the_window_by_platform(self) -> None:
        source = (ROOT / "quadsens" / "__main__.py").read_text()
        windows_branch = source[source.index("platforms.WINDOWS"):source.index("else:")]
        self.assertIn("WindowsMainWindow", windows_branch)
        self.assertNotIn("MainWindow(QtCore.QSettings(), DEFAULT_GLOB", windows_branch)
        linux = self.loaded("from quadsens.ui.main_window import MainWindow", "linux")
        self.assertIn("quadsens.emulator", linux)
        self.assertNotIn("quadsens.ui.rawaccel_panel", linux)
        # Linux never fetches or installs anything: that code is not even loaded.
        for name in ("quadsens.win_tools", "quadsens.win_download", "quadsens.win_install",
                     "quadsens.ui.install_dialog", "quadsens.ui.tools_strip"):
            self.assertNotIn(name, linux)


if __name__ == "__main__":
    unittest.main()
