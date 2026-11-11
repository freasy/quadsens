"""Colour arithmetic and the derivation of the application's colour set (no Qt).

Everything is computed from five colours of the desktop palette: window, base,
text, accent and the text colour used on the accent. Surfaces, borders and
muted tones are mixtures of those, so light and dark themes and the desktop's
accent colour carry over without any table of fixed colours.

Contrast follows WCAG 2.1: text needs 4.5:1 against what it is drawn on, the
boundary of an interactive component 3:1 against what surrounds it. Where a
mixture falls short (a low-contrast palette), the colour is pushed towards
black or white until it passes; that is the fallback rule.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, fields

TEXT_CONTRAST = 4.5        # WCAG 2.1 SC 1.4.3, normal text
COMPONENT_CONTRAST = 3.0   # WCAG 2.1 SC 1.4.11, component boundaries and focus rings

RGB = tuple[int, int, int]

# Hues of the semantic tints (degrees). Saturation and lightness come from the
# palette, only the hue is fixed: green, amber, red.
SUCCESS_HUE, WARNING_HUE, ERROR_HUE = 140.0, 38.0, 4.0


def parse(color: str) -> RGB:
    """"#rrggbb" (or "#rgb") to a tuple of three ints."""
    text = color.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"not a colour: {color!r}")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def to_hex(rgb: RGB) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def mix(a: str, b: str, share: float) -> str:
    """``a`` with ``share`` (0..1) of ``b`` mixed in."""
    share = max(0.0, min(1.0, share))
    ca, cb = parse(a), parse(b)
    return to_hex(tuple(x + (y - x) * share for x, y in zip(ca, cb)))  # type: ignore[arg-type]


def luminance(color: str) -> float:
    """Relative luminance as defined by WCAG 2.1 (0 = black, 1 = white)."""
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = parse(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two colours, between 1 and 21."""
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def is_dark(color: str) -> bool:
    """Whether white text is the better choice on ``color``."""
    return contrast(color, "#ffffff") >= contrast(color, "#000000")


def ensure_contrast(color: str, backgrounds: str | list[str], target: float) -> str:
    """``color`` moved towards black or white until it reaches ``target``.

    The contrast is taken against every background given. The colour is left
    alone if it passes already. The direction is the one that can reach the
    target; if neither can, the better extreme is returned.
    """
    grounds = [backgrounds] if isinstance(backgrounds, str) else list(backgrounds)

    def worst(candidate: str) -> float:
        return min(contrast(candidate, ground) for ground in grounds)

    if worst(color) >= target:
        return color
    best = color
    for extreme in sorted(("#000000", "#ffffff"), key=worst, reverse=True):
        for step in range(1, 21):
            candidate = mix(color, extreme, step / 20)
            if worst(candidate) >= target:
                return candidate
            if worst(candidate) > worst(best):
                best = candidate
    return best


def soften(background: str, toward: str, foregrounds: list[str], target: float) -> str:
    """``background`` pulled back to ``toward`` until the foregrounds stay readable."""
    for step in range(0, 6):
        candidate = mix(background, toward, step / 5)
        if all(contrast(fg, candidate) >= target for fg in foregrounds):
            return candidate
    return toward


def with_hue(hue: float, like: str) -> str:
    """A colour of the given hue with the saturation and lightness class of ``like``."""
    r, g, b = (c / 255 for c in parse(like))
    _h, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    lightness = min(0.62, max(0.38, lightness))
    saturation = min(0.85, max(0.55, saturation))
    return to_hex(tuple(c * 255 for c in colorsys.hls_to_rgb(hue / 360, lightness, saturation)))  # type: ignore[arg-type]


@dataclass(frozen=True)
class Theme:
    """The colour set of the application, as "#rrggbb" strings."""

    dark: bool
    window: str          # background of the window
    surface: str         # background of a card
    base: str            # background of inputs and the log
    text: str            # body text
    muted: str           # captions and secondary text
    disabled: str        # text of disabled controls
    border: str          # outline of a card (decorative)
    outline: str         # boundary of buttons and inputs (3:1 against its surroundings)
    control: str         # fill of a secondary button
    control_hover: str
    control_pressed: str
    accent: str          # primary button, focus ring, arrows
    accent_hover: str
    accent_pressed: str
    on_accent: str       # text on the accent
    focus: str           # focus ring on ordinary controls
    focus_on_accent: str  # focus ring around an accent-filled button
    handle: str          # scroll bar handle
    info_bg: str
    info_mark: str
    success_bg: str
    success_mark: str
    warning_bg: str
    warning_mark: str
    error_bg: str
    error_mark: str

    def as_dict(self) -> dict[str, str]:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "dark"}

    def checks(self) -> list[tuple[str, str, str, float]]:
        """Every (name, foreground, background, required ratio) the look relies on."""
        grounds = {"window": self.window, "surface": self.surface, "base": self.base}
        result: list[tuple[str, str, str, float]] = []
        for name, ground in grounds.items():
            result.append((f"text on {name}", self.text, ground, TEXT_CONTRAST))
            result.append((f"muted text on {name}", self.muted, ground, TEXT_CONTRAST))
            result.append((f"disabled text on {name}", self.disabled, ground,
                           COMPONENT_CONTRAST))
            result.append((f"control outline on {name}", self.outline, ground,
                           COMPONENT_CONTRAST))
            result.append((f"focus ring on {name}", self.focus, ground, COMPONENT_CONTRAST))
            result.append((f"scroll handle on {name}", self.handle, ground,
                           COMPONENT_CONTRAST))
        for name in ("control", "control_hover", "control_pressed"):
            result.append((f"text on {name}", self.text, getattr(self, name), TEXT_CONTRAST))
        result.append(("disabled text on control", self.disabled, self.control,
                       COMPONENT_CONTRAST))
        for name in ("accent", "accent_hover", "accent_pressed"):
            result.append((f"text on {name}", self.on_accent, getattr(self, name),
                           TEXT_CONTRAST))
        result.append(("primary button against card", self.accent, self.surface,
                       COMPONENT_CONTRAST))
        result.append(("primary button against window", self.accent, self.window,
                       COMPONENT_CONTRAST))
        result.append(("focus ring of primary button against card", self.focus_on_accent,
                       self.surface, COMPONENT_CONTRAST))
        result.append(("focus ring of primary button against window", self.focus_on_accent,
                       self.window, COMPONENT_CONTRAST))
        for kind in ("info", "success", "warning", "error"):
            ground = getattr(self, f"{kind}_bg")
            result.append((f"text on {kind} banner", self.text, ground, TEXT_CONTRAST))
            result.append((f"mark on {kind} banner", getattr(self, f"{kind}_mark"), ground,
                           COMPONENT_CONTRAST))
        return result

    def failures(self) -> list[str]:
        """Descriptions of the checks that do not reach their ratio (empty = all fine)."""
        return [f"{name}: {contrast(fg, bg):.2f} < {need}"
                for name, fg, bg, need in self.checks() if contrast(fg, bg) < need - 1e-9]

    def lowest(self) -> tuple[str, float]:
        """The check with the smallest margin, as (name, ratio)."""
        name, fg, bg, _need = min(self.checks(), key=lambda c: contrast(c[1], c[2]) / c[3])
        return name, contrast(fg, bg)


