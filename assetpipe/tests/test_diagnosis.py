"""diagnosis.md rendering (spec 16.6): per-iteration table, persisted defects,
and the final hypothesis line — either model-supplied or the deterministic
heuristic fallback. All LoopResult/IterationRecord/Finding objects are built by
hand here (see loop.py / test_loop.py for the shapes), no loop execution needed."""
from assetpipe.diagnosis import render_diagnosis, write_diagnosis
from assetpipe.loop import IterationRecord, LoopResult, State
from assetpipe.vision.report import Finding

REQUEST = {
    "asset_id": "scifi_crate_small_01",
    "category": "prop_small",
    "theme": "scifi_industrial",
    "platform_profile": "web",
    "seed": 421337,
    "description": "a small worn scifi storage crate with chamfered edges",
}


def seam(confidence=0.9, description="seam visible on chamfer"):
    return Finding(check_id="R4", defect_type="VISIBLE_SEAM", severity="blocker",
                   verdict="fail", confidence=confidence, location="top edge",
                   description=description)


def _best_effort_result():
    """3 iterations; VISIBLE_SEAM persists through all of them. iter1 gets a
    table fix, iter2 escalates to an llm patch, iter3 is the last iteration
    (cap exhausted) and therefore has no fix_plan of its own."""
    plan1 = {
        "asset_id": REQUEST["asset_id"], "for_iteration": 1, "produces_iteration": 2,
        "defects_addressed": ["VISIBLE_SEAM"],
        "actions": [{"type": "table_fix", "fix_id": "reseam_uv", "target": "top edge"}],
        "planner": "table", "resume_stage": "M",
    }
    plan2 = {
        "asset_id": REQUEST["asset_id"], "for_iteration": 2, "produces_iteration": 3,
        "defects_addressed": ["VISIBLE_SEAM"],
        "actions": [{"type": "llm_param_patch", "target": "top edge"}],
        "planner": "llm", "resume_stage": "M",
    }
    records = [
        IterationRecord(1, [seam()], [], True, plan1),
        IterationRecord(2, [seam()], [], True, plan2),
        IterationRecord(3, [seam()], [], True, None),
    ]
    events = [
        {"event": "iteration_end", "iteration": 1, "blockers": ["R4:VISIBLE_SEAM"]},
        {"event": "fix_planned", "iteration": 1, "resume": "M"},
        {"event": "iteration_end", "iteration": 2, "blockers": ["R4:VISIBLE_SEAM"]},
        {"event": "fix_planned", "iteration": 2, "resume": "M"},
        {"event": "iteration_end", "iteration": 3, "blockers": ["R4:VISIBLE_SEAM"]},
    ]
    return LoopResult(State.BEST_EFFORT, records, 3, [seam()],
                      "iteration cap exhausted", events)


def test_best_effort_table_has_one_row_per_iteration():
    md = render_diagnosis(REQUEST, _best_effort_result())
    assert "| 1 |" in md and "| 2 |" in md and "| 3 |" in md
    # iteration 3 is the last iteration: no fix_plan, so "applied" cell is the dash
    assert "| 3 | R4:VISIBLE_SEAM (top edge) | — |" in md


def test_best_effort_shows_fix_applied_for_non_terminal_iterations():
    md = render_diagnosis(REQUEST, _best_effort_result())
    assert "table_fix:reseam_uv" in md
    assert "llm_param_patch:top edge" in md


def test_persisted_defects_section_lists_surviving_finding():
    md = render_diagnosis(REQUEST, _best_effort_result())
    assert "## Persisted defects" in md
    assert "R4:VISIBLE_SEAM" in md.split("## Persisted defects")[1]
    assert "top edge" in md.split("## Persisted defects")[1]


def test_heuristic_hypothesis_names_defect_and_tried_fix_classes():
    md = render_diagnosis(REQUEST, _best_effort_result())
    hyp = md.split("## Hypothesis")[1]
    assert "VISIBLE_SEAM" in hyp
    assert "persisted across 3 iteration" in hyp
    assert "table_fix" in hyp and "llm_param_patch" in hyp


def test_what_was_requested_section_has_intake_fields():
    md = render_diagnosis(REQUEST, _best_effort_result())
    section = md.split("## What was requested")[1].split("##")[0]
    assert REQUEST["description"] in section
    assert REQUEST["category"] in section
    assert REQUEST["theme"] in section
    assert REQUEST["platform_profile"] in section
    assert str(REQUEST["seed"]) in section


def test_hypothesis_fn_injected_value_appears_verbatim():
    custom = ("The seam on the chamfer persists across margin increases and "
             "re-unwraps; likely the EdgeWear mask is discontinuous across "
             "UV islands — a material-recipe bug, not an asset-level fixable.")
    md = render_diagnosis(REQUEST, _best_effort_result(), hypothesis_fn=lambda _h: custom)
    assert custom in md
    # the heuristic line must NOT also appear alongside it
    assert "Recurrence despite escalation" not in md


def test_hypothesis_fn_receives_history_text():
    captured = {}

    def fake(history_text):
        captured["text"] = history_text
        return "custom hypothesis"

    render_diagnosis(REQUEST, _best_effort_result(), hypothesis_fn=fake)
    assert "VISIBLE_SEAM" in captured["text"]
    assert "iteration cap exhausted" in captured["text"]


def test_hypothesis_fn_raising_falls_back_to_heuristic():
    def boom(_history_text):
        raise RuntimeError("model unavailable")

    md = render_diagnosis(REQUEST, _best_effort_result(), hypothesis_fn=boom)
    hyp = md.split("## Hypothesis")[1]
    assert "VISIBLE_SEAM" in hyp
    assert "persisted across 3 iteration" in hyp


def test_hard_failed_has_no_iteration_table_and_states_stop_reason():
    result = LoopResult(State.HARD_FAILED, [], None, [],
                        "HARD-FAIL: blender crashed twice",
                        events=[{"event": "infra_error", "error": "blender crashed twice"}])
    md = render_diagnosis(REQUEST, result)
    assert "hard_failed" in md
    assert "HARD-FAIL: blender crashed twice" in md
    assert "## Iterations" not in md
    assert "## Persisted defects" not in md
    # still produces a hypothesis line without raising
    assert "## Hypothesis" in md


def test_hard_failed_with_empty_events_does_not_crash():
    result = LoopResult(State.HARD_FAILED, [], None, [], "HARD-FAIL: timeout", events=[])
    md = render_diagnosis(REQUEST, result)
    assert "HARD-FAIL: timeout" in md


def test_validated_state_renders_sensibly():
    warn = Finding(check_id="R12", defect_type="PALETTE_VIOLATION", severity="warn",
                  verdict="fail", confidence=0.8, location="body")
    records = [IterationRecord(1, [], [warn], True, None)]
    result = LoopResult(State.VALIDATED, records, 1, [warn],
                        "all blocker checks pass", events=[])
    md = render_diagnosis(REQUEST, result)
    assert "validated" in md
    assert "## Iterations" in md
    assert "PALETTE_VIOLATION" in md
    assert "## Hypothesis" in md


def test_determinism_same_inputs_same_string():
    result = _best_effort_result()
    a = render_diagnosis(REQUEST, result)
    b = render_diagnosis(REQUEST, result)
    assert a == b


def test_write_diagnosis_writes_file(tmp_path):
    result = _best_effort_result()
    path = write_diagnosis(tmp_path, REQUEST, result)
    assert path == tmp_path / "diagnosis.md"
    assert path.exists()
    assert path.read_text() == render_diagnosis(REQUEST, result)
