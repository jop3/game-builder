# Next wave: description-driven color & texture survival

Follow-up to docs/TEXTURE_WAVE.md (items 1-6 landed and verified on branch
`claude/texture-wave-work-bsv4ak`: painted-look materials, cell_jitter,
slot-scoped material params, validated house run). What's left is COLOR
INTELLIGENCE (requests should not need hand-written hex overrides) and
making the painted detail SURVIVE the web budget. This file is
self-contained for a fresh session.

## Bootstrap (~10 min)

    bash scripts/setup_toolchain.sh && export PATH=/opt/toolchain/bin:$PATH
    python3 -m pytest assetpipe/tests -q        # ~460 tests, green baseline

Verification loop (how every item below gets judged): run the pipeline with
the agent vision client and inspect the renders yourself —

    python -m assetpipe generate --request <request.json> --out runs/ \
        --blender-bin blender --vision-client agent --vision-exchange exch/
    # watch exch/call_NNNN/: view images/, follow prompt.txt, write the tool
    # input to report.json (protocol: assetpipe/vision/agent_client.py)
    # NOTE: run with PYTHONPATH=<repo root> if driving from a scratch dir,
    # and give bake/render generous stage_timeouts (a pipeline.yaml with
    # bake/render/fixes at 2400 s worked on this 4-core box).

House request: asset_id fantasy_house_small_01, category environment_piece,
theme fantasy_medieval, seed 77, description "A small wooden house with a
red shingled roof, glowing windows and a roof dormer". The point of this
wave: that request should produce the SAME red roof WITHOUT its current
material_overrides {"color1_hex": "#8A1E1E", "color2_hex": "#6E1414"}.
Presentation check: scripts/beauty_shot.py on the final .glb.
Read docs/NEXT_STEPS.md "Gotchas" and the TEXTURE_WAVE status notes before
touching bake/render code.

## Improvement items

1. **Description-driven color** (deferred twice; the headline item). Map
   color words in the request description to material params at
   intake/planning time, replacing manual material_overrides.
   - A small pure-Python lexicon (red/oxblood/crimson, green, blue, gold/
     yellow, grey, brown, black, white...) -> hex anchors, then snap each
     to the NEAREST theme palette entry (HSV distance) so spec 10.5's
     "colors trace to the palette" survives; only fall back to the raw
     anchor when the palette has nothing within tolerance, and jitter it
     through the existing sample bounds.
   - Bind color words to their NOUN ("red shingled roof" colors the roof
     slot, not the walls): a naive window of 1-2 tokens before a known
     part word (roof/wall/door/window/trim/banner) is enough; unbound
     color words apply to nothing rather than everything.
   - Emit slot-scoped overrides using the item-6 plumbing: extend the
     resolved `materials` entries' params (e.g. shingles color1_hex/
     color2_hex from the matched word, second hex = darkened first).
     Explicit request material_overrides must still WIN over derived ones.
   - Unit-test the mapping pure-Python (word -> slot -> hex), then verify
     with the house request MINUS its material_overrides: roof must come
     out oxblood, not the gold accent (the exact bug in NEXT_STEPS
     priority 2's quality notes).
2. **Texture survival under shrink_textures**. The richer painted maps put
   the seed-77 web glb at ~6.8 MiB; the FILE_TOO_LARGE fix halves EVERY
   map and shipped 2.4 MiB. Painted per-plank detail is what pays. Make
   the fix priority-aware: shrink normal+orm first (their detail is least
   visible at this art style), re-measure, and only then touch albedo,
   emissive last (it is already 512-capped and carries the window read).
   The fix lives in assetpipe/fixes/ (shrink_textures family,
   map_fixes.py); keep S14 per-map budget checks green and the fix loop
   convergent (it must still terminate when albedo alone busts the cap).
3. **Moss/plinth readability at shipped resolution**. The moss accent
   reads in the 1024 albedo but nearly vanishes after the shrink (and the
   grout thins). Options once item 2 lands: slightly larger moss patches
   (moss_noise scale down toward ~2.0), stronger moss factor on the
   plinth's slot params in env/house, or nudge grout width. Judge in
   beauty_shot + turn renders, not the raw map.
4. **Window glow polish**. The dormer pane bakes paler than the main
   window (its center-bias noise peak clamps). Consider narrowing
   bias_var's To Max (1.35 -> ~1.2) so cores stay gold, and/or a slot
   param for the dormer. The door lantern cage (SLOT_GLASS) shares the
   window material and reads white-ish up close — acceptable, but a
   dedicated warmer/omni glow would sell it in beauty shots.
5. **Extend the painted look across the theme**: fantasy_iron_trim (edge
   highlights + per-rivet value jitter via cell_jitter) and
   fantasy_cloth_banner (weave value jitter, sun-fade gradient via the
   height mask trick from aged wood). Both are small graphs today, so
   this is mostly reusing groups that now exist. Optional stretch: port
   cell_jitter usage into medieval_realistic's timber/stone.
6. **Optional — theme green**: the moss green is derived from `secondary`
   to stay palette-traceable. If vision keeps calling it grey, add a
   proper desaturated green to the fantasy_medieval palette — but NOT to
   `accent` (banner/roof default-sample accent); a new group needs a
   validate_theme decision, so weigh cost vs. item 3's cheaper knobs.

## Hard-won constraints (do not re-learn these)

- The baked emissive PNG is the entire glow story: 8-bit, clamped at 1.0,
  export_gltf pins Emission Strength = 1.0. SHAPE the map; never multiply
  brightness through it (strength-4 clamped panes to hueless white).
- matlib.nodes.cell_jitter matches Cycles' brick cells exactly (offset on
  EVEN rows, verified against a real bake); pass it the same Vector/Brick
  Width/Row Height/Offset as the brick node or jitter bleeds across seams.
- Discrete patterns (brick/grid/cell_jitter) never route through
  PeriodicCoords; continuous noise in TILING recipes always does.
- Slot params beat request material_overrides in bake.py — derived colors
  (item 1) should be injected as slot params so explicit request overrides
  keep winning end-to-end.
- 2048 atlases are OFF the table for the web profile (3 MiB cap, item 2).

## Acceptance

A validated seed-77 house run with NO material_overrides in the request
whose roof is oxblood red (description-driven), whose painted per-plank/
per-tile detail is still legible in the SHIPPED (post-shrink) glb's
renders and beauty shot, with moss visible on the plinth and warm gold
panes on every window including the dormer. All ~460 unit tests green,
plus new pure-Python tests for the color-word mapping. Keep every rubric
check passing — S16/S17/A1 must not regress (thresholds in
assetpipe/config/defaults.yaml).
