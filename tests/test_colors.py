"""Tests of the colour arithmetic and of the contrast the derived colours reach (no Qt)."""

from __future__ import annotations

import unittest

from quadsens import colors
from quadsens.colors import COMPONENT_CONTRAST, TEXT_CONTRAST, contrast, derive_theme

# (window, base, text, accent, text on accent)
PALETTES = {
    "typical light": ("#efefef", "#ffffff", "#232629", "#3daee9", "#ffffff"),
    "typical dark": ("#2a2e32", "#1b1e20", "#e6e6e6", "#3daee9", "#ffffff"),
    "low-contrast odd": ("#808080", "#8a8a8a", "#707070", "#7f8080", "#858585"),
    "high contrast": ("#000000", "#000000", "#ffffff", "#ffff00", "#000000"),
    "sepia": ("#fdf6e3", "#eee8d5", "#657b83", "#b58900", "#fdf6e3"),
    "light, pale accent": ("#f6f6f6", "#ffffff", "#1a1a1a", "#ffe08a", "#ffffff"),
    "dark, dark accent": ("#202020", "#181818", "#dddddd", "#30343a", "#ffffff"),
    "all the same": ("#777777", "#777777", "#777777", "#777777", "#777777"),
}


class ArithmeticTest(unittest.TestCase):
    def test_parse_and_format(self) -> None:
        self.assertEqual(colors.parse("#3daee9"), (0x3D, 0xAE, 0xE9))
        self.assertEqual(colors.parse("fff"), (255, 255, 255))
        self.assertEqual(colors.to_hex((0, 128, 255)), "#0080ff")
        self.assertEqual(colors.to_hex((-5, 300, 12.6)), "#00ff0d")
        for bad in ("", "#12", "#12345", "red"):
            with self.assertRaises(ValueError):
                colors.parse(bad)

    def test_mix(self) -> None:
        self.assertEqual(colors.mix("#000000", "#ffffff", 0.0), "#000000")
        self.assertEqual(colors.mix("#000000", "#ffffff", 1.0), "#ffffff")
        self.assertEqual(colors.mix("#000000", "#ffffff", 0.5), "#808080")
        self.assertEqual(colors.mix("#102030", "#304050", 0.5), "#203040")
        self.assertEqual(colors.mix("#000000", "#ffffff", 7), "#ffffff")  # clamped

    def test_luminance_and_contrast(self) -> None:
        self.assertAlmostEqual(colors.luminance("#000000"), 0.0)
        self.assertAlmostEqual(colors.luminance("#ffffff"), 1.0)
        self.assertAlmostEqual(contrast("#000000", "#ffffff"), 21.0)
        self.assertAlmostEqual(contrast("#ffffff", "#000000"), 21.0)  # symmetric
        self.assertAlmostEqual(contrast("#123456", "#123456"), 1.0)
        # Reference value of the WCAG documentation: #767676 on white is just 4.5:1.
        self.assertAlmostEqual(contrast("#767676", "#ffffff"), 4.54, places=2)
        self.assertTrue(colors.is_dark("#2a2e32"))
        self.assertFalse(colors.is_dark("#efefef"))

    def test_ensure_contrast_is_the_fallback_rule(self) -> None:
        self.assertEqual(colors.ensure_contrast("#000000", "#ffffff", 4.5), "#000000")
        fixed = colors.ensure_contrast("#bbbbbb", "#ffffff", 4.5)
        self.assertNotEqual(fixed, "#bbbbbb")
        self.assertGreaterEqual(contrast(fixed, "#ffffff"), 4.5)
        self.assertLess(colors.luminance(fixed), colors.luminance("#bbbbbb"))  # darkened
        on_dark = colors.ensure_contrast("#444444", "#202020", 4.5)
        self.assertGreater(colors.luminance(on_dark), colors.luminance("#444444"))
        both = colors.ensure_contrast("#808080", ["#7a7a7a", "#909090"], 4.5)
        self.assertGreaterEqual(min(contrast(both, "#7a7a7a"), contrast(both, "#909090")), 4.5)
        # Changes no more than needed: a near miss stays close to the original.
        near = colors.ensure_contrast("#777777", "#ffffff", 4.5)
        self.assertLess(contrast(near, "#ffffff"), 5.5)

    def test_soften_and_hue(self) -> None:
        self.assertEqual(colors.soften("#ffffff", "#000000", ["#000000"], 4.5), "#ffffff")
        pulled = colors.soften("#444444", "#ffffff", ["#000000"], 4.5)
        self.assertGreaterEqual(contrast("#000000", pulled), 4.5)
        red = colors.parse(colors.with_hue(colors.ERROR_HUE, "#3daee9"))
        green = colors.parse(colors.with_hue(colors.SUCCESS_HUE, "#3daee9"))
        self.assertGreater(red[0], max(red[1], red[2]))
        self.assertGreater(green[1], max(green[0], green[2]))


