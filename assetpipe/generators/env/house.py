"""env/house -- category ``environment_piece``.

A small gabled wooden house: plank-wall body, overhanging shingled roof
prism, an optional roof dormer, a door slab on one gable end, and glowing
window slabs. The first recipe to exercise per-part material slots
(spec 10.2's "generators may pick per-slot materials"): faces carry
``material_index`` 0/1/2 and the ``materials`` param lists the theme
material recipe id for each slot --

- slot 0: walls, door, dormer body (default ``fantasy_aged_wood``)
- slot 1: roof + dormer roof      (default ``fantasy_roof_shingles``)
- slot 2: window glass            (default ``fantasy_window_glow``)

Windows and the door are thin slabs half-sunk into the walls (never
booleans -- spec 9.5 / blender-procedural-geometry skill: booleans are the
top producer of degenerate slivers); the resulting part interpenetration is
intentional and S9-warn-level, same as the crate's greebles.
"""
from __future__ import annotations

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "width_m": {"type": "number", "minimum": 2.5, "maximum": 5.0, "default": 3.6},
        "depth_m": {"type": "number", "minimum": 2.2, "maximum": 4.5, "default": 3.0},
        "wall_height_m": {"type": "number", "minimum": 2.0, "maximum": 3.2, "default": 2.4},
        "roof_pitch": {"type": "number", "minimum": 0.5, "maximum": 1.2, "default": 0.85},
        "roof_overhang_m": {"type": "number", "minimum": 0.1, "maximum": 0.45, "default": 0.28},
        "dormer": {"type": "integer", "minimum": 0, "maximum": 1, "default": 1},
        "n_windows": {"type": "integer", "minimum": 1, "maximum": 4, "default": 2},
        "materials": {"type": "array", "items": {"type": "string"},
                      "default": ["fantasy_aged_wood", "fantasy_roof_shingles",
                                  "fantasy_window_glow"]},
    },
    "additionalProperties": False,
}
CATEGORY = "environment_piece"
KEYWORDS = ["house", "hut", "cottage", "cabin", "shack", "home"]

# Real-world scale, environment_piece: a small one-storey house incl. roof.
BBOX_RANGE = {"min": [2.5, 2.2, 2.5], "max": [6.0, 5.5, 6.0]}

SLOT_WALLS, SLOT_ROOF, SLOT_GLASS = 0, 1, 2


def _part_faces(bm, part_verts):
    """Faces made exclusively of this part's verts -- create_* ops return
    only ``verts`` (see docs/NEXT_STEPS.md), so face sets are recovered by
    vert membership right after each part is added."""
    vset = set(part_verts)
    faces = set()
    for v in part_verts:
        for f in v.link_faces:
            if all(fv in vset for fv in f.verts):
                faces.add(f)
    return faces


def _assign(bm, part_verts, slot: int) -> None:
    for f in _part_faces(bm, part_verts):
        f.material_index = slot


def _box(bm, size_xyz, center_xyz, slot: int):
    """Axis-aligned box part at final world scale, faces assigned to slot."""
    import bmesh

    part = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, verts=part["verts"], vec=size_xyz)
    bmesh.ops.translate(bm, verts=part["verts"], vec=center_xyz)
    _assign(bm, part["verts"], slot)
    return part["verts"]


def _gable_prism(bm, half_w, half_d, z_base, z_ridge, slot: int,
                 axis: str = "x", center=(0.0, 0.0)):
    """Closed 6-vert triangular prism: rectangular base at ``z_base``, ridge
    line at ``z_ridge`` running along ``axis`` through the base's center.
    The primitive roof shape -- manifold by construction (5 faces)."""
    cx, cy = center
    if axis == "x":
        base = [(-half_w, -half_d), (half_w, -half_d), (half_w, half_d), (-half_w, half_d)]
        ridge = [(-half_w, 0.0), (half_w, 0.0)]
    else:
        base = [(-half_w, -half_d), (half_w, -half_d), (half_w, half_d), (-half_w, half_d)]
        ridge = [(0.0, -half_d), (0.0, half_d)]
    verts = [bm.verts.new((cx + x, cy + y, z_base)) for x, y in base]
    r0, r1 = (bm.verts.new((cx + x, cy + y, z_ridge)) for x, y in ridge)
    b0, b1, b2, b3 = verts
    bm.faces.new((b0, b1, b2, b3))          # bottom
    if axis == "x":
        bm.faces.new((b0, b1, r1, r0))      # slope -Y
        bm.faces.new((b3, b2, r1, r0))      # slope +Y
        bm.faces.new((b0, b3, r0))          # gable -X
        bm.faces.new((b1, b2, r1))          # gable +X
    else:
        bm.faces.new((b0, b3, r1, r0))      # slope -X
        bm.faces.new((b1, b2, r1, r0))      # slope +X
        bm.faces.new((b0, b1, r0))          # gable -Y
        bm.faces.new((b3, b2, r1))          # gable +Y
    part = verts + [r0, r1]
    _assign(bm, part, slot)
    return part


