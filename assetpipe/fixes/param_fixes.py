"""Pure-python param fixes (spec 16.2 table).

Each function is a table-fix `implementation` resolved and invoked by
`assetpipe.fixes.apply.apply_fix_plan` as `fn(ctx, action) -> dict`. They edit
`params.json` under `ctx.iter_dir` directly (not via JSON Patch -- these are
code fixes, not model output) and clamp every new value to `ctx.param_schema`
bounds, exactly like `planner.clamp_patch` does for LLM patches. Each returns
`{"changed": {param: [old, new], ...}}` for the applied-action note.
"""
from __future__ import annotations

import json
from fnmatch import fnmatch

SIZE_PATTERNS = ("*_m", "*width*", "*height*", "*depth*", "*radius*", "*scale*")
_NUMERIC_TYPES = ("number", "integer")


def _params_path(ctx):
    return ctx.iter_dir / "params.json"


def _load(ctx) -> dict:
    return json.loads(_params_path(ctx).read_text())


def _save(ctx, params: dict) -> None:
    _params_path(ctx).write_text(json.dumps(params, indent=2))


def _clamp(value, spec: dict):
    t = spec.get("type")
    if t not in _NUMERIC_TYPES:
        return value
    lo, hi = spec.get("minimum"), spec.get("maximum")
    if lo is not None:
        value = max(value, lo)
    if hi is not None:
        value = min(value, hi)
    return int(round(value)) if t == "integer" else float(value)


def _numeric_params(ctx, params: dict):
    """Yield (key, value, spec) for params that are numeric and schema-known."""
    props = ctx.param_schema.get("properties", {})
    for key, value in params.items():
        spec = props.get(key)
        if spec is not None and spec.get("type") in _NUMERIC_TYPES:
            yield key, value, spec


def rescale_params(ctx, action: dict) -> dict:
    """`rescale_params` / SCALE_IMPLAUSIBLE: move every size-ish param
    (`*_m`, width/height/depth/radius/scale) halfway toward its schema
    `default` (spec: "toward recipe default bbox midpoint"), clamped."""
    params = _load(ctx)
    changed = {}
    for key, value, spec in _numeric_params(ctx, params):
        if not any(fnmatch(key, pat) for pat in SIZE_PATTERNS):
            continue
        default = spec.get("default")
        if default is None:
            continue
        new = _clamp((value + default) / 2, spec)
        if new != value:
            changed[key] = [value, new]
            params[key] = new
    _save(ctx, params)
    return {"changed": changed}


def reduce_emissive(ctx, action: dict) -> dict:
    """`reduce_emissive` / CLIPPED_EMISSIVE: multiply every emissive
    strength/intensity param by 0.6 (spec: "reduce 40%"), clamped."""
    params = _load(ctx)
    changed = {}
    for key, value, spec in _numeric_params(ctx, params):
        kl = key.lower()
        if "emissive" not in kl or not ("strength" in kl or "intensity" in kl):
            continue
        new = _clamp(value * 0.6, spec)
        if new != value:
            changed[key] = [value, new]
            params[key] = new
    _save(ctx, params)
    return {"changed": changed}


def pole_fade(ctx, action: dict) -> dict:
    """`pole_fade` / POLE_PINCH: switch `pole_treatment` to "fade" if the
    schema declares that param."""
    props = ctx.param_schema.get("properties", {})
    changed = {}
    if "pole_treatment" in props:
        params = _load(ctx)
        old = params.get("pole_treatment")
        if old != "fade":
            params["pole_treatment"] = "fade"
            changed["pole_treatment"] = [old, "fade"]
            _save(ctx, params)
    return {"changed": changed}


def resnap_sky(ctx, action: dict) -> dict:
    """`resnap_sky` / TILING_SEAM (skybox): round every `*_period*` /
    `*_scale*` numeric param to the nearest integer >= 1, clamped."""
    params = _load(ctx)
    changed = {}
    for key, value, spec in _numeric_params(ctx, params):
        kl = key.lower()
        if not (fnmatch(kl, "*period*") or fnmatch(kl, "*scale*")):
            continue
        new = _clamp(max(1, round(value)), spec)
        if new != value:
            changed[key] = [value, new]
            params[key] = new
    _save(ctx, params)
    return {"changed": changed}
