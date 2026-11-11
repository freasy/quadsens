"""Tests of the acceleration model, its limits and the values for both drivers (no Qt).

Checked against tests/fixtures/accel/vectors.json, which comes from an
independent verification of the drivers' sources (see the README next to it).
Platform-independent; also run on Windows in CI.
"""

from __future__ import annotations

import inspect
import json
import math
import random
import unittest
from pathlib import Path

from quadsens import accel_edit as ae
from quadsens.accel_edit import CLASSIC, JUMP, LINEAR, NATURAL, OFF, POWER, AccelError

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "accel" / "vectors.json")
                     .read_text(encoding="utf-8"))
SETS = [(mode, entry) for mode, entries in VECTORS["modes"].items()
        if mode in ae.MODES for entry in entries]
# How far the fork's fixed-point code may be from the reference, per mode: the
# largest relative differences the verification measured, with some room.
FIXED_TOLERANCE = {OFF: 1e-9, LINEAR: 1e-6, CLASSIC: 5e-5, POWER: 5e-5, JUMP: 5e-5}


def natural_tolerance(curve: ae.Curve, speed: float) -> float:
    """Natural with gain is the one mode where fixed point is visibly off at low speed."""
    if not curve.gain:
        return 1e-6
    return 2e-2 if speed < 0.125 else 2e-3 if speed < 1 else 1e-4


def reference_rules(mode: str, n: dict) -> list[str]:
    """The verification's own statement of the rules (its editor_violations), kept
    apart from the implementation so that the two can be compared."""
    ranges = dict(
        linear=dict(accel=(1e-4, 1.0), cap=(0.1, 10.0)),
        classic=dict(accel=(1e-4, 2.0), exponent=(1.01, 5.0), cap=(0.1, 10.0)),
        power=dict(scale=(1e-3, 5.0), exponent=(0.01, 1.0), output_offset=(0.0, 5.0),
                   cap=(0.0, 10.0)),
        natural=dict(decay_rate=(1e-3, 5.0), limit=(0.01, 8.0), offset=(0.0, 50.0)),
        jump=dict(input=(0.1, 50.0), output=(0.01, 10.0), smooth=(0.0, 1.0)), off=dict())
    v = []
    for k, (lo, hi) in ranges[mode].items():
        x = n.get(k)
        if x is None:
            if k != "cap":
                v.append("R4 %s missing" % k)
            continue
        if not lo <= x <= hi:
            v.append("R4 %s" % k)
    if mode == "natural" and not v and abs(n["limit"] - 1) < 0.01 - 1e-12:
        v.append("R4 limit")
    if v:
        return v
    lx = math.log(1.0e5)
    if mode in ("linear", "classic") and n.get("cap") is not None and n["cap"] != 1:
        e = 2.0 if mode == "linear" else n["exponent"]
        if math.log(abs(n["cap"] - 1) / e) / (e - 1) - math.log(n["accel"]) > lx:
            v.append("R1 cap")
    if mode == "power":
        e, q, c = n["exponent"], n["output_offset"], n.get("cap")
        if q > 99 * n["scale"] * e:
            v.append("R2")
        if c is not None and c <= q:
            v.append("R3")
        if q > 0 and math.log(q / (e + 1)) / e - math.log(n["scale"]) > lx:
            v.append("R1 offset")
        if c is not None and c > 0 and math.log(c / (e + 1)) / e - math.log(n["scale"]) > lx:
            v.append("R1 cap")
    if not v and mode != "off":
        try:
            if not ae.multiplier(ae.from_dict(mode, n), 1000.0) <= 1e5:
                v.append("R5")
        except OverflowError:
            v.append("R5")
    return v


def random_set(rng: random.Random, mode: str) -> dict:
    """A parameter set inside the ranges, log-uniform where the range spans decades."""
    def pick(low: float, high: float) -> float:
        if low > 0 and high / low > 50:
            return math.exp(rng.uniform(math.log(low), math.log(high)))
        return rng.uniform(low, high)
    data: dict = {field.key: pick(field.low, field.high) for field in ae.FIELDS[mode]}
    if mode in ae.CAP and rng.random() < 0.6:
        data["cap"] = pick(max(ae.CAP[mode].low, 0.05), ae.CAP[mode].high)
    if mode in ae.GAIN_MODES:
        data["gain"] = rng.random() < 0.5
    return data


