# Next steps — resuming work on the asset pipeline

Last updated: 2026-07-07, end of the vision-tier verification wave
(branch `claude/vision-verification-9xdrpd`).

## Getting a fresh session going (~5–10 minutes, one command)

The pipeline's pure-Python tier needs nothing special. The real-toolchain
tier (Blender + Godot) is provisioned by a single idempotent script:

```bash
bash scripts/setup_toolchain.sh          # ~400 MB of downloads on first run
export PATH=/opt/toolchain/bin:$PATH

python3 -m pytest assetpipe/tests -q     # 435 tests, ~45 s, no toolchain needed
```

Container facts the script encodes (don't rediscover these):

- **GitHub downloads are blocked** by the egress proxy (repo-scoped API
  only), so Godot comes from **Debian sid** (`deb.debian.org` is reachable)
  plus ~27 sid runtime `.so`s unpacked into `/opt/toolchain/godot-libs`,
  wrapped by `/opt/toolchain/bin/godot` (sets `LD_LIBRARY_PATH`). The sid
  binary runs on Ubuntu 24.04 as long as its glibc requirement stays
  ≤ the host's (checked by the script; 4.6.3 needs 2.38, host has 2.39).
- `download.blender.org` and `pypi.org` are reachable directly.
- The spec §3 gate accepts Godot ≥ 4.3 via the `"4.3+"` floor pin in
  `config/defaults.yaml`; Blender must be 4.2.x exactly.
- 4 CPU cores, no GPU: a 1024² bake ≈ 2 min, the render view set ≈ 4 min.
  Give bake/render generous `stage_timeouts` in test configs (the spec's
  600 s default is tight for the AO pass on this hardware).

Smoke commands once the toolchain is up (drive them from a scratch dir;
each run writes a self-contained run dir with `history.jsonl`):

```bash
# one asset through the full loop (vision needs ANTHROPIC_API_KEY -- absent
# in these containers; use a stub client, see "vision" below)
python -m assetpipe generate --request <request.json> --out runs/ --blender-bin blender

# deliver + verify into a scratch Godot project
python -m assetpipe deliver --run runs/<id> --adapter godot \
    --project /path/to/scratch_proj --godot-bin godot
```

## Where things stand

Everything in `assetpipe/README.md`'s module map is built, and the
2026-07 waves verified against the real toolchain:

- all nine mesh generator recipes pass every V1 blocker (S1–S12e);
- the crate runs the full loop to `validated` (fix-loop convergence
  included: FILE_TOO_LARGE → shrink-at-X → pass) and the tiling deck-plate
  does the same on iteration 1 (S19 seam checks pass on real bakes);
- exports carry correct occlusion wiring; Godot headless import +
  `verify_import.gd` green;
- §21.2 determinism: same-seed runs are byte-identical through G and M.

The vision tier (V2) has now been verified end-to-end with REAL vision and
no API key (2026-07-07, branch `claude/vision-verification-9xdrpd`): the
file-exchange agent client (`assetpipe/vision/agent_client.py`,
`--vision-client agent --vision-exchange <dir>`) blocks each vision call
while dumping prompt + renders to `call_NNNN/`; the driving Claude session
inspects the images with its own vision and writes the `report_inspection`
tool input to `report.json`, which then flows through the unchanged
semantic validation / two-view rule / crop re-query. Two full runs: the
crate (correctly failed; fix planning, escalation, best_effort + diagnosis
all exercised) and `env/house` — a NEW three-slot multi-material recipe
(aged-wood walls, shingle roof, emissive windows) — which reached
`validated` and passed Godot deliver+verify. Vision inspection caught five
real bugs the scripted checks missed (all fixed): ground-plane framing,
silhouette furniture, raw-normal/backface-red collision, LOD siblings
z-fighting the root mesh, and `materials.clear()` zeroing polygon
material indices. Only the API *transport* (`inspector.py`'s retry/backoff
against the real endpoint) still needs an `ANTHROPIC_API_KEY` run.

## Prioritized next work

1. **Stage B: skybox + background_2d** — the one unimplemented pipeline
   branch. Intake currently rejects both categories `NOT_IMPLEMENTED`.
   Already in place and waiting: `render_views.render_skybox_views`
   (equirect view set), sky defects/fixes (`POLE_PINCH`/`pole_fade`,
   `HORIZON_SEAM`/`resnap_sky` in `param_fixes.py`), S19a(sky) analytics,
   and the Godot adapter's skybox (`PanoramaSkyMaterial` .tres) and
   background delivery. To build: a `blender_scripts/sky.py` stage script
   (procedural sky node graph → equirect EXR render, spec §11), a loop/
   stages branch that runs B→R→V2 (no G/M/X, no glb), theme sky recipes,
   then remove the intake gate. Spec §11 + §16.2's sky fixes define the
   contracts.
2. **Vision transport with the real API** (spec §21.3) — needs a session/
   CI environment with `ANTHROPIC_API_KEY`. The judgement tier is verified
   (agent client, see above); what remains is the live-endpoint call shape
   + retry policy, then the labeled fixture corpus (§21.1 rendered fault
   fixtures) and the ≥90%-catch / 0-blocker-FP regression.
   `assetpipe/tests/test_inspector.py`'s fakes document the expected
   response shapes.
   Quality follow-ups from the house run: material selection should honor
   description color words ("red shingled roof" sampled the gold accent);
   wall materials could use plank/beam relief; the dormer roof leaves a
   small notch at the main ridge; A1's min-std floor false-positives on a
   flat-faced asset filling the frame (crate turn_270) — consider a
   per-view or texture-aware floor.
3. **CI wiring** — a workflow that runs the pure-Python suite per-commit,
   plus a manual/nightly job that runs `scripts/setup_toolchain.sh` and
   the two e2e smokes (crate, tiling). The smoke driver pattern lives in
   this session's scratch (`run_e2e.py`: run_batch with a stub vision
   client that returns all-pass reports built from
   `Contracts.applicable_checks`); worth committing a version of it under
   `assetpipe/tests/toolchain/` as the §21.4 integration tier.
