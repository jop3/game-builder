"""Stage F -- deterministic table-fix implementations (spec 16.2).

One function per ``assetpipe.blender_scripts.fixes.*`` dotted path in
``assetpipe/schemas/fixes.json``; ``assetpipe/fixes/apply.py`` (not built
yet) resolves a ``fix_plan.json`` action's ``fix_id`` to the matching
function here and calls it. Every function has the signature
``def fix_name(ctx: dict, action: dict) -> dict`` and returns a small result
dict describing what it did -- never raises for "nothing to do" (e.g. no
matching sockets), only for a genuinely malformed target.

``ctx`` is the args-json payload: whatever the action needs to locate its
target (``object_name``, ``material_name``, ``asset_dir``, ``params``,
``thresholds``/``validation``, ``budget``, ...). Every function is
deliberately tolerant of ``ctx`` keys it doesn't need, since the applicator
passes one shared ``ctx`` to every action in a plan.
"""
from __future__ import annotations

import math
from pathlib import Path

import bmesh
import bpy
from mathutils.bvhtree import BVHTree

# Blender's bundled Python does not have this repo on sys.path when a stage
# script is launched via `blender --background --python <this file>`; bootstrap
# the repo root (two levels up) so `import assetpipe` works. Kept dependency-
# free (os, not pathlib) and inserted before the first assetpipe import.
import os as _os
import sys as _sys

_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

from assetpipe.blender_scripts import bake, common, export_gltf
from assetpipe.blender_scripts.generate import apply_all_modifiers, run_finishing_pass


def _get_object(ctx: dict) -> "bpy.types.Object":
    return bpy.data.objects[ctx["object_name"]]


# ---------------------------------------------------------------------------
# G-stage mesh fixes
# ---------------------------------------------------------------------------

def cleanup_mesh(ctx: dict, action: dict) -> dict:
    """``NON_MANIFOLD``/``DEGENERATE_FACES``/``LOOSE_GEOMETRY`` -> bmesh
    cleanup pass (spec 16.2): delete loose geometry, dissolve degenerate
    edges, ``remove_doubles`` at 2x the S3 threshold; fill holes on closed
    topology if the mesh is still non-manifold afterward."""
    obj = _get_object(ctx)
    dist = ctx.get("thresholds", {}).get("s3_min_edge_length_m", 1e-6) * 2

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    loose_verts = [v for v in bm.verts if not v.link_edges]
    if loose_verts:
        bmesh.ops.delete(bm, geom=loose_verts, context='VERTS')
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=1e-6)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
    if ctx.get("topology", "closed") == "closed":
        boundary = [e for e in bm.edges if e.is_boundary]
        if boundary:
            bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return {"fix_id": "cleanup_mesh", "object": obj.name}


def recalc_normals(ctx: dict, action: dict) -> dict:
    """``INVERTED_NORMALS`` -> ``normals_make_consistent(inside=False)``;
    clear custom split normals (spec 16.2)."""
    obj = _get_object(ctx)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    if hasattr(obj.data, "free_normals_split"):
        obj.data.free_normals_split()
    return {"fix_id": "recalc_normals", "object": obj.name}


def reunwrap_margin(ctx: dict, action: dict) -> dict:
    """``UV_OVERLAP``/``UV_OUT_OF_BOUNDS``/``BAKE_MARGIN_LOW`` -> re-run Smart
    UV Project with ``island_margin x1.5``, pack islands (spec 16.2)."""
    obj = _get_object(ctx)
    resolution = ctx.get("texture_resolution", 1024)
    margin = (4.0 / resolution) * 1.5
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=margin)
    bpy.ops.uv.pack_islands(margin=margin)
    bpy.ops.object.mode_set(mode='OBJECT')
    return {"fix_id": "reunwrap_margin", "object": obj.name, "island_margin": margin}


