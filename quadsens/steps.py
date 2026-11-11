"""The guided order of work, derived from the state of the application (no Qt).

Six steps lead from a fresh start to a saved calibration. Which of them are
done is worked out from facts (is the virtual mouse running, what does the
driver report, is there a result, ...), never from which buttons were pressed,
so doing things in another order or outside the application is picked up too.
Nothing here forbids an action; a step only reports why its action cannot work
right now.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .model import Scalars
from .persist import SaveState

UNITY_TOLERANCE = 5e-7  # "exactly 1" for values reported with six decimals


class Step(enum.Enum):
    START = 1
    RESET = 2
    CALIBRATE = 3
    USE = 4
    APPLY = 5
    SAVE = 6


class Status(enum.Enum):
    DONE = "done"
    CURRENT = "current"
    PENDING = "pending"


# Marker shown in front of a step, so that the state never depends on colour.
MARKERS = {Status.DONE: "✓", Status.CURRENT: "▶", Status.PENDING: "○"}

SHORT_TITLES = {
    Step.START: "Start mouse",
    Step.RESET: "Reset to 1.0",
    Step.CALIBRATE: "Calibrate",
    Step.USE: "Use result",
    Step.APPLY: "Apply",
    Step.SAVE: "Save",
}
TITLES = {
    Step.START: "Start the virtual mouse",
    Step.RESET: "Reset the driver values to 1.0",
    Step.CALIBRATE: "Run the calibration in the game",
    Step.USE: "Use the result",
    Step.APPLY: "Apply to the driver",
    Step.SAVE: "Save to config",
}
# Label of the button that carries out a step; CALIBRATE happens in the game.
ACTIONS = {
    Step.START: "Start virtual mouse",
    Step.RESET: "Reset to 1.0 and apply",
    Step.USE: "Use result",
    Step.APPLY: "Apply",
    Step.SAVE: "Save to config",
}


@dataclass(frozen=True)
class FlowState:
    """Everything the step display depends on."""

    mouse_running: bool = False
    mouse_problem: str = ""            # why the virtual mouse cannot be started, or ""
    driver: Scalars | None = None      # values the driver uses, None if unknown
    driver_problem: str = ""           # why nothing can be written to the driver, or ""
    result_available: bool = False     # there is a calibration result to take values from
    result_is_new: bool = False        # ... and it arrived after the mouse start / reset
    result_in_editor: bool = False     # the new values are the result's values
    result_in_driver: bool = False     # the driver uses the result's values
    save_state: SaveState = SaveState.UNKNOWN
    save_problem: str = ""             # why saving cannot work, or ""


@dataclass(frozen=True)
class StepInfo:
    step: Step
    status: Status
    detail: str          # one sentence: what to do, or what was found
    blocked: str = ""    # why the step's action cannot work right now, or ""

    @property
    def number(self) -> int:
        return int(self.step.value)

    @property
    def marker(self) -> str:
        return MARKERS[self.status]

    @property
    def action(self) -> str:
        """Button label for the step, "" if there is nothing to press."""
        return ACTIONS.get(self.step, "")

    def chip(self) -> str:
        """Compact form for the step row, e.g. "✓ 1 Start mouse"."""
        return f"{self.marker} {self.number} {SHORT_TITLES[self.step]}"

    def describe(self) -> str:
        """Full form for screen readers and tooltips."""
        text = f"Step {self.number} of {len(Step)}, {TITLES[self.step]}: {self.status.value}."
        return f"{text} {self.detail}" if self.detail else text


def is_unity(scalars: Scalars | None) -> bool:
    return scalars is not None and scalars.is_close(Scalars.unity(), UNITY_TOLERANCE)


def done_steps(state: FlowState) -> set[Step]:
    """The steps whose outcome is in place, whichever way it got there."""
    done: set[Step] = set()
    saved = state.result_in_driver and state.save_state is SaveState.SAVED
    calibrated = state.result_is_new or state.result_in_editor or state.result_in_driver
    if state.mouse_running or calibrated:
        done.add(Step.START)
    if is_unity(state.driver) or calibrated:
        done.add(Step.RESET)
    if calibrated:
        done.add(Step.CALIBRATE)
    if state.result_in_editor or state.result_in_driver:
        done.add(Step.USE)
    if state.result_in_driver:
        done.add(Step.APPLY)
    if saved:
        done.add(Step.SAVE)
    return done


def _detail(step: Step, done: bool, state: FlowState) -> str:
    if step is Step.START:
        if state.mouse_running:
            return "The virtual mouse is running. Start the game now if it is not open."
        return ("Not needed any more for this result." if done
                else "Start the virtual mouse first, then the game.")
    if step is Step.RESET:
        if is_unity(state.driver):
            return "All four driver values are 1: the calibration measures the unscaled mouse."
        if done:
            return "The driver holds calibrated values."
        if state.driver is None:
            return "Set all four driver values to 1 before calibrating (current values unknown)."
        return "Set all four driver values to 1, so that the calibration measures the raw mouse."
    if step is Step.CALIBRATE:
        if state.result_is_new:
            return "A new calibration result has arrived."
        if done:
            return "Using the calibration result that was already there."
        return "Run the per-direction calibration in the game; the result shows up here."
    if step is Step.USE:
        if done:
            return "The new values are the calibration result."
        if state.result_available:
            return "Take the four values of the calibration result as new driver values."
        return "Needs a calibration result."
    if step is Step.APPLY:
        if done:
            return "The driver uses the calibration result."
        return "Write the new values to the driver."
    if done:
        return "The config file holds the driver's values: they survive a module reload."
    return "Applied, not saved: the values are lost on module reload. Save them to the config."


def _blocked(step: Step, state: FlowState) -> str:
    if step is Step.START:
        return "" if state.mouse_running else state.mouse_problem
    if step in (Step.RESET, Step.APPLY):
        return state.driver_problem
    if step is Step.USE:
        return "" if state.result_available else "There is no calibration result yet."
    if step is Step.SAVE:
        return state.save_problem or state.driver_problem
    return ""


def derive(state: FlowState) -> list[StepInfo]:
    """All six steps in order; the first one that is not done is the current one."""
    done = done_steps(state)
    steps: list[StepInfo] = []
    current_given = False
    for step in Step:
        if step in done:
            status = Status.DONE
        elif not current_given:
            status, current_given = Status.CURRENT, True
        else:
            status = Status.PENDING
        steps.append(StepInfo(step, status, _detail(step, step in done, state),
                              "" if step in done else _blocked(step, state)))
    return steps


def current_step(steps: list[StepInfo]) -> StepInfo | None:
    """The step to do next, None when everything is done."""
    for info in steps:
        if info.status is Status.CURRENT:
            return info
    return None


ALL_DONE = "All six steps are done: the calibration is applied and saved."
