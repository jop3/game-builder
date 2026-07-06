"""Bpy-free verification of assetpipe/blender_scripts (spec 4.3, 9-14, 16.2).

Blender is not installed in this environment, so the bpy-touching modules
(``generate.py``, ``bake.py``, ``export_gltf.py``, ``static_checks_mesh.py``,
``render_views.py``, ``fixes.py``) are never imported here -- only parsed/
compiled, which does not require ``bpy`` (or any other import target) to
actually exist, since neither ``py_compile`` nor ``ast.parse`` resolves
imports. The bpy-free modules (``common``, ``views``, ``contact_sheets``) are
imported and exercised directly.
"""
from __future__ import annotations

import ast
import json
import py_compile
import random
from pathlib import Path

import pytest
from PIL import Image

from assetpipe.blender_scripts import common, contact_sheets, views

BLENDER_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "blender_scripts"
SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"

BPY_TOUCHING_MODULES = [
    "generate.py", "bake.py", "export_gltf.py",
    "static_checks_mesh.py", "render_views.py", "fixes.py",
]
BPY_FREE_MODULES = ["__init__.py", "common.py", "views.py", "contact_sheets.py"]
ALL_MODULES = BPY_TOUCHING_MODULES + BPY_FREE_MODULES


# ---------------------------------------------------------------------------
# (a) every file compiles
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename", ALL_MODULES)
def test_module_compiles(filename, tmp_path):
    src_path = BLENDER_SCRIPTS_DIR / filename
    assert src_path.is_file(), f"missing {src_path}"
    # py_compile never imports the module's dependencies -- it only compiles
    # source to bytecode -- so this is safe without bpy installed.
    py_compile.compile(str(src_path), cfile=str(tmp_path / (filename + "c")), doraise=True)


@pytest.mark.parametrize("filename", ALL_MODULES)
def test_module_parses_with_ast(filename):
    src_path = BLENDER_SCRIPTS_DIR / filename
    tree = ast.parse(src_path.read_text(), filename=str(src_path))
    assert isinstance(tree, ast.Module)


def _top_level_function_names(filename: str) -> set[str]:
    src_path = BLENDER_SCRIPTS_DIR / filename
    tree = ast.parse(src_path.read_text(), filename=str(src_path))
    return {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


# ---------------------------------------------------------------------------
# (b) every fixes.json blender implementation resolves to a top-level
# function in the right module (via ast, not import)
# ---------------------------------------------------------------------------

def test_every_fixes_json_blender_implementation_exists():
    fixes = json.loads((SCHEMAS_DIR / "fixes.json").read_text())["fixes"]
    prefix = "assetpipe.blender_scripts.fixes."
    blender_fix_ids = [
        (fid, spec["implementation"])
        for fid, spec in fixes.items()
        if spec["implementation"].startswith(prefix)
    ]
    assert blender_fix_ids, "expected at least one assetpipe.blender_scripts.fixes.* implementation"

    fn_names = _top_level_function_names("fixes.py")
    for fix_id, impl in blender_fix_ids:
        func_name = impl[len(prefix):]
        assert "." not in func_name, f"{fix_id}: {impl!r} is not a top-level function path"
        assert func_name in fn_names, (
            f"{fix_id}: fixes.py has no top-level function {func_name!r} "
            f"(implementation={impl!r})")


def test_fixes_module_defines_fix_table_mapping_every_blender_fix():
    """FIX_TABLE (used by apply_actions/main) must cover every fix_id whose
    implementation lives in this module, and only fix_ids that do."""
    fixes = json.loads((SCHEMAS_DIR / "fixes.json").read_text())["fixes"]
    prefix = "assetpipe.blender_scripts.fixes."
    expected = {fid for fid, spec in fixes.items() if spec["implementation"].startswith(prefix)}

    src = (BLENDER_SCRIPTS_DIR / "fixes.py").read_text()
    tree = ast.parse(src)
    fix_table = next(
        node for node in tree.body
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "FIX_TABLE" for t in node.targets))
    keys = {elt.value for elt in fix_table.value.keys}
    assert keys == expected


# ---------------------------------------------------------------------------
# (c) common.py: parse_args round-trips argv + args-json
# ---------------------------------------------------------------------------

def test_parse_args_round_trips_argv_and_json(tmp_path):
    payload = {"request": {"asset_id": "crate_01", "seed": 7}, "out_dir": "iter_01"}
    args_path = tmp_path / "args.json"
    args_path.write_text(json.dumps(payload))

    argv = ["blender", "file.blend", "--python", "generate.py", "--",
            "--args-json", str(args_path), "--out", "renders/", "--verbose"]
    result = common.parse_args(argv)

    assert result["request"]["asset_id"] == "crate_01"
    assert result["out_dir"] == "iter_01"
    assert result["_argv"]["args-json"] == str(args_path)
    assert result["_argv"]["out"] == "renders/"
    assert result["_argv"]["verbose"] is True


