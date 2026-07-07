"""Theme packs and material recipes: schema validation, the themes symlink,
and palette sampling determinism/bounds (spec 7, 10.2, 10.5, 20.1)."""
from __future__ import annotations

import colorsys
import random
from pathlib import Path

import pytest

from assetpipe.matlib.palette import (
    HUE_JITTER,
    SAT_JITTER,
    VAL_JITTER,
    hex_to_rgb,
    is_hex_color,
    rgb_to_hex,
    sample_palette_color,
)
from assetpipe.themes_io import (
    ThemeIOError,
    load_material_recipe,
    load_theme,
    validate_material_recipe,
    validate_theme,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
THEMES_ROOT = REPO_ROOT / "themes"

THEME_IDS = ["scifi_industrial", "fantasy_medieval", "lowpoly_stylized", "medieval_realistic"]


# ---------- the themes symlink ----------

def test_assetpipe_themes_symlink_resolves_to_themes_dir():
    package_themes = REPO_ROOT / "assetpipe" / "themes"
    assert package_themes.is_symlink()
    assert package_themes.resolve() == THEMES_ROOT.resolve()


# ---------- theme.json schema ----------

@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_theme_json_loads_and_validates(theme_id):
    theme = load_theme(THEMES_ROOT, theme_id)
    errors = validate_theme(theme)
    assert errors == []
    assert theme["theme_id"] == theme_id


def test_scifi_industrial_matches_spec_7_example_verbatim():
    theme = load_theme(THEMES_ROOT, "scifi_industrial")
    assert theme["palette"]["primary"] == ["#2E3A46", "#41525F"]
    assert theme["palette"]["forbidden"] == ["#8B4513"]
    assert theme["materials"] == [
        "scifi_hull_metal", "scifi_scuffed_paint", "scifi_rubber_trim",
        "scifi_emissive_strip", "scifi_deck_plate",
    ]
    assert theme["skybox_defaults"] == {
        "recipe": "space_station_interior", "sun_elevation_deg": 25}


def test_load_theme_missing_raises():
    with pytest.raises(ThemeIOError):
        load_theme(THEMES_ROOT, "does_not_exist")


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_wear_and_detail_density_ranges_within_unit_interval(theme_id):
    theme = load_theme(THEMES_ROOT, theme_id)
    for key in ("wear_range", "detail_density_range"):
        lo, hi = theme[key]
        assert 0.0 <= lo <= hi <= 1.0


def test_validate_theme_rejects_missing_palette_group():
    theme = load_theme(THEMES_ROOT, "scifi_industrial")
    theme = dict(theme)
    theme["palette"] = {k: v for k, v in theme["palette"].items() if k != "accent"}
    errors = validate_theme(theme)
    assert any("accent" in e for e in errors)


def test_validate_theme_rejects_non_hex_palette_entry():
    theme = load_theme(THEMES_ROOT, "scifi_industrial")
    theme = {**theme, "palette": {**theme["palette"], "primary": ["not-a-hex-color"]}}
    errors = validate_theme(theme)
    assert any("primary" in e for e in errors)


def test_validate_theme_rejects_empty_materials():
    theme = load_theme(THEMES_ROOT, "scifi_industrial")
    theme = {**theme, "materials": []}
    errors = validate_theme(theme)
    assert any("materials" in e for e in errors)


def test_validate_theme_rejects_out_of_range_wear_range():
    theme = load_theme(THEMES_ROOT, "scifi_industrial")
    theme = {**theme, "wear_range": [0.2, 1.5]}
    errors = validate_theme(theme)
    assert any("wear_range" in e for e in errors)


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_every_theme_declares_a_valid_anti_style_not_list(theme_id):
    # The NOT-list is optional in the schema but every shipped theme declares one
    # (spec 7); it must be a non-empty list of non-empty strings.
    theme = load_theme(THEMES_ROOT, theme_id)
    anti = theme.get("anti_style")
    assert isinstance(anti, list) and anti, f"{theme_id} has no anti_style NOT-list"
    assert all(isinstance(s, str) and s for s in anti)
    assert validate_theme(theme) == []


def test_validate_theme_rejects_malformed_anti_style():
    theme = load_theme(THEMES_ROOT, "scifi_industrial")
    assert any("anti_style" in e for e in validate_theme({**theme, "anti_style": "wood"}))
    assert any("anti_style" in e for e in validate_theme({**theme, "anti_style": ["", 3]}))


# ---------- material recipes ----------

def _all_theme_material_ids():
    for theme_id in THEME_IDS:
        theme = load_theme(THEMES_ROOT, theme_id)
        for material_id in theme["materials"]:
            yield theme_id, material_id


@pytest.mark.parametrize("theme_id,material_id", list(_all_theme_material_ids()))
def test_every_theme_material_resolves_and_satisfies_contract(theme_id, material_id):
    module = load_material_recipe(THEMES_ROOT, theme_id, material_id)
    errors = validate_material_recipe(module)
    assert errors == [], f"{theme_id}/{material_id}: {errors}"
    assert callable(module.build)


@pytest.mark.parametrize("theme_id,material_id", list(_all_theme_material_ids()))
def test_material_bakes_are_subset_of_allowed_maps(theme_id, material_id):
    module = load_material_recipe(THEMES_ROOT, theme_id, material_id)
    assert set(module.BAKES) <= {"albedo", "normal", "orm", "emissive"}
    assert isinstance(module.TILING, bool)


@pytest.mark.parametrize("material_id", [
    "lowpoly_flat_terrain", "lowpoly_flat_foliage", "lowpoly_flat_stone", "lowpoly_flat_wood",
])
def test_lowpoly_materials_are_flat_color_albedo_only(material_id):
    module = load_material_recipe(THEMES_ROOT, "lowpoly_stylized", material_id)
    assert module.FLAT_COLOR is True
    assert module.BAKES == ["albedo"]


def test_load_material_recipe_missing_raises():
    with pytest.raises(ThemeIOError):
        load_material_recipe(THEMES_ROOT, "scifi_industrial", "does_not_exist")


def test_scifi_materials_are_exactly_the_spec_10_2_five():
    theme = load_theme(THEMES_ROOT, "scifi_industrial")
    assert set(theme["materials"]) == {
        "scifi_hull_metal", "scifi_scuffed_paint", "scifi_rubber_trim",
        "scifi_emissive_strip", "scifi_deck_plate",
    }


# ---------- palette sampling (spec 10.5) ----------

def test_hex_rgb_roundtrip():
    assert hex_to_rgb("#FF6A00") == pytest.approx((1.0, 106 / 255, 0.0))
    assert rgb_to_hex((1.0, 106 / 255, 0.0)) == "#FF6A00"


def test_is_hex_color():
    assert is_hex_color("#2E3A46")
    assert not is_hex_color("2E3A46")
    assert not is_hex_color("#2E3A4")
    assert not is_hex_color("teal")
    assert not is_hex_color(123)


def test_sample_palette_color_is_deterministic_given_seeded_rng():
    palette = {"primary": ["#2E3A46", "#41525F"]}
    a = sample_palette_color(palette, "primary", random.Random(42))
    b = sample_palette_color(palette, "primary", random.Random(42))
    assert a == b


def test_sample_palette_color_differs_across_seeds_generally():
    palette = {"primary": ["#2E3A46", "#41525F"]}
    samples = {sample_palette_color(palette, "primary", random.Random(seed)) for seed in range(20)}
    assert len(samples) > 1


def test_sample_palette_color_missing_group_raises():
    with pytest.raises(KeyError):
        sample_palette_color({"primary": ["#2E3A46"]}, "accent", random.Random(0))


def test_sample_palette_color_empty_group_raises():
    with pytest.raises(ValueError):
        sample_palette_color({"primary": []}, "primary", random.Random(0))


@pytest.mark.parametrize("seed", range(30))
def test_sample_palette_color_within_jitter_bounds_of_some_entry(seed):
    palette = {"primary": ["#2E3A46", "#41525F", "#8C959D"]}
    rng = random.Random(seed)
    r, g, b = sample_palette_color(palette, "primary", rng)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)

    def hue_delta(h1, h2):
        d = abs(h1 - h2)
        return min(d, 1.0 - d)

    # Some palette entries may have similar hues to each other, so the
    # "origin" entry is whichever one satisfies *all three* jitter bounds
    # simultaneously -- not necessarily the single nearest-hue entry.
    matches = []
    for hex_color in palette["primary"]:
        hr, hg, hb = hex_to_rgb(hex_color)
        hh, hs, hv = colorsys.rgb_to_hsv(hr, hg, hb)
        dh = hue_delta(h, hh)
        ds = abs(s - hs)
        dv = abs(v - hv)
        matches.append(dh <= HUE_JITTER + 1e-9 and ds <= SAT_JITTER + 1e-9 and dv <= VAL_JITTER + 1e-9)

    assert any(matches), f"no palette entry within jitter bounds of sampled hsv={(h, s, v)}"


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_sample_palette_color_works_for_every_committed_theme(theme_id):
    theme = load_theme(THEMES_ROOT, theme_id)
    rng = random.Random(7)
    for group in ("primary", "secondary", "accent", "emissive"):
        r, g, b = sample_palette_color(theme["palette"], group, rng)
        assert 0.0 <= r <= 1.0
        assert 0.0 <= g <= 1.0
        assert 0.0 <= b <= 1.0
