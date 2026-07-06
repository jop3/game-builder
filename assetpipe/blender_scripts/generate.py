"""Stage G -- procedural object generation (spec 9).

Runs inside Blender: ``blender --background --python generate.py --
--args-json <path>``. Resolves a generator recipe via
:mod:`assetpipe.generators.registry`, resolves final parameters (spec 9.3,
:func:`assetpipe.blender_scripts.common.resolve_params`), builds the object,
enforces scene conventions (spec 9.4: ``EXPORT`` collection, base-center
origin, identity transforms), runs the finishing pass (spec 9.5, exact
order) and the UV pass (spec 9.6), then saves ``asset.blend`` and writes
``params.json`` / ``result.json``.

Expected ``--args-json`` payload (spec 5's G-stage inputs):

    {
      "request": {...AssetRequest...},        # schema: asset_request.schema.json
      "theme": {...theme.json...},
      "profile": {...platform profile json...},
      "generator": "props/crate",              # optional; falls back to
                                                 # request["generator"] or
                                                 # registry keyword resolution
      "out_dir": "runs/<run>/<asset>/iter_01"
    }
"""
from __future__ import annotations

import math
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix

from assetpipe.blender_scripts import common
from assetpipe.generators import registry as gen_registry

EXPORT_COLLECTION_NAME = "EXPORT"
FINISHING_REMOVE_DOUBLES_DIST = 1e-5
UV_ISLAND_MARGIN_TEXELS = 4.0
DECIMATE_STEP_RATIO = 0.85
DECIMATE_MAX_STEPS = 5
DEFAULT_TILING_TEXEL_DENSITY_PX_PER_M = 256.0


# ---------------------------------------------------------------------------
# Determinism (spec 3) -- shared by bake.py and render_views.py, which import
# this function rather than duplicating scene setup. Documented (not
# defined) in common.py because common.py must stay bpy-free.
# ---------------------------------------------------------------------------

def deterministic_scene_settings(scene) -> None:
    """Pin every setting that affects generation/bake/render determinism
    (spec 3): metric units at 1 BU = 1 m, Cycles on CPU, fixed seed, no
    animated seed. Callers that also render (bake.py, render_views.py) layer
    additional settings (samples, denoiser, view transform) on top of this."""
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1.0
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.seed = 0
    scene.cycles.use_animated_seed = False


# ---------------------------------------------------------------------------
# Scene conventions (spec 9.4)
# ---------------------------------------------------------------------------

def ensure_export_collection() -> "bpy.types.Collection":
    coll = bpy.data.collections.get(EXPORT_COLLECTION_NAME)
    if coll is None:
        coll = bpy.data.collections.new(EXPORT_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(coll)
    return coll


def link_to_collection(obj: "bpy.types.Object", coll: "bpy.types.Collection") -> None:
    for existing in list(obj.users_collection):
        existing.objects.unlink(obj)
    coll.objects.link(obj)


def ensure_transforms_applied(obj: "bpy.types.Object") -> None:
    """Bake the object's current world transform into its mesh data and reset
    the object transform to identity (spec 9.4: "Transforms applied: object
    scale = (1,1,1), rotation = identity"). Headless-safe -- avoids the
    ``bpy.ops.object.transform_apply`` context dance (blender-procedural-
    geometry skill)."""
    obj.data.transform(obj.matrix_basis)
    obj.matrix_basis = Matrix.Identity(4)


def recenter_to_base(obj: "bpy.types.Object") -> None:
    """Move mesh geometry (not the object transform) so the origin sits at
    the base-center: XY centroid, min-Z plane (spec 9.4). Skipped by callers
    for characters, whose recipes place the origin at the feet directly."""
    verts = obj.data.vertices
    if not verts:
        return
    xs = [v.co.x for v in verts]
    ys = [v.co.y for v in verts]
    zmin = min(v.co.z for v in verts)
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    for v in verts:
        v.co.x -= cx
        v.co.y -= cy
        v.co.z -= zmin
    obj.data.update()


# ---------------------------------------------------------------------------
# Finishing pass (spec 9.5) -- order matters, see blender-procedural-geometry
# skill: triangulating before remove_doubles leaves needle triangles.
# ---------------------------------------------------------------------------

def apply_all_modifiers(obj: "bpy.types.Object") -> None:
    """Apply modifiers via the evaluated depsgraph (headless-safe; no
    ``bpy.ops.object.modifier_apply`` context requirement)."""
    deps = bpy.context.evaluated_depsgraph_get()
    obj.data = bpy.data.meshes.new_from_object(obj.evaluated_get(deps))
    obj.modifiers.clear()


def triangle_count(obj: "bpy.types.Object") -> int:
    obj.data.calc_loop_triangles()
    return len(obj.data.loop_triangles)


def run_finishing_pass(obj: "bpy.types.Object", budget_max: int, budget_min: int = 0) -> int:
    """Spec 9.5, exact order: apply modifiers -> remove_doubles(1e-5) ->
    recalc normals outside -> triangulate (beauty) -> if over budget,
    Decimate in 0.85 steps (max 5) until <= budget. Returns the final
    triangle count (still possibly over budget after 5 steps -- that is a V1
    ``OVER_BUDGET``/``OVER_BUDGET_UNFIXABLE`` failure, not something this
    function silently hides)."""
    apply_all_modifiers(obj)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=FINISHING_REMOVE_DOUBLES_DIST)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bmesh.ops.triangulate(bm, faces=bm.faces, quad_method='BEAUTY', ngon_method='BEAUTY')
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    tris = triangle_count(obj)
    steps = 0
    while tris > budget_max and steps < DECIMATE_MAX_STEPS:
        mod = obj.modifiers.new(f"BudgetDecimate{steps}", 'DECIMATE')
        mod.ratio = DECIMATE_STEP_RATIO
        mod.use_collapse_triangulate = True
        apply_all_modifiers(obj)
        tris = triangle_count(obj)
        steps += 1
    return tris


