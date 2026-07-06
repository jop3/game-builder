"""Fix planner: table lookup, escalation ladder, patch clamping (spec 16.1-16.4)."""
import jsonschema
import pytest

from assetpipe.contracts import Contracts
from assetpipe.fixes.planner import (LadderConfig, PlannerState, clamp_patch,
                                     plan_fixes, plan_signature, strip_internal)
from assetpipe.vision.report import Finding

C = Contracts.load()
LADDER = LadderConfig()


def f(check_id="R4", defect="VISIBLE_SEAM", severity="blocker", location="front edge"):
    return Finding(check_id=check_id, defect_type=defect, severity=severity,
                   verdict="fail", confidence=0.9, location=location)


def test_table_fix_lookup_and_schema_conformance():
    plan = plan_fixes("a", 1, [f()], C, LADDER, PlannerState(), seed=7)
    assert plan["actions"] == [{"type": "table_fix", "fix_id": "rebake_margin_x2",
                                "target": "front edge"}]
    assert plan["resume_stage"] == "X" and plan["planner"] == "table"
    jsonschema.validate(strip_internal(plan), C.fix_plan_schema)


def test_resume_stage_is_earliest_across_defects():
    plan = plan_fixes("a", 1, [f(), f("R3", "INVERTED_NORMALS")], C,
                      LADDER, PlannerState(), seed=7)
    assert plan["resume_stage"] == "M"      # M (normals fix) earlier than X (seam rebake)


def test_no_table_fix_falls_back_to_llm_before_iter3():
    plan = plan_fixes("a", 1, [f("R5", "SILHOUETTE_MISMATCH")], C,
                      LADDER, PlannerState(), seed=7)
    assert plan["actions"][0]["type"] == "llm_param_patch"
    assert plan["planner"] == "llm"
    assert plan["resume_stage"] == "G"


def test_subcomponent_regen_unlocked_at_iter3():
    plan = plan_fixes("a", 3, [f("R5", "SILHOUETTE_MISMATCH")], C,
                      LADDER, PlannerState(), seed=7)
    assert plan["actions"][0]["type"] == "subcomponent_regen"
    assert plan["planner"] == "escalation"


def test_repeat_offense_escalates_instead_of_repeating_table_fix():
    state = PlannerState()
    for it in (1, 2):
        state.record_plan(plan_fixes("a", it, [f()], C, LADDER, state, seed=7))
    plan3 = plan_fixes("a", 3, [f()], C, LADDER, state, seed=7)
    assert all(a["type"] != "table_fix" for a in plan3["actions"])


def test_full_regen_window_and_single_use():
    state = PlannerState()
    for it in (1, 2):
        state.record_plan(plan_fixes("a", it, [f()], C, LADDER, state, seed=7))
    state.table_fix_uses[("R4", "VISIBLE_SEAM")] = 2      # burned
    # iter 3: full regen not yet allowed -> subcomponent regen
    p3 = plan_fixes("a", 3, [f()], C, LADDER, state, seed=7)
    assert p3["actions"][0]["type"] == "subcomponent_regen"
    # iter 4: full regen allowed, deterministic new seed
    p4 = plan_fixes("a", 4, [f()], C, LADDER, state, seed=7)
    assert p4["actions"] == [{"type": "full_regen", "new_seed": 11}]
    jsonschema.validate(strip_internal(p4), C.fix_plan_schema)
    state.record_plan(p4)
    # regen budget spent -> never again, even in-window
    p4b = plan_fixes("a", 4, [f()], C, LADDER, state, seed=7)
    assert all(a["type"] != "full_regen" for a in p4b["actions"])


def test_no_full_regen_at_final_iteration():
    state = PlannerState()
    state.table_fix_uses[("R4", "VISIBLE_SEAM")] = 2
    p5 = plan_fixes("a", 5, [f()], C, LADDER, state, seed=7)   # max_iterations == 5
    assert all(a["type"] != "full_regen" for a in p5["actions"])


