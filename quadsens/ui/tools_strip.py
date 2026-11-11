"""First lines of the Windows window: the USB/IP driver and RawAccel.

One line per tool: whether it was found and in which version, and what to do
if not. Installing goes through the install dialog; this strip only asks for
it. A newer release of a tool is a quiet line with a link, nothing more.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .. import usbip_client, win_install, win_tools
from ..win_tools import PINS, RAWACCEL, USBIP
from .qt import QtCore, QtWidgets, Signal
from .status import SPACE_M, SPACE_S, ElideLabel, WrapLabel, caption
from .theme import set_primary

OK_MARK, MISSING_MARK, ATTENTION_MARK = "✓", "✕", "⚠"


@dataclass(frozen=True)
class RawAccelInfo:
    """What is known about RawAccel on this PC."""

    folder: str = ""                                  # the chosen folder, "" if none
    version: str = ""                                 # of the copy in it, "" if unknown
    driver: str = win_install.DRIVER_MISSING          # a win_install.DRIVER_* state


def usbip_line(found: bool, version: str) -> tuple[str, str, str]:
    """(mark, text, button label) for the USB/IP driver; "" for no button."""
    pin = PINS[USBIP]
    if not found:
        return MISSING_MARK, "usbip-win2 is not installed", "Install USB/IP driver…"
    text = "USB/IP driver found (usbip-win2%s)" % (" " + version if version else "")
    relation = win_tools.compare(version, pin)
    if relation == win_tools.OLDER:
        return (ATTENTION_MARK, text + "; QuadSens was tested with %s" % pin.version,
                "Install tested version…")
    if relation == win_tools.NEWER:
        text += "; newer than the %s QuadSens was tested with" % pin.version
    return OK_MARK, text, ""


def rawaccel_line(info: RawAccelInfo) -> tuple[str, str, str]:
    """(mark, text, button label) for RawAccel."""
    pin = PINS[RAWACCEL]
    install = "Install RawAccel…"
    name = "RawAccel" + (" " + info.version if info.version else "")
    where = " in %s" % info.folder if info.folder else ""
    if info.driver == win_install.DRIVER_PENDING:
        return (ATTENTION_MARK, "%s: restart Windows to finish installing its driver; Apply "
                "works after that%s" % (name, where and " (%s)" % info.folder), "")
    if info.driver == win_install.DRIVER_MISSING:
        if not info.folder:
            return MISSING_MARK, "RawAccel is not installed, or its folder is not chosen", \
                install
        return ATTENTION_MARK, "%s%s, but its driver is not installed" % (name, where), install
    if not info.folder:
        return (ATTENTION_MARK, "The RawAccel driver is installed; choose the folder that "
                "holds rawaccel.exe", "")
    text = "%s found%s" % (name, where)
    if info.driver == win_install.DRIVER_UNKNOWN:
        text += "; whether Windows was restarted since its driver was installed is not known"
    relation = win_tools.compare(info.version, pin) if info.version else win_tools.UNKNOWN
    if relation == win_tools.OLDER:
        return (ATTENTION_MARK, text + "; QuadSens was tested with %s" % pin.version,
                "Install tested version…")
    if relation == win_tools.NEWER:
        text += "; newer than the %s QuadSens was tested with" % pin.version
    return OK_MARK, text, ""


class _Line:
    """Mark, text and buttons of one tool."""

    def __init__(self, name: str) -> None:
        self.mark = QtWidgets.QLabel()
        font = self.mark.font()
        font.setBold(True)
        self.mark.setFont(font)
        self.mark.setFixedWidth(self.mark.fontMetrics().horizontalAdvance(ATTENTION_MARK) + 4)
        self.mark.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.headline = ElideLabel()
        self.headline.setAccessibleName(name)
        self.install_button = QtWidgets.QPushButton()
        self.install_button.setAccessibleName("Install %s" % name)
        self.layout = QtWidgets.QHBoxLayout()
        self.layout.setSpacing(SPACE_M)
        self.layout.addWidget(self.mark)
        self.layout.addWidget(self.headline, 1)
        self.layout.addWidget(self.install_button)

    def show(self, mark: str, text: str, button: str, hint: str) -> None:
        self.mark.setText(mark)
        self.headline.setText(text)
        self.install_button.setText(button)
        self.install_button.setVisible(bool(button))
        self.install_button.setToolTip(hint)
        self.install_button.setAccessibleDescription(hint)


class ToolsStrip(QtWidgets.QFrame):
    install_requested = Signal(str)      # USBIP or RAWACCEL
    choose_folder_requested = Signal()
    link_requested = Signal(str)         # a release page
    checked = Signal()

    def __init__(self, find_client: Callable[[], usbip_client.UsbipClient],
                 rawaccel_info: Callable[[], RawAccelInfo],
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._find_client = find_client
        self._rawaccel_info = rawaccel_info
        self.found = False               # usbip.exe was found
        self.version = ""
        self.rawaccel = RawAccelInfo()
        self._notices: dict[str, tuple[str, str]] = {}     # key -> (text, link)

        self.usbip = _Line("USB/IP driver")
        self.raw = _Line("RawAccel")
        # Names the rest of the window and the tests know.
        self.mark, self.headline, self.get_button = (
            self.usbip.mark, self.usbip.headline, self.usbip.install_button)
        self.usbip.install_button.clicked.connect(lambda: self.install_requested.emit(USBIP))
        self.raw.install_button.clicked.connect(lambda: self.install_requested.emit(RAWACCEL))
        self.choose_button = QtWidgets.QPushButton("Choose folder…")
        self.choose_button.setAccessibleName("Choose an existing RawAccel folder")
        self.choose_button.setToolTip(
            "Pick the folder of a RawAccel you unpacked yourself (it holds rawaccel.exe).")
        self.choose_button.setAccessibleDescription(self.choose_button.toolTip())
        self.choose_button.clicked.connect(self.choose_folder_requested.emit)
        # Mirror of the indicator in the RawAccel card: is the driver applying values?
        self.active_label = ElideLabel()
        self.active_label.setAccessibleName("Whether RawAccel's driver is applying values")
        self.raw.layout.addWidget(self.active_label)
        self.raw.layout.addWidget(self.choose_button)

        self.recheck_button = QtWidgets.QToolButton()
        self.recheck_button.setText("Chec&k again")
        self.recheck_button.setAutoRaise(True)
        self.recheck_button.setFocusPolicy(QtCore.Qt.FocusPolicy.TabFocus)
        self.recheck_button.setAccessibleName("Look for the USB/IP driver and RawAccel again")
        self.recheck_button.setToolTip("Look for usbip-win2 and RawAccel again")
        self.recheck_button.setAccessibleDescription(self.recheck_button.toolTip())
        self.recheck_button.clicked.connect(self.check)
        self.usbip.layout.addWidget(self.recheck_button)

        self.detail = caption(WrapLabel("About the USB/IP driver"))
        self.notice_label = caption(WrapLabel("Newer versions of the tools"))
        self.link_buttons: dict[str, QtWidgets.QToolButton] = {}
        notice_row = QtWidgets.QHBoxLayout()
        notice_row.setSpacing(SPACE_M)
        notice_row.addWidget(self.notice_label, 1)
        for key in (USBIP, RAWACCEL):
            button = QtWidgets.QToolButton()
            button.setText("%s releases…" % PINS[key].title)
            button.setAutoRaise(True)
            button.setFocusPolicy(QtCore.Qt.FocusPolicy.TabFocus)
            button.setToolTip("Open the release page of %s in your browser"
                              % PINS[key].title)
            button.setAccessibleDescription(button.toolTip())
            button.clicked.connect(lambda _=False, k=key: self.link_requested.emit(
                self._notices.get(k, ("", PINS[k].releases_url))[1]))
            button.hide()
            notice_row.addWidget(button)
            self.link_buttons[key] = button

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(2)
        layout.setContentsMargins(SPACE_S, 2, SPACE_S, 2)
        layout.addLayout(self.usbip.layout)
        layout.addWidget(self.detail)
        layout.addLayout(self.raw.layout)
        layout.addLayout(notice_row)
        self.setProperty("banner", "")
        self.check()

    def buttons(self) -> list[QtWidgets.QWidget]:
        """In tab order."""
        return [self.usbip.install_button, self.recheck_button, self.raw.install_button,
                self.choose_button, *self.link_buttons.values()]

    def set_active(self, text: str, detail: str = "") -> None:
        """Show the short form of the RawAccel card's indicator."""
        self.active_label.setText(text)
        self.active_label.setToolTip(detail)

    def set_notice(self, key: str, text: str, link: str = "") -> None:
        """Show (or with "" remove) the line about a newer release of a tool."""
        if text:
            self._notices[key] = (text, link or PINS[key].releases_url)
        else:
            self._notices.pop(key, None)
        self._show_notices()

    def _show_notices(self) -> None:
        self.notice_label.setText(" ".join(text for text, _link in self._notices.values()))
        self.notice_label.setVisible(bool(self._notices))
        for key, button in self.link_buttons.items():
            button.setVisible(key in self._notices)

    def check(self) -> None:
        """Look for both tools again."""
        self._check_usbip()
        self.refresh_rawaccel()

    def _check_usbip(self) -> None:
        client = self._find_client()
        self.found = client.installed
        self.version = client.version() if client.installed else ""
        mark, text, button = usbip_line(self.found, self.version)
        self.usbip.show(mark, text, button,
                        "Download the tested usbip-win2 installer, check it and start it. "
                        "You are asked before anything happens.")
        self.detail.setText(
            "" if self.found else
            "The virtual mouse needs this USB/IP driver (usbip.exe was not found). Its "
            "installer asks for administrator rights and restarts the USB hubs. Without "
            "it you can still import or type the values and apply them.")
        self.detail.setVisible(not self.found)

    def refresh_rawaccel(self) -> None:
        """Look at RawAccel again (usbip.exe is not asked) and redraw the strip."""
        self.rawaccel = self._rawaccel_info()
        mark, text, button = rawaccel_line(self.rawaccel)
        self.raw.show(mark, text, button,
                      "Download the tested RawAccel, check it, unpack it and start its "
                      "driver installer. You are asked before anything happens.")
        self.choose_button.setVisible(not self.rawaccel.folder)
        for line in (self.usbip, self.raw):
            set_primary(line.install_button, False)
        self._show_notices()
        problem = MISSING_MARK in (self.usbip.mark.text(), self.raw.mark.text()) \
            or ATTENTION_MARK in (self.usbip.mark.text(), self.raw.mark.text())
        self.setAccessibleName("Tools check: %s. %s. %s. %s" % (
            "needs attention" if problem else "passed", self.usbip.headline.text(),
            self.raw.headline.text(), self.detail.text()))
        banner = "warning" if not self.found else ""
        if self.property("banner") != banner:
            self.setProperty("banner", banner)
            style = self.style()
            style.unpolish(self)
            style.polish(self)
            self.update()
        self.checked.emit()