class ThemeContrastTest(unittest.TestCase):
    def test_every_palette_reaches_aa(self) -> None:
        for name, palette in PALETTES.items():
            theme = derive_theme(*palette)
            with self.subTest(palette=name):
                self.assertEqual(theme.failures(), [])
                for label, fg, bg, need in theme.checks():
                    self.assertGreaterEqual(contrast(fg, bg), need - 1e-9, label)

    def test_what_is_checked(self) -> None:
        theme = derive_theme(*PALETTES["typical light"])
        labels = [label for label, _fg, _bg, _need in theme.checks()]
        for expected in ("text on surface", "muted text on window", "control outline on base",
                         "focus ring on surface", "text on accent", "text on control_hover",
                         "disabled text on control", "primary button against card",
                         "focus ring of primary button against card",
                         "text on warning banner", "mark on error banner",
                         "scroll handle on base"):
            self.assertIn(expected, labels)
        needs = {label: need for label, _fg, _bg, need in theme.checks()}
        self.assertEqual(needs["text on surface"], TEXT_CONTRAST)
        self.assertEqual(needs["control outline on surface"], COMPONENT_CONTRAST)
        self.assertEqual((TEXT_CONTRAST, COMPONENT_CONTRAST), (4.5, 3.0))

    def test_light_and_dark_follow_the_palette(self) -> None:
        light = derive_theme(*PALETTES["typical light"])
        dark = derive_theme(*PALETTES["typical dark"])
        self.assertFalse(light.dark)
        self.assertTrue(dark.dark)
        self.assertEqual(light.window, "#efefef")
        self.assertEqual(dark.base, "#1b1e20")
        self.assertEqual(light.text, "#232629")  # good palettes are left alone
        self.assertEqual(dark.text, "#e6e6e6")
        self.assertEqual(dark.accent, "#3daee9")  # the desktop's accent as it is
        # Cards are set off from the window in both, and stay close to it.
        for theme in (light, dark):
            self.assertNotEqual(theme.surface, theme.window)
            self.assertLess(contrast(theme.surface, theme.window), 1.25)
            self.assertNotEqual(theme.control, theme.surface)
            self.assertNotEqual(theme.control_hover, theme.control)
            self.assertNotEqual(theme.control_pressed, theme.control_hover)
            self.assertNotEqual(theme.accent_hover, theme.accent)
            self.assertNotEqual(theme.disabled, theme.text)   # distinguishable
            self.assertGreater(contrast(theme.text, theme.surface),
                               contrast(theme.disabled, theme.surface))

    def test_accent_is_adjusted_only_as_far_as_needed(self) -> None:
        light = derive_theme(*PALETTES["typical light"])
        self.assertEqual(light.on_accent, "#ffffff")   # the desktop's choice is kept
        self.assertNotEqual(light.accent, "#3daee9")   # ... by darkening the accent
        r, g, b = colors.parse(light.accent)
        self.assertTrue(b > g > r)                     # still the same blue

    def test_fallback_strengthens_a_low_contrast_palette(self) -> None:
        window, base, text, accent, accent_text = PALETTES["low-contrast odd"]
        self.assertLess(contrast(text, window), 1.5)   # unreadable as given
        theme = derive_theme(window, base, text, accent, accent_text)
        self.assertNotEqual(theme.text, text)
        self.assertGreaterEqual(contrast(theme.text, theme.window), 4.5)
        self.assertGreaterEqual(contrast(theme.outline, theme.surface), 3.0)
        self.assertGreaterEqual(contrast(theme.on_accent, theme.accent), 4.5)

    def test_lowest_ratio_is_reported(self) -> None:
        for name, palette in PALETTES.items():
            label, ratio = derive_theme(*palette).lowest()
            self.assertTrue(label)
            self.assertGreaterEqual(ratio, 3.0, name)

    def test_all_values_are_colours(self) -> None:
        theme = derive_theme(*PALETTES["typical dark"])
        for name, value in theme.as_dict().items():
            self.assertRegex(value, r"^#[0-9a-f]{6}$", name)
        self.assertNotIn("dark", theme.as_dict())


if __name__ == "__main__":
    unittest.main()
