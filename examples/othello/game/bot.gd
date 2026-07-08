# bot.gd — a deterministic corner-aware bot so a match plays out like real
# Othello (greedy disc-count loses; positional play looks strategic). No randf:
# ties break by lowest cell index, so a seed isn't even needed — same game
# every run (build_spec test_determinism).
class_name OthelloBot
extends RefCounted

const Rules := preload("res://rules.gd")

# Positional weights: corners decisive, the squares next to them (C/X) are
# traps, edges good, center neutral. Classic Othello heuristic table.
const WEIGHTS := [
	120, -20, 20,  5,  5, 20, -20, 120,
	-20, -40, -5, -5, -5, -5, -40, -20,
	 20,  -5, 15,  3,  3, 15,  -5,  20,
	  5,  -5,  3,  3,  3,  3,  -5,   5,
	  5,  -5,  3,  3,  3,  3,  -5,   5,
	 20,  -5, 15,  3,  3, 15,  -5,  20,
	-20, -40, -5, -5, -5, -5, -40, -20,
	120, -20, 20,  5,  5, 20, -20, 120,
]

# Pick a move for `side`, or -1 if none. Maximizes the resulting position's
# weight from `side`'s view (own weighted cells minus opponent's), which
# rewards taking corners and avoiding handing them over. Deterministic.
static func choose(b: PackedInt32Array, side: int) -> int:
	var moves: Array = Rules.legal_moves(b, side)
	if moves.is_empty():
		return -1
	var best_cell := -1
	var best_score := -1000000
	for m in moves:
		var trial := b.duplicate()
		Rules.apply_move(trial, side, m["cell"])
		var s := _positional(trial, side)
		# small mobility bonus: starving the opponent is good
		var mob: int = Rules.legal_moves(trial, Rules.opp(side)).size()
		s = s - mob
		if s > best_score:
			best_score = s
			best_cell = m["cell"]
	return best_cell

static func _positional(b: PackedInt32Array, side: int) -> int:
	var other := Rules.opp(side)
	var total := 0
	for i in 64:
		if b[i] == side:
			total += WEIGHTS[i]
		elif b[i] == other:
			total -= WEIGHTS[i]
	return total
