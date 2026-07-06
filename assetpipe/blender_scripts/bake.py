"""Stage M -- material & texture generation, bake procedure (spec 10, 10.3).

Runs inside Blender on the ``asset.blend`` produced by ``generate.py``.
Builds a material recipe's node graph, then bakes it to PNG maps using
Cycles' bake API. Everything here is deterministic (spec 3): CPU device,
``cycles.seed = 0``, ``use_animated_seed = False``.

Bake sample counts / margin (16 color, 64 normal+AO, 8px margin) are fixed by
spec 10.3 -- not tunable validation thresholds, so unlike the S-check
thresholds they are module constants here rather than pulled from
``config/defaults.yaml``.

Material recipes are plain Python modules (mirroring the generator recipe
contract in ``assetpipe/generators/__init__.py``): ``PARAM_SCHEMA``,
``BAKES`` (which maps this material actually produces), ``TILING`` (bool),
and ``build(nt, params, rng, palette)`` populating a node tree ending in a
Principled BSDF. They live under ``themes/<theme_id>/materials/<name>.py``
(spec 10.2) and are not shipped yet (see ``assetpipe/README.md``'s build
order) -- this module loads one dynamically by dotted module path from the
args-json payload, exactly like ``generate.py`` uses the generator registry.

**Tiling dependency (documented, not yet shippable):** for
``tiling_texture_set`` requests, material recipes must route every periodic
node's vector input through a shared ``PeriodicCoords`` node group (spec
10.3, 4D torus-mapping so the bake is mathematically periodic) provided by
``assetpipe/matlib`` (not built yet). This module looks the group up **by
name** (``bpy.data.node_groups['PeriodicCoords']``) rather than importing a
Python module for it, since it is a Blender asset (node tree), not code --
:func:`snap_periodic_scale_to_integer` documents the exact contract it
expects (a ``"period_scale"`` node-group input).
"""
from __future__ import annotations

import importlib
from pathlib import Path

import bpy

from assetpipe.blender_scripts import common
from assetpipe.blender_scripts.generate import deterministic_scene_settings

BAKE_SAMPLES_COLOR = 16          # spec 10.3: albedo/emissive (color pass, noise-free)
BAKE_SAMPLES_NORMAL_AO = 64      # spec 10.3: normal + AO (real Monte-Carlo passes)
BAKE_MARGIN_PX = 8              # spec 10.3: bleed past island borders
MAP_FILENAMES = {
    "albedo": "albedo.png", "normal": "normal.png",
    "orm": "orm.png", "emissive": "emissive.png",
}


# ---------------------------------------------------------------------------
# Node-tree construction helpers (pbr-material-baking skill)
# ---------------------------------------------------------------------------

def new_material(name: str) -> "bpy.types.Material":
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    return mat


def load_material_recipe(recipe_id: str, theme_id: str | None = None):
    """Load a material recipe module (spec 10.2's recipe module contract).

    Two accepted forms:
    - bare recipe id (e.g. ``"scifi_hull_metal"``) + ``theme_id`` -- resolved
      through :func:`assetpipe.themes_io.load_material_recipe` against the
      repo ``themes/`` root (theme material modules are plain files, not an
      importable package, so this is the canonical path; it is what the
      orchestrator's bake payload sends);
    - a dotted module path (contains ``.``) -- imported directly, for ad-hoc /
      test recipes that do live on ``sys.path``.
    """
    if "." in recipe_id:
        return importlib.import_module(recipe_id)
    if not theme_id:
        raise RuntimeError(f"bare material recipe id {recipe_id!r} requires a "
                           "'theme_id' in the bake payload")
    from assetpipe.themes_io import load_material_recipe as _load
    themes_root = Path(__file__).resolve().parent.parent.parent / "themes"
    return _load(themes_root, theme_id, recipe_id)


def get_periodic_coords_group():
    """Look up the ``PeriodicCoords`` node group by name (spec 10.3; provided
    by ``assetpipe/matlib`` -- see module docstring). Raises a clear error if
    a tiling bake is attempted before matlib ships it, rather than silently
    baking non-periodic noise."""
    group = bpy.data.node_groups.get("PeriodicCoords")
    if group is None:
        raise RuntimeError(
            "tiling bake requested but the 'PeriodicCoords' node group is not "
            "present in this .blend -- it must be appended from assetpipe/matlib "
            "before baking any tiling_texture_set material (spec 10.3)")
    return group


def snap_periodic_scale_to_integer(mat: "bpy.types.Material") -> None:
    """``TILING_SEAM`` table fix (spec 16.2): snap the ``PeriodicCoords`` node
    group instance's ``period_scale`` input to the nearest integer so the
    tile contains a whole number of pattern periods (non-integer periods are
    the classic cause of a visible seam in periodic-noise tiling)."""
    nt = mat.node_tree
    for node in nt.nodes:
        if node.type == 'GROUP' and node.node_tree and node.node_tree.name == "PeriodicCoords":
            sock = node.inputs.get("period_scale")
            if sock is not None:
                sock.default_value = max(1, round(sock.default_value))


