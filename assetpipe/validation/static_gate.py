"""Orchestrator-side half of Stage V1, the static validation gate (spec 13,
README item 2). ``static_checks_mesh.py`` (in Blender) covers S1-S13 on the
pre-export scene; this module covers S14 onward on the exported artifacts:

- S14/S15/S16/S17: texture compliance on ``iter_dir/maps/*.png`` via Pillow +
  the existing :mod:`assetpipe.validation.image_checks` functions.
- S19a/S19b: tiling continuity, only for ``tiling_texture_set`` requests or
  ``loop_x``-flagged material layers.
- S20a: the external Khronos ``gltf_validator`` CLI.
- S20b-S20d: reuses :func:`assetpipe.validation.glb.run_glb_checks` wholesale.

Every failed check (from ``mesh_report.json`` *or* the checks computed here)
is mapped to a defect-taxonomy id and turned into a
:class:`~assetpipe.vision.report.Finding`; :func:`run_static_gate` writes
``iter_dir/static_report.json`` in the spec 13.6 shape and returns
``(StageResult, all_check_dicts)`` so the orchestrator gets both the
loop-facing verdict and the full check list for logging/diagnosis.

**Deviation (documented):** S20a is checked by attempting to invoke the
``gltf_validator`` binary through the injected ``runner``. In this
environment (and in CI containers without the Khronos toolchain installed)
the binary is absent; rather than treating that as a validation failure (or
silently skipping the check without a trace), a missing binary is recorded as
a check entry with ``verdict: "skip"`` and a ``details`` string explaining why
-- it counts toward neither blockers nor warns, so it never gates the loop,
but it is visible in ``static_report.json`` and in any human/model audit of
why S20a wasn't enforced on this run.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from assetpipe.contracts import Contracts
from assetpipe.loop import StageResult
from assetpipe.validation import glb as glb_checks
from assetpipe.validation.image_checks import (check_albedo_stats, check_edge_wrap,
                                               check_normal_map_stats,
                                               check_rolled_seam)
from assetpipe.vision.report import Finding

# check_id -> defect_type, for checks computed in *this* module (S14-S20),
# whose result dicts (image_checks._result / glb._result) never carry a
# "defect" key themselves. mesh_report.json entries (S1-S13, produced by
# assetpipe/blender_scripts/static_checks_mesh.py) are expected to already
# carry "defect" per the spec 13.6 example; this table is also consulted as a
# defensive fallback for any that don't.
CHECK_DEFECT = {
    "S1": "NON_MANIFOLD", "S2": "DEGENERATE_FACES", "S3": "DEGENERATE_FACES",
    "S4": "LOOSE_GEOMETRY", "S5": "INVERTED_NORMALS", "S6": "BBOX_OUT_OF_RANGE",
    "S7": "OVER_BUDGET", "S8": "BBOX_OUT_OF_RANGE", "S9": "SELF_INTERSECTION",
    "S10": "SOCKET_OFF_GRID", "S11": "SKIN_WEIGHT_INVALID",
    "S12a": "UV_MISSING", "S12b": "UV_OVERLAP", "S12c": "UV_OUT_OF_BOUNDS",
    "S12d": "UV_STRETCH", "S12e": "BAKE_MARGIN_LOW",
    "S14": "TEX_RESOLUTION_INVALID", "S15": "TEX_FORMAT_INVALID",
    "S16": "BLACK_SURFACE", "S17": "NORMAL_MAP_INVALID",
    "S19a": "TILING_SEAM", "S19b": "TILING_SEAM",
    "S20a": "GLTF_INVALID", "S20a_container": "GLTF_INVALID",
    "S20b": "GLTF_EXTENSION_FORBIDDEN", "S20c": "GLTF_INVALID",
    "S20c_tangents": "GLTF_INVALID", "S20d": "FILE_TOO_LARGE",
}

_MAP_NAMES = ("albedo", "normal", "orm", "emissive")


def _is_pow2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def _check_resolution(map_name: str, img: Image.Image, budget_px: int) -> dict:
    """S14: square, power-of-two, >= 64px, <= profile budget."""
    w, h = img.size
    ok = w == h and _is_pow2(w) and 64 <= w <= budget_px
    return {"check_id": "S14", "verdict": "pass" if ok else "fail", "severity": "blocker",
            "measured": [w, h], "threshold": budget_px,
            "details": f"{map_name}: {w}x{h}, budget<= {budget_px}px, square+PoT+>=64 required"}


def _check_format(map_name: str, img: Image.Image) -> dict:
    """S15: 8-bit PNG; no alpha channel on normal/orm."""
    problems = []
    if (img.format or "PNG") != "PNG":
        problems.append(f"format {img.format} != PNG")
    sixteen_bit_modes = ("I", "I;16", "I;16B", "F")
    if img.mode in sixteen_bit_modes:
        problems.append(f"mode {img.mode} is not 8-bit")
    if map_name in ("normal", "orm") and img.mode == "RGBA":
        problems.append(f"{map_name} must not carry an alpha channel")
    return {"check_id": "S15", "verdict": "pass" if not problems else "fail",
            "severity": "blocker", "measured": img.mode, "threshold": "8-bit, no stray alpha",
            "details": "; ".join(problems)}


def _load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB")).astype(np.float64) / 255.0


def _wants_tiling_checks(request: dict) -> tuple[bool, tuple[int, ...]]:
    """Returns (should_check, axes). Full tiling_texture_set requests get
    both axes (spec 13.4: "both axes for textures"); a material layer
    declared ``loop_x`` gets X only ("X only for loop_x layers")."""
    if request.get("category") == "tiling_texture_set":
        return True, (0, 1)
    layers = (request.get("material_overrides") or {}).get("layers", [])
    if isinstance(layers, list) and any(
            isinstance(l, dict) and l.get("loop_x") for l in layers):
        return True, (1,)
    return False, ()


def _run_gltf_validator(glb_path: Path, runner: Callable, binary: str = "gltf_validator") -> dict:
    try:
        proc = runner([binary, "-o", str(glb_path)], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return {"check_id": "S20a", "verdict": "skip", "severity": "blocker",
                "measured": None, "threshold": "0 errors, 0 warnings",
                "details": (f"{binary!r} not found on PATH -- this environment has no "
                            "Khronos glTF-Validator installed; recorded as skip, not "
                            "pass/fail (see static_gate.py module docstring)")}
    except subprocess.TimeoutExpired:
        return {"check_id": "S20a", "verdict": "fail", "severity": "blocker",
                "measured": "timeout", "threshold": "0 errors, 0 warnings",
                "details": f"{binary} did not finish within its timeout"}
    try:
        report = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return {"check_id": "S20a", "verdict": "fail", "severity": "blocker",
                "measured": (proc.stdout or "")[:500], "threshold": "0 errors, 0 warnings",
                "details": f"could not parse {binary} JSON output (exit {proc.returncode})"}
    issues = report.get("issues", {})
    n_errors = issues.get("numErrors", 0)
    n_warnings = issues.get("numWarnings", 0)
    ok = n_errors == 0 and n_warnings == 0
    return {"check_id": "S20a", "verdict": "pass" if ok else "fail", "severity": "blocker",
            "measured": {"errors": n_errors, "warnings": n_warnings},
            "threshold": "0 errors, 0 warnings",
            "details": f"gltf_validator: {n_errors} error(s), {n_warnings} warning(s)"}


def _to_finding(check: dict) -> Finding:
    defect = check.get("defect") or CHECK_DEFECT.get(check["check_id"], "INFRA_ERROR")
    return Finding(check_id=check["check_id"], defect_type=defect,
                  severity=check.get("severity", "blocker"), verdict="fail",
                  confidence=1.0, evidence_views=[], location=check.get("details", ""),
                  description=check.get("details", ""))


def run_static_gate(iter_dir: Path, request: dict, contracts: Contracts, config: dict,
                    expected: dict, runner: Callable = subprocess.run,
                    ) -> tuple[StageResult, list[dict]]:
    """Run S14 onward and write ``iter_dir/static_report.json`` (spec 13.6).

    ``expected`` is the glb inventory-check dict for
    :func:`assetpipe.validation.glb.check_inventory` (mesh_names,
    material_count, image_count, lod_names -- all optional).
    """
    iter_dir = Path(iter_dir)
    v = config.get("validation", {})
    started = time.monotonic()
    all_checks: list[dict] = []
    timings: dict[str, float] = {}

    mesh_report_path = iter_dir / "mesh_report.json"
    if mesh_report_path.exists():
        try:
            mesh_checks = json.loads(mesh_report_path.read_text())
        except json.JSONDecodeError:
            mesh_checks = []
        if isinstance(mesh_checks, dict):
            mesh_checks = mesh_checks.get("checks", [])
        all_checks.extend(mesh_checks)

    category = request.get("category")
    platform_profile = request.get("platform_profile")
    profile = contracts.profile(platform_profile) if platform_profile else {}
    budget_override = (request.get("budget_overrides") or {}).get("max_texture_px")

    params_path = iter_dir / "params.json"
    params = json.loads(params_path.read_text()) if params_path.exists() else {}
    flat_color = bool(params.get("flat_color")
                      or (request.get("material_overrides") or {}).get("flat_color"))

    maps_dir = iter_dir / "maps"
    for map_name in _MAP_NAMES:
        path = maps_dir / f"{map_name}.png"
        if not path.exists():
            continue  # presence-driven: a recipe legitimately omits some maps
        t0 = time.monotonic()
        with Image.open(path) as img:
            texture_budgets = profile.get("textures", {}).get(category, {})
            budget = texture_budgets.get(map_name, 1024)
            if budget_override is not None:
                budget = min(budget, budget_override)
            all_checks.append(_check_resolution(map_name, img, budget))
            all_checks.append(_check_format(map_name, img))

        if map_name == "albedo":
            rgb = _load_rgb(path)
            all_checks.append(check_albedo_stats(
                rgb, lum_lo=v.get("s16_albedo_lum_range", [0.02, 0.98])[0],
                lum_hi=v.get("s16_albedo_lum_range", [0.02, 0.98])[1],
                min_std=v.get("s16_albedo_min_std", 0.01), flat_color_ok=flat_color))
        elif map_name == "normal":
            rgb = _load_rgb(path)
            all_checks.append(check_normal_map_stats(
                rgb, min_mean_blue=v.get("s17_normal_min_mean_blue", 0.7),
                min_blue_up_fraction=v.get("s17_normal_min_up_fraction", 0.99)))
        timings[f"maps:{map_name}"] = time.monotonic() - t0

    do_tiling, axes = _wants_tiling_checks(request)
    if do_tiling:
        for map_name in _MAP_NAMES:
            path = maps_dir / f"{map_name}.png"
            if not path.exists():
                continue
            rgb = _load_rgb(path)
            for axis in axes:
                all_checks.append(check_edge_wrap(
                    rgb, axis, max_ratio=v.get("s19a_wrap_seam_ratio_max", 1.5)))
            if axes == (0, 1):  # S19b (both-axes roll) only meaningful for a
                                 # full tiling texture set, not a loop_x layer
                all_checks.append(check_rolled_seam(
                    rgb, max_ratio=v.get("s19b_rolled_seam_ratio_max", 1.5)))

    glb_path = iter_dir / f"{request.get('asset_id')}.glb"
    if not glb_path.exists():
        alt = iter_dir / "asset.glb"
        if alt.exists():
            glb_path = alt

    t0 = time.monotonic()
    all_checks.append(_run_gltf_validator(glb_path, runner))
    timings["S20a"] = time.monotonic() - t0

    if glb_path.exists():
        max_bytes = profile.get("file_bytes", {}).get(category, 2 ** 63)
        if (request.get("budget_overrides") or {}).get("max_file_bytes"):
            max_bytes = min(max_bytes, request["budget_overrides"]["max_file_bytes"])
        whitelist = frozenset(v.get("gltf_extension_whitelist",
                                    list(glb_checks.CANONICAL_EXTENSION_WHITELIST)))
        t0 = time.monotonic()
        all_checks.extend(glb_checks.run_glb_checks(glb_path, expected, max_bytes, whitelist))
        timings["S20b-d"] = time.monotonic() - t0
    else:
        all_checks.append({"check_id": "S20a_container", "verdict": "fail", "severity": "blocker",
                           "measured": None, "threshold": "a .glb file must exist",
                           "details": f"no exported glb found at {glb_path}"})

    failed = [c for c in all_checks if c.get("verdict") == "fail"]
    blockers = [_to_finding(c) for c in failed if c.get("severity", "blocker") == "blocker"]
    warns = [_to_finding(c) for c in failed if c.get("severity") == "warn"]
    passed = not blockers

    report = {
        "asset_id": request.get("asset_id"), "iteration": request.get("_iteration"),
        "stage": "V1", "verdict": "pass" if passed else "fail",
        "checks": all_checks, "timings_s": {k: round(t, 4) for k, t in timings.items()},
        "blender_version": config.get("toolchain", {}).get("blender", "unknown"),
        "toolchain_hash": str(hash(json.dumps(config.get("toolchain", {}), sort_keys=True))),
    }
    (iter_dir / "static_report.json").write_text(json.dumps(report, indent=2, default=str))

    return StageResult(passed=passed, blockers=blockers, warns=warns), all_checks
