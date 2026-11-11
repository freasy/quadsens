"""Offscreen tests of the main window. Skipped when no Qt binding is installed.

Nothing here opens /dev/uhid, runs pkexec or touches /sys: the driver points at
a temp directory and the calibration glob at a copy of the fixture file.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from quadsens.ui.qt import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover - depends on the environment
    raise unittest.SkipTest("no Qt binding (PySide6) installed") from None

import quadsens
from quadsens import about, colors, driver_setup, emulator, hints
from quadsens.driver import PARAM_NAMES, Driver
from quadsens.model import Scalars
from quadsens.persist import Saver, SaveState
from quadsens.steps import Step
from quadsens.ui.driver_panel import CHANGED_MARK
from quadsens.ui.main_window import MainWindow, about_html
from quadsens.ui.mouse_panel import WRITE_PREFIX, write_line_colors
from quadsens.ui.scale_spin import ScaleSpinBox
from quadsens.ui.setup_dialog import DriverSetupDialog
from quadsens.ui.status import StatusDot
from quadsens.ui.theme import (
    CONTROL_HEIGHT,
    ThemeManager,
    current_theme,
    env_mode,
    stored_mode,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ASFSettings.json"
FAKE_TOOL = Path(__file__).parent / "fake_yeetmousectl.py"
BRANDS = re.compile(r"purevector|omnisense|corsair|sabre|aimlab", re.IGNORECASE)
_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])


def _luminance(color: QtGui.QColor) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return (0.2126 * channel(color.red()) + 0.7152 * channel(color.green())
            + 0.0722 * channel(color.blue()))


def _contrast(a: QtGui.QColor, b: QtGui.QColor) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class FakeSession:
    """Stands in for the uhid session, so that "running" can be tested without uhid."""

    def __init__(self, product_id: int) -> None:
        self.product_id = product_id
        self.running = False
        self.fds: list[int] = []
        self.open_count = 0

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def pump(self, _fd: int) -> None:
        pass


def _mnemonic(text: str) -> str:
    match = re.search(r"&([^&])", text.replace("&&", ""))
    return match.group(1).lower() if match else ""


class MainWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.params = self.tmp / "sys" / "yeetmouse" / "parameters"
        self.params.mkdir(parents=True)
        for name in (*PARAM_NAMES, "update"):
            (self.params / name).write_text("1.000000\n" if name != "update" else "0\n")
        self.cal_dir = self.tmp / "Users" / "u1" / "ASFData" / "mouse"
        self.cal_dir.mkdir(parents=True)
        self.cal_file = self.cal_dir / "ASFSettings.json"
        shutil.copy(FIXTURE, self.cal_file)
        self.pattern = str(self.tmp / "Users" / "*" / "ASFData" / "*" / "ASFSettings.json")
        self.uhid = self.tmp / "uhid"  # stands in for /dev/uhid; never opened
        self.uhid.write_bytes(b"")
        self.config = self.tmp / "yeetmouse.conf"  # stands in for /etc/yeetmouse.conf
        self.config.write_text("sens=1\n", encoding="ascii")
        self.saver = Saver(self.config, tool=FAKE_TOOL)
        patcher = mock.patch.dict(os.environ, {"QUADSENS_FAKE_PARAMS": str(self.params),
                                               "QUADSENS_FAKE_MODE": "fork"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for window in self.windows:
            window.close()
            window.deleteLater()
        _app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete.value)
        _app.processEvents()

    def make_window(self) -> MainWindow:
        settings = QtCore.QSettings(str(self.tmp / "settings.ini"),
                                    QtCore.QSettings.Format.IniFormat)
        window = MainWindow(settings, self.pattern, Driver(self.params),
                            str(self.tmp / "state.json"), uhid_path=str(self.uhid),
                            saver=self.saver)
        self.windows.append(window)
        return window

    def spin_values(self, window: MainWindow) -> list[float]:
        return [round(spin.value(), 4) for spin in window.driver_panel.spins]

    def test_shows_calibration_and_driver_values(self) -> None:
        window = self.make_window()
        cal = window.calibration_panel
        self.assertEqual(cal.path_edit.text(), str(self.cal_file))
        self.assertEqual(cal.file_labels["x_pos"].text(), "1.037500")
        self.assertEqual(cal.file_labels["y_neg"].text(), "0.875000")
        self.assertTrue(cal.mtime_label.text().startswith("Modified: 2"))
        self.assertEqual(cal.compass.values(),
                         (1.04, 0.99, 0.93, 1.0, 0.97, 1.02, 1.08, 0.95))
        self.assertIn("NE 0.9900", cal.compass.accessibleDescription())
        drv = window.driver_panel
        self.assertEqual([label.text() for label in drv.current_labels], ["1.000000"] * 4)
        self.assertIn("values read from the driver", drv.status_label.text())

    def test_use_calibration_flip_reset_apply(self) -> None:
        window = self.make_window()
        drv = window.driver_panel
        drv.use_button.click()
        self.assertEqual(self.spin_values(window), [1.0375, 0.9625, 1.125, 0.875])
        drv.flip_check.setChecked(True)  # exchanges the Y values in place
        self.assertEqual(self.spin_values(window), [1.0375, 0.9625, 0.875, 1.125])
        drv.use_button.click()  # and the mapping stays flipped
        self.assertEqual(self.spin_values(window), [1.0375, 0.9625, 0.875, 1.125])
        self.assertIn("Y flipped", drv.message.text())

        drv.apply_button.click()
        self.assertEqual((self.params / "DirScaleYPos").read_text(), "0.875000\n")
        self.assertEqual((self.params / "DirScaleYNeg").read_text(), "1.125000\n")
        self.assertEqual((self.params / "update").read_text(), "1\n")
        self.assertEqual([label.text() for label in drv.current_labels],
                         ["1.037500", "0.962500", "0.875000", "1.125000"])
        self.assertIn("driver reports the new values", drv.message.text())
        self.assertFalse(drv.message.is_error)

        drv.reset_button.click()
        self.assertEqual(self.spin_values(window), [1.0, 1.0, 1.0, 1.0])

    def test_settings_persist(self) -> None:
        first = self.make_window()
        first.driver_panel.use_button.click()
        first.driver_panel.flip_check.setChecked(True)
        first.driver_panel.apply_button.click()
        first.mouse_panel.set_product_id(0x2B44)
        first.resize(800, 600)
        self.assertFalse(first.driver_panel.auto_check.isChecked())  # default off
        first.driver_panel.auto_check.setChecked(True)
        first.close()

        for name in PARAM_NAMES:  # something else reset the driver meanwhile
            (self.params / name).write_text("1.000000\n")
        second = self.make_window()
        self.assertTrue(second.driver_panel.flip_check.isChecked())
        self.assertEqual(second.mouse_panel.product_id(), 0x2B44)
        self.assertEqual(second.driver_panel.saved_scalars(),
                         Scalars(1.0375, 0.9625, 0.875, 1.125))
        second.apply_saved_if_enabled()
        self.assertEqual((self.params / "DirScaleXPos").read_text(), "1.037500\n")

    def test_refresh_states(self) -> None:
        window = self.make_window()
        cal = window.calibration_panel
        self.cal_file.write_text("{ broken", encoding="utf-8")
        cal.refresh_button.click()
        self.assertIsNone(cal.calibration)
        self.assertTrue(cal.message.is_error)
        self.assertIn("not valid JSON", cal.message.text())
        window.driver_panel.use_button.click()
        self.assertTrue(window.driver_panel.message.is_error)

        self.cal_file.unlink()
        cal.refresh_button.click()
        self.assertEqual(cal.path_edit.text(), "")
        self.assertIn("No calibration file found", cal.message.text())
        self.assertIsNone(cal.compass.values())

    def test_file_change_is_picked_up_automatically(self) -> None:
        window = self.make_window()
        cal = window.calibration_panel
        text = FIXTURE.read_text(encoding="utf-8").replace('"XPos":1.0375', '"XPos":1.5')
        replacement = self.cal_dir / "new.tmp"
        replacement.write_text(text, encoding="utf-8")
        os.replace(replacement, self.cal_file)  # atomic replace, as editors and games do
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and cal.file_labels["x_pos"].text() != "1.500000":
            _app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
        self.assertEqual(cal.file_labels["x_pos"].text(), "1.500000")

    def test_driver_states(self) -> None:
        shutil.rmtree(self.params.parent)
        window = self.make_window()
        drv = window.driver_panel
        self.assertIn("module not loaded", drv.status_label.text())
        self.assertEqual(drv.current_labels[0].text(), "–")
        drv.apply_button.click()
        self.assertTrue(drv.message.is_error)
        self.assertIn("not loaded", drv.message.text())

    def test_emulator_writes_are_shown(self) -> None:
        window = self.make_window()
        mouse = window.mouse_panel
        device = emulator.BragiDevice(log=mouse._on_entry)
        for prop, value in ((0x125, 1.5), (0x126, 0.75), (0x127, 1.25), (0x128, 0.5)):
            request = bytes([0, 1, 0, emulator.CMD_SET]) + struct.pack("<Hf", prop, value)
            device.handle(request)
        device.handle(bytes([0, 1, 0, emulator.CMD_SET]) + struct.pack("<HI", 0x129, 1))
        cal = window.calibration_panel
        self.assertEqual(cal.emulator_labels["x_pos"].text(), "1.500000")
        self.assertEqual(cal.emulator_labels["y_neg"].text(), "0.500000")
        self.assertEqual(cal.enabled_label.text(), "yes")
        self.assertIn("DIRECTIONAL WRITE", mouse.log_view.toPlainText())

    def test_log_is_bounded(self) -> None:
        window = self.make_window()
        for index in range(700):
            window.mouse_panel._on_entry({"t": 0.0, "event": "line%d" % index})
        self.assertEqual(window.mouse_panel.log_view.blockCount(), 500)

    def test_start_failure_is_shown_inline(self) -> None:
        window = self.make_window()
        mouse = window.mouse_panel
        mouse._factory = lambda pid, on_log: emulator.EmulatorSession(
            pid, None, on_log, uhid_path=str(self.tmp / "no-uhid"))
        mouse.toggle_button.click()
        self.assertFalse(mouse.is_running())
        self.assertEqual(mouse.status_label.text(), "Stopped")
        self.assertTrue(mouse.message.is_error)
        self.assertIn("cannot create uhid device", mouse.message.text())
        self.assertEqual(mouse.dot._state, StatusDot.OFF)

    def test_fits_small_screens_and_names_controls(self) -> None:
        window = self.make_window()
        window.show()
        _app.processEvents()
        hint = window.minimumSizeHint()
        self.assertLessEqual(hint.width(), 720, hint)
        self.assertLessEqual(hint.height(), 520, hint)
        interactive = (QtWidgets.QAbstractButton, QtWidgets.QAbstractSpinBox,
                       QtWidgets.QComboBox, QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit)
        for widget in window.findChildren(QtWidgets.QWidget):
            if isinstance(widget, interactive) and widget.objectName() != "qt_spinbox_lineedit":
                name = widget.accessibleName() or getattr(widget, "text", lambda: "")()
                self.assertTrue(name, f"{widget!r} has no accessible name or text")
                self.assertNotEqual(widget.focusPolicy(), QtCore.Qt.FocusPolicy.NoFocus)

    # ---- window chrome ------------------------------------------------------

    def test_title_icon_and_menus(self) -> None:
        window = self.make_window()
        self.assertEqual(window.windowTitle(), "QuadSens")
        self.assertFalse(window.windowIcon().isNull())
        self.assertFalse(window.windowIcon().pixmap(32, 32).isNull())
        titles = [action.text() for action in window.menuBar().actions()]
        self.assertEqual(titles, ["&File", "View", "&Help"])
        self.assertEqual(ThemeManager.instance().mode(), "quadsens")
        self.assertEqual(window.open_folder_action.shortcut().toString(), "Ctrl+O")
        self.assertEqual(window.quit_action.shortcut().toString(), "Ctrl+Q")
        self.assertEqual(window.steps_action.shortcut().toString(), "F1")
        self.assertEqual(window.about_action.text(), "&About QuadSens")

    def test_about_box(self) -> None:
        text = about_html()
        for expected in ("QuadSens " + quadsens.__version__, "GPL-3.0-or-later",
                         'href="https://github.com/freasy/quadsens"', "not affiliated"):
            self.assertIn(expected, text)
        window = self.make_window()
        window.about_action.trigger()
        box = window.info_box
        assert box is not None
        self.assertEqual(box.windowTitle(), "About QuadSens")
        self.assertIn(quadsens.__version__, box.text())
        box.close()
        _app.processEvents()
        self.assertIsNone(window.info_box)
        window.steps_action.trigger()
        box = window.info_box
        assert box is not None
        self.assertIn("Start the virtual mouse", box.text())
        self.assertIn("Save to config", box.text())
        box.close()

    def test_open_result_folder(self) -> None:
        window = self.make_window()
        opened: list[str] = []
        window.open_url = lambda url: opened.append(url.toLocalFile())
        window.open_folder_action.trigger()
        self.assertEqual(opened, [str(self.cal_dir)])
        shutil.rmtree(self.tmp / "Users")
        window.open_folder_action.trigger()
        self.assertEqual(len(opened), 1)
        self.assertIn("no result folder yet", window.calibration_panel.message.text())

    def test_quit_action_closes_and_saves(self) -> None:
        window = self.make_window()
        window.show()
        window.mouse_panel.set_product_id(0x2B45)
        window.quit_action.trigger()
        self.assertFalse(window.isVisible())
        settings = QtCore.QSettings(str(self.tmp / "settings.ini"),
                                    QtCore.QSettings.Format.IniFormat)
        self.assertEqual(int(settings.value("emulator/pid", 0, type=int)), 0x2B45)

    def test_mnemonics_are_unique(self) -> None:
        window = self.make_window()
        for running_text in (False, True):
            if running_text:
                window.mouse_panel.toggle_button.setText("&Stop virtual mouse")
            seen: dict[str, str] = {}
            texts = [action.text() for action in window.menuBar().actions()]
            texts += [w.text() for w in window.findChildren(QtWidgets.QAbstractButton)]
            texts += [w.text() for w in window.findChildren(QtWidgets.QLabel)
                      if w.buddy() is not None]
            for text in texts:
                key = _mnemonic(text)
                if key:
                    self.assertNotIn(key, seen, f"{text!r} and {seen.get(key)!r} share Alt+{key}")
                    seen[key] = text
            self.assertGreaterEqual(len(seen), 18)

    def test_tab_order_follows_the_workflow(self) -> None:
        window = self.make_window()
        window.show()
        _app.processEvents()
        wanted = [w for w in window.tab_order if w.isEnabled()]
        chain: list[QtWidgets.QWidget] = []
        widget = wanted[0]
        for _ in range(400):
            if widget in wanted:
                chain.append(widget)
            widget = widget.nextInFocusChain()
            if widget is wanted[0]:
                break
        self.assertEqual(chain, wanted)

    def test_controls_have_descriptions(self) -> None:
        window = self.make_window()
        interactive = (QtWidgets.QAbstractButton, QtWidgets.QAbstractSpinBox,
                       QtWidgets.QComboBox, QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit)
        count = 0
        for widget in window.findChildren(QtWidgets.QWidget):
            if not isinstance(widget, interactive):
                continue
            if widget.objectName().startswith("qt_") or widget.window() is not window:
                continue
            count += 1
            self.assertTrue(widget.accessibleName() or getattr(widget, "text", lambda: "")(),
                            f"{widget!r} has no name")
            self.assertTrue(widget.accessibleDescription(), f"{widget!r} has no description")
        self.assertGreaterEqual(count, 16)

    def test_no_trademarks_in_the_interface(self) -> None:
        window = self.make_window()
        window.about_action.trigger()
        texts = [window.windowTitle(), about_html(), *hints.FIRST_STEPS]
        for action in window.findChildren(QtGui.QAction):
            texts += [action.text(), action.toolTip(), action.statusTip()]
        for widget in window.findChildren(QtWidgets.QWidget):
            texts += [widget.toolTip(), widget.accessibleName(),
                      widget.accessibleDescription(), widget.windowTitle()]
            for getter in ("text", "title", "placeholderText"):
                if hasattr(widget, getter):
                    texts.append(getattr(widget, getter)())
        hits = [text for text in texts if BRANDS.search(text)]
        self.assertEqual(hits, [])
        self.assertIsNone(BRANDS.search(about.NOT_AFFILIATED))

    # ---- empty states -------------------------------------------------------

    def test_no_result_file_says_what_to_do(self) -> None:
        shutil.rmtree(self.tmp / "Users")
        window = self.make_window()
        cal = window.calibration_panel
        self.assertEqual(cal.message.text(), hints.NO_RESULT)
        self.assertIn("Run the per-direction calibration", cal.message.text())
        self.assertIsNone(cal.diagram.scalars())
        self.assertFalse(cal.message.is_error)
        self.assertIn("Nothing received yet", window.mouse_panel.log_view.placeholderText())

    def test_driver_problem_states_say_what_to_do(self) -> None:
        window = self.make_window()
        drv = window.driver_panel
        self.assertTrue(drv.status_label.isHidden())  # nothing to do while it works

        (self.params / "DirScaleYNeg").unlink()
        drv.read_button.click()
        self.assertFalse(drv.status_label.isHidden())
        self.assertIn("no DirScale parameters", drv.status_label.text())
        self.assertIn("Help > Driver setup", drv.status_label.text())
        self.assertTrue(drv.pending_label.isHidden())  # the problem takes its place
        self.assertTrue(drv.saved_label.isHidden())
        self.assertTrue(drv.apply_button.isEnabled())
        self.assertFalse(drv.save_button.isEnabled())
        self.assertTrue(any("feature/directional-scalars" in text
                            for text in window.setup_problems()))

        shutil.rmtree(self.params.parent)
        drv.read_button.click()
        self.assertIn("Install or load the fork", drv.status_label.text())
        window.setup_action.trigger()
        box = window.info_box
        assert box is not None
        self.assertIn("sudo modprobe yeetmouse", box.text())
        box.close()

    @unittest.skipIf(os.geteuid() == 0, "root can read everything")
    def test_unreadable_driver_points_to_the_group(self) -> None:
        for name in PARAM_NAMES:
            (self.params / name).chmod(0)
        window = self.make_window()
        text = window.driver_panel.status_label.text()
        self.assertIn("not readable", text)
        self.assertIn("Join group yeetmouse", text)
        self.assertIn("log in again", text)
        self.assertFalse(window.driver_panel.status_label.isHidden())
        long_form = " ".join(window.setup_problems())
        self.assertIn("sudo usermod -aG yeetmouse $USER", long_form)
        self.assertIn("systemctl restart yeetmouse.service", long_form)

    def test_uhid_problems_are_shown_up_front(self) -> None:
        window = self.make_window()
        self.assertEqual(window.mouse_panel.status_label.text(), "Stopped")
        self.assertTrue(window.mouse_panel.toggle_button.isEnabled())
        self.assertEqual(window.setup_problems(), [])

        self.uhid.unlink()
        missing = self.make_window()
        mouse = missing.mouse_panel
        self.assertEqual(mouse.status_label.text(),
                         "Stopped: /dev/uhid is missing (Help > Setup help)")
        self.assertFalse(mouse.toggle_button.isEnabled())  # cannot work
        self.assertTrue(mouse.message.isHidden())           # no extra line in the layout
        self.assertIn("modprobe uhid", " ".join(missing.setup_problems()))
        self.assertFalse(missing.steps_bar.next_button.isEnabled())
        self.assertIn("/dev/uhid is missing", missing.steps_bar.next_label.text())

        if os.geteuid() != 0:
            self.uhid.write_bytes(b"")
            self.uhid.chmod(0)
            denied = self.make_window()
            self.assertIn("not writable", denied.mouse_panel.status_label.text())
            problems = " ".join(denied.setup_problems())
            self.assertIn("99-quadsens.rules", problems)
            self.assertIn("group input", problems)
            self.uhid.chmod(0o644)  # fixed while the window was open
            denied.mouse_panel.check_access()
            self.assertEqual(denied.mouse_panel.status_label.text(), "Stopped")
            self.assertTrue(denied.mouse_panel.toggle_button.isEnabled())

    # ---- log ----------------------------------------------------------------

    def feed_write(self, window: MainWindow, value: float = 1.5) -> None:
        device = emulator.BragiDevice(log=window.mouse_panel._on_entry)
        device.handle(bytes([0, 1, 0, emulator.CMD_SET]) + struct.pack("<Hf", 0x125, value))

    def test_directional_writes_stand_out_without_relying_on_colour(self) -> None:
        window = self.make_window()
        mouse = window.mouse_panel
        font = mouse.log_view.font()
        self.assertTrue(font.fixedPitch() or font.styleHint() == QtGui.QFont.StyleHint.Monospace)
        mouse._on_entry({"t": 0.0, "event": "plain"})
        self.feed_write(window)
        _app.processEvents()
        document = mouse.log_view.document()
        marked = plain = 0
        block = document.begin()
        while block.isValid():
            formats = block.layout().formats()
            if block.text().startswith(WRITE_PREFIX):
                marked += 1
                self.assertEqual(len(formats), 1)
                self.assertEqual(formats[0].length, len(block.text()))
                self.assertEqual(formats[0].format.fontWeight(), QtGui.QFont.Weight.Bold)
            else:
                plain += 1
                self.assertEqual(len(formats), 0)
            block = block.next()
        self.assertEqual((marked, plain), (1, 2))

    def test_write_colours_follow_the_palette(self) -> None:
        role = QtGui.QPalette.ColorRole
        for base, text, accent in (("#ffffff", "#000000", "#3daee9"),
                                   ("#1b1e20", "#e6e6e6", "#3daee9"),
                                   ("#000000", "#ffffff", "#ffff00"),
                                   ("#fdf6e3", "#333333", "#b58900")):
            palette = QtGui.QPalette()
            palette.setColor(role.Base, QtGui.QColor(base))
            palette.setColor(role.Text, QtGui.QColor(text))
            palette.setColor(role.Highlight, QtGui.QColor(accent))
            background, foreground = write_line_colors(palette)
            self.assertNotEqual(background.name(), QtGui.QColor(base).name())
            self.assertEqual(foreground.name(), QtGui.QColor(text).name())
            self.assertGreaterEqual(_contrast(background, foreground), 4.5, (base, accent))

    def test_copy_and_clear_log(self) -> None:
        window = self.make_window()
        mouse = window.mouse_panel
        self.assertFalse(mouse.copy_button.isEnabled())
        self.assertFalse(mouse.clear_button.isEnabled())
        self.feed_write(window)
        self.assertTrue(mouse.copy_button.isEnabled())
        mouse.copy_button.click()
        self.assertEqual(QtWidgets.QApplication.clipboard().text(),
                         mouse.log_view.toPlainText())
        self.assertIn(WRITE_PREFIX, QtWidgets.QApplication.clipboard().text())
        self.assertIn("Copied 2 lines", mouse.follow_label.text())
        mouse.clear_button.click()
        self.assertEqual(mouse.log_view.toPlainText(), "")
        self.assertFalse(mouse.clear_button.isEnabled())
        # The values shown under Calibration are not part of the log.
        self.assertEqual(window.calibration_panel.emulator_labels["x_pos"].text(), "1.500000")

    def test_auto_scroll_pauses_when_scrolled_up(self) -> None:
        window = self.make_window()
        window.resize(900, 660)
        window.mouse_panel.log_toggle.setChecked(True)
        window.show()
        _app.processEvents()
        mouse = window.mouse_panel
        bar = mouse.log_view.verticalScrollBar()
        for index in range(120):
            mouse._on_entry({"t": 0.0, "event": "line%d" % index})
        _app.processEvents()
        self.assertGreater(bar.maximum(), 0)
        self.assertEqual(bar.value(), bar.maximum())
        self.assertTrue(mouse.follows_end())
        self.assertIn("on", mouse.follow_label.text())

        bar.setValue(10)  # the user scrolls up
        self.assertFalse(mouse.follows_end())
        self.assertIn("paused", mouse.follow_label.text())
        for index in range(50):
            mouse._on_entry({"t": 0.0, "event": "more%d" % index})
        _app.processEvents()
        self.assertEqual(bar.value(), 10)
        self.assertLess(bar.value(), bar.maximum())

        for index in range(400):  # old lines are dropped: the view stays on its text
            mouse._on_entry({"t": 0.0, "event": "late%d" % index})
        self.assertEqual(mouse.log_view.blockCount(), 500)
        self.assertEqual(bar.value(), 0)
        self.assertFalse(mouse.follows_end())

        bar.setValue(bar.maximum())  # back at the end: following again
        self.assertTrue(mouse.follows_end())
        mouse._on_entry({"t": 0.0, "event": "last"})
        self.assertEqual(bar.value(), bar.maximum())

    # ---- driver panel -------------------------------------------------------

    def test_unsaved_changes_are_marked_and_gate_apply(self) -> None:
        window = self.make_window()
        drv = window.driver_panel
        self.assertFalse(drv.has_unsaved_changes())
        self.assertFalse(drv.apply_button.isEnabled())
        self.assertIn("No unsaved changes", drv.pending_label.text())
        self.assertEqual([mark.text() for mark in drv.change_marks], [""] * 4)

        drv.spins[2].setValue(1.125)
        self.assertEqual(drv.changed_directions(), ["down"])
        self.assertTrue(drv.apply_button.isEnabled())
        self.assertEqual([mark.text() for mark in drv.change_marks],
                         ["", "", CHANGED_MARK, ""])
        self.assertIn("Unsaved changes: down", drv.pending_label.text())
        self.assertTrue(drv.pending_label.text().startswith(CHANGED_MARK))
        self.assertTrue(drv.spins[2].font().bold())
        self.assertFalse(drv.spins[0].font().bold())

        drv.spins[2].setValue(1.0)  # edited back: nothing to write
        self.assertFalse(drv.apply_button.isEnabled())

        drv.spins[0].setValue(1.05)
        drv.apply_button.click()
        self.assertEqual((self.params / "DirScaleXPos").read_text(), "1.050000\n")
        self.assertFalse(drv.has_unsaved_changes())
        self.assertFalse(drv.apply_button.isEnabled())
        self.assertIn("Applied directly", drv.message.text())
        self.assertFalse(drv.message.is_error)

        (self.params / "DirScaleXPos").write_text("1.000000\n")  # module reloaded
        drv.read_button.click()
        self.assertEqual(drv.changed_directions(), ["right"])
        self.assertTrue(drv.apply_button.isEnabled())

    def test_directions_are_spelled_out(self) -> None:
        window = self.make_window()
        drv = window.driver_panel
        labels = [w.text() for w in drv.findChildren(QtWidgets.QLabel) if w.buddy() is not None]
        self.assertEqual(labels, ["→ &Right:", "← &Left:", "↓ &Down:", "↑ &Up:"])
        for spin, name, direction in zip(drv.spins, PARAM_NAMES,
                                         ("right", "left", "down", "up")):
            self.assertEqual(spin.accessibleName(), f"New value for {name} ({direction})")
            self.assertIn(name, spin.toolTip())
        self.assertIn("Save to config", drv.apply_button.toolTip())
        self.assertIn("until the yeetmouse module is reloaded", drv.apply_button.toolTip())

    def test_values_round_trip_in_a_comma_locale(self) -> None:
        previous = QtCore.QLocale()
        german = QtCore.QLocale(QtCore.QLocale.Language.German, QtCore.QLocale.Country.Germany)
        QtCore.QLocale.setDefault(german)
        self.addCleanup(QtCore.QLocale.setDefault, previous)
        stock = QtWidgets.QDoubleSpinBox()
        stock.setDecimals(4)
        stock.setValue(1.0375)
        self.assertEqual(stock.text(), "1,0375")  # the locale is in effect

        window = self.make_window()
        drv = window.driver_panel
        self.assertIsInstance(drv.spins[0], ScaleSpinBox)
        self.assertEqual(drv.spins[0].text(), "1.0000")
        drv.use_button.click()
        self.assertEqual([spin.text() for spin in drv.spins],
                         ["1.0375", "0.9625", "1.1250", "0.8750"])
        self.assertEqual(drv.values(), Scalars(1.0375, 0.9625, 1.125, 0.875))

        def typed(spin: ScaleSpinBox, text: str) -> float:
            spin.lineEdit().setText(text)
            spin.interpretText()
            return round(spin.value(), 4)

        spin = drv.spins[1]
        self.assertEqual(typed(spin, "0,9500"), 0.95)   # comma, as the keyboard gives it
        self.assertEqual(spin.text(), "0.9500")
        self.assertEqual(typed(spin, "0.9625"), 0.9625)  # dot is not a thousands separator
        self.assertEqual(typed(spin, "1,5"), 1.5)
        self.assertEqual(typed(spin, "1.000,5"), 1.5)    # ambiguous input: value kept
        self.assertEqual(typed(spin, "0.05"), 1.5)       # below the range: value kept
        self.assertEqual(typed(spin, "0.9625"), 0.9625)
        state = QtGui.QValidator.State
        for text, expected in (("1,0375", state.Acceptable), ("1.0375", state.Acceptable),
                               ("", state.Intermediate), ("0,0", state.Intermediate),
                               ("1.03755", state.Invalid), ("11", state.Invalid),
                               ("1e1", state.Invalid), ("-1", state.Invalid)):
            self.assertEqual(spin.validate(text, 0)[0], expected, text)
        spin.stepBy(1)
        self.assertEqual(spin.text(), "0.9635")
        spin.stepBy(-1)

        drv.apply_button.click()
        self.assertEqual([(self.params / name).read_text() for name in PARAM_NAMES],
                         ["1.037500\n", "0.962500\n", "1.125000\n", "0.875000\n"])
        self.assertEqual([label.text() for label in drv.current_labels],
                         ["1.037500", "0.962500", "1.125000", "0.875000"])
        self.assertFalse(drv.has_unsaved_changes())
        window.close()
        again = self.make_window()  # saved values survive the settings file as well
        self.assertEqual(again.driver_panel.saved_scalars(),
                         Scalars(1.0375, 0.9625, 1.125, 0.875))
        self.assertEqual(again.driver_panel.spins[0].text(), "1.0375")

    # ---- guided steps -------------------------------------------------------

    def statuses(self, window: MainWindow) -> str:
        return "".join(chip.text()[0] for chip in window.steps_bar.chips)

    def wait_idle(self, window: MainWindow) -> None:
        deadline = time.monotonic() + 10
        while window.driver_panel.is_busy() and time.monotonic() < deadline:
            _app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
        self.assertFalse(window.driver_panel.is_busy())

    def new_result(self, window: MainWindow) -> None:
        """The game writes a result now."""
        stamp = time.time() + 0.01
        os.utime(self.cal_file, (stamp, stamp))
        window.calibration_panel.refresh()

    def test_steps_walk_through(self) -> None:
        for name in PARAM_NAMES:  # an older calibration is still applied
            (self.params / name).write_text("0.990000\n")
        window = self.make_window()
        window.mouse_panel._factory = lambda pid, on_log: FakeSession(pid)
        bar = window.steps_bar
        self.assertEqual(self.statuses(window), "▶○○○○○")
        self.assertEqual(bar.chips[0].text(), "▶ 1 Start mouse")
        self.assertTrue(bar.chips[0].is_current())
        self.assertFalse(bar.chips[1].is_current())
        self.assertIn("Step 1 of 6", bar.next_label.text())
        self.assertEqual(bar.next_button.text(), "Start virtual mouse")
        self.assertTrue(bar.next_button.isDefault())
        self.assertIn("pending", bar.chips[5].accessibleName())

        bar.next_button.click()
        self.assertTrue(window.mouse_panel.is_running())
        self.assertEqual(self.statuses(window), "✓▶○○○○")
        self.assertEqual(bar.next_button.text(), "Reset to 1.0 and apply")

        bar.next_button.click()  # one click: reset and apply
        self.assertEqual([(self.params / name).read_text() for name in PARAM_NAMES],
                         ["1.000000\n"] * 4)
        self.assertEqual(self.statuses(window), "✓✓▶○○○")
        self.assertEqual(bar.next_button.text(), "Waiting for the result")
        self.assertFalse(bar.next_button.isEnabled())  # this step happens in the game
        self.assertIn("calibration in the game", bar.next_label.text())

        self.new_result(window)
        self.assertEqual(self.statuses(window), "✓✓✓▶○○")
        self.assertEqual(bar.next_button.text(), "Use result")
        bar.next_button.click()
        self.assertEqual(window.driver_panel.values(), Scalars(1.0375, 0.9625, 1.125, 0.875))
        self.assertEqual(self.statuses(window), "✓✓✓✓▶○")
        self.assertEqual(bar.next_button.text(), "Apply")
        bar.next_button.click()
        self.assertEqual((self.params / "DirScaleYNeg").read_text(), "0.875000\n")
        self.assertEqual(self.statuses(window), "✓✓✓✓✓▶")
        self.assertEqual(bar.next_button.text(), "Save to config")
        self.assertIn("lost on module reload", bar.next_label.text())
        bar.next_button.click()
        self.wait_idle(window)
        self.assertEqual(self.statuses(window), "✓✓✓✓✓✓")
        self.assertIn("All six steps are done", bar.next_label.text())
        self.assertFalse(bar.next_button.isEnabled())
        self.assertIn("dirScaleYNeg=0.875", self.config.read_text())

        # Calibrating again: after the reset the old result is not "new" any more.
        time.sleep(0.05)
        window.driver_panel.reset_and_apply()
        self.assertEqual(self.statuses(window), "✓✓▶○○○")

    def test_steps_notice_values_sent_to_the_virtual_mouse(self) -> None:
        self.cal_file.unlink()  # no result file at all
        window = self.make_window()
        window.mouse_panel._factory = lambda pid, on_log: FakeSession(pid)
        window.run_step(Step.START)
        self.assertEqual(self.statuses(window), "✓✓▶○○○")
        device = emulator.BragiDevice(log=window.mouse_panel._on_entry)
        for prop, value in ((0x125, 1.5), (0x126, 0.75), (0x127, 1.25)):
            device.handle(bytes([0, 1, 0, emulator.CMD_SET]) + struct.pack("<Hf", prop, value))
        self.assertEqual(self.statuses(window), "✓✓▶○○○")  # three of four: not yet
        device.handle(bytes([0, 1, 0, emulator.CMD_SET]) + struct.pack("<Hf", 0x128, 0.5))
        self.assertEqual(self.statuses(window), "✓✓✓▶○○")
        self.assertEqual(window.calibration_panel.diagram.scalars(),
                         Scalars(1.5, 0.75, 1.25, 0.5))
        window.run_step(Step.USE)
        self.assertEqual(window.driver_panel.values(), Scalars(1.5, 0.75, 1.25, 0.5))
        self.assertIn("sent to the virtual mouse", window.driver_panel.message.text())

    def test_steps_do_not_lock_anything(self) -> None:
        window = self.make_window()  # mouse stopped, an older result file, driver at 1.0
        drv = window.driver_panel
        self.assertEqual(self.statuses(window), "▶✓○○○○")
        self.assertTrue(drv.use_button.isEnabled())
        drv.use_button.click()  # out of order, straight to the old result
        self.assertEqual(self.statuses(window), "✓✓✓✓▶○")
        drv.apply_button.click()
        self.assertEqual(self.statuses(window), "✓✓✓✓✓▶")
        drv.spins[0].setValue(1.2)  # editing by hand leaves the result behind
        self.assertEqual(self.statuses(window)[3], "✓")
        self.assertEqual(self.statuses(window)[5], "▶")

    def test_step_actions_are_blocked_only_when_impossible(self) -> None:
        shutil.rmtree(self.params.parent)  # module not loaded
        window = self.make_window()
        window.mouse_panel._factory = lambda pid, on_log: FakeSession(pid)
        window.run_step(Step.START)
        bar = window.steps_bar
        # Resetting cannot work without the module; the button leads to the fix instead.
        self.assertEqual(bar.next_button.text(), "Driver setup…")
        self.assertTrue(bar.next_button.isEnabled())
        self.assertIn("not loaded", bar.next_label.text())
        self.assertEqual(window.steps_bar.infos[1].fix, "driver-setup")
        opened: list[str] = []
        window.show_driver_setup = lambda: opened.append("setup")  # type: ignore[method-assign]
        window.steps_bar.fix_requested.disconnect()
        window.steps_bar.fix_requested.connect(lambda _fix: window.show_driver_setup())
        bar.next_button.click()
        self.assertEqual(opened, ["setup"])
        self.assertEqual(len(bar.chips), 6)   # the stepper did not get longer

    def test_primary_button_has_a_shortcut_and_names(self) -> None:
        window = self.make_window()
        button = window.steps_bar.next_button
        self.assertEqual(button.shortcut().toString(), "Ctrl+Return")
        self.assertEqual(button.accessibleName(), "Next step: Start virtual mouse")
        self.assertTrue(button.font().bold())
        self.assertIs(window.tab_order[0], button)

    # ---- save to config -----------------------------------------------------

    def test_save_to_config(self) -> None:
        window = self.make_window()
        drv = window.driver_panel
        self.assertIs(drv.save_state, SaveState.MISSING_KEYS)
        self.assertIn("no directional values", drv.saved_label.text())
        drv.use_button.click()
        drv.apply_button.click()
        self.assertTrue(drv.save_button.isEnabled())
        drv.save_button.click()
        self.assertFalse(drv.save_button.isEnabled())  # one save at a time
        self.wait_idle(window)
        self.assertIs(drv.save_state, SaveState.SAVED)
        self.assertEqual(drv.saved_label.text(), "✓ Saved to config.")
        self.assertTrue(drv.message.text().startswith("✓ Saved to "))
        self.assertFalse(drv.message.is_error)
        self.assertEqual(self.saver.read_saved(), Scalars(1.0375, 0.9625, 1.125, 0.875))

        drv.spins[0].setValue(1.05)
        drv.apply_button.click()  # applied again, the file still has the old values
        self.assertIs(drv.save_state, SaveState.DIFFERS)
        self.assertEqual(drv.saved_label.text(),
                         "⚠ Applied, not saved: lost on module reload.")

        self.config.write_text("sens=1\ndirScaleXPos=1.05\ndirScaleXNeg=0.9625\n"
                               "dirScaleYPos=1.125\ndirScaleYNeg=0.875\n")
        drv.read_button.click()  # saved by other means: picked up on reading again
        self.assertIs(drv.save_state, SaveState.SAVED)

    def test_save_with_the_unmodified_tool_is_not_believed(self) -> None:
        os.environ["QUADSENS_FAKE_MODE"] = "stock"
        window = self.make_window()
        drv = window.driver_panel
        drv.use_button.click()
        drv.apply_button.click()
        drv.save_button.click()
        self.wait_idle(window)
        self.assertIs(drv.save_state, SaveState.MISSING_KEYS)
        self.assertTrue(drv.message.is_error)
        self.assertIn("Nothing directional was saved", drv.message.text())
        self.assertIn("not the fork's", drv.message.text())
        self.assertNotIn("✓", drv.saved_label.text())
        self.assertEqual(self.statuses(window)[5], "▶")

    def test_failed_save_is_reported(self) -> None:
        os.environ["QUADSENS_FAKE_MODE"] = "fail"
        window = self.make_window()
        drv = window.driver_panel
        drv.save_button.click()
        self.wait_idle(window)
        self.assertTrue(drv.message.is_error)
        self.assertIn("exited with status 3", drv.message.text())
        self.assertIn("cannot open config", drv.message.text())
        self.assertTrue(drv.save_button.isEnabled())

    def test_save_is_disabled_with_a_hint_when_the_tool_is_missing(self) -> None:
        empty = self.tmp / "no-tools"
        empty.mkdir()
        self.saver = Saver(self.config, trusted_dirs=(str(empty),))
        window = self.make_window()
        drv = window.driver_panel
        self.assertFalse(drv.save_button.isEnabled())
        self.assertIn("was not found", drv.save_button.toolTip())
        self.assertIn("was not found", drv.save_button.accessibleDescription())
        self.assertIn("Cannot save", drv.saved_label.text())
        self.assertIn("yeetmousectl", " ".join(window.setup_problems()))
        drv.use_button.click()
        drv.apply_button.click()
        save = window.steps_bar.infos[5]
        self.assertIn("was not found", save.blocked)
        self.assertFalse(window.steps_bar.next_button.isEnabled())

    # ---- advanced, log and diagram -------------------------------------------

    def test_flip_y_is_tucked_away_and_off_by_default(self) -> None:
        window = self.make_window()
        drv = window.driver_panel
        self.assertFalse(drv.flip_check.isChecked())
        self.assertFalse(drv.advanced_toggle.isChecked())
        self.assertTrue(drv.advanced.isHidden())
        self.assertIn("verified", drv.flip_hint.text())
        self.assertIn("verified", drv.flip_check.toolTip())
        drv.advanced_toggle.click()
        self.assertFalse(drv.advanced.isHidden())
        self.assertEqual(window.calibration_panel.diagram.scalars(),
                         Scalars(1.0375, 0.9625, 1.125, 0.875))
        drv.flip_check.setChecked(True)
        self.assertEqual(window.calibration_panel.diagram.scalars(),
                         Scalars(1.0375, 0.9625, 0.875, 1.125))
        window.close()

        again = self.make_window()  # the stored setting keeps working and is not hidden
        self.assertTrue(again.driver_panel.flip_check.isChecked())
        self.assertTrue(again.driver_panel.advanced_toggle.isChecked())
        self.assertFalse(again.driver_panel.advanced.isHidden())
        self.assertEqual(again.calibration_panel.diagram.scalars(),
                         Scalars(1.0375, 0.9625, 0.875, 1.125))

    def test_protocol_log_is_collapsed_and_summarised(self) -> None:
        window = self.make_window()
        mouse = window.mouse_panel
        self.assertFalse(mouse.log_shown())
        self.assertTrue(mouse.log_box.isHidden())
        self.assertEqual(mouse.summary_label.text(), "No events yet.")
        mouse._on_entry({"t": 0.0, "event": "OPEN"})
        self.assertTrue(mouse.summary_label.text().endswith("opened by a client"))
        mouse._on_entry({"t": 0.0, "cmd": "GET", "id": 19})  # chatter: summary stays
        self.assertTrue(mouse.summary_label.text().endswith("opened by a client"))
        self.feed_write(window, 1.0375)
        self.assertTrue(mouse.summary_label.text().endswith(
            "calibration value received: XPos = 1.0375"))
        self.assertEqual(mouse.log_view.blockCount(), 4)  # collected while hidden

        mouse.log_toggle.click()
        self.assertFalse(mouse.log_box.isHidden())
        window.close()
        again = self.make_window()  # remembered
        self.assertTrue(again.mouse_panel.log_shown())
        self.assertFalse(again.mouse_panel.log_box.isHidden())

    def test_diagram_shows_the_four_multipliers_with_deviation(self) -> None:
        window = self.make_window()
        cal = window.calibration_panel
        description = cal.diagram.accessibleDescription()
        for expected in ("Right 1.0375, 3.8 % faster than 1.0",
                         "Left 0.9625, 3.8 % slower than 1.0",
                         "Down 1.1250, 12.5 % faster than 1.0",
                         "Up 0.8750, 12.5 % slower than 1.0",
                         "measured: E 1.0400", "SE 0.9500"):
            self.assertIn(expected, description)
        shape = cal.diagram.shape()
        assert shape is not None
        self.assertEqual([arrow.name for arrow in shape.arrows],
                         ["Right", "Left", "Down", "Up"])
        self.assertIn("Centre = 0.5, dashed ring = 1.0", shape.caption)
        self.assertIn("Scale: centre 0.5, dashed ring 1.0, edge 1.5", description)
        self.assertIn("faster", cal.diagram.toolTip())
        self.assertIn("Outside the dashed ring", cal.diagram.toolTip())
        self.assertEqual((cal.diagram.minimumSize().width(),
                          cal.diagram.minimumSize().height()), (230, 150))
        self.assertTrue(cal.details.isHidden())  # the raw numbers are secondary
        cal.details_toggle.click()
        self.assertFalse(cal.details.isHidden())
        self.assertIn("NE 0.990", cal.directional_label.text())
        window.show()
        _app.processEvents()
        self.assertFalse(cal.diagram.grab().isNull())  # painting does not raise

        drv = window.driver_panel
        self.assertEqual([label.text() for label in drv.deviation_labels], ["0.0 %"] * 4)
        drv.use_button.click()
        drv.apply_button.click()
        self.assertEqual([label.text() for label in drv.deviation_labels],
                         ["+3.8 %", "-3.8 %", "+12.5 %", "-12.5 %"])

    def test_diagram_zoom_is_chosen_in_the_view_menu_and_remembered(self) -> None:
        window = self.make_window()
        diagram = window.calibration_panel.diagram
        self.assertEqual(window.zoom_menu.title(), "Diagram &zoom")
        self.assertEqual([action.text() for action in window.zoom_actions.values()],
                         ["Centre at 0.5", "Centre at 0.9", "Centre at 0.95"])
        self.assertTrue(window.zoom_group.isExclusive())
        self.assertEqual([a.isChecked() for a in window.zoom_actions.values()],
                         [True, False, False])
        self.assertEqual(diagram.zoom(), 0.5)
        right = diagram.shape().arrows[0]
        self.assertAlmostEqual(right.radius, 1.075)   # 1.0375 with the centre at 0.5
        self.assertFalse(right.clamped)

        window.zoom_actions[0.95].trigger()
        self.assertEqual(diagram.zoom(), 0.95)
        shape = diagram.shape()
        self.assertEqual(shape.centre_label, "0.95")
        self.assertAlmostEqual(shape.arrows[0].radius, 1.75)
        self.assertIn("Centre = 0.95", shape.caption)
        self.assertIn("Scale: centre 0.95", diagram.accessibleDescription())
        self.assertEqual([a.isChecked() for a in window.zoom_actions.values()],
                         [False, False, True])
        window.show()
        _app.processEvents()
        self.assertFalse(diagram.grab().isNull())
        window.close()

        again = self.make_window()   # remembered
        self.assertEqual(again.calibration_panel.diagram.zoom(), 0.95)
        self.assertTrue(again.zoom_actions[0.95].isChecked())
        again.zoom_actions[0.9].trigger()
        self.assertEqual(again.calibration_panel.diagram.zoom(), 0.9)
        again.zoom_actions[0.5].trigger()
        self.assertEqual(again.calibration_panel.diagram.zoom(), 0.5)

    def test_hints_and_messages_do_not_grow_the_window(self) -> None:
        # Worst case of problems: no uhid, module not loaded, no result, no tool.
        self.uhid.unlink()
        shutil.rmtree(self.params.parent)
        shutil.rmtree(self.tmp / "Users")
        empty = self.tmp / "no-tools"
        empty.mkdir()
        self.saver = Saver(self.config, trusted_dirs=(str(empty),))
        broken = self.make_window()
        broken.resize(720, 520)
        broken.show()
        for _ in range(5):
            _app.processEvents()
        hint = broken.minimumSizeHint()
        self.assertLessEqual(hint.width(), 720, hint)
        self.assertLessEqual(hint.height(), 520, hint)
        broken.close()

        # And the busiest good case: applied and saved, with both messages showing.
        self.setUp()
        window = self.make_window()
        window.resize(720, 520)
        window.show()
        window.driver_panel.use_button.click()
        window.driver_panel.apply_button.click()
        window.driver_panel.save_button.click()
        self.wait_idle(window)
        for _ in range(5):
            _app.processEvents()
        hint = window.minimumSizeHint()
        self.assertLessEqual(hint.width(), 720, hint)
        self.assertLessEqual(hint.height(), 520, hint)

    # ---- look ---------------------------------------------------------------

    def dark_palette(self) -> QtGui.QPalette:
        role = QtGui.QPalette.ColorRole
        palette = QtGui.QPalette()
        for name, color in ((role.Window, "#2a2e32"), (role.WindowText, "#e6e6e6"),
                            (role.Base, "#1b1e20"), (role.Text, "#e6e6e6"),
                            (role.Button, "#31363b"), (role.ButtonText, "#e6e6e6"),
                            (role.Highlight, "#3daee9"), (role.HighlightedText, "#ffffff")):
            palette.setColor(name, QtGui.QColor(color))
        return palette

    def use_theme(self, mode: str) -> ThemeManager:
        """Switch the theme for this test; the default comes back afterwards."""
        manager = ThemeManager.instance()
        self.addCleanup(manager.set_mode, "quadsens")
        manager.set_mode(mode)
        return manager

    def window_colour(self) -> str:
        return _app.palette().color(QtGui.QPalette.ColorRole.Window).name()

    def test_black_and_red_is_the_default(self) -> None:
        window = self.make_window()
        manager = ThemeManager.instance()
        self.assertEqual(manager.mode(), "quadsens")
        theme = current_theme()
        self.assertEqual(theme, colors.quadsens_theme())
        self.assertEqual((theme.window, theme.accent), ("#0b0b0d", "#d7263d"))
        self.assertEqual(theme.failures(), [])
        sheet = _app.styleSheet()
        for value in (theme.surface, theme.accent, theme.accent_line, theme.outline):
            self.assertIn(value, sheet)
        # The palette matches, so that menus, dialogs and tooltips are dark as well.
        role = QtGui.QPalette.ColorRole
        palette = _app.palette()
        self.assertEqual(palette.color(role.Window).name(), "#0b0b0d")
        self.assertEqual(palette.color(role.WindowText).name(), theme.text)
        self.assertEqual(palette.color(role.Base).name(), theme.base)
        self.assertEqual(palette.color(role.Highlight).name(), "#d7263d")
        self.assertEqual(palette.color(role.HighlightedText).name(), "#ffffff")
        self.assertEqual(palette.color(role.ToolTipText).name(), theme.text)
        self.assertEqual(palette.color(role.Link).name(), theme.accent_line)
        disabled = palette.color(QtGui.QPalette.ColorGroup.Disabled, role.Text).name()
        self.assertEqual(disabled, theme.disabled)
        for pair in ((role.WindowText, role.Window), (role.Text, role.Base),
                     (role.ButtonText, role.Button), (role.ToolTipText, role.ToolTipBase),
                     (role.HighlightedText, role.Highlight), (role.Link, role.Window)):
            ratio = colors.contrast(palette.color(pair[0]).name(), palette.color(pair[1]).name())
            self.assertGreaterEqual(ratio, 4.5, pair)
        window.about_action.trigger()   # dialogs take the palette
        box = window.info_box
        assert box is not None
        self.assertEqual(box.palette().color(role.Window).name(), "#0b0b0d")
        box.close()

    def test_black_and_red_ignores_the_desktop_scheme(self) -> None:
        self.make_window()
        before = _app.styleSheet()
        light = QtGui.QPalette()
        light.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#efefef"))
        light.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor("#202020"))
        _app.setPalette(light)   # the desktop switches to a light scheme
        for _ in range(5):
            _app.processEvents()
        self.assertEqual(self.window_colour(), "#0b0b0d")
        self.assertEqual(_app.styleSheet(), before)
        self.assertEqual(current_theme().window, "#0b0b0d")

    def test_follow_system_colours_generates_from_the_palette(self) -> None:
        self.make_window()
        manager = self.use_theme("system")
        self.assertEqual(manager.mode(), "system")
        sheet = _app.styleSheet()
        theme = current_theme()
        self.assertNotEqual(theme.window, "#0b0b0d")
        self.assertEqual(theme.window, self.window_colour())
        for value in (theme.surface, theme.accent, theme.outline, theme.focus,
                      theme.warning_bg):
            self.assertIn(value, sheet)
        self.assertEqual(theme.failures(), [])
        self.assertIn("border-radius: 6px", sheet)
        self.assertNotIn("transition", sheet)  # nothing animates
        self.assertNotIn("animation", sheet)

    def test_follow_system_colours_tracks_a_palette_change(self) -> None:
        self.make_window()
        self.use_theme("system")
        before = _app.styleSheet()
        self.addCleanup(_app.setPalette, QtGui.QPalette())
        _app.setPalette(self.dark_palette())
        for _ in range(5):
            _app.processEvents()
        theme = current_theme()
        self.assertTrue(theme.dark)
        self.assertEqual(theme.window, "#2a2e32")
        self.assertNotEqual(_app.styleSheet(), before)
        self.assertIn(theme.surface, _app.styleSheet())
        self.assertEqual(theme.failures(), [])

    def test_every_focusable_control_keeps_a_focus_ring(self) -> None:
        self.make_window()
        sheet = _app.styleSheet()
        theme = current_theme()
        ring = "border: 2px solid %s" % theme.focus
        for selector in ("QPushButton:focus", "QToolButton:focus", "QCheckBox:focus",
                         "QPlainTextEdit:focus",
                         "QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus"):
            start = sheet.index(selector + " {")
            rule = sheet[start:sheet.index("}", start)]
            self.assertIn(ring, rule, selector)
        start = sheet.index('QPushButton[primary="true"]:focus {')
        self.assertIn("border: 2px solid %s" % theme.focus_on_accent,
                      sheet[start:sheet.index("}", start)])
        self.assertNotIn("outline: none", sheet)

    def test_hit_targets_are_at_least_28_px_high(self) -> None:
        window = self.make_window()
        window.driver_panel.advanced_toggle.setChecked(True)
        window.mouse_panel.log_toggle.setChecked(True)
        window.resize(860, 760)
        window.show()
        self.feed_write(window)
        for _ in range(5):
            _app.processEvents()
        self.assertEqual(CONTROL_HEIGHT, 28)
        kinds = (QtWidgets.QAbstractButton, QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox,
                 QtWidgets.QLineEdit)
        checked = 0
        for widget in window.centralWidget().findChildren(QtWidgets.QWidget):
            if (isinstance(widget, kinds) and widget.isVisible()
                    and not widget.objectName().startswith("qt_")):
                checked += 1
                self.assertGreaterEqual(widget.height(), 28, repr(widget))
        self.assertGreaterEqual(checked, 18)

    def test_primary_style_marks_the_current_step(self) -> None:
        for name in PARAM_NAMES:
            (self.params / name).write_text("0.990000\n")
        window = self.make_window()
        window.mouse_panel._factory = lambda pid, on_log: FakeSession(pid)
        mouse, drv = window.mouse_panel, window.driver_panel

        def primaries() -> list[str]:
            return [button.text().replace("&", "") for button in window._step_buttons()
                    if button.property("primary")]

        self.assertTrue(window.steps_bar.next_button.property("primary"))
        self.assertEqual(primaries(), ["Start virtual mouse"])
        mouse.toggle_button.click()
        self.assertEqual(primaries(), ["Reset to 1.0"])   # first press in the card
        drv.reset_button.click()
        self.assertEqual(primaries(), ["Apply"])          # then the second
        drv.apply_button.click()
        self.assertEqual(primaries(), [])                 # calibrating happens in the game
        self.new_result(window)
        self.assertEqual(primaries(), ["Use result"])
        drv.use_button.click()
        self.assertEqual(primaries(), ["Apply"])
        drv.apply_button.click()
        self.assertEqual(primaries(), ["Save to config"])
        self.assertFalse(drv.read_button.property("primary"))

    def test_theme_menu_switches_at_once_and_is_remembered(self) -> None:
        window = self.make_window()
        manager = ThemeManager.instance()
        self.addCleanup(manager.set_mode, "quadsens")
        actions = window.theme_actions
        self.assertEqual(list(actions), ["quadsens", "system", "native"])
        self.assertEqual([action.text() for action in actions.values()],
                         ["&QuadSens (black and red)", "&Follow system colours",
                          "&System style"])
        self.assertEqual(window.theme_menu.title(), "&Theme")
        self.assertTrue(window.theme_group.isExclusive())
        self.assertEqual([a.isChecked() for a in actions.values()], [True, False, False])
        black_sheet = _app.styleSheet()

        actions["system"].trigger()
        self.assertEqual(manager.mode(), "system")
        self.assertEqual([a.isChecked() for a in actions.values()], [False, True, False])
        self.assertNotEqual(self.window_colour(), "#0b0b0d")   # the system's colours again
        self.assertNotEqual(_app.styleSheet(), black_sheet)
        self.assertTrue(_app.styleSheet())
        window.close()
        again = self.make_window()   # remembered
        self.assertEqual(manager.mode(), "system")
        self.assertTrue(again.theme_actions["system"].isChecked())

        again.theme_actions["native"].trigger()
        self.assertTrue(manager.is_native())
        self.assertEqual(_app.styleSheet(), "")
        self.assertNotEqual(self.window_colour(), "#0b0b0d")
        hint = again.minimumSizeHint()  # the plain style fits as well
        self.assertLessEqual((hint.width(), hint.height()), (720, 520))

        again.theme_actions["quadsens"].trigger()
        self.assertEqual(manager.mode(), "quadsens")
        self.assertEqual(_app.styleSheet(), black_sheet)
        self.assertEqual(self.window_colour(), "#0b0b0d")
        settings = QtCore.QSettings(str(self.tmp / "settings.ini"),
                                    QtCore.QSettings.Format.IniFormat)
        self.assertEqual(settings.value("ui/theme"), "quadsens")

    def test_switching_themes_survives_palette_changes_and_closed_windows(self) -> None:
        # Qt crashes when an application style sheet is updated after the palette
        # changed under it and a widget has gone since. The sheet is therefore
        # replaced, never updated. This walks through a sequence of that kind; the
        # crash depended on the memory layout, so it is a smoke test, not a proof.
        manager = ThemeManager.instance()
        self.addCleanup(manager.set_mode, "quadsens")
        self.addCleanup(_app.setPalette, QtGui.QPalette())
        sheets = set()
        for round_ in range(6):
            window = self.make_window()
            window.show()
            self.feed_write(window)
            manager.set_mode(("system", "native", "quadsens")[round_ % 3])
            _app.setPalette(self.dark_palette() if round_ % 2 else QtGui.QPalette())
            for _ in range(3):
                _app.processEvents()
            self.windows.remove(window)
            window.close()
            window.deleteLater()
            _app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete.value)
            _app.processEvents()
            manager.set_mode("system")
            sheets.add(_app.styleSheet())
            manager.set_mode("quadsens")
            self.assertEqual(self.window_colour(), "#0b0b0d")
            self.assertIn("#d7263d", _app.styleSheet())
        self.assertGreaterEqual(len(sheets), 2)   # light and dark system sheets were built

    def test_old_system_style_setting_is_migrated(self) -> None:
        path = str(self.tmp / "old.ini")
        settings = QtCore.QSettings(path, QtCore.QSettings.Format.IniFormat)
        self.assertEqual(stored_mode(settings), "quadsens")        # nothing stored
        settings.setValue("ui/native_style", True)
        self.assertEqual(stored_mode(settings), "native")
        self.assertFalse(settings.contains("ui/native_style"))
        self.assertEqual(settings.value("ui/theme"), "native")
        settings.setValue("ui/theme", "nonsense")
        self.assertEqual(stored_mode(settings), "quadsens")
        other = QtCore.QSettings(str(self.tmp / "off.ini"), QtCore.QSettings.Format.IniFormat)
        other.setValue("ui/native_style", False)                    # the switch was off
        self.assertEqual(stored_mode(other), "quadsens")
        self.assertFalse(other.contains("ui/native_style"))

    def test_environment_overrides_the_theme(self) -> None:
        manager = ThemeManager.instance()
        self.addCleanup(manager.set_mode, "quadsens")
        with mock.patch.dict(os.environ, {"QUADSENS_NATIVE_STYLE": "1"}):
            self.assertEqual(env_mode(), "native")
            window = self.make_window()
            self.assertTrue(manager.is_native())
            self.assertEqual(_app.styleSheet(), "")
            self.assertTrue(window.theme_actions["native"].isChecked())
            self.assertFalse(any(a.isEnabled() for a in window.theme_actions.values()))
            with mock.patch.dict(os.environ, {"QUADSENS_THEME": "quadsens"}):
                self.assertEqual(env_mode(), "quadsens")            # the newer name wins
        with mock.patch.dict(os.environ, {"QUADSENS_THEME": "System"}):
            self.assertEqual(env_mode(), "system")
            window = self.make_window()
            self.assertEqual(manager.mode(), "system")
            self.assertTrue(window.theme_actions["system"].isChecked())
        for value in ("", "0", "purple"):
            with mock.patch.dict(os.environ, {"QUADSENS_THEME": value,
                                              "QUADSENS_NATIVE_STYLE": "0"}):
                self.assertIsNone(env_mode(), value)
        self.assertIsNone(env_mode())
        window = self.make_window()   # without the variable the stored choice counts
        self.assertEqual(manager.mode(), "quadsens")
        self.assertTrue(all(a.isEnabled() for a in window.theme_actions.values()))

    def test_banners_carry_a_mark_for_their_kind(self) -> None:
        window = self.make_window()
        drv = window.driver_panel
        self.assertEqual(drv.message.kind, "")       # empty: nothing shown
        self.assertEqual(drv.message.mark.text(), "")
        drv.use_button.click()
        self.assertEqual((drv.message.kind, drv.message.mark.text()), ("info", "ℹ"))
        drv.apply_button.click()
        self.assertEqual((drv.message.kind, drv.message.mark.text()), ("success", "✓"))
        self.assertTrue(drv.message.text().startswith("✓ Applied"))
        self.assertFalse(drv.message.label.text().startswith("✓"))  # the mark is not doubled
        (self.params / "update").unlink()
        drv.spins[0].setValue(1.3)
        drv.apply_button.click()
        self.assertEqual((drv.message.kind, drv.message.mark.text()), ("error", "✕"))
        self.assertTrue(drv.message.text().startswith("Error: "))
        self.assertEqual(drv.message.property("banner"), "error")
        shutil.rmtree(self.params.parent)
        drv.read_button.click()
        self.assertEqual((drv.status_label.kind, drv.status_label.mark.text()),
                         ("warning", "⚠"))

    def test_disclosures_share_one_chevron(self) -> None:
        window = self.make_window()
        toggles = (window.mouse_panel.log_toggle, window.calibration_panel.details_toggle,
                   window.driver_panel.advanced_toggle)
        for toggle in toggles:
            self.assertTrue(toggle.text().startswith("▸ "), toggle.text())
            toggle.click()
            self.assertTrue(toggle.text().startswith("▾ "), toggle.text())
            self.assertNotIn("▸", toggle.accessibleName())
            self.assertNotIn("&", toggle.accessibleName())
        self.assertEqual([toggle.label() for toggle in toggles],
                         ["Sho&w protocol log", "&Measured values", "Ad&vanced"])

    def test_nothing_animates(self) -> None:
        source = "".join(path.read_text(encoding="utf-8")
                         for path in (Path(__file__).parent.parent / "quadsens" / "ui").glob("*.py"))
        for name in ("QPropertyAnimation", "QVariantAnimation", "QTimeLine", "QMovie",
                     "setGraphicsEffect"):
            self.assertNotIn(name, source)


class DriverSetupDialogTest(unittest.TestCase):
    """The dialog against a fake machine; the script runs for real, without root."""

    def setUp(self) -> None:
        from tests.test_driver_setup import FakeSystem
        self.sys = FakeSystem(self)
        (self.sys.bin / "pkexec").write_text("")
        self.confirmations: list[tuple[str, str]] = []
        self.answer = True
        self.stub_fail = ""
        self.dialogs: list[DriverSetupDialog] = []

    def tearDown(self) -> None:
        for dialog in self.dialogs:
            self.wait(dialog)
            dialog.close()
            dialog.deleteLater()
        _app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete.value)
        _app.processEvents()

    def launcher(self, action: str, dry_run: bool = False, text: str | None = None,
                 **_kwargs: object) -> driver_setup.Launch:
        # As the real one, minus pkexec: the script still arrives on standard input.
        argv = ["/bin/sh", "-s", "--", *(["--dry-run"] if dry_run else []), action]
        return driver_setup.Launch(argv, text or driver_setup.script_text(), "test launch")

    def confirm(self, action: str, text: str) -> bool:
        self.confirmations.append((action, text))
        return self.answer

    def make(self) -> DriverSetupDialog:
        env = {"PATH": "/usr/bin:/bin", "QUADSENS_TEST_ROOT": str(self.sys.root),
               "QUADSENS_TEST_PATH": str(self.sys.bin),
               "QUADSENS_STUB_LOG": str(self.sys.log), "QUADSENS_STUB_FAIL": self.stub_fail,
               "PKEXEC_UID": "1000"}
        dialog = DriverSetupDialog(None, self.sys.probe(), self.launcher, self.confirm, env)
        self.dialogs.append(dialog)
        return dialog

    def wait(self, dialog: DriverSetupDialog) -> None:
        deadline = time.monotonic() + 20
        while dialog.is_busy() and time.monotonic() < deadline:
            _app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
        self.assertFalse(dialog.is_busy())

    def test_shows_state_prerequisites_script_and_changes(self) -> None:
        self.sys.stock()
        self.sys.userspace(fork_tool=False)
        self.sys.group()
        dialog = self.make()
        self.assertEqual(dialog.windowTitle(), "Driver setup")
        self.assertIn("2a3e21956ef5a2ddfc4523a7ad4ef8117f6f4913", dialog.source_label.text())
        self.assertIn("https://github.com/freasy/YeetMouse.git", dialog.source_label.text())
        state = dialog.state_label.text()
        self.assertIn("✕ Driver: the unmodified driver 0.9.5 is installed", state)
        self.assertIn("✕ Module: not loaded.", state)
        self.assertIn("✓ Group yeetmouse: your session is a member.", state)
        self.assertIn("ℹ No previous driver is kept", state)
        checks = dialog.checks_label.text().splitlines()
        self.assertEqual(len(checks), 9)
        self.assertTrue(all(line.startswith("✓ ") for line in checks))
        self.assertIn("moves its source from /usr/src to /usr/local/src",
                      dialog.install_changes.text())
        self.assertIn("Mouse acceleration is off for a moment",
                      dialog.install_changes.text())
        self.assertIn("no yeetmouse module is installed", dialog.revert_changes.text())
        # The script shown is the script that would be fed, byte for byte.
        shown = dialog.script_view.toPlainText()
        self.assertEqual(shown, driver_setup.script_text())
        self.assertTrue(dialog.script_view.isReadOnly())
        self.assertEqual([dialog.tabs.tabText(i) for i in range(dialog.tabs.count())],
                         ["O&verview", "&Script", "Out&put"])
        self.assertTrue(dialog.install_button.isEnabled())
        self.assertTrue(dialog.install_button.property("primary"))
        self.assertFalse(dialog.revert_button.isEnabled())       # nothing to revert
        self.assertIn("nothing to revert", dialog.revert_button.toolTip())

    def test_install_needs_the_prerequisites_and_a_confirmation(self) -> None:
        (self.sys.bin / "dkms").unlink()
        dialog = self.make()
        self.assertFalse(dialog.install_button.isEnabled())
        self.assertIn("Missing: DKMS", dialog.install_button.toolTip())
        self.assertIn("✕ DKMS: not installed. Install it: sudo pacman -S dkms",
                      dialog.checks_label.text())
        dialog.run(driver_setup.INSTALL)      # even when called directly: refused
        self.assertFalse(dialog.is_busy())
        self.assertTrue(dialog.result.is_error)
        self.assertEqual(self.confirmations, [])
        self.assertEqual(self.sys.calls(), [])

    def test_packaged_driver_blocks_the_install(self) -> None:
        self.sys.stock()
        self.sys.packaged("usr/src/yeetmouse-driver-0.9.5")
        dialog = self.make()
        self.assertFalse(dialog.install_button.isEnabled())
        self.assertIn("remove that package first", dialog.install_button.toolTip())

    def test_declined_confirmation_runs_nothing(self) -> None:
        self.answer = False
        dialog = self.make()
        dialog.install_button.click()
        self.assertFalse(dialog.is_busy())
        self.assertEqual([action for action, _text in self.confirmations], ["install"])
        self.assertIn("builds a kernel module as root", self.confirmations[0][1])
        self.assertIn("cancelled, nothing was changed", dialog.result.text())
        self.assertEqual(self.sys.calls(), [])

    def test_install_runs_the_script_from_standard_input_and_reports(self) -> None:
        self.sys.stock()
        self.sys.group()
        dialog = self.make()
        changed: list[int] = []
        dialog.changed.connect(lambda: changed.append(1))
        dialog.install_button.click()
        self.assertTrue(dialog.is_busy())
        self.assertFalse(dialog.install_button.isEnabled())   # one run at a time
        self.assertEqual(dialog.tabs.currentIndex(), 2)
        dialog.close()                                        # refused while it runs
        self.assertTrue(dialog.is_busy())
        self.wait(dialog)
        output = dialog.output_view.toPlainText()
        self.assertIn("[1/9] Checking what is needed", output)
        self.assertIn("[9/9] Loading the new module", output)
        self.assertIn("verified: commit 2a3e21956ef5a2ddfc4523a7ad4ef8117f6f4913", output)
        self.assertTrue(dialog.result.text().startswith("✓ Install forked driver: finished."))
        self.assertIn("Log out and in again", dialog.result.text())
        self.assertFalse(dialog.result.is_error)
        self.assertEqual(changed, [1])
        calls = self.sys.calls()
        self.assertIn("dkms remove yeetmouse-driver/0.9.5 --all", calls)
        self.assertIn("dkms install yeetmouse-driver/0.9.5+dirscale1", calls)
        self.assertTrue(self.sys.exists("usr/local/src/yeetmouse-driver-0.9.5"))
        # Looked again afterwards: the kept driver is known now.
        self.assertIn("Previous driver kept for Revert: 0.9.5", dialog.state_label.text())

    def test_failure_names_the_state_and_the_way_back(self) -> None:
        self.sys.stock()
        self.sys.group()
        self.stub_fail = "dkms install yeetmouse-driver/0.9.5+dirscale1"
        dialog = self.make()
        dialog.install_button.click()
        self.wait(dialog)
        self.assertTrue(dialog.result.is_error)
        text = dialog.result.text()
        self.assertIn("Install forked driver failed (exit status 9)", text)
        self.assertIn("State: the previous driver 0.9.5 is installed again.", text)
        self.assertIn("Revert to previous driver", text)
        self.assertIn("FAILED", dialog.output_view.toPlainText())

    def test_dry_run_needs_no_confirmation_and_changes_nothing(self) -> None:
        (self.sys.bin / "pkexec").unlink()     # not even pkexec is needed for it
        self.sys.fork()
        self.sys.source("0.9.5", "usr/local/src")
        dialog = self.make()
        self.assertFalse(dialog.revert_button.isEnabled())
        dialog.dry_check.setChecked(True)
        self.assertTrue(dialog.revert_button.isEnabled())
        dialog.revert_button.click()
        self.wait(dialog)
        self.assertEqual(self.confirmations, [])
        output = dialog.output_view.toPlainText()
        self.assertIn("$ /bin/sh -s -- --dry-run revert", output)
        self.assertIn("Dry run: nothing is changed.", output)
        self.assertIn("would run:", output)
        self.assertIn("dry run finished. Nothing was changed", dialog.result.text())
        self.assertEqual(self.sys.calls(), [])
        self.assertTrue(self.sys.exists("usr/local/src/yeetmouse-driver-0.9.5"))

    def test_revert_of_the_hand_installed_state(self) -> None:
        self.sys.fork()
        self.sys.userspace()
        self.sys.group()
        self.sys.source("0.9.5", "usr/local/src")
        dialog = self.make()
        self.assertTrue(dialog.revert_button.isEnabled())
        self.assertFalse(dialog.install_button.property("primary"))   # already installed
        dialog.revert_button.click()
        self.wait(dialog)
        self.assertEqual([action for action, _text in self.confirmations], ["revert"])
        self.assertTrue(dialog.result.text().startswith("✓ Revert to previous driver"))
        self.assertEqual(self.sys.calls()[:2], [
            "dkms remove yeetmouse-driver/0.9.5+dirscale1 --all",
            "dkms install yeetmouse-driver/0.9.5"])

    def test_dismissed_authorization_is_not_reported_as_a_broken_system(self) -> None:
        dialog = self.make()
        dialog._launcher = lambda action, dry_run=False, text=None: driver_setup.Launch(
            ["/bin/sh", "-c", "exit 126"], "", "stand-in for a dismissed pkexec prompt")
        dialog.install_button.click()
        self.wait(dialog)
        self.assertIn("not authorized or dismissed. Nothing was changed.",
                      dialog.result.text())

    def test_controls_are_named_and_mnemonics_unique(self) -> None:
        dialog = self.make()
        seen: dict[str, str] = {}
        texts = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        texts += [w.text() for w in dialog.findChildren(QtWidgets.QAbstractButton)
                  if not w.objectName().startswith("qt_") and w.parent() is dialog]
        for text in texts:
            key = _mnemonic(text)
            self.assertTrue(key, text)
            self.assertNotIn(key, seen, f"{text!r} and {seen.get(key)!r} share Alt+{key}")
            seen[key] = text
        for widget in (dialog.install_button, dialog.revert_button, dialog.recheck_button,
                       dialog.close_button, dialog.dry_check, dialog.script_view,
                       dialog.output_view):
            self.assertTrue(widget.accessibleDescription(), repr(widget))
        texts = [dialog.windowTitle(), dialog.state_label.text(), dialog.checks_label.text(),
                 dialog.install_changes.text(), dialog.revert_changes.text(),
                 dialog.confirmation_text("install"), dialog.confirmation_text("revert")]
        self.assertEqual([text for text in texts if BRANDS.search(text)], [])


class DriverSetupInMainWindowTest(MainWindowTest):
    """How the main window leads to the driver setup. Runs only the tests named here."""

    def test_help_menu_opens_the_setup_and_refreshes_afterwards(self) -> None:
        from tests.test_driver_setup import FakeSystem
        fake = FakeSystem(self)
        shutil.rmtree(self.params.parent)          # the module is not loaded
        window = self.make_window()
        self.assertEqual(window.driver_setup_action.text(), "&Driver setup…")
        window.make_setup_dialog = lambda: DriverSetupDialog(
            window, fake.probe(), confirm=lambda *_: False)
        self.assertIn("not loaded", window.driver_panel.status_label.text())
        window.driver_setup_action.trigger()
        dialog = window.setup_dialog
        assert dialog is not None
        self.assertTrue(dialog.isVisible())
        self.assertIn("no yeetmouse driver is installed", dialog.state_label.text())
        # Meanwhile the module appears (as after an install); closing looks again.
        self.params.mkdir(parents=True)
        for name in (*PARAM_NAMES, "update"):
            (self.params / name).write_text("1.000000\n" if name != "update" else "0\n")
        dialog.changed.emit()
        self.assertIn("values read from the driver", window.driver_panel.status_label.text())
        dialog.close()
        _app.processEvents()
        self.assertIsNone(window.setup_dialog)

    def test_stepper_button_leads_to_the_setup(self) -> None:
        from tests.test_driver_setup import FakeSystem
        fake = FakeSystem(self)
        shutil.rmtree(self.params.parent)
        window = self.make_window()
        window.make_setup_dialog = lambda: DriverSetupDialog(
            window, fake.probe(), confirm=lambda *_: False)
        window.mouse_panel._factory = lambda pid, on_log: FakeSession(pid)
        window.run_step(Step.START)
        self.assertEqual(window.steps_bar.next_button.text(), "Driver setup…")
        window.steps_bar.next_button.click()
        self.assertIsNotNone(window.setup_dialog)
        window.setup_dialog.close()


def load_tests(loader: unittest.TestLoader, _tests: unittest.TestSuite,
               _pattern: str | None) -> unittest.TestSuite:
    """Do not run the whole MainWindowTest a second time through its subclass."""
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(MainWindowTest))
    suite.addTests(loader.loadTestsFromTestCase(DriverSetupDialogTest))
    own = [name for name in vars(DriverSetupInMainWindowTest) if name.startswith("test_")]
    suite.addTests(DriverSetupInMainWindowTest(name) for name in own)
    return suite


if __name__ == "__main__":
    unittest.main()
