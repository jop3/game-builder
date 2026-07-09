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
  sea.gdshader    deterministic ocean: Gerstner waves + Fresnel + shallow-water tint + fold-foam + ripple normals
  sky.gdshader    deterministic sky shader (drifting fbm clouds + lightning flash uniform)
  rock.gdshader   realistic wet rock: ridged+fine displacement, micro-detail normals, slope/crevice/lichen colouring
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

Run the tests:  `godot --headless --path game --script res://test_rules.gd`
Audit geometry: `bash verify_scene.sh`   (headless — MUST pass before recording)
Play it:        `godot --path game res://othello.tscn`   (with live procedural sound)
Record a game:  `godot --path game res://othello.tscn -- --record=DIR --fps=24`
Add the sound:  `python3 game/mux_audio.py --dir DIR --video silent.mp4 --out othello_game.mp4`
Look-dev still: `godot --path game res://othello.tscn -- --still=SECONDS --out=x.png`
                (fast-forwards the deterministic clock, renders ONE frame — a shader
                iteration costs ~40 s instead of a full recording; run under xvfb+vulkan)

### Guarding against silent geometry bugs

A pedestal that punched **up through the board** shipped once because it was
eyeballed in low-res smoke frames, where a poke-through is a couple of stray
bright pixels easily mistaken for a disc. Two habits stop that recurring:

1. **`verify_scene.sh`** runs the scene headless in `--audit` mode: it builds the
   rock, pedestal, board and discs and asserts the spatial invariants
   *deterministically, without rendering* — pedestal top below the board top but
   reaching its underside, discs on the play surface (not at the pedestal foot),
   board above sea level. It first runs a **self-test** that feeds the checker
   deliberately broken values and requires it to go RED on every one, so a
   silently-broken audit can't pass. Run it before every recording.
2. **Spot-check a HIGH-RES still**, not a 640×360 frame — extract one 1280×720
   landing frame and actually scrutinise it. Interpenetration and z-fighting are
   invisible at smoke resolution.

The root cause here was ordering: `_build_pedestal()` ran before `_load_assets()`,
so the column height was a hardcoded guess instead of measured against the board.
It now builds *after* the board loads and scales to `_board_bottom_y`.

### The ocean (`sea.gdshader`)

Not a bespoke sum-of-sines — the standard CG ocean recipe (GPU Gems 1, ch. 1):

- **Gerstner (trochoidal) waves** — each vertex orbits in a circle (horizontal +
  vertical), summed over 4 waves of different length/direction/steepness, giving
  sharp crests and rounded troughs with a correct *analytic* normal (no
  derivative hack needed for the coarse waves).
- **Fresnel** — water reflects the sky at grazing angles; a Schlick term
  (F0≈0.02) blends a sky tint into the albedo so the horizon reads bright and
  the near water deep — the single biggest "reads as water" cue.
- **Foam from the wave fold (Jacobian)** — `Σ Q·k·A·sin(...)`; where crests
  pinch past vertical the surface would self-fold, which is exactly where real
  water froths. The caps are gated by a **wind-stretched streak noise**
  (anisotropic along the main wind direction) so the whitecaps come in ragged
  wind rows, and the shoreline foam collar **pulses with the local fold** so
  the surf appears to strike and withdraw. NOTE: the fold sum tops out at
  `Σ Q·k·A` — if you retune the wave set, re-check that the foam smoothsteps
  are still reachable or every whitecap silently disappears.
- **Crest translucency** — the tops tint toward a bright blue-green with wave
  height (fake subsurface: the "glass" of a breaking crest).
- **Ripple normals** — per-fragment, the normal is perturbed by the gradient of
  a scrolling value-noise, adding sub-tessellation surface texture and letting
  the key light throw sun-glitter sparkle across the crests.

All animated by the `t = _elapsed` uniform (never `TIME`) so it stays
deterministic. Godot gotcha: `TAU`/`PI` are built-in shader constants — don't
redefine them (use your own `TWO_PI`), or the shader fails to compile.

Two more passes on top:

- **Shallow water** near the islet is depth-faked by distance-to-shore: within
  `shore_reach` of the island the water tints toward a bright turquoise
  (`shore_water`), broken by noise so the band isn't a clean ring — the "clear
  shallows over the reef" read, no depth buffer or screen refraction needed.
- A **`ReflectionProbe`** (UPDATE_ONCE — one cheap capture at start) gives the
  wet surfaces real local reflections of the sky, rock and board on top of the
  shader's Fresnel sky-tint, so the marble and sea pick up a wet sheen.

### The rocks (`rock.gdshader`)

The islet is displaced spheres, but the *look* is all shader:

- **Three displacement layers** in the vertex stage — big **ridged fbm** (sharp
  arêtes and gullies, not smooth bumps), mid-frequency ridges, and a fine fbm
  grain. Sampled in **object-size units** with a per-instance world offset and
  scaled by a per-instance **`dscale`** uniform: a fixed amplitude gave the
  small boulders glass-shard spikes (± half their radius), a fixed frequency
  made them smooth potatoes (under one noise period per block). Displacement
  fades out at the mesh's local pole (the sphere's UV pinch otherwise renders
  a radial "starburst" right under the pedestal).
- **Softened geometric normals**: the fragment normal blends the `dFdx`/`dFdy`
  facet normal with the smooth interpolated normal (pure facet normals read as
  crumpled foil on big triangles), then gets a micro-perturbation from the
  gradient of a 3-D world-space noise (no UVs → no stretch) for grit.
- **Colour by form and world height, not a flat albedo**: narrow sedimentary
  bands in world-Y (sin bands + noise warp — coherent layers across the whole
  islet), crevices darken like ambient occlusion, up-facing slopes take a
  lichen tint, and a *narrow* wet band at the waterline darkens and glosses
  the stone — full-height wetness read as wet brown clay. Dry rock keeps
  `SPECULAR ≈ 0`: with a ReflectionProbe in the scene, even 0.18 drapes a
  white sky-sheen veil over the whole rock.

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

### Reusable environment kit

The three environment shaders graduated into **`envkit/godot/`** at the repo
root — the canonical, documented, game-agnostic copies (determinism contract,
integration gotchas, look-dev harness pattern). The copies here are consumers;
`bash envkit/check_sync.sh` fails if they diverge. Iterate here (the look-dev
harness lives here), then copy back to `envkit/` and commit both.

The full playable game (rules + bot + flip animation, spec M1/M3/M4) **is built**
here; the remaining spec milestones (difficulty tiers, hotseat) are future work.
