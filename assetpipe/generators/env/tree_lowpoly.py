"""env/tree_lowpoly -- category ``environment_piece``.

A stylized low-poly tree: a tapered trunk cylinder plus 1-3 stacked
low-facet canopy blobs (icospheres), rng-jittered in radius and offset so a
forest of these does not read as instanced clones.
"""
from __future__ import annotations

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "height_m": {"type": "number", "minimum": 2.0, "maximum": 8.0, "default": 4.5},
        "trunk_radius_m": {"type": "number", "minimum": 0.08, "maximum": 0.35, "default": 0.18},
        "canopy_radius_m": {"type": "number", "minimum": 0.6, "maximum": 2.2, "default": 1.2},
        "canopy_layers": {"type": "integer", "minimum": 1, "maximum": 3, "default": 2},
        "canopy_subdivisions": {"type": "integer", "minimum": 0, "maximum": 1, "default": 1},
        "materials": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
CATEGORY = "environment_piece"
KEYWORDS = ["tree", "foliage", "pine", "canopy", "forest"]

# Real-world scale, environment_piece (spec 9.4): a background/midground tree.
BBOX_RANGE = {"min": [1.0, 1.0, 2.0], "max": [4.0, 4.0, 8.0]}


def generate(params: dict, rng, theme: dict):
    """Build and return the tree's root object.

    Determinism: canopy blob radius/offset jitter is drawn from ``rng``
    only.
    """
    import bmesh

    from assetpipe.generators import common

    height = params["height_m"]
    trunk_r = params["trunk_radius_m"]
    canopy_r = params["canopy_radius_m"]
    trunk_h = height * 0.45

    bm = bmesh.new()

    trunk = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=8,
                                   radius1=trunk_r, radius2=trunk_r * 0.6, depth=trunk_h)
    bmesh.ops.translate(bm, verts=trunk["verts"], vec=(0.0, 0.0, trunk_h / 2.0))

    n_layers = params["canopy_layers"]
    canopy_span = height - trunk_h
    for i in range(n_layers):
        t = i / max(n_layers - 1, 1)
        z = trunk_h + canopy_span * (0.3 + 0.6 * t)
        layer_radius = canopy_r * (1.0 - 0.35 * t) * rng.uniform(0.85, 1.15)
        blob = bmesh.ops.create_icosphere(
            bm, subdivisions=params["canopy_subdivisions"], radius=layer_radius)
        offset = (rng.uniform(-0.1, 0.1) * canopy_r, rng.uniform(-0.1, 0.1) * canopy_r, z)
        bmesh.ops.translate(bm, verts=blob["verts"], vec=offset)

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "tree_lowpoly")
    common.freeze_transform(obj)
    common.decimate_to_budget(obj, budget=10000)
    common.smart_uv_project(obj, texture_resolution=1024)
    return obj
