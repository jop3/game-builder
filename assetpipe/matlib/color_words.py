"""Description-driven color (docs/COLOR_WAVE.md item 1): map color words in a
request description to slot-scoped material params, replacing hand-written
``material_overrides`` hex pins.

Pure Python by design (no bpy) -- unit-tested in
``assetpipe/tests/test_color_words.py`` and called orchestrator-side when the
bake payload's per-slot ``materials`` list is assembled
(:meth:`assetpipe.stages.SubprocessStages._material_recipes`).

The pipeline, mirroring the item-1 spec:

1. **Lexicon**: color words map to hex anchors (:data:`COLOR_ANCHORS`).
2. **Noun binding**: a color word only acts when it sits within two tokens
   BEFORE a known part word ("red shingled roof" colors the roof; a color
   word bound to nothing colors nothing rather than everything).
3. **Palette snap**: each anchor snaps to the NEAREST theme palette entry by
   HSV distance (:func:`snap_to_palette`) so spec 10.5's "every color traces
   to the palette" survives; only when the palette has nothing within
   tolerance does the raw anchor pass through, jittered by the same bounded
   HSV jitter palette sampling uses (spec 10.5's sample bounds).
4. **Slot-scoped emission**: the matched part maps to a material slot by
   recipe-id keywords and the hex lands in that slot's ``params`` as
   ``color1_hex`` (+ ``color2_hex`` = darkened ``color1_hex``) using the
   TEXTURE_WAVE item-6 plumbing. Slot params beat the request-wide
   ``material_overrides`` in bake.py, so derivation must NOT emit for a slot
   when the request explicitly pins any of the same keys -- explicit request
   overrides keep winning end-to-end (docs/COLOR_WAVE.md hard-won
   constraints).
"""
from __future__ import annotations

import colorsys
import random
import re

from assetpipe.matlib import palette as _palette

# Color word -> hex anchor. Anchors are deliberately "the crayon color a
# person means", not theme colors -- the palette snap does the theming.
COLOR_ANCHORS: dict[str, str] = {
    "red": "#B03030",
    "crimson": "#A81C2C",
    "scarlet": "#C02020",
    "oxblood": "#6E1414",
    "maroon": "#701820",
    "rust": "#A5502A",
    "orange": "#C86A28",
    "gold": "#C99A2C",
    "golden": "#C99A2C",
    "yellow": "#D9B33C",
    "green": "#4E7C3A",
    "olive": "#6B6B2A",
    "blue": "#3A5FA8",
    "navy": "#22304F",
    "teal": "#2A8080",
    "purple": "#6C4180",
    "violet": "#7A4FA0",
    "brown": "#7A5230",
    "tan": "#B08A5C",
    "grey": "#8C8C8C",
    "gray": "#8C8C8C",
    "silver": "#B8B8C0",
    "black": "#26221E",
    "white": "#E6E2DA",
}

# Part word -> ordered recipe-id keywords. Order encodes preference: for
# "wall" the wood/plank keywords come before "wall" itself so a plank-walled
# house binds its walls to fantasy_aged_wood, not fantasy_stone_wall (whose
# id also contains "wall"). "door" matches only a dedicated door recipe --
# binding it to the shared wall material would repaint the whole house.
PART_RECIPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "roof": ("roof", "shingle", "thatch", "slate"),
    "shingle": ("shingle", "roof"),
    "wall": ("wood", "plank", "timber", "wall"),
    "door": ("door",),
    "window": ("window", "glass", "glow"),
    "trim": ("trim", "iron"),
    "banner": ("banner", "cloth", "flag"),
    "plinth": ("stone", "cobble", "plinth"),
}

# How many tokens before a part word a color word may sit ("red shingled
# roof": 2). Anything farther is left unbound.
BIND_WINDOW = 2

# Nearest-palette-entry distance above which an anchor keeps its own hue
# instead of snapping (see _hsv_distance). Calibrated in
# test_color_words.py: red -> #8A1E1E (0.21) and grey -> secondary (0.19)
# snap; green/blue against the fantasy_medieval palette (>0.65) fall through.
SNAP_TOLERANCE = 0.5

# Palette groups eligible as snap targets. ``emissive`` holds glow colors
# (wrong domain for albedo words) and ``forbidden`` is forbidden.
SNAP_GROUPS = ("primary", "secondary", "accent")

# color2_hex = color1 darkened by this value factor (matches the retired
# manual override pair #8A1E1E / #6E1414 within a hair).
DARKEN_FACTOR = 0.8

_WORD_RE = re.compile(r"[a-z]+")


def _normalize(token: str) -> str:
    """Fold trivial plurals: 'walls' -> 'wall', 'shingles' -> 'shingle'."""
    if token.endswith("s") and token[:-1] in PART_RECIPE_KEYWORDS:
        return token[:-1]
    return token


def parse_color_bindings(description: str) -> dict[str, str]:
    """``description`` -> ``{part_word: color_word}`` for every color word
    sitting within :data:`BIND_WINDOW` tokens before a part word. Nearest
    color wins per part; each color-word occurrence binds at most once; the
    first binding for a part wins. Unbound color words are dropped."""
    tokens = [_normalize(t) for t in _WORD_RE.findall(description.lower())]
    bindings: dict[str, str] = {}
    consumed: set[int] = set()
    for i, tok in enumerate(tokens):
        if tok not in PART_RECIPE_KEYWORDS or tok in bindings:
            continue
        for j in range(i - 1, max(i - 1 - BIND_WINDOW, -1), -1):
            if j in consumed:
                continue
            if tokens[j] in COLOR_ANCHORS:
                bindings[tok] = tokens[j]
                consumed.add(j)
                break
    return bindings


