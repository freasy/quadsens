"""Tests for quadsens.driver against a temp directory standing in for sysfs."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from quadsens import driver as drv
from quadsens.model import Scalars

IS_ROOT = os.geteuid() == 0


class FakeRunner:
    """Stands in for subprocess.run; pkexec is never executed by the tests."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        return subprocess.CompletedProcess(command, self.returncode, "", self.stderr)


class DriverTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.module = Path(self._tmp.name) / "yeetmouse"
        self.params = self.module / "parameters"
        self.helper = Path(self._tmp.name) / "quadsens-apply"
        self.helper.write_text("#!/bin/false\n", encoding="ascii")
        self.driver = drv.Driver(self.params, helper=self.helper, pkexec="true")

    def make_params(self, values: tuple[str, ...] = ("1", "1", "1", "1")) -> None:
        self.params.mkdir(parents=True)
        for name, value in zip(drv.PARAM_NAMES, values):
            (self.params / name).write_text(value + "\n", encoding="ascii")
        (self.params / "update").write_text("0\n", encoding="ascii")

    def lock(self) -> None:
        for path in self.params.iterdir():
            path.chmod(0o000)
        self.addCleanup(lambda: [p.chmod(0o600) for p in self.params.iterdir()])

    # ---- read -----------------------------------------------------------

    def test_read_module_not_loaded(self) -> None:
        self.assertEqual(self.driver.read().status, drv.DriverStatus.NOT_LOADED)

    def test_read_unpatched_module(self) -> None:
        self.params.mkdir(parents=True)
        (self.params / "Sensitivity").write_text("1\n", encoding="ascii")
        state = self.driver.read()
        self.assertEqual(state.status, drv.DriverStatus.UNSUPPORTED)
        self.assertIn("DirScaleXPos", state.message)

    def test_read_values(self) -> None:
        self.make_params(("1.037500", "0.9625", "1.125", "0.875"))
        state = self.driver.read()
        self.assertEqual(state.status, drv.DriverStatus.OK)
        self.assertEqual(state.scalars, Scalars(1.0375, 0.9625, 1.125, 0.875))

    def test_read_garbage(self) -> None:
        self.make_params(("1", "abc", "1", "1"))
        state = self.driver.read()
        self.assertEqual(state.status, drv.DriverStatus.ERROR)
        self.assertIn("DirScaleXNeg", state.message)

    @unittest.skipIf(IS_ROOT, "permission checks do not apply to root")
    def test_read_not_readable(self) -> None:
        self.make_params()
        self.lock()
        self.assertEqual(self.driver.read().status, drv.DriverStatus.NOT_READABLE)

    # ---- write ----------------------------------------------------------

    def test_write_direct_round_trip(self) -> None:
        self.make_params()
        runner = FakeRunner()
        method = self.driver.write(Scalars(1.0375, 0.9625, 1.125, 0.875), runner=runner)
        self.assertEqual(method, "direct")
        self.assertEqual(runner.calls, [])
        self.assertEqual((self.params / "DirScaleXPos").read_text(), "1.037500\n")
        self.assertEqual((self.params / "DirScaleYNeg").read_text(), "0.875000\n")
        self.assertEqual((self.params / "update").read_text(), "1\n")
        self.assertEqual(self.driver.read().scalars, Scalars(1.0375, 0.9625, 1.125, 0.875))

    def test_write_rejects_out_of_range_without_touching_files(self) -> None:
        self.make_params()
        for bad in (0.05, 10.5, float("nan"), float("inf")):
            with self.subTest(bad=bad), self.assertRaises(drv.DriverError):
                self.driver.write(Scalars(bad, 1.0, 1.0, 1.0), runner=FakeRunner())
        self.assertEqual((self.params / "DirScaleXPos").read_text(), "1\n")
        self.assertEqual((self.params / "update").read_text(), "0\n")

    def test_write_module_not_loaded(self) -> None:
        with self.assertRaisesRegex(drv.DriverError, "not loaded"):
            self.driver.write(Scalars.unity(), runner=FakeRunner())

    def test_write_does_not_create_missing_parameters(self) -> None:
        self.params.mkdir(parents=True)
        with self.assertRaises(drv.DriverError):
            self.driver.write_direct(Scalars.unity())
        self.assertEqual(list(self.params.iterdir()), [])

    @unittest.skipIf(IS_ROOT, "permission checks do not apply to root")
    def test_write_falls_back_to_one_pkexec_call(self) -> None:
        self.make_params()
        self.lock()
        with self.assertRaises(PermissionError):
            self.driver.write_direct(Scalars.unity())
        runner = FakeRunner()
        method = self.driver.write(Scalars(1.5, 0.5, 2.0, 0.25), runner=runner)
        self.assertEqual(method, "pkexec")
        self.assertEqual(len(runner.calls), 1)
        command = runner.calls[0]
        self.assertTrue(os.path.isabs(command[0]))
        self.assertEqual(command[1], str(self.helper.resolve()))
        self.assertEqual(command[2:], ["1.500000", "0.500000", "2.000000", "0.250000"])

    @unittest.skipIf(IS_ROOT, "permission checks do not apply to root")
    def test_helper_failure_is_reported(self) -> None:
        self.make_params()
        self.lock()
        with self.assertRaisesRegex(drv.DriverError, "dismissed"):
            self.driver.write(Scalars.unity(), runner=FakeRunner(126))
        with self.assertRaisesRegex(drv.DriverError, "status 4: write failed"):
            self.driver.write(Scalars.unity(), runner=FakeRunner(4, "write failed\n"))

    def test_helper_command_errors(self) -> None:
        self.make_params()
        missing = drv.Driver(self.params, helper=self.module / "nope", pkexec="true")
        with self.assertRaisesRegex(drv.DriverError, "not found"):
            missing.helper_command(Scalars.unity())
        no_pkexec = drv.Driver(self.params, helper=self.helper,
                               pkexec="quadsens-no-such-binary")
        with self.assertRaisesRegex(drv.DriverError, "pkexec is not installed"):
            no_pkexec.helper_command(Scalars.unity())

    def test_shipped_helper_is_found(self) -> None:
        helper = drv.find_helper()
        assert helper is not None
        self.assertEqual(helper.name, "quadsens-apply")


if __name__ == "__main__":
    unittest.main()