def derive_theme(window: str, base: str, text: str, accent: str,
                 accent_text: str = "#ffffff") -> Theme:
    """Work out the whole colour set from five palette colours."""
    window, base, text, accent, accent_text = (
        to_hex(parse(c)) for c in (window, base, text, accent, accent_text))
    dark = is_dark(window)

    # Cards sit slightly above the window: towards the base colour where that is
    # the lighter one, otherwise a little towards the text colour.
    if not dark and luminance(base) > luminance(window):
        surface = mix(window, base, 0.6)
    else:
        surface = mix(window, text, 0.05)
    grounds = [window, surface, base]

    # Fallback rule, text first: everything else is mixed from the final text colour.
    text = ensure_contrast(text, grounds, TEXT_CONTRAST)
    muted = ensure_contrast(mix(text, surface, 0.32), grounds, TEXT_CONTRAST)
    border = mix(surface, text, 0.16)
    outline = ensure_contrast(mix(surface, text, 0.42), grounds, COMPONENT_CONTRAST)
    handle = ensure_contrast(mix(surface, text, 0.38), grounds, COMPONENT_CONTRAST)

    control = soften(mix(surface, text, 0.07), surface, [text], TEXT_CONTRAST)
    control_hover = soften(mix(surface, text, 0.13), surface, [text], TEXT_CONTRAST)
    control_pressed = soften(mix(surface, text, 0.2), surface, [text], TEXT_CONTRAST)

    disabled = ensure_contrast(mix(text, surface, 0.55), [*grounds, control],
                               COMPONENT_CONTRAST)

    # The accent has to stand out from the card (it fills the primary button)
    # and carry readable text. Keep the desktop's text-on-accent colour if the
    # accent can be adjusted to suit it; otherwise take black or white.
    accent = ensure_contrast(accent, [window, surface], COMPONENT_CONTRAST)
    adjusted = ensure_contrast(accent, accent_text, TEXT_CONTRAST)
    if (contrast(adjusted, accent_text) >= TEXT_CONTRAST
            and min(contrast(adjusted, window), contrast(adjusted, surface))
            >= COMPONENT_CONTRAST):
        accent, on_accent = adjusted, accent_text
    else:
        on_accent = max(("#000000", "#ffffff"), key=lambda c: contrast(c, accent))
    # Hover and pressed move the accent away from its text, so contrast only grows.
    away = "#ffffff" if luminance(on_accent) < luminance(accent) else "#000000"
    accent_hover = soften(mix(accent, away, 0.12), accent, [on_accent], TEXT_CONTRAST)
    accent_pressed = soften(mix(accent, away, 0.22), accent, [on_accent], TEXT_CONTRAST)
    focus = ensure_contrast(accent, grounds, COMPONENT_CONTRAST)
    focus_on_accent = ensure_contrast(text, [window, surface], COMPONENT_CONTRAST)

    def tint(color: str) -> tuple[str, str]:
        background = soften(mix(surface, color, 0.16), surface, [text], TEXT_CONTRAST)
        mark = ensure_contrast(mix(color, text, 0.35), background, COMPONENT_CONTRAST)
        return background, mark

    info_bg, info_mark = tint(accent)
    success_bg, success_mark = tint(with_hue(SUCCESS_HUE, accent))
    warning_bg, warning_mark = tint(with_hue(WARNING_HUE, accent))
    error_bg, error_mark = tint(with_hue(ERROR_HUE, accent))

    return Theme(
        dark=dark, window=window, surface=surface, base=base, text=text, muted=muted,
        disabled=disabled, border=border, outline=outline, control=control,
        control_hover=control_hover, control_pressed=control_pressed, accent=accent,
        accent_hover=accent_hover, accent_pressed=accent_pressed, on_accent=on_accent,
        focus=focus, focus_on_accent=focus_on_accent, handle=handle,
        info_bg=info_bg, info_mark=info_mark, success_bg=success_bg,
        success_mark=success_mark, warning_bg=warning_bg, warning_mark=warning_mark,
        error_bg=error_bg, error_mark=error_mark)
