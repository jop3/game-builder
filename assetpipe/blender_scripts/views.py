"""Bpy-free view table, lighting-rig specs, and camera-framing math for the
Stage R render harness (spec 14).

Kept free of ``bpy``/``mathutils`` on purpose: the view IDs, rig assignments,
and the bounding-box-to-camera-distance formula are pure data/math, so this
module is unit-tested directly (``assetpipe/tests/test_blender_scripts.py``
asserts every view_id from spec 14.2 is present here). ``render_views.py``
imports this module and converts its plain ``(x, y, z)`` tuples into actual
``mathutils.Vector``/camera transforms.
"""
from __future__ import annotations

import math

RESOLUTION_PX = 1024
CYCLES_SAMPLES = 128

REFERENCE_CUBE_SIZE_M = 1.0
REFERENCE_CUBE_OFFSET_M = 1.5   # to the asset's left (spec 14.2)
GROUND_GREY = 0.18              # 18% grey ground plane (spec 14.2)

# ---------------------------------------------------------------------------
# Lighting rigs (spec 14.2)
# ---------------------------------------------------------------------------

LIGHTING_RIGS = {
    "L1": {"kind": "studio_hdri", "strength": 1.0,
           "description": "neutral studio HDRI, bundled/license-clean, fixed"},
    "L2": {"kind": "warm_sun", "color_temp_k": 4500, "elevation_deg": 45, "fill": True,
           "description": "warm directional sun (4500K, 45deg elevation) + weak fill"},
    "L3": {"kind": "dim_blue_rim",
           "description": "dim blue rim light only -- stress-tests black-texture/emissive bugs"},
    "none": {"kind": "none", "description": "no scene lighting (silhouette/debug overrides)"},
}

# ---------------------------------------------------------------------------
# Mesh-asset view set (spec 14.2). azimuth_deg 0 = front (+Y front, spec 9.4).
# ---------------------------------------------------------------------------

MESH_TURNTABLE_VIEWS = [
    {"view_id": f"turn_{az:03d}", "kind": "turntable", "azimuth_deg": az,
     "elevation_deg": 15, "rig": "L1"}
    for az in range(0, 360, 45)
]

MESH_VIEW_SET = MESH_TURNTABLE_VIEWS + [
    {"view_id": "high_045", "kind": "high", "azimuth_deg": 45, "elevation_deg": 40, "rig": "L1"},
    {"view_id": "high_225", "kind": "high", "azimuth_deg": 225, "elevation_deg": 40, "rig": "L1"},
    {"view_id": "top", "kind": "top", "azimuth_deg": 0, "elevation_deg": 90, "rig": "L1"},
    {"view_id": "close_034", "kind": "close", "azimuth_deg": 34, "elevation_deg": 15,
     "rig": "L1", "zoom": 2.0,
     "description": "3/4 view, 2x zoom on the densest-detail region (recipe-reported)"},
    {"view_id": "lit_warm_045", "kind": "turntable", "azimuth_deg": 45, "elevation_deg": 15, "rig": "L2"},
    {"view_id": "lit_warm_225", "kind": "turntable", "azimuth_deg": 225, "elevation_deg": 15, "rig": "L2"},
    {"view_id": "lit_dark_090", "kind": "side", "azimuth_deg": 90, "elevation_deg": 15, "rig": "L3"},
    {"view_id": "silhouette_000", "kind": "silhouette", "azimuth_deg": 0, "elevation_deg": 15,
     "rig": "none", "debug_material": "silhouette"},
    {"view_id": "silhouette_090", "kind": "silhouette", "azimuth_deg": 90, "elevation_deg": 15,
     "rig": "none", "debug_material": "silhouette"},
    {"view_id": "normals_045", "kind": "normals", "azimuth_deg": 45, "elevation_deg": 15,
     "rig": "none", "debug_material": "normals"},
    {"view_id": "normals_225", "kind": "normals", "azimuth_deg": 225, "elevation_deg": 15,
     "rig": "none", "debug_material": "normals"},
    {"view_id": "uvcheck_045", "kind": "uvcheck", "azimuth_deg": 45, "elevation_deg": 15,
     "rig": "L1", "debug_material": "uvcheck"},
]

# Characters additionally get turntables at eye level (elevation 0deg, spec 14.2).
CHARACTER_EXTRA_VIEWS = [
    {"view_id": f"turn_eye_{az:03d}", "kind": "turntable", "azimuth_deg": az,
     "elevation_deg": 0, "rig": "L1"}
    for az in range(0, 360, 45)
]

# Tiling texture sets: 3x3-tiled plane + a 1x1 flat view + a shaded sphere per
# lighting rig (spec 14.2).
TILING_VIEW_SET = [
    {"view_id": "tile3x3_persp", "kind": "tile3x3", "azimuth_deg": 45, "elevation_deg": 30, "rig": "L1"},
    {"view_id": "tile3x3_top", "kind": "tile3x3", "azimuth_deg": 0, "elevation_deg": 90, "rig": "L1"},
    {"view_id": "tile1x1", "kind": "tile1x1", "azimuth_deg": 45, "elevation_deg": 30, "rig": "L1"},
] + [
    {"view_id": f"sphere_{rig}", "kind": "sphere", "azimuth_deg": 45, "elevation_deg": 20, "rig": rig}
    for rig in ("L1", "L2", "L3")
]

