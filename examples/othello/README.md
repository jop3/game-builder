# examples/othello — Moonstone & Obsidian (fantasy Othello)

A worked example that exercises **both** sibling pipelines on a brand-new game:
the game is specced with **spelbygge** (the game-building skill in the Snittet
repo) and its fantasy graphics are produced by **this repo's** asset pipeline.

## What's here

```
spec/
  brief.md          Fas-0 brief: pitch, machine-checkable win condition, verbs,
                    NOT-list, feel rubric (fantasy Othello: Moonstone vs Obsidian).
  build_spec.md     Fas-1 build spec: milestones w/ Testable lines, invariants,
                    acceptance tests (incl. a verify-the-verifier fixture tier), DoD.
batch.json          The three asset requests (board + light disc + dark disc).
assets/
  othello_board_01.glb              8x8 fantasy board (validated, delivered).
  othello_disc_moonstone_01.glb     pale "Moonstone" light disc.
  othello_disc_obsidian_01.glb      dark "Obsidian" disc.
  *_preview.png                     the pipeline's own turn_045 preview renders.
assemble_render.py  Blender script: loads the 3 glb, places discs in an Othello
                    position on the board's cells, renders a Cycles beauty shot.
othello_board_render.png            the assembled fantasy Othello board.
```

The generators that build the meshes live in the pipeline proper:
`assetpipe/generators/props/game_board.py` and `.../game_disc.py`.

### The playable game (`game/`)

A working Godot Othello, built to the spec's M1:

```
game/
  rules.gd        pure rules (one shared 8-direction table, legal moves, flip, terminal, score)
  bot.gd          deterministic corner-aware bot (positional weights + mobility)
  test_rules.gd   14 headless checks incl. verify-the-verifier fixtures
  audio.gd        procedural PCM SFX (place / flip / win) — no audio assets
  othello.gd/.tscn  loads the .glb at runtime, plays a full bot-vs-bot game,
                    animates the flip cascade, flies a cinematic spiral camera,
                    fires drama FX on big cascades, records deterministically
  mux_audio.py    overlays the procedural soundtrack onto a recorded mp4 (headless has no audio)
  sea.gdshader    deterministic sea shader (Gerstner-ish waves, whitecaps, shoreline foam)
  sky.gdshader    deterministic sky shader (drifting fbm clouds + lightning flash uniform)
  rock.gdshader   craggy vertex-displaced wet-rock shader (fbm, derivative normals)
  assets/board.glb  the validated reversi_classic green-felt board (black frame)
  assets/disc.glb   the validated TWO-TONE disc (black one face, white the other)
  assets/column.glb the validated greek_arena fluted Ionic marble column (pipeline-generated)
  othello_game.mp4  a full recorded game on the Greek island arena, with sound (Black 40 – White 24)
```

### The arena (a board on a marble pedestal, in a stormy sea)

Staged to a reference photo: the board sits on a **single fluted Ionic marble
column** rising from a **rocky islet**, ringed by a choppy, foam-capped sea with
a distant coastline, under a bright dramatic sky — with lightning + thunder. The
**column is a pipeline asset** (`env/column` generator, Ionic capital, +
`greek_arena` theme's `marble_white` material, requested in `arena_batch.json`,
validated through V1 + V2). The **sea, sky and rock islet** are procedural Godot
(three `.gdshader` files + scene geometry) — every animated value reads an
`_elapsed` uniform, never `TIME`, so the render stays deterministic. Lightning
drives both a scene flash and the sky shader's `flash` uniform; thunder follows
~0.5 s later through the same audio-event log. The board is lifted onto the
pedestal (`LIFT`); note `_aabb()` measures in the board's *local* frame, so the
lift is added back into the play-surface height.

Run the tests: `godot --headless --path game --script res://test_rules.gd`
Play it:       `godot --path game res://othello.tscn`   (with live procedural sound)
Record a game: `godot --path game res://othello.tscn -- --record=DIR --fps=24`
Add the sound: `python3 game/mux_audio.py --dir DIR --video silent.mp4 --out othello_game.mp4`

## How the graphics were produced (reproducible)

```bash
bash scripts/setup_toolchain.sh && export PATH=/opt/toolchain/bin:$PATH
# generate + validate + render + vision-inspect all three (agent vision client
# = the driving session inspects the renders itself, no API key needed):
python -m assetpipe batch --requests examples/othello/batch.json --out runs/ \
    --parallel 1 --vision-client agent --vision-exchange /tmp/exchange
# deliver + headless-verify into a Godot project:
python -m assetpipe deliver --run runs/<id> --adapter godot --project <proj>
# assemble the beauty shot:
blender --background --python-exit-code 1 --python examples/othello/assemble_render.py -- \
    --board assets/othello_board_01.glb --light assets/othello_disc_moonstone_01.glb \
    --dark assets/othello_disc_obsidian_01.glb --border 0.0265 --out board.png
```

## Result

Every asset reached **`validated`** (V1 static gate + V2 vision inspection, all
blocker checks passing) in one iteration and was import-verified in Godot. The
look evolved across three themes as the design was refined:

1. **`fantasy_medieval`** — aged-oak board + mosaic-stone / forged-iron discs.
2. **`fantasy_tabletop`** — warm honey-oak board + obsidian-black / pearl-white
   discs (higher contrast).
3. **`reversi_classic`** (the current game set) — a realistic Reversi look: a
   **green-felt board with a black moulded frame** and thin grid lines, and a
   **two-tone disc** (glossy black on one face, white on the other, split at the
   rim) so a capture is a genuine 180° **turn-over**, not a colour swap. The
   `game/` scene stages it like the real set: light tabletop, side trays of
   striped discs lying in their channels, restrained lighting so black reads
   black, and procedural click/flip/chime sound synthesised in code. The
   recording is shot cinematically: the camera opens straight overhead and
   spirals down and around the table, settling into a clear side view exactly
   as the game ends, and large capture cascades set off a light flash, a subtle
   screen flash, a puff of smoke and a small camera shake.

`othello_game.mp4` is a full recorded game on that set (Black 40 – White 24).

### Notes

- The two-tone disc is one mesh bisected at its equator: top half → black
  material, bottom half → white (`generators/props/game_disc.py`); the game
  encodes which face is up as `rotation.x` and animates a real turn-over.
- A near-black surface only stays black under *moderate* light — over-bright
  even lighting lifts a 2.5%-albedo material to grey and kills the contrast, so
  the scene keeps a light backdrop but a controlled key.
- LODs are `"none"` (Godot 4 auto-generates mesh LODs on import).
- The video is driven by a manual fixed-timestep clock, so the camera move and
  every animation phase read from a dt-summed `_elapsed` — the recording is
  bit-stable and FPS-locked no matter how slowly software-Vulkan paints. The
  spiral lands on time because the game's exact duration is summed up front
  (`_compute_play_dur`) and the descent is keyed to it. The drama FX (flash,
  smoke, shake) are animated by the same manual `dt` — a real particle system
  would advance on wall-clock render time and desync/burn through in one slow
  frame. A straight-down camera also can't use `look_at` (up ∥ view is
  degenerate), so the basis is built by hand from the spiral azimuth.
- Sound is procedural PCM (`audio.gd`, no assets). Headless has no audio driver,
  so the game logs each sound event + saves the streams, and `mux_audio.py`
  builds the soundtrack and muxes it in — the event log is the single source of
  truth, so picture and sound can't drift.

The full playable game (rules + bot + flip animation, spec M1/M3/M4) **is built**
here; the remaining spec milestones (difficulty tiers, hotseat) are future work.