class ModelTest(unittest.TestCase):
    def test_six_modes_with_the_agreed_ranges_and_defaults(self) -> None:
        self.assertEqual(ae.MODES, (OFF, LINEAR, CLASSIC, POWER, NATURAL, JUMP))
        ranges = {mode: {f.key: (f.low, f.high, f.default) for f in fields}
                  for mode, fields in ae.FIELDS.items()}
        self.assertEqual(ranges, {
            OFF: {},
            LINEAR: {"accel": (1e-4, 1.0, 0.005)},
            CLASSIC: {"accel": (1e-4, 2.0, 0.005), "exponent": (1.01, 5.0, 2.0)},
            POWER: {"scale": (1e-3, 5.0, 1.0), "exponent": (0.01, 1.0, 0.05),
                    "output_offset": (0.0, 5.0, 0.0)},
            NATURAL: {"decay_rate": (1e-3, 5.0, 0.1), "limit": (0.01, 8.0, 1.5),
                      "offset": (0.0, 50.0, 0.0)},
            JUMP: {"input": (0.1, 50.0, 15.0), "output": (0.01, 10.0, 1.5),
                   "smooth": (0.0, 1.0, 0.5)},
        })
        self.assertEqual({m: (f.low, f.high) for m, f in ae.CAP.items()},
                         {LINEAR: (0.1, 10.0), CLASSIC: (0.1, 10.0), POWER: (0.0, 10.0)})
        self.assertEqual(ae.GAIN_MODES, (NATURAL, JUMP))
        for mode in ae.MODES:                      # every default is itself allowed
            self.assertEqual(ae.violations(ae.default_curve(mode)), [], mode)
            for field in ae.FIELDS[mode]:
                # The spin boxes can show every limit without rounding it away.
                self.assertEqual(round(field.low, field.decimals), field.low, field.key)
                self.assertTrue(field.help and field.yeetmouse and field.rawaccel)

    def test_describe_and_same(self) -> None:
        self.assertEqual(ae.make(OFF).describe(), "Off")
        self.assertEqual(ae.make(CLASSIC, cap=1.5).describe(),
                         "Classic: Acceleration 0.005, Exponent 2; cap 1.5")
        self.assertEqual(ae.make(LINEAR, accel=0.05).describe(),
                         "Linear: Acceleration 0.05; cap off")
        self.assertEqual(ae.make(JUMP, gain=False).describe(),
                         "Jump: Jump at speed 15, Multiplier after the jump 1.5, "
                         "Smoothness 0.5; gain off")
        self.assertTrue(ae.make(LINEAR).same(ae.make(LINEAR)))
        self.assertFalse(ae.make(LINEAR).same(ae.make(LINEAR, cap=2)))
        self.assertFalse(ae.make(LINEAR).same(None))
        with self.assertRaises(ValueError):
            ae.make(LINEAR, exponent=3)
        with self.assertRaises(ValueError):
            ae.make("synchronous")


