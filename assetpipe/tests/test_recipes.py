"""Generator recipes: discovery, schema/bbox invariants, and the bpy-free
import discipline (spec 9.1-9.4)."""
from __future__ import annotations

import ast
import inspect

import pytest

from assetpipe.contracts import MESH_CATEGORIES
from assetpipe.generators.registry import Registry

EXPECTED_RECIPES = {
    "props/crate": "prop_small",
    "props/barrel": "prop_small",
    "props/lantern": "prop_small",
    "env/rock": "environment_piece",
    "env/tree_lowpoly": "environment_piece",
    "kit/wall": "modular_kit_piece",
    "kit/floor": "modular_kit_piece",
    "kit/doorway": "modular_kit_piece",
    "char/humanoid_stylized": "character_primary",
}

_NUMERIC_TYPES = ("number", "integer")


@pytest.fixture(scope="module")
def registry():
    return Registry.discover()


# ---------- discovery ----------

def test_discover_finds_all_nine_recipes(registry):
    assert set(registry.ids()) == set(EXPECTED_RECIPES)


@pytest.mark.parametrize("recipe_id,expected_category", sorted(EXPECTED_RECIPES.items()))
def test_recipe_has_expected_category(registry, recipe_id, expected_category):
    module = registry.get(recipe_id)
    assert module.CATEGORY == expected_category
    assert module.CATEGORY in MESH_CATEGORIES


def test_crate_matches_spec_9_1_example_schema(registry):
    schema = registry.get("props/crate").PARAM_SCHEMA
    assert set(schema["properties"]) == {
        "width_m", "height_m", "chamfer", "panel_lines", "greeble_density", "materials",
    }


# ---------- PARAM_SCHEMA bounds ----------

@pytest.mark.parametrize("recipe_id", sorted(EXPECTED_RECIPES))
def test_numeric_params_have_bounded_default(registry, recipe_id):
    schema = registry.get(recipe_id).PARAM_SCHEMA
    for prop, prop_spec in schema.get("properties", {}).items():
        if prop_spec.get("type") in _NUMERIC_TYPES:
            assert "minimum" in prop_spec, f"{recipe_id}.{prop} missing minimum"
            assert "maximum" in prop_spec, f"{recipe_id}.{prop} missing maximum"
            assert "default" in prop_spec, f"{recipe_id}.{prop} missing default"
            assert prop_spec["minimum"] <= prop_spec["default"] <= prop_spec["maximum"], (
                f"{recipe_id}.{prop} default {prop_spec['default']} out of "
                f"[{prop_spec['minimum']}, {prop_spec['maximum']}]"
            )


# ---------- BBOX_RANGE ----------

@pytest.mark.parametrize("recipe_id", sorted(EXPECTED_RECIPES))
def test_bbox_range_present_and_well_formed(registry, recipe_id):
    module = registry.get(recipe_id)
    bbox = module.BBOX_RANGE
    assert set(bbox) == {"min", "max"}
    assert len(bbox["min"]) == 3
    assert len(bbox["max"]) == 3
    for axis in range(3):
        assert bbox["min"][axis] <= bbox["max"][axis], (
            f"{recipe_id}: BBOX_RANGE axis {axis} has min > max")


def test_crate_bbox_is_at_most_1_2_m(registry):
    bbox = registry.get("props/crate").BBOX_RANGE
    assert max(bbox["max"]) <= 1.2


def test_wall_and_doorway_bbox_footprint_is_exactly_3m(registry):
    for recipe_id in ("kit/wall", "kit/doorway"):
        bbox = registry.get(recipe_id).BBOX_RANGE
        # Width (X) and height (Z) are the fixed 3 m x 3 m footprint;
        # thickness (Y) is the only free axis (spec 9.4).
        assert bbox["min"][0] == bbox["max"][0] == 3.0
        assert bbox["min"][2] == bbox["max"][2] == 3.0


def test_floor_bbox_footprint_is_exactly_3m(registry):
    bbox = registry.get("kit/floor").BBOX_RANGE
    assert bbox["min"][0] == bbox["max"][0] == 3.0
    assert bbox["min"][1] == bbox["max"][1] == 3.0


def test_humanoid_bbox_height_is_1_6_to_2_0_m(registry):
    bbox = registry.get("char/humanoid_stylized").BBOX_RANGE
    assert bbox["min"][2] >= 1.6
    assert bbox["max"][2] <= 2.0


# ---------- bpy-free import discipline ----------

@pytest.mark.parametrize("recipe_id", sorted(EXPECTED_RECIPES))
def test_recipe_source_has_no_module_level_blender_import(registry, recipe_id):
    module = registry.get(recipe_id)
    source = inspect.getsource(module)
    tree = ast.parse(source)
    blender_only = {"bpy", "bmesh", "mathutils"}

    for node in tree.body:  # module-level statements only (no recursion into functions)
        if isinstance(node, ast.Import):
            names = {alias.name.split(".")[0] for alias in node.names}
            assert not (names & blender_only), (
                f"{recipe_id}: module-level import of {names & blender_only}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            assert root not in blender_only, (
                f"{recipe_id}: module-level 'from {node.module} import ...'")


@pytest.mark.parametrize("recipe_id", sorted(EXPECTED_RECIPES))
def test_generate_exists_and_is_callable(registry, recipe_id):
    module = registry.get(recipe_id)
    assert callable(module.generate)
    sig = inspect.signature(module.generate)
    assert list(sig.parameters) == ["params", "rng", "theme"]


def test_registering_all_nine_recipes_does_not_raise():
    # Registry.discover() already calls register() internally; re-assert
    # the whole set validates together (no cross-recipe id collisions etc.)
    reg = Registry.discover()
    assert len(reg) == 9