def _hsv(hex_color: str) -> tuple[float, float, float]:
    return colorsys.rgb_to_hsv(*_palette.hex_to_rgb(hex_color))


def _hsv_distance(hex_a: str, hex_b: str) -> float:
    """Perceptual-ish HSV distance: circular hue difference weighted by 6x
    the mean saturation (hue is meaningless between near-greys, decisive
    between saturated colors -- 4x let "green" snap to the theme's warm
    browns), plus saturation and value differences. Unit-free; see
    SNAP_TOLERANCE."""
    ha, sa, va = _hsv(hex_a)
    hb, sb, vb = _hsv(hex_b)
    dh = abs(ha - hb)
    dh = min(dh, 1.0 - dh)
    return 6.0 * dh * (sa + sb) / 2.0 + abs(sa - sb) + abs(va - vb)


def snap_to_palette(anchor_hex: str, palette: dict,
                    tolerance: float = SNAP_TOLERANCE) -> tuple[str, bool]:
    """Snap ``anchor_hex`` to the nearest entry across :data:`SNAP_GROUPS`
    (HSV distance). Returns ``(hex, snapped)`` -- ``snapped`` False when the
    palette has nothing within ``tolerance`` (caller then jitters the raw
    anchor through the spec-10.5 sample bounds instead)."""
    best_hex, best_d = None, None
    for group in SNAP_GROUPS:
        for entry in palette.get(group, []) or []:
            if not _palette.is_hex_color(entry):
                continue
            d = _hsv_distance(anchor_hex, entry)
            if best_d is None or d < best_d:
                best_hex, best_d = entry, d
    if best_hex is not None and best_d <= tolerance:
        return best_hex, True
    return anchor_hex, False


def _jitter_hex(hex_color: str, rng: random.Random) -> str:
    """Bounded HSV jitter with the same bounds palette sampling uses
    (spec 10.5), for raw-anchor fallbacks so they still vary per seed."""
    h, s, v = _hsv(hex_color)
    h = (h + rng.uniform(-_palette.HUE_JITTER, _palette.HUE_JITTER)) % 1.0
    s = min(1.0, max(0.0, s + rng.uniform(-_palette.SAT_JITTER, _palette.SAT_JITTER)))
    v = min(1.0, max(0.0, v + rng.uniform(-_palette.VAL_JITTER, _palette.VAL_JITTER)))
    return _palette.rgb_to_hex(colorsys.hsv_to_rgb(h, s, v))


def darken_hex(hex_color: str, factor: float = DARKEN_FACTOR) -> str:
    """The derived ``color2_hex``: same hue, value scaled down."""
    h, s, v = _hsv(hex_color)
    return _palette.rgb_to_hex(colorsys.hsv_to_rgb(h, s, v * factor))


def _slot_for_part(part: str, recipe_ids: list[str]) -> int | None:
    """First slot whose recipe id contains one of the part's keywords,
    scanning keywords in preference order (see PART_RECIPE_KEYWORDS)."""
    for keyword in PART_RECIPE_KEYWORDS[part]:
        for slot, recipe_id in enumerate(recipe_ids):
            if keyword in recipe_id:
                return slot
    return None


def derive_material_colors(description: str, materials: list, palette: dict,
                           *, seed: int = 0,
                           request_overrides: dict | None = None) -> list:
    """Return a copy of the normalized per-slot ``materials`` list (entries
    are recipe-id strings or ``{"recipe", "params"}`` objects) with
    description-derived ``color1_hex``/``color2_hex`` merged into the bound
    slots' params.

    Precedence, least to most specific:

    - derived colors lose to params already pinned on the slot entry (a
      generator pin is deliberate);
    - derived colors are withheld for a slot entirely when the request's
      ``material_overrides`` pins any of the same keys, because slot params
      beat the request-wide dict in bake.py -- emitting would silently
      invert "explicit request overrides win" (COLOR_WAVE hard-won
      constraint).
    """
    if not materials:
        return materials
    bindings = parse_color_bindings(description or "")
    if not bindings:
        return list(materials)

    recipe_ids = [entry["recipe"] if isinstance(entry, dict) else entry
                  for entry in materials]
    request_overrides = request_overrides or {}
    rng = random.Random(seed)
    derived: dict[int, dict[str, str]] = {}
    # Sorted for a deterministic rng draw order (dict order already follows
    # first-binding order, but be explicit about what the seed feeds).
    for part in sorted(bindings):
        slot = _slot_for_part(part, recipe_ids)
        if slot is None or slot in derived:
            continue
        anchor = COLOR_ANCHORS[bindings[part]]
        color1, snapped = snap_to_palette(anchor, palette)
        if not snapped:
            color1 = _jitter_hex(color1, rng)
        derived[slot] = {"color1_hex": color1, "color2_hex": darken_hex(color1)}

    out: list = []
    for slot, entry in enumerate(materials):
        params = derived.get(slot)
        if params is None or any(k in request_overrides for k in params):
            out.append(entry)
            continue
        existing = dict(entry.get("params") or {}) if isinstance(entry, dict) else {}
        out.append({"recipe": recipe_ids[slot], "params": {**params, **existing}})
    return out
