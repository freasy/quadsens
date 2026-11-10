#!/usr/bin/env python3
"""Offscreen smoke test: build the main window against fixtures and save a screenshot.

Usage: python3 tools/make_screenshot.py [--dark] [OUTPUT.png] [WIDTHxHEIGHT]

Uses a temp directory in place of sysfs and a copy of the fixture calibration
file. It does not create the uhid device, run pkexec or touch /sys. The window
shows fixture data only; the one path it displays is below a neutral temp
directory. --dark renders with a dark palette instead of the default one.
"""

from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quadsens import emulator  # noqa: E402
from quadsens.driver import PARAM_NAMES, Driver  # noqa: E402
from quadsens.ui.main_window import MainWindow  # noqa: E402
from quadsens.ui.qt import BINDING, QtCore, QtGui, QtWidgets  # noqa: E402


def dark_palette() -> QtGui.QPalette:
    """A plain dark palette, to check that nothing relies on light colours."""
    role, group = QtGui.QPalette.ColorRole, QtGui.QPalette.ColorGroup
    palette = QtGui.QPalette()
    for name, color in (
            (role.Window, "#2a2e32"), (role.WindowText, "#e6e6e6"),
            (role.Base, "#1b1e20"), (role.AlternateBase, "#232629"),
            (role.Text, "#e6e6e6"), (role.Button, "#31363b"),
            (role.ButtonText, "#e6e6e6"), (role.ToolTipBase, "#31363b"),
            (role.ToolTipText, "#e6e6e6"), (role.PlaceholderText, "#9a9a9a"),
            (role.Highlight, "#3daee9"), (role.HighlightedText, "#ffffff"),
            (role.Link, "#5cb8ff"), (role.Light, "#474d54"), (role.Midlight, "#3a3f44"),
            (role.Mid, "#6e7175"), (role.Dark, "#141618"), (role.Shadow, "#0b0c0d")):
        palette.setColor(name, QtGui.QColor(color))
    for name in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(group.Disabled, name, QtGui.QColor("#7a7d80"))
    return palette


def main(argv: list[str]) -> int:
    dark = "--dark" in argv
    argv = [arg for arg in argv if arg != "--dark"]
    default_name = "screenshot-dark.png" if dark else "screenshot.png"
    output = Path(argv[1]) if len(argv) > 1 else ROOT / "docs" / default_name
    width, height = (int(n) for n in (argv[2] if len(argv) > 2 else "900x660").split("x"))
    QtCore.QLocale.setDefault(QtCore.QLocale.c())
    app = QtWidgets.QApplication([argv[0]])
    app.setStyle("Fusion")
    if dark:
        app.setPalette(dark_palette())
    # The calibration panel shows this path, so keep it free of user names.
    base = "/tmp" if os.access("/tmp", os.W_OK) else None
    with tempfile.TemporaryDirectory(prefix="quadsens-demo-", dir=base) as tmp:
        params = Path(tmp, "sys", "yeetmouse", "parameters")
        params.mkdir(parents=True)
        for name in PARAM_NAMES:
            (params / name).write_text("1.000000\n")
        (params / "update").write_text("0\n")
        uhid = Path(tmp, "uhid")  # a plain file: "writable", but never opened
        uhid.write_bytes(b"")
        cal_dir = Path(tmp, "Users", "u1", "ASFData", "mouse")
        cal_dir.mkdir(parents=True)
        shutil.copy(ROOT / "tests" / "fixtures" / "ASFSettings.json", cal_dir)
        pattern = str(Path(tmp, "Users", "*", "ASFData", "*", "ASFSettings.json"))
        settings = QtCore.QSettings(str(Path(tmp, "settings.ini")),
                                    QtCore.QSettings.Format.IniFormat)

        window = MainWindow(settings, pattern, Driver(params), None, uhid_path=str(uhid))
        window.resize(width, height)
        window.show()

        # Exercise refresh, use-calibration, flip-Y and apply against the fixtures.
        window.calibration_panel.refresh()
        assert window.calibration_panel.calibration is not None
        drv = window.driver_panel
        drv.use_button.click()
        before = drv.values()
        drv.flip_check.setChecked(True)
        assert drv.values() == before.flipped_y(), "flip Y did not exchange the values"
        drv.flip_check.setChecked(False)
        drv.apply_button.click()
        assert (params / "update").read_text() == "1\n", "apply did not reach the driver dir"
        assert drv.state.scalars is not None and drv.state.scalars.is_close(before)

        # Feed the log view the way the device would, without opening /dev/uhid.
        device = emulator.BragiDevice(log=window.mouse_panel._on_entry)
        device.current_node = "cmd"
        device.handle(bytes([0, 1, 0, emulator.CMD_SESSION, 1, 0x40, 0xE2, 0x01, 0x00]))
        device.handle(bytes([0, 1, 1, emulator.CMD_GET]) + struct.pack("<H", 0x13))
        for prop, value in zip(range(0x125, 0x129), before.as_tuple()):
            device.handle(bytes([0, 1, 1, emulator.CMD_SET]) + struct.pack("<Hf", prop, value))
        device.handle(bytes([0, 1, 1, emulator.CMD_SET]) + struct.pack("<HI", 0x129, 1))

        app.processEvents()
        output.parent.mkdir(parents=True, exist_ok=True)
        if not window.grab().save(str(output)):
            print("could not write %s" % output, file=sys.stderr)
            return 1
        hint = window.minimumSizeHint()
        print("smoke test ok (%s); minimum size %dx%d; screenshot %s"
              % (BINDING, hint.width(), hint.height(), output))
        window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
