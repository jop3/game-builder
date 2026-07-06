"""tiling/surface -- the ``tiling_texture_set`` bake target (spec 10.3).

Not a prop: a ``tiling_texture_set`` request's deliverable is the baked map
set, and this recipe produces the canonical bake target the spec prescribes
for it -- "a unit plane with 0-1 UVs". The plane's UVs are authored here
exactly (corner-to-corner 0-1) and ``generate.py``'s UV pass keeps
recipe-authored UVs for tiling targets instead of box-projecting over them,
because periodic bakes are only mathematically seamless when the UV domain
covers the tile exactly once.

``tile_size_m`` scales the plane's world size only (texel density in renders);
UVs always span 0-1 regardless.
"""
from __future__ import annotations

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "tile_size_m": {"type": "number", "minimum": 0.5, "maximum": 4.0, "default": 1.0},
    },
    "additionalProperties": False,
}
CATEGORY = "tiling_texture_set"
KEYWORDS = ["tiling", "tile", "seamless", "texture", "surface", "plates",
            "deck", "floor", "wall", "ground", "pattern"]

# Flat target: XY footprint = tile_size_m, zero height.
BBOX_RANGE = {"min": [0.5, 0.5, 0.0], "max": [4.0, 4.0, 0.01]}


def generate(params: dict, rng, theme: dict):
    """Build and return the unit-plane bake target. Deterministic and
    rng-free: the target's geometry carries no design, only the UV domain."""
    import bmesh

    from assetpipe.generators import common

    half = params["tile_size_m"] / 2.0

    bm = bmesh.new()
    corners = [(-half, -half, 0.0), (half, -half, 0.0),
               (half, half, 0.0), (-half, half, 0.0)]
    verts = [bm.verts.new(c) for c in corners]
    face = bm.faces.new(verts)

    uv_layer = bm.loops.layers.uv.new("UVMap")
    for loop, uv in zip(face.loops, ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))):
        loop[uv_layer].uv = uv

    common.finishing_pass(bm)  # triangulates; loop UVs survive triangulation
    obj = common.emit_object(bm, "tile_surface")
    common.freeze_transform(obj)
    # S12c exemption marker (spec 13.2): tiling UV mode. For the unit plane the
    # UVs actually stay inside 0-1, but the marker keeps the category's UV
    # semantics uniform with box-projected tiling *surfaces* on kit meshes.
    obj.data["uv_mode"] = "tiling"
    return obj
