"""kit/wall -- category ``modular_kit_piece``.

A modular wall panel: exactly 3 m wide x 3 m tall footprint per spec 9.4
(only ``thickness_m`` varies), with ``SOCKET_<dir>_<i>`` empties on the
0.5 m grid along all four edges so adjacent kit pieces snap together
without a human eyeballing it.
"""
from __future__ import annotations

WIDTH_M = 3.0
HEIGHT_M = 3.0
GRID = 0.5

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "thickness_m": {"type": "number", "minimum": 0.15, "maximum": 0.4, "default": 0.25},
        "panel_lines": {"type": "integer", "minimum": 0, "maximum": 4, "default": 1},
        "materials": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
CATEGORY = "modular_kit_piece"
KEYWORDS = ["wall", "panel", "partition", "kit"]

# Real-world scale, modular_kit_piece (spec 9.4): exact 3 m x 3 m footprint;
# thickness is the only free axis.
BBOX_RANGE = {"min": [WIDTH_M, 0.15, HEIGHT_M], "max": [WIDTH_M, 0.4, HEIGHT_M]}


def _place_edge_sockets(root_obj, thickness):
    """Sockets every 0.5 m along the left/right/top/bottom edges, on both
    wall faces are not needed -- kit pieces meet edge-to-edge in the XZ
    plane; Y (thickness) sockets are centered.
    """
    from assetpipe.generators import common

    n = int(round(WIDTH_M / GRID))
    idx = 0
    for i in range(n + 1):
        x = -WIDTH_M / 2.0 + i * GRID
        common.add_socket(root_obj, f"SOCKET_BOTTOM_{idx}", (x, thickness / 2.0, 0.0))
        idx += 1
    idx = 0
    for i in range(n + 1):
        x = -WIDTH_M / 2.0 + i * GRID
        common.add_socket(root_obj, f"SOCKET_TOP_{idx}", (x, thickness / 2.0, HEIGHT_M))
        idx += 1
    idx = 0
    for i in range(n + 1):
        z = i * GRID
        common.add_socket(root_obj, f"SOCKET_LEFT_{idx}", (-WIDTH_M / 2.0, thickness / 2.0, z))
        idx += 1
    idx = 0
    for i in range(n + 1):
        z = i * GRID
        common.add_socket(root_obj, f"SOCKET_RIGHT_{idx}", (WIDTH_M / 2.0, thickness / 2.0, z))
        idx += 1


def generate(params: dict, rng, theme: dict):
    """Build and return the wall panel's root object with edge sockets.

    Deterministic given ``(params, rng)``: panel-line groove positions are
    evenly spaced (no randomness needed for a modular kit piece -- variety
    comes from the material, not the silhouette).
    """
    import bmesh

    from assetpipe.generators import common

    thickness = params["thickness_m"]

    bm = bmesh.new()
    box = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, verts=box["verts"], vec=(WIDTH_M, thickness, HEIGHT_M))

    n_panels = params["panel_lines"]
    for i in range(n_panels):
        t = (i + 1) / (n_panels + 1)
        z = -HEIGHT_M / 2.0 + HEIGHT_M * t
        band = [f for f in bm.faces
                if abs(f.normal.y) > 0.9 and abs(f.calc_center_median().z - z) < HEIGHT_M * 0.02]
        if band:
            bmesh.ops.inset_region(bm, faces=band, thickness=WIDTH_M * 0.01, depth=-0.005,
                                    use_boundary=True)

    common.base_center_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "wall")
    common.freeze_transform(obj)
    _place_edge_sockets(obj, thickness)
    common.decimate_to_budget(obj, budget=5000)
    common.smart_uv_project(obj, texture_resolution=2048)
    return obj
