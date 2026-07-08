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
  othello.gd/.tscn  loads the .glb at runtime, plays a full bot-vs-bot game,
                    animates the flip cascade, records deterministically
  assets/*.glb    the validated fantasy_tabletop board + obsidian + pearl discs
  othello_game.mp4  a full recorded game (Obsidian 40 – Moonstone 24)
```

Run the tests: `godot --headless --path game --script res://test_rules.gd`
Play it:       `godot --path game res://othello.tscn`
Record a game: `godot --path game res://othello.tscn -- --record=DIR --fps=24`

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

All three assets reached **`validated`** (V1 static gate + V2 vision inspection,
all blocker checks passing) in one iteration each, delivered and import-verified
in Godot. The board is an 8x8 aged-oak grid with a raised dark-iron lattice and
border rim; the discs are a pale mosaic-stone "Moonstone" and a dark forged
"Obsidian", giving the light/dark contrast Othello needs.

### Honest notes (the pipeline's own `worst_thing` findings)

- The board's aged-wood leans grungy/dark ("weathered dungeon board" rather than
  warm honey oak) — the fantasy_medieval theme has no lighter wood recipe.
- The Moonstone disc uses `fantasy_stone_wall` (the only pale material), so its
  surface is a busy cobble/mosaic rather than smooth polished stone.
- LODs are `"none"`: the overlapping-box board and the beveled disc are manifold
  at full resolution but not decimation-robust, and Godot 4 auto-generates mesh
  LODs on import anyway. A future pass could make the geometry decimation-safe
  or add a dedicated `fantasy_tabletop` theme (smooth pale stone + warm oak).

The playable game (spec milestones M1/M3/M4) is **specced, not built** this round
— this example delivers the spec and the graphics, assembled and rendered.
