# Claude Code skills/plugins for a Godot pipeline

Source: a list whose wording traces back to
https://snyk.io/articles/top-claude-skills-3d-modeling-game-dev-shader-programming/
(and equivalent write-ups). Every entry below was checked directly against
the actual GitHub repo (fetching READMEs/skill files, not just search
snippets) before being included — the source article got several details
wrong. See "Excluded" at the bottom for what was dropped and why.

**None of these are Godot-native.** The closest thing to a real "game
developer" skill (`Jeffallan/claude-skills`) explicitly covers Unity/Unreal
only and does not mention Godot. Everything here is included because it
transfers as a pattern/tool even though its examples target other
engines/stacks.

## 1. Game engineering skill (Unity/Unreal, not Godot)

### Jeffallan/claude-skills — `game-developer` skill
- **Verified scope:** confirmed by reading `skills/game-developer/SKILL.md`
  directly. It covers Unity and Unreal Engine feature implementation, ECS
  architecture, physics/collider systems, multiplayer networking with lag
  compensation, frame-rate optimization (60+ FPS targets), shader
  programming, and game AI/design patterns (object pooling, state machines).
  This part of the original description was accurate.
- **What was wrong in the source:** it was advertised as covering
  "Unity, Unreal, Godot" — Godot is not mentioned anywhere in this skill.
  The repo is also 66 skills total (`skills/` has ~80 folders spanning
  full-stack dev, not just games); `game-developer` is one of them.
- **Why include it anyway:** the ECS/physics/networking/performance
  *concepts* (object pooling, state machines, frame-budget optimization,
  lag compensation) apply directly to Godot even though the code samples
  in the skill will be C#/C++ (Unity/Unreal), not GDScript.
- **Install:**
  ```
  /plugin marketplace add jeffallan/claude-skills
  /plugin install fullstack-dev-skills@jeffallan
  ```
- **Link:** https://github.com/Jeffallan/claude-skills/blob/main/skills/game-developer/SKILL.md

## 2. Game-project orchestration (pattern reference, not Godot-native)

### PlayableIntelligence/game-creator
- **Verified scope:** confirmed via README — "Go from game idea to deployed,
  monetized browser game in minutes." This is an opinionated Claude Code
  plugin for **2D (Phaser 3) and 3D (Three.js) web games only**. It is
  *not* engine-agnostic and will not drive a Godot/GDScript/C# build. The
  only non-web-engine reference in the repo is an experimental `unity-mcp`
  skill (Unity Editor automation via MCP) — secondary and beta, no Godot
  equivalent exists.
- **Why include it anyway:** the workflow shape (idea → milestones → ADRs →
  `/make-game` multi-session loop, plus a QA subagent that runs build/
  runtime/gameplay/architecture/visual checks after every step) is a good
  structural reference to reimplement for Godot, even though the
  implementation is web-only.
- **Install (for reference/inspection, not for use in this Godot repo):**
  ```
  npx skills add playableintelligence/game-creator -a claude-code
  ```
- **Link:** https://github.com/PlayableIntelligence/game-creator

## 3. 3D/graphics tooling

### freshtechbro/claudedesignskills — `blender-web-pipeline` skill only
- **Verified scope:** the repo is 22 skills for **web** 3D/animation
  (Three.js, GSAP, React Three Fiber, Framer Motion, Babylon.js, A-Frame,
  PlayCanvas, etc). No mention of Godot anywhere. Buried in it is one
  skill, `blender-web-pipeline`
  (`.claude/skills/blender-web-pipeline/SKILL.md`), covering Blender →
  glTF/FBX export automation — the one piece actually relevant to feeding
  assets into Godot's filesystem. There is no separate
  "substance-3d-texturing" pack in this repo as originally described.
- **Install:**
  ```
  /plugin marketplace add freshtechbro/claudedesignskills
  ```
  then install just the `blender-web-pipeline` skill/plugin, not the full
  web-3D bundle.
- **Link:** https://github.com/freshtechbro/claudedesignskills/blob/main/.claude/skills/blender-web-pipeline/SKILL.md

### majiayu000/claude-skill-registry
- **Verified scope:** not a skill itself — it's a searchable **index/
  registry** aggregating thousands of community Claude Code skills (44
  top-level categories including `gaming`, `creative`, `development`,
  updated daily via a companion `sk` CLI). Could not confirm a skill
  literally named "3D Modeling Specialist" or "Shader Techniques" exists in
  it — those may be renamed/reorganized under the `gaming`/`creative`
  categories, or may not exist as named. Treat as a search tool, not a
  specific skill.
- **Use as:** a search index, e.g. https://majiayu000.github.io/claude-skill-registry-core/
- **Link:** https://github.com/majiayu000/claude-skill-registry

## Excluded from the original list

| Item | Reason excluded |
|---|---|
| `Andrew1326/dominations` (claimed: "Blender 3D Modeling / Procedural Python") | Repo **does exist** (11 stars, confirmed by direct fetch), but it is not a reusable Claude skill — it's someone's actual game project: a browser-based MMORTS (TypeScript/Phaser 3/React/Node/MongoDB) inspired by DomiNations. It has a Blender folder, but only for 2D sprite/art asset creation, not procedural bpy/bmesh modeling. The source article's description of it was wrong. |
| `phuetz/code-buddy` (claimed: "Blender CLI automation") | Real repo, but it's a general-purpose terminal AI coding agent (a Claude Code alternative) with no connection to Blender or asset pipelines. |
| `majiayu000/claude-skill-registry` — named "3D modeling"/"shader" skills | Kept the registry itself (above) as a search tool, but couldn't verify the specific named skills exist in it. |
| Game QA & Automated Reviews (as a standalone item) | This is the QA-subagent behavior bundled inside `PlayableIntelligence/game-creator`, not a separate resource — folded into that entry above. |
| Game Assets & Juice Designer (as a standalone item) | Same — bundled inside `PlayableIntelligence/game-creator`'s `/add-assets` / `/design-game` commands, not a separate repo. |

## Bottom line for a Godot pipeline

There is no maintained, Godot-native equivalent of these skills yet. The
practical path:
1. Use `Jeffallan/claude-skills`' `game-developer` skill for
   ECS/physics/networking/performance *concepts*, translating Unity/Unreal
   code patterns to GDScript/C# in Godot yourself.
2. Borrow the milestone/ADR/QA-loop *structure* from
   `PlayableIntelligence/game-creator` and reimplement it against Godot's
   CLI/export pipeline (`godot --headless --export-release ...`) instead of
   its web build tooling.
3. Use the `blender-web-pipeline` skill from `freshtechbro/claudedesignskills`
   for Blender → glTF export automation feeding Godot's asset import.
4. Use `majiayu000/claude-skill-registry` to search for any newer
   Godot-specific skill that may appear later.