def generate(params: dict, rng, theme: dict):
    """Build and return the house's root object.

    Deterministic given ``(params, rng)``: rng only draws window placement
    jitter and which roof slope hosts the dormer.
    """
    import bmesh

    from assetpipe.generators import common

    w = params["width_m"]
    d = params["depth_m"]
    h = params["wall_height_m"]
    ov = params["roof_overhang_m"]
    rise = params["roof_pitch"] * d / 2.0

    bm = bmesh.new()

    # --- body: plank-wall box, subdivided so the asset clears the category
    # triangle minimum and decimation/UVs have real topology to work with.
    body = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, verts=body["verts"], vec=(w, d, h))
    bmesh.ops.translate(bm, verts=body["verts"], vec=(0.0, 0.0, h / 2.0))
    body_edges = {e for v in body["verts"] for e in v.link_edges}
    # cuts=3 keeps the asset above the category triangle minimum (150) even
    # at the parameter floor (no dormer, one window).
    bmesh.ops.subdivide_edges(bm, edges=list(body_edges), cuts=3, use_grid_fill=True)
    # subdivide creates new faces; (re)assign every current face to walls --
    # later parts assign their own slots on top.
    for f in bm.faces:
        f.material_index = SLOT_WALLS

    # --- main roof: overhanging gable prism, ridge along X. Base sits
    # slightly below the wall top so the shared plane is never coplanar
    # (z-fighting, R8) while the sink stays intentional interpenetration.
    _gable_prism(bm, w / 2.0 + ov, d / 2.0 + ov, h - 0.03, h + rise, SLOT_ROOF, axis="x")

    # --- door: slab half-sunk into the +X gable wall.
    door_w, door_t, door_h = 0.95, 0.10, 1.9
    _box(bm, (door_t, door_w, door_h), (w / 2.0, rng.uniform(-0.15, 0.15), door_h / 2.0),
         SLOT_WALLS)

    # --- windows: glowing slabs half-sunk into the long (+/-Y) walls,
    # spread along X with a little rng jitter.
    win_w, win_t, win_h = 0.85, 0.10, 0.95
    sill_z = 1.15
    n = params["n_windows"]
    sides = [1.0, -1.0] * 2
    for i in range(n):
        side = sides[i]
        slots_along = max(1, (n + 1) // 2)
        idx = i // 2
        x = (idx + 1) / (slots_along + 1) * w - w / 2.0
        x += rng.uniform(-0.08, 0.08)
        x = max(-w / 2.0 + win_w, min(w / 2.0 - win_w, x))
        _box(bm, (win_w, win_t, win_h), (x, side * d / 2.0, sill_z + win_h / 2.0),
             SLOT_GLASS)

    # --- dormer: small gabled box poking out of one roof slope, with its
    # own glass pane on the outward face.
    if params["dormer"]:
        side = rng.choice([1.0, -1.0])
        dw, dd, dh = 1.0, 1.1, 0.85
        slope_half_d = d / 2.0 + ov
        y_c = side * slope_half_d * 0.45
        # z of the slope surface at |y| = |y_c|, then sink the box base into it
        z_surface = h + rise * (1.0 - abs(y_c) / slope_half_d)
        z_base = z_surface - dh * 0.55
        dx = rng.uniform(-w * 0.15, w * 0.15)
        _box(bm, (dw, dd, dh), (dx, y_c, z_base + dh / 2.0), SLOT_WALLS)
        # dormer roof: mini prism, ridge along Y (pointing down the slope)
        _gable_prism(bm, dw / 2.0 + 0.08, dd / 2.0 + 0.08, z_base + dh - 0.02,
                     z_base + dh + 0.4, SLOT_ROOF, axis="y", center=(dx, y_c))
        # dormer window pane on the outward (+/-Y) face
        pane_y = y_c + side * (dd / 2.0)
        _box(bm, (dw * 0.6, win_t, dh * 0.55), (dx, pane_y, z_base + dh * 0.5),
             SLOT_GLASS)

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "house")
    common.freeze_transform(obj)
    common.decimate_to_budget(obj, budget=3000)
    common.smart_uv_project(obj, texture_resolution=1024)
    return obj
