"""Prompt rendering + report semantics (spec 15)."""
import pytest

from assetpipe.contracts import Contracts
from assetpipe.vision.prompts import build_inspection_prompt, build_recheck_prompt
from assetpipe.vision.report import aggregate, extract_findings, validate_report

C = Contracts.load()

REQUEST = {
    "asset_id": "scifi_crate_small_01", "category": "prop_small",
    "theme": "scifi_industrial", "seed": 1,
    "description": "A small reinforced sci-fi supply crate with glowing status strip",
}
THEME = {
    "display_name": "Sci-Fi Industrial",
    "palette": {"primary": ["#2E3A46"], "secondary": ["#8C959D"],
                "accent": ["#00C2A8"], "emissive": ["#FFD24A"],
                "forbidden": ["#8B4513"]},
    "silhouette_language": "Chamfered boxes, panel lines, greebles.",
    "vision_style_brief": "Functional industrial sci-fi. Braces test: {not_a_slot}",
}


def test_prompt_contains_every_applicable_check_and_taxonomy():
    p = build_inspection_prompt(REQUEST, THEME, "0.3-1.2 m", C)
    for cid in C.applicable_checks("prop_small"):
        assert f"[{cid}]" in p
    assert "[R10]" not in p and "[R11]" not in p        # not applicable to props
    for d in ("VISIBLE_SEAM", "SILHOUETTE_MISMATCH", "INFRA_ERROR"):
        assert d in p                                    # taxonomy list present
    assert REQUEST["description"] in p
    assert "1 m reference" in p
    assert "{description}" not in p and "{palette}" not in p   # slots filled
    assert "{not_a_slot}" in p       # theme braces survive untouched (no str.format)


def test_recheck_prompt_scopes_single_check():
    prior = {"evidence_views": ["turn_045"], "description": "possible seam"}
    p = build_recheck_prompt("R4", prior, C)
    assert "[R4]" in p and "turn_045" in p and "fail-safe" in p.lower()


def test_prompt_injects_anti_style_not_list_when_present():
    theme = {**THEME, "anti_style": ["wood as a dominant surface",
                                     "teal/orange sci-fi accents"]}
    p = build_inspection_prompt(REQUEST, theme, "0.3-1.2 m", C)
    assert "NOT:" in p
    assert "wood as a dominant surface" in p
    assert "teal/orange sci-fi accents" in p


def test_prompt_degrades_gracefully_without_anti_style():
    p = build_inspection_prompt(REQUEST, THEME, "0.3-1.2 m", C)  # THEME has none
    assert "no anti-style declared" in p


def test_prompt_asks_for_worst_thing_catch_all():
    p = build_inspection_prompt(REQUEST, THEME, "0.3-1.2 m", C)
    assert "worst_thing" in p


def test_report_tool_schema_exposes_optional_worst_thing():
    schema = C.report_tool_schema("prop_small")
    assert "worst_thing" in schema["properties"]
    assert schema["properties"]["worst_thing"] == {"type": "string"}
    assert "worst_thing" not in schema["required"]   # non-gating: never required


def test_validate_report_ignores_worst_thing_field():
    # An open-ended advisory field must not affect semantic validation.
    r = _report([_entry()])
    r["worst_thing"] = "the accents read as a toy, not equipment"
    assert validate_report(r, "prop_small", C) == []


def _entry(cid="R4", verdict="fail", defect="VISIBLE_SEAM", views=("close_034",),
           conf=0.9, location="front edge"):
    return {"check_id": cid, "verdict": verdict, "confidence": conf,
            "evidence_views": list(views), "location": location,
            "defect_type": defect, "description": "d"}


def _report(checks, category="prop_small"):
    applicable = list(C.applicable_checks(category))
    covered = {c["check_id"] for c in checks}
    return {"asset_id": "a", "iteration": 1, "checks": checks,
            "checks_not_applicable": [c for c in applicable if c not in covered],
            "overall_impression": "x"}


def test_validate_report_happy_path():
    assert validate_report(_report([_entry()]), "prop_small", C) == []


def test_validate_report_catches_missing_and_duplicate_checks():
    r = _report([_entry()])
    r["checks_not_applicable"].remove("R1")
    assert any("R1 appears 0" in e for e in validate_report(r, "prop_small", C))
    r2 = _report([_entry(), _entry()])
    assert any("R4 appears 2" in e for e in validate_report(r2, "prop_small", C))


def test_validate_report_fail_requires_evidence_and_allowed_defect():
    bad = _entry(views=(), location="")
    errs = validate_report(_report([bad]), "prop_small", C)
    assert any("evidence_views" in e for e in errs)
    assert any("location" in e for e in errs)
    wrong = _entry(defect="TILING_SEAM")                 # not allowed for R4
    assert any("not allowed" in e
               for e in validate_report(_report([wrong]), "prop_small", C))


def test_two_view_rule_downgrades_single_view_geometry_fail():
    # R9 (structural coherence) requires 2 distinct views for a fail
    e = _entry(cid="R9", defect="FLOATING_PART", views=("turn_000",))
    f = extract_findings(_report([e]), "prop_small", C, final_round=False)[0]
    assert f.verdict == "uncertain"
    e2 = _entry(cid="R9", defect="FLOATING_PART", views=("turn_000", "top"))
    f2 = extract_findings(_report([e2]), "prop_small", C, final_round=False)[0]
    assert f2.verdict == "fail"


def test_uncertain_fails_safe_on_final_round():
    e = _entry(verdict="uncertain")
    f = extract_findings(_report([e]), "prop_small", C, final_round=True)[0]
    assert f.verdict == "fail" and f.confidence == 0.5


def test_aggregate_semantics():
    fail = extract_findings(_report([_entry()]), "prop_small", C, True)
    assert not aggregate(fail)["passed"]
    warn_entry = _entry(cid="R12", defect="PALETTE_VIOLATION", views=("turn_000",))
    warn = extract_findings(_report([warn_entry]), "prop_small", C, True)
    agg = aggregate(warn)
    assert agg["passed"] and len(agg["warns"]) == 1      # warns never gate (spec 15.6)
    unc = extract_findings(_report([_entry(verdict="uncertain")]),
                           "prop_small", C, False)
    assert not aggregate(unc)["passed"]                  # unresolved uncertain blocks pass
