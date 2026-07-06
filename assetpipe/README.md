# assetpipe — implementation

This package implements the pipeline specified in `docs/specs/asset-pipeline.md`.
All components are built; everything that can run without the pinned external
toolchain (Blender 4.2, Godot 4.3, the Khronos glTF validator, the Anthropic
API) is covered by the pure-Python test suite:

```
python3 -m pytest assetpipe/tests    # 415 tests, no Blender/Godot/network
```

## Module map

| Module | Spec | What it does |
|---|---|---|
| `schemas/defects.json` | App. B | Closed defect taxonomy: definition, severity, table fix, resume stage |
| `schemas/rubric.json` | §15.2 | The 12 vision checks: views, criteria text, allowed defects, two-view rule |
| `schemas/fixes.json` | §16.2 | Deterministic defect→fix table (implementation dotted paths) |
| `schemas/*.schema.json` | §6, §16.3 | AssetRequest and FixPlan JSON Schemas (draft 2020-12) |
| `profiles/*.json` | §8 | The four platform budget profiles |
| `contracts.py` | §2, App. B | Loader + cross-consistency gate; generates the vision tool schema |
| `intake.py` | §6 | Fail-fast request validation; zero iterations consumed on rejection |
| `generators/` | §9 | Registry + keyword resolution and the 9 minimum recipes (bpy only inside `generate()`) |
| `matlib/` | §10.2 | Shared shader node-group builders + bpy-free palette sampling (§10.5) |
| `themes_io.py`, `themes/` (→ `../themes`) | §7 | Four theme packs (17 material recipes) + loader/validator |
| `blender_scripts/` | §9–§14, §16.2 | In-Blender stage scripts: generate, bake, export (normative §12.1 kwargs), mesh checks S1–S12e, render harness, all table fixes. bpy-free parts (view table, framing math, args, contact sheets, param resolution) are unit-tested |
| `validation/` | §13, §14.5 | Pixel analytics (A1–A4, S16–S19), GLB structural checks (S20b–d), and `static_gate.py` assembling the orchestrator-side V1 |
| `vision/` | §15, App. A | Prompt builders, report semantics, and `inspector.py` — the forced-tool-use Anthropic caller with corrective retry, uncertain crop re-query, and backoff→`InfraError` |
| `fixes/` | §16 | Planner + escalation ladder, applicator (`apply.py`), pure-Python param/map fixes |
| `loop.py` | §4.2, §16.5–16.6 | Per-asset state machine with all five stopping conditions |
| `stages/` | §4.3 | `SubprocessStages`: Blender subprocess spawning (timeout, retry-once, `InfraError`), §9.3 param resolution, fix resume semantics, pre-vision A-checks |
| `rundir.py`, `pipeline_config.py` | §17, §3, §20.3 | Run-dir layout, append-only `history.jsonl`, single-writer manifest; config merge + toolchain gate |
| `orchestrator.py` | §16.5, §17, §20 | `run_batch` / `resume_run`: intake → parallel per-asset loops → final/ + manifest + diagnosis |
| `diagnosis.py` | §16.6 | Machine-written `diagnosis.md` for best-effort assets |
| `adapters/` | §18–§19 | `EngineAdapter` protocol + Godot adapter (deliver/verify, bundled `.gd` scripts) |
| `cli.py` (`python -m assetpipe`) | §20.2 | generate, batch, validate, render, inspect, deliver, resume, report |

## Running it

```
python -m assetpipe batch    --requests batch.json --out runs/
python -m assetpipe generate --request one.json --max-iterations 5
python -m assetpipe report   --run runs/<run_id> [--verbose]
python -m assetpipe deliver  --run runs/<run_id> --adapter godot --project /path/to/godot_proj
```

Requires on PATH (or via `--blender-bin` / `--godot-bin`): Blender 4.2 LTS,
Godot 4.3+, and `ANTHROPIC_API_KEY` (or an `ant auth login` profile) for the
vision stage. The §3 toolchain gate hard-fails a run on version mismatch
unless `toolchain.require_exact: false`.

## Test tiers (spec §21) — what runs where

- **Runs in CI here (pure Python):** validator truth tests on synthetic
  fixtures, contract cross-consistency, fix-loop unit tests, the vision
  harness against fake clients, the orchestrator end-to-end against a fake
  `blender` executable, and the Godot adapter against a fake `godot` binary.
- **Needs the real toolchain (not runnable in this container):** golden
  generation tests (§21.2), real-Blender bake/render smoke tests, real-Godot
  import verification, and the nightly real-API vision regression (§21.3).
  The highest-risk unverified seam is glTF occlusion-texture wiring in
  `blender_scripts/export_gltf.py` (documented in-code) — smoke-test that
  first when Blender is available.

## Invariants to preserve

- **Never hand-write what `contracts.py` generates.** The vision tool schema,
  prompt taxonomy list, and check applicability all derive from the three JSON
  data files; `Contracts.load()` raises on any inconsistency — keep it that way.
- **The loop owns stopping.** Stage code must not decide to retry iterations or
  give up; it either succeeds, returns findings, or raises `InfraError`.
- **Thresholds live in `config/defaults.yaml`**, not in code.
- Adding a defect type = edit `defects.json` (+ optionally `fixes.json`);
  prompts, schemas, and planner pick it up automatically. Same for rubric
  changes; adding a theme = a new `themes/<id>/` directory; adding a generator
  = a new recipe module (the registry discovers it).
- Recipes/material modules import `bpy` only inside function bodies so every
  module stays importable (and registry-discoverable) without Blender.
