"""Tests of saving the driver values to the yeetmouse config file (no Qt).

Nothing here runs pkexec or touches /etc: the config file is a temp file and
the tool is a stand-in script or a fake runner.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path

from quadsens import persist
from quadsens.model import Scalars
from quadsens.persist import PersistError, Saver, SaveState

FAKE_TOOL = Path(__file__).parent / "fake_yeetmousectl.py"
VALUES = Scalars(0.991374, 0.999013, 0.99732, 0.995924)
FORK_CONFIG = """sens=1
accelMode=1
dirScaleXPos=0.991374
dirScaleXNeg=0.999013
dirScaleYPos=0.99732
dirScaleYNeg=0.995924
LUT_size=0
"""
STOCK_CONFIG = "sens=1\naccelMode=1\nLUT_size=0\n"


class ParseTest(unittest.TestCase):
    def test_parse_config(self) -> None:
        entries = persist.parse_config("# comment\n\n a = 1 \nb=2=3\nnoise\n")
        self.assertEqual(entries, {"a": "1", "b": "2=3"})

    def test_saved_scalars(self) -> None:
        self.assertEqual(persist.saved_scalars(FORK_CONFIG), VALUES)
        self.assertIsNone(persist.saved_scalars(STOCK_CONFIG))
        self.assertIsNone(persist.saved_scalars(""))
        partial = FORK_CONFIG.replace("dirScaleYNeg=0.995924\n", "")
        self.assertIsNone(persist.saved_scalars(partial))
        self.assertIsNone(persist.saved_scalars(FORK_CONFIG.replace("0.99732", "abc")))
        self.assertIsNone(persist.saved_scalars(FORK_CONFIG.replace("0.99732", "nan")))
        self.assertIsNone(persist.saved_scalars(FORK_CONFIG.replace("0.99732", "99")))

    def test_keys_are_in_driver_order(self) -> None:
        self.assertEqual(persist.CONFIG_KEYS, ("dirScaleXPos", "dirScaleXNeg",
                                               "dirScaleYPos", "dirScaleYNeg"))


class TrustTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.tool = self.bin / "yeetmousectl"
        self.tool.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        self.tool.chmod(0o755)
        self.uid = os.getuid()

    def trusted(self, path: Path) -> bool:
        return persist.is_trusted_tool(path, (str(self.bin),), self.uid)

    def test_tool_in_a_trusted_directory(self) -> None:
        self.assertTrue(self.trusted(self.tool))

    def test_refuses_other_locations(self) -> None:
        other = self.tmp / "elsewhere"
        other.mkdir()
        copy = other / "yeetmousectl"
        copy.write_text("#!/bin/sh\n", encoding="ascii")
        copy.chmod(0o755)
        self.assertFalse(self.trusted(copy))
        self.assertFalse(self.trusted(Path("yeetmousectl")))       # not absolute
        self.assertFalse(self.trusted(self.bin / "missing"))
        self.assertFalse(self.trusted(self.bin))                   # a directory
        sub = self.bin / "sub"
        sub.mkdir()
        nested = sub / "yeetmousectl"
        nested.write_text("#!/bin/sh\n", encoding="ascii")
        nested.chmod(0o755)
        self.assertFalse(self.trusted(nested))                     # not directly inside

    def test_refuses_a_link_that_leaves_the_trusted_directory(self) -> None:
        target = self.tmp / "evil"
        target.write_text("#!/bin/sh\n", encoding="ascii")
        target.chmod(0o755)
        link = self.bin / "linked"
        link.symlink_to(target)
        self.assertFalse(self.trusted(link))

    def test_refuses_wrong_owner_and_loose_permissions(self) -> None:
        self.assertFalse(persist.is_trusted_tool(self.tool, (str(self.bin),), self.uid + 1))
        self.tool.chmod(0o777)
        self.assertFalse(self.trusted(self.tool))
        self.tool.chmod(0o644)  # not executable
        self.assertFalse(self.trusted(self.tool))

    def test_defaults_are_the_system_directories_and_root(self) -> None:
        self.assertEqual(persist.TRUSTED_DIRS, ("/usr/bin", "/usr/local/bin", "/bin"))
        if self.uid != 0:
            self.assertFalse(persist.is_trusted_tool(self.tool))
            saver = Saver(self.tmp / "conf", tool=self.tool, pkexec="true")
            (self.tmp / "conf").write_text("", encoding="ascii")
            (self.tmp / "conf").chmod(0o444)
            with self.assertRaisesRegex(PersistError, "not run as root"):
                saver.command()

    def test_find_tool_only_looks_in_trusted_directories(self) -> None:
        saver = Saver(self.tmp / "conf", trusted_dirs=(str(self.bin),), owner_uid=self.uid)
        self.assertEqual(saver.find_tool(), self.tool)
        self.tool.chmod(0o777)
        self.assertIsNone(saver.find_tool())
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertIsNone(Saver(self.tmp / "conf", trusted_dirs=(str(empty),)).find_tool())


class SaverTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.tool = self.bin / "yeetmousectl"
        self.tool.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        self.tool.chmod(0o755)
        self.config = self.tmp / "yeetmouse.conf"
        self.config.write_text(STOCK_CONFIG, encoding="ascii")
        self.saver = Saver(self.config, pkexec="true", trusted_dirs=(str(self.bin),),
                           owner_uid=os.getuid())

    def runner(self, text: str | None, returncode: int = 0, stderr: str = ""):
        calls: list[list[str]] = []

        def run(argv: list[str], **_kwargs: object) -> types.SimpleNamespace:
            calls.append(list(argv))
            if text is not None:
                self.config.chmod(0o644)
                self.config.write_text(text, encoding="ascii")
            return types.SimpleNamespace(returncode=returncode, stderr=stderr, stdout="")
        return run, calls

    def test_direct_command_when_the_config_is_writable(self) -> None:
        command = self.saver.command()
        self.assertFalse(command.elevated)
        self.assertEqual(command.argv, [str(self.tool), "save", str(self.config)])

    @unittest.skipIf(os.geteuid() == 0, "root can write everything")
    def test_one_pkexec_call_when_it_is_not(self) -> None:
        self.config.chmod(0o444)
        command = self.saver.command()
        self.assertTrue(command.elevated)
        self.assertEqual(Path(command.argv[0]).name, "true")  # stands in for pkexec
        self.assertTrue(os.path.isabs(command.argv[0]))
        self.assertEqual(command.argv[1:], [str(self.tool), "save", str(self.config)])
        self.assertTrue(os.path.isabs(command.argv[1]))

    @unittest.skipIf(os.geteuid() == 0, "root can write everything")
    def test_missing_config_goes_through_pkexec(self) -> None:
        self.config.unlink()
        self.assertFalse(self.saver.can_write_directly())
        self.assertTrue(self.saver.command().elevated)

    @unittest.skipIf(os.geteuid() == 0, "root can write everything")
    def test_command_errors(self) -> None:
        self.config.chmod(0o444)
        no_pkexec = Saver(self.config, pkexec="no-such-pkexec-binary",
                          trusted_dirs=(str(self.bin),), owner_uid=os.getuid())
        with self.assertRaisesRegex(PersistError, "pkexec is not installed"):
            no_pkexec.command()
        self.tool.unlink()
        with self.assertRaisesRegex(PersistError, "was not found"):
            self.saver.command()

    def test_state(self) -> None:
        self.assertIs(self.saver.state(VALUES), SaveState.MISSING_KEYS)
        self.config.write_text(FORK_CONFIG, encoding="ascii")
        self.assertIs(self.saver.state(VALUES), SaveState.SAVED)
        self.assertIs(self.saver.state(Scalars.unity()), SaveState.DIFFERS)
        self.assertIs(self.saver.state(None), SaveState.UNKNOWN)
        self.assertEqual(self.saver.read_saved(), VALUES)
        self.config.unlink()
        self.assertIs(self.saver.state(VALUES), SaveState.NO_CONFIG)
        with self.assertRaises(PersistError):
            self.saver.read_saved()

    def test_six_decimals_of_the_driver_match_the_shorter_config_text(self) -> None:
        self.config.write_text(FORK_CONFIG, encoding="ascii")
        driver = Scalars(*(float("%.6f" % value) for value in VALUES.as_tuple()))
        self.assertIs(self.saver.state(driver), SaveState.SAVED)
        off = Scalars(0.991374, 0.999013, 0.99733, 0.995924)
        self.assertIs(self.saver.state(off), SaveState.DIFFERS)

    def test_save_with_the_fork_tool(self) -> None:
        run, calls = self.runner(FORK_CONFIG)
        state, text = self.saver.save(VALUES, run)
        self.assertIs(state, SaveState.SAVED)
        self.assertIn("Saved to", text)
        self.assertEqual(calls, [[str(self.tool), "save", str(self.config)]])

    def test_save_with_the_stock_tool_is_reported(self) -> None:
        run, _calls = self.runner(STOCK_CONFIG)
        state, text = self.saver.save(VALUES, run)
        self.assertIs(state, SaveState.MISSING_KEYS)
        self.assertIn("Nothing directional was saved", text)
        self.assertIn("not the fork's", text)

    def test_save_that_wrote_other_values(self) -> None:
        run, _calls = self.runner(FORK_CONFIG)
        state, text = self.saver.save(Scalars.unity(), run)
        self.assertIs(state, SaveState.DIFFERS)
        self.assertIn("other values", text)

    def test_save_without_readable_driver(self) -> None:
        run, _calls = self.runner(FORK_CONFIG)
        state, text = self.saver.save(None, run)
        self.assertIs(state, SaveState.UNKNOWN)
        self.assertIn("not confirmed", text)

    def test_failures(self) -> None:
        run, _calls = self.runner(None, returncode=3, stderr="cannot open\n")
        with self.assertRaisesRegex(PersistError, r"status 3 \(cannot open\)"):
            self.saver.save(VALUES, run)

        def broken(argv: list[str], **_kwargs: object) -> None:
            raise OSError("exec format error")
        with self.assertRaisesRegex(PersistError, "could not run"):
            self.saver.save(VALUES, broken)
        self.assertIn("authorization was dismissed",
                      persist.describe_save_failure(126, "", elevated=True))
        self.assertIn("not authorized", persist.describe_save_failure(127, "", elevated=True))
        self.assertIn("status 126", persist.describe_save_failure(126, "", elevated=False))

    def test_stand_in_tool_behaves_like_the_real_one(self) -> None:
        import subprocess
        params = self.tmp / "params"
        params.mkdir()
        for name, value in zip(("DirScaleXPos", "DirScaleXNeg", "DirScaleYPos",
                                "DirScaleYNeg"), VALUES.as_tuple()):
            (params / name).write_text("%.6f\n" % value, encoding="ascii")
        self.assertTrue(FAKE_TOOL.stat().st_mode & stat.S_IXUSR)
        saver = Saver(self.config, tool=FAKE_TOOL)
        for mode, expected in (("fork", SaveState.SAVED), ("stock", SaveState.MISSING_KEYS)):
            env = dict(os.environ, QUADSENS_FAKE_PARAMS=str(params), QUADSENS_FAKE_MODE=mode)

            def run(argv: list[str], **kwargs: object) -> object:
                return subprocess.run([sys.executable, *argv], env=env, **kwargs)
            state, _text = saver.save(VALUES, run)
            self.assertIs(state, expected, mode)


if __name__ == "__main__":
    unittest.main()