class ReferenceCurveTest(unittest.TestCase):
    def test_equals_rawaccel_in_double_precision(self) -> None:
        checked = 0
        for mode, entry in SETS:
            curve = ae.from_dict(mode, entry["neutral"])
            for speed_text, rawaccel, _real, _fixed in entry["points"]:
                speed = float(speed_text)
                if mode == JUMP and not curve.gain and speed == curve.get("input"):
                    continue
                got = ae.multiplier(curve, speed)
                self.assertTrue(math.isclose(got, rawaccel, rel_tol=1e-9, abs_tol=1e-12),
                                (mode, entry["neutral"], speed, got, rawaccel))
                checked += 1
        self.assertGreater(checked, 5000)

    def test_fork_fixed_point_stays_within_the_measured_distance(self) -> None:
        worst: dict[str, float] = {}
        for mode, entry in SETS:
            if not entry["editor_allows"]:
                continue                 # outside the limits the fixed point is not bound
            curve = ae.from_dict(mode, entry["neutral"])
            for speed_text, _rawaccel, _real, fixed in entry["points"]:
                speed = float(speed_text)
                if fixed is None or (mode == JUMP and abs(speed - curve.get("input")) < 1e-9
                                     and curve.get("smooth") * curve.get("input") < 1):
                    continue
                want = ae.multiplier(curve, speed)
                tolerance = natural_tolerance(curve, speed) if mode == NATURAL \
                    else FIXED_TOLERANCE[mode]
                relative = abs(fixed - want) / max(abs(want), 1e-12)
                self.assertLessEqual(relative, tolerance, (mode, entry["neutral"], speed))
                worst[mode] = max(worst.get(mode, 0.0), relative)
        # The figures are real: something does deviate, by about what was measured.
        self.assertGreater(worst[POWER], 1e-8)
        self.assertGreater(worst[NATURAL], 1e-4)

    def test_stable_at_the_ends(self) -> None:
        hard = ae.make(JUMP, input=50, output=10, smooth=0.03, gain=True)
        soft = ae.make(JUMP, input=0.1, output=10, smooth=1.0, gain=True)
        for curve in (hard, soft, ae.make(JUMP, gain=False, input=50, smooth=1.0),
                      ae.make(NATURAL, decay_rate=5, limit=8, offset=50),
                      ae.make(POWER, scale=5, exponent=1, output_offset=5, cap=10)):
            for speed in (1e-6, 0.01, 1, 50, 1000, 1e6):
                value = ae.multiplier(curve, speed)
                self.assertTrue(math.isfinite(value) and value > 0, (curve, speed, value))
        self.assertEqual(ae.multiplier(ae.make(LINEAR), 0), 1.0)
        self.assertEqual(ae.multiplier(ae.make(OFF), 123), 1.0)

    def test_marks_name_the_points_of_the_curve(self) -> None:
        self.assertEqual(ae.marks(ae.make(LINEAR)), [])
        (name, speed), = ae.marks(ae.make(LINEAR, accel=0.05, cap=2.0))
        self.assertEqual(name, "cap")
        self.assertAlmostEqual(speed, 10.0)                # (|cap-1|/2)/accel
        self.assertEqual(ae.marks(ae.make(JUMP)), [("jump", 15.0)])
        names = [name for name, _ in ae.marks(ae.make(POWER, output_offset=1, cap=3,
                                                      exponent=0.5))]
        self.assertEqual(names, ["cap", "offset ends"])
        self.assertEqual(ae.marks(ae.make(NATURAL, offset=5)), [("offset", 5.0)])


