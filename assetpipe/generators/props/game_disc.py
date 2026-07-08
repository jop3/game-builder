"""props/game_disc -- category ``prop_small``.

A tabletop playing disc (Othello/Reversi marker): a short cylinder with both
rim edges beveled so it reads as a rounded, weighty stone piece rather than a
flat token. Single material slot -- the light (Moonstone) vs dark (Obsidian)
identity comes from the request's ``param_overrides.materials`` (which theme
recipe to bake) plus description colour words, not from geometry.

Authored for ``examples/othello`` (fantasy Othello): see its spec.
"""
from __future__ import annotations

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "radius_m": {"type": "number", "minimum": 0.015, "maximum": 0.03, "default": 0.022},
        "height_m": {"type": "number", "minimum": 0.004, "maximum": 0.012, "default": 0.007},
        # Rounded rim: beveled top+bottom edge. Kept < height/2 so the bevels
        # never meet and collapse the side wall.
        "bevel": {"type": "number", "minimum": 0.0, "maximum": 0.003, "default": 0.002},
        "segments": {"type": "integer", "minimum": 16, "maximum": 48, "default": 32},
        # Which theme material recipe(s) to bake (per request); empty -> the
        # stage falls back to the theme's first recipe.
        "materials": {"type": "array", "items": {"type": "string"}},
        # A polished game piece is intentionally near-uniform in albedo -- set
        # true so V1's S16 skips the not-flat variance test (spec 13.3), the
        # same escape lowpoly_stylized uses. The luminance-range bound still
        # applies, so a truly black/blown bake is still caught.
        "flat_color": {"type": "boolean", "default": False},
    },
    "additionalProperties": False,
}
CATEGORY = "prop_small"
KEYWORDS = ["disc", "disk", "marker", "piece", "counter", "token", "stone", "checker"]

# Real-world scale, prop_small (spec 9.4): a hand-held ~4-5 cm playing disc.
BBOX_RANGE = {"min": [0.03, 0.03, 0.004], "max": [0.06, 0.06, 0.014]}


def generate(params: dict, rng, theme: dict):
    """Build and return the disc's root object. Deterministic; ``rng`` is
    accepted (unused) to satisfy the recipe contract uniformly."""
    import bmesh

    from assetpipe.generators import common

    r = params["radius_m"]
    h = params["height_m"]
    segs = params["segments"]
    bevel = params["bevel"]

    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs,
                          radius1=r, radius2=r, depth=h)

    # Round both rim rings (top and bottom circumference edges, at |z| = h/2)
    # so the disc reads as a smooth stone counter, not a stamped-out cylinder.
    if bevel > 1e-6:
        rim = [e for e in bm.edges
               if all(abs(abs(v.co.z) - h / 2.0) < 1e-6 for v in e.verts)]
        if rim:
            bmesh.ops.bevel(bm, geom=rim, offset=bevel, segments=2, profile=0.7,
                            affect="EDGES")

    # Two-tone disc (a real Reversi/Othello counter is one colour on each face,
    # so the rim reads half-and-half from the side and a flip is a true
    # turn-over). When the request supplies two materials, bisect the disc at
    # its equator (z=0, before base_center_origin re-zeros it) and give the top
    # half slot 0, the bottom half slot 1.
    if len(params.get("materials") or []) >= 2:
        bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
                               dist=1e-6, plane_co=(0.0, 0.0, 0.0),
                               plane_no=(0.0, 0.0, 1.0),
                               clear_inner=False, clear_outer=False)
        for f in bm.faces:
            f.material_index = 0 if f.calc_center_median().z >= 0.0 else 1
    else:
        for f in bm.faces:
            f.material_index = 0

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "disc")
    common.freeze_transform(obj)
    common.decimate_to_budget(obj, budget=1500)
    common.smart_uv_project(obj, texture_resolution=512)
    return obj
