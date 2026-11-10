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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from quadsens.ui.qt import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover - depends on the environment
    raise unittest.SkipTest("no Qt binding (PySide6) installed") from None

import quadsens
from quadsens import about, emulator, hints
from quadsens.driver import PARAM_NAMES, Driver
from quadsens.model import Scalars
from quadsens.ui.driver_panel import CHANGED_MARK
from quadsens.ui.main_window import MainWindow, about_html
from quadsens.ui.mouse_panel import WRITE_PREFIX, write_line_colors
from quadsens.ui.scale_spin import ScaleSpinBox
from quadsens.ui.status import StatusDot

FIXTURE = Path(__file__).parent / "fixtures" / "ASFSettings.json"
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
                            str(self.tmp / "state.json"), uhid_path=str(self.uhid))
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
        self.assertEqual(titles, ["&File", "&Help"])
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
        self.assertIn("yeetmousectl save", box.text())
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
        self.assertIn("run the per-direction calibration", cal.message.text())
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
        self.assertIn("feature/directional-scalars", drv.status_label.text())
        self.assertIn("unknown", drv.pending_label.text())
        self.assertTrue(drv.apply_button.isEnabled())

        shutil.rmtree(self.params.parent)
        drv.read_button.click()
        self.assertIn("Load the forked yeetmouse module", drv.status_label.text())

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

    def test_uhid_problems_are_shown_up_front(self) -> None:
        window = self.make_window()
        self.assertTrue(window.mouse_panel.message.isHidden())
        self.assertEqual(window.mouse_panel.message.text(), "")

        self.uhid.unlink()
        missing = self.make_window()
        self.assertFalse(missing.mouse_panel.message.isHidden())
        self.assertIn("modprobe uhid", missing.mouse_panel.message.text())

        if os.geteuid() != 0:
            self.uhid.write_bytes(b"")
            self.uhid.chmod(0)
            denied = self.make_window()
            self.assertIn("99-quadsens.rules", denied.mouse_panel.message.text())
            self.assertIn("group input", denied.mouse_panel.message.text())
            self.assertTrue(denied.mouse_panel.message.is_error)

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
        self.assertIn("yeetmousectl save", drv.apply_button.toolTip())
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


if __name__ == "__main__":
    unittest.main()