# ---------------------------------------------------------------------------
# Bake setup + per-channel recipes (spec 10.3)
# ---------------------------------------------------------------------------

def setup_bake_settings(scene, margin: int = BAKE_MARGIN_PX) -> None:
    deterministic_scene_settings(scene)
    scene.render.bake.margin = margin
    scene.render.bake.use_selected_to_active = False


def _target_image(nt, name: str, resolution: int, colorspace: str, float_buffer: bool = True):
    img = bpy.data.images.new(name, width=resolution, height=resolution,
                              alpha=False, float_buffer=float_buffer)
    img.colorspace_settings.name = colorspace
    tex_node = nt.nodes.new('ShaderNodeTexImage')
    tex_node.image = img
    nt.nodes.active = tex_node
    return img, tex_node


def _select_active(obj) -> None:
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def bake_albedo(obj, mat: "bpy.types.Material", resolution: int, out_path: Path) -> Path:
    """spec 10.3 step 1: ``DIFFUSE`` bake, color contribution only, sRGB."""
    scene = bpy.context.scene
    scene.cycles.samples = BAKE_SAMPLES_COLOR
    _select_active(obj)
    img, _ = _target_image(mat.node_tree, "albedo_bake", resolution, 'sRGB')
    bpy.ops.object.bake(type='DIFFUSE', pass_filter={'COLOR'})
    img.filepath_raw = str(out_path)
    img.file_format = 'PNG'
    img.save()
    return out_path


def bake_normal(obj, mat: "bpy.types.Material", resolution: int, out_path: Path) -> Path:
    """spec 10.3 step 2: ``NORMAL`` bake, tangent space, OpenGL +Y (glTF
    convention -- POS_Y green, NOT DirectX NEG_Y)."""
    scene = bpy.context.scene
    scene.cycles.samples = BAKE_SAMPLES_NORMAL_AO
    scene.render.bake.normal_space = 'TANGENT'
    scene.render.bake.normal_r = 'POS_X'
    scene.render.bake.normal_g = 'POS_Y'
    scene.render.bake.normal_b = 'POS_Z'
    _select_active(obj)
    img, _ = _target_image(mat.node_tree, "normal_bake", resolution, 'Non-Color')
    bpy.ops.object.bake(type='NORMAL')
    img.filepath_raw = str(out_path)
    img.file_format = 'PNG'
    img.save()
    renormalize_normal_map(out_path)
    return out_path


def _bake_scalar_via_emit(nt, scalar_socket, out_node, resolution: int, name: str,
                          samples: int) -> "bpy.types.Image":
    """spec 10.3 step 3 / pbr-material-baking skill's EMIT-reroute trick:
    Cycles has no direct scalar bake, so temporarily wire the scalar into an
    Emission shader and bake EMIT (exact, sample-independent)."""
    scene = bpy.context.scene
    scene.cycles.samples = samples
    img, _ = _target_image(nt, name, resolution, 'Non-Color')
    emit = nt.nodes.new('ShaderNodeEmission')
    nt.links.new(scalar_socket, emit.inputs['Color'])
    surface_input = out_node.inputs['Surface']
    old_from = surface_input.links[0].from_socket if surface_input.links else None
    nt.links.new(emit.outputs['Emission'], surface_input)
    bpy.ops.object.bake(type='EMIT')
    if old_from is not None:
        nt.links.new(old_from, surface_input)
    nt.nodes.remove(emit)
    return img


