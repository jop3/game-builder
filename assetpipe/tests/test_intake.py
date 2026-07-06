"""Asset Request intake: fail-fast, zero-iteration validation (spec 6)."""
import json
import types

import pytest

from assetpipe.contracts import Contracts
from assetpipe.generators.registry import Registry
from assetpipe.intake import (IntakeError, load_requests, validate_batch,
                              validate_request)

C = Contracts.load()


def _valid_request(**overrides):
    req = {
        "schema_version": 1,
        "asset_id": "scifi_crate_small_01",
        "category": "prop_small",
        "theme": "scifi_industrial",
        "platform_profile": "web",
        "seed": 421337,
        "description": "A small reinforced sci-fi supply crate with glowing status strip",
    }
    req.update(overrides)
    return req


def _crate_module():
    mod = types.ModuleType("crate")
    mod.CATEGORY = "prop_small"
    mod.KEYWORDS = ["crate", "box", "container", "supply"]
    mod.PARAM_SCHEMA = {
        "type": "object",
        "properties": {
            "width_m": {"type": "number", "minimum": 0.1, "maximum": 2.0, "default": 0.5},
        },
    }
    mod.generate = lambda params, rng, theme: None
    return mod


def _barrel_module():
    mod = types.ModuleType("barrel")
    mod.CATEGORY = "prop_hero"
    mod.KEYWORDS = ["barrel", "drum"]
    mod.PARAM_SCHEMA = {"type": "object", "properties": {}}
    mod.generate = lambda params, rng, theme: None
    return mod


def _registry():
    reg = Registry()
    reg.register("props/crate", _crate_module())
    reg.register("props/barrel", _barrel_module())
    return reg


@pytest.fixture
def themes_root(tmp_path):
    theme_dir = tmp_path / "scifi_industrial"
    theme_dir.mkdir()
    (theme_dir / "theme.json").write_text(json.dumps({"theme_id": "scifi_industrial"}))
    return tmp_path


# ---------- happy path ----------

def test_valid_request_passes_with_no_registry_or_themes_root():
    normalized = validate_request(_valid_request(), C)
    assert normalized["asset_id"] == "scifi_crate_small_01"


def test_valid_request_passes_with_theme_and_explicit_generator(themes_root):
    reg = _registry()
    req = _valid_request(generator="props/crate")
    normalized = validate_request(req, C, themes_root=themes_root, registry=reg)
    assert normalized["generator"] == "props/crate"


def test_generator_resolved_from_description_when_omitted(themes_root):
    reg = _registry()
    req = _valid_request()  # description mentions "crate" and "supply"
    normalized = validate_request(req, C, themes_root=themes_root, registry=reg)
    assert normalized["generator"] == "props/crate"


def test_tiling_category_resolves_its_bake_target_generator(themes_root):
    """tiling_texture_set resolves a generator like mesh categories do: the
    recipe builds the spec-10.3 unit-plane bake target."""
    reg = _registry()
    tiling_mod = types.ModuleType("surface")
    tiling_mod.CATEGORY = "tiling_texture_set"
    tiling_mod.KEYWORDS = ["tiling", "seamless", "plating"]
    tiling_mod.PARAM_SCHEMA = {"type": "object", "properties": {}}
    tiling_mod.generate = lambda params, rng, theme: None
    reg.register("tiling/surface", tiling_mod)
    req = _valid_request(
        category="tiling_texture_set", description="Seamless scuffed metal plating texture")
    normalized = validate_request(req, C, themes_root=themes_root, registry=reg)
    assert normalized.get("generator") == "tiling/surface"


def test_skybox_and_background_rejected_as_unimplemented(themes_root):
    """No stage-B branch exists yet; intake fails fast (spec 6: zero
    iterations consumed) instead of letting the loop crash mid-run."""
    for category in ("skybox", "background_2d"):
        req = _valid_request(category=category, description="a nice sky")
        with pytest.raises(IntakeError) as excinfo:
            validate_request(req, C, themes_root=themes_root, registry=_registry())
        assert "NOT_IMPLEMENTED" in str(excinfo.value)


# ---------- individual error classes ----------

def test_bad_schema_is_reported():
    req = _valid_request(category="not_a_real_category")
    with pytest.raises(IntakeError) as excinfo:
        validate_request(req, C)
    assert any("schema" in e for e in excinfo.value.errors)


def test_unknown_platform_profile_is_reported():
    req = _valid_request(platform_profile="playstation2")
    with pytest.raises(IntakeError) as excinfo:
        validate_request(req, C)
    # schema also rejects the bad enum value, and the profile lookup itself
    # is guarded by isinstance(..., str) so no crash either way
    assert any("platform_profile" in e or "schema" in e for e in excinfo.value.errors)


def test_missing_theme_is_reported(tmp_path):
    req = _valid_request(theme="nonexistent_theme")
    with pytest.raises(IntakeError) as excinfo:
        validate_request(req, C, themes_root=tmp_path)
    assert any("nonexistent_theme" in e for e in excinfo.value.errors)


def test_wrong_category_generator_is_reported(themes_root):
    reg = _registry()
    # props/barrel is CATEGORY prop_hero, but request is prop_small
    req = _valid_request(generator="props/barrel")
    with pytest.raises(IntakeError) as excinfo:
        validate_request(req, C, themes_root=themes_root, registry=reg)
    assert any("props/barrel" in e for e in excinfo.value.errors)


