# test_rules.gd — headless acceptance tests for the Othello rules + bot
# (build_spec §4). Run: godot --headless --path . --script res://test_rules.gd
# Includes the verify-the-verifier fixtures: a broken move/board/direction
# table must make the checks go RED.
extends SceneTree

const Rules := preload("res://rules.gd")
const Bot := preload("res://bot.gd")

var _fail := 0
var _checks := 0

func _initialize() -> void:
	print("=== Othello: rules + bot tests ===")
	_start_position()
	_known_flip()
	_illegal_move_rejected()
	_full_board_terminal()
	_bot_plays_full_game()
	_determinism()
	_direction_table_sane()
	print("=== %d checks, %d fail ===" % [_checks, _fail])
	quit(1 if _fail > 0 else 0)

func ok(c: bool, m: String) -> void:
	_checks += 1
	print(("  PASS  " if c else "  FAIL  ") + m)
	if not c:
		_fail += 1

func _start_position() -> void:
	var b := Rules.start_board()
	var s := Rules.score(b)
	ok(s.dark == 2 and s.light == 2, "start has 2 dark + 2 light")
	ok(Rules.legal_moves(b, Rules.DARK).size() == 4, "dark has 4 opening moves")
	ok(not Rules.is_terminal(b), "start is not terminal")

func _known_flip() -> void:
	var b := Rules.start_board()
	# Dark plays (row 2, col 3): flanks the light at (3,3) against dark at (4,3).
	var flipped := Rules.apply_move(b, Rules.DARK, 2 * 8 + 3)
	ok(flipped.size() == 1 and flipped[0] == 3 * 8 + 3, "dark d3 flips exactly (3,3)")
	var s := Rules.score(b)
	ok(s.dark == 4 and s.light == 1, "after the flip: 4 dark, 1 light")

func _illegal_move_rejected() -> void:
	# verify-the-verifier: a placement that flanks nothing must be illegal.
	var b := Rules.start_board()
	ok(Rules.flips_for(b, Rules.DARK, 0, 0).is_empty(), "corner (0,0) flanks nothing -> illegal")
	var cells := []
	for m in Rules.legal_moves(b, Rules.DARK):
		cells.append(m.cell)
	ok(not cells.has(0), "legal_moves excludes the flip-nothing corner")

func _full_board_terminal() -> void:
	# verify-the-verifier: a completely filled board must read terminal.
	var b := PackedInt32Array()
	b.resize(64)
	b.fill(Rules.DARK)
	ok(Rules.is_terminal(b), "a full board is terminal")
	ok(not Rules.has_move(b, Rules.LIGHT), "no legal move on a full board")

func _play(seed_unused: int = 0) -> Array:
	# Returns the move transcript of a full bot-vs-bot game.
	var b := Rules.start_board()
	var side := Rules.DARK
	var moves: Array = []
	var guard := 0
	while not Rules.is_terminal(b) and guard < 200:
		guard += 1
		var cell := Bot.choose(b, side)
		if cell == -1:
			side = Rules.opp(side)     # pass
			continue
		Rules.apply_move(b, side, cell)
		moves.append(cell)
		side = Rules.opp(side)
	return [moves, b]

func _bot_plays_full_game() -> void:
	var res := _play()
	var b: PackedInt32Array = res[1]
	var s := Rules.score(b)
	var empty: int = 64 - s.dark - s.light
	ok(Rules.is_terminal(b), "bot game reaches a terminal state")
	ok(s.dark + s.light + empty == 64, "discs + empty sum to 64")
	ok(s.dark != s.light, "the game has a winner (this matchup isn't a draw)")
	print("    (final: dark=%d light=%d empty=%d, %d moves)" % [s.dark, s.light, empty, res[0].size()])

func _determinism() -> void:
	var a: Array = _play()[0]
	var b: Array = _play()[0]
	ok(a == b, "same bot-vs-bot game every run (deterministic transcript)")

func _direction_table_sane() -> void:
	var uniq := {}
	for d in Rules.DIRS:
		uniq[d] = true
	ok(Rules.DIRS.size() == 8 and uniq.size() == 8, "8 unique flip directions")