4. **Warn cleanup (optional quality)** — S9 SELF_INTERSECTION warns on
   crate/lantern/tree/humanoid are by-design part interpenetration;
   either relax S9's threshold per category or weld parts. A couple of
   S12e margin warns remain (doorway, barrel at some seeds). Neither
   blocks validation.
5. **Known gap: LLM param patches only touch generator params** —
   `llm_param_patch` writes `params.json` (generator schema); material
   defects (`MATERIAL_IMPLAUSIBLE`, `PALETTE_VIOLATION`) have no table fix
   and would need material-param patching (`material_overrides`) to be
   fixable. Currently they resume at G and effectively regenerate
   unchanged. Design needed if vision starts flagging them.
6. **Batch scale test** — everything verified was single-asset. Run a
   multi-asset batch (`--parallel 2` on 4 cores) to exercise the
   parallel per-asset loops and the shared run manifest under contention.

## Gotchas for whoever continues (hard-won, please keep)

- Blender exits 0 on Python exceptions unless spawned with
  `--python-exit-code 1` (stages does this; keep it for any new spawn).
- Blender's bundled Python has NumPy but **no Pillow** — in-Blender code
  must never import PIL. Contact sheets are composed orchestrator-side.
- `bmesh.ops.boolean` does not exist; `create_*` ops return only
  `"verts"`. Build holes from profiles (see `kit/doorway.py`).
- Fix-table `resume_stage` = the stage to re-run AFTER an in-place repair
  (see `contracts.py`'s validate comment). Getting this backwards makes
  the loop regenerate over its own repairs — it looked exactly like a
  planner bug and wasn't.
- Discrete patterns (brick/grid) must NOT route through `PeriodicCoords`;
  integer scale over raw UV is what tiles. The torus domain is for
  continuous noise only.
- The scripted A-checks must mirror the vision prompt's exemptions
  (`lit_dark_*` is dark by design → separate A1 mean floor).
- `mesh.materials.clear()` RESETS every `polygon.material_index` to 0
  (real Blender 4.2) — snapshot + restore around slot replacement
  (see `bake.bake_all_maps`), or multi-material assets silently flatten.
- The render harness must hide `*_LOD*` siblings (they're co-located with
  the root mesh and z-fight it) and place the reference cube relative to
  the asset's bbox, not the origin (a >3 m asset swallows it).
