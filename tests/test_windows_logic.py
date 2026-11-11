"""Tests of the Qt-free pieces of the Windows side: platform, values file, RawAccel settings.

Everything here runs on Linux. writer.exe is a fake runner; no file outside a
temp directory is touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import types
import unittest
from pathlib import Path

from quadsens import platforms, rawaccel, values_file
from quadsens.model import Scalars
from quadsens.rawaccel import Four, RawAccelError
from quadsens.values_file import ValuesError

FIXTURES = Path(__file__).parent / "fixtures" / "rawaccel"
F17 = FIXTURES / "settings-1.7.1-default.json"
F17_MULTI = FIXTURES / "settings-1.7.1-profiles.json"
F16 = FIXTURES / "settings-1.6.1-default.json"
VALUES = Scalars(1.011, 0.991, 1.006, 0.996)   # right, left, down, up


def raw(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


class PlatformTest(unittest.TestCase):
    def test_detection_and_override(self) -> None:
        self.assertEqual(platforms.current({}, "linux"), "linux")
        self.assertEqual(platforms.current({}, "win32"), "windows")
        self.assertEqual(platforms.current({}, "darwin"), "linux")
        self.assertEqual(platforms.current({"QUADSENS_PLATFORM": "windows"}, "linux"),
                         "windows")
        self.assertEqual(platforms.current({"QUADSENS_PLATFORM": " Linux "}, "win32"), "linux")
        self.assertEqual(platforms.current({"QUADSENS_PLATFORM": "amiga"}, "linux"), "linux")

    def test_windows_result_location(self) -> None:
        pattern = platforms.windows_result_glob({"USERPROFILE": "C:\\Users\\someone"})
        self.assertEqual(pattern, "C:\\Users\\someone\\AppData\\LocalLow\\Statespace\\"
                                  "aimlab_tb\\Users\\*\\ASFData\\*\\ASFSettings.json")
        from_parts = platforms.windows_result_glob({"HOMEDRIVE": "D:", "HOMEPATH": "\\u"})
        self.assertTrue(from_parts.startswith("D:\\u\\AppData\\LocalLow\\"))
        # On the developer's Linux machine the same layout is used with slashes.
        posix = platforms.windows_result_glob({"USERPROFILE": "/tmp/fake-profile"})
        self.assertEqual(posix, "/tmp/fake-profile/AppData/LocalLow/Statespace/aimlab_tb/"
                                "Users/*/ASFData/*/ASFSettings.json")
        self.assertEqual(platforms.default_result_glob("windows",
                                                       {"USERPROFILE": "C:\\Users\\x"}),
                         platforms.windows_result_glob({"USERPROFILE": "C:\\Users\\x"}))
        self.assertIn("compatdata", platforms.default_result_glob("linux"))


class ValuesFileTest(unittest.TestCase):
    def test_round_trip(self) -> None:
        measured = (1.012, 1.006, 0.991, 0.996, 0.988, 0.992, 1.009, 1.014)
        text = values_file.dump(VALUES, measured, "0.4.0", created=0)
        loaded = values_file.load(text)
        self.assertEqual(loaded.scalars, VALUES)
        self.assertEqual(loaded.measured, measured)
        self.assertEqual((loaded.created, loaded.app_version), ("1970-01-01T00:00:00Z", "0.4.0"))
        data = json.loads(text)
        self.assertEqual(list(data), ["format", "version", "multipliers", "measured",
                                      "created", "quadsens"])
        self.assertEqual(data["multipliers"], {"right": 1.011, "left": 0.991, "down": 1.006,
                                               "up": 0.996})
        self.assertTrue(text.endswith("\n"))
        without = values_file.load(values_file.dump(VALUES))
        self.assertIsNone(without.measured)

    def test_nothing_personal_is_written(self) -> None:
        text = values_file.dump(VALUES, None, "0.4.0").lower()
        for word in (os.path.expanduser("~").lower(), "user", "path", "serial", "host",
                     "device"):
            self.assertNotIn(word, text)

    def test_rejections(self) -> None:
        good = json.loads(values_file.dump(VALUES, None, "x", created=0))

        def changed(**changes: object) -> str:
            data = json.loads(json.dumps(good))
            data.update(changes)
            return json.dumps(data)

        cases = {
            "not json": "not valid JSON",
            "[1, 2]": "top level is not a JSON object",
            json.dumps({"multipliers": good["multipliers"]}): "not a QuadSens values file",
            changed(format="other"): "not a QuadSens values file",
            changed(version=2): "Update QuadSens",
            changed(version=0): "unknown version 0",
            changed(version="1"): "no usable version",
            changed(version=True): "no usable version",
            changed(multipliers=[1, 1, 1, 1]): "no \"multipliers\" block",
            changed(multipliers={"right": 1, "left": 1, "down": 1}): "\"up\" is missing",
            changed(multipliers={"right": 1, "left": 1, "down": 1, "up": "1"}): "not a number",
            changed(multipliers={"right": 1, "left": 1, "down": 1, "up": True}): "not a number",
            changed(multipliers={"right": 11, "left": 1, "down": 1, "up": 1}): "outside 0.1 to 10",
            changed(multipliers={"right": 0.05, "left": 1, "down": 1, "up": 1}): "outside",
            changed(multipliers={"right": 1, "left": 1, "down": 1, "up": 1, "x": 1}):
                "unknown direction",
            changed(measured=[1, 2, 3]): "list of eight numbers",
            changed(measured=[1] * 7 + ["x"]): "not a number",
            "x" * (values_file.MAX_SIZE + 1): "too large",
        }
        for text, expected in cases.items():
            with self.assertRaises(ValuesError) as caught:
                values_file.load(text)
            self.assertIn(expected, str(caught.exception), text[:40])
        nan = values_file.dump(VALUES).replace("1.011", "NaN")
        with self.assertRaisesRegex(ValuesError, "finite"):
            values_file.load(nan)
        with self.assertRaises(ValuesError):
            values_file.dump(Scalars(0.0, 1, 1, 1))
        with self.assertRaises(ValuesError):
            values_file.dump(VALUES, (1.0, 2.0))

    def test_extra_keys_of_a_known_version_are_ignored(self) -> None:
        data = json.loads(values_file.dump(VALUES))
        data["note"] = "from my other machine"
        self.assertEqual(values_file.load(json.dumps(data)).scalars, VALUES)


class SchemaTest(unittest.TestCase):
    def test_key_names_as_in_the_sources(self) -> None:
        self.assertEqual(rawaccel.SCHEMA_17.keys, (
            "Output DPI", "Y/X output DPI ratio (vertical sens multiplier)",
            "L/R output DPI ratio (left sens multiplier)",
            "U/D output DPI ratio (up sens multiplier)"))
        self.assertEqual(rawaccel.SCHEMA_16.keys, (
            "Sensitivity multiplier", "Y/X sensitivity ratio (vertical sens multiplier)",
            "L/R sensitivity ratio (left sens multiplier)",
            "U/D sensitivity ratio (up sens multiplier)"))
        self.assertEqual((rawaccel.SCHEMA_17.unit, rawaccel.SCHEMA_16.unit), (1000.0, 1.0))

    def test_recognises_1_7_and_1_6(self) -> None:
        new = rawaccel.read(F17)
        self.assertEqual((new.schema.name, new.version, new.profile_names),
                         ("1.7", "1.7.1", ["default"]))
        self.assertEqual(new.values(0), Four(1000.0, 1.0, 1.0, 1.0))
        self.assertFalse(new.has_acceleration(0))
        old = rawaccel.read(F16)
        self.assertEqual((old.schema.name, old.version), ("1.6", "1.6.1"))
        self.assertEqual(old.values(0), Four(1.0, 1.0, 1.0, 1.0))

    def test_several_profiles(self) -> None:
        document = rawaccel.read(F17_MULTI)
        self.assertEqual(document.profile_names, ["default", "flick", "tracking"])
        self.assertEqual(document.values(1), Four(800.0, 1.1, 0.98, 1.02))
        self.assertEqual([document.has_acceleration(i) for i in range(3)],
                         [False, True, True])
        self.assertIn("approximation", rawaccel.accuracy_note(document, 1))
        self.assertIn("exactly", rawaccel.accuracy_note(document, 0))
        with self.assertRaisesRegex(RawAccelError, "no profile number 4"):
            document.values(3)

    def test_refuses_what_it_does_not_recognise(self) -> None:
        data = json.loads(raw(F17))
        cases = {
            "{ broken": "not valid JSON",
            "[]": "does not hold a JSON object",
            json.dumps({"version": "1.7.1"}): "no list of profiles",
            json.dumps({"profiles": []}): "no list of profiles",
            json.dumps({"profiles": ["x"]}): "no list of profiles",
        }
        renamed = json.loads(raw(F17))
        renamed["version"] = "2.0.0"
        renamed["profiles"][0]["Output scale"] = renamed["profiles"][0].pop("Output DPI")
        cases[json.dumps(renamed)] = "it says version 2.0.0"
        mixed = json.loads(raw(F17_MULTI))
        del mixed["profiles"][2]["U/D output DPI ratio (up sens multiplier)"]
        cases[json.dumps(mixed)] = "does not guess"
        text_value = json.loads(raw(F17))
        text_value["profiles"][0]["Output DPI"] = "1000"
        cases[json.dumps(text_value)] = "neither the setting names"
        for text, expected in cases.items():
            with self.assertRaises(RawAccelError) as caught:
                rawaccel.parse(text)
            self.assertIn(expected, str(caught.exception))
        data["profiles"][0]["Output DPI"] = 0
        with self.assertRaisesRegex(RawAccelError, "zero or negative"):
            rawaccel.parse(json.dumps(data)).values(0)

    def test_missing_and_unreadable_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RawAccelError, "does not exist"):
                rawaccel.read(Path(tmp) / "settings.json")
            binary = Path(tmp) / "settings.json"
            binary.write_bytes(b"\xff\xfe\x00broken")
            with self.assertRaisesRegex(RawAccelError, "cannot be read"):
                rawaccel.read(binary)
            with_bom = Path(tmp) / "bom.json"
            with_bom.write_bytes(b"\xef\xbb\xbf" + F17.read_bytes())
            self.assertEqual(rawaccel.read(with_bom).schema.name, "1.7")


class MappingTest(unittest.TestCase):
    def test_mapping_on_defaults(self) -> None:
        new = rawaccel.map_values(Four(1000.0, 1.0, 1.0, 1.0), VALUES)
        self.assertAlmostEqual(new.sens, 1011.0)
        self.assertAlmostEqual(new.yx, 1.006 / 1.011)
        self.assertAlmostEqual(new.lr, 0.991 / 1.011)
        self.assertAlmostEqual(new.ud, 0.996 / 1.006)
        # What RawAccel then applies is exactly the four multipliers.
        got = rawaccel.effective(new, rawaccel.SCHEMA_17)
        for a, b in zip(got.as_tuple(), VALUES.as_tuple()):
            self.assertAlmostEqual(a, b)

    def test_mapping_keeps_the_users_own_settings(self) -> None:
        own = Four(0.8, 1.1, 0.98, 1.02)          # a 1.6 profile with its own taste
        new = rawaccel.map_values(own, VALUES)
        before = rawaccel.effective(own, rawaccel.SCHEMA_16).as_tuple()
        after = rawaccel.effective(new, rawaccel.SCHEMA_16).as_tuple()
        for was, now, factor in zip(before, after, VALUES.as_tuple()):
            self.assertAlmostEqual(now, was * factor)   # each direction times its multiplier

    def test_unity_changes_nothing_and_flip_swaps_up_and_down(self) -> None:
        own = Four(800.0, 1.1, 0.98, 1.02)
        self.assertTrue(rawaccel.map_values(own, Scalars.unity()).is_close(own))
        flipped = rawaccel.map_values(Four(1000.0, 1, 1, 1), VALUES, flip=True)
        got = rawaccel.effective(flipped, rawaccel.SCHEMA_17)
        self.assertAlmostEqual(got.y_pos, VALUES.y_neg)   # down gets the "up" value
        self.assertAlmostEqual(got.y_neg, VALUES.y_pos)
        with self.assertRaises(RawAccelError):
            rawaccel.map_values(own, Scalars(0.0, 1, 1, 1))

    def test_applying_twice_does_not_compound(self) -> None:
        original = Four(1000.0, 1.0, 1.0, 1.0)
        first = rawaccel.map_values(original, VALUES)
        # Second run: the file now holds what QuadSens wrote; the baseline stays.
        baseline, note = rawaccel.resolve_baseline(first, original, first)
        self.assertEqual((baseline, note), (original, ""))
        self.assertTrue(rawaccel.map_values(baseline, VALUES).is_close(first))
        other = Scalars(1.02, 0.98, 1.0, 1.0)
        second = rawaccel.map_values(baseline, other)
        self.assertAlmostEqual(second.sens, 1020.0)           # not 1011 * 1.02
        # Restoring is writing the baseline again.
        baseline, _note = rawaccel.resolve_baseline(second, original, second)
        self.assertEqual(baseline, original)

    def test_baseline_follows_changes_made_in_rawaccel(self) -> None:
        original = Four(1000.0, 1.0, 1.0, 1.0)
        written = rawaccel.map_values(original, VALUES)
        self.assertEqual(rawaccel.resolve_baseline(original, None, None), (original, ""))
        self.assertEqual(rawaccel.resolve_baseline(original, original, written)[0], original)
        changed = Four(1200.0, 1.0, 1.0, 1.0)     # the user set this in RawAccel since
        baseline, note = rawaccel.resolve_baseline(changed, original, written)
        self.assertEqual(baseline, changed)
        self.assertIn("changed outside QuadSens", note)

    def test_baseline_key_and_four(self) -> None:
        a = rawaccel.baseline_key("/x/settings.json", "default")
        self.assertEqual(a, rawaccel.baseline_key("/x/settings.json", "default"))
        self.assertNotEqual(a, rawaccel.baseline_key("/x/settings.json", "flick"))
        self.assertNotEqual(a, rawaccel.baseline_key("/y/settings.json", "default"))
        self.assertRegex(a, r"^[0-9a-f]{16}$")
        self.assertEqual(Four.from_list([1000, 1, 1, 1]), Four(1000.0, 1.0, 1.0, 1.0))
        for bad in (None, [1, 2, 3], ["a", 1, 1, 1], [0, 1, 1, 1], [float("nan"), 1, 1, 1]):
            self.assertIsNone(Four.from_list(bad))
        self.assertEqual(rawaccel.number_text(1011), "1011.0")
        self.assertEqual(rawaccel.number_text(0.9950544015825915), "0.995054")


class PatchTest(unittest.TestCase):
    def test_only_the_four_numbers_change(self) -> None:
        for path, index in ((F17, 0), (F17_MULTI, 1), (F16, 0)):
            document = rawaccel.read(path)
            new = rawaccel.map_values(document.values(index), VALUES)
            text = rawaccel.patch(document, index, new)
            old_lines, new_lines = document.text.split("\r\n"), text.split("\r\n")
            self.assertEqual(len(old_lines), len(new_lines))
            changed = [(a, b) for a, b in zip(old_lines, new_lines) if a != b]
            self.assertEqual(len(changed), 4, path.name)
            for before, after in changed:
                key = before.split(":")[0]
                self.assertEqual(key, after.split(":")[0])
                self.assertIn(key.strip().strip('"'), document.schema.keys)
            again = rawaccel.parse(text)
            self.assertTrue(again.values(index).is_close(new, 1e-6))
            self.assertEqual(again.schema, document.schema)
            self.assertEqual(list(again.data), list(document.data))   # key order
            self.assertIn("\r\n", text)                                 # line endings
            self.assertNotIn("\n\n", text.replace("\r\n", "\n"))

    def test_other_profiles_are_untouched(self) -> None:
        document = rawaccel.read(F17_MULTI)
        text = rawaccel.patch(document, 1, Four(900.0, 1.2, 0.9, 1.1))
        again = rawaccel.parse(text)
        self.assertEqual(again.values(0), document.values(0))
        self.assertEqual(again.values(2), document.values(2))
        self.assertEqual(again.values(1), Four(900.0, 1.2, 0.9, 1.1))

    def test_formatting_styles_survive(self) -> None:
        data = json.loads(raw(F17))
        for style in (dict(indent=4), dict(indent="\t"), dict(separators=(",", ":")),
                      dict(indent=2, sort_keys=True)):
            original = json.dumps(data, **style) + "\n"
            document = rawaccel.parse(original)
            text = rawaccel.patch(document, 0, Four(1011.0, 0.995, 0.98, 0.99))
            self.assertTrue(text.endswith("\n"))
            self.assertEqual(text.count("\n"), original.count("\n"))
            marker = "\x00"
            for value in ("1011.0", "0.995", "0.98", "0.99"):
                self.assertEqual(text.count(value) >= 1, True)
            self.assertEqual(len(text.replace("1011.0", marker)) < len(text), True)
        tricky = ('{"note": "\\"Output DPI\\": 5 } ] tricky", "profiles": [{"name": "a\\"b", '
                  '"Output DPI":1e3, "Y/X output DPI ratio (vertical sens multiplier)": 1,'
                  '"L/R output DPI ratio (left sens multiplier)":1.0 ,'
                  '"U/D output DPI ratio (up sens multiplier)": 1.0E+0, "x": [[1], {"y": 2}]}]}')
        patched = rawaccel.patch(rawaccel.parse(tricky), 0, Four(1011.0, 0.9, 0.8, 0.7))
        self.assertIn('"note": "\\"Output DPI\\": 5 } ] tricky"', patched)
        self.assertIn('"Output DPI":1011.0,', patched)
        self.assertIn('"x": [[1], {"y": 2}]', patched)

    def test_refuses_to_write_nonsense(self) -> None:
        document = rawaccel.read(F17)
        with self.assertRaisesRegex(RawAccelError, "not a positive number"):
            rawaccel.patch(document, 0, Four(0.0, 1, 1, 1))
        with self.assertRaisesRegex(RawAccelError, "changed safely"):
            rawaccel.patch(document, 5, Four(1.0, 1, 1, 1))


class FileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name) / "RawAccel"
        self.folder.mkdir()
        self.settings = self.folder / "settings.json"
        self.settings.write_bytes(F17.read_bytes())

    def test_write_is_atomic_and_makes_a_backup_once(self) -> None:
        document = rawaccel.read(self.settings)
        text = rawaccel.patch(document, 0, Four(1011.0, 0.995, 0.98, 0.99))
        stamp = time.mktime((2026, 11, 11, 10, 54, 39, 0, 0, -1))
        made = rawaccel.write(self.settings, text, backup=True, now=stamp)
        assert made is not None
        self.assertEqual(made.name, "settings.quadsens-backup-20261111-105439.json")
        self.assertEqual(made.read_bytes(), F17.read_bytes())          # the original
        self.assertEqual(raw(self.settings), text)
        self.assertIsNone(rawaccel.write(self.settings, document.text))
        self.assertEqual(self.settings.read_bytes(), F17.read_bytes())
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()),
                         ["settings.json", "settings.quadsens-backup-20261111-105439.json"])

    @unittest.skipIf(os.geteuid() == 0, "root can write everywhere")
    def test_read_only_folder_is_reported(self) -> None:
        self.folder.chmod(0o555)
        self.addCleanup(self.folder.chmod, 0o755)
        with self.assertRaisesRegex(RawAccelError, "could not be written"):
            rawaccel.write(self.settings, "{}", backup=True)
        self.assertEqual(self.settings.read_bytes(), F17.read_bytes())

    def test_folder_helpers(self) -> None:
        self.assertEqual(rawaccel.settings_path(self.folder), self.settings)
        self.assertIsNone(rawaccel.writer_path(self.folder))
        self.assertIsNone(rawaccel.writer_command(self.folder))
        self.assertTrue(rawaccel.looks_like_rawaccel(self.folder))
        self.assertFalse(rawaccel.looks_like_rawaccel(self.folder.parent))
        (self.folder / "writer.exe").write_text("")
        self.assertEqual(rawaccel.writer_command(self.folder),
                         [str(self.folder / "writer.exe"), str(self.settings)])

    def test_guessing_the_folder(self) -> None:
        home = Path(self._tmp.name) / "home"
        (home / "Downloads" / "RawAccel").mkdir(parents=True)
        (home / "Downloads" / "RawAccel" / "rawaccel.exe").write_text("")
        (home / "Desktop" / "RawAccel").mkdir(parents=True)      # empty: not it
        environ = {"USERPROFILE": str(home), "SystemDrive": str(home / "nodrive")}
        self.assertEqual(rawaccel.guess_folders(environ), [home / "Downloads" / "RawAccel"])
        self.assertEqual(rawaccel.guess_folders({"SystemDrive": str(home / "nodrive")}), [])

    def runner(self, returncode: int = 0, stderr: str = "", error: Exception | None = None):
        calls: list[tuple[list[str], dict[str, object]]] = []

        def run(argv: list[str], **kwargs: object) -> types.SimpleNamespace:
            calls.append((argv, kwargs))
            if error is not None:
                raise error
            return types.SimpleNamespace(returncode=returncode, stderr=stderr, stdout="")
        return run, calls

    def test_writer_outcomes(self) -> None:
        self.assertEqual(rawaccel.run_writer(self.folder).kind, rawaccel.MISSING)
        (self.folder / "writer.exe").write_text("")
        run, calls = self.runner()
        result = rawaccel.run_writer(self.folder, run)
        self.assertTrue(result.ok)
        self.assertIn("loaded the new settings", result.message)
        argv, kwargs = calls[0]
        self.assertEqual(argv, [str(self.folder / "writer.exe"), str(self.settings)])
        self.assertEqual(kwargs["cwd"], str(self.folder))
        self.assertEqual(kwargs["timeout"], rawaccel.WRITER_TIMEOUT)
        failed = rawaccel.run_writer(self.folder, self.runner(1, "Bad settings\n")[0])
        self.assertEqual(failed.kind, rawaccel.FAILED)
        self.assertIn("exit status 1", failed.message)
        self.assertIn("Bad settings", failed.message)
        self.assertIn("still uses the previous settings", failed.message)
        slow = rawaccel.run_writer(self.folder, self.runner(
            error=subprocess.TimeoutExpired("writer.exe", 20))[0])
        self.assertEqual(slow.kind, rawaccel.TIMEOUT)
        self.assertIn("message window", slow.message)
        broken = rawaccel.run_writer(self.folder, self.runner(
            error=OSError("not a valid application"))[0])
        self.assertEqual(broken.kind, rawaccel.NOT_STARTED)
        self.assertIn("not a valid application", broken.message)
        self.assertFalse(any(r.ok for r in (failed, slow, broken)))


if __name__ == "__main__":
    unittest.main()
