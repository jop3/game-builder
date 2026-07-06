"""Fix-plan action applicator (spec 16.3-16.4, README item 5).

`apply_fix_plan` executes a validated `fix_plan.json`'s `actions` list:

- `table_fix`: resolves `Contracts.fixes[fix_id]["implementation"]`. Dotted
  paths under ``assetpipe.blender_scripts.`` are the in-Blender fix scripts
  (spec 16.2 table) -- this module does not (and cannot, no bpy here) run
  them, so they are forwarded untouched in `ApplyResult.blender_actions` for
  stage code to hand to the Blender subprocess. Everything else resolves to a
  pure-python implementation in `assetpipe.fixes.param_fixes` or
  `assetpipe.fixes.map_fixes`, called as `fn(ctx, action) -> dict`.
- `param_patch` / `llm_param_patch`: sanitized via
  `assetpipe.fixes.planner.clamp_patch` against `ctx.param_schema`, then
  written into `params.json` in `ctx.iter_dir`. `llm_param_patch` additionally
  makes the (injected) model call and retries exactly once on an empty/invalid
  patch (spec 16.4) before failing.
- `subcomponent_regen`: forwarded like a Blender action -- the loop/stage code
  own re-running a generator sub-part.
- `full_regen`: never executed here. The loop is supposed to intercept
  `full_regen` before a plan ever reaches the applicator (it owns re-seeding
  and re-running Stage G from scratch); this function is defensive and simply
  fails the action with a reason so a stray plan can't silently no-op.

Nothing here reads or writes outside `ctx.iter_dir`.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import jsonschema

from assetpipe.contracts import Contracts
from assetpipe.fixes.planner import clamp_patch

BLENDER_PREFIX = "assetpipe.blender_scripts."


@dataclass
class FixContext:
    """Everything a fix implementation needs, scoped to one iteration."""

    iter_dir: Path            # iteration directory containing params.json, maps/
    request: dict
    contracts: Contracts
    config: dict               # merged pipeline config (config/defaults.yaml shape)
    param_schema: dict         # the resolved generator's PARAM_SCHEMA
    # receives {"defects": [...], "params": {...}, "param_schema": {...},
    # "target": str} and returns a raw JSON Patch list; the model call itself
    # lives elsewhere (vision/inspector.py sibling for text-only calls) -- tests
    # inject fakes here.
    llm_patch_fn: Callable[[dict], list[dict]] | None = None

    @property
    def params_path(self) -> Path:
        return self.iter_dir / "params.json"

    def read_params(self) -> dict:
        return json.loads(self.params_path.read_text())

    def write_params(self, params: dict) -> None:
        self.params_path.write_text(json.dumps(params, indent=2))


@dataclass
class ApplyResult:
    applied: list[dict] = field(default_factory=list)          # ran python-side
    blender_actions: list[dict] = field(default_factory=list)  # forward to Blender
    failed: list[dict] = field(default_factory=list)           # could not apply
    params_changed: bool = False


def _resolve(dotted: str):
    module_name, _, fn_name = dotted.rpartition(".")
    module = importlib.import_module(module_name)
    return getattr(module, fn_name)


def _apply_patch_ops(params: dict, patch: list[dict]) -> dict:
    """Apply an already-clamped replace-only patch in place; return the
    before/after note (spec: fixes return {"changed": {param: [old, new]}})."""
    changed = {}
    for op in patch:
        key = op["path"].lstrip("/")
        changed[key] = [params.get(key), op["value"]]
        params[key] = op["value"]
    return changed


def _do_direct_patch(ctx: FixContext, patch: list[dict]) -> dict | None:
    """Clamp + apply a patch straight from an action (param_patch path).
    Returns the changed-note, or None if nothing survived clamping."""
    params = ctx.read_params()
    safe = clamp_patch(patch, ctx.param_schema, params)
    if not safe:
        return None
    changed = _apply_patch_ops(params, safe)
    ctx.write_params(params)
    return changed


def _dispatch_table_fix(action: dict, ctx: FixContext, result: ApplyResult) -> None:
    fix_id = action.get("fix_id")
    fix = ctx.contracts.fixes.get(fix_id)
    if fix is None:
        result.failed.append({**action, "reason": f"unknown fix_id {fix_id!r}"})
        return
    impl = fix["implementation"]
    if impl.startswith(BLENDER_PREFIX):
        result.blender_actions.append(action)
        return
    try:
        fn = _resolve(impl)
    except (ImportError, AttributeError) as exc:
        result.failed.append({**action, "reason": f"could not resolve {impl}: {exc}"})
        return
    try:
        note = fn(ctx, action) or {}
    except Exception as exc:  # a broken fix must not crash the whole plan
        result.failed.append({**action, "reason": f"{impl} raised: {exc}"})
        return
    result.applied.append({**action, **note})
    if note.get("changed"):
        result.params_changed = True


def _dispatch_param_patch(action: dict, ctx: FixContext, result: ApplyResult) -> None:
    changed = _do_direct_patch(ctx, action.get("patch", []))
    if changed is None:
        result.failed.append({**action, "reason": "patch empty after clamping"})
        return
    result.applied.append({**action, "changed": changed})
    result.params_changed = True


def _dispatch_llm_param_patch(action: dict, plan: dict, ctx: FixContext,
                              result: ApplyResult) -> None:
    if ctx.llm_patch_fn is None:
        result.failed.append({**action, "reason": "no llm_patch_fn configured"})
        return
    params = ctx.read_params()
    request_payload = {
        "defects": plan.get("defects_addressed", []),
        "params": params,
        "param_schema": ctx.param_schema,
        "target": action.get("target"),
    }
    reason = "invalid patch after retry"
    for _attempt in range(2):  # spec 16.4: invalid patch -> retry once -> fall through
        try:
            raw_patch = ctx.llm_patch_fn(request_payload)
        except Exception as exc:
            reason = f"llm_patch_fn raised: {exc}"
            continue
        safe = clamp_patch(raw_patch, ctx.param_schema, params)
        if safe:
            changed = _apply_patch_ops(params, safe)
            ctx.write_params(params)
            result.applied.append({**action, "changed": changed})
            result.params_changed = True
            return
        reason = "patch empty after clamping"
    result.failed.append({**action, "reason": reason})


def apply_fix_plan(plan: dict, ctx: FixContext) -> ApplyResult:
    """Execute `plan["actions"]` against `ctx`. Raises ValueError if `plan`
    does not conform to `fix_plan.schema.json`."""
    try:
        jsonschema.validate(plan, ctx.contracts.fix_plan_schema)
    except jsonschema.exceptions.ValidationError as exc:
        raise ValueError(f"invalid fix_plan: {exc.message}") from exc

    result = ApplyResult()
    for action in plan["actions"]:
        kind = action["type"]
        if kind == "table_fix":
            _dispatch_table_fix(action, ctx, result)
        elif kind == "param_patch":
            _dispatch_param_patch(action, ctx, result)
        elif kind == "llm_param_patch":
            _dispatch_llm_param_patch(action, plan, ctx, result)
        elif kind == "subcomponent_regen":
            result.blender_actions.append(action)
        elif kind == "full_regen":
            # The loop must intercept full_regen before apply_fix_plan runs;
            # being defensive here rather than silently mis-executing it.
            result.failed.append({**action, "reason": "handled by loop"})
        else:  # pragma: no cover - unreachable once schema enum holds
            result.failed.append({**action, "reason": f"unknown action type {kind!r}"})
    return result
