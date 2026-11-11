"""The Windows composition of the application, shown on Linux through the override.

The RawAccel folder is a temp directory with a fixture settings.json and a
script that stands in for writer.exe. Nothing here has run on Windows.
"""

from __future__ import annotations

import json
import os
import shutil
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

from quadsens import rawaccel, values_file
from quadsens.model import Scalars
from quadsens.rawaccel import Four
from quadsens.ui.windows_window import WindowsMainWindow

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])
VALUES = Scalars(1.011, 0.991, 1.006, 0.996)
# The stand-in for writer.exe is a shell script, which only POSIX can start.
POSIX = os.name == "posix"
needs_fake_writer = unittest.skipUnless(POSIX, "the stand-in for writer.exe is a shell script")


class WindowsWindowTest(unittest.TestCase):
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

    def make(self, choose: bool = True) -> WindowsMainWindow:
        settings = QtCore.QSettings(str(self.tmp / "settings.ini"),
                                    QtCore.QSettings.Format.IniFormat)
        window = WindowsMainWindow(settings, self.pattern,
                                   {"USERPROFILE": str(self.tmp / "profile"),
                                    "SystemDrive": str(self.tmp / "nodrive")})
        self.windows.append(window)
        if choose and window.rawaccel_panel.folder is None:
            window.rawaccel_panel.choose_folder(str(self.folder))
        return window

    def wait(self, window: WindowsMainWindow) -> None:
        deadline = time.monotonic() + 10
        while window.rawaccel_panel.is_busy() and time.monotonic() < deadline:
            _app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)

    def statuses(self, window: WindowsMainWindow) -> str:
        return "".join(chip.text()[0] for chip in window.steps_bar.chips)

    def file_values(self, index: int = 0) -> Four:
        return rawaccel.read(self.settings_file).values(index)

    def test_composition_has_no_linux_parts(self) -> None:
        window = self.make(choose=False)
        self.assertEqual(window.windowTitle(), "QuadSens")
        for name in ("mouse_panel", "driver_panel", "driver_strip", "setup_dialog"):
            self.assertFalse(hasattr(window, name), name)
        self.assertEqual([a.text() for a in window.menuBar().actions()],
                         ["&File", "View", "&Help"])
        self.assertEqual(len(window.steps_bar.chips), 4)
        self.assertEqual([chip.text() for chip in window.steps_bar.chips], [
            "▶ 1 Get values", "○ 2 RawAccel folder", "○ 3 Preview", "○ 4 Apply"])
        self.assertIn("Import a values file", window.calibration_panel.message.text())
        self.assertIn("forgets its settings", window.rawaccel_panel.boot_label.text())
        self.assertFalse(window.rawaccel_panel.apply_button.isEnabled())
        self.assertIn("Choose the RawAccel folder", window.rawaccel_panel.state_label.text())
        hint = window.minimumSizeHint()
        self.assertLessEqual((hint.width() <= 760, hint.height() <= 560), (True, True), hint)

    @needs_fake_writer
    def test_walk_through_import_folder_preview_apply(self) -> None:
        window = self.make(choose=False)
        panel = window.rawaccel_panel
        self.assertEqual(self.statuses(window), "▶○○○")
        self.assertFalse(window.steps_bar.next_button.isEnabled())   # nothing to take yet
        self.assertTrue(window.import_values(str(self.values_path)))
        self.assertEqual(panel.values(), VALUES)                      # filled at once
        self.assertEqual(window.calibration_panel.diagram.scalars(), VALUES)
        self.assertEqual(self.statuses(window), "✓▶○○")
        self.assertEqual(window.steps_bar.next_button.text(), "Choose folder…")
        self.assertTrue(panel.choose_button.property("primary"))

        panel.choose_folder(str(self.folder))
        self.assertEqual(self.statuses(window), "✓✓✓▶")
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
        self.assertEqual(self.statuses(window), "✓✓✓✓")
        self.assertIn("All four steps are done", window.steps_bar.next_label.text())
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
        self.assertEqual(self.statuses(window)[1], "▶")
        self.assertIn("does not guess", window.steps_bar.next_label.text())

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
        self.assertIn("only applies values", window.info_box.text())
        window.info_box.close()


class ImportCleanlinessTest(unittest.TestCase):
    """The Windows path must not pull in anything that only exists on Linux."""

    # quadsens.persist is imported for an enum the step logic shares; it has no
    # POSIX-only import and runs nothing at import time.
    LINUX_ONLY = ("quadsens.emulator", "quadsens.driver", "quadsens.driver_setup",
                  "quadsens.hints", "quadsens.ui.main_window",
                  "quadsens.ui.mouse_panel", "quadsens.ui.driver_panel",
                  "quadsens.ui.driver_strip", "quadsens.ui.setup_dialog")
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
        # None of the modules on this path may call what only POSIX has. (Looking
        # at sys.modules for fcntl or pwd would prove nothing here: on Linux the
        # standard library loads those itself.)
        missing_on_windows = ("getuid", "geteuid", "getgid", "getegid", "getgroups",
                              "uname", "fork", "chown", "mkfifo", "O_NOFOLLOW", "getpwuid")
        offenders = []
        for name in sorted(modules):
            if not name.startswith("quadsens") or name == "quadsens.persist":
                continue
            path = ROOT / (name.replace(".", "/") + ".py")
            if not path.exists():
                path = ROOT / name.replace(".", "/") / "__init__.py"
            source = path.read_text(encoding="utf-8")
            offenders += [f"{name}: {word}" for word in missing_on_windows
                          if f".{word}" in source]
        self.assertEqual(offenders, [])

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


if __name__ == "__main__":
    unittest.main()