def reseam_stretched_island(ctx: dict, action: dict) -> dict:
    """``UV_STRETCH`` -> auto-seam the offending island by angle (60deg),
    re-unwrap that island only (spec 16.2). ``action['target']`` names the
    island via the face int layer ``stretch_island`` (set by the static
    check / generator recipe); falls back to the whole mesh if no such
    tagging exists."""
    obj = _get_object(ctx)
    target_id = action.get("target")
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    tag = bm.faces.layers.int.get("stretch_island")
    faces = ([f for f in bm.faces if str(f[tag]) == str(target_id)] if tag else list(bm.faces))
    for e in bm.edges:
        if any(f in faces for f in e.link_faces) and e.calc_face_angle(0.0) >= math.radians(60):
            e.seam = True
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.unwrap(method='ANGLE_BASED')
    bpy.ops.object.mode_set(mode='OBJECT')
    return {"fix_id": "reseam_stretched_island", "object": obj.name, "target": target_id}


def decimate_to_budget(ctx: dict, action: dict) -> dict:
    """``OVER_BUDGET`` -> ``Decimate`` ratio = budget/current * 0.97, re-run
    the finishing pass (spec 16.2)."""
    obj = _get_object(ctx)
    budget = ctx.get("budget", {})
    budget_max = budget.get("max", 10 ** 9)
    obj.data.calc_loop_triangles()
    current = len(obj.data.loop_triangles)
    ratio = max(0.01, min(1.0, (budget_max / max(current, 1)) * 0.97))
    mod = obj.modifiers.new("DecimateFix", 'DECIMATE')
    mod.ratio = ratio
    mod.use_collapse_triangulate = True
    apply_all_modifiers(obj)
    tris = run_finishing_pass(obj, budget_max=budget_max, budget_min=budget.get("min", 0))
    return {"fix_id": "decimate_to_budget", "object": obj.name, "ratio": ratio, "triangles": tris}


def snap_sockets(ctx: dict, action: dict) -> dict:
    """``SOCKET_OFF_GRID`` -> snap ``SOCKET_*`` empties to the nearest 0.5 m
    grid point (spec 16.2, 9.4)."""
    obj = _get_object(ctx)
    grid = ctx.get("kit_grid_m", 0.5)
    snapped = []
    for child in obj.children:
        if child.name.startswith("SOCKET_"):
            child.location = tuple(round(c / grid) * grid for c in child.location)
            snapped.append(child.name)
    return {"fix_id": "snap_sockets", "sockets": snapped}


def fix_weights(ctx: dict, action: dict) -> dict:
    """``SKIN_WEIGHT_INVALID`` -> ``vertex_group_limit_total(4)`` +
    ``vertex_group_normalize_all``; re-auto-weight orphaned regions against
    the named armature if one is given (spec 16.2)."""
    obj = _get_object(ctx)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.vertex_group_limit_total(limit=4)
    bpy.ops.object.vertex_group_normalize_all(lock_active=False)

    orphaned = [v.index for v in obj.data.vertices if not any(g.weight > 0 for g in v.groups)]
    armature_name = ctx.get("armature_name")
    reweighted = False
    if orphaned and armature_name and armature_name in bpy.data.objects:
        arm_obj = bpy.data.objects[armature_name]
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        arm_obj.select_set(True)
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.parent_set(type='ARMATURE_AUTO')
        reweighted = True
    return {"fix_id": "fix_weights", "object": obj.name,
            "orphaned_before": len(orphaned), "reweighted": reweighted}


