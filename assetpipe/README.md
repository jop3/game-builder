# assetpipe — implemented core

This package contains the **judgment-critical core** of the pipeline specified in
`docs/specs/asset-pipeline.md`, implemented and tested first because these are the
pieces where subtle mistakes are hardest to notice later: the cross-consistent
contracts, the vision-inspection semantics, the repair-loop state machine, and the
objective pixel/GLB checks. Everything here runs and is covered by
`assetpipe/tests/` (pure Python — no Blender, Godot, or API access needed):

```
python3 -m pytest assetpipe/tests    # 55 tests
```

## What is implemented (do not re-design; extend)

| Module | Spec | What it does |
|---|---|---|
| `schemas/defects.json` | App. B | Closed defect taxonomy: definition, severity, table fix, resume stage |
| `schemas/rubric.json` | §15.2 | The 12 vision checks: views, criteria text, allowed defects, two-view rule |
| `schemas/fixes.json` | §16.2 | Deterministic defect→fix table (implementations dotted-path stubs) |
| `schemas/*.schema.json` | §6, §16.3 | AssetRequest and FixPlan JSON Schemas (draft 2020-12) |
| `profiles/*.json` | §8 | The four platform budget profiles (triangles, textures, file size, LODs) |
| `contracts.py` | §2, App. B | Loader + **cross-consistency gate**: taxonomy/rubric/fixes cannot drift; generates the vision tool schema from the data files |
| `vision/prompts.py` | §15.1, App. A | Inspection + uncertainty-recheck prompt builders (criteria rendered from rubric.json, never paraphrased) |
| `vision/report.py` | §15.3–15.6 | Report semantic validation, two-view-rule enforcement, uncertain→fail-safe policy, verdict aggregation |
| `fixes/planner.py` | §16.1–16.4 | Table-fix planning, escalation ladder, full-regen window/budget, LLM patch clamping (`clamp_patch`) |
| `loop.py` | §4.2, §16.5–16.6 | Per-asset state machine with all five stopping conditions and best-iteration selection |
| `validation/image_checks.py` | §14.5, §13.3–13.4 | A1–A4 + S16/S17/S19 pixel analytics (S19 uses relative gradient ratios — see the spec's corrected §13.4 rationale) |
| `validation/glb.py` | §13.5 | Dependency-free GLB parsing + S20b–S20d structural checks |
| `config/defaults.yaml` | §20.3 | All thresholds/config defaults |

## What is deliberately left (in rough build order, spec §23)

These are well-specified by the spec plus the four original skills in
`.claude/skills/` (`blender-procedural-geometry`, `pbr-material-baking`,
`asset-visual-qa`, `godot-asset-import`) and are mechanical to build against the
contracts above:

1. `intake.py` — request validation against `asset_request.schema.json` + theme/profile
   existence (spec §6).
2. `orchestrator.py` + `stages/` — subprocess wrappers implementing the `Stages`
   protocol in `loop.py` (spawn `blender --background --python ... -- --args-json`,
   timeouts, one retry, raise `loop.InfraError` after retry; spec §4.3). The run-dir
   layout and `history.jsonl` events are specified in §17; `loop.py` already emits
   the event stream to persist.
3. `blender_scripts/` — generate/bake/export/render scripts and the fix
   implementations referenced by `schemas/fixes.json` (`implementation` dotted paths).
   The skills contain the exact bpy/bmesh/bake patterns to use.
4. `vision/inspector.py` — the Anthropic API caller: build prompt via
   `vision/prompts.py`, force the tool from `contracts.report_tool_schema(category)`,
   validate via `vision/report.validate_report`, run the single re-query round, and
   return a `loop.StageResult` (call shape in the `asset-visual-qa` skill).
5. `fixes/apply.py` — action applicator: table fixes dispatch to the dotted paths;
   `llm_param_patch` makes the text-only model call and sanitizes it with
   `planner.clamp_patch` before writing `params.json`.
6. `generators/`, `matlib/`, `themes/` — recipes (spec §9.2 minimum set, §10.2).
7. `adapters/godot/` — per the `godot-asset-import` skill and spec §19.
8. `cli.py` — the §20.2 commands.
9. Diagnosis writer (spec §16.6): render `diagnosis.md` from `LoopResult.events` +
   iteration records; one text-only model call for the final hypothesis line.

## Invariants to preserve

- **Never hand-write what `contracts.py` generates.** The vision tool schema, prompt
  taxonomy list, and check applicability all derive from the three JSON data files;
  `Contracts.load()` raises on any inconsistency — keep it that way.
- **The loop owns stopping.** Stage code must not decide to retry iterations or give
  up; it either succeeds, returns findings, or raises `InfraError`.
- **Thresholds live in `config/defaults.yaml`**, not in code.
- Adding a defect type = edit `defects.json` (+ optionally `fixes.json`); prompts,
  schemas, and planner pick it up automatically. Same for rubric changes.
