"""Machine-written diagnosis.md for BEST_EFFORT assets (spec 16.6, item 9).

Renders a deterministic postmortem from a completed :class:`~assetpipe.loop.LoopResult`:
what was requested, a per-iteration table of (defects found -> fix applied -> result),
which defects survived into the shipped iteration, and a one-line hypothesis about
*why* they persisted.

The hypothesis line is normally produced by one last text-only model call
(injected as `hypothesis_fn`, so this module stays pure and unit-testable — no
API access here) summarizing a compact plain-text history. If no `hypothesis_fn`
is supplied, or it raises, a deterministic heuristic takes over: it never lets a
missing/broken model call block the artifact from being written (spec 16.6 — this
file must always exist for a best-effort asset).
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Callable

from assetpipe.loop import IterationRecord, LoopResult
from assetpipe.vision.report import Finding

_EVENTS_TAIL = 5


def _fmt_finding(f: Finding) -> str:
    s = f"{f.check_id}:{f.defect_type}"
    if f.location:
        s += f" ({f.location})"
    return s


def _fmt_action(a: dict) -> str:
    t = a["type"]
    if t == "table_fix":
        return f"table_fix:{a.get('fix_id')}"
    if t == "full_regen":
        return f"full_regen(new_seed={a.get('new_seed')})"
    if t == "subcomponent_regen":
        return f"subcomponent_regen:{a.get('target')}"
    if t == "llm_param_patch":
        return f"llm_param_patch:{a.get('target')}"
    return t


def _fmt_fix_plan(plan: dict | None) -> str:
    """Compact summary of a fix_plan's actions, or '-' when there is none
    (the last iteration of a run never gets one — spec 16.5)."""
    if not plan or not plan.get("actions"):
        return "—"
    return "; ".join(_fmt_action(a) for a in plan["actions"])


def _fmt_result(record: IterationRecord) -> str:
    if not record.rendered:
        return "static gate failed"
    return f"{len(record.blockers)} blocker(s), {len(record.warns)} warn(s)"


def _iteration_table(result: LoopResult) -> str:
    lines = ["| iteration | defects found | fix applied | result |",
             "|---|---|---|---|"]
    for rec in result.iterations:
        defects = "; ".join(_fmt_finding(f) for f in rec.blockers) or "—"
        lines.append(f"| {rec.iteration} | {defects} | {_fmt_fix_plan(rec.fix_plan)} "
                     f"| {_fmt_result(rec)} |")
    return "\n".join(lines)


def _persisted_defects(result: LoopResult) -> str:
    if not result.remaining_defects:
        return "None."
    lines = []
    for f in result.remaining_defects:
        bits = [f"`{f.check_id}:{f.defect_type}`"]
        if f.location:
            bits.append(f"at {f.location}")
        bits.append(f"(confidence {f.confidence:.2f})")
        if f.description:
            bits.append(f"— {f.description}")
        lines.append("- " + " ".join(bits))
    return "\n".join(lines)


def _history_summary(result: LoopResult) -> str:
    """Compact plain-text history handed to `hypothesis_fn` — table info plus
    the raw event stream, so the model call has the same evidence a human
    reading history.jsonl would have (spec 17.2)."""
    lines = [f"state: {result.state.value}", f"stop_reason: {result.stop_reason}", ""]
    for rec in result.iterations:
        defects = ", ".join(_fmt_finding(f) for f in rec.blockers) or "none"
        lines.append(f"iter {rec.iteration}: defects=[{defects}] rendered={rec.rendered} "
                     f"fix_applied={_fmt_fix_plan(rec.fix_plan)} result={_fmt_result(rec)}")
    lines.append("")
    lines.append("events:")
    for ev in result.events:
        lines.append(f"  {ev}")
    return "\n".join(lines)


def _heuristic_hypothesis(result: LoopResult) -> str:
    """Deterministic fallback (no model call): name the defect_type(s) that
    persisted across the most iterations and the fix classes tried against
    them. Used whenever `hypothesis_fn` is absent or fails."""
    counts: Counter = Counter()
    fix_classes: dict[str, set] = {}
    for rec in result.iterations:
        for dt in {f.defect_type for f in rec.blockers}:
            counts[dt] += 1
        if rec.fix_plan:
            addressed = rec.fix_plan.get("defects_addressed", [])
            action_types = {a["type"] for a in rec.fix_plan.get("actions", [])}
            for dt in addressed:
                fix_classes.setdefault(dt, set()).update(action_types)
    if not counts:
        return ("Hypothesis: no blocker defects were recorded across the run; "
                "nothing to diagnose from the iteration history.")
    max_n = max(counts.values())
    worst = sorted(dt for dt, n in counts.items() if n == max_n)
    tried = sorted({t for dt in worst for t in fix_classes.get(dt, set())})
    tried_str = ", ".join(tried) if tried else "no fix was attempted"
    return (f"Hypothesis: {', '.join(worst)} persisted across {max_n} iteration(s); "
            f"fix classes tried: {tried_str}. Recurrence despite escalation suggests "
            f"this is not asset-level fixable with the current fix table and needs "
            f"recipe- or rubric-level investigation.")


def _hard_failed_section(result: LoopResult) -> list[str]:
    lines = [f"No iteration shipped: the loop terminated in state "
             f"{result.state.value.upper()} before producing a deliverable.",
             f"Stop reason: {result.stop_reason}", "", "Recent events:"]
    tail = result.events[-_EVENTS_TAIL:]
    if not tail:
        lines.append("(none recorded)")
    else:
        lines.extend(f"- {ev}" for ev in tail)
    return lines


def render_diagnosis(request: dict, result: LoopResult,
                     hypothesis_fn: Callable[[str], str] | None = None) -> str:
    """Machine-written diagnosis.md for a BEST_EFFORT asset (spec 16.6).

    Deterministic given `request`/`result`: no timestamps, no randomness. The
    optional `hypothesis_fn` is a text-only model call injected by the caller;
    if it is None or raises, a deterministic heuristic supplies the final line
    instead — this function never raises because of it.
    """
    asset_id = request.get("asset_id", "?")
    lines = [f"# Diagnosis: {asset_id} — {result.state.value} "
             f"({result.stop_reason})", ""]

    lines.append("## What was requested")
    lines.append("")
    lines.append(f"- description: {request.get('description', '—')}")
    lines.append(f"- category: {request.get('category', '—')}")
    lines.append(f"- theme: {request.get('theme', '—')}")
    lines.append(f"- platform_profile: {request.get('platform_profile', '—')}")
    lines.append(f"- seed: {request.get('seed', '—')}")
    lines.append("")

    if result.shipped_iteration is None:
        lines.append("## Result")
        lines.append("")
        lines.extend(_hard_failed_section(result))
        lines.append("")
    else:
        lines.append(f"## Iterations (shipped: iteration {result.shipped_iteration})")
        lines.append("")
        lines.append(_iteration_table(result))
        lines.append("")
        lines.append("## Persisted defects")
        lines.append("")
        lines.append(_persisted_defects(result))
        lines.append("")

    lines.append("## Hypothesis")
    lines.append("")
    hypothesis = None
    if hypothesis_fn is not None:
        try:
            hypothesis = hypothesis_fn(_history_summary(result))
        except Exception:
            hypothesis = None
    if not hypothesis:
        hypothesis = _heuristic_hypothesis(result)
    lines.append(hypothesis)

    return "\n".join(lines) + "\n"


def write_diagnosis(asset_dir: Path, request: dict, result: LoopResult,
                    hypothesis_fn: Callable[[str], str] | None = None) -> Path:
    """Render and write `asset_dir/diagnosis.md`; returns its path."""
    path = asset_dir / "diagnosis.md"
    path.write_text(render_diagnosis(request, result, hypothesis_fn))
    return path
