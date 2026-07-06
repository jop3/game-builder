"""Scripted image analytics (spec 14.5 A1-A4, 13.4 S19) — the cheap objective
net that runs before any vision-model call.

All functions take float arrays in [0,1], shape (H, W, 3) unless noted, and
return a result dict in the static-report check format. Thresholds are
parameters with spec defaults so pipeline.yaml can own them.
"""
from __future__ import annotations

import numpy as np


def _result(check_id: str, passed: bool, measured: float, threshold: float,
            severity: str = "blocker", details: str = "") -> dict:
    return {"check_id": check_id, "verdict": "pass" if passed else "fail",
            "severity": severity, "measured": round(float(measured), 6),
            "threshold": threshold, "details": details}


def check_not_empty(img: np.ndarray, min_std: float = 2 / 255,
                    mean_lo: float = 0.01, mean_hi: float = 0.99) -> dict:
    """A1: catches black frames, missing subject, fully blown renders."""
    std, mean = float(img.std()), float(img.mean())
    ok = std > min_std and mean_lo < mean < mean_hi
    return _result("A1", ok, std, min_std,
                   details=f"std={std:.4f} mean={mean:.4f}")


def check_backface_fraction(img: np.ndarray, max_fraction: float = 0.001) -> dict:
    """A2: on the backface-debug pass, pure red pixels = backfacing surface.
    Makes inverted normals a scripted catch; vision R3 is the backstop."""
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    frac = float(((r > 0.9) & (g < 0.1) & (b < 0.1)).mean())
    return _result("A2", frac <= max_fraction, frac, max_fraction)


def check_silhouette_area(img: np.ndarray, lo: float = 0.05, hi: float = 0.85) -> dict:
    """A3: white-on-black silhouette pass — subject present and framed sanely."""
    frac = float((img.mean(axis=-1) > 0.5).mean())
    return _result("A3", lo <= frac <= hi, frac, lo,
                   details=f"white fraction {frac:.3f}, sane range [{lo},{hi}]")


def check_clipping(img: np.ndarray, max_fraction: float = 0.02) -> dict:
    """A4 (warn): fraction of fully blown-out pixels in neutrally lit views."""
    frac = float((img >= 254 / 255).all(axis=-1).mean())
    return _result("A4", frac <= max_fraction, frac, max_fraction, severity="warn")


def check_edge_wrap(img: np.ndarray, axis: int, max_ratio: float = 1.5) -> dict:
    """S19a: tiling wrap continuity. In a seamless texture the opposite edges
    are wrap-ADJACENT texels, not duplicates — so an absolute edge-difference
    threshold rejects any texture with high-frequency detail even when it tiles
    perfectly. The correct test is relative: the gradient across the wrap seam
    (last row/col -> first row/col) must be statistically indistinguishable
    from interior adjacent-texel gradients. axis=0 top/bottom, axis=1 left/right.
    """
    grey = img.mean(axis=-1)
    grad = np.abs(np.diff(grey, axis=axis))            # interior adjacent pairs
    per_pair = grad.mean(axis=1 - axis)                # mean gradient per pair line
    baseline = float(np.percentile(per_pair, 95)) + 1e-6
    seam = float(np.abs(grey.take(0, axis=axis)
                        - grey.take(-1, axis=axis)).mean())
    ratio = seam / baseline
    return _result("S19a", ratio <= max_ratio, ratio, max_ratio,
                   details=f"axis={axis} seam={seam:.5f} interior_p95={baseline:.5f}")


def check_rolled_seam(img: np.ndarray, max_ratio: float = 1.5,
                      window: int = 2) -> dict:
    """S19b: roll the image 50% on both axes so the former borders land
    mid-image, then compare gradients in a small window around those lines to
    the interior p95 — catches 'edge texels were forged/blended to match but
    the pattern breaks just inside the border'."""
    h, w = img.shape[:2]
    rolled = np.roll(np.roll(img, h // 2, axis=0), w // 2, axis=1)
    grey = rolled.mean(axis=-1)
    gy = np.abs(np.diff(grey, axis=0)).mean(axis=1)    # (h-1,) per row-pair
    gx = np.abs(np.diff(grey, axis=1)).mean(axis=0)    # (w-1,) per col-pair
    baseline = float(np.percentile(np.concatenate([gy, gx]), 95)) + 1e-6
    rows = range(max(0, h // 2 - 1 - window), min(len(gy), h // 2 + window))
    cols = range(max(0, w // 2 - 1 - window), min(len(gx), w // 2 + window))
    seam = max(float(gy[list(rows)].max()), float(gx[list(cols)].max()))
    ratio = seam / baseline
    return _result("S19b", ratio <= max_ratio, ratio, max_ratio,
                   details=f"seam_window_max={seam:.5f} interior_p95={baseline:.5f}")


def check_normal_map_stats(img: np.ndarray, min_mean_blue: float = 0.7,
                           min_blue_up_fraction: float = 0.99,
                           rg_tolerance: float = 0.08) -> dict:
    """S17: tangent normal map sanity — OpenGL +Y, mostly up-facing, centered RG."""
    r, g, b = (float(img[..., i].mean()) for i in range(3))
    up = float((img[..., 2] >= 0.5).mean())
    ok = (b >= min_mean_blue and up >= min_blue_up_fraction
          and abs(r - 0.5) <= rg_tolerance and abs(g - 0.5) <= rg_tolerance)
    return _result("S17", ok, b, min_mean_blue,
                   details=f"meanRGB=({r:.3f},{g:.3f},{b:.3f}) upFrac={up:.4f}")


def check_albedo_stats(img: np.ndarray, lum_lo: float = 0.02, lum_hi: float = 0.98,
                       min_std: float = 0.01, flat_color_ok: bool = False) -> dict:
    """S16: albedo not black / not blown / not accidentally flat.
    flat_color_ok: recipe-declared flat-color themes skip the variance test."""
    lum = float(img.mean())
    std = float(img.std())
    ok = lum_lo <= lum <= lum_hi and (flat_color_ok or std > min_std)
    return _result("S16", ok, lum, lum_lo,
                   details=f"lum={lum:.4f} std={std:.4f} flat_ok={flat_color_ok}")
