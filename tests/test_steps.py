"""Tests of the guided step order (no Qt)."""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from quadsens.calibration import SOURCE_FILE, SOURCE_MOUSE, Calibration, pick_result
from quadsens.emulator import (
    P_DIR_SENS_ENABLED,
    P_DIR_SENS_X_POS,
    float_to_bits,
    summarize_entry,
)
from quadsens.model import Scalars, format_deviation
from quadsens.persist import SaveState
from quadsens.steps import (
    ACTIONS,
    FlowState,
    Status,
    Step,
    current_step,
    derive,
    done_steps,
    is_unity,
)

RESULT = Scalars(1.0375, 0.9625, 1.125, 0.875)


def statuses(state: FlowState) -> str:
    """One letter per step: d(one), C(urrent), p(ending)."""
    letters = {Status.DONE: "d", Status.CURRENT: "C", Status.PENDING: "p"}
    return "".join(letters[info.status] for info in derive(state))


class FlowTest(unittest.TestCase):
    def test_walk_through_in_order(self) -> None:
        state = FlowState(driver=RESULT, save_state=SaveState.DIFFERS)
        self.assertEqual(statuses(state), "Cppppp")
        state = replace(state, mouse_running=True)
        self.assertEqual(statuses(state), "dCpppp")
        state = replace(state, driver=Scalars.unity())
        self.assertEqual(statuses(state), "ddCppp")
        state = replace(state, result_available=True, result_is_new=True)
        self.assertEqual(statuses(state), "dddCpp")
        state = replace(state, result_in_editor=True)
        self.assertEqual(statuses(state), "ddddCp")
        state = replace(state, driver=RESULT, result_in_driver=True)
        self.assertEqual(statuses(state), "dddddC")
        state = replace(state, save_state=SaveState.SAVED)
        self.assertEqual(statuses(state), "dddddd")
        self.assertIsNone(current_step(derive(state)))

    def test_fresh_start_with_unit_driver(self) -> None:
        state = FlowState(driver=Scalars.unity())
        self.assertEqual(statuses(state), "Cdpppp")  # nothing to reset, still start first
        current = current_step(derive(state))
        assert current is not None
        self.assertIs(current.step, Step.START)
        self.assertEqual(current.action, "Start virtual mouse")

    def test_out_of_order_use_of_an_older_result(self) -> None:
        # After a reboot without saving: driver at 1, an old result file, mouse stopped.
        state = FlowState(driver=Scalars.unity(), result_available=True,
                          save_state=SaveState.DIFFERS)
        self.assertEqual(statuses(state), "Cdpppp")
        state = replace(state, result_in_editor=True)   # pressed Use result directly
        self.assertEqual(statuses(state), "ddddCp")
        state = replace(state, driver=RESULT, result_in_driver=True)
        self.assertEqual(statuses(state), "dddddC")

    def test_returning_with_everything_in_place(self) -> None:
        state = FlowState(driver=RESULT, result_available=True, result_in_editor=True,
                          result_in_driver=True, save_state=SaveState.SAVED)
        self.assertEqual(done_steps(state), set(Step))

    def test_saved_only_counts_for_the_result(self) -> None:
        # Config and driver agree on 1.0, but no result was applied: not "saved".
        state = FlowState(mouse_running=True, driver=Scalars.unity(),
                          save_state=SaveState.SAVED)
        self.assertEqual(statuses(state), "ddCppp")

    def test_unknown_driver_values_do_not_count_as_reset(self) -> None:
        state = FlowState(mouse_running=True, driver=None)
        self.assertEqual(statuses(state), "dCpppp")
        self.assertIn("unknown", derive(state)[1].detail)

    def test_is_unity_means_exactly_one(self) -> None:
        self.assertTrue(is_unity(Scalars.unity()))
        self.assertFalse(is_unity(Scalars(1.0, 1.0, 1.000001, 1.0)))
        self.assertFalse(is_unity(None))

    def test_blocked_reasons_only_for_what_cannot_work(self) -> None:
        state = FlowState(mouse_problem="/dev/uhid is missing",
                          driver_problem="module not loaded", save_problem="tool not found")
        infos = {info.step: info for info in derive(state)}
        self.assertEqual(infos[Step.START].blocked, "/dev/uhid is missing")
        self.assertEqual(infos[Step.RESET].blocked, "module not loaded")
        self.assertEqual(infos[Step.CALIBRATE].blocked, "")
        self.assertIn("no calibration result", infos[Step.USE].blocked)
        self.assertEqual(infos[Step.APPLY].blocked, "module not loaded")
        self.assertEqual(infos[Step.SAVE].blocked, "tool not found")
        fine = {info.step: info for info in derive(FlowState(result_available=True))}
        self.assertEqual([info.blocked for info in fine.values()], [""] * 6)
        running = derive(replace(state, mouse_running=True))
        self.assertEqual(running[0].blocked, "")

    def test_every_step_has_marker_text_and_description(self) -> None:
        infos = derive(FlowState(mouse_running=True, driver=Scalars.unity()))
        self.assertEqual([info.chip() for info in infos], [
            "✓ 1 Start mouse", "✓ 2 Reset to 1.0", "▶ 3 Calibrate",
            "○ 4 Use result", "○ 5 Apply", "○ 6 Save"])
        self.assertEqual(infos[2].describe().split(":")[0],
                         "Step 3 of 6, Run the calibration in the game")
        self.assertIn("current", infos[2].describe())
        for info in infos:
            self.assertTrue(info.detail)
        self.assertEqual(infos[2].action, "")  # happens in the game
        self.assertEqual(set(ACTIONS), set(Step) - {Step.CALIBRATE})

    def test_save_detail_names_the_loss(self) -> None:
        state = FlowState(driver=RESULT, result_available=True, result_in_editor=True,
                          result_in_driver=True, save_state=SaveState.DIFFERS)
        save = derive(state)[-1]
        self.assertIs(save.status, Status.CURRENT)
        self.assertIn("Applied, not saved", save.detail)
        self.assertIn("lost on module reload", save.detail)


