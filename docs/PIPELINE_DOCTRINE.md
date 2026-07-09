# Pipeline doctrine — gates, anti-patterns, and the one honest human boundary

This document is the *process* companion to `docs/specs/asset-pipeline.md` (the
*architecture*). The spec says what the stages are; this says how to run the loop
without fooling yourself. It is distilled from the sibling project **Snittet** and
its `spelbygge` skill — a game-building pipeline that converged on the same
autonomous, verify-before-claim discipline this one has, and that paid for the
lessons below on real builds. Where the spec is normative, this is doctrine: read
it before changing a gate or the fix loop.

---

## 1. The gate IS the work

Every stage in §4.1 ends at a gate (V1 static, V2 vision, the fix loop's stopping
conditions). The failure mode that doctrine exists to prevent is **passing a gate by
promising to satisfy it later** — shipping an asset `validated` with a note that the
seam "will be fixed in a follow-up", or relaxing a threshold to make a batch go green.
A gate that is deferred is a gate that never runs. If an asset cannot pass a blocker
check, it is `best_effort` with a diagnosis (§16.6), not `validated`. That honesty is
what makes the `validated` label mean something to whoever consumes the batch.

Corollary: **thresholds are tuned in `config/defaults.yaml` deliberately and with a
recorded reason, never nudged mid-run to clear a specific asset.** A threshold change
must keep the §21.1 property intact — every fault fixture still fails its designated
check, every golden still passes everything. Tuning to pass one asset silently
un-guards every future one.

## 2. Anti-patterns (each has cost a real build)

- **Technically-valid-but-soulless output.** The single most important failure mode,
  and the reason Snittet's whole pipeline exists: an asset that passes every check in a
  *closed* rubric can still be obviously wrong — off-theme, toy-like, the right object
  built with no conviction. A checklist cannot catch what it does not enumerate. Our two
  defenses are the **`worst_thing` open-ended field** (§15.4 — the inspector names the
  single worst thing even when every check passed) and the **`anti_style` NOT-list** (§7
  — the theme states what it must *not* be, hardening R5/R12). Neither gates; both feed
  the human spot-check in §4.
- **Verifying with the context that built it.** The build context is biased toward
  seeing success. V2 runs as a *fresh* inspection with no memory of generation, forced
  structured output, and evidence rules (§15.3); the vision backend should ideally be a
  *different model family* from anything used in fix planning (`docs/VISION_BACKENDS.md`
  makes the inspector swappable precisely so this is possible). A reviewer that shares the
  builder's context is a rubber stamp.
- **Trusting the vision net alone.** Cheap scripted analytics (A1–A4, S16–S19) catch the
  objective defects *first*; vision is the second net, not the only one. A blank render
  should never reach the model — it should have died at A1.
- **Silent truncation.** If a run bounds coverage (iteration cap hit, a warn left
  unfixed, skybox rejected `NOT_IMPLEMENTED`), it is written to `history.jsonl` and the
  manifest. A batch that quietly dropped work reads as "everything passed" when it didn't.
- **The pipeline declaring the *collection* done.** Per-asset `validated` is an objective
  claim the pipeline is entitled to make. "This *set* of assets hangs together as a
  coherent art direction" is not (see §4).

## 3. The loop owns stopping — the human is escalation, not a loop station

Between intake and a terminal state, the per-asset loop (`loop.py`) converges on its own:
generate → validate → render → inspect → fix, bounded by the escalation ladder (§16.1)
and the five stopping conditions (§16.5). Stage code never decides to retry or give up —
it succeeds, returns findings, or raises `InfraError`; the loop decides. This is the same
"the human is an escalation, not a loop-station" principle Snittet's convergence loop runs
on: a run that stops to ask a person mid-iteration is revealing a *missing contract*
(a threshold that was never set, a defect with no table fix), not asking a real question.

