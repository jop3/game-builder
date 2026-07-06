"""Per-asset autonomous repair loop (spec 4.2, 16).

Pure control logic: all Blender/render/vision work is injected via the Stages
protocol, so this module is fully unit-testable and the stopping conditions
(spec 16.5) live in exactly one place:

1. DONE        - static gate + vision both pass            -> VALIDATED
2. CAP         - max_iterations exhausted with blockers    -> BEST_EFFORT
3. NO-PROGRESS - identical defect multiset twice AND the new plan is identical
                 to the one just tried                     -> BEST_EFFORT (early)
4. HARD-FAIL   - InfraError from any stage (post-retry)    -> HARD_FAILED
5. WALL-CLOCK  - per-asset time budget exceeded            -> BEST_EFFORT if any
                 renderable iteration exists, else HARD_FAILED
"""
from __future__ import annotations

import enum
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Protocol

from assetpipe.fixes.planner import (LadderConfig, PlannerState, plan_fixes,
                                     plan_signature, strip_internal)
from assetpipe.contracts import Contracts
from assetpipe.vision.report import Finding


class InfraError(Exception):
    """Infrastructure failure. Stages raise this only after their own retry
    policy is exhausted (spec 4.3); the loop does not retry further."""


class State(str, enum.Enum):
    PENDING = "pending"
    VALIDATED = "validated"
    BEST_EFFORT = "best_effort"
    HARD_FAILED = "hard_failed"


@dataclass
class StageResult:
    passed: bool
    blockers: list[Finding] = field(default_factory=list)
    warns: list[Finding] = field(default_factory=list)


class Stages(Protocol):
    """Injected by the orchestrator; each method operates on the iteration dir.
    Implementations own subprocess spawning, timeouts, and the single retry."""

    def generate(self, iteration: int, seed: int) -> None: ...          # G+M(+B)+X
    def apply_fix(self, iteration: int, fix_plan: dict) -> None: ...    # resume_stage..X
    def static_validate(self, iteration: int) -> StageResult: ...       # V1
    def render(self, iteration: int) -> None: ...                       # R
    def inspect(self, iteration: int) -> StageResult: ...               # V2 (incl. re-query)


@dataclass
class IterationRecord:
    iteration: int
    blockers: list[Finding]
    warns: list[Finding]
    rendered: bool
    fix_plan: dict | None = None

    def defect_multiset(self) -> Counter:
        return Counter(f.key() for f in self.blockers)


@dataclass
class LoopConfig:
    ladder: LadderConfig = field(default_factory=LadderConfig)
    wall_clock_budget_s: float = 45 * 60
    ride_along_warns: bool = True   # spec 15.6: warn fixes may ride along with blockers


@dataclass
class LoopResult:
    state: State
    iterations: list[IterationRecord]
    shipped_iteration: int | None    # None only for HARD_FAILED
    remaining_defects: list[Finding]
    stop_reason: str
    events: list[dict] = field(default_factory=list)


def best_iteration(records: list[IterationRecord]) -> IterationRecord | None:
    """Spec 16.6: fewest blockers, then fewest warns, then latest — among
    iterations that produced a renderable artifact."""
    candidates = [r for r in records if r.rendered]
    if not candidates:
        return None
    return min(candidates,
               key=lambda r: (len(r.blockers), len(r.warns), -r.iteration))


def run_asset_loop(request: dict, stages: Stages, contracts: Contracts,
                   config: LoopConfig, now: Callable[[], float]) -> LoopResult:
    asset_id, seed = request["asset_id"], request["seed"]
    ladder = config.ladder
    records: list[IterationRecord] = []
    events: list[dict] = []
    planner_state = PlannerState()
    started = now()
    pending_fix: dict | None = None
    current_seed = seed

    def log(event: str, **kw) -> None:
        events.append({"event": event, **kw})

    def finish_exhausted(reason: str) -> LoopResult:
        best = best_iteration(records)
        if best is None:
            return LoopResult(State.HARD_FAILED, records, None, [],
                              f"{reason}; no renderable iteration", events)
        return LoopResult(State.BEST_EFFORT, records, best.iteration,
                          best.blockers + best.warns, reason, events)

    try:
        for iteration in range(1, ladder.max_iterations + 1):
            if now() - started > config.wall_clock_budget_s:
                log("wall_clock_exceeded", iteration=iteration)
                return finish_exhausted("WALL-CLOCK budget exceeded")

            # --- produce this iteration's artifacts ---
            if iteration == 1:
                stages.generate(iteration, current_seed)
            elif pending_fix and any(a["type"] == "full_regen"
                                     for a in pending_fix["actions"]):
                current_seed = next(a["new_seed"] for a in pending_fix["actions"]
                                    if a["type"] == "full_regen")
                log("full_regen", iteration=iteration, seed=current_seed)
                stages.generate(iteration, current_seed)
            else:
                stages.apply_fix(iteration, strip_internal(pending_fix))

            # --- V1 static gate: cheap fails first, no render/vision on fail ---
            static = stages.static_validate(iteration)
            if static.passed:
                stages.render(iteration)
                vision = stages.inspect(iteration)
                rendered = True
                blockers = vision.blockers
                warns = static.warns + vision.warns
            else:
                rendered, blockers, warns = False, static.blockers, static.warns

            record = IterationRecord(iteration, blockers, warns, rendered)
            records.append(record)
            log("iteration_end", iteration=iteration, rendered=rendered,
                blockers=[f.key() for f in blockers], warns=[f.key() for f in warns])

            # --- stopping condition 1: DONE ---
            if not blockers:
                return LoopResult(State.VALIDATED, records, iteration, warns,
                                  "all blocker checks pass", events)

            # --- stopping condition 2: CAP ---
            if iteration == ladder.max_iterations:
                return finish_exhausted("iteration cap exhausted")

            # --- plan the fix for the next iteration ---
            to_fix = blockers + (warns if config.ride_along_warns else [])
            plan = plan_fixes(asset_id, iteration, to_fix, contracts,
                              ladder, planner_state, seed)
            record.fix_plan = plan

            # --- stopping condition 3: NO-PROGRESS ---
            if len(records) >= 2:
                prev = records[-2]
                same_defects = prev.defect_multiset() == record.defect_multiset()
                same_plan = (prev.fix_plan is not None
                             and plan_signature(prev.fix_plan) == plan_signature(plan))
                if same_defects and same_plan:
                    log("no_progress", iteration=iteration)
                    return finish_exhausted("NO-PROGRESS: identical defects and "
                                            "identical plan on consecutive iterations")

            planner_state.record_plan(plan)
            pending_fix = plan
            log("fix_planned", iteration=iteration,
                plan=plan_signature(plan), resume=plan["resume_stage"])

        raise AssertionError("unreachable: loop exits via a stopping condition")

    except InfraError as exc:
        log("infra_error", error=str(exc))
        # spec 16.5.4: no asset verdict can be derived from an infra failure
        return LoopResult(State.HARD_FAILED, records, None, [],
                          f"HARD-FAIL: {exc}", events)
