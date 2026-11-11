"""Tests of the Linux side of the "is the driver applying it" logic (no Qt)."""

from __future__ import annotations

import time
import unittest

from quadsens import active
from quadsens.active_linux import linux_indicator
from quadsens.driver import DriverState, DriverStatus
from quadsens.model import Scalars

VALUES = Scalars(0.9855, 1.0012, 0.9953, 1.0016)
NOW = time.time()


class LinuxIndicatorTest(unittest.TestCase):
    def test_active_names_the_values_and_the_time(self) -> None:
        shown = linux_indicator(DriverState(DriverStatus.OK, VALUES), VALUES, NOW)
        self.assertEqual((shown.kind, shown.mark, shown.headline, shown.tint),
                         (active.ACTIVE, "✓", "Active in driver", "success"))
        self.assertEqual(shown.detail, "Applied %s: Right 0.9855, Left 1.0012, Down 0.9953, "
                                       "Up 1.0016" % active.clock(NOW))
        self.assertEqual(shown.short(), "✓ Active in driver")

    def test_neutral(self) -> None:
        shown = linux_indicator(DriverState(DriverStatus.OK, Scalars.unity()), VALUES, NOW)
        self.assertEqual((shown.kind, shown.mark, shown.tint), (active.NEUTRAL, "○", "info"))
        self.assertEqual(shown.headline, "Neutral: all four driver values are 1.0")
        self.assertIn("QuadSens last wrote Right 0.9855", shown.detail)
        shown = linux_indicator(DriverState(DriverStatus.OK, Scalars.unity()))
        self.assertEqual(shown.detail, "The module is loaded and scales nothing.")

    def test_changed_elsewhere_shows_both(self) -> None:
        other = Scalars(1.1, 1.0, 1.0, 1.0)
        shown = linux_indicator(DriverState(DriverStatus.OK, other), VALUES, NOW)
        self.assertEqual((shown.kind, shown.mark, shown.tint),
                         (active.DIFFERENT, "≠", "warning"))
        self.assertEqual(shown.headline, "Active, but different from QuadSens")
        self.assertIn("Driver: Right 1.1000", shown.detail)
        self.assertIn("QuadSens last wrote Right 0.9855", shown.detail)
        shown = linux_indicator(DriverState(DriverStatus.OK, other))
        self.assertEqual(shown.headline, "Active, but not set by QuadSens")

    def test_not_active_says_why(self) -> None:
        cases = {DriverStatus.NOT_LOADED: "module is not loaded",
                 DriverStatus.UNSUPPORTED: "no directional scaling"}
        for status, words in cases.items():
            shown = linux_indicator(DriverState(status), VALUES, NOW)
            self.assertEqual((shown.kind, shown.mark), (active.INACTIVE, "✕"))
            self.assertTrue(shown.headline.startswith("Not active: "))
            self.assertIn(words, shown.headline)

    def test_unreadable_is_not_called_active(self) -> None:
        shown = linux_indicator(DriverState(DriverStatus.NOT_READABLE), VALUES, NOW)
        self.assertEqual((shown.kind, shown.mark), (active.UNVERIFIED, "?"))
        self.assertIn("not readable for you", shown.headline)
        self.assertIn("QuadSens last wrote", shown.detail)
        shown = linux_indicator(DriverState(DriverStatus.ERROR, message="DirScaleXPos: bad"))
        self.assertEqual(shown.kind, active.UNVERIFIED)
        self.assertIn("DirScaleXPos: bad", shown.headline)


if __name__ == "__main__":
    unittest.main()
