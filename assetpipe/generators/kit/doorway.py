"""kit/doorway -- category ``modular_kit_piece``.

A modular wall segment with a rectangular door opening cut through it:
exactly 3 m x 3 m footprint per spec 9.4, matching ``kit/wall`` so the two
pieces are interchangeable in the same grid cell. Sockets on the 0.5 m grid
around the outer perimeter, same convention as ``kit/wall``.
"""
from __future__ import annotations

WIDTH_M = 3.0
HEIGHT_M = 3.0
GRID = 0.5

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "thickness_m": {"type": "number", "minimum": 0.15, "maximum": 0.4, "default": 0.25},
        "door_width_m": {"type": "number", "minimum": 0.8, "maximum": 1.6, "default": 1.1},
        "door_height_m": {"type": "number", "minimum": 1.8, "maximum": 2.4, "default": 2.1},
        "frame_bevel": {"type": "number", "minimum": 0.0, "maximum": 0.03, "default": 0.01},
        "materials": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
CATEGORY = "modular_kit_piece"
KEYWORDS = ["doorway", "door", "arch", "opening", "kit"]

# Real-world scale, modular_kit_piece (spec 9.4): exact 3 m x 3 m footprint,
# same cell size as kit/wall so the two are interchangeable.
BBOX_RANGE = {"min": [WIDTH_M, 0.15, HEIGHT_M], "max": [WIDTH_M, 0.4, HEIGHT_M]}


def _place_edge_sockets(root_obj, thickness):
    from assetpipe.generators import common

    n = int(round(WIDTH_M / GRID))
    for label, coord_axis, fixed in (
        ("BOTTOM", "x", 0.0), ("TOP", "x", HEIGHT_M),
    ):
        for i in range(n + 1):
            x = -WIDTH_M / 2.0 + i * GRID
            common.add_socket(root_obj, f"SOCKET_{label}_{i}", (x, thickness / 2.0, fixed))
    for label, fixed_x in (("LEFT", -WIDTH_M / 2.0), ("RIGHT", WIDTH_M / 2.0)):
        for i in range(n + 1):
            z = i * GRID
            common.add_socket(root_obj, f"SOCKET_{label}_{i}", (fixed_x, thickness / 2.0, z))


def generate(params: dict, rng, theme: dict):
    """Build and return the doorway panel's root object.

    Deterministic given ``(params, rng)``: the door opening is boolean-cut
    from the wall slab at exact parametric dimensions -- no randomness
    needed for a modular kit piece's silhouette. bmesh boolean is the one
    place the ``blender-procedural-geometry`` skill recommends avoiding
    where possible; here it is unavoidable (a true hole through the slab)
    so the finishing pass afterwards cleans up any slivers it leaves.
    """
    import bmesh

    from assetpipe.generators import common

    thickness = params["thickness_m"]
    door_w = params["door_width_m"]
    door_h = params["door_height_m"]

    bm = bmesh.new()
    wall = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, verts=wall["verts"], vec=(WIDTH_M, thickness, HEIGHT_M))

    # Door opening: an oversized box (deeper than the wall on Y) booleaned
    # out, centered on X, sitting on the floor (min Z of the wall).
    cutter = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, verts=cutter["verts"], vec=(door_w, thickness * 3.0, door_h))
    bmesh.ops.translate(bm, verts=cutter["verts"], vec=(0.0, 0.0, -HEIGHT_M / 2.0 + door_h / 2.0))

    wall_faces = [f for f in bm.faces if f not in set(cutter["faces"])]
    bmesh.ops.boolean(bm, geom=wall_faces + cutter["faces"], operation="DIFFERENCE")

    bevel = params["frame_bevel"]
    if bevel > 1e-6:
        frame_edges = [
            e for e in bm.edges
            if e.is_boundary and 0.0 < e.calc_length() < max(door_w, door_h) * 1.5
        ]
        if frame_edges:
            bmesh.ops.bevel(bm, geom=frame_edges, offset=bevel, segments=2, profile=0.7,
                             affect="EDGES")

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "doorway")
    common.freeze_transform(obj)
    _place_edge_sockets(obj, thickness)
    common.decimate_to_budget(obj, budget=5000)
    common.smart_uv_project(obj, texture_resolution=2048)
    return obj
