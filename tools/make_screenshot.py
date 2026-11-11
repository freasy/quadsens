#!/usr/bin/env python3
"""Offscreen smoke test: build the main window against fixtures and save a screenshot.

Usage: python3 tools/make_screenshot.py [--dark] [OUTPUT.png] [WIDTHxHEIGHT]
       python3 tools/make_screenshot.py --sheet OUTPUT.png

Uses a temp directory in place of sysfs and of the config file, a copy of the
fixture calibration file and a stand-in for yeetmousectl. It does not create
the uhid device, run pkexec or touch /sys or /etc. The picture shows the
window after Use result and Apply, with Save to config as the next step. The window
shows fixture data only; the one path it displays is below a neutral temp
directory. --dark renders with a dark palette instead of the default one.

--sheet renders a contact sheet for checking the look by eye: the controls in
their normal, hover, pressed, focused and disabled states, the banner kinds
and the stepper, once with the light and once with the dark palette.

The calibration data is made up but consistent: the four multipliers are the
1-2-1 weighted averages of the eight measured values, a few percent around 1.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quadsens import emulator  # noqa: E402
from quadsens.driver import PARAM_NAMES, Driver  # noqa: E402
from quadsens.model import Scalars  # noqa: E402
from quadsens.persist import SaveState, Saver  # noqa: E402
from quadsens.steps import FlowState, Step, derive  # noqa: E402
from quadsens.ui.main_window import MainWindow  # noqa: E402
from quadsens.ui.qt import BINDING, QtCore, QtGui, QtWidgets  # noqa: E402
from quadsens.ui.scale_spin import ScaleSpinBox  # noqa: E402
from quadsens.ui.status import Card, Disclosure, MessageLabel  # noqa: E402
from quadsens.ui.steps_bar import StepsBar  # noqa: E402
from quadsens.ui.theme import ThemeManager, set_primary  # noqa: E402


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


# Eight measured values in the order E, NE, N, NW, W, SW, S, SE.
DEMO_DIRECTIONAL = (1.012, 1.006, 0.991, 0.996, 0.988, 0.992, 1.009, 1.014)


def demo_calibration() -> dict[str, object]:
    """A result file as the game writes it: four values derived from the eight."""
    e, ne, n, nw, w, sw, s, se = DEMO_DIRECTIONAL

    def side(left: float, middle: float, right: float) -> float:
        return round((left + 2 * middle + right) / 4, 6)

    return {"m_WeightMode": 0, "directionalScalars": list(DEMO_DIRECTIONAL),
            "firmwareScalars": {"XPos": side(ne, e, se), "XNeg": side(nw, w, sw),
                                "YPos": side(sw, s, se), "YNeg": side(ne, n, nw)}}


def _sample(make, state: str = "") -> QtGui.QPixmap:
    """Render one control on a card. ``make`` builds the control."""
    app = QtWidgets.QApplication.instance()
    card = Card("sample")
    layout = QtWidgets.QHBoxLayout(card)
    layout.setContentsMargins(10, 8, 10, 8)
    widget = make()
    layout.addWidget(widget)
    if state == "disabled":
        widget.setEnabled(False)
    if state == "hover":
        widget.setAttribute(QtCore.Qt.WidgetAttribute.WA_UnderMouse, True)
    if state == "pressed" and isinstance(widget, QtWidgets.QAbstractButton):
        widget.setDown(True)
    if state != "focus":  # a lone control would take the focus by itself
        for item in (widget, *widget.findChildren(QtWidgets.QWidget)):
            item.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
    card.show()
    if state == "focus":
        card.activateWindow()
        widget.setFocus(QtCore.Qt.FocusReason.TabFocusReason)
    for _ in range(4):
        app.processEvents()
    if state == "focus" and not widget.hasFocus():
        print("note: %s did not take the focus on this platform" % type(widget).__name__)
    pixmap = card.grab()
    card.close()
    card.deleteLater()
    return pixmap


def contact_sheet(output: Path) -> None:
    """Light and dark renderings of every restyled component, side by side."""
    app = QtWidgets.QApplication.instance()
    manager = ThemeManager.instance()

    def button(text: str, primary: bool = False):
        def make() -> QtWidgets.QWidget:
            widget = QtWidgets.QPushButton(text)
            set_primary(widget, primary)
            return widget
        return make

    def line_edit() -> QtWidgets.QWidget:
        return QtWidgets.QLineEdit("1.0110")

    def spin() -> QtWidgets.QWidget:
        widget = ScaleSpinBox()
        widget.setDecimals(4)
        widget.setRange(0.1, 10)
        widget.setValue(1.011)
        return widget

    def combo() -> QtWidgets.QWidget:
        widget = QtWidgets.QComboBox()
        widget.addItems(["0x2B43 (default)", "0x2B44"])
        return widget

    def toggle(shown: bool):
        def make() -> QtWidgets.QWidget:
            widget = Disclosure("Ad&vanced", QtWidgets.QWidget())
            widget.setChecked(shown)
            return widget
        return make

    def check(checked: bool):
        def make() -> QtWidgets.QWidget:
            widget = QtWidgets.QCheckBox("Flip &Y")
            widget.setChecked(checked)
            return widget
        return make

    def banner(kind: str, text: str):
        def make() -> QtWidgets.QWidget:
            widget = MessageLabel(kind)
            widget.setMinimumWidth(330)
            widget.show_message(text, error=kind == "error", kind=kind)
            return widget
        return make

    def log() -> QtWidgets.QWidget:
        widget = QtWidgets.QPlainTextEdit()
        widget.setReadOnly(True)
        widget.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        widget.setPlainText("\n".join("12:00:%02d [cmd] GET id=%d value=0 | rx 00010102%02x"
                                      % (i, i, i) for i in range(12)))
        widget.setFixedSize(300, 90)
        return widget

    def stepper(state: FlowState):
        def make() -> QtWidgets.QWidget:
            widget = StepsBar()
            widget.set_steps(derive(state))
            widget.setMinimumWidth(640)
            return widget
        return make

    states = ("", "hover", "pressed", "focus", "disabled")
    rows: list[tuple[str, list[tuple]]] = [
        ("Secondary button: normal, hover, pressed, focus, disabled",
         [(button("Read again"), st) for st in states]),
        ("Primary button: normal, hover, pressed, focus, disabled",
         [(button("Save to config", True), st) for st in states]),
        ("Text field, spin box, combo box: normal, focus, disabled",
         [(make, st) for make in (line_edit, spin, combo) for st in ("", "focus", "disabled")]),
        ("Disclosure: closed, hover, open, focus; check box: off, on, focus, disabled",
         [(toggle(False), ""), (toggle(False), "hover"), (toggle(True), ""),
          (toggle(False), "focus"), (check(False), ""), (check(True), ""),
          (check(True), "focus"), (check(True), "disabled")]),
        ("Banners: info, success", [
            (banner("info", "Filled from the result file. Press Apply to write them."), ""),
            (banner("success", "✓ Applied directly. The driver reports the new values."), "")]),
        ("Banners: warning, error", [
            (banner("warning", "Status: not readable. Join group yeetmouse, log in again."),
             ""),
            (banner("error", "Not saved: authorization was dismissed"), "")]),
        ("Log view with thin scroll bars", [(log, "")]),
        ("Stepper: current step 3, with done and pending steps",
         [(stepper(FlowState(mouse_running=True, driver=Scalars.unity())), "")]),
    ]

    halves: list[QtGui.QImage] = []
    for palette in (app.style().standardPalette(), dark_palette()):
        app.setPalette(palette)
        manager.apply()
        theme = manager.theme()
        rendered = [(title, [_sample(make, st) for make, st in samples])
                    for title, samples in rows]
        width = max(sum(p.width() + 8 for p in pixmaps) for _t, pixmaps in rendered) + 24
        height = sum(26 + max(p.height() for p in pixmaps) + 10 for _t, pixmaps in rendered) + 16
        image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_ARGB32)
        image.fill(QtGui.QColor(theme.window))
        painter = QtGui.QPainter(image)
        y = 10
        for title, pixmaps in rendered:
            painter.setPen(QtGui.QColor(theme.muted))
            painter.drawText(12, y + 14, title)
            y += 22
            x = 12
            for pixmap in pixmaps:
                painter.drawPixmap(x, y, pixmap)
                x += pixmap.width() + 8
            y += max(p.height() for p in pixmaps) + 14
        painter.end()
        halves.append(image)
    sheet = QtGui.QImage(max(h.width() for h in halves), sum(h.height() for h in halves),
                         QtGui.QImage.Format.Format_ARGB32)
    sheet.fill(QtGui.QColor("#808080"))
    painter = QtGui.QPainter(sheet)
    y = 0
    for half in halves:
        painter.drawImage(0, y, half)
        y += half.height()
    painter.end()
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(str(output))
    print("contact sheet %s (%dx%d)" % (output, sheet.width(), sheet.height()))


def main(argv: list[str]) -> int:
    if "--sheet" in argv:
        argv = [arg for arg in argv if arg != "--sheet"]
        if len(argv) < 2:
            print("usage: make_screenshot.py --sheet OUTPUT.png", file=sys.stderr)
            return 2
        QtCore.QLocale.setDefault(QtCore.QLocale.c())
        app = QtWidgets.QApplication([argv[0]])
        ThemeManager.instance().apply()
        contact_sheet(Path(argv[1]))
        return 0
    dark = "--dark" in argv
    argv = [arg for arg in argv if arg != "--dark"]
    default_name = "screenshot-dark.png" if dark else "screenshot.png"
    output = Path(argv[1]) if len(argv) > 1 else ROOT / "docs" / default_name
    width, height = (int(n) for n in (argv[2] if len(argv) > 2 else "860x600").split("x"))
    QtCore.QLocale.setDefault(QtCore.QLocale.c())
    app = QtWidgets.QApplication([argv[0]])
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
        (cal_dir / "ASFSettings.json").write_text(
            json.dumps(demo_calibration(), separators=(",", ":")), encoding="utf-8")
        pattern = str(Path(tmp, "Users", "*", "ASFData", "*", "ASFSettings.json"))
        settings = QtCore.QSettings(str(Path(tmp, "settings.ini")),
                                    QtCore.QSettings.Format.IniFormat)

        config = Path(tmp, "yeetmouse.conf")
        config.write_text("sens=1\n", encoding="ascii")
        os.environ["QUADSENS_FAKE_PARAMS"] = str(params)
        saver = Saver(config, tool=ROOT / "tests" / "fake_yeetmousectl.py")

        window = MainWindow(settings, pattern, Driver(params), None, uhid_path=str(uhid),
                            saver=saver)
        window.resize(width, height)
        window.show()

        # Exercise the guided steps against the fixtures: use, flip Y, apply, save.
        window.calibration_panel.refresh()
        assert window.calibration_panel.calibration is not None
        drv = window.driver_panel
        window.run_step(Step.USE)
        before = drv.values()
        drv.flip_check.setChecked(True)
        assert drv.values() == before.flipped_y(), "flip Y did not exchange the values"
        drv.flip_check.setChecked(False)
        drv.advanced_toggle.setChecked(False)
        window.run_step(Step.APPLY)
        assert (params / "update").read_text() == "1\n", "apply did not reach the driver dir"
        assert drv.state.scalars is not None and drv.state.scalars.is_close(before)
        assert window.steps_bar.current().step is Step.SAVE
        capture = lambda: (app.processEvents(), window.grab())[1]  # noqa: E731
        picture = None

        # Feed the log view the way the device would, without opening /dev/uhid.
        device = emulator.BragiDevice(log=window.mouse_panel._on_entry)
        device.current_node = "cmd"
        device.handle(bytes([0, 1, 0, emulator.CMD_SESSION, 1, 0x40, 0xE2, 0x01, 0x00]))
        device.handle(bytes([0, 1, 1, emulator.CMD_GET]) + struct.pack("<H", 0x13))
        for prop, value in zip(range(0x125, 0x129), before.as_tuple()):
            device.handle(bytes([0, 1, 1, emulator.CMD_SET]) + struct.pack("<Hf", prop, value))
        device.handle(bytes([0, 1, 1, emulator.CMD_SET]) + struct.pack("<HI", 0x129, 1))

        for _ in range(5):
            app.processEvents()
        picture = capture()

        # The save itself, after the picture: the stand-in tool writes the config.
        window.run_step(Step.SAVE)
        deadline = time.monotonic() + 10
        while drv.is_busy() and time.monotonic() < deadline:
            app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
        assert drv.save_state is SaveState.SAVED, drv.message.text()
        assert window.steps_bar.current() is None, "not all steps are done after the save"

        output.parent.mkdir(parents=True, exist_ok=True)
        if not picture.save(str(output)):
            print("could not write %s" % output, file=sys.stderr)
            return 1
        hint = window.minimumSizeHint()
        print("smoke test ok (%s); minimum size %dx%d; screenshot %s"
              % (BINDING, hint.width(), hint.height(), output))
        window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
