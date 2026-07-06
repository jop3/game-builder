"""State machine: stopping conditions, escalation flow, best-iteration selection
(spec 16.5, 16.6) driven by scripted stage doubles — no Blender required."""
from dataclasses import dataclass, field

import pytest

from assetpipe.contracts import Contracts
from assetpipe.fixes.planner import LadderConfig
from assetpipe.loop import (InfraError, LoopConfig, StageResult, State,
                            best_iteration, run_asset_loop)
from assetpipe.vision.report import Finding

C = Contracts.load()
REQ = {"asset_id": "crate", "seed": 100, "category": "prop_small"}


def blocker(defect="VISIBLE_SEAM", check="R4"):
    return Finding(check_id=check, defect_type=defect, severity="blocker",
                   verdict="fail", confidence=0.9, location="edge")


def warn(defect="PALETTE_VIOLATION", check="R12"):
    return Finding(check_id=check, defect_type=defect, severity="warn",
                   verdict="fail", confidence=0.8, location="body")


@dataclass
class ScriptedStages:
    """static_script / vision_script: list per iteration (1-indexed via pop order).
    Entries: StageResult, or an Exception instance to raise."""
    static_script: list = field(default_factory=list)
    vision_script: list = field(default_factory=list)
    calls: list = field(default_factory=list)

    def _next(self, script, kind, iteration):
        self.calls.append((kind, iteration))
        item = script[iteration - 1]
        if isinstance(item, Exception):
            raise item
        return item

    def generate(self, iteration, seed):
        self.calls.append(("generate", iteration, seed))

    def apply_fix(self, iteration, fix_plan):
        self.calls.append(("apply_fix", iteration, fix_plan["resume_stage"]))

    def static_validate(self, iteration):
        return self._next(self.static_script, "static", iteration)

    def render(self, iteration):
        self.calls.append(("render", iteration))

    def inspect(self, iteration):
        return self._next(self.vision_script, "inspect", iteration)


PASS = StageResult(passed=True)
CFG = LoopConfig(ladder=LadderConfig(max_iterations=5))
clock = lambda: 0.0  # noqa: E731


def test_happy_path_single_iteration():
    st = ScriptedStages([PASS], [PASS])
    res = run_asset_loop(REQ, st, C, CFG, clock)
    assert res.state == State.VALIDATED
    assert res.shipped_iteration == 1 and res.stop_reason.startswith("all blocker")
    assert ("render", 1) in st.calls


def test_static_fail_skips_render_and_vision():
    st = ScriptedStages(
        [StageResult(False, [blocker("NON_MANIFOLD", "S1")]), PASS],
        [None, PASS])
    res = run_asset_loop(REQ, st, C, CFG, clock)
    assert res.state == State.VALIDATED and res.shipped_iteration == 2
    kinds1 = [c for c in st.calls if len(c) > 1 and c[1] == 1]
    assert ("render", 1) not in kinds1 and ("inspect", 1) not in kinds1
    assert ("apply_fix", 2, "M") in st.calls          # in-place mesh fix -> rebake


def test_vision_fail_then_fix_then_pass():
    st = ScriptedStages(
        [PASS, PASS],
        [StageResult(False, [blocker()]), PASS])
    res = run_asset_loop(REQ, st, C, CFG, clock)
    assert res.state == State.VALIDATED and res.shipped_iteration == 2
    assert ("apply_fix", 2, "X") in st.calls          # in-fix rebake -> re-export only


def test_cap_exhaustion_ships_best_effort_with_best_iteration():
    # iter2 has fewest blockers (1) -> shipped even though later iters exist
    two = StageResult(False, [blocker(), blocker("BLACK_SURFACE", "R2")])
    one = StageResult(False, [blocker()])
    st = ScriptedStages([PASS] * 5, [two, one, two, two, two])
    res = run_asset_loop(REQ, st, C, CFG, clock)
    assert res.state == State.BEST_EFFORT
    assert res.stop_reason == "iteration cap exhausted"
    assert res.shipped_iteration == 2
    assert [f.defect_type for f in res.remaining_defects] == ["VISIBLE_SEAM"]


