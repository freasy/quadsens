"""Tests of the small Qt-free helpers: number parsing, resources, hints, About facts."""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import quadsens
from quadsens import about, calibration, emulator, hints, resources
from quadsens.driver import DriverState, DriverStatus
from quadsens.model import Scalars, format_decimal, parse_decimal

ROOT = Path(__file__).resolve().parent.parent
BRANDS = re.compile(r"purevector|omnisense|corsair|sabre|aimlab", re.IGNORECASE)


class DecimalTest(unittest.TestCase):
    def test_dot_and_comma_mean_the_same(self) -> None:
        self.assertEqual(parse_decimal("1.0375"), 1.0375)
        self.assertEqual(parse_decimal("1,0375"), 1.0375)
        self.assertEqual(parse_decimal(" 0,5 "), 0.5)
        self.assertEqual(parse_decimal("2"), 2.0)
        self.assertEqual(parse_decimal("1."), 1.0)
        self.assertEqual(parse_decimal(",75"), 0.75)

    def test_rejects_everything_else(self) -> None:
        for text in ("", ".", ",", "-1", "+1", "1e3", "1.000,5", "1,000.5", "1 0", "nan",
                     "inf", "0x10", "1..2", "abc"):
            self.assertIsNone(parse_decimal(text), text)

    def test_format_round_trips(self) -> None:
        for value in (0.1, 0.9625, 1.0, 1.0375, 10.0):
            text = format_decimal(value, 4)
            self.assertNotIn(",", text)
            self.assertEqual(parse_decimal(text), value)
            self.assertEqual(parse_decimal(text.replace(".", ",")), value)
        self.assertEqual(format_decimal(1.125, 4), "1.1250")


class ResourceTest(unittest.TestCase):
    def test_icon_is_found_in_the_checkout(self) -> None:
        path = resources.resource_path(resources.ICON_FILE)
        self.assertEqual(path, ROOT / "assets" / "quadsens.svg")
        text = path.read_text(encoding="utf-8")
        self.assertIn("<svg", text)
        self.assertIsNone(BRANDS.search(text))

    def test_frozen_build_reads_from_the_unpack_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", tmp, create=True):
            self.assertEqual(resources.resource_root(), Path(tmp))
            self.assertEqual(resources.resource_path(resources.ICON_FILE),
                             Path(tmp) / "assets" / "quadsens.svg")

    def test_build_script_bundles_the_icon(self) -> None:
        script = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn("--add-data assets/quadsens.svg:assets", script)

    def test_desktop_file_uses_the_icon_and_neutral_names(self) -> None:
        text = (ROOT / "quadsens.desktop").read_text(encoding="utf-8")
        self.assertIn("\nIcon=quadsens\n", text)
        self.assertIn("\nName=QuadSens\n", text)
        self.assertIsNone(BRANDS.search(text))