def test_parse_args_without_bare_dashdash_uses_argv_tail(tmp_path):
    payload = {"a": 1}
    args_path = tmp_path / "args.json"
    args_path.write_text(json.dumps(payload))
    result = common.parse_args(["script.py", "--args-json", str(args_path)])
    assert result["a"] == 1


def test_parse_args_missing_args_json_raises():
    with pytest.raises(ValueError):
        common.parse_args(["script.py", "--", "--out", "renders/"])


def test_write_result_creates_parent_dirs_and_round_trips(tmp_path):
    out_path = tmp_path / "nested" / "dir" / "result.json"
    common.write_result(out_path, {"stage": "G", "triangles": 742})
    assert json.loads(out_path.read_text()) == {"stage": "G", "triangles": 742}


def test_seeded_random_is_deterministic():
    a = common.seeded_random(42)
    b = common.seeded_random(42)
    assert [a.random() for _ in range(5)] == [b.random() for _ in range(5)]


def test_seeded_numpy_rng_is_deterministic():
    a = common.seeded_numpy_rng(7)
    b = common.seeded_numpy_rng(7)
    assert list(a.uniform(size=4)) == list(b.uniform(size=4))


# ---------------------------------------------------------------------------
# resolve_params (spec 9.3) -- pure, so exercised directly here too
# ---------------------------------------------------------------------------

def test_resolve_params_defaults_theme_clamp_jitter_and_overrides():
    schema = {
        "type": "object",
        "properties": {
            "width_m": {"type": "number", "minimum": 0.3, "maximum": 1.2, "default": 0.6},
            "panel_lines": {"type": "integer", "minimum": 0, "maximum": 6, "default": 2},
            "wear": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.9},
        },
        "additionalProperties": False,
    }
    theme = {"wear_range": [0.15, 0.55]}
    rng = random.Random(0)

    params = common.resolve_params(schema, theme, {"panel_lines": 99}, rng)

    assert 0.3 <= params["width_m"] <= 1.2
    assert 0.15 <= params["wear"] <= 0.55        # theme clamp applied before jitter
    assert isinstance(params["panel_lines"], int)
    assert params["panel_lines"] == 6             # override clamped to schema maximum


def test_resolve_params_is_deterministic_for_same_rng_state():
    schema = {"type": "object", "properties": {
        "x": {"type": "number", "minimum": 0.0, "maximum": 10.0, "default": 5.0}}}
    p1 = common.resolve_params(schema, {}, {}, random.Random(123))
    p2 = common.resolve_params(schema, {}, {}, random.Random(123))
    assert p1 == p2


# ---------------------------------------------------------------------------
# (d) export_gltf.py: spec 12.1 exporter kwargs appear verbatim
# ---------------------------------------------------------------------------

def test_export_gltf_uses_canonical_exporter_kwargs():
    src = (BLENDER_SCRIPTS_DIR / "export_gltf.py").read_text()
    for literal in [
        "export_format='GLB'",
        "export_draco_mesh_compression_enable=False",
        "export_tangents=True",
        "export_yup=True",
        "export_apply=True",
        "export_animations=False",
        "export_materials='EXPORT'",
    ]:
        assert literal in src, f"missing canonical exporter kwarg: {literal!r}"


def test_export_gltf_calls_export_scene_gltf_once():
    tree = ast.parse((BLENDER_SCRIPTS_DIR / "export_gltf.py").read_text())
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "gltf"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "export_scene"
    ]
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# (e) render_views.py / views.py: every spec 14.2 view_id is defined
# ---------------------------------------------------------------------------

REQUIRED_MESH_VIEW_IDS = {
    *[f"turn_{az:03d}" for az in range(0, 360, 45)],
    "high_045", "high_225", "top", "close_034",
    "lit_warm_045", "lit_warm_225", "lit_dark_090",
    "silhouette_000", "silhouette_090",
    "normals_045", "normals_225",
    "uvcheck_045",
}


def test_view_set_defines_every_spec_14_2_mesh_view_id():
    assert REQUIRED_MESH_VIEW_IDS <= set(views.VIEW_IDS)


def test_render_views_module_imports_the_views_table():
    src = (BLENDER_SCRIPTS_DIR / "render_views.py").read_text()
    assert "views" in ast.dump(ast.parse(src))
    tree = ast.parse(src)
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "views" in imported_names


