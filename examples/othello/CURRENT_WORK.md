# CURRENT_WORK — Othello on a Greek sea-stack (resume notes)

State handoff so a fresh session can pick this up with full context. Read this
first, then `examples/othello/README.md` for the deeper "why".

## Branches

Both repos are on **`claude/cross-project-pipeline-review-59m1k8`**:
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
envkit/                          NEW: reusable deterministic env shaders (canonical)
  godot/{sea,sky,rock}.gdshader  + README.md (contract & gotchas) + check_sync.sh
examples/othello/
  spec/reference_sea_arena.png   the target reference photo (board on a column pedestal at sea)
  arena_batch.json               pipeline request for the Ionic marble column
  verify_scene.sh                headless geometry audit — RUN BEFORE EVERY RECORD
  game/
    othello.gd/.tscn   scene: loads glbs, plays a full game, spiral camera, drama FX,
                       lightning, deterministic record; `--audit` mode; `--still` look-dev
                       mode (NEW); builds sea/rock/coast
    rules.gd bot.gd test_rules.gd    pure rules + bot + 14 headless checks
    audio.gd           procedural PCM SFX (place/flip/win/thunder), no assets
    mux_audio.py       overlays the procedural soundtrack onto the recorded mp4
    sea.gdshader       Gerstner + Fresnel + wind-streaked whitecaps + crest translucency
                       + pulsing surf collar   (consumer copy — canonical in envkit/)
    sky.gdshader       daylight fbm clouds + horizon cloud bank + lightning `flash`
    rock.gdshader      3-layer self-similar displacement (instance `dscale`), world-Y
                       strata bands, softened facet normals, narrow waterline wet band
    assets/board.glb disc.glb column.glb    the delivered, validated pipeline assets
    othello_game.mp4   the recorded film
assetpipe/generators/env/column.py        Ionic/Doric fluted column generator (env piece)
themes/greek_arena/                        theme + materials/marble_white.py (aged warm marble)
```

## Session 2026-07-09 (this one) — what changed

Realism pass on water/rocks/sky against the reference photo, plus two pipeline
improvements:

1. **`--still=SECONDS --out=x.png` look-dev mode** in `othello.gd`: fast-forwards
   the deterministic clock *without* rendering per step, renders ONE frame after
   ~30 warm-up frames (ReflectionProbe/sky radiance need them or the rock renders
   black), saves PNG. Shader iteration: ~40 s instead of a ~30 min record.
2. **`envkit/`**: the sea/sky/rock shaders promoted to a documented, reusable
   module at the repo root. Consumers keep copies (Godot can't load outside the
   project root); `envkit/check_sync.sh` fails when copies diverge.
3. **Sea**: deep blue-green palette, damped Fresnel/fog washout, shorter/steeper
   chop, wind-streaked whitecaps (fold threshold now *reachable* — the old set
   never crossed it, so zero caps), crest translucency, pulsing surf collar.
4. **Rock**: self-similar per-instance displacement (`dscale` instance uniform),
   world-Y strata bands, softened facet normals, dry tan/grey palette,
   `SPECULAR≈0` dry (kills the probe's white veil), narrow wet band, pole fade
   (no starburst under the pedestal), denser tessellation, chunks rotated on all
   axes, plinth tucked into the rock shelf.
5. **Sky/atmosphere**: horizon cloud bank, darker cloud bases, deeper zenith,
   below-horizon dark sea haze (beyond the sea plane edge), thinner fog, coast
   changed from boxes (read as floating slabs) to unshaded haze-colored hill
   silhouettes, fill light 0.40→0.22.

Progression stills for this pass live only in the session scratchpad; the
committed evidence is the new `othello_game.mp4`.

## Inspelningspolicy (användarens direktiv 2026-07-09)

**Spela INTE in en ny film för små steg.** En full inspelning kostar ~30–40 min
under lavapipe; användaren vill inte se filmer av inkrementella förbättringar.
Iterera med `--still`-läget (sekunder, gratis) och spela in + committa en ny
`othello_game.mp4` FÖRST när scenen tagit ett STORT kliv mot referensbilden
(`spec/reference_sea_arena.png`) — t.ex. en helt omarbetad delkomponent, inte
justerade trösklar/färger.

## How to work on this (fresh cloud session)

```bash
bash scripts/setup_toolchain.sh && export PATH=/opt/toolchain/bin:$PATH
apt-get install -y --no-install-recommends mesa-vulkan-drivers ffmpeg   # lavapipe + mux
bash examples/othello/verify_scene.sh        # must print SELFTEST_PASS + AUDIT_PASS
cd examples/othello/game

# iterate on a shader (one ~40 s frame, NOT a full record):
xvfb-run -a -s "-screen 0 1280x720x24" godot --path . res://othello.tscn \
    --rendering-driver vulkan --resolution 1280x720 -- "--still=30" "--out=/tmp/x.png"

# full re-record + mux when the stills are right:
xvfb-run -a -s "-screen 0 1280x720x24" godot --path . res://othello.tscn \
    --rendering-driver vulkan --resolution 1280x720 -- "--record=/tmp/rec" "--fps=24"
ffmpeg -y -framerate 24 -i /tmp/rec/frame_%04d.png -c:v libx264 -pix_fmt yuv420p silent.mp4
python3 mux_audio.py --dir /tmp/rec --video silent.mp4 --out othello_game.mp4
# then: git add othello_game.mp4 && commit && push
# spot-check a HIGH-RES landing still (--still=999) — do NOT trust low-res.
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
  Gate with `verify_scene.sh` AND spot-check a 1280×720 still.
- **Env-shader gotchas** (probe warm-up, `dscale`, foam thresholds, dry SPECULAR≈0,
  pole starburst, tessellation) are all documented in **`envkit/README.md`** — read it
  before touching sea/sky/rock.
- **Blender toolchain probe** occasionally times out at 30s under load and aborts a batch
  ("toolchain version mismatch"). It's transient — just relaunch the batch.
- **ReflectionProbe (UPDATE_ONCE)** captures at start → the render is slow to produce the
  first frame (probe cubemap bake); it is not stuck.
- Kill stray `godot` procs (`pkill -9 godot`) before relaunching a render — a zombie on
  stale shader source will keep reporting old compile errors.
- **PATH does not persist between shell invocations** in the harness — re-export
  `/opt/toolchain/bin` in every command (or you get `godot: command not found`).

## Camera / timing knobs (game/othello.gd)

`LIFT=0.5`, `CAM_CENTER=(0,0.34,0)`, `CAM_EL_TOP=86°→CAM_EL_END=25°`,
`CAM_D_TOP=1.15 CAM_D_END=1.06`, `CAM_SPINS=1.5`. Sea plane 60×60 @ subdivide 280.
Island: `SEA_Y=-0.16`, `ISLAND_R=0.62`, main rock at y=-0.26 (shelf just above 0),
pedestal a single column at origin.

## Open ideas / possible next steps (not started)

- Sea: true transparency/refraction at the immediate waterline (see into the shallows);
  currently depth-faked by distance to shore.
- Crashing spray: animated billboard bursts where big folds meet the shore ring
  (the reference has explosive white surf; `_spawn_spray` exists but is unused
  because it occluded the pedestal — a fold-gated, low-height version could work).
- Sky: bolder sculpted cumulus (current fbm cover is decent but soft).
- Marble: dominant directional grain; even-blacker discs with clearcoat.
- Human-vs-bot input mode; SFX hit-stop on flips.
- envkit: give it its own tiny demo scene + golden-image test so the kit can be
  verified without the othello example.
