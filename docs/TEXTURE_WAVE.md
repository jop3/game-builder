# Next wave: texture & material polish (hand-painted look)

Follow-up to docs/HOUSE_ROADMAP.md (all five structural phases landed,
branch `claude/vision-verification-9xdrpd`). The house now matches the
stylized reference structurally; the remaining distance is almost entirely
in the TEXTURES. This file is self-contained for a fresh session.

## Bootstrap (~10 min)

    bash scripts/setup_toolchain.sh && export PATH=/opt/toolchain/bin:$PATH
    python3 -m pytest assetpipe/tests -q        # ~455 tests, green baseline

Verification loop (how every item below gets judged): run the pipeline with
the agent vision client and inspect the renders yourself against the
reference image —

    python -m assetpipe generate --request <house_request.json> --out runs/ \
        --blender-bin blender --vision-client agent --vision-exchange exch/
    # watch exch/call_NNNN/: view images/, follow prompt.txt, write the tool
    # input to report.json (protocol: assetpipe/vision/agent_client.py)

House request: asset_id fantasy_house_small_01, category environment_piece,
theme fantasy_medieval, seed 77, description "A small wooden house with a
red shingled roof, glowing windows and a roof dormer",
material_overrides {"color1_hex": "#8A1E1E", "color2_hex": "#6E1414"}.
Presentation check: scripts/beauty_shot.py on the final .glb.
Read docs/NEXT_STEPS.md "Gotchas" before touching bake/render code.

## Improvement items

All materials live in `themes/fantasy_medieval/materials/*.py` (node graphs
built in `build(nt, params, rng, palette_dict)`; shared builders in
`assetpipe/matlib/nodes.py`). Baking: `assetpipe/blender_scripts/bake.py`
(multi-slot atlas; per-map budgets; emissive capped 512 on web).

1. **Painted-light albedo** (the single biggest gap). The reference bakes
   fake lighting into albedo: per-plank/per-tile value + hue variation,
   darker toward the ground, bright painted edge highlights on every
   corner/chamfer. Add a `matlib.nodes` group for per-cell value jitter
   (white-noise cell index from the brick/course cell) and reuse it in
   `fantasy_aged_wood` (per plank) and `fantasy_roof_shingles` (per tile).
   Edge highlights: `matlib.nodes.edge_wear` exists and is unused by these
   materials — mix a lighter tint through it.
2. **Shingle read**: courses are geometry now, but the albedo brick pattern
   fights them (two competing tile grids). Scale `course_scale` so the
   painted courses match the geometric rows (7 rows/slope), add per-tile
   hue shift between oxblood/rust, and darken the underside/mortar lines.
3. **Wood grain richness**: `fantasy_aged_wood` reads flat mid-brown.
   Wants: streaky vertical grain (stretch the noise along Z), occasional
   near-black boards, subtle warm/cool alternation, stronger grunge at the
   bottom third (height-gradient mask via Object Z).
4. **Window glow**: brighter and warmer in dark shots. Raise emissive
   strength (or bake HDR-ish by scaling emissive albedo), add the deferred
   per-pane radial vignette — needs per-part coords: the shared material
   sees whole-house Object coords, so bake a Generated-coords trick or
   accept a noise-based center bias (see phase-1 note in HOUSE_ROADMAP).
   Also paint the mullion cross into the emissive as dark bars so the glow
   reads as panes even at LOD distance.
5. **Stone**: `fantasy_stone_wall` now samples grey `secondary` (fixed) but
   the plinth still reads flat and sandy in renders — wants visible cobble
   cells (bigger `cell_scale` on the plinth), darker grout, and a green
   moss accent (noise-masked mix toward a desaturated green; palette has no
   green — add one to the theme or keep it subtle/desaturated).
6. **Known gap that will bite here**: `material_overrides` is ONE dict
   shared by every slot (bake.py resolves each recipe against it). Slot-
   scoped params (extend `materials` entries to `{recipe, params}` objects,
   keeping plain strings valid) unblocks per-material tuning from requests.
   See "Known constraints" in HOUSE_ROADMAP.
7. **Description-driven color** (deferred from phase 1): map color words in
   the request description to material hex params at intake/planning time,
   replacing today's manual material_overrides.
8. **Optional — texel budget**: everything bakes into one 1024 atlas; the
   house is large, so per-plank detail is ~2-3 texels wide. If painted
   detail stays mushy, consider 2048 for environment_piece in
   `assetpipe/profiles/*.json` (check FILE_TOO_LARGE headroom: 3 MiB glb
   cap, shrink_textures fix will fight you).

## Acceptance

A validated run whose renders, judged side-by-side with the reference by
real vision, show: per-plank/per-tile color variation, painted edge
highlights, matched shingle geometry+texture, streaky dark wood, glowing
mullioned panes readable in beauty_shot.py output, and grey cobble plinth
with moss accents. Keep every existing rubric check passing — texture work
must not regress S16/S17/A1 (thresholds in assetpipe/config/defaults.yaml).
