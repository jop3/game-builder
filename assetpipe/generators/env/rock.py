"""env/rock -- category ``environment_piece``.

An irregular boulder: an icosphere with every vertex pushed along its own
normal by an rng-seeded displacement, so no two rocks from the same recipe
are identical while staying fully deterministic given ``(params, rng)``.
"""
from __future__ import annotations

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "radius_m": {"type": "number", "minimum": 0.3, "maximum": 1.5, "default": 0.6},
        "flatten": {"type": "number", "minimum": 0.0, "maximum": 0.6, "default": 0.25},
        "roughness": {"type": "number", "minimum": 0.0, "maximum": 0.5, "default": 0.2},
        "subdivisions": {"type": "integer", "minimum": 1, "maximum": 3, "default": 2},
        "materials": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
CATEGORY = "environment_piece"
KEYWORDS = ["rock", "boulder", "stone", "cliff"]

# Real-world scale, environment_piece (spec 9.4): a scatterable boulder.
BBOX_RANGE = {"min": [0.3, 0.3, 0.2], "max": [2.5, 2.5, 1.8]}


def generate(params: dict, rng, theme: dict):
    """Build and return the rock's root object.

    Determinism: every vertex displacement is drawn from ``rng`` (never
    ``mathutils.noise`` or module-level ``random``), so the same
    ``(params, rng-seed)`` pair always yields the same rock.
    """
    import bmesh

    from assetpipe.generators import common

    radius = params["radius_m"]

    bm = bmesh.new()
    sphere = bmesh.ops.create_icosphere(bm, subdivisions=params["subdivisions"], radius=radius)
    verts = sphere["verts"]

    roughness = params["roughness"] * radius
    for v in verts:
        n = v.co.normalized() if v.co.length > 1e-9 else v.co
        displacement = rng.uniform(-roughness, roughness)
        v.co += n * displacement

    # Flatten toward the ground so it sits like a settled rock rather than
    # a floating sphere: squash the lower hemisphere less than the upper.
    flatten = params["flatten"]
    for v in verts:
        v.co.z *= (1.0 - flatten) if v.co.z < 0 else (1.0 - flatten * 0.4)

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "rock")
    common.freeze_transform(obj)
    common.decimate_to_budget(obj, budget=10000)
    common.smart_uv_project(obj, texture_resolution=1024)
    return obj
