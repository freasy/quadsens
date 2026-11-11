#!/usr/bin/env python3
"""Offscreen smoke test: build the main window against fixtures and save a screenshot.

Usage: python3 tools/make_screenshot.py [--theme quadsens|system] [--dark]
                                        [OUTPUT.png] [WIDTHxHEIGHT]
       python3 tools/make_screenshot.py --sheet OUTPUT.png
       python3 tools/make_screenshot.py [--theme ...] [--dark] --diagrams DIRECTORY
       python3 tools/make_screenshot.py [--theme ...] [--dark] --gallery DIRECTORY
       python3 tools/make_screenshot.py [--theme ...] [--dark] --setup OUTPUT.png
       python3 tools/make_screenshot.py [--theme ...] [--dark] --checks DIRECTORY
       python3 tools/make_screenshot.py [--theme ...] [--dark] --windows OUTPUT.png
       python3 tools/make_screenshot.py [--theme ...] --windows-missing OUTPUT.png

Uses a temp directory in place of sysfs and of the config file, a made-up
calibration file and a stand-in for yeetmousectl. It does not create the uhid
device, run pkexec or touch /sys or /etc. The window shows fixture data only;
the one path it displays is below a neutral temp directory. The picture shows
the window after Use result and Apply, with Save to config as the next step.

--theme chooses the built-in black and red theme (default) or the one that
follows the system colours; --dark gives the latter a dark palette. Without
OUTPUT the picture goes to docs/screenshot.png (black and red) or
docs/screenshot-system-light.png.

--sheet renders a contact sheet for checking the look by eye: the controls in
their normal, hover, pressed, focused and disabled states, the banner kinds
and the stepper, once per theme (black and red, system light, system dark).

--diagrams renders the direction diagram alone for three sets of multipliers
(a real result, all 1.0, strongly lopsided) at each diagram zoom into DIRECTORY.

--gallery renders what the main screenshot does not show into DIRECTORY: a
problem state with warning and error banners, the About dialog and the open
Theme menu.

--setup renders the driver setup dialog for a made-up machine that has the
unmodified driver installed (default output: docs/screenshot-driver-setup.png).

--checks renders the main window with the driver check in its states (passed,
fixable, log in again, prerequisites missing) into DIRECTORY, each also at the
minimum window size. Without DIRECTORY it writes only the fixable state, to
docs/screenshot-driver-check.png.

--windows renders the window as it is composed on Windows, with a made-up
RawAccel folder and a stand-in for usbip-win2: the virtual mouse attached and
a calibration captured from it, before Apply. Default output:
docs/screenshot-windows.png. --windows-missing renders the same window when
usbip-win2 is not installed and the values come from an imported file.
Rendered on Linux; both show the layout, not Windows itself.

The calibration data is made up but consistent: the four multipliers are the
1-2-1 weighted averages of the eight measured values, a few percent around 1.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quadsens import emulator  # noqa: E402
from quadsens.diagram import ZOOMS  # noqa: E402
from quadsens.driver import PARAM_NAMES, Driver  # noqa: E402
from quadsens.driver_setup import Probe  # noqa: E402
from quadsens.model import Scalars  # noqa: E402
from quadsens.persist import SaveState, Saver  # noqa: E402
from quadsens.steps import FlowState, Step, derive  # noqa: E402
from quadsens.ui.main_window import MainWindow  # noqa: E402
from quadsens.ui.compass import DirectionDiagram  # noqa: E402
from quadsens.ui.qt import BINDING, QtCore, QtGui, QtWidgets  # noqa: E402
from quadsens.ui.scale_spin import ScaleSpinBox  # noqa: E402
from quadsens.ui.setup_dialog import DriverSetupDialog  # noqa: E402
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
    for mode, palette in (("quadsens", None), ("system", QtGui.QPalette()),
                          ("system", dark_palette())):
        manager.set_mode(mode)
        if palette is not None:
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


# Right, left, down, up.
DIAGRAM_CASES = {
    "real": Scalars(0.9855, 1.0012, 0.9953, 1.0016),
    "ones": Scalars.unity(),
    "lopsided": Scalars(1.08, 0.93, 1.05, 0.97),
}


def diagram_renders(directory: Path, size: tuple[int, int] = (360, 300)) -> None:
    """The diagram on a card, once per case and zoom, as zoom-<case>-<centre>.png."""
    app = QtWidgets.QApplication.instance()
    directory.mkdir(parents=True, exist_ok=True)
    for name, scalars, centre in [(name, scalars, centre)
                                  for name, scalars in DIAGRAM_CASES.items()
                                  for centre in ZOOMS]:
        card = Card("sample")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        widget = DirectionDiagram()
        widget.set_zoom(centre)
        widget.set_scalars(scalars)
        layout.addWidget(widget)
        card.resize(*size)
        card.show()
        for _ in range(4):
            app.processEvents()
        target = directory / f"zoom-{name}-{str(centre).replace('.', '')}.png"
        card.grab().save(str(target))
        card.close()
        card.deleteLater()
        print("diagram %s: %s" % (name, target))


def gallery(directory: Path, mode: str) -> None:
    """A problem state, the About dialog and the Theme menu, as PNG files."""
    app = QtWidgets.QApplication.instance()
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="quadsens-demo-") as tmp:
        settings = QtCore.QSettings(str(Path(tmp, "settings.ini")),
                                    QtCore.QSettings.Format.IniFormat)
        settings.setValue("ui/theme", mode)
        empty = Path(tmp, "no-tools")
        empty.mkdir()
        config = Path(tmp, "yeetmouse.conf")
        config.write_text("sens=1\n", encoding="ascii")
        # Nothing is set up: no uhid node, module not loaded, no result, no save tool.
        window = MainWindow(settings, str(Path(tmp, "Users", "*", "x.json")),
                            Driver(Path(tmp, "missing")), None,
                            uhid_path=str(Path(tmp, "no-uhid")),
                            saver=Saver(config, trusted_dirs=(str(empty),)))
        window.resize(760, 560)
        window.show()
        window.driver_panel.apply_button.click()        # an error banner
        window.driver_panel.read_button.setFocus()      # focus ring on a secondary button
        for _ in range(5):
            app.processEvents()
        window.grab().save(str(directory / "problems.png"))
        window.about_action.trigger()
        for _ in range(5):
            app.processEvents()
        window.info_box.grab().save(str(directory / "about.png"))
        window.info_box.close()
        window.setup_action.trigger()
        for _ in range(5):
            app.processEvents()
        window.info_box.grab().save(str(directory / "setup-help.png"))
        window.info_box.close()
        menu = window.theme_menu
        menu.setActiveAction(window.theme_actions["system"])
        menu.ensurePolished()
        menu.resize(menu.sizeHint())
        menu.grab().save(str(directory / "menu.png"))
        window.close()
    print("gallery in %s (%s)" % (directory, mode))


def demo_probe(root: Path, driver: str = "fork", tools: bool = True,
               in_session: bool = True) -> Probe:
    """A made-up machine for the driver check and the driver setup dialog."""
    kernel = "6.12.0-demo"
    for folder in ("usr/bin", "usr/lib/systemd/system", "run/systemd/system", "etc",
                   f"lib/modules/{kernel}/build"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "etc/os-release").write_text("ID=arch\n")
    status = ""
    if driver == "fork":
        status = f"yeetmouse-driver/0.9.5+dirscale1, {kernel}, x86_64: installed\n"
        (root / "usr/bin/yeetmousectl").write_text("tool dirScaleXPos")
        (root / "usr/lib/systemd/system/yeetmouse.service").write_text("unit")
        params = root / "sys/module/yeetmouse/parameters"
        params.mkdir(parents=True, exist_ok=True)
        (params / "DirScaleXPos").write_text("1")
    elif driver == "stock":
        status = f"yeetmouse-driver/0.9.5, {kernel}, x86_64: installed\n"
        (root / "usr/src/yeetmouse-driver-0.9.5").mkdir(parents=True, exist_ok=True)
        (root / "usr/src/yeetmouse-driver-0.9.5/dkms.conf").write_text("x")
        (root / "usr/bin/yeetmousectl").write_text("unmodified tool")
        (root / "sys/module/yeetmouse/parameters").mkdir(parents=True, exist_ok=True)

    def run(argv: list[str]) -> tuple[int, str]:
        if argv[1:2] == ["status"]:
            return 0, status
        if argv[1:3] == ["group", "yeetmouse"] and driver != "none":
            return 0, "yeetmouse:x:931:demo\n"
        return 1, ""

    present = ("dkms", "git", "make", "cc", "c++", "bash", "pkexec", "getent")
    if not tools:
        present = ("dkms", "bash", "pkexec", "getent")
    return Probe(root=root, run=run, kernel=kernel,
                 which=lambda name: f"/usr/bin/{name}" if name in present else None,
                 session_groups=lambda: {931} if in_session else {1000},
                 user=lambda: "demo")


def check_renders(directory: Path | None, mode: str) -> None:
    """The main window with the driver check in each of its states."""
    app = QtWidgets.QApplication.instance()
    doc_only = directory is None
    if directory is None:
        directory = ROOT / "docs"
    directory.mkdir(parents=True, exist_ok=True)
    cases = {
        "passed": dict(driver="fork"),
        "fix": dict(driver="stock"),
        "relogin": dict(driver="fork", in_session=False),
        "missing": dict(driver="none", tools=False),
    }
    if doc_only:
        cases = {"fix": cases["fix"]}
    for name, options in cases.items():
        with tempfile.TemporaryDirectory(prefix="quadsens-demo-") as tmp:
            params = Path(tmp, "sys", "yeetmouse", "parameters")
            params.mkdir(parents=True)
            for param in PARAM_NAMES:
                (params / param).write_text("1.000000\n")
            (params / "update").write_text("0\n")
            if name == "relogin":
                for param in PARAM_NAMES:
                    (params / param).chmod(0)
            if name in ("fix", "missing"):
                shutil.rmtree(params)
                if name == "fix":
                    params.mkdir(parents=True)
                    (params / "Sensitivity").write_text("1\n")
            uhid = Path(tmp, "uhid")
            uhid.write_bytes(b"")
            cal_dir = Path(tmp, "Users", "u1", "ASFData", "mouse")
            cal_dir.mkdir(parents=True)
            (cal_dir / "ASFSettings.json").write_text(
                json.dumps(demo_calibration(), separators=(",", ":")), encoding="utf-8")
            settings = QtCore.QSettings(str(Path(tmp, "settings.ini")),
                                        QtCore.QSettings.Format.IniFormat)
            settings.setValue("ui/theme", mode)
            config = Path(tmp, "yeetmouse.conf")
            config.write_text("sens=1\n", encoding="ascii")
            window = MainWindow(
                settings, str(Path(tmp, "Users", "*", "ASFData", "*", "ASFSettings.json")),
                Driver(params), None, uhid_path=str(uhid),
                saver=Saver(config, tool=ROOT / "tests" / "fake_yeetmousectl.py"),
                setup_probe=demo_probe(Path(tmp, "machine"), **options))
            for size, suffix in (((860, 600), ""), ((720, 520), "-min")):
                if doc_only and suffix:
                    continue
                window.resize(*size)
                window.show()
                window.driver_strip.wait()
                for _ in range(6):
                    app.processEvents()
                target = directory / ("screenshot-driver-check.png" if doc_only
                                      else f"drivercheck-{name}{suffix}.png")
                window.grab().save(str(target))
            hint = window.minimumSizeHint()
            print("driver check %-8s %-9s minimum %dx%d" % (
                name, window.driver_strip.strip.kind, hint.width(), hint.height()))
            window.close()


class _DemoUsbip:
    """Stands in for usbip.exe in the renders: attaches the in-process test client."""

    def __init__(self) -> None:
        self.client = None
        self.port = 0

    def __call__(self, argv: list[str], _timeout: float) -> tuple[int, str]:
        from quadsens.usbip_server import Client
        args = argv[1:]
        if args[:1] == ["-t"]:
            self.port, args = int(args[1]), args[2:]
        if args == ["--version"]:
            return 0, "0.9.8.1\n"
        if args[:1] == ["attach"] and "--stop" not in args:
            self.client = Client("127.0.0.1", self.port)
            self.client.import_device(args[args.index("-b") + 1])
            return 0, "3\n"
        if args[:1] == ["detach"] and self.client is not None:
            self.client.close()
            self.client = None
        return 0, ""

    def write(self, prop: int, payload: bytes) -> None:
        from quadsens import usbip_server
        request = bytes([0, 1, 0, emulator.CMD_SET]) + struct.pack("<H", prop) + payload
        self.client.submit(usbip_server.DIR_OUT, 1, 64, data=request.ljust(64, b"\0"))
        self.client.reply()


def windows_render(output: Path, mode: str, size: tuple[int, int] = (900, 720),
                   missing: bool = False) -> None:
    """The Windows composition with fixture data.

    Default: usbip-win2 found, the virtual mouse attached, a calibration
    captured from it and taken as multipliers, before Apply. With ``missing``:
    usbip-win2 is not installed and the values come from an imported file.
    """
    from quadsens import values_file, win_install
    from quadsens.ui.tools_strip import RawAccelInfo
    from quadsens.ui.windows_window import WindowsMainWindow
    from quadsens.usbip_client import UsbipClient
    from quadsens.usbip_mouse import VirtualMouse
    from quadsens.usbip_server import VirtualMouseServer
    app = QtWidgets.QApplication.instance()
    with tempfile.TemporaryDirectory(prefix="quadsens-demo-") as tmp:
        folder = Path(tmp, "RawAccel")
        folder.mkdir()
        shutil.copy(ROOT / "tests" / "fixtures" / "rawaccel" / "settings-1.7.1-default.json",
                    folder / "settings.json")
        (folder / "writer.exe").write_text("#!/bin/sh\nexit 0\n")
        (folder / "writer.exe").chmod(0o755)
        demo = demo_calibration()
        scalars = demo["firmwareScalars"]
        four = Scalars(scalars["XPos"], scalars["XNeg"], scalars["YPos"], scalars["YNeg"])
        values = Path(tmp, "multipliers.quadsens.json")
        values.write_text(values_file.dump(four, tuple(demo["directionalScalars"]), "demo"))
        settings = QtCore.QSettings(str(Path(tmp, "settings.ini")),
                                    QtCore.QSettings.Format.IniFormat)
        settings.setValue("ui/theme", mode)
        usbip = _DemoUsbip()
        tool = None if missing else "C:\\Program Files\\USBip\\usbip.exe"

        def pump(seconds: float = 0.3) -> None:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)

        window = WindowsMainWindow(
            settings, str(Path(tmp, "none", "*", "x.json")),
            {"USERPROFILE": str(Path(tmp, "profile")), "SystemDrive": str(Path(tmp, "nodrive"))},
            find_client=lambda: UsbipClient(tool, usbip),
            mouse_factory=lambda pid: VirtualMouse(
                UsbipClient(tool, usbip), VirtualMouseServer(pid, port=0)),
            # Nothing of the render asks the network or looks at this machine.
            driver_state=lambda installed_at: (
                win_install.DRIVER_MISSING if missing else win_install.DRIVER_READY),
            latest_release=None)
        window.resize(*size)
        window.show()
        window.rawaccel_panel.set_folder("C:\\Tools\\RawAccel", remember=False)
        window.rawaccel_panel.folder = folder      # shown as a Windows path, read from here
        window.rawaccel_panel.reload()
        # The strip names the folder as the user would see it, not the stand-in.
        window.tools_strip._rawaccel_info = lambda: RawAccelInfo(
            "" if missing else "C:\\Tools\\RawAccel", "" if missing else "1.7.1",
            win_install.DRIVER_MISSING if missing else win_install.DRIVER_READY)
        window.tools_strip.refresh_rawaccel()
        if missing:
            assert window.import_values(str(values))
            window.calibration_panel.message.show_message(
                "Showing the imported values. A newer result file replaces them.")
        else:
            window.mouse_panel.start()
            pump(0.5)
            assert usbip.client is not None, window.mouse_panel.message.text()
            for prop, value in zip((emulator.P_DIR_SENS_X_POS, emulator.P_DIR_SENS_X_NEG,
                                    emulator.P_DIR_SENS_Y_POS, emulator.P_DIR_SENS_Y_NEG),
                                   (four.x_pos, four.x_neg, four.y_pos, four.y_neg)):
                usbip.write(prop, struct.pack("<f", value))
            usbip.write(emulator.P_DIR_SENS_ENABLED, struct.pack("<I", 1))
            pump(0.5)
            window.rawaccel_panel.use_result()
        window.rawaccel_panel.message.show_message("")
        pump(0.2)
        output.parent.mkdir(parents=True, exist_ok=True)
        window.grab().save(str(output))
        hint = window.minimumSize()
        fits = window.scroll.widget().minimumSizeHint()
        print("windows composition (%s): %s (minimum %dx%d, no scrolling from %dx%d)" % (
            "usbip-win2 missing" if missing else "attached", output, hint.width(),
            hint.height(), fits.width(), fits.height()))
        if output.parent != ROOT / "docs":
            window.resize(hint)
            pump(0.2)
            window.grab().save(str(output.with_name(output.stem + "-minimum.png")))
        window.close()


def setup_render(output: Path) -> None:
    """The driver setup dialog for a fixture machine: unmodified driver installed."""
    app = QtWidgets.QApplication.instance()
    with tempfile.TemporaryDirectory(prefix="quadsens-demo-") as tmp:
        probe = demo_probe(Path(tmp, "root"), driver="stock")
        dialog = DriverSetupDialog(None, probe)
        dialog.resize(760, 640)
        dialog.show()
        for _ in range(5):
            app.processEvents()
        output.parent.mkdir(parents=True, exist_ok=True)
        dialog.grab().save(str(output))
        if output.parent != ROOT / "docs":   # the script page too, for checking by eye
            dialog.tabs.setCurrentIndex(1)
            for _ in range(3):
                app.processEvents()
            dialog.grab().save(str(output.with_name(output.stem + "-script.png")))
        dialog.close()
    print("driver setup: %s" % output)


def _options(argv: list[str]) -> tuple[list[str], str, bool]:
    """Strip --theme and --dark from the arguments."""
    rest: list[str] = []
    mode, dark = "quadsens", False
    skip = False
    for index, arg in enumerate(argv):
        if skip:
            skip = False
        elif arg == "--dark":
            mode, dark = "system", True
        elif arg == "--theme" and index + 1 < len(argv):
            mode, skip = argv[index + 1], True
        else:
            rest.append(arg)
    if mode not in ("quadsens", "system"):
        raise SystemExit("--theme must be quadsens or system")
    return rest, mode, dark


def main(argv: list[str]) -> int:
    argv, mode, dark = _options(argv)
    for flag, render in (("--diagrams", None), ("--gallery", None), ("--setup", None),
                         ("--checks", None), ("--windows", None),
                         ("--windows-missing", None)):
        if flag in argv:
            rest = [arg for arg in argv if arg != flag]
            if flag == "--setup" and len(rest) < 2:
                rest.append(str(ROOT / "docs" / "screenshot-driver-setup.png"))
            if flag == "--checks" and len(rest) < 2:
                rest.append("")
            if flag == "--windows" and len(rest) < 2:
                rest.append(str(ROOT / "docs" / "screenshot-windows.png"))
            if len(rest) < 2:
                print("usage: make_screenshot.py %s DIRECTORY" % flag, file=sys.stderr)
                return 2
            QtCore.QLocale.setDefault(QtCore.QLocale.c())
            app = QtWidgets.QApplication([rest[0]])
            manager = ThemeManager.instance()
            manager.set_mode(mode)
            if dark:
                app.setPalette(dark_palette())
                manager.apply()
            if flag == "--diagrams":
                diagram_renders(Path(rest[1]))
            elif flag == "--setup":
                setup_render(Path(rest[1]))
            elif flag == "--windows":
                windows_render(Path(rest[1]), mode)
            elif flag == "--windows-missing":
                windows_render(Path(rest[1]), mode, missing=True)
            elif flag == "--checks":
                check_renders(Path(rest[1]) if rest[1] else None, mode)
            else:
                gallery(Path(rest[1]), mode)
            return 0
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
    default_name = ("screenshot.png" if mode == "quadsens"
                    else "screenshot-system-dark.png" if dark
                    else "screenshot-system-light.png")
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
        settings.setValue("ui/theme", mode)

        config = Path(tmp, "yeetmouse.conf")
        config.write_text("sens=1\n", encoding="ascii")
        os.environ["QUADSENS_FAKE_PARAMS"] = str(params)
        saver = Saver(config, tool=ROOT / "tests" / "fake_yeetmousectl.py")

        window = MainWindow(settings, pattern, Driver(params), None, uhid_path=str(uhid),
                            saver=saver, setup_probe=demo_probe(Path(tmp, "machine")))
        window.resize(width, height)
        window.show()
        window.driver_strip.wait()

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
