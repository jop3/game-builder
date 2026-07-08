# Game brief — Moonstone & Obsidian (fantasy Othello)

> Fas 0 of the **spelbygge** pipeline (the game-building skill in the sibling
> Snittet repo). Every field is filled — a blank or vague field here becomes a
> basic game later. This brief is the contract; everything downstream is
> self-reviewed against it. Worked example: a brand-new game specced with
> spelbygge, its fantasy graphics produced by this repo's asset pipeline.

**Status:** confirmed draft (methodology demo; user confirms the pitch at Gate 0)

## 1. Pitch (one sentence)

A cozy, candlelit game of Othello where two rival orders — **Moonstone** (light)
and **Obsidian** (dark) — duel with carved stone discs on a fantasy board, every
capture flipping a run of discs to your side with a satisfying stone *clack*.

## 2. Target feel (2–3 named references)

| Reference | What exactly should feel the same? |
|---|---|
| A real, weighty Reversi/Othello set | The *thunk* of a heavy disc set on wood; discs feel like objects, not sprites. |
| Tabletop Simulator board games | The board reads as a physical object under warm light; pieces cast soft shadows on the felt/wood. |
| The "reveal" moment in match-3 / capture games | The **flip cascade** — a captured run turning over one disc at a time, left to right, is the payoff of every move. |

> For the flip-feel, the numeric knobs (per-disc flip delay, flip arc height,
> ease curve, settle bounce) are pinned in the build spec §5 so the "juice" is a
> tuning target, not a vibe.

## 3. Player verbs (3–7)

Othello is a pure abstract game — the verb list is deliberately tiny, and that
is a *feature*, not a scope gap:

1. **Place** a disc on a legal cell (the only action).
2. **Preview** legal cells (hover/highlight — shows where a placement flanks).
3. **Pass** (automatic, only when you have no legal move).

More verbs would betray the pitch. The depth lives in *where* you place, not in
*how many* things you can do.

## 4. Core loop (one sentence, verb chain)

place disc → flank & flip the enemy run → board fills → count discs → the fuller
side wins → new match. (The loop's last link feeds the first: the score state
seeds the next match's framing.)

## 5. Win condition — MACHINE-CHECKABLE

A state that code checks, not a feeling:

> `game_over == true` (neither side has a legal move) **AND**
> `count(MOONSTONE) != count(OBSIDIAN)`; winner = side with the greater count.
> A draw is the explicit `count(MOONSTONE) == count(OBSIDIAN)` state.

Every sub-fact is checkable per board state: legal-move existence, disc counts,
terminal detection. This is what lets a bot play the whole game unattended and
what makes the rules testable before any graphics exist (build spec §4).

## 6. Friction (what makes it hard/interesting?)

Pure strategic friction — no timers, no twitch:

- **Flanking constraint:** a placement is only legal if it captures ≥1 enemy disc
  in a straight line bounded by your own disc. Most cells are illegal most turns.
- **Corners & edges:** corners can never be flipped, so they are decisive;
  giving your opponent access to one is the classic mistake.
- **Mobility:** the fewer legal moves your opponent has, the better — a good move
  starves them, not just grabs discs. (Greedy "flip the most now" usually loses.)

The variation each match comes from the branching board state, not from content
the pipeline has to author.

## 7. Progression & long loop (what changes after an hour?)

For this **spec + graphics demo**: explicitly **none — the single match IS the
game**, and that is a decision, not a hole. A real product would add, as *named*
milestones (never smuggled in): a **difficulty bot** (parameterized lookahead +
mistake rate → win-rate-per-tier as data), **local 2-player hotseat**, **board
skins** (the asset pipeline makes new theme packs cheap), and **match history /
ranked ladder**. The demo deliberately ships the polished single match.

## 8. Reference images

For the graphics: warm fantasy tabletop — carved wood/stone board with a subtle
rune border, ivory/moonstone light discs, polished-obsidian dark discs, warm
key light. These become the asset requests + theme in this repo's pipeline
(`examples/othello/assets/`), and the produced turnaround renders are committed
back as the ground-truth reference (the pipeline's V2 vision gate is the
"is this what we meant?" check, standing in for a human vision-proof).

**Pointed vision-proof:** the pipeline's own render harness + V2 inspection
(cloud, no ComfyUI) — a candidate render is accepted only if it passes the
fantasy-tabletop rubric.

## 9. Audience & design consequences

Board-game players who want a calm, beautiful single game — so: **no punishment
for thinking** (no move clock), **legal moves are always shown** (the rule is the
puzzle, not remembering the rule), and **the flip is legible** (you can always
see what your move captured). A first-timer should learn Othello by watching one
flip cascade.

## 10. NOT-list (scope fence) — the most important field

- **NOT** online/networked multiplayer.
- **NOT** AI difficulty tiers beyond one reference bot (a tier ladder is a named
  future milestone, not this build).
- **NOT** animations beyond the disc-flip cascade and a soft place-down settle.
- **NOT** variant board sizes or rule variants (only standard 8×8 Othello).
- **NOT** a free 3D camera — one fixed, slightly-angled board view.
- **NOT** story/campaign, unlockables, or currency.
- **NOT** hand-modeled art — all graphics are generated by this repo's pipeline.

Anything that surfaces during the build and doesn't serve the pitch is tested
against this list; if it's in neither the brief nor here, ask the user.

## 11. Open questions

Empty (required before Gate 0). Standard-Othello rules are fully specified; the
only real design choice — light vs. dark disc identity — is fixed as
**Moonstone (light) vs. Obsidian (dark)**, Moonstone moves first (standard
Othello: dark moves first, so **Obsidian moves first** per the official rule).