# Skyboxes: 6 axis-aligned perspective views from the origin + up/down poles
# (spec 14.2).
SKYBOX_VIEW_SET = [
    {"view_id": f"axis_{name}", "kind": "skybox_axis", "azimuth_deg": az, "elevation_deg": 0, "rig": "none"}
    for name, az in (("front", 0), ("right", 90), ("back", 180), ("left", 270))
] + [
    {"view_id": f"axis_{name}", "kind": "skybox_axis", "azimuth_deg": 0, "elevation_deg": el, "rig": "none"}
    for name, el in (("high", 45), ("low", -45))
] + [
    {"view_id": "up_pole", "kind": "skybox_pole", "azimuth_deg": 0, "elevation_deg": 90, "rig": "none"},
    {"view_id": "down_pole", "kind": "skybox_pole", "azimuth_deg": 0, "elevation_deg": -90, "rig": "none"},
]

# Every view this harness knows how to render, across every asset class --
# the completeness assertion in test_blender_scripts.py checks against this.
VIEW_SET = MESH_VIEW_SET + CHARACTER_EXTRA_VIEWS + TILING_VIEW_SET + SKYBOX_VIEW_SET
VIEW_IDS = sorted({v["view_id"] for v in VIEW_SET})

DEBUG_MATERIALS = frozenset({"silhouette", "normals", "uvcheck"})


def view_set_for_category(category: str) -> list[dict]:
    """The applicable view list for one asset category (spec 14.2)."""
    if category == "tiling_texture_set":
        return TILING_VIEW_SET
    if category == "skybox":
        return SKYBOX_VIEW_SET
    views = list(MESH_VIEW_SET)
    if category in ("character_primary", "character_background"):
        views = views + CHARACTER_EXTRA_VIEWS
    return views


# ---------------------------------------------------------------------------
# Camera framing math (bbox -> distance/location), no mathutils.Vector so this
# is testable without Blender. See asset-visual-qa skill's `frame_object` for
# the mathutils.Vector version this reimplements.
# ---------------------------------------------------------------------------

def frame_distance(bbox_min: tuple[float, float, float], bbox_max: tuple[float, float, float],
                    fov_rad: float, fill: float = 0.65) -> float:
    """Camera distance so the asset's bounding sphere fills ``fill`` of the
    frame height (spec 14.1: "asset occupies 55-75% of frame height")."""
    radius = math.dist(bbox_min, bbox_max) / 2
    return radius / (fill * math.tan(fov_rad / 2))


def camera_transform(bbox_min: tuple[float, float, float], bbox_max: tuple[float, float, float],
                      azimuth_deg: float, elevation_deg: float, fov_rad: float,
                      fill: float = 0.65) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return ``(camera_location, look_at_target)`` as plain ``(x, y, z)``
    tuples for one turntable-style view. Pure math -- no ``mathutils`` -- so
    ``render_views.py`` (bpy-touching) wraps the result in
    ``mathutils.Vector`` rather than this module depending on it."""
    center = tuple((bbox_min[i] + bbox_max[i]) / 2 for i in range(3))
    dist = frame_distance(bbox_min, bbox_max, fov_rad, fill)
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    offset = (math.cos(el) * math.sin(az), -math.cos(el) * math.cos(az), math.sin(el))
    location = tuple(center[i] + offset[i] * dist for i in range(3))
    return location, center


def frame_fill_fraction(bbox_min: tuple[float, float, float], bbox_max: tuple[float, float, float],
                         fov_rad: float, distance: float) -> float:
    """Exact inverse of :func:`frame_distance`: what fraction of frame height
    the asset fills at a given camera distance -- used to assert the spec
    14.1 55-75% framing requirement holds for a computed distance.

    Defined as a ratio of tangents (not of angles) to match
    ``frame_distance``'s own definition of "fill" exactly
    (``dist = radius / (fill * tan(fov/2))`` implies
    ``fill = (radius / dist) / tan(fov/2)``); an angle-ratio definition would
    only be an approximate inverse away from small-angle FOVs."""
    radius = math.dist(bbox_min, bbox_max) / 2
    if distance <= 0:
        return float("inf")
    return (radius / distance) / math.tan(fov_rad / 2)


def fill_fraction_for_view(view: dict) -> float:
    """The target frame-fill fraction for one view-table entry (spec 14.1's
    default 0.65, adjusted for the `close_034` 2x zoom, spec 14.2)."""
    base = 0.65
    if view.get("kind") == "close":
        return min(0.95, base * view.get("zoom", 1.0))
    return base
