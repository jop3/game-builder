# rules.gd — pure Othello rules (build_spec §2: no node, no rendering, no randf).
# Board is a flat PackedInt32Array of 64, index = row*8 + col.
class_name OthelloRules
extends RefCounted

const N := 8
const EMPTY := 0
const DARK := 1    # Obsidian — moves first (standard Othello)
const LIGHT := 2   # Moonstone / pearl

# The 8 flip directions as (dcol, drow). ONE shared table used by both
# legality and apply — the classic Othello bug is two copies that disagree.
const DIRS := [
	Vector2i(-1, -1), Vector2i(0, -1), Vector2i(1, -1),
	Vector2i(-1, 0),                   Vector2i(1, 0),
	Vector2i(-1, 1), Vector2i(0, 1), Vector2i(1, 1),
]

static func start_board() -> PackedInt32Array:
	var b := PackedInt32Array()
	b.resize(64)
	b.fill(EMPTY)
	b[3 * 8 + 3] = LIGHT
	b[3 * 8 + 4] = DARK
	b[4 * 8 + 3] = DARK
	b[4 * 8 + 4] = LIGHT
	return b

static func opp(side: int) -> int:
	return LIGHT if side == DARK else DARK

# Flipped cells if placing `side` at (row,col) is legal; empty array otherwise.
static func flips_for(b: PackedInt32Array, side: int, row: int, col: int) -> Array:
	if b[row * 8 + col] != EMPTY:
		return []
	var other := opp(side)
	var flips: Array = []
	for d in DIRS:
		var line: Array = []
		var cc: int = col + d.x
		var rr: int = row + d.y
		while cc >= 0 and cc < 8 and rr >= 0 and rr < 8 and b[rr * 8 + cc] == other:
			line.append(rr * 8 + cc)
			cc += d.x
			rr += d.y
		# bounded by our own disc AND at least one opponent disc between
		if not line.is_empty() and cc >= 0 and cc < 8 and rr >= 0 and rr < 8 \
				and b[rr * 8 + cc] == side:
			flips.append_array(line)
	return flips

# All legal moves for `side`: array of { cell:int, flips:Array }.
static func legal_moves(b: PackedInt32Array, side: int) -> Array:
	var out: Array = []
	for row in 8:
		for col in 8:
			var f: Array = flips_for(b, side, row, col)
			if not f.is_empty():
				out.append({"cell": row * 8 + col, "flips": f})
	return out

# Place `side` at `cell` and flip the captured runs. Returns flipped cells.
static func apply_move(b: PackedInt32Array, side: int, cell: int) -> Array:
	var f: Array = flips_for(b, side, cell / 8, cell % 8)
	b[cell] = side
	for i in f:
		b[i] = side
	return f

static func has_move(b: PackedInt32Array, side: int) -> bool:
	return not legal_moves(b, side).is_empty()

static func is_terminal(b: PackedInt32Array) -> bool:
	return not has_move(b, DARK) and not has_move(b, LIGHT)

static func score(b: PackedInt32Array) -> Dictionary:
	var d := 0
	var l := 0
	for v in b:
		if v == DARK:
			d += 1
		elif v == LIGHT:
			l += 1
	return {"dark": d, "light": l}
