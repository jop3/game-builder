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

# Blender's bundled Python does not have this repo on sys.path when a stage
# script is launched via `blender --background --python <this file>`; bootstrap
# the repo root (two levels up) so `import assetpipe` works. Kept dependency-
# free (os, not pathlib) and inserted before the first assetpipe import.
import os as _os
import sys as _sys

_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

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


def _pixels_to_array(img) -> "np.ndarray":
    """Image pixels as a top-down ``(H, W, 4)`` float32 array. Blender stores
    pixel rows bottom-up; flip so row 0 is the image's top row."""
    import numpy as np

    w, h = img.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    return buf.reshape(h, w, 4)[::-1]


def _save_rgb8_png(arr, out_path: Path) -> None:
    """Save a top-down ``(H, W, 3)`` uint8 array as an 8-bit PNG through bpy.
    Blender's bundled Python ships NumPy but NOT Pillow, so in-Blender code
    must never import PIL (verified against real Blender 4.2). 'Non-Color'
    with no float buffer means the values are written to disk verbatim."""
    import numpy as np

    h, w = arr.shape[:2]
    img = bpy.data.images.new("__save_tmp", width=w, height=h, alpha=False,
                              float_buffer=False)
    img.colorspace_settings.name = 'Non-Color'
    rgba = np.empty((h, w, 4), dtype=np.float32)
    rgba[..., :3] = arr.astype(np.float32) / 255.0
    rgba[..., 3] = 1.0
    img.pixels.foreach_set(np.ascontiguousarray(rgba[::-1]).reshape(-1))
    img.filepath_raw = str(out_path)
    img.file_format = 'PNG'
    img.save()
    bpy.data.images.remove(img)


def _select_active(obj) -> None:
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def bake_albedo(obj, mat: "bpy.types.Material", resolution: int, out_path: Path) -> Path:
    """spec 10.3 step 1, corrected against real Blender 4.2: bake the Base
    Color *input signal* via the EMIT reroute rather than a ``DIFFUSE``
    color-only pass. The diffuse color pass is weighted by the diffuse
    closure, which is ZERO wherever metallic=1 -- fully-metal materials bake
    an all-black albedo (trips S16). EMIT of the Base Color input is exact
    for every metallic value and matches glTF ``baseColorTexture`` semantics
    (the metalness split lives in the ORM map, not in the albedo)."""
    scene = bpy.context.scene
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    out_node = next(n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL')
    _select_active(obj)
    img = _bake_input_via_emit(nt, bsdf.inputs['Base Color'], out_node, resolution,
                               "albedo_bake", samples=BAKE_SAMPLES_COLOR,
                               colorspace='sRGB')
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


def _bake_input_via_emit(nt, input_socket, out_node, resolution: int, name: str,
                         samples: int, colorspace: str = 'Non-Color') -> "bpy.types.Image":
    """spec 10.3 step 3 / pbr-material-baking skill's EMIT-reroute trick:
    Cycles cannot bake an arbitrary socket directly, so temporarily wire the
    signal into an Emission shader and bake EMIT (exact, sample-independent).
    Works for scalar sockets (Roughness/Metallic) and color sockets (Base
    Color) alike."""
    scene = bpy.context.scene
    scene.cycles.samples = samples
    img, _ = _target_image(nt, name, resolution, colorspace)
    emit = nt.nodes.new('ShaderNodeEmission')
    # ``input_socket`` is an *input* socket (e.g. bsdf.inputs['Roughness']);
    # links.new needs an output as source. Feed emission from whatever drives
    # the socket, or from a temp node holding its constant default
    # (verified against real Blender 4.2 -- linking input->input is invalid).
    temp_value = None
    if input_socket.links:
        src = input_socket.links[0].from_socket
    elif input_socket.type == 'RGBA':
        temp_value = nt.nodes.new('ShaderNodeRGB')
        temp_value.outputs[0].default_value = tuple(input_socket.default_value)
        src = temp_value.outputs[0]
    else:
        temp_value = nt.nodes.new('ShaderNodeValue')
        temp_value.outputs[0].default_value = float(input_socket.default_value)
        src = temp_value.outputs[0]
    nt.links.new(src, emit.inputs['Color'])
    surface_input = out_node.inputs['Surface']
    old_from = surface_input.links[0].from_socket if surface_input.links else None
    nt.links.new(emit.outputs['Emission'], surface_input)
    bpy.ops.object.bake(type='EMIT')
    if old_from is not None:
        nt.links.new(old_from, surface_input)
    nt.nodes.remove(emit)
    if temp_value is not None:
        nt.nodes.remove(temp_value)
    return img


def bake_orm(obj, mat: "bpy.types.Material", resolution: int, out_path: Path,
             ao_samples: int = BAKE_SAMPLES_NORMAL_AO) -> Path:
    """spec 10.3 step 3: AO via ``AO`` bake; roughness/metallic via the
    EMIT-reroute trick; composited R=AO G=roughness B=metallic with NumPy
    (linear, no alpha channel -- glTF ORM convention)."""
    import numpy as np

    scene = bpy.context.scene
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    out_node = next(n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL')

    _select_active(obj)
    scene.cycles.samples = ao_samples
    ao_img, _ = _target_image(nt, "ao_bake", resolution, 'Non-Color')
    bpy.ops.object.bake(type='AO')

    rough_img = _bake_input_via_emit(nt, bsdf.inputs['Roughness'], out_node,
                                     resolution, "rough_bake", samples=1)
    metal_img = _bake_input_via_emit(nt, bsdf.inputs['Metallic'], out_node,
                                     resolution, "metal_bake", samples=1)

    def to_array(img) -> "np.ndarray":
        # _pixels_to_array flips to top-down; _save_rgb8_png flips back on
        # write, so the composited ORM keeps the same orientation as the
        # albedo/normal maps Blender saves directly (the old PIL save wrote
        # Blender's bottom-up rows as top-down, mirroring the ORM vertically).
        return np.clip(_pixels_to_array(img)[..., 0] * 255.0, 0, 255).astype(np.uint8)

    ao_arr, rough_arr, metal_arr = to_array(ao_img), to_array(rough_img), to_array(metal_img)
    orm = np.stack([ao_arr, rough_arr, metal_arr], axis=-1)
    _save_rgb8_png(orm, out_path)
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

    src = bpy.data.images.load(str(path), check_existing=False)
    src.colorspace_settings.name = 'Non-Color'
    arr = _pixels_to_array(src)[..., :3]
    bpy.data.images.remove(src)
    xyz = arr * 2.0 - 1.0
    norm = np.linalg.norm(xyz, axis=-1, keepdims=True)
    norm[norm == 0] = 1.0
    xyz = xyz / norm
    out = np.clip((xyz + 1.0) * 0.5 * 255.0, 0, 255).astype(np.uint8)
    mean_blue = float(out[..., 2].mean() / 255.0)
    _save_rgb8_png(out, path)
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
    # Spec 9.3 applies to material recipes too (10.2: "same rules as generator
    # schemas"): defaults -> theme clamps -> seeded jitter -> request overrides.
    # ctx["material_params"] holds only the request's material_overrides, so
    # recipes indexing params["<name>"] need the schema defaults resolved here.
    params = common.resolve_params(getattr(recipe, "PARAM_SCHEMA", {}) or {},
                                   ctx.get("theme") or {},
                                   ctx.get("material_params") or {}, rng)
    recipe.build(mat.node_tree, params, rng, ctx.get("palette", {}))

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
