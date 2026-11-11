"""The driver check on top of the main window: one line when all is well."""

from __future__ import annotations

import threading

from .. import driver_setup as ds
from .qt import QtCore, QtWidgets, Signal
from .status import SPACE_M, SPACE_S, WrapLabel, caption
from .theme import set_primary

BANNERS = {ds.PASSED: "", ds.CHECKING: "", ds.FIXABLE: "error", ds.ATTENTION: "warning"}


class _Bridge(QtCore.QObject):
    """Carries results from the checking threads into the GUI thread.

    It lives as long as the application. A thread never touches a strip, which
    may be deleted while the thread is still looking; it only emits here, and
    the bridge hands the result to the strip if that still exists.
    """

    result = Signal(int, int, object, object)   # strip id, generation, state, checks

    def __init__(self) -> None:
        super().__init__()
        self.strips: dict[int, DriverCheckStrip] = {}
        self.result.connect(self._deliver)

    def _deliver(self, strip_id: int, generation: int, state: object, checks: object) -> None:
        strip = self.strips.get(strip_id)
        if strip is not None:
            strip._finished(generation, state, checks)


_bridge: _Bridge | None = None
_ids = iter(range(1, 1 << 62))


def _get_bridge() -> _Bridge:
    global _bridge
    if _bridge is None:
        _bridge = _Bridge()
        app = QtCore.QCoreApplication.instance()
        if app is not None:
            _bridge.setParent(app)
    return _bridge