class AboutTest(unittest.TestCase):
    def test_about_facts(self) -> None:
        lines = about.about_lines()
        self.assertEqual(lines[0], "QuadSens " + quadsens.__version__)
        self.assertIn("License: GPL-3.0-or-later", lines)
        self.assertIn("https://github.com/freasy/quadsens", lines)
        self.assertIn(about.NOT_AFFILIATED, lines)
        self.assertIn("not affiliated", about.NOT_AFFILIATED)
        self.assertIsNone(BRANDS.search("\n".join(lines)))

    def test_version_has_one_source(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('dynamic = ["version"]', pyproject)
        self.assertIn('version = {attr = "quadsens.__version__"}', pyproject)
        self.assertIsNone(re.search(r'^version = "', pyproject, re.MULTILINE))
        self.assertRegex(quadsens.__version__, r"^\d+\.\d+\.\d+")


class HintTest(unittest.TestCase):
    def test_driver_status_names_the_next_step(self) -> None:
        ok = hints.driver_status(DriverState(DriverStatus.OK, Scalars.unity()))
        self.assertIn("values read from the driver", ok)
        not_loaded = hints.driver_status(DriverState(DriverStatus.NOT_LOADED))
        self.assertIn("module not loaded", not_loaded)
        self.assertIn("forked yeetmouse module", not_loaded)
        unsupported = hints.driver_status(DriverState(DriverStatus.UNSUPPORTED))
        self.assertIn("no DirScale parameters", unsupported)
        self.assertIn("feature/directional-scalars", unsupported)
        unreadable = hints.driver_status(DriverState(DriverStatus.NOT_READABLE))
        self.assertIn("not readable", unreadable)
        self.assertIn("group yeetmouse", unreadable)
        self.assertIn("log in again", unreadable)
        self.assertIn("systemctl restart yeetmouse.service", unreadable)
        self.assertIn("asks for authorization", unreadable)
        no_helper = hints.driver_status(DriverState(DriverStatus.NOT_READABLE),
                                        helper_installed=False)
        self.assertIn("not installed system-wide", no_helper)
        error = hints.driver_status(DriverState(DriverStatus.ERROR, message="boom"))
        self.assertIn("boom", error)

    def test_uhid_hint(self) -> None:
        self.assertEqual(hints.uhid_hint(hints.UHID_OK), "")
        self.assertIn("modprobe uhid", hints.uhid_hint(hints.UHID_MISSING))
        denied = hints.uhid_hint(hints.UHID_DENIED)
        self.assertIn("99-quadsens.rules", denied)
        self.assertIn("group input", denied)
        self.assertTrue((ROOT / "99-quadsens.rules").is_file())

    def test_texts_use_neutral_names(self) -> None:
        texts = [*hints.FIRST_STEPS, hints.NO_RESULT, hints.NO_RESULT_FOLDER, hints.PERSIST,
                 hints.uhid_hint(hints.UHID_MISSING), hints.uhid_hint(hints.UHID_DENIED)]
        texts += [hints.driver_status(DriverState(status)) for status in DriverStatus]
        self.assertIsNone(BRANDS.search("\n".join(texts)))
        self.assertIn("yeetmousectl save", hints.PERSIST)
        self.assertIn("reloaded", hints.PERSIST)

    def test_uhid_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            node = Path(tmp) / "uhid"
            self.assertEqual(emulator.uhid_access(str(node)), hints.UHID_MISSING)
            node.write_bytes(b"")
            self.assertEqual(emulator.uhid_access(str(node)), hints.UHID_OK)
            self.assertEqual(node.read_bytes(), b"")  # looked at, not written
            if os.geteuid() != 0:
                node.chmod(0)
                self.assertEqual(emulator.uhid_access(str(node)), hints.UHID_DENIED)


class ResultFolderTest(unittest.TestCase):
    def test_result_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pattern = str(Path(tmp, "Users", "*", "ASFData", "*", "ASFSettings.json"))
            self.assertIsNone(calibration.result_folder(pattern))
            users = Path(tmp, "Users")
            users.mkdir()
            self.assertEqual(calibration.result_folder(pattern), users)
            folder = users / "u1" / "ASFData" / "mouse"
            folder.mkdir(parents=True)
            (folder / "ASFSettings.json").write_text("{}", encoding="utf-8")
            self.assertEqual(calibration.result_folder(pattern), folder)

    def test_display_path_abbreviates_home(self) -> None:
        with mock.patch.dict(os.environ, {"HOME": "/home/someone"}):
            self.assertEqual(calibration.display_path("/home/someone/a/b.json"), "~/a/b.json")
            self.assertEqual(calibration.display_path("/home/someone"), "~")
            self.assertEqual(calibration.display_path("/home/someone-else/a"),
                             "/home/someone-else/a")
            self.assertEqual(calibration.display_path("/tmp/x"), "/tmp/x")


if __name__ == "__main__":
    unittest.main()