def bake_orm(obj, mat: "bpy.types.Material", resolution: int, out_path: Path,
             ao_samples: int = BAKE_SAMPLES_NORMAL_AO) -> Path:
    """spec 10.3 step 3: AO via ``AO`` bake; roughness/metallic via the
    EMIT-reroute trick; composited R=AO G=roughness B=metallic with NumPy
    (linear, no alpha channel -- glTF ORM convention)."""
    import numpy as np
    from PIL import Image

    scene = bpy.context.scene
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    out_node = next(n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL')

    _select_active(obj)
    scene.cycles.samples = ao_samples
    ao_img, _ = _target_image(nt, "ao_bake", resolution, 'Non-Color')
    bpy.ops.object.bake(type='AO')

    rough_img = _bake_scalar_via_emit(nt, bsdf.inputs['Roughness'], out_node,
                                      resolution, "rough_bake", samples=1)
    metal_img = _bake_scalar_via_emit(nt, bsdf.inputs['Metallic'], out_node,
                                      resolution, "metal_bake", samples=1)

    def to_array(img) -> "np.ndarray":
        buf = np.array(img.pixels[:], dtype=np.float32).reshape(resolution, resolution, 4)
        return np.clip(buf[..., 0] * 255.0, 0, 255).astype(np.uint8)

    ao_arr, rough_arr, metal_arr = to_array(ao_img), to_array(rough_img), to_array(metal_img)
    orm = np.stack([ao_arr, rough_arr, metal_arr], axis=-1)
    Image.fromarray(orm, "RGB").save(out_path)
    return out_path


def bake_emissive(obj, mat: "bpy.types.Material", resolution: int, out_path: Path) -> Path:
    """spec 10.3 step 4: only if the recipe declares an emissive map -- bake
    ``EMIT`` with the real emission wiring (no reroute needed)."""
    scene = bpy.context.scene
    scene.cycles.samples = BAKE_SAMPLES_COLOR
    _select_active(obj)
    img, _ = _target_image(mat.node_tree, "emissive_bake", resolution, 'sRGB')
    bpy.ops.object.bake(type='EMIT')
    img.filepath_raw = str(out_path)
    img.file_format = 'PNG'
    img.save()
    return out_path


# ---------------------------------------------------------------------------
# Post-processing (spec 10.4)
# ---------------------------------------------------------------------------

def renormalize_normal_map(path: Path, min_mean_blue: float = 0.7) -> None:
    """Renormalize XYZ per pixel; raise if the S17 mean-blue sanity floor
    can't be met even after renormalization (spec 10.4, 13.3)."""
    import numpy as np
    from PIL import Image

    arr = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0
    xyz = arr * 2.0 - 1.0
    norm = np.linalg.norm(xyz, axis=-1, keepdims=True)
    norm[norm == 0] = 1.0
    xyz = xyz / norm
    out = np.clip((xyz + 1.0) * 0.5 * 255.0, 0, 255).astype(np.uint8)
    mean_blue = float(out[..., 2].mean() / 255.0)
    Image.fromarray(out, "RGB").save(path)
    if mean_blue < min_mean_blue:
        raise ValueError(
            f"normal map {path}: mean blue {mean_blue:.3f} below the {min_mean_blue} "
            f"sanity floor (S17) even after renormalization -- likely baked pre-"
            f"triangulation or with the wrong swizzle")


def dither_quantize(float01, rng_np):
    """16-bit-intermediate -> 8-bit with seeded dither (spec 10.4). Not true
    blue noise (that needs a precomputed texture); this uses per-pixel
    uniform dither seeded from ``rng_np`` (``common.seeded_numpy_rng``),
    which is deterministic and sufficient to break up banding."""
    import numpy as np

    noise = rng_np.uniform(-0.5, 0.5, size=float01.shape)
    return np.clip(np.round(float01 * 255.0 + noise), 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def bake_all_maps(ctx: dict, resolution_override: int | None = None, tiling: bool = False) -> dict:
    """Build the material recipe's node graph and bake every map it
    declares (``BAKES``). ``ctx`` carries: ``object_name``, ``material_recipe``
    (dotted path), ``material_params``, ``palette``, ``seed``, ``asset_dir``,
    ``texture_resolution``. Used by both this module's ``main()`` and by the
    rebake-family fix functions in ``fixes.py``."""
    obj = bpy.data.objects[ctx["object_name"]]
    recipe = load_material_recipe(ctx["material_recipe"], ctx.get("theme_id"))
    rng = common.seeded_random(int(ctx.get("seed", 0)))
    resolution = resolution_override or ctx.get("texture_resolution", 1024)

    mat = new_material(f"{obj.name}_material")
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    recipe.build(mat.node_tree, ctx.get("material_params", {}), rng, ctx.get("palette", {}))

    scene = bpy.context.scene
    setup_bake_settings(scene)
    maps_dir = Path(ctx["asset_dir"]) / "maps"
    written: dict[str, str] = {}

    bakes = set(getattr(recipe, "BAKES", ["albedo", "normal", "orm"]))
    if "albedo" in bakes:
        written["albedo"] = str(bake_albedo(obj, mat, resolution, maps_dir / MAP_FILENAMES["albedo"]))
    if "normal" in bakes:
        written["normal"] = str(bake_normal(obj, mat, resolution, maps_dir / MAP_FILENAMES["normal"]))
    if "orm" in bakes:
        written["orm"] = str(bake_orm(obj, mat, resolution, maps_dir / MAP_FILENAMES["orm"]))
    if "emissive" in bakes:
        written["emissive"] = str(bake_emissive(obj, mat, resolution, maps_dir / MAP_FILENAMES["emissive"]))

    return {"stage": "M", "material_recipe": ctx["material_recipe"], "maps": written}


def main() -> None:
    payload = common.parse_args()
    result = bake_all_maps(payload, tiling=payload.get("tiling", False))
    # bake_result.json, NOT result.json: generate.py already wrote result.json
    # (root_object etc.) into the same iteration dir, and downstream stages
    # (static checks, export) still read it after the bake.
    common.write_result(Path(payload["out_dir"]) / "bake_result.json", result)


if __name__ == "__main__":
    main()
