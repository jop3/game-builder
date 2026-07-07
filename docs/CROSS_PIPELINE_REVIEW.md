# Cross-pipeline review — game-builder ⇄ Snittet

A review of two sibling pipelines and what they taught each other. **game-builder**
generates 3D game *assets* (Blender → glTF → Godot). **Snittet** builds a *game* and
carries `spelbygge`, a meta-pipeline for taking a game from vision → verified → autoplayed.
Different domains, same spine: autonomous, verify-before-claim, "the human is an escalation,
not a loop-station."

## The finding: independent convergence

The two projects were built separately and still arrived at the same core ideas — which is
the strongest possible signal that those ideas are load-bearing, not incidental:

- a **fresh-context vision reviewer**, ideally a different model, forced to answer a rubric
  (game-builder's V2 `report_inspection`; Snittet's `vlmjudge`);
- **determinism discipline** (pinned/seeded RNG, version-tagged output, refuse-on-mismatch);
- an **idempotent cloud bootstrap** because the harness must never lack its own tools
  (`scripts/setup_toolchain.sh` ⇄ `tools/bootstrap_cloud.sh`);
- **cost-ordered verification** (cheap scripted checks before the expensive model call);
- a **loop that owns stopping** with plateau/oscillation/budget conditions.

Where they diverged is where they had something to teach each other.

## What game-builder was strong at (Snittet borrowed these)

game-builder's edge is *machinery*: a closed defect taxonomy + deterministic defect→fix
table, an append-only `history.jsonl` replay spine (§17.2), fault-injection tests that
verify the verifier (§21.1), an explicit escalation ladder (§16.1), and anti-false-positive
rules baked into its vision schema (§15.3).

Implemented in Snittet this pass:

| Borrow | game-builder source | Landed in Snittet |
|---|---|---|
| **Verify-the-verifier** fault-injection tier | §21.1 (every fixture fails its designated check; goldens pass all) | `tests/test_fixtures.gd` (wired into `tools/run_tests.sh`); doctrine in `spelbygge/referens/verifiering.md` |
| **Append-only structured log** (replay spine) | §17.2 `history.jsonl` | `docs/iterationslogg.jsonl` format in `spelbygge/referens/konvergens.md` |
| **Escalation ladder** + **multiset oscillation detection** | §16.1, §16.5.3 | `konvergens.md` step 5 |
| **Closed deviation vocabulary** | App. B closed taxonomy | `konvergens.md` step 2 |
| **Anti-false-positive rules + forced structured output** for the vision judge | §15.3–§15.4 | `tools/spatialcheck/vlmjudge_rubric.md` |

## What Snittet was strong at (game-builder borrowed these)

Snittet's edge is the *human front-end and anti-soulless doctrine*: convert vision to
controllable criteria **before** coding, the **NOT-list** as the strongest drift guard, an
open-ended "what's the ugliest thing?" catch-all beyond any closed rubric, and the honest
boundary that some quality genuinely needs eyes — documented, not automated away.

Implemented in game-builder this pass:

| Borrow | Snittet source | Landed in game-builder |
|---|---|---|
| **Open-ended catch-all** for the technically-valid-but-off failure | spelbygge feel-rubric "vad är det FULASTE?" | `worst_thing` field: `contracts.py`, `vision/prompts.py`, spec §15.4 (non-gating, logged) |
| **NOT-list** promoted to a first-class field | spelbygge brief's INTE-lista | `anti_style` in all 4 theme packs + `themes_io.validate_theme` + prompt injection + spec §7 |
| **Process doctrine**: gates-are-work, anti-patterns, and the **honest human boundary** as a counterpoint to "zero human review" | spelbygge phase gates + "done is declared by the user" | `docs/PIPELINE_DOCTRINE.md` |

The doctrine borrow is the important one: "zero human review" is right *per asset, for
objective quality*, but a batch of individually-valid assets can still be collectively
wrong (drifted theme, incoherent set). That is a property of the *set*, which no per-asset
gate can see — so `PIPELINE_DOCTRINE.md` §4 draws an explicit, un-automated art-direction
spot-check, fed by exactly the signals the borrows above now produce (`worst_thing`,
`anti_style`, best-effort diagnoses).

## Verification

- game-builder: `python -m pytest assetpipe/tests` — **527 passed** (10 new tests cover
  `worst_thing` schema/validation and `anti_style` validation/prompt injection).
- Snittet: `tools/run_tests.sh` — all suites green including the new **15-check**
  `test_fixtures.gd` fault-injection tier.

## Not done (deliberately out of scope this pass)

- game-builder's Stage B (skybox/background) is still unimplemented; unrelated to this review.
- Snittet's `iterationslogg.jsonl` is specified in the skill but no live convergence run has
  emitted one yet — it lands the first time Phase 4 runs.
- A shared bootstrap/determinism helper was *not* extracted; the two environments differ
  enough (Godot-in-cloud vs Blender+Godot toolchain) that duplication is currently cheaper
  than a shared abstraction.