# ---------------------------------------------------------------------------
# UV pass (spec 9.6)
# ---------------------------------------------------------------------------

def has_recipe_seams(obj: "bpy.types.Object") -> bool:
    return any(e.use_seam for e in obj.data.edges)


def unwrap_by_seams(obj: "bpy.types.Object") -> None:
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.unwrap(method='ANGLE_BASED')
    bpy.ops.object.mode_set(mode='OBJECT')


def smart_uv_project(obj: "bpy.types.Object", island_margin: float) -> None:
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=island_margin)
    bpy.ops.object.mode_set(mode='OBJECT')


def box_project_uvs(obj: "bpy.types.Object", texel_density_px_per_m: float,
                     texture_resolution: int) -> None:
    """Tiling surfaces: box-projection UVs at a fixed world-space texel
    density (spec 9.6). Picks the dominant axis plane per face normal and
    maps ``UV = world-plane coordinate * density / target resolution`` --
    tiling UVs are expected to exceed [0, 1], which is exactly why S12c is
    skipped for ``uv_mode: "tiling"`` faces."""
    mesh = obj.data
    uv_layer = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        n = poly.normal
        axis = max(range(3), key=lambda i: abs(n[i]))
        for li in poly.loop_indices:
            co = mesh.vertices[mesh.loops[li].vertex_index].co
            if axis == 0:
                u, w = co.y, co.z
            elif axis == 1:
                u, w = co.x, co.z
            else:
                u, w = co.x, co.y
            uv_layer.data[li].uv = (
                u * texel_density_px_per_m / texture_resolution,
                w * texel_density_px_per_m / texture_resolution,
            )
    mesh["uv_mode"] = "tiling"


def run_uv_pass(obj: "bpy.types.Object", texture_resolution: int, tiling: bool,
                 texel_density_px_per_m: float = DEFAULT_TILING_TEXEL_DENSITY_PX_PER_M) -> None:
    if tiling:
        box_project_uvs(obj, texel_density_px_per_m, texture_resolution)
        return
    if has_recipe_seams(obj):
        unwrap_by_seams(obj)
    else:
        island_margin = UV_ISLAND_MARGIN_TEXELS / texture_resolution
        smart_uv_project(obj, island_margin)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _resolve_recipe_id(payload: dict, request: dict, registry: "gen_registry.Registry") -> str:
    recipe_id = payload.get("generator") or request.get("generator")
    if recipe_id:
        return recipe_id
    category = request["category"]
    resolved = registry.resolve(category, request.get("description", ""))
    if resolved is None:
        raise RuntimeError(
            f"NO_GENERATOR: no recipe registered for category={category!r} "
            f"matches description {request.get('description', '')!r}")
    return resolved


def main() -> None:
    payload = common.parse_args()
    request = payload["request"]
    theme = payload.get("theme", {})
    profile = payload.get("profile", {})
    out_dir = Path(payload["out_dir"])

    seed = int(request["seed"])
    rng = common.seeded_random(seed)
    category = request["category"]

    registry = gen_registry.Registry.discover()
    recipe_id = _resolve_recipe_id(payload, request, registry)
    module = registry.get(recipe_id)

    params = common.resolve_params(
        module.PARAM_SCHEMA, theme, request.get("param_overrides", {}), rng)
    # Written before generation runs (spec 9.3): a crash still leaves the
    # exact inputs on disk, and this is the fix loop's canonical editing surface.
    common.write_result(out_dir / "params.json", params)

    scene = bpy.context.scene
    deterministic_scene_settings(scene)

    root = module.generate(params, rng, theme)
    link_to_collection(root, ensure_export_collection())
    ensure_transforms_applied(root)

    budgets = profile.get("triangles", {}).get(category, {})
    tris = run_finishing_pass(root, budget_max=budgets.get("max", 10 ** 9),
                               budget_min=budgets.get("min", 0))

    texture_budget = profile.get("textures", {}).get(category, {}).get("albedo", 1024)
    tiling = category == "tiling_texture_set"
    run_uv_pass(root, texture_resolution=texture_budget, tiling=tiling)

    blend_path = out_dir / "asset.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

    common.write_result(out_dir / "result.json", {
        "asset_id": request["asset_id"],
        "stage": "G",
        "recipe": recipe_id,
        "seed": seed,
        "triangles": tris,
        "root_object": root.name,
        "blend": str(blend_path),
    })


if __name__ == "__main__":
    main()
