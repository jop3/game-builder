"""Stage V1 (in-Blender half) -- mesh validity + UV checks S1-S12e (spec
13.1-13.2), run against the pre-export scene inside Blender.

Every check is a function of ``(obj, thresholds/params)`` returning the
spec 13.6 result dict:
``{check_id, verdict, severity, measured, threshold, details, defect}``.
Thresholds are always read from the caller's ``thresholds`` dict (mirroring
``config/defaults.yaml``'s ``validation:`` section, spec 20.3) -- never
hardcoded, per the pipeline-wide invariant in ``assetpipe/README.md``.

S5 (normal consistency) implements the spec's auto-fix-once semantics:
recalculating normals IS the fix, so a first-pass failure is corrected in
place on the real mesh and re-checked once; still failing means genuinely
broken topology.
"""
from __future__ import annotations

import bmesh
import bpy
from mathutils.bvhtree import BVHTree

# Blender's bundled Python does not have this repo on sys.path when a stage
# script is launched via `blender --background --python <this file>`; bootstrap
# the repo root (two levels up) so `import assetpipe` works. Kept dependency-
# free (os, not pathlib) and inserted before the first assetpipe import.
import os as _os
import sys as _sys

_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

from assetpipe.blender_scripts import common


def _result(check_id: str, passed: bool, severity: str, measured, threshold,
            details: str = "", defect: str | None = None) -> dict:
    return {
        "check_id": check_id,
        "verdict": "pass" if passed else "fail",
        "severity": severity,
        "measured": measured,
        "threshold": threshold,
        "details": details,
        "defect": defect if not passed else None,
    }


# ---------------------------------------------------------------------------
# S1-S4: mesh validity (spec 13.1)
# ---------------------------------------------------------------------------

def check_non_manifold(obj, topology: str = "closed") -> dict:
    """S1: non-manifold edges = 0 (boundary edges exempt if topology=='open',
    but wire edges never are)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    wire = [e for e in bm.edges if not e.link_faces]
    if topology == "open":
        bad = wire
        details = "topology=open: boundary edges exempt, wire edges are not"
    else:
        bad = [e for e in bm.edges if not e.is_manifold]
        details = "topology=closed"
    n = len(bad)
    bm.free()
    return _result("S1", n == 0, "blocker", n, 0, details=details, defect="NON_MANIFOLD")


def check_degenerate_faces(obj, min_area: float = 1e-8) -> dict:
    """S2: faces with area < min_area m^2 = 0."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bad = [f for f in bm.faces if f.calc_area() < min_area]
    n = len(bad)
    bm.free()
    return _result("S2", n == 0, "blocker", n, min_area, defect="DEGENERATE_FACES")


def check_zero_length_edges(obj, min_length: float = 1e-6) -> dict:
    """S3: edges with length < min_length m = 0. Maps to DEGENERATE_FACES in
    the fix table -- a zero-length edge always borders a degenerate face."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bad = [e for e in bm.edges if e.calc_length() < min_length]
    n = len(bad)
    bm.free()
    return _result("S3", n == 0, "blocker", n, min_length, defect="DEGENERATE_FACES")


def check_loose_geometry(obj) -> dict:
    """S4: loose verts (no edges) + loose edges (no faces) = 0."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    loose_verts = [v for v in bm.verts if not v.link_edges]
    loose_edges = [e for e in bm.edges if not e.link_faces]
    n = len(loose_verts) + len(loose_edges)
    bm.free()
    return _result("S4", n == 0, "blocker", n, 0, defect="LOOSE_GEOMETRY")


def check_normal_consistency(obj) -> dict:
    """S5: auto-fix-once semantics (spec 13.1) -- recalc on a copy first; if
    flips are found, apply the fix to the real mesh once and re-check;
    still-flipped means genuinely broken topology (regenerate, don't patch
    further)."""
    def _count_flips(mesh_data) -> int:
        bm = bmesh.new()
        bm.from_mesh(mesh_data)
        bm.faces.ensure_lookup_table()
        old_normals = [f.normal.copy() for f in bm.faces]
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        flips = sum(1 for f, n_old in zip(bm.faces, old_normals) if f.normal.dot(n_old) < 0)
        bm.free()
        return flips

    flips = _count_flips(obj.data)
    auto_fixed = False
    if flips > 0:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        auto_fixed = True
        flips = _count_flips(obj.data)
    return _result("S5", flips == 0, "blocker", flips, 0,
                   details=f"auto_fixed={auto_fixed}", defect="INVERTED_NORMALS")