class LimitsTest(unittest.TestCase):
    def test_every_set_the_fork_must_not_get_is_refused_with_words(self) -> None:
        cases = VECTORS["fork_must_refuse_examples"]["cases"]
        self.assertEqual(len(cases), 11)
        for case in cases:
            curve = ae.from_dict(case["mode"], case["neutral"])
            reasons = ae.violations(curve)
            self.assertTrue(reasons, case)
            for reason in reasons:
                self.assertNotRegex(reason, r"\bR[1-7]\b")       # words, not codes
                self.assertTrue(reason.endswith("."), reason)
            with self.assertRaises(AccelError):
                ae.fork_values(curve)
            with self.assertRaises(AccelError):
                ae.rawaccel_changes(curve)
        trap = ae.from_dict("power", cases[0]["neutral"])
        self.assertIn("counts/ms", ae.violations(trap)[0])
        self.assertIn("yeetmouse cannot compute", ae.violations(trap)[0])
        self.assertIn("lower the cap", ae.violations(trap)[0])

    def test_agrees_with_the_verification_on_every_vector(self) -> None:
        for mode, entry in SETS:
            curve = ae.from_dict(mode, entry["neutral"])
            self.assertEqual(not ae.violations(curve), entry["editor_allows"],
                             (mode, entry["neutral"], ae.violations(curve)))

    def test_random_sweep_never_lets_a_forbidden_set_through(self) -> None:
        rng = random.Random(20261111)
        allowed = refused = 0
        for _ in range(6000):
            mode = rng.choice(ae.MODES[1:])
            data = random_set(rng, mode)
            ours = ae.violations(ae.from_dict(mode, data))
            theirs = reference_rules(mode, data)
            self.assertEqual(bool(ours), bool(theirs), (mode, data, ours, theirs))
            allowed += not ours
            refused += bool(ours)
        self.assertGreater(allowed, 2000)
        self.assertGreater(refused, 300)             # the ranges alone are not enough

    def test_specific_reasons(self) -> None:
        def why(curve: ae.Curve) -> str:
            return " ".join(ae.violations(curve))
        self.assertIn("Acceleration must be between 0.0001 and 1",
                      why(ae.make(LINEAR, accel=2e-9, cap=10)))
        self.assertIn("Limit must differ from 1", why(ae.make(NATURAL, limit=1.005)))
        self.assertIn("switch acceleration off without saying so",
                      why(ae.make(POWER, scale=0.001, exponent=0.2, output_offset=0.1)))
        self.assertIn("Cap must be above the output offset",
                      why(ae.make(POWER, exponent=0.5, output_offset=1.0, cap=1.0)))
        self.assertIn("At 1000 counts/ms the multiplier would be",
                      why(ae.make(CLASSIC, accel=1.60389, exponent=4.95483)))
        self.assertIn("output offset would hold up to",
                      why(ae.make(POWER, scale=0.9, exponent=0.05, output_offset=4.0)))
        self.assertIn("not a number", why(ae.make(LINEAR, accel=float("nan"))))
        self.assertIn("Cap must be between", why(ae.make(LINEAR, cap=50)))
        self.assertEqual(ae.violations(ae.Curve("synchronous")),
                         ["'synchronous' is not a mode QuadSens edits."])

    def test_validation_is_the_only_way_to_driver_values(self) -> None:
        bad = ae.make(LINEAR, accel=5.0)
        for producer in (ae.fork_values, ae.rawaccel_changes):
            with self.assertRaises(AccelError) as caught:
                producer(bad)
            self.assertTrue(caught.exception.reasons)
            self.assertIn("checked(curve)", inspect.getsource(producer))
        # Nothing else in the module turns a curve into driver values.
        public = [name for name, value in vars(ae).items()
                  if inspect.isfunction(value) and not name.startswith("_")
                  and "curve" in inspect.signature(value).parameters]
        producers = {"fork_values", "rawaccel_changes"}
        readers = {"multiplier", "marks", "violations", "checked", "cap_point", "offset_point"}
        self.assertEqual(set(public), producers | readers)


