"""Fix-plan applicator: dispatch, clamping, retry-once, and isolation from
anything outside ctx.iter_dir (spec 16.3-16.4)."""
import json

import pytest

from assetpipe.contracts import Contracts
from assetpipe.fixes.apply import ApplyResult, FixContext, apply_fix_plan

C = Contracts.load()

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "width_m": {"type": "number", "minimum": 0.1, "maximum": 5.0, "default": 1.0},
        "greeble_density": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "panel_lines": {"type": "integer", "minimum": 0, "maximum": 6},
        "emissive_strength": {"type": "number", "minimum": 0.0, "maximum": 5.0},
        "pole_treatment": {"type": "string"},
        "pattern_scale": {"type": "number", "minimum": 1.0, "maximum": 50.0},
    },
}
PARAMS = {
    "width_m": 3.0,
    "greeble_density": 0.9,
    "panel_lines": 2,
    "emissive_strength": 2.0,
    "pole_treatment": "hard",
    "pattern_scale": 7.3,
}


def make_ctx(tmp_path, llm_patch_fn=None):
    iter_dir = tmp_path / "iter_2"
    iter_dir.mkdir()
    (iter_dir / "params.json").write_text(json.dumps(PARAMS))
    request = {"asset_id": "widget_01", "category": "prop_small",
               "platform_profile": "web", "seed": 7}
    return FixContext(iter_dir=iter_dir, request=request, contracts=C, config={},
                       param_schema=PARAM_SCHEMA, llm_patch_fn=llm_patch_fn)


def read_params(ctx):
    return json.loads((ctx.iter_dir / "params.json").read_text())


def make_plan(actions, defects=("SCALE_IMPLAUSIBLE",), resume_stage="G", planner="table"):
    return {
        "asset_id": "widget_01", "for_iteration": 2, "produces_iteration": 3,
        "defects_addressed": list(defects), "actions": actions,
        "planner": planner, "resume_stage": resume_stage,
    }


# ---------- plan validation ----------

def test_invalid_plan_empty_actions_raises_valueerror(tmp_path):
    ctx = make_ctx(tmp_path)
    with pytest.raises(ValueError):
        apply_fix_plan(make_plan([]), ctx)


def test_invalid_plan_missing_fix_id_raises_valueerror(tmp_path):
    ctx = make_ctx(tmp_path)
    plan = make_plan([{"type": "table_fix", "target": "hull"}])  # fix_id required
    with pytest.raises(ValueError):
        apply_fix_plan(plan, ctx)


# ---------- table_fix dispatch ----------

def test_table_fix_python_impl_rescale_params_clamped_and_written(tmp_path):
    ctx = make_ctx(tmp_path)
    action = {"type": "table_fix", "fix_id": "rescale_params", "target": "geometry"}
    result = apply_fix_plan(make_plan([action]), ctx)

    assert result.failed == []
    assert result.blender_actions == []
    assert result.applied == [{**action, "changed": {"width_m": [3.0, 2.0]}}]
    assert result.params_changed is True
    assert read_params(ctx)["width_m"] == 2.0
    # untouched params stay untouched
    assert read_params(ctx)["greeble_density"] == 0.9


def test_table_fix_reduce_emissive(tmp_path):
    ctx = make_ctx(tmp_path)
    action = {"type": "table_fix", "fix_id": "reduce_emissive", "target": "material:hull"}
    result = apply_fix_plan(make_plan([action], defects=["CLIPPED_EMISSIVE"]), ctx)

    assert result.applied[0]["changed"] == {"emissive_strength": [2.0, pytest.approx(1.2)]}
    assert read_params(ctx)["emissive_strength"] == pytest.approx(1.2)


def test_table_fix_pole_fade(tmp_path):
    ctx = make_ctx(tmp_path)
    action = {"type": "table_fix", "fix_id": "pole_fade", "target": "sky"}
    result = apply_fix_plan(make_plan([action], defects=["POLE_PINCH"], resume_stage="B"), ctx)

    assert result.applied[0]["changed"] == {"pole_treatment": ["hard", "fade"]}
    assert read_params(ctx)["pole_treatment"] == "fade"


def test_table_fix_resnap_sky(tmp_path):
    ctx = make_ctx(tmp_path)
    action = {"type": "table_fix", "fix_id": "resnap_sky", "target": "sky"}
    result = apply_fix_plan(make_plan([action], defects=["TILING_SEAM"], resume_stage="B"), ctx)

    assert result.applied[0]["changed"] == {"pattern_scale": [7.3, 7.0]}
    assert read_params(ctx)["pattern_scale"] == 7.0


def test_table_fix_blender_impl_forwarded_untouched(tmp_path):
    ctx = make_ctx(tmp_path)
    action = {"type": "table_fix", "fix_id": "cleanup_mesh", "target": "hull"}
    result = apply_fix_plan(make_plan([action], defects=["NON_MANIFOLD"]), ctx)

    assert result.blender_actions == [action]
    assert result.applied == []
    assert result.failed == []
    assert result.params_changed is False
    # never touched params.json
    assert read_params(ctx) == PARAMS


