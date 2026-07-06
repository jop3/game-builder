"""Vision report semantic validation and verdict aggregation (spec 15.4-15.6).

Forced tool use guarantees the report's *shape*; this module enforces the
*semantics* the schema cannot express:

- every applicable check appears exactly once across checks[]/checks_not_applicable[]
- fail verdicts carry evidence (views + location) and an allowed defect_type
- the two-view rule is enforced server-side (a fail with too few distinct
  evidence views is downgraded to uncertain, spec 15.3)
- uncertain resolution policy: after the single re-query round, uncertain => fail
  with confidence 0.5 (fail-safe, spec 15.5)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from assetpipe.contracts import Contracts


@dataclass
class Finding:
    check_id: str
    defect_type: str
    severity: str  # blocker | warn (check severity, not defect default)
    verdict: str
    confidence: float
    evidence_views: list[str] = field(default_factory=list)
    location: str = ""
    description: str = ""

    def key(self) -> tuple[str, str]:
        """Identity used for no-progress multiset comparison (spec 16.5.3)."""
        return (self.check_id, self.defect_type)


def validate_report(report: dict, category: str, contracts: Contracts) -> list[str]:
    """Return a list of semantic errors; empty list == valid.

    A non-empty result means re-query once, then infrastructure error — never an
    asset verdict (spec 15.1).
    """
    errors: list[str] = []
    applicable = set(contracts.applicable_checks(category))
    seen: list[str] = [c.get("check_id", "?") for c in report.get("checks", [])]
    seen += list(report.get("checks_not_applicable", []))
    for cid in applicable:
        n = seen.count(cid)
        if n != 1:
            errors.append(f"check {cid} appears {n} times (must be exactly 1)")
    for cid in seen:
        if cid not in applicable:
            errors.append(f"check {cid} is not applicable to category {category}")

    for entry in report.get("checks", []):
        cid, verdict = entry.get("check_id"), entry.get("verdict")
        if verdict not in ("pass", "fail", "uncertain"):
            errors.append(f"{cid}: bad verdict {verdict!r}")
            continue
        if verdict == "pass":
            continue
        dt = entry.get("defect_type")
        if dt is None:
            errors.append(f"{cid}: verdict {verdict} requires defect_type")
        elif dt not in contracts.defects:
            errors.append(f"{cid}: defect_type {dt!r} not in taxonomy")
        elif cid in contracts.rubric["checks"] and \
                dt not in contracts.rubric["checks"][cid]["allowed_defects"]:
            errors.append(f"{cid}: defect_type {dt!r} not allowed for this check")
        if verdict == "fail":
            if not entry.get("evidence_views"):
                errors.append(f"{cid}: fail requires evidence_views")
            if not entry.get("location"):
                errors.append(f"{cid}: fail requires a location phrase")
    return errors


def extract_findings(report: dict, category: str, contracts: Contracts,
                     final_round: bool) -> list[Finding]:
    """Convert a *validated* report into findings, applying the two-view rule and
    the uncertainty policy.

    final_round=False: first inspection — uncertain findings are returned with
        verdict 'uncertain' so the orchestrator can run the crop re-query.
    final_round=True: post-re-query — uncertain becomes fail @ confidence 0.5.
    """
    findings: list[Finding] = []
    for entry in report.get("checks", []):
        verdict = entry["verdict"]
        if verdict == "pass":
            continue
        cid = entry["check_id"]
        chk = contracts.rubric["checks"][cid]
        distinct_views = len(set(entry.get("evidence_views", [])))
        if verdict == "fail" and distinct_views < chk["min_views_for_fail"]:
            verdict = "uncertain"  # server-side enforcement of the two-view rule
        confidence = float(entry.get("confidence", 0.0))
        if verdict == "uncertain" and final_round:
            verdict, confidence = "fail", 0.5  # fail-safe
        findings.append(Finding(
            check_id=cid,
            defect_type=entry["defect_type"],
            severity=chk["severity"],
            verdict=verdict,
            confidence=confidence,
            evidence_views=list(entry.get("evidence_views", [])),
            location=entry.get("location", ""),
            description=entry.get("description", ""),
        ))
    return findings


def aggregate(findings: list[Finding]) -> dict:
    """Iteration verdict (spec 15.6): pass <=> zero blocker fails after
    uncertainty resolution. Unresolved uncertain findings block a pass verdict
    but are not yet fails (they trigger the re-query round)."""
    blockers = [f for f in findings if f.verdict == "fail" and f.severity == "blocker"]
    warns = [f for f in findings if f.verdict == "fail" and f.severity == "warn"]
    uncertain = [f for f in findings if f.verdict == "uncertain"]
    return {
        "passed": not blockers and not uncertain,
        "blockers": blockers,
        "warns": warns,
        "uncertain": uncertain,
    }