class ForkTest(unittest.TestCase):
    def test_values_match_the_vectors(self) -> None:
        for mode, entry in SETS:
            if not entry["editor_allows"]:
                continue
            values = ae.fork_values(ae.from_dict(mode, entry["neutral"]))
            fork = entry["fork"]
            self.assertEqual(values["AccelerationMode"], str(fork["AccelerationMode"]))
            self.assertEqual(values["UseSmoothing"], str(fork["UseSmoothing"]))
            for name in ae.FORK_SLOTS:
                self.assertAlmostEqual(float(values[name]), float(fork[name]), places=9,
                                       msg=(mode, entry["neutral"], name))

    def test_numbers_are_written_as_the_parser_needs_them(self) -> None:
        self.assertEqual(ae.fork_decimal(0.00005), "0.0000500000")
        self.assertEqual(ae.fork_decimal(0.5), "0.5000000000")
        self.assertEqual(ae.fork_decimal(0), "0.0000000000")
        self.assertEqual(ae.fork_decimal(1e-4), "0.0001000000")
        for bad in (float("nan"), float("inf"), -1.0, 1e7):
            with self.assertRaises(AccelError):
                ae.fork_decimal(bad)
        rng = random.Random(7)
        for _ in range(500):
            mode = rng.choice(ae.MODES)
            curve = ae.from_dict(mode, random_set(rng, mode))
            if ae.violations(curve):
                continue
            values = ae.fork_values(curve)
            self.assertEqual(set(values), {*ae.FORK_SLOTS, "AccelerationMode", "UseSmoothing"})
            for name in ae.FORK_SLOTS:
                self.assertRegex(values[name], r"^[0-9]{1,2}\.[0-9]{10}$")

    def test_write_order_strings_then_switch_then_update(self) -> None:
        order = ae.fork_write_order(ae.fork_values(ae.make(POWER, cap=3, exponent=0.5)))
        self.assertEqual([name for name, _ in order],
                         ["Acceleration", "Exponent", "Midpoint", "Motivity",
                          "AccelerationMode", "UseSmoothing", "update"])
        self.assertEqual(order[-2:], [("UseSmoothing", "1"), ("update", "1")])

    def test_read_back_gives_the_same_curve(self) -> None:
        rng = random.Random(3)
        neutral = {"Offset": "0", "InputCap": "0", "OutputCap": "0", "PreScale": "1"}
        seen = 0
        for _ in range(800):
            mode = rng.choice(ae.MODES)
            curve = ae.from_dict(mode, random_set(rng, mode))
            if ae.violations(curve):
                continue
            found = ae.classify_fork({**ae.fork_values(curve), **neutral})
            self.assertTrue(found.editable, (curve, found.reason))
            assert found.curve is not None
            self.assertEqual(found.curve.mode, curve.mode)
            self.assertEqual(found.curve.cap is None, curve.cap is None)
            self.assertEqual(found.curve.gain, curve.gain)
            for (key, a), (_key, b) in zip(found.curve.values, curve.values):
                self.assertAlmostEqual(a, b, places=9, msg=key)
            seen += 1
        self.assertGreater(seen, 300)

    def test_what_is_read_only_and_why(self) -> None:
        base = {**ae.fork_values(ae.make(LINEAR)), "Offset": "0", "InputCap": "0",
                "OutputCap": "0", "PreScale": "1"}

        def reason(**changes: str) -> str:
            found = ae.classify_fork({**base, **changes})
            self.assertFalse(found.editable, changes)
            return found.reason
        self.assertIn("Offset is 2", reason(Offset="2"))
        self.assertIn("InputCap is 40", reason(InputCap="40"))
        self.assertIn("OutputCap is 3", reason(OutputCap="3.0"))
        self.assertIn("PreScale is 0.8", reason(PreScale="0.8"))
        for mode in ("4", "5", "8", "9", "12"):
            self.assertIn("mode %s, which QuadSens does not edit" % mode,
                          reason(AccelerationMode=mode))
        self.assertIn("bends the curve down to 0", reason(UseSmoothing="1", Midpoint="0"))
        self.assertIn("Classic needs more than 1", reason(AccelerationMode="3", Exponent="1"))
        self.assertIn("Jump", reason(AccelerationMode="7", Exponent="1.5"))
        self.assertIn("Jump", reason(AccelerationMode="7", Acceleration="0"))
        self.assertIn("negative", reason(Midpoint="-1"))
        self.assertIn("outside what QuadSens writes", reason(Acceleration="0.00000001"))
        self.assertIn("could not be read", reason(AccelerationMode="banana"))
        self.assertIn("could not be read", reason(Acceleration="x"))
        self.assertIn("could not be read", reason(UseSmoothing="2"))
        # The fork's own default (Linear 0.1, UseSmoothing 1, Midpoint 1.3) is a capped line.
        default = ae.classify_fork({**base, "Acceleration": "0.1", "Exponent": "1.8",
                                    "Midpoint": "1.3", "UseSmoothing": "1"})
        self.assertEqual(default.curve, ae.make(LINEAR, accel=0.1, cap=1.3))
        self.assertEqual(ae.classify_fork({**base, "AccelerationMode": "0"}).curve,
                         ae.make(OFF))


RA_DEFAULTS = {"mode": "noaccel", "Gain / Velocity": True, "inputOffset": 0.0,
               "outputOffset": 0.0, "acceleration": 0.005, "decayRate": 0.1, "gamma": 1.0,
               "motivity": 1.5, "exponentClassic": 2.0, "scale": 1.0, "exponentPower": 0.05,
               "limit": 1.5, "syncSpeed": 5.0, "smooth": 0.5,
               "Cap / Jump": {"x": 15.0, "y": 1.5}, "Cap mode": "output", "data": []}


