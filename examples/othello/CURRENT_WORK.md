# CURRENT_WORK — Othello on a Greek sea-stack (resume notes)

State handoff so a fresh session can pick this up with full context. Read this
first, then `examples/othello/README.md` for the deeper "why".

## Branches

Both repos are on **`claude/cross-project-pipeline-review-59m1k8`** (clean, pushed):
- **game-builder** — all of the arena/game/shader/pipeline work below.
- **Snittet** — sibling repo (cozy digging game + `spelbygge` skill); only the
  cross-pipeline review docs were touched. Nothing pending there.

## What this build is

A playable bot-vs-bot **Othello/Reversi** whose board sits on a single **fluted
Ionic marble column** rising from a **craggy rock islet** in a **Gerstner-wave
sea**, under a bright daylight sky with a distant coastline. A cinematic camera
opens straight overhead and **spirals down**, landing in a low side view exactly
as the game ends; big capture cascades fire flash/smoke/shake, lightning +
thunder punctuate, and all SFX are procedural. Staged to match the user's
reference photo: **`spec/reference_sea_arena.png`**.

Everything the camera sees is either **pipeline-generated** (board, disc, column
via `assetpipe`) or a **deterministic procedural shader** (sea, sky, rock).

## Where things live

```
examples/othello/
  spec/reference_sea_arena.png   the target reference photo (board on a column pedestal at sea)
  arena_batch.json               pipeline request for the Ionic marble column
  verify_scene.sh                headless geometry audit — RUN BEFORE EVERY RECORD
  game/
    othello.gd/.tscn   scene: loads glbs, plays a full game, spiral camera, drama FX,
                       lightning, deterministic record; `--audit` mode; builds sea/rock/coast
    rules.gd bot.gd test_rules.gd    pure rules + bot + 14 headless checks
    audio.gd           procedural PCM SFX (place/flip/win/thunder), no assets
    mux_audio.py       overlays the procedural soundtrack onto the recorded mp4
    sea.gdshader       Gerstner waves + Fresnel + shallow-water tint + fold-foam + ripple normals
    sky.gdshader       daylight fbm clouds + lightning `flash` uniform
    rock.gdshader      3-layer displacement + micro-normals + strata/AO/lichen colouring
    assets/board.glb disc.glb column.glb    the delivered, validated pipeline assets
    othello_game.mp4   the recorded film (see "IN PROGRESS" below)
assetpipe/generators/env/column.py        Ionic/Doric fluted column generator (env piece)
themes/greek_arena/                        theme + materials/marble_white.py (aged warm marble)
```

## IN PROGRESS at handoff

A full 1280×720 re-record with the **latest rocks (strata/warm palette) + warmer
aged marble** was rendering to `/home/user/e2e/othello/frames10/` (outside the
repo). If it did not get muxed+committed before the session ended, the committed
`game/othello_game.mp4` is the previous (water+rock) version — the *source* for
the new look is committed, only the film needs a refresh:

```bash
export PATH=/opt/toolchain/bin:$PATH
bash examples/othello/verify_scene.sh        # must print AUDIT_PASS (+ SELFTEST_PASS)
cd examples/othello/game
xvfb-run -a -s "-screen 0 1280x720x24" godot --path . res://othello.tscn \
    --rendering-driver vulkan --resolution 1280x720 -- "--record=/tmp/rec" "--fps=24"
ffmpeg -y -framerate 24 -i /tmp/rec/frame_%04d.png -c:v libx264 -pix_fmt yuv420p silent.mp4
python3 mux_audio.py --dir /tmp/rec --video silent.mp4 --out othello_game.mp4
# then: git add othello_game.mp4 && commit && push
# spot-check a HIGH-RES still (frame_1134 = landing) — do NOT trust low-res.
```

## How to regenerate the pipeline assets

```bash
export PATH=/opt/toolchain/bin:$PATH
python -m assetpipe batch --requests examples/othello/arena_batch.json --out runs/ \
    --parallel 1 --vision-client agent --vision-exchange /tmp/exchange
# agent vision client: watch /tmp/exchange/call_*/, look at images/, write report.json
# (report_inspection tool input; checks_not_applicable MUST be []). Then deliver:
cp runs/<id>/greek_column_01/iter_01/greek_column_01.glb game/assets/column.glb
```
`arena_batch.json` currently requests `capital_style: "ionic"` + `marble_white`.

## Hard-won gotchas (do not re-learn these)

- **Determinism**: every animated value reads the `t = _elapsed` uniform / dt-summed
  clock, **never `TIME`/`OS` delta**. Software-Vulkan renders slowly; wall-clock delta
  would desync. Same reason drama FX are hand-animated billboards, not particle systems.
- **`TAU`/`PI` are built-in Godot shader constants** — redefining `TAU` => "Redefinition"
  compile error. Use your own `TWO_PI`.
- **`_aabb()` measures in the board's LOCAL frame** (it stops at the root, excluding the
  root's transform). The board is lifted by `LIFT`, so `_surf_z` and `_board_bottom_y`
  add `LIFT` back. Discs place on `_surf_z`; get this wrong and they pile at the pedestal foot.
- **Ordering**: `_build_pedestal()` runs AFTER `_load_assets()` and scales the column to
  `_board_bottom_y`. It once ran before, guessed the height, and the capital punched up
  through the board. That class of bug is now caught by `verify_scene.sh`.
- **Don't eyeball low-res**: a poke-through / z-fight is a few stray pixels at 640×360.
  Gate with `verify_scene.sh` (deterministic geometry audit + a self-test that proves the
  audit goes RED on broken input) AND spot-check a 1280×720 still.
- **Blender toolchain probe** occasionally times out at 30s under load and aborts a batch
  ("toolchain version mismatch"). It's transient — just relaunch the batch.
- **ReflectionProbe (UPDATE_ONCE)** captures at start → the render is slow to produce the
  first frame (probe cubemap bake); it is not stuck.
- Kill stray `godot` procs (`pkill -9 godot`) before relaunching a render — a zombie on
  stale shader source will keep reporting old compile errors.

## Camera / timing knobs (game/othello.gd)

`LIFT=0.5`, `CAM_CENTER=(0,0.34,0)`, `CAM_EL_TOP=86°→CAM_EL_END=25°`,
`CAM_D_TOP=1.15 CAM_D_END=1.06`, `CAM_SPINS=1.5`. Sea plane 60×60 @ subdivide 200.
Island: `SEA_Y=-0.16`, `ISLAND_R=0.62`, pedestal a single column at origin.

## Open ideas / possible next steps (not started)

- Sea: true transparency/refraction at the immediate waterline (see into the shallows);
  currently depth-faked by distance to shore.
- Rocks: even sharper stratification / more directional marble grain.
- Marble: currently evenly veined — could add a dominant directional grain.
- Human-vs-bot input mode; SFX hit-stop on flips; even-blacker discs with clearcoat.

Reference vs our render: the composition (board on marble pedestal, rock islet,
foamy sea, distant coast, daylight) matches; the sea is bluer-greyer than the
photo's deep blue-green and the sky less dramatically clouded at the low landing
angle. See the side-by-sides delivered in chat.