class DriverCheckStrip(QtWidgets.QFrame):
    """Says whether the driver is ready, and offers the way to fix it if not.

    Passed takes one line: a check mark, "Driver check passed", a muted detail
    and a quiet "Details" button. Anything else shows a mark, the problem in
    plain words, what to do about it and, where the driver setup can help, one
    button that opens it. The button never starts anything by itself.

    Looking at the system means asking DKMS and the package manager, which can
    take a moment; that happens in a thread and the strip says "Checking"
    meanwhile.
    """

    # The user asked for the driver setup: ds.ACT_FIX, ds.ACT_CHECKLIST or ds.ACT_DETAILS.
    action_requested = Signal(str)
    # A check finished and the strip shows its result.
    checked = Signal()

    def __init__(self, probe: ds.Probe | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._probe = probe or ds.Probe()
        self._generation = 0
        self._id = next(_ids)
        self._bridge = _get_bridge()
        self._bridge.strips[self._id] = self
        self.destroyed.connect(lambda *_a, i=self._id, b=self._bridge: b.strips.pop(i, None))
        self._running = False
        self._again = False
        self._readable: bool | None = None
        self.state: ds.SetupState | None = None
        self.checks: list[ds.Check] = []
        self.strip: ds.Strip = ds.CHECKING_STRIP
        try:
            self._pin: ds.Pin | None = ds.pin()
        except ds.SetupError:
            self._pin = None

        self.mark = QtWidgets.QLabel()
        self.mark.setObjectName("bannerMark")
        font = self.mark.font()
        font.setBold(True)
        self.mark.setFont(font)
        self.mark.setFixedWidth(self.mark.fontMetrics().horizontalAdvance("⚠") + 4)
        self.mark.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.headline = WrapLabel("Driver check")
        font = self.headline.font()
        font.setWeight(font.Weight.DemiBold)
        self.headline.setFont(font)
        # In the passed state the detail sits beside the headline; otherwise below it.
        self.side_detail = caption(QtWidgets.QLabel())
        self.side_detail.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                       QtWidgets.QSizePolicy.Policy.Preferred)
        self.passed_headline = QtWidgets.QLabel()
        self.passed_headline.setFont(font)
        self.detail = caption(WrapLabel("Driver check detail"))

        self.action_button = QtWidgets.QPushButton(ds.ACTION_LABELS[ds.ACT_FIX])
        self.action_button.setAccessibleName("Fix driver setup")
        self.action_button.setAccessibleDescription(
            "Open the driver setup. It shows what it will do and asks before it changes "
            "anything.")
        self.action_button.clicked.connect(self._act)
        self.details_button = QtWidgets.QToolButton()
        self.details_button.setText("Details")
        self.details_button.setAutoRaise(True)
        self.details_button.setFocusPolicy(QtCore.Qt.FocusPolicy.TabFocus)
        self.details_button.setAccessibleName("Driver check details")
        self.details_button.setToolTip(
            "Open the driver setup: what is installed, and install or revert the fork")
        self.details_button.setAccessibleDescription(self.details_button.toolTip())
        self.details_button.clicked.connect(
            lambda: self.action_requested.emit(ds.ACT_DETAILS))
        self.recheck_button = QtWidgets.QToolButton()
        self.recheck_button.setText("Chec&k again")
        self.recheck_button.setAutoRaise(True)
        self.recheck_button.setFocusPolicy(QtCore.Qt.FocusPolicy.TabFocus)
        self.recheck_button.setAccessibleName("Check the driver again")
        self.recheck_button.setToolTip("Look at the installed driver again")
        self.recheck_button.setAccessibleDescription(self.recheck_button.toolTip())
        self.recheck_button.clicked.connect(self.check)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(SPACE_M)
        top.addWidget(self.mark)
        top.addWidget(self.passed_headline)
        top.addWidget(self.side_detail, 1)
        top.addWidget(self.headline, 1)
        top.addWidget(self.action_button)
        top.addWidget(self.details_button)
        top.addWidget(self.recheck_button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(SPACE_S, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addWidget(self.detail)

        self.setProperty("banner", "")
        self._show(ds.CHECKING_STRIP)

    # ---- checking -------------------------------------------------------------

    def is_checking(self) -> bool:
        return self._running

    def passed(self) -> bool:
        return self.strip.kind == ds.PASSED

    def blocks_steps(self) -> bool:
        """True while the check says the driver is not ready."""
        return self.strip.kind in (ds.FIXABLE, ds.ATTENTION)

    def set_readable(self, readable: bool | None) -> None:
        """What the driver panel found; refines the result without a new check."""
        if readable != self._readable:
            self._readable = readable
            if self.state is not None:
                self._show(ds.strip(self.state, self.checks, self._pin, readable))
                self.checked.emit()

    def check(self) -> None:
        """Look at the system in the background; one check at a time."""
        if self._pin is None:
            self._show(ds.Strip(ds.ATTENTION, "The driver setup script is missing.",
                                "This copy of QuadSens is incomplete."))
            return
        if self._running:
            self._again = True
            return
        self._running = True
        self._generation += 1
        generation, probe, pinned = self._generation, self._probe, self._pin
        strip_id, bridge = self._id, self._bridge
        if self.state is None:
            self._show(ds.CHECKING_STRIP)
        self.recheck_button.setEnabled(False)

        def work() -> None:
            try:
                state: ds.SetupState | None = ds.detect(probe, pinned)
                checks = ds.prerequisites(probe)
            except Exception:  # noqa: BLE001 - a failed look must not kill the thread silently
                state, checks = None, []
            try:
                bridge.result.emit(strip_id, generation, state, checks)
            except RuntimeError:
                pass  # the application is shutting down

        threading.Thread(target=work, name="driver-check", daemon=True).start()

    def wait(self, seconds: float = 10.0) -> None:
        """Process events until the running check is done (tests and screenshots)."""
        import time
        deadline = time.monotonic() + seconds
        app = QtCore.QCoreApplication.instance()
        while (self._running or self._again) and time.monotonic() < deadline:
            app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)

    def _finished(self, generation: int, state: object, checks: object) -> None:
        if generation != self._generation:
            return
        self._running = False
        self.recheck_button.setEnabled(True)
        if isinstance(state, ds.SetupState):
            self.state = state
            self.checks = list(checks) if isinstance(checks, list) else []
            self._show(ds.strip(state, self.checks, self._pin, self._readable))
        else:
            self._show(ds.Strip(ds.ATTENTION, "The driver could not be checked.",
                                "Press Check again, or open the details."))
        self.checked.emit()
        if self._again:
            self._again = False
            self.check()

    # ---- showing --------------------------------------------------------------

    def _act(self) -> None:
        if self.strip.action:
            self.action_requested.emit(self.strip.action)

    def _show(self, strip: ds.Strip) -> None:
        self.strip = strip
        compact = strip.kind in (ds.PASSED, ds.CHECKING)
        self.mark.setText(strip.mark)
        self.passed_headline.setText(strip.headline)
        self.side_detail.setText(strip.detail)
        self.headline.setText(strip.headline)
        self.detail.setText(strip.detail)
        self.passed_headline.setVisible(compact)
        self.side_detail.setVisible(compact)
        self.headline.setVisible(not compact)
        self.detail.setVisible(not compact and bool(strip.detail))
        button = strip.action in (ds.ACT_FIX, ds.ACT_CHECKLIST)
        self.action_button.setVisible(button)
        if button:
            self.action_button.setText(strip.action_label)
            self.action_button.setAccessibleName(strip.action_label.replace("&", ""))
            hint = ("Open the driver setup. It shows what it will do and asks before it "
                    "changes anything.")
            self.action_button.setToolTip(hint)
            self.action_button.setAccessibleDescription(hint)
        set_primary(self.action_button, strip.action == ds.ACT_FIX)
        self.details_button.setVisible(strip.kind != ds.CHECKING)
        margins = (SPACE_S, 0, 0, 0) if compact else (SPACE_M, 3, SPACE_M, 4)
        self.layout().setContentsMargins(*margins)
        self.setAccessibleName(strip.describe())
        self.setToolTip(strip.detail if compact else "")
        banner = BANNERS[strip.kind]
        if self.property("banner") != banner:
            self.setProperty("banner", banner)
            style = self.style()
            style.unpolish(self)
            style.polish(self)
            self.update()