When the loop genuinely cannot proceed — an oscillating defect set, a plateau, the
iteration cap — it does not pause for a human. It terminates the asset `best_effort` with
a machine-written `diagnosis.md` listing every persisted defect, and the batch continues.
The human reads diagnoses *after* the batch, in one pass, not one asset at a time.

## 3.5 The cheap eye comes first — iteration medium doctrine

When work converges on an *expensive* artifact (a 30-minute film render, a full
batch, a long bake), the artifact must not double as the microscope. Build the
**cheap deterministic probe first**: a seconds-fast, single-sample view of the
same pixels (the Othello example's `--still` mode fast-forwards the dt-summed
clock and captures one frame, bit-identical to the corresponding film frame).
The 2026-07-09 history is the measurement: sessions that verified through full
recordings ran ~6 looks/day; the session that built the probe first ran ~12
looks in two hours and shipped more per hour (`docs/ITERATION_RETRO.md` has
the commit-level evidence).

Three disciplines ride along with the probe:

- **Arithmetic before tuning.** If an effect "doesn't show", first check it is
  *reachable* (threshold vs theoretical max, amplitude vs object radius). Two
  render-cycles were once spent tuning whitecaps whose foam threshold sat
  *above* the wave set's maximum possible fold — no parameter value could ever
  have worked.
- **Artifact differencing.** An artifact identical across parameter changes is
  not coming from the subsystem being edited — stop tuning, switch suspects.
- **Crop-zoom before diagnosing.** Visually similar defects (facet shading,
  specular veil, UV pole pinch) have different root causes that only separate
  at 4× crop. Name the root cause before writing the fix; a fix without a
  cause-name is a tweak, and tweaks don't transfer to the next asset.

## 4. The one honest boundary: art-direction cohere is not automatable

"Zero human review" (§1.1) is true and correct **per asset, for objective quality** —
manifold geometry, valid glTF, present textures, plausible scale. Those are facts a
script or a rubric can settle, and a person adds nothing by looking.

Snittet's hardest-won lesson is the counterpoint: *some* quality genuinely requires eyes,
and pretending otherwise is how you ship a batch of individually-valid, collectively-wrong
assets. For a game project that question is "is it fun / cozy?"; for an asset batch it is
**"does this set cohere as one art direction, and does it match the intent?"** — a knight
and a crate that each pass every check but read as two different games; a theme that drifted
one plausible step per asset until the batch no longer matches the reference. No per-asset
gate can see this, because it is a property *of the set*, not of any asset.

So the honest boundary is drawn, not automated away:

- **What the pipeline owns (no human):** every §13/§15 check, per asset. These gate.
- **What the human owns (a periodic spot-check, never in the loop):** a pass over the
  batch's preview renders asking "does this collection cohere and match the intent?".
  Its inputs are already produced — the `final/` previews, each report's `worst_thing`,
  and the `best_effort` diagnoses. This is the asset-pipeline analog of Snittet's Gate 0
  (confirm the target before autonomy — the `anti_style` NOT-list is where a corrected
  intent lands) and its final "the user declares done, not the agent". It does **not**
  re-enter the fix loop; findings become theme edits or new requests for the next batch.

Drawing this boundary explicitly is not a weakness of "zero human review" — it is what
keeps the automated claims trustworthy. The pipeline never claims the one thing it cannot
verify.

---

### Cross-reference

| Doctrine | Mechanism in this repo | Borrowed from |
|---|---|---|
| Catch what the closed rubric can't | `worst_thing` field (§15.4) | spelbygge feel-rubric "what's the ugliest thing?" |
| State what the theme must NOT be | `anti_style` NOT-list (§7) | spelbygge brief's NOT-list |
| Fresh, unbiased verification | V2 fresh inspection + swappable backend | spelbygge Layer-5 fresh-context reviewer |
| The loop owns stopping | `loop.py` + §16.5 | spelbygge "human is escalation, not loop-station" |
| Honest un-automatable boundary | §4 art-direction spot-check | spelbygge "done is declared by the user" |