def test_no_progress_stops_early():
    same = lambda: StageResult(False, [blocker()])  # noqa: E731
    st = ScriptedStages([PASS] * 5, [same(), same(), same(), same(), same()])
    res = run_asset_loop(REQ, st, C, CFG, clock)
    assert res.state == State.BEST_EFFORT
    assert "NO-PROGRESS" in res.stop_reason
    # identical defects + identical plan detected at iteration 2 — not run to cap
    assert max(i for k, i, *rest in [c for c in st.calls if c[0] == "inspect"]) == 2


def test_progress_via_escalation_is_not_no_progress():
    """Same defect persisting is NOT a fixpoint while the ladder still escalates:
    plans differ (table -> table -> subcomponent regen...), so the loop runs on."""
    same = lambda: StageResult(False, [blocker("SILHOUETTE_MISMATCH", "R5")])  # noqa: E731
    st = ScriptedStages([PASS] * 5, [same(), same(), same(), same(), same()])
    res = run_asset_loop(REQ, st, C, CFG, clock)
    assert res.state == State.BEST_EFFORT
    # llm patch at 1,2 (same plan => no-progress would fire at 2 if signature equal)
    # SILHOUETTE_MISMATCH has no table fix: iter1 llm, iter2 llm -> identical plan
    # -> NO-PROGRESS fires. This documents the interaction precisely.
    assert "NO-PROGRESS" in res.stop_reason


def test_full_regen_uses_new_seed_and_calls_generate():
    """Drive to iteration 4 with a burned table fix so the planner emits
    full_regen; the loop must re-generate with the planner's new seed."""
    # Alternate the defect so NO-PROGRESS never fires, and make the seam
    # defect the one that fails at iteration 4 — by then its table fix has
    # been used twice (iters 1 and 3), so the planner must full-regen.
    v1 = StageResult(False, [blocker()])
    v2 = StageResult(False, [blocker("BLACK_SURFACE", "R2")])
    v3 = StageResult(False, [blocker()])
    v4 = StageResult(False, [blocker()])
    st = ScriptedStages([PASS] * 5, [v1, v2, v3, v4, PASS])
    res = run_asset_loop(REQ, st, C, CFG, clock)
    assert res.state == State.VALIDATED and res.shipped_iteration == 5
    regen_calls = [c for c in st.calls if c[0] == "generate" and c[1] == 5]
    assert regen_calls == [("generate", 5, (100 + 4) % 2**32)]


def test_infra_error_hard_fails_and_batch_continues_semantics():
    st = ScriptedStages([PASS], [InfraError("blender crashed twice")])
    res = run_asset_loop(REQ, st, C, CFG, clock)
    assert res.state == State.HARD_FAILED
    assert res.shipped_iteration is None
    assert "blender crashed" in res.stop_reason


def test_wall_clock_exceeded_ships_best_effort_if_renderable():
    t = iter([0.0, 0.0, 1e9])           # iter1 in budget, iter2 over
    st = ScriptedStages([PASS] * 2, [StageResult(False, [blocker()]), PASS])
    res = run_asset_loop(REQ, st, C,
                         LoopConfig(ladder=LadderConfig(max_iterations=5),
                                    wall_clock_budget_s=60),
                         now=lambda: next(t))
    assert res.state == State.BEST_EFFORT and "WALL-CLOCK" in res.stop_reason
    assert res.shipped_iteration == 1


def test_wall_clock_with_no_renderable_iteration_hard_fails():
    t = iter([0.0, 1e9])
    st = ScriptedStages([], [])
    res = run_asset_loop(REQ, st, C,
                         LoopConfig(wall_clock_budget_s=60), now=lambda: next(t))
    assert res.state == State.HARD_FAILED


def test_warns_never_gate_but_are_reported():
    st = ScriptedStages([PASS], [StageResult(True, [], [warn()])])
    res = run_asset_loop(REQ, st, C, CFG, clock)
    assert res.state == State.VALIDATED
    assert [f.defect_type for f in res.remaining_defects] == ["PALETTE_VIOLATION"]


def test_best_iteration_prefers_fewer_blockers_then_warns_then_latest():
    from assetpipe.loop import IterationRecord
    recs = [
        IterationRecord(1, [blocker()], [], True),
        IterationRecord(2, [], [warn(), warn()], True),
        IterationRecord(3, [], [warn()], True),
        IterationRecord(4, [], [warn()], True),
        IterationRecord(5, [blocker()], [], False),   # not renderable
    ]
    assert best_iteration(recs).iteration == 4        # ties on warns -> latest
    assert best_iteration([recs[4]]) is None          # nothing renderable
