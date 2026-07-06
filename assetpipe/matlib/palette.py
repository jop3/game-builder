"""Palette sampling with bounded HSV jitter (spec 10.5). bpy-free by design
-- pure arithmetic, unit-tested without Blender.

Material recipes may only sample colors from the theme's palette groups
(``primary``/``secondary``/``accent``/``emissive``/``forbidden`` -- see the
scifi_industrial ``theme.json`` example, spec 7); this module is the single
place that turns a palette hex list + an rng into a concrete linear color,
so "matches the theme" (spec 10.5) is mechanically true rather than a
matter of taste: any color a recipe uses traces back to exactly one palette
entry plus a bounded jitter.
"""
from __future__ import annotations

import colorsys
import re

_HEX_RE = re.compile(r"^#([0-9A-Fa-f]{6})$")

# Spec 10.5: recipes may only sample colors from the theme palette, with
# bounded HSV jitter: +/-4 deg hue, +/-10% sat/val.
HUE_JITTER = 4.0 / 360.0
SAT_JITTER = 0.10
VAL_JITTER = 0.10


def is_hex_color(value) -> bool:
    return isinstance(value, str) and bool(_HEX_RE.match(value))


def hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    """``"#RRGGBB"`` -> linear-scale-agnostic ``(r, g, b)`` floats in
    ``[0, 1]`` (no gamma conversion -- palette hex is treated as the sRGB
    "what you'd pick in a color swatch" value; Blender-side wiring decides
    color space when it builds the actual node graph)."""
    m = _HEX_RE.match(hex_color)
    if not m:
        raise ValueError(f"not a #RRGGBB hex color: {hex_color!r}")
    value = m.group(1)
    r = int(value[0:2], 16) / 255.0
    g = int(value[2:4], 16) / 255.0
    b = int(value[4:6], 16) / 255.0
    return (r, g, b)


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    """``(r, g, b)`` floats in ``[0, 1]`` -> ``"#RRGGBB"`` (clamped)."""
    def to_byte(c: float) -> int:
        return max(0, min(255, round(c * 255.0)))

    r, g, b = rgb
    return f"#{to_byte(r):02X}{to_byte(g):02X}{to_byte(b):02X}"


def sample_palette_color(palette: dict, group: str, rng) -> tuple[float, float, float]:
    """Pick a hex color from ``palette[group]`` and apply bounded HSV
    jitter (+/-4 deg hue, +/-10% sat/val -- spec 10.5). Deterministic given
    ``rng`` (a seeded ``random.Random``): the same rng state always yields
    the same pick + jitter.

    Raises ``KeyError`` if ``group`` is not a palette group, ``ValueError``
    if the group's hex list is empty.
    """
    try:
        hex_list = palette[group]
    except KeyError:
        raise KeyError(f"palette has no group {group!r}") from None
    if not hex_list:
        raise ValueError(f"palette group {group!r} is empty")

    chosen = rng.choice(list(hex_list))
    r, g, b = hex_to_rgb(chosen)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)

    dh = rng.uniform(-HUE_JITTER, HUE_JITTER)
    ds = rng.uniform(-SAT_JITTER, SAT_JITTER)
    dv = rng.uniform(-VAL_JITTER, VAL_JITTER)

    h2 = (h + dh) % 1.0
    s2 = min(1.0, max(0.0, s + ds))
    v2 = min(1.0, max(0.0, v + dv))

    return colorsys.hsv_to_rgb(h2, s2, v2)
