"""Tests for quadsens.calibration: locating and parsing the result file."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from quadsens import calibration as cal
from quadsens.model import Scalars

FIXTURE = Path(__file__).parent / "fixtures" / "ASFSettings.json"


class LoadTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write(self, content: object, name: str = "ASFSettings.json") -> Path:
        path = self.tmp / name
        path.write_text(content if isinstance(content, str) else json.dumps(content),
                        encoding="utf-8")
        return path

    def test_fixture(self) -> None:
        result = cal.load(FIXTURE)
        self.assertEqual(result.firmware, Scalars(1.0375, 0.9625, 1.125, 0.875))
        self.assertEqual(result.directional,
                         (1.04, 0.99, 0.93, 1.0, 0.97, 1.02, 1.08, 0.95))
        self.assertEqual(result.path, FIXTURE)
        self.assertAlmostEqual(result.mtime, FIXTURE.stat().st_mtime)

    def test_missing_file(self) -> None:
        with self.assertRaisesRegex(cal.CalibrationError, "not found"):
            cal.load(self.tmp / "nope.json")

    def test_invalid_json(self) -> None:
        with self.assertRaisesRegex(cal.CalibrationError, "not valid JSON"):
            cal.load(self.write("{ truncated"))

    def test_wrong_shapes(self) -> None:
        cases: list[tuple[object, str]] = [
            ([1, 2], "not a JSON object"),
            ({}, "no firmwareScalars"),
            ({"firmwareScalars": [1, 1, 1, 1]}, "no firmwareScalars"),
            ({"firmwareScalars": {"XPos": 1, "XNeg": 1, "YPos": 1}}, "YNeg"),
            ({"firmwareScalars": {"XPos": "1", "XNeg": 1, "YPos": 1, "YNeg": 1}}, "XPos"),
            ({"firmwareScalars": {"XPos": True, "XNeg": 1, "YPos": 1, "YNeg": 1}}, "XPos"),
        ]
        for content, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(cal.CalibrationError, expected):
                    cal.load(self.write(content))

    def test_non_finite_firmware_value(self) -> None:
        path = self.write('{"firmwareScalars":{"XPos":NaN,"XNeg":1,"YPos":1,"YNeg":1}}')
        with self.assertRaisesRegex(cal.CalibrationError, "XPos"):
            cal.load(path)

    def test_directional_is_optional_and_checked(self) -> None:
        fw = {"XPos": 1, "XNeg": 1, "YPos": 1, "YNeg": 1}
        for directional in (None, [1.0] * 7, [1.0] * 7 + ["x"], "nope"):
            with self.subTest(directional=directional):
                data: dict[str, object] = {"firmwareScalars": fw}
                if directional is not None:
                    data["directionalScalars"] = directional
                result = cal.load(self.write(data))
                self.assertIsNone(result.directional)
                self.assertEqual(result.firmware, Scalars.unity())


class FindTest(unittest.TestCase):
    def test_newest_file_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pattern = os.path.join(tmp, "Users", "*", "ASFData", "*", "ASFSettings.json")
            self.assertIsNone(cal.find_latest(pattern))
            self.assertIsNone(cal.load_latest(pattern))
            paths = []
            for index, device in enumerate(("old", "new", "middle")):
                folder = Path(tmp, "Users", "u1", "ASFData", device)
                folder.mkdir(parents=True)
                path = folder / "ASFSettings.json"
                path.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
                paths.append(path)
                os.utime(path, (1000 + index, 1000 + index))
            os.utime(paths[1], (5000, 5000))
            self.assertEqual(cal.find_latest(pattern), paths[1])
            latest = cal.load_latest(pattern)
            assert latest is not None
            self.assertEqual(latest.mtime, 5000)

    def test_watch_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pattern = os.path.join(tmp, "a", "Users", "*", "ASFData", "*", "ASFSettings.json")
            # Nothing exists yet: watch the deepest existing ancestor.
            self.assertEqual(cal.watch_paths(pattern), [tmp])
            folder = Path(tmp, "a", "Users", "u1", "ASFData", "dev")
            folder.mkdir(parents=True)
            path = folder / "ASFSettings.json"
            path.write_text("{}", encoding="utf-8")
            self.assertEqual(set(cal.watch_paths(pattern)),
                             {str(path), str(folder), os.path.join(tmp, "a", "Users")})


class MappingTest(unittest.TestCase):
    def test_flip_y(self) -> None:
        fw = Scalars(1.1, 0.9, 1.2, 0.8)
        self.assertEqual(cal.driver_scalars(fw), fw)
        self.assertEqual(cal.driver_scalars(fw, flip_y=False), Scalars(1.1, 0.9, 1.2, 0.8))
        self.assertEqual(cal.driver_scalars(fw, flip_y=True), Scalars(1.1, 0.9, 0.8, 1.2))

    def test_validate_range(self) -> None:
        Scalars(0.1, 10.0, 1.0, 1.0).validate()
        for bad in (0.09, 10.01, float("nan"), float("inf"), -1.0):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Scalars(1.0, 1.0, bad, 1.0).validate()


if __name__ == "__main__":
    unittest.main()
