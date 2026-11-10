"""Offscreen tests of the main window. Skipped when no Qt binding is installed.

Nothing here opens /dev/uhid, runs pkexec or touches /sys: the driver points at
a temp directory and the calibration glob at a copy of the fixture file.
"""

from __future__ import annotations

import os
import shutil
import struct
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from quadsens.ui.qt import QtCore, QtWidgets
except ImportError:  # pragma: no cover - depends on the environment
    raise unittest.SkipTest("no Qt binding (PySide6) installed") from None

from quadsens import emulator
from quadsens.driver import PARAM_NAMES, Driver
from quadsens.model import Scalars
from quadsens.ui.main_window import MainWindow
from quadsens.ui.status import StatusDot

FIXTURE = Path(__file__).parent / "fixtures" / "ASFSettings.json"
_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])


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
                            str(self.tmp / "state.json"))
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


if __name__ == "__main__":
    unittest.main()
