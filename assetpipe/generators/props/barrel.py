"""props/barrel -- category ``prop_small``.

A lathed cylinder with a slight barrel-bulge profile and rng-jittered hoop
bands. Built from a cone primitive with equal top/bottom radii (the
``blender-procedural-geometry`` skill's "cylinders: same op, equal radii"
trick) rather than a bespoke lathe, keeping the recipe bmesh-only.
"""
from __future__ import annotations

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "radius_m": {"type": "number", "minimum": 0.25, "maximum": 0.4, "default": 0.3},
        "height_m": {"type": "number", "minimum": 0.6, "maximum": 1.0, "default": 0.85},
        "bulge": {"type": "number", "minimum": 0.0, "maximum": 0.25, "default": 0.1},
        "hoop_bands": {"type": "integer", "minimum": 0, "maximum": 5, "default": 3},
        "segments": {"type": "integer", "minimum": 8, "maximum": 24, "default": 16},
        "materials": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
CATEGORY = "prop_small"
KEYWORDS = ["barrel", "drum", "container", "cask"]

# Real-world scale, prop_small (spec 9.4): ~0.5-0.8 m diameter, ~0.6-1.0 m tall.
BBOX_RANGE = {"min": [0.5, 0.5, 0.6], "max": [0.8, 0.8, 1.0]}


def generate(params: dict, rng, theme: dict):
    """Build and return the barrel's root object. The only rng draw is a
    small per-hoop-band radial jitter, keeping the silhouette from reading
    as a perfectly lathed CG primitive.
    """
    import bmesh

    from assetpipe.generators import common

    radius = params["radius_m"]
    height = params["height_m"]
    segs = params["segments"]

    bm = bmesh.new()
    cyl = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs,
                                 radius1=radius, radius2=radius, depth=height)
    verts = cyl["verts"]

    # Barrel bulge: push mid-height ring verts outward, taper to nothing at
    # the caps (a simple parabolic profile keyed on local z).
    bulge = params["bulge"] * radius
    for v in verts:
        t = (v.co.z + height / 2.0) / height  # 0 at bottom cap, 1 at top cap
        profile = 1.0 - (2.0 * t - 1.0) ** 2  # 0 at caps, 1 at equator
        if profile <= 0.0 or (v.co.x == 0 and v.co.y == 0):
            continue
        r = (v.co.x ** 2 + v.co.y ** 2) ** 0.5
        scale = (r + bulge * profile) / r
        v.co.x *= scale
        v.co.y *= scale

    # Hoop bands: shallow inset rings at rng-jittered heights.
    for _ in range(params["hoop_bands"]):
        z = rng.uniform(-height * 0.35, height * 0.35)
        band = [f for f in bm.faces
                if abs(f.normal.z) < 0.3 and abs(f.calc_center_median().z - z) < height * 0.03]
        if band:
            bmesh.ops.inset_region(bm, faces=band, thickness=radius * 0.03, depth=0.004,
                                    use_boundary=True)

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "barrel")
    common.freeze_transform(obj)
    common.decimate_to_budget(obj, budget=3000)
    common.smart_uv_project(obj, texture_resolution=1024)
    return obj
