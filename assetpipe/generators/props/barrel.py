"""props/barrel -- category ``prop_small``.

A lathed cylinder with a slight barrel-bulge profile and proud iron hoop
rings. Built from a cone primitive with equal top/bottom radii (the
``blender-procedural-geometry`` skill's "cylinders: same op, equal radii"
trick) rather than a bespoke lathe, keeping the recipe bmesh-only.

Two material slots (spec 10.2 "generators may pick per-slot materials"):
slot 0 = stave wood (body + caps), slot 1 = iron hoops. With no explicit
``materials`` param the orchestrator resolves :data:`SLOT_MATERIALS`
keywords against the theme's material list (see
``stages.resolve_slot_materials``), so the same recipe gets aged wood +
forged iron in fantasy_medieval and collapses gracefully to a single
material in themes with no wood/metal split.
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
        # Entries are recipe id strings or slot-scoped {"recipe", "params"}
        # objects (docs/TEXTURE_WAVE.md item 6). No default: SLOT_MATERIALS
        # resolves per-theme at stage time.
        "materials": {"type": "array",
                      "items": {"anyOf": [
                          {"type": "string"},
                          {"type": "object",
                           "properties": {"recipe": {"type": "string"},
                                          "params": {"type": "object"}},
                           "required": ["recipe"],
                           "additionalProperties": False},
                      ]}},
    },
    "additionalProperties": False,
}
CATEGORY = "prop_small"
KEYWORDS = ["barrel", "drum", "container", "cask"]

# Per-slot theme-material keyword preferences, most-specific first
# (resolved by stages.resolve_slot_materials when the request/params carry
# no explicit ``materials`` list).
SLOT_MATERIALS = (
    ("wood", "plank", "timber", "crate"),        # slot 0: stave body
    ("iron", "trim", "metal", "hull"),           # slot 1: hoops
)

# Real-world scale, prop_small (spec 9.4): ~0.5-0.8 m diameter, ~0.6-1.0 m tall.
BBOX_RANGE = {"min": [0.5, 0.5, 0.6], "max": [0.8, 0.8, 1.0]}

SLOT_WOOD, SLOT_HOOPS = 0, 1


def generate(params: dict, rng, theme: dict):
    """Build and return the barrel's root object. The only rng draws are the
    per-hoop height jitters, keeping the silhouette from reading as a
    perfectly lathed CG primitive.
    """
    import bmesh

    from assetpipe.generators import common

    radius = params["radius_m"]
    height = params["height_m"]
    segs = params["segments"]

    def bulged_radius(t: float) -> float:
        """Body radius at height fraction ``t`` (0 bottom cap, 1 top cap)."""
        profile = 1.0 - (2.0 * t - 1.0) ** 2
        return radius + params["bulge"] * radius * profile

    bm = bmesh.new()
    cyl = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs,
                                 radius1=radius, radius2=radius, depth=height)
    verts = cyl["verts"]

    # Barrel bulge: push mid-height ring verts outward, taper to nothing at
    # the caps (a simple parabolic profile keyed on local z).
    for v in verts:
        t = (v.co.z + height / 2.0) / height  # 0 at bottom cap, 1 at top cap
        if v.co.x == 0 and v.co.y == 0:
            continue
        r = (v.co.x ** 2 + v.co.y ** 2) ** 0.5
        scale = (r + (bulged_radius(t) - radius)) / r
        v.co.x *= scale
        v.co.y *= scale

    for f in bm.faces:
        f.material_index = SLOT_WOOD

    # Iron hoop rings: proud manifold squat cylinders that strictly overlap
    # the bulged body (never coincident planes -- remove_doubles welds those
    # into non-manifold seams). Evenly spread with a small rng jitter, iron
    # material slot; the old shallow insets read as scratches, not hoops.
    n = params["hoop_bands"]
    for i in range(n):
        t = (i + 0.5) / n + rng.uniform(-0.04, 0.04)
        t = min(0.92, max(0.08, t))
        r_here = bulged_radius(t) + 0.010
        hoop = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=True, segments=segs,
                                     radius1=r_here, radius2=r_here,
                                     depth=height * 0.07)
        z = (t - 0.5) * height
        bmesh.ops.translate(bm, verts=hoop["verts"], vec=(0.0, 0.0, z))
        vset = set(hoop["verts"])
        for v in hoop["verts"]:
            for f in v.link_faces:
                if all(fv in vset for fv in f.verts):
                    f.material_index = SLOT_HOOPS

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "barrel")
    common.freeze_transform(obj)
    common.decimate_to_budget(obj, budget=3000)
    common.smart_uv_project(obj, texture_resolution=1024)
    return obj
