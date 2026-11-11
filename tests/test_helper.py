"""Tests for helper/quadsens-apply: input validation and the writes it performs."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

HELPER = Path(__file__).resolve().parent.parent / "helper" / "quadsens-apply"


def load_helper() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("quadsens_apply", str(HELPER))
    spec = importlib.util.spec_from_loader("quadsens_apply", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


helper = load_helper()


class ValidationTest(unittest.TestCase):
    def test_accepts_plain_decimals(self) -> None:
        self.assertEqual(helper.parse_args(["1", "0.1", "10", "1.037500"]),
                         ["1.000000", "0.100000", "10.000000", "1.037500"])
        self.assertEqual(helper.parse_args(["10.0", "01.5", "9.999999999", "0.100"])[1],
                         "1.500000")

    def test_rejects_wrong_argument_count(self) -> None:
        for args in ([], ["1"], ["1", "1", "1"], ["1", "1", "1", "1", "1"]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                helper.parse_args(args)

    def test_rejects_anything_but_plain_decimals_in_range(self) -> None:
        bad = ["", " 1", "1 ", "1\n", "+1", "-1", "1e0", "1E-1", "nan", "inf", "0x1",
               "1,0", "1.", ".5", "1.0.0", "1;id", "$(id)", "../1", "--help", "１",
               "1.0000000000", "100", "0.09", "0.0999", "10.01", "11", "0", "99"]
        for value in bad:
            for position in range(4):
                args = ["1", "1", "1", "1"]
                args[position] = value
                with self.subTest(value=value, position=position):
                    with self.assertRaises(ValueError):
                        helper.parse_args(args)


class MainTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.params = Path(self._tmp.name) / "parameters"

    def make_params(self) -> None:
        self.params.mkdir()
        for name in (*helper.PARAM_NAMES, "update"):
            (self.params / name).write_text("0\n", encoding="ascii")

    def run_main(self, args: list[str]) -> tuple[int, str]:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = helper.main(args, params_dir=str(self.params))
        return code, stderr.getvalue()

    def test_writes_four_values_and_update(self) -> None:
        self.make_params()
        code, err = self.run_main(["1.0375", "0.9625", "1.125", "0.875"])
        self.assertEqual((code, err), (0, ""))
        got = {p.name: p.read_text() for p in self.params.iterdir()}
        self.assertEqual(got, {"DirScaleXPos": "1.037500\n", "DirScaleXNeg": "0.962500\n",
                               "DirScaleYPos": "1.125000\n", "DirScaleYNeg": "0.875000\n",
                               "update": "1\n"})

    def test_invalid_input_writes_nothing(self) -> None:
        self.make_params()
        code, err = self.run_main(["1", "1", "1", "11"])
        self.assertEqual(code, 2)
        self.assertIn("DirScaleYNeg", err)
        for path in self.params.iterdir():
            self.assertEqual(path.read_text(), "0\n")

    def test_module_not_loaded(self) -> None:
        code, err = self.run_main(["1", "1", "1", "1"])
        self.assertEqual(code, 3)
        self.assertIn("not loaded", err)

    def test_missing_parameter_is_not_created(self) -> None:
        self.params.mkdir()
        code, _err = self.run_main(["1", "1", "1", "1"])
        self.assertEqual(code, 4)
        self.assertEqual(list(self.params.iterdir()), [])

    def test_refuses_symlinked_parameter(self) -> None:
        self.make_params()
        target = Path(self._tmp.name) / "elsewhere"
        target.write_text("untouched\n", encoding="ascii")
        (self.params / "DirScaleXPos").unlink()
        (self.params / "DirScaleXPos").symlink_to(target)
        code, _err = self.run_main(["2", "2", "2", "2"])
        self.assertEqual(code, 4)
        self.assertEqual(target.read_text(), "untouched\n")

    def test_default_target_is_the_fixed_sysfs_directory(self) -> None:
        self.assertEqual(helper.PARAMS_DIR, "/sys/module/yeetmouse/parameters")


class AccelTest(unittest.TestCase):
    """The acceleration command: the helper checks everything itself."""

    def setUp(self) -> None:
        import json
        import random
        import tempfile

        from quadsens import accel_edit
        self.ae = accel_edit
        self.random = random
        self.helper = load_helper()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.params = Path(self._tmp.name)
        for name, text in (("Acceleration", "0.1"), ("Exponent", "1.8"), ("Midpoint", "1.3"),
                           ("Motivity", "1.5"), ("AccelerationMode", "1"),
                           ("UseSmoothing", "1"), ("update", "0"), ("Offset", "0"),
                           ("InputCap", "0"), ("OutputCap", "0"), ("PreScale", "1")):
            (self.params / name).write_text(text + "\n")
        self.vectors = json.loads((Path(__file__).parent / "fixtures" / "accel"
                                   / "vectors.json").read_text(encoding="utf-8"))

    def args(self, curve) -> list[str]:
        values = self.ae.fork_values(curve)
        return [values["AccelerationMode"], values["UseSmoothing"],
                *(values[name] for name in self.ae.FORK_SLOTS)]

    def raw_args(self, curve) -> list[str]:
        """The arguments for a curve QuadSens itself would refuse to produce."""
        slots, smoothing = self.ae._fork_slots(curve)
        return [str(self.ae.FORK_MODE_NUMBERS[curve.mode]), str(smoothing),
                *("%.10f" % slots[name] for name in self.ae.FORK_SLOTS)]

    def state(self) -> dict[str, str]:
        return {path.name: path.read_text() for path in self.params.iterdir()}

    def test_writes_all_slots_then_switch_then_update(self) -> None:
        order: list[str] = []
        original = self.helper._write
        self.helper._write = lambda path, text: (order.append(Path(path).name),
                                                 original(path, text))[1]
        curve = self.ae.make("power", cap=3.0, exponent=0.5, output_offset=1.0)
        self.assertEqual(self.helper.main(["accel", *self.args(curve)], str(self.params)), 0)
        self.assertEqual(order, ["Acceleration", "Exponent", "Midpoint", "Motivity",
                                 "AccelerationMode", "UseSmoothing", "update"])
        state = self.state()
        self.assertEqual(state["Acceleration"], "1.0000000000\n")
        self.assertEqual(state["Midpoint"], "1.0000000000\n")
        self.assertEqual(state["Motivity"], "3.0000000000\n")
        self.assertEqual((state["AccelerationMode"], state["UseSmoothing"], state["update"]),
                         ("2\n", "1\n", "1\n"))
        self.assertEqual(state["Offset"], "0\n")                  # nothing else touched

    def test_every_dangerous_example_is_refused_and_nothing_is_written(self) -> None:
        before = self.state()
        for case in self.vectors["fork_must_refuse_examples"]["cases"]:
            curve = self.ae.from_dict(case["mode"], case["neutral"])
            try:
                args = self.raw_args(curve)
            except Exception:                 # cannot even be put into the grammar
                continue
            self.assertEqual(self.helper.main(["accel", *args], str(self.params)), 2, case)
            self.assertEqual(self.state(), before)

    def test_agrees_with_the_application_on_a_random_sweep(self) -> None:
        import tests.test_accel_edit as model_tests
        rng = self.random.Random(99)
        allowed = refused = 0
        for _ in range(4000):
            mode = rng.choice(self.ae.MODES)
            curve = self.ae.from_dict(mode, model_tests.random_set(rng, mode))
            ours = not self.ae.violations(curve)
            try:
                self.helper.parse_accel(self.raw_args(curve))
                helpers = True
            except ValueError:
                helpers = False
            self.assertEqual(helpers, ours, (curve, self.ae.violations(curve)))
            allowed += ours
            refused += not ours
        self.assertGreater(allowed, 1500)
        self.assertGreater(refused, 200)

    def test_grammar_is_fixed(self) -> None:
        good = self.args(self.ae.make("linear"))
        self.helper.parse_accel(good)
        bad = [
            good[:5], good + ["1"], ["4", *good[1:]], ["9", *good[1:]], ["1 ", *good[1:]],
            [good[0], "2", *good[2:]], [good[0], "true", *good[2:]],
            [*good[:2], "0.005", *good[3:]],                 # not ten decimals
            [*good[:2], "5e-03", *good[3:]], [*good[:2], ".0050000000", *good[3:]],
            [*good[:2], "-0.0050000000", *good[3:]], [*good[:2], "0,0050000000", *good[3:]],
            [*good[:2], "0.0050000000\n", *good[3:]], [*good[:2], "nan", *good[3:]],
            [*good[:2], "123.0000000000", *good[3:]],
            [*good[:3], "3.0000000000", *good[4:]],          # a slot the mode does not use
            [*good[:5], "9.0000000000"],
        ]
        before = self.state()
        for args in bad:
            self.assertEqual(self.helper.main(["accel", *args], str(self.params)), 2, args)
        self.assertEqual(self.state(), before)
        # No free-form parameter names anywhere in the grammar.
        self.assertEqual(self.helper.main(["accel", "Sensitivity", "5"], str(self.params)), 2)
        self.assertEqual(self.helper.main(["Acceleration", "1", "1", "1"], str(self.params)), 2)

    def test_refuses_when_the_module_is_set_to_something_else(self) -> None:
        good = ["accel", *self.args(self.ae.make("classic", cap=2.0))]
        for name, text in (("Offset", "2"), ("InputCap", "40"), ("OutputCap", "3"),
                           ("PreScale", "0.8"), ("AccelerationMode", "5"),
                           ("AccelerationMode", "8"), ("Offset", "junk")):
            original = (self.params / name).read_text()
            (self.params / name).write_text(text + "\n")
            before = self.state()
            self.assertEqual(self.helper.main(good, str(self.params)), 5, name)
            self.assertEqual(self.state(), before)
            (self.params / name).write_text(original)
        self.assertEqual(self.helper.main(good, str(self.params / "missing")), 3)
        self.assertEqual(self.helper.main(good, str(self.params)), 0)

    def test_the_four_value_form_still_works(self) -> None:
        for name in self.helper.PARAM_NAMES:
            (self.params / name).write_text("1.000000\n")
        self.assertEqual(self.helper.main(["1.01", "0.99", "1", "1"], str(self.params)), 0)
        self.assertEqual((self.params / "DirScaleXPos").read_text(), "1.010000\n")
        self.assertEqual((self.params / "Acceleration").read_text(), "0.1\n")


if __name__ == "__main__":
    unittest.main()
