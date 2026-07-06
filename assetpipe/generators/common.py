"""Shared bpy/bmesh helpers for generator recipes (spec 9.4-9.5).

Lives directly in ``assetpipe/generators/`` rather than in a category
subpackage, so :meth:`assetpipe.generators.registry.Registry.discover` never
treats it as a recipe module — discovery only descends into immediate
subpackages of ``assetpipe.generators`` (see ``registry.py``'s docstring),
and a plain module sitting next to ``registry.py`` is invisible to it, same
as ``registry.py`` itself.

Like every recipe module (see ``assetpipe/generators/__init__.py``), this
file imports Blender-only modules (``bpy``, ``bmesh``, ``mathutils``)
*inside* each function body only, so it stays importable in plain CPython
and is exercised by the recipe unit tests without a Blender process.
"""
from __future__ import annotations


def finishing_pass(bm) -> None:
    """Mesh finishing pass, spec 9.5 (everything except decimate-to-budget,
    which needs a real object/modifier stack — see :func:`decimate_to_budget`):
    remove doubles -> dissolve degenerate slivers -> recalc outward normals ->
    triangulate (beauty). Order matters (triangulating first would leave
    needle triangles behind after remove_doubles).
    """
    import bmesh

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bmesh.ops.triangulate(bm, faces=bm.faces, quad_method="BEAUTY", ngon_method="BEAUTY")


def base_center_origin(bm) -> None:
    """Translate geometry (not the object) so the XY centroid sits at
    x=y=0 and the min-Z plane sits at z=0 -- the props/kit/env origin
    convention (spec 9.4). Characters use :func:`feet_origin` instead.
    """
    import bmesh

    verts = list(bm.verts)
    if not verts:
        return
    xs = [v.co.x for v in verts]
    ys = [v.co.y for v in verts]
    zs = [v.co.z for v in verts]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    zmin = min(zs)
    bmesh.ops.translate(bm, verts=verts, vec=(-cx, -cy, -zmin))


def feet_origin(bm) -> None:
    """Translate geometry so the XY centroid sits at x=y=0 and the min-Z
    plane (the feet) sits at z=0 -- the character origin convention (spec
    9.4). Identical math to :func:`base_center_origin`; kept as a separate
    name for readability at call sites.
    """
    base_center_origin(bm)


def emit_object(bm, name: str):
    """``bm.to_mesh()`` into a new object linked into the ``EXPORT``
    collection (spec 9.4), creating the collection on first use. Frees
    ``bm`` (bmesh handles are not reusable after this call).
    """
    import bpy

    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection = bpy.data.collections.get("EXPORT")
    if collection is None:
        collection = bpy.data.collections.new("EXPORT")
        bpy.context.scene.collection.children.link(collection)
    collection.objects.link(obj)
    return obj


def freeze_transform(obj) -> None:
    """Enforce the identity-transform convention (spec 9.4): geometry is
    built at final world scale/location inside bmesh so there is nothing to
    bake down; this just guards against a stray non-identity basis.
    """
    obj.scale = (1.0, 1.0, 1.0)
    obj.rotation_euler = (0.0, 0.0, 0.0)


def add_socket(root_obj, name: str, location) -> None:
    """Create a ``SOCKET_<dir>_<i>`` empty parented to ``root_obj`` at exact
    0.5 m grid coordinates (spec 9.4, modular kit pieces).
    """
    import bpy

    empty = bpy.data.objects.new(name, None)
    empty.empty_display_size = 0.05
    empty.parent = root_obj
    empty.location = location
    collection = bpy.data.collections.get("EXPORT")
    if collection is None:
        collection = bpy.context.collection
    collection.objects.link(empty)
    return empty


def triangle_count(obj) -> int:
    obj.data.calc_loop_triangles()
    return len(obj.data.loop_triangles)


def decimate_to_budget(obj, budget: int, max_steps: int = 5) -> int:
    """Decimate(collapse) in ratio-0.85 steps until <= ``budget`` triangles
    or ``max_steps`` is reached (spec 9.5). Applies each modifier via the
    evaluated depsgraph (headless-safe; no ``bpy.ops.object.transform_apply``
    context dance). Returns the final triangle count -- callers in the
    orchestrator raise ``OVER_BUDGET_UNFIXABLE`` if it is still over budget.
    """
    import bpy

    steps = 0
    while triangle_count(obj) > budget and steps < max_steps:
        mod = obj.modifiers.new(f"Decimate{steps}", "DECIMATE")
        mod.ratio = 0.85
        mod.use_collapse_triangulate = True
        deps = bpy.context.evaluated_depsgraph_get()
        obj.data = bpy.data.meshes.new_from_object(obj.evaluated_get(deps))
        obj.modifiers.clear()
        steps += 1
    return triangle_count(obj)


def apply_modifiers(obj) -> None:
    """Apply every modifier on ``obj`` via the evaluated depsgraph
    (headless-safe, spec 9.5 "apply modifiers" step)."""
    import bpy

    deps = bpy.context.evaluated_depsgraph_get()
    obj.data = bpy.data.meshes.new_from_object(obj.evaluated_get(deps))
    obj.modifiers.clear()


def smart_uv_project(obj, texture_resolution: int = 1024) -> None:
    """Fallback UV unwrap (spec 9.6): Smart UV Project at a fixed angle
    limit with an island margin sized for a 4-texel bake bleed at
    ``texture_resolution``. Recipe-placed seams are preferred where the
    recipe knows tiling/feature edges; call this only when it does not.
    """
    import math

    import bpy

    # 2x the 4-texel S12e requirement: Blender's island_margin is nominal
    # (actual gaps come out smaller depending on island scale), and the S12e
    # dilation check needs a strictly-greater-than-4-texel gap, so aiming at
    # exactly 4 fails all over (observed against real Blender 4.2).
    margin = 8.0 / texture_resolution
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    # 35 deg, not the folkloric 66: at 66 the charts swallow bevel-corner
    # fans whose facets differ by ~60 deg, and projecting those onto one
    # plane folds them over each other -- real S12b overlap, measured on
    # real Blender 4.2 (66/55 deg: 1.0%; 35 deg: 0.49%, passing).
    bpy.ops.uv.smart_project(angle_limit=math.radians(35), island_margin=margin)
    bpy.ops.object.mode_set(mode="OBJECT")