def check_transforms_applied(obj, expected_origin=(0.0, 0.0, 0.0), tol: float = 0.001) -> dict:
    """S6: scale=(1,1,1)+-1e-6, rotation=identity+-1e-6, origin within
    ``tol`` m of ``expected_origin`` (spec 9.4)."""
    scale_ok = all(abs(s - 1.0) < 1e-6 for s in obj.scale)
    rot_ok = all(abs(a) < 1e-6 for a in obj.rotation_euler)
    origin_err = max(abs(obj.location[i] - expected_origin[i]) for i in range(3))
    ok = scale_ok and rot_ok and origin_err < tol
    return _result("S6", ok, "blocker", origin_err, tol,
                   details=f"scale_ok={scale_ok} rot_ok={rot_ok}")


def check_triangle_budget(obj, budget_min: int = 0, budget_max: int = 10 ** 9,
                          lod_ratio: float | None = None) -> dict:
    """S7: budget_min <= tris <= budget_max; each LOD level's tris must be
    <= its ratio * budget_max + 10% (spec 8.1/8.4)."""
    obj.data.calc_loop_triangles()
    tris = len(obj.data.loop_triangles)
    if lod_ratio is not None:
        lod_max = budget_max * lod_ratio * 1.10
        return _result("S7", tris <= lod_max, "blocker", tris, lod_max,
                       details=f"LOD ratio={lod_ratio}", defect="OVER_BUDGET")
    if tris > budget_max:
        return _result("S7", False, "blocker", tris, budget_max, defect="OVER_BUDGET")
    if tris < budget_min:
        return _result("S7", False, "blocker", tris, budget_min, defect="UNDER_BUDGET")
    return _result("S7", True, "blocker", tris, budget_max)


def check_bbox(obj, bbox_range: dict) -> dict:
    """S8: bounding box within the recipe's declared ``BBOX_RANGE``.

    ``bbox_range`` matches the generator-recipe convention (see e.g.
    ``assetpipe/generators/props/crate.py``): ``{"min": [x, y, z], "max": [x,
    y, z]}`` triples compared component-wise against ``obj.dimensions``
    (Blender X/Y/Z, post finishing-pass -- transforms already applied)."""
    dims = (obj.dimensions.x, obj.dimensions.y, obj.dimensions.z)
    lo, hi = bbox_range["min"], bbox_range["max"]
    bad = [i for i in range(3) if not (lo[i] <= dims[i] <= hi[i])]
    return _result("S8", not bad, "blocker", list(dims), bbox_range,
                   details=f"out-of-range axes (0=X,1=Y,2=Z): {bad}", defect="BBOX_OUT_OF_RANGE")


