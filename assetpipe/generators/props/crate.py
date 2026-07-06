"""props/crate -- the spec 9.1 canonical example, category ``prop_small``.

A chamfered box with panel-line grooves and rng-scattered greebles. Schema
matches the spec 9.1 example verbatim (``width_m``, ``height_m``, ``chamfer``,
``panel_lines``, ``greeble_density``, ``materials``).
"""
from __future__ import annotations

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "width_m": {"type": "number", "minimum": 0.3, "maximum": 1.2, "default": 0.6},
        "height_m": {"type": "number", "minimum": 0.3, "maximum": 1.2, "default": 0.6},
        "chamfer": {"type": "number", "minimum": 0.0, "maximum": 0.08, "default": 0.02},
        "panel_lines": {"type": "integer", "minimum": 0, "maximum": 6, "default": 2},
        "greeble_density": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.4},
        "materials": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
CATEGORY = "prop_small"
KEYWORDS = ["crate", "box", "container", "supply"]

# Real-world scale, prop_small (spec 9.4): a crate is 0.3-1.2 m on a side.
BBOX_RANGE = {"min": [0.3, 0.3, 0.3], "max": [1.2, 1.2, 1.2]}


def generate(params: dict, rng, theme: dict):
    """Build and return the crate's root object.

    Deterministic given ``(params, rng)``: the only random draws are which
    side faces receive a greeble and how deep each one is pushed, both from
    ``rng``. Geometry is built directly at final world scale (no post-hoc
    object transform) per the scene-convention discipline in the
    ``blender-procedural-geometry`` skill.
    """
    import bmesh

    from assetpipe.generators import common

    width = params["width_m"]
    height = params["height_m"]
    chamfer = params["chamfer"]

    bm = bmesh.new()
    box = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, verts=box["verts"], vec=(width, width, height))

    # Chamfer, clamped so bevel offset never exceeds local edge spacing.
    max_chamfer = min(width, height) * 0.45
    offset = min(chamfer, max_chamfer)
    if offset > 1e-6:
        bmesh.ops.bevel(bm, geom=list(bm.edges), offset=offset, segments=2,
                         profile=0.7, affect="EDGES")

    # Panel lines: shallow inset grooves ringing the crate at evenly spaced
    # heights on the four side faces.
    n_panels = params["panel_lines"]
    for i in range(n_panels):
        t = (i + 1) / (n_panels + 1)
        z = -height / 2.0 + height * t
        band = [f for f in bm.faces
                if abs(f.normal.z) < 0.5 and abs(f.calc_center_median().z - z) < height * 0.08]
        if band:
            bmesh.ops.inset_region(bm, faces=band, thickness=min(width, height) * 0.02,
                                    depth=-0.003, use_boundary=True)

    # Greebles: rng-scattered inset+push bumps on side faces, count driven
    # by greeble_density.
    # Greeble candidates: real panel faces only. Chamfer bands and panel-line
    # groove walls are millimeter-wide slivers; insetting them produces
    # micro-geometry that folds in the UV charts (the residual S12b overlap
    # observed on real Blender 4.2 traced back to these).
    min_greeble_area = (min(width, height) * 0.12) ** 2
    side_faces = [f for f in bm.faces
                  if abs(f.normal.z) < 0.2 and f.calc_area() > min_greeble_area]
    side_faces.sort(key=lambda f: tuple(round(c, 6) for c in f.calc_center_median()))
    rng.shuffle(side_faces)
    n_greebles = int(round(len(side_faces) * params["greeble_density"] * 0.5))
    for f in side_faces[:n_greebles]:
        size = f.calc_area() ** 0.5
        bmesh.ops.inset_individual(bm, faces=[f], thickness=size * 0.2, depth=0.0)
        # inset_individual returns the RING faces, not the inner cap (verified
        # on real Blender 4.2); the original face remains the cap. Push only
        # the cap's verts: the ring's outer verts are shared with the
        # surrounding surface, and translating them warps neighboring faces
        # (self-intersections + folded UV charts were the observed result).
        depth = rng.uniform(0.002, 0.01)
        normal = f.normal.copy()
        bmesh.ops.translate(bm, verts=list(f.verts),
                             vec=(normal.x * depth, normal.y * depth, normal.z * depth))

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "crate")
    common.freeze_transform(obj)
    common.decimate_to_budget(obj, budget=3000)
    common.smart_uv_project(obj, texture_resolution=1024)
    return obj