def test_unknown_generator_id_is_reported(themes_root):
    reg = _registry()
    req = _valid_request(generator="props/does_not_exist")
    with pytest.raises(IntakeError) as excinfo:
        validate_request(req, C, themes_root=themes_root, registry=reg)
    assert any("generator" in e for e in excinfo.value.errors)


def test_no_generator_match_is_reported(themes_root):
    reg = _registry()
    req = _valid_request(description="A glowing mystical crystal orb of unknown origin")
    with pytest.raises(IntakeError) as excinfo:
        validate_request(req, C, themes_root=themes_root, registry=reg)
    assert any("NO_GENERATOR" in e for e in excinfo.value.errors)


def test_loosening_max_triangles_is_rejected():
    profile_max = C.profile("web")["triangles"]["prop_small"]["max"]
    req = _valid_request(budget_overrides={"max_triangles": profile_max + 1})
    with pytest.raises(IntakeError) as excinfo:
        validate_request(req, C)
    assert any("max_triangles" in e for e in excinfo.value.errors)


def test_tightening_max_triangles_is_accepted():
    profile_max = C.profile("web")["triangles"]["prop_small"]["max"]
    req = _valid_request(budget_overrides={"max_triangles": profile_max - 1})
    normalized = validate_request(req, C)
    assert normalized["budget_overrides"]["max_triangles"] == profile_max - 1


def test_loosening_max_file_bytes_is_rejected():
    profile_max = C.profile("web")["file_bytes"]["prop_small"]
    req = _valid_request(budget_overrides={"max_file_bytes": profile_max + 1})
    with pytest.raises(IntakeError) as excinfo:
        validate_request(req, C)
    assert any("max_file_bytes" in e for e in excinfo.value.errors)


def test_loosening_max_texture_px_beyond_tightest_map_is_rejected():
    textures = C.profile("web")["textures"]["prop_small"]
    tightest = min(textures.values())
    req = _valid_request(budget_overrides={"max_texture_px": tightest + 1})
    with pytest.raises(IntakeError) as excinfo:
        validate_request(req, C)
    assert any("max_texture_px" in e for e in excinfo.value.errors)


def test_tightening_max_texture_px_to_tightest_map_is_accepted():
    textures = C.profile("web")["textures"]["prop_small"]
    tightest = min(textures.values())
    req = _valid_request(budget_overrides={"max_texture_px": tightest})
    normalized = validate_request(req, C)
    assert normalized["budget_overrides"]["max_texture_px"] == tightest


def test_budget_override_key_not_applicable_to_category_is_rejected():
    # tiling_texture_set has no triangle budget at all to tighten
    req = _valid_request(
        category="tiling_texture_set", description="Seamless scuffed metal plating",
        budget_overrides={"max_triangles": 100})
    with pytest.raises(IntakeError) as excinfo:
        validate_request(req, C)
    assert any("max_triangles" in e for e in excinfo.value.errors)


# ---------- aggregation ----------

def test_multiple_errors_are_all_reported_together(tmp_path):
    profile_max = C.profile("web")["triangles"]["prop_small"]["max"]
    req = _valid_request(
        theme="nonexistent_theme",
        platform_profile="playstation2",
        budget_overrides={"max_triangles": profile_max + 1},
    )
    with pytest.raises(IntakeError) as excinfo:
        validate_request(req, C, themes_root=tmp_path)
    joined = "; ".join(excinfo.value.errors)
    assert "nonexistent_theme" in joined
    assert len(excinfo.value.errors) >= 2
    assert str(excinfo.value) == "; ".join(excinfo.value.errors)


# ---------- batch ----------

def test_validate_batch_separates_accepted_and_rejected(themes_root):
    reg = _registry()
    good = _valid_request(asset_id="good_one")
    bad = _valid_request(asset_id="bad_one", platform_profile="playstation2")
    accepted, rejected = validate_batch([good, bad], C, themes_root=themes_root, registry=reg)
    assert [r["asset_id"] for r in accepted] == ["good_one"]
    assert "bad_one" in rejected
    assert rejected["bad_one"]  # non-empty error list


def test_validate_batch_rejects_duplicate_asset_ids(themes_root):
    reg = _registry()
    req = _valid_request(asset_id="dup_one")
    accepted, rejected = validate_batch(
        [req, dict(req)], C, themes_root=themes_root, registry=reg)
    assert len(accepted) == 1
    assert "dup_one" in rejected
    assert any("duplicate" in e for e in rejected["dup_one"])


def test_validate_batch_all_valid_none_rejected(themes_root):
    reg = _registry()
    reqs = [_valid_request(asset_id=f"asset_{i}") for i in range(3)]
    accepted, rejected = validate_batch(reqs, C, themes_root=themes_root, registry=reg)
    assert len(accepted) == 3
    assert rejected == {}


# ---------- load_requests ----------

def test_load_requests_single_object(tmp_path):
    path = tmp_path / "one.json"
    path.write_text(json.dumps(_valid_request()))
    reqs = load_requests(path)
    assert isinstance(reqs, list) and len(reqs) == 1
    assert reqs[0]["asset_id"] == "scifi_crate_small_01"


def test_load_requests_array(tmp_path):
    path = tmp_path / "many.json"
    path.write_text(json.dumps([
        _valid_request(asset_id="a_one"), _valid_request(asset_id="a_two"),
    ]))
    reqs = load_requests(path)
    assert [r["asset_id"] for r in reqs] == ["a_one", "a_two"]
