"""props/game_board -- category ``prop_small``.

An 8x8 fantasy tabletop board (Othello/Reversi): a wood base slab, a raised
border rim, and an inlaid grid of thin metal ribs dividing the play surface
into 8x8 cells. Two material slots -- slot 0 (wood) is the play surface/base,
slot 1 (metal) is the border rim + grid inlay -- resolved theme-agnostically
via SLOT_MATERIALS (like props/barrel).

The rim and grid ribs are proud boxes that strictly overlap the base (never
coincident planes, which remove_doubles would weld into non-manifold seams);
the intentional overlap trips S9 SELF_INTERSECTION as a warn, exactly like the
barrel's hoops -- by design, never a blocker.

Authored for ``examples/othello`` (fantasy Othello): see its spec.
"""
from __future__ import annotations

CELLS = 8  # standard Othello board

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "width_m": {"type": "number", "minimum": 0.36, "maximum": 0.48, "default": 0.44},
        "thickness_m": {"type": "number", "minimum": 0.02, "maximum": 0.045, "default": 0.03},
        "border_m": {"type": "number", "minimum": 0.02, "maximum": 0.04, "default": 0.028},
        "border_rise_m": {"type": "number", "minimum": 0.004, "maximum": 0.016, "default": 0.010},
        "grid_rib_m": {"type": "number", "minimum": 0.002, "maximum": 0.006, "default": 0.0035},
        "grid_rise_m": {"type": "number", "minimum": 0.001, "maximum": 0.005, "default": 0.003},
        "materials": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
CATEGORY = "prop_small"
KEYWORDS = ["board", "othello", "reversi", "checkerboard", "grid", "gameboard", "tabletop"]

# Per-slot theme-material keyword preferences (resolved by
# stages.resolve_slot_materials): slot 0 = wood surface, slot 1 = metal inlay.
SLOT_MATERIALS = (
    ("wood", "plank", "timber"),          # slot 0: board surface + base
    ("iron", "trim", "metal", "gold"),    # slot 1: rim + grid inlay
)

# Real-world scale, prop_small: a ~40 cm square board, a few cm thick.
BBOX_RANGE = {"min": [0.36, 0.36, 0.025], "max": [0.48, 0.48, 0.06]}

SLOT_WOOD, SLOT_METAL = 0, 1


def _add_box(bm, center, size, mat_index):
    """Add an axis-aligned box (center, full-size) and tag its faces."""
    import bmesh

    res = bmesh.ops.create_cube(bm, size=1.0)
    verts = res["verts"]
    bmesh.ops.scale(bm, verts=verts, vec=size)
    bmesh.ops.translate(bm, verts=verts, vec=center)
    vset = set(verts)
    for v in verts:
        for f in v.link_faces:
            if all(fv in vset for fv in f.verts):
                f.material_index = mat_index
    return res


def generate(params: dict, rng, theme: dict):
    """Build and return the board's root object (wood base + metal rim + grid).
    Deterministic; ``rng`` accepted (unused) per the recipe contract."""
    import bmesh

    from assetpipe.generators import common

    w = params["width_m"]
    h = params["thickness_m"]
    border = params["border_m"]
    b_rise = params["border_rise_m"]
    rib = params["grid_rib_m"]
    g_rise = params["grid_rise_m"]

    top = h / 2.0
    play = w - 2.0 * border            # inner play-field span
    cell = play / CELLS
    half = play / 2.0

    bm = bmesh.new()

    # 1) Wood base slab (slot 0).
    _add_box(bm, (0.0, 0.0, 0.0), (w, w, h), SLOT_WOOD)

    # 2) Metal border rim (slot 1): four full-length rails proud of the surface
    #    by b_rise. They must STRICTLY interpenetrate the base -- never share a
    #    vertex/face with it or each other, or finishing_pass's remove_doubles
    #    welds the overlap into non-manifold edges (S1 blocker). So: inset from
    #    the outer edge by a hair (outer faces sit just inside the base
    #    footprint), sunk only partway down (bottom above the base bottom), and
    #    full-length on both axes so the four rails OVERLAP at the corners
    #    rather than abut. All overlap -> S9 SELF_INTERSECTION warn, by design.
    edge_inset = 0.0015
    outer = w / 2.0 - edge_inset
    span = w - 2.0 * edge_inset        # long-axis length: ends stay inside the base
    r_bottom = -h / 2.0 + 0.4 * h      # sunk partway, not to the base bottom
    r_top = top + b_rise
    rail_cz = (r_top + r_bottom) / 2.0
    rail_sz = r_top - r_bottom
    cen = outer - border / 2.0
    # The four rails reach the corners so the frame has no notch, which means
    # perpendicular rails share the corner point. Stagger the X-pair down by a
    # weld-safe 0.6 mm (>> remove_doubles' 1e-5 threshold) so no corner vertex
    # coincides -- the shells still interpenetrate (S9 warn) but stay manifold.
    dz = 0.0006
    _add_box(bm, (0.0, cen, rail_cz), (span, border, rail_sz), SLOT_METAL)         # +Y
    _add_box(bm, (0.0, -cen, rail_cz), (span, border, rail_sz), SLOT_METAL)        # -Y
    _add_box(bm, (cen, 0.0, rail_cz - dz), (border, span, rail_sz), SLOT_METAL)    # +X
    _add_box(bm, (-cen, 0.0, rail_cz - dz), (border, span, rail_sz), SLOT_METAL)   # -X

    # 3) Metal grid inlay (slot 1): CELLS+1 ribs each axis at cell boundaries,
    #    proud by g_rise, overlapping the surface downward. Spans the play-field.
    rib_h = (top + g_rise) - (top - g_rise)   # = 2*g_rise thickness in z
    rib_cz = top                              # centered on the surface: proud + sunk
    rib_z_size = 2.0 * g_rise
    for i in range(CELLS + 1):
        coord = -half + i * cell
        # lines of constant X (run along Y)
        _add_box(bm, (coord, 0.0, rib_cz), (rib, play, rib_z_size), SLOT_METAL)
        # lines of constant Y (run along X)
        _add_box(bm, (0.0, coord, rib_cz), (play, rib, rib_z_size), SLOT_METAL)

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "board")
    common.freeze_transform(obj)
    common.decimate_to_budget(obj, budget=1500)
    common.smart_uv_project(obj, texture_resolution=1024)
    return obj