class ResultTest(unittest.TestCase):
    def calibration(self, mtime: float, scalars: Scalars = RESULT) -> Calibration:
        return Calibration(Path("/x/ASFSettings.json"), mtime, scalars, None)

    def test_pick_result(self) -> None:
        self.assertIsNone(pick_result(None))
        from_file = pick_result(self.calibration(100.0))
        assert from_file is not None
        self.assertEqual((from_file.source, from_file.time), (SOURCE_FILE, 100.0))
        written = Scalars(1.01, 1.02, 1.03, 1.04)
        only_mouse = pick_result(None, written, 50.0)
        assert only_mouse is not None
        self.assertEqual((only_mouse.source, only_mouse.scalars), (SOURCE_MOUSE, written))
        newer_mouse = pick_result(self.calibration(100.0), written, 150.0)
        assert newer_mouse is not None
        self.assertEqual(newer_mouse.source, SOURCE_MOUSE)
        newer_file = pick_result(self.calibration(200.0), written, 150.0)
        assert newer_file is not None
        self.assertEqual(newer_file.source, SOURCE_FILE)

    def test_out_of_range_values_are_not_a_result(self) -> None:
        bad = Scalars(0.0, 1.0, 1.0, 1.0)
        self.assertIsNone(pick_result(self.calibration(100.0, bad)))
        fallback = pick_result(self.calibration(100.0), bad, 150.0)
        assert fallback is not None
        self.assertEqual(fallback.source, SOURCE_FILE)

    def test_format_deviation(self) -> None:
        self.assertEqual(format_deviation(1.0375), "+3.8 %")
        self.assertEqual(format_deviation(0.985), "-1.5 %")
        self.assertEqual(format_deviation(1.0), "0.0 %")
        self.assertEqual(format_deviation(0.9997), "0.0 %")
        self.assertEqual(format_deviation(1.125), "+12.5 %")

    def test_summaries_skip_protocol_chatter(self) -> None:
        self.assertIsNone(summarize_entry({"t": 0.0, "cmd": "GET", "id": 19}))
        self.assertIsNone(summarize_entry({"t": 0.0, "event": "GET_REPORT"}))
        self.assertTrue(summarize_entry({"t": 0.0, "event": "created"}).endswith(
            "virtual mouse created"))
        self.assertTrue(summarize_entry({"t": 0.0, "event": "OPEN"}).endswith(
            "opened by a client"))
        write = {"t": 0.0, "cmd": "SET", "id": P_DIR_SENS_X_POS,
                 "value": float_to_bits(1.0375), "highlight": "raw text"}
        self.assertTrue(summarize_entry(write).endswith(
            "calibration value received: XPos = 1.0375"))
        enabled = {"t": 0.0, "cmd": "SET", "id": P_DIR_SENS_ENABLED, "value": 1,
                   "highlight": "raw text"}
        self.assertIn("switched on", summarize_entry(enabled))


if __name__ == "__main__":
    unittest.main()
