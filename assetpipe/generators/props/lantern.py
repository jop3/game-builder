"""props/lantern -- category ``prop_small``.

A small hanging/standing lantern: base, a glass shell wrapped by REAL
vertical cage bars, a peaked cap, and a finial knob. Two material slots
(spec 10.2): slot 0 = forged metal (base, cap, knob, cage bars), slot 1 =
glowing glass. The bars being real geometry means the glass slot's AO-based
bar painting (fantasy_window_glow) darkens exactly the texels they cover --
the same trick as the house windows' mullion cross.

With no explicit ``materials`` param the orchestrator resolves
:data:`SLOT_MATERIALS` keywords against the theme's material list (see
``stages.resolve_slot_materials``): fantasy_medieval yields iron trim +
window glow; themes without a metal/glow split collapse to one material.
"""
from __future__ import annotations

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "width_m": {"type": "number", "minimum": 0.15, "maximum": 0.35, "default": 0.22},
        "height_m": {"type": "number", "minimum": 0.25, "maximum": 0.6, "default": 0.4},
        "cage_bars": {"type": "integer", "minimum": 3, "maximum": 8, "default": 4},
        "glow_strength": {"type": "number", "minimum": 0.0, "maximum": 5.0, "default": 2.0},
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
KEYWORDS = ["lantern", "lamp", "light", "torch"]

# Per-slot theme-material keyword preferences, most-specific first
# (resolved by stages.resolve_slot_materials when the request/params carry
# no explicit ``materials`` list).
SLOT_MATERIALS = (
    ("iron", "trim", "metal", "hull"),           # slot 0: body + cage
    ("glow", "window", "glass", "emissive"),     # slot 1: glass shell
)

# Real-world scale, prop_small (spec 9.4): small hand/post prop.
BBOX_RANGE = {"min": [0.15, 0.15, 0.25], "max": [0.35, 0.35, 0.6]}

SLOT_METAL, SLOT_GLASS = 0, 1


def generate(params: dict, rng, theme: dict):
    """Build and return the lantern's root object.

    Deterministic given ``(params, rng)``: rng only perturbs each cage bar's
    thickness slightly so the silhouette does not read as perfectly
    array-modified.
    """
    import math

    import bmesh
    from mathutils import Matrix

    from assetpipe.generators import common

    width = params["width_m"]
    height = params["height_m"]

    base_h = height * 0.15
    cage_h = height * 0.55
    cap_h = height * 0.3

    bm = bmesh.new()

    def _assign(part_verts, slot: int) -> None:
        vset = set(part_verts)
        for v in part_verts:
            for f in v.link_faces:
                if all(fv in vset for fv in f.verts):
                    f.material_index = slot

    # Base (squat box).
    base = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, verts=base["verts"], vec=(width, width, base_h))
    bmesh.ops.translate(bm, verts=base["verts"], vec=(0.0, 0.0, base_h / 2.0))
    _assign(base["verts"], SLOT_METAL)

    # Glass shell: a slim cylinder standing on the base -- the glow slot.
    glass_r = width * 0.35
    cage = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False,
                                 segments=max(8, params["cage_bars"] * 2),
                                 radius1=glass_r, radius2=glass_r, depth=cage_h)
    bmesh.ops.translate(bm, verts=cage["verts"], vec=(0.0, 0.0, base_h + cage_h / 2.0))
    _assign(cage["verts"], SLOT_GLASS)

    # REAL cage bars around the glass (accessories pass): thin vertical boxes
    # whose inner faces overlap into the shell (strict overlap, S9-warn like
    # the house windows). Their tight AO paints dark bars into the glass
    # slot's emissive, so the cage reads even at LOD distance.
    bar_r = glass_r + 0.004
    for i in range(params["cage_bars"]):
        ang = (i + 0.5) / params["cage_bars"] * 2.0 * math.pi
        thick = width * 0.10 * (1.0 + rng.uniform(-0.12, 0.12))
        bar = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, verts=bar["verts"],
                        vec=(width * 0.09, thick, cage_h + 0.012))
        bmesh.ops.rotate(bm, verts=bar["verts"], cent=(0, 0, 0),
                         matrix=Matrix.Rotation(ang, 3, 'Z'))
        bmesh.ops.translate(bm, verts=bar["verts"],
                            vec=(bar_r * math.cos(ang), bar_r * math.sin(ang),
                                 base_h + cage_h / 2.0))
        _assign(bar["verts"], SLOT_METAL)

    # Peaked cap: cone tapering to a point.
    cap = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=True,
                                segments=max(8, params["cage_bars"] * 2),
                                radius1=width * 0.45, radius2=0.0, depth=cap_h)
    bmesh.ops.translate(bm, verts=cap["verts"], vec=(0.0, 0.0, base_h + cage_h + cap_h / 2.0))
    _assign(cap["verts"], SLOT_METAL)

    # Top finial knob (solid, capped): a wire circle here (the earlier
    # "ring handle") is non-manifold by construction -- faceless edges fail
    # S1/S4 on every run (verified against real Blender 4.2).
    knob = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=True, segments=8,
                                  radius1=width * 0.1, radius2=width * 0.04,
                                  depth=width * 0.12)
    bmesh.ops.translate(bm, verts=knob["verts"],
                         vec=(0.0, 0.0, base_h + cage_h + cap_h + width * 0.06))
    _assign(knob["verts"], SLOT_METAL)

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "lantern")
    common.freeze_transform(obj)
    common.decimate_to_budget(obj, budget=1500)
    common.smart_uv_project(obj, texture_resolution=1024)
    return obj