def check_self_intersection(obj, max_fraction: float = 0.005) -> dict:
    """S9 (warn): BVH self-overlap, excluding adjacent (shared-vertex) face
    pairs, as a fraction of total faces."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    tree = BVHTree.FromBMesh(bm, epsilon=0.0)
    pairs = tree.overlap(tree)

    def shares_vert(i: int, j: int) -> bool:
        return bool({v.index for v in bm.faces[i].verts} & {v.index for v in bm.faces[j].verts})

    real = [(i, j) for i, j in pairs if i < j and not shares_vert(i, j)]
    involved = {i for pair in real for i in pair}
    frac = len(involved) / max(len(bm.faces), 1)
    bm.free()
    return _result("S9", frac <= max_fraction, "warn", frac, max_fraction,
                   defect="SELF_INTERSECTION")


def check_kit_sockets_on_grid(obj, grid: float = 0.5, tol: float = 0.0001) -> dict:
    """S10 (kit pieces only): every ``SOCKET_*`` empty within ``tol`` m of
    the ``grid`` m grid."""
    bad = []
    for child in obj.children:
        if not child.name.startswith("SOCKET_"):
            continue
        for coord in child.location:
            nearest = round(coord / grid) * grid
            if abs(coord - nearest) > tol:
                bad.append(child.name)
                break
    return _result("S10", not bad, "blocker", bad, [],
                   details=f"grid={grid}m tol={tol}m", defect="SOCKET_OFF_GRID")


def check_skin_weights(obj, max_influences: int = 4, weight_tol: float = 1e-4,
                       min_total: float = 0.5) -> dict:
    """S11 (characters only): max 4 influences/vertex, weights normalized to
    1+-1e-4, no vertex with total weight < 0.5 (orphaned)."""
    bad = 0
    for v in obj.data.vertices:
        groups = [g for g in v.groups if g.weight > 0]
        total = sum(g.weight for g in groups)
        if len(groups) > max_influences or total < min_total or abs(total - 1.0) > weight_tol:
            bad += 1
    return _result("S11", bad == 0, "blocker", bad, 0, defect="SKIN_WEIGHT_INVALID")


# ---------------------------------------------------------------------------
# S12a-e: UV checks (spec 13.2)
# ---------------------------------------------------------------------------

def _uv_area(coords) -> float:
    if len(coords) < 3:
        return 0.0
    a, b, c = coords[0], coords[1], coords[2]
    return abs((b - a).cross(c - a)) / 2


def check_uv_coverage(obj) -> dict:
    """S12a: every face has a UV map, per-face UV area > 0."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    uv = bm.loops.layers.uv.active
    if uv is None:
        bm.free()
        return _result("S12a", False, "blocker", 0, 0, details="no UV layer", defect="UV_MISSING")
    bad = sum(1 for f in bm.faces if _uv_area([l[uv].uv for l in f.loops]) <= 0)
    bm.free()
    return _result("S12a", bad == 0, "blocker", bad, 0, defect="UV_MISSING")


def _rasterize_triangle_add(accum, tri_px) -> None:
    """Barycentric rasterization of one UV triangle into an accumulation
    buffer (the skill's "draw each triangle into a count array" approach,
    without a polygon-clip library)."""
    import numpy as np

    res = accum.shape[0]
    xs, ys = [p[0] for p in tri_px], [p[1] for p in tri_px]
    x0, x1 = max(0, int(min(xs))), min(res, int(max(xs)) + 1)
    y0, y1 = max(0, int(min(ys))), min(res, int(max(ys)) + 1)
    if x1 <= x0 or y1 <= y0:
        return
    xx, yy = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
    (ax, ay), (bx, by), (cx, cy) = tri_px
    denom = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(denom) < 1e-12:
        return
    w0 = ((by - cy) * (xx - cx) + (cx - bx) * (yy - cy)) / denom
    w1 = ((cy - ay) * (xx - cx) + (ax - cx) * (yy - cy)) / denom
    w2 = 1 - w0 - w1
    inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    accum[y0:y1, x0:x1] += inside.astype(accum.dtype)


def _rasterize_uv_coverage(obj, resolution: int = 1024, skip_mirrored: bool = True):
    """Returns an ``int32`` ``(resolution, resolution)`` per-texel coverage
    count (spec 13.1/13.2's "rasterize islands ... count multiply-covered
    texels"), shared by S12b (overlap) and S12e (bake margin)."""
    import numpy as np

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    uv = bm.loops.layers.uv.active
    mirror_layer = bm.faces.layers.int.get("uv_mirrored") if skip_mirrored else None
    accum = np.zeros((resolution, resolution), dtype=np.int32)
    for f in bm.faces:
        if mirror_layer is not None and f[mirror_layer]:
            continue
        loops = f.loops
        if len(loops) != 3:
            continue
        pts = [(l[uv].uv.x * resolution, (1.0 - l[uv].uv.y) * resolution) for l in loops]
        _rasterize_triangle_add(accum, pts)
    bm.free()
    return accum


def check_uv_overlap(obj, max_fraction: float = 0.005, resolution: int = 1024) -> dict:
    """S12b: overlapping UV area / total shell area <= max_fraction.
    Mirrored islands (face int layer ``uv_mirrored``) are exempt."""
    accum = _rasterize_uv_coverage(obj, resolution)
    covered = accum >= 1
    overlapped = accum >= 2
    frac = float(overlapped.sum()) / float(max(int(covered.sum()), 1))
    return _result("S12b", frac <= max_fraction, "blocker", frac, max_fraction,
                   defect="UV_OVERLAP")