def merge_coplanar(ctx: dict, action: dict) -> dict:
    """``ZFIGHT_COPLANAR`` -> merge duplicate geometry (``remove_doubles``)
    and nudge any remaining coplanar-overlapping faces apart along their
    shared normal by a tiny offset (spec 16.2)."""
    obj = _get_object(ctx)
    dist = ctx.get("thresholds", {}).get("s3_min_edge_length_m", 1e-6) * 2
    offset = 1e-4

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)

    seen_planes: dict[tuple, int] = {}
    nudged = 0
    for f in bm.faces:
        n, c = f.normal, f.calc_center_median()
        key = (round(n.x, 3), round(n.y, 3), round(n.z, 3), round(c.dot(n), 4))
        if key in seen_planes:
            for v in f.verts:
                v.co += n * offset
            nudged += 1
        else:
            seen_planes[key] = f.index
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return {"fix_id": "merge_coplanar", "object": obj.name, "faces_nudged": nudged}


def reattach_part(ctx: dict, action: dict) -> dict:
    """``FLOATING_PART``/``INTERPENETRATION`` -> re-run part placement with
    snap-to-surface for the named sub-part (``action['target']``); if the
    part has no valid nearest-surface hit, fall back to a boolean-union pass
    (spec 16.2)."""
    obj = _get_object(ctx)
    part_name = action.get("target")
    part = bpy.data.objects.get(part_name) if part_name else None
    if part is None:
        return {"fix_id": "reattach_part", "object": obj.name, "applied": False,
                "reason": f"part {part_name!r} not found"}

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    tree = BVHTree.FromBMesh(bm)
    hit, _normal, _index, _dist = tree.find_nearest(part.location)
    bm.free()

    if hit is not None:
        part.location = hit
        return {"fix_id": "reattach_part", "object": obj.name, "part": part_name,
                "applied": True, "method": "snap_to_surface"}

    mod = obj.modifiers.new("ReattachUnion", 'BOOLEAN')
    mod.operation = 'UNION'
    mod.object = part
    apply_all_modifiers(obj)
    return {"fix_id": "reattach_part", "object": obj.name, "part": part_name,
            "applied": True, "method": "boolean_union"}


# ---------------------------------------------------------------------------
# M-stage bake/texture fixes
# ---------------------------------------------------------------------------

def rebake_margin_x2(ctx: dict, action: dict) -> dict:
    """``VISIBLE_SEAM`` -> re-bake all maps with bake margin x2 (spec 16.2;
    a repeat offense escalates to ``reseam_stretched_island`` via the
    planner, not here)."""
    scene = bpy.context.scene
    scene.render.bake.margin = bake.BAKE_MARGIN_PX * 2
    result = bake.bake_all_maps(ctx)
    return {"fix_id": "rebake_margin_x2", "margin_px": scene.render.bake.margin, **result}


def relink_textures(ctx: dict, action: dict) -> dict:
    """``MISSING_TEXTURE``/``BLACK_SURFACE`` -> verify map files exist and
    re-link any broken image path against ``<asset_dir>/maps`` (the most
    common cause: a broken image link surviving export). A second offense of
    the same defect escalates to a full re-bake via the planner, not here
    (spec 16.2)."""
    obj = _get_object(ctx)
    maps_dir = Path(ctx["asset_dir"]) / "maps"
    relinked = []
    for img in bpy.data.images:
        if img.filepath and not Path(bpy.path.abspath(img.filepath)).exists():
            candidate = maps_dir / Path(img.filepath).name
            if candidate.exists():
                img.filepath = str(candidate)
                img.reload()
                relinked.append(img.name)
    return {"fix_id": "relink_textures", "object": obj.name, "relinked": relinked}


def rebake_at_budget(ctx: dict, action: dict) -> dict:
    """``TEX_RESOLUTION_INVALID`` -> re-bake maps directly at the profile
    budget resolution (never upscale an existing bake, spec 16.2)."""
    result = bake.bake_all_maps(ctx, resolution_override=ctx.get("texture_resolution"))
    return {"fix_id": "rebake_at_budget", **result}