def test_plan_signature_is_order_independent():
    a = plan_fixes("a", 1, [f(), f("R3", "INVERTED_NORMALS")], C,
                   LADDER, PlannerState(), seed=7)
    b = plan_fixes("a", 1, [f("R3", "INVERTED_NORMALS"), f()], C,
                   LADDER, PlannerState(), seed=7)
    assert plan_signature(a) == plan_signature(b)


def test_identical_defects_dedupe_to_one_action():
    plan = plan_fixes("a", 1, [f(location="edge"), f(location="edge")], C,
                      LADDER, PlannerState(), seed=7)
    assert len(plan["actions"]) == 1


def test_empty_defects_rejected():
    with pytest.raises(ValueError):
        plan_fixes("a", 1, [], C, LADDER, PlannerState(), seed=7)


PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "greeble_density": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "panel_lines": {"type": "integer", "minimum": 0, "maximum": 6},
        "name": {"type": "string"},
    },
}
PARAMS = {"greeble_density": 0.4, "panel_lines": 2, "name": "crate"}


def test_clamp_patch_bounds_types_and_paths():
    raw = [
        {"op": "replace", "path": "/greeble_density", "value": 7.5},   # clamp to 1.0
        {"op": "replace", "path": "/panel_lines", "value": -3},        # clamp to 0
        {"op": "replace", "path": "/name", "value": "hack"},           # non-numeric: drop
        {"op": "replace", "path": "/no_such", "value": 1},             # unknown: drop
        {"op": "add", "path": "/greeble_density", "value": 1},         # op: drop
        {"op": "replace", "path": "/nested/deep", "value": 1},         # nested: drop
        {"op": "replace", "path": "/panel_lines", "value": True},      # bool: drop
    ]
    safe = clamp_patch(raw, PARAM_SCHEMA, PARAMS)
    assert safe == [
        {"op": "replace", "path": "/greeble_density", "value": 1.0},
        {"op": "replace", "path": "/panel_lines", "value": 0},
    ]


def test_clamp_patch_caps_at_four_ops():
    raw = [{"op": "replace", "path": "/greeble_density", "value": 0.5}] * 6
    assert len(clamp_patch(raw, PARAM_SCHEMA, PARAMS)) <= 4


def test_warn_actions_cannot_pull_resume_earlier_than_blockers():
    """A warn-driven fix wanting an earlier resume stage than any blocker fix
    is deferred, not planned: an earlier resume regenerates over the artifacts
    the blocker fixes repair, so the blockers recur unchanged and the loop
    no-progress-exits (observed end-to-end on real Blender 4.2)."""
    blocker = f()                                             # VISIBLE_SEAM -> X
    warn = f("S9", "SELF_INTERSECTION", severity="warn")      # llm patch -> G
    plan = plan_fixes("a", 1, [blocker, warn], C, LADDER, PlannerState(), seed=7)
    assert plan["resume_stage"] == "X"
    assert all(a["type"] == "table_fix" for a in plan["actions"])
    assert [d["deferred_for"] for d in plan["_deferred_warn_actions"]] == \
        ["SELF_INTERSECTION"]
    jsonschema.validate(strip_internal(plan), C.fix_plan_schema)


def test_warn_actions_at_or_after_blocker_resume_still_ride_along():
    blocker = f("R3", "INVERTED_NORMALS")                     # recalc fix -> M resume
    warn = f("S12e", "BAKE_MARGIN_LOW", severity="warn")      # reunwrap fix -> M
    plan = plan_fixes("a", 1, [blocker, warn], C, LADDER, PlannerState(), seed=7)
    assert plan["resume_stage"] == "M"
    assert {a["fix_id"] for a in plan["actions"]} == {"recalc_normals", "reunwrap_margin"}
    assert plan["_deferred_warn_actions"] == []


def test_blockers_pulling_resume_earlier_is_still_allowed():
    plan = plan_fixes("a", 1, [f(), f("R3", "INVERTED_NORMALS")], C,
                      LADDER, PlannerState(), seed=7)
    assert plan["resume_stage"] == "M"      # both blockers: earliest wins as before