def check_uv_bounds(obj, tol: float = 0.001) -> dict:
    """S12c: all UVs in [-tol, 1+tol] -- skipped for ``uv_mode: "tiling"``
    meshes (box-projected surfaces are expected to exceed [0, 1])."""
    if obj.data.get("uv_mode") == "tiling":
        return _result("S12c", True, "blocker", 0, 0, details="skipped: uv_mode=tiling")
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    uv = bm.loops.layers.uv.active
    bad = 0
    if uv is not None:
        for f in bm.faces:
            for l in f.loops:
                u, v = l[uv].uv
                if not (-tol <= u <= 1 + tol and -tol <= v <= 1 + tol):
                    bad += 1
    bm.free()
    return _result("S12c", bad == 0, "blocker", bad, 0, defect="UV_OUT_OF_BOUNDS")


def check_uv_stretch_density(obj, p95_p5_max: float = 4.0, hard_max: float = 8.0,
                             stretch_max: float = 2.5) -> dict:
    """S12d: texel-density p95/p5 ratio <= p95_p5_max (warn; blocker if >
    hard_max); per-face conformal stretch <= stretch_max for >= 99% of faces."""
    import numpy as np

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    uv = bm.loops.layers.uv.active
    densities, stretch_ratios = [], []
    for f in bm.faces:
        if len(f.loops) != 3 or uv is None:
            continue
        world_area = f.calc_area()
        if world_area <= 1e-12:
            continue
        coords = [l[uv].uv for l in f.loops]
        uv_area = _uv_area(coords)
        densities.append((uv_area / world_area) ** 0.5)
        verts = [l.vert.co for l in f.loops]
        edge_ratios = []
        for i in range(3):
            world_len = (verts[(i + 1) % 3] - verts[i]).length
            uv_len = (coords[(i + 1) % 3] - coords[i]).length
            if world_len > 1e-9:
                edge_ratios.append(uv_len / world_len)
        if edge_ratios:
            stretch_ratios.append(max(edge_ratios) / max(min(edge_ratios), 1e-9))
    bm.free()

    if not densities:
        return _result("S12d", True, "warn", 0.0, p95_p5_max, details="no valid faces")
    p95, p5 = float(np.percentile(densities, 95)), float(np.percentile(densities, 5))
    ratio = p95 / max(p5, 1e-9)
    stretch_ok_frac = float(np.mean([r <= stretch_max for r in stretch_ratios])) if stretch_ratios else 1.0
    severity = "blocker" if ratio > hard_max else "warn"
    ok = ratio <= p95_p5_max and stretch_ok_frac >= 0.99
    return _result("S12d", ok, severity, ratio, p95_p5_max,
                   details=f"stretch_p99_ok_frac={stretch_ok_frac:.4f}", defect="UV_STRETCH")


def _label_islands(covered):
    """4-connected component labeling. Uses ``scipy.ndimage.label`` when
    available (fast path); falls back to a pure-NumPy/stdlib flood fill
    otherwise, since scipy is not a hard dependency of this pipeline."""
    import numpy as np

    try:
        from scipy import ndimage
        labels, _ = ndimage.label(covered)
        return labels
    except ImportError:
        pass

    labels = np.zeros(covered.shape, dtype=np.int32)
    next_label = 1
    h, w = covered.shape
    for y in range(h):
        row = covered[y]
        for x in range(w):
            if row[x] and labels[y, x] == 0:
                stack = [(y, x)]
                labels[y, x] = next_label
                while stack:
                    cy, cx = stack.pop()
                    for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                        if 0 <= ny < h and 0 <= nx < w and covered[ny, nx] and labels[ny, nx] == 0:
                            labels[ny, nx] = next_label
                            stack.append((ny, nx))
                next_label += 1
    return labels


