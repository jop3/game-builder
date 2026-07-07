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
        "wall_height_m": {"type": "number", "minimum": 2.0, "maximum": 3.2, "default": 2.7},
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
                 axis: str = "x", center=(0.0, 0.0), gable_slot: int | None = None):
    """Closed 6-vert triangular prism: rectangular base at ``z_base``, ridge
    line at ``z_ridge`` running along ``axis`` through the base's center.
    The primitive roof shape -- manifold by construction (5 faces).
    ``gable_slot`` overrides the material for the two triangular end faces
    (the reference language: shingled slopes, plank gable ends)."""
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
    gables = []
    bm.faces.new((b0, b1, b2, b3))                    # bottom
    if axis == "x":
        bm.faces.new((b0, b1, r1, r0))                # slope -Y
        bm.faces.new((b3, b2, r1, r0))                # slope +Y
        gables.append(bm.faces.new((b0, b3, r0)))     # gable -X
        gables.append(bm.faces.new((b1, b2, r1)))     # gable +X
    else:
        bm.faces.new((b0, b3, r1, r0))                # slope -X
        bm.faces.new((b1, b2, r1, r0))                # slope +X
        gables.append(bm.faces.new((b0, b1, r0)))     # gable -Y
        gables.append(bm.faces.new((b3, b2, r1)))     # gable +Y
    part = verts + [r0, r1]
    _assign(bm, part, slot)
    if gable_slot is not None:
        for f in gables:
            f.material_index = gable_slot
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

    # --- main roof (roadmap phase 2): the gable prism fills the volume, and
    # two THICK slope slabs laid over it give the reference's visible roof
    # thickness, fascia edge, and deeper eaves. A ridge beam caps the top.
    # The prism base sits slightly below the wall top so the shared plane is
    # never coplanar (z-fighting, R8).
    import math

    from mathutils import Matrix

    _gable_prism(bm, w / 2.0 + ov, d / 2.0 + ov, h - 0.03, h + rise, SLOT_ROOF, axis="x",
                 gable_slot=SLOT_WALLS)
    slope_half_d = d / 2.0 + ov
    slope_len = math.hypot(slope_half_d, rise) + 0.18
    pitch = math.atan2(rise, slope_half_d)
    for side in (1.0, -1.0):
        slab = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, verts=slab["verts"], vec=(w + 2 * ov + 0.15, slope_len, 0.12))
        # Negative sign: for the +Y slope the slab must descend as y grows
        # (a +pitch rotation about X lifts the +Y end -- the first render
        # produced a butterfly roof).
        bmesh.ops.rotate(bm, verts=slab["verts"], cent=(0, 0, 0),
                         matrix=Matrix.Rotation(-side * pitch, 3, 'X'))
        mid_y = side * slope_half_d / 2.0
        bmesh.ops.translate(bm, verts=slab["verts"],
                            vec=(0.0, mid_y, h + rise / 2.0 + 0.02))
        _assign(bm, slab["verts"], SLOT_ROOF)
        # Shingle courses (roadmap phase 3): coarse row slabs laid down the
        # slope, each slightly proud with rng eave-line jitter -- the
        # reference's chunky, hand-laid tile read.
        n_rows = 7
        sin_p, cos_p = math.sin(pitch), math.cos(pitch)
        for r in range(n_rows):
            t = (r + 0.78) / n_rows * math.hypot(slope_half_d, rise)
            row = bmesh.ops.create_cube(bm, size=1.0)
            row_len = w + 2 * ov + 0.15 + rng.uniform(-0.04, 0.05)
            row_w = math.hypot(slope_half_d, rise) / n_rows * 1.22
            bmesh.ops.scale(bm, verts=row["verts"], vec=(row_len, row_w, 0.055))
            bmesh.ops.rotate(bm, verts=row["verts"], cent=(0, 0, 0),
                             matrix=Matrix.Rotation(-side * pitch, 3, 'X'))
            y_r = side * (t * cos_p + sin_p * 0.10)
            z_r = (h + rise + 0.08) - t * sin_p + cos_p * 0.10
            bmesh.ops.translate(bm, verts=row["verts"],
                                vec=(rng.uniform(-0.02, 0.02), y_r, z_r))
            _assign(bm, row["verts"], SLOT_ROOF)
    _box(bm, (w + 2 * ov + 0.2, 0.2, 0.16), (0.0, 0.0, h + rise + 0.12), SLOT_ROOF)

    # --- wall relief (roadmap phase 3): corner posts and a storey beam so
    # the silhouette stops reading as an extruded rectangle.
    post = 0.16
    for sx in (1.0, -1.0):
        for sy in (1.0, -1.0):
            _box(bm, (post, post, h + 0.04),
                 (sx * (w / 2.0 - 0.01), sy * (d / 2.0 - 0.01), (h + 0.04) / 2.0),
                 SLOT_WALLS)
    for sy in (1.0, -1.0):
        _box(bm, (w - 0.1, 0.09, 0.13), (0.0, sy * (d / 2.0 + 0.015), h * 0.42),
             SLOT_WALLS)

    def framed_opening(center, size_wh, normal_axis: str, sign: float,
                       hood: bool = False, glass: bool = True) -> None:
        """A framed pane half-sunk into a wall: 4 frame bars (walls slot),
        recessed glass pane, cross mullions, optional shingled hood above --
        the reference's window language. ``normal_axis`` is the wall's facing
        axis ('x' or 'y'); ``center`` is (along, z) on that wall plane."""
        along, cz = center
        ww, wh = size_wh
        bar = 0.09
        frame_t, pane_t = 0.14, 0.07

        def place(sz_along, sz_z, thick, o_along, o_z, slot):
            if normal_axis == "y":
                size, cen = (sz_along, thick, sz_z), (along + o_along, sign * d / 2.0, cz + o_z)
            else:
                size, cen = (thick, sz_along, sz_z), (sign * w / 2.0, along + o_along, cz + o_z)
            _box(bm, size, cen, slot)

        # Jambs overlap INTO the head/sill bars and are fractionally thinner/
        # narrower: exactly-coincident joint planes get welded by the
        # finishing pass's remove_doubles into 4-faces-per-edge non-manifold
        # seams (12 of them failed S1 on the LODs) -- strict overlap with
        # unequal extents keeps every box manifold on its own.
        place(ww + 2 * bar, bar, frame_t, 0, wh / 2.0 + bar / 2.0, SLOT_WALLS)   # head
        place(ww + 2 * bar, bar, frame_t, 0, -wh / 2.0 - bar / 2.0, SLOT_WALLS)  # sill
        place(bar * 0.96, wh + bar, frame_t * 0.94, -(ww + bar) / 2.0, 0, SLOT_WALLS)
        place(bar * 0.96, wh + bar, frame_t * 0.94, (ww + bar) / 2.0, 0, SLOT_WALLS)
        if glass:
            place(ww, wh, pane_t, 0, 0, SLOT_GLASS)                              # pane
            place(bar * 0.6, wh, frame_t * 0.9, 0, 0, SLOT_WALLS)                # mullions
            place(ww, bar * 0.6, frame_t * 0.9, 0, 0, SLOT_WALLS)
        if hood:
            hw = ww + 2 * bar + 0.16
            hd = 0.34
            hood_part = bmesh.ops.create_cube(bm, size=1.0)
            bmesh.ops.scale(bm, verts=hood_part["verts"], vec=(hw, hd, 0.09))
            tilt = Matrix.Rotation(-sign * 0.5, 3, 'X') if normal_axis == "y" \
                else Matrix.Rotation(sign * 0.5, 3, 'Y')
            bmesh.ops.rotate(bm, verts=hood_part["verts"], cent=(0, 0, 0), matrix=tilt)
            if normal_axis == "y":
                cen = (along, sign * (d / 2.0 + hd * 0.32), cz + wh / 2.0 + bar + 0.12)
            else:
                cen = (sign * (w / 2.0 + hd * 0.32), along, cz + wh / 2.0 + bar + 0.12)
                bmesh.ops.rotate(bm, verts=hood_part["verts"], cent=(0, 0, 0),
                                 matrix=Matrix.Rotation(math.pi / 2.0, 3, 'Z'))
            bmesh.ops.translate(bm, verts=hood_part["verts"], vec=cen)
            _assign(bm, hood_part["verts"], SLOT_ROOF)

    # --- door on the +X gable: framed plank slab + stone-grey step.
    door_w, door_h = 0.95, 1.9
    door_y = rng.uniform(-0.15, 0.15)
    _box(bm, (0.12, door_w, door_h), (w / 2.0 + 0.02, door_y, door_h / 2.0), SLOT_WALLS)
    _box(bm, (0.16, door_w + 0.22, 0.1), (w / 2.0 + 0.02, door_y, door_h + 0.06), SLOT_WALLS)
    _box(bm, (0.5, door_w + 0.3, 0.12), (w / 2.0 + 0.18, door_y, 0.06), SLOT_WALLS)

    # --- windows: framed glowing panes on the long (+/-Y) walls; the first
    # (front) window gets the reference's shingled hood.
    win_w, win_h = 0.85, 0.95
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
        framed_opening((x, sill_z + win_h / 2.0), (win_w, win_h), "y", side,
                       hood=(i == 0))

    # --- dormer: gabled box on one roof slope with a framed pane.
    if params["dormer"]:
        side = rng.choice([1.0, -1.0])
        dw, dd, dh = 1.0, 1.1, 0.85
        y_c = side * slope_half_d * 0.45
        z_surface = h + rise * (1.0 - abs(y_c) / slope_half_d)
        z_base = z_surface - dh * 0.55
        dx = rng.uniform(-w * 0.15, w * 0.15)
        _box(bm, (dw, dd, dh), (dx, y_c, z_base + dh / 2.0), SLOT_WALLS)
        _gable_prism(bm, dw / 2.0 + 0.1, dd / 2.0 + 0.12, z_base + dh - 0.02,
                     z_base + dh + 0.42, SLOT_ROOF, axis="y", center=(dx, y_c),
                     gable_slot=SLOT_WALLS)
        pane_y = y_c + side * (dd / 2.0)
        _box(bm, (dw * 0.55, 0.07, dh * 0.5), (dx, pane_y, z_base + dh * 0.5),
             SLOT_GLASS)
        _box(bm, (dw * 0.55 + 0.12, 0.12, 0.08),
             (dx, pane_y, z_base + dh * 0.5 + dh * 0.25 + 0.04), SLOT_WALLS)
        _box(bm, (dw * 0.55 + 0.12, 0.12, 0.08),
             (dx, pane_y, z_base + dh * 0.5 - dh * 0.25 - 0.04), SLOT_WALLS)

    # --- attached wing (roadmap phase 3): the reference's asymmetric
    # two-mass build -- a lower volume on the gable end opposite the door,
    # with its own plank-gabled roof and window.
    ww_, wd_, wh_ = w * 0.5, d * 0.72, h * 0.68
    wing_cx = -(w / 2.0 + ww_ / 2.0 - 0.08)
    wing_part = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, verts=wing_part["verts"], vec=(ww_, wd_, wh_))
    bmesh.ops.translate(bm, verts=wing_part["verts"], vec=(wing_cx, 0.0, wh_ / 2.0))
    wing_edges = {e for v in wing_part["verts"] for e in v.link_edges}
    bmesh.ops.subdivide_edges(bm, edges=list(wing_edges), cuts=1, use_grid_fill=True)
    _assign(bm, [v for v in bm.verts if abs(v.co.x - wing_cx) <= ww_ / 2.0 + 0.01
                 and abs(v.co.y) <= wd_ / 2.0 + 0.01 and v.co.z <= wh_ + 0.01
                 and v.co.x < -w / 2.0 + 0.15], SLOT_WALLS)
    wing_rise = params["roof_pitch"] * wd_ / 2.0
    _gable_prism(bm, ww_ / 2.0 + ov * 0.8, wd_ / 2.0 + ov * 0.8, wh_ - 0.02,
                 wh_ + wing_rise, SLOT_ROOF, axis="x", center=(wing_cx, 0.0),
                 gable_slot=SLOT_WALLS)
    framed_opening((0.0, wh_ * 0.55), (win_w * 0.8, win_h * 0.75), "x", -1.0)

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "house")
    common.freeze_transform(obj)
    common.decimate_to_budget(obj, budget=3000)
    common.smart_uv_project(obj, texture_resolution=1024)
    return obj