def test_table_fix_unknown_fix_id_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    action = {"type": "table_fix", "fix_id": "no_such_fix", "target": "hull"}
    result = apply_fix_plan(make_plan([action]), ctx)

    assert result.applied == []
    assert result.blender_actions == []
    assert len(result.failed) == 1
    assert result.failed[0]["fix_id"] == "no_such_fix"
    assert "reason" in result.failed[0]


# ---------- param_patch ----------

def test_param_patch_clamped_and_applied(tmp_path):
    ctx = make_ctx(tmp_path)
    action = {"type": "param_patch",
              "patch": [{"op": "replace", "path": "/greeble_density", "value": 7.5}]}
    result = apply_fix_plan(make_plan([action]), ctx)

    assert result.applied == [{**action, "changed": {"greeble_density": [0.9, 1.0]}}]
    assert result.params_changed is True
    assert read_params(ctx)["greeble_density"] == 1.0


def test_param_patch_empty_after_clamping_fails(tmp_path):
    ctx = make_ctx(tmp_path)
    action = {"type": "param_patch",
              "patch": [{"op": "replace", "path": "/no_such_param", "value": 1}]}
    result = apply_fix_plan(make_plan([action]), ctx)

    assert result.applied == []
    assert result.params_changed is False
    assert len(result.failed) == 1
    assert read_params(ctx) == PARAMS  # untouched


# ---------- llm_param_patch (retry-once semantics, spec 16.4) ----------

def test_llm_param_patch_retries_once_then_succeeds(tmp_path):
    calls = []

    def fake_llm(payload):
        calls.append(payload)
        if len(calls) == 1:
            return [{"op": "add", "path": "/greeble_density", "value": 0.5}]  # garbage: wrong op
        return [{"op": "replace", "path": "/greeble_density", "value": 0.5}]

    ctx = make_ctx(tmp_path, llm_patch_fn=fake_llm)
    action = {"type": "llm_param_patch", "target": "greeble_density"}
    result = apply_fix_plan(make_plan([action], defects=["SILHOUETTE_MISMATCH"]), ctx)

    assert len(calls) == 2
    assert result.applied == [{**action, "changed": {"greeble_density": [0.9, 0.5]}}]
    assert result.params_changed is True
    assert read_params(ctx)["greeble_density"] == 0.5
    # payload shape passed to the model call
    assert set(calls[0]) == {"defects", "params", "param_schema", "target"}
    assert calls[0]["defects"] == ["SILHOUETTE_MISMATCH"]


def test_llm_param_patch_fails_after_retry_exhausted(tmp_path):
    calls = []

    def always_garbage(payload):
        calls.append(payload)
        return [{"op": "add", "path": "/greeble_density", "value": 0.5}]

    ctx = make_ctx(tmp_path, llm_patch_fn=always_garbage)
    action = {"type": "llm_param_patch", "target": "greeble_density"}
    result = apply_fix_plan(make_plan([action], defects=["SILHOUETTE_MISMATCH"]), ctx)

    assert len(calls) == 2       # exactly one retry, not more
    assert result.applied == []
    assert result.params_changed is False
    assert len(result.failed) == 1
    assert read_params(ctx) == PARAMS  # untouched


def test_llm_param_patch_without_fn_configured_fails(tmp_path):
    ctx = make_ctx(tmp_path, llm_patch_fn=None)
    action = {"type": "llm_param_patch", "target": "greeble_density"}
    result = apply_fix_plan(make_plan([action], defects=["SILHOUETTE_MISMATCH"]), ctx)

    assert result.failed == [{**action, "reason": "no llm_patch_fn configured"}]
    assert read_params(ctx) == PARAMS


# ---------- regen action types ----------

def test_subcomponent_regen_forwarded_to_blender_actions(tmp_path):
    ctx = make_ctx(tmp_path)
    action = {"type": "subcomponent_regen", "target": "hull"}
    result = apply_fix_plan(make_plan([action], defects=["SILHOUETTE_MISMATCH"]), ctx)

    assert result.blender_actions == [action]
    assert result.applied == [] and result.failed == []
    assert read_params(ctx) == PARAMS


def test_full_regen_is_defensively_failed_not_executed(tmp_path):
    ctx = make_ctx(tmp_path)
    action = {"type": "full_regen", "new_seed": 42}
    result = apply_fix_plan(make_plan([action]), ctx)

    assert result.failed == [{**action, "reason": "handled by loop"}]
    assert result.blender_actions == []
    assert read_params(ctx) == PARAMS


# ---------- mixed plan ----------

def test_mixed_plan_dispatches_each_action_independently(tmp_path):
    ctx = make_ctx(tmp_path)
    actions = [
        {"type": "table_fix", "fix_id": "rescale_params", "target": "geometry"},
        {"type": "table_fix", "fix_id": "cleanup_mesh", "target": "hull"},
        {"type": "table_fix", "fix_id": "bogus", "target": "x"},
    ]
    result = apply_fix_plan(make_plan(actions), ctx)

    assert len(result.applied) == 1 and result.applied[0]["fix_id"] == "rescale_params"
    assert len(result.blender_actions) == 1 and result.blender_actions[0]["fix_id"] == "cleanup_mesh"
    assert len(result.failed) == 1 and result.failed[0]["fix_id"] == "bogus"
    assert isinstance(result, ApplyResult)
