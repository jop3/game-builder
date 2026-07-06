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
            common.add_socket(root_obj, f"SOCKET_{label}_{i}", (x, 0.0, fixed))
    for label, fixed_x in (("LEFT", -WIDTH_M / 2.0), ("RIGHT", WIDTH_M / 2.0)):
        for i in range(n + 1):
            z = i * GRID
            common.add_socket(root_obj, f"SOCKET_{label}_{i}", (fixed_x, 0.0, z))


def generate(params: dict, rng, theme: dict):
    """Build and return the doorway panel's root object.

    Deterministic given ``(params, rng)``: no randomness is needed for a
    modular kit piece's silhouette. The opening is NOT boolean-cut: bmesh has
    no boolean operator at all (``bmesh.ops.boolean`` does not exist --
    verified on real Blender 4.2; ``bmesh.ops.create_cube`` also returns only
    ``verts``, so the original cut code could never run). Instead the panel
    is a single notched profile polygon extruded through the wall thickness,
    which is manifold by construction and leaves no boolean slivers.
    """
    import bmesh

    from assetpipe.generators import common

    thickness = params["thickness_m"]
    door_w = params["door_width_m"]
    door_h = params["door_height_m"]

    half_w = WIDTH_M / 2.0
    half_t = thickness / 2.0
    half_dw = door_w / 2.0

    # Cross-section in the XZ plane: outer rectangle with a door notch cut
    # from the floor line; wound counter-clockwise, z from 0 (floor) to top.
    profile = [(-half_w, 0.0), (-half_dw, 0.0), (-half_dw, door_h),
               (half_dw, door_h), (half_dw, 0.0), (half_w, 0.0),
               (half_w, HEIGHT_M), (-half_w, HEIGHT_M)]

    bm = bmesh.new()
    verts = [bm.verts.new((x, -half_t, z)) for x, z in profile]
    face = bm.faces.new(verts)
    ext = bmesh.ops.extrude_face_region(bm, geom=[face])
    ext_verts = [el for el in ext["geom"] if isinstance(el, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=ext_verts, vec=(0.0, thickness, 0.0))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    bevel = params["frame_bevel"]
    if bevel > 1e-6:
        eps = 1e-5

        def on_frame(v) -> bool:
            return (abs(abs(v.co.x) - half_dw) < eps and v.co.z <= door_h + eps) \
                or (abs(v.co.z - door_h) < eps and abs(v.co.x) <= half_dw + eps)

        frame_edges = [e for e in bm.edges if all(on_frame(v) for v in e.verts)]
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
