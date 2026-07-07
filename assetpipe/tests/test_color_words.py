"""Description-driven color mapping (docs/COLOR_WAVE.md item 1): color word
-> part noun -> slot -> palette-snapped hex, pure Python."""
import colorsys
import json

from assetpipe.matlib import palette
from assetpipe.matlib.color_words import (
    darken_hex,
    derive_material_colors,
    parse_color_bindings,
    snap_to_palette,
)

FANTASY_PALETTE = json.loads(
    (__import__("pathlib").Path(__file__).resolve().parent.parent.parent
     / "themes" / "fantasy_medieval" / "theme.json").read_text())["palette"]

HOUSE_MATERIALS = [
    "fantasy_aged_wood",
    {"recipe": "fantasy_roof_shingles", "params": {"course_scale": 4.0}},
    "fantasy_window_glow",
    {"recipe": "fantasy_stone_wall", "params": {"cell_scale": 10.0, "moss": 0.45}},
]

HOUSE_DESC = ("A small wooden house with a red shingled roof, glowing windows "
              "and a roof dormer")


# ---------- parse_color_bindings ----------

def test_binds_color_across_one_intervening_token():
    assert parse_color_bindings(HOUSE_DESC) == {"roof": "red"}


def test_binds_adjacent_color():
    assert parse_color_bindings("a crimson banner over the door") == {"banner": "crimson"}


def test_unbound_color_words_bind_to_nothing():
    # "red" is more than BIND_WINDOW tokens from any part word.
    assert parse_color_bindings("a red and rather large squat roof") == {}
    # A color word with no part word at all.
    assert parse_color_bindings("a green thing") == {}


def test_color_word_consumed_once_and_first_part_binding_wins():
    # One "red", two roofs: only the first roof occurrence binds it.
    assert parse_color_bindings("red roof beside another roof") == {"roof": "red"}
    # Two colors, two parts: each binds its own.
    assert parse_color_bindings("blue door and a green banner") == {
        "door": "blue", "banner": "green"}


def test_plural_part_words_normalize():
    assert parse_color_bindings("white walls and grey shingles") == {
        "wall": "white", "shingle": "grey"}


def test_no_color_words_is_empty():
    assert parse_color_bindings("a small wooden house with a dormer") == {}


# ---------- snap_to_palette ----------

def test_red_snaps_to_oxblood_accent_not_gold():
    # The exact NEXT_STEPS priority-2 bug: "red shingled roof" must land on
    # the palette's #8A1E1E oxblood, never the #B08D2A gold accent.
    snapped, ok = snap_to_palette("#B03030", FANTASY_PALETTE)
    assert ok
    assert snapped == "#8A1E1E"


def test_grey_snaps_to_secondary():
    snapped, ok = snap_to_palette("#8C8C8C", FANTASY_PALETTE)
    assert ok
    assert snapped in FANTASY_PALETTE["secondary"]


def test_brown_snaps_to_primary():
    snapped, ok = snap_to_palette("#7A5230", FANTASY_PALETTE)
    assert ok
    assert snapped in FANTASY_PALETTE["primary"]


def test_gold_snaps_to_gold_accent():
    snapped, ok = snap_to_palette("#C99A2C", FANTASY_PALETTE)
    assert ok
    assert snapped == "#B08D2A"


def test_green_and_blue_fall_back_raw():
    # fantasy_medieval has no green or blue anywhere near tolerance.
    for anchor in ("#4E7C3A", "#3A5FA8"):
        snapped, ok = snap_to_palette(anchor, FANTASY_PALETTE)
        assert not ok
        assert snapped == anchor


def test_forbidden_and_emissive_groups_are_never_snap_targets():
    # A teal anchor sits exactly on the forbidden color; it must not snap
    # to it (nor to the emissive golds).
    snapped, ok = snap_to_palette("#00C2A8", FANTASY_PALETTE)
    assert not ok


# ---------- derive_material_colors ----------

