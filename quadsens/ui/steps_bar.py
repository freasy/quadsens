"""The row of six steps on top of the window, with the button for the next one."""

from __future__ import annotations

from ..steps import ALL_DONE, Status, Step, StepInfo, current_step
from .qt import QtCore, QtGui, QtWidgets, Signal
from .status import SPACE_L, SPACE_M, SPACE_S, WrapLabel


class StepsBar(QtWidgets.QWidget):
    """Shows done / current / pending for every step and offers the next action.

    Each step carries a marker (✓ done, ▶ current, ○ pending) and the current
    one is bold, so the state does not depend on colour. The button on the
    right always carries out the current step; it is the window's primary
    button. The buttons inside the panels keep working in any order.
    """

    action_requested = Signal(object)  # Step

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Steps")
        self._current: StepInfo | None = None
        self.infos: list[StepInfo] = []

        self.chips: list[QtWidgets.QLabel] = []
        chips = QtWidgets.QHBoxLayout()
        chips.setSpacing(SPACE_L)
        for _step in Step:
            chip = QtWidgets.QLabel()
            self.chips.append(chip)
            chips.addWidget(chip)
        chips.addStretch(1)

        self.next_label = WrapLabel("Next step")
        self.next_button = QtWidgets.QPushButton()
        self.next_button.setAutoDefault(True)
        self.next_button.setDefault(True)
        font = self.next_button.font()
        font.setBold(True)
        self.next_button.setFont(font)
        self.next_button.setShortcut(QtGui.QKeySequence("Ctrl+Return"))
        self.next_button.clicked.connect(self._clicked)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(SPACE_M)
        row.addWidget(self.next_label, 1, QtCore.Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.next_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)
        layout.addLayout(chips)
        layout.addLayout(row)

    def current(self) -> StepInfo | None:
        return self._current

    def set_steps(self, infos: list[StepInfo]) -> None:
        self.infos = list(infos)
        for chip, info in zip(self.chips, infos):
            chip.setText(info.chip())
            font = chip.font()
            font.setBold(info.status is Status.CURRENT)
            chip.setFont(font)
            chip.setEnabled(info.status is not Status.PENDING)
            chip.setAccessibleName(info.describe())
            chip.setToolTip(info.describe())
        self._current = current = current_step(infos)
        if current is None:
            self.next_label.setText(ALL_DONE)
            self.next_button.setText("Done")
            self.next_button.setEnabled(False)
            self.next_button.setAccessibleName("Next step: nothing left to do")
        else:
            text = f"Step {current.number} of {len(infos)}: {current.blocked or current.detail}"
            self.next_label.setText(text)
            action = current.action
            self.next_button.setText(action or "Waiting for the result")
            self.next_button.setEnabled(bool(action) and not current.blocked)
            self.next_button.setAccessibleName(
                f"Next step: {action}" if action else "Next step: waiting for the result")
        self.next_button.setShortcut(QtGui.QKeySequence("Ctrl+Return"))
        self.next_button.setToolTip(
            "Carries out the current step (Ctrl+Return). The buttons below do the same "
            "and can be used in any order.")
        self.next_button.setAccessibleDescription(self.next_label.text())

    def _clicked(self) -> None:
        if self._current is not None and self._current.action:
            self.action_requested.emit(self._current.step)
