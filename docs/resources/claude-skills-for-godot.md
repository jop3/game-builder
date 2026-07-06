# Claude Code skills/plugins for a Godot pipeline

Curated from a longer list that was passed along. Each entry below was checked
against the actual GitHub repo before being included — several items in the
original list either don't exist or were mis-described. See "Excluded" at the
bottom for what was dropped and why.

None of these are Godot-specific out of the box (there is no dedicated,
maintained "Godot skill" among them). They're included because they provide
patterns/tooling that transfer to a Godot pipeline even though their examples
target other engines/stacks.

## 1. General engineering skills

### Jeffallan/claude-skills
- **What it actually is:** 66 general full-stack developer skills for Claude
  Code (languages, backend/frontend frameworks, infra, testing, DevOps,
  security, data/ML). Not game- or Godot-specific, and does not contain
  ECS/physics/multiplayer-networking guides as originally described.
- **Why include it anyway:** solid general engineering hygiene (testing,
  performance profiling habits, security review) that a game codebase still
  benefits from.
- **Install:**
  ```
  /plugin marketplace add jeffallan/claude-skills
  /plugin install fullstack-dev-skills@jeffallan
  ```
- **Link:** https://github.com/Jeffallan/claude-skills

## 2. Game-project orchestration (pattern reference, not Godot-native)

### PlayableIntelligence/game-creator
- **What it actually is:** an opinionated Claude Code plugin for building
  **2D (Phaser) and 3D (Three.js) web games** — i.e. JS/web-stack only. It is
  *not* engine-agnostic; it will not drive Godot/GDScript/C# builds.
- **Why include it anyway:** the workflow shape (idea → milestones → ADRs →
  `/make-game` multi-session loop, plus a QA subagent that runs build/runtime/
  gameplay/architecture/visual checks after every step) is a good structural
  reference to reimplement for Godot, even though the implementation is
  web-only.
- **Install (for reference/inspection, not for use in this Godot repo):**
  ```
  npx skills add playableintelligence/game-creator -a claude-code
  ```
- **Link:** https://github.com/PlayableIntelligence/game-creator

## 3. 3D/graphics tooling

### freshtechbro/claudedesignskills — `blender-web-pipeline` skill only
- **What it actually is:** a collection of skills for **web** 3D/animation
  (Three.js, GSAP, React Three Fiber, Framer Motion, Babylon.js). Buried in
  it is one skill, `blender-web-pipeline`
  (`.claude/skills/blender-web-pipeline/SKILL.md`), covering Blender →
  glTF/FBX export automation, which is the one piece actually relevant to
  feeding assets into Godot's filesystem. There is no separate
  "substance-3d-texturing" pack in this repo as originally described.
- **Install:**
  ```
  /plugin marketplace add freshtechbro/claudedesignskills
  ```
  then install just the `blender-web-pipeline` skill/plugin, not the full
  web-3D bundle.
- **Link:** https://github.com/freshtechbro/claudedesignskills/blob/main/.claude/skills/blender-web-pipeline/SKILL.md

### majiayu000/claude-skill-registry
- **What it actually is:** not a skill itself — it's a searchable **index/
  registry** aggregating thousands of community Claude Code skills (with a
  daily-updated site and a `sk` CLI). Useful as a discovery tool if you need
  to search for a 3D-modeling, shader, or DCC-pipeline skill later, but it
  has no dedicated "3D modeling" or "shader" pathway as a single skill.
- **Use as:** a search index, e.g. https://majiayu000.github.io/claude-skill-registry-core/
- **Link:** https://github.com/majiayu000/claude-skill-registry

## Excluded from the original list

| Item | Reason excluded |
|---|---|
| `Andrew1326/dominations` (Blender procedural Python) | Repo does not exist — no such GitHub repository was found under this name. |
| `phuetz/code-buddy` (Blender CLI automation) | Real repo, but it's a general-purpose terminal AI coding agent (a Claude Code alternative) with no connection to Blender or asset pipelines. Description did not match the project. |
| `majiayu000/claude-skill-registry` — "3D modeling" / "shader" pathways | Kept the registry itself (above) as a search tool, but there is no single dedicated 3D-modeling or shader skill in it to point to directly; "look for the pathway" wasn't backed by anything concrete. |
| Game QA & Automated Reviews (as a standalone item) | This is the QA-subagent behavior bundled inside `PlayableIntelligence/game-creator`, not a separate resource — folded into that entry above. |
| Game Assets & Juice Designer (as a standalone item) | Same as above — bundled inside `PlayableIntelligence/game-creator`'s `/add-assets` / `/design-game` commands, not a separate repo. |

## Bottom line for a Godot pipeline

There isn't yet a maintained, Godot-native equivalent of these skills in the
list that was supplied. The practical path is:
1. Use `Jeffallan/claude-skills` for general engineering practices.
2. Borrow the milestone/ADR/QA-loop *structure* from `PlayableIntelligence/game-creator`
   and reimplement it against Godot's CLI/export pipeline (`godot --headless --export-release ...`)
   instead of its web build tooling.
3. Use the `blender-web-pipeline` skill from `freshtechbro/claudedesignskills`
   for Blender → glTF export automation feeding Godot's asset import.
4. Use `majiayu000/claude-skill-registry` to search for any newer Godot-specific
   skill that may appear later.