def rebake_normal(ctx: dict, action: dict) -> dict:
    """``NORMAL_MAP_INVALID`` -> recreate the normal target image as a
    Non-Color float buffer and re-bake the ``NORMAL`` pass with the OpenGL
    (POS_Y) swizzle (spec 16.2, 10.3)."""
    obj = _get_object(ctx)
    mat = obj.active_material
    out_path = Path(ctx["asset_dir"]) / "maps" / "normal.png"
    bake.bake_normal(obj, mat, ctx.get("texture_resolution", 1024), out_path)
    return {"fix_id": "rebake_normal", "object": obj.name, "path": str(out_path)}


def rebake_periodic_snap(ctx: dict, action: dict) -> dict:
    """``TILING_SEAM`` -> re-bake with the ``PeriodicCoords`` domain scale
    snapped to integer periods (spec 16.2, 10.3). A second offense escalates
    to increasing the pattern-scale parameter one step via the planner, not
    here."""
    obj = _get_object(ctx)
    mat = obj.active_material
    bake.snap_periodic_scale_to_integer(mat)
    result = bake.bake_all_maps(ctx, tiling=True)
    return {"fix_id": "rebake_periodic_snap", "object": obj.name, **result}


# ---------------------------------------------------------------------------
# X-stage export fixes
# ---------------------------------------------------------------------------

def reexport(ctx: dict, action: dict) -> dict:
    """``GLTF_INVALID``/``GLTF_EXTENSION_FORBIDDEN`` -> re-run the glTF
    export with the canonical settings (uncompressed, tangents on,
    whitelisted extensions only, spec 12.1/16.2)."""
    request = ctx.get("request", {})
    asset_id = request.get("asset_id", ctx.get("asset_id", "asset"))
    out_glb = Path(ctx["asset_dir"]) / f"{asset_id}.glb"
    export_gltf.export(ctx, out_glb)
    return {"fix_id": "reexport", "glb": str(out_glb)}


FIX_TABLE = {
    "cleanup_mesh": cleanup_mesh,
    "recalc_normals": recalc_normals,
    "reunwrap_margin": reunwrap_margin,
    "reseam_stretched_island": reseam_stretched_island,
    "decimate_to_budget": decimate_to_budget,
    "snap_sockets": snap_sockets,
    "fix_weights": fix_weights,
    "merge_coplanar": merge_coplanar,
    "reattach_part": reattach_part,
    "rebake_margin_x2": rebake_margin_x2,
    "relink_textures": relink_textures,
    "rebake_at_budget": rebake_at_budget,
    "rebake_normal": rebake_normal,
    "rebake_periodic_snap": rebake_periodic_snap,
    "reexport": reexport,
}


def apply_actions(ctx: dict, actions: list[dict]) -> list[dict]:
    """Apply a fix_plan.json-style ``actions`` list, dispatching each
    ``table_fix`` action's ``fix_id`` to the matching function above. Actions
    of other types (``param_patch``, ``llm_param_patch``, ``subcomponent_regen``,
    ``full_regen``) are not this module's concern (spec 16.3-16.4) and are
    skipped with a note rather than erroring the whole batch."""
    results = []
    for action in actions:
        if action.get("type") not in (None, "table_fix"):
            results.append({"fix_id": action.get("fix_id"), "skipped": action["type"]})
            continue
        fix_id = action.get("fix_id")
        fn = FIX_TABLE.get(fix_id)
        if fn is None:
            results.append({"fix_id": fix_id, "error": "unknown fix_id"})
            continue
        results.append(fn(ctx, action))
    return results


def main() -> None:
    payload = common.parse_args()
    actions = payload.get("actions", [])
    results = apply_actions(payload, actions)

    # The orchestrator opens the iteration's .blend as Blender's file argument
    # and does not repeat it in the payload; save back to wherever we loaded
    # from unless an explicit path is given.
    blend_path = payload.get("blend_path") or bpy.data.filepath
    if blend_path:
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

    out_path = payload.get("out_path") or (
        Path(payload["asset_dir"]) / "fixes_result.json")
    common.write_result(out_path, {"stage": "fix", "results": results})


if __name__ == "__main__":
    main()
