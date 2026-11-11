"""Tests of the read-only acceleration model and its readers (no Qt).

Platform-independent; also run on Windows in CI.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from quadsens import accel

FIXTURES = Path(__file__).parent / "fixtures" / "rawaccel"
SAMPLE = """sens=1
accel=0.1
exponent=1.8
midpoint=1.3
motivity=1.5
offset=0
inCap=0
outCap=0
accelMode=AccelMode_Linear
useSmoothing=1
"""


class YeetmouseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.params = Path(self._tmp.name) / "parameters"
        self.params.mkdir()
        self.config = Path(self._tmp.name) / "yeetmouse.conf"

    def write(self, **values: str) -> None:
        base = {"AccelerationMode": "1", "Acceleration": "0.05", "Exponent": "1.8",
                "Midpoint": "1.3", "Motivity": "1.5", "Offset": "0", "InputCap": "0",
                "OutputCap": "0", "UseSmoothing": "1"}
        base.update(values)
        for name, text in base.items():
            (self.params / name).write_text(text + "\n")

    def test_linear_as_the_driver_holds_it(self) -> None:
        self.write()
        found = accel.read_yeetmouse_params(self.params)
        self.assertEqual((found.mode, found.raw_mode, found.known), (accel.LINEAR, "1", True))
        self.assertEqual(found.params, (("Acceleration", 0.05), ("Midpoint", 1.3)))
        self.assertEqual(found.summary(), "Acceleration: Linear, Acceleration 0.05, Midpoint "
                                          "1.3; cap none; UseSmoothing on")
        self.write(UseSmoothing="0")              # without it the midpoint is not read
        found = accel.read_yeetmouse_params(self.params)
        self.assertEqual(found.params, (("Acceleration", 0.05),))
        self.assertEqual(found.summary(),
                         "Acceleration: Linear, Acceleration 0.05; cap none; UseSmoothing off")
        self.assertEqual(found.short(), "Acceleration: Linear, 0.05; cap none")
        self.write(AccelerationMode="2")
        self.assertIn("Motivity 1.5", accel.read_yeetmouse_params(self.params).summary())

    def test_every_mode_number_of_the_fork(self) -> None:
        expected = {0: "off", 2: "Power", 3: "Classic", 4: "Motivity", 5: "Synchronous",
                    6: "Natural", 7: "Jump", 8: "Look-up table", 9: "Custom curve"}
        for code, title in expected.items():
            self.write(AccelerationMode=str(code))
            found = accel.read_yeetmouse_params(self.params)
            self.assertIn(title, found.summary(), code)
            self.assertTrue(found.known)
        self.write(AccelerationMode="3", OutputCap="2.5", InputCap="40", Offset="1.5",
                   UseSmoothing="0")
        text = accel.read_yeetmouse_params(self.params).summary()
        self.assertEqual(text, "Acceleration: Classic, Acceleration 0.05, Exponent 1.8; "
                               "offset 1.5; input cap 40, output cap 2.5; UseSmoothing off")

    def test_off_in_both_ways(self) -> None:
        self.write(AccelerationMode="0")
        self.assertEqual(accel.read_yeetmouse_params(self.params).summary(),
                         "Acceleration: off")
        self.write(Acceleration="0")          # Linear with 0: the driver applies no curve
        found = accel.read_yeetmouse_params(self.params)
        self.assertTrue(found.is_off)
        self.assertEqual(found.raw_mode, "1")

    def test_unknown_modes_are_shown_raw_and_never_guessed(self) -> None:
        for raw in ("12", "AccelMode_Future", "banana"):
            self.write(AccelerationMode=raw)
            found = accel.read_yeetmouse_params(self.params)
            self.assertFalse(found.known)
            self.assertEqual(found.raw_mode, raw)
            self.assertEqual(found.summary(),
                             "Acceleration: mode %s (not known to QuadSens)" % raw)
            self.assertEqual(found.params, ())

    def test_problems_are_named(self) -> None:
        self.assertIn("module is not loaded",
                      accel.read_yeetmouse_params(self.params / "missing").summary())
        self.assertIn("no acceleration mode parameter",
                      accel.read_yeetmouse_params(self.params).summary())
        self.assertEqual(accel.read_yeetmouse_params(self.params).short(),
                         "Acceleration: not read")

    def test_config_file_is_the_fallback_and_says_so(self) -> None:
        self.config.write_text(SAMPLE + "# comment\nbroken line\n")
        stored = accel.read_yeetmouse_config(self.config)
        self.assertEqual(stored.summary(), "Acceleration: Linear, Acceleration 0.1, Midpoint "
                                           "1.3; cap none; UseSmoothing on")
        found = accel.read_yeetmouse(self.params / "missing", self.config)
        self.assertEqual(found.mode, accel.LINEAR)
        self.assertIn("from the config file, not from the driver", found.summary())
        self.write()                                      # the driver wins when readable
        self.assertNotIn("config file", accel.read_yeetmouse(self.params, self.config).summary())
        self.config.unlink()
        self.assertIn("not loaded",
                      accel.read_yeetmouse(self.params / "missing", self.config).summary())
        self.assertIn("cannot be read", accel.read_yeetmouse_config(self.config).summary())

    def test_names_of_the_fork(self) -> None:
        self.assertEqual(accel.YEETMOUSE_GUI, "yeetmouse")
        self.assertEqual(accel.YEETMOUSE_CONFIG_KEYS["AccelerationMode"], "accelMode")
        self.assertEqual(sorted(accel.YEETMOUSE_MODES), list(range(10)))


class RawAccelTest(unittest.TestCase):
    def profiles(self, name: str) -> list[dict]:
        return json.loads((FIXTURES / name).read_text())["profiles"]

    def test_profiles_of_1_7(self) -> None:
        default, flick, tracking = self.profiles("settings-1.7.1-profiles.json")
        self.assertEqual(accel.read_rawaccel_profile(default).summary(), "Acceleration: off")
        found = accel.read_rawaccel_profile(flick)
        self.assertEqual((found.mode, found.raw_mode), (accel.CLASSIC, "classic"))
        self.assertEqual(found.params, (("acceleration", 0.005), ("exponentClassic", 2.0)))
        self.assertEqual(found.summary(),
                         "Acceleration: Classic, acceleration 0.005, exponentClassic 2; "
                         "cap (output) input 15, output 1.5; gain")
        self.assertEqual(accel.read_rawaccel_profile(tracking).summary(),
                         "Acceleration: Natural, decayRate 0.1, limit 1.5; gain")

    def test_profile_of_1_6(self) -> None:
        (default,) = self.profiles("settings-1.6.1-default.json")
        self.assertTrue(accel.read_rawaccel_profile(default).is_off)
        default[accel.WHOLE_ARGS]["mode"] = "motivity"
        found = accel.read_rawaccel_profile(default)
        self.assertEqual(found.mode, accel.MOTIVITY)
        self.assertIn("Motivity", found.summary())

    def test_by_component_names_both_axes(self) -> None:
        _default, flick, _tracking = self.profiles("settings-1.7.1-profiles.json")
        flick[accel.SPEED_ARGS][accel.WHOLE_KEY] = False
        flick[accel.VERTICAL_ARGS]["mode"] = "power"
        text = accel.read_rawaccel_profile(flick).summary()
        self.assertIn("Classic", text)
        self.assertIn("horizontal only; vertical: Power, scale 1, exponentPower 0.05", text)
        flick[accel.WHOLE_ARGS]["mode"] = "noaccel"       # off horizontally, not as a whole
        found = accel.read_rawaccel_profile(flick)
        self.assertFalse(found.is_off)
        self.assertIn("vertical: Power", found.summary())

    def test_unknown_mode_and_broken_profiles(self) -> None:
        (default,) = self.profiles("settings-1.6.1-default.json")
        default[accel.WHOLE_ARGS]["mode"] = "quantum"
        found = accel.read_rawaccel_profile(default)
        self.assertFalse(found.known)
        self.assertEqual(found.summary(), "Acceleration: mode quantum (not known to QuadSens)")
        self.assertIn("no acceleration settings", accel.read_rawaccel_profile({}).summary())
        default[accel.WHOLE_ARGS] = {"mode": "classic", "acceleration": "x", "Cap / Jump": 5}
        self.assertEqual(accel.read_rawaccel_profile(default).summary(),
                         "Acceleration: Classic")
        default[accel.WHOLE_ARGS] = {"mode": "lut", "data": [1, 2, 3, 4]}
        self.assertEqual(accel.read_rawaccel_profile(default).summary(),
                         "Acceleration: Look-up table; 4 values")

    def test_nothing_here_writes(self) -> None:
        import inspect
        source = inspect.getsource(accel)
        for word in ("write_text", "open(", ".write(", "os.replace", "unlink"):
            self.assertNotIn(word, source)


if __name__ == "__main__":
    unittest.main()
