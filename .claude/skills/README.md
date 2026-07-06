# Skills

This directory contains two kinds of skills: **vendored** (copied from verified
external repos) and **original** (authored in this repo for the asset pipeline).

## Original skills (authored here)

Written to cover the expert-knowledge gaps identified in
`docs/specs/asset-pipeline.md` §2 — most importantly texture/material
generation, which no verified external skill covers. Each skill states which
spec sections it supports.

| Skill | Domain | Supports spec |
|---|---|---|
| `blender-procedural-geometry` | bmesh generator recipes, mesh/UV validation, budgets/LODs, stylized rigging | §9, §13.1–13.2 |
| `pbr-material-baking` | Procedural PBR node graphs, Cycles channel baking, ORM packing, seamless tiling | §10, §12.2, §13.3–13.4 |
| `asset-visual-qa` | Deterministic headless renders, scripted image analytics, glTF checks, vision-model rubrics | §13.5, §14, §15 |
| `godot-asset-import` | Godot 4 headless import, suffix conventions, post-import scripts, verification | §18, §19 |

These are original design (not externally verified); their code blocks are
reference patterns written against Blender 4.2 LTS / Godot 4.3 and should be
validated by the pipeline's own test tiers (spec §21) as they get implemented.

## Vendored skills

These are committed copies of specific Claude Code skills verified against
their source repos (see `docs/resources/claude-skills-for-godot.md` for the
full verification writeup). Vendored rather than installed via plugin
marketplace so they work offline with no external install step.

Both source repos are MIT-licensed; each skill directory keeps the source
repo's `LICENSE` file alongside its content per the license's attribution
requirement.

| Skill | Source repo | Path in source | Version pulled |
|---|---|---|---|
| `game-developer` | https://github.com/Jeffallan/claude-skills | `skills/game-developer/` | main @ 2026-07-06 |
| `blender-web-pipeline` | https://github.com/freshtechbro/claudedesignskills | `.claude/skills/blender-web-pipeline/` | main @ 2026-07-06 |

Caveats carried over from verification:
- `game-developer` covers Unity/Unreal only — no Godot content. Useful for
  ECS/physics/networking/performance *concepts*, not GDScript/C# code
  samples.
- `blender-web-pipeline` targets web (Three.js/glTF) consumption, but its
  Blender → glTF export automation is directly reusable for feeding a Godot
  asset pipeline.

These are pulled from their upstream `main` branch as of the date above and
will not auto-update — re-fetch manually if the upstream skills change.
