"""Fix planner: defects -> fix_plan (spec 16.1-16.4).

Deterministic policy, in order of preference per defect:
1. table fix (fixes.json), unless that exact (check_id, defect_type) has already
   been table-fixed twice on this asset (repeat offense) — then escalate;
2. escalation per the iteration ladder: subcomponent regen (iter >= 3);
3. LLM param patch as the fallback action — the planner only *emits the request*
   (type=llm_param_patch); the model call and patch clamping happen in the
   applicator, keeping this module pure and unit-testable;
4. full regen (new seed) when every defect is out of targeted options and the
   ladder window permits it — the "different class of action" rule.

The planner never mutates pipeline state; it returns a fix_plan dict conforming
to fix_plan.schema.json (after strip_internal).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from assetpipe.contracts import Contracts, earliest_stage
from assetpipe.vision.report import Finding


@dataclass
class LadderConfig:
    max_iterations: int = 5
    subcomponent_regen_from: int = 3   # failures at iteration >= this may sub-regen
    full_regen_from: int = 4           # failures at iteration >= this may full-regen
    full_regen_allowed: int = 1        # per asset


@dataclass
class PlannerState:
    """Per-asset planner memory across iterations."""
    table_fix_uses: dict = field(default_factory=dict)   # (check_id, defect_type) -> count
    regens_used: int = 0

    def record_plan(self, plan: dict) -> None:
        if any(a["type"] == "full_regen" for a in plan["actions"]):
            self.regens_used += 1
        for key in plan.get("_fix_by_defect", {}):
            self.table_fix_uses[key] = self.table_fix_uses.get(key, 0) + 1


def allowed_escalations(iteration: int, ladder: LadderConfig, state: PlannerState) -> set[str]:
    """Fix classes permitted for a failure produced at `iteration` (spec 16.1)."""
    allowed = {"table_fix", "param_patch", "llm_param_patch"}
    if iteration >= ladder.subcomponent_regen_from:
        allowed.add("subcomponent_regen")
    if (ladder.full_regen_from <= iteration < ladder.max_iterations
            and state.regens_used < ladder.full_regen_allowed):
        allowed.add("full_regen")
    return allowed


def _out_of_targeted_options(f: Finding, contracts: Contracts, state: PlannerState) -> bool:
    fix_id = contracts.table_fix_for(f.defect_type)
    return fix_id is None or state.table_fix_uses.get(f.key(), 0) >= 2


def plan_fixes(asset_id: str, iteration: int, defects: list[Finding],
               contracts: Contracts, ladder: LadderConfig, state: PlannerState,
               seed: int) -> dict:
    """Build the fix plan for the defects that failed `iteration`.

    `defects` = blocker findings, plus warn findings the caller wants riding
    along (spec 15.6). Raises ValueError on empty input.
    """
    if not defects:
        raise ValueError("plan_fixes called with no defects")
    allowed = allowed_escalations(iteration, ladder, state)

    # Full regen wins when nothing targeted is left for ANY defect.
    if all(_out_of_targeted_options(f, contracts, state) for f in defects) \
            and "full_regen" in allowed:
        return {
            "asset_id": asset_id,
            "for_iteration": iteration,
            "produces_iteration": iteration + 1,
            "defects_addressed": sorted({f.defect_type for f in defects}),
            "actions": [{"type": "full_regen", "new_seed": (seed + iteration) % 2**32}],
            "planner": "escalation",
            "resume_stage": "G",
            "_fix_by_defect": {},
        }

    actions: list[dict] = []
    fix_by_defect: dict = {}
    resume_stages: list[str] = []
    planner = "table"

    for f in defects:
        if not _out_of_targeted_options(f, contracts, state):
            fix_id = contracts.table_fix_for(f.defect_type)
            action = {"type": "table_fix", "fix_id": fix_id,
                      "target": f.location or f.defect_type}
            if action not in actions:                     # dedupe identical fixes
                actions.append(action)
            fix_by_defect[f.key()] = fix_id
            resume_stages.append(contracts.fixes[fix_id]["resume_stage"])
        elif "subcomponent_regen" in allowed:
            actions.append({"type": "subcomponent_regen",
                            "target": f.location or f.defect_type})
            resume_stages.append("G")
            planner = "escalation"
        else:
            actions.append({"type": "llm_param_patch",
                            "target": f.location or f.defect_type})
            resume_stages.append(contracts.resume_stage_for(f.defect_type))
            if planner == "table":
                planner = "llm"

    return {
        "asset_id": asset_id,
        "for_iteration": iteration,
        "produces_iteration": iteration + 1,
        "defects_addressed": sorted({f.defect_type for f in defects}),
        "actions": actions,
        "planner": planner,
        "resume_stage": earliest_stage(resume_stages),
        "_fix_by_defect": fix_by_defect,   # planner-internal; strip before persisting
    }


def plan_signature(plan: dict) -> tuple:
    """Order-independent identity of a plan's actions, for no-progress detection
    (spec 16.5.3): identical defect multiset + identical plan signature on two
    consecutive iterations => the loop is at a fixpoint."""
    return tuple(sorted(
        (a["type"], a.get("fix_id", ""), a.get("target", ""))
        for a in plan["actions"]))


def strip_internal(plan: dict) -> dict:
    """Remove planner-internal keys before schema validation / persistence."""
    return {k: v for k, v in plan.items() if not k.startswith("_")}


def clamp_patch(patch: list[dict], param_schema: dict, params: dict) -> list[dict]:
    """Clamp an LLM-proposed JSON Patch to the generator PARAM_SCHEMA bounds
    (spec 16.4). Ops limited to 'replace' on existing top-level params; numeric
    values clamped to [minimum, maximum]; unknown paths and wrong-typed values
    dropped. Returns the sanitized patch (possibly empty => patch was useless)."""
    props = param_schema.get("properties", {})
    safe: list[dict] = []
    for op in patch[:4]:
        if op.get("op") != "replace":
            continue
        key = op.get("path", "").lstrip("/")
        if "/" in key or key not in props or key not in params:
            continue
        spec, value = props[key], op.get("value")
        t = spec.get("type")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if t == "number":
            value = float(min(max(value, spec.get("minimum", value)),
                              spec.get("maximum", value)))
        elif t == "integer":
            value = int(min(max(value, spec.get("minimum", value)),
                            spec.get("maximum", value)))
        else:
            continue
        safe.append({"op": "replace", "path": "/" + key, "value": value})
    return safe
