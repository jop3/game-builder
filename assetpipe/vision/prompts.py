"""Vision inspection prompt builder (spec 15.1, Appendix A).

Renders the full inspector prompt from the rubric + theme + request, so the
per-check pass criteria in prompts are always the rubric's own text — never a
paraphrase that can drift. String assembly deliberately avoids str.format on
the whole template (theme text may contain braces); only rubric criteria
strings, which we control, are formatted, with explicit slot values.
"""
from __future__ import annotations

from assetpipe.contracts import Contracts

# Views whose special semantics the model must know or it will report the
# harness itself as defects (spec 15.3 / asset-visual-qa skill).
_LABEL_LINES = {
    "contact_sheets": "Each contact-sheet cell is labeled with its view_id in "
                      "the corner.",
    "views": "Each render is its own full-resolution image, and the text line "
             "immediately BEFORE each image names its view_id (cite those ids).",
}

_HARNESS_NOTES = """\
RENDER SET
{label_line} Lighting rigs:
L1 = neutral studio HDRI; lit_warm_* = warm directional sun; lit_dark_090 = dim blue
rim light (dark regions there are EXPECTED - judge texture presence by the rim-lit
edge only). silhouette_* views are white-on-black by design and show the asset ONLY
(no ground plane, no reference cube). normals_* views use a debug material: surface
normal remapped to RGB as 0.5*n+0.5 (mid-tones are normal; expect pastel hues, e.g.
+X renders pinkish (1,0.5,0.5), straight-up renders (0.5,0.5,1)) -- ONLY backfacing
surfaces render PURE saturated RED (1,0,0).
uvcheck_045 deliberately shows a checker pattern - it is exempt from texture checks.
The matte grey cube 1.5 m to the asset's left is a 1 m reference object, not part of
the asset."""

_RULES = """\
RULES
1. A verdict of "fail" REQUIRES: at least one cited view_id in evidence_views, a
   specific location phrase (e.g. "upper-left panel of the lid in turn_090"), and a
   defect_type chosen from the taxonomy below.
2. Where a check specifies a two-view rule, a defect visible in only ONE view must be
   reported as "uncertain", not "fail".
3. Judge only what is visible in the provided renders. Do not infer defects from
   expectations, and do not fail the asset for stylistic choices permitted by the
   style brief. When a check's pass criteria hold, report pass.
4. confidence is your honest calibration in [0,1] for the verdict you chose.
5. Every check listed under CHECKS must appear exactly once: either in checks[] or,
   if it does not apply to this asset, in checks_not_applicable[].
6. worst_thing (open-ended, non-gating): in one sentence, name the single thing that
   most makes this asset NOT read as the requested description in the theme style --
   even if every check above passed. This is a catch-all for defects the closed
   rubric does not name. Leave it empty ONLY if nothing detracts.
7. Report exclusively through the report_inspection tool."""


def _fill_criteria(text: str, slots: dict[str, str]) -> str:
    for key, value in slots.items():
        text = text.replace("{" + key + "}", value)
    return text


def build_inspection_prompt(request: dict, theme: dict, bbox_range: str,
                            contracts: Contracts,
                            image_delivery: str = "contact_sheets") -> str:
    checks = contracts.applicable_checks(request["category"])
    palette = theme.get("palette", {})
    # NOT-list (borrowed from Snittet's spelbygge brief): the theme's explicit
    # anti-style, promoted from the "NOT:" clause buried in vision_style_brief to
    # a first-class list. Stating what the asset must NOT be is the strongest
    # drift guard for R5/R12 (a knight in a sci-fi theme, wood on a metal hull).
    anti_style = theme.get("anti_style", [])
    allowed = ", ".join(
        c for group in ("primary", "secondary", "accent", "emissive")
        for c in palette.get(group, []))
    slots = {
        "description": request["description"],
        "silhouette_language": theme.get("silhouette_language", "(no style constraint)"),
        "vision_style_brief": theme.get("vision_style_brief", "(no style brief)"),
        "palette": allowed or "(unconstrained)",
        "forbidden": ", ".join(palette.get("forbidden", [])) or "(none)",
        "bbox_range": bbox_range,
    }

    check_lines = []
    for cid, chk in checks.items():
        two_view = (" Two-view rule: only report fail if visible in at least "
                    "2 distinct views; one view -> uncertain."
                    if chk["min_views_for_fail"] >= 2 else "")
        check_lines.append(
            f"[{cid}] {chk['title']} (severity: {chk['severity']}; "
            f"judge in views: {', '.join(chk['views'])}; "
            f"allowed defect_types: {', '.join(chk['allowed_defects'])})\n"
            f"    {_fill_criteria(chk['criteria'], slots)}{two_view}")

    return "\n\n".join([
        "You are a strict technical art QA inspector for an automated game asset "
        "pipeline. You are inspecting deterministic headless renders of ONE asset. "
        "Your verdicts gate the pipeline; there is no human reviewer after you.",
        "ASSET UNDER INSPECTION\n"
        f"- asset_id: {request['asset_id']}   category: {request['category']}\n"
        f"- requested description: \"{request['description']}\"\n"
        f"- theme: {theme.get('display_name', request['theme'])}\n"
        f"- theme silhouette language: {slots['silhouette_language']}\n"
        f"- theme style brief: {slots['vision_style_brief']}\n"
        f"- theme palette (allowed dominant hues): {slots['palette']}; "
        f"forbidden: {slots['forbidden']}\n"
        f"- this theme is explicitly NOT: "
        f"{'; '.join(anti_style) if anti_style else '(no anti-style declared)'}\n"
        f"- expected real-world size range: {bbox_range}",
        _HARNESS_NOTES.replace(
            "{label_line}",
            _LABEL_LINES.get(image_delivery, _LABEL_LINES["contact_sheets"])),
        "CHECKS - evaluate every one of: " + ", ".join(checks) + "\n\n"
        + "\n".join(check_lines),
        "DEFECT TAXONOMY (defect_type must be one of):\n"
        + ", ".join(contracts.taxonomy_ids()),
        _RULES,
    ])


def build_recheck_prompt(check_id: str, prior: dict, contracts: Contracts) -> str:
    """Follow-up prompt for resolving one 'uncertain' verdict with full-res crops
    of the cited view (spec 15.5). Same tool, single-check scope."""
    chk = contracts.rubric["checks"][check_id]
    return "\n\n".join([
        "Follow-up inspection: you previously returned verdict 'uncertain' for one "
        "check on this asset. Attached are full-resolution crops of the view you "
        f"cited ({', '.join(prior.get('evidence_views', []) or ['(none)'])}).",
        f"Re-evaluate ONLY check [{check_id}] {chk['title']}:\n"
        f"    {chk['criteria']}\n"
        f"Your prior note: {prior.get('description', '(none)')}",
        "Return the report_inspection tool with exactly one entry in checks[] for "
        f"[{check_id}], and every other check id listed in checks_not_applicable[]. "
        "If you still cannot decide from these crops, answer 'uncertain' - the "
        "pipeline treats an unresolvable check as a fail (fail-safe).",
        _RULES,
    ])
