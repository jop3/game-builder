# Roadmap: closing the gap between env/house and the stylized reference

Reference: hand-painted fantasy cottage (dark plank walls, chunky red
shingle roof, framed glowing windows, dormer, lantern, barrels, stone
plinth). Current state: validated blockout (single box + thin roof prism,
unframed glowing slabs, gold roof). This file is the working plan; strike
items as they land. Verification loop per phase: run the pipeline with the
agent vision client, compare renders against the reference with real
vision, tune, commit.

## Phase 1 — Color & material identity (cheap, biggest visual delta)

- [x] `fantasy_roof_shingles`: `color1_hex`/`color2_hex` params so a request
      can pin the course colors; default stays palette-sampled. Request sets
      oxblood `#8A1E1E` (description says "red shingled roof" — material
      selection cannot read description words yet; explicit override is the
      honest v1).
- [x] Darken `fantasy_aged_wood` toward the reference's chocolate planks:
      bias Color A/B to `primary` (both draws), raise grain contrast,
      strengthen grunge darkening; add `plank` mode — vertical board seams
      (brick texture, 1 column x N rows, dark mortar) + per-board value
      jitter.
- [x] `fantasy_window_glow`: brighter center vignette (radial falloff),
      slightly deeper gold.

## Phase 2 — Read-at-a-glance architecture (framing & thickness)

- [x] Roof thickness: build slopes as slabs (solidified prism) with fascia
      boards and a ridge cap beam; deepen the overhang.
- [x] Framed windows: frame border (4 bars), recessed pane, cross mullions
      (2 crossing bars, wall material), shingled hood above the main window.
- [x] Door: frame + plank door slab + stone doorstep (stone material slot).
- [x] Dormer v2: gabled face flush with the roof plane (integrated look),
      framed pane, its own overhanging mini roof.

## Phase 3 — Massing & surface relief

- [x] Two-mass build: main tall gable volume + attached lower wing with its
      own roof (param `wing: 0|1`, sizes relative to main).
- [x] Wall plank relief: corner posts + horizontal beam at storey line +
      subtle per-board depth (inset strips), so silhouettes stop reading as
      extruded rectangles. Watch the 3000-tri web budget.
- [x] Shingle rows as coarse geometry: 8–12 slightly overlapping course
      slabs per slope with eave-line jitter (the reference's wavy edge).

## Phase 4 — Scene dressing (reuse existing generators as parts)

- [x] Barrels: 2–3 scaled `props/barrel` bodies clustered at a wall.
- [x] Lantern: bracket + small emissive cage (reuse `props/lantern` shapes)
      hanging by the door gable.
- [x] Stone plinth: low cylinder disc under the footprint,
      `fantasy_stone_wall` slot; doorstep merges into it.
- [x] Budget check: this phase is the first credible threat to 3000 tris —
      measure before polishing.

## Phase 5 — Presentation (optional, out of validation scope)

- [x] `beauty_dark` render view: black background, low ambient, emissives
      dominant — the reference's presentation style. Extra view only; not
      judged by the rubric, excluded from A-checks.

## Known constraints

- Per-slot material params: bake ctx currently shares one
  `material_overrides` dict across all slots; phase 1's hex params must be
  namespaced or slot-scoped (extend `materials` entries to
  `{recipe, params}` objects in the generator schema, keeping plain-string
  entries valid).
- Two-view rubric checks and A-checks all still apply — every phase ends
  with a full validated run, not just pretty renders.
- Web profile budget: 3000 tris, 3 MiB glb, emissive 512.
