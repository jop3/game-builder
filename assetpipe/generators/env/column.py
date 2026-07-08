"""env/column -- category ``environment_piece``.

A Greek marble column: a square plinth + round base torus, a tall fluted shaft
with a gentle entasis taper, and a Doric capital (round echinus + square
abacus). The shaft is a hand-built lathe of stacked rings whose radius is
modulated per angle so the flutes are real concave grooves meeting at ridges
(not a faceted prism) -- the "build rings, select by query" idiom from the
``blender-procedural-geometry`` skill. Every part is an individually watertight
solid; they overlap rather than share verts (S9 self-intersection is
warn-by-design, coincident planes are what break manifoldness).

One material slot (marble). No rng draws beyond an optional lean so the column
reads as carved stone, not a CG primitive.
"""
from __future__ import annotations

import math

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "height_m": {"type": "number", "minimum": 2.2, "maximum": 3.8, "default": 3.0},
        "radius_m": {"type": "number", "minimum": 0.22, "maximum": 0.4, "default": 0.3},
        "flutes": {"type": "integer", "minimum": 12, "maximum": 24, "default": 20},
        "flute_depth": {"type": "number", "minimum": 0.02, "maximum": 0.12, "default": 0.06},
        "taper": {"type": "number", "minimum": 0.7, "maximum": 1.0, "default": 0.84},
        "rings": {"type": "integer", "minimum": 10, "maximum": 28, "default": 18},
        "materials": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
CATEGORY = "environment_piece"
KEYWORDS = ["column", "pillar", "pilaster", "colonnade", "marble"]

# Real-world scale, environment_piece: a ~3 m architectural column, ~0.8 m wide
# at the base/capital flare.
BBOX_RANGE = {"min": [0.5, 0.5, 2.2], "max": [1.2, 1.2, 3.8]}


def generate(params: dict, rng, theme: dict):
    import bmesh
    from mathutils import Vector

    from assetpipe.generators import common

    H = params["height_m"]
    R = params["radius_m"]
    flutes = params["flutes"]
    fdepth = params["flute_depth"] * R
    r_top = R * params["taper"]
    nz = params["rings"]
    seg = flutes * 4                       # angular resolution: 4 segments per flute

    base_h = 0.18 * (H / 3.0)
    cap_h = 0.22 * (H / 3.0)
    shaft_h = H - base_h - cap_h
    z0 = base_h                            # shaft bottom
    z1 = base_h + shaft_h                  # shaft top

    bm = bmesh.new()

    # ---- shaft: hand-built fluted lathe -----------------------------------
    def shaft_radius(zf: float, theta: float) -> float:
        # entasis: linear taper base->top, with a faint mid bulge
        rz = R + (r_top - R) * zf + 0.02 * R * math.sin(math.pi * zf)
        groove = 0.5 + 0.5 * math.cos(flutes * theta)     # 1 at groove centres
        return rz - fdepth * groove

    ring = []                              # ring[j][i] -> BMVert
    for j in range(nz + 1):
        zf = j / nz
        z = z0 + zf * shaft_h
        row = []
        for i in range(seg):
            theta = 2.0 * math.pi * i / seg
            r = shaft_radius(zf, theta)
            row.append(bm.verts.new(Vector((r * math.cos(theta), r * math.sin(theta), z))))
        ring.append(row)
    for j in range(nz):
        for i in range(seg):
            i2 = (i + 1) % seg
            bm.faces.new((ring[j][i], ring[j][i2], ring[j + 1][i2], ring[j + 1][i]))
    # cap the open ends so the shaft is watertight
    cb = bm.verts.new(Vector((0.0, 0.0, z0)))
    ct = bm.verts.new(Vector((0.0, 0.0, z1)))
    for i in range(seg):
        i2 = (i + 1) % seg
        bm.faces.new((ring[0][i2], ring[0][i], cb))        # bottom (downward)
        bm.faces.new((ring[nz][i], ring[nz][i2], ct))      # top (upward)

    # ---- helpers for the stubby round + square parts ----------------------
    def add_cyl(rb: float, rt: float, h: float, zc: float):
        c = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=True, segments=seg // 2,
                                  radius1=rb, radius2=rt, depth=h)
        bmesh.ops.translate(bm, verts=c["verts"], vec=(0.0, 0.0, zc))

    def add_box(sx: float, sy: float, h: float, zc: float):
        c = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, verts=c["verts"], vec=(sx, sy, h))
        bmesh.ops.translate(bm, verts=c["verts"], vec=(0.0, 0.0, zc))

    # base: square plinth, then a round base flaring up into the shaft
    add_box(R * 3.0, R * 3.0, base_h * 0.42, base_h * 0.21)
    add_cyl(R * 1.4, R * 1.08, base_h * 0.62, base_h * 0.42 + base_h * 0.31)

    # capital: round echinus flaring out of the shaft top, square abacus slab
    add_cyl(r_top * 1.02, R * 1.32, cap_h * 0.55, z1 + cap_h * 0.275)
    add_box(R * 3.0, R * 3.0, cap_h * 0.45, z1 + cap_h * 0.55 + cap_h * 0.225)

    for f in bm.faces:
        f.material_index = 0

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "column")
    common.freeze_transform(obj)
    common.decimate_to_budget(obj, budget=6000)
    common.smart_uv_project(obj, texture_resolution=1024)
    return obj
