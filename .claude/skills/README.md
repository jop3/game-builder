# Vendored skills

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
