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
    FIX_FOLDER,
    FIX_IMPORT,
    FlowState,
    Status,
    Step,
    WinFlowState,
    WinStep,
    current_step,
    derive,
    derive_windows,
    done_steps,
    is_newer,
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
        self.assertEqual({step for step in ACTIONS if isinstance(step, Step)},
                         set(Step) - {Step.CALIBRATE})

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


class NewerTest(unittest.TestCase):
    def test_times_rounded_to_the_millisecond_still_count_as_after(self) -> None:
        baseline = 1700000000.0003              # the reset
        written = round(1700000000.0004, 3)     # a write right after, as the log stamps it
        self.assertLess(written, baseline)      # the rounding went below the baseline
        self.assertTrue(is_newer(written, baseline))
        self.assertTrue(is_newer(baseline + 5, baseline))
        self.assertFalse(is_newer(baseline - 0.002, baseline))
        self.assertFalse(is_newer(baseline - 3600, baseline))
        self.assertFalse(is_newer(baseline, None))


class WindowsStepsTest(unittest.TestCase):
    """The five steps on Windows, with and without usbip-win2."""

    READY = WinFlowState(folder_ok=True, neutral=True)

    def marks(self, state: WinFlowState) -> str:
        return "".join(info.marker for info in derive_windows(state))

    def current(self, state: WinFlowState):
        return current_step(derive_windows(state))

    def test_the_whole_way_with_the_virtual_mouse(self) -> None:
        self.assertEqual([info.chip() for info in derive_windows(WinFlowState())], [
            "▶ 1 Start mouse", "○ 2 Neutral", "○ 3 Calibrate", "○ 4 Use result", "○ 5 Apply"])
        self.assertEqual(self.current(WinFlowState()).action, "Start virtual mouse")
        running = replace(self.READY, mouse_running=True)
        self.assertEqual(self.marks(running), "✓✓▶○○")
        self.assertEqual(self.current(running).action, "")      # happens in the game
        old = replace(running, result_available=True)           # a result from earlier
        self.assertEqual(self.marks(old), "✓✓▶○○")
        new = replace(old, result_is_new=True)
        self.assertEqual(self.marks(new), "✓✓✓▶○")
        self.assertEqual(self.current(new).action, "Use result")
        self.assertEqual(self.current(new).blocked, "")
        taken = replace(new, values_ready=True)
        self.assertEqual(self.marks(taken), "✓✓✓✓▶")
        self.assertEqual(self.current(taken).action, "Apply")
        applied = replace(taken, applied=True, neutral=False)
        self.assertEqual(self.marks(applied), "✓✓✓✓✓")
        self.assertIsNone(self.current(applied))

    def test_earlier_multipliers_must_go_before_calibrating(self) -> None:
        state = WinFlowState(mouse_running=True, folder_ok=True, neutral=False)
        self.assertEqual(self.marks(state), "✓▶○○○")
        info = self.current(state)
        self.assertEqual((info.step, info.action, info.blocked),
                         (WinStep.RESET, "Restore original values", ""))
        # A result that arrives while the old multipliers are still in is new all
        # the same; the step row then moves on.
        self.assertEqual(self.marks(replace(state, result_available=True,
                                            result_is_new=True)), "✓✓✓▶○")

    def test_no_folder_blocks_reset_and_apply_with_a_way_out(self) -> None:
        state = WinFlowState(mouse_running=True)
        info = self.current(state)
        self.assertEqual((info.step, info.fix), (WinStep.RESET, FIX_FOLDER))
        self.assertEqual(info.blocked, "Choose the RawAccel folder first.")
        broken = WinFlowState(mouse_running=True, folder_problem="This file is not understood.")
        self.assertEqual(self.current(broken).blocked, "This file is not understood.")
        ready = WinFlowState(values_ready=True)
        info = self.current(ready)
        self.assertEqual((info.step, info.fix), (WinStep.APPLY, FIX_FOLDER))
        self.assertEqual(self.marks(ready), "✓✓✓✓▶")
        # A folder alone is never "applied".
        self.assertEqual(self.marks(WinFlowState(values_ready=True, applied=True)), "✓✓✓✓▶")

    def test_without_usbip_win2_the_import_route_is_offered(self) -> None:
        missing = WinFlowState(mouse_possible=False)
        self.assertEqual(self.marks(missing), "○○○▶○")
        info = self.current(missing)
        self.assertEqual((info.step, info.fix), (WinStep.USE, FIX_IMPORT))
        self.assertIn("Import a values file", info.blocked)
        self.assertIn("usbip-win2", derive_windows(missing)[0].detail)
        with_file = replace(missing, result_available=True)     # an old result file
        self.assertEqual(self.current(with_file).action, "Use result")
        self.assertEqual(self.current(with_file).blocked, "")
        typed = replace(missing, values_ready=True)
        self.assertEqual(self.marks(typed), "✓✓✓✓▶")
        self.assertEqual(self.current(typed).fix, FIX_FOLDER)
        done = replace(typed, folder_ok=True, applied=True)
        self.assertEqual(self.marks(done), "✓✓✓✓✓")
        # The server can run without the driver (somebody attaches by hand).
        self.assertEqual(self.marks(replace(missing, mouse_running=True)), "✓▶○○○")

    def test_every_combination_gives_one_consistent_row(self) -> None:
        import itertools
        fields = ("mouse_running", "mouse_possible", "folder_ok", "neutral",
                  "result_available", "result_is_new", "values_ready", "applied")
        for bits in itertools.product((False, True), repeat=len(fields)):
            state = WinFlowState(**dict(zip(fields, bits)))
            if state.result_is_new and not state.result_available:
                continue
            infos = derive_windows(state)
            self.assertEqual([info.step for info in infos], list(WinStep))
            currents = [info for info in infos if info.status is Status.CURRENT]
            self.assertLessEqual(len(currents), 1, state)
            if not currents:
                self.assertTrue(all(info.status is Status.DONE for info in infos), state)
                self.assertTrue(state.applied and state.folder_ok and state.values_ready)
            for info in infos:
                self.assertTrue(info.detail, state)
                self.assertEqual(bool(info.fix), bool(info.blocked), (state, info))
                if info.status is Status.DONE:
                    self.assertEqual(info.blocked, "", state)
            # Nothing before the current step is left open, except what is set
            # aside because usbip-win2 is missing.
            if currents and state.mouse_possible:
                before = infos[:infos.index(currents[0])]
                self.assertTrue(all(info.status is Status.DONE for info in before), state)
            # Applying is always reachable: values plus folder is enough.
            if state.values_ready and state.folder_ok and not state.applied:
                self.assertEqual((currents[0].step, currents[0].blocked),
                                 (WinStep.APPLY, ""), state)


if __name__ == "__main__":
    unittest.main()
