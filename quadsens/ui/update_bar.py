"""The notice about a newer QuadSens: a line on top of the window, and its check.

The check runs in a thread and is taken from a queue by a timer, so the
window never waits for the network. A check QuadSens starts itself is silent
unless there is a newer version; only a check the user asked for answers in
every case. Nothing is downloaded or installed from here: the button opens
the release page in the browser.
"""

from __future__ import annotations

import html
import queue
import threading
from collections.abc import Callable, Mapping

from .. import __version__, update_check
from .qt import QtCore, QtGui, QtWidgets, Signal
from .status import BANNER_MARKS, SPACE_M, SPACE_S, WrapLabel, caption

ONLINE_KEY = "tools/online_check"       # one switch for everything asked online
SKIP_KEY = "update/skipped_version"
ENV_OFF = "QUADSENS_NO_ONLINE_CHECK"
ONLINE_ACTION_TEXT = "Check for newer versions &online"
UPDATE_NOTE = (
    "When it starts, QuadSens asks api.github.com for the newest release of QuadSens, "
    "to tell you when there is a newer one. Nothing else is sent and nothing is "
    "downloaded or installed by that. Help > Check for newer versions online switches "
    "it off.")


class UpdateBar(QtWidgets.QFrame):
    """One line: which version is available, and the two things to do about it."""

    open_requested = Signal()
    skip_requested = Signal()

    def __init__(self, system: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.mark = QtWidgets.QLabel(BANNER_MARKS["info"])
        self.mark.setObjectName("bannerMark")
        font = self.mark.font()
        font.setBold(True)
        self.mark.setFont(font)
        self.headline = WrapLabel("Newer version of QuadSens")
        self.detail = caption(WrapLabel("How to get the newer version"))
        self.detail.setText(update_check.how_to_update(system))
        self.open_button = QtWidgets.QPushButton("Open release page")
        self.open_button.setToolTip("Open the release page of the newer version in your "
                                    "browser. Nothing is downloaded by QuadSens.")
        self.open_button.setAccessibleDescription(self.open_button.toolTip())
        self.open_button.clicked.connect(self.open_requested.emit)
        self.skip_button = QtWidgets.QPushButton("Skip this version")
        self.skip_button.setToolTip("Hide this line until a version newer than this one "
                                    "is released.")
        self.skip_button.setAccessibleDescription(self.skip_button.toolTip())
        self.skip_button.clicked.connect(self.skip_requested.emit)

        texts = QtWidgets.QVBoxLayout()
        texts.setSpacing(0)
        texts.addWidget(self.headline)
        texts.addWidget(self.detail)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(SPACE_M - 2, 3, SPACE_M, 3)
        layout.setSpacing(SPACE_S)
        layout.addWidget(self.mark, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addLayout(texts, 1)
        layout.addWidget(self.open_button)
        layout.addWidget(self.skip_button)
        self.setProperty("banner", "info")
        self.hide()

    def show_version(self, result: update_check.Result) -> None:
        text = update_check.headline(result, __version__)
        self.headline.setText(text)
        self.setAccessibleName(text)
        self.show()


class UpdateNotice:
    """Mixin for a main window: the bar, the check and the switch for it.

    The window calls init_update_notice() once and puts ``update_bar`` on top
    of its page. It needs ``_settings``, ``open_url`` and ``_open_box``.
    """

    _settings: QtCore.QSettings
    open_url: Callable[[QtCore.QUrl], object]

    def init_update_notice(self, check: Callable[[], update_check.Result] | None,
                           environ: Mapping[str, str], system: str) -> None:
        self._update_check = check                 # None: this window never asks
        self._update_environ = environ
        self.update_result: update_check.Result | None = None
        self._update_thread: threading.Thread | None = None
        self._update_results: queue.Queue[tuple[update_check.Result, bool]] = queue.Queue()
        self.update_bar = UpdateBar(system)
        self.update_bar.open_requested.connect(self.open_update_page)
        self.update_bar.skip_requested.connect(self.skip_update)
        self._update_timer = QtCore.QTimer(self)   # type: ignore[arg-type]
        self._update_timer.setInterval(200)
        self._update_timer.timeout.connect(self._take_update_results)

    def add_update_action(self, menu: QtWidgets.QMenu) -> None:
        self.update_action = QtGui.QAction("Check for &updates now", self)  # type: ignore[arg-type]
        self.update_action.setToolTip("Ask api.github.com whether there is a newer QuadSens")
        self.update_action.triggered.connect(
            lambda _checked=False: self.check_for_update(manual=True))
        menu.addAction(self.update_action)

    def add_online_switch(self, menu: QtWidgets.QMenu, note: str) -> None:
        self.online_action = QtGui.QAction(ONLINE_ACTION_TEXT, self)  # type: ignore[arg-type]
        self.online_action.setCheckable(True)
        self.online_action.setChecked(self.online_check_enabled())
        self.online_action.setToolTip(note)
        self.online_action.toggled.connect(self.set_online_check)
        menu.addAction(self.online_action)

    def online_check_enabled(self) -> bool:
        if self._update_environ.get(ENV_OFF):
            return False
        return bool(self._settings.value(ONLINE_KEY, True, type=bool))

    def set_online_check(self, enabled: bool) -> None:
        self._settings.setValue(ONLINE_KEY, enabled)
        if enabled:
            self.check_for_update()
        else:
            self.update_bar.hide()

    def check_for_update(self, manual: bool = False) -> bool:
        """Ask in the background whether there is a newer QuadSens.

        Returns whether the question was sent. A check the user asked for is
        made even when the automatic one is switched off, and always answers.
        """
        if self._update_check is None or not (manual or self.online_check_enabled()) \
                or (self._update_thread is not None and self._update_thread.is_alive()):
            return False
        ask = self._update_check

        def work() -> None:
            try:
                result = ask()
            except Exception as exc:      # noqa: BLE001 - a notice is never worth an error
                result = update_check.Result(update_check.FAILED, reason="%s." % exc)
            self._update_results.put((result, manual))
        self._update_thread = threading.Thread(target=work, name="update-check", daemon=True)
        self._update_thread.start()
        self._update_timer.start()
        return True

    def _take_update_results(self) -> None:
        while True:
            try:
                result, manual = self._update_results.get_nowait()
            except queue.Empty:
                break
            self._show_update_result(result, manual)
        if self._update_thread is None or not self._update_thread.is_alive():
            if self._update_results.empty():
                self._update_timer.stop()

    def _show_update_result(self, result: update_check.Result, manual: bool) -> None:
        if result.kind == update_check.NEWER:
            skipped = str(self._settings.value(SKIP_KEY, "", type=str))
            if manual or (self.online_check_enabled() and result.version != skipped):
                self.update_result = result
                self.update_bar.show_version(result)
            return
        if result.kind == update_check.CURRENT:
            self.update_bar.hide()
        if manual:
            self._open_box("Updates", "<p>%s</p>" % html.escape(     # type: ignore[attr-defined]
                update_check.manual_answer(result, __version__)))

    def open_update_page(self) -> None:
        if self.update_result is not None:
            self.open_url(QtCore.QUrl(self.update_result.url))

    def skip_update(self) -> None:
        if self.update_result is not None:
            self._settings.setValue(SKIP_KEY, self.update_result.version)
        self.update_bar.hide()
