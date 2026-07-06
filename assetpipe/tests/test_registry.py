"""Generator registry: registration validation, discovery, and deterministic
keyword resolution (spec 9.2)."""
import sys
import types

import pytest

from assetpipe.contracts import MESH_CATEGORIES
from assetpipe.generators.registry import Registry, RegistryError


def _make_module(name: str, *, category="prop_small", keywords=None,
                  param_schema=None, generate=None, extra_attrs=None):
    mod = types.ModuleType(name)
    mod.CATEGORY = category
    mod.KEYWORDS = keywords if keywords is not None else ["thing"]
    mod.PARAM_SCHEMA = param_schema if param_schema is not None else {
        "type": "object",
        "properties": {
            "width_m": {"type": "number", "minimum": 0.1, "maximum": 2.0, "default": 0.5},
        },
        "additionalProperties": False,
    }
    mod.generate = generate if generate is not None else (lambda params, rng, theme: None)
    for k, v in (extra_attrs or {}).items():
        setattr(mod, k, v)
    return mod


# ---------- register() ----------

def test_register_accepts_well_formed_recipe():
    reg = Registry()
    mod = _make_module("crate")
    reg.register("props/crate", mod)
    assert reg.get("props/crate") is mod
    assert "props/crate" in reg
    assert reg.ids() == ["props/crate"]


@pytest.mark.parametrize("attr", ["PARAM_SCHEMA", "CATEGORY", "KEYWORDS", "generate"])
def test_register_rejects_missing_attribute(attr):
    mod = _make_module("crate")
    delattr(mod, attr)
    reg = Registry()
    with pytest.raises(RegistryError):
        reg.register("props/crate", mod)


def test_register_rejects_numeric_param_missing_minimum_or_maximum():
    mod = _make_module("crate", param_schema={
        "type": "object",
        "properties": {
            "width_m": {"type": "number", "maximum": 2.0, "default": 0.5},
        },
    })
    reg = Registry()
    with pytest.raises(RegistryError, match="minimum"):
        reg.register("props/crate", mod)


def test_register_rejects_numeric_param_missing_default():
    mod = _make_module("crate", param_schema={
        "type": "object",
        "properties": {
            "width_m": {"type": "number", "minimum": 0.1, "maximum": 2.0},
        },
    })
    reg = Registry()
    with pytest.raises(RegistryError, match="default"):
        reg.register("props/crate", mod)


def test_register_rejects_bad_category():
    mod = _make_module("crate", category="spaceship")
    reg = Registry()
    with pytest.raises(RegistryError, match="spaceship"):
        reg.register("props/crate", mod)


def test_register_rejects_non_object_schema():
    mod = _make_module("crate", param_schema={"type": "array"})
    reg = Registry()
    with pytest.raises(RegistryError):
        reg.register("props/crate", mod)


def test_register_rejects_keywords_not_a_list_of_str():
    mod = _make_module("crate", keywords="crate")
    reg = Registry()
    with pytest.raises(RegistryError, match="KEYWORDS"):
        reg.register("props/crate", mod)


def test_get_unknown_recipe_raises():
    reg = Registry()
    with pytest.raises(RegistryError):
        reg.get("props/nope")


def test_all_mesh_categories_are_valid_for_registration():
    # sanity: every category constant registry validates against is accepted
    for cat in MESH_CATEGORIES:
        reg = Registry()
        reg.register("x/y", _make_module("y", category=cat))
        assert reg.get("x/y").CATEGORY == cat


# ---------- resolve() ----------

def _registry_with_crate_and_barrel():
    reg = Registry()
    reg.register("props/crate", _make_module(
        "crate", category="prop_small", keywords=["crate", "box", "container", "supply"]))
    reg.register("props/barrel", _make_module(
        "barrel", category="prop_small", keywords=["barrel", "drum", "container"]))
    return reg


def test_resolve_picks_highest_keyword_overlap():
    reg = _registry_with_crate_and_barrel()
    assert reg.resolve("prop_small", "A small reinforced supply crate with a lid") == "props/crate"
    assert reg.resolve("prop_small", "A rusty steel barrel drum") == "props/barrel"


def test_resolve_no_keyword_match_returns_none():
    reg = _registry_with_crate_and_barrel()
    assert reg.resolve("prop_small", "a glowing crystal orb") is None


def test_resolve_no_recipes_for_category_returns_none():
    reg = _registry_with_crate_and_barrel()
    assert reg.resolve("character_primary", "a knight in armor") is None


def test_resolve_empty_registry_returns_none():
    reg = Registry()
    assert reg.resolve("prop_small", "a crate") is None


def test_resolve_tie_breaks_lexicographically():
    reg = Registry()
    # Both score 1 on the same token; "props/alpha" < "props/beta" lexicographically.
    reg.register("props/beta", _make_module("beta", keywords=["shiny"]))
    reg.register("props/alpha", _make_module("alpha", keywords=["shiny"]))
    assert reg.resolve("prop_small", "a shiny thing") == "props/alpha"


def test_resolve_is_case_insensitive_and_tokenizes_on_punctuation():
    reg = Registry()
    reg.register("props/crate", _make_module("crate", keywords=["Crate"]))
    assert reg.resolve("prop_small", "CRATE-like object, crate!") == "props/crate"


# ---------- discover() ----------

def test_discover_empty_package_returns_empty_registry(tmp_path, monkeypatch):
    pkg_name = "assetpipe_test_empty_generators"
    pkg_dir = tmp_path / pkg_name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        reg = Registry.discover(package=pkg_name)
        assert len(reg) == 0
        assert reg.ids() == []
    finally:
        sys.modules.pop(pkg_name, None)


def test_discover_finds_recipes_in_subpackages_and_skips_registry_module(tmp_path, monkeypatch):
    pkg_name = "assetpipe_test_generators"
    pkg_dir = tmp_path / pkg_name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    # A stray top-level module living directly in the package dir (like the
    # real registry.py) must never be treated as a recipe.
    (pkg_dir / "registry.py").write_text("NOT_A_RECIPE = True\n")

    props_dir = pkg_dir / "props"
    props_dir.mkdir()
    (props_dir / "__init__.py").write_text("")
    (props_dir / "_helpers.py").write_text("# private module, must be skipped\n")
    (props_dir / "crate.py").write_text(
        "PARAM_SCHEMA = {'type': 'object', 'properties': {\n"
        "    'width_m': {'type': 'number', 'minimum': 0.1, 'maximum': 2.0, 'default': 0.5}}}\n"
        "CATEGORY = 'prop_small'\n"
        "KEYWORDS = ['crate', 'box']\n"
        "def generate(params, rng, theme):\n"
        "    raise RuntimeError('generate() must only run inside Blender')\n"
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        reg = Registry.discover(package=pkg_name)
        assert reg.ids() == ["props/crate"]
        assert reg.get("props/crate").CATEGORY == "prop_small"
    finally:
        for mod in list(sys.modules):
            if mod == pkg_name or mod.startswith(pkg_name + "."):
                del sys.modules[mod]