def profile_with(args: dict, **extra: object) -> dict:
    return {ae.RA_ARGS: args, ae.RA_VERTICAL: dict(RA_DEFAULTS),
            ae.RA_SPEED: {ae.RA_WHOLE: True, "lpNorm": 2.0,
                          **{key: 0.0 for key in ae.RA_HALFLIVES}},
            ae.RA_DOMAIN: {"x": 1.0, "y": 1.0}, ae.RA_RANGE: {"x": 1.0, "y": 1.0},
            ae.RA_SPEED_CAP: 0.0, **extra}


class RawAccelTest(unittest.TestCase):
    def test_changes_match_the_vectors(self) -> None:
        for mode, entry in SETS:
            if not entry["editor_allows"]:
                continue
            curve = ae.from_dict(mode, entry["neutral"])
            # From whatever another mode left behind: a cap of 1.5 and so on.
            got = ae.apply_changes(RA_DEFAULTS, ae.rawaccel_changes(curve))
            want = entry["rawaccel"]
            for key in ("mode", "Gain / Velocity", "Cap mode", "data"):
                self.assertEqual(got[key], want[key], (mode, entry["neutral"], key))
            relevant = {LINEAR: ("acceleration", "exponentClassic", "inputOffset"),
                        CLASSIC: ("acceleration", "exponentClassic", "inputOffset"),
                        POWER: ("scale", "exponentPower", "outputOffset"),
                        NATURAL: ("decayRate", "limit", "inputOffset"),
                        JUMP: ("smooth",), OFF: ()}[mode]
            for key in relevant:
                self.assertAlmostEqual(got[key], want[key], places=12, msg=key)
            if mode in (LINEAR, CLASSIC, POWER, JUMP):
                self.assertAlmostEqual(got["Cap / Jump"]["y"], want["Cap / Jump"]["y"])
            if mode == JUMP:
                self.assertAlmostEqual(got["Cap / Jump"]["x"], want["Cap / Jump"]["x"])
            self.assertEqual(ae.rawaccel_errors(got), [], (mode, entry["neutral"]))
            self.assertEqual(set(got), set(RA_DEFAULTS))         # no key added or dropped

    def test_cap_off_is_written_because_the_slot_is_shared(self) -> None:
        changes = ae.rawaccel_changes(ae.make(LINEAR))
        self.assertEqual(changes[("Cap / Jump", "y")], 0.0)      # not the leftover 1.5
        self.assertEqual(changes[("Cap mode",)], "output")
        self.assertIs(changes[("Gain / Velocity",)], True)
        self.assertEqual(changes[("inputOffset",)], 0.0)
        self.assertEqual(changes[("exponentClassic",)], 2.0)
        self.assertEqual(ae.rawaccel_changes(ae.make(OFF)), {("mode",): "noaccel"})

    def test_read_back_gives_the_same_curve(self) -> None:
        rng = random.Random(5)
        for _ in range(600):
            mode = rng.choice(ae.MODES)
            curve = ae.from_dict(mode, random_set(rng, mode))
            if ae.violations(curve):
                continue
            args = ae.apply_changes(RA_DEFAULTS, ae.rawaccel_changes(curve))
            found = ae.classify_rawaccel(profile_with(args))
            self.assertTrue(found.editable, (curve, found.reason))
            self.assertTrue(found.curve.same(curve), (found.curve, curve))   # type: ignore
        linear = ae.classify_rawaccel(profile_with({**RA_DEFAULTS, "mode": "classic",
                                                    "Cap / Jump": {"x": 15.0, "y": 0.0}}))
        self.assertEqual(linear.curve, ae.make(LINEAR))          # classic with exponent 2

    def test_what_is_read_only_and_why(self) -> None:
        classic = {**RA_DEFAULTS, "mode": "classic", "Cap / Jump": {"x": 15.0, "y": 0.0}}

        def reason(args: dict, settings: dict | None = None, **extra: object) -> str:
            found = ae.classify_rawaccel(profile_with(args, **extra), settings)
            self.assertFalse(found.editable)
            return found.reason
        speed = {ae.RA_WHOLE: True, "lpNorm": 2.0, **{key: 0.0 for key in ae.RA_HALFLIVES}}
        self.assertIn("by component", reason(classic, **{ae.RA_SPEED: {**speed,
                                                                       ae.RA_WHOLE: False}}))
        self.assertIn("lpNorm", reason(classic, **{ae.RA_SPEED: {**speed, "lpNorm": 4.0}}))
        self.assertIn("halflife", reason(classic, **{ae.RA_SPEED: {
            **speed, ae.RA_HALFLIVES[1]: 5.0}}))
        self.assertIn("domain weights", reason(classic, **{ae.RA_DOMAIN: {"x": 2.0, "y": 1.0}}))
        self.assertIn("range weights", reason(classic, **{ae.RA_RANGE: {"x": 1.0, "y": 0.5}}))
        self.assertIn("input speed cap", reason(classic, **{ae.RA_SPEED_CAP: 50.0}))
        for mode in ("lut", "synchronous", "motivity", "quantum"):
            self.assertIn('mode "%s"' % mode, reason({**RA_DEFAULTS, "mode": mode}))
        self.assertIn("cap mode", reason({**classic, "Cap mode": "input"}))
        self.assertIn("cap mode", reason({**RA_DEFAULTS, "mode": "power", "Cap mode": "in_out"}))
        self.assertIn("input offset", reason({**classic, "inputOffset": 3.0}))
        self.assertIn("without gain", reason({**classic, "Gain / Velocity": False,
                                              "Cap / Jump": {"x": 15.0, "y": 2.0}}))
        self.assertIn("equal", reason({**RA_DEFAULTS, "mode": "power", "outputOffset": 1.5}))
        self.assertIn("Limit is 1", reason({**RA_DEFAULTS, "mode": "natural", "limit": 1.0}))
        self.assertIn("incomplete", reason({"mode": "classic"}))
        self.assertIn("outside what QuadSens writes", reason({**classic, "acceleration": 50.0}))
        self.assertIn("device DPI", reason(classic, {"defaultDeviceConfig": {
            ae.RA_DPI_KEYS[0]: 1600}}))
        self.assertIn("device DPI", reason(classic, {"devices": [{"config": {
            ae.RA_DPI_KEYS[1]: 800}}]}))
        self.assertIn("no acceleration settings", ae.classify_rawaccel({}).reason)
        # A 1.6 profile keeps the whole switch at the top level.
        old = profile_with(classic)
        del old[ae.RA_SPEED]
        old[ae.RA_WHOLE] = True
        self.assertTrue(ae.classify_rawaccel(old).editable)
        old[ae.RA_WHOLE] = False
        self.assertIn("by component", ae.classify_rawaccel(old).reason)

    def test_port_of_rawaccels_own_validation(self) -> None:
        self.assertEqual(ae.rawaccel_errors(RA_DEFAULTS), [])
        cases = {"offset can not be negative": {"inputOffset": -1.0},
                 "cap (input) can not be 0": {"mode": "jump", "Cap / Jump": {"x": 0.0, "y": 2}},
                 "cap (output) can not be negative": {"Cap / Jump": {"x": 1.0, "y": -1.0}},
                 "cap < offset": {"mode": "power", "outputOffset": 2.0,
                                  "Cap / Jump": {"x": 15.0, "y": 1.0}},
                 "acceleration must be positive": {"acceleration": 0.0},
                 "decay rate must be positive": {"decayRate": 0.0},
                 "exponent must be greater than 1": {"exponentClassic": 1.0},
                 "exponent must be positive": {"exponentPower": 0.0},
                 "limit must be positive": {"limit": 0.0},
                 "smooth must be between 0 and 1": {"smooth": 1.5},
                 "scale must be positive": {"scale": "big"}}
        for message, change in cases.items():
            self.assertIn(message, ae.rawaccel_errors({**RA_DEFAULTS, **change}), message)


if __name__ == "__main__":
    unittest.main()