def check_uv_bake_margin(obj, min_texels: int = 4, resolution: int = 1024) -> dict:
    """S12e (warn): minimum distance between distinct UV islands >= min_texels
    at the target resolution. Approximated by dilating each island's mask by
    ``min_texels`` (iterated 8-neighbour NumPy dilation: Blender's bundled
    Python ships NumPy but neither Pillow nor scipy) and checking for overlap
    with any other island's raw mask."""
    import numpy as np

    def _dilate(mask: "np.ndarray", texels: int) -> "np.ndarray":
        # texels iterations of a 3x3 (8-neighbour) binary dilation == one
        # dilation by a (2*texels+1)-square element, i.e. PIL MaxFilter(size).
        out = mask
        for _ in range(int(texels)):
            p = np.pad(out, 1)
            out = (p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1]
                   | p[1:-1, :-2] | p[1:-1, 2:]
                   | p[:-2, :-2] | p[:-2, 2:] | p[2:, :-2] | p[2:, 2:])
        return out

    accum = _rasterize_uv_coverage(obj, resolution)
    labels = _label_islands(accum > 0)
    n_islands = int(labels.max())
    if n_islands <= 1:
        return _result("S12e", True, "warn", 0, min_texels, details="single island or no coverage")

    violations = 0
    for i in range(1, n_islands + 1):
        dilated = _dilate(labels == i, min_texels)
        others = (labels != i) & (labels != 0)
        if np.any(dilated & others):
            violations += 1
    return _result("S12e", violations == 0, "warn", violations, 0,
                   details=f"islands_with_margin_violation={violations}", defect="BAKE_MARGIN_LOW")


# ---------------------------------------------------------------------------
# Dispatch + entry point
# ---------------------------------------------------------------------------

def run_all_checks(obj, thresholds: dict, *, topology: str = "closed",
                   bbox_range: dict | None = None, budget: dict | None = None,
                   is_kit: bool = False, is_character: bool = False,
                   expected_origin=(0.0, 0.0, 0.0), lod_ratio: float | None = None) -> list[dict]:
    """Run every applicable S1-S12e check on one object (spec 13.1-13.2)."""
    budget = budget or {}
    checks = [
        check_non_manifold(obj, topology),
        check_degenerate_faces(obj, thresholds.get("s2_min_face_area_m2", 1e-8)),
        check_zero_length_edges(obj, thresholds.get("s3_min_edge_length_m", 1e-6)),
        check_loose_geometry(obj),
        check_normal_consistency(obj),
        check_transforms_applied(obj, expected_origin, thresholds.get("s6_origin_tolerance_m", 0.001)),
        check_triangle_budget(obj, budget.get("min", 0), budget.get("max", 10 ** 9), lod_ratio),
        check_uv_coverage(obj),
        check_uv_overlap(obj, thresholds.get("s12b_uv_overlap_max_fraction", 0.005)),
        check_uv_bounds(obj),
        check_uv_stretch_density(
            obj, thresholds.get("s12d_texel_density_p95_p5_max", 4.0),
            thresholds.get("s12d_texel_density_hard_max", 8.0),
            thresholds.get("s12d_conformal_stretch_max", 2.5)),
        check_uv_bake_margin(obj, thresholds.get("s12e_min_island_margin_texels", 4)),
        check_self_intersection(obj, thresholds.get("s9_max_self_intersect_face_fraction", 0.005)),
    ]
    if bbox_range:
        checks.append(check_bbox(obj, bbox_range))
    if is_kit:
        checks.append(check_kit_sockets_on_grid(
            obj, 0.5, thresholds.get("s10_socket_grid_tolerance_m", 0.0001)))
    if is_character:
        checks.append(check_skin_weights(obj))
    return checks


def main() -> None:
    payload = common.parse_args()
    obj = bpy.data.objects[payload["object_name"]]
    thresholds = payload.get("validation", {})
    results = run_all_checks(
        obj, thresholds,
        topology=payload.get("topology", "closed"),
        bbox_range=payload.get("bbox_range"),
        budget=payload.get("budget"),
        is_kit=payload.get("is_kit", False),
        is_character=payload.get("is_character", False),
        expected_origin=tuple(payload.get("expected_origin", (0.0, 0.0, 0.0))))
    verdict = "fail" if any(r["verdict"] == "fail" and r["severity"] == "blocker" for r in results) \
        else "pass"
    common.write_result(payload["out_path"], {
        "asset_id": payload.get("asset_id"),
        "iteration": payload.get("iteration"),
        "stage": "V1",
        "verdict": verdict,
        "checks": results,
    })


if __name__ == "__main__":
    main()
