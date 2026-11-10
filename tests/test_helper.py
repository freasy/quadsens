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


if __name__ == "__main__":
    unittest.main()
