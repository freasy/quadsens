"""Tests of the opt-in startup entry for RawAccel's writer (no Qt).

The registry is a stand-in. Platform-independent; also run on Windows in CI.
"""

from __future__ import annotations

import unittest

from quadsens import win_autostart


class MemoryRegistry:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.fail = False

    def get(self, name: str) -> str | None:
        if self.fail:
            raise PermissionError(13, "Access is denied")
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        if self.fail:
            raise PermissionError(13, "Access is denied")
        self.values[name] = value

    def delete(self, name: str) -> None:
        if self.fail:
            raise PermissionError(13, "Access is denied")
        self.values.pop(name, None)


class AutostartTest(unittest.TestCase):
    FOLDER = "C:\\Users\\some one\\AppData\\Local\\Programs\\RawAccel"

    def test_command_quotes_both_paths(self) -> None:
        self.assertEqual(
            win_autostart.command(self.FOLDER),
            '"%s\\writer.exe" "%s\\settings.json"' % (self.FOLDER, self.FOLDER))
        self.assertEqual(win_autostart.command(self.FOLDER + "\\"),
                         win_autostart.command(self.FOLDER))
        self.assertEqual(win_autostart.entry_folder(win_autostart.command(self.FOLDER)),
                         self.FOLDER)
        for bad in ('C:\\a"b', "", "C:\\a\nb"):
            with self.assertRaises(win_autostart.AutostartError):
                win_autostart.command(bad)
        self.assertEqual(win_autostart.entry_folder("notepad.exe"), "")
        self.assertIn("CurrentVersion\\Run", win_autostart.RUN_KEY)
        self.assertTrue(win_autostart.WHERE.startswith("HKEY_CURRENT_USER\\"))

    def test_off_by_default_on_and_off_again(self) -> None:
        registry = MemoryRegistry()
        self.assertEqual(win_autostart.state(self.FOLDER, registry).kind, win_autostart.OFF)
        self.assertEqual(registry.values, {})                      # looking writes nothing
        on = win_autostart.enable(self.FOLDER, registry)
        self.assertEqual((on.kind, on.folder), (win_autostart.ON, self.FOLDER))
        self.assertEqual(registry.values,
                         {win_autostart.VALUE_NAME: win_autostart.command(self.FOLDER)})
        self.assertIn("At every sign-in", win_autostart.describe(on))
        self.assertEqual(win_autostart.disable(registry).kind, win_autostart.OFF)
        self.assertEqual(registry.values, {})
        self.assertEqual(win_autostart.describe(win_autostart.State(win_autostart.OFF)),
                         "Values are lost when Windows restarts.")
        win_autostart.disable(registry)                            # twice does no harm

    def test_an_entry_for_another_folder_is_seen_and_follows_a_change(self) -> None:
        registry = MemoryRegistry()
        win_autostart.enable("D:\\Old\\RawAccel", registry)
        other = win_autostart.state(self.FOLDER, registry)
        self.assertEqual((other.kind, other.folder), (win_autostart.OTHER, "D:\\Old\\RawAccel"))
        self.assertTrue(other.exists)
        self.assertIn("D:\\Old\\RawAccel", win_autostart.describe(other))
        moved = win_autostart.follow_folder(self.FOLDER, registry)
        self.assertEqual((moved.kind, moved.folder), (win_autostart.ON, self.FOLDER))
        # No entry stays no entry; no folder removes the entry.
        empty = MemoryRegistry()
        self.assertEqual(win_autostart.follow_folder(self.FOLDER, empty).kind,
                         win_autostart.OFF)
        self.assertEqual(empty.values, {})
        self.assertEqual(win_autostart.follow_folder(None, registry).kind, win_autostart.OFF)
        self.assertEqual(registry.values, {})
        # Case and spaces around do not make it another entry.
        registry.values[win_autostart.VALUE_NAME] = \
            " " + win_autostart.command(self.FOLDER).upper() + " "
        self.assertEqual(win_autostart.state(self.FOLDER, registry).kind, win_autostart.ON)

    def test_registry_failures_are_reported(self) -> None:
        registry = MemoryRegistry()
        registry.fail = True
        for call in (lambda: win_autostart.enable(self.FOLDER, registry),
                     lambda: win_autostart.disable(registry),
                     lambda: win_autostart.state(self.FOLDER, registry)):
            with self.assertRaises(win_autostart.AutostartError) as caught:
                call()
            self.assertIn("Access is denied", str(caught.exception))
        self.assertIsNone(win_autostart.NoRegistry().get("x"))
        with self.assertRaises(OSError):
            win_autostart.NoRegistry().set("x", "y")

    def test_only_the_users_own_run_key_is_named(self) -> None:
        import inspect
        source = inspect.getsource(win_autostart)
        self.assertIn("HKEY_CURRENT_USER", source)
        self.assertNotIn("HKEY_LOCAL_MACHINE", source)
        self.assertNotIn("RunOnce", source)


if __name__ == "__main__":
    unittest.main()
