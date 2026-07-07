# Autonomous Game Asset Generation Pipeline — Architecture Specification

**Status:** Implemented. All components in this document exist under `assetpipe/` (plus the
top-level `themes/` packs) — see `assetpipe/README.md` for the module map, how to run the
pipeline, which spec §21 test tiers run in pure-Python CI versus which need the real pinned
toolchain, and the invariants to preserve. Where this document and the tested code differ
(currently only the §13.4 tiling checks, corrected to relative gradient ratios), the code +
its tests are authoritative and this document has been updated to match.
**Version:** 1.1
**Date:** 2026-07-06
**Audience:** An implementing model/engineer. This document is intended to be sufficient to build
the pipeline without further clarification from the author.

---

## Table of contents

1. [Scope, goals, non-goals](#1-scope-goals-non-goals)
2. [Grounding in existing reference material](#2-grounding-in-existing-reference-material)
3. [Toolchain and pinned versions](#3-toolchain-and-pinned-versions)
4. [Architecture overview and data flow](#4-architecture-overview-and-data-flow)
5. [File formats at every boundary](#5-file-formats-at-every-boundary)
6. [Input contract: the Asset Request](#6-input-contract-the-asset-request)
7. [Theme packs](#7-theme-packs)
8. [Platform budget profiles](#8-platform-budget-profiles)
9. [Stage G — Procedural object generation](#9-stage-g--procedural-object-generation)
10. [Stage M — Material & texture generation (original design)](#10-stage-m--material--texture-generation-original-design)
11. [Stage B — Skyboxes and backgrounds](#11-stage-b--skyboxes-and-backgrounds)
12. [Stage X — glTF 2.0 export](#12-stage-x--gltf-20-export)
13. [Stage V1 — Static validation gate (scriptable checks)](#13-stage-v1--static-validation-gate-scriptable-checks)
14. [Stage R — Headless render harness](#14-stage-r--headless-render-harness)
15. [Stage V2 — Vision inspection: rubric and output schema](#15-stage-v2--vision-inspection-rubric-and-output-schema)
16. [Stage F — The autonomous repair loop](#16-stage-f--the-autonomous-repair-loop)
17. [Run state, logging, and post-hoc debuggability](#17-run-state-logging-and-post-hoc-debuggability)
18. [Engine adapter interface](#18-engine-adapter-interface)
19. [Godot adapter (concrete)](#19-godot-adapter-concrete)
20. [Orchestrator: CLI, config, process model](#20-orchestrator-cli-config-process-model)
21. [Testing the pipeline itself](#21-testing-the-pipeline-itself)
22. [Failure modes and mitigations](#22-failure-modes-and-mitigations)
23. [Suggested implementation order](#23-suggested-implementation-order)
24. [Appendix A — Vision inspection prompt template](#appendix-a--vision-inspection-prompt-template)
25. [Appendix B — Defect taxonomy](#appendix-b--defect-taxonomy)

---

## 1. Scope, goals, non-goals

### 1.1 Goals

Build a pipeline that, given a structured **asset request** (e.g. "sci-fi crate, small prop,
mobile budget"), autonomously produces a **validated, engine-ready 3D asset** (or texture set /
skybox / background) with:

- **Blender (headless `bpy`/`bmesh`)** as the sole authoring tool.
- **glTF 2.0 (`.glb`) with PBR metallic-roughness materials** as the canonical interchange
  format at the pipeline boundary. Everything upstream of the engine adapter is engine-neutral.
- **Zero human review.** Every quality gate is either a deterministic script check or a
  vision-model check against an explicit rubric with structured output. There is no step whose
  definition involves a person looking at anything.
- **Bounded, debuggable iteration.** Failed assets are repaired via targeted fixes in a loop
  with hard stopping conditions; assets that exhaust the loop are delivered as flagged
  best-effort output with a machine-written diagnosis, never blocking the batch.
- **Godot 4.x as the first delivery target**, via a thin, swappable engine adapter.

### 1.2 Non-goals (v1)

- **Realistic/AAA characters.** Characters are supported only in stylized/low-poly form
  (parameterized modular humanoids). Sculpted realistic characters, facial rigs, and cloth
  simulation are out of scope.
- **Animation authoring beyond a basic rig.** V1 exports a skinned humanoid rest pose with a
  standard bone naming convention; animation clips are out of scope.
- **Diffusion/image-generation models for textures.** All texture/material content is
  procedural (Blender shader node graphs baked to maps). This keeps generation deterministic
  and re-runnable. An image-gen texture backend can be added later behind the same
  `MaterialSource` interface (§10.6), but v1 must not depend on one.
- **Runtime performance work** (draw-call batching in the engine, shader compilation, etc.).
  The budget discipline from the `game-developer` skill is applied to *asset generation
  targets* (poly counts, texture sizes, LODs), not to game code.

### 1.3 Definitions

- **Asset**: one deliverable unit — a mesh asset (prop, character, environment piece, modular
  kit piece), a tiling texture set, a skybox, or a layered 2D background.
- **Iteration**: one pass of (generate-or-fix → validate → render → inspect) for one asset.
- **Run**: one orchestrator invocation processing a batch of asset requests.
- **Blocker / warning**: severities of check failures. Blockers gate delivery; warnings are
  recorded and opportunistically fixed but never gate.

---

## 2. Grounding in existing reference material

Two vendored skills in this repo (`.claude/skills/`, provenance in `.claude/skills/README.md`)
ground parts of this design. **The implementer should read both.**

| Piece of this spec | Grounded in | What is reused |
|---|---|---|
| §12 glTF export, §9 mesh cleanup ops, LOD generation | `blender-web-pipeline` (freshtechbro/claudedesignskills) | Headless `blender --background --python` invocation pattern; `bpy.ops.export_scene.gltf(...)` parameter set; decimation/LOD/`remove_doubles`/triangulate recipes; texture downscale-before-export pattern; batch-processing script shape. **Correction applied:** that skill defaults to Draco compression; this pipeline must NOT Draco-compress the canonical `.glb` (§12.3) because Godot's importer does not support `KHR_draco_mesh_compression`. Compression is an adapter concern. |
| §8 budgets, §9.6 LODs, general "budget-first" posture | `game-developer` (Jeffallan/claude-skills) | The performance discipline — explicit numeric budgets validated at a checkpoint before proceeding, LOD thinking, "profile/validate before shipping" — transplanted from runtime code to asset targets. Its Unity/Unreal code patterns are *not* used. |

**Explicit gap statement (required by the design brief):** neither vendored skill, nor any
other verified existing skill (see `docs/resources/claude-skills-for-godot.md`), covers
**texture/material content generation**. §10 (procedural PBR material synthesis and baking) and
§11 (skyboxes/backgrounds) are **original design in this document** and carry the highest
implementation risk. The implementer should build §10's golden-fixture tests (§21) first for
exactly this reason.

**Companion skills (original, authored in this repo):** four skills under `.claude/skills/`
encode the expert knowledge for the gap areas and should be loaded when implementing the
corresponding stages — `blender-procedural-geometry` (§9, §13.1–13.2), `pbr-material-baking`
(§10, §12.2, §13.3–13.4), `asset-visual-qa` (§13.5–§15), `godot-asset-import` (§18–§19).
They are original (not externally verified) reference knowledge, distinct from the two
vendored skills above; the pipeline test tiers in §21 are what validate their patterns in
practice.

---

## 3. Toolchain and pinned versions

All versions are pinned; the pipeline must refuse to run (hard error at startup, listed in the
run manifest) if a component reports a different major/minor version, because mesh hashes,
bake output, and importer behavior all drift across versions.

| Component | Version | Role |
|---|---|---|
| Blender | **4.2 LTS** (headless, `blender --background`) | Authoring, baking, rendering, glTF export |
| Python (orchestrator) | **3.11** | Orchestrator package `assetpipe` (separate from Blender's bundled Python) |
| Python (inside Blender) | Blender-bundled (3.11 in 4.2) | Generation/validation scripts executed via `--python` |
| glTF-Validator (Khronos) | **2.0.0-dev.3.x** CLI (`gltf_validator`) | Post-export structural validation |
| `gltf-transform` (Node CLI) | **v4.x** | Inspection (`gltf-transform inspect --format json`) and adapter-side optimization only |
| Godot | **4.3+** (`godot --headless`) | Godot adapter import + verification |
| Anthropic API | model id **`claude-fable-5`** (configurable string in `pipeline.yaml`) | Vision inspection (§15) and constrained fix planning (§16.4) |
| Pillow / NumPy | latest 10.x / 1.26+ | Scripted image analytics (§13.4, §14.5) |

Blender determinism requirements: fixed Cycles seed (`scene.cycles.seed = 0`), denoiser
OpenImageDenoise with fixed prefilter, `scene.cycles.use_animated_seed = False`, all
generation RNG from `random.Random(request.seed)` / `numpy.random.default_rng(request.seed)` —
**never** the global RNG, never wall-clock.

---

## 4. Architecture overview and data flow

### 4.1 Stage graph

```
 asset_request.json ─┐
 theme pack ─────────┤
 platform profile ───┘
        │
        ▼
 ┌─────────────┐   .blend + params.json
 │ G  Generate │──────────────────────────┐
 │ (bpy/bmesh) │                          │
 └─────────────┘                          ▼
 ┌─────────────┐   PBR maps (.png)  ┌──────────────┐
 │ M  Material │───────────────────▶│ X  Export    │── canonical .glb
 │ synth+bake  │                    │ glTF 2.0     │        │
 └─────────────┘                    └──────────────┘        │
 (B Backgrounds/skyboxes: parallel branch, own checks)      ▼
                                                   ┌────────────────┐
                                              ┌────│ V1 Static gate │ FAIL → F
                                              │    │ (script checks)│
                                              │    └────────────────┘
                                              ▼ PASS
                                     ┌────────────────┐
                                     │ R  Headless    │  renders/*.png
                                     │ render harness │
                                     └────────────────┘
                                              │
                                              ▼
                                     ┌────────────────┐
                                     │ V2 Vision      │ vision_report.json
                                     │ inspection     │
                                     └────────────────┘
                                        PASS │   │ FAIL
                                             │   ▼
                                             │  ┌────────────────┐
                                             │  │ F  Fix planner │── targeted fix → back to V1
                                             │  │  + applicator  │   (or G/M partial regen)
                                             │  └────────────────┘
                                             │        │ iteration cap exhausted
                                             ▼        ▼
                                     ┌────────────────────────────┐
                                     │ D  Deliver                 │  status: "validated"
                                     │ manifest + engine adapter  │  or "best_effort"+diagnosis
                                     └────────────────────────────┘
```

### 4.2 Per-asset state machine

Each asset is driven by an explicit state machine persisted after every transition
(§17), so a crashed run resumes exactly where it stopped.

```
PENDING → GENERATING → STATIC_VALIDATING → RENDERING → INSPECTING
   ▲                        │ fail              │            │ fail
   │                        ▼                   │            ▼
   │ (full regen,       FIXING ◀────────────────┴──────── FIX_PLANNING
   │  new seed)             │ applied
   └────────────────────────┘  (loops via STATIC_VALIDATING)

Terminal states: VALIDATED | BEST_EFFORT | HARD_FAILED
```

- `VALIDATED`: all blocker checks (static + vision) pass.
- `BEST_EFFORT`: iteration cap exhausted; asset is still delivered, flagged, with diagnosis (§16.6).
- `HARD_FAILED`: unrecoverable infrastructure error (Blender crashed on every retry, disk
  full). No asset output; error recorded; batch continues.

### 4.3 Process model

The orchestrator (plain Python 3.11) never imports `bpy`. Every Blender-touching stage is a
**separate subprocess** — `blender --background [file.blend] --python <stage_script>.py -- --args-json <path>` —
with a per-stage timeout (default 600 s generate/bake, 900 s render) and one automatic retry
on nonzero exit. Stage scripts communicate exclusively through files in the iteration
directory (§17.1); stdout/stderr are captured to `logs/`. This isolates Blender crashes,
keeps stages independently re-runnable by hand, and makes every stage testable in isolation.

---

## 5. File formats at every boundary

| Boundary | Producer → Consumer | Format | Notes |
|---|---|---|---|
| Request intake | user/batch file → orchestrator | `asset_request.json` (schema §6) | One file per asset or a batch array |
| Theme definition | repo `themes/` → all stages | `theme.json` (schema §7) + material recipes (Python modules) | Versioned in git |
| Generation output | G → M, X | `.blend` (single scene, conventions §9.4) + `params.json` (resolved generator params) | `params.json` is the fix loop's editing surface |
| Baked maps | M → X, V1 | PNG: `albedo.png` (sRGB), `normal.png` (linear, OpenGL Y+), `orm.png` (linear; R=AO, G=roughness, B=metallic), optional `emissive.png` (sRGB) | Always these filenames inside the iteration dir |
| Canonical asset | X → V1, R, D | `.glb` (glTF 2.0 binary, uncompressed, tangents included) | THE interchange artifact; §12 |
| Static report | V1 → orchestrator, F | `static_report.json` (schema §13.6) | |
| Renders | R → V2, archive | `renders/<view_id>.png` 1024×1024 + `contact_sheet_{n}.png` | View IDs §14.2 |
| Vision report | V2 → orchestrator, F | `vision_report.json` (schema §15.4) | |
| Fix plan | F → G/M/X | `fix_plan.json` (schema §16.3) | Includes JSON-patch of `params.json` when applicable |
| Iteration history | every stage → archive | `history.jsonl` (append-only, §17.2) | |
| Delivery | D → engine adapter | `manifest.json` (§17.3) + `.glb` + preview renders + (best-effort) `diagnosis.md` | |
| Godot delivery | adapter → Godot project | files under `res://assets/generated/…` + import verification report | §19 |
| Skybox | B → D | `.exr` (equirect HDR) + `.png` LDR preview | §11.1 |
| Background | B → D | layered `layer_NN_<name>.png` (RGBA) + `background.json` (parallax metadata) | §11.3 |

---

## 6. Input contract: the Asset Request

One JSON object per asset. JSON Schema (draft 2020-12) to be committed at
`assetpipe/schemas/asset_request.schema.json`; normative shape:

```json
{
  "schema_version": 1,
  "asset_id": "scifi_crate_small_01",          // ^[a-z0-9_]{3,64}$, unique per run
  "category": "prop_small",                    // enum, see table below
  "theme": "scifi_industrial",                 // must match a theme pack id (§7)
  "platform_profile": "web",                   // enum: desktop_high|desktop_mid|mobile|web (§8)
  "seed": 421337,                              // uint32; drives ALL randomness for this asset
  "description": "A small reinforced sci-fi supply crate with glowing status strip",
                                               // free text; used by generator selection AND
                                               // verbatim in the vision rubric (R5 silhouette check)
  "generator": "props/crate",                  // optional; explicit generator recipe id.
                                               // If omitted, resolved from category+description
                                               // via the generator registry keyword index (§9.2)
  "param_overrides": {},                       // optional; validated against generator PARAM_SCHEMA
  "material_overrides": {},                    // optional; validated against material recipe schema
  "budget_overrides": {                        // optional; may only TIGHTEN profile budgets
    "max_triangles": 1200
  },
  "topology": "closed",                        // "closed" (default) | "open" — open permits
                                               // intentionally non-manifold cards/planes (§13.1)
  "lods": "auto",                              // "auto" (profile default) | "none" | [0.5, 0.25]
  "tags": ["kit:supply", "interactive"]        // freeform, passed through to manifest
}
```

`category` enum and intent:

| category | Meaning | Example |
|---|---|---|
| `prop_small` | Hand-held/desk-scale prop | crate, lantern, bottle |
| `prop_hero` | Focal-point prop, closer camera | vehicle, throne, reactor |
| `character_primary` | Playable/NPC stylized humanoid | knight, engineer |
| `character_background` | Crowd-filler humanoid | villager |
| `environment_piece` | Large static scenery | rock formation, tree, ruin |
| `modular_kit_piece` | Grid-snapping construction piece | wall, floor, doorway |
| `tiling_texture_set` | No mesh; seamless PBR texture set | metal plating, cobblestone |
| `skybox` | Equirect HDRI environment | night sky, alien sunset |
| `background_2d` | Layered parallax background | mountain silhouettes |

Validation at intake (fail fast, before any Blender process): schema-validate, verify theme
and profile exist, verify `generator` (if given) exists and its category matches, verify
`budget_overrides` only tighten. Intake failures are reported per-asset in the run manifest;
they consume zero iterations.

---

## 7. Theme packs

A theme pack is a directory `themes/<theme_id>/` containing `theme.json` plus material recipe
modules. Themes are data + small code, versioned in git, and are the single source of truth
for "what does sci-fi look like" — generators and the vision rubric both read from them, which
is what makes the vision check *against the theme* well-defined rather than vibes.

```json
{
  "schema_version": 1,
  "theme_id": "scifi_industrial",
  "display_name": "Sci-Fi Industrial",
  "palette": {
    "primary":   ["#2E3A46", "#41525F"],
    "secondary": ["#8C959D", "#B0B7BD"],
    "accent":    ["#00C2A8", "#FF6A00"],
    "emissive":  ["#00C2A8", "#FFD24A"],
    "forbidden": ["#8B4513"]
  },
  "materials": [
    "scifi_hull_metal", "scifi_scuffed_paint", "scifi_rubber_trim",
    "scifi_emissive_strip", "scifi_deck_plate"
  ],
  "silhouette_language": "Chamfered boxes and cylinders, panel lines, greebles on 10-20% of surface area, functional details (vents, bolts, handles). No organic curves except cabling.",
  "wear_range": [0.15, 0.55],
  "detail_density_range": [0.3, 0.7],
  "vision_style_brief": "Assets should read as functional, mass-produced industrial sci-fi equipment: desaturated blue-grey metals, bright teal or orange accents used sparingly (<10% of surface), visible panel seams and edge wear on chamfers, subtle emissive elements. NOT: fantasy ornamentation, wood, bright saturated primary colors covering large areas.",
  "skybox_defaults": { "recipe": "space_station_interior", "sun_elevation_deg": 25 }
}
```

Field roles:

- `palette.*`: hex lists sampled (seeded) by material recipes. `forbidden` colors are used by
  vision check R12 (§15.2) — the rubric literally tells the model these hues must not appear
  as dominant surface colors.
- `materials`: ids of material recipes (§10.2) legal for this theme. Generators may only
  request materials from this list.
- `silhouette_language` and `vision_style_brief`: injected verbatim into the vision prompt
  (Appendix A) for checks R5/R12. Writing these well is part of authoring a theme.
- `anti_style` (optional): a **NOT-list** — a first-class array of short phrases naming
  what the theme must *not* look like (e.g. `"wood as a dominant surface"`,
  `"teal/orange sci-fi accents"`). Promoted from the `NOT:` clause that used to live only
  inside `vision_style_brief`, it is injected into the vision prompt as an explicit
  "this theme is NOT: …" line. Borrowed from Snittet's spelbygge brief, where the NOT-list
  is the single strongest guard against scope drift; here it hardens R5/R12 against a
  correct-but-off-theme asset (a knight rendered in the sci-fi theme). Absent is legal —
  the brief still carries the intent — but every shipped theme declares one.
- `wear_range` / `detail_density_range`: clamp ranges for the corresponding generator/material
  scalar params.

V1 ships with four theme packs to prove parameterization: `scifi_industrial`,
`fantasy_medieval`, `lowpoly_stylized` (flat-shaded, vertex-color-driven, near-zero textures),
and `medieval_realistic`. `lowpoly_stylized` is important as the degenerate case: its material
recipes emit flat albedo + constant roughness and skip normal/ORM baking; validators must not
assume every asset has all maps (the glTF material declares what it uses).

---

## 8. Platform budget profiles

Budgets follow the `game-developer` skill's discipline — explicit numeric targets, validated
at a checkpoint (V1), never "optimize later" — applied to asset targets. Profiles live in
`assetpipe/profiles/<name>.json` and are enforceable by script alone.

### 8.1 Triangle budgets (max triangles, LOD0, per asset)

| category | desktop_high | desktop_mid | web | mobile |
|---|---:|---:|---:|---:|
| prop_small | 3 000 | 1 500 | 800 | 500 |
| prop_hero | 20 000 | 10 000 | 6 000 | 3 000 |
| character_primary | 30 000 | 15 000 | 10 000 | 6 000 |
| character_background | 8 000 | 4 000 | 2 500 | 1 500 |
| environment_piece | 10 000 | 5 000 | 3 000 | 1 500 |
| modular_kit_piece | 5 000 | 2 500 | 1 500 | 800 |

There is also a per-category **minimum** (5% of the max) — a 12-triangle "crate" passes every
mesh-validity check but is garbage; the floor forces the silhouette check to have something
to look at and catches degenerate generator output cheaply.

### 8.2 Texture budgets (max resolution per map, pixels, square, power-of-two required)

| map | desktop_high | desktop_mid | web | mobile |
|---|---:|---:|---:|---:|
| albedo / normal / ORM (props, kit, env) | 2048 | 1024 | 1024 | 512 |
| albedo / normal / ORM (`prop_hero`, `character_primary`) | 4096 | 2048 | 1024 | 1024 |
| emissive | 1024 | 512 | 512 | 256 |
| tiling_texture_set (all maps) | 2048 | 2048 | 1024 | 1024 |
| skybox equirect | 4096×2048 EXR | 4096×2048 | 2048×1024 | 2048×1024 |
| background_2d layer | 4096×2304 | 2048×1152 | 2048×1152 | 1024×576 |

### 8.3 File-size caps (canonical `.glb`, uncompressed)

| category | desktop_high | desktop_mid | web | mobile |
|---|---:|---:|---:|---:|
| prop_small / modular_kit_piece | 8 MB | 4 MB | 2 MB | 1 MB |
| prop_hero / character_primary | 32 MB | 16 MB | 6 MB | 4 MB |
| environment_piece / character_background | 12 MB | 6 MB | 3 MB | 1.5 MB |

(Web caps assume the web adapter will additionally meshopt/Draco-compress — per the
`blender-web-pipeline` skill's targets of <5 MB delivered — but the cap applies to the
*uncompressed canonical* file so it is engine-neutral.)

### 8.4 LOD policy

`lods: "auto"` resolves per profile: `desktop_*` → ratios `[0.5, 0.25]` (LOD1, LOD2);
`web`/`mobile` → `[0.4]`. LODs are generated with the Decimate-modifier recipe from
`blender-web-pipeline`'s `generate_lods.py` (collapse + triangulate), then **each LOD is
re-validated** against the mesh checks (§13.1 — decimation is the classic source of
degenerate triangles) and included in the same `.glb` as sibling meshes named
`<asset_id>_LOD1`, `<asset_id>_LOD2`. Rationale for names-not-extensions: `MSFT_lod` has poor
importer support; name-suffix convention is trivially consumed by every adapter, and the
Godot adapter (§19) can also simply ignore them in favor of Godot 4's automatic mesh LOD.

---

## 9. Stage G — Procedural object generation

### 9.1 Generator recipes

A generator recipe is a Python module under `assetpipe/generators/<category_group>/<name>.py`
executed *inside Blender*. Contract:

```python
# assetpipe/generators/props/crate.py
PARAM_SCHEMA = {  # JSON Schema for this generator's parameters. Every param MUST declare
  "type": "object",  # min/max bounds — the fix loop (§16.4) may only move values inside them.
  "properties": {
    "width_m":        {"type": "number", "minimum": 0.3, "maximum": 1.2, "default": 0.6},
    "height_m":       {"type": "number", "minimum": 0.3, "maximum": 1.2, "default": 0.6},
    "chamfer":        {"type": "number", "minimum": 0.0, "maximum": 0.08, "default": 0.02},
    "panel_lines":    {"type": "integer", "minimum": 0, "maximum": 6, "default": 2},
    "greeble_density":{"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.4},
    "materials":      {"type": "array", "items": {"type": "string"}}  # theme material ids per slot
  },
  "additionalProperties": False
}
CATEGORY = "prop_small"
KEYWORDS = ["crate", "box", "container", "supply"]   # for registry resolution from description

def generate(params: dict, rng, theme: dict) -> "bpy.types.Object":
    """Build and return the root object. Must be deterministic given (params, rng state).
    Must not touch bpy random ops without passing a seed derived from rng."""
```

Recipes build geometry with `bmesh` (preferred, testable) and modifiers (applied before
return). Character recipes assemble parameterized modular parts (head/torso/limb primitives
with lattice-based proportion controls) onto a fixed humanoid armature using the **Godot/glTF
humanoid bone names** (`Hips, Spine, Chest, Neck, Head, Left/RightUpperArm…` per the Godot
`SkeletonProfileHumanoid`), automatic weights, then weight cleanup (max 4 influences,
normalize — validated in §13.1).

### 9.2 Generator registry and resolution

`assetpipe/generators/registry.py` imports all recipe modules and indexes them by
`(CATEGORY, KEYWORDS)`. Resolution for a request without an explicit `generator`:
filter by category → score by keyword overlap with `description` (lowercased token match) →
highest score wins; tie → lexicographic first (determinism). No match → intake error
`NO_GENERATOR` (not an iteration failure). V1 must ship at least: `props/crate`,
`props/barrel`, `props/lantern`, `env/rock`, `env/tree_lowpoly`, `kit/wall`, `kit/floor`,
`kit/doorway`, `char/humanoid_stylized`. That set is small on purpose; the architecture is
the deliverable, recipes accrete over time.

### 9.3 Parameter resolution

Final `params.json` = recipe defaults → theme clamps (`wear_range` etc.) → seeded jitter
(±10% uniform on numeric params, from `rng`) → `param_overrides` (validated). The resolved
dict is written to `params.json` **before** generation so a crash still leaves the exact
inputs on disk, and so the fix loop has a canonical editing surface.

### 9.4 Scene conventions (enforced by V1)

- Units: metric, 1 BU = 1 m. Real-world scale per category (crate 0.3–1.2 m, humanoid
  1.6–2.0 m tall, kit wall exactly 3 m × 3 m footprint…) — each recipe documents its range
  and V1 checks the bounding box against the recipe's declared `BBOX_RANGE`.
- Origin: props/kit/env pieces at the base center (min-Z plane, XY centroid), z=0 is the
  floor contact. Characters: origin at feet. Enforced: `|origin - expected| < 1 mm`.
- Transforms applied: object scale = (1,1,1), rotation = identity, location = origin.
- Orientation: +Y is "front" in Blender (glTF exporter converts to its +Z-forward/-Y…
  handled by exporter; recipes only need to agree with each other).
- One root object per asset, children allowed; collection named `EXPORT`.
- Modular kit pieces: snap sockets on a 0.5 m grid; each socket is an empty named
  `SOCKET_<dir>_<i>` at exact grid coordinates; V1 verifies socket positions are on-grid to
  within 0.1 mm (this is what makes kit pieces actually interlock without a human eyeballing it).

### 9.5 Mesh finishing pass (always run, end of G)

In order: apply modifiers → `remove_doubles(threshold=1e-5)` → limited dissolve OFF (never;
destroys UV intent) → recalc normals outside → triangulate (`quads_convert_to_tris`, beauty)
→ if triangle count > budget: Decimate(collapse) in steps of ratio 0.85 until ≤ budget or 5
steps (then it's a V1 failure with defect `OVER_BUDGET_UNFIXABLE`). This is the
`blender-web-pipeline` pre-export checklist, made unconditional and ordered.

### 9.6 UV unwrapping

Per-recipe: recipes may place explicit seams (preferred for kit pieces and anything tiling);
fallback is Smart UV Project (`angle_limit=66°, island_margin` computed as
`4 / texture_resolution` so islands keep a ≥4-texel bake bleed margin at the target
resolution). Tiling-material surfaces (kit walls/floors) instead get box-projection UVs at a
fixed world-space texel density (default 256 px/m) and are flagged `uv_mode: "tiling"` in
mesh custom properties so V1 skips the 0–1 bounds check for them (§13.2).

---

## 10. Stage M — Material & texture generation (original design)

> **Note:** as stated in §2, no verified existing skill covers this. This section is original
> design; treat its fixture tests (§21.2) as the first thing to build.

### 10.1 Approach

All materials are **procedural Blender shader node graphs, baked to PBR maps**. A *material
recipe* constructs a node graph parameterized by theme palette + scalar params; the bake step
renders each channel to textures via Cycles bake. Nothing hand-painted, nothing fetched from
the internet, no image-model calls → deterministic, seed-reproducible, license-clean.

### 10.2 Material recipe contract

```python
# themes/scifi_industrial/materials/scifi_hull_metal.py
PARAM_SCHEMA = { ... }   # same rules as generator schemas: every param bounded
def build(nt: "bpy.types.NodeTree", params: dict, rng, palette: dict) -> None:
    """Populate node tree ending in a Principled BSDF wired to Material Output."""
BAKES = ["albedo", "normal", "orm"]          # which maps this material actually produces
TILING = False                               # True → must pass seamlessness checks (§13.4)
```

Recipes compose from a shared library `assetpipe/matlib/` of building-block node groups
(implemented once, reused everywhere): `NoiseBreakup`, `EdgeWear` (uses bevel-node +
pointiness-driven mask to lighten/roughen edges), `PanelLines` (voronoi/brick-based grooves
feeding both albedo darkening and bump), `Grunge` (layered musgrave), `MetalBase`,
`WoodGrain`, `StoneBase`, `EmissiveStrip`. Height-like outputs are wired into a Bump node so
the **normal map is produced by baking the `NORMAL` pass** of the shaded surface — not by
converting a height PNG afterward (fewer moving parts, correct tangent space for free).

### 10.3 Bake procedure (per mesh asset)

Cycles, GPU-off deterministic CPU mode acceptable (config flag), `samples=16` for color
passes / `64` for normal+AO, margin = 8 px (bleed), using the asset's final UVs:

1. `albedo.png` — the Base Color input signal baked via the EMIT reroute (same trick as
   step 3). sRGB. *(Corrected from "bake type `DIFFUSE`, color only": the diffuse color
   pass is weighted by the diffuse closure, which is zero wherever metallic=1 — fully-metal
   materials bake an all-black albedo, tripping S16. EMIT of Base Color is exact for every
   metallic value and matches glTF `baseColorTexture` semantics; verified on Blender 4.2.)*
2. `normal.png` — bake type `NORMAL`, space `TANGENT`, swizzle **OpenGL (+Y green)** — this is
   the glTF convention; the Godot importer expects glTF convention and handles it.
3. `orm.png` — three separate scalar bakes (AO via `AO` bake 64 samples; roughness and
   metallic via `EMIT`-rerouting trick: temporarily wire the scalar socket into an Emission
   shader and bake `EMIT`) composited into R/G/B with NumPy. Linear.
4. `emissive.png` — only if recipe declares it; bake `EMIT` with the real emission wiring.

For `tiling_texture_set` requests there is no asset mesh; the bake target is a unit plane
with 0–1 UVs and the shader is built in **4D-periodic mode**: every noise/voronoi node gets
its vector input remapped so the texture is mathematically periodic over the tile — recipes
must use the `matlib.PeriodicCoords` group (implements torus-mapping of UV into a 4D noise
domain, the standard seamless-noise construction). Seamlessness is then *verified*, not
assumed (§13.4).

### 10.4 Map post-processing (NumPy/Pillow, outside Blender)

- Resize to profile budget if baked larger (bake at budget resolution directly by default).
- Normal map renormalization: renormalize XYZ per pixel; assert mean blue ≥ 0.7.
- ORM channel clamps: metallic snapped to {0,1} ± recipe-declared tolerance unless recipe
  declares `blended_metal: true` (glTF PBR treats mid-metal as usually-an-error).
- Dither 16→8 bit with seeded blue-noise to avoid banding (banding is a known vision-check
  trigger, R8).

### 10.5 What "matches the theme" means mechanically

Recipes may only sample colors from the theme palette (with bounded HSV jitter: ±4° hue,
±10% sat/val). This guarantee is what lets vision check R12 be strict: any dominant hue far
from the palette is a *pipeline bug*, not taste.

### 10.6 `MaterialSource` interface (future image-gen backend)

`get_maps(request, mesh_uv_layout) -> {albedo, normal, orm, emissive}` — the bake pipeline
above is the v1 implementation. A future diffusion-based source must return the same map set
and pass the same V1/V2 gates. Documented so the implementer keeps the seam clean; not built in v1.

---

## 11. Stage B — Skyboxes and backgrounds

### 11.1 Skyboxes

Built in Blender world-shader space: recipe composes Sky Texture (Nishita) and/or procedural
star fields (thresholded high-frequency voronoi on the world vector), nebula/cloud layers
(musgrave + color ramps from palette), optional celestial bodies (disc via spherical
distance). Rendered to **equirectangular EXR** (float16) by baking the world: a camera-based
360° render (Cycles, panoramic camera, equirect lens) at profile resolution (§8.2), plus a
tonemapped `preview.png`.

Skybox-specific validations:
- **Horizontal wrap** (scripted): mean abs diff between leftmost and rightmost 4-px columns
  ≤ 1.5/255 (on the tonemapped preview).
- **Pole pinching** (vision): renders of straight-up and straight-down views (two extra
  perspective renders sampling the sky) checked by rubric R11.
- **Dynamic range sanity** (scripted): EXR max luminance within [1.0, 100 000]; ≥0.5% of
  pixels above 1.0 if recipe declares a sun (so it actually functions as an HDRI light source).

### 11.2 Skybox delivery

Canonical artifact: `skybox.exr` (equirect) + `preview.png`. glTF has no skybox concept —
this is the one asset class delivered beside, not inside, a `.glb`; the manifest marks it
`container: "exr"` and each adapter maps it (Godot: `PanoramaSkyMaterial`, §19.5).

### 11.3 2D backgrounds

Layered parallax backgrounds are produced by building a simple 3D scene from `environment`
recipes (mountain ridges = displaced planes, silhouette props), then rendering **per-layer
RGBA passes** by isolating depth bands (collections per band, one render each, transparent
film). Output: `layer_00_far.png … layer_NN_near.png` + `background.json`:

```json
{ "layers": [ { "file": "layer_00_far.png", "parallax_factor": 0.1, "loop_x": true } ], 
  "viewport_design_size": [1920, 1080] }
```

`loop_x: true` layers must pass the horizontal-wrap scripted check (§11.1). Vision rubric
applies R1/R2/R8/R12 plus R10 on looping layers.

---

## 12. Stage X — glTF 2.0 export

### 12.1 Exporter invocation (normative parameter set)

Based on the `blender-web-pipeline` recommended settings, with the corrections noted:

```python
bpy.ops.export_scene.gltf(
    filepath=str(out_glb),
    export_format='GLB',
    use_selection=False,                # scene contains only the EXPORT collection
    export_apply=True,                  # apply modifiers
    export_yup=True,                    # glTF standard +Y up
    export_texcoords=True,
    export_normals=True,
    export_tangents=True,               # REQUIRED: normal maps need exported tangents;
                                        # engines' tangent regeneration differs → seams
    export_materials='EXPORT',
    export_image_format='AUTO',         # keeps PNGs as PNG (normal/ORM must stay lossless)
    export_cameras=False,
    export_lights=False,
    export_animations=False,            # v1: rest pose only
    export_skins=True,                  # characters
    export_draco_mesh_compression_enable=False,  # see §12.3
)
```

### 12.2 Material mapping

Materials must survive as **glTF metallic-roughness PBR**: baked maps are re-assigned to a
clean Principled BSDF before export (Base Color ← `albedo.png` [sRGB], ORM ←
`occlusionTexture`+`metallicRoughnessTexture` sharing `orm.png` [linear], Normal ←
`normal.png` via Normal Map node [Non-Color], Emissive ← `emissive.png` + strength). The
procedural node graph itself is **never** exported (it can't be, per the skill's pitfall #4
"materials look different"); only baked maps cross the boundary. `alphaMode` is `OPAQUE`
unless the recipe declares cutout foliage (`MASK`, `alphaCutoff` 0.5).

### 12.3 Compression policy (deviation from `blender-web-pipeline` defaults)

The canonical `.glb` is **uncompressed** (no `KHR_draco_mesh_compression`, no
`EXT_meshopt_compression`, no KTX2/`KHR_texture_basisu`). Rationale: Godot's glTF importer
does not support Draco; KTX2 support varies. Compression is a per-adapter delivery
optimization: a future web adapter runs `gltf-transform meshopt` / `ktx2` on its copy; the
Godot adapter delivers the canonical file as-is. Allowed extensions in the canonical file:
`KHR_materials_emissive_strength` only. V1 validates the extension whitelist (§13.5).

---

## 13. Stage V1 — Static validation gate (scriptable checks)

Every check below is a pure function returning
`{check_id, verdict: "pass"|"fail", severity: "blocker"|"warn", measured, threshold, details}`.
Checks S1–S13 run inside Blender on the pre-export scene; S14–S20 run in the orchestrator on
the exported `.glb` and PNGs. **All thresholds are config values** (defaults below) in
`pipeline.yaml → validation:` so tuning never means code edits.

### 13.1 Mesh validity (in-Blender, bmesh)

| ID | Check | Pass condition (default) | Severity |
|---|---|---|---|
| S1 | Non-manifold edges | `edges where not e.is_manifold` = 0. If `topology:"open"`: boundary edges (`len(e.link_faces)==1`) are exempt, but wire edges (0 faces) still = 0 | blocker |
| S2 | Degenerate faces | faces with `calc_area() < 1e-8` m² = 0 | blocker |
| S3 | Zero-length edges | edges with `calc_length() < 1e-6` m = 0 | blocker |
| S4 | Loose geometry | verts with no edges = 0; edges with no faces = 0 | blocker |
| S5 | Normal consistency | run `normals_make_consistent(inside=False)` on a *copy*; number of faces whose normal flipped = 0 (if >0 on the original: auto-fix in place once, re-check; still >0 → fail) | blocker |
| S6 | Transforms applied | scale=(1,1,1)±1e-6, rot=identity±1e-6; origin per §9.4 within 1 mm | blocker |
| S7 | Triangle budget | budget_min ≤ tris ≤ budget_max (per §8.1), per LOD level (each LOD ≤ its ratio × budget +10%) | blocker |
| S8 | Bounding box | within recipe `BBOX_RANGE` | blocker |
| S9 | Self-intersection | BVH overlap (`bmesh` + `mathutils.bvhtree.BVHTree.overlap` of mesh against itself, excluding adjacent faces) intersecting-face-pair count ≤ 0.5% of faces | warn |
| S10 | Kit sockets on-grid | every `SOCKET_*` empty within 0.1 mm of 0.5 m grid (kit pieces only) | blocker |
| S11 | Skin weights | max 4 influences/vertex; weights normalized to 1±1e-4; no vertex with total weight < 0.5 (characters only) | blocker |

### 13.2 UV checks (in-Blender)

| ID | Check | Pass condition | Severity |
|---|---|---|---|
| S12a | UV coverage | every face has a UV map; per-face UV area > 0 | blocker |
| S12b | Island overlap | overlapping UV area ≤ 0.5% of total shell area (rasterize islands at 1024², count multiply-covered texels). Islands recipe-flagged as mirrored are exempt | blocker |
| S12c | 0–1 bounds | all UVs in [−0.001, 1.001] — skipped for faces flagged `uv_mode:"tiling"` | blocker |
| S12d | Stretch / texel density | per-face texel density = sqrt(uv_area/world_area); ratio p95/p5 across faces ≤ 4.0; per-face conformal stretch (ratio of edge-length ratios UV-vs-world, max over edges) ≤ 2.5 for 99% of faces | warn (blocker if p95/p5 > 8) |
| S12e | Bake margin | min distance between distinct islands ≥ 4 texels at target resolution | warn |

### 13.3 Texture compliance (orchestrator, Pillow/NumPy)

| ID | Check | Pass condition | Severity |
|---|---|---|---|
| S14 | Resolution | each map square, power of two, ≤ profile budget (§8.2), ≥ 64 | blocker |
| S15 | Format | PNG, 8-bit; albedo/emissive tagged or treated sRGB, normal/ORM linear; no alpha channel in ORM/normal | blocker |
| S16 | Not-black / not-flat | albedo mean luminance ∈ [0.02, 0.98] and stddev > 0.01 (skipped if recipe declares `flat_color: true`, e.g. lowpoly_stylized) | blocker |
| S17 | Normal-map sanity | after normalization: mean(B) ≥ 0.7; ≥99% pixels with B ≥ 0.5; mean(R)≈mean(G)≈0.5±0.08 | blocker |
| S18 | ORM sanity | G (roughness) stddev < 0.5 (no noise blowout); B (metallic) mass within recipe declaration | warn |

### 13.4 Tiling validation (tiling texture sets and `loop_x` layers)

| ID | Check | Pass condition | Severity |
|---|---|---|---|
| S19a | Edge wrap | for each map: the gradient across the wrap seam (last row/col → first row/col) ≤ 1.5× the 95th-percentile interior adjacent-texel gradient (both axes for textures; X only for `loop_x` layers) | blocker |
| S19b | Offset continuity | roll image by 50% both axes; max per-line gradient in a ±2-texel window around the (now central) former borders ≤ 1.5× the interior p95 gradient | blocker |

**Why relative, not absolute:** in a seamless texture the opposite edges are wrap-*adjacent*
texels, not duplicates — an absolute edge-difference threshold (e.g. ≤ 2/255) rejects any
texture with high-frequency detail even when it tiles perfectly. Both checks therefore
compare the seam gradient to the texture's own interior gradient statistics. The window in
S19b catches the classic forgery mode where border texels were blended/cloned to match and
the discontinuity sits one texel inside the border (S19a alone is fooled by that; the test
suite proves both behaviors — see `assetpipe/tests/test_image_checks.py`). The *visual*
seam check on a 3×3 tiled render is R10, §15.

### 13.5 glTF structural compliance (orchestrator)

| ID | Check | Pass condition | Severity |
|---|---|---|---|
| S20a | Khronos validator | `gltf_validator -o` JSON report: 0 errors, 0 warnings of severity ≤ 1 (hints allowed) | blocker |
| S20b | Extension whitelist | `extensionsUsed ⊆ {KHR_materials_emissive_strength}` | blocker |
| S20c | Inventory match | via `gltf-transform inspect --format json`: mesh count, LOD names, material count, texture dimensions all match `params.json` expectations; tangents present on every primitive with a normal-textured material | blocker |
| S20d | File size | ≤ cap (§8.3) | blocker |

### 13.6 `static_report.json`

```json
{ "asset_id": "…", "iteration": 2, "stage": "V1",
  "verdict": "fail",
  "checks": [ { "check_id": "S12b", "verdict": "fail", "severity": "blocker",
                "measured": 0.031, "threshold": 0.005,
                "details": "overlap concentrated in island 7 (lid inner faces)",
                "defect": "UV_OVERLAP" } ],
  "timings_s": {"S1": 0.1}, "blender_version": "4.2.3", "toolchain_hash": "…" }
```

Any blocker fail short-circuits to Stage F (no render, no vision call — cheap fails first).

---

## 14. Stage R — Headless render harness

### 14.1 Invocation

`blender --background --python render_views.py -- --glb <asset.glb> --out renders/ --config render.json`.
Critically, the harness renders **the exported `.glb` re-imported into a clean scene** — not
the authoring `.blend` — so what the vision model inspects is exactly what engines will load
(this is how export-time texture/tangent bugs become visible).

Engine: Cycles, 128 samples, OIDN denoise, seed 0, film transparent OFF, color management
Filmic/AgX pinned (`view_transform='AgX'`, look `None`). Output 1024×1024 PNG per view.

### 14.2 View set (mesh assets)

Scene furniture, always present: 18% grey ground plane; a **1 m reference cube** (matte,
mid-grey, labeled by its known size in the prompt) 1.5 m to the asset's left — this is what
makes the scale check R6 objective; camera framed so asset occupies 55–75% of frame height
(computed from bounding box).

| view_id | Camera | Lighting rig |
|---|---|---|
| `turn_000 … turn_315` | 8 angles, 45° steps, elevation 15° | L1 |
| `high_045`, `high_225` | elevation 40° | L1 |
| `top` | straight down | L1 |
| `close_034` | ¾ view, framed to 2× zoom on densest-detail region (max greeble/panel param area, recipe-reported) | L1 |
| `lit_warm_045`, `lit_warm_225` | ¾ views | L2 |
| `lit_dark_090` | side view | L3 |
| `silhouette_000`, `silhouette_090` | front/side, emission-white asset on black | none |
| `normals_045`, `normals_225` | ¾ views, debug override material: geometry-normal→RGB, `Backfacing` socket → pure red emission | none |
| `uvcheck_045` | ¾ view, debug override: 8×8 UV checker texture | L1 |

Lighting rigs: **L1** neutral studio HDRI (bundled, license-clean, fixed) at strength 1.0;
**L2** warm directional sun (4500 K, 45° elevation) + weak fill; **L3** dim blue rim light
only (stress-test: black-texture and emissive problems hide in bright renders).

Characters additionally get `turn_*` at elevation 0° (eye level). Tiling texture sets are
rendered on a 3×3-tiled 3 m plane (`tile3x3_persp`, `tile3x3_top`) plus a 1×1 flat view and
a shaded sphere per lighting rig (`sphere_L1/L2/L3` — material response reading). Skyboxes:
6 axis-aligned perspective views from origin + `up_pole`/`down_pole`.

### 14.3 Contact sheets

Views are composited (Pillow) into ≤ 2×3-grid contact sheets at 1024 px per cell with the
`view_id` burned into each cell's corner strip — the vision model must cite view ids, and
burned-in labels remove ambiguity about which image is which. Full-res singles are kept for
crop re-queries (§15.5).

### 14.4 Determinism

Same `.glb` + same harness config ⇒ pixel-identical renders (CPU render path; document that
GPU rendering trades determinism for speed behind a config flag, default off in CI).

### 14.5 Scripted image analytics (pre-vision, cheap)

Before any model call, NumPy checks on the renders (blocker unless noted):
- **A1 not-empty:** each render's stddev > 2/255 and mean ∈ [0.01, 0.99].
- **A2 backface pixels:** in `normals_*` views, pure-red pixel fraction ≤ 0.1% (this makes
  inverted normals a *scripted* catch; vision R3 is the backstop).
- **A3 silhouette area:** in `silhouette_*`, white-pixel fraction ∈ [5%, 85%] (asset actually
  in frame and framed sanely).
- **A4 clipping:** ≤ 2% of pixels at 255 in L1 views (blown-out emissive/specular), warn.

---

## 15. Stage V2 — Vision inspection: rubric and output schema

### 15.1 Call structure

One Anthropic API call per asset per iteration (model id from config; default
`claude-fable-5`), temperature 0, images = the contact sheets, **forced tool use** with a
single tool `report_inspection` whose input schema is §15.4 — structured output is not
optional; a reply that fails schema validation is retried once, then treated as
infrastructure error (not asset failure). The full prompt template is Appendix A; it embeds
the request `description`, the theme's `silhouette_language`, `vision_style_brief`, palette
swatches (hex list), and the per-check instructions below.

### 15.2 Rubric (normative, complete)

Each check declares which views the model must base it on, and its severity.

| ID | Check | Views | Pass criteria given to the model (verbatim intent) | Severity |
|---|---|---|---|---|
| R1 | Render sanity | all | No view is empty, solid-color, or unrecognizable garbage | blocker |
| R2 | Texture presence | `turn_*`, `lit_dark_090` | No surfaces that are pure black, solid magenta, or showing an obvious placeholder/checker where the design calls for a real material. (The deliberate `uvcheck` view is exempt and labeled.) | blocker |
| R3 | Normal integrity | `normals_*` | No red regions (red = backfacing). Normal colors vary smoothly with surface direction; no large patches inconsistent with neighboring geometry | blocker |
| R4 | Texture seams | `close_034`, `turn_*` | No visible UV-seam discontinuities in albedo or shading: no hard color/lighting line where the surface is geometrically continuous | blocker |
| R5 | Silhouette & type match | `silhouette_*`, `turn_*` | The object reads unambiguously as: *{request.description}*, in the style: *{theme.silhouette_language}*. A person seeing only the silhouette should name the object type correctly | blocker |
| R6 | Scale plausibility | `turn_000`, `turn_090` | Against the labeled 1 m reference cube, the object's apparent size is plausible for its description (e.g. a supply crate should be ~0.4–1.2 m, not 3 m) | blocker |
| R7 | Material response | `lit_warm_*`, `lit_dark_090`, (`sphere_*` for texture sets) | Materials respond physically plausibly: metals show directional specular and reflect the warm light warmly; rough surfaces show broad soft highlights; nothing looks uniformly plastic when it should be metal, or mirror-like when it should be rough; emissive elements visible in the dark view | blocker |
| R8 | Shading artifacts | all L1 views, `close_034` | No banding, blocky/faceted shading on surfaces meant to be smooth, z-fighting patterns (striped flicker where two faces are coplanar), or stretched/smeared texels | warn (blocker if severe) |
| R9 | Structural coherence | `turn_*`, `high_*`, `top` | No parts floating disconnected from the body, no parts interpenetrating implausibly, no obviously missing pieces (e.g. a handle attached to nothing) | blocker |
| R10 | Tiling seams (texture sets, `loop_x` layers) | `tile3x3_*` | The 3×3 tiled surface shows no visible grid: no repeated obvious landmark feature, no seam lines at tile borders | blocker |
| R11 | Skybox integrity (skyboxes) | 6 axis views, `up_pole`, `down_pole` | No pinching/swirl artifacts at poles; horizon is level and continuous across adjacent views; no hard vertical seam | blocker |
| R12 | Theme palette conformance | `turn_*`, `lit_warm_*` | Dominant surface colors fall within the provided palette swatches (moderate lighting-induced shift allowed); no forbidden colors as major surface areas; overall look matches: *{theme.vision_style_brief}* | warn |

### 15.3 Anti-false-positive rules (encoded in the prompt and the orchestrator)

- Every `fail` must cite ≥1 specific `view_id` and a location phrase ("upper-left panel of
  the lid in turn_090").
- Geometry-class defects (R3, R9) must be visible in ≥2 views to be a `fail`; visible in
  exactly one view → verdict `uncertain`.
- The model is told renders are deterministic and lighting rigs are known — "dark side of the
  object in lit_dark_090 is expected; judge texture presence there by the rim-lit region."

### 15.4 `vision_report.json` (the `report_inspection` tool schema)

```json
{
  "asset_id": "scifi_crate_small_01",
  "iteration": 2,
  "checks": [
    {
      "check_id": "R4",
      "verdict": "fail",                     // "pass" | "fail" | "uncertain"
      "confidence": 0.86,                    // 0..1, model's own calibration
      "evidence_views": ["close_034", "turn_045"],
      "location": "vertical seam on the front-left chamfered edge",
      "defect_type": "VISIBLE_SEAM",         // MUST be from the taxonomy (Appendix B) when verdict != pass
      "description": "Albedo discontinuity where panel-line groove crosses the UV island boundary",
      "suggested_fix_hint": "increase bake margin or move seam off the chamfer"
    }
  ],
  "overall_impression": "one-sentence summary",   // logged, never gates
  "checks_not_applicable": ["R10", "R11"],
  "worst_thing": "the accent teal covers ~40% of the lid, reading as a toy not equipment"
                                                   // open-ended catch-all; logged, never gates
}
```

**`worst_thing` (open-ended catch-all).** Optional free-text field, outside the closed
R1–R12 rubric on purpose. It asks the inspector for the single thing that most makes the
asset *not read as the requested description in the theme style* — even when every check
passed. This is borrowed from Snittet's spelbygge feel-rubric ("what is the ugliest thing?
what breaks the intended feeling most?"), which exists because a closed checklist cannot
catch the technically-valid-but-soulless failure mode. It **never gates** (an asset is not
failed on it) and is not a defect; it is logged to `vision_report.json` / `history.jsonl`,
carried into best-effort `diagnosis.md`, and is the primary machine signal feeding the
human art-direction spot-check (see `docs/PIPELINE_DOCTRINE.md`).

Orchestrator-side validation: every rubric check applicable to the asset class appears
exactly once as `checks[]` or `checks_not_applicable[]`; `defect_type` ∈ taxonomy; else the
schema-retry path fires.

### 15.5 `uncertain` resolution policy (explicit)

For each `uncertain`: orchestrator crops the cited region from the full-res source views
(2× zoom around the location via a follow-up "locate the bounding box" is over-engineering —
instead the crop is the full-res version of the cited view, center-weighted 512² crop grid of
4) and re-queries **that check only**. If still `uncertain` after one re-query → treated as
`fail` with `confidence: 0.5` (fail-safe: an unverifiable asset is not a validated asset).
Max one re-query round per iteration.

### 15.6 Verdict aggregation

Iteration passes vision ⇔ zero blocker `fail`s after uncertainty resolution. Warn-severity
fails are recorded; if iterations remain *and* there are also blockers to fix, warn fixes may
ride along in the same fix plan; warn-only results never trigger another iteration.

---

## 16. Stage F — The autonomous repair loop

### 16.1 Iteration budget and escalation ladder

`max_iterations` default **5** per asset (config). Iteration 1 is the initial generation.
What Stage F is allowed to do escalates deterministically:

| Iteration producing the failure | Allowed fix classes |
|---|---|
| 1–2 | Targeted fixes only (table §16.2): mesh ops, UV ops, re-bake, param micro-adjust |
| 3 | Plus sub-component regeneration: re-run one generator sub-part or one material recipe with adjusted params (same seed) |
| 4 | Full regeneration with `seed' = seed + iteration` (allowed **once** per asset) |
| 5 | Targeted fixes only (last chance; no more regens) |

### 16.2 Deterministic defect→fix table (first resort)

The fix planner first consults this table keyed on `defect_type` (Appendix B lists all).
These fixes are code, not model output:

| defect_type | Targeted fix (code) |
|---|---|
| `NON_MANIFOLD` / `DEGENERATE_FACES` / `LOOSE_GEOMETRY` | bmesh cleanup pass: delete loose, dissolve degenerate, `remove_doubles` at 2× threshold; if S1 persists and `topology:"closed"`: apply `bpy.ops.mesh.fill_holes(sides=0)` on boundary loops |
| `INVERTED_NORMALS` | `normals_make_consistent(inside=False)` on affected object; clear custom split normals |
| `UV_OVERLAP` / `UV_OUT_OF_BOUNDS` | re-run Smart UV Project with island_margin × 1.5; pack islands |
| `UV_STRETCH` | add seams on edges with conformal stretch > threshold (auto seam-by-angle 60° on the offending island), re-unwrap that island |
| `VISIBLE_SEAM` | re-bake with margin × 2; if repeat offense: re-unwrap moving seams to concave edges (seam-from-islands off chamfer edges) |
| `MISSING_TEXTURE` / `BLACK_SURFACE` | verify map files exist & S16; re-run material assignment + re-export (most common cause: broken image link at export); second offense: re-bake |
| `OVER_BUDGET` | decimate ratio = budget/current × 0.97, re-run finishing pass |
| `TILING_SEAM` | re-bake with `PeriodicCoords` domain-scale snapped to integer periods; second offense: increase pattern scale param one step |
| `BANDING` | re-run post-processing dither at higher amplitude; check 16-bit intermediate present |
| `CLIPPED_EMISSIVE` | reduce emissive strength param 40% |
| `SCALE_IMPLAUSIBLE` | multiply size params toward recipe default bbox midpoint (factor from ratio of apparent-to-expected size, clamped to schema bounds) |
| `FLOATING_PART` / `INTERPENETRATION` | re-run part placement with snap-to-surface enabled for the named sub-part; else boolean-union pass |
| `POLE_PINCH` (skybox) | switch pole treatment param to `fade`; re-render |

### 16.3 `fix_plan.json`

```json
{ "asset_id": "…", "for_iteration": 2, "produces_iteration": 3,
  "defects_addressed": ["VISIBLE_SEAM"],
  "actions": [
    { "type": "table_fix", "fix_id": "rebake_margin_x2", "target": "material:scifi_hull_metal" },
    { "type": "param_patch", "patch": [ {"op": "replace", "path": "/greeble_density", "value": 0.3} ] }
  ],
  "planner": "table",            // "table" | "llm"
  "resume_stage": "M"            // earliest stage that must re-run: G | M | X
}
```

`resume_stage` implements "targeted, not full regeneration": a re-bake resumes at M with the
existing `.blend`; a UV fix resumes at G's finishing pass output; only geometry param patches
resume at G. Everything downstream of `resume_stage` re-runs (renders are never patched).

### 16.4 LLM fix planner (second resort, constrained)

If the table has no entry, or the same defect_type recurs after its table fix (tracked in
history), the planner makes one text-only model call: inputs are the defect entries, the
current `params.json`, and the generator/material `PARAM_SCHEMA`s; the required output is a
**JSON Patch against `params.json` only**, schema-validated, with every value clamped to its
schema bounds and at most 4 operations. The LLM can never emit code, never touch files, never
exceed param bounds — its whole action space is "turn these knobs." Invalid patch → retry
once → fall through to escalation ladder (regen if permitted, else this iteration burns).

### 16.5 Stopping conditions (explicit, complete)

An asset's loop terminates when the **first** of these holds:

1. **DONE:** V1 all-blockers-pass AND V2 zero blocker fails ⇒ state `VALIDATED`.
2. **CAP:** `iteration == max_iterations` and the last inspection still has blocker fails ⇒
   state `BEST_EFFORT` (§16.6). The pipeline moves to the next asset. Never blocks, never
   waits for a human, never retries past the cap.
3. **NO-PROGRESS:** two consecutive iterations produce identical defect sets (same
   `(check_id, defect_type)` multiset) *and* the escalation ladder has no stronger action
   left ⇒ early `BEST_EFFORT` (don't burn remaining iterations on a fixpoint).
4. **HARD-FAIL:** an infrastructure error (Blender nonzero exit after retry, timeout ×2,
   corrupt output file) ⇒ state `HARD_FAILED`, error in manifest, continue batch.
5. **WALL-CLOCK:** per-asset wall-clock budget (default 45 min) exceeded ⇒ `BEST_EFFORT` if a
   renderable iteration exists, else `HARD_FAILED`.

### 16.6 Best-effort output contract

A `BEST_EFFORT` asset still ships its **best iteration** — selected by
(fewest blocker fails, then fewest warns, then latest) — with:

- `manifest.json` entry: `"status": "best_effort"`, `"remaining_defects": […]` (the surviving
  check entries verbatim), `"shipped_iteration": n`.
- `diagnosis.md`, machine-written from history (template): what was requested; per-iteration
  table of (defects found → fix applied → result); which defects persisted; the planner's
  final hypothesis line, generated by one last text-only model call summarizing the history
  JSONL ("The seam on the chamfer persists across margin increases and re-unwraps; likely the
  EdgeWear mask itself is discontinuous across UV islands — a material-recipe bug, not an
  asset-level fixable"). This is the artifact a human (or a later, smarter pipeline version)
  debugs from, after the fact.

---

## 17. Run state, logging, and post-hoc debuggability

### 17.1 Directory layout (one run)

```
runs/<run_id>/                          # run_id = UTC timestamp + short hash of batch file
  run_manifest.json                     # updated after every asset state transition
  pipeline_config_snapshot.yaml         # frozen copy of config used
  <asset_id>/
    request.json                        # the intake-validated request
    history.jsonl                       # append-only event log (§17.2)
    diagnosis.md                        # only if best_effort
    iter_01/
      params.json
      asset.blend                       # kept per retention policy (config: keep_blend: last|all|none)
      maps/{albedo,normal,orm,emissive}.png
      asset.glb
      static_report.json
      renders/*.png  contact_sheet_*.png
      vision_report.json
      fix_plan.json                     # the plan that produced iter_02 (absent on pass)
      logs/{generate,bake,export,render}.{out,err}.txt
    iter_02/ …
    final/                              # symlink/copy of the shipped iteration's deliverables
      asset.glb  preview_*.png  manifest.json
```

### 17.2 `history.jsonl` — the debugging spine

Every stage appends one event; nothing is ever rewritten:

```json
{"t":"2026-07-06T04:58:11Z","asset":"scifi_crate_small_01","iter":2,"event":"stage_end",
 "stage":"V2","verdict":"fail","blockers":["R4:VISIBLE_SEAM"],"warns":[],
 "duration_s":38.2,"api_usage":{"input_tokens":9412,"output_tokens":631},
 "artifacts":["iter_02/vision_report.json"]}
```

Event types: `intake`, `stage_start`, `stage_end`, `fix_planned`, `fix_applied`,
`state_change`, `escalation`, `error`, `terminal`. The invariant that makes headless failures
debuggable: **for every verdict, the exact inputs (params.json, .blend per retention, .glb),
the exact evidence (reports, renders), and the exact decision (fix_plan) are on disk, keyed
by iteration** — a human or model reading `history.jsonl` can replay any decision without
having watched the run. Vision API calls also log full request/response bodies (minus image
bytes; image references by path + SHA-256) to `iter_NN/logs/vision_call.json`.

### 17.3 `run_manifest.json` / per-asset `manifest.json`

Run manifest: toolchain versions + hashes, batch totals
(`validated/best_effort/hard_failed/intake_rejected`), per-asset one-line status, wall-clock
and API-token totals. Per-asset manifest (shipped in `final/`):

```json
{ "asset_id":"…", "status":"validated", "category":"prop_small",
  "theme":"scifi_industrial", "platform_profile":"web", "seed":421337,
  "iterations_used":2, "container":"glb", "files":{"asset":"asset.glb","previews":["preview_turn_045.png"]},
  "stats":{"triangles":742,"lods":[742,296],"textures":{"albedo":"1024"},"glb_bytes":812345},
  "checks_passed":["S1","…","R12"], "remaining_defects":[], "toolchain_hash":"…" }
```

---

## 18. Engine adapter interface

An adapter is a Python class registered under a name; the core never imports engine
specifics. Contract (`assetpipe/adapters/base.py`):

```python
class EngineAdapter(Protocol):
    name: str
    def deliver(self, asset_dir: Path, manifest: dict, target_root: Path) -> DeliveryRecord:
        """Copy/transform final/ artifacts into the engine project. Pure file ops +
        engine-CLI calls. Must be idempotent (re-delivery overwrites cleanly)."""
    def verify(self, record: DeliveryRecord) -> AdapterReport:
        """Headless-engine check that the asset actually imports and instantiates.
        Returns pass/fail + errors; failures mark the asset delivery_failed in the
        run manifest (asset itself keeps its validated status — the canonical glb
        is fine; the adapter is what's broken)."""
```

Adapter rules: adapters may *add* engine files and *compress copies*, but never mutate the
canonical `final/` artifacts; adapter verification failures never re-enter the fix loop
(the fix loop guards the canonical asset; adapter bugs are pipeline bugs). Swapping engines
= implementing this class; `pipeline.yaml → delivery.adapters: [godot]` selects.

---

## 19. Godot adapter (concrete)

Target: Godot **4.3+**, delivering into a Godot project at `delivery.godot.project_path`.

### 19.1 File placement

```
res://assets/generated/<theme>/<category>/<asset_id>/
    <asset_id>.glb
    <asset_id>.manifest.json          # copy of per-asset manifest (renamed: Godot ignores unknown json)
res://assets/generated/skies/<asset_id>/<asset_id>.exr
res://assets/generated/backgrounds/<asset_id>/…png + background.json
```

### 19.2 Import configuration

Godot imports `.glb` as `PackedScene` automatically. The adapter writes a **sidecar
`.import` override is not hand-authored** (fragile across Godot versions); instead the
adapter sets import options via a bundled **`EditorScenePostImport` script + project-level
import defaults**, and controls per-asset behavior through glTF **name suffix conventions**
Godot honors natively:

- Meshes needing a static collider are exported with `-col` suffix on the mesh name
  (generates `StaticBody3D` + `ConcavePolygonShape3D`) or `-convcol` for convex — driven by
  request tag `collision: static|convex|none` (default: `convex` for props,
  `static` for kit/environment, `none` for characters).
- LOD sibling meshes (`_LOD1`…) are stripped by the post-import script (§19.3) because Godot
  4 generates its own mesh LODs on import; a config flag `use_pipeline_lods: true` inverts
  this (keeps explicit LODs, disables Godot's).

### 19.3 Post-import script (bundled asset)

`res://assets/generated/_pipeline/post_import.gd` (`extends EditorScenePostImport`), assigned
via the adapter writing `importer_scene` defaults into `project.godot`'s
`[importer_defaults]` section (documented exact key: `nodes/import_script/path` under
`importer_defaults/scene`). It: strips `_LOD*` siblings (per flag), sets
`GeometryInstance3D.gi_mode = STATIC` for kit/env categories (read from the sibling
manifest), applies physics layer from `tags` (`layer:<n>`), and renames the root to
`asset_id` PascalCased.

### 19.4 Import trigger + verification (`verify()`)

1. `godot --headless --path <project> --import` (imports all pending; exit code checked;
   stderr scanned for `ERROR:` lines mentioning the delivered paths).
2. Run a bundled verification script:
   `godot --headless --path <project> --script res://assets/generated/_pipeline/verify_import.gd -- <asset_path>`
   which: `load()`s the `PackedScene`, `instantiate()`s it, asserts ≥1 `MeshInstance3D`,
   asserts every surface has a material of type `BaseMaterial3D`/`StandardMaterial3D` with a
   non-null albedo texture when the manifest says one exists, asserts texture dimensions ≤
   profile budget (via `texture.get_size()`), asserts collision node presence matches the
   request's collision tag, prints a JSON report line, exits 0/1.
3. Skybox delivery verification: script builds a `PanoramaSkyMaterial` with the EXR, assigns
   to a `Sky` in a throwaway `WorldEnvironment`, asserts no load errors.

`AdapterReport` = the parsed JSON from step 2 + stderr scan results, stored at
`runs/<run_id>/<asset_id>/final/godot_report.json`.

### 19.5 Godot-specific mappings

| Canonical | Godot |
|---|---|
| glTF PBR material | `StandardMaterial3D` (automatic via importer; ORM texture honored via glTF `occlusionTexture`/`metallicRoughnessTexture`) |
| `skybox.exr` | `PanoramaSkyMaterial` resource `.tres` generated by adapter + demo `WorldEnvironment` scene |
| `background_2d` layers | generated `.tscn` with `Parallax2D` nodes (`scroll_scale = parallax_factor`, `repeat_size.x` set when `loop_x`) |
| glTF emissive strength | supported natively 4.3+ via `KHR_materials_emissive_strength` |

---

## 20. Orchestrator: CLI, config, process model

### 20.1 Package layout

```
assetpipe/
  cli.py                    # entrypoints below
  orchestrator.py           # state machine driver, per-asset loop
  intake.py                 # request/schema validation
  stages/ {generate.py, material.py, export.py, render.py}      # thin wrappers: build args, spawn blender
  blender_scripts/ {generate.py, bake.py, export_gltf.py, render_views.py, static_checks_mesh.py}
  validation/ {static_gate.py, image_checks.py, gltf_checks.py, tiling.py}
  vision/ {inspector.py, prompts.py, schemas.py}
  fixes/ {planner.py, table.py, llm_planner.py, apply.py}
  generators/…  matlib/…  profiles/…  schemas/…
  adapters/ {base.py, godot/…}
  themes -> ../themes
```

### 20.2 CLI

```
assetpipe generate  --request path.json [--out runs/] [--profile web] [--max-iterations 5]
assetpipe batch     --requests batch.json [--parallel 4]
assetpipe validate  --glb some.glb --request path.json      # V1 only, standalone
assetpipe render    --glb some.glb --out renders/           # R only, standalone
assetpipe inspect   --renders renders/ --request path.json  # V2 only, standalone
assetpipe deliver   --run runs/<id> --adapter godot --project /path/to/godot_proj
assetpipe resume    --run runs/<id>                         # resume crashed run from state files
assetpipe report    --run runs/<id>                         # human/model-readable run summary
```

Standalone stage commands exist precisely so Claude (or CI) can drive and debug any stage in
isolation.

### 20.3 `pipeline.yaml` (defaults; every threshold in §13–§16 lives here)

```yaml
toolchain: { blender: "4.2", godot: "4.3", require_exact: true }
vision:    { model: "claude-fable-5", max_retries: 1, temperature: 0 }
iteration: { max_iterations: 5, wall_clock_minutes_per_asset: 45, full_regen_allowed: 1 }
validation:
  uv_overlap_max_fraction: 0.005
  texel_density_p95_p5_max: 4.0
  tiling_edge_diff_max: 0.0078          # 2/255
  # …every S-check threshold, keyed by check id
render:    { resolution: 1024, samples: 128, deterministic_cpu: true }
retention: { keep_blend: "last", keep_renders: "all" }
delivery:  { adapters: [godot], godot: { project_path: null, use_pipeline_lods: false } }
parallelism: { assets: 4 }              # per-asset loops are independent; Blender procs pool-limited
```

### 20.4 Parallelism

Assets are independent → process-pool over assets (default 4). Vision calls are per-asset
serialized within the loop; a global semaphore caps concurrent API calls (config, default 4).
No shared mutable state between assets except the run manifest (single-writer via the
orchestrator process).

---

## 21. Testing the pipeline itself

The pipeline is code; it gets its own test suite (pytest + a Blender-invoking integration
tier). CI gates (GitHub Actions, container with pinned Blender/Godot).

### 21.1 Validator truth tests (fault injection) — the most important tier

A fixtures corpus `tests/fixtures/` of deliberately broken assets, each targeting exactly one
check, generated by scripts (committed) so they're reproducible:

| Fixture | Must be caught by |
|---|---|
| cube with 3 flipped faces | S5, A2, R3 |
| mesh with interior wire edge / loose vert | S1, S4 |
| UV islands overlapped 5% | S12b |
| all-black albedo | S16, R2 |
| normal map with red/green swapped | S17 |
| texture 1200×1200 (non-PoT) | S14 |
| 60 k-tri crate on `web` profile | S7 |
| tiling texture with 6-px edge mismatch | S19a, R10 |
| glb with Draco extension | S20b |
| skybox with hard vertical seam | S19a(sky), R11 |

CI asserts **every fixture fails its designated check** (true-positive coverage) and **the
golden corpus passes everything** (false-positive rate 0 on goldens). Any threshold change
must keep both properties.

### 21.2 Golden generation tests

For each shipped generator × 2 themes × fixed seed: run G→M→X, assert byte-stable
`params.json`, stable triangle/vertex counts (exact), stable glb inventory
(`gltf-transform inspect` snapshot), and image-similarity of `albedo.png` to a committed
golden within RMSE ≤ 2/255 (bake noise tolerance). This is the determinism contract as a test.

### 21.3 Vision-harness regression tests

A labeled set: ~10 good renders + the rendered fault fixtures, each with expected per-check
verdicts. A CI job (marked `api`, run on demand/nightly, not per-commit) calls the real
inspector and asserts: 0 blocker false-positives on goods; ≥90% catch rate on seeded visual
defects; schema validity 100%. This measures rubric prompt drift when the model id is bumped.

### 21.4 Integration & adapter tests

- End-to-end: one `prop_small` request on `web` profile through the full loop in CI
  (deterministic; vision stage mocked with recorded responses for the per-commit run, real
  API on the nightly).
- Godot adapter: deliver golden assets into a scratch Godot project in CI, run
  `verify_import.gd`, assert green; fault case: deliver a glb with a missing texture,
  assert `verify()` fails and manifest marks `delivery_failed`.

### 21.5 Fix-loop tests

Unit tests drive the state machine with scripted verdicts: assert escalation ladder order,
NO-PROGRESS early exit, single-regen limit, cap behavior producing `BEST_EFFORT` + a
`diagnosis.md` containing every persisted defect id.

---

## 22. Failure modes and mitigations

| Failure mode | Mitigation designed in |
|---|---|
| Vision model hallucinates defects (false positives) → infinite churn | Evidence rules §15.3 (view citations, 2-view rule), temperature 0, NO-PROGRESS stop §16.5(3), warn-severity never loops §15.6, regression harness §21.3 tracks FP rate |
| Vision model misses defects (false negatives) | Cheap scripted analytics catch the objective ones first (A1–A4, S16–S19); vision is the *second* net, not the only one |
| Fix oscillation (fix A causes defect B, fix B causes A) | Defect-set history comparison (NO-PROGRESS uses multiset over last 2 iterations); escalation ladder forces a *different class* of action rather than repeating |
| Blender version drift changes bakes/hashes | Hard version pin + toolchain_hash in every report §3, §17.3 |
| Draco/KTX2 sneaking in and breaking Godot | Extension whitelist check S20b |
| Budget passes but asset is degenerate (12-tri "crate") | Triangle minimums §8.1 + silhouette check R5 |
| A stuck asset stalls the batch | Per-asset wall-clock §16.5(5), process isolation §4.3, batch continues on all terminal states |
| Un-debuggable autonomous failures | §17 in full: append-only history, per-iteration frozen artifacts, machine-written diagnosis.md |
| API outage mid-run | Vision stage errors are infrastructure errors → retry with backoff (3 attempts over ≤5 min), then asset `HARD_FAILED` (not `BEST_EFFORT` — no evidence exists either way); `assetpipe resume` re-enters at INSPECTING |
| Same-seed reruns diverge (nondeterminism bug) | Golden tests §21.2 fail CI; deterministic CPU render path default §14.4 |

---

## 23. Suggested implementation order

1. **Skeleton + contracts:** schemas (request, reports, fix plan), run-dir/state machine,
   `pipeline.yaml`, one trivial generator (`props/crate`), no materials (flat grey).
2. **V1 static gate + fixtures (§21.1)** — validators before generators, so every later piece
   is built against a working truth harness.
3. **Export (X) + Godot adapter deliver/verify** — proves the canonical boundary end-to-end
   with the flat-grey crate.
4. **Render harness (R) + scripted analytics (A1–A4).**
5. **Vision inspection (V2)** with prompt template, on the fixture corpus; tune until §21.3
   targets hold.
6. **Material system (§10)** — the original-design, highest-risk piece — starting with
   `matlib` blocks + 2 recipes + golden bake tests.
7. **Fix loop (F)** table fixes → escalation → LLM param patcher; fix-loop unit tests.
8. Themes ×4, generator set §9.2, tiling/skybox/background branches, batch parallelism, `resume`.

---

## Appendix A — Vision inspection prompt template

(`assetpipe/vision/prompts.py`; `{…}` are format slots. Sent with contact sheets as image
blocks; `report_inspection` tool forced.)

```
You are a strict technical art QA inspector for a game asset pipeline. You are inspecting
deterministic headless renders of ONE asset. Your verdicts gate an automated pipeline; there
is no human reviewer after you. Report through the report_inspection tool ONLY.

ASSET UNDER INSPECTION
- asset_id: {asset_id}   category: {category}
- requested description: "{description}"
- theme: {theme_display_name}
- theme silhouette language: {silhouette_language}
- theme style brief: {vision_style_brief}
- theme palette (allowed dominant hues): {palette_hex_list}; forbidden: {forbidden_hex_list}
- expected real-world size range: {bbox_range} (a labeled 1 m grey reference cube stands
  1.5 m to the asset's left in turn_000/turn_090)

RENDER SET
Each contact-sheet cell is labeled with its view_id in the corner. Lighting rigs: L1 neutral
studio; lit_warm_* warm sun; lit_dark_090 dim blue rim (dark regions there are EXPECTED —
judge texture presence by the rim-lit edge). silhouette_* are white-on-black by design.
normals_* use a debug material: surface normal as RGB, backfacing surfaces PURE RED.
uvcheck_045 deliberately shows a checker pattern — it is exempt from R2.

CHECKS — evaluate every one of: {applicable_check_ids}
{per_check_instructions_block}   # the "Pass criteria" column of §15.2, one paragraph each

RULES
1. verdict "fail" REQUIRES: at least one cited view_id in evidence_views, a specific location
   phrase, and a defect_type from this exact list: {taxonomy_list}.
2. For R3 and R9, a defect visible in only ONE view must be reported as "uncertain", not "fail".
3. Judge only what is visible. Do not infer defects from expectations. Do not fail an asset
   for stylistic choices permitted by the style brief.
4. confidence is your honest calibration in [0,1].
5. Every applicable check appears exactly once in checks[] or checks_not_applicable[].
```

## Appendix B — Defect taxonomy

Closed vocabulary; `vision_report.defect_type` and the fix table (§16.2) both key on it.

`NON_MANIFOLD, DEGENERATE_FACES, LOOSE_GEOMETRY, INVERTED_NORMALS, SELF_INTERSECTION,
OVER_BUDGET, UNDER_BUDGET, BBOX_OUT_OF_RANGE, SOCKET_OFF_GRID, SKIN_WEIGHT_INVALID,
UV_MISSING, UV_OVERLAP, UV_OUT_OF_BOUNDS, UV_STRETCH, BAKE_MARGIN_LOW,
TEX_RESOLUTION_INVALID, TEX_FORMAT_INVALID, BLACK_SURFACE, MISSING_TEXTURE,
NORMAL_MAP_INVALID, ORM_INVALID, TILING_SEAM, VISIBLE_SEAM, BANDING, CLIPPED_EMISSIVE,
SCALE_IMPLAUSIBLE, SILHOUETTE_MISMATCH, MATERIAL_IMPLAUSIBLE, PALETTE_VIOLATION,
SHADING_ARTIFACT, ZFIGHT_COPLANAR, FLOATING_PART, INTERPENETRATION, MISSING_PART,
POLE_PINCH, HORIZON_SEAM, GLTF_INVALID, GLTF_EXTENSION_FORBIDDEN, FILE_TOO_LARGE,
RENDER_EMPTY, INFRA_ERROR`

Each taxonomy entry gets a one-line definition in `assetpipe/schemas/defects.json` (id,
definition, default severity, table-fix id or `null`). Adding a defect type = adding it
there + (optionally) a table fix; the vision prompt taxonomy list is generated from that
file so prompt and code cannot drift apart.