def test_view_set_for_category_dispatches_by_category():
    assert views.view_set_for_category("tiling_texture_set") == views.TILING_VIEW_SET
    assert views.view_set_for_category("skybox") == views.SKYBOX_VIEW_SET
    mesh_views = views.view_set_for_category("prop_small")
    assert mesh_views == views.MESH_VIEW_SET
    char_views = views.view_set_for_category("character_primary")
    assert all(v in char_views for v in views.CHARACTER_EXTRA_VIEWS)


def test_frame_distance_and_fill_fraction_are_inverses():
    bbox_min, bbox_max = (-0.5, -0.5, 0.0), (0.5, 0.5, 1.0)
    fov = 0.9
    dist = views.frame_distance(bbox_min, bbox_max, fov, fill=0.65)
    fraction = views.frame_fill_fraction(bbox_min, bbox_max, fov, dist)
    assert fraction == pytest.approx(0.65, rel=1e-6)


def test_camera_transform_places_camera_at_expected_distance():
    bbox_min, bbox_max = (-1.0, -1.0, 0.0), (1.0, 1.0, 2.0)
    location, target = views.camera_transform(bbox_min, bbox_max, azimuth_deg=0,
                                              elevation_deg=0, fov_rad=1.0, fill=0.65)
    center = tuple((bbox_min[i] + bbox_max[i]) / 2 for i in range(3))
    assert target == center
    dist = sum((location[i] - center[i]) ** 2 for i in range(3)) ** 0.5
    expected = views.frame_distance(bbox_min, bbox_max, 1.0, 0.65)
    assert dist == pytest.approx(expected, rel=1e-6)


def test_fill_fraction_for_close_view_is_zoomed_in():
    close_view = next(v for v in views.MESH_VIEW_SET if v["view_id"] == "close_034")
    turn_view = next(v for v in views.MESH_VIEW_SET if v["view_id"] == "turn_000")
    assert views.fill_fraction_for_view(close_view) > views.fill_fraction_for_view(turn_view)


# ---------------------------------------------------------------------------
# contact_sheets.py -- bpy-free, unit-tested with tiny synthetic images
# ---------------------------------------------------------------------------

def _make_png(path: Path, color: tuple[int, int, int], size: int = 8) -> Path:
    Image.new("RGB", (size, size), color).save(path)
    return path


def test_chunk_views_splits_into_grid_sized_groups():
    ids = [f"v{i}" for i in range(14)]
    chunks = contact_sheets.chunk_views(ids, grid=(2, 3))
    assert [len(c) for c in chunks] == [6, 6, 2]
    assert sum(chunks, []) == ids


def test_compose_sheet_writes_expected_size_and_labels(tmp_path):
    cells = [
        ("turn_000", _make_png(tmp_path / "a.png", (255, 0, 0))),
        ("turn_045", _make_png(tmp_path / "b.png", (0, 255, 0))),
    ]
    cell_px = 64
    out = contact_sheets.compose_sheet(cells, tmp_path / "sheet.png", grid=(2, 1), cell_px=cell_px)
    img = Image.open(out)
    assert img.size == (2 * cell_px, cell_px)
    # Sample away from the burned-in label (top-left corner) so the
    # underlying cell color, not the label backing strip, is checked.
    left = img.crop((0, 0, cell_px, cell_px)).getpixel((cell_px - 4, cell_px - 4))
    right = img.crop((cell_px, 0, 2 * cell_px, cell_px)).getpixel((cell_px - 4, cell_px - 4))
    assert left[0] > left[1]
    assert right[1] > right[0]


def test_compose_sheet_rejects_too_many_cells(tmp_path):
    cells = [(f"v{i}", _make_png(tmp_path / f"{i}.png", (1, 2, 3))) for i in range(3)]
    with pytest.raises(ValueError):
        contact_sheets.compose_sheet(cells, tmp_path / "out.png", grid=(1, 1), cell_px=4)


def test_compose_all_writes_numbered_sheets(tmp_path):
    renders_dir = tmp_path / "renders"
    renders_dir.mkdir()
    view_ids = [f"v{i}" for i in range(5)]
    for vid in view_ids:
        _make_png(renders_dir / f"{vid}.png", (10, 20, 30))
    out_dir = tmp_path / "sheets"
    sheets = contact_sheets.compose_all(renders_dir, view_ids, out_dir, grid=(2, 2), cell_px=4)
    assert [p.name for p in sheets] == ["contact_sheet_1.png", "contact_sheet_2.png"]
    assert all(p.is_file() for p in sheets)