def test_house_roof_gets_oxblood_without_manual_overrides():
    out = derive_material_colors(HOUSE_DESC, HOUSE_MATERIALS, FANTASY_PALETTE, seed=77)
    roof = out[1]
    assert roof["recipe"] == "fantasy_roof_shingles"
    assert roof["params"]["color1_hex"] == "#8A1E1E"
    # color2 = darkened color1, retired manual pair was #6E1414.
    c2 = roof["params"]["color2_hex"]
    h1, s1, v1 = colorsys.rgb_to_hsv(*palette.hex_to_rgb("#8A1E1E"))
    h2, s2, v2 = colorsys.rgb_to_hsv(*palette.hex_to_rgb(c2))
    assert abs(h1 - h2) < 0.01 and v2 < v1
    # generator-pinned slot params survive the merge
    assert roof["params"]["course_scale"] == 4.0
    # unbound slots untouched
    assert out[0] == "fantasy_aged_wood"
    assert out[2] == "fantasy_window_glow"
    assert out[3] == HOUSE_MATERIALS[3]


def test_wall_binds_to_wood_not_stone_wall():
    out = derive_material_colors("a house with white walls", HOUSE_MATERIALS,
                                 FANTASY_PALETTE, seed=1)
    assert isinstance(out[0], dict) and out[0]["recipe"] == "fantasy_aged_wood"
    assert "color1_hex" in out[0]["params"]
    assert out[3] == HOUSE_MATERIALS[3]  # stone slot untouched


def test_explicit_request_overrides_win_whole_slot():
    # Slot params beat request material_overrides in bake.py, so when the
    # request pins any derived key the slot's derivation is withheld
    # entirely -- explicit request overrides keep winning end-to-end.
    out = derive_material_colors(
        HOUSE_DESC, HOUSE_MATERIALS, FANTASY_PALETTE, seed=77,
        request_overrides={"color1_hex": "#123456"})
    assert out[1] == HOUSE_MATERIALS[1]


def test_generator_pinned_slot_param_beats_derived():
    materials = [{"recipe": "fantasy_roof_shingles",
                  "params": {"color1_hex": "#101010"}}]
    out = derive_material_colors("red roof", materials, FANTASY_PALETTE, seed=1)
    assert out[0]["params"]["color1_hex"] == "#101010"
    # the un-pinned derived key still lands
    assert "color2_hex" in out[0]["params"]


def test_fallback_color_is_deterministic_and_jittered_within_bounds():
    materials = ["fantasy_roof_shingles"]
    out_a = derive_material_colors("green roof", materials, FANTASY_PALETTE, seed=9)
    out_b = derive_material_colors("green roof", materials, FANTASY_PALETTE, seed=9)
    assert out_a == out_b
    c1 = out_a[0]["params"]["color1_hex"]
    h, s, v = colorsys.rgb_to_hsv(*palette.hex_to_rgb(c1))
    ah, as_, av = colorsys.rgb_to_hsv(*palette.hex_to_rgb("#4E7C3A"))
    assert abs(h - ah) <= palette.HUE_JITTER + 1e-6      # still green
    assert abs(s - as_) <= palette.SAT_JITTER + 0.01
    assert abs(v - av) <= palette.VAL_JITTER + 0.01


def test_no_bindings_returns_materials_unchanged():
    out = derive_material_colors("a plain house", HOUSE_MATERIALS, FANTASY_PALETTE)
    assert out == HOUSE_MATERIALS


def test_empty_materials_passthrough():
    assert derive_material_colors("red roof", [], FANTASY_PALETTE) == []


def test_darken_hex_matches_retired_manual_pair_closely():
    c2 = darken_hex("#8A1E1E")
    r, g, b = palette.hex_to_rgb(c2)
    rr, gr, br = palette.hex_to_rgb("#6E1414")
    assert abs(r - rr) < 0.03 and abs(g - gr) < 0.03 and abs(b - br) < 0.03
