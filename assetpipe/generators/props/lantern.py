"""props/lantern -- category ``prop_small``.

A small hanging/standing lantern: base, a glass-cage body, a peaked cap, and
a top ring handle. The cage interior faces are tagged for an emissive
material slot (``materials[-1]`` by convention, matched at material-assign
time -- the recipe only builds geometry and slot indices).
"""
from __future__ import annotations

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "width_m": {"type": "number", "minimum": 0.15, "maximum": 0.35, "default": 0.22},
        "height_m": {"type": "number", "minimum": 0.25, "maximum": 0.6, "default": 0.4},
        "cage_bars": {"type": "integer", "minimum": 3, "maximum": 8, "default": 4},
        "glow_strength": {"type": "number", "minimum": 0.0, "maximum": 5.0, "default": 2.0},
        "materials": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
CATEGORY = "prop_small"
KEYWORDS = ["lantern", "lamp", "light", "torch"]

# Real-world scale, prop_small (spec 9.4): small hand/post prop.
BBOX_RANGE = {"min": [0.15, 0.15, 0.25], "max": [0.35, 0.35, 0.6]}


def generate(params: dict, rng, theme: dict):
    """Build and return the lantern's root object.

    Deterministic given ``(params, rng)``: rng only perturbs each cage bar's
    thickness slightly so the silhouette does not read as perfectly
    array-modified.
    """
    import bmesh

    from assetpipe.generators import common

    width = params["width_m"]
    height = params["height_m"]

    base_h = height * 0.15
    cage_h = height * 0.55
    cap_h = height * 0.3

    bm = bmesh.new()

    # Base (squat box).
    base = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, verts=base["verts"], vec=(width, width, base_h))
    bmesh.ops.translate(bm, verts=base["verts"], vec=(0.0, 0.0, base_h / 2.0))

    # Cage: a slim cylinder shell standing on the base -- the "glass" faces
    # get the emissive/glass material slot at assign time.
    cage = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=params["cage_bars"] * 2,
                                  radius1=width * 0.35, radius2=width * 0.35, depth=cage_h)
    bmesh.ops.translate(bm, verts=cage["verts"], vec=(0.0, 0.0, base_h + cage_h / 2.0))

    # Peaked cap: cone tapering to a point.
    cap = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=True, segments=params["cage_bars"] * 2,
                                 radius1=width * 0.45, radius2=0.0, depth=cap_h)
    bmesh.ops.translate(bm, verts=cap["verts"], vec=(0.0, 0.0, base_h + cage_h + cap_h / 2.0))

    # Top finial knob (solid, capped): a wire circle here (the earlier
    # "ring handle") is non-manifold by construction -- faceless edges fail
    # S1/S4 on every run (verified against real Blender 4.2).
    knob = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=True, segments=8,
                                  radius1=width * 0.1, radius2=width * 0.04,
                                  depth=width * 0.12)
    bmesh.ops.translate(bm, verts=knob["verts"],
                         vec=(0.0, 0.0, base_h + cage_h + cap_h + width * 0.06))

    # rng jitter: slight per-cage-bar height wobble on the topmost cage ring
    # so the cage does not read as a perfect array-modifier repeat.
    top_ring_verts = [v for v in cage["verts"] if v.co.z > (base_h + cage_h) * 0.4]
    for v in top_ring_verts:
        v.co.z += rng.uniform(-0.004, 0.004)

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "lantern")
    common.freeze_transform(obj)
    common.decimate_to_budget(obj, budget=1500)
    common.smart_uv_project(obj, texture_resolution=1024)
    return obj
