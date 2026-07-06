"""kit/floor -- category ``modular_kit_piece``.

A modular floor tile: exactly 3 m x 3 m footprint per spec 9.4 (only
``thickness_m`` varies), with perimeter ``SOCKET_<dir>_<i>`` empties on the
0.5 m grid so tiles snap edge-to-edge into a seamless grid.
"""
from __future__ import annotations

WIDTH_M = 3.0
DEPTH_M = 3.0
GRID = 0.5

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "thickness_m": {"type": "number", "minimum": 0.1, "maximum": 0.3, "default": 0.2},
        "bevel_edge": {"type": "number", "minimum": 0.0, "maximum": 0.02, "default": 0.0},
        "materials": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
CATEGORY = "modular_kit_piece"
KEYWORDS = ["floor", "tile", "ground", "kit"]

# Real-world scale, modular_kit_piece (spec 9.4): exact 3 m x 3 m footprint;
# thickness is the only free axis.
BBOX_RANGE = {"min": [WIDTH_M, DEPTH_M, 0.1], "max": [WIDTH_M, DEPTH_M, 0.3]}


def _place_edge_sockets(root_obj, thickness):
    from assetpipe.generators import common

    n = int(round(WIDTH_M / GRID))
    for label, axis_fixed, sign in (
        ("NORTH", "y", 1), ("SOUTH", "y", -1), ("EAST", "x", 1), ("WEST", "x", -1),
    ):
        for i in range(n + 1):
            coord = -WIDTH_M / 2.0 + i * GRID
            # z=0 (the tile's base/snapping plane): a thickness-derived z is
            # off the 0.5 m grid for any non-grid thickness (S10).
            if axis_fixed == "y":
                loc = (coord, sign * DEPTH_M / 2.0, 0.0)
            else:
                loc = (sign * WIDTH_M / 2.0, coord, 0.0)
            common.add_socket(root_obj, f"SOCKET_{label}_{i}", loc)


def generate(params: dict, rng, theme: dict):
    """Build and return the floor tile's root object with perimeter
    sockets. No randomness is needed for a flat modular tile; ``rng`` is
    accepted (and unused) to satisfy the recipe contract uniformly.
    """
    import bmesh

    from assetpipe.generators import common

    thickness = params["thickness_m"]

    bm = bmesh.new()
    box = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, verts=box["verts"], vec=(WIDTH_M, DEPTH_M, thickness))

    bevel = params["bevel_edge"]
    if bevel > 1e-6:
        top_edges = [e for e in bm.edges
                     if all(abs(v.co.z - thickness / 2.0) < 1e-6 for v in e.verts)]
        if top_edges:
            bmesh.ops.bevel(bm, geom=top_edges, offset=bevel, segments=2, profile=0.7,
                             affect="EDGES")

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "floor")
    common.freeze_transform(obj)
    _place_edge_sockets(obj, thickness)
    common.decimate_to_budget(obj, budget=2500)

    # Tiling surfaces get box-projection UVs at fixed world texel density,
    # not Smart UV Project (spec 9.6); mark the mesh so V1 skips the 0-1
    # bounds check for it.
    obj.data["uv_mode"] = "tiling"
    return obj
